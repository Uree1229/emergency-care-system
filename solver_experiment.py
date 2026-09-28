"""Synthetic emergency allocation study. No real patient/hospital data.
Run using the bundled Python. Local .solver_deps contains OR-Tools.
All times in 5-minute slots; output metrics in minutes.
"""
from pathlib import Path
import sys, json, random, math, time, statistics, copy, platform
from collections import Counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / '.solver_deps'))
import ortools
from ortools.sat.python import cp_model

OUT = ROOT / 'optimization_results'
HORIZON = 24  # latest permitted planned treatment start, 120 minutes
CAP = [(2, 1), (2, 1), (1, 0)]  # beds, specialist teams
SCENARIOS = ['normal', 'surge', 'stale_capacity', 'unobserved_inflow', 'time_error']

def generate(seed, scenario):
    rng = random.Random(seed)
    patients = []
    for i in range(24 if scenario == 'surge' else 12):
        special = rng.random() < .4
        patients.append(dict(id=i, special=special, weight=rng.choice([1, 2, 4]),
                             duration=rng.randint(3, 6), travel=[rng.randint(1, 7) for _ in CAP]))
    blocks = [dict(h=h, start=0, duration=rng.randint(2, 6), special=h < 2) for h in range(3)]
    observed = dict(patients=patients, blocks=blocks)
    actual = copy.deepcopy(observed)
    if scenario == 'stale_capacity':
        for b in actual['blocks']:
            b['duration'] += 4  # actual release is 20 minutes later than reported
    if scenario == 'unobserved_inflow':
        # Four unreported occupants: three at t=0 (one waits at C), another at
        # t=30 at A. Each uses a bed for 30 min. Two-bed hospitals may have
        # concurrent occupants. No hidden-event knowledge is passed to planner.
        for k in range(4):
            h = k % 3
            actual['blocks'].append(dict(h=h, start=0 if k < 3 else 6,
                                         duration=6, special=False))
        # Hospital C has one bed: replace concurrent unknown arrival by a queue
        # occupying that bed immediately after the known initial occupant.
        c = actual['blocks'][5]
        c['start'] = actual['blocks'][2]['duration']
    if scenario == 'time_error':
        for p in actual['patients']:
            p['duration'] = math.ceil(p['duration'] * 1.3)
            p['travel'] = [math.ceil(v * 1.3) for v in p['travel']]
    return observed, actual

def calendar(data, size=2000):
    cal = [[[0] * size for _ in range(2)] for _ in CAP]
    for b in data['blocks']:
        occupy(cal, b['h'], b['start'], b['duration'], b['special'])
    assert all(max(cal[h][r]) <= CAP[h][r] for h in range(3) for r in range(2))
    return cal

def fits(cal, h, start, duration, special):
    return all(cal[h][0][t] < CAP[h][0] and
               (not special or cal[h][1][t] < CAP[h][1]) for t in range(start, start + duration))

def occupy(cal, h, start, duration, special):
    for t in range(start, start + duration):
        cal[h][0][t] += 1
        cal[h][1][t] += int(special)

def eligible(p):
    return range(2 if p['special'] else 3)

def penalty(data):
    return HORIZON * sum(p['weight'] for p in data['patients']) + 1

def objective(data, plan):
    return sum(penalty(data) if a is None else p['weight'] * a['start']
               for p, a in zip(data['patients'], plan))

def greedy(data, method='earliest'):
    cal = calendar(data)
    plan = [None] * len(data['patients'])
    for p in sorted(data['patients'], key=lambda p: (-p['weight'], p['id'])):
        choices = []
        for h in eligible(p):
            start = next((s for s in range(p['travel'][h], HORIZON + 1)
                          if fits(cal, h, s, p['duration'], p['special'])), None)
            if start is not None:
                key = (p['travel'][h], start, h) if method == 'nearest' else (start, p['travel'][h], h)
                choices.append((key, h, start))
        if choices:
            _, h, start = min(choices)
            plan[p['id']] = dict(h=h, start=start)
            occupy(cal, h, start, p['duration'], p['special'])
    return plan

