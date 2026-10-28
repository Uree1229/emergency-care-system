"""Independent shifted/thinned current-head stop-loss audit.

The complete Followplan proposal remains independently checked. Its future
contact membership and times stay fixed while shared future work is rebuilt.
No producer model, policy, runner, oracle or future event is imported.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from . import cohort_followplan_audit as foundation
from . import cohort_advance_audit as control_audit

ROOT=Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest()!='3eb9ba7d78e7d3954f16a44abe86c4a22c34e6a9ae43b4a3022d2e9d0b13604a':
    raise ValueError('frozen independent Followplan auditor differs')
if hashlib.sha256(Path(control_audit.__file__).read_bytes()).hexdigest()!='fde57726f49faf8e8d91577d934ad2ef246e59d4a1373ebfe23fd600252f17c2':
    raise ValueError('frozen independent Advance control auditor differs')
base=foundation.base
parent,horizon,joint=foundation.parent,foundation.horizon,foundation.joint
numeric=foundation.numeric
dynamic=foundation.dynamic
require,precise_same,exact_copy=foundation.require,foundation.precise_same,foundation.exact_copy
_pid,_integer,EPOCH=foundation._pid,foundation._integer,foundation.EPOCH
INPUT_KEYS,CALL_KEYS,GROUP_KEYS=foundation.INPUT_KEYS,foundation.CALL_KEYS,foundation.GROUP_KEYS
NAME='eat_cohort_stoploss_l15_f'
BASE_POLICY=foundation.NAME
BASE_SOURCE_SHA256='835c700739170a690ddce4cf26395861d300d950ad370dc292550a55d5362cb4'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_followplan_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Followplan producer source differs')
NEW_INSTRUMENTATION=dict(control_audit.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_stoploss_runner.run_once',first_future_mean_preserving_stoploss_baseline=True)


def _project_name(name):
    if name==NAME:return BASE_POLICY
    return '__foreign_cohort_followplan__' if name==BASE_POLICY else name


def alias_context(context):
    value=deepcopy(context)
    for row in value['eligible']:
        require(row['job']['policy_name']==NAME,'current eligible job has wrong native followplan policy')
        row['job']['policy_name']=BASE_POLICY
    return value


def alias_member(member):
    value=deepcopy(member)
    require(value['workflow_snapshot']['job']['policy_name']==NAME,'current member has wrong native followplan policy')
    value['workflow_snapshot']['job']['policy_name']=BASE_POLICY
    return value


def alias_owners(owners):
    value=deepcopy(owners)
    for row in value['inventory']:
        if row['job'] is not None:row['job']['policy_name']=_project_name(row['job']['policy_name'])
    return value


def alias_events(events):
    value=deepcopy(events)
    for event in value:
        if event['type'] in ('inquiry_workflow_started','inquiry_workflow_paused','inquiry_policy_changed'):
            data=parent._data(event)
            if 'policy_name' in data:event['data']=dict(data,policy_name=_project_name(data['policy_name']))
    return value


def alias_registry(registry):
    value=deepcopy(registry)
    for row in value['jobs']:row['policy_name']=_project_name(row['policy_name'])
    return value


def alias_source(source):
    value=deepcopy(source);value['events']=alias_events(value['events'])
    for row in value['rows']:
        if row['job'] is not None:row['job']['policy_name']=_project_name(row['job']['policy_name'])
    return value


def audit_registry(events,decisions,registry,now,event_prefix,decision_prefix):
    value=foundation.audit_registry(alias_events(events),decisions,alias_registry(registry),now,event_prefix,decision_prefix)
    for row in value['eligible']:row['job']['policy_name']=NAME
    return value


def audit_priority_context(priority,context,members,tails,*,lab_defaults=False):
    return foundation.audit_priority_context(priority,alias_context(context),[alias_member(m) for m in members],
        [alias_member(m) for m in tails],lab_defaults=lab_defaults)


def audit_time_context(events,inputs,member,event_prefix,decision_prefix,decision=None):
    return foundation.audit_time_context(alias_events(events),inputs,alias_member(member),event_prefix,decision_prefix,decision)


def audit_owner_provenance(scenario,events,registry,owners,source):
    return foundation.audit_owner_provenance(scenario,alias_events(events),alias_registry(registry),alias_owners(owners),alias_source(source))


def audit_member_provenance(scenario,events,member,event_prefix,decision_prefix):
    return foundation.audit_member_provenance(scenario,alias_events(events),alias_member(member),event_prefix,decision_prefix)



RESIDUAL_SOURCE_SHA256='8cf391ab5062de374d3c843edd3f6b1be98ac281df9217acc0538cbd98b838ef'
ADVANCE_MODEL_SOURCE_SHA256='ecb3ebd0dc10594a705b20bf193b9a38fc6afa731a6fc79651960853ea94d9e8'
for filename,digest in (('residual_policy.py',RESIDUAL_SOURCE_SHA256),('cohort_advance_model.py',ADVANCE_MODEL_SOURCE_SHA256)):
    require(hashlib.sha256((ROOT/'inquiry_lab'/filename).read_bytes()).hexdigest()==digest,
        'frozen residual or Advance mathematical source differs')
BASE_RESIDUAL_RULE='mean_preserving_shifted_thinned_current_head_stoploss'
MODEL_RULE='frozen_first_future_plans_shifted_thinned_stoploss_F0_chronological_mean_progression'
MODEL_SCOPE='fixed initial CDFs and full parent future plans; exact baseline stop loss for declared C0=F+B*R current-head approximation with fixed current no-reply denominator, mean-preserving parent queue and deterministic followers; unchanged fixed-F0 one-pass survival and chronological mean future-completion maximum; inferred queue stages, follower shifts, cancellation independence and future-job means remain approximate; no replanning, F1 feedback, future-event input or actual capacity release'
RULE='intact_Followplan_menu_frozen_first_future_plans_with_mean_preserving_head_stoploss'
OBJECTIVE='global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer Followplan actual index then original D menu order'
SCOPE='complete current WAITING/H-positive roster; initial CDFs, full future plan membership/times and residual scores fixed; mean-preserving shifted-thinned current-head baseline stop loss with fixed current evidence, unchanged chronological mean future-work recurrence; inferred stages, deterministic followers and joint queue dependence remain approximate; current pair applied, no actual capacity release, future action, reservation or outcome guarantee'


def _finite(value,label,nonnegative=False):
    require(type(value) in (int,float) and math.isfinite(value) and (not nonnegative or value>=0),
        'finite '+label+' required')
    return float(value)


def _normal_log_survival(z):
    """Independent Gaussian tail with an optimally truncated Mills series."""
    if z<8:return math.log(math.erfc(z/math.sqrt(2.0))*.5)
    term=1.0;total=1.0;previous=math.inf
    for order in range(1,200):
        term*=-(2*order-1)/(z*z)
        if abs(term)>=previous:break
        total+=term;previous=abs(term)
        if abs(term)<=abs(total)*1e-16:break
    return -.5*z*z-math.log(z)-.5*math.log(2*math.pi)+math.log(total)


def _log_stoploss_arithmetic(mu,sigma,age):
    """Independent log J and a declared engineering floating-point allowance.

    The sensitivity calculation assumes 32-ULP operand envelopes. It is not a
    certified interval for the platform's transcendental implementations.
    """
    logmean=mu+.5*sigma*sigma
    if age==0:return logmean,32*math.ulp(logmean)
    logage=math.log(age);z=(logage-mu)/sigma
    left=logmean+_normal_log_survival(z-sigma)
    right=logage+_normal_log_survival(z)
    difference=right-left;envelope=32*(math.ulp(left)+math.ulp(right))
    require(difference+envelope<0,'independent positive lognormal tail not numerically resolved')
    result=left+math.log(-math.expm1(difference))
    allowance=envelope/(-math.expm1(difference+envelope))+32*math.ulp(result)
    return result,allowance


def _log_stoploss(mu,sigma,age):
    return _log_stoploss_arithmetic(mu,sigma,age)[0]


def _current_log_denominator(mu,sigma,reply,age):
    if age==0 or reply==0:return 0.0
    logtail=_normal_log_survival((math.log(age)-mu)/sigma)
    if reply==1:return logtail
    left,right=math.log1p(-reply),math.log(reply)+logtail
    high=max(left,right)
    return high+math.log1p(math.exp(min(left,right)-high))


def base_stoploss_decomposition(row):
    """Rebuild C0=F+B*R; R conditions on the current public no-reply law."""
    q0=max(0.0,_finite(row['estimated_wait_seconds'],'parent queue wait'))
    mR=_finite(row['head_expected_remaining_seconds'],'current head residual',True)
    mu=_finite(row['service_mu'],'service mu');sigma=_finite(row['service_sigma'],'service sigma')
    reply=_finite(row['reply_probability'],'reply probability')
    require(sigma>0 and 0<=reply<=1,'invalid stop-loss distribution')
    count=row['queue_count'];head=row['head'];age=row['age_seconds']
    require(type(count) is int and count>=0,'integer public queue count required')
    if count==0 or head is None:
        require(count==0 and head is None and age is None and q0==0 and mR==0,
            'empty public head/queue has nonzero residual law')
    else:
        require(type(head) is dict and age is not None,'nonempty public head lacks inferred age')
        age=_finite(age,'current inferred head age',True)
    logden=None if age is None else _current_log_denominator(mu,sigma,reply,age)
    expected=0.0
    if age is not None:
        expected=math.exp(_log_stoploss(mu,sigma,age)-logden)
        require((mR==0)==(expected==0) and math.isclose(mR,expected,rel_tol=2e-12,
                abs_tol=32*max(math.ulp(mR),math.ulp(expected))),
            'source head mean differs from independent J(a)/D(a)')
    mean_tolerance=8*max(math.ulp(mR),math.ulp(expected))
    if mR==0:F,p,branch=q0,0.0,'deterministic_zero_head'
    elif q0>=mR:F,p,branch=q0-mR,1.0,'shifted_full_head'
    else:F,p,branch=0.0,q0/mR,'thinned_head'
    reconstructed=F+p*mR
    require(abs(reconstructed-q0)<=8*max(math.ulp(q0),math.ulp(reconstructed)),
        'shifted/thinned current-head mean is not preserved')
    return dict(schema_version=1,rule=BASE_RESIDUAL_RULE,decomposition_branch=branch,
        q0_seconds=q0,head_expected_remaining_seconds=mR,deterministic_follower_seconds=F,
        head_inclusion_probability=p,reconstructed_mean_seconds=reconstructed,
        mean_arithmetic_error_seconds=reconstructed-q0,current_age_seconds=age,
        recomputed_current_head_mean_seconds=expected,head_mean_numeric_tolerance_seconds=mean_tolerance,
        current_log_no_reply_probability=logden,current_no_reply_probability=None if logden is None else math.exp(logden),
        service_mu=mu,service_sigma=sigma,reply_probability=reply,residual_source_sha256=RESIDUAL_SOURCE_SHA256,
        mathematical_mean_identity='F+p*mR=q0',conditioning='current_no_reply_only')


def base_stoploss_at_elapsed(decomposition,elapsed_seconds):
    """Independent E[(F+B*R-s)+] with its original D(a) held fixed."""
    s=_finite(elapsed_seconds,'elapsed seconds',True);d=decomposition
    q0,F,p=d['q0_seconds'],d['deterministic_follower_seconds'],d['head_inclusion_probability']
    age,logden=d['current_age_seconds'],d['current_log_no_reply_probability']
    shifted=max(0.0,s-F);future_age=None if age is None else age+shifted
    logJ=None;logerr=None;conditional=None
    if q0==0:branch,raw='zero',0.0
    elif s==0:branch,raw='origin',q0
    elif s<F:branch,raw='before_shift',F-s+p*d['head_expected_remaining_seconds']
    elif p==0:branch,raw='deterministic_cleared',0.0
    else:
        logJ,logerr=_log_stoploss_arithmetic(d['service_mu'],d['service_sigma'],future_age)
        conditional=math.exp(logJ-logden);branch,raw='head_tail',p*conditional
    lower=max(q0-s,0.0)
    remaining=min(q0,max(lower,raw))
    tolerance=32*max(math.ulp(q0),math.ulp(raw),math.ulp(lower))
    if logerr is not None:
        tolerance=max(tolerance,abs(raw)*math.expm1(logerr+32*math.ulp(logden)))
    require(math.isfinite(raw) and math.isfinite(tolerance) and abs(remaining-raw)<=tolerance,
        'Jensen or monotone stop-loss correction exceeds roundoff')
    return dict(schema_version=1,elapsed_seconds=s,branch=branch,residual_elapsed_seconds=shifted,
        stoploss_age_seconds=future_age,log_stoploss=logJ,fixed_log_no_reply_probability=logden,
        conditional_head_remaining_seconds=conditional,baseline_positive_part_seconds=lower,
        raw_remaining_seconds=raw,remaining_seconds=remaining,numeric_bound_correction_seconds=remaining-raw,
        numeric_bound_tolerance_seconds=tolerance,log_stoploss_numeric_error_bound=logerr)


def future_cdf(job,x,wait_field):
    return job['reply_probability']*job['accept_probability']*parent._cdf(
        x-job['send_offset_seconds']-job[wait_field],job['service_mu'],job['service_sigma'])


def flatten_future_jobs(plans):
    """Derive exactly the frozen contacts from audited parent plans."""
    require(type(plans) is list and len({base.owner_key(p['patient_id']) for p in plans})==len(plans),
        'complete unique frozen first-plan inventory required')
    jobs=[]
    for pi,first in enumerate(plans):
        remaining=first['source_owner']['evaluation_end_at']-first['source_owner']['inputs']['now']
        records=first['continuation']['queue_records'];by_hospital={r['hospital']:(i,r) for i,r in enumerate(records)}
        require(len(by_hospital)==len(records),'repeated frozen future queue record')
        for ci,contact in enumerate(first['continuation']['plan']):
            s=contact['offset_seconds']
            if s is None:continue
            require(type(s) is int and 0<=s<remaining,'future offset outside public H')
            qi,row=by_hospital[contact['hospital']]
            require(type(row['assumed_workers']) is int and row['assumed_workers']==1,'future fractional or hidden workers')
            require(all(type(row[k]) in (int,float) and math.isfinite(row[k]) and row[k]>=0
                for k in ('estimated_wait_seconds','mean_service_seconds')),'future queue work or wait invalid')
            decomposition=base_stoploss_decomposition(row)
            at_send=base_stoploss_at_elapsed(decomposition,s)
            jobs.append(dict(patient_id=first['patient_id'],first_patient_index=pi,plan_candidate_index=ci,
                parent_queue_record_index=qi,hospital=contact['hospital'],send_offset_seconds=s,owner_horizon_seconds=remaining,
                cell_edges_seconds=deepcopy(first['hypothetical_origin']['cell_edges_seconds']),
                base_wait_seconds=row['estimated_wait_seconds'],fixed_F0_wait_seconds=at_send['remaining_seconds'],
                base_stoploss_decomposition=decomposition,base_stoploss_at_send=at_send,
                service_mu=row['service_mu'],service_sigma=row['service_sigma'],
                reply_probability=row['reply_probability'],accept_probability=row['accept_probability'],
                mean_service_seconds=row['mean_service_seconds'],assumed_workers=row['assumed_workers'],
                full_mean_work_seconds=row['mean_service_seconds']/row['assumed_workers'],travel_seconds=row['travel_seconds'],
                inquiry_cost_seconds=row['inquiry_cost_seconds'],source_plan=deepcopy(contact),source_queue_record=deepcopy(row)))
    jobs.sort(key=lambda q:(q['send_offset_seconds'],q['first_patient_index'],q['plan_candidate_index']))
    for i,row in enumerate(jobs):row['future_job_index']=i
    return jobs


def expected_future_work(jobs,plans):
    """Independent stop-loss baseline and unchanged mean future recurrence."""
    require(jobs==sorted(jobs,key=lambda q:(q['send_offset_seconds'],q['first_patient_index'],q['plan_candidate_index']))
        and [j['future_job_index'] for j in jobs]==list(range(len(jobs))),'future chronological or tie order differs')
    values=[];completion={}
    for job in jobs:
        s=job['send_offset_seconds'];prior=completion.get(job['hospital'],0.0)
        d=base_stoploss_decomposition(job['source_queue_record'])
        at_send=base_stoploss_at_elapsed(d,s)
        precise_same(job['base_stoploss_decomposition'],d,'current-head source decomposition')
        precise_same(job['base_stoploss_at_send'],at_send,'fixed-current-evidence baseline at send')
        precise_same(job['fixed_F0_wait_seconds'],at_send['remaining_seconds'],'fixed stoploss F0')
        base_remaining=at_send['remaining_seconds'];prior_remaining=max(0.0,prior-s)
        wait=max(base_remaining,prior_remaining,0.0)
        probe=min(s+wait,job['owner_horizon_seconds'])
        require(wait>=base_remaining and wait>=prior_remaining and probe>=0,'mean queue baseline/completion maximum differs')
        no_accept=1.0;factors=[]
        origin=plans[job['first_patient_index']]['hypothetical_origin']
        for index,item in enumerate(origin['own_items']):
            if item['hospital']==job['hospital']:continue
            probability=base.descriptor_cdf(item,probe);no_accept*=max(0.0,1.0-probability)
            factors.append(dict(kind='initial',initial_item_index=index,hospital=item['hospital'],selection_probability_at_probe=probability))
        for other in jobs:
            if base.owner_key(other['patient_id'])!=base.owner_key(job['patient_id']) or other['hospital']==job['hospital']:continue
            probability=future_cdf(other,probe,'fixed_F0_wait_seconds');no_accept*=max(0.0,1.0-probability)
            factors.append(dict(kind='future',future_job_index=other['future_job_index'],hospital=other['hospital'],selection_probability_at_probe=probability))
        require(0<=no_accept<=1,'invalid fixed-F0 no-accept survival')
        expected=job['full_mean_work_seconds']*no_accept
        after=s+wait+expected
        values.append(dict(job,elapsed_seconds=s,base_remaining_seconds=base_remaining,
            prior_future_completion_seconds=prior,prior_future_remaining_seconds=prior_remaining,
            corrected_wait_seconds=wait,probe_offset_seconds=probe,fixed_F0_survival_factors=factors,
            no_prior_accept_probability=no_accept,expected_work_seconds=expected,future_completion_after_seconds=after))
        completion[job['hospital']]=after
    return values


def score_first_cohort(plans):
    jobs=expected_future_work(flatten_future_jobs(plans),plans);patients=[]
    for pi,first in enumerate(plans):
        own=sorted((j for j in jobs if j['first_patient_index']==pi),key=lambda q:q['plan_candidate_index'])
        initial=deepcopy(first['hypothetical_origin']['own_items']);edges=deepcopy(first['hypothetical_origin']['cell_edges_seconds'])
        remaining=first['source_owner']['evaluation_end_at']-first['source_owner']['inputs']['now'];bins=horizon.intervals(remaining)
        require(edges==[r for _,r in bins],'unchanged partial H grid differs')
        future=[]
        for job in own:
            masses=horizon.masses(lambda x,job=job:future_cdf(job,x,'corrected_wait_seconds'),1.0,bins)
            future.append(dict(future_job_index=job['future_job_index'],hospital=job['hospital'],travel_seconds=job['travel_seconds'],
                send_offset_seconds=job['send_offset_seconds'],wait_seconds=job['corrected_wait_seconds'],
                service_mu=job['service_mu'],service_sigma=job['service_sigma'],
                total_probability=job['reply_probability']*job['accept_probability'],masses=masses))
        contact_cost=0.0
        for job in own:
            s=job['send_offset_seconds'];survival=1.0
            for item in initial:survival*=max(0.0,1.0-base.descriptor_cdf(item,s))
            for other in own:survival*=max(0.0,1.0-future_cdf(other,s,'corrected_wait_seconds'))
            contact_cost+=job['inquiry_cost_seconds']*survival
        metrics=numeric.corrected_owner_metrics([(r['travel_seconds'],r['masses']) for r in initial+future],bins,remaining,contact_cost)
        metrics['immediate_contact_cost_seconds']=first['initial_contact_cost_seconds'];metrics['total_cost_seconds']+=first['initial_contact_cost_seconds']
        patients.append(dict(patient_id=first['patient_id'],effective_priority=first['effective_priority'],parent_first_plan_index=pi,
            job_indices=[j['future_job_index'] for j in own],fixed_initial_items=initial,cell_edges_seconds=edges,future_items=future,
            initial_contact_cost_seconds=first['initial_contact_cost_seconds'],future_contact_cost_seconds=contact_cost,metrics=metrics))
    return dict(schema_version=1,rule=MODEL_RULE,scope=MODEL_SCOPE,jobs=jobs,patient_scores=patients)


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
    require(type(evidence) is dict and type(evidence.get('original_followplan_proposal')) is dict
        and set(evidence['original_followplan_proposal'])=={'decisions','evidence'},'missing intact original Followplan proposal')
    original=evidence['original_followplan_proposal'];bm=[alias_member(m) for m in members];bt=[alias_member(m) for m in tails]
    checked=foundation.audit_pair(bm,alias_context(context),bt,original['decisions'],original['evidence'],priority_context,alias_owners(owner_context))
    priorities=audit_priority_context(priority_context,context,members,tails)
    old=original['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    menu=old['menu_indices'];scores=[];selection=None
    counts=Counter({k:0 for k in ('stoploss_menus_evaluated','stoploss_rows_checked','stoploss_future_jobs_checked',
        'stoploss_discounted_jobs_checked','stoploss_first_patient_scores_checked')})
    hedge=old['original_recourse_proposal']['evidence']['original_hedge_proposal']
    table=dynamic._nested_joint(hedge['evidence']['original_dynamic_proposal']['evidence']['original_cohort_success_proposal'])['evidence']
    if evaluated:
        require(1<=len(menu)<=3 and type(evidence.get('row_scores')) is list and len(evidence['row_scores'])==len(menu),
            'future stoploss altered bounded original menu')
        by_index={r['row_index']:r for r in old['row_scores']}
        for index,recorded in zip(menu,evidence['row_scores']):
            prior=by_index[index]
            require(type(recorded) is dict and set(recorded)=={'row_index','base_row_score','first_cohort_stoploss',
                'owner_scores','member_metrics','tier_probabilities','sum_capped_wait_seconds'},'future stoploss row fields differ')
            exact_copy(recorded['base_row_score'],prior,'intact independently audited Followplan row')
            first=score_first_cohort(prior['first_cohort_future']);residual=deepcopy(prior['owner_scores'])
            metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics']))
                for s in first['patient_scores']+residual]
            require(sorted(base.owner_key(m['patient_id']) for m in metrics)
                ==sorted(base.owner_key(o['patient']['id']) for o in owner_context['inventory']),'future stoploss owner partition differs')
            wanted=dict(row_index=index,base_row_score=prior,first_cohort_stoploss=first,owner_scores=residual,member_metrics=metrics,
                tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.0) for t in (4,2,1)},
                sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.0))
            precise_same(recorded,wanted,'independent fixed-future-plan workahead row')
            scores.append(wanted);counts['stoploss_rows_checked']+=1;counts['stoploss_future_jobs_checked']+=len(first['jobs'])
            counts['stoploss_discounted_jobs_checked']+=sum(j['no_prior_accept_probability']<1.0 for j in first['jobs'])
            counts['stoploss_first_patient_scores_checked']+=len(first['patient_scores'])
        selection=dynamic.base.global_staged_choice(scores,base_index)
        selection['base_selected_tied']=selection.pop('base_budget_tied');selected=selection['selected_index'];counts['stoploss_menus_evaluated']+=1
    else:require(menu==[] and evidence.get('row_scores')==[],'unevaluated Followplan invented stoploss rows')
    intervened=selected!=base_index
    wanted=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_followplan__'),
        joint_context=context,priority_context=priority_context,owner_context=owner_context,original_followplan_proposal=original,
        evaluated=evaluated,menu_indices=menu,row_scores=scores,selection=selection,base_selected_index=base_index,selected_index=selected,
        intervened=intervened,tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=OBJECTIVE,scope=SCOPE)
    precise_same(evidence,wanted,'complete independent fixed-future stoploss choice')
    require(type(outputs) is list and len(outputs)==2,'actual stoploss pair output inventory differs')
    chosen=next((r for r in scores if r['row_index']==selected),None)
    for index in range(2):
        actual=deepcopy(original['decisions'][index])
        if intervened:
            hospitals=table['combinations'][selected]['hospitals'][index]
            actual=deepcopy(table['baseline_proposals'][index]);actual['send']=list(hospitals);actual['reason']='cohort_stoploss_current_only'
            ev=actual['evidence'];ev['sent_hospitals']=list(hospitals);ev['planned_expected_cost']=round(chosen['member_metrics'][index]['metrics']['total_cost_seconds'],3)
            for row in ev['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
        actual['evidence']['policy']=NAME
        actual['evidence']['cohort_stoploss']=dict(schema_version=1,mode='pair_cohort_stoploss',evaluated=evaluated,
            intervened=intervened,member_index=index,base_selected_index=base_index,selected_index=selected,
            effective_priority=priorities[_pid(members[index]['patient_id'])])
        precise_same(outputs[index],actual,'separately reconstructed actual stoploss outputs')
    checked=dict(checked,original_Followplan_groups_applied=checked['joint_groups_applied'],joint_groups_applied=int(selected!=table['baseline_index']))
    return dict(checked,**dict(counts),stoploss_interventions=int(intervened))


_same=base._same
_audit_phase_order=base._audit_phase_order
time_context_from_prefix=audit_time_context


def _audit_calls(scenario, branch):
    events, calls, decisions = branch['db_events'], branch['policy_calls'], branch['db_decisions']
    groups = branch['joint_groups']; specs = {p['id']: p for p in scenario['patients']}
    require(type(events) is list and type(calls) is list and type(decisions) is list
        and len(calls) == len(decisions) and type(groups) is list, 'call/decision/group inventory differs')
    _audit_phase_order(calls)
    require(all(type(e['id']) is int and e['id'] > 0 for e in events)
        and [e['id'] for e in events] == sorted({e['id'] for e in events}), 'event IDs not unique and ordered')
    event_ids = {e['id'] for e in events}; sent = [e for e in events if e['type'] == 'inquiry_sent']
    group_map = {}; checks = Counter()
    for index, group in enumerate(groups, 1):
        require(type(group) is dict and set(group) == GROUP_KEYS and type(group['id']) is int and group['id'] == index,
            'unknown, missing or reordered joint-group fields')
        for key in ('now', 'event_prefix_max', 'decision_prefix_max', 'revision'):
            _integer(group[key], 'group '+key)
        require(type(group['validated_before_write']) is bool and group['validated_before_write'],
            'group lacks all-member validation before first write')
        require(group['event_prefix_max'] in event_ids and group['db_decision_ids']
            == [group['decision_prefix_max']+1, group['decision_prefix_max']+2],
            'joint decisions not the next two recorded decisions at common prefix')
        wanted = audit_registry(events, decisions, group['registry_snapshot'], group['now'],
            group['event_prefix_max'], group['decision_prefix_max'])
        _same(group['context'], wanted, 'complete current public eligibility')
        require(len(wanted['pair_patient_ids']) == 2, 'group created without two eligible current patients')
        checks.update(audit_pair(group['members'], wanted, group['tail_members'], group['outputs'], group['evidence'], group['priority_context'], group['owner_context']))
        count, history_proof = audit_owner_provenance(scenario, events, group['registry_snapshot'], group['owner_context'], group['owner_source_snapshot'])
        checks['owner_public_provenance_checked'] += count
        checks['owner_full_source_profiles_checked'] += count
        checks['historical_source_profiles_checked'] += history_proof['historical_owners_verified']
        audit_priority_context(group['priority_context'], wanted, group['members'], group['tail_members'], lab_defaults=True)
        for tail in group['tail_members']:
            checks['tail_public_provenance_checked'] += audit_member_provenance(scenario, events, tail,
                group['event_prefix_max'], group['decision_prefix_max'])
        for member in group['members']+group['tail_members']:
            require(member['workflow_snapshot']['apply_snapshot']['revision'] == group['revision'],
                'member was not captured at common planning revision')
        group_map[group['id']] = group
    declared, actual, group_calls = Counter(), Counter(), Counter()
    previous = 0; last_prefix = 0
    for call, decision in zip(calls, decisions):
        grouped = 'joint_group_id' in call
        expected_keys = CALL_KEYS | {'joint_group_id', 'joint_member_index'} if grouped else CALL_KEYS
        require(type(call) is dict and set(call) == expected_keys, 'unknown or missing policy-call fields')
        pid, at, inputs, output = call['patient_id'], call['at'], call['inputs'], call['output']
        prefix = call['db_event_prefix_max']; now = EPOCH+at
        require(pid in specs and type(at) is int and specs[pid]['at'] <= at < specs[pid]['at']+300,
            'policy used future or out-of-window patient')
        require(type(prefix) is int and prefix >= last_prefix and prefix in event_ids,
            'invalid or reordered committed event prefix')
        last_prefix = prefix
        require(type(inputs) is dict and set(inputs) == INPUT_KEYS and not horizon._contains_private(inputs)
            and inputs['now'] == now, 'private, future or unknown policy input')
        require(call['name'] == branch['policy'] == decision['policy_name'] == NAME
            and decision['patient_id'] == pid and decision['at'] == now and type(decision['id']) is int
            and decision['id'] == call['db_decision_id'] > previous, 'committed policy identity/clock differs')
        exact_copy(output['send'], decision['hospital_ids'], 'committed sends')
        exact_copy(output['reason'], decision['reason'], 'committed reason')
        exact_copy(output['evidence'], decision['evidence'], 'committed evidence')
        if grouped:
            gid, index = call['joint_group_id'], call['joint_member_index']
            require(type(gid) is int and gid in group_map and type(index) is int and index in (0, 1),
                'unknown joint-group or member index')
            group = group_map[gid]; member = group['members'][index]
            require(group['now'] == now and prefix == group['event_prefix_max']
                and decision['id'] == group['db_decision_ids'][index]
                and previous == group['decision_prefix_max']+index,
                'group decisions were interleaved or used different common prefixes')
            _same(member, dict(patient_id=pid, inputs=inputs, workflow_snapshot=call['workflow_snapshot']), 'linked group member')
            exact_copy(output, group['outputs'][index], 'linked group output')
            dprefix = group['decision_prefix_max']; group_calls[gid, index] += 1
        else:
            member = dict(patient_id=pid, inputs=inputs, workflow_snapshot=call['workflow_snapshot'])
            dprefix = previous
            require(output['evidence'].get('joint_tail') == dict(schema_version=1, mode='horizon_fallback', guard_evaluated=False)
                and output['evidence'].get('joint_first') == dict(schema_version=1, mode='horizon_fallback', current_only=False)
                and output['evidence'].get('joint_budget') == dict(schema_version=1, mode='horizon_fallback', projection_evaluated=False)
                and output['evidence'].get('cohort_success') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False)
                and output['evidence'].get('cohort_dynamic') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False)
                and output['evidence'].get('cohort_hedge') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False),
                'ungrouped decision changed the original H fallback')
            require(output['evidence'].get('cohort_recourse') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed recourse scope')
            require(output['evidence'].get('cohort_followplan') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed followplan scope')
            require(output['evidence'].get('cohort_stoploss') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed stoploss scope')
            normalized = deepcopy(output); normalized['evidence'].pop('cohort_stoploss'); normalized['evidence'].pop('cohort_followplan'); normalized['evidence'].pop('cohort_recourse')
            normalized['evidence'].pop('joint_first')
            normalized['evidence'].pop('joint_tail')
            normalized['evidence'].pop('joint_budget')
            normalized['evidence'].pop('cohort_success')
            normalized['evidence'].pop('cohort_dynamic')
            normalized['evidence'].pop('cohort_hedge')
            normalized['evidence']['policy'] = horizon.NAME
            checks.update(horizon.audit_horizon(inputs, normalized))
        context = time_context_from_prefix(events, inputs, member, prefix, dprefix, decision)
        require(context['evaluation_end_at'] == EPOCH+specs[pid]['at']+300,
            'public H differs from registered scenario patient')
        previous = decision['id']
        history, pending, own, _ = horizon._ledger(events, now, pid, prefix)
        travel = specs[pid]['travel_seconds']
        expected_own = [dict(row, travel_seconds=travel[row['hospital']]) for row in own if row['hospital'] in travel]
        require(inputs['observations'] == history and inputs['pending'] == pending
            and inputs['patient_pending'] == expected_own, 'common-prefix public observation ledger differs')
        earlier = [e for e in sent if e['id'] <= prefix and e['patient_id'] == pid]
        contacted = {e['hospital_id'] for e in earlier}
        require(type(inputs['max_new']) is int and inputs['max_new'] == 10-len(contacted)
            and inputs['last_contact_at'] == max((e['at'] for e in earlier if e['at'] <= now), default=None),
            'original contact budget/history differs')
        ranked = horizon._ranked(inputs['candidates'])
        require({r['hospital'] for r in ranked} == set(travel)-contacted
            and all(r['travel_seconds'] == travel[r['hospital']] for r in ranked),
            'currently eligible uncontacted offers/travel differ from frozen lab')
        require([r['hospital'] for r in ranked] == sorted(set(travel)-contacted, key=lambda h: (travel[h], h))
            and all(c.get('resources_enough') is True for c in inputs['candidates']),
            'actual candidate order/resource eligibility differs from frozen public lab construction')
        require(type(output['send']) is list and len(set(output['send'])) == len(output['send'])
            and len(output['send']) <= inputs['max_new'] and set(output['send']) <= set(travel)-contacted
            and sum(now-r['submitted_at'] <= 180 for r in expected_own)+len(output['send']) <= 4,
            'contact count/candidate or original EAT concurrency constraint differs')
        for hid in output['send']:
            matching = [e for e in sent if e['patient_id'] == pid and e['hospital_id'] == hid
                and e['at'] == now and e['id'] > prefix]
            require(len(matching) == 1, 'decision lacks unique subsequent inquiry send')
            declared[matching[0]['id']] += 1
    require(group_calls == Counter({(gid, index): 1 for gid in group_map for index in (0, 1)}),
        'joint members/committed calls not covered exactly once')
    for group in groups:
        send_ids = [[e['id'] for e in sent if e['patient_id'] == m['patient_id']
            and e['id'] > group['event_prefix_max'] and e['at'] == group['now']]
            for m in group['members']]
        if all(send_ids):
            require(max(send_ids[0]) < min(send_ids[1]), 'new shared FIFO order differs from modeled member order')
    for event in sent:
        if event['patient_id'] in specs: actual[event['id']] += 1
    require(declared == actual, 'actual inquiries not covered exactly once by saved policy calls')
    return dict(policy_calls=len(calls), **dict(checks))


def audit_branch(scenario,branch):
    if branch.get('policy')!=NAME:
        view=dict(schema_version=1,scenario=scenario,arms=[[branch['policy'],branch['rule']]],branches=[branch],
            environment_sha256=branch['environment_sha256'],initial_snapshot_sha256=branch['snapshot_sha256'])
        return control_audit.audit_replay(view)['branch_audits'][0]
    try:
        environment=hashlib.sha256(json.dumps(scenario,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        require(branch['environment_sha256']==environment and branch['rule']=='immediate','bound public environment or confirmation rule differs')
        exact_copy(branch['instrumentation'],NEW_INSTRUMENTATION,'new adapter source contract')
        checks=_audit_calls(scenario,branch);accounting=horizon._audit_trace_fixed(scenario,branch)
        total_j=sum(1800 if p['failed'] else p['estimated_arrival_seconds'] for p in branch['patients'])
        return dict(passed=True,**checks,**accounting,total_arrival_failure_cost_seconds=total_j,
            mean_arrival_failure_cost_seconds=total_j/len(branch['patients']) if branch['patients'] else None,
            original_Hedge_proposal_independently_checked=True,original_D_CS_continuation_intact=True,
            original_Recourse_proposal_independently_checked=True,
            original_Followplan_proposal_independently_checked=True,
            fixed_F0_one_pass_expected_work_independently_rebuilt=True,
            first_patient_static_next_review_continuation_independently_rebuilt=True,
            frozen_first_future_plans_shared_work_independently_rebuilt=True,
            fixed_F0_future_survival_and_corrected_contact_cost_independently_checked=True,
            chronological_mean_queue_completion_independently_checked=True,
            fixed_F0_uses_mean_preserving_current_head_stoploss=True,
            current_denominator_fixed_without_future_silence_conditioning=True,
            shifted_thinned_mean_identity_and_Jensen_bounds_independently_checked=True,
            exact_baseline_only_under_declared_shifted_thinned_head_law=True,
            log_arithmetic_sensitivity_allowance_independently_checked=True,
            numerical_allowance_scope='engineering 32-ULP operand envelopes propagated through log-stoploss sensitivity; not a certified transcendental interval',
            exact_stochastic_residual_expectation_claimed=False,
            hypothetical_descriptors_separate_from_committed_source=True,
            actual_pending_and_own_CDF_unchanged=True,integer_occupancy_and_contact_budget_independently_checked=True,
            actual_outputs_sends_and_all_patient_FIFO_checked=True,physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='complete actual source captures and prefix/version checks; physical atomicity needs frozen adapter and rollback tests',
            scope='intact original Followplan proposal and unchanged initial CDFs/full future plans; independent mean-preserving shifted/thinned inferred-head stop loss with fixed current denominator, baseline/prior-completion maximum and unchanged chronological mean future-work recurrence; exact baseline only under declared law, deterministic follower and unknown service-stage approximations remain; residual scores and actual caps unchanged; late H unchanged; actual all-patient trace accounting')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise base.CohortHedgeAuditError('malformed saved cohort-stoploss artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version']==1 and type(replay['branches']) is list,'invalid complete replay')
    require(replay['arms']==[[b['policy'],b['rule']] for b in replay['branches']]
        and len({tuple(a) for a in replay['arms']})==len(replay['arms'])>0,'complete arm inventory differs')
    require(all(b['environment_sha256']==replay['environment_sha256']
        and b['snapshot_sha256']==replay['initial_snapshot_sha256'] for b in replay['branches']), 'paired initial environment differs')
    if not any(b.get('policy')==NAME for b in replay['branches']):return control_audit.audit_replay(replay)
    controls=[b for b in replay['branches'] if b.get('policy')!=NAME];control_proof=None;by_policy={}
    if controls:
        view=dict(replay,branches=controls,arms=[[b['policy'],b['rule']] for b in controls])
        control_proof=control_audit.audit_replay(view)
        by_policy={(b['policy'],b['rule']):p for b,p in zip(controls,control_proof['branch_audits'])}
    checked=[audit_branch(replay['scenario'],b) if b['policy']==NAME else by_policy[b['policy'],b['rule']] for b in replay['branches']]
    return dict(passed=True,branches=len(checked),branch_audits=checked,new_executions=0,
        preserved_parent_audit_proof=control_proof)

