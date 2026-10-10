"""배정 알고리즘 비교·조정·최종 평가 실행기 (검증계획 2026-10-10 5~9절).

단계
    python -m simulation.experiment compare            # 1·2단계: 같은 문의 정책에서 R0~R3 비교 (train)
    python -m simulation.experiment tune --rule R1     # 4단계: 동시 문의 수·확정 대기·탐색 반경 조정 (train → validation)
    python -m simulation.experiment ablate --policy simulation_results/experiments/frozen_policy.json
                                                       # 5단계: 조건 변경 대응 켬/끔 비교 (validation)
    python -m simulation.experiment freeze --rule R1 --k 3 --wait 2   # 최종 정책 고정
    python -m simulation.experiment final              # 6단계: 고정한 정책을 test seed로 한 번 평가
    python -m simulation.experiment trace --seed 3     # 같은 사건을 두 정책으로 재생한 사건 기록 (재생 화면용)

원칙
- seed 분할은 datasets.io.partition과 같다(train 80%, validation 10%, test 10%).
- 비교는 같은 seed·같은 시나리오끼리 짝지어 차이(정책 - 기준)를 보고, 95% 구간은 합성 시나리오 안의 변동만 뜻한다.
- 평균 개선과 함께 나빠진 seed 비율, 미배정·미도착 수를 같이 적는다.
- 공식 선정시간(전북 8분 40초)은 모델의 현실성 점검용 참고값이며, 개선율은 같은 조건의 정책 비교로만 계산한다.
- final은 freeze로 고정한 정책만 평가한다. 결과를 보고 다시 조정했다면 그 test seed는 개발용으로 취급한다.
"""
import argparse
import json
import math
import statistics
from itertools import product
from pathlib import Path

from datasets.io import partition

from .sim import CONFIG, Policy, Simulation

OUT = Path('simulation_results/experiments')
FROZEN = OUT / 'frozen_policy.json'
OFFICIAL_SELECTION_MINUTES = 8 + 40 / 60    # 전북 시범사업 병원 선정 시간(보건복지부 2026.6.22)

SCENARIOS = {
    'normal': {},
    'slow_response': dict(resp_median=6.0),
    'low_response': dict(nonresponse=0.52),            # 2025년 119구급스마트시스템 응답률 47.96% 수준
    'surge': dict(arrival_rate=0.45, init_occupancy=0.85),
    'resource_change': dict(outage_rate=0.008, walkin_rate=0.012),
    'compound': dict(arrival_rate=0.35, init_occupancy=0.8, nonresponse=0.4, outage_rate=0.006),
}
METRICS = [  # (키, 설명, 작을수록 좋은가)
    ('unassigned', '관찰 종료 시 미배정(명)', True),
    ('not_arrived', '관찰 종료 시 미도착(명)', True),
    ('time_to_commit', '요청→확정 평균(분)', True),
    ('commit_p95', '요청→확정 P95(분)', True),
    ('arrive_mean', '요청→최종 병원 도착 평균(분)', True),
    ('arrive_p95', '요청→최종 병원 도착 P95(분)', True),
    ('mean_wait', '요청→치료 시작 평균(분, 보조)', True),
    ('inquiries_per_patient', '환자당 문의 수', True),
    ('max_hospital_inquiries', '한 병원 최대 문의 수', True),
    ('redirects', '목적지 변경(건)', True),
    ('decision_ms_mean', '결정 계산시간 평균(ms)', True),
]


def seeds_for(split, total=200):
    return [s for s in range(1, total + 1) if partition(s) == split]


def run_one(spec, seed, scenario):
    cfg = {**CONFIG, **SCENARIOS[scenario]}
    return Simulation(seed, Policy(**spec), cfg).run()


def evaluate(specs, seeds, scenarios):
    """{정책 이름: {시나리오: [지표 dict...]}}"""
    out = {}
    for spec in specs:
        name = Policy(**spec).name
        out[name] = {sc: [run_one(spec, s, sc) for s in seeds] for sc in scenarios}
    return out


