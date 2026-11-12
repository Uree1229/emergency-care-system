"""Structural cases only: reserved development/validation seeds are not run."""
from copy import deepcopy
import unittest

from simulation.existing_info_scenarios import (
    ADVERSARIAL_FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, FAMILIES,
    ORIGINAL_FAMILIES, build_existing_info_scenarios,
)
from simulation.map_replay import validate_scenario
from simulation.total_wait_scenarios import build_total_wait_scenarios


class ExistingInfoScenarioTests(unittest.TestCase):
    def case(self, family):
        return build_existing_info_scenarios(7, [family])[0]

    def test_manifest_and_seed_partitions(self):
        self.assertEqual(len(ORIGINAL_FAMILIES), 31)
        self.assertEqual(len(ADVERSARIAL_FAMILIES), 8)
        self.assertEqual(len(FAMILIES), 39)
        self.assertEqual(len(set(FAMILIES)), 39)
        self.assertEqual(DEVELOPMENT_SEEDS, (27101, 27102, 27103))
        self.assertEqual(VALIDATION_SEEDS, tuple(range(28101, 28107)))
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))

    def test_all_cases_are_valid_and_additional_channel_is_disabled(self):
        cases = build_existing_info_scenarios(7)
        self.assertEqual([c['family'] for c in cases], list(FAMILIES))
        for case in cases:
            validate_scenario(case)
            self.assertTrue(case['synthetic'])
            self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
            self.assertEqual(case['experiment']['patient_count'], len(case['patients']))

    def test_inherited_environment_is_exact(self):
        before = build_total_wait_scenarios(7)
        after = build_existing_info_scenarios(7, ORIGINAL_FAMILIES)
        descriptive = {'id', 'assumptions', 'experiment', 'existing_info_family'}
        for original, new in zip(before, after):
            self.assertEqual({k: v for k, v in original.items() if k not in descriptive},
                             {k: v for k, v in new.items() if k not in descriptive})

    def test_requested_order_does_not_change_environment(self):
        chosen = list(reversed(ADVERSARIAL_FAMILIES))
        separate = {family: self.case(family) for family in chosen}
        together = build_existing_info_scenarios(7, chosen)
        self.assertEqual([c['family'] for c in together], chosen)
        self.assertEqual(together, [separate[family] for family in chosen])

    def test_detached_mutations_cannot_contaminate_later_calls(self):
        original = self.case('dominant_near_slow_other')
        dirty = deepcopy(original)
        dirty['patients'][0]['resource_demands']['ed_bed'] = 99
        dirty['hospitals'][0]['capacity']['ed_bed'] = 99
        dirty['observation_config']['feed_interval_minutes'] = 99
        self.assertEqual(self.case('dominant_near_slow_other'), original)

    def test_small_seed_geometry_and_treatment_variation_remain_supported(self):
        cases = build_existing_info_scenarios(8, ADVERSARIAL_FAMILIES)
        self.assertEqual(len(cases), 8)
        for case in cases:
            validate_scenario(case)
            self.assertEqual(case['seed'], 8)

    def test_closest_offer_arrives_before_unresolved_far_offer(self):
        case = self.case('dominant_near_slow_other')
        self.assertEqual(len(case['patients']), 1)
        self.assertEqual(case['response_environment']['*']['NEAR']['delay'], 1)
        self.assertEqual(case['response_environment']['*']['FAR']['delay'], 5)
        self.assertEqual(case['patients'][0]['resource_demands'], {'ed_bed': 1})
        self.assertGreaterEqual(case['hospitals'][0]['capacity']['ed_bed'], 1)

    def test_ample_reported_capacity_covers_entire_registered_cohort(self):
        case = self.case('ample_cohort_dominant_offer')
        demand = sum(p['resource_demands']['ed_bed'] for p in case['patients'])
        self.assertEqual(len(case['patients']), 2)
        self.assertEqual(demand, case['hospitals'][0]['capacity']['ed_bed'])
        self.assertEqual({p['search_start_t'] for p in case['patients']}, {0})
        self.assertEqual({tuple(p['required_capabilities']) for p in case['patients']},
                         {(case['patients'][0]['required_capabilities'][0],)})

    def test_farther_fast_reply_cannot_remove_nearer_pending_alternative(self):
        case = self.case('nearer_offer_pending')
        self.assertEqual(case['response_environment']['*']['NEAR']['delay'], 2)
        self.assertEqual(case['response_environment']['*']['FAR']['delay'], 1)
        self.assertEqual(case['experiment']['nearest_hospital_id'], 'NEAR')

    def test_pending_scarce_patient_is_registered_but_not_yet_accepted(self):
        case = self.case('competing_scarce_pending')
        flexible, scarce = case['patients']
        self.assertEqual(flexible['search_start_t'], scarce['search_start_t'])
        self.assertGreater(scarce['priority'], flexible['priority'])
        self.assertLess(case['hospitals'][0]['capacity']['ed_bed'],
                        sum(p['resource_demands']['ed_bed'] for p in case['patients']))
        self.assertEqual(case['response_environment'][scarce['id']]['NEAR']['delay'], 2)
        self.assertGreater(len(scarce['required_capabilities']),
                           len(flexible['required_capabilities']))

    def test_unknown_offer_has_no_initial_quantities_or_periodic_backdoor(self):
        case = self.case('dominant_offer_unknown')
        self.assertEqual(case['observation_config']['initial_reports'], [])
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 0)
        self.assertEqual(case['observation_config']['confirmation_delay_minutes'], 4)

    def test_stale_offer_reports_are_older_than_source_freshness_limit(self):
        case = self.case('dominant_offer_stale')
        self.assertEqual(case['observation_config']['feed_interval_minutes'], 0)
        reports = case['observation_config']['initial_reports']
        self.assertEqual({r['observed_at'] for r in reports}, {-11})
        self.assertEqual({r['hospital'] for r in reports},
                         {h['id'] for h in case['hospitals']})
        self.assertEqual(case['observation_config']['confirmation_delay_minutes'], 4)

    def test_future_outage_remains_an_exogenous_event_after_offer(self):
        case = self.case('future_outage_after_early_offer')
        event, = case['exogenous_events']
        self.assertEqual(event, dict(type='resource_unavailable', t=2, until=10,
                                     hospital='NEAR', resource='ed_bed'))
        self.assertLess(case['response_environment']['*']['NEAR']['delay'], event['t'])
        self.assertEqual(case['hospitals'][0]['capacity']['ed_bed'], 1)

    def test_expiring_offer_lifetime_is_one_minute(self):
        case = self.case('expiring_dominant_offer')
        self.assertEqual(case['accept_ttl_minutes'], 1)
        self.assertEqual(case['experiment']['first_near_offer_expiry_minute'], 2)
        self.assertEqual(case['experiment']['intent'],
                         'current_valid_offer_may_be_used_before_expiry')

    def test_invalid_seeds_fail_before_build(self):
        for seed in (True, False, -1, 7.0, '7', None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_existing_info_scenarios(seed)

    def test_invalid_family_selection_fails(self):
        for families in ('fresh_control', b'fresh_control', 7, [None], ['unknown'],
                         ['fresh_control', 'fresh_control']):
            with self.subTest(families=families), self.assertRaises(ValueError):
                build_existing_info_scenarios(7, families)

    def test_empty_family_selection_is_supported(self):
        self.assertEqual(build_existing_info_scenarios(7, []), [])


if __name__ == '__main__':
    unittest.main()
