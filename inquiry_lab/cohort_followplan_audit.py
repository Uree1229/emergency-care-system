"""Independent first-cohort future-plan audit, using frozen analytic auditors only.

The complete Recourse proposal remains independently checked. New hypothetical
plans derive only from its verified pass1 jobs and the current public capture;
no producer model, policy, runner, oracle or future event is imported.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from . import cohort_recourse_audit as recourse

ROOT=Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(recourse.__file__).read_bytes()).hexdigest()!='3dbb240cbbdc5c44968ba9fbae866487f5cdab2fbef6b953525bf230d9676a2c':
    raise ValueError('frozen independent Recourse auditor differs')
base=recourse.base
parent,horizon,joint=recourse.parent,recourse.horizon,recourse.joint
numeric=recourse.numeric
dynamic=recourse.dynamic
require,precise_same,exact_copy=recourse.require,recourse.precise_same,recourse.exact_copy
_pid,_integer,EPOCH=recourse._pid,recourse._integer,recourse.EPOCH
INPUT_KEYS,CALL_KEYS,GROUP_KEYS=recourse.INPUT_KEYS,recourse.CALL_KEYS,recourse.GROUP_KEYS
NAME='eat_cohort_followplan_l15_f'
BASE_POLICY=recourse.NAME
BASE_SOURCE_SHA256='191591bc184ae3643c97d0c801800bae507675f94d603423f7553e00e317b961'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_recourse_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Recourse producer source differs')
NEW_INSTRUMENTATION=dict(recourse.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_followplan_runner.run_once',first_contact_static_H_continuation_added=True)


def _project_name(name):
    if name==NAME:return BASE_POLICY
    return '__foreign_cohort_recourse__' if name==BASE_POLICY else name


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
    value=recourse.audit_registry(alias_events(events),decisions,alias_registry(registry),now,event_prefix,decision_prefix)
    for row in value['eligible']:row['job']['policy_name']=NAME
    return value


def audit_priority_context(priority,context,members,tails,*,lab_defaults=False):
    return recourse.audit_priority_context(priority,alias_context(context),[alias_member(m) for m in members],
        [alias_member(m) for m in tails],lab_defaults=lab_defaults)


def audit_time_context(events,inputs,member,event_prefix,decision_prefix,decision=None):
    return recourse.audit_time_context(alias_events(events),inputs,alias_member(member),event_prefix,decision_prefix,decision)


def audit_owner_provenance(scenario,events,registry,owners,source):
    return recourse.audit_owner_provenance(scenario,alias_events(events),alias_registry(registry),alias_owners(owners),alias_source(source))


def audit_member_provenance(scenario,events,member,event_prefix,decision_prefix):
    return recourse.audit_member_provenance(scenario,alias_events(events),alias_member(member),event_prefix,decision_prefix)


MODEL_RULE='initial_pass1_CDF_plus_separate_static_next_review_H_continuation'
MODEL_SCOPE='hypothetical origin-clock first-contact continuation; original public input and fixed F0 work unchanged; conservative no-terminal-reply occupancy, no future-event input, interowner future contention or later-action guarantee'
RULE='intact_Recourse_menu_plus_first_patient_static_next_review_H_continuation'
OBJECTIVE='global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer Recourse actual index then original D menu order'
SCOPE='complete current WAITING/H-positive roster; initial first-cohort sends and fixed F0 work unchanged; separate hypothetical local H continuations for first patients, unchanged residual scores; no actual future-action or outcome guarantee'


def first_review_sources(first,members,baseline):
    """Read only next-review fields from already-audited initial proposals."""
    reviews=[]
    def collect(people,proposals,kind,step):
        require(len(people)==len(proposals),'initial review proposal inventory differs')
        for index,(member,proposal) in enumerate(zip(people,proposals)):
            reviews.append(dict(patient_id=member['patient_id'],kind=kind,step_index=step,
                member_index=index,next_review_at=proposal['next_review_at']))
    collect(members,baseline,'current_pair_H_baseline',None)
    projection=first['projection']
    for step in projection['steps']:
        collect(step['members'],step['proposal']['decisions'],'reference_CS_decision',step['step_index'])
    singleton=projection['singleton']
    if singleton is not None:
        collect([singleton['member']],[singleton['proposal']],'reference_H_singleton',len(projection['steps']))
    return reviews


def first_patient_score(owner,initial_jobs,row_pending,all_jobs,next_review_at):
    """Derive a hypothetical continuation; committed source fields stay intact.

    Initial distributions come directly from independently verified pass1 jobs,
    never the generic half-tie queue estimate. The separate occupancy descriptor
    counts each initial contact as one fresh job, even if its expected work is
    discounted for another patient's prospective queue.
    """
    source=deepcopy(owner);pid=source['patient']['id'];now=source['inputs']['now']
    require(type(next_review_at) is int and next_review_at>=now,'initial public next-review clock differs')
    require(not source['inquiries'] and not source['inputs']['patient_pending'] and not source['valid_acceptance_ids'],
        'first continuation replaced a real inquiry or acceptance')
    require(source['job'] is not None and source['job']['policy_name']==base.NAME,
        'first continuation lacks original supported calculation identity')
    origin=base.freeze_owner(source)
    require(not origin['original_all_own'] and not origin['own_items'],'initial owner already has own work')
    exact_copy(row_pending,source['inputs']['pending']+[j['source']['inquiry'] for j in all_jobs],
        'hypothetical plan complete row public queue')
    wanted=[j for j in all_jobs if base.owner_key(j['patient_id'])==base.owner_key(pid)]
    exact_copy(initial_jobs,wanted,'all and only initial own virtual jobs')
    hospitals=[j['hospital'] for j in initial_jobs]
    require(len(hospitals)==len(set(hospitals)) and len(hospitals)<=source['inputs']['max_new'],
        'hypothetical initial contact budget or uniqueness differs')
    for job in initial_jobs:
        exact_copy(job['source']['inquiry'],dict(hospital=job['hospital'],patient=pid,submitted_at=now),
            'initial virtual inquiry source owner and clock')
    hypothesis=deepcopy(source)
    hypothesis['inputs']['candidates']=[deepcopy(c) for c in source['inputs']['candidates'] if c['hospital_id'] not in hospitals]
    hypothesis['inputs']['max_new']=source['inputs']['max_new']-len(initial_jobs)
    hypothesis['inputs']['last_contact_at']=now if initial_jobs else source['inputs']['last_contact_at']
    hypothesis['job']['next_evaluation_at']=next_review_at
    frozen=deepcopy(origin)
    frozen['original_all_own']=[deepcopy(j['source']['inquiry']) for j in initial_jobs]
    bins=horizon.intervals(source['evaluation_end_at']-now);items=[]
    for job in initial_jobs:
        require(job['cell_edges_seconds']==origin['cell_edges_seconds']
            and job['owner_horizon_seconds']==source['evaluation_end_at']-now,'initial CDF partial H grid differs')
        item=dict(hospital=job['hospital'],travel_seconds=job['travel_seconds'],service_mu=job['service_mu'],
            service_sigma=job['service_sigma'],total_probability=job['reply_probability']*job['accept_probability'],
            mode='queued',wait_seconds=job['pass1_wait_seconds'],spent_seconds=0.0,f0=0.0)
        item['masses']=horizon.masses(lambda x,item=item:base.descriptor_cdf(item,x),1.0,bins)
        items.append(item)
    frozen['own_items']=items
    discounts={}
    for job in all_jobs:discounts[job['hospital']]=discounts.get(job['hospital'],0.0)+job['discount_seconds']
    # This is only a calculation-label alias: the recorded hypothetical owner
    # remains the exact Hedge-label copy described above. No DB ID/version is
    # invented, and no original own distribution is re-frozen after virtual work.
    calc=deepcopy(hypothesis);calc['job']['policy_name']=BASE_POLICY
    continuation=recourse.owner_score(calc,frozen,row_pending,discounts)
    initial_cost=sum((j['inquiry_cost_seconds'] for j in initial_jobs),0.0)
    metrics=deepcopy(continuation['metrics']);metrics['immediate_contact_cost_seconds']=initial_cost
    metrics['total_cost_seconds']+=initial_cost
    return dict(schema_version=1,rule=MODEL_RULE,scope=MODEL_SCOPE,patient_id=pid,
        effective_priority=source['patient']['effective_priority'],source_owner=source,
        initial_job_indices=[j['job_index'] for j in initial_jobs],next_review_at=next_review_at,
        source_origin=origin,hypothetical_owner=hypothesis,hypothetical_origin=frozen,
        initial_contact_cost_seconds=initial_cost,continuation=continuation,metrics=metrics)


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
    require(type(evidence) is dict and type(evidence.get('original_recourse_proposal')) is dict
        and set(evidence['original_recourse_proposal'])=={'decisions','evidence'},'missing intact original Recourse proposal')
    original=evidence['original_recourse_proposal']
    bm=[alias_member(m) for m in members];bt=[alias_member(m) for m in tails]
    bc=alias_context(context);bo=alias_owners(owner_context)
    checked=recourse.audit_pair(bm,bc,bt,original['decisions'],original['evidence'],priority_context,bo)
    priorities=audit_priority_context(priority_context,context,members,tails)
    old=original['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    menu=old['menu_indices'];scores=[];selection=None
    counts=Counter({k:0 for k in ('followplan_menus_evaluated','followplan_rows_checked',
        'followplan_first_patient_plans_checked','followplan_future_occupancy_waves_checked')})
    hedge=old['original_hedge_proposal'];dynamic_proposal=hedge['evidence']['original_dynamic_proposal']
    table=dynamic._nested_joint(dynamic_proposal['evidence']['original_cohort_success_proposal'])['evidence']
    if evaluated:
        require(1<=len(menu)<=3 and type(evidence.get('row_scores')) is list
            and len(evidence['row_scores'])==len(menu),'futureplan altered intact bounded menu')
        owner_by={base.owner_key(o['patient']['id']):o for o in hedge['evidence']['owner_context']['inventory']}
        by_index={r['row_index']:r for r in old['row_scores']}
        for index,recorded in zip(menu,evidence['row_scores']):
            parent_row=by_index[index]
            require(type(recorded) is dict and set(recorded)=={'row_index','base_row_score','first_review_sources',
                'first_cohort_future','owner_scores','member_metrics','tier_probabilities','sum_capped_wait_seconds'},
                'futureplan row fields differ')
            exact_copy(recorded['base_row_score'],parent_row,'intact independently audited Recourse row')
            first=parent_row['base_row_score']['first_cohort_score'];initial=parent_row['first_cohort']
            reviews=first_review_sources(first,bm,table['baseline_proposals']);plans=[]
            for review in reviews:
                pid=review['patient_id'];jobs=[j for j in initial['jobs'] if base.owner_key(j['patient_id'])==base.owner_key(pid)]
                plans.append(first_patient_score(owner_by[base.owner_key(pid)],jobs,
                    first['projection']['final_pending'],initial['jobs'],review['next_review_at']))
            residual=deepcopy(parent_row['owner_scores'])
            values=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in plans+residual]
            require(sorted(base.owner_key(v['patient_id']) for v in values)
                ==sorted(base.owner_key(o['patient']['id']) for o in owner_context['inventory']),
                'futureplan complete waiting score partition differs')
            wanted=dict(row_index=index,base_row_score=parent_row,first_review_sources=reviews,first_cohort_future=plans,
                owner_scores=residual,member_metrics=values,
                tier_probabilities={str(t):sum((v['metrics']['selection_probability'] for v in values if v['effective_priority']==t),0.0) for t in (4,2,1)},
                sum_capped_wait_seconds=sum((v['metrics']['capped_wait_seconds'] for v in values),0.0))
            precise_same(recorded,wanted,'independent first-patient static continuation row')
            scores.append(wanted);counts['followplan_rows_checked']+=1
            counts['followplan_first_patient_plans_checked']+=len(plans)
            counts['followplan_future_occupancy_waves_checked']+=sum(len(p['continuation']['occupancy']) for p in plans)
        selection=dynamic.base.global_staged_choice(scores,base_index)
        selection['base_selected_tied']=selection.pop('base_budget_tied');selected=selection['selected_index']
        counts['followplan_menus_evaluated']+=1
    else:require(menu==[] and evidence.get('row_scores')==[],'unevaluated Recourse invented futureplan rows')
    intervened=selected!=base_index
    wanted=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_recourse__'),
        joint_context=context,priority_context=priority_context,owner_context=owner_context,original_recourse_proposal=original,
        evaluated=evaluated,menu_indices=menu,row_scores=scores,selection=selection,base_selected_index=base_index,
        selected_index=selected,intervened=intervened,tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),
        objective=OBJECTIVE,scope=SCOPE)
    precise_same(evidence,wanted,'complete independent futureplan priority choice')
    require(type(outputs) is list and len(outputs)==2,'actual futureplan pair outputs missing')
    chosen=next((r for r in scores if r['row_index']==selected),None)
    for index in range(2):
        actual=deepcopy(original['decisions'][index])
        if intervened:
            hospitals=table['combinations'][selected]['hospitals'][index]
            actual=deepcopy(table['baseline_proposals'][index]);actual['send']=list(hospitals)
            actual['reason']='cohort_followplan_current_only';ev=actual['evidence'];ev['sent_hospitals']=list(hospitals)
            ev['planned_expected_cost']=round(chosen['member_metrics'][index]['metrics']['total_cost_seconds'],3)
            for row in ev['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in hospitals else None
        actual['evidence']['policy']=NAME
        actual['evidence']['cohort_followplan']=dict(schema_version=1,mode='pair_cohort_followplan',evaluated=evaluated,
            intervened=intervened,member_index=index,base_selected_index=base_index,selected_index=selected,
            effective_priority=priorities[_pid(members[index]['patient_id'])])
        precise_same(outputs[index],actual,'separately reconstructed actual futureplan outputs')
    checked=dict(checked,original_Recourse_groups_applied=checked['joint_groups_applied'],
        joint_groups_applied=int(selected!=table['baseline_index']))
    return dict(checked,**dict(counts),followplan_interventions=int(intervened))

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
            normalized = deepcopy(output); normalized['evidence'].pop('cohort_followplan'); normalized['evidence'].pop('cohort_recourse')
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
        return recourse.audit_replay(view)['branch_audits'][0]
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
            fixed_F0_one_pass_expected_work_independently_rebuilt=True,
            first_patient_static_next_review_continuation_independently_rebuilt=True,
            hypothetical_descriptors_separate_from_committed_source=True,
            actual_pending_and_own_CDF_unchanged=True,integer_occupancy_and_contact_budget_independently_checked=True,
            actual_outputs_sends_and_all_patient_FIFO_checked=True,physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='complete actual source captures and prefix/version checks; physical atomicity needs frozen adapter and rollback tests',
            scope='intact original Recourse proposal and fixed row-specific initial CS sends; independently rebuilt first-patient hypothetical next-review H plans, initial pass1 CDFs, source barriers, contact budgets and integer occupancy; residual owner scores unchanged; late H unchanged; actual all-patient trace accounting')
    except (KeyError,TypeError,IndexError,AttributeError,OverflowError,StopIteration) as error:
        raise base.CohortHedgeAuditError('malformed saved cohort-followplan artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version']==1 and type(replay['branches']) is list,'invalid complete replay')
    require(replay['arms']==[[b['policy'],b['rule']] for b in replay['branches']]
        and len({tuple(a) for a in replay['arms']})==len(replay['arms'])>0,'complete arm inventory differs')
    require(all(b['environment_sha256']==replay['environment_sha256']
        and b['snapshot_sha256']==replay['initial_snapshot_sha256'] for b in replay['branches']), 'paired initial environment differs')
    if not any(b.get('policy')==NAME for b in replay['branches']):return recourse.audit_replay(replay)
    controls=[b for b in replay['branches'] if b.get('policy')!=NAME];control_proof=None;by_policy={}
    if controls:
        view=dict(replay,branches=controls,arms=[[b['policy'],b['rule']] for b in controls])
        control_proof=recourse.audit_replay(view)
        by_policy={(b['policy'],b['rule']):p for b,p in zip(controls,control_proof['branch_audits'])}
    checked=[audit_branch(replay['scenario'],b) if b['policy']==NAME else by_policy[b['policy'],b['rule']] for b in replay['branches']]
    return dict(passed=True,branches=len(checked),branch_audits=checked,new_executions=0,
        preserved_parent_audit_proof=control_proof)

