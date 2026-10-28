"""13차 실험: 처리 용량을 아는 EAT(EAT-K). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_aware development --output simulation_results/inquiry_lab_aware_development_20261025
python -m inquiry_lab.experiment_aware validation --lock simulation_results/inquiry_lab_aware_development_20261025_lock.json \
    --output simulation_results/inquiry_lab_aware_validation_20261025

사전 고정:
- 질문: EAT의 대기 모형이 병원별 처리 인원(평소 인원, 또는 보조 담당 합류를 포함한 지금 인원)을 알면,
  12차에서 병원 쪽 수단(패키지)과 함께 쓸 때 사라졌던 후보 범위 확대의 이득이 돌아오고 전체적으로 나아지는가.
- 환경: 보정 혼합·다수 사상자(12차와 같은 생성기)·기존 8유형. 새 seed만 사용:
  개발 혼합 1340001–1340240·다수 1360001–1360120·8유형 1380001–1380003,
  검증 혼합 1350001–1350400·다수 1370001–1370200·8유형 1390001–1390006.
- 문의 정책: fixed_3, eat_l15_f(1차), eatw_k10_m4(12차 채택 후보), 후보 eatk_base_k10_m4·eatk_live_k10_m4·eatk_live_k6_m4.
- 환경 규칙(aware_runner): base(현행 병원 운영)와 PACKAGE(보조 호출 + 취소 중단 + 분산 정렬, 12차와 같다).
- 선택(개발): 혼합의 두 환경 모두에서 ΔJ(후보 − EAT λ15) ≤ +2초인 후보 중 다수 사상자·PACKAGE의 J 최소
  (같으면 다수 사상자·base의 J). 그 후보의 다수 사상자·PACKAGE ΔJ < 0이 아니면 검증하지 않는다.
- 판정(검증): 다수 사상자·PACKAGE에서 ΔJ(선택 − EAT λ15)의 95% 구간 상한 < 0 이고, 혼합 두 환경에서 95% 구간 상한 < +5초(비열등).
  다수 사상자·base에서의 EAT-W·EAT 대비, 고정3 대비, 도착 형태별 결과는 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import aware_runner, eat_capacity_policy, eat_wide_policy, experiment as base, experiment_wide as prev, scenarios

DEV_MIX = tuple(range(1340001, 1340241))
DEV_MASS = tuple(range(1360001, 1360121))
DEV_FAMILY = (1380001, 1380002, 1380003)
VAL_MIX = tuple(range(1350001, 1350401))
VAL_MASS = tuple(range(1370001, 1370201))
VAL_FAMILY = (1390001, 1390002, 1390003, 1390004, 1390005, 1390006)
BASE_POLICY = prev.BASE_POLICY
WIDE = eat_wide_policy.variant_name(10, 4)
CANDIDATES = (eat_capacity_policy.variant_name('base', 10, 4), eat_capacity_policy.variant_name('live', 10, 4),
              eat_capacity_policy.variant_name('live', 6, 4))
POLICIES = ('fixed_3', BASE_POLICY, WIDE) + CANDIDATES
PACKAGE, RULES = prev.PACKAGE, prev.RULES
MIX_TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/eat_capacity_policy.py', 'inquiry_lab/aware_runner.py',
                                    'inquiry_lab/experiment_aware.py')
SELECTION_RULE = (f'개발: 혼합 두 환경 모두 ΔJ(후보 − EAT λ15) ≤ +{MIX_TOLERANCE:.0f}초인 EAT-K 중 다수 사상자·PACKAGE J 최소. '
                  '그 ΔJ < 0이 아니면 검증하지 않는다.')
SUCCESS_RULE = (f'검증: 다수 사상자·PACKAGE ΔJ(선택 − EAT λ15) 95% 구간 상한 < 0 이고 혼합 두 환경 95% 구간 상한 < +{MARGIN:.0f}초이면 '
                '채택 후보.')
LIMITATIONS = [
    "'지금 인원'은 병원 화면에 접속한 검토자 수처럼 보조 담당 합류가 서버에 바로 알려진다는 가정이다. 현재 병원 화면에는 이 기능이 없다.",
    "'평소 인원'은 병원이 문의 담당 인원을 미리 등록한다는 가정이다.",
]


def arm(policy, rule):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_wide_policy.install([p for p in names if p.startswith(eat_wide_policy.PREFIX)])
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    scenario = prev.build(kind, seed)
    result = aware_runner.run_scenario(scenario, arm_list)
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={arm(b['policy'], b['rule']): prev.prev.prev.compact(b) for b in result['branches']})


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def evaluate(results, policies):
    report = prev.evaluate(results, policies)
    mass = [r for r in results if r['kind'] == 'mass']
    if WIDE in policies:
        report['versus_wide'] = {f'mass|{rule}': {p: base.paired(mass, arm(p, rule), arm(WIDE, rule))
                                                   for p in policies if p.startswith(eat_capacity_policy.PREFIX)}
                                 for rule in RULES}
    return report


def select(report):
    mix, mass = report['groups']['mix'], report['groups']['mass']
    feasible = [c for c in CANDIDATES if all(mix[rule]['versus_eat'][c]['delta_J_mean'] <= MIX_TOLERANCE for rule in RULES)]
    if not feasible:
        return dict(policy=None, proceed=False, feasible=[])
    best = min(feasible, key=lambda c: (round(mass[PACKAGE]['metrics'][c]['J'], 9), round(mass['base']['metrics'][c]['J'], 9),
                                        CANDIDATES.index(c)))
    delta = mass[PACKAGE]['versus_eat'][best]['delta_J_mean']
    return dict(policy=best, proceed=delta < 0, feasible=feasible, mass_package_delta_J=delta,
                mass_base_delta_J=mass['base']['versus_eat'][best]['delta_J_mean'],
                mix_delta_J={rule: mix[rule]['versus_eat'][best]['delta_J_mean'] for rule in RULES})


def verdict(report, chosen):
    mass = report['groups']['mass'][PACKAGE]['versus_eat'][chosen]
    mix = {rule: report['groups']['mix'][rule]['versus_eat'][chosen] for rule in RULES}
    noninferior = all(v['ci95'][1] < MARGIN for v in mix.values())
    return dict(policy=chosen, mass_package_better=mass['ci95'][1] < 0, mix_noninferior=noninferior,
                adopted=mass['ci95'][1] < 0 and noninferior, mass_package_delta_J=mass['delta_J_mean'],
                mass_package_ci95=mass['ci95'], mix={rule: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for rule, v in mix.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    lines = [f"# 처리 용량을 아는 EAT(EAT-K) {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
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
                      prev.table(block, report['policies']), '']
    if 'versus_wide' in report:
        lines += ['## 다수 사상자에서 EAT-W(12차) 대비', '', '| 환경 | 정책 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
        for key, by_policy in report['versus_wide'].items():
            for p, v in by_policy.items():
                lines.append(f"| {key} | {p} | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
        lines.append('')
    lines += ['## 도착 형태별 ΔJ vs EAT λ15', '', '| 묶음 | 환경 | 정책 | ΔJ | 95% 구간 |', '|---|---|---|---:|---|']
    for key, by_rule in report['by_arrival'].items():
        for rule, block in by_rule.items():
            for p, v in block['versus_eat'].items():
                if p.startswith(eat_capacity_policy.PREFIX) or p == WIDE:
                    lines.append(f"| {key} | {'base' if rule == 'base' else 'PACKAGE'} | {p} | {_f(v['delta_J_mean'])} | "
                                 f"[{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 현행(고정3·현행 병원 운영) 대비 패키지', '', '| 묶음 | 정책 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for g, by_policy in report['versus_current'].items():
        for p, v in by_policy.items():
            lines.append(f"| {names[g]} | {p} + 패키지 | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + prev.prev.LIMITATIONS + prev.LIMITATIONS + LIMITATIONS]
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
    results, elapsed = run_all(prev._tasks(DEV_MIX, DEV_MASS, DEV_FAMILY, POLICIES), workers)
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(RULES),
                  selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], mass=[DEV_MASS[0], DEV_MASS[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, POLICIES))
    report['selection'] = select(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_aware_20261025', selection=report['selection'],
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
        raise RuntimeError('development selected no EAT-K candidate; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['policy']
    policies = ('fixed_3', BASE_POLICY, WIDE, chosen)
    results, elapsed = run_all(prev._tasks(VAL_MIX, VAL_MASS, VAL_FAMILY, policies), workers)
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
                    print(g, 'base' if rule == 'base' else 'package', p, round(v['delta_J_mean'], 1), [round(x, 1) for x in v['ci95']])
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
