"""저장된 서버 문의 실험을 독립적으로 재집계하고 원시 이력을 감사한다.

python -m server.inquiry_experiment_audit REPORT.json --output AUDIT.json

실행기·집계기·정책 모듈을 import하지 않는다. DB와 네트워크를 사용하지 않으며
보고서와 현재 소스 파일만 읽는다. 실패하면 상세 결과를 저장하고 종료 코드 1을 낸다.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import platform
import sys


SOURCE_FILES = (
    'server/inquiry_experiment.py', 'server/inquiry_experiment_scenarios.py',
    'server/inquiry_workflow.py', 'server/inquiry_policies.py',
    'server/inquiry_observations.py', 'server/service.py', 'server/db.py',
    'server/treatment_binding.py', 'simulation/observed_inquiry.py',
)
POLICIES = ('fixed_1', 'fixed_3', 'fixed_5', 'observed_v2')
V3_VARIANTS = tuple(f'v3_w{wait}_h{history}' for wait in (45, 60, 90) for history in (600, 900))
V3_SOURCE_FILES = SOURCE_FILES + ('server/inquiry_v3.py', 'server/inquiry_v3_experiment.py')
V3_SELECTION_RULE = {
    'population': 'development_only',
    'aggregation': 'equal_weight_per_scenario_family',
    'ordered_objectives': ['failed_fraction_mean', 'selection_wait_capped_mean',
                           'inquiries_per_patient_mean'],
    'comparison': 'lexicographic', 'tie_break': 'stable_policy_name',
    'failure_wait_seconds': 300,
}
TRACE_TYPES = {'queue_added', 'review_started', 'review_finished',
               'review_cancelled_before_start', 'reply_suppressed_after_cancel',
               'reply_omitted', 'reply_accept', 'reply_decline',
               'destination_confirmed'}


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def _canonical_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _is_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


class Checks:
    def __init__(self):
        self.total = 0
        self.failures = []
        self.groups = defaultdict(lambda: {'checks': 0, 'failed': 0})

    def check(self, condition, path, group, *, actual=None, expected=None):
        self.total += 1
        self.groups[group]['checks'] += 1
        if not condition:
            self.groups[group]['failed'] += 1
            self.failures.append(dict(path=path, group=group, actual=actual, expected=expected))
        return bool(condition)

    def equal(self, actual, expected, path, group):
        if type(expected) is float:
            same = (type(actual) in (int, float) and math.isfinite(actual)
                    and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9))
        else:
            same = type(actual) is type(expected) and actual == expected
        return self.check(same, path, group, actual=actual, expected=expected)


def _patient_metrics(rows):
    selected = [p for p in rows if not p['failed']]
    return dict(patient_count=len(rows), selected_count=len(selected),
                failed_count=len(rows) - len(selected),
                selection_seconds_mean=_mean(p['selection_seconds'] for p in selected),
                selection_wait_capped_mean=_mean(300 if p['failed'] else p['selection_seconds'] for p in rows),
                inquiries_per_patient=_mean(p['inquiry_count'] for p in rows),
                estimated_arrival_seconds_mean=_mean(p['estimated_arrival_seconds'] for p in selected))


def _check_fields(checks, actual, expected, prefix, group):
    for key, value in expected.items():
        checks.equal(actual.get(key), value, prefix + '.' + key, group)


def _audit_trace(checks, branch, scenario, prefix):
    queues = defaultdict(list)
    active = defaultdict(dict)
    started, finished, accepted, confirms = {}, {}, {}, {}
    workers = {h['id']: h['workers'] for h in scenario['hospitals']}
    inputs = {p['id']: p for p in scenario['patients']}
    outcomes = {p['id']: p for p in branch['patients']}
    potentials = {(p['patient_id'], p['hospital_id']): p for p in scenario['potentials']}
    last_at = -1
    for index, event in enumerate(branch['trace']):
        path = f'{prefix}.trace[{index}]'
        at, typ = event['at'], event['type']
        pid, hid, iid = event['patient_id'], event['hospital_id'], event['inquiry_id']
        checks.check(type(at) is int and at >= last_at, path + '.at', 'trace_clock',
                     actual=at, expected=f'integer >= {last_at}')
        last_at = at
        checks.check(typ in TRACE_TYPES, path + '.type', 'trace_contract', actual=typ)
        checks.check(pid in inputs and hid in workers, path + '.identities', 'trace_contract')
        if pid not in inputs or hid not in workers:
            continue
        potential = potentials[(pid, hid)]
        if typ == 'queue_added':
            checks.check(iid not in queues[hid] and iid not in started, path + '.unique', 'trace_queue')
            queues[hid].append(iid)
        elif typ == 'review_cancelled_before_start':
            checks.check(iid in queues[hid] and iid not in started, path + '.queued', 'trace_queue')
            if iid in queues[hid]:
                queues[hid].remove(iid)
            p = outcomes[pid]
            end = p['created_at'] + 300 if p['failed'] else p['selected_at']
            checks.check(at >= end, path + '.after_patient_end', 'trace_cancellation')
        elif typ == 'review_started':
            checks.check(bool(queues[hid]) and queues[hid][0] == iid, path + '.fifo', 'trace_queue',
                         actual=iid, expected=queues[hid][0] if queues[hid] else None)
            if iid in queues[hid]:
                queues[hid].remove(iid)
            checks.check(iid not in started, path + '.once', 'trace_work')
            checks.equal(event['service_seconds'], potential['service_seconds'], path + '.service_seconds', 'trace_work')
            checks.equal(event['due_at'], at + potential['service_seconds'], path + '.due_at', 'trace_work')
            checks.check(len(active[hid]) < workers[hid], path + '.worker_capacity', 'trace_work')
            active[hid][iid] = event['due_at']
            started[iid] = event
        elif typ == 'review_finished':
            checks.check(iid in active[hid] and iid not in finished, path + '.active', 'trace_work')
            if iid in active[hid]:
                checks.equal(at, active[hid].pop(iid), path + '.exact_due', 'trace_work')
            checks.equal(event['service_seconds'], potential['service_seconds'], path + '.service_seconds', 'trace_work')
            if iid in started:
                checks.equal(event['started_at'], started[iid]['at'], path + '.started_at', 'trace_work')
            finished[iid] = event
        elif typ in ('reply_accept', 'reply_decline', 'reply_omitted', 'reply_suppressed_after_cancel'):
            checks.check(iid in finished and finished[iid]['at'] == at, path + '.after_review', 'trace_reply')
            if typ == 'reply_suppressed_after_cancel':
                p = outcomes[pid]
                end = p['created_at'] + 300 if p['failed'] else p['selected_at']
                checks.check(at >= end, path + '.after_patient_end', 'trace_cancellation')
            elif typ == 'reply_omitted':
                checks.equal(potential['no_response'], True, path + '.potential', 'trace_reply')
            else:
                checks.equal(potential['no_response'], False, path + '.not_omitted', 'trace_reply')
                checks.equal(potential['accept'], typ == 'reply_accept', path + '.potential', 'trace_reply')
                if typ == 'reply_accept':
                    accepted[iid] = event
        elif typ == 'destination_confirmed':
            checks.check(pid not in confirms, path + '.once', 'trace_selection')
            checks.check(iid in accepted and accepted[iid]['at'] == at, path + '.same_time_accept', 'trace_selection')
            candidates = [e for e in accepted.values() if e['patient_id'] == pid and e['at'] <= at]
            if candidates:
                best = min(candidates, key=lambda e: (inputs[pid]['travel_seconds'][e['hospital_id']], e['hospital_id']))
                checks.equal(iid, best['inquiry_id'], path + '.shared_confirmation_rule', 'trace_selection')
            confirms[pid] = event
    checks.check(not any(queues.values()) and not any(active.values()), prefix + '.drained', 'trace_work')
    checks.equal(sorted(started), sorted(finished), prefix + '.started_finished_ids', 'trace_work')
    return dict(review_work_seconds=sum(e['service_seconds'] for e in started.values()),
                review_started_count=len(started),
                cancelled_before_start_count=sum(e['type'] == 'review_cancelled_before_start' for e in branch['trace']),
                reply_suppressed_count=sum(e['type'] == 'reply_suppressed_after_cancel' for e in branch['trace'])), confirms


def _audit_branch(checks, branch, result, scenario, policy_specs):
    prefix = result['scenario']['id'] + '.' + branch['policy']
    checks.equal(branch['snapshot_sha256'], result['initial_snapshot_sha256'], prefix + '.snapshot', 'hashes')
    checks.equal(branch['environment_sha256'], result['environment_sha256'], prefix + '.environment', 'hashes')
    checks.equal(branch['policy_spec'], policy_specs[branch['policy']], prefix + '.policy_spec', 'policy_contract')
    inputs = {p['id']: p for p in scenario['patients']}
    rows = branch['patients']
    checks.equal(sorted(p['id'] for p in rows), sorted(inputs), prefix + '.patients', 'coverage')
    work, confirms = _audit_trace(checks, branch, scenario, prefix)
    for patient in rows:
        pid = patient['id']
        source, path = inputs[pid], prefix + '.patients.' + pid
        checks.equal(patient['created_at'], source['at'], path + '.created_at', 'patient_outcomes')
        checks.check(type(patient['failed']) is bool, path + '.failed_type', 'patient_outcomes')
        contacts = [e['hospital_id'] for e in branch['trace'] if e['type'] == 'queue_added' and e['patient_id'] == pid]
        checks.equal(patient['contacted_hospitals'], contacts, path + '.contacts_vs_trace', 'patient_outcomes')
        checks.equal(patient['inquiry_count'], len(contacts), path + '.inquiry_count', 'patient_outcomes')
        checks.equal(len(contacts), len(set(contacts)), path + '.unique_contacts', 'patient_outcomes')
        if patient['failed']:
            checks.check(pid not in confirms, path + '.no_confirmation', 'patient_outcomes')
            for key in ('selected_at', 'selection_seconds', 'travel_seconds', 'estimated_arrival_seconds', 'destination_hospital_id'):
                checks.equal(patient[key], None, path + '.' + key, 'patient_outcomes')
        else:
            elapsed = patient['selection_seconds']
            checks.check(type(elapsed) is int and 0 <= elapsed <= 300, path + '.within_300', 'patient_outcomes')
            checks.equal(patient['selected_at'], source['at'] + elapsed, path + '.selected_at', 'patient_outcomes')
            checks.equal(patient['travel_seconds'], source['travel_seconds'][patient['destination_hospital_id']], path + '.travel', 'patient_outcomes')
            checks.equal(patient['estimated_arrival_seconds'], elapsed + patient['travel_seconds'], path + '.arrival', 'patient_outcomes')
            checks.check(pid in confirms, path + '.confirmation_exists', 'patient_outcomes')
            if pid in confirms:
                checks.equal(confirms[pid]['at'], patient['selected_at'], path + '.confirmation_time', 'patient_outcomes')
                checks.equal(confirms[pid]['hospital_id'], patient['destination_hospital_id'], path + '.confirmation_hospital', 'patient_outcomes')
    expected = _patient_metrics(rows) | work
    _check_fields(checks, branch['metrics'], expected, prefix + '.metrics', 'branch_metrics')


def _independent_summaries(results, policies):
    rows = []
    for family in ['ALL'] + sorted({r['scenario']['family'] for r in results}):
        records = results if family == 'ALL' else [r for r in results if r['scenario']['family'] == family]
        for policy in policies:
            branches = [next(b for b in r['branches'] if b['policy'] == policy) for r in records]
            patients = [p for b in branches for p in b['patients']]
            metrics = _patient_metrics(patients)
            work = sum(e['service_seconds'] for b in branches for e in b['trace'] if e['type'] == 'review_started')
            starts = sum(e['type'] == 'review_started' for b in branches for e in b['trace'])
            common = only_policy = only_base = both_failed = 0
            selection_deltas, arrival_deltas = [], []
            for record, branch in zip(records, branches):
                baseline = {p['id']: p for b in record['branches'] if b['policy'] == 'fixed_3' for p in b['patients']}
                for patient in branch['patients']:
                    base = baseline[patient['id']]
                    if not patient['failed'] and not base['failed']:
                        common += 1
                        selection_deltas.append(patient['selection_seconds'] - base['selection_seconds'])
                        arrival_deltas.append(patient['estimated_arrival_seconds'] - base['estimated_arrival_seconds'])
                    elif not patient['failed']:
                        only_policy += 1
                    elif not base['failed']:
                        only_base += 1
                    else:
                        both_failed += 1
            rows.append(dict(family=family, policy=policy, scenario_count=len(records), **metrics,
                             review_work_seconds=work, review_work_seconds_per_patient=work / len(patients),
                             review_started_per_patient=starts / len(patients),
                             common_patient_count_vs_fixed3=common, selected_only_policy_vs_fixed3=only_policy,
                             selected_only_fixed3=only_base, common_failed_vs_fixed3=both_failed,
                             common_selection_difference_vs_fixed3=_mean(selection_deltas),
                             common_arrival_difference_vs_fixed3=_mean(arrival_deltas)))
    return rows


def _independent_macro(summary, policies):
    output = []
    for policy in policies:
        values = [row for row in summary if row['family'] != 'ALL' and row['policy'] == policy]
        output.append(dict(policy=policy, family_count=len(values),
                           success_fraction_mean=_mean(row['selected_count'] / row['patient_count'] for row in values),
                           selection_wait_capped_mean=_mean(row['selection_wait_capped_mean'] for row in values),
                           inquiries_per_patient_mean=_mean(row['inquiries_per_patient'] for row in values),
                           review_work_seconds_per_patient_mean=_mean(row['review_work_seconds_per_patient'] for row in values)))
    return output


def _independent_selection(macro):
    """실행기의 selector를 import하지 않고 개발 결과에서 선택 순서를 다시 계산한다."""
    values = {row['policy']: row for row in macro}
    candidates = []
    for name in V3_VARIANTS:
        row = values[name]
        score = [1 - row['success_fraction_mean'], row['selection_wait_capped_mean'],
                 row['inquiries_per_patient_mean']]
        candidates.append((tuple(score) + (name,), name, score))
    _, name, score = min(candidates)
    return dict(name=name, score=dict(zip(V3_SELECTION_RULE['ordered_objectives'], score)), score_vector=score)


def _audit_v3_contract(checks, report, policies):
    kind = report['kind']
    checks.equal(report.get('selection_rule'), V3_SELECTION_RULE, 'selection_rule', 'selection_contract')
    if kind == 'server_inquiry_v3_development':
        expected = POLICIES + V3_VARIANTS
    else:
        chosen = report['selected_policy']['name']
        checks.check(chosen in V3_VARIANTS, 'selected_policy.known_variant', 'selection_contract', actual=chosen)
        expected = POLICIES + (chosen,)
    checks.equal(sorted(policies), sorted(expected), 'policies.coverage', 'coverage')
    for name, spec in policies.items():
        if name in V3_VARIANTS:
            wait, history = [int(value[1:]) for value in name.split('_')[1:]]
            checks.equal(spec.get('wait_seconds'), wait, 'policies.' + name + '.wait_seconds', 'selection_contract')
            checks.equal(spec.get('review_seconds'), 15, 'policies.' + name + '.review_seconds', 'selection_contract')
            checks.equal(spec.get('batch_size'), 1, 'policies.' + name + '.batch_size', 'selection_contract')
            checks.equal(spec.get('research_only'), True, 'policies.' + name + '.research_only', 'selection_contract')
            config = spec.get('config', {})
            for key, value in (('max_wait_seconds', wait), ('history_window_seconds', history), ('window_seconds', history)):
                checks.equal(config.get(key), value, 'policies.' + name + '.config.' + key, 'selection_contract')


def _audit_v3_selection(checks, report, macro, policies):
    if report['kind'] == 'server_inquiry_v3_development':
        expected = _independent_selection(macro)
        checks.equal(report.get('selected_policy'), expected, 'selected_policy', 'selection_recomputed')
        return
    # 검증은 개발 잠금에만 의존한다. 검증 결과에서 승자를 새로 계산하지 않는다.
    metadata = report['validation_lock']
    raw = Path(metadata['path']).read_bytes()
    checks.equal(hashlib.sha256(raw).hexdigest(), metadata['sha256'], 'validation_lock.disk_sha256', 'lock_integrity')
    lock = json.loads(raw.decode('utf-8'))
    body = {key: value for key, value in lock.items() if key != 'sha256'}
    checks.equal(_canonical_digest(body), lock.get('sha256'), 'validation_lock.content_integrity', 'lock_integrity')
    checks.equal(lock.get('sha256'), metadata.get('content_sha256'), 'validation_lock.content_sha256', 'lock_integrity')
    checks.equal(lock.get('kind'), 'server_inquiry_v3_development_lock', 'validation_lock.kind', 'lock_integrity')
    checks.equal(lock.get('selection_rule'), V3_SELECTION_RULE, 'validation_lock.selection_rule', 'lock_integrity')
    checks.equal(lock.get('source_sha256'), report['source_sha256'], 'validation_lock.source_sha256', 'lock_integrity')
    development_seeds = lock['development_seeds']
    checks.equal(report.get('development_seeds'), development_seeds, 'validation_lock.development_seeds', 'lock_integrity')
    forbidden = set(range(6101, 6109)).union(*(range(start, start + 20) for start in (1001, 2001, 3001, 4001, 5001)))
    checks.check(not (set(report['seeds']) & (forbidden | set(development_seeds))),
                 'seeds.validation_disjoint', 'selection_contract', actual=report['seeds'])
    chosen = lock['chosen_policy']
    checks.check(chosen in V3_VARIANTS, 'validation_lock.chosen_known', 'lock_integrity', actual=chosen)
    checks.equal(policies.get(chosen), lock['chosen_policy_spec'], 'validation_lock.chosen_spec', 'lock_integrity')
    checks.equal([policies.get(name) for name in POLICIES], lock['baseline_policy_specs'],
                 'validation_lock.baseline_specs', 'lock_integrity')
    development_raw = Path(lock['development_report_path']).read_bytes()
    checks.equal(hashlib.sha256(development_raw).hexdigest(), lock['development_report_sha256'],
                 'validation_lock.development_report_sha256', 'lock_integrity')
    development = json.loads(development_raw.decode('utf-8'))
    checks.equal(development.get('kind'), 'server_inquiry_v3_development', 'development_report.kind', 'lock_integrity')
    checks.equal(development.get('seeds'), development_seeds, 'development_report.seeds', 'lock_integrity')
    checks.equal(development.get('source_sha256'), report['source_sha256'], 'development_report.source_sha256', 'lock_integrity')
    development_policies = {row['name']: row for row in development['policies']}
    checks.equal(sorted(development_policies), sorted(POLICIES + V3_VARIANTS), 'development_report.policies', 'lock_integrity')
    development_summary = _independent_summaries(development['results'], development_policies)
    expected = _independent_selection(_independent_macro(development_summary, development_policies))
    checks.equal(development.get('selected_policy'), expected, 'development_report.selection', 'selection_recomputed')
    checks.equal(report.get('selected_policy'), expected, 'selected_policy', 'selection_recomputed')
    checks.equal(lock.get('chosen_policy'), expected['name'], 'validation_lock.chosen_policy', 'selection_recomputed')
    checks.equal(lock.get('score'), expected['score'], 'validation_lock.score', 'selection_recomputed')
    checks.equal(lock.get('score_vector'), expected['score_vector'], 'validation_lock.score_vector', 'selection_recomputed')


def audit(report_path, *, source_root=None):
    report_path = Path(report_path)
    raw = report_path.read_bytes()
    checks = Checks()
    counts, independently_calculated, family_equal = {}, [], []
    warnings = []
    try:
        def invalid_constant(value):
            raise ValueError('non-finite JSON value: ' + value)
        report = json.loads(raw.decode('utf-8'), parse_constant=invalid_constant)
        checks.check(report['kind'] in ('server_inquiry_policy_synthetic_comparison',
                     'server_inquiry_v3_development', 'server_inquiry_v3_validation'), 'kind', 'report_contract', actual=report['kind'])
        v3 = report['kind'] in ('server_inquiry_v3_development', 'server_inquiry_v3_validation')
        checks.equal(report['parameters_frozen'], True, 'parameters_frozen', 'report_contract')
        checks.equal(report['clinically_validated'], False, 'clinically_validated', 'report_contract')
        source_root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
        hashes = report['source_sha256']
        source_files = V3_SOURCE_FILES if v3 else SOURCE_FILES
        checks.equal(sorted(hashes), sorted(source_files), 'source_sha256.coverage', 'source_fingerprints')
        for name in source_files:
            path = source_root / name
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            checks.equal(hashes.get(name), actual, 'source_sha256.' + name, 'source_fingerprints')
        if report.get('python_version') != platform.python_version():
            warnings.append('감사 Python 버전이 실험 기록과 다릅니다: ' + str(report.get('python_version')))
        policies = {p['name']: p for p in report['policies']}
        if v3:
            _audit_v3_contract(checks, report, policies)
        else:
            checks.equal(sorted(policies), sorted(POLICIES), 'policies.coverage', 'coverage')
        checks.equal(len(policies), len(report['policies']), 'policies.unique', 'coverage')
        scenarios = {s['id']: s for s in report['scenarios']}
        results = report['results']
        checks.equal(len(scenarios), len(report['scenarios']), 'scenarios.unique', 'coverage')
        checks.equal(sorted(r['scenario']['id'] for r in results), sorted(scenarios), 'results.coverage', 'coverage')
        seeds = report['seeds']
        checks.equal(len(seeds), len(set(seeds)), 'seeds.unique', 'coverage')
        families = sorted({s['family'] for s in scenarios.values()})
        for family in families:
            checks.equal(sorted(s['seed'] for s in scenarios.values() if s['family'] == family), sorted(seeds),
                         'seeds.' + family, 'coverage')
        for result in results:
            scenario = scenarios[result['scenario']['id']]
            prefix = scenario['id']
            checks.equal(result['scenario'], {k: scenario.get(k) for k in ('id', 'family', 'seed', 'description')}, prefix + '.metadata', 'coverage')
            checks.equal(result['environment_sha256'], _canonical_digest(scenario), prefix + '.environment_hash', 'hashes')
            checks.check(_is_digest(result['initial_snapshot_sha256']), prefix + '.snapshot_digest_format', 'hashes')
            checks.equal(sorted(b['policy'] for b in result['branches']), sorted(policies), prefix + '.policy_coverage', 'coverage')
            for branch in result['branches']:
                _audit_branch(checks, branch, result, scenario, policies)
        independently_calculated = _independent_summaries(results, policies)
        reported = {(s['family'], s['policy']): s for s in report['summary']}
        keys = {(s['family'], s['policy']) for s in independently_calculated}
        checks.equal(sorted(reported), sorted(keys), 'summary.coverage', 'summary')
        checks.equal(len(reported), len(report['summary']), 'summary.unique', 'summary')
        for expected in independently_calculated:
            key = (expected['family'], expected['policy'])
            _check_fields(checks, reported[key], expected, 'summary.' + '.'.join(key), 'summary')
        family_equal = _independent_macro(independently_calculated, policies)
        macro = {s['policy']: s for s in report['family_equal_summary']}
        checks.equal(sorted(macro), sorted(policies), 'family_equal_summary.coverage', 'family_equal_summary')
        checks.equal(len(macro), len(report['family_equal_summary']), 'family_equal_summary.unique', 'family_equal_summary')
        for expected in family_equal:
            _check_fields(checks, macro[expected['policy']], expected,
                          'family_equal_summary.' + expected['policy'], 'family_equal_summary')
        if v3:
            _audit_v3_selection(checks, report, family_equal, policies)
        counts = dict(scenarios=len(results), branches=sum(len(r['branches']) for r in results),
                      families=len(families), seeds=len(seeds),
                      patient_rows=sum(len(b['patients']) for r in results for b in r['branches']),
                      patients_per_policy={p: sum(len(b['patients']) for r in results for b in r['branches'] if b['policy'] == p) for p in policies})
    except Exception as error:
        checks.check(False, 'document_structure', 'fatal', actual=type(error).__name__ + ': ' + str(error))
    return dict(kind='independent_server_inquiry_experiment_audit', report_path=str(report_path.resolve()),
                report_sha256=hashlib.sha256(raw).hexdigest(), passed=not checks.failures,
                checks=checks.total, failed_checks=len(checks.failures), counts=counts,
                check_groups=dict(checks.groups), failures=checks.failures, warnings=warnings,
                independently_recomputed_summary=independently_calculated,
                independently_recomputed_family_equal_summary=family_equal,
                limitations=[
                    '초기 SQL 스냅샷의 실제 내용은 제공되지 않아 기록된 해시의 분기 간 일치만 검증했다.',
                    '저장된 원시 기록의 일관성과 재집계, 현재 소스의 지문을 검사한다. 실행 전체를 재현하거나 임상 효과를 검증하는 감사가 아니다.',
                ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve() == args.output.resolve():
        parser.error('output must differ from the input report')
    if args.output.exists():
        parser.error('output already exists; choose a new output path')
    try:
        result = audit(args.report)
    except OSError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(passed=result['passed'], checks=result['checks'], failed_checks=result['failed_checks'],
                          counts=result['counts'], output=str(args.output)), ensure_ascii=False))
    sys.exit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
