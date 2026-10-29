"""12차 실험: 다수 사상자 상황에서 EAT 후보 범위 넓히기(EAT-W). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_wide development --output simulation_results/inquiry_lab_wide_development_20261022
python -m inquiry_lab.experiment_wide validation --lock simulation_results/inquiry_lab_wide_development_20261022_lock.json \
    --output simulation_results/inquiry_lab_wide_validation_20261022

사전 고정:
- 질문: 환자 12~20명이 한꺼번에 몰리면 가까운 6곳이 모두 붐빈다. EAT가 더 먼 병원까지 계획에 넣고(K곳)
  동시 문의를 늘리면(M곳) J가 줄어드는가. 보통 상황(보정 혼합)에서는 나빠지지 않아야 한다.
- 환경: 보정 혼합(`scenarios.make_mix`)과 다수 사상자 스트레스(`mass_scenarios.make_mass`)와 기존 8유형. 새 seed만 사용:
  개발 혼합 1280001–1280240·다수 1300001–1300120·8유형 1320001–1320003,
  검증 혼합 1290001–1290400·다수 1310001–1310200·8유형 1330001–1330006.
- 문의 정책: fixed_3, eat_l15_f(1차 잠금), eatw_k8_m4, eatw_k10_m4, eatw_k10_m6.
- 환경 규칙(order2_runner): base(현행 병원 운영)와 PACKAGE(8~11차에서 효과가 확인된 병원 쪽 수단을 모두 쓴 운영).
- 선택(개발, base): 혼합에서 ΔJ(후보 − EAT λ15) ≤ +2초인 EAT-W 중 다수 사상자 J 최소. 없으면 검증하지 않는다.
- 판정(검증, base): 다수 사상자 ΔJ(선택 − EAT λ15)의 95% 구간 상한 < 0 이고,
  혼합 ΔJ(선택 − EAT λ15)의 95% 구간 상한 < +5초(비열등 여유 5초, 6차와 같음)이면 채택 후보.
  PACKAGE 환경 결과·고정3 비교·도착 형태별 결과는 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import eat_wide_policy, experiment as base, experiment_order2 as prev, harness, mass_scenarios, order2_runner, scenarios

DEV_MIX = tuple(range(1280001, 1280241))
DEV_MASS = tuple(range(1300001, 1300121))
DEV_FAMILY = (1320001, 1320002, 1320003)
VAL_MIX = tuple(range(1290001, 1290401))
VAL_MASS = tuple(range(1310001, 1310201))
VAL_FAMILY = (1330001, 1330002, 1330003, 1330004, 1330005, 1330006)
BASE_POLICY = 'eat_l15_f'
CANDIDATES = (eat_wide_policy.variant_name(8, 4), eat_wide_policy.variant_name(10, 4), eat_wide_policy.variant_name(10, 6))
POLICIES = ('fixed_3', BASE_POLICY) + CANDIDATES
PACKAGE = 'od_k2_d60_l120+ci_r30_q50+ord_spread_o100_c50'
RULES = ('base', PACKAGE)
MIX_TOLERANCE = 2.0
MARGIN = 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/eat_wide_policy.py', 'inquiry_lab/mass_scenarios.py',
                                    'inquiry_lab/experiment_wide.py')
SELECTION_RULE = (f'개발 base 환경: 혼합 ΔJ(후보 − EAT λ15) ≤ +{MIX_TOLERANCE:.0f}초인 EAT-W 중 다수 사상자 J 최소. '
                  '없으면 검증하지 않는다.')
SUCCESS_RULE = (f'검증 base 환경: 다수 사상자 ΔJ(선택 − EAT λ15) 95% 구간 상한 < 0 이고 혼합 ΔJ 95% 구간 상한 < +{MARGIN:.0f}초이면 '
                '채택 후보.')
LIMITATIONS = [
    '다수 사상자 입력은 보정 혼합의 가정을 크게 늘린 합성 스트레스다. 실제 사고의 환자 중증도·현장 분류·이송 순서는 넣지 않았다.',
    'PACKAGE는 보조 호출(미회신 2건, 60초 합류)·취소 중단(30초 뒤 절반)·분산 정렬(열람 100%, 따름 50%)을 함께 쓴 가정이다.',
]


def arm(policy, rule):
    return f'{policy}|{rule}'


def build(kind, seed):
    if kind == 'mass':
        return mass_scenarios.make_mass(seed)
    return harness.build(kind, seed)


def run_case(task):
    kind, seed, arm_list = task
    eat_wide_policy.install([p for p, _ in arm_list if p.startswith(eat_wide_policy.PREFIX)])
    scenario = build(kind, seed)
    result = order2_runner.run_scenario(scenario, arm_list)
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={arm(b['policy'], b['rule']): prev.prev.compact(b) for b in result['branches']})


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


# ---------------------------------------------------------------- 집계

def kind_table(results, rule, policies):
    metrics = {p: base.policy_metrics(results, arm(p, rule)) for p in policies}
    versus_eat = {p: base.paired(results, arm(p, rule), arm(BASE_POLICY, rule)) for p in policies if p != BASE_POLICY}
    return dict(metrics=metrics, versus_eat=versus_eat)


def evaluate(results, policies):
    groups = dict(mix=[r for r in results if r['kind'] == 'mix'], mass=[r for r in results if r['kind'] == 'mass'])
    fam = [r for r in results if r['kind'] not in groups]
    report = dict(groups={g: {rule: kind_table(rs, rule, policies) for rule in RULES} for g, rs in groups.items() if rs})
    report['by_arrival'] = {}
    for g, rs in groups.items():
        for a in sorted({r['mix']['arrival'] for r in rs}):
            subset = [r for r in rs if r['mix']['arrival'] == a]
            report['by_arrival'][f'{g}:{a}'] = {rule: kind_table(subset, rule, policies) for rule in RULES}
    report['package_effect'] = {g: {p: base.paired(rs, arm(p, PACKAGE), arm(p, 'base')) for p in policies}
                                for g, rs in groups.items() if rs}
    report['versus_current'] = {g: {p: base.paired(rs, arm(p, PACKAGE), arm('fixed_3', 'base')) for p in policies}
                                for g, rs in groups.items() if rs}
    report['families'] = base.family_table(fam, [arm(p, r) for p in policies for r in RULES])
    return report


def select(report):
    mix, mass = report['groups']['mix']['base'], report['groups']['mass']['base']
    feasible = [c for c in CANDIDATES if mix['versus_eat'][c]['delta_J_mean'] <= MIX_TOLERANCE]
    if not feasible:
        return dict(policy=None, proceed=False, feasible=[])
    best = min(feasible, key=lambda c: (round(mass['metrics'][c]['J'], 9), CANDIDATES.index(c)))
    return dict(policy=best, proceed=mass['versus_eat'][best]['delta_J_mean'] < 0, feasible=feasible,
                mass_delta_J=mass['versus_eat'][best]['delta_J_mean'], mix_delta_J=mix['versus_eat'][best]['delta_J_mean'])


def verdict(report, chosen):
    mass = report['groups']['mass']['base']['versus_eat'][chosen]
    mix = report['groups']['mix']['base']['versus_eat'][chosen]
    return dict(policy=chosen, mass_better=mass['ci95'][1] < 0, mix_noninferior=mix['ci95'][1] < MARGIN,
                adopted=mass['ci95'][1] < 0 and mix['ci95'][1] < MARGIN,
                mass_delta_J=mass['delta_J_mean'], mass_ci95=mass['ci95'], mix_delta_J=mix['delta_J_mean'], mix_ci95=mix['ci95'])


# ---------------------------------------------------------------- 보고서

def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def table(block, policies):
    lines = ['| 정책 | J(초) | 실패 | 피할 수 있던 실패 | 환자당 문의 | ΔJ vs EAT λ15 [95% 구간] |', '|---|---:|---:|---:|---:|---|']
    for p in policies:
        m = block['metrics'][p]
        v = block['versus_eat'].get(p)
        delta = f"{_f(v['delta_J_mean'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}]" if v else '—'
        lines.append(f"| {p} | {_f(m['J'])} | {m['failed']} | {m['avoidable_failed']} | {_f(m['inquiries_per_patient'], 2)} | {delta} |")
    return '\n'.join(lines)


def markdown(report):
    lines = [f"# 다수 사상자 EAT 후보 범위(EAT-W) {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['policy']} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '',
                  f"- {v['policy']}: 다수 사상자 ΔJ {_f(v['mass_delta_J'])} [{_f(v['mass_ci95'][0])}, {_f(v['mass_ci95'][1])}], "
                  f"혼합 ΔJ {_f(v['mix_delta_J'])} [{_f(v['mix_ci95'][0])}, {_f(v['mix_ci95'][1])}] → 채택 후보 **{v['adopted']}**", '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, by_rule in report['groups'].items():
        for rule, block in by_rule.items():
            lines += [f"## {names[g]} — {'현행 병원 운영' if rule == 'base' else '병원 쪽 수단 모두 사용'}", '',
                      table(block, report['policies']), '']
    lines += ['## 도착 형태별 ΔJ vs EAT λ15(현행 병원 운영)', '', '| 묶음 | 정책 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for key, by_rule in report['by_arrival'].items():
        for p, v in by_rule['base']['versus_eat'].items():
            lines.append(f"| {key} | {p} | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 현행(고정3·현행 병원 운영) 대비 패키지', '', '| 묶음 | 정책 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for g, by_policy in report['versus_current'].items():
        for p, v in by_policy.items():
            lines.append(f"| {names[g]} | {p} + 패키지 | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + prev.LIMITATIONS + LIMITATIONS]
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


def _tasks(mix_seeds, mass_seeds, family_seeds, policies):
    arm_list = [(p, r) for p in policies for r in RULES]
    tasks = [('mass', s, arm_list) for s in mass_seeds]      # 무거운 작업을 먼저 나눈다
    tasks += [('mix', s, arm_list) for s in mix_seeds]
    tasks += [(f'family:{f}', s, arm_list) for f in scenarios.original.FAMILY_NAMES for s in family_seeds]
    return tasks


def development(output, workers):
    output = Path(output).resolve()
    hashes = source_hashes()
    results, elapsed = run_all(_tasks(DEV_MIX, DEV_MASS, DEV_FAMILY, POLICIES), workers)
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(RULES),
                  selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], mass=[DEV_MASS[0], DEV_MASS[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, POLICIES))
    report['selection'] = select(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_wide_20261022', selection=report['selection'],
                source_sha256=hashes, development_report=base.relative_path(json_path),
                development_report_sha256=base.sha256_file(json_path),
                validation_seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], mass=[VAL_MASS[0], VAL_MASS[-1]], families=list(VAL_FAMILY)),
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
        raise RuntimeError('development selected no EAT-W candidate; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['policy']
    policies = ('fixed_3', BASE_POLICY, chosen)
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_MASS, VAL_FAMILY, policies), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(policies), rules=list(RULES),
                  selected_policy=chosen, lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], mass=[VAL_MASS[0], VAL_MASS[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, policies))
    report['verdict'] = verdict(report, chosen)
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
        for g, by_rule in report['groups'].items():
            for rule, block in by_rule.items():
                for p, v in block['versus_eat'].items():
                    print(g, rule, p, round(v['delta_J_mean'], 1), [round(x, 1) for x in v['ci95']])
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
