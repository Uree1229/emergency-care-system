"""Small-seed checks of actual whole-patient waiting reduction.

No reserved development or validation seed is used.  The source notification
and the policy's reaction are tested separately, with valid acceptance and
physical scarcity retained throughout.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import CPSATDependencyError, _load_cp_model
from simulation.stable_c1_engine import DEFAULT_STABLE_C1_POLICIES, run_stable_c1_replay
from simulation.stable_cp_sat_assignment import StableCPSATAssignmentRule
from simulation.total_wait_engine import DEFAULT_TOTAL_WAIT_POLICIES, run_total_wait_replay
from simulation.total_wait_scenarios import build_total_wait_scenarios


def case(family='no_alternative_hospital', seed=8):
    return build_total_wait_scenarios(seed, [family])[0]


def policy(name='total_wait_C1', **changes):
    result = deepcopy(next(p for p in DEFAULT_TOTAL_WAIT_POLICIES if p['id'] == name))
    result.update(changes)
    return result


def run(scenario, *ids):
    return run_total_wait_replay(scenario, [policy(name) for name in ids])


def branch(result, name='total_wait_C1'):
    return next(b for b in result['branches'] if b['id'] == name)


def events(value, kind, patient=None):
    return [e for e in value['events'] if e['type'] == kind
            and (patient is None or e.get('patient') == patient)]


def patients(value):
    return {p['id']: p for p in value['patient_outcomes']}


def total(value, field):
    end = value['metrics']['observation_end_t']
    return sum(max(0, min(end, p[field] if p[field] is not None else end)
                   - p['search_start_t']) for p in value['patient_outcomes'])


def semantic(value):
    if isinstance(value, list):
        return [semantic(item) for item in value]
    if isinstance(value, dict):
        return {key: semantic(item) for key, item in value.items()
                if key not in ('comparison_id', 'source_experiment')
                and not key.endswith('_seconds')}
    return value


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


class TotalWaitConfigurationTests(unittest.TestCase):
    def test_twenty_branches_keep_the_frozen_seventeen_and_three_ablations(self):
        self.assertEqual(len(DEFAULT_TOTAL_WAIT_POLICIES), 20)
        self.assertEqual(len({p['id'] for p in DEFAULT_TOTAL_WAIT_POLICIES}), 20)
        for old, new in zip(DEFAULT_STABLE_C1_POLICIES, DEFAULT_TOTAL_WAIT_POLICIES):
            actual = deepcopy(new)
            self.assertFalse(actual.pop('resource_updates'))
            self.assertFalse(actual.pop('event_review'))
            self.assertEqual(actual, old)

    def test_new_ablations_share_every_original_inquiry_and_allocation_setting(self):
        expected = policy('stable_responsive_C1')
        for key in ('id', 'label', 'resource_updates', 'event_review'):
            expected.pop(key)
        for name, flags in (
            ('review_stable_C1', (False, True)),
            ('updates_stable_C1', (True, False)),
            ('total_wait_C1', (True, True)),
        ):
            actual = policy(name)
            self.assertEqual((actual.pop('resource_updates'), actual.pop('event_review')), flags)
            actual.pop('id')
            actual.pop('label')
            self.assertEqual(actual, expected)

    def test_nonboolean_or_missing_intervention_flags_are_rejected(self):
        for changes in ({'resource_updates': 1}, {'event_review': None}, {'event_review': 'yes'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_total_wait_replay(case(), [policy(**changes)])
        for key in ('resource_updates', 'event_review'):
            spec = policy()
            del spec[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                run_total_wait_replay(case(), [spec])

    def test_interventions_require_observed_stable_c1(self):
        for changes in ({'mode': 'perfect'}, {'stable_ties': False}, {'rule': 'H1'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_total_wait_replay(case(), [policy(**changes)])

    def test_mismatched_inquiry_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            run_total_wait_replay(case(), [policy(), policy('stable_responsive_C1', k=1)])

    def test_duplicate_branch_or_execution_order_is_rejected(self):
        with self.assertRaises(ValueError):
            run_total_wait_replay(case(), [policy(), policy()])
        specs = [policy(), policy('stable_responsive_C1')]
        with self.assertRaises(ValueError):
            run_total_wait_replay(case(), specs, execution_order=['total_wait_C1'] * 2)

    def test_invalid_source_contract_cannot_bypass_missing_fields_or_silence(self):
        for changes in ({'delay_minutes': -1}, {'delay_minutes': True},
                        {'missing_resources': ['special_team']}, {'silent': 0},
                        {'unregistered_field': 1}):
            scenario = case()
            scenario['resource_update_config'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run(scenario, 'total_wait_C1')
        scenario = case('missing_unresolved')
        scenario['resource_update_config']['silent'] = False
        with self.assertRaises(ValueError):
            run(scenario, 'total_wait_C1')
        scenario = case('partial_team_unresolved')
        scenario['resource_update_config']['missing_resources'] = []
        with self.assertRaises(ValueError):
            run(scenario, 'total_wait_C1')

    def test_missing_required_backend_fails_explicitly(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=CPSATDependencyError('test backend unavailable')):
            with self.assertRaises(CPSATDependencyError):
                run(case(), 'total_wait_C1')


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class TotalWaitReplayTests(unittest.TestCase):
    def test_all_seventeen_disabled_branches_retain_frozen_patient_and_event_behavior(self):
        scenario = case(seed=7)
        before = run_stable_c1_replay(scenario)
        after = run_total_wait_replay(scenario,
            [policy(p['id']) for p in DEFAULT_STABLE_C1_POLICIES])
        for left, right in zip(before['branches'], after['branches']):
            with self.subTest(branch=left['id']):
                for key in ('patient_outcomes', 'events', 'allocation_decisions',
                            'observation_records', 'solver_records', 'heuristic_records'):
                    self.assertEqual(semantic(left[key]), semantic(right[key]))

    def test_target_reduces_sum_instead_of_transferring_delay_between_patients(self):
        result = run(case(), 'stable_responsive_C1', 'total_wait_C1')
        old, new = branch(result, 'stable_responsive_C1'), branch(result)
        for field in ('first_commit_t', 'final_arrive_t', 'treatment_start_t'):
            self.assertLess(total(new, field), total(old, field))
        left, right = patients(old), patients(new)
        for pid in ('P01', 'P02'):
            self.assertEqual(left[pid], right[pid])
        self.assertLess(right['P03']['treatment_start_t'], left['P03']['treatment_start_t'])
        self.assertEqual(new['metrics']['not_arrived'], 0)

    def test_target_keeps_occupied_bed_wait_and_stable_first_patient(self):
        scenario = case()
        value = branch(run(scenario, 'total_wait_C1'))
        rows = patients(value)
        release = min(rows[pid]['treatment_start_t'] + scenario['patients'][0]['treatment_minutes']
                      for pid in ('P01', 'P02'))
        self.assertEqual(rows['P01']['first_commit_t'], 1)
        self.assertEqual(rows['P02']['first_commit_t'], 1)
        self.assertGreater(rows['P03']['first_commit_t'], release)
        self.assertGreater(rows['P03']['treatment_start_t'], release)

    def test_same_information_event_review_reduces_periodic_report_retry_idle(self):
        result = run(case('periodic_release_long_retry'),
                     'stable_responsive_C1', 'review_stable_C1')
        old, new = branch(result, 'stable_responsive_C1'), branch(result, 'review_stable_C1')
        # Once the earlier patient is treated, its real bed use changes later
        # report contents.  Compare the identical information available at
        # the causal decision point, rather than demanding identical futures.
        def received_through_release(value):
            return [{k: v for k, v in e.items() if k != 'event_sequence'}
                    for e in events(value, 'resource_report_received') if e['t'] <= 8]
        self.assertEqual(received_through_release(old), received_through_release(new))
        self.assertLess(total(new, 'treatment_start_t'), total(old, 'treatment_start_t'))
        self.assertTrue(events(new, 'resource_event_review'))
        self.assertFalse(events(new, 'resource_update_captured'))

    def test_current_update_and_event_review_are_separate_factors(self):
        result = run(case('release_long_retry'), 'stable_responsive_C1',
                     'review_stable_C1', 'updates_stable_C1', 'total_wait_C1')
        old = branch(result, 'stable_responsive_C1')
        updates = branch(result, 'updates_stable_C1')
        both = branch(result)
        self.assertFalse(events(old, 'resource_update_captured'))
        self.assertTrue(events(updates, 'resource_update_captured'))
        self.assertFalse(events(updates, 'resource_event_review'))
        self.assertLess(total(both, 'treatment_start_t'), total(updates, 'treatment_start_t'))
        self.assertLess(total(both, 'treatment_start_t'), total(old, 'treatment_start_t'))

    def test_delayed_release_message_cannot_wake_before_its_receipt(self):
        scenario = case('release_notification_delay')
        value = branch(run(scenario, 'total_wait_C1'))
        receipts = [e for e in events(value, 'resource_report_received')
                    if 'publication_id' in e and e['observed_at'] == 6]
        self.assertTrue(receipts)
        self.assertEqual(receipts[0]['t'], 9)
        reasks = [e for e in events(value, 'ask', 'P01') if e['t'] > 1]
        self.assertTrue(reasks)
        self.assertGreaterEqual(reasks[0]['t'], 9)
        self.assertGreaterEqual(patients(value)['P01']['first_commit_t'], 10)

    def test_captures_publish_only_current_changed_fields(self):
        value = branch(run(case(), 'total_wait_C1'))
        captures = events(value, 'resource_update_captured')
        self.assertTrue(captures)
        self.assertEqual(set(captures[0]['resources']), {'ed_bed'})
        self.assertEqual(set(captures[1]['resources']), {'ed_bed'})
        for event in captures:
            self.assertEqual(set(event), {'t', 'type', 'event_sequence', 'hospital',
                'publication_id', 'resources', 'observed_at', 'receive_at'})
            self.assertEqual(event['observed_at'], event['t'])
            self.assertTrue(set(event['resources']) <= {'ed_bed', 'special_team'})
            for row in event['resources'].values():
                self.assertEqual(set(row), {'capacity', 'used'})

    def test_recovery_capture_occurs_at_actual_release_not_future_schedule(self):
        value = branch(run(case('release_long_retry'), 'total_wait_C1'))
        recovered = [e for e in events(value, 'resource_update_captured')
                     if e['resources'].get('ed_bed') == {'capacity': 1, 'used': 0}]
        self.assertTrue(recovered)
        self.assertEqual(recovered[0]['observed_at'], 6)
        self.assertFalse([e for e in recovered if e['t'] < 6])

    def test_source_cycles_sample_before_queries_and_after_treatment_changes(self):
        scenario = case('release_long_retry')
        value = branch(run(scenario, 'total_wait_C1'))
        self.assertEqual([(e['t'], e['phase']) for e in events(value, 'resource_update_cycle')],
            [(t, phase) for t in range(scenario['max_minutes'] + 1)
             for phase in ('pre_queries', 'after_treatments')])

    def test_permanent_missing_team_is_not_resolved_by_bed_change_messages(self):
        value = branch(run(case('partial_team_unresolved'), 'total_wait_C1'))
        self.assertIsNone(patients(value)['P03']['first_commit_t'])
        self.assertIsNone(patients(value)['P03']['treatment_start_t'])
        self.assertFalse(events(value, 'commit', 'P03'))
        for event in events(value, 'resource_update_captured'):
            if 'special_team' in event['resources']:
                self.assertIsNone(event['resources']['special_team'])
        self.assertFalse(any(p['id'] == 'P03' for d in value['allocation_decisions']
                             for p in d['inputs']['patients']))

    def test_silent_hospital_does_not_publish_an_alternate_truth_channel(self):
        value = branch(run(case('missing_unresolved'), 'total_wait_C1'))
        self.assertTrue(events(value, 'resource_update_cycle'))
        self.assertFalse(events(value, 'resource_update_captured'))
        self.assertFalse(events(value, 'commit'))
        self.assertEqual(value['metrics']['unassigned'], 3)

    def test_expired_acceptance_is_replaced_with_a_new_patient_reply(self):
        value = branch(run(case('expiry_before_slow_confirmation'), 'total_wait_C1'))
        expired = events(value, 'expire', 'P03')
        self.assertTrue(expired)
        committed = events(value, 'commit', 'P03')
        self.assertTrue(committed)
        accepts = events(value, 'accept', 'P03')
        self.assertTrue(any(e['t'] > expired[0]['t'] and e['t'] <= committed[0]['t'] for e in accepts))
        for decision in value['allocation_decisions']:
            for patient in decision['inputs']['patients']:
                for hid in patient['options']:
                    self.assertGreater(patient['valid_offer_expiries'][hid], decision['t'])

    def test_event_review_has_no_duplicate_patient_report_reactions(self):
        value = branch(run(case('intrinsic_single_hospital_queue'), 'total_wait_C1'))
        reviews = events(value, 'resource_event_review')
        self.assertTrue(reviews)
        keys = [(e['patient'], e['report_event_sequence']) for e in reviews]
        self.assertEqual(len(keys), len(set(keys)))

    def test_event_review_requires_current_applied_report_and_follows_receipt(self):
        value = branch(run(case('periodic_release_long_retry'), 'total_wait_C1'))
        by_seq = {e['event_sequence']: e for e in value['events'] if 'event_sequence' in e}
        self.assertTrue(events(value, 'resource_event_review'))
        for event in events(value, 'resource_event_review'):
            source = by_seq[event['report_event_sequence']]
            self.assertEqual(source['type'], 'resource_report_received')
            self.assertEqual(source['t'], event['t'])
            self.assertTrue(source['applied'])
            self.assertLess(source['event_sequence'], event['event_sequence'])
            self.assertEqual(source['hospital'], event['hospital'])

    def test_ignored_old_report_cannot_trigger_review(self):
        scenario = case('out_of_order')
        scenario['resource_update_config']['delay_minutes'] = 10
        value = branch(run(scenario, 'total_wait_C1'))
        ignored = {e['event_sequence'] for e in events(value, 'resource_report_received') if not e['applied']}
        self.assertTrue(ignored)
        self.assertTrue([e for e in events(value, 'resource_report_received')
                         if 'publication_id' in e and not e['applied']])
        self.assertFalse([e for e in events(value, 'resource_event_review')
                          if e['report_event_sequence'] in ignored])

    def test_unregistered_future_patient_never_causes_early_review(self):
        value = branch(run(case('future_patient_not_yet_active'), 'total_wait_C1'))
        self.assertFalse([e for e in events(value, 'resource_event_review', 'P03') if e['t'] < 12])
        self.assertFalse([e for e in events(value, 'ask', 'P03') if e['t'] < 12])

    def test_solver_only_receives_detached_current_patient_and_resource_view(self):
        original = StableCPSATAssignmentRule.decide_batch
        calls = []

        def checked(rule, view, ready, t):
            self.assertEqual(set(vars(view)), {'hospitals', 'position', 'travel'})
            for patient, offered in ready:
                self.assertEqual(set(vars(patient)), {'id', 'priority', 'demand', 'caps', 't_req', 'loc'})
                self.assertLessEqual(patient.t_req, t)
                self.assertTrue(offered)
            calls.append(t)
            return original(rule, view, ready, t)

        with patch.object(StableCPSATAssignmentRule, 'decide_batch', checked):
            branch(run(case('release_long_retry'), 'total_wait_C1'))
        self.assertTrue(calls)

    def test_unknown_or_stale_quantities_and_expired_offers_never_enter_model(self):
        value = branch(run(case('slow_confirmation_eight'), 'total_wait_C1'))
        for decision in value['allocation_decisions']:
            self.assertTrue(all(r['free'] is not None for r in decision['inputs']['free_resources']))
            for resources in decision['resource_evidence'].values():
                self.assertTrue(all(r['status'] == 'fresh' for r in resources.values()))
            for patient in decision['inputs']['patients']:
                self.assertLessEqual(patient['request_t'], decision['t'])
                self.assertTrue(all(patient['valid_offer_expiries'][h] > decision['t']
                                    for h in patient['options']))

    def test_physical_gate_remains_after_the_proposal_and_before_commit(self):
        value = branch(run(case('release_before_offer_expiry'), 'total_wait_C1'))
        self.assertTrue(value['allocation_decisions'])
        self.assertTrue(events(value, 'commit'))
        by_seq = {e['event_sequence']: e for e in value['events'] if 'event_sequence' in e}
        for decision in value['allocation_decisions']:
            for gate in decision['gate_records']:
                self.assertGreater(gate['event_sequence'], decision['event_sequence'])
                if gate['passed']:
                    commit = by_seq[gate['commit_event_sequence']]
                    self.assertEqual(commit['type'], 'commit')
                    self.assertLess(gate['event_sequence'], commit['event_sequence'])
                    self.assertGreater(gate['offer_expires_at'], gate['t'])

    def test_intrinsic_single_bed_queue_is_retained(self):
        scenario = case('intrinsic_single_hospital_queue')
        value = branch(run(scenario, 'total_wait_C1'))
        starts = sorted(p['treatment_start_t'] for p in value['patient_outcomes'])
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(t is not None for t in starts))
        duration = scenario['patients'][0]['treatment_minutes']
        self.assertGreaterEqual(starts[1] - starts[0], duration)
        self.assertGreaterEqual(starts[2] - starts[1], duration)
        self.assertGreater(total(value, 'treatment_start_t'), 2 * duration)

    def test_slow_confirmation_reduces_whole_sum_without_delaying_other_patients(self):
        result = run(case('slow_confirmation_eight'), 'stable_responsive_C1', 'total_wait_C1')
        old, new = branch(result, 'stable_responsive_C1'), branch(result)
        self.assertLess(total(new, 'treatment_start_t'), total(old, 'treatment_start_t'))
        for pid in patients(old):
            self.assertLessEqual(patients(new)[pid]['treatment_start_t'],
                                 patients(old)[pid]['treatment_start_t'])

    def test_whole_episode_optimality_is_never_claimed(self):
        value = branch(run(case(), 'total_wait_C1'))
        self.assertFalse(value['metrics']['whole_episode_optimal_proven'])
        self.assertFalse(any(d['actual_optimal_proven'] for d in value['allocation_decisions']))
        for record in value['solver_records']:
            self.assertEqual(record['objective_names'], [
                'unassigned_priority_weight', 'priority_weighted_travel_minutes'])
            self.assertFalse(record['stability']['optimality_claimed'])

    def test_rotation_and_input_immutability_preserve_results(self):
        scenario = case('periodic_release_long_retry', seed=7)
        saved = deepcopy(scenario)
        specs = [policy('stable_responsive_C1'), policy('review_stable_C1'), policy()]
        saved_specs = deepcopy(specs)
        ids = [p['id'] for p in specs]
        one = run_total_wait_replay(scenario, specs, execution_order=ids)
        two = run_total_wait_replay(scenario, specs, execution_order=ids[::-1])
        self.assertEqual(scenario, saved)
        self.assertEqual(specs, saved_specs)
        for left, right in zip(one['branches'], two['branches']):
            self.assertEqual(semantic(left), semantic(right))


if __name__ == '__main__':
    unittest.main()
