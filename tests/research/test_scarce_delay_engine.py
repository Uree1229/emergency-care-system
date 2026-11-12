"""Current-information initial supplementation and unchanged physical motion."""
from copy import deepcopy
from collections import Counter
import unittest
from unittest.mock import patch

from simulation.joint_policy_engine import run_joint_policy_branch
from simulation.joint_policy_scenarios import make_scenario
from simulation.scarce_delay_scenarios import make_scenario as make_scarce_scenario
from simulation.outage_recovery_audit import audit_motion
from simulation.scarce_delay_engine import (
    DEFAULT_SCARCE_DELAY_POLICIES, ScarceDelayReplay, decide_scarce_candidates,
    decide_scarce_supplement, run_scarce_delay_branch, run_scarce_delay_replay,
)


def fresh(free=1):
    return dict(status='fresh', free=free, observed_at=0, received_at=0)


def snapshot():
    patient = dict(id='FLEX', priority=4, request_t=0, demand={'ed_bed': 1},
        required_capabilities=['BASIC'], state='SEARCH', request_event_sequence=2,
        compatible_hospitals=['H1', 'H2'], inquiry_count=1,
        pending_inquiries=[dict(hospital='H1', attempt=1, submitted_at=0,
                               timeout_at=3, ask_event_sequence=5)])
    waiter = dict(id='LIMITED', priority=4, request_t=0,
        demand={'ed_bed': 1, 'special_team': 1}, required_capabilities=['BASIC', 'SCARCE'],
        state='SEARCH', request_event_sequence=3, compatible_hospitals=['H1'],
        inquiry_count=0, pending_inquiries=[],
        resource_views={'H1': {'ed_bed': fresh(), 'special_team': fresh()}})
    own = dict(deepcopy(patient), resource_views={})
    return dict(t=0, context='initial_supplement', patient=patient,
        candidates=[dict(hospital='H1', travel_minutes=3, resources={'ed_bed': fresh()}),
                    dict(hospital='H2', travel_minutes=5, resources={'ed_bed': fresh()})],
        registered_search_patients=[own, waiter])


def inputs():
    return dict(opportunity_snapshot=snapshot(), original_ask_event_sequence=5,
        original_selector_event_sequence=4, eligible_new_hospitals=['H2'],
        max_new=2, episode_contacts=1, open_inquiries=1)


def event_rows(branch, kind):
    return [row for row in branch['events'] if row['type'] == kind]


def target(seed=7):
    return make_scenario(seed, 'scarce_options_staggered_replies')


