from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from simulation import recovery_dispatch_experiment as experiment
from test_joint_policy_experiment import summary


def case(eager=8, latched=9, reference=10, queries=3):
    policies = experiment.policies_for('development', cached=True)
    rows = {p['id']: summary(reference, 3) for p in policies}
    for allocation in ('H1', 'C1'):
        for variant, time in (('eager', eager), ('latched', latched)):
            rows[experiment.candidate_id(allocation, variant)] = summary(time, queries)
    for row in rows.values():
        for field in experiment.AGGREGATE_FIELDS:
            row.setdefault(field, 0)
    pairs = experiment.pairs_for('development')
    return dict(family=experiment.FAMILIES[0], summaries=rows,
        comparisons={name: experiment.previous.compare(rows[a], rows[b]) for name, a, b in pairs})


class RecoveryDispatchExperimentTests(unittest.TestCase):
    def test_fresh_references_include_every_declared_negative_comparator(self):
        ids = {p['id'] for p in experiment.fresh_references()}
        self.assertEqual(len(ids), 10)
        for allocation in ('H1', 'C1'):
            self.assertTrue({f'retry_{allocation}_frontier', f'v4_{allocation}_safe',
                f'v4_{allocation}_probe', f'v4_{allocation}_baseline', f'fixed3_{allocation}_baseline'} <= ids)

    def test_only_preceding_development_can_use_fourteen_cached_references(self):
        self.assertEqual(len(experiment.cached_references()), 14)
        self.assertEqual(len(experiment.policies_for('development', cached=True)), 18)
        self.assertEqual(len(experiment.policies_for('development')), 14)

    def test_validation_contains_only_fixed_choices(self):
        selected = experiment.select_policy([case()])
        selected['H1']['variant'] = 'latched'
        policies = experiment.policies_for('validation', selected)
        self.assertEqual(len(policies), 12)
        self.assertEqual([(p['allocation'], p['variant']) for p in policies if p.get('recovery_dispatch')],
            [('H1', 'latched'), ('C1', 'eager')])

    def test_validation_cannot_reuse_old_development_references(self):
        with self.assertRaises(ValueError):
            experiment.policies_for('validation', experiment.select_policy([case()]), cached=True)

    def test_preserved_inquiry_count_allows_latency_repair(self):
        result = experiment.assess([case()], 'H1', 'eager')
        self.assertTrue(result['primary_passed'])
        self.assertTrue(result['primary_checks']['bounded_inquiry_cost'])
        self.assertFalse(experiment.SELECTION_RULE['strict_total_inquiry_reduction_required'])

    def test_one_case_query_growth_cannot_hide_in_population_query_reduction(self):
        result = experiment.assess([case(queries=0), case(queries=4)], 'H1', 'eager')
        self.assertTrue(result['primary_checks']['bounded_inquiry_cost'])
        self.assertFalse(result['primary_checks']['no_case_inquiry_increase'])
        self.assertFalse(result['primary_passed'])

    def test_large_gain_does_not_hide_one_individual_harm(self):
        result = experiment.assess([case(reference=30), case(eager=11)], 'H1', 'eager')
        self.assertFalse(result['primary_checks']['no_individual_treatment_worsening'])
        self.assertFalse(result['primary_passed'])

    def test_arrival_harm_is_independent_of_treatment_harm(self):
        row = case()
        row['summaries']['recovery_H1_eager']['patient_rows'][0]['final_arrival_capped_minutes'] = 11
        result = experiment.assess([row], 'H1', 'eager')
        self.assertTrue(result['primary_checks']['no_individual_treatment_worsening'])
        self.assertFalse(result['primary_checks']['no_individual_final_arrival_worsening'])

    def test_lost_success_at_censor_tie_is_rejected(self):
        row = case(eager=10)
        row['summaries']['recovery_H1_eager']['patient_rows'][0]['treated'] = False
        self.assertFalse(experiment.assess([row], 'H1', 'eager')['primary_checks']['no_lost_treatments'])

    def test_case_total_wait_is_not_replaced_by_aggregate_wait(self):
        first, second = case(reference=30), case()
        second['summaries']['recovery_H1_eager']['total_wait_sum_minutes'] = 8
        checks = experiment.assess([first, second], 'H1', 'eager')['primary_checks']
        self.assertTrue(checks['strict_total_wait_reduction'])
        self.assertFalse(checks['no_case_total_wait_increase'])

    def test_capped_treatment_sum_is_separately_required(self):
        row = case()
        row['summaries']['recovery_H1_eager']['request_to_treatment_capped_sum_minutes'] = 11
        checks = experiment.assess([row], 'H1', 'eager')['primary_checks']
        self.assertFalse(checks['no_case_capped_treatment_increase'])
        self.assertFalse(checks['strict_capped_treatment_reduction'])

    def test_no_time_gain_cannot_qualify(self):
        self.assertFalse(experiment.assess([case(eager=10)], 'H1', 'eager')['primary_passed'])

    def test_safety_wins_over_larger_total_improvement(self):
        rows = [case(eager=1, latched=9, reference=30), case(eager=11, latched=9)]
        selected = experiment.select_policy(rows)
        self.assertEqual({r['variant'] for r in selected.values()}, {'latched'})
        self.assertTrue(all(r['primary_passed'] for r in selected.values()))

    def test_primary_pass_does_not_bypass_safe_reference_gate(self):
        row = case()
        row['summaries']['v4_H1_safe'] = summary(7, 3)
        result = experiment.assess([row], 'H1', 'eager')
        self.assertTrue(result['primary_passed'])
        self.assertFalse(result['deployment_eligible'])

    def test_primary_pass_does_not_bypass_prototype_population_gate(self):
        row = case()
        row['summaries']['v4_H1_probe'] = summary(7, 3)
        self.assertFalse(experiment.assess([row], 'H1', 'eager')['gates']['vs_prototype_population'])

    def test_research_never_automatically_adopts(self):
        selected = experiment.select_policy([case()])
        self.assertTrue(all(r['deployment_eligible'] for r in selected.values()))
        self.assertTrue(all(r['adopted'] is False and r['live_default'] == 'fixed_3' for r in selected.values()))

    def test_selection_does_not_mutate_any_saved_case(self):
        row = case()
        before = deepcopy(row)
        experiment.select_policy([row])
        self.assertEqual(before, row)

    def test_totals_expose_different_cached_reference_denominators(self):
        first, second = case(), case()
        second['summaries'].pop('v4_H1_capability')
        totals = experiment.totals([first, second], ['v4_H1_capability', 'recovery_H1_eager'])
        self.assertEqual(totals['v4_H1_capability']['cases'], 1)
        self.assertEqual(totals['recovery_H1_eager']['cases'], 2)

    def test_all_preceding_scientific_sources_remain_in_protocol(self):
        self.assertEqual(len(experiment.SOURCE_FILES), 156)
        self.assertTrue(set(experiment.previous.SOURCE_FILES) <= set(experiment.SOURCE_FILES))
        self.assertEqual(tuple(experiment.DEVELOPMENT_SEEDS), (44101, 44102, 44103))
        self.assertEqual(tuple(experiment.VALIDATION_SEEDS), tuple(range(46101, 46107)))

    def test_reserved_validation_needs_fixed_selection(self):
        with self.assertRaises(ValueError):
            experiment.policies_for('validation')
        with self.assertRaises(ValueError):
            experiment.policies_for('validation', {'H1': {'variant': 'bad'}, 'C1': {'variant': 'latched'}})

    def test_no_frozen_sources_or_input_manifest_means_no_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                experiment.verify_freeze(Path(directory) / 'absent.json')

    def test_empty_prerequisite_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'freeze.json'
            path.write_text(json.dumps(dict(passed=True, frozen_protocol={}, evidence_sha256={})))
            with patch.object(experiment, 'protocol', return_value={}):
                with self.assertRaises(ValueError):
                    experiment.verify_freeze(path)

    def test_changed_locked_source_is_rejected(self):
        with patch.object(experiment, 'protocol', return_value={'changed': True}):
            with self.assertRaises(ValueError):
                experiment.verify_lock({'protocol': {}})

    def test_existing_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result'
            output.with_suffix('.json').write_text('keep')
            with self.assertRaises(ValueError):
                experiment.execute('development', output, Path(directory) / 'lock.json')
            self.assertEqual(output.with_suffix('.json').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
