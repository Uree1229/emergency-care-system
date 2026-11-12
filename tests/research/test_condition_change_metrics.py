"""Patient-specific condition exposure, measured without clinical outcome claims."""
import copy
import unittest

from simulation.map_replay import generate_replay
from test_map_replay import scenario


POLICIES = [dict(id='off', label='off', rule='R1', k=1, wait=0, notify=False),
            dict(id='on', label='on', rule='R1', k=1, wait=0, notify=True)]


class ConditionChangeMetricsTests(unittest.TestCase):
    def test_no_change_has_no_condition_exposure_and_same_outcomes(self):
        left, right = generate_replay(scenario(), POLICIES)['branches']
        self.assertEqual(left['patient_outcomes'], right['patient_outcomes'])
        self.assertEqual(left['metrics']['resource_invalid_transit_minutes'], 0)
        self.assertEqual(right['metrics']['resource_condition_affected_patients'], 0)

    def test_persistent_change_counts_only_until_arrival_or_redirection(self):
        value = scenario()
        value['response_environment']['*']['NEAR']['delay'] = 1
        value['exogenous_events'] = [dict(t=12, until=70, type='resource_unavailable',
                                          hospital='NEAR', resource='special_team')]
        off, on = generate_replay(value, POLICIES)['branches']
        for branch in (off, on):
            events = branch['events']
            start = next(e['t'] for e in events if e['type'] == 'resource_condition_risk')
            stop = next(e['t'] for e in events if e['type'] == 'resource_condition_resolved')
            self.assertEqual(branch['metrics']['resource_invalid_transit_minutes'], stop - start)
            self.assertEqual(branch['metrics']['resource_condition_affected_patients'], 1)
        self.assertLess(on['metrics']['resource_invalid_transit_minutes'],
                        off['metrics']['resource_invalid_transit_minutes'])
        self.assertEqual(on['metrics']['transfers'], 0)
        self.assertEqual(off['metrics']['transfers'], 1)

    def test_future_changes_do_not_affect_past_decisions(self):
        value = scenario()
        altered = copy.deepcopy(value)
        altered['exogenous_events'] = [dict(t=30, until=60, type='resource_unavailable',
                                            hospital='NEAR', resource='special_team')]
        base = generate_replay(value, POLICIES)['branches']
        change = generate_replay(altered, POLICIES)['branches']
        for left, right in zip(base, change):
            self.assertEqual([e for e in left['events'] if e['t'] < 30],
                             [e for e in right['events'] if e['t'] < 30])

    def test_short_outage_recovery_stops_exposure_without_alerts(self):
        value = scenario()
        value['response_environment']['*']['NEAR']['delay'] = 1
        value['exogenous_events'] = [dict(t=12, until=14, type='resource_unavailable',
                                          hospital='NEAR', resource='special_team')]
        branch = generate_replay(value, [POLICIES[0]])['branches'][0]
        self.assertEqual(branch['metrics']['resource_invalid_transit_minutes'], 2)
        self.assertEqual(branch['metrics']['transfers'], 0)
        self.assertTrue(all(e['type'] != 'notify' for e in branch['events']))


if __name__ == '__main__':
    unittest.main()
