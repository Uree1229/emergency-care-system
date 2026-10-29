"""Conditional queue envelope under the frozen shifted/thinned head law.

Only the local maximum of a random baseline completion and a deterministic
preceding-future completion forecast changes.  Fixed F0, plans and all initial
and residual scores remain the frozen parent quantities.  This is exact for
that declared local law, not a physical joint-queue expectation or a global
monotonicity guarantee.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from . import cohort_fullmenu_model as fullmenu,cohort_stoploss_model as stoploss
from . import cohort_recourse_model as recourse,cohort_followplan_model as followplan

NAME='eat_cohort_envelope_l15_f'
TIERS=(4,2,1)
RULE='frozen_plans_with_exact_declared_baseline_and_deterministic_prior_completion_envelope'
SCOPE='same frozen initial CDFs, first continuation plans and fixed F0; replace max of baseline stoploss mean and deterministic prior remainder by the expectation of their declared maximum; deterministic prior completion, shifted/thinned baseline and cancellation independence remain approximations; no future conditioning or physical queue exactness'
ENVELOPE_RULE='prior_positive_part_plus_baseline_stoploss_at_max_send_prior'
ENVELOPE_SCOPE='for declared nonnegative C0 and deterministic T, E[(max(C0,T)-s)+]=(T-s)+ +E[(C0-max(s,T))+]; local same-T Jensen comparison only; current no-reply denominator retained; inherited J roundoff corrections retained; no additional envelope clamp'
SOURCE_SHA256={
    'cohort_fullmenu_model':'8185c654bb7402d67708b14bd151572b50950cc36d639c0b5153fa37eececaf1',
    'cohort_stoploss_model':'ae9e4f870c56551b922982b914a38418a681f81d210afb66254c4c740bd30c9e',
    'cohort_recourse_model':'e776bd643611a9ce33cbfa3fdbd8f05042fae755c20be5e0a1f4bddfdcebc2de',
    'cohort_followplan_model':'8f8dfc82451b05a804d33253823bde3a6cbb68ff67728d1458dbb17aca0d6268',
}
for module in (fullmenu,stoploss,recourse,followplan):
    if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()!=SOURCE_SHA256[module.__name__.rsplit('.',1)[-1]]:
        raise ValueError('frozen envelope foundation differs: '+module.__name__)


def _finite(value,name):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError('finite nonnegative '+name+' required')
    return float(value)


def _sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def envelope_wait(decomposition,send_seconds,prior_completion_seconds):
    """Stable addition identity, without introducing a new probability law."""
    send=_finite(send_seconds,'send offset');prior=_finite(prior_completion_seconds,'prior completion')
    current=stoploss.base_stoploss_at_elapsed(decomposition,send)
    barrier=max(send,prior)
    at_barrier=current if barrier==send else stoploss.base_stoploss_at_elapsed(decomposition,barrier)
    remainder=max(0.,prior-send);baseline=current['remaining_seconds'];tail=at_barrier['remaining_seconds']
    value=remainder+tail;old=max(baseline,remainder);upper=remainder+baseline
    tolerance=(current['numeric_bound_tolerance_seconds']+at_barrier['numeric_bound_tolerance_seconds']+
        stoploss.BOUND_CORRECTION_ULPS*sum(math.ulp(x) for x in (value,old,upper,remainder,baseline,tail)))
    if not all(math.isfinite(x) for x in (value,old,upper,tolerance)):
        raise ArithmeticError('queue envelope exceeds finite arithmetic')
    if value<old-tolerance or value>upper+tolerance:
        raise ArithmeticError('queue envelope violates same-T local bounds beyond inherited arithmetic allowance')
    return dict(schema_version=1,rule=ENVELOPE_RULE,send_offset_seconds=send,
        prior_completion_seconds=prior,prior_remaining_seconds=remainder,barrier_elapsed_seconds=barrier,
        baseline_at_send=current,baseline_at_barrier=at_barrier,baseline_remaining_seconds=baseline,
        baseline_tail_after_barrier_seconds=tail,old_max_of_means_seconds=old,
        envelope_wait_seconds=value,local_lower_bound_seconds=old,local_upper_bound_seconds=upper,
        envelope_minus_old_max_seconds=value-old,numeric_bound_tolerance_seconds=tolerance,
        local_bounds_within_allowance=True,numeric_clamp_applied=False,
        prior_is_deterministic_forecast=True,scope=ENVELOPE_SCOPE)


def _forward(jobs,plans):
    result=deepcopy(jobs);completion={}
    for job in result:
        send=job['send_offset_seconds'];prior=completion.get(job['hospital'],0.0)
        proof=envelope_wait(job['base_stoploss_decomposition'],send,prior)
        q1=proof['envelope_wait_seconds'];probe=min(send+q1,job['owner_horizon_seconds'])
        survival=1.;factors=[];first=plans[job['first_patient_index']]
        for index,item in enumerate(first['hypothetical_origin']['own_items']):
            if item['hospital']==job['hospital']:continue
            probability=recourse._descriptor_cdf(item,probe);survival*=max(0.,1.-probability)
            factors.append(dict(kind='initial',initial_item_index=index,hospital=item['hospital'],selection_probability_at_probe=probability))
        for other in jobs:
            if stoploss._key(other['patient_id'])==stoploss._key(job['patient_id']) and other['hospital']!=job['hospital']:
                probability=stoploss._future_cdf(other,probe,'fixed_F0_wait_seconds');survival*=max(0.,1.-probability)
                factors.append(dict(kind='future',future_job_index=other['future_job_index'],hospital=other['hospital'],selection_probability_at_probe=probability))
        expected=job['full_mean_work_seconds']*survival;after=send+q1+expected
        job.update(elapsed_seconds=send,base_remaining_seconds=proof['baseline_remaining_seconds'],
            prior_future_completion_seconds=prior,prior_future_remaining_seconds=proof['prior_remaining_seconds'],
            corrected_wait_seconds=q1,probe_offset_seconds=probe,fixed_F0_survival_factors=factors,
            no_prior_accept_probability=survival,expected_work_seconds=expected,
            future_completion_after_seconds=after,queue_envelope=proof)
        completion[job['hospital']]=after
    return result


def score_first_cohort(plans):
    if type(plans) is not list:raise ValueError('complete frozen first plans required')
    if len({stoploss._key(p['patient_id']) for p in plans})!=len(plans):raise ValueError('duplicate frozen first patient')
    jobs=_forward(stoploss._jobs(plans),plans);patients=[];H=stoploss.H
    for pi,parent in enumerate(plans):
        own=sorted((j for j in jobs if j['first_patient_index']==pi),key=lambda j:j['plan_candidate_index'])
        initial=deepcopy(parent['hypothetical_origin']['own_items']);edges=deepcopy(parent['hypothetical_origin']['cell_edges_seconds']);future=[]
        for job in own:
            masses=H.masses_at_edges(lambda x,job=job:stoploss._future_cdf(job,x,'corrected_wait_seconds'),1.,edges)
            future.append(dict(future_job_index=job['future_job_index'],hospital=job['hospital'],travel_seconds=job['travel_seconds'],
                send_offset_seconds=job['send_offset_seconds'],wait_seconds=job['corrected_wait_seconds'],
                service_mu=job['service_mu'],service_sigma=job['service_sigma'],
                total_probability=job['reply_probability']*job['accept_probability'],masses=masses))
        cost=0.
        for job in own:
            send=job['send_offset_seconds'];survival=1.
            for item in initial:survival*=max(0.,1.-recourse._descriptor_cdf(item,send))
            for other in own:survival*=max(0.,1.-stoploss._future_cdf(other,send,'corrected_wait_seconds'))
            cost+=job['inquiry_cost_seconds']*survival
        metrics=recourse._metrics([(x['travel_seconds'],x['masses']) for x in initial+future],edges,cost)
        metrics['immediate_contact_cost_seconds']=parent['initial_contact_cost_seconds'];metrics['total_cost_seconds']+=parent['initial_contact_cost_seconds']
        patients.append(dict(patient_id=parent['patient_id'],effective_priority=parent['effective_priority'],parent_first_plan_index=pi,
            job_indices=[j['future_job_index'] for j in own],fixed_initial_items=initial,cell_edges_seconds=edges,future_items=future,
            initial_contact_cost_seconds=parent['initial_contact_cost_seconds'],future_contact_cost_seconds=cost,metrics=metrics))
    return dict(schema_version=1,rule=RULE,scope=SCOPE,jobs=jobs,patient_scores=patients)


def rescore_row(old_row,prepared):
    """Reuse D projections and plans; never rerun D or residual plan search."""
    index=old_row['row_index'];p=prepared
    if 'base_row_score' in old_row:
        plans=old_row['base_row_score']['first_cohort_future']
        source='inherited_Stoploss_base_row_first_cohort_future'
        source_digest=_sha(plans)
    elif old_row.get('row_kind')=='expanded' and 'dynamic_row_projection' in old_row:
        row=old_row['dynamic_row_projection']
        if row['row_index']!=index:raise ValueError('existing Dynamic row index differs')
        joint=p['views']['joint']
        initial=recourse.score_first_cohort(row,members=p['hedge_members'],current_baseline_proposals=joint['baseline_proposals'],priorities=p['priority_context'])
        reviews=followplan.first_review_sources(row,members=p['recourse_members'],current_baseline_proposals=joint['baseline_proposals']);plans=[]
        for review in reviews:
            pid=review['patient_id'];own=[j for j in initial['jobs'] if stoploss._key(j['patient_id'])==stoploss._key(pid)]
            plans.append(followplan.score_first_patient(p['owner_by'][stoploss._key(pid)],initial_jobs=own,
                row_pending=row['projection']['final_pending'],all_jobs=initial['jobs'],next_review_at=review['next_review_at']))
        source='reconstructed_frozen_plans_from_existing_Dynamic_row'
        source_digest=_sha(row)
    else:raise ValueError('known inherited or expanded Fullmenu row required')
    first_ids={stoploss._key(plan['patient_id']) for plan in plans}
    if len(first_ids)!=len(plans):raise ValueError('duplicate first plan owner')
    residual=[deepcopy(m) for m in old_row['member_metrics'] if stoploss._key(m['patient_id']) not in first_ids]
    first=score_first_cohort(plans)
    metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in first['patient_scores']]+residual
    if [stoploss._key(m['patient_id']) for m in metrics]!=[stoploss._key(m['patient_id']) for m in old_row['member_metrics']]:
        raise ValueError('complete frozen owner metric order differs')
    return dict(row_index=index,first_plan_source=source,first_plan_source_sha256=source_digest,
        first_cohort_envelope=first,unchanged_residual_member_metrics=residual,member_metrics=metrics,
        tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.) for t in TIERS},
        sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.))
