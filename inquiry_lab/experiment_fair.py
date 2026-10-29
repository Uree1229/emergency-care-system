"""18차 실험: 분산 정렬의 공정성 보완(대기 상한). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_fair development --output simulation_results/inquiry_lab_fair_development_20261025
python -m inquiry_lab.experiment_fair validation --lock simulation_results/inquiry_lab_fair_development_20261025_lock.json \
    --output simulation_results/inquiry_lab_fair_validation_20261025

사전 고정:
- 질문: 10차 분산 정렬은 평균 J를 줄이지만, 받은 순서(FIFO)였다면 성공했을 환자 일부를 실패로 바꾼다(10차 검증 126명 악화 중 6명).
  T초 이상 기다린 문의를 먼저 주는 대기 상한을 더하면, J를 지키면서 이런 '새 실패'를 줄일 수 있는가.
- 환경: 17차 최종 구성(보조 호출 + 선제 호출 + 취소 중단)에서 정렬 부분만 바꾼다. 새 seed만 사용:
  개발 혼합 1610001–1610240·다수 1630001–1630120·8유형 1650001–1650003,
  검증 혼합 1620001–1620400·다수 1640001–1640200·8유형 1660001–1660006.
- 정책: eatks_seenbase_k10_m4(15차, 선택·판정 기준), fixed_3(참고).
- 정렬: 받은 순서(FIFO), 분산 정렬(17차 구성), 대기 상한 분산 정렬 T ∈ {30, 60, 120}초(열람 100%·따름 50%).
- '새 실패' = 같은 시나리오에서 FIFO였다면 성공했지만 이 정렬에서는 실패한 환자.
- 선택(개발, EAT-K): 혼합·다수 모두 ΔJ(후보 − 분산 정렬) ≤ +2초인 후보 중 새 실패(혼합+다수 합)가 가장 적은 것.
  그 새 실패가 분산 정렬보다 적지 않으면 검증하지 않는다.
- 판정(검증, EAT-K): 혼합·다수 모두 ΔJ(선택 − 분산 정렬)의 95% 구간 상한 < +5초(비열등)이고,
  시나리오별 새 실패 차이(선택 − 분산 정렬, 혼합+다수)의 부트스트랩 95% 구간 상한 < 0이면 채택 후보.
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

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_final as prev, experiment_wide as wide,
               fair_runner, harness, scenarios)

DEV_MIX = tuple(range(1610001, 1610241))
DEV_MASS = tuple(range(1630001, 1630121))
DEV_FAMILY = (1650001, 1650002, 1650003)
VAL_MIX = tuple(range(1620001, 1620401))
VAL_MASS = tuple(range(1640001, 1640201))
VAL_FAMILY = (1660001, 1660002, 1660003, 1660004, 1660005, 1660006)
FOCUS = prev.EATK
POLICIES = (FOCUS, 'fixed_3')
STEM = 'od_k2_d60_l120+sg_n10_w120+ci_r30_q50'
FIFO = STEM
SPREAD = f'{STEM}+ord_spread_o100_c50'
CANDIDATES = tuple(f'{STEM}+{fair_runner.fair_name(t, 100, 50)}' for t in (30, 60, 120))
RULES = (FIFO, SPREAD) + CANDIDATES
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/fair_runner.py', 'inquiry_lab/experiment_fair.py')
SELECTION_RULE = (f'개발(EAT-K): 혼합·다수 모두 ΔJ(후보 − 분산 정렬) ≤ +{TOLERANCE:.0f}초인 후보 중 새 실패 최소. '
                  '분산 정렬보다 새 실패가 적지 않으면 검증하지 않는다.')
SUCCESS_RULE = (f'검증(EAT-K): 혼합·다수 모두 ΔJ(선택 − 분산 정렬) 95% 구간 상한 < +{MARGIN:.0f}초이고 '
                '시나리오별 새 실패 차이(선택 − 분산 정렬)의 95% 구간 상한 < 0이면 채택 후보.')


def arm(policy, rule):
    return f'{policy}|{rule}'


def short(rule):
    return 'FIFO' if rule == FIFO else rule.rsplit('+', 1)[1]


def run_case(task):
    kind, seed, arm_list = task
    names = [p for p, _ in arm_list]
    eat_capacity_policy.install([p for p in names if p.startswith(eat_capacity_policy.PREFIX)])
    eat_seen_policy.install([p for p in names if p.startswith(eat_seen_policy.PREFIX)])
    scenario = wide.build(kind, seed)
    result = fair_runner.run_scenario(scenario, arm_list)
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={arm(b['policy'], b['rule']): wide.prev.prev.compact(b) for b in result['branches']})


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


def new_failures(r, name, reference):
    ours = {p['id']: p for p in r['branches'][name]['patients']}
    ref = {p['id']: p for p in r['branches'][reference]['patients']}
    return sum(1 for pid in ours if ours[pid]['failed'] and not ref[pid]['failed'])


def harm(results, name, reference):
    """FIFO 대비 악화 환자 수·새 실패·악화 폭."""
    increases, failures = [], 0
    for r in results:
        ours = {p['id']: p for p in r['branches'][name]['patients']}
        ref = {p['id']: p for p in r['branches'][reference]['patients']}
        for pid in ours:
            d = harness.patient_cost(ours[pid]) - harness.patient_cost(ref[pid])
            if d > 1e-9:
                increases.append(d)
            failures += ours[pid]['failed'] and not ref[pid]['failed']
    increases.sort()
    n = len(increases)
    return dict(worse=n, new_failures=failures, median_increase=increases[n // 2] if n else 0.0,
                p90_increase=increases[int(0.9 * n)] if n else 0.0)


def new_failure_difference(results, a, b, reference):
    """시나리오별 (a의 새 실패 − b의 새 실패) 합과 부트스트랩 95% 구간."""
    rows = [new_failures(r, a, reference) - new_failures(r, b, reference) for r in results]
    rng = random.Random(base.BOOTSTRAP_SEED)
    stats = sorted(sum(rows[rng.randrange(len(rows))] for _ in rows) for _ in range(base.BOOTSTRAP_RESAMPLES))
    return dict(difference=sum(rows), ci95=[stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats)) - 1]])


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
                row = dict(metrics=base.policy_metrics(rs, name))
                if rule != FIFO:
                    row['versus_fifo'] = base.paired(rs, name, arm(p, FIFO))
                    row['harm'] = harm(rs, name, arm(p, FIFO))
                if rule not in (FIFO, SPREAD):
                    row['versus_spread'] = base.paired(rs, name, arm(p, SPREAD))
                rows[rule] = row
            block[p] = rows
        report['groups'][g] = block
    both = groups['mix'] + groups['mass']
    report['new_failure_difference'] = {rule: new_failure_difference(both, arm(FOCUS, rule), arm(FOCUS, SPREAD), arm(FOCUS, FIFO))
                                        for rule in rules if rule not in (FIFO, SPREAD)}
    report['families'] = base.family_table(fam, [arm(p, r) for p in POLICIES for r in rules])
    return report


def select(report):
    g = report['groups']
    feasible = [c for c in CANDIDATES
                if all(g[k][FOCUS][c]['versus_spread']['delta_J_mean'] <= TOLERANCE for k in ('mix', 'mass'))]
    if not feasible:
        return dict(rule=None, proceed=False, feasible=[])
    total = lambda rule: sum(g[k][FOCUS][rule]['harm']['new_failures'] for k in ('mix', 'mass'))
    best = min(feasible, key=lambda c: (total(c), CANDIDATES.index(c)))
    return dict(rule=best, proceed=total(best) < total(SPREAD), feasible=feasible,
                new_failures=dict(candidate=total(best), spread=total(SPREAD)),
                delta_J_vs_spread={k: g[k][FOCUS][best]['versus_spread']['delta_J_mean'] for k in ('mix', 'mass')})


def verdict(report, chosen):
    g = report['groups']
    nonin = {k: g[k][FOCUS][chosen]['versus_spread'] for k in ('mix', 'mass')}
    diff = report['new_failure_difference'][chosen]
    ok = all(v['ci95'][1] < MARGIN for v in nonin.values())
    return dict(rule=chosen, noninferior=ok, fewer_new_failures=diff['ci95'][1] < 0, adopted=ok and diff['ci95'][1] < 0,
                delta_J_vs_spread={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in nonin.items()},
                new_failure_difference=diff)


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    rules = report['rules']
    lines = [f"# 분산 정렬 공정성 보완(대기 상한) {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['rule']} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '', f"- {short(v['rule'])} → 채택 후보 **{v['adopted']}**"]
        for k, row in v['delta_J_vs_spread'].items():
            lines.append(f"- {k}: ΔJ vs 분산 정렬 {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
        d = v['new_failure_difference']
        lines += [f"- 새 실패 차이(선택 − 분산 정렬): {d['difference']} [{d['ci95'][0]}, {d['ci95'][1]}]", '']
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, by_policy in report['groups'].items():
        lines += [f'## {names[g]}', '', '| 정책 | 정렬 | J(초) | 실패 | ΔJ vs FIFO | ΔJ vs 분산 [95% 구간] | FIFO 대비 악화 | 새 실패 | 악화 폭 중앙/p90(초) |',
                  '|---|---|---:|---:|---:|---|---:|---:|---|']
        for p, rows in by_policy.items():
            for rule in rules:
                row = rows[rule]
                vf, vs, h = row.get('versus_fifo'), row.get('versus_spread'), row.get('harm')
                lines.append(f"| {p} | {short(rule)} | {_f(row['metrics']['J'])} | {row['metrics']['failed']} | "
                             f"{_f(vf['delta_J_mean']) if vf else '—'} | "
                             f"{(_f(vs['delta_J_mean']) + ' [' + _f(vs['ci95'][0]) + ', ' + _f(vs['ci95'][1]) + ']') if vs else '—'} | "
                             f"{h['worse'] if h else '—'} | {h['new_failures'] if h else '—'} | "
                             f"{(_f(h['median_increase'], 0) + ' / ' + _f(h['p90_increase'], 0)) if h else '—'} |")
        lines.append('')
    lines += ['## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS]
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
    lock = dict(schema_version=1, experiment='inquiry_lab_fair_20261025', selection=report['selection'],
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
        raise RuntimeError('development selected no fairness rule; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['rule']
    rules = (FIFO, SPREAD, chosen)
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
                    vs, h = row.get('versus_spread'), row.get('harm')
                    print(g, p, short(rule), round(row['metrics']['J'], 1), None if vs is None else round(vs['delta_J_mean'], 1),
                          None if h is None else (h['worse'], h['new_failures']))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
