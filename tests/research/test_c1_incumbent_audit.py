"""Independent current-input arithmetic and actual saved C1 trace audits.

The test module never imports the initializer, replay or OR-Tools.  Any
hand-built timing traces below are audit fault tests, not formal performance
executions or clinical/simulation benchmark observations.
"""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from simulation import c1_incumbent_audit as audit


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / 'simulation/fixtures/c1_incumbent_20261020'
# Pinned from actual unique seed-7 preflight executions before source freeze.
FIXTURE_SHA256 = {
    'out_of_order_50ms.json.gz':'dc044167ba3aa7b1d48fecf5f9e7bae246c011f65bda8b2c81e0d23ea85f80cd',
    'native_budget.json.gz':'398aa4a00700d0b7bf057484d2312d57b2c1dab498eace579520bb651d2709f2',
    'multiple_resources.json.gz':'282d0883e3416e1ebd713a616cc47a4b6407bafe980bf604330b0b2ffd39bb26',
    'cooperative.json.gz':'cb1659dd0dd417cfceb487d0393f002522e98b28480e1fac35a8bff9f0786739',
    'no_repair.json.gz':'f04a02a2d6d6aa06b0cf28c10c69eb93ff8834fe78e04f2b55f8cd1db2e1f0c6',
}


def read_fixtures():
    paths={path.name:path for path in FIXTURE_DIR.glob('*.json.gz')}
    if len(FIXTURE_SHA256)!=5 or set(paths)!=set(FIXTURE_SHA256):
        raise AssertionError('complete fixed actual C1 incumbent fixture inventory differs')
    fixtures={}
    for name,path in paths.items():
        before=path.stat();compressed=path.read_bytes();after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) \
                or len(compressed)!=after.st_size \
                or hashlib.sha256(compressed).hexdigest()!=FIXTURE_SHA256[name]:
            raise AssertionError('actual incumbent fixture bytes changed: '+name)
        raw=json.loads(gzip.decompress(compressed))
        if raw['scenario']['seed']!=7 or not raw['branches'] \
                or any(branch['allocation_intervention_id']!=audit.EXPERIMENT_ID for branch in raw['branches']):
            raise AssertionError('fixture is not its declared unique actual seed-7 execution')
        fixtures[name]=raw
    if sum(len(raw['branches']) for raw in fixtures.values())!=8:
        raise AssertionError('actual fixed fixture branch inventory differs')
    return fixtures


def problem():
    patients = [dict(id='A',priority=4,request_t=0,demand={'bed':1}),
                dict(id='B',priority=4,request_t=1,demand={'bed':1})]
    free = {('H1','bed'):1,('H2','bed'):1}
    options = {'A':['H1','H2'],'B':['H1']}
    travel = {('A','H1'):1,('A','H2'):2,('B','H1'):1}
    return patients,free,options,travel


def controlled_initializer_trace(*,started=True,reason='improvement'):
    """Hand-built audit fault-test clock; never an executed replay fixture."""
    patients,free,options,travel=problem()
    baseline=audit._greedy(patients,free,options,travel)
    objective=audit._objective(patients,baseline,travel)
    trace=dict(schema_version=1,variant='single_augment',rule=audit.RULE,
        max_candidates=128,initializer_limit_seconds=.005,budget_scope=audit.BUDGET_SCOPE,
        original_greedy_assignments=baseline,original_greedy_objective=objective,
        selected_assignments=dict(baseline),selected_objective=list(objective),
        applied=False,evaluated_candidates=0,candidates=[],stop_reason=reason,
        elapsed_seconds=0.,started_at=None,deadline_started_at=None,global_deadline_at=None,
        initializer_deadline_at=None,finished_at=None,optimality_claimed=False)
    budget=dict(started_at=None,deadline_at=None)
    if not started:return (patients,free,options,travel),trace,budget
    pid,hid,blocker,alternate,plan=next(audit._candidate_plans(patients,options,baseline))
    value=audit._objective(patients,plan,travel)
    trace.update(started_at=10.001,deadline_started_at=10.,global_deadline_at=10.05,
        initializer_deadline_at=10.006,finished_at=10.0013,elapsed_seconds=.0003,
        selected_assignments=plan,selected_objective=value,applied=True,evaluated_candidates=1,
        candidates=[dict(candidate_index=1,patient=pid,hospital=hid,blocker=blocker,
            alternate=alternate,assignments=dict(plan),feasible=True,objective=list(value),
            accepted=True,started_at=10.0011,finished_at=10.0012)])
    budget.update(started_at=10.,deadline_at=10.05)
    return (patients,free,options,travel),trace,budget