def paired(base_runs, runs, key, lower=True):
    pairs = [(a[key], b[key]) for a, b in zip(base_runs, runs) if a[key] is not None and b[key] is not None]
    if not pairs:
        return None
    diffs = [b - a for a, b in pairs]
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
    half = 1.96 * sd / math.sqrt(len(diffs))
    better = sum((d < 0) if lower else (d > 0) for d in diffs) / len(diffs)
    worse = sum((d > 0) if lower else (d < 0) for d in diffs) / len(diffs)
    return dict(n=len(diffs), base=statistics.mean(a for a, _ in pairs), value=statistics.mean(b for _, b in pairs),
                diff=mean, ci=(mean - half, mean + half), better=better, worse=worse)


def summarize(results, reference):
    table = {}
    for name, by_sc in results.items():
        table[name] = {}
        for sc, runs in by_sc.items():
            base = results[reference][sc]
            table[name][sc] = {key: paired(base, runs, key, lower) for key, _, lower in METRICS}
    return table


def fmt(x, digits=2):
    return '-' if x is None else (f'{x:.{digits}f}' if isinstance(x, float) else str(x))


def markdown(title, table, reference, seeds, split, notes=()):
    lines = [f'# {title}', '', f'split={split}, seed {len(seeds)}개, 기준 정책={reference}. 차이 = 정책 - 기준(같은 seed 짝).',
             '95% 구간은 합성 시나리오 안의 변동이며 현실의 불확실성 전체가 아니다. 모든 분포·비율은 실험 가정이다.', '']
    lines += [f'- {n}' for n in notes] + ([''] if notes else [])
    scenarios = list(next(iter(table.values())).keys())
    for sc in scenarios:
        lines += [f'## 시나리오: {sc}', '', '| 지표 | ' + ' | '.join(table) + ' |', '|---|' + '---|' * len(table)]
        for key, label, _ in METRICS:
            cells = []
            for name in table:
                r = table[name][sc][key]
                if r is None:
                    cells.append('-')
                elif name == reference:
                    cells.append(fmt(r['value']))
                else:
                    cells.append(f"{fmt(r['value'])} ({r['diff']:+.2f}, 구간 {r['ci'][0]:+.2f}~{r['ci'][1]:+.2f}, "
                                 f"나쁨 {r['worse']:.0%})")
            lines.append(f'| {label} | ' + ' | '.join(cells) + ' |')
        lines.append('')
    return '\n'.join(lines) + '\n'


def save(name, payload, md):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{name}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    (OUT / f'{name}.md').write_text(md, encoding='utf-8')
    print(md)
    print(f'저장: {OUT / name}.md')


def realism_note(results, name):
    runs = results[name].get('normal') or next(iter(results[name].values()))
    commit = statistics.mean(r['time_to_commit'] for r in runs)
    return (f'현실성 점검: {name}의 normal 요청→확정 평균 {commit:.1f}분, 공식 전북 병원 선정시간 '
            f'{OFFICIAL_SELECTION_MINUTES:.1f}분(참고값, 보정·개선율 계산에 쓰지 않음).')


# ---------------------------------------------------------------- 단계

def cmd_compare(a):
    seeds = seeds_for(a.split, a.total)
    specs = [dict(rule=r, k=a.k, wait=a.wait) for r in ('R0', 'R1', 'R2', 'R3')]
    results = evaluate(specs, seeds, a.scenarios)
    ref = Policy(**specs[0]).name
    table = summarize(results, ref)
    save(f'compare_{a.split}', dict(stage='compare', split=a.split, seeds=seeds, specs=specs, table=table),
         markdown('배정 규칙 비교 (R0~R3, 같은 문의 정책)', table, ref, seeds, a.split,
                  [realism_note(results, ref), f'공통 문의 정책: 동시 {a.k}곳, 확정 대기 {a.wait}분.']))


