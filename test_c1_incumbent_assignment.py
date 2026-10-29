"""Initializer properties and explicitly labelled controlled-clock fault tests."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation import c1_incumbent_assignment as new
from simulation.cp_sat_assignment import assignment_objective,validate_assignment,CPSATDependencyError,_load_cp_model
from simulation.stable_cp_sat_assignment import StableCPSATAssignmentRule,TIE_RULE


def patient(pid,priority=4,demand=None,request_t=0):
    return SimpleNamespace(id=pid,priority=priority,t_req=request_t,
        demand=dict(ed_bed=1) if demand is None else dict(demand),loc=(0,0))


class World:
    def __init__(self,free,travel=None):
        self.free=deepcopy(free);self.times=dict(travel or {})
        self.hospitals={hid:SimpleNamespace(free=lambda r,hid=hid:self.free[hid].get(r,0)) for hid in free}
    def position(self,p,t):return p.loc
    def travel(self,p,hid,position):return self.times.get((p.id,hid),3)


class ControlledClock:
    """Unit fault injection only; not a formal environment or timing result."""
    def __init__(self):self.t=100.;self.step=.000001
    def __call__(self):self.t+=self.step;return self.t


class IncumbentTests(unittest.TestCase):
    def setUp(self):
        self.rule=new.StableSeededCPSATAssignmentRule()
        self.world=World({'NEAR':{'ed_bed':1},'FAR':{'ed_bed':1}},
            {('P02','NEAR'):3,('P02','FAR'):9,('P03','NEAR'):3})
        self.ready=[(patient('P02'),['NEAR','FAR']),(patient('P03'),['NEAR'])]

    def seed(self,world=None,ready=None):
        rows,travel,free=self.rule._snapshot(world or self.world,ready or self.ready,0)
        baseline=self.rule._greedy(rows,travel,free)
        clock=ControlledClock()
        with patch.object(new.time,'perf_counter',clock):
            plan,trace=self.rule._initialize(rows,travel,free,baseline,100.,100.05)
        return rows,travel,free,baseline,plan,trace

    def test_exact_parent_tie_body_and_greedy_are_reused(self):
        self.assertIs(new.StableSeededCPSATAssignmentRule.decide_batch,StableCPSATAssignmentRule.decide_batch)
        self.assertNotIn('_greedy',new.SeededCPSATAssignmentRule.__dict__)
        mro=new.StableSeededCPSATAssignmentRule.__mro__
        self.assertLess(mro.index(StableCPSATAssignmentRule),mro.index(new.SeededCPSATAssignmentRule))

    def test_one_current_blocker_augment_solves_the_saved_model_shape(self):
        rows,travel,free,baseline,plan,trace=self.seed()
        self.assertEqual(baseline,{'P02':'NEAR','P03':None})
        self.assertEqual(plan,{'P02':'FAR','P03':'NEAR'})
        self.assertEqual(assignment_objective(rows,baseline,travel),[4,12])
        self.assertEqual(assignment_objective(rows,plan,travel),[0,48])
        self.assertTrue(validate_assignment(rows,plan,free))
        self.assertEqual(trace['stop_reason'],'improvement')
        self.assertEqual(trace['evaluated_candidates'],1)
        self.assertFalse(trace['optimality_claimed'])

    def test_each_originally_assigned_patient_remains_assigned(self):
        *_,baseline,plan,trace=self.seed()
        self.assertTrue(all(plan[pid] is not None for pid,hid in baseline.items() if hid is not None))
        self.assertEqual(sum(h is not None for h in plan.values()),sum(h is not None for h in baseline.values())+1)

    def test_multiple_resources_can_refuse_an_apparent_bed_repair(self):
        world=World({'NEAR':{'ed_bed':2,'team':1},'FAR':{'ed_bed':1,'team':1}},
            {('A','NEAR'):1,('A','FAR'):3})
        ready=[(patient('A',demand={'ed_bed':1,'team':1}),['NEAR','FAR']),
            (patient('B',demand={'ed_bed':1,'team':1}),['NEAR']),
            (patient('C',demand={'team':1}),['FAR'])]
        rows,travel,free,baseline,plan,trace=self.seed(world,ready)
        # Beds permit A to move FAR and B to take NEAR, but FAR's team is used.
        self.assertEqual(baseline,{'A':'NEAR','B':None,'C':'FAR'})
        self.assertEqual(plan,baseline)
        self.assertFalse(trace['applied'])
        self.assertEqual(len(trace['candidates']),1)
        self.assertFalse(trace['candidates'][0]['feasible'])
        self.assertTrue(validate_assignment(rows,plan,free))

    def test_no_alternate_is_never_replaced_by_none(self):
        world=World({'NEAR':{'ed_bed':1}})
        ready=[(patient('A'),['NEAR']),(patient('B'),['NEAR'])]
        *_,baseline,plan,trace=self.seed(world,ready)
        self.assertEqual(plan,baseline);self.assertEqual(trace['candidates'],[])
        self.assertEqual(trace['stop_reason'],'exhausted')

    def test_permutations_do_not_change_normalized_snapshot_seed(self):
        *_,expected,_=self.seed()
        reversed_ready=[(p,list(reversed(options))+options) for p,options in reversed(self.ready)]
        *_,actual,_=self.seed(ready=reversed_ready)
        self.assertEqual(actual,expected)

    def test_initializer_and_input_resources_are_not_mutated(self):
        before=deepcopy(self.world.free)
        self.seed()
        self.assertEqual(self.world.free,before)

    def test_expired_shared_budget_does_not_evaluate_a_candidate(self):
        rows,travel,free=self.rule._snapshot(self.world,self.ready,0)
        baseline=self.rule._greedy(rows,travel,free)
        with patch.object(new.time,'perf_counter',return_value=100.1):
            plan,trace=self.rule._initialize(rows,travel,free,baseline,100.,100.05)
        self.assertEqual(plan,baseline);self.assertEqual(trace['evaluated_candidates'],0)
        self.assertEqual(trace['stop_reason'],'global_time_limit')

    def test_candidate_cap_is_a_fixed_prefix_of_proposals(self):
        rows=[dict(id='A',priority=4,demand={'bed':2},options=['H']+[f'Z{i:03}' for i in range(160)]),
            dict(id='Q',priority=4,demand={'bed':1},options=['H'])]
        rows += [dict(id=f'R{i:03}',priority=4,demand={'bed':2},options=[f'Z{i:03}']) for i in range(160)]
        free={(hid,'bed'):2 for hid in rows[0]['options']}
        travel={(r['id'],h):1 for r in rows for h in r['options']}
        baseline=self.rule._greedy(rows,travel,free)
        clock=ControlledClock()
        with patch.object(new.time,'perf_counter',clock):
            plan,trace=self.rule._initialize(rows,travel,free,baseline,100.,100.05)
        self.assertEqual(plan,baseline);self.assertEqual(trace['evaluated_candidates'],128)
        self.assertEqual(trace['stop_reason'],'candidate_limit')
        self.assertTrue(all(not c['feasible'] for c in trace['candidates']))

    def test_zero_budget_keeps_original_greedy_and_no_proof(self):
        rule=new.StableSeededCPSATAssignmentRule(time_limit_seconds=0)
        with patch.object(new,'_load_cp_model',side_effect=AssertionError('unused')):
            plan=rule.decide_batch(self.world,self.ready,0)
        self.assertEqual(plan,{'P02':'NEAR','P03':None})
        record=rule.records[-1]
        self.assertFalse(record['optimal_proven']);self.assertEqual(record['stages'],[])
        self.assertEqual(record['incumbent']['stop_reason'],'not_started_zero_budget')
        self.assertEqual(record['stability']['rule'],TIE_RULE)

    def test_missing_backend_never_runs_initializer(self):
        with patch.object(new,'_load_cp_model',side_effect=CPSATDependencyError('unit missing')):
            plan=self.rule.decide_batch(self.world,self.ready,0)
        self.assertEqual(plan,{'P02':'NEAR','P03':None})
        self.assertEqual(self.rule.records[-1]['incumbent']['stop_reason'],'not_started_missing_backend')

    def test_required_backend_failure_creates_no_partial_record(self):
        rule=new.StableSeededCPSATAssignmentRule(require_backend=True)
        with patch.object(new,'_load_cp_model',side_effect=CPSATDependencyError('unit missing')):
            with self.assertRaises(CPSATDependencyError):rule.decide_batch(self.world,self.ready,0)
        self.assertEqual(rule.records,[])

    def test_empty_batch_reads_no_world(self):
        self.assertEqual(self.rule.decide_batch(SimpleNamespace(),[],0),{})
        self.assertEqual(self.rule.records,[])

    def test_controlled_clock_fault_model_overrun_retains_the_prepared_augment(self):
        """Fault test only: this artificial model pause is never a formal run."""
        cp=_load_cp_model();original_model=cp.CpModel;clock=ControlledClock()
        def delayed_model():
            clock.t += .06
            return original_model()
        rule=new.StableSeededCPSATAssignmentRule(require_backend=True)
        with patch.object(new,'_load_cp_model',return_value=cp),patch.object(new.time,'perf_counter',clock),\
                patch.object(cp,'CpModel',side_effect=delayed_model),\
                patch.object(rule,'_solve_stage',side_effect=AssertionError('no second budget')):
            plan=rule.decide_batch(self.world,self.ready,0)
        self.assertEqual(plan,{'P02':'FAR','P03':'NEAR'})
        record=rule.records[-1]
        self.assertEqual(record['fallback_reason'],'budget_exhausted_before_stage_one')
        self.assertEqual(record['stages'],[]);self.assertFalse(record['optimal_proven'])
        self.assertTrue(record['incumbent']['applied'])
        self.assertEqual(record['c1_incumbent_budget']['stage_requests'],[])
        self.assertEqual(record['c1_incumbent_selection']['selected_source'],'initializer')


if __name__=='__main__':unittest.main()
