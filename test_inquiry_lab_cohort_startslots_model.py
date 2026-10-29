"""Synthetic public capacity matching, augmentations and marginal extras."""
from copy import deepcopy
import inspect
from itertools import permutations
import math
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_startslots_model as model
from test_inquiry_lab_cohort_dynamic_context import fixture as public_fixture


def flow(costs,caps=None,order=None):
    hospitals=['H'+str(i) for i in range(len(costs[0]))]
    edges=[dict(owner_index=i,hospital=h,total_cost_seconds=v) for i,row in enumerate(costs) for h,v in zip(hospitals,row) if v is not None]
    return model.cover_slots(edges,owner_count=len(costs),hospital_capacities=caps or [dict(hospital=h,capacity=1) for h in hospitals],owner_order=list(range(len(costs))) if order is None else order)


def fixture(count=4,hospitals=6):
    _,j,ms,ts,p=public_fixture(count,hospitals);members=ms+ts
    proposals=[model.JOINT.original.decide(model.JOINT.original.NAME,**deepcopy(m['inputs'])) for m in members]
    return members,proposals,p


def mock_allocation(benefit=20.,cardinality=2):
    members,proposals,p=fixture(hospitals=5)
    models=[dict(owner=i,hospitals=[dict(hospital='H'+str(k),residual_wait_seconds=0.) for k in range(5)]) for i in range(4)]
    for proposal in proposals:proposal['send']=['H0','H1'][:cardinality]
    def metric(m,selected,extra):
        value=1500.-sum((100. if h=='H'+str(m['owner']) else benefit) for h in selected)
        return dict(total_cost_seconds=value,capped_wait_seconds=value,selection_probability=len(selected)*.1)
    with patch.object(model.JOINT,'_model',side_effect=models),patch.object(model.JOINT,'_metrics',side_effect=metric):
        return model.allocate_slots(members,baseline_proposals=proposals,priority_context=p)


