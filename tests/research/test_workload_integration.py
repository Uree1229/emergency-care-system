"""Replay integration checks for inquiry congestion without future knowledge."""
import copy
import unittest

from simulation.map_replay import ReplaySimulation, generate_replay
from simulation.sim import Policy
from test_map_replay import scenario


def queue_scenario(workers=1, patients=1, service=3):
    value = scenario()
    value['hospitals'] = value['hospitals'][:1]
    value['hospitals'][0]['capacity'] = {'ed_bed': patients, 'special_team': patients}
    template = value['patients'][0]
    value['patients'] = []
    for index in range(patients):
        patient = copy.deepcopy(template)
        patient['id'] = f'P{index + 1}'
        value['patients'].append(patient)
    value['response_environment'] = {'*': {'NEAR': {'delay': service}}}
    value['inquiry_queue'] = {'enabled': True, 'workers': workers, 'background_jobs': []}
    value['timeout_minutes'] = 30
    return value


def execute(value, **settings):
    policy = Policy(rule='R1', k=1, wait=0, **settings)
    sim = ReplaySimulation(copy.deepcopy(value), policy)
    return sim, sim.run_replay()


class WorkloadIntegrationTests(unittest.TestCase):
    def test_two_simultaneous_patients_wait_for_the_same_hospital_worker(self):
        sim, branch = execute(queue_scenario(patients=2))
        jobs = [job for job in sim.inquiry_queue.jobs if job['kind'] == 'inquiry']
        self.assertEqual([(job['patient'], job['start_t'], job['finish_t']) for job in jobs],
                         [('P1', 10, 13), ('P2', 13, 16)])
        self.assertEqual([patient['first_commit_t'] for patient in branch['patient_outcomes']], [13, 16])
        self.assertEqual(branch['metrics']['inquiry_queue_wait_mean'], 1.5)
        self.assertEqual(branch['metrics']['inquiry_peak_waiting'], 1)

    def test_second_worker_removes_queue_delay_under_the_same_environment(self):
        one_sim, one = execute(queue_scenario(workers=1, patients=2))
        two_sim, two = execute(queue_scenario(workers=2, patients=2))
        self.assertEqual([p['first_commit_t'] for p in one['patient_outcomes']], [13, 16])
        self.assertEqual([p['first_commit_t'] for p in two['patient_outcomes']], [13, 13])
        self.assertEqual(two['metrics']['inquiry_queue_wait_mean'], 0)
        self.assertEqual(two['metrics']['inquiry_peak_waiting'], 0)
        self.assertEqual(one['metrics']['inquiries'], two['metrics']['inquiries'])
        self.assertEqual(one['metrics']['unassigned'], two['metrics']['unassigned'])
        self.assertEqual(one_sim.scenario['response_environment'], two_sim.scenario['response_environment'])

    def test_completion_exactly_at_deadline_remains_a_valid_acceptance(self):
        value = queue_scenario(service=6)
        value['timeout_minutes'] = 6
        _, branch = execute(value)
        self.assertEqual(branch['patient_outcomes'][0]['first_commit_t'], 16)
        self.assertTrue(any(e['type'] == 'accept' and e['t'] == 16 for e in branch['events']))
        self.assertFalse(any(e['type'] in ('late_reply', 'no_response') for e in branch['events']))

    def test_late_reply_is_logged_but_cannot_accept_or_resurrect_a_request(self):
        value = queue_scenario(service=7)
        value.update(timeout_minutes=6, max_minutes=18)
        _, branch = execute(value)
        events = branch['events']
        self.assertTrue(any(e['type'] == 'no_response' and e['t'] == 16 for e in events))
        late = [e for e in events if e['type'] == 'late_reply']
        self.assertEqual([(e['t'], e['attempt'], e['ignored']) for e in late], [(17, 1, True)])
        self.assertFalse(any(e['type'] in ('accept', 'commit') for e in events))
        self.assertIsNone(branch['patient_outcomes'][0]['first_commit_t'])
        self.assertEqual(branch['metrics']['inquiry_late_replies'], 1)

    def test_silent_review_still_delays_the_next_patients_reply(self):
        value = queue_scenario(patients=2, service=3)
        value['timeout_minutes'] = 6
        value['response_environment']['P1'] = {'NEAR': {'delay': 3, 'silent': True}}
        sim, branch = execute(value)
        second = next(job for job in sim.inquiry_queue.jobs if job['patient'] == 'P2')
        self.assertEqual((second['start_t'], second['finish_t']), (13, 16))
        self.assertEqual(branch['patient_outcomes'][1]['first_commit_t'], 16)
        self.assertTrue(any(e['type'] == 'no_response' and e['patient'] == 'P1' and e['t'] == 16
                            for e in branch['events']))
        self.assertTrue(any(e['type'] == 'review_completed' and e['patient'] == 'P1' and e['t'] == 13
                            for e in branch['events']))

    def test_queue_disabled_preserves_the_legacy_replay_exactly(self):
        value = scenario()
        disabled = copy.deepcopy(value)
        disabled['inquiry_queue'] = {'enabled': False, 'workers': 1,
                                    'background_jobs': [{'hospital': 'NEAR', 't': 0,
                                                         'service_minutes': 100}]}
        baseline = generate_replay(value)['branches']
        actual = generate_replay(disabled)['branches']
        self.assertEqual(baseline, actual)

    def test_recent_load_ranks_only_actual_past_requests_in_its_window(self):
        value = scenario()
        sim = ReplaySimulation(value, Policy(rule='R1', k=1, inquiry_ranking='recent_load',
                                              load_weight=100, load_window=6))
        patient = sim.patients['P']
        candidates = ['NEAR', 'FAR']
        sim.inquiry_history = {'NEAR': [9], 'FAR': [11], 'ADVANCED': []}
        ranked = sim.rank_inquiry_candidates(patient, patient.loc, candidates, 10)
        self.assertEqual(ranked, ['FAR', 'NEAR'])
        # A request at the excluded lower boundary is no longer recent, and a
        # future request is not visible even if a trace accidentally contains it.
        sim.inquiry_history['NEAR'] = [4]
        self.assertEqual(sim.rank_inquiry_candidates(patient, patient.loc, candidates, 10),
                         ['NEAR', 'FAR'])

    def test_future_background_and_not_yet_occurred_patients_do_not_change_present_choices(self):
        value = scenario()
        value['inquiry_queue'] = {'enabled': True, 'workers': 1, 'background_jobs': []}
        changed = copy.deepcopy(value)
        changed['inquiry_queue']['background_jobs'] = [
            {'hospital': 'NEAR', 't': 40, 'service_minutes': 30}]
        future = copy.deepcopy(changed['patients'][0])
        future.update(id='FUTURE', occur_t=50, dispatch_t=51,
                      ambulance_arrive_t=57, search_start_t=60)
        changed['patients'].append(future)
        _, baseline = execute(value, inquiry_ranking='recent_load', load_weight=10)
        _, actual = execute(changed, inquiry_ranking='recent_load', load_weight=10)
        cutoff = 30
        self.assertEqual([e for e in baseline['events'] if e['t'] <= cutoff],
                         [e for e in actual['events'] if e['t'] <= cutoff])
        self.assertEqual([f for f in baseline['inquiry_snapshots'] if f['t'] <= cutoff],
                         [f for f in actual['inquiry_snapshots'] if f['t'] <= cutoff])

    def test_background_jobs_are_sorted_with_stable_equal_time_order_before_patient_requests(self):
        value = queue_scenario(service=1)
        value['inquiry_queue']['background_jobs'] = [
            {'hospital': 'NEAR', 't': 20, 'service_minutes': 1},
            {'hospital': 'NEAR', 't': 10, 'service_minutes': 3},
            {'hospital': 'NEAR', 't': 0, 'service_minutes': 2},
            {'hospital': 'NEAR', 't': 10, 'service_minutes': 1}]
        before = copy.deepcopy(value)
        sim, branch = execute(value)
        background = [j for j in sim.inquiry_queue.jobs if j['kind'] == 'background']
        self.assertEqual([j['submitted_t'] for j in background], [0, 10, 10, 20])
        self.assertEqual([j['service_minutes'] for j in background], [2, 3, 1, 1])
        inquiry = next(j for j in sim.inquiry_queue.jobs if j['kind'] == 'inquiry')
        self.assertEqual((inquiry['submitted_t'], inquiry['start_t'], inquiry['finish_t']), (10, 14, 15))
        self.assertEqual(branch['patient_outcomes'][0]['first_commit_t'], 15)
        self.assertEqual(value, before)

    def test_review_start_and_completion_appear_only_when_their_times_are_reached(self):
        value = queue_scenario(service=3)
        value['inquiry_queue']['background_jobs'] = [
            {'hospital': 'NEAR', 't': 0, 'service_minutes': 20}]
        _, branch = execute(value)
        reviews = [e for e in branch['events'] if e.get('patient') == 'P1'
                   and e['type'] in ('review_started', 'review_completed')]
        self.assertEqual([(e['type'], e['t']) for e in reviews],
                         [('review_started', 20), ('review_completed', 23)])
        at_10 = next(f for f in branch['inquiry_snapshots'] if f['t'] == 10)['hospitals'][0]
        self.assertEqual((at_10['submitted'], at_10['completed'], at_10['waiting'], at_10['processing']),
                         (2, 0, 1, 1))
        self.assertFalse({'start_t', 'finish_t', 'jobs'} & at_10.keys())
        ask = next(e for e in branch['events'] if e['type'] == 'ask')
        self.assertFalse({'start_t', 'finish_t'} & ask.keys())

    def test_other_hospital_acceptance_does_not_cancel_already_submitted_work(self):
        value = scenario()
        value['inquiry_queue'] = {'enabled': True, 'workers': 1, 'background_jobs': []}
        sim = ReplaySimulation(value, Policy(rule='R0', k=2, wait=0))
        branch = sim.run_replay()
        self.assertEqual(branch['patient_outcomes'][0]['destination_hospital_id'], 'FAR')
        self.assertEqual(branch['patient_outcomes'][0]['first_commit_t'], 11)
        self.assertTrue(any(e['type'] == 'review_completed' and e['hospital'] == 'NEAR' and e['t'] == 13
                            for e in branch['events']))
        self.assertFalse(any(e['type'] == 'accept' and e['hospital'] == 'NEAR'
                             for e in branch['events']))
        self.assertEqual(len(sim.inquiry_queue.jobs), 2)


if __name__ == '__main__':
    unittest.main()
