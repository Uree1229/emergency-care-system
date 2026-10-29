"""38차 실험: 병원이 적은 지역 적응 규칙(문의할 병원 4곳 이하이면 모두 한 번에) 확인 실험.
선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_sparse run --output simulation_results/inquiry_lab_sparse_validation_20261028

사전 고정:
- 근거: 37차 농어촌형(가까운 4곳·거리 차이 2.5배·+300초)에서 EAT는 같은 운영 수단의 고정3보다 보통 상황 +5.1초 [−1.6, +12.2],
  선정 실패 14명 더, 환자당 문의 3.47 vs 3.80곳이었다. 병원이 적으면 문의 비용을 아끼지 말고 모두 한 번에 묻는 규칙을 시험한다.
- 구성(농어촌형, experiment_region.regionalize): R 고정3 + G의 운영 수단 · G 최종 구성(eatks_seenbase_k10_m4) · S 적응 규칙(eatkr_n4_l0).
  보통·다수 사상자 기본 환경은 병원이 5곳 이상이라 규칙이 작동하지 않는다(G와 완전히 같음, 테스트로 확인).
- seed(처음 사용): 혼합 2510001–2510600, 다수 2520001–2520300.
- 판정: ΔJ(S − G) 95% 구간 상한이 혼합·다수 중 하나 이상 < 0이고, 둘 다 < +5초이면 채택 후보. S − R은 판정 없이 보고한다.
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

from . import (eat_capacity_policy, eat_seen_policy, eat_sparse_policy, experiment as base, experiment_region as region,
               experiment_wide as wide, hold2_runner, scenarios)

MIX = tuple(range(2510001, 2510601))
MASS = tuple(range(2520001, 2520301))
RURAL = next(p for k, _, p in region.REGIONS if k == 'rural')
G_RULE, FOCUS = region.G_RULE, region.FOCUS
ARMS = (('R', '고정3 + G의 운영 수단', 'fixed_3'), ('G', '최종 구성', FOCUS), ('S', '병원 4곳 이하이면 모두 한 번에', 'eatkr_n4_l0'))
MARGIN = 5.0
SOURCE_FILES = region.SOURCE_FILES + ('inquiry_lab/eat_sparse_policy.py', 'inquiry_lab/experiment_sparse.py')
SUCCESS_RULE = f'ΔJ(S − G) 상한이 혼합·다수 중 하나 이상 < 0이고 둘 다 < +{MARGIN:.0f}초이면 채택 후보(농어촌형).'


def run_case(task):
    kind, seed = task
    eat_capacity_policy.install([])
    eat_seen_policy.install([FOCUS])
    eat_sparse_policy.install(['eatkr_n4_l0'])
    scenario = region.regionalize(wide.build(kind, seed), **RURAL)
    out = hold2_runner.run_scenario(scenario, [(p, G_RULE) for _, _, p in ARMS])
    names = {p: key for key, _, p in ARMS}
    branches = {}
    for b in out['branches']:
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
        branches[names[b['policy']]] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], mix=scenario.get('mix'), hospitals=len(scenario['hospitals']),
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario), branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def evaluate(results):
    report = dict(groups={})
    for kind in ('mix', 'mass'):
        rs = [r for r in results if r['kind'] == kind]
        rows = {key: base.policy_metrics(rs, key) for key, _, _ in ARMS}
        report['groups'][kind] = dict(
            rows={k: dict(label=next(l for kk, l, _ in ARMS if kk == k), J=m['J'], failed=m['failed'], patients=m['patients'],
                          inquiries_per_patient=m['inquiries_per_patient'],
                          review_work_seconds_per_patient=m['review_work_seconds_per_patient']) for k, m in rows.items()},
            S_minus_G=base.paired(rs, 'S', 'G'), S_minus_R=base.paired(rs, 'S', 'R'), G_minus_R=base.paired(rs, 'G', 'R'))
    return report


def verdict(report):
    rows = {k: report['groups'][k]['S_minus_G'] for k in ('mix', 'mass')}
    better = any(v['ci95'][1] < 0 for v in rows.values())
    noninferior = all(v['ci95'][1] < MARGIN for v in rows.values())
    return dict(better=better, noninferior=noninferior, adopted=better and noninferior,
                delta_J={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in rows.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v = report['verdict']
    lines = ['# 병원이 적은 지역 적응 규칙 확인 실험(농어촌형)', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {SUCCESS_RULE} → 채택 후보 **{v["adopted"]}**', '']
    for k, row in v['delta_J'].items():
        lines.append(f"- {k}: S − G {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
    names = dict(mix='보정 혼합(농어촌형)', mass='다수 사상자(농어촌형)')
    for kind, block in report['groups'].items():
        lines += ['', f'## {names[kind]}', '', '| 구성 | J(초) | 실패 | 환자당 문의 | 검토 업무(초/환자) |', '|---|---:|---:|---:|---:|']
        for key, row in block['rows'].items():
            lines.append(f"| {key} {row['label']} | {_f(row['J'])} | {row['failed']} | {_f(row['inquiries_per_patient'], 2)} | "
                         f"{_f(row['review_work_seconds_per_patient'])} |")
        lines += ['', '| 비교 | ΔJ | 95% 구간 |', '|---|---:|---|']
        for name in ('S_minus_G', 'S_minus_R', 'G_minus_R'):
            c = block[name]
            lines.append(f"| {name.replace('_minus_', ' − ')} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 농어촌형은 생성한 시나리오의 병원 수와 이동 시간만 바꾼 것이다. 실제 지역의 병원 배치·수용 수준은 반영하지 않았다.']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(region=RURAL, rule=G_RULE, arms=[dict(key=k, label=l, policy=p) for k, l, p in ARMS],
                  seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]]), success_rule=SUCCESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_sparse_20261028', design=design,
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
