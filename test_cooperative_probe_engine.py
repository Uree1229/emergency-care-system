"""Online work conservation, public ACK boundaries and actual recovery cases."""
from copy import deepcopy
import json
import unittest

from simulation.cooperative_probe_engine import (
    CooperativeInquiryQueue,DEFAULT_COOPERATIVE_PROBE_POLICIES,COOPERATIVE_PROBE_SPEC,
    COOPERATIVE_SHORTAGE_SPEC,decide_cooperative_ready,decide_busy_report_retry,
    run_cooperative_probe_branch,run_cooperative_probe_replay,
)
from simulation.shortage_probe_engine import run_shortage_probe_branch
from simulation.shortage_probe_scenarios import make_scenario as make_previous
from simulation.cooperative_probe_scenarios import make_scenario,FAMILIES
from test_shortage_probe_engine import nonblocking_snapshot,snapshot as opportunity_snapshot
from test_deadline_safe_engine import target


def cooperative(case,**kwargs):
    return run_cooperative_probe_branch(case,shortage_probe=True,cooperative_probe=True,**kwargs)


def events(branch,kind):return [e for e in branch['ordered_emissions'] if e['type']==kind]


def ready_snapshot(state='completed'):
    return dict(base_snapshot=nonblocking_snapshot(),control_acknowledgements=[dict(
        hospital='H01',attempt=1,ask_event_sequence=3,job_id=1,state=state,
        received_at=.5,control_event_sequence=5)])


def retry_snapshot():
    value=opportunity_snapshot();value['probe_count']=1
    value['candidates'][0]['resources']['ed_bed'].update(used=0,free=1,
        observed_at=.5,received_at=.5,age_minutes=0,sequence=2,source='feed')
    return dict(opportunity_snapshot=value,not_admitted_ack=dict(hospital='H01',attempt=1,
        ask_event_sequence=3,job_id=None,state='not_admitted',received_at=.25,control_event_sequence=4),
        retry_count=0,ordinary_selector_event_sequence=6,offered_hospitals=['H01'],ordinary_send=[],max_new=2)


