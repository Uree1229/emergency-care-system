from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import deadline_recovery_experiment as experiment
from test_joint_policy_experiment import summary


def case(baseline=10, previous=9.5, late=9):
    summaries = {p['id']: summary(late if p['late_reply_recovery'] else
        previous if p['scarce_routing'] else baseline) for p in experiment.POLICIES}
    return dict(family=experiment.FAMILIES[0], summaries=summaries,
        comparisons={name: experiment.compare(summaries[a], summaries[b]) for name,a,b in experiment.PAIRS})


class DeadlineRecoveryExperimentTests(unittest.TestCase):
    def test_eight_branches_separate_previous_candidate_and_new_reply_contract(self):
        self.assertEqual(len(experiment.POLICIES), 8)
        self.assertEqual(len(set(experiment.POLICY_IDS)), 8)
        for allocation in ('H1','C1'):
            for active,late in ((False,False),(True,False),(True,True)):
                experiment.policy_id(allocation,cohort=active,routing=active,late=late)

    def test_safe_strict_improvement_can_be_selected(self):
        self.assertEqual(experiment.select_policy([case()])['H1']['candidate'], 'bounded_late_reply')

    def test_no_gain_over_previous_candidate_is_not_promoted(self):
        self.assertEqual(experiment.select_policy([case(previous=9,late=9)])['H1']['candidate'],
                         'protected_v4_serial_registration')

    def test_fixing_previous_candidate_harm_can_pass_protected_comparison(self):
        rows = [case(baseline=10,previous=11,late=10), case()]
        self.assertEqual(experiment.select_policy(rows)['C1']['candidate'], 'bounded_late_reply')

    def test_one_patient_harm_vs_previous_cannot_hide_in_average(self):
        chosen = experiment.select_policy([case(previous=20,late=1),case(previous=10,late=10.25)])
        self.assertFalse(chosen['H1']['checks']['vs_previous_candidate']['no_individual_treatment_worsening'])
        self.assertEqual(chosen['H1']['candidate'], 'protected_v4_serial_registration')

    def test_one_case_wait_increase_vs_protected_cannot_hide_in_population(self):
        rows = [case(baseline=20,previous=20,late=1),case(baseline=10,previous=11,late=10.25)]
        chosen = experiment.select_policy(rows)
        self.assertFalse(chosen['H1']['checks']['vs_protected']['no_case_total_wait_increase'])

    def test_late_arrival_rejected_even_if_treatment_unchanged(self):
        row = case()
        row['summaries'][experiment.policy_id('H1',cohort=True,routing=True,late=True)]['patient_rows'][0]['final_arrival_capped_minutes'] = 11
        chosen = experiment.select_policy([row])
        self.assertFalse(chosen['H1']['checks']['vs_previous_candidate']['no_individual_final_arrival_worsening'])

    def test_existing_equal_priority_tradeoff_is_disclosed_not_counted_as_new_recovery_harm(self):
        row = case()
        for p in experiment.POLICIES:
            x = row['summaries'][p['id']]
            times = (22,8) if p['scarce_routing'] else (8,26)
            x.update(patients=2, total_wait_sum_minutes=sum(times)-10,
                request_to_treatment_capped_sum_minutes=sum(times))
            x['patient_rows'] = [dict(x['patient_rows'][0],patient=pid,
                treatment_capped_minutes=value,final_arrival_capped_minutes=value)
                for pid,value in zip(('P01','P03'),times)]
        chosen = experiment.select_policy([row,case()])
        self.assertFalse(chosen['H1']['checks']['vs_protected']['no_individual_treatment_worsening'])
        self.assertTrue(chosen['H1']['checks']['vs_previous_candidate']['no_individual_treatment_worsening'])
        self.assertEqual(chosen['H1']['candidate'],'bounded_late_reply')

    def test_lost_treatment_rejected_even_at_censor_tie(self):
        row = case(baseline=10,previous=10,late=10)
        row['summaries'][experiment.policy_id('H1',cohort=True,routing=True,late=True)]['patient_rows'][0]['treated'] = False
        chosen = experiment.select_policy([row])
        self.assertFalse(chosen['H1']['checks']['vs_previous_candidate']['no_lost_treatments'])

    def test_extra_inquiry_cost_has_explicit_bound(self):
        row = case()
        row['summaries'][experiment.policy_id('H1',cohort=True,routing=True,late=True)]['inquiries'] = 4
        chosen = experiment.select_policy([row])
        self.assertFalse(chosen['H1']['checks']['vs_protected']['bounded_inquiry_cost'])

    def test_decisions_for_h1_and_c1_are_separate(self):
        row = case()
        row['summaries'][experiment.policy_id('H1',cohort=True,routing=True,late=True)] = summary(11)
        chosen = experiment.select_policy([row])
        self.assertEqual(chosen['H1']['candidate'],'protected_v4_serial_registration')
        self.assertEqual(chosen['C1']['candidate'],'bounded_late_reply')

    def test_protocol_detaches_settings_and_reserves_fresh_seeds(self):
        with patch.object(experiment,'source_hashes',return_value={}),patch.object(experiment,'runtime_versions',return_value={}):
            before = deepcopy(experiment.SETTINGS)
            protocol = experiment.protocol()
            protocol['settings']['policies'][0]['id'] = 'changed'
            self.assertEqual(before,experiment.SETTINGS)
            self.assertEqual(protocol['development_seeds'],[35101,35102,35103])
            self.assertFalse(set(protocol['development_seeds']) & set(protocol['validation_seeds']))

    def test_old_output_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'run'
            output.with_suffix('.json').write_text('original')
            with self.assertRaises(ValueError):experiment.execute('development',output,Path(folder)/'lock.json')
            self.assertEqual(output.with_suffix('.json').read_text(),'original')

    def test_changed_source_or_contract_rejects_validation_lock(self):
        with patch.object(experiment,'protocol',return_value={'new_contract':True}):
            with self.assertRaises(ValueError):experiment.verify_lock({'protocol':{'old_contract':True}})


if __name__ == '__main__':
    unittest.main()
