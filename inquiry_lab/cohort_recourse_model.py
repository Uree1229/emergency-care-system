"""One forward expected-work pass over unchanged virtual first-cohort sends.

F0 always comes from the uncancelled reference queue. Only preceding virtual
work is discounted; actual pending and fixed original-own CDFs stay intact.
This is a scoring approximation, not fractional execution capacity.
"""
from copy import deepcopy
import math
from server import inquiry_policies
from . import cohort_hedge_model as foundation,cohort_dynamic_policy as D

NAME='eat_cohort_recourse_l15_f'
H=foundation.H
JOINT=D.JOINT
TIERS=(4,2,1)
RULE='fixed_F0_forward_one_pass_virtual_expected_cancellation_workahead'
OWNER_RULE='fixed_original_own_CDF_static_H_plan_with_expected_virtual_workahead'
SCOPE='same-clock scoring surrogate; unchanged reference sends, fixed F0, one forward pass; no actual cancellation prediction guarantee, fractional occupancy, future replies or policy rollout'
freeze_owner=foundation.freeze_owner
planning_reason=foundation.planning_reason
plan_occupancy=foundation.plan_occupancy
_descriptor_cdf=foundation._descriptor_cdf
_metrics=foundation._metrics


def _f0(job,t):
    return job['reply_probability']*job['accept_probability']*H._lognorm_cdf(
        t-job['pass0_wait_seconds'],job['service_mu'],job['service_sigma'])


def _passes(jobs):
    """Forward q1 first, then cancellation survival using fixed cross-hospital F0."""
    result=deepcopy(jobs);discounts={}
    for job in result:
        hospital=job['hospital'];discount=discounts.get(hospital,0.0)
        # This algebraic form preserves the frozen uncancelled float sequence.
        q1=max(0.0,job['pass0_wait_seconds']-discount)
        t=min(q1,job['owner_horizon_seconds']);survival=1.0;others=[]
        for other in jobs:
            if type(other['patient_id']) is type(job['patient_id']) and other['patient_id']==job['patient_id'] and other['hospital']!=hospital:
                probability=_f0(other,t);survival*=max(0.0,1.0-probability)
                others.append(dict(job_index=other['job_index'],selection_probability_at_probe=probability))
        weighted=job['full_mean_work_seconds']*survival
        removed=job['full_mean_work_seconds']-weighted
        job.update(prior_virtual_discount_seconds=discount,pass1_wait_seconds=q1,
            cancellation_probe_seconds=t,other_hospital_probabilities=others,
            survival_probability=survival,expected_work_seconds=weighted,discount_seconds=removed)
        discounts[hospital]=discount+removed
    return result


