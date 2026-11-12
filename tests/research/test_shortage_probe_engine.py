"""Current-data boundaries and real hospital-work probe regressions."""
from copy import deepcopy
import unittest

from simulation.shortage_probe_engine import (
    DEFAULT_SHORTAGE_PROBE_POLICIES,SHORTAGE_PROBE_SPEC,decide_shortage_probe,decide_nonblocking_probe_ready,
    run_shortage_probe_branch,run_shortage_probe_replay,
)
from simulation.deadline_safe_engine import run_deadline_safe_branch
from simulation.shortage_probe_scenarios import make_scenario as make_probe_scenario
from simulation.outage_recovery_audit import audit_motion
from test_deadline_safe_engine import target,multi_pending


def view(*,capacity=1,used=0,t=.5,age=.5,status='fresh'):
    return dict(capacity=capacity,used=used,committed=0,queued=0,free=capacity-used,
        observed_at=t-age,received_at=0,sequence=0,source='initial',status=status,age_minutes=age)


def handle(hospital='H02',sequence=1):
    return dict(hospital=hospital,attempt=1,submitted_at=0,timeout_at=3,ask_event_sequence=sequence)


def snapshot():
    own=dict(id='P01',state='SEARCH',priority=4,request_t=0,request_event_sequence=0,
        inquiry_episode=0,episode_contacts=1,inquiry_count=1,first_commit_t=None,
        first_ask=handle(),tried_hospitals=['H02'],pending_inquiries=[handle()],valid_offers=[],
        demand={'ed_bed':1},required_capabilities=[],last_contact_at=0)
    return dict(t=.5,phase='before_joint_batch',patient=own,probe_count=0,
        candidates=[dict(hospital='H01',travel_minutes=3,resources={'ed_bed':view(used=1)}),
                    dict(hospital='H02',travel_minutes=5,resources={'ed_bed':view()})],
        registered_search_patients=[dict(id='P01',state='SEARCH',request_t=0,request_event_sequence=0,
            pending_inquiries=[handle()],valid_offers=[])])


def nonblocking_snapshot():
    value=snapshot()
    return dict(t=.5,patient=dict(id='P01',state='SEARCH',first_commit_t=None,
        pending_inquiries=[handle('H01',3)],valid_offers=[dict(hospital='H02',accepted_at=.25,
            expires_at=2,accept_event_sequence=4)],demand={'ed_bed':1}),
        probe_asks=[handle('H01',3)],candidates=[dict(hospital=c['hospital'],resources=deepcopy(c['resources']))
            for c in value['candidates']])


def probe_case(seed=7):
    value=target(seed);value['id']=f'probe-unit-{seed}'
    value['patients']=value['patients'][:1]
    value['patients'][0]['required_capabilities']=[]
    for hospital in value['hospitals']:
        hospital['capabilities']=[]
        hospital['capacity']={'ed_bed':1}
    value['hospitals'][0]['initial_occupancy']=[dict(until=1,demand={'ed_bed':1})]
    value['hospitals'][1]['initial_occupancy']=[]
    value['inquiry_queue']['background_jobs']=[]
    value['response_matrix']={'*':{'H01':dict(delay=.75,silent=False),'H02':dict(delay=2.5,silent=False)}}
    value['observation_config']=dict(feed_interval_minutes=1,feed_delay_minutes=0,
        confirmation_delay_minutes=1,confirmation_silent=False,confirmation_missing_resources=[])
    return value


def probe(case=None,**kwargs):
    return run_shortage_probe_branch(probe_case() if case is None else case,shortage_probe=True,**kwargs)


def events(branch,kind):return [e for e in branch['ordered_emissions'] if e['type']==kind]


