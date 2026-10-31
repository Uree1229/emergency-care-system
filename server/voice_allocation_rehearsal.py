"""Reproducible server-only voice/allocation lifecycle checks, using synthetic data.

python -m server.voice_allocation_rehearsal --output .codex-build/voice-allocation-run

Only a NEW output directory and private loopback servers are used. Hospital and
crew decisions are role-emulated API calls; the actual inquiry/planning cycles
run synchronously against a controlled clock. No microphone, live demo DB,
external routing, frontend, or real hospital is accessed.
"""
import argparse
import copy
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import traceback
import urllib.error
import urllib.request

from . import allocation_baseline, db, inquiry_workflow, transport_reassignment, worker
from .app import make_handler
from .travel import TravelEstimator

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'voice-allocation-rehearsal-20261030-v1'
NOW = 2_000_000_000
REPORT = '47세 남성. 흉통. 의식 명료. 호흡 정상. 케이타스 2. 심전도 정상. 맥박 90회.'
CASE_NAMES = (
    'normal_arrival', 'waiting_treatment_change', 'enroute_resource_keep',
    'enroute_resource_reroute', 'optional_unavailable', 'no_hospital_response',
    'no_available_capacity', 'stale_treatment_approval', 'missing_r1_rejected',
)
SOURCE_FILES = tuple(sorted(set(str(path.relative_to(ROOT)).replace('\\', '/')
    for path in (ROOT/'server').glob('*.py')) | set(allocation_baseline.SOURCE_PATHS) | {
        'solver_improved.py', 'config/treatment_requirements.json',
        'config/voice_commands.json', 'config/paramedic_voice_terms.json',
        'config/server_allocation_baseline_20261030.json',
    }))


def fingerprints():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


