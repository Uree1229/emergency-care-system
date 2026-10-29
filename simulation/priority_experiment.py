"""우선순위 보호 후보를 개발에서 잠그고 미사용 합성 입력에서 비교한다."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics

from .map_replay import ROOT, RouteSnapshot, build_default_scenarios, generate_replay
from .priority_scenarios import build_priority_scenarios
from .replay_experiment import bootstrap_mean_interval, shared_patient_time_difference
from .road_routes import RoadRouteError, RoadRouteProvider
from .sim import Policy
from .workload_experiment import run_suite, seed_workload_scenario, summarize_workload


FIXED_POLICIES = tuple(dict(id=f'priority_fixed_k{k}', label=f'고정 {k}곳 문의',
    rule='R1', k=k, wait=0, notify=True, inquiry_ranking='travel') for k in (1, 3, 5))
LOCKED_V2 = dict(id='priority_locked_v2_w1_h2', label='잠근 V2 · 우선순위 처리 없음',
    rule='R1', k=3, wait=0, notify=True, inquiry_ranking='travel',
    observed_inquiry=dict(window=30, prior_reply=3, min_samples=2, congestion_weight=1,
        hedge_after=2, hedge_margin=0, max_new_per_review=1, timeout_penalty=6))
PRIORITY_POLICIES = (
    dict(LOCKED_V2, id='priority_requests', label='V2 · 신규 요청 우선순위순', priority_order='requests'),
    dict(LOCKED_V2, id='priority_all', label='V2 · 현재 환자 처리 우선순위순', priority_order='all'),
    dict(LOCKED_V2, id='priority_all_highhedge1', label='V2 · 우선순위순 · 높은 군 추가 문의 1분',
         priority_order='all', high_priority_hedge_after=1),
    dict(LOCKED_V2, id='priority_all_r4', label='V2 · 우선순위순 · 사전식 공동 배정 R4',
         priority_order='all', rule='R4'),
)
DEVELOPMENT_POLICIES = FIXED_POLICIES + (LOCKED_V2,) + PRIORITY_POLICIES
PREVIOUS_VALIDATION_SEEDS = frozenset(range(1001, 1021)) | frozenset(range(2001, 2021)) | frozenset(range(3001, 3021)) | frozenset(range(4001, 4021))
DEFAULT_DEVELOPMENT_START = 301
DEFAULT_VALIDATION_START = 5001
REPLAY_CONDITIONS = (('mixed_priority_scarcity', 0), ('staggered_priority', .2), ('resource_change', 0))
OBJECTIVE_ORDER = ['priority4_not_treated', 'total_not_treated', 'priority4_not_arrived',
    'total_not_arrived', 'priority4_conditional_start_mean',
    'conditional_start_mean + 0.25 * inquiries_per_patient']
LIMITATIONS = [
    '실제 119·광역상황실의 구현을 복제한 것이 아닌 합성 정책 비교다. 현행 체계 대비 개선율이나 임상적 효과·최적성의 증명이 아니다.',
    '기관 식별자·이름·좌표만 공개 자료다. 환자·우선순위·역량·자원·검토시간·배경 업무·무응답·외생 사건은 합성 입력이다.',
    '우선순위 4/2/1과 0.25분 문의 비용은 실험 설정이며 임상적으로 합의된 중증도 기준이 아니다.',
    '정책은 현재 발생한 환자와 도착한 회신·타임아웃, 자신이 보낸 문의만 사용한다. 미래 환자·외생 일정·실제 문의 담당자 수·대기열·잠재 서비스시간은 제공하지 않는다.',
    '우선순위 처리는 현재 시각의 신규 요청 또는 환자 처리 순서에만 적용한다. 이미 접수된 병원 문의는 FIFO이며 취소·선점하지 않는다. 이미 확정된 이송도 후발 환자를 위해 취소하지 않는다.',
    'R4는 같은 시각에 유효한 수용 응답이 모인 환자만 대상으로 사전식 공동 배정을 한다. 가능한 조합 수의 제한을 넘으면 우선순위순 탐욕 대체를 사용한다. 전 시간대의 최적성 보장이 아니다.',
    '높은 우선순위를 먼저 보호하면 다른 군의 미치료나 지연이 늘 수 있다. 전체 수와 군별 분모·미도착·미치료를 함께 제시한다.',
    '개발에서 선택한 우선순위 후보와 고정 문의 후보를 잠근 뒤 검증에서 재선택하지 않는다. V2는 이전 개발에서 고른 설정 그대로 비교한다.',
    '이전 검증 seed 1001~1020·2001~2020·3001~3020·4001~4020은 제외했다. 새 입력도 같은 합성 모형·기관 위치 계열을 공유하므로 실제 지역 일반화를 뜻하지 않는다.',
    '시간은 탐색 시작부터 사건이 관측된 환자의 조건부 값이다. 치료 미시작 환자에게 0을 대입하지 않는다. 공통 환자 시간 차이와 전체 조건부 평균 차이를 구분한다.',
    '95% 구간은 전체 seed를 묶음으로 재표집한 합성 입력 변동이다. 설정의 타당성이나 실제 임상 효과의 불확실성을 포함하지 않는다.',
    '치료 시작·완료는 합성 자원 점유 시작·종료이며 생존·회복·예후가 아니다. 문의 정책은 부족한 자원을 새로 만들지 못한다.',
    '기본 이동은 직선거리 추정이다. 도로 모드도 일반 자동차 예상시간으로 구급차 우선주행·실시간 교통은 반영하지 않는다.',
    '지도는 첫 검증 seed에서 동시 자원 부족·후발 높은 우선순위·자원 변화 세 조건을 결과와 무관하게 사전 지정했다.',
]


def _mean(values):
    return statistics.mean(values) if values else None


def _p95(values):
    """Nearest-rank percentile; never impute censored patients."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(.95 * len(ordered)) - 1)] if ordered else None


