"""Independent synthetic JSON fixtures; no engine, store or solver imports."""
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import importlib.abc
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from simulation.partial_information_audit import (
    EXPERIMENT_ID, FIXED_SETTINGS, POLICY_IDS, PartialInformationAuditError,
    _apply_observation, _observation_view, _TruthLedger, audit_branch, audit_report,
    main,
)
from simulation.online_allocation_audit import raw_branch_metrics, paired_common_metrics
from simulation.h1_online_audit import _diagnostics, _paired_tails


def branch_fixture(rule='H1', mode='observed', *, failed_gate=False):
    if failed_gate and mode != 'observed':
        raise ValueError('this fixture models a fresh but optimistic observation')
    scenario = dict(id='raw-information-fixture', family='fresh_control', seed=7,
        synthetic=True, comparison_id=EXPERIMENT_ID, max_minutes=20, accept_ttl_minutes=10,
        patients=[dict(id='P1', occur_t=0, search_start_t=0, priority=4,
            resource_demands={'bed': 1}, required_capabilities=['general'], treatment_minutes=3)],
        hospitals=[dict(id='H1', capabilities=['general'], capacity={'bed': 1})],
        observation_config=dict(feed_interval_minutes=0, confirmation_delay_minutes=30),
        exogenous_events=[dict(t=2, type='resource_unavailable', until=35,
                             hospital='H1', resource='bed')] if failed_gate else [])
    events = []
    def event(at, kind, **fields):
        value = dict(t=at, type=kind, event_sequence=len(events), **fields)
        events.append(value)
        return value
    report = None
    if mode == 'observed':
        report = event(0, 'resource_report_received', hospital='H1',
            resources={'bed': {'capacity': 1, 'used': 0}}, observed_at=0, received_at=0,
            packet_sequence=0, source='initial', applied=True)
    def view(at):
        return dict(capacity=1, used=0, committed=0, queued=0, free=1,
            observed_at=0 if mode == 'observed' else at,
            received_at=0 if mode == 'observed' else at,
            sequence=0 if mode == 'observed' else None,
            source='initial' if mode == 'observed' else 'perfect_reference',
            status='fresh', age_minutes=at if mode == 'observed' else 0)
    event(0, 'request', patient='P1')
    event(0, 'information_check', patient='P1', hospital='H1', purpose='inquiry',
          eligible=True, reason=None, resources={'bed': view(0)})
    event(0, 'ask', patient='P1', hospital='H1')
    event(1, 'accept', patient='P1', hospital='H1')
    if failed_gate:
        event(2, 'resource_unavailable', hospital='H1', resource='bed', until=35)
    event(3, 'information_check', patient='P1', hospital='H1', purpose='allocation',
          eligible=True, reason=None, resources={'bed': view(3)})
    marker = event(3, 'allocation_proposal', decision_index=0, rule=rule)
    gate = event(3, 'confirmation_gate', patient='P1', hospital='H1', accepted_at=1,
        offer_expires_at=11, resources={'bed': dict(capacity=0 if failed_gate else 1,
            used=0, committed=0, queued=0, free=0 if failed_gate else 1)},
        passed=not failed_gate, reason='RESOURCE_CONFLICT' if failed_gate else None)
    gate_record = deepcopy(gate)
    gate_record['commit_event_sequence'] = None
    if failed_gate:
        event(3, 'confirmation_rejected', patient='P1', hospital='H1', reason='RESOURCE_CONFLICT')
        event(3, 'information_wait', patient='P1', hospital='H1', reason='RESOURCE_CONFLICT')
        event(3, 'resource_confirmation_request', patient='P1', hospital='H1', request_id=1)
        events.append(dict(t=20, type='observation_end', patient='P1', state='UNASSIGNED'))
    else:
        commit = event(3, 'commit', patient='P1', hospital='H1')
        gate_record['commit_event_sequence'] = commit['event_sequence']
        event(5, 'arrive', patient='P1', hospital='H1')
        event(5, 'treatment_start', patient='P1', hospital='H1')
        events.append(dict(t=8, type='treatment_complete', patient='P1', hospital='H1'))
    inputs = dict(patients=[dict(id='P1', priority=4, demand={'bed': 1},
        required_capabilities=['general'], position=[37, 127], request_t=0, options=['H1'],
        accepted_at={'H1': 1}, valid_offer_expiries={'H1': 11})],
        hospitals=[dict(id='H1', capabilities=['general'], capacity={'bed': 1}, used={'bed': 0},
            committed={'bed': 0}, queued={'bed': 0})],
        free_resources=[dict(hospital='H1', resource='bed', free=1)],
        travel_minutes=[dict(patient='P1', hospital='H1', minutes=2)])
    proposed, actual = {'P1': 'H1'}, {'P1': None if failed_gate else 'H1'}
    decision = dict(t=3, event_sequence=marker['event_sequence'], rule=rule,
        kind='single' if rule == 'R1' else 'batch',
        scope='current_patient_accepted_offers' if rule == 'R1' else 'current_same_minute_accepted_offers',
        inputs=inputs, resource_evidence={'H1': {'bed': view(3)}}, proposed_assignments=proposed,
        assignments=actual, proposed_objective=dict(unassigned_priority_weight=0, priority_weighted_travel_minutes=8),
        objective=dict(unassigned_priority_weight=4 if failed_gate else 0,
                       priority_weighted_travel_minutes=0 if failed_gate else 8),
        model_optimal_proven=rule == 'C1', actual_optimal_proven=False,
        method={'R1': 'nearest_current_offer', 'H1': 'heuristic', 'C1': 'cp_sat'}[rule],
        elapsed_seconds=.004, gate_records=[gate_record])
    policy = dict(name=mode + '_' + rule, rule=rule, k=3, wait=2, notify=True,
        near_minutes=None, expand_after=None, inquiry_ranking='travel', load_weight=0,
        load_window=6, adaptive_inquiry=None, observed_inquiry=None)
    heuristic, solver = [], []
    if rule == 'H1':
        policy['heuristic'] = dict(backend='bounded_joint_heuristic', time_limit_seconds=.05,
            max_moves=2048, time_budget_scope='optional_search_after_snapshot_and_baseline')
        decision['heuristic_record_index'] = 0
        heuristic = [dict(t=3, decision_index=0, scope='current_same_minute_accepted_offers',
            assignments=deepcopy(proposed), objective=[0, 8], status='HEURISTIC', optimal_proven=False,
            objective_names=['unassigned_priority_weight', 'priority_weighted_travel_minutes'],
            batch_size=1, edge_count=1, assigned_count=1, baseline_assignments=deepcopy(proposed),
            baseline_objective=[0, 8], baseline_assigned_count=1, time_limit_seconds=.05, max_moves=2048,
            time_budget_scope='optional_search_after_snapshot_and_baseline', wall_seconds=.003,
            preprocessing_seconds=.001, search_seconds=.002, evaluated_moves=0, accepted_moves=0,
            accepted_move_types=[], improvements=[])]
    elif rule == 'C1':
        policy['solver'] = dict(backend='ortools_cp_sat', time_limit_seconds=.05, seed=0,
                               require_backend=True, num_search_workers=1)
        decision['solver_record_index'] = 0
        solver = [dict(t=3, decision_index=0, scope='current_same_minute_accepted_offers',
            assignments=deepcopy(proposed), objective=[0, 8], status='OPTIMAL', optimal_proven=True,
            primary_optimal_proven=True, secondary_optimal_proven=True, batch_size=1, edge_count=1,
            fallback=False, wall_seconds=.003, stages=[
                dict(status='OPTIMAL', objective_name='assigned_priority_weight', objective=4, best_bound=4),
                dict(status='OPTIMAL', objective_name='priority_weighted_travel_minutes', objective=8, best_bound=8)])]
    outcome = dict(id='P1', occur_t=0, search_start_t=0, priority=4,
        first_commit_t=None if failed_gate else 3, final_arrive_t=None if failed_gate else 5,
        treatment_start_t=None if failed_gate else 5, treatment_complete_t=None if failed_gate else 8,
        inquiries=1, redirects=0, transfers=0, state='UNASSIGNED' if failed_gate else 'COMPLETE')
    branch = dict(id=mode + '_' + rule, information_mode=mode, policy=policy,
        comparison_id=EXPERIMENT_ID, source_experiment='partial_information_comparison',
        events=events, observation_records=[deepcopy(report)] if report else [], patient_outcomes=[outcome],
        allocation_decisions=[decision], heuristic_records=heuristic, solver_records=solver,
        metrics=dict(observation_end_t=20, whole_episode_optimal_proven=False, allocation_decisions=1,
            allocation_wall_seconds=.004, heuristic_calls=len(heuristic), solver_calls=len(solver),
            information_wait_checks=int(failed_gate), resource_confirmation_requests=int(failed_gate),
            confirmation_rejections=int(failed_gate), ignored_resource_reports=0))
    return scenario, branch


