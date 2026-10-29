"""Choose the best existing pair-table row that preserves tail public options.

The complete frozen tail proposal is immutable evidence. Its original joint
table is not rewritten. Existing P/cost feasibility is intersected with the
same public zero-pending option constraint before choosing the global minimum.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from server import inquiry_policies
from . import joint_tail_policy as original, joint_reselect_context

NAME = 'eat_joint_reselect_l15_f'
BASE_POLICY = 'eat_joint_tail_l15_f'
RULE = 'global_pair_minimum_with_public_tail_option_constraints'
WAIT_EPSILON = 1e-9
BASE_SOURCE_SHA256 = '77a683c08dec240d5a18d333d37f8fc7fae53ca62b6e3f73877c50cdf1365e07'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen joint-reselection foundation differs')


def spec(name=NAME):
    if name != NAME:
        raise ValueError('the fixed joint-reselection candidate is ' + NAME)
    value = original.spec(BASE_POLICY)
    value.update(name=NAME,label='EAT l15 f, tail-safe selection within the original pair table')
    return value


def decide(name, *, now, candidates, observations=(), pending=(), patient_pending=(), max_new,
           last_contact_at=None, time_context):
    spec(name)
    result = original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,
        pending=pending,patient_pending=patient_pending,max_new=max_new,
        last_contact_at=last_contact_at,time_context=time_context)
    result['evidence']['policy'] = NAME
    result['evidence']['joint_reselect'] = dict(schema_version=1,mode='horizon_fallback',reselection_evaluated=False)
    return result


def _counts(pending, pair_send):
    counts = {}
    for row in pending:
        counts[row['hospital']] = counts.get(row['hospital'],0)+1
    for batch in pair_send:
        for hospital in batch:
            counts[hospital] = counts.get(hospital,0)+1
    return counts


def _checks(opportunities, baseline_counts, row_counts):
    checks = []
    for opportunity in opportunities:
        pool = opportunity['hospitals']
        baseline_zero = [h for h in pool if baseline_counts.get(h,0)==0]
        selected_zero = [h for h in pool if row_counts.get(h,0)==0]
        checks.append(dict(patient_id=opportunity['patient_id'],candidate_pool=list(pool),
            baseline_pending_counts={h:baseline_counts.get(h,0) for h in pool},
            selected_pending_counts={h:row_counts.get(h,0) for h in pool},
            baseline_zero_pending_hospitals=baseline_zero,selected_zero_pending_hospitals=selected_zero,
            triggered=bool(baseline_zero) and not selected_zero))
    return checks


def _actual_decision(joint_evidence, selected_index, index, applied, tail_index):
    decision = deepcopy(joint_evidence['baseline_proposals'][index])
    selected = joint_evidence['combinations'][selected_index]
    hospitals = selected['hospitals'][index]
    decision['send'] = list(hospitals)
    decision['reason'] = 'joint_reselect_current_only' if applied else 'joint_reselect_baseline_current_only'
    e = decision['evidence']
    e['policy'] = NAME
    e['sent_hospitals'] = list(hospitals)
    e['planned_expected_cost'] = round(selected['member_metrics'][index]['total_cost_seconds'],3)
    for row in e['candidate_plan']:
        row['planned_offset_seconds'] = 0 if row['hospital'] in hospitals else None
    e['joint_first'] = dict(schema_version=1,mode='pair_current_only',member_index=index,
        current_only=True,applied=applied,
        baseline_send=list(joint_evidence['baseline_combination']['hospitals'][index]),
        selected_send=list(hospitals),
        baseline_metrics=deepcopy(joint_evidence['baseline_combination']['member_metrics'][index]),
        selected_metrics=deepcopy(selected['member_metrics'][index]),
        constraint_reference='common-prefix simultaneous immediate-only H sends')
    e['joint_reselect'] = dict(schema_version=1,mode='pair_tail_safe_reselection',member_index=index,
        selected_index=selected_index,baseline_index=joint_evidence['baseline_index'],
        original_joint_selected_index=joint_evidence['selected_index'],
        original_tail_selected_index=tail_index,tail_safe=True,applied=applied)
    return decision


def decide_joint(name, *, members, joint_context, tail_members):
    spec(name)
    context = joint_reselect_context.validate_context(joint_context)
    opportunities = joint_reselect_context.validate_tail_members(context,tail_members,members=members)
    proposal = original.decide_joint(BASE_POLICY,
        members=joint_reselect_context.base_members_projection(members),
        joint_context=joint_reselect_context.base_context_projection(context),
        tail_members=joint_reselect_context.base_members_projection(tail_members))
    joint = proposal['evidence']['original_joint_proposal']['evidence']
    table = joint['combinations']
    pending = inquiry_policies._pending(members[0]['inputs']['pending'],context['now'])
    baseline_counts = _counts(pending,joint['baseline_combination']['hospitals'])
    rows = []
    for index,row in enumerate(table):
        checks = _checks(opportunities,baseline_counts,_counts(pending,row['hospitals']))
        blocked = [check['patient_id'] for check in checks if check['triggered']]
        tail_feasible = not blocked
        rows.append(dict(index=index,joint_feasible=row['feasible'],tail_feasible=tail_feasible,
            feasible=row['feasible'] and tail_feasible,blocked_tail_patients=blocked))
    feasible = [row['index'] for row in rows if row['feasible']]
    assert joint['baseline_index'] in feasible
    minimum = min(table[index]['sum_capped_wait_seconds'] for index in feasible)
    best = next(index for index in feasible if table[index]['sum_capped_wait_seconds']<=minimum+WAIT_EPSILON)
    baseline = joint['baseline_index']
    baseline_best_tied = table[baseline]['sum_capped_wait_seconds']<=minimum+WAIT_EPSILON
    selected = baseline if baseline_best_tied else best
    applied = table[baseline]['sum_capped_wait_seconds']-table[selected]['sum_capped_wait_seconds']>WAIT_EPSILON
    if not applied:
        selected = baseline
    selected_checks = _checks(opportunities,baseline_counts,_counts(pending,table[selected]['hospitals']))
    decisions = [_actual_decision(joint,selected,index,applied,proposal['evidence']['actual_selected_index'])
        for index in range(2)]
    evidence = dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'],copied_only=True,public_identity_unchanged=True),
        joint_context=context,original_tail_proposal=proposal,tail_opportunities=opportunities,
        row_feasibility=rows,minimum_feasible_wait_seconds=minimum,best_index=best,
        baseline_best_tied=baseline_best_tied,selected_index=selected,applied=applied,
        selected_tail_checks=selected_checks,wait_epsilon_seconds=WAIT_EPSILON,
        tie_rule='baseline when best-tied; otherwise first original-pool row within 1e-9 of global feasible minimum',
        feasibility_rule='unchanged original pair P/cost feasibility and no elimination of a tail last public zero-pending option',
        scope='all original rows; current public options only; no changes to counts, model, grouping, order or H fallback')
    return dict(decisions=decisions,evidence=evidence)
