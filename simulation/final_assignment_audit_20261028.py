"""Independent recorded-event and current-decision audit for R0/R1/H1.

Imports only frozen standard-library auditors, never a simulator, solver,
scenario generator, experiment driver or allocation implementation. This checks
recorded current ledgers and event endpoints; it does not replay every mutable
resource transition, execute H1 mathematics, or prove an online optimum.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import statistics

from . import online_allocation_audit as base
from . import h1_online_audit as inherited

EXPERIMENT_ID = 'final_assignment_20261028'
POLICY_IDS = ('R0', 'R1', 'H1')
H1_SPEC = dict(rule='H1', time_limit_seconds=10.0, max_moves=2048)
ASSUMPTION_SUFFIX = [
    '세 규칙은 같은 고정3 문의·첫 수용 뒤 최대2분 추가대기·외부 잠재 사건·현재 관측 조건을 사용한다.',
    'R0/R1은 환자별 순차 확정, H1은 같은 분에 준비된 환자만 공동 확정한다.',
    '배정 이후 자원과 실제 회신은 결과로 달라질 수 있다. 사후 수용 목록을 동일하게 고정하지 않는다.',
    'H1은 기존 알고리즘에 2048회 탐색을 주고 10초는 실패 감지용 안전상한만 사용한다. 과거50ms버전과 구분한다.',
    '합성 입력의 결과이며 실제 병원 성능·임상 효과·전체 온라인 최적성의 검증이 아니다.',
]
EXECUTION_CONTRACT = dict(
    inquiry_policy='fixed_three_with_first_acceptance_extra_wait_two_minutes',
    joint_scope='currently_ready_same_minute_received_accepted_offers_only',
    H1_primary_budget='at_most_2048_deterministic_candidate_plan_evaluations',
    H1_safety_seconds=10.0, H1_safety_exhaustion_invalidates_run=True,
    H1_previous_selected_50ms_version_reused_exactly=False,
    H1_algorithm_source_unchanged=True, whole_episode_optimal_proven=False,
    simulation_clock='integer_minute', wall_time_added_to_simulation_clock=False,
    information_observation='complete_current_resources_and_received_offers; no future schedules passed to allocation rule')
FinalAssignmentAuditError = base.OnlineAllocationAuditError


def require(value, message):
    base._require(value, message)


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def percentile(values, fraction=.95):
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def _same(actual, expected, label):
    require(base._same(actual, expected), label)


def _ordered(values):
    return sorted(values, key=lambda value: (type(value).__name__, str(value)))


def _vectors(scenario, branch):
    rows, _, _ = base._event_rows(scenario, branch)
    end = base._end(scenario, branch)
    specs = {p['id']: p for p in scenario['patients']}
    result = []
    for pid in _ordered(rows):
        row, spec = rows[pid], specs[pid]
        occur, search = spec.get('occur_t', 0), spec['search_start_t']
        selected, arrived = row['first_commit_t'] is not None, row['final_arrive_t'] is not None
        result.append(dict(id=pid, priority=spec.get('priority', 4), occur_t=occur,
            search_start_t=search, observation_end_t=end,
            first_commit_t=row['first_commit_t'], final_arrive_t=row['final_arrive_t'],
            treatment_start_t=row['treatment_start_t'], selected=selected, arrived=arrived,
            inquiries=row['inquiries'],
            selection_elapsed_minutes=None if not selected else row['first_commit_t'] - search,
            arrival_elapsed_minutes=None if not arrived else row['final_arrive_t'] - occur,
            selection_capped_minutes=row['first_commit_t'] - search if selected else end - search,
            arrival_capped_minutes=row['final_arrive_t'] - occur if arrived else end - occur))
    return result


def branch_metrics(scenario, branch):
    value = base.raw_branch_metrics(scenario, branch)
    vectors = _vectors(scenario, branch)
    priority_sum = sum(p['priority'] for p in vectors)
    unassigned_weight = sum(p['priority'] for p in vectors if not p['selected'])
    observed = [p['arrival_elapsed_minutes'] for p in vectors if p['arrived']]
    capped = [p['arrival_capped_minutes'] for p in vectors]
    walls = [d['elapsed_seconds'] * 1000 for d in branch['allocation_decisions']]
    value.update(unassigned_priority_weight=unassigned_weight,
        priority_weight_total=priority_sum,
        unassigned_priority_fraction=unassigned_weight / priority_sum,
        arrival_p95_minutes=percentile(observed),
        arrival_capped_p95_minutes=percentile(capped),
        arrival_observed_denominator=len(observed),
        allocation_wall_p95_ms=percentile(walls), allocation_wall_max_ms=max(walls, default=None),
        allocation_decision_count=len(walls))
    return value


def paired_summary(before, after):
    """All patients stay in capped means; conditional arrivals retain IDs."""
    left, right = {r['id']: r for r in before}, {r['id']: r for r in after}
    require(len(left) == len(before) == len(after) == len(right) and set(left) == set(right),
        'paired typed patient partition differs')
    ids = _ordered(left)
    for pid in ids:
        for key in ('priority', 'occur_t', 'search_start_t', 'observation_end_t'):
            require(type(left[pid][key]) is type(right[pid][key]) and left[pid][key] == right[pid][key],
                'paired source patient fields differ: ' + key)
    common = [pid for pid in ids if left[pid]['arrived'] and right[pid]['arrived']]
    changes = [dict(patient_id=pid,
        before_arrival_minutes=left[pid]['arrival_elapsed_minutes'],
        after_arrival_minutes=right[pid]['arrival_elapsed_minutes'],
        delta_minutes=right[pid]['arrival_elapsed_minutes']-left[pid]['arrival_elapsed_minutes'])
        for pid in common]
    deltas = [r['delta_minutes'] for r in changes]
    weight = sum(left[pid]['priority'] for pid in ids)
    weighted_before = sum(p['priority'] for p in before if not p['selected'])
    weighted_after = sum(p['priority'] for p in after if not p['selected'])
    return dict(patients=len(ids), common_arrival_denominator=len(common),
        selected_lost_ids=[pid for pid in ids if left[pid]['selected'] and not right[pid]['selected']],
        selected_gained_ids=[pid for pid in ids if not left[pid]['selected'] and right[pid]['selected']],
        arrived_lost_ids=[pid for pid in ids if left[pid]['arrived'] and not right[pid]['arrived']],
        arrived_gained_ids=[pid for pid in ids if not left[pid]['arrived'] and right[pid]['arrived']],
        common_arrival_mean_delta_minutes=statistics.mean(deltas) if deltas else None,
        common_arrival_p95_delta_minutes=percentile(deltas),
        maximum_common_arrival_delay_minutes=max(0, max(deltas)) if deltas else None,
        common_arrival_delayed_count=sum(v > 0 for v in deltas),
        common_arrival_earlier_count=sum(v < 0 for v in deltas),
        common_arrival_equal_count=sum(v == 0 for v in deltas),
        common_arrival_patient_changes=changes,
        unassigned_count_delta=sum(not p['selected'] for p in after)-sum(not p['selected'] for p in before),
        unassigned_priority_weight_delta=weighted_after-weighted_before,
        unassigned_priority_fraction_delta=(weighted_after-weighted_before)/weight,
        arrival_capped_sum_delta_minutes=sum(p['arrival_capped_minutes'] for p in after)-sum(p['arrival_capped_minutes'] for p in before),
        arrival_capped_mean_delta_minutes=statistics.mean(p['arrival_capped_minutes'] for p in after)-statistics.mean(p['arrival_capped_minutes'] for p in before),
        selection_capped_mean_delta_minutes=statistics.mean(p['selection_capped_minutes'] for p in after)-statistics.mean(p['selection_capped_minutes'] for p in before),
        inquiry_count_delta=sum(p['inquiries'] for p in after)-sum(p['inquiries'] for p in before))


def _policy(branch):
    code, policy = branch['id'], branch['policy']
    require(code in POLICY_IDS and policy['rule'] == code and policy['name'] == code,
        'policy identity differs')
    for key, expected in dict(k=3, wait=2, notify=True).items():
        require(type(policy.get(key)) is type(expected) and policy[key] == expected,
            'fixed inquiry or notification setting changed: ' + key)
    optional = dict(near_minutes=None, expand_after=None, inquiry_ranking='travel', load_weight=0,
        load_window=6, adaptive_inquiry=None, observed_inquiry=None)
    for key, expected in optional.items():
        require(policy.get(key, expected) == expected, 'nonshared inquiry configuration: ' + key)
    require(set(policy) <= {'name','rule','k','wait','notify','heuristic'} | set(optional),
        'unrecognized policy setting')
    _same(branch.get('execution_contract'), EXECUTION_CONTRACT, 'execution contract differs')
    require(not branch.get('solver_records'), 'solver records are outside three-arm comparison')


def _simple_choice(scenario, branch):
    code = branch['id']
    count = 0
    for decision in branch['allocation_decisions']:
        require(decision.get('rule') == code, 'decision identity differs from branch')
        if decision['kind'] == 'batch':
            require(code == 'H1', 'simple rule used a joint batch')
            continue
        patients, _, options, travel = base._problem(scenario, decision)
        require(len(patients) == 1, 'single choice has multiple patients')
        patient = patients[0]; pid = patient['id']; eligible = options[pid]
        if code == 'R0':
            expected = min(eligible, key=lambda hid:(patient['accepted_at'][hid],travel[pid,hid])) if eligible else None
            require(decision['method'] == 'first_accept', 'R0 method differs')
        else:
            expected = min(eligible, key=lambda hid:(travel[pid,hid],hid)) if eligible else None
            require(decision['method'] == 'nearest_current_offer', 'nearest single method differs')
        require(decision['assignments'] == {pid:expected}, 'simple current-offer choice mismatch')
        count += 1
    return count


def audit_case(replay, *, expected_order=None, scenario_sha256=None, source_scenario=None):
    """Verify an entire saved replay, independently rebuilding every endpoint."""
    try:
        require(replay['schema_version'] == 2 and replay['source_experiment'] == 'online_allocation_comparison'
            and replay['comparison_id'] == EXPERIMENT_ID, 'wrong final replay identity')
        source = replay['original_scenario'] if source_scenario is None else source_scenario
        require(replay['original_scenario'] == source, 'original source scenario changed')
        original_hash = canonical_sha256(source)
        require(replay['input_scenario_sha256'] == replay['original_scenario_sha256'] == original_hash,
            'original scenario hash differs')
        if scenario_sha256 is not None:
            require(original_hash == scenario_sha256, 'case differs from input manifest hash')
        scenario = replay['scenario']
        augmented = deepcopy(source)
        augmented.update(online_allocation_comparison=True, source_experiment='online_allocation_comparison', comparison_id=EXPERIMENT_ID)
        augmented.setdefault('assumptions', []).extend(ASSUMPTION_SUFFIX)
        _same(scenario, augmented, 'scenario augmentation changed physical inputs')
        require([b['id'] for b in replay['branches']] == list(POLICY_IDS), 'canonical branch order differs')
        require(len(replay['execution_order']) == 3 and set(replay['execution_order']) == set(POLICY_IDS),
            'each arm must execute once')
        if expected_order is not None:
            require(replay['execution_order'] == list(expected_order), 'execution rotation differs')
        routing = replay['routing_summary']
        _same({k:routing.get(k) for k in ('provider','scope','live_traffic','ambulance_priority','shared_mutable_routing_cache','external_route_provider_allowed')},
            dict(provider='synthetic',scope='independent_per_branch',live_traffic=False,ambulance_priority=False,
                shared_mutable_routing_cache=False,external_route_provider_allowed=False), 'routing contract differs')
        require(set(routing['by_branch']) == set(POLICY_IDS), 'routing branch partition differs')
        route_hash = replay['routing_input_sha256']
        require(type(route_hash) is str and len(route_hash) == 64 and all(c in '0123456789abcdef' for c in route_hash),
            'routing fingerprint malformed')
        metrics, vectors, totals = {}, {}, Counter()
        for branch in replay['branches']:
            code = branch['id']; _policy(branch)
            require(branch['source_experiment'] == 'online_allocation_comparison'
                and branch['comparison_id'] == EXPERIMENT_ID, 'branch comparison identity differs')
            require(branch['original_scenario_sha256'] == original_hash and branch['routing_input_sha256'] == route_hash,
                'arm did not start from the same input environment')
            route = routing['by_branch'][code]
            require(route['provider'] == 'synthetic' and route['scope'] == 'scenario'
                and route['live_traffic'] is False and route['ambulance_priority'] is False
                and route['road_routes'] == 0 and route['fallback_routes'] == 0
                and route['synthetic_routes'] == route['routes_evaluated'], 'non-synthetic route observation')
            audited = base.audit_branch(scenario, branch, exact_limit=0)
            totals.update({k:v for k,v in audited['counts'].items() if type(v) is int})
            totals.update(inherited.audit_h1_branch(scenario, branch, dict(H1_SPEC, id=code)))
            inherited._runtime_metrics(branch)
            totals['simple_rule_choices_checked'] += _simple_choice(scenario, branch)
            for record in branch.get('heuristic_records', []):
                require(record.get('search_stop_reason') in ('search_complete','move_limit','no_eligible_offers'),
                    'H1 deadline or unrecognized stop reason invalidates outcome')
                require(record['search_seconds'] < 10.0, 'H1 safety duration was reached')
            metrics[code] = branch_metrics(scenario, branch)
            vectors[code] = _vectors(scenario, branch)
            if 'online_summary' in branch:
                _same(branch['online_summary'], audited['metrics'], 'saved online summary differs from raw events')
        expected_duration = max((e['t'] for b in replay['branches'] for e in b['events']), default=0)
        require(replay['duration_minutes'] == expected_duration, 'duration differs from event trace')
        require(all([r['id'] for r in vectors[c]] == [r['id'] for r in vectors['R0']] for c in POLICY_IDS),
            'patient partition differs between arms')
        return dict(schema_version=1, passed=True, experiment_id=EXPERIMENT_ID,
            scenario_sha256=original_hash, patients=len(vectors['R0']),
            branch_metrics=metrics, patient_vectors=vectors,
            paired={code:paired_summary(vectors[code],vectors['H1']) for code in ('R0','R1')},
            counters=dict(totals), recorded_events_and_current_decisions_verified=True,
            independent_raw_metrics=True, H1_algorithm_reexecuted=False,
            full_resource_history_replayed=False, whole_online_optimality_verified=False,
            clinical_effect_verified=False, operational_adoption=False)
    except (KeyError, TypeError, AttributeError, IndexError, ZeroDivisionError) as error:
        raise FinalAssignmentAuditError('malformed final assignment replay: '+str(error)) from error


def audit_aggregate(report):
    """Check basic denominators/totals only; bootstrap inference is separate."""
    cases = report['cases']; require(type(cases) is list and cases, 'case audit list required')
    groups, seen = {}, set()
    for case in cases:
        key=(case['track'],case['stratum'],case['seed'])
        require(case['track'] in ('main','stress') and key not in seen, 'duplicate or unknown case track')
        seen.add(key); value=case['audit']
        require(value['passed'] is True and value['scenario_sha256'] == case['scenario_sha256'], 'case audit/input mismatch')
        require(set(value['branch_metrics']) == set(value['patient_vectors']) == set(POLICY_IDS), 'aggregate arm partition differs')
        group=groups.setdefault((case['track'],case['stratum']),dict(cases=0,patients=0,branch_totals={c:Counter() for c in POLICY_IDS}))
        group['cases']+=1;group['patients']+=value['patients']
        for code in POLICY_IDS:
            vectors=value['patient_vectors'][code];m=value['branch_metrics'][code]
            require(len(vectors)==value['patients']==m['patients'], 'aggregate patient denominator differs')
            totals=dict(patients=len(vectors),unassigned=sum(not v['selected'] for v in vectors),
                not_arrived=sum(not v['arrived'] for v in vectors),
                unassigned_priority_weight=sum(v['priority'] for v in vectors if not v['selected']),
                priority_weight_total=sum(v['priority'] for v in vectors),
                inquiries=sum(v['inquiries'] for v in vectors),
                arrival_capped_sum_minutes=sum(v['arrival_capped_minutes'] for v in vectors),
                selection_capped_sum_minutes=sum(v['selection_capped_minutes'] for v in vectors))
            for field in ('patients','unassigned','not_arrived','unassigned_priority_weight','priority_weight_total','inquiries'):
                require(totals[field]==m[field], 'aggregate stored metric/vector disagreement: '+field)
            group['branch_totals'][code].update(totals)
    rows=[dict(track=track,stratum=stratum,cases=value['cases'],patients=value['patients'],
        branch_totals={c:dict(value['branch_totals'][c]) for c in POLICY_IDS})
        for (track,stratum),value in sorted(groups.items())]
    return dict(passed=True,cases=len(cases),branches=len(cases)*3,groups=rows,
        strata_pooled_for_efficacy=False,bootstrap_confidence_intervals_independently_checked=False,
        full_resource_history_replayed=False,whole_online_optimality_verified=False)
