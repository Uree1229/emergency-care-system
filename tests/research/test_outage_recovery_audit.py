"""Hand-authored backup evidence and adversarial mutations; no replay imports."""
from copy import deepcopy
import hashlib
import importlib.abc
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from simulation import outage_recovery_audit as audit


def backup_fixture():
    scenario=dict(max_minutes=20,timeout_minutes=6,accept_ttl_minutes=10,
        transfer_preparation_minutes=3,patients=[dict(id='P',occur_t=0,search_start_t=0,
            location=[35.17,126.85],ambulance_origin=[35.17,126.85],ambulance_arrive_t=0,
            priority=4,resource_demands={'bed':1},required_capabilities=['general'],treatment_minutes=3)],
        hospitals=[dict(id='NEAR',lat=35.18,lon=126.85,capacity={'bed':1},capabilities=['general']),
                   dict(id='FAR',lat=35.206,lon=126.85,capacity={'bed':2},capabilities=['general'])],
        response_environment={'*':{'NEAR':{'delay':1},'FAR':{'delay':5}}})
    events=[]
    def emit(at,kind,**fields):
        event=dict(t=at,type=kind,event_sequence=len(events),**fields);events.append(event);return event
    emit(0,'request',patient='P')
    emit(0,'ask',patient='P',hospital='NEAR',attempt=1)
    far=emit(0,'ask',patient='P',hospital='FAR',attempt=1)
    emit(1,'accept',patient='P',hospital='NEAR')
    early=emit(1,'early_offer_ready',patient='P',hospital='NEAR')
    commit=emit(1,'commit',patient='P',hospital='NEAR',eta=4,estimated_eta=4,transfer=False)
    pending=[dict(hospital='FAR',attempt=1,submitted_at=0,timeout_at=6,ask_event_sequence=far['event_sequence'])]
    emit(1,'backup_retained',patient='P',hospital='NEAR',commit_event_sequence=commit['event_sequence'],
        early_ready_event_sequence=early['event_sequence'],pending=deepcopy(pending),offers=[])
    emit(2,'resource_unavailable',hospital='NEAR',resource='bed',until=10)
    emit(2,'notify',patient='P',hospital='NEAR',resource='bed',cause='resource_unavailable')
    arrival=emit(4,'arrive',patient='P',hospital='NEAR')
    emit(4,'backup_arrival_preserved',patient='P',hospital='NEAR',arrive_event_sequence=arrival['event_sequence'],
        pending=deepcopy(pending),offers=[])
    failure=emit(4,'arrival_unable',patient='P',hospital='NEAR',reason='resource_capacity',transfer_search_at=7)
    emit(5,'backup_reply_received',patient='P',hospital='FAR',attempt=1,
        ask_event_sequence=far['event_sequence'],outcome='accept',phase='preparation')
    received=emit(5,'accept',patient='P',hospital='FAR')
    offers=[dict(hospital='FAR',accepted_at=5,expires_at=15,accept_event_sequence=received['event_sequence'])]
    emit(7,'backup_preparation_ready',patient='P',hospital='NEAR',arrival_unable_event_sequence=failure['event_sequence'],
        preparation_minutes=3,ready_at=7)
    search=emit(7,'transfer_search',patient='P',hospital='NEAR')
    emit(7,'backup_transfer_reused',patient='P',hospital='NEAR',transfer_event_sequence=search['event_sequence'],
        arrival_unable_event_sequence=failure['event_sequence'],pending=[],offers=deepcopy(offers))
    emit(7,'transfer_depart',patient='P',hospital='FAR',previous='NEAR')
    second=emit(7,'commit',patient='P',hospital='FAR',eta=13,estimated_eta=13,transfer=True)
    emit(7,'backup_closed',patient='P',reason='COMMITTED',cause_event_sequence=second['event_sequence'])
    emit(13,'arrive',patient='P',hospital='FAR')
    emit(13,'treatment_start',patient='P',hospital='FAR')
    emit(16,'treatment_complete',patient='P',hospital='FAR')
    legs=[dict(kind='dispatch',depart_t=0,arrive_t=0,origin=[35.17,126.85],destination=[35.17,126.85]),
        dict(kind='transport',provider='synthetic',geometry_kind='synthetic_straight_line',depart_t=1,arrive_t=4,
            planned_arrive_t=4,hospital='NEAR',origin=[35.17,126.85],destination=[35.18,126.85],
            estimated_minutes=3,planned_duration_seconds=180,realized_travel_factor=1.0,ended_by='arrival',
            motion_geometry=[[35.17,126.85],[35.18,126.85]]),
        dict(kind='transfer',provider='synthetic',geometry_kind='synthetic_straight_line',depart_t=7,arrive_t=13,
            planned_arrive_t=13,hospital='FAR',origin=[35.18,126.85],destination=[35.206,126.85],
            estimated_minutes=6,planned_duration_seconds=360,realized_travel_factor=1.0,ended_by='arrival',
            motion_geometry=[[35.18,126.85],[35.206,126.85]])]
    branch=dict(retained_backups_enabled=True,transfer_same_tick_enabled=True,recovery_followup_enabled=False,
        events=events,trajectories=[dict(patient='P',legs=legs)])
    return scenario,branch