class C1IncumbentIndependentArithmeticTests(unittest.TestCase):
    def test_original_priority_request_identity_baseline_is_unchanged(self):
        patients,free,options,travel = problem()
        self.assertEqual(audit._greedy(patients,free,options,travel),{'A':'H1','B':None})
        self.assertEqual(audit._greedy(list(reversed(patients)),free,options,travel),
                         {'A':'H1','B':None})

    def test_later_current_pool_scarce_patient_has_single_blocker_witness(self):
        patients,free,options,travel = problem()
        baseline = audit._greedy(patients,free,options,travel)
        candidates = list(audit._candidate_plans(patients,options,baseline))
        self.assertEqual(candidates,[('B','H1','A','H2',{'A':'H2','B':'H1'})])
        candidate = candidates[0][-1]
        self.assertTrue(audit._feasible(patients,free,options,candidate))
        self.assertLess(audit._objective(patients,candidate,travel),
                        audit._objective(patients,baseline,travel))

    def test_all_required_resources_checked_after_blocker_moves(self):
        patients,free,options,travel = problem()
        for patient in patients:patient['demand']['team']=1
        free.update({('H1','team'):1,('H2','team'):0})
        baseline = {'A':'H1','B':None}
        candidate = next(audit._candidate_plans(patients,options,baseline))[-1]
        self.assertFalse(audit._feasible(patients,free,options,candidate))

    def test_every_existing_assigned_patient_remains_assigned(self):
        patients,free,options,travel = problem()
        patients.append(dict(id='C',priority=2,request_t=0,demand={'bed':1}))
        options['C']=['H3'];free['H3','bed']=1;travel['C','H3']=2
        baseline = audit._greedy(patients,free,options,travel)
        for _,_,blocker,alternate,candidate in audit._candidate_plans(patients,options,baseline):
            self.assertIsNotNone(alternate)
            self.assertTrue(all(candidate[pid] is not None for pid,hid in baseline.items()
                                if hid is not None))
            self.assertEqual(candidate['C'],'H3')

    def test_no_alternate_cannot_evict_blocker(self):
        patients,free,options,travel = problem();options['A']=['H1']
        baseline = audit._greedy(patients,free,options,travel)
        self.assertEqual(list(audit._candidate_plans(patients,options,baseline)),[])

    def test_options_and_patients_have_fixed_identity_order(self):
        patients,free,options,travel = problem()
        options['A']=['H3','H2','H1'];free['H3','bed']=1;travel['A','H3']=3
        baseline = audit._greedy(patients,free,options,travel)
        self.assertEqual([row[3] for row in audit._candidate_plans(patients,options,baseline)],
                         ['H2','H3'])

    def test_clean_import_does_not_load_new_or_old_engines_or_solver(self):
        script = ("import json,sys; import simulation.c1_incumbent_audit; "
            "print(json.dumps(sorted(name for name in sys.modules if "
            "name.startswith('ortools') or name in {"
            "'simulation.c1_incumbent_assignment','simulation.c1_incumbent_engine',"
            "'simulation.cp_sat_assignment','simulation.stable_cp_sat_assignment',"
            "'simulation.recovery_rank_engine','simulation.sim'})))")
        output=subprocess.check_output([sys.executable,'-B','-c',script],cwd=ROOT,text=True)
        self.assertEqual(json.loads(output),[])