class Flow:
    """Actual HTTP and worker functions; all writes stay in this new fixture DB."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = str(self.directory/'fixture.db')
        self.now = NOW
        self.counter = 0
        self.actor = 'SYNTHETIC-CREW'
        self.steps, self.requests, self.solves = [], [], []
        self.pid = None
        self.conn = self.http = self.thread = None

    def __enter__(self):
        try:
            return self._open()
        except BaseException:
            self.close()
            raise

    def _open(self):
        self.directory.mkdir(exist_ok=False)
        Path(self.path).touch(exist_ok=False)
        self.conn = db.connect(self.path)
        db.init(self.conn)
        from . import service
        for hid, lat, caps in (
                ('E2E-H1', 35.161, ['응급의학']),
                ('E2E-H2', 35.171, ['응급의학', '심장내과']),
                ('E2E-H3', 35.181, ['응급의학', '심장내과'])):
            service.upsert_hospital(self.conn, dict(id=hid, name='Synthetic '+hid, lat=lat,
                lon=126.85, capabilities=caps, source='synthetic'))
            for rtype in ('ed_bed', 'special_team'):
                service.set_resource(self.conn, self.now, hid, rtype, 4, 0, source='synthetic')
        self.estimator = TravelEstimator(use_kakao=False)
        # No fake RUNNING health status: worker cycles below are invoked manually.
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
            self.path, self.estimator, clock=lambda: self.now))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.http.server_port}'
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.http is not None:
            if self.thread is not None and self.thread.is_alive():
                self.http.shutdown()
                self.thread.join(timeout=3)
            self.http.server_close()
            self.http = None
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def call(self, method, path, body=None, expected=200):
        request = urllib.request.Request(self.origin+path, method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status, result = response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                status, result = response.code, json.load(response)
        self.requests.append(dict(method=method, path=path, status=status))
        if status != expected:
            raise AssertionError(f'{method} {path}: expected {expected}, got {status}: {result}')
        return result

    def require(self, name, condition, **evidence):
        if not condition:
            raise AssertionError(name)
        self.steps.append({'name': name, 'pass': True, **evidence})

    def common(self, session=None, **extra):
        self.counter += 1
        return dict(actor_id=self.actor, client_request_id=f'e2e-{self.counter}',
            **({'expected_version': session['version']} if session else {}), **extra)

    def voice(self, session, suffix, **extra):
        return self.call('POST', f'/api/voice/sessions/{session["id"]}/{suffix}',
                         self.common(session, **extra))

    def say(self, session, text):
        return self.voice(session, 'transcripts', text=text, mode='auto', is_final=True,
                          utterance_id=f'synthetic-u-{self.counter}')['session']

    def new_draft(self, *, linked=False, text=REPORT):
        session = self.call('POST', '/api/voice/sessions',
            self.common(**({'patient_id': self.pid} if linked else {})))['session']
        return self.say(session, text)

    def create(self, *, optional=False):
        session = self.new_draft()
        self.require('draft_does_not_create_patient', self.conn.execute(
            'SELECT COUNT(*) FROM patients').fetchone()[0] == 0)
        if optional:
            session = self.voice(session, 'followup/answers', question_id='blood_pressure',
                profile='voice_extended_v1', text='측정 못함',
                utterance_id=f'unavailable-{self.counter}')['session']
        session = self.voice(session, 'confirm', confirmed=True)['session']
        body = self.common(session, start_inquiry=True, inquiry_policy='fixed_3',
                           context=dict(consent=True, lat=35.16, lon=126.85))
        submitted = self.call('POST', f'/api/voice/sessions/{session["id"]}/submit', body)
        self.pid = submitted['patient']['id']
        duplicate = self.call('POST', f'/api/voice/sessions/{session["id"]}/submit', body)
        self.require('submit_retry_creates_one_patient', duplicate.get('duplicate') is True and
            duplicate['patient']['id'] == self.pid and self.conn.execute(
                'SELECT COUNT(*) FROM patients').fetchone()[0] == 1)
        self.require('voice_fields_projected_to_patient', submitted['patient']['extra']['avpu'] == 'A'
            and submitted['patient']['extra']['pre_ktas_level'] == 2
            and submitted['patient']['extra']['vitals']['hr'] == 90)
        self.require('initial_inquiry_policy_fixed', self.workflow()['policy_name'] == 'fixed_3')
        return submitted['patient']

    def patient(self):
        return self.call('GET', f'/api/patients/{self.pid}')

    def workflow(self):
        return self.call('GET', f'/api/patients/{self.pid}/inquiry-workflow')

    def tick_inquiry(self):
        inquiry_workflow.run_once(self.path, self.estimator, clock=lambda: self.now)

    def tick_transport(self):
        transport_reassignment.run_once(self.path, self.estimator, clock=lambda: self.now)

    def inquiry(self, hid):
        return next(q for q in self.patient()['inquiries'] if q['hospital_id'] == hid)

    def accept(self, hid):
        q = self.inquiry(hid)
        inbox = self.call('GET', f'/api/hospitals/{hid}/inquiries')
        self.require('hospital_sees_current_patient_version', any(
            item['id'] == q['id'] and item['patient_version'] == self.patient()['version']
            for item in inbox['open']))
        return self.call('POST', f'/api/inquiries/{q["id"]}/respond', dict(action='accept',
            handled_by='SYNTHETIC-DESK', expected_version=q['version']))['inquiry']

    def plan(self):
        # Use the real bounded CP-SAT path and the frozen THREE-second budget.
        self.require('actual_planning_cycle_published', worker.run_once(
            self.path, self.estimator, clock=lambda: self.now, seconds=3))
        result = self.call('GET', f'/api/patients/{self.pid}/recommendation')
        self.require('recommendation_is_fresh', result['status'] == 'READY')
        meta = result['result']['solver']
        self.solves.append(dict(meta, case_time=self.now))
        return result

    def setup_accepted(self):
        self.create()
        self.tick_inquiry()
        self.require('automatic_initial_inquiries_sent', len(self.patient()['inquiries']) == 3)
        q = self.accept('E2E-H1')
        result = self.plan()
        self.require('accepted_hospital_is_confirmable', result['result']['hospital_id'] == 'E2E-H1'
            and result['result']['confirmable_now'])
        self.require('recommendation_is_not_destination_confirmation',
            self.patient()['state'] == 'WAITING' and self.patient()['destination_hospital_id'] is None)
        return q

    def depart(self, q):
        self.call('POST', f'/api/patients/{self.pid}/confirm', {'inquiry_id': q['id']})
        self.call('POST', f'/api/patients/{self.pid}/transport-review', {'enabled': True})
        self.now += 1
        moving = self.call('POST', f'/api/patients/{self.pid}/depart', {})
        self.require('explicit_confirmation_then_departure', moving['state'] == 'IN_TRANSIT')

    def arrive(self, hid):
        self.now += 1
        arrived = self.call('POST', f'/api/hospitals/{hid}/patients/{self.pid}/arrive',
                            {'handled_by': 'SYNTHETIC-DESK'})
        self.require('hospital_arrival_closes_transport', arrived['state'] == 'ARRIVED')
        hospital = self.call('GET', f'/api/hospitals/{hid}')
        bed = next(r for r in hospital['resources'] if r['resource_type'] == 'ed_bed')
        self.require('arrival_counted_once', bed['occupied'] == 1 and bed['expected'] == 0)
        self.require('monitoring_completed', self.review()['review']['state'] == 'COMPLETED')

    def review(self):
        return self.call('GET', f'/api/patients/{self.pid}/transport-review')

    def loss(self):
        self.now += 1
        self.call('PUT', '/api/hospitals/E2E-H1/resources/ed_bed',
                  dict(capacity=4, occupied=4, handled_by='SYNTHETIC-DESK'))
        current = self.review()
        self.require('resource_change_flags_review', current['review']['state'] == 'RECHECK_REQUIRED')
        self.require('resource_change_is_not_automatic_reroute',
            self.patient()['destination_hospital_id'] == 'E2E-H1'
            and not self.patient()['needs_adjustment']
            and current['workflow']['state'] == 'NOT_STARTED')
        events = self.call('GET', f'/api/patients/{self.pid}/important-events?actor_id={self.actor}')
        self.require('recheck_event_is_queryable', any(
            item['type'] == 'transport_recheck_required' for item in events['items']))
        return current['review']

    def decision(self, choice, review, *, expected=200):
        return self.call('POST', f'/api/hospitals/E2E-H1/patients/{self.pid}/decision',
            dict(decision=choice, note='Synthetic hospital decision', handled_by='SYNTHETIC-DESK',
                 expected_review_version=review['version'], expected_risk_signature=review['risk_signature']),
            expected=expected)


def normal_arrival(flow):
    flow.depart(flow.setup_accepted())
    flow.arrive('E2E-H1')


def waiting_treatment_change(flow):
    flow.setup_accepted()
    before = flow.patient()
    session = flow.say(flow.new_draft(linked=True, text='맥박 90회'), 'ST 상승 확인됨')
    flow.require('draft_change_preserves_current_acceptance', flow.patient()['version'] == before['version']
        and flow.inquiry('E2E-H1')['state'] == 'ACCEPTED')
    session = flow.voice(session, 'confirm', confirmed=True)['session']
    review = flow.call('GET', f'/api/voice/sessions/{session["id"]}/treatment-review?actor_id={flow.actor}')['review']
    flow.require('condition_change_preview_is_actionable', review['can_apply']
        and review['proposed']['treatment_profile_id'] == 'TR_CARDIAC_INTERVENTION')
    applied = flow.call('POST', f'/api/voice/sessions/{session["id"]}/treatment-review/apply',
        flow.common(session, expected_patient_version=review['patient_version'], review_token=review['token'],
            confirmed=True, selection=copy.deepcopy(review['selection']), allow_reduction=True))
    flow.require('confirmed_change_invalidates_old_acceptance',
        applied['patient']['version'] > before['version'] and flow.inquiry('E2E-H1')['state'] == 'CANCELLED')
    flow.require('reevaluation_is_requested', applied['reevaluation']['requested'])
    stale = flow.call('GET', f'/api/patients/{flow.pid}/recommendation')
    flow.require('old_plan_is_not_reused', stale['status'] == 'STALE' and stale['result'] is None)
    # No acceptance for the revised information: the solver must leave it unassigned.
    flow.require('new_acceptance_required', not flow.plan()['result']['confirmable_now'])
    flow.tick_inquiry()
    candidates = flow.call('GET', f'/api/patients/{flow.pid}/candidates')
    flow.require('changed_requirements_filter_candidates', 'E2E-H1' not in {
        item['hospital_id'] for item in candidates['candidates']})
    q = flow.accept('E2E-H2')
    revised = flow.plan()
    flow.require('new_version_recommended_after_acceptance', revised['result']['hospital_id'] == 'E2E-H2'
        and revised['result']['confirmable_now'] and q['patient_version'] == flow.patient()['version'])
    flow.depart(q)
    flow.arrive('E2E-H2')


def enroute_resource_keep(flow):
    flow.depart(flow.setup_accepted())
    review = flow.loss()
    flow.decision('keep', review)
    flow.tick_transport()
    flow.require('hospital_keep_preserves_destination', not flow.patient()['needs_adjustment']
        and flow.patient()['destination_hospital_id'] == 'E2E-H1'
        and flow.review()['review']['state'] == 'MONITORING'
        and flow.review()['workflow']['state'] == 'NOT_STARTED')


def enroute_resource_reroute(flow):
    flow.depart(flow.setup_accepted())
    old_review = flow.review()['review']
    current = flow.loss()
    rejected = flow.decision('adjust', old_review, expected=409)
    flow.require('old_transport_approval_cannot_adjust_destination',
        rejected['error'] == 'TRANSPORT_REVIEW_CONFLICT' and not flow.patient()['needs_adjustment']
        and flow.patient()['destination_hospital_id'] == 'E2E-H1')
    flow.decision('adjust', current)
    flow.tick_transport()
    pending = flow.inquiry('E2E-H2')
    response = flow.call('POST', f'/api/patients/{flow.pid}/change-destination',
        dict(inquiry_id=pending['id'], lat=35.165, lon=126.85), expected=409)
    flow.require('pending_alternative_cannot_be_destination', response['error'] == 'NOT_ACCEPTED'
        and flow.patient()['destination_hospital_id'] == 'E2E-H1')
    flow.call('PATCH', f'/api/patients/{flow.pid}/transport-position',
              dict(lat=35.165, lon=126.85, measured_at=flow.now))
    q = flow.accept('E2E-H2')
    flow.tick_transport()
    flow.require('alternative_acceptance_waits_for_human_change',
        flow.review()['workflow']['state'] == 'AWAITING_CHANGE'
        and flow.patient()['destination_hospital_id'] == 'E2E-H1')
    changed = flow.call('POST', f'/api/patients/{flow.pid}/change-destination', {'inquiry_id': q['id']})
    flow.require('explicit_reroute_uses_new_acceptance', changed['destination_hospital_id'] == 'E2E-H2'
        and changed['state'] == 'IN_TRANSIT' and not changed['needs_adjustment'])
    flow.arrive('E2E-H2')


def optional_unavailable(flow):
    flow.create(optional=True)
    flow.tick_inquiry()
    flow.accept('E2E-H1')
    result = flow.plan()
    guidance = result['input_review']
    flow.require('unmeasured_optional_is_information_only', guidance['status'] == 'INFORMATION_ONLY'
        and not guidance['blocking'] and not guidance['requires_review']
        and result['result']['confirmable_now'])


def no_hospital_response(flow):
    flow.create()
    flow.tick_inquiry()
    flow.require('no_acceptance_means_no_assignment', not flow.plan()['result']['confirmable_now'])
    flow.now += 61
    flow.tick_inquiry()
    flow.now = NOW+301
    flow.tick_inquiry()
    flow.require('deadline_does_not_invent_hospital_response', flow.workflow()['state'] == 'EXHAUSTED'
        and all(q['state'] in ('PENDING', 'CHECKING') for q in flow.patient()['inquiries'])
        and flow.patient()['destination_hospital_id'] is None)


def no_available_capacity(flow):
    for hid in ('E2E-H1', 'E2E-H2', 'E2E-H3'):
        flow.call('PUT', f'/api/hospitals/{hid}/resources/ed_bed', {'capacity': 0, 'occupied': 0})
    flow.create()
    flow.tick_inquiry()
    flow.require('no_capacity_creates_no_false_inquiries', flow.patient()['inquiries'] == []
        and flow.workflow()['state'] == 'EXHAUSTED')
    flow.require('no_capacity_does_not_force_assignment', not flow.plan()['result']['confirmable_now']
        and flow.patient()['destination_hospital_id'] is None)


def stale_treatment_approval(flow):
    flow.setup_accepted()
    before = flow.patient()
    session = flow.new_draft(linked=True, text='ST 상승 확인됨')
    session = flow.voice(session, 'confirm', confirmed=True)['session']
    review = flow.call('GET', f'/api/voice/sessions/{session["id"]}/treatment-review?actor_id={flow.actor}')['review']
    session = flow.say(session, '맥박이 지금 100으로 올랐어')
    session = flow.voice(session, 'confirm', confirmed=True)['session']
    rejected = flow.call('POST', f'/api/voice/sessions/{session["id"]}/treatment-review/apply',
        flow.common(session, expected_patient_version=review['patient_version'], review_token=review['token'],
            confirmed=True, selection=review['selection'], allow_reduction=True), expected=409)
    flow.require('stale_approval_rejected_without_partial_change',
        rejected['error'] == 'TREATMENT_REVIEW_STALE' and flow.patient()['version'] == before['version']
        and flow.inquiry('E2E-H1')['state'] == 'ACCEPTED')


def missing_r1_rejected(flow):
    session = flow.new_draft(text='맥박 90회. 혈압 120에 80.')
    session = flow.voice(session, 'confirm', confirmed=True)['session']
    rejected = flow.call('POST', f'/api/voice/sessions/{session["id"]}/submit',
        flow.common(session, start_inquiry=True, inquiry_policy='fixed_3',
                    context=dict(consent=True, lat=35.16, lon=126.85)), expected=409)
    flow.require('required_r1_missing_blocks_first_inquiry_atomically',
        rejected['error'] == 'R1_FIELDS_REQUIRED' and flow.conn.execute(
            'SELECT COUNT(*) FROM patients').fetchone()[0] == 0
        and flow.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0] == 0)


def write_report(output, report):
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 음성 입력부터 배정·조건 변경까지 서버 연결 검증', '',
        f'- 기준: `{report["baseline_before"]["baseline_id"]}`',
        f'- 전체 검증 유효: `{report["pass"]}` (시나리오와 소스 무결성을 함께 확인)',
        f'- 결과: {report["passed"]} 통과 / {report["failed"]} 실패',
        '- 실제 API와 워커 코드 사용, 합성 환자·합성 병원·통제된 시각.',
        '- CP-SAT 예산 3초. 시간제한 대체 해/최적 증명 여부는 각 solver 기록에 보존.',
        '- 실제 음성인식·UI 조작·외부 도로 API·병원 전송·푸시 전달은 시험하지 않음.',
        '- 기능·상태 일관성 검증이며 현행 체계 대비 속도 우위·임상 효과 검증이 아님.', '',
        '| 시나리오 | 결과 | 확인 항목 수 | 최종 상태 |', '|---|---|---:|---|']
    for case in report['cases']:
        lines.append(f'| {case["name"]} | {"PASS" if case["pass"] else "FAIL"} | '
                     f'{len(case["steps"])} | {case.get("final_state", "-")} |')
    lines += ['', f'기준 해시 일치: `{report["baseline_after"]["ok"]}` / '
              f'연결 의존 소스 변경 없음: `{report["sources_stable"]}`',
              '', '세부 API 호출·확인 근거·솔버 상태는 `report.json`에 기록했다.']
    (output/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def run(output, *, cases=CASE_NAMES):
    selected = tuple(cases)
    if not selected or len(set(selected)) != len(selected) or any(name not in CASE_NAMES for name in selected):
        raise ValueError('알려진 고유 시나리오를 하나 이상 지정하세요.')
    baseline = allocation_baseline.verify()
    if not baseline['ok']:
        raise ValueError('기준 알고리즘이 변경돼 실행을 중단합니다: '+json.dumps(baseline['issues'], ensure_ascii=False))
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = fingerprints()
    # Preserve the exact source bytes as well as fingerprints: several current
    # dependencies are still uncommitted, so a Git revision alone is insufficient.
    snapshot_hashes = {}
    for name in SOURCE_FILES:
        content = (ROOT/name).read_bytes()
        target = output/'sources'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as handle:
            handle.write(content)
        snapshot_hashes[name] = hashlib.sha256(content).hexdigest()
    protocol = dict(revision=REVISION, planned_cases=list(selected), baseline_id=baseline['baseline_id'],
        baseline_manifest_sha256=baseline['manifest_sha256'], solver_budget_seconds=3,
        inquiry_policy='fixed_3', travel_provider='synthetic_use_kakao_false',
        clock='controlled_epoch_seconds', worker_mode='actual_code_manual_cycles',
        speech_mode='synthetic_transcripts_not_microphone_or_stt')
    (output/'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf-8')
    report = dict(revision=REVISION, synthetic=True, actual_speech_count=0, actual_hospital_requests=0,
        external_requests=0, existing_business_db_accessed=False, frontends_modified=False,
        mode='actual_http_and_manual_worker_cycles', solver_budget_seconds=3,
        travel_provider='synthetic_use_kakao_false', inquiry_policy='fixed_3',
        controlled_clock=True, baseline_before=baseline, source_hashes_before=before,
        snapshot_hashes=snapshot_hashes, source_snapshot_matches=before == snapshot_hashes,
        planned_cases=list(selected), cases=[], passed=0, failed=0)
    for name in selected:
        item = dict(name=name, pass_=False)
        started = time.perf_counter()
        flow = Flow(output/name)
        try:
            with flow:
                globals()[name](flow)
                item['final_state'] = flow.patient()['state'] if flow.pid else 'NO_PATIENT'
                item['pass'] = True
        except Exception:
            item['pass'] = False
            item['error'] = traceback.format_exc()
        item.pop('pass_', None)
        item.update(steps=flow.steps, requests=flow.requests, solver_runs=flow.solves,
                    verification_wall_seconds=time.perf_counter()-started)
        report['cases'].append(item)
        report['passed' if item['pass'] else 'failed'] += 1
    integrity_errors = []
    try:
        after = fingerprints()
    except OSError as error:
        # A concurrent editor can remove a file between checks. Keep all case
        # evidence and fail the run rather than losing its report completely.
        after = None
        integrity_errors.append(dict(stage='final_source_check', error=type(error).__name__))
    report.update(source_hashes_after=after, sources_stable=before == after,
                  integrity_errors=integrity_errors,
                  baseline_after=allocation_baseline.verify())
    report['pass'] = (report['failed'] == 0 and report['sources_stable']
                      and report['source_snapshot_matches'] and report['baseline_after']['ok'])
    write_report(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='새 출력 폴더. 기존 폴더/DB는 사용하지 않습니다.')
    parser.add_argument('--case', action='append', choices=CASE_NAMES)
    args = parser.parse_args()
    result = run(args.output, cases=tuple(args.case) if args.case else CASE_NAMES)
    print(json.dumps({key: result[key] for key in ('pass', 'passed', 'failed', 'sources_stable')}, ensure_ascii=False))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
