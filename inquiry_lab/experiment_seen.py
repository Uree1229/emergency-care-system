"""15차 실험: 열람 기록만으로 처리 인원을 추정하는 EAT-K. 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_seen development --output simulation_results/inquiry_lab_seen_development_20261025
python -m inquiry_lab.experiment_seen validation --lock simulation_results/inquiry_lab_seen_development_20261025_lock.json \
    --output simulation_results/inquiry_lab_seen_validation_20261025

사전 고정:
- 질문: 13차 EAT-K(병원이 지금 처리 인원을 알려 줌) 대신, 열람 기록으로 센 '지금 검토 중인 문의 수'로 처리 인원을
  추정해도 같은 효과를 내는가. 그렇다면 병원 화면에 필요한 정보가 열람 기록 하나로 줄어든다.
- 환경: 13차와 같은 생성기와 두 운영(base, PACKAGE). 새 seed만 사용:
  개발 혼합 1460001–1460240·다수 1480001–1480120·8유형 1500001–1500003,
  검증 혼합 1470001–1470400·다수 1490001–1490200·8유형 1510001–1510006.
- 문의 정책: fixed_3, eat_l15_f, 기준 eatk_live_k10_m4(13차), 후보 eatks_seen_k10_m4·eatks_seenbase_k10_m4.
- 선택(개발): 네 칸(혼합·다수 × base·PACKAGE) 모두 ΔJ(후보 − 13차 기준) ≤ +2초이고 다수·PACKAGE에서
  ΔJ(후보 − EAT λ15) < 0인 후보 중 seen을 먼저(필요한 정보가 적다), 아니면 seenbase. 없으면 검증하지 않는다.
- 판정(검증): 선택 − 13차 기준의 95% 구간 상한이 다수·PACKAGE, 혼합·base, 혼합·PACKAGE 모두 < +5초(비열등)이고,
  다수·PACKAGE에서 선택 − EAT λ15의 95% 구간 상한 < 0이면 채택 후보.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_tune as prev, experiment_wide as wide,
               scenarios, seen_runner)

DEV_MIX = tuple(range(1460001, 1460241))
DEV_MASS = tuple(range(1480001, 1480121))
DEV_FAMILY = (1500001, 1500002, 1500003)
VAL_MIX = tuple(range(1470001, 1470401))
VAL_MASS = tuple(range(1490001, 1490201))
VAL_FAMILY = (1510001, 1510002, 1510003, 1510004, 1510005, 1510006)
REFERENCE = eat_capacity_policy.variant_name('live', 10, 4)
CANDIDATES = (eat_seen_policy.variant_name('seen', 10, 4), eat_seen_policy.variant_name('seenbase', 10, 4))
POLICIES = ('fixed_3', 'eat_l15_f', REFERENCE) + CANDIDATES
PACKAGE, RULES = wide.PACKAGE, wide.RULES
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/eat_seen_policy.py', 'inquiry_lab/seen_runner.py', 'inquiry_lab/experiment_seen.py')
SELECTION_RULE = (f'개발: 네 칸 모두 ΔJ(후보 − {REFERENCE}) ≤ +{TOLERANCE:.0f}초이고 다수·PACKAGE ΔJ(후보 − EAT λ15) < 0인 후보 중 '
                  'seen 우선, 아니면 seenbase. 없으면 검증하지 않는다.')
SUCCESS_RULE = (f'검증: 선택 − {REFERENCE}의 95% 구간 상한이 다수·PACKAGE·혼합 두 운영 모두 < +{MARGIN:.0f}초이고 '
                '다수·PACKAGE 선택 − EAT λ15의 95% 구간 상한 < 0이면 채택 후보.')
LIMITATIONS = ["'검토 중인 문의 수'는 병원 화면이 문의를 열고 닫을 때마다 서버에 알린다는 가정이다(10차 열람 기록과 같다)."]


def arm(policy, rule):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    scenario = wide.build(kind, seed)
    result = seen_runner.run_scenario(scenario, arm_list)
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
    versus_reference = {p: base.paired(results, arm(p, rule), arm(REFERENCE, rule)) for p in policies if p != REFERENCE}
    versus_eat = {p: base.paired(results, arm(p, rule), arm('eat_l15_f', rule)) for p in policies if p != 'eat_l15_f'}
    return dict(metrics=metrics, versus_reference=versus_reference, versus_eat=versus_eat)


def evaluate(results, policies):
    groups = dict(mix=[r for r in results if r['kind'] == 'mix'], mass=[r for r in results if r['kind'] == 'mass'])
    fam = [r for r in results if r['kind'] not in groups]
    report = dict(groups={g: {rule: kind_table(rs, rule, policies) for rule in RULES} for g, rs in groups.items() if rs})
    report['versus_current'] = {g: {p: base.paired(rs, arm(p, PACKAGE), arm('fixed_3', 'base')) for p in policies}
                                for g, rs in groups.items() if rs}
    report['families'] = base.family_table(fam, [arm(p, r) for p in policies for r in RULES])
    return report


def select(report):
    groups = report['groups']
    cells = [(g, rule) for g in ('mix', 'mass') for rule in RULES]
    feasible = [c for c in CANDIDATES
                if all(groups[g][rule]['versus_reference'][c]['delta_J_mean'] <= TOLERANCE for g, rule in cells)
                and groups['mass'][PACKAGE]['versus_eat'][c]['delta_J_mean'] < 0]
    if not feasible:
        return dict(policy=None, proceed=False, feasible=[])
    best = feasible[0]
    return dict(policy=best, proceed=True, feasible=feasible,
                versus_reference={f'{g}|{"base" if rule == "base" else "package"}': groups[g][rule]['versus_reference'][best]['delta_J_mean']
                                  for g, rule in cells},
                mass_package_versus_eat=groups['mass'][PACKAGE]['versus_eat'][best]['delta_J_mean'])


def verdict(report, chosen):
    groups = report['groups']
    checks = {key: groups[g][rule]['versus_reference'][chosen] for key, g, rule in
              (('mass|package', 'mass', PACKAGE), ('mix|base', 'mix', 'base'), ('mix|package', 'mix', PACKAGE))}
    eat = groups['mass'][PACKAGE]['versus_eat'][chosen]
    noninferior = all(v['ci95'][1] < MARGIN for v in checks.values())
    return dict(policy=chosen, noninferior_to_reference=noninferior, better_than_eat=eat['ci95'][1] < 0,
                adopted=noninferior and eat['ci95'][1] < 0,
                versus_reference={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in checks.items()},
                mass_package_versus_eat=dict(delta_J=eat['delta_J_mean'], ci95=eat['ci95']))


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def table(block, policies):
    lines = [f'| 정책 | J(초) | 실패 | 환자당 문의 | ΔJ vs {REFERENCE} [95% 구간] | ΔJ vs EAT λ15 |', '|---|---:|---:|---:|---|---:|']
    for p in policies:
        m, v, e = block['metrics'][p], block['versus_reference'].get(p), block['versus_eat'].get(p)
        delta = f"{_f(v['delta_J_mean'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}]" if v else '—'
        lines.append(f"| {p} | {_f(m['J'])} | {m['failed']} | {_f(m['inquiries_per_patient'], 2)} | {delta} | "
                     f"{_f(e['delta_J_mean']) if e else '—'} |")
    return '\n'.join(lines)


def markdown(report):
    lines = [f"# 열람 기록으로 처리 인원 추정(EAT-K) {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['policy']} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '', f"- {v['policy']} → 채택 후보 **{v['adopted']}**"]
        for key, row in v['versus_reference'].items():
            lines.append(f"- 13차 기준 대비 {key}: ΔJ {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
        e = v['mass_package_versus_eat']
        lines += [f"- EAT λ15 대비 다수·PACKAGE: ΔJ {_f(e['delta_J'])} [{_f(e['ci95'][0])}, {_f(e['ci95'][1])}]", '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, by_rule in report['groups'].items():
        for rule, block in by_rule.items():
            lines += [f"## {names[g]} — {'현행 병원 운영' if rule == 'base' else '병원 쪽 수단 모두 사용'}", '',
                      table(block, report['policies']), '']
    lines += ['## 현행(고정3·현행 병원 운영) 대비 패키지', '', '| 묶음 | 정책 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for g, by_policy in report['versus_current'].items():
        for p, v in by_policy.items():
            lines.append(f"| {names[g]} | {p} + 패키지 | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS + wide.LIMITATIONS + LIMITATIONS]
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
    lock = dict(schema_version=1, experiment='inquiry_lab_seen_20261025', selection=report['selection'],
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
        raise RuntimeError('development selected no seen candidate; validation is not run by protocol')
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
