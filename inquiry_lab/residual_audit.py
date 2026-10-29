"""Standalone residual-queue audit on complete immutable server workflow raw.

Only the standard library and the SHA-bound independent parent auditors are
used. No policy, planning kernel, replay engine or solver is imported/executed.
The actual workflow phase correction is reused exactly; the only new policy
checks reproduce the residual queue mathematics and preserve the first-six
travel-ordered pool. Kernel coordinate descent is source-bound, not solved here.
"""
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from . import shortlist_audit as parent

ORIGINAL_AUDITOR_SHA256='1da093ede0b75808fd0f9dc8f3d968b8a5f2524272700e45268b45f9aa1f2dd3'
FIXED_TRACE_SHA256='7e76ae3e3f9ef551d9934ca879652df48c91b93253731c49fbe8828d7062922d'
ROOT=Path(__file__).resolve().parents[1]
FIXED_TRACE_PATH=ROOT/'.codex-build/eat-shortlist-20261020/audit-addendum/audit_fixed.py'
if hashlib.sha256(Path(parent.__file__).read_bytes()).hexdigest()!=ORIGINAL_AUDITOR_SHA256:
    raise ValueError('frozen original raw auditor differs')
if hashlib.sha256(FIXED_TRACE_PATH.read_bytes()).hexdigest()!=FIXED_TRACE_SHA256:
    raise ValueError('fixed actual workflow phase auditor differs')
_spec=importlib.util.spec_from_file_location('frozen_residual_workflow_trace',FIXED_TRACE_PATH)
_fixed=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_fixed)
ResidualAuditError=parent.ShortlistAuditError
require,number,EPOCH=parent.require,parent.number,parent.EPOCH
NAME='eat_residual_l15_f';BASE_POLICY='eat_l15_f'
INPUT_KEYS,CALL_KEYS=parent.INPUT_KEYS,parent.CALL_KEYS
_ledger,_ranked,_contains_private=parent._ledger,parent._ranked,parent._contains_private
_audit_trace_fixed=_fixed._audit_trace_fixed

def _log_sf(z):
    """Independent stable normal survival; asymptotic tail avoids erfc zero."""
    if z<8:return math.log(.5*math.erfc(z/math.sqrt(2)))
    inverse=1/(z*z);term=1.;total=1.;previous=math.inf
    for index in range(1,200):
        term*=-(2*index-1)*inverse
        if abs(term)>=previous:break
        total+=term;previous=abs(term)
        if abs(term)<=abs(total)*1e-16:break
    return -.5*z*z-math.log(z)-.5*math.log(2*math.pi)+math.log(total)


def expected_remaining(age,mu,sigma,reply):
    """E[unfinished head work | no reply yet], including silent completions."""
    number(age,'head age');number(sigma,'service sigma');number(reply,'reply probability')
    require(sigma>0 and reply<=1 and type(mu) in (int,float) and math.isfinite(mu),
        'invalid residual distribution parameters')
    log_mean=mu+.5*sigma*sigma
    if age==0:return math.exp(log_mean)
    z=(math.log(age)-mu)/sigma;log_sf=_log_sf(z)
    log_first=log_mean+_log_sf(z-sigma);log_second=math.log(age)+log_sf
    require(log_second<=log_first+1e-10,'negative independently computed head work')
    if log_second>=log_first:return 0.
    log_numerator=log_first+math.log(-math.expm1(log_second-log_first))
    if reply==0:log_denominator=0.
    elif reply==1:log_denominator=log_sf
    else:
        left,right=math.log1p(-reply),math.log(reply)+log_sf
        upper=max(left,right);log_denominator=upper+math.log1p(math.exp(min(left,right)-upper))
    return math.exp(log_numerator-log_denominator)


RESIDUAL_FIELDS={'hospital','queue_count','assumed_workers','head','prior_terminal_reply',
    'estimated_start_at','age_seconds','service_mu','service_sigma','mean_service_seconds',
    'reply_probability','head_expected_remaining_seconds','queued_behind_count',
    'estimated_wait_seconds','original_full_wait_seconds'}


