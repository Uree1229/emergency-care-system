"""Received-reply lifecycle, causal boundaries and physical regression tests."""
from copy import deepcopy
from collections import Counter
import unittest
from unittest.mock import patch

from simulation.deadline_recovery_engine import (
    DEFAULT_DEADLINE_RECOVERY_POLICIES, DeadlineRecoveryReplay,
    condition_fingerprint, decide_late_offer, run_deadline_recovery_branch,
    run_deadline_recovery_replay,
)
from simulation.scarce_delay_engine import run_scarce_delay_branch
from simulation.scarce_delay_scenarios import make_scenario
from simulation.outage_recovery_audit import audit_motion


def fresh(free=1):
    return dict(status='fresh', free=free, observed_at=3, received_at=3)


def snapshot():
    condition = dict(id='FLEX', priority=4, demand={'ed_bed':1}, required_capabilities=['BASIC'])
    current = dict(id='FLEX', state='SEARCH', condition=condition,
        condition_fingerprint=condition_fingerprint(condition), latest_attempt=1, first_commit_t=None,
        valid_offers=[], registered_request_sequence=2)
    observed = dict(id='FLEX', condition=deepcopy(condition), state='SEARCH', request_t=0,
        request_event_sequence=2, compatible_hospitals=['H1','H2'],
        pending_inquiries=[], valid_offers=[], resource_views={})
    return dict(t=3.25, receipt=dict(patient='FLEX', hospital='H2', attempt=1,
        submitted_at=0, timeout_at=3, ask_event_sequence=7, supplement_review_event_sequence=6,
        condition_fingerprint=condition_fingerprint(condition), received_at=3.25,
        outcome='accept', receipt_event_sequence=30), patient=current,
        resources={'ed_bed':fresh()}, accept_ttl_minutes=10,
        registered_search_patients=[observed])


def claim(*, priority=4, pending=True, offered=False):
    condition = dict(id='LIMITED', priority=priority, demand={'ed_bed':1}, required_capabilities=['BASIC'])
    return dict(id='LIMITED', condition=condition, state='SEARCH', request_t=3,
        request_event_sequence=22, compatible_hospitals=['H2'],
        pending_inquiries=[dict(hospital='H2',attempt=1,submitted_at=3,timeout_at=6,
                               ask_event_sequence=26)] if pending else [],
        valid_offers=[dict(hospital='H2',accepted_at=3,expires_at=13,
                          accept_event_sequence=27)] if offered else [],
        resource_views={'H2':{'ed_bed':fresh()}})


def rows(branch, kind):
    return [e for e in branch['ordered_emissions'] if e['type']==kind]


def target(seed=7):
    return make_scenario(seed, 'scarce_alternative_late_deadline')


def run(case=None, **kwargs):
    return run_deadline_recovery_branch(case or target(), late_reply_recovery=True, **kwargs)


