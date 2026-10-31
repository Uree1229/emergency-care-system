"""Independent G5 input/constraint/event audit; never executes an allocator.

Unchanged G2 proposal mathematics are checked in an isolated hypothetical
decision view. Such a seed is never relabelled as a full executed G2 arm.
Whole-episode comparisons must use separately executed or hash-bound G2 data.
"""
from collections import Counter
from copy import deepcopy
import math

from . import online_allocation_audit as base
from . import final_assignment_audit_20261028 as common
from . import arrival_guard_priority_audit_20261028 as g2_audit

EXPERIMENT_ID = 'g2_preserving_20261029'
G2_SOURCE_SHA256 = '57373bf5fa1723adbd284274b7b63ba52f8c5665d02e94c6a78ae258fd759878'
CONSTRAINTS = dict(selected_ids='exact_G2_selected_set', priority4='same_G2_destination',
    resources='same_per_hospital_resource_footprint', travel='per_selected_patient_nonincrease',
    change_requires='strict_travel_improvement_for_at_least_one_selected_patient')
SCOPE = 'current accepted-offer snapshot only; no whole-episode or clinical nonregression guarantee'
CONTRACT = dict(inquiry_policy='fixed_three_with_first_acceptance_extra_wait_two_minutes',
    joint_scope='currently_ready_same_minute_received_accepted_offers_only',
    constraints=CONSTRAINTS, total_max_work_units=2048, safety_seconds=10.0,
    seed='unchanged_G2_or_proven_equivalent_caller_baseline_shortcut',
    future_events_supplied_to_rule=False, simulation_clock='integer_minute',
    wall_time_added_to_simulation_clock=False, whole_episode_nonregression_guaranteed=False,
    whole_episode_priority4_nonregression_guaranteed=False, whole_episode_optimal_proven=False,
    clinical_priority_policy_validated=False)
ASSUMPTIONS = [
    'G5 locks G2 selected IDs, experimental weight-4 destinations, hospital resource totals and individual travel upper bounds in the current snapshot.',
    'Singleton or all-assigned caller baselines are logically equivalent G2 seed shortcuts; no H1 execution is fabricated for them.',
    'G2 parent search and G5 partial-node refinement share a total 2048-unit deterministic work budget.',
    'Current resource footprint equality does not guarantee equal future releases or whole-episode outcomes; all gates require full replay.',
    'Experimental weights 1, 2, 4 are simulation settings, not validated clinical severity or priority rules.',
]
require = base._require


def _footprint(rows, plan):
    used = Counter()
    for row in rows:
        if plan[row['id']] is not None:
            used.update({(plan[row['id']], res): q for res, q in row['demand'].items() if q})
    return [dict(hospital=hid, resource=res, quantity=q) for (hid, res), q in sorted(used.items()) if q]


def _cost(rows, travel, seed, plan):
    picked = [p for p in rows if plan[p['id']] is not None]
    return [sum(p['priority'] * travel[p['id'], plan[p['id']]] for p in picked),
            sum(travel[p['id'], plan[p['id']]] for p in picked),
            sum(plan[pid] != hid for pid, hid in seed.items())]


def _finite(value, name):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, name)
    return value


