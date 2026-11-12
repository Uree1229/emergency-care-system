from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import deadline_safe_experiment as experiment
from test_joint_policy_experiment import summary


def case(baseline=10, previous=9.5, late=9.25, safe=9):
    summaries = {p['id']: summary(safe if p['late_pending_guard'] else
        late if p['late_reply_recovery'] else previous if p['scarce_routing'] else baseline)
        for p in experiment.POLICIES}
    return dict(family=experiment.FAMILIES[0], summaries=summaries,
        comparisons={name: experiment.compare(summaries[a], summaries[b])
                     for name,a,b in experiment.PAIRS})


class DeadlineSafeExperimentTests(unittest.TestCase):
    def test_ten_branches_separate_pending_guard(self):
        self.assertEqual(len(experiment.POLICIES),10)
        self.assertEqual(len(set(experiment.POLICY_IDS)),10)
        self.assertEqual(len(experiment.PAIRS),10)
        for allocation in ('H1','C1'):
            for active,late,safe in ((False,False,False),(True,False,False),
                                     (True,True,False),(True,True,True)):
                experiment.policy_id(allocation,cohort=active,routing=active,late=late,safe=safe)

    def test_strict_safe_gain_can_be_selected(self):
        selected=experiment.select_policy([case()])['H1']
        self.assertEqual(selected['candidate'],'safe_late_reply')
        self.assertTrue(all(selected['gates'].values()))

    def test_no_gain_over_previous_candidate_is_not_promoted(self):
        self.assertEqual(experiment.select_policy([case(previous=9,safe=9)])['H1']['candidate'],
                         'protected_v4_serial_registration')

    def test_no_gain_over_unguarded_late_is_not_promoted(self):
        self.assertEqual(experiment.select_policy([case(late=9,safe=9)])['H1']['candidate'],
                         'protected_v4_serial_registration')

    def test_fixing_previous_candidate_harm_can_pass_protected_comparison(self):
        rows=[case(baseline=10,previous=11,late=11,safe=10),case()]
        self.assertEqual(experiment.select_policy(rows)['C1']['candidate'],'safe_late_reply')

    def test_one_patient_harm_vs_previous_cannot_hide_in_average(self):
        chosen=experiment.select_policy([case(previous=20,late=20,safe=1),
                                         case(previous=10,late=11,safe=10.25)])
        self.assertFalse(chosen['H1']['checks']['vs_previous_candidate']['no_individual_treatment_worsening'])
        self.assertEqual(chosen['H1']['candidate'],'protected_v4_serial_registration')

    def test_one_patient_harm_vs_unguarded_cannot_hide_in_average(self):
        chosen=experiment.select_policy([case(previous=20,late=20,safe=1),
                                         case(previous=11,late=10,safe=10.25)])
        self.assertFalse(chosen['H1']['checks']['vs_unguarded_late']['no_individual_treatment_worsening'])
        self.assertEqual(chosen['H1']['candidate'],'protected_v4_serial_registration')

    def test_one_case_wait_increase_vs_protected_cannot_hide(self):
        chosen=experiment.select_policy([case(baseline=20,previous=20,late=20,safe=1),
                                         case(baseline=10,previous=11,late=11,safe=10.25)])
        self.assertFalse(chosen['H1']['checks']['vs_protected']['no_case_total_wait_increase'])

    def test_late_arrival_rejected_even_if_treatment_unchanged(self):
        row=case()
        pid=experiment.policy_id('H1',cohort=True,routing=True,late=True,safe=True)
        row['summaries'][pid]['patient_rows'][0]['final_arrival_capped_minutes']=11
        chosen=experiment.select_policy([row])
        self.assertFalse(chosen['H1']['checks']['vs_previous_candidate']['no_individual_final_arrival_worsening'])

    def test_existing_equal_priority_tradeoff_disclosed_without_new_harm(self):
        row=case()
        for p in experiment.POLICIES:
            x=row['summaries'][p['id']]
            times=(22,8) if p['scarce_routing'] else (8,26)
            x.update(patients=2,total_wait_sum_minutes=sum(times)-10,
                     request_to_treatment_capped_sum_minutes=sum(times))
            x['patient_rows']=[dict(x['patient_rows'][0],patient=pid,
                treatment_capped_minutes=value,final_arrival_capped_minutes=value)
                for pid,value in zip(('P01','P03'),times)]
        chosen=experiment.select_policy([row,case()])
        self.assertFalse(chosen['H1']['checks']['vs_protected']['no_individual_treatment_worsening'])
        self.assertTrue(chosen['H1']['checks']['vs_previous_candidate']['no_individual_treatment_worsening'])
        self.assertTrue(chosen['H1']['checks']['vs_unguarded_late']['no_individual_treatment_worsening'])
        self.assertEqual(chosen['H1']['candidate'],'safe_late_reply')

    def test_lost_treatment_rejected_even_at_censor_tie(self):
        row=case(baseline=10,previous=10,late=10,safe=10)
        pid=experiment.policy_id('H1',cohort=True,routing=True,late=True,safe=True)
        row['summaries'][pid]['patient_rows'][0]['treated']=False
        chosen=experiment.select_policy([row])
        self.assertFalse(chosen['H1']['checks']['vs_previous_candidate']['no_lost_treatments'])

    def test_extra_inquiry_cost_has_explicit_bound(self):
        row=case()
        pid=experiment.policy_id('H1',cohort=True,routing=True,late=True,safe=True)
        row['summaries'][pid]['inquiries']=4
        self.assertFalse(experiment.select_policy([row])['H1']['checks']['vs_protected']['bounded_inquiry_cost'])

    def test_h1_c1_decisions_are_separate(self):
        row=case()
        row['summaries'][experiment.policy_id('H1',cohort=True,routing=True,late=True,safe=True)]=summary(11)
        chosen=experiment.select_policy([row])
        self.assertEqual(chosen['H1']['candidate'],'protected_v4_serial_registration')
        self.assertEqual(chosen['C1']['candidate'],'safe_late_reply')

    def test_protocol_detaches_settings_and_reserves_fresh_seeds(self):
        with patch.object(experiment,'source_hashes',return_value={}),patch.object(experiment,'runtime_versions',return_value={}):
            before=deepcopy(experiment.SETTINGS)
            protocol=experiment.protocol()
            protocol['settings']['policies'][0]['id']='changed'
            self.assertEqual(before,experiment.SETTINGS)
            self.assertEqual(protocol['development_seeds'],[37101,37102,37103])
            self.assertEqual(protocol['validation_seeds'],list(range(38101,38107)))
            self.assertFalse(set(protocol['development_seeds']) & set(protocol['validation_seeds']))

    def test_old_output_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'run'
            output.with_suffix('.json').write_text('original')
            with self.assertRaises(ValueError):experiment.execute('development',output,Path(folder)/'lock.json')
            self.assertEqual(output.with_suffix('.json').read_text(),'original')

    def test_changed_source_or_contract_rejects_validation_lock(self):
        with patch.object(experiment,'protocol',return_value={'new_guard':True}):
            with self.assertRaises(ValueError):experiment.verify_lock({'protocol':{'old_guard':True}})


if __name__=='__main__':unittest.main()
