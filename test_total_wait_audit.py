"""Raw synthetic audit fixtures and mutation tests; no engine/solver imports."""
from copy import deepcopy
import importlib.abc
from pathlib import Path
import subprocess
import sys
import unittest

from simulation import total_wait_audit as audit
from test_stable_c1_audit import stable_branch


def publication_fixture(*, delay=0, silent=False, missing=False):
    scenario = dict(max_minutes=4, patients=[], hospitals=[dict(id='H', lat=37, lon=127,
        capabilities=['general'], capacity={'bed':1},
        initial_occupancy=[dict(until=2, demand={'bed':1})])],
        observation_config=dict(feed_interval_minutes=0, confirmation_silent=silent,
            feed_missing_resources=['bed'] if missing else [], confirmation_missing_resources=[]),
        resource_update_config=dict(delay_minutes=delay, silent=silent,
                                    missing_resources=['bed'] if missing else []))
    events, pending, identifier = [], [], 0
    def emit(at, kind, **fields):
        row = dict(t=at, type=kind, event_sequence=len(events), **fields)
        events.append(row)
        return row
    for at in range(5):
        for due, capture in list(pending):
            if due == at:
                emit(at, 'resource_report_received', hospital='H', source='feed',
                    publication_id=capture['publication_id'], packet_sequence=0,
                    observed_at=capture['observed_at'], received_at=at,
                    resources=deepcopy(capture['resources']), applied=True)
                pending.remove((due, capture))
        for phase in ('pre_queries', 'after_treatments'):
            emit(at, 'resource_update_cycle', phase=phase)
            if at == 2 and phase == 'pre_queries' and not silent:
                capture = emit(at, 'resource_update_captured', hospital='H',
                    publication_id=identifier, observed_at=at, receive_at=at+delay,
                    resources={'bed':None if missing else {'capacity':1, 'used':0}})
                identifier += 1
                if delay:
                    pending.append((at+delay, capture))
                else:
                    emit(at, 'resource_report_received', hospital='H', source='feed',
                        publication_id=capture['publication_id'], packet_sequence=0,
                        observed_at=at, received_at=at, resources=deepcopy(capture['resources']),
                        applied=True)
    return scenario, dict(information_mode='observed', resource_updates_enabled=True, events=events)


def review_fixture():
    scenario = dict(max_minutes=2, accept_ttl_minutes=10,
        patients=[dict(id='P', occur_t=0, search_start_t=0, priority=4, location=[37,127],
            resource_demands={'bed':1}, required_capabilities=['general'])],
        hospitals=[dict(id='H', lat=37, lon=127, capacity={'bed':1}, capabilities=['general'])])
    events = []
    def emit(at, kind, **fields):
        row = dict(t=at, type=kind, event_sequence=len(events), **fields)
        events.append(row)
        return row
    emit(0, 'resource_report_received', hospital='H', source='initial', packet_sequence=0,
         observed_at=0, received_at=0, resources={'bed':{'capacity':1,'used':1}}, applied=True)
    emit(0, 'request', patient='P')
    for at in range(3):
        report = None
        if at == 1:
            report = emit(1, 'resource_report_received', hospital='H', source='feed',
                packet_sequence=1, observed_at=1, received_at=1,
                resources={'bed':{'capacity':1,'used':0}}, applied=True)
        for phase in ('pre_queries', 'after_treatments'):
            emit(at, 'event_review_cycle', phase=phase)
            if at == 1 and phase == 'pre_queries':
                emit(1, 'resource_event_review', patient='P', hospital='H',
                     report_event_sequence=report['event_sequence'], previous_retry_at=12)
                emit(1, 'information_check', patient='P', hospital='H', purpose='inquiry',
                     eligible=True, reason=None, resources={})
                emit(1, 'ask', patient='P', hospital='H')
    return scenario, dict(information_mode='observed', event_review_enabled=True, events=events)


