"""26차 실험(25차 다음): 보조 담당 호출을 처리 인원 2명 병원까지 넓히기. 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_reactive development --output simulation_results/inquiry_lab_reactive_development_20261027
python -m inquiry_lab.experiment_reactive validation --lock simulation_results/inquiry_lab_reactive_development_20261027_lock.json \
    --output simulation_results/inquiry_lab_reactive_validation_20261027

사전 고정:
- 질문: 보조 담당 호출은 처리 인원 1명 병원에서만 한다. 2명 병원(보통 상황 병원의 약 21%, 다수 사상자의 약 29%)은 선제 호출
  때만 보조를 받는다. 모든 병원을 호출 대상으로 넓히면(기준: 미회신 k + 처리 인원 − 1건) J가 줄어드는가. 복귀 전 대기를
  줄이면 늘어난 호출의 근무를 상쇄할 수 있는가. 24차 개발 seed 진단에서 유망했다(판정에 쓰지 않음).
- 환경: 26차 확인 실험의 최종 구성(BASE = experiment_final2.G_RULE, 정책 eatks_seenbase_k10_m4)에서 보조 호출 부분만 바꾼다.
  새 seed만 사용: 개발 혼합 2090001–2090240·다수 2110001–2110120·8유형 2130001–2130003,
  검증 혼합 2100001–2100400·다수 2120001–2120200·8유형 2140001–2140006.
- 정책: eatks_seenbase_k10_m4(선택·판정 기준), fixed_3(참고).
- 후보: BASE + ra(모든 병원 호출)에 복귀 전 대기 120·90·60초.
- 선택(개발, EAT-K): 혼합·다수 모두 ΔJ(후보 − BASE) ≤ +2초인 후보 중 두 ΔJ 평균이 최솟값에서 1초 이내인 후보들 가운데
  환자당 보조 근무(혼합+다수)가 가장 적은 것. 그 후보의 평균 ΔJ가 0 미만이 아니면 검증하지 않는다.
- 판정(검증, EAT-K): 혼합·다수 모두 ΔJ 95% 구간 상한 < +5초이고, 둘 중 하나 이상 상한 < 0이면 채택 후보.
  환자당 보조 근무 차이(혼합+다수)와 95% 구간은 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_duty as duty, experiment_final2 as prev,
               experiment_wide as wide, reactive_runner, scenarios)

DEV_MIX = tuple(range(2090001, 2090241))
DEV_MASS = tuple(range(2110001, 2110121))
DEV_FAMILY = (2130001, 2130002, 2130003)
VAL_MIX = tuple(range(2100001, 2100401))
VAL_MASS = tuple(range(2120001, 2120201))
VAL_FAMILY = (2140001, 2140002, 2140003, 2140004, 2140005, 2140006)
FOCUS = prev.G_POLICY
POLICIES = (FOCUS, 'fixed_3')
BASE = prev.G_RULE
BASE_OD = 'od_k2_d60_l120'
assert BASE.startswith(BASE_OD + '+')
TAIL = BASE[len(BASE_OD):]
CANDIDATES = tuple(f'od_k2_d60_l{linger}{TAIL}+ra' for linger in (120, 90, 60))
RULES = (BASE,) + CANDIDATES
TOLERANCE, MARGIN, NEAR = 2.0, 5.0, 1.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/reactive_runner.py', 'inquiry_lab/experiment_reactive.py')
SELECTION_RULE = (f'개발(EAT-K): 혼합·다수 모두 ΔJ ≤ +{TOLERANCE:.0f}초인 후보 중 두 ΔJ 평균이 최솟값에서 {NEAR:.0f}초 이내인 후보들 가운데 '
                  '보조 근무가 가장 적은 것. 그 평균이 0 미만이 아니면 검증하지 않는다.')
SUCCESS_RULE = f'검증(EAT-K): 혼합·다수 모두 ΔJ 95% 구간 상한 < +{MARGIN:.0f}초이고, 둘 중 하나 이상 상한 < 0이면 채택 후보.'


def arm(policy, rule):
    return f'{policy}|{rule}'


def short(rule):
    if rule == BASE:
        return 'BASE(1명 병원, 복귀 전 120초)'
    return f"전체 병원, 복귀 전 {rule.split('+', 1)[0].rsplit('_l', 1)[1]}초"


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    scenario = wide.build(kind, seed)
    result = reactive_runner.run_scenario(scenario, arm_list)
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
                row = dict(metrics=base.policy_metrics(rs, name), duty=duty.duty(rs, name))
                if rule != BASE:
                    row['versus_base'] = base.paired(rs, name, arm(p, BASE))
                    row['duty_difference'] = duty.duty_difference(rs, name, arm(p, BASE))
                rows[rule] = row
            block[p] = rows
        report['groups'][g] = block
    both = groups['mix'] + groups['mass']
    report['pooled_duty'] = {rule: duty.duty(both, arm(FOCUS, rule))['on_duty_seconds_per_patient'] for rule in rules}
    report['pooled_duty_difference'] = {rule: duty.duty_difference(both, arm(FOCUS, rule), arm(FOCUS, BASE))
                                        for rule in rules if rule != BASE}
    report['by_arrival'] = {}
    for g, rs in groups.items():
        for a in sorted({r['mix']['arrival'] for r in rs}):
            subset = [r for r in rs if r['mix']['arrival'] == a]
            report['by_arrival'][f'{g}:{a}'] = {rule: base.paired(subset, arm(FOCUS, rule), arm(FOCUS, BASE))
                                                for rule in rules if rule != BASE}
    report['families'] = base.family_table(fam, [arm(p, r) for p in POLICIES for r in rules])
    return report


def select(report):
    g = report['groups']
    delta = lambda k, c: g[k][FOCUS][c]['versus_base']['delta_J_mean']
    feasible = [c for c in CANDIDATES if all(delta(k, c) <= TOLERANCE for k in ('mix', 'mass'))]
    if not feasible:
        return dict(rule=None, proceed=False, feasible=[])
    mean = lambda c: (delta('mix', c) + delta('mass', c)) / 2
    best_mean = min(mean(c) for c in feasible)
    near = [c for c in feasible if mean(c) <= best_mean + NEAR]
    chosen = min(near, key=lambda c: (round(report['pooled_duty'][c], 9), CANDIDATES.index(c)))
    return dict(rule=chosen, proceed=mean(chosen) < 0, feasible=feasible, near_best=near, mean_delta_J=mean(chosen),
                delta_J={k: delta(k, chosen) for k in ('mix', 'mass')}, pooled_duty=report['pooled_duty'][chosen])


def verdict(report, chosen):
    g = report['groups']
    rows = {k: g[k][FOCUS][chosen]['versus_base'] for k in ('mix', 'mass')}
    noninferior = all(v['ci95'][1] < MARGIN for v in rows.values())
    better = any(v['ci95'][1] < 0 for v in rows.values())
    return dict(rule=chosen, noninferior=noninferior, better=better, adopted=noninferior and better,
                delta_J={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in rows.items()},
                duty_difference=report['pooled_duty_difference'][chosen])


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    rules = report['rules']
    lines = [f"# 보조 호출 대상 확대 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'기준(BASE): `{BASE}`', '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['rule'] and short(s['rule'])} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '', f"- {short(v['rule'])} → 채택 후보 **{v['adopted']}**"]
        for k, row in v['delta_J'].items():
            lines.append(f"- {k}: ΔJ {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
        d = v['duty_difference']
        lines += [f"- 보조 근무 차이(초/환자, 혼합+다수): {_f(d['difference_per_patient'])} [{_f(d['ci95'][0])}, {_f(d['ci95'][1])}]", '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, by_policy in report['groups'].items():
        lines += [f'## {names[g]}', '', '| 정책 | 보조 호출 | J(초) | 실패 | ΔJ vs BASE [95% 구간] | 보조 근무(초/환자) | 근무 차이 [95% 구간] | 호출/환자100 | 헛호출 |',
                  '|---|---|---:|---:|---|---:|---|---:|---:|']
        for p, rows in by_policy.items():
            for rule in rules:
                row = rows[rule]
                v, dd, du = row.get('versus_base'), row.get('duty_difference'), row['duty']
                delta = f"{_f(v['delta_J_mean'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}]" if v else '—'
                ddiff = f"{_f(dd['difference_per_patient'])} [{_f(dd['ci95'][0])}, {_f(dd['ci95'][1])}]" if dd else '—'
                false = '—' if du['false_alarm_share'] is None else f"{100 * du['false_alarm_share']:.0f}%"
                lines.append(f"| {p} | {short(rule)} | {_f(row['metrics']['J'])} | {row['metrics']['failed']} | {delta} | "
                             f"{_f(du['on_duty_seconds_per_patient'])} | {ddiff} | {_f(du['calls_per_100_patients'])} | {false} |")
        lines.append('')
    lines += [f'## 도착 형태별 ΔJ(후보 − BASE, {FOCUS})', '', '| 묶음 | 보조 호출 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for key, by_rule in report['by_arrival'].items():
        for rule, v in by_rule.items():
            lines.append(f"| {key} | {short(rule)} | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 확정 대기 규칙의 확률 계산은 2차 모형을 쓴다.',
        '- 보조 근무시간은 호출부터 복귀까지(합류 대기·복귀 전 대기 포함)다. 2명 병원에도 보조 담당이 있다고 가정한다.']
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
                  base_rule=BASE, selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], mass=[DEV_MASS[0], DEV_MASS[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, RULES))
    report['selection'] = select(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_reactive_20261027', base_rule=BASE, selection=report['selection'],
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
        raise RuntimeError('development selected no improving rule; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    if lock['base_rule'] != BASE:
        raise RuntimeError('base rule changed after development lock')
    chosen = lock['selection']['rule']
    rules = (BASE, chosen)
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_MASS, VAL_FAMILY, rules), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(rules),
                  base_rule=BASE, selected_rule=chosen, lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
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
                    v = row.get('versus_base')
                    print(g, p, short(rule), round(row['metrics']['J'], 1), None if v is None else round(v['delta_J_mean'], 1),
                          round(row['duty']['on_duty_seconds_per_patient'], 1))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
