"""14차 실험: 처리 용량을 아는 EAT의 문의비용 λ·동시 문의 수 재조정. 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_tune development --output simulation_results/inquiry_lab_tune_development_20261025
python -m inquiry_lab.experiment_tune validation --lock simulation_results/inquiry_lab_tune_development_20261025_lock.json \
    --output simulation_results/inquiry_lab_tune_validation_20261025

사전 고정:
- 질문: 13차 EAT-K(지금 처리 인원, 가까운 10곳, λ15, 동시 4곳)에서 λ를 낮추거나 동시 문의를 늘리면 나아지는가.
  λ15·동시 4곳은 처리 인원을 1명으로 보던 1차 EAT에서 고른 값이다.
- 환경: 13차와 같은 생성기(보정 혼합·다수 사상자·기존 8유형)와 같은 두 운영(base, PACKAGE). 새 seed만 사용:
  개발 혼합 1400001–1400240·다수 1420001–1420120·8유형 1440001–1440003,
  검증 혼합 1410001–1410400·다수 1430001–1430200·8유형 1450001–1450006.
- 문의 정책: fixed_3, eat_l15_f, 기준 eatk_live_k10_m4(13차), 후보 eatkt_l0_m4·eatkt_l0_m6·eatkt_l15_m6·eatkt_l30_m4.
- 선택(개발): 혼합 두 운영 모두 ΔJ(후보 − 13차 기준) ≤ +2초인 후보 중 다수 사상자·PACKAGE J 최소(같으면 다수 사상자·base J).
  그 후보의 다수 사상자·PACKAGE ΔJ < 0이 아니면 검증하지 않는다.
- 판정(검증): 다수 사상자·PACKAGE ΔJ(선택 − 13차 기준) 95% 구간 상한 < 0 이고 혼합 두 운영 95% 구간 상한 < +5초이면 채택 후보.
  다수 사상자·base, 문의 수, 고정3·EAT 대비는 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import (aware_runner, eat_capacity_policy, eat_tuned_policy, experiment as base, experiment_aware as prev,
               experiment_wide as wide, scenarios)

DEV_MIX = tuple(range(1400001, 1400241))
DEV_MASS = tuple(range(1420001, 1420121))
DEV_FAMILY = (1440001, 1440002, 1440003)
VAL_MIX = tuple(range(1410001, 1410401))
VAL_MASS = tuple(range(1430001, 1430201))
VAL_FAMILY = (1450001, 1450002, 1450003, 1450004, 1450005, 1450006)
REFERENCE = eat_capacity_policy.variant_name('live', 10, 4)
CANDIDATES = tuple(eat_tuned_policy.variant_name(lam, m) for lam, m in ((0, 4), (0, 6), (15, 6), (30, 4)))
POLICIES = ('fixed_3', 'eat_l15_f', REFERENCE) + CANDIDATES
PACKAGE, RULES = wide.PACKAGE, wide.RULES
MIX_TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/eat_tuned_policy.py', 'inquiry_lab/experiment_tune.py')
SELECTION_RULE = (f'개발: 혼합 두 운영 모두 ΔJ(후보 − {REFERENCE}) ≤ +{MIX_TOLERANCE:.0f}초인 후보 중 다수 사상자·PACKAGE J 최소. '
                  '그 ΔJ < 0이 아니면 검증하지 않는다.')
SUCCESS_RULE = (f'검증: 다수 사상자·PACKAGE ΔJ(선택 − {REFERENCE}) 95% 구간 상한 < 0 이고 혼합 두 운영 95% 구간 상한 < +{MARGIN:.0f}초이면 '
                '채택 후보.')


def arm(policy, rule):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_tuned_policy.install([p for p in names if p.startswith(eat_tuned_policy.PREFIX)])
    scenario = wide.build(kind, seed)
    result = aware_runner.run_scenario(scenario, arm_list)
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={arm(b['policy'], b['rule']): wide.prev.prev.compact(b) for b in result['branches']})


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def kind_table(results, rule, policies):
    metrics = {p: base.policy_metrics(results, arm(p, rule)) for p in policies}
    versus = {p: base.paired(results, arm(p, rule), arm(REFERENCE, rule)) for p in policies if p != REFERENCE}
    return dict(metrics=metrics, versus_reference=versus)


def evaluate(results, policies):
    groups = dict(mix=[r for r in results if r['kind'] == 'mix'], mass=[r for r in results if r['kind'] == 'mass'])
    fam = [r for r in results if r['kind'] not in groups]
    report = dict(groups={g: {rule: kind_table(rs, rule, policies) for rule in RULES} for g, rs in groups.items() if rs})
    report['by_arrival'] = {}
    for g, rs in groups.items():
        for a in sorted({r['mix']['arrival'] for r in rs}):
            subset = [r for r in rs if r['mix']['arrival'] == a]
            report['by_arrival'][f'{g}:{a}'] = {rule: kind_table(subset, rule, policies) for rule in RULES}
    report['versus_current'] = {g: {p: base.paired(rs, arm(p, PACKAGE), arm('fixed_3', 'base')) for p in policies}
                                for g, rs in groups.items() if rs}
    report['families'] = base.family_table(fam, [arm(p, r) for p in policies for r in RULES])
    return report


def select(report):
    mix, mass = report['groups']['mix'], report['groups']['mass']
    feasible = [c for c in CANDIDATES
                if all(mix[rule]['versus_reference'][c]['delta_J_mean'] <= MIX_TOLERANCE for rule in RULES)]
    if not feasible:
        return dict(policy=None, proceed=False, feasible=[])
    best = min(feasible, key=lambda c: (round(mass[PACKAGE]['metrics'][c]['J'], 9), round(mass['base']['metrics'][c]['J'], 9),
                                        CANDIDATES.index(c)))
    delta = mass[PACKAGE]['versus_reference'][best]['delta_J_mean']
    return dict(policy=best, proceed=delta < 0, feasible=feasible, mass_package_delta_J=delta,
                mass_base_delta_J=mass['base']['versus_reference'][best]['delta_J_mean'],
                mix_delta_J={rule: mix[rule]['versus_reference'][best]['delta_J_mean'] for rule in RULES})


def verdict(report, chosen):
    mass = report['groups']['mass'][PACKAGE]['versus_reference'][chosen]
    mix = {rule: report['groups']['mix'][rule]['versus_reference'][chosen] for rule in RULES}
    noninferior = all(v['ci95'][1] < MARGIN for v in mix.values())
    return dict(policy=chosen, mass_package_better=mass['ci95'][1] < 0, mix_noninferior=noninferior,
                adopted=mass['ci95'][1] < 0 and noninferior, mass_package_delta_J=mass['delta_J_mean'],
                mass_package_ci95=mass['ci95'], mix={rule: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for rule, v in mix.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def table(block, policies):
    lines = [f'| 정책 | J(초) | 실패 | 환자당 문의 | ΔJ vs {REFERENCE} [95% 구간] |', '|---|---:|---:|---:|---|']
    for p in policies:
        m, v = block['metrics'][p], block['versus_reference'].get(p)
        delta = f"{_f(v['delta_J_mean'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}]" if v else '—'
        lines.append(f"| {p} | {_f(m['J'])} | {m['failed']} | {_f(m['inquiries_per_patient'], 2)} | {delta} |")
    return '\n'.join(lines)


def markdown(report):
    lines = [f"# EAT-K 재조정 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['policy']} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '',
                  f"- {v['policy']}: 다수 사상자·PACKAGE ΔJ {_f(v['mass_package_delta_J'])} "
                  f"[{_f(v['mass_package_ci95'][0])}, {_f(v['mass_package_ci95'][1])}] → 채택 후보 **{v['adopted']}**"]
        for rule, row in v['mix'].items():
            lines.append(f"- 혼합·{'base' if rule == 'base' else 'PACKAGE'}: ΔJ {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
        lines.append('')
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, by_rule in report['groups'].items():
        for rule, block in by_rule.items():
            lines += [f"## {names[g]} — {'현행 병원 운영' if rule == 'base' else '병원 쪽 수단 모두 사용'}", '',
                      table(block, report['policies']), '']
    lines += ['## 현행(고정3·현행 병원 운영) 대비 패키지', '', '| 묶음 | 정책 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for g, by_policy in report['versus_current'].items():
        for p, v in by_policy.items():
            lines.append(f"| {names[g]} | {p} + 패키지 | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS + wide.LIMITATIONS + prev.LIMITATIONS]
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


def development(output, workers):
    output = Path(output).resolve()
    hashes = source_hashes()
    results, elapsed = run_all(wide._tasks(DEV_MIX, DEV_MASS, DEV_FAMILY, POLICIES), workers)
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(RULES),
                  selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], mass=[DEV_MASS[0], DEV_MASS[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, POLICIES))
    report['selection'] = select(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_tune_20261025', selection=report['selection'],
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
        raise RuntimeError('development selected no tuned candidate; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['policy']
    policies = ('fixed_3', 'eat_l15_f', REFERENCE, chosen)
    results, elapsed = run_all(wide._tasks(VAL_MIX, VAL_MASS, VAL_FAMILY, policies), workers)
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
                for p, v in block['versus_reference'].items():
                    print(g, 'base' if rule == 'base' else 'package', p, round(v['delta_J_mean'], 1), [round(x, 1) for x in v['ci95']])
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
