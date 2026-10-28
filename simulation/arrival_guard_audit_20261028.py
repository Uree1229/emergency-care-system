"""Independent recorded-input G1 audit, with complete R0/R1/old-H1 references.

This module never imports a simulator, allocation implementation or solver. It
reconstructs G1's public-snapshot guard mathematics and checks event endpoints.
The nested H1 evidence is checked as a hypothetical proposal, never relabelled
as the plan actually committed by G1. No full resource history or online optimum
is proved. Cached known controls are hash-bound prior evidence, not fresh runs.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

from . import online_allocation_audit as base
from . import h1_online_audit as inherited
from . import final_assignment_audit_20261028 as oldfinal

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = 'arrival_guard_20261028'
POLICY_IDS = ('R0', 'R1', 'H1', 'G1')
GUARD_RULE = 'strict_current_count_gain_and_no_greater_baseline_priority_miss_else_caller_order_nearest'
SCOPE = 'current supplied accepted-offer snapshot only; caller-order nearest is not a whole-episode R1 replay; H1 priority-only improvement may be rejected; no global success or arrival guarantee'
PARENT_SOURCE_SHA256 = '17ace2f204915695d13a7f69a2dca61236f0c25e80c79030ac294e261c1dcfcb'
KNOWN_REPORT_PATH = ROOT / 'simulation_results/final_assignment_validation_20261028.json'
KNOWN_REPORT_SHA256 = 'cc7cea4391d5135bfe0086f436452bed0a4230730f61f0ac96e01631e1be14ad'
ASSUMPTION_SUFFIX = [
    'G1은 현재 caller-order 거리순 기준안보다 인원이 엄격히 늘고 가중 미배정이 늘지 않을 때만 원 H1 제안을 사용한다.',
    '같은 인원에서 우선순위만 바꾸는 원 H1 제안은 거절될 수 있다. 현재 H1 목적이 변경된 별도 연구 후보다.',
    '기준안은 같은 현재 공동 입력의 거리순이며 실제 R1 전체 과정의 반사실 재생이 아니다.',
    '고정3 문의·첫 수용 뒤 최대2분 대기·알림과 외부 잠재 환경은 동일하며 배정 후 자원과 회신은 달라질 수 있다.',
    '현재 배치의 보호 조건은 전체 성공·환자별 도착 비퇴행 또는 온라인 최적성을 보장하지 않는다.',
]
GUARD_METADATA = dict(parent_h1_max_moves=2048, parent_h1_safety_seconds=10.0,
    guard_rule=GUARD_RULE, current_H1_objective_changed=True,
    whole_episode_nonregression_guaranteed=False)
EXECUTION_CONTRACT = dict(inquiry_policy='fixed_three_with_first_acceptance_extra_wait_two_minutes',
    joint_scope='currently_ready_same_minute_received_accepted_offers_only',
    guard_rule=GUARD_RULE, parent_h1_max_moves=2048, parent_h1_safety_seconds=10.0,
    objective_change_disclosed=True, old_H1_local_objective_nonregression_claimed=False,
    whole_episode_nonregression_guaranteed=False, whole_episode_optimal_proven=False,
    simulation_clock='integer_minute', wall_time_added_to_simulation_clock=False,
    information_observation='complete_current_resources_and_received_offers; no future schedules passed to allocation rule')
ArrivalGuardAuditError = base.OnlineAllocationAuditError
canonical_sha256 = oldfinal.canonical_sha256
require = base._require


def _same(actual, expected, label):
    require(base._same(actual, expected), label)


def _finite(value, name):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'invalid ' + name)
    return value


def _nearest(patients, free, options, travel):
    """Independent caller order, deliberately distinct from H1 priority sort."""
    plan, used = {}, Counter()
    for patient in patients:
        pid, demand = patient['id'], patient['demand']
        feasible = [hid for hid in options[pid] if all(
            free.get((hid, resource), 0) - used[(hid, resource)] >= quantity
            for resource, quantity in demand.items() if quantity)]
        selected = min(feasible, key=lambda hid: (travel[pid, hid], hid)) if feasible else None
        plan[pid] = selected
        if selected is not None:
            for resource, quantity in demand.items():
                used[selected, resource] += quantity
    return plan


def _parent_proposal(scenario, decision, record, patients, free, options):
    """Check hypothetical H1 evidence in an isolated copy, not actual commits."""
    plan = record['original_h1_plan']
    require(base._plan_feasible(patients, free, options, plan), 'original H1 proposal infeasible')
    original = record['original_h1_record']
    require('decision_index' not in original, 'original H1 record was overwritten with replay metadata')
    require(original.get('assignments') == plan, 'original H1 proposal evidence differs')
    require(original.get('search_stop_reason') in ('search_complete', 'move_limit', 'no_eligible_offers'),
        'parent H1 deadline or unknown stop invalidates candidate')
    require(_finite(original['search_seconds'], 'parent search seconds') < 10.0,
        'parent H1 safety duration reached')
    projected_record = deepcopy(original)
    projected_record['decision_index'] = 0
    projected_decision = deepcopy(decision)
    projected_decision.pop('guard_record_index', None)
    projected_decision.pop('guard_status', None)
    objective = record['original_h1_objective']
    projected_decision.update(rule='H1', method='heuristic', assignments=deepcopy(plan),
        heuristic_record_index=0, objective=dict(unassigned_priority_weight=objective[0],
        priority_weighted_travel_minutes=objective[1]))
    view = dict(policy=dict(rule='H1', heuristic=dict(backend='bounded_joint_heuristic',
        time_limit_seconds=10.0, max_moves=2048,
        time_budget_scope='optional_search_after_snapshot_and_baseline')),
        metrics=dict(whole_episode_optimal_proven=False), solver_records=[],
        heuristic_records=[projected_record], allocation_decisions=[projected_decision])
    return inherited.audit_h1_branch(scenario, view, oldfinal.H1_SPEC)


def _guard_decisions(scenario, branch):
    records = branch['guard_records']
    require(type(records) is list, 'guard record list required')
    require(branch.get('solver_records') == [] and branch.get('heuristic_records') == [],
        'G1 actual records mixed with solver or old H1 records')
    used, totals = set(), Counter()
    for index, decision in enumerate(branch['allocation_decisions']):
        require(decision.get('rule') == 'G1', 'G1 decision identity differs')
        require('heuristic_record_index' not in decision and 'solver_record_index' not in decision,
            'G1 decision references another policy record')
        require(decision.get('optimal_proven', False) is False, 'G1 falsely claims optimality')
        patients, free, options, travel = base._problem(scenario, decision)
        if decision['kind'] == 'single':
            require('guard_record_index' not in decision, 'single decision references guard batch')
            require(decision['method'] == 'nearest_current_offer' and len(patients) == 1,
                'G1 single method differs')
            require(decision['assignments'] == _nearest(patients, free, options, travel),
                'G1 single current-offer choice mismatch')
            totals['guard_single_choices_checked'] += 1
            continue
        require(decision['kind'] == 'batch' and decision['method'] == 'guarded_joint', 'G1 batch method differs')
        ri = base._integer(decision['guard_record_index'], 'invalid guard record index')
        require(ri < len(records) and ri not in used, 'guard record missing or reused')
        used.add(ri)
        record = records[ri]
        require(record['schema_version'] == 1 and record['rule'] == GUARD_RULE
            and record['scope'] == SCOPE, 'guard rule or scope differs')
        require(record['t'] == decision['t'] and record['decision_index'] == index, 'guard record points to another decision')
        require(record['status'] == decision.get('guard_status') == 'GUARDED_HEURISTIC'
            and record['optimal_proven'] is False, 'guard record status differs')
        for key, expected in dict(objective_change_disclosed=True,
            old_H1_local_objective_nonregression_claimed=False,
            whole_episode_nonregression_guaranteed=False,
            actual_arrival_nonregression_guaranteed=False).items():
            require(record.get(key) is expected, 'guard limitation disclosure differs: ' + key)
        require(record['parent_h1_source_sha256'] == PARENT_SOURCE_SHA256
            and record['parent_h1_max_moves'] == 2048 and record['parent_h1_safety_seconds'] == 10.0,
            'parent H1 source or budget differs')
        require(record['caller_ready_order'] == [p['id'] for p in patients], 'caller ready order changed')
        baseline = _nearest(patients, free, options, travel)
        parent = record['original_h1_plan']
        require(base._plan_feasible(patients, free, options, parent), 'original H1 proposal infeasible')
        bobj, hobj = base._objective(patients, baseline, travel), base._objective(patients, parent, travel)
        bc, hc = sum(h is not None for h in baseline.values()), sum(h is not None for h in parent.values())
        gain, priority_ok = hc > bc, hobj[0] <= bobj[0]
        use_parent = gain and priority_ok
        actual = parent if use_parent else baseline
        reason = ('strict_current_count_gain_without_baseline_priority_cost' if use_parent else
            'rejected_current_count_gain_with_baseline_priority_cost' if gain else
            'preserve_caller_nearest_without_strict_current_count_gain')
        expected = dict(baseline_ready_order_r1_plan=baseline, actual_plan=actual,
            baseline_objective=bobj, original_h1_objective=hobj,
            actual_objective=base._objective(patients, actual, travel),
            baseline_assigned_count=bc, original_h1_assigned_count=hc,
            actual_assigned_count=sum(h is not None for h in actual.values()), reason=reason)
        for key, value in expected.items():
            _same(record[key], value, 'guard mathematics mismatch: ' + key)
        for key, value in dict(strict_current_count_gain=gain,
            baseline_priority_cost_nonincrease=priority_ok, used_h1_proposal=use_parent).items():
            require(record[key] is value, 'guard boolean mismatch: ' + key)
        require(decision['assignments'] == actual, 'actual G1 plan differs from reconstructed guard')
        wall = _finite(record['wall_seconds'], 'guard wall seconds')
        require(_finite(record['original_h1_record']['wall_seconds'], 'parent wall seconds') <= wall + 1e-9
            and wall <= decision['elapsed_seconds'] + 1e-9, 'guard or parent timing exceeds enclosing decision')
        totals.update(_parent_proposal(scenario, decision, record, patients, free, options))
        totals['guard_batches_checked'] += 1
        totals['guard_parent_proposals_used'] += use_parent
        totals['guard_baselines_used'] += not use_parent
    require(used == set(range(len(records))), 'orphan guard record')
    return totals


def _runtime(branch):
    decisions, records = branch['allocation_decisions'], branch['guard_records']
    expected = dict(allocation_decisions=len(decisions),
        allocation_wall_seconds=sum(_finite(d['elapsed_seconds'], 'decision seconds') for d in decisions),
        solver_calls=0, solver_wall_seconds=0, solver_fallbacks=0, solver_optimal_batches=0,
        heuristic_calls=0, heuristic_wall_seconds=0, heuristic_optimal_batches=0,
        guard_calls=len(records), guard_wall_seconds=sum(r['wall_seconds'] for r in records),
        parent_h1_proposal_calls=len(records),
        parent_h1_proposal_wall_seconds=sum(r['original_h1_record']['wall_seconds'] for r in records))
    for key, value in expected.items():
        _same(branch['metrics'][key], value, 'G1 runtime metric mismatch: ' + key)
    require(branch['metrics']['optimality_scope'] == 'current_same_minute_accepted_offers_only'
        and branch['metrics']['whole_episode_optimal_proven'] is False, 'G1 runtime optimality scope differs')


def _policy(branch):
    require(branch['id'] == branch['policy']['name'] == branch['policy']['rule'] == 'G1', 'G1 policy identity differs')
    policy = branch['policy']
    for key, value in dict(k=3, wait=2, notify=True).items():
        require(type(policy[key]) is type(value) and policy[key] == value, 'G1 inquiry setting changed: ' + key)
    optional = dict(near_minutes=None, expand_after=None, inquiry_ranking='travel', load_weight=0,
        load_window=6, adaptive_inquiry=None, observed_inquiry=None)
    require(set(policy) <= {'name', 'rule', 'k', 'wait', 'notify', 'arrival_guard'} | set(optional),
        'unrecognized G1 policy setting')
    for key, value in optional.items():
        require(policy.get(key, value) == value, 'G1 nonshared inquiry setting: ' + key)
    _same(policy['arrival_guard'], GUARD_METADATA, 'G1 policy guard metadata differs')
    _same(branch['execution_contract'], EXECUTION_CONTRACT, 'G1 execution contract differs')


def audit_candidate(replay, *, source_scenario=None, scenario_sha256=None):
    """Audit one recorded G1 arm, without executing an algorithm or simulator."""
    try:
        require(replay['schema_version'] == 2 and replay['source_experiment'] == 'online_allocation_comparison'
            and replay['comparison_id'] == EXPERIMENT_ID, 'wrong G1 replay identity')
        source = replay['original_scenario'] if source_scenario is None else source_scenario
        require(replay['original_scenario'] == source, 'G1 original source changed')
        fingerprint = canonical_sha256(source)
        require(replay['original_scenario_sha256'] == replay['input_scenario_sha256'] == fingerprint,
            'G1 original scenario hash differs')
        if scenario_sha256 is not None:
            require(fingerprint == scenario_sha256, 'G1 input manifest hash differs')
        physical = deepcopy(source)
        physical.update(online_allocation_comparison=True, source_experiment='online_allocation_comparison', comparison_id=EXPERIMENT_ID)
        physical.setdefault('assumptions', []).extend(ASSUMPTION_SUFFIX)
        _same(replay['scenario'], physical, 'G1 scenario augmentation changed physical inputs')
        require([b['id'] for b in replay['branches']] == ['G1'] and replay['execution_order'] == ['G1'],
            'G1 candidate branch partition differs')
        branch = replay['branches'][0]
        _policy(branch)
        require(branch['source_experiment'] == 'online_allocation_comparison' and branch['comparison_id'] == EXPERIMENT_ID,
            'G1 branch identity differs')
        route_sha = replay['routing_input_sha256']
        require(type(route_sha) is str and len(route_sha) == 64 and all(c in '0123456789abcdef' for c in route_sha),
            'G1 routing fingerprint malformed')
        require(branch['original_scenario_sha256'] == fingerprint and branch['routing_input_sha256'] == route_sha,
            'G1 branch input identity differs')
        routing = replay['routing_summary']
        _same({key:routing.get(key) for key in ('provider', 'scope', 'live_traffic', 'ambulance_priority',
            'shared_mutable_routing_cache', 'external_route_provider_allowed')}, dict(provider='synthetic',
            scope='independent_per_branch', live_traffic=False, ambulance_priority=False,
            shared_mutable_routing_cache=False, external_route_provider_allowed=False), 'G1 routing contract differs')
        require(set(routing['by_branch']) == {'G1'}, 'G1 route branch partition differs')
        route = routing['by_branch']['G1']
        require(route['provider'] == 'synthetic' and route['scope'] == 'scenario' and route['live_traffic'] is False
            and route['ambulance_priority'] is False and route['road_routes'] == route['fallback_routes'] == 0
            and route['synthetic_routes'] == route['routes_evaluated'], 'G1 non-synthetic route observation')
        audited = base.audit_branch(physical, branch, exact_limit=0)
        counters = Counter({k:v for k,v in audited['counts'].items() if type(v) is int})
        counters.update(_guard_decisions(physical, branch))
        _runtime(branch)
        if 'online_summary' in branch:
            _same(branch['online_summary'], audited['metrics'], 'G1 online summary differs from raw events')
        require(replay['duration_minutes'] == max((e['t'] for e in branch['events']), default=0), 'G1 duration differs from events')
        vectors = oldfinal._vectors(physical, branch)
        return dict(schema_version=1, passed=True, experiment_id=EXPERIMENT_ID, scenario_sha256=fingerprint,
            patients=len(vectors), branch_metrics={'G1':oldfinal.branch_metrics(physical, branch)},
            patient_vectors={'G1':vectors}, counters=dict(counters),
            recorded_events_and_current_decisions_verified=True, guard_math_independently_reconstructed=True,
            independent_raw_metrics=True, H1_algorithm_reexecuted=False,
            original_H1_proposal_record_checked_separately=True, original_H1_proposal_treated_as_actual=False,
            full_resource_history_replayed=False, whole_online_optimality_verified=False,
            clinical_effect_verified=False, operational_adoption=False)
    except (KeyError, TypeError, AttributeError, IndexError, ZeroDivisionError) as error:
        raise ArrivalGuardAuditError('malformed G1 replay: ' + str(error)) from error


@lru_cache(maxsize=1)
def _known_references():
    body = KNOWN_REPORT_PATH.read_bytes()
    require(hashlib.sha256(body).hexdigest() == KNOWN_REPORT_SHA256, 'known control report SHA differs')
    report = json.loads(body)
    require(report['experiment_id'] == oldfinal.EXPERIMENT_ID and report['phase'] == 'validation'
        and report['technical_passed'] is True and report['all_three_arms_fresh'] is True,
        'known control provenance differs')
    cases = report['cases']
    require(len(cases) == 345 and len({c['case_id'] for c in cases}) == 345, 'known control case partition differs')
    for case in cases:
        value = case['audit']
        require(value['passed'] is True and case['scenario_sha256'] == value['scenario_sha256'],
            'known control input/audit identity differs')
        require(set(value['branch_metrics']) == set(value['patient_vectors']) == {'R0', 'R1', 'H1'},
            'known control arm partition differs')
    return {case['case_id']:case for case in cases}


def load_known_references():
    """Return isolated prior case copies, bound to the fixed report byte hash."""
    return deepcopy(_known_references())


def audit_bundle(bundle, *, source_scenario=None, scenario_sha256=None, known_case_id=None,
                 expected_control_order=None):
    """Return full4; original H1 remains H1 and actual guarded candidate is G1."""
    try:
        require(bundle['schema_version'] == 1 and bundle['comparison_id'] == EXPERIMENT_ID, 'wrong G1 bundle identity')
        candidate = audit_candidate(bundle['candidate'], source_scenario=source_scenario, scenario_sha256=scenario_sha256)
        fingerprint = candidate['scenario_sha256']
        require(bundle['input_sha256'] == fingerprint, 'bundle input hash differs')
        if bundle['controls'] is None:
            require(known_case_id is not None and expected_control_order is None, 'cached controls need known case ID and no fresh order')
            refs = _known_references()
            require(known_case_id in refs, 'unknown known control case ID')
            ref = refs[known_case_id]
            require(ref['scenario_sha256'] == fingerprint, 'known control source hash differs')
            controls = deepcopy(ref['audit'])
            provenance = dict(mode='cached_known', known_case_id=known_case_id,
                report_sha256=KNOWN_REPORT_SHA256, raw_sha256=ref['raw_sha256'],
                raw_compressed_sha256=ref['compressed_sha256'], fresh_controls_executed=False,
                known_controls_raw_reaudited_this_phase=False)
        else:
            require(known_case_id is None, 'fresh control bundle cannot claim cached known case ID')
            require(bundle['candidate']['routing_input_sha256'] == bundle['controls']['routing_input_sha256'],
                'candidate and controls routing inputs differ')
            controls = oldfinal.audit_case(bundle['controls'], expected_order=expected_control_order,
                source_scenario=bundle['candidate']['original_scenario'], scenario_sha256=fingerprint)
            provenance = dict(mode='fresh', fresh_controls_executed=True,
                control_events_and_current_decisions_reaudited=True)
        require(controls['scenario_sha256'] == fingerprint and controls['patients'] == candidate['patients'],
            'controls/candidate source partition differs')
        metrics = {code:deepcopy(controls['branch_metrics'][code]) for code in POLICY_IDS[:-1]}
        metrics.update(candidate['branch_metrics'])
        vectors = {code:deepcopy(controls['patient_vectors'][code]) for code in POLICY_IDS[:-1]}
        vectors.update(candidate['patient_vectors'])
        require(list(metrics) == list(vectors) == list(POLICY_IDS), 'full4 canonical arm order differs')
        paired = {code:oldfinal.paired_summary(vectors[code], vectors['G1']) for code in POLICY_IDS[:-1]}
        counters = Counter(candidate['counters'])
        if provenance['mode'] == 'fresh':
            counters.update(controls['counters'])
        return dict(schema_version=1, passed=True, experiment_id=EXPERIMENT_ID,
            scenario_sha256=fingerprint, patients=candidate['patients'], branch_metrics=metrics,
            patient_vectors=vectors, paired=paired, controls_source=provenance,
            candidate_audit=candidate, counters=dict(counters),
            current_phase_raw_branches_audited=4 if provenance['mode'] == 'fresh' else 1,
            complete_old_H1_reference_preserved=True, synthetic_best_of_reference=False,
            independent_raw_metrics=True, guard_math_independently_reconstructed=True,
            H1_algorithm_reexecuted=False, full_resource_history_replayed=False,
            whole_online_optimality_verified=False, clinical_effect_verified=False, operational_adoption=False)
    except (KeyError, TypeError, AttributeError, IndexError, ZeroDivisionError) as error:
        raise ArrivalGuardAuditError('malformed G1 bundle: ' + str(error)) from error
