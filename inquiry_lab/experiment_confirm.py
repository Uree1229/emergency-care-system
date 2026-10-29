"""2차 실험: 확정 대기(hold) 규칙. 문의 정책 × 확정 규칙의 요인 설계로 효과를 분리한다.

python -m inquiry_lab.experiment_confirm development --output simulation_results/inquiry_lab_confirm_development_20261020
python -m inquiry_lab.experiment_confirm validation --lock simulation_results/inquiry_lab_confirm_development_20261020_lock.json \
    --output simulation_results/inquiry_lab_confirm_validation_20261020

사전 고정:
- 문의 정책 {fixed_3, eat_l15_f(1차 잠금 선택), v3_w45_h900} × 확정 {immediate, hold30, hold60, hold120}.
- 새 seed만 사용: 개발 혼합 850001–850240·8유형 870001–870003, 검증 혼합 860001–860400·8유형 880001–880006.
- 선택: 혼합 개발에서 eat+holdτ 중 (J < eat 즉시) ∧ (실패 ≤ eat 즉시) ∧ (환자당 검토 업무 ≤ fixed_3 즉시)인
  후보의 J 최소(동률이면 짧은 τ). 없으면 검증을 실행하지 않고 미채택으로 기록한다.
- 판정: 검증 혼합에서 ΔJ(eat+holdτ − eat 즉시)의 부트스트랩 95% 구간 상한 < 0이면 개선으로 본다.
1차 실험 파일(eat_policy·harness·scenarios·experiment)은 수정하지 않고 import만 한다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import confirm_policy, eat_policy, experiment as base, harness, runner, scenarios

INQUIRY_POLICIES = ('fixed_3', 'eat_l15_f', 'v3_w45_h900')
PRIMARY = 'eat_l15_f'
RULES = ('immediate',) + tuple(confirm_policy.rule_name(h) for h in confirm_policy.HOLD_GRID)
DEV_MIX = tuple(range(850001, 850241))
DEV_FAMILY = (870001, 870002, 870003)
VAL_MIX = tuple(range(860001, 860401))
VAL_FAMILY = (880001, 880002, 880003, 880004, 880005, 880006)
SOURCE_FILES = base.SOURCE_FILES + ('inquiry_lab/confirm_policy.py', 'inquiry_lab/runner.py',
                                    'inquiry_lab/experiment_confirm.py')
SELECTION_RULE = ('혼합 개발에서 eat+holdτ 중 J가 eat 즉시보다 낮고, 실패가 eat 즉시 이하이며, 환자당 병원 검토 '
                  '업무가 fixed_3 즉시 이하인 후보의 J 최소(동률이면 짧은 τ). 없으면 검증 없이 미채택.')
SUCCESS_RULE = '검증 혼합에서 ΔJ(eat+holdτ − eat 즉시)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0.'


def arm(policy, rule):
    return f'{policy}|{rule}'


def run_case(task):
    kind, seed, arms = task
    harness.install([p for p, _ in arms if p.startswith(eat_policy.POLICY_PREFIX)])
    scenario = harness.build(kind, seed)
    result = runner.run_scenario(scenario, arms)
    branches = {}
    for b in result['branches']:
        row = harness.compact(b)
        row.update(rule=b['rule'], holds=len(b['holds']),
                   holds_switched=sum(h['switched'] for h in b['holds']),
                   held_seconds=sum(h['ended_at'] - h['started_at'] for h in b['holds']))
        branches[arm(b['policy'], b['rule'])] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def hold_stats(results, name):
    rows = [r['branches'][name] for r in results]
    patients = sum(len(b['patients']) for b in rows)
    return dict(holds=sum(b['holds'] for b in rows), switched=sum(b['holds_switched'] for b in rows),
                held_seconds_per_patient=sum(b['held_seconds'] for b in rows) / patients)


def evaluate(results, arms, compare):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] != 'mix']
    names = [arm(p, r) for p, r in arms]
    report = dict(mix=dict(metrics={n: base.policy_metrics(mix, n) for n in names},
                           holds={n: hold_stats(mix, n) for n in names},
                           sensitivity={str(k): {n: base.policy_metrics(mix, n, k)['J'] for n in names}
                                        for k in base.PENALTIES},
                           lower_bound=base.lower_bound(mix),
                           subgroups=base.subgroups(mix, names)),
                  families=base.family_table(fam, names))
    report['paired'] = [base.paired(mix, a, b, k) for a, b in compare for k in base.PENALTIES]
    return report


def select(metrics):
    ref = metrics[arm(PRIMARY, 'immediate')]
    work_cap = metrics[arm('fixed_3', 'immediate')]['review_work_seconds_per_patient']
    candidates = [arm(PRIMARY, r) for r in RULES if r != 'immediate']
    feasible = [c for c in candidates
                if metrics[c]['J'] < ref['J'] and metrics[c]['failed'] <= ref['failed']
                and metrics[c]['review_work_seconds_per_patient'] <= work_cap]
    if not feasible:
        return dict(arm=None, rule=None, adopted_for_validation=False, feasible=[])
    best = min(feasible, key=lambda c: (round(metrics[c]['J'], 9), confirm_policy.parse_rule(c.split('|')[1])))
    return dict(arm=best, rule=best.split('|')[1], adopted_for_validation=True, feasible=feasible)


def markdown(report):
    names = report['arms']
    lines = [f"# 확정 대기 규칙 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        s = report['selection']
        lines += [f"**선택:** {s['arm'] or '없음(검증 미실행)'}", '', f'선택 규칙: {SELECTION_RULE}', '']
    else:
        lines += [f"**잠금 선택:** {report['selected_rule']}", '', f'판정 규칙: {SUCCESS_RULE}', '']
    m, h = report['mix']['metrics'], report['mix']['holds']
    lines += ['## 보정 혼합', '', '| 분기 | J | 실패 | 피할 수 있던 실패 | 환자당 문의 | 환자당 검토 업무 | 대기 횟수 | 더 가까운 병원으로 바뀜 | 환자당 대기초 |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for n in names:
        x = m[n]
        lines.append(f"| {n} | {x['J']:.1f} | {x['failed']} | {x['avoidable_failed']} | {x['inquiries_per_patient']:.2f} | "
                     f"{x['review_work_seconds_per_patient']:.1f} | {h[n]['holds']} | {h[n]['switched']} | {h[n]['held_seconds_per_patient']:.1f} |")
    lines += ['', f"완전정보 하한 J {report['mix']['lower_bound']:.1f}", '', '## 짝지은 비교', '', base.paired_table(report['paired']), '']
    fam = report['families']['family_equal']
    lines += ['## 기존 8개 유형(스트레스 시험, 유형 균등)', '', '| 분기 | J | 선정(제한) | 문의 |', '|---|---:|---:|---:|']
    for n in names:
        lines.append(f"| {n} | {fam[n]['J']:.1f} | {fam[n]['selection_capped_mean']:.1f} | {fam[n]['inquiries_per_patient']:.2f} |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS] + [
        '- 대기 중 수용 응답은 유효하다고 본다(서버 수용 유효시간 600초). 병원의 수용 철회나 준비 비용은 모형에 없다.']
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
    arms = [(p, r) for p in INQUIRY_POLICIES for r in RULES]
    tasks = [('mix', s, arms) for s in DEV_MIX]
    tasks += [(f'family:{f}', s, arms) for f in scenarios.original.FAMILY_NAMES for s in DEV_FAMILY]
    hashes = source_hashes()
    results, elapsed = run_all(tasks, workers)
    compare = [(arm(PRIMARY, r), arm(PRIMARY, 'immediate')) for r in RULES if r != 'immediate']
    compare += [(arm('fixed_3', r), arm('fixed_3', 'immediate')) for r in RULES if r != 'immediate']
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, arms=[arm(*a) for a in arms],
                  selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, arms, compare))
    report['selection'] = select(report['mix']['metrics'])
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_confirm_20261020', selection=report['selection'],
                source_sha256=hashes, development_report=base.relative_path(json_path),
                development_report_sha256=base.sha256_file(json_path),
                validation_seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE)
    lock_path = Path(str(output) + '_lock.json')
    if lock_path.exists():
        raise FileExistsError(f'{lock_path} already exists')
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding='utf-8')
    return json_path, md_path, lock_path, report


def validation(lock_path, output, workers):
    lock_path, output = Path(lock_path).resolve(), Path(output).resolve()
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if not lock['selection']['adopted_for_validation']:
        raise RuntimeError('development selected no hold rule; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    rule = lock['selection']['rule']
    arms = [(PRIMARY, 'immediate'), (PRIMARY, rule), ('fixed_3', 'immediate'), ('fixed_3', rule),
            ('v3_w45_h900', 'immediate')]
    tasks = [('mix', s, arms) for s in VAL_MIX]
    tasks += [(f'family:{f}', s, arms) for f in scenarios.original.FAMILY_NAMES for s in VAL_FAMILY]
    results, elapsed = run_all(tasks, workers)
    compare = [(arm(PRIMARY, rule), arm(PRIMARY, 'immediate')), (arm(PRIMARY, rule), arm('fixed_3', 'immediate')),
               (arm('fixed_3', rule), arm('fixed_3', 'immediate')), (arm(PRIMARY, rule), arm('v3_w45_h900', 'immediate'))]
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, arms=[arm(*a) for a in arms],
                  selected_rule=rule, lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE, seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, arms, compare))
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
        json_path, md_path, lock_path, report = development(args.output, args.workers)
        print('selection', report['selection'])
    else:
        json_path, md_path, report = validation(args.lock, args.output, args.workers)
        for row in report['paired']:
            print(row['policy'], '-', row['baseline'], row['penalty'], round(row['delta_J_mean'], 1),
                  [round(x, 1) for x in row['ci95']])


if __name__ == '__main__':
    main()