def _batch(physical, decision, record, index, totals):
    rows, free, options, travel = base._problem(physical, decision)
    seed, plan = record['g2_plan'], decision['assignments']
    require(record['decision_index'] == index and record['t'] == decision['t'], 'G5 record references another decision')
    require(record['schema_version'] == 1 and record['rule'] == 'G5' and record['scope'] == SCOPE,
            'G5 record identity differs')
    require(record['status'] == 'G2_PRESERVING_PARETO' and record['constraints'] == CONSTRAINTS,
            'G5 constraint contract differs')
    require(base._plan_feasible(rows, free, options, seed), 'infeasible G2 seed')
    require(record['actual_plan'] == plan and base._plan_feasible(rows, free, options, plan),
            'G5 actual plan differs or is infeasible')
    baseline = g2_audit._nearest(rows, free, options, travel)
    mode = record['g2_seed_mode']
    if mode == 'unchanged_G2_parent':
        parent = deepcopy(record['parent_g2_record'])
        require(parent is not None and parent['actual_plan'] == seed, 'unchanged G2 seed missing')
        # This projection audits one hypothetical current G2 decision only.
        # Actual G5 events and endpoints are independently checked below.
        parent['decision_index'] = 0
        view = deepcopy(decision)
        obj = base._objective(rows, seed, travel)
        view.update(rule='G2', method='guarded_joint', assignments=deepcopy(seed),
            objective=dict(unassigned_priority_weight=obj[0], priority_weighted_travel_minutes=obj[1]),
            guard_record_index=0, guard_status='GUARDED_HEURISTIC')
        checked = g2_audit._guard_decisions(physical, dict(guard_records=[parent],
            solver_records=[], heuristic_records=[], allocation_decisions=[view]))
        totals.update(checked)
        parent_work = parent['original_h1_record']['evaluated_moves']
        totals['unchanged_G2_seeds'] += 1
    else:
        require(mode in ('singleton_guard_cannot_gain', 'all_ready_already_assigned'), 'unknown G2 seed mode')
        require(record['parent_g2_record'] is None and seed == baseline, 'shortcut fabricated a parent run or changed baseline')
        if mode == 'singleton_guard_cannot_gain':
            require(len(rows) == 1, 'singleton shortcut on a joint problem')
        else:
            require(len(rows) > 1 and all(hid is not None for hid in baseline.values()),
                    'all-assigned shortcut with missed caller patients')
        parent_work = 0
        totals['logical_G2_seed_shortcuts'] += 1
    require(record['parent_g2_source_sha256'] == G2_SOURCE_SHA256, 'G2 source binding differs')
    selected = [p['id'] for p in rows if seed[p['id']] is not None]
    require({pid for pid, hid in plan.items() if hid is not None} == set(selected), 'G2 selected patient removed or replaced')
    require(record['selected_patient_ids'] == selected, 'selected ID evidence differs')
    locked = {p['id']: seed[p['id']] for p in rows if p['priority'] == 4 and seed[p['id']] is not None}
    require(record['priority4_destinations'] == locked and all(plan[pid] == hid for pid, hid in locked.items()),
            'weight-4 current destination changed')
    before, after = _footprint(rows, seed), _footprint(rows, plan)
    require(before == after == record['g2_resource_footprint'] == record['actual_resource_footprint'],
            'hospital resource consumption differs from G2')
    saved = {pid: travel[pid, seed[pid]]-travel[pid, plan[pid]] for pid in selected}
    require(record['travel_saved_minutes'] == saved and all(value >= 0 for value in saved.values()),
            'selected patient travel worsened or evidence differs')
    if plan != seed:
        require(any(value > 0 for value in saved.values()), 'changed plan without a strict travel gain')
    require(record['g2_travel_objective'] == _cost(rows, travel, seed, seed)
            and record['actual_travel_objective'] == _cost(rows, travel, seed, plan), 'travel objective differs')
    expected_reason = 'strict_G2_preserving_pareto_refinement' if plan != seed else 'G2_plan_preserved'
    require(record['reason'] == expected_reason, 'G5 refinement reason differs')
    require(type(parent_work) is int and 0 <= parent_work <= 2048, 'invalid parent search count')
    work = record['work_units']
    require(type(work) is int and 0 <= work <= 2048-parent_work, 'refinement budget exceeded')
    require(record['parent_work_units'] == parent_work and record['total_work_units'] == work+parent_work
            and record['max_refinement_work_units'] == 2048-parent_work
            and record['total_max_work_units'] == 2048, 'total work accounting differs')
    for key in ('evaluated_plans', 'feasible_policy_plans', 'accepted_improvements'):
        require(type(record[key]) is int and 0 <= record[key] <= work, 'invalid refinement work counter')
    require(record['accepted_improvements'] <= record['feasible_policy_plans'] <= record['evaluated_plans'],
            'inconsistent refinement counts')
    require(record['search_stop_reason'] in ('pareto_envelope_complete', 'no_strict_pareto_edge', 'work_limit'),
            'unknown refinement stop reason')
    require(record['safety_seconds'] == 10.0 and _finite(record['wall_seconds'], 'invalid G5 wall time') < 10.0
            and record['wall_seconds'] <= decision['elapsed_seconds'] + 1e-9, 'G5 safety budget exhausted')
    for key in ('optimal_proven', 'whole_episode_nonregression_guaranteed',
                'whole_episode_priority4_nonregression_guaranteed', 'clinical_priority_policy_validated'):
        require(record[key] is False, 'unproved guarantee in G5 record')
    totals['preserving_batches'] += 1
    totals['refined_batches'] += plan != seed
    totals['selected_current_patients'] += len(selected)
    totals['locked_current_priority4_patients'] += len(locked)
    totals['direct_current_travel_saved_minutes'] += sum(saved.values())
    totals['refinement_work_units'] += work
    totals['total_work_units'] += work + parent_work


