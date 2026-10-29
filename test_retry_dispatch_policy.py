"""Dispatch invariants from public contacts and unchanged retry deadlines."""
from copy import deepcopy
import unittest

from server.inquiry_v4 import decide as base_decide
from simulation.retry_dispatch_policy import (
    VARIANTS, RETRY_DISPATCH_SPEC, condition_fingerprint, decide, resource_change_reason,
)


def view(*, capacity=1, used=0, status='fresh', observed=0, received=0, sequence=1, at=1):
    if observed is None:
        received=sequence=None
    return {'ed_bed': dict(capacity=capacity, used=used, committed=0, queued=0,
        free=None if capacity is None or used is None else capacity-used,
        observed_at=observed, received_at=received, sequence=sequence, source=None if observed is None else 'feed',
        status=status, age_minutes=None if observed is None else at-observed)}


def example(*, t=1, offered=('A', 'B'), contacts=(), barrier=None):
    condition = dict(id='P', priority=4, demand={'ed_bed': 1}, required_capabilities=['basic'])
    fp = condition_fingerprint(condition)
    history = []
    for index, hospital in enumerate(contacts):
        history.append(dict(patient='P', hospital=hospital, attempt=1+sum(r['hospital']==hospital for r in history),
            submitted_at=0, ask_event_sequence=index, condition_fingerprint=fp, resources=view(at=0)))
    barriers = [] if barrier is None else [dict(patient='P', hospital='A', received_t=.5,
        wake_event_sequence=100, report_event_sequence=90, previous_retry_at=barrier,
        condition_fingerprint=fp, resources=view(at=.5))]
    context = dict(schema_version=1, evaluated_t=t,
        patient=dict(id='P', state='SEARCH', first_commit_t=None, first_accept_event_sequence=None,
                     condition=condition, condition_fingerprint=fp),
        episode=dict(id=0, contacts=len(contacts)),
        universe=[dict(hospital=h, travel_minutes=1+index, resources=view(at=t)) for index,h in enumerate(('A','B'))],
        contacts=history, retry_barriers=barriers)
    inputs = dict(now=86400+int(t*60), candidates=[dict(hospital_id=h,
        travel={'duration_seconds': 60 if h=='A' else 120}) for h in offered],
        observations=[], pending=[], patient_pending=[], max_new=3, last_contact_at=None)
    return inputs, context


