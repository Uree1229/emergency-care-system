"""33차 실험: 시스템 전체 권장안(서버 최종 구성 + 병원 화면·운영 권장) 확인 실험.
선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_system run --output simulation_results/inquiry_lab_system_validation_20261028

사전 고정:
- 근거: 26차에서 서버 최종 구성(G)을 확인했고, 30·31차에서 병원 쪽 지렛대(빠른 거절·취소 즉시 확인·보조 빠른 합류)를
  하나씩 확인했다. 이들을 한꺼번에 넣었을 때 현행보다 얼마나 나은지, 발표용 최종 숫자를 새 seed로 한 번 잰다.
- 구성(같은 시나리오):
  A 현행: fixed_3 · base
  G 서버 최종 구성: eatks_seenbase_k10_m4 · G_RULE
  H 시스템 권장안: G + 빠른 거절 50%(31차) + 취소 즉시·전원 중단 + 보조 30초 합류(30차)
  RH 참고: fixed_3 + H와 같은 운영·병원 권장
  검토 시간 20% 단축은 일반 가정이라 넣지 않았다. 문의함 순서 100% 따름은 30차에서 보통 상황 효과가 확인되지 않아 넣지 않았다.
- seed(처음 사용): 혼합 2380001–2380400, 다수 2390001–2390200.
- 판정: 혼합·다수 모두 ΔJ(H − G) 95% 구간 상한 < 0이면 '병원 권장안 효과 확인'. H − A, G − A, H − RH는 판정 없이 보고한다.
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

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_fastdecline as fastdecline,
               experiment_levers as levers, experiment_wide as wide, hold2_runner, scenarios)

MIX = tuple(range(2380001, 2380401))
MASS = tuple(range(2390001, 2390201))
FOCUS, G_RULE = levers.FOCUS, levers.G_RULE
H_RULE = levers.operating(delay=30, notice=0, comply=100)
FAST_SHARE = 0.5
ARMS = (('A', '현행(고정3)', 'fixed_3', 'base', 0.0),
        ('G', '서버 최종 구성', FOCUS, G_RULE, 0.0),
        ('H', '시스템 권장안(G + 빠른 거절 50% + 취소 즉시 + 보조 30초 합류)', FOCUS, H_RULE, FAST_SHARE),
        ('RH', '참고: 고정3 + 같은 운영·병원 권장', 'fixed_3', H_RULE, FAST_SHARE))
SOURCE_FILES = tuple(dict.fromkeys(levers.SOURCE_FILES + fastdecline.SOURCE_FILES + ('inquiry_lab/experiment_system.py',)))
SUCCESS_RULE = '혼합·다수 모두 ΔJ(H − G) 95% 구간 상한 < 0이면 병원 권장안 효과 확인.'


def run_case(task):
    kind, seed = task
    eat_capacity_policy.install([])
    eat_seen_policy.install([FOCUS])
    scenario = wide.build(kind, seed)
    branches = {}
    for key, _, policy, rule, share in ARMS:
        b = hold2_runner.run_scenario(fastdecline.fast_declines(scenario, share), [(policy, rule)])['branches'][0]
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
        branches[key] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], mix=scenario.get('mix'),
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
        rows = {}
        for key, label, _, _, _ in ARMS:
            m = base.policy_metrics(rs, key)
            rows[key] = dict(label=label, J=m['J'], failed=m['failed'], patients=m['patients'],
                             inquiries_per_patient=m['inquiries_per_patient'],
                             review_work_seconds_per_patient=m['review_work_seconds_per_patient'])
        comparisons = {f'{a}_minus_{b}': base.paired(rs, a, b) for a, b in (('H', 'G'), ('H', 'A'), ('G', 'A'), ('H', 'RH'), ('RH', 'A'))}
        sensitivity = {str(penalty): {f'{a}_minus_{b}': base.paired(rs, a, b, penalty=penalty) for a, b in (('H', 'A'), ('G', 'A'))}
                       for penalty in (600, 3000)}
        by_arrival = {a: base.paired([x for x in rs if x['mix']['arrival'] == a], 'H', 'G') for a in sorted({x['mix']['arrival'] for x in rs})}
        report['groups'][kind] = dict(rows=rows, comparisons=comparisons, penalty_sensitivity=sensitivity, H_minus_G_by_arrival=by_arrival)
    return report


def verdict(report):
    rows = {k: report['groups'][k]['comparisons']['H_minus_G'] for k in ('mix', 'mass')}
    return dict(confirmed=all(v['ci95'][1] < 0 for v in rows.values()),
                delta_J={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in rows.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v = report['verdict']
    lines = ['# 시스템 전체 권장안 확인 실험', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {SUCCESS_RULE} → 확인 **{v["confirmed"]}**', '']
    for k, row in v['delta_J'].items():
        lines.append(f"- {k}: H − G {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for kind, block in report['groups'].items():
        lines += ['', f'## {names[kind]}', '', '| 구성 | J(초) | 실패 | 환자당 문의 | 검토 업무(초/환자) |', '|---|---:|---:|---:|---:|']
        for key, row in block['rows'].items():
            lines.append(f"| {key} {row['label']} | {_f(row['J'])} | {row['failed']} | {_f(row['inquiries_per_patient'], 2)} | "
                         f"{_f(row['review_work_seconds_per_patient'])} |")
        lines += ['', '| 비교 | ΔJ | 95% 구간 |', '|---|---:|---|']
        for name, c in block['comparisons'].items():
            lines.append(f"| {name.replace('_minus_', ' − ')} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
        lines += ['', '| 실패 비용 | H − A | G − A |', '|---|---:|---:|']
        for penalty, comps in block['penalty_sensitivity'].items():
            lines.append(f"| {penalty}초 | {_f(comps['H_minus_A']['delta_J_mean'])} | {_f(comps['G_minus_A']['delta_J_mean'])} |")
        lines += ['', '| 도착 형태 | H − G | 95% 구간 |', '|---|---:|---|']
        for a, c in block['H_minus_G_by_arrival'].items():
            lines.append(f"| {a} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 병원 권장안(빠른 거절 50%·취소 즉시·보조 30초 합류)은 병원 화면·운영의 협조를 가정한 값이다. 실제 달성 수준은 측정하지 않았다.']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(arms=[dict(key=k, label=l, policy=p, rule=r, fast_share=s) for k, l, p, r, s in ARMS],
                  seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]]), success_rule=SUCCESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_system_20261028', design=design,
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
