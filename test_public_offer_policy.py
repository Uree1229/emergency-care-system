"""Public evidence, original scope, and pending dominance boundaries."""
from copy import deepcopy
import unittest

from simulation.existing_info_engine import decide_early_offer
from simulation.public_offer_policy import decide_public_offer, VARIANTS


def view(*, capacity=3, used=0, status='fresh'):
    return dict(capacity=capacity, used=used, committed=0, queued=0,
        free=None if capacity is None or used is None else capacity-used,
        observed_at=None if status == 'unknown' else 1,
        received_at=None if status == 'unknown' else 1,
        sequence=None if status == 'unknown' else 10,
        source=None if status == 'unknown' else 'feed', status=status,
        age_minutes=None if status == 'unknown' else 0)


def snapshot():
    resources = {'A':{'ed_bed':view(capacity=1, used=1)},
        'B':{'ed_bed':view()}, 'C':{'ed_bed':view()}}
    patient = dict(id='P', priority=4, request_t=0, demand={'ed_bed':1}, required_capabilities=[])
    original = dict(t=1, patient=patient, first_valid_t=.75, pending_hospitals=['C'],
        valid_offers=[dict(hospital='B', accepted_at=.75, expires_at=10,
            resources=deepcopy(resources['B']), travel_minutes=5)],
        structural_candidates=[dict(hospital='A',travel_minutes=3),dict(hospital='B',travel_minutes=5),
            dict(hospital='C',travel_minutes=8)],
        search_patients=[dict(deepcopy(patient),compatible_hospitals=['A','B','C'])])
    return dict(schema_version=1,evaluated_t=1,mode='independent_replies',patient_state='SEARCH',
        first_commit_t=None,wait_minutes=2,original_snapshot=original,
        pending_inquiries=[dict(hospital='C',attempt=1,submitted_at=0,timeout_at=3,ask_event_sequence=3)],
        structural_resource_views=resources)


