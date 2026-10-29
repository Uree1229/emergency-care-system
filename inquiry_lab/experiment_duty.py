"""23차 실험: 같은 J에서 보조 담당 근무 줄이기(비열등성). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_duty development --output simulation_results/inquiry_lab_duty_development_20261026
python -m inquiry_lab.experiment_duty validation --lock simulation_results/inquiry_lab_duty_development_20261026_lock.json \
    --output simulation_results/inquiry_lab_duty_validation_20261026

사전 고정:
- 질문: 21차 최종 구성에서 보조 담당 근무시간 중 실제 검토는 35%(보통)~47%(다수 사상자)뿐이다. 보조 호출 기준(미회신 k건)이나
  복귀 전 대기(L초)를 바꿔 근무시간을 줄여도 J가 유지되는가. 선제 호출도 같은 합류 지연·복귀 규칙을 쓴다.
- 환경: 22차 채택 구성(FINAL = od_k2_d60_l120 + 선제 호출 + 취소 중단 + 분산 정렬 + 답 없이 닫힘 종료 + 즉시 재판단
  + 확정 대기 120초)에서 보조 호출 부분만 바꾼다. 새 seed만 사용: 개발 혼합 1880001–1880240·다수 1900001–1900120·8유형 1920001–1920003,
  검증 혼합 1890001–1890400·다수 1910001–1910200·8유형 1930001–1930006.
- 정책: eatks_seenbase_k10_m4(선택·판정 기준), fixed_3(참고).
- 후보: od_k2_d60_l0, od_k2_d60_l60, od_k3_d60_l0, od_k3_d60_l120(나머지 부분은 FINAL과 같다).
- 선택(개발, EAT-K): 혼합·다수 모두 ΔJ(후보 − FINAL) ≤ +2초인 후보 중 환자당 보조 근무 감소(혼합·다수 합산)가 가장 큰 것.
  근무가 줄지 않으면 검증하지 않는다.
- 판정(검증, EAT-K): 혼합·다수 모두 ΔJ 95% 구간 상한 < +5초(비열등)이고, 환자당 보조 근무 차이(혼합+다수 합산)의
  시나리오 단위 부트스트랩 95% 구간 상한 < 0이면 채택 후보.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import random
import time

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_hold as prev, experiment_wide as wide,
               hold_runner, scenarios)

DEV_MIX = tuple(range(1880001, 1880241))
DEV_MASS = tuple(range(1900001, 1900121))
DEV_FAMILY = (1920001, 1920002, 1920003)
VAL_MIX = tuple(range(1890001, 1890401))
VAL_MASS = tuple(range(1910001, 1910201))
VAL_FAMILY = (1930001, 1930002, 1930003, 1930004, 1930005, 1930006)
FOCUS = prev.FOCUS
POLICIES = (FOCUS, 'fixed_3')
FINAL = prev.CANDIDATES[2]
assert FINAL.endswith('+cf120')
BASE_OD = 'od_k2_d60_l120'
assert FINAL.startswith(BASE_OD + '+')
TAIL = FINAL[len(BASE_OD):]
CANDIDATE_OD = ('od_k2_d60_l0', 'od_k2_d60_l60', 'od_k3_d60_l0', 'od_k3_d60_l120')
CANDIDATES = tuple(od + TAIL for od in CANDIDATE_OD)
RULES = (FINAL,) + CANDIDATES
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/experiment_duty.py',)
SELECTION_RULE = (f'개발(EAT-K): 혼합·다수 모두 ΔJ(후보 − FINAL) ≤ +{TOLERANCE:.0f}초인 후보 중 환자당 보조 근무 감소가 가장 큰 것. '
                  '근무가 줄지 않으면 검증하지 않는다.')
SUCCESS_RULE = (f'검증(EAT-K): 혼합·다수 모두 ΔJ 95% 구간 상한 < +{MARGIN:.0f}초이고, 환자당 보조 근무 차이(혼합+다수)의 '
                '95% 구간 상한 < 0이면 채택 후보.')


def arm(policy, rule):
    return f'{policy}|{rule}'


def short(rule):
    return rule[:-len(TAIL)] if rule.endswith(TAIL) else rule


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


def _tasks(mix_seeds, mass_seeds, family_seeds, rules):
    arm_list = [(p, r) for p in POLICIES for r in rules]
    tasks = [('mass', s, arm_list) for s in mass_seeds]
    tasks += [('mix', s, arm_list) for s in mix_seeds]
    tasks += [(f'family:{f}', s, arm_list) for f in scenarios.original.FAMILY_NAMES for s in family_seeds]
    return tasks


def duty(results, name):
    patients = sum(r['patient_count'] for r in results)
    b = [r['branches'][name]['backup'] for r in results]
    calls = sum(x['calls'] for x in b)
    on_duty = sum(x['on_duty_seconds'] for x in b)
    return dict(on_duty_seconds_per_patient=on_duty / patients, busy_seconds_per_patient=sum(x['busy_seconds'] for x in b) / patients,
                calls_per_100_patients=100 * calls / patients,
                false_alarm_share=sum(x['false_alarms'] for x in b) / calls if calls else None,
                utilization=sum(x['busy_seconds'] for x in b) / on_duty if on_duty else None)


def duty_difference(results, a, b):
    """환자당 보조 근무 차이(a − b)와 시나리오 단위 부트스트랩 95% 구간."""
    rows = [(r['branches'][a]['backup']['on_duty_seconds'] - r['branches'][b]['backup']['on_duty_seconds'], r['patient_count'])
            for r in results]
    total = sum(n for _, n in rows)
    rng = random.Random(base.BOOTSTRAP_SEED)
    stats = sorted(sum(d for d, _ in s) / sum(n for _, n in s)
                   for s in ([rows[rng.randrange(len(rows))] for _ in rows] for _ in range(base.BOOTSTRAP_RESAMPLES)))
    return dict(difference_per_patient=sum(d for d, _ in rows) / total,
                ci95=[stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats)) - 1]])


def evaluate(results, rules):
    groups = dict(mix=[r for r in results if r['kind'] == 'mix'], mass=[r for r in results if r['kind'] == 'mass'])
    fam = [r for r in results if r['kind'] not in groups]
    report = dict(groups={})
    for g, rs in groups.items():
        block = {}
        for p in POLICIES:
            rows = {}
            for rule in rules:
                name = arm(p, rule)
                row = dict(metrics=base.policy_metrics(rs, name), duty=duty(rs, name))
                if rule != FINAL:
                    row['versus_final'] = base.paired(rs, name, arm(p, FINAL))
                    row['duty_difference'] = duty_difference(rs, name, arm(p, FINAL))
                rows[rule] = row
            block[p] = rows
        report['groups'][g] = block
    both = groups['mix'] + groups['mass']
    report['pooled_duty_difference'] = {rule: duty_difference(both, arm(FOCUS, rule), arm(FOCUS, FINAL)) for rule in rules if rule != FINAL}
    report['families'] = base.family_table(fam, [arm(p, r) for p in POLICIES for r in rules])
    return report


def select(report):
    g = report['groups']
    feasible = [c for c in CANDIDATES if all(g[k][FOCUS][c]['versus_final']['delta_J_mean'] <= TOLERANCE for k in ('mix', 'mass'))]
    if not feasible:
        return dict(rule=None, proceed=False, feasible=[])
    reduction = lambda c: report['pooled_duty_difference'][c]['difference_per_patient']
    best = min(feasible, key=lambda c: (round(reduction(c), 9), CANDIDATES.index(c)))
    return dict(rule=best, proceed=reduction(best) < 0, feasible=feasible, duty_difference_per_patient=reduction(best),
                delta_J={k: g[k][FOCUS][best]['versus_final']['delta_J_mean'] for k in ('mix', 'mass')})


def verdict(report, chosen):
    g = report['groups']
    rows = {k: g[k][FOCUS][chosen]['versus_final'] for k in ('mix', 'mass')}
    diff = report['pooled_duty_difference'][chosen]
    noninferior = all(v['ci95'][1] < MARGIN for v in rows.values())
    return dict(rule=chosen, noninferior=noninferior, less_duty=diff['ci95'][1] < 0, adopted=noninferior and diff['ci95'][1] < 0,
                delta_J={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in rows.items()}, duty_difference=diff)


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    rules = report['rules']
    lines = [f"# 보조 근무 줄이기 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['rule'] and short(s['rule'])} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '', f"- {short(v['rule'])} → 채택 후보 **{v['adopted']}**"]
        for k, row in v['delta_J'].items():
            lines.append(f"- {k}: ΔJ {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
        d = v['duty_difference']
        lines += [f"- 보조 근무 차이(초/환자): {_f(d['difference_per_patient'])} [{_f(d['ci95'][0])}, {_f(d['ci95'][1])}]", '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, by_policy in report['groups'].items():
        lines += [f'## {names[g]}', '', '| 정책 | 보조 호출 | J(초) | 실패 | ΔJ vs FINAL [95% 구간] | 보조 근무(초/환자) | 근무 차이 [95% 구간] | 호출/환자100 | 헛호출 | 근무 중 검토 비율 |',
                  '|---|---|---:|---:|---|---:|---|---:|---:|---:|']
        for p, rows in by_policy.items():
            for rule in rules:
                row = rows[rule]
                v, dd, du = row.get('versus_final'), row.get('duty_difference'), row['duty']
                delta = f"{_f(v['delta_J_mean'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}]" if v else '—'
                ddiff = f"{_f(dd['difference_per_patient'])} [{_f(dd['ci95'][0])}, {_f(dd['ci95'][1])}]" if dd else '—'
                false = '—' if du['false_alarm_share'] is None else f"{100 * du['false_alarm_share']:.0f}%"
                util = '—' if du['utilization'] is None else f"{100 * du['utilization']:.0f}%"
                lines.append(f"| {p} | {short(rule)} | {_f(row['metrics']['J'])} | {row['metrics']['failed']} | {delta} | "
                             f"{_f(du['on_duty_seconds_per_patient'])} | {ddiff} | {_f(du['calls_per_100_patients'])} | {false} | {util} |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 확정 대기 규칙의 확률 계산은 2차 그대로(처리 인원 1명 가정)이며, 처리 용량을 아는 형태로 바꾸지 않았다.',
        '- 보조 근무시간은 호출부터 복귀까지(합류 대기·복귀 전 대기 포함)다. 잦은 호출로 생기는 업무 중단 비용은 따로 세지 않았다.']
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
    lock = dict(schema_version=1, experiment='inquiry_lab_duty_20261026', selection=report['selection'],
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
        raise RuntimeError('development selected no duty-saving rule; validation is not run by protocol')
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
                          round(row['duty']['on_duty_seconds_per_patient'], 1))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
