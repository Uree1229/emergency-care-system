"""32차 실험: 중증도 관점(중증 우선 문의함 정렬·중증 환자 적극 문의). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_priority development --output simulation_results/inquiry_lab_priority_development_20261028
python -m inquiry_lab.experiment_priority validation --lock simulation_results/inquiry_lab_priority_development_20261028_lock.json \
    --output simulation_results/inquiry_lab_priority_validation_20261028

사전 고정:
- 질문: 지금까지 모든 환자의 중증도가 같다고 봤다. 서버는 전문가 평가 중증도(4 높음·2 중간·1 낮음)를 저장하고 병원 문의함을
  중증도 순으로 보여 준다. 중증 환자를 먼저 검토하게 하거나(중증 우선 정렬) 중증 환자에게 더 적극적으로 물으면, 중증 환자의
  도착이 얼마나 빨라지고 전체 평균은 얼마나 손해를 보는가.
- 환경: 26차 최종 구성(G)에서 환자마다 중증도를 해시로 고정해 준다(높음 20%·중간 50%·낮음 30%). 수용률·검토 시간은 중증도와
  무관하게 그대로 둔다. 8유형(환자 1명) 시나리오는 정렬·경합이 없어 쓰지 않는다.
  새 seed만 사용: 개발 혼합 2340001–2340240·다수 2350001–2350120, 검증 혼합 2360001–2360400·다수 2370001–2370200.
- 후보: (eatks_seenbase_k10_m4, G + pf 중증 우선 정렬) · (eatkp_l0_m6 중증 적극 문의, G) · (eatkp_l0_m6, G + pf).
- 선택(개발): 혼합·다수 모두 전체 ΔJ(후보 − G) ≤ +2초인 후보 중 중증 환자 ΔJ의 혼합·다수 평균이 가장 작은 것. 그 평균이 0 미만이
  아니면 검증하지 않는다.
- 판정(검증): 중증 환자 ΔJ의 95% 구간 상한이 혼합·다수 중 하나 이상 < 0이고 둘 다 < +5초, 그리고 전체 ΔJ 상한이 혼합·다수 모두
  < +5초이면 채택 후보(중증 환자는 빨라지고 전체 평균은 크게 나빠지지 않음).
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import platform
import random
import time

from . import (eat_capacity_policy, eat_priority_policy, eat_seen_policy, experiment as base, experiment_final2 as prev,
               experiment_wide as wide, harness, priority_runner, scenarios)

DEV_MIX = tuple(range(2340001, 2340241))
DEV_MASS = tuple(range(2350001, 2350121))
VAL_MIX = tuple(range(2360001, 2360401))
VAL_MASS = tuple(range(2370001, 2370201))
DEV_FAMILY = VAL_FAMILY = ()
SHARES = ((4, 0.2), (2, 0.5), (1, 0.3))
LEVELS = {4: 'high', 2: 'mid', 1: 'low'}
FOCUS, G_RULE = prev.G_POLICY, prev.G_RULE
PF_RULE = G_RULE + '+pf'
BASE_ARM = (FOCUS, G_RULE)
CANDIDATES = ((FOCUS, PF_RULE), ('eatkp_l0_m6', G_RULE), ('eatkp_l0_m6', PF_RULE))
ARMS = (BASE_ARM,) + CANDIDATES
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/priority_runner.py', 'inquiry_lab/eat_priority_policy.py',
                                    'inquiry_lab/experiment_priority.py')
SELECTION_RULE = (f'개발: 혼합·다수 모두 전체 ΔJ ≤ +{TOLERANCE:.0f}초인 후보 중 중증 환자 ΔJ의 혼합·다수 평균 최소. '
                  '그 평균이 0 미만이 아니면 검증하지 않는다.')
SUCCESS_RULE = (f'검증: 중증 환자 ΔJ 상한이 혼합·다수 중 하나 이상 < 0이고 둘 다 < +{MARGIN:.0f}초, 전체 ΔJ 상한이 혼합·다수 모두 '
                f'< +{MARGIN:.0f}초이면 채택 후보.')


def arm(policy, rule):
    return f'{policy}|{rule}'


def label(arm_pair):
    policy, rule = arm_pair
    parts = ['중증 적극 문의' if policy.startswith(eat_priority_policy.PREFIX) else 'EAT-K',
             '중증 우선 정렬' if rule.endswith('+pf') else '분산 정렬']
    return ' + '.join(parts)


def with_priority(scenario):
    """환자마다 중증도를 (시나리오, 환자) 해시로 고정해 붙인다."""
    patients = []
    for p in scenario['patients']:
        u = int(sha256(f"{scenario['id']}|{p['id']}|priority".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        total, level = 0.0, SHARES[-1][0]
        for value, share in SHARES:
            total += share
            if u < total:
                level = value
                break
        patients.append(dict(p, priority=level))
    return dict(scenario, patients=patients)


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    eat_priority_policy.install([p for p in names if p.startswith(eat_priority_policy.PREFIX)])
    scenario = with_priority(wide.build(kind, seed))
    result = priority_runner.run_scenario(scenario, arm_list)
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


def _tasks(mix_seeds, mass_seeds, arms):
    arm_list = list(arms)
    return [('mass', s, arm_list) for s in mass_seeds] + [('mix', s, arm_list) for s in mix_seeds]


def group_costs(result, name, level=None):
    """시나리오 하나에서 (중증도 level 환자의 비용 합, 환자 수). level이 없으면 전체."""
    rows = [p for p in result['branches'][name]['patients'] if level is None or result['priorities'][p['id']] == level]
    return sum(harness.patient_cost(p, base.PRIMARY_PENALTY) for p in rows), len(rows)


def group_paired(results, a, b, level=None):
    """중증도 묶음의 환자당 ΔJ(a − b)와 시나리오 단위 부트스트랩 95% 구간."""
    rows = []
    for r in results:
        ca, n = group_costs(r, a, level)
        cb, _ = group_costs(r, b, level)
        if n:
            rows.append((ca - cb, n))
    if not rows:
        return dict(delta_J_mean=None, ci95=[None, None], patients=0)
    rng = random.Random(base.BOOTSTRAP_SEED)
    stats = sorted(sum(d for d, _ in s) / sum(n for _, n in s)
                   for s in ([rows[rng.randrange(len(rows))] for _ in rows] for _ in range(base.BOOTSTRAP_RESAMPLES)))
    return dict(delta_J_mean=sum(d for d, _ in rows) / sum(n for _, n in rows), patients=sum(n for _, n in rows),
                ci95=[stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats)) - 1]])


def group_J(results, name, level=None):
    total = [group_costs(r, name, level) for r in results]
    n = sum(c for _, c in total)
    return sum(s for s, _ in total) / n if n else None


def evaluate(results, arms):
    report = dict(groups={})
    for kind in ('mix', 'mass'):
        rs = [r for r in results if r['kind'] == kind]
        rows = {}
        for pair in arms:
            name = arm(*pair)
            m = base.policy_metrics(rs, name)
            row = dict(label=label(pair), J=m['J'], failed=m['failed'], inquiries_per_patient=m['inquiries_per_patient'],
                       J_by_level={LEVELS[v]: group_J(rs, name, v) for v in LEVELS})
            if pair != BASE_ARM:
                row['versus_base'] = dict(all=group_paired(rs, name, arm(*BASE_ARM)),
                                          **{LEVELS[v]: group_paired(rs, name, arm(*BASE_ARM), v) for v in LEVELS})
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
    g = report['groups']
    rows = {k: g[k][arm(*chosen)]['versus_base'] for k in ('mix', 'mass')}
    upper = lambda v, part: float('inf') if v[part]['ci95'][1] is None else v[part]['ci95'][1]   # 환자가 없으면 판정 미달
    high_ok = any(upper(v, 'high') < 0 for v in rows.values()) and all(upper(v, 'high') < MARGIN for v in rows.values())
    overall_ok = all(upper(v, 'all') < MARGIN for v in rows.values())
    return dict(arm=list(chosen), high_better=high_ok, overall_noninferior=overall_ok, adopted=high_ok and overall_ok,
                delta_J={k: {part: dict(delta_J=v[part]['delta_J_mean'], ci95=v[part]['ci95']) for part in ('all', 'high', 'mid', 'low')}
                         for k, v in rows.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    lines = [f"# 중증도 관점 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'운영 구성: `{G_RULE}` · 중증도 높음 20%·중간 50%·낮음 30%', '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['arm'] and label(tuple(s['arm']))} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '', f"- {label(tuple(v['arm']))} → 채택 후보 **{v['adopted']}**"]
        for k, parts in v['delta_J'].items():
            lines.append(f"- {k}: 전체 {_f(parts['all']['delta_J'])} [{_f(parts['all']['ci95'][0])}, {_f(parts['all']['ci95'][1])}], "
                         f"중증 {_f(parts['high']['delta_J'])} [{_f(parts['high']['ci95'][0])}, {_f(parts['high']['ci95'][1])}]")
        lines.append('')
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for kind, rows in report['groups'].items():
        lines += [f'## {names[kind]}', '', '| 구성 | 전체 J | 중증 J | 중간 J | 경증 J | 전체 ΔJ [95%] | 중증 ΔJ [95%] | 중간 ΔJ | 경증 ΔJ | 실패 |',
                  '|---|---:|---:|---:|---:|---|---|---:|---:|---:|']
        for name, row in rows.items():
            by = row['J_by_level']
            v = row.get('versus_base')
            fmt = lambda part: f"{_f(v[part]['delta_J_mean'])} [{_f(v[part]['ci95'][0])}, {_f(v[part]['ci95'][1])}]" if v else '—'
            short = lambda part: _f(v[part]['delta_J_mean']) if v else '—'
            lines.append(f"| {row['label']} | {_f(row['J'])} | {_f(by['high'])} | {_f(by['mid'])} | {_f(by['low'])} | "
                         f"{fmt('all')} | {fmt('high')} | {short('mid')} | {short('low')} | {row['failed']} |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 중증도 비율(20·50·30%)은 가정이며, 수용률·검토 시간이 중증도와 무관하다고 본다. 실제로는 중증 환자를 받을 수 있는 병원이 더 적다.',
        '- 중증 환자의 시간 가치를 몇 배로 볼지는 정하지 않았다. 묶음별 J를 따로 보고한다.']
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
    results, elapsed = run_all(_tasks(DEV_MIX, DEV_MASS, ARMS), workers)
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), scenario_count=len(results),
                  elapsed_seconds=elapsed, arms=[list(a) for a in ARMS], selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], mass=[DEV_MASS[0], DEV_MASS[-1]]),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()), results=results, **evaluate(results, ARMS))
    report['selection'] = select(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_priority_20261028', selection=report['selection'], source_sha256=hashes,
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
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_MASS, arms), workers)
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
            for name, row in rows.items():
                v = row.get('versus_base')
                print(kind, row['label'], round(row['J'], 1), None if v is None else
                      (round(v['all']['delta_J_mean'], 1), round(v['high']['delta_J_mean'], 1), round(v['low']['delta_J_mean'], 1)))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