def residual_rows(inputs):
    """Fixed original first-six pool; current public pending/history only."""
    now=inputs['now'];history=[{key:row[key] for key in ('hospital','submitted_at','observed_at','outcome')}
        for row in inputs['observations']]
    beliefs=parent._beliefs(history,now)
    pool=_ranked(inputs['candidates'])[:min(6,inputs['max_new'])]
    rows=[]
    for candidate in pool:
        hid=candidate['hospital'];reply,_,mu,sigma=beliefs(hid);mean=math.exp(mu+.5*sigma*sigma)
        queue=[row for row in inputs['pending'] if row['hospital']==hid]
        head=min(queue,key=lambda row:(row['submitted_at'],type(row['patient']).__name__,str(row['patient']))) if queue else None
        terminal=None;start=None;age=None;remaining=0.;behind=0;wait=0.
        if head is not None:
            head={key:head[key] for key in ('hospital','patient','submitted_at')}
            previous=[row for row in history if row['hospital']==hid and row['outcome'] in ('accept','decline')
                and row['submitted_at']<head['submitted_at']]
            if previous:terminal=max(previous,key=lambda row:(row['observed_at'],row['submitted_at'],row['outcome']))
            start=max(head['submitted_at'],terminal['observed_at'] if terminal is not None else head['submitted_at'])
            age=now-start;remaining=expected_remaining(age,mu,sigma,reply);behind=len(queue)-1
            wait=remaining+behind*mean
        rows.append(dict(hospital=hid,queue_count=len(queue),assumed_workers=1,head=head,
            prior_terminal_reply=terminal,estimated_start_at=start,age_seconds=age,service_mu=mu,
            service_sigma=sigma,mean_service_seconds=mean,reply_probability=reply,
            head_expected_remaining_seconds=remaining,queued_behind_count=behind,
            estimated_wait_seconds=wait,original_full_wait_seconds=len(queue)*mean))
    return rows


def audit_residual(inputs,output):
    evidence=output['evidence'];trace=evidence['residual_queue'];expected=residual_rows(inputs)
    require(type(trace) is dict and set(trace)=={'schema_version','rule','hospitals'}
        and type(trace['schema_version']) is int and trace['schema_version']==1
        and trace['rule']=='head_residual_plus_full_followers','unknown residual queue trace contract')
    require(type(trace['hospitals']) is list and len(trace['hospitals'])==len(expected),
        'residual trace omitted or added original pool candidates')
    discrete={'hospital','queue_count','assumed_workers','head','prior_terminal_reply',
        'estimated_start_at','age_seconds','queued_behind_count'}
    for actual,wanted in zip(trace['hospitals'],expected):
        require(type(actual) is dict and set(actual)==RESIDUAL_FIELDS,'unknown residual candidate evidence fields')
        require(all(actual[key]==wanted[key] for key in discrete)
            and all(type(actual[key]) is int for key in ('queue_count','assumed_workers','queued_behind_count')),
            'residual queue head, current-age start, worker assumption or original order differs')
        require(all(actual[key] is None if wanted[key] is None else
            type(actual[key]) in (int,float) and math.isfinite(actual[key])
            for key in ('estimated_start_at','age_seconds')),'invalid residual public clock types')
        require(all(parent.close(actual[key],wanted[key]) for key in RESIDUAL_FIELDS-discrete),
            'residual wait differs from independent public-information distribution arithmetic')
    pool=[row['hospital'] for row in expected]
    require([row['hospital'] for row in evidence['candidate_plan']]==pool,
        'residual intervention altered the original first-six travel-ordered planning pool')
    require(all(hid in pool for hid in output['send']) and evidence['sent_hospitals']==output['send']
        and evidence['policy']==NAME and output['next_review_at']==inputs['now']+15,
        'residual send, policy identity or original review timing differs')
    require('shortlist' not in evidence,'rejected shortlist policy was combined with residual correction')
    return len(expected)


def _audit_calls_residual(scenario,branch):
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
        if call['name']==NAME:changed+=audit_residual(inputs,output)
        else:require('residual_queue' not in output['evidence'] and 'shortlist' not in output['evidence'],'reference policy contains residual intervention')
    for event in inquiry_events:
        if event['patient_id'] in specs:actual_sends[event['id']]+=1
    require(declared_sends==actual_sends,'actual sends not covered exactly once by policy calls')
    return dict(policy_calls=len(calls),residual_queue_candidates_checked=changed)

def audit_branch(scenario,branch):
    """Audit complete current workflow evidence without executing any policy."""
    try:
        environment=hashlib.sha256(json.dumps(scenario,ensure_ascii=False,sort_keys=True,
            separators=(',',':')).encode('utf-8')).hexdigest()
        require(branch['environment_sha256']==environment,'raw scenario/environment binding differs')
        require((branch['policy'],branch['rule']) in ((NAME,'immediate'),(BASE_POLICY,'immediate'),
            (BASE_POLICY,'hold120'),('fixed_3','immediate'),('v3_w45_h900','immediate')),
            'unknown or combined comparison intervention')
        require(branch['instrumentation']==dict(original_runner='inquiry_lab.runner._branch',
            actual_workflow='server.inquiry_workflow.run_once',record_only=True,epoch=EPOCH,
            policy_inputs_unchanged=True),'record-only adapter contract differs')
        calls=_audit_calls_residual(scenario,branch);accounting=_audit_trace_fixed(scenario,branch)
        return dict(passed=True,**calls,**accounting,policy_kernel_independently_reexecuted=False,
            residual_queue_math_independently_reconstructed=True,optimality_claimed=False,
            scope='saved current event-prefix ledger, residual queue math, unchanged first-six pool, actual FIFO/response/confirmation/accounting; unchanged planning kernel source-bound elsewhere')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError) as error:
        raise ResidualAuditError('malformed saved residual artifact') from error

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
