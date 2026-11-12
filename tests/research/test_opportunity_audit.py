"""Raw-JSON fixtures and tampering tests, without simulation/guard imports."""
from copy import deepcopy
import importlib.abc
import json
from pathlib import Path
import subprocess
import sys
import unittest

from simulation.opportunity_audit import (
    EXPERIMENT_ID, FIXED_SETTINGS, POLICY_IDS, OpportunityAuditError,
    _expected_guard, _paired, audit_branch, audit_guard, audit_report, guard_metrics,
)
from simulation.h1_online_audit import _diagnostics, _paired_tails
from simulation import partial_information_audit as information
from test_partial_information_audit import branch_fixture


def _policies():
    rows = []
    for code in POLICY_IDS:
        prefix, rule = code.split('_')
        row = dict(id=code, label=code, mode='observed' if prefix == 'protected' else prefix,
                   rule=rule, k=3, wait=2, notify=True, protection=prefix == 'protected')
        if rule == 'H1':
            row.update(time_limit_seconds=.05, max_moves=2048)
        elif rule == 'C1':
            row.update(time_limit_seconds=.05, seed=0, require_backend=True)
        rows.append(row)
    return rows


def complete_branch(rule='H1', mode='observed', protection=True):
    scenario, branch = branch_fixture(rule, mode)
    scenario['comparison_id'] = EXPERIMENT_ID
    branch.update(id=('protected' if protection else mode) + '_' + rule,
                  comparison_id=EXPERIMENT_ID, source_experiment='opportunity_protection_comparison',
                  protection_enabled=protection, protection_records=[])
    branch['policy']['name'] = branch['id']
    branch['metrics'].update(protection_wall_seconds=.001 if protection else 0,
        opportunity_guard_calls=int(protection), opportunity_hold_checks=0, opportunity_held_patients=0)
    if not protection:
        return scenario, branch
    decision = branch['allocation_decisions'][0]
    insertion = decision['event_sequence']
    for event in branch['events']:
        if event.get('event_sequence', -1) >= insertion:
            event['event_sequence'] += 1
    marker = dict(t=3, type='opportunity_guard', record_index=0, event_sequence=insertion)
    branch['events'].insert(insertion, marker)
    decision['event_sequence'] += 1
    for gate in decision['gate_records']:
        gate['event_sequence'] += 1
        if gate['commit_event_sequence'] is not None:
            gate['commit_event_sequence'] += 1
    patient = dict(id='P1', priority=4, request_t=0, demand={'bed': 1},
        required_capabilities=['general'], compatible_hospitals=['H1'], options=['H1'],
        valid_offer_expiries={'H1': 11})
    snapshot = dict(t=3, ready=[patient], waiting=[],
                    hospitals=[dict(id='H1', capabilities=['general'])])
    result = dict(held=[], ready_after=['P1'], holds=[], reservations=[], held_since_after={})
    branch['protection_records'] = [dict(snapshot=snapshot, waiting_since={}, held_since={},
        result=result, grace_minutes=3, event_sequence=insertion)]
    return scenario, branch


