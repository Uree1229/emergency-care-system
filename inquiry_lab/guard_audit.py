"""Independent current-membership guard audit on immutable full workflow raw.

Only stdlib and SHA-bound independent auditors run. The recorded residual
proposal's queue math, plan cost, immediate sends, imminent capacity conflict,
every immediate batch cost, and same-cardinality replacement are checked here.
The unchanged residual coordinate-descent *choice* remains source-bound; it is
not independently optimized again. No producer/server/kernel is imported.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from . import batch_audit as batch

BATCH_AUDITOR_SHA256='d0f97ec65ceaa90eb620e8dbb6b4d8e02ce1c7adf2ef275d5479ae3fc179fc4d'
if hashlib.sha256(Path(batch.__file__).read_bytes()).hexdigest()!=BATCH_AUDITOR_SHA256:
    raise ValueError('frozen immediate-batch independent auditor differs')
residual=batch.residual;parent=batch.parent
GuardAuditError=batch.BatchAuditError
require,number,EPOCH=batch.require,batch.number,batch.EPOCH
NAME='eat_guard_l15_f'
RESIDUAL_NAME=batch.RESIDUAL_NAME
BATCH_NAME=batch.NAME
BASE_POLICY=batch.BASE_POLICY
RULE='same_count_imminent_capacity_guard'
INPUT_KEYS,CALL_KEYS=batch.INPUT_KEYS,batch.CALL_KEYS
_ledger,_ranked,_contains_private=batch._ledger,batch._ranked,batch._contains_private
_audit_trace_fixed=batch._audit_trace_fixed
ROOT=Path(__file__).resolve().parents[1]
SOURCE_SHA256=dict(
    residual_policy='8cf391ab5062de374d3c843edd3f6b1be98ac281df9217acc0538cbd98b838ef',
    batch_policy='91106a7d9cb7bf23baca77b6f826d303c662c0628ebd85f7be3ae33926d8d246')
for _name,_sha in SOURCE_SHA256.items():
    if hashlib.sha256((ROOT/'inquiry_lab'/(_name+'.py')).read_bytes()).hexdigest()!=_sha:
        raise ValueError('frozen guard source '+_name+' differs')

PROPOSAL_EVIDENCE_FIELDS={'evaluated_at','policy','horizon_seconds','lambda_seconds',
    'congestion_aware','expected_cost_without_new','planned_expected_cost',
    'reply_probability_global','accept_probability_global','service_median_seconds',
    'history_used_count','own_waiting_count','own_observation_timed_out_count',
    'unresolved_inquiry_count','candidate_plan','sent_hospitals','residual_queue'}


def proposal_plan(inputs,proposal):
    """Validate a recorded residual plan, not its coordinate-descent search."""
    require(type(proposal) is dict and set(proposal)=={'send','reason','evidence','next_review_at'},
        'invalid full original residual proposal')
    residual.audit_residual(inputs,proposal)
    evidence=proposal['evidence'];model=batch.batch_model(inputs)
    require(type(evidence) is dict and set(evidence)==PROPOSAL_EVIDENCE_FIELDS,
        'unknown full original residual evidence fields')
    require('guard' not in evidence and 'batch' not in evidence,'nested residual proposal contains another intervention')
    allowed={0,15,30,60,90,120,180};chosen=[]
    beliefs=parent._beliefs(inputs['observations'],inputs['now'])
    for row in evidence['candidate_plan']:
        require(type(row) is dict and set(row)=={'hospital','planned_offset_seconds','inquiry_cost',
            'reply_probability','accept_probability','queue_length','travel_seconds'},
            'invalid original residual candidate plan fields')
        hid=row['hospital'];offset=row['planned_offset_seconds']
        require(offset is None or type(offset) is int and offset in allowed and offset<model['horizon'],
            'residual plan offset differs from original legal grid')
        r,a,_,_=beliefs(hid)
        require(row['travel_seconds']==model['items'][hid][0] and row['inquiry_cost']==15
            and row['reply_probability']==round(r,4) and row['accept_probability']==round(a,4)
            and type(row['queue_length']) is int
            and row['queue_length']==sum(p['hospital']==hid for p in inputs['pending']),
            'residual proposal changed public travel, queue or original beliefs/cost')
        if offset is not None:chosen.append((offset,hid))
    send=[hid for offset,hid in chosen if offset==0]
    require(proposal['send']==send and len(send)<=max(0,model['room']),
        'residual proposal immediate send/cap differs from its recorded plan')
    items=list(model['current'])
    for offset,hid in chosen:
        travel,masses=model['items'][hid];shift=offset//5
        items.append((travel,[0.]*shift+masses[:model['cells']-shift] if shift else list(masses)))
    cost=batch.expected_first_accept_cost(items,model['cells'],model['horizon'])
    for offset,_ in chosen:
        probability=math.prod(max(0.,1-sum(masses[:offset//5])) for _,masses in items) if offset else 1.
        cost+=15*probability
    base=batch.expected_first_accept_cost(model['current'],model['cells'],model['horizon'])
    require(evidence['evaluated_at']==inputs['now'] and evidence['horizon_seconds']==model['horizon']
        and evidence['lambda_seconds']==15 and evidence['congestion_aware'] is False
        and evidence['own_waiting_count']==model['fresh']
        and evidence['own_observation_timed_out_count']==len(inputs['patient_pending'])-model['fresh']
        and evidence['expected_cost_without_new']==round(base,3)
        and evidence['planned_expected_cost']==round(cost,3),
        'residual proposal plan cost, horizon or current counts differ from independent math')
    require(type(evidence['history_used_count']) is int
        and evidence['history_used_count']==len(inputs['observations'])
        and type(evidence['unresolved_inquiry_count']) is int
        and evidence['unresolved_inquiry_count']==len(inputs['pending'])
        and all(type(evidence[key]) in (int,float) and math.isfinite(evidence[key])
            and 0<=evidence[key]<=1 for key in ('reply_probability_global','accept_probability_global'))
        and type(evidence['service_median_seconds']) in (int,float)
        and math.isfinite(evidence['service_median_seconds']) and evidence['service_median_seconds']>0,
        'invalid original proposal history/count or global summary types')
    if inputs['max_new']==0:reason='contact_budget'
    elif send:reason='eat_initial' if not inputs['patient_pending'] else 'eat_hedge'
    elif not inputs['candidates']:reason='no_candidates'
    elif model['room']<=0:reason='eat_concurrency_cap'
    else:reason='eat_wait'
    require(proposal['reason']==reason,'recorded residual proposal action reason differs')
    return model


def imminent_conflicts(inputs,proposal,model):
    """No-reply slot arithmetic only at proposed sends before next review."""
    plan=proposal['evidence']['candidate_plan']
    offsets=sorted({row['planned_offset_seconds'] for row in plan
        if row['planned_offset_seconds'] is not None and 0<row['planned_offset_seconds']<=15})
    checks=[]
    for offset in offsets:
        existing=[row['hospital'] for row in inputs['patient_pending']
            if inputs['now']+offset-row['submitted_at']<=180]
        scheduled=[row['hospital'] for row in plan if row['planned_offset_seconds'] is not None
            and row['planned_offset_seconds']<=offset and offset-row['planned_offset_seconds']<=180]
        checks.append(dict(offset_seconds=offset,existing_fresh_hospitals=existing,
            scheduled_fresh_hospitals=scheduled,fresh_own_count=len(existing),
            proposal_fresh_count=len(scheduled),total_fresh_count=len(existing)+len(scheduled),
            conflict=len(existing)+len(scheduled)>4))
    full=model['fresh']+len(proposal['send'])==4
    conflicts=[row['offset_seconds'] for row in checks if row['conflict']]
    return checks,conflicts,full,full and bool(conflicts)


GUARD_FIELDS={'schema_version','rule','source_sha256','proposal','review_window_seconds',
    'max_concurrent','fresh_own_count','proposal_send_count','current_planned_fresh_count',
    'currently_full','future_checks','conflict_offsets_seconds','triggered','batch_evaluated',
    'evaluation_skip_reason','batch_output','same_count_costs','same_count_minimum_cost',
    'best_same_count_hospitals','best_same_count_cost','proposal_immediate_cost',
    'improvement_seconds','applied','output_cost_scope'}


def audit_guard(inputs,output):
    evidence=output['evidence'];guard=evidence['guard']
    require(type(guard) is dict and set(guard)==GUARD_FIELDS
        and type(guard['schema_version']) is int and guard['schema_version']==1
        and guard['rule']==RULE and guard['source_sha256']==SOURCE_SHA256,
        'unknown guard evidence/source contract')
    proposal=guard['proposal'];model=proposal_plan(inputs,proposal)
    checks,conflicts,full,trigger=imminent_conflicts(inputs,proposal,model)
    count=len(proposal['send']);evaluated=trigger and count>0
    require(type(guard['review_window_seconds']) is int and guard['review_window_seconds']==15
        and type(guard['max_concurrent']) is int and guard['max_concurrent']==4
        and type(guard['fresh_own_count']) is int and guard['fresh_own_count']==model['fresh']
        and type(guard['proposal_send_count']) is int and guard['proposal_send_count']==count
        and type(guard['current_planned_fresh_count']) is int
        and guard['current_planned_fresh_count']==model['fresh']+count
        and type(guard['currently_full']) is bool and guard['currently_full']==full
        and type(guard['triggered']) is bool and guard['triggered']==trigger
        and type(guard['batch_evaluated']) is bool and guard['batch_evaluated']==evaluated,
        'guard trigger changed current full capacity or original next-review window')
    require(guard['future_checks']==checks and guard['conflict_offsets_seconds']==conflicts,
        'imminent future fresh-cap conflict differs from independent public-clock arithmetic')
    # Equality alone would accept False in place of zero integer counts.
    require(type(guard['future_checks']) is list and all(type(row) is dict
        and all(type(row[key]) is int for key in ('offset_seconds','fresh_own_count',
            'proposal_fresh_count','total_fresh_count')) and type(row['conflict']) is bool for row in guard['future_checks']),
        'invalid guard future-clock/count types')
    chosen=list(proposal['send']);applied=False;selected_cost=None;checked=0
    if evaluated:
        require(guard['evaluation_skip_reason'] is None,'evaluated guard declares skipped utility')
        utility=guard['batch_output'];checked=batch.audit_batch(inputs,utility)
        table=batch.enumerate_batches(inputs)['subsets']
        rows=[dict(hospitals=row['hospitals'],cost=row['cost']) for row in table if len(row['hospitals'])==count]
        require(type(guard['same_count_costs']) is list and len(guard['same_count_costs'])==len(rows),
            'same-count comparison omitted feasible current memberships')
        for actual,wanted in zip(guard['same_count_costs'],rows):
            require(type(actual) is dict and set(actual)=={'hospitals','cost'}
                and actual['hospitals']==wanted['hospitals'] and parent.close(actual['cost'],wanted['cost']),
                'same-count cost/order differs from complete independent batch table')
        minimum=min(row['cost'] for row in rows)
        best=next(row for row in rows if row['cost']<=minimum+1e-9)
        original=next(row for row in rows if row['hospitals']==proposal['send'])
        improvement=original['cost']-best['cost'];applied=improvement>1e-9
        require(guard['best_same_count_hospitals']==best['hospitals']
            and all(parent.close(guard[key],wanted) for key,wanted in (
                ('same_count_minimum_cost',minimum),('best_same_count_cost',best['cost']),
                ('proposal_immediate_cost',original['cost']),('improvement_seconds',improvement))),
            'same-cardinality optimum, strict improvement or original immediate cost differs')
        if applied:chosen=list(best['hospitals']);selected_cost=best['cost']
    else:
        require(guard['evaluation_skip_reason']==('zero_cardinality' if trigger else 'not_triggered')
            and guard['batch_output'] is None and guard['same_count_costs']==[]
            and all(guard[key] is None for key in ('same_count_minimum_cost','best_same_count_hospitals',
                'best_same_count_cost','proposal_immediate_cost','improvement_seconds')),
            'inactive/zero-cardinality guard executed a utility or fabricated cost evidence')
    require(type(guard['applied']) is bool and guard['applied']==applied,
        'guard applied without a strict same-count current-cost improvement')
    scope='immediate_same_count_batch' if applied else 'original_residual_future_plan'
    require(guard['output_cost_scope']==scope,'guard current/future cost scope differs')
    require(type(output) is dict and set(output)==set(proposal)
        and set(evidence)==set(proposal['evidence'])|{'guard'}
        and output['send']==chosen and len(chosen)==count
        and evidence['policy']==NAME and evidence['sent_hospitals']==chosen,
        'guard changed inquiry count, top-level contract or selected current membership')
    require(all(output[key]==proposal[key] for key in proposal if key not in ('send','evidence')),
        'guard changed original review timing or action reason')
    for key,value in proposal['evidence'].items():
        if key in ('policy','sent_hospitals'):continue
        if key=='candidate_plan' and applied:
            require(type(evidence[key]) is list and len(evidence[key])==len(value),'guard final plan omitted original pool')
            for actual,original in zip(evidence[key],value):
                require(type(actual) is dict and set(actual)==set(original)
                    and all(actual[field]==original[field] for field in original if field!='planned_offset_seconds')
                    and actual['planned_offset_seconds']==(0 if original['hospital'] in chosen else None)
                    and actual['planned_offset_seconds'] is not False,
                    'guard changed more than current batch membership in final plan')
        elif key=='planned_expected_cost' and applied:
            require(evidence[key]==round(selected_cost,3),'guard final cost does not describe current same-count batch')
        else:require(evidence[key]==value,'guard altered original residual core outside approved membership intervention')
    return dict(utility_subsets_checked=checked,guard_triggered=int(trigger),
        guard_batch_evaluated=int(evaluated),guard_applied=int(applied))


def _audit_calls_guard(scenario,branch):
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
        if call['name']==NAME:changed.update(audit_guard(inputs,output))
        elif call['name']==BATCH_NAME:
            require('guard' not in output['evidence'],'batch diagnostic reference contains guard intervention')
            batch.audit_batch(inputs,output)
        elif call['name']==RESIDUAL_NAME:
            require('guard' not in output['evidence'] and 'batch' not in output['evidence'],
                'residual reference contains guard/batch intervention')
            residual.audit_residual(inputs,output)
        else:require('residual_queue' not in output['evidence'] and 'shortlist' not in output['evidence']
            and 'batch' not in output['evidence'] and 'guard' not in output['evidence'],
            'reference policy contains guard/batch/residual intervention')
    for event in inquiry_events:
        if event['patient_id'] in specs:actual_sends[event['id']]+=1
    require(declared_sends==actual_sends,'actual sends not covered exactly once by policy calls')
    return dict(policy_calls=len(calls),**{key:changed[key] for key in
        ('utility_subsets_checked','guard_triggered','guard_batch_evaluated','guard_applied')})


def audit_branch(scenario,branch):
    """Audit a complete saved branch without executing a producer policy."""
    try:
        environment=hashlib.sha256(json.dumps(scenario,ensure_ascii=False,sort_keys=True,
            separators=(',',':')).encode('utf-8')).hexdigest()
        require(branch['environment_sha256']==environment,'raw scenario/environment binding differs')
        require((branch['policy'],branch['rule']) in ((NAME,'immediate'),(RESIDUAL_NAME,'immediate'),
            (BATCH_NAME,'immediate'),(BASE_POLICY,'immediate'),(BASE_POLICY,'hold120'),
            ('fixed_3','immediate'),('v3_w45_h900','immediate')),'unknown or combined comparison intervention')
        require(branch['instrumentation']==dict(original_runner='inquiry_lab.runner._branch',
            actual_workflow='server.inquiry_workflow.run_once',record_only=True,epoch=EPOCH,
            policy_inputs_unchanged=True),'record-only adapter contract differs')
        calls=_audit_calls_guard(scenario,branch);accounting=_audit_trace_fixed(scenario,branch)
        return dict(passed=True,**calls,**accounting,policy_kernel_independently_reexecuted=False,
            recorded_proposal_plan_cost_independently_reconstructed=True,
            residual_proposal_search_independently_reexecuted=False,
            guard_trigger_independently_reconstructed=True,
            same_count_batch_math_independently_reconstructed=True,optimality_claimed=False,
            scope='saved public ledger, recorded residual plan/cost consistency (search source-bound), next-review capacity conflict, full immediate batch table, strict same-count membership replacement, unchanged residual core, actual workflow/accounting; no global or clinical optimum')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise GuardAuditError('malformed saved guarded-membership artifact') from error


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
