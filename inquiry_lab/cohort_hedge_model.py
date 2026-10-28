"""Origin-clock H-derived owner-local plans; no future events or reservations.

Original own reply distributions are fixed before any candidate's virtual sends.
Prospective queues use the candidate's final same-clock backlog without aging it.
Every planned wave respects four fresh unresolved jobs on the stated no-terminal-
reply occupancy branch. Other owners' speculative plans are never appended.
"""
from copy import deepcopy
import math
from server import inquiry_policies
from . import horizon_policy as H

NAME='eat_cohort_hedge_l15_f'
RULE='fixed_original_own_CDF_and_static_row_backlog_barrier_H_plan_with_every_wave_cap'
SCOPE='origin-clock static conditional surrogate; no future replies, timeout observations, queue aging, other-owner future contention or actual future H agreement'


def _descriptor_cdf(item,x):
    if x<=0:return 0.0
    if item['mode']=='queued':
        cdf=H._lognorm_cdf(x-item['wait_seconds'],item['service_mu'],item['service_sigma'])
    else:
        cdf=(H._lognorm_cdf(item['spent_seconds']+x,item['service_mu'],item['service_sigma'])-item['f0'])/max(1e-9,1-item['f0'])
    return min(1.0,max(0.0,item['total_probability']*cdf))


def freeze_owner(owner):
    """One public original-prefix own CDF computation, shared by all menu rows."""
    inputs=owner['inputs'];now=inputs['now'];end=owner['evaluation_end_at']
    if type(now) is not int or type(end) is not int or not 0<=now<end:raise ValueError('positive owner horizon required')
    config=H.spec()['config'];history=inquiry_policies._observations(inputs['observations'],now,config['history_window_seconds'])
    pending=inquiry_policies._pending(inputs['pending'],now)
    own=inquiry_policies._pending(inputs['patient_pending'],now,own=True)
    pid=owner['patient']['id'];keys={(q['hospital'],type(q['patient']),q['patient'],q['submitted_at']) for q in pending}
    if any(type(q['patient']) is not type(pid) or q['patient']!=pid or (q['hospital'],type(q['patient']),q['patient'],q['submitted_at']) not in keys for q in own):raise ValueError('owner pending source differs')
    beliefs=H.Beliefs(history,now,config);edges=H.cell_edges(end-now,config['grid_seconds']);items=[]
    for q in own:
        hospital=q['hospital'];submitted=q['submitted_at'];mu=beliefs.mu(hospital);sigma=beliefs.sigma
        reply=beliefs.reply(hospital);accept=beliefs.accept(hospital)
        ahead=sum(1.0 if other['submitted_at']<submitted else .5 for other in pending
            if other['hospital']==hospital and other['submitted_at']<=submitted and other['patient']!=pid)
        if ahead>0:
            mode='queued';wait=ahead*beliefs.mean_service(hospital)/config['assumed_workers'];spent=f0=0.0;total=reply*accept
        else:
            freed=[o['observed_at'] for o in history if o['hospital']==hospital and o['outcome']!='timeout' and o['submitted_at']<=submitted]
            spent=max(0.0,now-max([submitted,*freed]));f0=H._lognorm_cdf(spent,mu,sigma)
            alive=max(1e-9,1-f0);total=reply*alive/max(1e-9,1-reply*f0)*accept;mode='residual';wait=0.0
        item=dict(hospital=hospital,travel_seconds=q['travel_seconds'],service_mu=mu,service_sigma=sigma,
            total_probability=total,mode=mode,wait_seconds=wait,spent_seconds=spent,f0=f0)
        item['masses']=H.masses_at_edges(lambda x,item=item:_descriptor_cdf(item,x),1.0,edges);items.append(item)
    all_own=[deepcopy(q) for q in pending if type(q['patient']) is type(pid) and q['patient']==pid]
    return dict(schema_version=1,patient_id=pid,now=now,evaluation_end_at=end,cell_edges_seconds=edges,
        original_pending=deepcopy(inputs['pending']),normalized_history=history,original_all_own=all_own,own_items=items)


def planning_reason(owner):
    if owner['valid_acceptance_ids']:return 'valid_acceptance'
    job=owner['job']
    if job is None:return 'no_job'
    if not job['enabled']:return 'disabled_job'
    if job['policy_name']!=NAME:return 'unsupported_policy'
    if min(job['deadline_at'],owner['evaluation_end_at'])<=owner['inputs']['now']:return 'closed_new_contact_window'
    if owner['inputs']['max_new']==0:return 'contact_budget_exhausted'
    return 'plan'


def plan_occupancy(plan,all_own,now,timeout=180):
    """Conservative original + every earlier same-plan contact at every wave."""
    times=sorted({s for s in plan.values() if s is not None});rows=[]
    for s in times:
        original=sum(0<=now+s-q['submitted_at']<=timeout for q in all_own)
        planned=sum(t is not None and t<=s and s-t<=timeout for t in plan.values())
        rows.append(dict(offset_seconds=s,original_fresh_count=original,planned_fresh_count=planned,total_fresh_count=original+planned))
    return rows


def _metrics(items,edges,contact_cost):
    fail=1.0
    for _,mass in items:fail*=max(0.0,1.0-sum(mass))
    wait=H.bounded_expected_cost([(0,mass) for _,mass in items],edges,0)
    arrival=H.bounded_expected_cost(items,edges,H.spec()['config']['failure_penalty_seconds'])
    return dict(selection_probability=1-fail,failure_probability=fail,capped_wait_seconds=wait,
        arrival_failure_cost_seconds=arrival,planned_contact_cost_seconds=contact_cost,total_cost_seconds=arrival+contact_cost)


def score_owner(owner,frozen,row_pending):
    """Choose the fixed two-pass H-derived plan and score its original-time CDF."""
    inputs=owner['inputs'];now=inputs['now'];pid=owner['patient']['id'];config=H.spec()['config']
    if frozen['patient_id']!=pid or frozen['now']!=now or frozen['evaluation_end_at']!=owner['evaluation_end_at'] or frozen['original_pending']!=inputs['pending']:raise ValueError('fixed owner origin differs')
    if type(row_pending) is not list or row_pending[:len(inputs['pending'])]!=inputs['pending'] or any(q['submitted_at']!=now for q in row_pending[len(inputs['pending']):]):raise ValueError('row backlog must append current virtual sends only')
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
            record=deepcopy(record);record.update(travel_seconds=q['travel_seconds'],accept_probability=beliefs.accept(h),
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
    return dict(schema_version=1,rule=RULE,scope=SCOPE,patient_id=pid,effective_priority=owner['patient']['effective_priority'],
        planning_status=reason,barrier_offset_seconds=delta,allowed_offsets_seconds=offsets,
        queue_records=records,plan=[dict(hospital=q['hospital'],offset_seconds=plan[q['hospital']]) for q in pool],
        occupancy=plan_occupancy(plan,frozen['original_all_own'],now,config['observation_timeout_seconds']),
        passes=passes,forced=forced,metrics=metrics)
