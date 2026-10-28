from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import cooperative_probe_experiment as experiment
from test_joint_policy_experiment import summary


def case(baseline=10, safe=9.5, probe=9.25, cooperative=9):
    times = dict(baseline=baseline, safe=safe, probe=probe, cooperative=cooperative)
    summaries = {code: summary(times[code.rsplit('_', 1)[1]]) for code in experiment.POLICY_IDS}
    return dict(family=experiment.FAMILIES[0], summaries=summaries,
        comparisons={name: experiment.compare(summaries[a], summaries[b])
                     for name, a, b in experiment.PAIRS})


class CooperativeProbeExperimentTests(unittest.TestCase):
    def test_ten_explicit_branches_and_all_comparators(self):
        self.assertEqual(len(experiment.POLICIES), 10)
        self.assertEqual(len(set(experiment.POLICY_IDS)), 10)
        self.assertEqual(len(experiment.PAIRS), 12)
        for a in ('H1', 'C1'):
            for stage in ('baseline', 'safe', 'probe', 'cooperative'):
                experiment.policy_id(a, stage)
            experiment.policy_id(a, inquiry='fixed_3')

    def test_strict_gain_can_select_workflow(self):
        result = experiment.select_policy([case()])['H1']
        self.assertEqual(result['candidate'], 'cooperative_probe_workflow')
        self.assertTrue(all(result['gates'].values()))

    def test_current_workflow_information_extension_is_explicit(self):
        settings = experiment.SETTINGS
        self.assertIn('new actual current job-control ACK', settings['observations'])
        self.assertIn('preempts', settings['worker_contract'])
        self.assertEqual(settings['switch_overhead_minutes'], 0)
        self.assertIn('isolated simulation', settings['integration_status'])

    def test_no_gain_cannot_select_extra_work(self):
        result = experiment.select_policy([case(cooperative=9.25)])['H1']
        self.assertFalse(result['gates']['vs_previous_probe_population'])
        self.assertEqual(result['candidate'], 'protected_v4_serial_registration')

    def test_safe_patient_harm_cannot_hide_in_average(self):
        cases = [case(safe=20, probe=19, cooperative=1), case(safe=10, probe=11, cooperative=10.25)]
        result = experiment.select_policy(cases)['H1']
        self.assertFalse(result['checks']['vs_previous_safe']['no_individual_treatment_worsening'])
        self.assertEqual(result['candidate'], 'protected_v4_serial_registration')

    def test_one_case_wait_increase_cannot_hide_in_population(self):
        cases = [case(probe=20, cooperative=1), case(probe=9, cooperative=9.25)]
        result = experiment.select_policy(cases)['H1']
        self.assertFalse(result['checks']['vs_previous_probe']['no_case_total_wait_increase'])
        self.assertFalse(result['gates']['vs_previous_probe_population'])

    def test_protected_case_gate_is_retained(self):
        result = experiment.select_policy([case(baseline=8, cooperative=9), case()])['H1']
        self.assertFalse(result['gates']['vs_protected_population'])

    def test_final_arrival_harm_is_checked_separately(self):
        row = case()
        row['summaries']['v4_H1_cooperative']['patient_rows'][0]['final_arrival_capped_minutes'] = 11
        self.assertFalse(experiment.select_policy([row])['H1']['checks']['vs_previous_safe']['no_individual_final_arrival_worsening'])

    def test_lost_treatment_is_rejected_at_censor_tie(self):
        row = case(baseline=10, safe=10, probe=10, cooperative=10)
        row['summaries']['v4_H1_cooperative']['patient_rows'][0]['treated'] = False
        self.assertFalse(experiment.select_policy([row])['H1']['checks']['vs_previous_safe']['no_lost_treatments'])

    def test_extra_inquiry_cost_is_bounded(self):
        row = case()
        row['summaries']['v4_H1_cooperative']['inquiries'] = 4
        self.assertFalse(experiment.select_policy([row])['H1']['checks']['vs_previous_safe']['bounded_inquiry_cost'])

    def test_h1_and_c1_are_selected_separately(self):
        row = case()
        row['summaries']['v4_H1_cooperative'] = summary(11)
        chosen = experiment.select_policy([row])
        self.assertEqual(chosen['H1']['candidate'], 'protected_v4_serial_registration')
        self.assertEqual(chosen['C1']['candidate'], 'cooperative_probe_workflow')

    def test_probe_patient_tradeoff_is_disclosed_and_safe_individual_gate_retained(self):
        row = case()
        for code, x in row['summaries'].items():
            if code.endswith('_probe'):
                times = (7.5, 6.25)
            elif code.endswith('_cooperative'):
                times = (8.25, 4.25)
            else:
                times = (8.75, 4.25)
            x.update(patients=2, total_wait_sum_minutes=sum(times)-6,
                request_to_treatment_capped_sum_minutes=sum(times))
            x['patient_rows'] = [dict(x['patient_rows'][0], patient=p,
                treatment_capped_minutes=v, final_arrival_capped_minutes=v)
                for p, v in zip(('P01', 'P02'), times)]
        result = experiment.select_policy([row, case()])['H1']
        self.assertFalse(result['checks']['vs_previous_probe']['no_individual_treatment_worsening'])
        self.assertTrue(result['checks']['vs_previous_safe']['no_individual_treatment_worsening'])
        self.assertEqual(result['candidate'], 'cooperative_probe_workflow')

    def test_protocol_is_detached_and_fresh_seeds_are_fixed(self):
        with patch.object(experiment, 'source_hashes', return_value={}), patch.object(experiment, 'runtime_versions', return_value={}):
            before = deepcopy(experiment.SETTINGS)
            protocol = experiment.protocol()
            protocol['settings']['policies'][0]['id'] = 'changed'
            self.assertEqual(experiment.SETTINGS, before)
            self.assertEqual(protocol['development_seeds'], [41101, 41102, 41103])
            self.assertEqual(protocol['validation_seeds'], list(range(42101, 42107)))

    def test_saved_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result'
            output.with_suffix('.json').write_text('keep')
            with self.assertRaises(ValueError):
                experiment.execute('development', output, Path(directory) / 'lock.json')
            self.assertEqual(output.with_suffix('.json').read_text(), 'keep')

    def test_changed_sources_reject_validation_lock(self):
        with patch.object(experiment, 'protocol', return_value={'changed': True}):
            with self.assertRaises(ValueError):
                experiment.verify_lock({'protocol': {}})


if __name__ == '__main__':
    unittest.main()