class ShortageProbeDecisionTests(unittest.TestCase):
    def test_current_full_bed_can_request_one_actual_recheck(self):
        value=snapshot();result=decide_shortage_probe(value)
        self.assertEqual(result['send'],['H01'])
        self.assertEqual(result['reason'],'CURRENT_SHORTAGE_PROBE')
        self.assertEqual(result['candidate_checks'][0],dict(hospital='H01',eligible=True,reason='RECHECK_ELIGIBLE'))

    def test_future_work_or_recovery_cannot_enter_exact_snapshot(self):
        for where,key,extra in (('top','future_release',1),('candidate','finish_t',1),
                ('patient','silent',False),('handle','at',1),('view','until',1)):
            value=snapshot()
            row=value if where=='top' else value['patient'] if where=='patient' else \
                value['candidates'][0] if where=='candidate' else value['patient']['pending_inquiries'][0] if where=='handle' \
                else value['candidates'][0]['resources']['ed_bed']
            row[key]=extra
            with self.assertRaises(ValueError):decide_shortage_probe(value)

    def test_boundary_requires_half_minute_since_first_and_latest_actual_contact(self):
        for changes in (dict(last_contact_at=.25),dict(first_ask=dict(handle(),submitted_at=.25))):
            value=snapshot();value['patient'].update(changes)
            self.assertEqual(decide_shortage_probe(value)['reason'],'TOO_EARLY')

    def test_patient_lifetime_state_and_contact_budget_are_not_reset(self):
        for field,entry,reason in (('state','TRANSIT','STATE'),('first_commit_t',0,'COMMITTED'),
                ('inquiry_episode',1,'EPISODE'),('inquiry_count',0,'NO_INITIAL_INQUIRY'),
                ('episode_contacts',10,'CONTACT_LIMIT'),('pending_inquiries',[],'NO_PENDING_INQUIRY')):
            value=snapshot();value['patient'][field]=entry
            self.assertEqual(decide_shortage_probe(value)['reason'],reason)
        value=snapshot();value['probe_count']=1
        self.assertEqual(decide_shortage_probe(value)['reason'],'ALREADY_PROBED')

    def test_three_open_real_handles_leave_no_room(self):
        value=snapshot();value['patient']['pending_inquiries']=[handle(h,i) for i,h in enumerate(('H02','H03','H04'),1)]
        self.assertEqual(decide_shortage_probe(value)['reason'],'CONCURRENCY_LIMIT')

    def test_fresh_sufficient_accepted_offer_suppresses_probe(self):
        value=snapshot();value['patient']['valid_offers']=[dict(hospital='H02',accepted_at=0,expires_at=2,accept_event_sequence=2)]
        self.assertEqual(decide_shortage_probe(value)['reason'],'USABLE_OFFER')

    def test_expired_offer_is_not_a_usable_offer(self):
        value=snapshot();value['patient']['valid_offers']=[dict(hospital='H01',accepted_at=0,expires_at=.5,accept_event_sequence=2)]
        self.assertEqual(decide_shortage_probe(value)['send'],['H01'])

    def test_other_patient_pending_blocks_even_lower_priority_or_multiple_options(self):
        value=snapshot();value['registered_search_patients'].append(dict(id='P02',state='SEARCH',request_t=0,
            request_event_sequence=5,pending_inquiries=[handle('H01',6)],valid_offers=[]))
        result=decide_shortage_probe(value)
        self.assertEqual(result['send'],[]);self.assertEqual(result['protected_hospitals'],['H01'])
        self.assertEqual(result['candidate_checks'][0]['reason'],'CLAIMED_BY_SEARCH_PATIENT')

    def test_other_current_offer_protects_its_hospital(self):
        value=snapshot();value['registered_search_patients'].append(dict(id='P02',state='SEARCH',request_t=0,
            request_event_sequence=5,pending_inquiries=[],valid_offers=[dict(hospital='H01',accepted_at=0,
                expires_at=1,accept_event_sequence=6)]))
        self.assertEqual(decide_shortage_probe(value)['candidate_checks'][0]['reason'],'CLAIMED_BY_SEARCH_PATIENT')

    def test_expired_other_pending_or_offer_does_not_create_phantom_claim(self):
        value=snapshot();value['registered_search_patients'].append(dict(id='P02',state='SEARCH',request_t=0,
            request_event_sequence=5,pending_inquiries=[dict(handle('H01',6),timeout_at=.5)],
            valid_offers=[dict(hospital='H01',accepted_at=0,expires_at=.5,accept_event_sequence=7)]))
        self.assertEqual(decide_shortage_probe(value)['send'],['H01'])

    def test_travel_limit_is_shortest_current_pending_plus_two_minutes(self):
        for minutes,eligible in ((7,True),(7.25,False)):
            value=snapshot();value['candidates'][0]['travel_minutes']=minutes
            self.assertEqual(bool(decide_shortage_probe(value)['send']),eligible)

    def test_reported_free_bed_is_an_ordinary_candidate_not_a_probe(self):
        value=snapshot();value['candidates'][0]['resources']['ed_bed']=view()
        self.assertEqual(decide_shortage_probe(value)['candidate_checks'][0]['reason'],'NO_REPORTED_BED_SHORTAGE')

    def test_structural_bed_capacity_must_support_own_demand(self):
        value=snapshot();value['patient']['demand']['ed_bed']=2
        self.assertEqual(decide_shortage_probe(value)['candidate_checks'][0]['reason'],'INSUFFICIENT_REPORTED_CAPACITY')

    def test_other_resource_shortage_is_never_bypassed(self):
        value=snapshot();value['patient']['demand']['ventilator']=1
        for c in value['candidates']:c['resources']['ventilator']=view(used=1)
        self.assertEqual(decide_shortage_probe(value)['candidate_checks'][0]['reason'],'OTHER_RESOURCE_SHORTAGE')

    def test_unknown_resource_cannot_be_promoted_to_fresh(self):
        value=snapshot();resource=value['candidates'][0]['resources']['ed_bed']
        resource.update(capacity=None,used=None,free=None,observed_at=None,received_at=None,sequence=None,
                        source=None,status='unknown',age_minutes=None)
        self.assertEqual(decide_shortage_probe(value)['candidate_checks'][0]['reason'],'UNKNOWN_RESOURCE')

    def test_old_report_is_not_a_probe_opportunity(self):
        value=snapshot();value['candidates'][0]['resources']['ed_bed']=view(used=1,age=11,status='stale')
        self.assertEqual(decide_shortage_probe(value)['candidate_checks'][0]['reason'],'STALE_RESOURCE')

    def test_first_probe_uses_shortest_eligible_hospital_and_stable_id_tie(self):
        value=snapshot();value['candidates'].append(dict(hospital='H00',travel_minutes=3,resources={'ed_bed':view(used=1)}))
        self.assertEqual(decide_shortage_probe(value)['send'],['H00'])

    def test_tried_hospital_cannot_be_retried_through_probe(self):
        value=snapshot();value['patient']['tried_hospitals'].append('H01')
        self.assertEqual(decide_shortage_probe(value)['candidate_checks'][0]['reason'],'ALREADY_TRIED')

    def test_policy_has_no_side_effect_on_public_snapshot(self):
        value=snapshot();original=deepcopy(value);result=decide_shortage_probe(value)
        result['send'].append('FAKE');self.assertEqual(value,original)

    def test_forged_resource_arithmetic_or_quality_is_rejected(self):
        for field,entry in (('free',2),('status','stale'),('received_at',1),('sequence',-1)):
            value=snapshot();value['candidates'][0]['resources']['ed_bed'][field]=entry
            with self.assertRaises(ValueError):decide_shortage_probe(value)

    def test_current_cohort_and_unique_actual_handles_are_required(self):
        for change in ('missing_self','duplicate','future_handle','bad_counter'):
            value=snapshot()
            if change=='missing_self':value['registered_search_patients']=[]
            elif change=='duplicate':value['patient']['pending_inquiries']*=2
            elif change=='future_handle':value['patient']['pending_inquiries'][0]['submitted_at']=1
            else:value['patient']['episode_contacts']=True
            with self.assertRaises(ValueError):decide_shortage_probe(value)


