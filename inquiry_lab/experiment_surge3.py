"""36차 실험: 28차 후보(선제 호출 6명 + 보조 복귀 전 60초) 표본 확대 확인 실험. 선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_surge3 run --output simulation_results/inquiry_lab_surge3_validation_20261028

사전 고정:
- 근거: 28차에서 이 후보는 보통 상황 ΔJ가 개발 −6.4초, 검증 −2.1초 [−4.8, +0.3]로 방향이 같았고(개발+검증 합산 −3.6초
  [−6.7, −1.1]), 다수 사상자는 +0.3초, 보조 근무는 −25.1초/환자였다. 검증 상한이 +0.3초로 판정에 실패했다.
  보통 상황 표본을 두 배로 늘려 같은 후보를 한 번 확인한다. 같은 후보의 두 번째 확인이라 우연한 통과 위험이 조금 커진다.
- 구성: 26차 최종 구성(G) vs 후보(G의 보조 호출을 od_k2_d60_l60, 선제 호출을 sg_n6_w120으로). 정책 eatks_seenbase_k10_m4(판정),
  fixed_3(참고). 실행기와 계산은 28차(experiment_surge2)와 같다.
- seed(처음 사용): 혼합 2460001–2460800, 다수 2470001–2470200, 8유형 2480001–2480006.
- 판정(EAT-K): 혼합 ΔJ 95% 구간 상한 < 0, 다수 ΔJ 상한 < +5초, 환자당 보조 근무 차이(혼합+다수) 상한 < 0이면 채택 후보.
- 실행 전에 설계와 소스 해시를 잠금 파일로 먼저 쓴다. 기존 결과가 있으면 덮어쓰지 않는다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import time

from . import experiment as base, experiment_surge2 as prev

MIX = tuple(range(2460001, 2460801))
MASS = tuple(range(2470001, 2470201))
FAMILY = (2480001, 2480002, 2480003, 2480004, 2480005, 2480006)
FOCUS, BASE = prev.FOCUS, prev.BASE
CANDIDATE = f'od_k2_d60_l60+sg_n6_w120+{prev.REST}'
assert CANDIDATE in prev.CANDIDATES
RULES = (BASE, CANDIDATE)
MARGIN = 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/experiment_surge3.py',)
SUCCESS_RULE = (f'혼합 ΔJ 상한 < 0, 다수 ΔJ 상한 < +{MARGIN:.0f}초, 환자당 보조 근무 차이(혼합+다수) 상한 < 0이면 채택 후보.')


def verdict(report):
    g = report['groups']
    mix, mass = g['mix'][FOCUS][CANDIDATE]['versus_base'], g['mass'][FOCUS][CANDIDATE]['versus_base']
    duty = report['pooled_duty_difference'][CANDIDATE]
    checks = dict(mix_better=mix['ci95'][1] < 0, mass_noninferior=mass['ci95'][1] < MARGIN, less_duty=duty['ci95'][1] < 0)
    return dict(checks, adopted=all(checks.values()), mix=dict(delta_J=mix['delta_J_mean'], ci95=mix['ci95']),
                mass=dict(delta_J=mass['delta_J_mean'], ci95=mass['ci95']), duty_difference=duty)


def markdown(report):
    v, _f = report['verdict'], prev._f
    lines = ['# 선제 호출 6명 + 보조 복귀 전 60초 확인 실험(표본 확대)', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {SUCCESS_RULE} → 채택 후보 **{v["adopted"]}**', '',
             f"- 혼합 ΔJ {_f(v['mix']['delta_J'])} [{_f(v['mix']['ci95'][0])}, {_f(v['mix']['ci95'][1])}]",
             f"- 다수 ΔJ {_f(v['mass']['delta_J'])} [{_f(v['mass']['ci95'][0])}, {_f(v['mass']['ci95'][1])}]",
             f"- 보조 근무 차이(초/환자): {_f(v['duty_difference']['difference_per_patient'])} "
             f"[{_f(v['duty_difference']['ci95'][0])}, {_f(v['duty_difference']['ci95'][1])}]"]
    body = prev.markdown(dict(report, stage='development', selection=dict(rule=CANDIDATE, proceed=True)))
    lines += [''] + ['## ' + part for part in body.split('\n## ')[1:]]       # 28차 보고서의 표(혼합·다수·도착 형태·한계)
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(rules=list(RULES), policies=list(prev.POLICIES), seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]], families=list(FAMILY)),
                  success_rule=SUCCESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_surge3_20261028', design=design,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    results, elapsed = prev.run_all(prev._tasks(MIX, MASS, FAMILY, RULES), workers)
    if {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES} != hashes:
        raise RuntimeError('source changed during the confirmatory run')
    report = dict(stage='confirm', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), scenario_count=len(results),
                  elapsed_seconds=elapsed, design=design, rules=list(RULES), lock=base.relative_path(lock_path),
                  lock_sha256=base.sha256_file(lock_path), source_sha256=hashes,
                  runtime=dict(python=platform.python_version()), results=results, **prev.evaluate(results, RULES))
    report['verdict'] = verdict(report)
    tmp = json_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(json_path)
    md_path.write_text(markdown(report), encoding='utf-8')
    return json_path, md_path, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='stage', required=True)
    go = sub.add_parser('run')
    go.add_argument('--output', required=True)
    go.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    _, _, report = run(args.output, args.workers)
    print('verdict', report['verdict'])


if __name__ == '__main__':
    main()
