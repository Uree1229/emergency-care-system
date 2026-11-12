"""Exact source-clock boundaries for the two predeclared probe vetoes."""
from copy import deepcopy
import unittest

from simulation.probe_guard_policy import VARIANTS, PROBE_GUARD_SPEC, decide_probe_guard
from simulation.shortage_probe_engine import decide_shortage_probe
from test_shortage_probe_engine import snapshot


def observed_snapshot(observed_at=0, received_at=0):
    value = snapshot()
    bed = value['candidates'][0]['resources']['ed_bed']
    bed.update(observed_at=observed_at, received_at=received_at,
        age_minutes=value['t'] - observed_at)
    return value


class ProbeGuardPolicyTests(unittest.TestCase):
    def test_two_fixed_variants_and_no_extra_threshold(self):
        self.assertEqual(VARIANTS, ('off', 'renewed'))
        self.assertFalse(PROBE_GUARD_SPEC['additional_age_or_quantity_threshold'])
        self.assertTrue(PROBE_GUARD_SPEC['original_probe_decision_is_shadow_only'])

    def test_off_vetoes_the_original_selected_probe(self):
        value = snapshot()
        self.assertEqual(decide_shortage_probe(value)['send'], ['H01'])
        decision = decide_probe_guard('off', snapshot=value)
        self.assertEqual((decision['action'], decision['reason']),
            ('veto', 'ALL_OPTIONAL_PROBES_DISABLED'))
        self.assertEqual(decision['original_send'], ['H01'])

    def test_equal_source_clock_delegates_without_veto(self):
        decision = decide_probe_guard('renewed', snapshot=observed_snapshot())
        self.assertEqual(decision['action'], 'delegate')
        self.assertEqual(decision['first_ask_submitted_at'], decision['selected_bed_observed_at'])

    def test_strictly_later_source_clock_vetoes(self):
        decision = decide_probe_guard('renewed', snapshot=observed_snapshot(.25, .25))
        self.assertEqual((decision['action'], decision['reason']),
            ('veto', 'POST_INITIAL_ASK_SHORTAGE_OBSERVATION'))
        self.assertEqual(decision['selected_bed_observed_at'], .25)

    def test_later_receipt_alone_does_not_veto(self):
        decision = decide_probe_guard('renewed', snapshot=observed_snapshot(0, .5))
        self.assertEqual(decision['action'], 'delegate')

    def test_unselected_hospital_timestamp_does_not_change_original_selection(self):
        value = snapshot()
        other = deepcopy(value['candidates'][0])
        other.update(hospital='H03', travel_minutes=4)
        other['resources']['ed_bed'].update(observed_at=.25, received_at=.25, age_minutes=.25)
        value['candidates'].append(other)
        decision = decide_probe_guard('renewed', snapshot=value)
        self.assertEqual((decision['selected_hospital'], decision['action']), ('H01', 'delegate'))

    def test_guard_does_not_replace_an_original_nonselection(self):
        value = observed_snapshot(.25, .25)
        value['probe_count'] = 1
        decision = decide_probe_guard('renewed', snapshot=value)
        self.assertEqual(decision['action'], 'delegate')
        self.assertEqual(decision['reason'], 'NO_ORIGINAL_PROBE_SELECTED')
        self.assertEqual(decision['original_send'], [])
        self.assertIsNone(decision['selected_bed_observed_at'])

    def test_off_also_vetoes_a_due_review_with_no_selected_probe(self):
        value = snapshot()
        value['probe_count'] = 1
        self.assertEqual(decide_probe_guard('off', snapshot=value)['action'], 'veto')

    def test_original_exact_schema_validation_is_retained(self):
        changes = (('top', 'future_release', 1), ('candidate', 'finish_t', 1),
            ('patient', 'silent', True), ('view', 'remaining_work', 1))
        for variant in VARIANTS:
            for location, key, entry in changes:
                value = snapshot()
                row = value if location == 'top' else value['candidates'][0] if location == 'candidate' \
                    else value['patient'] if location == 'patient' else value['candidates'][0]['resources']['ed_bed']
                row[key] = entry
                with self.subTest(variant=variant, location=location):
                    with self.assertRaises(ValueError):
                        decide_probe_guard(variant, snapshot=value)

    def test_input_and_original_shadow_decision_are_not_mutated(self):
        for variant in VARIANTS:
            value = observed_snapshot(.25, .25)
            before = deepcopy(value)
            original = decide_shortage_probe(value)
            decision = decide_probe_guard(variant, snapshot=value)
            self.assertEqual(value, before)
            self.assertEqual(decide_shortage_probe(value), original)
            decision['original_send'].append('unrelated')
            self.assertEqual(decide_shortage_probe(value)['send'], ['H01'])

    def test_first_ask_absence_uses_original_nonselection(self):
        value = snapshot()
        value['patient']['first_ask'] = None
        decision = decide_probe_guard('renewed', snapshot=value)
        self.assertEqual(decision['action'], 'delegate')
        self.assertIsNone(decision['first_ask_submitted_at'])

    def test_unknown_variant_is_rejected(self):
        with self.assertRaises(ValueError):
            decide_probe_guard('selective', snapshot=snapshot())


if __name__ == '__main__':
    unittest.main()