class ShortageProbeReplayTests(unittest.TestCase):
    def test_disabled_extension_preserves_frozen_safe_raw_outputs(self):
        case=target();old=run_deadline_safe_branch(case,late_reply_recovery=True,late_pending_guard=True,branch_id='same')
        new=run_shortage_probe_branch(case,branch_id='same')
        for key in ('patient_outcomes','ordered_emissions','events','inquiry_decisions','resource_snapshots'):
            self.assertEqual(new[key],old[key])
        self.assertNotIn('shortage_probe_enabled',new)

    def test_probe_is_a_real_ask_with_own_purpose_and_original_fifo_metadata(self):
        b=probe();asks=[e for e in events(b,'ask') if 'shortage_probe_review_event_sequence' in e]
        self.assertEqual(len(asks),1);ask=asks[0]
        self.assertEqual((ask['t'],ask['hospital'],ask['timeout_at']),(.5,'H01',3.5))
        check=next(e for e in events(b,'information_check') if e['purpose']=='shortage_probe')
        self.assertTrue(check['eligible']);self.assertEqual(check['reason'],'REPORTED_BED_SHORTAGE_RECHECK')
        self.assertEqual(check['resources']['ed_bed']['free'],0)
        record=next(r for r in b['shortage_probe_records'] if r['event_sequence']==ask['shortage_probe_review_event_sequence'])
        self.assertEqual(record['result']['send'],['H01'])
        work=next(e for e in events(b,'review_started') if e.get('hospital')=='H01' and e.get('patient')=='P01')
        self.assertEqual(work['t'],.5)

    def test_current_reply_and_fresh_resource_gate_precede_probe_commit(self):
        b=probe()
        commit=next(e for e in events(b,'commit') if e['patient']=='P01')
        self.assertTrue(any(e['patient']=='P01' and e['hospital']==commit['hospital']
            and e['event_sequence']<commit['event_sequence'] for e in events(b,'accept')))
        gate=[e for e in events(b,'information_check') if e['patient']=='P01' and e['purpose']=='allocation']
        self.assertTrue(gate);self.assertTrue(all(e['eligible'] and all(v['status']=='fresh' and v['free']>=1
            for v in e['resources'].values()) for e in gate))

    def test_actual_full_hospital_cannot_be_forced_to_accept(self):
        case=probe_case();case['hospitals'][0]['initial_occupancy'][0]['until']=30
        b=probe(case)
        self.assertTrue(any(e['hospital']=='H01' for e in events(b,'decline')))
        self.assertEqual(b['patient_outcomes'][0]['destination_hospital_id'],'H02')

    def test_original_one_minute_recovery_and_pending_clock_regressions_remain(self):
        for allocation in ('H1','C1'):
            b=probe(target(),allocation=allocation)
            self.assertEqual([p['treatment_start_t'] for p in b['patient_outcomes']],[8.25,3.5])
            case=multi_pending();old=run_shortage_probe_branch(case,allocation=allocation)
            new=probe(case,allocation=allocation)
            for earlier,later in zip(old['patient_outcomes'],new['patient_outcomes']):
                self.assertLessEqual(later['treatment_start_t'],earlier['treatment_start_t'])

    def test_actual_silent_probe_keeps_original_timeout(self):
        case=probe_case();case['response_matrix']['*']['H01']['silent']=True
        b=probe(case);ask=next(e for e in events(b,'ask') if 'shortage_probe_review_event_sequence' in e)
        feedback=next(e for e in events(b,'inquiry_feedback') if e['hospital']=='H01')
        self.assertEqual((ask['timeout_at'],feedback['t'],feedback['outcome']),(3.5,3.5,'timeout'))
        self.assertFalse(any(e['hospital']=='H01' for e in events(b,'accept')))

    def test_silent_probe_does_not_make_existing_usable_offer_wait_for_its_deadline(self):
        case=probe_case();case['response_matrix']['*']['H01']['silent']=True
        b=probe(case);ready=events(b,'shortage_probe_nonblocking_ready')
        self.assertEqual(len(ready),1);self.assertEqual((ready[0]['t'],ready[0]['options']),(2.5,['H02']))
        commit=next(e for e in events(b,'commit') if e['patient']=='P01')
        self.assertEqual((commit['t'],commit['hospital']),(2.5,'H02'))
        record=next(r for r in b['shortage_probe_nonblocking_records'] if r['result']['eligible'])
        self.assertEqual(len(record['snapshot']['patient']['pending_inquiries']),1)
        self.assertEqual(record['snapshot']['patient']['pending_inquiries'],record['snapshot']['probe_asks'])
        self.assertTrue(any(e['t']==3.5 and e['outcome']=='timeout' for e in events(b,'inquiry_feedback')))

    def test_at_most_one_probe_and_no_extra_resource_publication(self):
        b=probe();self.assertEqual(b['metrics']['shortage_probe_sent'],1)
        self.assertEqual(b['metrics']['resource_publications'],0)
        self.assertTrue(audit_motion(probe_case(),b))
        self.assertEqual(b['shortage_probe_spec'],SHORTAGE_PROBE_SPEC)

    def test_eight_default_branches_include_two_probes_and_preserve_order(self):
        self.assertEqual(len(DEFAULT_SHORTAGE_PROBE_POLICIES),8)
        self.assertEqual(sum(r['shortage_probe'] for r in DEFAULT_SHORTAGE_PROBE_POLICIES),2)
        specs=deepcopy(list(DEFAULT_SHORTAGE_PROBE_POLICIES[:3]));order=[r['id'] for r in reversed(specs)]
        result=run_shortage_probe_replay(probe_case(),specs,order)
        self.assertEqual([b['id'] for b in result['branches']],[r['id'] for r in specs])

    def test_inputs_and_policy_rows_are_not_mutated(self):
        case=probe_case();specs=deepcopy(list(DEFAULT_SHORTAGE_PROBE_POLICIES[:3]));old=deepcopy((case,specs))
        run_shortage_probe_replay(case,specs);self.assertEqual((case,specs),old)

    def test_probe_requires_the_actual_safe_inquiry_stack(self):
        for changes in (dict(shortage_probe=1),dict(shortage_probe=True,late_pending_guard=False),
                dict(shortage_probe=True,inquiry='fixed_3'),dict(shortage_probe=True,scarce_routing=False),
                dict(shortage_probe=True,cohort_registration=False),dict(shortage_probe=True,late_reply_recovery=False)):
            with self.assertRaises(ValueError):run_shortage_probe_branch(probe_case(),**changes)

    def test_a_future_single_candidate_patient_keeps_the_real_fifo_cost(self):
        case=make_probe_scenario(7,'shortage_probe_later_patient_fifo')
        for rule in ('H1','C1'):
            old=run_shortage_probe_branch(case,allocation=rule);new=probe(case,allocation=rule)
            self.assertEqual([p['treatment_start_t'] for p in old['patient_outcomes']],[8.75,4.25])
            self.assertEqual([p['treatment_start_t'] for p in new['patient_outcomes']],[7.5,6.25])
            first=next(e for e in events(new,'review_started') if e.get('patient')=='P01' and e['hospital']=='H01')
            later=next(e for e in events(new,'review_started') if e.get('patient')=='P02' and e['hospital']=='H01')
            self.assertLess(first['t'],later['t'])
            self.assertEqual(first['t'],.5);self.assertEqual(later['t'],3)

    def test_hidden_background_work_can_make_a_real_probe_worse(self):
        case=make_probe_scenario(7,'shortage_probe_late_reply')
        old=run_shortage_probe_branch(case);new=probe(case)
        self.assertEqual((old['patient_outcomes'][0]['treatment_start_t'],new['patient_outcomes'][0]['treatment_start_t']),(6.75,7.5))
        asks=[e for e in events(new,'ask') if 'shortage_probe_review_event_sequence' in e]
        self.assertEqual(len(asks),1)
        self.assertEqual((asks[0]['t'],asks[0]['timeout_at']),(.5,3.5))
        self.assertFalse(any(e['hospital']=='H01' for e in events(new,'accept')))

    def test_existing_other_patient_pending_blocks_actual_probe(self):
        case=make_probe_scenario(7,'shortage_probe_other_patient_pending')
        old=run_shortage_probe_branch(case);new=probe(case)
        self.assertEqual(new['metrics']['shortage_probe_sent'],0)
        self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
        self.assertTrue(any(c['reason']=='CLAIMED_BY_SEARCH_PATIENT' for record in new['shortage_probe_records']
            for c in record['result']['candidate_checks']))

    def test_stale_missing_and_other_resource_shortages_never_submit_probe(self):
        for family in ('shortage_probe_stale_report','shortage_probe_missing_other_resource','shortage_probe_other_resource_shortage'):
            case=make_probe_scenario(7,family);old=run_shortage_probe_branch(case);new=probe(case)
            self.assertEqual(new['metrics']['shortage_probe_sent'],0)
            self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])

    def test_real_probe_only_pending_still_blocks_optional_initial_supplement_callback(self):
        # New optional-work readiness does not secretly weaken the frozen late
        # receipt guard. A real probe remains an actual pending inquiry there.
        case=target();hospital=deepcopy(case['hospitals'][1])
        hospital.update(id='H03',initial_occupancy=[dict(until=1,demand={'ed_bed':1})])
        case['hospitals'].append(hospital)
        case['response_matrix']['*']['H03']=dict(delay=3.5,silent=False)
        for rule in ('H1','C1'):
            b=probe(case,allocation=rule);record=b['late_offer_records'][0]
            self.assertEqual(record['snapshot']['t'],3.25)
            self.assertEqual(record['result']['reason'],'PENDING_INQUIRY')
            pending=record['snapshot']['patient']['pending_inquiries']
            self.assertEqual(len(pending),1)
            self.assertEqual(pending[0]['hospital'],'H03')
            ask=next(e for e in events(b,'ask') if e['event_sequence']==pending[0]['ask_event_sequence'])
            self.assertIn('shortage_probe_review_event_sequence',ask)
            self.assertFalse(events(b,'late_offer_accepted'))
            self.assertEqual([p['treatment_start_t'] for p in b['patient_outcomes']],[9.25,3.5])


