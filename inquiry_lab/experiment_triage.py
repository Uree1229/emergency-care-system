"""35차 실험: 문의함 중증도 정렬 방식(엄격한 중증 우선 vs 중증만 우선). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_triage development --output simulation_results/inquiry_lab_triage_development_20261028
python -m inquiry_lab.experiment_triage validation --lock simulation_results/inquiry_lab_triage_development_20261028_lock.json \
    --output simulation_results/inquiry_lab_triage_validation_20261028

사전 고정:
- 질문: 서버의 병원 문의함 목록은 중증도 4 → 2 → 1 순으로 엄격하게 정렬한다. 32차 개발에서 엄격한 중증 우선 정렬은 다수 사상자
  중증 환자를 −44초 앞당겼지만 경증 환자를 +52초 늦추고 전체를 +11초 나쁘게 했다. 중증도 높음만 앞으로 보내고 중간·낮음은
  같은 등급으로 보는 '중증만 우선' 정렬이 더 나은 균형인가.
- 환경: 34차 채택 구성(eatkp_l0_m6 + G, 중증도 높음 20%·중간 50%·낮음 30%)을 기준으로 문의함 정렬만 바꾼다(triage_runner).
  새 seed만 사용: 개발 혼합 2420001–2420240·다수 2430001–2430120, 검증 혼합 2440001–2440400·다수 2450001–2450400.
- 후보: + pf(엄격한 중증 우선), + pf4(중증만 우선).
- 선택(개발): 혼합·다수 모두 전체 ΔJ(후보 − 기준) ≤ +2초인 후보 중 중증 환자 ΔJ의 혼합·다수 평균 최소. 그 평균이 0 미만이 아니면
  검증하지 않는다.
- 판정(검증): 다수 사상자 중증 환자 ΔJ 상한 < 0, 다수 사상자 전체 상한 < +5초, 보통 상황 전체 상한 < +5초이면 채택 후보
  (34차와 같은 기준, 보통 상황 중증 환자는 표본이 작아 보고만 한다).
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_priority_policy, eat_seen_policy, experiment as base, experiment_priority as prio,
               experiment_priority2 as prev, experiment_wide as wide, scenarios, triage_runner)

DEV_MIX = tuple(range(2420001, 2420241))
DEV_MASS = tuple(range(2430001, 2430121))
VAL_MIX = tuple(range(2440001, 2440401))
VAL_MASS = tuple(range(2450001, 2450401))
DEV_FAMILY = VAL_FAMILY = ()
G_RULE = prio.G_RULE
BASE_ARM = prev.CANDIDATE
CANDIDATES = ((BASE_ARM[0], G_RULE + '+pf'), (BASE_ARM[0], G_RULE + '+pf4'))
ARMS = (BASE_ARM,) + CANDIDATES
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/triage_runner.py', 'inquiry_lab/experiment_triage.py')
SELECTION_RULE = (f'개발: 혼합·다수 모두 전체 ΔJ ≤ +{TOLERANCE:.0f}초인 후보 중 중증 환자 ΔJ의 혼합·다수 평균 최소. '
                  '그 평균이 0 미만이 아니면 검증하지 않는다.')
SUCCESS_RULE = (f'검증: 다수 사상자 중증 환자 ΔJ 상한 < 0, 다수 사상자 전체 상한 < +{MARGIN:.0f}초, '
                f'보통 상황 전체 상한 < +{MARGIN:.0f}초이면 채택 후보.')
arm = prio.arm


def label(pair):
    rule = pair[1]
    return '엄격한 중증 우선' if rule.endswith('+pf') else '중증만 우선' if rule.endswith('+pf4') else '분산 정렬(34차 구성)'


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    eat_priority_policy.install([p for p in names if p.startswith(eat_priority_policy.PREFIX)])
    scenario = prio.with_priority(wide.build(kind, seed))
    result = triage_runner.run_scenario(scenario, arm_list)
    branches = {}
    for b in result['branches']:
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
        branches[arm(b['policy'], b['rule'])] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], mix=scenario.get('mix'),
                environment_sha256=result['environment_sha256'], patient_count=len(scenario['patients']),
                priorities={p['id']: p['priority'] for p in scenario['patients']},
                oracle=scenarios.oracle(scenario), branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def evaluate(results, arms):
    report = dict(groups={})
    for kind in ('mix', 'mass'):
        rs = [r for r in results if r['kind'] == kind]
        rows = {}
        for pair in arms:
            name = arm(*pair)
            m = base.policy_metrics(rs, name)
            row = dict(label=label(pair), J=m['J'], failed=m['failed'], inquiries_per_patient=m['inquiries_per_patient'],
                       J_by_level={prio.LEVELS[v]: prio.group_J(rs, name, v) for v in prio.LEVELS})
            if pair != BASE_ARM:
                row['versus_base'] = dict(all=prio.group_paired(rs, name, arm(*BASE_ARM)),
                                          **{prio.LEVELS[v]: prio.group_paired(rs, name, arm(*BASE_ARM), v) for v in prio.LEVELS})
            rows[name] = row
        report['groups'][kind] = rows
    return report


def select(report):
    g = report['groups']
    delta = lambda k, c, part: g[k][arm(*c)]['versus_base'][part]['delta_J_mean']
    known = lambda c: all(delta(k, c, part) is not None for k in ('mix', 'mass') for part in ('all', 'high'))
    feasible = [c for c in CANDIDATES if known(c) and all(delta(k, c, 'all') <= TOLERANCE for k in ('mix', 'mass'))]
    if not feasible:
        return dict(arm=None, proceed=False, feasible=[])
    high = lambda c: (delta('mix', c, 'high') + delta('mass', c, 'high')) / 2
    best = min(feasible, key=lambda c: (round(high(c), 9), CANDIDATES.index(c)))
    return dict(arm=list(best), proceed=high(best) < 0, feasible=[list(c) for c in feasible], mean_high_delta_J=high(best),
                delta_J={k: dict(all=delta(k, best, 'all'), high=delta(k, best, 'high')) for k in ('mix', 'mass')})


def verdict(report, chosen):
    mass = report['groups']['mass'][arm(*chosen)]['versus_base']
    mix = report['groups']['mix'][arm(*chosen)]['versus_base']
    upper = lambda v: float('inf') if v['ci95'][1] is None else v['ci95'][1]
    checks = dict(mass_high_better=upper(mass['high']) < 0, mass_overall_noninferior=upper(mass['all']) < MARGIN,
                  mix_overall_noninferior=upper(mix['all']) < MARGIN)
    return dict(checks, arm=list(chosen), adopted=all(checks.values()),
                delta_J={k: {part: dict(delta_J=v[part]['delta_J_mean'], ci95=v[part]['ci95']) for part in ('all', 'high', 'mid', 'low')}
                         for k, v in (('mix', mix), ('mass', mass))})


def markdown(report):
    _f = prio._f
    lines = [f"# 문의함 중증도 정렬 방식 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'기준: 34차 채택 구성(`{BASE_ARM[0]}` + 분산 정렬) · 중증도 높음 20%·중간 50%·낮음 30%', '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['arm'] and label(tuple(s['arm']))} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '', f"- {label(tuple(v['arm']))} → 채택 후보 **{v['adopted']}**"]
        for k, parts in v['delta_J'].items():
            lines.append(f"- {k}: " + ', '.join(f"{name} {_f(parts[part]['delta_J'])} [{_f(parts[part]['ci95'][0])}, {_f(parts[part]['ci95'][1])}]"
                                                for part, name in (('all', '전체'), ('high', '중증'), ('mid', '중간'), ('low', '경증'))))
        lines.append('')
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for kind, rows in report['groups'].items():
        lines += [f'## {names[kind]}', '', '| 정렬 | 전체 J | 중증 J | 중간 J | 경증 J | 전체 ΔJ [95%] | 중증 ΔJ [95%] | 중간 ΔJ | 경증 ΔJ | 실패 |',
                  '|---|---:|---:|---:|---:|---|---|---:|---:|---:|']
        for row in rows.values():
            by, d = row['J_by_level'], row.get('versus_base')
            fmt = lambda part: f"{_f(d[part]['delta_J_mean'])} [{_f(d[part]['ci95'][0])}, {_f(d[part]['ci95'][1])}]" if d else '—'
            short = lambda part: _f(d[part]['delta_J_mean']) if d else '—'
            lines.append(f"| {row['label']} | {_f(row['J'])} | {_f(by['high'])} | {_f(by['mid'])} | {_f(by['low'])} | "
                         f"{fmt('all')} | {fmt('high')} | {short('mid')} | {short('low')} | {row['failed']} |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS] + [
        '- 중증도 비율(20·50·30%)은 가정이며, 수용률·검토 시간이 중증도와 무관하다고 본다.',
        '- 검토자가 서버 순서를 따르는 비율(50%)은 정렬 방식과 무관하게 같다고 본다.']
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
    results, elapsed = run_all(prio._tasks(DEV_MIX, DEV_MASS, ARMS), workers)
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), scenario_count=len(results),
                  elapsed_seconds=elapsed, arms=[list(a) for a in ARMS], selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], mass=[DEV_MASS[0], DEV_MASS[-1]]),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()), results=results, **evaluate(results, ARMS))
    report['selection'] = select(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_triage_20261028', selection=report['selection'], source_sha256=hashes,
                development_report=base.relative_path(json_path), development_report_sha256=base.sha256_file(json_path),
                validation_seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], mass=[VAL_MASS[0], VAL_MASS[-1]]),
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
        raise RuntimeError('development selected no candidate; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = tuple(lock['selection']['arm'])
    arms = (BASE_ARM, chosen)
    results, elapsed = run_all(prio._tasks(VAL_MIX, VAL_MASS, arms), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), scenario_count=len(results),
                  elapsed_seconds=elapsed, arms=[list(a) for a in arms], selected_arm=list(chosen), lock=base.relative_path(lock_path),
                  lock_sha256=base.sha256_file(lock_path), success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], mass=[VAL_MASS[0], VAL_MASS[-1]]),
                  source_sha256=current, runtime=dict(python=platform.python_version()), results=results, **evaluate(results, arms))
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
        for kind, rows in report['groups'].items():
            for row in rows.values():
                v = row.get('versus_base')
                print(kind, row['label'], round(row['J'], 1), None if v is None else
                      (round(v['all']['delta_J_mean'], 1), round(v['high']['delta_J_mean'], 1), round(v['low']['delta_J_mean'], 1)))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
