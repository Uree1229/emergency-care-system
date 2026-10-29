from copy import deepcopy
from pathlib import Path
import unittest
from inquiry_lab import queue_review_diagnostic as q


class QueueReviewContinuation(unittest.TestCase):
    def item(self, index=0, folder='releasealign-screen-cases'):
        return dict(file='000_123.json.gz', raw_folder=folder, source_index=index,
                    input_sha256='i'*64, raw_sha256='r'*64, compressed_sha256='c'*64)

    def test_initial24_new_work_is_exact_complement(self):
        self.assertEqual(q.phase_plan('initial24'), [96,97,99,101,102,103,336,337,339,341,342,343])
        self.assertEqual(sorted(list(q.SCREEN)+q.phase_plan('initial24')), list(q.INITIAL))

    def test_full_work_is_exact_remaining336(self):
        values = q.phase_plan('full360')
        self.assertEqual(len(values), 336)
        self.assertEqual(sorted(list(q.INITIAL)+values), list(range(360)))

    def test_screen_reexecution_is_not_an_entry_point(self):
        with self.assertRaises(ValueError):
            q.phase_plan('screen')

    def test_old_screen_generation_is_not_relabelled700(self):
        item = self.item()
        result = q.with_generation(item, 'new-freeze')
        self.assertEqual(result['generation_source_count'], 698)
        self.assertEqual(result['generation_source_freeze_sha256'], q.OLD_FREEZE_SHA)
        self.assertNotIn('generation_source_count', item)

    def test_new_generation_binds_new_freeze(self):
        result = q.with_generation(self.item(96, 'releasealign-initial24-cases'), 'new-freeze')
        self.assertEqual(result['generation_source_count'], 700)
        self.assertEqual(result['generation_source_freeze_sha256'], 'new-freeze')

    def test_archive_and_new_roots_are_separate(self):
        self.assertEqual(q.resolve_raw(self.item()).parent, q.OLD_BUILD/'releasealign-screen-cases')
        self.assertEqual(q.resolve_raw(self.item(96, 'releasealign-initial24-cases')).parent,
                         q.BUILD/'releasealign-initial24-cases')
        self.assertEqual(q.resolve_raw(self.item(50, 'releasealign-full360-cases')).parent,
                         q.BUILD/'releasealign-full360-cases')

    def test_storage_folder_spoof_is_rejected(self):
        for item in (self.item(0, 'releasealign-initial24-cases'), self.item(96, 'releasealign-screen-cases'),
                     self.item(50, 'releasealign-initial24-cases'), self.item(96, '../releasealign-initial24-cases')):
            with self.subTest(item=item), self.assertRaises(AssertionError):
                q.resolve_raw(item)

    def test_path_escape_and_bool_indices_are_rejected(self):
        for item in (dict(self.item(), file='../secret.gz'), self.item(True), self.item(-1), self.item(360)):
            with self.subTest(item=item), self.assertRaises(AssertionError):
                q.resolve_raw(item)

    def test_proof_binds_generation_without_modifying_case(self):
        case = dict(q.with_generation(self.item(), 'new'), audit={'passed':True}, compact={'x':1})
        before = deepcopy(case)
        proof = q.proof_payload(case, 'initial24', {'b':'sha2','a':'sha1'})
        self.assertEqual(proof['generation_source_count'], 698)
        self.assertEqual(proof['generation_source_freeze_sha256'], q.OLD_FREEZE_SHA)
        self.assertEqual(proof['source_map_sha256'], q.sha(q.raw({'a':'sha1','b':'sha2'})))
        self.assertEqual(case, before)

    def test_frozen_previous_module_globals_are_unchanged(self):
        self.assertEqual(q.previous.BUILD, q.OLD_BUILD)
        self.assertNotEqual(q.previous.BUILD, q.BUILD)
        self.assertEqual(len(q.NEW_SOURCES), 2)

    def test_continuation_inventory_retains_old_screen(self):
        value = q.continuation()
        self.assertEqual(value['archive_sha256'], q.ARCHIVE_SHA)
        self.assertEqual(value['inherited_screen_indices'], list(q.SCREEN))
        self.assertEqual([i['source_index'] for i in value['inherited_screen_raws']], list(q.SCREEN))
        self.assertTrue(all(set(i)==set(q.BASE_FIELDS) for i in value['inherited_screen_raws']))

    def test_new_source_inventory_is_only_two_additions(self):
        value = q.bindings()
        old = q.read(q.OLD_BUILD/'input-manifest.json')
        self.assertEqual(len(value['source_sha256']), 700)
        self.assertEqual({k:value['source_sha256'][k] for k in old['source_sha256']}, old['source_sha256'])
        self.assertEqual(set(value['source_sha256'])-set(old['source_sha256']), set(q.NEW_SOURCES))
        self.assertEqual(value['entries'], old['entries'])


if __name__ == '__main__':
    unittest.main()
