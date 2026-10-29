"""Saved actual seed-7 raw audit/mutation checks; no producer or replay import."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from inquiry_lab import shortlist_audit as audit

ROOT=Path(__file__).resolve().parent
FIXTURE=ROOT/'simulation/fixtures/eat_shortlist_20261020/mix7.json.gz'
COMPRESSED_SHA256='761cee418feb846cd4c42fcb5d0fbe15787f523fac5495875f4b76fc7590768b'
RAW_SHA256='b56b9e2fb39d00520a3ae1c7eb8bbe61161cf156516d36e561ebe66a353e8f36'


def fixture():
    before=FIXTURE.stat();compressed=FIXTURE.read_bytes();after=FIXTURE.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) \
            or hashlib.sha256(compressed).hexdigest()!=COMPRESSED_SHA256:
        raise AssertionError('actual first seed-7 compressed raw changed')
    raw=gzip.decompress(compressed)
    if hashlib.sha256(raw).hexdigest()!=RAW_SHA256:raise AssertionError('actual first seed-7 raw changed')
    value=json.loads(raw)
    if value['scenario']['seed']!=7 or len(value['branches'])!=5:
        raise AssertionError('actual fixture identity/inventory differs')
    return value


class ShortlistSavedRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.raw=fixture()

    def pair(self,policy=audit.NAME,rule='immediate'):
        raw=deepcopy(self.raw)
        return raw['scenario'],next(b for b in raw['branches'] if b['policy']==policy and b['rule']==rule)

    def reject(self,scenario,branch):
        with self.assertRaises(audit.ShortlistAuditError):audit.audit_branch(scenario,branch)

    def call(self,branch,*,changed=False):
        return next(c for c in branch['policy_calls'] if not changed or c['output']['evidence']['shortlist']['membership_changed'])

    def sync(self,branch,call):
        row=next(d for d in branch['db_decisions'] if d['id']==call['db_decision_id'])
        row.update(evidence=deepcopy(call['output']['evidence']),reason=call['output']['reason'],
            hospital_ids=deepcopy(call['output']['send']))

    def test_actual_all_five_complete_raw_arms(self):
        checked=audit.audit_replay(deepcopy(self.raw))
        self.assertTrue(checked['passed']);self.assertEqual(checked['branches'],5)
        self.assertEqual(checked['new_executions'],0)

    def test_auditor_is_read_only(self):
        scenario,branch=self.pair();before=deepcopy((scenario,branch))
        audit.audit_branch(scenario,branch);self.assertEqual((scenario,branch),before)

    def test_new_shortlist_has_actual_changed_membership(self):
        scenario,branch=self.pair()
        self.assertGreater(audit.audit_branch(scenario,branch)['shortlist_membership_changes'],0)

    def test_fixed_three_wave_is_not_false_global_concurrency_three(self):
        scenario,branch=self.pair('fixed_3')
        self.assertTrue(any(len(c['inputs']['patient_pending'])>=3 and c['output']['send']
            for c in branch['policy_calls']))
        self.assertTrue(audit.audit_branch(scenario,branch)['passed'])

    def test_original_call_inputs_are_exact_seven_public_fields(self):
        scenario,branch=self.pair();self.call(branch)['inputs']['future_patients']=[]
        self.reject(scenario,branch)
        scenario,branch=self.pair();self.call(branch)['inputs']['candidates'][0]['workers']=1
        self.reject(scenario,branch)

    def test_each_policy_input_and_output_call_required(self):
        for key in audit.CALL_KEYS:
            scenario,branch=self.pair();del self.call(branch)[key];self.reject(scenario,branch)

    def test_captured_output_is_committed_database_decision(self):
        scenario,branch=self.pair();self.call(branch)['output']['reason']='changed'
        self.reject(scenario,branch)

    def test_policy_cannot_see_future_reply_or_unreceived_history(self):
        scenario,branch=self.pair();call=self.call(branch)
        call['inputs']['observations'].append(dict(hospital='H01',patient='future',
            submitted_at=call['inputs']['now'],observed_at=call['inputs']['now']+1,outcome='accept'))
        self.reject(scenario,branch)

    def test_global_pending_and_own_pending_rebuilt_from_committed_event_prefix(self):
        scenario,branch=self.pair();call=next(c for c in branch['policy_calls'] if c['inputs']['pending'])
        call['inputs']['pending'].pop();self.reject(scenario,branch)
        scenario,branch=self.pair();call=next(c for c in branch['policy_calls'] if c['inputs']['patient_pending'])
        call['inputs']['patient_pending'][0]['travel_seconds']+=1;self.reject(scenario,branch)

    def test_remaining_contact_budget_and_last_contact_rebuilt(self):
        for key in ('max_new','last_contact_at'):
            scenario,branch=self.pair();call=next(c for c in branch['policy_calls'] if c['inputs']['last_contact_at'] is not None)
            call['inputs'][key]+=1;self.reject(scenario,branch)

    def test_db_prefix_and_policy_decision_id_cannot_be_relabelled(self):
        for key in ('db_event_prefix_max','db_decision_id'):
            scenario,branch=self.pair();self.call(branch)[key]=-1;self.reject(scenario,branch)

    def test_all_candidates_and_current_travel_preserved(self):
        scenario,branch=self.pair();self.call(branch)['inputs']['candidates'].pop();self.reject(scenario,branch)
        scenario,branch=self.pair();self.call(branch)['inputs']['candidates'][0]['travel']['duration_seconds']+=1
        self.reject(scenario,branch)

    def test_shortlist_score_independently_reconstructed(self):
        for key in ('score','expected_arrival_cost','inquiry_cost'):
            scenario,branch=self.pair();call=self.call(branch,changed=True)
            call['output']['evidence']['shortlist']['scores'][0][key]+=1
            self.sync(branch,call);self.reject(scenario,branch)

    def test_shortlist_rule_limit_and_membership_cannot_change(self):
        for key,value in (('rule','nearest'),('limit',10),('membership_changed',False)):
            scenario,branch=self.pair();call=self.call(branch,changed=True)
            call['output']['evidence']['shortlist'][key]=value
            self.sync(branch,call);self.reject(scenario,branch)

    def test_pool_coordinate_order_remains_original_candidate_order(self):
        scenario,branch=self.pair();call=self.call(branch,changed=True)
        call['output']['evidence']['shortlist']['selected'].reverse();self.sync(branch,call)
        self.reject(scenario,branch)

    def test_actual_send_must_be_within_mathematically_selected_pool(self):
        scenario,branch=self.pair();call=self.call(branch,changed=True)
        call['output']['evidence']['candidate_plan'].reverse();self.sync(branch,call)
        self.reject(scenario,branch)

    def test_actual_fifo_start_order_checked(self):
        scenario,branch=self.pair();row=next(r for r in branch['trace'] if r['type']=='review_started')
        row['inquiry_id']=-1;self.reject(scenario,branch)

    def test_actual_service_work_not_shortened_or_fabricated(self):
        for typ in ('review_started','review_finished'):
            scenario,branch=self.pair();next(r for r in branch['trace'] if r['type']==typ)['service_seconds']+=1
            self.reject(scenario,branch)

    def test_actual_hospital_reply_matches_unchanged_potential(self):
        scenario,branch=self.pair();row=next(r for r in branch['trace'] if r['type']=='reply_accept')
        row['type']='reply_decline';self.reject(scenario,branch)

    def test_confirmation_requires_actual_live_acceptance(self):
        scenario,branch=self.pair();row=next(r for r in branch['trace'] if r['type']=='destination_confirmed')
        row['inquiry_id']=-1;self.reject(scenario,branch)

    def test_work_and_wait_metrics_independently_recomputed(self):
        for key in ('review_work_seconds','selection_wait_capped_mean','inquiries_per_patient'):
            scenario,branch=self.pair();branch['metrics'][key]+=1;self.reject(scenario,branch)

    def test_actual_patient_selection_and_route_cannot_be_changed(self):
        for key in ('selection_seconds','travel_seconds','estimated_arrival_seconds','inquiry_count'):
            scenario,branch=self.pair();branch['patients'][0][key]+=1;self.reject(scenario,branch)

    def test_observation_failure_has_actual_300_second_cancellation(self):
        scenario,branch=self.pair('v3_w45_h900')
        event=next(e for e in branch['db_events'] if e['type']=='patient_cancelled')
        event['type']='unrecorded_cancellation';self.reject(scenario,branch)

    def test_hold_log_must_match_actual_start_end_duration(self):
        scenario,branch=self.pair(audit.BASE_POLICY,'hold120')
        self.assertTrue(branch['holds']);branch['holds'][0]['ended_at']+=1
        self.reject(scenario,branch)

    def test_full_environment_is_paired_and_hash_bound(self):
        raw=deepcopy(self.raw);raw['scenario']['patients'][0]['at']+=1
        with self.assertRaises(audit.ShortlistAuditError):audit.audit_replay(raw)
        raw=deepcopy(self.raw);raw['arms'].reverse()
        with self.assertRaises(audit.ShortlistAuditError):audit.audit_replay(raw)

    def test_clean_import_is_standard_library_only(self):
        code=("import json,sys; import inquiry_lab.shortlist_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server.','simulation.','ortools')) or n in {'inquiry_lab.eat_policy',"
            "'inquiry_lab.shortlist_policy','inquiry_lab.runner','inquiry_lab.shortlist_runner'})))")
        output=subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,text=True)
        self.assertEqual(json.loads(output),[])


class ObservationCensorArithmeticTests(unittest.TestCase):
    def test_late_acceptance_does_not_rewrite_prior_timeout_and_pending_resolves(self):
        events=[dict(id=1,type='inquiry_sent',at=100,patient_id='P',hospital_id='H',data={'inquiry_id':7}),
                dict(id=2,type='inquiry_responded',at=300,patient_id='P',hospital_id='H',data={'inquiry_id':7,'action':'accept'})]
        observations,pending,own,_=audit._ledger(events,290,'P',1)
        self.assertEqual(observations[0]['outcome'],'timeout');self.assertEqual(len(pending),1)
        observations,pending,own,_=audit._ledger(events,301,'P',2)
        self.assertEqual(observations[0]['outcome'],'timeout');self.assertEqual(pending,[])

    def test_cancellation_before_timeout_is_not_a_nonresponse_observation(self):
        events=[dict(id=1,type='inquiry_sent',at=100,patient_id='P',hospital_id='H',data={'inquiry_id':7}),
                dict(id=2,type='inquiry_closed',at=110,patient_id='P',hospital_id='H',data={'inquiry_id':7})]
        self.assertEqual(audit._ledger(events,300,'P',2)[:3],([],[],[]))

    def test_future_event_and_event_after_prefix_do_not_enter_observations(self):
        events=[dict(id=1,type='inquiry_sent',at=100,patient_id='P',hospital_id='H',data={'inquiry_id':7}),
                dict(id=2,type='inquiry_responded',at=150,patient_id='P',hospital_id='H',data={'inquiry_id':7,'action':'decline'})]
        self.assertEqual(audit._ledger(events,120,'P',2)[0],[])
        self.assertEqual(audit._ledger(events,160,'P',1)[0],[])


if __name__=='__main__':unittest.main()
