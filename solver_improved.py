"""Synthetic allocation prototype, integer time slots; not a clinical policy.

Absolute times preserve elapsed waiting across replans. destination_locks keep
the hospital while allowing a new start; fixed keeps both. Existing patients
continue to consume resources. No production hospital connection.
"""
from pathlib import Path
import copy
import itertools
import json
import math
import sys
import time

sys.path.insert(0, str(Path(__file__).parent / '.solver_deps'))
from ortools.sat.python import cp_model

GROUPS = (4, 2, 1)


def check_input(data):
    for key in ('now', 'horizon', 'version'):
        if type(data[key]) is not int or data[key] < 0:
            raise ValueError(f'{key} must be a nonnegative integer')
    if data['horizon'] < data['now']:
        raise ValueError('horizon precedes now')
    caps = data['capacities']
    if not caps or any(len(c) != 2 or any(type(v) is not int or v < 0 for v in c) for c in caps):
        raise ValueError('capacities must contain nonnegative bed and team counts')
    ids = [p['id'] for p in data['patients']]
    if len(ids) != len(set(ids)) or any(not isinstance(i, str) for i in ids):
        raise ValueError('patient IDs must be unique strings')
    for p in data['patients']:
        if p['priority'] not in GROUPS or type(p['special']) is not bool:
            raise ValueError('invalid priority or resource requirement')
        for key in ('release', 'deadline', 'duration'):
            if type(p[key]) is not int or p[key] < (1 if key == 'duration' else 0):
                raise ValueError(f'invalid {key}')
        if len(p['travel']) != len(caps) or any(type(v) is not int or v < 0 for v in p['travel']):
            raise ValueError('invalid travel times')
        if len(p['eligible']) != len(set(p['eligible'])) or any(type(h) is not int or not 0 <= h < len(caps) for h in p['eligible']):
            raise ValueError('invalid eligible hospital')
    if not set(data.get('fixed', {})) <= set(ids):
        raise ValueError('fixed assignment refers to unknown patient')
    if any(a is None for a in data.get('fixed', {}).values()):
        raise ValueError('fixed patients must have an assignment')
    if not set(data.get('destination_locks', {})) <= set(ids):
        raise ValueError('destination lock refers to unknown patient')
    for pid, h in data.get('destination_locks', {}).items():
        p = next(p for p in data['patients'] if p['id'] == pid)
        if type(h) is not int or h not in p['eligible']:
            raise ValueError('invalid locked hospital')
    for p in data['patients']:
        if 'arrivals' in p and (len(p['arrivals']) != len(caps) or
                               any(type(v) is not int or v < 0 for v in p['arrivals'])):
            raise ValueError('invalid absolute arrival times')
        check_demands(p)
    for b in data.get('blocks', []):
        if type(b['h']) is not int or not 0 <= b['h'] < len(caps):
            raise ValueError('invalid blocked hospital')
        if any(type(b[k]) is not int or b[k] < (1 if k == 'duration' else 0) for k in ('start', 'duration')):
            raise ValueError('invalid blocked interval')
        if type(b['special']) is not bool:
            raise ValueError('invalid blocked resource')
        check_demands(b)


def demands(entry):
    return entry.get('demands', [1, int(entry['special'])])


def check_demands(entry):
    d = demands(entry)
    if len(d) != 2 or any(type(v) is not int or v < 0 for v in d) or not any(d):
        raise ValueError('invalid resource demand counts')


def eligible(data, p):
    lock = data.get('destination_locks', {}).get(p['id'])
    return p['eligible'] if lock is None else [lock]


def upper(data, p):
    if p['id'] in data.get('destination_locks', {}):
        return data['horizon']  # 기존 목적지는 유지하고, 기한 초과는 재확인 대상으로 보고한다.
    return min(data['horizon'], p['release'] + p['deadline'])


