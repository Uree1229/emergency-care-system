"""Adversarial clinical/control/work separation; structural seeds 7 and 8."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest

from simulation import cooperative_probe_audit as audit
from simulation import shortage_probe_audit as frozen
from simulation.cooperative_probe_engine import run_cooperative_probe_branch,run_cooperative_probe_replay
from simulation.cooperative_probe_scenarios import make_scenario


class CooperativeProbeRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={}
        for family in ('shortage_probe_later_patient_fifo','shortage_probe_late_reply','shortage_probe_silent',
            'cooperative_probe_active_reply_300','cooperative_probe_busy_early_report','cooperative_probe_busy_attempt_vary'):
            scenario=make_scenario(7,family)
            branch=run_cooperative_probe_branch(scenario,shortage_probe=True,cooperative_probe=True)
            cls.cases[family]=(scenario,branch)

    def fixture(self,family='shortage_probe_later_patient_fifo'):
        return deepcopy(self.cases[family])

    def reject(self,scenario,branch):
        with self.assertRaises((audit.CooperativeProbeAuditError,KeyError,IndexError,TypeError,ValueError)):
            audit.audit_branch(scenario,branch)

    def event(self,branch,kind,*,match=None,**changes):
        row=next(row for row in branch['ordered_emissions'] if row['type']==kind and (match is None or match(row)))
        seq=row['event_sequence'];row.update(deepcopy(changes))
        next(value for value in branch['events'] if value.get('event_sequence')==seq).update(deepcopy(changes))
        return row

    def control(self,branch,*,match=None,**changes):
        row=self.event(branch,'cooperative_probe_control_ack',match=match,**changes)
        next(value for value in branch['cooperative_control_acks'] if value['event_sequence']==row['event_sequence']).update(deepcopy(changes))
        return row

    def test_real_pause_resume_conserves_all_original_work_and_normal_patient_time(self):
        scenario,branch=self.fixture()
        checked=audit.audit_branch(scenario,branch)
        self.assertTrue(checked['passed']);self.assertEqual(checked['counts']['cooperative_preemptions'],1)
        self.assertEqual(checked['counts']['cooperative_resumptions'],1)
        probe=next(job for job in branch['cooperative_job_ledger'] if job['work_class']=='probe')
        self.assertEqual(probe['service_minutes'],2.5)
        self.assertEqual(probe['segments'],[dict(start_t=.5,end_t=1,worker=0),dict(start_t=1.25,end_t=3.25,worker=0)])
        self.assertEqual([row['treatment_start_t'] for row in branch['patient_outcomes']],[8.25,4.25])

    def test_busy_control_is_not_a_clinical_reply_and_keeps_original_timeout_metadata(self):
        scenario,branch=self.fixture('shortage_probe_late_reply')
        checked=audit.audit_branch(scenario,branch)
        self.assertEqual(checked['counts']['cooperative_admission_refusals'],1)
        refused=next(row for row in branch['ordered_emissions'] if row['type']=='ask' and row.get('cooperative_job_id') is None)
        self.assertIsNone(refused['clinical_attempt']);self.assertEqual(refused['timeout_at'],3.5)
        closure=next(row for row in branch['ordered_emissions'] if row['type']=='cooperative_probe_not_admitted')
        self.assertEqual(closure['original_timeout_at'],refused['timeout_at'])
        self.assertEqual(branch['patient_outcomes'][0]['treatment_start_t'],6.75)

    def test_completed_silent_work_can_release_optional_wait_without_fabricating_acceptance(self):
        scenario,branch=self.fixture('shortage_probe_silent')
        checked=audit.audit_branch(scenario,branch)
        self.assertEqual(checked['counts']['cooperative_nonblocking_ready'],1)
        self.assertEqual(branch['patient_outcomes'][0]['treatment_start_t'],7.5)
        self.assertFalse(any(row['type']=='accept' and row.get('hospital')=='H01' for row in branch['ordered_emissions']))

    def test_active_optional_work_does_not_become_completed_control(self):
        scenario,branch=self.fixture('cooperative_probe_active_reply_300')
        self.assertTrue(audit.audit_branch(scenario,branch)['passed'])
        self.assertEqual(branch['cooperative_ready_records'][0]['result']['reason'],'PROBE_WORK_PENDING')

    def test_busy_control_consumes_contact_but_not_first_clinical_review(self):
        scenario,branch=self.fixture('cooperative_probe_busy_attempt_vary')
        self.assertTrue(audit.audit_branch(scenario,branch)['passed'])
        normal=next(row for row in branch['ordered_emissions'] if row['type']=='ask'
            and row.get('hospital')=='H01' and row.get('cooperative_job_id') is not None)
        self.assertEqual(normal['attempt'],2);self.assertEqual(normal['clinical_attempt'],1)

    def test_fresh_report_exception_keeps_actual_busy_contact_time_and_original_selector(self):
        scenario,branch=self.fixture('cooperative_probe_busy_early_report')
        checked=audit.audit_branch(scenario,branch)
        self.assertEqual(checked['counts']['cooperative_busy_retries'],1)
        record=next(row for row in branch['cooperative_busy_retry_records'] if row['result']['send'])
        self.assertEqual(record['snapshot']['opportunity_snapshot']['patient']['last_contact_at'],.75)
        original=next(row for row in branch['inquiry_decisions']
            if row['event_sequence']==record['snapshot']['ordinary_selector_event_sequence'])
        self.assertEqual(original['inputs']['last_contact_at'],86445)
        self.assertEqual(original['decision']['send'],[])

    def test_all_ten_branches_pass_with_the_eight_frozen_references(self):
        result=run_cooperative_probe_replay(make_scenario(8,'shortage_probe_release_before_reply'))
        self.assertEqual(audit.audit_cooperative_probe_replay(result)['branches'],10)
        old=next(branch for branch in result['branches'] if branch['id']=='v4_H1_probe')
        self.assertEqual(audit.audit_branch(result['scenario'],deepcopy(old)),frozen.audit_branch(result['scenario'],deepcopy(old)))

    def test_queue_disabled_environment_gets_no_invented_worker_or_control_channel(self):
        scenario=make_scenario(8,'fresh_control')
        branch=run_cooperative_probe_branch(scenario,shortage_probe=True,cooperative_probe=True)
        self.assertFalse(branch['cooperative_queue_enabled'])
        self.assertEqual(audit.audit_branch(scenario,branch)['counts']['cooperative_jobs'],0)

    def test_elapsed_work_cannot_disappear_at_preemption(self):
        scenario,branch=self.fixture();self.event(branch,'cooperative_job_paused',elapsed_minutes=0,remaining_minutes=2.5)
        self.reject(scenario,branch)

    def test_resume_cannot_reset_remaining_work(self):
        scenario,branch=self.fixture();self.event(branch,'cooperative_job_resumed',elapsed_minutes=0,remaining_minutes=2.5)
        self.reject(scenario,branch)

    def test_completion_cannot_discard_remaining_work(self):
        scenario,branch=self.fixture();self.event(branch,'cooperative_job_completed',match=lambda row:row['work_class']=='probe',remaining_minutes=.25)
        self.reject(scenario,branch)

    def test_service_quantity_stays_the_original_clinical_environment(self):
        scenario,branch=self.fixture();self.event(branch,'cooperative_job_submitted',match=lambda row:row['work_class']=='probe',service_minutes=1)
        self.reject(scenario,branch)

    def test_no_extra_worker_can_be_added(self):
        scenario,branch=self.fixture();self.event(branch,'cooperative_job_resumed',worker=1)
        self.reject(scenario,branch)

    def test_normal_work_cannot_be_labelled_optional(self):
        scenario,branch=self.fixture();self.event(branch,'cooperative_job_submitted',match=lambda row:row['patient']=='P02',work_class='probe')
        self.reject(scenario,branch)

    def test_same_physical_job_cannot_be_attached_to_two_actual_contacts(self):
        scenario,branch=self.fixture();asks=[row for row in branch['ordered_emissions'] if row['type']=='ask']
        self.event(branch,'ask',match=lambda row:row['event_sequence']==asks[-1]['event_sequence'],cooperative_job_id=asks[0]['cooperative_job_id'])
        self.reject(scenario,branch)

    def test_final_private_ledger_cannot_remove_a_pause(self):
        scenario,branch=self.fixture();next(job for job in branch['cooperative_job_ledger'] if job['work_class']=='probe')['segments'][0]['end_t']=3
        self.reject(scenario,branch)

    def test_queue_aggregate_cannot_hide_optional_work(self):
        scenario,branch=self.fixture();branch['inquiry_queue_report']['aggregate']['service_total_minutes']-=.5
        self.reject(scenario,branch)

    def test_legacy_original_clinical_start_is_not_moved_to_resume(self):
        scenario,branch=self.fixture();self.event(branch,'review_started',match=lambda row:row.get('hospital')=='H01' and row.get('patient')=='P01',attempt=99)
        self.reject(scenario,branch)

    def test_running_ack_cannot_be_forged_as_completed_even_in_both_exports(self):
        scenario,branch=self.fixture();self.control(branch,match=lambda row:row['state']=='running',state='completed')
        self.reject(scenario,branch)

    def test_pause_ack_cannot_claim_work_is_still_running(self):
        scenario,branch=self.fixture();self.control(branch,match=lambda row:row['state']=='queued',state='running')
        self.reject(scenario,branch)

    def test_control_ack_cannot_include_future_completion(self):
        scenario,branch=self.fixture();self.control(branch,finish_t=3.25)
        self.reject(scenario,branch)

    def test_control_ack_cannot_reveal_remaining_private_work(self):
        scenario,branch=self.fixture();self.control(branch,remaining_minutes=2.5)
        self.reject(scenario,branch)

    def test_control_ack_must_reference_the_actual_original_ask(self):
        scenario,branch=self.fixture();self.control(branch,ask_event_sequence=0)
        self.reject(scenario,branch)

    def test_control_export_cannot_invent_an_unemitted_ack(self):
        scenario,branch=self.fixture();branch['cooperative_control_acks'].append(deepcopy(branch['cooperative_control_acks'][0]))
        self.reject(scenario,branch)

    def test_busy_contact_does_not_consume_a_clinical_work_ordinal(self):
        scenario,branch=self.fixture('cooperative_probe_busy_attempt_vary')
        self.event(branch,'ask',match=lambda row:row.get('cooperative_job_id') is None,clinical_attempt=1)
        self.reject(scenario,branch)

    def test_actual_normal_contact_after_busy_keeps_clinical_ordinal_one(self):
        scenario,branch=self.fixture('cooperative_probe_busy_attempt_vary')
        self.event(branch,'ask',match=lambda row:row.get('hospital')=='H01' and row.get('cooperative_job_id') is not None,clinical_attempt=2)
        self.reject(scenario,branch)

    def test_private_job_cannot_skip_clinical_work_ordinal(self):
        scenario,branch=self.fixture('cooperative_probe_busy_attempt_vary')
        self.event(branch,'cooperative_job_submitted',match=lambda row:row.get('hospital')=='H01' and row['kind']=='inquiry',clinical_attempt=2)
        self.reject(scenario,branch)

    def test_actual_contact_ordinal_cannot_erase_busy_request(self):
        scenario,branch=self.fixture('cooperative_probe_busy_attempt_vary')
        self.event(branch,'ask',match=lambda row:row.get('hospital')=='H01' and row.get('cooperative_job_id') is not None,attempt=1)
        self.reject(scenario,branch)

    def test_nonadmission_closure_keeps_original_absolute_deadline(self):
        scenario,branch=self.fixture('shortage_probe_late_reply')
        self.event(branch,'cooperative_probe_not_admitted',original_timeout_at=4)
        self.reject(scenario,branch)

    def test_active_probe_cannot_be_promoted_by_forged_ready_result(self):
        scenario,branch=self.fixture('cooperative_probe_active_reply_300')
        branch['cooperative_ready_records'][0]['result']=dict(eligible=True,options=['H02'],reason='OPTIONAL_PROBE_NONBLOCKING')
        self.reject(scenario,branch)

    def test_policy_ack_snapshot_cannot_peek_at_later_actual_completion(self):
        scenario,branch=self.fixture('cooperative_probe_active_reply_300')
        branch['cooperative_ready_records'][0]['snapshot']['control_acknowledgements'][0]['state']='completed'
        self.reject(scenario,branch)

    def test_pending_ordinary_work_cannot_be_hidden_in_ready_snapshot(self):
        scenario,branch=self.fixture('cooperative_probe_busy_attempt_vary')
        branch['cooperative_ready_records'][0]['snapshot']['base_snapshot']['patient']['pending_inquiries'].clear()
        self.reject(scenario,branch)

    def test_completed_ack_never_substitutes_for_received_patient_acceptance(self):
        scenario,branch=self.fixture('shortage_probe_silent')
        branch['cooperative_ready_records'][0]['snapshot']['base_snapshot']['patient']['valid_offers'].clear()
        self.reject(scenario,branch)

    def test_ready_marker_keeps_exact_proven_source(self):
        scenario,branch=self.fixture('shortage_probe_silent')
        self.event(branch,'cooperative_probe_nonblocking_ready',review_event_sequence=0)
        self.reject(scenario,branch)

    def test_busy_retry_cannot_rewrite_actual_last_contact(self):
        scenario,branch=self.fixture('cooperative_probe_busy_early_report')
        branch['cooperative_busy_retry_records'][0]['snapshot']['opportunity_snapshot']['patient']['last_contact_at']=0
        self.reject(scenario,branch)

    def test_original_selector_clock_cannot_be_reset_to_make_retry_look_ordinary(self):
        scenario,branch=self.fixture('cooperative_probe_busy_early_report')
        record=branch['cooperative_busy_retry_records'][0]
        next(row for row in branch['inquiry_decisions'] if row['event_sequence']==record['snapshot']['ordinary_selector_event_sequence'])['inputs']['last_contact_at']=86400
        self.reject(scenario,branch)

    def test_refused_probe_cannot_be_added_to_normal_tried_set(self):
        scenario,branch=self.fixture('cooperative_probe_busy_early_report')
        record=branch['cooperative_busy_retry_records'][0]
        record['snapshot']['opportunity_snapshot']['patient']['tried_hospitals'].append(record['snapshot']['not_admitted_ack']['hospital'])
        self.reject(scenario,branch)

    def test_busy_retry_requires_actual_new_report_and_sufficient_resources(self):
        scenario,branch=self.fixture('cooperative_probe_busy_early_report')
        record=branch['cooperative_busy_retry_records'][0];hid=record['snapshot']['not_admitted_ack']['hospital']
        candidate=next(row for row in record['snapshot']['opportunity_snapshot']['candidates'] if row['hospital']==hid)
        candidate['resources']['ed_bed']['received_at']=.75
        self.reject(scenario,branch)

    def test_busy_retry_cannot_invent_a_missing_normal_selector_candidate(self):
        scenario,branch=self.fixture('cooperative_probe_busy_early_report')
        branch['cooperative_busy_retry_records'][0]['snapshot']['offered_hospitals']=[]
        self.reject(scenario,branch)

    def test_busy_retry_contact_budget_is_not_refunded(self):
        scenario,branch=self.fixture('cooperative_probe_busy_early_report')
        branch['cooperative_busy_retry_records'][0]['snapshot']['opportunity_snapshot']['patient']['episode_contacts']-=1
        self.reject(scenario,branch)

    def test_auxiliary_actual_retry_keeps_its_distinct_raw_review_source(self):
        scenario,branch=self.fixture('cooperative_probe_busy_early_report')
        self.event(branch,'ask',match=lambda row:'cooperative_busy_retry_review_event_sequence' in row,cooperative_busy_retry_review_event_sequence=0)
        self.reject(scenario,branch)

    def test_new_protocol_cannot_claim_unchanged_nonpreemptive_work_calendar(self):
        scenario,branch=self.fixture();branch['deadline_recovery_spec']['hospital_work_calendar_unchanged']=True
        self.reject(scenario,branch)

    def test_cooperative_protocol_cannot_add_a_worker_or_hidden_forecast(self):
        for key in ('worker_count_unchanged','future_finish_or_remaining_work_visible_to_policy'):
            with self.subTest(key=key):
                scenario,branch=self.fixture();branch['cooperative_probe_spec'][key]=not branch['cooperative_probe_spec'][key]
                self.reject(scenario,branch)

    def test_new_work_cannot_be_hidden_by_disabled_cooperative_flag(self):
        scenario,branch=self.fixture();branch['cooperative_probe_enabled']=False
        self.reject(scenario,branch)

    def test_frozen_reference_cannot_hide_orphan_new_retry_export(self):
        scenario=make_scenario(8,'shortage_probe_release_before_reply')
        branch=run_cooperative_probe_branch(scenario,shortage_probe=True,cooperative_probe=False)
        branch['cooperative_busy_retry_records']=[dict(snapshot={},result={},event_sequence=0)]
        self.reject(scenario,branch)

    def test_worker_free_environment_cannot_hide_orphan_new_retry_export(self):
        scenario=make_scenario(8,'fresh_control')
        branch=run_cooperative_probe_branch(scenario,shortage_probe=True,cooperative_probe=True)
        branch['cooperative_busy_retry_records']=[dict(snapshot={},result={},event_sequence=0)]
        self.reject(scenario,branch)

    def test_unknown_cooperative_control_event_is_rejected(self):
        scenario,branch=self.fixture();self.control(branch,type='cooperative_future_release_prediction')
        self.reject(scenario,branch)

    def test_private_work_metrics_cannot_hide_service_wait_or_unfinished_work(self):
        for key in ('inquiry_service_minutes','inquiry_queue_wait_mean','inquiry_unfinished_jobs','inquiry_peak_waiting'):
            with self.subTest(key=key):
                scenario,branch=self.fixture();branch['metrics'][key]+=1
                self.reject(scenario,branch)

    def test_current_queue_snapshot_cannot_hide_optional_work(self):
        scenario,branch=self.fixture()
        next(row for row in branch['inquiry_snapshots'] if row['t']==1)['hospitals'][0]['waiting']=0
        self.reject(scenario,branch)

    def test_compacted_snapshot_timestamp_cannot_move_actual_work(self):
        scenario,branch=self.fixture();branch['inquiry_snapshots'][1]['t']=0
        self.reject(scenario,branch)

    def test_integer_job_or_clinical_identity_cannot_be_boolean(self):
        scenario,branch=self.fixture('cooperative_probe_busy_attempt_vary')
        self.event(branch,'ask',match=lambda row:row.get('hospital')=='H01' and row.get('cooperative_job_id') is not None,clinical_attempt=True)
        self.reject(scenario,branch)

    def test_counters_cannot_replace_actual_work_or_ready_proofs(self):
        for key in ('cooperative_preemptions','cooperative_control_acks','cooperative_probe_not_admitted','cooperative_nonblocking_ready'):
            with self.subTest(key=key):
                scenario,branch=self.fixture();branch['metrics'][key]+=1
                self.reject(scenario,branch)

    def test_saved_raw_audits_without_engine_solver_or_queue_implementation(self):
        scenario,branch=self.fixture('shortage_probe_silent')
        payload=json.dumps(dict(scenario=scenario,branch=branch),ensure_ascii=True)
        code='''
import importlib.abc,json,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.startswith('ortools') or (fullname.startswith('simulation.') and (
      fullname.endswith('_engine') or fullname.endswith('_assignment') or fullname in (
      'simulation.sim','simulation.map_replay','simulation.inquiry_queue','simulation.observation_store'))):
   raise AssertionError('forbidden execution import '+fullname)
sys.meta_path.insert(0,Block())
from simulation.cooperative_probe_audit import audit_branch
value=json.loads(sys.stdin.read())
assert audit_branch(value['scenario'],value['branch'])['passed']
print('PASS')
'''
        result=subprocess.run([sys.executable,'-B','-c',code],input=payload,text=True,capture_output=True,check=False)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'PASS')


if __name__=='__main__':unittest.main()
