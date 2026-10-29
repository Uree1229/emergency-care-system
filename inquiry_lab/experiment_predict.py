"""40차 실험: 수용 여부를 일부 예측할 수 있는 환경에서 질환 유형을 아는 EAT-K의 몫. 선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_predict run --output simulation_results/inquiry_lab_predict_validation_20261028

사전 고정:
- 근거: 기존 시나리오는 수용 여부가 환자와 무관한 무작위라, 남은 격차(완전정보 하한 대비 보통 86.6·다수 268.6초)의 상당 부분이
  '묻기 전에는 알 수 없는' 정보의 한계였다. 실제로는 환자 상태와 병원 역량이 맞을수록 잘 받아 준다. 수용을 예측할 수 있으면
  알고리즘의 몫이 얼마나 다시 생기는지 잰다.
- 환경: predictable_scenarios.predictable(기존 시나리오에 질환 유형 4가지·병원 적합도 강함 30%·보통 40%·약함 30%를 주고 수용을 조정,
  전체 수용률은 기존과 같음).
- 구성(같은 시나리오): A 현행(fixed_3·base) · G 최종 구성(유형을 모르는 eatks_seenbase_k10_m4 · G_RULE) ·
  TF 유형을 아는 EAT-K(eatkt, 적합도 표 정확) · TN 유형을 아는 EAT-K(적합도 표의 30%가 틀림).
- seed(처음 사용): 혼합 2550001–2550400, 다수 2560001–2560200.
- 판정: 혼합·다수 모두 ΔJ(TF − G) 95% 구간 상한 < 0이면 '예측 정보의 가치 확인'. 보조 판정: TN − G가 혼합·다수 모두 상한 < +5초이고
  하나 이상 상한 < 0이면 '틀린 정보에도 유효'. 완전정보 하한 대비 격차가 얼마나 줄었는지 함께 보고한다.
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

from . import (eat_capacity_policy, eat_seen_policy, eat_type_policy, experiment as base, experiment_levers as levers,
               experiment_wide as wide, harness, hold2_runner, predictable_scenarios as ps, scenarios)

MIX = tuple(range(2550001, 2550401))
MASS = tuple(range(2560001, 2560201))
FOCUS, G_RULE = levers.FOCUS, levers.G_RULE
ARMS = (('A', '현행(고정3)', 'fixed_3', 'base', None), ('G', '최종 구성(유형 모름)', FOCUS, G_RULE, None),
        ('TF', '유형을 아는 EAT-K(적합도 정확)', eat_type_policy.NAME, G_RULE, 0.0),
        ('TN', '유형을 아는 EAT-K(적합도 30% 틀림)', eat_type_policy.NAME, G_RULE, 0.3))
MARGIN = 5.0
SOURCE_FILES = levers.SOURCE_FILES + ('inquiry_lab/predictable_scenarios.py', 'inquiry_lab/eat_type_policy.py',
                                      'inquiry_lab/experiment_predict.py')
SUCCESS_RULE = '혼합·다수 모두 ΔJ(TF − G) 상한 < 0이면 예측 정보의 가치 확인.'
NOISY_RULE = f'TN − G가 혼합·다수 모두 상한 < +{MARGIN:.0f}초이고 하나 이상 상한 < 0이면 틀린 정보에도 유효.'


def run_case(task):
    kind, seed = task
    eat_capacity_policy.install([])
    eat_seen_policy.install([FOCUS])
    eat_type_policy.install()
    scenario = ps.predictable(wide.build(kind, seed))
    branches = {}
    for key, _, policy, rule, error in ARMS:
        eat_type_policy.set_knowledge(None if error is None else ps.knowledge(scenario, error))
        try:
            b = hold2_runner.run_scenario(scenario, [(policy, rule)])['branches'][0]
        finally:
            eat_type_policy.set_knowledge(None)
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
        branches[key] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], mix=scenario.get('mix'), patient_count=len(scenario['patients']),
                oracle=scenarios.oracle(scenario), branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def oracle_J(results):
    total = n = 0
    for r in results:
        for best in r['oracle']['arrival_seconds'].values():
            total += 300 + harness.FAILURE_PENALTY_SECONDS if best is None else best
            n += 1
    return total / n if n else None


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
        bound = oracle_J(rs)
        comparisons = {f'{a}_minus_{b}': base.paired(rs, a, b) for a, b in (('TF', 'G'), ('TN', 'G'), ('G', 'A'), ('TF', 'A'))}
        gap = rows['G']['J'] - bound
        report['groups'][kind] = dict(rows=rows, comparisons=comparisons, oracle_J=bound,
                                      gap_closed_by_TF=(rows['G']['J'] - rows['TF']['J']) / gap if gap else None,
                                      gap_closed_by_TN=(rows['G']['J'] - rows['TN']['J']) / gap if gap else None)
    return report


def verdict(report):
    g = report['groups']
    tf = {k: g[k]['comparisons']['TF_minus_G'] for k in ('mix', 'mass')}
    tn = {k: g[k]['comparisons']['TN_minus_G'] for k in ('mix', 'mass')}
    noisy = all(v['ci95'][1] < MARGIN for v in tn.values()) and any(v['ci95'][1] < 0 for v in tn.values())
    return dict(confirmed=all(v['ci95'][1] < 0 for v in tf.values()), noisy_useful=noisy,
                TF_minus_G={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in tf.items()},
                TN_minus_G={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in tn.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v = report['verdict']
    lines = ['# 수용 예측 가능 환경에서 질환 유형을 아는 EAT-K', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {SUCCESS_RULE} → 확인 **{v["confirmed"]}**', f'**보조 판정:** {NOISY_RULE} → **{v["noisy_useful"]}**', '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for kind, block in report['groups'].items():
        lines += [f'## {names[kind]}', '', f"완전정보 하한 J {_f(block['oracle_J'])}초 · 격차(G − 하한) 중 TF가 줄인 비율 "
                  f"{_f(100 * block['gap_closed_by_TF'] if block['gap_closed_by_TF'] is not None else None)}%, "
                  f"TN {_f(100 * block['gap_closed_by_TN'] if block['gap_closed_by_TN'] is not None else None)}%", '',
                  '| 구성 | J(초) | 실패 | 환자당 문의 | 검토 업무(초/환자) |', '|---|---:|---:|---:|---:|']
        for key, row in block['rows'].items():
            lines.append(f"| {key} {row['label']} | {_f(row['J'])} | {row['failed']} | {_f(row['inquiries_per_patient'], 2)} | "
                         f"{_f(row['review_work_seconds_per_patient'])} |")
        lines += ['', '| 비교 | ΔJ | 95% 구간 |', '|---|---:|---|']
        for name, c in block['comparisons'].items():
            lines.append(f"| {name.replace('_minus_', ' − ')} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 질환 유형(4가지)·적합도 비율·수용 조정 크기는 가정이다. 실제 수용이 얼마나 예측 가능한지는 실제 기록으로 확인해야 한다.',
        '- 적합도 배율(강함 1.75·보통 1.0·약함 0.2)은 환경의 실제 비율(약 1.65·1.0·0.21)에 가깝게 정했다. 실제로는 과거 기록으로 추정해야 한다.',
        '- 확정 대기 판단은 유형을 쓰지 않는다(2차 계산 그대로).']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(arms=[dict(key=k, label=l, policy=p, rule=r, knowledge_error=e) for k, l, p, r, e in ARMS],
                  environment=dict(types=list(ps.TYPES), fit_shares=[list(x) for x in ps.FIT_SHARES], raise_=ps.RAISE, keep=ps.KEEP),
                  factors=eat_type_policy.FACTORS, seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]]),
                  success_rule=SUCCESS_RULE, noisy_rule=NOISY_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_predict_20261028', design=design,
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
