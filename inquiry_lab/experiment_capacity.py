"""8차 실험: 병원 문의 처리 용량 설계 — 보조 담당 호출 규칙. 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_capacity development --output simulation_results/inquiry_lab_capacity_development_20261021
python -m inquiry_lab.experiment_capacity validation --lock simulation_results/inquiry_lab_capacity_development_20261021_lock.json \
    --output simulation_results/inquiry_lab_capacity_validation_20261021

사전 고정:
- 질문: 서버가 병원별 미회신 문의 수를 보고 보조 담당을 부르면, 상시 2명 효과(7차 진단 −57초/환자)의
  얼마를 얼마의 보조 근무로 얻는가.
- 환경: 1차와 같은 보정 혼합. 새 seed만 사용: 개발 1120001–1120240·8유형 1140001–1140003,
  검증 1130001–1130400·8유형 1150001–1150006.
- 문의 정책: eat_l15_f(1차 잠금, 선택 기준)와 fixed_3(운영 기본, 참고). 정책은 바꾸지 않는다.
- 용량 규칙(capacity_runner): base, min2(상시 2명), inf(상한 진단), 호출 규칙 od_k{k}_d{d}_l{L}.
  k∈{2,3,4}(병원의 미회신 문의 수), d∈{0,30,60,120}초(합류 지연), L∈{0,120}초(복귀 전 대기, d=0은 L=0만).
- 포착률 = ΔJ(규칙 − base) / ΔJ(min2 − base). 혼합 환경, 환자 가중, 시나리오 단위 부트스트랩 구간.
- 선택(EAT λ15): 합류 지연 60초 가정(d=60) 규칙 중 포착률 ≥ 0.75에서 환자당 보조 근무시간 최소
  (동률이면 큰 k, 작은 L). 없으면 포착률 최대 규칙을 '기준 미달'로 표시하고 같은 방식으로 검증한다.
- 판정: 검증 혼합에서 (1) ΔJ(선택 − base) 95% 구간 상한 < 0. (2) 포착률 점추정 ≥ 0.75는 실무 기준으로 함께 판정한다.
  같은 k·L의 d=0·30·120초 민감도, fixed_3 결과, 도착 형태별 결과, 정책 차이(EAT − fixed_3)의 용량별 변화는 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import random
import time

from . import capacity_runner, experiment as base, harness, scenarios

DEV_MIX = tuple(range(1120001, 1120241))
DEV_FAMILY = (1140001, 1140002, 1140003)
VAL_MIX = tuple(range(1130001, 1130401))
VAL_FAMILY = (1150001, 1150002, 1150003, 1150004, 1150005, 1150006)
POLICIES = ('fixed_3', 'eat_l15_f')
BASE_POLICY = 'eat_l15_f'
REFERENCE_RULES = ('base', 'min2', 'inf')
CENTRAL_DELAY = 60
CAPTURE_TARGET = 0.75
GRID = tuple([capacity_runner.on_demand_name(k, 0, 0) for k in (2, 3, 4)] +
             [capacity_runner.on_demand_name(k, d, l) for d in (30, 60, 120) for k in (2, 3, 4) for l in (0, 120)])
DEV_RULES = REFERENCE_RULES + GRID
SOURCE_FILES = base.SOURCE_FILES + ('inquiry_lab/capacity_runner.py', 'inquiry_lab/experiment_capacity.py')
SELECTION_RULE = ('혼합 개발(EAT λ15)에서 합류 지연 60초 규칙 중 포착률 ≥ 0.75인 규칙의 환자당 보조 근무시간 최소'
                  '(동률이면 큰 k, 작은 L). 없으면 포착률 최대 규칙을 기준 미달로 표시하고 검증한다.')
SUCCESS_RULE = ('검증 혼합(EAT λ15)에서 ΔJ(선택 규칙 − base)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0이면 효과 확인. '
                '포착률 점추정 ≥ 0.75이면 실무 기준 충족.')
LIMITATIONS = [
    '보조 담당의 합류 지연(0·30·60·120초)은 실측값이 아닌 가정이다. 병원 내 다른 업무를 멈추는 비용은 근무시간으로만 셌다.',
    '보조 담당도 원래 담당과 같은 처리시간·같은 수용 판단을 낸다고 가정했다. 숙련도 차이는 없다.',
    '호출 기준은 서버가 볼 수 있는 미회신 문의 수다. 병원 화면의 실제 대기 건수(병원만 아는 값)를 쓰는 규칙은 비교하지 않았다.',
    '시나리오는 환자 발생이 있는 짧은 구간이다. 상시 2명의 실제 비용(24시간 근무)과 호출형의 비용을 같은 단위로 비교하지 않는다.',
    '문의 정책(EAT)은 처리 인원 1명을 가정한 대기 모형을 그대로 쓴다. 용량을 아는 정책 조정은 범위 밖이다.',
]


def arm(policy, rule):
    return f'{policy}|{rule}'


def arms_for(rules, policies=POLICIES):
    return [(p, r) for p in policies for r in rules]


def compact(branch):
    m = branch['metrics']
    return dict(policy=arm(branch['policy'], branch['rule']),
                patients=[dict(id=p['id'], created_at=p['created_at'], failed=p['failed'],
                               selection_seconds=p['selection_seconds'],
                               estimated_arrival_seconds=p['estimated_arrival_seconds'],
                               travel_seconds=p['travel_seconds'], destination=p['destination_hospital_id'],
                               inquiry_count=p['inquiry_count'])
                          for p in branch['patients']],
                review_work_seconds=m['review_work_seconds'], review_started_count=m['review_started_count'],
                cancelled_before_start_count=m['cancelled_before_start_count'],
                reply_suppressed_count=m['reply_suppressed_count'], backup=branch['backup'])


def run_case(task):
    kind, seed, arm_list = task
    harness.install([p for p, _ in arm_list if p.startswith('eat_')])
    scenario = harness.build(kind, seed)
    result = capacity_runner.run_scenario(scenario, arm_list)
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={arm(b['policy'], b['rule']): compact(b) for b in result['branches']})


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


# ---------------------------------------------------------------- 집계

def backup_metrics(results, name):
    patients = sum(r['patient_count'] for r in results)
    total = {k: sum(r['branches'][name]['backup'][k] for r in results)
             for k in ('calls', 'false_alarms', 'reviews', 'busy_seconds', 'on_duty_seconds')}
    return dict(calls_per_100_patients=100 * total['calls'] / patients,
                false_alarm_share=total['false_alarms'] / total['calls'] if total['calls'] else None,
                backup_reviews_per_patient=total['reviews'] / patients,
                busy_seconds_per_patient=total['busy_seconds'] / patients,
                on_duty_seconds_per_patient=total['on_duty_seconds'] / patients,
                utilization=total['busy_seconds'] / total['on_duty_seconds'] if total['on_duty_seconds'] else None)


def _scenario_delta(r, a, b, penalty=base.PRIMARY_PENALTY):
    ours = {p['id']: p for p in r['branches'][a]['patients']}
    theirs = {p['id']: p for p in r['branches'][b]['patients']}
    return sum(harness.patient_cost(ours[pid], penalty) - harness.patient_cost(theirs[pid], penalty) for pid in ours)


def capture(results, policy, rule):
    """포착률 = Σ(규칙 − base) / Σ(min2 − base). 시나리오 단위 부트스트랩 95% 구간."""
    rows = [(_scenario_delta(r, arm(policy, rule), arm(policy, 'base')),
             _scenario_delta(r, arm(policy, 'min2'), arm(policy, 'base'))) for r in results]
    num, den = sum(a for a, _ in rows), sum(b for _, b in rows)
    if den >= 0:
        return dict(capture=None, ci95=[None, None], note='상시 2명 효과가 없어 포착률을 정의하지 않는다.')
    rng = random.Random(base.BOOTSTRAP_SEED)
    stats = []
    for _ in range(base.BOOTSTRAP_RESAMPLES):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        d = sum(b for _, b in sample)
        stats.append(sum(a for a, _ in sample) / d if d < 0 else 0.0)
    stats.sort()
    return dict(capture=num / den, ci95=[stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats)) - 1]])


def rule_rows(results, policy, rules):
    """규칙별 J·ΔJ(규칙 − base)·포착률·보조 근무 지표."""
    out = {}
    base_J = base.policy_metrics(results, arm(policy, 'base'))['J']
    for rule in rules:
        name = arm(policy, rule)
        m = base.policy_metrics(results, name)
        row = dict(J=m['J'], failed=m['failed'], avoidable_failed=m['avoidable_failed'],
                   inquiries_per_patient=m['inquiries_per_patient'],
                   review_work_seconds_per_patient=m['review_work_seconds_per_patient'],
                   backup=backup_metrics(results, name))
        if rule != 'base':
            row['paired_vs_base'] = base.paired(results, name, arm(policy, 'base'))
        if rule not in ('base', 'min2', 'inf'):
            row['capture'] = capture(results, policy, rule)
            on_duty = row['backup']['on_duty_seconds_per_patient']
            row['J_saved_per_backup_minute'] = 60 * (base_J - m['J']) / on_duty if on_duty else None
        out[rule] = row
    return out


def evaluate(results, rules):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] != 'mix']
    names = [arm(p, r) for p, r in arms_for(rules)]
    report = dict(mix=dict(metrics={n: base.policy_metrics(mix, n) for n in names},
                           sensitivity={str(k): {n: base.policy_metrics(mix, n, k)['J'] for n in names}
                                        for k in base.PENALTIES},
                           lower_bound=base.lower_bound(mix),
                           rules={p: rule_rows(mix, p, rules) for p in POLICIES}),
                  families=base.family_table(fam, names))
    report['by_arrival'] = {}
    for a in ('burst', 'stream'):
        subset = [r for r in mix if r['mix']['arrival'] == a]
        if subset:
            report['by_arrival'][a] = {BASE_POLICY: rule_rows(subset, BASE_POLICY, rules)}
    report['policy_gap'] = {r: base.paired(mix, arm('eat_l15_f', r), arm('fixed_3', r)) for r in REFERENCE_RULES}
    return report


def select(rule_table):
    """rule_table: EAT λ15의 rule_rows 결과."""
    central = [r for r in GRID if capacity_runner.parse_rule(r)['delay'] == CENTRAL_DELAY]

    def key(r):
        spec = capacity_runner.parse_rule(r)
        return (round(rule_table[r]['backup']['on_duty_seconds_per_patient'], 9), -spec['k'], spec['linger'])

    feasible = [r for r in central if (rule_table[r]['capture']['capture'] or 0) >= CAPTURE_TARGET]
    if feasible:
        return dict(rule=min(feasible, key=key), target_met=True, feasible=feasible)
    best = max(central, key=lambda r: ((rule_table[r]['capture']['capture'] or 0), [-x for x in key(r)]))
    return dict(rule=best, target_met=False, feasible=[])


def sensitivity_rules(rule):
    spec = capacity_runner.parse_rule(rule)
    return tuple(capacity_runner.on_demand_name(spec['k'], d, spec['linger']) for d in (0, 30, 120))


# ---------------------------------------------------------------- 보고서

def _f(value, digits=1):
    if value is None:
        return '—'
    return f'{value:,.{digits}f}'


def _ci(pair, digits=1):
    return f'[{_f(pair[0], digits)}, {_f(pair[1], digits)}]'


def reference_table(metrics):
    lines = ['| 정책 · 용량 | J(초) | 실패 | 피할 수 있던 실패 | 환자당 문의 | 환자당 검토 업무(초) |', '|---|---:|---:|---:|---:|---:|']
    for p in POLICIES:
        for r in REFERENCE_RULES:
            m = metrics[arm(p, r)]
            lines.append(f"| {p} · {r} | {_f(m['J'])} | {m['failed']} | {m['avoidable_failed']} | "
                         f"{_f(m['inquiries_per_patient'], 2)} | {_f(m['review_work_seconds_per_patient'])} |")
    return '\n'.join(lines)


def _pct(value):
    return '—' if value is None else f'{100 * value:.0f}%'


def rule_table(rows, rules):
    lines = ['| 규칙 | J(초) | ΔJ vs base [95% 구간] | 포착률 [95% 구간] | 호출/환자100 | 헛호출 | 보조 근무(초/환자) | 보조 검토(초/환자) | 근무 1분당 J 단축(초) |',
             '|---|---:|---|---|---:|---:|---:|---:|---:|']
    for rule in rules:
        row = rows[rule]
        pv, cap, b = row.get('paired_vs_base'), row.get('capture'), row['backup']
        delta = f"{_f(pv['delta_J_mean'])} {_ci(pv['ci95'])}" if pv else '—'
        if rule == 'min2':
            cells = ['100%(기준)', '—', '—', '상시', _f(b['busy_seconds_per_patient']), '—']
        elif rule == 'inf':
            cells = ['—', '—', '—', '—', '—', '—']
        else:
            cells = [f"{_pct(cap['capture'])} [{_pct(cap['ci95'][0])}, {_pct(cap['ci95'][1])}]",
                     _f(b['calls_per_100_patients']), _pct(b['false_alarm_share']),
                     _f(b['on_duty_seconds_per_patient']), _f(b['busy_seconds_per_patient']),
                     _f(row.get('J_saved_per_backup_minute'))]
        lines.append(f"| {rule} | {_f(row['J'])} | {delta} | " + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def markdown(report):
    rules = report['rules']
    lines = [f"# 병원 문의 처리 용량(보조 담당 호출) {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f"**선택:** `{s['rule']}` (포착률 기준 충족: {s['target_met']})", '', f'선택 규칙: {SELECTION_RULE}', '']
    else:
        v = report['verdict']
        lines += [f"**잠금 선택:** `{report['selected_rule']}`", '', f'판정 규칙: {SUCCESS_RULE}', '',
                  f"- 효과 확인(ΔJ 상한 < 0): **{v['effect_confirmed']}** · 실무 기준(포착률 ≥ {CAPTURE_TARGET:.0%}): **{v['capture_target_met']}**", '']
    mix = report['mix']
    lines += ['## 기준 용량(보정 혼합)', '', reference_table(mix['metrics']), '',
              f"완전정보 하한 J {_f(mix['lower_bound'])}초(여러 환자는 대기열을 무시한 하한).", '']
    for p in POLICIES:
        lines += [f'## 호출 규칙 — {p}', '', rule_table(mix['rules'][p], [r for r in rules if r != 'base']), '']
    for a, by_policy in report['by_arrival'].items():
        rows = by_policy[BASE_POLICY]
        lines += [f'### 도착 형태 {a} — {BASE_POLICY}', '', rule_table(rows, [r for r in rules if r != 'base']), '']
    lines += ['## 정책 차이 ΔJ(EAT λ15 − fixed_3)의 용량별 변화', '', '| 용량 | ΔJ | 95% 구간 |', '|---|---:|---|']
    for r, row in report['policy_gap'].items():
        lines.append(f"| {r} | {_f(row['delta_J_mean'])} | {_ci(row['ci95'])} |")
    fam = report['families']
    show = [arm(BASE_POLICY, r) for r in report['family_rules']]
    lines += ['', '## 기존 8유형(스트레스 시험, EAT λ15)', '', '| 유형 | ' + ' | '.join(r for r in report['family_rules']) + ' |',
              '|---|' + '---:|' * len(show)]
    for f, vals in fam['by_family'].items():
        lines.append(f'| {f} | ' + ' | '.join(f"{vals[n]['J']:.0f}" for n in show) + ' |')
    lines.append('| 유형 균등 | ' + ' | '.join(f"{fam['family_equal'][n]['J']:.1f}" for n in show) + ' |')
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + LIMITATIONS]
    return '\n'.join(lines) + '\n'


def _write(stem, report):
    json_path, md_path = Path(str(stem) + '.json'), Path(str(stem) + '.md')
    for p in (json_path, md_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    tmp = json_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(json_path)
    md_path.write_text(markdown(report), encoding='utf-8')
    return json_path, md_path


def source_hashes():
    return {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}


def _tasks(mix_seeds, family_seeds, rules):
    arm_list = arms_for(rules)
    tasks = [('mix', s, arm_list) for s in mix_seeds]
    tasks += [(f'family:{f}', s, arm_list) for f in scenarios.original.FAMILY_NAMES for s in family_seeds]
    return tasks


def development(output, workers):
    output = Path(output).resolve()
    hashes = source_hashes()
    results, elapsed = run_all(_tasks(DEV_MIX, DEV_FAMILY, DEV_RULES), workers)
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(DEV_RULES),
                  selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, DEV_RULES))
    report['selection'] = select(report['mix']['rules'][BASE_POLICY])
    chosen = report['selection']['rule']
    report['policy_gap'][chosen] = base.paired([r for r in results if r['kind'] == 'mix'],
                                               arm('eat_l15_f', chosen), arm('fixed_3', chosen))
    report['family_rules'] = ['base', 'min2', 'inf', chosen]
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_capacity_20261021', selection=report['selection'],
                source_sha256=hashes, development_report=base.relative_path(json_path),
                development_report_sha256=base.sha256_file(json_path),
                validation_seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE)
    lock_path = Path(str(output) + '_lock.json')
    if lock_path.exists():
        raise FileExistsError(f'{lock_path} already exists')
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding='utf-8')
    return json_path, md_path, lock_path, report


def verdict(rows, rule):
    pv, cap = rows[rule]['paired_vs_base'], rows[rule]['capture']
    return dict(effect_confirmed=pv['ci95'][1] < 0,
                capture_target_met=cap['capture'] is not None and cap['capture'] >= CAPTURE_TARGET,
                delta_J=pv['delta_J_mean'], ci95=pv['ci95'], capture=cap['capture'], capture_ci95=cap['ci95'])


def validation(lock_path, output, workers):
    lock_path, output = Path(lock_path).resolve(), Path(output).resolve()
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['rule']
    rules = REFERENCE_RULES + (chosen,) + sensitivity_rules(chosen)
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_FAMILY, rules), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(rules),
                  selected_rule=chosen, lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE, seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, rules))
    report['policy_gap'][chosen] = base.paired([r for r in results if r['kind'] == 'mix'],
                                               arm('eat_l15_f', chosen), arm('fixed_3', chosen))
    report['verdict'] = verdict(report['mix']['rules'][BASE_POLICY], chosen)
    report['family_rules'] = ['base', 'min2', 'inf', chosen]
    return _write(output, report) + (report,)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='stage', required=True)
    dev = sub.add_parser('development')
    dev.add_argument('--output', required=True)
    dev.add_argument('--workers', type=int, default=8)
    val = sub.add_parser('validation')
    val.add_argument('--lock', required=True)
    val.add_argument('--output', required=True)
    val.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    if args.stage == 'development':
        _, _, _, report = development(args.output, args.workers)
        print('selection', report['selection'])
        for rule, row in report['mix']['rules'][BASE_POLICY].items():
            cap = row.get('capture', {}).get('capture')
            print(rule, round(row['J'], 1), None if cap is None else round(cap, 3),
                  round(row['backup']['on_duty_seconds_per_patient'], 1))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
