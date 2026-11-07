"""Intake recovery contracts, without importing the app or touching profile DBs."""
import ast
import importlib.util
import json
import re
import unittest
import uuid
from html import escape, unescape
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('dev1_intake_bridge', ROOT / 'emergency-app/common_server.py')
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)
PROFILE = (3, '<img src=x onerror=alert(1)>', '2000-01-01', '010-0000-0000', '<script>address</script>', 'guardian', '010-0000-0001')


def payload(**changes):
    args = dict(profile=PROFILE, reporter_role='transporter', consciousness='unknown', breathing='unknown',
                symptoms='가슴 통증', required_capabilities=[], special_team=False,
                lat=37.5, lon=126.9, consent=True, body_parts=['가슴'])
    args.update(changes)
    return bridge.build_episode_payload(**args)


def voice(**changes):
    result = dict(version=2, flow_token='b9f1a814-34b3-4c36-8041-eddaab02544a',
                  created_at=1000000, updated_at=1000010, patient_id='3', transcript='흉통은 없다고 합니다.',
                  conditions='미확인', areas='가슴', symptoms='부정 진술 · 직접 확인 필요', onset='미확인', confirmed=True)
    result.update(changes)
    return result


class Response:
    def __init__(self, html, status_code=200):
        self.body, self.status_code = html.encode('utf-8'), status_code


