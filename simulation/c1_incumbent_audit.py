"""Independently audit saved bounded C1 incumbent traces and whole raw replays.

No initializer, assignment rule, replay engine, scenario builder or CP-SAT
backend is imported or executed here.  The frozen complete recovery-rank
auditor receives the original branch before this additional audit reconstructs
the current accepted-offer initializer and its shared deadline accounting.
"""
from collections import Counter
from copy import deepcopy
import math

from . import recovery_rank_audit as parent


EXPERIMENT_ID = 'c1_incumbent_20261020'
VARIANTS = ('single_augment',)
C1IncumbentAuditError = parent.RecoveryRankAuditError
_require = parent._require
base, joint = parent.base, parent.joint
RULE = 'first_strict_single_blocker_augment'
MAX_CANDIDATES = 128
INITIALIZER_LIMIT_SECONDS = .005
BUDGET_SCOPE = 'shared_initializer_model_and_two_solver_stages'
NAMESPACE_FIELDS = {'allocation_intervention_id','c1_incumbent'}
CONTRACT_FIELDS = {'schema_version','allocation','seeded','variant','actual_assignment_class',
    'base_assignment_class','budget_seconds','initializer_max_candidates','initializer_limit_seconds',
    'budget_scope','actual_replay_class','original_normal_rank_workflow_descriptors_retained',
    'inquiry_readiness_and_physical_gates_unchanged','actual_assignment_paths_may_change'}
TRACE_FIELDS = {'schema_version','variant','rule','max_candidates','initializer_limit_seconds',
    'budget_scope','original_greedy_assignments','original_greedy_objective',
    'selected_assignments','selected_objective','applied','evaluated_candidates','candidates',
    'stop_reason','elapsed_seconds','started_at','deadline_started_at','global_deadline_at',
    'initializer_deadline_at','finished_at','optimality_claimed'}
CANDIDATE_FIELDS = {'candidate_index','patient','hospital','blocker','alternate','assignments',
    'feasible','objective','accepted','started_at','finished_at'}
BUDGET_FIELDS = {'scope','limit_seconds','started_at','deadline_at','finished_at',
    'model_started_at','model_finished_at','stage_requests'}
SELECTION_FIELDS = {'initializer_assignments','initializer_objective','stage_candidates',
    'selected_source','selected_assignments','selected_objective'}


def _number(value, label, *, nonnegative=True):
    _require(type(value) in (int, float) and math.isfinite(value)
             and (not nonnegative or value >= 0), 'invalid ' + label)
    return value


def _same_time(left, right):
    return abs(left - right) <= 1e-8


def _exact_objective(saved,expected):
    return type(saved) is list and len(saved)==2 and all(type(value) is int and value>=0
        for value in saved) and saved==expected


def _ordered(patients):
    return sorted(patients, key=lambda row: (-row['priority'], row['request_t'], row['id']))


def _greedy(patients, free, options, travel):
    """Original priority/request/ID nearest baseline, rebuilt locally."""
    result, used = {}, Counter()
    for patient in _ordered(patients):
        pid = patient['id']
        feasible = [hid for hid in sorted(options[pid]) if all(
            free.get((hid, resource), 0) - used[hid, resource] >= amount
            for resource, amount in patient['demand'].items() if amount)]
        target = min(feasible, key=lambda hid: (travel[pid, hid], hid)) if feasible else None
        result[pid] = target
        if target is not None:
            for resource, amount in patient['demand'].items():
                used[target, resource] += amount
    return result


def _candidate_plans(patients, options, baseline):
    """The declared deterministic single-blocker enumeration, no search oracle."""
    rows = _ordered(patients)
    for unassigned in rows:
        pid = unassigned['id']
        if baseline[pid] is not None:
            continue
        for target in sorted(options[pid]):
            for blocker in rows:
                blocked = blocker['id']
                if baseline[blocked] != target:
                    continue
                for alternate in sorted(options[blocked]):
                    if alternate == target:
                        continue
                    candidate = dict(baseline)
                    candidate[pid], candidate[blocked] = target, alternate
                    yield pid, target, blocked, alternate, candidate


def _objective(patients, plan, travel):
    return base._objective(patients, plan, travel)


def _feasible(patients, free, options, plan):
    return base._plan_feasible(patients, free, options, plan)


