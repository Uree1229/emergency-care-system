import ast
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation import offer_reserve_experiment as exp


class OfferReserveExperimentTests(unittest.TestCase):
    def test_fixed_candidate_references_and_comparisons(self):
        self.assertEqual(exp.VARIANTS, ('cohort_reserve',))
        self.assertEqual(exp.references(), exp.parent.policies_for('development'))
        self.assertEqual(len(exp.references()), 8)
        self.assertEqual(len(exp.policies_for('development')), 10)
        self.assertEqual(len(exp.pairs_for('development')), 8)
        self.assertEqual(len(exp.NEW_SOURCES), 10)
        self.assertEqual(len(exp.TEST_FIXTURES), 5)
        self.assertEqual(len(exp.SOURCE_FILES), len(exp.parent.SOURCE_FILES)+15)
        self.assertFalse(exp.SETTINGS['adopted'])
        self.assertEqual(exp.SETTINGS['fixed_additional_same_demand_reserve_units'], 1)

    def test_all_inherited_inputs_are_unchanged(self):
        self.assertEqual(len(exp.ORIGINAL_FAMILIES), 141)
        for family in exp.ORIGINAL_FAMILIES:
            with self.subTest(family=family):
                self.assertEqual(exp.make_scenario(7,family), exp.parent.make_scenario(7,family))

    def test_validation_requires_a_passing_locked_choice(self):
        with self.assertRaises(ValueError): exp.policies_for('validation')
        choices={a:dict(variant='cohort_reserve') for a in ('H1','C1')}
        self.assertEqual(len(exp.policies_for('validation',choices)), 10)
        with patch.object(exp,'protocol',return_value={}):
            with self.assertRaisesRegex(ValueError,'locked passing'):
                exp.verify_development_lock(dict(protocol={},validation_allowed=False))
        self.assertEqual(exp.VALIDATION_SEEDS,tuple(range(52101,52107)))

    def test_original_primary_and_deployment_gates_have_identical_ast(self):
        def function(module,name):
            tree=ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
            return ast.dump(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name),include_attributes=False)
        for name in ('assess','select_policy'):
            self.assertEqual(function(exp,name),function(exp.parent,name))

    def test_cached_full_references_bind_both_nested_and_direct_cases(self):
        cache=exp.SavedReferences()
        for family in ('fresh_control','public_offer_later_scarce_patient'):
            scenario=exp.make_scenario(44101,family)
            before=deepcopy(scenario)
            branches,audits,proof=cache.load(scenario)
            self.assertEqual(scenario,before)
            self.assertEqual(set(branches),{p['id'] for p in exp.references()})
            self.assertEqual(set(audits),set(branches))
            self.assertTrue(all(a['passed'] for a in audits.values()))
            self.assertEqual(proof['new_execution_count'],0)
            self.assertEqual(proof['new_audit_count'],0)
            for code,branch in branches.items():
                self.assertEqual(exp.sha(exp.raw(branch)),proof['cached_branch_sha256'][code])
            self.assertEqual(proof['nested_provenance'] is None,family.startswith('public_offer_'))

    def test_incomplete_freeze_and_unknown_candidate_are_rejected(self):
        for allocation,variant in (('H2','cohort_reserve'),('H1','tuned')):
            with self.assertRaises(ValueError): exp.candidate_id(allocation,variant)
        incomplete=dict(passed=True,protocol={},evidence_sha256={})
        with patch.object(exp,'read',return_value=incomplete),patch.object(exp,'protocol',return_value={}):
            with self.assertRaisesRegex(ValueError,'complete prerequisite'): exp.verify_freeze()


if __name__=='__main__':unittest.main()