def rendering_functions():
    """Compile only pure render functions; no top-level app/database initialization."""
    names = {'_observed_state', '_flow_response', '_connect_form_html', 'emergency_page', 'patients_page',
             'patient_detail', 'emergency_check', 'emergency_breathing', 'emergency_symptoms',
             'emergency_bodymap', 'emergency_report'}
    tree = ast.parse((ROOT / 'emergency-app/main.py').read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in selected:
        node.decorator_list = []
    namespace = dict(HTMLResponse=Response, Query=lambda default: default, re=re, uuid=uuid, escape=escape,
                     unescape=unescape, parse_qsl=parse_qsl, urlencode=urlencode, urlsplit=urlsplit,
                     urlunsplit=urlunsplit, new_submission_id=bridge.new_submission_id,
                     get_patients=lambda: [PROFILE], get_patient=lambda _: PROFILE)
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<intake-render-only>', 'exec'), namespace)
    return namespace


class IntakePayloadTests(unittest.TestCase):
    def test_manual_fields_stay_unknown_and_voice_is_only_additional_display_evidence(self):
        item = voice()
        result = payload(voice_intake=item, body_parts=['가슴', '가슴', '머리'])
        self.assertEqual((result['consciousness'], result['breathing']), ('unknown', 'unknown'))
        self.assertEqual((result['symptoms'], result['priority'], result['priority_source']), ('가슴 통증', None, 'unknown'))
        self.assertEqual(result['required_capabilities'], [])
        self.assertEqual(result['resource_demands'], {'ed_bed': 1})
        self.assertEqual(result['extra']['body_parts'], ['가슴', '머리'])
        self.assertEqual(result['extra']['voice_intake'], item)

    def test_body_parts_must_be_explicit_catalog_choices(self):
        for parts in ('가슴', ['diagnosed-cardiac'], ['<script>']):
            with self.subTest(parts=parts), self.assertRaises(bridge.CommonServerError):
                payload(body_parts=parts)

    def test_confirmed_voice_requires_same_patient_flow_and_live_explicit_confirmation(self):
        item = voice()
        self.assertEqual(bridge.confirmed_voice_intake(json.dumps(item), 'yes', item['flow_token'], 3, now_ms=1000020), item)
        self.assertIsNone(bridge.confirmed_voice_intake('', '', item['flow_token'], 3, now_ms=1000020))
        cases = [dict(confirmed=False), dict(patient_id='4'), dict(flow_token=str(uuid.uuid4())),
                 dict(version=True), dict(created_at=-1), dict(updated_at=999999),
                 dict(transcript='x' * 8001), dict(conditions='x' * 1001), dict(extra='unrequested')]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(bridge.CommonServerError):
                bridge.confirmed_voice_intake(json.dumps(voice(**change)), 'yes', item['flow_token'], 3, now_ms=1000020)
        for confirmation in ('', 'true', 'no'):
            with self.subTest(confirmation=confirmation), self.assertRaises(bridge.CommonServerError):
                bridge.confirmed_voice_intake(json.dumps(item), confirmation, item['flow_token'], 3, now_ms=1000020)
        with self.assertRaises(bridge.CommonServerError):
            bridge.confirmed_voice_intake(json.dumps(item), 'yes', item['flow_token'], 3, now_ms=item['created_at'] + bridge.VOICE_TTL_MS)

    def test_exact_retry_keeps_original_profile_snapshot_but_rejects_changed_request_fields(self):
        original = payload()
        current = payload(profile=(PROFILE[0], '프로필 수정 뒤 이름', *PROFILE[2:]))
        key, submission, flow = b'synthetic test key', str(uuid.uuid4()), str(uuid.uuid4())
        token = bridge.sign_episode_payload(original, key, submission, flow)
        self.assertEqual(bridge.restore_episode_payload(token, current, key, submission, flow), original)
        legacy_profile = (PROFILE[0], '합성 장기 보관 프로필', '1880-01-01', '', '합성 주소' * 9000, '', '')
        large_snapshot = payload(profile=legacy_profile)
        large_token = bridge.sign_episode_payload(large_snapshot, key, submission, flow)
        self.assertGreater(len(large_token), 40068)
        self.assertEqual(bridge.restore_episode_payload(large_token, current, key, submission, flow), large_snapshot)
        with patch.dict('os.environ', {'COMMON_API_URL': 'http://127.0.0.1:59999'}):
            with self.assertRaises(bridge.CommonServerError):
                bridge.restore_episode_payload(token, current, key, submission, flow)
        for changes in (dict(symptoms='수정한 내용'), dict(lat=38.0), dict(consent=False), dict(body_parts=['머리'])):
            if changes.get('consent') is False:
                with self.assertRaises(bridge.CommonServerError):
                    payload(**changes)
                continue
            with self.subTest(changes=changes), self.assertRaises(bridge.CommonServerError):
                bridge.restore_episode_payload(token, payload(**changes), key, submission, flow)

    def test_unreadable_or_wrong_success_needs_exact_stored_episode_before_success(self):
        request = payload()
        token = str(uuid.uuid4())
        pid = 'MOBILE-' + uuid.UUID(token).hex
        calls = []
        def request_api(path, body=None):
            calls.append((path, body))
            if body is not None:
                return {}  # HTTP success alone cannot prove registration.
            outgoing = calls[0][1]
            return dict(outgoing)
        with patch.object(bridge, '_request', side_effect=request_api):
            saved = bridge.create_episode(request, token)
        self.assertEqual(saved['id'], pid)
        self.assertEqual([path for path, _ in calls], ['/api/patients', '/api/patients/' + pid])
        self.assertEqual(calls[0][1]['extra']['body_parts'], ['가슴'])
        with patch.object(bridge, '_request', side_effect=[{}, {'id': pid, 'extra': {}}]):
            with self.assertRaises(bridge.CommonServerError):
                bridge.create_episode(request, token)


class IntakeRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.render = rendering_functions()

    def test_original_profile_pages_escape_stored_profile_fields(self):
        for name, args in [('patients_page', ()), ('patient_detail', (3,))]:
            with self.subTest(name=name):
                html = self.render[name](*args)
                html = html.body.decode() if hasattr(html, 'body') else html
                self.assertNotIn('<img src=x', html)
                self.assertNotIn('<script>address', html)
                self.assertIn('&lt;img', html)

    def test_flow_id_and_safe_profile_display_survive_existing_intake_steps(self):
        flow = str(uuid.uuid4())
        cases = [('emergency_page', dict(flow_token=flow)),
                 ('emergency_check', dict(patient_id='3', flow_token=flow)),
                 ('emergency_breathing', dict(patient_id=3, consciousness='malformed', flow_token=flow)),
                 ('emergency_symptoms', dict(patient_id=3, consciousness='malformed', breathing='malformed', flow_token=flow)),
                 ('emergency_bodymap', dict(patient_id=3, consciousness='malformed', breathing='malformed', symptom=['가슴 통증'], flow_token=flow)),
                 ('emergency_report', dict(patient_id=3, consciousness='malformed', breathing='malformed', symptom=['가슴 통증'], body_part=['가슴'], flow_token=flow))]
        for name, args in cases:
            with self.subTest(name=name):
                html = self.render[name](**args).body.decode()
                if '<form' in html:
                    self.assertIn('name="flow_token" value="' + flow + '"', html)
                else:
                    self.assertIn('flow_token=' + flow, html)
                self.assertIn('data-flow-token="' + flow + '"', html)
                self.assertIn('/assets/voice-intake-flow.mjs', html)
                self.assertNotIn('<img src=x', html)
                self.assertNotIn('emergencyVoiceIntake', html)
                if 'consciousness' in args:
                    self.assertNotIn('value="malformed"', html)

    def test_connect_form_preserves_exact_failed_submission_and_escaped_raw_fields(self):
        kwargs = dict(submission_id=str(uuid.uuid4()), flow_token=str(uuid.uuid4()), reporter_role='transporter',
                      required_capabilities=['emergency'], consent=True, episode_payload='{"name":"<original>"}',
                      error='<upstream failed>')
        html = self.render['_connect_form_html']([{'capabilities': ['emergency']}], 3, 'unknown', 'unknown',
                ['가슴 통증'], ['가슴'], 'bad<latitude>', '126.9', **kwargs)
        self.assertIn('action="/emergency/connect" method="post"', html)
        self.assertIn('name="submission_id" value="' + kwargs['submission_id'] + '"', html)
        self.assertIn('name="body_part" value="가슴"', html)
        self.assertIn('value="bad&lt;latitude&gt;"', html)
        self.assertIn('&lt;upstream failed&gt;', html)
        self.assertIn('&quot;name&quot;:&quot;&lt;original&gt;&quot;', html)


if __name__ == '__main__':
    unittest.main()
