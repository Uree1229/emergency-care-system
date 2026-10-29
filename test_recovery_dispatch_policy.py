"""Eligibility, actual report provenance, and single-use recovery tokens."""
from copy import deepcopy
import unittest

from simulation.retry_dispatch_policy import condition_fingerprint
from simulation.recovery_dispatch_policy import decide_recovery_reopen,RECOVERY_DISPATCH_SPEC


def resources(t,*,used=0,capacity=3,observed=None,received=None,sequence=40,status='fresh'):
    observed=t if observed is None else observed
    received=t if received is None else received
    return {'ed_bed':dict(capacity=capacity,used=used,committed=0,queued=0,free=capacity-used,
        observed_at=observed,received_at=received,sequence=sequence,source='feed',status=status,age_minutes=t-observed)}


def snapshot(variant='eager'):
    condition=dict(id='P',priority=4,demand={'ed_bed':1},required_capabilities=['basic'])
    fp=condition_fingerprint(condition)
    return dict(t=4.25,variant=variant,
        patient=dict(id='P',state='SEARCH',first_commit_t=None,first_accept_event_sequence=None,
            condition=condition,condition_fingerprint=fp,request_event_sequence=1),
        scheduling=dict(episode_id=0,episode_contacts=2,retry_at=None,next_policy_review_at=4.25,last_contact_at=1.75),
        tried_hospitals=['H01','H02'],
        pending_inquiries=[] if variant=='latched' else [dict(hospital='H02',attempt=1,submitted_at=1.75,
            timeout_at=4.75,ask_event_sequence=20)],valid_offers=[],
        candidates=[dict(hospital=h,travel_minutes=3+i*2,resources=resources(4.25,observed=4,received=4))
                    for i,h in enumerate(('H01','H02'))],
        contacts=[dict(patient='P',hospital='H01',attempt=1,submitted_at=1,ask_event_sequence=10,
                condition_fingerprint=fp,resources=resources(1,used=3,sequence=10)),
            dict(patient='P',hospital='H02',attempt=1,submitted_at=1.75,ask_event_sequence=20,
                condition_fingerprint=fp,resources=resources(1.75,sequence=20))],
        declines=[dict(patient='P',hospital='H01',t=1.5,decline_event_sequence=15,ask_event_sequence=10,
            attempt=1,condition_fingerprint=fp,resources=resources(1.5,used=3,observed=1,received=1,sequence=10))],
        transitions=[dict(hospital='H01',t=4,report_event_sequence=40,
            before_report_event_sequences={'ed_bed':30},
            before_resources=resources(4,used=3,observed=3,received=3,sequence=30),
            after_resources=resources(4,used=0,observed=4,received=4,sequence=40))],consumptions=[])