class C1IncumbentControlledClockAuditFaultTests(unittest.TestCase):
    """Mutations of hand-built evidence, not algorithm performance reruns."""
    def reject(self,values,trace,budget):
        with self.assertRaises(audit.C1IncumbentAuditError):
            audit._audit_initializer(*values,trace,budget)

    def test_declared_first_strict_single_augment_and_read_only(self):
        values,trace,budget=controlled_initializer_trace()
        before=deepcopy((values,trace,budget))
        self.assertEqual(audit._audit_initializer(*values,trace,budget)['improvements'],1)
        self.assertEqual((values,trace,budget),before)

    def test_all_initializer_fields_required(self):
        for key in audit.TRACE_FIELDS:
            with self.subTest(key=key):
                values,trace,budget=controlled_initializer_trace();del trace[key]
                with self.assertRaises((audit.C1IncumbentAuditError,KeyError)):
                    audit._audit_initializer(*values,trace,budget)

    def test_unknown_future_input_rejected(self):
        values,trace,budget=controlled_initializer_trace();trace['future_patient']=dict(id='C')
        self.reject(values,trace,budget)

    def test_unknown_candidate_field_rejected(self):
        values,trace,budget=controlled_initializer_trace();trace['candidates'][0]['release_at']=12
        self.reject(values,trace,budget)

    def test_original_baseline_and_objective_cannot_be_relabelled(self):
        for field,value in (('original_greedy_assignments',{'A':'H2','B':'H1'}),
                ('original_greedy_objective',[0,12])):
            values,trace,budget=controlled_initializer_trace();trace[field]=value
            self.reject(values,trace,budget)

    def test_candidate_cannot_drop_an_assigned_blocker(self):
        values,trace,budget=controlled_initializer_trace()
        trace['candidates'][0]['alternate']=None;trace['candidates'][0]['assignments']['A']=None
        self.reject(values,trace,budget)

    def test_candidate_resource_feasibility_cannot_be_fabricated(self):
        values,trace,budget=controlled_initializer_trace()
        values[1]['H2','bed']=0
        self.reject(values,trace,budget)

    def test_candidate_order_and_count_are_exact(self):
        for field,value in (('candidate_index',2),('patient','A'),('hospital','H2'),
                ('blocker','B'),('alternate','H1')):
            values,trace,budget=controlled_initializer_trace();trace['candidates'][0][field]=value
            self.reject(values,trace,budget)
        values,trace,budget=controlled_initializer_trace();trace['evaluated_candidates']=0
        self.reject(values,trace,budget)

    def test_feasibility_and_acceptance_must_be_boolean(self):
        for field in ('feasible','accepted'):
            values,trace,budget=controlled_initializer_trace();trace['candidates'][0][field]=1
            self.reject(values,trace,budget)

    def test_initializer_does_not_claim_optimality(self):
        values,trace,budget=controlled_initializer_trace();trace['optimality_claimed']=True
        self.reject(values,trace,budget)

    def test_no_candidate_may_start_after_local_or_global_deadline(self):
        values,trace,budget=controlled_initializer_trace()
        trace.update(finished_at=10.007,elapsed_seconds=.006)
        trace['candidates'][0].update(started_at=10.006,finished_at=10.0061)
        self.reject(values,trace,budget)

    def test_no_second_candidate_after_first_improvement(self):
        values,trace,budget=controlled_initializer_trace()
        trace['candidates'].append(deepcopy(trace['candidates'][0]));trace['evaluated_candidates']=2
        self.reject(values,trace,budget)

    def test_budget_cannot_be_renewed_or_elapsed_time_omitted(self):
        for field,value in (('global_deadline_at',10.1),('initializer_deadline_at',10.05),
                ('elapsed_seconds',0.)):
            values,trace,budget=controlled_initializer_trace();trace[field]=value
            self.reject(values,trace,budget)

    def test_no_early_exhaustion_of_fixed_candidate_sequence(self):
        values,trace,budget=controlled_initializer_trace()
        trace.update(candidates=[],evaluated_candidates=0,applied=False,stop_reason='exhausted',
            selected_assignments=trace['original_greedy_assignments'],
            selected_objective=trace['original_greedy_objective'])
        self.reject(values,trace,budget)

    def test_zero_empty_missing_backend_have_no_initializer_search(self):
        for reason in ('not_started_zero_budget','not_started_empty_model','not_started_missing_backend'):
            values,trace,budget=controlled_initializer_trace(started=False,reason=reason)
            self.assertEqual(audit._audit_initializer(*values,trace,budget)['candidates'],0)
            trace['elapsed_seconds']=.001
            self.reject(values,trace,budget)


