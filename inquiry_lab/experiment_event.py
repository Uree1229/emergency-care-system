"""20차 실험: 회신 즉시 재판단. 개발(진행 판단) → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_event development --output simulation_results/inquiry_lab_event_development_20261026
python -m inquiry_lab.experiment_event validation --lock simulation_results/inquiry_lab_event_development_20261026_lock.json \
    --output simulation_results/inquiry_lab_event_validation_20261026

사전 고정:
- 질문: 환자의 다른 문의가 대기 중이어도 거절(또는 19차 자동 종료)이 오는 즉시 그 환자를 다시 판단하면(ev) J가 줄어드는가.
  지금 서버는 EAT의 다음 판단 시각(15초 간격)까지 기다린다. 병원 화면 변경 없이 서버만 바꾸는 규칙이다.
- 환경: 17차 최종 구성(PACKAGE = 보조 호출 + 선제 호출 + 취소 중단 + 분산 정렬)과 현행 병원 운영(base).
  19차 '응답 없이 닫힘 즉시 종료'(nr0)는 판정 미달이라 기준 구성에 넣지 않고, 함께 쓴 경우(PACKAGE+nr0+ev)만 참고로 보고한다. 새 seed만 사용: 개발 혼합 1730001–1730240·다수 1750001–1750120·8유형 1770001–1770003,
  검증 혼합 1740001–1740400·다수 1760001–1760200·8유형 1780001–1780006.
- 정책: eatks_seenbase_k10_m4(판정 기준), fixed_3(참고).
- 진행 조건(개발, EAT-K): PACKAGE에서 혼합·다수 ΔJ(ev − 없음)가 모두 +2초 이하이고 둘 중 하나 이상 0 미만.
- 판정(검증, EAT-K): PACKAGE에서 혼합·다수 ΔJ의 95% 구간 상한이 모두 < +5초(비열등)이고 둘 중 하나 이상 < 0이면 채택 후보.
  현행 운영(base)에서의 효과와 고정3은 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_seen_policy, event_runner, experiment as base, experiment_silent as prev,
               experiment_wide as wide, scenarios)

DEV_MIX = tuple(range(1730001, 1730241))
DEV_MASS = tuple(range(1750001, 1750121))
DEV_FAMILY = (1770001, 1770002, 1770003)
VAL_MIX = tuple(range(1740001, 1740401))
VAL_MASS = tuple(range(1760001, 1760201))
VAL_FAMILY = (1780001, 1780002, 1780003, 1780004, 1780005, 1780006)
FOCUS = prev.FOCUS
POLICIES = (FOCUS, 'fixed_3')
PACKAGE = prev.PACKAGE
CANDIDATE = f'{PACKAGE}+ev'
COMBO = f'{PACKAGE}+nr0+ev'
RULES = ('base', 'base+ev', PACKAGE, CANDIDATE, COMBO)
COMPARE = {'base+ev': 'base', CANDIDATE: PACKAGE, COMBO: PACKAGE}
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/event_runner.py', 'inquiry_lab/experiment_event.py')
GO_RULE = f'개발(EAT-K): PACKAGE에서 혼합·다수 ΔJ(ev − 없음) ≤ +{TOLERANCE:.0f}초이고 둘 중 하나 이상 < 0이면 검증한다.'
SUCCESS_RULE = (f'검증(EAT-K): PACKAGE에서 혼합·다수 ΔJ 95% 구간 상한 모두 < +{MARGIN:.0f}초이고 둘 중 하나 이상 < 0이면 채택 후보.')


def arm(policy, rule):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    scenario = wide.build(kind, seed)
    result = event_runner.run_scenario(scenario, arm_list)
    branches = {}
    for b in result['branches']:
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'])
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
                           replans_per_patient=sum(r['branches'][name]['replans'] for r in rs) / patients)
                if rule in COMPARE:
                    row['versus_without'] = base.paired(rs, name, arm(p, COMPARE[rule]))
                rows[rule] = row
            block[p] = rows
        report['groups'][g] = block
    report['by_arrival'] = {}
    for g, rs in groups.items():
        for a in sorted({r['mix']['arrival'] for r in rs}):
            subset = [r for r in rs if r['mix']['arrival'] == a]
            report['by_arrival'][f'{g}:{a}'] = {rule: base.paired(subset, arm(FOCUS, rule), arm(FOCUS, COMPARE[rule]))
                                                for rule in rules if rule in COMPARE}
    report['families'] = base.family_table(fam, [arm(p, r) for p in POLICIES for r in rules])
    return report


def go(report):
    g = report['groups']
    delta = {k: g[k][FOCUS][CANDIDATE]['versus_without']['delta_J_mean'] for k in ('mix', 'mass')}
    proceed = all(v <= TOLERANCE for v in delta.values()) and any(v < 0 for v in delta.values())
    return dict(rule=CANDIDATE, proceed=proceed, delta_J=delta)


def verdict(report):
    g = report['groups']
    rows = {k: g[k][FOCUS][CANDIDATE]['versus_without'] for k in ('mix', 'mass')}
    noninferior = all(v['ci95'][1] < MARGIN for v in rows.values())
    better = any(v['ci95'][1] < 0 for v in rows.values())
    return dict(rule=CANDIDATE, noninferior=noninferior, better=better, adopted=noninferior and better,
                delta_J={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in rows.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def short(rule):
    return rule.replace(prev.PACKAGE, 'PACKAGE')


def markdown(report):
    rules = report['rules']
    lines = [f"# 회신 즉시 재판단 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['go']
        lines += [f'**진행 조건:** {GO_RULE}', '', f"- 혼합 {_f(s['delta_J']['mix'])}초, 다수 {_f(s['delta_J']['mass'])}초 → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '', f"- 채택 후보 **{v['adopted']}**"]
        for k, row in v['delta_J'].items():
            lines.append(f"- {k}: ΔJ {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
        lines.append('')
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, by_policy in report['groups'].items():
        lines += [f'## {names[g]}', '', '| 정책 | 운영 | J(초) | 실패 | 환자당 문의 | ΔJ(규칙 − 기준 운영) [95% 구간] | 재판단(건/환자) |',
                  '|---|---|---:|---:|---:|---|---:|']
        for p, rows in by_policy.items():
            for rule in rules:
                row = rows[rule]
                v = row.get('versus_without')
                lines.append(f"| {p} | {short(rule)} | {_f(row['metrics']['J'])} | {row['metrics']['failed']} | "
                             f"{_f(row['metrics']['inquiries_per_patient'], 2)} | "
                             f"{(_f(v['delta_J_mean']) + ' [' + _f(v['ci95'][0]) + ', ' + _f(v['ci95'][1]) + ']') if v else '—'} | "
                             f"{_f(row['replans_per_patient'], 2)} |")
        lines.append('')
    lines += [f'## 도착 형태별 ΔJ(규칙 − 기준 운영, {FOCUS})', '', '| 묶음 | 운영 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for key, by_rule in report['by_arrival'].items():
        for rule, v in by_rule.items():
            lines.append(f"| {key} | {short(rule)} | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS]
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
                  go_rule=GO_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], mass=[DEV_MASS[0], DEV_MASS[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, RULES))
    report['go'] = go(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_event_20261026', go=report['go'],
                source_sha256=hashes, development_report=base.relative_path(json_path),
                development_report_sha256=base.sha256_file(json_path),
                validation_seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], mass=[VAL_MASS[0], VAL_MASS[-1]], families=list(VAL_FAMILY)),
                go_rule=GO_RULE, success_rule=SUCCESS_RULE)
    lock_path = Path(str(output) + '_lock.json')
    if lock_path.exists():
        raise FileExistsError(f'{lock_path} already exists')
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding='utf-8')
    return json_path, md_path, lock_path, report


def validation(lock_path, output, workers):
    lock_path, output = Path(lock_path).resolve(), Path(output).resolve()
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if not lock['go']['proceed']:
        raise RuntimeError('development did not meet the go rule; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_MASS, VAL_FAMILY, RULES), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(RULES),
                  lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path), success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], mass=[VAL_MASS[0], VAL_MASS[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, RULES))
    report['verdict'] = verdict(report)
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
        print('go', report['go'])
        for g, by_policy in report['groups'].items():
            for p, rows in by_policy.items():
                for rule, row in rows.items():
                    v = row.get('versus_without')
                    print(g, p, short(rule), round(row['metrics']['J'], 1), None if v is None else round(v['delta_J_mean'], 1),
                          round(row['replans_per_patient'], 2))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