class ScarceCandidateDecisionTests(unittest.TestCase):
    def test_registered_equal_priority_one_hospital_claim_has_fresh_alternative(self):
        result = decide_scarce_candidates(snapshot())
        self.assertEqual(result['offered_after'], ['H2'])
        self.assertEqual(result['blocked'][0]['waiting_demand'], 1)
        self.assertEqual(result['blocked'][0]['waiters'][0]['basis'], 'new_registration')

    def test_inputs_remain_independent(self):
        value = snapshot(); before = deepcopy(value)
        result = decide_scarce_candidates(value)
        result['blocked'][0]['waiters'][0]['patient'] = 'OTHER'
        self.assertEqual(value, before)

    def test_higher_priority_flexible_patient_is_not_restricted(self):
        value = snapshot(); value['registered_search_patients'][1]['priority'] = 2
        self.assertEqual(decide_scarce_candidates(value)['reason'], 'UNCHANGED')

    def test_flexible_patient_without_structural_alternative_is_not_restricted(self):
        value = snapshot(); value['patient']['compatible_hospitals'] = ['H1']
        value['candidates'] = value['candidates'][:1]
        self.assertEqual(decide_scarce_candidates(value)['reason'], 'UNCHANGED')

    def test_enough_beds_for_both_never_creates_a_claim(self):
        value = snapshot(); value['candidates'][0]['resources']['ed_bed']['free'] = 2
        value['registered_search_patients'][1]['resource_views']['H1']['ed_bed']['free'] = 2
        self.assertEqual(decide_scarce_candidates(value)['reason'], 'UNCHANGED')

    def test_multiple_current_claims_are_counted_together(self):
        value = snapshot(); value['candidates'][0]['resources']['ed_bed']['free'] = 2
        waiter = value['registered_search_patients'][1]
        waiter['resource_views']['H1']['ed_bed']['free'] = 2
        second = deepcopy(waiter); second['id'] = 'LIMITED2'; second['request_event_sequence'] = 7
        value['registered_search_patients'].append(second)
        self.assertEqual(decide_scarce_candidates(value)['blocked'][0]['waiting_demand'], 2)

    def test_unknown_required_resource_does_not_support_scarce_claim(self):
        value = snapshot()
        value['registered_search_patients'][1]['resource_views']['H1']['special_team'] = dict(status='unknown', free=None)
        self.assertEqual(decide_scarce_candidates(value)['reason'], 'UNCHANGED')

    def test_stale_required_resource_does_not_support_scarce_claim(self):
        value = snapshot()
        value['registered_search_patients'][1]['resource_views']['H1']['special_team']['status'] = 'stale'
        self.assertEqual(decide_scarce_candidates(value)['reason'], 'UNCHANGED')

    def test_known_required_shortage_does_not_support_scarce_claim(self):
        value = snapshot()
        value['registered_search_patients'][1]['resource_views']['H1']['special_team']['free'] = 0
        self.assertEqual(decide_scarce_candidates(value)['reason'], 'UNCHANGED')

    def test_unverified_alternative_keeps_original_candidates(self):
        value = snapshot(); value['candidates'][1]['resources']['ed_bed'] = dict(status='unknown', free=None)
        result = decide_scarce_candidates(value)
        self.assertEqual(result['reason'], 'NO_FRESH_ALTERNATIVE')
        self.assertEqual(result['offered_after'], ['H1', 'H2'])

    def test_pending_claim_expires_at_original_one_minute(self):
        value = snapshot(); value['t'] = .75
        waiter = value['registered_search_patients'][1]
        waiter['inquiry_count'] = 1
        waiter['pending_inquiries'] = [dict(hospital='H1', attempt=1, submitted_at=0,
                                           timeout_at=3, ask_event_sequence=8)]
        self.assertEqual(decide_scarce_candidates(value)['blocked'][0]['waiters'][0]['deadline'], 1)
        value['t'] = 1
        self.assertEqual(decide_scarce_candidates(value)['reason'], 'UNCHANGED')

    def test_original_timeout_can_end_claim_earlier(self):
        value = snapshot(); value['t'] = .75
        waiter = value['registered_search_patients'][1]; waiter['inquiry_count'] = 1
        waiter['pending_inquiries'] = [dict(hospital='H1', attempt=1, submitted_at=0,
                                           timeout_at=.75, ask_event_sequence=8)]
        self.assertEqual(decide_scarce_candidates(value)['reason'], 'UNCHANGED')

    def test_failed_first_request_does_not_recreate_new_registration(self):
        value = snapshot(); value['registered_search_patients'][1]['inquiry_count'] = 1
        self.assertEqual(decide_scarce_candidates(value)['reason'], 'UNCHANGED')

    def test_future_registration_is_rejected(self):
        value = snapshot(); value['registered_search_patients'][1]['request_t'] = .25
        with self.assertRaises(ValueError): decide_scarce_candidates(value)

    def test_nonsearch_patient_is_rejected_from_current_cohort(self):
        value = snapshot(); value['registered_search_patients'][1]['state'] = 'TRANSIT'
        with self.assertRaises(ValueError): decide_scarce_candidates(value)

    def test_hidden_pending_calendar_is_rejected(self):
        value = snapshot(); value['patient']['pending_inquiries'][0]['at'] = .25
        with self.assertRaises(ValueError): decide_scarce_candidates(value)

    def test_hidden_candidate_calendar_is_rejected(self):
        value = snapshot(); value['candidates'][0]['silent'] = False
        with self.assertRaises(ValueError): decide_scarce_candidates(value)

    def test_nonregistered_inquiry_patient_is_rejected(self):
        value = snapshot(); value['registered_search_patients'] = value['registered_search_patients'][1:]
        with self.assertRaises(ValueError): decide_scarce_candidates(value)