class CooperativeQueueTests(unittest.TestCase):
    def test_optional_work_is_admitted_only_to_a_currently_idle_worker(self):
        q=CooperativeInquiryQueue(['H1']);normal=q.enqueue('H1',0,'A',1,1,clinical_attempt=1)
        self.assertFalse(q.can_admit_probe('H1'))
        self.assertIsNone(q.enqueue('H1',0,'B',1,1,work_class='probe',clinical_attempt=1))
        self.assertEqual(len(q.jobs),1);self.assertEqual(normal['state'],'running')

    def test_actual_normal_arrival_preempts_optional_work_and_resumes_remaining_work(self):
        emitted=[];q=CooperativeInquiryQueue(['H1'],emit=lambda t,typ,**x:emitted.append(dict(t=t,type=typ,**x)))
        p=q.enqueue('H1',0,'P',1,1,work_class='probe',clinical_attempt=1);q.advance(.25)
        n=q.enqueue('H1',.25,'N',1,.25,clinical_attempt=1);q.advance(.5)
        self.assertEqual(n['finish_t'],.5);self.assertEqual(p['remaining_minutes'],.75)
        for t in (.75,1,1.25):q.advance(t)
        self.assertEqual(p['finish_t'],1.25);self.assertEqual(p['elapsed_minutes'],1)
        self.assertEqual(p['segments'],[dict(start_t=0,end_t=.25,worker=0),dict(start_t=.5,end_t=1.25,worker=0)])
        self.assertEqual([e['type'] for e in emitted].count('cooperative_job_paused'),1)

    def test_multiple_normal_jobs_keep_their_actual_fifo_order(self):
        q=CooperativeInquiryQueue(['H1']);p=q.enqueue('H1',0,'P',1,2,work_class='probe')
        q.advance(.25);a=q.enqueue('H1',.25,'A',1,.5);b=q.enqueue('H1',.25,'B',1,.25)
        for t in (.5,.75,1):q.advance(t)
        self.assertEqual((a['start_t'],a['finish_t'],b['start_t'],b['finish_t']),(.25,.75,.75,1))
        self.assertEqual(p['state'],'running');self.assertEqual(p['elapsed_minutes'],.25)

    def test_existing_normal_work_is_never_preempted(self):
        q=CooperativeInquiryQueue(['H1']);a=q.enqueue('H1',0,'A',1,1);q.advance(.25)
        b=q.enqueue('H1',.25,'B',1,.25)
        self.assertEqual(a['state'],'running');self.assertEqual(b['state'],'queued')
        self.assertEqual(q.snapshot(.25)[0]['service_total_minutes'],.25)

    def test_another_idle_worker_can_accept_optional_work(self):
        q=CooperativeInquiryQueue(['H1'],workers=2);q.enqueue('H1',0,None,0,3,kind='background')
        p=q.enqueue('H1',0,'P',1,.5,work_class='probe')
        self.assertEqual(p['segments'][0]['worker'],1);self.assertEqual(q.workers,2)

    def test_no_future_arrival_or_future_completion_is_consulted(self):
        q=CooperativeInquiryQueue(['H1']);q.enqueue('H1',0,'P',1,2,work_class='probe')
        self.assertEqual(q.snapshot(0)[0]['processing'],1)
        with self.assertRaises(ValueError):q.snapshot(.25)
        with self.assertRaises(ValueError):q.advance(1)

    def test_service_and_queue_wait_include_real_pauses(self):
        q=CooperativeInquiryQueue(['H1']);q.enqueue('H1',0,'P',1,1,work_class='probe');q.advance(.25)
        q.enqueue('H1',.25,'N',1,.5)
        for t in (.5,.75,1,1.25,1.5):q.advance(t)
        report=q.report(1.5)['aggregate']
        self.assertEqual(report['service_total_minutes'],1.5)
        self.assertEqual(report['queue_wait_total_minutes'],.5)

    def test_raw_ledger_uses_only_actual_start_and_finish_times(self):
        q=CooperativeInquiryQueue(['H1']);q.enqueue('H1',0,'P',1,2)
        q.enqueue('H1',0,'Q',1,1);raw=q.ledger()
        self.assertEqual(raw[0]['start_t'],0);self.assertIsNone(raw[0]['finish_t'])
        self.assertIsNone(raw[1]['start_t']);self.assertIsNone(raw[1]['finish_t'])
        json.dumps(raw,allow_nan=False)


class CooperativeReadyTests(unittest.TestCase):
    def test_completed_ack_preserves_an_existing_usable_offer(self):
        self.assertEqual(decide_cooperative_ready(ready_snapshot()),dict(eligible=True,options=['H02'],reason='OPTIONAL_PROBE_NONBLOCKING'))

    def test_active_or_queued_optional_work_is_not_assumed_silent(self):
        for state in ('running','queued'):
            self.assertEqual(decide_cooperative_ready(ready_snapshot(state))['reason'],'PROBE_WORK_PENDING')

    def test_clinical_acceptance_still_cannot_be_fabricated_by_an_ack(self):
        value=ready_snapshot();value['base_snapshot']['patient']['valid_offers']=[]
        self.assertFalse(decide_cooperative_ready(value)['eligible'])

    def test_completed_ack_does_not_bypass_an_ordinary_pending_inquiry(self):
        value=ready_snapshot();value['base_snapshot']['patient']['pending_inquiries'].append(
            dict(hospital='H03',attempt=1,submitted_at=0,timeout_at=3,ask_event_sequence=6))
        self.assertEqual(decide_cooperative_ready(value)['reason'],'ORDINARY_INQUIRY_PENDING')

    def test_hidden_finish_and_remaining_service_cannot_enter_public_ack_input(self):
        for name in ('finish_t','due_at','remaining_minutes','service_minutes','silent'):
            value=ready_snapshot();value['control_acknowledgements'][0][name]=1
            with self.assertRaises(ValueError):decide_cooperative_ready(value)

    def test_actual_provenance_and_current_receipt_time_are_required(self):
        for change in (dict(received_at=.75),dict(control_event_sequence=2),dict(job_id=None),dict(state='predicted')):
            value=ready_snapshot();value['control_acknowledgements'][0].update(change)
            with self.assertRaises(ValueError):decide_cooperative_ready(value)

    def test_missing_or_duplicate_ack_is_not_assumed_completed(self):
        for acks in ([],ready_snapshot()['control_acknowledgements']*2):
            value=ready_snapshot();value['control_acknowledgements']=acks
            with self.assertRaises(ValueError):decide_cooperative_ready(value)

    def test_status_decision_does_not_mutate_original_public_handles(self):
        value=ready_snapshot();before=deepcopy(value);decide_cooperative_ready(value)
        self.assertEqual(value,before)