def report_fixture():
    scenario, _ = branch_fixture()
    branches = [branch_fixture(rule, mode)[1] for mode in ('perfect', 'observed') for rule in ('R1', 'H1', 'C1')]
    policies = []
    for branch in branches:
        mode, rule = branch['id'].split('_')
        spec = dict(id=branch['id'], label='raw fixture', mode=mode, rule=rule, k=3, wait=2, notify=True)
        if rule in ('H1', 'C1'): spec['time_limit_seconds'] = .05
        if rule == 'H1': spec['max_moves'] = 2048
        if rule == 'C1': spec.update(seed=0, require_backend=True)
        policies.append(spec)
    by_id = {branch['id']: branch for branch in branches}
    paired = {}
    for rule in ('R1', 'H1', 'C1'):
        raw = paired_common_metrics(scenario, by_id['perfect_' + rule], by_id['observed_' + rule])
        paired[rule] = {name: {key: value for key, value in row.items()
            if key not in ('patient_ids', 'total_difference_minutes')} for name, row in raw.items()}
    zero_information = dict(information_wait_checks=0, resource_confirmation_requests=0,
        confirmation_rejections=0, ignored_resource_reports=0,
        unknown_allocation_checks=0, stale_allocation_checks=0)
    case = dict(scenario=scenario, branches=branches, execution_order=list(POLICY_IDS),
        summaries={b['id']: raw_branch_metrics(scenario, b) for b in branches},
        information_metrics={b['id']: deepcopy(zero_information) for b in branches},
        diagnostics={b['id']: _diagnostics(scenario, b) for b in branches}, paired_vs_perfect=paired,
        paired_arrival_details={rule: _paired_tails(scenario, by_id['perfect_' + rule], by_id['observed_' + rule])
                                for rule in ('R1', 'H1', 'C1')})
    return dict(kind='partial_information_comparison', experiment_id=EXPERIMENT_ID, synthetic=True,
        phase='development', seeds=[7], families=['fresh_control'], settings=dict(FIXED_SETTINGS, policies=policies),
        source_sha256={'simulation/partial_information_audit.py': 'a' * 64}, runtime_versions={'python': 'raw fixture'},
        results=[case])


