"""Casebook checks preserve evidence and distinguish observed success from caps."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import demo_casebook_20261030 as book


class CasebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data,cls.sources=book.build_casebook()

    def test_verified_case_pairs_and_correct_scarce_capacity_label(self):
        self.assertEqual([p['id'] for p in self.data['cases']],
            ['h1-improvement','h1-worsening','w1-waiting','w1-transfer','zero-capped-loss'])
        self.assertEqual(self.data['cases'][0]['profile'],'scarce_capacity')
        expected=[[-77,51],[67,-39],[-67,26],[-60,83],[0]]
        for case,values in zip(self.data['cases'],expected):
            self.assertEqual([p['comparisons'][case['preferred_comparison']]['delta_capped_minutes'] for p in case['patients']],values)
            for patient in case['patients']:self.assertEqual(set(patient['endpoints']),set(book.POLICIES))

    def test_zero_difference_preserves_arrival_and_treatment_failure(self):
        case=self.data['cases'][-1];p=case['patients'][0];comparison=p['comparisons']['W1_minus_R0']
        self.assertEqual((p['occur_t'],p['search_start_t'],p['observation_end_t']),(54,60,180))
        self.assertEqual(comparison['delta_capped_minutes'],0)
        self.assertIsNone(comparison['actual_arrival_delta_minutes'])
        self.assertEqual(comparison['success_lost'],['arrived','treated'])
        self.assertEqual(p['endpoints']['R0']['arrive_t'],180)
        self.assertIsNone(p['endpoints']['W1']['arrive_t'])
        self.assertEqual(p['endpoints']['W1']['arrival_capped_minutes'],126)

    def test_fresh_denominators_and_known_development_are_separate(self):
        summary=self.data['fresh_summary']
        self.assertEqual((summary['patients'],summary['both_arrived'],summary['both_not_arrived']),(987,902,85))
        self.assertEqual(summary['H1_W1_endpoint_equal'],987)
        self.assertEqual((summary['max_W1_minus_R1'],summary['priority4_max_W1_minus_R1']),(124,112))
        self.assertEqual((summary['known_development']['improved_vs_H1'],summary['known_development']['worsened_vs_H1']),(3,5))
        self.assertFalse(summary['research_gate_passed'])
        self.assertEqual(self.data['scope']['new_policy_executions'],0)

    def test_original_evidence_hashes_remain_unchanged(self):
        self.assertTrue(all(book.digest(book.ROOT/name)==expected for name,expected in self.sources.items()))

    def test_existing_output_or_viewer_is_rejected_before_source_reads(self):
        parent=book.ROOT/'.codex-build/demo-casebook-tests';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            root=Path(directory);existing=root/'existing';existing.mkdir();script=root/'existing.js';script.write_text('preserve',encoding='utf-8')
            with patch.object(book,'build_casebook',side_effect=AssertionError('must not read')):
                with self.assertRaises(FileExistsError):book.write_casebook(existing,root/'new.js',root=root)
                with self.assertRaises(FileExistsError):book.write_casebook(root/'new',script,root=root)
            self.assertFalse((root/'new').exists());self.assertEqual(script.read_text(encoding='utf-8'),'preserve')

    def test_corrupted_waiting_decision_pin_is_rejected(self):
        original=book.read
        def changed(path):
            result=original(path)
            if str(path).endswith('selection-review.json'):result['authoritative_decision_sha256']='0'*64
            return result
        with patch.object(book,'read',side_effect=changed),self.assertRaisesRegex(ValueError,'decision/receipt binding'):
            book.build_casebook()


if __name__=='__main__':unittest.main()
