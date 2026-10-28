"""Behavioral integration checks for the isolated H1 full-transport replay.

The cases exercise received offers, commitment boundaries, later resource
changes and saved decision evidence. They do not assert a heuristic is optimal
or that its current-batch improvement guarantees a better complete episode.
"""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import CPSATDependencyError, _load_cp_model
from simulation.h1_online_engine import (
    DEFAULT_H1_POLICIES, H1OnlineReplay, run_h1_online_replay,
)
from simulation.heuristic_assignment import HeuristicAssignmentRule


ROOT = Path(__file__).resolve().parent


def scarcity_scenario():
    """One flexible patient and one patient who can use only NEAR.

    The long occupancy outlasts the observation window: a late reuse of NEAR
    cannot hide the first-batch loss made by the sequential comparison rule.
    """
    origin = [37.1, 127.0]
    common = dict(location=origin, occur_t=0, dispatch_t=0,
                  ambulance_origin=origin, ambulance_arrive_t=0,
                  search_start_t=0, resource_demands={'ed_bed': 1},
                  priority=4, treatment_minutes=150)
    return dict(
        id='h1-online-scarcity-unit', label='합성 희소 후보 통합 시험', seed=77,
        synthetic=True, max_minutes=30,
        hospitals=[
            dict(id='NEAR', name='합성 근거리', lat=37.11, lon=127.0,
                 capabilities=['basic', 'advanced'], capacity={'ed_bed': 1}),
            dict(id='FAR', name='합성 원거리', lat=37.15, lon=127.0,
                 capabilities=['basic'], capacity={'ed_bed': 1}),
        ],
        patients=[dict(common, id='A', required_capabilities=['basic']),
                  dict(common, id='B', required_capabilities=['advanced'])],
        exogenous_events=[],
        response_environment={'*': {'NEAR': {'delay': 1}, 'FAR': {'delay': 1}}},
    )


def policy(rule, **kwargs):
    parameters = dict(id=rule, label=rule, rule=rule, k=3, wait=2, notify=True)
    if rule == 'H1':
        parameters.update(time_limit_seconds=.05, max_moves=2048)
    elif rule == 'C1':
        parameters.update(time_limit_seconds=.05, require_backend=True, seed=0)
    parameters.update(kwargs)
    return parameters


def branch(result, code):
    return next(item for item in result['branches'] if item['id'] == code)


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


