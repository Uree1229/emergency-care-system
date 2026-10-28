"""Independent audit of a current-only, feasible EAT batch on saved full raw.

The frozen residual auditor supplies the public event-prefix ledger, residual
head-work arithmetic, and actual workflow accounting. No producer, planning
kernel, server, simulator, or solver is imported. Every feasible immediate
subset is rebuilt here from public inputs. Its first-acceptance expectation is
computed by disjoint winner probabilities, independently of the producer's
hazard calculation. Exactness is limited to this one current, fixed model batch;
it is not global, future-policy, empirical, or clinical optimality.
"""
from collections import Counter
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path

from . import residual_audit as residual

RESIDUAL_AUDITOR_SHA256='528eddf407442d1489ccf26c58d14cf5cb4da6225f3560b60457b5611dd61191'
if hashlib.sha256(Path(residual.__file__).read_bytes()).hexdigest()!=RESIDUAL_AUDITOR_SHA256:
    raise ValueError('frozen residual raw auditor differs')
parent=residual.parent
BatchAuditError=residual.ResidualAuditError
require,number,EPOCH=residual.require,residual.number,residual.EPOCH
NAME='eat_batch_l15_f'
RESIDUAL_NAME=residual.NAME
BASE_POLICY=residual.BASE_POLICY
RULE='enumerate_current_feasible_batches'
INPUT_KEYS,CALL_KEYS=residual.INPUT_KEYS,residual.CALL_KEYS
_ledger,_ranked,_contains_private=residual._ledger,residual._ranked,residual._contains_private
_audit_trace_fixed=residual._audit_trace_fixed
TOLERANCE=1e-9


def expected_first_accept_cost(items,cells,horizon):
    """Sum disjoint (first accepting bin, nearest winner) events directly.

    For hospital i in a bin, the winner event is its bin mass, no acceptance
    through this bin at nearer hospitals, and none before this bin at farther
    hospitals. No conditional hazards or producer expectation code are used.
    """
    number(horizon,'planning horizon')
    require(type(cells) is int and cells>=1,'invalid distribution grid size')
    ordered=sorted(items,key=lambda row:row[0])
    for travel,masses in ordered:
        number(travel,'planning travel')
        require(type(masses) is list and len(masses)==cells,'invalid acceptance grid')
        require(all(type(p) in (int,float) and math.isfinite(p) and p>=0 for p in masses)
            and sum(masses)<=1+1e-10,'invalid acceptance probability mass')
    remaining=[1.]*len(ordered);cost=0.
    for cell in range(cells):
        # Retain the frozen model's tiny-probability numerical stopping rule.
        if math.prod(remaining)<=1e-12:return cost
        after=[max(0.,remaining[i]-item[1][cell]) for i,item in enumerate(ordered)]
        for i,(travel,masses) in enumerate(ordered):
            if remaining[i]<=1e-12:continue
            winner=masses[cell]*math.prod(after[:i])*math.prod(remaining[i+1:])
            cost+=winner*((cell+.5)*5+travel)
        remaining=after
    return cost+math.prod(remaining)*(horizon+1500)