def cmd_tune(a):
    train, valid = seeds_for('train', a.total)[:a.tune_seeds], seeds_for('validation', a.total)
    grid = [dict(rule=a.rule, k=k, wait=w, near_minutes=r)
            for k, w, r in product(a.ks, a.waits, a.radii)]
    rows = []
    for spec in grid:
        runs = {sc: [run_one(spec, s, sc) for s in train] for sc in a.scenarios}
        per_sc = {sc: dict(arrive=statistics.mean(r['arrive_mean'] for r in rs if r['arrive_mean'] is not None),
                           inquiries=statistics.mean(r['inquiries_per_patient'] for r in rs),
                           unassigned=statistics.mean(r['unassigned'] for r in rs)) for sc, rs in runs.items()}
        rows.append(dict(spec=spec, name=Policy(**spec).name, per_scenario=per_sc))
    # 시나리오별 최선 대비 손해(후회)의 최댓값이 작은 설정 = 조건이 바뀌어도 고르게 좋은 설정
    for sc in a.scenarios:
        best = min(r['per_scenario'][sc]['arrive'] for r in rows)
        for r in rows:
            r.setdefault('regret', {})[sc] = r['per_scenario'][sc]['arrive'] - best
    for r in rows:
        r['max_regret'] = max(r['regret'].values())
        r['mean_inquiries'] = statistics.mean(v['inquiries'] for v in r['per_scenario'].values())
    allowed = [r for r in rows if r['mean_inquiries'] <= a.max_inquiries] or rows
    pick = min(allowed, key=lambda r: (r['max_regret'], r['mean_inquiries']))
    # 문의 부담과 도착시간의 경계(더 개선할 수 없는 설정들)
    front = [r for r in rows if not any(o['max_regret'] <= r['max_regret'] and o['mean_inquiries'] <= r['mean_inquiries']
                                        and (o['max_regret'], o['mean_inquiries']) != (r['max_regret'], r['mean_inquiries'])
                                        for o in rows)]
    ref_spec = dict(rule='R0', k=3, wait=0)          # 기준: 동시 3곳 + 첫 수용 즉시 확정
    check = evaluate([ref_spec, pick['spec']], valid, a.scenarios)
    ref = Policy(**ref_spec).name
    table = summarize(check, ref)
    lines = ['# 문의 정책 조정', '', f'규칙 {a.rule}, train seed {len(train)}개로 조정, validation seed {len(valid)}개로 확인.',
             f'선택 기준: 환자당 문의 {a.max_inquiries}건 이하 중 시나리오별 최대 후회(최선 대비 평균 도착 손해, 분)가 가장 작은 설정.',
             '', '| 설정 | 최대 후회(분) | 평균 문의/환자 | 경계 |', '|---|---|---|---|']
    for r in sorted(rows, key=lambda r: (r['max_regret'], r['mean_inquiries'])):
        lines.append(f"| {r['name']} | {r['max_regret']:.2f} | {r['mean_inquiries']:.2f} | {'●' if r in front else ''} |")
    lines += ['', f"**선택:** {pick['name']}", '']
    md = '\n'.join(lines) + '\n' + markdown('선택한 설정의 validation 확인', table, ref, valid, 'validation')
    save(f'tune_{a.rule}', dict(stage='tune', rows=rows, pick=pick, validation=table), md)


