"""Small-seed tests of source-free inquiry review and early offer maturity.

The checks retain genuine capacity competition and include a future outage
where an earlier departure is harmful. They never use a reserved sweep seed.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import CPSATDependencyError, _load_cp_model
from simulation.existing_info_engine import (
    DEFAULT_EXISTING_INFO_POLICIES, decide_early_offer, run_existing_info_replay,
)
from simulation.stable_cp_sat_assignment import StableCPSATAssignmentRule
from simulation.total_wait_engine import DEFAULT_TOTAL_WAIT_POLICIES, run_total_wait_replay
from simulation.total_wait_metrics import waiting_metrics
from simulation.total_wait_scenarios import build_total_wait_scenarios


def case(family='fresh_control', seed=7):
    return build_total_wait_scenarios(seed, [family])[0]


def offer_case(*, count=1, capacity=2, near_delay=1, far_delay=5):
    value = case()
    value['patients'] = value['patients'][:count]
    basic = value['hospitals'][1]['capabilities']
    for patient in value['patients']:
        patient.update(required_capabilities=deepcopy(basic), resource_demands={'ed_bed': 1})
    value['hospitals'][0]['capacity']['ed_bed'] = capacity
    value['response_environment'] = {'*': {
        'NEAR': dict(delay=near_delay, silent=False),
        'FAR': dict(delay=far_delay, silent=False),
    }}
    return value


def policy(name='existing_info_C1', **changes):
    result = deepcopy(next(p for p in DEFAULT_EXISTING_INFO_POLICIES if p['id'] == name))
    result.update(changes)
    return result


def run(scenario, *ids):
    return run_existing_info_replay(scenario, [policy(name) for name in ids])


def branch(result, name='existing_info_C1'):
    return next(b for b in result['branches'] if b['id'] == name)


def events(value, kind, patient=None):
    return [e for e in value['events'] if e['type'] == kind
            and (patient is None or e.get('patient') == patient)]


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


class ExistingInfoConfigurationTests(unittest.TestCase):
    def test_four_branches_expose_review_and_early_maturity_as_separate_factors(self):
        self.assertEqual([p['id'] for p in DEFAULT_EXISTING_INFO_POLICIES], [
            'stable_responsive_C1', 'review_stable_C1', 'early_stable_C1', 'existing_info_C1'])
        for name, expected in (
            ('stable_responsive_C1', (False, False)),
            ('review_stable_C1', (True, False)),
            ('early_stable_C1', (False, True)),
            ('existing_info_C1', (True, True)),
        ):
            actual = policy(name)
            self.assertEqual((actual['event_review'], actual['early_offers']), expected)
            self.assertIs(actual['resource_updates'], False)

    def test_every_factor_retains_the_same_allocation_inquiry_and_reporting_settings(self):
        original = deepcopy(next(p for p in DEFAULT_TOTAL_WAIT_POLICIES
                                 if p['id'] == 'stable_responsive_C1'))
        for key in ('id', 'label', 'event_review', 'resource_updates'):
            original.pop(key)
        for actual in DEFAULT_EXISTING_INFO_POLICIES:
            actual = deepcopy(actual)
            for key in ('id', 'label', 'event_review', 'resource_updates', 'early_offers'):
                actual.pop(key)
            self.assertEqual(actual, original)

    def test_additional_resource_publications_are_rejected(self):
        with self.assertRaises(ValueError):
            run_existing_info_replay(case(), [policy(resource_updates=True)])

    def test_flags_must_be_explicit_booleans(self):
        for key in ('early_offers', 'event_review', 'resource_updates'):
            for value in (None, 1, 'yes'):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    run_existing_info_replay(case(), [policy(**{key: value})])
            invalid = policy()
            del invalid[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                run_existing_info_replay(case(), [invalid])

    def test_interventions_require_the_frozen_observed_stable_responsive_c1(self):
        for changes in ({'mode': 'perfect'}, {'stable_ties': False}, {'rule': 'H1'},
                        {'prefetch': False}, {'watch': False}, {'grace_minutes': 4}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run_existing_info_replay(case(), [policy(**changes)])

    def test_duplicate_ids_invalid_execution_order_and_mixed_settings_are_rejected(self):
        for specs, order in (([policy(), policy()], None),
                             ([policy(), policy('stable_responsive_C1')], ['existing_info_C1'] * 2),
                             ([policy(), policy('stable_responsive_C1', k=1)], None)):
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                run_existing_info_replay(case(), specs, execution_order=order)

    def test_missing_required_solver_is_not_silently_substituted(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=CPSATDependencyError('test unavailable')):
            with self.assertRaises(CPSATDependencyError):
                run(case(), 'existing_info_C1')


class EarlyOfferDecisionTests(unittest.TestCase):
    def snapshot(self):
        return dict(t=1, patient=dict(id='P01', priority=4, request_t=0,
                    demand={'ed_bed': 1}, required_capabilities=['basic']),
            first_valid_t=1, pending_hospitals=['FAR'],
            valid_offers=[dict(hospital='NEAR', accepted_at=1, expires_at=11,
                              travel_minutes=3, resources={'ed_bed': dict(
                                  status='fresh', free=1, observed_at=1,
                                  received_at=1, source='feed')})],
            structural_candidates=[dict(hospital='NEAR', travel_minutes=3),
                                   dict(hospital='FAR', travel_minutes=8)],
            search_patients=[dict(id='P01', priority=4, request_t=0,
                                 demand={'ed_bed': 1}, required_capabilities=['basic'],
                                 compatible_hospitals=['NEAR', 'FAR'])])

    def test_dominant_live_offer_can_be_ready_using_only_detached_current_input(self):
        value = self.snapshot()
        saved = deepcopy(value)
        self.assertEqual(decide_early_offer(value),
                         dict(eligible=True, hospital='NEAR', reason='EARLY_READY'))
        self.assertEqual(value, saved)

    def test_offer_expiring_now_is_rejected(self):
        value = self.snapshot()
        value['valid_offers'][0]['expires_at'] = value['t']
        self.assertEqual(decide_early_offer(value)['reason'], 'NO_FRESH_OFFER')

    def test_offer_expiring_next_minute_is_valid_at_current_gate_time(self):
        value = self.snapshot()
        value['valid_offers'][0]['expires_at'] = value['t'] + 1
        self.assertTrue(decide_early_offer(value)['eligible'])

    def test_unknown_stale_or_insufficient_resource_report_is_rejected(self):
        for resource in (dict(status='unknown', free=None), dict(status='stale', free=1),
                         dict(status='fresh', free=0)):
            value = self.snapshot()
            value['valid_offers'][0]['resources']['ed_bed'].update(resource)
            with self.subTest(resource=resource):
                self.assertEqual(decide_early_offer(value)['reason'], 'NO_FRESH_OFFER')

    def test_unanswered_nearer_candidate_prevents_short_circuit(self):
        value = self.snapshot()
        value['structural_candidates'][1]['travel_minutes'] = 2
        self.assertEqual(decide_early_offer(value)['reason'], 'NOT_DOMINANT')

    def test_equal_distance_preferred_identity_is_conservatively_preserved(self):
        value = self.snapshot()
        value['structural_candidates'][1] = dict(hospital='A_EQ', travel_minutes=3)
        self.assertEqual(decide_early_offer(value)['reason'], 'NOT_DOMINANT')

    def test_registered_compatible_patient_demand_counts_even_without_any_offer(self):
        value = self.snapshot()
        value['search_patients'].append(dict(id='P02', priority=4, request_t=0,
            demand={'ed_bed': 1}, required_capabilities=['basic', 'scarce'],
            compatible_hospitals=['NEAR']))
        self.assertEqual(decide_early_offer(value)['reason'], 'SHARED_CAPACITY')

    def test_incompatible_patient_does_not_compete_for_this_hospital(self):
        value = self.snapshot()
        value['search_patients'].append(dict(id='P02', priority=4, request_t=0,
            demand={'ed_bed': 3}, required_capabilities=['other'],
            compatible_hospitals=['FAR']))
        self.assertTrue(decide_early_offer(value)['eligible'])

    def test_every_shared_resource_must_cover_the_registered_cohort(self):
        value = self.snapshot()
        value['patient']['demand']['ventilator'] = 1
        value['search_patients'][0]['demand']['ventilator'] = 1
        value['valid_offers'][0]['resources']['ventilator'] = dict(status='fresh', free=1)
        value['valid_offers'][0]['resources']['ed_bed']['free'] = 2
        value['search_patients'].append(dict(id='P02', priority=2, request_t=0,
            demand={'ed_bed': 1, 'ventilator': 1}, compatible_hospitals=['NEAR']))
        self.assertEqual(decide_early_offer(value)['reason'], 'SHARED_CAPACITY')

    def test_current_cohort_with_enough_each_resource_can_depart(self):
        value = self.snapshot()
        value['valid_offers'][0]['resources']['ed_bed']['free'] = 3
        value['search_patients'].append(dict(id='P02', priority=2, request_t=0,
            demand={'ed_bed': 2}, compatible_hospitals=['NEAR']))
        self.assertTrue(decide_early_offer(value)['eligible'])


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class ExistingInfoReplayTests(unittest.TestCase):
    def test_baseline_and_review_only_match_the_two_frozen_source_free_branches(self):
        scenario = case('periodic_release_long_retry')
        names = ('stable_responsive_C1', 'review_stable_C1')
        frozen = run_total_wait_replay(scenario, [deepcopy(next(
            p for p in DEFAULT_TOTAL_WAIT_POLICIES if p['id'] == name)) for name in names])
        current = run(scenario, *names)
        for left, right in zip(frozen['branches'], current['branches']):
            with self.subTest(branch=left['id']):
                for key in ('events', 'patient_outcomes', 'observation_records',
                            'allocation_decisions', 'solver_records', 'protection_records'):
                    self.assertEqual(semantic(left[key]), semantic(right[key]))

    def test_one_patient_avoids_fixed_reply_wait_without_extra_inquiries(self):
        scenario = offer_case()
        result = run(scenario, 'stable_responsive_C1', 'early_stable_C1', 'existing_info_C1')
        old = branch(result, 'stable_responsive_C1')
        for name in ('early_stable_C1', 'existing_info_C1'):
            new = branch(result, name)
            self.assertEqual(patients(old)['P01']['first_commit_t'], 3)
            self.assertEqual(patients(new)['P01']['first_commit_t'], 1)
            self.assertEqual(patients(old)['P01']['treatment_start_t']
                             - patients(new)['P01']['treatment_start_t'], 2)
            self.assertEqual(old['metrics']['inquiries'], new['metrics']['inquiries'])
            self.assertLess(waiting_metrics(scenario, new)['total_wait_sum_minutes'],
                            waiting_metrics(scenario, old)['total_wait_sum_minutes'])

    def test_ample_capacity_improves_every_patient_and_the_whole_sum(self):
        scenario = offer_case(count=2, capacity=2)
        result = run(scenario, 'stable_responsive_C1', 'existing_info_C1')
        old, new = branch(result, 'stable_responsive_C1'), branch(result)
        self.assertEqual(waiting_metrics(scenario, old)['total_wait_sum_minutes']
                         - waiting_metrics(scenario, new)['total_wait_sum_minutes'], 4)
        for pid in patients(old):
            self.assertEqual(patients(old)[pid]['treatment_start_t']
                             - patients(new)[pid]['treatment_start_t'], 2)

    def test_known_competition_blocks_early_departure(self):
        scenario = offer_case(count=3, capacity=2)
        result = run(scenario, 'stable_responsive_C1', 'early_stable_C1')
        old, new = branch(result, 'stable_responsive_C1'), branch(result, 'early_stable_C1')
        self.assertEqual(patients(old), patients(new))
        self.assertFalse([e for e in events(new, 'commit') if e['t'] < 3])

    def test_first_farther_offer_cannot_short_circuit_a_potentially_nearer_hospital(self):
        scenario = offer_case(near_delay=5, far_delay=1)
        result = run(scenario, 'stable_responsive_C1', 'early_stable_C1')
        old, new = branch(result, 'stable_responsive_C1'), branch(result, 'early_stable_C1')
        self.assertEqual(patients(old), patients(new))
        self.assertFalse([e for e in events(new, 'commit') if e['t'] < 3])

    def test_same_received_report_review_reduces_retry_wait_without_publications(self):
        scenario = case('periodic_release_long_retry')
        result = run(scenario, 'stable_responsive_C1', 'existing_info_C1')
        old, new = branch(result, 'stable_responsive_C1'), branch(result)
        self.assertLess(patients(new)['P01']['treatment_start_t'],
                        patients(old)['P01']['treatment_start_t'])
        def source_before_causal_review(value):
            return [{k: v for k, v in e.items() if k != 'event_sequence'}
                    for e in events(value, 'resource_report_received') if e['t'] <= 8]
        self.assertEqual(source_before_causal_review(old), source_before_causal_review(new))
        self.assertTrue(events(new, 'resource_event_review'))
        self.assertFalse(events(new, 'resource_update_captured'))

    def test_resource_reports_never_use_a_new_publication_channel(self):
        for family in ('release_long_retry', 'watch_outage_recovery', 'fresh_control'):
            value = branch(run(case(family), 'existing_info_C1'))
            self.assertFalse(events(value, 'resource_update_cycle'))
            self.assertFalse(events(value, 'resource_update_captured'))
            self.assertFalse(any('publication_id' in e
                                 for e in events(value, 'resource_report_received')))

    def test_persistent_unknown_resource_cannot_be_cured_by_early_offer_maturity(self):
        value = branch(run(case('partial_team_unresolved'), 'existing_info_C1'))
        self.assertIsNone(patients(value)['P03']['treatment_start_t'])
        self.assertFalse(events(value, 'commit', 'P03'))
        self.assertFalse(any(p['id'] == 'P03' for d in value['allocation_decisions']
                             for p in d['inputs']['patients']))

    def test_stale_nearest_quantity_cannot_authorize_early_commit(self):
        scenario = offer_case()
        observation = scenario['observation_config']
        observation.update(feed_interval_minutes=0, confirmation_delay_minutes=8)
        observation['initial_reports'] = [dict(hospital=h['id'], observed_at=-11,
            resources={r: dict(capacity=n, used=0) for r, n in h['capacity'].items()})
            for h in scenario['hospitals']]
        value = branch(run(scenario, 'existing_info_C1'))
        self.assertFalse([e for e in events(value, 'commit') if e['t'] < 8])
        self.assertTrue(events(value, 'resource_confirmation_request'))

    def test_missing_nearest_quantity_cannot_authorize_early_commit(self):
        scenario = offer_case()
        observation = scenario['observation_config']
        observation.update(feed_interval_minutes=0, confirmation_delay_minutes=8,
                           initial_reports=[])
        value = branch(run(scenario, 'existing_info_C1'))
        self.assertFalse([e for e in events(value, 'commit') if e['t'] < 8])
        self.assertTrue(events(value, 'resource_confirmation_request'))

    def test_silent_hospital_still_leaves_every_patient_unassigned(self):
        value = branch(run(case('missing_unresolved'), 'existing_info_C1'))
        self.assertEqual(value['metrics']['unassigned'], 3)
        self.assertFalse(events(value, 'commit'))
        self.assertFalse(events(value, 'resource_update_captured'))

    def test_expired_patient_offer_requires_a_later_new_acceptance(self):
        value = branch(run(case('expiry_before_slow_confirmation'), 'existing_info_C1'))
        expires = events(value, 'expire', 'P03')
        self.assertTrue(expires)
        for commit in events(value, 'commit', 'P03'):
            self.assertTrue(any(e['t'] > expires[0]['t'] and e['t'] <= commit['t']
                                for e in events(value, 'accept', 'P03')))
        for decision in value['allocation_decisions']:
            for patient in decision['inputs']['patients']:
                self.assertTrue(all(expiry > decision['t']
                                    for expiry in patient['valid_offer_expiries'].values()))

    def test_ignored_old_reports_never_authorize_event_review(self):
        value = branch(run(case('out_of_order'), 'existing_info_C1'))
        ignored = {e['event_sequence'] for e in events(value, 'resource_report_received')
                   if not e['applied']}
        self.assertTrue(ignored)
        self.assertFalse(any(e['report_event_sequence'] in ignored
                             for e in events(value, 'resource_event_review')))

    def test_a_future_patient_is_not_used_to_block_a_present_offer(self):
        scenario = offer_case(count=2, capacity=1)
        future = scenario['patients'][1]
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            future[key] = 12
        value = branch(run(scenario, 'existing_info_C1'))
        self.assertEqual(patients(value)['P01']['first_commit_t'], 1)
        self.assertFalse([e for e in events(value, 'ask', 'P02') if e['t'] < 12])
        self.assertFalse([e for e in events(value, 'resource_event_review', 'P02') if e['t'] < 12])

    def test_current_fresh_quantities_and_live_offers_are_required_for_every_solver_input(self):
        value = branch(run(case('slow_confirmation_eight'), 'existing_info_C1'))
        for decision in value['allocation_decisions']:
            self.assertTrue(all(row['free'] is not None
                                for row in decision['inputs']['free_resources']))
            self.assertTrue(all(row['status'] == 'fresh'
                                for data in decision['resource_evidence'].values()
                                for row in data.values()))
            self.assertTrue(all(p['request_t'] <= decision['t']
                                for p in decision['inputs']['patients']))

    def test_early_maturity_does_not_send_extra_resource_confirmations_in_fresh_case(self):
        result = run(offer_case(count=2), 'stable_responsive_C1', 'existing_info_C1')
        for value in result['branches']:
            self.assertFalse(events(value, 'resource_confirmation_request'))
            self.assertEqual(value['metrics']['inquiries'], 4)

    def test_early_commit_does_not_erase_submitted_hospital_work(self):
        scenario = offer_case()
        scenario['inquiry_queue'] = dict(enabled=True, workers=1)
        result = run(scenario, 'stable_responsive_C1', 'existing_info_C1')
        old, new = branch(result, 'stable_responsive_C1'), branch(result)
        self.assertEqual(old['inquiry_queue_report'], new['inquiry_queue_report'])
        self.assertEqual(new['inquiry_queue_report']['aggregate']['submitted'], 2)
        self.assertEqual(new['inquiry_queue_report']['aggregate']['completed'], 2)
        self.assertLess(patients(new)['P01']['first_commit_t'],
                        patients(old)['P01']['first_commit_t'])

    def test_early_offer_does_not_remove_the_final_physical_conflict_gate(self):
        value = branch(run(offer_case(), 'existing_info_C1'))
        self.assertTrue(events(value, 'commit'))
        by_seq = {e['event_sequence']: e for e in value['events'] if 'event_sequence' in e}
        for decision in value['allocation_decisions']:
            for gate in decision['gate_records']:
                self.assertGreater(gate['event_sequence'], decision['event_sequence'])
                if gate['passed']:
                    commit = by_seq[gate['commit_event_sequence']]
                    self.assertEqual(commit['type'], 'commit')
                    self.assertGreater(commit['event_sequence'], gate['event_sequence'])

    def test_unannounced_future_outage_can_make_earlier_departure_harmful(self):
        scenario = offer_case(capacity=1)
        scenario['exogenous_events'] = [dict(type='resource_unavailable', t=2, until=6,
                                           hospital='NEAR', resource='ed_bed')]
        result = run(scenario, 'stable_responsive_C1', 'existing_info_C1')
        old, new = branch(result, 'stable_responsive_C1'), branch(result)
        self.assertEqual(patients(old)['P01']['treatment_start_t'], 13)
        self.assertEqual(patients(new)['P01']['treatment_start_t'], 18)
        self.assertGreater(waiting_metrics(scenario, new)['total_wait_sum_minutes'],
                           waiting_metrics(scenario, old)['total_wait_sum_minutes'])
        self.assertTrue(events(new, 'arrival_unable', 'P01'))
        self.assertFalse(new['metrics']['whole_episode_optimal_proven'])

    def test_intrinsic_occupied_bed_wait_remains(self):
        scenario = case('intrinsic_single_hospital_queue')
        value = branch(run(scenario, 'existing_info_C1'))
        starts = sorted(p['treatment_start_t'] for p in value['patient_outcomes'])
        self.assertTrue(all(t is not None for t in starts))
        duration = scenario['patients'][0]['treatment_minutes']
        self.assertGreaterEqual(starts[1] - starts[0], duration)
        self.assertGreaterEqual(starts[2] - starts[1], duration)

    def test_solver_view_contains_no_environment_timing_or_patient_future(self):
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
            run(offer_case(count=2), 'existing_info_C1')
        self.assertTrue(calls)

    def test_early_review_snapshot_has_no_reply_schedule_duration_or_future_patient(self):
        value = branch(run(offer_case(), 'existing_info_C1'))
        self.assertTrue(value['early_offer_records'])
        for record in value['early_offer_records']:
            snapshot = record['snapshot']
            self.assertEqual(set(snapshot), {'t', 'patient', 'first_valid_t',
                'pending_hospitals', 'valid_offers', 'structural_candidates', 'search_patients'})
            self.assertTrue(all(isinstance(hid, str) for hid in snapshot['pending_hospitals']))
            for patient in [snapshot['patient']] + snapshot['search_patients']:
                self.assertLessEqual(patient['request_t'], snapshot['t'])
                self.assertFalse({'duration', 'treatment_minutes', 'due_at', 'at',
                                  'silent', 'arrive_t'} & set(patient))
            for candidate in snapshot['structural_candidates']:
                self.assertEqual(set(candidate), {'hospital', 'travel_minutes'})

    def test_execution_rotation_and_input_objects_do_not_change_outcomes(self):
        scenario = offer_case(count=2)
        saved = deepcopy(scenario)
        specs = [policy(name) for name in ('stable_responsive_C1', 'review_stable_C1',
                                          'early_stable_C1', 'existing_info_C1')]
        saved_specs = deepcopy(specs)
        ids = [p['id'] for p in specs]
        one = run_existing_info_replay(scenario, specs, execution_order=ids)
        two = run_existing_info_replay(scenario, specs, execution_order=ids[::-1])
        self.assertEqual(scenario, saved)
        self.assertEqual(specs, saved_specs)
        for left, right in zip(one['branches'], two['branches']):
            self.assertEqual(semantic(left), semantic(right))


if __name__ == '__main__':
    unittest.main()
