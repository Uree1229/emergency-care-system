"""Independent W1 arithmetic, input provenance and endpoint gate traps."""
from copy import deepcopy
import itertools
import random
import unittest

from simulation import waiting_audit_20261030 as audit
from test_final_assignment_audit_20261028 import fixture as control_fixture


def row(pid,priority=1,options=('H',)):
    return dict(id=pid,priority=priority,demand={'ed_bed':1},options=list(options),request_t=0)


def fixture():
    control=control_fixture()
    waiting=deepcopy(next(b for b in control['branches'] if b['id']=='H1'))
    waiting['id']='W1';waiting['policy']['rule']='W1'
    originals=deepcopy(waiting['heuristic_records'])
    waiting['heuristic_records']=[];waiting['waiting_records']=[]
    for index,decision in enumerate(waiting['allocation_decisions']):
        decision['rule']='W1'
        if decision['kind']!='batch':
            continue
        original=originals[decision.pop('heuristic_record_index')]
        patients,free,options,travel=audit.current._problem(control['original_scenario'],decision)
        ages={p['id']:decision['t']-p['request_t'] for p in patients}
        objective=audit.objective_oracle(patients,decision['assignments'],travel,ages)
        record=dict(t=decision['t'],decision_index=index,ages=ages,age_buckets=audit.age_buckets(patients,ages),
            observed_waiting_ages=ages,time_limit_seconds=10,future_inputs_used=False,
            whole_episode_nonregression_guaranteed=False,wall_seconds=original['wall_seconds'],
            preprocessing_seconds=original['preprocessing_seconds'],search_seconds=original['search_seconds'],
            assignments=decision['assignments'],objective=objective,optimal_proven=False,
            H1_reference_assignments=decision['assignments'],H1_reference_objective=original['objective'],
            H1_reference_primary=original['objective'][0],H1_reference_waiting_objective=objective,
            H1_reference_record=original,H1_reference_evaluated_moves=original['evaluated_moves'],
            baseline_assignments=decision['assignments'],baseline_objective=objective,
            waiting_evaluated_moves=0,evaluated_moves=original['evaluated_moves'],max_moves=2048,
            search_stop_reason='search_complete',accepted_moves=0,improvements=[],search_mode='baseline_only',small_enumeration_complete=False)
        decision['waiting_ages']=ages
        decision['waiting_record_index']=len(waiting['waiting_records'])
        waiting['waiting_records'].append(record)
    return dict(original_scenario=control['original_scenario'],control_replay=control,
        branches=deepcopy(control['branches'])+[waiting])


def endpoint(pid,arrival,priority=4,end=180):
    return dict(id=pid,priority=priority,occur_t=0,search_start_t=0,observation_end_t=end,
        first_commit_t=1,final_arrive_t=arrival,treatment_start_t=arrival,selected=True,
        arrived=arrival is not None,arrival_capped_minutes=arrival if arrival is not None else end,
        arrival_elapsed_minutes=arrival,selection_elapsed_minutes=1,selection_capped_minutes=1,inquiries=1)


def gate_fixture(h1=20,w1=19,r1=21):
    vectors={'R0':[endpoint('P',r1)],'R1':[endpoint('P',r1)],'H1':[endpoint('P',h1)],'W1':[endpoint('P',w1)]}
    return dict(passed=True,case_id='unit-case',profile='unit-profile',patient_vectors=vectors)


class ObjectiveTests(unittest.TestCase):
    def test_primary_priority_weight_precedes_waiting(self):
        rows=[row('old',1),row('young',2)];ages={'old':100,'young':0}
        travel={('old','H'):1,('young','H'):1}
        a=audit.objective_oracle(rows,{'old':'H','young':None},travel,ages)
        b=audit.objective_oracle(rows,{'old':None,'young':'H'},travel,ages)
        self.assertLess(b,a)

    def test_exact_old_age_bucket_precedes_travel_only_on_primary_tie(self):
        rows=[row('old'),row('young')];ages={'old':10,'young':1}
        travel={('old','H'):100,('young','H'):1}
        old=audit.objective_oracle(rows,{'old':'H','young':None},travel,ages)
        young=audit.objective_oracle(rows,{'old':None,'young':'H'},travel,ages)
        self.assertEqual(old,[1,0,100]);self.assertEqual(young,[1,1,1]);self.assertLess(old,young)

    def test_same_age_removes_redundant_waiting_bucket(self):
        rows=[row('A'),row('B')];ages={'A':3,'B':3}
        self.assertEqual(audit.age_buckets(rows,ages),[])
        self.assertEqual(audit.objective_oracle(rows,{'A':None,'B':'H'},{('A','H'):5,('B','H'):1},ages),[1,1])

    def test_invalid_ages_and_plan_partitions_are_rejected(self):
        rows=[row('A')]
        for ages in ({},{'A':True},{'A':-1}):
            with self.assertRaises(audit.WaitingAuditError):audit.age_buckets(rows,ages)
        with self.assertRaises(audit.WaitingAuditError):audit.objective_oracle(rows,{}, {},{'A':0})

    def test_small_exact_oracle_and_capacity(self):
        rows=[row('old'),row('young')];ages={'old':10,'young':1};travel={('old','H'):100,('young','H'):1}
        free={('H','ed_bed'):1};options={'old':['H'],'young':['H']}
        self.assertEqual(audit.exact_current_objective(rows,free,options,travel,ages)['objective'],[1,0,100])
        self.assertFalse(audit.exact_current_objective(rows,free,options,travel,ages,max_combinations=1)['enumerated'])
        self.assertFalse(audit.feasible(rows,{'old':'H','young':'H'},free,options))

    def test_candidate_objective_matches_independent_implementation(self):
        # Tests may import the candidate; the auditor itself never imports it.
        from simulation.waiting_assignment_20261030 import waiting_objective,age_buckets
        rng=random.Random(202610300901)
        for _ in range(30):
            rows=[row(str(i),rng.choice((4,2,1)),('A','B')) for i in range(4)]
            ages={p['id']:rng.randrange(5) for p in rows}
            travel={(p['id'],h):rng.randrange(1,20) for p in rows for h in p['options']}
            self.assertEqual(audit.age_buckets(rows,ages),age_buckets(rows,ages))
            for choices in itertools.product(('A','B',None),repeat=4):
                plan=dict(zip((p['id'] for p in rows),choices))
                self.assertEqual(audit.objective_oracle(rows,plan,travel,ages),waiting_objective(rows,plan,travel,ages))