def solve(data, hint=None, max_deterministic=.05):
    t0 = time.perf_counter()
    model = cp_model.CpModel()
    resource_intervals = [[[], []] for _ in CAP]
    for k, b in enumerate(data['blocks']):
        itv = model.new_fixed_size_interval_var(b['start'], b['duration'], f'block{k}')
        resource_intervals[b['h']][0].append(itv)
        if b['special']:
            resource_intervals[b['h']][1].append(itv)
    variables, missing, terms = {}, [], []
    for p in data['patients']:
        miss = model.new_bool_var(f'missing{p["id"]}')
        missing.append(miss)
        terms.append(penalty(data) * miss)
        selected = []
        for h in eligible(p):
            x = model.new_bool_var(f'x{p["id"]}_{h}')
            start = model.new_int_var(0, HORIZON, f's{p["id"]}_{h}')
            model.add(start >= p['travel'][h]).only_enforce_if(x)
            model.add(start == 0).only_enforce_if(x.Not())
            itv = model.new_optional_fixed_size_interval_var(start, p['duration'], x, f'i{p["id"]}_{h}')
            resource_intervals[h][0].append(itv)
            if p['special']:
                resource_intervals[h][1].append(itv)
            variables[p['id'], h] = x, start
            selected.append(x)
            terms.append(p['weight'] * start)
        model.add(sum(selected) + miss == 1)
    for h in range(3):
        for r in range(2):
            if resource_intervals[h][r]:
                model.add_cumulative(resource_intervals[h][r], [1] * len(resource_intervals[h][r]), CAP[h][r])
    obj = sum(terms)
    model.minimize(obj)
    if hint is not None:
        model.add(obj <= objective(data, hint))
        for p, a in zip(data['patients'], hint):
            model.add_hint(missing[p['id']], int(a is None))
            for h in eligible(p):
                x, s = variables[p['id'], h]
                active = a is not None and a['h'] == h
                model.add_hint(x, int(active))
                model.add_hint(s, a['start'] if active else 0)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_deterministic_time = max_deterministic
    solver.parameters.max_time_in_seconds = 5
    status = solver.solve(model)
    ok = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    if not ok and hint is None:
        return None, dict(status=solver.status_name(status), fallback=False)
    plan = copy.deepcopy(hint) if not ok else [None] * len(data['patients'])
    if ok:
        for (i, h), (x, s) in variables.items():
            if solver.value(x):
                plan[i] = dict(h=h, start=solver.value(s))
    return plan, dict(status=solver.status_name(status), fallback=not ok,
                     objective=objective(data, plan), bound=solver.best_objective_bound,
                     gap=(objective(data, plan) - solver.best_objective_bound) / max(1, objective(data, plan)),
                     wall_seconds=time.perf_counter() - t0)

def validate(data, plan):
    cal = calendar(data)
    for p, a in zip(data['patients'], plan):
        if a is None:
            continue
        assert a['h'] in eligible(p)
        assert p['travel'][a['h']] <= a['start'] <= HORIZON
        assert fits(cal, a['h'], a['start'], p['duration'], p['special'])
        occupy(cal, a['h'], a['start'], p['duration'], p['special'])

def replay(actual, plan):
    # Fixed destinations; earliest physical start at or after planned appointment.
    # Resource calendar contains actual exogenous occupants, unavailable to planner.
    # Process appointments by planned start then weight. No re-optimization.
    cal = calendar(actual)
    executed = [None] * len(plan)
    for p in sorted(actual['patients'], key=lambda p: (plan[p['id']]['start'] if plan[p['id']] else 10000,
                                                     -p['weight'], p['id'])):
        a = plan[p['id']]
        if a is None:
            continue
        h = a['h']
        start = next(s for s in range(max(a['start'], p['travel'][h]), 1800)
                     if fits(cal, h, s, p['duration'], p['special']))
        occupy(cal, h, start, p['duration'], p['special'])
        executed[p['id']] = dict(h=h, start=start)
    assigned = [(p, a, executed[p['id']]) for p, a in zip(actual['patients'], plan) if a is not None]
    starts = [e['start'] * 5 for _, _, e in assigned]
    return dict(unassigned=sum(a is None for a in plan),
                late_over_120=sum(e['start'] > HORIZON for _, _, e in assigned),
                mean_start=statistics.mean(starts) if starts else None,
                weighted_start=sum(p['weight'] * e['start'] * 5 for p, _, e in assigned) /
                               sum(p['weight'] for p, _, _ in assigned) if assigned else None,
                max_start=max(starts) if starts else None,
                mean_slip=statistics.mean((e['start']-a['start']) * 5 for _, a, e in assigned) if assigned else None,
                delayed_patients=sum(e['start'] > a['start'] for _, a, e in assigned),
                execution=executed)