def validate_seed_split(development_seeds, validation_seeds):
    for values in (development_seeds, validation_seeds):
        if not values or len(set(values)) != len(values):
            raise ValueError('각 seed 집합은 하나 이상이며 중복이 없어야 합니다.')
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in values):
            raise ValueError('seed는 0 이상의 정수여야 합니다.')
    if set(development_seeds) & set(validation_seeds):
        raise ValueError('개발·검증 seed는 겹칠 수 없습니다.')
    if (set(development_seeds) | set(validation_seeds)) & PREVIOUS_VALIDATION_SEEDS:
        raise ValueError('이전 검증 seed 1001~1020·2001~2020·3001~3020·4001~4020은 재사용할 수 없습니다.')


def priority_summary(records):
    """Pool patient outcomes, retaining group denominators and censoring counts."""
    outcomes = [o for r in records for o in r['outcomes']]
    def group(members):
        result = dict(patients=len(members), not_treated=sum(o.get('treatment_start_t') is None for o in members),
            not_arrived=sum(o.get('final_arrive_t') is None for o in members),
            unassigned=sum(o.get('first_commit_t') is None for o in members))
        for field, name in (('first_commit_t', 'commit'), ('final_arrive_t', 'arrival'), ('treatment_start_t', 'start')):
            values = [o[field] - o['search_start_t'] for o in members if o.get(field) is not None]
            result.update({f'{name}_observed': len(values), f'{name}_mean_minutes': _mean(values),
                f'{name}_p95_minutes': _p95(values), f'{name}_max_minutes': max(values, default=None)})
        return result
    result = dict(overall=group(outcomes), by_priority={str(p): group([o for o in outcomes if o['priority'] == p])
        for p in sorted({o['priority'] for o in outcomes}, reverse=True)}, records=len(records))
    inquiries = sum(r['metrics']['inquiries'] for r in records)
    result['overall'].update(inquiries=inquiries,
        inquiries_per_patient=inquiries / len(outcomes) if outcomes else None,
        redirects=sum(r['metrics']['redirects'] for r in records),
        transfers=sum(r['metrics']['transfers'] for r in records))
    keys = ('resource_invalid_transit_minutes', 'notifications', 'queue_wait_mean',
        'inquiry_service_minutes', 'inquiry_late_replies', 'joint_fallbacks')
    result['diagnostics'] = {}
    for key in keys + tuple(sorted({key for r in records for key in r['metrics'] if key.startswith('observed_')})):
        values = [r['metrics'][key] for r in records if isinstance(r['metrics'].get(key), (int, float))]
        result['diagnostics'][key] = dict(value=(max(values) if 'max_' in key else
            _mean(values) if key == 'queue_wait_mean' else sum(values)) if values else None,
            aggregation='max' if 'max_' in key else 'branch_mean' if key == 'queue_wait_mean' else 'sum',
            branches_observed=len(values), branches_total=len(records))
    return result


def score_policy(records):
    summary = priority_summary(records)
    a, high = summary['overall'], summary['by_priority'].get('4', {})
    start, inquiry = a['start_mean_minutes'], a['inquiries_per_patient']
    return dict(priority4_not_treated=high.get('not_treated', 0),
        not_treated=a['not_treated'], priority4_not_arrived=high.get('not_arrived', 0),
        not_arrived=a['not_arrived'], priority4_conditional_start_mean=high.get('start_mean_minutes'),
        conditional_start_mean=start, inquiries_per_patient=inquiry,
        combined_minutes=start + .25 * inquiry if start is not None else None,
        patients=a['patients'], start_observed=a['start_observed'],
        priority4_patients=high.get('patients', 0), priority4_start_observed=high.get('start_observed', 0),
        records=len(records))


def _score_key(score):
    return (score['priority4_not_treated'], score['not_treated'], score['priority4_not_arrived'],
        score['not_arrived'], score['priority4_conditional_start_mean']
        if score['priority4_conditional_start_mean'] is not None else math.inf,
        score['combined_minutes'] if score['combined_minutes'] is not None else math.inf,
        score['candidate_order'])


def _candidate_scores(records, candidates):
    if not records or any(r.get('split') != 'development' for r in records):
        raise ValueError('정책 선택은 development 기록만 사용할 수 있습니다.')
    expected, scores = None, []
    for order, policy in enumerate(candidates):
        rows = [r for r in records if r['policy_id'] == policy['id']]
        keys = [(r.get('scenario_id'), r.get('seed'), r['metrics']['patients']) for r in rows]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError('개발 후보 기록이 없거나 상황·seed가 중복됐습니다.')
        identity = {(r.get('scenario_id'), r.get('seed')): sorted((o['id'], o['priority'], o['search_start_t'])
            for o in r['outcomes']) for r in rows}
        current = (set(keys), identity)
        if expected is None:
            expected = current
        elif current != expected:
            raise ValueError('개발 후보는 같은 상황·seed·환자 ID·우선순위·시작시각을 가져야 합니다.')
        scores.append(dict(policy_id=policy['id'], candidate_order=order, **score_policy(rows)))
    return scores


def _selection(records, candidates):
    scores = _candidate_scores(records, candidates)
    winner = min(scores, key=_score_key)
    return dict(selected_policy=copy.deepcopy(next(p for p in candidates if p['id'] == winner['policy_id'])),
        scores=scores, selection_split='development', validation_retuning=False,
        objective_order=copy.deepcopy(OBJECTIVE_ORDER), inquiry_penalty_minutes=.25,
        objective_is_clinically_validated=False, tie_break='candidate_order')


