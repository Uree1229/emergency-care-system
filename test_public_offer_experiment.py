import unittest
from unittest.mock import patch
from simulation import public_offer_experiment as exp


class PublicOfferExperimentTests(unittest.TestCase):
    def test_single_candidate_and_reference_scope(self):
        self.assertEqual(exp.VARIANTS, ('public_blocked',))
        self.assertEqual(len(exp.references()), 6)
        self.assertEqual(len(exp.policies_for('development')), 8)
        self.assertEqual(len(exp.NEW_SOURCES), 10)
        self.assertEqual(len(exp.TEST_FIXTURES), 10)
        self.assertEqual(len(exp.SOURCE_FILES), 196)
        self.assertFalse(exp.SETTINGS['adopted'])

    def test_validation_requires_locked_passing_development(self):
        with self.assertRaises(ValueError): exp.policies_for('validation')
        fixed = {a: dict(variant='public_blocked') for a in ('H1','C1')}
        self.assertEqual(len(exp.policies_for('validation',fixed)), 8)
        self.assertEqual(len(exp.pairs_for('validation',fixed)), 6)
        with patch.object(exp,'protocol',return_value={}):
            with self.assertRaisesRegex(ValueError,'locked passing'):
                exp.verify_development_lock(dict(protocol={},validation_allowed=False))

    def test_all_135_inherited_inputs_unchanged_at_exposed_smoke_seed(self):
        for family in exp.ORIGINAL_FAMILIES:
            with self.subTest(family=family):
                self.assertEqual(exp.make_scenario(7,family), exp.parent.make_scenario(7,family))

    def test_unknown_candidate_or_incomplete_freeze_rejected(self):
        for allocation, variant in (('H2','public_blocked'),('H1','tuned')):
            with self.assertRaises(ValueError): exp.candidate_id(allocation,variant)
        incomplete = dict(passed=True,protocol={},evidence_sha256={})
        with patch.object(exp,'read',return_value=incomplete),patch.object(exp,'protocol',return_value={}):
            with self.assertRaisesRegex(ValueError,'complete prerequisite'):
                exp.verify_freeze()

    def test_original_gates_are_preserved(self):
        self.assertTrue(exp.SETTINGS['deployment_requires_both_inherited_references'])
        self.assertEqual(exp.SETTINGS['no_primary_pass'],
            'preserve complete failed development; skip reserved validation')
        self.assertTrue(exp.SETTINGS['actual_pending_work_and_backup_offers_retained_after_new_commit'])


if __name__ == '__main__': unittest.main()
