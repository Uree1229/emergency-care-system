"""Audit guards against missing comparisons and misleading matched denominators."""
from copy import deepcopy
import statistics
import unittest

from simulation.observed_audit import (
    bootstrap_seed_mean, common_patient_differences, paired_overall, validate_records,
)


def row(policy, seed, starts, asks=(1, 1, 1)):
    outcomes = []
    for i, (start, inquiry_count) in enumerate(zip(starts, asks)):
        outcomes.append(dict(id=('A', 'B', 'C')[i], priority=(4, 1, 2)[i],
            search_start_t=10, first_commit_t=10 if start is not None else None,
            final_arrive_t=12 if start is not None else None,
            treatment_start_t=start, inquiries=inquiry_count))
    metrics = dict(patients=3, inquiries=sum(asks))
    for field, count_key, mean_key in (
        ('treatment_start_t', 'start_observed', 'start_mean_minutes'),
        ('final_arrive_t', 'arrival_observed', 'arrival_mean_minutes'),
        ('first_commit_t', 'commit_observed', 'commit_mean_minutes'),
    ):
        times = [o[field] - o['search_start_t'] for o in outcomes if o[field] is not None]
        metrics[count_key] = len(times)
        metrics[mean_key] = statistics.mean(times) if times else None
    metrics.update(not_treated=3 - metrics['start_observed'],
                   not_arrived=3 - metrics['arrival_observed'])
    return dict(policy_id=policy, scenario_id='S', seed=seed,
                outcomes=outcomes, metrics=metrics)


def fixture():
    return [row('B0', 1, (20, 30, None)), row('V2', 1, (22, 32, None)),
            row('B0', 2, (20, None, 30)), row('V2', 2, (None, 40, 22))]


class ObservedAuditTests(unittest.TestCase):
    def validate(self, records):
        return validate_records(records, {'B0', 'V2'}, {('S', 1), ('S', 2)})

    def test_complete_matching_input_coverage_and_denominators_pass(self):
        records = fixture()
        before = deepcopy(records)
        cells = self.validate(records)
        self.assertEqual(set(cells), {('S', 1), ('S', 2)})
        self.assertEqual(records, before)

    def test_missing_policy_seed_or_duplicate_branch_cannot_silently_pass(self):
        records = fixture()
        for changed in (records[:-1], records[:2], records + [deepcopy(records[0])]):
            with self.subTest(count=len(changed)), self.assertRaises(ValueError):
                self.validate(changed)

    def test_changed_patient_priority_or_search_time_rejects_unfair_comparison(self):
        for key, value in (('priority', 1), ('search_start_t', 9)):
            records = fixture()
            records[1]['outcomes'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(records)

    def test_tampered_outcome_averages_counts_or_inquiry_totals_are_rejected(self):
        for key, value in (('start_mean_minutes', 999), ('start_observed', 3),
                           ('patients', 2), ('not_treated', 0), ('inquiries', 99)):
            records = fixture()
            records[1]['metrics'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(records)

    def test_common_patient_pairing_does_not_equate_same_count_with_same_ids(self):
        before, after = fixture()[2:]
        self.assertEqual(before['metrics']['start_observed'], after['metrics']['start_observed'])
        self.assertEqual(common_patient_differences(before, after), [-8])

    def test_paired_aggregation_weights_seeds_and_preserves_identity_switches(self):
        cells = self.validate(fixture())
        result = paired_overall(cells, 'B0', 'V2', samples=100, rng_seed=3)
        self.assertEqual(result['common_patients'], 3)
        self.assertEqual(result['mean_difference_minutes'], -3)
        self.assertAlmostEqual(result['pooled_common_patient_mean_minutes'], -4 / 3)
        self.assertEqual(result['regression_seed_fraction'], .5)
        self.assertEqual(result['treated_identity_switches'],
            [dict(scenario_id='S', seed=2, baseline_only=['A'], selected_only=['B'])])
        self.assertEqual(result['per_seed'][1]['common_patients'], 1)

    def test_bootstrap_is_deterministic_and_resamples_whole_seed_values(self):
        values = [-8, 2]
        before = list(values)
        expected = bootstrap_seed_mean(values, samples=1000, rng_seed=73131)
        self.assertEqual(expected, [-8, 2])
        self.assertEqual(expected, bootstrap_seed_mean(values, samples=1000, rng_seed=73131))
        self.assertEqual(bootstrap_seed_mean([2, 2, 2], samples=100), [2, 2])
        self.assertEqual(values, before)

    def test_bootstrap_rejects_unavailable_nonfinite_or_insufficient_inputs(self):
        for values in ([], [float('nan')], [float('inf')]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                bootstrap_seed_mean(values)
        for samples in (0, 99, True, 100.5):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                bootstrap_seed_mean([1], samples=samples)


if __name__ == '__main__':
    unittest.main()