def lock_development_policies(records):
    _candidate_scores(records, DEVELOPMENT_POLICIES)
    fixed, priority = _selection(records, FIXED_POLICIES), _selection(records, PRIORITY_POLICIES)
    return dict(fixed=fixed, priority=priority, selected_fixed_policy=copy.deepcopy(fixed['selected_policy']),
        selected_priority_policy=copy.deepcopy(priority['selected_policy']), v2_reference=copy.deepcopy(LOCKED_V2),
        selection_split='development', validation_retuning=False, objective_order=copy.deepcopy(OBJECTIVE_ORDER),
        inquiry_penalty_minutes=.25, objective_is_clinically_validated=False)


def validation_policies(locked):
    return [copy.deepcopy(LOCKED_V2), copy.deepcopy(locked['selected_priority_policy'])] + [copy.deepcopy(p) for p in FIXED_POLICIES]


def _paired_group(baseline, comparison, priority=None, samples=1000, key='all'):
    """One observation per seed, pooling paired patients over all conditions."""
    before = {(r['scenario_id'], r['seed']): r for r in baseline}
    details = []
    for seed in sorted({r['seed'] for r in comparison}):
        deltas, base_observed, after_observed, total = [], 0, 0, 0
        for row in (r for r in comparison if r['seed'] == seed):
            base = before[(row['scenario_id'], seed)]
            left = {o['id']: o for o in base['outcomes'] if priority is None or o['priority'] == priority}
            right = [o for o in row['outcomes'] if priority is None or o['priority'] == priority]
            total += len(right)
            base_observed += sum(o.get('treatment_start_t') is not None for o in left.values())
            after_observed += sum(o.get('treatment_start_t') is not None for o in right)
            for o in right:
                b = left.get(o['id'])
                if b and b.get('treatment_start_t') is not None and o.get('treatment_start_t') is not None:
                    deltas.append((o['treatment_start_t'] - o['search_start_t']) - (b['treatment_start_t'] - b['search_start_t']))
        details.append(dict(seed=seed, mean_difference=_mean(deltas), common_patients=len(deltas),
            baseline_start_observed=base_observed, comparison_start_observed=after_observed, patients=total))
    values = [d['mean_difference'] for d in details if d['mean_difference'] is not None]
    return dict(mean_difference=_mean(values), interval_95=bootstrap_mean_interval(values, key, samples),
        interval_method='paired_whole_seed_percentile_bootstrap', paired_seed_count=len(values),
        common_patient_total=sum(d['common_patients'] for d in details),
        baseline_start_observed=sum(d['baseline_start_observed'] for d in details),
        comparison_start_observed=sum(d['comparison_start_observed'] for d in details),
        patients=sum(d['patients'] for d in details), seed_details=details,
        regression_seed_count=sum(v > 0 for v in values), regression_fraction=sum(v > 0 for v in values) / len(values) if values else None)


def comparison_summary(records, policies, samples=1000):
    baseline = [r for r in records if r['policy_id'] == LOCKED_V2['id']]
    base_index = {(r['scenario_id'], r['seed']): r for r in baseline}
    result = []
    for policy in policies:
        rows = [r for r in records if r['policy_id'] == policy['id']]
        pairs = []
        for row in rows:
            b = base_index[(row['scenario_id'], row['seed'])]
            common = shared_patient_time_difference(b, row, 'treatment_start_t')
            high_b = sum(o['priority'] == 4 and o.get('treatment_start_t') is None for o in b['outcomes'])
            high_a = sum(o['priority'] == 4 and o.get('treatment_start_t') is None for o in row['outcomes'])
            pairs.append(dict(scenario_id=row['scenario_id'], seed=row['seed'],
                priority4_not_treated_difference=high_a - high_b,
                not_treated_difference=row['metrics']['not_treated'] - b['metrics']['not_treated'],
                not_arrived_difference=row['metrics']['not_arrived'] - b['metrics']['not_arrived'],
                inquiries_per_patient_difference=row['metrics']['inquiries_per_patient'] - b['metrics']['inquiries_per_patient'],
                common_start_mean_difference=common['mean_difference'], common_patients=common['common_patients']))
        per_seed = []
        for seed in sorted({p['seed'] for p in pairs}):
            group = [p for p in pairs if p['seed'] == seed]
            current = [r for r in rows if r['seed'] == seed]
            previous = [r for r in baseline if r['seed'] == seed]
            n = sum(r['metrics']['patients'] for r in current)
            per_seed.append(dict(seed=seed,
                priority4_not_treated_difference=sum(p['priority4_not_treated_difference'] for p in group),
                not_treated_difference=sum(p['not_treated_difference'] for p in group),
                inquiries_per_patient_difference=(sum(r['metrics']['inquiries'] for r in current)
                    - sum(r['metrics']['inquiries'] for r in previous)) / n if n else None))
        inquiry_deltas = [p['inquiries_per_patient_difference'] for p in per_seed if p['inquiries_per_patient_difference'] is not None]
        result.append(dict(policy_id=policy['id'], baseline_id=LOCKED_V2['id'],
            shared_patient_times={str(p) if p is not None else 'all': _paired_group(baseline, rows, p, samples,
                f'priority:{policy["id"]}:{p}') for p in (None, 4, 2, 1)},
            inquiry_difference=dict(mean_difference=_mean(inquiry_deltas),
                interval_95=bootstrap_mean_interval(inquiry_deltas, f'priority:{policy["id"]}:inquiry', samples),
                interval_method='paired_whole_seed_percentile_bootstrap'),
            condition_seed_details=pairs, seed_count_details=per_seed,
            priority4_worsened_condition_seeds=sum(p['priority4_not_treated_difference'] > 0 for p in pairs),
            total_worsened_condition_seeds=sum(p['not_treated_difference'] > 0 for p in pairs),
            time_worsened_condition_seeds=sum(p['common_start_mean_difference'] is not None and p['common_start_mean_difference'] > 0 for p in pairs)))
    return result