class NonblockingProbeDecisionTests(unittest.TestCase):
    def test_actual_optional_handles_do_not_block_a_usable_received_offer(self):
        value=nonblocking_snapshot()
        self.assertEqual(decide_nonblocking_probe_ready(value),dict(eligible=True,options=['H02'],reason='OPTIONAL_PROBE_NONBLOCKING'))

    def test_original_ordinary_pending_inquiry_still_controls_readiness(self):
        value=nonblocking_snapshot();value['patient']['pending_inquiries'].append(handle('H03',5))
        self.assertEqual(decide_nonblocking_probe_ready(value)['reason'],'ORDINARY_INQUIRY_PENDING')

    def test_probe_origin_is_exact_actual_public_handle_not_just_hospital_name(self):
        for field,entry in (('attempt',2),('ask_event_sequence',8),('submitted_at',.25),('timeout_at',4)):
            value=nonblocking_snapshot();value['probe_asks'][0][field]=entry
            self.assertEqual(decide_nonblocking_probe_ready(value)['reason'],'ORDINARY_INQUIRY_PENDING')

    def test_expired_offer_cannot_become_ready(self):
        value=nonblocking_snapshot();value['patient']['valid_offers'][0]['expires_at']=.5
        self.assertFalse(decide_nonblocking_probe_ready(value)['eligible'])

    def test_fresh_shortage_still_cannot_enter_an_allocation(self):
        value=nonblocking_snapshot();value['candidates'][1]['resources']['ed_bed']=view(used=1)
        self.assertFalse(decide_nonblocking_probe_ready(value)['eligible'])

    def test_stale_offer_resources_still_require_actual_confirmation(self):
        value=nonblocking_snapshot();value['candidates'][1]['resources']['ed_bed']=view(age=11,status='stale')
        self.assertFalse(decide_nonblocking_probe_ready(value)['eligible'])

    def test_no_pending_work_needs_no_optional_readiness_exception(self):
        value=nonblocking_snapshot();value['patient']['pending_inquiries']=[]
        self.assertEqual(decide_nonblocking_probe_ready(value)['reason'],'NO_PENDING_INQUIRY')

    def test_previously_committed_patient_cannot_change_initial_allocation(self):
        value=nonblocking_snapshot();value['patient']['first_commit_t']=0
        self.assertEqual(decide_nonblocking_probe_ready(value)['reason'],'COMMITTED')

    def test_nonblocking_input_preserves_hidden_information_boundary(self):
        value=nonblocking_snapshot();value['probe_asks'][0]['finish_t']=3
        with self.assertRaises(ValueError):decide_nonblocking_probe_ready(value)

    def test_nonblocking_ready_preserves_every_original_pending_handle(self):
        value=nonblocking_snapshot();old=deepcopy(value);decide_nonblocking_probe_ready(value)
        self.assertEqual(value,old)


if __name__=='__main__':unittest.main()