class BusyReportRetryTests(unittest.TestCase):
    def test_actual_new_sufficient_report_can_create_one_explicit_normal_request(self):
        self.assertEqual(decide_busy_report_retry(retry_snapshot()),dict(send=['H01'],reason='NONADMITTED_NEW_FRESH_REPORT_NORMAL_RETRY'))

    def test_old_report_or_new_shortage_does_not_override_actual_selector(self):
        for change in ('old','shortage'):
            value=retry_snapshot();view=value['opportunity_snapshot']['candidates'][0]['resources']['ed_bed']
            if change=='old':view.update(observed_at=0,received_at=0,age_minutes=.5)
            else:view.update(used=1,free=0)
            self.assertEqual(decide_busy_report_retry(value)['send'],[])

    def test_already_sent_normal_selector_is_preserved(self):
        value=retry_snapshot();value['ordinary_send']=['H01']
        self.assertEqual(decide_busy_report_retry(value)['reason'],'NORMAL_SELECTOR_ALREADY_SENT')

    def test_retry_is_bounded_and_uses_the_actual_original_budget(self):
        for changes in (dict(retry_count=1),dict(max_new=0)):
            value=retry_snapshot();value.update(changes)
            self.assertEqual(decide_busy_report_retry(value)['send'],[])
        for changes in (dict(episode_contacts=10),dict(first_commit_t=0),dict(inquiry_episode=1)):
            value=retry_snapshot();value['opportunity_snapshot']['patient'].update(changes)
            self.assertEqual(decide_busy_report_retry(value)['send'],[])

    def test_retry_must_be_an_original_actual_selector_candidate(self):
        value=retry_snapshot();value['offered_hospitals']=[]
        self.assertEqual(decide_busy_report_retry(value)['reason'],'NOT_CURRENT_NORMAL_CANDIDATE')

    def test_current_other_patient_claim_is_not_overridden(self):
        value=retry_snapshot();value['opportunity_snapshot']['registered_search_patients'].append(
            dict(id='P02',state='SEARCH',request_t=0,request_event_sequence=7,pending_inquiries=[dict(
                hospital='H01',attempt=1,submitted_at=0,timeout_at=3,ask_event_sequence=8)],valid_offers=[]))
        self.assertEqual(decide_busy_report_retry(value)['reason'],'OTHER_CURRENT_SEARCH_CLAIM')

    def test_fake_busy_or_future_received_ack_is_rejected(self):
        for change in (dict(state='completed'),dict(job_id=1),dict(received_at=.75)):
            value=retry_snapshot();value['not_admitted_ack'].update(change)
            with self.assertRaises(ValueError):decide_busy_report_retry(value)

    def test_retry_leaves_the_actual_selector_input_and_contact_time_unchanged(self):
        value=retry_snapshot();before=deepcopy(value);decide_busy_report_retry(value)
        self.assertEqual(value,before)