def score_first_cohort(first_D_row,*,members,current_baseline_proposals,priorities):
    """Re-score the exact D row's current, reference-CS and singleton sends."""
    projection=first_D_row['projection'];jobs=[];people=[]
    prefix=projection['initial_pending'];prior_full={}
    def add(people_in,proposals,sends,appends,kind,step_index):
        models=[JOINT._model(m['inputs'],p) for m,p in zip(people_in,proposals)]
        app_index=0;earlier={}
        for mi,(member,model,hospitals) in enumerate(zip(people_in,models,sends)):
            by={r['hospital']:r for r in model['hospitals']};indices=[]
            for si,hospital in enumerate(hospitals):
                row=by[hospital];extra=earlier.get(hospital,0.0)
                q0=row['residual_wait_seconds']+extra
                full=row['mean_service_seconds']/row['assumed_workers']
                source=deepcopy(appends[app_index]);app_index+=1
                expected=dict(hospital=hospital,patient=member['patient_id'],submitted_at=member['inputs']['now'])
                if source['inquiry']!=expected:raise ValueError('reference append order differs')
                base_inputs=deepcopy(member['inputs']);base_inputs['pending']=deepcopy(prefix)
                base=next(r for r in JOINT._model(base_inputs,proposals[mi])['hospitals'] if r['hospital']==hospital)
                jobs.append(dict(job_index=len(jobs),patient_id=member['patient_id'],hospital=hospital,
                    kind=kind,step_index=step_index,member_index=mi,send_index=si,source=source,
                    owner_horizon_seconds=model['horizon_seconds'],cell_edges_seconds=deepcopy(model['cell_edges_seconds']),
                    travel_seconds=row['travel_seconds'],service_mu=row['service_mu'],service_sigma=row['service_sigma'],
                    reply_probability=row['reply_probability'],accept_probability=row['accept_probability'],
                    mean_service_seconds=row['mean_service_seconds'],assumed_workers=row['assumed_workers'],
                    full_mean_work_seconds=full,base_residual_wait_seconds=base['residual_wait_seconds'],
                    prior_virtual_full_work_seconds=prior_full.get(hospital,0.0),pass0_wait_seconds=q0,
                    inquiry_cost_seconds=row['inquiry_cost_seconds']))
                indices.append(len(jobs)-1);prior_full[hospital]=prior_full.get(hospital,0.0)+full
            people.append(dict(member=deepcopy(member),model=model,selected=list(hospitals),job_indices=indices))
            for hospital in hospitals:earlier[hospital]=by[hospital]['mean_service_seconds']/by[hospital]['assumed_workers']
        if app_index!=len(appends):raise ValueError('extra reference appends')
    add(members,current_baseline_proposals,projection['current_pair_send'],projection['initial_appended_inquiries'],'current_pair_row',None)
    for step in projection['steps']:
        proposals=D._joint(step['proposal'])['evidence']['baseline_proposals']
        add(step['members'],proposals,[d['send'] for d in step['proposal']['decisions']],
            step['appended_inquiries'],'reference_CS_selected',step['step_index'])
    singleton=projection['singleton']
    if singleton is not None:add([singleton['member']],[singleton['proposal']],[singleton['proposal']['send']],
        singleton['appended_inquiries'],'reference_H_singleton',len(projection['steps']))
    if prefix+[j['source']['inquiry'] for j in jobs]!=projection['final_pending']:raise ValueError('complete flat virtual ledger differs')
    jobs=_passes(jobs);values=[]
    priority_by={(type(p['patient_id']),p['patient_id']):p['effective_priority'] for p in priorities['patients']}
    for person in people:
        model=deepcopy(person['model']);by={r['hospital']:r for r in model['hospitals']}
        for index in person['job_indices']:
            job=jobs[index];by[job['hospital']]['residual_wait_seconds']=job['pass1_wait_seconds']
        pid=person['member']['patient_id'];metrics=JOINT._metrics(model,person['selected'],{})
        values.append(dict(patient_id=pid,effective_priority=priority_by[type(pid),pid],metrics=metrics))
    return dict(schema_version=1,rule=RULE,scope=SCOPE,row_index=first_D_row['row_index'],jobs=jobs,
        member_metrics=values,tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in values if m['effective_priority']==t),0.0) for t in TIERS},
        sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in values),0.0))


