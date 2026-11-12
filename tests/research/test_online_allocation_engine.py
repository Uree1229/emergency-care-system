"""Full-flow and observed-input checks for the online allocation wrapper."""
import copy
import json
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import (CPSATAssignmentRule, CPSATDependencyError,
                                           _load_cp_model)
from simulation.map_replay import ReplaySimulation
from simulation.online_allocation_engine import (DEFAULT_ONLINE_POLICIES,
                                                  OnlineAllocationReplay,
                                                  run_online_replay)
from simulation.sim import Policy


def scarcity_scenario():
    """Flexible A competes with B whose only compatible destination is NEAR."""
    origin = [37.1, 127.0]
    common = dict(location=origin, occur_t=0, dispatch_t=0,
                  ambulance_origin=origin, ambulance_arrive_t=0,
                  search_start_t=0, resource_demands={'ed_bed': 1},
                  priority=4, treatment_minutes=25)
    return dict(
        id='online-scarcity-unit', label='온라인 경합 단위 시험', seed=77,
        synthetic=True, max_minutes=100,
        hospitals=[dict(id='NEAR', name='가상 근거리', lat=37.11, lon=127.0,
                        capabilities=['basic', 'advanced'], capacity={'ed_bed': 1}),
                   dict(id='FAR', name='가상 원거리', lat=37.15, lon=127.0,
                        capabilities=['basic'], capacity={'ed_bed': 1})],
        patients=[dict(common, id='A', required_capabilities=['basic']),
                  dict(common, id='B', required_capabilities=['advanced'])],
        exogenous_events=[],
        response_environment={'*': {'NEAR': {'delay': 1}, 'FAR': {'delay': 1}}},
    )


def policy(rule, **kwargs):
    return dict(id=rule, label=rule, rule=rule, k=3, wait=2, notify=True, **kwargs)


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


