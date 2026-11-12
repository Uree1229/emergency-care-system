"""Server contract checks for reconnects, retries and concurrent team actions.

python -m server.team_connection_check --output .codex-build/team-connection-new

Fixed synthetic HTTP test suites use private CommonRuntime instances and real
background workers. Existing servers, business databases and UI files are not
used or modified. This is not a browser/offline or clinical performance test.
"""
import argparse
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from . import allocation_baseline, team_runtime_profile
from .team_release_info import SERVER_REQUIREMENTS

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'team-connection-check-20261030-v1'
TEST_MODULES = ('test_team_connection_races', 'test_team_connection_recovery')
TEST_DIRECTORY = 'tests/core'
SOURCE_FILES = tuple(sorted(set(allocation_baseline.SOURCE_PATHS) | {
    'server/team_connection_check.py', 'server/team_runtime.py', 'server/team_runtime_profile.py',
    'server/app.py', 'server/recommendation_view.py', 'server/allocation_input_review.py',
    'server/important_events.py', 'server/conversations.py', 'server/work_activity.py',
    'server/voice_input.py', 'server/voice_terms.py', 'server/voice_r1.py',
    'server/voice_followup.py', 'server/voice_commands.py', 'server/voice_lexicon.py',
    'server/voice_treatment_review.py', 'config/voice_commands.json',
    'config/paramedic_voice_terms.json', 'config/server_allocation_baseline_20261030.json',
    'config/team_runtime_profile_20261030.json', SERVER_REQUIREMENTS,
    *(f'{TEST_DIRECTORY}/{name}.py' for name in TEST_MODULES),
}))
CONTRACT = [
    dict(case='uncertain_voice_submit', request='POST /api/voice/sessions/{sid}/submit',
         rule='Reuse the same actor, request ID, operation and complete body for the uncertain retry.',
         next='Treat duplicate success as the original receipt; GET the current session and patient afterward.'),
    dict(case='changed_retry_body', status=409, error='VOICE_REQUEST_CONFLICT',
         next='Do not change the same request body or generate a second patient to hide an uncertain outcome.'),
    dict(case='stale_hospital_response', status=409, errors=['CONFLICT', 'REVIEW_VERSION_REQUIRED'],
         next='Reload the current inquiry and patient; obtain a new explicit response for that information version.'),
    dict(case='last_resource_confirmation', status=409, error='RESOURCE_CONFLICT',
         next='Keep the waiting patient unconfirmed, reload capacity/candidates, and ask for another valid acceptance.'),
    dict(case='important_event_recovery', request='GET /api/patients/{pid}/important-events',
         rule='Fetch does not mean displayed or acknowledged. Preserve the event ID and the stable actor ID.',
         next='On reconnect fetch unacknowledged_only separately from the forward event cursor.'),
    dict(case='acknowledge_before_display', status=409, error='DELIVERY_REQUIRED',
         next='Record delivered only after rendering. Record acknowledge only after the person confirms.'),
    dict(case='recommendation_refresh', statuses=['QUEUED', 'READY', 'STALE', 'ERROR', 'NOT_APPLICABLE'],
         next='Use GET for polling; clear stale actions. POST replan is an explicit request, not a poll.'),
    dict(case='restart', rule='Use the same dedicated DB; never seed/reset it to reconnect.',
         next='Recover current patient, inquiry, voice session and unacknowledged events by their original IDs.'),
]


def fingerprints():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def collect_evidence():
    return {name:deepcopy(getattr(sys.modules.get(name), 'CONNECTION_EVIDENCE', {}))
            for name in TEST_MODULES}


def load_suite():
    loader = unittest.TestLoader()
    return unittest.TestSuite(
        loader.discover(str(ROOT/TEST_DIRECTORY), pattern=name+'.py')
        for name in TEST_MODULES
    )


def _ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _ids(item)
        else:
            yield item.id()


