"""EAT 문의 정책의 개발 선택 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment development --output simulation_results/inquiry_lab_development_20261020
python -m inquiry_lab.experiment validation --lock simulation_results/inquiry_lab_development_20261020_lock.json \
    --output simulation_results/inquiry_lab_validation_20261020

규칙(사전 고정):
- 목적 J = 환자별 (선정 성공: 예상 도착초, 실패: 300 + 1500초)의 평균. 600·3000초 민감도 병기.
- 개발: 보정 혼합 240개 + 기존 8유형×3 seed. EAT 후보 10개와 기준 6개를 같은 입력으로 실행.
- 선택: 혼합 개발에서 실패 수·환자당 문의가 fixed_3 이하인 후보 중 J 최소(동률: 문의 적은 쪽).
  조건을 만족하는 후보가 없으면 J 최소 후보를 고르고 constraints_met=false로 남긴다.
- 잠금: 소스 해시·선택·개발 보고서 해시. 검증은 해시가 같을 때만 실행하고 재선택하지 않는다.
- 검증: 미사용 혼합 400개 + 8유형×6 seed. EAT가 fixed_3보다 낫다고 말하려면 혼합 검증의
  ΔJ(EAT−fixed_3) 시나리오 단위 부트스트랩 95% 구간 상한이 0 미만이어야 한다. V3도 같은 방식.
기존 결과 파일은 덮어쓰지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import platform
import random
import time

from . import eat_policy, harness, scenarios

ROOT = Path(__file__).resolve().parents[1]
PENALTIES = (600, 1500, 3000)
PRIMARY_PENALTY = harness.FAILURE_PENALTY_SECONDS
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 20261020
SOURCE_FILES = (
    'inquiry_lab/__init__.py', 'inquiry_lab/eat_policy.py', 'inquiry_lab/scenarios.py',
    'inquiry_lab/harness.py', 'inquiry_lab/experiment.py',
    'server/inquiry_experiment.py', 'server/inquiry_experiment_scenarios.py',
    'server/inquiry_policies.py', 'server/inquiry_v3.py', 'server/inquiry_v4.py',
    'server/inquiry_workflow.py', 'server/inquiry_observations.py', 'server/service.py',
    'server/db.py', 'simulation/observed_inquiry.py', 'config/inquiry_v3_selected.json',
)
SELECTION_RULE = ('혼합 개발 세트에서 실패 수와 환자당 문의가 fixed_3 이하인 EAT 후보 중 J(실패비용 1500초) '
                  '최소. 동률이면 환자당 문의가 적은 후보, 그다음 이름순. 조건 충족 후보가 없으면 J 최소.')
SUCCESS_RULE = ('혼합 검증 세트에서 ΔJ(EAT−기준)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0이면 '
                '"기준보다 J가 낮다"고 판단한다. 실패·문의·민감도·기존 8유형은 판정 없이 함께 보고한다.')
LIMITATIONS = [
    '모든 병원·환자·회신·처리시간·이동시간은 합성값이다. 실제 병원 효과나 임상 예후를 뜻하지 않는다.',
    '회신률·회신시간 사전값은 충북 ICT-EMS 공개 통계, 수용 수준은 호남권 환자당 문의 3.8곳과 맞춘 설계 가정이다.',
    '서버 실험기 계약상 과거 기록에는 거절만 넣을 수 있어 수용 이력은 없다.',
    '선정은 첫 유효 수용 회신 즉시 확정한다고 본다. 사람의 확인·통화 시간은 포함하지 않는다.',
    '실패 비용 1500초(상황실 수동 선정 18분 + 이동 약 7분)는 설계값이며 600·3000초 민감도를 함께 보고한다.',
    '완전정보 기준은 단일 환자에서만 실제 달성 가능한 최선값이고, 여러 환자에서는 대기열을 무시한 하한이다.',
]


def relative_path(path):
    """보고서·잠금에 남길 저장소 기준 경로. 상대·절대 입력을 모두 받는다."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {name: sha256_file(ROOT / name) for name in SOURCE_FILES}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(harness.run_case, tasks, chunksize=1))
    return results, time.time() - started


# ---------------------------------------------------------------- 집계

