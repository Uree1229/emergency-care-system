from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import capability_probe_experiment as experiment
from test_joint_policy_experiment import summary


def case(baseline=10, safe=9.5, probe=9.25, cooperative=9, capability=8.75):
    values = dict(baseline=baseline, safe=safe, probe=probe, cooperative=cooperative, capability=capability)
    summaries = {code: summary(values[code.rsplit('_', 1)[1]]) for code in experiment.POLICY_IDS}
    return dict(family=experiment.FAMILIES[0], summaries=summaries,
        comparisons={name: experiment.compare(summaries[a], summaries[b]) for name, a, b in experiment.PAIRS})


class CapabilityProbeExperimentTests(unittest.TestCase):
    def test_twelve_unique_branches_and_sixteen_pairs(self):
        self.assertEqual(len(experiment.POLICIES), 12)
        self.assertEqual(len(set(experiment.POLICY_IDS)), 12)
        self.assertEqual(len(experiment.PAIRS), 16)
        for a in ('H1', 'C1'):
            for stage in ('baseline', 'safe', 'probe', 'cooperative', 'capability'):
                experiment.policy_id(a, stage)
            experiment.policy_id(a, inquiry='fixed_3')

    def test_all_strict_gates_can_select(self):
        result = experiment.select_policy([case()])['H1']
        self.assertTrue(all(result['gates'].values()))
        self.assertEqual(result['candidate'], 'capability_preserving_probe_workflow')

    def test_no_gain_over_immediate_cooperative_parent_is_rejected(self):
        result = experiment.select_policy([case(capability=9)])['H1']
        self.assertFalse(result['gates']['vs_previous_cooperative'])
        self.assertEqual(result['candidate'], 'protected_v4_serial_registration')

    def test_safe_patient_harm_is_not_hidden_by_population_gain(self):
        result = experiment.select_policy([case(safe=20, cooperative=20, capability=1),
            case(safe=10, cooperative=11, capability=10.25)])['H1']
        self.assertFalse(result['checks']['vs_previous_safe']['no_individual_treatment_worsening'])

    def test_cooperative_patient_harm_is_not_hidden_by_population_gain(self):
        result = experiment.select_policy([case(cooperative=20, capability=1),
            case(safe=11, cooperative=10, capability=10.25)])['H1']
        self.assertFalse(result['checks']['vs_previous_cooperative']['no_individual_treatment_worsening'])

    def test_case_wait_gate_against_probe_is_not_relaxed(self):
        result = experiment.select_policy([case(probe=20, capability=1),
            case(safe=10, cooperative=9.5, probe=9, capability=9.25)])['H1']
        self.assertFalse(result['checks']['vs_previous_probe']['no_case_total_wait_increase'])
        self.assertFalse(result['gates']['vs_previous_probe_population'])

    def test_protected_case_gate_is_retained(self):
        self.assertFalse(experiment.select_policy([case(baseline=8), case()])['H1']['gates']['vs_protected_population'])

    def test_final_arrival_is_checked_separately(self):
        row = case()
        row['summaries']['v4_H1_capability']['patient_rows'][0]['final_arrival_capped_minutes'] = 11
        self.assertFalse(experiment.select_policy([row])['H1']['checks']['vs_previous_safe']['no_individual_final_arrival_worsening'])

    def test_lost_treatment_is_checked_at_censor_tie(self):
        row = case(baseline=10, safe=10, probe=10, cooperative=10, capability=10)
        row['summaries']['v4_H1_capability']['patient_rows'][0]['treated'] = False
        self.assertFalse(experiment.select_policy([row])['H1']['checks']['vs_previous_cooperative']['no_lost_treatments'])

    def test_original_ten_percent_contact_cost_limit_is_retained(self):
        row = case()
        row['summaries']['v4_H1_capability']['inquiries'] = 4
        self.assertFalse(experiment.select_policy([row])['H1']['checks']['vs_previous_safe']['bounded_inquiry_cost'])

    def test_h1_and_c1_selection_are_separate(self):
        row = case()
        row['summaries']['v4_H1_capability'] = summary(11)
        result = experiment.select_policy([row])
        self.assertEqual(result['H1']['candidate'], 'protected_v4_serial_registration')
        self.assertEqual(result['C1']['candidate'], 'capability_preserving_probe_workflow')

    def test_development_reuse_and_fresh_validation_are_explicit(self):
        with patch.object(experiment, 'source_hashes', return_value={}), patch.object(experiment, 'runtime_versions', return_value={}):
            original = deepcopy(experiment.SETTINGS)
            protocol = experiment.protocol()
            self.assertEqual(protocol['development_seeds'], [41101, 41102, 41103])
            self.assertEqual(protocol['validation_seeds'], list(range(43101, 43107)))
            self.assertIn('previously exposed', protocol['settings']['development_context'])
            protocol['settings']['policies'][0]['id'] = 'changed'
            self.assertEqual(experiment.SETTINGS, original)

    def test_capability_dispatch_does_not_claim_new_control_on_disabled_queue(self):
        settings = experiment.SETTINGS
        self.assertIn('no invented worker or ACK', settings['queue_disabled'])
        self.assertTrue(settings['parent_source_files_unchanged'])
        self.assertTrue(settings['original_scenario_values_unchanged'])

    def test_existing_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result'
            output.with_suffix('.json').write_text('keep')
            with self.assertRaises(ValueError):
                experiment.execute('development', output, Path(directory) / 'lock.json')
            self.assertEqual(output.with_suffix('.json').read_text(), 'keep')

    def test_changed_source_rejects_validation_lock(self):
        with patch.object(experiment, 'protocol', return_value={'changed': True}):
            with self.assertRaises(ValueError):
                experiment.verify_lock({'protocol': {}})


if __name__ == '__main__':
    unittest.main()