def _run_conditions(scenarios, policies, seeds, split, routing=None, sigma=.35,
                    generator=generate_replay, bootstrap_samples=1000, progress=None):
    records, observed = [], {}
    def collecting(value, policies, routing):
        value['experiment_environment'].update(kind='priority_protection_validation',
            prior_validation_seed_reused=False, policy_has_true_queue_state=False, future_patient_schedule_visible=False)
        replay = generator(value, policies=policies, routing=routing)
        for branch in replay['branches']:
            observed[(value['id'], value['seed'], branch['id'])] = {k: v for k, v in branch.get('metrics', {}).items() if k.startswith('observed_')}
        return replay
    for probability in sorted({s.get('experiment_nonresponse', 0) for s in scenarios}):
        group = [s for s in scenarios if s.get('experiment_nonresponse', 0) == probability]
        suite = run_suite(group, policies, seeds, split, f'priority_{split}', routing=routing,
            nonresponse=probability, sigma=sigma, generator=collecting,
            bootstrap_samples=bootstrap_samples, progress=progress)
        for row in suite['records']:
            row['nonresponse_probability'] = probability
            row['metrics'].update(observed[(row['scenario_id'], row['seed'], row['policy_id'])])
        records.extend(suite['records'])
    return dict(records=records, baseline_id=LOCKED_V2['id'], bootstrap_samples=bootstrap_samples,
        summaries=summarize_workload(records, LOCKED_V2['id'], bootstrap_samples),
        overall_summary=[dict(policy_id=p['id'], label=p['label'], **priority_summary([r for r in records if r['policy_id'] == p['id']])) for p in policies],
        policy_comparisons=comparison_summary(records, policies, bootstrap_samples))


def select_replay_cases(records, locked):
    if not records:
        return []
    first_seed, picks = min(r['seed'] for r in records), []
    selected = locked['selected_priority_policy']['id']
    for family, nonresponse in REPLAY_CONDITIONS:
        before = next((r for r in records if r.get('case_kind') == family and r['seed'] == first_seed
            and r.get('nonresponse_probability', 0) == nonresponse and r['policy_id'] == LOCKED_V2['id']), None)
        if before is None:
            continue
        after = next(r for r in records if r['scenario_id'] == before['scenario_id'] and r['seed'] == first_seed and r['policy_id'] == selected)
        shared = shared_patient_time_difference(before, after, 'treatment_start_t')
        picks.append(dict(scenario_id=before['scenario_id'], seed=first_seed, baseline_id=LOCKED_V2['id'],
            policy_id=selected, metric='treatment_start_t', difference=shared['mean_difference'],
            common_patients=shared['common_patients'], selection='validation_predeclared_priority_control',
            outcome_independent_selection=True, selection_rule='first_validation_seed_mixed_nr0_staggered_nr20_change_nr0'))
    return picks


def _validate_scenarios(development, validation):
    if not development or not validation:
        raise ValueError('개발·검증 시나리오가 각각 필요합니다.')
    dev_ids, val_ids = {s['id'] for s in development}, {s['id'] for s in validation}
    if dev_ids & val_ids or len(dev_ids) != len(development) or len(val_ids) != len(validation):
        raise ValueError('개발·검증 시나리오 ID는 중복될 수 없습니다.')
    for split, specs in (('development', development), ('validation', validation)):
        if any(s.get('split', split) != split for s in specs):
            raise ValueError('시나리오 split과 실행 집합이 다릅니다.')


def run_experiment(development_scenarios, validation_scenarios,
                   development_seeds=tuple(range(301, 309)), validation_seeds=tuple(range(5001, 5021)),
                   routing=None, sigma=.35, generator=generate_replay, bootstrap_samples=1000,
                   progress=None, development_only=False):
    validate_seed_split(development_seeds, validation_seeds)
    _validate_scenarios(development_scenarios, validation_scenarios)
    options = dict(routing=routing, sigma=sigma, generator=generator, bootstrap_samples=bootstrap_samples, progress=progress)
    development = _run_conditions(development_scenarios, DEVELOPMENT_POLICIES, development_seeds, 'development', **options)
    locked = lock_development_policies(development['records'])
    validation = dict(records=[], summaries=[], baseline_id=LOCKED_V2['id'], overall_summary=[], policy_comparisons=[]) if development_only else _run_conditions(validation_scenarios, validation_policies(locked), validation_seeds, 'validation', **options)
    records = development['records'] + validation['records']
    return dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(), experiment='priority_protection_v1',
        development_only=development_only, development_seeds=list(development_seeds), validation_seeds=list(validation_seeds),
        previous_validation_seeds_excluded=sorted(PREVIOUS_VALIDATION_SEEDS), service_lognormal_sigma=sigma,
        bootstrap_samples=bootstrap_samples,
        tuning=locked, suites=dict(development=development, validation=validation),
        replay_cases=select_replay_cases(validation['records'], locked), branch_runs=len(records),
        patient_runs=sum(r['metrics']['patients'] for r in records), actual_system_comparison=False,
        validation_scope='new_seeds_new_priority_resource_schedules_shared_synthetic_locations', limitations=LIMITATIONS,
        scenario_specs=copy.deepcopy(dict(development=development_scenarios, validation=validation_scenarios)))


def _canonical_policy(spec):
    value = copy.deepcopy(spec)
    policy_id = value.pop('id')
    value.pop('label', None)
    return Policy(name=policy_id, **value).spec()


