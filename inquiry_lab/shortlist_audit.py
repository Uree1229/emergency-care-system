"""Standalone saved-raw shortlist audit using only the standard library.

Rebuilds the event-prefix observation ledger, shortlist mathematics and actual
FIFO/work/selection accounting.  It does not import or execute the producer,
server workflow, EAT kernel, scenario generator or any solver.  The unchanged
coordinate-descent kernel is source-bound elsewhere, not independently solved
by this auditor; no optimality or clinical correctness is claimed.
"""
from collections import Counter,deque
import hashlib
import json
import math

EPOCH=1_800_000_000
NAME='eat_shortlist_l15_f'
BASE_POLICY='eat_l15_f'
RULE='expected_single_addition_cost'
INPUT_KEYS={'now','candidates','observations','pending','patient_pending','max_new','last_contact_at'}
CALL_KEYS={'patient_id','at','name','inputs','output','db_decision_id','db_event_prefix_max'}


class ShortlistAuditError(ValueError):pass


def require(condition,message):
    if not condition:raise ShortlistAuditError(message)


def number(value,label,minimum=0):
    require(type(value) in (int,float) and math.isfinite(value) and value>=minimum,'invalid '+label)
    return value


def close(left,right):
    return type(left) in (int,float) and math.isfinite(left) and math.isclose(left,right,rel_tol=1e-10,abs_tol=1e-7)


def _data(event):
    value=event.get('data',{})
    return json.loads(value) if isinstance(value,str) else value


def _ledger(events,now,pid,prefix):
    """Reconstruct once-only first replies and the original 180-second censor."""
    latest={};observations=[]
    def finish(attempt):
        submitted,first,ended=attempt['submitted_at'],attempt['first'],attempt['ended']
        threshold=submitted+180
        if first is not None and first[0]<=threshold:observed,outcome=first
        elif threshold<=now and (ended is None or ended>=threshold):observed,outcome=threshold,'timeout'
        else:return
        if now-1800<observed<=now:
            observations.append(dict(hospital=attempt['hospital'],patient=attempt['patient'],
                submitted_at=submitted,observed_at=observed,outcome=outcome))
    for event in events:
        if event['id']>prefix or event['at']>now:continue
        typ=event['type'];patient=event.get('patient_id');hospital=event.get('hospital_id')
        if not patient or not hospital:continue
        key=patient,hospital;data=_data(event);at=event['at']
        if typ in ('inquiry_sent','inquiry_retried'):
            if key in latest:
                if latest[key]['ended'] is None:latest[key]['ended']=at
                finish(latest[key])
            latest[key]=dict(hospital=hospital,patient=patient,submitted_at=at,
                inquiry_id=data.get('inquiry_id'),first=None,ended=None,unresolved=True)
        elif typ in ('inquiry_responded','inquiry_question','inquiry_cancelled','inquiry_closed'):
            attempt=latest.get(key)
            if attempt is None or at<attempt['submitted_at'] or not attempt['unresolved'] \
                    or (data.get('inquiry_id') is not None and data['inquiry_id']!=attempt['inquiry_id']):continue
            if typ in ('inquiry_responded','inquiry_question'):
                action=data.get('action')
                if typ=='inquiry_responded' and action not in ('checking','accept','decline'):continue
                if attempt['first'] is None:
                    attempt['first']=(at,action if action in ('accept','decline') else 'reply')
                if action in ('accept','decline'):attempt.update(ended=at,unresolved=False)
            else:attempt.update(ended=at,unresolved=False)
    pending=[]
    for attempt in latest.values():
        finish(attempt)
        if attempt['unresolved']:
            pending.append({k:attempt[k] for k in ('hospital','patient','submitted_at')})
    observations.sort(key=lambda row:(row['observed_at'],row['submitted_at'],row['hospital'],row['patient']))
    pending.sort(key=lambda row:(row['submitted_at'],row['hospital'],row['patient']))
    return observations,pending,[row for row in pending if row['patient']==pid],latest


def _cdf(x,mu,sigma):
    return 0. if x<=0 else .5*(1+math.erf((math.log(x)-mu)/(sigma*math.sqrt(2))))


