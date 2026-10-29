"""Compare fresh synthetic H1 allocation and CP-SAT under several search budgets.

Development runs are permitted before the manifest is frozen. Validation must
match a reviewed manifest and use only the reserved validation seeds.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time

from .allocation_benchmark import ObservedWorld, audit_assignment, digest, metrics, paired_common_travel
from .allocation_stress_scenarios import (FAMILIES, SIZES, DEVELOPMENT_SEEDS,
                                         VALIDATION_SEEDS, build_stress_snapshots,
                                         validate_stress_snapshot)
from .cp_sat_assignment import CPSATAssignmentRule
from .sim import NearestArrivalRule, CompetitionAwareRule


ROOT = Path(__file__).resolve().parents[1]
BUDGETS = (.01, .05, .2)
POLICIES = ('R1', 'R2') + tuple(f'{algorithm}@{budget:g}' for budget in BUDGETS for algorithm in ('H1', 'C1'))
SETTINGS = dict(at=10, r2_lambda=15., h1_max_moves=2048, budgets_seconds=list(BUDGETS),
                cp_sat_seed=0, cp_sat_workers=1, priority_weights=dict(high=4, medium=2, low=1),
                objective=['unassigned_priority_weight', 'priority_weighted_travel_minutes'],
                order='priority descending, request time ascending, patient ID ascending',
                branch_order='deterministic cyclic rotation by case index',
                inquiry='fixed currently observed accepted offers; no inquiry/search simulation',
                budget_scope='optional model/search work; total observable wall time reported separately')
SOURCE_FILES = ('simulation/allocation_stress_scenarios.py', 'simulation/allocation_stress_experiment.py',
                'simulation/heuristic_assignment.py', 'simulation/allocation_stress_audit.py',
                'test_allocation_stress.py', 'test_allocation_stress_audit.py', 'test_heuristic_assignment.py',
                'simulation/allocation_benchmark.py', 'simulation/allocation_scenarios.py',
                'simulation/cp_sat_assignment.py', 'simulation/sim.py', 'simulation/priority_assignment.py')
LIMITATIONS = [
    '모든 입력은 합성이다. 현행 119 체계나 실제 의료진 의사결정의 재현이 아니다.',
    '같은 현재 수용 후보·자원·이동시간 스냅샷의 배정만 비교한다. 문의시간·선정시간·치료성과는 측정하지 않는다.',
    '우선순위 4·2·1, 자원 수요와 이동시간은 임상적 기준이 아닌 실험 설정이다.',
    '목적은 미배정 우선순위 합을 먼저 최소화하고, 동률일 때 우선순위 가중 이동분 합을 최소화하는 것이다.',
    'CP-SAT가 두 목적의 최적성을 증명한 사례만 최적값과의 차이를 표시한다. 나머지는 관측한 규칙 중 최선값과의 차이다.',
    'H1이 제한시간 CP-SAT의 실행 가능해보다 좋은 경우가 있어도 수학적 최적해보다 우수하다는 뜻이 아니다.',
    '동일 탐색 예산을 주지만 전처리·검증·모델 구성과 구현 차이 때문에 전체 경과시간이 예산을 초과할 수 있다.',
    '단일 실행의 계산시간은 운영체제 부하에 영향을 받는다. 정책 실행 순서를 회전하고 중앙값·p95를 함께 표시한다.',
    '공통 배정 환자의 이동시간과 한쪽만 배정된 환자를 분리한다. 평균 이동시간만으로 미배정 감소를 평가하지 않는다.',
    '사전 정의한 같은 유형의 새 seed 검증이며, 실제 지역·기관·새로운 상황에 대한 일반화를 증명하지 않는다.',
]


def objective(branch):
    row = branch['metrics']
    return (row['unassigned_priority_weight'], row['priority_weighted_travel_minutes'])


def sources():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def manifest():
    from ortools import __version__ as ortools_version
    return dict(schema_version=1, kind='allocation_stress_lock', settings=SETTINGS,
                families=list(FAMILIES), sizes=list(SIZES), development_seeds=list(DEVELOPMENT_SEEDS),
                validation_seeds=list(VALIDATION_SEEDS), runtime_versions=dict(
                    python=sys.version.split()[0], ortools=ortools_version,
                    platform=platform.platform(), machine=platform.machine()), source_sha256=sources())


def verify_lock(lock):
    if lock != manifest():
        raise ValueError('개발 잠금과 현재 소스·설정·환경이 달라 검증 실행을 중단합니다.')


def execution_order(case_index):
    shift = case_index % len(POLICIES)
    return list(POLICIES[shift:] + POLICIES[:shift])


def run_policy(snapshot, policy):
    validate_stress_snapshot(snapshot)
    world = ObservedWorld(snapshot)
    algorithm, sep, raw_budget = policy.partition('@')
    if policy not in POLICIES:
        raise ValueError('unknown stress policy')
    budget = float(raw_budget) if sep else None
    if algorithm == 'R1':
        rule = NearestArrivalRule()
    elif algorithm == 'R2':
        rule = CompetitionAwareRule(lam=SETTINGS['r2_lambda'])
    elif algorithm == 'C1':
        rule = CPSATAssignmentRule(time_limit_seconds=budget, seed=SETTINGS['cp_sat_seed'], require_backend=True)
    else:
        from .heuristic_assignment import HeuristicAssignmentRule
        rule = HeuristicAssignmentRule(time_limit_seconds=budget, max_moves=SETTINGS['h1_max_moves'])
    started = time.perf_counter()
    if getattr(rule, 'joint', False):
        plan = rule.decide_batch(world, world.ready, snapshot['at'])
    else:
        plan = {}
        for patient, options in sorted(world.ready, key=lambda row: (-row[0].priority, row[0].t_req, row[0].id)):
            feasible = [hid for hid in options if world.hospitals[hid].can_accept(patient)]
            hid = rule.choose(world, patient, feasible, snapshot['at']) if feasible else None
            plan[patient.id] = hid
            if hid is not None:
                world.hospitals[hid].taken.update(patient.demand)
                patient.state = 'CONFIRMED'
    wall = time.perf_counter() - started
    audit_assignment(snapshot, plan)
    return dict(policy=policy, algorithm=algorithm, budget_seconds=budget,
                assignments=plan, metrics=metrics(snapshot, plan), wall_seconds=wall,
                fallbacks=getattr(rule, 'fallbacks', 0),
                solver_records=deepcopy(getattr(rule, 'records', [])), input_sha256=digest(snapshot))


def choose_reference(branches):
    proven = [b for b in branches if b['algorithm'] == 'C1' and
              any(r.get('optimal_proven') is True for r in b['solver_records'])]
    if proven:
        values = {objective(b) for b in proven}
        if len(values) != 1:
            raise ValueError('CP-SAT 최적성 증명 결과가 서로 다릅니다.')
        selected = min(proven, key=lambda b: (b['budget_seconds'], b['policy']))
        if any(objective(b) < objective(selected) for b in branches):
            raise ValueError('증명된 최적값보다 우수한 배정이 발견돼 실험을 중단합니다.')
        return dict(kind='proven_optimum', policy=selected['policy'],
                    objective=list(objective(selected)), optimal_proven=True)
    selected = min(branches, key=lambda b: (objective(b), b['policy']))
    return dict(kind='observed_best', policy=selected['policy'],
                objective=list(objective(selected)), optimal_proven=False)


def objective_gaps(branches, reference):
    p_ref, s_ref = reference['objective']
    result = {}
    for branch in branches:
        primary, secondary = objective(branch)
        secondary_gap = secondary - s_ref if primary == p_ref else None
        result[branch['policy']] = dict(primary_difference=primary - p_ref,
            secondary_difference_if_primary_equal=secondary_gap,
            relative_secondary_gap_if_primary_equal=(secondary_gap / s_ref if s_ref and secondary_gap is not None else None))
    return result


def run_case(snapshot, case_index):
    order = execution_order(case_index)
    branches = [run_policy(snapshot, policy) for policy in order]
    by_policy = {b['policy']: b for b in branches}
    reference = choose_reference(branches)
    return dict(snapshot=deepcopy(snapshot), case_index=case_index, execution_order=order,
                branches=branches, reference=reference, objective_gaps=objective_gaps(branches, reference),
                common_travel_vs_r1={code: paired_common_travel(snapshot, by_policy['R1'], by_policy[code]) for code in POLICIES},
                common_travel_vs_c1_same_budget={f'H1@{budget:g}': paired_common_travel(
                    snapshot, by_policy[f'C1@{budget:g}'], by_policy[f'H1@{budget:g}']) for budget in BUDGETS},
                combinations=math.prod(len(options) + 1 for _, options in ObservedWorld(snapshot).ready))


def _aggregate_common(rows):
    denominator = sum(row['denominator'] for row in rows)
    total = sum(row['total_difference_minutes'] for row in rows)
    return dict(denominator=denominator, total_difference_minutes=total,
                mean_difference_minutes=total / denominator if denominator else None,
                comparison_only=sum(row['comparison_only'] for row in rows),
                baseline_only=sum(row['baseline_only'] for row in rows))


def summarize(results):
    summaries = []
    for family in (*FAMILIES, 'ALL'):
        rows = [r for r in results if family == 'ALL' or r['snapshot']['family'] == family]
        if not rows:
            continue
        policies = {}
        for code in POLICIES:
            branches = [next(b for b in row['branches'] if b['policy'] == code) for row in rows]
            walls = sorted(b['wall_seconds'] for b in branches)
            status = Counter(record.get('status', 'UNSPECIFIED') for b in branches for record in b['solver_records'])
            if not status:
                status['NO_PROOF'] = len(branches)
            policies[code] = dict(cases=len(branches),
                mean_unassigned_count=statistics.mean(b['metrics']['unassigned_count'] for b in branches),
                mean_unassigned_priority_weight=statistics.mean(b['metrics']['unassigned_priority_weight'] for b in branches),
                mean_priority_weighted_travel_minutes=statistics.mean(b['metrics']['priority_weighted_travel_minutes'] for b in branches),
                unassigned_by_priority_totals={str(weight): sum(b['metrics']['unassigned_by_priority'][str(weight)] for b in branches)
                                               for weight in (4, 2, 1)},
                wall_median_seconds=statistics.median(walls), wall_p95_seconds=walls[math.ceil(.95 * len(walls)) - 1],
                wall_max_seconds=max(walls), fallbacks=sum(b['fallbacks'] for b in branches),
                optimal_proven_count=sum(any(r.get('optimal_proven') is True for r in b['solver_records']) for b in branches),
                proof_status_counts=dict(sorted(status.items())),
                constraint_violations=sum(b['metrics']['constraint_violations'] for b in branches))
        comparisons = {}
        for budget in BUDGETS:
            h_code, c_code = f'H1@{budget:g}', f'C1@{budget:g}'
            counts = Counter(dict(better=0, equal=0, worse=0))
            for row in rows:
                by_code = {b['policy']: b for b in row['branches']}
                h, c = objective(by_code[h_code]), objective(by_code[c_code])
                counts['better' if h < c else 'worse' if h > c else 'equal'] += 1
            comparisons[f'{budget:g}'] = dict(counts, common_travel=_aggregate_common(
                [row['common_travel_vs_c1_same_budget'][h_code] for row in rows]))
        summaries.append(dict(family=family, cases=len(rows), policies=policies,
                              h1_vs_c1=comparisons,
                              proven_reference_count=sum(row['reference']['optimal_proven'] for row in rows)))
    return summaries


def run(phase, lock=None):
    if phase not in ('development', 'validation'):
        raise ValueError('phase must be development or validation')
    if phase == 'validation':
        if lock is None:
            raise ValueError('validation requires a reviewed lock')
        verify_lock(lock)
    experiment_started = time.perf_counter()
    imported = time.perf_counter()
    from ortools import __version__ as ortools_version
    from ortools.sat.python import cp_model  # noqa: F401
    cold_import = time.perf_counter() - imported
    seeds = DEVELOPMENT_SEEDS if phase == 'development' else VALIDATION_SEEDS
    results = []
    for seed in seeds:
        for snapshot in build_stress_snapshots(seed):
            results.append(run_case(snapshot, len(results)))
    source_hashes = sources()
    return dict(schema_version=1, kind='allocation_stress_comparison', phase=phase,
                synthetic=True, settings=SETTINGS, families=list(FAMILIES), sizes=list(SIZES),
                seeds=list(seeds), limitations=LIMITATIONS,
                runtime=dict(python=sys.version, ortools=ortools_version,
                             cold_solver_import_seconds=cold_import,
                             experiment_wall_seconds=time.perf_counter() - experiment_started,
                             solver_import='completed before policy timing',
                             platform=platform.platform(), machine=platform.machine()),
                runtime_versions=dict(python=sys.version.split()[0], ortools=ortools_version,
                                      platform=platform.platform(), machine=platform.machine()),
                source_sha256=source_hashes, lock_sha256=digest(lock) if lock else None,
                results=results, summary=summarize(results))


def markdown(report):
    all_rows = next(row for row in report['summary'] if row['family'] == 'ALL')
    lines = ['# 합성 자원 경합 입력의 배정 알고리즘 비교', '',
             f"단계: **{report['phase']}** · {len(report['results'])}사례 × 8가지 분기 · 합성 입력.", '',
             'R1(순차 최근거리), R2(기존 경합 비용), H1(개선 휴리스틱), C1(CP-SAT)을 같은 현재 정보에서 비교했다.', '',
             '사전식 목적: **미배정 우선순위 합 → 우선순위 가중 이동시간 합**. 우선순위는 실험값 4·2·1이다.', '',
             '| 규칙 | 평균 미배정 수 | 평균 미배정 가중치 | 평균 가중 이동분 합 | wall 중앙값(ms) | wall p95(ms) | fallback | 최적 증명 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for code, p in all_rows['policies'].items():
        lines.append(f"| {code} | {p['mean_unassigned_count']:.2f} | {p['mean_unassigned_priority_weight']:.2f} | {p['mean_priority_weighted_travel_minutes']:.2f} | {p['wall_median_seconds'] * 1000:.2f} | {p['wall_p95_seconds'] * 1000:.2f} | {p['fallbacks']} | {p['optimal_proven_count']} |")
    lines += ['', '## 같은 탐색 예산의 H1과 C1', '',
              '| 예산(초) | H1 목적값 우수 | 동률 | 열세 | 공통 배정 환자 수 | 공통 환자 이동 차이 H1−C1(분) | H1만 배정 | C1만 배정 |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for budget, p in all_rows['h1_vs_c1'].items():
        c = p['common_travel']
        mean = f"{c['mean_difference_minutes']:.2f}" if c['mean_difference_minutes'] is not None else '없음'
        lines.append(f"| {budget} | {p['better']} | {p['equal']} | {p['worse']} | {c['denominator']} | {mean} | {c['comparison_only']} | {c['baseline_only']} |")
    lines += ['', f"전체 {all_rows['cases']}사례 중 CP-SAT가 두 목적의 최적값을 증명한 사례는 {all_rows['proven_reference_count']}개다. 나머지의 기준값은 관측된 8가지 배정 중 최선이며 최적값으로 부르지 않는다.", '',
              '## 조건별 비교', '', '| 조건 | H1 0.2초 평균 미배정 가중치 | C1 0.2초 평균 미배정 가중치 | H1 우수/동률/열세 | 최적 기준 사례 |', '|---|---:|---:|---|---:|']
    for row in report['summary']:
        if row['family'] == 'ALL':
            continue
        pair = row['h1_vs_c1']['0.2']
        lines.append(f"| {row['family']} | {row['policies']['H1@0.2']['mean_unassigned_priority_weight']:.2f} | {row['policies']['C1@0.2']['mean_unassigned_priority_weight']:.2f} | {pair['better']}/{pair['equal']}/{pair['worse']} | {row['proven_reference_count']} |")
    lines += ['', '## 해석 범위', ''] + ['- ' + item for item in report['limitations']]
    lines += ['', '원시 JSON에는 입력 전체, 실행 순서, 모든 배정, 분리된 목적값, 계산시간, solver 기록, 우선순위별 미배정, 공통 환자·한쪽만 배정된 환자, 소스 해시를 보존했다.', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--phase', choices=('development', 'validation'), default='development')
    parser.add_argument('--lock-file', type=Path, default=ROOT / 'config/allocation_stress_lock_20261014.json')
    parser.add_argument('--lock', action='store_true', help='검토된 개발 실행 후 설정과 소스·환경을 잠금')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.phase == 'validation' and args.lock:
        parser.error('validation cannot overwrite its lock')
    if args.lock and args.lock_file.exists():
        parser.error('기존 잠금 파일을 덮어쓸 수 없습니다. 새 실험에는 명시적인 새 --lock-file 경로를 사용하세요.')
    output = args.output or ROOT / f'simulation_results/allocation_stress_{args.phase}_20261014.json'
    if args.phase == 'validation' and (output.exists() or output.with_suffix('.md').exists()):
        parser.error('기존 검증 결과를 덮어쓸 수 없습니다. 재현 실행에는 명시적인 새 --output 경로를 사용하세요. 같은 seed 재실행은 동일 평가의 재생입니다.')
    if output.exists():
        try:
            previous = json.loads(output.read_text(encoding='utf-8'))
        except (UnicodeError, json.JSONDecodeError):
            previous = None
        if isinstance(previous, dict) and previous.get('phase') == 'validation':
            parser.error('개발 실행으로 기존 검증 결과를 덮어쓸 수 없습니다. 새 --output 경로를 사용하세요.')
    lock = json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase == 'validation' else None
    report = run(args.phase, lock)
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = 'x' if args.phase == 'validation' else 'w'
    with output.open(mode, encoding='utf-8') as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    with output.with_suffix('.md').open(mode, encoding='utf-8') as stream:
        stream.write(markdown(report))
    if args.lock:
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with args.lock_file.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(manifest(), ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(output=str(output), phase=args.phase, cases=len(report['results']),
                          branches=len(report['results']) * len(POLICIES)), ensure_ascii=False))


if __name__ == '__main__':
    main()