def _audit_initializer(patients, free, options, travel, trace, budget):
    """Reproduce the exact evaluated prefix from current raw accepted offers."""
    _require(type(trace) is dict and set(trace)==TRACE_FIELDS,'unknown or missing initializer trace field')
    _require(type(trace['schema_version']) is int and trace['schema_version']==1
        and trace['variant']=='single_augment' and trace['rule']==RULE,
        'unknown initializer identity')
    _require(type(trace['max_candidates']) is int and trace['max_candidates']==MAX_CANDIDATES
        and trace['initializer_limit_seconds']==INITIALIZER_LIMIT_SECONDS
        and trace['budget_scope']==BUDGET_SCOPE,'initializer limits or shared budget scope changed')
    _require(trace['optimality_claimed'] is False,'initializer claimed optimality')
    baseline=_greedy(patients,free,options,travel)
    initial_key=_objective(patients,baseline,travel)
    _require(trace['original_greedy_assignments']==baseline
        and _exact_objective(trace['original_greedy_objective'],initial_key),'original greedy baseline changed')
    _require(_feasible(patients,free,options,baseline),'rebuilt original baseline infeasible')
    _require(type(trace['applied']) is bool and type(trace['evaluated_candidates']) is int
        and type(trace['candidates']) is list
        and trace['evaluated_candidates']==len(trace['candidates'])<=MAX_CANDIDATES,
        'initializer candidate count or applied flag differs')
    elapsed=_number(trace['elapsed_seconds'],'initializer elapsed time')
    start=trace['started_at']
    if start is None:
        _require(not trace['candidates'] and trace['applied'] is False and elapsed==0
            and all(trace[key] is None for key in ('deadline_started_at','global_deadline_at',
                'initializer_deadline_at','finished_at')),'unstarted initializer fabricated work or clocks')
        _require(trace['stop_reason'] in ('not_started_zero_budget','not_started_empty_model',
            'not_started_missing_backend'),'unknown unstarted initializer reason')
        _require(trace['selected_assignments']==baseline and _exact_objective(trace['selected_objective'],initial_key),
            'unstarted initializer changed original baseline')
        return dict(candidates=0,improvements=0,elapsed_seconds=0.)
    start=_number(start,'initializer start')
    finish=_number(trace['finished_at'],'initializer finish')
    global_start=_number(trace['deadline_started_at'],'initializer global start')
    global_deadline=_number(trace['global_deadline_at'],'initializer global deadline')
    cap=_number(trace['initializer_deadline_at'],'initializer deadline')
    _require(global_start==budget['started_at'] and global_deadline==budget['deadline_at']
        and global_start<=start<=finish and _same_time(elapsed,finish-start),
        'initializer clocks differ from the single shared budget')
    _require(_same_time(cap,min(global_deadline,start+INITIALIZER_LIMIT_SECONDS)),
        'initializer cap extends its local or global deadline')
    proposals=iter(_candidate_plans(patients,options,baseline))
    selected=dict(baseline); accepted_count=0; last_finish=start
    for index,item in enumerate(trace['candidates'],1):
        _require(type(item) is dict and set(item)==CANDIDATE_FIELDS,'initializer candidate fields differ')
        expected=next(proposals,None)
        _require(expected is not None,'initializer evaluated a nonexistent proposal')
        pid,hid,blocker,alternate,plan=expected
        _require(type(item['candidate_index']) is int and item['candidate_index']==index
            and (item['patient'],item['hospital'],item['blocker'],item['alternate'])==
                (pid,hid,blocker,alternate) and item['assignments']==plan,
            'initializer skipped, reordered or altered a single-blocker proposal')
        _require(alternate is not None and all(plan[patient] is not None
            for patient,target in baseline.items() if target is not None),
            'initializer evicted an originally assigned patient')
        feasible=_feasible(patients,free,options,plan)
        objective=_objective(patients,plan,travel) if feasible else None
        accepted=feasible and objective<initial_key
        _require(type(item['feasible']) is bool and item['feasible']==feasible
            and (_exact_objective(item['objective'],objective) if feasible else item['objective'] is None)
            and type(item['accepted']) is bool
            and item['accepted']==accepted,'initializer feasibility, objective or acceptance differs')
        checked=_number(item['started_at'],'candidate start')
        finished=_number(item['finished_at'],'candidate finish')
        _require(last_finish<=checked<=finished<=finish and checked<cap and checked<global_deadline,
            'initializer evaluated a candidate outside its remaining shared budget')
        last_finish=finished
        _require(not accepted_count,'initializer continued after its first strict improvement')
        if accepted:selected=dict(plan);accepted_count+=1
    reason=trace['stop_reason']
    if accepted_count:
        _require(reason=='improvement' and trace['candidates'][-1]['accepted'],
            'initializer first improvement has no immediate stopping witness')
    elif reason=='exhausted':
        _require(len(trace['candidates'])<MAX_CANDIDATES and next(proposals,None) is None,
            'initializer claimed exhausted proposals before checking the full fixed sequence')
    elif reason=='candidate_limit':
        _require(len(trace['candidates'])==MAX_CANDIDATES,'initializer candidate-limit stop differs')
    elif reason=='global_time_limit':
        _require(finish>=global_deadline,'initializer fabricated global budget exhaustion')
    elif reason=='initializer_time_limit':
        _require(finish>=cap,'initializer fabricated local budget exhaustion')
    else:
        _require(False,'unknown started initializer stop reason')
    _require(trace['applied']==bool(accepted_count) and trace['selected_assignments']==selected
        and _exact_objective(trace['selected_objective'],_objective(patients,selected,travel))
        and _feasible(patients,free,options,selected),'initializer final incumbent differs')
    return dict(candidates=len(trace['candidates']),improvements=accepted_count,elapsed_seconds=elapsed)