def _beliefs(history,now):
    replies={};accepts={};services={};last_free={}
    for row in sorted(history,key=lambda r:(r['observed_at'],r['submitted_at'],r['hospital'])):
        h=row['hospital'];w=math.exp(-max(0.,now-row['observed_at'])/600.)
        count=replies.setdefault(h,[0.,0.]);count[1]+=w
        if row['outcome']=='timeout':continue
        count[0]+=w
        if row['outcome'] in ('accept','decline'):
            count=accepts.setdefault(h,[0.,0.]);count[1]+=w;count[0]+=w*(row['outcome']=='accept')
        start=max(row['submitted_at'],last_free.get(h,row['submitted_at']))
        services.setdefault(h,[]).append((w,math.log(max(1.,row['observed_at']-start))))
        last_free[h]=max(last_free.get(h,row['observed_at']),row['observed_at'])
    shrink=lambda success,total,prior,k:(k*prior+success)/(k+total)
    reply=shrink(sum(x[0] for x in replies.values()),sum(x[1] for x in replies.values()),.79,10.)
    accept=shrink(sum(x[0] for x in accepts.values()),sum(x[1] for x in accepts.values()),.40,4.)
    logs=[pair for values in services.values() for pair in values];weight=sum(w for w,_ in logs)
    mu=(math.log(66.)*3+sum(w*x for w,x in logs))/(3+weight)
    sigma=max(.25,math.sqrt((.70**2*3+sum(w*(x-mu)**2 for w,x in logs))/(3+weight)))
    def hospital(h):
        rc=replies.get(h,[0.,0.]);ac=accepts.get(h,[0.,0.]);values=services.get(h,[])
        hm=(mu*2+sum(w*x for w,x in values))/(2+sum(w for w,_ in values))
        return shrink(*rc,reply,3.),shrink(*ac,accept,2.),hm,sigma
    return hospital


def _masses(cdf,total,cells):
    out=[];previous=0.
    for k in range(1,cells+1):
        value=cdf(k*5);out.append(max(0.,(value-previous)*total));previous=value
    return out


def _expected_cost(items,cells,horizon):
    items=sorted(items,key=lambda row:row[0]);survival=[1.]*len(items);total=0.
    for k in range(cells):
        alive=math.prod(survival)
        if alive<=1e-12:return total
        none_nearer=1.
        for index,(travel,masses) in enumerate(items):
            hazard=min(1.,masses[k]/survival[index]) if survival[index]>1e-12 else 0.
            if hazard>0:
                total+=alive*none_nearer*hazard*((k+.5)*5+travel);none_nearer*=1-hazard
        survival=[max(0.,s-items[index][1][k]) for index,s in enumerate(survival)]
    return total+math.prod(survival)*(horizon+1500)


def shortlist_scores(inputs):
    """Independent fixed EAT prior/queue/survival math for shortlist scores."""
    now=inputs['now'];own=inputs['patient_pending'];history=inputs['observations'];pending=inputs['pending']
    beliefs=_beliefs(history,now);queue={};last_reply={}
    for row in pending:queue.setdefault(row['hospital'],[]).append(row)
    for row in history:
        if row['outcome']!='timeout':last_reply.setdefault(row['hospital'],[]).append(row)
    known=[row['submitted_at'] for row in own]
    if inputs['last_contact_at'] is not None:known.append(inputs['last_contact_at'])
    elapsed=now-min(known) if known else 0
    horizon=max(15,300-elapsed);cells=max(3,int(math.ceil(horizon/5)))
    current=[]
    for row in own:
        hid,submitted=row['hospital'],row['submitted_at'];reply,accept,mu,sigma=beliefs(hid)
        ahead=sum(1. if other['submitted_at']<submitted else .5 for other in queue.get(hid,[])
            if other['submitted_at']<=submitted and other['patient']!=row['patient'])
        if ahead>0:
            wait=ahead*math.exp(mu+sigma*sigma/2)
            masses=_masses(lambda x:_cdf(x-wait,mu,sigma),reply*accept,cells)
        else:
            freed=[other['observed_at'] for other in last_reply.get(hid,[]) if other['submitted_at']<=submitted]
            spent=max(0.,now-max([submitted,*freed]));f0=_cdf(spent,mu,sigma);alive=max(1e-9,1-f0)
            left=reply*alive/max(1e-9,1-reply*f0)
            masses=_masses(lambda x:(_cdf(spent+x,mu,sigma)-f0)/alive,left*accept,cells)
        current.append((row['travel_seconds'],masses))
    rows=[]
    for row in _ranked(inputs['candidates']):
        hid=row['hospital'];reply,accept,mu,sigma=beliefs(hid)
        wait=len(queue.get(hid,[]))*math.exp(mu+sigma*sigma/2)
        item=(row['travel_seconds'],_masses(lambda x:_cdf(x-wait,mu,sigma),reply*accept,cells))
        cost=_expected_cost(current+[item],cells,horizon)
        rows.append(dict(hospital=hid,travel_seconds=row['travel_seconds'],expected_arrival_cost=cost,
            inquiry_cost=15,score=cost+15))
    return rows


