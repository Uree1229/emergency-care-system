"""Paired bounded rechecks of currently reported bed shortage.

Actual hospital replies and fresh sufficient resource checks remain mandatory.
The preceding safe candidate is the primary comparator; a case-level wait
increase cannot be hidden by a population saving. Protected V4 remains the
additional global adoption gate, with individual tradeoffs disclosed.
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
from .shortage_probe_engine import (
    DEFAULT_SHORTAGE_PROBE_POLICIES as POLICIES, SHORTAGE_PROBE_SPEC, run_shortage_probe_replay,
)
from .shortage_probe_scenarios import (
    FAMILIES, ORIGINAL_FAMILIES, ADDITIONAL_FAMILIES,
    DEVELOPMENT_SEEDS, VALIDATION_SEEDS, make_scenario,
)


from .deadline_safe_experiment import SOURCE_FILES as PREVIOUS_SOURCES

EXPERIMENT_ID = 'shortage_probe_20261017'
POLICY_IDS = tuple(p['id'] for p in POLICIES)
NEW_SOURCES = (
    'simulation/shortage_probe_engine.py', 'simulation/shortage_probe_scenarios.py',
    'simulation/shortage_probe_audit.py', 'simulation/shortage_probe_experiment.py',
    'tests/research/test_shortage_probe_engine.py', 'tests/research/test_shortage_probe_scenarios.py',
    'tests/research/test_shortage_probe_audit.py', 'tests/research/test_shortage_probe_experiment.py',
)
SOURCE_FILES = tuple(sorted(set(PREVIOUS_SOURCES) | set(NEW_SOURCES)))
def policy_id(allocation, stage='baseline', *, inquiry='v4'):
    code=f'fixed3_{allocation}_baseline' if inquiry=='fixed_3' else f'v4_{allocation}_{stage}'
    if code not in POLICY_IDS:raise ValueError('unknown explicit experimental branch')
    return code


PAIRS=tuple((name+'_'+allocation,policy_id(allocation,before),policy_id(allocation,after))
    for allocation in ('H1','C1') for name,before,after in (
        ('probe','safe','probe'),('combined','baseline','probe'),
        ('previous_safe','baseline','safe'))) + tuple(
    ('fixed3_reference_'+a,policy_id(a,inquiry='fixed_3'),policy_id(a,'probe'))
    for a in ('H1','C1'))

SELECTION_RULE=dict(population='development_only',baseline='protected_v4_serial_registration',
    candidates=['bounded_shortage_probe'],additional_comparators=['previous_safe_candidate'],
    maximum_lost_treatments=0,maximum_individual_treatment_worsening_minutes=0,
    maximum_individual_final_arrival_worsening_minutes=0,
    maximum_case_total_wait_increase_minutes=0,maximum_case_capped_treatment_increase_minutes=0,
    maximum_total_inquiry_ratio=1.10,require_strict_total_wait_reduction=True,
    individual_nonworsening_scope='probe versus previous safe candidate',
    protected_reference_scope='no lost treatment or case wait/capped increase; disclose individual tradeoffs',
    fallback='protected_v4_serial_registration',tie_break='less_policy_change')
SETTINGS=dict(time_unit='actual_minutes',review_resolution_seconds=15,
    observations='same existing initial/feed/confirmation; no added resource reports',
    inquiry_contract='one explicit recheck inquiry may bypass reported fresh ed_bed shortage only',
    probe_scope='SEARCH before first commitment, original episode, pending inquiry and no usable offer',
    probe_budget='once per patient, original concurrency 3 and episode contacts 10',
    probe_timing='at least 30 seconds after both first and latest actual inquiry; before joint batch',
    probe_resources='current fresh bed shortage, reported capacity sufficient, all other required resources fresh sufficient',
    probe_conflicts='no other currently registered SEARCH patient pending inquiry or live offer at this hospital',
    probe_distance='at most two minutes longer than shortest current pending inquiry',
    physical_contract='actual hospital response, fresh sufficient allocation check and original physical confirmation gate',
    hidden_future='no response due times, future resource release/outage, or future requests in policy input',
    original_work='submitted FIFO work and original inquiry deadlines retained; later work follows actual actions',
    late_reply_contract='previous safe recovery unchanged, original inquiry timeout retained',
    priority='unchanged supplied priority, no automatic clinical assessment',
    global_server_deadline_applied=False,inquiry_scope='shared simulation pure selector; not full server workflow',
    policies=deepcopy(list(POLICIES)),probe_spec=deepcopy(SHORTAGE_PROBE_SPEC),selection_rule=deepcopy(SELECTION_RULE),
    target_regression_seeds=list(range(32101,32107))+list(range(34101,34107)),
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
    before,after=policy_id(allocation,comparator),policy_id(allocation,'probe')
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
                    vs_protected=candidate_checks(cases,allocation,comparator='baseline'))
        protected=('no_lost_treatments','no_case_total_wait_increase',
                   'no_case_capped_treatment_increase','bounded_inquiry_cost','strict_total_wait_reduction')
        gates=dict(vs_previous_safe=all(checks['vs_previous_safe'].values()),
                   vs_protected_population=all(checks['vs_protected'][key] for key in protected))
        chosen=all(gates.values())
        selected[allocation]=dict(policy=policy_id(allocation,'probe' if chosen else 'baseline'),
            candidate='bounded_shortage_probe' if chosen else 'protected_v4_serial_registration',
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
    lines = ['# 현재 병상 부족 보고의 제한적 수용 재확인', '',
        f"합성 {report['phase']} · {len(report['cases'])}환경 · {len(report['cases']) * len(POLICIES)}실행", '',
        '직전 안전 회신 후보와 기존 보호 V4를 각각 기준으로 제한적 만석 병원 재확인을 비교한다.',
        '현재 병상 부족 보고를 가용으로 바꾸지 않고, 실제 수용 재확인 문의를 최대 한 번 제출한다.', '',
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
        '직전 후보에 대한 새 변화와 기존 보호 기준의 환자 간 절충을 분리해 공개한다.', '']
    return '\n'.join(lines)


def execute(phase, output, lock_path):
    from .shortage_probe_audit import audit_branch
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
            replay = run_shortage_probe_replay(scenario, policies=deepcopy(POLICIES), execution_order=order)
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
            ('inherited_73', [c for c in cases if c['family'] in ORIGINAL_FAMILIES]),
            ('new_probe_boundaries', [c for c in cases if c['family'] in ADDITIONAL_FAMILIES]))}
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
    parser.add_argument('--lock', type=Path, default=ROOT / 'config/shortage_probe_lock_20261017.json')
    args = parser.parse_args(argv)
    execute(args.phase, args.output, args.lock)


if __name__ == '__main__':
    main()