def _quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    position = q * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def policy_metrics(results, policy, penalty=PRIMARY_PENALTY):
    costs, regrets, selections_capped, arrivals = [], [], [], []
    failed = avoidable_failed = feasible = inquiries = 0
    work = cancelled = 0
    for r in results:
        b = r['branches'][policy]
        oracle = r['oracle']['arrival_seconds']
        exact = r['oracle']['exact']
        for p in b['patients']:
            cost = harness.patient_cost(p, penalty)
            costs.append(cost)
            inquiries += p['inquiry_count']
            failed += p['failed']
            best = oracle[p['id']]
            if best is not None:
                feasible += 1
                avoidable_failed += p['failed']
            if exact:
                regrets.append(cost - (best if best is not None else 300 + penalty))
            selections_capped.append(300 if p['failed'] else p['selection_seconds'])
            if not p['failed']:
                arrivals.append(p['estimated_arrival_seconds'])
        work += b['review_work_seconds']
        cancelled += b['cancelled_before_start_count']
    n = len(costs)
    worst = sorted(costs)[int(0.9 * n):] if n else []
    return dict(patients=n, J=sum(costs) / n, p90=_quantile(costs, 0.9),
                cvar90=sum(worst) / len(worst) if worst else None,
                failed=failed, feasible_patients=feasible, avoidable_failed=avoidable_failed,
                inquiries_per_patient=inquiries / n, review_work_seconds_per_patient=work / n,
                cancelled_before_start=cancelled,
                selection_capped_mean=sum(selections_capped) / n,
                arrival_mean_selected=sum(arrivals) / len(arrivals) if arrivals else None,
                single_patient_regret_mean=sum(regrets) / len(regrets) if regrets else None,
                single_patient_count=len(regrets))


def lower_bound(results, penalty=PRIMARY_PENALTY):
    """완전정보 하한 J(단일=달성 가능 최선, 여러 환자=대기열 무시 하한)."""
    values = [v if v is not None else 300 + penalty
              for r in results for v in r['oracle']['arrival_seconds'].values()]
    return sum(values) / len(values)


def paired(results, policy, baseline, penalty=PRIMARY_PENALTY):
    per_scenario, better = [], dict(better=0, same=0, worse=0)
    worst_increase = 0.0
    for r in results:
        ours = {p['id']: p for p in r['branches'][policy]['patients']}
        theirs = {p['id']: p for p in r['branches'][baseline]['patients']}
        delta = 0.0
        for pid in ours:
            d = harness.patient_cost(ours[pid], penalty) - harness.patient_cost(theirs[pid], penalty)
            delta += d
            key = 'better' if d < -1e-9 else 'worse' if d > 1e-9 else 'same'
            better[key] += 1
            worst_increase = max(worst_increase, d)
        per_scenario.append((delta, len(ours)))
    total = sum(n for _, n in per_scenario)
    mean = sum(d for d, _ in per_scenario) / total
    rng = random.Random(BOOTSTRAP_SEED)
    k = len(per_scenario)
    stats = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [per_scenario[rng.randrange(k)] for _ in range(k)]
        stats.append(sum(d for d, _ in sample) / sum(n for _, n in sample))
    stats.sort()
    low = stats[int(0.025 * BOOTSTRAP_RESAMPLES)]
    high = stats[int(0.975 * BOOTSTRAP_RESAMPLES) - 1]
    scen = dict(better=sum(d < -1e-9 for d, _ in per_scenario),
                same=sum(abs(d) <= 1e-9 for d, _ in per_scenario),
                worse=sum(d > 1e-9 for d, _ in per_scenario))
    return dict(policy=policy, baseline=baseline, penalty=penalty, delta_J_mean=mean,
                ci95=[low, high], significant_lower=high < 0, significant_higher=low > 0,
                patients=better, scenarios=scen, max_individual_increase=worst_increase)


def subgroups(results, policies):
    out = {}
    for key in ('level', 'arrival', 'history'):
        for value in sorted({r['mix'][key] for r in results if r['mix']}):
            subset = [r for r in results if r['mix'] and r['mix'][key] == value]
            out[f'{key}={value}'] = {p: policy_metrics(subset, p) for p in policies}
    return out


def family_table(results, policies):
    out = {}
    for family in sorted({r['family'] for r in results}):
        subset = [r for r in results if r['family'] == family]
        out[family] = {p: policy_metrics(subset, p) for p in policies}
    equal = {}
    for p in policies:
        rows = [out[f][p] for f in out]
        equal[p] = dict(J=sum(r['J'] for r in rows) / len(rows),
                        selection_capped_mean=sum(r['selection_capped_mean'] for r in rows) / len(rows),
                        inquiries_per_patient=sum(r['inquiries_per_patient'] for r in rows) / len(rows),
                        failed=sum(r['failed'] for r in rows))
    return dict(by_family=out, family_equal=equal)


def select(mix_metrics, candidates):
    base = mix_metrics['fixed_3']
    feasible = [c for c in candidates
                if mix_metrics[c]['failed'] <= base['failed']
                and mix_metrics[c]['inquiries_per_patient'] <= base['inquiries_per_patient']]
    pool = feasible or list(candidates)
    best = min(pool, key=lambda c: (round(mix_metrics[c]['J'], 9),
                                    mix_metrics[c]['inquiries_per_patient'], c))
    return dict(policy=best, constraints_met=bool(feasible), feasible_candidates=feasible)


