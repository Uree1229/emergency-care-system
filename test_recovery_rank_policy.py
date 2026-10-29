"""Pure recovery rank eligibility, actual evidence, and unchanged input clocks."""
from copy import deepcopy
import unittest

from server.inquiry_v4 import decide as kernel_decide
from simulation.retry_dispatch_policy import condition_fingerprint
from simulation.recovery_rank_policy import decide_recovery_rank,VARIANTS,RECOVERY_RANK_SPEC
from test_retry_dispatch_policy import example,view


def fixture(variant='normal'):
    inputs,context=example(t=8,offered=('A',),contacts=('A','B','A'))
    fp=context['patient']['condition_fingerprint']
    for row in context['universe']:
        row['resources']=view(at=8,observed=8,received=8,sequence=40)
    rank=dict(schema_version=1,evaluated_t=8,variant=variant,valid_offers=[],consumptions=[],
        declines=[dict(patient='P',hospital='A',t=7.25,decline_event_sequence=20,ask_event_sequence=2,
            attempt=2,condition_fingerprint=fp,resources=view(used=1,at=7.25,observed=7,received=7,sequence=30))],
        transitions=[dict(hospital='A',t=8,report_event_sequence=40,before_report_event_sequences={'ed_bed':30},
            before_resources=view(used=1,at=8,observed=7,received=7,sequence=30),
            after_resources=view(at=8,observed=8,received=8,sequence=40))])
    return inputs,context,rank


def result(rows):
    return decide_recovery_rank(inputs=rows[0],context=rows[1],rank_context=rows[2])


