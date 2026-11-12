"""Behavioral checks for resource uncertainty, confirmation and commit gates.

Small synthetic cases verify observable workflow boundaries rather than an
expected heuristic win. No live database, network or UI is used.
"""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import CPSATDependencyError, _load_cp_model
from simulation.heuristic_assignment import HeuristicAssignmentRule
from simulation.partial_information_engine import (
    DEFAULT_INFORMATION_POLICIES, PartialInformationReplay, run_information_replay,
)


ROOT = Path(__file__).resolve().parents[2]


def policy(rule='H1', mode='observed', **changes):
    value = copy.deepcopy(next(spec for spec in DEFAULT_INFORMATION_POLICIES
                               if spec['rule'] == rule and spec['mode'] == mode))
    value.update(changes)
    return value


def branch(result, rule='H1', mode='observed'):
    return next(item for item in result['branches']
                if item['information_mode'] == mode and item['policy']['rule'] == rule)


def small_scenario(*, patients=1, capacity=1, maximum=35):
    location = [37.1, 127.0]
    common = dict(location=location, occur_t=0, dispatch_t=0,
        ambulance_origin=location, ambulance_arrive_t=0, search_start_t=0,
        required_capabilities=['basic'], resource_demands={'ed_bed':1},
        priority=4, treatment_minutes=100)
    return dict(id='information-unit',label='합성 관측 품질 경계 시험',synthetic=True,
        seed=77,max_minutes=maximum,
        hospitals=[dict(id='NEAR',name='합성 근거리',lat=37.15,lon=127.0,
                        capabilities=['basic'],capacity={'ed_bed':capacity}),
                   dict(id='FAR',name='합성 대안',lat=37.20,lon=127.0,
                        capabilities=['basic'],capacity={'ed_bed':capacity})],
        patients=[dict(common,id=f'P{i+1}') for i in range(patients)],
        exogenous_events=[],
        response_environment={'*':{'NEAR':dict(delay=1),'FAR':dict(delay=1)}})


def events(value, kind, *, patient=None, hospital=None):
    return [event for event in value['events'] if event['type'] == kind
            and (patient is None or event.get('patient') == patient)
            and (hospital is None or event.get('hospital') == hospital)]


def observation_config(*, initial=None, confirmation_delay=2, **changes):
    config=dict(feed_interval_minutes=0,feed_delay_minutes=0,
        feed_missing_resources=[],confirmation_delay_minutes=confirmation_delay,
        confirmation_silent=False,confirmation_missing_resources=[],initial_reports=[])
    if initial is not None: config['initial_reports']=copy.deepcopy(initial)
    config.update(changes)
    return config


def reports(*, observed_at=0, capacity=1, used=0):
    return [dict(hospital=hid,observed_at=observed_at,
                 resources={'ed_bed':dict(capacity=capacity,used=used)}) for hid in ('NEAR','FAR')]


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