def cmd_freeze(a):
    spec = dict(rule=a.rule, k=a.k, wait=a.wait, near_minutes=a.radius, expand_after=a.expand_after, notify=not a.no_notify)
    ref = dict(rule='R0', k=a.k, wait=0)
    OUT.mkdir(parents=True, exist_ok=True)
    FROZEN.write_text(json.dumps(dict(policy=spec, reference=ref, note='final 전에 고정. 결과를 본 뒤 바꾸면 새 test seed 필요'),
                                 ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'고정: {Policy(**spec).name} (기준 {Policy(**ref).name}) → {FROZEN}')


def load_frozen(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return data['policy'], data['reference']


def cmd_ablate(a):
    spec, _ = load_frozen(a.policy)
    seeds = seeds_for(a.split, a.total)
    on, off = dict(spec, notify=True), dict(spec, notify=False)
    results = evaluate([off, on], seeds, a.scenarios)
    ref = Policy(**off).name
    save(f'ablate_{a.split}', dict(stage='ablate', split=a.split, seeds=seeds, table=summarize(results, ref)),
         markdown('조건 변경 대응 켬/끔 비교', summarize(results, ref), ref, seeds, a.split,
                  ['기준=대응 끔. 같은 배정 규칙·문의 정책에서 영향 환자 재확인·대체 문의만 켰을 때의 차이.']))


def cmd_final(a):
    spec, ref_spec = load_frozen(a.policy)
    seeds = seeds_for('test', a.total)
    results = evaluate([ref_spec, spec], seeds, a.scenarios)
    ref = Policy(**ref_spec).name
    table = summarize(results, ref)
    save('final_test', dict(stage='final', seeds=seeds, policy=spec, reference=ref_spec, table=table),
         markdown('최종 정책 평가 (test, 개발·조정에 쓰지 않은 seed)', table, ref, seeds, 'test',
                  [realism_note(results, ref), '이 결과를 보고 정책을 다시 바꾸면 이 seed는 개발용이 되며 새 test seed가 필요하다.']))


def cmd_trace(a):
    cfg = {**CONFIG, **SCENARIOS[a.scenario]}
    specs = [dict(rule='R0', k=a.k, wait=0), json.loads(a.right) if a.right else dict(rule='R1', k=a.k, wait=2)]
    out = dict(seed=a.seed, scenario=a.scenario, note='알고리즘 입력과 평가용 실제 상태를 구분해 보여줄 것', runs=[])
    for spec in specs:
        sim = Simulation(a.seed, Policy(**spec), cfg, record=True)
        metrics = sim.run()
        out['runs'].append(dict(policy=Policy(**spec).spec(), metrics=metrics, events=sim.log,
                                hospitals=[dict(id=h.id, loc=h.loc) for h in sim.hospitals.values()]))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'trace_{a.scenario}_{a.seed}.json'
    path.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding='utf-8')
    print(f"사건 {[len(r['events']) for r in out['runs']]}개 기록 → {path}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p, split='train'):
        p.add_argument('--split', default=split, choices=['train', 'validation', 'test'])
        p.add_argument('--total', type=int, default=200, help='seed 1..total을 partition으로 나눔')
        p.add_argument('--scenarios', nargs='+', default=list(SCENARIOS), choices=list(SCENARIOS))
    c = sub.add_parser('compare'); common(c); c.add_argument('--k', type=int, default=3); c.add_argument('--wait', type=int, default=2)
    t = sub.add_parser('tune'); common(t)
    t.add_argument('--rule', default='R1', choices=['R0', 'R1', 'R2', 'R3'])
    t.add_argument('--ks', type=int, nargs='+', default=[1, 2, 3, 5])
    t.add_argument('--waits', type=int, nargs='+', default=[0, 1, 2, 4])
    t.add_argument('--radii', type=lambda v: None if v == 'none' else int(v), nargs='+', default=[None, 15, 25])
    t.add_argument('--tune-seeds', type=int, default=40)
    t.add_argument('--max-inquiries', type=float, default=4.0, help='허용 환자당 평균 문의 수 (조정 전에 정함)')
    f = sub.add_parser('freeze')
    f.add_argument('--rule', default='R1'); f.add_argument('--k', type=int, default=3); f.add_argument('--wait', type=int, default=2)
    f.add_argument('--radius', type=int, default=None); f.add_argument('--expand-after', type=int, default=None)
    f.add_argument('--no-notify', action='store_true')
    b = sub.add_parser('ablate'); common(b, 'validation'); b.add_argument('--policy', default=str(FROZEN))
    n = sub.add_parser('final'); common(n, 'test'); n.add_argument('--policy', default=str(FROZEN))
    r = sub.add_parser('trace')
    r.add_argument('--seed', type=int, default=3); r.add_argument('--scenario', default='resource_change', choices=list(SCENARIOS))
    r.add_argument('--k', type=int, default=3); r.add_argument('--right', default=None, help='오른쪽 정책 JSON')
    a = ap.parse_args()
    dict(compare=cmd_compare, tune=cmd_tune, freeze=cmd_freeze, ablate=cmd_ablate, final=cmd_final, trace=cmd_trace)[a.cmd](a)


if __name__ == '__main__':
    main()