class H1OnlineEngineTests(unittest.TestCase):
    def test_scarce_candidate_changes_real_commit_and_arrival_flow(self):
        result = run_h1_online_replay(scarcity_scenario(), [policy('R1'), policy('H1')])
        nearest, improved = branch(result, 'R1'), branch(result, 'H1')
        initial = {event['patient']: event['hospital'] for event in improved['events']
                   if event['type'] == 'commit' and event['t'] == 1}
        self.assertEqual(initial, {'A': 'FAR', 'B': 'NEAR'})
        self.assertEqual(nearest['metrics']['unassigned'], 1)
        self.assertEqual(improved['metrics']['unassigned'], 0)
        self.assertEqual(improved['metrics']['not_arrived'], 0)
        self.assertEqual(len(improved['heuristic_records']), 1)
        self.assertEqual(improved['solver_records'], [])
        decision = improved['allocation_decisions'][0]
        record = improved['heuristic_records'][decision['heuristic_record_index']]
        self.assertEqual(record['assignments'], initial)
        self.assertEqual(record['assignments'], decision['assignments'])
        self.assertEqual(record['decision_index'], 0)
        self.assertEqual(record['status'], 'HEURISTIC')
        self.assertEqual(decision['method'], 'heuristic')
        self.assertFalse(record['optimal_proven'])
        self.assertFalse(decision['optimal_proven'])
        self.assertNotIn('solver_status', decision)
        self.assertFalse(improved['metrics']['whole_episode_optimal_proven'])

    def test_zero_optional_search_keeps_feasible_baseline_without_backend(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=AssertionError('H1 must not load OR-Tools')):
            result = run_h1_online_replay(scarcity_scenario(),
                                         [policy('H1', time_limit_seconds=0)])
        value = branch(result, 'H1')
        first = value['heuristic_records'][0]
        self.assertEqual(first['assignments'], {'A': 'NEAR', 'B': None})
        self.assertEqual(first['assignments'], first['baseline_assignments'])
        self.assertEqual(first['objective'], first['baseline_objective'])
        self.assertEqual(first['search_stop_reason'], 'time_limit_zero')
        self.assertEqual(first['evaluated_moves'], 0)
        self.assertFalse(first['optimal_proven'])
        self.assertTrue(all(item['method'] == 'heuristic'
                            for item in value['decision_batches']))

    def test_positive_h1_search_does_not_load_solver_backend(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=AssertionError('H1 must not load OR-Tools')):
            value = branch(run_h1_online_replay(scarcity_scenario(), [policy('H1')]), 'H1')
        self.assertEqual(value['metrics']['unassigned'], 0)

    def test_h1_receives_copied_current_view_without_future_or_mutable_environment(self):
        value = scarcity_scenario()
        future = dict(value['patients'][0], id='FUTURE-POISON', occur_t=25,
                      dispatch_t=25, ambulance_arrive_t=25, search_start_t=25)
        value['patients'].append(future)
        value['hospitals'][0]['capacity']['ed_bed'] = 2
        value['hospitals'][0]['initial_occupancy'] = [
            {'until': 20, 'demand': {'ed_bed': 1}},
        ]
        value['exogenous_events'] = [dict(type='resource_unavailable', t=22,
                                         until=29, hospital='NEAR', resource='ed_bed')]
        original = HeuristicAssignmentRule.decide_batch
        calls = []

        def checked(rule, view, ready, at):
            self.assertEqual(set(vars(view)), {'hospitals', 'position', 'travel'})
            for forbidden in ('scenario', 'patients', 'walkins', 'outages',
                              'pending', 'response_fn', 'response_environment'):
                with self.assertRaises(AttributeError):
                    getattr(view, forbidden)
            for patient, offers in ready:
                self.assertEqual(set(vars(patient)),
                                 {'id', 'priority', 'demand', 'caps', 't_req', 'loc'})
                self.assertLessEqual(patient.t_req, at)
                self.assertTrue(offers)
                if at < 25:
                    self.assertNotEqual(patient.id, 'FUTURE-POISON')
            for hospital in view.hospitals.values():
                self.assertEqual(set(vars(hospital)), {'free'})
                for forbidden in ('active', 'committed', 'queue', 'until'):
                    with self.assertRaises(AttributeError):
                        getattr(hospital, forbidden)
            calls.append((at, [patient.id for patient, _ in ready]))
            return original(rule, view, ready, at)

        with patch.object(HeuristicAssignmentRule, 'decide_batch', checked):
            result = run_h1_online_replay(value, [policy('H1')])
        self.assertEqual(calls[0], (1, ['A', 'B']))
        first = branch(result, 'H1')['allocation_decisions'][0]['inputs']
        encoded = json.dumps(first)
        for forbidden in ('FUTURE-POISON', 'until', 'response_environment', 'silent', 'duration'):
            self.assertNotIn(forbidden, encoded)

    def test_pending_reply_cannot_become_a_current_offer_or_predictive_commit(self):
        value = scarcity_scenario()
        value['patients'] = value['patients'][:1]
        value['response_environment']['*']['FAR']['delay'] = 4
        improved = branch(run_h1_online_replay(value, [policy('H1')]), 'H1')
        outcome = improved['patient_outcomes'][0]
        self.assertEqual(outcome['first_commit_t'], 3)
        decision = improved['allocation_decisions'][0]
        self.assertEqual(decision['t'], 3)
        self.assertEqual(decision['inputs']['patients'][0]['options'], ['NEAR'])
        self.assertEqual(decision['inputs']['patients'][0]['accepted_at'], {'NEAR': 1})
        self.assertEqual(improved['heuristic_records'][0]['edge_count'], 1)

    def test_later_patient_does_not_rewrite_existing_commitment(self):
        value = scarcity_scenario()
        value['patients'][1].update(occur_t=2, dispatch_t=2,
                                    ambulance_arrive_t=2, search_start_t=2)
        improved = branch(run_h1_online_replay(value, [policy('H1')]), 'H1')
        a_commits = [event for event in improved['events']
                     if event['type'] == 'commit' and event.get('patient') == 'A']
        self.assertEqual([(event['t'], event['hospital']) for event in a_commits], [(1, 'NEAR')])
        self.assertFalse(any(event['type'] == 'redirect' for event in improved['events']))
        outcomes = {row['id']: row for row in improved['patient_outcomes']}
        self.assertIsNone(outcomes['B']['first_commit_t'])
        self.assertEqual(improved['heuristic_records'][0]['batch_size'], 1)

    def test_resource_change_redirect_uses_nearest_single_rule_and_separate_record(self):
        value = scarcity_scenario()
        value['patients'] = value['patients'][:1]
        value['patients'][0]['resource_demands'] = {'ed_bed': 1, 'special_team': 1}
        value['hospitals'][0]['lat'] = 37.15
        value['hospitals'][1]['lat'] = 37.20
        value['hospitals'].append(dict(id='FARTHEST', name='합성 추가 대안',
            lat=37.25, lon=127.0, capabilities=['basic'], capacity={'ed_bed': 1}))
        for hospital in value['hospitals']:
            hospital['capacity']['special_team'] = 1
        value['response_environment']['*']['FARTHEST'] = {'delay': 1}
        value['exogenous_events'] = [dict(type='resource_unavailable', t=2,
            until=29, hospital='NEAR', resource='special_team')]
        improved = branch(run_h1_online_replay(value, [policy('H1')]), 'H1')
        redirects = [event for event in improved['events'] if event['type'] == 'redirect']
        self.assertEqual([(event['hospital'], event['previous'])
                          for event in redirects], [('FAR', 'NEAR')])
        # Existing transport semantics retain the first-round tried set and
        # may wait for the shared retry interval before issuing new inquiries.
        self.assertGreater(redirects[0]['t'], 2)
        self.assertTrue(any(event['type'] == 'accept' and event.get('hospital') == 'FAR'
                            and event['t'] == redirects[0]['t']
                            for event in improved['events']))
        self.assertEqual(len(improved['heuristic_records']), 1)
        self.assertEqual(improved['solver_records'], [])
        single = [row for row in improved['allocation_decisions'] if row['kind'] == 'single']
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0]['method'], 'nearest_current_offer')
        self.assertEqual(single[0]['assignments'], {'A': 'FAR'})
        self.assertFalse(single[0]['optimal_proven'])
        self.assertNotIn('heuristic_record_index', single[0])
        self.assertNotIn('solver_record_index', single[0])
        self.assertNotIn('solver_status', single[0])

    def test_source_is_unchanged_and_repeated_runs_get_fresh_records(self):
        value = scarcity_scenario()
        before = copy.deepcopy(value)
        policies = [policy('R1'), policy('H1')]
        policy_before = copy.deepcopy(policies)
        first = run_h1_online_replay(value, policies)
        untouched = copy.deepcopy(branch(first, 'H1'))
        branch(first, 'H1')['heuristic_records'][0]['caller_poison'] = True
        second = run_h1_online_replay(value, policies)
        self.assertEqual(value, before)
        self.assertEqual(policies, policy_before)
        self.assertEqual(branch(second, 'H1')['events'], untouched['events'])
        self.assertEqual(branch(second, 'H1')['patient_outcomes'], untouched['patient_outcomes'])
        records = branch(second, 'H1')['heuristic_records']
        self.assertEqual(len(records), len(untouched['heuristic_records']))
        self.assertTrue(all('caller_poison' not in row for row in records))
        self.assertEqual(branch(second, 'R1')['heuristic_records'], [])
        self.assertEqual(second['source_experiment'], 'online_allocation_comparison')
        self.assertEqual(second['comparison_id'], 'h1_online_20261015')
        json.dumps(second, allow_nan=False, ensure_ascii=False)

    def test_rotated_execution_order_does_not_change_patient_flow_or_output_order(self):
        policies = [policy('R1'), policy('H1')]
        first = run_h1_online_replay(scarcity_scenario(), policies,
                                    execution_order=['R1', 'H1'])
        rotated = run_h1_online_replay(scarcity_scenario(), policies,
                                      execution_order=['H1', 'R1'])
        self.assertEqual([item['id'] for item in rotated['branches']], ['R1', 'H1'])
        self.assertEqual(rotated['execution_order'], ['H1', 'R1'])
        for code in ('R1', 'H1'):
            with self.subTest(rule=code):
                self.assertEqual(branch(first, code)['events'], branch(rotated, code)['events'])
                self.assertEqual(branch(first, code)['patient_outcomes'],
                                 branch(rotated, code)['patient_outcomes'])

    def test_inquiry_factors_cannot_change_silently_between_allocation_branches(self):
        with self.assertRaisesRegex(ValueError, 'identical inquiry'):
            run_h1_online_replay(scarcity_scenario(),
                                 [policy('R1', wait=0), policy('H1', wait=2)])
        with self.assertRaisesRegex(ValueError, 'execution_order'):
            run_h1_online_replay(scarcity_scenario(), [policy('R1'), policy('H1')],
                                 execution_order=['R1', 'R1'])

    def test_configuration_preserves_selected_h1_parameters(self):
        self.assertEqual([item['id'] for item in DEFAULT_H1_POLICIES], ['R1', 'H1', 'C1'])
        self.assertTrue(all((item['k'], item['wait'], item['notify']) == (3, 2, True)
                            for item in DEFAULT_H1_POLICIES))
        configured = next(item for item in DEFAULT_H1_POLICIES if item['id'] == 'H1')
        self.assertEqual(configured['time_limit_seconds'], .05)
        self.assertEqual(configured['max_moves'], 2048)
        result = run_h1_online_replay(scarcity_scenario(), [policy('H1')])
        saved = branch(result, 'H1')['policy']['heuristic']
        self.assertEqual(saved['time_limit_seconds'], .05)
        self.assertEqual(saved['max_moves'], 2048)

    def test_zero_budget_c1_records_do_not_mix_with_heuristic_evidence(self):
        result = run_h1_online_replay(scarcity_scenario(),
            [policy('H1'), policy('C1', time_limit_seconds=0)])
        improved, cp = branch(result, 'H1'), branch(result, 'C1')
        self.assertTrue(improved['heuristic_records'])
        self.assertEqual(improved['solver_records'], [])
        self.assertEqual(cp['heuristic_records'], [])
        self.assertTrue(cp['solver_records'])
        self.assertTrue(all(row['fallback_reason'] == 'time_limit_zero'
                            for row in cp['solver_records']))
        self.assertFalse(cp['metrics']['whole_episode_optimal_proven'])

    def test_historical_locked_sources_are_preserved(self):
        for name in ('online_allocation_lock.json', 'allocation_stress_lock_20261014.json'):
            lock = json.loads((ROOT / 'config' / name).read_text(encoding='utf-8'))
            for relative, expected in lock['source_sha256'].items():
                with self.subTest(lock=name, source=relative):
                    self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected)


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class H1OnlineCPSATTests(unittest.TestCase):
    def test_default_three_branches_share_flow_without_claiming_episode_optimality(self):
        result = run_h1_online_replay(scarcity_scenario())
        self.assertEqual({item['id'] for item in result['branches']}, {'R1', 'H1', 'C1'})
        cp = branch(result, 'C1')
        self.assertTrue(cp['solver_records'])
        self.assertEqual(cp['heuristic_records'], [])
        self.assertEqual(cp['metrics']['unassigned'], 0)
        self.assertTrue(all(not item['metrics']['whole_episode_optimal_proven']
                            for item in result['branches']))
        self.assertTrue(all('heuristic_record_index' not in row
                            for row in cp['allocation_decisions']))


if __name__ == '__main__':
    unittest.main()
