"""Priority controls reorder only visible work, preserving legacy policy locks."""
import copy
import unittest
from unittest.mock import patch

from simulation.map_replay import ReplaySimulation, generate_replay
from simulation.observed_inquiry import plan_inquiries
from simulation.sim import Policy
from test_map_replay import scenario


def mixed():
    value = scenario()
    value['hospitals'] = value['hospitals'][:1]
    value['hospitals'][0]['capacity'] = {'ed_bed': 1, 'special_team': 1}
    value['response_environment'] = {'*': {'NEAR': {'delay': 2}}}
    value['inquiry_queue'] = {'enabled': True, 'workers': 1, 'background_jobs': []}
    low = copy.deepcopy(value['patients'][0])
    low.update(id='LOW', priority=1, treatment_minutes=200)
    high = copy.deepcopy(low)
    high.update(id='HIGH', priority=4)
    value['patients'] = [low, high]
    value['max_minutes'] = 35
    return value


class PriorityIntegrationTests(unittest.TestCase):
    def test_default_spec_keeps_saved_legacy_shape(self):
        old = Policy(observed_inquiry={}).spec()
        self.assertNotIn('priority_order', old)
        self.assertNotIn('high_priority_hedge_after', old)
        self.assertEqual(Policy(**old).spec(), old)
        new = Policy(priority_order='all', observed_inquiry={}, high_priority_hedge_after=1).spec()
        self.assertEqual(Policy(**new).spec(), new)

    def test_bad_config_is_rejected(self):
        for value in ('later', None, True):
            with self.assertRaises(ValueError):
                Policy(priority_order=value)
        for value in (0, -1, float('nan'), True, '1'):
            with self.assertRaises(ValueError):
                Policy(observed_inquiry={}, high_priority_hedge_after=value)
        with self.assertRaises(ValueError):
            Policy(high_priority_hedge_after=1)

    def test_simultaneous_priority_order_is_input_permutation_invariant(self):
        first = mixed()
        second = copy.deepcopy(first)
        second['patients'].reverse()
        runs = [ReplaySimulation(s, Policy(k=1, priority_order='all')).run_replay()
                for s in (first, second)]
        for result in runs:
            asks = [e for e in result['events'] if e['type'] == 'ask']
            self.assertEqual(asks[0]['patient'], 'HIGH')
            outcomes = {p['id']: p for p in result['patient_outcomes']}
            self.assertIsNotNone(outcomes['HIGH']['treatment_start_t'])
            self.assertIsNone(outcomes['LOW']['treatment_start_t'])
        decision_types = {'request', 'ask', 'accept', 'decline', 'commit', 'inquiry_policy'}
        self.assertEqual([e for e in runs[0]['events'] if e['type'] in decision_types],
                         [e for e in runs[1]['events'] if e['type'] in decision_types])

    def test_default_does_not_reorder_and_later_high_does_not_preempt_fifo(self):
        value = mixed()
        default = ReplaySimulation(copy.deepcopy(value), Policy(k=1)).run_replay()
        self.assertEqual(next(e for e in default['events'] if e['type'] == 'ask')['patient'], 'LOW')
        high = value['patients'][1]
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            high[key] += 1
        ordered = ReplaySimulation(value, Policy(k=1, priority_order='all')).run_replay()
        starts = [e for e in ordered['events'] if e['type'] == 'review_started']
        self.assertEqual([e['patient'] for e in starts[:2]], ['LOW', 'HIGH'])
        outcomes = {p['id']: p for p in ordered['patient_outcomes']}
        self.assertIsNotNone(outcomes['LOW']['first_commit_t'])
        self.assertIsNone(outcomes['HIGH']['first_commit_t'])

    def test_future_high_patient_cannot_change_present_events(self):
        value = mixed()
        value['patients'] = value['patients'][:1]
        changed = copy.deepcopy(value)
        future = copy.deepcopy(changed['patients'][0])
        future.update(id='FUTURE', priority=4, occur_t=30, dispatch_t=31,
                      ambulance_arrive_t=37, search_start_t=40)
        changed['patients'].append(future)
        branches = [ReplaySimulation(s, Policy(k=3, observed_inquiry={}, priority_order='all',
                          high_priority_hedge_after=1)).run_replay() for s in (value, changed)]
        self.assertEqual([e for e in branches[0]['events'] if e['t'] < 30],
                         [e for e in branches[1]['events'] if e['t'] < 30])

    def test_priority_response_order_is_separate_from_request_order(self):
        value = mixed()
        value['inquiry_queue']['workers'] = 2
        branches = {mode: ReplaySimulation(copy.deepcopy(value),
                      Policy(k=1, priority_order=mode)).run_replay()
                    for mode in ('requests', 'all')}
        # Both were submitted highest first and both finish at the same minute.
        # Reordering only submission leaves input-order commitment contention.
        for branch in branches.values():
            asks = [e for e in branch['events'] if e['type'] == 'ask']
            self.assertEqual(asks[0]['patient'], 'HIGH')
        commitments = {mode: next(e for e in b['events'] if e['type'] == 'commit')['patient']
                       for mode, b in branches.items()}
        self.assertEqual(commitments, {'requests': 'LOW', 'all': 'HIGH'})

    def test_high_only_hedge_override_is_per_call_and_config_is_immutable(self):
        value = mixed()
        calls = []
        def checked(config, **inputs):
            calls.append((inputs['patient_pending'], dict(config)))
            return plan_inquiries(config, **inputs)
        policy = Policy(observed_inquiry={}, priority_order='all', high_priority_hedge_after=1)
        original = copy.deepcopy(policy.observed_inquiry)
        with patch('simulation.sim.plan_inquiries', side_effect=checked):
            branch = ReplaySimulation(value, policy).run_replay()
        self.assertEqual(policy.observed_inquiry, original)
        rows = [e for e in branch['events'] if e['type'] == 'inquiry_policy']
        self.assertTrue(all(e['effective_hedge_after'] == (1 if e['patient_priority'] == 4 else 2)
                            for e in rows))
        self.assertEqual({c['hedge_after'] for _, c in calls}, {1, 2})

    def test_joint_priority_rule_runs_through_replay_and_policy_roundtrip(self):
        value = mixed()
        value['inquiry_queue']['workers'] = 2
        spec = Policy(rule='R4', k=1, priority_order='all', observed_inquiry={}).spec()
        branch_spec = dict(spec)
        branch_spec.update(id=branch_spec.pop('name'), label='우선순위 통합 검증')
        branch = generate_replay(value, policies=[branch_spec])['branches'][0]
        commitments = [e for e in branch['events'] if e['type'] == 'commit']
        self.assertEqual([e['patient'] for e in commitments], ['HIGH'])
        self.assertEqual(branch['policy'], spec)
        self.assertEqual(branch['metrics']['joint_fallbacks'], 0)
        self.assertTrue(branch['decision_batches'])

    def test_joint_ready_patient_does_not_send_a_redundant_inquiry_before_commit(self):
        value = scenario()
        value['hospitals'] = value['hospitals'][:2]
        value['response_environment'] = {'*': {h['id']: {'delay': 1} for h in value['hospitals']}}
        value['inquiry_queue'] = {'enabled': True, 'workers': 1, 'background_jobs': []}
        branches = {rule: ReplaySimulation(copy.deepcopy(value),
                    Policy(rule=rule, k=3, priority_order='all', observed_inquiry={})).run_replay()
                    for rule in ('R1', 'R4')}
        for branch in branches.values():
            asks = [e for e in branch['events'] if e['type'] == 'ask']
            self.assertEqual([(e['t'], e['hospital']) for e in asks], [(10, 'NEAR')])
            self.assertEqual(branch['patient_outcomes'][0]['first_commit_t'], 11)

    def test_patient_not_chosen_by_joint_batch_can_continue_searching(self):
        value = mixed()
        far = copy.deepcopy(value['hospitals'][0])
        far.update(id='FAR', lat=37.25)
        value['hospitals'].append(far)
        value['response_environment']['*']['FAR'] = {'delay': 2}
        value['inquiry_queue']['workers'] = 2
        branch = ReplaySimulation(value, Policy(rule='R4', k=3, priority_order='all',
                                  observed_inquiry={})).run_replay()
        asks = [e for e in branch['events'] if e['type'] == 'ask']
        self.assertEqual([(e['patient'], e['hospital']) for e in asks],
                         [('HIGH', 'NEAR'), ('LOW', 'NEAR'), ('LOW', 'FAR')])
        self.assertEqual({p['id']: p['destination_hospital_id'] for p in branch['patient_outcomes']},
                         {'LOW': 'FAR', 'HIGH': 'NEAR'})


if __name__ == '__main__':
    unittest.main()
