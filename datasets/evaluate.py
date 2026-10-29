"""스냅샷 배정 실험과 저장된 B0/B1 사건 재생. B2 전체 효과 실험은 아니다."""
import argparse
from collections import Counter
import json
from pathlib import Path

from simulation.sim import ParallelGreedyPolicy, SequentialPolicy, Simulation
from solver_improved import greedy, metrics, score, solve, validate

from .episodes import episode_config, world_from_episode
from .io import read_jsonl


def evaluate_allocation(path, limit=200, seconds=0.1, split=None):
    runs, counts = [], Counter()
    examined = 0
    for record in read_jsonl(path, split=split):
        if examined >= limit:
            break
        examined += 1
        data = record['observed']
        if len(data['patients']) > 64 or len(data['capacities']) > 32:
            counts['skipped_above_demo_size'] += 1
            continue
        baseline = greedy(data)
        plan, meta = solve(data, seconds=seconds)
        run = dict(id=record['id'], profile=record['profile'], split=record['split'], meta=meta)
        if plan is None:
            counts['needs_review'] += 1
        else:
            if not validate(data, plan):
                raise AssertionError('invalid observed plan')
            counts['observed_valid'] += 1
            hidden_valid = validate(record['actual'], plan)
            counts['actual_valid' if hidden_valid else 'actual_invalid'] += 1
            run.update(observed_metrics=metrics(data, plan), actual_feasible=hidden_valid)
            if baseline is not None:
                better = score(data, plan, 'severity_first') < score(data, baseline, 'severity_first')
                worse = score(data, plan, 'severity_first') > score(data, baseline, 'severity_first')
                if worse:
                    raise AssertionError('worse than feasible incumbent')
                counts['paired_with_greedy'] += 1
                counts['strictly_better' if better else 'tied'] += 1
                run.update(greedy_objective=score(data, baseline, 'severity_first'),
                           solver_objective=score(data, plan, 'severity_first'))
            else:
                counts['feasible_when_greedy_failed'] += 1
        runs.append(run)
    return dict(kind='allocation_evaluation', examined=examined, counts=dict(counts), runs=runs,
                limitations=['스냅샷 모델 내 평가이며 실제 치료시간 단축의 증거는 아님',
                             'actual은 숨겨진 실제 상태에서 실행 가능한지 검사하는 용도. 추가 정보를 가진 정책과의 공정 비교가 아님'])


def evaluate_episodes(path, limit=20, split=None):
    runs = []
    for record in read_jsonl(path, split=split):
        if len(runs) >= limit:
            break
        cfg = episode_config(record)
        result = dict(id=record['id'], profile=record['profile'], split=record['split'])
        for name, policy in (('B0', SequentialPolicy()), ('B1', ParallelGreedyPolicy(cfg['k_parallel']))):
            result[name] = Simulation(record['seed'], policy, cfg, world=world_from_episode(record)).run()
        runs.append(result)
    return dict(kind='episode_replay', runs=runs, comparisons=['B0', 'B1'],
                note='같은 사건을 두 정책에 제공. B2는 아직 시뮬레이터에 연결되지 않음',
                limitations=['기존 B0/B1은 후보 정렬과 실제 이동에 같은 이동시간 함수를 사용하므로 이동시간 추정 오차를 숨긴 정책 비교가 아님'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--kind', choices=['allocation', 'episode'], default='allocation')
    ap.add_argument('--input', default='datasets/generated')
    ap.add_argument('--output', default='data/dataset_evaluation.json')
    ap.add_argument('--limit', type=int, default=200)
    ap.add_argument('--seconds', type=float, default=0.1)
    ap.add_argument('--split', choices=['train', 'validation', 'test'])
    args = ap.parse_args()
    if args.limit < 1 or not 0 <= args.seconds <= 30:
        ap.error('limit는 양수, seconds는 0~30')
    root = Path(args.input)
    result = (evaluate_allocation(root / 'allocation.jsonl.gz', args.limit, args.seconds, args.split)
              if args.kind == 'allocation' else evaluate_episodes(root / 'episodes.jsonl.gz', args.limit, args.split))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'runs'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