class StartSlotModelTests(unittest.TestCase):
    def test_reverse_augmentation_repairs_earlier_greedy_choice(self):
        proof=flow([[1.,2.],[1.,100.]])
        self.assertEqual(proof['assignment'],[dict(owner_index=0,hospital='H1',total_cost_seconds=2.),dict(owner_index=1,hospital='H0',total_cost_seconds=1.)])
        self.assertTrue(any(s['reverse'] for a in proof['augmentations'] for s in a['path']))
        self.assertEqual(proof['total_edge_cost_seconds'],3.)

    def test_minimum_cost_matches_all_tiny_permutations(self):
        costs=[[5.,1.,8.],[2.,6.,4.],[3.,7.,2.]];proof=flow(costs)
        minimum=min(sum(costs[i][h] for i,h in enumerate(choice)) for choice in permutations(range(3)))
        self.assertEqual(proof['total_edge_cost_seconds'],minimum);self.assertTrue(proof['complete'])
        self.assertGreaterEqual(proof['minimum_residual_reduced_cost_seconds'],-model.EPSILON)

    def test_insufficient_distinct_slots_never_claims_full_coverage(self):
        proof=flow([[1.,None],[2.,None],[3.,None]])
        self.assertEqual(proof['flow'],1);self.assertFalse(proof['complete']);self.assertEqual(len(proof['assignment']),1)

    def test_integer_public_workers_allow_parallel_slots_and_stable_priority_ties(self):
        proof=flow([[1.],[1.]],caps=[dict(hospital='H0',capacity=2)],order=[1,0])
        self.assertTrue(proof['complete']);self.assertEqual(proof['owner_order'],[1,0])
        self.assertEqual(proof['augmentations'][0]['path'][0]['owner_index'],1)
        self.assertEqual(flow([[1.,1.],[1.,1.]]),flow([[1.,1.],[1.,1.]]))

    def test_invalid_edges_order_and_nonfinite_cost_fail_closed(self):
        kwargs=dict(owner_count=1,hospital_capacities=[dict(hospital='H0',capacity=1)],owner_order=[0])
        for edges in ([dict(owner_index=True,hospital='H0',total_cost_seconds=1.)],[dict(owner_index=0,hospital='H0',total_cost_seconds=math.nan)],
                      [dict(owner_index=0,hospital='H0',total_cost_seconds=1.)]*2):
            with self.assertRaises(ValueError):model.cover_slots(edges,**kwargs)
        with self.assertRaises(ValueError):model.cover_slots([],**dict(kwargs,owner_order=[True]))
        with self.assertRaises(ValueError):model.cover_slots([],**dict(kwargs,hospital_capacities=[dict(hospital='H0',capacity=True)]))

    def test_real_public_H_edges_and_all_owner_metrics_are_recorded_without_mutation(self):
        args=fixture();before=deepcopy(args);proof=model.allocate_slots(args[0],baseline_proposals=args[1],priority_context=args[2])
        self.assertEqual(args,before);self.assertTrue(proof['coverage_complete']);self.assertEqual(len(proof['final_owner_metrics']),4)
        self.assertTrue(all(1<=len(s)<=r['contact_limit'] for s,r in zip(proof['planned_sends'],proof['owner_records'])))
        self.assertTrue(all(r['used']<=r['capacity']==proof['public_assumed_workers'] for r in proof['hospital_slot_usage']))
        self.assertFalse(proof['original_probability_wait_guards_applicable']);self.assertFalse(proof['remaining_assignments_are_reservations'])

    def test_extras_require_positive_full_subset_marginal_and_remaining_capacity(self):
        proof=mock_allocation();self.assertEqual(len(proof['extras']),1);self.assertEqual(proof['extras'][0]['owner_index'],0)
        self.assertGreater(proof['extras'][0]['marginal_cost_decrease_seconds'],model.EPSILON)
        self.assertEqual(proof['extra_passes'][-1]['selected'],None);self.assertEqual(proof['extras'][0]['hospital'],'H4')

    def test_nonpositive_extras_and_original_H_count_one_are_not_added(self):
        self.assertEqual(mock_allocation(benefit=0.)['extras'],[])
        self.assertEqual(mock_allocation(cardinality=1)['extras'],[])
        proof=mock_allocation(cardinality=0);self.assertFalse(proof['coverage_complete']);self.assertIsNone(proof['planned_sends'])

    def test_scope_size_clocks_and_public_pending_are_structural(self):
        members,_,_=fixture();self.assertTrue(model.scope_members(members)[0])
        self.assertFalse(model.scope_members(members[:3])[0]);self.assertFalse(model.scope_members(members*5)[0])
        bad=deepcopy(members);bad[-1]['workflow_snapshot']['job']['started_at']-=1;self.assertFalse(model.scope_members(bad)[0])
        bad=deepcopy(members);bad[0]['inputs']['pending']=[dict(hospital='H0',patient='FOREIGN',submitted_at=1000)];self.assertFalse(model.scope_members(bad)[0])

    def test_typed_duplicate_patient_ids_and_incomplete_priority_are_rejected(self):
        members,proposals,p=fixture();members[1]['patient_id']=members[0]['patient_id']
        with self.assertRaises(ValueError):model.allocate_slots(members,baseline_proposals=proposals,priority_context=p)
        members,proposals,p=fixture();p['patients'].pop()
        with self.assertRaises(ValueError):model.allocate_slots(members,baseline_proposals=proposals,priority_context=p)

    def test_no_latent_staffing_outcomes_or_future_arguments(self):
        self.assertEqual(set(inspect.signature(model.allocate_slots).parameters),{'members','baseline_proposals','priority_context'})
        self.assertEqual(model.EPSILON,model.JOINT.WAIT_EPSILON)
        members,proposals,p=fixture();config=deepcopy(model.JOINT.spec()['config']);config['assumed_workers']=1.5
        with patch.object(model.JOINT,'spec',return_value=dict(config=config)):
            with self.assertRaises(ValueError):model.allocate_slots(members,baseline_proposals=proposals,priority_context=p)


if __name__=='__main__':unittest.main()