class LateOfferDecisionTests(unittest.TestCase):
    def test_actual_received_reply_with_current_fresh_resources_gets_new_offer(self):
        result=decide_late_offer(snapshot())
        self.assertEqual(result,dict(eligible=True,hospital='H2',expires_at=13.25,
                                    protected_patients=[],reason='RECEIVED_LATE_OFFER'))

    def test_original_absolute_timeout_and_fifteen_second_window_are_fixed(self):
        for t in (3,3.5,3.75,4):
            value=snapshot();value['t']=value['receipt']['received_at']=t
            self.assertEqual(decide_late_offer(value)['reason'],'OUTSIDE_WINDOW')

    def test_reply_before_timeout_does_not_become_a_late_offer(self):
        value=snapshot();value['t']=value['receipt']['received_at']=2.75
        self.assertFalse(decide_late_offer(value)['eligible'])

    def test_absent_or_future_receipt_cannot_generate_acceptance(self):
        value=snapshot();value['receipt']['received_at']=3.5
        with self.assertRaises(ValueError):decide_late_offer(value)

    def test_decline_is_not_assumed_acceptance_from_completed_work(self):
        value=snapshot();value['receipt']['outcome']='decline'
        self.assertEqual(decide_late_offer(value)['reason'],'HOSPITAL_DECLINED')

    def test_silent_private_flag_is_rejected(self):
        value=snapshot();value['receipt']['silent']=False
        with self.assertRaises(ValueError):decide_late_offer(value)

    def test_future_finish_time_is_rejected(self):
        value=snapshot();value['receipt']['finish_t']=3.25
        with self.assertRaises(ValueError):decide_late_offer(value)

    def test_wait_for_callback_or_timeout_extension_is_not_an_input(self):
        value=snapshot();value['soft_timeout_at']=3.25
        with self.assertRaises(ValueError):decide_late_offer(value)

    def test_changed_patient_demands_cannot_reuse_old_review(self):
        value=snapshot();value['patient']['condition']['demand']['ed_bed']=2
        value['patient']['condition_fingerprint']=condition_fingerprint(value['patient']['condition'])
        self.assertEqual(decide_late_offer(value)['reason'],'CONDITION_CHANGED')

    def test_changed_patient_capability_cannot_reuse_old_review(self):
        value=snapshot();value['patient']['condition']['required_capabilities'].append('NEW')
        value['patient']['condition_fingerprint']=condition_fingerprint(value['patient']['condition'])
        self.assertEqual(decide_late_offer(value)['reason'],'CONDITION_CHANGED')

    def test_changed_priority_cannot_reuse_old_review(self):
        value=snapshot();value['patient']['condition']['priority']=2
        value['patient']['condition_fingerprint']=condition_fingerprint(value['patient']['condition'])
        self.assertEqual(decide_late_offer(value)['reason'],'CONDITION_CHANGED')

    def test_stale_claimed_fingerprint_is_rejected(self):
        value=snapshot();value['patient']['condition']['demand']['ed_bed']=2
        with self.assertRaises(ValueError):decide_late_offer(value)

    def test_already_committed_patient_is_not_redirected(self):
        for state in ('TRANSIT','QUEUED','TREATING','COMPLETE','TRANSFER_WAIT'):
            value=snapshot();value['patient']['state']=state
            self.assertEqual(decide_late_offer(value)['reason'],'NOT_SEARCHING')

    def test_transfer_search_does_not_reuse_initial_supplement(self):
        value=snapshot();value['patient']['first_commit_t']=.25
        self.assertEqual(decide_late_offer(value)['reason'],'PREVIOUS_COMMITMENT')

    def test_new_same_hospital_attempt_rejects_obsolete_callback(self):
        value=snapshot();value['patient']['latest_attempt']=2
        self.assertEqual(decide_late_offer(value)['reason'],'NEWER_INQUIRY')

    def test_other_current_usable_offer_rejects_optional_callback(self):
        value=snapshot();value['patient']['valid_offers']=[dict(hospital='H1',expires_at=10,
                                                               resources={'ed_bed':fresh()})]
        self.assertEqual(decide_late_offer(value)['reason'],'OTHER_VALID_OFFER')

    def test_expired_offer_does_not_block_and_is_not_renewed(self):
        value=snapshot();value['patient']['valid_offers']=[dict(hospital='H1',expires_at=3.25,
                                                               resources={'ed_bed':fresh()})]
        before=deepcopy(value)
        self.assertTrue(decide_late_offer(value)['eligible'])
        self.assertEqual(value,before)

    def test_current_shortage_makes_old_ttl_unusable(self):
        value=snapshot();value['patient']['valid_offers']=[dict(hospital='H1',expires_at=10,
                                                               resources={'ed_bed':fresh(0)})]
        self.assertTrue(decide_late_offer(value)['eligible'])

    def test_unknown_stale_short_or_nan_required_resources_reject(self):
        for view in (dict(status='unknown',free=None),dict(status='stale',free=1),
                     fresh(0),dict(status='fresh',free=float('nan'))):
            value=snapshot();value['resources']['ed_bed']=view
            self.assertEqual(decide_late_offer(value)['reason'],'RESOURCE_NOT_VERIFIED')

    def test_every_required_resource_needs_current_verification(self):
        value=snapshot();value['patient']['condition']['demand']['team']=1
        fingerprint=condition_fingerprint(value['patient']['condition'])
        value['patient']['condition_fingerprint']=value['receipt']['condition_fingerprint']=fingerprint
        self.assertEqual(decide_late_offer(value)['reason'],'RESOURCE_NOT_VERIFIED')

    def test_equal_priority_single_hospital_pending_opportunity_is_protected(self):
        value=snapshot();value['registered_search_patients'].append(claim())
        result=decide_late_offer(value)
        self.assertEqual((result['reason'],result['protected_patients']),('SCARCE_OPPORTUNITY',['LIMITED']))

    def test_received_valid_opportunity_is_protected_without_pending_inquiry(self):
        value=snapshot();value['registered_search_patients'].append(claim(pending=False,offered=True))
        self.assertEqual(decide_late_offer(value)['reason'],'SCARCE_OPPORTUNITY')

    def test_lower_priority_demand_does_not_force_optional_offer_rejection(self):
        value=snapshot();value['registered_search_patients'].append(claim(priority=2))
        self.assertTrue(decide_late_offer(value)['eligible'])

    def test_sufficient_beds_for_both_do_not_make_false_competition(self):
        value=snapshot();value['resources']['ed_bed']=fresh(2)
        q=claim();q['resource_views']['H2']['ed_bed']=fresh(2)
        value['registered_search_patients'].append(q)
        self.assertTrue(decide_late_offer(value)['eligible'])

    def test_unverified_or_short_other_patient_does_not_fabricate_opportunity(self):
        for view in (dict(status='unknown',free=None),dict(status='stale',free=1),fresh(0)):
            value=snapshot();q=claim();q['resource_views']['H2']['ed_bed']=view
            value['registered_search_patients'].append(q)
            self.assertTrue(decide_late_offer(value)['eligible'])

    def test_expired_pending_window_does_not_restart(self):
        value=snapshot();q=claim();q['pending_inquiries'][0].update(submitted_at=2.25,timeout_at=5.25)
        value['registered_search_patients'].append(q)
        self.assertTrue(decide_late_offer(value)['eligible'])

    def test_future_or_unregistered_patient_is_not_allowed_as_competition(self):
        value=snapshot();q=claim();q['request_t']=4;value['registered_search_patients'].append(q)
        with self.assertRaises(ValueError):decide_late_offer(value)

    def test_fingerprint_is_independent_of_dictionary_and_capability_order(self):
        p=dict(id='P',priority=4,demand={'bed':1,'team':2},required_capabilities=['B','A'])
        q=dict(required_capabilities=['A','B'],demand={'team':2,'bed':1},priority=4,id='P')
        self.assertEqual(condition_fingerprint(p),condition_fingerprint(q))

    def test_unknown_condition_fields_are_not_policy_evidence(self):
        value=snapshot();value['patient']['condition']['future_treatment_end']=10
        with self.assertRaises(ValueError):decide_late_offer(value)

    def test_snapshots_and_results_do_not_mutate_inputs(self):
        value=snapshot();before=deepcopy(value);result=decide_late_offer(value)
        result['protected_patients'].append('P')
        self.assertEqual(value,before)