def report_fixture():
    branches, scenario = [], None
    for code in POLICY_IDS:
        prefix, rule = code.split('_')
        protected = prefix == 'protected'
        scenario, branch = complete_branch(rule, 'observed' if protected else prefix, protected)
        branches.append(branch)
    scenario['comparison_id'] = EXPERIMENT_ID
    by_id = {branch['id']: branch for branch in branches}
    summaries = {branch['id']: information.base.raw_branch_metrics(scenario, branch) for branch in branches}
    case = dict(scenario=scenario, branches=branches, execution_order=list(POLICY_IDS),
        summaries=summaries,
        information_metrics={branch['id']: information.information_metrics(branch) for branch in branches},
        guard_metrics={branch['id']: guard_metrics(branch) for branch in branches},
        diagnostics={branch['id']: _diagnostics(scenario, branch) for branch in branches},
        paired_vs_perfect={rule: _paired(scenario, by_id['perfect_' + rule], by_id['observed_' + rule])
                           for rule in ('R1', 'H1', 'C1')},
        paired_arrival_details={rule: _paired_tails(scenario, by_id['perfect_' + rule], by_id['observed_' + rule])
                                for rule in ('R1', 'H1', 'C1')},
        paired_vs_observed={rule: _paired(scenario, by_id['observed_' + rule], by_id['protected_' + rule])
                            for rule in ('H1', 'C1')},
        paired_protection_details={rule: _paired_tails(scenario, by_id['observed_' + rule], by_id['protected_' + rule])
                                   for rule in ('H1', 'C1')})
    return dict(schema_version=1, kind='opportunity_protection_comparison', experiment_id=EXPERIMENT_ID,
        phase='development', synthetic=True, seeds=[7], families=['fresh_control'],
        settings=dict(deepcopy(FIXED_SETTINGS), policies=_policies()),
        source_sha256={'fixture.py': 'abc'}, runtime_versions={'python': 'fixture'},
        lock_sha256=None, results=[case])


def hold_fixture():
    """One flexible ready patient and one blocked single-choice patient."""
    scenario = dict(max_minutes=6, accept_ttl_minutes=10,
        patients=[dict(id='F', search_start_t=0, priority=2, resource_demands={'ed_bed': 1},
                       required_capabilities=['general']),
                  dict(id='W', search_start_t=0, priority=4,
                       resource_demands={'ed_bed': 1, 'team': 1},
                       required_capabilities=['general', 'special'])],
        hospitals=[dict(id='NEAR', capabilities=['general', 'special'], capacity={'ed_bed': 1, 'team': 1}),
                   dict(id='FAR', capabilities=['general'], capacity={'ed_bed': 1})])
    events = []
    def event(at, kind, **fields):
        row = dict(t=at, type=kind, event_sequence=len(events), **fields)
        events.append(row)
        return row
    event(0, 'resource_report_received', hospital='NEAR', resources={'ed_bed': {'capacity': 1, 'used': 0}, 'team': None},
          observed_at=0, received_at=0, packet_sequence=0, source='initial', applied=True)
    event(0, 'resource_report_received', hospital='FAR', resources={'ed_bed': {'capacity': 1, 'used': 0}},
          observed_at=0, received_at=0, packet_sequence=1, source='initial', applied=True)
    for pid in ('F', 'W'):
        event(0, 'request', patient=pid)
    for pid, hid in (('F', 'NEAR'), ('F', 'FAR'), ('W', 'NEAR')):
        event(0, 'ask', patient=pid, hospital=hid)
    for pid, hid in (('F', 'NEAR'), ('F', 'FAR'), ('W', 'NEAR')):
        event(1, 'accept', patient=pid, hospital=hid)
    def bed(at, sequence):
        return dict(capacity=1, used=0, committed=0, queued=0, free=1,
            observed_at=0, received_at=0, sequence=sequence, source='initial', status='fresh', age_minutes=at)
    def team(at):
        return dict(capacity=None, used=None, committed=0, queued=0, free=None,
            observed_at=0, received_at=0, sequence=0, source='initial', status='unknown', age_minutes=at)
    records, decisions = [], []
    for at in (2, 3, 5):
        for hid, sequence in (('NEAR', 0), ('FAR', 1)):
            event(at, 'information_check', patient='F', hospital=hid, purpose='allocation',
                  eligible=True, reason=None, resources={'ed_bed': bed(at, sequence)})
        event(at, 'information_check', patient='W', hospital='NEAR', purpose='allocation',
              eligible=False, reason='UNKNOWN', resources={'ed_bed': bed(at, 0), 'team': team(at)})
        event(at, 'information_wait', patient='W', hospital='NEAR', reason='UNKNOWN')
        flexible = dict(id='F', priority=2, request_t=0, demand={'ed_bed': 1},
            required_capabilities=['general'], compatible_hospitals=['FAR', 'NEAR'],
            options=['NEAR', 'FAR'], valid_offer_expiries={'NEAR': 11, 'FAR': 11})
        waiting = dict(id='W', priority=4, request_t=0, demand={'ed_bed': 1, 'team': 1},
            required_capabilities=['general', 'special'], compatible_hospitals=['NEAR'],
            offers=[dict(hospital='NEAR', accepted_at=1, expires_at=11, reason='UNKNOWN',
                         resources={'ed_bed': bed(at, 0), 'team': team(at)})])
        snapshot = dict(t=at, ready=[flexible], waiting=[waiting], hospitals=[
            dict(id='NEAR', capabilities=['general', 'special']), dict(id='FAR', capabilities=['general'])])
        hold = dict(patient='F', waiting_patient='W', hospital='NEAR', resource='ed_bed',
            known_free=1, demand=1, waiting_since=2, waiting_deadline=5,
            held_since=2, held_deadline=5, deadline=5)
        result = dict(held=['F'] if at < 5 else [], ready_after=[] if at < 5 else ['F'],
            holds=[hold] if at < 5 else [],
            reservations=[dict(waiting_patient='W', hospital='NEAR', resource='ed_bed',
                               quantity=1, priority=4, known_free=1)] if at < 5 else [],
            held_since_after={'F': 2})
        marker = event(at, 'opportunity_guard', record_index=len(records))
        records.append(dict(snapshot=snapshot, waiting_since={'W': 2}, held_since={} if at == 2 else {'F': 2},
            result=result, grace_minutes=3, event_sequence=marker['event_sequence']))
        if at < 5:
            event(at, 'opportunity_hold', **hold)
        else:
            proposal = event(at, 'allocation_proposal', decision_index=0, rule='H1')
            decisions.append(dict(event_sequence=proposal['event_sequence'], kind='batch',
                                  inputs={'patients': [dict(id='F', options=['NEAR', 'FAR'])]}))
            event(at, 'commit', patient='F', hospital='NEAR')
    branch = dict(protection_enabled=True, information_mode='observed', policy={'rule': 'H1'},
        events=events, allocation_decisions=decisions, protection_records=records,
        metrics=dict(protection_wall_seconds=.003, opportunity_guard_calls=3,
                     opportunity_hold_checks=2, opportunity_held_patients=1))
    return scenario, branch


