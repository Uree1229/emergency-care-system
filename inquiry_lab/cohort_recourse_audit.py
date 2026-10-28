"""Independent audit of one-pass expected work for virtual first contacts.

Only SHA-bound independent auditors and the standard library are imported.
The original Hedge proposal, public capture and actual FIFO remain separately
checked. Actual pending work and each owner's original reply CDF are immutable.
"""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from . import cohort_hedge_numeric_audit as numeric

base=numeric.frozen
history=numeric.history
dynamic=base.base
ROOT=Path(__file__).resolve().parents[1]
for module,digest in ((numeric,'cba8091a9750c6b87f9132add28fa7f6c4dc4b554282b42c91c88196f83c36b0'),
        (history,'064d6069488887e5366c7b175570550c40b9c5d3072d8c8a50d84b5a61e83cc3'),
        (base,'43f4a13bb737311e7e5468595698955d77612d13856cdd213520da0efb74516f')):
    if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()!=digest:
        raise ValueError('frozen recourse audit dependency differs: '+module.__name__)
NAME='eat_cohort_recourse_l15_f'
BASE_POLICY=base.NAME
BASE_SOURCE_SHA256='0b820192c851e192768179b9ed097db826013570c079a8edc1397d4ea835fa65'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_hedge_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Hedge source differs')
parent,horizon,joint=base.parent,base.horizon,base.joint
require,precise_same,exact_copy=base.require,base.precise_same,base.exact_copy
_pid,_integer,EPOCH=base._pid,base._integer,base.EPOCH
INPUT_KEYS,CALL_KEYS,GROUP_KEYS=base.INPUT_KEYS,base.CALL_KEYS,base.GROUP_KEYS
NEW_INSTRUMENTATION=dict(base.NEW_INSTRUMENTATION,actual_workflow='inquiry_lab.cohort_recourse_runner.run_once',
    first_contact_virtual_queued_work_cancellation_model=True,
    existing_pending_and_integer_concurrency_unchanged=True)


def _project_name(name):
    if name==NAME:return BASE_POLICY
    return '__foreign_cohort_hedge__' if name==BASE_POLICY else name


def alias_context(context):
    result=deepcopy(context)
    for row in result['eligible']:
        require(row['job']['policy_name']==NAME,'current eligible job has wrong native policy')
        row['job']['policy_name']=BASE_POLICY
    return result


def alias_member(member):
    result=deepcopy(member)
    require(result['workflow_snapshot']['job']['policy_name']==NAME,'current member has wrong native policy')
    result['workflow_snapshot']['job']['policy_name']=BASE_POLICY
    return result


def alias_owners(owners):
    result=deepcopy(owners)
    for owner in result['inventory']:
        if owner['job'] is not None:owner['job']['policy_name']=_project_name(owner['job']['policy_name'])
    return result


def alias_events(events):
    result=deepcopy(events)
    for event in result:
        if event['type'] in ('inquiry_workflow_started','inquiry_workflow_paused','inquiry_policy_changed'):
            data=parent._data(event)
            if 'policy_name' in data:event['data']=dict(data,policy_name=_project_name(data['policy_name']))
    return result


def alias_registry(registry):
    result=deepcopy(registry)
    for job in result['jobs']:job['policy_name']=_project_name(job['policy_name'])
    return result


def alias_source(source):
    result=deepcopy(source);result['events']=alias_events(result['events'])
    for row in result['rows']:
        if row['job'] is not None:row['job']['policy_name']=_project_name(row['job']['policy_name'])
    return result


@contextmanager
def inherited_empty_scalars():
    """Use the sealed integer-to-float correction, restoring on every exit."""
    previous=base.owner_metrics
    require(previous is numeric._ORIGINAL_OWNER_METRICS,'original Hedge metric hook already replaced')
    base.owner_metrics=numeric.corrected_owner_metrics
    try:yield
    finally:base.owner_metrics=previous