def _audit_budget(record, trace, expected_limit):
    budget=record['c1_incumbent_budget']
    _require(type(budget) is dict and set(budget)==BUDGET_FIELDS,'unknown or missing shared-budget fields')
    _require(budget['scope']==BUDGET_SCOPE and budget['limit_seconds']==expected_limit
        and record['time_limit_seconds']==expected_limit,'C1 shared search budget changed')
    _number(expected_limit,'shared time limit')
    _number(budget['limit_seconds'],'recorded shared limit')
    _number(record['time_limit_seconds'],'recorded solver time limit')
    finish=_number(budget['finished_at'],'shared-budget finish')
    requests=budget['stage_requests']; stages=record['stages']
    _require(type(requests) is list and len(requests)==len(stages)<=2,
        'solver stage lacks exactly one budget request')
    if budget['started_at'] is None:
        _require(budget['deadline_at'] is None and budget['model_started_at'] is None
            and budget['model_finished_at'] is None and not requests and trace['started_at'] is None,
            'unstarted budget contains model, initializer or solver work')
        return dict(started=False,overrun_seconds=0.)
    start=_number(budget['started_at'],'shared-budget start')
    deadline=_number(budget['deadline_at'],'shared-budget deadline')
    _require(_same_time(deadline,start+expected_limit) and start<=finish
        and trace['started_at'] is not None,'shared deadline renewed or initializer omitted')
    init_finish=_number(trace['finished_at'],'initializer finished')
    _require(start<=trace['started_at']<=init_finish<=finish,'initializer is outside shared pipeline time')
    previous=init_finish
    model_start,model_finish=budget['model_started_at'],budget['model_finished_at']
    if model_start is None:
        _require(model_finish is None and not requests and finish>=deadline
            and record['fallback_reason']=='budget_exhausted_before_model',
            'model omitted without actual shared-budget exhaustion')
    else:
        model_start=_number(model_start,'model construction start')
        model_finish=_number(model_finish,'model construction finish')
        _require(init_finish<=model_start<deadline and model_start<=model_finish<=finish,
            'model construction is outside the original remaining budget')
        previous=model_finish
        if not requests:
            _require(finish>=deadline and record['fallback_reason']=='budget_exhausted_before_stage_one',
                'stage one omitted without actual shared-budget exhaustion')
    for index,request in enumerate(requests):
        _require(type(request) is dict and set(request)=={
            'index','requested_at','remaining_seconds','finished_at'}
            and type(request['index']) is int and request['index']==index,
            'solver budget request fields or stage order differ')
        requested=_number(request['requested_at'],'solver requested time')
        remaining=_number(request['remaining_seconds'],'solver remaining budget')
        completed=_number(request['finished_at'],'solver request finish')
        _require(previous<=requested<deadline and requested<=completed<=finish
            and remaining>0 and _same_time(remaining,deadline-requested),
            'solver received an extra, renewed or already exhausted search budget')
        stage_seconds=_number(stages[index]['wall_seconds'],'solver stage wall time')
        _require(stage_seconds<=completed-requested+1e-6,'solver stage wall time exceeds actual request duration')
        previous=completed
    pipeline=_number(record['solver_pipeline_wall_seconds'],'recorded original pipeline seconds')
    _require(pipeline+1e-8>=finish-start and pipeline+1e-8>=trace['elapsed_seconds'],
        'initializer or model/search time omitted from original pipeline wall time')
    return dict(started=True,overrun_seconds=max(0.,finish-deadline))


