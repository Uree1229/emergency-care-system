"""30차 실험: 병원 운영 지렛대와 나쁜 환경에서의 강건성 확인. 선택 없이 사전 고정 → 잠금 → 처음 쓰는 seed로 한 번 실행한다.

python -m inquiry_lab.experiment_levers run --output simulation_results/inquiry_lab_levers_validation_20261028

사전 고정:
- 근거: 25차 개발 seed 진단(판정에 쓰지 않음)에서 26차 최종 구성(G)에 병원 쪽 개선을 하나씩 주면 검토 시간 20% 단축이
  혼합 −40.8·다수 −54.6초로 가장 컸다(보조 즉시 합류 −19.6·−32.7, 취소 즉시·전원 중단 −14.3·−13.5, 문의함 순서 100% −3.0·−1.1).
  나쁜 환경(회신률 낮음·검토 느림·수용 드묾)에서도 G는 현행보다 크게 나았다. 이를 새 seed로 확인한다.
- 1부(지렛대, 보통 환경): G와 병원 쪽 개선 6가지(보조 합류 30초·즉시, 취소 즉시·전원 중단, 문의함 순서 100% 따름,
  검토 시간 20% 단축, 모두 함께). seed 혼합 2270001–2270400, 다수 2280001–2280200.
- 2부(강건성): 회신률 0.6, 검토 시간 중앙값 110초, 수용률 0.15 환경마다 A 현행(fixed_3·base)·R 고정3 + G의 운영 수단·G.
  seed 혼합 2300001–2300200, 다수 2310001–2310100(세 환경 공통).
- 판정(사전 고정): 1부는 지렛대마다 혼합·다수 ΔJ(지렛대 − G) 95% 구간 상한 < 0이면 '효과 확인'. 2부는 모든 환경·묶음에서
  ΔJ(G − A) 상한 < 0이면 '강건성 확인'. G − R은 판정 없이 보고한다.
- 지렛대는 서버가 정할 수 없는 병원 운영값이다. 결과는 병원·화면 설계의 우선순위 근거로만 쓴다.
- 실행 전에 설계와 소스 해시를 잠금 파일로 먼저 쓴다. 기존 결과가 있으면 덮어쓰지 않는다.
이전 단계 파일은 import만 하고 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import platform
import time

from . import (eat_capacity_policy, eat_seen_policy, experiment as base, experiment_final2 as prev, experiment_wide as wide,
               hold2_runner, scenarios)

LEVER_MIX = tuple(range(2270001, 2270401))
LEVER_MASS = tuple(range(2280001, 2280201))
STRESS_MIX = tuple(range(2300001, 2300201))
STRESS_MASS = tuple(range(2310001, 2310101))
FOCUS = prev.G_POLICY
G_RULE = prev.G_RULE
HEAD = 'od_k2_d60_l120+sg_n10_w120+ci_r30_q50+ord_spread_o100_c50+'
assert G_RULE.startswith(HEAD)
TAIL = G_RULE[len(HEAD):]


def operating(delay=60, notice=30, comply=50, follow=50):
    return f'od_k2_d{delay}_l120+sg_n10_w120+ci_r{notice}_q{comply}+ord_spread_o100_c{follow}+{TAIL}'


LEVERS = (('G', '최종 구성', operating(), 1.0),
          ('join30', '보조 합류 30초', operating(delay=30), 1.0),
          ('join0', '보조 즉시 합류', operating(delay=0), 1.0),
          ('cancel', '취소 즉시·전원 중단', operating(notice=0, comply=100), 1.0),
          ('order', '문의함 순서 100% 따름', operating(follow=100), 1.0),
          ('review', '검토 시간 20% 단축', operating(), 0.8),
          ('all', '모두 함께(합류 30초)', operating(delay=30, notice=0, comply=100, follow=100), 0.8))
ENVS = (('low_reply', '회신률 0.6', dict(REPLY_MEAN=0.6)),
        ('slow_review', '검토 시간 중앙값 110초', dict(SERVICE_MEDIAN=110.0)),
        ('scarce', '수용률 0.15', dict(ACCEPT_LEVELS={'low_scarcity': 0.15, 'medium_scarcity': 0.15, 'high_scarcity': 0.15})))
STRESS_ARMS = (('A', '현행(고정3)', 'fixed_3', 'base'), ('R', '고정3 + G의 운영 수단', 'fixed_3', G_RULE), ('G', '최종 구성', FOCUS, G_RULE))
assert G_RULE == operating()
SOURCE_FILES = prev.SOURCE_FILES + ('inquiry_lab/experiment_levers.py',)
LEVER_RULE = '1부: 지렛대마다 혼합·다수 ΔJ(지렛대 − G) 95% 구간 상한 < 0이면 효과 확인.'
STRESS_RULE = '2부: 모든 환경·묶음에서 ΔJ(G − A) 95% 구간 상한 < 0이면 강건성 확인.'


@contextmanager
def patched(changes):
    saved = {k: getattr(scenarios, k) for k in changes}
    for k, v in changes.items():
        setattr(scenarios, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(scenarios, k, v)


def _compact(b):
    row = wide.prev.prev.compact(b)
    row.update(silent_closed=b['silent_closed'], replans=b['replans'], holds=b['holds'])
    return row


def run_case(task):
    part, env, kind, seed = task
    eat_capacity_policy.install([])
    eat_seen_policy.install([FOCUS])
    branches = {}
    if part == 'lever':
        for key, _, rule, scale in LEVERS:
            with patched(dict(SERVICE_MEDIAN=scenarios.SERVICE_MEDIAN * scale)):
                scenario = wide.build(kind, seed)
            branches[key] = _compact(hold2_runner.run_scenario(scenario, [(FOCUS, rule)])['branches'][0])
        with patched({}):
            scenario = wide.build(kind, seed)
    else:
        changes = dict(next(c for k, _, c in ENVS if k == env))
        with patched(changes):
            scenario = wide.build(kind, seed)
        out = hold2_runner.run_scenario(scenario, [(p, r) for _, _, p, r in STRESS_ARMS])
        names = {f'{p}|{r}': key for key, _, p, r in STRESS_ARMS}
        branches = {names[f"{b['policy']}|{b['rule']}"]: _compact(b) for b in out['branches']}
    return dict(part=part, env=env, kind=kind, seed=seed, scenario_id=scenario['id'], mix=scenario.get('mix'),
                patient_count=len(scenario['patients']), oracle=scenarios.oracle(scenario), branches=branches)


def run_all(tasks, workers):
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_case, tasks, chunksize=1))
    return results, time.time() - started


def _tasks():
    tasks = [('lever', None, 'mass', s) for s in LEVER_MASS] + [('lever', None, 'mix', s) for s in LEVER_MIX]
    for env, _, _ in ENVS:
        tasks += [('stress', env, 'mass', s) for s in STRESS_MASS] + [('stress', env, 'mix', s) for s in STRESS_MIX]
    return tasks


def evaluate(results):
    report = dict(levers={}, stress={})
    for kind in ('mix', 'mass'):
        rs = [r for r in results if r['part'] == 'lever' and r['kind'] == kind]
        rows = {}
        for key, label, _, _ in LEVERS:
            m = base.policy_metrics(rs, key)
            row = dict(label=label, J=m['J'], failed=m['failed'], patients=m['patients'])
            if key != 'G':
                row['versus_G'] = base.paired(rs, key, 'G')
            rows[key] = row
        report['levers'][kind] = rows
    for env, label, _ in ENVS:
        block = {}
        for kind in ('mix', 'mass'):
            rs = [r for r in results if r['part'] == 'stress' and r['env'] == env and r['kind'] == kind]
            metrics = {key: base.policy_metrics(rs, key) for key, _, _, _ in STRESS_ARMS}
            block[kind] = dict(rows={k: dict(J=m['J'], failed=m['failed'], patients=m['patients'],
                                             inquiries_per_patient=m['inquiries_per_patient']) for k, m in metrics.items()},
                               G_minus_A=base.paired(rs, 'G', 'A'), G_minus_R=base.paired(rs, 'G', 'R'),
                               R_minus_A=base.paired(rs, 'R', 'A'))
        report['stress'][env] = dict(label=label, groups=block)
    return report


def verdict(report):
    levers = {key: all(report['levers'][k][key]['versus_G']['ci95'][1] < 0 for k in ('mix', 'mass'))
              for key, _, _, _ in LEVERS if key != 'G'}
    robust = {env: all(report['stress'][env]['groups'][k]['G_minus_A']['ci95'][1] < 0 for k in ('mix', 'mass'))
              for env, _, _ in ENVS}
    return dict(levers_confirmed=levers, robust_by_env=robust, robust=all(robust.values()))


def _f(value, digits=1):
    return '—' if value is None else f'{value:,.{digits}f}'


def markdown(report):
    v = report['verdict']
    lines = ['# 병원 운영 지렛대와 강건성 확인 실험', '',
             f"생성 {report['generated_at']} · 시나리오 {report['scenario_count']}개 · 실행 {report['elapsed_seconds']:.0f}초", '',
             f'**판정:** {LEVER_RULE} {STRESS_RULE}', '', '## 1부: 병원 운영 지렛대(보통 환경, G 대비)', '',
             '| 지렛대 | 혼합 ΔJ [95% 구간] | 다수 ΔJ [95% 구간] | 혼합 실패 | 다수 실패 | 효과 확인 |', '|---|---|---|---:|---:|---|']
    for key, label, _, _ in LEVERS:
        mix, mass = report['levers']['mix'][key], report['levers']['mass'][key]
        if key == 'G':
            lines.append(f"| {label}(기준) | J {_f(mix['J'])} | J {_f(mass['J'])} | {mix['failed']} | {mass['failed']} | — |")
            continue
        a, b = mix['versus_G'], mass['versus_G']
        lines.append(f"| {label} | {_f(a['delta_J_mean'])} [{_f(a['ci95'][0])}, {_f(a['ci95'][1])}] | "
                     f"{_f(b['delta_J_mean'])} [{_f(b['ci95'][0])}, {_f(b['ci95'][1])}] | {mix['failed']} | {mass['failed']} | "
                     f"{v['levers_confirmed'][key]} |")
    lines += ['', f"## 2부: 나쁜 환경에서의 강건성(강건성 확인 **{v['robust']}**)", '',
              '| 환경 | 묶음 | A J | R J | G J | G − A [95% 구간] | G − R [95% 구간] | 실패(A·R·G) |', '|---|---|---:|---:|---:|---|---|---|']
    for env, label, _ in ENVS:
        for kind in ('mix', 'mass'):
            g = report['stress'][env]['groups'][kind]
            rows, ga, gr = g['rows'], g['G_minus_A'], g['G_minus_R']
            lines.append(f"| {label} | {kind} | {_f(rows['A']['J'])} | {_f(rows['R']['J'])} | {_f(rows['G']['J'])} | "
                         f"{_f(ga['delta_J_mean'])} [{_f(ga['ci95'][0])}, {_f(ga['ci95'][1])}] | "
                         f"{_f(gr['delta_J_mean'])} [{_f(gr['ci95'][0])}, {_f(gr['ci95'][1])}] | "
                         f"{rows['A']['failed']}·{rows['R']['failed']}·{rows['G']['failed']} |")
    lines += ['', '## 한계', ''] + [f'- {x}' for x in base.LIMITATIONS + wide.prev.LIMITATIONS] + [
        '- 지렛대는 병원 운영값을 바꾼 가정이다. 검토 시간 단축은 시나리오의 검토 시간 중앙값을 0.8배로 둔 것이며, EAT 사전값은 바꾸지 않았다.',
        '- 나쁜 환경은 환경 평균만 바꾼 것이다. 정책의 사전값(회신 0.79·수용 0.40·검토 66초)은 그대로 두어 어긋난 가정에서의 강건성을 본다.']
    return '\n'.join(lines) + '\n'


def run(output, workers):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: base.sha256_file(base.ROOT / name) for name in SOURCE_FILES}
    design = dict(levers=[dict(key=k, label=l, rule=r, review_scale=s) for k, l, r, s in LEVERS],
                  envs=[dict(key=k, label=l, changes={a: b for a, b in c.items()}) for k, l, c in ENVS],
                  stress_arms=[dict(key=k, label=l, policy=p, rule=r) for k, l, p, r in STRESS_ARMS],
                  seeds=dict(lever_mix=[LEVER_MIX[0], LEVER_MIX[-1]], lever_mass=[LEVER_MASS[0], LEVER_MASS[-1]],
                             stress_mix=[STRESS_MIX[0], STRESS_MIX[-1]], stress_mass=[STRESS_MASS[0], STRESS_MASS[-1]]),
                  lever_rule=LEVER_RULE, stress_rule=STRESS_RULE)
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='inquiry_lab_levers_20261028', design=design,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    results, elapsed = run_all(_tasks(), workers)
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