# ---------------------------------------------------------------- 보고서

def _fmt(value, digits=1):
    if value is None:
        return '—'
    if isinstance(value, float):
        return f'{value:,.{digits}f}'
    return f'{value:,}'


def metrics_table(metrics, policies, lb=None):
    lines = ['| 정책 | J(초) | p90 | 하위10% 평균 | 실패 | 피할 수 있던 실패 | 환자당 문의 | 환자당 검토 업무(초) | 단일환자 손실(초) |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for p in policies:
        m = metrics[p]
        lines.append(f"| {p} | {_fmt(m['J'])} | {_fmt(m['p90'])} | {_fmt(m['cvar90'])} | {m['failed']} | "
                     f"{m['avoidable_failed']} | {_fmt(m['inquiries_per_patient'], 2)} | "
                     f"{_fmt(m['review_work_seconds_per_patient'])} | {_fmt(m['single_patient_regret_mean'])} |")
    if lb is not None:
        lines.append(f'| 완전정보 하한 | {_fmt(lb)} | | | | | | | 0 |')
    return '\n'.join(lines)


def paired_table(rows):
    lines = ['| 비교 | 실패비용 | ΔJ 평균(초) | 95% 구간 | 환자 개선/동일/악화 | 시나리오 개선/동일/악화 | 개인 최대 악화(초) |',
             '|---|---:|---:|---|---|---|---:|']
    for r in rows:
        lines.append(f"| {r['policy']} − {r['baseline']} | {r['penalty']} | {_fmt(r['delta_J_mean'])} | "
                     f"[{_fmt(r['ci95'][0])}, {_fmt(r['ci95'][1])}] | "
                     f"{r['patients']['better']}/{r['patients']['same']}/{r['patients']['worse']} | "
                     f"{r['scenarios']['better']}/{r['scenarios']['same']}/{r['scenarios']['worse']} | "
                     f"{_fmt(r['max_individual_increase'])} |")
    return '\n'.join(lines)


def markdown(report):
    stage = report['stage']
    policies = report['policies']
    out = [f"# EAT 문의 정책 {'개발' if stage == 'development' else '검증'} 결과", '',
           f"생성: {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if stage == 'development':
        s = report['selection']
        out += [f"**선택:** `{s['policy']}` (조건 충족: {s['constraints_met']}).", '', f"선택 규칙: {SELECTION_RULE}", '']
    else:
        out += [f"**잠금된 선택:** `{report['selected_policy']}` · 잠금 해시 `{report['lock_sha256'][:12]}…`", '',
                f"판정 규칙: {SUCCESS_RULE}", '']
    out += ['## 보정 혼합 환경', '', metrics_table(report['mix']['metrics'], policies, report['mix']['lower_bound']), '']
    if report.get('paired'):
        out += ['### 짝지은 비교', '', paired_table(report['paired']), '']
    out += ['### 조건별 J(초)', '']
    groups = report['mix']['subgroups']
    show = report['subgroup_policies']
    out.append('| 조건 | 환자 | ' + ' | '.join(show) + ' |')
    out.append('|---|---:|' + '---:|' * len(show))
    for g, values in groups.items():
        out.append(f"| {g} | {values[show[0]]['patients']} | " +
                   ' | '.join(f"{_fmt(values[p]['J'])} (실패 {values[p]['failed']})" for p in show) + ' |')
    out += ['', '## 기존 8개 유형(새 seed, 판정 없는 스트레스 시험)', '']
    fam = report['families']
    out.append('| 유형 | ' + ' | '.join(show) + ' |')
    out.append('|---|' + '---:|' * len(show))
    for family, values in fam['by_family'].items():
        out.append(f'| {family} | ' + ' | '.join(
            f"{_fmt(values[p]['J'])} / 문의 {_fmt(values[p]['inquiries_per_patient'], 2)}" for p in show) + ' |')
    out.append('| 유형 균등 평균 | ' + ' | '.join(
        f"{_fmt(fam['family_equal'][p]['J'])} / 선정 {_fmt(fam['family_equal'][p]['selection_capped_mean'])}초"
        for p in show) + ' |')
    out += ['', '## 한계', ''] + [f'- {x}' for x in LIMITATIONS] + ['']
    return '\n'.join(out)


# ---------------------------------------------------------------- 실행

def _evaluate(results, policies, compare_to=()):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] != 'mix']
    report = dict(mix=dict(metrics={p: policy_metrics(mix, p) for p in policies},
                           sensitivity={str(k): {p: policy_metrics(mix, p, k)['J'] for p in policies}
                                        for k in PENALTIES},
                           lower_bound=lower_bound(mix),
                           subgroups=subgroups(mix, policies)),
                  families=family_table(fam, policies))
    rows = []
    for policy, baseline in compare_to:
        for penalty in PENALTIES:
            rows.append(paired(mix, policy, baseline, penalty))
    report['paired'] = rows
    return report


