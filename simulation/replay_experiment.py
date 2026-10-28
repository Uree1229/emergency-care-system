"""같은 지도 재생 엔진으로 배정 규칙과 문의 정책을 반복 비교한다.

python -m simulation.replay_experiment --seeds 20 --offline

이 실행기는 실제 119 시스템을 복제하지 않는다. 도로/기관 위치 외 환자,
진료 자원, 응답 분포는 합성 입력이다. 기본 실행은 저장된 도로 캐시만 읽는다.
seed는 개발 비교용이며 별도 검증 집합 없이 최적 정책을 확정하지 않는다.
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
from .road_routes import RoadRouteError, RoadRouteProvider
from .sim import rng_for


INQUIRY_POLICIES = tuple(
    dict(id=f'R1_k{k}_w{wait}', label=f'예상 도착 우선 · {k}곳 · 대기 {wait}분',
         rule='R1', k=k, wait=wait, notify=True)
    for k, wait in ((3, 2), (1, 0), (1, 2), (3, 0), (5, 0), (5, 2))
)

# Time means are conditional on the relevant observed patients; their denominators
# are always exported beside them. Count/coverage comparisons come first.
METRICS = (
    ('unassigned', '미배정(명)', True),
    ('not_arrived', '최종 병원 미도착(명)', True),
    ('not_treated', '치료 미시작(명)', True),
    ('completed', '관찰 안에 치료 완료(명)', False),
    ('high_priority_unassigned', '높은 우선순위 미배정(명)', True),
    ('completion_coverage', '치료 완료 비율', False),
    ('inquiries_per_patient', '환자당 문의(건)', True),
    ('commit_mean_minutes', '탐색 시작→최초 확정 평균(분)', True),
    ('arrival_mean_minutes', '탐색 시작→최종 도착 평균(분)', True),
    ('arrival_max_minutes', '탐색 시작→최종 도착 최장(분)', True),
    ('start_mean_minutes', '탐색 시작→치료 시작 평균(분)', True),
    ('start_max_minutes', '탐색 시작→치료 시작 최장(분)', True),
    ('redirects', '목적지 변경(건)', True),
    ('transfers', '전원(건)', True),
)

LIMITATIONS = [
    '실제 기관 위치·자동차 도로 예상시간을 사용하지만 환자와 진료역량·자원·응답은 합성 설정이다.',
    '응답 지연은 병원별 설정값을 중앙값으로 한 로그정규분포(σ=0.7), 미응답은 설정 확률이다. 실제 병원 응답 분포를 추정한 값이 아니다.',
    '같은 seed·환자·병원·문의 회차의 잠재 응답을 모든 정책에 공유한다. 정책은 아직 받지 않은 미래 응답을 볼 수 없다.',
    '문의량에 따른 병원의 응답 지연 증가·응답률 개선을 모델링하지 않는다. 문의 감소를 업무시간 감소나 응답률 개선으로 환산할 수 없다.',
    '평균 확정·도착·치료 시작 시간은 해당 사건이 관측된 환자만 포함한다. 미배정·미도착·치료 완료 수와 분모를 먼저 확인해야 한다.',
    '짝지은 평균 차이의 95% 부트스트랩 구간은 이 합성 시나리오의 seed 변동만 나타낸다. 임상 효과나 실제 시스템 대비 효과 구간이 아니다.',
    '모든 seed는 개발 비교용이다. 이 결과에서 선택한 정책은 미사용 시나리오·seed에서 추가 검증해야 하며 전역 최적성을 주장하지 않는다.',
    'R3는 작은 동시 결정 묶음을 열거한다. 큰 묶음에서는 R2로 대체될 수 있으며 이 결과는 CP-SAT 솔버의 최적성 증명이 아니다.',
    '분 단위 계산·일반 자동차 시간·누적거리 비례 재생이다. 실시간 교통과 구급차 우선주행은 반영하지 않는다.',
]


def seeded_scenario(scenario, seed, nonresponse=0.2, sigma=0.7):
    """Create an immutable, keyed potential-response environment for one seed.

    Enough attempts are prepared for every simulated minute. The same attempt
    index, rather than policy-dependent wall-clock time, identifies a response.
    """
    if not 0 <= nonresponse <= 1 or not math.isfinite(nonresponse):
        raise ValueError('미응답 확률은 0~1이어야 합니다.')
    if not math.isfinite(sigma) or sigma < 0:
        raise ValueError('응답 분포 σ는 0 이상이어야 합니다.')
    value = copy.deepcopy(scenario)
    value['seed'] = int(seed)
    original = scenario.get('response_environment', {})
    attempts = int(value.get('max_minutes', 180)) + 2
    environment = {}
    for patient in value['patients']:
        pid = patient['id']
        environment[pid] = {}
        for hospital in value['hospitals']:
            hid = hospital['id']
            entry = original.get(pid, {}).get(hid, original.get('*', {}).get(hid, {}))
            median = max(1, float(entry.get('delay', 2)))
            potential = []
            for attempt in range(1, attempts + 1):
                random = rng_for(seed, 'map_response_experiment', pid, hid, attempt)
                silent = random.random() < nonresponse
                delay = max(1, math.ceil(random.lognormvariate(math.log(median), sigma)))
                potential.append(dict(delay=delay, silent=silent))
            environment[pid][hid] = dict(attempts=potential)
    value['response_environment'] = environment
    value['experiment_environment'] = dict(seed=int(seed), nonresponse=nonresponse,
        delay_distribution='ceil(lognormal(log(base_delay), sigma))', sigma=sigma,
        response_key=['seed', 'patient_id', 'hospital_id', 'attempt'],
        actual_response_distribution=False,
        base_fixed_silence_replaced=True,
        base_delay_interpreted_as_lognormal_median=True)
    return value


def _mean(values):
    return statistics.mean(values) if values else None


def branch_record(scenario, branch):
    """Drop trajectory geometry and retain counts, time denominators and outcomes."""
    specs = {patient['id']: patient for patient in scenario['patients']}
    outcomes = copy.deepcopy(branch['patient_outcomes'])
    commits, arrivals, starts = [], [], []
    for outcome in outcomes:
        spec = specs[outcome['id']]
        start = spec['search_start_t']
        for field, target in (('first_commit_t', commits), ('final_arrive_t', arrivals),
                              ('treatment_start_t', starts)):
            if outcome.get(field) is not None:
                target.append(outcome[field] - start)
        outcome['priority'] = spec.get('priority', 4)
        outcome['search_start_t'] = start
    n = len(outcomes)
    metrics = dict(patients=n,
        unassigned=sum(outcome.get('first_commit_t') is None for outcome in outcomes),
        not_arrived=sum(outcome.get('final_arrive_t') is None for outcome in outcomes),
        not_treated=sum(outcome.get('treatment_start_t') is None for outcome in outcomes),
        completed=sum(outcome['state'] == 'COMPLETE' for outcome in outcomes),
        high_priority_unassigned=sum(outcome['priority'] == 4 and outcome.get('first_commit_t') is None
                                     for outcome in outcomes),
        inquiries=sum(outcome.get('inquiries', 0) for outcome in outcomes),
        redirects=sum(outcome.get('redirects', 0) for outcome in outcomes),
        transfers=sum(outcome.get('transfers', 0) for outcome in outcomes),
        commit_observed=len(commits), arrival_observed=len(arrivals), start_observed=len(starts),
        commit_mean_minutes=_mean(commits), arrival_mean_minutes=_mean(arrivals),
        arrival_max_minutes=max(arrivals, default=None), start_mean_minutes=_mean(starts),
        start_max_minutes=max(starts, default=None),
        joint_fallbacks=branch.get('metrics', {}).get('joint_fallbacks', 0))
    metrics['inquiries_per_patient'] = metrics['inquiries'] / n if n else None
    metrics['completion_coverage'] = metrics['completed'] / n if n else None
    priority = {}
    for group in (4, 2, 1):
        members = [outcome for outcome in outcomes if outcome['priority'] == group]
        observed = [outcome['treatment_start_t'] - outcome['search_start_t']
                    for outcome in members if outcome.get('treatment_start_t') is not None]
        priority[str(group)] = dict(total=len(members), start_observed=len(observed),
            unassigned=sum(outcome.get('first_commit_t') is None for outcome in members),
            start_max_minutes=max(observed, default=None))
    return dict(policy_id=branch['id'], label=branch['label'], policy=copy.deepcopy(branch['policy']),
                metrics=metrics, priority=priority, outcomes=outcomes)


def bootstrap_mean_interval(values, key, samples=1000):
    """Deterministic percentile bootstrap over paired seed differences."""
    if len(values) < 2:
        return None
    random = rng_for(71011, 'map_bootstrap', key)
    n = len(values)
    draws = sorted(sum(values[random.randrange(n)] for _ in range(n)) / n for _ in range(samples))
    return [draws[int((samples - 1) * .025)], draws[int((samples - 1) * .975)]]


def paired_summary(baseline, comparison, key, lower=True, samples=1000):
    """Rows must refer to the same seed and scenario; missing values stay missing."""
    by_seed = {row['seed']: row for row in baseline}
    differences = []
    for row in comparison:
        base = by_seed.get(row['seed'])
        if base is None:
            continue
        before, after = base['metrics'].get(key), row['metrics'].get(key)
        if before is not None and after is not None:
            differences.append((row['seed'], after - before))
    values = [value for _, value in differences]
    worst = max(differences, key=lambda item: item[1] if lower else -item[1]) if differences else None
    return dict(mean_difference=_mean(values), paired_seed_count=len(values),
        total_comparison_seeds=len(comparison), lower_is_better=lower,
        interval_95=bootstrap_mean_interval(values, key, samples), interval_method='paired_seed_percentile_bootstrap',
        regression_fraction=(sum(value > 0 if lower else value < 0 for value in values) / len(values)) if values else None,
        worst_seed=worst[0] if worst else None, worst_difference=worst[1] if worst else None)


def shared_patient_time_difference(baseline, comparison, field):
    """Avoid a change of patient coverage masquerading as a time improvement."""
    left = {outcome['id']: outcome for outcome in baseline['outcomes']}
    values = []
    for outcome in comparison['outcomes']:
        before = left.get(outcome['id'])
        if before and before.get(field) is not None and outcome.get(field) is not None:
            values.append((outcome[field] - outcome['search_start_t'])
                          - (before[field] - before['search_start_t']))
    return dict(mean_difference=_mean(values), common_patients=len(values),
                baseline_patients=len(left), comparison_patients=len(comparison['outcomes']))


def summarize(records, baseline_id, samples=1000):
    grouped = {}
    for record in records:
        grouped.setdefault(record['scenario_id'], {}).setdefault(record['policy_id'], []).append(record)
    summaries = []
    for scenario_id, policies in grouped.items():
        baseline = policies[baseline_id]
        policy_results = []
        for policy_id, rows in policies.items():
            aggregates = {}
            paired = {}
            for key, label, lower in METRICS:
                values = [row['metrics'][key] for row in rows if row['metrics'][key] is not None]
                aggregates[key] = dict(label=label, mean=_mean(values), seeds_observed=len(values),
                                       seeds_total=len(rows))
                paired[key] = paired_summary(baseline, rows, key, lower, samples)
            denominator_totals = {key: sum(row['metrics'][key] for row in rows)
                                  for key in ('patients', 'commit_observed', 'arrival_observed', 'start_observed')}
            base_index = {row['seed']: row for row in baseline}
            shared = {}
            for field in ('first_commit_t', 'final_arrive_t', 'treatment_start_t'):
                parts = [dict(seed=row['seed'], **shared_patient_time_difference(base_index[row['seed']], row, field))
                         for row in rows]
                valid = [part['mean_difference'] for part in parts if part['mean_difference'] is not None]
                shared[field] = dict(mean_difference=_mean(valid), paired_seed_count=len(valid),
                    common_patient_total=sum(part['common_patients'] for part in parts),
                    interval_95=bootstrap_mean_interval(valid, f'{scenario_id}:{policy_id}:{field}', samples),
                    seed_details=parts)
            policy_results.append(dict(id=policy_id, label=rows[0]['label'], policy=rows[0]['policy'],
                aggregate=aggregates, denominators=denominator_totals, paired_vs_baseline=paired,
                shared_patient_times=shared,
                joint_fallbacks=sum(row['metrics']['joint_fallbacks'] for row in rows)))
        summaries.append(dict(scenario_id=scenario_id, label=baseline[0]['scenario_label'],
                              baseline_id=baseline_id, policies=policy_results))
    return summaries


def run_batch(scenarios, policies, seeds, routing=None, nonresponse=.2,
              generator=generate_replay, progress=None, bootstrap_samples=1000):
    """Only the common replay engine makes decisions; no second simulator exists."""
    if not policies or not seeds or not scenarios:
        raise ValueError('시나리오·정책·seed가 각각 하나 이상 필요합니다.')
    if len({policy['id'] for policy in policies}) != len(policies):
        raise ValueError('정책 id가 중복됐습니다.')
    if len(set(seeds)) != len(seeds):
        raise ValueError('seed가 중복됐습니다.')
    records = []
    for scenario in scenarios:
        for seed in seeds:
            experiment = seeded_scenario(scenario, seed, nonresponse=nonresponse)
            replay = generator(experiment, policies=policies, routing=routing)
            actual = replay['scenario']
            for branch in replay['branches']:
                record = branch_record(actual, branch)
                record.update(scenario_id=scenario['id'], scenario_label=scenario['label'], seed=seed)
                records.append(record)
            if progress:
                progress(scenario['id'], seed)
    return dict(records=records, summaries=summarize(records, policies[0]['id'], bootstrap_samples))


def select_replay_cases(records, baseline_id='R0'):
    """Pick visible best/worst arrival differences among equal-coverage runs."""
    baseline = {(r['scenario_id'], r['seed']): r for r in records if r['policy_id'] == baseline_id}
    candidates = []
    for row in records:
        base = baseline.get((row['scenario_id'], row['seed']))
        if row['policy_id'] == baseline_id or base is None:
            continue
        a, b = base['metrics'], row['metrics']
        if any(a[key] != b[key] for key in ('unassigned', 'not_arrived', 'completed')):
            continue
        shared = shared_patient_time_difference(base, row, 'final_arrive_t')
        difference = shared['mean_difference']
        if difference is not None:
            candidates.append(dict(scenario_id=row['scenario_id'], seed=row['seed'],
                                   policy_id=row['policy_id'], difference=difference))
    if not candidates:
        return []
    selections = []
    for kind, selected in (('best_observed', min(candidates, key=lambda r: r['difference'])),
                           ('worst_observed', max(candidates, key=lambda r: r['difference']))):
        if not any((item['scenario_id'], item['seed']) == (selected['scenario_id'], selected['seed']) for item in selections):
            selections.append(dict(selected, selection=kind))
    return selections


def _format(value):
    return '—' if value is None else f'{value:.2f}'


def report_markdown(payload):
    lines = ['# 지도 시뮬레이션 알고리즘 반복 비교', '',
        f"개발 seed {len(payload['seeds'])}개 · 미응답 확률 {payload['nonresponse']:.0%} · 도로 모드 {payload['routing']['provider']}", '',
        '모든 값은 아래 합성 시나리오의 실험 결과다. 실제 시스템 대비 개선율이나 최적성 증명이 아니다.', '',
        '## 실험 조건', '',
        '배정 규칙 비교는 R0~R3 모두 3곳 동시 문의·최대 2분 추가 대기·동일 변경 알림을 사용한다. 문의 정책 비교는 R1을 고정하고 k=1/3/5, wait=0/2를 별도로 비교한다.', '',
        '시간 원점은 119 도착 후 현장처치가 끝나 병원 탐색을 시작한 시점이다. 치료 완료는 합성 자원 점유가 종료된 뜻이며 임상 예후가 아니다.', '']
    for name, suite in payload['suites'].items():
        lines += [f'## {"배정 규칙" if name == "assignment_rules" else "문의 정책"} 비교', '']
        for summary in suite['summaries']:
            lines += [f"### {summary['label']}", '',
                '표의 값은 seed별 지표의 평균이다. 도착 최장은 각 seed의 최장 도착시간을 평균한 값이다. 시간 단위는 분이다.', '',
                '| 정책 | 미배정 | 미도착 | 완료 | 고우선 미배정 | 문의/환자 | 확정 평균 | 도착 평균 | 도착 최장 |',
                '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
            keys = ('unassigned', 'not_arrived', 'completed', 'high_priority_unassigned', 'inquiries_per_patient',
                    'commit_mean_minutes', 'arrival_mean_minutes', 'arrival_max_minutes')
            for policy in summary['policies']:
                values = [_format(policy['aggregate'][key]['mean']) for key in keys]
                lines.append(f"| {policy['label']} | " + ' | '.join(values) + ' |')
            lines += ['', f"아래 차이는 정책 − 기준({summary['baseline_id']})이다. 도착시간은 양쪽 모두 도착한 같은 환자만 짝지었다.", '',
                '| 정책 | 도착 차이(분) | 95% 구간 | 공통 도착 환자/전체 | 문의 차이 | 문의 증가 seed 비율 | 최악 seed(문의 차이) |',
                '|---|---:|---|---:|---:|---:|---|']
            for policy in summary['policies']:
                shared = policy['shared_patient_times']['final_arrive_t']
                inquiry = policy['paired_vs_baseline']['inquiries_per_patient']
                interval = shared['interval_95']
                interval_text = '추정 불가' if interval is None else f"[{interval[0]:.2f}, {interval[1]:.2f}]"
                denominator = policy['denominators']
                fraction = inquiry['regression_fraction']
                lines.append(f"| {policy['label']} | {_format(shared['mean_difference'])} | {interval_text} | "
                    f"{shared['common_patient_total']}/{denominator['patients']} | {_format(inquiry['mean_difference'])} | "
                    f"{'—' if fraction is None else f'{fraction:.0%}'} | {inquiry['worst_seed']} ({_format(inquiry['worst_difference'])}) |")
            lines += ['', '시간 평균은 사건 관측 환자에 대한 조건부 평균이다. JSON에 정책별 확정·도착·치료 시작 분모, 우선순위별 최장 지연, seed별 결과와 모든 지표의 짝지은 차이를 저장했다.', '']
    lines += ['## 관측된 개선·악화 사례', '']
    for selected in payload.get('replay_cases', []):
        lines.append(f"- {selected['selection']}: {selected['scenario_id']} / seed {selected['seed']} / {selected['policy_id']} / 같은 환자 도착 차이 {selected['difference']:.2f}분")
    lines += ['', '이 사례는 설명용으로 결과를 보고 골랐다. 대표 사례나 일반적인 효과의 근거로 단독 사용하지 않는다.', '', '## 해석의 한계', '']
    lines += [f'- {limitation}' for limitation in payload['limitations']]
    lines += ['', '## 다음 검증', '',
        '이 개발 비교로 후보를 좁힌 뒤 목적과 정책을 고정하고, 미사용 seed·환자 위치·자원 밀도·응답률에서 평가한다. 문의 부담 감소와 선정·도착 지연 사이의 균형을 먼저 확인하고, 실제 운영자료 또는 현장 검토로 입력 가정을 점검한다.', '']
    return '\n'.join(lines)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + '.tmp')
    staged.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    staged.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=20, help='개발 비교 seed 수(1부터 시작)')
    parser.add_argument('--routing', choices=('osrm', 'synthetic'), default='osrm')
    parser.add_argument('--offline', action=argparse.BooleanOptionalAction, default=True,
                        help='기본값: 캐시만 읽기. --no-offline 지정 시에만 외부 도로 조회 허용')
    parser.add_argument('--route-cache', type=Path, default=ROOT / 'simulation/route_cache')
    parser.add_argument('--output', type=Path, default=ROOT / 'simulation_results/map_comparison',
                        help='결과 파일의 접두어(.json, .md 생성)')
    parser.add_argument('--replay-output', type=Path, help='개선·악화 설명용 사례의 지도 재생 JSON 저장')
    parser.add_argument('--nonresponse', type=float, default=.2, help='합성 미응답 확률. 실제 추정값 아님')
    args = parser.parse_args(argv)
    if args.seeds < 1 or args.seeds > 1000:
        parser.error('--seeds는 1~1000이어야 합니다.')
    if not math.isfinite(args.nonresponse) or not 0 <= args.nonresponse <= 1:
        parser.error('--nonresponse는 0~1이어야 합니다.')
    from .multi_scenarios import COMPARISON_POLICIES, build_multi_scenarios
    provider = RoadRouteProvider(cache_dir=args.route_cache, offline=args.offline,
                                 allow_synthetic_fallback=False) if args.routing == 'osrm' else None
    routing = RouteSnapshot(provider)
    seeds = list(range(1, args.seeds + 1))
    try:
        base = build_default_scenarios(routing=routing)[0]
        scenarios = build_multi_scenarios(base)
        def progress(scenario_id, seed):
            if seed == 1 or seed == args.seeds or seed % 5 == 0:
                print(f'계산: {scenario_id} / seed {seed}/{args.seeds}', flush=True)
        suites = dict(assignment_rules=run_batch(scenarios, COMPARISON_POLICIES, seeds,
                      routing, args.nonresponse, progress=progress),
                      inquiry_policies=run_batch(scenarios, INQUIRY_POLICIES, seeds,
                      routing, args.nonresponse, progress=progress))
        selected = select_replay_cases(suites['assignment_rules']['records'], COMPARISON_POLICIES[0]['id'])
        payload = dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
            development_only=True, seeds=seeds, nonresponse=args.nonresponse, routing=routing.summary(),
            limitations=LIMITATIONS, suites=suites, replay_cases=selected)
        if args.replay_output:
            lookup = {scenario['id']: scenario for scenario in scenarios}
            exported = []
            for selected_case in selected:
                scenario = seeded_scenario(lookup[selected_case['scenario_id']], selected_case['seed'], args.nonresponse)
                scenario['id'] = f"experiment_{scenario['id']}_seed{selected_case['seed']}"
                scenario['label'] += f" · seed {selected_case['seed']} ({selected_case['selection']})"
                scenario['experiment_selection'] = selected_case
                exported.append(generate_replay(scenario, policies=COMPARISON_POLICIES, routing=routing))
            _write_json(args.replay_output, dict(schema_version=2, scenarios=exported))
    except RoadRouteError as error:
        parser.exit(1, f'도로 캐시가 부족하거나 유효하지 않습니다: {error}\n외부 조회 없이 종료했으며 기존 결과는 유지됩니다.\n')
    json_path = args.output.with_suffix('.json')
    md_path = args.output.with_suffix('.md')
    _write_json(json_path, payload)
    md_path.write_text(report_markdown(payload), encoding='utf-8')
    print(f'결과 저장: {json_path}\n보고서: {md_path}')


if __name__ == '__main__':
    main()
