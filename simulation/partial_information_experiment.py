"""Locked synthetic missing/delayed resource information comparisons.

python -m simulation.partial_information_experiment --phase development --lock
python -m simulation.partial_information_experiment --phase validation
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics

from .h1_online_experiment import (
    SOURCE_FILES as PREVIOUS_SOURCE_FILES, AGGREGATE_FIELDS, diagnostic_metrics,
    paired_arrival_details, runtime_versions,
)
from .online_allocation_experiment import common_patient_metrics, summary_metrics
from .partial_information_engine import DEFAULT_INFORMATION_POLICIES, EXPERIMENT_ID, run_information_replay
from .partial_information_scenarios import FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, build_information_scenarios


ROOT = Path(__file__).resolve().parents[1]
DEV_SEEDS = DEVELOPMENT_SEEDS
POLICY_IDS = tuple(p['id'] for p in DEFAULT_INFORMATION_POLICIES)
LOCK_PATH = ROOT / 'config/partial_information_lock_20261015.json'
SOURCE_FILES = tuple(sorted(set(PREVIOUS_SOURCE_FILES) | {
    'simulation/information_observations.py', 'simulation/partial_information_engine.py',
    'simulation/partial_information_experiment.py', 'simulation/partial_information_scenarios.py',
    'simulation/partial_information_audit.py',
}))
SETTINGS = dict(
    policies=deepcopy(list(DEFAULT_INFORMATION_POLICIES)), stale_after_minutes=10,
    inquiry_policy='fixed_3_first_acceptance_extra_wait_2_minutes',
    observation_scope='hospital capacity/used reports; current server commitments and queues remain known',
    freshness_clock='time since source observation, not since network receipt',
    confirmation_contract='explicit synthetic resource response; patient acceptance does not update quantities',
    transient_shortage_retry_minutes=1,
    execution_order='rotate six branches by seed index plus family index; canonical output order',
    routing='synthetic coordinates/travel; no API', simulation_unit='integer minute',
    compute_time='measured separately, not added to simulation clock', tuning='none',
)
LIMITATIONS = [
    '병원·환자·자원·회신·자원 확인·위치·이동·처치시간은 합성 설정이다. 실제 병원이나 카카오 API를 호출하지 않는다.',
    '완전정보는 손실을 비교하기 위한 기준이다. 관측 분기는 전달된 병원 capacity/used만 알고 서버의 현재 확정 예약·도착 대기는 즉시 안다.',
    '병원 자원 수량의 누락·전달 지연만 바꾼다. 수용 판단·이송 중 변경 알림·서버 확정 검증까지 모두 지연시킨 실험이 아니다.',
    '현재 서버의 수용 회신은 자원 수량 갱신이 아니다. 이 실험의 별도 자원 확인 요청·응답은 새 합성 실험 계약이다.',
    '자원 수량을 모르는 상태에서 유효 수용 응답으로 사람이 수동 확정하는 기존 운영 경로는 별도다. 여기의 미배정은 관측 수량 기반 자동 배정 미확보이며 실제 수용 거절을 뜻하지 않는다.',
    '등록된 대기 수요는 외부 유입을 포함해 즉시 알려진 원장으로 가정한다. 등록되지 않은 외부 대기열이나 대기 정보 자체의 누락은 검증하지 않는다.',
    'source 관측시각과 수신시각을 분리한다. 10분 초과만 stale이고 정확히 10분은 fresh다. 현재 운영 API에 이 전달층을 구현한 것은 아니다.',
    'fresh 부족은 일시 유보하고 1분 후 재평가한다. 실제 서버 문의 workflow 수명주기를 그대로 재현한 것이 아니다.',
    '관측 모델의 제안과 실제 확정 결과를 분리한다. H1/C1 목적·증명은 제안 모델에만 적용하고 gate 이후 결과의 최적성은 주장하지 않는다.',
    '최종 확정 gate는 환경의 실제 수량을 즉시 검사할 수 있다고 가정한다. 운영 서버가 병원 내부 실제 수량을 자동 감지한다는 뜻이 아니며 gate 자체의 지연·실패는 이번 비교에서 제외한다.',
    '같은 잠재 환경과 정보 전달 규칙을 주지만 분기별 선택으로 이후 점유·회신·확인 요청과 관측 수량은 달라질 수 있다.',
    '없는 정보는 0이나 가용량으로 대체하지 않는다. 끝까지 확인 응답이 없으면 미배정/확인 필요 상태가 남을 수 있다.',
    'H1 50ms는 기준안 이후 추가 탐색, C1 50ms는 모델 작성과 두 단계 탐색이다. 총 호출시간의 강제 상한이나 임상 안전시간이 아니다.',
    '계산시간은 분 단위 모의 시계에 더하지 않는다. 같은 유형의 새 seed 검증이며 실제 현장 일반화를 입증하지 않는다.',
]
INFO_FIELDS = ('information_wait_checks', 'resource_confirmation_requests', 'confirmation_rejections',
               'ignored_resource_reports', 'unknown_allocation_checks', 'stale_allocation_checks')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def manifest():
    return dict(schema_version=1, kind='partial_information_lock', experiment_id=EXPERIMENT_ID,
        families=list(FAMILIES), settings=deepcopy(SETTINGS), development_seeds=list(DEV_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS), runtime_versions=runtime_versions(), source_sha256=source_hashes())


def verify_lock(lock):
    if lock != manifest():
        raise ValueError('정보 누락·갱신 지연 실험의 소스·설정·런타임·seed가 잠금과 다릅니다.')


def information_metrics(branch):
    events = branch['events']
    return dict(
        information_wait_checks=sum(e['type'] == 'information_wait' for e in events),
        resource_confirmation_requests=sum(e['type'] == 'resource_confirmation_request' for e in events),
        confirmation_rejections=sum(e['type'] == 'confirmation_rejected' for e in events),
        ignored_resource_reports=sum(e['type'] == 'resource_report_received' and not e['applied'] for e in events),
        unknown_allocation_checks=sum(e['type'] == 'information_check' and e['purpose'] == 'allocation'
                                      and e['reason'] == 'UNKNOWN' for e in events),
        stale_allocation_checks=sum(e['type'] == 'information_check' and e['purpose'] == 'allocation'
                                    and e['reason'] == 'STALE' for e in events),
    )


def aggregate(results):
    groups = []
    for family in FAMILIES:
        cases = [r for r in results if r['scenario']['family'] == family]
        policies = {}
        for code in POLICY_IDS:
            policies[code] = dict(cases=len(cases), **{
                field: statistics.mean(c['summaries'][code][field] for c in cases)
                for field in AGGREGATE_FIELDS})
        groups.append(dict(family=family, policies=policies))
    return groups


def run(phase, lock=None, progress=None):
    if phase not in ('development', 'validation'):
        raise ValueError('unknown phase')
    if phase == 'validation':
        verify_lock(lock)
    from ortools.sat.python import cp_model  # cold import excluded from measured decisions
    seeds = DEV_SEEDS if phase == 'development' else VALIDATION_SEEDS
    results = []
    total = len(seeds) * len(FAMILIES)
    for seed_index, seed in enumerate(seeds):
        for family_index, scenario in enumerate(build_information_scenarios(seed)):
            offset = (seed_index + family_index) % len(POLICY_IDS)
            replay = run_information_replay(scenario, execution_order=POLICY_IDS[offset:] + POLICY_IDS[:offset])
            branches = replay['branches']
            summaries = {b['id']: summary_metrics(replay['scenario'], b) for b in branches}
            by_id = {b['id']: b for b in branches}
            results.append(dict(scenario=replay['scenario'], branches=branches, summaries=summaries,
                execution_order=replay['execution_order'], routing_summary=replay['routing_summary'],
                information_metrics={b['id']: information_metrics(b) for b in branches},
                diagnostics={b['id']: diagnostic_metrics(replay['scenario'], b) for b in branches},
                paired_vs_perfect={rule: common_patient_metrics(replay['scenario'], by_id['perfect_' + rule],
                    by_id['observed_' + rule]) for rule in ('R1', 'H1', 'C1')},
                paired_arrival_details={rule: paired_arrival_details(by_id['perfect_' + rule],
                    by_id['observed_' + rule]) for rule in ('R1', 'H1', 'C1')}))
            if progress:
                progress(len(results), total, scenario['id'])
    return dict(schema_version=1, kind='partial_information_comparison', experiment_id=EXPERIMENT_ID,
        phase=phase, synthetic=True, seeds=list(seeds), families=list(FAMILIES), settings=deepcopy(SETTINGS),
        limitations=list(LIMITATIONS), runtime_versions=runtime_versions(), source_sha256=source_hashes(),
        lock_sha256=digest(lock) if lock else None, results=results, summary=aggregate(results))


def markdown(report):
    lines = ['# 정보 누락·갱신 지연 검증', '',
        f"{report['phase']} · {len(report['results'])}사례 × 6분기 · 합성 입력", '',
        '같은 실제 환경에서 완전정보 기준과 관측정보를 R1/H1/C1별로 비교했다. 문의 3곳·추가 대기 2분을 고정했다.', '',
        '| 조건 | 규칙 | 완전정보 미도착 | 관측정보 미도착 | 공통 도착 | 도착 차이(분) | 수량 재확인 | 확정 거절 |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for family in report['families']:
        cases = [r for r in report['results'] if r['scenario']['family'] == family]
        for rule in ('R1', 'H1', 'C1'):
            left, right = 'perfect_' + rule, 'observed_' + rule
            pairs = [r['paired_vs_perfect'][rule]['arrival'] for r in cases]
            n = sum(p['denominator'] for p in pairs)
            total = sum(p['denominator'] * p['mean_difference_minutes'] for p in pairs if p['denominator'])
            delta = f'{total / n:+.3f}' if n else '—'
            values = (sum(r['summaries'][left]['not_arrived'] for r in cases),
                      sum(r['summaries'][right]['not_arrived'] for r in cases),
                      sum(r['information_metrics'][right]['resource_confirmation_requests'] for r in cases),
                      sum(r['information_metrics'][right]['confirmation_rejections'] for r in cases))
            lines.append(f'| {family} | {rule} | {values[0]} | {values[1]} | {n} | {delta} | {values[2]} | {values[3]} |')
    lines += ['', '차이는 관측정보−같은 규칙의 완전정보 기준이며 양수는 지연이다. 공통 도착 분모와 한쪽만 도착한 수를 원시 JSON에 함께 보존한다.', '',
        '환자 수용 문의와 병원 자원 수량 재확인은 별도 사건이다. 재확인 수를 초기 문의 수에 섞지 않는다. '
        '확정 거절은 실제 자원 검사가 오래된 관측 기반 제안을 차단한 횟수이며 잘못 확정한 환자 수가 아니다.', '',
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
    output = args.output or ROOT / f'simulation_results/partial_information_{args.phase}_20261015.json'
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
    from .partial_information_audit import audit_report
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
