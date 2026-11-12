"""Behavioral contracts for the new isolated final three-arm comparison."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation import final_assignment_engine_20261028 as engine
from simulation.heuristic_assignment import HeuristicAssignmentRule
from simulation.map_replay import RouteSnapshot
from simulation import sim


def scarcity():
    point=[37.1,127.0]
    common=dict(location=point,occur_t=0,dispatch_t=0,ambulance_origin=point,
        ambulance_arrive_t=0,search_start_t=0,resource_demands={'ed_bed':1},priority=4,treatment_minutes=150)
    return dict(id='final-assignment-synthetic-scarcity-unit',label='Synthetic unit',seed=77,
        synthetic=True,max_minutes=30,hospitals=[
            dict(id='NEAR',name='Near',lat=37.11,lon=127.0,capabilities=['basic','advanced'],capacity={'ed_bed':1}),
            dict(id='FAR',name='Far',lat=37.15,lon=127.0,capabilities=['basic'],capacity={'ed_bed':1})],
        patients=[dict(common,id='A',required_capabilities=['basic']),dict(common,id='B',required_capabilities=['advanced'])],
        exogenous_events=[],response_environment={'*':{'NEAR':{'delay':1},'FAR':{'delay':1}}})


def branch(value,code):return next(row for row in value['branches'] if row['id']==code)
def canonical_clinical(value):
    return [{k:copy.deepcopy(row[k]) for k in ('id','events','patient_outcomes','resource_frames','inquiry_frames') if k in row}
        for row in value['branches']]
def policy(code):return copy.deepcopy(next(row for row in engine.DEFAULT_FINAL_ASSIGNMENT_POLICIES if row['id']==code))


class FinalAssignmentEngineTests(unittest.TestCase):
    def test_scarce_hospital_is_preserved_for_constrained_patient(self):
        value=engine.run_final_assignment_replay(scarcity())
        self.assertEqual([row['id'] for row in value['branches']],['R0','R1','H1'])
        for code in ('R0','R1'):
            self.assertEqual(branch(value,code)['metrics']['unassigned'],1)
        h=branch(value,'H1');self.assertEqual(h['metrics']['unassigned'],0)
        self.assertEqual({e['patient']:e['hospital'] for e in h['events'] if e['type']=='commit' and e['t']==1},
            {'A':'FAR','B':'NEAR'})
        self.assertEqual(h['heuristic_records'][0]['assignments'],h['allocation_decisions'][0]['assignments'])
        self.assertFalse(h['metrics']['whole_episode_optimal_proven'])

    def test_first_reply_and_nearest_choose_different_current_accepted_offers(self):
        value=scarcity();value['patients']=value['patients'][:1]
        value['response_environment']['*'].update(NEAR={'delay':2},FAR={'delay':1})
        result=engine.run_final_assignment_replay(value)
        destinations={row['id']:row['patient_outcomes'][0]['destination_hospital_id'] for row in result['branches']}
        self.assertEqual(destinations,{'R0':'FAR','R1':'NEAR','H1':'NEAR'})
        for row in result['branches']:
            decision=row['allocation_decisions'][0]
            self.assertEqual(set(decision['inputs']['patients'][0]['options']),{'NEAR','FAR'})

    def test_fresh_per_arm_worlds_and_reversed_execution_give_identical_clinical_outcomes(self):
        scenario=scarcity();before=copy.deepcopy(scenario)
        first=engine.run_final_assignment_replay(scenario)
        second=engine.run_final_assignment_replay(scenario,execution_order=['H1','R1','R0'])
        self.assertEqual(canonical_clinical(first),canonical_clinical(second))
        self.assertEqual(scenario,before)
        h1=branch(first,'H1')['heuristic_records'][0]
        h2=branch(second,'H1')['heuristic_records'][0]
        for field in ('objective','assignments','baseline_assignments','evaluated_moves','accepted_moves','search_stop_reason'):
            self.assertEqual(h1[field],h2[field])
        self.assertNotIn(h1['search_stop_reason'],('time_limit','time_limit_zero'))

    def test_source_registry_and_original_functions_are_not_modified(self):
        root=Path(__file__).resolve().parents[2]
        paths=['simulation/sim.py','simulation/map_replay.py','simulation/h1_online_engine.py',
            'simulation/online_allocation_engine.py','simulation/heuristic_assignment.py']
        before={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
        ids={k:id(v) for k,v in sim.RULES.items()}
        method=HeuristicAssignmentRule.decide_batch
        engine.run_final_assignment_replay(scarcity())
        self.assertEqual(before,{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths})
        self.assertEqual(ids,{k:id(v) for k,v in sim.RULES.items()})
        self.assertIs(method,HeuristicAssignmentRule.decide_batch)

    def test_routing_template_and_used_keys_are_not_shared_or_mutated(self):
        routing=RouteSnapshot();routing.route((37.0,127.0),(37.01,127.0))
        before=copy.deepcopy((routing.routes,routing.used_keys))
        result=engine.run_final_assignment_replay(scarcity(),routing=routing)
        self.assertEqual((routing.routes,routing.used_keys),before)
        self.assertFalse(result['routing_summary']['shared_mutable_routing_cache'])
        self.assertEqual(set(result['routing_summary']['by_branch']),{'R0','R1','H1'})
        self.assertEqual(len({b['routing_input_sha256'] for b in result['branches']}),1)

    def test_live_provider_and_unknown_route_types_rejected_before_simulation(self):
        class Poison:
            def route(self,*args):raise AssertionError('network must never be called')
        for routing in (RouteSnapshot(provider=Poison()),Poison()):
            with self.assertRaises(ValueError):engine.run_final_assignment_replay(scarcity(),routing=routing)

    def test_changed_inquiry_or_budget_and_unknown_arm_rejected(self):
        for change in ({'k':1},{'wait':0},{'notify':False},{'k':True},{'priority_order':'all'},
                       {'rule':'C1'},{'id':'OTHER'},{'label':''}):
            values=copy.deepcopy(list(engine.DEFAULT_FINAL_ASSIGNMENT_POLICIES));values[0].update(change)
            with self.assertRaises(ValueError):engine.run_final_assignment_replay(scarcity(),policies=values)
        for change in ({'time_limit_seconds':.05},{'max_moves':2047},{'max_moves':True}):
            values=[policy('H1')];values[0].update(change)
            with self.assertRaises(ValueError):engine.run_final_assignment_replay(scarcity(),policies=values)

    def test_duplicate_or_incomplete_execution_order_and_empty_policy_rejected(self):
        for order in (['H1','R1'],['R0','R1','R1'],['R0','R1','UNKNOWN']):
            with self.assertRaises(ValueError):engine.run_final_assignment_replay(scarcity(),execution_order=order)
        with self.assertRaises(ValueError):engine.run_final_assignment_replay(scarcity(),policies=[])
        with self.assertRaises(ValueError):engine.run_final_assignment_replay(scarcity(),policies=[policy('R0'),policy('R0')])

    def test_h1_safety_exhaustion_is_technical_failure_instead_of_silent_fallback(self):
        original=HeuristicAssignmentRule.decide_batch
        def exhausted(rule,*args):
            result=original(rule,*args);rule.records[-1]['search_stop_reason']='time_limit';return result
        with patch.object(HeuristicAssignmentRule,'decide_batch',exhausted):
            with self.assertRaises(engine.H1SafetyBudgetExhausted):engine.run_final_assignment_replay(scarcity())

    def test_h1_has_no_future_world_access_and_never_loads_solver_backend(self):
        original=HeuristicAssignmentRule.decide_batch;calls=[]
        def checked(rule,view,ready,at):
            self.assertEqual(set(vars(view)),{'hospitals','position','travel'})
            for patient,offers in ready:
                self.assertEqual(set(vars(patient)),{'id','priority','demand','caps','t_req','loc'})
                self.assertTrue(offers);self.assertLessEqual(patient.t_req,at)
            for hospital in view.hospitals.values():self.assertEqual(set(vars(hospital)),{'free'})
            calls.append(at);return original(rule,view,ready,at)
        with patch.object(HeuristicAssignmentRule,'decide_batch',checked),patch('simulation.cp_sat_assignment._load_cp_model',side_effect=AssertionError('H1 must not load solver')):
            engine.run_final_assignment_replay(scarcity())
        self.assertEqual(calls,[1])

    def test_unreceived_offer_cannot_be_selected_in_current_batch(self):
        value=scarcity();value['patients']=value['patients'][:1]
        value['response_environment']['*']['FAR']['delay']=4
        result=engine.run_final_assignment_replay(value)
        for row in result['branches']:
            first=row['allocation_decisions'][0]
            self.assertEqual(first['t'],3)
            self.assertEqual(first['inputs']['patients'][0]['options'],['NEAR'])
            self.assertEqual(first['assignments'],{'A':'NEAR'})

    def test_policy_specs_and_routing_metadata_preserve_fair_fixed_contract(self):
        value=engine.run_final_assignment_replay(scarcity())
        self.assertEqual(value['comparison_id'],engine.EXPERIMENT_ID)
        self.assertEqual(value['original_scenario'],scarcity())
        self.assertEqual(value['input_scenario_sha256'],engine.canonical_sha256(value['original_scenario']))
        for row in value['branches']:
            self.assertEqual(row['comparison_id'],engine.EXPERIMENT_ID)
            self.assertEqual({k:row['policy'][k] for k in ('k','wait','notify')},{'k':3,'wait':2,'notify':True})
            self.assertEqual(row['original_scenario_sha256'],engine.canonical_sha256(scarcity()))
            self.assertFalse(row['execution_contract']['wall_time_added_to_simulation_clock'])
            self.assertTrue(row['execution_contract']['H1_algorithm_source_unchanged'])
            self.assertFalse(row['execution_contract']['H1_previous_selected_50ms_version_reused_exactly'])
        h=branch(value,'H1')
        self.assertEqual(h['policy']['heuristic']['time_limit_seconds'],10.0)
        self.assertEqual(h['policy']['heuristic']['max_moves'],2048)
        self.assertEqual(h['solver_records'],[])

if __name__=='__main__':unittest.main(verbosity=2)
