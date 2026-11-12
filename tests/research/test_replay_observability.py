"""Queue capacity regression and resource traces for multi-patient playback."""
import copy
import unittest

from simulation.map_replay import generate_replay
from simulation.multi_scenarios import build_multi_scenarios
from simulation.sim import Hospital, JointAssignmentRule, Patient
from test_map_replay import scenario


class ResourceAccountingTests(unittest.TestCase):
    def test_arrival_queue_keeps_slot_until_treatment_starts(self):
        hospital = Hospital('H', (37.1, 127), {'basic'}, {'ed_bed': 1})
        arrived = Patient('ARRIVED', 0, hospital.loc, {'basic'}, {'ed_bed': 1}, 4, 20)
        newcomer = Patient('NEW', 0, hospital.loc, {'basic'}, {'ed_bed': 1}, 4, 20)
        hospital.queue.append(arrived)
        self.assertEqual(hospital.free('ed_bed'), 0)
        self.assertFalse(hospital.can_accept(newcomer))
        self.assertTrue(hospital.fits_now(arrived.demand))
        hospital.queue.remove(arrived)
        hospital.active.append([20, arrived.demand])
        self.assertEqual(hospital.free('ed_bed'), 0)
        hospital.active.clear()
        self.assertTrue(hospital.can_accept(newcomer))

    def test_joint_assignment_cannot_reuse_an_arrived_patients_slot(self):
        hospital = Hospital('H', (37.1, 127), set(), {'ed_bed': 1})
        arrived = Patient('A', 0, hospital.loc, set(), {'ed_bed': 1}, 4, 20)
        waiting = Patient('B', 0, hospital.loc, set(), {'ed_bed': 1}, 4, 20)
        hospital.queue.append(arrived)
        class Engine:
            hospitals = {'H': hospital}
            def travel(self, p, hid, loc): return 1
            def position(self, p, t): return p.loc
        self.assertEqual(JointAssignmentRule().decide_batch(Engine(), [(waiting, ['H'])], 1), {'B': None})

    def test_resource_frames_record_initial_occupancy_release_and_treatment(self):
        value = scenario()
        value['hospitals'][0]['initial_occupancy'] = [{'until': 2, 'demand': {'ed_bed': 1}}]
        branch = generate_replay(value, [dict(id='r', label='r', rule='R1', k=1, wait=0)])['branches'][0]
        frames = branch['resource_snapshots']
        first = next(h for h in frames[0]['hospitals'] if h['id'] == 'NEAR')
        self.assertEqual(first['used']['ed_bed'], 1)
        released = next(frame for frame in frames if frame['t'] == 2)
        self.assertEqual(next(h for h in released['hospitals'] if h['id'] == 'NEAR')['used']['ed_bed'], 0)
        start = branch['patient_outcomes'][0]['treatment_start_t']
        at_start = next(frame for frame in frames if frame['t'] == start)
        self.assertEqual(next(h for h in at_start['hospitals'] if h['id'] == 'NEAR')['used']['special_team'], 1)
        self.assertTrue(all(a['hospitals'] != b['hospitals'] for a, b in zip(frames, frames[1:])))

    def test_all_multi_policy_frames_respect_capacity_and_use_local_batches(self):
        base = scenario()
        for value in build_multi_scenarios(base):
            data = generate_replay(copy.deepcopy(value))
            self.assertEqual(len(data['branches']), 4)
            for branch in data['branches']:
                for frame in branch['resource_snapshots']:
                    for h in frame['hospitals']:
                        for r, capacity in h['capacity'].items():
                            self.assertLessEqual(h['used'][r] + h['committed'][r] + h['queued'][r], capacity)
                for batch in branch['decision_batches']:
                    self.assertEqual(batch['scope'], 'same_minute_ready_patients_only')
                    self.assertTrue(all(next(p for p in value['patients'] if p['id'] == pid)['search_start_t'] <= batch['t']
                                        for pid in batch['patients']))


if __name__ == '__main__':
    unittest.main()
