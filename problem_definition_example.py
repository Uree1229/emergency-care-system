"""Exhaustive check of a small synthetic allocation example; standard library only."""
import itertools
import json
from pathlib import Path

PATIENTS = [
    {"id": "P1", "priority": 0, "weight": 4, "travel": {"A": 5, "B": 15}},
    {"id": "P2", "priority": 1, "weight": 2, "travel": {"A": 5, "B": 10}},
    {"id": "P3", "priority": 2, "weight": 1, "travel": {"A": 10}},
]
DURATION = 10
HORIZON = 60
BOOKINGS = {"A": [(10, 30)], "B": []}


def overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def feasible(plan, bookings):
    calendar = {h: list(intervals) for h, intervals in bookings.items()}
    for patient, assignment in zip(PATIENTS, plan):
        if assignment is None:
            continue
        hospital, start = assignment
        if hospital not in patient['travel'] or not patient['travel'][hospital] <= start <= HORIZON:
            return False
        interval = (start, start + DURATION)
        if any(overlaps(interval, old) for old in calendar[hospital]):
            return False
        calendar[hospital].append(interval)
    return True


def objective(plan):
    # Lexicographic: high, medium, low unassigned counts, weighted start, max start.
    missing = [0, 0, 0]
    cost, latest = 0, 0
    for patient, assignment in zip(PATIENTS, plan):
        if assignment is None:
            missing[patient['priority']] += 1
        else:
            cost += patient['weight'] * assignment[1]
            latest = max(latest, assignment[1])
    return (*missing, cost, latest)


def priority_first_objective(plan):
    base = objective(plan)
    group_start_sums = [0, 0, 0]
    for patient, assignment in zip(PATIENTS, plan):
        if assignment is not None:
            group_start_sums[patient['priority']] += assignment[1]
    return (*base[:3], *group_start_sums, base[-1])


def enumerate_best(bookings, priority_first=False):
    options = [[None] + [(h, t) for h, arrival in p['travel'].items()
                        for t in range(arrival, HORIZON + 1, 5)] for p in PATIENTS]
    best, plans, checked = None, [], 0
    for plan in itertools.product(*options):
        checked += 1
        if not feasible(plan, bookings):
            continue
        score = priority_first_objective(plan) if priority_first else objective(plan)
        if best is None or score < best:
            best, plans = score, [plan]
        elif score == best:
            plans.append(plan)
    return {'objective': best, 'plans': plans, 'candidates_checked': checked}


def replay(plan):
    # Existing confirmed booking is fixed. Keep assigned hospitals and planned order.
    calendar = {h: list(v) for h, v in BOOKINGS.items()}
    result = [None] * len(PATIENTS)
    for i in sorted(range(len(plan)), key=lambda i: (
            plan[i][1] if plan[i] else 10**6, PATIENTS[i]['priority'], i)):
        assignment = plan[i]
        if assignment is None:
            continue
        hospital, planned_start = assignment
        start = max(planned_start, PATIENTS[i]['travel'][hospital])
        while any(overlaps((start, start + DURATION), old) for old in calendar[hospital]):
            start += 5
        calendar[hospital].append((start, start + DURATION))
        result[i] = (hospital, start)
    return result


def metrics(plan):
    assigned = [(p, a) for p, a in zip(PATIENTS, plan) if a is not None]
    return {'unassigned': len(PATIENTS) - len(assigned),
            'mean_start': sum(a[1] for _, a in assigned) / len(assigned),
            'weighted_mean_start': sum(p['weight'] * a[1] for p, a in assigned) /
                                   sum(p['weight'] for p, _ in assigned),
            'maximum_start': max(a[1] for _, a in assigned)}


def main():
    assert not overlaps((0, 10), (10, 30))
    assert overlaps((5, 15), (10, 30))
    blind = enumerate_best({'A': [], 'B': []})
    aware = enumerate_best(BOOKINGS)
    assert len(blind['plans']) == len(aware['plans']) == 1
    assert blind['objective'] == (0, 0, 0, 55, 15)
    assert aware['objective'] == (0, 0, 0, 130, 30)
    strict = enumerate_best(BOOKINGS, priority_first=True)
    assert strict['plans'] == [(('B', 15), ('B', 25), ('A', 30))]
    blind_executed = replay(blind['plans'][0])
    aware_executed = replay(aware['plans'][0])
    assert not feasible(blind['plans'][0], BOOKINGS)
    assert feasible(blind_executed, BOOKINGS)
    assert feasible(aware_executed, BOOKINGS)
    assert objective(blind_executed) == (0, 0, 0, 180, 40)
    assert list(aware['plans'][0]) == aware_executed
    result = {'synthetic_only': True, 'patients': PATIENTS, 'duration_minutes': DURATION,
              'latest_planned_start_minutes': HORIZON, 'actual_bookings': BOOKINGS,
              'ignore_incoming': blind, 'include_incoming': aware,
              'priority_first': strict, 'priority_first_metrics': metrics(strict['plans'][0]),
              'ignore_incoming_executed': blind_executed,
              'include_incoming_executed': aware_executed,
              'ignore_metrics': metrics(blind_executed),
              'include_metrics': metrics(aware_executed)}
    output = Path(__file__).with_name('problem_definition_example_results.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