def batch_model(inputs):
    """Rebuild unchanged current inquiry CDFs and residual new-inquiry CDFs."""
    now=inputs['now'];own=inputs['patient_pending'];history=inputs['observations']
    beliefs=parent._beliefs(history,now);queue={};replies={}
    for row in inputs['pending']:queue.setdefault(row['hospital'],[]).append(row)
    for row in history:
        if row['outcome']!='timeout':replies.setdefault(row['hospital'],[]).append(row)
    known=[row['submitted_at'] for row in own]
    if inputs['last_contact_at'] is not None:known.append(inputs['last_contact_at'])
    elapsed=now-min(known) if known else 0
    horizon=max(15,300-elapsed);cells=max(3,int(math.ceil(horizon/5)))
    current=[]
    for row in own:
        hid,submitted=row['hospital'],row['submitted_at'];r,a,mu,sigma=beliefs(hid)
        ahead=sum(1. if other['submitted_at']<submitted else .5 for other in queue.get(hid,[])
            if other['submitted_at']<=submitted and other['patient']!=row['patient'])
        if ahead>0:
            wait=ahead*math.exp(mu+.5*sigma*sigma)
            masses=parent._masses(lambda x:parent._cdf(x-wait,mu,sigma),r*a,cells)
        else:
            freed=[other['observed_at'] for other in replies.get(hid,[])
                if other['submitted_at']<=submitted]
            spent=max(0.,now-max([submitted,*freed]));f0=parent._cdf(spent,mu,sigma)
            alive=max(1e-9,1-f0);left=r*alive/max(1e-9,1-r*f0)
            masses=parent._masses(lambda x:(parent._cdf(spent+x,mu,sigma)-f0)/alive,left*a,cells)
        current.append((row['travel_seconds'],masses))
    rows=residual.residual_rows(inputs);pool=_ranked(inputs['candidates'])[:min(6,inputs['max_new'])]
    items={}
    for row,record in zip(pool,rows):
        hid=row['hospital'];r,a,mu,sigma=beliefs(hid);wait=record['estimated_wait_seconds']
        masses=parent._masses(lambda x:parent._cdf(x-wait,mu,sigma),r*a,cells)
        items[hid]=(row['travel_seconds'],masses)
    fresh=sum(now-row['submitted_at']<=180 for row in own)
    room=min(inputs['max_new'],4-fresh)
    return dict(horizon=horizon,cells=cells,current=current,items=items,pool=pool,
        residual_rows=rows,fresh=fresh,room=room)


def select_near_minimum(subsets):
    """Global epsilon tie, followed by fewer contacts and original pool order."""
    require(type(subsets) is list and bool(subsets),'empty batch enumeration')
    require(all(type(row) is dict and type(row['hospitals']) is list
        and type(row['cost']) in (int,float) and math.isfinite(row['cost']) for row in subsets),
        'invalid batch costs')
    minimum=min(row['cost'] for row in subsets)
    index=min((i for i,row in enumerate(subsets) if row['cost']<=minimum+TOLERANCE),
        key=lambda i:(len(subsets[i]['hospitals']),i))
    return index,minimum


def enumerate_batches(inputs):
    """All legal current subsets, in fewer-contacts then original-pool order."""
    model=batch_model(inputs);pool=[row['hospital'] for row in model['pool']]
    costs=[];maximum=max(0,min(model['room'],len(pool)))
    for size in range(maximum+1):
        for subset in combinations(pool,size):
            cost=expected_first_accept_cost(model['current']+[model['items'][h] for h in subset],
                model['cells'],model['horizon'])+15*size
            costs.append(dict(hospitals=list(subset),cost=cost))
    best_index,minimum=select_near_minimum(costs)
    best=list(costs[best_index]['hospitals']);best_cost=costs[best_index]['cost']
    selected=list(best);forced=False;safety=None;selected_cost=best_cost
    if model['fresh']==0 and not selected and pool and model['room']>0:
        singles=[row for row in costs if len(row['hospitals'])==1]
        travels={row['hospital']:row['travel_seconds'] for row in model['pool']}
        chosen=min(singles,key=lambda row:(row['cost'],travels[row['hospitals'][0]]))
        selected=list(chosen['hospitals']);selected_cost=chosen['cost'];forced=True;safety=selected[0]
    for row in costs:row['selected_before_safety']=row['hospitals']==best
    return dict(model=model,maximum_subset_size=maximum,subsets=costs,minimum_cost=minimum,
        optimal_subset=best,optimal_cost=best_cost,
        selected=selected,selected_cost=selected_cost,forced=forced,safety_hospital=safety)


BATCH_FIELDS={'schema_version','rule','room','pool','subsets','evaluated_count',
    'maximum_subset_size','minimum_cost','optimal_subset','optimal_cost',
    'selected','selected_cost','forced','safety_hospital'}


