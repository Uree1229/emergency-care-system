"""41차 실험: 서버가 과거 문의 기록으로 유형별 수용률을 스스로 배우는 EAT-K. 선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_learn run --output simulation_results/inquiry_lab_learn_validation_20261028

사전 고정:
- 근거: 40차에서 병원 적합도 등록 정보를 정확히 아는 EAT-K(TF)는 유형을 모르는 최종 구성(G)보다 보통 −8.8·다수 −33.4초였다.
  등록 정보 없이 서버가 같은 병원들의 지난 문의 기록(유형·수용 여부)에서 배우면 얼마나 따라가는지 잰다.
- 환경: 40차와 같은 수용 예측 가능 환경(predictable_scenarios.predictable).
- 구성(같은 시나리오): G 유형 모름(eatks_seenbase_k10_m4) · L20 병원당 지난 기록 20건으로 학습(eatkl) · L80 80건으로 학습 ·
  TF 적합도 등록 정보 정확(40차 eatkt, 참고). 운영 구성은 모두 G_RULE.
- 과거 기록: learned_fit.past_records(병원마다 회신 온 지난 문의 n건, 유형 고르게, 수용 여부는 환경과 같은 참 확률). 학습은 learned_table.
- seed(처음 사용): 혼합 2570001–2570400, 다수 2580001–2580200.
- 판정: 혼합·다수 모두 ΔJ(L80 − G) 95% 구간 상한 < 0이면 '스스로 학습 가치 확인'. 보조 판정: L20 − G가 둘 다 상한 < +5초이고
  하나 이상 상한 < 0이면 '적은 기록으로도 유효'. L80 − TF(등록 정보와의 차이)는 판정 없이 보고한다.
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

from . import (eat_capacity_policy, eat_learn_policy, eat_seen_policy, eat_type_policy, experiment as base,
               experiment_predict as prev, experiment_wide as wide, hold2_runner, learned_fit as lf, predictable_scenarios as ps,
               scenarios)

MIX = tuple(range(2570001, 2570401))
MASS = tuple(range(2580001, 2580201))
FOCUS, G_RULE = prev.FOCUS, prev.G_RULE
ARMS = (('G', '유형 모름(최종 구성)', FOCUS, None), ('L20', '지난 기록 병원당 20건으로 학습', eat_learn_policy.NAME, 20),
        ('L80', '지난 기록 병원당 80건으로 학습', eat_learn_policy.NAME, 80),
        ('TF', '적합도 등록 정보 정확(40차, 참고)', eat_type_policy.NAME, 'registry'))
MARGIN = 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/learned_fit.py', 'inquiry_lab/eat_learn_policy.py', 'inquiry_lab/experiment_learn.py')
SUCCESS_RULE = '혼합·다수 모두 ΔJ(L80 − G) 상한 < 0이면 스스로 학습 가치 확인.'
SMALL_RULE = f'L20 − G가 혼합·다수 모두 상한 < +{MARGIN:.0f}초이고 하나 이상 상한 < 0이면 적은 기록으로도 유효.'


def run_case(task):
    kind, seed = task
    eat_capacity_policy.install([])
    eat_seen_policy.install([FOCUS])
    eat_type_policy.install()
    eat_learn_policy.install()
    scenario = ps.predictable(wide.build(kind, seed))
    types = {p['id']: p['ptype'] for p in scenario['patients']}
    hospital_ids = [h['id'] for h in scenario['hospitals']]
    branches, table_error = {}, {}
    for key, _, policy, source in ARMS:
        if source == 'registry':
            eat_type_policy.set_knowledge(ps.knowledge(scenario, 0.0))
        elif source is not None:
            table = lf.learned_table(lf.past_records(scenario, source), hospital_ids)
            eat_learn_policy.set_knowledge(dict(types=types, prob=table))
            params = lf.hidden(scenario)
            truth = {(t, h): lf.true_prob(params[h]['accept'], ps.fit(scenario['id'], h, t)) for t in ps.TYPES for h in hospital_ids}
            table_error[key] = sum(abs(table[x] - truth[x]) for x in truth) / len(truth)
        try:
            b = hold2_runner.run_scenario(scenario, [(policy, G_RULE)])['branches'][0]
        finally:
            eat_type_policy.set_knowledge(None)
            eat_learn_policy.set_knowledge(None)
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
        branches[key] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], mix=scenario.get('mix'), patient_count=len(scenario['patients']),
                oracle=scenarios.oracle(scenario), branches=branches, table_mean_abs_error=table_error)


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
        for key, label, _, _ in ARMS:
            m = base.policy_metrics(rs, key)
            rows[key] = dict(label=label, J=m['J'], failed=m['failed'], patients=m['patients'],
                             inquiries_per_patient=m['inquiries_per_patient'],
                             review_work_seconds_per_patient=m['review_work_seconds_per_patient'])
        comparisons = {f'{a}_minus_{b}': base.paired(rs, a, b) for a, b in (('L20', 'G'), ('L80', 'G'), ('TF', 'G'), ('L80', 'TF'))}
        error = {key: sum(r['table_mean_abs_error'][key] for r in rs) / len(rs) for key in ('L20', 'L80')}
        report['groups'][kind] = dict(rows=rows, comparisons=comparisons, oracle_J=prev.oracle_J(rs), table_mean_abs_error=error)
    return report


def verdict(report):
    g = report['groups']
    l80 = {k: g[k]['comparisons']['L80_minus_G'] for k in ('mix', 'mass')}
    l20 = {k: g[k]['comparisons']['L20_minus_G'] for k in ('mix', 'mass')}
    small = all(v['ci95'][1] < MARGIN for v in l20.values()) and any(v['ci95'][1] < 0 for v in l20.values())
    return dict(confirmed=all(v['ci95'][1] < 0 for v in l80.values()), small_useful=small,
                L80_minus_G={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in l80.items()},
                L20_minus_G={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in l20.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v = report['verdict']
    lines = ['# 과거 기록으로 유형별 수용률을 스스로 배우는 EAT-K', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {SUCCESS_RULE} → 확인 **{v["confirmed"]}**', f'**보조 판정:** {SMALL_RULE} → **{v["small_useful"]}**', '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for kind, block in report['groups'].items():
        err = block['table_mean_abs_error']
        lines += [f'## {names[kind]}', '', f"완전정보 하한 J {_f(block['oracle_J'])}초 · 추정 수용률의 평균 절대 오차: 20건 "
                  f"{_f(err['L20'], 3)}, 80건 {_f(err['L80'], 3)}", '',
                  '| 구성 | J(초) | 실패 | 환자당 문의 | 검토 업무(초/환자) |', '|---|---:|---:|---:|---:|']
        for key, row in block['rows'].items():
            lines.append(f"| {key} {row['label']} | {_f(row['J'])} | {row['failed']} | {_f(row['inquiries_per_patient'], 2)} | "
                         f"{_f(row['review_work_seconds_per_patient'])} |")
        lines += ['', '| 비교 | ΔJ | 95% 구간 |', '|---|---:|---|']
        for name, c in block['comparisons'].items():
            lines.append(f"| {name.replace('_minus_', ' − ')} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 과거 기록은 같은 병원·같은 적합도 구조에서 나온다고 본다(병원 역량이 기간 중 바뀌지 않음). 실제 기록에는 계절·시간대 변화가 섞인다.',
        '- 과거 기록의 수용 여부는 환경과 같은 참 확률로 만든다. 실제 기록에서는 환자 유형 분류의 정확도도 문제다.',
        '- 질환 유형·적합도 구조는 40차와 같은 가정이다.']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(arms=[dict(key=k, label=l, policy=p, source=s) for k, l, p, s in ARMS], rule=G_RULE,
                  learning=dict(prior=lf.PRIOR, k_hospital=lf.K_HOSPITAL, k_type=lf.K_TYPE),
                  seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]]), success_rule=SUCCESS_RULE, small_rule=SMALL_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_learn_20261028', design=design,
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