class RecoveryEligibilityTests(unittest.TestCase):
    def test_actual_decline_then_applied_content_recovery_reopens_one(self):
        result=decide_recovery_reopen(snapshot())
        self.assertEqual(result['hospital'],'H01')
        self.assertEqual(result['decline_event_sequence'],15)
        self.assertEqual(result['transition_report_event_sequence'],40)

    def test_latched_holds_proof_until_all_original_pending_resolve(self):
        row=snapshot('latched')
        row['pending_inquiries']=snapshot()['pending_inquiries']
        self.assertEqual(decide_recovery_reopen(row)['reason'],'WAIT_FOR_ORIGINAL_PENDING')
        row['pending_inquiries']=[]
        self.assertEqual(decide_recovery_reopen(row)['hospital'],'H01')

    def test_latched_can_interrupt_existing_future_retry_with_real_recovery(self):
        row=snapshot('latched');row['scheduling']['retry_at']=7.25
        self.assertEqual(decide_recovery_reopen(row)['hospital'],'H01')

    def test_latched_never_advances_an_unrelated_untried_candidate(self):
        for status in ('fresh','stale','unknown'):
            row=snapshot('latched');other=deepcopy(row['candidates'][1]);other['hospital']='H03'
            other['resources']['ed_bed']['status']=status
            if status=='unknown':
                other['resources']['ed_bed'].update(capacity=None,used=None,free=None,
                    observed_at=None,received_at=None,sequence=None,source=None,age_minutes=None)
            row['candidates'].append(other)
            result=decide_recovery_reopen(row)
            self.assertIsNone(result['hospital'])
            self.assertEqual(result['candidate_checks'][0]['reason'],'OTHER_NORMAL_CANDIDATE')

    def test_latched_fresh_insufficient_alternative_is_not_a_normal_candidate(self):
        row=snapshot('latched');other=deepcopy(row['candidates'][1]);other['hospital']='H03'
        other['resources']['ed_bed'].update(used=3,free=0);row['candidates'].append(other)
        self.assertEqual(decide_recovery_reopen(row)['hospital'],'H01')

    def test_eager_keeps_original_lane_when_an_untried_alternative_exists(self):
        row=snapshot();other=deepcopy(row['candidates'][1]);other['hospital']='H03'
        row['candidates'].append(other)
        self.assertEqual(decide_recovery_reopen(row)['hospital'],'H01')

    def test_eager_respects_existing_future_retry(self):
        row=snapshot();row['scheduling']['retry_at']=7.25
        self.assertEqual(decide_recovery_reopen(row)['reason'],'ORIGINAL_RETRY_NOT_DUE')

    def test_any_valid_offer_blocks_both_modes(self):
        for variant in ('eager','latched'):
            row=snapshot(variant);row['valid_offers']=[dict(hospital='H02',expires_at=5)]
            self.assertEqual(decide_recovery_reopen(row)['reason'],'VALID_OFFER')

    def test_expired_offer_is_not_renewed_and_does_not_block(self):
        row=snapshot();row['valid_offers']=[dict(hospital='H02',expires_at=4.25)]
        self.assertEqual(decide_recovery_reopen(row)['hospital'],'H01')

    def test_timeout_or_ack_without_actual_clinical_decline_cannot_reopen(self):
        row=snapshot();row['declines']=[]
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_report_received_after_decline_with_old_source_is_rejected(self):
        row=snapshot();r=row['transitions'][0]['after_resources']['ed_bed']
        r.update(observed_at=1,age_minutes=3)
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_exact_decline_source_time_is_not_newer_evidence(self):
        row=snapshot();r=row['transitions'][0]['after_resources']['ed_bed']
        r.update(observed_at=1.5,age_minutes=2.5)
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_before_decline_recovery_cannot_override_a_later_real_decline(self):
        row=snapshot();row['declines'][0].update(t=4,decline_event_sequence=41,
            resources=resources(4,used=3))
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_identical_content_timestamp_refresh_is_not_recovery(self):
        row=snapshot();row['transitions'][0]['before_resources']=resources(4,used=0,observed=3,received=3,sequence=30)
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_quantity_worsening_cannot_authorize_reopening(self):
        row=snapshot();row['transitions'][0]['after_resources']=resources(4,used=3)
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_stale_before_or_after_is_not_fresh_shortage_to_fresh_sufficient(self):
        for side in ('before_resources','after_resources'):
            row=snapshot();row['transitions'][0][side]['ed_bed']['status']='stale'
            self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_current_stale_or_insufficient_resources_invalidate_the_token(self):
        for values in (dict(status='stale'),dict(used=3,free=0)):
            row=snapshot();row['candidates'][0]['resources']['ed_bed'].update(values)
            self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_actual_newer_same_hospital_contact_invalidates_old_decline(self):
        row=snapshot();contact=deepcopy(row['contacts'][0])
        contact.update(attempt=2,submitted_at=4,ask_event_sequence=42,resources=resources(4))
        row['contacts'].append(contact)
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_changed_patient_requirements_cannot_reuse_old_decline(self):
        row=snapshot();row['patient']['condition']['priority']=3
        row['patient']['condition_fingerprint']=condition_fingerprint(row['patient']['condition'])
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_actual_target_pending_blocks_duplicate_work(self):
        row=snapshot();row['pending_inquiries'][0]['hospital']='H01'
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_only_an_originally_tried_hospital_can_be_reopened(self):
        row=snapshot();row['tried_hospitals']=['H02','H03']
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_last_tried_hospital_is_not_removed_to_renew_episode_budget(self):
        row=snapshot('latched');row['tried_hospitals']=['H01']
        self.assertEqual(decide_recovery_reopen(row)['reason'],'WOULD_RENEW_ORIGINAL_EPISODE')

    def test_original_ten_contact_and_three_pending_caps_are_retained(self):
        row=snapshot();row['scheduling']['episode_contacts']=10
        self.assertEqual(decide_recovery_reopen(row)['reason'],'CONTACT_OR_OPEN_LIMIT')
        row=snapshot();row['pending_inquiries']*=3
        self.assertEqual(decide_recovery_reopen(row)['reason'],'CONTACT_OR_OPEN_LIMIT')

    def test_original_policy_review_is_never_advanced(self):
        row=snapshot();row['scheduling']['next_policy_review_at']=4.5
        self.assertEqual(decide_recovery_reopen(row)['reason'],'ORIGINAL_REVIEW_NOT_DUE')

    def test_initial_search_ends_at_actual_acceptance_or_commit(self):
        for values in (dict(first_accept_event_sequence=42),dict(first_commit_t=4),dict(state='TRANSIT')):
            row=snapshot();row['patient'].update(values)
            self.assertEqual(decide_recovery_reopen(row)['reason'],'NOT_INITIAL_UNACCEPTED_SEARCH')

    def test_consumed_token_is_not_recreated_by_new_identical_reports(self):
        row=snapshot();result=decide_recovery_reopen(row)
        row['consumptions']=[dict(patient='P',hospital='H01',token_id=result['token_id'],
            condition_fingerprint=row['patient']['condition_fingerprint'],ask_event_sequence=45,
            review_event_sequence=43,reopen_event_sequence=44)]
        self.assertEqual(decide_recovery_reopen(row)['reason'],'PATIENT_REOPEN_ALREADY_CONSUMED')

    def test_later_semantic_loss_invalidates_earlier_recovery(self):
        row=snapshot();later=deepcopy(row['transitions'][0])
        later.update(t=4.25,report_event_sequence=42,
            before_resources=resources(4.25,observed=4,received=4),after_resources=resources(4.25,used=3))
        row['transitions'].append(later)
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_all_required_resources_must_be_newer_than_decline(self):
        row=snapshot();row['patient']['condition']['demand']['special_team']=1
        row['patient']['condition_fingerprint']=condition_fingerprint(row['patient']['condition'])
        for item in row['contacts']+row['declines']:
            item['condition_fingerprint']=row['patient']['condition_fingerprint']
            item['resources']['special_team']=deepcopy(item['resources']['ed_bed'])
        for c in row['candidates']:c['resources']['special_team']=deepcopy(c['resources']['ed_bed'])
        transition=row['transitions'][0]
        transition['before_report_event_sequences']['special_team']=30
        transition['before_resources']['special_team']=deepcopy(transition['before_resources']['ed_bed'])
        transition['after_resources']['special_team']=deepcopy(transition['after_resources']['ed_bed'])
        transition['after_resources']['special_team'].update(observed_at=1,age_minutes=3)
        self.assertIsNone(decide_recovery_reopen(row)['hospital'])

    def test_future_transition_hidden_state_or_wrong_view_arithmetic_is_rejected(self):
        for change in ('future','hidden','arithmetic'):
            row=snapshot()
            if change=='future':row['transitions'][0]['t']=5
            elif change=='hidden':row['future_finish_t']=5
            else:row['candidates'][0]['resources']['ed_bed']['free']=99
            with self.assertRaises(ValueError):decide_recovery_reopen(row)

    def test_candidate_views_must_cover_exact_current_required_resources(self):
        row=snapshot();row['candidates'][0]['resources']['unrelated']=row['candidates'][0]['resources'].pop('ed_bed')
        with self.assertRaises(ValueError):decide_recovery_reopen(row)

    def test_input_is_read_only_and_protocol_has_no_synthetic_acceptance(self):
        row=snapshot();before=deepcopy(row);decide_recovery_reopen(row)
        self.assertEqual(row,before)
        self.assertFalse(RECOVERY_DISPATCH_SPEC['old_inquiry_deadline_or_offer_ttl_renewed'])
        self.assertTrue(RECOVERY_DISPATCH_SPEC['fifo_work_acceptance_readiness_and_allocation_unchanged'])


if __name__=='__main__':unittest.main()
