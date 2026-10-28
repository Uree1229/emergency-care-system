"""First-contact initial CDFs plus separate hypothetical static H continuations.

The committed public owner is never changed.  Virtual initial work and its
pass1 CDF are the frozen Recourse row; future contacts are local plans, not
promises, reservations, extra actual actions or a forecast of future replies.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from . import cohort_recourse_model as original

NAME='eat_cohort_followplan_l15_f'
BASE_SOURCE_SHA256='e776bd643611a9ce33cbfa3fdbd8f05042fae755c20be5e0a1f4bddfdcebc2de'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen first-followplan model foundation differs')
H=original.H
TIERS=(4,2,1)
RULE='initial_pass1_CDF_plus_separate_static_next_review_H_continuation'
SCOPE='hypothetical origin-clock first-contact continuation; original public input and fixed F0 work unchanged; conservative no-terminal-reply occupancy, no future-event input, interowner future contention or later-action guarantee'


def score_first_patient(owner,*,initial_jobs,row_pending,all_jobs,next_review_at):
    """Score one fully disclosed hypothetical continuation, never a DB snapshot."""
    source=deepcopy(owner);pid=source['patient']['id'];now=source['inputs']['now']
    if type(next_review_at) is not int or next_review_at<now:
        raise ValueError('a public initial proposal next-review clock is required')
    if source.get('inquiries') or source['inputs']['patient_pending'] or source['valid_acceptance_ids']:
        raise ValueError('first-contact continuation cannot replace an existing inquiry')
    if source['job'] is None or source['job']['policy_name']!=original.foundation.NAME:
        raise ValueError('first-contact hypothetical owner must use the frozen H calculation identity')
    origin=original.freeze_owner(source)
    if origin['original_all_own'] or origin['own_items']:
        raise ValueError('first-contact continuation has original own pending work')
    if row_pending!=source['inputs']['pending']+[j['source']['inquiry'] for j in all_jobs]:
        raise ValueError('hypothetical continuation row ledger differs')
    wanted=[j for j in all_jobs if type(j['patient_id']) is type(pid) and j['patient_id']==pid]
    if initial_jobs!=wanted:
        raise ValueError('every initial own job must come from the complete fixed row')
    hospitals=[j['hospital'] for j in initial_jobs]
    if len(set(hospitals))!=len(hospitals) or len(hospitals)>source['inputs']['max_new']:
        raise ValueError('hypothetical initial contact budget differs')
    if any(j['source']['inquiry']!=dict(hospital=j['hospital'],patient=pid,submitted_at=now) for j in initial_jobs):
        raise ValueError('hypothetical initial work clock or owner differs')
    hypothesis=deepcopy(source)
    hypothesis['inputs']['candidates']=[deepcopy(c) for c in source['inputs']['candidates'] if c['hospital_id'] not in hospitals]
    hypothesis['inputs']['max_new']=source['inputs']['max_new']-len(initial_jobs)
    hypothesis['inputs']['last_contact_at']=now if initial_jobs else source['inputs']['last_contact_at']
    hypothesis['job']['next_evaluation_at']=next_review_at
    frozen=deepcopy(origin);frozen['original_all_own']=[deepcopy(j['source']['inquiry']) for j in initial_jobs]
    items=[]
    for job in initial_jobs:
        if job['cell_edges_seconds']!=origin['cell_edges_seconds'] or job['owner_horizon_seconds']!=source['evaluation_end_at']-now:
            raise ValueError('initial own CDF horizon differs from public H')
        item=dict(hospital=job['hospital'],travel_seconds=job['travel_seconds'],service_mu=job['service_mu'],
            service_sigma=job['service_sigma'],total_probability=job['reply_probability']*job['accept_probability'],
            mode='queued',wait_seconds=job['pass1_wait_seconds'],spent_seconds=0.0,f0=0.0)
        item['masses']=H.masses_at_edges(lambda x,item=item:original._descriptor_cdf(item,x),1.0,origin['cell_edges_seconds'])
        items.append(item)
    frozen['own_items']=items
    # original_pending stays the committed P0.  Only the explicitly hypothetical
    # original_all_own/own_items describe this row's initial virtual contacts.
    continuation=original.score_owner(hypothesis,frozen,row_pending,all_jobs)
    initial_cost=sum((j['inquiry_cost_seconds'] for j in initial_jobs),0.0)
    metrics=deepcopy(continuation['metrics'])
    metrics['immediate_contact_cost_seconds']=initial_cost
    metrics['total_cost_seconds']+=initial_cost
    return dict(schema_version=1,rule=RULE,scope=SCOPE,patient_id=pid,effective_priority=source['patient']['effective_priority'],
        source_owner=source,initial_job_indices=[j['job_index'] for j in initial_jobs],next_review_at=next_review_at,
        source_origin=origin,hypothetical_owner=hypothesis,hypothetical_origin=frozen,
        initial_contact_cost_seconds=initial_cost,continuation=continuation,metrics=metrics)


def first_review_sources(first_D_row,*,members,current_baseline_proposals):
    """Recorded public next-review sources, in the unchanged cohort order."""
    result=[]
    def add(people,proposals,kind,step):
        for index,(member,proposal) in enumerate(zip(people,proposals)):
            result.append(dict(patient_id=member['patient_id'],kind=kind,step_index=step,member_index=index,
                next_review_at=proposal['next_review_at']))
    add(members,current_baseline_proposals,'current_pair_H_baseline',None)
    projection=first_D_row['projection']
    for step in projection['steps']:
        add(step['members'],step['proposal']['decisions'],'reference_CS_decision',step['step_index'])
    if projection['singleton'] is not None:
        item=projection['singleton'];add([item['member']],[item['proposal']],'reference_H_singleton',len(projection['steps']))
    return result