class PublicOfferPolicyTests(unittest.TestCase):
    def test_fixed_rule_extends_only_not_dominant_with_public_shortage(self):
        value=snapshot()
        self.assertEqual(VARIANTS,('public_blocked',))
        self.assertEqual(decide_early_offer(value['original_snapshot'])['reason'],'NOT_DOMINANT')
        output=decide_public_offer(snapshot=value)
        self.assertTrue(output['eligible'])
        self.assertEqual(output['hospital'],'B')
        self.assertEqual(output['candidate_checks'],[dict(hospital='A',pending=False,
            shortage_resources=['ed_bed'],publicly_blocked=True)])

    def test_fresh_sufficient_closer_candidate_blocks(self):
        value=snapshot();value['structural_resource_views']['A']['ed_bed']=view()
        self.assertEqual(decide_public_offer(snapshot=value)['reason'],'BETTER_STRUCTURAL_NOT_PUBLICLY_BLOCKED')

    def test_unknown_or_stale_closer_cannot_supply_a_shortage_proof(self):
        for v in (view(capacity=None,used=None,status='unknown'),view(capacity=1,used=1,status='stale')):
            value=snapshot();value['structural_resource_views']['A']['ed_bed']=v
            self.assertFalse(decide_public_offer(snapshot=value)['eligible'])

    def test_actual_pending_cannot_be_discarded_even_if_currently_short(self):
        value=snapshot();value['original_snapshot']['pending_hospitals']=['A','C']
        value['pending_inquiries'].insert(0,dict(hospital='A',attempt=2,submitted_at=.5,timeout_at=3.5,ask_event_sequence=4))
        self.assertEqual(decide_public_offer(snapshot=value)['reason'],'BETTER_ACTUAL_PENDING')

    def test_every_pending_is_checked_and_older_inquiry_is_not_erased(self):
        value=snapshot();value['original_snapshot']['structural_candidates'][-1]['travel_minutes']=4
        value['pending_inquiries'][0]['submitted_at']=0
        self.assertFalse(decide_public_offer(snapshot=value)['pending_checks'][0]['at_least_offer_rank'])

    def test_equal_distance_uses_original_hospital_identity_tie(self):
        value=snapshot();value['original_snapshot']['structural_candidates'][-1]['travel_minutes']=5
        self.assertTrue(decide_public_offer(snapshot=value)['eligible'])
        value=snapshot();value['original_snapshot']['structural_candidates'][1]['hospital']='Z'
        value['original_snapshot']['valid_offers'][0]['hospital']='Z'
        value['structural_resource_views']['Z']=value['structural_resource_views'].pop('B')
        value['original_snapshot']['structural_candidates'][-1]['travel_minutes']=5
        self.assertEqual(decide_public_offer(snapshot=value)['reason'],'BETTER_ACTUAL_PENDING')

    def test_shared_capacity_uses_the_full_original_registered_cohort(self):
        value=snapshot();other=deepcopy(value['original_snapshot']['search_patients'][0])
        other.update(id='Q',priority=1,demand={'ed_bed':3})
        value['original_snapshot']['search_patients'].append(other)
        self.assertEqual(decide_public_offer(snapshot=value)['reason'],'SHARED_CAPACITY')

    def test_original_early_success_is_not_relabelled_as_new_success(self):
        value=snapshot();value['original_snapshot']['structural_candidates'][0]['travel_minutes']=6
        self.assertEqual(decide_public_offer(snapshot=value)['original_reason'],'EARLY_READY')
        self.assertFalse(decide_public_offer(snapshot=value)['eligible'])

    def test_unusable_or_expired_offers_do_not_become_new_readiness(self):
        value=snapshot();bad=view(status='stale');value['structural_resource_views']['B']['ed_bed']=bad
        value['original_snapshot']['valid_offers'][0]['resources']['ed_bed']=deepcopy(bad)
        self.assertEqual(decide_public_offer(snapshot=value)['original_reason'],'NO_FRESH_OFFER')
        value=snapshot();value['original_snapshot']['valid_offers'][0]['expires_at']=1
        with self.assertRaises(ValueError):decide_public_offer(snapshot=value)

    def test_second_required_resource_can_supply_exact_current_shortage(self):
        value=snapshot();value['original_snapshot']['patient']['demand']['team']=1
        value['original_snapshot']['search_patients'][0]['demand']['team']=1
        for hid,resources in value['structural_resource_views'].items():resources['team']=view()
        value['structural_resource_views']['A']['ed_bed']=view()
        value['structural_resource_views']['A']['team']=view(capacity=1,used=1)
        value['original_snapshot']['valid_offers'][0]['resources']=deepcopy(value['structural_resource_views']['B'])
        output=decide_public_offer(snapshot=value)
        self.assertTrue(output['eligible'])
        self.assertEqual(output['candidate_checks'][0]['shortage_resources'],['team'])

    def test_one_fresh_required_resource_shortage_proves_infeasible_despite_other_unknown(self):
        for status in ('unknown','stale'):
            value=snapshot();value['original_snapshot']['patient']['demand']['team']=1
            value['original_snapshot']['search_patients'][0]['demand']['team']=1
            for resources in value['structural_resource_views'].values():resources['team']=view()
            value['structural_resource_views']['A']['team']=view(capacity=None,used=None,status=status) \
                if status=='unknown' else view(status=status)
            value['original_snapshot']['valid_offers'][0]['resources']=deepcopy(value['structural_resource_views']['B'])
            self.assertTrue(decide_public_offer(snapshot=value)['eligible'])

    def test_current_patient_and_cohort_self_row_must_agree(self):
        value=snapshot();value['original_snapshot']['search_patients'][0]['priority']=3
        with self.assertRaises(ValueError):decide_public_offer(snapshot=value)

    def test_all_original_pending_routes_and_all_structural_views_are_required(self):
        for part in ('pending','views'):
            value=snapshot()
            if part=='pending':value['pending_inquiries'][0]['hospital']='MISSING'
            else:value['structural_resource_views'].pop('A')
            with self.assertRaises(ValueError):decide_public_offer(snapshot=value)

    def test_exact_schema_rejects_future_fields(self):
        for where in ('top','view','pending'):
            value=snapshot();target=value if where=='top' else value['structural_resource_views']['A']['ed_bed'] \
                if where=='view' else value['pending_inquiries'][0]
            target['future_finish_t']=2
            with self.assertRaises(ValueError):decide_public_offer(snapshot=value)

    def test_snapshot_and_original_shadow_are_not_mutated(self):
        value=snapshot();before=deepcopy(value);shadow=decide_early_offer(value['original_snapshot'])
        output=decide_public_offer(snapshot=value);output['candidate_checks'].clear()
        self.assertEqual(value,before)
        self.assertEqual(decide_early_offer(value['original_snapshot']),shadow)

    def test_actual_search_scope_and_original_two_minute_wait_are_required(self):
        value=snapshot();value['patient_state']='TRANSIT'
        self.assertEqual(decide_public_offer(snapshot=value)['reason'],'OUTSIDE_ORIGINAL_READINESS_SCOPE')
        value=snapshot();value['first_commit_t']=.5
        self.assertEqual(decide_public_offer(snapshot=value)['reason'],'OUTSIDE_ORIGINAL_READINESS_SCOPE')
        value=snapshot();value['wait_minutes']=3
        with self.assertRaises(ValueError):decide_public_offer(snapshot=value)


if __name__=='__main__':unittest.main()
