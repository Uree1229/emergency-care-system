"""Hand-authored raw evidence and adversarial mutations, no replay imports."""
from copy import deepcopy
import importlib.abc
from pathlib import Path
import subprocess
import sys
import unittest

from simulation import existing_info_audit as audit


def full_branch(code):
    from test_total_wait_audit import total_wait_branch
    scenario, branch = total_wait_branch('stable_responsive_C1')
    scenario['comparison_id'] = audit.EXPERIMENT_ID
    branch.update(id=code, comparison_id=audit.EXPERIMENT_ID, source_experiment='existing_info_comparison',
        event_review_enabled=code in ('review_stable_C1','existing_info_C1'),
        early_offers_enabled=code in ('early_stable_C1','existing_info_C1'), early_offer_records=[])
    branch['policy']['name'] = code
    branch['metrics'].update(event_review_wall_seconds=.001 if branch['event_review_enabled'] else 0,
        early_offer_wall_seconds=.001 if branch['early_offers_enabled'] else 0)
    old = audit.information._indexed_events(branch)
    unordered = [event for event in branch['events'] if 'event_sequence' not in event]
    events, mapping = [], {}
    for at in range(scenario['max_minutes']+1):
        for row in (event for event in old if event['t']==at):
            mapping[row['event_sequence']] = len(events)
            events.append(dict(deepcopy(row), event_sequence=len(events)))
        if branch['event_review_enabled']:
            for phase in ('pre_queries','after_treatments'):
                events.append(dict(t=at, type='event_review_cycle', phase=phase,event_sequence=len(events)))
        if branch['early_offers_enabled']:
            events.append(dict(t=at,type='early_offer_cycle',event_sequence=len(events)))
    for row in branch['allocation_decisions'] + branch['protection_records']:
        row['event_sequence'] = mapping[row['event_sequence']]
    for decision in branch['allocation_decisions']:
        for gate in decision['gate_records']:
            gate['event_sequence'] = mapping[gate['event_sequence']]
            if gate['commit_event_sequence'] is not None:
                gate['commit_event_sequence'] = mapping[gate['commit_event_sequence']]
    branch['events'] = sorted(events+unordered,key=lambda event:event['t'])
    branch['observation_records'] = [deepcopy(event) for event in events if event['type']=='resource_report_received']
    branch['metrics'].update(audit.total.update_metrics(branch), **audit.early_offer_metrics(branch))
    return scenario, branch


def report_fixture():
    from test_stable_c1_audit import report_fixture as frozen_report
    original = frozen_report()['settings']['policies'][-1]
    branches, policies = [], []
    for code in audit.POLICY_IDS:
        scenario, branch = full_branch(code)
        branches.append(branch)
        policies.append(dict(deepcopy(original),id=code,label=code,resource_updates=False,
            event_review=code in ('review_stable_C1','existing_info_C1'),
            early_offers=code in ('early_stable_C1','existing_info_C1')))
    by = {branch['id']: branch for branch in branches}
    case = dict(scenario=scenario, branches=branches, execution_order=list(audit.POLICY_IDS),
        summaries={b['id']:audit.base.raw_branch_metrics(scenario,b) for b in branches},
        information_metrics={b['id']:audit.information.information_metrics(b) for b in branches},
        guard_metrics={b['id']:audit.responsive.guard_metrics(b) for b in branches},
        responsiveness_metrics={b['id']:audit.responsive.responsiveness_metrics(b) for b in branches},
        stability_metrics={b['id']:audit.stable.stability_metrics(b) for b in branches},
        waiting_metrics={b['id']:audit.total.waiting_metrics(scenario,b) for b in branches},
        update_metrics={b['id']:audit.total.update_metrics(b) for b in branches},
        early_offer_metrics={b['id']:audit.early_offer_metrics(b) for b in branches},
        diagnostics={b['id']:audit._diagnostics(scenario,b) for b in branches},
        paired_comparisons={name:dict(baseline=left,comparison=right,
            metrics=audit.responsive._paired(scenario,by[left],by[right]),
            arrival_details=audit._paired_tails(scenario,by[left],by[right]))
            for name,left,right in audit.COMPARISON_PAIRS})
    return dict(schema_version=1,kind='existing_info_comparison',experiment_id=audit.EXPERIMENT_ID,
        phase='development',synthetic=True,seeds=[7],families=['fresh_control'],
        settings=dict(deepcopy(audit.FIXED_SETTINGS),policies=policies),
        source_sha256={'fixture.py':'abc'},runtime_versions={'python':'raw-fixture'},
        lock_sha256=None,results=[case])