class TotalWaitAuditTests(unittest.TestCase):
    def test_current_resource_release_publication_passes(self):
        scenario, branch = publication_fixture()
        self.assertEqual(audit.audit_publications(scenario, branch)['resource_update_receipts'], 1)

    def test_delayed_publication_preserves_source_time(self):
        scenario, branch = publication_fixture(delay=2)
        self.assertEqual(audit.audit_publications(scenario, branch)['resource_update_receipts'], 1)

    def test_delivery_beyond_horizon_remains_pending(self):
        scenario, branch = publication_fixture(delay=4)
        result = audit.audit_publications(scenario, branch)
        self.assertEqual(result['resource_update_captures'], 1)
        self.assertEqual(result['resource_update_receipts'], 0)

    def test_silence_keeps_source_cycles_but_no_reports(self):
        scenario, branch = publication_fixture(silent=True)
        self.assertEqual(audit.audit_publications(scenario, branch)['resource_update_captures'], 0)

    def test_permanent_unknown_field_stays_unknown(self):
        scenario, branch = publication_fixture(missing=True)
        self.assertEqual(audit.audit_publications(scenario, branch)['resource_update_captures'], 1)

    def test_missing_capture_rejected(self):
        scenario, branch = publication_fixture()
        branch['events'] = [e for e in branch['events'] if e['type'] != 'resource_update_captured']
        for index, event in enumerate(branch['events']): event['event_sequence'] = index
        with self.assertRaises(audit.TotalWaitAuditError):
            audit.audit_publications(scenario, branch)

    def test_extra_unchanged_field_rejected(self):
        scenario, branch = publication_fixture()
        capture = next(e for e in branch['events'] if e['type'] == 'resource_update_captured')
        capture['resources']['team'] = {'capacity':1,'used':0}
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'current changed fields'):
            audit.audit_publications(scenario, branch)

    def test_wrong_physical_occupancy_rejected(self):
        scenario, branch = publication_fixture()
        next(e for e in branch['events'] if e['type'] == 'resource_update_captured')['resources']['bed']['used'] = 1
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'current changed fields'):
            audit.audit_publications(scenario, branch)

    def test_future_release_field_rejected(self):
        scenario, branch = publication_fixture()
        next(e for e in branch['events'] if e['type'] == 'resource_update_captured')['expected_release_at'] = 8
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'future schedule'):
            audit.audit_publications(scenario, branch)

    def test_mask_bypass_rejected(self):
        scenario, branch = publication_fixture(missing=True)
        scenario['resource_update_config']['missing_resources'] = []
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'bypasses'):
            audit.audit_publications(scenario, branch)

    def test_hospital_silence_bypass_rejected(self):
        scenario, branch = publication_fixture()
        scenario['observation_config']['confirmation_silent'] = True
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'bypasses'):
            audit.audit_publications(scenario, branch)

    def test_source_age_cannot_be_reset_on_receipt(self):
        scenario, branch = publication_fixture(delay=2)
        next(e for e in branch['events'] if e['type'] == 'resource_report_received')['observed_at'] = 4
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'source payload'):
            audit.audit_publications(scenario, branch)

    def test_delivery_cannot_arrive_before_capture(self):
        scenario, branch = publication_fixture(delay=2)
        next(e for e in branch['events'] if e['type'] == 'resource_update_captured')['receive_at'] = 1
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'current time and delay'):
            audit.audit_publications(scenario, branch)

    def test_current_report_driven_new_inquiry_passes(self):
        scenario, branch = review_fixture()
        result = audit.audit_event_review(scenario, branch)
        self.assertEqual(result['event_review_retries'], 1)
        self.assertEqual(result['event_review_cycles'], 6)

    def test_missing_eligible_retry_rejected(self):
        scenario, branch = review_fixture()
        branch['events'] = [e for e in branch['events'] if e['type'] not in
                            ('resource_event_review','information_check','ask')]
        for index, event in enumerate(branch['events']): event['event_sequence'] = index
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'omitted eligible'):
            audit.audit_event_review(scenario, branch)

    def test_report_review_cannot_replace_inquiry_with_commit(self):
        scenario, branch = review_fixture()
        next(e for e in branch['events'] if e['type'] == 'ask')['type'] = 'commit'
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'new patient inquiry'):
            audit.audit_event_review(scenario, branch)

    def test_report_review_requires_registered_patient(self):
        scenario, branch = review_fixture()
        branch['events'] = [e for e in branch['events'] if e['type'] != 'request']
        for index, event in enumerate(branch['events']): event['event_sequence'] = index
        # Keep the source pointer valid after removing the request.
        report = next(e for e in branch['events'] if e['type'] == 'resource_report_received' and e['t']==1)
        next(e for e in branch['events'] if e['type'] == 'resource_event_review')['report_event_sequence'] = report['event_sequence']
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'eligible current waiter'):
            audit.audit_event_review(scenario, branch)

    def test_stale_or_unknown_current_report_cannot_wake(self):
        for payload, observed in ((None,1), ({'capacity':1,'used':0},-11)):
            scenario, branch = review_fixture()
            report = next(e for e in branch['events'] if e['type']=='resource_report_received' and e['t']==1)
            report['resources']['bed'], report['observed_at'] = payload, observed
            report['applied'] = observed >= 0
            with self.assertRaises(audit.TotalWaitAuditError):
                audit.audit_event_review(scenario, branch)

    def test_disabled_review_cannot_emit_cycle(self):
        scenario, branch = review_fixture()
        branch['event_review_enabled'] = False
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'disabled branch'):
            audit.audit_event_review(scenario, branch)

    def test_review_cannot_use_an_older_report_pointer(self):
        scenario,branch=review_fixture()
        next(e for e in branch['events'] if e['type']=='resource_event_review')['report_event_sequence']=0
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'latest same-minute'):
            audit.audit_event_review(scenario,branch)

    def test_missing_required_team_prevents_review(self):
        scenario,branch=review_fixture()
        scenario['patients'][0]['resource_demands']['team']=1
        scenario['hospitals'][0]['capacity']['team']=1
        first=next(e for e in branch['events'] if e['type']=='resource_report_received')
        first['resources']['team']=None
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'complete feasible'):
            audit.audit_event_review(scenario,branch)

    def test_waiting_for_existing_inquiry_prevents_duplicate_review(self):
        scenario,branch=review_fixture()
        insertion=next(i for i,e in enumerate(branch['events']) if e['type']=='event_review_cycle')
        branch['events'].insert(insertion,dict(t=0,type='ask',patient='P',hospital='H'))
        for index,event in enumerate(branch['events']):event['event_sequence']=index
        report=next(e for e in branch['events'] if e['type']=='resource_report_received' and e['t']==1)
        next(e for e in branch['events'] if e['type']=='resource_event_review')['report_event_sequence']=report['event_sequence']
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'eligible current waiter'):
            audit.audit_event_review(scenario,branch)

    def test_existing_acceptance_requires_normal_resolution_before_review(self):
        scenario,branch=review_fixture()
        insertion=next(i for i,e in enumerate(branch['events']) if e['type']=='event_review_cycle')
        branch['events'][insertion:insertion]=[
            dict(t=0,type='ask',patient='P',hospital='H'),
            dict(t=0,type='accept',patient='P',hospital='H')]
        for index,event in enumerate(branch['events']):event['event_sequence']=index
        report=next(e for e in branch['events'] if e['type']=='resource_report_received' and e['t']==1)
        next(e for e in branch['events'] if e['type']=='resource_event_review')['report_event_sequence']=report['event_sequence']
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'eligible current waiter'):
            audit.audit_event_review(scenario,branch)

    def test_whole_wait_includes_search_and_omits_transit(self):
        scenario, branch = stable_branch()
        metrics = audit.waiting_metrics(scenario, branch)
        self.assertEqual(metrics['search_wait_sum_minutes'], 3)
        self.assertEqual(metrics['queue_wait_sum_minutes'], 0)
        self.assertEqual(metrics['total_wait_sum_minutes'], 3)
        self.assertEqual(metrics['request_to_treatment_capped_sum_minutes'], 5)
        self.assertEqual(metrics['priority_weighted_total_wait_sum_minutes'], 12)

    def test_unassigned_patient_stays_in_capped_denominator(self):
        scenario, branch = stable_branch()
        branch['events'] = [e for e in branch['events'] if e['type'] not in
                            ('commit','arrive','treatment_start','treatment_complete')]
        branch['patient_outcomes'][0].update(first_commit_t=None,final_arrive_t=None,
            treatment_start_t=None,treatment_complete_t=None,state='UNASSIGNED')
        for index, event in enumerate(e for e in branch['events'] if 'event_sequence' in e):
            event['event_sequence'] = index
        metrics = audit.waiting_metrics(scenario, branch)
        self.assertEqual(metrics['total_wait_sum_minutes'], 20)
        self.assertEqual(metrics['request_to_treatment_capped_sum_minutes'], 20)
        self.assertEqual(metrics['untreated'], 1)

    def test_hospital_queue_adds_to_search_wait(self):
        scenario, branch = stable_branch()
        for event in branch['events']:
            if event['type'] == 'treatment_start': event['t'] = 8
            if event['type'] == 'treatment_complete': event['t'] = 11
        branch['events'].sort(key=lambda event:event['t'])
        for index,event in enumerate(e for e in branch['events'] if 'event_sequence' in e):
            event['event_sequence']=index
        branch['patient_outcomes'][0].update(treatment_start_t=8,treatment_complete_t=11)
        metrics = audit.waiting_metrics(scenario,branch)
        self.assertEqual(metrics['search_wait_sum_minutes'],3)
        self.assertEqual(metrics['queue_wait_sum_minutes'],3)
        self.assertEqual(metrics['total_wait_sum_minutes'],6)
        self.assertEqual(metrics['request_to_treatment_capped_sum_minutes'],8)

    def test_transfer_preparation_and_research_are_in_wait(self):
        scenario, branch = stable_branch()
        timeline=[(0,'request'),(3,'commit'),(5,'arrive'),(5,'arrival_unable'),
                  (7,'transfer_search'),(8,'transfer_depart'),(8,'commit'),
                  (10,'arrive'),(12,'treatment_start')]
        branch['events']=[dict(t=at,type=kind,patient='P1',hospital='H1',event_sequence=index)
                          for index,(at,kind) in enumerate(timeline)]
        branch['events'].append(dict(t=15,type='treatment_complete',patient='P1',hospital='H1'))
        branch['patient_outcomes'][0].update(first_commit_t=3,final_arrive_t=10,
            treatment_start_t=12,treatment_complete_t=15,inquiries=0,transfers=1)
        metrics = audit.waiting_metrics(scenario,branch)
        self.assertEqual(metrics['search_wait_sum_minutes'],4)
        self.assertEqual(metrics['transfer_wait_sum_minutes'],2)
        self.assertEqual(metrics['queue_wait_sum_minutes'],2)
        self.assertEqual(metrics['total_wait_sum_minutes'],8)

    def test_untreated_queue_is_capped_at_observation_end(self):
        scenario, branch = stable_branch()
        branch['events']=[e for e in branch['events'] if e['type'] not in
                          ('treatment_start','treatment_complete')]
        for index,event in enumerate(e for e in branch['events'] if 'event_sequence' in e):
            event['event_sequence']=index
        branch['patient_outcomes'][0].update(treatment_start_t=None,
            treatment_complete_t=None,state='NOT_TREATED')
        metrics = audit.waiting_metrics(scenario,branch)
        self.assertEqual(metrics['queue_wait_sum_minutes'],15)
        self.assertEqual(metrics['total_wait_sum_minutes'],18)
        self.assertEqual(metrics['request_to_treatment_capped_sum_minutes'],20)
        self.assertEqual(metrics['untreated'],1)

    def test_independent_imports_never_load_engine_or_solver(self):
        program = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('ortools','simulation.total_wait_engine','simulation.total_wait_experiment',
            'simulation.total_wait_scenarios','simulation.map_replay','simulation.stable_c1_engine',
            'simulation.partial_information_engine','server')):
            raise RuntimeError('forbidden import: '+fullname)
