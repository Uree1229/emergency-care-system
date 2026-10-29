"""Compare inquiry selectors and H1/C1 on the same quarter-minute environment.

Raw cases are separately compressed and hashed so the experiment never has to
keep thousands of event streams in memory. Historical artifacts are untouched.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import platform

from server import inquiry_policies, inquiry_v4
from .joint_policy_engine import run_joint_policy_replay
from .joint_policy_scenarios import FAMILIES, ADDITIONAL_FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, make_scenario
from .outage_recovery_experiment import SOURCE_FILES as PREVIOUS_SOURCES
from .total_wait_metrics import waiting_metrics


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = 'joint_policy_20261016'
_BASE_POLICIES = tuple(
    dict(id=f'fixed3_{allocation}_no_backup', label=f'고정3 · {allocation} · 백업 미보존',
         inquiry='fixed_3', allocation=allocation, recovery=False)
    for allocation in ('H1', 'C1')) + tuple(
    dict(id=f'{short}_{allocation}', label=f'{label} · {allocation} · 공통 회복',
         inquiry=inquiry, allocation=allocation, recovery=True)
    for short, label, inquiry in (('fixed3', '고정3', 'fixed_3'), ('v3', 'V3', 'v3'), ('v4', 'V4 후보', 'v4'))
    for allocation in ('H1', 'C1'))
POLICIES = tuple(dict(p, pending_protection=True) for p in _BASE_POLICIES) + tuple(
    dict(id=f'v3_{allocation}_no_pending_guard', label=f'V3 · {allocation} · 진행 문의 기회 유보 없음',
         inquiry='v3', allocation=allocation, recovery=True, pending_protection=False)
    for allocation in ('H1', 'C1'))
POLICY_IDS = tuple(p['id'] for p in POLICIES)
NEW_SOURCES = (
    'server/inquiry_policies.py', 'server/inquiry_v3.py', 'server/inquiry_v4.py',
    'config/inquiry_v3_selected.json', 'simulation/joint_policy_engine.py',
    'simulation/joint_policy_scenarios.py', 'simulation/joint_policy_experiment.py',
    'simulation/joint_policy_audit.py', 'test_inquiry_v4.py',
    'test_joint_policy_engine.py', 'test_joint_policy_scenarios.py',
    'test_joint_policy_experiment.py', 'test_joint_policy_audit.py')
SOURCE_FILES = tuple(sorted(set(PREVIOUS_SOURCES) | set(NEW_SOURCES)))
PAIRS = (
    ('recovery_H1', 'fixed3_H1_no_backup', 'fixed3_H1'),
    ('recovery_C1', 'fixed3_C1_no_backup', 'fixed3_C1'),
    ('v3_vs_fixed_H1', 'fixed3_H1', 'v3_H1'),
    ('v3_vs_fixed_C1', 'fixed3_C1', 'v3_C1'),
    ('v4_vs_v3_H1', 'v3_H1', 'v4_H1'),
    ('v4_vs_v3_C1', 'v3_C1', 'v4_C1'),
    ('H1_vs_C1_fixed', 'fixed3_C1', 'fixed3_H1'),
    ('H1_vs_C1_v3', 'v3_C1', 'v3_H1'),
    ('H1_vs_C1_v4', 'v4_C1', 'v4_H1'),
    ('pending_guard_H1', 'v3_H1_no_pending_guard', 'v3_H1'),
    ('pending_guard_C1', 'v3_C1_no_pending_guard', 'v3_C1'))
SELECTION_RULE = dict(
    population='development_only', candidate='v4', baseline='v3',
    separate_selection_per_allocation=True, maximum_lost_treatments=0,
    maximum_individual_treatment_worsening_minutes=0,
    maximum_inquiry_ratio=1.10, require_strict_capped_treatment_improvement=True,
    fallback='v3', tie_break='keep_v3')
SETTINGS = dict(
    time_unit='actual_minutes', review_resolution_seconds=15,
    physical_motion='unchanged whole-minute route rounding; no route speedup',
    observations='unchanged initial/feed/confirmation; no added resource-change channel',
    transport='same accepted-offer wait, resource checks, opportunity protection and final gate',
    inquiry_scope='actual server pure selectors in common transport loop; not the entire server workflow',
    global_server_deadline_applied=False,
    pending_opportunity_grace_minutes=1,
    v3=inquiry_policies.spec('observed_v3'), v4=inquiry_v4.spec(),
    policies=deepcopy(list(POLICIES)), selection_rule=SELECTION_RULE,
    source_control='new files only; frozen historical source and report hashes preserved')
SUMMARY_FIELDS = (
    'patients', 'untreated', 'total_wait_sum_minutes',
    'request_to_treatment_capped_sum_minutes', 'inquiries',
    'resource_confirmation_requests', 'added_resource_publications',
    'selection_capped_sum_minutes', 'final_arrival_capped_sum_minutes', 'lost_treatment_denominator',
    'queue_wait_sum_minutes','inquiry_service_minutes')


def _raw(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_new(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def source_hashes():
    return {name: _sha((ROOT / name).read_bytes()) for name in SOURCE_FILES}


def runtime_versions():
    import ortools
    return dict(python=platform.python_version(), ortools=ortools.__version__)


def protocol():
    return dict(experiment_id=EXPERIMENT_ID, families=list(FAMILIES), settings=deepcopy(SETTINGS),
                development_seeds=list(DEVELOPMENT_SEEDS), validation_seeds=list(VALIDATION_SEEDS),
                source_sha256=source_hashes(), runtime_versions=runtime_versions())


def summarize(scenario, branch):
    waiting = waiting_metrics(scenario, branch)
    end = scenario.get('max_minutes', 180)
    outcomes = {p['id']: p for p in branch['patient_outcomes']}
    rows = []
    for spec in sorted(scenario['patients'], key=lambda p: p['id']):
        p = outcomes[spec['id']]
        start = spec['search_start_t']
        rows.append(dict(patient=p['id'], priority=p['priority'],
            treated=p['treatment_start_t'] is not None,
            treatment_capped_minutes=(p['treatment_start_t'] if p['treatment_start_t'] is not None else end) - start,
            selection_capped_minutes=(p['first_commit_t'] if p['first_commit_t'] is not None else end) - start,
            final_arrival_capped_minutes=(p['final_arrive_t'] if p['final_arrive_t'] is not None else end) - start,
            final_arrival_t=p['final_arrive_t'], treatment_start_t=p['treatment_start_t'],
            first_commit_t=p['first_commit_t'], inquiries=p['inquiries']))
    metrics = branch['metrics']
    return dict(**{k: v for k, v in waiting.items() if k != 'patient_details'},
        inquiries=metrics['inquiries'],
        resource_confirmation_requests=metrics['resource_confirmation_requests'],
        added_resource_publications=sum(e['type'] == 'resource_update_captured' for e in branch['events']),
        added_resource_update_receipts=sum(e['type'] == 'resource_report_received' and 'publication_id' in e for e in branch['events']),
        added_resource_update_cycles=sum(e['type'] == 'resource_update_cycle' for e in branch['events']),
        selection_capped_sum_minutes=sum(p['selection_capped_minutes'] for p in rows),
        final_arrival_capped_sum_minutes=sum(p['final_arrival_capped_minutes'] for p in rows),
        lost_treatment_denominator=len(rows), patient_rows=rows,
        heuristic_calls=metrics.get('heuristic_calls', 0), solver_calls=metrics.get('solver_calls', 0),
        allocation_wall_seconds=metrics.get('allocation_wall_seconds', 0),
        backup_episodes=metrics.get('backup_retained_episodes', 0),
        inquiry_service_minutes=metrics.get('inquiry_service_minutes',0),
        inquiry_peak_waiting=metrics.get('inquiry_peak_waiting',0),
        inquiry_work_by_hospital=deepcopy((branch.get('inquiry_queue_report') or {}).get('by_hospital',[])))


def compare(before, after):
    a = {p['patient']: p for p in before['patient_rows']}
    b = {p['patient']: p for p in after['patient_rows']}
    if set(a) != set(b):
        raise ValueError('paired comparison needs the same patients')
    differences = []
    improved = worsened = same = gained = lost = 0
    largest = 0
    selection_improved=selection_worsened=arrival_improved=arrival_worsened=0
    max_selection_worsening=max_arrival_worsening=0
    for pid in sorted(a):
        x, y = a[pid], b[pid]
        delta = y['treatment_capped_minutes'] - x['treatment_capped_minutes']
        improved += delta < 0
        worsened += delta > 0
        same += delta == 0
        gained += not x['treated'] and y['treated']
        lost += x['treated'] and not y['treated']
        largest = max(largest, delta)
        selection = y['selection_capped_minutes'] - x['selection_capped_minutes']
        arrival=y['final_arrival_capped_minutes']-x['final_arrival_capped_minutes']
        selection_improved+=selection<0;selection_worsened+=selection>0
        arrival_improved+=arrival<0;arrival_worsened+=arrival>0
        max_selection_worsening=max(max_selection_worsening,selection)
        max_arrival_worsening=max(max_arrival_worsening,arrival)
        if delta or selection or arrival or x['treated'] != y['treated']:
            differences.append(dict(patient=pid, treatment_delta_minutes=delta,
                selection_delta_minutes=selection, final_arrival_delta_minutes=arrival,
                before_treated=x['treated'], after_treated=y['treated']))
    result = dict(patients=len(a), improved=improved, worsened=worsened, same=same,
        newly_treated=gained, lost_treatments=lost, maximum_treatment_worsening_minutes=largest,
        total_wait_delta_minutes=after['total_wait_sum_minutes']-before['total_wait_sum_minutes'],
        capped_treatment_delta_minutes=after['request_to_treatment_capped_sum_minutes']-before['request_to_treatment_capped_sum_minutes'],
        inquiry_delta=after['inquiries']-before['inquiries'], differences=differences)
    result.update(selection_improved=selection_improved,selection_worsened=selection_worsened,
        final_arrival_improved=arrival_improved,final_arrival_worsened=arrival_worsened,
        maximum_selection_worsening_minutes=max_selection_worsening,
        maximum_final_arrival_worsening_minutes=max_arrival_worsening)
    return result


def totals(cases):
    result = {}
    for code in POLICY_IDS:
        rows = [case['summaries'][code] for case in cases]
        result[code] = {field: sum(row[field] for row in rows) for field in SUMMARY_FIELDS}
    return result


def combined_pairs(cases):
    result = {}
    for name, before, after in PAIRS:
        comparisons = [case['comparisons'][name] for case in cases]
        fields = ('patients', 'improved', 'worsened', 'same', 'newly_treated', 'lost_treatments',
                  'total_wait_delta_minutes', 'capped_treatment_delta_minutes', 'inquiry_delta',
                  'selection_improved','selection_worsened','final_arrival_improved','final_arrival_worsened')
        result[name] = dict(before=before, after=after,
            **{field: sum(row[field] for row in comparisons) for field in fields},
            **{field:max((row[field] for row in comparisons),default=0) for field in (
                'maximum_treatment_worsening_minutes','maximum_selection_worsening_minutes',
                'maximum_final_arrival_worsening_minutes')})
    return result


def select_inquiry(cases):
    summed, paired = totals(cases), combined_pairs(cases)
    chosen = {}
    for allocation in ('H1', 'C1'):
        a, b = summed['v3_' + allocation], summed['v4_' + allocation]
        delta = paired['v4_vs_v3_' + allocation]
        tests = dict(no_lost_treatments=delta['lost_treatments'] == 0,
            no_individual_treatment_worsening=delta['worsened'] == 0,
            bounded_inquiry_cost=b['inquiries'] <= a['inquiries'] * SELECTION_RULE['maximum_inquiry_ratio'],
            strict_capped_treatment_improvement=b['request_to_treatment_capped_sum_minutes'] < a['request_to_treatment_capped_sum_minutes'])
        chosen[allocation] = dict(inquiry='v4' if all(tests.values()) else 'v3', checks=tests)
    return chosen


def family_summaries(cases):
    result=[]
    for family in FAMILIES:
        group=[c for c in cases if c['family']==family]
        if not group:continue
        summed=totals(group)
        result.append(dict(family=family,cases=len(group),policies={
            code:dict(row,untreated_fraction=row['untreated']/row['patients'],
                capped_treatment_mean_minutes=row['request_to_treatment_capped_sum_minutes']/row['patients'],
                inquiries_per_patient=row['inquiries']/row['patients']) for code,row in summed.items()},
            comparisons=combined_pairs(group)))
    return result


def family_equal_summary(families):
    fields=('untreated_fraction','capped_treatment_mean_minutes','inquiries_per_patient')
    return {code:{field:sum(f['policies'][code][field] for f in families)/len(families)
            for field in fields} for code in POLICY_IDS}


def read_case(index, artifact_dir):
    base = Path(artifact_dir).resolve()
    path = (base / index['file']).resolve()
    if not path.is_relative_to(base):
        raise ValueError('case artifact path escapes experiment directory')
    compressed = path.read_bytes()
    if _sha(compressed) != index['compressed_sha256']:
        raise ValueError('compressed case artifact changed')
    raw = gzip.decompress(compressed)
    if _sha(raw) != index['raw_sha256']:
        raise ValueError('raw case artifact changed')
    return json.loads(raw)


def verify_lock(lock):
    if lock.get('protocol') != protocol():
        raise ValueError('source, settings, runtime or seed differs from locked development')
    path = Path(lock['development_report'])
    raw = path.read_bytes()
    if _sha(raw) != lock['development_report_sha256']:
        raise ValueError('development report changed')
    report = json.loads(raw)
    if select_inquiry(report['cases']) != lock['selected_inquiry']:
        raise ValueError('locked inquiry selection differs from development results')
    for index in report['cases']:
        replay = read_case(index, report['artifact_directory'])
        if _sha(_raw(replay['scenario'])) != index['original_scenario_sha256']:
            raise ValueError('raw case scenario differs from original input')
        summaries = {b['id']:summarize(replay['scenario'],b) for b in replay['branches']}
        if summaries != index['summaries']:
            raise ValueError('compact development summary differs from hashed raw record')


def markdown(report):
    lines = ['# 문의 정책 × 공동 배정 공통 회복 비교', '',
        f"합성 {report['phase']} · {len(report['cases'])}사례 · {len(report['cases'])*len(POLICIES)}분기 · 15초 재평가", '',
        '서버의 순수 문의 선택 계산을 공통 이송 루프에 연결했다. 서버 전체 워크플로·실제 의료 성과 검증이 아니다.', '',
        '| 분기 | 환자 실행 | 미처치 | 전체 대기 합(환자·분) | 요청→처치 제한 합(분) | 문의 | 수량 확인 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for code, row in report['totals'].items():
        lines.append(f"| {code} | {row['patients']} | {row['untreated']} | {row['total_wait_sum_minutes']:.2f} | {row['request_to_treatment_capped_sum_minutes']:.2f} | {row['inquiries']} | {row['resource_confirmation_requests']} |")
    lines += ['', '| 전후 비교 | 처치 단축 | 동일 | 악화 | 최대 악화(분) | 새 처치 | 처치 상실 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for name, row in report['comparisons'].items():
        lines.append(f"| {name} | {row['improved']} | {row['same']} | {row['worsened']} | {row['maximum_treatment_worsening_minutes']:.2f} | {row['newly_treated']} | {row['lost_treatments']} |")
    lines += ['', '개발에서 고정한 문의 선택:', '', '```json',
        json.dumps(report['selected_inquiry'], ensure_ascii=False, indent=2), '```', '',
        '원시 기록은 동명 디렉터리의 사례별 gzip JSON이다. 압축 파일과 복원 내용의 SHA-256을 모두 기록한다.',
        '전체 대기는 탐색·병원 대기·전원 준비를 포함하고 이동은 제외한다. 요청→처치는 미처치를 같은 관찰 종료로 제한한다.',
        '개인별 악화·최대 지연·실패를 함께 공개하며, 검증 결과로 설정을 다시 고르지 않는다.', '']
    return '\n'.join(lines)


def execute(phase, output, lock_path):
    from .joint_policy_audit import audit_branch
    if phase not in ('development', 'validation'):
        raise ValueError('unknown phase')
    output, lock_path = Path(output), Path(lock_path)
    report_path = output.with_suffix('.json')
    artifact_dir = output.parent / (output.name + '_cases')
    if report_path.exists() or output.with_suffix('.md').exists() or artifact_dir.exists():
        raise ValueError('choose a new output; artifacts cannot be overwritten')
    if phase == 'development' and lock_path.exists():
        raise ValueError('development lock already exists')
    locked = None
    if phase == 'validation':
        locked = json.loads(lock_path.read_text(encoding='utf-8'))
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
            replay = run_joint_policy_replay(scenario, policies=deepcopy(POLICIES), execution_order=order)
            case_scenario = replay['scenario']
            summaries, audits = {}, {}
            for branch in replay['branches']:
                code = branch['id']
                audits[code] = audit_branch(case_scenario, branch)
                if not audits[code]['passed']:
                    raise ValueError('raw branch audit failed')
                summaries[code] = summarize(case_scenario, branch)
                if branch.get('resource_updates_enabled') is not False or any(
                    summaries[code][field] for field in ('added_resource_publications',
                    'added_resource_update_receipts', 'added_resource_update_cycles')):
                    raise ValueError('added resource reports forbidden')
            raw = _raw(replay)
            compressed = gzip.compress(raw, compresslevel=6, mtime=0)
            name = f'{seed}_{fi:02d}_{family}.json.gz'
            _write_new(artifact_dir / name, compressed)
            case = dict(seed=seed, family=family, file=name,
                compressed_sha256=_sha(compressed), raw_sha256=_sha(raw),
                original_scenario_sha256=_sha(_raw(scenario)), execution_order=list(order),
                summaries=summaries, audits=audits,
                comparisons={name: compare(summaries[before], summaries[after]) for name,before,after in PAIRS})
            cases.append(case)
            print(f'{phase} {len(cases)}/{len(seeds)*len(FAMILIES)} {seed} {family}', flush=True)
    if protocol() != frozen:
        raise ValueError('source or settings changed during execution')
    selected = select_inquiry(cases) if locked is None else locked['selected_inquiry']
    report = dict(schema_version=1, experiment_id=EXPERIMENT_ID, phase=phase, synthetic=True,
        protocol=frozen, artifact_directory=str(artifact_dir.resolve()), cases=cases,
        totals=totals(cases), comparisons=combined_pairs(cases), selected_inquiry=selected,
        validation_checks=select_inquiry(cases) if phase=='validation' else None)
    report['families']=family_summaries(cases)
    report['family_equal_summary']=family_equal_summary(report['families'])
    report['contributions']={group:dict(totals=totals(subset),comparisons=combined_pairs(subset))
        for group,subset in (
            ('inherited_48',[c for c in cases if c['family'] not in ADDITIONAL_FAMILIES]),
            ('new_stress_8',[c for c in cases if c['family'] in ADDITIONAL_FAMILIES]))}
    raw = _raw(report)
    _write_new(report_path, raw)
    _write_new(output.with_suffix('.md'), markdown(report).encode('utf-8'))
    if phase == 'development':
        lock = dict(protocol=frozen, development_report=str(report_path.resolve()),
            development_report_sha256=_sha(raw), selected_inquiry=selected)
        _write_new(lock_path, _raw(lock))
    else:
        verify_lock(locked)
    print(json.dumps(dict(output=str(report_path), cases=len(cases),
        runs=len(cases)*len(POLICIES), selected=selected), ensure_ascii=False), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=('development','validation'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lock', type=Path, default=ROOT/'config/joint_policy_lock_20261016.json')
    args = parser.parse_args(argv)
    execute(args.phase, args.output, args.lock)


if __name__ == '__main__':
    main()
