"""10차 실험: 병원 문의함 정렬(중복 검토 분산). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_order development --output simulation_results/inquiry_lab_order_development_20261022
python -m inquiry_lab.experiment_order validation --lock simulation_results/inquiry_lab_order_development_20261022_lock.json \
    --output simulation_results/inquiry_lab_order_validation_20261022

사전 고정:
- 질문: 서버가 병원 문의함 순서를 정해, 다른 병원에서 이미 검토 중인 환자를 뒤로 미루면(중복 검토 분산)
  여러 환자가 몰릴 때 J가 줄어드는가. 서버 장부만 쓰는 '다른 병원 미회신이 적은 환자 먼저'와도 비교한다.
- 환경: 1차와 같은 보정 혼합. 새 seed만 사용: 개발 1200001–1200240·8유형 1220001–1220003,
  검증 1210001–1210400·8유형 1230001–1230006.
- 문의 정책: fixed_3, eat_l15_f, eat_l0_f. 정책 코드는 바꾸지 않는다.
- 규칙(order_runner): base(FIFO), min2(비교), ord_spread_o{100,50}_c{100,50}, ord_open_c{100,50},
  ci_r30_q50(9차 판정 규칙), ci_r30_q50+ord_spread_o100_c50, od_k2_d60_l120(8차), od_k2_d60_l120+ord_spread_o100_c50.
- 선택(EAT λ15): 현실적 가정(열람 기록 100%, 검토자가 서버 순서를 따르는 비율 50%)인 ord_spread_o100_c50과
  ord_open_c50 중 J가 낮은 것. 그 ΔJ(− base) ≥ 0이면 검증하지 않는다.
- 판정(검증): EAT λ15에서 ΔJ(선택 − base)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0이면 효과 확인.
  따르는 비율 100% 상한·9차 중단과의 결합·8차 보조 호출과의 결합·고정3·λ0은 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import experiment as base, experiment_capacity as cap, experiment_interrupt as prev, harness, order_runner, scenarios

DEV_MIX = tuple(range(1200001, 1200241))
DEV_FAMILY = (1220001, 1220002, 1220003)
VAL_MIX = tuple(range(1210001, 1210401))
VAL_FAMILY = (1230001, 1230002, 1230003, 1230004, 1230005, 1230006)
POLICIES = ('fixed_3', 'eat_l15_f', 'eat_l0_f')
BASE_POLICY = 'eat_l15_f'
INTERRUPT_RULE = 'ci_r30_q50'
BACKUP_RULE = 'od_k2_d60_l120'
SPREAD = order_runner.spread_name(100, 50)
OPEN = order_runner.open_name(50)
CANDIDATES = (SPREAD, OPEN)
GRID = tuple(order_runner.spread_name(o, c) for o in (100, 50) for c in (100, 50)) + tuple(order_runner.open_name(c) for c in (100, 50))
DEV_RULES = ('base', 'min2') + GRID + (INTERRUPT_RULE, f'{INTERRUPT_RULE}+{SPREAD}', BACKUP_RULE, f'{BACKUP_RULE}+{SPREAD}')
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/order_runner.py', 'inquiry_lab/experiment_order.py')
SELECTION_RULE = (f'혼합 개발(EAT λ15)에서 {SPREAD}와 {OPEN} 중 J가 낮은 규칙. 그 ΔJ(− base) ≥ 0이면 검증하지 않는다.')
SUCCESS_RULE = '검증 혼합(EAT λ15)에서 ΔJ(선택 − base)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0이면 효과 확인.'
LIMITATIONS = [
    '검토자가 서버가 정한 문의함 맨 위 문의를 받는다는 가정이다. 따르는 비율(c)은 가정값이고 실제 행동은 측정하지 않았다.',
    "'검토 중' 판단은 병원 화면의 열람 기록이 서버에 남는다는 가정이다(o%). 현재 병원 화면에는 이 기록이 없다.",
    '모든 환자의 중증도가 같다고 본다. 실제 문의함 정렬은 중증도·임상 판단을 먼저 따라야 한다.',
]
ARRIVALS = ('burst', 'stream')


def arm(policy, rule):
    return f'{policy}|{rule}'


def arms_for(rules, policies=POLICIES):
    return [(p, r) for p in policies for r in rules]


def compact(branch):
    row = prev.compact(branch)
    row['ordering'] = branch['ordering']
    row['overlap'] = branch['overlap']
    return row


def run_case(task):
    kind, seed, arm_list = task
    harness.install([p for p, _ in arm_list if p.startswith('eat_')])
    scenario = harness.build(kind, seed)
    result = order_runner.run_scenario(scenario, arm_list)
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

def order_metrics(results, name):
    patients = sum(r['patient_count'] for r in results)
    o = {k: sum(r['branches'][name]['ordering'][k] for r in results) for k in ('picks', 'followed', 'reordered')}
    v = {k: sum(r['branches'][name]['overlap'][k] for r in results) for k in ('starts', 'parallel_starts')}
    return dict(reordered_per_patient=o['reordered'] / patients, followed_share=o['followed'] / o['picks'] if o['picks'] else None,
                parallel_start_share=v['parallel_starts'] / v['starts'] if v['starts'] else None,
                parallel_starts_per_patient=v['parallel_starts'] / patients)


def rule_rows(results, policy, rules):
    rows = prev.rule_rows(results, policy, rules)
    for rule in rules:
        rows[rule]['order'] = order_metrics(results, arm(policy, rule))
    return rows


def evaluate(results, rules, focus):
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
    for a in ARRIVALS:
        subset = [r for r in mix if r['mix']['arrival'] == a]
        if subset:
            report['by_arrival'][a] = {BASE_POLICY: rule_rows(subset, BASE_POLICY, rules)}
    keep = [r for r in ('base', 'min2') + tuple(focus) if r in rules]
    report['policy_gaps'] = {
        'eat_l0_f − eat_l15_f': {r: base.paired(mix, arm('eat_l0_f', r), arm('eat_l15_f', r)) for r in keep},
        'eat_l15_f − fixed_3': {r: base.paired(mix, arm('eat_l15_f', r), arm('fixed_3', r)) for r in keep},
    }
    return report


def select(rule_table):
    best = min(CANDIDATES, key=lambda r: (round(rule_table[r]['J'], 9), CANDIDATES.index(r)))
    row = rule_table[best]['paired_vs_base']
    return dict(rule=best, proceed=row['delta_J_mean'] < 0, delta_J=row['delta_J_mean'], ci95=row['ci95'],
                candidates={r: rule_table[r]['J'] for r in CANDIDATES})


def validation_rules(chosen):
    upper = order_runner.spread_name(100, 100) if chosen.startswith('ord_spread') else order_runner.open_name(100)
    return ('base', 'min2', chosen, upper, INTERRUPT_RULE, f'{INTERRUPT_RULE}+{chosen}', BACKUP_RULE, f'{BACKUP_RULE}+{chosen}')


def verdict(rule_table, chosen):
    row = rule_table[chosen]['paired_vs_base']
    return dict(effect_confirmed=row['ci95'][1] < 0, rule=chosen, delta_J=row['delta_J_mean'], ci95=row['ci95'])


# ---------------------------------------------------------------- 보고서

def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def _pct(value):
    return '—' if value is None else f'{100 * value:.0f}%'


def rule_table(rows, rules):
    lines = ['| 규칙 | J(초) | ΔJ vs base [95% 구간] | 상시 2명 대비 | 실패 | 환자당 문의 | 검토 업무(초/환자) | 중복 검토 비율 | 순서 바꾼 선택(건/환자) |',
             '|---|---:|---|---:|---:|---:|---:|---:|---:|']
    for rule in rules:
        row = rows[rule]
        pv, share, od = row.get('paired_vs_base'), row.get('share_of_min2'), row['order']
        delta = f"{_f(pv['delta_J_mean'])} [{_f(pv['ci95'][0])}, {_f(pv['ci95'][1])}]" if pv else '—'
        lines.append(f"| {rule} | {_f(row['J'])} | {delta} | {_pct(share['capture']) if share else '—'} | {row['failed']} | "
                     f"{_f(row['inquiries_per_patient'], 2)} | {_f(row['review_work_seconds_per_patient'])} | "
                     f"{_pct(od['parallel_start_share'])} | {_f(od['reordered_per_patient'], 2)} |")
    return '\n'.join(lines)


def markdown(report):
    rules = report['rules']
    lines = [f"# 병원 문의함 정렬 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '',
                  f"- 선택 {s['rule']}: ΔJ {_f(s['delta_J'])} [{_f(s['ci95'][0])}, {_f(s['ci95'][1])}] → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '',
                  f"- {v['rule']}: ΔJ {_f(v['delta_J'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] → 효과 확인 **{v['effect_confirmed']}**", '']
    mix = report['mix']
    for p in POLICIES:
        lines += [f'## {p}', '', rule_table(mix['rules'][p], rules), '']
    for a, by in report['by_arrival'].items():
        lines += [f'### 도착 형태 {a} — {BASE_POLICY}', '', rule_table(by[BASE_POLICY], rules), '']
    lines += ['## 정책 차이의 규칙별 변화', '', '| 비교 | 규칙 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for label, by_rule in report['policy_gaps'].items():
        for r, row in by_rule.items():
            lines.append(f"| {label} | {r} | {_f(row['delta_J_mean'])} | [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}] |")
    fam = report['families']
    show = [r for r in report['family_rules'] if r in rules]
    lines += ['', '## 기존 8유형(스트레스 시험, EAT λ15)', '', '| 유형 | ' + ' | '.join(show) + ' |', '|---|' + '---:|' * len(show)]
    for f, vals in fam['by_family'].items():
        lines.append(f'| {f} | ' + ' | '.join(f"{vals[arm(BASE_POLICY, r)]['J']:.0f}" for r in show) + ' |')
    lines.append('| 유형 균등 | ' + ' | '.join(f"{fam['family_equal'][arm(BASE_POLICY, r)]['J']:.1f}" for r in show) + ' |')
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
                  results=results, **evaluate(results, DEV_RULES, CANDIDATES))
    report['selection'] = select(report['mix']['rules'][BASE_POLICY])
    report['family_rules'] = ['base', 'min2', SPREAD, OPEN, order_runner.spread_name(100, 100)]
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_order_20261022', selection=report['selection'],
                source_sha256=hashes, development_report=base.relative_path(json_path),
                development_report_sha256=base.sha256_file(json_path),
                validation_seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                validation_rules=list(validation_rules(report['selection']['rule'])),
                selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE)
    lock_path = Path(str(output) + '_lock.json')
    if lock_path.exists():
        raise FileExistsError(f'{lock_path} already exists')
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding='utf-8')
    return json_path, md_path, lock_path, report


def validation(lock_path, output, workers):
    lock_path, output = Path(lock_path).resolve(), Path(output).resolve()
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if not lock['selection']['proceed']:
        raise RuntimeError('development selection did not improve J; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['rule']
    rules = tuple(lock['validation_rules'])
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_FAMILY, rules), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(rules),
                  selected_rule=chosen, lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE, seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, rules, (chosen,)))
    report['verdict'] = verdict(report['mix']['rules'][BASE_POLICY], chosen)
    report['family_rules'] = ['base', 'min2', chosen, rules[3]]
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
        for p in POLICIES:
            for rule, row in report['mix']['rules'][p].items():
                pv = row.get('paired_vs_base')
                print(p, rule, round(row['J'], 1), None if pv is None else round(pv['delta_J_mean'], 1),
                      None if row['order']['parallel_start_share'] is None else round(row['order']['parallel_start_share'], 3))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
