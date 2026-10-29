"""Behavior checks for bounded synchronization around unknown scarce resources.

The tests use small synthetic seeds and actual commitment/arrival events. They
do not require every patient to benefit or claim complete-episode optimality.
"""
import copy
import json
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import CPSATDependencyError, _load_cp_model
from simulation.heuristic_assignment import HeuristicAssignmentRule
from simulation.opportunity_engine import (
    DEFAULT_OPPORTUNITY_POLICIES, run_opportunity_replay,
)
from simulation.partial_information_scenarios import build_information_scenarios


def policy(branch_id='protected_H1', **changes):
    spec = copy.deepcopy(next(value for value in DEFAULT_OPPORTUNITY_POLICIES
                              if value['id'] == branch_id))
    spec.update(changes)
    return spec


def branch(result, branch_id='protected_H1'):
    return next(value for value in result['branches'] if value['id'] == branch_id)


def events(value, kind, patient=None):
    return [event for event in value['events'] if event['type'] == kind
            and (patient is None or event.get('patient') == patient)]


def outcomes(value):
    return {row['id']: row for row in value['patient_outcomes']}


def scarcity_scenario(*, confirmation_delay=1, silent=False, maximum=30):
    """A has an alternative; B needs an unconfirmed team at NEAR only."""
    origin = [37.1, 127.0]
    common = dict(location=origin, occur_t=0, dispatch_t=0,
        ambulance_origin=origin, ambulance_arrive_t=0, search_start_t=0,
        priority=4, treatment_minutes=150)
    hospitals = [
        dict(id='NEAR', name='합성 근거리', lat=37.11, lon=127.0,
             capabilities=['basic', 'advanced'], capacity={'ed_bed':1,'special_team':1}),
        dict(id='FAR', name='합성 대안', lat=37.15, lon=127.0,
             capabilities=['basic'], capacity={'ed_bed':1,'special_team':1}),
    ]
    return dict(id='opportunity-unit', label='합성 희소 자원 확인 대기', seed=7,
        synthetic=True, max_minutes=maximum, accept_ttl_minutes=10,
        patients=[dict(common,id='A',required_capabilities=['basic'],
                       resource_demands={'ed_bed':1}),
                  dict(common,id='B',required_capabilities=['advanced'],
                       resource_demands={'ed_bed':1,'special_team':1})],
        hospitals=hospitals, exogenous_events=[],
        response_environment={'*':{'NEAR':dict(delay=1),'FAR':dict(delay=1)}},
        observation_config=dict(feed_interval_minutes=0,feed_delay_minutes=0,
            feed_missing_resources=[],confirmation_delay_minutes=confirmation_delay,
            confirmation_silent=silent,confirmation_missing_resources=[],
            initial_reports=[dict(hospital=h['id'],observed_at=0,
                resources={'ed_bed':dict(capacity=1,used=0),'special_team':None})
                for h in hospitals]))


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