def checks():
    # Independent exhaustive enumeration on tiny 2-patient case, including unassignment.
    data = dict(patients=[dict(id=0, special=True, weight=4, duration=4, travel=[1,2,3]),
                         dict(id=1, special=True, weight=1, duration=3, travel=[1,3,2])], blocks=[])
    options = [[None] + [dict(h=h, start=s) for h in eligible(p)
                         for s in range(p['travel'][h], HORIZON+1)] for p in data['patients']]
    best = float('inf')
    for a in options[0]:
        for b in options[1]:
            try:
                validate(data, [a,b])
            except AssertionError:
                continue
            best = min(best, objective(data, [a,b]))
    plan, meta = solve(data, max_deterministic=1)
    assert meta['status'] == 'OPTIMAL' and objective(data, plan) == best
    # No specialist can start within the horizon, but unassignment stays feasible.
    blocked = copy.deepcopy(data)
    blocked['blocks'] = [dict(h=h,start=0,duration=HORIZON+10,special=True) for h in [0,1]]
    plan, meta = solve(blocked, max_deterministic=1)
    assert all(a is None for a in plan) and meta['status'] == 'OPTIMAL'
    # Force all beds unavailable: no artificial capacity is created.
    full = copy.deepcopy(data)
    full['blocks'] = [dict(h=h,start=0,duration=HORIZON+10,special=False)
                      for h in range(3) for _ in range(CAP[h][0])]
    plan, _ = solve(full, max_deterministic=1)
    assert all(a is None for a in plan)
    return ['tiny case equals exhaustive optimum', 'no suitable specialist implies unassignment',
            'no beds implies unassignment', 'all trial plans independently checked for resource overlap']

def main():
    OUT.mkdir(exist_ok=True)
    checks_passed = checks()
    trials, inputs = [], []
    for scenario in SCENARIOS:
        for seed in range(30):
            observed, actual = generate(seed, scenario)
            inputs.append(dict(scenario=scenario, seed=seed, observed=observed, actual=actual))
            nearest, earliest = greedy(observed, 'nearest'), greedy(observed)
            optimized, meta = solve(observed, earliest)
            assert objective(observed, optimized) <= objective(observed, earliest)
            for name, plan in [('nearest', nearest), ('earliest', earliest), ('cp_sat', optimized)]:
                validate(observed, plan)
                expected = replay(observed, plan)
                assert expected['mean_slip'] == 0
                actual_metrics = replay(actual, plan)
                trials.append(dict(scenario=scenario, seed=seed, policy=name, n=len(plan),
                                   plan=plan, expected=expected, actual=actual_metrics,
                                   solver=meta if name=='cp_sat' else None))
        print('completed', scenario, flush=True)
    summary = []
    for scenario in SCENARIOS:
        for policy in ['nearest', 'earliest', 'cp_sat']:
            rows = [r for r in trials if r['scenario']==scenario and r['policy']==policy]
            rec = dict(scenario=scenario,policy=policy, trials=len(rows))
            for k in ['unassigned','late_over_120','mean_start','weighted_start','max_start','mean_slip','delayed_patients']:
                vals = [r['actual'][k] for r in rows]
                rec[k] = statistics.mean(vals)
                rec[k+'_sd'] = statistics.stdev(vals)
            rec['expected_weighted_start'] = statistics.mean(r['expected']['weighted_start'] for r in rows)
            if policy == 'cp_sat':
                rec['statuses'] = dict(Counter(r['solver']['status'] for r in rows))
                rec['fallbacks'] = sum(r['solver']['fallback'] for r in rows)
                rec['gap_mean'] = statistics.mean(r['solver']['gap'] for r in rows)
                rec['seconds_mean'] = statistics.mean(r['solver']['wall_seconds'] for r in rows)
                rec['seconds_max'] = max(r['solver']['wall_seconds'] for r in rows)
            summary.append(rec)
    metadata = dict(ortools=ortools.__version__,python=platform.python_version(), slot_minutes=5,
                    horizon_minutes=120,seeds=list(range(30)),capacities=CAP,
                    max_deterministic_time=.05,max_wall_seconds=5, workers=1,checks=checks_passed)
    for name, obj in [('summary',summary),('trials',trials),('inputs',inputs),('metadata',metadata)]:
        (OUT/f'{name}.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