def score_owner(owner,frozen,row_pending,jobs):
    """Choose the fixed two-pass H-derived plan and score its original-time CDF."""
    inputs=owner['inputs'];now=inputs['now'];pid=owner['patient']['id'];config=H.spec()['config']
    if frozen['patient_id']!=pid or frozen['now']!=now or frozen['evaluation_end_at']!=owner['evaluation_end_at'] or frozen['original_pending']!=inputs['pending']:raise ValueError('fixed owner origin differs')
    if type(row_pending) is not list or row_pending[:len(inputs['pending'])]!=inputs['pending'] or any(q['submitted_at']!=now for q in row_pending[len(inputs['pending']):]):raise ValueError('row backlog must append current virtual sends only')
    if row_pending[len(inputs['pending']):]!=[j['source']['inquiry'] for j in jobs]:raise ValueError('expected work source differs from row ledger')
    pending=inquiry_policies._pending(row_pending,now);edges=frozen['cell_edges_seconds'];reason=planning_reason(owner)
    current=[(x['travel_seconds'],x['masses']) for x in frozen['own_items']]
    rank=inquiry_policies._candidates(inputs['candidates'])
    if type(inputs['max_new']) is not int or inputs['max_new']<0:raise ValueError('nonnegative owner contact budget required')
    pool=rank[:min(config['max_candidates_evaluated'],inputs['max_new'])]
    if reason=='plan' and not pool:reason='no_candidates'
    job=owner['job'];delta=None;offsets=[];records=[];plan={q['hospital']:None for q in pool}
    passes=0;forced=False
    if reason=='plan':
        # The actual capture kernel ignores next_evaluation_at with no pending.
        barrier=max(now,job['started_at'],job['next_evaluation_at'] if frozen['original_all_own'] else now)
        delta=barrier-now;window=min(job['deadline_at'],owner['evaluation_end_at'])-now
        offsets=sorted({delta+s for s in config['plan_offsets_seconds'] if delta+s<window})
        if not offsets:reason='barrier_outside_new_contact_window'
    history=frozen['normalized_history'];beliefs=H.Beliefs(history,now,config);new={}
    if reason=='plan':
        for q in pool:
            h=q['hospital'];queue=[p for p in pending if p['hospital']==h]
            wait,record=H._queue_wait_estimate(h,now,queue,history,beliefs,config['assumed_workers'])
            discount=sum((j['discount_seconds'] for j in jobs if j['hospital']==h),0.0)
            record=deepcopy(record);record.update(full_wait_before_discount_seconds=wait,virtual_discount_seconds=discount,
                estimated_wait_seconds=max(0.0,wait-discount))
            record.update(travel_seconds=q['travel_seconds'],accept_probability=beliefs.accept(h),
                inquiry_cost_seconds=config['lambda_seconds']*(1+len(queue) if config['congestion_aware'] else 1))
            records.append(record);new[h]=record
    def new_cdf(h,s,x):
        row=new[h]
        return row['reply_probability']*row['accept_probability']*H._lognorm_cdf(x-s-row['estimated_wait_seconds'],row['service_mu'],row['service_sigma'])
    def make_items(p):
        return current+[(new[h]['travel_seconds'],H.masses_at_edges(lambda x,h=h,s=s:new_cdf(h,s,x),1.0,edges)) for h,s in p.items() if s is not None]
    def feasible(p):return all(x['total_fresh_count']<=config['max_concurrent'] for x in plan_occupancy(p,frozen['original_all_own'],now,config['observation_timeout_seconds']))
    def contact_cost(p):
        total=0.0
        for hospital,s in p.items():
            if s is None:continue
            survival=1.0
            for item in frozen['own_items']:survival*=max(0.0,1-_descriptor_cdf(item,s))
            for h,t in p.items():
                if t is not None:survival*=max(0.0,1-new_cdf(h,t,s))
            total+=new[hospital]['inquiry_cost_seconds']*survival
        return total
    def cost(p):return H.bounded_expected_cost(make_items(p),edges,config['failure_penalty_seconds'])+contact_cost(p)
    if reason=='plan':
        best=cost(plan)
        for _ in range(config['plan_passes']):
            passes+=1;improved=False
            for q in pool:
                h=q['hospital']
                for offset in [None]+offsets:
                    if offset==plan[h]:continue
                    trial=dict(plan);trial[h]=offset
                    if not feasible(trial):continue
                    value=cost(trial)
                    if value<best-1e-9:plan,best,improved=trial,value,True
            if not improved:break
        fresh=sum(0<=now+delta-q['submitted_at']<=config['observation_timeout_seconds'] for q in frozen['original_all_own'])
        if fresh==0 and not any(s==delta for s in plan.values()) and pool and delta in offsets:
            options=[]
            for q in pool:
                trial=dict(plan);trial[q['hospital']]=delta
                if feasible(trial):options.append((cost(trial),q['travel_seconds'],trial))
            if options:plan=min(options,key=lambda x:(x[0],x[1]))[2];forced=True
        metrics=_metrics(make_items(plan),edges,contact_cost(plan))
    elif reason=='valid_acceptance':
        metrics=dict(selection_probability=1.0,failure_probability=0.0,capped_wait_seconds=0.0,
            arrival_failure_cost_seconds=0.0,planned_contact_cost_seconds=0.0,total_cost_seconds=0.0)
    else:metrics=_metrics(current,edges,0.0)
    return dict(schema_version=1,rule=OWNER_RULE,scope=SCOPE,patient_id=pid,effective_priority=owner['patient']['effective_priority'],
        planning_status=reason,barrier_offset_seconds=delta,allowed_offsets_seconds=offsets,
        queue_records=records,plan=[dict(hospital=q['hospital'],offset_seconds=plan[q['hospital']]) for q in pool],
        occupancy=plan_occupancy(plan,frozen['original_all_own'],now,config['observation_timeout_seconds']),
        passes=passes,forced=forced,metrics=metrics)