class ScarceSupplementDecisionTests(unittest.TestCase):
    def test_original_handle_is_kept_and_one_actual_alternative_is_proposed(self):
        value = inputs(); before = deepcopy(value)
        result = decide_scarce_supplement(value)
        self.assertEqual(result['send'], ['H2'])
        self.assertEqual(value, before)

    def test_cannot_send_to_original_or_duplicate_new_hospital(self):
        for candidates in (['H1'], ['H2', 'H2']):
            value = inputs(); value['eligible_new_hospitals'] = candidates
            with self.assertRaises(ValueError): decide_scarce_supplement(value)

    def test_false_contact_budget_is_rejected(self):
        value = inputs(); value['max_new'] = 3
        with self.assertRaises(ValueError): decide_scarce_supplement(value)

    def test_supplement_cannot_renew_or_replace_original_inquiry(self):
        value = inputs(); value['original_ask_event_sequence'] = 6
        with self.assertRaises(ValueError): decide_scarce_supplement(value)

    def test_initial_supplement_cannot_be_repeated_later(self):
        value = inputs(); value['opportunity_snapshot']['t'] = .25
        with self.assertRaises(ValueError): decide_scarce_supplement(value)

    def test_missing_fresh_alternative_does_not_send(self):
        value = inputs(); value['opportunity_snapshot']['candidates'][1]['resources']['ed_bed']['status'] = 'stale'
        self.assertEqual(decide_scarce_supplement(value)['send'], [])

    def test_fresh_candidate_that_is_not_new_cannot_be_requeried(self):
        value = inputs(); value['eligible_new_hospitals'] = []
        self.assertEqual(decide_scarce_supplement(value)['send'], [])

    def test_concurrency_and_episode_limits_cannot_be_bypassed_by_supplement(self):
        for changes in (dict(open_inquiries=3, episode_contacts=3, max_new=0),
                        dict(open_inquiries=1, episode_contacts=10, max_new=0)):
            value = inputs(); value.update(changes)
            with self.assertRaises(ValueError): decide_scarce_supplement(value)


