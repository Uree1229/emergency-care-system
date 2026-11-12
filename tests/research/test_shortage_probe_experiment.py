from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import shortage_probe_experiment as experiment
from test_joint_policy_experiment import summary


def case(baseline=10,safe=9.5,probe=9):
    summaries={code:summary(probe if code.endswith('_probe') else
        safe if code.endswith('_safe') else baseline) for code in experiment.POLICY_IDS}
    return dict(family=experiment.FAMILIES[0],summaries=summaries,
        comparisons={name:experiment.compare(summaries[a],summaries[b])
                     for name,a,b in experiment.PAIRS})


class ShortageProbeExperimentTests(unittest.TestCase):
    def test_eight_unique_explicit_branches(self):
        self.assertEqual(len(experiment.POLICIES),8)
        self.assertEqual(len(set(experiment.POLICY_IDS)),8)
        self.assertEqual(len(experiment.PAIRS),8)
        for allocation in ('H1','C1'):
            for stage in ('baseline','safe','probe'):experiment.policy_id(allocation,stage)
            experiment.policy_id(allocation,inquiry='fixed_3')

    def test_safe_strict_improvement_can_be_selected(self):
        chosen=experiment.select_policy([case()])['H1']
        self.assertEqual(chosen['candidate'],'bounded_shortage_probe')
        self.assertTrue(all(chosen['gates'].values()))

    def test_no_gain_cannot_promote_extra_work(self):
        chosen=experiment.select_policy([case(safe=9,probe=9)])['H1']
        self.assertEqual(chosen['candidate'],'protected_v4_serial_registration')

    def test_one_patient_harm_cannot_hide_in_average(self):
        rows=[case(safe=20,probe=1),case(safe=10,probe=10.25)]
        chosen=experiment.select_policy(rows)['H1']
        self.assertFalse(chosen['checks']['vs_previous_safe']['no_individual_treatment_worsening'])
        self.assertEqual(chosen['candidate'],'protected_v4_serial_registration')

    def test_one_case_wait_increase_cannot_hide_in_population(self):
        chosen=experiment.select_policy([case(safe=20,probe=1),case(safe=10,probe=10.25)])
        self.assertFalse(chosen['H1']['checks']['vs_previous_safe']['no_case_total_wait_increase'])

    def test_protected_case_wait_requirement_still_applies(self):
        chosen=experiment.select_policy([case(baseline=10,safe=11,probe=10.25),case()])
        self.assertTrue(chosen['H1']['gates']['vs_previous_safe'])
        self.assertFalse(chosen['H1']['gates']['vs_protected_population'])

    def test_final_arrival_harm_rejected_with_equal_treatment(self):
        row=case()
        row['summaries']['v4_H1_probe']['patient_rows'][0]['final_arrival_capped_minutes']=11
        chosen=experiment.select_policy([row])
        self.assertFalse(chosen['H1']['checks']['vs_previous_safe']['no_individual_final_arrival_worsening'])

    def test_lost_treatment_rejected_at_censor_tie(self):
        row=case(baseline=10,safe=10,probe=10)
        row['summaries']['v4_H1_probe']['patient_rows'][0]['treated']=False
        self.assertFalse(experiment.select_policy([row])['H1']['checks']['vs_previous_safe']['no_lost_treatments'])

    def test_extra_query_has_explicit_cost_bound(self):
        row=case()
        row['summaries']['v4_H1_probe']['inquiries']=4
        self.assertFalse(experiment.select_policy([row])['H1']['checks']['vs_previous_safe']['bounded_inquiry_cost'])

    def test_h1_c1_selection_is_separate(self):
        row=case()
        row['summaries']['v4_H1_probe']=summary(11)
        chosen=experiment.select_policy([row])
        self.assertEqual(chosen['H1']['candidate'],'protected_v4_serial_registration')
        self.assertEqual(chosen['C1']['candidate'],'bounded_shortage_probe')

    def test_old_protected_individual_tradeoff_is_disclosed(self):
        row=case()
        for code,x in row['summaries'].items():
            times=(22,8) if code.endswith(('_safe','_probe')) else (8,26)
            x.update(patients=2,total_wait_sum_minutes=sum(times)-10,
                     request_to_treatment_capped_sum_minutes=sum(times))
            x['patient_rows']=[dict(x['patient_rows'][0],patient=pid,
                treatment_capped_minutes=value,final_arrival_capped_minutes=value)
                for pid,value in zip(('P01','P03'),times)]
        chosen=experiment.select_policy([row,case()])['H1']
        self.assertFalse(chosen['checks']['vs_protected']['no_individual_treatment_worsening'])
        self.assertTrue(chosen['checks']['vs_previous_safe']['no_individual_treatment_worsening'])
        self.assertEqual(chosen['candidate'],'bounded_shortage_probe')

    def test_protocol_detaches_configuration(self):
        with patch.object(experiment,'source_hashes',return_value={}),patch.object(experiment,'runtime_versions',return_value={}):
            before=deepcopy(experiment.SETTINGS)
            protocol=experiment.protocol()
            protocol['settings']['policies'][0]['id']='changed'
            self.assertEqual(before,experiment.SETTINGS)
            self.assertEqual(protocol['development_seeds'],[39101,39102,39103])
            self.assertEqual(protocol['validation_seeds'],list(range(40101,40107)))

    def test_existing_result_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'result'
            output.with_suffix('.json').write_text('keep')
            with self.assertRaises(ValueError):experiment.execute('development',output,Path(folder)/'lock.json')
            self.assertEqual(output.with_suffix('.json').read_text(),'keep')

    def test_changed_source_or_protocol_rejects_validation_lock(self):
        with patch.object(experiment,'protocol',return_value={'changed':True}):
            with self.assertRaises(ValueError):experiment.verify_lock({'protocol':{}})


if __name__=='__main__':unittest.main()
