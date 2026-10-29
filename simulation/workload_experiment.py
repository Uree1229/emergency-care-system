"""문의 처리 대기열·조건 변경을 분리하고, 개발에서 고른 정책을 미사용 입력에서 검증한다.

python -m simulation.workload_experiment --routing synthetic

실제 응급의료 시스템의 복제가 아니다. 기관 좌표 이외의 환자·문의 서비스시간·
업무량·자원 변화는 합성 설정이며, 이 실행기는 미래의 응답/사건을 정책에 제공하지 않는다.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics

from .map_replay import ROOT, RouteSnapshot, build_default_scenarios, generate_replay
from .replay_experiment import (branch_record, bootstrap_mean_interval, paired_summary,
    seeded_scenario, shared_patient_time_difference, summarize)
from .road_routes import RoadRouteError, RoadRouteProvider


BASELINE = dict(id='load_travel', label='이동시간순 · 3곳', rule='R1', k=3, wait=0,
                notify=True, inquiry_ranking='travel', load_weight=0, load_window=6)
TUNING_POLICIES = (BASELINE,) + tuple(
    dict(BASELINE, id=f'load_w{weight}', label=f'관측 문의량 고려 · 가중치 {weight}',
         inquiry_ranking='recent_load', load_weight=weight)
    for weight in (2, 5, 10)
)
REFERENCE_POLICIES = tuple(dict(BASELINE, id=f'load_k{k}', label=f'이동시간순 · {k}곳', k=k)
                           for k in (1, 5))
CHANGE_POLICIES = (dict(BASELINE, id='change_no_notice', label='변경 알림 비활성', notify=False),
                   dict(BASELINE, id='change_notice', label='변경 알림 활성', notify=True))

EXTRA_METRICS = (
    ('queue_wait_mean', '문의당 관찰된 누적 대기(분)', True),
    ('inquiry_peak_waiting', '최대 대기 업무 수·외생 포함(건)', True),
    ('inquiry_unfinished_jobs', '관찰 종료시 미처리 문의(건)', True),
    ('inquiry_late_replies', '응답 제한시간 뒤 회신(건)', True),
    ('inquiry_service_minutes', '문의 검토 누적 인력·분', True),
    ('resource_invalid_transit_minutes', '필요 자원 불가 상태에서 이송한 환자·분', True),
    ('notifications', '조건 변경 알림(건)', True),
    ('queue_submitted', '전체 접수 작업(건)', True),
    ('queue_completed', '전체 처리 완료 작업(건)', False),
    ('queue_open', '관찰 종료시 미처리 작업(건)', True),
)

LIMITATIONS = [
    '합성 모델의 정책 비교다. 실제 119·광역상황실의 알고리즘 재현이나 현행 체계 대비 개선율이 아니다.',
    '기관 이름·좌표는 공개 자료지만 환자·진료 역량·자원·인력·배경 업무·응답 서비스시간은 실험 설정이다.',
    '기본 이동시간은 직선거리 기반 추정이다. 도로 모드를 선택하더라도 일반 자동차 예상시간이며 구급차 우선주행·실시간 교통은 반영하지 않는다.',
    '대기열은 병원 내 문의 확인 업무의 단순 FIFO 모형이다. 문의당 검토시간과 처리 인원은 실제 측정값이 아니다.',
    '정책은 지난 6분간 자신이 보낸 문의 건수만 사용한다. 실제 대기열·처리 인원·미래 응답·외생 사건·복구 시각은 선택 규칙에 제공하지 않는다.',
    '개발 seed와 검증 seed, 환자 수·발생 간격·배경 업무 설정을 분리했다. 기관·현장 좌표와 같은 네 가지 합성 상황 계열은 공유하므로 새로운 지역·임상 환경에 대한 일반화를 입증하지 않는다.',
    '정책 선정은 개발 데이터의 사전 정의 점수만 사용한다. 검증 결과가 나빠도 그 결과로 가중치를 다시 선택하지 않는다.',
    '치료 시작·완료는 합성 자원 점유의 시작·종료이며, 환자의 생존·회복·예후를 뜻하지 않는다.',
    '시간 평균은 해당 사건이 관측된 환자에 대한 조건부 값이다. 미도착·치료 미시작 수와 공통 환자 분모를 먼저 확인해야 한다.',
    '95% 부트스트랩 구간은 합성 seed 변동만 나타낸다. 합성 설정의 정확성이나 실제 임상 효과에 대한 구간이 아니다.',
    '변경 알림 켬/끔은 구성요소의 효과를 분리하는 실험이다. 기존 운영 시스템에 변경 알림이 없다고 가정하거나 주장하지 않는다.',
    '개선·악화·조건 변경 재생 사례는 검증 결과를 본 후 설명을 위해 골랐다. 문의 규모 대조는 결과와 무관하게 첫 검증 seed를 고정했다. 어느 예시도 대표 효과나 정책 선택의 근거로 단독 사용하지 않는다.',
]


def _mean(values):
    return statistics.mean(values) if values else None


def seed_workload_scenario(scenario, seed, nonresponse=0, sigma=.35):
    """Key review service demand by patient/hospital/attempt; preserve fixed silence.

    When the queue is enabled, these delays are review service durations, rather
    than elapsed request-to-response times. Queue waiting is added by the engine.
    """
    value = seeded_scenario(scenario, seed, nonresponse=nonresponse, sigma=sigma)
    source = scenario.get('response_environment', {})
    for pid, hospitals in value['response_environment'].items():
        for hid, entry in hospitals.items():
            original = source.get(pid, {}).get(hid, source.get('*', {}).get(hid, {}))
            for index, attempt in enumerate(entry['attempts']):
                fixed = original
                if original.get('attempts'):
                    fixed = original['attempts'][min(index, len(original['attempts']) - 1)]
                attempt['silent'] = attempt['silent'] or bool(fixed.get('silent', False))
    value['experiment_environment'].update(
        kind='workload_validation', base_fixed_silence_replaced=False,
        delay_semantics='review_service_minutes_if_queue_enabled',
        workload_measured_from_real_data=False)
    return value


def workload_record(scenario, branch):
    result = branch_record(scenario, branch)
    metrics, raw = result['metrics'], branch.get('metrics', {})
    queue = branch.get('inquiry_queue_report')
    if scenario.get('inquiry_queue', {}).get('enabled') and queue is None:
        raise RuntimeError('문의 대기열이 설정됐지만 엔진의 inquiry_queue_report가 없습니다.')
    aggregate = (queue or {}).get('aggregate', {})
    for key, _, _ in EXTRA_METRICS:
        metrics[key] = raw.get('inquiry_queue_wait_mean' if key == 'queue_wait_mean' else key)
    metrics.update(queue_submitted=aggregate.get('submitted'),
                   queue_completed=aggregate.get('completed'),
                   queue_open=(aggregate.get('waiting', 0) + aggregate.get('processing', 0))
                              if queue is not None else None)
    # Preserve report denominators and censoring information, not queue trajectories.
    result['inquiry_queue_report'] = copy.deepcopy(queue)
    result['queue_enabled'] = bool(scenario.get('inquiry_queue', {}).get('enabled'))
    return result


def summarize_workload(records, baseline_id, samples=1000):
    summaries = summarize(records, baseline_id, samples)
    for case in summaries:
        grouped = {policy['id']: [r for r in records if r['scenario_id'] == case['scenario_id']
                                  and r['policy_id'] == policy['id']]
                   for policy in case['policies']}
        baseline = grouped[baseline_id]
        for policy in case['policies']:
            rows = grouped[policy['id']]
            for key, label, lower in EXTRA_METRICS:
                values = [r['metrics'][key] for r in rows if r['metrics'].get(key) is not None]
                policy['aggregate'][key] = dict(label=label, mean=_mean(values),
                                                seeds_observed=len(values), seeds_total=len(rows))
                policy['paired_vs_baseline'][key] = paired_summary(baseline, rows, key, lower, samples)
        row = next(r for r in records if r['scenario_id'] == case['scenario_id'])
        case.update(family=row['family'], case_kind=row.get('case_kind'), split=row['split'])
    return summaries


def run_suite(scenarios, policies, seeds, split, suite, routing=None,
              nonresponse=0, sigma=.35, generator=generate_replay,
              bootstrap_samples=1000, progress=None):
    if not scenarios or not policies or not seeds:
        raise ValueError('시나리오·정책·seed가 각각 하나 이상 필요합니다.')
    if len({p['id'] for p in policies}) != len(policies) or len(set(seeds)) != len(seeds):
        raise ValueError('정책 ID 또는 seed가 중복됐습니다.')
    records = []
    for source in scenarios:
        for seed in seeds:
            scenario = seed_workload_scenario(source, seed, nonresponse, sigma)
            replay = generator(scenario, policies=policies, routing=routing)
            for branch in replay['branches']:
                result = workload_record(replay['scenario'], branch)
                result.update(scenario_id=source['id'], scenario_label=source['label'], seed=seed,
                              family=source.get('family', source.get('experiment_family', 'unknown')),
                              case_kind=source.get('case_kind'), split=split, suite=suite)
                records.append(result)
            if progress:
                progress(suite, source['id'], seed)
    return dict(records=records, baseline_id=policies[0]['id'],
                summaries=summarize_workload(records, policies[0]['id'], bootstrap_samples))


def score_policy(records):
    """Predeclared lexicographic objective; incomplete coverage cannot win by omission."""
    patients = sum(r['metrics']['patients'] for r in records)
    observed = sum(r['metrics']['start_observed'] for r in records)
    start_total = sum((r['metrics']['start_mean_minutes'] or 0) * r['metrics']['start_observed']
                      for r in records)
    inquiries = sum(r['metrics']['inquiries'] for r in records)
    start = start_total / observed if observed else None
    inquiry_mean = inquiries / patients if patients else None
    return dict(not_treated=sum(r['metrics']['not_treated'] for r in records),
                not_arrived=sum(r['metrics']['not_arrived'] for r in records),
                conditional_start_mean=start, inquiries_per_patient=inquiry_mean,
                combined_minutes=start + .25 * inquiry_mean if start is not None else None,
                patients=patients, start_observed=observed, records=len(records))


def select_development_policy(records, candidates=TUNING_POLICIES):
    if not records or any(r.get('split') != 'development' for r in records):
        raise ValueError('정책 선택에는 development 기록만 사용할 수 있습니다.')
    expected = None
    scores = []
    for order, policy in enumerate(candidates):
        rows = [r for r in records if r['policy_id'] == policy['id']]
        if not rows:
            raise ValueError(f"개발 기록에 정책 {policy['id']}가 없습니다.")
        keys = [(r.get('scenario_id'), r.get('seed'), r['metrics']['patients']) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('개발 정책별 상황·seed 기록이 중복됐습니다.')
        if expected is None:
            expected = set(keys)
        elif set(keys) != expected:
            raise ValueError('개발 후보는 같은 상황·seed·환자 수에서 비교해야 합니다.')
        scores.append(dict(policy_id=policy['id'], candidate_order=order, **score_policy(rows)))
    chosen = min(scores, key=lambda s: (s['not_treated'], s['not_arrived'],
                 s['combined_minutes'] if s['combined_minutes'] is not None else math.inf,
                 s['candidate_order']))
    policy = next(p for p in candidates if p['id'] == chosen['policy_id'])
    return dict(selected_policy=copy.deepcopy(policy), scores=scores,
                selection_split='development',
                objective_order=['total_not_treated', 'total_not_arrived',
                                 'conditional_start_mean + 0.25 * inquiries_per_patient'],
                inquiry_penalty_minutes=.25, tie_break='candidate_order_baseline_first',
                objective_is_clinically_validated=False, validation_retuning=False)


def validation_policies(selected):
    result = []
    for policy in (BASELINE, selected, *REFERENCE_POLICIES):
        if policy['id'] not in {p['id'] for p in result}:
            result.append(copy.deepcopy(policy))
    return result


def _family(scenarios, name):
    return [s for s in scenarios if s.get('family', s.get('experiment_family')) == name]


def _ablation(on_records, off_records, baseline_id, selected_id, samples):
    comparisons, interactions = [], []
    keys = ('not_treated', 'not_arrived', 'start_mean_minutes', 'inquiries_per_patient', 'queue_wait_mean')
    for sid in dict.fromkeys(r['scenario_id'] for r in on_records):
        for policy_id in dict.fromkeys(r['policy_id'] for r in off_records):
            on = [r for r in on_records if r['scenario_id'] == sid and r['policy_id'] == policy_id]
            off = [r for r in off_records if r['scenario_id'] == sid and r['policy_id'] == policy_id]
            comparisons.append(dict(scenario_id=sid, policy_id=policy_id,
                queue_enabled_minus_disabled={key: paired_summary(off, on, key, samples=samples) for key in keys}))
        by_key = {(r['seed'], r['policy_id'], r['queue_enabled']): r for r in on_records + off_records
                  if r['scenario_id'] == sid}
        for metric in ('start_mean_minutes', 'inquiries_per_patient', 'not_treated'):
            values = []
            for seed in sorted({r['seed'] for r in off_records if r['scenario_id'] == sid}):
                rows = [by_key.get((seed, pid, enabled))
                        for enabled in (True, False) for pid in (baseline_id, selected_id)]
                if all(r is not None and r['metrics'].get(metric) is not None for r in rows):
                    a, b, c, d = [r['metrics'][metric] for r in rows]
                    values.append((b - a) - (d - c))
            interactions.append(dict(scenario_id=sid, metric=metric, mean_difference=_mean(values),
                paired_seed_count=len(values), interval_95=bootstrap_mean_interval(values, f'{sid}:{metric}', samples),
                definition='(selected-baseline with queue) - (selected-baseline without queue)'))
    return dict(queue_disabled_records=off_records, comparisons=comparisons, interactions=interactions)


def select_validation_replays(inquiry_records, change_records, selected_id):
    """Select explanatory extremes after validation; never feed them back into tuning."""
    picks, candidates = [], []
    baseline = {(r['scenario_id'], r['seed']): r for r in inquiry_records if r['policy_id'] == BASELINE['id']}
    if selected_id != BASELINE['id']:
        for row in inquiry_records:
            before = baseline.get((row['scenario_id'], row['seed']))
            if row['policy_id'] != selected_id or before is None:
                continue
            if any(row['metrics'][k] != before['metrics'][k] for k in ('not_treated', 'not_arrived')):
                continue
            difference = shared_patient_time_difference(before, row, 'treatment_start_t')['mean_difference']
            if difference is not None:
                candidates.append(dict(scenario_id=row['scenario_id'], seed=row['seed'],
                    policy_id=selected_id, baseline_id=BASELINE['id'], metric='treatment_start_t', difference=difference))
    if candidates:
        for kind, selected in (('validation_best_observed', min(candidates, key=lambda r: r['difference'])),
                               ('validation_worst_observed', max(candidates, key=lambda r: r['difference']))):
            if kind == 'validation_best_observed' and selected['difference'] >= 0:
                continue
            if kind == 'validation_worst_observed' and selected['difference'] <= 0:
                continue
            if not any((p['scenario_id'], p['seed']) == (selected['scenario_id'], selected['seed']) for p in picks):
                picks.append(dict(selected, selection=kind))
    # Always display the two sufficient-capacity controls at the first held-out
    # seed. The choice is independent of their measured effect, including when
    # development chose the travel baseline and no improvement example exists.
    if inquiry_records:
        first_seed = min(r['seed'] for r in inquiry_records)
        for case_kind in ('work_sufficient_w1', 'work_sufficient_w2'):
            before = next((r for r in inquiry_records if r.get('case_kind') == case_kind
                           and r['seed'] == first_seed and r['policy_id'] == BASELINE['id']), None)
            after = next((r for r in inquiry_records if r.get('case_kind') == case_kind
                          and r['seed'] == first_seed and r['policy_id'] == REFERENCE_POLICIES[0]['id']), None)
            if before is not None and after is not None:
                shared = shared_patient_time_difference(before, after, 'treatment_start_t')
                picks.append(dict(scenario_id=before['scenario_id'], seed=first_seed,
                    policy_id=REFERENCE_POLICIES[0]['id'], baseline_id=BASELINE['id'],
                    metric='treatment_start_t', difference=shared['mean_difference'],
                    common_patients=shared['common_patients'],
                    selection='validation_fixed_inquiry_control',
                    selection_rule='sufficient_capacity_workers_1_and_2_at_first_validation_seed',
                    outcome_independent_selection=True))
    silent = {(r['scenario_id'], r['seed']): r for r in change_records if r['policy_id'] == CHANGE_POLICIES[0]['id']}
    changes = []
    for row in change_records:
        before = silent.get((row['scenario_id'], row['seed']))
        if row['policy_id'] != CHANGE_POLICIES[1]['id'] or before is None:
            continue
        a, b = before['metrics'].get('resource_invalid_transit_minutes'), row['metrics'].get('resource_invalid_transit_minutes')
        if a is not None and b is not None:
            changes.append(dict(scenario_id=row['scenario_id'], seed=row['seed'],
                policy_id=CHANGE_POLICIES[1]['id'], baseline_id=CHANGE_POLICIES[0]['id'],
                metric='resource_invalid_transit_minutes', difference=b - a))
    if changes:
        picks.append(dict(min(changes, key=lambda r: r['difference']), selection='validation_state_change'))
    return picks


def run_experiment(development_scenarios, validation_scenarios, development_seeds, validation_seeds,
                   routing=None, nonresponse=0, sigma=.35, generator=generate_replay,
                   bootstrap_samples=1000, progress=None):
    if set(development_seeds) & set(validation_seeds):
        raise ValueError('개발 seed와 검증 seed는 겹칠 수 없습니다.')
    dev, validation = _family(development_scenarios, 'inquiry_workload'), _family(validation_scenarios, 'inquiry_workload')
    changes = _family(validation_scenarios, 'condition_change')
    if not dev or not validation or not changes:
        raise ValueError('문의 업무와 조건 변경 계열의 개발/검증 시나리오가 필요합니다.')
    options = dict(routing=routing, nonresponse=nonresponse, sigma=sigma, generator=generator,
                   bootstrap_samples=bootstrap_samples, progress=progress)
    development = run_suite(dev, TUNING_POLICIES, development_seeds, 'development', 'inquiry_development', **options)
    locked = select_development_policy(development['records'])
    selected = locked['selected_policy']
    # The held-out suite is generated only after the selected policy is frozen.
    inquiry = run_suite(validation, validation_policies(selected), validation_seeds,
                        'validation', 'inquiry_validation', **options)
    change = run_suite(changes, CHANGE_POLICIES, validation_seeds,
                       'validation', 'condition_change', **options)
    ablation_policies = [copy.deepcopy(BASELINE)]
    if selected['id'] != BASELINE['id']:
        ablation_policies.append(copy.deepcopy(selected))
    disabled = copy.deepcopy(validation)
    for scenario in disabled:
        scenario.setdefault('inquiry_queue', {})['enabled'] = False
    off = run_suite(disabled, ablation_policies, validation_seeds,
                    'validation', 'queue_disabled_ablation', **options)
    on_records = [r for r in inquiry['records'] if r['policy_id'] in {p['id'] for p in ablation_policies}]
    replay_cases = select_validation_replays(inquiry['records'], change['records'], selected['id'])
    suites = dict(inquiry_development=development, inquiry_validation=inquiry, condition_change=change,
                  queue_ablation=_ablation(on_records, off['records'], BASELINE['id'], selected['id'], bootstrap_samples))
    count = sum(len(s['records']) for s in (development, inquiry, change, off))
    return dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
        development_seeds=list(development_seeds), validation_seeds=list(validation_seeds),
        nonresponse=nonresponse, service_lognormal_sigma=sigma,
        tuning=locked, suites=suites, replay_cases=replay_cases,
        branch_runs=count, patient_runs=sum(r['metrics']['patients'] for s in (development, inquiry, change, off) for r in s['records']),
        validation_scope='held_out_seeds_and_parameters_in_the_same_synthetic_families',
        actual_system_comparison=False, limitations=LIMITATIONS)


def export_replay_cases(payload, scenarios):
    by_id = {s['id']: s for s in scenarios}
    exported = []
    for selected in payload['replay_cases']:
        scenario = seed_workload_scenario(by_id[selected['scenario_id']], selected['seed'],
                                         payload['nonresponse'], payload['service_lognormal_sigma'])
        if selected['selection'] == 'validation_state_change':
            policies = CHANGE_POLICIES
            label = '조건 변경 알림 비교'
        elif selected['selection'] == 'validation_fixed_inquiry_control':
            policies = (BASELINE, *REFERENCE_POLICIES)
            label = '문의규모 고정 대조 · 첫 검증 seed'
        else:
            policies = (BASELINE, payload['tuning']['selected_policy'])
            label = '관측된 최대 개선' if selected['selection'] == 'validation_best_observed' else '관측된 최대 악화'
        scenario['id'] = f"workload_{scenario['id']}_seed{selected['seed']}"
        scenario['label'] += f" · {label} · seed {selected['seed']}"
        scenario['policies'] = copy.deepcopy(policies)
        scenario['experiment_selection'] = copy.deepcopy(selected)
        metric = '공통 환자 치료 시작' if selected['metric'] == 'treatment_start_t' else '필요 자원 불가 상태 이송 환자·분'
        if selected['selection'] == 'validation_fixed_inquiry_control':
            timing = '문의 담당 1명/2명 대조의 첫 검증 seed를 결과와 무관하게 고정했다. 사후에 더 나은 정책이나 seed를 고른 예시가 아니다.'
        else:
            timing = '검증 후 고른 설명용 사례다. 대표 사례가 아니다.'
        difference = '비교 가능한 공통 환자 없음' if selected['difference'] is None else f"{selected['difference']:+.2f}"
        scenario['description'] = scenario.get('description', '') + f" {timing} {metric} 차이 {difference}."
        exported.append(dict(scenario=scenario, **selected))
    return dict(schema_version=2, geometry_included=False, scenarios=exported)


def _fmt(value):
    return '—' if value is None else f'{value:.2f}'


def _interval(value):
    return '추정 불가' if value is None else f'[{value[0]:.2f}, {value[1]:.2f}]'


def report_markdown(payload):
    locked = payload['tuning']
    lines = ['# 문의 부담·조건 변경 분리 검증', '',
        f"개발 seed {len(payload['development_seeds'])}개 / 검증 seed {len(payload['validation_seeds'])}개 · 총 {payload['branch_runs']}개 정책 실행 · {payload['patient_runs']}회 환자 실행", '',
        '**모든 효과는 합성 모델 안의 결과다. 현행 응급의료 체계 대비 실제 개선율이 아니다.**', '',
        '## 비교 설계와 정책 선택', '',
        'A. 배정 규칙은 R1·추가 대기 0분·변경 알림 켬으로 고정하고 문의 후보 순위/규모만 비교했다. 이동시간순 3곳을 기준으로, 지난 6분간 보낸 문의 건수에 가중치 2/5/10을 곱해 예상 이동시간에 더하는 규칙을 개발 데이터에서 비교했다. 1곳/5곳 문의는 별도 참고 비교군이다.', '',
        'B. 동일 문의·배정 규칙에서 변경 알림 켬/끔만 비교했다. 상태 변화 없음, 짧은 중단, 긴 중단, 외부 환자 유입을 분리한다. 알림 활성은 자동 목적지 변경 정책을 모사한 실험이며 실제 운영은 의료진·구급대 판단이 필요하다.', '',
        'C. 문의 처리 대기열을 켜고 끈 비교를 추가했다. 큐를 끈 경우도 동일 검토 서비스시간은 남고, 문의 간 처리 대기만 제거한다.', '',
        '개발 선택 순서: ① 치료 미시작 합계 최소 ② 동률이면 최종 미도착 합계 최소 ③ 다시 동률이면 치료 시작 조건부 평균 + 0.25 × 환자당 문의 최소. 문의 1건을 0.25분으로 놓은 값은 실험용 비용이며 임상 기준이 아니다. 완전 동률은 기준 정책을 우선한다.', '',
        f"**개발에서 고정한 정책: {locked['selected_policy']['label']} (`{locked['selected_policy']['id']}`). 검증에서 재선택하지 않았다.**", '',
        '| 개발 후보 | 치료 미시작 합계 | 미도착 합계 | 치료 시작 평균 | 문의/환자 | 결합 점수 |',
        '|---|---:|---:|---:|---:|---:|']
    for score in locked['scores']:
        lines.append(f"| {score['policy_id']} | {score['not_treated']} | {score['not_arrived']} | {_fmt(score['conditional_start_mean'])} | {_fmt(score['inquiries_per_patient'])} | {_fmt(score['combined_minutes'])} |")
    lines += ['', '## 미사용 입력 검증 결과', '',
        '표는 seed별 지표의 평균이다. 시간은 119 도착·현장처치 후 병원 탐색 시작부터의 분이다. 큐 지표의 미관측 값은 0으로 대체하지 않는다. 문의 대기는 종료시까지 관찰된 누적 대기를 전체 접수 문의로 나눈 값이며 미처리 문의의 미래 대기는 포함하지 않는다. 최대 대기 업무는 고정 외생 업무도 포함한다. 문의별 제한시간 뒤 회신은 무시되지만 병원의 검토 작업은 계속 점유된다는 실험 설정이다.', '']
    for suite_name, title in (('inquiry_validation', '문의 정책'), ('condition_change', '조건 변경 알림')):
        lines += [f'### {title}', '']
        for case in payload['suites'][suite_name]['summaries']:
            lines += [f"#### {case['label']}", '',
                '| 정책 | 미도착 | 치료 미시작 | 완료 | 문의/환자 | 확정 평균 | 치료 시작 평균 | 문의 대기 평균 | 최대 대기 문의 | 자원 불가 이송 환자·분 | 목적지 변경 | 전원 |',
                '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
            keys = ('not_arrived', 'not_treated', 'completed', 'inquiries_per_patient', 'commit_mean_minutes',
                    'start_mean_minutes', 'queue_wait_mean', 'inquiry_peak_waiting',
                    'resource_invalid_transit_minutes', 'redirects', 'transfers')
            for policy in case['policies']:
                lines.append(f"| {policy['label']} | " + ' | '.join(_fmt(policy['aggregate'][k]['mean']) for k in keys) + ' |')
            lines += ['', f"차이는 정책 − 기준({case['baseline_id']})이다. 치료 시작 차이는 양쪽에서 치료가 시작된 같은 환자만 비교한다.", '',
                '| 정책 | 공통 환자 치료 시작 차이 | 95% 구간 | 공통 환자/전체 | 문의 차이/환자 | 문의 증가 seed 비율 | 자원 불가 이송 차이 |',
                '|---|---:|---|---:|---:|---:|---:|']
            for policy in case['policies']:
                shared = policy['shared_patient_times']['treatment_start_t']
                inquiry = policy['paired_vs_baseline']['inquiries_per_patient']
                fraction = inquiry['regression_fraction']
                invalid = policy['paired_vs_baseline']['resource_invalid_transit_minutes']['mean_difference']
                lines.append(f"| {policy['label']} | {_fmt(shared['mean_difference'])} | {_interval(shared['interval_95'])} | {shared['common_patient_total']}/{policy['denominators']['patients']} | {_fmt(inquiry['mean_difference'])} | {'—' if fraction is None else f'{fraction:.0%}'} | {_fmt(invalid)} |")
            lines += ['']
    lines += ['## 문의 대기열 제거 비교', '',
        '아래 값은 큐를 켰을 때 − 껐을 때다. 양수 시간은 문의 처리 대기가 지연을 늘렸다는 뜻이다.', '',
        '| 상황 | 정책 | 치료 미시작 차이 | 치료 시작 평균 차이 | 문의/환자 차이 |',
        '|---|---|---:|---:|---:|']
    for row in payload['suites']['queue_ablation']['comparisons']:
        d = row['queue_enabled_minus_disabled']
        lines.append(f"| {row['scenario_id']} | {row['policy_id']} | {_fmt(d['not_treated']['mean_difference'])} | {_fmt(d['start_mean_minutes']['mean_difference'])} | {_fmt(d['inquiries_per_patient']['mean_difference'])} |")
    lines += ['', 'JSON의 interactions에는 (선택 정책−기준 정책)의 차이가 큐 유무에 따라 얼마나 달라졌는지도 저장했다. 시간 차이는 생존·회복 개선으로 환산하지 않는다.', '',
        '## 설명용 지도 재생 사례', '']
    for selected in payload.get('replay_cases', []):
        description = '첫 검증 seed 고정 대조' if selected['selection'] == 'validation_fixed_inquiry_control' else '검증 결과를 보고 고른 설명용 사례'
        lines.append(f"- {description}: {selected['scenario_id']} / seed {selected['seed']} / {selected['policy_id']} / {selected['metric']} 차이 {_fmt(selected['difference'])}")
    lines += ['', '문의 담당 1명/2명 상황의 고정 대조는 첫 검증 seed에서 문의 규모 1/3/5곳을 모두 보여준다. 결과와 무관하게 고른 입력이며 최선 정책을 사후에 고른 예시가 아니다. 최대 개선·악화와 조건 변경 사례는 별도로 검증 결과를 보고 고른 설명용 사례다. 유리한 예시만 효과 근거로 삼지 않는다.', '', '## 한계와 해석', '']
    lines += [f'- {item}' for item in payload['limitations']]
    lines += ['', '실제 문의 처리시간·수용 응답률·조건 변경 빈도 자료를 확보하면 같은 구조에서 입력을 교체해 다시 검증해야 한다. 개선이 작거나 악화한 상황도 그대로 유지한다.', '']
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
    parser.add_argument('--routing', choices=('synthetic', 'osrm'), default='synthetic')
    parser.add_argument('--offline', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--route-cache', type=Path, default=ROOT / 'simulation/route_cache')
    parser.add_argument('--nonresponse', type=float, default=0, help='합성 무응답 확률, 기본 0: 큐 메커니즘 분리')
    parser.add_argument('--service-sigma', type=float, default=.35)
    parser.add_argument('--bootstrap-samples', type=int, default=1000)
    parser.add_argument('--output', type=Path, default=ROOT / 'simulation_results/workload_validation')
    parser.add_argument('--replay-output', type=Path, default=ROOT / 'simulation_results/workload_validation_replays.json')
    args = parser.parse_args(argv)
    if not 1 <= args.development_seeds <= 1000 or not 1 <= args.validation_seeds <= 1000:
        parser.error('각 seed 수는 1~1000이어야 합니다.')
    if not 0 <= args.nonresponse <= 1 or not math.isfinite(args.nonresponse):
        parser.error('--nonresponse는 0~1이어야 합니다.')
    if args.service_sigma < 0 or not math.isfinite(args.service_sigma):
        parser.error('--service-sigma는 0 이상이어야 합니다.')
    if not 100 <= args.bootstrap_samples <= 10000:
        parser.error('--bootstrap-samples는 100~10000이어야 합니다.')
    from .workload_scenarios import build_workload_scenarios
    routing = RouteSnapshot(RoadRouteProvider(cache_dir=args.route_cache, offline=args.offline,
                            allow_synthetic_fallback=False) if args.routing == 'osrm' else None)
    try:
        base = build_default_scenarios(routing=routing)[0]
        development = build_workload_scenarios(base, split='development')
        validation = build_workload_scenarios(base, split='validation')
        def progress(suite, sid, seed):
            print(f'계산: {suite} / {sid} / seed {seed}', flush=True)
        payload = run_experiment(development, validation, list(range(1, args.development_seeds + 1)),
            list(range(1001, 1001 + args.validation_seeds)), routing=routing,
            nonresponse=args.nonresponse, sigma=args.service_sigma,
            bootstrap_samples=args.bootstrap_samples, progress=progress)
        payload['routing'] = routing.summary()
        payload['scenario_specs'] = dict(development=development, validation=validation)
        _write_json(args.output.with_suffix('.json'), payload)
        args.output.with_suffix('.md').write_text(report_markdown(payload), encoding='utf-8')
        if args.replay_output:
            _write_json(args.replay_output, export_replay_cases(payload, validation))
    except RoadRouteError as error:
        parser.exit(1, f'도로 캐시가 부족합니다: {error}\n외부 조회 없이 종료했으며 기존 결과는 유지됩니다.\n')
    print(f"정책 고정: {payload['tuning']['selected_policy']['id']} / 실행 {payload['branch_runs']}회", flush=True)
    print(f'결과: {args.output.with_suffix(".json")}\n보고서: {args.output.with_suffix(".md")}')


if __name__ == '__main__':
    main()
