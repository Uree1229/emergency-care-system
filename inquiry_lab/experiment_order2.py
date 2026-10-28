"""11차 실험: 분산 정렬 개선(위험이 큰 환자 먼저)과 통합 패키지 검증. 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_order2 development --output simulation_results/inquiry_lab_order2_development_20261022
python -m inquiry_lab.experiment_order2 validation --lock simulation_results/inquiry_lab_order2_development_20261022_lock.json \
    --output simulation_results/inquiry_lab_order2_validation_20261022

사전 고정:
- 질문 1(개선): 10차 분산 정렬(따름 50%)에서 같은 검토 중 건수면 실패 위험이 큰 환자를 먼저 주면 J가 더 줄어드는가.
  후보는 ord_age(오래 기다린 환자 먼저)와 ord_risk(거절을 많이 받은 환자 먼저)다.
- 질문 2(통합): 지금까지 효과가 확인된 수단을 모두 쓰면 현행(고정3·받은 순서·중단 없음·보조 없음)보다 얼마나 줄어드는가.
  패키지 = EAT λ15 + 문의함 정렬 + 취소 중단(30초 뒤 절반) + 보조 호출(미회신 2건, 60초 합류, 복귀 대기 120초).
- 환경: 1차와 같은 보정 혼합. 새 seed만 사용: 개발 1240001–1240240·8유형 1260001–1260003,
  검증 1250001–1250400·8유형 1270001–1270006.
- 문의 정책: fixed_3, eat_l15_f. 정책 코드는 바꾸지 않는다.
- 선택(개발, EAT λ15): ord_age_o100_c50과 ord_risk_o100_c50 중 J가 낮은 것. 그것이 10차 규칙(ord_spread_o100_c50)보다
  J가 낮으면(ΔJ < 0) 개선 후보로 검증한다. 아니면 개선은 검증하지 않고 패키지에는 10차 규칙을 쓴다.
- 판정(검증):
  1. 개선 후보가 있으면 EAT λ15에서 ΔJ(후보 − 10차 규칙)의 95% 구간 상한 < 0이면 개선 확인.
  2. 패키지: ΔJ(EAT λ15 패키지 − 고정3 기준)의 95% 구간 상한 < 0이면 통합 효과 확인. 단계별 누적 변화는 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import experiment as base, experiment_order as prev, harness, order2_runner, scenarios

DEV_MIX = tuple(range(1240001, 1240241))
DEV_FAMILY = (1260001, 1260002, 1260003)
VAL_MIX = tuple(range(1250001, 1250401))
VAL_FAMILY = (1270001, 1270002, 1270003, 1270004, 1270005, 1270006)
POLICIES = ('fixed_3', 'eat_l15_f')
BASE_POLICY = 'eat_l15_f'
REFERENCE_ORDER = order2_runner.spread_name(100, 50)
CANDIDATES = (order2_runner.spread_name(100, 50, 'age'), order2_runner.spread_name(100, 50, 'risk'))
UPPER = tuple(order2_runner.spread_name(100, 100, k) for k in ('spread', 'age', 'risk'))
INTERRUPT_RULE = 'ci_r30_q50'
BACKUP_RULE = 'od_k2_d60_l120'


def package(order):
    return f'{BACKUP_RULE}+{INTERRUPT_RULE}+{order}'


DEV_RULES = ('base', 'min2', REFERENCE_ORDER) + CANDIDATES + UPPER + (f'{INTERRUPT_RULE}+{REFERENCE_ORDER}',
                                                                     package(REFERENCE_ORDER))
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/order2_runner.py', 'inquiry_lab/experiment_order2.py')
SELECTION_RULE = (f'혼합 개발(EAT λ15)에서 {CANDIDATES[0]}와 {CANDIDATES[1]} 중 J가 낮은 후보가 {REFERENCE_ORDER}보다 '
                  'J가 낮으면 개선 후보로 검증한다. 아니면 개선은 검증하지 않고 패키지에 10차 규칙을 쓴다.')
SUCCESS_RULE = ('검증 혼합(EAT λ15): (1) 개선 후보가 있으면 ΔJ(후보 − 10차 규칙) 95% 구간 상한 < 0이면 개선 확인. '
                '(2) ΔJ(EAT λ15 패키지 − 고정3 기준) 95% 구간 상한 < 0이면 통합 효과 확인.')
LIMITATIONS = prev.LIMITATIONS + [
    '패키지의 각 수단(정렬·중단·보조 호출)은 병원 화면·병원 운영의 협조를 가정한다. 서버만으로 되는 것은 문의 정책과 순서 계산이다.',
]
ARRIVALS = ('burst', 'stream')


def arm(policy, rule):
    return f'{policy}|{rule}'


def arms_for(rules, policies=POLICIES):
    return [(p, r) for p in policies for r in rules]


def run_case(task):
    kind, seed, arm_list = task
    harness.install([p for p, _ in arm_list if p.startswith('eat_')])
    scenario = harness.build(kind, seed)
    result = order2_runner.run_scenario(scenario, arm_list)
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={arm(b['policy'], b['rule']): prev.compact(b) for b in result['branches']})


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


# ---------------------------------------------------------------- 집계

def evaluate(results, rules):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] != 'mix']
    names = [arm(p, r) for p, r in arms_for(rules)]
    report = dict(mix=dict(metrics={n: base.policy_metrics(mix, n) for n in names},
                           sensitivity={str(k): {n: base.policy_metrics(mix, n, k)['J'] for n in names}
                                        for k in base.PENALTIES},
                           lower_bound=base.lower_bound(mix),
                           rules={p: prev.rule_rows(mix, p, rules) for p in POLICIES}),
                  families=base.family_table(fam, names))
    report['by_arrival'] = {}
    for a in ARRIVALS:
        subset = [r for r in mix if r['mix']['arrival'] == a]
        if subset:
            report['by_arrival'][a] = {BASE_POLICY: prev.rule_rows(subset, BASE_POLICY, rules)}
    report['versus_reference'] = {r: base.paired(mix, arm(BASE_POLICY, r), arm(BASE_POLICY, REFERENCE_ORDER))
                                  for r in rules if r.startswith('ord_') and r != REFERENCE_ORDER}
    report['versus_current'] = {r: base.paired(mix, arm(BASE_POLICY, r), arm('fixed_3', 'base')) for r in rules}
    return report


def select(rule_table, versus_reference):
    best = min(CANDIDATES, key=lambda r: (round(rule_table[r]['J'], 9), CANDIDATES.index(r)))
    row = versus_reference[best]
    return dict(rule=best, proceed=row['delta_J_mean'] < 0, delta_J_vs_reference=row['delta_J_mean'], ci95=row['ci95'],
                candidates={r: rule_table[r]['J'] for r in CANDIDATES}, reference_J=rule_table[REFERENCE_ORDER]['J'])


def validation_rules(selection):
    chosen = selection['rule'] if selection['proceed'] else REFERENCE_ORDER
    rules = ['base', 'min2', REFERENCE_ORDER]
    if chosen != REFERENCE_ORDER:
        rules.append(chosen)
    rules += [f'{INTERRUPT_RULE}+{chosen}', package(chosen)]
    return tuple(rules), chosen


def verdict(report, selection, chosen):
    out = {}
    if selection['proceed']:
        row = report['versus_reference'][chosen]
        out['improvement'] = dict(rule=chosen, confirmed=row['ci95'][1] < 0, delta_J=row['delta_J_mean'], ci95=row['ci95'])
    else:
        out['improvement'] = dict(rule=None, confirmed=None, note='개발 선택 조건 미충족으로 검증하지 않음')
    row = report['versus_current'][package(chosen)]
    out['package'] = dict(rule=package(chosen), confirmed=row['ci95'][1] < 0, delta_J=row['delta_J_mean'], ci95=row['ci95'])
    return out


# ---------------------------------------------------------------- 보고서

def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    rules = report['rules']
    lines = [f"# 분산 정렬 개선·통합 패키지 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '',
                  f"- 후보 {s['rule']}: 10차 규칙 대비 ΔJ {_f(s['delta_J_vs_reference'])} [{_f(s['ci95'][0])}, {_f(s['ci95'][1])}] → 개선 검증 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '']
        if v['improvement']['rule']:
            i = v['improvement']
            lines.append(f"- 개선 {i['rule']}: ΔJ {_f(i['delta_J'])} [{_f(i['ci95'][0])}, {_f(i['ci95'][1])}] → 확인 **{i['confirmed']}**")
        else:
            lines.append(f"- 개선: {v['improvement']['note']}")
        p = v['package']
        lines += [f"- 패키지 {p['rule']}: 고정3 기준 대비 ΔJ {_f(p['delta_J'])} [{_f(p['ci95'][0])}, {_f(p['ci95'][1])}] → 확인 **{p['confirmed']}**", '']
    mix = report['mix']
    for p in POLICIES:
        lines += [f'## {p}', '', prev.rule_table(mix['rules'][p], rules), '']
    for a, by in report['by_arrival'].items():
        lines += [f'### 도착 형태 {a} — {BASE_POLICY}', '', prev.rule_table(by[BASE_POLICY], rules), '']
    lines += ['## 10차 규칙 대비(EAT λ15)', '', '| 규칙 | ΔJ | 95% 구간 | 환자 개선/동일/악화 |', '|---|---:|---|---|']
    for r, row in report['versus_reference'].items():
        lines.append(f"| {r} | {_f(row['delta_J_mean'])} | [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}] | "
                     f"{row['patients']['better']}/{row['patients']['same']}/{row['patients']['worse']} |")
    lines += ['', '## 현행(고정3·받은 순서) 대비 EAT λ15', '', '| 규칙 | ΔJ | 95% 구간 |', '|---|---:|---|']
    for r, row in report['versus_current'].items():
        lines.append(f"| {r} | {_f(row['delta_J_mean'])} | [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}] |")
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
                  results=results, **evaluate(results, DEV_RULES))
    report['selection'] = select(report['mix']['rules'][BASE_POLICY], report['versus_reference'])
    rules, chosen = validation_rules(report['selection'])
    report['family_rules'] = ['base', 'min2', REFERENCE_ORDER] + list(CANDIDATES) + [package(REFERENCE_ORDER)]
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_order2_20261022', selection=report['selection'],
                chosen_order=chosen, source_sha256=hashes, development_report=base.relative_path(json_path),
                development_report_sha256=base.sha256_file(json_path),
                validation_seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                validation_rules=list(rules), selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE)
    lock_path = Path(str(output) + '_lock.json')
    if lock_path.exists():
        raise FileExistsError(f'{lock_path} already exists')
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding='utf-8')
    return json_path, md_path, lock_path, report


def validation(lock_path, output, workers):
    lock_path, output = Path(lock_path).resolve(), Path(output).resolve()
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    rules, chosen = tuple(lock['validation_rules']), lock['chosen_order']
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_FAMILY, rules), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(rules),
                  chosen_order=chosen, lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE, seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, rules))
    report['verdict'] = verdict(report, lock['selection'], chosen)
    report['family_rules'] = list(rules)
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
                print(p, rule, round(row['J'], 1), None if pv is None else round(pv['delta_J_mean'], 1))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
