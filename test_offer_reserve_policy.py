"""The fixed reserve boundary, complete cohort and all actual usable offers."""
from copy import deepcopy
import unittest

from simulation.offer_reserve_policy import VARIANTS, PUBLIC_OFFER_SPEC, decide_offer_reserve
from test_public_offer_policy import snapshot, view


def set_free(value, hospital, quantity, resource='ed_bed'):
    resources = value['structural_resource_views'][hospital]
    resources[resource] = view(capacity=quantity)
    for offer in value['original_snapshot']['valid_offers']:
        if offer['hospital'] == hospital:
            offer['resources'] = deepcopy(resources)


class OfferReservePolicyTests(unittest.TestCase):
    def test_single_fixed_rule_declares_new_reserve_threshold(self):
        self.assertEqual(VARIANTS,('cohort_reserve',))
        self.assertTrue(PUBLIC_OFFER_SPEC['new_threshold'])
        self.assertEqual(PUBLIC_OFFER_SPEC['reserve_own_demand_copies'],1)
        self.assertFalse(PUBLIC_OFFER_SPEC['unregistered_patient_nonworsening_guarantee'])

    def test_exact_boundary_and_one_less_or_more(self):
        for free, expected in ((1,False),(2,True),(3,True)):
            with self.subTest(free=free):
                value=snapshot();set_free(value,'B',free)
                result=decide_offer_reserve(snapshot=value)
                self.assertTrue(result['base_decision']['eligible'])
                self.assertEqual(result['eligible'],expected)
                proof=result['option_checks'][0]['resources']['ed_bed']
                self.assertEqual(proof,dict(cohort_demand=1,own_demand=1,free=free,
                    required_free=2,residual_after_cohort=free-1,passed=expected))

    def test_complete_compatible_cohort_counts_other_priorities_and_self_once(self):
        value=snapshot();other=deepcopy(value['original_snapshot']['search_patients'][0])
        other.update(id='Q',priority=1,demand={'ed_bed':2})
        value['original_snapshot']['search_patients'].append(other)
        result=decide_offer_reserve(snapshot=value)
        self.assertTrue(result['base_decision']['eligible'])
        self.assertFalse(result['eligible'])
        proof=result['option_checks'][0]['resources']['ed_bed']
        self.assertEqual((proof['cohort_demand'],proof['required_free']),(3,4))
        set_free(value,'B',4)
        self.assertTrue(decide_offer_reserve(snapshot=value)['eligible'])

    def test_incompatible_registered_patient_does_not_consume_this_cohort(self):
        value=snapshot();other=deepcopy(value['original_snapshot']['search_patients'][0])
        other.update(id='Q',compatible_hospitals=['A'],demand={'ed_bed':100})
        value['original_snapshot']['search_patients'].append(other)
        self.assertTrue(decide_offer_reserve(snapshot=value)['eligible'])

    def test_nonbest_usable_offer_cannot_bypass_reserve(self):
        value=snapshot();original=value['original_snapshot']
        value['structural_resource_views']['D']={'ed_bed':view(capacity=1)}
        original['structural_candidates'].append(dict(hospital='D',travel_minutes=6))
        original['search_patients'][0]['compatible_hospitals'].append('D')
        original['valid_offers'].append(dict(hospital='D',accepted_at=.75,expires_at=10,
            resources=deepcopy(value['structural_resource_views']['D']),travel_minutes=6))
        result=decide_offer_reserve(snapshot=value)
        self.assertTrue(result['base_decision']['eligible'])
        self.assertEqual(result['usable_options'],['B','D'])
        self.assertFalse(result['eligible'])
        self.assertEqual([row['passed'] for row in result['option_checks']],[True,False])
        original['valid_offers'].reverse()
        self.assertEqual(decide_offer_reserve(snapshot=value)['usable_options'],['D','B'])

    def test_required_second_resource_failure_blocks_despite_spare_beds(self):
        value=snapshot();original=value['original_snapshot']
        original['patient']['demand']['team']=1
        original['search_patients'][0]['demand']['team']=1
        for resources in value['structural_resource_views'].values():
            resources['team']=view(capacity=1)
        original['valid_offers'][0]['resources']=deepcopy(value['structural_resource_views']['B'])
        result=decide_offer_reserve(snapshot=value)
        self.assertTrue(result['base_decision']['eligible'])
        self.assertFalse(result['eligible'])
        self.assertTrue(result['option_checks'][0]['resources']['ed_bed']['passed'])
        self.assertFalse(result['option_checks'][0]['resources']['team']['passed'])

    def test_capacity_is_not_free_when_committed_and_queued_are_known(self):
        value=snapshot();resource=value['structural_resource_views']['B']['ed_bed']
        resource.update(capacity=4,committed=2,queued=1,free=1)
        value['original_snapshot']['valid_offers'][0]['resources']=deepcopy(value['structural_resource_views']['B'])
        result=decide_offer_reserve(snapshot=value)
        self.assertTrue(result['base_decision']['eligible'])
        self.assertFalse(result['eligible'])

    def test_unknown_or_stale_offer_does_not_prove_reserve(self):
        for status in ('unknown','stale'):
            value=snapshot()
            resource=view(capacity=None,used=None,status='unknown') if status=='unknown' else view(status='stale')
            value['structural_resource_views']['B']['ed_bed']=resource
            value['original_snapshot']['valid_offers'][0]['resources']=deepcopy(value['structural_resource_views']['B'])
            result=decide_offer_reserve(snapshot=value)
            self.assertFalse(result['base_decision']['eligible'])
            self.assertEqual((result['option_checks'],result['usable_options']),([],[]))

    def test_failed_base_has_no_reserve_action_and_original_reason_retained(self):
        value=snapshot();value['structural_resource_views']['A']['ed_bed']=view()
        result=decide_offer_reserve(snapshot=value)
        self.assertEqual(result['reason'],'BASE_PUBLIC_OFFER_INELIGIBLE')
        self.assertEqual(result['base_decision']['reason'],'BETTER_STRUCTURAL_NOT_PUBLICLY_BLOCKED')
        self.assertIsNone(result['hospital'])

    def test_future_fields_rejected_and_input_is_detached(self):
        value=snapshot();before=deepcopy(value)
        result=decide_offer_reserve(snapshot=value)
        result['base_decision']['candidate_checks'].clear()
        self.assertEqual(value,before)
        value['future_patient_demand']={'ed_bed':3}
        with self.assertRaises(ValueError):decide_offer_reserve(snapshot=value)
        with self.assertRaises(ValueError):decide_offer_reserve(snapshot=before,variant='two_copies')


if __name__=='__main__':unittest.main()