def early_fixture(*, guard=False):
    scenario = dict(max_minutes=2, accept_ttl_minutes=10,
        patients=[dict(id='P', occur_t=0, search_start_t=0, priority=4,
            location=[37,127], resource_demands={'bed':1}, required_capabilities=['general'])],
        hospitals=[dict(id=hid, lat=37, lon=127, capacity={'bed':2}, capabilities=['general'])
                   for hid in ('A', 'Z')])
    events, records, guards, decisions = [], [], [], []
    def emit(at, kind, **fields):
        row = dict(t=at, type=kind, event_sequence=len(events), **fields)
        events.append(row)
        return row
    for sequence, hid in enumerate(('A', 'Z')):
        emit(0, 'resource_report_received', hospital=hid, source='initial', packet_sequence=sequence,
            observed_at=0, received_at=0, resources={'bed':{'capacity':2,'used':0}}, applied=True)
    emit(0, 'request', patient='P')
    emit(0, 'ask', patient='P', hospital='A')
    emit(0, 'ask', patient='P', hospital='Z')
    emit(0, 'early_offer_cycle')
    emit(1, 'accept', patient='P', hospital='A')
    emit(1, 'early_offer_cycle')
    review = emit(1, 'early_offer_review', record_index=0)
    view = dict(capacity=2, used=0, committed=0, queued=0, free=2,
        observed_at=0, received_at=0, sequence=0, source='initial', status='fresh', age_minutes=1)
    patient = dict(id='P', priority=4, request_t=0, demand={'bed':1}, required_capabilities=['general'])
    records.append(dict(event_sequence=review['event_sequence'], elapsed_seconds=.0001,
        snapshot=dict(t=1, patient=patient, first_valid_t=1, pending_hospitals=['Z'],
            valid_offers=[dict(hospital='A', accepted_at=1, expires_at=11, resources={'bed':view}, travel_minutes=1)],
            structural_candidates=[dict(hospital=hid, travel_minutes=1) for hid in ('A','Z')],
            search_patients=[dict(patient, compatible_hospitals=['A','Z'])]),
        result=dict(eligible=True, hospital='A', reason='EARLY_READY')))
    emit(1, 'early_offer_ready', patient='P', hospital='A', review_event_sequence=review['event_sequence'])
    emit(1, 'information_check', patient='P', hospital='A', purpose='allocation',
         resources={'bed':deepcopy(view)}, eligible=True, reason=None)
    if guard:
        marker = emit(1, 'opportunity_guard', record_index=0)
        guards.append(dict(event_sequence=marker['event_sequence'], grace_minutes=3,
            waiting_since={}, held_since={},
            snapshot=dict(t=1, ready=[dict(patient, compatible_hospitals=['A','Z'],
                options=['A'], valid_offer_expiries={'A':11})], waiting=[],
                hospitals=[dict(id=hid, capabilities=['general']) for hid in ('A','Z')]),
            result=dict(held=[], ready_after=['P'], holds=[], reservations=[], held_since_after={})))
        marker = emit(1, 'allocation_proposal', decision_index=0, rule='C1')
        decisions.append(dict(event_sequence=marker['event_sequence'], kind='batch',
            inputs=dict(patients=[dict(id='P', options=['A'])])))
    emit(1, 'commit', patient='P', hospital='A')
    emit(2, 'early_offer_cycle')
    branch = dict(information_mode='observed', resource_updates_enabled=False,
        early_offers_enabled=True, early_offer_records=records, events=events,
        protection_enabled=True, grace_minutes=3, prefetch_enabled=True,
        policy={'rule':'C1'}, protection_records=guards, allocation_decisions=decisions,
        metrics=dict(protection_wall_seconds=.001, opportunity_guard_calls=int(guard),
                     opportunity_hold_checks=0, opportunity_held_patients=0))
    return scenario, branch


def _remove(branch, kind):
    removed = {event['event_sequence'] for event in branch['events'] if event['type'] == kind}
    mapping = {}
    kept = []
    for event in branch['events']:
        old = event['event_sequence']
        if old in removed:
            continue
        mapping[old] = len(kept)
        event['event_sequence'] = len(kept)
        kept.append(event)
    for event in kept:
        if 'review_event_sequence' in event:
            event['review_event_sequence'] = mapping.get(event['review_event_sequence'], -1)
    for record in branch['early_offer_records']:
        record['event_sequence'] = mapping.get(record['event_sequence'], -1)
    branch['events'] = kept


