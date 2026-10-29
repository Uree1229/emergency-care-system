"""Independent conditional queue-envelope rescore of all feasible rows.

Only the declared local queue-envelope law changes. The complete frozen
Waitpareto/Fullmenu proof remains separately checked; no producer model, policy,
future event, actual outcome or extra scoring call is imported.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from . import cohort_waitpareto_audit as foundation
control_audit=foundation

ROOT=Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest()!='e8fec8092b7d137cce3d4148f4ed212ac04b6eea83a106d48806fbd287dafb28':
    raise ValueError('frozen independent Pareto auditor differs')
base=foundation.base
parent,horizon,joint=foundation.parent,foundation.horizon,foundation.joint
numeric=foundation.numeric
dynamic=foundation.dynamic
require,precise_same,exact_copy=foundation.require,foundation.precise_same,foundation.exact_copy
_pid,_integer,EPOCH=foundation._pid,foundation._integer,foundation.EPOCH
INPUT_KEYS,CALL_KEYS,GROUP_KEYS=foundation.INPUT_KEYS,foundation.CALL_KEYS,foundation.GROUP_KEYS
NAME='eat_cohort_envelope_l15_f'
BASE_POLICY=foundation.NAME
BASE_SOURCE_SHA256='60f565d5696a9e87316e61b6da3d1e7932a7d1e79ec44f57ded68c8e942afaf9'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_waitpareto_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Pareto producer source differs')
NEW_INSTRUMENTATION=dict(control_audit.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_envelope_runner.run_once',expected_future_queue_envelope=True)


def _project_name(name):
    if name==NAME:return BASE_POLICY
    return '__foreign_cohort_waitpareto__' if name==BASE_POLICY else name


def alias_context(context):
    value=deepcopy(context)
    for row in value['eligible']:
        require(row['job']['policy_name']==NAME,'current eligible job has wrong native followplan policy')
        row['job']['policy_name']=BASE_POLICY
    return value


def alias_member(member):
    value=deepcopy(member)
    require(value['workflow_snapshot']['job']['policy_name']==NAME,'current member has wrong native followplan policy')
    value['workflow_snapshot']['job']['policy_name']=BASE_POLICY
    return value


def alias_owners(owners):
    value=deepcopy(owners)
    for row in value['inventory']:
        if row['job'] is not None:row['job']['policy_name']=_project_name(row['job']['policy_name'])
    return value


def alias_events(events):
    value=deepcopy(events)
    for event in value:
        if event['type'] in ('inquiry_workflow_started','inquiry_workflow_paused','inquiry_policy_changed'):
            data=parent._data(event)
            if 'policy_name' in data:event['data']=dict(data,policy_name=_project_name(data['policy_name']))
    return value


def alias_registry(registry):
    value=deepcopy(registry)
    for row in value['jobs']:row['policy_name']=_project_name(row['policy_name'])
    return value


def alias_source(source):
    value=deepcopy(source);value['events']=alias_events(value['events'])
    for row in value['rows']:
        if row['job'] is not None:row['job']['policy_name']=_project_name(row['job']['policy_name'])
    return value


def audit_registry(events,decisions,registry,now,event_prefix,decision_prefix):
    value=foundation.audit_registry(alias_events(events),decisions,alias_registry(registry),now,event_prefix,decision_prefix)
    for row in value['eligible']:row['job']['policy_name']=NAME
    return value


def audit_priority_context(priority,context,members,tails,*,lab_defaults=False):
    return foundation.audit_priority_context(priority,alias_context(context),[alias_member(m) for m in members],
        [alias_member(m) for m in tails],lab_defaults=lab_defaults)


def audit_time_context(events,inputs,member,event_prefix,decision_prefix,decision=None):
    return foundation.audit_time_context(alias_events(events),inputs,alias_member(member),event_prefix,decision_prefix,decision)


def audit_owner_provenance(scenario,events,registry,owners,source):
    return foundation.audit_owner_provenance(scenario,alias_events(events),alias_registry(registry),alias_owners(owners),alias_source(source))


def audit_member_provenance(scenario,events,member,event_prefix,decision_prefix):
    return foundation.audit_member_provenance(scenario,alias_events(events),alias_member(member),event_prefix,decision_prefix)



PROBABILITY_EPSILON=1e-12
WAIT_EPSILON=1e-9
fullmenu=foundation.foundation.foundation
stoploss=fullmenu.foundation
recourse=fullmenu.recourse
followplan=fullmenu.followplan
SCORING_SOURCE_SHA256={
    'cohort_fullmenu_model':'8185c654bb7402d67708b14bd151572b50950cc36d639c0b5153fa37eececaf1',
    'cohort_stoploss_model':'ae9e4f870c56551b922982b914a38418a681f81d210afb66254c4c740bd30c9e',
    'cohort_recourse_model':'e776bd643611a9ce33cbfa3fdbd8f05042fae755c20be5e0a1f4bddfdcebc2de',
    'cohort_followplan_model':'8f8dfc82451b05a804d33253823bde3a6cbb68ff67728d1458dbb17aca0d6268',
}
for module,digest in SCORING_SOURCE_SHA256.items():
    require(hashlib.sha256((ROOT/'inquiry_lab'/(module+'.py')).read_bytes()).hexdigest()==digest,
        'frozen queue-envelope scoring source differs: '+module)
MODEL_RULE='frozen_plans_with_exact_declared_baseline_and_deterministic_prior_completion_envelope'
MODEL_SCOPE='same frozen initial CDFs, first continuation plans and fixed F0; replace max of baseline stoploss mean and deterministic prior remainder by the expectation of their declared maximum; deterministic prior completion, shifted/thinned baseline and cancellation independence remain approximations; no future conditioning or physical queue exactness'
ENVELOPE_RULE='prior_positive_part_plus_baseline_stoploss_at_max_send_prior'
ENVELOPE_SCOPE='for declared nonnegative C0 and deterministic T, E[(max(C0,T)-s)+]=(T-s)+ +E[(C0-max(s,T))+]; local same-T Jensen comparison only; current no-reply denominator retained; inherited J roundoff corrections retained; no additional envelope clamp'
# Producer policy constants are fixed explicitly below before source freeze.
RULE='uniform_queue_envelope_rescore_then_unchanged_owner_guard_and_wait_first_selection'
OBJECTIVE='uniformly rescore every feasible row including original Stoploss actual anchor, unchanged per-owner probability floor within1e-12, minimum total capped-wait band within1e-9 then tier4,2,1 probability bands, original Stoploss anchor and source order'
SCOPE='complete intact Waitpareto proposal retained; same activation, feasible menu, initial/residual scores, frozen future plans and fixed F0; only local random-baseline/deterministic-prior envelope changes, followed by unchanged guard law on uniformly new scores and unchanged wait selector; current pair only, no realized outcome guarantee'


def _sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def envelope_wait(decomposition,send_seconds,prior_completion_seconds):
    for value in (send_seconds,prior_completion_seconds):
        require(type(value) in (int,float) and math.isfinite(value) and value>=0,
            'finite nonnegative local queue-envelope times required')
    send=float(send_seconds);prior=float(prior_completion_seconds)
    current=stoploss.base_stoploss_at_elapsed(decomposition,send)
    barrier=max(send,prior)
    at_barrier=current if barrier==send else stoploss.base_stoploss_at_elapsed(decomposition,barrier)
    remaining=max(0.0,prior-send);baseline=current['remaining_seconds'];tail=at_barrier['remaining_seconds']
    value=remaining+tail;old=max(baseline,remaining);upper=remaining+baseline
    tolerance=(current['numeric_bound_tolerance_seconds']+at_barrier['numeric_bound_tolerance_seconds']+
        32*sum(math.ulp(x) for x in (value,old,upper,remaining,baseline,tail)))
    require(all(math.isfinite(x) for x in (value,old,upper,tolerance)), 'queue envelope must have finite arithmetic')
    require(value>=old-tolerance and value<=upper+tolerance,
        'declared random-baseline/deterministic-prior envelope local bounds violated')
    return dict(schema_version=1,rule=ENVELOPE_RULE,send_offset_seconds=send,prior_completion_seconds=prior,
        prior_remaining_seconds=remaining,barrier_elapsed_seconds=barrier,baseline_at_send=current,
        baseline_at_barrier=at_barrier,baseline_remaining_seconds=baseline,baseline_tail_after_barrier_seconds=tail,
        old_max_of_means_seconds=old,envelope_wait_seconds=value,local_lower_bound_seconds=old,
        local_upper_bound_seconds=upper,envelope_minus_old_max_seconds=value-old,numeric_bound_tolerance_seconds=tolerance,
        local_bounds_within_allowance=True,numeric_clamp_applied=False,prior_is_deterministic_forecast=True,scope=ENVELOPE_SCOPE)


def expected_future_work(jobs,plans):
    require(jobs==sorted(jobs,key=lambda q:(q['send_offset_seconds'],q['first_patient_index'],q['plan_candidate_index']))
        and [j['future_job_index'] for j in jobs]==list(range(len(jobs))), 'future chronological and tie order differs')
    values=[];completion={}
    for job in jobs:
        send=job['send_offset_seconds'];prior=completion.get(job['hospital'],0.0)
        decomposition=stoploss.base_stoploss_decomposition(job['source_queue_record'])
        at_send=stoploss.base_stoploss_at_elapsed(decomposition,send)
        precise_same(job['base_stoploss_decomposition'],decomposition,'unchanged current-head source decomposition')
        precise_same(job['base_stoploss_at_send'],at_send,'unchanged baseline stoploss at send')
        precise_same(job['fixed_F0_wait_seconds'],at_send['remaining_seconds'],'unchanged fixed-F0 queue input')
        proof=envelope_wait(decomposition,send,prior);wait=proof['envelope_wait_seconds']
        probe=min(send+wait,job['owner_horizon_seconds']);no_accept=1.0;factors=[]
        origin=plans[job['first_patient_index']]['hypothetical_origin']
        for index,item in enumerate(origin['own_items']):
            if item['hospital']==job['hospital']:continue
            probability=base.descriptor_cdf(item,probe);no_accept*=max(0.0,1.0-probability)
            factors.append(dict(kind='initial',initial_item_index=index,hospital=item['hospital'],selection_probability_at_probe=probability))
        for other in jobs:
            if base.owner_key(other['patient_id'])!=base.owner_key(job['patient_id']) or other['hospital']==job['hospital']:continue
            probability=stoploss.future_cdf(other,probe,'fixed_F0_wait_seconds');no_accept*=max(0.0,1.0-probability)
            factors.append(dict(kind='future',future_job_index=other['future_job_index'],hospital=other['hospital'],selection_probability_at_probe=probability))
        require(0<=no_accept<=1,'invalid fixed-F0 no-accept survival')
        expected=job['full_mean_work_seconds']*no_accept;after=send+wait+expected
        values.append(dict(job,elapsed_seconds=send,base_remaining_seconds=proof['baseline_remaining_seconds'],
            prior_future_completion_seconds=prior,prior_future_remaining_seconds=proof['prior_remaining_seconds'],
            corrected_wait_seconds=wait,probe_offset_seconds=probe,fixed_F0_survival_factors=factors,
            no_prior_accept_probability=no_accept,expected_work_seconds=expected,future_completion_after_seconds=after,queue_envelope=proof))
        completion[job['hospital']]=after
    return values


def score_first_cohort(plans):
    require(type(plans) is list and len({base.owner_key(p['patient_id']) for p in plans})==len(plans),
        'complete unique frozen first-plan inventory required')
    jobs=expected_future_work(stoploss.flatten_future_jobs(plans),plans);patients=[]
    for pi,first in enumerate(plans):
        own=sorted((j for j in jobs if j['first_patient_index']==pi),key=lambda q:q['plan_candidate_index'])
        initial=deepcopy(first['hypothetical_origin']['own_items']);edges=deepcopy(first['hypothetical_origin']['cell_edges_seconds'])
        remaining=first['source_owner']['evaluation_end_at']-first['source_owner']['inputs']['now'];bins=horizon.intervals(remaining)
        require(edges==[r for _,r in bins],'unchanged partial H grid differs')
        future=[]
        for job in own:
            masses=horizon.masses(lambda x,job=job:stoploss.future_cdf(job,x,'corrected_wait_seconds'),1.0,bins)
            future.append(dict(future_job_index=job['future_job_index'],hospital=job['hospital'],travel_seconds=job['travel_seconds'],
                send_offset_seconds=job['send_offset_seconds'],wait_seconds=job['corrected_wait_seconds'],
                service_mu=job['service_mu'],service_sigma=job['service_sigma'],total_probability=job['reply_probability']*job['accept_probability'],masses=masses))
        cost=0.0
        for job in own:
            send=job['send_offset_seconds'];survival=1.0
            for item in initial:survival*=max(0.0,1.0-base.descriptor_cdf(item,send))
            for other in own:survival*=max(0.0,1.0-stoploss.future_cdf(other,send,'corrected_wait_seconds'))
            cost+=job['inquiry_cost_seconds']*survival
        metrics=numeric.corrected_owner_metrics([(r['travel_seconds'],r['masses']) for r in initial+future],bins,remaining,cost)
        metrics['immediate_contact_cost_seconds']=first['initial_contact_cost_seconds'];metrics['total_cost_seconds']+=first['initial_contact_cost_seconds']
        patients.append(dict(patient_id=first['patient_id'],effective_priority=first['effective_priority'],parent_first_plan_index=pi,
            job_indices=[j['future_job_index'] for j in own],fixed_initial_items=initial,cell_edges_seconds=edges,future_items=future,
            initial_contact_cost_seconds=first['initial_contact_cost_seconds'],future_contact_cost_seconds=cost,metrics=metrics))
    return dict(schema_version=1,rule=MODEL_RULE,scope=MODEL_SCOPE,jobs=jobs,patient_scores=patients)


def rescore_row(old_row,prepared):
    index=old_row['row_index'];p=prepared
    if 'base_row_score' in old_row:
        plans=old_row['base_row_score']['first_cohort_future'];source='inherited_Stoploss_base_row_first_cohort_future';source_digest=_sha(plans)
    else:
        require(old_row.get('row_kind')=='expanded' and type(old_row.get('dynamic_row_projection')) is dict,
            'known complete parent Fullmenu score row required')
        row=old_row['dynamic_row_projection'];require(row['row_index']==index,'existing Dynamic row identity differs')
        initial=recourse.first_cohort_score(row,p['hedge_members'],p['priority_context'])
        reviews=followplan.first_review_sources(row,p['recourse_members'],p['views']['joint']['baseline_proposals']);plans=[]
        for review in reviews:
            pid=review['patient_id'];own=[j for j in initial['jobs'] if base.owner_key(j['patient_id'])==base.owner_key(pid)]
            plans.append(followplan.first_patient_score(p['owner_by'][base.owner_key(pid)],own,row['projection']['final_pending'],initial['jobs'],review['next_review_at']))
        source='reconstructed_frozen_plans_from_existing_Dynamic_row';source_digest=_sha(row)
    first_ids={base.owner_key(p['patient_id']) for p in plans};require(len(first_ids)==len(plans),'duplicate frozen first-plan owner')
    residual=[deepcopy(m) for m in old_row['member_metrics'] if base.owner_key(m['patient_id']) not in first_ids]
    first=score_first_cohort(plans)
    metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in first['patient_scores']]+residual
    require([base.owner_key(m['patient_id']) for m in metrics]==[base.owner_key(m['patient_id']) for m in old_row['member_metrics']],
        'complete unchanged score-owner order differs')
    return dict(row_index=index,first_plan_source=source,first_plan_source_sha256=source_digest,first_cohort_envelope=first,
        unchanged_residual_member_metrics=residual,member_metrics=metrics,
        tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.0) for t in (4,2,1)},
        sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.0))


def prepare(full,members,context,tails,priority):
    ms=[alias_member(m) for m in members];ts=[alias_member(m) for m in tails];c=alias_context(context)
    for adapter in (foundation,foundation.foundation,fullmenu):
        ms=[adapter.alias_member(m) for m in ms];ts=[adapter.alias_member(m) for m in ts];c=adapter.alias_context(c)
    return fullmenu.prepare(full['original_stoploss_proposal'],members=ms,tail_members=ts,joint_context=c,priority_context=priority)


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
    require(type(evidence) is dict and type(evidence.get('original_waitpareto_proposal')) is dict
        and set(evidence['original_waitpareto_proposal'])=={'decisions','evidence'},'missing intact Waitpareto proposal')
    original=evidence['original_waitpareto_proposal']
    checked=foundation.audit_pair([alias_member(m) for m in members],alias_context(context),[alias_member(m) for m in tails],
        original['decisions'],original['evidence'],priority_context,alias_owners(owner_context))
    priorities=audit_priority_context(priority_context,context,members,tails)
    old=original['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    pareto=old['original_pareto_proposal']['evidence'];full=pareto['original_fullmenu_proposal']['evidence']
    anchor=old['stoploss_anchor_index'];guard=None;selection=None;scores=[]
    require(anchor==pareto['stoploss_anchor_index']==full['base_selected_index']==full['original_stoploss_proposal']['evidence']['selected_index'],
        'queue-envelope anchor differs from original Stoploss actual row')
    counts=Counter({key:0 for key in ('envelope_rows_checked','envelope_jobs_checked','envelope_local_barrier_checks',
        'envelope_groups_evaluated','envelope_groups_unevaluated','envelope_interventions','envelope_guard_anchor_rescored_checked')})
    if evaluated:
        prior=full['row_scores'];recorded=evidence.get('new_row_scores')
        require(type(recorded) is list and len(recorded)==len(prior) and [r['row_index'] for r in recorded]==full['menu_indices'],
            'new scores omit or duplicate original feasible rows')
        prepared=prepare(full,members,context,tails,priority_context)
        for previous,value in zip(prior,recorded):
            rebuilt=rescore_row(previous,prepared)
            precise_same(value,rebuilt,'independent all-row queue-envelope reconstruction')
            scores.append(rebuilt)
            counts['envelope_rows_checked']+=1
            counts['envelope_jobs_checked']+=len(rebuilt['first_cohort_envelope']['jobs'])
            counts['envelope_local_barrier_checks']+=len(rebuilt['first_cohort_envelope']['jobs'])
        guard=foundation.foundation.guard_rows(scores,baseline_index=anchor,owner_inventory=owner_context['inventory'],
            menu_indices=full['menu_indices'],feasible_row_indices=full['feasible_row_indices'])
        selection=foundation.select_protected_wait(scores,admissible_row_indices=guard['admissible_row_indices'],
            baseline_index=anchor,owner_inventory=owner_context['inventory'],menu_indices=full['menu_indices'],feasible_row_indices=full['feasible_row_indices'])
        selected=selection['selected_index'];counts['envelope_groups_evaluated']+=1;counts['envelope_guard_anchor_rescored_checked']+=1
    else:
        require(full['row_scores']==[] and full['menu_indices']==[] and old['protected_wait_selection'] is None,
            'unevaluated Waitpareto invented new envelope scores')
        counts['envelope_groups_unevaluated']+=1
    intervened=selected!=base_index;counts['envelope_interventions']+=int(intervened)
    wanted=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        scoring_source_sha256=deepcopy(SCORING_SOURCE_SHA256),
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_waitpareto__'),
        joint_context=context,priority_context=priority_context,owner_context=owner_context,original_waitpareto_proposal=original,
        evaluated=evaluated,new_row_scores=scores,owner_probability_guard=guard,protected_wait_selection=selection,
        base_selected_index=base_index,stoploss_anchor_index=anchor,selected_index=selected,intervened=intervened,
        tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=OBJECTIVE,scope=SCOPE)
    precise_same(evidence,wanted,'complete independently rescored queue-envelope choice')
    require(type(outputs) is list and len(outputs)==2,'actual queue-envelope output pair differs')
    table=fullmenu.parent_views(full['original_stoploss_proposal'])['joint']
    chosen=next((r for r in scores if r['row_index']==selected),None)
    for index in range(2):
        actual=deepcopy(original['decisions'][index])
        if intervened:
            hospitals=table['combinations'][selected]['hospitals'][index]
            actual=deepcopy(table['baseline_proposals'][index]);actual['send']=list(hospitals);actual['reason']='cohort_envelope_current_only'
            ev=actual['evidence'];ev['sent_hospitals']=list(hospitals);ev['planned_expected_cost']=round(chosen['member_metrics'][index]['metrics']['total_cost_seconds'],3)
            for row in ev['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
        actual['evidence']['policy']=NAME
        actual['evidence']['cohort_envelope']=dict(schema_version=1,mode='pair_cohort_envelope',evaluated=evaluated,
            intervened=intervened,member_index=index,base_selected_index=base_index,stoploss_anchor_index=anchor,
            selected_index=selected,effective_priority=priorities[_pid(members[index]['patient_id'])])
        precise_same(outputs[index],actual,'independently reconstructed queue-envelope actual output')
    checked=dict(checked,original_Waitpareto_groups_applied=checked['joint_groups_applied'],joint_groups_applied=int(selected!=table['baseline_index']))
    return dict(checked,**dict(counts))



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
            require(output['evidence'].get('cohort_followplan') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed followplan scope')
            require(output['evidence'].get('cohort_envelope') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed stoploss scope')
            require(output['evidence'].get('cohort_waitpareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Waitpareto scope')
            require(output['evidence'].get('cohort_pareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Pareto scope')
            require(output['evidence'].get('cohort_fullmenu') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed original Fullmenu scope')
            require(output['evidence'].get('cohort_stoploss') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Stoploss scope')
            normalized = deepcopy(output); normalized['evidence'].pop('cohort_envelope'); normalized['evidence'].pop('cohort_waitpareto'); normalized['evidence'].pop('cohort_pareto'); normalized['evidence'].pop('cohort_fullmenu'); normalized['evidence'].pop('cohort_stoploss'); normalized['evidence'].pop('cohort_followplan'); normalized['evidence'].pop('cohort_recourse')
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
        return control_audit.audit_replay(view)['branch_audits'][0]
    try:
        environment=hashlib.sha256(json.dumps(scenario,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        require(branch['environment_sha256']==environment and branch['rule']=='immediate','bound public environment or confirmation rule differs')
        exact_copy(branch['instrumentation'],NEW_INSTRUMENTATION,'new adapter source contract')
        checks=_audit_calls(scenario,branch);accounting=horizon._audit_trace_fixed(scenario,branch)
        total_j=sum(1800 if p['failed'] else p['estimated_arrival_seconds'] for p in branch['patients'])
        return dict(passed=True,**checks,**accounting,total_arrival_failure_cost_seconds=total_j,
            mean_arrival_failure_cost_seconds=total_j/len(branch['patients']) if branch['patients'] else None,
            original_Hedge_proposal_independently_checked=True,original_D_CS_continuation_intact=True,
            original_Recourse_proposal_independently_checked=True,
            original_Followplan_proposal_independently_checked=True,
            original_Stoploss_proposal_independently_checked=True,
            original_Fullmenu_proposal_independently_checked=True,
            original_Pareto_proposal_independently_checked=True,
            original_Waitpareto_proposal_independently_checked=True,
            expected_future_queue_envelope_independently_checked=True,
            envelope_exact_only_for_declared_random_C0_deterministic_T=True,
            all_feasible_rows_and_anchor_uniformly_rescored=True,
            fixed_F0_not_recomputed_from_new_waits=True,
            physical_C0_prior_dependency_modeled=False,
            global_trajectory_queue_nonregression_claimed=False,
            local_envelope_no_numeric_clamp=True,
            protected_wait_first_selection_independently_checked=True,
            per_owner_probability_guard_reapplied_to_rescored_anchor=True,
            selected_predicted_wait_within_Stoploss_baseline_tolerance=True,
            additional_baseline_wait_cap_applied=False,
            every_owner_final_predicted_probability_guard_independently_checked=True,
            Stoploss_actual_row_baseline_and_tie_anchor_independently_checked=True,
            score_repair_adds_no_outcome_inputs=True,
            realized_success_identity_preservation_guaranteed=False,
            original_activation_gate_independently_preserved=True,
            every_original_feasible_row_in_activated_groups_independently_checked=True,
            compact_added_row_D_references_and_full_scoring_pipeline_independently_rebuilt=True,
            fixed_F0_one_pass_expected_work_independently_rebuilt=True,
            first_patient_static_next_review_continuation_independently_rebuilt=True,
            frozen_first_future_plans_shared_work_independently_rebuilt=True,
            fixed_F0_future_survival_and_corrected_contact_cost_independently_checked=True,
            chronological_mean_queue_completion_independently_checked=True,
            fixed_F0_uses_mean_preserving_current_head_stoploss=True,
            current_denominator_fixed_without_future_silence_conditioning=True,
            shifted_thinned_mean_identity_and_Jensen_bounds_independently_checked=True,
            exact_baseline_only_under_declared_shifted_thinned_head_law=True,
            log_arithmetic_sensitivity_allowance_independently_checked=True,
            numerical_allowance_scope='engineering 32-ULP operand envelopes propagated through log-stoploss sensitivity; not a certified transcendental interval',
            exact_stochastic_residual_expectation_claimed=False,
            hypothetical_descriptors_separate_from_committed_source=True,
            actual_pending_and_own_CDF_unchanged=True,integer_occupancy_and_contact_budget_independently_checked=True,
            actual_outputs_sends_and_all_patient_FIFO_checked=True,physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='complete actual source captures and prefix/version checks; physical atomicity needs frozen adapter and rollback tests',
            scope='intact independently audited Waitpareto proposal; all feasible rows and Stoploss anchor independently rescored with declared random-C0/deterministic-prior-mean queue envelope only; unchanged plans/fixed-F0/initial/residual scores and actual caps; owner guard and wait-first staging reapplied to common repaired scores; no physical joint-queue or realized-success guarantee')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise base.CohortHedgeAuditError('malformed saved cohort-waitpareto artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version']==1 and type(replay['branches']) is list,'invalid complete replay')
    require(replay['arms']==[[b['policy'],b['rule']] for b in replay['branches']]
        and len({tuple(a) for a in replay['arms']})==len(replay['arms'])>0,'complete arm inventory differs')
    require(all(b['environment_sha256']==replay['environment_sha256']
        and b['snapshot_sha256']==replay['initial_snapshot_sha256'] for b in replay['branches']), 'paired initial environment differs')
    if not any(b.get('policy')==NAME for b in replay['branches']):return control_audit.audit_replay(replay)
    controls=[b for b in replay['branches'] if b.get('policy')!=NAME];control_proof=None;by_policy={}
    if controls:
        view=dict(replay,branches=controls,arms=[[b['policy'],b['rule']] for b in controls])
        control_proof=control_audit.audit_replay(view)
        by_policy={(b['policy'],b['rule']):p for b,p in zip(controls,control_proof['branch_audits'])}
    checked=[audit_branch(replay['scenario'],b) if b['policy']==NAME else by_policy[b['policy'],b['rule']] for b in replay['branches']]
    return dict(passed=True,branches=len(checked),branch_audits=checked,new_executions=0,
        preserved_parent_audit_proof=control_proof)