def audit_registry(events,decisions,registry,now,event_prefix,decision_prefix):
    context=base.audit_registry(alias_events(events),decisions,alias_registry(registry),now,event_prefix,decision_prefix)
    for row in context['eligible']:row['job']['policy_name']=NAME
    return context


def audit_priority_context(priority,context,members,tails,*,lab_defaults=False):
    return base.audit_priority_context(priority,alias_context(context),[alias_member(m) for m in members],
        [alias_member(m) for m in tails],lab_defaults=lab_defaults)


def audit_time_context(events,inputs,member,event_prefix,decision_prefix,decision=None):
    return base.audit_time_context(alias_events(events),inputs,alias_member(member),event_prefix,decision_prefix,decision)


def audit_owner_provenance(scenario,events,registry,owners,source):
    e,r,o,s=alias_events(events),alias_registry(registry),alias_owners(owners),alias_source(source)
    proof=history.audit_history_provenance(scenario,e,r,o,s)
    checked=history.corrected_audit_owner_provenance(scenario,e,r,o)
    return checked,proof


def audit_member_provenance(scenario,events,member,event_prefix,decision_prefix):
    return base.audit_member_provenance(scenario,alias_events(events),alias_member(member),event_prefix,decision_prefix)


MODEL_RULE='fixed_original_own_CDF_static_H_plan_with_expected_virtual_workahead'
FIRST_RULE='fixed_F0_forward_one_pass_virtual_expected_cancellation_workahead'
MODEL_SCOPE='same-clock scoring surrogate; unchanged reference sends, fixed F0, one forward pass; no actual cancellation prediction guarantee, fractional occupancy, future replies or policy rollout'


def fixed_F0(job,t):
    return job['reply_probability']*job['accept_probability']*parent._cdf(
        t-job['pass0_wait_seconds'],job['service_mu'],job['service_sigma'])


def expected_virtual_work(jobs):
    """Independent fixed-F0 forward recurrence, with no F1 feedback."""
    result=[];removed_by_hospital={}
    for index,item in enumerate(jobs):
        require(type(item['job_index']) is int and item['job_index']==index,'virtual flat job order differs')
        full=item['full_mean_work_seconds'];q0=item['pass0_wait_seconds'];base_wait=item['base_residual_wait_seconds']
        require(all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in (full,q0,base_wait)),
            'virtual job has an invalid full-work or wait parameter')
        require(item['assumed_workers']==1 and type(item['assumed_workers']) is int,'fractional or hidden worker capacity')
        previous=removed_by_hospital.get(item['hospital'],0.)
        q1=max(0.,q0-previous)
        # All the removed work must come from earlier virtual jobs; original
        # pending service remains a lower bound even when survival is zero.
        require(q1>=base_wait-1e-10,'discount reached original actual pending work')
        probe=min(q1,item['owner_horizon_seconds']);survival=1.;others=[]
        for other in jobs:
            if base.owner_key(other['patient_id'])!=base.owner_key(item['patient_id']) or other['hospital']==item['hospital']:continue
            probability=fixed_F0(other,probe)
            require(0<=probability<=1,'invalid fixed original selection probability')
            survival*=max(0.,1-probability)
            others.append(dict(job_index=other['job_index'],selection_probability_at_probe=probability))
        work=full*survival;removed=full-work
        result.append(dict(item,prior_virtual_discount_seconds=previous,pass1_wait_seconds=q1,
            cancellation_probe_seconds=probe,other_hospital_probabilities=others,
            survival_probability=survival,expected_work_seconds=work,discount_seconds=removed))
        removed_by_hospital[item['hospital']]=previous+removed
    return result