class OnlineAllocationTests(unittest.TestCase):
    def test_current_inputs_do_not_include_future_fields_or_future_patient(self):
        value = scarcity_scenario()
        future = dict(value['patients'][0], id='NOT-YET-OCCURRED', occur_t=50,
                      dispatch_t=50, ambulance_arrive_t=50, search_start_t=50)
        value['patients'].append(future)
        value['hospitals'][0]['capacity']['ed_bed'] = 2
        value['hospitals'][0]['initial_occupancy'] = [{'until': 45, 'demand': {'ed_bed': 1}}]
        value['exogenous_events'] = [dict(type='resource_unavailable', t=40, until=70,
                                          hospital='NEAR', resource='ed_bed')]
        branch = run_online_replay(value, [policy('R3')])['branches'][0]
        decision = branch['allocation_decisions'][0]
        self.assertEqual(decision['t'], 1)
        self.assertEqual([row['id'] for row in decision['inputs']['patients']], ['A', 'B'])
        encoded = json.dumps(decision['inputs'])
        for forbidden in ('NOT-YET-OCCURRED', 'until', 'exogenous_events',
                          'response_environment', 'duration', 'asks', 'silent'):
            self.assertNotIn(forbidden, encoded)
        near = next(row for row in decision['inputs']['hospitals'] if row['id'] == 'NEAR')
        self.assertEqual(near['used']['ed_bed'], 1)
        self.assertEqual(near['capacity']['ed_bed'], 2)

    def test_pending_response_does_not_commit_before_wait_or_enter_options(self):
        value = scarcity_scenario()
        value['patients'] = value['patients'][:1]
        value['response_environment']['*']['FAR']['delay'] = 4
        for rule in ('R0', 'R1', 'R3'):
            branch = run_online_replay(value, [policy(rule)])['branches'][0]
            with self.subTest(rule=rule):
                self.assertEqual(branch['patient_outcomes'][0]['first_commit_t'], 3)
                inputs = branch['allocation_decisions'][0]['inputs']
                self.assertEqual(inputs['patients'][0]['options'], ['NEAR'])
                self.assertEqual(inputs['patients'][0]['accepted_at'], {'NEAR': 1})
                self.assertEqual(inputs['patients'][0]['valid_offer_expiries'], {'NEAR': 11})
                self.assertEqual([row['hospital'] for row in inputs['travel_minutes']], ['NEAR'])

    def test_wrapper_preserves_frozen_replay_outputs_for_legacy_rule(self):
        value = scarcity_scenario()
        for rule in ('R0', 'R1', 'R2', 'R3'):
            expected = ReplaySimulation(copy.deepcopy(value), Policy(rule=rule, k=3, wait=2, name=rule)).run_replay()
            actual = run_online_replay(value, [policy(rule)])['branches'][0]
            with self.subTest(rule=rule):
                for field in ('events', 'trajectories', 'patient_outcomes', 'resource_snapshots',
                              'decision_batches', 'inquiry_snapshots', 'resource_risk_snapshots'):
                    self.assertEqual(actual[field], expected[field])
                for key, expected_value in expected['metrics'].items():
                    self.assertEqual(actual['metrics'][key], expected_value)

    def test_snapshot_ledger_and_actual_assignments_are_self_consistent(self):
        branch = run_online_replay(scarcity_scenario(), [policy('R3')])['branches'][0]
        self.assertTrue(branch['allocation_decisions'])
        for decision in branch['allocation_decisions']:
            inputs = decision['inputs']
            ledger = {row['id']: row for row in inputs['hospitals']}
            for resource in inputs['free_resources']:
                h, r = ledger[resource['hospital']], resource['resource']
                self.assertEqual(resource['free'], h['capacity'][r] - h['used'][r]
                                 - h['committed'][r] - h['queued'][r])
            for row in inputs['patients']:
                for hid in row['options']:
                    self.assertLessEqual(row['accepted_at'][hid], decision['t'])
                    self.assertGreater(row['valid_offer_expiries'][hid], decision['t'])
            self.assertEqual(decision['objective']['unassigned_priority_weight'],
                             sum(row['priority'] for row in inputs['patients']
                                 if decision['assignments'][row['id']] is None))
            self.assertGreaterEqual(decision['elapsed_seconds'], 0)

    def test_safe_fallback_is_explicit_not_enumeration_or_optimal(self):
        branch = run_online_replay(scarcity_scenario(),
                                   [policy('C1', time_limit_seconds=0)])['branches'][0]
        self.assertTrue(branch['solver_records'])
        self.assertTrue(all(row['method'] == 'cp_sat_fallback' for row in branch['decision_batches']))
        self.assertTrue(all(not row['optimal_proven'] for row in branch['allocation_decisions']))
        self.assertEqual(branch['metrics']['solver_calls'], branch['metrics']['solver_fallbacks'])
        self.assertTrue(all(row['fallback_reason'] == 'time_limit_zero' for row in branch['solver_records']))

    def test_source_unchanged_standard_viewer_schema_and_finite_json(self):
        value = scarcity_scenario()
        before = copy.deepcopy(value)
        result = run_online_replay(value, [policy('R3')])
        self.assertEqual(value, before)
        self.assertEqual(result['schema_version'], 2)
        self.assertTrue(result['scenario']['online_allocation_comparison'])
        self.assertEqual(result['source_experiment'], 'online_allocation_comparison')
        self.assertIn('duration_minutes', result)
        self.assertEqual(result['routing_summary']['provider'], 'synthetic')
        self.assertFalse(result['branches'][0]['metrics']['whole_episode_optimal_proven'])
        json.dumps(result, allow_nan=False, ensure_ascii=False)

    def test_default_policy_factors_equal_and_solver_config_explicit(self):
        self.assertEqual([row['id'] for row in DEFAULT_ONLINE_POLICIES], ['R0', 'R1', 'R2', 'R3', 'C1'])
        self.assertTrue(all((row['k'], row['wait'], row['notify']) == (3, 2, True)
                            for row in DEFAULT_ONLINE_POLICIES))
        c1 = DEFAULT_ONLINE_POLICIES[-1]
        self.assertEqual(c1['time_limit_seconds'], 1)
        self.assertTrue(c1['require_backend'])

    def test_duplicate_branch_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate online branch ID'):
            run_online_replay(scarcity_scenario(), [policy('R3'), policy('R3')])


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class OnlineCPSATTests(unittest.TestCase):
    def test_joint_scarcity_changes_real_flow_and_one_batch_is_proven(self):
        result = run_online_replay(scarcity_scenario())
        branches = {row['id']: row for row in result['branches']}
        cp, nearest = branches['C1'], branches['R1']
        outcomes = {row['id']: row for row in cp['patient_outcomes']}
        other = {row['id']: row for row in nearest['patient_outcomes']}
        self.assertEqual(outcomes['A']['destination_hospital_id'], 'FAR')
        self.assertEqual(outcomes['B']['destination_hospital_id'], 'NEAR')
        self.assertEqual(outcomes['A']['first_commit_t'], 1)
        self.assertEqual(outcomes['B']['first_commit_t'], 1)
        self.assertLess(outcomes['B']['treatment_start_t'], other['B']['treatment_start_t'])
        self.assertEqual(len(cp['solver_records']), 1)
        record = cp['solver_records'][0]
        self.assertEqual(record['status'], 'OPTIMAL')
        self.assertTrue(record['optimal_proven'])
        self.assertEqual(len(record['stages']), 2)
        self.assertTrue(all(row['status'] == 'OPTIMAL' for row in record['stages']))
        decision = cp['allocation_decisions'][0]
        self.assertEqual(decision['solver_record_index'], 0)
        self.assertEqual(record['decision_index'], 0)
        self.assertEqual(record['assignments'], decision['assignments'])
        self.assertEqual(record['objective'], list(decision['objective'].values()))
        self.assertEqual(cp['decision_batches'][0]['method'], 'cp_sat')
        self.assertNotIn('exact_enumeration', json.dumps(cp['decision_batches']))
        self.assertFalse(cp['metrics']['whole_episode_optimal_proven'])
        self.assertEqual(cp['policy']['solver']['time_limit_seconds'], 1.0)

    def test_cp_solver_receives_only_copied_current_view(self):
        original = CPSATAssignmentRule.decide_batch
        calls = []

        def checked(rule, view, ready, t):
            self.assertEqual(set(vars(view)), {'hospitals', 'position', 'travel'})
            for patient, options in ready:
                self.assertEqual(set(vars(patient)), {'id', 'priority', 'demand', 'caps', 't_req', 'loc'})
                self.assertTrue(options)
            for hospital in view.hospitals.values():
                self.assertEqual(set(vars(hospital)), {'free'})
            calls.append(t)
            return original(rule, view, ready, t)

        with patch.object(CPSATAssignmentRule, 'decide_batch', checked):
            run_online_replay(scarcity_scenario(), [policy('C1', require_backend=True)])
        self.assertEqual(calls, [1])

    def test_c1_pending_response_does_not_enter_current_solver_batch(self):
        value = scarcity_scenario()
        value['patients'] = value['patients'][:1]
        value['response_environment']['*']['FAR']['delay'] = 4
        branch = run_online_replay(value, [policy('C1', require_backend=True)])['branches'][0]
        self.assertEqual(branch['patient_outcomes'][0]['first_commit_t'], 3)
        record = branch['solver_records'][0]
        self.assertEqual(record['t'], 3)
        self.assertEqual(record['edge_count'], 1)
        self.assertEqual(record['assignments'], {'A': 'NEAR'})


if __name__ == '__main__':
    unittest.main()
