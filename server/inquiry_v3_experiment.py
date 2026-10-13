"""V3 개발 선택과 별도 검증을 분리하는 실제 서버 합성 실험.

python -m server.inquiry_v3_experiment development --output simulation_results/server_inquiry_v3_development_20261012
python -m server.inquiry_v3_experiment validation --lock simulation_results/server_inquiry_v3_development_20261012_lock.json --output simulation_results/server_inquiry_v3_validation_20261012

개발 결과로 고른 설정을 디스크에 잠근 뒤 다른 seed로 실행한다. 검증 결과로
다시 설정을 고르지 않는다. 실험 이름의 설정은 운영 별칭과 독립적이다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform

from . import inquiry_policies
from .inquiry_experiment import aggregate, digest, family_equal_summary, run_scenario
from .inquiry_experiment_scenarios import make_suite


ORIGINAL_POLICIES = ('fixed_1', 'fixed_3', 'fixed_5', 'observed_v2')
DEVELOPMENT_SEEDS = tuple(range(6101, 6109))
VALIDATION_SEEDS = tuple(range(7101, 7113))
PREVIOUSLY_USED_SEEDS = frozenset(DEVELOPMENT_SEEDS).union(
    *(range(start, start + 20) for start in (1001, 2001, 3001, 4001, 5001)))
SOURCE_FILES = (
    'server/inquiry_experiment.py', 'server/inquiry_experiment_scenarios.py',
    'server/inquiry_workflow.py', 'server/inquiry_policies.py',
    'server/inquiry_observations.py', 'server/service.py', 'server/db.py',
    'server/treatment_binding.py', 'simulation/observed_inquiry.py',
    'server/inquiry_v3.py', 'server/inquiry_v3_experiment.py',
)
SELECTION_RULE = {
    'population': 'development_only',
    'aggregation': 'equal_weight_per_scenario_family',
    'ordered_objectives': ['failed_fraction_mean', 'selection_wait_capped_mean',
                           'inquiries_per_patient_mean'],
    'comparison': 'lexicographic',
    'tie_break': 'stable_policy_name',
    'failure_wait_seconds': 300,
}
LIMITATIONS = [
    '실제 서버 문의 코드와 합성 회신 환경의 비교다. 현행 119 체계 복제나 임상 효과 검증이 아니다.',
    '병원·환자·이동시간·검토 시간·인원은 합성값이다. 자원 부족·이송 중 재배정은 이번 실험 범위가 아니다.',
    '개발 조건은 이전 실험에서 본 8개 유형을 재사용한다. 검증은 같은 생성기의 다른 seed이며 현실이나 새로운 유형에 대한 검증은 아니다.',
    '선정은 유효한 수용 회신 후 실험자가 지연 없이 확정한 시각이다. 모든 정책에 같은 확정 규칙을 적용한다.',
    '환자별 발생 후 300초를 관측한다. 실패를 300초로 포함한 대기 평균과 성공률, 문의 수를 함께 본다.',
    '시간차는 양쪽 모두 선정된 동일 환자에 대해서만 비교한다. 성공 환자만의 단순 평균 차이는 개선율로 해석하지 않는다.',
    '개발 선택은 사전 정의한 6개 V3 설정 안에서만 수행한다. 전역 최적 정책이나 전국 개선율을 뜻하지 않는다.',
    '검증 결과를 본 뒤 재튜닝하지 않는다. 기존 4개 기준 정책 설정은 개발·검증에서 동일하다.',
    '잠재 회신값과 병원 검토자 수는 환경만 읽으며 정책에는 실제 시점까지 관측한 DB 사건만 전달한다.',
    '취소된 미착수 문의는 제거하지만 착수한 작업은 끝까지 업무시간을 소비한다.',
]


def source_fingerprints():
    root = Path(__file__).resolve().parents[1]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in SOURCE_FILES}


def _seeds(values):
    if isinstance(values, (str, bytes)):
        raise ValueError('seeds must be distinct nonnegative integers')
    try:
        result = tuple(values)
    except TypeError as error:
        raise ValueError('seeds must be distinct nonnegative integers') from error
    if (not result or len(result) != len(set(result)) or
            any(type(seed) is not int or seed < 0 for seed in result)):
        raise ValueError('seeds must be distinct nonnegative integers')
    return result


def validate_validation_seeds(seeds, development_seeds=DEVELOPMENT_SEEDS):
    result = _seeds(seeds)
    forbidden = PREVIOUSLY_USED_SEEDS.union(_seeds(development_seeds))
    if set(result) & forbidden:
        raise ValueError('validation seeds overlap previously used or development seeds')
    return result


def select_variant(family_equal_rows, variants=None):
    variants = tuple(inquiry_policies.V3_EXPERIMENT_NAMES if variants is None else variants)
    if not variants or len(variants) != len(set(variants)):
        raise ValueError('unique candidate variants are required')
    by_name = {row['policy']: row for row in family_equal_rows}
    if any(name not in by_name for name in variants):
        raise ValueError('a family-equal score is required for every variant')
    candidates = []
    for name in variants:
        row = by_name[name]
        success = row['success_fraction_mean']
        wait = row['selection_wait_capped_mean']
        inquiries = row['inquiries_per_patient_mean']
        if (any(type(value) not in (int, float) or not math.isfinite(value)
                for value in (success, wait, inquiries)) or
                not 0 <= success <= 1 or not 0 <= wait <= 300 or inquiries < 0):
            raise ValueError('finite, bounded policy scores are required')
        vector = [1 - success, wait, inquiries]
        candidates.append((tuple(vector) + (name,), name, vector))
    _, name, vector = min(candidates)
    return dict(name=name, score=dict(zip(SELECTION_RULE['ordered_objectives'], vector)),
                score_vector=vector)


def _paths(output, *, include_lock=False):
    output = Path(output)
    result = [output.with_suffix('.json'), output.with_suffix('.md')]
    if include_lock:
        result.append(output.parent / (output.name + '_lock.json'))
    if any(path.exists() for path in result):
        raise ValueError('output already exists; choose a new output path')
    return result


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)


def read_lock(path):
    """잠금 무결성·현재 소스·정책 설정을 검증한다. 결과를 보고 새로 고르지 않는다."""
    path = Path(path)
    def reject_constant(value):
        raise ValueError('non-finite JSON value: ' + value)
    lock = json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_constant)
    if not isinstance(lock, dict) or lock.get('kind') != 'server_inquiry_v3_development_lock':
        raise ValueError('not a V3 development lock')
    checksum = lock.get('sha256')
    body = {key: value for key, value in lock.items() if key != 'sha256'}
    if checksum != digest(body):
        raise ValueError('lock integrity check failed')
    if lock.get('selection_rule') != SELECTION_RULE:
        raise ValueError('lock selection rule changed')
    if lock.get('source_sha256') != source_fingerprints():
        raise ValueError('source files differ from the development lock')
    chosen = lock.get('chosen_policy')
    if chosen not in inquiry_policies.V3_EXPERIMENT_NAMES:
        raise ValueError('lock contains an unknown V3 variant')
    if lock.get('chosen_policy_spec') != inquiry_policies.spec(chosen):
        raise ValueError('locked policy configuration differs from current code')
    if lock.get('baseline_policy_specs') != [inquiry_policies.spec(name) for name in ORIGINAL_POLICIES]:
        raise ValueError('baseline policy configurations differ from the development lock')
    _seeds(lock['development_seeds'])
    report_path = Path(lock['development_report_path'])
    report_raw = report_path.read_bytes()
    if hashlib.sha256(report_raw).hexdigest() != lock.get('development_report_sha256'):
        raise ValueError('development report differs from the lock')
    report = json.loads(report_raw.decode('utf-8'), parse_constant=reject_constant)
    selected = select_variant(report['family_equal_summary'])
    if (report.get('kind') != 'server_inquiry_v3_development' or
            report.get('seeds') != lock['development_seeds'] or
            report.get('source_sha256') != lock['source_sha256'] or
            selected['name'] != chosen or selected['score'] != lock.get('score') or
            selected['score_vector'] != lock.get('score_vector') or
            report.get('selected_policy') != selected):
        raise ValueError('lock does not match the development selection')
    return lock


def _execute(scenarios, policies, sources):
    results = []
    for index, scenario in enumerate(scenarios, 1):
        results.append(run_scenario(scenario, policies=policies))
        print(f"{index}/{len(scenarios)} {scenario['id']} ({len(policies)} policies)", flush=True)
    if source_fingerprints() != sources:
        raise RuntimeError('source files changed during experiment; results were not published')
    summary = aggregate(results)
    return dict(parameters_frozen=True, clinically_validated=False,
                seeds=sorted({scenario['seed'] for scenario in scenarios}),
                policies=[inquiry_policies.spec(name) for name in policies],
                source_sha256=sources, python_version=platform.python_version(),
                limitations=LIMITATIONS, scenarios=scenarios, results=results,
                summary=summary, family_equal_summary=family_equal_summary(summary))


def markdown(report):
    def number(value):
        return '—' if value is None else f'{value:.2f}'
    development_mode = report['kind'] == 'server_inquiry_v3_development'
    title = 'V3 설정 개발 비교' if development_mode else '잠금 설정의 별도 seed 검증'
    selected = report['selected_policy']
    lines = ['# ' + title, '',
             f"시나리오 {len(report['results'])}개 · 정책 분기 {sum(len(r['branches']) for r in report['results'])}개.", '',
             f"개발 선택 설정: `{selected['name']}`. 실패율 → 실패를 300초로 포함한 대기 → 문의 수 순으로 유형 균등 평균을 비교했다.", '',
             '사전 정의한 6개 설정 중 개발 입력에서 선택한 설정이며, 전역 최적성이나 실제 현장 효과를 뜻하지 않는다.', '',
             '## 유형 균등 평균', '',
             '| 정책 | 유형 수 | 유형별 성공률 평균 | 300초 제한 대기 평균(초) | 문의/환자 평균 |',
             '|---|---:|---:|---:|---:|']
    for row in report['family_equal_summary']:
        lines.append(f"| {row['policy']} | {row['family_count']} | {number(row['success_fraction_mean'] * 100)}% | {number(row['selection_wait_capped_mean'])} | {number(row['inquiries_per_patient_mean'])} |")
    lines += ['', '## 전체 환자 및 유형별 비교', '',
              '선정 평균은 성공한 환자만 포함한다. 시간차는 양쪽 모두 선정된 동일 환자만 비교하며 음수는 고정3보다 빠르다는 뜻이다.', '',
              '| 조건 | 정책 | 성공/전체 | 선정 평균(초) | 제한 대기 평균(초) | 문의/환자 | 공통 성공 수 | 공통 선정 차이 vs 고정3(초) | 공통 예상 도착 차이(초) |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in report['summary']:
        lines.append(f"| {row['family']} | {row['policy']} | {row['selected_count']}/{row['patient_count']} | {number(row['selection_seconds_mean'])} | {number(row['selection_wait_capped_mean'])} | {number(row['inquiries_per_patient'])} | {row['common_patient_count_vs_fixed3']} | {number(row['common_selection_difference_vs_fixed3'])} | {number(row['common_arrival_difference_vs_fixed3'])} |")
    lines += ['', '## 재현', '',
              '각 분기는 같은 초기 SQL 스냅샷과 잠재 회신 환경을 사용한다. JSON에 실제 문의·FIFO 검토·수용 확정의 원시 이력과 소스 해시를 저장했다.']
    if not development_mode:
        lines += ['', f"검증 전에 읽은 디스크 잠금: `{report['validation_lock']['path']}`", '',
                  '개발 선택과 전체 설정을 고정한 뒤 실행했다. 검증 결과로 다시 설정을 고르지 않았다.']
    lines += ['', '## 한계', ''] + ['- ' + text for text in LIMITATIONS]
    return '\n'.join(lines) + '\n'


def development(output, seeds=DEVELOPMENT_SEEDS):
    paths = _paths(output, include_lock=True)
    seeds = _seeds(seeds)
    policies = ORIGINAL_POLICIES + tuple(inquiry_policies.V3_EXPERIMENT_NAMES)
    sources = source_fingerprints()
    report = _execute(make_suite(seeds=seeds), policies, sources)
    report.update(kind='server_inquiry_v3_development', selection_rule=SELECTION_RULE,
                  selected_policy=select_variant(report['family_equal_summary']))
    raw = _json_bytes(report)
    chosen = report['selected_policy']
    lock = dict(kind='server_inquiry_v3_development_lock',
                development_seeds=report['seeds'], selection_rule=SELECTION_RULE,
                chosen_policy=chosen['name'], chosen_policy_spec=inquiry_policies.spec(chosen['name']),
                baseline_policy_specs=[inquiry_policies.spec(name) for name in ORIGINAL_POLICIES],
                score=chosen['score'], score_vector=chosen['score_vector'], source_sha256=sources,
                development_report_path=str(paths[0].resolve()),
                development_report_sha256=hashlib.sha256(raw).hexdigest())
    lock['sha256'] = digest(lock)
    _write_new(paths[0], raw)
    _write_new(paths[1], markdown(report).encode('utf-8'))
    _write_new(paths[2], _json_bytes(lock))
    print(json.dumps(dict(json=str(paths[0]), markdown=str(paths[1]), lock=str(paths[2]),
                          selected_policy=chosen), ensure_ascii=False), flush=True)
    return report


def validation(output, lock_path, seeds=VALIDATION_SEEDS):
    paths = _paths(output)
    # 디스크에 존재하며 무결성을 통과한 잠금을 읽기 전에는 입력을 생성하거나 실행하지 않는다.
    lock_path = Path(lock_path)
    lock = read_lock(lock_path)
    seeds = validate_validation_seeds(seeds, lock['development_seeds'])
    sources = source_fingerprints()
    policies = ORIGINAL_POLICIES + (lock['chosen_policy'],)
    report = _execute(make_suite(seeds=seeds), policies, sources)
    # 실행 중 잠금 파일을 바꾸는 것도 허용하지 않는다.
    current = read_lock(lock_path)
    if current != lock:
        raise RuntimeError('development lock changed during validation; results were not published')
    report.update(kind='server_inquiry_v3_validation', selection_rule=SELECTION_RULE,
                  selected_policy=dict(name=lock['chosen_policy'], score=lock['score'], score_vector=lock['score_vector']),
                  validation_lock=dict(path=str(lock_path.resolve()),
                                       sha256=hashlib.sha256(lock_path.read_bytes()).hexdigest(),
                                       content_sha256=lock['sha256']),
                  development_seeds=lock['development_seeds'])
    _write_new(paths[0], _json_bytes(report))
    _write_new(paths[1], markdown(report).encode('utf-8'))
    print(json.dumps(dict(json=str(paths[0]), markdown=str(paths[1]),
                          locked_policy=lock['chosen_policy']), ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    develop = commands.add_parser('development')
    develop.add_argument('--output', type=Path, required=True)
    validate = commands.add_parser('validation')
    validate.add_argument('--lock', type=Path, required=True)
    validate.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'development':
            development(args.output)
        else:
            validation(args.output, args.lock)
    except (ValueError, OSError, RuntimeError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
