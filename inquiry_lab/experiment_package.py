"""39차 실험: 최종 권장안(서버 + 중증도 + 병원 권장) 확인 실험. 선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_package run --output simulation_results/inquiry_lab_package_validation_20261028

사전 고정:
- 근거: 26차 서버 최종 구성(G), 33차 병원 권장안(빠른 거절 50%·취소 즉시·보조 30초 합류), 34차 중증 환자 적극 문의(eatkp_l0_m6)를
  각각 확인했다. 중증도가 있는 환경에서 이들을 모두 합친 최종 권장안의 크기를 새 seed로 한 번 잰다.
- 환경: 환자마다 중증도를 해시로 고정(높음 20%·중간 50%·낮음 30%, 32차와 같다). priority_runner를 쓴다.
- 구성(같은 시나리오): A 현행(fixed_3·base) · G 서버 최종 구성(eatks_seenbase_k10_m4 · G_RULE) · P G + 중증 적극 문의(eatkp_l0_m6) ·
  HP P + 병원 권장안(33차 H_RULE + 빠른 거절 50%).
- seed(처음 사용): 혼합 2530001–2530400, 다수 2540001–2540300.
- 판정: 혼합·다수 모두 ΔJ(HP − A)의 전체와 중증 환자 95% 구간 상한 < 0이면 확인. P − G, HP − P, HP − G는 판정 없이 보고한다.
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

from . import (eat_capacity_policy, eat_priority_policy, eat_seen_policy, experiment as base, experiment_fastdecline as fastdecline,
               experiment_priority as prio, experiment_system as system, experiment_wide as wide, priority_runner, scenarios)

MIX = tuple(range(2530001, 2530401))
MASS = tuple(range(2540001, 2540301))
ARMS = (('A', '현행(고정3)', 'fixed_3', 'base', 0.0),
        ('G', '서버 최종 구성', prio.FOCUS, prio.G_RULE, 0.0),
        ('P', 'G + 중증 적극 문의', 'eatkp_l0_m6', prio.G_RULE, 0.0),
        ('HP', 'P + 병원 권장안(빠른 거절 50%·취소 즉시·보조 30초 합류)', 'eatkp_l0_m6', system.H_RULE, system.FAST_SHARE))
PAIRS = (('HP', 'A'), ('G', 'A'), ('P', 'G'), ('HP', 'P'), ('HP', 'G'))
SOURCE_FILES = tuple(dict.fromkeys(prio.SOURCE_FILES + system.SOURCE_FILES + ('inquiry_lab/experiment_package.py',)))
SUCCESS_RULE = '혼합·다수 모두 ΔJ(HP − A)의 전체와 중증 환자 95% 구간 상한 < 0이면 확인.'


def run_case(task):
    kind, seed = task
    eat_capacity_policy.install([])
    eat_seen_policy.install([prio.FOCUS])
    eat_priority_policy.install(['eatkp_l0_m6'])
    scenario = prio.with_priority(wide.build(kind, seed))
    branches = {}
    for key, _, policy, rule, share in ARMS:
        b = priority_runner.run_scenario(fastdecline.fast_declines(scenario, share), [(policy, rule)])['branches'][0]
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
        branches[key] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], mix=scenario.get('mix'), patient_count=len(scenario['patients']),
                priorities={p['id']: p['priority'] for p in scenario['patients']}, oracle=scenarios.oracle(scenario), branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def evaluate(results):
    report = dict(groups={})
    for kind in ('mix', 'mass'):
        rs = [r for r in results if r['kind'] == kind]
        rows = {}
        for key, label, _, _, _ in ARMS:
            m = base.policy_metrics(rs, key)
            rows[key] = dict(label=label, J=m['J'], failed=m['failed'], patients=m['patients'], inquiries_per_patient=m['inquiries_per_patient'],
                             review_work_seconds_per_patient=m['review_work_seconds_per_patient'],
                             J_by_level={prio.LEVELS[v]: prio.group_J(rs, key, v) for v in prio.LEVELS})
        comparisons = {f'{a}_minus_{b}': dict(all=prio.group_paired(rs, a, b),
                                              **{prio.LEVELS[v]: prio.group_paired(rs, a, b, v) for v in prio.LEVELS})
                       for a, b in PAIRS}
        report['groups'][kind] = dict(rows=rows, comparisons=comparisons)
    return report


def verdict(report):
    rows = {k: report['groups'][k]['comparisons']['HP_minus_A'] for k in ('mix', 'mass')}
    confirmed = all(v[part]['ci95'][1] is not None and v[part]['ci95'][1] < 0 for v in rows.values() for part in ('all', 'high'))
    return dict(confirmed=confirmed, delta_J={k: {part: dict(delta_J=v[part]['delta_J_mean'], ci95=v[part]['ci95'])
                                                  for part in ('all', 'high', 'mid', 'low')} for k, v in rows.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v = report['verdict']
    lines = ['# 최종 권장안 확인 실험(중증도 반영)', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {SUCCESS_RULE} → 확인 **{v["confirmed"]}**', '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for kind, block in report['groups'].items():
        lines += [f'## {names[kind]}', '', '| 구성 | 전체 J | 중증 J | 중간 J | 경증 J | 실패 | 환자당 문의 | 검토 업무 |', '|---|---:|---:|---:|---:|---:|---:|---:|']
        for key, row in block['rows'].items():
            by = row['J_by_level']
            lines.append(f"| {key} {row['label']} | {_f(row['J'])} | {_f(by['high'])} | {_f(by['mid'])} | {_f(by['low'])} | {row['failed']} | "
                         f"{_f(row['inquiries_per_patient'], 2)} | {_f(row['review_work_seconds_per_patient'])} |")
        lines += ['', '| 비교 | 전체 ΔJ [95%] | 중증 ΔJ [95%] | 중간 ΔJ | 경증 ΔJ |', '|---|---|---|---:|---:|']
        for name, c in block['comparisons'].items():
            fmt = lambda part: f"{_f(c[part]['delta_J_mean'])} [{_f(c[part]['ci95'][0])}, {_f(c[part]['ci95'][1])}]"
            lines.append(f"| {name.replace('_minus_', ' − ')} | {fmt('all')} | {fmt('high')} | {_f(c['mid']['delta_J_mean'])} | {_f(c['low']['delta_J_mean'])} |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS] + [
        '- 중증도 비율(20·50·30%)과 병원 권장안(빠른 거절 50%·취소 즉시·보조 30초 합류)은 가정이다.',
        '- 중증도는 전문가 평가로 입력된다고 본다. 현행(A)은 중증도를 쓰지 않는다.']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(arms=[dict(key=k, label=l, policy=p, rule=r, fast_share=s) for k, l, p, r, s in ARMS],
                  shares=[list(s) for s in prio.SHARES], seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]]), success_rule=SUCCESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_package_20261028', design=design,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    tasks = [('mass', s) for s in MASS] + [('mix', s) for s in MIX]
    results, elapsed = run_all(tasks, workers)
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


if __name__ == '__main__':
    main()