def _ranked(candidates):
    rows=[];seen=set()
    for row in candidates:
        require(type(row) is dict and 'hospital_id' in row and type(row.get('travel')) is dict,
            'invalid current candidate shape')
        hid=row['hospital_id'];seconds=row['travel']['duration_seconds']
        require(type(hid) is str and hid and hid not in seen,'duplicate or invalid candidate hospital')
        number(seconds,'candidate travel');seen.add(hid)
        rows.append(dict(hospital=hid,travel_seconds=seconds))
    return rows


def audit_shortlist(inputs,output):
    evidence=output['evidence'];trace=evidence['shortlist'];rows=shortlist_scores(inputs)
    require(type(trace) is dict and set(trace)=={'rule','limit','scores','selected','original_nearest',
        'membership_changed'} and trace['rule']==RULE,'unknown shortlist trace schema')
    limit=min(6,inputs['max_new'])
    require(type(trace['limit']) is int and trace['limit']==limit,'shortlist size limit changed')
    ids={row['hospital'] for row in sorted(rows,key=lambda row:(row['score'],row['travel_seconds'],row['hospital']))[:limit]}
    selected=[row['hospital'] for row in rows if row['hospital'] in ids]
    require(type(trace['scores']) is list and len(trace['scores'])==len(rows),'shortlist omitted score rows')
    for actual,expected in zip(trace['scores'],rows):
        require(type(actual) is dict and set(actual)==set(expected)|{'selected'},'unknown shortlist score fields')
        require(actual['hospital']==expected['hospital'] and actual['travel_seconds']==expected['travel_seconds']
            and type(actual['selected']) is bool and actual['selected']==(actual['hospital'] in ids),
            'shortlist input order, travel or membership differs')
        require(all(close(actual[key],expected[key]) for key in ('score','expected_arrival_cost','inquiry_cost')),
            'shortlist score differs from independent current-information math')
    original=[row['hospital'] for row in rows[:limit]]
    require(trace['selected']==selected and trace['original_nearest']==original
        and type(trace['membership_changed']) is bool and trace['membership_changed']==(ids!=set(original)),
        'shortlist selected pool did not preserve original candidate coordinate order')
    require([row['hospital'] for row in evidence['candidate_plan']]==selected,
        'kernel planning pool differs from independently checked shortlist')
    require(all(hid in selected for hid in output['send']),'kernel sent outside checked shortlist')
    require(evidence['policy']==NAME and evidence['sent_hospitals']==output['send']
        and output['next_review_at']==inputs['now']+15,'new policy identity/review/send evidence differs')
    return int(trace['membership_changed'])


def _contains_private(value):
    if isinstance(value,dict):
        return any(str(key) in ('workers','potentials','no_response','service_seconds')
            or _contains_private(item) for key,item in value.items())
    return isinstance(value,list) and any(_contains_private(item) for item in value)


def _audit_calls(scenario,branch):
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
        if call['name']==NAME:changed+=audit_shortlist(inputs,output)
        else:require('shortlist' not in output['evidence'],'reference policy contains shortlist intervention')
    for event in inquiry_events:
        if event['patient_id'] in specs:actual_sends[event['id']]+=1
    require(declared_sends==actual_sends,'actual sends not covered exactly once by policy calls')
    return dict(policy_calls=len(calls),shortlist_membership_changes=changed)


