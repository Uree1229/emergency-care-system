"""4차 2단계: '확인 중' 신호를 해석하는 EAT-C의 개발 선택 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_ack development --output simulation_results/inquiry_lab_ack_development_20261021
python -m inquiry_lab.experiment_ack validation --lock simulation_results/inquiry_lab_ack_development_20261021_lock.json \
    --output simulation_results/inquiry_lab_ack_validation_20261021

사전 고정:
- 1단계 진단(simulation_results/inquiry_lab_voi_20261021)에서 무응답 완전 정보 상한이 −28.8초/환자로
  진행 조건(≤ −10초, 구간 상한 < 0)을 통과해 진행한다.
- 새 seed만 사용: 개발 혼합 950001–950240·8유형 960001–960003, 검증 혼합 970001–970400·8유형 980001–980006.
- 분기(정책|확인 중 환경): fixed_3|on, v3_w45_h900|off, v3_w45_h900|on, eat_l15_f|off, eat_l15_f|on,
  eat_l15_f|perfect, eatc_k5|on, eatc_k20|on, eatc_k5|perfect, eato_silence|off(사전 무응답 완전 정보 상한).
  perfect는 '회신할 문의는 검토 시작 즉시 반드시 누르고 무응답 문의는 누르지 않는' 사후 정보 상한 환경이다.
- 질문: "확인 중 버튼을 도입하고 정책이 이를 해석하면, 버튼이 없는 지금(EAT|off)보다 나은가."
  개발 탐색(개발 seed 60개)에서 기존 EAT가 확인 중을 회신으로 오인해 나빠지는 것을 확인해, 오인하는
  EAT|on이 아니라 버튼 없는 EAT|off를 기준으로 정식 실행 전에 기준을 고정했다.
- 선택: 혼합 개발에서 EAT-C|on 중 (J < EAT|off) ∧ (실패 ≤ EAT|off) ∧ (문의·검토 업무 ≤ fixed_3|on)인 후보의 J 최소
  (동률이면 작은 κ). 없으면 검증 없이 미채택.
- 판정: 검증 혼합에서 ΔJ(EAT-C|on − EAT|off)의 부트스트랩 95% 구간 상한 < 0이면 개선.
  EAT|on·V3|on − 각 off(해석 없이 도입한 영향), perfect 분기(사후 정보 상한), 누름 비율별 결과는 판정 없이 보고.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import ack_environment, eat_ack_policy, eat_oracle_policy, experiment as base, harness, scenarios

DEV_MIX = tuple(range(950001, 950241))
DEV_FAMILY = (960001, 960002, 960003)
VAL_MIX = tuple(range(970001, 970401))
VAL_FAMILY = (980001, 980002, 980003, 980004, 980005, 980006)
CANDIDATES = tuple(f'{c}|on' for c in eat_ack_policy.variant_grid())
REFERENCE = 'eato_silence|off'
DEV_ARMS = (('fixed_3', 'on'), ('v3_w45_h900', 'off'), ('v3_w45_h900', 'on'), ('eat_l15_f', 'off'),
            ('eat_l15_f', 'on'), ('eat_l15_f', 'perfect')) + \
    tuple((c, 'on') for c in eat_ack_policy.variant_grid()) + (('eatc_k5', 'perfect'), ('eato_silence', 'off'))
SOURCE_FILES = base.SOURCE_FILES + ('inquiry_lab/public_signal.py', 'inquiry_lab/eat_public_policy.py',
                                    'inquiry_lab/eat_oracle_policy.py', 'inquiry_lab/ack_environment.py',
                                    'inquiry_lab/eat_ack_policy.py', 'inquiry_lab/experiment_ack.py')
SELECTION_RULE = ('혼합 개발에서 EAT-C|on 중 J가 버튼 없는 EAT|off보다 낮고 실패가 EAT|off 이하이며 환자당 문의·검토 '
                  '업무가 fixed_3|on 이하인 후보의 J 최소(동률이면 작은 κ). 없으면 검증 없이 미채택.')
SUCCESS_RULE = '검증 혼합에서 ΔJ(EAT-C|on − EAT|off)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0.'
LIMITATIONS = base.LIMITATIONS + [
    "'확인 중' 누름 비율(30·60·90%), 무응답 병원의 누름(10%·상대), 누르는 시각(검토 시작 후 0~10초)은 설계 가정이다.",
    '서버 관측 장부는 첫 회신만 기록하므로, 확인 중을 누른 문의의 최종 수용·불가와 처리시간은 정책이 학습에 쓸 수 없다.',
]


def arm_name(policy, mode):
    return f'{policy}|{mode}'


def run_case(task):
    kind, seed, arms = task
    eat_oracle_policy.install()
    eat_ack_policy.install()
    scenario = harness.build(kind, seed)
    eat_oracle_policy.set_scenario(scenario['potentials'])
    try:
        result = ack_environment.run_scenario(scenario, arms)
    finally:
        eat_oracle_policy.clear()
    branches = {}
    for b in result['branches']:
        row = harness.compact(b)
        row.update(mode=b['mode'], checking_count=b['metrics']['checking_count'])
        branches[arm_name(b['policy'], b['mode'])] = row
    mix = dict(scenario['mix'], ack_rate=result['ack_rate']) if scenario.get('mix') else None
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'], mix=mix,
                ack_rate=result['ack_rate'], environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario), branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def evaluate(results, names, compare):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] != 'mix']
    subgroups = base.subgroups(mix, names)
    for rate in ack_environment.ACK_RATES:
        subset = [r for r in mix if r['ack_rate'] == rate]
        if subset:
            subgroups[f'ack_rate={rate}'] = {n: base.policy_metrics(subset, n) for n in names}
    report = dict(mix=dict(metrics={n: base.policy_metrics(mix, n) for n in names},
                           sensitivity={str(k): {n: base.policy_metrics(mix, n, k)['J'] for n in names}
                                        for k in base.PENALTIES},
                           lower_bound=base.lower_bound(mix), subgroups=subgroups,
                           checking_per_patient={n: sum(r['branches'][n]['checking_count'] for r in mix)
                                                 / sum(r['patient_count'] for r in mix) for n in names}),
                  families=base.family_table(fam, names))
    report['paired'] = [base.paired(mix, a, b, k) for a, b in compare for k in base.PENALTIES]
    report['paired_by_ack_rate'] = {
        str(rate): [base.paired([r for r in mix if r['ack_rate'] == rate], a, b) for a, b in compare]
        for rate in ack_environment.ACK_RATES if any(r['ack_rate'] == rate for r in mix)}
    return report


def select(metrics):
    ref, cap = metrics['eat_l15_f|off'], metrics['fixed_3|on']
    feasible = [c for c in CANDIDATES
                if metrics[c]['J'] < ref['J'] and metrics[c]['failed'] <= ref['failed']
                and metrics[c]['inquiries_per_patient'] <= cap['inquiries_per_patient']
                and metrics[c]['review_work_seconds_per_patient'] <= cap['review_work_seconds_per_patient']]
    if not feasible:
        return dict(arm=None, policy=None, adopted_for_validation=False, feasible=[])
    best = min(feasible, key=lambda c: (round(metrics[c]['J'], 9), eat_ack_policy.spec(c.split('|')[0])['config']['silent_ack_relative']))
    return dict(arm=best, policy=best.split('|')[0], adopted_for_validation=True, feasible=feasible)


def markdown(report):
    names = report['arms']
    lines = [f"# '확인 중' 신호 EAT-C {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        lines += [f"**선택:** {report['selection']['arm'] or '없음(검증 미실행)'}", '', f'선택 규칙: {SELECTION_RULE}', '']
    else:
        lines += [f"**잠금 선택:** {report['selected_arm']}", '', f'판정 규칙: {SUCCESS_RULE}', '']
    lines += ['## 보정 혼합', '', base.metrics_table(report['mix']['metrics'], names, report['mix']['lower_bound']), '',
              '## 짝지은 비교', '', base.paired_table(report['paired']), '', '## 누름 비율별 ΔJ(실패 비용 1500초)', '',
              '| 누름 비율 | 비교 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for rate, rows in report['paired_by_ack_rate'].items():
        for r in rows:
            lines.append(f"| {rate} | {r['policy']} − {r['baseline']} | {r['delta_J_mean']:.1f} | "
                         f"[{r['ci95'][0]:.1f}, {r['ci95'][1]:.1f}] |")
    fam = report['families']
    lines += ['', '## 기존 8유형(스트레스 시험)', '', '| 유형 | ' + ' | '.join(names) + ' |', '|---|' + '---:|' * len(names)]
    for f, vals in fam['by_family'].items():
        lines.append(f'| {f} | ' + ' | '.join(f"{vals[n]['J']:.0f}" for n in names) + ' |')
    lines.append('| 유형 균등 | ' + ' | '.join(f"{fam['family_equal'][n]['J']:.1f}" for n in names) + ' |')
    lines += ['', '## 한계', ''] + [f'- {x}' for x in LIMITATIONS]
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


def _compare(chosen):
    return [(chosen, 'eat_l15_f|off'), (chosen, 'eat_l15_f|on'), ('eat_l15_f|on', 'eat_l15_f|off'),
            (chosen, 'fixed_3|on'), (chosen, 'v3_w45_h900|on'), (REFERENCE, 'eat_l15_f|off')]


def development(output, workers):
    output = Path(output).resolve()
    hashes = source_hashes()
    tasks = [('mix', s, DEV_ARMS) for s in DEV_MIX]
    tasks += [(f'family:{f}', s, DEV_ARMS) for f in scenarios.original.FAMILY_NAMES for s in DEV_FAMILY]
    results, elapsed = run_all(tasks, workers)
    names = [arm_name(*a) for a in DEV_ARMS]
    compare = [(c, 'eat_l15_f|off') for c in CANDIDATES] + [(c, 'eat_l15_f|on') for c in CANDIDATES] + [
        ('eat_l15_f|on', 'eat_l15_f|off'), ('eat_l15_f|perfect', 'eat_l15_f|off'), ('eatc_k5|perfect', 'eat_l15_f|off'),
        ('v3_w45_h900|on', 'v3_w45_h900|off'), (REFERENCE, 'eat_l15_f|off')]
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, arms=names,
                  selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  limitations=LIMITATIONS, results=results, **evaluate(results, names, compare))
    report['selection'] = select(report['mix']['metrics'])
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_ack_20261021', selection=report['selection'],
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
        raise RuntimeError('development selected no EAT-C candidate; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['arm']
    arms = (('fixed_3', 'on'), ('v3_w45_h900', 'on'), ('eat_l15_f', 'off'), ('eat_l15_f', 'on'),
            (lock['selection']['policy'], 'on'), ('eato_silence', 'off'))
    tasks = [('mix', s, arms) for s in VAL_MIX]
    tasks += [(f'family:{f}', s, arms) for f in scenarios.original.FAMILY_NAMES for s in VAL_FAMILY]
    results, elapsed = run_all(tasks, workers)
    names = [arm_name(*a) for a in arms]
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, arms=names, selected_arm=chosen,
                  lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE, seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  limitations=LIMITATIONS, results=results, **evaluate(results, names, _compare(chosen)))
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
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        for row in report['paired']:
            print(row['policy'], '-', row['baseline'], row['penalty'], round(row['delta_J_mean'], 1),
                  [round(x, 1) for x in row['ci95']])


if __name__ == '__main__':
    main()