class RecordedAuditTests(unittest.TestCase):
    def test_w1_as_w1_keeps_control_endpoints_and_current_ledger(self):
        result=audit.audit_replay(fixture())
        self.assertTrue(result['passed']);self.assertGreater(result['waiting_audit']['age_checks'],0)
        self.assertEqual(result['patient_vectors']['H1'],result['patient_vectors']['W1'])

    def test_actual_unit_engine_record_and_total_technical_guard_are_compatible(self):
        from test_waiting_experiment_20261030 import WaitingEngineTests
        from simulation.waiting_experiment_20261030 import run_replay
        replay=run_replay(WaitingEngineTests().unit_case(),['W1','R0','H1','R1'])
        result=audit.audit_replay(replay)
        self.assertTrue(result['passed'])
        self.assertGreater(result['waiting_audit']['primary_preservation_checks'],0)

    def test_complete_enumeration_flag_cannot_be_truthy_or_mismatched(self):
        for flag in (1,True):
            value=fixture();value['branches'][-1]['waiting_records'][0]['small_enumeration_complete']=flag
            with self.assertRaises(audit.WaitingAuditError):audit.audit_replay(value)

    def test_future_field_age_and_budget_tampering_rejected(self):
        for kind in ('future','age','budget','orphan','primary'):
            value=fixture();branch=value['branches'][-1];decision=next(d for d in branch['allocation_decisions'] if d['kind']=='batch')
            if kind=='future':decision['inputs']['future_reply_schedule']=[]
            elif kind=='age':decision['waiting_ages'][next(iter(decision['waiting_ages']))]+=1
            elif kind=='budget':branch['waiting_records'][0]['evaluated_moves']=2049
            elif kind=='orphan':branch['waiting_records'].append(deepcopy(branch['waiting_records'][0]))
            else:branch['waiting_records'][0]['H1_reference_primary']+=1
            with self.subTest(kind=kind),self.assertRaises((audit.WaitingAuditError,audit.current.OnlineAllocationAuditError)):
                audit.audit_replay(value)


class GateTests(unittest.TestCase):
    def test_research_gate_eligible_requires_actual_arrival_improvement(self):
        result=audit.evaluate_gates([gate_fixture()])
        self.assertTrue(result['research_recommendation_eligible']);self.assertFalse(result['operational_adoption'])
        same=audit.evaluate_gates([gate_fixture(w1=20)])
        self.assertFalse(same['passed'])

    def test_zero_capped_difference_cannot_hide_success_loss(self):
        case=gate_fixture(h1=180,w1=None,r1=180)
        result=audit.evaluate_gates([case])
        self.assertFalse(result['passed'])
        self.assertEqual(result['comparisons']['H1']['maximum_positive_capped_difference'],0)
        self.assertEqual(result['success_losses'][0]['delta_capped_minutes'],0)
        self.assertIn('arrived',result['success_losses'][0]['lost'])

    def test_any_individual_delay_shift_fails_even_with_mean_gain(self):
        case=gate_fixture();case['patient_vectors']['H1'].append(endpoint('Q',20));case['patient_vectors']['W1'].append(endpoint('Q',21))
        for code in ('R0','R1'):case['patient_vectors'][code].append(endpoint('Q',22))
        result=audit.evaluate_gates([case]);self.assertFalse(result['passed'])
        self.assertEqual(result['comparisons']['H1']['capped_difference']['mean'],0)

    def test_gate_mutation_duplicates_or_failed_audits_cannot_be_reinterpreted(self):
        criteria=deepcopy(audit.GATE_CRITERIA);criteria['maximum_positive_capped_difference_vs_H1']=5
        for cases,rule in (([gate_fixture()],criteria),([gate_fixture(),gate_fixture()],None),([dict(gate_fixture(),passed=False)],None)):
            with self.assertRaises(audit.WaitingAuditError):audit.evaluate_gates(cases,rule)


if __name__=='__main__':unittest.main()