def validate_development_input(payload, development_scenarios, validation_scenarios, development_seeds, validation_seeds, sigma):
    """Verify stored scores and frozen winner without invoking candidate selection."""
    validate_seed_split(development_seeds, validation_seeds)
    _validate_scenarios(development_scenarios, validation_scenarios)
    if payload.get('experiment') != 'priority_protection_v1' or payload.get('schema_version') != 1 or payload.get('development_only') is not True:
        raise ValueError('priority development-only 결과만 재개할 수 있습니다.')
    suites = payload.get('suites', {})
    empty = suites.get('validation', {})
    if any(empty.get(key) != [] for key in ('records', 'summaries', 'overall_summary', 'policy_comparisons')) or payload.get('replay_cases') != []:
        raise ValueError('이미 검증한 기록이 있는 입력은 재개할 수 없습니다.')
    if payload.get('development_seeds') != list(development_seeds) or payload.get('validation_seeds') != list(validation_seeds):
        raise ValueError('저장된 개발·검증 seed와 현재 설정이 다릅니다.')
    if payload.get('previous_validation_seeds_excluded') != sorted(PREVIOUS_VALIDATION_SEEDS) or payload.get('service_lognormal_sigma') != sigma:
        raise ValueError('이전 seed 제외 범위나 서비스 분포 설정이 다릅니다.')
    if payload.get('scenario_specs') != dict(development=development_scenarios, validation=validation_scenarios):
        raise ValueError('저장된 개발·검증 시나리오와 현재 입력이 다릅니다.')
    records = suites.get('development', {}).get('records', [])
    expected = {(s['id'], seed, p['id']) for s in development_scenarios for seed in development_seeds for p in DEVELOPMENT_POLICIES}
    configs = {p['id']: _canonical_policy(p) for p in DEVELOPMENT_POLICIES}
    sources, seen = {s['id']: s for s in development_scenarios}, set()
    for row in records:
        key = (row.get('scenario_id'), row.get('seed'), row.get('policy_id'))
        if key not in expected or key in seen or row.get('split') != 'development':
            raise ValueError('개발 상황·seed·정책 coverage가 다르거나 중복됐습니다.')
        seen.add(key)
        source = sources[key[0]]
        if row.get('policy') != configs[key[2]] or row.get('metrics', {}).get('patients') != len(source['patients']) or row.get('case_kind') != source.get('case_kind') or row.get('family') != source.get('family', source.get('experiment_family', 'unknown')) or row.get('nonresponse_probability') != source.get('experiment_nonresponse', 0):
            raise ValueError('개발 후보 설정·환자 수·계열·무응답 조건이 다릅니다.')
        identities = sorted((o['id'], o['priority'], o['search_start_t']) for o in row['outcomes'])
        actual = sorted((p['id'], p.get('priority', 4), p['search_start_t']) for p in source['patients'])
        summary = priority_summary([row])['overall']
        if identities != actual or any(row['metrics'].get(k) != summary[k] for k in ('not_treated', 'not_arrived', 'start_observed', 'inquiries')):
            raise ValueError('개발 환자 식별·분모·사건 수와 원본 입력이 일치하지 않습니다.')
    if seen != expected or payload.get('branch_runs') != len(records) or payload.get('patient_runs') != sum(r['metrics']['patients'] for r in records):
        raise ValueError('개발 coverage 또는 실행 수 요약이 다릅니다.')
    locked = payload.get('tuning', {})
    if locked.get('selection_split') != 'development' or locked.get('validation_retuning') is not False or locked.get('v2_reference') != LOCKED_V2 or locked.get('objective_order') != OBJECTIVE_ORDER or locked.get('inquiry_penalty_minutes') != .25:
        raise ValueError('저장된 개발 잠금·V2·목적함수가 현재 계약과 다릅니다.')
    for group, choices, key in (('fixed', FIXED_POLICIES, 'selected_fixed_policy'), ('priority', PRIORITY_POLICIES, 'selected_priority_policy')):
        result, frozen = locked.get(group, {}), locked.get(key)
        scores = _candidate_scores(records, choices)
        winner_id = min(scores, key=_score_key)['policy_id']
        if frozen not in choices or frozen['id'] != winner_id or result.get('selected_policy') != frozen or result.get('scores') != scores or result.get('selection_split') != 'development' or result.get('validation_retuning') is not False or result.get('objective_order') != OBJECTIVE_ORDER:
            raise ValueError('저장된 개발 점수·선택 정책·잠금이 실행 기록과 일치하지 않습니다.')
    return copy.deepcopy(locked)


def resume_validation(development_payload, development_scenarios, validation_scenarios,
                      development_seeds=tuple(range(301, 309)), validation_seeds=tuple(range(5001, 5021)),
                      routing=None, sigma=.35, generator=generate_replay, bootstrap_samples=1000, progress=None):
    locked = validate_development_input(development_payload, development_scenarios, validation_scenarios, development_seeds, validation_seeds, sigma)
    validation = _run_conditions(validation_scenarios, validation_policies(locked), validation_seeds, 'validation',
        routing=routing, sigma=sigma, generator=generator, bootstrap_samples=bootstrap_samples, progress=progress)
    payload = copy.deepcopy(development_payload)
    payload.update(generated_at=datetime.now(timezone.utc).isoformat(), development_only=False, development_reexecuted=False,
        bootstrap_samples=bootstrap_samples,
        development_reselected=False, resumed_from_development_generated_at=development_payload.get('generated_at'))
    payload['suites']['validation'] = validation
    payload['replay_cases'] = select_replay_cases(validation['records'], locked)
    records = payload['suites']['development']['records'] + validation['records']
    payload.update(branch_runs=len(records), patient_runs=sum(r['metrics']['patients'] for r in records))
    return payload


def export_replay_cases(payload, scenarios):
    by_id, exported = {s['id']: s for s in scenarios}, []
    for selected in payload['replay_cases']:
        source = by_id[selected['scenario_id']]
        value = seed_workload_scenario(source, selected['seed'], nonresponse=source.get('experiment_nonresponse', 0), sigma=payload['service_lognormal_sigma'])
        value['experiment_environment'].update(kind='priority_protection_validation', prior_validation_seed_reused=False,
            policy_has_true_queue_state=False, future_patient_schedule_visible=False)
        value['id'] += f"_seed{selected['seed']}"
        value['label'] += f" · 우선순위 보호 비교 · seed {selected['seed']}"
        value['policies'] = validation_policies(payload['tuning'])
        value['experiment_selection'] = copy.deepcopy(selected)
        value['description'] = value.get('description', '') + (' 첫 검증 seed의 사전 지정 조건을 개선 여부와 무관하게 내보냈다. '
            '잠근 V2와 개발에서 잠근 우선순위 후보, 고정 문의 3종을 비교한다. 유리한 검증 결과를 고른 예시가 아니다.')
        exported.append(dict(scenario=value, **selected))
    return dict(schema_version=2, geometry_included=False, scenarios=exported)