class ScarceDelayReplayTests(unittest.TestCase):
    def test_current_clock_mro_is_used_by_frozen_replay_aggregator(self):
        names = [row.__name__ for row in ScarceDelayReplay.mro()]
        self.assertLess(names.index('ReplaySimulation'), names.index('CohortClockSimulation'))
        self.assertLess(names.index('CohortClockSimulation'), names.index('QuarterClockSimulation'))

    def test_baseline_calls_original_joint_engine_and_preserves_raw_outcomes(self):
        scenario = target()
        old = run_joint_policy_branch(scenario, inquiry='v4', allocation='H1', branch_id='same')
        new = run_scarce_delay_branch(scenario, inquiry='v4', allocation='H1',
                                     cohort_registration=False, scarce_routing=False, branch_id='same')
        for key in ('events', 'patient_outcomes', 'inquiry_decisions', 'resource_snapshots', 'ordered_emissions'):
            self.assertEqual(old[key], new[key])
        self.assertNotIn('scarce_supplement_records', new)

    def test_registration_only_changes_no_target_patient_result(self):
        scenario = target()
        old = run_scarce_delay_branch(scenario, cohort_registration=False, scarce_routing=False)
        new = run_scarce_delay_branch(scenario, cohort_registration=True, scarce_routing=False)
        self.assertEqual(old['patient_outcomes'], new['patient_outcomes'])
        self.assertEqual(new['scarce_supplement_records'], [])

    def test_all_current_requests_are_emitted_before_any_initial_inquiry(self):
        branch = run_scarce_delay_branch(target())
        requests = event_rows(branch, 'request'); marker = event_rows(branch, 'request_cohort_registered')[0]
        self.assertEqual({row['patient'] for row in marker['patients']}, {'P01', 'P02'})
        self.assertTrue(all(row['event_sequence'] < marker['event_sequence'] for row in requests))
        self.assertTrue(all(marker['event_sequence'] < row['event_sequence'] for row in event_rows(branch, 'ask') if row['t'] == 0))

    def test_seed_seven_and_eight_reduce_target_wait_without_stealing_scarce_bed(self):
        for seed in (7, 8):
            for allocation in ('H1', 'C1'):
                with self.subTest(seed=seed, allocation=allocation):
                    old = run_scarce_delay_branch(target(seed), allocation=allocation,
                                                 cohort_registration=False, scarce_routing=False)
                    new = run_scarce_delay_branch(target(seed), allocation=allocation)
                    self.assertEqual([p['treatment_start_t'] for p in old['patient_outcomes']], [5.75, 3.5])
                    self.assertEqual([p['treatment_start_t'] for p in new['patient_outcomes']], [5.5, 3.5])
                    self.assertEqual([p['inquiries'] for p in new['patient_outcomes']], [2, 1])
                    self.assertEqual(new['metrics']['scarce_supplement_sent'], 1)

    def test_original_fifo_handles_and_service_times_are_retained(self):
        old = run_scarce_delay_branch(target(), cohort_registration=False, scarce_routing=False)
        new = run_scarce_delay_branch(target())
        old_asks = [(e['t'], e['patient'], e['hospital'], e['attempt']) for e in event_rows(old, 'ask') if e['hospital'] == 'H01']
        new_asks = [(e['t'], e['patient'], e['hospital'], e['attempt']) for e in event_rows(new, 'ask') if e['hospital'] == 'H01']
        self.assertEqual(old_asks, new_asks)
        self.assertEqual([(e['t'], e['patient']) for e in event_rows(old, 'review_completed') if e['hospital'] == 'H01'],
                         [(e['t'], e['patient']) for e in event_rows(new, 'review_completed') if e['hospital'] == 'H01'])

    def test_alternative_silent_counterexample_keeps_original_acceptance_clock(self):
        scenario = target(8); scenario['response_matrix']['P01'] = {'H02': dict(delay=.5, silent=True)}
        old = run_scarce_delay_branch(scenario, cohort_registration=False, scarce_routing=False)
        new = run_scarce_delay_branch(scenario)
        self.assertEqual(old['patient_outcomes'], new['patient_outcomes'])
        self.assertEqual(new['patient_outcomes'][0]['treatment_start_t'], 9.25)

    def test_scarce_hospital_silence_does_not_lose_original_patient_offer(self):
        scenario = target(8); scenario['response_matrix']['P02'] = {'H01': dict(delay=.25, silent=True)}
        old = run_scarce_delay_branch(scenario, cohort_registration=False, scarce_routing=False)
        new = run_scarce_delay_branch(scenario)
        self.assertEqual(old['patient_outcomes'], new['patient_outcomes'])

    def test_hidden_late_reply_counterexample_remains_explicit_and_is_not_retimed(self):
        # The real earlier sender deadline must remain earlier too. This is
        # the observed reason this experimental candidate cannot be promoted
        # under a strict individual-nonworsening gate.
        scenario = make_scarce_scenario(8, 'scarce_alternative_late_deadline')
        old = run_scarce_delay_branch(scenario, cohort_registration=False, scarce_routing=False)
        new = run_scarce_delay_branch(scenario)
        self.assertEqual(old['patient_outcomes'][0]['treatment_start_t'], 8.25)
        self.assertEqual(new['patient_outcomes'][0]['treatment_start_t'], 9.25)
        first = next(e for e in event_rows(new, 'ask') if e['patient'] == 'P01' and e['hospital'] == 'H02')
        self.assertEqual((first['t'], first['timeout_at']), (0, 3))
        late = next(e for e in event_rows(new, 'late_reply') if e['patient'] == 'P01' and e['hospital'] == 'H02')
        self.assertEqual(late['t'], 3.25)
        self.assertTrue(late['ignored'])

    def test_cohort_registration_keeps_existing_fifo_submission_order(self):
        scenario = make_scenario(8, 'shared_fifo_burst')
        old = run_scarce_delay_branch(scenario, cohort_registration=False, scarce_routing=False)
        new = run_scarce_delay_branch(scenario, cohort_registration=True, scarce_routing=False)
        self.assertEqual(old['patient_outcomes'], new['patient_outcomes'])
        self.assertEqual([(e['t'], e['patient'], e['hospital']) for e in event_rows(old, 'ask')],
                         [(e['t'], e['patient'], e['hospital']) for e in event_rows(new, 'ask')])

    def test_actual_concurrency_three_and_episode_contacts_ten_are_reached_and_bounded(self):
        scenario = make_scarce_scenario(7, 'scarce_contact_concurrency_limits')
        branch = run_scarce_delay_branch(scenario)
        self.assertEqual(max(len(row['inputs']['patient_pending']) for row in branch['inquiry_decisions']), 3)
        contacts = Counter((row['patient'], row['inquiry_episode']) for row in event_rows(branch, 'ask'))
        self.assertEqual(max(contacts.values()), 10)
        self.assertTrue(all(count <= 10 for count in contacts.values()))
        self.assertEqual(branch['metrics']['scarce_supplement_sent'], 1)

    def test_actual_accepted_distant_alternative_is_not_duplicated_by_supplement(self):
        scenario = make_scarce_scenario(7, 'scarce_distant_accepted_alternative')
        seen = []
        original = ScarceDelayReplay._maybe_scarce_supplement
        def inspect(engine, patient, at):
            before = patient.inquiries
            original(engine, patient, at)
            if 'H02' in patient.valid:
                seen.append((at, patient.valid['H02'], before, patient.inquiries))
        with patch.object(ScarceDelayReplay, '_maybe_scarce_supplement', inspect):
            branch = run_scarce_delay_branch(scenario)
        self.assertTrue(seen)
        self.assertTrue(all(expiry > at and before == after for at, expiry, before, after in seen))
        self.assertEqual(len([e for e in event_rows(branch, 'ask') if e['patient'] == 'P01' and e['hospital'] == 'H02']), 1)

    def test_actually_expired_backup_never_passes_frozen_confirmation_gate(self):
        scenario = make_scenario(7, 'expired_backup_after_notify')
        branch = run_scarce_delay_branch(scenario)
        self.assertTrue(event_rows(branch, 'expire'))
        self.assertTrue(all(e['offer_expires_at'] > e['t'] for e in event_rows(branch, 'confirmation_gate') if e['passed']))

    def test_actual_movement_and_original_transfer_preparation_pass_frozen_audit(self):
        for family in ('scarce_options_staggered_replies', 'future_outage_after_early_offer'):
            scenario = make_scenario(7, family); branch = run_scarce_delay_branch(scenario)
            self.assertTrue(audit_motion(scenario, branch)['movement_legs'])

    def test_higher_priority_flexible_patient_never_gets_scarce_supplement(self):
        scenario = target(); scenario['patients'][1]['priority'] = 2
        branch = run_scarce_delay_branch(scenario)
        self.assertEqual(branch['metrics']['scarce_supplement_sent'], 0)

    def test_enough_capacity_does_not_add_queries(self):
        scenario = target(); scenario['hospitals'][0]['capacity']['ed_bed'] = 2
        branch = run_scarce_delay_branch(scenario)
        self.assertEqual(branch['metrics']['scarce_supplement_sent'], 0)
        self.assertEqual([p['inquiries'] for p in branch['patient_outcomes']], [1, 1])

    def test_future_request_is_absent_from_current_opportunity_snapshot(self):
        scenario = target(); p = scenario['patients'][1]
        for key in ('occur_t', 'dispatch_t', 'ambulance_arrive_t', 'search_start_t'):
            p[key] = 1
        branch = run_scarce_delay_branch(scenario)
        first = branch['scarce_supplement_records'][0]['inputs']['opportunity_snapshot']
        self.assertEqual([row['id'] for row in first['registered_search_patients']], ['P01'])
        self.assertEqual(branch['scarce_supplement_records'][0]['result']['send'], [])

    def test_supplement_uses_actual_original_selector_inputs_and_actual_ask_event(self):
        branch = run_scarce_delay_branch(target())
        record = next(row for row in branch['scarce_supplement_records'] if row['result']['send'])
        indexed = {row['event_sequence']: row for row in branch['ordered_emissions']}
        original = indexed[record['inputs']['original_ask_event_sequence']]
        selector = next(row for row in branch['inquiry_decisions'] if row['event_sequence'] == record['inputs']['original_selector_event_sequence'])
        self.assertEqual(selector['decision']['send'], [original['hospital']])
        supplement = indexed[record['event_sequence'] + 1]
        self.assertEqual((supplement['type'], supplement['hospital'], supplement['t']), ('ask', 'H02', 0))

    def test_no_added_resource_publication_or_synthetic_acceptance(self):
        branch = run_scarce_delay_branch(target())
        self.assertEqual(branch['metrics']['resource_publications'], 0)
        self.assertFalse(branch['resource_updates_enabled'])
        self.assertEqual([(e['hospital'], e['t']) for e in event_rows(branch, 'accept') if e['patient'] == 'P01'], [('H01', .25), ('H02', .5)])

    def test_complete_replay_respects_explicit_execution_order_and_detaches_inputs(self):
        scenario = target(); before = deepcopy(scenario); policies = deepcopy(list(DEFAULT_SCARCE_DELAY_POLICIES[:3]))
        order = [row['id'] for row in reversed(policies)]
        report = run_scarce_delay_replay(scenario, policies, order)
        self.assertEqual(report['execution_order'], order)
        self.assertEqual([b['id'] for b in report['branches']], [row['id'] for row in policies])
        self.assertEqual(scenario, before)

    def test_invalid_contract_flags_are_rejected(self):
        for args in (dict(cohort_registration=False, scarce_routing=True), dict(cohort_registration=1),
                     dict(scarce_routing='yes'), dict(allocation='R1'), dict(inquiry='v2')):
            with self.assertRaises(ValueError): run_scarce_delay_branch(target(), **args)


if __name__ == '__main__':
    unittest.main()