def _audit_residual_evidence(inputs,output):
    """Original residual trace contract, with only the new policy name allowed."""
    evidence=output['evidence'];trace=evidence['residual_queue'];expected=residual.residual_rows(inputs)
    require(type(trace) is dict and set(trace)=={'schema_version','rule','hospitals'}
        and type(trace['schema_version']) is int and trace['schema_version']==1
        and trace['rule']=='head_residual_plus_full_followers','unknown residual queue trace contract')
    require(type(trace['hospitals']) is list and len(trace['hospitals'])==len(expected),
        'residual trace omitted or added original pool candidates')
    discrete={'hospital','queue_count','assumed_workers','head','prior_terminal_reply',
        'estimated_start_at','age_seconds','queued_behind_count'}
    for actual,wanted in zip(trace['hospitals'],expected):
        require(type(actual) is dict and set(actual)==residual.RESIDUAL_FIELDS,'unknown residual candidate evidence fields')
        require(all(actual[key]==wanted[key] for key in discrete)
            and all(type(actual[key]) is int for key in ('queue_count','assumed_workers','queued_behind_count')),
            'residual queue head, current-age start, worker assumption or original order differs')
        require(all(actual[key] is None if wanted[key] is None else
            type(actual[key]) in (int,float) and math.isfinite(actual[key])
            for key in ('estimated_start_at','age_seconds')),'invalid residual public clock types')
        require(all(parent.close(actual[key],wanted[key]) for key in residual.RESIDUAL_FIELDS-discrete),
            'residual wait differs from independent public-information distribution arithmetic')
    require('shortlist' not in evidence,'rejected shortlist policy combined with current batch')
    return len(expected)


def audit_batch(inputs,output):
    evidence=output['evidence'];trace=evidence['batch'];wanted=enumerate_batches(inputs);model=wanted['model']
    _audit_residual_evidence(inputs,output)
    require(type(trace) is dict and set(trace)==BATCH_FIELDS
        and type(trace['schema_version']) is int and trace['schema_version']==1
        and trace['rule']==RULE,'unknown current-batch enumeration trace contract')
    pool=[row['hospital'] for row in model['pool']]
    require(type(trace['room']) is int and trace['room']==model['room'] and trace['pool']==pool,
        'batch room, original first-six pool or candidate order differs')
    require(type(trace['maximum_subset_size']) is int
        and trace['maximum_subset_size']==wanted['maximum_subset_size'],
        'batch maximum feasible subset size differs')
    require(type(trace['subsets']) is list and len(trace['subsets'])==len(wanted['subsets'])
        and type(trace['evaluated_count']) is int and trace['evaluated_count']==len(wanted['subsets']),
        'feasible immediate subset enumeration incomplete or duplicated')
    for actual,expected in zip(trace['subsets'],wanted['subsets']):
        require(type(actual) is dict and set(actual)=={'hospitals','cost','selected_before_safety'}
            and actual['hospitals']==expected['hospitals']
            and type(actual['selected_before_safety']) is bool
            and actual['selected_before_safety']==expected['selected_before_safety'],
            'subset membership, order, tie or selected-before-safety marker differs')
        require(parent.close(actual['cost'],expected['cost']),
            'subset cost differs from independent first-acceptance probability arithmetic')
    require(trace['optimal_subset']==wanted['optimal_subset'] and trace['selected']==wanted['selected']
        and type(trace['forced']) is bool and trace['forced']==wanted['forced']
        and trace['safety_hospital']==wanted['safety_hospital'],
        'batch optimum, strict tie, safety exception or final current batch differs')
    require(all(parent.close(trace[key],wanted[key]) for key in ('minimum_cost','optimal_cost','selected_cost')),
        'batch optimal or safety cost differs from independent enumeration')
    require(output['send']==wanted['selected'] and evidence['sent_hospitals']==output['send']
        and evidence['policy']==NAME and output['next_review_at']==inputs['now']+15,
        'actual batch send, identity or unchanged review interval differs')
    plan=evidence['candidate_plan']
    require(type(plan) is list and [row['hospital'] for row in plan]==pool,
        'batch changed the original first-six planning pool')
    for actual,row in zip(plan,model['pool']):
        require(actual['planned_offset_seconds']==(0 if row['hospital'] in wanted['selected'] else None)
            and actual['planned_offset_seconds'] is not False
            and actual['travel_seconds']==row['travel_seconds'] and actual['inquiry_cost']==15,
            'batch contains a future plan, changed travel or changed contact cost')
    base=expected_first_accept_cost(model['current'],model['cells'],model['horizon'])
    require(evidence['horizon_seconds']==model['horizon'] and evidence['lambda_seconds']==15
        and evidence['congestion_aware'] is False and evidence['own_waiting_count']==model['fresh']
        and evidence['own_observation_timed_out_count']==len(inputs['patient_pending'])-model['fresh']
        and evidence['expected_cost_without_new']==round(base,3)
        and evidence['planned_expected_cost']==round(wanted['selected_cost'],3),
        'batch changed original horizon, contact cost, stale convention or cost summary')
    if inputs['max_new']==0:reason='contact_budget'
    elif output['send']:reason='eat_initial' if not inputs['patient_pending'] else 'eat_hedge'
    elif not inputs['candidates']:reason='no_candidates'
    elif model['room']<=0:reason='eat_concurrency_cap'
    else:reason='eat_wait'
    require(output['reason']==reason,'current batch action reason differs')
    return len(wanted['subsets'])


