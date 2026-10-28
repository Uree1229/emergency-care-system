"""A separate, locked R1/H1/C1 inquiry-to-arrival comparison.

python -m simulation.h1_online_experiment --phase development --lock
python -m simulation.h1_online_experiment --phase validation

No frozen experiment, live DB, app UI or existing map replay is overwritten.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics
import sys

from .h1_online_engine import DEFAULT_H1_POLICIES, EXPERIMENT_ID, run_h1_online_replay
from .online_allocation_experiment import (
    SOURCE_FILES as PREVIOUS_SOURCE_FILES, common_patient_metrics, summary_metrics,
)
from .online_allocation_scenarios import FAMILIES, build_online_scenarios


ROOT = Path(__file__).resolve().parents[1]
DEV_SEEDS = (15101, 15102, 15103)
VALIDATION_SEEDS = (16101, 16102, 16103, 16104, 16105, 16106)
POLICY_IDS = tuple(s['id'] for s in DEFAULT_H1_POLICIES)
SOURCE_FILES = tuple(sorted(set(PREVIOUS_SOURCE_FILES) | {
    'simulation/h1_online_engine.py', 'simulation/h1_online_experiment.py',
    'simulation/h1_online_audit.py', 'simulation/heuristic_assignment.py',
    'config/heuristic_assignment_selected_20261014.json',
}))
LOCK_PATH = ROOT / 'config/h1_online_lock_20261015.json'
SETTINGS = dict(
    policies=deepcopy(list(DEFAULT_H1_POLICIES)),
    comparison_scope='same_potential_environment_not_identical_offers',
    inquiry_policy='fixed_3_first_acceptance_extra_wait_2_minutes',
    routing='synthetic fixed coordinates and travel; no external API',
    simulation_unit='integer minute',
    solver_wall_time='measured separately; not added to simulation clock',
    execution_order='rotate R1/H1/C1 by seed index plus family index; canonical output order',
    tuning='none; H1 uses previously selected 50ms/2048 moves unchanged',
)
LIMITATIONS = [
    '환자·병원·자원·회신·출동·처치시간·경로는 전부 합성 설정이다. 실제 카카오 경로나 병원 운영 자료를 사용한 실험이 아니다.',
    '고정 문의 3곳·첫 수용 후 추가 대기 2분·변경 알림을 동일하게 유지한다. V3를 통합한 실험은 아니다.',
    '동일한 잠재 환경을 주지만 배정이 달라지면 이후 자원·수용 응답도 달라진다. 동일 수용 후보를 고정한 스냅샷 비교와 구분한다.',
    'R1은 환자별 순차 선택이고 H1/C1은 같은 분의 준비 환자만 공동 배정한다. 기존 확정 목적지를 공동 배정으로 변경하지 않는다.',
    '이송 중 단일 대체 선택은 H1/C1 모두 근거리 규칙이다. 이송 중 모든 목적지 변경을 공동 최적화한 실험이 아니다.',
    '현재 자원 관측이 완전하다는 가정이다. 관측 누락·오래된 정보·실시간 실제 위치의 검증은 다음 단계다.',
    '우선순위 4·2·1, 치료시간과 변경 대응은 실험 설정이다. 현행 119 또는 의료진의 임상 판단을 재현하지 않는다.',
    'H1은 개별 배치·전체 이송의 최적성을 증명하지 않는다. C1의 증명도 현재 배치의 두 목적 안에서만 성립한다.',
    'H1의 50ms는 기준안 이후 추가 탐색, C1의 50ms는 모델 작성과 두 단계 탐색 예산이다. 전체 호출시간의 강제 상한이 아니다.',
    '시간 제한 실행은 같은 입력에서도 컴퓨터 부하에 따라 다른 실행 가능한 해를 반환할 수 있다. 실행 순서를 회전하고 시간은 별도 보고한다.',
    '계산시간은 분 단위 모의 시계에 더하지 않는다. 같은 생성기의 새 seed 검증이며 실제 현장 일반화를 입증하지 않는다.',
    '미배정 감소가 모든 환자의 도착 단축을 뜻하지 않는다. 공통 환자의 지연·한쪽만 도착한 수와 실패를 포함한 제한 평균을 함께 본다.',
]
AGGREGATE_FIELDS = (
    'unassigned', 'not_arrived', 'untreated', 'inquiries_per_patient',
    'selection_capped_mean_minutes', 'arrival_capped_mean_minutes',
    'treatment_capped_mean_minutes', 'redirects', 'transfers',
    'notifications', 'notification_without_redirect',
)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def runtime_versions():
    from ortools import __version__
    return dict(python=sys.version.split()[0], ortools=__version__)


def manifest():
    return dict(schema_version=1, kind='h1_online_lock', experiment_id=EXPERIMENT_ID,
        families=list(FAMILIES), settings=deepcopy(SETTINGS),
        development_seeds=list(DEV_SEEDS), validation_seeds=list(VALIDATION_SEEDS),
        runtime_versions=runtime_versions(), source_sha256=source_hashes())


def verify_lock(lock):
    if lock != manifest():
        raise ValueError('H1 전체 이송 실험의 소스·설정·런타임·seed가 잠금과 다릅니다.')


def aggregate(results):
    groups = []
    for family in FAMILIES:
        rows = [r for r in results if r['scenario']['family'] == family]
        policies = {}
        for code in POLICY_IDS:
            policies[code] = dict(cases=len(rows), **{
                field: statistics.mean(r['summaries'][code][field] for r in rows)
                for field in AGGREGATE_FIELDS})
        groups.append(dict(family=family, policies=policies))
    return groups


def _percentile(values, fraction=.95):
    if not values:
        return None
    import math
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def diagnostic_metrics(scenario, branch):
    """Keep additional distributions outside the frozen summary contract."""
    outcomes = branch['patient_outcomes']
    decisions = branch['allocation_decisions']
    values = [p['final_arrive_t'] - p['occur_t'] for p in outcomes if p['final_arrive_t'] is not None]
    times = [d['elapsed_seconds'] * 1000 for d in decisions]
    priorities = {}
    for priority in (4, 2, 1):
        group = [p for p in outcomes if p['priority'] == priority]
        priorities[str(priority)] = dict(
            patients=len(group), unassigned=sum(p['first_commit_t'] is None for p in group),
            not_arrived=sum(p['final_arrive_t'] is None for p in group),
            untreated=sum(p['treatment_start_t'] is None for p in group))
    return dict(
        arrival_p95_minutes=_percentile(values), arrival_denominator=len(values),
        allocation_wall_p95_ms=_percentile(times), allocation_wall_max_ms=max(times, default=None),
        allocation_decision_denominator=len(times),
        priority_groups=priorities,
    )


def paired_arrival_details(baseline, comparison):
    left = {p['id']: p for p in baseline['patient_outcomes']}
    right = {p['id']: p for p in comparison['patient_outcomes']}
    differences = [right[pid]['final_arrive_t'] - left[pid]['final_arrive_t']
        for pid in left if left[pid]['final_arrive_t'] is not None and right[pid]['final_arrive_t'] is not None]
    return dict(denominator=len(differences), delayed=sum(v > 0 for v in differences),
        earlier=sum(v < 0 for v in differences), equal=sum(v == 0 for v in differences),
        max_delay_minutes=max(0, max(differences)) if differences else None,
        p95_difference_minutes=_percentile(differences))


def run(phase, lock=None, progress=None):
    if phase not in ('development', 'validation'):
        raise ValueError('unknown phase')
    if phase == 'validation':
        verify_lock(lock)
    from ortools.sat.python import cp_model  # preload before measured decisions
    selected = json.loads((ROOT / 'config/heuristic_assignment_selected_20261014.json').read_text(encoding='utf-8'))
    h1 = next(s for s in DEFAULT_H1_POLICIES if s['rule'] == 'H1')
    if selected['parameters'] != {k: h1[k] for k in ('time_limit_seconds', 'max_moves')}:
        raise ValueError('H1 parameters differ from the previous development selection')
    seeds = DEV_SEEDS if phase == 'development' else VALIDATION_SEEDS
    results = []
    total = len(seeds) * len(FAMILIES)
    for seed_index, seed in enumerate(seeds):
        for family_index, scenario in enumerate(build_online_scenarios(seed)):
            offset = (seed_index + family_index) % len(POLICY_IDS)
            order = POLICY_IDS[offset:] + POLICY_IDS[:offset]
            replay = run_h1_online_replay(scenario, execution_order=order)
            branches = replay['branches']
            summaries = {b['id']: summary_metrics(replay['scenario'], b) for b in branches}
            for branch in branches:
                branch['online_summary'] = summaries[branch['id']]
            baseline = next(b for b in branches if b['id'] == 'R1')
            results.append(dict(
                scenario=replay['scenario'], branches=branches, summaries=summaries,
                duration_minutes=replay['duration_minutes'], routing_summary=replay['routing_summary'],
                execution_order=replay['execution_order'],
                paired_vs_r1={b['id']: common_patient_metrics(replay['scenario'], baseline, b) for b in branches},
                diagnostics={b['id']: diagnostic_metrics(replay['scenario'], b) for b in branches},
                paired_arrival_details={b['id']: paired_arrival_details(baseline, b) for b in branches},
            ))
            if progress:
                progress(len(results), total, scenario['id'])
    return dict(schema_version=1, kind='online_allocation_comparison', experiment_id=EXPERIMENT_ID,
        phase=phase, synthetic=True, seeds=list(seeds), families=list(FAMILIES),
        settings=deepcopy(SETTINGS), limitations=list(LIMITATIONS),
        runtime_versions=runtime_versions(), source_sha256=source_hashes(),
        lock_sha256=digest(lock) if lock else None, results=results, summary=aggregate(results))


def markdown(report):
    lines = ['# H1 전체 이송 실험: R1·H1·C1 비교', '',
        f"{report['phase']} · {len(report['results'])}사례 × 3규칙 · 전부 합성 입력", '',
        '문의 3곳·첫 수용 후 최대 추가 대기 2분·변경 알림을 고정했다. H1과 C1의 탐색 설정은 각각 50ms다.', '',
        '| 조건 | 규칙 | 평균 미배정 | 평균 미도착 | 도착 제한 경과분 | 환자당 문의 | 평균 재배정 | 평균 전원 |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for group in report['summary']:
        for code, row in group['policies'].items():
            lines.append(f"| {group['family']} | {code} | {row['unassigned']:.2f} | {row['not_arrived']:.2f} | "
                f"{row['arrival_capped_mean_minutes']:.2f} | {row['inquiries_per_patient']:.2f} | "
                f"{row['redirects']:.2f} | {row['transfers']:.2f} |")
    lines += ['', '도착 시간은 환자 발생부터 최종 수용 병원 도착까지다. 미도착은 관찰 종료까지의 경과시간으로 제한 평균에 포함한다.', '',
        '## 전체 집계와 공통 환자 비교', '',
        '| 규칙 | 환자 실행 수 | 미배정 | 미도착 | 문의 | 재배정 | 전원 | R1과 공통 도착 | 공통 환자 도착 차이(분) | 더 늦은 공통 환자 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for code in POLICY_IDS:
        sums = {k: sum(r['summaries'][code][k] for r in report['results'])
                for k in ('patients', 'unassigned', 'not_arrived', 'inquiries', 'redirects', 'transfers')}
        pairs = [r['paired_vs_r1'][code]['arrival'] for r in report['results']]
        denominator = sum(p['denominator'] for p in pairs)
        total = sum(p['mean_difference_minutes'] * p['denominator'] for p in pairs if p['denominator'])
        delta = f'{total / denominator:+.3f}' if denominator else '—'
        delayed = sum(r['paired_arrival_details'][code]['delayed'] for r in report['results'])
        lines.append(f"| {code} | {sums['patients']} | {sums['unassigned']} | {sums['not_arrived']} | "
            f"{sums['inquiries']} | {sums['redirects']} | {sums['transfers']} | {denominator} | {delta} | {delayed} |")
    lines += ['', '차이는 비교 규칙−R1이며 양수는 지연이다. 같은 환자 중 양쪽 모두 최종 도착한 사람만 비교한다. '
        '한쪽만 도착한 수와 우선순위별 결과, P95, 계산시간은 원시 JSON에 함께 보존한다.', '',
        '## 범위와 한계', ''] + ['- ' + item for item in report['limitations']]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--phase', choices=('development', 'validation'), default='development')
    parser.add_argument('--lock', action='store_true')
    parser.add_argument('--lock-file', type=Path, default=LOCK_PATH)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.phase == 'validation' and args.lock:
        parser.error('validation cannot overwrite the development lock')
    output = args.output or ROOT / f'simulation_results/h1_online_{args.phase}_20261015.json'
    protected = [output, output.with_suffix('.md'), output.with_name(output.stem + '_audit.json')]
    if args.lock:
        protected.append(args.lock_file)
    if any(p.exists() for p in protected):
        parser.error('output already exists; choose new explicit paths to preserve previous results')
    lock = json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase == 'validation' else None
    report = run(args.phase, lock, progress=lambda done, total, name:
        print(json.dumps(dict(done=done, total=total, scenario=name)), flush=True))
    from .h1_online_audit import audit_report
    audited = audit_report(report, expected_source_sha256=source_hashes(), lock=lock)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    output.with_suffix('.md').write_text(markdown(report), encoding='utf-8')
    output.with_name(output.stem + '_audit.json').write_text(
        json.dumps(audited, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.lock:
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        args.lock_file.write_text(json.dumps(manifest(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(output=str(output), phase=args.phase, cases=len(report['results']),
        branches=len(report['results']) * len(POLICY_IDS), audit=audited), ensure_ascii=True))


if __name__ == '__main__':
    main()
