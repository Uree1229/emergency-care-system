"""병원별 회신 관측·진행 문의 경과시간 정책의 개발 및 신규 입력 검증.

python -m simulation.observed_experiment --development-only
python -m simulation.observed_experiment

기존 결과를 덮어쓰지 않는다. 고정 정책과 V2 정책을 개발에서 각각 고른 뒤
신규 검증으로 넘어가며, V1은 이전 개발에서 고른 설정을 그대로 비교한다.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from .map_replay import ROOT, RouteSnapshot, build_default_scenarios, generate_replay
from .observed_scenarios import build_observed_scenarios
from .replay_experiment import shared_patient_time_difference
from .road_routes import RoadRouteError, RoadRouteProvider
from .sim import Policy
from .workload_experiment import (run_suite, score_policy, seed_workload_scenario,
                                  select_development_policy, summarize_workload)


FIXED_POLICIES = tuple(dict(id=f'observed_fixed_k{k}', label=f'고정 {k}곳 문의',
    rule='R1', k=k, wait=0, notify=True, inquiry_ranking='travel') for k in (1, 3, 5))
V1_REFERENCE = dict(id='locked_v1_i1s3', label='V1 고정 설정 · 초기 1곳 · 지연 3분',
    rule='R1', k=5, wait=0, notify=True, inquiry_ranking='travel',
    adaptive_inquiry=dict(initial_k=1, min_k=1, fast_k=3, window=20,
        min_samples=3, slow_minutes=3, timeout_fraction=.5))
OBSERVED_POLICIES = tuple(dict(
    id=f'observed_v2_w{weight}_h{hedge}', label=f'병원별 관측 V2 · 혼잡 {weight} · 추가 문의 {hedge}분',
    rule='R1', k=3, wait=0, notify=True, inquiry_ranking='travel',
    observed_inquiry=dict(window=30, prior_reply=3, min_samples=2,
        congestion_weight=weight, hedge_after=hedge, hedge_margin=0,
        max_new_per_review=1, timeout_penalty=6))
    for weight in (0, 1) for hedge in (2, 4))
DEVELOPMENT_POLICIES = FIXED_POLICIES + (V1_REFERENCE,) + OBSERVED_POLICIES
PREVIOUS_VALIDATION_SEEDS = frozenset(range(1001, 1021)) | frozenset(range(2001, 2021))
DEFAULT_DEVELOPMENT_START = 201
DEFAULT_VALIDATION_START = 3001
REPLAY_FAMILIES = ('burst_w1', 'streaming_w2', 'shifting_background_w1')

LIMITATIONS = [
    '실제 119·광역상황실 구현의 복제가 아니라 합성 모형 내 정책 비교다. 현행 체계 대비 개선율이나 최적성 증명이 아니다.',
    '기관 식별자·이름·좌표만 공개 자료다. 모든 환자·치료조건·자원·문의 검토시간·인원·배경 업무·무응답은 합성 입력이다.',
    '정책은 도착한 회신·타임아웃과 자신이 보낸 문의의 경과시간만 사용한다. 실제 인원·대기열·잠재 서비스시간·향후 환자·외생 일정은 제공하지 않는다.',
    '회신 지연만으로 혼잡과 무응답의 원인을 완전히 구별할 수 없다. 과거 회신은 향후 수용을 보장하지 않으며 실제 병원의 명시적 수용 회신이 필요하다.',
    '개발에서 최선 고정 정책과 최선 V2 정책을 따로 잠갔다. V1 설정은 이전 개발에서 선택한 그대로이며 신규 검증에서 재선택하지 않는다.',
    '이전 검증 seed 1001~1020·2001~2020을 재사용하지 않았다. 새 seed·입력 설정도 같은 위치와 합성 가정 계열을 공유하므로 임상·지역 일반화를 증명하지 않는다.',
    '개발 점수는 치료 미시작, 미도착, 치료 시작 평균+0.25×문의/환자 순이다. 0.25분 비용과 우선순위는 실험값이며 임상 기준이 아니다.',
    '일반 계열은 치료 용량을 충분히 두어 문의 부하를 분리했다. 실제 병원의 자원 경쟁을 재현하지 않으며 총자원 부족은 별도 대조로 남긴다.',
    '시간 평균은 해당 사건이 관측된 환자의 조건부 값이다. 미도착·치료 미시작과 공통 환자 분모를 먼저 봐야 한다.',
    '95% 부트스트랩 구간은 합성 seed 변동만 나타낸다. 설정의 타당성이나 실제 임상 효과의 불확실성을 포함하지 않는다.',
    '치료 시작·완료는 자원 점유 시작·종료이며 생존·회복·예후를 뜻하지 않는다. 문의 정책은 부족한 병상을 새로 만들지 못한다.',
    '기본 이동은 직선거리 추정이다. 도로 모드도 일반 자동차 예상시간이며 구급차 우선주행·실시간 교통을 반영하지 않는다.',
    '지도 예시는 첫 검증 seed·무응답 0의 동시1명/시간차2명/배경변화1명 세 입력으로 미리 고정했다. 유리한 결과를 골라 대표 효과로 제시하지 않는다.',
]


def validate_seed_split(development_seeds, validation_seeds):
    for values in (development_seeds, validation_seeds):
        if not values or len(set(values)) != len(values):
            raise ValueError('각 seed 집합은 하나 이상이며 중복이 없어야 합니다.')
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in values):
            raise ValueError('seed는 0 이상의 정수여야 합니다.')
    if set(development_seeds) & set(validation_seeds):
        raise ValueError('개발 seed와 검증 seed는 겹칠 수 없습니다.')
    if (set(development_seeds) | set(validation_seeds)) & PREVIOUS_VALIDATION_SEEDS:
        raise ValueError('이전 검증 seed 1001~1020·2001~2020은 재사용할 수 없습니다.')


def lock_development_policies(records):
    # The all-candidate check prevents every V2 candidate from omitting the same
    # difficult family while still passing a within-family coverage check.
    select_development_policy(records, DEVELOPMENT_POLICIES)
    fixed = select_development_policy(records, FIXED_POLICIES)
    observed = select_development_policy(records, OBSERVED_POLICIES)
    return dict(fixed=fixed, observed=observed,
        selected_fixed_policy=copy.deepcopy(fixed['selected_policy']),
        selected_observed_policy=copy.deepcopy(observed['selected_policy']),
        v1_reference=copy.deepcopy(V1_REFERENCE), selection_split='development',
        validation_retuning=False, objective_order=fixed['objective_order'],
        inquiry_penalty_minutes=.25, objective_is_clinically_validated=False)


def validation_policies(locked):
    baseline = locked['selected_fixed_policy']
    return [copy.deepcopy(baseline), copy.deepcopy(locked['selected_observed_policy']),
            copy.deepcopy(V1_REFERENCE)] + [copy.deepcopy(p) for p in FIXED_POLICIES
                                           if p['id'] != baseline['id']]


def _run_conditions(scenarios, policies, seeds, split, routing=None, sigma=.35,
                    generator=generate_replay, bootstrap_samples=1000, progress=None):
    records, observed_metrics = [], {}
    def collecting(value, policies, routing):
        value['experiment_environment'].update(kind='observed_inquiry_validation',
            prior_validation_seed_reused=False, policy_has_true_queue_state=False)
        replay = generator(value, policies=policies, routing=routing)
        for branch in replay['branches']:
            observed_metrics[(value['id'], value['seed'], branch['id'])] = {
                key: val for key, val in branch.get('metrics', {}).items()
                if key.startswith(('observed_', 'adaptive_'))}
        return replay
    for probability in sorted({s.get('experiment_nonresponse', 0) for s in scenarios}):
        group = [s for s in scenarios if s.get('experiment_nonresponse', 0) == probability]
        suite = run_suite(group, policies, seeds, split, f'observed_{split}', routing=routing,
            nonresponse=probability, sigma=sigma, generator=collecting,
            bootstrap_samples=bootstrap_samples, progress=progress)
        for row in suite['records']:
            row['nonresponse_probability'] = probability
            row['metrics'].update(observed_metrics[(row['scenario_id'], row['seed'], row['policy_id'])])
        records.extend(suite['records'])
    return dict(records=records, baseline_id=policies[0]['id'],
        summaries=summarize_workload(records, policies[0]['id'], bootstrap_samples))


def select_replay_cases(records, locked):
    if not records:
        return []
    first_seed = min(r['seed'] for r in records)
    baseline, selected = locked['selected_fixed_policy']['id'], locked['selected_observed_policy']['id']
    cases = []
    for family in REPLAY_FAMILIES:
        before = next((r for r in records if r.get('case_kind') == family and r['seed'] == first_seed
                       and r.get('nonresponse_probability', 0) == 0 and r['policy_id'] == baseline), None)
        if before is None:
            continue
        after = next(r for r in records if r['scenario_id'] == before['scenario_id']
                     and r['seed'] == first_seed and r['policy_id'] == selected)
        shared = shared_patient_time_difference(before, after, 'treatment_start_t')
        cases.append(dict(scenario_id=before['scenario_id'], seed=first_seed,
            baseline_id=baseline, policy_id=selected, metric='treatment_start_t',
            difference=shared['mean_difference'], common_patients=shared['common_patients'],
            selection='validation_predeclared_family_control', outcome_independent_selection=True,
            selection_rule='first_validation_seed_nonresponse_0_burst_w1_streaming_w2_shifting_background_w1'))
    return cases


def run_experiment(development_scenarios, validation_scenarios,
                   development_seeds=tuple(range(201, 209)), validation_seeds=tuple(range(3001, 3021)),
                   routing=None, sigma=.35, generator=generate_replay,
                   bootstrap_samples=1000, progress=None, development_only=False):
    validate_seed_split(development_seeds, validation_seeds)
    if not development_scenarios or not validation_scenarios:
        raise ValueError('개발·검증 시나리오가 각각 하나 이상 필요합니다.')
    dev_ids, val_ids = {s['id'] for s in development_scenarios}, {s['id'] for s in validation_scenarios}
    if dev_ids & val_ids or len(dev_ids) != len(development_scenarios) or len(val_ids) != len(validation_scenarios):
        raise ValueError('개발·검증 시나리오 ID는 중복될 수 없습니다.')
    for split, specs in (('development', development_scenarios), ('validation', validation_scenarios)):
        if any(s.get('split', split) != split for s in specs):
            raise ValueError('시나리오의 split과 실행 집합이 다릅니다.')
    options = dict(routing=routing, sigma=sigma, generator=generator,
                   bootstrap_samples=bootstrap_samples, progress=progress)
    development = _run_conditions(development_scenarios, DEVELOPMENT_POLICIES, development_seeds,
                                   'development', **options)
    locked = lock_development_policies(development['records'])
    # Both winners freeze before the first call to the held-out engine.
    validation = (dict(records=[], summaries=[], baseline_id=locked['selected_fixed_policy']['id'])
                  if development_only else _run_conditions(validation_scenarios,
                      validation_policies(locked), validation_seeds, 'validation', **options))
    records = development['records'] + validation['records']
    return dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
        experiment='observed_inquiry_v2', development_only=development_only,
        development_seeds=list(development_seeds), validation_seeds=list(validation_seeds),
        previous_validation_seeds_excluded=sorted(PREVIOUS_VALIDATION_SEEDS),
        service_lognormal_sigma=sigma, tuning=locked,
        suites=dict(development=development, validation=validation),
        replay_cases=select_replay_cases(validation['records'], locked),
        branch_runs=len(records), patient_runs=sum(r['metrics']['patients'] for r in records),
        validation_scope='new_seeds_new_stream_and_background_parameters_shared_synthetic_locations',
        actual_system_comparison=False, limitations=LIMITATIONS,
        scenario_specs=copy.deepcopy(dict(development=development_scenarios,
                                         validation=validation_scenarios)))


def _canonical_policy(spec):
    value = copy.deepcopy(spec)
    policy_id = value.pop('id')
    value.pop('label', None)
    return Policy(name=policy_id, **value).spec()


def validate_development_input(payload, development_scenarios, validation_scenarios,
                               development_seeds, validation_seeds, sigma):
    """저장된 개발 결과와 현재 계약을 검사한다. 정책을 다시 선택하지 않는다."""
    validate_seed_split(development_seeds, validation_seeds)
    if payload.get('experiment') != 'observed_inquiry_v2' or payload.get('schema_version') != 1:
        raise ValueError('병원별 관측 V2 개발 결과가 아닙니다.')
    if payload.get('development_only') is not True:
        raise ValueError('development-only 결과만 검증 재개에 사용할 수 있습니다.')
    suites = payload.get('suites', {})
    if 'validation' not in suites or suites['validation'].get('records') != [] \
            or suites['validation'].get('summaries') != [] or payload.get('replay_cases') != []:
        raise ValueError('이미 검증한 기록이나 지도 선정 결과가 있는 입력은 재개할 수 없습니다.')
    if payload.get('development_seeds') != list(development_seeds) \
            or payload.get('validation_seeds') != list(validation_seeds):
        raise ValueError('저장된 개발·검증 seed와 현재 실행 설정이 다릅니다.')
    if payload.get('previous_validation_seeds_excluded') != sorted(PREVIOUS_VALIDATION_SEEDS):
        raise ValueError('이전 검증 seed 제외 범위가 현재 계약과 다릅니다.')
    if payload.get('service_lognormal_sigma') != sigma:
        raise ValueError('저장된 서비스 분포 설정과 현재 실행 설정이 다릅니다.')
    if payload.get('scenario_specs') != dict(development=development_scenarios,
                                             validation=validation_scenarios):
        raise ValueError('저장된 개발·검증 시나리오와 현재 입력이 다릅니다.')
    by_scenario = {s['id']: s for s in development_scenarios}
    if len(by_scenario) != len(development_scenarios):
        raise ValueError('개발 시나리오 ID가 중복됐습니다.')
    candidates = {p['id']: p for p in DEVELOPMENT_POLICIES}
    expected_configs = {pid: _canonical_policy(p) for pid, p in candidates.items()}
    records = suites.get('development', {}).get('records')
    if not isinstance(records, list) or not records:
        raise ValueError('저장된 개발 실행 기록이 없습니다.')
    expected_keys = {(s['id'], seed, p['id']) for s in development_scenarios
                     for seed in development_seeds for p in DEVELOPMENT_POLICIES}
    observed_keys = set()
    for row in records:
        key = (row.get('scenario_id'), row.get('seed'), row.get('policy_id'))
        if key not in expected_keys or key in observed_keys or row.get('split') != 'development':
            raise ValueError('개발 상황·seed·정책 coverage가 현재 계약과 다르거나 중복됐습니다.')
        observed_keys.add(key)
        source = by_scenario[key[0]]
        if row.get('policy') != expected_configs[key[2]]:
            raise ValueError('개발 실행 기록의 후보 정책 설정이 현재 설정과 다릅니다.')
        if row.get('metrics', {}).get('patients') != len(source['patients']) \
                or row.get('case_kind') != source.get('case_kind') \
                or row.get('family') != source.get('family', source.get('experiment_family', 'unknown')) \
                or row.get('nonresponse_probability') != source.get('experiment_nonresponse', 0):
            raise ValueError('개발 실행 기록의 환자 수·상황 계열·무응답 조건이 다릅니다.')
    if observed_keys != expected_keys:
        raise ValueError('개발 후보 중 누락된 상황·seed 실행이 있습니다.')
    if payload.get('branch_runs') != len(records) \
            or payload.get('patient_runs') != sum(r['metrics']['patients'] for r in records):
        raise ValueError('개발 실행 수 요약과 실제 기록이 다릅니다.')
    locked = payload.get('tuning', {})
    if locked.get('selection_split') != 'development' or locked.get('validation_retuning') is not False \
            or locked.get('v1_reference') != V1_REFERENCE:
        raise ValueError('개발 선택 잠금이나 V1 고정 설정이 현재 계약과 다릅니다.')
    expected_objective = ['total_not_treated', 'total_not_arrived',
                          'conditional_start_mean + 0.25 * inquiries_per_patient']
    if locked.get('objective_order') != expected_objective or locked.get('inquiry_penalty_minutes') != .25:
        raise ValueError('저장된 개발 선택 목적함수가 현재 계약과 다릅니다.')
    for group, choices, selected_key in (('fixed', FIXED_POLICIES, 'selected_fixed_policy'),
                                         ('observed', OBSERVED_POLICIES, 'selected_observed_policy')):
        group_result = locked.get(group, {})
        frozen = locked.get(selected_key)
        if frozen not in choices or group_result.get('selected_policy') != frozen \
                or group_result.get('selection_split') != 'development' \
                or group_result.get('validation_retuning') is not False:
            raise ValueError('저장된 선택 정책과 후보 설정 또는 잠금 상태가 다릅니다.')
        scores = [dict(policy_id=p['id'], candidate_order=index,
                       **score_policy([r for r in records if r['policy_id'] == p['id']]))
                  for index, p in enumerate(choices)]
        if group_result.get('scores') != scores:
            raise ValueError('저장된 개발 점수와 실행 기록이 일치하지 않습니다.')
    return copy.deepcopy(locked)


def resume_validation(development_payload, development_scenarios, validation_scenarios,
                      development_seeds=tuple(range(201, 209)),
                      validation_seeds=tuple(range(3001, 3021)), routing=None, sigma=.35,
                      generator=generate_replay, bootstrap_samples=1000, progress=None):
    """이미 잠근 개발 결과로 검증만 실행한다. 개발 실행·선택 호출은 없다."""
    locked = validate_development_input(development_payload, development_scenarios,
        validation_scenarios, development_seeds, validation_seeds, sigma)
    validation = _run_conditions(validation_scenarios, validation_policies(locked), validation_seeds,
        'validation', routing=routing, sigma=sigma, generator=generator,
        bootstrap_samples=bootstrap_samples, progress=progress)
    payload = copy.deepcopy(development_payload)
    payload.update(generated_at=datetime.now(timezone.utc).isoformat(), development_only=False,
        development_reexecuted=False, development_reselected=False,
        resumed_from_development_generated_at=development_payload.get('generated_at'))
    payload['suites']['validation'] = validation
    payload['replay_cases'] = select_replay_cases(validation['records'], locked)
    records = payload['suites']['development']['records'] + validation['records']
    payload.update(branch_runs=len(records), patient_runs=sum(r['metrics']['patients'] for r in records))
    return payload


def export_replay_cases(payload, scenarios):
    by_id, exported = {s['id']: s for s in scenarios}, []
    for selected in payload['replay_cases']:
        source = by_id[selected['scenario_id']]
        value = seed_workload_scenario(source, selected['seed'],
            nonresponse=source.get('experiment_nonresponse', 0), sigma=payload['service_lognormal_sigma'])
        value['experiment_environment'].update(kind='observed_inquiry_validation',
            prior_validation_seed_reused=False, policy_has_true_queue_state=False)
        value['id'] += f"_seed{selected['seed']}"
        value['label'] += f" · 고정/V1/V2 비교 · seed {selected['seed']}"
        value['policies'] = validation_policies(payload['tuning'])
        value['experiment_selection'] = copy.deepcopy(selected)
        value['description'] = value.get('description', '') + (' 첫 검증 seed의 사전 지정 세 입력을 개선 여부와 무관하게 내보냈다. '
            '개발에서 잠근 최선 고정과 V2, 이전 V1 고정 설정을 비교한다. 유리한 검증 결과를 고른 예시가 아니다.')
        if selected['difference'] is not None:
            value['description'] += f" 이 입력의 V2−고정 기준 공통 환자 치료 시작 차이는 {selected['difference']:+.2f}분이다."
        exported.append(dict(scenario=value, **selected))
    return dict(schema_version=2, geometry_included=False, scenarios=exported)


def _fmt(value):
    return '—' if value is None else f'{value:.2f}'


def _interval(value):
    return '추정 불가' if value is None else f'[{value[0]:.2f}, {value[1]:.2f}]'


def report_markdown(payload):
    locked = payload['tuning']
    baseline, selected = locked['selected_fixed_policy'], locked['selected_observed_policy']
    validation_label = '미사용 검증 예정' if payload.get('development_only') else '신규 검증'
    lines = ['# 병원별 관측 응답·진행 문의 경과시간 정책 검증', '',
        f"개발 {len(payload['development_seeds'])} seed / {validation_label} {len(payload['validation_seeds'])} seed · {payload['branch_runs']}개 분기 · {payload['patient_runs']}회 환자 실행", '',
        '**합성 모형 내 비교다. 실제 119 체계 대비 개선율이나 최적성 증명이 아니다.**', '',
        '## 비교 설계와 잠근 정책', '',
        '고정 1/3/5곳, 이전에 잠근 V1 1개, 병원별 관측 V2 4개를 같은 개발 입력에서 비교한다. V2는 최근 병원별 회신 중앙값을 쓰며 관측 부족 시 전역 관측·사전값 순으로 대체한다. 자신이 보낸 미해결 문의 수와 타임아웃 이력으로 후보 비용을 평가한다. 진행 문의가 오래 걸리면 현재 경과시간과 대체 후보의 추정 비용을 비교해 제한적으로 추가 문의한다. 실제 병원 처리 인원·대기열·미래 응답은 보지 않는다.', '',
        'V2 개발 후보는 혼잡 가중치 0/1 × 추가 문의 검토 최소 경과 2/4분이다. 최근 30분, 사전 회신 3분, 최소 표본 2건, 최대 동시 3곳, 검토당 새 문의 최대 1곳, 타임아웃 비용 6분은 고정했다. 모두 실험 정책값이다.', '',
        '선택은 ① 치료 미시작 합계 ② 미도착 합계 ③ 치료 시작 조건부 평균 + 0.25 × 문의/환자 순이다. 각 정책은 동일한 상황·seed·환자 수를 가져야 한다. 고정 정책과 V2의 개발 승자를 각각 잠그고 검증에서 재선택하지 않았다. V1은 이전 설정 그대로다.', '',
        f"잠근 고정 기준: **{baseline['label']}** (`{baseline['id']}`)  ",
        f"잠근 V2: **{selected['label']}** (`{selected['id']}`)  ",
        f"V1 참조: `{V1_REFERENCE['id']}` · 초기 1곳, 지연 3분, 최근 20분, 최소 표본 3건", '',
        '8개 계열(동시 발생 1/2명, 시간차 발생 1/2명, 배경 업무 변화 1/2명, 희소 조건, 총자원 부족)에 무응답 0%/20%를 교차했다. 개발 12명·검증 16명이며 시간차 발생 간격, 검토 서비스시간, 배경 업무, 제한시간·관찰기간도 분리했다. 기관·위치는 공유한다.', '',
        '개발 seed: ' + ', '.join(map(str, payload['development_seeds'])) + '.  ',
        '신규 검증 seed: ' + ', '.join(map(str, payload['validation_seeds'])) + '.  ',
        '이전 검증 seed 1001~1020·2001~2020은 제외했다.', '',
        '| 정책군 | 개발 후보 | 치료 미시작 | 미도착 | 치료 시작 평균 | 문의/환자 | 결합 점수 |',
        '|---|---|---:|---:|---:|---:|---:|']
    for group, label in (('fixed', '고정'), ('observed', 'V2')):
        for score in locked[group]['scores']:
            lines.append(f"| {label} | {score['policy_id']} | {score['not_treated']} | {score['not_arrived']} | {_fmt(score['conditional_start_mean'])} | {_fmt(score['inquiries_per_patient'])} | {_fmt(score['combined_minutes'])} |")
    reference_rows = [r for r in payload['suites']['development']['records'] if r['policy_id'] == V1_REFERENCE['id']]
    ref = score_policy(reference_rows)
    lines.append(f"| 이전 V1 고정 | {V1_REFERENCE['id']} | {ref['not_treated']} | {ref['not_arrived']} | {_fmt(ref['conditional_start_mean'])} | {_fmt(ref['inquiries_per_patient'])} | {_fmt(ref['combined_minutes'])} |")
    if payload.get('development_only'):
        lines += ['', '**현재는 개발 실행만 완료했다. 신규 검증 입력은 아직 실행하지 않았다.**', '']
    else:
        rows = payload['suites']['validation']['records']
        before = score_policy([r for r in rows if r['policy_id'] == baseline['id']])
        after = score_policy([r for r in rows if r['policy_id'] == selected['id']])
        lines += ['', '## 신규 검증 요약', '',
            f"잠근 고정 기준 대비 탐색 시작→가상 치료 시작 조건부 평균은 {_fmt(before['conditional_start_mean'])}→{_fmt(after['conditional_start_mean'])}분, 문의는 {before['inquiries_per_patient']:.4f}→{after['inquiries_per_patient']:.4f}건/환자다. 문의의 점추정 차이는 작으며 문의 감소가 입증됐다고 해석하지 않는다. 두 정책 모두 {before['patients']}명 중 {before['start_observed']}명에서 치료 시작이 관측됐다.", '',
            '**전체 미시작 수가 같아도 우선순위별 결과는 다르다. 아래는 실험 우선순위이며 실제 임상 분류가 아니다.**', '',
            '| 실험 우선순위 | 잠근 고정 기준 치료 미시작 | V2 치료 미시작 |',
            '|---|---:|---:|']
        for priority in sorted({o['priority'] for r in rows for o in r['outcomes']}, reverse=True):
            counts = [sum(o['priority'] == priority and o.get('treatment_start_t') is None
                          for r in rows if r['policy_id'] == pid for o in r['outcomes'])
                      for pid in (baseline['id'], selected['id'])]
            lines.append(f'| {priority} | {counts[0]} | {counts[1]} |')
        lines += ['',
            '평균 시간 개선만으로 우선순위 보호나 최적성을 주장할 수 없다. 자원 부족 조건에서는 어떤 환자가 치료를 시작했는지도 달라질 수 있으므로, 전체 조건부 평균 차이와 공통 환자를 짝지은 차이를 구분한다. 세 지도 예시는 사전에 고정했으며 개선 여부와 무관하게 보존했다.', '',
            '전체 공통 환자·seed별 차이, 독립 부트스트랩 구간과 원본/지도 일치 감사는 [감사 결과](observed_inquiry_audit.json) 및 `python -m simulation.observed_audit`로 확인한다. 이 감사는 정책을 다시 선택하지 않는다.', '']
        lines += ['', '## 신규 입력 결과', '',
            '시간 원점은 병원 탐색 시작이고 단위는 분이다. 시간은 관측된 환자의 조건부 평균이다. 미도착·치료 미시작과 분모를 먼저 확인한다. 시간 차이는 양쪽에서 관측된 같은 환자를 짝지어 V2 등 각 정책 − 잠근 고정 기준으로 계산하며 음수가 단축이다. 개선·악화 결과를 모두 남긴다.', '']
        for case in payload['suites']['validation']['summaries']:
            lines += [f"### {case['label']}", '',
                '| 정책 | 미도착 | 치료 미시작 | 치료 시작 관측/전체 | 치료 시작 평균 | 문의/환자 | 문의 대기 평균 |',
                '|---|---:|---:|---:|---:|---:|---:|']
            for p in case['policies']:
                a, d = p['aggregate'], p['denominators']
                lines.append(f"| {p['label']} | {_fmt(a['not_arrived']['mean'])} | {_fmt(a['not_treated']['mean'])} | {d['start_observed']}/{d['patients']} | {_fmt(a['start_mean_minutes']['mean'])} | {_fmt(a['inquiries_per_patient']['mean'])} | {_fmt(a['queue_wait_mean']['mean'])} |")
            lines += ['', '| 정책 | 공통 환자 치료 시작 차이 | 95% 구간 | 공통 환자/전체 | 문의 차이/환자 | 시간 악화 seed 비율 |',
                '|---|---:|---|---:|---:|---:|']
            for p in case['policies']:
                shared = p['shared_patient_times']['treatment_start_t']
                diffs = [v['mean_difference'] for v in shared['seed_details'] if v['mean_difference'] is not None]
                regression = sum(v > 0 for v in diffs) / len(diffs) if diffs else None
                lines.append(f"| {p['label']} | {_fmt(shared['mean_difference'])} | {_interval(shared['interval_95'])} | {shared['common_patient_total']}/{p['denominators']['patients']} | {_fmt(p['paired_vs_baseline']['inquiries_per_patient']['mean_difference'])} | {'—' if regression is None else f'{regression:.0%}'} |")
            lines.append('')
        lines += ['## 검증 전체 요약', '',
            '전체 요약으로 검증 정책을 다시 선택하지 않는다. 총자원 부족과 상황별 개선·악화는 위의 표를 함께 확인한다.', '',
            '| 정책 | 치료 미시작 | 미도착 | 치료 시작 관측/전체 | 치료 시작 평균 | 문의/환자 | 결합 점수 |',
            '|---|---:|---:|---:|---:|---:|---:|']
        rows = payload['suites']['validation']['records']
        for p in validation_policies(locked):
            score = score_policy([r for r in rows if r['policy_id'] == p['id']])
            lines.append(f"| {p['label']} | {score['not_treated']} | {score['not_arrived']} | {score['start_observed']}/{score['patients']} | {_fmt(score['conditional_start_mean'])} | {_fmt(score['inquiries_per_patient'])} | {_fmt(score['combined_minutes'])} |")
        lines += ['', '## 관측 정책 동작 진단', '',
            '동작 지표는 실제 실험 엔진에서 내보낸 값만 집계한다. 동작 횟수가 많다는 사실만으로 효과가 있다고 판단하지 않는다.', '',
            '| 지표 | V2 집계 | 집계 방식 | 관측 분기 수 |', '|---|---:|---|---:|']
        v2_rows = [r for r in rows if r['policy_id'] == selected['id']]
        keys = sorted({key for r in v2_rows for key in r['metrics'] if key.startswith('observed_')})
        for key in keys:
            values = [r['metrics'][key] for r in v2_rows if isinstance(r['metrics'].get(key), (int, float))]
            maximum = 'max_' in key
            aggregate = max(values, default=None) if maximum else sum(values)
            lines.append(f"| {key} | {_fmt(aggregate)} | {'최댓값' if maximum else '합계'} | {len(values)} |")
        lines += ['', '## 사전 고정 지도 예시', '']
        for case in payload['replay_cases']:
            lines.append(f"- {case['scenario_id']} / seed {case['seed']} / 공통 환자 치료 시작 차이 {_fmt(case['difference'])}분 / 공통 환자 {case['common_patients']}명")
        lines += ['', '개선 여부와 무관하게 사전 지정 세 계열의 첫 검증 seed를 사용한다.', '']
    lines += ['## 한계', ''] + [f'- {item}' for item in payload['limitations']]
    lines += ['', '이번 검증을 본 뒤 정책을 바꾸면 해당 입력은 다음 단계의 미사용 검증으로 다시 부를 수 없다.', '']
    return '\n'.join(lines)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + '.tmp')
    staged.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    staged.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-seeds', type=int, default=8)
    parser.add_argument('--validation-seeds', type=int, default=20)
    parser.add_argument('--development-seed-start', type=int, default=DEFAULT_DEVELOPMENT_START)
    parser.add_argument('--validation-seed-start', type=int, default=DEFAULT_VALIDATION_START)
    parser.add_argument('--development-only', action='store_true')
    parser.add_argument('--development-input', type=Path,
        help='기존 development-only JSON의 선택을 유지하고 검증만 실행한다.')
    parser.add_argument('--service-sigma', type=float, default=.35)
    parser.add_argument('--bootstrap-samples', type=int, default=1000)
    parser.add_argument('--routing', choices=('synthetic', 'osrm'), default='synthetic')
    parser.add_argument('--offline', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--route-cache', type=Path, default=ROOT / 'simulation/route_cache')
    parser.add_argument('--output', type=Path, default=ROOT / 'simulation_results/observed_inquiry')
    parser.add_argument('--replay-output', type=Path, default=ROOT / 'simulation_results/observed_inquiry_replays.json')
    args = parser.parse_args(argv)
    if args.development_only and args.development_input is not None:
        parser.error('--development-only와 --development-input은 함께 사용할 수 없습니다.')
    if args.development_input is not None and args.development_input.resolve() in (
            args.output.with_suffix('.json').resolve(), args.output.with_suffix('.md').resolve(),
            args.replay_output.resolve()):
        parser.error('개발 입력 파일을 출력 파일로 덮어쓸 수 없습니다.')
    if not 1 <= args.development_seeds <= 1000 or not 1 <= args.validation_seeds <= 1000:
        parser.error('각 seed 수는 1~1000이어야 합니다.')
    if args.service_sigma < 0 or not math.isfinite(args.service_sigma):
        parser.error('--service-sigma는 0 이상의 유한한 수여야 합니다.')
    if not 100 <= args.bootstrap_samples <= 10000:
        parser.error('--bootstrap-samples는 100~10000이어야 합니다.')
    development_seeds = list(range(args.development_seed_start, args.development_seed_start + args.development_seeds))
    validation_seeds = list(range(args.validation_seed_start, args.validation_seed_start + args.validation_seeds))
    try:
        validate_seed_split(development_seeds, validation_seeds)
    except ValueError as error:
        parser.error(str(error))
    routing = RouteSnapshot(RoadRouteProvider(cache_dir=args.route_cache, offline=args.offline,
        allow_synthetic_fallback=False) if args.routing == 'osrm' else None)
    try:
        base = build_default_scenarios(routing=routing)[0]
        development, validation = (build_observed_scenarios(base, split) for split in ('development', 'validation'))
        def progress(suite, sid, seed):
            print(f'계산: {suite} / {sid} / seed {seed}', flush=True)
        if args.development_input is not None:
            try:
                saved = json.loads(args.development_input.read_text(encoding='utf-8'))
                if saved.get('routing', {}).get('provider') != args.routing:
                    raise ValueError('저장된 개발 이동시간 방식과 현재 실행 방식이 다릅니다.')
                payload = resume_validation(saved, development, validation, development_seeds, validation_seeds,
                    routing=routing, sigma=args.service_sigma, bootstrap_samples=args.bootstrap_samples, progress=progress)
                payload['development_input'] = str(args.development_input.resolve())
            except (OSError, ValueError, TypeError, KeyError) as error:
                parser.error(f'개발 결과를 재개할 수 없습니다: {error}')
        else:
            payload = run_experiment(development, validation, development_seeds, validation_seeds,
                routing=routing, sigma=args.service_sigma, bootstrap_samples=args.bootstrap_samples,
                progress=progress, development_only=args.development_only)
        payload.update(routing=routing.summary(), scenario_specs=dict(development=development, validation=validation))
        output = args.output.with_name(args.output.name + '_development') if args.development_only else args.output
        _write_json(output.with_suffix('.json'), payload)
        output.with_suffix('.md').write_text(report_markdown(payload), encoding='utf-8')
        if not args.development_only:
            _write_json(args.replay_output, export_replay_cases(payload, validation))
    except RoadRouteError as error:
        parser.exit(1, f'도로 캐시가 부족합니다: {error}\n기존 결과를 유지하고 종료했습니다.\n')
    print(f"고정: {payload['tuning']['selected_fixed_policy']['id']} / V2: {payload['tuning']['selected_observed_policy']['id']} / {payload['branch_runs']}분기", flush=True)
    print(f'결과: {output.with_suffix(".json")}\n보고서: {output.with_suffix(".md")}')


if __name__ == '__main__':
    main()
