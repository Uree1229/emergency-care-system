"""DEV1 단계식 입력 → 공통 이송 흐름의 합성 HTTP 통합 검증.

FastAPI는 TestClient(ASGI), 공통 서버는 실제 로컬 ThreadingHTTPServer와
서로 다른 임시 SQLite DB를 사용한다. GPS 좌표와 확인 음성은 입력 fixture다.
실제 마이크/STT·위치 센서·브라우저 DOM·의료기관/API 효과를 검증하지 않는다.
"""
from contextlib import ExitStack, closing
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
import uuid

from fastapi.testclient import TestClient

from server import db, service
from server.app import make_handler
from server.travel import TravelEstimator


ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / 'emergency-app'
ABSENT = object()


class Page(HTMLParser):
    """Read rendered navigation and successful controls, without executing JS."""
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.forms, self.links, self.ids, self.tags, self.scripts = [], [], {}, [], []
        self.form, self.script = None, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if attrs.get('id'):
            self.ids[attrs['id']] = attrs
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        if tag == 'form':
            self.form = {'attrs': attrs, 'fields': [], 'inputs': []}
            self.forms.append(self.form)
        if tag == 'input' and self.form is not None and attrs.get('name'):
            self.form['inputs'].append(attrs)
            if attrs.get('type') not in ('checkbox', 'radio', 'submit', 'button') or 'checked' in attrs:
                self.form['fields'].append((attrs['name'], attrs.get('value', '')))
        if tag == 'script' and not attrs.get('src'):
            self.script = {'attrs': attrs, 'code': ''}

    def handle_data(self, data):
        if self.script is not None:
            self.script['code'] += data

    def handle_endtag(self, tag):
        if tag == 'form':
            self.form = None
        if tag == 'script' and self.script is not None:
            self.scripts.append(self.script)
            self.script = None

    def action(self, target):
        return next(f for f in self.forms if urlsplit(f['attrs'].get('action', '')).path == target)


