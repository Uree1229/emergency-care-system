"""Chronological mean queue progression for frozen first-patient H plans.

Initial pass1 CDFs and every planned contact/time stay fixed.  Baseline queue
work drains by elapsed time; each earlier future job contributes its expected
completion time.  This deterministic mean approximation is not the exact
expectation of stochastic remaining work (positive-part/Jensen bias remains).
No observed terminal event, reservation or actual capacity release is invented.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_followplan_model as original

NAME='eat_cohort_advance_l15_f'
BASE_SOURCE_SHA256='8f8dfc82451b05a804d33253823bde3a6cbb68ff67728d1458dbb17aca0d6268'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen first-contention model foundation differs')
H=original.H
TIERS=(4,2,1)
RULE='frozen_first_future_plans_aged_F0_chronological_mean_workload_progression'
SCOPE='fixed initial CDFs and full parent future plans; chronological mean baseline/prior-completion maximum, fixed aged F0 and one pass; no replanning, F1 feedback, observed future events or actual capacity release; deterministic mean positive-part is not exact stochastic residual expectation and may understate it by Jensen'


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
            jobs.append(dict(patient_id=first['patient_id'],first_patient_index=pi,plan_candidate_index=ci,
                parent_queue_record_index=qi,hospital=plan['hospital'],send_offset_seconds=offset,
                owner_horizon_seconds=horizon,cell_edges_seconds=deepcopy(first['hypothetical_origin']['cell_edges_seconds']),
                base_wait_seconds=row['estimated_wait_seconds'],fixed_F0_wait_seconds=max(0.0,row['estimated_wait_seconds']-offset),
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
    """Advance means by elapsed time; fixed aged F0 never uses corrected CDFs."""
    result=deepcopy(jobs);completion={}
    for job in result:
        send=job['send_offset_seconds'];prior=completion.get(job['hospital'],0.0)
        base_remaining=max(0.0,job['base_wait_seconds']-send)
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