def first_cohort_score(first,members,priority):
    """Flatten the already-audited immutable D sends and rebuild every CDF."""
    projection=first['projection'];prefix=projection['initial_pending'];jobs=[];people=[];prior_full={}
    def add(group,sends,appends,kind,step):
        require(type(appends) is list and len(appends)==sum(len(s) for s in sends),'flat source appends omitted or added')
        models=[base.public_model(m['inputs']) for m in group];within={};append_position=0
        for member_index,(member,model,send) in enumerate(zip(group,models,sends)):
            rows={r['hospital']:r for r in model['hospitals']};indices=[]
            original_inputs=deepcopy(member['inputs']);original_inputs['pending']=deepcopy(prefix)
            original_rows={r['hospital']:r for r in base.public_model(original_inputs)['hospitals']}
            require(type(send) is list and len(send)==len(set(send)) and set(send)<=set(rows),'flat source sent hidden/unavailable hospital')
            for send_index,hid in enumerate(send):
                row=rows[hid];origin=original_rows[hid];full=row['mean_service_seconds']/row['assumed_workers']
                q0=row['residual_wait_seconds']+within.get(hid,0.)
                require(math.isclose(q0,origin['residual_wait_seconds']+prior_full.get(hid,0.),rel_tol=0,abs_tol=1e-10),
                    'original R plus preceding full virtual work differs')
                source=deepcopy(appends[append_position]);append_position+=1
                exact_copy(source['inquiry'],dict(hospital=hid,patient=member['patient_id'],submitted_at=member['inputs']['now']),
                    'flat immutable public append identity')
                jobs.append(dict(job_index=len(jobs),patient_id=member['patient_id'],hospital=hid,
                    kind=kind,step_index=step,member_index=member_index,send_index=send_index,source=source,
                    owner_horizon_seconds=model['horizon_seconds'],cell_edges_seconds=deepcopy(model['cell_edges_seconds']),
                    travel_seconds=row['travel_seconds'],service_mu=row['service_mu'],service_sigma=row['service_sigma'],
                    reply_probability=row['reply_probability'],accept_probability=row['accept_probability'],
                    mean_service_seconds=row['mean_service_seconds'],assumed_workers=row['assumed_workers'],
                    full_mean_work_seconds=full,base_residual_wait_seconds=origin['residual_wait_seconds'],
                    prior_virtual_full_work_seconds=prior_full.get(hid,0.),pass0_wait_seconds=q0,
                    inquiry_cost_seconds=row['inquiry_cost_seconds']))
                indices.append(len(jobs)-1);prior_full[hid]=prior_full.get(hid,0.)+full
            people.append((member,model,list(send),indices))
            for hid in send:within[hid]=rows[hid]['mean_service_seconds']/rows[hid]['assumed_workers']
    add(members,projection['current_pair_send'],projection['initial_appended_inquiries'],'current_pair_row',None)
    for step in projection['steps']:
        add(step['members'],[d['send'] for d in step['proposal']['decisions']],step['appended_inquiries'],
            'reference_CS_selected',step['step_index'])
    if projection['singleton'] is not None:
        singleton=projection['singleton']
        add([singleton['member']],[singleton['proposal']['send']],singleton['appended_inquiries'],
            'reference_H_singleton',len(projection['steps']))
    exact_copy(prefix+[j['source']['inquiry'] for j in jobs],projection['final_pending'],'complete flat D query ledger')
    require(len({(base.owner_key(j['patient_id']),j['hospital']) for j in jobs})==len(jobs),'virtual owner/hospital repeated')
    jobs=expected_virtual_work(jobs);weights={base.owner_key(p['patient_id']):p['effective_priority'] for p in priority['patients']};values=[]
    for member,model,sends,indices in people:
        adjusted=deepcopy(model);by_hospital={r['hospital']:r for r in adjusted['hospitals']}
        for index in indices:by_hospital[jobs[index]['hospital']]['residual_wait_seconds']=jobs[index]['pass1_wait_seconds']
        # This reconstructs the six current-only metrics from analytic shifted
        # distributions; no recorded D/Hedge metrics participate in the score.
        metric=joint.member_metrics(adjusted,sends,{})
        if not sends:metric=dict(metric,selection_probability=float(metric['selection_probability']),failure_probability=float(metric['failure_probability']))
        values.append(dict(patient_id=member['patient_id'],effective_priority=weights[base.owner_key(member['patient_id'])],metrics=metric))
    return dict(schema_version=1,rule=FIRST_RULE,scope=MODEL_SCOPE,row_index=first['row_index'],jobs=jobs,
        member_metrics=values,tier_probabilities={str(t):sum((v['metrics']['selection_probability'] for v in values if v['effective_priority']==t),0.) for t in (4,2,1)},
        sum_capped_wait_seconds=sum((v['metrics']['capped_wait_seconds'] for v in values),0.))


