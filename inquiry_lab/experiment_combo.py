"""21차 실험: '응답 없이 닫힘 즉시 종료(nr0) + 회신 즉시 재판단(ev)' 조합 확인 실험.
선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다(17차와 같은 방식).

python -m inquiry_lab.experiment_combo run --output simulation_results/inquiry_lab_combo_validation_20261026

사전 고정:
- 근거: 19차 nr0 단독은 혼합 판정 미달(−3.5초 [−7.4, +0.2]), 20차 개발에서 nr0 + ev 조합이 혼합 −5.5초·다수 −20.8초였다.
  nr0이 '거절과 같은 사건'을 만들고 ev가 그 사건에 바로 반응한다는 가설을 새 seed로 확인한다.
- 구성(모두 event_runner):
  A 현행: fixed_3 · base
  E 17차 최종: eatks_seenbase_k10_m4 · PACKAGE(보조 호출 + 선제 호출 + 취소 중단 + 분산 정렬)
  F 새 구성: eatks_seenbase_k10_m4 · PACKAGE + nr0 + ev
  참고: fixed_3 · PACKAGE + nr0 + ev
- seed(처음 사용): 혼합 1790001–1790400, 다수 1800001–1800200, 8유형 1810001–1810006.
- 판정: 혼합 ΔJ(F − E)의 95% 구간 상한 < 0 이고 다수 사상자 ΔJ(F − E)의 95% 구간 상한 < +5초이면 채택 후보.
  F − A(현행 대비 전체 효과)는 판정 없이 보고한다.
- 실행 전에 설계와 소스 해시를 잠금 파일로 먼저 쓴다. 기존 결과가 있으면 덮어쓰지 않는다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import eat_capacity_policy, eat_seen_policy, event_runner, experiment as base, experiment_event as prev, experiment_wide as wide, scenarios

MIX = tuple(range(1790001, 1790401))
MASS = tuple(range(1800001, 1800201))
FAMILY = (1810001, 1810002, 1810003, 1810004, 1810005, 1810006)
FOCUS = prev.FOCUS
PACKAGE = prev.PACKAGE
COMBO = prev.COMBO
ARMS = (('A', '현행(고정3)', 'fixed_3', 'base'),
        ('E', '17차 최종 구성', FOCUS, PACKAGE),
        ('F', '+ 응답 없이 닫힘 즉시 종료 + 회신 즉시 재판단', FOCUS, COMBO),
        ('R', '참고: 고정3 + 같은 수단', 'fixed_3', COMBO))
MARGIN = 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/experiment_combo.py',)
SUCCESS_RULE = f'혼합 ΔJ(F − E) 95% 구간 상한 < 0 이고 다수 사상자 ΔJ(F − E) 95% 구간 상한 < +{MARGIN:.0f}초이면 채택 후보.'


def arm(policy, rule):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    scenario = wide.build(kind, seed)
    result = event_runner.run_scenario(scenario, arm_list)
    branches = {}
    for b in result['branches']:
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'])
        branches[arm(b['policy'], b['rule'])] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario), branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def _tasks():
    arm_list = [(p, r) for _, _, p, r in ARMS]
    tasks = [('mass', s, arm_list) for s in MASS]
    tasks += [('mix', s, arm_list) for s in MIX]
    tasks += [(f'family:{f}', s, arm_list) for f in scenarios.original.FAMILY_NAMES for s in FAMILY]
    return tasks


def evaluate(results):
    groups = dict(mix=[r for r in results if r['kind'] == 'mix'], mass=[r for r in results if r['kind'] == 'mass'])
    fam = [r for r in results if r['kind'] not in groups]
    names = {key: arm(p, r) for key, _, p, r in ARMS}
    report = dict(groups={})
    for g, rs in groups.items():
        rows = {}
        for key, label, p, r in ARMS:
            m = base.policy_metrics(rs, names[key])
            rows[key] = dict(label=label, arm=names[key], J=m['J'], failed=m['failed'], patients=m['patients'],
                             inquiries_per_patient=m['inquiries_per_patient'])
        comparisons = dict(F_minus_E=base.paired(rs, names['F'], names['E']), F_minus_A=base.paired(rs, names['F'], names['A']),
                           E_minus_A=base.paired(rs, names['E'], names['A']), R_minus_A=base.paired(rs, names['R'], names['A']))
        by_arrival = {a: base.paired([x for x in rs if x['mix']['arrival'] == a], names['F'], names['E'])
                      for a in sorted({x['mix']['arrival'] for x in rs})}
        report['groups'][g] = dict(rows=rows, comparisons=comparisons, F_minus_E_by_arrival=by_arrival)
    report['families'] = base.family_table(fam, list(names.values()))
    return report


def verdict(report):
    mix = report['groups']['mix']['comparisons']['F_minus_E']
    mass = report['groups']['mass']['comparisons']['F_minus_E']
    return dict(mix_better=mix['ci95'][1] < 0, mass_noninferior=mass['ci95'][1] < MARGIN,
                adopted=mix['ci95'][1] < 0 and mass['ci95'][1] < MARGIN,
                mix=dict(delta_J=mix['delta_J_mean'], ci95=mix['ci95']), mass=dict(delta_J=mass['delta_J_mean'], ci95=mass['ci95']))


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v = report['verdict']
    lines = ['# 응답 없이 닫힘 종료 + 회신 즉시 재판단 확인 실험', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정 규칙:** {SUCCESS_RULE}', '',
             f"- 혼합 F − E: {_f(v['mix']['delta_J'])} [{_f(v['mix']['ci95'][0])}, {_f(v['mix']['ci95'][1])}], "
             f"다수 F − E: {_f(v['mass']['delta_J'])} [{_f(v['mass']['ci95'][0])}, {_f(v['mass']['ci95'][1])}] → 채택 후보 **{v['adopted']}**", '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, block in report['groups'].items():
        lines += [f'## {names[g]}', '', '| 구성 | 내용 | J(초) | 실패 | 환자당 문의 |', '|---|---|---:|---:|---:|']
        for key, row in block['rows'].items():
            lines.append(f"| {key} | {row['label']} | {_f(row['J'])} | {row['failed']} | {_f(row['inquiries_per_patient'], 2)} |")
        lines += ['', '| 비교 | ΔJ | 95% 구간 |', '|---|---:|---|']
        for key, c in block['comparisons'].items():
            lines.append(f"| {key.replace('_minus_', ' − ')} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
        lines += ['', '| 도착 형태 | F − E | 95% 구간 |', '|---|---:|---|']
        for a, c in block['F_minus_E_by_arrival'].items():
            lines.append(f"| {a} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS]
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(arms=[dict(key=k, label=l, policy=p, rule=r) for k, l, p, r in ARMS],
                  seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]], families=list(FAMILY)), success_rule=SUCCESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_combo_20261026', design=design,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    results, elapsed = run_all(_tasks(), workers)
    if {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES} != hashes:
        raise RuntimeError('source changed during the confirmatory run')
    report = dict(stage='confirm', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), scenario_count=len(results),
                  elapsed_seconds=elapsed, design=design, lock=base.relative_path(lock_path),
                  lock_sha256=base.sha256_file(lock_path), source_sha256=hashes,
                  runtime=dict(python=platform.python_version()), results=results, **evaluate(results))
    report['verdict'] = verdict(report)
    tmp = json_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(json_path)
    md_path.write_text(markdown(report), encoding='utf-8')
    return json_path, md_path, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='stage', required=True)
    go = sub.add_parser('run')
    go.add_argument('--output', required=True)
    go.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    _, _, report = run(args.output, args.workers)
    print('verdict', report['verdict'])
    for g, block in report['groups'].items():
        for key, c in block['comparisons'].items():
            print(g, key, round(c['delta_J_mean'], 1), [round(x, 1) for x in c['ci95']])


if __name__ == '__main__':
    main()