def event(branch,kind):
    return next(row for row in branch['events'] if row['type']==kind)


def remove(branch,kind):
    # Renumber raw event identities and every explicit source pointer.
    mapping={};kept=[]
    for row in branch['events']:
        if row['type']==kind:continue
        mapping[row['event_sequence']]=len(kept)
        row['event_sequence']=len(kept);kept.append(row)
    def rewrite(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.endswith('event_sequence') or key in ('arrival_unable_event_sequence','commit_event_sequence','cause_event_sequence'):
                    value[key]=mapping.get(item,-1)
                elif key!='event_sequence':rewrite(item)
        elif isinstance(value,list):
            for item in value:rewrite(item)
    for row in kept:
        for key,item in list(row.items()):
            if key!='event_sequence' and key.endswith('event_sequence'):row[key]=mapping.get(item,-1)
        rewrite(row.get('pending',[]));rewrite(row.get('offers',[]))
    branch['events']=kept


def reopen_fixture():
    scenario,branch=backup_fixture()
    scenario['response_environment']['*']['FAR']['silent']=True
    branch['recovery_followup_enabled']=True
    events=branch['events'][:12]
    def emit(at,kind,**fields):
        row=dict(t=at,type=kind,event_sequence=len(events),**fields);events.append(row);return row
    failure=event(branch,'arrival_unable')
    handle=event(branch,'backup_retained')['pending'][0]
    emit(6,'backup_reply_received',patient='P',hospital='FAR',attempt=1,
        ask_event_sequence=handle['ask_event_sequence'],outcome='no_response',phase='preparation')
    emit(6,'no_response',patient='P',hospital='FAR')
    emit(7,'backup_preparation_ready',patient='P',hospital='NEAR',arrival_unable_event_sequence=failure['event_sequence'],
        preparation_minutes=3,ready_at=7)
    transfer=emit(7,'transfer_search',patient='P',hospital='NEAR')
    emit(7,'backup_transfer_reused',patient='P',hospital='NEAR',transfer_event_sequence=transfer['event_sequence'],
        arrival_unable_event_sequence=failure['event_sequence'],pending=[],offers=[])
    emit(7,'resource_restored',hospital='NEAR',resource='bed')
    report=emit(7,'resource_report_received',hospital='NEAR',source='feed',packet_sequence=0,
        observed_at=7,received_at=7,resources={'bed':{'capacity':1,'used':0}},applied=True)
    emit(7,'backup_failed_reopened',patient='P',hospital='NEAR',arrival_unable_event_sequence=failure['event_sequence'],
        report_event_sequence=report['event_sequence'])
    emit(7,'information_check',patient='P',hospital='NEAR',purpose='inquiry',eligible=True,reason=None,resources={})
    emit(7,'ask',patient='P',hospital='NEAR',attempt=2)
    emit(8,'accept',patient='P',hospital='NEAR')
    commit=emit(8,'commit',patient='P',hospital='NEAR',eta=11,estimated_eta=11,transfer=True)
    emit(8,'backup_closed',patient='P',reason='COMMITTED',cause_event_sequence=commit['event_sequence'])
    branch['events']=events
    return scenario,branch


def report_fixture():
    from test_existing_info_audit import full_branch,report_fixture as previous_report
    prior=previous_report()
    policies=[];branches=[]
    for code in audit.POLICY_IDS:
        old=code if code in audit.existing.POLICY_IDS else 'existing_info_C1'
        scenario,branch=full_branch(old)
        scenario['comparison_id']=audit.EXPERIMENT_ID
        spec=scenario['patients'][0]
        spec.update(ambulance_origin=[37,127],ambulance_arrive_t=0,location=[37,127])
        scenario['hospitals'][0].update(lat=37.007,lon=127)
        branch.update(id=code,comparison_id=audit.EXPERIMENT_ID,source_experiment='outage_recovery_comparison',
            retained_backups_enabled=code in ('retained_backup_C1','outage_recovery_C1'),
            transfer_same_tick_enabled=code=='outage_recovery_C1',recovery_followup_enabled=code=='outage_recovery_C1')
        branch['policy']['name']=code
        for row in branch['events']:
            if row['type']=='ask':row['attempt']=1
            if row['type']=='commit':row.update(eta=5,estimated_eta=5,transfer=False)
        branch['trajectories']=[dict(patient='P1',legs=[
            dict(kind='dispatch',depart_t=0,arrive_t=0,origin=[37,127],destination=[37,127]),
            dict(kind='transport',provider='synthetic',geometry_kind='synthetic_straight_line',
                depart_t=3,arrive_t=5,planned_arrive_t=5,hospital='H1',origin=[37,127],destination=[37.007,127],
                estimated_minutes=2,planned_duration_seconds=120,realized_travel_factor=1.,ended_by='arrival',
                motion_geometry=[[37,127],[37.007,127]])])]
        branch['metrics']['backup_wall_seconds']=.001 if branch['retained_backups_enabled'] else 0
        branch['metrics'].update(audit.backup_metrics(branch))
        branches.append(branch)
        policy=deepcopy(next(row for row in prior['settings']['policies'] if row['id']==old))
        policy.update(id=code,label=code,retained_backups=branch['retained_backups_enabled'],
                      transfer_same_tick=branch['transfer_same_tick_enabled'],recovery_followup=branch['recovery_followup_enabled'])
        policies.append(policy)
    by={branch['id']:branch for branch in branches}
    case=dict(scenario=scenario,branches=branches,execution_order=list(audit.POLICY_IDS),
        summaries={b['id']:audit.base.raw_branch_metrics(scenario,b) for b in branches},
        information_metrics={b['id']:audit.information.information_metrics(b) for b in branches},
        guard_metrics={b['id']:audit.responsive.guard_metrics(b) for b in branches},
        responsiveness_metrics={b['id']:audit.responsive.responsiveness_metrics(b) for b in branches},
        stability_metrics={b['id']:audit.stable.stability_metrics(b) for b in branches},
        waiting_metrics={b['id']:audit.waiting_metrics(scenario,b) for b in branches},
        update_metrics={b['id']:audit.total.update_metrics(b) for b in branches},
        early_offer_metrics={b['id']:audit.early_offer_metrics(b) for b in branches},
        backup_metrics={b['id']:audit.backup_metrics(b) for b in branches},
        diagnostics={b['id']:audit._diagnostics(scenario,b) for b in branches},
        paired_comparisons={name:dict(baseline=left,comparison=right,
            metrics=audit.responsive._paired(scenario,by[left],by[right]),
            arrival_details=audit._paired_tails(scenario,by[left],by[right]))
            for name,left,right in audit.COMPARISON_PAIRS})
    source='simulation/outage_recovery_audit.py'
    return dict(schema_version=1,kind='outage_recovery_comparison',experiment_id=audit.EXPERIMENT_ID,
        phase='development',synthetic=True,seeds=[7],families=['fresh_control'],
        settings=dict(deepcopy(audit.FIXED_SETTINGS),policies=policies),
        source_sha256={source:hashlib.sha256((Path(__file__).resolve().parents[2]/source).read_bytes()).hexdigest()},
        runtime_versions={'python':'raw-fixture'},lock_sha256=None,results=[case])


def alert_fixture():
    scenario,branch=backup_fixture()
    scenario['accept_ttl_minutes']=1
    scenario['response_environment']['*']['FAR']['delay']=2
    branch['recovery_followup_enabled']=True
    events=[]
    def emit(at,kind,**fields):
        row=dict(t=at,type=kind,event_sequence=len(events),**fields);events.append(row);return row
    emit(0,'resource_report_received',hospital='FAR',source='initial',packet_sequence=0,
        observed_at=0,received_at=0,resources={'bed':{'capacity':2,'used':0}},applied=True)
    emit(0,'request',patient='P')
    emit(0,'ask',patient='P',hospital='NEAR',attempt=1)
    ask=emit(0,'ask',patient='P',hospital='FAR',attempt=1)
    emit(1,'accept',patient='P',hospital='NEAR')
    early=emit(1,'early_offer_ready',patient='P',hospital='NEAR')
    commit=emit(1,'commit',patient='P',hospital='NEAR')
    emit(1,'backup_retained',patient='P',hospital='NEAR',commit_event_sequence=commit['event_sequence'],
        early_ready_event_sequence=early['event_sequence'],pending=[dict(hospital='FAR',attempt=1,
            submitted_at=0,timeout_at=6,ask_event_sequence=ask['event_sequence'])],offers=[])
    emit(2,'backup_reply_received',patient='P',hospital='FAR',attempt=1,ask_event_sequence=ask['event_sequence'],outcome='accept',phase='transit')
    emit(2,'accept',patient='P',hospital='FAR')
    expired=emit(3,'expire',patient='P',hospital='FAR')
    emit(4,'resource_unavailable',hospital='NEAR',resource='bed',until=10)
    notice=emit(4,'notify',patient='P',hospital='NEAR',resource='bed',cause='resource_unavailable')
    emit(4,'backup_alert_retry',patient='P',hospital='FAR',notify_event_sequence=notice['event_sequence'],expire_event_sequence=expired['event_sequence'])
    emit(4,'ask',patient='P',hospital='FAR',attempt=2)
    emit(6,'accept',patient='P',hospital='FAR')
    emit(6,'redirect',patient='P',hospital='FAR',previous='NEAR')
    commit=emit(6,'commit',patient='P',hospital='FAR')
    emit(6,'backup_closed',patient='P',reason='COMMITTED',cause_event_sequence=commit['event_sequence'])
    branch['events']=events
    return scenario,branch


class BackupEvidenceTests(unittest.TestCase):
    def setUp(self):self.scenario,self.branch=backup_fixture()
    def rejects(self):
        with self.assertRaises(audit.OutageRecoveryAuditError):audit.audit_backups(self.scenario,self.branch)
    def test_original_work_received_before_normal_transfer_reuses_original_ttl(self):
        result=audit.audit_backups(self.scenario,self.branch)
        self.assertEqual(result['backup_replies'],1)
        self.assertEqual(result['backup_transfers_reused'],1)
        self.assertEqual(result['backup_episodes_closed'],1)
    def test_retention_disabled_rejects_markers(self):
        self.branch.update(retained_backups_enabled=False,transfer_same_tick_enabled=False);self.rejects()
    def test_nonboolean_flags_rejected(self):
        self.branch['retained_backups_enabled']=1;self.rejects()
    def test_preparation_without_retention_rejected(self):
        self.branch['retained_backups_enabled']=False;self.rejects()
    def test_followup_without_same_tick_rejected(self):
        self.branch.update(transfer_same_tick_enabled=False,recovery_followup_enabled=True);self.rejects()
    def test_missing_retention_rejected(self):remove(self.branch,'backup_retained');self.rejects()
    def test_missing_arrival_preservation_rejected(self):remove(self.branch,'backup_arrival_preserved');self.rejects()
    def test_missing_preparation_marker_rejected(self):remove(self.branch,'backup_preparation_ready');self.rejects()
    def test_missing_transfer_reuse_rejected(self):remove(self.branch,'backup_transfer_reused');self.rejects()
    def test_missing_closure_rejected(self):remove(self.branch,'backup_closed');self.rejects()
    def test_missing_receive_only_marker_rejected(self):remove(self.branch,'backup_reply_received');self.rejects()
    def test_original_query_cannot_be_omitted(self):event(self.branch,'backup_retained')['pending']=[];self.rejects()
    def test_original_attempt_cannot_change(self):event(self.branch,'backup_retained')['pending'][0]['attempt']=2;self.rejects()
    def test_original_submission_cannot_change(self):event(self.branch,'backup_retained')['pending'][0]['submitted_at']=1;self.rejects()
    def test_timeout_cannot_be_extended(self):event(self.branch,'backup_retained')['pending'][0]['timeout_at']=10;self.rejects()
    def test_future_reply_time_cannot_be_exposed(self):event(self.branch,'backup_retained')['pending'][0]['at']=5;self.rejects()
    def test_silence_cannot_be_exposed(self):event(self.branch,'backup_retained')['pending'][0]['silent']=False;self.rejects()
    def test_retention_cannot_fabricate_offer(self):
        event(self.branch,'backup_retained')['offers']=[dict(hospital='FAR',accepted_at=1,expires_at=11,accept_event_sequence=3)];self.rejects()
    def test_retention_must_follow_real_commit(self):event(self.branch,'backup_retained')['commit_event_sequence']=3;self.rejects()
    def test_retention_requires_current_early_ready(self):event(self.branch,'early_offer_ready')['t']=0;self.rejects()
    def test_reply_attempt_cannot_change(self):event(self.branch,'backup_reply_received')['attempt']=2;self.rejects()
    def test_reply_original_handle_cannot_change(self):event(self.branch,'backup_reply_received')['ask_event_sequence']=1;self.rejects()
    def test_reply_phase_cannot_change(self):event(self.branch,'backup_reply_received')['phase']='transit';self.rejects()
    def test_reply_cannot_use_unreceived_future_acceptance(self):event(self.branch,'backup_reply_received')['outcome']='decline';self.rejects()
    def test_reply_cannot_arrive_before_original_hospital_work(self):
        event(self.branch,'backup_reply_received')['t']=4;self.rejects()
    def test_original_silence_cannot_become_acceptance(self):
        self.scenario['response_environment']['*']['FAR']['silent']=True;self.rejects()
    def test_original_timeout_cannot_be_resurrected(self):self.scenario['timeout_minutes']=4;self.rejects()
    def test_arrival_cannot_drop_original_pending_work(self):event(self.branch,'backup_arrival_preserved')['pending']=[];self.rejects()
    def test_reuse_cannot_reset_original_acceptance(self):event(self.branch,'backup_transfer_reused')['offers'][0]['accepted_at']=7;self.rejects()
    def test_reuse_cannot_renew_original_expiry(self):event(self.branch,'backup_transfer_reused')['offers'][0]['expires_at']=17;self.rejects()
    def test_expired_original_offer_cannot_be_reused(self):self.scenario['accept_ttl_minutes']=2;self.rejects()
    def test_transfer_cannot_skip_preparation(self):event(self.branch,'backup_preparation_ready')['preparation_minutes']=0;self.rejects()
    def test_transfer_cannot_use_different_failed_arrival(self):event(self.branch,'backup_transfer_reused')['arrival_unable_event_sequence']=1;self.rejects()
    def test_closure_cannot_use_other_cause(self):event(self.branch,'backup_closed')['cause_event_sequence']=1;self.rejects()
    def test_notification_cannot_include_future_restore(self):event(self.branch,'notify')['until']=10;self.rejects()


class MovementIntegrityTests(unittest.TestCase):
    def setUp(self):self.scenario,self.branch=backup_fixture()
    def rejects(self):
        with self.assertRaises(audit.OutageRecoveryAuditError):audit.audit_motion(self.scenario,self.branch)
    def test_unchanged_route_and_transfer_preparation_pass(self):
        self.assertEqual(audit.audit_motion(self.scenario,self.branch),dict(movement_legs=2,unchanged_transfer_preparations=1))
    def test_route_shortening_rejected(self):self.branch['trajectories'][0]['legs'][-1]['arrive_t']=12;self.rejects()
    def test_future_eta_shortening_rejected(self):self.branch['trajectories'][0]['legs'][-1]['planned_arrive_t']=12;self.rejects()
    def test_teleported_origin_rejected(self):self.branch['trajectories'][0]['legs'][-1]['origin']=[35.2,126.85];self.rejects()
    def test_pause_rejected(self):self.branch['trajectories'][0]['legs'][1]['ended_by']='notification_pause';self.rejects()
    def test_estimator_changed_rejected(self):self.branch['trajectories'][0]['legs'][-1]['estimated_minutes']=5;self.rejects()
    def test_transfer_preparation_shortened_rejected(self):event(self.branch,'arrival_unable')['transfer_search_at']=4;self.rejects()
    def test_dispatch_modified_rejected(self):self.branch['trajectories'][0]['legs'][0]['arrive_t']=1;self.rejects()


class ReceivedEvidenceReopeningTests(unittest.TestCase):
    def setUp(self):self.scenario,self.branch=reopen_fixture()
    def rejects(self):
        with self.assertRaises(audit.OutageRecoveryAuditError):audit.audit_backups(self.scenario,self.branch)
    def test_current_complete_report_reopens_resource_failure_and_asks_again(self):
        self.assertEqual(audit.audit_backups(self.scenario,self.branch)['backup_report_reopens'],1)
    def test_followup_disabled_rejects_reopening(self):self.branch['recovery_followup_enabled']=False;self.rejects()
    def test_capability_failure_cannot_reopen(self):event(self.branch,'arrival_unable')['reason']='required_capability';self.rejects()
    def test_wrong_failure_evidence_rejected(self):event(self.branch,'backup_failed_reopened')['arrival_unable_event_sequence']=1;self.rejects()
    def test_wrong_report_evidence_rejected(self):event(self.branch,'backup_failed_reopened')['report_event_sequence']=1;self.rejects()
    def test_pre_failure_source_rejected(self):event(self.branch,'resource_report_received')['observed_at']=3;self.rejects()
    def test_report_not_received_this_minute_rejected(self):
        row=event(self.branch,'resource_report_received');row['t']=6;row['received_at']=6;row['observed_at']=6;self.rejects()
    def test_unknown_resource_cannot_reopen(self):event(self.branch,'resource_report_received')['resources']['bed']=None;self.rejects()
    def test_insufficient_resource_cannot_reopen(self):event(self.branch,'resource_report_received')['resources']['bed']['used']=1;self.rejects()
    def test_extra_future_restoration_field_rejected(self):event(self.branch,'backup_failed_reopened')['until']=10;self.rejects()
    def test_new_patient_inquiry_still_required(self):remove(self.branch,'ask');self.rejects()
    def test_resource_confirmation_is_not_a_patient_inquiry(self):event(self.branch,'information_check')['purpose']='allocation';self.rejects()
    def test_reopening_cannot_ask_different_hospital(self):
        row=next(row for row in self.branch['events'] if row['type']=='ask' and row['attempt']==2);row['hospital']='FAR';self.rejects()
    def test_missing_reopen_marker_rejected(self):remove(self.branch,'backup_failed_reopened');self.rejects()


class ReceivedAlertRetryTests(unittest.TestCase):
    def setUp(self):self.scenario,self.branch=alert_fixture()
    def rejects(self):
        with self.assertRaises(audit.OutageRecoveryAuditError):audit.audit_backups(self.scenario,self.branch)
    def test_received_expiry_and_alert_allow_fresh_new_patient_inquiry(self):
        self.assertEqual(audit.audit_backups(self.scenario,self.branch)['backup_alert_retries'],1)
    def test_omitted_required_alert_retry_rejected(self):remove(self.branch,'backup_alert_retry');self.rejects()
    def test_unreceived_expiration_cannot_trigger_retry(self):event(self.branch,'backup_alert_retry')['expire_event_sequence']=1;self.rejects()
    def test_unreceived_notification_cannot_trigger_retry(self):event(self.branch,'backup_alert_retry')['notify_event_sequence']=1;self.rejects()
    def test_future_expiration_cannot_trigger_retry(self):event(self.branch,'expire')['t']=5;self.rejects()
    def test_followup_disabled_rejects_retry(self):self.branch['recovery_followup_enabled']=False;self.rejects()
    def test_insufficient_current_quantities_block_alert_retry(self):
        event(self.branch,'resource_report_received')['resources']['bed']['used']=2;self.rejects()
    def test_unknown_current_quantities_block_alert_retry(self):
        event(self.branch,'resource_report_received')['resources']['bed']=None;self.rejects()
    def test_retry_cannot_expose_future_reply_schedule(self):event(self.branch,'backup_alert_retry')['at']=6;self.rejects()


class AuditImportBoundaryTests(unittest.TestCase):
    def test_verifier_imports_without_engine_solver_or_scenario_builder(self):
        program='''
import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.startswith('ortools') or fullname.endswith(('_engine','_scenarios')) or fullname in ('simulation.sim','simulation.map_replay','simulation.information_observations'):
   raise AssertionError('forbidden verifier dependency '+fullname)
sys.meta_path.insert(0,Block())
from simulation import outage_recovery_audit
print('independent')
'''
        result=subprocess.run([sys.executable,'-c',program],cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'independent')


class ReportAuditTests(unittest.TestCase):
    def test_hand_authored_six_branch_report_passes(self):
        self.assertEqual(audit.audit_report(report_fixture())['branches'],6)
    def test_backup_aggregate_cannot_override_raw_events(self):
        report=report_fixture();report['results'][0]['backup_metrics']['outage_recovery_C1']['backup_report_reopens']=2
        with self.assertRaises(audit.OutageRecoveryAuditError):audit.audit_report(report)
    def test_new_source_flag_rejected(self):
        report=report_fixture();report['settings']['policies'][-1]['resource_updates']=True
        with self.assertRaises(audit.OutageRecoveryAuditError):audit.audit_report(report)
    def test_followup_policy_ablation_rejected(self):
        report=report_fixture();report['settings']['policies'][-2]['recovery_followup']=True
        with self.assertRaises(audit.OutageRecoveryAuditError):audit.audit_report(report)
    def test_missing_branch_rejected(self):
        report=report_fixture();report['results'][0]['branches'].pop()
        with self.assertRaises(audit.OutageRecoveryAuditError):audit.audit_report(report)
    def test_malformed_json_contract_raises_audit_error(self):
        report=report_fixture();report['results'][0]['backup_metrics']=None
        with self.assertRaises(audit.OutageRecoveryAuditError):audit.audit_report(report)
    def test_cli_audits_saved_json_without_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'raw.json';path.write_text(json.dumps(report_fixture()),encoding='utf-8')
            result=subprocess.run([sys.executable,'-m','simulation.outage_recovery_audit',str(path)],
                cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['passed'])


if __name__=='__main__':unittest.main()