class OpportunityEngineTests(unittest.TestCase):
    def test_wait_then_joint_batch_preserves_scarce_option_and_actual_arrival(self):
        result=run_opportunity_replay(scarcity_scenario(),
            [policy('observed_H1'),policy()])
        baseline,protected=branch(result,'observed_H1'),branch(result)
        self.assertEqual([(e['t'],e['patient'],e['hospital']) for e in events(baseline,'commit')],
                         [(1,'A','NEAR')])
        self.assertEqual({e['patient']:e['hospital'] for e in events(protected,'commit')},
                         {'A':'FAR','B':'NEAR'})
        self.assertEqual({e['t'] for e in events(protected,'commit')},{2})
        self.assertIsNotNone(outcomes(protected)['B']['final_arrive_t'])
        self.assertIsNone(outcomes(baseline)['B']['final_arrive_t'])
        self.assertEqual(protected['metrics']['unassigned'],0)
        self.assertFalse(protected['metrics']['whole_episode_optimal_proven'])

    def test_original_partial_team_missing_family_improves_actual_scarce_patient(self):
        scenario=build_information_scenarios(7,['partial_team_missing'])[0]
        result=run_opportunity_replay(scenario,[policy('observed_H1'),policy()])
        before,after=outcomes(branch(result,'observed_H1')),outcomes(branch(result))
        self.assertLess(after['P03']['final_arrive_t'],before['P03']['final_arrive_t'])
        self.assertEqual(after['P03']['destination_hospital_id'],'NEAR')
        self.assertEqual(after['P02']['destination_hospital_id'],'FAR')
        self.assertEqual({row['id'] for row in after.values()},{'P01','P02','P03'})

    def test_unknown_team_remains_excluded_until_separate_resource_confirmation(self):
        value=branch(run_opportunity_replay(scarcity_scenario(confirmation_delay=2),[policy()]))
        confirmations=[e['t'] for e in events(value,'resource_report_received')
                       if e['source']=='confirmation']
        self.assertTrue(confirmations)
        first_confirmation=min(confirmations)
        for decision in value['allocation_decisions']:
            ids={row['id'] for row in decision['inputs']['patients']}
            if decision['t']<first_confirmation: self.assertNotIn('B',ids)
            for resource in decision['inputs']['free_resources']:
                self.assertIsNotNone(resource['free'])
        self.assertGreaterEqual(events(value,'commit','B')[0]['t'],first_confirmation)
        self.assertTrue(events(value,'information_wait','B'))

    def test_stale_team_and_fresh_bed_synchronize_without_acceptance_refreshing_counts(self):
        scenario=scarcity_scenario()
        scenario['observation_config']['initial_reports']=[report
            for hid in ('NEAR','FAR')
            for report in (
                dict(hospital=hid,observed_at=-11,
                     resources={'special_team':dict(capacity=1,used=0)}),
                dict(hospital=hid,observed_at=0,
                     resources={'ed_bed':dict(capacity=1,used=0)}),
            )]
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertEqual({e['patient']:e['hospital'] for e in events(value,'commit')},
                         {'A':'FAR','B':'NEAR'})
        self.assertEqual({e['t'] for e in events(value,'commit')},{2})
        checks=[e for e in events(value,'information_check','B')
                if e['t']==1 and e['purpose']=='allocation']
        self.assertEqual(len(checks),1)
        self.assertEqual(checks[0]['reason'],'STALE')
        self.assertEqual(checks[0]['resources']['special_team']['observed_at'],-11)
        self.assertEqual(checks[0]['resources']['special_team']['status'],'stale')
        self.assertEqual(checks[0]['resources']['ed_bed']['observed_at'],0)
        self.assertEqual(checks[0]['resources']['ed_bed']['status'],'fresh')
        self.assertTrue(events(value,'opportunity_hold','A'))
        first=value['allocation_decisions'][0]
        self.assertEqual(first['t'],2)
        self.assertEqual(first['resource_evidence']['NEAR']['special_team']['observed_at'],2)
        self.assertEqual(first['resource_evidence']['NEAR']['special_team']['source'],'confirmation')
        patient=next(row for row in first['inputs']['patients'] if row['id']=='B')
        self.assertEqual(patient['accepted_at'],{'NEAR':1})
        self.assertEqual(patient['valid_offer_expiries'],{'NEAR':11})

    def test_silent_confirmation_releases_flexible_patient_by_lifetime_deadline(self):
        value=branch(run_opportunity_replay(scarcity_scenario(silent=True,maximum=15),[policy()]))
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','A')],[(4,'NEAR')])
        self.assertFalse(events(value,'commit','B'))
        self.assertEqual(value['metrics']['unassigned'],1)
        self.assertTrue(events(value,'resource_confirmation_timeout'))
        self.assertTrue(all('B' not in {p['id'] for p in d['inputs']['patients']}
                            for d in value['allocation_decisions']))

    def test_waiting_offer_expiry_releases_before_offer_loss_and_retry_delay(self):
        scenario=scarcity_scenario(silent=True,maximum=14)
        scenario['accept_ttl_minutes']=2
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertTrue(events(value,'expire','B'))
        commits=events(value,'commit','A')
        self.assertTrue(commits)
        self.assertEqual(commits[0]['t'],2)
        holds=events(value,'opportunity_hold','A')
        self.assertTrue(holds)
        self.assertEqual({event['waiting_since'] for event in holds},{1})
        self.assertEqual({event['held_since'] for event in holds},{1})
        self.assertTrue(all(event['t']<event['deadline']==2 for event in holds))
        self.assertFalse(events(value,'expire','A'))
        self.assertFalse(events(value,'commit','B'))

    def test_one_minute_offer_cannot_be_held_past_its_last_commit_opportunity(self):
        scenario=scarcity_scenario(silent=True,maximum=14)
        scenario['accept_ttl_minutes']=1
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','A')],[(1,'NEAR')])
        self.assertFalse(events(value,'opportunity_hold','A'))

    def test_ready_patients_earlier_offer_expiry_prevents_hold_independent_of_waiter_expiry(self):
        scenario=scarcity_scenario(silent=True,maximum=14)
        scenario['accept_ttl_minutes']=2
        scenario['timeout_minutes']=2
        third=dict(id='THIRD',name='합성 무응답 대안',lat=37.20,lon=127.0,
                   capabilities=['basic'],capacity={'ed_bed':1,'special_team':1})
        scenario['hospitals'].append(third)
        scenario['observation_config']['initial_reports'].append(dict(
            hospital='THIRD',observed_at=0,
            resources={'ed_bed':dict(capacity=1,used=0),'special_team':None}))
        scenario['response_environment']['A']={'THIRD':dict(silent=True)}
        scenario['response_environment']['B']={'NEAR':dict(delay=2)}
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','A')],[(2,'NEAR')])
        self.assertFalse(events(value,'opportunity_hold','A'))
        snapshots=[record['snapshot'] for record in value['protection_records']
                   if record['snapshot']['t']==2]
        self.assertEqual(len(snapshots),1)
        self.assertEqual(set(snapshots[0]['ready'][0]['valid_offer_expiries'].values()),{3})
        self.assertEqual(snapshots[0]['waiting'][0]['offers'][0]['expires_at'],4)

    def test_staggered_new_waiter_cannot_restart_same_ready_patients_lifetime_budget(self):
        scenario=scarcity_scenario(silent=True,maximum=14)
        later=dict(scenario['patients'][1],id='C',occur_t=2,dispatch_t=2,
                   ambulance_arrive_t=2,search_start_t=2)
        scenario['patients'].append(later)
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','A')],[(4,'NEAR')])
        holds=events(value,'opportunity_hold','A')
        self.assertEqual({event['held_since'] for event in holds},{1})
        self.assertTrue(all(event['t']<event['deadline']<=4 for event in holds))
        at_deadline=[record for record in value['protection_records'] if record['snapshot']['t']==4]
        self.assertEqual(len(at_deadline),1)
        self.assertIn('C',{patient['id'] for patient in at_deadline[0]['snapshot']['waiting']})
        self.assertEqual(at_deadline[0]['held_since']['A'],1)
        self.assertEqual(at_deadline[0]['result']['held'],[])

    def test_higher_priority_ready_patient_bypasses_lower_priority_waiter(self):
        scenario=scarcity_scenario()
        scenario['patients'][1]['priority']=2
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','A')],[(1,'NEAR')])

    def test_equal_structural_options_do_not_create_a_scarcity_hold(self):
        scenario=scarcity_scenario()
        scenario['patients'][0]['required_capabilities']=['advanced']
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','A')],[(1,'NEAR')])

    def test_patient_without_current_acceptance_cannot_hold_another_patients_bed(self):
        scenario=scarcity_scenario()
        scenario['response_environment']['B']={'NEAR':dict(delay=5)}
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','A')],[(1,'NEAR')])

    def test_future_patient_never_preserves_capacity_before_becoming_visible(self):
        scenario=scarcity_scenario()
        scenario['patients'][1].update(occur_t=12,dispatch_t=12,
            ambulance_arrive_t=12,search_start_t=12)
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','A')],[(1,'NEAR')])
        before=[d for d in value['allocation_decisions'] if d['t']<12]
        self.assertTrue(before)
        self.assertTrue(all('B' not in {p['id'] for p in d['inputs']['patients']} for d in before))

    def test_known_spare_bed_capacity_allows_ready_patient_to_commit_immediately(self):
        scenario=scarcity_scenario()
        scenario['hospitals'][0]['capacity']['ed_bed']=2
        scenario['observation_config']['initial_reports'][0]['resources']['ed_bed']['capacity']=2
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','A')],[(1,'NEAR')])
        self.assertEqual([(e['t'],e['hospital']) for e in events(value,'commit','B')],[(2,'NEAR')])

    def test_late_confirmation_cannot_revive_an_expired_original_offer(self):
        scenario=scarcity_scenario(confirmation_delay=12,maximum=20)
        value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertTrue(events(value,'expire','B'))
        self.assertFalse(any(e['patient']=='B' and e['t']<=11 for e in events(value,'commit')))
        for decision in value['allocation_decisions']:
            for patient in decision['inputs']['patients']:
                for hid in patient['options']:
                    self.assertGreater(patient['valid_offer_expiries'][hid],decision['t'])
                    if patient['id']=='B' and decision['t']>=11:
                        self.assertGreater(patient['accepted_at'][hid],1)

    def test_unaffected_known_information_path_keeps_baseline_commitments(self):
        scenario=scarcity_scenario()
        for row in scenario['observation_config']['initial_reports']:
            row['resources']['special_team']=dict(capacity=1,used=0)
        result=run_opportunity_replay(scenario,[policy('observed_H1'),policy()])
        baseline,protected=branch(result,'observed_H1'),branch(result)
        self.assertEqual(baseline['patient_outcomes'],protected['patient_outcomes'])
        strip_sequence=lambda rows:[{k:v for k,v in row.items() if k!='event_sequence'} for row in rows]
        self.assertEqual(strip_sequence(events(baseline,'commit')),
                         strip_sequence(events(protected,'commit')))

    def test_h1_protection_does_not_load_cp_sat_backend(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=AssertionError('H1 cannot load CP-SAT')):
            value=branch(run_opportunity_replay(scarcity_scenario(),[policy()]))
        self.assertFalse(value['solver_records'])
        self.assertTrue(value['heuristic_records'])

    def test_protected_h1_model_receives_only_detached_current_accepted_offers(self):
        scenario=scarcity_scenario()
        future=dict(scenario['patients'][1],id='FUTURE-POISON',occur_t=24,
                    dispatch_t=24,ambulance_arrive_t=24,search_start_t=24)
        scenario['patients'].append(future)
        scenario['exogenous_events']=[dict(type='resource_unavailable',t=25,until=29,
            hospital='FAR',resource='ed_bed')]
        original=HeuristicAssignmentRule.decide_batch
        calls=[]

        def checked(rule,view,ready,at):
            self.assertEqual(set(vars(view)),{'hospitals','position','travel'})
            for forbidden in ('scenario','patients','pending','response_environment',
                              'observation_config','outages','_confirmation_pending'):
                with self.assertRaises(AttributeError): getattr(view,forbidden)
            for patient,offers in ready:
                self.assertEqual(set(vars(patient)),{'id','priority','demand','caps','t_req','loc'})
                self.assertLessEqual(patient.t_req,at)
                self.assertTrue(offers)
                if at<24: self.assertNotEqual(patient.id,'FUTURE-POISON')
            for hospital in view.hospitals.values():
                self.assertEqual(set(vars(hospital)),{'free'})
            calls.append(at)
            return original(rule,view,ready,at)

        with patch.object(HeuristicAssignmentRule,'decide_batch',checked):
            value=branch(run_opportunity_replay(scenario,[policy()]))
        self.assertTrue(calls)
        first=json.dumps(value['allocation_decisions'][0]['inputs'])
        for forbidden in ('FUTURE-POISON','until','silent','response_environment',
                          'confirmation_delay_minutes','receive_at'):
            self.assertNotIn(forbidden,first)

    def test_input_scenario_policies_and_returned_results_are_isolated(self):
        scenario=scarcity_scenario(); frozen=copy.deepcopy(scenario)
        specs=[policy('observed_H1'),policy()]; frozen_specs=copy.deepcopy(specs)
        first=run_opportunity_replay(scenario,specs)
        saved=copy.deepcopy(first)
        first['branches'][0]['events'].append(dict(t=0,type='caller_poison'))
        second=run_opportunity_replay(scenario,specs)
        self.assertEqual(scenario,frozen); self.assertEqual(specs,frozen_specs)
        for before,after in zip(saved['branches'],second['branches']):
            self.assertEqual(before['events'],after['events'])
            self.assertEqual(before['patient_outcomes'],after['patient_outcomes'])
        json.dumps(second,allow_nan=False)

    def test_branch_execution_order_does_not_change_actual_patient_flow(self):
        specs=[policy('observed_H1'),policy()]
        ids=[spec['id'] for spec in specs]
        first=run_opportunity_replay(scarcity_scenario(),specs,execution_order=ids)
        rotated=run_opportunity_replay(scarcity_scenario(),specs,execution_order=ids[::-1])
        self.assertEqual([b['id'] for b in rotated['branches']],ids)
        for before,after in zip(first['branches'],rotated['branches']):
            self.assertEqual(before['events'],after['events'])
            self.assertEqual(before['patient_outcomes'],after['patient_outcomes'])


