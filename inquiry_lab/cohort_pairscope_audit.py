"""Independent activation of no-tail first-contact pairs with all residual owners.

Only inactive no-tail first-pair activation changes. The complete frozen
Envelope proof remains separately checked; no producer model, policy,
future event, actual outcome or extra scoring call is imported.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from . import cohort_envelope_audit as foundation
control_audit=foundation

ROOT=Path(__file__).resolve().parents[1]
if hashlib.sha256(Path(foundation.__file__).read_bytes()).hexdigest()!='d1398f787e6b1da241ccd6b993e5428e9ff02ff8bf92a467b1bdc463d1a57200':
    raise ValueError('frozen independent Pareto auditor differs')
base=foundation.base
parent,horizon,joint=foundation.parent,foundation.horizon,foundation.joint
numeric=foundation.numeric
dynamic=foundation.dynamic
require,precise_same,exact_copy=foundation.require,foundation.precise_same,foundation.exact_copy
_pid,_integer,EPOCH=foundation._pid,foundation._integer,foundation.EPOCH
INPUT_KEYS,CALL_KEYS,GROUP_KEYS=foundation.INPUT_KEYS,foundation.CALL_KEYS,foundation.GROUP_KEYS
NAME='eat_cohort_pairscope_l15_f'
BASE_POLICY=foundation.NAME
BASE_SOURCE_SHA256='6e4f54f71ad33fc37293fa4d5d389ae35209b46f4b187c0eff0aaa9650db3fc4'
if hashlib.sha256((ROOT/'inquiry_lab/cohort_envelope_policy.py').read_bytes()).hexdigest()!=BASE_SOURCE_SHA256:
    raise ValueError('frozen Pareto producer source differs')
NEW_INSTRUMENTATION=dict(control_audit.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_pairscope_runner.run_once',no_tail_first_pair_evaluation=True)


def _project_name(name):
    if name==NAME:return BASE_POLICY
    return '__foreign_cohort_envelope__' if name==BASE_POLICY else name


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
fullmenu=foundation.fullmenu
recourse=fullmenu.recourse
followplan=fullmenu.followplan
SCORING_SOURCE_SHA256={
    'cohort_fullmenu_model':'8185c654bb7402d67708b14bd151572b50950cc36d639c0b5153fa37eececaf1',
    'cohort_dynamic_policy':'62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb',
    'cohort_hedge_model':'348cc0d2207ce2a27ba8f98f12b62d69c592e66d50f10fa66f42b39f2f40d79e',
    'cohort_recourse_model':'e776bd643611a9ce33cbfa3fdbd8f05042fae755c20be5e0a1f4bddfdcebc2de',
    'cohort_followplan_model':'8f8dfc82451b05a804d33253823bde3a6cbb68ff67728d1458dbb17aca0d6268',
    'cohort_envelope_model':'c7a07257df98bc4435eee6c7253509d9ab9241ee2eab1062ad3563fc7cd57866',
    'cohort_stoploss_context':'a53774b1855e07fd15fb7436b0207db488ba84446f491f51692be01e3e0ba911',
}
for module,digest in SCORING_SOURCE_SHA256.items():
    require(hashlib.sha256((ROOT/'inquiry_lab'/(module+'.py')).read_bytes()).hexdigest()==digest,
        'frozen pair-scope scoring source differs: '+module)
RULE='inherited_Envelope_or_exact_two_eligible_first_contacts_with_complete_owner_guard'
OBJECTIVE='new no-tail rows retain original JOINT feasibility and cardinalities; uniform frozen Envelope scores, unchanged per-owner probability guard within1e-12, minimum-wait band within1e-9 then tier4,2,1 and original Stoploss actual anchor; inherited-active rows/actions untouched'
SCOPE='single activation-scope extension to exact-two eligible first contacts, not only two waiting owners; all captured residual owners protected with fresh original-prefix origins; inherited active and one-tail paths retained, current pair actions only; no future inputs or realized success guarantee'


def _sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def prepare(full,members,context,tails,priority):
    require(type(members) is list and len(members)==2 and type(tails) is list and tails==[] and len(context['eligible'])==2,
        'new scope requires exactly two eligible first contacts and no tail')
    views=fullmenu.parent_views(full['original_stoploss_proposal'])
    require(views['hedge']['evidence']['evaluated'] is False and views['hedge']['evidence']['owner_origins']==[],
        'fresh-origin factory requires the intact unevaluated Hedge parent')
    def identity(people,name):
        result=deepcopy(people)
        for person in result:person['workflow_snapshot']['job']['policy_name']=name
        return result
    dm=identity(members,dynamic.NAME);hm=identity(members,base.NAME);rm=identity(members,recourse.NAME)
    dc=views['dynamic']['evidence']['joint_context']
    require(dc['now']==context['now'],'pair-scope preparation changed the common clock')
    models=[dynamic.public_model(m['inputs']) for m in dm]
    independent=joint.enumerate_pairs(models,[p['send'] for p in views['joint']['baseline_proposals']])
    precise_same(views['joint']['combinations'],independent['combinations'],'independent original feasible JOINT table')
    owners=views['recourse']['evidence']['owner_context'];hedge_owners=views['hedge']['evidence']['owner_context']['inventory']
    first=[base.owner_key(pid) for pid in owners['first_cohort_patient_ids']]
    require(first==[base.owner_key(m['patient_id']) for m in members], 'complete first-owner partition is not the current pair')
    residual=[o for o in owners['inventory'] if base.owner_key(o['patient']['id']) not in set(first)]
    origins=[base.freeze_owner(o) for o in residual]
    require(len(residual)==len(origins),'fresh residual-origin factory omitted an owner')
    return dict(views=views,dynamic_members=dm,dynamic_tails=[],dynamic_context=dc,current_models=models,
        priority_context=priority,hedge_members=hm,recourse_members=rm,owners=owners,
        owner_by={base.owner_key(o['patient']['id']):o for o in hedge_owners},residual=residual,origins=origins,
        independent_table=independent)


def rebuild_row(recorded,row_index,menu_position,prepared):
    p=prepared;table=p['independent_table']['combinations']
    require(type(row_index) is int and 0<=row_index<len(table) and table[row_index]['feasible'],
        'new pair-scope row left original JOINT feasibility')
    require(type(recorded) is dict and type(recorded.get('dynamic_row_projection')) is dict,
        'missing newly materialized Dynamic projection')
    first,counters=dynamic.audit_row(recorded['dynamic_row_projection'],row_index,menu_position,table[row_index]['hospitals'],
        p['current_models'],p['dynamic_members'],p['dynamic_context'],[],p['priority_context'])
    require(first['projection']['steps']==[] and first['projection']['singleton'] is None,
        'no-tail scope fabricated later first-contact patients')
    initial=recourse.first_cohort_score(first,p['hedge_members'],p['priority_context'])
    discounts={}
    for job in initial['jobs']:discounts[job['hospital']]=discounts.get(job['hospital'],0.0)+job['discount_seconds']
    pending=first['projection']['final_pending']
    residual=[recourse.owner_score(owner,origin,pending,discounts) for owner,origin in zip(p['residual'],p['origins'])]
    require(len(residual)==len(p['residual'])==len(p['origins']),'new score dropped a residual owner or origin')
    reviews=followplan.first_review_sources(first,p['recourse_members'],p['views']['joint']['baseline_proposals']);plans=[]
    for review in reviews:
        pid=review['patient_id'];own=[j for j in initial['jobs'] if base.owner_key(j['patient_id'])==base.owner_key(pid)]
        plans.append(followplan.first_patient_score(p['owner_by'][base.owner_key(pid)],own,pending,initial['jobs'],review['next_review_at']))
    corrected=foundation.score_first_cohort(plans)
    residual_metrics=[dict(patient_id=r['patient_id'],effective_priority=r['effective_priority'],metrics=deepcopy(r['metrics'])) for r in residual]
    metrics=[dict(patient_id=r['patient_id'],effective_priority=r['effective_priority'],metrics=deepcopy(r['metrics'])) for r in corrected['patient_scores']]+residual_metrics
    require(sorted(base.owner_key(m['patient_id']) for m in metrics)==sorted(base.owner_key(o['patient']['id']) for o in p['owners']['inventory']),
        'new row omitted first or residual captured owner')
    wanted=dict(row_index=row_index,row_kind='new_no_tail_first_pair',dynamic_row_projection=first,
        first_plan_source_sha256=_sha(recorded['dynamic_row_projection']),first_cohort_envelope=corrected,
        unchanged_law_residual_member_metrics=residual_metrics,member_metrics=metrics,
        tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.0) for t in (4,2,1)},
        sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.0))
    precise_same(recorded,wanted,'complete independently rebuilt new first-pair row')
    return wanted,counters


def audit_pair(members,context,tails,outputs,evidence,priority_context,owner_context):
    require(type(evidence) is dict and type(evidence.get('original_envelope_proposal')) is dict
        and set(evidence['original_envelope_proposal'])=={'decisions','evidence'}, 'missing intact Envelope parent')
    original=evidence['original_envelope_proposal']
    checked=foundation.audit_pair([alias_member(m) for m in members],alias_context(context),[alias_member(m) for m in tails],
        original['decisions'],original['evidence'],priority_context,alias_owners(owner_context))
    priorities=audit_priority_context(priority_context,context,members,tails)
    old=original['evidence'];inherited=old['evaluated'];require(type(inherited) is bool,'typed inherited activation required')
    newly=not inherited and tails==[];evaluated=inherited or newly;base_index=old['selected_index'];selected=base_index
    wait=old['original_waitpareto_proposal']['evidence'];pareto=wait['original_pareto_proposal']['evidence'];full=pareto['original_fullmenu_proposal']['evidence']
    anchor=old['stoploss_anchor_index']
    require(anchor==wait['stoploss_anchor_index']==pareto['stoploss_anchor_index']==full['base_selected_index']==full['original_stoploss_proposal']['evidence']['selected_index'],
        'new pair-scope anchor differs from original Stoploss actual row')
    scores=[];origins=[];menu=[];feasible=[];guard=deepcopy(old['owner_probability_guard']);selection=deepcopy(old['protected_wait_selection']);coverage=None
    counts=Counter({key:0 for key in ('pairscope_groups_inherited','pairscope_groups_newly_evaluated','pairscope_groups_still_unevaluated',
        'pairscope_rows_checked','pairscope_residual_origins_checked','pairscope_anchor_checked','pairscope_interventions',
        'pairscope_future_jobs_checked','pairscope_score_owner_comparisons_checked')})
    if inherited:counts['pairscope_groups_inherited']=1
    elif newly:
        prepared=prepare(full,members,context,tails,priority_context)
        table=prepared['independent_table']['combinations']
        menu,added,feasible=fullmenu.full_feasible_menu([anchor],table)
        origins=prepared['origins'];precise_same(evidence.get('new_owner_origins'),origins,'complete fresh same-prefix residual origins')
        recorded=evidence.get('new_row_scores')
        require(type(recorded) is list and len(recorded)==len(menu) and [r['row_index'] for r in recorded]==menu,
            'new no-tail score inventory omitted or duplicated feasible rows')
        for position,(index,row) in enumerate(zip(menu,recorded)):
            rebuilt,row_checks=rebuild_row(row,index,position,prepared);scores.append(rebuilt)
            counts.update({'pairscope_'+key:value for key,value in row_checks.items()})
            counts['pairscope_future_jobs_checked']+=len(rebuilt['first_cohort_envelope']['jobs'])
        guard=foundation.foundation.foundation.guard_rows(scores,baseline_index=anchor,owner_inventory=owner_context['inventory'],menu_indices=menu,feasible_row_indices=feasible)
        selection=foundation.foundation.select_protected_wait(scores,admissible_row_indices=guard['admissible_row_indices'],baseline_index=anchor,
            owner_inventory=owner_context['inventory'],menu_indices=menu,feasible_row_indices=feasible)
        selected=selection['selected_index']
        coverage=dict(original_joint_row_count=len(table),evaluated_row_count=len(scores),feasible_row_count=len(feasible),
            fresh_residual_origin_count=len(origins),captured_owner_count=len(owner_context['inventory']),first_contact_count=2,anchor_originally_feasible=True)
        counts.update(pairscope_groups_newly_evaluated=1,pairscope_rows_checked=len(scores),pairscope_residual_origins_checked=len(origins),pairscope_anchor_checked=1,pairscope_score_owner_comparisons_checked=len(scores)*len(owner_context['inventory']))
    else:counts['pairscope_groups_still_unevaluated']=1
    intervened=selected!=base_index;counts['pairscope_interventions']=int(intervened)
    reason='inherited_active' if inherited else 'new_no_tail_first_pair' if newly else 'inherited_inactive_with_tail'
    wanted=dict(schema_version=1,rule=RULE,base_policy=BASE_POLICY,base_source_sha256=BASE_SOURCE_SHA256,scoring_source_sha256=deepcopy(SCORING_SOURCE_SHA256),
        identity_projection=dict(source_policy=NAME,calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_envelope__'),
        joint_context=context,priority_context=priority_context,owner_context=owner_context,original_envelope_proposal=original,
        evaluated=evaluated,inherited_evaluated=inherited,newly_activated=newly,activation_reason=reason,
        new_owner_origins=origins,new_menu_indices=menu,new_feasible_row_indices=feasible,new_row_scores=scores,new_coverage=coverage,
        score_source='new_row_scores' if newly else 'original_envelope_proposal.evidence.new_row_scores' if inherited else 'none',
        owner_probability_guard=guard,protected_wait_selection=selection,base_selected_index=base_index,stoploss_anchor_index=anchor,
        selected_index=selected,intervened=intervened,tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=OBJECTIVE,scope=SCOPE)
    precise_same(evidence,wanted,'complete independent pair-scope activation and protected choice')
    require(type(outputs) is list and len(outputs)==2,'actual pair-scope output inventory differs')
    table=fullmenu.parent_views(full['original_stoploss_proposal'])['joint'];chosen=next((r for r in scores if r['row_index']==selected),None)
    for index in range(2):
        actual=deepcopy(original['decisions'][index])
        if intervened:
            sends=table['combinations'][selected]['hospitals'][index]
            actual=deepcopy(table['baseline_proposals'][index]);actual['send']=list(sends);actual['reason']='cohort_pairscope_current_only'
            ev=actual['evidence'];ev['sent_hospitals']=list(sends);ev['planned_expected_cost']=round(chosen['member_metrics'][index]['metrics']['total_cost_seconds'],3)
            for row in ev['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in sends else None
        actual['evidence']['policy']=NAME
        actual['evidence']['cohort_pairscope']=dict(schema_version=1,mode='pair_cohort_pairscope',evaluated=evaluated,
            inherited_evaluated=inherited,newly_activated=newly,intervened=intervened,member_index=index,
            base_selected_index=base_index,stoploss_anchor_index=anchor,selected_index=selected,effective_priority=priorities[_pid(members[index]['patient_id'])])
        precise_same(outputs[index],actual,'independently reconstructed current pair-scope action')
    checked=dict(checked,original_Envelope_groups_applied=checked['joint_groups_applied'],joint_groups_applied=int(selected!=table['baseline_index']))
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
            require(output['evidence'].get('cohort_pairscope') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed stoploss scope')
            require(output['evidence'].get('cohort_envelope') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Envelope scope')
            require(output['evidence'].get('cohort_waitpareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Waitpareto scope')
            require(output['evidence'].get('cohort_pareto') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Pareto scope')
            require(output['evidence'].get('cohort_fullmenu') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed original Fullmenu scope')
            require(output['evidence'].get('cohort_stoploss') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False), 'late fallback changed parent Stoploss scope')
            normalized = deepcopy(output); normalized['evidence'].pop('cohort_pairscope'); normalized['evidence'].pop('cohort_envelope'); normalized['evidence'].pop('cohort_waitpareto'); normalized['evidence'].pop('cohort_pareto'); normalized['evidence'].pop('cohort_fullmenu'); normalized['evidence'].pop('cohort_stoploss'); normalized['evidence'].pop('cohort_followplan'); normalized['evidence'].pop('cohort_recourse')
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
            original_Envelope_proposal_independently_checked=True,
            new_activation_exactly_no_tail_first_pair=True,
            all_residual_owners_frozen_at_same_public_prefix=True,
            inherited_active_groups_unchanged=True,
            one_tail_inactive_groups_unchanged=True,
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
            original_active_groups_preserved=True,
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
            scope='intact independently audited Envelope proposal; inherited active and one-tail inactive groups retained; only inactive groups with exactly two eligible first contacts newly evaluated, with all captured residual owners freshly frozen and scored at the same public prefix; original feasible table, anchor, Dynamic projection and unchanged Recourse/Followplan/Envelope guard/selector/caps/FIFO independently checked; no physical queue or realized-success guarantee')
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

