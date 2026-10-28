"""25차 실험: 마감을 아는 EAT-K(첫 문의 기준 경과 시간 + 마감 임박 시 동시 문의 확대). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_deadline development --output simulation_results/inquiry_lab_deadline_development_20261026
python -m inquiry_lab.experiment_deadline validation --lock simulation_results/inquiry_lab_deadline_development_20261026_lock.json \
    --output simulation_results/inquiry_lab_deadline_validation_20261026

사전 고정:
- 질문: 22차 구성의 다수 사상자 실패 중 약 절반은 혼자라면 받아 줄 병원이 있던 환자다. 진단(22차 개발 seed)에서 그런 환자는
  받아 줄 병원에 도착 후 중앙값 191초에야 문의했다. EAT는 경과 시간을 마지막 문의부터 세고 동시 문의 상한(4곳)에 막혀,
  마감이 가까워도 더 묻지 못한다. 경과 시간을 첫 문의부터 세고, 마감 임박 환자만 상한을 올리면 J가 줄어드는가.
- 환경: 22차 채택 구성(RULE = experiment_hold2.BASE)을 그대로 두고 정책만 바꾼다(hold2_runner).
  새 seed만 사용: 개발 혼합 2030001–2030240·다수 2050001–2050120·8유형 2070001–2070003,
  검증 혼합 2040001–2040400·다수 2060001–2060200·8유형 2080001–2080006.
- 기준 정책: eatks_seenbase_k10_m4. 후보: eatkf_seenbase_k10_m4(경과 시간만 수정), eatkd_t120_m6·t120_m8·t150_m6·t150_m8.
- 선택(개발): 혼합·다수 모두 ΔJ(후보 − 기준) ≤ +2초인 후보 중 두 ΔJ 평균 최소. 평균이 0 미만이 아니면 검증하지 않는다.
- 판정(검증): 혼합·다수 모두 ΔJ 95% 구간 상한 < +5초이고, 둘 중 하나 이상 상한 < 0이면 채택 후보.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_first_policy, eat_seen_policy, experiment as base, experiment_hold2 as prev,
               experiment_wide as wide, hold2_runner, scenarios)

DEV_MIX = tuple(range(2030001, 2030241))
DEV_MASS = tuple(range(2050001, 2050121))
DEV_FAMILY = (2070001, 2070002, 2070003)
VAL_MIX = tuple(range(2040001, 2040401))
VAL_MASS = tuple(range(2060001, 2060201))
VAL_FAMILY = (2080001, 2080002, 2080003, 2080004, 2080005, 2080006)
FOCUS = prev.FOCUS
RULE = prev.BASE
CANDIDATES = ('eatkf_seenbase_k10_m4', 'eatkd_t120_m6', 'eatkd_t120_m8', 'eatkd_t150_m6', 'eatkd_t150_m8')
POLICIES = (FOCUS,) + CANDIDATES
TOLERANCE, MARGIN = 2.0, 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/eat_first_policy.py', 'inquiry_lab/experiment_deadline.py')
SELECTION_RULE = (f'개발: 혼합·다수 모두 ΔJ(후보 − {FOCUS}) ≤ +{TOLERANCE:.0f}초인 후보 중 두 ΔJ 평균 최소. '
                  '평균이 0 미만이 아니면 검증하지 않는다.')
SUCCESS_RULE = f'검증: 혼합·다수 모두 ΔJ 95% 구간 상한 < +{MARGIN:.0f}초이고, 둘 중 하나 이상 상한 < 0이면 채택 후보.'


def arm(policy, rule=RULE):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, policies = task
    eat_capacity_policy.install([])
    eat_seen_policy.install([p for p in policies if p.startswith(eat_seen_policy.PREFIX)])
    eat_first_policy.install([p for p in policies if p.startswith(eat_first_policy.PREFIXES)])
    scenario = wide.build(kind, seed)
    result = hold2_runner.run_scenario(scenario, [(p, RULE) for p in policies])
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


def _tasks(mix_seeds, mass_seeds, family_seeds, policies):
    tasks = [('mass', s, policies) for s in mass_seeds]
    tasks += [('mix', s, policies) for s in mix_seeds]
    tasks += [(f'family:{f}', s, policies) for f in scenarios.original.FAMILY_NAMES for s in family_seeds]
    return tasks


def evaluate(results, policies):
    groups = dict(mix=[r for r in results if r['kind'] == 'mix'], mass=[r for r in results if r['kind'] == 'mass'])
    fam = [r for r in results if r['kind'] not in groups]
    report = dict(groups={})
    for g, rs in groups.items():
        rows = {}
        for p in policies:
            row = dict(metrics=base.policy_metrics(rs, arm(p)))
            if p != FOCUS:
                row['versus_base'] = base.paired(rs, arm(p), arm(FOCUS))
            rows[p] = row
        report['groups'][g] = rows
    report['by_arrival'] = {}
    for g, rs in groups.items():
        for a in sorted({r['mix']['arrival'] for r in rs}):
            subset = [r for r in rs if r['mix']['arrival'] == a]
            report['by_arrival'][f'{g}:{a}'] = {p: base.paired(subset, arm(p), arm(FOCUS)) for p in policies if p != FOCUS}
    report['families'] = base.family_table(fam, [arm(p) for p in policies])
    return report


def select(report):
    g = report['groups']
    delta = lambda k, c: g[k][c]['versus_base']['delta_J_mean']
    feasible = [c for c in CANDIDATES if all(delta(k, c) <= TOLERANCE for k in ('mix', 'mass'))]
    if not feasible:
        return dict(policy=None, proceed=False, feasible=[])
    mean = lambda c: (delta('mix', c) + delta('mass', c)) / 2
    best = min(feasible, key=lambda c: (round(mean(c), 9), CANDIDATES.index(c)))
    return dict(policy=best, proceed=mean(best) < 0, feasible=feasible, mean_delta_J=mean(best),
                delta_J={k: delta(k, best) for k in ('mix', 'mass')})


def verdict(report, chosen):
    g = report['groups']
    rows = {k: g[k][chosen]['versus_base'] for k in ('mix', 'mass')}
    noninferior = all(v['ci95'][1] < MARGIN for v in rows.values())
    better = any(v['ci95'][1] < 0 for v in rows.values())
    return dict(policy=chosen, noninferior=noninferior, better=better, adopted=noninferior and better,
                delta_J={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in rows.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    policies = report['policies']
    lines = [f"# 마감을 아는 EAT-K {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'운영 구성(모든 정책 공통): `{RULE}`', '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f'**선택 규칙:** {SELECTION_RULE}', '', f"- 선택 {s['policy']} → 검증 진행 {s['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '', f"- {v['policy']} → 채택 후보 **{v['adopted']}**"]
        for k, row in v['delta_J'].items():
            lines.append(f"- {k}: ΔJ {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
        lines.append('')
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for g, rows in report['groups'].items():
        lines += [f'## {names[g]}', '', '| 정책 | J(초) | 실패 | 피할 수 있던 실패 | ΔJ vs 기준 [95% 구간] | 환자당 문의 | 검토 업무(초/환자) |',
                  '|---|---:|---:|---:|---|---:|---:|']
        for p in policies:
            row, m = rows[p], rows[p]['metrics']
            v = row.get('versus_base')
            delta = f"{_f(v['delta_J_mean'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}]" if v else '—'
            lines.append(f"| {p} | {_f(m['J'])} | {m['failed']} | {m['avoidable_failed']} | {delta} | "
                         f"{_f(m['inquiries_per_patient'], 2)} | {_f(m['review_work_seconds_per_patient'])} |")
        lines.append('')
    lines += [f'## 도착 형태별 ΔJ(후보 − {FOCUS})', '', '| 묶음 | 정책 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for key, by_policy in report['by_arrival'].items():
        for p, v in by_policy.items():
            lines.append(f"| {key} | {p} | {_f(v['delta_J_mean'])} | [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 확정 대기 규칙의 확률 계산은 2차 모형을 쓴다.',
        '- 첫 문의 시각은 실험기 안에서 서버 워크플로의 문의 목록 읽기를 감싸 얻는다. 서버에는 탐색 시작 시각을 정책에 넘기는 변경이 필요하다.',
        '- 피할 수 있던 실패는 혼자라면 받아 줄 병원이 있던 환자의 실패다. 여러 환자가 몰리면 모두 피할 수는 없다.']
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
    results, elapsed = run_all(_tasks(DEV_MIX, DEV_MASS, DEV_FAMILY, POLICIES), workers)
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rule=RULE,
                  selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], mass=[DEV_MASS[0], DEV_MASS[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, POLICIES))
    report['selection'] = select(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_deadline_20261026', rule=RULE, selection=report['selection'],
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
        raise RuntimeError('development selected no improving policy; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    if lock['rule'] != RULE:
        raise RuntimeError('operating rule changed after development lock')
    chosen = lock['selection']['policy']
    policies = (FOCUS, chosen)
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_MASS, VAL_FAMILY, policies), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(policies), rule=RULE,
                  selected_policy=chosen, lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], mass=[VAL_MASS[0], VAL_MASS[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, policies))
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
        for g, rows in report['groups'].items():
            for p, row in rows.items():
                v = row.get('versus_base')
                print(g, p, round(row['metrics']['J'], 1), None if v is None else round(v['delta_J_mean'], 1),
                      row['metrics']['failed'], round(row['metrics']['inquiries_per_patient'], 2))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
