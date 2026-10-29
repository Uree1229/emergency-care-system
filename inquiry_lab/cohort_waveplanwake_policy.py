"""개발 자료를 본 뒤 설계한 공개 대기열 기반 조기 문의 복원 후보.

첫 공동 문의·원래 예정 판단과 H의 계산은 보존한다. 조기 판단의 추가
문의에 기존 거리 보호를 적용하되 현재 공개 타환자 문의가 없는 곳만 예외로 허용한다.
실제 도착시간·성공 비퇴행을 보장하는 규칙은 아니며 운영에 연결하지 않는다.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from . import cohort_tailwait_policy as original,cohort_waveplanwake_context as context
from . import cohort_travelwake_policy as travel_parent
from . import cohort_travelrecall_policy as recall_parent

NAME='eat_cohort_waveplanwake_l15_f'
BASE_POLICY=original.NAME
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:raise ValueError('frozen decline-wake Tailwait foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
RULE='public_decline_extra_review_with_travel_guard_and_empty_foreign_target_queue_exception'
SCOPE='development-tuned after observed known training outcomes and public target queues; unchanged Tailwait first groups, regular fallback and H mathematics; existing distance protection and unknown-travel block remain, with an exception for a known farther target only when captured public foreign unresolved queue is empty; no actual success/wait/arrival nonregression guarantee'
DECLINEWAKE_POLICY_SOURCE_SHA256='c1afca06d518f6e1ad3b1d8cbcea766ee83be441898f60112f5f2c5281a4b4b0'
if hashlib.sha256(Path(__file__).with_name('cohort_declinewake_policy.py').read_bytes()).hexdigest()!=DECLINEWAKE_POLICY_SOURCE_SHA256:
    raise ValueError('frozen Declinewake policy source differs')
TRAVELWAKE_POLICY_SOURCE_SHA256='3be76a9b9dc8f74a4ffbfe567e65717cdcaae49b05403744f9f0c1bff6b9f85e'
if hashlib.sha256(Path(travel_parent.__file__).read_bytes()).hexdigest()!=TRAVELWAKE_POLICY_SOURCE_SHA256:
    raise ValueError('frozen Travelwake policy source differs')
TRAVEL_RULE='retain_public_travel_guard_except_known_farther_target_with_zero_public_foreign_unresolved_inquiries'
QUEUE_RULE='all_captured_unresolved_foreign_patient_inquiries_at_target_including_age_over180; unknown_or_future_queue_rows_disable_exception'
RECALL_POLICY_SOURCE_SHA256='3ab826fafd7bfafbaf8d961b21fac87c3df429840983c336692b86f6c9bd313f'
if hashlib.sha256(Path(recall_parent.__file__).read_bytes()).hexdigest()!=RECALL_POLICY_SOURCE_SHA256:
    raise ValueError('frozen Recall policy source differs')
PREFETCH_RULE='own_decline_wake_with_empty_original_H_send_may_advance_only_first_positive_finite_native_future_plan_when_earliest_wave_has_two_distinct_valid_hospitals_if_known_travel_empty_foreign_target_queue_and_Recall_guard_allow'
PLAN_SCOPE='development-tuned on already observed training; own-decline Recall behavior plus at most one earliest native positive-offset H plan advanced on an otherwise empty original H send only if the earliest wave has at least two distinct valid hospital identifiers; first blocked planned candidate stops; original H plan/cost are not rescored; no actual success/wait/arrival nonregression guarantee'


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0


def _same(a,b):
    return type(a) is type(b) and a==b


def travel_guard(*,now,patient_id,candidates,pending,patient_pending,proposed_sends):
    """원래 거리 필터의 허용은 유지하고 공개 빈 타환자 대기열만 예외로 쓴다."""
    base=travel_parent.travel_guard(now=now,patient_id=patient_id,candidates=candidates,
        pending=pending,patient_pending=patient_pending,proposed_sends=proposed_sends)
    value=deepcopy(base);value['rule']=TRAVEL_RULE
    value.update(original_travelwake_guard=base,travelwake_policy_source_sha256=TRAVELWAKE_POLICY_SOURCE_SHA256,
        queue_exception_rule=QUEUE_RULE,queue_exception_applied_hospital_ids=[])
    queue_known=True
    for row in pending:
        if (type(row) is not dict or type(row.get('hospital')) is not str or not row['hospital']
                or type(row.get('patient')) not in (str,int) or (type(row['patient']) is str and not row['patient'])
                or not _finite(row.get('submitted_at')) or row['submitted_at']>now):
            queue_known=False
    allowed=[];rejected=[]
    for row in value['rows']:
        old_allowed=row['allowed'];old_reason=row['reason'];hospital=row['hospital_id']
        foreign=[deepcopy(q) for q in pending if type(q) is dict and _same(q.get('hospital'),hospital)
            and not _same(q.get('patient'),patient_id)]
        exception=(not old_allowed and old_reason=='farther_than_minimum_fresh_own_pending'
            and row['travel_known'] is True and not base['unknown_fresh_pending_travel']
            and queue_known and not foreign)
        row.update(travelwake_allowed=old_allowed,travelwake_reason=old_reason,
            foreign_pending_count=len(foreign),foreign_pending_sources=foreign,
            public_queue_known=queue_known,queue_exception_applied=exception)
        if exception:
            row['allowed']=True;row['reason']='known_farther_target_has_no_public_foreign_unresolved_inquiries'
            value['queue_exception_applied_hospital_ids'].append(hospital)
        (allowed if row['allowed'] else rejected).append(hospital)
    value.update(allowed_sends=allowed,rejected_hospital_ids=rejected,
        public_queue_known=queue_known,foreign_queue_age_limit_seconds=None,
        input_sources='current captured public pending/patient_pending and remaining candidate travel only; all unresolved foreign ages count')
    return value


def prefetch_plan(*,now,patient_id,wake_context,parent_proposal,candidates,pending,patient_pending,max_new):
    """원 H의 첫 미래계획 한 곳만 공개 현재 정보로 검사한다. 실패 후 우회하지 않는다."""
    own_wake=type(wake_context) is dict and wake_context.get('rule')==context.WAKE_RULE
    parent_send=deepcopy(parent_proposal['send'])
    plan=deepcopy(parent_proposal['evidence'].get('candidate_plan',[]))
    own_fresh=[];pending_known=type(pending) is list
    for row in pending:
        valid=(type(row) is dict and type(row.get('hospital')) is str and bool(row['hospital'])
            and type(row.get('patient')) in (str,int) and (type(row['patient']) is int or bool(row['patient']))
            and _finite(row.get('submitted_at')) and row['submitted_at']<=now)
        if not valid:pending_known=False;continue
        if _same(row['patient'],patient_id) and 0<=now-row['submitted_at']<=180:own_fresh.append(deepcopy(row))
    result=dict(schema_version=1,rule=PREFETCH_RULE,now=now,patient_id=patient_id,own_wake=own_wake,
        parent_H_send=parent_send,original_candidate_plan=plan,current_max_new=max_new,
        public_fresh_own_pending=own_fresh,fresh_own_count=len(own_fresh),public_pending_known=pending_known,
        selected_plan_index=None,selected_plan_row=None,current_candidate_sources=[],
        earliest_wave_offset_seconds=None,earliest_wave_plan_indices=[],earliest_wave_distinct_hospital_ids=[],
        earliest_wave_rows=[],earliest_wave_has_distinct_peer=False,
        target_foreign_pending_sources=[],target_foreign_pending_count=None,recall_travel_guard=None,
        applied=False,added_hospital_ids=[],reason='not_own_decline_wake',
        model_or_cost_recomputed=False,search_after_first_blocked=False,
        actual_arrival_nonregression_guaranteed=False,development_tuned=True)
    if not own_wake:return result
    if type(parent_send) is not list or parent_send:
        result['reason']='original_H_has_current_send';return result
    if type(max_new) is not int or not 1<=max_new<=10:
        result['reason']='no_supported_remaining_contact_budget';return result
    if not pending_known:
        result['reason']='unknown_public_pending_queue';return result
    if len(own_fresh)>=4:
        result['reason']='fresh_own_concurrency_full';return result
    future=[(row.get('planned_offset_seconds'),i,row) for i,row in enumerate(plan) if type(row) is dict
        and _finite(row.get('planned_offset_seconds')) and row['planned_offset_seconds']>0] if type(plan) is list else []
    if not future:
        result['reason']='no_positive_finite_future_plan';return result
    offset,index,row=min(future,key=lambda item:(item[0],item[1]))
    wave=[item for item in future if item[0]==offset]
    hospitals=[]
    for _,_,peer in wave:
        identifier=peer.get('hospital')
        if type(identifier) is str and identifier and identifier not in hospitals:hospitals.append(identifier)
    result.update(selected_plan_index=index,selected_plan_row=deepcopy(row),
        earliest_wave_offset_seconds=offset,earliest_wave_plan_indices=[item[1] for item in wave],
        earliest_wave_rows=[deepcopy(item[2]) for item in wave],
        earliest_wave_distinct_hospital_ids=hospitals,earliest_wave_has_distinct_peer=len(hospitals)>=2)
    if not result['earliest_wave_has_distinct_peer']:
        result['reason']='future_wave_has_no_distinct_peer';return result
    hospital=row.get('hospital')
    if type(hospital) is not str or not hospital:
        result['reason']='unknown_first_planned_hospital';return result
    matches=[deepcopy(c) for c in candidates if type(c) is dict and _same(c.get('hospital_id'),hospital)]
    result['current_candidate_sources']=matches
    if len(matches)!=1:
        result['reason']='missing_or_ambiguous_current_candidate';return result
    travel=matches[0].get('travel')
    if type(travel) is not dict or not _finite(travel.get('duration_seconds')):
        result['reason']='unknown_new_travel';return result
    foreign=[deepcopy(q) for q in pending if _same(q['hospital'],hospital) and not _same(q['patient'],patient_id)]
    result.update(target_foreign_pending_sources=foreign,target_foreign_pending_count=len(foreign))
    if foreign:
        result['reason']='target_has_foreign_unresolved_inquiries';return result
    guard=recall_parent.travel_guard(now=now,patient_id=patient_id,candidates=candidates,
        pending=pending,patient_pending=patient_pending,proposed_sends=[hospital])
    result['recall_travel_guard']=guard
    if guard['allowed_sends']!=[hospital]:
        result['reason']='Recall_travel_guard_rejected';return result
    result.update(applied=True,added_hospital_ids=[hospital],reason='advanced_one_original_future_plan')
    return result


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed decline-wake candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, one guarded same-wave future-plan prefetch')
    return value


def decide(name,*,now,candidates,observations=(),pending=(),patient_pending=(),max_new,last_contact_at=None,time_context,wake_context=None):
    spec(name)
    parent=original.decide(BASE_POLICY,now=now,candidates=candidates,observations=observations,pending=pending,
        patient_pending=patient_pending,max_new=max_new,last_contact_at=last_contact_at,time_context=time_context)
    wake=None if wake_context is None else context.validate_wake_context(wake_context,now=now,patient_id=time_context['patient_id'])
    value=deepcopy(parent)
    if wake is not None:
        if wake['capture_snapshot']['job_version']!=time_context['job_version'] or wake['event_prefix_max']!=time_context['event_prefix_max'] or wake['decision_prefix_max']!=time_context['decision_prefix_max']:
            raise ValueError('wake and original H public application prefixes differ')
        value['next_review_at']=min(wake['captured_job']['next_evaluation_at'],parent['next_review_at'])
    value['evidence']['policy']=NAME
    value['evidence']['cohort_waveplanwake']=dict(schema_version=1,mode='horizon_fallback',rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,
        triggered=wake is not None,wake_context=wake,original_tailwait_proposal=parent,
        parent_next_review_at=parent['next_review_at'],actual_next_review_at=value['next_review_at'],
        sent_hospitals_unchanged=value['send']==parent['send'],model_or_objective_changed=False,
        next_review_clipped=value['next_review_at']!=parent['next_review_at'],scope=SCOPE)
    tag=value['evidence']['cohort_waveplanwake']
    # This is the exact identity-projected pre-guard Declinewake behavior, not
    # a call through or mutation of that frozen module's namespace.
    before=deepcopy(value)
    guard=None
    if wake is not None:
        guard=travel_guard(now=now,patient_id=time_context['patient_id'],candidates=candidates,
            pending=pending,patient_pending=patient_pending,proposed_sends=value['send'])
        value['send']=list(guard['allowed_sends'])
        value['evidence']['sent_hospitals']=list(value['send'])
    tag.update(travel_guard_evaluated=wake is not None,travel_guard=guard,
        original_declinewake_proposal=before,declinewake_source_sha256=DECLINEWAKE_POLICY_SOURCE_SHA256,
        declinewake_proposal_identity_projected=True,
        actual_sent_hospitals=list(value['send']),sent_hospitals_unchanged=value['send']==parent['send'],
        H_candidate_plan_and_cost_scope='unchanged parent H pre-guard plan and expected cost; filtered send is not rescored',
        policy_math_reexecuted_for_filtered_send=False,development_tuned_after_observed_training_case=True)
    distance=deepcopy(value)
    if guard is not None:
        old_guard=deepcopy(guard['original_travelwake_guard'])
        distance['send']=list(old_guard['allowed_sends']);distance['evidence']['sent_hospitals']=list(distance['send'])
        old_tag=distance['evidence']['cohort_waveplanwake']
        old_tag.update(travel_guard=old_guard,actual_sent_hospitals=list(distance['send']),
            sent_hospitals_unchanged=distance['send']==parent['send'])
    tag.update(original_travelwake_proposal=distance,travelwake_source_sha256=TRAVELWAKE_POLICY_SOURCE_SHA256,
        travelwake_proposal_identity_projected=True,travelwake_proposal_recreated_locally=True,
        queue_exception_evaluated=wake is not None,
        queue_exception_applied_hospital_ids=[] if guard is None else list(guard['queue_exception_applied_hospital_ids']))
    before_prefetch=deepcopy(value)
    prefetch=prefetch_plan(now=now,patient_id=time_context['patient_id'],wake_context=wake,parent_proposal=parent,
        candidates=candidates,pending=pending,patient_pending=patient_pending,max_new=max_new)
    if prefetch['applied']:
        if value['send']:raise ValueError('prefetch requires the complete original Recall send to be empty')
        value['send']=list(prefetch['added_hospital_ids'])
        value['reason']='waveplanwake_prefetch_one'
        value['evidence']['sent_hospitals']=list(value['send'])
    if wake is not None:value['evidence']['prefetch']=deepcopy(prefetch)
    tag.update(original_travelrecall_proposal=before_prefetch,travelrecall_source_sha256=RECALL_POLICY_SOURCE_SHA256,
        travelrecall_proposal_identity_projected=True,travelrecall_proposal_recreated_locally=True,
        prefetch=deepcopy(prefetch),prefetch_evaluated=wake is not None,prefetch_applied=prefetch['applied'],
        actual_prefetched_hospital_ids=list(prefetch['added_hospital_ids']),
        actual_sent_hospitals=list(value['send']),sent_hospitals_unchanged=value['send']==parent['send'],
        rule=PREFETCH_RULE,scope=PLAN_SCOPE,
        travel_guard_scope='original Recall stage only; additional dispatch is explained by evidence.prefetch')
    return value


def decide_joint(name,*,members,joint_context,tail_members,priority_context,owner_context):
    spec(name);native=context.validate_context(joint_context)
    context.validate_tail_members(native,tail_members,members=members)
    priority=context.validate_priority_context(priority_context,joint_context=native,members=members,tail_members=tail_members)
    owners=context.validate_owner_context(owner_context,joint_context=native,members=members,tail_members=tail_members,priority_context=priority)
    parent=original.decide_joint(BASE_POLICY,members=context.base_members_projection(members),joint_context=context.base_context_projection(native),
        tail_members=context.base_members_projection(tail_members),priority_context=priority,owner_context=context.base_owner_projection(owners))
    decisions=deepcopy(parent['decisions'])
    for index,value in enumerate(decisions):
        value['evidence']['policy']=NAME
        value['evidence']['cohort_waveplanwake']=dict(schema_version=1,mode='pair_copied_tailwait',triggered=False,member_index=index,model_or_objective_changed=False)
    return dict(decisions=decisions,evidence=dict(schema_version=1,rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,
        joint_context=native,priority_context=priority,owner_context=owners,original_tailwait_proposal=parent,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,copied_only=True,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),
        triggered=False,copied_tailwait_group=True,scope=SCOPE))
