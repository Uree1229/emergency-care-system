"""Small-seed integration checks for reusing actual unfinished inquiries.

These tests keep movement, physical transfer preparation and hospital work
fixed. They use only seeds 7 and 8, never a reserved experiment seed.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import CPSATDependencyError, _load_cp_model
from simulation.existing_info_engine import (
    DEFAULT_EXISTING_INFO_POLICIES, run_existing_info_replay,
)
from simulation.outage_recovery_engine import (
    DEFAULT_OUTAGE_RECOVERY_POLICIES, run_outage_recovery_replay,
)
from simulation.outage_recovery_scenarios import build_outage_recovery_scenarios
from simulation.stable_cp_sat_assignment import StableCPSATAssignmentRule
from simulation.total_wait_metrics import waiting_metrics


def case(family='future_outage_after_early_offer', seed=7):
    return build_outage_recovery_scenarios(seed, [family])[0]


def policy(name='outage_recovery_C1', **changes):
    value = deepcopy(next(p for p in DEFAULT_OUTAGE_RECOVERY_POLICIES if p['id'] == name))
    value.update(changes)
    return value


def run(scenario, *names):
    return run_outage_recovery_replay(scenario, [policy(name) for name in names])


def branch(result, name='outage_recovery_C1'):
    return next(b for b in result['branches'] if b['id'] == name)


def events(value, kind, patient=None, hospital=None):
    return [e for e in value['events'] if e['type'] == kind
            and (patient is None or e.get('patient') == patient)
            and (hospital is None or e.get('hospital') == hospital)]


def patients(value):
    return {p['id']: p for p in value['patient_outcomes']}


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


class OutageRecoveryConfigurationTests(unittest.TestCase):
    def test_six_branches_keep_old_four_and_expose_retention_and_recovery_flags(self):
        self.assertEqual([p['id'] for p in DEFAULT_OUTAGE_RECOVERY_POLICIES], [
            'stable_responsive_C1', 'review_stable_C1', 'early_stable_C1',
            'existing_info_C1', 'retained_backup_C1', 'outage_recovery_C1'])
        for name, expected in (
            ('stable_responsive_C1', (False, False)),
            ('review_stable_C1', (False, False)),
            ('early_stable_C1', (False, False)),
            ('existing_info_C1', (False, False)),
            ('retained_backup_C1', (True, False)),
            ('outage_recovery_C1', (True, True)),
        ):
            actual = policy(name)
            self.assertEqual((actual['retained_backups'], actual['transfer_same_tick']), expected)
            self.assertIs(actual['recovery_followup'], name == 'outage_recovery_C1')
            self.assertIs(actual['resource_updates'], False)

    def test_new_factors_retain_frozen_inquiry_solver_and_confirmation_settings(self):
        original = deepcopy(next(p for p in DEFAULT_EXISTING_INFO_POLICIES
                                 if p['id'] == 'existing_info_C1'))
        for key in ('id', 'label'):
            original.pop(key)
        for name in ('retained_backup_C1', 'outage_recovery_C1'):
            actual = policy(name)
            for key in ('id', 'label', 'retained_backups', 'transfer_same_tick', 'recovery_followup'):
                actual.pop(key)
            self.assertEqual(actual, original)

    def test_an_additional_resource_publication_channel_is_rejected(self):
        with self.assertRaises(ValueError):
            run_outage_recovery_replay(case(), [policy(resource_updates=True)])

    def test_new_flags_require_explicit_booleans(self):
        for key in ('retained_backups', 'transfer_same_tick', 'recovery_followup'):
            for value in (None, 1, 'yes'):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    run_outage_recovery_replay(case(), [policy(**{key: value})])
            invalid = policy()
            del invalid[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                run_outage_recovery_replay(case(), [invalid])

    def test_canonical_policies_reject_mixed_backend_and_information_settings(self):
        for changes in ({'mode': 'perfect'}, {'stable_ties': False}, {'rule': 'H1'},
                        {'prefetch': False}, {'watch': False}, {'wait': 0},
                        {'grace_minutes': 4}, {'retained_backups': False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_outage_recovery_replay(case(), [policy(**changes)])

    def test_duplicate_branches_and_invalid_execution_order_are_rejected(self):
        for specs, order in (([policy(), policy()], None),
                             ([policy(), policy('existing_info_C1')], ['outage_recovery_C1'] * 2)):
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                run_outage_recovery_replay(case(), specs, execution_order=order)

    def test_required_cp_sat_backend_is_not_silently_substituted(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=CPSATDependencyError('test unavailable')):
            with self.assertRaises(CPSATDependencyError):
                run(case(), 'outage_recovery_C1')


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class OutageRecoveryReplayTests(unittest.TestCase):
    def test_original_four_branches_keep_frozen_events_decisions_and_outcomes(self):
        scenario = case()
        names = tuple(p['id'] for p in DEFAULT_EXISTING_INFO_POLICIES)
        frozen = run_existing_info_replay(scenario)
        current = run(scenario, *names)
        for left, right in zip(frozen['branches'], current['branches']):
            with self.subTest(branch=left['id']):
                for key in ('events', 'patient_outcomes', 'observation_records',
                            'allocation_decisions', 'solver_records', 'protection_records',
                            'trajectories', 'resource_snapshots'):
                    self.assertEqual(semantic(left[key]), semantic(right[key]))

    def test_original_five_minute_outage_penalty_is_removed_at_both_small_seeds(self):
        for seed in (7, 8):
            scenario = case(seed=seed)
            result = run(scenario, 'review_stable_C1', 'existing_info_C1', 'outage_recovery_C1')
            baseline = patients(branch(result, 'review_stable_C1'))['P01']
            old = patients(branch(result, 'existing_info_C1'))['P01']
            new = patients(branch(result))['P01']
            with self.subTest(seed=seed):
                self.assertEqual(old['treatment_start_t'] - new['treatment_start_t'], 5)
                self.assertEqual(new['treatment_start_t'], baseline['treatment_start_t'])
                self.assertEqual(new['final_arrive_t'], baseline['final_arrive_t'])

    def test_original_far_inquiry_and_attempt_are_reused_without_resubmission(self):
        value = branch(run(case(), 'outage_recovery_C1'))
        asks = events(value, 'ask', 'P01', 'FAR')
        self.assertEqual([(e['t'], e['attempt']) for e in asks], [(0, 1)])
        self.assertEqual([e['t'] for e in events(value, 'accept', 'P01', 'FAR')], [5])
        self.assertEqual([e['t'] for e in events(value, 'commit', 'P01', 'FAR')], [7])

    def test_retaining_inquiry_alone_does_not_remove_same_tick_transfer_review_factor(self):
        result = run(case(), 'retained_backup_C1', 'outage_recovery_C1')
        retained = branch(result, 'retained_backup_C1')
        complete = branch(result)
        self.assertEqual(patients(retained)['P01']['treatment_start_t']
                         - patients(complete)['P01']['treatment_start_t'], 1)
        self.assertEqual([e['t'] for e in events(retained, 'accept', 'P01', 'FAR')], [5])
        self.assertEqual([e['t'] for e in events(complete, 'accept', 'P01', 'FAR')], [5])

    def test_same_physical_first_leg_and_arrival_are_kept_after_notification(self):
        result = run(case(), 'existing_info_C1', 'outage_recovery_C1')
        old, new = branch(result, 'existing_info_C1'), branch(result)
        def first_transport(value):
            return next(leg for trajectory in value['trajectories'] for leg in trajectory['legs']
                        if leg['kind'] == 'transport')
        self.assertEqual(first_transport(old), first_transport(new))
        self.assertEqual(events(old, 'arrive', 'P01', 'NEAR')[0]['t'], 4)
        self.assertEqual(events(new, 'arrive', 'P01', 'NEAR')[0]['t'], 4)
        self.assertFalse(events(new, 'redirect'))
        self.assertFalse(any(leg.get('ended_by') in ('notification_stop', 'recovery_stop')
                             for trajectory in new['trajectories'] for leg in trajectory['legs']))

    def test_three_minute_transfer_preparation_is_not_shortened(self):
        value = branch(run(case(), 'outage_recovery_C1'))
        failure = events(value, 'arrival_unable', 'P01')[0]
        search = events(value, 'transfer_search', 'P01')[0]
        departure = events(value, 'transfer_depart', 'P01')[0]
        self.assertEqual(failure['transfer_search_at'] - failure['t'], 3)
        self.assertEqual(search['t'], failure['transfer_search_at'])
        self.assertEqual(departure['t'], search['t'])

    def test_healthy_early_offer_still_reduces_patient_wait_and_whole_wait(self):
        scenario = case('ample_cohort_dominant_offer')
        result = run(scenario, 'review_stable_C1', 'existing_info_C1', 'outage_recovery_C1')
        old = branch(result, 'existing_info_C1')
        baseline = branch(result, 'review_stable_C1')
        new = branch(result)
        self.assertEqual(patients(old), patients(new))
        self.assertLess(waiting_metrics(scenario, new)['total_wait_sum_minutes'],
                        waiting_metrics(scenario, baseline)['total_wait_sum_minutes'])
        self.assertEqual(new['metrics']['inquiries'], old['metrics']['inquiries'])

    def test_quick_restore_does_not_force_stopping_or_abandoning_original_destination(self):
        for family in ('release_before_offer_expiry', 'restore_before_failed_arrival'):
            result = run(case(family), 'existing_info_C1', 'outage_recovery_C1')
            old, new = branch(result, 'existing_info_C1'), branch(result)
            with self.subTest(family=family):
                self.assertEqual(patients(new)['P01']['treatment_start_t'], 4)
                self.assertEqual(patients(old), patients(new))
                self.assertFalse(events(new, 'arrival_unable'))
                self.assertFalse(events(new, 'redirect'))
                self.assertFalse(events(new, 'transfer_depart'))

    def test_backup_acceptance_cannot_redirect_before_existing_patient_notification(self):
        value = branch(run(case('notify_after_backup_reply'), 'outage_recovery_C1'))
        self.assertEqual([e['t'] for e in events(value, 'accept', 'P01', 'FAR')], [2])
        self.assertEqual([e['t'] for e in events(value, 'notify', 'P01')], [3])
        self.assertFalse([e for e in events(value, 'commit', 'P01', 'FAR') if e['t'] < 3])
        self.assertEqual([e['t'] for e in events(value, 'redirect', 'P01', 'FAR')], [3])
        gates = events(value, 'confirmation_gate', 'P01', 'FAR')
        self.assertTrue(any(e['t'] == 3 and e['passed'] and e['accepted_at'] == 2 for e in gates))

    def test_offer_expiring_at_current_minute_is_never_reused(self):
        value = branch(run(case('expired_backup_after_notify'), 'outage_recovery_C1'))
        self.assertTrue([e for e in events(value, 'expire', 'P01', 'FAR') if e['t'] == 3])
        for decision in value['allocation_decisions']:
            for patient in decision['inputs']['patients']:
                self.assertTrue(all(expiry > decision['t']
                                    for expiry in patient['valid_offer_expiries'].values()))
            for gate in decision['gate_records']:
                if gate['passed']:
                    self.assertGreater(gate['offer_expires_at'], gate['t'])
        for commit in events(value, 'commit', 'P01', 'FAR'):
            self.assertTrue(any(e['t'] > 3 and e['t'] <= commit['t']
                                for e in events(value, 'accept', 'P01', 'FAR')))

    def test_silent_backup_keeps_original_timeout_and_never_invents_acceptance(self):
        value = branch(run(case('silent_backup_timeout'), 'outage_recovery_C1'))
        responses = events(value, 'no_response', 'P01', 'FAR')
        self.assertTrue(responses)
        self.assertEqual(responses[0]['t'], 4)
        self.assertFalse(events(value, 'accept', 'P01', 'FAR'))
        self.assertFalse(events(value, 'commit', 'P01', 'FAR'))

    def test_late_finished_work_cannot_resurrect_timed_out_original_inquiry(self):
        value = branch(run(case('late_backup_timeout'), 'outage_recovery_C1'))
        self.assertEqual(events(value, 'no_response', 'P01', 'FAR')[0]['t'], 4)
        late = events(value, 'late_reply', 'P01', 'FAR')
        self.assertTrue(any(e['t'] == 7 and e['attempt'] == 1 and e['ignored'] for e in late))
        self.assertFalse([e for e in events(value, 'accept', 'P01', 'FAR') if e['t'] == 7])
        for gate in events(value, 'confirmation_gate', 'P01', 'FAR'):
            if gate['passed']:
                self.assertGreater(gate['accepted_at'], 4)

    def test_two_patient_acceptances_do_not_create_a_second_backup_bed(self):
        value = branch(run(case('competing_backup_capacity'), 'outage_recovery_C1'))
        self.assertEqual(len([e for e in events(value, 'accept', hospital='FAR') if e['t'] == 5]), 2)
        same_tick = [e for e in events(value, 'commit', hospital='FAR') if e['t'] == 7]
        self.assertEqual(len(same_tick), 1)
        decisions = [d for d in value['allocation_decisions'] if d['t'] == 7]
        self.assertTrue(any(len(d['inputs']['patients']) == 2 for d in decisions))
        for gate in events(value, 'confirmation_gate', hospital='FAR'):
            if gate['passed']:
                self.assertGreaterEqual(gate['resources']['ed_bed']['free'], 1)

    def test_stale_or_unknown_backup_quantities_cannot_be_refreshed_by_patient_acceptance(self):
        value = branch(run(case('unverified_backup_resources'), 'outage_recovery_C1'))
        self.assertTrue(events(value, 'accept', 'P01', 'FAR'))
        self.assertFalse(events(value, 'commit', 'P01', 'FAR'))
        self.assertTrue(events(value, 'information_wait', 'P01', 'FAR'))
        self.assertFalse(any(row['id'] == 'P01' and 'FAR' in row['options']
                             for decision in value['allocation_decisions']
                             for row in decision['inputs']['patients']))

    def test_outage_at_arrival_boundary_needs_no_nonexistent_prearrival_notification(self):
        value = branch(run(case('outage_at_arrival_boundary'), 'outage_recovery_C1'))
        self.assertFalse(events(value, 'notify', 'P01'))
        self.assertEqual(events(value, 'arrival_unable', 'P01')[0]['t'], 4)
        self.assertEqual(events(value, 'commit', 'P01', 'FAR')[0]['t'], 7)

    def test_retained_offer_still_passes_final_physical_resource_conflict_gate(self):
        scenario = case()
        scenario['hospitals'][1]['capacity']['ed_bed'] = 1
        scenario['observation_config']['feed_interval_minutes'] = 0
        scenario['exogenous_events'].append(dict(type='external_arrival', t=6,
            hospital='FAR', duration=10, demand={'ed_bed': 1}))
        value = branch(run(scenario, 'outage_recovery_C1'))
        rejected = events(value, 'confirmation_rejected', 'P01', 'FAR')
        self.assertTrue(any(e['t'] == 7 and e['reason'] == 'RESOURCE_CONFLICT' for e in rejected))
        self.assertFalse([e for e in events(value, 'commit', 'P01', 'FAR') if e['t'] < 16])

    def test_recovery_reduces_whole_wait_rather_than_only_renaming_transfer_wait(self):
        scenario = case()
        result = run(scenario, 'existing_info_C1', 'outage_recovery_C1')
        old, new = branch(result, 'existing_info_C1'), branch(result)
        old_wait, new_wait = waiting_metrics(scenario, old), waiting_metrics(scenario, new)
        self.assertEqual(old_wait['total_wait_sum_minutes'] - new_wait['total_wait_sum_minutes'], 5)
        self.assertEqual(old_wait['transfer_wait_sum_minutes'], new_wait['transfer_wait_sum_minutes'])
        self.assertEqual(old_wait['request_to_treatment_capped_sum_minutes']
                         - new_wait['request_to_treatment_capped_sum_minutes'], 5)

    def test_no_added_reports_or_resource_queries_in_the_fresh_outage_case(self):
        result = run(case(), 'existing_info_C1', 'outage_recovery_C1')
        old, new = branch(result, 'existing_info_C1'), branch(result)
        self.assertEqual(len(events(old, 'resource_report_received')),
                         len(events(new, 'resource_report_received')))
        self.assertEqual(len(events(old, 'resource_confirmation_request')),
                         len(events(new, 'resource_confirmation_request')))
        for value in result['branches']:
            self.assertFalse(events(value, 'resource_update_cycle'))
            self.assertFalse(events(value, 'resource_update_captured'))
            self.assertFalse(any('publication_id' in e for e in events(value, 'resource_report_received')))
            self.assertTrue(all(e['source'] in ('initial', 'feed', 'confirmation')
                                for e in events(value, 'resource_report_received')))

    def test_backup_metadata_does_not_expose_environmental_response_schedule(self):
        value = branch(run(case(), 'outage_recovery_C1'))
        markers = [e for e in value['events'] if 'backup' in e['type']]
        self.assertTrue(markers)
        forbidden = {'silent', 'due_at', 'receive_at', 'restore_at', 'until',
                     'treatment_minutes', 'duration', 'physical_capacity', 'physical_used'}
        def check(item):
            if isinstance(item, dict):
                self.assertFalse(forbidden & set(item))
                for child in item.values():
                    check(child)
            elif isinstance(item, list):
                for child in item:
                    check(child)
        for marker in markers:
            check(marker)

    def test_future_outage_schedule_cannot_change_initial_commitment_or_retention(self):
        one = case()
        two = deepcopy(one)
        two['exogenous_events'][0].update(t=20, until=30)
        left = branch(run(one, 'outage_recovery_C1'))
        right = branch(run(two, 'outage_recovery_C1'))
        before = lambda value: semantic([e for e in value['events'] if e['t'] <= 1])
        self.assertEqual(before(left), before(right))

    def test_all_solver_inputs_stay_detached_and_use_live_current_offers(self):
        original = StableCPSATAssignmentRule.decide_batch
        called = []
        def checked(rule, view, ready, t):
            self.assertEqual(set(vars(view)), {'hospitals', 'position', 'travel'})
            for patient, offered in ready:
                self.assertEqual(set(vars(patient)), {'id', 'priority', 'demand', 'caps', 't_req', 'loc'})
                self.assertLessEqual(patient.t_req, t)
                self.assertTrue(offered)
            called.append(t)
            return original(rule, view, ready, t)
        with patch.object(StableCPSATAssignmentRule, 'decide_batch', checked):
            value = branch(run(case('competing_backup_capacity'), 'outage_recovery_C1'))
        self.assertIn(7, called)
        for decision in value['allocation_decisions']:
            self.assertTrue(all(row['status'] == 'fresh'
                                for data in decision['resource_evidence'].values() for row in data.values()))
            self.assertTrue(all(expiry > decision['t'] for row in decision['inputs']['patients']
                                for expiry in row['valid_offer_expiries'].values()))

    def test_branch_execution_rotation_and_inputs_do_not_change_outcomes(self):
        scenario = case('competing_backup_capacity')
        saved = deepcopy(scenario)
        specs = [policy(name) for name in ('review_stable_C1', 'existing_info_C1',
                                         'retained_backup_C1', 'outage_recovery_C1')]
        saved_specs = deepcopy(specs)
        names = [p['id'] for p in specs]
        one = run_outage_recovery_replay(scenario, specs, execution_order=names)
        two = run_outage_recovery_replay(scenario, specs, execution_order=names[::-1])
        self.assertEqual(scenario, saved)
        self.assertEqual(specs, saved_specs)
        for left, right in zip(one['branches'], two['branches']):
            self.assertEqual(semantic(left), semantic(right))


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class OutageRecoveryFollowupTests(unittest.TestCase):
    def test_existing_post_failure_feed_can_reopen_original_hospital_with_new_acceptance(self):
        value = branch(run(case('silent_backup_timeout'), 'outage_recovery_C1'))
        asks = events(value, 'ask', 'P01', 'NEAR')
        self.assertEqual([(e['t'], e['attempt']) for e in asks], [(0, 1), (10, 2)])
        self.assertEqual([e['t'] for e in events(value, 'accept', 'P01', 'NEAR')], [1, 11])
        self.assertEqual([e['t'] for e in events(value, 'commit', 'P01', 'NEAR')], [1, 11])
        self.assertEqual(patients(value)['P01']['treatment_start_t'], 12)
        self.assertFalse(events(value, 'resource_update_captured'))
        for gate in events(value, 'confirmation_gate', 'P01', 'NEAR'):
            if gate['t'] > 4 and gate['passed']:
                self.assertEqual(gate['accepted_at'], 11)

    def test_an_old_initial_free_report_does_not_prove_recovery_after_failed_arrival(self):
        scenario = case('silent_backup_timeout')
        scenario['observation_config']['feed_interval_minutes'] = 0
        value = branch(run(scenario, 'outage_recovery_C1'))
        self.assertEqual([e['t'] for e in events(value, 'ask', 'P01', 'NEAR')], [0])
        self.assertIsNone(patients(value)['P01']['treatment_start_t'])
        self.assertFalse([e for e in value['events'] if 'reopen' in e['type']])

    def test_ignored_old_free_packet_cannot_reopen_a_hospital_with_later_busy_evidence(self):
        scenario = case('silent_backup_timeout')
        scenario['observation_config']['feed_delay_by_source_minute'] = {
            '0': 12, **{str(t): 100 for t in range(10, 61)}}
        value = branch(run(scenario, 'outage_recovery_C1'))
        old_free = [e for e in events(value, 'resource_report_received', hospital='NEAR')
                    if e['observed_at'] == 0 and e['t'] == 12]
        self.assertTrue(old_free)
        self.assertTrue(all(e['applied'] is False for e in old_free))
        self.assertEqual([e['t'] for e in events(value, 'ask', 'P01', 'NEAR')], [0])
        self.assertIsNone(patients(value)['P01']['treatment_start_t'])

    def test_every_required_resource_must_have_a_post_failure_source_timestamp(self):
        scenario = case('silent_backup_timeout')
        near, far = scenario['hospitals']
        far['capabilities'] = deepcopy(near['capabilities'])
        scenario['patients'][0].update(required_capabilities=deepcopy(near['capabilities']),
            resource_demands={'ed_bed': 1, 'special_team': 1})
        scenario['observation_config']['initial_reports'] = [dict(
            hospital=h['id'], observed_at=0,
            resources={r: dict(capacity=q, used=0) for r, q in h['capacity'].items()})
            for h in scenario['hospitals']]
        # A permitted partial packet keeps the old team observation rather
        # than falsely giving that unchanged field the new bed timestamp.
        from simulation.outage_recovery_engine import OutageRecoveryReplay
        original = OutageRecoveryReplay._physical_report
        def bed_only(replay, hid, missing=()):
            return {'ed_bed': original(replay, hid, missing)['ed_bed']}
        with patch.object(OutageRecoveryReplay, '_physical_report', bed_only):
            value = branch(run(scenario, 'outage_recovery_C1'))
        self.assertEqual([e['t'] for e in events(value, 'ask', 'P01', 'NEAR')], [0])
        self.assertIsNone(patients(value)['P01']['treatment_start_t'])

    def test_missing_post_failure_quantity_cannot_be_replaced_with_actual_physical_capacity(self):
        scenario = case('silent_backup_timeout')
        scenario['observation_config']['feed_missing_resources'] = ['ed_bed']
        scenario['observation_config']['confirmation_missing_resources'] = ['ed_bed']
        scenario['resource_update_config']['missing_resources'] = ['ed_bed']
        # Keep the initial early dispatch eligible. Subsequent packets make
        # the quantity explicitly unknown after that already valid dispatch.
        scenario['observation_config']['feed_interval_minutes'] = 5
        scenario['observation_config']['feed_delay_by_source_minute'] = {'0': 5}
        value = branch(run(scenario, 'outage_recovery_C1'))
        self.assertTrue(events(value, 'arrival_unable', 'P01'))
        self.assertEqual([e['t'] for e in events(value, 'ask', 'P01', 'NEAR')], [0])
        self.assertIsNone(patients(value)['P01']['treatment_start_t'])

    def test_capability_failure_is_not_reopened_by_free_bed_reports(self):
        scenario = case('dominant_near_slow_other')
        extra = 'synthetic_arrival_only_capability'
        scenario['patients'][0]['arrival_assessment_adds_capabilities'] = [extra]
        scenario['hospitals'][1]['capabilities'].append(extra)
        value = branch(run(scenario, 'outage_recovery_C1'))
        failure = events(value, 'arrival_unable', 'P01', 'NEAR')[0]
        self.assertEqual(failure['reason'], 'required_capability')
        self.assertEqual([e['t'] for e in events(value, 'ask', 'P01', 'NEAR')], [0])
        self.assertFalse([e for e in value['events']
                          if 'reopen' in e['type'] and e.get('hospital') == 'NEAR'])

    def test_healthy_treated_patient_cannot_be_reopened_by_later_resource_reports(self):
        value = branch(run(case('restore_before_failed_arrival'), 'outage_recovery_C1'))
        self.assertEqual(patients(value)['P01']['treatment_start_t'], 4)
        self.assertEqual([e['t'] for e in events(value, 'ask', 'P01', 'NEAR')], [0])
        self.assertFalse([e for e in value['events'] if 'reopen' in e['type']])

    def test_expired_backup_retry_uses_a_new_inquiry_and_new_acceptance_after_alert(self):
        for seed in (7, 8):
            result = run(case('expired_backup_after_notify', seed), 'review_stable_C1', 'outage_recovery_C1')
            value = branch(result)
            with self.subTest(seed=seed):
                self.assertEqual([(e['t'], e['attempt']) for e in events(value, 'ask', 'P01', 'FAR')],
                                 [(0, 1), (4, 2)])
                self.assertEqual([e['t'] for e in events(value, 'accept', 'P01', 'FAR')], [2, 6])
                self.assertEqual([e['t'] for e in events(value, 'expire', 'P01', 'FAR')], [3])
                self.assertEqual(patients(value)['P01']['treatment_start_t'],
                                 patients(branch(result, 'review_stable_C1'))['P01']['treatment_start_t'])
                gate = events(value, 'confirmation_gate', 'P01', 'FAR')[0]
                self.assertTrue(gate['passed'])
                self.assertEqual((gate['t'], gate['accepted_at'], gate['offer_expires_at']), (6, 6, 7))

    def test_alert_retry_does_not_duplicate_an_existing_pending_or_live_backup(self):
        for family in ('future_outage_after_early_offer', 'notify_after_backup_reply'):
            value = branch(run(case(family), 'outage_recovery_C1'))
            with self.subTest(family=family):
                self.assertEqual([(e['t'], e['attempt']) for e in events(value, 'ask', 'P01', 'FAR')],
                                 [(0, 1)])
                self.assertFalse([e for e in value['events'] if e['type'] == 'backup_alert_retry'])

    def test_expired_backup_with_unknown_quantities_is_not_retried_on_alert(self):
        scenario = case('expired_backup_after_notify')
        near, far = scenario['hospitals']
        scenario['observation_config'].update(feed_interval_minutes=0,
            confirmation_missing_resources=['ed_bed'], initial_reports=[
                dict(hospital=near['id'], observed_at=0,
                     resources={r: dict(capacity=q, used=0) for r, q in near['capacity'].items()}),
                dict(hospital=far['id'], observed_at=-11,
                     resources={r: dict(capacity=q, used=0) for r, q in far['capacity'].items()}),
            ])
        scenario['resource_update_config']['missing_resources'] = ['ed_bed']
        value = branch(run(scenario, 'outage_recovery_C1'))
        self.assertTrue(events(value, 'notify', 'P01'))
        self.assertFalse([e for e in events(value, 'ask', 'P01', 'FAR') if e['t'] == 4])
        self.assertFalse([e for e in value['events'] if e['type'] == 'backup_alert_retry'])


if __name__ == '__main__':
    unittest.main()