class ExistingInfoEarlyAuditTests(unittest.TestCase):
    def test_present_dominant_offer_can_enter_guard_early(self):
        scenario, branch = early_fixture(guard=True)
        result = audit.audit_early_offers(scenario, branch)
        self.assertEqual(result, dict(early_offer_cycles=3, early_offer_reviews=1, early_offer_ready=1))
        self.assertEqual(audit.audit_guard(scenario, branch)['opportunity_evaluations'], 1)

    def test_existing_initial_feed_confirmation_channels_allowed(self):
        scenario, branch = early_fixture()
        self.assertEqual(audit.audit_no_added_source(scenario, branch)['added_resource_publications'], 0)

    def test_new_resource_publication_forbidden(self):
        scenario, branch = early_fixture()
        branch['events'].append(dict(t=2, type='resource_update_cycle', phase='pre_queries'))
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'additional resource publication'):
            audit.audit_no_added_source(scenario, branch)

    def test_hidden_publication_id_forbidden(self):
        scenario, branch = early_fixture()
        branch['events'][0]['publication_id'] = 0
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'additional resource publication'):
            audit.audit_no_added_source(scenario, branch)

    def test_new_source_flag_forbidden(self):
        scenario, branch = early_fixture()
        branch['resource_updates_enabled'] = True
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'additional reporting source'):
            audit.audit_no_added_source(scenario, branch)

    def test_new_unrecognized_channel_forbidden(self):
        scenario, branch = early_fixture()
        branch['events'][0]['source'] = 'predicted_release'
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'reporting channel'):
            audit.audit_no_added_source(scenario, branch)

    def test_missing_required_review_rejected(self):
        scenario, branch = early_fixture()
        _remove(branch, 'early_offer_review')
        branch['early_offer_records'] = []
        with self.assertRaises(audit.ExistingInfoAuditError):
            audit.audit_early_offers(scenario, branch)

    def test_missing_cycle_rejected(self):
        scenario, branch = early_fixture()
        _remove(branch, 'early_offer_cycle')
        with self.assertRaises(audit.ExistingInfoAuditError):
            audit.audit_early_offers(scenario, branch)

    def test_extra_cycle_rejected(self):
        scenario, branch = early_fixture()
        branch['events'].append(dict(t=2, type='early_offer_cycle', event_sequence=len(branch['events'])))
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'extra early-offer cycle'):
            audit.audit_early_offers(scenario, branch)

    def test_disabled_branch_cannot_review(self):
        scenario, branch = early_fixture()
        branch['early_offers_enabled'] = False
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'disabled branch'):
            audit.audit_early_offers(scenario, branch)

    def test_boolean_flag_required(self):
        scenario, branch = early_fixture()
        branch['early_offers_enabled'] = 1
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'explicit early-offer'):
            audit.audit_early_offers(scenario, branch)

    def test_missing_ready_marker_rejected(self):
        scenario, branch = early_fixture()
        _remove(branch, 'early_offer_ready')
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'immediate ready marker'):
            audit.audit_early_offers(scenario, branch)

    def test_wrong_ready_target_rejected(self):
        scenario, branch = early_fixture()
        next(event for event in branch['events'] if event['type']=='early_offer_ready')['hospital'] = 'Z'
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'immediate ready marker'):
            audit.audit_early_offers(scenario, branch)

    def test_missing_current_allocation_check_rejected(self):
        scenario, branch = early_fixture()
        _remove(branch, 'information_check')
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'every eligible offer'):
            audit.audit_early_offers(scenario, branch)

    def test_no_pending_inquiry_cannot_claim_early_review(self):
        scenario, branch = early_fixture()
        branch['events'] = [event for event in branch['events'] if not
            (event['type']=='ask' and event['hospital']=='Z')]
        for index, event in enumerate(branch['events']):event['event_sequence']=index
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'unexpected early-offer'):
            audit.audit_early_offers(scenario, branch)

    def test_no_acceptance_cannot_claim_early_review(self):
        scenario, branch = early_fixture()
        _remove(branch, 'accept')
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'unexpected early-offer'):
            audit.audit_early_offers(scenario, branch)

    def test_resource_quantity_cannot_be_invented(self):
        scenario, branch = early_fixture()
        branch['early_offer_records'][0]['snapshot']['valid_offers'][0]['resources']['bed']['free'] = 8
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'earlier delivered evidence'):
            audit.audit_early_offers(scenario, branch)

    def test_known_cohort_cannot_be_omitted(self):
        scenario, branch = early_fixture()
        branch['early_offer_records'][0]['snapshot']['search_patients'] = []
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'current cohort'):
            audit.audit_early_offers(scenario, branch)

    def test_future_patient_cannot_enter_cohort(self):
        scenario, branch = early_fixture()
        scenario['patients'].append(dict(id='FUTURE', search_start_t=2, priority=4, location=[37,127],
            resource_demands={'bed':1}, required_capabilities=['general']))
        future = deepcopy(branch['early_offer_records'][0]['snapshot']['search_patients'][0])
        future.update(id='FUTURE', request_t=2)
        branch['early_offer_records'][0]['snapshot']['search_patients'].append(future)
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'current cohort'):
            audit.audit_early_offers(scenario, branch)

    def test_source_age_cannot_be_reset(self):
        scenario, branch = early_fixture()
        branch['early_offer_records'][0]['snapshot']['valid_offers'][0]['resources']['bed']['age_minutes'] = 0
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'earlier delivered evidence'):
            audit.audit_early_offers(scenario, branch)

    def test_first_valid_clock_cannot_be_reset(self):
        scenario, branch = early_fixture()
        branch['early_offer_records'][0]['snapshot']['first_valid_t'] = 0
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'earlier delivered evidence'):
            audit.audit_early_offers(scenario, branch)

    def test_pending_reply_deadline_not_a_policy_input(self):
        scenario, branch = early_fixture()
        branch['early_offer_records'][0]['snapshot']['pending_due_at'] = 5
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'earlier delivered evidence'):
            audit.audit_early_offers(scenario, branch)

    def test_future_restore_field_not_a_policy_input(self):
        scenario, branch = early_fixture()
        branch['early_offer_records'][0]['result']['future_outage_free_until'] = 9
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'independent current-information'):
            audit.audit_early_offers(scenario, branch)

    def test_marker_unknown_fields_rejected(self):
        scenario, branch = early_fixture()
        next(event for event in branch['events'] if event['type']=='early_offer_review')['physical_future'] = {}
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'future policy fields'):
            audit.audit_early_offers(scenario, branch)

    def test_evaluation_runtime_must_be_finite(self):
        scenario, branch = early_fixture()
        branch['early_offer_records'][0]['elapsed_seconds'] = float('nan')
        with self.assertRaises(audit.ExistingInfoAuditError):
            audit.audit_early_offers(scenario, branch)

    def test_orphan_record_rejected(self):
        scenario, branch = early_fixture()
        branch['early_offer_records'].append(deepcopy(branch['early_offer_records'][0]))
        with self.assertRaisesRegex(audit.ExistingInfoAuditError, 'orphan early-offer record'):
            audit.audit_early_offers(scenario, branch)

    def test_nearest_pending_offer_cannot_be_ignored(self):
        snapshot = early_fixture()[1]['early_offer_records'][0]['snapshot']
        snapshot['structural_candidates'][1]['travel_minutes'] = 0
        self.assertEqual(audit._early_expected(snapshot)['reason'], 'NOT_DOMINANT')

    def test_equal_travel_static_tie_break_is_preserved(self):
        snapshot = early_fixture()[1]['early_offer_records'][0]['snapshot']
        snapshot['valid_offers'][0]['hospital'] = 'Z'
        self.assertEqual(audit._early_expected(snapshot)['reason'], 'NOT_DOMINANT')

    def test_shared_resource_competition_blocks_early_offer(self):
        snapshot = early_fixture()[1]['early_offer_records'][0]['snapshot']
        snapshot['search_patients'].append(dict(id='P2', priority=4, request_t=0,
            demand={'bed':2}, required_capabilities=['general'], compatible_hospitals=['A']))
        self.assertEqual(audit._early_expected(snapshot)['reason'], 'SHARED_CAPACITY')

    def test_unknown_or_stale_quantities_do_not_become_ready(self):
        for status in ('unknown','stale'):
            snapshot = deepcopy(early_fixture()[1]['early_offer_records'][0]['snapshot'])
            snapshot['valid_offers'][0]['resources']['bed']['status'] = status
            self.assertEqual(audit._early_expected(snapshot)['reason'], 'NO_FRESH_OFFER')

    def test_current_offer_usable_one_tick_before_expiry(self):
        snapshot = early_fixture()[1]['early_offer_records'][0]['snapshot']
        snapshot['valid_offers'][0]['expires_at'] = 2
        self.assertTrue(audit._early_expected(snapshot)['eligible'])
        snapshot['valid_offers'][0]['expires_at'] = 1
        self.assertEqual(audit._early_expected(snapshot)['reason'], 'NO_FRESH_OFFER')

    def test_guard_cannot_claim_early_maturity_without_marker(self):
        scenario, branch = early_fixture(guard=True)
        _remove(branch, 'early_offer_ready')
        with self.assertRaises(audit.ExistingInfoAuditError):
            audit.audit_guard(scenario, branch)

    def test_auditor_import_does_not_load_simulator_or_solver(self):
        root = Path(__file__).resolve().parents[2]
        program = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('ortools') or fullname in {
            'simulation.existing_info_engine', 'simulation.existing_info_experiment',
            'simulation.existing_info_scenarios', 'simulation.total_wait_engine',
            'simulation.sim', 'simulation.map_replay', 'simulation.resource_observation'}:
            raise RuntimeError('forbidden implementation import: '+fullname)
