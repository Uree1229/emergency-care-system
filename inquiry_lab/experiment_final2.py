"""26차 실험: 22~25차를 반영한 최종 구성 확인 실험. 선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_final2 run --output simulation_results/inquiry_lab_final2_validation_20261026

사전 고정:
- 근거: 21차 최종 구성(F) 뒤에 22차 확정 대기(cf120)를 채택했다. 23·24·25차는 미채택이라 G의 정책은 15차 EAT-K
  (eatks_seenbase_k10_m4)로 실행 전에 고정한다. 각 단계는 따로 검증했으므로, 합친 구성을 현행과 21차 구성에 견주어 새 seed로
  한 번 더 확인한다. 23차 개발에서 J 변화 없이 보조 근무를 줄인 '복귀 전 60초'(L60)는 사전 고정한 보조 판정으로 함께 본다.
- 구성(모두 hold2_runner):
  A 현행: fixed_3 · base
  F 21차 최종: eatks_seenbase_k10_m4 · COMBO(보조 호출 + 선제 호출 + 취소 중단 + 분산 정렬 + 답 없이 닫힘 종료 + 즉시 재판단)
  G 새 최종: G_POLICY · G_RULE(F + 확정 대기 cf120)
  L 부담 감소 선택지: G_POLICY · G_RULE의 보조 호출을 od_k2_d60_l60(일이 없으면 60초 뒤 복귀)으로 바꾼 것
  R 참고: fixed_3 · G_RULE
- seed(처음 사용): 혼합 2000001–2000400, 다수 2010001–2010200, 8유형 2020001–2020006.
- 주 판정: 혼합 ΔJ(G − F) 95% 구간 상한 < 0 이고 다수 사상자 ΔJ(G − F) 95% 구간 상한 < +5초이면 확인.
- 보조 판정(L): 혼합·다수 모두 ΔJ(L − G) 95% 구간 상한 < +5초이고, 환자당 보조 근무 차이(혼합+다수)의 95% 구간 상한 < 0이면
  '보조 근무 선택지' 확인. G − A(현행 대비 전체 효과)는 판정 없이 보고한다.
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

from . import (eat_capacity_policy, eat_first_policy, eat_seen_policy, experiment as base, experiment_combo as combo,
               experiment_deadline as deadline, experiment_duty as duty, experiment_hold2 as hold2, experiment_wide as wide,
               hold2_runner, scenarios)

MIX = tuple(range(2000001, 2000401))
MASS = tuple(range(2010001, 2010201))
FAMILY = (2020001, 2020002, 2020003, 2020004, 2020005, 2020006)
FOCUS = combo.FOCUS
F_RULE = combo.COMBO
G_POLICY = FOCUS                         # 25차 미채택(비열등이지만 개선 없음)이라 15차 EAT-K를 유지한다
G_RULE = hold2.BASE
assert G_RULE.startswith('od_k2_d60_l120+')
LIGHT_RULE = 'od_k2_d60_l60+' + G_RULE[len('od_k2_d60_l120+'):]
ARMS = (('A', '현행(고정3)', 'fixed_3', 'base'),
        ('F', '21차 최종 구성', FOCUS, F_RULE),
        ('G', '새 최종 구성', G_POLICY, G_RULE),
        ('L', '부담 감소 선택지: G + 보조 복귀 전 60초', G_POLICY, LIGHT_RULE),
        ('R', '참고: 고정3 + G의 운영 수단', 'fixed_3', G_RULE))
MARGIN = 5.0
SOURCE_FILES = deadline.SOURCE_FILES + ('inquiry_lab/experiment_final2.py',)
SUCCESS_RULE = f'혼합 ΔJ(G − F) 95% 구간 상한 < 0 이고 다수 사상자 ΔJ(G − F) 95% 구간 상한 < +{MARGIN:.0f}초이면 확인.'
LIGHT_RULE_TEXT = (f'혼합·다수 모두 ΔJ(L − G) 95% 구간 상한 < +{MARGIN:.0f}초이고 환자당 보조 근무 차이(혼합+다수) 95% 구간 상한 < 0이면 '
                   '보조 근무 선택지 확인.')


def arm(policy, rule):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    eat_first_policy.install([p for p in names if p.startswith(eat_first_policy.PREFIXES)])
    scenario = wide.build(kind, seed)
    result = hold2_runner.run_scenario(scenario, arm_list)
    branches = {}
    for b in result['branches']:
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
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
    pairs = [('G', 'F'), ('G', 'A'), ('F', 'A'), ('R', 'A'), ('L', 'G')]
    report = dict(groups={})
    for g, rs in groups.items():
        rows = {}
        for key, label, p, r in ARMS:
            m = base.policy_metrics(rs, names[key])
            rows[key] = dict(label=label, arm=names[key], J=m['J'], failed=m['failed'], patients=m['patients'],
                             avoidable_failed=m['avoidable_failed'], inquiries_per_patient=m['inquiries_per_patient'],
                             backup=duty.duty(rs, names[key]) if r != 'base' else None)
        comparisons = {f'{a}_minus_{b}': base.paired(rs, names[a], names[b]) for a, b in pairs}
        by_arrival = {a: base.paired([x for x in rs if x['mix']['arrival'] == a], names['G'], names['F'])
                      for a in sorted({x['mix']['arrival'] for x in rs})}
        report['groups'][g] = dict(rows=rows, comparisons=comparisons, G_minus_F_by_arrival=by_arrival,
                                   L_minus_G_duty=duty.duty_difference(rs, names['L'], names['G']))
    both = groups['mix'] + groups['mass']
    report['L_minus_G_duty_pooled'] = duty.duty_difference(both, names['L'], names['G'])
    report['families'] = base.family_table(fam, list(names.values()))
    return report


def verdict(report):
    mix = report['groups']['mix']['comparisons']['G_minus_F']
    mass = report['groups']['mass']['comparisons']['G_minus_F']
    return dict(mix_better=mix['ci95'][1] < 0, mass_noninferior=mass['ci95'][1] < MARGIN,
                confirmed=mix['ci95'][1] < 0 and mass['ci95'][1] < MARGIN,
                mix=dict(delta_J=mix['delta_J_mean'], ci95=mix['ci95']), mass=dict(delta_J=mass['delta_J_mean'], ci95=mass['ci95']))


def light_verdict(report):
    rows = {k: report['groups'][k]['comparisons']['L_minus_G'] for k in ('mix', 'mass')}
    pooled = report['L_minus_G_duty_pooled']
    noninferior = all(v['ci95'][1] < MARGIN for v in rows.values())
    return dict(noninferior=noninferior, less_duty=pooled['ci95'][1] < 0, confirmed=noninferior and pooled['ci95'][1] < 0,
                delta_J={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in rows.items()}, duty_difference=pooled)


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v, lv = report['verdict'], report['light_verdict']
    lines = ['# 최종 구성 확인 실험(22~25차 반영)', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'G = `{G_POLICY}` · `{G_RULE}`', '', f'**주 판정:** {SUCCESS_RULE}', '',
             f"- 혼합 G − F: {_f(v['mix']['delta_J'])} [{_f(v['mix']['ci95'][0])}, {_f(v['mix']['ci95'][1])}], "
             f"다수 G − F: {_f(v['mass']['delta_J'])} [{_f(v['mass']['ci95'][0])}, {_f(v['mass']['ci95'][1])}] → 확인 **{v['confirmed']}**", '',
             f'**보조 판정(L):** {LIGHT_RULE_TEXT}', '']
    for k, row in lv['delta_J'].items():
        lines.append(f"- {k} L − G: ΔJ {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
    d = lv['duty_difference']
    lines += [f"- 보조 근무 차이(초/환자, 혼합+다수): {_f(d['difference_per_patient'])} [{_f(d['ci95'][0])}, {_f(d['ci95'][1])}] "
              f"→ 확인 **{lv['confirmed']}**", '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, block in report['groups'].items():
        lines += [f'## {names[g]}', '', '| 구성 | 내용 | J(초) | 실패 | 피할 수 있던 실패 | 환자당 문의 | 보조 근무(초/환자) | 호출/환자100 | 헛호출 |',
                  '|---|---|---:|---:|---:|---:|---:|---:|---:|']
        for key, row in block['rows'].items():
            b = row['backup']
            false = '—' if not b or b['false_alarm_share'] is None else f"{100 * b['false_alarm_share']:.0f}%"
            lines.append(f"| {key} | {row['label']} | {_f(row['J'])} | {row['failed']} | {row['avoidable_failed']} | "
                         f"{_f(row['inquiries_per_patient'], 2)} | {_f(b and b['on_duty_seconds_per_patient'])} | "
                         f"{_f(b and b['calls_per_100_patients'])} | {false} |")
        lines += ['', '| 비교 | ΔJ | 95% 구간 |', '|---|---:|---|']
        for key, c in block['comparisons'].items():
            lines.append(f"| {key.replace('_minus_', ' − ')} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
        dd = block['L_minus_G_duty']
        lines += ['', f"- L − G 보조 근무(초/환자): {_f(dd['difference_per_patient'])} [{_f(dd['ci95'][0])}, {_f(dd['ci95'][1])}]"]
        lines += ['', '| 도착 형태 | G − F | 95% 구간 |', '|---|---:|---|']
        for a, c in block['G_minus_F_by_arrival'].items():
            lines.append(f"| {a} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 확정 대기 규칙의 확률 계산은 2차 모형을 쓴다. 보조 근무시간은 호출부터 복귀까지(합류 대기 포함)다.']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(arms=[dict(key=k, label=l, policy=p, rule=r) for k, l, p, r in ARMS],
                  seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]], families=list(FAMILY)),
                  success_rule=SUCCESS_RULE, light_rule=LIGHT_RULE_TEXT)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_final2_20261026', design=design,
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
    report['light_verdict'] = light_verdict(report)
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
    print('light', report['light_verdict'])
    for g, block in report['groups'].items():
        for key, c in block['comparisons'].items():
            print(g, key, round(c['delta_J_mean'], 1), [round(x, 1) for x in c['ci95']])


if __name__ == '__main__':
    main()
