"""4차 1단계 진단: 병원 회신 정보의 가치(완전 정보 상한) 분해.

python -m inquiry_lab.experiment_voi --output simulation_results/inquiry_lab_voi_20261021

- 새 seed: 혼합 930001–930240, 8유형 940001–940003. 정책을 고르지 않는 진단이라 잠금·검증 단계가 없다.
- 분기: fixed_3, v3_w45_h900, eat_l15_f, eatp_oracle(병원별 수용확률), eato_silence(무응답 여부),
  eato_timing(무응답+처리시간), eato_all(무응답+처리시간+수용). 모두 즉시 확정.
- 사전 고정 진행 조건: ΔJ(eato_silence − EAT) ≤ −10초/환자이고 95% 구간 상한 < 0일 때만
  '확인 중' 신호 정책(2단계)을 개발·검증한다. 아니면 상한이 작아 진행하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import platform
import time

from server import inquiry_experiment

from . import eat_oracle_policy, eat_public_policy, experiment as base, harness, public_signal, scenarios

MIX = tuple(range(930001, 930241))
FAMILY = (940001, 940002, 940003)
POLICIES = ('fixed_3', 'v3_w45_h900', 'eat_l15_f', eat_public_policy.ORACLE_NAME,
            'eato_silence', 'eato_timing', 'eato_all')
INFORMATION = {
    eat_public_policy.ORACLE_NAME: '병원별 수용확률',
    'eato_silence': '무응답 여부',
    'eato_timing': '무응답 여부 + 처리시간',
    'eato_all': '무응답 여부 + 처리시간 + 수용/불가',
}
GATE_SECONDS = -10.0
GATE_RULE = ('ΔJ(eato_silence − EAT)가 환자당 −10초 이하이고 95% 구간 상한 < 0이면 2단계(확인 중 신호 정책)를 '
             '개발·검증한다. 아니면 진행하지 않는다.')
SOURCE_FILES = base.SOURCE_FILES + ('inquiry_lab/public_signal.py', 'inquiry_lab/eat_public_policy.py',
                                    'inquiry_lab/eat_oracle_policy.py', 'inquiry_lab/experiment_voi.py')


def run_case(task):
    kind, seed = task
    eat_oracle_policy.install()
    scenario = harness.build(kind, seed)
    eat_public_policy.set_context({}, None, oracle=public_signal.oracle_accept(kind, seed, scenario))
    eat_oracle_policy.set_scenario(scenario['potentials'])
    try:
        result = inquiry_experiment.run_scenario(scenario, policies=POLICIES)
    finally:
        eat_public_policy.clear_context()
        eat_oracle_policy.clear()
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], family=scenario['family'],
                mix=scenario.get('mix'), environment_sha256=result['environment_sha256'],
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario),
                branches={b['policy']: harness.compact(b) for b in result['branches']})


def evaluate(results):
    mix = [r for r in results if r['kind'] == 'mix']
    fam = [r for r in results if r['kind'] != 'mix']
    metrics = {p: base.policy_metrics(mix, p) for p in POLICIES}
    lb = base.lower_bound(mix)
    paired = [base.paired(mix, p, 'eat_l15_f', k) for p in INFORMATION for k in base.PENALTIES]
    primary = {r['policy']: r for r in paired if r['penalty'] == base.PRIMARY_PENALTY}
    gate = primary['eato_silence']
    proceed = gate['delta_J_mean'] <= GATE_SECONDS and gate['ci95'][1] < 0
    decomposition = dict(eat_gap_to_lower_bound=metrics['eat_l15_f']['J'] - lb,
                         value_by_information={p: -primary[p]['delta_J_mean'] for p in INFORMATION},
                         remaining_after_all_reply_information=metrics['eato_all']['J'] - lb)
    return dict(mix=dict(metrics=metrics, lower_bound=lb, subgroups=base.subgroups(mix, list(POLICIES))),
                families=base.family_table(fam, list(POLICIES)), paired=paired,
                decomposition=decomposition, gate=dict(rule=GATE_RULE, proceed=proceed,
                                                       eato_silence=dict(delta=gate['delta_J_mean'], ci95=gate['ci95'])))


def markdown(report):
    m, lb = report['mix']['metrics'], report['mix']['lower_bound']
    d = report['decomposition']
    lines = ['# 병원 회신 정보의 가치(완전 정보 상한) 진단', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f"**진행 판단:** {'2단계 진행' if report['gate']['proceed'] else '진행하지 않음(상한이 작음)'} — {GATE_RULE}", '',
             '## 보정 혼합', '', base.metrics_table(m, list(POLICIES), lb), '',
             '## EAT 대비 완전 정보의 가치', '', '| 아는 정보 | 분기 | J 감소(환자당) |', '|---|---|---:|']
    for p, label in INFORMATION.items():
        lines.append(f"| {label} | {p} | {d['value_by_information'][p]:.1f}초 |")
    lines += ['', f"EAT의 완전정보 하한 대비 손실 {d['eat_gap_to_lower_bound']:.1f}초 중, 회신 정보를 모두 알아도 "
              f"남는 손실 {d['remaining_after_all_reply_information']:.1f}초(대기열·여러 환자 경합, 하한은 대기열 무시).", '',
              '## 짝지은 비교', '', base.paired_table(report['paired']), '',
              '## 한계', '', '- 완전 정보 분기는 실제 운영에서 불가능한 비교 기준이다. 정책이 아니라 정보의 가치를 잰다.',
              '- 다른 환자의 처리시간·미래 환자·병원 처리 인원은 알려 주지 않았다.'] + [f'- {x}' for x in base.LIMITATIONS]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    json_path, md_path = Path(str(output) + '.json'), Path(str(output) + '.md')
    for p in (json_path, md_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    tasks = [('mix', s) for s in MIX] + [(f'family:{f}', s) for f in scenarios.original.FAMILY_NAMES for s in FAMILY]
    started = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    report = dict(stage='value_of_information', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                  scenario_count=len(results), elapsed_seconds=time.time() - started, policies=list(POLICIES),
                  seeds=dict(mix=[MIX[0], MIX[-1]], families=list(FAMILY)), source_sha256=hashes,
                  runtime=dict(python=platform.python_version()), results=results, **evaluate(results))
    tmp = json_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(json_path)
    md_path.write_text(markdown(report), encoding='utf-8')
    print('gate', report['gate'])
    print('decomposition', report['decomposition'])


if __name__ == '__main__':
    main()
