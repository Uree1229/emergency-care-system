"""16차 실험: 다수 사상자 선제 호출. 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_surge development --output simulation_results/inquiry_lab_surge_development_20261025
python -m inquiry_lab.experiment_surge validation --lock simulation_results/inquiry_lab_surge_development_20261025_lock.json \
    --output simulation_results/inquiry_lab_surge_validation_20261025

사전 고정:
- 질문: 최근 120초 안에 등록된 환자가 N명 이상이면 서버가 모든 병원(평소 2명 병원 포함)의 보조 담당을 바로 부르면,
  미회신 문의가 쌓인 뒤 부르는 8차 반응형 호출보다 다수 사상자에서 J가 줄어드는가. 평소에는 거의 작동하지 않아야 한다.
- 환경: 13차와 같은 생성기(보정 혼합·다수 사상자·기존 8유형). 새 seed만 사용:
  개발 혼합 1520001–1520240·다수 1540001–1540120·8유형 1560001–1560003,
  검증 혼합 1530001–1530400·다수 1550001–1550200·8유형 1570001–1570006.
- 문의 정책: fixed_3, eat_l15_f, eatk_live_k10_m4(13차 채택 후보, 선택·판정 기준).
- 운영: base, PACKAGE(8~10차 반응형 보조 호출 + 취소 중단 + 분산 정렬), PACKAGE + 선제 호출 sg_n6_w120·sg_n10_w120.
- 선택(개발, EAT-K): 혼합 ΔJ(선제 − PACKAGE) ≤ +2초인 변형 중 다수 사상자 J 최소. 그 다수 사상자 ΔJ < 0이 아니면 검증하지 않는다.
- 판정(검증, EAT-K): 다수 사상자 ΔJ(선제 − PACKAGE) 95% 구간 상한 < 0 이고 혼합 95% 구간 상한 < +5초이면 채택 후보.
  보조 근무시간(환자당)·선제 호출이 작동한 시나리오 비율·고정3·EAT 결과는 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import eat_capacity_policy, experiment as base, experiment_seen as prev, experiment_wide as wide, scenarios, surge_runner

DEV_MIX = tuple(range(1520001, 1520241))
DEV_MASS = tuple(range(1540001, 1540121))
DEV_FAMILY = (1560001, 1560002, 1560003)
VAL_MIX = tuple(range(1530001, 1530401))
VAL_MASS = tuple(range(1550001, 1550201))
VAL_FAMILY = (1570001, 1570002, 1570003, 1570004, 1570005, 1570006)
FOCUS = eat_capacity_policy.variant_name('live', 10, 4)
POLICIES = ('fixed_3', 'eat_l15_f', FOCUS)
PACKAGE = wide.PACKAGE


def surge_package(n):
    return f'od_k2_d60_l120+{surge_runner.surge_name(n, 120)}+ci_r30_q50+ord_spread_o100_c50'


CANDIDATES = (surge_package(6), surge_package(10))
RULES = ('base', PACKAGE) + CANDIDATES
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/surge_runner.py', 'inquiry_lab/experiment_surge.py')
SELECTION_RULE = (f'개발(EAT-K): 혼합 ΔJ(선제 − PACKAGE) ≤ +{TOLERANCE:.0f}초인 변형 중 다수 사상자 J 최소. '
                  '그 다수 사상자 ΔJ < 0이 아니면 검증하지 않는다.')
SUCCESS_RULE = (f'검증(EAT-K): 다수 사상자 ΔJ(선제 − PACKAGE) 95% 구간 상한 < 0 이고 혼합 95% 구간 상한 < +{MARGIN:.0f}초이면 채택 후보.')
LIMITATIONS = ['선제 호출은 병원마다 보조 담당 1명이 있고 호출 60초 뒤 합류한다는 가정이다. 평소 2명 병원도 3번째 담당을 부른다.',
               '다수 사상자 감지는 등록 환자 수만 본다. 실제로는 현장 보고·사고 유형 같은 정보로 더 일찍 알 수 있다.']


def arm(policy, rule):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, arm_list = task
    eat_capacity_policy.install([p for p, _ in arm_list if p.startswith(eat_capacity_policy.PREFIX)])
    scenario = wide.build(kind, seed)
    result = surge_runner.run_scenario(scenario, arm_list)
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={arm(b['policy'], b['rule']): wide.prev.prev.compact(b) for b in result['branches']})


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def _tasks(mix_seeds, mass_seeds, family_seeds, rules):
    arm_list = [(p, r) for p in POLICIES for r in rules]
    tasks = [('mass', s, arm_list) for s in mass_seeds]
    tasks += [('mix', s, arm_list) for s in mix_seeds]
    tasks += [(f'family:{f}', s, arm_list) for f in scenarios.original.FAMILY_NAMES for s in family_seeds]
    return tasks


def backup_cost(results, name):
    patients = sum(r['patient_count'] for r in results)
    duty = sum(r['branches'][name]['backup']['on_duty_seconds'] for r in results)
    triggered = sum(1 for r in results if r['branches'][name]['backup'].get('surge_calls', 0) > 0)
    return dict(on_duty_seconds_per_patient=duty / patients, surge_triggered_share=triggered / len(results) if results else None)


def evaluate(results, rules):
    groups = dict(mix=[r for r in results if r['kind'] == 'mix'], mass=[r for r in results if r['kind'] == 'mass'])
    fam = [r for r in results if r['kind'] not in groups]
    report = dict(groups={})
    for g, rs in groups.items():
        if not rs:
            continue
        block = {}
        for p in POLICIES:
            rows = {}
            for rule in rules:
                name = arm(p, rule)
                rows[rule] = dict(metrics=base.policy_metrics(rs, name), cost=backup_cost(rs, name))
                if rule != PACKAGE and rule != 'base':
                    rows[rule]['versus_package'] = base.paired(rs, name, arm(p, PACKAGE))
                if rule != 'base':
                    rows[rule]['versus_current'] = base.paired(rs, name, arm('fixed_3', 'base'))
            block[p] = rows
        report['groups'][g] = block
    report['by_arrival'] = {}
    for g, rs in groups.items():
        for a in sorted({r['mix']['arrival'] for r in rs}):
            subset = [r for r in rs if r['mix']['arrival'] == a]
            report['by_arrival'][f'{g}:{a}'] = {rule: base.paired(subset, arm(FOCUS, rule), arm(FOCUS, PACKAGE))
                                                for rule in rules if rule not in ('base', PACKAGE)}
    report['families'] = base.family_table(fam, [arm(p, r) for p in POLICIES for r in rules])
    return report


def select(report):
    mix, mass = report['groups']['mix'][FOCUS], report['groups']['mass'][FOCUS]
    feasible = [c for c in CANDIDATES if mix[c]['versus_package']['delta_J_mean'] <= TOLERANCE]
    if not feasible:
        return dict(rule=None, proceed=False, feasible=[])
    best = min(feasible, key=lambda c: (round(mass[c]['metrics']['J'], 9), CANDIDATES.index(c)))
    delta = mass[best]['versus_package']['delta_J_mean']
    return dict(rule=best, proceed=delta < 0, feasible=feasible, mass_delta_J=delta,
                mix_delta_J=mix[best]['versus_package']['delta_J_mean'])


def verdict(report, chosen):
    mass = report['groups']['mass'][FOCUS][chosen]['versus_package']
    mix = report['groups']['mix'][FOCUS][chosen]['versus_package']
    return dict(rule=chosen, mass_better=mass['ci95'][1] < 0, mix_noninferior=mix['ci95'][1] < MARGIN,
                adopted=mass['ci95'][1] < 0 and mix['ci95'][1] < MARGIN, mass_delta_J=mass['delta_J_mean'],
                mass_ci95=mass['ci95'], mix_delta_J=mix['delta_J_mean'], mix_ci95=mix['ci95'])


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    rules = report['rules']
    lines = [f"# 다수 사상자 선제 호출 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['rule']} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '',
                  f"- {v['rule']}: 다수 사상자 ΔJ {_f(v['mass_delta_J'])} [{_f(v['mass_ci95'][0])}, {_f(v['mass_ci95'][1])}], "
                  f"혼합 ΔJ {_f(v['mix_delta_J'])} [{_f(v['mix_ci95'][0])}, {_f(v['mix_ci95'][1])}] → 채택 후보 **{v['adopted']}**", '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, by_policy in report['groups'].items():
        lines += [f'## {names[g]}', '', '| 정책 | 운영 | J(초) | 실패 | ΔJ vs PACKAGE [95% 구간] | ΔJ vs 현행 | 보조 근무(초/환자) | 선제 호출 작동 |',
                  '|---|---|---:|---:|---|---:|---:|---:|']
        for p, rows in by_policy.items():
            for rule in rules:
                row = rows[rule]
                vp, vc = row.get('versus_package'), row.get('versus_current')
                lines.append(f"| {p} | {rule} | {_f(row['metrics']['J'])} | {row['metrics']['failed']} | "
                             f"{(_f(vp['delta_J_mean']) + ' [' + _f(vp['ci95'][0]) + ', ' + _f(vp['ci95'][1]) + ']') if vp else '—'} | "
                             f"{_f(vc['delta_J_mean']) if vc else '—'} | {_f(row['cost']['on_duty_seconds_per_patient'])} | "
                             f"{_f(100 * row['cost']['surge_triggered_share'], 0)}% |")
        lines.append('')
    lines += [f'## 도착 형태별 ΔJ(선제 − PACKAGE, {FOCUS})', '', '| 묶음 | 운영 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for key, by_rule in report['by_arrival'].items():
        for rule, v in by_rule.items():
            lines.append(f"| {key} | {rule} | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
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
    results, elapsed = run_all(_tasks(DEV_MIX, DEV_MASS, DEV_FAMILY, RULES), workers)
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(RULES),
                  selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], mass=[DEV_MASS[0], DEV_MASS[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, RULES))
    report['selection'] = select(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_surge_20261025', selection=report['selection'],
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
        raise RuntimeError('development selected no surge rule; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['rule']
    rules = ('base', PACKAGE, chosen)
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_MASS, VAL_FAMILY, rules), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(rules),
                  selected_rule=chosen, lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], mass=[VAL_MASS[0], VAL_MASS[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, rules))
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
        for g, by_policy in report['groups'].items():
            for p, rows in by_policy.items():
                for rule, row in rows.items():
                    vp = row.get('versus_package')
                    print(g, p, rule[:40], round(row['metrics']['J'], 1), None if vp is None else round(vp['delta_J_mean'], 1),
                          round(row['cost']['on_duty_seconds_per_patient'], 1), round(row['cost']['surge_triggered_share'] or 0, 2))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
