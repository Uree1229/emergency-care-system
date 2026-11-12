"""Actual raw inquiry records, hospital work, and episode continuity."""
from collections import Counter
from copy import deepcopy
import unittest

from simulation.capability_probe_engine import run_capability_probe_branch
from simulation.cooperative_probe_scenarios import make_scenario
from simulation.retry_dispatch_engine import (
    DEFAULT_RETRY_DISPATCH_POLICIES, NAMESPACE_FIELDS, run_retry_dispatch_branch,
    run_retry_dispatch_replay,
)
from simulation.retry_dispatch_policy import decide, VARIANTS


def old(case,allocation='H1',code='same'):
    return run_capability_probe_branch(case,allocation=allocation,shortage_probe=True,
        cooperative_probe=True,capability_probe=True,branch_id=code)


class RetryDispatchEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scarce=make_scenario(7,'scarce_contact_concurrency_limits')
        cls.scarce_branches={v:run_retry_dispatch_branch(cls.scarce,variant=v) for v in VARIANTS}

    def test_unvisited_twelfth_hospital_is_reached_after_original_budget_rollover(self):
        result=self.scarce_branches['frontier']
        asks=[e for e in result['events'] if e['type']=='ask' and e['patient']=='P01']
        self.assertTrue(any(e['hospital']=='H12' for e in asks))
        p=next(p for p in result['patient_outcomes'] if p['id']=='P01')
        self.assertIsNotNone(p['treatment_start_t'])
        self.assertLess(p['inquiries'],50)
        self.assertTrue(any(e['hospital']=='H11' and e['inquiry_episode']>0 for e in asks))

    def test_all_variants_preserve_three_open_and_ten_contact_limits(self):
        for branch in self.scarce_branches.values():
            count=Counter()
            for event in branch['events']:
                if event['type']=='ask':count[event['patient'],event['inquiry_episode']]+=1
            self.assertLessEqual(max(count.values()),10)
            for row in branch['inquiry_decisions']:
                self.assertLessEqual(len(row['inputs']['patient_pending'])+len(row['decision']['send']),3)
                self.assertLessEqual(len(row['decision']['send']),row['inputs']['max_new'])

    def test_original_seven_inputs_and_nine_row_keys_remain_visible(self):
        row=self.scarce_branches['frontier']['inquiry_decisions'][0]
        self.assertEqual(set(row),{'t','patient','inquiry','inputs','decision','event_sequence',
            'inquiry_episode','ask_event_sequences','resource_check_sequences'})
        self.assertEqual(set(row['inputs']),{'now','candidates','observations','pending',
                                            'patient_pending','max_new','last_contact_at'})

    def test_internal_v4_slot_and_actual_new_selector_are_both_explicit(self):
        for variant,branch in self.scarce_branches.items():
            self.assertEqual(branch['inquiry_selector'],'v4')
            self.assertEqual(branch['retry_dispatch_selector'],variant)
            self.assertTrue(all(r['decision']['evidence']['policy']=='retry_dispatch_'+variant
                                for r in branch['inquiry_decisions']))

    def test_every_selector_has_one_full_public_context_and_reproduces(self):
        for variant,branch in self.scarce_branches.items():
            rows={r['event_sequence']:r for r in branch['inquiry_decisions']}
            records=branch['retry_dispatch_records']
            self.assertEqual([r['inquiry_event_sequence'] for r in records],list(rows))
            for record in records:
                row=rows[record['inquiry_event_sequence']]
                self.assertEqual(row['decision'],record['decision'])
                self.assertEqual(decide(variant,inputs=row['inputs'],context=record['context']),row['decision'])

    def test_actual_ask_events_and_contact_evidence_match_one_to_one(self):
        result=self.scarce_branches['frontier']
        asks=[e for e in result['events'] if e['type']=='ask']
        contacts=result['retry_dispatch_contacts']
        self.assertEqual([c['ask_event_sequence'] for c in contacts],[e['event_sequence'] for e in asks])
        for ask,contact in zip(asks,contacts):
            self.assertEqual((ask['patient'],ask['hospital'],ask['attempt'],ask['t']),
                (contact['patient'],contact['hospital'],contact['attempt'],contact['submitted_at']))

    def test_pacing_uses_actual_old_retry_deadline_instead_of_a_new_backoff(self):
        branch=self.scarce_branches['paced']
        self.assertGreater(branch['metrics']['retry_dispatch_wait_preservations'],0)
        events={e['event_sequence']:e for e in branch['events'] if 'event_sequence' in e}
        for wake in branch['retry_dispatch_wakes']:
            raw=events[wake['wake_event_sequence']]
            self.assertEqual(wake['previous_retry_at'],raw['previous_retry_at'])
            self.assertEqual(wake['report_event_sequence'],raw['report_event_sequence'])
            self.assertIn(raw['type'],('resource_event_review','report_driven_wake'))

    def test_fresh_identical_feed_reduces_repeated_single_option_requests(self):
        frontier=next(p for p in self.scarce_branches['frontier']['patient_outcomes'] if p['id']=='P02')
        paced=next(p for p in self.scarce_branches['paced']['patient_outcomes'] if p['id']=='P02')
        self.assertLess(paced['inquiries'],frontier['inquiries'])

    def test_every_cooperative_normal_request_still_has_real_work(self):
        branch=self.scarce_branches['frontier']
        asks=[e for e in branch['events'] if e['type']=='ask' and e.get('cooperative_job_id') is not None]
        self.assertEqual(len(branch['cooperative_job_ledger']),len(asks))
        self.assertTrue(all(j['service_minutes']==.25 for j in branch['cooperative_job_ledger']))
        self.assertEqual(branch['inquiry_queue_report']['aggregate']['workers'],12)

    def test_initial_same_tick_scarcity_supplement_is_preserved(self):
        branch=self.scarce_branches['frontier']
        self.assertTrue(any(r['result']['send']==['H02'] for r in branch['scarce_supplement_records']))

    def test_queue_disabled_mode_adds_no_ack_worker_or_cooperative_work(self):
        branch=run_retry_dispatch_branch(make_scenario(7,'out_of_order'))
        self.assertEqual(branch['retry_dispatch_foundation_spec']['mode'],'independent_replies')
        self.assertNotIn('capability_probe_spec',branch)
        self.assertNotIn('cooperative_job_ledger',branch)
        self.assertFalse(any(e['type'].startswith('cooperative_') for e in branch['events']))

    def test_no_extra_resource_publication_channel_is_added(self):
        branch=run_retry_dispatch_branch(make_scenario(7,'fresh_control'))
        self.assertFalse(branch['resource_updates_enabled'])
        self.assertFalse(any(e['type']=='resource_update_captured' for e in branch['events']))

    def test_original_clocks_ttl_and_fixed_foundation_specs_are_retained(self):
        case=make_scenario(7,'shortage_probe_later_patient_fifo')
        reference=old(case)
        actual=run_retry_dispatch_branch(case,branch_id='same')
        for key in ('deadline_recovery_spec','shortage_probe_spec','cooperative_probe_spec'):
            self.assertEqual(actual[key],reference[key],key)
        self.assertFalse(actual['scarce_delay_spec']['actual_selector_reused'])
        self.assertTrue(actual['scarce_delay_spec']['base_selector_reused_as_kernel'])
        self.assertEqual(actual['patient_outcomes'],reference['patient_outcomes'])

    def test_existing_late_reply_target_still_matches_saved_safe_timing(self):
        branch=run_retry_dispatch_branch(make_scenario(7,'shortage_probe_late_reply'))
        self.assertEqual(branch['patient_outcomes'][0]['treatment_start_t'],6.75)

    def test_context_contains_no_future_response_or_worker_fields(self):
        result=self.scarce_branches['frontier']
        for record in result['retry_dispatch_records']:
            self.assertEqual(set(record['context']),{'schema_version','evaluated_t','patient','episode',
                                                    'universe','contacts','retry_barriers'})
            self.assertNotIn('due_t',str(record['context']))
            self.assertNotIn('finish_t',str(record['context']))

    def test_scenario_mutation_and_branch_metadata_aliasing_are_prevented(self):
        case=make_scenario(8,'out_of_order')
        before=deepcopy(case)
        branch=run_retry_dispatch_branch(case)
        self.assertEqual(case,before)
        branch['retry_dispatch_spec']['variants'].append('bad')
        self.assertEqual(run_retry_dispatch_branch(case)['retry_dispatch_spec']['variants'],list(VARIANTS))

    def test_c1_uses_the_same_actual_dispatch_before_unchanged_allocation(self):
        result=run_retry_dispatch_branch(make_scenario(7,'fresh_control'),allocation='C1')
        reference=run_retry_dispatch_branch(make_scenario(7,'fresh_control'),allocation='H1')
        self.assertEqual(result['inquiry_decisions'],reference['inquiry_decisions'])
        self.assertEqual(result['patient_outcomes'],reference['patient_outcomes'])

    def test_standalone_replay_preserves_canonical_order(self):
        specs=[dict(id='one',allocation='H1',variant='frontier'),dict(id='two',allocation='H1',variant='paced')]
        case=make_scenario(7,'out_of_order')
        result=run_retry_dispatch_replay(case,specs,['two','one'])
        self.assertEqual([b['id'] for b in result['branches']],['one','two'])
        self.assertEqual(result['execution_order'],['two','one'])
        self.assertEqual(len(DEFAULT_RETRY_DISPATCH_POLICIES),6)

    def test_bad_variants_allocations_or_execution_specs_are_rejected(self):
        case=make_scenario(7,'out_of_order')
        for arguments in (dict(variant='best'),dict(allocation='R1')):
            with self.assertRaises(ValueError):run_retry_dispatch_branch(case,**arguments)
        specs=[dict(id='one',allocation='H1',variant='frontier')]
        with self.assertRaises(ValueError):run_retry_dispatch_replay(case,specs,['one','one'])
        specs[0]['workers']=2
        with self.assertRaises(ValueError):run_retry_dispatch_replay(case,specs)

    def test_namespace_is_present_on_both_foundation_modes(self):
        for branch in (self.scarce_branches['frontier'],run_retry_dispatch_branch(make_scenario(7,'out_of_order'))):
            self.assertTrue(NAMESPACE_FIELDS <= set(branch))


if __name__=='__main__':unittest.main()