class GuardAuditTests(unittest.TestCase):
    def test_valid_bounded_hold_and_release(self):
        scenario, branch = hold_fixture()
        self.assertEqual(audit_guard(scenario, branch)['opportunity_hold_checks'], 2)

    def test_snapshot_cannot_include_future_patient(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][0]['snapshot']['waiting'][0]['id'] = 'FUTURE'
        with self.assertRaisesRegex(OpportunityAuditError, 'snapshot'):
            audit_guard(scenario, branch)

    def test_unknown_quantity_cannot_be_invented(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][0]['snapshot']['waiting'][0]['offers'][0]['resources']['team']['free'] = 1
        with self.assertRaisesRegex(OpportunityAuditError, 'snapshot'):
            audit_guard(scenario, branch)

    def test_waiting_clock_cannot_reset(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][1]['waiting_since']['W'] = 3
        with self.assertRaisesRegex(OpportunityAuditError, 'waiting lifetime'):
            audit_guard(scenario, branch)

    def test_held_clock_cannot_reset(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][1]['held_since']['F'] = 3
        with self.assertRaisesRegex(OpportunityAuditError, 'held lifetime'):
            audit_guard(scenario, branch)

    def test_cannot_hold_at_deadline(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][2]['result']['held'] = ['F']
        with self.assertRaisesRegex(OpportunityAuditError, 'bounded independent'):
            audit_guard(scenario, branch)

    def test_cannot_hide_hold_event(self):
        scenario, branch = hold_fixture()
        hold = next(event for event in branch['events'] if event['type'] == 'opportunity_hold')
        hold['type'] = 'hidden_hold'
        with self.assertRaisesRegex(OpportunityAuditError, 'immediate raw event'):
            audit_guard(scenario, branch)

    def test_cannot_allocate_held_patient(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][0]['result']['ready_after'] = ['F']
        with self.assertRaisesRegex(OpportunityAuditError, 'bounded independent'):
            audit_guard(scenario, branch)

    def test_batch_must_keep_current_option_edges(self):
        scenario, branch = hold_fixture()
        branch['allocation_decisions'][0]['inputs']['patients'][0]['options'] = ['NEAR']
        with self.assertRaisesRegex(OpportunityAuditError, 'current options'):
            audit_guard(scenario, branch)

    def test_cannot_silently_drop_ready_evaluation(self):
        scenario, branch = hold_fixture()
        branch['events'].append(dict(t=6, type='information_check', patient='W', hospital='NEAR',
            purpose='allocation', eligible=True, reason=None, resources={}, event_sequence=len(branch['events'])))
        with self.assertRaisesRegex(OpportunityAuditError, 'silently dropped'):
            audit_guard(scenario, branch)

    def test_higher_priority_ready_never_held(self):
        scenario, branch = hold_fixture()
        snapshot = deepcopy(branch['protection_records'][0]['snapshot'])
        snapshot['ready'][0]['priority'] = 4
        snapshot['waiting'][0]['priority'] = 2
        self.assertEqual(_expected_guard(snapshot, {'W': 2}, {})['held'], [])

    def test_single_choice_ready_never_held(self):
        scenario, branch = hold_fixture()
        snapshot = deepcopy(branch['protection_records'][0]['snapshot'])
        snapshot['ready'][0]['compatible_hospitals'] = ['NEAR']
        self.assertEqual(_expected_guard(snapshot, {'W': 2}, {})['held'], [])

    def test_stale_bed_never_protected(self):
        scenario, branch = hold_fixture()
        snapshot = deepcopy(branch['protection_records'][0]['snapshot'])
        snapshot['waiting'][0]['offers'][0]['resources']['ed_bed']['status'] = 'stale'
        self.assertEqual(_expected_guard(snapshot, {'W': 2}, {})['held'], [])

    def test_next_tick_ready_expiry_prevents_hold(self):
        scenario, branch = hold_fixture()
        snapshot = deepcopy(branch['protection_records'][0]['snapshot'])
        snapshot['ready'][0]['valid_offer_expiries'] = {'NEAR': 3, 'FAR': 3}
        self.assertEqual(_expected_guard(snapshot, {'W': 2}, {})['held'], [])

    def test_next_tick_waiter_expiry_prevents_hold(self):
        scenario, branch = hold_fixture()
        snapshot = deepcopy(branch['protection_records'][0]['snapshot'])
        snapshot['waiting'][0]['offers'][0]['expires_at'] = 3
        self.assertEqual(_expected_guard(snapshot, {'W': 2}, {})['held'], [])

    def test_deadline_shortened_before_offer_expiry(self):
        scenario, branch = hold_fixture()
        snapshot = deepcopy(branch['protection_records'][0]['snapshot'])
        snapshot['ready'][0]['valid_offer_expiries'] = {'NEAR': 4, 'FAR': 4}
        self.assertEqual(_expected_guard(snapshot, {'W': 2}, {})['holds'][0]['deadline'], 3)

    def test_unprotected_baseline_has_no_intervention(self):
        scenario, branch = complete_branch(protection=False)
        self.assertEqual(audit_guard(scenario, branch)['opportunity_evaluations'], 0)
        branch['protection_records'].append({})
        with self.assertRaisesRegex(OpportunityAuditError, 'unprotected baseline'):
            audit_guard(scenario, branch)

    def test_full_branch_composes_frozen_model_audit(self):
        for rule in ('H1', 'C1'):
            with self.subTest(rule=rule):
                scenario, branch = complete_branch(rule)
                self.assertEqual(audit_branch(scenario, branch)['counts']['opportunity_evaluations'], 1)


