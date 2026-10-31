"""Independent inquiry-ledger/decision audit; never executes new planners.

Checks frozen G2 current decisions separately from inquiry policy evidence.
This is not a full independent reconstruction of resource-occupancy history,
clinical efficacy, or a proof of whole-episode G2 outcome preservation.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from statistics import median

from . import online_allocation_audit as base
from . import final_assignment_audit_20261028 as common
from . import arrival_guard_priority_audit_20261028 as g2_audit

EXPERIMENT_ID='inquiry_preserving_20261029'
FROZEN_SOURCES={
 'G2_policy':'57373bf5fa1723adbd284274b7b63ba52f8c5665d02e94c6a78ae258fd759878',
 'G2_engine':'6ea8f99d22130a92f09c73d8200fb8fce8cec5f105a21d6ae5cd2fd5c2e29ced'}
ASSUMPTIONS=[
 'G2 assignment, its priority weights and H1 work budget are unchanged; only inquiry refill and retry cooldown may differ.',
 'Refill uses received replies after G2 commitments, at most three simultaneous inquiries and only unqueried compatible candidates.',
 "Retry history contains this patient's completed accept/decline/timeout observations; resource releases and future replies are not supplied.",
 'Whole-episode G2 successes and weight4 outcomes are acceptance gates for experiments, not online preservation guarantees.',
 'Weights 1/2/4 and minute thresholds are experimental; no clinical efficacy, real response probability or national-system equivalence is claimed.']
REFILL_KEYS={'t','patient','priority','request_t','state','has_valid_offer','ready',
 'received_this_tick','retry_at','pending','candidates','max_k'}
RETRY_KEYS={'t','patient','priority','baseline_wait','observations'}
require=base._require


def integer(value,message,minimum=0):
    require(type(value) is int and value>=minimum,message)
    return value


def identifier(value,message):
    require(type(value) is str and bool(value),message)
    return value


def spec_check(spec):
    require(type(spec) is dict and set(spec)=={'id','refill','retry'},'inquiry spec schema differs')
    identifier(spec['id'],'policy id missing')
    require(spec['id']!='G2' and spec['refill'] in ('none','one','all')
            and spec['retry'] in ('baseline','fixed_2','history'),'invalid inquiry spec')
    return spec


def contract(spec):
    return dict(inquiry_spec=spec,allocation_rule='unchanged_G2',allocation_source_sha256=FROZEN_SOURCES,
      max_simultaneous_inquiries=3,first_acceptance_extra_wait_minutes=2,notify=True,
      refill_phase='after_current_minute_G2_joint_commitments',refill_order='original_received_patient_order',
      retry_scope='newly_scheduled_patient_cooldown_only',
      observations='completed_same_patient_reply_or_timeout_ledger_only',
      future_events_supplied_to_inquiry_policy=False,future_events_supplied_to_assignment_rule=False,
      hospital_queue_state_supplied_to_policy=False,simulation_clock='integer_minute',
      wall_time_added_to_simulation_clock=False,whole_episode_nonregression_guaranteed=False,
      whole_episode_priority4_nonregression_guaranteed=False,clinical_priority_policy_validated=False)


def refill_math(spec,s):
    require(type(s) is dict and set(s)==REFILL_KEYS,'refill snapshot whitelist differs')
    t=integer(s['t'],'invalid refill time');identifier(s['patient'],'invalid refill patient')
    require(s['priority'] in (1,2,4) and type(s['priority']) is int,'invalid experimental weight')
    integer(s['request_t'],'invalid request time');require(type(s['state']) is str,'state must be string')
    require(type(s['max_k']) is int and s['max_k']==3,'concurrency cap differs')
    for key in ('has_valid_offer','ready','received_this_tick'):require(type(s[key]) is bool,'nonboolean refill flag')
    if s['retry_at'] is not None:integer(s['retry_at'],'invalid retry time')
    require(type(s['pending']) is list and type(s['candidates']) is list,'refill list missing')
    pending=[];candidates=[]
    for p in s['pending']:
        require(type(p) is dict and set(p)=={'hospital','submitted_t'},'private pending field supplied')
        pending.append(identifier(p['hospital'],'pending hospital missing'))
        require(integer(p['submitted_t'],'invalid submitted time')<=t,'future pending supplied')
    require(len(pending)==len(set(pending)) and len(pending)<=3,'duplicate/excess pending inquiry')
    for p in s['candidates']:
        require(type(p) is dict and set(p)=={'hospital','travel_minutes'},'private candidate field supplied')
        hid=identifier(p['hospital'],'candidate hospital missing')
        require(hid not in pending and all(hid!=q[1] for q in candidates),'duplicate/open candidate')
        candidates.append((integer(p['travel_minutes'],'invalid current travel'),hid))
    send=[]
    if spec['refill']=='none':reason='disabled'
    elif s['request_t']>t:reason='patient_not_started'
    elif s['state']!='SEARCH':reason='not_searching'
    elif s['has_valid_offer'] or s['ready']:reason='offer_or_G2_decision_pending'
    elif s['retry_at'] is not None and s['retry_at']>t:reason='retry_cooldown'
    elif not s['received_this_tick']:reason='no_received_reply_this_tick'
    elif not pending:reason='no_partial_wave_to_refill'
    elif len(pending)>=3:reason='concurrency_full'
    elif not candidates:reason='no_unqueried_candidate'
    else:
        count=min(3-len(pending),1 if spec['refill']=='one' else 3)
        send=[h for _,h in sorted(candidates)[:count]];reason='refill_after_received_result'
    return dict(policy_id=spec['id'],send=send,reason=reason,open_requests=len(pending),max_k=3)


def retry_math(spec,s):
    require(type(s) is dict and set(s)==RETRY_KEYS,'retry snapshot whitelist differs')
    t=integer(s['t'],'invalid retry observation time');pid=identifier(s['patient'],'missing retry patient')
    require(type(s['priority']) is int and s['priority'] in (1,2,4),'invalid retry weight')
    baseline=integer(s['baseline_wait'],'invalid baseline cooldown',1)
    require(type(s['observations']) is list,'received observations missing')
    seen=set();recent=[]
    for row in s['observations']:
        require(type(row) is dict and set(row)=={'patient','hospital','submitted_t','observed_t','outcome'},'private received field supplied')
        require(row['patient']==pid,'other patient history supplied')
        hid=identifier(row['hospital'],'observed hospital missing')
        sent=integer(row['submitted_t'],'invalid submitted time');at=integer(row['observed_t'],'invalid observed time')
        require(sent<=at<=t,'future/reversed received observation')
        require(row['outcome'] in ('accept','decline','timeout'),'invalid received outcome')
        key=(pid,hid,sent);require(key not in seen,'duplicate completed observation');seen.add(key)
        if t-30<at<=t:recent.append((at,sent,hid,at-sent))
    recent=sorted(recent)[-6:];empirical=median([r[3] for r in recent]) if recent else None
    wait=baseline;reason='baseline_cooldown'
    if spec['retry']=='fixed_2':wait=min(2,baseline);reason='bounded_fixed_two'
    elif spec['retry']=='history' and empirical is not None:
        wait=min(baseline,3,max(1,math.ceil(empirical)));reason='observed_response_cadence'
    elif spec['retry']=='history':reason='no_history_keep_baseline'
    return dict(policy_id=spec['id'],wait_minutes=wait,retry_at=t+wait,reason=reason,
      samples=len(recent),observed_median_elapsed=empirical,history_window=30,history_limit=6,
      acceptance_or_resource_release_prediction=False)


def ledger_audit(physical,branch,spec):
    patients={p['id']:p for p in physical['patients']};hospitals={h['id']:h for h in physical['hospitals']}
    pending={pid:{} for pid in patients};tried={pid:set() for pid in patients};valid={pid:{} for pid in patients}
    state={pid:'SEARCH' for pid in patients};failed={pid:set() for pid in patients}
    caps={pid:set(p.get('required_capabilities',[])) for pid,p in patients.items()}
    retry_at={pid:None for pid in patients};last_received={};latest={};attempts=Counter()
    submitted=[];received=[];used_refill=set();used_retry=set();phase_markers={};counts=Counter()
    received_order={};refill_order={}
    refill=branch['inquiry_refill_decisions'];retry=branch['inquiry_retry_decisions'];events=branch['events']
    base_wait=physical.get('retry_minutes',3);integer(base_wait,'invalid baseline retry interval',1)
    commits=Counter((e['t'],e.get('patient'),e.get('hospital')) for e in events if e['type']=='commit')
    def compatible(pid):
        p=patients[pid];demand=p.get('resource_demands',{'ed_bed':1})
        return {hid for hid,h in hospitals.items() if hid not in failed[pid]
          and caps[pid]<=set(h.get('capabilities',[]))
          and all(h.get('capacity',{}).get(r,0)>=q for r,q in demand.items())}
    batch_commits={}
    for decision in branch['allocation_decisions']:
        if decision['kind']=='batch':
            batch_commits.setdefault(decision['t'],set()).update((pid,hid) for pid,hid in decision['assignments'].items() if hid is not None)
    seen_commits={};last_t=-1
    for ei,e in enumerate(events):
        t=e['t'];pid=e.get('patient');kind=e['type']
        require(type(t) is int and t>=last_t,'inquiry events unordered')
        if t>last_t:
            for key,deadline in retry_at.items():
                if deadline is not None and deadline<t and state[key]=='SEARCH':retry_at[key]=None
        last_t=t
        if kind=='G2_batch_phase_complete':
            require(t not in phase_markers,'duplicate post-G2 phase marker')
            dc=sum(d['t']<=t for d in branch['allocation_decisions'])
            gc=sum(r['t']<=t for r in branch['guard_records'])
            require(e['allocation_decision_count']==dc and e['guard_record_count']==gc,'phase marker decision counts differ')
            require(batch_commits.get(t,set())<=seen_commits.get(t,set()),'refill phase precedes G2 commits')
            phase_markers[t]=ei;continue
        if pid not in patients:continue
        p=patients[pid]
        if kind=='ask':
            require(t>=p['search_start_t'],'inquiry before patient search')
            hid=e['hospital'];require(hid in compatible(pid),'inquiry incompatible or repeats failed transfer hospital')
            require(hid not in pending[pid] and hid not in valid[pid],'duplicate open/valid inquiry')
            require(hid not in tried[pid],'repeated hospital before candidate-cycle reset')
            if spec['retry']!='baseline':require(retry_at[pid] is None or t>=retry_at[pid],'patient cooldown bypass')
            if retry_at[pid] is not None and retry_at[pid]<=t:retry_at[pid]=None
            attempts[pid,hid]+=1;require(e['attempt']==attempts[pid,hid],'inquiry attempt sequence differs')
            row=dict(patient=pid,hospital=hid,submitted_t=t,attempt=e['attempt'])
            submitted.append(row);latest[pid,hid]=row;pending[pid][hid]=t;tried[pid].add(hid)
            require(len(pending[pid])<=3,'simultaneous inquiry cap exceeded');counts['asks_checked']+=1
        elif kind in ('accept','decline','no_response'):
            hid=e['hospital'];require(hid in pending[pid],'terminal without live submitted inquiry')
            sent=pending[pid].pop(hid)
            row=dict(patient=pid,hospital=hid,submitted_t=sent,observed_t=t,
                     outcome='timeout' if kind=='no_response' else kind)
            require(sent<=t,'terminal precedes request');received.append(row);last_received[pid]=t
            if pid not in received_order.setdefault(t,[]):received_order[t].append(pid)
            if kind=='accept':valid[pid][hid]=t+physical.get('accept_ttl_minutes',10)
            counts['received_checked']+=1
        elif kind in ('expire','recheck_failed'):
            valid[pid].pop(e['hospital'],None)
        elif kind=='commit':
            state[pid]='TRANSIT';pending[pid].clear();valid[pid].clear()
            seen_commits.setdefault(t,set()).add((pid,e['hospital']))
        elif kind=='arrive':state[pid]='QUEUED';pending[pid].clear()
        elif kind=='treatment_start':state[pid]='TREATING'
        elif kind=='arrival_unable':state[pid]='TRANSFER_WAIT';failed[pid].add(e['hospital'])
        elif kind=='condition_update':caps[pid]=set(e['required_capabilities'])
        elif kind=='transfer_search':
            state[pid]='SEARCH';pending[pid].clear();valid[pid].clear();tried[pid].clear();retry_at[pid]=None
        elif kind=='no_compatible':state[pid]='NO_COMPATIBLE'
        elif kind=='inquiry_retry_policy':
            ri=e['record_index'];require(type(ri) is int and 0<=ri<len(retry) and ri not in used_retry,'missing/reused retry record')
            used_retry.add(ri);record=retry[ri];s=record['snapshot']
            require(record['t']==t and record['patient']==pid,'retry record identity differs')
            require(not pending[pid] and not valid[pid],'cooldown scheduled with pending/valid inquiry')
            require(compatible(pid)<=tried[pid],'cooldown before all candidates tried')
            expected=dict(t=t,patient=pid,priority=p.get('priority',4),baseline_wait=base_wait,
              observations=[deepcopy(r) for r in received if r['patient']==pid and r['observed_t']<=t])
            require(s==expected,'retry snapshot differs from received ledger')
            decision=retry_math(spec,s);require(record['decision']==decision,'retry policy arithmetic differs')
            require(record['baseline_retry_at']==e['baseline_retry_at']==t+base_wait
              and record['actual_retry_at']==e['actual_retry_at']==decision['retry_at'],'retry scheduled time differs')
            retry_at[pid]=decision['retry_at'];tried[pid].clear();counts['retry_cycles_checked']+=1
        elif kind=='inquiry_refill_policy':
            ri=e['record_index'];require(type(ri) is int and 0<=ri<len(refill) and ri not in used_refill,'missing/reused refill record')
            used_refill.add(ri);record=refill[ri];s=record['snapshot']
            refill_order.setdefault(t,[]).append(pid)
            require(spec['refill']!='none' and t in phase_markers and phase_markers[t]<ei,'refill before post-G2 phase')
            require(record['t']==t and record['patient']==pid and record['phase']=='after_G2_batch','refill identity/phase differs')
            require(s.get('patient')==pid and s.get('t')==t and s.get('priority')==p.get('priority',4)
                    and s.get('request_t')==p['search_start_t'],'refill patient metadata differs')
            expected_pending=[dict(hospital=h,submitted_t=sent) for h,sent in pending[pid].items()]
            require(s.get('pending')==expected_pending,'refill pending differs from actual asks')
            require(s.get('state')==state[pid] and s.get('has_valid_offer')==bool(valid[pid])
              and s.get('ready') is False and s.get('received_this_tick')==(last_received.get(pid)==t),'refill state differs from actual events')
            deadline=retry_at[pid]
            if deadline is not None and deadline==t and state[pid]=='SEARCH':
                deadline=None;retry_at[pid]=None
            require(s.get('retry_at')==deadline,'refill cooldown differs')
            expected_candidates=compatible(pid)-tried[pid]-set(pending[pid])-set(valid[pid])
            require({r['hospital'] for r in s['candidates']}==expected_candidates,'refill candidates differ from compatible unqueried set')
            decision=refill_math(spec,s)
            require(record['decision']==decision and record['actual_sent']==decision['send']
                    and e['selected_hospitals']==decision['send'],'refill arithmetic or sent inquiries differ')
            for offset,hid in enumerate(decision['send'],1):
                require(ei+offset<len(events),'refill lacks actual asks')
                actual=events[ei+offset]
                require(actual['t']==t and actual['type']=='ask' and actual['patient']==pid
                        and actual['hospital']==hid,'refill actual asks/order differ')
            counts['refill_reviews_checked']+=1;counts['refill_requests_checked']+=len(decision['send'])
    require(submitted==branch['submitted_inquiry_ledger'],'submitted ledger differs from actual ask events')
    require(received==branch['received_inquiry_ledger'],'received ledger differs from terminal events')
    require(used_refill==set(range(len(refill))) and used_retry==set(range(len(retry))),'orphan inquiry record')
    require(set(phase_markers)=={r['t'] for r in refill},'orphan/missing refill phase marker')
    if spec['refill']!='none':require(refill_order==received_order,'refill patient order/coverage differs from received intents')
    expected_metrics=dict(inquiry_refill_reviews=len(refill),inquiry_refill_calls=sum(bool(r['actual_sent']) for r in refill),
      inquiry_refill_requests=sum(len(r['actual_sent']) for r in refill),inquiry_retry_cycles=len(retry),
      configured_retry_wait_delta_minutes=sum(r['baseline_retry_at']-r['actual_retry_at'] for r in retry),
      retry_wait_delta_is_not_total_patient_time_saved=True)
    for key,value in expected_metrics.items():require(branch['metrics'][key]==value,'inquiry metric mismatch: '+key)
    return counts


def audit_replay(replay,source_scenario=None):
    original=replay['original_scenario'];spec=spec_check(replay['policy_spec'])
    if source_scenario is not None:require(original==source_scenario,'original scenario differs')
    fingerprint=common.canonical_sha256(original)
    require(replay['schema_version']==1 and replay['comparison_id']==EXPERIMENT_ID
            and replay['source_experiment']=='online_allocation_comparison','inquiry replay identity differs')
    require(replay['input_scenario_sha256']==replay['original_scenario_sha256']==fingerprint,'input hash differs')
    physical=deepcopy(original);physical.update(online_allocation_comparison=True,
      source_experiment='online_allocation_comparison',comparison_id=EXPERIMENT_ID)
    physical.setdefault('assumptions',[]).extend(ASSUMPTIONS)
    require(replay['scenario']==physical,'physical scenario modified')
    require(len(replay['branches'])==1 and replay['execution_order']==[spec['id']],'wrong inquiry execution order')
    branch=replay['branches'][0];policy=branch['policy']
    require(branch['id']==policy['name']==spec['id'] and policy['rule']=='G2','inquiry branch mislabeled as executed original G2')
    require(branch['comparison_id']==EXPERIMENT_ID and branch['source_experiment']=='online_allocation_comparison','branch identity differs')
    require(branch['original_scenario_sha256']==fingerprint and branch['routing_input_sha256']==replay['routing_input_sha256'],'branch input binding differs')
    require(branch['inquiry_spec']==policy['inquiry_revision']==spec,'inquiry policy spec differs')
    require(branch['execution_contract']==contract(spec),'inquiry execution contract differs')
    for name,filename in (('G2_policy','arrival_guard_priority_policy_20261028.py'),('G2_engine','arrival_guard_priority_engine_20261028.py')):
        require(hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()==FROZEN_SOURCES[name],'frozen G2 source differs')
    projected=deepcopy(branch);projected['id']='G2';projected['policy']['name']='G2'
    projected['policy'].pop('inquiry_revision');projected['execution_contract']=deepcopy(g2_audit.EXECUTION_CONTRACT)
    g2_audit._policy(projected)
    routing=replay['routing_summary'];require(set(routing['by_branch'])=={spec['id']},'routing branch differs')
    for key,value in dict(provider='synthetic',scope='independent_per_branch',live_traffic=False,
      ambulance_priority=False,external_route_provider_allowed=False,shared_mutable_routing_cache=False).items():
        require(routing.get(key)==value,'external/shared routing differs')
    route=routing['by_branch'][spec['id']]
    require(route['provider']=='synthetic' and route['scope']=='scenario' and route['road_routes']==route['fallback_routes']==0
      and route['synthetic_routes']==route['routes_evaluated'],'non-synthetic route evidence')
    general=base.audit_branch(physical,branch,exact_limit=0)
    counters=Counter({k:v for k,v in general['counts'].items() if type(v) is int})
    counters.update(g2_audit._guard_decisions(physical,branch));g2_audit._runtime(branch)
    counters.update(ledger_audit(physical,branch,spec))
    require(replay['duration_minutes']==max((e['t'] for e in branch['events']),default=0),'duration differs')
    return dict(schema_version=1,passed=True,input_sha256=fingerprint,
      branch_metrics={spec['id']:common.branch_metrics(physical,branch)},
      patient_vectors={spec['id']:common._vectors(physical,branch)},counters=dict(counters),
      allocator_or_inquiry_planner_reexecuted=False,
      executed_control_G2_arm_claimed=False,allocation_rule_scope='unchanged G2 on this inquiry branch current inputs',
      whole_episode_nonregression_verified=False,resource_history_independently_reconstructed=False,
      synthetic_current_travel_measurement_independently_verified=False,
      clinical_effect_verified=False)