def audit_replay(replay, *, source_scenario=None):
    original = replay['original_scenario']
    if source_scenario is not None:
        require(original == source_scenario, 'original scenario changed')
    fingerprint = common.canonical_sha256(original)
    require(replay['schema_version'] == 1 and replay['source_experiment'] == 'online_allocation_comparison'
            and replay['comparison_id'] == EXPERIMENT_ID, 'wrong G5 replay identity')
    require(replay['input_scenario_sha256'] == replay['original_scenario_sha256'] == fingerprint,
            'G5 input hash differs')
    physical = deepcopy(original)
    physical.update(online_allocation_comparison=True, source_experiment='online_allocation_comparison',
                    comparison_id=EXPERIMENT_ID)
    physical.setdefault('assumptions', []).extend(ASSUMPTIONS)
    require(replay['scenario'] == physical, 'G5 changed physical scenario inputs')
    require(replay['execution_order'] == ['G5'] and len(replay['branches']) == 1, 'single G5 branch required')
    branch = replay['branches'][0]
    require(branch['id'] == branch['policy']['name'] == branch['policy']['rule'] == 'G5', 'G5 policy identity differs')
    require(branch['comparison_id'] == EXPERIMENT_ID and branch['source_experiment'] == 'online_allocation_comparison',
            'G5 branch experiment differs')
    require(branch['original_scenario_sha256'] == fingerprint
            and branch['routing_input_sha256'] == replay['routing_input_sha256'], 'G5 branch input binding differs')
    require(branch['execution_contract'] == branch['policy']['g2_preserving'] == CONTRACT, 'G5 execution contract differs')
    policy = branch['policy']
    for key, value in dict(k=3, wait=2, notify=True).items():
        require(type(policy[key]) is type(value) and policy[key] == value, 'G5 inquiry setting changed')
    optional = dict(near_minutes=None, expand_after=None, inquiry_ranking='travel', load_weight=0,
                    load_window=6, adaptive_inquiry=None, observed_inquiry=None)
    require(set(policy) <= {'name', 'rule', 'k', 'wait', 'notify', 'g2_preserving'} | set(optional),
            'unrecognized G5 policy setting')
    for key, value in optional.items():
        require(policy.get(key, value) == value, 'G5 nonshared inquiry setting')
    require(branch.get('solver_records') == [], 'unexpected solver evidence')
    routing = replay['routing_summary']
    require({k: routing.get(k) for k in ('provider', 'scope', 'external_route_provider_allowed',
        'live_traffic', 'ambulance_priority', 'shared_mutable_routing_cache')} == dict(provider='synthetic',
        scope='independent_per_branch', external_route_provider_allowed=False,
        live_traffic=False, ambulance_priority=False, shared_mutable_routing_cache=False), 'G5 route contract differs')
    require(set(routing['by_branch']) == {'G5'}, 'G5 route branch differs')
    route = routing['by_branch']['G5']
    require(route['provider'] == 'synthetic' and route['scope'] == 'scenario'
            and route['road_routes'] == route['fallback_routes'] == 0
            and route['synthetic_routes'] == route['routes_evaluated'], 'non-synthetic G5 route')
    # General audit verifies accepted-offer evidence, all actual commits and raw
    # endpoint accounting; it does not assume that G5 is an optimal allocator.
    general = base.audit_branch(physical, branch, exact_limit=0)
    totals = Counter({k:v for k,v in general['counts'].items() if type(v) is int})
    records = branch['preserving_records']
    used = set()
    for index, decision in enumerate(branch['allocation_decisions']):
        require(decision['rule'] == 'G5' and decision.get('optimal_proven') is False, 'wrong G5 decision identity')
        if decision['kind'] == 'single':
            rows, free, options, travel = base._problem(physical, decision)
            require(decision['method'] == 'nearest_current_offer'
                and decision['assignments'] == g2_audit._nearest(rows, free, options, travel), 'single G5 reroute differs')
            require('preserving_record_index' not in decision, 'single reroute references a batch')
            continue
        require(decision['kind'] == 'batch' and decision['method'] == 'G2_preserving_pareto', 'wrong G5 batch method')
        ri = decision['preserving_record_index']
        require(type(ri) is int and 0 <= ri < len(records) and ri not in used, 'missing or reused G5 record')
        used.add(ri)
        _batch(physical, decision, records[ri], index, totals)
    require(used == set(range(len(records))), 'orphan G5 record')
    require(branch['metrics']['preserving_calls'] == len(records)
            and branch['metrics']['allocation_decisions'] == len(branch['allocation_decisions']), 'G5 call metrics differ')
    recorded_seconds = sum(d['elapsed_seconds'] for d in branch['allocation_decisions'])
    require(math.isclose(branch['metrics']['allocation_wall_seconds'], recorded_seconds, abs_tol=1e-9), 'G5 runtime metrics differ')
    require(replay['duration_minutes'] == max((e['t'] for e in branch['events']), default=0), 'G5 event duration differs')
    return dict(schema_version=1, passed=True, input_sha256=fingerprint,
        branch_metrics={'G5':common.branch_metrics(physical, branch)},
        patient_vectors={'G5':common._vectors(physical, branch)}, counters=dict(totals),
        G2_seed_scope='hypothetical_current_snapshot; not a counterfactual whole-episode G2 arm',
        allocator_reexecuted=False, whole_episode_optimality_verified=False,
        whole_episode_nonregression_verified=False, clinical_effect_verified=False,
        full_resource_history_replayed=False,
        snapshot_resource_values_reconstructed_from_history=False,
        recorded_current_resource_equations_verified=True)