def _audit_calls_batch(scenario,branch):
    events=branch['db_events'];calls=branch['policy_calls'];decisions=branch['db_decisions']
    require(type(calls) is list and len(calls)==len(decisions),'policy call/DB decision coverage differs')
    require(all(type(row['id']) is int and row['id']>0 for row in events)
        and [row['id'] for row in events]==sorted({row['id'] for row in events}),
        'DB event identities not unique and ordered')
    event_ids={event['id'] for event in events};specs={p['id']:p for p in scenario['patients']}
    inquiry_events=[event for event in events if event['type']=='inquiry_sent']
    actual_sends=Counter();declared_sends=Counter();changed=0;last_decision=0;last_prefix=0
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
        if call['name']==NAME:changed+=audit_batch(inputs,output)
        elif call['name']==RESIDUAL_NAME:
            require('batch' not in output['evidence'],'residual reference contains current-batch intervention')
            residual.audit_residual(inputs,output)
        else:require('residual_queue' not in output['evidence'] and 'shortlist' not in output['evidence']
            and 'batch' not in output['evidence'],'reference policy contains batch/residual intervention')
    for event in inquiry_events:
        if event['patient_id'] in specs:actual_sends[event['id']]+=1
    require(declared_sends==actual_sends,'actual sends not covered exactly once by policy calls')
    return dict(policy_calls=len(calls),immediate_subsets_checked=changed)


def audit_branch(scenario,branch):
    """Audit a complete saved branch without importing/executing any policy."""
    try:
        environment=hashlib.sha256(json.dumps(scenario,ensure_ascii=False,sort_keys=True,
            separators=(',',':')).encode('utf-8')).hexdigest()
        require(branch['environment_sha256']==environment,'raw scenario/environment binding differs')
        require((branch['policy'],branch['rule']) in ((NAME,'immediate'),(RESIDUAL_NAME,'immediate'),
            (BASE_POLICY,'immediate'),(BASE_POLICY,'hold120'),('fixed_3','immediate'),('v3_w45_h900','immediate')),
            'unknown or combined comparison intervention')
        require(branch['instrumentation']==dict(original_runner='inquiry_lab.runner._branch',
            actual_workflow='server.inquiry_workflow.run_once',record_only=True,epoch=EPOCH,
            policy_inputs_unchanged=True),'record-only adapter contract differs')
        calls=_audit_calls_batch(scenario,branch);accounting=_audit_trace_fixed(scenario,branch)
        return dict(passed=True,**calls,**accounting,policy_kernel_independently_reexecuted=False,
            residual_queue_math_independently_reconstructed=True,
            immediate_subset_costs_independently_reconstructed=True,
            current_batch_enumeration_independently_reconstructed=True,optimality_claimed=False,
            scope='saved public event-prefix ledger, residual queue and acceptance-distribution math, every feasible current subset and safety exception, actual FIFO/response/confirmation/accounting; current fixed-model batch only, no global or clinical optimum')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError) as error:
        raise BatchAuditError('malformed saved current-batch artifact') from error


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
