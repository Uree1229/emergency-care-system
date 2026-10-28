"""37차 실험: 지역형(농어촌형·도시형)에서의 강건성 확인. 선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_region run --output simulation_results/inquiry_lab_region_validation_20261028

사전 고정:
- 근거: 지금까지 시나리오는 병원 5~12곳, 병원 간 이동 시간 차이 15~150초였다. 호남권처럼 병원이 드물고 거리가 먼 지역,
  반대로 병원이 촘촘한 도시에서도 최종 구성이 현행보다 나은지 확인한다(30차 2부와 같은 방식).
- 지역형 변환(생성한 시나리오를 바꾼다):
  농어촌형: 가장 가까운 4곳만 남기고, 병원 간 이동 시간 차이를 2.5배로 늘리고, 기본 이동 시간에 300초를 더한다.
  도시형: 병원은 그대로 두고, 병원 간 이동 시간 차이를 절반으로 줄인다.
  남긴 병원의 수용·회신·검토 시간은 그대로 둔다.
- 구성: A 현행(fixed_3·base) · R 고정3 + G의 운영 수단 · G 26차 최종 구성(eatks_seenbase_k10_m4 · G_RULE).
- seed(처음 사용): 혼합 2490001–2490300, 다수 2500001–2500150(두 지역형 공통).
- 판정: 모든 지역형·묶음에서 ΔJ(G − A) 95% 구간 상한 < 0이면 강건성 확인. G − R은 판정 없이 보고한다.
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

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_levers as levers, experiment_wide as wide,
               hold2_runner, scenarios)

MIX = tuple(range(2490001, 2490301))
MASS = tuple(range(2500001, 2500151))
FOCUS, G_RULE = levers.FOCUS, levers.G_RULE
REGIONS = (('rural', '농어촌형(병원 4곳·거리 차이 2.5배·기본 +300초)', dict(keep=4, stretch=2.5, offset=300)),
           ('urban', '도시형(거리 차이 절반)', dict(keep=None, stretch=0.5, offset=0)))
ARMS = (('A', '현행(고정3)', 'fixed_3', 'base'), ('R', '고정3 + G의 운영 수단', 'fixed_3', G_RULE), ('G', '최종 구성', FOCUS, G_RULE))
SOURCE_FILES = levers.SOURCE_FILES + ('inquiry_lab/experiment_region.py',)
SUCCESS_RULE = '모든 지역형·묶음에서 ΔJ(G − A) 95% 구간 상한 < 0이면 강건성 확인.'


def regionalize(scenario, keep=None, stretch=1.0, offset=0):
    """가까운 keep곳만 남기고, 환자마다 가장 가까운 병원과의 이동 시간 차이를 stretch배로, 기본 이동 시간에 offset초를 더한다."""
    order = sorted(scenario['hospitals'], key=lambda h: min(p['travel_seconds'][h['id']] for p in scenario['patients']))
    kept = order[:keep] if keep else order
    ids = {h['id'] for h in kept}
    patients = []
    for p in scenario['patients']:
        nearest = min(p['travel_seconds'][h] for h in ids)
        travel = {h: max(60, int(round(offset + nearest + (t - nearest) * stretch))) for h, t in p['travel_seconds'].items() if h in ids}
        patients.append(dict(p, travel_seconds=travel))
    return dict(scenario, hospitals=[h for h in scenario['hospitals'] if h['id'] in ids], patients=patients,
                potentials=[q for q in scenario['potentials'] if q['hospital_id'] in ids],
                history=[r for r in scenario.get('history', []) if r['hospital_id'] in ids])


def run_case(task):
    region, kind, seed = task
    eat_capacity_policy.install([])
    eat_seen_policy.install([FOCUS])
    params = next(p for k, _, p in REGIONS if k == region)
    scenario = regionalize(wide.build(kind, seed), **params)
    out = hold2_runner.run_scenario(scenario, [(p, r) for _, _, p, r in ARMS])
    names = {f'{p}|{r}': key for key, _, p, r in ARMS}
    branches = {}
    for b in out['branches']:
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
        branches[names[f"{b['policy']}|{b['rule']}"]] = row
    return dict(region=region, kind=kind, seed=seed, scenario_id=scenario['id'], mix=scenario.get('mix'),
                hospitals=len(scenario['hospitals']), patient_count=len(scenario['patients']),
                oracle=scenarios.oracle(scenario), branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def evaluate(results):
    report = dict(regions={})
    for region, label, _ in REGIONS:
        block = {}
        for kind in ('mix', 'mass'):
            rs = [r for r in results if r['region'] == region and r['kind'] == kind]
            metrics = {key: base.policy_metrics(rs, key) for key, _, _, _ in ARMS}
            block[kind] = dict(rows={k: dict(J=m['J'], failed=m['failed'], patients=m['patients'],
                                             inquiries_per_patient=m['inquiries_per_patient']) for k, m in metrics.items()},
                               G_minus_A=base.paired(rs, 'G', 'A'), G_minus_R=base.paired(rs, 'G', 'R'), R_minus_A=base.paired(rs, 'R', 'A'))
        report['regions'][region] = dict(label=label, groups=block)
    return report


def verdict(report):
    robust = {region: all(report['regions'][region]['groups'][k]['G_minus_A']['ci95'][1] < 0 for k in ('mix', 'mass'))
              for region, _, _ in REGIONS}
    return dict(robust_by_region=robust, robust=all(robust.values()))


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v = report['verdict']
    lines = ['# 지역형 강건성 확인 실험', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {SUCCESS_RULE} → 강건성 확인 **{v["robust"]}**', '',
             '| 지역형 | 묶음 | A J | R J | G J | G − A [95% 구간] | G − R [95% 구간] | 실패(A·R·G) |', '|---|---|---:|---:|---:|---|---|---|']
    for region, label, _ in REGIONS:
        for kind in ('mix', 'mass'):
            g = report['regions'][region]['groups'][kind]
            rows, ga, gr = g['rows'], g['G_minus_A'], g['G_minus_R']
            lines.append(f"| {label} | {kind} | {_f(rows['A']['J'])} | {_f(rows['R']['J'])} | {_f(rows['G']['J'])} | "
                         f"{_f(ga['delta_J_mean'])} [{_f(ga['ci95'][0])}, {_f(ga['ci95'][1])}] | "
                         f"{_f(gr['delta_J_mean'])} [{_f(gr['ci95'][0])}, {_f(gr['ci95'][1])}] | "
                         f"{rows['A']['failed']}·{rows['R']['failed']}·{rows['G']['failed']} |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 지역형은 생성한 시나리오의 병원 수와 이동 시간만 바꾼 것이다. 실제 지역의 병원 배치·수용 수준은 반영하지 않았다.']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(regions=[dict(key=k, label=l, params=p) for k, l, p in REGIONS],
                  arms=[dict(key=k, label=l, policy=p, rule=r) for k, l, p, r in ARMS],
                  seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]]), success_rule=SUCCESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_region_20261028', design=design,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    tasks = [(region, 'mass', s) for region, _, _ in REGIONS for s in MASS] + [(region, 'mix', s) for region, _, _ in REGIONS for s in MIX]
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
