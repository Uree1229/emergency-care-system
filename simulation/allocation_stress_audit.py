"""Independent audit of large current-input allocation experiments.

The auditor reads saved JSON only. It imports no experiment, world, metric,
heuristic or CP-SAT helper. Feasibility, lexicographic objectives, paired
denominators and reported solver evidence are reconstructed independently.
An internally consistent artifact is not proof of clinical realism.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = {'high': 4, 'medium': 2, 'low': 1}
OBJECTIVE = ['unassigned_priority_weight', 'priority_weighted_travel_minutes']


class AllocationStressAuditError(ValueError):
    """A raw input, assignment or claimed result is inconsistent."""


def _require(condition, message):
    if not condition:
        raise AllocationStressAuditError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _integer(value, label, *, minimum=0):
    _require(isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
             f'{label} must be an integer >= {minimum}')
    return value


def _number(value, label, *, minimum=0):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool)
             and math.isfinite(value) and value >= minimum,
             f'{label} must be a finite number >= {minimum}')
    return value


def _strings(value, label):
    _require(isinstance(value, list) and all(isinstance(x, str) and x for x in value),
             f'{label} must be a string list')
    _require(len(value) == len(set(value)), f'duplicate {label}')
    return value


def _resource_map(value, label):
    _require(isinstance(value, dict), f'{label} must be a resource mapping')
    for resource, quantity in value.items():
        _require(isinstance(resource, str) and resource, f'{label} has an invalid resource')
        _integer(quantity, f'{label}.{resource}')
    return value


def _raw_problem(snapshot):
    """Reconstruct actual accepted feasible edges and all residual resources."""
    _require(isinstance(snapshot, dict) and snapshot.get('synthetic') is True,
             'snapshot must be explicitly synthetic')
    _require(set(snapshot) <= {'id', 'family', 'seed', 'size', 'synthetic', 'description',
             'at', 'hospitals', 'patients'}, 'unobserved/unknown snapshot fields')
    _require(isinstance(snapshot.get('id'), str) and snapshot['id'], 'invalid snapshot ID')
    _require(isinstance(snapshot.get('family'), str) and snapshot['family'], 'invalid snapshot family')
    _integer(snapshot.get('seed'), 'snapshot seed')
    _integer(snapshot.get('size'), 'snapshot size', minimum=1)
    at = _number(snapshot.get('at'), 'snapshot time')
    hospitals = snapshot.get('hospitals')
    patients = snapshot.get('patients')
    _require(isinstance(hospitals, list) and hospitals, 'missing hospitals')
    _require(isinstance(patients, list) and patients, 'missing patients')
    by_hid, free = {}, {}
    for h in hospitals:
        _require(isinstance(h, dict), 'invalid hospital row')
        _require(set(h) <= {'id', 'name', 'lat', 'lon', 'capabilities', 'capacity',
                 'occupied', 'committed', 'queued'}, 'unobserved/unknown hospital fields')
        hid = h.get('id')
        _require(isinstance(hid, str) and hid and hid not in by_hid,
                 'duplicate or invalid hospital ID')
        by_hid[hid] = h
        _strings(h.get('capabilities', []), 'hospital capabilities')
        maps = {field: _resource_map(h.get(field, {}), f'{hid}.{field}')
                for field in ('capacity', 'occupied', 'committed', 'queued')}
        resources = set().union(*(set(value) for value in maps.values()))
        for resource in resources:
            free[hid, resource] = (maps['capacity'].get(resource, 0)
                - maps['occupied'].get(resource, 0) - maps['committed'].get(resource, 0)
                - maps['queued'].get(resource, 0))
    seen, options = set(), {}
    for p in patients:
        _require(isinstance(p, dict), 'invalid patient row')
        _require(set(p) <= {'id', 't_req', 'lat', 'lon', 'priority', 'required_capabilities',
                 'resource_demands', 'accepted_hospitals', 'accepted_at', 'travel_minutes'},
                 'unobserved/unknown patient fields')
        pid = p.get('id')
        _require(isinstance(pid, str) and pid and pid not in seen,
                 'duplicate or invalid patient ID')
        seen.add(pid)
        _require(isinstance(p.get('priority'), str) and p['priority'] in WEIGHTS,
                 f'unknown priority: {pid}')
        requested = _number(p.get('t_req'), f'{pid}.t_req')
        _require(requested <= at, 'future patient in observed snapshot')
        demand = _resource_map(p.get('resource_demands'), f'{pid}.resource_demands')
        _require(any(demand.values()), 'patient must demand a resource')
        required = set(_strings(p.get('required_capabilities', []), 'required capabilities'))
        offers = _strings(p.get('accepted_hospitals'), 'accepted hospitals')
        _require(all(hid in by_hid for hid in offers), 'unknown accepted hospital')
        accepted_at = p.get('accepted_at')
        _require(isinstance(accepted_at, dict) and set(accepted_at) == set(offers),
                 'accepted_at must match actual accepted hospitals')
        for hid in offers:
            observed_at = _number(accepted_at[hid], 'accepted time')
            _require(requested <= observed_at <= at, 'future or pre-request acceptance')
        travel = p.get('travel_minutes')
        _require(isinstance(travel, dict) and set(travel) == set(by_hid),
                 'travel estimates must cover exactly all hospitals')
        for hid, minutes in travel.items():
            _integer(minutes, f'{pid}.travel.{hid}')
        options[pid] = [hid for hid in offers
            if required <= set(by_hid[hid].get('capabilities', []))
            and all(quantity <= free.get((hid, resource), 0)
                    for resource, quantity in demand.items() if quantity)]
    return patients, free, options


def _raw_metrics(snapshot, plan, free, options):
    patients = snapshot['patients']
    _require(isinstance(plan, dict) and set(plan) == {p['id'] for p in patients},
             'assignment patient IDs do not match')
    used, times, unassigned = Counter(), [], []
    weighted = 0
    for p in patients:
        hid = plan[p['id']]
        if hid is None:
            unassigned.append(p)
            continue
        _require(hid in options[p['id']], 'assignment outside feasible actual accepted offers')
        for resource, quantity in p['resource_demands'].items():
            if quantity:
                used[hid, resource] += quantity
        minutes = p['travel_minutes'][hid]
        times.append(minutes)
        weighted += WEIGHTS[p['priority']] * minutes
    _require(all(quantity <= free.get(key, 0) for key, quantity in used.items()),
             'resource ledger exceeded capacity - occupied - committed - queued')
    return dict(patient_count=len(patients), assigned_count=len(times),
        unassigned_count=len(unassigned),
        unassigned_priority_weight=sum(WEIGHTS[p['priority']] for p in unassigned),
        priority_weighted_travel_minutes=weighted,
        unassigned_by_priority={str(tier): sum(WEIGHTS[p['priority']] == tier
                                             for p in unassigned) for tier in (4, 2, 1)},
        travel_denominator=len(times), travel_mean_minutes=statistics.mean(times) if times else None,
        travel_max_minutes=max(times) if times else None,
        travel_p95_minutes=sorted(times)[math.ceil(.95 * len(times)) - 1] if times else None,
        constraint_violations=0)


def _objective(metrics):
    return tuple(metrics[name] for name in OBJECTIVE)


def _exact_objective(patients, free, options):
    """Small fixtures only; independent of any solver/model implementation."""
    best = None
    for combination in product(*(options[p['id']] + [None] for p in patients)):
        used, weight, travel = Counter(), 0, 0
        for p, hid in zip(patients, combination):
            if hid is None:
                weight += WEIGHTS[p['priority']]
            else:
                travel += WEIGHTS[p['priority']] * p['travel_minutes'][hid]
                for resource, quantity in p['resource_demands'].items():
                    if quantity:
                        used[hid, resource] += quantity
        if all(q <= free.get(key, 0) for key, q in used.items()):
            value = (weight, travel)
            if best is None or value < best:
                best = value
    return best


def _common(snapshot, baseline, plan):
    values = [p['travel_minutes'][plan[p['id']]] - p['travel_minutes'][baseline[p['id']]]
              for p in snapshot['patients']
              if baseline[p['id']] is not None and plan[p['id']] is not None]
    return dict(denominator=len(values),
        mean_difference_minutes=statistics.mean(values) if values else None,
        total_difference_minutes=sum(values),
        comparison_only=sum(baseline[p['id']] is None and plan[p['id']] is not None
                            for p in snapshot['patients']),
        baseline_only=sum(baseline[p['id']] is not None and plan[p['id']] is None
                          for p in snapshot['patients']))


def _equal_number(left, right):
    return left == right if left is None or right is None else math.isclose(
        left, right, rel_tol=1e-12, abs_tol=1e-8)


def _nearest_greedy(patients, free, options):
    """Independently reproduce the documented H1 initial/R1 plan."""
    used, plan = Counter(), {}
    for p in sorted(patients, key=lambda row: (-WEIGHTS[row['priority']], row['t_req'], row['id'])):
        feasible = [hid for hid in options[p['id']]
                    if all(quantity <= free.get((hid, resource), 0) - used[hid, resource]
                           for resource, quantity in p['resource_demands'].items() if quantity)]
        hid = min(feasible, key=lambda h: (p['travel_minutes'][h], h)) if feasible else None
        plan[p['id']] = hid
        if hid is not None:
            used.update({(hid, resource): quantity for resource, quantity in
                         p['resource_demands'].items() if quantity})
    return plan


def _audit_h1(branch, raw, snapshot, patients, free, options):
    records = branch.get('solver_records')
    _require(isinstance(records, list) and len(records) == 1, 'H1 requires one decision record')
    record = records[0]
    _require(record.get('status') == 'HEURISTIC' and record.get('optimal_proven') is False,
             'H1 must not claim optimality')
    _require(branch.get('fallbacks') == 0, 'H1 unexpected fallback counter')
    _require(record.get('assignments') == branch['assignments'], 'H1 recorded assignments mismatch')
    _require(record.get('objective') == list(_objective(raw)), 'H1 recorded objective mismatch')
    _require(record.get('assigned_count') == raw['assigned_count'], 'H1 assigned count mismatch')
    _require(record.get('time_limit_seconds') == branch['budget_seconds'], 'H1 budget mismatch')
    baseline = _nearest_greedy(patients, free, options)
    baseline_raw = _raw_metrics(snapshot, baseline, free, options)
    _require(record.get('baseline_assignments') == baseline, 'H1 baseline is not documented nearest greedy')
    _require(record.get('baseline_objective') == list(_objective(baseline_raw)),
             'H1 baseline objective mismatch')
    _require(record.get('baseline_assigned_count') == baseline_raw['assigned_count'],
             'H1 baseline assigned count mismatch')
    _require(_objective(raw) <= _objective(baseline_raw), 'H1 worsened the baseline objective')
    maximum = _integer(record.get('max_moves'), 'H1 max_moves')
    evaluated = _integer(record.get('evaluated_moves'), 'H1 evaluated_moves')
    accepted = _integer(record.get('accepted_moves'), 'H1 accepted_moves')
    _require(accepted <= evaluated <= maximum, 'H1 move counter exceeds its bound')
    for field in ('wall_seconds', 'preprocessing_seconds', 'search_seconds'):
        _number(record.get(field), f'H1 {field}')
    _require(record['preprocessing_seconds'] + record['search_seconds']
             <= record['wall_seconds'] + 1e-6, 'H1 component time exceeds total time')
    _require(isinstance(record.get('search_stop_reason'), str)
             and record['search_stop_reason'], 'H1 missing stop reason')
    _require(record['search_stop_reason'] in ('search_complete', 'move_limit', 'time_limit',
             'time_limit_zero', 'no_eligible_offers'), 'H1 invalid stop reason')
    if record['search_stop_reason'] == 'move_limit':
        _require(evaluated == maximum, 'H1 move-limit stop without exhausted counter')
    if record['search_stop_reason'] == 'time_limit_zero':
        _require(record['time_limit_seconds'] == 0 and evaluated == 0,
                 'H1 false zero-budget stop')
    if record['search_stop_reason'] == 'no_eligible_offers':
        _require(not any(options.values()) and evaluated == 0, 'H1 false empty-offer stop')
    _require(record.get('batch_size') == len(patients)
             and record.get('edge_count') == sum(map(len, options.values())), 'H1 input summary mismatch')
    strategies = record.get('strategy_results')
    _require(isinstance(strategies, list) and strategies
             and strategies[0] == dict(strategy='priority_nearest',
                 objective=list(_objective(baseline_raw)), accepted=True), 'H1 missing baseline strategy')
    strategies_names = record.get('strategies')
    _require(isinstance(strategies_names, list) and strategies_names[:len(strategies)] == [
             item.get('strategy') for item in strategies], 'H1 strategy order mismatch')
    _require(len(strategies) <= len(strategies_names) <= len(strategies) + 1,
             'H1 started/completed strategy count mismatch')
    if len(strategies_names) > len(strategies):
        _require(record['search_stop_reason'] == 'time_limit', 'H1 unfinished strategy without timeout')
    incumbent = _objective(baseline_raw)
    strategy_improvements = []
    for item in strategies[1:]:
        value = item.get('objective')
        _require(isinstance(value, list) and len(value) == 2, 'H1 invalid strategy objective')
        for v in value:
            _integer(v, 'H1 strategy objective')
        improved = tuple(value) < incumbent
        _require(item.get('accepted') is improved, 'H1 strategy acceptance contradiction')
        if improved:
            incumbent = tuple(value)
            strategy_improvements.append((item['strategy'], value))
    improvements = record.get('improvements')
    _require(isinstance(improvements, list) and len(improvements) == accepted,
             'H1 improvement count mismatch')
    _require(record.get('accepted_move_types') == [item.get('kind') for item in improvements],
             'H1 accepted move types mismatch')
    _require([(item.get('kind'), item.get('objective')) for item in improvements[:len(strategy_improvements)]]
             == strategy_improvements, 'H1 construction improvements mismatch')
    previous, last_evaluation = _objective(baseline_raw), 0
    for item in improvements:
        _require(item.get('kind') in set(strategies_names) | {'augment', 'swap', 'relocate'},
                 'H1 unknown improvement kind')
        evaluation = _integer(item.get('evaluation'), 'H1 improvement evaluation', minimum=1)
        _require(last_evaluation < evaluation <= evaluated, 'H1 improvement evaluation order mismatch')
        value = item.get('objective')
        _require(isinstance(value, list) and len(value) == 2, 'H1 invalid improvement objective')
        for v in value:
            _integer(v, 'H1 improvement objective')
        _require(tuple(value) < previous, 'H1 improvement is not strictly lexicographic')
        previous, last_evaluation = tuple(value), evaluation
    _require(previous == _objective(raw), 'H1 final objective differs from improvement history')
    return 'heuristic'


def _audit_cp(branch, raw, patients, options):
    records = branch.get('solver_records')
    _require(isinstance(records, list) and len(records) == 1, 'C1 requires one decision record')
    record = records[0]
    for field in ('optimal_proven', 'primary_optimal_proven', 'secondary_optimal_proven'):
        _require(type(record.get(field)) is bool, 'C1 proof flags must be boolean')
    _require(record.get('status') in ('OPTIMAL', 'OPTIMAL_EMPTY', 'FEASIBLE',
             'FEASIBLE_PRIMARY_OPTIMAL', 'FALLBACK', 'MISSING_DEPENDENCY', 'UNKNOWN',
             'INFEASIBLE', 'MODEL_INVALID', 'INVALID_INCUMBENT'), 'C1 unknown final status')
    _require(record.get('batch_size') == len(patients), 'C1 batch size mismatch')
    _require(record.get('edge_count') == sum(map(len, options.values())), 'C1 edge count mismatch')
    _require(record.get('assignments') == branch['assignments'], 'C1 recorded assignments mismatch')
    _require(record.get('objective') == list(_objective(raw)), 'C1 recorded objective mismatch')
    _require(record.get('assigned_count') == raw['assigned_count'], 'C1 assigned count mismatch')
    _require(record.get('time_limit_seconds') == branch['budget_seconds'], 'C1 budget mismatch')
    _require(record.get('seed') == 0 and record.get('num_search_workers') == 1,
             'C1 seed/worker settings mismatch')
    _require(type(record.get('fallback')) is bool, 'C1 fallback must be boolean')
    _require(branch.get('fallbacks') == int(record['fallback']), 'C1 fallback counter mismatch')
    _require(bool(record.get('fallback_reason')) == record['fallback'], 'C1 fallback reason mismatch')
    _number(record.get('wall_seconds'), 'C1 record wall time')
    stages = record.get('stages')
    _require(isinstance(stages, list) and len(stages) <= 2, 'C1 unexpected solver stages')
    total_weight = sum(WEIGHTS[p['priority']] for p in patients)
    assigned_weight = total_weight - raw['unassigned_priority_weight']
    for index, stage in enumerate(stages):
        _require(stage.get('objective_name') == ('assigned_priority_weight' if index == 0
                 else 'priority_weighted_travel_minutes'), 'C1 wrong stage objective')
        _require(stage.get('status') in ('OPTIMAL', 'FEASIBLE', 'UNKNOWN', 'INFEASIBLE', 'MODEL_INVALID'),
                 'C1 unknown stage status')
        _number(stage.get('wall_seconds'), 'C1 stage wall time')
        bound = _number(stage.get('best_bound'), 'C1 stage bound')
        if stage['status'] in ('OPTIMAL', 'FEASIBLE'):
            value = _number(stage.get('objective'), 'C1 stage objective')
            _require(_equal_number(value, bound) if stage['status'] == 'OPTIMAL' else
                     (bound + 1e-8 >= value if index == 0 else bound - 1e-8 <= value),
                     'C1 stage objective/bound contradiction')
        else:
            _require(stage.get('objective') is None, 'C1 non-feasible stage has an objective')
        if index == 0 and stage['status'] in ('OPTIMAL', 'FEASIBLE'):
            _require(bound + 1e-8 >= assigned_weight, 'C1 primary bound below returned feasible plan')
    _require(sum(stage['wall_seconds'] for stage in stages) <= record['wall_seconds'] + 1e-6,
             'C1 stage time exceeds total time')
    if record.get('status') == 'OPTIMAL_EMPTY':
        _require(not any(options.values()) and not stages and not record['fallback']
                 and raw['assigned_count'] == 0, 'C1 false empty optimum')
        _require(all(record.get(k) is True for k in ('optimal_proven', 'primary_optimal_proven',
                 'secondary_optimal_proven')), 'C1 missing empty proof flags')
        return 'logical_empty'
    primary = bool(stages and stages[0]['status'] == 'OPTIMAL')
    secondary = bool(len(stages) == 2 and stages[1]['status'] == 'OPTIMAL')
    _require(not secondary or primary, 'C1 second-stage proof without primary optimum')
    _require(bool(record.get('primary_optimal_proven')) == primary, 'C1 false primary proof flag')
    _require(bool(record.get('secondary_optimal_proven')) == secondary, 'C1 false secondary proof flag')
    _require(bool(record.get('optimal_proven')) == (primary and secondary), 'C1 false overall proof flag')
    _require((record.get('status') == 'OPTIMAL') == (primary and secondary),
             'C1 final status contradicts proof flags')
    if record['status'] == 'FEASIBLE_PRIMARY_OPTIMAL':
        _require(primary and not secondary, 'C1 partial proof final status mismatch')
    if record['status'] in ('FALLBACK', 'MISSING_DEPENDENCY', 'UNKNOWN', 'INFEASIBLE',
                           'MODEL_INVALID', 'INVALID_INCUMBENT'):
        _require(record['fallback'], 'C1 unavailable solve without fallback disclosure')
    if len(stages) == 2:
        _require(primary, 'C1 second stage before proving primary optimum')
    if primary:
        _require(_equal_number(stages[0]['objective'], assigned_weight),
                 'C1 primary optimum differs from returned plan')
        if len(stages) == 2:
            _require(stages[1]['best_bound'] <= raw['priority_weighted_travel_minutes'] + 1e-8,
                     'C1 secondary bound above returned feasible plan')
    if secondary:
        _require(not record['fallback'], 'C1 optimum marked fallback')
        _require(_equal_number(stages[1]['objective'], raw['priority_weighted_travel_minutes']),
                 'C1 secondary optimum differs from returned plan')
    return 'cp_sat_two_stage' if primary and secondary else 'unproven'


def _check_provenance(report, expected_source_sha256, lock):
    _require(report.get('schema_version') == 1 and report.get('kind') == 'allocation_stress_comparison',
             'wrong stress report schema/kind')
    _require(report.get('synthetic') is True, 'missing report synthetic flag')
    sources = report.get('source_sha256')
    _require(isinstance(sources, dict) and sources, 'missing source provenance')
    if expected_source_sha256 is not None:
        _require(sources == expected_source_sha256, 'source hashes differ from expected manifest')
    _require(report.get('phase') in ('development', 'validation'), 'unknown experiment phase')
    _strings(report.get('families'), 'families')
    for field in ('sizes', 'seeds'):
        values = report.get(field)
        _require(isinstance(values, list) and values and len(values) == len(set(values)),
                 f'missing or duplicate {field}')
        for value in values:
            _integer(value, field, minimum=1 if field == 'sizes' else 0)
    settings = report.get('settings')
    _require(isinstance(settings, dict), 'missing settings')
    _require(settings.get('objective') == OBJECTIVE, 'objective definition mismatch')
    _require(settings.get('priority_weights') == WEIGHTS, 'priority weights mismatch')
    if lock is not None:
        _require(lock.get('kind') == 'allocation_stress_lock', 'wrong lock kind')
        _require(settings == lock.get('settings'), 'report settings differ from lock')
        _require(sources == lock.get('source_sha256'), 'report source hashes differ from lock')
        for field in ('families', 'sizes', 'runtime_versions'):
            _require(report.get(field) == lock.get(field), f'report {field} differ from lock')
        development, validation = lock.get('development_seeds'), lock.get('validation_seeds')
        _require(isinstance(development, list) and isinstance(validation, list)
                 and development and validation, 'missing development/validation seeds')
        _require(not set(development) & set(validation), 'development/validation seed overlap')
        _require(len(development) == len(set(development))
                 and len(validation) == len(set(validation)), 'duplicate lock seeds')
        _require(report['seeds'] == lock[f"{report['phase']}_seeds"], 'phase seed list differs from lock')
        if report['phase'] == 'validation':
            _require(report.get('lock_sha256') == _digest(lock), 'validation lock digest mismatch')
    _require(isinstance(report.get('runtime_versions'), dict)
             and all(isinstance(report['runtime_versions'].get(k), str)
                     and report['runtime_versions'][k] for k in ('python', 'ortools')),
             'missing runtime versions')


def _audit_reference(result, by_policy, raw, proofs):
    proven = [code for code in by_policy if proofs[code] in ('cp_sat_two_stage', 'logical_empty')]
    if proven:
        optimum = _objective(raw[proven[0]])
        _require(all(_objective(raw[code]) == optimum for code in proven),
                 'proven C1 objectives disagree')
        _require(all(_objective(value) >= optimum for value in raw.values()),
                 'branch claims an objective better than a proven optimum')
        reference_codes = proven
        kind, is_proven = 'proven_optimum', True
    else:
        optimum = min(map(_objective, raw.values()))
        reference_codes = [code for code in by_policy if _objective(raw[code]) == optimum]
        kind, is_proven = 'observed_best', False
    reference = result.get('reference')
    _require(isinstance(reference, dict) and reference.get('kind') == kind
             and reference.get('optimal_proven') is is_proven
             and reference.get('objective') == list(optimum)
             and reference.get('policy') in reference_codes, 'reference objective/proof mismatch')
    if proven:
        _require(reference['policy'] == min(proven, key=lambda code: (
                 by_policy[code]['budget_seconds'], code)), 'proven reference tie order mismatch')
    if not proven:
        _require(reference['policy'] == min(reference_codes), 'observed best tie order mismatch')
    gaps = result.get('objective_gaps')
    _require(isinstance(gaps, dict) and set(gaps) == set(by_policy), 'objective gap policy coverage mismatch')
    for code, value in raw.items():
        key = _objective(value)
        primary = key[0] - optimum[0]
        secondary = key[1] - optimum[1] if primary == 0 else None
        relative = (secondary / optimum[1] if optimum[1] else None) \
            if primary == 0 else None
        expected = dict(primary_difference=primary,
            secondary_difference_if_primary_equal=secondary,
            relative_secondary_gap_if_primary_equal=relative)
        _require(gaps[code] == expected, 'raw objective gap mismatch')
    return is_proven


def audit_report(report, *, expected_source_sha256=None, lock=None):
    """Validate saved results without importing the execution implementation."""
    _check_provenance(report, expected_source_sha256, lock)
    budgets = report['settings'].get('budgets_seconds')
    _require(isinstance(budgets, list) and budgets and len(budgets) == len(set(budgets)),
             'missing or duplicate budgets_seconds')
    for budget in budgets:
        _number(budget, 'budget', minimum=0)
    policies = ['R1', 'R2'] + [f'{algorithm}@{budget:g}'
                              for budget in budgets for algorithm in ('H1', 'C1')]
    counts = dict(cases=0, branches=0, constraint_violations=0, small_exact_cases=0,
        cp_sat_two_stage_proofs=0, logical_empty_proofs=0, unproven_cp_cases=0,
        heuristic_cases=0, proven_reference_cases=0,
        provenance_checked=expected_source_sha256 is not None or lock is not None)
    results = report.get('results')
    _require(isinstance(results, list) and results, 'missing results')
    seen, seen_ids, summary_rows = set(), set(), []
    for index, result in enumerate(results):
        snapshot = result.get('snapshot')
        patients, free, options = _raw_problem(snapshot)
        _require(snapshot['id'] not in seen_ids, 'duplicate snapshot ID')
        seen_ids.add(snapshot['id'])
        if 'at' in report['settings']:
            _require(snapshot['at'] == report['settings']['at'], 'snapshot time differs from settings')
        _require(snapshot.get('size') == len(patients), 'declared size differs from patient count')
        key = (snapshot.get('family'), snapshot.get('seed'), snapshot['size'])
        _require(key not in seen, 'duplicate family/seed/size case')
        seen.add(key)
        _require(result.get('case_index') == index, 'case index/order mismatch')
        shift = index % len(policies)
        _require(result.get('execution_order') == policies[shift:] + policies[:shift],
                 'execution order policy mismatch')
        combinations = math.prod(len(options[p['id']]) + 1 for p in patients)
        _require(result.get('combinations') == combinations, 'combination count mismatch')
        branches = result.get('branches')
        _require(isinstance(branches, list), 'missing branches')
        by_policy = {b.get('policy'): b for b in branches}
        _require(len(by_policy) == len(branches) and set(by_policy) == set(policies),
                 'branch policy coverage mismatch')
        _require([b['policy'] for b in branches] == result['execution_order'],
                 'branch order differs from execution order')
        raw, proofs = {}, {}
        for code, branch in by_policy.items():
            algorithm = code.split('@')[0]
            budget = float(code.split('@')[1]) if '@' in code else None
            _require(branch.get('algorithm') == algorithm and branch.get('budget_seconds') == budget,
                     'branch algorithm/budget mismatch')
            _require(branch.get('input_sha256') == _digest(snapshot), 'branch input digest mismatch')
            raw[code] = _raw_metrics(snapshot, branch.get('assignments'), free, options)
            _require(isinstance(branch.get('metrics'), dict), 'missing branch metrics')
            for field, value in raw[code].items():
                _require(branch['metrics'].get(field) == value, f'raw metric mismatch: {field}')
            _number(branch.get('wall_seconds'), 'branch wall time')
            if algorithm == 'H1':
                proof = _audit_h1(branch, raw[code], snapshot, patients, free, options)
                if 'h1_max_moves' in report['settings']:
                    _require(branch['solver_records'][0]['max_moves'] == report['settings']['h1_max_moves'],
                             'H1 operation limit differs from settings')
                counts['heuristic_cases'] += 1
            elif algorithm == 'C1':
                proof = _audit_cp(branch, raw[code], patients, options)
                counts[{'cp_sat_two_stage': 'cp_sat_two_stage_proofs', 'logical_empty': 'logical_empty_proofs',
                        'unproven': 'unproven_cp_cases'}[proof]] += 1
            else:
                _require(branch.get('solver_records') == [] and branch.get('fallbacks') == 0,
                         'sequential baseline has solver/fallback records')
                proof = 'baseline'
            proofs[code] = proof
            counts['branches'] += 1
        _require(by_policy['R1']['assignments'] == _nearest_greedy(patients, free, options),
                 'R1 differs from documented nearest greedy')
        common_r1 = result.get('common_travel_vs_r1')
        _require(isinstance(common_r1, dict) and set(common_r1) == set(policies),
                 'common R1 policy coverage mismatch')
        common_cp = result.get('common_travel_vs_c1_same_budget')
        _require(isinstance(common_cp, dict)
                 and set(common_cp) == {f'H1@{budget:g}' for budget in budgets},
                 'common C1 policy coverage mismatch')
        for code in policies:
            _require(common_r1[code] == _common(snapshot, by_policy['R1']['assignments'],
                     by_policy[code]['assignments']), 'common-patient R1 denominator/value mismatch')
        for budget in budgets:
            h1, cp = f'H1@{budget:g}', f'C1@{budget:g}'
            _require(common_cp[h1] == _common(snapshot, by_policy[cp]['assignments'],
                     by_policy[h1]['assignments']), 'common-patient C1 denominator/value mismatch')
        proven = _audit_reference(result, by_policy, raw, proofs)
        counts['proven_reference_cases'] += int(proven)
        if combinations <= 20000:
            optimum = _exact_objective(patients, free, options)
            _require(all(_objective(value) >= optimum for value in raw.values()),
                     'plan claims an objective below independent exact optimum')
            if proven:
                _require(tuple(result['reference']['objective']) == optimum,
                         'proven C1 differs from independent exact optimum')
            counts['small_exact_cases'] += 1
        summary_rows.append((result, by_policy, raw, proofs))
        counts['cases'] += 1
    expected = {(family, seed, size) for family in report['families']
                for seed in report['seeds'] for size in report['sizes']}
    _require(seen == expected, 'missing or unexpected family/seed/size case coverage')
    expected_order = [(family, seed, size) for seed in report['seeds']
                      for family in report['families'] for size in report['sizes']]
    _require([(row[0]['snapshot']['family'], row[0]['snapshot']['seed'], row[0]['snapshot']['size'])
              for row in summary_rows] == expected_order, 'case coverage order differs from plan')
    _audit_summary(report, summary_rows, policies, budgets)
    return counts


def _audit_summary(report, rows, policies, budgets):
    # The exact summary contract is checked below; kept separate from the raw
    # feasibility path so it cannot substitute saved metrics for input facts.
    summaries = report.get('summary')
    _require(isinstance(summaries, list), 'missing summary')
    by_family = {row.get('family'): row for row in summaries}
    _require(len(by_family) == len(summaries)
             and set(by_family) == set(report['families']) | {'ALL'}, 'summary family coverage mismatch')
    for family, summary in by_family.items():
        selected = [row for row in rows if family == 'ALL' or row[0]['snapshot']['family'] == family]
        _require(summary.get('cases') == len(selected), 'summary case count mismatch')
        _require(summary.get('proven_reference_count') == sum(
                 row[0]['reference']['optimal_proven'] for row in selected), 'summary proven reference mismatch')
        _require(isinstance(summary.get('policies'), dict)
                 and set(summary['policies']) == set(policies), 'summary policy coverage mismatch')
        for code in policies:
            observations = [(row[1][code], row[2][code], row[3][code]) for row in selected]
            walls = [branch['wall_seconds'] for branch, _, _ in observations]
            statuses = Counter(record.get('status', 'UNSPECIFIED')
                               for branch, _, _ in observations for record in branch['solver_records'])
            if not statuses:
                statuses['NO_PROOF'] = len(observations)
            expected = dict(cases=len(observations),
                mean_unassigned_count=statistics.mean(raw['unassigned_count'] for _, raw, _ in observations),
                mean_unassigned_priority_weight=statistics.mean(raw['unassigned_priority_weight'] for _, raw, _ in observations),
                mean_priority_weighted_travel_minutes=statistics.mean(raw['priority_weighted_travel_minutes'] for _, raw, _ in observations),
                unassigned_by_priority_totals={str(tier): sum(raw['unassigned_by_priority'][str(tier)]
                    for _, raw, _ in observations) for tier in (4, 2, 1)},
                wall_median_seconds=statistics.median(walls),
                wall_p95_seconds=sorted(walls)[math.ceil(.95 * len(walls)) - 1], wall_max_seconds=max(walls),
                fallbacks=sum(branch['fallbacks'] for branch, _, _ in observations),
                optimal_proven_count=sum(proof in ('cp_sat_two_stage', 'logical_empty') for _, _, proof in observations),
                proof_status_counts=dict(statuses), constraint_violations=0)
            _require(summary['policies'][code] == expected, 'summary raw aggregate mismatch')
        comparisons = summary.get('h1_vs_c1')
        _require(isinstance(comparisons, dict) and set(comparisons) == {f'{budget:g}' for budget in budgets},
                 'summary H1/C1 budget coverage mismatch')
        for budget in budgets:
            h1, cp = f'H1@{budget:g}', f'C1@{budget:g}'
            comparison_counts = Counter(dict(better=0, equal=0, worse=0))
            common_rows = []
            for result, branches, raw, _ in selected:
                hvalue, cvalue = _objective(raw[h1]), _objective(raw[cp])
                comparison_counts['better' if hvalue < cvalue else 'worse' if hvalue > cvalue else 'equal'] += 1
                common_rows.append(_common(result['snapshot'], branches[cp]['assignments'], branches[h1]['assignments']))
            denominator = sum(row['denominator'] for row in common_rows)
            total = sum(row['total_difference_minutes'] for row in common_rows)
            common = dict(denominator=denominator, total_difference_minutes=total,
                mean_difference_minutes=total / denominator if denominator else None,
                comparison_only=sum(row['comparison_only'] for row in common_rows),
                baseline_only=sum(row['baseline_only'] for row in common_rows))
            _require(comparisons[f'{budget:g}'] == dict(comparison_counts, common_travel=common),
                     'summary H1/C1 comparison or denominator mismatch')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('report', type=Path)
    parser.add_argument('--lock-file', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding='utf-8'))
    lock = json.loads(args.lock_file.read_text(encoding='utf-8')) if args.lock_file else None
    expected = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in report['source_sha256']}
    result = dict(passed=True, **audit_report(report, expected_source_sha256=expected, lock=lock),
        report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
        auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')


if __name__ == '__main__':
    main()