def owner_score(owner,origin,row_pending,discount_by_hospital):
    """Independent old H-derived search with virtual-only queue work removed.

    Its count occupancy, old CDF, exact contact survival and two-pass grid stay
    unchanged. Discounts apply only to the future-query R wait, never a count.
    """
    inputs=owner['inputs'];now=inputs['now'];end=owner['evaluation_end_at'];pid=owner['patient']['id']
    require(base.owner_key(origin['patient_id'])==base.owner_key(pid) and origin['now']==now
        and origin['evaluation_end_at']==end,'fixed original owner identity or H differs')
    exact_copy(origin['original_pending'],inputs['pending'],'immutable original own CDF prefix')
    require(type(row_pending) is list and row_pending[:len(inputs['pending'])]==inputs['pending']
        and all(q['submitted_at']==now for q in row_pending[len(inputs['pending']):]),'row altered original or appended future jobs')
    old=[(item['travel_seconds'],item['masses']) for item in origin['own_items']];job=owner['job']
    if owner['valid_acceptance_ids']:reason='valid_acceptance'
    elif job is None:reason='no_job'
    elif not job['enabled']:reason='disabled_job'
    elif job['policy_name']!=NAME:reason='unsupported_policy'
    elif min(job['deadline_at'],end)<=now:reason='closed_new_contact_window'
    elif inputs['max_new']==0:reason='contact_budget_exhausted'
    else:reason='plan'
    pool=horizon._ranked(inputs['candidates'])[:min(6,inputs['max_new'])]
    if reason=='plan' and not pool:reason='no_candidates'
    bins=horizon.intervals(end-now);plan={q['hospital']:None for q in pool};delta=None;offsets=[];records=[]
    passes=0;forced=False
    if reason=='plan':
        barrier=max(now,job['started_at'],job['next_evaluation_at'] if origin['original_all_own'] else now)
        delta=barrier-now;window=min(job['deadline_at'],end)-now
        offsets=sorted({delta+s for s in (0,15,30,60,90,120,180) if delta+s<window})
        if not offsets:reason='barrier_outside_new_contact_window'
    if reason=='plan':
        rows=horizon.residual.residual_rows(dict(inputs,pending=row_pending));beliefs=parent._beliefs(origin['normalized_history'],now)
        for candidate,row in zip(pool,rows):
            if row['age_seconds'] is not None:row=dict(row,age_seconds=max(0.,now-row['estimated_start_at']))
            full=row['estimated_wait_seconds'];discount=discount_by_hospital.get(candidate['hospital'],0.)
            require(type(discount) in (int,float) and math.isfinite(discount) and 0<=discount<=full+1e-10,
                'virtual-only queue discount exceeds independently reconstructed full wait')
            records.append(dict(row,travel_seconds=candidate['travel_seconds'],accept_probability=beliefs(candidate['hospital'])[1],
                inquiry_cost_seconds=15,full_wait_before_discount_seconds=full,virtual_discount_seconds=discount,
                estimated_wait_seconds=max(0.,full-discount)))
    new={r['hospital']:r for r in records}
    def cdf(h,s,x):return base.new_cdf(new[h],s,x)
    def items(p):return old+[(new[h]['travel_seconds'],horizon.masses(lambda x,h=h,s=s:cdf(h,s,x),1.,bins)) for h,s in p.items() if s is not None]
    def feasible(p):return all(r['total_fresh_count']<=4 for r in base.occupancy(p,origin['original_all_own'],now))
    def contact_cost(p):return base.analytic_contact_cost(origin,records,p)
    def cost(p):return numeric.corrected_owner_metrics(items(p),bins,end-now,contact_cost(p))['total_cost_seconds']
    if reason=='plan':
        best=cost(plan)
        for _ in range(2):
            passes+=1;improved=False
            for row in pool:
                hid=row['hospital']
                for offset in [None]+offsets:
                    if offset==plan[hid]:continue
                    trial=dict(plan);trial[hid]=offset
                    if not feasible(trial):continue
                    value=cost(trial)
                    if value<best-1e-9:plan,best,improved=trial,value,True
            if not improved:break
        fresh=sum(0<=now+delta-q['submitted_at']<=180 for q in origin['original_all_own'])
        if fresh==0 and not any(s==delta for s in plan.values()) and pool and delta in offsets:
            options=[]
            for row in pool:
                trial=dict(plan);trial[row['hospital']]=delta
                if feasible(trial):options.append((cost(trial),row['travel_seconds'],trial))
            if options:plan=min(options,key=lambda r:(r[0],r[1]))[2];forced=True
        metrics=numeric.corrected_owner_metrics(items(plan),bins,end-now,contact_cost(plan))
    elif reason=='valid_acceptance':
        metrics=dict(selection_probability=1.,failure_probability=0.,capped_wait_seconds=0.,
            arrival_failure_cost_seconds=0.,planned_contact_cost_seconds=0.,total_cost_seconds=0.)
    else:metrics=numeric.corrected_owner_metrics(old,bins,end-now,0.)
    return dict(schema_version=1,rule=MODEL_RULE,scope=MODEL_SCOPE,patient_id=pid,
        effective_priority=owner['patient']['effective_priority'],planning_status=reason,barrier_offset_seconds=delta,
        allowed_offsets_seconds=offsets,queue_records=records,plan=[dict(hospital=q['hospital'],offset_seconds=plan[q['hospital']]) for q in pool],
        occupancy=base.occupancy(plan,origin['original_all_own'],now),passes=passes,forced=forced,metrics=metrics)


