"""Independent audit of the visible-FIFO silence-conditioned queue estimate.

Only standard library and SHA-bound independent saved-raw auditors run here.
The numerical reconstruction uses unweighted prefix completion distributions
and applies silence likelihoods at observation, rather than forwarding the
producer's weighted states. The original residual planner's search is source-
bound, not independently optimized. No policy, kernel or server is imported.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from . import guard_audit as guard

GUARD_AUDITOR_SHA256='6a8607bd247d819699e69897eebca40d0e9c5b4ab7d253cb6cd86b049c3b1d3f'
if hashlib.sha256(Path(guard.__file__).read_bytes()).hexdigest()!=GUARD_AUDITOR_SHA256:
    raise ValueError('frozen guarded-membership independent auditor differs')
batch=guard.batch;residual=guard.residual;parent=guard.parent
FifoAuditError=guard.GuardAuditError
require,number,EPOCH=guard.require,guard.number,guard.EPOCH
NAME='eat_fifo_l15_f';RESIDUAL_NAME=guard.RESIDUAL_NAME
BASE_POLICY=guard.BASE_POLICY;BATCH_NAME=guard.BATCH_NAME;GUARD_NAME=guard.NAME
INPUT_KEYS,CALL_KEYS=guard.INPUT_KEYS,guard.CALL_KEYS
_ledger,_ranked,_contains_private=guard._ledger,guard._ranked,guard._contains_private
_audit_trace_fixed=guard._audit_trace_fixed


def _sf(elapsed,mu,sigma):
    return 1. if elapsed<=0 else .5*math.erfc((math.log(elapsed)-mu)/(sigma*math.sqrt(2)))


def _interval(lower,upper,mu,sigma):
    """Stable independent lognormal mass in (lower, upper]."""
    if upper<=0 or upper<=lower:return 0.
    if lower<=0:return .5*math.erfc(-(math.log(upper)-mu)/(sigma*math.sqrt(2)))
    if upper<=math.exp(mu):
        cdf=lambda x:.5*math.erfc(-(math.log(x)-mu)/(sigma*math.sqrt(2)))
        return max(0.,cdf(upper)-cdf(lower))
    return max(0.,_sf(lower,mu,sigma)-_sf(upper,mu,sigma))


def prefix_expectation(submitted,now,started,mu,sigma,reply,step):
    """Finite-grid quadrature of conditional remaining visible FIFO work.

    Prefix finish distributions here contain no response likelihood. For a
    state with j completed jobs, q**j is applied only to that stage's ongoing
    contribution. Fully completed n-job mass enters the denominator alone.
    The final partial completion bin ends at now, never in the future.
    """
    require(type(submitted) is list and bool(submitted),'nonempty submitted sequence required')
    require(type(now) in (int,float) and math.isfinite(now) and type(started) in (int,float)
        and math.isfinite(started) and 0<=started<=now,'invalid current FIFO head clock')
    require(all(type(t) in (int,float) and math.isfinite(t) and 0<=t<=now for t in submitted)
        and submitted==sorted(submitted) and submitted[0]<=started,
        'invalid current FIFO submission clocks')
    number(now-started,'head age');number(step,'FIFO grid step')
    require(step>0 and type(mu) in (int,float) and math.isfinite(mu)
        and type(sigma) in (int,float) and math.isfinite(sigma) and sigma>0
        and type(reply) in (int,float) and math.isfinite(reply) and 0<=reply<=1,
        'invalid FIFO service or reply distribution')
    age=now-started;mean=math.exp(mu+.5*sigma*sigma)
    endpoints=[float(started)]
    index=1
    while started+index*step<now:
        endpoints.append(started+index*step);index+=1
    if now>started:endpoints.append(float(now))
    completion={float(started):1.};den_parts=[];num_parts=[];stages=[];cache={};underflow=False
    for completed,arrival in enumerate(submitted):
        likelihood=(1-reply)**completed;next_completion={};active_den=[];active_num=[]
        if reply<1 and likelihood==0:underflow=True
        for previous,probability in completion.items():
            start=max(previous,arrival,started)
            if start not in cache:
                spent=now-start;survival=_sf(spent,mu,sigma)
                stoploss=residual.expected_remaining(spent,mu,sigma,0)
                if survival==0 or stoploss==0:underflow=True
                transitions=[]
                for left,right in zip(endpoints,endpoints[1:]):
                    mass=_interval(left-start,right-start,mu,sigma)
                    if right>max(left,start) and mass==0:underflow=True
                    if mass:transitions.append((right,mass))
                cache[start]=(survival,stoploss,transitions)
            survival,stoploss,transitions=cache[start]
            active_d=probability*survival*likelihood
            work=stoploss+(len(submitted)-completed-1)*mean*survival
            active_n=probability*work*likelihood
            if probability>0 and likelihood>0 and ((survival>0 and active_d==0) or (work>0 and active_n==0)):
                underflow=True
            active_den.append(active_d);active_num.append(active_n)
            for finish,mass in transitions:
                product=probability*mass
                if probability>0 and mass>0 and product==0:underflow=True
                weighted=probability*likelihood
                if probability>0 and likelihood>0 and weighted==0:underflow=True
                after_reply=weighted*(1-reply)
                if weighted>0 and reply<1 and after_reply==0:underflow=True
                if after_reply>0 and mass>0 and after_reply*mass==0:underflow=True
                next_completion[finish]=next_completion.get(finish,0.)+product
        den=math.fsum(active_den);num=math.fsum(active_num)
        den_parts.append(den);num_parts.append(num)
        complete_weight=math.fsum(next_completion.values())*(1-reply)**(completed+1)
        if reply<1 and next_completion and complete_weight==0:underflow=True
        stages.append(dict(position=completed,entry_state_count=len(completion),
            entry_weight=math.fsum(completion.values())*likelihood,
            active_denominator=den,active_numerator=num,completed_weight=complete_weight))
        completion=next_completion
    all_complete=math.fsum(completion.values())*(1-reply)**len(submitted)
    denominator=1. if reply==0 else math.fsum(den_parts+[all_complete]);numerator=math.fsum(num_parts)
    return dict(denominator=denominator,numerator_seconds=numerator,
        expected_remaining_seconds=numerator/denominator if denominator>0 else None,
        all_completed_silence_mass=all_complete,endpoints=endpoints,stages=stages,underflow=underflow)


FIFO_RULE='silence_conditioned_visible_fifo_prefix'
FIFO_EXTRA_FIELDS={'fifo_order','method','fallback_reason','grid_base_seconds','grid_max_cells',
    'grid_step_seconds','grid_offsets_seconds','prefix_layers','silence_probability',
    'remaining_numerator','conditional_work_seconds'}
LAYER_FIELDS={'position','entry_state_count','entry_weight','active_denominator',
    'active_numerator','completed_weight'}


def fifo_estimate(queue,history,now,base_record,workers=1):
    """Expected evidence from public visible jobs and the bound old head row."""
    row=dict(base_record)
    row['assumed_workers']=workers
    row['estimated_wait_seconds']=base_record['estimated_wait_seconds']/workers
    row['original_full_wait_seconds']=base_record['original_full_wait_seconds']/workers
    ordered=sorted(queue,key=lambda r:(r['submitted_at'],type(r['patient']).__name__,str(r['patient'])))
    ordered=[{key:r[key] for key in ('hospital','patient','submitted_at')} for r in ordered]
    row.update(fifo_order=ordered,method='residual_analytic_exact',fallback_reason=None,
        grid_base_seconds=5,grid_max_cells=60,grid_step_seconds=None,grid_offsets_seconds=[],
        prefix_layers=[],silence_probability=None,remaining_numerator=None,
        conditional_work_seconds=row['estimated_wait_seconds']*workers)
    if not ordered:reason='empty_queue'
    elif workers!=1:reason='worker_assumption'
    elif any(o['hospital']==r['hospital'] and o['submitted_at']==r['submitted_at']
        and o['outcome']!='timeout' for o in history for r in ordered):reason='visible_reply'
    elif len(ordered)==1:reason='single_job_exact'
    elif row['age_seconds']==0:reason='fresh_head_exact'
    elif row['reply_probability']==1:reason='fully_responsive_exact'
    else:reason=None
    if reason is not None:
        row['fallback_reason']=reason
        if reason in ('worker_assumption','visible_reply'):row['method']='residual_analytic_fallback'
        return row
    step=max(5,int(math.ceil(row['age_seconds']/60)))
    try:
        result=prefix_expectation([r['submitted_at'] for r in ordered],now,row['estimated_start_at'],
            row['service_mu'],row['service_sigma'],row['reply_probability'],step)
        den,num=result['denominator'],result['numerator_seconds'];wait=result['expected_remaining_seconds']
        if result['underflow']:reason='numerical_underflow'
        elif not math.isfinite(den) or den<=0 or den>1:reason='probability_out_of_range'
        elif not math.isfinite(num) or num<0 or wait is None or not math.isfinite(wait):reason='nonfinite_work'
        else:reason=None
    except (OverflowError,ArithmeticError):
        reason='nonfinite_work'
    if reason is not None:
        row.update(method='residual_analytic_fallback',fallback_reason=reason)
        return row
    row.update(method='weighted_prefix_dp',grid_step_seconds=step,
        grid_offsets_seconds=[t-row['estimated_start_at'] for t in result['endpoints']],
        prefix_layers=result['stages'],silence_probability=den,remaining_numerator=num,
        estimated_wait_seconds=wait,conditional_work_seconds=wait)
    return row


def fifo_rows(inputs):
    rows=[]
    for base in residual.residual_rows(inputs):
        queue=[p for p in inputs['pending'] if p['hospital']==base['hospital']]
        rows.append(fifo_estimate(queue,inputs['observations'],inputs['now'],base))
    return rows


def audit_fifo(inputs,output):
    evidence=output['evidence'];trace=evidence['fifo_queue'];expected=fifo_rows(inputs)
    require(type(trace) is dict and set(trace)=={'schema_version','rule','hospitals'}
        and type(trace['schema_version']) is int and trace['schema_version']==1
        and trace['rule']==FIFO_RULE,'unknown FIFO queue evidence contract')
    require(type(trace['hospitals']) is list and len(trace['hospitals'])==len(expected),
        'FIFO trace omitted or added original first-six candidates')
    discrete={'hospital','queue_count','assumed_workers','head','prior_terminal_reply',
        'estimated_start_at','age_seconds','queued_behind_count','fifo_order','method','fallback_reason',
        'grid_base_seconds','grid_max_cells','grid_step_seconds','grid_offsets_seconds'}
    for actual,wanted in zip(trace['hospitals'],expected):
        require(type(actual) is dict and set(actual)==residual.RESIDUAL_FIELDS|FIFO_EXTRA_FIELDS,
            'unknown FIFO candidate evidence fields')
        require(all(actual[key]==wanted[key] for key in discrete)
            and all(type(actual[key]) is int for key in ('queue_count','assumed_workers',
                'queued_behind_count','grid_base_seconds','grid_max_cells')),
            'FIFO public order, head clock, method, fallback or bounded grid differs')
        require(all(actual[key] is None if wanted[key] is None else
            type(actual[key]) in (int,float) and math.isfinite(actual[key]) for key in ('estimated_start_at','age_seconds'))
            and type(actual['grid_offsets_seconds']) is list
            and all(type(value) in (int,float) and math.isfinite(value) for value in actual['grid_offsets_seconds']),
            'invalid FIFO public clock or grid endpoint types')
        for key in (residual.RESIDUAL_FIELDS|FIFO_EXTRA_FIELDS)-discrete-{'prefix_layers'}:
            require(actual[key] is None if wanted[key] is None else parent.close(actual[key],wanted[key]),
                'FIFO queue math differs from independent unweighted-prefix reconstruction')
        require(type(actual['prefix_layers']) is list and len(actual['prefix_layers'])==len(wanted['prefix_layers']),
            'FIFO prefix layer evidence incomplete')
        for got,row in zip(actual['prefix_layers'],wanted['prefix_layers']):
            require(type(got) is dict and set(got)==LAYER_FIELDS
                and all(type(got[key]) is int and got[key]==row[key] for key in ('position','entry_state_count'))
                and all(parent.close(got[key],row[key]) for key in LAYER_FIELDS-{'position','entry_state_count'}),
                'weighted FIFO layer differs from independent unweighted-prefix expectation')
    return expected


def audit_fifo_plan(inputs,output):
    """Audit queue math and the recorded unchanged planner's resulting plan."""
    require(type(output) is dict and set(output)=={'send','reason','evidence','next_review_at'},
        'invalid FIFO policy output contract')
    evidence=output['evidence'];records=audit_fifo(inputs,output)
    require(type(evidence) is dict and set(evidence)==(guard.PROPOSAL_EVIDENCE_FIELDS-{'residual_queue'})|{'fifo_queue'}
        and evidence['policy']==NAME,'FIFO altered original residual evidence core')
    model=batch.batch_model(inputs);beliefs=parent._beliefs(inputs['observations'],inputs['now'])
    global_reply,global_accept,global_mu,_=beliefs('')
    require(evidence['reply_probability_global']==round(global_reply,4)
        and evidence['accept_probability_global']==round(global_accept,4)
        and evidence['service_median_seconds']==round(math.exp(global_mu),2),
        'FIFO original learned global evidence differs from independent public-history beliefs')
    new_items={}
    for row,record in zip(model['pool'],records):
        hid=row['hospital'];r,a,mu,sigma=beliefs(hid);wait=record['estimated_wait_seconds']
        masses=parent._masses(lambda x:parent._cdf(x-wait,mu,sigma),r*a,model['cells'])
        new_items[hid]=(row['travel_seconds'],masses)
    plan=evidence['candidate_plan'];pool=[row['hospital'] for row in model['pool']]
    require(type(plan) is list and [row['hospital'] for row in plan]==pool,
        'FIFO changed the original first-six travel-input-order pool')
    allowed={0,15,30,60,90,120,180};chosen=[]
    for row in plan:
        require(type(row) is dict and set(row)=={'hospital','planned_offset_seconds','inquiry_cost',
            'reply_probability','accept_probability','queue_length','travel_seconds'},
            'invalid unchanged planner candidate evidence')
        hid=row['hospital'];offset=row['planned_offset_seconds'];r,a,_,_=beliefs(hid)
        require(offset is None or type(offset) is int and offset in allowed and offset<model['horizon'],
            'FIFO planner changed original allowed offsets')
        require(row['travel_seconds']==new_items[hid][0] and row['inquiry_cost']==15
            and row['reply_probability']==round(r,4) and row['accept_probability']==round(a,4)
            and type(row['queue_length']) is int
            and row['queue_length']==sum(p['hospital']==hid for p in inputs['pending']),
            'FIFO altered original inquiry cost, public travel or learned response beliefs')
        if offset is not None:chosen.append((offset,hid))
    send=[hid for offset,hid in chosen if offset==0]
    require(output['send']==send and evidence['sent_hospitals']==send
        and len(send)<=max(0,model['room']) and output['next_review_at']==inputs['now']+15,
        'FIFO recorded current send, original cap or review interval differs')
    require(not (model['fresh']==0 and pool and model['room']>0) or bool(send),
        'FIFO suppressed the original no-fresh-pending safety inquiry')
    items=list(model['current'])
    for offset,hid in chosen:
        travel,masses=new_items[hid];shift=offset//5
        items.append((travel,[0.]*shift+masses[:model['cells']-shift] if shift else list(masses)))
    cost=batch.expected_first_accept_cost(items,model['cells'],model['horizon'])
    for offset,_ in chosen:
        probability=math.prod(max(0.,1-sum(m[:offset//5])) for _,m in items) if offset else 1.
        cost+=15*probability
    base=batch.expected_first_accept_cost(model['current'],model['cells'],model['horizon'])
    require(evidence['evaluated_at']==inputs['now'] and evidence['horizon_seconds']==model['horizon']
        and evidence['lambda_seconds']==15 and evidence['congestion_aware'] is False
        and evidence['own_waiting_count']==model['fresh']
        and evidence['own_observation_timed_out_count']==len(inputs['patient_pending'])-model['fresh']
        and evidence['expected_cost_without_new']==round(base,3)
        and evidence['planned_expected_cost']==round(cost,3)
        and type(evidence['history_used_count']) is int and evidence['history_used_count']==len(inputs['observations'])
        and type(evidence['unresolved_inquiry_count']) is int and evidence['unresolved_inquiry_count']==len(inputs['pending']),
        'FIFO changed pending CDF, horizon, current counts or recorded plan cost')
    if inputs['max_new']==0:reason='contact_budget'
    elif send:reason='eat_initial' if not inputs['patient_pending'] else 'eat_hedge'
    elif not inputs['candidates']:reason='no_candidates'
    elif model['room']<=0:reason='eat_concurrency_cap'
    else:reason='eat_wait'
    require(output['reason']==reason,'FIFO changed original action reason')
    return dict(fifo_queue_candidates_checked=len(records),
        fifo_dp_candidates_checked=sum(r['method']=='weighted_prefix_dp' for r in records),
        fifo_exact_candidates_checked=sum(r['method']=='residual_analytic_exact' for r in records),
        fifo_fallback_candidates_checked=sum(r['method']=='residual_analytic_fallback' for r in records))


def _audit_calls_fifo(scenario,branch):
    events=branch['db_events'];calls=branch['policy_calls'];decisions=branch['db_decisions']
    require(type(calls) is list and len(calls)==len(decisions),'policy call/DB decision coverage differs')
    require(all(type(row['id']) is int and row['id']>0 for row in events)
        and [row['id'] for row in events]==sorted({row['id'] for row in events}),
        'DB event identities not unique and ordered')
    event_ids={event['id'] for event in events};specs={p['id']:p for p in scenario['patients']}
    inquiry_events=[event for event in events if event['type']=='inquiry_sent']
    actual_sends=Counter();declared_sends=Counter();changed=Counter();last_decision=0;last_prefix=0
    for call,decision in zip(calls,decisions):
        require(type(call) is dict and set(call)==CALL_KEYS,'unknown or missing policy call fields')
        pid=call['patient_id'];at=call['at'];inputs=call['inputs'];output=call['output'];prefix=call['db_event_prefix_max']
        require(pid in specs and type(at) is int and specs[pid]['at']<=at<specs[pid]['at']+300,
            'policy ran outside current patient search window')
        require(type(prefix) is int and prefix>=last_prefix and (prefix==0 or prefix in event_ids),
            'invalid or reordered committed DB event prefix')
        last_prefix=prefix
        require(type(inputs) is dict and set(inputs)==INPUT_KEYS and not _contains_private(inputs),
            'policy received hidden, future or unknown context fields')
        now=EPOCH+at
        require(inputs['now']==now and call['name']==branch['policy']==decision['policy_name']
            and decision['at']==now and decision['patient_id']==pid
            and type(decision['id']) is int and decision['id']==call['db_decision_id']>last_decision,
            'policy call identity/clock differs from actual DB decision')
        last_decision=decision['id']
        require(output['send']==decision['hospital_ids'] and output['reason']==decision['reason']
            and output['evidence']==decision['evidence'],'captured output differs from committed decision')
        history,pending,own,latest=_ledger(events,now,pid,prefix)
        travel=specs[pid]['travel_seconds']
        expected_own=[dict(row,travel_seconds=travel[row['hospital']]) for row in own if row['hospital'] in travel]
        require(inputs['observations']==history and inputs['pending']==pending
            and inputs['patient_pending']==expected_own,'policy ledger differs from original received event prefix')
        sent=[event for event in inquiry_events if event['id']<=prefix and event['patient_id']==pid]
        contacted={event['hospital_id'] for event in sent}
        require(inputs['max_new']==10-len(contacted) and type(inputs['max_new']) is int,
            'remaining contact budget differs from actual previous sends')
        require(inputs['last_contact_at']==max((event['at'] for event in sent if event['at']<=now),default=None),
            'last_contact_at differs from actual current contact history')
        ranked=_ranked(inputs['candidates'])
        require({row['hospital'] for row in ranked}==set(travel)-contacted
            and all(row['travel_seconds']==travel[row['hospital']] for row in ranked),
            'candidate universe/travel differs from current uncontacted scenario offers')
        require(type(output['send']) is list and len(set(output['send']))==len(output['send'])
            and len(output['send'])<=inputs['max_new'] and all(hid in set(travel)-contacted for hid in output['send']),
            'decision sent duplicate, already-contacted or unavailable candidate')
        fresh=sum(now-row['submitted_at']<=180 for row in expected_own)
        if call['name'].startswith('eat_'):
            require(fresh+len(output['send'])<=4,'EAT fresh concurrency limit exceeded')
        elif call['name'].startswith(('v3_','observed_')):
            require(fresh+len(output['send'])<=3,'observed-policy fresh concurrency limit exceeded')
        elif call['name']=='fixed_3':
            require(len(output['send'])<=3,'fixed-three wave exceeded three sends')
        else:require(False,'unknown comparison policy')
        for hid in output['send']:
            matches=[event for event in inquiry_events if event['patient_id']==pid and event['hospital_id']==hid
                and event['at']==now and event['id']>prefix]
            require(len(matches)==1,'actual policy send lacks unique subsequent DB contact')
            declared_sends[matches[0]['id']]+=1
        if call['name']==NAME:changed.update(audit_fifo_plan(inputs,output))
        elif call['name']==GUARD_NAME:
            require('fifo_queue' not in output['evidence'],'guard reference contains FIFO intervention')
            guard.audit_guard(inputs,output)
        elif call['name']==BATCH_NAME:
            require('fifo_queue' not in output['evidence'] and 'guard' not in output['evidence'],
                'batch reference contains FIFO/guard intervention')
            batch.audit_batch(inputs,output)
        elif call['name']==RESIDUAL_NAME:
            require(all(key not in output['evidence'] for key in ('fifo_queue','guard','batch')),
                'residual reference contains FIFO/guard/batch intervention')
            residual.audit_residual(inputs,output)
        else:require(all(key not in output['evidence'] for key in ('fifo_queue','residual_queue','shortlist','batch','guard')),
            'reference policy contains FIFO/guard/batch/residual intervention')
    for event in inquiry_events:
        if event['patient_id'] in specs:actual_sends[event['id']]+=1
    require(declared_sends==actual_sends,'actual sends not covered exactly once by policy calls')
    return dict(policy_calls=len(calls),**{key:changed[key] for key in
        ('fifo_queue_candidates_checked','fifo_dp_candidates_checked','fifo_exact_candidates_checked','fifo_fallback_candidates_checked')})


def audit_branch(scenario,branch):
    """Audit complete saved actual workflow raw, never execute any policy."""
    try:
        environment=hashlib.sha256(json.dumps(scenario,ensure_ascii=False,sort_keys=True,
            separators=(',',':')).encode('utf-8')).hexdigest()
        require(branch['environment_sha256']==environment,'raw scenario/environment binding differs')
        require((branch['policy'],branch['rule']) in ((NAME,'immediate'),(RESIDUAL_NAME,'immediate'),
            (BATCH_NAME,'immediate'),(GUARD_NAME,'immediate'),(BASE_POLICY,'immediate'),
            (BASE_POLICY,'hold120'),('fixed_3','immediate'),('v3_w45_h900','immediate')),
            'unknown or combined FIFO comparison intervention')
        require(branch['instrumentation']==dict(original_runner='inquiry_lab.runner._branch',
            actual_workflow='server.inquiry_workflow.run_once',record_only=True,epoch=EPOCH,
            policy_inputs_unchanged=True),'record-only adapter contract differs')
        calls=_audit_calls_fifo(scenario,branch);accounting=_audit_trace_fixed(scenario,branch)
        return dict(passed=True,**calls,**accounting,policy_kernel_independently_reexecuted=False,
            fifo_queue_math_independently_reconstructed=True,
            recorded_plan_cost_independently_reconstructed=True,
            original_residual_search_independently_reexecuted=False,optimality_claimed=False,
            numerical_fallback_labels_independently_checked=True,
            scope='saved public ledger, visible ordered cohort with one-worker/independence assumptions, unweighted prefix convolution plus no-reply likelihood, analytic boundaries/fallback and bounded ceil grid, recorded original planner cost (search source-bound), actual workflow/accounting; no true FIFO detection or global/clinical optimum')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise FifoAuditError('malformed saved visible-FIFO artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version']==1 and type(replay['branches']) is list,
        'invalid complete raw replay')
    require(replay['arms']==[[branch['policy'],branch['rule']] for branch in replay['branches']]
        and len({tuple(arm) for arm in replay['arms']})==len(replay['arms'])>0,
        'replay arm inventory or order differs')
    require(all(branch['environment_sha256']==replay['environment_sha256']
        and branch['snapshot_sha256']==replay['initial_snapshot_sha256'] for branch in replay['branches']),
        'paired branches did not share the recorded original environment/snapshot')
    checked=[audit_branch(replay['scenario'],branch) for branch in replay['branches']]
    return dict(passed=True,branches=len(checked),branch_audits=checked,new_executions=0)