def earliest(data, p, h):
    if 'arrivals' in p:
        return max(data['now'], p['arrivals'][h])
    return max(data['now'], p['release']) + p['travel'][h]


def intervals(data, plan):
    entries = list(data.get('blocks', []))
    for p in data['patients']:
        a = plan.get(p['id'])
        if a is not None:
            entries.append(dict(h=a['h'], start=a['start'], duration=p['duration'], special=p['special'],
                                demands=demands(p)))
    return entries


def capacity_ok(data, plan):
    # Sweep endpoints, aggregating departures and arrivals at the same instant.
    events = {}
    for b in intervals(data, plan):
        start, end = max(data['now'], b['start']), b['start'] + b['duration']
        if start >= end:
            continue
        for resource, demand in enumerate(demands(b)):
            for at, delta in ((start, demand), (end, -demand)):
                key = (b['h'], resource, at)
                events[key] = events.get(key, 0) + delta
    load = {}
    for (h, resource, at), delta in sorted(events.items()):
        key = (h, resource)
        load[key] = load.get(key, 0) + delta
        if load[key] > data['capacities'][h][resource]:
            return False
    return True


def validate(data, plan):
    if not isinstance(plan, dict) or set(plan) != {p['id'] for p in data['patients']}:
        return False
    fixed = data.get('fixed', {})
    for p in data['patients']:
        a = plan[p['id']]
        if p['id'] in fixed and a != fixed[p['id']]:
            return False
        if a is None:
            if p['id'] in data.get('destination_locks', {}):
                return False
            continue
        if not isinstance(a, dict) or set(a) != {'h', 'start'}:
            return False
        h, start = a['h'], a['start']
        if type(h) is not int or type(start) is not int or h not in eligible(data, p):
            return False
        lower = p['release'] + p['travel'][h] if p['id'] in fixed else earliest(data, p, h)
        if not lower <= start <= upper(data, p):
            return False
    return capacity_ok(data, plan)


def score(data, plan, policy):
    missing = [sum(plan[p['id']] is None for p in data['patients'] if p['priority'] == g) for g in GROUPS]
    waits = [sum(plan[p['id']]['start'] - p['release'] for p in data['patients']
                 if p['priority'] == g and plan[p['id']] is not None) for g in GROUPS]
    return tuple(([sum(missing)] if policy == 'total_first' else []) + missing + waits)


def greedy(data):
    plan = {p['id']: copy.deepcopy(data.get('fixed', {}).get(p['id'])) for p in data['patients']}
    initial_data = dict(data, destination_locks={})
    if not validate(initial_data, plan):
        return None
    for p in sorted(data['patients'], key=lambda p: (p['id'] not in data.get('destination_locks', {}),
                                                    -p['priority'], p['release'], p['id'])):
        if p['id'] in data.get('fixed', {}):
            continue
        choices = []
        for h in eligible(data, p):
            for start in range(earliest(data, p, h), upper(data, p) + 1):
                plan[p['id']] = dict(h=h, start=start)
                if capacity_ok(data, plan):
                    choices.append((start, p['travel'][h], h))
                    break
        plan[p['id']] = None
        if choices:
            start, _, h = min(choices)
            plan[p['id']] = dict(h=h, start=start)
        elif p['id'] in data.get('destination_locks', {}):
            return None
    return plan


