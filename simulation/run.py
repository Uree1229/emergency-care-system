"""B0/B1 짝지은 비교 실행 (최소 30 seed).

    python -m simulation.run                 # 기본 설정 30 seed
    python -m simulation.run --seeds 50 --sensitivity

결과: simulation_results/summary.json, simulation_results/summary.md
같은 seed끼리 차이(B1 - B0)를 구해 평균, 중앙값, B1이 나은 seed 비율을 보고한다.
배정된 환자만의 평균으로 결론 내지 않도록 미치료와 허용 시간 초과를 함께 적는다.
"""
import argparse
import json
import statistics
from pathlib import Path

from .sim import CONFIG, ParallelGreedyPolicy, SequentialPolicy, Simulation

METRICS = [  # (키, 설명, 작을수록 좋은가)
    ('mean_wait', '요청→치료 시작 평균(분)', True),
    ('p90_wait', '요청→치료 시작 90백분위(분)', True),
    ('g4.mean_wait', '높음 군 평균 대기(분)', True),
    ('g4.over_allowance', '높음 군 허용 시간 초과(명)', True),
    ('g2.over_allowance', '중간 군 허용 시간 초과(명)', True),
    ('untreated', '관찰 종료까지 미치료(명)', True),
    ('time_to_commit', '요청→목적지 확정 평균(분)', True),
    ('inquiries', '병원 문의 건수(병원 부담)', True),
    ('redirects', '목적지 변경(건)', True),
    ('extra_travel', '목적지 변경으로 늘어난 도착시각 합(분)', True),
]
SENSITIVITY = [
    ('미응답 10%', dict(nonresponse=0.1)),
    ('미응답 40%', dict(nonresponse=0.4)),
    ('돌발 유입 절반', dict(walkin_rate=0.003)),
    ('돌발 유입 2배', dict(walkin_rate=0.012)),
    ('환자 발생 1.5배', dict(arrival_rate=0.225)),
]


def get(m, key):
    for part in key.split('.'):
        m = m[part]
    return m


def compare(seeds, cfg):
    runs = {name: [] for name in ('B0', 'B1')}
    for s in seeds:
        runs['B0'].append(Simulation(s, SequentialPolicy(), cfg).run())
        runs['B1'].append(Simulation(s, ParallelGreedyPolicy(cfg['k_parallel']), cfg).run())
    table = {}
    for key, label, lower in METRICS:
        pairs = [(get(a, key), get(b, key)) for a, b in zip(runs['B0'], runs['B1'])
                 if get(a, key) is not None and get(b, key) is not None]
        diffs = [b - a for a, b in pairs]
        table[key] = dict(label=label, n=len(pairs),
                          b0_mean=statistics.mean(a for a, _ in pairs), b1_mean=statistics.mean(b for _, b in pairs),
                          diff_mean=statistics.mean(diffs), diff_median=statistics.median(diffs),
                          diff_sd=statistics.stdev(diffs) if len(diffs) > 1 else 0.0,
                          b1_better_share=sum((d < 0) if lower else (d > 0) for d in diffs) / len(diffs),
                          tie_share=sum(d == 0 for d in diffs) / len(diffs))
    return table, runs


def fmt(x):
    return f'{x:.1f}' if isinstance(x, float) else str(x)


def markdown(results, seeds):
    lines = ['# B0 vs B1 시뮬레이션 비교', '',
             f'seed {len(seeds)}개({seeds[0]}~{seeds[-1]}), 짝지은 차이 = B1 - B0. 음수면 B1이 작음(대부분 좋음).',
             '모든 분포와 비율은 실험 가정이며 임상적 효과를 주장하지 않는다. B2(솔버)는 아직 연결 전이다.', '',
             'B0: 순차 문의 + 최근접 수용 병원 / B1: 상위 3곳 병렬 문의 + 먼저 수용한 병원 + 상황 변경 알림', '']
    for title, table in results.items():
        lines += [f'## {title}', '', '| 지표 | B0 평균 | B1 평균 | 차이 평균 | 차이 중앙값 | 차이 SD | B1이 나은 seed | 같음 |',
                  '|---|---|---|---|---|---|---|---|']
        for key, r in table.items():
            lines.append(f"| {r['label']} | {fmt(r['b0_mean'])} | {fmt(r['b1_mean'])} | {fmt(r['diff_mean'])} | "
                         f"{fmt(r['diff_median'])} | {fmt(r['diff_sd'])} | {r['b1_better_share']:.0%} | {r['tie_share']:.0%} |")
        lines.append('')
    lines += ['## 해석 시 주의', '',
              '- 문의 건수는 병렬 문의로 늘어나는 병원 부담이다. 응답 지연 분포는 두 비교군이 같다고 가정했다.',
              '- 목적지 변경은 B1에만 있다(알림이 있어야 대체 문의를 한다). 늘어난 도착시각은 변경의 비용이다.',
              '- 도착했는데 자리가 없으면 두 비교군 모두 그 병원에서 대기한다. 실제 재이송은 모델링하지 않았다.']
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seeds', type=int, default=30)
    ap.add_argument('--sensitivity', action='store_true')
    ap.add_argument('--out', default='simulation_results')
    args = ap.parse_args()
    seeds = list(range(1, args.seeds + 1))
    results, raw = {}, {}
    results['기본 설정'], raw['기본 설정'] = compare(seeds, CONFIG)
    if args.sensitivity:
        for title, change in SENSITIVITY:
            results[title], raw[title] = compare(seeds, {**CONFIG, **change})
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    (out / 'summary.json').write_text(json.dumps(dict(config=CONFIG, seeds=seeds, results=results, runs=raw),
                                                 ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    (out / 'summary.md').write_text(markdown(results, seeds), encoding='utf-8')
    print(markdown(results, seeds))


if __name__ == '__main__':
    main()
