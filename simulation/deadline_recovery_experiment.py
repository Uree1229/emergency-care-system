"""Paired nonblocking revalidation of actually received late hospital replies.

The previous scarce candidate is the primary comparator for new patient harm.
Protected V4 is a second comparator for whole-case waiting and treatment loss.
A saving in one case cannot hide a longer total wait in another case.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import json
from pathlib import Path

from .joint_policy_experiment import (
    ROOT, SUMMARY_FIELDS, _raw, _sha,
    _write_new, compare, read_case, runtime_versions, summarize,
)
from .deadline_recovery_engine import DEFAULT_DEADLINE_RECOVERY_POLICIES as POLICIES, run_deadline_recovery_replay
from .deadline_recovery_scenarios import (
    FAMILIES, ORIGINAL_FAMILIES, ADDITIONAL_FAMILIES,
    DEVELOPMENT_SEEDS, VALIDATION_SEEDS, make_scenario,
)


from .scarce_delay_experiment import SOURCE_FILES as PREVIOUS_SOURCES

EXPERIMENT_ID = 'deadline_recovery_20261016'
POLICY_IDS = tuple(p['id'] for p in POLICIES)
NEW_SOURCES = (
    'simulation/deadline_recovery_engine.py', 'simulation/deadline_recovery_scenarios.py',
    'simulation/deadline_recovery_audit.py', 'simulation/deadline_recovery_experiment.py',
    'tests/research/test_deadline_recovery_engine.py', 'tests/research/test_deadline_recovery_scenarios.py',
    'tests/research/test_deadline_recovery_audit.py', 'tests/research/test_deadline_recovery_experiment.py',
)
SOURCE_FILES = tuple(sorted(set(PREVIOUS_SOURCES) | set(NEW_SOURCES)))
# Names are resolved from the explicit configuration instead of relying on IDs.
def policy_id(allocation, *, inquiry='v4', cohort=False, routing=False, late=False):
    rows = [p for p in POLICIES if p['allocation'] == allocation
            and p['inquiry'] == inquiry
            and p['cohort_registration'] == cohort and p['scarce_routing'] == routing
            and p['late_reply_recovery'] == late]
    if len(rows) != 1:
        raise ValueError('policy configuration is missing or ambiguous')
    return rows[0]['id']


PAIRS = tuple(
    (name + '_' + allocation,
     policy_id(allocation, cohort=before[0], routing=before[0], late=before[1]),
     policy_id(allocation, cohort=after[0], routing=after[0], late=after[1]))
    for allocation in ('H1', 'C1')
    for name, before, after in (
        ('previous_candidate', (False, False), (True, False)),
        ('late_recovery', (True, False), (True, True)),
        ('combined', (False, False), (True, True)),
    )) + tuple(
    ('fixed3_reference_' + allocation, policy_id(allocation, inquiry='fixed_3'),
     policy_id(allocation, cohort=True, routing=True, late=True))
    for allocation in ('H1', 'C1'))

SELECTION_RULE = dict(
    population='development_only', baseline='protected_v4_serial_registration',
    candidates=['bounded_late_reply'], additional_comparator='previous_scarce_candidate',
    maximum_lost_treatments=0, maximum_individual_treatment_worsening_minutes=0,
    maximum_individual_final_arrival_worsening_minutes=0,
    maximum_case_total_wait_increase_minutes=0,
    maximum_case_capped_treatment_increase_minutes=0,
    maximum_total_inquiry_ratio=1.10,
    require_strict_total_wait_reduction=True,
    require_no_patient_harm_vs_previous_candidate=True,
    individual_nonworsening_scope='new recovery versus previous scarce candidate',
    protected_reference_scope='no lost treatment, no case waiting/capped treatment increase; disclose individual tradeoffs',
    fallback='protected_v4_serial_registration',
    tie_break='less_policy_change',
)
SETTINGS = dict(
    time_unit='actual_minutes', review_resolution_seconds=15,
    observations='same initial/feed/confirmation contract; no added resource report channel',
    reply_contract='original inquiry timeout retained; actually received late hospital reply separately revalidated',
    late_reply_scope='initial scarce supplement only, at most 15 seconds after original timeout, SEARCH only',
    late_reply_waiting='nonblocking; original retry and other inquiry work are not postponed',
    late_reply_safety='same patient conditions, no newer same-hospital inquiry, fresh sufficient data and actual hospital assessment',
    physical_motion='unchanged routes, travel durations, beds and treatment durations',
    priority='unchanged supplied patient priority; no diagnosis or automatic clinical triage',
    recovery=True, pending_protection=True, pending_grace_minutes=1,
    inquiry_scope='server pure selector in shared simulation loop; not full server workflow',
    global_server_deadline_applied=False,
    cohort_scope='only actual requests registered at the current time before first inquiry',
    routing_scope='keep original selector inquiry; at most one parallel fresh alternative at initial request',
    routing_basis='current observations and actual registered same/higher-priority demand only',
    routing_flag_meaning='legacy scarce_routing flag denotes parallel supplement, not candidate removal',
    policies=deepcopy(list(POLICIES)), selection_rule=deepcopy(SELECTION_RULE),
    target_regression_seeds=list(range(32101, 32107)) + list(range(34101, 34107)),
    source_control='historical source and artifacts immutable; new modules only',
)


def source_hashes():
    return {name: _sha((ROOT / name).read_bytes()) for name in SOURCE_FILES}


def protocol():
    return dict(experiment_id=EXPERIMENT_ID, families=list(FAMILIES),
        original_families=list(ORIGINAL_FAMILIES), additional_families=list(ADDITIONAL_FAMILIES),
        settings=deepcopy(SETTINGS), development_seeds=list(DEVELOPMENT_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS), source_sha256=source_hashes(),
        runtime_versions=runtime_versions())


def totals(cases):
    return {code: {field: sum(c['summaries'][code][field] for c in cases)
                   for field in SUMMARY_FIELDS} for code in POLICY_IDS}


def combined_pairs(cases):
    fields = ('patients', 'improved', 'worsened', 'same', 'newly_treated', 'lost_treatments',
        'total_wait_delta_minutes', 'capped_treatment_delta_minutes', 'inquiry_delta',
        'selection_improved', 'selection_worsened', 'final_arrival_improved', 'final_arrival_worsened')
    maxima = ('maximum_treatment_worsening_minutes', 'maximum_selection_worsening_minutes',
              'maximum_final_arrival_worsening_minutes')
    return {name: dict(before=before, after=after,
        **{field: sum(c['comparisons'][name][field] for c in cases) for field in fields},
        **{field: max((c['comparisons'][name][field] for c in cases), default=0) for field in maxima},
        cases_with_longer_total_wait=sum(c['comparisons'][name]['total_wait_delta_minutes'] > 0 for c in cases),
        cases_with_longer_capped_treatment=sum(c['comparisons'][name]['capped_treatment_delta_minutes'] > 0 for c in cases),
        cases_with_more_inquiries=sum(c['comparisons'][name]['inquiry_delta'] > 0 for c in cases))
        for name, before, after in PAIRS}


def candidate_checks(cases, allocation, *, against_previous=False):
    before = policy_id(allocation, cohort=against_previous, routing=against_previous)
    after = policy_id(allocation, cohort=True, routing=True, late=True)
    comparisons = [compare(c['summaries'][before], c['summaries'][after]) for c in cases]
    return dict(
        no_lost_treatments=all(c['lost_treatments'] == 0 for c in comparisons),
        no_individual_treatment_worsening=all(c['worsened'] == 0 for c in comparisons),
        no_individual_final_arrival_worsening=all(c['final_arrival_worsened'] == 0 for c in comparisons),
        no_case_total_wait_increase=all(c['total_wait_delta_minutes'] <= 0 for c in comparisons),
        no_case_capped_treatment_increase=all(c['capped_treatment_delta_minutes'] <= 0 for c in comparisons),
        bounded_inquiry_cost=sum(c['summaries'][after]['inquiries'] for c in cases) <=
            sum(c['summaries'][before]['inquiries'] for c in cases) * SELECTION_RULE['maximum_total_inquiry_ratio'],
        strict_total_wait_reduction=sum(c['total_wait_delta_minutes'] for c in comparisons) < 0,
    )


def select_policy(cases):
    selected = {}
    for allocation in ('H1', 'C1'):
        checks = dict(vs_protected=candidate_checks(cases, allocation),
                      vs_previous_candidate=candidate_checks(cases, allocation, against_previous=True))
        protected_gate = ('no_lost_treatments', 'no_case_total_wait_increase',
                          'no_case_capped_treatment_increase', 'bounded_inquiry_cost',
                          'strict_total_wait_reduction')
        gates = dict(vs_previous_candidate=all(checks['vs_previous_candidate'].values()),
                     vs_protected_population=all(checks['vs_protected'][key] for key in protected_gate))
        chosen = all(gates.values())
        name = 'bounded_late_reply' if chosen else 'protected_v4_serial_registration'
        pid = policy_id(allocation, cohort=chosen, routing=chosen, late=chosen)
        selected[allocation] = dict(policy=pid, candidate=name, checks=checks, gates=gates)
    return selected


def family_summaries(cases):
    return [dict(family=f, cases=len(group), totals=totals(group), comparisons=combined_pairs(group))
            for f in FAMILIES if (group := [c for c in cases if c['family'] == f])]


def verify_lock(lock):
    if lock.get('protocol') != protocol():
        raise ValueError('locked sources, settings, runtime or seeds changed')
    data = Path(lock['development_report']).read_bytes()
    if _sha(data) != lock['development_report_sha256']:
        raise ValueError('development report changed')
    report = json.loads(data)
    if select_policy(report['cases']) != lock['selected_policy']:
        raise ValueError('selection differs from frozen development gate')
    for index in report['cases']:
        replay = read_case(index, report['artifact_directory'])
        if _sha(_raw(replay['scenario'])) != index['original_scenario_sha256']:
            raise ValueError('scenario artifact differs from original input')
        if {b['id']: summarize(replay['scenario'], b) for b in replay['branches']} != index['summaries']:
            raise ValueError('summary differs from hashed raw event evidence')


def markdown(report):
    lines = ['# 늦게 도착한 병원 회신 재검증', '',
        f"합성 {report['phase']} · {len(report['cases'])}환경 · {len(report['cases']) * len(POLICIES)}실행", '',
        '기존 보호 V4와 이전 보충 문의 후보를 각각 기준으로 늦은 회신 재검증을 비교한다.',
        '원 기한은 유지하며, 실제 도착한 회신을 현재 조건으로 재검증한다. 회신을 추가로 기다리지 않는다.', '',
        '| 분기 | 환자 | 미처치 | 전체 대기 합(환자·분) | 요청→처치 제한 합(분) | 문의 |',
        '|---|---:|---:|---:|---:|---:|']
    for code, row in report['totals'].items():
        lines.append(f"| {code} | {row['patients']} | {row['untreated']} | {row['total_wait_sum_minutes']:.2f} | {row['request_to_treatment_capped_sum_minutes']:.2f} | {row['inquiries']} |")
    lines += ['', '| 전후 비교 | 처치 단축 | 동일 | 악화 | 최대 악화(분) | 대기 증가 환경 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for name, row in report['comparisons'].items():
        lines.append(f"| {name} | {row['improved']} | {row['same']} | {row['worsened']} | {row['maximum_treatment_worsening_minutes']:.2f} | {row['cases_with_longer_total_wait']} |")
    lines += ['', '개발에서 고정한 선택:', '', '```json',
        json.dumps(report['selected_policy'], ensure_ascii=False, indent=2), '```', '',
        'validation_checks는 검증의 진단 결과다. 실제 선택은 개발 잠금값이며 검증으로 재선택하지 않는다.',
        '환자별 지연과 환경별 대기 증가를 모두 공개한다.',
        '대기는 이동을 제외한 탐색·병원 대기·전원 준비 합이다. 요청→처치는 미처치를 동일 관찰 종료로 제한한다.',
        '이전 보충 후보의 환자 간 절충과 새 회신 회복으로 추가된 변화를 분리해 공개한다.', '']
    return '\n'.join(lines)


def execute(phase, output, lock_path):
    from .deadline_recovery_audit import audit_branch
    if phase not in ('development', 'validation'):
        raise ValueError('unknown experiment phase')
    output, lock_path = Path(output), Path(lock_path)
    report_path = output.with_suffix('.json')
    artifact_dir = output.parent / (output.name + '_cases')
    if any(p.exists() for p in (report_path, output.with_suffix('.md'), artifact_dir)):
        raise ValueError('choose new output paths; evidence cannot be overwritten')
    if phase == 'development' and lock_path.exists():
        raise ValueError('development lock already exists')
    locked = json.loads(lock_path.read_text(encoding='utf-8')) if phase == 'validation' else None
    if locked is not None:
        verify_lock(locked)
    frozen = protocol()
    seeds = DEVELOPMENT_SEEDS if phase == 'development' else VALIDATION_SEEDS
    artifact_dir.mkdir(parents=True)
    _write_new(artifact_dir / 'protocol.json', _raw(frozen))
    cases = []
    for si, seed in enumerate(seeds):
        for fi, family in enumerate(FAMILIES):
            scenario = make_scenario(seed, family)
            offset = (si + fi) % len(POLICIES)
            order = POLICY_IDS[offset:] + POLICY_IDS[:offset]
            replay = run_deadline_recovery_replay(scenario, policies=deepcopy(POLICIES), execution_order=order)
            summaries, audits = {}, {}
            for branch in replay['branches']:
                code = branch['id']
                audits[code] = audit_branch(replay['scenario'], branch)
                if not audits[code]['passed']:
                    raise ValueError('raw branch audit failed')
                summaries[code] = summarize(replay['scenario'], branch)
                if branch.get('resource_updates_enabled') is not False or any(summaries[code][field]
                    for field in ('added_resource_publications', 'added_resource_update_receipts', 'added_resource_update_cycles')):
                    raise ValueError('extra resource reporting is forbidden')
            raw = _raw(replay)
            compressed = gzip.compress(raw, compresslevel=6, mtime=0)
            name = f'{seed}_{fi:02d}_{family}.json.gz'
            _write_new(artifact_dir / name, compressed)
            cases.append(dict(seed=seed, family=family, file=name,
                compressed_sha256=_sha(compressed), raw_sha256=_sha(raw),
                original_scenario_sha256=_sha(_raw(scenario)), execution_order=list(order),
                summaries=summaries, audits=audits,
                comparisons={name: compare(summaries[before], summaries[after]) for name, before, after in PAIRS}))
            print(f'{phase} {len(cases)}/{len(seeds) * len(FAMILIES)} {seed} {family}', flush=True)
    if protocol() != frozen:
        raise ValueError('sources or settings changed during execution')
    selected = select_policy(cases) if locked is None else locked['selected_policy']
    report = dict(schema_version=1, experiment_id=EXPERIMENT_ID, phase=phase, synthetic=True,
        protocol=frozen, artifact_directory=str(artifact_dir.resolve()), cases=cases,
        totals=totals(cases), comparisons=combined_pairs(cases), selected_policy=selected,
        validation_checks=select_policy(cases) if phase == 'validation' else None,
        families=family_summaries(cases))
    report['contributions'] = {name: dict(totals=totals(group), comparisons=combined_pairs(group))
        for name, group in (
            ('inherited_67', [c for c in cases if c['family'] in ORIGINAL_FAMILIES]),
            ('new_stress_5', [c for c in cases if c['family'] in ADDITIONAL_FAMILIES]))}
    raw = _raw(report)
    _write_new(report_path, raw)
    _write_new(output.with_suffix('.md'), markdown(report).encode('utf-8'))
    if locked is None:
        _write_new(lock_path, _raw(dict(protocol=frozen, development_report=str(report_path.resolve()),
            development_report_sha256=_sha(raw), selected_policy=selected)))
    else:
        verify_lock(locked)
    print(json.dumps(dict(output=str(report_path), cases=len(cases),
        runs=len(cases) * len(POLICIES), selected=selected), ensure_ascii=False), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('development', 'validation'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lock', type=Path, default=ROOT / 'config/deadline_recovery_lock_20261016.json')
    args = parser.parse_args(argv)
    execute(args.phase, args.output, args.lock)


if __name__ == '__main__':
    main()
