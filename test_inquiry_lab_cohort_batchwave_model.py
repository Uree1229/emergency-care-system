"""Synthetic reference preservation, primary restriction and full dispatch."""
from copy import deepcopy
from functools import lru_cache
import inspect
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_batchwave_model as model
from test_inquiry_lab_cohort_startslots_policy import initial_fixture as previous_initial


@lru_cache(maxsize=4)
def _reference_fixture(count=4,hospitals=6):
    args=previous_initial(count,hospitals);j,ms,ts,p,owners,parent=args
    reference=model.build_batch_reference(parent,members=ms,tail_members=ts,joint_context=j,priority_context=p)
    return args,reference


def fixture(count=4,hospitals=6):return deepcopy(_reference_fixture(count,hospitals))


def allocate(args,reference):return model.allocate_reference(reference,roster=args[1]+args[2],priority_context=args[3])


class BatchWaveModelTests(unittest.TestCase):
    def test_one_frozen_D_reference_uses_original_actual_Stoploss_anchor(self):
        args=previous_initial();j,ms,ts,p,owners,parent=args;before=deepcopy(args)
        with patch.object(model.dynamic,'_score',wraps=model.dynamic._score) as D:
            ref=model.build_batch_reference(parent,members=ms,tail_members=ts,joint_context=j,priority_context=p)
        self.assertEqual(D.call_count,1);self.assertEqual(args,before)
        self.assertEqual(ref['stoploss_actual_anchor_index'],parent['evidence']['stoploss_anchor_index'])
        self.assertFalse(ref['actual_Tailwait_continuation_claim']);self.assertFalse(ref['residual_owner_replanning'])

    def test_current_tail_and_singleton_reference_provenance_and_order(self):
        args,ref=fixture(count=5);records=ref['owner_references']
        self.assertEqual([r['patient_id'] for r in records],[m['patient_id'] for m in args[1]+args[2]])
        self.assertEqual([r['kind'] for r in records],['original_Stoploss_anchor_current_pair']*2+['frozen_CS_reference_tail_pair']*2+['frozen_H_reference_singleton'])
        self.assertEqual([r['reference_cardinality'] for r in records],[len(r['reference_send']) for r in records])

    def test_primary_edges_are_only_preserved_reference_queries(self):
        args,ref=fixture();before=deepcopy((args,ref));proof=allocate(args,ref)
        self.assertEqual((args,ref),before);self.assertTrue(proof['coverage_complete'])
        self.assertEqual({(e['owner_index'],e['hospital']) for e in proof['primary_edges']},
            {(i,h) for i,r in enumerate(ref['owner_references']) for h in r['reference_send']})
        for edge in proof['coverage']['assignment']:self.assertIn(edge['hospital'],ref['owner_references'][edge['owner_index']]['reference_send'])

    def test_all_queries_and_counts_preserved_with_primary_then_secondary_dispatch(self):
        args,ref=fixture();proof=allocate(args,ref);dispatch=proof['dispatch_order'];n=len(ref['owner_references'])
        self.assertTrue(all(r['phase']=='matched_primary' for r in dispatch[:n]))
        self.assertTrue(all(r['phase']=='remaining_preserved_reference' for r in dispatch[n:]))
        self.assertEqual(len(dispatch),sum(r['reference_cardinality'] for r in ref['owner_references']))
        for i,r in enumerate(ref['owner_references']):
            sent=[x['hospital'] for x in dispatch if x['owner_index']==i]
            self.assertEqual(len(sent),len(r['reference_send']));self.assertEqual(set(sent),set(r['reference_send']))
        rest=dispatch[n:];self.assertEqual([(r['owner_index'],r['reference_send_index']) for r in rest],sorted((r['owner_index'],r['reference_send_index']) for r in rest))

    def test_primary_capacity_only_secondaries_are_permitted_to_queue(self):
        args,ref=fixture();proof=allocate(args,ref);primary=proof['dispatch_order'][:len(ref['owner_references'])]
        self.assertTrue(all(sum(r['hospital']==h for r in primary)<=proof['public_assumed_workers'] for h in {r['hospital'] for r in primary}))
        self.assertTrue(proof['secondary_queries_may_queue_normally']);self.assertFalse(proof['old_probability_wait_guards_applicable'])
        self.assertTrue(proof['whole_wave_metrics_are_uncontended_reference_only'])

    def test_no_coverage_does_not_drop_or_replace_reference_queries(self):
        args,ref=fixture(hospitals=3);proof=allocate(args,ref)
        self.assertFalse(proof['coverage_complete']);self.assertEqual(proof['dispatch_order'],[])
        self.assertEqual(proof['coverage']['flow'],3);self.assertFalse(proof['all_reference_queries_preserved'])

    def test_changed_reference_set_or_projection_digest_is_rejected(self):
        args,ref=fixture();ref['owner_references'][0]['reference_send'].pop()
        with self.assertRaises(ValueError):allocate(args,ref)
        args,ref=fixture();ref['dynamic_reference_row']['projection']['current_pair_send'][0].reverse()
        with self.assertRaises(ValueError):allocate(args,ref)

    def test_typed_priority_partition_and_source_anchor_are_required(self):
        args,ref=fixture();args[3]['patients'].pop()
        with self.assertRaises(ValueError):allocate(args,ref)
        args,ref=fixture();args[-1]['evidence']['stoploss_anchor_index']=-1
        with self.assertRaises(ValueError):model.build_batch_reference(args[-1],members=args[1],tail_members=args[2],joint_context=args[0],priority_context=args[3])

    def test_no_outcomes_staffing_or_new_reference_search_arguments(self):
        self.assertEqual(set(inspect.signature(model.allocate_reference).parameters),{'reference','roster','priority_context'})
        args,ref=fixture()
        with patch.object(model.dynamic,'_score',side_effect=AssertionError('reference is already frozen')):allocate(args,ref)
        self.assertEqual(model.EPSILON,model.matching.EPSILON)


if __name__=='__main__':unittest.main()
