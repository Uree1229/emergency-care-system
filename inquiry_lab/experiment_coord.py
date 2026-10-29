"""7차 실험: 여러 환자 동시 계획 — 병원당 동시 미처리 문의 상한(EAT-J). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_coord development --output simulation_results/inquiry_lab_coord_development_20261021
python -m inquiry_lab.experiment_coord validation --lock simulation_results/inquiry_lab_coord_development_20261021_lock.json \
    --output simulation_results/inquiry_lab_coord_validation_20261021

사전 고정:
- 환경: 1차와 같은 보정 혼합. 새 seed만 사용: 개발 1080001–1080240·8유형 1100001–1100003,
  검증 1090001–1090400·8유형 1110001–1110006.
- 분기: fixed_3, v3_w45_h900, eat_l15_f(1차 잠금 기준), eat_l45_f(6차 권장, 참고), eatj_c1·c2·c3(후보).
- 진단(개발만): 같은 혼합 시나리오에서 병원 처리 인원을 99명으로 둔 가상 환경(경합 상한)과
  모든 병원의 문의 담당을 최소 2명으로 늘린 환경의 EAT λ15를 함께 돌려 seed 단위로 짝지어 보고한다.
  선택에는 쓰지 않는다.
- 선택: 혼합 개발에서 (J < EAT λ15) ∧ (실패 ≤ EAT λ15) ∧ (문의·검토 업무 ≤ fixed_3)인 EAT-J의 J 최소
  (동률이면 큰 상한). 없으면 검증 없이 미채택.
- 판정: 검증 혼합에서 ΔJ(EAT-J − EAT λ15)의 부트스트랩 95% 구간 상한 < 0. 도착 형태(단일·동시·순차)별 결과는 판정 없이 보고.
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

from server import inquiry_experiment

from . import eat_coord_policy, experiment as base, harness, scenarios

DEV_MIX = tuple(range(1080001, 1080241))
DEV_FAMILY = (1100001, 1100002, 1100003)
VAL_MIX = tuple(range(1090001, 1090401))
VAL_FAMILY = (1110001, 1110002, 1110003, 1110004, 1110005, 1110006)
BASE_POLICY = 'eat_l15_f'
CANDIDATES = eat_coord_policy.variant_grid()
DEV_POLICIES = ('fixed_3', 'v3_w45_h900', BASE_POLICY, 'eat_l45_f') + CANDIDATES
INFINITE_WORKERS = 99
SOURCE_FILES = base.SOURCE_FILES + ('inquiry_lab/eat_coord_policy.py', 'inquiry_lab/experiment_coord.py')
SELECTION_RULE = ('혼합 개발에서 J가 EAT λ15보다 낮고 실패가 EAT λ15 이하이며 환자당 문의·검토 업무가 fixed_3 이하인 '
                  'EAT-J의 J 최소(동률이면 큰 상한). 없으면 검증 없이 미채택.')
SUCCESS_RULE = '검증 혼합에서 ΔJ(EAT-J − EAT λ15)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0.'


def run_case(task):
    kind, seed, policies = task
    eat_coord_policy.install()
    if kind in ('mix_inf', 'mix_w2'):
        scenario = scenarios.make_mix(seed)
        for h in scenario['hospitals']:
            h['workers'] = INFINITE_WORKERS if kind == 'mix_inf' else max(2, h['workers'])
        scenario['id'] += '-infinite-workers' if kind == 'mix_inf' else '-two-workers'
    else:
        scenario = harness.build(kind, seed)
    result = inquiry_experiment.run_scenario(scenario, policies=tuple(policies))
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={b['policy']: harness.compact(b) for b in result['branches']})


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def congestion_headroom(mix, inf):
    """같은 seed의 일반 환경과 처리 인원 99명 환경에서 EAT λ15의 J 차이(환자당, 부트스트랩)."""
    by_seed = {r['seed']: r for r in inf}
    rows = []
    for r in mix:
        a = by_seed[r['seed']]['branches'][BASE_POLICY]['patients']
        b = r['branches'][BASE_POLICY]['patients']
        rows.append((sum(harness.patient_cost(p) for p in a) - sum(harness.patient_cost(p) for p in b), len(b)))
    total = sum(n for _, n in rows)
    mean = sum(d for d, _ in rows) / total
    rng = random.Random(base.BOOTSTRAP_SEED)
    stats = sorted(sum(d for d, _ in s) / sum(n for _, n in s)
                   for s in ([rows[rng.randrange(len(rows))] for _ in rows] for _ in range(base.BOOTSTRAP_RESAMPLES)))
    return dict(delta_J_mean=mean, ci95=[stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats)) - 1]],
                note='처리 인원 99명 가상 환경 − 실제 환경. 음수일수록 대기열·경합 손실이 크다.')


def evaluate(results, policies, compare):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] not in ('mix', 'mix_inf', 'mix_w2')]
    inf = [r for r in results if r['kind'] == 'mix_inf']
    two = [r for r in results if r['kind'] == 'mix_w2']
    report = dict(mix=dict(metrics={p: base.policy_metrics(mix, p) for p in policies},
                           sensitivity={str(k): {p: base.policy_metrics(mix, p, k)['J'] for p in policies}
                                        for k in base.PENALTIES},
                           lower_bound=base.lower_bound(mix), subgroups=base.subgroups(mix, list(policies))),
                  families=base.family_table(fam, list(policies)))
    report['paired'] = [base.paired(mix, a, b, k) for a, b in compare for k in base.PENALTIES]
    report['paired_by_arrival'] = {
        a: [base.paired([r for r in mix if r['mix']['arrival'] == a], p, q) for p, q in compare]
        for a in ('single', 'burst', 'stream') if any(r['mix']['arrival'] == a for r in mix)}
    if inf:
        report['congestion_headroom'] = congestion_headroom(mix, inf)
        report['congestion_headroom_by_arrival'] = {
            a: congestion_headroom([r for r in mix if r['mix']['arrival'] == a], inf)
            for a in ('single', 'burst', 'stream') if any(r['mix']['arrival'] == a for r in mix)}
    if two:
        report['two_worker_effect'] = congestion_headroom(mix, two)
        report['two_worker_effect_by_arrival'] = {
            a: congestion_headroom([r for r in mix if r['mix']['arrival'] == a], two)
            for a in ('single', 'burst', 'stream') if any(r['mix']['arrival'] == a for r in mix)}
    return report


def select(metrics):
    ref, cap = metrics[BASE_POLICY], metrics['fixed_3']
    feasible = [c for c in CANDIDATES
                if metrics[c]['J'] < ref['J'] and metrics[c]['failed'] <= ref['failed']
                and metrics[c]['inquiries_per_patient'] <= cap['inquiries_per_patient']
                and metrics[c]['review_work_seconds_per_patient'] <= cap['review_work_seconds_per_patient']]
    if not feasible:
        return dict(policy=None, adopted_for_validation=False, feasible=[])
    best = min(feasible, key=lambda c: (round(metrics[c]['J'], 9), -eat_coord_policy.spec(c)['config']['hospital_inflight_cap']))
    return dict(policy=best, adopted_for_validation=True, feasible=feasible)


def markdown(report):
    pols = report['policies']
    lines = [f"# 여러 환자 동시 계획(EAT-J) {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        lines += [f"**선택:** {report['selection']['policy'] or '없음(검증 미실행)'}", '', f'선택 규칙: {SELECTION_RULE}', '']
        h = report.get('congestion_headroom')
        if h:
            lines += [f"**경합 상한(진단):** 처리 인원 99명 가상 환경의 EAT − 실제 EAT = {h['delta_J_mean']:.1f}초/환자 "
                      f"[{h['ci95'][0]:.1f}, {h['ci95'][1]:.1f}]", '']
            for a, row in report['congestion_headroom_by_arrival'].items():
                lines.append(f"- {a}: {row['delta_J_mean']:.1f}초 [{row['ci95'][0]:.1f}, {row['ci95'][1]:.1f}]")
            lines.append('')
        w = report.get('two_worker_effect')
        if w:
            lines += [f"**병원 문의 담당 최소 2명(진단):** {w['delta_J_mean']:.1f}초/환자 [{w['ci95'][0]:.1f}, {w['ci95'][1]:.1f}]", '']
            for a, row in report['two_worker_effect_by_arrival'].items():
                lines.append(f"- {a}: {row['delta_J_mean']:.1f}초 [{row['ci95'][0]:.1f}, {row['ci95'][1]:.1f}]")
            lines.append('')
    else:
        lines += [f"**잠금 선택:** {report['selected_policy']}", '', f'판정 규칙: {SUCCESS_RULE}', '']
    lines += ['## 보정 혼합', '', base.metrics_table(report['mix']['metrics'], pols, report['mix']['lower_bound']), '',
              '## 짝지은 비교', '', base.paired_table(report['paired']), '', '## 도착 형태별 ΔJ(실패 비용 1500초)', '',
              '| 도착 | 비교 | ΔJ | 95% 구간 | 환자 개선/동일/악화 |', '|---|---|---:|---|---|']
    for a, rows in report['paired_by_arrival'].items():
        for r in rows:
            lines.append(f"| {a} | {r['policy']} − {r['baseline']} | {r['delta_J_mean']:.1f} | "
                         f"[{r['ci95'][0]:.1f}, {r['ci95'][1]:.1f}] | {r['patients']['better']}/{r['patients']['same']}/{r['patients']['worse']} |")
    fam = report['families']
    lines += ['', '## 기존 8유형(스트레스 시험)', '', '| 유형 | ' + ' | '.join(pols) + ' |', '|---|' + '---:|' * len(pols)]
    for f, vals in fam['by_family'].items():
        lines.append(f'| {f} | ' + ' | '.join(f"{vals[p]['J']:.0f} / {vals[p]['inquiries_per_patient']:.2f}" for p in pols) + ' |')
    lines.append('| 유형 균등 | ' + ' | '.join(f"{fam['family_equal'][p]['J']:.1f}" for p in pols) + ' |')
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS] + [
        '- 병원 처리 인원 99명 환경은 경합 손실의 크기를 재기 위한 가상 비교이며 실제 운영 조건이 아니다.']
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
    tasks = [('mix', s, DEV_POLICIES) for s in DEV_MIX]
    tasks += [('mix_inf', s, (BASE_POLICY,)) for s in DEV_MIX]
    tasks += [('mix_w2', s, (BASE_POLICY,)) for s in DEV_MIX]
    tasks += [(f'family:{f}', s, DEV_POLICIES) for f in scenarios.original.FAMILY_NAMES for s in DEV_FAMILY]
    results, elapsed = run_all(tasks, workers)
    compare = [(c, BASE_POLICY) for c in CANDIDATES] + [('eat_l45_f', BASE_POLICY)] + [(c, 'fixed_3') for c in CANDIDATES]
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(DEV_POLICIES),
                  selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, DEV_POLICIES, compare))
    report['selection'] = select(report['mix']['metrics'])
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_coord_20261021', selection=report['selection'],
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
        raise RuntimeError('development selected no EAT-J candidate; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['policy']
    policies = ('fixed_3', 'v3_w45_h900', BASE_POLICY, 'eat_l45_f', chosen)
    tasks = [('mix', s, policies) for s in VAL_MIX]
    tasks += [(f'family:{f}', s, policies) for f in scenarios.original.FAMILY_NAMES for s in VAL_FAMILY]
    results, elapsed = run_all(tasks, workers)
    compare = [(chosen, BASE_POLICY), (chosen, 'fixed_3'), (chosen, 'v3_w45_h900'), (chosen, 'eat_l45_f')]
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(policies),
                  selected_policy=chosen, lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE, seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, policies, compare))
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
        print('headroom', report.get('congestion_headroom'))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        for row in report['paired']:
            print(row['policy'], '-', row['baseline'], row['penalty'], round(row['delta_J_mean'], 1),
                  [round(x, 1) for x in row['ci95']])


if __name__ == '__main__':
    main()