def _write(path_stem, report):
    json_path, md_path = Path(str(path_stem) + '.json'), Path(str(path_stem) + '.md')
    for path in (json_path, md_path):
        if path.exists():
            raise FileExistsError(f'{path} already exists; choose a new output name')
    tmp = json_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(json_path)
    md_path.write_text(markdown(report), encoding='utf-8')
    return json_path, md_path


def development(output, workers):
    output = Path(output).resolve()
    grid = eat_policy.variant_grid()
    policies = list(harness.BASELINES) + list(grid)
    tasks = [('mix', s, policies) for s in scenarios.DEVELOPMENT_MIX_SEEDS]
    tasks += [(f'family:{f}', s, policies) for f in scenarios.original.FAMILY_NAMES
              for s in scenarios.DEVELOPMENT_FAMILY_SEEDS]
    hashes = source_hashes()
    results, elapsed = run_all(tasks, workers)
    evaluation = _evaluate(results, policies)
    selection = select(evaluation['mix']['metrics'], grid)
    chosen = selection['policy']
    evaluation['paired'] = [paired([r for r in results if r['kind'] == 'mix'], chosen, base, k)
                            for base in ('fixed_3', 'v3_w45_h900') for k in PENALTIES]
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=policies,
                  subgroup_policies=['fixed_3', 'v3_w45_h900', 'observed_v4', chosen],
                  selection=selection, selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=list(scenarios.DEVELOPMENT_MIX_SEEDS),
                             families=list(scenarios.DEVELOPMENT_FAMILY_SEEDS)),
                  source_sha256=hashes, eat_spec=eat_policy.spec(chosen),
                  runtime=dict(python=platform.python_version()), limitations=LIMITATIONS,
                  results=results, **evaluation)
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_eat_20261020', selected_policy=chosen,
                selection=selection, source_sha256=hashes,
                development_report=relative_path(json_path),
                development_report_sha256=sha256_file(json_path),
                validation_seeds=dict(mix=[scenarios.VALIDATION_MIX_SEEDS[0], scenarios.VALIDATION_MIX_SEEDS[-1]],
                                      families=list(scenarios.VALIDATION_FAMILY_SEEDS)),
                selection_rule=SELECTION_RULE, success_rule=SUCCESS_RULE,
                runtime=dict(python=platform.python_version()))
    lock_path = Path(str(output) + '_lock.json')
    if lock_path.exists():
        raise FileExistsError(f'{lock_path} already exists')
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding='utf-8')
    return json_path, md_path, lock_path, report


def validation(lock_path, output, workers):
    lock_path, output = Path(lock_path).resolve(), Path(output).resolve()
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    report_path = ROOT / lock['development_report']
    if sha256_file(report_path) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    chosen = lock['selected_policy']
    policies = list(harness.BASELINES) + [chosen]
    tasks = [('mix', s, policies) for s in scenarios.VALIDATION_MIX_SEEDS]
    tasks += [(f'family:{f}', s, policies) for f in scenarios.original.FAMILY_NAMES
              for s in scenarios.VALIDATION_FAMILY_SEEDS]
    results, elapsed = run_all(tasks, workers)
    evaluation = _evaluate(results, policies,
                           compare_to=[(chosen, 'fixed_3'), (chosen, 'v3_w45_h900'),
                                       (chosen, 'observed_v4'), (chosen, 'fixed_5')])
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=policies,
                  subgroup_policies=['fixed_3', 'v3_w45_h900', 'observed_v4', chosen],
                  selected_policy=chosen, lock=relative_path(lock_path), lock_sha256=sha256_file(lock_path),
                  success_rule=SUCCESS_RULE,
                  seeds=dict(mix=list(scenarios.VALIDATION_MIX_SEEDS),
                             families=list(scenarios.VALIDATION_FAMILY_SEEDS)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  limitations=LIMITATIONS, results=results, **evaluation)
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
        json_path, md_path, lock_path, report = development(Path(args.output), args.workers)
        print('selected', report['selection'])
        print(json_path, md_path, lock_path)
    else:
        json_path, md_path, report = validation(args.lock, Path(args.output), args.workers)
        for row in report['paired']:
            print(row['baseline'], row['penalty'], round(row['delta_J_mean'], 1),
                  [round(x, 1) for x in row['ci95']])
        print(json_path, md_path)


if __name__ == '__main__':
    main()
