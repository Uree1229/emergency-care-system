"""Public initial review-slot coverage, then positive marginal extra contacts.

The workers are the fixed H belief budget, never latent simulator staffing.
Roster-wide assignments are local plans, not reservations or future actions.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from . import joint_first_policy as JOINT

NAME='eat_cohort_startslots_l15_f'
EPSILON=JOINT.WAIT_EPSILON
TIERS=(4,2,1)
RULE='initial_public_worker_slot_minimum_cost_owner_coverage_then_positive_marginal_extras'
SCOPE='empty common public queue, 4..16 simultaneous first-contact owners; cover every owner once if feasible using fixed H assumed workers; additional contacts only in remaining slots up to original H immediate cardinalities and public limits; zero added queued work within that belief budget; current pair only is executed, no reservations, probability floor or actual success/wait/arrival guarantee'
TIE_RULE='coverage shortest augmentations visit higher effective priority then native owner order and original H pool order; extras use greatest positive marginal cost decrease beyond inherited1e-9, then higher priority/native owner/pool order'
SOURCE_SHA256={'joint_first_policy':'c176b2c5e4fd4ccb459229efb86661ad6120c8f39525414352738e40ee7f6dcc',
               'horizon_policy':'5061be48175098ce6a30be2af0f0721852e2d67d8bd75ab475a202bcee3ce387'}
for name,digest in SOURCE_SHA256.items():
    if hashlib.sha256(Path(__file__).with_name(name+'.py').read_bytes()).hexdigest()!=digest:
        raise ValueError('frozen start-slot source differs: '+name)


def _key(value):
    if type(value) not in (int,str):raise ValueError('typed int or string patient identity required')
    return type(value).__name__,str(value)


def scope_members(members):
    if type(members) is not list or not 4<=len(members)<=16:return False,'first_roster_size_outside_4_to_16'
    starts=[m['workflow_snapshot']['job']['started_at'] for m in members]
    if any(type(s) is not int for s in starts) or len(set(starts))!=1:return False,'different_public_workflow_start_clocks'
    if any(m['inputs']['pending'] for m in members):return False,'nonempty_common_public_pending'
    if any(m['inputs']['patient_pending'] or m['inputs']['last_contact_at'] is not None for m in members):return False,'not_first_contact'
    return True,'simultaneous_empty_queue_first_roster'


def _number(x):
    if type(x) not in (int,float) or not math.isfinite(x):raise ValueError('finite edge cost required')
    return float(x)


def cover_slots(edge_rows,*,owner_count,hospital_capacities,owner_order):
    """Unit owner demand min-cost flow with a full residual augmentation proof."""
    if type(owner_count) is not int or not 1<=owner_count<=16:raise ValueError('bounded positive typed owner count required')
    if type(owner_order) is not list or any(type(i) is not int for i in owner_order) or sorted(owner_order)!=list(range(owner_count)):
        raise ValueError('complete typed owner order required')
    if type(hospital_capacities) is not list or not hospital_capacities:raise ValueError('public hospital capacity list required')
    hospitals=[];caps={}
    for r in hospital_capacities:
        h=r['hospital'];cap=r['capacity']
        if type(h) is not str or not h or h in caps or type(cap) is not int or cap<1:raise ValueError('unique hospitals with integer public capacities required')
        hospitals.append(h);caps[h]=cap
    if type(edge_rows) is not list:raise ValueError('complete typed edge list required')
    seen=set();edges=[]
    for r in edge_rows:
        i=r['owner_index'];h=r['hospital'];cost=_number(r['total_cost_seconds'])
        if type(i) is not int or not 0<=i<owner_count or h not in caps or (i,h) in seen:raise ValueError('unique bounded owner-hospital edge required')
        seen.add((i,h));edges.append(dict(owner_index=i,hospital=h,total_cost_seconds=cost))
    # Stable node and adjacency order gives a reproducible shortest-path proof.
    nodes=['source']+['owner:'+str(i) for i in owner_order]+['hospital:'+str(i) for i in range(len(hospitals))]+['sink']
    lookup={n:i for i,n in enumerate(nodes)};source=0;sink=len(nodes)-1;graph=[[] for _ in nodes];forward=[]
    def add(u,v,cap,cost,kind,owner=None,hospital=None):
        a=dict(to=v,rev=len(graph[v]),capacity=cap,cost=cost,kind=kind,owner_index=owner,hospital=hospital,reverse=False)
        b=dict(to=u,rev=len(graph[u]),capacity=0,cost=-cost,kind=kind,owner_index=owner,hospital=hospital,reverse=True)
        graph[u].append(a);graph[v].append(b);forward.append((u,len(graph[u])-1,cap))
    for i in owner_order:add(source,lookup['owner:'+str(i)],1,0.,'owner_demand',owner=i)
    for i in owner_order:
        for r in edges:
            if r['owner_index']==i:add(lookup['owner:'+str(i)],lookup['hospital:'+str(hospitals.index(r['hospital']))],1,r['total_cost_seconds'],'owner_hospital',owner=i,hospital=r['hospital'])
    for k,h in enumerate(hospitals):add(lookup['hospital:'+str(k)],sink,caps[h],0.,'hospital_budget',hospital=h)
    augmentations=[];flow=0
    while flow<owner_count:
        distance=[math.inf]*len(nodes);parent=[None]*len(nodes);distance[source]=0.
        for _ in range(len(nodes)-1):
            changed=False
            for u in range(len(nodes)):
                if not math.isfinite(distance[u]):continue
                for ei,e in enumerate(graph[u]):
                    if e['capacity']>0 and distance[u]+e['cost']<distance[e['to']]-EPSILON:
                        distance[e['to']]=distance[u]+e['cost'];parent[e['to']]=(u,ei);changed=True
            if not changed:break
        if parent[sink] is None:break
        path=[];v=sink;visited=set()
        while v!=source:
            if v in visited or parent[v] is None:raise ArithmeticError('invalid shortest residual augmenting path')
            visited.add(v);u,ei=parent[v];e=graph[u][ei]
            path.append(dict(from_node=nodes[u],to_node=nodes[v],kind=e['kind'],owner_index=e['owner_index'],hospital=e['hospital'],
                reverse=e['reverse'],cost_seconds=e['cost'],residual_capacity_before=e['capacity']))
            e['capacity']-=1;graph[v][e['rev']]['capacity']+=1;v=u
        path.reverse();flow+=1
        augmentations.append(dict(augmentation_index=len(augmentations),flow_after=flow,unit_flow=1,
            shortest_distance_seconds=distance[sink],distances=[None if not math.isfinite(x) else x for x in distance],path=path,
            path_cost_seconds=sum((x['cost_seconds'] for x in path),0.)))
    assignment=[]
    for u,ei,capacity in forward:
        e=graph[u][ei]
        if e['kind']=='owner_hospital' and capacity-e['capacity']==1:
            assignment.append(dict(owner_index=e['owner_index'],hospital=e['hospital'],total_cost_seconds=e['cost']))
    assignment.sort(key=lambda r:r['owner_index'])
    # Super-source potentials expose per-edge floating comparison tolerances.
    # Edge allowances can accumulate around a cycle; this is not exact LP proof.
    potential=[0.]*len(nodes)
    for _ in range(len(nodes)-1):
        changed=False
        for u,adj in enumerate(graph):
            for e in adj:
                if e['capacity']>0 and potential[u]+e['cost']<potential[e['to']]-EPSILON:
                    potential[e['to']]=potential[u]+e['cost'];changed=True
        if not changed:break
    reduced=[]
    for u,adj in enumerate(graph):
        for e in adj:
            if e['capacity']>0:
                reduced.append(e['cost']+potential[u]-potential[e['to']])
    if any(x < -EPSILON for x in reduced):raise ArithmeticError('minimum-cost residual certificate failed')
    return dict(schema_version=1,algorithm='unit_demand_shortest_residual_augmenting_paths',epsilon=EPSILON,node_order=nodes,
        owner_order=list(owner_order),hospital_capacities=deepcopy(hospital_capacities),edges=deepcopy(edges),
        augmentations=augmentations,flow=flow,required_flow=owner_count,complete=flow==owner_count,assignment=assignment,
        total_edge_cost_seconds=sum((r['total_cost_seconds'] for r in assignment),0.),residual_potentials=potential,
        minimum_residual_reduced_cost_seconds=min(reduced,default=0.),residual_edges_above_negative_epsilon=True,
        maximum_simple_cycle_epsilon_allowance_seconds=len(nodes)*EPSILON,exact_minimum_cost_certificate=False,
        certificate_scope='declared shortest augmentations and per-residual-edge reduced cost >= -existing1e-9; a simple cycle can accumulate at most node_count times that comparison allowance; not an exact arithmetic minimum-cost certificate')


def allocate_slots(members,*,baseline_proposals,priority_context):
    eligible,reason=scope_members(members)
    if not eligible:raise ValueError('start-slot model outside scope: '+reason)
    if type(baseline_proposals) is not list or len(baseline_proposals)!=len(members):raise ValueError('complete public H baseline proposals required')
    n=len(members);ids=[m['patient_id'] for m in members]
    if len({_key(pid) for pid in ids})!=n:raise ValueError('unique typed first owners required')
    priorities={_key(p['patient_id']):p['effective_priority'] for p in priority_context['patients']}
    if set(priorities)!={_key(pid) for pid in ids} or len(priority_context['patients'])!=n or any(type(t) is not int or t not in TIERS for t in priorities.values()):
        raise ValueError('complete public priority roster required')
    config=JOINT.spec()['config'];workers=config['assumed_workers']
    if type(workers) is not int or workers<1 or type(config['max_candidates_evaluated']) is not int or not 1<=config['max_candidates_evaluated']<=6:
        raise ValueError('fixed integer public worker budget and bounded public H pool required')
    models=[JOINT._model(m['inputs'],p) for m,p in zip(members,baseline_proposals)]
    hospitals=[];edge_rows=[];owner_records=[];limits=[]
    order=sorted(range(n),key=lambda i:(-priorities[_key(ids[i])],i))
    for i,(m,p,model) in enumerate(zip(members,baseline_proposals,models)):
        send=p['send'];pool=[r['hospital'] for r in model['hospitals']]
        limit=min(len(send),m['inputs']['max_new'],config['max_concurrent']);limits.append(limit)
        if type(m['inputs']['max_new']) is not int or m['inputs']['max_new']<0 or len(set(send))!=len(send) or not set(send)<=set(pool) or len(send)>min(config['max_concurrent'],m['inputs']['max_new']):
            raise ValueError('original H immediate cardinality or public budget differs')
        for pi,h in enumerate(pool):
            if h not in hospitals:hospitals.append(h)
            if next(x for x in model['hospitals'] if x['hospital']==h)['residual_wait_seconds']!=0:
                raise ValueError('empty public initial queue must have zero residual wait')
            metrics=JOINT._metrics(model,[h],{})
            if limit>0:edge_rows.append(dict(owner_index=i,patient_id=ids[i],hospital=h,pool_index=pi,
                total_cost_seconds=metrics['total_cost_seconds'],metrics=metrics))
        owner_records.append(dict(owner_index=i,patient_id=ids[i],effective_priority=priorities[_key(ids[i])],original_H_immediate_send=deepcopy(send),
            original_H_immediate_cardinality=len(send),contact_limit=limit,candidate_pool=pool,
            baseline_uncontended_metrics=JOINT._metrics(model,send,{})))
    capacities=[dict(hospital=h,capacity=workers) for h in hospitals]
    if capacities:
        cover=cover_slots(edge_rows,owner_count=n,hospital_capacities=capacities,owner_order=order)
    else:
        cover=dict(schema_version=1,algorithm='no_public_candidate_slots',flow=0,required_flow=n,complete=False,assignment=[],augmentations=[])
    result=dict(schema_version=1,rule=RULE,source_sha256=deepcopy(SOURCE_SHA256),scope=SCOPE,tie_rule=TIE_RULE,
        public_assumed_workers=workers,slot_capacity_source='frozen_H_config.assumed_workers',owner_order=order,
        owner_records=owner_records,one_contact_edges=edge_rows,coverage=cover,coverage_complete=cover['complete'],
        extras=[],extra_passes=[],planned_sends=None,final_owner_metrics=None,original_probability_wait_guards_applicable=False,
        current_pair_only_execution=True,remaining_assignments_are_reservations=False)
    if not cover['complete']:return result
    sends=[[] for _ in members];used={h:0 for h in hospitals}
    for r in cover['assignment']:sends[r['owner_index']].append(r['hospital']);used[r['hospital']]+=1
    extras=[];extra_passes=[]
    while True:
        best=None;options=[]
        for i in order:
            if len(sends[i])>=limits[i]:continue
            before=JOINT._metrics(models[i],sends[i],{})
            for pi,h in enumerate(owner_records[i]['candidate_pool']):
                if h in sends[i] or used[h]>=workers:continue
                after=JOINT._metrics(models[i],sends[i]+[h],{});gain=before['total_cost_seconds']-after['total_cost_seconds']
                options.append(dict(owner_index=i,patient_id=ids[i],hospital=h,pool_index=pi,marginal_cost_decrease_seconds=gain,
                    before_metrics=before,after_metrics=after,capacity_before=workers-used[h],strictly_positive=gain>EPSILON))
                if gain>EPSILON and (best is None or gain>best['marginal_cost_decrease_seconds']+EPSILON):
                    best=dict(extra_index=len(extras),owner_index=i,patient_id=ids[i],hospital=h,pool_index=pi,
                        marginal_cost_decrease_seconds=gain,before_metrics=before,after_metrics=after,capacity_before=workers-used[h])
        extra_passes.append(dict(pass_index=len(extra_passes),candidate_evaluations=options,selected=None if best is None else deepcopy(best)))
        if best is None:break
        sends[best['owner_index']].append(best['hospital']);used[best['hospital']]+=1;extras.append(best)
    # Emit each subset in the original public H pool order, not flow traversal order.
    sends=[[h for h in owner_records[i]['candidate_pool'] if h in selected] for i,selected in enumerate(sends)]
    final=[dict(owner_index=i,patient_id=ids[i],effective_priority=priorities[_key(ids[i])],
        planned_send=deepcopy(sends[i]),metrics=JOINT._metrics(models[i],sends[i],{})) for i in range(n)]
    result.update(extras=extras,extra_passes=extra_passes,planned_sends=sends,final_owner_metrics=final,
        hospital_slot_usage=[dict(hospital=h,used=used[h],capacity=workers) for h in hospitals])
    return result
