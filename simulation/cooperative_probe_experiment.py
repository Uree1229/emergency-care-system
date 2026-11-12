"""Paired cooperative optional work with explicit hospital control acknowledgements.

This is a new workflow contract, not an unchanged-information policy improvement.
Clinical acceptance, original deadlines, resources and physical gates remain
mandatory. No patient/case regression may be hidden by a population saving.
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
from .cooperative_probe_engine import (
    DEFAULT_COOPERATIVE_PROBE_POLICIES as POLICIES, COOPERATIVE_PROBE_SPEC, run_cooperative_probe_replay,
)
from .cooperative_probe_scenarios import (
    FAMILIES, ORIGINAL_FAMILIES, ADDITIONAL_FAMILIES,
    DEVELOPMENT_SEEDS, VALIDATION_SEEDS, make_scenario,
)


from .shortage_probe_experiment import SOURCE_FILES as PREVIOUS_SOURCES

EXPERIMENT_ID = 'cooperative_probe_20261017'
POLICY_IDS = tuple(p['id'] for p in POLICIES)
NEW_SOURCES = (
    'simulation/cooperative_probe_engine.py', 'simulation/cooperative_probe_scenarios.py',
    'simulation/cooperative_probe_audit.py', 'simulation/cooperative_probe_experiment.py',
    'tests/research/test_cooperative_probe_engine.py', 'tests/research/test_cooperative_probe_scenarios.py',
    'tests/research/test_cooperative_probe_audit.py', 'tests/research/test_cooperative_probe_experiment.py',
)
SOURCE_FILES = tuple(sorted(set(PREVIOUS_SOURCES) | set(NEW_SOURCES)))
def policy_id(allocation, stage='baseline', *, inquiry='v4'):
    code=f'fixed3_{allocation}_baseline' if inquiry=='fixed_3' else f'v4_{allocation}_{stage}'
    if code not in POLICY_IDS:raise ValueError('unknown explicit experimental branch')
    return code


PAIRS=tuple((name+'_'+allocation,policy_id(allocation,before),policy_id(allocation,after))
    for allocation in ('H1','C1') for name,before,after in (
        ('cooperative','safe','cooperative'),('workflow','probe','cooperative'),
        ('combined','baseline','cooperative'),('previous_probe','safe','probe'),
        ('previous_safe','baseline','safe'))) + tuple(
    ('fixed3_reference_'+a,policy_id(a,inquiry='fixed_3'),policy_id(a,'cooperative'))
    for a in ('H1','C1'))

SELECTION_RULE=dict(population='development_only',baseline='protected_v4_serial_registration',
    candidates=['cooperative_probe_workflow'],
    additional_comparators=['previous_safe_candidate','previous_probe_candidate'],
    maximum_lost_treatments=0,maximum_individual_treatment_worsening_minutes=0,
    maximum_individual_final_arrival_worsening_minutes=0,
    maximum_case_total_wait_increase_minutes=0,maximum_case_capped_treatment_increase_minutes=0,
    maximum_total_inquiry_ratio=1.10,require_strict_total_wait_reduction=True,
    individual_nonworsening_scope='cooperative versus previous safe candidate',
    previous_probe_scope='no lost treatment or case wait/capped increase; disclose individual tradeoffs',
    protected_reference_scope='no lost treatment or case wait/capped increase; disclose individual tradeoffs',
    fallback='protected_v4_serial_registration',tie_break='less_policy_change')
SETTINGS=dict(time_unit='actual_minutes',review_resolution_seconds=15,
    observations='original resource publications unchanged; new actual current job-control ACK channel',
    inquiry_contract='current-idle admission for optional probes; busy admission rejection is not a clinical response',
    worker_contract='same worker count and original service demand per accepted review; all added work logged; normal FIFO preempts and resumes optional work',
    readiness_contract='optional work can be ignored only after actual completed-work ACK; running/paused work blocks early optional readiness',
    probe_scope='inherited bounded shortage probe selector unchanged',
    probe_budget='once per patient, logical inquiry contact and concurrency budgets retained',
    normal_eligibility='nonadmitted optional probe does not consume ordinary-hospital eligibility',
    admission_retry='at most one actual ordinary inquiry after a nonadmission ACK and a later fresh sufficient resource report; actual contact times and budgets are retained',
    attempt_identity='each actual contact including nonadmission consumes its original contact attempt identity',
    clinical_work_identity='separate accepted-review counter; nonadmission has no clinical work or clinical response sample',
    contact_provenance='actual latest contact time and episode counters include busy admission contacts',
    workflow_availability='only enabled inquiry queues support cooperative control; unsupported queues retain safe handling without optional probes',
    physical_contract='actual clinical response, original deadlines/TTL, fresh allocation and physical gates unchanged',
    hidden_future='no future response due, remaining service, release, outage or future requests in policy input',
    priority='unchanged supplied patient priority; no automatic destination change',
    switch_overhead_minutes=0,
    integration_status='isolated simulation workflow extension; hospital admission/status/preemption support required',
    global_server_deadline_applied=False,
    policies=deepcopy(list(POLICIES)),workflow_spec=deepcopy(COOPERATIVE_PROBE_SPEC),
    selection_rule=deepcopy(SELECTION_RULE),
    target_regression_seeds=list(range(40101,40107)),
    target_regression_families=['shortage_probe_late_reply','shortage_probe_later_patient_fifo'],
    source_control='all preceding source and evidence preserved; new modules only')


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


def candidate_checks(cases, allocation, *, comparator='safe'):
    before,after=policy_id(allocation,comparator),policy_id(allocation,'cooperative')
    comparisons=[compare(c['summaries'][before],c['summaries'][after]) for c in cases]
    return dict(
        no_lost_treatments=all(c['lost_treatments']==0 for c in comparisons),
        no_individual_treatment_worsening=all(c['worsened']==0 for c in comparisons),
        no_individual_final_arrival_worsening=all(c['final_arrival_worsened']==0 for c in comparisons),
        no_case_total_wait_increase=all(c['total_wait_delta_minutes']<=0 for c in comparisons),
        no_case_capped_treatment_increase=all(c['capped_treatment_delta_minutes']<=0 for c in comparisons),
        bounded_inquiry_cost=sum(c['summaries'][after]['inquiries'] for c in cases)<=
            sum(c['summaries'][before]['inquiries'] for c in cases)*SELECTION_RULE['maximum_total_inquiry_ratio'],
        strict_total_wait_reduction=sum(c['total_wait_delta_minutes'] for c in comparisons)<0)


def select_policy(cases):
    selected={}
    for allocation in ('H1','C1'):
        checks=dict(vs_previous_safe=candidate_checks(cases,allocation),
                    vs_previous_probe=candidate_checks(cases,allocation,comparator='probe'),
                    vs_protected=candidate_checks(cases,allocation,comparator='baseline'))
        population=('no_lost_treatments','no_case_total_wait_increase',
                   'no_case_capped_treatment_increase','bounded_inquiry_cost','strict_total_wait_reduction')
        gates=dict(vs_previous_safe=all(checks['vs_previous_safe'].values()),
                   vs_previous_probe_population=all(checks['vs_previous_probe'][key] for key in population),
                   vs_protected_population=all(checks['vs_protected'][key] for key in population))
        chosen=all(gates.values())
        selected[allocation]=dict(policy=policy_id(allocation,'cooperative' if chosen else 'baseline'),
            candidate='cooperative_probe_workflow' if chosen else 'protected_v4_serial_registration',
            checks=checks,gates=gates)
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
    lines = ['# 일반 문의를 보호하는 선택적 재확인 처리', '',
        f"합성 {report['phase']} · {len(report['cases'])}환경 · {len(report['cases']) * len(POLICIES)}실행", '',
        '직전 안전 후보·재확인 후보·기존 보호 V4를 각각 기준으로 새 접수·작업 처리 규칙을 비교한다.',
        '병원이 바쁘면 선택적 재확인을 비접수 처리하고, 새 일반 문의가 오면 재확인 작업을 중단·재개한다.', '',
        '실제 수용 회신·신선하고 충분한 자원·기존 실제 자원 게이트를 모두 통과해야 배정한다.', '',
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
        '현재 작업 상태 ACK와 선점 처리는 새 병원 업무 계약이다. 실제 병원 지원이 없으면 이 효과를 주장할 수 없다.', '']
    return '\n'.join(lines)


def execute(phase, output, lock_path):
    from .cooperative_probe_audit import audit_branch
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
            replay = run_cooperative_probe_replay(scenario, policies=deepcopy(POLICIES), execution_order=order)
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
            ('inherited_83', [c for c in cases if c['family'] in ORIGINAL_FAMILIES]),
            ('new_workflow_boundaries', [c for c in cases if c['family'] in ADDITIONAL_FAMILIES]))}
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
    parser.add_argument('--lock', type=Path, default=ROOT / 'config/cooperative_probe_lock_20261017.json')
    args = parser.parse_args(argv)
    execute(args.phase, args.output, args.lock)


if __name__ == '__main__':
    main()