class PartialInformationEngineTests(unittest.TestCase):
    def test_unknown_is_confirmed_separately_after_acceptance_instead_of_guessed_zero(self):
        scenario=small_scenario()
        scenario['observation_config']=observation_config()
        value=branch(run_information_replay(scenario,[policy()]))
        self.assertEqual([e['t'] for e in events(value,'accept')],[1,1])
        commits=events(value,'commit')
        self.assertEqual([(e['t'],e['hospital']) for e in commits],[(3,'NEAR')])
        self.assertEqual(value['metrics']['unassigned'],0)
        self.assertEqual(value['metrics']['not_arrived'],0)
        self.assertTrue(value['allocation_decisions'][0]['resource_evidence'])
        waits=events(value,'information_check')
        unknown=[e for e in waits if e['reason']=='UNKNOWN']
        self.assertTrue(unknown)
        self.assertTrue(all(v['free'] is None for event in unknown for v in event['resources'].values()))
        self.assertTrue(events(value,'resource_confirmation_request'))
        self.assertTrue(any(r['source']=='confirmation' and r['received_at']==3
                            for r in value['observation_records']))

    def test_reply_at_exactly_ten_minutes_is_fresh_but_older_requires_confirmation(self):
        results={}
        for source in (-9,-10):
            scenario=small_scenario()
            scenario['observation_config']=observation_config(initial=reports(observed_at=source),
                                                               confirmation_delay=3)
            results[source]=branch(run_information_replay(scenario,[policy()]))
        self.assertEqual(events(results[-9],'commit')[0]['t'],1)
        self.assertEqual(events(results[-10],'accept')[0]['t'],1)
        self.assertEqual(events(results[-10],'commit')[0]['t'],4)

    def test_fresh_shortage_defers_then_retries_without_terminal_no_compatible(self):
        scenario=small_scenario()
        for hospital in scenario['hospitals']:
            hospital['initial_occupancy']=[dict(until=3,demand={'ed_bed':1})]
        scenario['observation_config']=observation_config(initial=reports(used=1))
        value=branch(run_information_replay(scenario,[policy()]))
        asks=events(value,'ask')
        self.assertTrue(asks)
        self.assertGreater(asks[0]['t'],10)
        self.assertTrue(events(value,'commit'))
        self.assertFalse(events(value,'no_compatible'))
        self.assertEqual(value['metrics']['unassigned'],0)

    def test_optimistic_observation_survives_to_proposal_then_truth_gate_rejects(self):
        scenario=small_scenario()
        scenario['observation_config']=observation_config(initial=reports())
        scenario['response_environment']['*']['FAR']['delay']=4
        scenario['exogenous_events']=[dict(type='resource_unavailable',t=2,until=29,
                                          hospital='NEAR',resource='ed_bed')]
        value=branch(run_information_replay(scenario,[policy()]))
        first=value['allocation_decisions'][0]
        self.assertEqual(first['t'],3)
        self.assertEqual(first['proposed_assignments'],{'P1':'NEAR'})
        self.assertEqual(first['assignments'],{'P1':None})
        self.assertIn('NEAR',first['inputs']['patients'][0]['options'])
        self.assertFalse(events(value,'commit',hospital='NEAR'))
        self.assertEqual(first['gate_records'][0]['reason'],'RESOURCE_CONFLICT')
        self.assertFalse(first['gate_records'][0]['passed'])
        self.assertEqual(first['resource_evidence']['NEAR']['ed_bed']['free'],1)
        self.assertEqual(value['heuristic_records'][0]['assignments'],{'P1':'NEAR'})
        self.assertFalse(first['model_optimal_proven'])
        self.assertFalse(first['actual_optimal_proven'])

    def test_r1_sequential_commits_subtract_current_reservations_immediately(self):
        scenario=small_scenario(patients=2)
        scenario['observation_config']=observation_config()
        value=branch(run_information_replay(scenario,[policy('R1')]),'R1')
        self.assertEqual({e['patient']:e['hospital'] for e in events(value,'accept')
                          if e['t']==1 and e['hospital']=='NEAR'},
                         {'P1':'NEAR','P2':'NEAR'})
        commits=events(value,'commit')
        self.assertEqual([(e['t'],e['patient'],e['hospital']) for e in commits],
                         [(3,'P1','NEAR'),(3,'P2','FAR')])
        first_two=value['allocation_decisions'][:2]
        self.assertEqual([row['proposed_assignments'] for row in first_two],
                         [{'P1':'NEAR'},{'P2':'FAR'}])
        self.assertTrue(all(row['assignments']==row['proposed_assignments'] for row in first_two))

    def test_resource_confirmation_does_not_extend_acceptance_ttl(self):
        scenario=small_scenario()
        scenario['observation_config']=observation_config(confirmation_delay=3)
        value=branch(run_information_replay(scenario,[policy()]))
        decision=value['allocation_decisions'][0]
        self.assertEqual(decision['t'],4)
        offered=decision['inputs']['patients'][0]
        self.assertEqual(set(offered['accepted_at'].values()),{1})
        self.assertEqual(set(offered['valid_offer_expiries'].values()),{11})
        self.assertNotIn(14,offered['valid_offer_expiries'].values())

    def test_confirmation_arriving_after_expiry_cannot_resurrect_original_offer(self):
        scenario=small_scenario(maximum=18)
        scenario['observation_config']=observation_config(confirmation_delay=12)
        value=branch(run_information_replay(scenario,[policy()]))
        self.assertEqual([e['t'] for e in events(value,'expire')],[11,11])
        self.assertEqual(events(value,'commit')[0]['t'],15)
        self.assertTrue(value['allocation_decisions'])
        for decision in value['allocation_decisions']:
            for patient in decision['inputs']['patients']:
                for hospital in patient['options']:
                    self.assertGreater(patient['valid_offer_expiries'][hospital],decision['t'])
                    self.assertGreater(patient['accepted_at'][hospital],1)

    def test_unconfirmed_second_required_resource_blocks_allocation_despite_acceptance(self):
        scenario=small_scenario(maximum=18)
        scenario['patients'][0]['resource_demands']['special_team']=1
        for hospital in scenario['hospitals']: hospital['capacity']['special_team']=1
        scenario['observation_config']=observation_config(initial=reports(),
            confirmation_missing_resources=['special_team'])
        value=branch(run_information_replay(scenario,[policy()]))
        self.assertTrue(events(value,'accept'))
        self.assertTrue(events(value,'resource_confirmation_request'))
        self.assertFalse(value['allocation_decisions'])
        self.assertFalse(events(value,'commit'))
        self.assertEqual(value['metrics']['unassigned'],1)
        checks=[e for e in events(value,'information_check') if e['purpose']=='allocation']
        self.assertTrue(checks)
        self.assertTrue(all(e['resources']['special_team']['free'] is None for e in checks))
        self.assertTrue(all(e['resources']['special_team']['status']=='unknown' for e in checks))

    def test_silent_resource_confirmation_times_out_without_guessing_available_capacity(self):
        scenario=small_scenario(maximum=12)
        scenario['observation_config']=observation_config(confirmation_silent=True)
        value=branch(run_information_replay(scenario,[policy()]))
        self.assertTrue(events(value,'accept'))
        self.assertTrue(events(value,'resource_confirmation_timeout'))
        self.assertFalse(value['observation_records'])
        self.assertFalse(value['allocation_decisions'])
        self.assertFalse(events(value,'commit'))
        self.assertEqual(value['metrics']['unassigned'],1)

    def test_late_old_feed_packet_cannot_overwrite_a_newer_shortage(self):
        scenario=small_scenario(maximum=15)
        scenario['patients'][0]['search_start_t']=6
        scenario['observation_config']=observation_config(feed_interval_minutes=1,
            feed_delay_by_source_minute={'0':5})
        scenario['exogenous_events']=[dict(type='resource_unavailable',t=2,until=20,
                                          hospital=hid,resource='ed_bed') for hid in ('NEAR','FAR')]
        value=branch(run_information_replay(scenario,[policy()]))
        delayed=[r for r in value['observation_records']
                 if r['source']=='feed' and r['observed_at']==0 and r['received_at']==5]
        self.assertEqual(len(delayed),2)
        self.assertTrue(all(r['applied'] is False for r in delayed))
        self.assertEqual(value['metrics']['ignored_resource_reports'],2)
        self.assertFalse(events(value,'ask'))
        self.assertFalse(events(value,'commit'))
        self.assertFalse(events(value,'no_compatible'))
        checks=events(value,'information_check')
        self.assertTrue(checks)
        self.assertTrue(all(e['reason']=='FRESH_SHORTAGE' for e in checks))

    def test_single_redirect_gate_rejection_keeps_existing_destination_and_reservation(self):
        scenario=small_scenario(maximum=10)
        scenario['observation_config']=observation_config(initial=reports())
        scenario['exogenous_events']=[dict(type='resource_unavailable',t=2,until=20,
                                          hospital='NEAR',resource='ed_bed')]
        original=PartialInformationReplay._gate
        rejected=[]
        def race(sim,patient,hospital,at,redirect=False):
            if redirect and hospital=='FAR' and not rejected:
                previous=patient.dest
                capacity=sim.hospitals[hospital].cap['ed_bed']
                sim.hospitals[hospital].cap['ed_bed']=0
                try: gate=original(sim,patient,hospital,at,redirect=redirect)
                finally: sim.hospitals[hospital].cap['ed_bed']=capacity
                self.assertFalse(gate['passed'])
                self.assertEqual(patient.dest,previous)
                self.assertEqual(patient.state,'TRANSIT')
                self.assertIn(patient.id,sim.hospitals[previous].committed)
                self.assertNotIn(patient.id,sim.hospitals[hospital].committed)
                rejected.append(at)
                return gate
            return original(sim,patient,hospital,at,redirect=redirect)
        with patch.object(PartialInformationReplay,'_gate',race):
            value=branch(run_information_replay(scenario,[policy()]))
        self.assertTrue(rejected,'in-transit retry must reach a new single confirmation')
        singles=[d for d in value['allocation_decisions'] if d['kind']=='single' and d['t']==rejected[0]]
        self.assertEqual(len(singles),1)
        self.assertEqual(singles[0]['proposed_assignments'],{'P1':'FAR'})
        self.assertEqual(singles[0]['assignments'],{'P1':None})
        self.assertFalse(any(e['t']==rejected[0] for e in events(value,'redirect')))

    def test_initial_future_source_is_rejected_before_it_can_reach_policy(self):
        scenario=small_scenario()
        scenario['observation_config']=observation_config(initial=reports(observed_at=1))
        with patch.object(HeuristicAssignmentRule,'decide_batch',
                          side_effect=AssertionError('future source reached policy')):
            with self.assertRaises(ValueError): run_information_replay(scenario,[policy()])

    def test_invalid_initial_reports_are_rejected_in_perfect_and_observed_modes(self):
        for mode in ('perfect','observed'):
            for invalid in ('future','boolean_quantity'):
                scenario=small_scenario()
                initial=reports(observed_at=1 if invalid=='future' else 0)
                if invalid=='boolean_quantity': initial[0]['resources']['ed_bed']['used']=True
                scenario['observation_config']=observation_config(initial=initial)
                with self.subTest(mode=mode,invalid=invalid),self.assertRaises(ValueError):
                    run_information_replay(scenario,[policy(mode=mode)])

    def test_default_comparison_keeps_inquiry_factors_and_search_budgets_equal(self):
        self.assertEqual([spec['id'] for spec in DEFAULT_INFORMATION_POLICIES],
            ['perfect_R1','perfect_H1','perfect_C1','observed_R1','observed_H1','observed_C1'])
        self.assertEqual({(spec['mode'],spec['rule']) for spec in DEFAULT_INFORMATION_POLICIES},
                         {(mode,rule) for mode in ('perfect','observed') for rule in ('R1','H1','C1')})
        for spec in DEFAULT_INFORMATION_POLICIES:
            self.assertEqual((spec['k'],spec['wait'],spec['notify']),(3,2,True))
            if spec['rule'] in ('H1','C1'):
                self.assertEqual(spec['time_limit_seconds'],.05)
            if spec['rule']=='H1': self.assertEqual(spec['max_moves'],2048)

    def test_adaptive_or_observed_inquiry_is_rejected_even_when_both_modes_match(self):
        for field in ('adaptive_inquiry','observed_inquiry'):
            specs=[policy('R1','perfect',**{field:{}}),policy('R1','observed',**{field:{}})]
            with self.subTest(field=field),patch.object(PartialInformationReplay,'run_replay') as replay, \
                    self.assertRaisesRegex(ValueError,'fixed inquiry policy'):
                run_information_replay(small_scenario(),specs)
            replay.assert_not_called()

    def test_hidden_load_priority_and_hedge_settings_cannot_differ_between_modes(self):
        for field,value in (('load_weight',2),('load_window',5),
                            ('priority_order','requests'),('high_priority_hedge_after',4)):
            specs=[policy('R1','perfect'),policy('R1','observed',**{field:value})]
            with self.subTest(field=field),patch.object(PartialInformationReplay,'run_replay') as replay, \
                    self.assertRaisesRegex(ValueError,'share inquiry and notification settings'):
                run_information_replay(small_scenario(),specs)
            replay.assert_not_called()

    def test_h1_never_loads_solver_backend(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=AssertionError('H1 cannot load a solver')):
            result=run_information_replay(small_scenario(),[policy()])
        value=branch(result)
        self.assertFalse(value['metrics']['whole_episode_optimal_proven'])
        self.assertFalse(value['solver_records'])

    def test_policy_receives_only_copied_current_view_without_hidden_future(self):
        scenario=small_scenario()
        scenario['observation_config']=observation_config()
        future=dict(scenario['patients'][0],id='FUTURE-POISON',occur_t=30,dispatch_t=30,
                    ambulance_arrive_t=30,search_start_t=30)
        scenario['patients'].append(future)
        scenario['exogenous_events']=[dict(type='resource_unavailable',t=31,until=40,
                                          hospital='FAR',resource='ed_bed')]
        original=HeuristicAssignmentRule.decide_batch
        calls=[]
        def checked(rule,view,ready,at):
            self.assertEqual(set(vars(view)),{'hospitals','position','travel'})
            for forbidden in ('scenario','patients','walkins','outages','pending','response_environment',
                              'observation_config','feed_delay_by_source_minute'):
                with self.assertRaises(AttributeError): getattr(view,forbidden)
            for patient,offers in ready:
                self.assertEqual(set(vars(patient)),{'id','priority','demand','caps','t_req','loc'})
                self.assertLessEqual(patient.t_req,at)
                if at<30: self.assertNotEqual(patient.id,'FUTURE-POISON')
            for hospital in view.hospitals.values():
                self.assertEqual(set(vars(hospital)),{'free'})
                for forbidden in ('active','committed','queue','until'):
                    with self.assertRaises(AttributeError): getattr(hospital,forbidden)
            calls.append(at)
            return original(rule,view,ready,at)
        with patch.object(HeuristicAssignmentRule,'decide_batch',checked):
            value=branch(run_information_replay(scenario,[policy()]))
        self.assertTrue(calls)
        first=json.dumps(value['allocation_decisions'][0]['inputs'])
        for forbidden in ('FUTURE-POISON','until','silent','response_environment','feed_delay_by_source_minute'):
            self.assertNotIn(forbidden,first)

    def test_original_scenario_policies_and_previous_result_are_isolated(self):
        scenario=small_scenario(); original=copy.deepcopy(scenario)
        specs=[policy('R1'),policy('H1')]; original_specs=copy.deepcopy(specs)
        first=run_information_replay(scenario,specs)
        saved=copy.deepcopy(first)
        first['branches'][0]['events'].append(dict(t=0,type='caller_poison'))
        second=run_information_replay(scenario,specs)
        self.assertEqual(scenario,original); self.assertEqual(specs,original_specs)
        for before,after in zip(saved['branches'],second['branches']):
            self.assertEqual(before['events'],after['events'])
            self.assertEqual(before['patient_outcomes'],after['patient_outcomes'])
        json.dumps(second,allow_nan=False)

    def test_rotating_execution_order_preserves_canonical_output_and_patient_flow(self):
        specs=[policy('R1'),policy('H1')]
        ids=[spec['id'] for spec in specs]
        first=run_information_replay(small_scenario(),specs,execution_order=ids)
        rotated=run_information_replay(small_scenario(),specs,execution_order=ids[::-1])
        self.assertEqual([b['id'] for b in rotated['branches']],ids)
        self.assertEqual(rotated['execution_order'],ids[::-1])
        for before,after in zip(first['branches'],rotated['branches']):
            self.assertEqual(before['events'],after['events'])
            self.assertEqual(before['patient_outcomes'],after['patient_outcomes'])

    def test_original_nineteen_locked_sources_remain_unchanged(self):
        lock=json.loads((ROOT/'config/h1_online_lock_20261015.json').read_text(encoding='utf-8'))
        self.assertEqual(len(lock['source_sha256']),19)
        for name,expected in lock['source_sha256'].items():
            with self.subTest(source=name):
                self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),expected)


@unittest.skipUnless(HAS_CP_SAT,'optional OR-Tools backend is not installed')
class PartialInformationCPSATTests(unittest.TestCase):
    def test_six_default_branches_preserve_separate_evidence_without_episode_optimality(self):
        result=run_information_replay(small_scenario())
        self.assertEqual([b['id'] for b in result['branches']],
                         [spec['id'] for spec in DEFAULT_INFORMATION_POLICIES])
        for value in result['branches']:
            self.assertFalse(value['metrics']['whole_episode_optimal_proven'])
            rule=value['policy']['rule']
            if rule=='H1':
                self.assertFalse(value['solver_records'])
            elif rule=='C1':
                self.assertFalse(value['heuristic_records'])


if __name__=='__main__':
    unittest.main()
