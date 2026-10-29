import unittest
from unittest.mock import patch
from simulation import probe_guard_experiment as exp

class ProbeGuardExperimentTests(unittest.TestCase):
    def test_declared_variants_and_reference_scope(self):
        self.assertEqual(exp.VARIANTS,('renewed','off'))
        self.assertEqual(len(exp.references()),6)
        self.assertEqual(len(exp.policies_for('development')),10)
        self.assertFalse(exp.SETTINGS['adopted'])

    def test_validation_requires_lock(self):
        with self.assertRaises(ValueError): exp.policies_for('validation')
        fixed={a:dict(variant='renewed') for a in ('H1','C1')}
        self.assertEqual(len(exp.policies_for('validation',fixed)),8)
        self.assertEqual(len(exp.pairs_for('validation',fixed)),6)

    def test_original_families_unchanged(self):
        for family in exp.ORIGINAL_FAMILIES:
            self.assertEqual(exp.make_scenario(7,family),exp.parent.make_scenario(7,family))

    def test_only_predeclared_candidate_ids(self):
        with self.assertRaises(ValueError): exp.candidate_id('H2','renewed')
        with self.assertRaises(ValueError): exp.candidate_id('H1','tuned')

    def test_incomplete_prerequisite_freeze_rejected(self):
        incomplete=dict(passed=True,protocol={},evidence_sha256={})
        with patch.object(exp,'read',return_value=incomplete),patch.object(exp,'protocol',return_value={}):
            with self.assertRaisesRegex(ValueError,'complete prerequisite'):
                exp.verify_freeze()

    def test_validation_cannot_use_failed_development(self):
        with patch.object(exp,'protocol',return_value={}):
            with self.assertRaisesRegex(ValueError,'locked passing'):
                exp.verify_development_lock(dict(protocol={},validation_allowed=False))

if __name__=='__main__': unittest.main()