def _audit_selection(patients,free,options,travel,record,trace):
    selection=record['c1_incumbent_selection']
    _require(type(selection) is dict and set(selection)==SELECTION_FIELDS,
        'unknown or missing incumbent selection fields')
    selected=deepcopy(trace['selected_assignments']); objective=_objective(patients,selected,travel)
    source='initializer' if trace['applied'] else 'original_greedy'
    _require(selection['initializer_assignments']==selected
        and _exact_objective(selection['initializer_objective'],objective),
        'solver pipeline did not retain the actual initializer')
    candidates=selection['stage_candidates'];stages=record['stages']
    _require(type(stages) is list and len(stages)<=2,'invalid incumbent solver stage list')
    for stage in stages:
        _require(stage['status'] in ('OPTIMAL','FEASIBLE','UNKNOWN','INFEASIBLE','MODEL_INVALID'),
            'unknown incumbent solver stage status')
        _number(stage['best_bound'],'solver best bound',nonnegative=False)
        if stage['status'] in ('OPTIMAL','FEASIBLE'):
            _number(stage['objective'],'solver stage objective',nonnegative=False)
        else:_require(stage['objective'] is None,'nonfeasible stage invented an objective')
    expected_indices=[index for index,stage in enumerate(stages) if stage['status'] in ('OPTIMAL','FEASIBLE')]
    _require(type(candidates) is list and [item.get('stage_index') for item in candidates]==expected_indices,
        'missing, reused or invented feasible solver incumbent')
    total_priority=sum(row['priority'] for row in patients)
    for item in candidates:
        _require(type(item) is dict and set(item)=={'stage_index','assignments','objective','feasible','selected'},
            'solver incumbent selection fields differ')
        _require(type(item['stage_index']) is int,'invalid solver stage candidate identity')
        index=item['stage_index']; plan=item['assignments']
        feasible=_feasible(patients,free,options,plan)
        key=_objective(patients,plan,travel) if feasible else None
        take=feasible and key<=objective
        _require(type(item['feasible']) is bool and item['feasible']==feasible
            and (_exact_objective(item['objective'],key) if feasible else item['objective'] is None)
            and type(item['selected']) is bool and item['selected']==take,
            'solver incumbent feasibility, value or replacement differs')
        if feasible:
            expected_value=total_priority-key[0] if index==0 else key[1]
            _require(base._same(stages[index]['objective'],expected_value),
                'solver stage value differs from its actual incumbent plan')
        if take:
            selected,objective=dict(plan),key
            source='solver_stage_one' if index==0 else 'solver_stage_two'
    _require(selection['selected_source']==source and selection['selected_assignments']==selected
        and _exact_objective(selection['selected_objective'],objective),'retained incumbent source or plan differs')
    _require(_feasible(patients,free,options,selected)
        and objective<=trace['original_greedy_objective'] and objective<=trace['selected_objective'],
        'pipeline returned a worse or infeasible plan than its available safe incumbent')
    _require(record['stability']['solver_assignments']==selected and record['objective']==objective,
        'stable postselection did not receive the recorded retained solver/initializer plan')
    primary=bool(stages and stages[0]['status']=='OPTIMAL')
    secondary=bool(len(stages)==2 and stages[1]['status']=='OPTIMAL')
    for name,expected in (('primary_optimal_proven',primary),('secondary_optimal_proven',secondary),
            ('optimal_proven',primary and secondary)):
        if record['status']=='OPTIMAL_EMPTY':expected=True
        _require(type(record[name]) is bool and record[name]==expected,'false initializer/solver proof flag')
    if stages and stages[0]['status'] in ('OPTIMAL','FEASIBLE'):
        _require(stages[0]['best_bound']+1e-8>=total_priority-objective[0],
            'primary bound is below the retained feasible assigned weight')
    if primary:
        _require(base._same(stages[0]['objective'],total_priority-objective[0]),
            'partial primary optimum differs from retained assignment')
    if len(stages)==2:
        _require(primary and stages[1]['best_bound']<=objective[1]+1e-8,
            'secondary search lacks primary optimum or valid retained-plan bound')
    if secondary:
        _require(base._same(stages[1]['objective'],objective[1]) and not record['fallback'],
            'secondary optimum differs from retained assignment or marked fallback')
    _require(type(record['fallback']) is bool and bool(record['fallback_reason'])==record['fallback'],
        'fallback reason is missing or contradicts fallback flag')
    _require(record['status'] in ('OPTIMAL','OPTIMAL_EMPTY','FEASIBLE','FEASIBLE_PRIMARY_OPTIMAL',
        'FALLBACK','MISSING_DEPENDENCY','UNKNOWN','INFEASIBLE','MODEL_INVALID','INVALID_INCUMBENT'),
        'unknown incumbent final solver status')
    _require(record['fallback_reason'] in (None,'time_limit_zero','missing_backend',
        'budget_exhausted_before_model','budget_exhausted_before_stage_one',
        'no_feasible_solver_incumbent','invalid_solver_incumbent','incumbent_better_than_solver_incumbent'),
        'unknown incumbent fallback reason')
    if record['fallback_reason']=='no_feasible_solver_incumbent':
        _require(len(stages)==1 and stages[0]['status'] not in ('OPTIMAL','FEASIBLE')
            and record['status']==stages[0]['status'],'solver fallback lacks unavailable first-stage witness')
    if record['fallback_reason']=='incumbent_better_than_solver_incumbent':
        _require(len(stages)==1 and stages[0]['status']=='FEASIBLE' and len(candidates)==1
            and candidates[0]['feasible'] and not candidates[0]['selected'] and record['status']=='FEASIBLE',
            'inferior solver fallback lacks an actual rejected stage-one incumbent')
    if record['status']=='FEASIBLE_PRIMARY_OPTIMAL':
        _require(primary and not secondary,'partial primary final status has no exact primary proof')
    return dict(initializer_selected=int(source=='initializer'),
        solver_candidates_rejected=sum(not item['selected'] for item in candidates))


