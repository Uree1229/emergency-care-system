"""22차 실험: 최종 구성에서 확정 대기 규칙 재검증. 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_hold development --output simulation_results/inquiry_lab_hold_development_20261026
python -m inquiry_lab.experiment_hold validation --lock simulation_results/inquiry_lab_hold_development_20261026_lock.json \
    --output simulation_results/inquiry_lab_hold_validation_20261026

사전 고정:
- 질문: 2차 확정 대기 규칙(더 가까운 병원의 수용이 곧 올 것 같으면 최대 H초 확정을 미룸)이 21차 최종 구성에서도 도움이 되는가.
  2차에서는 EAT −2.4초(실무 의미 작음), 고정3 −12.5초였다. 지금은 EAT가 먼 병원까지 함께 묻는다.
- 환경: 21차 최종 구성(FINAL = 보조 호출 + 선제 호출 + 취소 중단 + 분산 정렬 + 답 없이 닫힘 종료 + 즉시 재판단).
  새 seed만 사용: 개발 혼합 1820001–1820240·다수 1840001–1840120·8유형 1860001–1860003,
  검증 혼합 1830001–1830400·다수 1850001–1850200·8유형 1870001–1870006.
- 정책: eatks_seenbase_k10_m4(선택·판정 기준), fixed_3(참고).
- 후보: FINAL + cf30·cf60·cf120.
- 선택(개발, EAT-K): 혼합·다수 모두 ΔJ(후보 − FINAL) ≤ +2초인 후보 중 두 ΔJ 평균 최소. 평균이 0 미만이 아니면 검증하지 않는다.
- 판정(검증, EAT-K): 혼합 ΔJ 95% 구간 상한 < 0 이고 다수 95% 구간 상한 < +5초이면 채택 후보. 고정3은 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_combo as prev, experiment_wide as wide,
               hold_runner, scenarios)

DEV_MIX = tuple(range(1820001, 1820241))
DEV_MASS = tuple(range(1840001, 1840121))
DEV_FAMILY = (1860001, 1860002, 1860003)
VAL_MIX = tuple(range(1830001, 1830401))
VAL_MASS = tuple(range(1850001, 1850201))
VAL_FAMILY = (1870001, 1870002, 1870003, 1870004, 1870005, 1870006)
FOCUS = prev.FOCUS
POLICIES = (FOCUS, 'fixed_3')
FINAL = prev.COMBO
CANDIDATES = tuple(f'{FINAL}+cf{h}' for h in (30, 60, 120))
RULES = (FINAL,) + CANDIDATES
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/confirm_policy.py', 'inquiry_lab/hold_runner.py', 'inquiry_lab/experiment_hold.py')
SELECTION_RULE = (f'개발(EAT-K): 혼합·다수 모두 ΔJ(후보 − FINAL) ≤ +{TOLERANCE:.0f}초인 후보 중 두 ΔJ 평균 최소. '
                  '평균이 0 미만이 아니면 검증하지 않는다.')
SUCCESS_RULE = f'검증(EAT-K): 혼합 ΔJ(선택 − FINAL) 95% 구간 상한 < 0 이고 다수 95% 구간 상한 < +{MARGIN:.0f}초이면 채택 후보.'


def arm(policy, rule):
    return f'{policy}|{rule}'


def short(rule):
    return 'FINAL' if rule == FINAL else 'FINAL+' + rule.rsplit('+', 1)[1]


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    scenario = wide.build(kind, seed)
    result = hold_runner.run_scenario(scenario, arm_list)
    branches = {}
    for b in result['branches']:
        row = wide.prev.prev.compact(b)
        row.update(holds=b['holds'])
        branches[arm(b['policy'], b['rule'])] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario), branches=branches)


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


def evaluate(results, rules):
    groups = dict(mix=[r for r in results if r['kind'] == 'mix'], mass=[r for r in results if r['kind'] == 'mass'])
    fam = [r for r in results if r['kind'] not in groups]
    report = dict(groups={})
    for g, rs in groups.items():
        patients = sum(r['patient_count'] for r in rs)
        block = {}
        for p in POLICIES:
            rows = {}
            for rule in rules:
                name = arm(p, rule)
                h = {k: sum(r['branches'][name]['holds'][k] for r in rs) for k in ('count', 'held_seconds', 'switched')}
                row = dict(metrics=base.policy_metrics(rs, name), holds_per_100=100 * h['count'] / patients,
                           mean_hold_seconds=h['held_seconds'] / h['count'] if h['count'] else None,
                           switched_share=h['switched'] / h['count'] if h['count'] else None)
                if rule != FINAL:
                    row['versus_final'] = base.paired(rs, name, arm(p, FINAL))
                rows[rule] = row
            block[p] = rows
        report['groups'][g] = block
    report['by_arrival'] = {}
    for g, rs in groups.items():
        for a in sorted({r['mix']['arrival'] for r in rs}):
            subset = [r for r in rs if r['mix']['arrival'] == a]
            report['by_arrival'][f'{g}:{a}'] = {rule: base.paired(subset, arm(FOCUS, rule), arm(FOCUS, FINAL))
                                                for rule in rules if rule != FINAL}
    report['families'] = base.family_table(fam, [arm(p, r) for p in POLICIES for r in rules])
    return report


def select(report):
    g = report['groups']
    delta = lambda k, c: g[k][FOCUS][c]['versus_final']['delta_J_mean']
    feasible = [c for c in CANDIDATES if all(delta(k, c) <= TOLERANCE for k in ('mix', 'mass'))]
    if not feasible:
        return dict(rule=None, proceed=False, feasible=[])
    mean = lambda c: (delta('mix', c) + delta('mass', c)) / 2
    best = min(feasible, key=lambda c: (round(mean(c), 9), CANDIDATES.index(c)))
    return dict(rule=best, proceed=mean(best) < 0, feasible=feasible, mean_delta_J=mean(best),
                delta_J={k: delta(k, best) for k in ('mix', 'mass')})


def verdict(report, chosen):
    g = report['groups']
    mix, mass = g['mix'][FOCUS][chosen]['versus_final'], g['mass'][FOCUS][chosen]['versus_final']
    return dict(rule=chosen, mix_better=mix['ci95'][1] < 0, mass_noninferior=mass['ci95'][1] < MARGIN,
                adopted=mix['ci95'][1] < 0 and mass['ci95'][1] < MARGIN, mix_delta_J=mix['delta_J_mean'], mix_ci95=mix['ci95'],
                mass_delta_J=mass['delta_J_mean'], mass_ci95=mass['ci95'])


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    rules = report['rules']
    lines = [f"# 최종 구성의 확정 대기 규칙 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['rule'] and short(s['rule'])} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '',
                  f"- {short(v['rule'])}: 혼합 ΔJ {_f(v['mix_delta_J'])} [{_f(v['mix_ci95'][0])}, {_f(v['mix_ci95'][1])}], "
                  f"다수 ΔJ {_f(v['mass_delta_J'])} [{_f(v['mass_ci95'][0])}, {_f(v['mass_ci95'][1])}] → 채택 후보 **{v['adopted']}**", '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, by_policy in report['groups'].items():
        lines += [f'## {names[g]}', '', '| 정책 | 운영 | J(초) | 실패 | ΔJ vs FINAL [95% 구간] | 대기(환자 100명당) | 평균 대기(초) | 병원 바뀜 |',
                  '|---|---|---:|---:|---|---:|---:|---:|']
        for p, rows in by_policy.items():
            for rule in rules:
                row = rows[rule]
                v = row.get('versus_final')
                delta = f"{_f(v['delta_J_mean'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}]" if v else '—'
                switched = '—' if row['switched_share'] is None else f"{100 * row['switched_share']:.0f}%"
                lines.append(f"| {p} | {short(rule)} | {_f(row['metrics']['J'])} | {row['metrics']['failed']} | {delta} | "
                             f"{_f(row['holds_per_100'])} | {_f(row['mean_hold_seconds'])} | {switched} |")
        lines.append('')
    lines += [f'## 도착 형태별 ΔJ(후보 − FINAL, {FOCUS})', '', '| 묶음 | 운영 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for key, by_rule in report['by_arrival'].items():
        for rule, v in by_rule.items():
            lines.append(f"| {key} | {short(rule)} | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 확정 대기 규칙의 확률 계산은 2차 그대로(처리 인원 1명 가정)이며, 처리 용량을 아는 형태로 바꾸지 않았다.']
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
    lock = dict(schema_version=1, experiment='inquiry_lab_hold_20261026', selection=report['selection'],
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
        raise RuntimeError('development selected no hold rule; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['rule']
    rules = (FINAL, chosen)
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
                    v = row.get('versus_final')
                    print(g, p, short(rule), round(row['metrics']['J'], 1), None if v is None else round(v['delta_J_mean'], 1),
                          round(row['holds_per_100'], 1))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
