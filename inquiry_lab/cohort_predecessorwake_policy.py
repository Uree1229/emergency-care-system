"""공개 strict 선행 문의의 종료 회신도 활용하는 별도 조기 문의 후보.

첫 공동 문의·원래 예정 판단과 H의 계산은 보존한다. 조기 판단의 추가
문의에 기존 거리 보호를 적용하되 현재 공개 타환자 문의가 없는 곳만 예외로 허용한다.
실제 도착시간·성공 비퇴행을 보장하는 규칙은 아니며 운영에 연결하지 않는다.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from . import cohort_tailwait_policy as original,cohort_predecessorwake_context as context
from . import cohort_travelwake_policy as travel_parent

NAME='eat_cohort_predecessorwake_l15_f'
BASE_POLICY=original.NAME
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:raise ValueError('frozen decline-wake Tailwait foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
RULE='own_decline_first_then_public_strict_predecessor_terminal_reply_extra_review_with_unchanged_queue_travel_guard'
SCOPE='development-tuned on already observed training; own terminal-decline wake has priority, otherwise a newly public terminal reply of a strictly earlier foreign inquiry at a current own-pending hospital may insert a review; unchanged Tailwait first groups/regular fallback/H mathematics and Recall distance/unknown-travel/empty-foreign-queue filter; no actual success/wait/arrival nonregression guarantee'
DECLINEWAKE_POLICY_SOURCE_SHA256='c1afca06d518f6e1ad3b1d8cbcea766ee83be441898f60112f5f2c5281a4b4b0'
if hashlib.sha256(Path(__file__).with_name('cohort_declinewake_policy.py').read_bytes()).hexdigest()!=DECLINEWAKE_POLICY_SOURCE_SHA256:
    raise ValueError('frozen Declinewake policy source differs')
TRAVELWAKE_POLICY_SOURCE_SHA256='3be76a9b9dc8f74a4ffbfe567e65717cdcaae49b05403744f9f0c1bff6b9f85e'
if hashlib.sha256(Path(travel_parent.__file__).read_bytes()).hexdigest()!=TRAVELWAKE_POLICY_SOURCE_SHA256:
    raise ValueError('frozen Travelwake policy source differs')
TRAVEL_RULE='retain_public_travel_guard_except_known_farther_target_with_zero_public_foreign_unresolved_inquiries'
QUEUE_RULE='all_captured_unresolved_foreign_patient_inquiries_at_target_including_age_over180; unknown_or_future_queue_rows_disable_exception'


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


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed decline-wake candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, public empty-queue travel wake recall')
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
    value['evidence']['cohort_predecessorwake']=dict(schema_version=1,mode='horizon_fallback',rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,
        triggered=wake is not None,wake_context=wake,original_tailwait_proposal=parent,
        parent_next_review_at=parent['next_review_at'],actual_next_review_at=value['next_review_at'],
        sent_hospitals_unchanged=value['send']==parent['send'],model_or_objective_changed=False,
        next_review_clipped=value['next_review_at']!=parent['next_review_at'],scope=SCOPE)
    tag=value['evidence']['cohort_predecessorwake']
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
        old_tag=distance['evidence']['cohort_predecessorwake']
        old_tag.update(travel_guard=old_guard,actual_sent_hospitals=list(distance['send']),
            sent_hospitals_unchanged=distance['send']==parent['send'])
    tag.update(original_travelwake_proposal=distance,travelwake_source_sha256=TRAVELWAKE_POLICY_SOURCE_SHA256,
        travelwake_proposal_identity_projected=True,travelwake_proposal_recreated_locally=True,
        queue_exception_evaluated=wake is not None,
        queue_exception_applied_hospital_ids=[] if guard is None else list(guard['queue_exception_applied_hospital_ids']))
    predecessor=wake is not None and wake['rule']==context.predecessor.RULE
    tag.update(wake_kind=None if wake is None else 'public_strict_predecessor_terminal_reply' if predecessor else 'own_terminal_decline',
        public_predecessor_context=deepcopy(wake) if predecessor else None,
        predecessor_helper_sha256=context.PREDECESSOR_HELPER_SHA256,
        predecessor_context_is_additional_public_evidence_only=True,
        original_timing_without_inserted_review_reexecuted=False)
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
        value['evidence']['cohort_predecessorwake']=dict(schema_version=1,mode='pair_copied_tailwait',triggered=False,member_index=index,model_or_objective_changed=False)
    return dict(decisions=decisions,evidence=dict(schema_version=1,rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,
        joint_context=native,priority_context=priority,owner_context=owners,original_tailwait_proposal=parent,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,copied_only=True,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),
        triggered=False,copied_tailwait_group=True,scope=SCOPE))