def _audit_contract(scenario,branch):
    namespace={key for key in branch if key=='allocation_intervention_id' or key.startswith('c1_incumbent')}
    if not namespace:
        _require(not str(branch.get('id','')).startswith('c1_incumbent_'),
            'incumbent candidate concealed its actual allocation intervention')
        return None
    _require(namespace==NAMESPACE_FIELDS and branch['allocation_intervention_id']==EXPERIMENT_ID,
        'unknown, incomplete or false C1 incumbent namespace')
    contract=branch['c1_incumbent']
    _require(type(contract) is dict and set(contract)==CONTRACT_FIELDS,'C1 incumbent contract fields differ')
    code=branch['policy']['rule'];seeded=contract['seeded']
    _require(type(contract['schema_version']) is int and contract['schema_version']==1
        and code in ('H1','C1') and contract['allocation']==code and type(seeded) is bool
        and (not seeded or code=='C1'),'C1 incumbent allocation identity differs')
    budget=_number(contract['budget_seconds'],'declared C1 search budget')
    base_class='simulation.stable_cp_sat_assignment.StableCPSATAssignmentRule' if code=='C1' else \
        'simulation.heuristic_assignment.HeuristicAssignmentRule'
    actual_class='simulation.c1_incumbent_assignment.StableSeededCPSATAssignmentRule' if seeded else base_class
    _require(contract['base_assignment_class']==base_class and contract['actual_assignment_class']==actual_class
        and contract['variant']==('single_augment' if seeded else None)
        and contract['initializer_max_candidates']==(MAX_CANDIDATES if seeded else None)
        and contract['initializer_limit_seconds']==(INITIALIZER_LIMIT_SECONDS if seeded else None)
        and contract['budget_scope']==(BUDGET_SCOPE if seeded else 'original_assignment_budget'),
        'actual assignment class, variant or initializer limits concealed')
    enabled=scenario.get('inquiry_queue',{}).get('enabled',False)
    replay='CooperativeNormalRecoveryRankReplay' if enabled else 'IndependentNormalRecoveryRankReplay'
    _require(contract['actual_replay_class']=='simulation.recovery_rank_engine.'+replay
        and branch['recovery_rank_variant']=='normal' and all(contract[key] is True for key in (
            'original_normal_rank_workflow_descriptors_retained','inquiry_readiness_and_physical_gates_unchanged',
            'actual_assignment_paths_may_change')),'original normal rank replay or explicit intervention scope changed')
    if code=='C1':
        spec=branch['policy']['solver']
        _require(spec==dict(backend='ortools_cp_sat',time_limit_seconds=budget,seed=0,
            num_search_workers=1,require_backend=True),'C1 solver budget/backend/seed/worker policy differs')
        _require(type(spec['seed']) is int and type(spec['num_search_workers']) is int,
            'C1 solver policy seed or worker count is not integer')
    else:_require(budget==.05,'H1 reference budget changed')
    return contract


