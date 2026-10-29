"""Input preservation and actual boundaries, without looking at reserved seeds."""
from copy import deepcopy
import unittest

from simulation import public_offer_scenarios as new
from simulation import probe_guard_scenarios as old


class PublicOfferScenariosTests(unittest.TestCase):
    def test_all_old_seed7_inputs_are_exact_and_detached(self):
        for family in old.FAMILIES:
            with self.subTest(family=family):
                case = new.make_scenario(7, family)
                self.assertEqual(case, old.make_scenario(7, family))
                case['patients'][0]['priority'] = 999
                self.assertNotEqual(case, new.make_scenario(7, family))

    def test_new_boundaries_validate_and_family_order_is_detached(self):
        cases = new.build_public_offer_scenarios(7, new.ADDITIONAL_FAMILIES)
        reverse = new.build_public_offer_scenarios(7, reversed(new.ADDITIONAL_FAMILIES))
        self.assertEqual(cases, list(reversed(reverse)))
        for case in cases:
            self.assertEqual(case['experiment']['patient_count'], len(case['patients']))
            self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])

    def test_future_outage_twin_changes_only_declared_environment_and_metadata(self):
        base = new.make_scenario(7, 'public_offer_fresh_shortage')
        twin = new.make_scenario(7, 'public_offer_future_outage')
        for field in ('patients', 'hospitals', 'response_matrix', 'observation_config', 'inquiry_queue'):
            self.assertEqual(base[field], twin[field])
        self.assertEqual(twin['exogenous_events'], [dict(type='resource_unavailable', t=4, until=6,
            hospital='H03', resource='ed_bed')])

    def test_later_patient_is_future_environment_not_in_common_patient_rows(self):
        base = new.make_scenario(7, 'public_offer_fresh_shortage')
        twin = new.make_scenario(7, 'public_offer_later_scarce_patient')
        self.assertEqual(base['patients'], twin['patients'][:2])
        late = twin['patients'][2]
        self.assertEqual(late['search_start_t'], 2)
        compatible = [h['id'] for h in twin['hospitals'] if set(late['required_capabilities']) <= set(h['capabilities'])]
        self.assertEqual(compatible, ['H03'])

    def test_second_resource_boundary_has_bed_without_required_team(self):
        case = new.make_scenario(7, 'public_offer_second_required_resource')
        self.assertEqual(case['patients'][0]['resource_demands'], dict(ed_bed=2, special_team=1))
        h = case['hospitals'][0]
        self.assertEqual(h['initial_occupancy'][0]['demand'], dict(special_team=3))

    def test_invalid_input_and_generator_selection(self):
        for seed in (True, -1, 1.2, '7'):
            with self.assertRaises(ValueError): new.make_scenario(seed, new.ADDITIONAL_FAMILIES[0])
        for families in ('bad', ['bad'], [new.FAMILIES[0]] * 2, 1):
            with self.assertRaises(ValueError): new.build_public_offer_scenarios(7, families)
        self.assertEqual(new.build_public_offer_scenarios(7, []), [])


if __name__ == '__main__':
    unittest.main()
