"""Only structural seeds 7/8; actual work and eligibility record integrity."""
from collections import Counter
from copy import deepcopy
import unittest
from unittest.mock import patch

from simulation.retry_dispatch_scenarios import make_scenario
from simulation.retry_dispatch_engine import run_retry_dispatch_branch
from simulation.recovery_dispatch_engine import (run_recovery_dispatch_branch,NAMESPACE_FIELDS,
    RecoveryDispatchMixin,CooperativeRetryDispatchReplay,IndependentRetryDispatchReplay)


class RecoveryDispatchEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case=make_scenario(7,'retry_dispatch_decline_then_recovery')
        cls.branches={v:run_recovery_dispatch_branch(cls.case,variant=v) for v in ('eager','latched')}
        cls.reference=run_retry_dispatch_branch(cls.case)

    def test_eager_uses_real_received_recovery_and_original_normal_lane(self):
        b=self.branches['eager']
        self.assertEqual(b['patient_outcomes'][0]['treatment_start_t'],7.5)
        self.assertEqual(b['metrics']['recovery_reopen_sent'],1)
        action=b['recovery_dispatch_reopens'][0]
        events={e['event_sequence']:e for e in b['ordered_emissions']}
        self.assertEqual(events[action['actual_target_ask_event_sequence']]['t'],4)
        self.assertEqual(events[action['actual_target_ask_event_sequence']]['attempt'],2)

    def test_latched_waits_for_original_pending_completion_and_uses_saved_recovery(self):
        b=self.branches['latched']
        self.assertEqual(b['patient_outcomes'][0]['treatment_start_t'],8.25)
        action=b['recovery_dispatch_reopens'][0]
        events={e['event_sequence']:e for e in b['ordered_emissions']}
        self.assertEqual(events[action['actual_target_ask_event_sequence']]['t'],4.75)
        review=next(r for r in b['recovery_dispatch_reviews'] if r['event_sequence']==action['review_event_sequence'])
        self.assertEqual(review['snapshot']['pending_inquiries'],[])
        self.assertEqual(review['decision']['transition_report_event_sequence'],
            next(r['report_event_sequence'] for r in review['snapshot']['transitions']
                 if r['hospital']=='H01' and r['t']==4))

    def test_original_episode_and_actual_contact_budget_are_not_renewed(self):
        for b in self.branches.values():
            for action in b['recovery_dispatch_reopens']:
                self.assertEqual(action['original_episode_id'],action['after_episode_id'])
                self.assertEqual(action['after_episode_contacts'],action['original_episode_contacts']+
                    len(action['actual_new_ask_event_sequences']))
                self.assertLessEqual(action['after_episode_contacts'],10)

    def test_every_consumed_token_has_exact_actual_new_ask_and_reopen_refs(self):
        for b in self.branches.values():
            events={e['event_sequence']:e for e in b['ordered_emissions']}
            for c in b['recovery_dispatch_consumptions']:
                self.assertEqual(events[c['ask_event_sequence']]['type'],'ask')
                self.assertEqual(events[c['reopen_event_sequence']]['type'],'recovery_reopen')
                self.assertEqual(events[c['review_event_sequence']]['type'],'recovery_reopen_review')
                self.assertEqual(events[c['ask_event_sequence']]['hospital'],c['hospital'])

    def test_real_clinical_decline_source_and_actual_previous_ask_match(self):
        b=self.branches['eager'];events={e['event_sequence']:e for e in b['ordered_emissions']}
        for decline in b['recovery_dispatch_declines']:
            self.assertEqual(events[decline['decline_event_sequence']]['type'],'decline')
            self.assertEqual(events[decline['ask_event_sequence']]['type'],'ask')
            self.assertEqual(events[decline['ask_event_sequence']]['attempt'],decline['attempt'])

    def test_parent_frontier_rows_and_original_seven_inputs_are_unchanged_in_shape(self):
        for b in self.branches.values():
            for row in b['inquiry_decisions']:
                self.assertEqual(len(row),9);self.assertEqual(len(row['inputs']),7)
                self.assertEqual(row['decision']['evidence']['policy'],'retry_dispatch_frontier')

    def test_each_recovery_report_is_existing_applied_data_with_full_public_views(self):
        b=self.branches['eager'];events={e['event_sequence']:e for e in b['ordered_emissions']}
        for change in b['recovery_dispatch_resource_transitions']:
            packet=events[change['report_event_sequence']]
            self.assertEqual(packet['type'],'resource_report_received')
            self.assertTrue(packet['applied'])
            self.assertEqual(packet['hospital'],change['hospital'])
            self.assertTrue(any(change['before_resources'][r][k]!=change['after_resources'][r][k]
                for r in change['before_resources'] for k in ('capacity','used','status')))

    def test_timestamp_only_refresh_does_not_generate_extra_recovery_transitions(self):
        b=self.branches['eager']
        self.assertLess(len(b['recovery_dispatch_resource_transitions']),len(b['observation_records']))

    def test_no_extra_information_checks_workers_or_resource_publications(self):
        for b in self.branches.values():
            self.assertFalse(b['resource_updates_enabled'])
            self.assertFalse(any(e['type']=='information_check' and e['purpose'] not in
                ('inquiry','allocation','shortage_probe') for e in b['ordered_emissions']))
            self.assertEqual(b['inquiry_queue_report']['aggregate']['workers'],4)

    def test_actual_new_query_keeps_the_original_three_minute_deadline(self):
        for b in self.branches.values():
            c=b['recovery_dispatch_consumptions'][0]
            ask=next(e for e in b['ordered_emissions'] if e['event_sequence']==c['ask_event_sequence'])
            self.assertEqual(ask['timeout_at']-ask['submitted_at'],3)
            self.assertEqual(len(b['cooperative_job_ledger']),5)

    def test_no_actual_clinical_decline_keeps_original_frontier_outcomes(self):
        case=make_scenario(7,'shortage_probe_later_patient_fifo')
        for v in ('eager','latched'):
            b=run_recovery_dispatch_branch(case,variant=v)
            self.assertEqual(b['patient_outcomes'],run_retry_dispatch_branch(case)['patient_outcomes'])
            self.assertEqual(b['recovery_dispatch_consumptions'],[])

    def test_no_token_after_tick_never_advances_empty_tried_episode(self):
        case=make_scenario(7,'watch_outage_recovery')
        snapshots={}
        base_class=CooperativeRetryDispatchReplay if case.get('inquiry_queue',{}).get('enabled',False) \
            else IndependentRetryDispatchReplay
        original_base=base_class.after_tick
        original_recovery=RecoveryDispatchMixin.after_tick
        def state(replay):
            p=replay.patients['P03']
            return dict(tried=sorted(p.tried),retry_at=p.retry_at,
                episode=replay._episode_ids.get(p.id,0),contacts=replay._episode_contacts.get(p.id,0),
                last_contact=replay._last_contact_t.get(p.id),recovery_reviews=len(replay.recovery_reviews))
        def base_tick(replay,t):
            original_base(replay,t)
            if t==1:snapshots['base']=state(replay)
        def recovery_tick(replay,t):
            original_recovery(replay,t)
            if t==1:snapshots['recovery']=state(replay)
        with patch.object(base_class,'after_tick',new=base_tick), \
                patch.object(RecoveryDispatchMixin,'after_tick',new=recovery_tick):
            branch=run_recovery_dispatch_branch(case,variant='latched')
        self.assertEqual(snapshots['base']['tried'],[])
        self.assertEqual(snapshots['base']['retry_at'],4)
        self.assertEqual(snapshots['base']['episode'],0)
        self.assertEqual(snapshots['base'],snapshots['recovery'])
        self.assertEqual(branch['recovery_dispatch_consumptions'],[])

    def test_known_maturity_risk_is_retained_for_eager_and_avoided_by_latched(self):
        case=deepcopy(self.case)
        case['response_matrix']['*']['H01']=dict(attempts=[dict(delay=.5,silent=False),dict(delay=2.75,silent=False)])
        case['response_matrix']['*']['H04']=dict(delay=2.5,silent=False)
        reference=run_retry_dispatch_branch(case)
        eager=run_recovery_dispatch_branch(case,variant='eager')
        latched=run_recovery_dispatch_branch(case,variant='latched')
        self.assertGreater(eager['patient_outcomes'][0]['treatment_start_t'],
                           reference['patient_outcomes'][0]['treatment_start_t'])
        self.assertEqual(latched['patient_outcomes'],reference['patient_outcomes'])

    def test_independent_mode_adds_no_hospital_control_channel(self):
        case=make_scenario(7,'out_of_order')
        b=run_recovery_dispatch_branch(case)
        self.assertFalse(any(e['type'].startswith('cooperative_') for e in b['ordered_emissions']))
        self.assertEqual(b['recovery_dispatch_foundation_spec']['base_dispatch_foundation_spec']['mode'],
                         'independent_replies')

    def test_actual_subclass_is_declared_without_false_original_delegation_metadata(self):
        b=self.branches['latched']
        self.assertTrue(NAMESPACE_FIELDS<=set(b))
        self.assertNotIn('retry_dispatch_foundation_spec',b)
        self.assertNotIn('capability_probe_spec',b)
        self.assertIn('CooperativeRecoveryDispatchReplay',str(b['recovery_dispatch_foundation_spec']))

    def test_c1_keeps_the_same_actual_recovery_lane(self):
        b=run_recovery_dispatch_branch(make_scenario(8,'retry_dispatch_decline_then_recovery'),allocation='C1')
        self.assertEqual(b['patient_outcomes'][0]['treatment_start_t'],8.25)
        self.assertEqual(b['policy']['rule'],'C1')

    def test_scenario_and_module_metadata_are_not_mutated(self):
        case=make_scenario(8,'out_of_order');before=deepcopy(case)
        b=run_recovery_dispatch_branch(case)
        self.assertEqual(case,before)
        b['recovery_dispatch_spec']['variants'].append('invalid')
        self.assertEqual(run_recovery_dispatch_branch(case)['recovery_dispatch_spec']['variants'],['eager','latched'])

    def test_unknown_mode_and_allocation_are_rejected(self):
        for changes in (dict(variant='auto'),dict(allocation='R1')):
            with self.assertRaises(ValueError):run_recovery_dispatch_branch(self.case,**changes)


if __name__=='__main__':unittest.main()