def _fmt(value):
    return '—' if value is None else f'{value:.2f}'


def _interval(value):
    return '추정 불가' if value is None else f'[{value[0]:.2f}, {value[1]:.2f}]'


def report_markdown(payload):
    locked = payload['tuning']
    lines = ['# 우선순위 보호·관측 문의 정책 검증', '',
        f"개발 {len(payload['development_seeds'])} seed / 검증 {len(payload['validation_seeds'])} seed · {payload['branch_runs']}개 분기 · {payload['patient_runs']}회 환자 실행", '',
        '**합성 모형 내 정책 비교다. 실제 119 체계 대비 개선율·임상 효과·최적성 증명이 아니다.**', '']
    if payload.get('development_only'):
        lines += ['**개발 후보 선택만 완료했으며, 선택 정책의 신규 검증 효과는 아직 확인하지 않았다.**', '']
    else:
        suite = payload['suites']['validation']
        before = next(s for s in suite['overall_summary'] if s['policy_id'] == LOCKED_V2['id'])
        after = next(s for s in suite['overall_summary'] if s['policy_id'] == locked['selected_priority_policy']['id'])
        comparison = next(s for s in suite['policy_comparisons'] if s['policy_id'] == after['policy_id'])
        a, b = before['overall'], after['overall']
        high_a, high_b = before['by_priority'].get('4', {}), after['by_priority'].get('4', {})
        paired, high_paired = comparison['shared_patient_times']['all'], comparison['shared_patient_times']['4']
        lines += ['## 신규 검증 핵심 결과', '',
            f"잠근 V2 → **{locked['selected_priority_policy']['label']}**의 비교다. 시간은 탐색 시작부터 분이며 조건부 평균의 관측 환자와 공통 환자는 구분한다.", '',
            f"- 높은 우선순위4 미치료: **{high_a.get('not_treated', 0)}/{high_a.get('patients', 0)} → {high_b.get('not_treated', 0)}/{high_b.get('patients', 0)}명**.",
            f"- 전체 미치료: **{a['not_treated']}/{a['patients']} → {b['not_treated']}/{b['patients']}명**.",
            f"- 치료 시작 조건부 평균: 높은 군 {_fmt(high_a.get('start_mean_minutes'))} → {_fmt(high_b.get('start_mean_minutes'))}분, 전체 {_fmt(a['start_mean_minutes'])} → {_fmt(b['start_mean_minutes'])}분.",
            f"- 공통 환자 치료 시작 차이: 전체 **{_fmt(paired['mean_difference'])}분**, 95% 구간 {_interval(paired['interval_95'])} ({paired['common_patient_total']}명); 높은 군 {_fmt(high_paired['mean_difference'])}분, 95% 구간 {_interval(high_paired['interval_95'])} ({high_paired['common_patient_total']}명). 음수는 단축이다.",
            f"- 문의/환자: **{_fmt(a['inquiries_per_patient'])} → {_fmt(b['inquiries_per_patient'])}건**; 차이 {_fmt(comparison['inquiry_difference']['mean_difference'])}건, 95% 구간 {_interval(comparison['inquiry_difference']['interval_95'])}.", '']
        losses = []
        for priority, current in after['by_priority'].items():
            previous = before['by_priority'].get(priority, {})
            details = []
            for key, label in (('not_treated', '미치료'), ('not_arrived', '미도착')):
                if current[key] > previous.get(key, 0):
                    details.append(f"{label} {previous.get(key, 0)}/{previous.get('patients', 0)} → {current[key]}/{current['patients']}명")
            for key, label in (('start_mean_minutes', '치료 시작 조건부 평균'), ('start_p95_minutes', '치료 시작 조건부 P95')):
                old, new = previous.get(key), current.get(key)
                if old is not None and new is not None and new > old:
                    details.append(f"{label} {_fmt(old)} → {_fmt(new)}분")
            if details:
                losses.append(f"우선순위{priority}: " + ', '.join(details))
        if losses:
            lines += ['**군별 악화도 관측됐다.** ' + '; '.join(losses) + '. 치료를 시작한 환자 구성이 달라지므로 조건부 평균과 공통 환자 차이를 함께 해석한다.', '']
        low_paired = comparison['shared_patient_times'].get('1')
        if low_paired and low_paired['mean_difference'] is not None:
            lines += [f"낮은 군 공통 환자 {low_paired['common_patient_total']}명의 치료 시작 차이는 **{_fmt(low_paired['mean_difference'])}분**, 95% 구간 {_interval(low_paired['interval_95'])}다. 양수는 지연이며, 치료 미시작 환자를 제외한 단순 평균의 개선만으로 이 군의 이득을 주장하지 않는다.", '']
    lines += ['[중단·수정 이력](priority_protection_interrupted.json): 공동 배정 대기 중 중복 문의를 수정했다. 수정 후 검증에는 중단한 seed 4001~4020 전체를 제외한 새 seed를 사용한다.', '',
        '## 비교 설계', '',
        '이전 개발에서 선택한 V2(혼잡 가중치 1, 추가 문의 2분)는 고정한다. 고정 1/3/5곳, 잠근 V2, 우선순위 처리 4후보를 같은 개발 입력에 적용한다. 신규 요청만 우선순위순 처리 / 현재 환자 처리도 우선순위순 / 높은 군 추가 문의 1분 / 같은 시각 유효 응답의 사전식 공동 배정 R4를 비교한다.', '',
        '선택 순서는 ① 우선순위4 미치료 ② 전체 미치료 ③ 우선순위4 미도착 ④ 전체 미도착 ⑤ 우선순위4 조건부 치료 시작 평균 ⑥ 전체 조건부 평균+0.25×문의/환자다. 동률은 후보 순서로 결정한다. 우선순위 후보와 고정 문의 후보를 각각 잠갔으며 검증에서 재선택하지 않았다.', '',
        f"잠근 V2 기준: `{LOCKED_V2['id']}`  ",
        f"잠근 우선순위 후보: **{locked['selected_priority_policy']['label']}** (`{locked['selected_priority_policy']['id']}`)  ",
        f"잠근 고정 문의 후보: `{locked['selected_fixed_policy']['id']}`", '',
        '충분한 자원 담당1/2명·동시 자원 부족·낮은 군 먼저/높은 군 후발·희소 역량·배경 업무 변화·자원 변화·총자원 부족의 8계열에 무응답 0/20%를 교차했다. 개발 12명·검증 16명이며 서비스시간·환자 간격·배경 업무·외생 사건·관찰기간을 분리했다.', '',
        '이미 접수된 병원 문의는 FIFO를 유지하며 확정된 이송은 후발 환자를 위해 취소하지 않는다. 미래 환자를 예측하거나 자원을 미리 비워두는 정책이 아니다.', '',
        '개발 seed: ' + ', '.join(map(str, payload['development_seeds'])) + '.  ',
        '미사용 검증 seed: ' + ', '.join(map(str, payload['validation_seeds'])) + '.  ',
        '이전 holdout 1001~1020·2001~2020·3001~3020·4001~4020은 제외했다.', '',
        '## 개발 점수', '',
        '| 정책 | 높은 군 미치료 | 전체 미치료 | 높은 군 미도착 | 전체 미도착 | 높은 군 시작 평균 | 전체 시작 평균 | 문의/환자 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    rows = payload['suites']['development']['records']
    for p in DEVELOPMENT_POLICIES:
        s = score_policy([r for r in rows if r['policy_id'] == p['id']])
        lines.append(f"| {p['id']} | {s['priority4_not_treated']} | {s['not_treated']} | {s['priority4_not_arrived']} | {s['not_arrived']} | {_fmt(s['priority4_conditional_start_mean'])} | {_fmt(s['conditional_start_mean'])} | {_fmt(s['inquiries_per_patient'])} |")
    if payload.get('development_only'):
        lines += ['', '**현재는 개발 실행만 완료했다. 신규 검증 입력은 아직 실행하지 않았다.**', '']
    else:
        suite = payload['suites']['validation']
        lines += ['', '## 신규 검증: 분모와 우선순위별 결과', '',
            '시간 원점은 탐색 시작이며 단위는 분이다. 평균/P95/최장은 사건 관측 환자만의 조건부 값이다. P95는 최근접 순위 정의다. 미치료·미도착 환자를 0으로 바꾸지 않는다. 낮은 군의 손실과 전체 처리 수를 함께 본다.', '',
            '| 정책 | 군 | 치료 시작/전체 | 미치료 | 미도착 | 시작 평균 | 시작 P95 | 시작 최장 | 문의/환자 |',
            '|---|---|---:|---:|---:|---:|---:|---:|---:|']
        for item in suite['overall_summary']:
            for group, a in [('전체', item['overall']), *item['by_priority'].items()]:
                lines.append(f"| {item['policy_id']} | {group} | {a['start_observed']}/{a['patients']} | {a['not_treated']} | {a['not_arrived']} | {_fmt(a['start_mean_minutes'])} | {_fmt(a['start_p95_minutes'])} | {_fmt(a['start_max_minutes'])} | {_fmt(a.get('inquiries_per_patient'))} |")
        lines += ['', '## 잠근 V2와 공통 환자를 짝지은 차이', '',
            '정책−V2 차이이며 음수는 단축이다. 같은 seed의 모든 조건을 묶고 공통 환자별 차이를 평균한 뒤, 전체 seed 묶음을 재표집했다. 양쪽에서 치료한 환자가 달라질 수 있어 전체 조건부 평균 차이와 구분한다.', '',
            '| 정책 | 군 | 공통 환자/전체 | 시간 차이 | 95% 구간 | 시간 악화 seed |',
            '|---|---|---:|---:|---|---:|']
        for comparison in suite['policy_comparisons']:
            for group, s in comparison['shared_patient_times'].items():
                lines.append(f"| {comparison['policy_id']} | {group} | {s['common_patient_total']}/{s['patients']} | {_fmt(s['mean_difference'])} | {_interval(s['interval_95'])} | {s['regression_seed_count']}/{s['paired_seed_count']} |")
        lines += ['', '## 상황별 개선·악화', '',
            '| 조건 | 정책 | 높은 군 미치료 차이 | 전체 미치료 차이 | 공통 환자 시작 차이 | 시간 악화 seed |',
            '|---|---|---:|---:|---:|---:|']
        for case in suite['summaries']:
            for p in case['policies']:
                if p['id'] == LOCKED_V2['id']:
                    continue
                comparison = next(c for c in suite['policy_comparisons'] if c['policy_id'] == p['id'])
                details = [d for d in comparison['condition_seed_details'] if d['scenario_id'] == case['scenario_id']]
                shared = p['shared_patient_times']['treatment_start_t']
                lines.append(f"| {case['label']} | {p['id']} | {sum(d['priority4_not_treated_difference'] for d in details):+d} | {sum(d['not_treated_difference'] for d in details):+d} | {_fmt(shared['mean_difference'])} | {sum(d['common_start_mean_difference'] is not None and d['common_start_mean_difference'] > 0 for d in details)}/{len(details)} |")
        lines += ['', 'JSON에는 모든 조건·seed의 악화/개선과 군별 확정·도착·치료 시간 분모를 남겼다. 검증을 본 후 정책을 바꾸면 이 입력은 다음 검증에서 미사용 데이터가 아니다.', '',
            '## 문의·전원·조건 변화', '',
            '| 정책 | 문의/환자 | V2 대비 문의 차이 | 95% 구간 | 목적지 변경 | 전원 | 자원 불가 이송 환자·분 | R4 대체 계산 |',
            '|---|---:|---:|---|---:|---:|---:|---:|']
        for item, comparison in zip(suite['overall_summary'], suite['policy_comparisons']):
            a, d, q = item['overall'], item['diagnostics'], comparison['inquiry_difference']
            lines.append(f"| {item['policy_id']} | {_fmt(a['inquiries_per_patient'])} | {_fmt(q['mean_difference'])} | {_interval(q['interval_95'])} | {a['redirects']} | {a['transfers']} | {_fmt(d['resource_invalid_transit_minutes']['value'])} | {_fmt(d['joint_fallbacks']['value'])} |")
        lines += ['', '## 사전 고정 지도 예시', '']
        for case in payload['replay_cases']:
            lines.append(f"- {case['scenario_id']} / seed {case['seed']} / 공통 환자 치료 시작 차이 {_fmt(case['difference'])}분 / 공통 환자 {case['common_patients']}명")
        lines += ['', '첫 검증 seed의 동시 자원 부족 nr0·후발 높은 군 nr20·자원 변화 nr0 조건을 결과와 무관하게 보존한다.', '']
    lines += ['## 한계', ''] + [f'- {item}' for item in payload['limitations']] + ['']
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
    parser.add_argument('--development-input', type=Path)
    parser.add_argument('--service-sigma', type=float, default=.35)
    parser.add_argument('--bootstrap-samples', type=int, default=1000)
    parser.add_argument('--routing', choices=('synthetic', 'osrm'), default='synthetic')
    parser.add_argument('--offline', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--route-cache', type=Path, default=ROOT / 'simulation/route_cache')
    parser.add_argument('--output', type=Path, default=ROOT / 'simulation_results/priority_protection_corrected')
    parser.add_argument('--replay-output', type=Path, default=ROOT / 'simulation_results/priority_protection_corrected_replays.json')
    args = parser.parse_args(argv)
    output = args.output.with_name(args.output.name + '_development') if args.development_only else args.output
    destinations = (output.with_suffix('.json').resolve(), output.with_suffix('.md').resolve(), args.replay_output.resolve())
    if len(set(destinations)) != len(destinations):
        parser.error('JSON·보고서·지도 출력 경로는 서로 달라야 합니다.')
    if args.development_only and args.development_input is not None:
        parser.error('--development-only와 --development-input은 함께 사용할 수 없습니다.')
    if args.development_input is not None and args.development_input.resolve() in destinations:
        parser.error('개발 입력을 출력 파일로 덮어쓸 수 없습니다.')
    if not 1 <= args.development_seeds <= 1000 or not 1 <= args.validation_seeds <= 1000:
        parser.error('각 seed 수는 1~1000이어야 합니다.')
    if not math.isfinite(args.service_sigma) or args.service_sigma < 0:
        parser.error('--service-sigma는 0 이상의 유한한 수여야 합니다.')
    if not 100 <= args.bootstrap_samples <= 10000:
        parser.error('--bootstrap-samples는 100~10000이어야 합니다.')
    development_seeds = list(range(args.development_seed_start, args.development_seed_start + args.development_seeds))
    validation_seeds = list(range(args.validation_seed_start, args.validation_seed_start + args.validation_seeds))
    try:
        validate_seed_split(development_seeds, validation_seeds)
    except ValueError as error:
        parser.error(str(error))
    routing = RouteSnapshot(RoadRouteProvider(cache_dir=args.route_cache, offline=args.offline, allow_synthetic_fallback=False) if args.routing == 'osrm' else None)
    try:
        base = build_default_scenarios(routing=routing)[0]
        development, validation = (build_priority_scenarios(base, split) for split in ('development', 'validation'))
        def progress(suite, sid, seed):
            print(f'계산: {suite} / {sid} / seed {seed}', flush=True)
        if args.development_input is not None:
            try:
                saved = json.loads(args.development_input.read_text(encoding='utf-8'))
                if saved.get('routing', {}).get('provider') != args.routing:
                    raise ValueError('저장된 개발 이동시간 방식과 현재 방식이 다릅니다.')
                payload = resume_validation(saved, development, validation, development_seeds, validation_seeds,
                    routing=routing, sigma=args.service_sigma, bootstrap_samples=args.bootstrap_samples, progress=progress)
                payload['development_input'] = str(args.development_input.resolve())
            except (OSError, ValueError, TypeError, KeyError) as error:
                parser.error(f'개발 결과를 재개할 수 없습니다: {error}')
        else:
            payload = run_experiment(development, validation, development_seeds, validation_seeds,
                routing=routing, sigma=args.service_sigma, bootstrap_samples=args.bootstrap_samples,
                progress=progress, development_only=args.development_only)
        payload['routing'] = routing.summary()
        _write_json(output.with_suffix('.json'), payload)
        output.with_suffix('.md').write_text(report_markdown(payload), encoding='utf-8')
        if not args.development_only:
            _write_json(args.replay_output, export_replay_cases(payload, validation))
    except RoadRouteError as error:
        parser.exit(1, f'도로 캐시가 부족합니다: {error}\n기존 결과를 유지하고 종료했습니다.\n')
    print(f"고정: {payload['tuning']['selected_fixed_policy']['id']} / 우선순위: {payload['tuning']['selected_priority_policy']['id']} / {payload['branch_runs']}분기", flush=True)
    print(f'결과: {output.with_suffix(".json")}\n보고서: {output.with_suffix(".md")}')


if __name__ == '__main__':
    main()
