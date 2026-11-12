"""Conservative pending guard and multiple-reply timing regression."""
from copy import deepcopy
import unittest

from simulation.deadline_recovery_engine import decide_late_offer, run_deadline_recovery_branch
from simulation.deadline_safe_engine import (
    DEFAULT_DEADLINE_SAFE_POLICIES, DEADLINE_SAFE_SPEC, decide_safe_late_offer,
    run_deadline_safe_branch, run_deadline_safe_replay,
)
from simulation.scarce_delay_scenarios import make_scenario
from simulation.outage_recovery_audit import audit_motion
from test_deadline_recovery_engine import snapshot as v1_snapshot, target, rows


def snapshot(*, pending=True):
    value=v1_snapshot()
    value['patient']['pending_inquiries']=[dict(hospital='H3',attempt=1,submitted_at=3,
        timeout_at=6,ask_event_sequence=25)] if pending else []
    return value


def multi_pending(seed=7):
    case=target(seed);case['accept_ttl_minutes']=3
    case['id']=f'diagnostic-deadline-multiple-pending-{seed}'
    case['family']='diagnostic_deadline_multiple_pending'
    for hid,fraction,service in (('H03',1.4,.25),('H04',.5,2.5)):
        hospital=deepcopy(case['hospitals'][1]);base=case['patients'][0]['location'][0]
        hospital.update(id=hid,lat=base+(case['hospitals'][0]['lat']-base)*fraction,
            initial_occupancy=[dict(until=3,demand={'ed_bed':1})])
        case['hospitals'].append(hospital)
        case['response_matrix']['*'][hid]=dict(delay=service,silent=False)
    return case


def single_pending(seed=7):
    case=target(seed);hospital=deepcopy(case['hospitals'][1])
    hospital.update(id='H03',lat=(case['hospitals'][0]['lat']+hospital['lat'])/2,
                    initial_occupancy=[dict(until=3,demand={'ed_bed':1})])
    case['hospitals'].append(hospital)
    case['response_matrix']['*']['H03']=dict(delay=.5,silent=False)
    return case


def safe(case=None,**kwargs):
    return run_deadline_safe_branch(case or target(),late_reply_recovery=True,
                                    late_pending_guard=True,**kwargs)


class SafeLateOfferDecisionTests(unittest.TestCase):
    def test_any_actual_current_pending_handle_ignores_optional_late_offer(self):
        self.assertEqual(decide_safe_late_offer(snapshot()),dict(eligible=False,hospital=None,
            expires_at=None,protected_patients=[],reason='PENDING_INQUIRY'))

    def test_pending_guard_does_not_need_hidden_service_time_or_silence(self):
        for hospital in ('H2','H3','H4'):
            value=snapshot();value['patient']['pending_inquiries'][0]['hospital']=hospital
            self.assertEqual(decide_safe_late_offer(value)['reason'],'PENDING_INQUIRY')

    def test_no_pending_inquiry_preserves_every_previous_pure_decision(self):
        for change in ('eligible','decline','outside','committed','shortage'):
            value=snapshot(pending=False)
            if change=='decline':value['receipt']['outcome']='decline'
            elif change=='outside':value['t']=value['receipt']['received_at']=3.5
            elif change=='committed':value['patient']['state']='TRANSIT'
            elif change=='shortage':value['resources']['ed_bed']['free']=0
            old=deepcopy(value);old['patient'].pop('pending_inquiries')
            self.assertEqual(decide_safe_late_offer(value),decide_late_offer(old))

    def test_pending_guard_is_always_a_rejection_and_never_revives_an_old_failure(self):
        value=snapshot();value['t']=value['receipt']['received_at']=3.5
        self.assertEqual(decide_safe_late_offer(value)['reason'],'PENDING_INQUIRY')
        self.assertFalse(decide_safe_late_offer(value)['eligible'])

    def test_original_public_submission_and_timeout_are_required(self):
        for field in ('submitted_at','timeout_at','ask_event_sequence'):
            value=snapshot();value['patient']['pending_inquiries'][0].pop(field)
            with self.assertRaises(ValueError):decide_safe_late_offer(value)

    def test_hidden_calendar_or_silence_cannot_enter_pending_policy_input(self):
        for field,value in (('at',4),('finish_t',4),('silent',False),('worker',0)):
            data=snapshot();data['patient']['pending_inquiries'][0][field]=value
            with self.assertRaises(ValueError):decide_safe_late_offer(data)

    def test_future_or_invalid_pending_handle_is_rejected(self):
        for changes in (dict(submitted_at=4),dict(timeout_at=2),dict(attempt=0),
                        dict(ask_event_sequence=-1),dict(hospital='')):
            value=snapshot();value['patient']['pending_inquiries'][0].update(changes)
            with self.assertRaises(ValueError):decide_safe_late_offer(value)

    def test_duplicate_pending_handle_is_rejected(self):
        value=snapshot();value['patient']['pending_inquiries']*=2
        with self.assertRaises(ValueError):decide_safe_late_offer(value)

    def test_pending_handles_cannot_replace_previous_patient_and_receipt_validation(self):
        value=snapshot();value['receipt']['received_at']=4
        with self.assertRaises(ValueError):decide_safe_late_offer(value)

    def test_callback_never_changes_inputs_or_original_handle(self):
        value=snapshot();before=deepcopy(value);result=decide_safe_late_offer(value)
        result['protected_patients'].append('OTHER')
        self.assertEqual(value,before)

    def test_missing_pending_ledger_is_not_assumed_empty(self):
        value=snapshot();value['patient'].pop('pending_inquiries')
        with self.assertRaises(ValueError):decide_safe_late_offer(value)