def _audit_branch(scenario,branch,*,exact_limit=20000):
    # The frozen whole-raw clinical/work/FIFO/resource/proof auditors see the
    # original object, with no projection, modified fields or monkeypatch.
    result=parent.audit_branch(scenario,branch,exact_limit=exact_limit)
    contract=_audit_contract(scenario,branch)
    records=branch['solver_records']
    if contract is None or not contract['seeded']:
        _require(all(not any(key=='incumbent' or key.startswith('c1_incumbent') for key in record)
            for record in records),'unmodified reference invented initializer traces')
        return result
    counts=Counter(c1_incumbent_batches=0,c1_incumbent_candidates=0,c1_incumbent_improvements=0,
        c1_incumbent_selected=0,c1_incumbent_solver_candidates_rejected=0)
    used=set();elapsed=0.;overrun=0.
    for decision in branch['allocation_decisions']:
        if decision['kind']!='batch':continue
        index=decision['solver_record_index']
        _require(type(index) is int and 0<=index<len(records) and index not in used,
            'missing or reused incumbent solver record')
        used.add(index);record=records[index]
        _require({key for key in record if key.startswith('c1_incumbent')}=={
            'c1_incumbent_budget','c1_incumbent_selection'} and 'incumbent' in record,
            'missing or unknown C1 incumbent solver namespace')
        patients,free,options,travel=joint._problem(scenario,decision)
        trace=record['incumbent']
        budget=_audit_budget(record,trace,contract['budget_seconds'])
        initialized=_audit_initializer(patients,free,options,travel,trace,record['c1_incumbent_budget'])
        selected=_audit_selection(patients,free,options,travel,record,trace)
        if not budget['started']:
            reason=trace['stop_reason']
            _require((reason=='not_started_zero_budget' and record['time_limit_seconds']==0
                and record['fallback_reason']=='time_limit_zero') or
                (reason=='not_started_empty_model' and record['edge_count']==0
                    and record['time_limit_seconds']>0 and record['status']=='OPTIMAL_EMPTY') or
                (reason=='not_started_missing_backend' and record['fallback_reason']=='missing_backend'
                    and record['status']=='MISSING_DEPENDENCY' and bool(record.get('dependency_error'))),
                'unstarted initializer reason lacks original solver control-flow witness')
        _require(type(record['seed']) is int and record['seed']==0
            and type(record['num_search_workers']) is int and record['num_search_workers']==1,
            'incumbent changed solver seed or worker count')
        counts.update(c1_incumbent_batches=1,c1_incumbent_candidates=initialized['candidates'],
            c1_incumbent_improvements=initialized['improvements'],
            c1_incumbent_selected=selected['initializer_selected'],
            c1_incumbent_solver_candidates_rejected=selected['solver_candidates_rejected'])
        elapsed+=initialized['elapsed_seconds'];overrun+=budget['overrun_seconds']
    _require(used==set(range(len(records))),'orphan C1 incumbent solver record')
    result['counts'].update(counts)
    result['c1_incumbent']=dict(variant='single_augment',initializer_seconds=elapsed,
        shared_pipeline_overrun_seconds=overrun,whole_raw_frozen_audit_retained=True,
        initializer_optimality_claimed=False,scope='current_same_minute_accepted_offers_only')
    return result


def audit_branch(scenario,branch,*,exact_limit=20000):
    """Read-only whole-raw audit; no replay, initializer or solver execution."""
    _require(type(exact_limit) is int and exact_limit>=0,'invalid exact enumeration audit limit')
    try:return _audit_branch(scenario,branch,exact_limit=exact_limit)
    except (KeyError,TypeError,AttributeError,IndexError,OverflowError) as error:
        raise C1IncumbentAuditError('malformed C1 incumbent artifact') from error

