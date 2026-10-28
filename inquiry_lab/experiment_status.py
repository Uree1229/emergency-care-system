"""5차 실험: '현재 문의 응대 가능/불가' 상태 표시를 해석하는 EAT-R. 개발 선택 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_status development --output simulation_results/inquiry_lab_status_development_20261021
python -m inquiry_lab.experiment_status validation --lock simulation_results/inquiry_lab_status_development_20261021_lock.json \
    --output simulation_results/inquiry_lab_status_validation_20261021

사전 고정:
- 환경: 상태 기반 무응답 혼합(status_signal.make_state_mix). 새 seed만 사용:
  개발 990001–990240·8유형 1030001–1030003, 검증 1010001–1010400·8유형 1020001–1020006.
- 보정: 개발 seed의 표시에서 P(회신 | 응대 가능), P(회신 | 응대 불가)를 세어 잠근다. pooled는 모든 품질 합산,
  trusted는 정확한 표시만으로 센 값이다(표시를 늘 정확하다고 믿는 설계).
- 분기: fixed_3, v3_w45_h900, eat_l15_f(1차 잠금), eatr_s4·eatr_s16(pooled), eatr_t16(trusted),
  eato_silence(무응답 완전 정보 상한, 비교 기준).
- 선택: 혼합 개발에서 (J < EAT) ∧ (실패 ≤ EAT) ∧ (문의·검토 업무 ≤ fixed_3)인 EAT-R의 J 최소
  (동률이면 pooled, 약한 강도 순). 없으면 검증 없이 미채택.
- 판정: 검증 혼합에서 ΔJ(EAT-R − EAT)의 부트스트랩 95% 구간 상한 < 0이면 개선. 표시 품질별 결과는 판정 없이 보고.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from server import inquiry_experiment

from . import eat_oracle_policy, eat_status_policy, experiment as base, harness, scenarios, status_signal

DEV_MIX = tuple(range(990001, 990241))
DEV_FAMILY = (1030001, 1030002, 1030003)
VAL_MIX = tuple(range(1010001, 1010401))
VAL_FAMILY = (1020001, 1020002, 1020003, 1020004, 1020005, 1020006)
CANDIDATES = eat_status_policy.variant_grid()
REFERENCE = 'eato_silence'
DEV_POLICIES = ('fixed_3', 'v3_w45_h900', 'eat_l15_f') + CANDIDATES + (REFERENCE,)
SOURCE_FILES = base.SOURCE_FILES + ('inquiry_lab/public_signal.py', 'inquiry_lab/eat_public_policy.py',
                                    'inquiry_lab/eat_oracle_policy.py', 'inquiry_lab/status_signal.py',
                                    'inquiry_lab/eat_status_policy.py', 'inquiry_lab/experiment_status.py')
SELECTION_RULE = ('혼합 개발에서 J가 EAT보다 낮고 실패가 EAT 이하이며 환자당 문의·검토 업무가 fixed_3 이하인 EAT-R의 '
                  'J 최소(동률이면 약한 강도). 없으면 검증 없이 미채택. 무응답 완전 정보 분기는 비교 기준이다.')
SUCCESS_RULE = '검증 혼합에서 ΔJ(EAT-R − EAT)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0.'
LIMITATIONS = base.LIMITATIONS + [
    '무응답을 병원 상태(시나리오 동안 고정)로 만든 환경이다. 상태가 문의 도중 바뀌는 경우는 다루지 않았다.',
    '상태 표시의 정확도(정확·30% 틀림·미사용·혼합)와 결측 10%는 설계 가정이다. 실제 병원의 표시 갱신 습관은 측정하지 않았다.',
]


def build(kind, seed):
    if kind == 'state':
        scenario, states = status_signal.make_state_mix(seed)
        return scenario, states, status_signal.status_signal(seed, states)
    scenario = harness.build(kind, seed)
    states = status_signal.family_states(scenario)
    return scenario, states, status_signal.status_signal(f"{scenario['id']}", states, quality='accurate')


def run_case(task):
    kind, seed, policies, calibration = task
    eat_oracle_policy.install()
    eat_status_policy.install()
    scenario, states, signal = build(kind, seed)
    eat_status_policy.set_context(status_signal.policy_view(signal), calibration)
    eat_oracle_policy.set_scenario(scenario['potentials'])
    try:
        result = inquiry_experiment.run_scenario(scenario, policies=tuple(policies))
    finally:
        eat_status_policy.clear_context()
        eat_oracle_policy.clear()
    mix = dict(scenario['mix'], status=signal['quality']) if kind == 'state' else None
    return dict(kind='mix' if kind == 'state' else kind, seed=seed, scenario_id=scenario['id'],
                family=scenario['family'], mix=mix, states=states, signal=signal,
                environment_sha256=result['environment_sha256'], patient_count=len(scenario['patients']),
                oracle=scenarios.oracle(scenario), branches={b['policy']: harness.compact(b) for b in result['branches']})


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def evaluate(results, policies, compare):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] != 'mix']
    groups = base.subgroups(mix, list(policies))
    for q in status_signal.QUALITIES:
        subset = [r for r in mix if r['mix']['status'] == q]
        if subset:
            groups[f'status={q}'] = {p: base.policy_metrics(subset, p) for p in policies}
    report = dict(mix=dict(metrics={p: base.policy_metrics(mix, p) for p in policies},
                           sensitivity={str(k): {p: base.policy_metrics(mix, p, k)['J'] for p in policies}
                                        for k in base.PENALTIES},
                           lower_bound=base.lower_bound(mix), subgroups=groups),
                  families=base.family_table(fam, list(policies)))
    report['paired'] = [base.paired(mix, a, b, k) for a, b in compare for k in base.PENALTIES]
    report['paired_by_status'] = {
        q: [base.paired([r for r in mix if r['mix']['status'] == q], a, b) for a, b in compare]
        for q in status_signal.QUALITIES if any(r['mix']['status'] == q for r in mix)}
    return report


def select(metrics):
    ref, cap = metrics['eat_l15_f'], metrics['fixed_3']
    feasible = [c for c in CANDIDATES
                if metrics[c]['J'] < ref['J'] and metrics[c]['failed'] <= ref['failed']
                and metrics[c]['inquiries_per_patient'] <= cap['inquiries_per_patient']
                and metrics[c]['review_work_seconds_per_patient'] <= cap['review_work_seconds_per_patient']]
    if not feasible:
        return dict(policy=None, adopted_for_validation=False, feasible=[])
    def order(c):
        config = eat_status_policy.spec(c)['config']
        return (round(metrics[c]['J'], 9), config['status_calibration'] == 'trusted', config['status_strength'])
    best = min(feasible, key=order)
    return dict(policy=best, adopted_for_validation=True, feasible=feasible)


def markdown(report):
    pols = report['policies']
    cal = report['calibration']
    lines = [f"# 응대 가능 상태 표시 EAT-R {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    for source, label in (('pooled', '모든 품질 합산'), ('trusted', '정확한 표시만')):
        c = cal[source]
        lines.append(f"보정({label}): P(회신 | 응대 가능) {c['available']['reply_probability']:.3f}"
                     f"(표본 {c['available']['samples']}), P(회신 | 응대 불가) {c['unavailable']['reply_probability']:.3f}"
                     f"(표본 {c['unavailable']['samples']})")
    lines.append('')
    if report['stage'] == 'development':
        lines += [f"**선택:** {report['selection']['policy'] or '없음(검증 미실행)'}", '', f'선택 규칙: {SELECTION_RULE}', '']
    else:
        lines += [f"**잠금 선택:** {report['selected_policy']}", '', f'판정 규칙: {SUCCESS_RULE}', '']
    lines += ['## 상태 기반 무응답 혼합', '', base.metrics_table(report['mix']['metrics'], pols, report['mix']['lower_bound']), '',
              '## 짝지은 비교', '', base.paired_table(report['paired']), '', '## 표시 품질별 ΔJ(실패 비용 1500초)', '',
              '| 품질 | 비교 | ΔJ | 95% 구간 | 환자 개선/동일/악화 |', '|---|---|---:|---|---|']
    for q, rows in report['paired_by_status'].items():
        for r in rows:
            lines.append(f"| {q} | {r['policy']} − {r['baseline']} | {r['delta_J_mean']:.1f} | "
                         f"[{r['ci95'][0]:.1f}, {r['ci95'][1]:.1f}] | {r['patients']['better']}/{r['patients']['same']}/{r['patients']['worse']} |")
    fam = report['families']
    lines += ['', '## 기존 8유형(정확한 표시, 스트레스 시험)', '', '| 유형 | ' + ' | '.join(pols) + ' |', '|---|' + '---:|' * len(pols)]
    for f, vals in fam['by_family'].items():
        lines.append(f'| {f} | ' + ' | '.join(f"{vals[p]['J']:.0f}" for p in pols) + ' |')
    lines.append('| 유형 균등 | ' + ' | '.join(f"{fam['family_equal'][p]['J']:.1f}" for p in pols) + ' |')
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


def development(output, workers):
    output = Path(output).resolve()
    hashes = source_hashes()
    calibration = status_signal.calibration(DEV_MIX)
    tasks = [('state', s, DEV_POLICIES, calibration) for s in DEV_MIX]
    tasks += [(f'family:{f}', s, DEV_POLICIES, calibration) for f in scenarios.original.FAMILY_NAMES for s in DEV_FAMILY]
    results, elapsed = run_all(tasks, workers)
    compare = [(c, 'eat_l15_f') for c in CANDIDATES] + [(c, 'fixed_3') for c in CANDIDATES] + [(REFERENCE, 'eat_l15_f')]
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(DEV_POLICIES),
                  calibration=calibration, selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  limitations=LIMITATIONS, results=results, **evaluate(results, DEV_POLICIES, compare))
    report['selection'] = select(report['mix']['metrics'])
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_status_20261021', selection=report['selection'],
                calibration=calibration, source_sha256=hashes, development_report=base.relative_path(json_path),
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
        raise RuntimeError('development selected no EAT-R candidate; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen, calibration = lock['selection']['policy'], lock['calibration']
    policies = ('fixed_3', 'v3_w45_h900', 'eat_l15_f', chosen, REFERENCE)
    tasks = [('state', s, policies, calibration) for s in VAL_MIX]
    tasks += [(f'family:{f}', s, policies, calibration) for f in scenarios.original.FAMILY_NAMES for s in VAL_FAMILY]
    results, elapsed = run_all(tasks, workers)
    compare = [(chosen, 'eat_l15_f'), (chosen, 'fixed_3'), (chosen, 'v3_w45_h900'), (REFERENCE, 'eat_l15_f')]
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(policies),
                  calibration=calibration, selected_policy=chosen, lock=base.relative_path(lock_path),
                  lock_sha256=base.sha256_file(lock_path), success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  limitations=LIMITATIONS, results=results, **evaluate(results, policies, compare))
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
        print('calibration', report['calibration'])
        print('selection', report['selection'])
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        for row in report['paired']:
            print(row['policy'], '-', row['baseline'], row['penalty'], round(row['delta_J_mean'], 1),
                  [round(x, 1) for x in row['ci95']])


if __name__ == '__main__':
    main()