@unittest.skipUnless(HAS_CP_SAT,'optional OR-Tools backend is not installed')
class OpportunityCPSATTests(unittest.TestCase):
    def test_cp_sat_protection_changes_actual_flow_without_claiming_episode_optimality(self):
        result=run_opportunity_replay(scarcity_scenario(),
            [policy('observed_C1'),policy('protected_C1')])
        baseline,protected=branch(result,'observed_C1'),branch(result,'protected_C1')
        self.assertEqual(baseline['metrics']['unassigned'],1)
        self.assertEqual(protected['metrics']['unassigned'],0)
        self.assertEqual(outcomes(protected)['B']['destination_hospital_id'],'NEAR')
        self.assertFalse(protected['metrics']['whole_episode_optimal_proven'])
        self.assertTrue(protected['solver_records'])
        self.assertFalse(protected['heuristic_records'])

    def test_eight_default_branches_keep_proof_and_heuristic_records_separate(self):
        result=run_opportunity_replay(scarcity_scenario())
        self.assertEqual([b['id'] for b in result['branches']],
            ['perfect_R1','perfect_H1','perfect_C1','observed_R1','observed_H1','observed_C1',
             'protected_H1','protected_C1'])
        for value in result['branches']:
            self.assertFalse(value['metrics']['whole_episode_optimal_proven'])
            if value['policy']['rule']=='H1': self.assertFalse(value['solver_records'])
            elif value['policy']['rule']=='C1': self.assertFalse(value['heuristic_records'])


if __name__=='__main__':
    unittest.main()
