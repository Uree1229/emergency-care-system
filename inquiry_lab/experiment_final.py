"""17차 실험: 최종 통합 검증(확인 실험). 선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행.

python -m inquiry_lab.experiment_final run --output simulation_results/inquiry_lab_final_validation_20261025

사전 고정:
- 질문: 1~16차에서 채택 기준을 통과한 수단을 차례로 더하면, 현행(고정3·현행 병원 운영)보다 얼마나 줄어드는가.
  보통 상황(보정 혼합)과 다수 사상자를 따로 본다.
- 단계(누적):
  A 현행: fixed_3 · base
  B 문의 정책 EAT: eat_l15_f · base(1차)
  C 처리 용량을 아는 EAT: eatks_seenbase_k10_m4 · base(12·13·15차). 현행 운영에서는 평소 인원만 쓰는 것과 같다.
  D + 병원 쪽 수단: eatks_seenbase_k10_m4 · PACKAGE(8차 보조 호출 + 9차 취소 중단 + 10차 분산 정렬)
  E + 다수 사상자 선제 호출: eatks_seenbase_k10_m4 · PACKAGE + sg_n10_w120(16차)
- seed(처음 사용, 개발 단계 없음): 혼합 1580001–1580400, 다수 1590001–1590200, 8유형 1600001–1600006.
- 판정: E − A의 시나리오 단위 부트스트랩 95% 구간 상한 < 0을 혼합·다수 각각에서 확인한다. 단계별 차이는 판정 없이 보고한다.
- 실행 전에 이 설계와 소스 해시를 잠금 파일로 먼저 쓴다. 기존 결과가 있으면 덮어쓰지 않는다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_surge as prev, experiment_wide as wide,
               scenarios, surge_runner)

MIX = tuple(range(1580001, 1580401))
MASS = tuple(range(1590001, 1590201))
FAMILY = (1600001, 1600002, 1600003, 1600004, 1600005, 1600006)
EATK = eat_seen_policy.variant_name('seenbase', 10, 4)
PACKAGE = wide.PACKAGE
SURGE = prev.surge_package(10)
STEPS = (('A', '현행(고정3)', 'fixed_3', 'base'),
         ('B', '문의 정책 EAT', 'eat_l15_f', 'base'),
         ('C', '처리 용량을 아는 EAT', EATK, 'base'),
         ('D', '+ 병원 쪽 수단(보조 호출·취소 중단·분산 정렬)', EATK, PACKAGE),
         ('E', '+ 다수 사상자 선제 호출', EATK, SURGE))
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/experiment_final.py',)
SUCCESS_RULE = 'E − A의 시나리오 단위 부트스트랩 95% 구간 상한 < 0을 혼합·다수 각각에서 확인한다.'


def arm(policy, rule):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
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


def _tasks():
    arm_list = [(p, r) for _, _, p, r in STEPS]
    tasks = [('mass', s, arm_list) for s in MASS]
    tasks += [('mix', s, arm_list) for s in MIX]
    tasks += [(f'family:{f}', s, arm_list) for f in scenarios.original.FAMILY_NAMES for s in FAMILY]
    return tasks


def evaluate(results):
    groups = dict(mix=[r for r in results if r['kind'] == 'mix'], mass=[r for r in results if r['kind'] == 'mass'])
    fam = [r for r in results if r['kind'] not in groups]
    report = dict(groups={})
    for g, rs in groups.items():
        rows = []
        first = arm(STEPS[0][2], STEPS[0][3])
        previous = first
        for key, label, p, r in STEPS:
            name = arm(p, r)
            m = base.policy_metrics(rs, name)
            row = dict(step=key, label=label, arm=name, J=m['J'], failed=m['failed'], patients=m['patients'],
                       inquiries_per_patient=m['inquiries_per_patient'],
                       backup_on_duty_seconds_per_patient=prev.backup_cost(rs, name)['on_duty_seconds_per_patient'])
            if name != first:
                row['versus_current'] = base.paired(rs, name, first)
                row['versus_previous'] = base.paired(rs, name, previous)
            rows.append(row)
            previous = name
        by_arrival = {}
        for a in sorted({x['mix']['arrival'] for x in rs}):
            subset = [x for x in rs if x['mix']['arrival'] == a]
            by_arrival[a] = base.paired(subset, arm(STEPS[-1][2], STEPS[-1][3]), first)
        report['groups'][g] = dict(steps=rows, final_by_arrival=by_arrival)
    report['families'] = base.family_table(fam, [arm(p, r) for _, _, p, r in STEPS])
    return report


def verdict(report):
    out = {}
    for g, block in report['groups'].items():
        last = block['steps'][-1]['versus_current']
        out[g] = dict(confirmed=last['ci95'][1] < 0, delta_J=last['delta_J_mean'], ci95=last['ci95'])
    return out


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    lines = ['# 최종 통합 검증 결과', '', f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정 규칙:** {SUCCESS_RULE}', '']
    for g, v in report['verdict'].items():
        lines.append(f"- {'보정 혼합' if g == 'mix' else '다수 사상자'}: E − A = {_f(v['delta_J'])}초 [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] → 확인 **{v['confirmed']}**")
    lines.append('')
    for g, block in report['groups'].items():
        lines += [f"## {'보정 혼합' if g == 'mix' else '다수 사상자'}", '',
                  '| 단계 | 내용 | J(초) | 실패 | 현행 대비 ΔJ [95% 구간] | 앞 단계 대비 ΔJ [95% 구간] | 보조 근무(초/환자) |',
                  '|---|---|---:|---:|---|---|---:|']
        for row in block['steps']:
            vc, vp = row.get('versus_current'), row.get('versus_previous')
            fmt = lambda v: f"{_f(v['delta_J_mean'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}]" if v else '—'
            lines.append(f"| {row['step']} | {row['label']} | {_f(row['J'])} | {row['failed']} | {fmt(vc)} | {fmt(vp)} | "
                         f"{_f(row['backup_on_duty_seconds_per_patient'])} |")
        lines += ['', '| 도착 형태 | 최종(E) − 현행(A) | 95% 구간 |', '|---|---:|---|']
        for a, v in block['final_by_arrival'].items():
            lines.append(f"| {a} | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS + wide.LIMITATIONS + prev.LIMITATIONS]
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(steps=[dict(step=k, label=l, policy=p, rule=r) for k, l, p, r in STEPS],
                  seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]], families=list(FAMILY)), success_rule=SUCCESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_final_20261025', design=design,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    results, elapsed = run_all(_tasks(), workers)
    current = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    if current != hashes:
        raise RuntimeError('source changed during the confirmatory run')
    report = dict(stage='final', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), scenario_count=len(results),
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
        for row in block['steps']:
            v = row.get('versus_current')
            print(g, row['step'], round(row['J'], 1), row['failed'], None if v is None else round(v['delta_J_mean'], 1))


if __name__ == '__main__':
    main()