RULE='intact_Hedge_menu_reference_sends_with_fixed_F0_forward_expected_work'
OBJECTIVE='global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer Hedge actual index then original D menu order'
SCOPE='complete current WAITING/H-positive roster; unchanged first-cohort reference sends and fixed original-own CDFs; virtual expected work only, not fractional actual capacity or future-action agreement'


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
    require(type(evidence) is dict and type(evidence.get('original_hedge_proposal')) is dict
        and set(evidence['original_hedge_proposal'])=={'decisions','evidence'},'missing intact original Hedge proposal')
    original=evidence['original_hedge_proposal'];bm=[alias_member(m) for m in members];bt=[alias_member(t) for t in tails]
    bc=alias_context(context);bo=alias_owners(owner_context)
    # The two disclosed parent corrections are reused without changing any
    # frozen source, arithmetic, original proposal or comparison tolerance.
    with inherited_empty_scalars():
        checked=base.audit_pair(bm,bc,bt,original['decisions'],original['evidence'],priority_context,bo)
    base.validate_tail_members(bm,bc,bt)
    priorities=audit_priority_context(priority_context,context,members,tails)
    base.validate_owner_context(bo,bc,bm,bt,priority_context)
    evaluated=original['evidence']['evaluated'];base_index=original['evidence']['selected_index']
    menu=original['evidence']['menu_indices'];selected=base_index;origins=[];scores=[];selection=None
    first_ids={base.owner_key(pid) for pid in owner_context['first_cohort_patient_ids']}
    residual=[o for o in owner_context['inventory'] if base.owner_key(o['patient']['id']) not in first_ids]
    counts=Counter({k:0 for k in ('recourse_menus_evaluated','recourse_rows_checked','recourse_virtual_jobs_checked',
        'recourse_discounted_jobs_checked','recourse_owner_searches_checked','recourse_occupancy_waves_checked')})
    if evaluated:
        require(1<=len(menu)<=3 and type(evidence.get('row_scores')) is list and len(evidence['row_scores'])==len(menu),
            'recourse expanded or omitted original bounded menu')
        origins=[base.freeze_owner(o) for o in residual]
        precise_same(original['evidence']['owner_origins'],origins,'independent immutable owner origins')
        by_index={r['row_index']:r for r in original['evidence']['row_scores']}
        for index,recorded in zip(menu,evidence['row_scores']):
            parent_row=by_index[index]
            require(type(recorded) is dict and set(recorded)=={'row_index','base_row_score','first_cohort','owner_scores',
                'member_metrics','tier_probabilities','sum_capped_wait_seconds'},'recourse row fields differ')
            exact_copy(recorded['base_row_score'],parent_row,'intact original independently audited Hedge row')
            first=first_cohort_score(parent_row['first_cohort_score'],bm,priority_context)
            discounts={}
            for job in first['jobs']:discounts[job['hospital']]=discounts.get(job['hospital'],0.)+job['discount_seconds']
            pending=parent_row['first_cohort_score']['projection']['final_pending']
            exact_copy(pending[len(members[0]['inputs']['pending']):],[j['source']['inquiry'] for j in first['jobs']],
                'every owner hedge discount has exactly the intact virtual ledger source')
            owner_scores=[owner_score(o,f,pending,discounts) for o,f in zip(residual,origins)]
            metrics=deepcopy(first['member_metrics'])+[dict(patient_id=o['patient_id'],effective_priority=o['effective_priority'],metrics=o['metrics']) for o in owner_scores]
            require(sorted(base.owner_key(x['patient_id']) for x in metrics)
                ==sorted(base.owner_key(o['patient']['id']) for o in owner_context['inventory']), 'complete scored owner partition differs')
            wanted=dict(row_index=index,base_row_score=parent_row,first_cohort=first,owner_scores=owner_scores,member_metrics=metrics,
                tier_probabilities={str(t):sum((x['metrics']['selection_probability'] for x in metrics if x['effective_priority']==t),0.) for t in (4,2,1)},
                sum_capped_wait_seconds=sum((x['metrics']['capped_wait_seconds'] for x in metrics),0.))
            precise_same(recorded,wanted,'independent expected-work row')
            scores.append(wanted);counts['recourse_rows_checked']+=1;counts['recourse_virtual_jobs_checked']+=len(first['jobs'])
            counts['recourse_discounted_jobs_checked']+=sum(j['discount_seconds']>0 for j in first['jobs'])
            counts['recourse_owner_searches_checked']+=sum(s['planning_status']=='plan' for s in owner_scores)
            counts['recourse_occupancy_waves_checked']+=sum(len(s['occupancy']) for s in owner_scores)
        selection=dynamic.base.global_staged_choice(scores,base_index)
        selection['base_selected_tied']=selection.pop('base_budget_tied');selected=selection['selected_index']
        counts['recourse_menus_evaluated']+=1
    else:require(menu==[] and evidence.get('row_scores')==[],'unevaluated original Hedge invented recourse rows')
    intervened=selected!=base_index
    wanted=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_hedge__'),
        joint_context=context,priority_context=priority_context,owner_context=owner_context,original_hedge_proposal=original,
        evaluated=evaluated,menu_indices=menu,owner_origins=origins,row_scores=scores,selection=selection,
        base_selected_index=base_index,selected_index=selected,intervened=intervened,tiers=[4,2,1],
        tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=OBJECTIVE,scope=SCOPE)
    precise_same(evidence,wanted,'complete recourse priority selection')
    require(type(outputs) is list and len(outputs)==2,'actual recourse pair output coverage differs')
    original_joint=dynamic._nested_joint(original['evidence']['original_dynamic_proposal']['evidence']['original_cohort_success_proposal'])
    table=original_joint['evidence'];chosen=next((r for r in scores if r['row_index']==selected),None)
    for index in range(2):
        actual=deepcopy(original['decisions'][index])
        if intervened:
            hospitals=table['combinations'][selected]['hospitals'][index]
            actual=deepcopy(table['baseline_proposals'][index]);actual['send']=list(hospitals);actual['reason']='cohort_recourse_current_only'
            ev=actual['evidence'];ev['sent_hospitals']=list(hospitals)
            ev['planned_expected_cost']=round(chosen['member_metrics'][index]['metrics']['total_cost_seconds'],3)
            for row in ev['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
        actual['evidence']['policy']=NAME
        actual['evidence']['cohort_recourse']=dict(schema_version=1,mode='pair_cohort_recourse',evaluated=evaluated,
            intervened=intervened,member_index=index,base_selected_index=base_index,selected_index=selected,
            effective_priority=priorities[_pid(members[index]['patient_id'])])
        precise_same(outputs[index],actual,'separately reconstructed actual recourse outputs')
    checked=dict(checked,original_Hedge_groups_applied=checked['joint_groups_applied'],joint_groups_applied=int(selected!=table['baseline_index']))
    return dict(checked,**dict(counts),recourse_interventions=int(intervened))


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
            normalized = deepcopy(output); normalized['evidence'].pop('cohort_recourse')
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
        return numeric.audit_replay(view)['branch_audits'][0]
    try:
        environment=hashlib.sha256(json.dumps(scenario,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        require(branch['environment_sha256']==environment and branch['rule']=='immediate','bound public environment or confirmation rule differs')
        exact_copy(branch['instrumentation'],NEW_INSTRUMENTATION,'new adapter source contract')
        checks=_audit_calls(scenario,branch);accounting=horizon._audit_trace_fixed(scenario,branch)
        total_j=sum(1800 if p['failed'] else p['estimated_arrival_seconds'] for p in branch['patients'])
        return dict(passed=True,**checks,**accounting,total_arrival_failure_cost_seconds=total_j,
            mean_arrival_failure_cost_seconds=total_j/len(branch['patients']) if branch['patients'] else None,
            original_Hedge_proposal_independently_checked=True,original_D_CS_continuation_intact=True,
            fixed_F0_one_pass_expected_work_independently_rebuilt=True,
            actual_pending_and_own_CDF_unchanged=True,integer_occupancy_and_contact_budget_independently_checked=True,
            actual_outputs_sends_and_all_patient_FIFO_checked=True,physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='complete actual source captures and prefix/version checks; physical atomicity needs frozen adapter and rollback tests',
            scope='intact original Hedge proposal; fixed row-specific CS send/order; independently rebuilt virtual-only expected work and full priority score; late H unchanged; actual all-patient trace accounting')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise base.CohortHedgeAuditError('malformed saved cohort-recourse artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version']==1 and type(replay['branches']) is list,'invalid complete replay')
    require(replay['arms']==[[b['policy'],b['rule']] for b in replay['branches']]
        and len({tuple(a) for a in replay['arms']})==len(replay['arms'])>0,'complete arm inventory differs')
    require(all(b['environment_sha256']==replay['environment_sha256']
        and b['snapshot_sha256']==replay['initial_snapshot_sha256'] for b in replay['branches']), 'paired initial environment differs')
    if not any(b.get('policy')==NAME for b in replay['branches']):return numeric.audit_replay(replay)
    controls=[b for b in replay['branches'] if b.get('policy')!=NAME];control_proof=None;by_policy={}
    if controls:
        view=dict(replay,branches=controls,arms=[[b['policy'],b['rule']] for b in controls])
        control_proof=numeric.audit_replay(view)
        by_policy={(b['policy'],b['rule']):p for b,p in zip(controls,control_proof['branch_audits'])}
    checked=[audit_branch(replay['scenario'],b) if b['policy']==NAME else by_policy[b['policy'],b['rule']] for b in replay['branches']]
    return dict(passed=True,branches=len(checked),branch_audits=checked,new_executions=0,
        preserved_parent_audit_proof=control_proof)

