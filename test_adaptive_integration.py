"""Feedback must change inquiry counts without revealing scheduled replies."""
import copy
import unittest
from unittest.mock import patch

from simulation.adaptive_inquiry import decide_inquiry_limit
from simulation.map_replay import ReplaySimulation, generate_replay
from simulation.sim import Policy
from test_map_replay import scenario


def adaptive_policy(**config):
    return Policy(rule='R1', k=5, wait=0, adaptive_inquiry=config)


class AdaptiveIntegrationTests(unittest.TestCase):
    def test_fixed_policy_keeps_existing_dispatch_and_has_no_feedback_events(self):
        value = scenario()
        before = generate_replay(value)
        sim = ReplaySimulation(copy.deepcopy(value), Policy(rule='R0', k=3, wait=0))
        branch = sim.run_replay()
        self.assertEqual(branch['patient_outcomes'][0]['first_commit_t'], 11)
        self.assertEqual(branch['metrics']['inquiries'], 3)
        self.assertEqual(sim.inquiry_observations, [])
        self.assertFalse(any(e['type'].startswith('inquiry_') for e in branch['events']))
        self.assertEqual(before, generate_replay(value))

    def test_feedback_arrives_only_at_delivery_and_continues_after_other_acceptance(self):
        value = scenario()
        sim = ReplaySimulation(value, adaptive_policy(initial_k=3))
        branch = sim.run_replay()
        self.assertEqual(branch['patient_outcomes'][0]['first_commit_t'], 11)
        feedback = [e for e in branch['events'] if e['type'] == 'inquiry_feedback']
        self.assertEqual([(e['hospital'], e['t']) for e in feedback],
                         [('FAR', 11), ('ADVANCED', 12), ('NEAR', 13)])
        self.assertTrue(all(e['t'] == e['observed_t'] >= e['submitted_t'] for e in feedback))
        self.assertEqual(branch['metrics']['adaptive_feedback_received'], 3)
        self.assertEqual(sim._inquiry_feedback_pending, [])

    def test_fast_then_slow_observed_replies_change_dispatch_limit(self):
        value = scenario()
        template = value['patients'][0]
        value['patients'] = []
        for index, search in enumerate((10, 14, 24), 1):
            patient = copy.deepcopy(template)
            patient.update(id=f'P{index}', occur_t=search-10, dispatch_t=search-9,
                           ambulance_arrive_t=search-3, search_start_t=search,
                           resource_demands={'ed_bed': 1}, treatment_minutes=2)
            value['patients'].append(patient)
        for hospital in value['hospitals']:
            hospital['capacity']['ed_bed'] = 10
        value['response_environment'] = {'*': {h['id']: {'delay': 1} for h in value['hospitals']},
            'P2': {h['id']: {'delay': 7} for h in value['hospitals']}}
        sim = ReplaySimulation(value, adaptive_policy(initial_k=1, min_samples=1, slow_minutes=3))
        branch = sim.run_replay()
        decisions = [e for e in branch['events'] if e['type'] == 'inquiry_policy']
        self.assertEqual([(e['patient'], e['limit'], e['reason']) for e in decisions],
            [('P1', 1, 'cold_start'), ('P2', 3, 'fast_replies'), ('P3', 1, 'slow_replies')])
        asks = [e for e in branch['events'] if e['type'] == 'ask']
        self.assertEqual([sum(e['patient'] == pid for e in asks) for pid in ('P1', 'P2', 'P3')],
                         [1, 3, 1])

    def test_controller_boundary_contains_only_received_feedback_and_submission_times(self):
        value = scenario()
        captured = []

        def checked(config, **inputs):
            captured.append(copy.deepcopy(inputs))
            self.assertFalse({'jobs', 'workers', 'queue', 'patients', 'outages'} & inputs.keys())
            for row in inputs['observations']:
                self.assertLessEqual(row['observed_t'], inputs['t'])
                self.assertEqual(set(row), {'submitted_t', 'observed_t', 'outcome', 'patient', 'hospital'})
            for row in inputs['pending']:
                self.assertLessEqual(row['submitted_t'], inputs['t'])
                self.assertEqual(set(row), {'submitted_t', 'patient', 'hospital'})
            return decide_inquiry_limit(config, **inputs)

        with patch('simulation.sim.decide_inquiry_limit', side_effect=checked):
            sim = ReplaySimulation(value, adaptive_policy())
            sim.run_replay()
        self.assertTrue(captured)
        self.assertEqual(captured[0]['observations'], [])
        self.assertEqual(captured[0]['pending'], [])

    def test_future_background_and_future_patients_do_not_change_prior_decisions(self):
        first = scenario()
        first['inquiry_queue'] = {'enabled': True, 'workers': 1, 'background_jobs': []}
        future = copy.deepcopy(first)
        future['inquiry_queue']['background_jobs'] = [
            {'hospital': 'NEAR', 't': 40, 'service_minutes': 30}]
        patient = copy.deepcopy(future['patients'][0])
        patient.update(id='FUTURE', occur_t=50, dispatch_t=51,
                       ambulance_arrive_t=57, search_start_t=60)
        future['patients'].append(patient)
        a = ReplaySimulation(first, adaptive_policy(initial_k=1)).run_replay()
        b = ReplaySimulation(future, adaptive_policy(initial_k=1)).run_replay()
        self.assertEqual([e for e in a['events'] if e['t'] <= 30],
                         [e for e in b['events'] if e['t'] <= 30])

    def test_timeout_feedback_uses_deadline_not_private_service_finish(self):
        value = scenario()
        value['hospitals'] = value['hospitals'][:1]
        value.update(timeout_minutes=3, max_minutes=16)
        value['response_environment'] = {'*': {'NEAR': {'delay': 9}}}
        value['inquiry_queue'] = {'enabled': True, 'workers': 1, 'background_jobs': []}
        sim = ReplaySimulation(value, adaptive_policy(initial_k=1))
        branch = sim.run_replay()
        feedback = [e for e in branch['events'] if e['type'] == 'inquiry_feedback']
        self.assertEqual([(e['t'], e['outcome']) for e in feedback], [(13, 'timeout')])
        self.assertIsNone(branch['patient_outcomes'][0]['first_commit_t'])


if __name__ == '__main__':
    unittest.main()