class RetryDispatchPolicyTests(unittest.TestCase):
    def test_three_fixed_ablations_have_explicit_unchanged_limits(self):
        self.assertEqual(VARIANTS, ('frontier','paced','balanced'))
        self.assertEqual(RETRY_DISPATCH_SPEC['maximum_open_inquiries'],3)
        self.assertEqual(RETRY_DISPATCH_SPEC['maximum_episode_contacts'],10)
        self.assertFalse(RETRY_DISPATCH_SPEC['backoff_added'])

    def test_never_contacted_hospital_precedes_repeat(self):
        inputs, context=example(contacts=('A',))
        result=decide('frontier',inputs=inputs,context=context)
        self.assertEqual(result['send'],['B'])
        self.assertEqual(result['evidence']['retry_dispatch']['kernel_candidate_ids'],['B'])

    def test_visited_only_report_wake_does_not_hide_unvisited_universe(self):
        inputs,context=example(offered=('A',),contacts=('A',))
        result=decide('frontier',inputs=inputs,context=context)
        self.assertEqual(result['send'],[])
        self.assertEqual(result['reason'],'frontier_not_offered')
        self.assertEqual(result['evidence']['retry_dispatch']['frontier_hospitals'],['B'])

    def test_kernel_original_reason_is_separate_from_dispatch_deferral(self):
        inputs,context=example(offered=('A',),contacts=('A',))
        result=decide('frontier',inputs=inputs,context=context)
        kernel_inputs=deepcopy(inputs)
        kernel_inputs['candidates']=[]
        self.assertEqual(result['evidence']['retry_dispatch']['kernel_decision'],base_decide(**kernel_inputs))
        self.assertEqual(result['evidence']['retry_dispatch']['kernel_decision']['reason'],'no_candidates')
        self.assertEqual(result['reason'],'frontier_not_offered')

    def test_least_contacted_round_survives_multiple_episodes(self):
        inputs,context=example(contacts=('A','A','A','B','B'))
        context['episode']=dict(id=3,contacts=0)
        self.assertEqual(decide('frontier',inputs=inputs,context=context)['send'],['B'])

    def test_old_patient_condition_contacts_are_not_repeated_current_inquiries(self):
        inputs,context=example(contacts=('A',))
        context['contacts'][0]['condition_fingerprint']='old condition'
        self.assertEqual(decide('frontier',inputs=inputs,context=context)['send'],['A'])

    def test_original_v4_cost_ranks_within_equal_contact_tier(self):
        inputs,context=example()
        result=decide('frontier',inputs=inputs,context=context)
        self.assertEqual(result['send'],base_decide(**inputs)['send'])
        self.assertEqual(result['evidence']['retry_dispatch']['kernel_decision'],base_decide(**inputs))

    def test_same_content_fresh_feed_preserves_original_retry_wait(self):
        inputs,context=example(offered=('A',),contacts=('A','B'),barrier=5)
        context['universe'][0]['resources']=view(observed=1,received=1,sequence=99)
        result=decide('paced',inputs=inputs,context=context)
        self.assertEqual(result['send'],[])
        self.assertEqual(result['reason'],'retry_wait_preserved')
        self.assertEqual(result['evidence']['retry_dispatch']['preserved_retry_until'],5)

    def test_frontier_ablation_does_not_preserve_the_retry_barrier(self):
        inputs,context=example(offered=('A',),contacts=('A','B'),barrier=5)
        self.assertEqual(decide('frontier',inputs=inputs,context=context)['send'],['A'])

    def test_global_original_retry_wait_cannot_be_bypassed_with_an_unvisited_hospital(self):
        inputs,context=example(offered=('B',),contacts=('A',),barrier=5)
        self.assertEqual(decide('paced',inputs=inputs,context=context)['send'],[])

    def test_original_retry_wait_expires_without_creating_a_new_timeout(self):
        inputs,context=example(t=5,offered=('A',),contacts=('A','B'),barrier=5)
        result=decide('paced',inputs=inputs,context=context)
        self.assertEqual(result['send'],['A'])
        self.assertIsNone(result['evidence']['retry_dispatch']['preserved_retry_until'])

    def test_real_used_quantity_change_releases_only_that_candidate(self):
        inputs,context=example(contacts=('A','B'),barrier=5)
        context['contacts'][0]['resources']=view(used=1,at=0)
        result=decide('paced',inputs=inputs,context=context)
        self.assertEqual(result['send'],['A'])
        self.assertEqual(result['evidence']['retry_dispatch']['meaningful_change_exceptions'],
                         {'A':'capacity_or_used_changed'})

    def test_real_capacity_change_releases_existing_wait(self):
        inputs,context=example(offered=('A',),contacts=('A','B'),barrier=5)
        context['contacts'][0]['resources']=view(capacity=2,at=0)
        self.assertEqual(decide('paced',inputs=inputs,context=context)['send'],['A'])

    def test_unknown_to_fresh_can_release_existing_wait(self):
        inputs,context=example(offered=('A',),contacts=('A','B'),barrier=5)
        context['contacts'][0]['resources']=view(capacity=None,used=None,status='unknown',observed=None,at=0)
        self.assertEqual(decide('paced',inputs=inputs,context=context)['send'],['A'])

    def test_stale_to_fresh_can_release_existing_wait(self):
        inputs,context=example(offered=('A',),contacts=('A','B'),barrier=5)
        context['contacts'][0]['resources']=view(status='stale',at=0)
        self.assertEqual(decide('paced',inputs=inputs,context=context)['send'],['A'])

    def test_fresh_to_stale_is_not_a_recovery(self):
        self.assertIsNone(resource_change_reason(view(),view(status='stale')))

    def test_commitments_and_queues_alone_are_not_public_quantity_recovery(self):
        changed=view()
        changed['ed_bed'].update(committed=1,queued=1,free=-1)
        self.assertIsNone(resource_change_reason(view(),changed))

    def test_balanced_prefers_less_submitted_unresolved_work(self):
        inputs,context=example()
        inputs['pending']=[dict(hospital='A',patient='other',submitted_at=86400)]
        self.assertEqual(decide('balanced',inputs=inputs,context=context)['send'],['B'])

    def test_balanced_deduplicates_the_public_ledger(self):
        inputs,context=example()
        row=dict(hospital='A',patient='other',submitted_at=86400)
        inputs['pending']=[row,row]
        result=decide('balanced',inputs=inputs,context=context)
        self.assertEqual(result['evidence']['retry_dispatch']['pending_loads'],
                         [dict(hospital='A',unresolved=1),dict(hospital='B',unresolved=0)])

    def test_balancing_cannot_override_frontier(self):
        inputs,context=example(contacts=('A',))
        inputs['pending']=[dict(hospital='B',patient=str(i),submitted_at=86400) for i in range(4)]
        self.assertEqual(decide('balanced',inputs=inputs,context=context)['send'],['B'])

    def test_balancing_does_not_create_a_global_concurrency_cap(self):
        inputs,context=example(offered=('A',))
        inputs['pending']=[dict(hospital='A',patient=str(i),submitted_at=86400) for i in range(20)]
        self.assertEqual(decide('balanced',inputs=inputs,context=context)['send'],['A'])

    def test_after_actual_acceptance_original_v4_is_delegated(self):
        inputs,context=example(offered=('A',),contacts=('A',),barrier=5)
        context['patient']['first_accept_event_sequence']=99
        result=decide('balanced',inputs=inputs,context=context)
        self.assertEqual(result['send'],base_decide(**inputs)['send'])
        self.assertFalse(result['evidence']['retry_dispatch']['scope_active'])

    def test_after_first_commitment_original_v4_is_delegated(self):
        inputs,context=example(offered=('A',),contacts=('A',),barrier=5)
        context['patient'].update(state='TRANSIT',first_commit_t=.5)
        self.assertEqual(decide('balanced',inputs=inputs,context=context)['send'],base_decide(**inputs)['send'])

    def test_original_contact_budget_cannot_be_exceeded(self):
        inputs,context=example()
        inputs['max_new']=0
        self.assertEqual(decide('frontier',inputs=inputs,context=context)['send'],[])

    def test_input_and_context_are_never_mutated(self):
        inputs,context=example(contacts=('A',),barrier=5)
        before=deepcopy((inputs,context))
        for variant in VARIANTS:
            decide(variant,inputs=inputs,context=context)
        self.assertEqual((inputs,context),before)

    def test_future_contact_is_rejected(self):
        inputs,context=example(contacts=('A',))
        context['contacts'][0]['submitted_at']=1.25
        with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_duplicate_source_event_is_rejected(self):
        inputs,context=example(contacts=('A',))
        context['contacts'].append(deepcopy(context['contacts'][0]))
        with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_hidden_schedule_in_context_is_rejected(self):
        inputs,context=example()
        context['future_finish_t']=2
        with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_hidden_work_inside_resource_view_is_rejected(self):
        inputs,context=example()
        context['universe'][0]['resources']['ed_bed']['finish_t']=2
        with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_future_public_report_and_incorrect_age_are_rejected(self):
        for change in ({'received_at':2},{'observed_at':2,'received_at':2},{'age_minutes':99}):
            inputs,context=example()
            context['universe'][0]['resources']['ed_bed'].update(change)
            with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_initial_stale_report_can_have_a_negative_source_time(self):
        inputs,context=example()
        context['universe'][0]['resources']=view(observed=-20,received=0,status='stale')
        self.assertEqual(decide('frontier',inputs=inputs,context=context)['send'],['A'])

    def test_contact_resource_view_uses_its_actual_submission_clock(self):
        inputs,context=example(contacts=('A',))
        context['contacts'][0]['resources']=view(observed=.25,received=.25,at=.25)
        with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_incorrect_public_resource_arithmetic_is_rejected(self):
        inputs,context=example()
        context['universe'][0]['resources']['ed_bed']['free']=2
        with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_nonoriginal_input_fields_are_rejected(self):
        inputs,context=example()
        inputs['workers']=2
        with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_candidate_must_belong_to_the_public_eligible_universe(self):
        inputs,context=example()
        context['universe']=context['universe'][1:]
        with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_condition_hash_cannot_be_supplied_arbitrarily(self):
        inputs,context=example()
        context['patient']['condition_fingerprint']='different'
        with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_episode_limit_and_clock_are_validated(self):
        for alteration in ('count','clock'):
            inputs,context=example()
            if alteration=='count':context['episode']['contacts']=11
            else:context['evaluated_t']=1.1
            with self.assertRaises(ValueError):decide('frontier',inputs=inputs,context=context)

    def test_unknown_variants_are_rejected(self):
        inputs,context=example()
        for variant in ('auto','best','v4',None):
            with self.assertRaises(ValueError):decide(variant,inputs=inputs,context=context)


if __name__=='__main__':unittest.main()