class DeadlineRecoveryReplayTests(unittest.TestCase):
    def test_disabled_branch_is_exact_previous_runner_output(self):
        for cohort,routing in ((False,False),(True,False),(True,True)):
            old=run_scarce_delay_branch(target(),cohort_registration=cohort,scarce_routing=routing,
                                        branch_id='same')
            new=run_deadline_recovery_branch(target(),cohort_registration=cohort,scarce_routing=routing,
                                             branch_id='same')
            for key in ('events','ordered_emissions','patient_outcomes','inquiry_decisions',
                        'resource_snapshots','pending_guard_records'):
                self.assertEqual(old[key],new[key])

    def test_actual_late_replies_restore_protected_result_for_two_allocators(self):
        for seed in (7,8):
            for allocation in ('H1','C1'):
                b=run(target(seed),allocation=allocation)
                self.assertEqual([p['treatment_start_t'] for p in b['patient_outcomes']],[8.25,3.5])
                self.assertEqual(b['metrics']['late_offer_accepted'],1)
                self.assertEqual(b['metrics']['resource_publications'],0)

    def test_previous_fifteen_second_early_supplement_improvement_is_preserved(self):
        for allocation in ('H1','C1'):
            case=make_scenario(7,'scarce_options_staggered_replies')
            old=run_scarce_delay_branch(case,allocation=allocation)
            new=run(case,allocation=allocation)
            self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
            self.assertEqual([p['treatment_start_t'] for p in new['patient_outcomes']],[5.5,3.5])

    def test_original_handle_still_times_out_at_three_and_observation_remains_timeout(self):
        b=run()
        first=next(e for e in rows(b,'ask') if e['patient']=='P01' and e['hospital']=='H02')
        self.assertEqual((first['t'],first['timeout_at']),(0,3))
        feedback=next(e for e in rows(b,'inquiry_feedback') if e['patient']=='P01' and e['hospital']=='H02')
        self.assertEqual((feedback['t'],feedback['outcome']),(3,'timeout'))
        self.assertTrue(any(e['t']==3 and e['hospital']=='H02' for e in rows(b,'no_response')))

    def test_completion_and_received_outcome_precede_new_offer_and_ordinary_gate(self):
        b=run();kinds=('review_completed','late_reply','hospital_reply_received','late_offer_review',
                       'late_offer_accepted','accept','confirmation_gate','commit')
        selected=[next(e for e in rows(b,k) if e.get('patient')=='P01' and e.get('hospital')=='H02'
                       and e['t']==3.25) for k in kinds]
        self.assertEqual([e['event_sequence'] for e in selected],
                         sorted(e['event_sequence'] for e in selected))
        self.assertTrue(selected[1]['ignored'])
        self.assertTrue(selected[-2]['passed'])

    def test_fresh_offer_lifetime_begins_at_actual_receipt(self):
        b=run();offer=rows(b,'late_offer_accepted')[0]
        self.assertEqual((offer['t'],offer['expires_at']),(3.25,13.25))
        gate=next(e for e in rows(b,'confirmation_gate') if e['patient']=='P01' and e['passed'])
        self.assertEqual(gate['offer_expires_at'],13.25)

    def test_original_hospital_work_calendar_is_preserved_and_retry_is_avoided(self):
        old=run_scarce_delay_branch(target());new=run()
        for kind in ('ask','review_started','review_completed','late_reply','inquiry_feedback'):
            fields=('t','type','patient','hospital','attempt','outcome')
            left=Counter(tuple(e.get(k) for k in fields) for e in rows(old,kind)
                         if e.get('attempt',1)==1 and e.get('submitted_t',0)==0)
            right=Counter(tuple(e.get(k) for k in fields) for e in rows(new,kind)
                          if e.get('attempt',1)==1 and e.get('submitted_t',0)==0)
            self.assertEqual(left,right)
        self.assertEqual(sum(p['inquiries'] for p in old['patient_outcomes'])-
                         sum(p['inquiries'] for p in new['patient_outcomes']),1)

    def test_silent_alternative_has_no_late_received_offer_or_added_wait(self):
        case=make_scenario(7,'scarce_alternative_silent')
        old=run_scarce_delay_branch(case);new=run(case)
        self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
        self.assertFalse(new['late_offer_records'])

    def test_reply_at_three_and_half_or_three_and_three_quarters_is_rejected(self):
        for delay in (.5,.75):
            case=target();case['response_matrix']['*']['H02']['delay']=delay
            old=run_scarce_delay_branch(case);new=run(case)
            self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
            self.assertTrue(new['late_offer_records'])
            self.assertTrue(all(r['result']['reason']=='OUTSIDE_WINDOW' for r in new['late_offer_records']))

    def test_completed_hospital_work_with_current_shortage_returns_decline(self):
        case=target();case['exogenous_events']=[dict(t=3,type='resource_unavailable',hospital='H02',
                                                   resource='ed_bed',until=12)]
        old=run_scarce_delay_branch(case);new=run(case)
        self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
        receipt=rows(new,'hospital_reply_received')[0]
        self.assertEqual(receipt['outcome'],'decline')
        self.assertEqual(new['late_offer_records'][0]['result']['reason'],'HOSPITAL_DECLINED')

    def test_new_offer_does_not_reuse_old_unusable_offer_wait_clock(self):
        seen=[];original=DeadlineRecoveryReplay.resolve_asks
        def observed(instance,p,t):
            result=original(instance,p,t)
            if p.id=='P01' and t==3.25:
                seen.append((p.first_valid_t,p.accepted_at.get('H01')))
            return result
        with patch.object(DeadlineRecoveryReplay,'resolve_asks',observed):run()
        # The old acceptance timestamp remains intact; new wait starts at receipt.
        self.assertEqual(seen,[(3.25,.25)])

    def test_nearer_pending_hospital_is_not_skipped_using_an_old_unusable_clock(self):
        case=target();hospital=deepcopy(case['hospitals'][1])
        hospital.update(id='H03',lat=(case['hospitals'][0]['lat']+hospital['lat'])/2,
                        initial_occupancy=[dict(until=3,demand={'ed_bed':1})])
        case['hospitals'].append(hospital)
        case['response_matrix']['*']['H03']=dict(delay=.5,silent=False)
        for allocation in ('H1','C1'):
            old=run_scarce_delay_branch(case,allocation=allocation)
            new=run(case,allocation=allocation)
            self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
            self.assertEqual([p['treatment_start_t'] for p in new['patient_outcomes']],[7.5,3.5])
            first=next(e for e in rows(new,'late_offer_accepted'))
            commit=next(e for e in rows(new,'commit') if e['patient']=='P01')
            self.assertEqual((first['t'],commit['t'],commit['hospital']),(3.25,3.5,'H03'))

    def test_new_late_offer_uses_existing_motion_and_transfer_physics(self):
        b=run();self.assertTrue(audit_motion(target(),b))
        self.assertEqual(b['patient_outcomes'][0]['final_arrive_t'],8.25)

    def test_execution_order_is_explicit_and_canonical_output_is_preserved(self):
        policies=deepcopy(list(DEFAULT_DEADLINE_RECOVERY_POLICIES[:3]))
        result=run_deadline_recovery_replay(target(),policies,execution_order=[p['id'] for p in reversed(policies)])
        self.assertEqual([b['id'] for b in result['branches']],[p['id'] for p in policies])

    def test_default_comparison_has_six_factor_branches_and_two_fixed_references(self):
        self.assertEqual(len(DEFAULT_DEADLINE_RECOVERY_POLICIES),8)
        self.assertEqual(sum(p['late_reply_recovery'] for p in DEFAULT_DEADLINE_RECOVERY_POLICIES),2)

    def test_scenario_and_policy_inputs_are_not_mutated(self):
        case=target();policies=deepcopy(list(DEFAULT_DEADLINE_RECOVERY_POLICIES[:3]))
        before=(deepcopy(case),deepcopy(policies))
        run_deadline_recovery_replay(case,policies)
        self.assertEqual((case,policies),before)

    def test_malformed_flags_or_lifecycle_modes_are_rejected(self):
        for kwargs in (dict(late_reply_recovery=1),dict(late_reply_recovery=True,scarce_routing=False),
                       dict(late_reply_recovery=True,inquiry='fixed_3'),dict(allocation='R1'),
                       dict(cohort_registration=False,scarce_routing=True)):
            with self.assertRaises(ValueError):run_deadline_recovery_branch(target(),**kwargs)


if __name__=='__main__':unittest.main()
