"""Exact inherited inputs and native-budget boundary declarations; seed 7 only."""
import json
import unittest
from simulation.c1_incumbent_scenarios import (FAMILIES,ORIGINAL_FAMILIES,ADDITIONAL_FAMILIES,
    make_scenario,build_c1_incumbent_scenarios)
from simulation.offer_reserve_scenarios import make_scenario as previous
from simulation.map_replay import validate_scenario
from simulation.joint_policy_scenarios import _validate_extra_inputs


class IncumbentScenariosTests(unittest.TestCase):
    def test_every_inherited_family_is_byte_for_value_identical(self):
        self.assertEqual(len(ORIGINAL_FAMILIES),149)
        for family in ORIGINAL_FAMILIES:
            with self.subTest(family=family):
                encode=lambda x:json.dumps(x,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
                self.assertEqual(encode(make_scenario(7,family)),encode(previous(7,family)))

    def test_fixed_six_new_families_pass_both_original_validators(self):
        self.assertEqual(len(ADDITIONAL_FAMILIES),6)
        for family in ADDITIONAL_FAMILIES:
            case=make_scenario(7,family)
            validate_scenario(case);_validate_extra_inputs(case)
            self.assertEqual(case['experiment']['c1_budget_seconds'],.001)
            self.assertTrue(case['experiment']['no_fault_injection'])
            self.assertTrue(case['experiment']['no_clock_mocking'])

    def test_private_future_twin_has_the_same_prior_hospitals_and_patients(self):
        base=make_scenario(7,'c1_incumbent_native_budget')
        adverse=make_scenario(7,'c1_incumbent_adverse_resource')
        self.assertEqual(base['patients'],adverse['patients'])
        self.assertEqual(base['hospitals'],adverse['hospitals'])
        self.assertEqual(base['observation_config'],adverse['observation_config'])
        self.assertEqual(adverse['exogenous_events'][0]['t'],9)

    def test_builder_rejects_invalid_inputs_and_preserves_requested_order(self):
        for families in ('out_of_order',['out_of_order']*2,['unknown'],42):
            with self.assertRaises(ValueError):build_c1_incumbent_scenarios(7,families)
        for seed in (True,-1,7.0):
            with self.assertRaises(ValueError):make_scenario(seed,FAMILIES[0])
        chosen=[ADDITIONAL_FAMILIES[-1],FAMILIES[0]]
        self.assertEqual([s['family'] for s in build_c1_incumbent_scenarios(7,chosen)],chosen)

    def test_generation_is_detached_and_deterministic(self):
        family=ADDITIONAL_FAMILIES[0]
        one=make_scenario(7,family);two=make_scenario(7,family)
        self.assertEqual(one,two)
        one['patients'][0]['priority']=1
        self.assertNotEqual(one,two)
        self.assertEqual(two,make_scenario(7,family))


if __name__=='__main__':unittest.main()