class C1IncumbentSelectionAndBudgetFaultTests(unittest.TestCase):
    """Unit evidence faults only; no injected timing in formal replay data."""
    def selection(self,*,primary=False):
        values,trace,_=controlled_initializer_trace()
        plan=trace['selected_assignments'] if primary else trace['original_greedy_assignments']
        objective=trace['selected_objective'] if primary else trace['original_greedy_objective']
        stages=[dict(status='OPTIMAL' if primary else 'FEASIBLE',objective=8 if primary else 4,
            best_bound=8,wall_seconds=.0007,objective_name='assigned_priority_weight')]
        record=dict(stages=stages,status='FEASIBLE_PRIMARY_OPTIMAL' if primary else 'FEASIBLE',
            primary_optimal_proven=primary,secondary_optimal_proven=False,optimal_proven=False,
            fallback=not primary,fallback_reason=None if primary else 'incumbent_better_than_solver_incumbent',
            objective=trace['selected_objective'],stability=dict(solver_assignments=trace['selected_assignments']),
            c1_incumbent_selection=dict(initializer_assignments=trace['selected_assignments'],
                initializer_objective=trace['selected_objective'],stage_candidates=[dict(stage_index=0,
                    assignments=plan,objective=objective,feasible=True,selected=primary)],
                selected_source='solver_stage_one' if primary else 'initializer',
                selected_assignments=trace['selected_assignments'],selected_objective=trace['selected_objective']))
        return values,trace,record

    def budget(self):
        values,trace,record=self.selection()
        record.update(time_limit_seconds=.05,solver_pipeline_wall_seconds=.004,
            c1_incumbent_budget=dict(scope=audit.BUDGET_SCOPE,limit_seconds=.05,started_at=10.,
                deadline_at=10.05,finished_at=10.003,model_started_at=10.0014,
                model_finished_at=10.002,stage_requests=[dict(index=0,requested_at=10.0021,
                    remaining_seconds=10.05-10.0021,finished_at=10.0029)]))
        return trace,record

    def test_inferior_solver_incumbent_is_rejected_for_actual_initializer(self):
        values,trace,record=self.selection()
        checked=audit._audit_selection(*values,record,trace)
        self.assertEqual(checked,dict(initializer_selected=1,solver_candidates_rejected=1))
        record['c1_incumbent_selection']['stage_candidates'][0]['selected']=True
        with self.assertRaises(audit.C1IncumbentAuditError):audit._audit_selection(*values,record,trace)

    def test_partial_primary_proof_requires_returned_assigned_weight(self):
        values,trace,record=self.selection(primary=True)
        audit._audit_selection(*values,record,trace)
        record['stages'][0]['objective']=4
        with self.assertRaises(audit.C1IncumbentAuditError):audit._audit_selection(*values,record,trace)

    def test_secondary_bound_cannot_exceed_retained_feasible_cost(self):
        values,trace,record=self.selection(primary=True)
        record['stages'].append(dict(status='UNKNOWN',objective=None,best_bound=13,
            wall_seconds=.001,objective_name='priority_weighted_travel_minutes'))
        with self.assertRaises(audit.C1IncumbentAuditError):audit._audit_selection(*values,record,trace)

    def test_heuristic_incumbent_does_not_become_primary_optimum(self):
        values,trace,record=self.selection();record['primary_optimal_proven']=True
        with self.assertRaises(audit.C1IncumbentAuditError):audit._audit_selection(*values,record,trace)

    def test_selected_source_and_baseline_retention_are_bound(self):
        for field,value in (('selected_source','original_greedy'),('selected_objective',[4,4]),
                ('initializer_assignments',{'A':'H1','B':None})):
            values,trace,record=self.selection();record['c1_incumbent_selection'][field]=value
            with self.assertRaises(audit.C1IncumbentAuditError):audit._audit_selection(*values,record,trace)

    def test_initializer_model_and_solver_share_one_actual_deadline(self):
        trace,record=self.budget()
        self.assertTrue(audit._audit_budget(record,trace,.05)['started'])
        record['c1_incumbent_budget']['stage_requests'][0]['remaining_seconds']=.05
        with self.assertRaises(audit.C1IncumbentAuditError):audit._audit_budget(record,trace,.05)

    def test_atomic_overrun_is_measured_without_renewing_search_budget(self):
        trace,record=self.budget()
        record['c1_incumbent_budget']['stage_requests'][0]['finished_at']=10.06
        record['c1_incumbent_budget']['finished_at']=10.061
        record['solver_pipeline_wall_seconds']=.062
        self.assertAlmostEqual(audit._audit_budget(record,trace,.05)['overrun_seconds'],.011)

    def test_no_solver_start_after_global_deadline(self):
        trace,record=self.budget();request=record['c1_incumbent_budget']['stage_requests'][0]
        request.update(requested_at=10.05,remaining_seconds=.05,finished_at=10.06)
        record['c1_incumbent_budget']['finished_at']=10.061;record['solver_pipeline_wall_seconds']=.062
        with self.assertRaises(audit.C1IncumbentAuditError):audit._audit_budget(record,trace,.05)

    def test_initializer_overhead_cannot_disappear_from_total_pipeline_time(self):
        trace,record=self.budget();record['solver_pipeline_wall_seconds']=.0001
        with self.assertRaises(audit.C1IncumbentAuditError):audit._audit_budget(record,trace,.05)

    def test_model_omission_requires_real_global_exhaustion(self):
        trace,record=self.budget();record['stages']=[]
        record['c1_incumbent_budget'].update(model_started_at=None,model_finished_at=None,stage_requests=[])
        record['fallback_reason']='budget_exhausted_before_model'
        with self.assertRaises(audit.C1IncumbentAuditError):audit._audit_budget(record,trace,.05)
        record['c1_incumbent_budget']['finished_at']=10.05;record['solver_pipeline_wall_seconds']=.051
        self.assertTrue(audit._audit_budget(record,trace,.05)['started'])