def feed_fixture():
    """A captured packet waits five minutes before becoming a policy input."""
    scenario, branch = branch_fixture()
    scenario['observation_config'].update(feed_interval_minutes=20, feed_delay_minutes=5)
    payload = {'bed': {'capacity': 1, 'used': 0}}
    branch['events'].insert(1, dict(t=0, type='resource_report_captured', hospital='H1',
        resources=deepcopy(payload), observed_at=0, receive_at=5, packet_sequence=1))
    arrival_index = next(index for index, event in enumerate(branch['events']) if event['type'] == 'arrive')
    branch['events'].insert(arrival_index, dict(t=5, type='resource_report_received', hospital='H1',
        resources=deepcopy(payload), observed_at=0, received_at=5, packet_sequence=1,
        source='feed', applied=True))
    branch['events'].append(dict(t=20, type='resource_report_captured', hospital='H1',
        resources=deepcopy(payload), observed_at=20, receive_at=25, packet_sequence=2))
    remap, next_sequence = {}, 0
    for event in branch['events']:
        if event['type'] == 'treatment_complete':
            continue
        if 'event_sequence' in event:
            remap[event['event_sequence']] = next_sequence
        event['event_sequence'] = next_sequence
        next_sequence += 1
    decision = branch['allocation_decisions'][0]
    decision['event_sequence'] = remap[decision['event_sequence']]
    for gate in decision['gate_records']:
        gate['event_sequence'] = remap[gate['event_sequence']]
        gate['commit_event_sequence'] = remap[gate['commit_event_sequence']]
    branch['observation_records'] = [deepcopy(event) for event in branch['events']
                                     if event['type'] == 'resource_report_received']
    return scenario, branch


