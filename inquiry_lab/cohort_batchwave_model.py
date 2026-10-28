"""Preserve one frozen CS-reference first wave and match its primary queries.

The reference is not an actual Tailwait rollout. Only primary submissions use
the fixed public worker budget; every other preserved query may queue normally.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from . import cohort_dynamic_policy as dynamic,cohort_fullmenu_model as fullmenu
from . import cohort_startslots_model as matching

NAME='eat_cohort_batchwave_l15_f'
JOINT=dynamic.JOINT
HORIZON=dynamic.HORIZON
EPSILON=matching.EPSILON
TIERS=(4,2,1)
RULE='preserve_single_Stoploss_anchor_frozen_CS_reference_wave_primary_cover_then_all_remaining_queries'
SCOPE='empty common public queue, 4..16 simultaneous first owners; one same-clock Dynamic reference at original Stoploss actual anchor, current pair plus frozen CS-reference tails/singleton; preserve every reference query set and count, not an actual Tailwait continuation; match one primary within each preserved set using public H worker budget, then submit every remaining query; entire roster is actual atomic batch, no old probability/wait floor or actual success/wait/arrival guarantee'
DISPATCH_RULE='matched primaries in higher effective priority then native owner order; all remaining preserved queries in native owner and reference send order; actual hospital FIFO unchanged'
SOURCE_SHA256={'cohort_dynamic_policy':'62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb',
    'cohort_fullmenu_model':'8185c654bb7402d67708b14bd151572b50950cc36d639c0b5153fa37eececaf1',
    'cohort_startslots_model':'6ebaa7635c718e69c5d99a9d45537447aeff47b7e123f6b2066ed2269244f299'}
for name,digest in SOURCE_SHA256.items():
    if hashlib.sha256(Path(__file__).with_name(name+'.py').read_bytes()).hexdigest()!=digest:
        raise ValueError('frozen batch-wave source differs: '+name)


def _key(value):return matching._key(value)
def _sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()
scope_members=matching.scope_members


def parent_views(parent):
    shield=parent['evidence']['original_pairshield_proposal']
    pairscope=shield['evidence']['original_pairscope_proposal']
    envelope=pairscope['evidence']['original_envelope_proposal']
    wait=envelope['evidence']['original_waitpareto_proposal']
    pareto=wait['evidence']['original_pareto_proposal']
    full=pareto['evidence']['original_fullmenu_proposal']
    stop=full['evidence']['original_stoploss_proposal']
    views=fullmenu.parent_views(stop)
    return dict(stoploss=stop,fullmenu=full,**views)


def build_batch_reference(parent_tailwait_proposal,*,members,tail_members,joint_context,priority_context):
    roster=members+tail_members;eligible,reason=scope_members(roster)
    if not eligible:raise ValueError('batch reference outside scope: '+reason)
    if len(members)!=2:raise ValueError('original current pair required')
    views=parent_views(parent_tailwait_proposal);joint=views['joint'];anchor=views['stoploss']['evidence']['selected_index']
    if type(anchor) is not int or not 0<=anchor<len(joint['combinations']):raise ValueError('original Stoploss actual anchor required')
    if anchor!=parent_tailwait_proposal['evidence']['stoploss_anchor_index']:raise ValueError('Tailwait and original Stoploss anchors differ')
    # The frozen context projections change these calculation labels only.
    # Native patient identity, public clocks, ledger and complete roster stay fixed.
    people=deepcopy(roster);cs=deepcopy(joint_context)
    for m in people:m['workflow_snapshot']['job']['policy_name']=dynamic.BASE_POLICY
    for row in cs['eligible']:row['job']['policy_name']=dynamic.BASE_POLICY
    current=people[:2];tails=people[2:]
    dynamic.context.original.validate_context(cs)
    dynamic.context.original.validate_tail_members(cs,tails,members=current)
    models=[JOINT._model(m['inputs'],p) for m,p in zip(current,joint['baseline_proposals'])]
    row=dynamic._score(anchor,0,joint['combinations'][anchor]['hospitals'],members=current,tail_members=tails,
        joint_context=cs,priority_context=priority_context,current_models=models)
    records=[]
    def add(people_in,proposals,sends,kind,step):
        for mi,(m,p,send) in enumerate(zip(people_in,proposals,sends)):
            records.append(dict(owner_index=len(records),patient_id=m['patient_id'],kind=kind,step_index=step,member_index=mi,
                reference_send=deepcopy(send),reference_cardinality=len(send),reference_proposal=deepcopy(p)))
    add(current,joint['baseline_proposals'],row['projection']['current_pair_send'],'original_Stoploss_anchor_current_pair',None)
    for step in row['projection']['steps']:
        add(step['members'],step['proposal']['decisions'],[p['send'] for p in step['proposal']['decisions']],
            'frozen_CS_reference_tail_pair',step['step_index'])
    singleton=row['projection']['singleton']
    if singleton is not None:add([singleton['member']],[singleton['proposal']],[singleton['proposal']['send']],
        'frozen_H_reference_singleton',len(row['projection']['steps']))
    if [_key(r['patient_id']) for r in records]!=[_key(m['patient_id']) for m in roster]:raise ValueError('complete reference owner order differs')
    return dict(schema_version=1,rule=RULE,source_sha256=deepcopy(SOURCE_SHA256),scope=SCOPE,
        stoploss_actual_anchor_index=anchor,original_JOINT_anchor_feasible=joint['combinations'][anchor]['feasible'],
        original_JOINT_anchor_hospitals=deepcopy(joint['combinations'][anchor]['hospitals']),
        original_Stoploss_proposal_sha256=_sha(views['stoploss']),calculation_identity=dynamic.BASE_POLICY,
        identity_projection_fields=['joint_context.eligible[].job.policy_name','members_and_tail[].workflow_snapshot.job.policy_name'],
        dynamic_reference_row=row,dynamic_reference_row_sha256=_sha(row),owner_references=records,
        actual_Tailwait_continuation_claim=False,residual_owner_replanning=False)


def allocate_reference(reference,*,roster,priority_context):
    eligible,reason=scope_members(roster)
    if not eligible:raise ValueError('batch matching outside scope: '+reason)
    records=reference['owner_references'];n=len(roster)
    row=reference['dynamic_reference_row']
    if _sha(row)!=reference['dynamic_reference_row_sha256']:raise ValueError('frozen reference projection digest differs')
    reference_sends=deepcopy(row['projection']['current_pair_send'])
    for step in row['projection']['steps']:reference_sends.extend(deepcopy(p['send']) for p in step['proposal']['decisions'])
    if row['projection']['singleton'] is not None:reference_sends.append(deepcopy(row['projection']['singleton']['proposal']['send']))
    if [r['reference_send'] for r in records]!=reference_sends:raise ValueError('preserved sets differ from frozen reference projection')
    if len(records)!=n or [_key(r['patient_id']) for r in records]!=[_key(m['patient_id']) for m in roster]:raise ValueError('complete reference roster required')
    if len({_key(m['patient_id']) for m in roster})!=n:raise ValueError('unique typed batch owner identity required')
    by={_key(p['patient_id']):p['effective_priority'] for p in priority_context['patients']}
    if len(priority_context['patients'])!=n or set(by)!={_key(m['patient_id']) for m in roster} or any(type(t) is not int or t not in TIERS for t in by.values()):
        raise ValueError('complete public batch priority required')
    config=HORIZON.spec()['config'];workers=config['assumed_workers']
    if type(workers) is not int or workers<1:raise ValueError('fixed positive integer public worker budget required')
    baselines=[HORIZON.decide(HORIZON.NAME,**deepcopy(m['inputs'])) for m in roster]
    models=[JOINT._model(m['inputs'],p) for m,p in zip(roster,baselines)]
    edges=[];hospitals=[];metrics=[]
    for i,(m,r,model) in enumerate(zip(roster,records,models)):
        send=r['reference_send'];pool=[x['hospital'] for x in model['hospitals']]
        if type(send) is not list or len(set(send))!=len(send) or not set(send)<=set(pool) or r['reference_cardinality']!=len(send) or len(send)>min(config['max_concurrent'],m['inputs']['max_new']):
            raise ValueError('reference set/cardinality exceeds unchanged public pool or budget')
        if len(pool)>6 or any(x['residual_wait_seconds']!=0 for x in model['hospitals']):raise ValueError('empty common-prefix H model required')
        for pi,h in enumerate(pool):
            if h not in send:continue
            if h not in hospitals:hospitals.append(h)
            edge_metrics=JOINT._metrics(model,[h],{})
            edges.append(dict(owner_index=i,patient_id=m['patient_id'],hospital=h,pool_index=pi,
                reference_send_index=send.index(h),total_cost_seconds=edge_metrics['total_cost_seconds'],metrics=edge_metrics))
        metrics.append(dict(owner_index=i,patient_id=m['patient_id'],reference_send=deepcopy(send),reference_cardinality=len(send),
            common_prefix_uncontended_metrics=JOINT._metrics(model,send,{})))
    order=sorted(range(n),key=lambda i:(-by[_key(roster[i]['patient_id'])],i))
    caps=[dict(hospital=h,capacity=workers) for h in hospitals]
    cover=matching.cover_slots(edges,owner_count=n,hospital_capacities=caps,owner_order=order) if caps else dict(
        schema_version=1,algorithm='no_preserved_reference_primary_slots',flow=0,required_flow=n,complete=False,assignment=[],augmentations=[])
    dispatch=[]
    if cover['complete']:
        primary={r['owner_index']:r['hospital'] for r in cover['assignment']}
        def add(i,h,phase):
            dispatch.append(dict(dispatch_index=len(dispatch),phase=phase,owner_index=i,patient_id=roster[i]['patient_id'],hospital=h,
                reference_send_index=records[i]['reference_send'].index(h)))
        for i in order:add(i,primary[i],'matched_primary')
        for i,r in enumerate(records):
            for h in r['reference_send']:
                if h!=primary[i]:add(i,h,'remaining_preserved_reference')
        expected={(i,h) for i,r in enumerate(records) for h in r['reference_send']}
        if len(dispatch)!=len(expected) or {(r['owner_index'],r['hospital']) for r in dispatch}!=expected:raise ArithmeticError('preserved reference dispatch partition differs')
    return dict(schema_version=1,rule=RULE,source_sha256=deepcopy(SOURCE_SHA256),scope=SCOPE,
        public_assumed_workers=workers,slot_capacity_source='frozen_H_config.assumed_workers_for_primary_wave_only',
        common_prefix_H_proposals=baselines,owner_order=order,primary_edges=edges,coverage=cover,
        coverage_complete=cover['complete'],reference_owner_metrics=metrics,dispatch_rule=DISPATCH_RULE,dispatch_order=dispatch,
        all_reference_queries_preserved=cover['complete'],secondary_queries_may_queue_normally=True,
        whole_wave_metrics_are_uncontended_reference_only=True,old_probability_wait_guards_applicable=False)