class Dev1HttpFlow(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.profile_path = self.tmp / 'nested' / 'profiles.sqlite3'
        self.common_path = self.tmp / 'common.sqlite3'
        self.now = int(time.time())
        self.conn = db.connect(str(self.common_path))
        self.stack.callback(self.conn.close)
        db.init(self.conn)
        service.upsert_hospital(self.conn, dict(id='INTAKE-H', name='합성 응급실',
            lat=37.51, lon=126.96, capabilities=['응급의학과'], source='synthetic'))
        service.set_resource(self.conn, self.now, 'INTAKE-H', 'ed_bed', 8, 0, source='synthetic')
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
            str(self.common_path), TravelEstimator(use_kakao=False), clock=lambda: self.now))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.stack.callback(self.close_server)
        self.base = f'http://127.0.0.1:{self.http.server_port}'
        self.stack.enter_context(patch.dict('os.environ', {
            'COMMON_API_URL': self.base, 'EMERGENCY_PROFILE_DB': str(self.profile_path)}))

        # Legacy imports use simple names. Restore any caller modules afterwards.
        names = ('database', 'common_server', 'api_integrations')
        previous = {n: sys.modules.pop(n, ABSENT) for n in names}
        def restore_modules():
            for name, module in previous.items():
                sys.modules.pop(name, None)
                if module is not ABSENT:
                    sys.modules[name] = module
        self.stack.callback(restore_modules)
        old_path = list(sys.path)
        sys.path.insert(0, str(APP_DIR))
        self.stack.callback(lambda: sys.path.__setitem__(slice(None), old_path))
        spec = importlib.util.spec_from_file_location('dev1_http_intake', APP_DIR / 'main.py')
        self.main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.main)
        self.profile_db = sys.modules['database']
        self.bridge = sys.modules['common_server']

        # Public reference cards stay present, but all upstream results are synthetic.
        self.network = []
        def only_common(request, *args, **kwargs):
            target = request.full_url if isinstance(request, Request) else str(request)
            if urlsplit(target).netloc != urlsplit(self.base).netloc:
                raise AssertionError('External API attempted: ' + target)
            self.network.append((request.get_method() if isinstance(request, Request) else 'GET', target))
            return urlopen(request, *args, **kwargs)
        self.stack.enter_context(patch.object(self.bridge, 'urlopen', side_effect=only_common))
        self.stack.enter_context(patch('urllib.request.urlopen', side_effect=only_common))
        public_hospital = dict(name='합성 공개 조회 병원', lat=37.51, lon=126.96,
                               phone='02-0000-0000', address='합성 병원 주소', emergency_beds='2')
        self.public_calls = {
            'fetch': self.stack.enter_context(patch.object(self.main, 'fetch_emergency_hospitals',
                                                           return_value=([public_hospital], None))),
            'reverse': self.stack.enter_context(patch.object(self.main, 'reverse_geocode',
                                                             return_value=('서울특별시', '동작구'))),
        }
        for name in ('enrich_hospitals', 'enrich_hospitals_with_hira', 'enrich_hospitals_with_severe'):
            self.public_calls[name] = self.stack.enter_context(patch.object(
                self.main, name, side_effect=lambda hospitals, *args: hospitals))
        self.client = self.stack.enter_context(TestClient(self.main.app, follow_redirects=False))
        self.flow = str(uuid.uuid4())
        self.pages = []

    def close_server(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=3)

    def common(self, path, body=None):
        request = Request(self.base + path,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
            headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def counts(self):
        return tuple(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                     for table in ('patients', 'inquiry_workflows', 'inquiries'))

    def page(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.pages.append((url, response.text))
        return Page(response.text)

    def submit_get(self, form, **overrides):
        fields = [(key, value) for key, value in form['fields'] if key not in overrides]
        for key, value in overrides.items():
            fields.extend((key, str(item)) for item in (value if isinstance(value, list) else [value]))
        return self.page(form['attrs']['action'] + '?' + urlencode(fields))

    def choice(self, page, path, key, value):
        link = next(link for link in page.links if urlsplit(link).path == path
                    and parse_qs(urlsplit(link).query).get(key) == [value])
        self.assertEqual(parse_qs(urlsplit(link).query).get('flow_token'), [self.flow])
        return self.page(link)

    def register(self, name='합성 환자 A'):
        response = self.client.post('/register', data=dict(name=name, birth='1999-01-02',
            phone='010-0000-0000', address='합성 주소', guardian='합성 보호자', guardian_phone='010-0000-0001'))
        self.assertIn(response.status_code, (200, 303))
        return self.profile_db.get_patients()[0][0]

    def chain(self, patient_id=0, consciousness='yes', breathing='yes'):
        page = self.page('/emergency?' + urlencode({'flow_token': self.flow}))
        page = self.submit_get(page.action('/emergency/check'), patient_id=patient_id)
        page = self.choice(page, '/emergency/breathing', 'consciousness', consciousness)
        page = self.choice(page, '/emergency/symptoms', 'breathing', breathing)
        page = self.submit_get(page.action('/emergency/bodymap'), symptom=['가슴 통증', '호흡 곤란'])
        page = self.submit_get(page.action('/emergency/report'), body_part=['가슴', '왼팔'])
        page = self.submit_get(page.action('/emergency/hospitals'),
                               latitude='37.5051', longitude='126.9573', accuracy='12')
        form = page.action('/emergency/connect')
        self.assertEqual(form['attrs'].get('id'), 'common-connect-form')
        fields = dict(form['fields'])
        self.assertEqual(fields.get('flow_token'), self.flow)
        self.assertEqual(fields.get('patient_id'), str(patient_id))
        self.assertEqual(self.counts(), (0, 0, 0), 'GET navigation must not create transport episodes')
        return form

    def post(self, form, **changes):
        fields = [(key, value) for key, value in form['fields'] if key not in changes]
        defaults = {'reporter_role': 'transporter', 'required_capabilities': ['응급의학과'],
                    'latitude': '37.5051', 'longitude': '126.9573', 'inquiry_policy': 'fixed_3'}
        defaults.update(changes)
        fields = [(key, value) for key, value in fields if key not in defaults]
        for key, value in defaults.items():
            if value is not None:
                fields.extend((key, str(item)) for item in (value if isinstance(value, list) else [value]))
        return self.client.post(form['attrs']['action'], content=urlencode(fields),
                                headers={'Content-Type': 'application/x-www-form-urlencoded'})

    def created(self, response):
        self.assertEqual(response.status_code, 303, response.text[:500])
        location = urlsplit(response.headers['location'])
        self.assertEqual(location.netloc, urlsplit(self.base).netloc)
        self.assertEqual(location.path, '/patient/')
        pid = parse_qs(location.query)['pid'][0]
        self.assertTrue(pid.startswith('MOBILE-'))
        status, patient = self.common('/api/patients/' + pid)
        self.assertEqual(status, 200)
        return patient

    def voice(self, patient_id=0, **changes):
        now = int(time.time() * 1000)
        value = dict(version=2, flow_token=self.flow, created_at=now, updated_at=now,
                     patient_id=str(patient_id), transcript='30분 전부터 가슴이 아픕니다.',
                     conditions='언급 없음', areas='가슴', symptoms='흉통', onset='30분 전', confirmed=True)
        value.update(changes)
        return json.dumps(value, ensure_ascii=False)

    def test_profile_db_env_and_new_pages_remain_available(self):
        self.assertEqual(Path(self.profile_db.DB_PATH).resolve(), self.profile_path.resolve())
        self.assertTrue(self.profile_path.is_file())
        pid = self.register()
        self.assertEqual(self.profile_db.get_patient(pid)[1], '합성 환자 A')
        self.page('/')
        self.page('/voice-intake?' + urlencode({'flow_token': self.flow}))
        modules = sorted((APP_DIR / 'static').glob('voice-intake*.mjs'))
        self.assertTrue(modules)
        for module in modules:
            response = self.client.get('/assets/' + module.name)
            self.assertEqual(response.status_code, 200, module.name)
            self.assertIn('javascript', response.headers.get('content-type', ''), module.name)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_rendered_chain_consent_idempotency_and_hospital_arrival(self):
        profile_id = self.register()
        form = self.chain(profile_id, consciousness='no', breathing='no')
        self.public_calls['fetch'].assert_called_once_with(37.5051, 126.9573)
        denied = self.post(form, consent=None)
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(self.counts(), (0, 0, 0))
        retry_form = Page(denied.text).action('/emergency/connect')
        self.assertEqual(dict(retry_form['fields'])['submission_id'], dict(form['fields'])['submission_id'])
        first = self.post(retry_form, consent='yes')
        patient = self.created(first)
        self.assertEqual((patient['name'], patient['extra']['source_profile_id']), ('합성 환자 A', profile_id))
        self.assertNotEqual(patient['id'], str(profile_id))
        self.assertEqual((patient['consciousness'], patient['breathing']), ('unresponsive', 'abnormal'))
        self.assertEqual(patient['symptoms'], '가슴 통증, 호흡 곤란')
        self.assertEqual(patient['extra']['body_parts'], ['가슴', '왼팔'])
        self.assertEqual((patient['lat'], patient['lon']), (37.5051, 126.9573))
        self.assertEqual((patient['priority'], patient['priority_source']), (None, 'unknown'))
        self.assertNotIn('voice_intake', patient['extra'])
        before = self.counts()
        again = self.post(retry_form, consent='yes')
        self.assertEqual(again.headers.get('location'), first.headers['location'])
        self.assertEqual(self.counts(), before)
        conflict = self.post(retry_form, consent='yes', body_part=['머리'])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.counts(), before)

        pid = patient['id']
        self.assertEqual(self.common(f'/api/patients/{pid}/inquiries', {'hospital_ids': ['INTAKE-H']})[0], 200)
        patient = self.common('/api/patients/' + pid)[1]
        inquiry = next(q for q in patient['inquiries'] if q['hospital_id'] == 'INTAKE-H')
        self.assertEqual(self.common(f"/api/inquiries/{inquiry['id']}/respond", {
            'action': 'accept', 'handled_by': '합성 담당자', 'expected_version': inquiry['version']})[0], 200)
        self.assertEqual(self.common(f'/api/patients/{pid}/confirm', {'inquiry_id': inquiry['id']})[1]['state'], 'CONFIRMED')
        self.assertEqual(self.common(f'/api/patients/{pid}/depart', {})[1]['state'], 'IN_TRANSIT')
        arrival = self.common(f'/api/hospitals/INTAKE-H/patients/{pid}/arrive', {'handled_by': '합성 담당자'})
        self.assertEqual((arrival[0], arrival[1]['state']), (200, 'ARRIVED'))
        resource = self.common('/api/hospitals/INTAKE-H')[1]['resources'][0]
        self.assertEqual((resource['occupied'], resource['expected']), (1, 0))
        self.assertEqual(self.counts()[:2], (1, 1))

    def test_explicit_confirmed_voice_is_extra_only_and_survives_retry(self):
        profile_id = self.register()
        form = self.chain(profile_id)
        raw = self.voice(profile_id)
        patient = self.created(self.post(form, consent='yes', voice_payload=raw, voice_confirmed='yes'))
        self.assertEqual(patient['extra']['voice_intake']['transcript'], json.loads(raw)['transcript'])
        self.assertEqual(patient['extra']['voice_intake']['conditions'], '언급 없음')
        self.assertEqual(patient['extra']['body_parts'], ['가슴', '왼팔'])
        self.assertEqual(patient['symptoms'], '가슴 통증, 호흡 곤란')
        self.assertEqual((patient['priority'], patient['priority_source']), (None, 'unknown'))
        again = self.created(self.post(form, consent='yes', voice_payload=raw, voice_confirmed='yes'))
        self.assertEqual(again['id'], patient['id'])
        self.assertEqual(self.counts()[:2], (1, 1))

    def test_unconfirmed_expired_or_foreign_voice_never_creates_an_episode(self):
        profile_id = self.register()
        form = self.chain(profile_id)
        cases = [
            (self.voice(profile_id), None),
            (self.voice(profile_id, confirmed=False), 'yes'),
            (self.voice(profile_id + 1), 'yes'),
            (self.voice(profile_id, flow_token=str(uuid.uuid4())), 'yes'),
            (self.voice(profile_id, created_at=int(time.time() * 1000) - 2 * 60 * 60 * 1000 - 1000), 'yes'),
        ]
        for raw, confirmation in cases:
            with self.subTest(raw=raw, confirmation=confirmation):
                result = self.post(form, consent='yes', voice_payload=raw, voice_confirmed=confirmation)
                self.assertEqual(result.status_code, 400, result.text[:300])
                self.assertEqual(self.counts(), (0, 0, 0))

    def test_first_submit_cannot_bypass_profile_or_catalogue_validation(self):
        profile_id = self.register()
        form = self.chain(profile_id)
        expected = self.bridge.build_episode_payload(self.profile_db.get_patient(profile_id), 'transporter',
            'yes', 'yes', '가슴 통증, 호흡 곤란', ['응급의학과'], False, 37.5051, 126.9573, True,
            body_parts=['가슴', '왼팔'])
        forged = json.dumps(dict(expected, name='임의로 바꾼 이름'))
        unlisted = ['목록에 없는 진료과']
        cases = [
            ({'episode_payload': forged}, 409),
            ({'episode_payload': 'v1.' + '0' * 64 + '.' + forged}, 409),
            ({'episode_payload': '[' * 1500 + '0' + ']' * 1500}, 409),
            ({'episode_payload': json.dumps(dict(expected, required_capabilities=unlisted)),
              'required_capabilities': unlisted}, 400),
        ]
        for changes, status in cases:
            with self.subTest(status=status, signed=changes['episode_payload'].startswith('v1.')):
                denied = self.post(form, consent='yes', **changes)
                self.assertEqual(denied.status_code, status)
                self.assertEqual(self.counts(), (0, 0, 0))
        self.assertFalse(any(method == 'POST' for method, _ in self.network))
        patient = self.created(self.post(form, consent='yes', episode_payload=json.dumps(expected)))
        self.assertEqual(patient['name'], '합성 환자 A')
        self.assertEqual(self.counts()[:2], (1, 1))

    def test_connect_failure_preserves_exact_form_and_retry_creates_once(self):
        profile_id = self.register()
        form = self.chain(profile_id)
        raw_voice = self.voice(profile_id)
        real_open = self.bridge.urlopen
        def offline_post(request, **kwargs):
            if request.get_method() == 'POST':
                raise URLError('synthetic offline')
            return real_open(request, **kwargs)
        with patch.object(self.bridge, 'urlopen', side_effect=offline_post):
            failed = self.post(form, consent='yes', voice_payload=raw_voice, voice_confirmed='yes')
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(self.counts(), (0, 0, 0))
        retry = Page(failed.text).action('/emergency/connect')
        values = parse_qs(urlencode(retry['fields']), keep_blank_values=True)
        initial = dict(form['fields'])
        for key in ('submission_id', 'patient_id', 'flow_token'):
            self.assertEqual(values[key], [initial[key]])
        self.assertEqual(values['body_part'], ['가슴', '왼팔'])
        self.assertEqual(values['latitude'], ['37.5051'])
        self.assertEqual(values['longitude'], ['126.9573'])
        self.assertEqual(values['consent'], ['yes'])
        self.assertEqual(values['voice_payload'], [raw_voice])
        self.assertEqual(values['voice_confirmed'], ['yes'])
        token = values['episode_payload'][0]
        version, signature, raw = token.split('.', 2)
        tampered = dict(json.loads(raw), name='서명 이후 바꾼 이름')
        for changes in (
            {'episode_payload': f'{version}.{signature}.{json.dumps(tampered)}'},
            {'submission_id': str(uuid.uuid4())},
            {'flow_token': str(uuid.uuid4()), 'voice_payload': '', 'voice_confirmed': ''},
        ):
            with self.subTest(fields=tuple(changes)):
                self.assertEqual(self.post(retry, consent='yes', **changes).status_code, 409)
                self.assertEqual(self.counts(), (0, 0, 0))
        changed = self.post(retry, consent='yes', latitude='37.6')
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.counts(), (0, 0, 0))
        with closing(sqlite3.connect(self.profile_path)) as local:
            local.execute('UPDATE patients SET name=? WHERE id=?', ('전송 후 수정한 프로필', profile_id))
            local.commit()
        with patch.object(self.main, 'hospital_catalog', return_value=[]):
            patient = self.created(self.post(retry, consent='yes'))
        self.assertEqual(patient['name'], '합성 환자 A', 'Retry must retain the original profile snapshot')
        self.assertEqual(patient['extra']['voice_intake']['transcript'], json.loads(raw_voice)['transcript'])
        self.assertEqual(self.counts()[:2], (1, 1))

    def test_lost_create_response_recovers_one_committed_episode(self):
        form = self.chain()
        real_open = self.bridge.urlopen
        def lose_response(request, **kwargs):
            if request.get_method() == 'POST':
                with real_open(request, **kwargs) as response:
                    response.read()
                raise URLError('synthetic reply lost after commit')
            return real_open(request, **kwargs)
        with patch.object(self.bridge, 'urlopen', side_effect=lose_response):
            patient = self.created(self.post(form, consent='yes'))
        self.assertEqual(self.counts()[:2], (1, 1))
        self.assertEqual(patient['extra']['source_profile_id'], None)

    def test_profile_and_query_text_cannot_create_markup_in_rendered_stages(self):
        attack = '<img src=x onerror="alert(123)"><script>globalThis.injection=1</script>'
        profile_id = self.register(attack)
        self.chain(profile_id)
        for url, html in self.pages:
            with self.subTest(url=url):
                parsed = Page(html)
                self.assertFalse(any(tag == 'img' and attrs.get('onerror') == 'alert(123)' for tag, attrs in parsed.tags))
                self.assertFalse(any('globalThis.injection=1' in script['code'] for script in parsed.scripts))
        response = self.client.get('/emergency/report', params={
            'patient_id': profile_id, 'consciousness': attack, 'breathing': attack,
            'symptom': attack, 'body_part': attack, 'flow_token': self.flow})
        self.assertEqual(response.status_code, 200)
        parsed = Page(response.text)
        self.assertFalse(any(tag == 'img' and attrs.get('onerror') == 'alert(123)' for tag, attrs in parsed.tags))
        self.assertFalse(any('globalThis.injection=1' in s['code'] for s in parsed.scripts))

    def test_rendered_inline_javascript_has_no_syntax_error(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is needed only for this rendered JavaScript syntax check')
        self.page('/')
        self.page('/voice-intake?' + urlencode({'flow_token': self.flow}))
        self.chain(self.register())
        for url, html in self.pages:
            for index, script in enumerate(Page(html).scripts):
                with self.subTest(url=url, script=index):
                    args = [node, '--check']
                    if script['attrs'].get('type') == 'module':
                        args.append('--input-type=module')
                    checked = subprocess.run(args + ['-'], input=script['code'], text=True,
                                             encoding='utf-8', capture_output=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == '__main__':
    unittest.main()
