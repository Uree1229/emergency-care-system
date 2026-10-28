"""Independent public-plan-context/veto audit on complete immutable raw.

The current event prefix and the strictly earlier committed decision prefix
reconstruct every context row. Only standard library and SHA-bound independent
auditors run; no policy/server/planning kernel is imported. The guard's recorded
proposal/utility math is audited by its frozen auditor. A future plan is an
observed, expiring intention, not an actual reservation or a success guarantee.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

from . import guard_audit as guard

GUARD_AUDITOR_SHA256='6a8607bd247d819699e69897eebca40d0e9c5b4ab7d253cb6cd86b049c3b1d3f'
SOURCE_GUARD_SHA256='8a3af35193c1bc97e09677fa9025758d5d51aed7562df99e23f7edd1dd8024dc'
ROOT=Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(guard.__file__).read_bytes()).hexdigest()!=GUARD_AUDITOR_SHA256:
    raise ValueError('frozen guard independent auditor differs')
if hashlib.sha256((ROOT/'inquiry_lab/guard_policy.py').read_bytes()).hexdigest()!=SOURCE_GUARD_SHA256:
    raise ValueError('frozen guard producer source differs')
batch,residual,parent=guard.batch,guard.residual,guard.parent
IntentAuditError=guard.GuardAuditError
require,number,EPOCH=guard.require,guard.number,guard.EPOCH
NAME='eat_intent_l15_f';BASE_POLICY=guard.BASE_POLICY
RESIDUAL_NAME=guard.RESIDUAL_NAME;GUARD_NAME=guard.NAME;BATCH_NAME=guard.BATCH_NAME
RULE='veto_guard_swap_on_public_intent_overlap'
INPUT_KEYS,CALL_KEYS=guard.INPUT_KEYS,guard.CALL_KEYS
_ledger,_ranked,_contains_private=guard._ledger,guard._ranked,guard._contains_private
_audit_trace_fixed=guard._audit_trace_fixed
POSITIVE_OFFSETS={15,30,60,90,120,180}
NEW_INSTRUMENTATION=dict(original_runner='inquiry_lab.runner._branch',
    actual_workflow='server.inquiry_workflow.run_once',record_only=False,epoch=EPOCH,
    original_policy_inputs_unchanged=True,public_plan_context_added=True,
    current_patient_source='snapshot.patient_id via ContextVar around unchanged _apply')


def context_from_prefix(events,decisions,now,patient,event_prefix,decision_prefix):
    """Rebuild filtered positive/uncontacted TOP plans, never nested proposals."""
    require(type(event_prefix) is int and event_prefix>=0
        and type(decision_prefix) is int and decision_prefix>=0,'invalid context prefix identities')
    _,pending,_,_=_ledger(events,now,patient,event_prefix)
    owners={row['patient'] for row in pending if row['patient']!=patient}
    contacted={(row['patient_id'],row['hospital_id']) for row in events
        if row['id']<=event_prefix and row['at']<=now and row['type'] in ('inquiry_sent','inquiry_retried')}
    latest={}
    for row in decisions:
        if row['id']<=decision_prefix and row['patient_id'] in owners:
            if row['patient_id'] not in latest or row['id']>latest[row['patient_id']]['id']:
                latest[row['patient_id']]=row
    plans=[]
    for row in sorted(latest.values(),key=lambda row:row['id']):
        require(type(row['at']) is int and 0<=row['at']<=now,'context references a future decision clock')
        if now-row['at']>15:continue
        candidate_plan=row['evidence'].get('candidate_plan',[])
        require(type(candidate_plan) is list,'invalid original top candidate plan')
        filtered=[]
        for candidate in candidate_plan:
            require(type(candidate) is dict and type(candidate['hospital']) is str
                and bool(candidate['hospital']),'invalid original top-plan hospital')
            offset=candidate['planned_offset_seconds']
            require(offset is None or type(offset) is int and offset in POSITIVE_OFFSETS|{0},
                'context source offset differs from the original planning grid')
            if offset is None or offset==0:continue
            if (row['patient_id'],candidate['hospital']) not in contacted:
                filtered.append(dict(hospital=candidate['hospital'],planned_offset_seconds=offset))
        if filtered:
            plans.append(dict(patient=row['patient_id'],decision_id=row['id'],at=row['at'],candidate_plan=filtered))
    return dict(patient_id=patient,event_prefix_max=event_prefix,
        decision_prefix_max=decision_prefix,plans=plans)


def audit_context(inputs,context,patient,events,decisions,event_prefix,previous_decision,current_decision):
    require(type(context) is dict and set(context)=={'patient_id','event_prefix_max','decision_prefix_max','plans'}
        and context['patient_id']==patient
        and type(context['event_prefix_max']) is int and context['event_prefix_max']==event_prefix
        and type(context['decision_prefix_max']) is int
        and context['decision_prefix_max']==previous_decision<current_decision,
        'context identity/prefix differs from the current caller or strictly prior committed decision')
    wanted=context_from_prefix(events,decisions,inputs['now'],patient,event_prefix,previous_decision)
    require(context==wanted,'public context differs from pending owners, latest committed TOP plans, TTL or inquiry events')
    require(type(context['plans']) is list,'invalid context plans type')
    for row in context['plans']:
        require(type(row) is dict and set(row)=={'patient','decision_id','at','candidate_plan'}
            and type(row['decision_id']) is int and row['decision_id']>0
            and type(row['at']) is int and type(row['candidate_plan']) is list and bool(row['candidate_plan']),
            'invalid public context row contract')
        require(all(type(p) is dict and set(p)=={'hospital','planned_offset_seconds'}
            and type(p['planned_offset_seconds']) is int and p['planned_offset_seconds'] in POSITIVE_OFFSETS
            for p in row['candidate_plan']),'invalid filtered context plan rows')
    return len(wanted['plans'])


INTENT_FIELDS={'schema_version','rule','source_guard_sha256','guard_output','public_context',
    'guard_applied','added_hospitals','protected_hospitals','conflicts','vetoed','core_origin'}


def audit_intent(inputs,output):
    """Audit the veto and exact selected core after context reconstruction."""
    core_inputs={key:inputs[key] for key in INPUT_KEYS};context=inputs['public_context']
    metadata=output['evidence']['intent']
    require(type(metadata) is dict and set(metadata)==INTENT_FIELDS
        and type(metadata['schema_version']) is int and metadata['schema_version']==1
        and metadata['rule']==RULE and metadata['source_guard_sha256']==SOURCE_GUARD_SHA256,
        'unknown public-intent intervention/source evidence contract')
    original=metadata['guard_output'];guard.audit_guard(core_inputs,original)
    proposal=original['evidence']['guard']['proposal'];applied=original['evidence']['guard']['applied']
    added=[hid for hid in original['send'] if hid not in proposal['send']]
    protected=sorted({p['hospital'] for row in context['plans'] for p in row['candidate_plan']})
    conflicts=[]
    for hid in added:
        for row in context['plans']:
            for plan in row['candidate_plan']:
                if plan['hospital']==hid:
                    conflicts.append(dict(hospital=hid,patient=row['patient'],decision_id=row['decision_id'],
                        at=row['at'],planned_offset_seconds=plan['planned_offset_seconds']))
    vetoed=applied and bool(conflicts);origin='residual_proposal' if vetoed else 'guard_output'
    require(metadata['public_context']==context and type(metadata['guard_applied']) is bool
        and metadata['guard_applied']==applied and metadata['added_hospitals']==added
        and metadata['protected_hospitals']==protected and metadata['conflicts']==conflicts
        and type(metadata['vetoed']) is bool and metadata['vetoed']==vetoed
        and metadata['core_origin']==origin,
        'public intent overlap, added-only membership, veto or selected source core differs')
    core=proposal if vetoed else original
    require(type(output) is dict and set(output)==set(core)
        and all(output[key]==core[key] for key in core if key!='evidence')
        and type(output['evidence']) is dict and set(output['evidence'])==set(core['evidence'])|{'intent'}
        and output['evidence']['policy']==NAME
        and all(output['evidence'][key]==value for key,value in core['evidence'].items() if key!='policy'),
        'public intent veto changed more than policy identity and the selected original core')
    return dict(intent_context_plans_checked=len(context['plans']),intent_guard_applied=int(applied),
        intent_conflicts_checked=len(conflicts),intent_vetoed=int(vetoed))


def _audit_calls_intent(scenario,branch):
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
        expected_inputs=INPUT_KEYS|{'public_context'} if call['name']==NAME else INPUT_KEYS
        require(type(inputs) is dict and set(inputs)==expected_inputs and not _contains_private(inputs),
            'policy received hidden, future or unknown context fields')
        now=EPOCH+at
        require(inputs['now']==now and call['name']==branch['policy']==decision['policy_name']
            and decision['at']==now and decision['patient_id']==pid
            and type(decision['id']) is int and decision['id']==call['db_decision_id']>last_decision,
            'policy call identity/clock differs from actual DB decision')
        previous_decision=last_decision
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
        if call['name']==NAME:
            audit_context(inputs,inputs['public_context'],pid,events,decisions,prefix,previous_decision,decision['id'])
            changed.update(audit_intent(inputs,output))
        elif call['name']==GUARD_NAME:
            require('intent' not in output['evidence'],'guard reference contains public-intent intervention')
            guard.audit_guard(inputs,output)
        elif call['name']==BATCH_NAME:
            require('intent' not in output['evidence'] and 'guard' not in output['evidence'],
                'batch reference contains public-intent/guard intervention')
            batch.audit_batch(inputs,output)
        elif call['name']==RESIDUAL_NAME:
            require(all(key not in output['evidence'] for key in ('intent','guard','batch')),
                'residual reference contains public-intent/guard/batch intervention')
            residual.audit_residual(inputs,output)
        else:require(all(key not in output['evidence'] for key in ('intent','residual_queue','shortlist','batch','guard')),
            'reference policy contains public-intent/guard/batch/residual intervention')
    for event in inquiry_events:
        if event['patient_id'] in specs:actual_sends[event['id']]+=1
    require(declared_sends==actual_sends,'actual sends not covered exactly once by policy calls')
    return dict(policy_calls=len(calls),**{key:changed[key] for key in
        ('intent_context_plans_checked','intent_guard_applied','intent_conflicts_checked','intent_vetoed')})


def audit_branch(scenario,branch):
    """Audit current public coordination evidence and actual stored workflow."""
    try:
        environment=hashlib.sha256(json.dumps(scenario,ensure_ascii=False,sort_keys=True,
            separators=(',',':')).encode('utf-8')).hexdigest()
        require(branch['environment_sha256']==environment,'raw scenario/environment binding differs')
        require((branch['policy'],branch['rule']) in ((NAME,'immediate'),(RESIDUAL_NAME,'immediate'),
            (GUARD_NAME,'immediate'),(BATCH_NAME,'immediate'),(BASE_POLICY,'immediate'),
            (BASE_POLICY,'hold120'),('fixed_3','immediate'),('v3_w45_h900','immediate')),
            'unknown or combined public-intent comparison intervention')
        expected=NEW_INSTRUMENTATION if branch['policy']==NAME else dict(
            original_runner='inquiry_lab.runner._branch',actual_workflow='server.inquiry_workflow.run_once',
            record_only=True,epoch=EPOCH,policy_inputs_unchanged=True)
        require(branch['instrumentation']==expected,'public-context adapter or unchanged reference instrumentation differs')
        calls=_audit_calls_intent(scenario,branch);accounting=_audit_trace_fixed(scenario,branch)
        return dict(passed=True,**calls,**accounting,policy_kernel_independently_reexecuted=False,
            public_context_independently_reconstructed=True,
            public_context_scope='other currently pending owners, latest prior committed TOP plan, inclusive original 15s review TTL, positive original offsets, no previous inquiry; observed intention only',
            veto_and_core_preservation_independently_reconstructed=True,
            original_guard_search_independently_reexecuted=False,optimality_claimed=False,
            scope='current event and strictly prior decision prefixes, filtered public plan view and explicit caller ID, bound recorded guard math, added-only overlap veto and exact R/guard core, actual FIFO/response/confirmation/accounting; extra public coordination input, no reservation or outcome guarantee')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise IntentAuditError('malformed saved public-intent artifact') from error


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