class PartialInformationAuditTests(unittest.TestCase):
    def test_six_branch_raw_report_passes(self):
        result = audit_report(report_fixture())
        self.assertEqual(result['branches'], 6)
        self.assertEqual(result['physical_gate_passes'], 6)
        self.assertEqual(result['cp_sat_model_proofs'], 2)
        self.assertFalse(result['actual_optimality_claimed'])

    def test_optimistic_proposal_and_rejected_actual_are_valid_separate_results(self):
        for rule in ('R1', 'H1', 'C1'):
            with self.subTest(rule=rule):
                scenario, branch = branch_fixture(rule, failed_gate=True)
                result = audit_branch(scenario, branch)
                self.assertEqual(result['counts']['physical_gate_rejections'], 1)
                self.assertEqual(result['metrics']['unassigned'], 1)

    def test_model_optimum_cannot_be_claimed_for_actual_gate(self):
        scenario, branch = branch_fixture('C1', failed_gate=True)
        branch['allocation_decisions'][0]['actual_optimal_proven'] = True
        with self.assertRaisesRegex(PartialInformationAuditError, 'actual confirmation'):
            audit_branch(scenario, branch)

    def test_solver_record_must_describe_proposed_not_actual_plan(self):
        scenario, branch = branch_fixture('C1', failed_gate=True)
        branch['solver_records'][0].update(assignments={'P1': None}, objective=[4, 0])
        with self.assertRaises(PartialInformationAuditError): audit_branch(scenario, branch)

    def test_heuristic_record_must_describe_proposal(self):
        scenario, branch = branch_fixture('H1', failed_gate=True)
        branch['heuristic_records'][0]['assignments'] = {'P1': None}
        with self.assertRaises(PartialInformationAuditError): audit_branch(scenario, branch)

    def test_gate_ledger_cannot_be_faked_after_resource_loss(self):
        scenario, branch = branch_fixture(failed_gate=True)
        for event in branch['events']:
            if event['type'] == 'confirmation_gate': event['resources']['bed']['capacity'] = 1
        with self.assertRaisesRegex(PartialInformationAuditError, 'truth ledger'):
            audit_branch(scenario, branch)

    def test_gate_record_must_match_raw_gate_and_commit(self):
        scenario, branch = branch_fixture()
        branch['allocation_decisions'][0]['gate_records'][0]['commit_event_sequence'] += 1
        with self.assertRaisesRegex(PartialInformationAuditError, 'matching actual commitment'):
            audit_branch(scenario, branch)

    def test_future_source_or_received_packet_is_rejected(self):
        for key in ('observed_at', 'received_at'):
            scenario, branch = branch_fixture()
            branch['events'][0][key] = 4
            with self.subTest(key=key), self.assertRaises(PartialInformationAuditError): audit_branch(scenario, branch)

    def test_missing_observation_cannot_be_replaced_with_known_zero(self):
        scenario, branch = branch_fixture()
        branch['events'][0]['resources']['bed'] = None
        branch['observation_records'] = [deepcopy(branch['events'][0])]
        scenario['observation_config'] = {'initial_reports': [dict(hospital='H1', observed_at=0,
            resources={'bed': None})]}
        with self.assertRaisesRegex(PartialInformationAuditError, 'delivered reports'):
            audit_branch(scenario, branch)

    def test_acceptance_cannot_refresh_old_resource_counts(self):
        scenario, branch = branch_fixture()
        branch['events'][0]['observed_at'] = -11
        branch['observation_records'] = [deepcopy(branch['events'][0])]
        scenario['observation_config'] = {'initial_reports': [dict(hospital='H1', observed_at=-11,
            resources={'bed': {'capacity': 1, 'used': 0}})]}
        with self.assertRaisesRegex(PartialInformationAuditError, 'delivered reports'):
            audit_branch(scenario, branch)

    def test_initial_packet_tamper_is_checked_against_fixed_scenario(self):
        scenario, branch = branch_fixture()
        branch['events'][0]['resources']['bed']['used'] = 1
        branch['observation_records'] = [deepcopy(branch['events'][0])]
        with self.assertRaisesRegex(PartialInformationAuditError, 'configured raw packet'):
            audit_branch(scenario, branch)

    def test_resource_loss_log_must_match_fixed_environment(self):
        scenario, branch = branch_fixture(failed_gate=True)
        for event in branch['events']:
            if event['type'] == 'resource_unavailable': event['until'] = 36
        with self.assertRaisesRegex(PartialInformationAuditError, 'fixed exogenous scenario'):
            audit_branch(scenario, branch)

    def test_invented_capacity_restore_is_not_a_source_of_real_resources(self):
        scenario, branch = branch_fixture(failed_gate=True)
        last = branch['events'].pop()
        branch['events'].append(dict(t=4, type='resource_restored', hospital='H1', resource='bed',
            event_sequence=len(branch['events'])))
        branch['events'].append(last)
        with self.assertRaisesRegex(PartialInformationAuditError, 'fixed exogenous scenario'):
            audit_branch(scenario, branch)

    def test_hospital_capability_cannot_be_invented_by_model(self):
        scenario, branch = branch_fixture()
        scenario['hospitals'][0]['capabilities'] = []
        with self.assertRaisesRegex(PartialInformationAuditError, 'physical confirmation gate'):
            audit_branch(scenario, branch)

    def test_patient_capability_cannot_be_removed_from_model(self):
        scenario, branch = branch_fixture()
        branch['allocation_decisions'][0]['inputs']['patients'][0]['required_capabilities'] = []
        with self.assertRaisesRegex(PartialInformationAuditError, 'patient capabilities'):
            audit_branch(scenario, branch)

    def test_declared_feed_packets_cannot_be_silently_omitted(self):
        scenario, branch = branch_fixture()
        scenario['observation_config']['feed_interval_minutes'] = 1
        with self.assertRaisesRegex(PartialInformationAuditError, 'configured feed capture'):
            audit_branch(scenario, branch)

    def test_configured_no_initial_packets_cannot_use_hidden_initial_truth(self):
        scenario, branch = branch_fixture()
        scenario['observation_config']['initial_reports'] = []
        with self.assertRaisesRegex(PartialInformationAuditError, 'initial resource report'):
            audit_branch(scenario, branch)

    def test_delayed_feed_capture_and_receipt_are_valid(self):
        scenario, branch = feed_fixture()
        self.assertEqual(audit_branch(scenario, branch)['metrics']['unassigned'], 0)

    def test_captured_and_received_payload_cannot_both_forge_source_truth(self):
        scenario, branch = feed_fixture()
        for event in branch['events']:
            if event.get('packet_sequence') == 1:
                event['resources']['bed']['used'] = 1
        branch['observation_records'] = [deepcopy(event) for event in branch['events']
                                         if event['type'] == 'resource_report_received']
        with self.assertRaisesRegex(PartialInformationAuditError, 'physical source ledger'):
            audit_branch(scenario, branch)

    def test_captured_but_not_delivered_packet_cannot_enter_policy_snapshot(self):
        scenario, branch = feed_fixture()
        view = branch['allocation_decisions'][0]['resource_evidence']['H1']['bed']
        view.update(sequence=1, source='feed', received_at=5)
        with self.assertRaisesRegex(PartialInformationAuditError, 'newest received source'):
            audit_branch(scenario, branch)

    def test_feed_receipt_cannot_move_before_configured_network_delivery(self):
        scenario, branch = feed_fixture()
        for event in branch['events']:
            if event['type'] == 'resource_report_received' and event['source'] == 'feed':
                event.update(t=4, received_at=4)
        with self.assertRaisesRegex(PartialInformationAuditError, 'configured network delay'):
            audit_branch(scenario, branch)

    def test_decision_snapshot_must_use_newest_source_not_latest_receipt(self):
        observations = {}
        latest = dict(t=5, hospital='H1', resources={'bed': {'capacity': 3, 'used': 1}},
            observed_at=5, received_at=5, packet_sequence=9, source='feed', applied=True)
        _apply_observation(observations, latest)
        older = dict(t=9, hospital='H1', resources={'bed': {'capacity': 3, 'used': 3}},
            observed_at=1, received_at=9, packet_sequence=8, source='feed', applied=False)
        _apply_observation(observations, older)
        self.assertEqual(_observation_view(observations, 'H1', 'bed', 9, 1, 0)['free'], 1)
        older['applied'] = True
        with self.assertRaises(PartialInformationAuditError): _apply_observation(observations, older)

    def test_exactly_ten_minutes_is_fresh_and_later_is_stale(self):
        observation = {('H1', 'bed'): dict(capacity=2, used=0, observed_at=0,
            received_at=0, sequence=0, source='initial')}
        self.assertEqual(_observation_view(observation, 'H1', 'bed', 10, 1, 0)['status'], 'fresh')
        self.assertEqual(_observation_view(observation, 'H1', 'bed', 11, 1, 0)['status'], 'stale')

    def test_current_commitment_and_arrival_do_not_create_a_free_slot(self):
        scenario, _ = branch_fixture()
        ledger = _TruthLedger(scenario)
        ledger.apply(dict(t=3, type='commit', patient='P1', hospital='H1'))
        self.assertEqual(ledger.resources('H1', ['bed'], 3)['bed']['free'], 0)
        ledger.apply(dict(t=5, type='arrive', patient='P1', hospital='H1'))
        queued = ledger.resources('H1', ['bed'], 5)['bed']
        self.assertEqual((queued['committed'], queued['queued'], queued['free']), (0, 1, 0))
        ledger.apply(dict(t=5, type='treatment_start', patient='P1', hospital='H1'))
        self.assertEqual(ledger.resources('H1', ['bed'], 5)['bed']['free'], 0)
        self.assertEqual(ledger.resources('H1', ['bed'], 8)['bed']['free'], 1)

    def test_decision_emission_sequence_cannot_point_to_a_later_event(self):
        scenario, branch = branch_fixture()
        branch['allocation_decisions'][0]['event_sequence'] += 1
        with self.assertRaisesRegex(PartialInformationAuditError, 'proposal event'):
            audit_branch(scenario, branch)

    def test_tampered_raw_metrics_and_pair_tails_are_rejected(self):
        mutations = (
            lambda c: c['summaries']['observed_H1'].__setitem__('unassigned', 999),
            lambda c: c['information_metrics']['observed_H1'].__setitem__('confirmation_rejections', 999),
            lambda c: c['diagnostics']['observed_H1'].__setitem__('arrival_p95_minutes', 999),
            lambda c: c['paired_vs_perfect']['H1']['arrival'].__setitem__('denominator', 999),
            lambda c: c['paired_arrival_details']['H1'].__setitem__('delayed', 999),
        )
        for mutation in mutations:
            report = report_fixture(); mutation(report['results'][0])
            with self.assertRaises(PartialInformationAuditError): audit_report(report)

    def test_execution_rotation_and_settings_are_checked(self):
        report = report_fixture()
        report['results'][0]['execution_order'].reverse()
        with self.assertRaisesRegex(PartialInformationAuditError, 'rotation'): audit_report(report)
        report = report_fixture(); report['settings']['stale_after_minutes'] = 11
        with self.assertRaises(PartialInformationAuditError): audit_report(report)

    def test_source_and_runtime_lock_mismatch_are_rejected(self):
        report = report_fixture()
        with self.assertRaisesRegex(PartialInformationAuditError, 'source hashes'):
            audit_report(report, expected_source_sha256={'different': 'b' * 64})
        lock = dict(kind='partial_information_lock', experiment_id=EXPERIMENT_ID,
            **{key: deepcopy(report[key]) for key in ('source_sha256', 'runtime_versions', 'settings', 'families')},
            development_seeds=[7], validation_seeds=[8])
        lock['runtime_versions'] = {'python': 'different'}
        with self.assertRaisesRegex(PartialInformationAuditError, 'runtime_versions'): audit_report(report, lock=lock)

    def test_cli_preserves_existing_output(self):
        report = report_fixture()
        source = 'simulation/partial_information_audit.py'
        report['source_sha256'][source] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='.partial-audit-', dir=Path(__file__).resolve().parents[2]) as folder:
            input_path, output_path = Path(folder) / 'report.json', Path(folder) / 'audit.json'
            input_path.write_text(json.dumps(report), encoding='utf-8')
            output_path.write_text('preserve me', encoding='utf-8')
            with self.assertRaisesRegex(PartialInformationAuditError, 'already exists'), redirect_stdout(StringIO()):
                main([str(input_path), '--output', str(output_path)])
            self.assertEqual(output_path.read_text(encoding='utf-8'), 'preserve me')

    def test_auditor_import_does_not_load_runner_store_or_solver(self):
        code = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ('simulation.sim', 'simulation.map_replay', 'simulation.heuristic_assignment',
            'simulation.cp_sat_assignment', 'simulation.information_observations',
            'simulation.partial_information_engine', 'simulation.partial_information_experiment', 'ortools')
        if fullname in blocked or fullname.startswith('ortools.'):
            raise RuntimeError('forbidden audit dependency: ' + fullname)
sys.meta_path.insert(0, Block())
from simulation.partial_information_audit import audit_report
from test_partial_information_audit import report_fixture
assert audit_report(report_fixture())['branches'] == 6
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