def solve(data, policy='severity_first', seconds=3.0, hint=None):
    started = time.perf_counter()
    check_input(data)
    if policy not in ('severity_first', 'total_first') or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('invalid policy or time budget')
    data = copy.deepcopy(data)
    incumbent = greedy(data)
    meta = dict(snapshot_version=data['version'], policy=policy, stages=[],
                hint_rejected=hint is not None and not validate(data, hint), fallback=False,
                budget_seconds=seconds, lex_optimal=False)
    if incumbent is None and seconds == 0:
        meta.update(status='NEEDS_REVIEW', reason='Fixed assignments or existing occupancy conflict with current constraints',
                    elapsed_seconds=time.perf_counter() - started)
        return None, meta
    if hint is not None and not meta['hint_rejected'] and (incumbent is None or
                                                         score(data, hint, policy) < score(data, incumbent, policy)):
        incumbent = copy.deepcopy(hint)
    if seconds == 0:
        meta.update(status='BUDGET_EXHAUSTED', fallback=True, objective=list(score(data, incumbent, policy)),
                    elapsed_seconds=time.perf_counter() - started)
        return incumbent, meta

    model = cp_model.CpModel()
    resources = [[[], []] for _ in data['capacities']]
    resource_counts = [[[], []] for _ in data['capacities']]
    for k, b in enumerate(intervals(data, data.get('fixed', {}))):
        start, end = max(data['now'], b['start']), b['start'] + b['duration']
        if start < end:
            itv = model.new_fixed_size_interval_var(start, end - start, f'fixed{k}')
            for r, demand in enumerate(demands(b)):
                if demand:
                    resources[b['h']][r].append(itv)
                    resource_counts[b['h']][r].append(demand)
    misses = {g: [] for g in GROUPS}
    delays = {g: [] for g in GROUPS}
    variables, missing = {}, {}
    for p in data['patients']:
        pid, g = p['id'], p['priority']
        if pid in data.get('fixed', {}):
            delays[g].append(data['fixed'][pid]['start'] - p['release'])
            continue
        miss = model.new_bool_var('miss_' + pid)
        missing[pid] = miss
        if pid in data.get('destination_locks', {}):
            model.add(miss == 0)
        misses[g].append(miss)
        selected = []
        for h in eligible(data, p):
            low, high = earliest(data, p, h), upper(data, p)
            if low > high:
                continue
            x = model.new_bool_var(f'x_{pid}_{h}')
            start = model.new_int_var(0, high, f's_{pid}_{h}')
            model.add(start >= low).only_enforce_if(x)
            model.add(start == 0).only_enforce_if(x.Not())
            itv = model.new_optional_fixed_size_interval_var(start, p['duration'], x, f'i_{pid}_{h}')
            for r, demand in enumerate(demands(p)):
                if demand:
                    resources[h][r].append(itv)
                    resource_counts[h][r].append(demand)
            variables[pid, h] = (x, start)
            selected.append(x)
            delays[g].append(start - p['release'] * x)
        model.add(sum(selected) + miss == 1)
    for h, pair in enumerate(resources):
        for r, itvs in enumerate(pair):
            if itvs:
                model.add_cumulative(itvs, resource_counts[h][r], data['capacities'][h][r])
    objectives = ([sum(sum(misses[g]) for g in GROUPS)] if policy == 'total_first' else [])
    objectives += [sum(misses[g]) for g in GROUPS] + [sum(delays[g]) for g in GROUPS]
    found = False
    for index, obj in enumerate(objectives):
        remaining = seconds - (time.perf_counter() - started)
        if remaining <= 0:
            meta['status'] = 'BUDGET_EXHAUSTED'
            break
        if type(obj) is int:
            meta['stages'].append(dict(index=index, status='OPTIMAL', value=obj, bound=obj))
            continue
        model.minimize(obj)
        # A valid incumbent provides both a hint and a stage-specific upper bound.
        if incumbent is not None:
            model.add(obj <= score(data, incumbent, policy)[index])
        model.clear_hints()
        for pid, miss in missing.items() if incumbent is not None else []:
            model.add_hint(miss, int(incumbent[pid] is None))
        for (pid, h), (x, start) in variables.items() if incumbent is not None else []:
            a = incumbent[pid]
            active = a is not None and a['h'] == h
            model.add_hint(x, int(active))
            model.add_hint(start, a['start'] if active else 0)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(0.000001, seconds - (time.perf_counter() - started))
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        status = solver.solve(model)
        name = solver.status_name(status)
        stage = dict(index=index, status=name)
        meta['stages'].append(stage)
        meta['status'] = name
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # Do not proceed to lower objectives when an upper objective is unresolved.
            break
        candidate = {p['id']: copy.deepcopy(data.get('fixed', {}).get(p['id'])) for p in data['patients']}
        for (pid, h), (x, start) in variables.items():
            if solver.value(x):
                candidate[pid] = dict(h=h, start=solver.value(start))
        if not validate(data, candidate):
            meta['status'] = 'VALIDATION_FAILED'
            break
        found = True
        # Equal values in the active stage may leave lower objectives worse.
        # Keep the better full incumbent while retaining this stage's proof.
        if incumbent is None or score(data, candidate, policy) <= score(data, incumbent, policy):
            incumbent = candidate
        value = score(data, incumbent, policy)[index]
        stage.update(value=value, bound=solver.best_objective_bound)
        if status != cp_model.OPTIMAL:
            break
        model.add(obj == value)
    else:
        meta.update(status='OPTIMAL', lex_optimal=True)
    if incumbent is None:
        meta.update(status='NEEDS_REVIEW', elapsed_seconds=time.perf_counter() - started)
        return None, meta
    meta.update(fallback=not found and not meta['lex_optimal'], objective=list(score(data, incumbent, policy)),
                elapsed_seconds=time.perf_counter() - started)
    assert validate(data, incumbent)
    return incumbent, meta


