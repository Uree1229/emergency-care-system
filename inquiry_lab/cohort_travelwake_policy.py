"""개발 자료를 본 뒤 설계한 공개 이동시간 보호 조기 재평가 후보.

첫 공동 문의·원래 예정 판단과 H의 계산은 보존한다. 조기 판단의 추가
문의만 공개된 자기 fresh pending보다 멀지 않은 병원으로 제한한다.
실제 도착시간·성공 비퇴행을 보장하는 규칙은 아니며 운영에 연결하지 않는다.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from . import cohort_tailwait_policy as original,cohort_travelwake_context as context

NAME='eat_cohort_travelwake_l15_f'
BASE_POLICY=original.NAME
BASE_SOURCE_SHA256='2fd6914a26c05651a79569395b30c8281881d5a72edbb11b08b85f33caa3a4a5'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:raise ValueError('frozen decline-wake Tailwait foundation differs')
HORIZON=original.HORIZON
JOINT=original.JOINT
RULE='public_decline_extra_review_with_no_farther_fresh_own_pending_travel_guard'
SCOPE='development-tuned after observed case240 P02; unchanged Tailwait first groups, regular fallback and H mathematics; early extra sends only may be suppressed using current public own fresh pending travel; existing review is never postponed; no actual success/wait/arrival nonregression guarantee'
DECLINEWAKE_POLICY_SOURCE_SHA256='c1afca06d518f6e1ad3b1d8cbcea766ee83be441898f60112f5f2c5281a4b4b0'
if hashlib.sha256(Path(__file__).with_name('cohort_declinewake_policy.py').read_bytes()).hexdigest()!=DECLINEWAKE_POLICY_SOURCE_SHA256:
    raise ValueError('frozen Declinewake policy source differs')
TRAVEL_RULE='suppress_wake_only_new_sends_farther_than_minimum_public_fresh_own_pending_travel_or_with_unknown_travel'


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0


def _same(a,b):
    return type(a) is type(b) and a==b


def travel_guard(*,now,patient_id,candidates,pending,patient_pending,proposed_sends):
    """읽기 전용 공개 입력 필터. 미래 회신·서비스 시간·시나리오는 받지 않는다."""
    rows=[];fresh=[];unknown=False
    for source in pending:
        if not _same(source.get('patient'),patient_id):continue
        submitted=source.get('submitted_at')
        age=now-submitted if _finite(submitted) and submitted<=now else None
        is_fresh=age is not None and 0<=age<=180
        matches=[q for q in patient_pending if _same(q.get('patient'),patient_id)
            and _same(q.get('hospital'),source.get('hospital')) and _same(q.get('submitted_at'),submitted)]
        travel=matches[0].get('travel_seconds') if len(matches)==1 else None
        known=_finite(travel)
        if age is None:unknown=True
        if is_fresh:
            if known:fresh.append(travel)
            else:unknown=True
        rows.append(dict(public_pending=deepcopy(source),age_seconds=age,fresh=is_fresh,
            matched_public_patient_pending=deepcopy(matches),travel_known=known,
            travel_seconds=travel if known else None))
    minimum=min(fresh) if fresh and not unknown else None
    choices=[];allowed=[];rejected=[]
    for hospital in proposed_sends:
        matches=[c for c in candidates if _same(c.get('hospital_id'),hospital)]
        travel=matches[0].get('travel',{}).get('duration_seconds') if len(matches)==1 and type(matches[0].get('travel')) is dict else None
        known=_finite(travel)
        if not known:reason='unknown_new_travel'
        elif unknown:reason='unknown_fresh_own_pending_travel_or_clock'
        elif minimum is None:reason='no_fresh_own_pending'
        elif travel<=minimum:reason='no_farther_than_minimum_fresh_own_pending'
        else:reason='farther_than_minimum_fresh_own_pending'
        accept=reason in ('no_fresh_own_pending','no_farther_than_minimum_fresh_own_pending')
        (allowed if accept else rejected).append(hospital)
        choices.append(dict(hospital_id=hospital,public_candidate_sources=deepcopy(matches),travel_known=known,
            travel_seconds=travel if known else None,minimum_fresh_pending_travel_seconds=minimum,allowed=accept,reason=reason))
    return dict(schema_version=1,rule=TRAVEL_RULE,now=now,patient_id=patient_id,fresh_pending_age_limit_inclusive_seconds=180,
        public_own_pending_sources=rows,minimum_fresh_pending_travel_seconds=minimum,unknown_fresh_pending_travel=unknown,
        proposed_sends=list(proposed_sends),rows=choices,allowed_sends=allowed,rejected_hospital_ids=rejected,
        input_sources='current captured public pending/patient_pending and remaining candidate travel only',
        future_replies_used=False,actual_arrival_nonregression_guaranteed=False,development_tuned=True)


def spec(name=NAME):
    if name!=NAME:raise ValueError('the fixed decline-wake candidate is '+NAME)
    value=original.spec(BASE_POLICY);value.update(name=NAME,label='EAT l15 f, public travel-protected terminal-decline wake')
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
    value['evidence']['cohort_travelwake']=dict(schema_version=1,mode='horizon_fallback',rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,
        triggered=wake is not None,wake_context=wake,original_tailwait_proposal=parent,
        parent_next_review_at=parent['next_review_at'],actual_next_review_at=value['next_review_at'],
        sent_hospitals_unchanged=value['send']==parent['send'],model_or_objective_changed=False,
        next_review_clipped=value['next_review_at']!=parent['next_review_at'],scope=SCOPE)
    tag=value['evidence']['cohort_travelwake']
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
        value['evidence']['cohort_travelwake']=dict(schema_version=1,mode='pair_copied_tailwait',triggered=False,member_index=index,model_or_objective_changed=False)
    return dict(decisions=decisions,evidence=dict(schema_version=1,rule=RULE,base_source_sha256=BASE_SOURCE_SHA256,
        joint_context=native,priority_context=priority,owner_context=owners,original_tailwait_proposal=parent,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,copied_only=True,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_tailwait__'),
        triggered=False,copied_tailwait_group=True,scope=SCOPE))
