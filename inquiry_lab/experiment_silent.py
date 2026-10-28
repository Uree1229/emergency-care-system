"""19차 실험: 응답 없이 닫힌 문의 조기 종료. 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_silent development --output simulation_results/inquiry_lab_silent_development_20261026
python -m inquiry_lab.experiment_silent validation --lock simulation_results/inquiry_lab_silent_development_20261026_lock.json \
    --output simulation_results/inquiry_lab_silent_validation_20261026

사전 고정:
- 질문: 병원이 검토만 하고 답하지 않은 문의(무응답, 약 20%)를, 병원 화면의 닫기 기록으로 알아 G초 뒤 '불가'로 종료하면
  문의 정책이 더 빨리 다른 병원에 물어 J가 줄어드는가. 4차에서 '묻기 전에 무응답을 알면' −28.8초였다.
- 환경: 17차 최종 구성(PACKAGE = 보조 호출 + 선제 호출 + 취소 중단 + 분산 정렬). 참고로 현행 운영(base)에도 붙여 본다.
  새 seed만 사용: 개발 혼합 1670001–1670240·다수 1690001–1690120·8유형 1710001–1710003,
  검증 혼합 1680001–1680400·다수 1700001–1700200·8유형 1720001–1720006.
- 정책: eatks_seenbase_k10_m4(15차, 선택·판정 기준), fixed_3(참고).
- 후보: PACKAGE + nr0·nr30·nr60(답 없이 닫힌 뒤 0·30·60초에 종료).
- 선택(개발, EAT-K): 혼합·다수 모두 ΔJ(후보 − PACKAGE) ≤ +2초인 후보 중 두 ΔJ의 평균이 가장 작은 것(같으면 긴 유예).
  그 평균이 0 미만이 아니면 검증하지 않는다.
- 판정(검증, EAT-K): 혼합 ΔJ(선택 − PACKAGE)의 95% 구간 상한 < 0 이고 다수 사상자 95% 구간 상한 < +5초이면 채택 후보.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_fair as prev, experiment_wide as wide,
               scenarios, silent_runner)

DEV_MIX = tuple(range(1670001, 1670241))
DEV_MASS = tuple(range(1690001, 1690121))
DEV_FAMILY = (1710001, 1710002, 1710003)
VAL_MIX = tuple(range(1680001, 1680401))
VAL_MASS = tuple(range(1700001, 1700201))
VAL_FAMILY = (1720001, 1720002, 1720003, 1720004, 1720005, 1720006)
FOCUS = prev.FOCUS
POLICIES = (FOCUS, 'fixed_3')
PACKAGE = prev.SPREAD
CANDIDATES = tuple(f'{PACKAGE}+{silent_runner.silent_name(g)}' for g in (0, 30, 60))
RULES = ('base', f'base+{silent_runner.silent_name(0)}', PACKAGE) + CANDIDATES
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/silent_runner.py', 'inquiry_lab/experiment_silent.py')
SELECTION_RULE = (f'개발(EAT-K): 혼합·다수 모두 ΔJ(후보 − PACKAGE) ≤ +{TOLERANCE:.0f}초인 후보 중 두 ΔJ 평균 최소(같으면 긴 유예). '
                  '평균이 0 미만이 아니면 검증하지 않는다.')
SUCCESS_RULE = f'검증(EAT-K): 혼합 ΔJ(선택 − PACKAGE) 95% 구간 상한 < 0 이고 다수 사상자 95% 구간 상한 < +{MARGIN:.0f}초이면 채택 후보.'


def arm(policy, rule):
    return f'{policy}|{rule}'


def short(rule):
    if rule == PACKAGE:
        return 'PACKAGE'
    if rule.startswith(PACKAGE):
        return 'PACKAGE+' + rule.rsplit('+', 1)[1]
    return rule


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    scenario = wide.build(kind, seed)
    result = silent_runner.run_scenario(scenario, arm_list)
    branches = {}
    for b in result['branches']:
        row = wide.prev.prev.compact(b)
        row['silent_closed'] = b['silent_closed']
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
                row = dict(metrics=base.policy_metrics(rs, name),
                           silent_closed_per_patient=sum(r['branches'][name]['silent_closed'] for r in rs) / patients)
                if rule.startswith(PACKAGE) and rule != PACKAGE:
                    row['versus_package'] = base.paired(rs, name, arm(p, PACKAGE))
                if rule == f'base+{silent_runner.silent_name(0)}':
                    row['versus_base'] = base.paired(rs, name, arm(p, 'base'))
                rows[rule] = row
            block[p] = rows
        report['groups'][g] = block
    report['by_arrival'] = {}
    for g, rs in groups.items():
        for a in sorted({r['mix']['arrival'] for r in rs}):
            subset = [r for r in rs if r['mix']['arrival'] == a]
            report['by_arrival'][f'{g}:{a}'] = {rule: base.paired(subset, arm(FOCUS, rule), arm(FOCUS, PACKAGE))
                                                for rule in rules if rule.startswith(PACKAGE) and rule != PACKAGE}
    report['families'] = base.family_table(fam, [arm(p, r) for p in POLICIES for r in rules])
    return report


def select(report):
    g = report['groups']
    delta = lambda k, c: g[k][FOCUS][c]['versus_package']['delta_J_mean']
    feasible = [c for c in CANDIDATES if all(delta(k, c) <= TOLERANCE for k in ('mix', 'mass'))]
    if not feasible:
        return dict(rule=None, proceed=False, feasible=[])
    mean = lambda c: (delta('mix', c) + delta('mass', c)) / 2
    best = min(feasible, key=lambda c: (round(mean(c), 9), -CANDIDATES.index(c)))
    return dict(rule=best, proceed=mean(best) < 0, feasible=feasible, mean_delta_J=mean(best),
                delta_J={k: delta(k, best) for k in ('mix', 'mass')})


def verdict(report, chosen):
    g = report['groups']
    mix, mass = g['mix'][FOCUS][chosen]['versus_package'], g['mass'][FOCUS][chosen]['versus_package']
    return dict(rule=chosen, mix_better=mix['ci95'][1] < 0, mass_noninferior=mass['ci95'][1] < MARGIN,
                adopted=mix['ci95'][1] < 0 and mass['ci95'][1] < MARGIN, mix_delta_J=mix['delta_J_mean'], mix_ci95=mix['ci95'],
                mass_delta_J=mass['delta_J_mean'], mass_ci95=mass['ci95'])


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    rules = report['rules']
    lines = [f"# 응답 없이 닫힌 문의 조기 종료 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
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
        lines += [f'## {names[g]}', '', '| 정책 | 운영 | J(초) | 실패 | 환자당 문의 | ΔJ vs PACKAGE(또는 base) [95% 구간] | 자동 종료(건/환자) |',
                  '|---|---|---:|---:|---:|---|---:|']
        for p, rows in by_policy.items():
            for rule in rules:
                row = rows[rule]
                v = row.get('versus_package') or row.get('versus_base')
                lines.append(f"| {p} | {short(rule)} | {_f(row['metrics']['J'])} | {row['metrics']['failed']} | "
                             f"{_f(row['metrics']['inquiries_per_patient'], 2)} | "
                             f"{(_f(v['delta_J_mean']) + ' [' + _f(v['ci95'][0]) + ', ' + _f(v['ci95'][1]) + ']') if v else '—'} | "
                             f"{_f(row['silent_closed_per_patient'], 2)} |")
        lines.append('')
    lines += [f'## 도착 형태별 ΔJ(후보 − PACKAGE, {FOCUS})', '', '| 묶음 | 운영 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for key, by_rule in report['by_arrival'].items():
        for rule, v in by_rule.items():
            lines.append(f"| {key} | {short(rule)} | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 무응답은 검토 뒤 답을 보내지 않는 모형이다. 실제로는 검토 전에 놓친 문의도 있어, 닫기 기록이 없는 무응답에는 이 규칙이 작동하지 않는다.']
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
    lock = dict(schema_version=1, experiment='inquiry_lab_silent_20261026', selection=report['selection'],
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
        raise RuntimeError('development selected no silent-close rule; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['rule']
    rules = (PACKAGE, chosen)
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
                    v = row.get('versus_package') or row.get('versus_base')
                    print(g, p, short(rule), round(row['metrics']['J'], 1), None if v is None else round(v['delta_J_mean'], 1),
                          round(row['silent_closed_per_patient'], 2))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
