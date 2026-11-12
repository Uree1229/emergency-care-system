"""Small 7/8 structural checks; the two fixed foundations stay distinguishable."""
from copy import deepcopy
import unittest

from simulation.recovery_dispatch_scenarios import make_scenario
from simulation.retry_dispatch_engine import run_retry_dispatch_branch
from simulation.recovery_dispatch_engine import run_recovery_dispatch_branch
from simulation.recovery_rank_engine import run_recovery_rank_branch,NAMESPACE_FIELDS


class RecoveryRankEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case=make_scenario(7,'recovery_dispatch_recovery_then_decline')
        cls.branches={variant:run_recovery_rank_branch(cls.case,variant=variant) for variant in ('normal','latched')}
        cls.frontier=run_retry_dispatch_branch(cls.case)
        cls.old_latched=run_recovery_dispatch_branch(cls.case,variant='latched')

    def test_normal_keeps_frontier_outcomes_when_it_already_chooses_the_recovery(self):
        branch=self.branches['normal']
        self.assertEqual(branch['patient_outcomes'],self.frontier['patient_outcomes'])
        self.assertEqual(branch['recovery_rank_consumptions'],[])
        self.assertNotIn('recovery_dispatch_reopens',branch)

    def test_latched_rank_uses_the_actual_eight_minute_recovery_in_normal_lane(self):
        branch=self.branches['latched'];events={e['event_sequence']:e for e in branch['events'] if 'event_sequence' in e}
        self.assertEqual(len(branch['recovery_rank_consumptions']),1)
        used=branch['recovery_rank_consumptions'][0]
        ask=events[used['ask_event_sequence']]
        self.assertEqual((ask['t'],ask['hospital'],ask['attempt']),(8,'H01',3))
        self.assertEqual(events[used['decline_event_sequence']]['t'],7.25)
        self.assertEqual(events[used['report_event_sequence']]['observed_at'],8)
        self.assertLess(branch['patient_outcomes'][0]['treatment_start_t'],self.old_latched['patient_outcomes'][0]['treatment_start_t'])

    def test_initial_reopen_token_and_latest_decline_rank_token_are_separate(self):
        branch=self.branches['latched']
        first=branch['recovery_dispatch_consumptions'][0];rank=branch['recovery_rank_consumptions'][0]
        self.assertNotEqual(first['token_id'],rank['token_id'])
        self.assertNotEqual(first['ask_event_sequence'],rank['ask_event_sequence'])
        self.assertTrue(rank['token_id'].startswith('rank:'))

    def test_every_rank_ask_is_in_the_original_seven_input_offered_pool(self):
        for branch in self.branches.values():
            rows={r['event_sequence']:r for r in branch['inquiry_decisions']}
            events={e['event_sequence']:e for e in branch['events'] if 'event_sequence' in e}
            for used in branch['recovery_rank_consumptions']:
                row=rows[used['inquiry_event_sequence']];ask=events[used['ask_event_sequence']]
                self.assertIn(used['hospital'],[r['hospital_id'] for r in row['inputs']['candidates']])
                self.assertEqual(row['t'],ask['t'])
                self.assertEqual(len(row['inputs']),7)
                self.assertEqual(row['inputs']['patient_pending'],[])

    def test_original_three_minute_query_deadline_and_episode_budget_are_retained(self):
        branch=self.branches['latched']
        for event in branch['events']:
            if event['type']=='ask':self.assertEqual(event['timeout_at']-event['submitted_at'],3)
        for record in branch['retry_dispatch_records']:
            self.assertLessEqual(record['context']['episode']['contacts'],10)
        for record in branch['recovery_dispatch_reopens']:
            self.assertEqual(record['original_episode_id'],record['after_episode_id'])

    def test_actual_rank_consumption_uses_a_literal_original_inquiry_decision(self):
        branch=self.branches['latched'];used=branch['recovery_rank_consumptions'][0]
        record=next(r for r in branch['recovery_rank_records'] if r['inquiry_event_sequence']==used['inquiry_event_sequence'])
        actual=next(r for r in branch['inquiry_decisions'] if r['event_sequence']==used['inquiry_event_sequence'])
        self.assertEqual(record['decision'],actual['decision'])
        detail=record['decision']['evidence']['recovery_rank']
        self.assertEqual(detail['base_frontier_decision']['reason'],'frontier_not_offered')
        self.assertEqual(detail['rank_send'],['H01'])

    def test_every_original_review_keeps_a_factual_retry_context_and_new_rank_record(self):
        for branch in self.branches.values():
            self.assertEqual(len(branch['inquiry_decisions']),len(branch['retry_dispatch_records']))
            self.assertEqual(len(branch['inquiry_decisions']),len(branch['recovery_rank_records']))
            for original,rank in zip(branch['retry_dispatch_records'],branch['recovery_rank_records'],strict=True):
                self.assertEqual(original['inquiry_event_sequence'],rank['inquiry_event_sequence'])
                self.assertEqual(original['decision'],rank['decision'])

    def test_actual_contact_history_is_not_changed_into_discounted_counts(self):
        branch=self.branches['latched'];used=branch['recovery_rank_consumptions'][0]
        row=next(r for r in branch['retry_dispatch_records'] if r['inquiry_event_sequence']==used['inquiry_event_sequence'])
        counts=row['decision']['evidence']['recovery_rank']['base_frontier_decision']['evidence']['retry_dispatch']['contact_counts']
        self.assertIn(dict(hospital='H01',contacts=2),counts)
        self.assertIn(dict(hospital='H02',contacts=1),counts)

    def test_metadata_declares_actual_subclasses_and_nests_inherited_selector_contract(self):
        for variant,branch in self.branches.items():
            self.assertTrue(NAMESPACE_FIELDS <= set(branch))
            self.assertNotIn('retry_dispatch_selector',branch)
            self.assertNotIn('retry_dispatch_spec',branch)
            self.assertNotIn('retry_dispatch_foundation_spec',branch)
            self.assertEqual(branch['recovery_rank_selector'],'recovery_rank_'+variant)
            self.assertEqual(branch['recovery_rank_base_contract']['retry_dispatch_selector'],'frontier')
            self.assertTrue(branch['recovery_rank_foundation_spec']['actual_selector_changed'])
        self.assertNotIn('recovery_dispatch_foundation_spec',self.branches['latched'])
        self.assertIn('recovery_dispatch_foundation_spec',self.branches['latched']['recovery_rank_base_contract'])

    def test_no_additional_workers_information_checks_or_resource_reports(self):
        for branch in self.branches.values():
            self.assertFalse(branch['resource_updates_enabled'])
            self.assertEqual(branch['inquiry_queue_report']['aggregate']['workers'],4)
            self.assertFalse(any(e['type']=='information_check' and e['purpose'] not in
                ('inquiry','allocation','shortage_probe') for e in branch['events']))

    def test_normal_adds_no_recovery_scan_or_reopen_to_no_token_environment(self):
        case=make_scenario(7,'watch_outage_recovery')
        actual=run_recovery_rank_branch(case,variant='normal')
        reference=run_retry_dispatch_branch(case)
        self.assertEqual(actual['events'],reference['events'])
        self.assertEqual(actual['patient_outcomes'],reference['patient_outcomes'])

    def test_c1_uses_the_same_actual_normal_rank_lane(self):
        branch=run_recovery_rank_branch(make_scenario(8,'recovery_dispatch_recovery_then_decline'),allocation='C1')
        self.assertEqual(branch['policy']['rule'],'C1')
        self.assertEqual(len(branch['recovery_rank_consumptions']),1)

    def test_independent_foundation_adds_no_control_queue(self):
        branch=run_recovery_rank_branch(make_scenario(8,'out_of_order'),variant='normal')
        self.assertFalse(any(e['type'].startswith('cooperative_') for e in branch['events']))
        self.assertEqual(branch['recovery_rank_foundation_spec']['mode'],'independent_replies')

    def test_scenario_and_nested_module_metadata_are_not_mutated(self):
        case=make_scenario(8,'out_of_order');before=deepcopy(case)
        branch=run_recovery_rank_branch(case,variant='normal')
        self.assertEqual(case,before)
        branch['recovery_rank_spec']['variants'].append('invalid')
        self.assertEqual(run_recovery_rank_branch(case,variant='normal')['recovery_rank_spec']['variants'],['normal','latched'])

    def test_unknown_allocation_or_variant_is_rejected(self):
        for change in (dict(variant='eager'),dict(allocation='R1')):
            with self.assertRaises(ValueError):run_recovery_rank_branch(self.case,**change)


if __name__=='__main__':unittest.main()
