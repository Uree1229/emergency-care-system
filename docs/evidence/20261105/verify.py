"""Verify stored synthetic presentation evidence; does not run a solver."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent


def read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def totals(rows, fields):
    result = defaultdict(Counter)
    for row in rows:
        for field in fields:
            result[row['policy']][field] += row[field]
    return {policy: dict(values) for policy, values in result.items()}


def main():
    manifest = read('manifest.json')
    for name, expected in manifest['files'].items():
        assert name in {'allocation-seed9.json', 'resource-workload.json'}
        actual = hashlib.sha256((HERE / name).read_bytes()).hexdigest()
        assert actual == expected, name + ' checksum differs'

    allocation = read('allocation-seed9.json')
    paired = [row for row in allocation['runs']
              if isinstance(row.get('solver_objective'), list)
              and isinstance(row.get('greedy_objective'), list)]
    better = [row for row in paired
              if tuple(row['solver_objective']) < tuple(row['greedy_objective'])]
    tied = [row for row in paired if row['solver_objective'] == row['greedy_objective']]
    status = Counter(row['meta']['status'] for row in allocation['runs'])
    hidden_invalid = sum(row.get('actual_feasible') is False for row in paired)
    assert allocation['examined'] == 50 and len(allocation['runs']) == 49
    assert (len(paired), len(better), len(tied)) == (46, 20, 26)
    assert (status['OPTIMAL'], status['FEASIBLE'], status['NEEDS_REVIEW']) == (26, 20, 3)
    assert sum(row['meta'].get('lex_optimal') is True for row in paired) == 26
    assert hidden_invalid == 5
    assert allocation['counts']['skipped_above_demo_size'] == 1

    workload = read('resource-workload.json')
    assert len(workload['inquiry_scenarios']) == 96 * 2
    assert len(workload['allocation_scenarios']) == 108 * 2
    inquiry = totals(workload['inquiry_scenarios'], (
        'patients', 'selected', 'inquiries', 'review_started',
        'review_work_seconds', 'capped_wait_sum_seconds'))
    online = totals(workload['allocation_scenarios'], (
        'patients', 'inquiries', 'unassigned', 'not_arrived', 'untreated'))
    assert inquiry['fixed_3'] == dict(
        patients=264, selected=229, inquiries=1617, review_started=1417,
        review_work_seconds=86008, capped_wait_sum_seconds=35649)
    assert inquiry['fixed_5'] == dict(
        patients=264, selected=229, inquiries=1833, review_started=1655,
        review_work_seconds=99790, capped_wait_sum_seconds=33945)
    assert online['R1'] == dict(
        patients=433, inquiries=2278, unassigned=108, not_arrived=108, untreated=108)
    assert online['C1'] == dict(
        patients=433, inquiries=1275, unassigned=41, not_arrived=41, untreated=41)
    print(json.dumps(dict(
        status='pass', stored_result_only=True,
        allocation=dict(examined=50, observed_paired=46, better=20, tied=26,
                        optimal_proven=26, feasible=20, needs_review=3,
                        skipped=1, hidden_actual_invalid=5),
        inquiry_totals=inquiry, online_totals=online), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