class C1IncumbentSavedWholeRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.fixtures=read_fixtures()

    def fixture(self,*,augmented=False):
        for raw in self.fixtures.values():
            for branch in raw['branches']:
                if not branch['c1_incumbent']['seeded']:continue
                if augmented and not any(record['incumbent']['candidates'] for record in branch['solver_records']):
                    continue
                return deepcopy((raw['scenario'],branch))
        raise AssertionError('portable actual fixture lacks required initializer witness')

    def reject(self,scenario,branch):
        with self.assertRaises(audit.C1IncumbentAuditError):audit.audit_branch(scenario,branch)

    def test_all_fixed_actual_seed7_complete_workflows(self):
        for name,raw in self.fixtures.items():
            for branch in raw['branches']:
                with self.subTest(name=name,branch=branch['id']):
                    self.assertTrue(audit.audit_branch(raw['scenario'],branch)['passed'])

    def test_whole_raw_audit_is_read_only(self):
        scenario,branch=self.fixture();before=deepcopy((scenario,branch))
        audit.audit_branch(scenario,branch)
        self.assertEqual((scenario,branch),before)

    def test_complete_actual_namespace_is_required(self):
        for key in audit.NAMESPACE_FIELDS:
            scenario,branch=self.fixture();del branch[key];self.reject(scenario,branch)
        scenario,branch=self.fixture()
        for key in audit.NAMESPACE_FIELDS:del branch[key]
        self.reject(scenario,branch)

    def test_actual_assignment_class_budget_and_workflow_cannot_be_concealed(self):
        changes=(('actual_assignment_class','simulation.stable_cp_sat_assignment.StableCPSATAssignmentRule'),
            ('budget_seconds',.1),('actual_replay_class','simulation.offer_reserve_engine.CooperativeOfferReserveReplay'),
            ('inquiry_readiness_and_physical_gates_unchanged',False))
        for key,value in changes:
            scenario,branch=self.fixture();branch['c1_incumbent'][key]=value;self.reject(scenario,branch)

    def test_unknown_branch_and_record_namespace_fields_rejected(self):
        scenario,branch=self.fixture();branch['c1_incumbent_future']=1;self.reject(scenario,branch)
        scenario,branch=self.fixture();branch['solver_records'][0]['c1_incumbent_future']=1
        self.reject(scenario,branch)

    def test_original_raw_contact_work_validation_remains_active(self):
        scenario,branch=self.fixture()
        event=next(row for row in branch['ordered_emissions'] if row['type']=='ask')
        for key in ('events','ordered_emissions'):
            next(row for row in branch[key] if row.get('event_sequence')==event['event_sequence'])['attempt']+=1
        self.reject(scenario,branch)

    def test_original_actual_confirmation_gate_validation_remains_active(self):
        scenario,branch=self.fixture()
        decision=next(row for row in branch['allocation_decisions'] if row['gate_records'])
        decision['gate_records'][0]['passed']=not decision['gate_records'][0]['passed']
        self.reject(scenario,branch)

    def test_actual_initializer_witness_cannot_be_changed_to_eviction(self):
        scenario,branch=self.fixture(augmented=True)
        trace=next(record['incumbent'] for record in branch['solver_records']
            if record['incumbent']['candidates'])
        candidate=trace['candidates'][0]
        candidate['assignments'][candidate['blocker']]=None;candidate['alternate']=None
        self.reject(scenario,branch)

    def test_actual_shared_deadline_cannot_be_renewed(self):
        scenario,branch=self.fixture(augmented=True)
        record=next(record for record in branch['solver_records'] if record['incumbent']['started_at'] is not None)
        record['c1_incumbent_budget']['deadline_at']+=.05
        self.reject(scenario,branch)

    def test_actual_retained_source_cannot_be_relabelled(self):
        scenario,branch=self.fixture(augmented=True)
        record=next(record for record in branch['solver_records'] if record['incumbent']['candidates'])
        record['c1_incumbent_selection']['selected_source']='unrecorded_new_search'
        self.reject(scenario,branch)

    def test_actual_initializer_cannot_claim_whole_episode_optimum(self):
        scenario,branch=self.fixture();branch['solver_records'][0]['incumbent']['optimality_claimed']=True
        self.reject(scenario,branch)


if __name__=='__main__':unittest.main()
