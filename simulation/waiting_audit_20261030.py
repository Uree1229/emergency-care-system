"""Independent recorded-input/endpoints and research-gate audit for waiting W1.

No candidate, solver, simulator or experiment runner is imported. Current ledger
checks reuse the frozen JSON auditor; waiting objectives are calculated here.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import gzip
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import traceback

from . import online_allocation_audit as current
from . import h1_online_audit as h1_math
from .final_assignment_audit_20261028 import audit_case as audit_controls


class WaitingAuditError(ValueError):
    pass


def require(condition,message):
    if not condition:
        raise WaitingAuditError(message)


def integer(value,label,minimum=0):
    require(type(value) is int and value>=minimum,'invalid '+label)
    return value


def age_buckets(rows,ages):
    require(set(ages)=={p['id'] for p in rows},'waiting-age patient partition differs')
    for value in ages.values():
        integer(value,'waiting age')
    result = []
    for priority in (4,2,1):
        unique = sorted({ages[p['id']] for p in rows if p['priority']==priority},reverse=True)
        result.extend(dict(priority=priority,age=age) for age in unique[:-1])
    return result


def objective_oracle(rows,plan,travel,ages):
    """Independent priority weight -> exact older-age groups -> travel tuple."""
    require(set(plan)=={p['id'] for p in rows},'objective plan patient partition differs')
    buckets = age_buckets(rows,ages)
    return [sum(p['priority'] for p in rows if plan[p['id']] is None),
        *[sum(plan[p['id']] is None for p in rows
            if p['priority']==bucket['priority'] and ages[p['id']]==bucket['age']) for bucket in buckets],
        sum(p['priority']*travel[p['id'],plan[p['id']]] for p in rows if plan[p['id']] is not None)]


def feasible(rows,plan,free,options):
    if set(plan)!={p['id'] for p in rows}:
        return False
    used = Counter()
    for row in rows:
        hospital = plan[row['id']]
        if hospital is None:
            continue
        if hospital not in options[row['id']]:
            return False
        for resource,quantity in row['demand'].items():
            if quantity:
                used[hospital,resource] += quantity
    return all(quantity<=free.get(key,0) for key,quantity in used.items())


def exact_current_objective(rows,free,options,travel,ages,max_combinations=50000):
    """Small current-input oracle only; heuristic/global optimality not implied."""
    combinations = math.prod(len(options[p['id']])+1 for p in rows)
    if combinations>max_combinations:
        return dict(enumerated=False,combinations=combinations,objective=None)
    best = None
    for choice in product(*(list(options[p['id']])+[None] for p in rows)):
        plan = {p['id']:hospital for p,hospital in zip(rows,choice)}
        if feasible(rows,plan,free,options):
            objective = objective_oracle(rows,plan,travel,ages)
            if best is None or objective<best:
                best = objective
    return dict(enumerated=True,combinations=combinations,objective=best)


def endpoints(scenario,branch):
    """Keep failures and arrival exactly at the observation boundary."""
    specs = {p['id']:p for p in scenario['patients']}
    require(len(specs)==len(scenario['patients']),'duplicate endpoint patient')
    end = integer(branch['metrics']['observation_end_t'],'observation end')
    rows = {pid:dict(first_commit_t=None,final_arrive_t=None,treatment_start_t=None,inquiries=0) for pid in specs}
    previous = -1
    for event in branch['events']:
        at = integer(event['t'],'event time')
        require(previous<=at<=end,'unordered event or event outside observation')
        previous = at
        row = rows.get(event.get('patient'))
        if row is None:
            continue
        if event['type']=='ask':
            row['inquiries']+=1
        elif event['type']=='commit' and row['first_commit_t'] is None:
            row['first_commit_t']=at
        elif event['type']=='arrive':
            row['final_arrive_t']=at
        elif event['type']=='arrival_unable':
            row['final_arrive_t']=None
        elif event['type']=='treatment_start':
            row['treatment_start_t']=at
    result = []
    for pid in sorted(specs,key=lambda value:(type(value).__name__,str(value))):
        spec,row=specs[pid],rows[pid]
        occur,search=spec.get('occur_t',0),spec['search_start_t']
        selected,arrived=row['first_commit_t'] is not None,row['final_arrive_t'] is not None
        result.append(dict(id=pid,priority=spec.get('priority',4),occur_t=occur,search_start_t=search,
            observation_end_t=end,**row,selected=selected,arrived=arrived,
            selection_elapsed_minutes=row['first_commit_t']-search if selected else None,
            arrival_elapsed_minutes=row['final_arrive_t']-occur if arrived else None,
            selection_capped_minutes=(row['first_commit_t'] if selected else end)-search,
            arrival_capped_minutes=(row['final_arrive_t'] if arrived else end)-occur))
    return result


def distribution(values):
    values=sorted(values)
    return dict(n=len(values),mean=math.fsum(values)/len(values) if values else None,
        p95=values[math.ceil(.95*len(values))-1] if values else None,
        p99=values[math.ceil(.99*len(values))-1] if values else None,
        minimum=min(values) if values else None,maximum=max(values) if values else None)


def compare(before,after):
    left,right={p['id']:p for p in before},{p['id']:p for p in after}
    require(len(left)==len(before)==len(right)==len(after) and set(left)==set(right),'paired endpoint partition differs')
    differences=[]
    for pid in left:
        a,b=left[pid],right[pid]
        for key in ('priority','occur_t','search_start_t','observation_end_t'):
            require(type(a[key]) is type(b[key]) and a[key]==b[key],'paired endpoint context differs')
        states={name:dict(before=a[field] is not None,after=b[field] is not None)
            for name,field in (('selected','first_commit_t'),('arrived','final_arrive_t'),('treated','treatment_start_t'))}
        differences.append(dict(patient_id=pid,priority=a['priority'],
            delta_capped_minutes=b['arrival_capped_minutes']-a['arrival_capped_minutes'],
            delta_common_arrival_minutes=b['arrival_elapsed_minutes']-a['arrival_elapsed_minutes']
                if a['arrived'] and b['arrived'] else None,
            success_transitions=states,
            success_lost=[name for name,state in states.items() if state['before'] and not state['after']],
            success_gained=[name for name,state in states.items() if not state['before'] and state['after']]))
    def summary(rows):
        return dict(patients=len(rows),capped_difference=distribution([r['delta_capped_minutes'] for r in rows]),
            maximum_positive_capped_difference=max([0]+[r['delta_capped_minutes'] for r in rows]),
            common_arrival_difference=distribution([r['delta_common_arrival_minutes'] for r in rows if r['delta_common_arrival_minutes'] is not None]),
            success_lost={name:[r['patient_id'] for r in rows if name in r['success_lost']] for name in ('selected','arrived','treated')},
            success_gained={name:[r['patient_id'] for r in rows if name in r['success_gained']] for name in ('selected','arrived','treated')},
            time_direction=dict(Counter('earlier_capped' if r['delta_capped_minutes']<0 else 'later_capped'
                if r['delta_capped_minutes']>0 else 'equal_capped' for r in rows)))
    return dict(**summary(differences),priority_groups={str(priority):summary([r for r in differences if r['priority']==priority])
        for priority in (4,2,1)},patient_differences=differences)


def endpoint_metrics(rows):
    def metrics(subset):
        return dict(patients=len(subset),unassigned=sum(not p['selected'] for p in subset),
            not_arrived=sum(not p['arrived'] for p in subset),untreated=sum(p['treatment_start_t'] is None for p in subset),
            arrival_capped_minutes=distribution([p['arrival_capped_minutes'] for p in subset]),
            selection_capped_minutes=distribution([p['selection_capped_minutes'] for p in subset]),
            conditional_arrival_minutes=distribution([p['arrival_elapsed_minutes'] for p in subset if p['arrived']]))
    return dict(**metrics(rows),priority_groups={str(priority):metrics([p for p in rows if p['priority']==priority]) for priority in (4,2,1)})


GATE_CRITERIA = dict(
    id='waiting-research-protection-gate-20261030-v1',
    prespecified_before_candidate_results=True,
    clinical_safety_threshold=False,
    operational_adoption=False,
    success_references=['R0','R1','H1'],
    preserve_successes=['selected','arrived','treated'],
    maximum_positive_capped_difference_vs_H1=0,
    profile_priority_nonworsening_vs_H1=['mean','p95','p99'],
    maximum_positive_vs_R1_not_above_H1_vs_R1=['all','4','2','1'],
    minimum_strict_arrived_capped_improvements_vs_H1=1,
    technical_input_age_acceptance_capacity_budget_commit_violations=0,
    recommendation_scope='research_eligible_only; retain operational fixed_3 + CP-SAT severity_first 3s',
    performance_failure_does_not_stop_prespecified_fresh_cases=True,
    technical_or_audit_or_source_failure_stops_run=True,
)


def _h1_reference(rows,free,options,travel,record):
    reference = record['H1_reference_assignments']
    require(feasible(rows,reference,free,options),'H1 reference is infeasible on current input')
    pair=[sum(p['priority'] for p in rows if reference[p['id']] is None),
        sum(p['priority']*travel[p['id'],reference[p['id']]] for p in rows if reference[p['id']] is not None)]
    require(record['H1_reference_objective']==pair and record['H1_reference_primary']==pair[0],
            'H1 reference objective differs from independent arithmetic')
    original=record['H1_reference_record']
    require(original['assignments']==reference and original['objective']==pair,'embedded H1 reference differs')
    greedy=h1_math._greedy(rows,free,options,travel)
    baseline=current._objective(rows,greedy,travel)
    require(original['baseline_assignments']==greedy and original['baseline_objective']==baseline,
            'embedded H1 greedy baseline differs from independent reconstruction')
    evaluated=integer(original['evaluated_moves'],'H1 evaluated moves')
    accepted=integer(original['accepted_moves'],'H1 accepted moves')
    require(accepted<=evaluated<=2048,'H1 reference budget exceeded')
    require(evaluated==record['H1_reference_evaluated_moves'],'H1 move budget reference differs')
    reference_seconds=original['time_limit_seconds']
    require(record['time_limit_seconds']==10 and type(reference_seconds) in (int,float)
            and math.isfinite(reference_seconds) and 0<reference_seconds<=record['time_limit_seconds']
            and original['max_moves']==2048,
            'H1 reference must fit inside the total W1 ten-second technical guard')
    require(original['search_stop_reason'] in ('search_complete','move_limit','no_eligible_offers'),
            'H1 reference technical guard reached')
    require(original['optimal_proven'] is False,'H1 reference falsely claims optimality')
    previous,index=baseline,0
    trace=original['improvements']
    require(len(trace)==accepted,'embedded H1 trace count differs')
    for step in trace:
        position=integer(step['evaluation'],'H1 improvement evaluation',1)
        require(index<position<=evaluated and step['objective']<previous,'invalid embedded H1 improvement trace')
        previous,index=step['objective'],position
    require(previous==pair,'embedded H1 final objective differs from its trace')
    return reference,pair


def audit_waiting_branch(scenario,branch,*,exact_limit=50000):
    """Audit W1 as W1, never relabel its waiting objective as H1 mathematics."""
    require(branch['id']=='W1' and branch['policy']['rule']=='W1','wrong waiting policy identity')
    require(not branch.get('solver_records'),'waiting candidate cannot claim CP-SAT proof')
    ledger=current.audit_branch(scenario,branch,exact_limit=0)
    records=branch.get('waiting_records',[])
    require(isinstance(records,list),'missing waiting record list')
    used=set(); age_checks=0; oracle_checks=0; primary_checks=0; evaluated_total=0
    for index,decision in enumerate(branch.get('allocation_decisions',[])):
        require(decision['rule']=='W1','waiting decision rule differs')
        integer(decision['t'],'current decision time')
        rows,free,options,travel=current._problem(scenario,decision)
        for row in rows:
            integer(row['request_t'],'observed request time')
            integer(row['priority'],'observed priority',1)
            require(row['priority'] in (4,2,1),'observed priority is outside the fixed research weights')
        ages={p['id']:decision['t']-p['request_t'] for p in rows}
        for value in ages.values():
            integer(value,'observed past waiting age')
        age_checks+=len(ages)
        if decision['kind']=='single':
            require('waiting_record_index' not in decision,'single decision references waiting batch')
            row=rows[0]
            expected=min(options[row['id']],key=lambda hid:(travel[row['id'],hid],hid)) if options[row['id']] else None
            require(decision['assignments']=={row['id']:expected},'single waiting decision differs from nearest current offer')
            continue
        require(decision.get('optimal_proven') is False,'waiting heuristic falsely claims optimality')
        require(decision.get('waiting_ages')==ages,'decision age is not t - observed request_t')
        record_index=integer(decision.get('waiting_record_index'),'waiting record index')
        require(record_index<len(records) and record_index not in used,'missing or reused waiting record')
        used.add(record_index);record=records[record_index]
        require(record['decision_index']==index and record['t']==decision['t'],'waiting record decision/time differs')
        require(record['ages']==ages and record['age_buckets']==age_buckets(rows,ages)
            and record['observed_waiting_ages']==ages,'waiting record age provenance differs')
        plan=decision['assignments']
        objective=objective_oracle(rows,plan,travel,ages)
        require(record['assignments']==plan and record['objective']==objective,'waiting record plan/objective differs')
        require(record['optimal_proven'] is False,'waiting record falsely claims optimality')
        require(record['future_inputs_used'] is False and record['whole_episode_nonregression_guaranteed'] is False,
            'waiting record overclaims future information or episode protection')
        for key in ('wall_seconds','preprocessing_seconds','search_seconds'):
            value=record[key]
            require(type(value) in (int,float) and math.isfinite(value) and value>=0,'invalid waiting '+key)
        require(record['wall_seconds']<10 and record['wall_seconds']<=decision['elapsed_seconds']+1e-9,
            'waiting technical/enclosing wall guard mismatch')
        require(math.isclose(record['wall_seconds'],record['preprocessing_seconds']+record['search_seconds'],rel_tol=1e-9,abs_tol=1e-9),
            'waiting timing components do not sum to total wall time')
        reference,reference_pair=_h1_reference(rows,free,options,travel,record)
        reference_objective=objective_oracle(rows,reference,travel,ages)
        require(record['baseline_assignments']==reference and record['baseline_objective']==reference_objective
            and record['H1_reference_waiting_objective']==reference_objective,'waiting baseline differs from actual H1 reference')
        require(objective[0]<=reference_pair[0] and objective<=reference_objective,'W1 worsens its H1 reference objective')
        primary_checks+=1
        h1_evaluated=integer(record['H1_reference_evaluated_moves'],'H1 reference budget')
        waiting_evaluated=integer(record['waiting_evaluated_moves'],'waiting search budget')
        evaluated=integer(record['evaluated_moves'],'total evaluated moves')
        require(evaluated==h1_evaluated+waiting_evaluated<=2048 and record['max_moves']==2048,
            'combined H1/W1 search budget differs or exceeds 2048')
        evaluated_total+=evaluated
        require(record['search_stop_reason'] in ('search_complete','move_limit','no_eligible_offers'),
            'waiting search reached technical deadline')
        accepted=integer(record['accepted_moves'],'waiting accepted moves')
        trace=record['improvements']
        require(len(trace)==accepted<=waiting_evaluated,'waiting improvement count differs')
        previous,evaluation=reference_objective,h1_evaluated
        for step in trace:
            now=integer(step['evaluation'],'waiting improvement evaluation',1)
            local=integer(step['waiting_evaluation'],'local waiting improvement evaluation',1)
            require(evaluation<now<=evaluated and now==h1_evaluated+local and step['objective']<previous,
                'waiting improvement trace is not strictly decreasing/in budget')
            previous,evaluation=step['objective'],now
        require(previous==objective,'waiting final objective differs from improvement trace')
        exact=exact_current_objective(rows,free,options,travel,ages,max_combinations=exact_limit)
        complete=record['small_enumeration_complete']
        require(type(complete) is bool and complete==(record.get('search_mode')=='complete_small_enumeration'),
            'waiting complete-enumeration mode/flag mismatch')
        if exact['enumerated']:
            require(objective>=exact['objective'],'waiting objective below independent exact optimum')
            oracle_checks+=1
            if complete:
                require(objective==exact['objective'],'complete small enumeration missed independent optimum')
    require(used==set(range(len(records))),'orphan waiting records')
    return dict(passed=True,current_ledger_audit=ledger,waiting_batches=len(records),age_checks=age_checks,
        primary_preservation_checks=primary_checks,small_current_objective_oracle_checks=oracle_checks,
        evaluated_moves=evaluated_total,full_resource_history_replayed=False,
        candidate_or_solver_imported=False,whole_episode_optimality_verified=False)


def audit_replay(replay,*,exact_limit=50000):
    control=replay['control_replay']
    control_audit=audit_controls(control)
    scenario=replay['original_scenario']
    require(scenario==control['original_scenario'],'W1 and controls do not share the original scenario')
    branches={b['id']:b for b in replay['branches']}
    require(len(branches)==len(replay['branches'])==4 and set(branches)=={'R0','R1','H1','W1'},'missing/duplicate waiting comparison branches')
    for branch in control['branches']:
        require(branches[branch['id']]==branch,'top-level control branch differs from frozen control replay')
    waiting=audit_waiting_branch(scenario,branches['W1'],exact_limit=exact_limit)
    vectors={code:endpoints(scenario,branch) for code,branch in branches.items()}
    for code in ('R0','R1','H1'):
        require(vectors[code]==control_audit['patient_vectors'][code],'independent endpoint reconstruction differs from control auditor')
    return dict(passed=True,case_id=scenario['id'],profile=scenario.get('stratum',scenario.get('family')),
        seed=scenario['seed'],patient_vectors=vectors,metrics={code:endpoint_metrics(rows) for code,rows in vectors.items()},
        paired_W1_vs={code:compare(vectors[code],vectors['W1']) for code in ('R0','R1','H1')},
        control_audit=control_audit,waiting_audit=waiting,new_policy_executions=0,
        full_resource_history_replayed=False,operational_adoption=False)


def evaluate_gates(audits,criteria=None):
    """Strict observed-cohort protection gate; recommendation is research only."""
    criteria=GATE_CRITERIA if criteria is None else criteria
    require(criteria==GATE_CRITERIA,'waiting gate differs from the pre-results fixed research criteria')
    require(isinstance(audits,list) and audits,'nonempty completed case audits required')
    require(all(a.get('passed') is True for a in audits),'technical/audit failure cannot become a valid performance outcome')
    require(len({a['case_id'] for a in audits})==len(audits),'duplicate gate case')
    checks=[]
    def check(name,passed,**evidence):
        checks.append(dict(name=name,passed=bool(passed),**evidence))
    comparisons={};priority={};per_profile={};lost=[]
    for reference in ('R0','R1','H1'):
        all_before=[p for a in audits for p in a['patient_vectors'][reference]]
        all_after=[p for a in audits for p in a['patient_vectors']['W1']]
        differences=[]
        for a in audits:
            pair=compare(a['patient_vectors'][reference],a['patient_vectors']['W1'])
            for p in pair['patient_differences']:
                differences.append(dict(case_id=a['case_id'],profile=a['profile'],**p))
                if p['success_lost']:
                    lost.append(dict(reference=reference,case_id=a['case_id'],patient_id=p['patient_id'],
                        lost=p['success_lost'],delta_capped_minutes=p['delta_capped_minutes']))
        comparisons[reference]=dict(patients=len(differences),
            capped_difference=distribution([p['delta_capped_minutes'] for p in differences]),
            maximum_positive_capped_difference=max([0]+[p['delta_capped_minutes'] for p in differences]),
            patient_differences=differences)
        priority[reference]={str(k):dict(n=sum(p['priority']==k for p in differences),
            capped_difference=distribution([p['delta_capped_minutes'] for p in differences if p['priority']==k]),
            maximum_positive_capped_difference=max([0]+[p['delta_capped_minutes'] for p in differences if p['priority']==k])) for k in (4,2,1)}
    check('all_reference_success_ids_preserved',not lost,losses=lost)
    maximum=comparisons['H1']['maximum_positive_capped_difference']
    check('no_individual_capped_delay_shift_vs_H1',maximum==0,maximum_positive_minutes=maximum)
    for profile in sorted({a['profile'] for a in audits}):
        cases=[a for a in audits if a['profile']==profile]
        per_profile[profile]={}
        for k in (4,2,1):
            before=[p['arrival_capped_minutes'] for a in cases for p in a['patient_vectors']['H1'] if p['priority']==k]
            after=[p['arrival_capped_minutes'] for a in cases for p in a['patient_vectors']['W1'] if p['priority']==k]
            b,c=distribution(before),distribution(after)
            require(b['n']==c['n'],'profile/priority gate denominator differs')
            per_profile[profile][str(k)]=dict(H1=b,W1=c,cases=len(cases))
            for metric in ('mean','p95','p99'):
                check('profile_priority_'+metric+'_nonworsening_vs_H1',
                    c[metric] is None and b[metric] is None or c[metric] is not None and b[metric] is not None and c[metric]<=b[metric],
                    profile=profile,priority=k,metric=metric,H1=b[metric],W1=c[metric],denominator=b['n'])
    h1_r1=[]
    for a in audits:
        h1_r1.extend(compare(a['patient_vectors']['R1'],a['patient_vectors']['H1'])['patient_differences'])
    reference_maxima={}
    for key in ('all','4','2','1'):
        subset=h1_r1 if key=='all' else [p for p in h1_r1 if p['priority']==int(key)]
        reference=max([0]+[p['delta_capped_minutes'] for p in subset])
        candidate=comparisons['R1']['maximum_positive_capped_difference'] if key=='all' else priority['R1'][key]['maximum_positive_capped_difference']
        reference_maxima[key]=dict(H1_vs_R1=reference,W1_vs_R1=candidate)
        check('maximum_positive_vs_R1_not_worse_than_H1',candidate<=reference,priority=key,**reference_maxima[key])
    strict_improvements=[dict(case_id=p['case_id'],patient_id=p['patient_id']) for p in comparisons['H1']['patient_differences']
        if p['delta_capped_minutes']<0 and p['success_transitions']['arrived']['after']]
    check('at_least_one_strict_actual_arrival_capped_improvement_vs_H1',len(strict_improvements)>=1,
        count=len(strict_improvements),ids=strict_improvements)
    return dict(passed=all(c['passed'] for c in checks),research_recommendation_eligible=all(c['passed'] for c in checks),
        criteria=criteria,checks=checks,comparisons=comparisons,by_priority=priority,
        by_profile_priority=per_profile,reference_regression_maxima=reference_maxima,
        cases=len(audits),patients_per_policy=sum(len(a['patient_vectors']['W1']) for a in audits),
        success_losses=lost,technical_failure_count=0,operational_adoption=False,
        operational_policy_changed=False,descriptive_nested_environments=True,
        inferential_superiority_claim=False)


def _read(path):
    def pairs(values):
        result={}
        for key,value in values:
            require(key not in result,'duplicate JSON key')
            result[key]=value
        return result
    def reject(value):
        raise WaitingAuditError('nonfinite JSON value: '+value)
    return json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=pairs,parse_constant=reject)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _contained(root,name):
    require(isinstance(name,str) and not Path(name).is_absolute() and '\\' not in name
        and ':' not in name and '..' not in Path(name).parts,'invalid relative artifact path')
    result=(Path(root)/name).resolve()
    require(result.is_relative_to(Path(root).resolve()),'artifact path escapes its root')
    return result


def validate_protocol(protocol):
    gates=protocol['adoption_gates']
    require(protocol['candidate_count']==1 and protocol['candidate']=='W1','single candidate protocol differs')
    require(gates['success_preservation']['comparators']==['R0','R1','H1']
        and gates['success_preservation']['endpoints']==['selected','arrived','treatment_started']
        and gates['success_preservation']['maximum_lost_ids']==0,'success ID protection differs')
    require(gates['no_harm_transfer']['comparator']=='H1'
        and gates['no_harm_transfer']['maximum_positive_individual_arrival_capped_difference_minutes']==0,'individual H1 protection differs')
    require(gates['priority_distribution']['comparator']=='H1'
        and gates['priority_distribution']['arrival_capped_mean_p95_p99_maximum_increase_minutes']==0,'priority distribution protection differs')
    regret=gates['regret_protection']
    require(regret['baseline']=='R1' and regret['reference']=='H1'
        and regret['maximum_positive_W1_minus_R1_no_greater_than_H1_minus_R1'] is True,'R1 regret gate differs')
    effect=gates['strict_effect']
    require(effect['comparator']=='H1' and effect['minimum_strictly_earlier_arrival_capped_patients']==1
        and effect['candidate_must_actually_arrive'] is True,'strict arrival effect differs')
    require(gates['arbitrary_delay_tolerance_minutes'] is None and gates['post_result_gate_changes_allowed'] is False
        and protocol['no_post_validation_tuning'] is True,'post-result tolerance/tuning cannot be introduced')
    require(protocol['validation_environments']==60 and protocol['validation_replicates']==5,'new cohort schedule differs')
    return True


def audit_run(run_directory,output):
    """Read all recorded cases once and write a NEW independent audit receipt."""
    run_directory=Path(run_directory).resolve();output=Path(output).resolve()
    require(not output.exists(),'independent audit output must be new')
    output.mkdir(parents=True)
    source_root=Path(__file__).resolve().parents[1]
    source_before={name:_digest(source_root/name) for name in
        ('simulation/waiting_audit_20261030.py','test_waiting_audit_20261030.py')}
    try:
        report=_read(run_directory/'report.json');lock=_read(run_directory/'execution-lock.json')
        validate_protocol(lock['protocol'])
        report_sha=_digest(run_directory/'report.json');lock_sha=_digest(run_directory/'execution-lock.json')
        require(report['passed'] is True and report['source_before_after_equal'] is True,'recorded run has technical/source failure')
        require(report['lock_sha256']==lock_sha
            and report['protocol_sha256']==hashlib.sha256(_canonical(lock['protocol'])).hexdigest(),'report/lock/protocol binding differs')
        for name,checksum in lock['sources'].items():
            require(_digest(_contained(run_directory/'sources',name))==checksum,'archived source checksum differs: '+name)
        for name,checksum in source_before.items():
            require(lock['sources'].get(name)==checksum,'independent auditor differs from pre-run frozen source: '+name)
        require(_digest(run_directory/'seed-inventory.json')==lock['seed_inventory_sha256'],'seed inventory differs')
        require(len(report['cases'])==len(report['raw_index'])==len(lock['entries'])==96,'full 36+60 case schedule required')
        phases=defaultdict(list);raw_hashes=[];known_cards=[]
        chosen={3:['P02','P05'],16:['P05','P17'],21:['P17','P04'],31:['P12','P05'],27:['P19']}
        for index,(case,item,entry) in enumerate(zip(report['cases'],report['raw_index'],lock['entries'])):
            raw_path=_contained(run_directory,item['path'])
            packed=raw_path.read_bytes();body=gzip.decompress(packed)
            require(hashlib.sha256(packed).hexdigest()==item['sha256']
                and hashlib.sha256(body).hexdigest()==item['raw_sha256'],'raw checksum differs')
            raw=json.loads(body)
            require(hashlib.sha256(_canonical(raw['original_scenario'])).hexdigest()==entry['scenario_sha256']
                and case['scenario_sha256']==entry['scenario_sha256'],'raw input differs from pre-run lock')
            require(raw['execution_order']==entry['execution_order'],'locked execution order differs')
            require(case['phase']==entry['phase'] and case['profile']==entry['profile'] and case['seed']==entry['seed'],
                'recorded phase/profile/seed differs from schedule')
            audit=audit_replay(raw)
            for code,rows in audit['patient_vectors'].items():
                stored={p['id']:p for p in case['audit']['patient_vectors'][code]}
                for row in rows:
                    expected=dict(row,treatment_capped_minutes=(row['treatment_start_t'] if row['treatment_start_t'] is not None
                        else row['observation_end_t'])-row['occur_t'])
                    require(stored.get(row['id'])==expected,'independent endpoint differs from runner report')
            phases[entry['phase']].append(audit)
            raw_hashes.append(dict(path=item['path'],sha256=item['sha256'],raw_sha256=item['raw_sha256']))
            if entry['phase']=='development_known' and index in chosen:
                for pid in chosen[index]:
                    states={}
                    for code in ('R0','R1','H1','W1'):
                        p=next(p for p in audit['patient_vectors'][code] if p['id']==pid)
                        states[code]={key:p[key] for key in ('priority','first_commit_t','final_arrive_t',
                            'treatment_start_t','arrival_capped_minutes','selected','arrived')}
                        states[code]['treated']=p['treatment_start_t'] is not None
                    known_cards.append(dict(raw_path=item['path'],profile=entry['profile'],patient_id=pid,policies=states))
        require({phase:len(cases) for phase,cases in phases.items()}=={'development_known':36,'validation_new':60},'phase schedule differs')
        gates={phase:evaluate_gates(cases) for phase,cases in phases.items()}
        source_after={name:_digest(source_root/name) for name in source_before}
        require(source_after==source_before and _digest(run_directory/'report.json')==report_sha
            and _digest(run_directory/'execution-lock.json')==lock_sha,'audit input/source changed during review')
        result=dict(passed=True,technical_audit_passed=True,new_policy_executions=0,
            report_sha256=report_sha,lock_sha256=lock_sha,independent_source_hashes=source_before,
            raw_hashes=raw_hashes,cases=96,phase_gates=gates,known_case_cards=known_cards,
            research_recommendation_eligible=gates['validation_new']['research_recommendation_eligible'],
            operational_adoption=False,operational_policy_changed=False,
            limitations=['Recorded current resources and event endpoints; not full independent resource-history reconstruction.',
                '96 synthetic environments; development 36 already observed, new validation 60, five per profile.',
                'Individual patients are nested in environments; no independent-patient inference or clinical safety claim.',
                'Finite-cohort predeclared research protection; all observed results become seen data for future candidates.'])
        (output/'auditor-source.py').write_bytes(Path(__file__).read_bytes())
        (output/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        lines=['# W1 독립 원자료 감사와 사전 연구 게이트','',
            '원래 정책을 다시 실행하지 않고96환경·384분기의 현재입력·대기나이·수용/자원/실제commit·최초확정/도착/치료시작을 검사했다.',
            '기술 감사 통과와 연구추천 게이트 통과는 별개다. 운영 기준은 유지한다.','',
            '| 단계 | 환경 | 연구추천 조건 | 실패한 게이트 수 |','|---|---:|---|---:|']
        for phase,gate in gates.items():
            lines.append(f"| {phase} | {gate['cases']} | {'PASS' if gate['passed'] else 'FAIL'} | {sum(not c['passed'] for c in gate['checks'])} |")
        lines+=['','| 원자료/환자 | 정책 | 선정 t | 도착 t | 치료시작 t | 도착 제한값 | 선정/도착/치료 성공 |',
            '|---|---|---:|---:|---:|---:|---|']
        for card in known_cards:
            for code,p in card['policies'].items():
                fmt=lambda value:'미관측' if value is None else str(value)
                lines.append(f"| {card['raw_path']}/{card['patient_id']} | {code} | {fmt(p['first_commit_t'])} | {fmt(p['final_arrive_t'])} | {fmt(p['treatment_start_t'])} | {p['arrival_capped_minutes']} | {p['selected']}/{p['arrived']}/{p['treated']} |")
        lines+=['','도착 제한값 차이가0이어도 성공 상실은 별도 실패다. 전체·우선군·profile별 분모/평균/p95/p99/최대·성공 ID는 receipt.json에 보존한다.',
            '소규모 합성 환경의 엄격한 관찰 보호 게이트이며 임상 안전성·일반적 우월성·전체 온라인 최적성을 뜻하지 않는다.','']
        (output/'receipt.md').write_text('\n'.join(lines),encoding='utf-8')
        return result
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(passed=False,new_policy_executions=0,
            error=traceback.format_exc(),source_before=source_before),ensure_ascii=False,indent=2),encoding='utf-8')
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    result=audit_run(args.run,args.output)
    print(json.dumps(dict(technical_audit_passed=result['passed'],cases=result['cases'],
        research_recommendation_eligible=result['research_recommendation_eligible'],new_policy_executions=0)))


if __name__=='__main__':
    main()
