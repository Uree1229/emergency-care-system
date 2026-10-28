"""6차 실험: 같은 J에서 병원 문의 줄이기(비열등성). EAT의 문의비용 λ만 바꾼다.

python -m inquiry_lab.experiment_burden development --output simulation_results/inquiry_lab_burden_development_20261021
python -m inquiry_lab.experiment_burden validation --lock simulation_results/inquiry_lab_burden_development_20261021_lock.json \
    --output simulation_results/inquiry_lab_burden_validation_20261021

질문: J(도착 기준 기대 비용)를 사실상 유지하면서 병원 문의를 유의하게 줄일 수 있는가.

사전 고정:
- 환경: 1차와 같은 보정 혼합(무응답 독립). 새 seed만 사용:
  개발 혼합 1040001–1040240·8유형 1060001–1060003, 검증 혼합 1050001–1050400·8유형 1070001–1070006.
- 분기: fixed_1·3·5, v3_w45_h900, EAT λ 0·15(1차 잠금 기준)·25·35·45·60·90·120. 후보는 λ 25·35·45·60.
  λ 0·90·120은 J–문의 교환 곡선을 그리기 위한 참고 분기다.
- 비열등 여유 δ = +5초/환자(1차에서 EAT가 고정3 대비 얻은 20초 이득의 75% 이상 유지).
- 선택: 혼합 개발에서 ΔJ(후보 − EAT λ15) 평균 ≤ δ/2이고 문의 변화 평균 < 0인 후보 중 환자당 문의 최소(동률이면 작은 λ).
  없으면 검증 없이 미채택.
- 판정: 검증 혼합에서 ΔJ의 시나리오 단위 부트스트랩 95% 구간 상한 < δ이고,
  환자당 문의 변화의 95% 구간 상한 < 0이면 "J를 유지하며 문의를 줄였다"고 본다. 검토 업무·실패·민감도는 함께 보고.
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

from . import experiment as base, harness, scenarios

DEV_MIX = tuple(range(1040001, 1040241))
DEV_FAMILY = (1060001, 1060002, 1060003)
VAL_MIX = tuple(range(1050001, 1050401))
VAL_FAMILY = (1070001, 1070002, 1070003, 1070004, 1070005, 1070006)
BASE_POLICY = 'eat_l15_f'
CANDIDATES = ('eat_l25_f', 'eat_l35_f', 'eat_l45_f', 'eat_l60_f')
CURVE = ('eat_l0_f', 'eat_l90_f', 'eat_l120_f')
DEV_POLICIES = ('fixed_1', 'fixed_3', 'fixed_5', 'v3_w45_h900', BASE_POLICY) + CANDIDATES + CURVE
MARGIN_SECONDS = 5.0
SOURCE_FILES = base.SOURCE_FILES + ('inquiry_lab/experiment_burden.py',)
SELECTION_RULE = (f'혼합 개발에서 ΔJ(후보 − EAT λ15) 평균 ≤ {MARGIN_SECONDS / 2:g}초이고 환자당 문의 변화 평균 < 0인 후보 중 '
                  '환자당 문의 최소(동률이면 작은 λ). 없으면 검증 없이 미채택.')
SUCCESS_RULE = (f'검증 혼합에서 ΔJ 95% 구간 상한 < +{MARGIN_SECONDS:g}초(비열등)이고 환자당 문의 변화 95% 구간 상한 < 0.')


def paired_burden(results, policy, baseline, key):
    """환자당 문의(inquiry_count) 또는 검토 업무(review_work_seconds) 차이의 시나리오 단위 부트스트랩."""
    rows = []
    for r in results:
        a, b = r['branches'][policy], r['branches'][baseline]
        if key == 'inquiry_count':
            delta = sum(p['inquiry_count'] for p in a['patients']) - sum(p['inquiry_count'] for p in b['patients'])
        else:
            delta = a['review_work_seconds'] - b['review_work_seconds']
        rows.append((delta, len(a['patients'])))
    total = sum(n for _, n in rows)
    mean = sum(d for d, _ in rows) / total
    rng = random.Random(base.BOOTSTRAP_SEED)
    stats = []
    for _ in range(base.BOOTSTRAP_RESAMPLES):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        stats.append(sum(d for d, _ in sample) / sum(n for _, n in sample))
    stats.sort()
    low = stats[int(0.025 * base.BOOTSTRAP_RESAMPLES)]
    high = stats[int(0.975 * base.BOOTSTRAP_RESAMPLES) - 1]
    return dict(policy=policy, baseline=baseline, key=key, delta_mean=mean, ci95=[low, high])


def frontier(metrics, policies):
    """J와 환자당 문의가 모두 더 낮은 분기가 있으면 지배된다(파레토)."""
    out = {}
    for p in policies:
        dominated_by = [q for q in policies if q != p
                        and metrics[q]['J'] <= metrics[p]['J'] and metrics[q]['inquiries_per_patient'] <= metrics[p]['inquiries_per_patient']
                        and (metrics[q]['J'] < metrics[p]['J'] or metrics[q]['inquiries_per_patient'] < metrics[p]['inquiries_per_patient'])]
        out[p] = dict(J=metrics[p]['J'], inquiries_per_patient=metrics[p]['inquiries_per_patient'],
                      review_work_seconds_per_patient=metrics[p]['review_work_seconds_per_patient'],
                      dominated_by=dominated_by)
    return out


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(harness.run_case, tasks, chunksize=1))
    return results, time.time() - started


def evaluate(results, policies, compare):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] != 'mix']
    metrics = {p: base.policy_metrics(mix, p) for p in policies}
    report = dict(mix=dict(metrics=metrics, lower_bound=base.lower_bound(mix),
                           sensitivity={str(k): {p: base.policy_metrics(mix, p, k)['J'] for p in policies}
                                        for k in base.PENALTIES},
                           subgroups=base.subgroups(mix, list(policies)), frontier=frontier(metrics, list(policies))),
                  families=base.family_table(fam, list(policies)))
    report['paired'] = [base.paired(mix, a, b, k) for a, b in compare for k in base.PENALTIES]
    report['paired_burden'] = [paired_burden(mix, a, b, key) for a, b in compare
                               for key in ('inquiry_count', 'review_work_seconds')]
    return report


def select(report):
    j = {r['policy']: r for r in report['paired'] if r['baseline'] == BASE_POLICY and r['penalty'] == base.PRIMARY_PENALTY}
    q = {r['policy']: r for r in report['paired_burden'] if r['baseline'] == BASE_POLICY and r['key'] == 'inquiry_count'}
    metrics = report['mix']['metrics']
    feasible = [c for c in CANDIDATES if j[c]['delta_J_mean'] <= MARGIN_SECONDS / 2 and q[c]['delta_mean'] < 0]
    if not feasible:
        return dict(policy=None, adopted_for_validation=False, feasible=[])
    best = min(feasible, key=lambda c: (round(metrics[c]['inquiries_per_patient'], 9), int(c.split('_l')[1].split('_')[0])))
    return dict(policy=best, adopted_for_validation=True, feasible=feasible)


def verdict(report, chosen):
    j = next(r for r in report['paired'] if r['policy'] == chosen and r['baseline'] == BASE_POLICY
             and r['penalty'] == base.PRIMARY_PENALTY)
    q = next(r for r in report['paired_burden'] if r['policy'] == chosen and r['baseline'] == BASE_POLICY
             and r['key'] == 'inquiry_count')
    return dict(non_inferior=j['ci95'][1] < MARGIN_SECONDS, fewer_inquiries=q['ci95'][1] < 0,
                passed=j['ci95'][1] < MARGIN_SECONDS and q['ci95'][1] < 0,
                delta_J=j['delta_J_mean'], delta_J_ci95=j['ci95'], delta_inquiries=q['delta_mean'],
                delta_inquiries_ci95=q['ci95'])


def markdown(report):
    pols = report['policies']
    m, fr = report['mix']['metrics'], report['mix']['frontier']
    lines = [f"# 같은 J에서 문의 줄이기 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        lines += [f"**선택:** {report['selection']['policy'] or '없음(검증 미실행)'}", '', f'선택 규칙: {SELECTION_RULE}', '']
    else:
        v = report['verdict']
        lines += [f"**잠금 선택:** {report['selected_policy']} · 판정 {'통과' if v['passed'] else '미통과'} "
                  f"(ΔJ {v['delta_J']:.1f}초 [{v['delta_J_ci95'][0]:.1f}, {v['delta_J_ci95'][1]:.1f}], "
                  f"문의 {v['delta_inquiries']:+.2f}건 [{v['delta_inquiries_ci95'][0]:.2f}, {v['delta_inquiries_ci95'][1]:.2f}])", '',
                  f'판정 규칙: {SUCCESS_RULE}', '']
    lines += ['## J–문의 교환(보정 혼합)', '', '| 분기 | J(초) | 환자당 문의 | 환자당 검토 업무(초) | 실패 | 지배하는 분기 |', '|---|---:|---:|---:|---:|---|']
    for p in pols:
        lines.append(f"| {p} | {m[p]['J']:.1f} | {m[p]['inquiries_per_patient']:.2f} | {m[p]['review_work_seconds_per_patient']:.1f} | "
                     f"{m[p]['failed']} | {', '.join(fr[p]['dominated_by']) or '—(파레토 위)'} |")
    lines += ['', '## 짝지은 비교(J)', '', base.paired_table(report['paired']), '', '## 짝지은 비교(병원 부담)', '',
              '| 비교 | 지표 | 환자당 변화 | 95% 구간 |', '|---|---|---:|---|']
    for r in report['paired_burden']:
        unit = '건' if r['key'] == 'inquiry_count' else '초'
        lines.append(f"| {r['policy']} − {r['baseline']} | {r['key']} | {r['delta_mean']:+.3f}{unit} | "
                     f"[{r['ci95'][0]:.3f}, {r['ci95'][1]:.3f}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS] + [
        '- 병원 부담은 문의 건수(알림)와 실제 검토 업무 시간으로만 쟀다. 담당자의 심리적 부담·전화 응대는 모형에 없다.']
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
    tasks += [(f'family:{f}', s, DEV_POLICIES) for f in scenarios.original.FAMILY_NAMES for s in DEV_FAMILY]
    results, elapsed = run_all(tasks, workers)
    compare = [(c, BASE_POLICY) for c in CANDIDATES + CURVE] + [(c, 'fixed_3') for c in (BASE_POLICY,) + CANDIDATES]
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(DEV_POLICIES),
                  margin_seconds=MARGIN_SECONDS, selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, DEV_POLICIES, compare))
    report['selection'] = select(report)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_burden_20261021', selection=report['selection'],
                margin_seconds=MARGIN_SECONDS, source_sha256=hashes, development_report=base.relative_path(json_path),
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
        raise RuntimeError('development selected no candidate; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selection']['policy']
    policies = ('fixed_3', 'v3_w45_h900', BASE_POLICY, chosen)
    tasks = [('mix', s, policies) for s in VAL_MIX]
    tasks += [(f'family:{f}', s, policies) for f in scenarios.original.FAMILY_NAMES for s in VAL_FAMILY]
    results, elapsed = run_all(tasks, workers)
    compare = [(chosen, BASE_POLICY), (chosen, 'fixed_3'), (BASE_POLICY, 'fixed_3'), (chosen, 'v3_w45_h900')]
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(policies),
                  margin_seconds=MARGIN_SECONDS, selected_policy=chosen, lock=base.relative_path(lock_path),
                  lock_sha256=base.sha256_file(lock_path), success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, policies, compare))
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
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