def commit_allowed(data, plan, meta):
    """A local version gate; production use requires an atomic transaction."""
    return data['version'] == meta['snapshot_version'] and validate(data, plan)


def metrics(data, plan):
    result = {}
    for g in GROUPS:
        patients = [p for p in data['patients'] if p['priority'] == g]
        waits = [plan[p['id']]['start'] - p['release'] for p in patients if plan[p['id']] is not None]
        result[str(g)] = dict(total=len(patients), unassigned=sum(plan[p['id']] is None for p in patients),
                              max_delay=max(waits) if waits else None,
                              mean_delay=sum(waits)/len(waits) if waits else None,
                              deadline_violations=sum(plan[p['id']]['start'] > p['release']+p['deadline']
                                                      for p in patients if plan[p['id']] is not None))
    return result


def sample(priority=4, pid='H', duration=3):
    return dict(id=pid, priority=priority, special=False, release=0, deadline=2,
                duration=duration, travel=[0], eligible=[0])


def conflict_case():
    high = sample()
    high['deadline'] = 0
    return dict(version=1, now=0, horizon=2, capacities=[(1, 0)], blocks=[], fixed={},
                patients=[high] + [sample(1, f'L{k}', 1) for k in range(3)])


def exhaustive(data, policy):
    options = []
    for p in data['patients']:
        if p['id'] in data.get('fixed', {}):
            options.append([data['fixed'][p['id']]])
        else:
            options.append(([None] if p['id'] not in data.get('destination_locks', {}) else []) +
                           [dict(h=h, start=s) for h in eligible(data, p)
                           for s in range(earliest(data, p, h), upper(data, p)+1)])
    best = None
    for assignments in itertools.product(*options):
        plan = dict(zip((p['id'] for p in data['patients']), assignments))
        if validate(data, plan) and (best is None or score(data, plan, policy) < score(data, best, policy)):
            best = plan
    return best


if __name__ == '__main__':
    records = []
    for policy in ('total_first', 'severity_first'):
        data = conflict_case()
        plan, meta = solve(data, policy)
        records.append(dict(case='priority_tradeoff', plan=plan, meta=meta, metrics=metrics(data, plan)))
    data = conflict_case()
    data['fixed'] = {'H': dict(h=0, start=0)}
    plan, meta = solve(data)
    records.append(dict(case='fixed_in_transit', plan=plan, meta=meta, metrics=metrics(data, plan)))
    Path('optimization_results_v2').mkdir(exist_ok=True)
    Path('optimization_results_v2/examples.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(records, ensure_ascii=False, indent=2))
