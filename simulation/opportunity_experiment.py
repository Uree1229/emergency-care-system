"""Freeze bounded opportunity protection, then evaluate unused scenario seeds.

python -m simulation.opportunity_experiment --phase development --lock
python -m simulation.opportunity_experiment --phase validation
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics

from .h1_online_experiment import AGGREGATE_FIELDS, diagnostic_metrics, paired_arrival_details, runtime_versions
from .online_allocation_experiment import common_patient_metrics, summary_metrics
from .opportunity_engine import DEFAULT_OPPORTUNITY_POLICIES, EXPERIMENT_ID, run_opportunity_replay
from .opportunity_guard import GRACE_MINUTES
from .opportunity_scenarios import FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, build_opportunity_scenarios
from .partial_information_experiment import SOURCE_FILES as PREVIOUS_SOURCE_FILES, LIMITATIONS as PREVIOUS_LIMITATIONS
from .partial_information_experiment import digest, information_metrics


ROOT = Path(__file__).resolve().parents[1]
DEV_SEEDS = DEVELOPMENT_SEEDS
POLICY_IDS = tuple(p['id'] for p in DEFAULT_OPPORTUNITY_POLICIES)
LOCK_PATH = ROOT / 'config/opportunity_protection_lock_20261015.json'
SOURCE_FILES = tuple(sorted(set(PREVIOUS_SOURCE_FILES) | {
    'simulation/opportunity_guard.py', 'simulation/opportunity_engine.py',
    'simulation/opportunity_scenarios.py', 'simulation/opportunity_experiment.py',
    'simulation/opportunity_audit.py', 'test_opportunity_engine.py',
    'test_opportunity_scenarios.py', 'test_opportunity_experiment.py', 'test_opportunity_audit.py',
}))
SETTINGS = dict(
    policies=deepcopy(list(DEFAULT_OPPORTUNITY_POLICIES)), stale_after_minutes=10,
    inquiry_policy='fixed_3_first_acceptance_extra_wait_2_minutes',
    observation_scope='hospital capacity/used reports; current server commitments and queues remain known',
    freshness_clock='time since source observation, not since network receipt',
    confirmation_contract='explicit synthetic resource response; patient acceptance does not update quantities',
    transient_shortage_retry_minutes=1,
    execution_order='rotate eight branches by seed index plus family index; canonical output order',
    routing='synthetic coordinates/travel; no API', simulation_unit='integer minute',
    compute_time='allocation and protection measured separately, not added to simulation clock',
    tuning='fixed rule before new development and validation',
    protection=dict(grace_minutes=GRACE_MINUTES, resource='ed_bed',
        waiter_structural_choices=1, competitor_minimum_structural_choices=2,
        priority='never defer a higher-priority ready patient',
        clocks='lifetime first unknown/stale wait and first actual hold; never reset',
        offer_expiry='release before the waiting offer or last current ready offer expires on the next tick',
        effect='defer eligible flexible ready patients in priority order until headroom fits, then use ordinary allocation model',
        reservation='no physical reservation or unconfirmed patient allocation'),
)
LIMITATIONS = list(PREVIOUS_LIMITATIONS) + [
    '기회 보호는 H1/C1의 현재 배정 대상을 잠시 동기화하는 규칙이다. 기존 R1·H1·C1 소스와 기존 결과는 변경하지 않는다.',
    '구조적으로 한 병원만 가능한 환자와 대안 병원이 있는 동등·낮은 우선순위 환자 사이의 알려진 fresh 병상 경합만 보호한다.',
    '구조적 대안은 진료 역량 일치로만 센다. 해당 병원의 현재 수용 보장이나 이후 회신을 예측한 것이 아니다.',
    '유보는 첫 확인 대기와 해당 환자의 첫 유보부터 각각 최대 3분이며 재문의·새 수용 응답으로 초기화하지 않는다.',
    '유보 환자의 마지막 유효 수용 회신이나 확인 대기 환자의 회신이 다음 tick에 만료된다면 유보하지 않는다. 3분은 전체 확정·도착 지연의 상한이 아니다.',
    '현재 수용·자원 보고가 끝까지 없으면 자동 배정 미확보가 남는다. 보호 후 도착이나 모든 환자의 비악화는 보장하지 않는다.',
    '16개 합성 유형의 새 seed 검증이며 유형 자체가 보지 못한 실상황이라는 뜻은 아니다. 기존 검증 seed는 재사용하지 않는다.',
]


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def manifest():
    return dict(schema_version=1, kind='opportunity_protection_lock', experiment_id=EXPERIMENT_ID,
        families=list(FAMILIES), settings=deepcopy(SETTINGS), development_seeds=list(DEV_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS), runtime_versions=runtime_versions(), source_sha256=source_hashes())


def verify_lock(lock):
    if lock != manifest():
        raise ValueError('기회 보호 실험의 소스·설정·런타임·seed가 잠금과 다릅니다.')


def guard_metrics(branch):
    holds = [e for e in branch['events'] if e['type'] == 'opportunity_hold']
    return dict(guard_calls=len(branch['protection_records']), hold_checks=len(holds),
        held_patients=len({e['patient'] for e in holds}),
        max_hold_age_minutes=max((e['t'] - e['held_since'] for e in holds), default=0),
        max_hold_deadline_minutes=max((e['deadline'] - e['held_since'] for e in holds), default=0),
        protection_wall_seconds=branch['metrics']['protection_wall_seconds'])


def aggregate(results):
    return [dict(family=family, policies={code: dict(cases=len(cases), **{
        field: statistics.mean(c['summaries'][code][field] for c in cases)
        for field in AGGREGATE_FIELDS}) for code in POLICY_IDS})
        for family in FAMILIES
        for cases in [[r for r in results if r['scenario']['family'] == family]]]


def run(phase, lock=None, progress=None):
    if phase not in ('development', 'validation'):
        raise ValueError('unknown phase')
    if phase == 'validation':
        verify_lock(lock)
    from ortools.sat.python import cp_model  # exclude cold import from measured decisions
    seeds = DEV_SEEDS if phase == 'development' else VALIDATION_SEEDS
    results, total = [], len(seeds) * len(FAMILIES)
    for si, seed in enumerate(seeds):
        for fi, scenario in enumerate(build_opportunity_scenarios(seed)):
            offset = (si + fi) % len(POLICY_IDS)
            replay = run_opportunity_replay(scenario, execution_order=POLICY_IDS[offset:] + POLICY_IDS[:offset])
            branches = replay['branches']
            by_id = {b['id']: b for b in branches}
            results.append(dict(scenario=replay['scenario'], branches=branches,
                execution_order=replay['execution_order'], routing_summary=replay['routing_summary'],
                summaries={b['id']: summary_metrics(replay['scenario'], b) for b in branches},
                information_metrics={b['id']: information_metrics(b) for b in branches},
                guard_metrics={b['id']: guard_metrics(b) for b in branches},
                diagnostics={b['id']: diagnostic_metrics(replay['scenario'], b) for b in branches},
                paired_vs_perfect={r: common_patient_metrics(replay['scenario'], by_id['perfect_' + r],
                    by_id['observed_' + r]) for r in ('R1', 'H1', 'C1')},
                paired_arrival_details={r: paired_arrival_details(by_id['perfect_' + r], by_id['observed_' + r])
                    for r in ('R1', 'H1', 'C1')},
                paired_vs_observed={r: common_patient_metrics(replay['scenario'], by_id['observed_' + r],
                    by_id['protected_' + r]) for r in ('H1', 'C1')},
                paired_protection_details={r: paired_arrival_details(by_id['observed_' + r], by_id['protected_' + r])
                    for r in ('H1', 'C1')}))
            if progress:
                progress(len(results), total, scenario['id'])
    return dict(schema_version=1, kind='opportunity_protection_comparison', experiment_id=EXPERIMENT_ID,
        phase=phase, synthetic=True, seeds=list(seeds), families=list(FAMILIES), settings=deepcopy(SETTINGS),
        limitations=list(LIMITATIONS), runtime_versions=runtime_versions(), source_sha256=source_hashes(),
        lock_sha256=digest(lock) if lock else None, results=results, summary=aggregate(results))


def markdown(report):
    lines = ['# 확인 대기 환자의 병상 기회 보호 검증', '',
        f"{report['phase']} · {len(report['results'])}사례 × 8분기 · 합성 입력", '',
        '동일한 실제 환경·정보 전달 조건에서 보호 전/후 H1·C1을 비교한다. 기존 규칙과 완전정보 기준도 함께 기록한다.',
        '최대 3분간 경합하는 유연 환자 일부의 배정을 유보한다. 실제 병상을 예약하거나 미확인 수용을 확정하지 않는다.', '',
        '| 조건 | 규칙 | 보호 전 미도착 | 보호 후 미도착 | 공통 도착 | 도착 차이(분) | 빨라짐 | 느려짐 | 유보 환자 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for family in report['families']:
        cases = [r for r in report['results'] if r['scenario']['family'] == family]
        for rule in ('H1', 'C1'):
            left, right = 'observed_' + rule, 'protected_' + rule
            pairs = [c['paired_vs_observed'][rule]['arrival'] for c in cases]
            n = sum(p['denominator'] for p in pairs)
            delta = sum(p['denominator'] * p['mean_difference_minutes'] for p in pairs if p['denominator']) / n if n else None
            details = [c['paired_protection_details'][rule] for c in cases]
            # Detailed patient changes, including one-sided arrival, stay in JSON.
            faster = sum(d['earlier'] for d in details)
            slower = sum(d['delayed'] for d in details)
            values = (sum(c['summaries'][left]['not_arrived'] for c in cases),
                      sum(c['summaries'][right]['not_arrived'] for c in cases),
                      sum(c['guard_metrics'][right]['held_patients'] for c in cases))
            delta_text = f'{delta:+.3f}' if delta is not None else '—'
            lines.append(f'| {family} | {rule} | {values[0]} | {values[1]} | {n} | {delta_text} | {faster} | {slower} | {values[2]} |')
    lines += ['', '차이는 보호 후−보호 전이며 음수는 빨라짐이다. 공통 도착만의 평균과 한쪽만 도착한 환자를 구분한다.',
        '유보 환자 수는 보호가 적용된 고유 환자 수다. 유보 검사 횟수·자원 재확인·수용 문의는 별도 집계한다.', '',
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
    output = args.output or ROOT / f'simulation_results/opportunity_protection_{args.phase}_20261015.json'
    if output.suffix.lower() != '.json':
        parser.error('report output must use a .json suffix')
    protected = [output, output.with_suffix('.md'), output.with_name(output.stem + '_audit.json')]
    if args.lock:
        protected.append(args.lock_file)
    if len({p.resolve() for p in protected}) != len(protected):
        parser.error('report, Markdown, audit and lock paths must be distinct')
    if any(p.exists() for p in protected):
        parser.error('output already exists; choose new explicit paths')
    lock = json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase == 'validation' else None
    report = run(args.phase, lock, progress=lambda done, total, name:
        print(json.dumps(dict(done=done, total=total, scenario=name)), flush=True))
    from .opportunity_audit import audit_report
    audited = audit_report(report, expected_source_sha256=source_hashes(), lock=lock)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    output.with_suffix('.md').write_text(markdown(report), encoding='utf-8')
    output.with_name(output.stem + '_audit.json').write_text(json.dumps(audited, indent=2) + '\n', encoding='utf-8')
    if args.lock:
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        args.lock_file.write_text(json.dumps(manifest(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(output=str(output), phase=args.phase, cases=len(report['results']),
                          branches=len(report['results']) * len(POLICY_IDS), audit=audited), ensure_ascii=True))


if __name__ == '__main__':
    main()
