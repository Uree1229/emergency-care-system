"""34차 실험: 다수 사상자 중증 환자 적극 문의 확인 실험(32차 후보, 표본 확대). 선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_priority2 run --output simulation_results/inquiry_lab_priority2_validation_20261028

사전 고정:
- 근거: 32차에서 중증 환자 적극 문의(eatkp_l0_m6: 중증도 높음이면 λ0·동시 6곳)는 다수 사상자 중증 환자 ΔJ가 개발 −35.8초,
  검증 −18.8초 [−37.6, −0.2]였고 전체는 −2.2·−1.9초였다. 보통 상황에서는 효과가 없었다. 판정 미달은 표본이 작은 묶음의 넓은
  구간 탓이었다(보통 상황 중증 273명, 다수 전체 상한 +5.7초). 다수 사상자 표본을 두 배로 늘려 같은 후보를 한 번 확인한다.
- 환경·구성: 32차와 같다(26차 최종 구성 G, 중증도 높음 20%·중간 50%·낮음 30%, priority_runner). 기준 eatks_seenbase_k10_m4,
  후보 eatkp_l0_m6.
- seed(처음 사용): 다수 2400001–2400400, 혼합 2410001–2410400.
- 판정: 다수 사상자 중증 환자 ΔJ 95% 구간 상한 < 0, 다수 사상자 전체 ΔJ 상한 < +5초, 보통 상황 전체 ΔJ 상한 < +5초이면 채택 후보.
- 실행 전에 설계와 소스 해시를 잠금 파일로 먼저 쓴다. 기존 결과가 있으면 덮어쓰지 않는다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import time

from . import experiment as base, experiment_priority as prev

MASS = tuple(range(2400001, 2400401))
MIX = tuple(range(2410001, 2410401))
BASE_ARM = prev.BASE_ARM
CANDIDATE = ('eatkp_l0_m6', prev.G_RULE)
ARMS = (BASE_ARM, CANDIDATE)
MARGIN = 5.0
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/experiment_priority2.py',)
SUCCESS_RULE = (f'다수 사상자 중증 환자 ΔJ 상한 < 0, 다수 사상자 전체 ΔJ 상한 < +{MARGIN:.0f}초, '
                f'보통 상황 전체 ΔJ 상한 < +{MARGIN:.0f}초이면 채택 후보.')


def verdict(report):
    mass = report['groups']['mass'][prev.arm(*CANDIDATE)]['versus_base']
    mix = report['groups']['mix'][prev.arm(*CANDIDATE)]['versus_base']
    upper = lambda v: float('inf') if v['ci95'][1] is None else v['ci95'][1]
    checks = dict(mass_high_better=upper(mass['high']) < 0, mass_overall_noninferior=upper(mass['all']) < MARGIN,
                  mix_overall_noninferior=upper(mix['all']) < MARGIN)
    return dict(checks, adopted=all(checks.values()),
                delta_J={k: {part: dict(delta_J=v[part]['delta_J_mean'], ci95=v[part]['ci95']) for part in ('all', 'high', 'mid', 'low')}
                         for k, v in (('mix', mix), ('mass', mass))})


def markdown(report):
    v = report['verdict']
    lines = ['# 다수 사상자 중증 환자 적극 문의 확인 실험', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {SUCCESS_RULE} → 채택 후보 **{v["adopted"]}**', '']
    for k, parts in v['delta_J'].items():
        lines.append(f"- {k}: " + ', '.join(f"{name} {prev._f(parts[part]['delta_J'])} [{prev._f(parts[part]['ci95'][0])}, "
                                            f"{prev._f(parts[part]['ci95'][1])}]"
                                            for part, name in (('all', '전체'), ('high', '중증'), ('mid', '중간'), ('low', '경증'))))
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for kind, rows in report['groups'].items():
        lines += ['', f'## {names[kind]}', '', '| 구성 | 전체 J | 중증 J | 중간 J | 경증 J | 전체 ΔJ [95%] | 중증 ΔJ [95%] | 실패 |',
                  '|---|---:|---:|---:|---:|---|---|---:|']
        for row in rows.values():
            by, d = row['J_by_level'], row.get('versus_base')
            fmt = lambda part: (f"{prev._f(d[part]['delta_J_mean'])} [{prev._f(d[part]['ci95'][0])}, {prev._f(d[part]['ci95'][1])}]"
                                if d else '—')
            lines.append(f"| {row['label']} | {prev._f(row['J'])} | {prev._f(by['high'])} | {prev._f(by['mid'])} | {prev._f(by['low'])} | "
                         f"{fmt('all')} | {fmt('high')} | {row['failed']} |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS] + [
        '- 중증도 비율(20·50·30%)은 가정이며, 수용률·검토 시간이 중증도와 무관하다고 본다.']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(arms=[list(a) for a in ARMS], shares=[list(s) for s in prev.SHARES],
                  seeds=dict(mass=[MASS[0], MASS[-1]], mix=[MIX[0], MIX[-1]]), success_rule=SUCCESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_priority2_20261028', design=design,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    results, elapsed = prev.run_all(prev._tasks(MIX, MASS, ARMS), workers)
    if {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES} != hashes:
        raise RuntimeError('source changed during the confirmatory run')
    report = dict(stage='confirm', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), scenario_count=len(results),
                  elapsed_seconds=elapsed, design=design, lock=base.relative_path(lock_path),
                  lock_sha256=base.sha256_file(lock_path), source_sha256=hashes,
                  runtime=dict(python=platform.python_version()), results=results, **prev.evaluate(results, ARMS))
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
