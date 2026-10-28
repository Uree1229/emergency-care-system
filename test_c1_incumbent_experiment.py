from copy import deepcopy
import unittest
from unittest.mock import patch
from simulation import c1_incumbent_experiment as exp


class C1IncumbentExperimentTests(unittest.TestCase):
    def test_fixed_single_candidate_scope_and_budgets(self):
        self.assertEqual(exp.VARIANTS, ('single_augment',))
        self.assertEqual([p['id'] for p in exp.references()], ['rank_H1_normal','rank_C1_normal'])
        self.assertEqual(len(exp.policies_for('development')),3)
        self.assertEqual(len(exp.pairs_for('development')),2)
        self.assertEqual(len(exp.NEW_SOURCES),10)
        self.assertEqual(len(exp.TEST_FIXTURES),5)
        self.assertEqual(len(exp.SOURCE_FILES),len(exp.parent.SOURCE_FILES)+15)
        self.assertFalse(exp.SETTINGS['deployment_eligible'])
        self.assertFalse(exp.SETTINGS['adopted'])
        for family in exp.FAMILIES:
            self.assertEqual(exp.budget_for(exp.make_scenario(7,family)),.001 if family in exp.ADDITIONAL_FAMILIES else .05)
        for value in (0,.002,True,'0.05'):
            with self.assertRaises(ValueError):exp.budget_for({'experiment':{'c1_budget_seconds':value}})

    def test_original_inputs_and_parent_proof_unchanged(self):
        self.assertEqual(len(exp.ORIGINAL_FAMILIES),149)
        self.assertEqual(len(exp.FAMILIES),155)
        for family in exp.ORIGINAL_FAMILIES:
            self.assertEqual(exp.make_scenario(7,family),exp.parent.make_scenario(7,family))
        self.assertEqual(exp.parent_binding()['seal_sha256'],'36d5c7639c295767f4d454ecd37d33b0d09054b3f1a4d5342750d8b740475db8')

    def test_reserved_choice_and_incomplete_freeze_rejected(self):
        with self.assertRaises(ValueError):exp.policies_for('validation')
        choice={'C1':{'variant':'single_augment'}}
        self.assertEqual(len(exp.policies_for('validation',choice)),3)
        self.assertEqual(exp.VALIDATION_SEEDS,tuple(range(54101,54107)))
        with patch.object(exp,'protocol',return_value={}):
            with self.assertRaisesRegex(ValueError,'locked passing'):
                exp.verify_development_lock({'protocol':{},'validation_allowed':False})
            with patch.object(exp,'read',return_value={'passed':True,'protocol':{},'evidence_sha256':{}}):
                with self.assertRaisesRegex(ValueError,'complete prerequisite'):exp.verify_freeze()
        for allocation,variant in (('H1','single_augment'),('C1','tuned')):
            with self.assertRaises(ValueError):exp.candidate_id(allocation,variant)

    def test_complete_cached_references_are_exact_old_objects(self):
        cache=exp.SavedReferences()
        for family in ('out_of_order','public_offer_later_scarce_patient','reserve_larger_demand'):
            if family not in exp.ORIGINAL_FAMILIES:continue
            scenario=exp.make_scenario(44101,family);before=deepcopy(scenario)
            branches,audits,proof=cache.load(scenario)
            self.assertEqual(scenario,before)
            self.assertEqual(set(branches),{'rank_H1_normal','rank_C1_normal'})
            self.assertTrue(all(a['passed'] for a in audits.values()))
            self.assertEqual(proof['new_execution_count'],0)
            self.assertEqual(proof['new_audit_count'],0)
            for code,b in branches.items():self.assertEqual(exp.sha(exp.raw(b)),proof['cached_branch_sha256'][code])

    def test_run_policy_has_paired_c1_native_budget(self):
        s=exp.make_scenario(7,exp.ADDITIONAL_FAMILIES[0])
        with patch.object(exp,'run_c1_incumbent_branch',return_value={}) as run:
            for policy in exp.policies_for('development'):exp.run_policy(s,policy)
        self.assertEqual([c.kwargs['budget_seconds'] for c in run.call_args_list],[.001]*3)
        self.assertEqual([c.kwargs['seeded'] for c in run.call_args_list],[False,False,True])
        self.assertEqual([c.kwargs['allocation'] for c in run.call_args_list],['H1','C1','C1'])

    def test_primary_patient_regression_gate_cannot_be_hidden_by_total_gain(self):
        sample={'lost_treatments':0,'worsened':1,'final_arrival_worsened':0,
            'total_wait_delta_minutes':-10,'capped_treatment_delta_minutes':-10,'inquiry_delta':0}
        with patch.object(exp,'compare',return_value=sample):
            result=exp.assess([{'summaries':{'rank_C1_normal':{},'seeded_C1_single_augment':{}}}],'C1','single_augment')
        self.assertFalse(result['primary_passed'])
        self.assertTrue(result['primary_checks']['strict_total_wait_reduction'])
        self.assertFalse(result['deployment_eligible'])


if __name__=='__main__':unittest.main()