class RecoveryRankPolicyTests(unittest.TestCase):
    def test_two_fixed_attribution_variants_share_the_same_rank_rule(self):
        self.assertEqual(VARIANTS,('normal','latched'))
        self.assertEqual(result(fixture('normal'))['send'],['A'])
        self.assertEqual(result(fixture('latched'))['send'],['A'])

    def test_actual_recovery_promotes_only_an_original_offered_candidate(self):
        output=result(fixture());detail=output['evidence']['recovery_rank']
        self.assertEqual(detail['base_frontier_decision']['send'],[])
        self.assertEqual(detail['base_frontier_decision']['reason'],'frontier_not_offered')
        self.assertEqual(output['send'],['A'])
        self.assertEqual(detail['recovery_tier'],['A'])
        self.assertEqual(detail['selected_recovery_proofs']['A']['decline_event_sequence'],20)

    def test_actual_contact_counts_are_not_discounted_or_reset(self):
        rows=fixture();before=deepcopy(rows)
        output=result(rows)
        self.assertEqual(rows,before)
        self.assertEqual(output['evidence']['recovery_rank']['base_frontier_decision']['evidence']
            ['retry_dispatch']['contact_counts'],[dict(hospital='A',contacts=2),dict(hospital='B',contacts=1)])

    def test_original_kernel_result_and_next_review_clock_are_literal(self):
        rows=fixture();output=result(rows);detail=output['evidence']['recovery_rank']
        self.assertEqual(detail['kernel_decision'],kernel_decide(**rows[0]))
        self.assertEqual(output['next_review_at'],detail['base_frontier_decision']['next_review_at'])

    def test_no_actual_clinical_decline_does_not_use_reply_ack_or_timeout(self):
        rows=fixture();rows[2]['declines']=[]
        self.assertEqual(result(rows)['send'],[])

    def test_latest_contact_must_be_the_declined_attempt(self):
        rows=fixture();new=deepcopy(rows[1]['contacts'][-1]);new.update(attempt=3,ask_event_sequence=21)
        rows[1]['contacts'].append(new)
        self.assertEqual(result(rows)['send'],[])

    def test_decline_attempt_mismatch_is_not_the_latest_actual_contact(self):
        rows=fixture();rows[2]['declines'][0]['attempt']=1
        self.assertEqual(result(rows)['send'],[])

    def test_changed_patient_condition_cannot_reuse_old_decline(self):
        rows=fixture();rows[1]['patient']['condition']['priority']=3
        rows[1]['patient']['condition_fingerprint']=condition_fingerprint(rows[1]['patient']['condition'])
        detail=result(rows)['evidence']['recovery_rank']
        self.assertEqual(detail['rank_send'],[])
        self.assertEqual(detail['candidate_checks'][0]['reason'],'CONDITION_CHANGED')

    def test_current_fresh_sufficient_is_required(self):
        for values in (dict(status='stale'),dict(used=1,free=0)):
            rows=fixture();rows[1]['universe'][0]['resources']['ed_bed'].update(values)
            self.assertEqual(result(rows)['send'],[])

    def test_old_optimistic_report_source_cannot_override_a_new_decline(self):
        rows=fixture();rows[2]['transitions'][0]['after_resources']['ed_bed'].update(observed_at=7,age_minutes=1)
        self.assertEqual(result(rows)['send'],[])

    def test_timestamp_refresh_alone_is_not_quantity_recovery(self):
        rows=fixture();rows[2]['transitions'][0]['before_resources']=view(at=8,observed=7,received=7,sequence=30)
        self.assertEqual(result(rows)['send'],[])

    def test_quality_recovery_without_fresh_numeric_shortage_is_insufficient(self):
        rows=fixture();rows[2]['transitions'][0]['before_resources']['ed_bed']['status']='stale'
        self.assertEqual(result(rows)['send'],[])

    def test_recovery_before_latest_decline_is_not_new_evidence(self):
        rows=fixture();decline=rows[2]['declines'][0];decline.update(t=8,decline_event_sequence=41)
        decline['resources']=view(used=1,at=8)
        self.assertEqual(result(rows)['send'],[])

    def test_later_actual_loss_invalidates_an_old_recovery(self):
        rows=fixture();loss=deepcopy(rows[2]['transitions'][0]);loss['report_event_sequence']=41
        loss['after_resources']=view(used=1,at=8,observed=8,received=8,sequence=41)
        rows[2]['transitions'].append(loss)
        self.assertEqual(result(rows)['send'],[])

    def test_partial_or_mixed_packet_recovery_is_not_sufficient(self):
        rows=fixture();rows[1]['patient']['condition']['demand']['team']=1
        fp=condition_fingerprint(rows[1]['patient']['condition']);rows[1]['patient']['condition_fingerprint']=fp
        for row in rows[1]['contacts']+rows[2]['declines']:
            row['condition_fingerprint']=fp
            row['resources']['team']=deepcopy(row['resources']['ed_bed'])
        for row in rows[1]['universe']:row['resources']['team']=deepcopy(row['resources']['ed_bed'])
        change=rows[2]['transitions'][0]
        for name in ('before_resources','after_resources'):change[name]['team']=deepcopy(change[name]['ed_bed'])
        change['before_report_event_sequences']['team']=30
        change['after_resources']['team']['sequence']=39
        self.assertEqual(result(rows)['send'],[])

    def test_unvisited_eligible_hospital_keeps_its_original_exploration_chance(self):
        rows=fixture();other=deepcopy(rows[1]['universe'][1]);other['hospital']='C'
        rows[1]['universe'].append(other)
        self.assertEqual(result(rows)['send'],[])

    def test_target_already_in_frontier_does_not_consume_an_exception(self):
        rows=fixture();rows[1]['contacts']=rows[1]['contacts'][:2]
        rows[2]['declines'][0].update(ask_event_sequence=0,attempt=1)
        output=result(rows);detail=output['evidence']['recovery_rank']
        self.assertEqual(output['send'],['A']);self.assertEqual(detail['rank_send'],[])
        self.assertEqual(detail['candidate_checks'][0]['reason'],'ALREADY_IN_BASE_FRONTIER')

    def test_candidate_outside_original_offered_pool_cannot_be_added(self):
        rows=fixture();rows[0]['candidates']=[]
        self.assertEqual(result(rows)['send'],[])

    def test_own_pending_blocks_rank_exception(self):
        rows=fixture();pending=dict(hospital='B',patient='P',submitted_at=86850)
        rows[0]['pending']=[pending];rows[0]['patient_pending']=[dict(pending,travel_seconds=120)]
        output=result(rows)
        self.assertEqual(output['evidence']['recovery_rank']['reason'],'OWN_PENDING')

    def test_any_current_valid_offer_blocks_rank_exception(self):
        rows=fixture();rows[2]['valid_offers']=[dict(hospital='B',expires_at=9)]
        self.assertEqual(result(rows)['evidence']['recovery_rank']['reason'],'VALID_OFFER')

    def test_actual_acceptance_or_commit_ends_the_rank_scope(self):
        for change in (dict(first_accept_event_sequence=25),dict(first_commit_t=7),dict(state='TRANSIT')):
            rows=fixture();rows[1]['patient'].update(change)
            self.assertEqual(result(rows)['evidence']['recovery_rank']['reason'],'NOT_INITIAL_UNACCEPTED_SEARCH')

    def test_original_contact_caps_remain_binding(self):
        for field in ('max_new','episode'):
            rows=fixture()
            if field=='max_new':rows[0]['max_new']=0
            else:rows[1]['episode']['contacts']=10
            self.assertEqual(result(rows)['evidence']['recovery_rank']['reason'],'CONTACT_OR_OPEN_LIMIT')

    def test_only_actual_rank_ask_consumption_blocks_the_next_rank(self):
        rows=fixture();proof=result(rows)['evidence']['recovery_rank']['selected_recovery_proofs']['A']
        rows[2]['consumptions']=[dict(patient='P',hospital='A',token_id=proof['token_id'],
            condition_fingerprint=rows[1]['patient']['condition_fingerprint'],ask_event_sequence=45,
            inquiry_event_sequence=44,decline_event_sequence=20,report_event_sequence=40)]
        self.assertEqual(result(rows)['evidence']['recovery_rank']['reason'],'RANK_ALREADY_CONSUMED')

    def test_unknown_hidden_future_or_malformed_context_is_rejected(self):
        for mode in ('hidden','future','variant','arithmetic'):
            rows=fixture()
            if mode=='hidden':rows[2]['future_work_finish']=9
            elif mode=='future':rows[2]['transitions'][0]['t']=9
            elif mode=='variant':rows[2]['variant']='adaptive'
            else:rows[1]['universe'][0]['resources']['ed_bed']['free']=99
            with self.assertRaises(ValueError):result(rows)

    def test_one_rank_budget_is_separate_from_initial_reopening(self):
        self.assertTrue(RECOVERY_RANK_SPEC['rank_consumption_separate_from_initial_reopen_consumption'])
        self.assertEqual(RECOVERY_RANK_SPEC['maximum_actual_rank_asks_per_patient_condition'],1)


if __name__=='__main__':unittest.main()
