"""9차 실험: 취소 즉시 검토 중단(병원 화면의 취소 알림). 개발 → 잠금 → 미사용 seed 검증.

python -m inquiry_lab.experiment_interrupt development --output simulation_results/inquiry_lab_interrupt_development_20261022
python -m inquiry_lab.experiment_interrupt validation --lock simulation_results/inquiry_lab_interrupt_development_20261022_lock.json \
    --output simulation_results/inquiry_lab_interrupt_validation_20261022

사전 고정:
- 질문: 이미 다른 병원으로 확정된 환자의 문의를 병원이 검토하다 멈추면(알아차리는 시간 r초, 멈추는 비율 q%)
  J가 얼마나 줄고, 문의 정책의 알맞은 강도가 바뀌는가. 8차 개발 진단에서 병원 검토 시간의 20%(EAT)·27%(고정3)가
  이런 문의에 쓰였다.
- 환경: 1차와 같은 보정 혼합. 새 seed만 사용: 개발 1160001–1160240·8유형 1180001–1180003,
  검증 1170001–1170400·8유형 1190001–1190006.
- 문의 정책: fixed_3(운영 기본), eat_l15_f(1차 잠금), eat_l0_f(문의비용 0인 더 적극적인 EAT). 정책 코드는 바꾸지 않는다.
- 규칙(interrupt_runner): base, min2(상시 2명, 비교), ci_r{0,15,30,60}_q{50,100}, od_k2_d60_l120(8차 잠금 규칙),
  od_k2_d60_l120+ci_r30_q50.
- 판정 규칙은 실행 전에 ci_r30_q50(30초 뒤, 절반만 멈춤 — 보수적 가정)으로 정했다. 개발에서 고르지 않는다.
- 진행 조건(개발): EAT λ15에서 ΔJ(ci_r30_q50 − base) < 0. 아니면 검증하지 않는다.
- 판정(검증): EAT λ15에서 ΔJ(ci_r30_q50 − base)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0이면 효과 확인.
  상한(ci_r0_q100)·ci_r15_q100·8차 규칙과의 결합·고정3·λ0, 그리고 λ0 − λ15·EAT − 고정3의 변화는 판정 없이 보고한다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from . import experiment as base, experiment_capacity as cap, harness, interrupt_runner, scenarios

DEV_MIX = tuple(range(1160001, 1160241))
DEV_FAMILY = (1180001, 1180002, 1180003)
VAL_MIX = tuple(range(1170001, 1170401))
VAL_FAMILY = (1190001, 1190002, 1190003, 1190004, 1190005, 1190006)
POLICIES = ('fixed_3', 'eat_l15_f', 'eat_l0_f')
BASE_POLICY = 'eat_l15_f'
VERDICT_RULE = 'ci_r30_q50'
BACKUP_RULE = 'od_k2_d60_l120'
GRID = tuple(interrupt_runner.interrupt_name(r, q) for r in (0, 15, 30, 60) for q in (100, 50))
DEV_RULES = ('base', 'min2') + GRID + (BACKUP_RULE, f'{BACKUP_RULE}+{VERDICT_RULE}')
VAL_RULES = ('base', 'min2', 'ci_r0_q100', 'ci_r15_q100', VERDICT_RULE, BACKUP_RULE, f'{BACKUP_RULE}+{VERDICT_RULE}')
SOURCE_FILES = cap.SOURCE_FILES + ('inquiry_lab/interrupt_runner.py', 'inquiry_lab/experiment_interrupt.py')
GO_RULE = f'혼합 개발(EAT λ15)에서 ΔJ({VERDICT_RULE} − base) < 0이면 검증한다. 아니면 검증하지 않는다.'
SUCCESS_RULE = (f'검증 혼합(EAT λ15)에서 ΔJ({VERDICT_RULE} − base)의 시나리오 단위 부트스트랩 95% 구간 상한 < 0이면 '
                '효과 확인. 나머지 규칙·정책 비교는 판정 없이 보고한다.')
LIMITATIONS = [
    '검토 중단은 검토자가 취소 알림을 보고 r초 뒤 멈춘다는 가정이다. 멈춘 뒤 다른 업무로 넘어가는 전환 시간은 없다.',
    '멈추는 비율 q는 문의마다 해시로 정한 가정값이다. 실제 병원 화면의 알림 방식·검토자 행동은 측정하지 않았다.',
    '취소는 환자가 다른 병원으로 확정되거나 관측 300초가 끝날 때 생긴다. 확정은 수용 회신 즉시라는 1차 가정을 따른다.',
    '문의 정책은 실행 중 바꾸지 않았다. λ0은 1차 격자에 있던 EAT 변형이며 이번에 새로 고른 값이 아니다.',
]
ARRIVALS = ('burst', 'stream')


def arm(policy, rule):
    return f'{policy}|{rule}'


def arms_for(rules, policies=POLICIES):
    return [(p, r) for p in policies for r in rules]


def compact(branch):
    row = cap.compact(branch)
    row['interrupt'] = branch['interrupt']
    return row


def run_case(task):
    kind, seed, arm_list = task
    harness.install([p for p, _ in arm_list if p.startswith('eat_')])
    scenario = harness.build(kind, seed)
    result = interrupt_runner.run_scenario(scenario, arm_list)
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={arm(b['policy'], b['rule']): compact(b) for b in result['branches']})


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


# ---------------------------------------------------------------- 집계

def interrupt_metrics(results, name):
    patients = sum(r['patient_count'] for r in results)
    total = {k: sum(r['branches'][name]['interrupt'][k] for r in results)
             for k in ('count', 'saved_seconds', 'cancelled_in_review')}
    return dict(cancelled_in_review_per_patient=total['cancelled_in_review'] / patients,
                interrupted_per_patient=total['count'] / patients,
                saved_seconds_per_patient=total['saved_seconds'] / patients)


def rule_rows(results, policy, rules):
    out = {}
    for rule in rules:
        name = arm(policy, rule)
        m = base.policy_metrics(results, name)
        row = dict(J=m['J'], failed=m['failed'], avoidable_failed=m['avoidable_failed'],
                   inquiries_per_patient=m['inquiries_per_patient'],
                   review_work_seconds_per_patient=m['review_work_seconds_per_patient'],
                   interrupt=interrupt_metrics(results, name), backup=cap.backup_metrics(results, name))
        if rule != 'base':
            row['paired_vs_base'] = base.paired(results, name, arm(policy, 'base'))
        if rule not in ('base', 'min2'):
            row['share_of_min2'] = cap.capture(results, policy, rule)
        out[rule] = row
    return out


def evaluate(results, rules):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] != 'mix']
    names = [arm(p, r) for p, r in arms_for(rules)]
    report = dict(mix=dict(metrics={n: base.policy_metrics(mix, n) for n in names},
                           sensitivity={str(k): {n: base.policy_metrics(mix, n, k)['J'] for n in names}
                                        for k in base.PENALTIES},
                           lower_bound=base.lower_bound(mix),
                           rules={p: rule_rows(mix, p, rules) for p in POLICIES}),
                  families=base.family_table(fam, names))
    report['by_arrival'] = {}
    for a in ARRIVALS:
        subset = [r for r in mix if r['mix']['arrival'] == a]
        if subset:
            report['by_arrival'][a] = {BASE_POLICY: rule_rows(subset, BASE_POLICY, rules)}
    focus = [r for r in ('base', VERDICT_RULE, 'ci_r0_q100', 'min2') if r in rules]
    report['policy_gaps'] = {
        'eat_l0_f − eat_l15_f': {r: base.paired(mix, arm('eat_l0_f', r), arm('eat_l15_f', r)) for r in focus},
        'eat_l15_f − fixed_3': {r: base.paired(mix, arm('eat_l15_f', r), arm('fixed_3', r)) for r in focus},
    }
    return report


def go(rule_table):
    row = rule_table[VERDICT_RULE]['paired_vs_base']
    return dict(proceed=row['delta_J_mean'] < 0, rule=VERDICT_RULE, delta_J=row['delta_J_mean'], ci95=row['ci95'])


def verdict(rule_table):
    row = rule_table[VERDICT_RULE]['paired_vs_base']
    return dict(effect_confirmed=row['ci95'][1] < 0, rule=VERDICT_RULE, delta_J=row['delta_J_mean'], ci95=row['ci95'])


# ---------------------------------------------------------------- 보고서

def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def _pct(value):
    return '—' if value is None else f'{100 * value:.0f}%'


def rule_table(rows, rules):
    lines = ['| 규칙 | J(초) | ΔJ vs base [95% 구간] | 상시 2명 대비 | 실패 | 환자당 문의 | 검토 업무(초/환자) | 검토 중 취소(건/환자) | 중단(건/환자) | 회수 시간(초/환자) |',
             '|---|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    for rule in rules:
        row = rows[rule]
        pv, share, it = row.get('paired_vs_base'), row.get('share_of_min2'), row['interrupt']
        delta = f"{_f(pv['delta_J_mean'])} [{_f(pv['ci95'][0])}, {_f(pv['ci95'][1])}]" if pv else '—'
        lines.append(f"| {rule} | {_f(row['J'])} | {delta} | {_pct(share['capture']) if share else '—'} | {row['failed']} | "
                     f"{_f(row['inquiries_per_patient'], 2)} | {_f(row['review_work_seconds_per_patient'])} | "
                     f"{_f(it['cancelled_in_review_per_patient'], 2)} | {_f(it['interrupted_per_patient'], 2)} | "
                     f"{_f(it['saved_seconds_per_patient'])} |")
    return '\n'.join(lines)


def markdown(report):
    rules = report['rules']
    lines = [f"# 취소 즉시 검토 중단 {'개발' if report['stage'] == 'development' else '검증'} 결과", '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '']
    if report['stage'] == 'development':
        g = report['go']
        lines += [f"**진행 조건:** {GO_RULE}", '',
                  f"- {g['rule']}: ΔJ {_f(g['delta_J'])} [{_f(g['ci95'][0])}, {_f(g['ci95'][1])}] → 검증 진행 {g['proceed']}", '']
    else:
        v = report['verdict']
        lines += [f'**판정 규칙:** {SUCCESS_RULE}', '',
                  f"- {v['rule']}: ΔJ {_f(v['delta_J'])} [{_f(v['ci95'][0])}, {_f(v['ci95'][1])}] → 효과 확인 **{v['effect_confirmed']}**", '']
    mix = report['mix']
    for p in POLICIES:
        lines += [f'## {p}', '', rule_table(mix['rules'][p], rules), '']
    for a, by in report['by_arrival'].items():
        lines += [f'### 도착 형태 {a} — {BASE_POLICY}', '', rule_table(by[BASE_POLICY], rules), '']
    lines += ['## 정책 차이의 규칙별 변화', '', '| 비교 | 규칙 | ΔJ | 95% 구간 |', '|---|---|---:|---|']
    for label, by_rule in report['policy_gaps'].items():
        for r, row in by_rule.items():
            lines.append(f"| {label} | {r} | {_f(row['delta_J_mean'])} | [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}] |")
    fam = report['families']
    show = [r for r in ('base', 'min2', VERDICT_RULE, 'ci_r0_q100') if r in rules]
    lines += ['', '## 기존 8유형(스트레스 시험, EAT λ15)', '', '| 유형 | ' + ' | '.join(show) + ' |', '|---|' + '---:|' * len(show)]
    for f, vals in fam['by_family'].items():
        lines.append(f'| {f} | ' + ' | '.join(f"{vals[arm(BASE_POLICY, r)]['J']:.0f}" for r in show) + ' |')
    lines.append('| 유형 균등 | ' + ' | '.join(f"{fam['family_equal'][arm(BASE_POLICY, r)]['J']:.1f}" for r in show) + ' |')
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + LIMITATIONS]
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


def _tasks(mix_seeds, family_seeds, rules):
    arm_list = arms_for(rules)
    tasks = [('mix', s, arm_list) for s in mix_seeds]
    tasks += [(f'family:{f}', s, arm_list) for f in scenarios.original.FAMILY_NAMES for s in family_seeds]
    return tasks


def development(output, workers):
    output = Path(output).resolve()
    hashes = source_hashes()
    results, elapsed = run_all(_tasks(DEV_MIX, DEV_FAMILY, DEV_RULES), workers)
    report = dict(stage='development', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(DEV_RULES),
                  go_rule=GO_RULE, success_rule=SUCCESS_RULE,
                  seeds=dict(mix=[DEV_MIX[0], DEV_MIX[-1]], families=list(DEV_FAMILY)),
                  source_sha256=hashes, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, DEV_RULES))
    report['go'] = go(report['mix']['rules'][BASE_POLICY])
    json_path, md_path = _write(output, report)
    lock = dict(schema_version=1, experiment='inquiry_lab_interrupt_20261022', go=report['go'],
                source_sha256=hashes, development_report=base.relative_path(json_path),
                development_report_sha256=base.sha256_file(json_path),
                validation_seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                validation_rules=list(VAL_RULES), go_rule=GO_RULE, success_rule=SUCCESS_RULE)
    lock_path = Path(str(output) + '_lock.json')
    if lock_path.exists():
        raise FileExistsError(f'{lock_path} already exists')
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding='utf-8')
    return json_path, md_path, lock_path, report


def validation(lock_path, output, workers):
    lock_path, output = Path(lock_path).resolve(), Path(output).resolve()
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if not lock['go']['proceed']:
        raise RuntimeError('development did not meet the go rule; validation is not run by protocol')
    current = source_hashes()
    changed = sorted(k for k in lock['source_sha256'] if lock['source_sha256'][k] != current.get(k))
    if changed:
        raise RuntimeError('source changed after development lock: ' + ', '.join(changed))
    if base.sha256_file(base.ROOT / lock['development_report']) != lock['development_report_sha256']:
        raise RuntimeError('development report changed after lock')
    rules = tuple(lock['validation_rules'])
    results, elapsed = run_all(_tasks(VAL_MIX, VAL_FAMILY, rules), workers)
    report = dict(stage='validation', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=elapsed, policies=list(POLICIES), rules=list(rules),
                  lock=base.relative_path(lock_path), lock_sha256=base.sha256_file(lock_path),
                  success_rule=SUCCESS_RULE, seeds=dict(mix=[VAL_MIX[0], VAL_MIX[-1]], families=list(VAL_FAMILY)),
                  source_sha256=current, runtime=dict(python=platform.python_version()),
                  results=results, **evaluate(results, rules))
    report['verdict'] = verdict(report['mix']['rules'][BASE_POLICY])
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
        print('go', report['go'])
        for p in POLICIES:
            for rule, row in report['mix']['rules'][p].items():
                pv = row.get('paired_vs_base')
                print(p, rule, round(row['J'], 1), None if pv is None else round(pv['delta_J_mean'], 1),
                      round(row['interrupt']['saved_seconds_per_patient'], 1))
    else:
        _, _, report = validation(args.lock, args.output, args.workers)
        print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
