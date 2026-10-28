"""Small-seed replay checks for objective-preserving C1 tie stability.

These cases test current-input fairness and actual resource competition. No
reserved development or validation sweep seed is used.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import CPSATDependencyError, _load_cp_model
from simulation.responsive_confirmation_engine import run_responsive_confirmation_replay
from simulation.sim import Policy
from simulation.stable_c1_engine import (
    DEFAULT_STABLE_C1_POLICIES, StableC1Replay, run_stable_c1_replay,
)
from simulation.stable_c1_scenarios import build_stable_c1_scenarios
from simulation.stable_cp_sat_assignment import StableCPSATAssignmentRule


def case(family='no_alternative_hospital', seed=7):
    return build_stable_c1_scenarios(seed, [family])[0]


def policy(branch_id='stable_responsive_C1', **changes):
    spec = deepcopy(next(p for p in DEFAULT_STABLE_C1_POLICIES if p['id'] == branch_id))
    spec.update(changes)
    return spec


def run(scenario, *ids):
    return run_stable_c1_replay(scenario, [policy(i) for i in ids])


def branch(result, name='stable_responsive_C1'):
    return next(b for b in result['branches'] if b['id'] == name)


def events(value, kind, patient=None):
    return [e for e in value['events'] if e['type'] == kind
            and (patient is None or e.get('patient') == patient)]


def arrivals(value):
    return {p['id']: p['final_arrive_t'] for p in value['patient_outcomes']}


def semantic(value):
    """Remove only runtime measurements and new experiment annotations."""
    if isinstance(value, list):
        return [semantic(item) for item in value]
    if isinstance(value, dict):
        return {key: semantic(item) for key, item in value.items()
                if key not in ('comparison_id', 'source_experiment', 'stable_ties_enabled')
                and not key.startswith('stability_')
                and not key.endswith('_seconds')}
    return value


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


class StableC1ConfigurationTests(unittest.TestCase):
    def test_default_seventeen_branches_change_only_explicit_tie_flags(self):
        self.assertEqual(len(DEFAULT_STABLE_C1_POLICIES), 17)
        self.assertEqual(len({p['id'] for p in DEFAULT_STABLE_C1_POLICIES}), 17)
        self.assertEqual(sum(p['stable_ties'] for p in DEFAULT_STABLE_C1_POLICIES), 3)
        for name in ('observed_C1', 'protected_C1', 'responsive_C1'):
            old, new = policy(name), policy('stable_' + name)
            self.assertFalse(old.pop('stable_ties'))
            self.assertTrue(new.pop('stable_ties'))
            for key in ('id', 'label'):
                old.pop(key)
                new.pop(key)
            self.assertEqual(old, new)

    def test_invalid_flags_and_non_c1_stability_are_rejected_before_replay(self):
        scenario = case()
        invalid = (
            {'stable_ties': 1}, {'prefetch': None}, {'watch': 'yes'},
            {'protection': 0}, {'rule': 'H1'}, {'mode': 'unknown'},
            {'grace_minutes': 4}, {'protection': False, 'grace_minutes': None},
            {'observed_inquiry': {}}, {'adaptive_inquiry': {}},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_stable_c1_replay(scenario, [policy(**changes)])
        spec = policy()
        del spec['stable_ties']
        with self.assertRaises(ValueError):
            run_stable_c1_replay(scenario, [spec])

    def test_mismatched_rule_class_and_flag_are_rejected(self):
        scenario = case()
        stable = Policy(rule=StableCPSATAssignmentRule(time_limit_seconds=0),
                        k=3, wait=2, notify=True)
        with self.assertRaisesRegex(ValueError, 'unmodified branch'):
            StableC1Replay(scenario, stable, stable_ties=False)
        old = Policy(rule='C1', k=3, wait=2, notify=True, time_limit_seconds=0)
        with self.assertRaisesRegex(ValueError, 'requires the stable'):
            StableC1Replay(scenario, old, stable_ties=True)

    def test_mismatched_inquiry_settings_and_duplicate_execution_order_are_rejected(self):
        scenario = case()
        with self.assertRaisesRegex(ValueError, 'share inquiry'):
            run_stable_c1_replay(scenario, [policy(), policy('responsive_C1', k=1)])
        specs = [policy(), policy('responsive_C1')]
        with self.assertRaisesRegex(ValueError, 'execution_order'):
            run_stable_c1_replay(scenario, specs,
                                 execution_order=['stable_responsive_C1'] * 2)

    def test_missing_required_backend_is_explicit(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=CPSATDependencyError('test backend unavailable')):
            with self.assertRaises(CPSATDependencyError):
                run_stable_c1_replay(case(), [policy()])

    def test_optional_missing_backend_keeps_validated_fallback_without_proof(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=CPSATDependencyError('test backend unavailable')):
            value = run_stable_c1_replay(case(), [policy(require_backend=False)])['branches'][0]
        self.assertTrue(value['solver_records'])
        self.assertTrue(all(r['fallback_reason'] == 'missing_backend'
                            for r in value['solver_records']))
        self.assertFalse(any(r['optimal_proven'] for r in value['solver_records']))
        self.assertFalse(any(r['stability']['optimality_claimed']
                             for r in value['solver_records']))


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class StableC1ReplayTests(unittest.TestCase):
    def test_target_restores_p01_first_commit_but_retains_real_capacity_wait(self):
        result = run(case(), 'responsive_C1', 'stable_responsive_C1')
        old, new = branch(result, 'responsive_C1'), branch(result)
        self.assertEqual(arrivals(old)['P01'] - arrivals(new)['P01'], 37)
        self.assertEqual(events(new, 'commit', 'P01')[0]['t'], 1)
        self.assertEqual(arrivals(new)['P03'] - arrivals(old)['P03'], 37)
        self.assertEqual(sum(arrivals(new).values()), sum(arrivals(old).values()))
        self.assertEqual(max(arrivals(new).values()), max(arrivals(old).values()))
        self.assertEqual(new['metrics']['not_arrived'], 0)
        self.assertTrue(new['metrics']['stability_tie_replacements'])

    def test_both_stable_confirmation_variants_keep_early_patient_identity(self):
        result = run(case(), 'stable_protected_C1', 'stable_responsive_C1')
        protected, responsive = (branch(result, name) for name in
                                 ('stable_protected_C1', 'stable_responsive_C1'))
        self.assertEqual(events(protected, 'commit', 'P01')[0]['t'], 1)
        self.assertEqual(events(responsive, 'commit', 'P01')[0]['t'], 1)
        self.assertEqual(arrivals(protected)['P01'], arrivals(responsive)['P01'])

    def test_model_records_keep_two_objectives_and_never_prove_episode(self):
        value = branch(run(case('multiple_waiting_patients'), 'stable_responsive_C1'))
        self.assertTrue(value['solver_records'])
        for record in value['solver_records']:
            stable = record['stability']
            self.assertEqual(record['objective'], stable['solver_objective'])
            self.assertEqual(record['assignments'], stable['selected_assignments'])
            self.assertEqual(record['assigned_count'], sum(
                hid is not None for hid in record['assignments'].values()))
            self.assertEqual(record['objective_names'], [
                'unassigned_priority_weight', 'priority_weighted_travel_minutes'])
            self.assertLessEqual(len(record['stages']), 2)
            self.assertFalse(stable['optimality_claimed'])
            if stable['applied']:
                self.assertEqual(stable['baseline_objective'], stable['solver_objective'])
            self.assertGreaterEqual(record['wall_seconds'], stable['elapsed_seconds'])
        self.assertFalse(value['metrics']['whole_episode_optimal_proven'])
        self.assertFalse(any(d['actual_optimal_proven'] for d in value['allocation_decisions']))

    def test_solver_sees_detached_current_view_and_unknowns_stay_out(self):
        original = StableCPSATAssignmentRule.decide_batch
        calls = []

        def checked(rule, view, ready, t):
            self.assertEqual(set(vars(view)), {'hospitals', 'position', 'travel'})
            for patient, offered in ready:
                self.assertEqual(set(vars(patient)), {
                    'id', 'priority', 'demand', 'caps', 't_req', 'loc'})
                self.assertLessEqual(patient.t_req, t)
                self.assertTrue(offered)
            calls.append(t)
            return original(rule, view, ready, t)

        with patch.object(StableCPSATAssignmentRule, 'decide_batch', checked):
            value = branch(run(case('delayed_team_confirmation'), 'stable_responsive_C1'))
        self.assertTrue(calls)
        first = min(e['t'] for e in events(value, 'resource_report_received')
                    if e['source'] == 'confirmation')
        for decision in value['allocation_decisions']:
            if decision['t'] < first:
                self.assertNotIn('P03', {p['id'] for p in decision['inputs']['patients']})
            self.assertTrue(all(r['free'] is not None
                                for r in decision['inputs']['free_resources']))
            for patient in decision['inputs']['patients']:
                for hid in patient['options']:
                    self.assertLessEqual(patient['accepted_at'][hid], decision['t'])
                    self.assertGreater(patient['valid_offer_expiries'][hid], decision['t'])

    def test_all_fourteen_frozen_branches_keep_existing_behavior(self):
        scenario = case()
        original_specs = [deepcopy(p) for p in DEFAULT_STABLE_C1_POLICIES
                          if not p['stable_ties']]
        for spec in original_specs:
            del spec['stable_ties']
        old = run_responsive_confirmation_replay(scenario, original_specs)
        new = run_stable_c1_replay(scenario)
        for value in old['branches']:
            self.assertEqual(semantic(value), semantic(branch(new, value['id'])), value['id'])

    def test_strictly_better_c1_plan_keeps_scarce_option_for_second_patient(self):
        result = run(case('greedy_suboptimal_scarce_option'),
                     'responsive_C1', 'stable_responsive_C1')
        old, new = branch(result, 'responsive_C1'), branch(result)
        self.assertEqual(old['patient_outcomes'], new['patient_outcomes'])
        record = new['solver_records'][0]
        self.assertEqual(record['assignments'], {'P01': 'FAR', 'P02': 'NEAR'})
        self.assertEqual(record['objective'][0], 0)
        self.assertEqual(record['stability']['baseline_objective'][0], 4)
        self.assertEqual(record['stability']['reason'], 'solver_strictly_better')
        self.assertFalse(record['stability']['applied'])

    def test_structural_ties_follow_request_then_patient_then_hospital_identity(self):
        for family, pid, hid, minute in (
            ('tie_older_request_priority', 'P02', 'NEAR', 2),
            ('tie_same_request_identity', 'P01', 'NEAR', 1),
            ('tie_equal_distance_hospital_identity', 'P01', 'A_EQ', 1),
        ):
            with self.subTest(family=family):
                value = branch(run(case(family), 'stable_observed_C1'), 'stable_observed_C1')
                committed = events(value, 'commit', pid)[0]
                self.assertEqual((committed['t'], committed['hospital']), (minute, hid))

    def test_rotation_and_scenario_policy_immutability(self):
        scenario = case('delayed_team_confirmation', seed=8)
        before = deepcopy(scenario)
        specs = [policy('responsive_C1'), policy(), policy('stable_observed_C1')]
        specs_before = deepcopy(specs)
        ids = [p['id'] for p in specs]
        first = run_stable_c1_replay(scenario, specs, execution_order=ids)
        second = run_stable_c1_replay(scenario, specs, execution_order=ids[::-1])
        self.assertEqual(scenario, before)
        self.assertEqual(specs, specs_before)
        for left, right in zip(first['branches'], second['branches']):
            self.assertEqual(semantic(left), semantic(right))


if __name__ == '__main__':
    unittest.main()
