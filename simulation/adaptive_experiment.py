"""관측 응답으로 문의 규모를 조절하는 정책의 개발·미사용 입력 검증.

python -m simulation.adaptive_experiment

기존 workload_validation 결과를 보존한다. 정책 선택에는 개발 입력만 쓰며,
검증은 이전 실험에 쓰지 않은 seed와 별도의 합성 서비스·배경 업무를 사용한다.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from .map_replay import ROOT, RouteSnapshot, build_default_scenarios, generate_replay
from .replay_experiment import shared_patient_time_difference
from .road_routes import RoadRouteError, RoadRouteProvider
from .workload_experiment import (run_suite, score_policy, seed_workload_scenario,
                                  select_development_policy, summarize_workload)
from .workload_scenarios import build_workload_scenarios


FIXED_POLICIES = tuple(dict(id=f'adaptive_fixed_k{k}', label=f'고정 {k}곳 문의',
    rule='R1', k=k, wait=0, notify=True, inquiry_ranking='travel') for k in (1, 3, 5))
ADAPTIVE_POLICIES = tuple(dict(
    id=f'adaptive_i{initial}_s{slow}', label=f'응답 관측형 · 초기 {initial}곳 · 지연 {slow}분',
    rule='R1', k=5, wait=0, notify=True, inquiry_ranking='travel',
    adaptive_inquiry=dict(initial_k=initial, min_k=1, fast_k=3, window=20,
        min_samples=3, slow_minutes=slow, timeout_fraction=.5))
    for initial in (1, 3) for slow in (3, 6))
DEVELOPMENT_POLICIES = FIXED_POLICIES + ADAPTIVE_POLICIES
PREVIOUS_VALIDATION_SEEDS = frozenset(range(1001, 1021))
DEFAULT_DEVELOPMENT_START = 101
DEFAULT_VALIDATION_START = 2001

LIMITATIONS = [
    '합성 모형 안의 정책 비교다. 실제 119·광역상황실 구현이나 현행 체계 대비 개선율이 아니다.',
    '기관 식별자·이름·좌표만 공개 기관 자료다. 환자, 자원, 문의 검토시간, 담당 인원, 배경 업무, 무응답은 합성 입력이다.',
    '정책은 이미 받은 응답·타임아웃과 자신이 보낸 문의만 관측한다. 실제 병원 대기열·처리 인원·미래 응답·향후 환자·외생 사건은 입력으로 제공하지 않는다.',
    '관측 응답의 지연은 혼잡, 무응답, 수용 불가의 원인을 완전히 구별하지 못한다. 타임아웃 뒤 늦은 회신은 배정에 사용하지 않는다.',
    '개발에서 최선 고정 정책과 최선 관측형 정책을 각각 잠갔다. 검증에서 다른 정책의 결과가 더 좋아도 재선택하지 않는다.',
    '개발·검증 seed와 합성 서비스·발생 간격·배경 업무를 분리했다. 기관과 현장 위치 및 네 상황 계열은 공유하므로 새로운 지역·임상 환경에서의 효과를 증명하지 않는다.',
    '이전 실험의 검증 seed 1001~1020은 이번 개발·검증에서 제외했다. 새 검증 결과를 본 뒤 정책을 수정하면 다시 미사용 입력을 확보해야 한다.',
    '개발 점수의 문의 1건당 0.25분 비용은 실험용 절충값이며 임상 기준이나 실제 의료진 업무 측정값이 아니다.',
    '시간 평균은 해당 사건이 발생한 환자의 조건부 값이다. 미도착·치료 미시작 수와 공통 환자 분모를 함께 확인해야 한다.',
    '95% 부트스트랩 구간은 합성 seed 변동만 나타낸다. 설정의 정확성이나 실제 임상 효과에 대한 구간이 아니다.',
    '치료 시작·완료는 합성 자원의 점유 시작·종료이며, 생존·회복·예후를 뜻하지 않는다. 자원 자체가 부족하면 문의 정책으로 병상을 만들 수 없다.',
    '기본 이동시간은 직선거리 추정이다. 도로 모드도 일반 자동차 예상시간이며 구급차 우선주행·실시간 교통은 반영하지 않는다.',
    '설명용 지도 두 건은 결과와 무관하게 첫 검증 seed·무응답 0·충분 자원·담당 1명/2명으로 미리 정했다. 유리한 seed를 고른 사례가 아니다.',
]


def build_adaptive_scenarios(base_scenario, split='development', nonresponse_levels=(0, .2)):
    """새 합성 입력 4계열 × 무응답 조건. 정책 결과를 보지 않고 생성한다."""
    levels = list(nonresponse_levels)
    if not levels or len(set(levels)) != len(levels):
        raise ValueError('무응답 조건은 하나 이상이고 중복이 없어야 합니다.')
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 1 for x in levels):
        raise ValueError('무응답 확률은 0~1이어야 합니다.')
    originals = [s for s in build_workload_scenarios(base_scenario, split)
                 if s['family'] == 'inquiry_workload']
    result = []
    for source in originals:
        source = copy.deepcopy(source)
        source['timeout_minutes'] = 14 if split == 'development' else 17
        # Preserve each patient's dispatch/scene-care offsets, changing only the
        # stream spacing. These settings are fixed before either policy is run.
        first = source['patients'][0]
        phase = {key: first[key] for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t')}
        for i, patient in enumerate(source['patients']):
            offset = i // 3 if split == 'development' else i // 2
            for key, value in phase.items():
                patient[key] = value + offset
            for j, hospital in enumerate(source['hospitals']):
                source['response_environment'][patient['id']][hospital['id']] = dict(
                    delay=1 + ((i + 2*j) % 4 if split == 'development' else (2*i + j) % 5),
                    silent=False)
        start = min(p['search_start_t'] for p in source['patients'])
        source['inquiry_queue']['background_jobs'] = [dict(
            t=max(0, start - 2 + wave * (5 if split == 'development' else 6)),
            hospital=h['id'], service_minutes=1 + (i + wave) % 4 + (split == 'validation'))
            for wave in range(3) for i, h in enumerate(source['hospitals'])]
        source['experiment'].update(
            iteration='adaptive_inquiry_v1', generation_selected_from_outcomes=False,
            response_delay_interpretation='synthetic_review_service_duration',
            inquiry_background='three_fixed_jobs_per_hospital_new_times_and_durations',
            previous_validation_seeds_excluded=sorted(PREVIOUS_VALIDATION_SEEDS))
        source['assumptions'].append(
            '이번 입력은 이전 업무량 실험과 발생 간격·검토 서비스 분포·배경 업무·제한시간이 다르다. '
            '문의 처리 인원은 실험 제어변수이며 알고리즘에 공개하지 않는다.')
        for probability in levels:
            value = copy.deepcopy(source)
            suffix = f'nr{round(probability * 1000):03d}'
            value['id'] = f"adaptive_{source['id']}_{suffix}"
            value['label'] = f"응답 관측 검증 · {source['label']} · 무응답 {probability:.0%}"
            value['experiment_nonresponse'] = probability
            value['experiment']['nonresponse_probability'] = probability
            value['experiment']['new_input_settings'] = ['patient_spacing', 'service_durations',
                                                        'background_jobs', 'response_timeout']
            result.append(value)
    if len({s['id'] for s in result}) != len(result):
        raise ValueError('무응답 조건의 ID가 충돌했습니다. 확률 간격을 넓혀 주세요.')
    return result


def validate_seed_split(development_seeds, validation_seeds):
    for values in (development_seeds, validation_seeds):
        if not values or len(set(values)) != len(values):
            raise ValueError('각 seed 집합은 하나 이상이며 중복이 없어야 합니다.')
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in values):
            raise ValueError('seed는 0 이상의 정수여야 합니다.')
    if set(development_seeds) & set(validation_seeds):
        raise ValueError('개발 seed와 검증 seed는 겹칠 수 없습니다.')
    if (set(development_seeds) | set(validation_seeds)) & PREVIOUS_VALIDATION_SEEDS:
        raise ValueError('이전 검증 seed 1001~1020은 이번 정책 선택·검증에 사용할 수 없습니다.')


def lock_development_policies(records):
    """두 정책군 각각의 개발 승자만 선택한다. 검증 기록은 거부한다."""
    # Also compare coverage keys across the two families, rather than allowing
    # every adaptive candidate to be missing the same difficult development case.
    select_development_policy(records, DEVELOPMENT_POLICIES)
    fixed = select_development_policy(records, FIXED_POLICIES)
    adaptive = select_development_policy(records, ADAPTIVE_POLICIES)
    return dict(fixed=fixed, adaptive=adaptive,
        selected_fixed_policy=copy.deepcopy(fixed['selected_policy']),
        selected_adaptive_policy=copy.deepcopy(adaptive['selected_policy']),
        selection_split='development', validation_retuning=False,
        objective_order=fixed['objective_order'], inquiry_penalty_minutes=.25,
        objective_is_clinically_validated=False)


def validation_policies(locked):
    baseline = locked['selected_fixed_policy']
    return [copy.deepcopy(baseline), copy.deepcopy(locked['selected_adaptive_policy'])] + [
        copy.deepcopy(p) for p in FIXED_POLICIES if p['id'] != baseline['id']]


def _seeded_generator(generator):
    def wrapped(value, policies, routing):
        value['experiment_environment'].update(kind='adaptive_inquiry_validation',
            prior_validation_seed_reused=False, policy_has_true_queue_state=False,
            service_lognormal_sigma=value['experiment_environment'].get('sigma'))
        return generator(value, policies=policies, routing=routing)
    return wrapped


def _run_conditions(scenarios, policies, seeds, split, routing, sigma, generator,
                    bootstrap_samples, progress):
    records = []
    observations = {}
    real_generator = _seeded_generator(generator)
    def collecting(value, policies, routing):
        replay = real_generator(value, policies, routing)
        for branch in replay['branches']:
            observations[(value['id'], value['seed'], branch['id'])] = {
                key: val for key, val in branch.get('metrics', {}).items()
                if key.startswith('adaptive_')}
        return replay
    probabilities = sorted({s.get('experiment_nonresponse', 0) for s in scenarios})
    for probability in probabilities:
        group = [s for s in scenarios if s.get('experiment_nonresponse', 0) == probability]
        suite = run_suite(group, policies, seeds, split, f'adaptive_{split}', routing=routing,
            nonresponse=probability, sigma=sigma, generator=collecting,
            bootstrap_samples=bootstrap_samples, progress=progress)
        for row in suite['records']:
            row['nonresponse_probability'] = probability
            row['metrics'].update(observations[(row['scenario_id'], row['seed'], row['policy_id'])])
        records.extend(suite['records'])
    return dict(records=records, baseline_id=policies[0]['id'],
        summaries=summarize_workload(records, policies[0]['id'], bootstrap_samples))


def select_replay_cases(records, locked):
    """Predeclared controls, not largest improvements or a held-out policy winner."""
    if not records:
        return []
    first_seed = min(r['seed'] for r in records)
    baseline_id = locked['selected_fixed_policy']['id']
    adaptive_id = locked['selected_adaptive_policy']['id']
    picks = []
    for kind in ('work_sufficient_w1', 'work_sufficient_w2'):
        before = next((r for r in records if r.get('case_kind') == kind
                       and r['seed'] == first_seed and r.get('nonresponse_probability', 0) == 0
                       and r['policy_id'] == baseline_id), None)
        if before is None:
            continue
        after = next(r for r in records if r['scenario_id'] == before['scenario_id']
                     and r['seed'] == first_seed and r['policy_id'] == adaptive_id)
        shared = shared_patient_time_difference(before, after, 'treatment_start_t')
        picks.append(dict(scenario_id=before['scenario_id'], seed=first_seed,
            baseline_id=baseline_id, policy_id=adaptive_id, metric='treatment_start_t',
            difference=shared['mean_difference'], common_patients=shared['common_patients'],
            selection='validation_predeclared_worker_control', outcome_independent_selection=True,
            selection_rule='first_validation_seed_nonresponse_0_sufficient_workers_1_and_2'))
    return picks


def run_experiment(development_scenarios, validation_scenarios,
                   development_seeds=tuple(range(101, 113)), validation_seeds=tuple(range(2001, 2021)),
                   routing=None, sigma=.35, generator=generate_replay,
                   bootstrap_samples=1000, progress=None):
    validate_seed_split(development_seeds, validation_seeds)
    if not development_scenarios or not validation_scenarios:
        raise ValueError('개발·검증 시나리오가 각각 하나 이상 필요합니다.')
    dev_ids, val_ids = {s['id'] for s in development_scenarios}, {s['id'] for s in validation_scenarios}
    if dev_ids & val_ids:
        raise ValueError('개발·검증 시나리오 ID가 겹칩니다.')
    if len(dev_ids) != len(development_scenarios) or len(val_ids) != len(validation_scenarios):
        raise ValueError('같은 집합 안에서 시나리오 ID가 중복됐습니다.')
    for split, scenarios in (('development', development_scenarios), ('validation', validation_scenarios)):
        if any(s.get('split', split) != split for s in scenarios):
            raise ValueError('시나리오의 split과 실행 집합이 다릅니다.')
    options = dict(routing=routing, sigma=sigma, generator=generator,
                   bootstrap_samples=bootstrap_samples, progress=progress)
    development = _run_conditions(development_scenarios, DEVELOPMENT_POLICIES, development_seeds,
                                   'development', **options)
    locked = lock_development_policies(development['records'])
    # No held-out generator call occurs until both policy configurations freeze.
    validation = _run_conditions(validation_scenarios, validation_policies(locked), validation_seeds,
                                  'validation', **options)
    records = development['records'] + validation['records']
    return dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
        experiment='adaptive_inquiry_v1', development_seeds=list(development_seeds),
        validation_seeds=list(validation_seeds), previous_validation_seeds_excluded=sorted(PREVIOUS_VALIDATION_SEEDS),
        service_lognormal_sigma=sigma, tuning=locked,
        suites=dict(development=development, validation=validation),
        replay_cases=select_replay_cases(validation['records'], locked),
        branch_runs=len(records), patient_runs=sum(r['metrics']['patients'] for r in records),
        validation_scope='new_seeds_and_parameters_within_shared_synthetic_locations_and_families',
        actual_system_comparison=False, limitations=LIMITATIONS)


def export_replay_cases(payload, scenarios):
    by_id, exported = {s['id']: s for s in scenarios}, []
    for selected in payload['replay_cases']:
        source = by_id[selected['scenario_id']]
        value = seed_workload_scenario(source, selected['seed'],
            nonresponse=source.get('experiment_nonresponse', 0), sigma=payload['service_lognormal_sigma'])
        value['experiment_environment'].update(kind='adaptive_inquiry_validation',
            prior_validation_seed_reused=False, policy_has_true_queue_state=False)
        value['id'] = f"{source['id']}_seed{selected['seed']}"
        value['label'] += f" · 고정/관측형 대조 · seed {selected['seed']}"
        value['policies'] = validation_policies(payload['tuning'])
        value['experiment_selection'] = copy.deepcopy(selected)
        value['description'] = value.get('description', '') + (
            ' 첫 검증 seed의 담당 1명/2명 입력을 결과와 무관하게 고정했다. '
            '개발에서 고른 최선 고정 문의와 관측 응답 기반 문의를 비교하며, '
            '검증에서 이긴 정책이나 유리한 seed를 다시 고른 예시가 아니다.')
        if selected['difference'] is not None:
            value['description'] += f" 이 예시의 관측형−고정 기준 치료 시작 차이는 {selected['difference']:+.2f}분이다."
            records = payload.get('suites', {}).get('validation', {}).get('records', [])
            row = next((r for r in records
                        if r['scenario_id'] == source['id'] and r['seed'] == selected['seed']
                        and r['policy_id'] == selected['policy_id']), None)
            if (row is not None and row['metrics'].get('adaptive_max_limit') == 1
                    and payload['tuning']['selected_fixed_policy']['k'] == 1):
                value['description'] += ' 이번 고정 예시에서 관측형은 계속 1곳을 선택해 고정 1곳과 같은 흐름을 보인다.'
        exported.append(dict(scenario=value, **selected))
    return dict(schema_version=2, geometry_included=False, scenarios=exported)


def _fmt(value):
    return '—' if value is None else f'{value:.2f}'


def _interval(value):
    return '추정 불가' if value is None else f'[{value[0]:.2f}, {value[1]:.2f}]'


def report_markdown(payload):
    tuning = payload['tuning']
    baseline, adaptive = tuning['selected_fixed_policy'], tuning['selected_adaptive_policy']
    lines = ['# 관측 응답 기반 문의 규모 조절 검증', '',
        f"개발 {len(payload['development_seeds'])} seed / 신규 검증 {len(payload['validation_seeds'])} seed · {payload['branch_runs']}개 분기 · {payload['patient_runs']}회 환자 실행", '',
        '**합성 모형의 비교 결과다. 실제 119 체계 대비 개선율이나 알고리즘의 최적성 증명이 아니다.**', '',
        '## 사전 고정한 비교 설계', '',
        '이전 검증에서 담당 인원에 따라 유리한 고정 문의 규모가 달랐다. 이번에는 고정 1/3/5곳 전체와 관측형 후보 4개를 개발에서 비교했다. 담당 인원·진짜 대기열·미래 응답을 정책에 주지 않고, 도착한 응답과 타임아웃·이미 보낸 문의만 사용한다. R1 배정·추가 대기 0분·변경 알림 활성·이동시간순 후보는 모두 동일하다.', '',
        '관측형 후보는 초기 문의 1/3곳 × 지연 판단 3/6분의 네 조합이다. 최근 20분, 최소 관측 3건, 타임아웃 비율 0.5, 느린 경우 1곳/빠른 경우 3곳이라는 나머지 설정은 고정했다. 완료 관측 3건 이상에서 타임아웃 비율이 50% 이상이고, 실제 응답이 최소 1건 있으며 그 중앙값이 지연 기준보다 짧을 때에는 최대 5곳까지 늘린다. 모두 타임아웃이면 혼잡과 무응답을 구별할 수 없으므로 이 규칙으로 무조건 확대하지 않는다. 이러한 임계값은 임상 기준이 아니라 개발용 정책 후보다.', '',
        '선택은 ① 치료 미시작 합계 ② 최종 미도착 합계 ③ 치료 시작 조건부 평균 + 0.25 × 문의/환자의 사전 순서다. 문의 1건의 비용 0.25분은 실험용이다. 고정 정책군과 관측형 정책군에서 각각 하나를 선택하고 잠근 뒤 검증을 실행했다.', '',
        f"**잠근 기준: {baseline['label']} (`{baseline['id']}`)**  ",
        f"**잠근 관측형: {adaptive['label']} (`{adaptive['id']}`)**  ",
        '검증에서 재선택하지 않았다. 검증 표에는 고정 1/3/5곳을 모두 남겨 기준 정책만 고른 비교를 피했다.', '',
        '개발 seed는 ' + ', '.join(map(str, payload['development_seeds'])) + '.  ',
        '검증 seed는 ' + ', '.join(map(str, payload['validation_seeds'])) + '.  ',
        '이전 검증 seed 1001~1020은 재사용하지 않았다. 새 입력은 환자 발생 간격, 문의 검토시간, 배경 업무, 타임아웃이 이전 실험과 다르다. 무응답 0%/20%를 충분 자원 담당 1명/2명·희소 조건·총자원 부족에 교차한다.', '',
        '| 정책군 | 개발 후보 | 치료 미시작 합계 | 미도착 합계 | 치료 시작 평균 | 문의/환자 | 결합 점수 |',
        '|---|---|---:|---:|---:|---:|---:|']
    for group, name in (('fixed', '고정'), ('adaptive', '관측형')):
        for score in tuning[group]['scores']:
            lines.append(f"| {name} | {score['policy_id']} | {score['not_treated']} | {score['not_arrived']} | {_fmt(score['conditional_start_mean'])} | {_fmt(score['inquiries_per_patient'])} | {_fmt(score['combined_minutes'])} |")
    lines += ['', '## 미사용 입력 검증', '',
        '각 표는 seed별 지표 평균이다. 시간 원점은 현장처치 후 병원 탐색 시작이며 단위는 분이다. 미도착·치료 미시작과 분모를 먼저 확인한다. 치료 시작 차이는 양쪽 정책에서 치료를 시작한 같은 환자만 짝지어 계산한다. 차이는 정책 − 잠근 고정 기준이며 음수 시간이 단축이다.', '']
    for case in payload['suites']['validation']['summaries']:
        lines += [f"### {case['label']}", '',
            '| 정책 | 미도착 | 치료 미시작 | 치료 시작 관측/전체 | 치료 시작 평균 | 문의/환자 | 문의 대기 평균 | 최대 대기 업무 |',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
        for policy in case['policies']:
            aggregate, denominator = policy['aggregate'], policy['denominators']
            lines.append(f"| {policy['label']} | {_fmt(aggregate['not_arrived']['mean'])} | {_fmt(aggregate['not_treated']['mean'])} | {denominator['start_observed']}/{denominator['patients']} | {_fmt(aggregate['start_mean_minutes']['mean'])} | {_fmt(aggregate['inquiries_per_patient']['mean'])} | {_fmt(aggregate['queue_wait_mean']['mean'])} | {_fmt(aggregate['inquiry_peak_waiting']['mean'])} |")
        lines += ['', '| 정책 | 공통 환자 치료 시작 차이 | 95% 구간 | 공통 환자/전체 | 문의 차이/환자 | 시간 악화 seed 비율 |',
            '|---|---:|---|---:|---:|---:|']
        for policy in case['policies']:
            shared = policy['shared_patient_times']['treatment_start_t']
            values = [item['mean_difference'] for item in shared['seed_details'] if item['mean_difference'] is not None]
            regression = sum(v > 0 for v in values) / len(values) if values else None
            lines.append(f"| {policy['label']} | {_fmt(shared['mean_difference'])} | {_interval(shared['interval_95'])} | {shared['common_patient_total']}/{policy['denominators']['patients']} | {_fmt(policy['paired_vs_baseline']['inquiries_per_patient']['mean_difference'])} | {'—' if regression is None else f'{regression:.0%}'} |")
        lines += ['']
    lines += ['## 검증 전체 요약', '',
        '아래 결합 점수는 해석용이며 검증 정책 선택에 쓰지 않는다. 상황별 개선·악화와 수용 범위 변화는 위 표와 JSON을 함께 봐야 한다.', '',
        '| 정책 | 치료 미시작 합계 | 미도착 합계 | 치료 시작 평균 | 문의/환자 | 결합 점수 |',
        '|---|---:|---:|---:|---:|---:|']
    validation = payload['suites']['validation']['records']
    for policy in validation_policies(tuning):
        score = score_policy([r for r in validation if r['policy_id'] == policy['id']])
        lines.append(f"| {policy['label']} | {score['not_treated']} | {score['not_arrived']} | {_fmt(score['conditional_start_mean'])} | {_fmt(score['inquiries_per_patient'])} | {_fmt(score['combined_minutes'])} |")
    lines += ['', '## 고정한 지도 재생 입력', '']
    for selected in payload['replay_cases']:
        lines.append(f"- {selected['scenario_id']} / seed {selected['seed']} / 공통 환자 치료 시작 차이 {_fmt(selected['difference'])}분 / 공통 환자 {selected['common_patients']}명")
    lines += ['', '개선 여부와 무관하게 첫 검증 seed의 충분 자원·무응답 0·담당 1명/2명 두 입력을 내보낸다. 다른 조건에서 악화한 결과도 삭제하지 않는다.', '', '## 해석의 한계', '']
    lines += [f'- {item}' for item in payload['limitations']]
    lines += ['', '정책 수정은 다음 개발 단계에서 수행하고, 수정 후에는 이번 검증을 다시 미사용 검증이라고 부르지 않는다.', '']
    # Describe the frozen validation. These diagnostics cannot select a policy
    # or replace the predeclared first-seed replay controls.
    before = score_policy([r for r in validation if r['policy_id'] == baseline['id']])
    after_rows = [r for r in validation if r['policy_id'] == adaptive['id']]
    after = score_policy(after_rows)
    widened = sum((r['metrics'].get('adaptive_max_limit') or 1) > 1 for r in after_rows)
    comparisons = [p['shared_patient_times']['treatment_start_t']
                   for c in payload['suites']['validation']['summaries']
                   for p in c['policies'] if p['id'] == adaptive['id']]
    changed = [c for c in comparisons if c['mean_difference'] not in (None, 0)]
    improved = sum(c['interval_95'] is not None and c['interval_95'][1] < 0 for c in changed)
    diagnosis = (f'시간 차이가 나타난 조건은 {len(changed)}개이며 95% 구간 전체가 단축 방향인 조건은 {improved}개다. '
                 + ('**이 버전으로 안정적인 개선을 입증하지 못했다.**' if not improved
                    else '일부 조건의 단축이 관측됐으나 전체 조건의 우수성으로 확대하지 않는다.'))
    lines[6:6] = ['## 이번 검증에서 확인된 점', '',
        f"개발에서 선택한 고정 기준의 치료 시작 평균 {_fmt(before['conditional_start_mean'])}분, 관측형 {_fmt(after['conditional_start_mean'])}분이다. 문의/환자는 {_fmt(before['inquiries_per_patient'])}→{_fmt(after['inquiries_per_patient'])}건이며 치료 미시작은 {before['not_treated']}→{after['not_treated']}명이다. 시간은 치료 시작이 관측된 환자에 한정한 값이다.", '',
        f"관측형 {len(after_rows)}개 검증 분기 중 1곳보다 큰 한도를 사용한 분기는 {widened}개다. {diagnosis}", '',
        '전역 회신 중앙값은 병원별 서비스 소요와 혼잡을 구별하지 못하고, 문의 초기에는 관측 이력이 부족하다. 다음 개발에서는 병원별 관측·진행 중 문의의 경과시간과 시간에 따라 변하는 부하를 분리해 검증할 필요가 있다. 이번 검증 seed와 입력은 이후 미사용 검증으로 재사용하지 않는다.', '']
    return '\n'.join(lines)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + '.tmp')
    staged.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    staged.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-seeds', type=int, default=12)
    parser.add_argument('--validation-seeds', type=int, default=20)
    parser.add_argument('--development-seed-start', type=int, default=DEFAULT_DEVELOPMENT_START)
    parser.add_argument('--validation-seed-start', type=int, default=DEFAULT_VALIDATION_START)
    parser.add_argument('--service-sigma', type=float, default=.35)
    parser.add_argument('--bootstrap-samples', type=int, default=1000)
    parser.add_argument('--routing', choices=('synthetic', 'osrm'), default='synthetic')
    parser.add_argument('--offline', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--route-cache', type=Path, default=ROOT / 'simulation/route_cache')
    parser.add_argument('--output', type=Path, default=ROOT / 'simulation_results/adaptive_inquiry')
    parser.add_argument('--replay-output', type=Path, default=ROOT / 'simulation_results/adaptive_inquiry_replays.json')
    args = parser.parse_args(argv)
    if not 1 <= args.development_seeds <= 1000 or not 1 <= args.validation_seeds <= 1000:
        parser.error('각 seed 수는 1~1000이어야 합니다.')
    if args.service_sigma < 0 or not math.isfinite(args.service_sigma):
        parser.error('--service-sigma는 유한한 0 이상이어야 합니다.')
    if not 100 <= args.bootstrap_samples <= 10000:
        parser.error('--bootstrap-samples는 100~10000이어야 합니다.')
    dev_seeds = list(range(args.development_seed_start, args.development_seed_start + args.development_seeds))
    val_seeds = list(range(args.validation_seed_start, args.validation_seed_start + args.validation_seeds))
    try:
        validate_seed_split(dev_seeds, val_seeds)
    except ValueError as error:
        parser.error(str(error))
    routing = RouteSnapshot(RoadRouteProvider(cache_dir=args.route_cache, offline=args.offline,
        allow_synthetic_fallback=False) if args.routing == 'osrm' else None)
    try:
        base = build_default_scenarios(routing=routing)[0]
        development, validation = (build_adaptive_scenarios(base, split) for split in ('development', 'validation'))
        def progress(suite, sid, seed):
            print(f'계산: {suite} / {sid} / seed {seed}', flush=True)
        payload = run_experiment(development, validation, dev_seeds, val_seeds, routing=routing,
            sigma=args.service_sigma, bootstrap_samples=args.bootstrap_samples, progress=progress)
        payload['routing'] = routing.summary()
        payload['scenario_specs'] = dict(development=development, validation=validation)
        _write_json(args.output.with_suffix('.json'), payload)
        args.output.with_suffix('.md').write_text(report_markdown(payload), encoding='utf-8')
        _write_json(args.replay_output, export_replay_cases(payload, validation))
    except RoadRouteError as error:
        parser.exit(1, f'도로 캐시가 부족합니다: {error}\n외부 조회 없이 종료했으며 기존 결과는 유지됩니다.\n')
    print(f"고정 기준: {payload['tuning']['selected_fixed_policy']['id']} / 관측형: {payload['tuning']['selected_adaptive_policy']['id']} / {payload['branch_runs']}분기", flush=True)
    print(f'결과: {args.output.with_suffix(".json")}\n보고서: {args.output.with_suffix(".md")}')


if __name__ == '__main__':
    main()