sys.meta_path.insert(0, Block())
from simulation import existing_info_audit
print('independent')
"""
        result = subprocess.run([sys.executable, '-c', program], cwd=root, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'independent')


class ExistingInfoFullReportTests(unittest.TestCase):
    def test_four_branch_report_passes_independent_audit(self):
        result = audit.audit_report(report_fixture())
        self.assertEqual(result['branches'],4)
        self.assertEqual(result['cases'],1)
        self.assertEqual(result['added_resource_publications'],0)
        self.assertEqual(result['stable_solver_original_proofs'],4)

    def test_all_patient_waiting_sum_tampering_rejected(self):
        report = report_fixture()
        report['results'][0]['waiting_metrics']['existing_info_C1']['total_wait_sum_minutes'] -= 1
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'raw metric mismatch'):
            audit.audit_report(report)

    def test_censored_waiting_patient_denominator_tampering_rejected(self):
        report = report_fixture()
        report['results'][0]['waiting_metrics']['existing_info_C1']['patients'] = 0
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'raw metric mismatch'):
            audit.audit_report(report)

    def test_individual_patient_waiting_tampering_rejected(self):
        report = report_fixture()
        report['results'][0]['waiting_metrics']['existing_info_C1']['patient_details'][0]['total_wait_minutes'] -= 1
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'raw metric mismatch'):
            audit.audit_report(report)

    def test_early_ready_count_tampering_rejected(self):
        report = report_fixture()
        report['results'][0]['early_offer_metrics']['existing_info_C1']['early_offer_ready'] = 1
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'raw metric mismatch'):
            audit.audit_report(report)

    def test_extra_reporting_policy_flag_rejected(self):
        report = report_fixture()
        report['settings']['policies'][-1]['resource_updates'] = True
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'additional resource reporting'):
            audit.audit_report(report)

    def test_early_review_ablation_mismatch_rejected(self):
        report = report_fixture()
        report['settings']['policies'][1]['early_offers'] = True
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'ablation differs'):
            audit.audit_report(report)

    def test_branch_early_flag_mismatch_rejected(self):
        report = report_fixture()
        report['results'][0]['branches'][-1]['early_offers_enabled'] = False
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'flags/grace differ'):
            audit.audit_report(report)

    def test_future_aware_setting_description_rejected(self):
        report = report_fixture()
        report['settings']['early_offers']['decision'] = 'use future outage restoration times'
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'fixed existing-information'):
            audit.audit_report(report)

    def test_fixed_original_inquiry_setting_rejected(self):
        report = report_fixture()
        report['settings']['policies'][-1]['wait'] = 0
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'inquiry, protection or solver'):
            audit.audit_report(report)

    def test_execution_rotation_rejected(self):
        report = report_fixture()
        report['results'][0]['execution_order'].reverse()
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'execution order'):
            audit.audit_report(report)

    def test_paired_survivor_denominator_tampering_rejected(self):
        report = report_fixture()
        report['results'][0]['paired_comparisons']['existing_vs_stable']['metrics']['arrival']['denominator'] = 0
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'raw paired'):
            audit.audit_report(report)

    def test_expected_source_manifest_checked(self):
        report = report_fixture()
        self.assertTrue(audit.audit_report(report,
            expected_source_sha256=report['source_sha256'])['provenance_checked'])
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'source hashes'):
            audit.audit_report(report,expected_source_sha256={'fixture.py':'wrong'})

    def test_global_episode_optimality_claim_rejected(self):
        report = report_fixture()
        report['results'][0]['branches'][-1]['metrics']['whole_episode_optimal_proven'] = True
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'whole episode'):
            audit.audit_report(report)

    def test_disabled_intervention_computation_time_rejected(self):
        report = report_fixture()
        report['results'][0]['branches'][0]['metrics']['early_offer_wall_seconds'] = .1
        with self.assertRaisesRegex(audit.ExistingInfoAuditError,'disabled intervention'):
            audit.audit_report(report)

    def test_malformed_report_raises_audit_error(self):
        with self.assertRaises(audit.ExistingInfoAuditError):
            audit.audit_report(dict(kind='existing_info_comparison',experiment_id=audit.EXPERIMENT_ID,
                                   synthetic=True))


if __name__ == '__main__':
    unittest.main()
