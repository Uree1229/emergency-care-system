from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation import scarce_delay_experiment as experiment
from test_joint_policy_experiment import summary


def case(*, baseline=10, cohort=10, routed=9, inquiries=3):
    summaries = {}
    for p in experiment.POLICIES:
        value = routed if p['scarce_routing'] else cohort if p['cohort_registration'] else baseline
        summaries[p['id']] = summary(value, inquiries)
    return dict(family=experiment.FAMILIES[0], summaries=summaries,
        comparisons={name: experiment.compare(summaries[a], summaries[b])
                     for name, a, b in experiment.PAIRS})


class ScarceDelayExperimentTests(unittest.TestCase):
    def test_eight_explicit_branches_separate_registration_from_routing(self):
        self.assertEqual(len(experiment.POLICIES), 8)
        self.assertEqual(len(set(experiment.POLICY_IDS)), 8)
        for allocation in ('H1', 'C1'):
            for cohort, routing in ((False, False), (True, False), (True, True)):
                experiment.policy_id(allocation, cohort=cohort, routing=routing)
            experiment.policy_id(allocation, inquiry='fixed_3')

    def test_one_case_wait_increase_cannot_hide_in_population_gain(self):
        selected = experiment.select_policy([case(routed=1), case(routed=10.25)])
        for row in selected.values():
            self.assertEqual(row['candidate'], 'protected_v4_serial_registration')
            self.assertFalse(row['checks']['registration_and_routing']['no_case_total_wait_increase'])

    def test_one_patient_harm_cannot_hide_in_case_total_gain(self):
        row = case()
        for policy in experiment.POLICIES:
            saved = row['summaries'][policy['id']]
            saved['patient_rows'].append(dict(saved['patient_rows'][0], patient='P02',
                treatment_capped_minutes=21 if policy['scarce_routing'] else 20))
        chosen = experiment.select_policy([row])
        self.assertFalse(chosen['H1']['checks']['registration_and_routing']['no_individual_treatment_worsening'])

    def test_lost_treatment_rejected_at_same_censored_time(self):
        row = case(baseline=10, routed=10)
        for allocation in ('H1', 'C1'):
            b = row['summaries'][experiment.policy_id(allocation, cohort=True, routing=True)]
            b['patient_rows'][0]['treated'] = False
        selected = experiment.select_policy([row])
        self.assertFalse(selected['C1']['checks']['registration_and_routing']['no_lost_treatments'])

    def test_no_improvement_keeps_protected_baseline(self):
        self.assertEqual(experiment.select_policy([case(routed=10)])['H1']['candidate'],
                         'protected_v4_serial_registration')

    def test_safe_strict_gain_can_be_selected(self):
        self.assertEqual(experiment.select_policy([case()])['H1']['candidate'], 'registration_and_routing')

    def test_less_change_breaks_equal_gain_tie(self):
        self.assertEqual(experiment.select_policy([case(cohort=9, routed=9)])['H1']['candidate'],
                         'registration_only')

    def test_inquiry_cost_above_locked_budget_rejects_candidate(self):
        row = case()
        for allocation in ('H1', 'C1'):
            row['summaries'][experiment.policy_id(allocation, cohort=True, routing=True)]['inquiries'] += 1
        selected = experiment.select_policy([row])
        self.assertFalse(selected['H1']['checks']['registration_and_routing']['bounded_inquiry_cost'])

    def test_small_extra_inquiry_within_total_budget_is_visible_and_allowed(self):
        rows = [case() for _ in range(4)]
        for allocation in ('H1', 'C1'):
            rows[0]['summaries'][experiment.policy_id(allocation, cohort=True, routing=True)]['inquiries'] += 1
        selected = experiment.select_policy(rows)
        self.assertTrue(selected['H1']['checks']['registration_and_routing']['bounded_inquiry_cost'])
        self.assertEqual(selected['H1']['candidate'], 'registration_and_routing')

    def test_late_arrival_rejected_even_when_treatment_unchanged(self):
        row = case()
        key = experiment.policy_id('H1', cohort=True, routing=True)
        row['summaries'][key]['patient_rows'][0]['final_arrival_capped_minutes'] = 11
        chosen = experiment.select_policy([row])
        self.assertFalse(chosen['H1']['checks']['registration_and_routing']['no_individual_final_arrival_worsening'])

    def test_selection_is_independent_for_h1_and_c1(self):
        row = case()
        row['summaries'][experiment.policy_id('H1', cohort=True, routing=True)] = summary(11)
        selected = experiment.select_policy([row])
        self.assertEqual(selected['H1']['candidate'], 'protected_v4_serial_registration')
        self.assertEqual(selected['C1']['candidate'], 'registration_and_routing')

    def test_aggregate_reports_case_harms_as_well_as_patient_tails(self):
        rows = [case(routed=1), case(routed=10.25)]
        compared = experiment.combined_pairs(rows)['combined_H1']
        self.assertEqual(compared['cases_with_longer_total_wait'], 1)
        self.assertEqual(compared['worsened'], 1)
        self.assertEqual(compared['maximum_treatment_worsening_minutes'], .25)
        self.assertLess(compared['total_wait_delta_minutes'], 0)

    def test_protocol_is_detached_and_records_fresh_split(self):
        with patch.object(experiment, 'source_hashes', return_value={}), \
                patch.object(experiment, 'runtime_versions', return_value={}):
            before = deepcopy(experiment.SETTINGS)
            protocol = experiment.protocol()
            protocol['settings']['policies'][0]['id'] = 'changed'
            self.assertEqual(experiment.SETTINGS, before)
            self.assertFalse(set(protocol['development_seeds']) & set(protocol['validation_seeds']))
            self.assertEqual(protocol['development_seeds'], [33101,33102,33103])

    def test_existing_artifact_and_lock_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'run'
            output.with_suffix('.json').write_text('original')
            with self.assertRaises(ValueError):
                experiment.execute('development', output, Path(folder) / 'lock.json')
            self.assertEqual(output.with_suffix('.json').read_text(), 'original')

    def test_changed_source_protocol_rejects_validation(self):
        with patch.object(experiment, 'protocol', return_value={'source': 'new'}):
            with self.assertRaises(ValueError):
                experiment.verify_lock({'protocol': {'source': 'old'}})


if __name__ == '__main__':
    unittest.main()