class ReportAuditTests(unittest.TestCase):
    def test_valid_eight_branch_report(self):
        result = audit_report(report_fixture(), expected_source_sha256={'fixture.py': 'abc'})
        self.assertEqual((result['cases'], result['branches']), (1, 8))
        self.assertFalse(result['actual_optimality_claimed'])

    def test_branch_mode_or_protection_mismatch(self):
        report = report_fixture()
        report['results'][0]['branches'][-1]['protection_enabled'] = False
        with self.assertRaisesRegex(OpportunityAuditError, 'branch protection'):
            audit_report(report)

    def test_no_hidden_policy_tuning(self):
        report = report_fixture()
        report['settings']['policies'][-1]['wait'] = 3
        with self.assertRaisesRegex(OpportunityAuditError, 'inquiry'):
            audit_report(report)

    def test_source_hash_mismatch(self):
        with self.assertRaisesRegex(OpportunityAuditError, 'source hashes'):
            audit_report(report_fixture(), expected_source_sha256={'fixture.py': 'different'})

    def test_fixed_guard_setting_mismatch(self):
        report = report_fixture()
        report['settings']['protection']['grace_minutes'] = 4
        with self.assertRaisesRegex(OpportunityAuditError, 'fixed opportunity'):
            audit_report(report)

    def test_missing_branch_or_case_rejected(self):
        report = report_fixture()
        report['results'][0]['branches'].pop()
        with self.assertRaisesRegex(OpportunityAuditError, 'branch output'):
            audit_report(report)
        report = report_fixture()
        report['results'] = []
        with self.assertRaisesRegex(OpportunityAuditError, 'missing family'):
            audit_report(report)

    def test_raw_protection_metrics_recomputed(self):
        report = report_fixture()
        report['results'][0]['guard_metrics']['protected_H1']['held_patients'] += 1
        with self.assertRaisesRegex(OpportunityAuditError, 'raw protection metric'):
            audit_report(report)

    def test_paired_protection_outcome_recomputed(self):
        report = report_fixture()
        report['results'][0]['paired_vs_observed']['H1']['arrival']['mean_difference_minutes'] = -99
        with self.assertRaisesRegex(OpportunityAuditError, 'paired protection'):
            audit_report(report)

    def test_execution_order_enforced(self):
        report = report_fixture()
        report['results'][0]['execution_order'].reverse()
        with self.assertRaisesRegex(OpportunityAuditError, 'execution order'):
            audit_report(report)

    def test_model_optimality_not_whole_episode(self):
        report = report_fixture()
        report['results'][0]['branches'][-1]['metrics']['whole_episode_optimal_proven'] = True
        with self.assertRaisesRegex(OpportunityAuditError, 'whole episode'):
            audit_report(report)

    def test_malformed_json_contract_has_audit_error(self):
        with self.assertRaises(OpportunityAuditError):
            audit_report({'kind': 'opportunity_protection_comparison', 'experiment_id': EXPERIMENT_ID,
                          'synthetic': True})

    def test_import_does_not_load_simulation_or_guard(self):
        script = '''import importlib.abc, sys
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked=('simulation.opportunity_guard','simulation.opportunity_engine',
                 'simulation.opportunity_experiment','simulation.opportunity_scenarios',
                 'simulation.partial_information_engine','simulation.information_observations',
                 'simulation.heuristic_assignment','simulation.sim','ortools')
        if any(fullname == name or fullname.startswith(name+'.') for name in blocked):
            raise RuntimeError('forbidden implementation import: '+fullname)
sys.meta_path.insert(0, Reject())
import simulation.opportunity_audit
print('independent')
'''
        completed = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                   cwd=Path(__file__).resolve().parents[2])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'independent')


if __name__ == '__main__':
    unittest.main()