def _audit_trace(scenario,branch):
    specs={p['id']:p for p in scenario['patients']};hospitals={h['id']:h for h in scenario['hospitals']}
    potentials={(p['patient_id'],p['hospital_id']):p for p in scenario['potentials']}
    events=branch['db_events'];trace=branch['trace'];queries={};queues={hid:deque() for hid in hospitals}
    require(all(type(h['workers']) is int and h['workers']>=1 for h in hospitals.values()),'invalid actual worker capacity')
    slots={hid:[0]*hospital['workers'] for hid,hospital in hospitals.items()}
    active={};completed={};replies={};confirmed={};cancelled=set();work=0;last_at=-1;holds={};ended_holds=[]
    allowed={'queue_added','review_started','review_finished','review_cancelled_before_start',
        'reply_suppressed_after_cancel','reply_omitted','reply_accept','reply_decline',
        'destination_confirmed','confirmation_hold_started','confirmation_hold_ended'}
    db_sent={_data(event).get('inquiry_id'):event for event in events
        if event['type']=='inquiry_sent' and event.get('patient_id') in specs}
    db_queries={row['id']:row for row in branch['db_inquiries'] if row['patient_id'] in specs}
    require(len(db_queries)==len([row for row in branch['db_inquiries'] if row['patient_id'] in specs])
        and set(db_queries)==set(db_sent),'DB inquiry inventory differs from actual contact events')
    for iid,event in db_sent.items():
        require(db_queries[iid]['patient_id']==event['patient_id']
            and db_queries[iid]['hospital_id']==event['hospital_id'],'DB inquiry patient/hospital identity differs')
    def closed(iid,at):
        return any(event['at']<=EPOCH+at and _data(event).get('inquiry_id')==iid
            and event['type'] in ('inquiry_cancelled','inquiry_closed') for event in events)
    for row in trace:
        at=row['at'];typ=row['type'];pid=row['patient_id'];hid=row['hospital_id'];iid=row['inquiry_id']
        require(type(at) is int and at>=last_at and pid in specs and hid in hospitals and typ in allowed,
            'unknown, off-clock or reordered actual workflow event')
        last_at=at
        require(at>=specs[pid]['at'],'workflow used future patient')
        require(iid in db_sent and db_sent[iid]['patient_id']==pid and db_sent[iid]['hospital_id']==hid,
            'trace inquiry identity differs from actual DB contact')
        if typ=='queue_added':
            require(iid not in queries and db_sent[iid]['at']==EPOCH+at,'query duplicated or queued at wrong time')
            queries[iid]=pid,hid;queues[hid].append(iid)
        elif typ=='review_cancelled_before_start':
            require(iid in queues[hid] and closed(iid,at),'uncancelled query disappeared from FIFO queue')
            queues[hid].remove(iid);cancelled.add(iid)
        elif typ=='review_started':
            require(queues[hid] and queues[hid][0]==iid and not closed(iid,at),'FIFO order skipped or closed work started')
            require(sum(qhid==hid for _,qhid,_ in active.values())<hospitals[hid]['workers'],
                'hospital review worker capacity exceeded')
            duration=potentials[pid,hid]['service_seconds']
            slot=min(range(len(slots[hid])),key=lambda index:slots[hid][index])
            require(at==max(db_sent[iid]['at']-EPOCH,slots[hid][slot]),
                'hospital work was artificially delayed despite current FIFO/worker availability')
            slots[hid][slot]=at+duration
            require(row['service_seconds']==duration and row['due_at']==at+duration,
                'actual review used changed synthetic service work')
            queues[hid].popleft();active[iid]=(pid,hid,at);work+=duration
        elif typ=='review_finished':
            require(iid in active,'review finished without actual running job')
            _,_,started=active.pop(iid);duration=potentials[pid,hid]['service_seconds']
            require(row['started_at']==started and row['service_seconds']==duration and at==started+duration,
                'review completion omitted or changed actual FIFO work')
            completed[iid]=at
        elif typ.startswith('reply_'):
            require(completed.get(iid)==at and iid not in replies,'reply lacks unique current actual completion')
            potential=potentials[pid,hid]
            expected='reply_suppressed_after_cancel' if closed(iid,at) else 'reply_omitted' if potential['no_response'] \
                else 'reply_accept' if potential['accept'] else 'reply_decline'
            require(typ==expected,'actual hospital outcome differs from current open state/potential')
            replies[iid]=(typ,at)
            if typ in ('reply_accept','reply_decline'):
                require(any(event['type']=='inquiry_responded' and event['at']==EPOCH+at
                    and _data(event).get('inquiry_id')==iid and _data(event).get('action')==typ[6:]
                    for event in events),'actual reply missing its DB response')
        elif typ=='destination_confirmed':
            require(pid not in confirmed and iid in replies and replies[iid][0]=='reply_accept'
                and 0<=at-replies[iid][1]<600 and at<=specs[pid]['at']+300 and not closed(iid,at),
                'destination lacks live actual acceptance or violates search observation deadline')
            require(any(event['type']=='destination_confirmed' and event['at']==EPOCH+at
                and event['patient_id']==pid and event['hospital_id']==hid and _data(event).get('inquiry_id')==iid
                for event in events),'trace destination lacks actual DB confirmation')
            accepted=[(specs[pid]['travel_seconds'][queries[other][1]],queries[other][1])
                for other,(kind,received) in replies.items() if queries[other][0]==pid and kind=='reply_accept'
                and 0<=at-received<600 and not any(event['at']<EPOCH+at
                    and _data(event).get('inquiry_id')==other and event['type'] in ('inquiry_cancelled','inquiry_closed')
                    for event in events)]
            require(accepted and min(accepted)[1]==hid,'confirmation did not choose closest current live acceptance')
            if branch['rule']=='immediate':
                require(at==min(received for other,(kind,received) in replies.items()
                    if queries[other][0]==pid and kind=='reply_accept'),'immediate rule delayed an actual valid acceptance')
            confirmed[pid]=(at,hid)
        elif typ=='confirmation_hold_started':
            require(branch['rule']=='hold120' and pid not in holds and replies.get(iid)==('reply_accept',at),
                'hold start lacks actual first acceptance or valid unchanged rule')
            holds[pid]=dict(patient_id=pid,started_at=at,first_best=hid)
        elif typ=='confirmation_hold_ended':
            require(pid in holds and pid in confirmed and confirmed[pid]==(at,hid),
                'hold ended without actual current confirmation')
            start=holds.pop(pid);duration=at-start['started_at']
            require(row['held_seconds']==duration and 0<=duration<=120
                and row['switched']==(hid!=start['first_best']),'hold duration or destination change differs')
            ended_holds.append(dict(**start,ended_at=at,confirmed=hid,switched=hid!=start['first_best'],reason=row['reason']))
    require(not active and all(not queue for queue in queues.values()),'unaccounted active or queued hospital work')
    require(not holds and ended_holds==branch['holds'],'hold output coverage/order differs from actual events')
    require(set(queries)==set(db_sent) and set(completed)==set(replies)
        and set(queries)==set(completed)|cancelled,'raw hospital work coverage incomplete')
    counts=Counter(pid for pid,hid in queries.values());contacts={pid:[] for pid in specs}
    for iid,(pid,hid) in sorted(queries.items()):contacts[pid].append(hid)
    rows=branch['patients'];require({row['id'] for row in rows}==set(specs) and len(rows)==len(specs),
        'patient output inventory differs')
    waits=[];arrivals=[];failed=0
    for row in rows:
        pid=row['id'];spec=specs[pid]
        require(type(row['failed']) is bool and row['created_at']==spec['at'] and row['inquiry_count']==counts[pid]
            and counts[pid]<=10 and len(set(contacts[pid]))==len(contacts[pid])
            and row['contacted_hospitals']==contacts[pid],'patient contact accounting differs')
        if pid in confirmed:
            at,hid=confirmed[pid];selection=at-spec['at'];travel=spec['travel_seconds'][hid]
            require(not row['failed'] and row['selected_at']==at and row['selection_seconds']==selection
                and row['travel_seconds']==travel and row['estimated_arrival_seconds']==selection+travel
                and row['destination_hospital_id']==hid,'patient selected/arrival accounting differs')
            waits.append(selection);arrivals.append(selection+travel)
        else:
            require(row['failed'] and all(row[key] is None for key in ('selected_at','selection_seconds',
                'travel_seconds','estimated_arrival_seconds','destination_hospital_id')),'missing confirmation labelled success')
            waits.append(300);failed+=1
            require(any(event['type']=='patient_cancelled' and event['patient_id']==pid
                and event['at']==EPOCH+spec['at']+300 for event in events),
                'failed patient lacks actual 300-second observation cancellation')
    metrics=branch['metrics'];mean=lambda values:sum(values)/len(values) if values else None
    expected=dict(patient_count=len(rows),selected_count=len(rows)-failed,failed_count=failed,
        selection_seconds_mean=mean([wait for row,wait in zip(rows,waits) if not row['failed']]),
        selection_wait_capped_mean=mean(waits),inquiries_per_patient=mean(list(counts.get(pid,0) for pid in specs)),
        estimated_arrival_seconds_mean=mean(arrivals),review_work_seconds=work,
        review_started_count=len(completed),cancelled_before_start_count=len(cancelled),
        reply_suppressed_count=sum(kind=='reply_suppressed_after_cancel' for kind,_ in replies.values()))
    require(all(metrics.get(key)==value for key,value in expected.items()),'saved metrics differ from actual raw workflow')
    return dict(patients=len(rows),failed=failed,total_capped_selection_wait_seconds=sum(waits),
        review_work_seconds=work,actual_reviews=len(completed),actual_contacts=len(queries))


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
        calls=_audit_calls(scenario,branch);accounting=_audit_trace(scenario,branch)
        return dict(passed=True,**calls,**accounting,policy_kernel_independently_reexecuted=False,
            shortlist_math_independently_reconstructed=True,optimality_claimed=False,
            scope='saved current event-prefix ledger, shortlist membership/order, actual FIFO/response/confirmation/accounting; unchanged planning kernel source-bound elsewhere')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError) as error:
        raise ShortlistAuditError('malformed saved shortlist artifact') from error


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
