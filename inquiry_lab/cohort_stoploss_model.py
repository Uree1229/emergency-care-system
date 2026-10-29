"""Mean-preserving current-head stop loss for frozen first-patient H plans.

Only the baseline residual changes from Advance.  C0=F+B*R preserves the
parent queue mean, with a deterministic follower shift F and Bernoulli B.
R is the inferred head residual conditional on CURRENT no reply.  Its future
stop loss keeps that current denominator.  This is exact for the declared
shifted-thinned head law, not for a latent physical whole queue.  The inferred
stage, deterministic followers, fixed-F0 independence and mean future-job
completion recurrence remain approximations.  No actual capacity is released.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import math
from . import cohort_followplan_model as original, residual_policy as residual

NAME='eat_cohort_stoploss_l15_f'
BASE_SOURCE_SHA256='8f8dfc82451b05a804d33253823bde3a6cbb68ff67728d1458dbb17aca0d6268'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen first-contention model foundation differs')
H=original.H
TIERS=(4,2,1)
RESIDUAL_SOURCE_SHA256='8cf391ab5062de374d3c843edd3f6b1be98ac281df9217acc0538cbd98b838ef'
ADVANCE_MODEL_SOURCE_SHA256='ecb3ebd0dc10594a705b20bf193b9a38fc6afa731a6fc79651960853ea94d9e8'
for source,digest in ((Path(residual.__file__),RESIDUAL_SOURCE_SHA256),
        (Path(__file__).with_name('cohort_advance_model.py'),ADVANCE_MODEL_SOURCE_SHA256)):
    if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:
        raise ValueError('frozen stoploss residual or Advance foundation differs')
BASE_RESIDUAL_RULE='mean_preserving_shifted_thinned_current_head_stoploss'
HEAD_MEAN_NUMERIC_ULPS=8
BOUND_CORRECTION_ULPS=32
RULE='frozen_first_future_plans_shifted_thinned_stoploss_F0_chronological_mean_progression'
SCOPE='fixed initial CDFs and full parent future plans; exact baseline stop loss for declared C0=F+B*R current-head approximation with fixed current no-reply denominator, mean-preserving parent queue and deterministic followers; unchanged fixed-F0 one-pass survival and chronological mean future-completion maximum; inferred queue stages, follower shifts, cancellation independence and future-job means remain approximate; no replanning, F1 feedback, future-event input or actual capacity release'


def _finite(value,name,*,nonnegative=False):
    if type(value) not in (int,float) or not math.isfinite(value) or (nonnegative and value<0):
        raise ValueError('finite '+name+' required')
    return float(value)


def _log_stoploss(mu,sigma,age):
    """log J(age), using the pinned residual logtail/expm1 arithmetic."""
    return _log_stoploss_arithmetic(mu,sigma,age)[0]


def _log_stoploss_arithmetic(mu,sigma,age):
    """Log value and declared arithmetic allowance, not a libm interval proof.

    Each log operand is allowed 32 ULPs.  The subtraction sensitivity is
    bounded over that operand envelope, then propagated through exp by the
    caller.  This exposes late-tail cancellation instead of treating all
    possible log subtraction errors as a few ULPs of the final seconds value.
    """
    logmean=mu+sigma*sigma/2.
    if age==0:return logmean,BOUND_CORRECTION_ULPS*math.ulp(logmean)
    logage=math.log(age);z=(logage-mu)/sigma
    left=logmean+residual._log_normal_survival(z-sigma)
    right=logage+residual._log_normal_survival(z)
    difference=right-left
    if difference>=0:raise ArithmeticError('stop-loss subtraction lost its positive tail')
    logJ=left+math.log(-math.expm1(difference))
    operand_error=BOUND_CORRECTION_ULPS*(math.ulp(left)+math.ulp(right))
    if not all(math.isfinite(x) for x in (logJ,operand_error)) or difference+operand_error>=0:
        raise ArithmeticError('stop-loss positive tail unresolved within log operand tolerance')
    log_error=operand_error/(-math.expm1(difference+operand_error))+BOUND_CORRECTION_ULPS*math.ulp(logJ)
    if not math.isfinite(log_error):raise ArithmeticError('stop-loss log arithmetic allowance is nonfinite')
    return logJ,log_error


def _current_log_denominator(mu,sigma,r,age):
    logtail=0. if age==0 else residual._log_normal_survival((math.log(age)-mu)/sigma)
    return residual._logadd(math.log1p(-r) if r<1 else -math.inf,
        math.log(r)+logtail if r>0 else -math.inf)


def base_stoploss_decomposition(row):
    """Declare C0=F+B*R using only the complete frozen parent queue row.

    The mathematical identity F+p*mR=q0 is exact.  The recorded floating
    reconstruction/error discloses binary arithmetic; elapsed zero returns
    q0 directly, so no numerical error changes the parent initial wait.
    """
    q0=max(0.,_finite(row['estimated_wait_seconds'],'parent queue wait'))
    mR=_finite(row['head_expected_remaining_seconds'],'head residual',nonnegative=True)
    mu=_finite(row['service_mu'],'service mu');sigma=_finite(row['service_sigma'],'service sigma')
    r=_finite(row['reply_probability'],'reply probability')
    if sigma<=0 or not 0<=r<=1:raise ValueError('valid lognormal/reply values required')
    age=row['age_seconds']
    if age is not None:age=_finite(age,'current inferred head age',nonnegative=True)
    count=row['queue_count']
    if type(count) is not int or count<0:raise ValueError('nonnegative inferred queue count required')
    if count==0 and (q0!=0 or mR!=0 or row['head'] is not None or age is not None):
        raise ValueError('empty parent queue must have zero wait/residual and no inferred head')
    if count>0 and (row['head'] is None or age is None):raise ValueError('nonempty parent queue needs inferred head and age')
    if q0>0 and mR>0 and age is None:raise ValueError('positive head law needs its current inferred age')
    logden=None if age is None else _current_log_denominator(mu,sigma,r,age)
    expected=0. if age is None else residual.head_expected_remaining(mu,sigma,r,age)
    head_tolerance=HEAD_MEAN_NUMERIC_ULPS*max(math.ulp(mR),math.ulp(expected))
    if abs(mR-expected)>head_tolerance:
        raise ValueError('parent head mean differs from pinned current conditional residual law')
    if mR==0:F,p=q0,0.;branch='deterministic_zero_head'
    elif q0>=mR:F,p=q0-mR,1.;branch='shifted_full_head'
    else:F,p=0.,q0/mR;branch='thinned_head'
    reconstructed=F+p*mR
    mean_tolerance=HEAD_MEAN_NUMERIC_ULPS*max(math.ulp(q0),math.ulp(reconstructed))
    if abs(reconstructed-q0)>mean_tolerance:
        raise ArithmeticError('shifted/thinned mean preservation exceeds floating-point tolerance')
    return dict(schema_version=1,rule=BASE_RESIDUAL_RULE,decomposition_branch=branch,
        q0_seconds=q0,head_expected_remaining_seconds=mR,deterministic_follower_seconds=F,
        head_inclusion_probability=p,reconstructed_mean_seconds=reconstructed,
        recomputed_current_head_mean_seconds=expected,head_mean_numeric_tolerance_seconds=head_tolerance,
        mean_arithmetic_error_seconds=reconstructed-q0,current_age_seconds=age,
        current_log_no_reply_probability=logden,
        current_no_reply_probability=None if logden is None else math.exp(logden),
        service_mu=mu,service_sigma=sigma,reply_probability=r,
        residual_source_sha256=RESIDUAL_SOURCE_SHA256,
        mathematical_mean_identity='F+p*mR=q0',conditioning='current_no_reply_only')


def base_stoploss_at_elapsed(decomposition,elapsed_seconds):
    """E[(C0-s)+], retaining D(a), never conditioning on future silence."""
    s=_finite(elapsed_seconds,'elapsed seconds',nonnegative=True);d=decomposition
    q0=d['q0_seconds'];F=d['deterministic_follower_seconds'];p=d['head_inclusion_probability']
    age=d['current_age_seconds'];logden=d['current_log_no_reply_probability']
    shifted=max(0.,s-F);future_age=None if age is None else age+shifted
    logJ=None;log_error=None;conditional=None
    if q0==0:branch='zero';remaining=0.
    elif s==0:branch='origin';remaining=q0
    elif s<F:branch='before_shift';remaining=F-s+p*d['head_expected_remaining_seconds']
    elif p==0:branch='deterministic_cleared';remaining=0.
    else:
        branch='head_tail';logJ,log_error=_log_stoploss_arithmetic(d['service_mu'],d['service_sigma'],future_age)
        conditional=math.exp(logJ-logden);remaining=p*conditional
    # Bounds are mathematical identities for this law.  Permit only scalar
    # roundoff or the exposed log-subtraction sensitivity allowance.  This is
    # an engineering guard under declared ULP envelopes, not a libm interval
    # proof.  A materially invalid law must never be hidden by the clamp.
    raw=remaining;lower=max(q0-s,0.);remaining=min(q0,max(lower,raw))
    tolerance=BOUND_CORRECTION_ULPS*max(math.ulp(q0),math.ulp(raw),math.ulp(lower))
    if log_error is not None:
        if not math.isfinite(log_error) or log_error<0:
            raise ArithmeticError('stop-loss log arithmetic allowance is nonfinite or negative')
        try:tail_allowance=abs(raw)*math.expm1(log_error+BOUND_CORRECTION_ULPS*math.ulp(logden))
        except OverflowError as error:raise ArithmeticError('stop-loss arithmetic allowance exceeds finite range') from error
        tolerance=max(tolerance,tail_allowance)
    if not math.isfinite(raw) or not math.isfinite(tolerance) or abs(remaining-raw)>tolerance:
        raise ArithmeticError('stop-loss bound correction exceeds floating-point tolerance')
    return dict(schema_version=1,elapsed_seconds=s,branch=branch,residual_elapsed_seconds=shifted,
        stoploss_age_seconds=future_age,log_stoploss=logJ,log_stoploss_numeric_error_bound=log_error,
        fixed_log_no_reply_probability=logden,
        conditional_head_remaining_seconds=conditional,baseline_positive_part_seconds=lower,
        raw_remaining_seconds=raw,remaining_seconds=remaining,numeric_bound_correction_seconds=remaining-raw,
        numeric_bound_tolerance_seconds=tolerance)


def _key(value):return type(value).__name__,str(value)


def _future_cdf(job,x,wait_key):
    return job['reply_probability']*job['accept_probability']*H._lognorm_cdf(
        x-job['send_offset_seconds']-job[wait_key],job['service_mu'],job['service_sigma'])


def _jobs(plans):
    jobs=[]
    for pi,first in enumerate(plans):
        horizon=first['source_owner']['evaluation_end_at']-first['source_owner']['inputs']['now']
        records=first['continuation']['queue_records'];by={r['hospital']:(i,r) for i,r in enumerate(records)}
        for ci,plan in enumerate(first['continuation']['plan']):
            offset=plan['offset_seconds']
            if offset is None:continue
            if type(offset) is not int or not 0<=offset<horizon:raise ValueError('frozen future send exceeds public H')
            qi,row=by[plan['hospital']]
            decomposition=base_stoploss_decomposition(row)
            at_send=base_stoploss_at_elapsed(decomposition,offset)
            jobs.append(dict(patient_id=first['patient_id'],first_patient_index=pi,plan_candidate_index=ci,
                parent_queue_record_index=qi,hospital=plan['hospital'],send_offset_seconds=offset,
                owner_horizon_seconds=horizon,cell_edges_seconds=deepcopy(first['hypothetical_origin']['cell_edges_seconds']),
                base_wait_seconds=max(0.0,row['estimated_wait_seconds']),fixed_F0_wait_seconds=at_send['remaining_seconds'],
                base_stoploss_decomposition=decomposition,base_stoploss_at_send=at_send,
                service_mu=row['service_mu'],service_sigma=row['service_sigma'],
                reply_probability=row['reply_probability'],accept_probability=row['accept_probability'],
                mean_service_seconds=row['mean_service_seconds'],assumed_workers=row['assumed_workers'],
                full_mean_work_seconds=row['mean_service_seconds']/row['assumed_workers'],
                travel_seconds=row['travel_seconds'],inquiry_cost_seconds=row['inquiry_cost_seconds'],
                source_plan=deepcopy(plan),source_queue_record=deepcopy(row)))
    jobs.sort(key=lambda j:(j['send_offset_seconds'],j['first_patient_index'],j['plan_candidate_index']))
    for index,job in enumerate(jobs):job['future_job_index']=index
    return jobs


def _forward(jobs,plans):
    """Advance prior future means; fixed stoploss F0 never uses corrected CDFs."""
    result=deepcopy(jobs);completion={}
    for job in result:
        send=job['send_offset_seconds'];prior=completion.get(job['hospital'],0.0)
        base_remaining=job['base_stoploss_at_send']['remaining_seconds']
        prior_remaining=max(0.0,prior-send)
        q1=max(base_remaining,prior_remaining,0.0)
        probe=min(send+q1,job['owner_horizon_seconds'])
        survival=1.0;factors=[]
        first=plans[job['first_patient_index']]
        for index,item in enumerate(first['hypothetical_origin']['own_items']):
            if item['hospital']==job['hospital']:continue
            probability=original.original._descriptor_cdf(item,probe)
            survival*=max(0.0,1.0-probability)
            factors.append(dict(kind='initial',initial_item_index=index,hospital=item['hospital'],
                selection_probability_at_probe=probability))
        for other in jobs:
            if _key(other['patient_id'])==_key(job['patient_id']) and other['hospital']!=job['hospital']:
                probability=_future_cdf(other,probe,'fixed_F0_wait_seconds')
                survival*=max(0.0,1.0-probability)
                factors.append(dict(kind='future',future_job_index=other['future_job_index'],hospital=other['hospital'],
                    selection_probability_at_probe=probability))
        expected=job['full_mean_work_seconds']*survival
        after=send+q1+expected
        job.update(elapsed_seconds=send,base_remaining_seconds=base_remaining,
            prior_future_completion_seconds=prior,prior_future_remaining_seconds=prior_remaining,
            corrected_wait_seconds=q1,probe_offset_seconds=probe,fixed_F0_survival_factors=factors,
            no_prior_accept_probability=survival,expected_work_seconds=expected,future_completion_after_seconds=after)
        completion[job['hospital']]=after
    return result


def score_first_cohort(plans):
    """Re-score the frozen plans, without adding or moving a single contact."""
    if type(plans) is not list:raise ValueError('a complete frozen first-plan list is required')
    if len({_key(p['patient_id']) for p in plans})!=len(plans):raise ValueError('duplicate frozen first patient')
    jobs=_forward(_jobs(plans),plans);patients=[]
    for pi,parent in enumerate(plans):
        own=sorted((j for j in jobs if j['first_patient_index']==pi),key=lambda j:j['plan_candidate_index'])
        initial=deepcopy(parent['hypothetical_origin']['own_items']);edges=deepcopy(parent['hypothetical_origin']['cell_edges_seconds'])
        future=[]
        for job in own:
            masses=H.masses_at_edges(lambda x,job=job:_future_cdf(job,x,'corrected_wait_seconds'),1.0,edges)
            future.append(dict(future_job_index=job['future_job_index'],hospital=job['hospital'],travel_seconds=job['travel_seconds'],
                send_offset_seconds=job['send_offset_seconds'],wait_seconds=job['corrected_wait_seconds'],
                service_mu=job['service_mu'],service_sigma=job['service_sigma'],
                total_probability=job['reply_probability']*job['accept_probability'],masses=masses))
        cost=0.0
        for job in own:
            send=job['send_offset_seconds'];survival=1.0
            for item in initial:survival*=max(0.0,1.0-original.original._descriptor_cdf(item,send))
            for other in own:survival*=max(0.0,1.0-_future_cdf(other,send,'corrected_wait_seconds'))
            cost+=job['inquiry_cost_seconds']*survival
        items=[(x['travel_seconds'],x['masses']) for x in initial+future]
        metrics=original.original._metrics(items,edges,cost)
        metrics['immediate_contact_cost_seconds']=parent['initial_contact_cost_seconds']
        metrics['total_cost_seconds']+=parent['initial_contact_cost_seconds']
        patients.append(dict(patient_id=parent['patient_id'],effective_priority=parent['effective_priority'],
            parent_first_plan_index=pi,job_indices=[j['future_job_index'] for j in own],
            fixed_initial_items=initial,cell_edges_seconds=edges,future_items=future,
            initial_contact_cost_seconds=parent['initial_contact_cost_seconds'],future_contact_cost_seconds=cost,metrics=metrics))
    return dict(schema_version=1,rule=RULE,scope=SCOPE,jobs=jobs,patient_scores=patients)
