"""31차 실험: 빠른 거절(받을 수 없는 병원이 상세 검토 없이 15초 안에 거절) 업무 흐름 효과 확인.
선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_fastdecline run --output simulation_results/inquiry_lab_fastdecline_validation_20261028

사전 고정:
- 근거: 30차에서 병원 검토 시간이 가장 큰 지렛대였다. 25차 개발 seed 진단(판정에 쓰지 않음)에서 거절 회신의 50%가 15초 안에
  끝나면 최종 구성(G)이 혼합 −20.9·다수 −34.9초, 100%면 −32.6·−72.3초였다. 고정3 + 같은 운영 수단(R)은 50%에서 −11.5·−45.6초.
- 구성: 같은 시나리오에서 G·G 빠른 거절 50%·G 빠른 거절 100%·R·R 빠른 거절 50%. 빠른 거절은 수용하지 않는 잠재 회신(무응답 제외)
  중 해당 비율의 검토 시간을 min(원래, 15초)로 바꾼다. 비율 선택은 (시나리오, 환자, 병원) 해시로 고정한다.
- seed(처음 사용): 혼합 2320001–2320400, 다수 2330001–2330200.
- 판정: 혼합·다수 모두 ΔJ(G 빠른 거절 50% − G) 95% 구간 상한 < 0이면 효과 확인. 나머지는 판정 없이 보고한다.
- 빠른 거절은 병원 화면·업무 흐름의 설계안이다. 실제로 몇 %가 빠른 거절이 될지는 측정하지 않았다(50%·100%는 가정).
- 실행 전에 설계와 소스 해시를 잠금 파일로 먼저 쓴다. 기존 결과가 있으면 덮어쓰지 않는다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_final2 as prev, experiment_wide as wide,
               hold2_runner, scenarios)

MIX = tuple(range(2320001, 2320401))
MASS = tuple(range(2330001, 2330201))
FAST_SECONDS = 15
FOCUS, RULE = prev.G_POLICY, prev.G_RULE
ARMS = (('G', '최종 구성', FOCUS, 0.0), ('G50', '최종 구성 + 빠른 거절 50%', FOCUS, 0.5),
        ('G100', '최종 구성 + 빠른 거절 100%', FOCUS, 1.0), ('R', '고정3 + 같은 운영 수단', 'fixed_3', 0.0),
        ('R50', '고정3 + 같은 운영 수단 + 빠른 거절 50%', 'fixed_3', 0.5))
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/experiment_fastdecline.py',)
SUCCESS_RULE = '혼합·다수 모두 ΔJ(G 빠른 거절 50% − G) 95% 구간 상한 < 0이면 효과 확인.'


def fast_declines(scenario, share):
    """거절(무응답 아님) 잠재 회신 중 share 비율의 검토 시간을 FAST_SECONDS 이하로 줄인 시나리오."""
    if not share:
        return scenario
    potentials = []
    for p in scenario['potentials']:
        q = dict(p)
        if not q['accept'] and not q['no_response']:
            key = f"{scenario['id']}|{q['patient_id']}|{q['hospital_id']}".encode()
            if int(sha256(key).hexdigest()[:8], 16) / 0xFFFFFFFF < share:
                q['service_seconds'] = min(q['service_seconds'], FAST_SECONDS)
        potentials.append(q)
    return dict(scenario, potentials=potentials)


def run_case(task):
    kind, seed = task
    eat_capacity_policy.install([])
    eat_seen_policy.install([FOCUS])
    scenario = wide.build(kind, seed)
    branches = {}
    for key, _, policy, share in ARMS:
        b = hold2_runner.run_scenario(fast_declines(scenario, share), [(policy, RULE)])['branches'][0]
        row = wide.prev.prev.compact(b)
        row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
        branches[key] = row
    return dict(kind=kind, seed=seed, scenario_id=scenario['id'], mix=scenario.get('mix'),
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario), branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def evaluate(results):
    report = dict(groups={})
    for kind in ('mix', 'mass'):
        rs = [r for r in results if r['kind'] == kind]
        rows = {}
        for key, label, _, _ in ARMS:
            m = base.policy_metrics(rs, key)
            rows[key] = dict(label=label, J=m['J'], failed=m['failed'], patients=m['patients'],
                             review_work_seconds_per_patient=m['review_work_seconds_per_patient'],
                             inquiries_per_patient=m['inquiries_per_patient'])
        comparisons = {name: base.paired(rs, a, b) for name, a, b in (('G50_minus_G', 'G50', 'G'), ('G100_minus_G', 'G100', 'G'),
                                                                       ('R50_minus_R', 'R50', 'R'), ('G_minus_R', 'G', 'R'),
                                                                       ('G50_minus_R50', 'G50', 'R50'))}
        by_arrival = {a: base.paired([x for x in rs if x['mix']['arrival'] == a], 'G50', 'G')
                      for a in sorted({x['mix']['arrival'] for x in rs})}
        report['groups'][kind] = dict(rows=rows, comparisons=comparisons, G50_minus_G_by_arrival=by_arrival)
    return report


def verdict(report):
    rows = {k: report['groups'][k]['comparisons']['G50_minus_G'] for k in ('mix', 'mass')}
    return dict(confirmed=all(v['ci95'][1] < 0 for v in rows.values()),
                delta_J={k: dict(delta_J=v['delta_J_mean'], ci95=v['ci95']) for k, v in rows.items()})


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v = report['verdict']
    lines = ['# 빠른 거절 업무 흐름 효과 확인 실험', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {SUCCESS_RULE} → 효과 확인 **{v["confirmed"]}**', '']
    for k, row in v['delta_J'].items():
        lines.append(f"- {k}: G 빠른 거절 50% − G {_f(row['delta_J'])} [{_f(row['ci95'][0])}, {_f(row['ci95'][1])}]")
    names = dict(mix='보정 혼합', mass='다수 사상자')
    for kind, block in report['groups'].items():
        lines += ['', f'## {names[kind]}', '', '| 구성 | J(초) | 실패 | 환자당 문의 | 검토 업무(초/환자) |', '|---|---:|---:|---:|---:|']
        for key, row in block['rows'].items():
            lines.append(f"| {row['label']} | {_f(row['J'])} | {row['failed']} | {_f(row['inquiries_per_patient'], 2)} | "
                         f"{_f(row['review_work_seconds_per_patient'])} |")
        lines += ['', '| 비교 | ΔJ | 95% 구간 |', '|---|---:|---|']
        for name, c in block['comparisons'].items():
            lines.append(f"| {name.replace('_minus_', ' − ')} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
        lines += ['', '| 도착 형태 | G 빠른 거절 50% − G | 95% 구간 |', '|---|---:|---|']
        for a, c in block['G50_minus_G_by_arrival'].items():
            lines.append(f"| {a} | {_f(c['delta_J_mean'])} | [{_f(c['ci95'][0])}, {_f(c['ci95'][1])}] |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 빠른 거절 비율(50%·100%)과 15초는 가정이다. 실제 병원에서 상세 검토 없이 거절할 수 있는 비율은 측정하지 않았다.',
        '- 빠른 거절이 잘못된 거절(검토했다면 수용했을 환자)을 만들 위험은 넣지 않았다. 수용 회신의 검토 시간은 그대로다.']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(arms=[dict(key=k, label=l, policy=p, fast_share=s) for k, l, p, s in ARMS], rule=RULE,
                  fast_seconds=FAST_SECONDS, seeds=dict(mix=[MIX[0], MIX[-1]], mass=[MASS[0], MASS[-1]]), success_rule=SUCCESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_fastdecline_20261028', design=design,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    tasks = [('mass', s) for s in MASS] + [('mix', s) for s in MIX]
    results, elapsed = run_all(tasks, workers)
    if {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES} != hashes:
        raise RuntimeError('source changed during the confirmatory run')
    report = dict(stage='confirm', generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), scenario_count=len(results),
                  elapsed_seconds=elapsed, design=design, lock=base.relative_path(lock_path),
                  lock_sha256=base.sha256_file(lock_path), source_sha256=hashes,
                  runtime=dict(python=platform.python_version()), results=results, **evaluate(results))
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