def validate_evidence(planned, evidence, test_results):
    """A passing unittest alone is insufficient proof of the HTTP/worker path."""
    issues = []
    statuses = {item['test_id']:item['status'] for item in test_results}
    for test_id in planned:
        module = test_id.split('.', 1)[0]
        rows = evidence.get(module, {})
        row = rows.get(test_id) if isinstance(rows, dict) else None
        if not isinstance(row, dict):
            issues.append(dict(test_id=test_id, code='EVIDENCE_MISSING'))
            continue
        requests, checks = row.get('requests'), row.get('checks')
        valid_requests = isinstance(requests, list) and bool(requests) and all(
            isinstance(item, dict) and item.get('method') in ('GET','POST','PUT','PATCH','DELETE')
            and isinstance(item.get('path'), str) and item['path'].startswith('/api/')
            and type(item.get('status')) is int and 200 <= item['status'] <= 599 for item in requests)
        if not valid_requests:
            issues.append(dict(test_id=test_id, code='HTTP_EVIDENCE_MISSING_OR_INVALID'))
        valid_checks = isinstance(checks, list) and bool(checks) and all(
            (isinstance(item, str) and bool(item)) or
            (isinstance(item, dict) and isinstance(item.get('name'), str) and bool(item['name'])
             and item.get('pass') is True) for item in checks)
        if not valid_checks:
            issues.append(dict(test_id=test_id, code='ASSERTION_EVIDENCE_MISSING_OR_INVALID'))
        health = row.get('worker_health', {})
        start = row.get('workers_start', health.get('start') if isinstance(health, dict) else None)
        if not isinstance(start, dict) or not all(isinstance(start.get(name), dict)
                and start[name].get('state') == 'RUNNING' and start[name].get('enabled') is True
                and start[name].get('thread_alive') is True for name in ('planning','inquiry')):
            issues.append(dict(test_id=test_id, code='RUNNING_WORKER_EVIDENCE_MISSING'))
        stopped = row.get('workers_stopped', health.get('threads_stopped') if isinstance(health, dict) else None)
        if stopped is not True:
            issues.append(dict(test_id=test_id, code='STOPPED_WORKER_EVIDENCE_MISSING'))
        if statuses.get(test_id) != 'PASS':
            issues.append(dict(test_id=test_id, code='TEST_DID_NOT_PASS'))
    return issues


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = {}
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.perf_counter()
        self.records[test.id()] = dict(test_id=test.id(), status='RUNNING')
        super().startTest(test)

    def _record(self, test, status):
        # Class setup/teardown errors are _ErrorHolder objects with no startTest.
        row = self.records.setdefault(test.id(), dict(test_id=test.id()))
        start = self.started.get(test.id())
        row.update(status=status,
            verification_wall_seconds=time.perf_counter()-start if start is not None else None)

    def addSuccess(self, test):
        self._record(test, 'PASS')
        super().addSuccess(test)

    def addFailure(self, test, err):
        self._record(test, 'FAIL')
        super().addFailure(test, err)

    def addError(self, test, err):
        self._record(test, 'ERROR')
        super().addError(test, err)

    def addSkip(self, test, reason):
        self._record(test, 'SKIP')
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._record(test, 'EXPECTED_FAILURE')
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._record(test, 'UNEXPECTED_SUCCESS')
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._record(test, 'FAIL')
        super().addSubTest(test, subtest, err)