class CooperativeReplayTests(unittest.TestCase):
    def test_previous_eight_branches_remain_exact_when_new_workflow_is_disabled(self):
        case=make_previous(7,'shortage_probe_later_patient_fifo')
        for flag in (False,True):
            old=run_shortage_probe_branch(case,shortage_probe=flag,branch_id='same')
            new=run_cooperative_probe_branch(case,shortage_probe=flag,branch_id='same')
            for key in ('patient_outcomes','ordered_emissions','inquiry_snapshots','resource_snapshots'):
                self.assertEqual(old[key],new[key])
            self.assertNotIn('cooperative_probe_enabled',new)

    def test_busy_queue_does_not_consume_a_normal_candidate_or_clinical_work_sample(self):
        case=make_previous(7,'shortage_probe_late_reply');b=cooperative(case)
        self.assertEqual(b['patient_outcomes'][0]['treatment_start_t'],6.75)
        ask=[e for e in events(b,'ask') if e['hospital']=='H01']
        self.assertEqual([(e['t'],e['attempt'],e['clinical_attempt']) for e in ask],[(.5,1,None),(1,2,1)])
        self.assertEqual(ask[0]['timeout_at'],3.5)
        self.assertEqual([a['state'] for a in b['cooperative_control_acks']],['not_admitted'])
        self.assertEqual(b['metrics']['cooperative_busy_retries'],0)
        self.assertFalse(any(e['hospital']=='H01' and e.get('submitted_t')==.5 for e in events(b,'inquiry_feedback')))

    def test_busy_admission_does_not_skip_a_fast_first_clinical_attempt(self):
        case=make_previous(7,'shortage_probe_late_reply')
        case['response_matrix']['*']['H01']={'attempts':[dict(delay=.25,silent=False),dict(delay=.75,silent=True)]}
        b=cooperative(case);h01=[j for j in b['cooperative_job_ledger'] if j['hospital']=='H01' and j['patient']=='P01']
        self.assertEqual(len(h01),1);self.assertEqual((h01[0]['attempt'],h01[0]['clinical_attempt']),(2,1))
        self.assertEqual(h01[0]['service_minutes'],.25);self.assertFalse(h01[0]['silent'])

    def test_actual_new_normal_request_recovers_the_two_minute_fifo_regression(self):
        case=make_previous(7,'shortage_probe_later_patient_fifo')
        for rule in ('H1','C1'):
            b=cooperative(case,allocation=rule)
            self.assertEqual([p['treatment_start_t'] for p in b['patient_outcomes']],[8.25,4.25])
            self.assertEqual(b['metrics']['cooperative_preemptions'],1)
            p=next(j for j in b['cooperative_job_ledger'] if j['patient']=='P01' and j['hospital']=='H01')
            self.assertEqual(p['elapsed_minutes'],p['service_minutes'])
            self.assertEqual(p['elapsed_minutes'],2.5)
            self.assertEqual(p['segments'],[dict(start_t=.5,end_t=1,worker=0),dict(start_t=1.25,end_t=3.25,worker=0)])

    def test_active_slow_probe_is_waited_for_without_guessing_it_will_be_silent(self):
        case=make_scenario(7,'cooperative_probe_active_reply_300');b=cooperative(case)
        self.assertEqual(b['patient_outcomes'][0]['treatment_start_t'],6.5)
        self.assertTrue(any(r['result']['reason']=='PROBE_WORK_PENDING' for r in b['cooperative_ready_records']))

    def test_silent_completed_work_preserves_the_previous_nonblocking_benefit(self):
        b=cooperative(make_previous(7,'shortage_probe_silent'))
        self.assertEqual(b['patient_outcomes'][0]['treatment_start_t'],7.5)
        self.assertTrue(any(a['state']=='completed' for a in b['cooperative_control_acks']))
        self.assertFalse(any(e['hospital']=='H01' for e in events(b,'accept')))
        self.assertEqual(events(b,'cooperative_probe_nonblocking_ready')[0]['t'],2.5)

    def test_release_and_short_ttl_improvements_are_preserved(self):
        for family in ('shortage_probe_release_before_reply','shortage_probe_short_ttl'):
            b=cooperative(make_previous(7,family));self.assertEqual(b['patient_outcomes'][0]['treatment_start_t'],4.25)

    def test_new_busy_report_retry_preserves_actual_clock_and_original_selector_decision(self):
        case=make_scenario(7,'cooperative_probe_busy_early_report')
        for background in case['inquiry_queue']['background_jobs']:background['service_minutes']=1
        case['hospitals'][0]['lat']=case['hospitals'][1]['lat']
        b=cooperative(case);self.assertEqual(b['patient_outcomes'][0]['treatment_start_t'],4.75)
        selector=next(r for r in b['inquiry_decisions'] if r['t']==1 and r['patient']=='P01')
        self.assertEqual(selector['inputs']['last_contact_at'],86445)
        self.assertEqual(selector['decision']['send'],[])
        retried=next(e for e in events(b,'ask') if 'cooperative_busy_retry_review_event_sequence' in e)
        self.assertEqual((retried['t'],retried['hospital'],retried['attempt'],retried['clinical_attempt']),(1,'H03',2,1))

    def test_original_six_deadline_recoveries_are_preserved(self):
        for rule in ('H1','C1'):
            self.assertEqual([p['treatment_start_t'] for p in cooperative(target(),allocation=rule)['patient_outcomes']],[8.25,3.5])

    def test_queue_disabled_environments_invent_neither_workers_nor_ack(self):
        case=make_scenario(7,'fresh_control');b=cooperative(case)
        self.assertFalse(b['cooperative_queue_enabled']);self.assertEqual(b['cooperative_job_ledger'],[])
        self.assertEqual(b['cooperative_control_acks'],[]);self.assertEqual(b['metrics']['shortage_probe_sent'],0)
        old=run_cooperative_probe_branch(case)
        self.assertEqual(old['patient_outcomes'],b['patient_outcomes'])

    def test_private_work_data_is_absent_from_every_public_ack(self):
        b=cooperative(make_scenario(7,'cooperative_probe_pause_resume'))
        forbidden={'service_minutes','remaining_minutes','finish_t','start_t','silent','worker'}
        self.assertTrue(b['cooperative_control_acks'])
        for ack in b['cooperative_control_acks']:self.assertFalse(set(ack)&forbidden)
        json.dumps(b,allow_nan=False)

    def test_workflow_metadata_discloses_changed_calendar_and_readiness_contract(self):
        b=cooperative(make_scenario(7,'cooperative_probe_pause_resume'))
        self.assertEqual(b['cooperative_probe_spec'],COOPERATIVE_PROBE_SPEC)
        self.assertEqual(b['shortage_probe_spec'],COOPERATIVE_SHORTAGE_SPEC)
        self.assertFalse(b['deadline_recovery_spec']['hospital_work_calendar_unchanged'])
        self.assertEqual(b['metrics']['resource_publications'],0)

    def test_ten_branch_api_keeps_old_eight_and_canonical_order(self):
        self.assertEqual(len(DEFAULT_COOPERATIVE_PROBE_POLICIES),10)
        self.assertEqual(sum(p['cooperative_probe'] for p in DEFAULT_COOPERATIVE_PROBE_POLICIES),2)
        specs=deepcopy(list(DEFAULT_COOPERATIVE_PROBE_POLICIES[8:]));case=make_previous(7,'shortage_probe_silent')
        result=run_cooperative_probe_replay(case,specs,list(reversed([p['id'] for p in specs])))
        self.assertEqual([b['id'] for b in result['branches']],[p['id'] for p in specs])

    def test_inputs_and_explicit_flags_cannot_be_mutated_or_bypassed(self):
        case=make_previous(7,'shortage_probe_silent');before=deepcopy(case);cooperative(case)
        self.assertEqual(case,before)
        for changes in (dict(cooperative_probe=1),dict(cooperative_probe=True),
                dict(cooperative_probe=True,shortage_probe=True,late_pending_guard=False)):
            with self.assertRaises(ValueError):run_cooperative_probe_branch(case,**changes)


if __name__=='__main__':unittest.main()