sys.meta_path.insert(0,Block())
import simulation.total_wait_audit
"""
        result = subprocess.run([sys.executable,'-c',program], cwd=Path(__file__).resolve().parent,
                                capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


def total_wait_branch(code='total_wait_C1'):
    reference = code if code in audit.stable.POLICY_IDS else 'stable_responsive_C1'
    scenario, branch = stable_branch(reference)
    scenario['patients'][0]['location'] = [37,127]
    scenario['hospitals'][0].update(lat=37,lon=127)
    scenario.update(comparison_id=audit.EXPERIMENT_ID,
        resource_update_config=dict(delay_minutes=0, missing_resources=[], silent=False))
    branch.update(id=code, comparison_id=audit.EXPERIMENT_ID, source_experiment='total_wait_comparison',
        resource_updates_enabled=code in ('updates_stable_C1','total_wait_C1'),
        event_review_enabled=code in ('review_stable_C1','total_wait_C1'))
    branch['policy']['name'] = code
    branch['metrics'].update(update_wall_seconds=.001 if branch['resource_updates_enabled'] else 0,
        event_review_wall_seconds=.001 if branch['event_review_enabled'] else 0)
    old = audit.information._indexed_events(branch)
    unordered = [event for event in branch['events'] if 'event_sequence' not in event]
    events, mapping, publication, packet = [], {}, 0, 1
    def emit(at, kind, **fields):
        row = dict(t=at,type=kind,event_sequence=len(events),**fields)
        events.append(row)
        return row
    for at in range(scenario['max_minutes']+1):
        for event in (row for row in old if row['t']==at):
            mapping[event['event_sequence']] = len(events)
            copied = deepcopy(event)
            copied['event_sequence'] = len(events)
            events.append(copied)
        for phase in ('pre_queries','after_treatments'):
            if branch['resource_updates_enabled']:
                emit(at, 'resource_update_cycle', phase=phase)
                if at in (5,8) and phase=='pre_queries':
                    payload = {'bed':{'capacity':1,'used':int(at==5)}}
                    emit(at,'resource_update_captured',hospital='H1',publication_id=publication,
                        resources=deepcopy(payload),observed_at=at,receive_at=at)
                    emit(at,'resource_report_received',hospital='H1',publication_id=publication,
                        packet_sequence=packet,source='feed',resources=deepcopy(payload),
                        observed_at=at,received_at=at,applied=True)
                    publication += 1
                    packet += 1
            if branch['event_review_enabled']:
                emit(at, 'event_review_cycle', phase=phase)
    for row in branch['allocation_decisions']+branch['protection_records']:
        row['event_sequence'] = mapping[row['event_sequence']]
    for decision in branch['allocation_decisions']:
        for gate in decision['gate_records']:
            gate['event_sequence'] = mapping[gate['event_sequence']]
            if gate['commit_event_sequence'] is not None:
                gate['commit_event_sequence'] = mapping[gate['commit_event_sequence']]
    branch['events'] = sorted(events+unordered,key=lambda event:event['t'])
    branch['observation_records'] = [deepcopy(e) for e in events if e['type']=='resource_report_received']
    branch['metrics'].update(audit.update_metrics(branch))
    return scenario, branch


class TotalWaitFullBranchTests(unittest.TestCase):
    def test_frozen_baseline_audited_with_no_new_information(self):
        scenario, branch = total_wait_branch('stable_responsive_C1')
        checked = audit.audit_branch(scenario,branch)
        self.assertEqual(checked['counts']['physical_gate_passes'],1)
        self.assertEqual(checked['counts']['resource_update_captures'],0)

    def test_new_publication_stream_keeps_all_old_model_proofs(self):
        scenario, branch = total_wait_branch()
        checked = audit.audit_branch(scenario,branch)
        self.assertEqual(checked['counts']['resource_update_captures'],2)
        self.assertEqual(checked['counts']['stable_solver_original_proofs'],1)
        self.assertEqual(checked['waiting']['total_wait_sum_minutes'],3)

    def test_publication_packet_id_creation_gap_rejected(self):
        scenario, branch = total_wait_branch()
        report = next(e for e in branch['events'] if 'publication_id' in e and e['type']=='resource_report_received')
        report['packet_sequence'] = 30
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'packet creation sequence'):
            audit.audit_branch(scenario,branch)

    def test_extra_report_without_publication_marker_rejected(self):
        scenario, branch = total_wait_branch()
        next(e for e in branch['events'] if 'publication_id' in e and e['type']=='resource_report_received').pop('publication_id')
        with self.assertRaises(audit.TotalWaitAuditError):
            audit.audit_branch(scenario,branch)

    def test_global_episode_optimum_claim_rejected(self):
        scenario, branch = total_wait_branch()
        branch['metrics']['whole_episode_optimal_proven'] = True
        with self.assertRaisesRegex(audit.TotalWaitAuditError, 'whole episode'):
            audit.audit_branch(scenario,branch)

    def test_new_runtime_metric_tampering_rejected(self):
        scenario, branch = total_wait_branch()
        branch['metrics']['resource_publications'] = 77
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'raw update/review metric'):
            audit.audit_branch(scenario,branch)

    def test_disabled_publication_runtime_is_zero(self):
        scenario, branch = total_wait_branch('stable_responsive_C1')
        branch['metrics']['update_wall_seconds'] = .1
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'disabled intervention'):
            audit.audit_branch(scenario,branch)



def report_fixture():
    from test_stable_c1_audit import report_fixture as original_report
    policies=deepcopy(original_report()['settings']['policies'])
    for policy in policies:
        policy.update(resource_updates=False,event_review=False)
    for code in audit.POLICY_IDS[len(audit.stable.POLICY_IDS):]:
        policy=deepcopy(policies[-1 if code=='review_stable_C1' else 16])
        policy.update(id=code,label=code,
            resource_updates=code in ('updates_stable_C1','total_wait_C1'),
            event_review=code in ('review_stable_C1','total_wait_C1'))
        policies.append(policy)
    branches=[]
    for code in audit.POLICY_IDS:
        scenario,branch=total_wait_branch(code)
        branches.append(branch)
    by_id={b['id']:b for b in branches}
    case=dict(scenario=scenario,branches=branches,execution_order=list(audit.POLICY_IDS),
        summaries={b['id']:audit.base.raw_branch_metrics(scenario,b) for b in branches},
        information_metrics={b['id']:audit.information_metrics(b) for b in branches},
        guard_metrics={b['id']:audit.guard_metrics(b) for b in branches},
        responsiveness_metrics={b['id']:audit.responsiveness_metrics(b) for b in branches},
        stability_metrics={b['id']:audit.stability_metrics(b) for b in branches},
        waiting_metrics={b['id']:audit.waiting_metrics(scenario,b) for b in branches},
        update_metrics={b['id']:audit.update_metrics(b) for b in branches},
        diagnostics={b['id']:audit._diagnostics(scenario,b) for b in branches},
        paired_comparisons={name:dict(baseline=left,comparison=right,
            metrics=audit.responsive._paired(scenario,by_id[left],by_id[right]),
            arrival_details=audit._paired_tails(scenario,by_id[left],by_id[right]))
            for name,left,right in audit.COMPARISON_PAIRS})
    return dict(schema_version=1,kind='total_wait_comparison',experiment_id=audit.EXPERIMENT_ID,
        phase='development',synthetic=True,seeds=[7],families=['fresh_control'],
        settings=dict(deepcopy(audit.FIXED_SETTINGS),policies=policies),
        source_sha256={'fixture.py':'abc'},runtime_versions={'python':'raw-fixture'},
        lock_sha256=None,results=[case])


class TotalWaitReportTests(unittest.TestCase):
    def test_twenty_branch_report_passes_independent_audit(self):
        checked=audit.audit_report(report_fixture())
        self.assertEqual(checked['branches'],20)
        self.assertEqual(checked['cases'],1)
        self.assertEqual(checked['resource_update_captures'],4)
        self.assertFalse(checked['actual_optimality_claimed'])

    def test_capped_waiting_sum_tampering_rejected(self):
        report=report_fixture()
        report['results'][0]['waiting_metrics']['total_wait_C1']['total_wait_sum_minutes']-=1
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'raw metric mismatch'):
            audit.audit_report(report)

    def test_per_patient_waiting_tampering_rejected(self):
        report=report_fixture()
        report['results'][0]['waiting_metrics']['total_wait_C1']['patient_details'][0]['total_wait_minutes']-=1
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'raw metric mismatch'):
            audit.audit_report(report)

    def test_report_cannot_attribute_new_inputs_to_review_only(self):
        report=report_fixture()
        report['settings']['policies'][-3]['resource_updates']=True
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'ablation differs'):
            audit.audit_report(report)

    def test_branch_ablation_flag_mismatch_rejected(self):
        report=report_fixture()
        report['results'][0]['branches'][-3]['resource_updates_enabled']=True
        with self.assertRaises(audit.TotalWaitAuditError):
            audit.audit_report(report)

    def test_changed_original_inquiry_settings_rejected(self):
        report=report_fixture()
        report['settings']['policies'][-1]['wait']=3
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'inquiry or notification'):
            audit.audit_report(report)

    def test_future_aware_setting_description_rejected(self):
        report=report_fixture()
        report['settings']['resource_updates']['source']='future release prediction'
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'fixed total-wait'):
            audit.audit_report(report)

    def test_missing_waiting_patient_denominator_rejected(self):
        report=report_fixture()
        report['results'][0]['waiting_metrics']['total_wait_C1']['patients']=0
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'raw metric mismatch'):
            audit.audit_report(report)

    def test_paired_survivor_detail_tampering_rejected(self):
        report=report_fixture()
        report['results'][0]['paired_comparisons']['total_wait_C1_vs_stable']['metrics']['arrival']['denominator']=0
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'raw paired'):
            audit.audit_report(report)

    def test_execution_rotation_required(self):
        report=report_fixture()
        report['results'][0]['execution_order'].reverse()
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'execution order'):
            audit.audit_report(report)

    def test_expected_source_manifest_required_when_provided(self):
        report=report_fixture()
        checked=audit.audit_report(report,expected_source_sha256=report['source_sha256'])
        self.assertTrue(checked['provenance_checked'])
        with self.assertRaisesRegex(audit.TotalWaitAuditError,'source hashes'):
            audit.audit_report(report,expected_source_sha256={'fixture.py':'wrong'})

    def test_malformed_report_raises_audit_error(self):
        with self.assertRaises(audit.TotalWaitAuditError):
            audit.audit_report({'kind':'total_wait_comparison','experiment_id':audit.EXPERIMENT_ID,
                                'synthetic':True})


if __name__ == '__main__':
    unittest.main()


