"""Hospital-specific inquiries must use past feedback and respect workflow gates."""
import copy
import unittest
from unittest.mock import patch

from simulation.map_replay import ReplaySimulation
from simulation.observed_inquiry import plan_inquiries
from simulation.sim import Policy
from test_map_replay import scenario


def observed_policy(**config):
    return Policy(rule='R1', k=3, wait=0, observed_inquiry=config)


def slow_request_scenario():
    value = scenario()
    value['hospitals'] = value['hospitals'][:2]
    value['hospitals'][0]['id'] = 'A'
    value['hospitals'][1].update(id='B', lat=37.156)
    value['response_environment'] = {'*': {'A': {'delay': 30}, 'B': {'delay': 1}}}
    value['timeout_minutes'] = 18
    value['inquiry_queue'] = {'enabled': True, 'workers': 1, 'background_jobs': []}
    return value


class ObservedIntegrationTests(unittest.TestCase):
    def test_waiting_patient_can_query_another_hospital_before_first_deadline(self):
        value = slow_request_scenario()
        observed = ReplaySimulation(copy.deepcopy(value), observed_policy()).run_replay()
        fixed = ReplaySimulation(copy.deepcopy(value), Policy(rule='R1', k=1, wait=0)).run_replay()
        asks = [e for e in observed['events'] if e['type'] == 'ask']
        self.assertEqual(asks[0]['hospital'], 'A')
        hedge = next(e for e in asks if e['hospital'] == 'B')
        self.assertGreaterEqual(hedge['t'], 12)
        self.assertLess(hedge['t'], 28)
        self.assertLess(observed['patient_outcomes'][0]['first_commit_t'],
                        fixed['patient_outcomes'][0]['first_commit_t'])
        self.assertEqual(observed['metrics']['observed_hedges'], 1)

    def test_periodic_reviews_stop_after_commit_but_old_feedback_still_arrives(self):
        value = slow_request_scenario()
        branch = ReplaySimulation(value, observed_policy()).run_replay()
        commit = branch['patient_outcomes'][0]['first_commit_t']
        reviews = [e for e in branch['events'] if e['type'] == 'inquiry_policy']
        self.assertTrue(all(e['t'] < commit for e in reviews))
        feedback = [e for e in branch['events'] if e['type'] == 'inquiry_feedback']
        self.assertTrue(any(e['hospital'] == 'A' and e['t'] == 28
                            and e['outcome'] == 'timeout' for e in feedback))
        self.assertEqual(branch['metrics']['observed_sent'], branch['metrics']['inquiries'])

    def test_each_patient_has_at_most_one_review_per_minute_and_cap_is_enforced(self):
        value = slow_request_scenario()
        branch = ReplaySimulation(value, observed_policy()).run_replay()
        reviews = [e for e in branch['events'] if e['type'] == 'inquiry_policy']
        self.assertEqual(len(reviews), len({(e['t'], e['patient']) for e in reviews}))
        self.assertTrue(all(e['limit'] <= 3 and len(e['send']) <= 1 for e in reviews))
        self.assertTrue(all(e['limit'] == e['open_requests'] + len(e['send']) for e in reviews))
        self.assertTrue(any(not e['send'] for e in reviews))

    def test_future_patients_and_background_work_do_not_change_present_decisions(self):
        before = slow_request_scenario()
        changed = copy.deepcopy(before)
        changed['inquiry_queue']['background_jobs'] = [
            {'hospital': 'A', 't': 50, 'service_minutes': 25}]
        patient = copy.deepcopy(changed['patients'][0])
        patient.update(id='FUTURE', occur_t=60, dispatch_t=61,
                       ambulance_arrive_t=67, search_start_t=70)
        changed['patients'].append(patient)
        a = ReplaySimulation(before, observed_policy()).run_replay()
        b = ReplaySimulation(changed, observed_policy()).run_replay()
        self.assertEqual([e for e in a['events'] if e['t'] <= 40],
                         [e for e in b['events'] if e['t'] <= 40])
        self.assertFalse(any(e.get('patient') == 'FUTURE' and e['type'] in ('inquiry_policy', 'ask')
                             and e['t'] < 70 for e in b['events']))

    def test_retry_wait_is_not_bypassed_by_periodic_review(self):
        value = scenario()
        value['hospitals'] = value['hospitals'][:1]
        value['hospitals'][0]['capacity'] = {'ed_bed': 1, 'special_team': 1}
        value['response_environment'] = {'*': {'NEAR': {'delay': 1}}}
        value['max_minutes'] = 20
        value['exogenous_events'] = [dict(type='external_arrival', t=0, hospital='NEAR',
            demand={'ed_bed': 1, 'special_team': 1}, duration=100)]
        branch = ReplaySimulation(value, observed_policy()).run_replay()
        self.assertEqual([e['t'] for e in branch['events'] if e['type'] == 'ask'], [10, 14, 18])
        self.assertIsNone(branch['patient_outcomes'][0]['first_commit_t'])

    def test_timed_expansion_respects_retry_cooldown(self):
        value = scenario()
        value['hospitals'] = value['hospitals'][:1]
        value['hospitals'][0]['capacity'] = {'ed_bed': 1, 'special_team': 1}
        value['response_environment'] = {'*': {'NEAR': {'delay': 1}}}
        value['max_minutes'] = 20
        value['exogenous_events'] = [dict(type='external_arrival', t=0, hospital='NEAR',
            demand={'ed_bed': 1, 'special_team': 1}, duration=100)]
        policy = Policy(rule='R1', k=3, wait=0, near_minutes=999, expand_after=2,
                        observed_inquiry={})
        branch = ReplaySimulation(value, policy).run_replay()
        self.assertTrue(any(e['type'] == 'expand' and e['t'] == 12 for e in branch['events']))
        self.assertEqual([e['t'] for e in branch['events'] if e['type'] == 'ask'], [10, 14, 18])

    def test_early_review_defers_until_delivered_reply_is_consumed(self):
        value = scenario()
        value['hospitals'] = value['hospitals'][:1]
        value['response_environment'] = {'*': {'NEAR': {'delay': 1}}}

        class EarlyReviewSimulation(ReplaySimulation):
            def before_tick(self, t):
                super().before_tick(t)
                if t == 11:
                    self.send_asks(next(iter(self.patients.values())), t)

        ordinary = ReplaySimulation(copy.deepcopy(value), observed_policy()).run_replay()
        early = EarlyReviewSimulation(value, observed_policy()).run_replay()
        self.assertEqual(early['events'], ordinary['events'])
        self.assertEqual(early['patient_outcomes'], ordinary['patient_outcomes'])
        self.assertEqual(early['patient_outcomes'][0]['first_commit_t'], 11)

    def test_planner_receives_only_projected_feedback_and_known_routes(self):
        value = slow_request_scenario()
        calls = []

        def checked(config, **inputs):
            calls.append(copy.deepcopy(inputs))
            self.assertEqual(set(inputs), {'t', 'candidates', 'observations', 'pending',
                                           'patient_pending', 'max_k'})
            for row in inputs['candidates']:
                self.assertEqual(set(row), {'hospital', 'travel_minutes'})
            for row in inputs['pending']:
                self.assertEqual(set(row), {'submitted_t', 'hospital', 'patient'})
                self.assertLessEqual(row['submitted_t'], inputs['t'])
            for row in inputs['patient_pending']:
                self.assertEqual(set(row), {'submitted_t', 'hospital', 'patient', 'travel_minutes'})
            for row in inputs['observations']:
                self.assertLessEqual(row['observed_t'], inputs['t'])
                self.assertEqual(set(row), {'submitted_t', 'observed_t', 'outcome', 'hospital', 'patient'})
            return plan_inquiries(config, **inputs)

        with patch('simulation.sim.plan_inquiries', side_effect=checked):
            ReplaySimulation(value, observed_policy()).run_replay()
        self.assertGreater(len(calls), 1)
        self.assertTrue(any(c['patient_pending'] for c in calls))

    def test_two_inquiry_controllers_cannot_be_combined(self):
        with self.assertRaises(ValueError):
            Policy(adaptive_inquiry={}, observed_inquiry={})

    def test_adaptive_reference_and_fixed_policies_do_not_gain_periodic_reviews(self):
        value = scenario()
        for policy in (Policy(rule='R1', k=1), Policy(rule='R1', k=5, adaptive_inquiry={'initial_k': 1})):
            branch = ReplaySimulation(copy.deepcopy(value), policy).run_replay()
            self.assertNotIn('observed_decisions', branch['metrics'])
            self.assertFalse(any(e.get('policy_kind') == 'observed' for e in branch['events']))


if __name__ == '__main__':
    unittest.main()