class DeadlineSafeReplayTests(unittest.TestCase):
    def test_disabled_guard_returns_exact_frozen_v1_outputs(self):
        for cohort,scarce,late in ((False,False,False),(True,True,False),(True,True,True)):
            old=run_deadline_recovery_branch(target(),cohort_registration=cohort,
                scarce_routing=scarce,late_reply_recovery=late,branch_id='same')
            new=run_deadline_safe_branch(target(),cohort_registration=cohort,
                scarce_routing=scarce,late_reply_recovery=late,branch_id='same')
            for key in ('patient_outcomes','ordered_emissions','events','inquiry_decisions',
                        'pending_guard_records','resource_snapshots'):
                self.assertEqual(old[key],new[key])
            self.assertNotIn('late_pending_guard_enabled',new)

    def test_no_pending_target_keeps_the_one_minute_recovery_for_both_rules(self):
        for seed in (7,8):
            for allocation in ('H1','C1'):
                new=safe(target(seed),allocation=allocation)
                self.assertEqual([p['treatment_start_t'] for p in new['patient_outcomes']],[8.25,3.5])
                self.assertEqual(new['late_offer_records'][0]['snapshot']['patient']['pending_inquiries'],[])
                self.assertEqual(new['metrics']['late_offer_accepted'],1)

    def test_original_fast_supplement_improvement_is_unchanged(self):
        for allocation in ('H1','C1'):
            case=make_scenario(7,'scarce_options_staggered_replies')
            old=run_deadline_recovery_branch(case,allocation=allocation)
            new=safe(case,allocation=allocation)
            self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
            self.assertEqual([p['treatment_start_t'] for p in new['patient_outcomes']],[5.5,3.5])

    def test_two_pending_hospitals_do_not_lose_the_nearer_late_reply(self):
        for seed in (7,8):
            for allocation in ('H1','C1'):
                case=multi_pending(seed)
                old=run_deadline_recovery_branch(case,allocation=allocation)
                v1=run_deadline_recovery_branch(case,allocation=allocation,late_reply_recovery=True)
                new=safe(case,allocation=allocation)
                self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
                self.assertEqual(old['patient_outcomes'][0]['treatment_start_t'],7.5)
                self.assertEqual(v1['patient_outcomes'][0]['treatment_start_t'],9.25)
                self.assertEqual(new['late_offer_records'][0]['result']['reason'],'PENDING_INQUIRY')
                commit=next(e for e in rows(new,'commit') if e['patient']=='P01')
                self.assertEqual((commit['t'],commit['hospital']),(5.5,'H04'))
                self.assertFalse(rows(new,'late_offer_accepted'))

    def test_single_pending_hospital_is_also_ignored_conservatively(self):
        for allocation in ('H1','C1'):
            case=single_pending()
            old=run_deadline_recovery_branch(case,allocation=allocation)
            new=safe(case,allocation=allocation)
            self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
            self.assertEqual(new['patient_outcomes'][0]['treatment_start_t'],7.5)
            self.assertEqual(new['late_offer_records'][0]['result']['reason'],'PENDING_INQUIRY')

    def test_rejected_callback_does_not_shift_original_inquiries_or_response_calendar(self):
        case=multi_pending();old=run_deadline_recovery_branch(case);new=safe(case)
        for kind in ('ask','inquiry_feedback','no_response','review_started','review_completed','late_reply'):
            fields=('t','patient','hospital','attempt','submitted_t','outcome')
            self.assertEqual(sorted((tuple(e.get(k) for k in fields) for e in rows(old,kind)),key=repr),
                             sorted((tuple(e.get(k) for k in fields) for e in rows(new,kind)),key=repr))

    def test_pending_raw_handle_matches_the_actual_earlier_ask(self):
        new=safe(multi_pending());record=new['late_offer_records'][0]
        pending=record['snapshot']['patient']['pending_inquiries']
        self.assertEqual(len(pending),1)
        ask=next(e for e in rows(new,'ask') if e['event_sequence']==pending[0]['ask_event_sequence'])
        self.assertEqual(pending[0],dict(hospital=ask['hospital'],attempt=ask['attempt'],
            submitted_at=ask['submitted_at'],timeout_at=ask['timeout_at'],ask_event_sequence=ask['event_sequence']))

    def test_safe_recovery_still_needs_actual_hospital_reply(self):
        case=make_scenario(7,'scarce_alternative_silent')
        old=run_deadline_recovery_branch(case);new=safe(case)
        self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
        self.assertFalse(new['late_offer_records'])

    def test_original_timeout_and_offer_ttl_remain_explicit(self):
        new=safe();ask=next(e for e in rows(new,'ask') if e['hospital']=='H02')
        feedback=next(e for e in rows(new,'inquiry_feedback') if e['hospital']=='H02')
        offer=rows(new,'late_offer_accepted')[0]
        self.assertEqual((ask['timeout_at'],feedback['t'],feedback['outcome']),(3,3,'timeout'))
        self.assertEqual((offer['t'],offer['expires_at']),(3.25,13.25))

    def test_added_guard_does_not_change_physical_movement_or_add_resource_publication(self):
        case=multi_pending();new=safe(case)
        self.assertTrue(audit_motion(case,new))
        self.assertEqual(new['metrics']['resource_publications'],0)

    def test_raw_flags_describe_actual_new_guard_only(self):
        new=safe()
        self.assertTrue(new['late_pending_guard_enabled'])
        self.assertEqual(new['deadline_safe_spec'],DEADLINE_SAFE_SPEC)
        self.assertFalse(new['deadline_safe_spec']['blocking_wait_added'])

    def test_ten_default_branches_separate_v1_from_guarded_candidate(self):
        self.assertEqual(len(DEFAULT_DEADLINE_SAFE_POLICIES),10)
        self.assertEqual(sum(p['late_pending_guard'] for p in DEFAULT_DEADLINE_SAFE_POLICIES),2)
        self.assertEqual(sum(p['late_reply_recovery'] for p in DEFAULT_DEADLINE_SAFE_POLICIES),4)

    def test_execution_order_and_canonical_output_are_preserved(self):
        policies=deepcopy(list(DEFAULT_DEADLINE_SAFE_POLICIES[:4]))
        result=run_deadline_safe_replay(target(),policies,execution_order=[p['id'] for p in reversed(policies)])
        self.assertEqual([b['id'] for b in result['branches']],[p['id'] for p in policies])

    def test_input_scenarios_and_policy_rows_are_not_mutated(self):
        case=target();policies=deepcopy(list(DEFAULT_DEADLINE_SAFE_POLICIES[:4]))
        before=deepcopy((case,policies));run_deadline_safe_replay(case,policies)
        self.assertEqual((case,policies),before)

    def test_guard_requires_actual_late_recovery_and_explicit_flags(self):
        for changes in (dict(late_pending_guard=1),dict(late_pending_guard=True),
                dict(late_pending_guard=True,late_reply_recovery=True,scarce_routing=False),
                dict(late_pending_guard=True,late_reply_recovery=True,cohort_registration=False),
                dict(late_pending_guard=True,late_reply_recovery=True,inquiry='fixed_3')):
            with self.assertRaises(ValueError):run_deadline_safe_branch(target(),**changes)


if __name__=='__main__':unittest.main()