def write_report(output, report, log):
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'tests.log').write_text(log, encoding='utf-8')
    (output/'connection-contract.json').write_text(json.dumps(CONTRACT, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = ['# 팀원 화면 연결을 위한 서버 계약 검증', '',
        f'- 전체 통과: `{report["pass"]}`',
        f'- 테스트 {report["tests_run"]}개 / 실패 {report["failures"]} / 오류 {report["errors"]} / 생략 {report["skipped"]}',
        f'- 실행 전후 소스 일치: `{report["sources_stable"]}`, 소스 사본 일치: `{report["source_snapshot_matches"]}`',
        f'- 테스트별 HTTP·확인 항목·워커 증거 일치: `{not report["evidence_errors"]}`',
        '- 고정된 합성 HTTP 테스트와 실제 공통 실행 워커를 사용한다. UI·실제 마이크·실제 병원은 시험하지 않는다.',
        '- 아래 시간은 검증 실행시간이며 환자 선정·이동·의료진 업무시간 지표가 아니다.', '',
        '| 검사 | 결과 |', '|---|---|']
    rows.extend(f'| {item["test_id"]} | {item["status"]} |' for item in report['test_results'])
    rows += ['', '요청·검증 근거는 `report.json`, 오류 원문은 `tests.log`, 화면 후속 동작은 `connection-contract.json`에 보존했다.',
        '서버 계약 통과가 팀원 화면의 재접속·알림 복구 구현 완료를 뜻하지 않는다.']
    (output/'report.md').write_text('\n'.join(rows)+'\n', encoding='utf-8')


def run(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('기존 출력은 보존합니다. 새 출력 폴더를 지정하세요.')
    profile = team_runtime_profile.verify_profile(mode='full')
    if not profile['ok']:
        raise ValueError('공통 실행 기준 검사 실패: '+json.dumps(profile['issues'], ensure_ascii=False))
    before = fingerprints()
    output.mkdir(parents=True, exist_ok=False)
    snapshot_hashes = {}
    for name in SOURCE_FILES:
        content = (ROOT/name).read_bytes()
        target = output/'sources'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as handle:
            handle.write(content)
        snapshot_hashes[name] = hashlib.sha256(content).hexdigest()
    suite = load_suite()
    planned = list(_ids(suite))
    protocol = dict(revision=REVISION, modules=list(TEST_MODULES), planned_tests=planned,
        profile=profile, source_hashes=before, actual_speech_count=0, actual_hospital_requests=0,
        existing_business_db_accessed=False, ui_modified=False,
        scope='synthetic API reconnect/retry/concurrency contracts; real local workers',
        clinical_effectiveness=False, browser_offline_test=False)
    (output/'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf-8')
    stream = io.StringIO()
    old_temp = tempfile.tempdir
    (output/'tmp').mkdir()
    try:
        tempfile.tempdir = str(output/'tmp')
        result = unittest.TextTestRunner(stream=stream, verbosity=2,
            resultclass=EvidenceResult).run(suite)
    finally:
        tempfile.tempdir = old_temp
    integrity_errors = []
    try:
        after = fingerprints()
    except OSError as error:
        after = None
        integrity_errors.append(dict(stage='final_sources', error=type(error).__name__))
    evidence = collect_evidence()
    evidence_errors = validate_evidence(planned, evidence, list(result.records.values()))
    final_profile = team_runtime_profile.verify_profile(mode='full')
    report = dict(protocol, profile_after=final_profile, tests_run=result.testsRun,
        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        expected_failures=len(result.expectedFailures), unexpected_successes=len(result.unexpectedSuccesses),
        test_results=list(result.records.values()), evidence=evidence,
        evidence_errors=evidence_errors,
        source_hashes_after=after, sources_stable=before == after,
        snapshot_hashes=snapshot_hashes, source_snapshot_matches=before == snapshot_hashes,
        integrity_errors=integrity_errors)
    report['pass'] = (result.wasSuccessful() and result.testsRun == len(planned) and result.testsRun > 0
        and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
        and not report['evidence_errors']
        and report['sources_stable'] and report['source_snapshot_matches'] and final_profile['ok'])
    write_report(output, report, stream.getvalue())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        report = run(args.output)
    except (ValueError, OSError) as error:
        print(json.dumps({'pass':False, 'error':str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({key:report[key] for key in ('pass','tests_run','failures','errors','skipped','sources_stable')}, ensure_ascii=False))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
