"""Raw-JSON fixtures and tampering tests, without simulation/guard imports."""
from copy import deepcopy
import importlib.abc
import json
from pathlib import Path
import subprocess
import sys
import unittest

from simulation.responsive_confirmation_audit import (
    EXPERIMENT_ID, FIXED_SETTINGS, POLICY_IDS, COMPARISON_PAIRS, ResponsiveAuditError,
    _expected_guard, _paired, audit_branch, audit_guard, audit_report, guard_metrics,
    audit_responsiveness, responsiveness_metrics,
)
from simulation.h1_online_audit import _diagnostics, _paired_tails
from simulation import partial_information_audit as information
from test_partial_information_audit import branch_fixture


def _policies():
    rows = []
    for code in POLICY_IDS:
        prefix, rule = code.split('_')
        enabled = prefix in ('protected', 'early', 'watch', 'responsive')
        row = dict(id=code, label=code, mode='observed' if enabled else prefix,
                   rule=rule, k=3, wait=2, notify=True, protection=enabled, prefetch=prefix in ('early', 'responsive'),
                   watch=prefix in ('watch', 'responsive'),
                   grace_minutes=3 if enabled else None)
        if rule == 'H1':
            row.update(time_limit_seconds=.05, max_moves=2048)
        elif rule == 'C1':
            row.update(time_limit_seconds=.05, seed=0, require_backend=True)
        rows.append(row)
    return rows


def complete_branch(rule='H1', mode='observed', protection=True, prefix=None):
    scenario, branch = branch_fixture(rule, mode)
    scenario['comparison_id'] = EXPERIMENT_ID
    prefix = prefix or ('protected' if protection else mode)
    branch.update(id=prefix + '_' + rule,
                  comparison_id=EXPERIMENT_ID, source_experiment='responsive_confirmation_comparison',
                  protection_enabled=protection, protection_records=[],
                  prefetch_enabled=prefix in ('early', 'responsive'), watch_enabled=prefix in ('watch', 'responsive'),
                  grace_minutes=3 if protection else None)
    branch['policy']['name'] = branch['id']
    branch['metrics'].update(protection_wall_seconds=.001 if protection else 0,
        opportunity_guard_calls=int(protection), opportunity_hold_checks=0, opportunity_held_patients=0,
        prefetch_checks=0, watch_checks=0, watch_cycles=0, report_driven_wakes=0,
        confirmation_intervention_wall_seconds=.001 if prefix in ('early', 'watch', 'responsive') else 0)
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
        result=result, grace_minutes=branch['grace_minutes'], event_sequence=insertion)]
    if branch['watch_enabled']:
        _add_watch_cycles(scenario, branch)
    return scenario, branch


def _add_watch_cycles(scenario, branch):
    ordered = [e for e in branch['events'] if 'event_sequence' in e]
    unordered = [e for e in branch['events'] if 'event_sequence' not in e]
    start = len(ordered)
    ordered.extend(dict(t=t, type='resource_watch_cycle', watched_hospitals=[], event_sequence=start + t)
                   for t in range(scenario['max_minutes'] + 1))
    ordered.sort(key=lambda e: (e['t'], e['event_sequence']))
    mapping = {e['event_sequence']: i for i, e in enumerate(ordered)}
    for e in ordered:
        e['event_sequence'] = mapping[e['event_sequence']]
    for e in branch['observation_records'] + branch['allocation_decisions'] + branch['protection_records']:
        e['event_sequence'] = mapping[e['event_sequence']]
    for decision in branch['allocation_decisions']:
        for gate in decision['gate_records']:
            gate['event_sequence'] = mapping[gate['event_sequence']]
            if gate['commit_event_sequence'] is not None:
                gate['commit_event_sequence'] = mapping[gate['commit_event_sequence']]
    branch['events'] = sorted(ordered + unordered, key=lambda e: e['t'])
    branch['metrics']['watch_cycles'] = scenario['max_minutes'] + 1


def report_fixture():
    branches, scenario = [], None
    for code in POLICY_IDS:
        prefix, rule = code.split('_')
        protected = prefix in ('protected', 'early', 'watch', 'responsive')
        scenario, branch = complete_branch(rule, 'observed' if protected else prefix, protected, prefix=prefix)
        branches.append(branch)
    scenario['comparison_id'] = EXPERIMENT_ID
    by_id = {branch['id']: branch for branch in branches}
    summaries = {branch['id']: information.base.raw_branch_metrics(scenario, branch) for branch in branches}
    case = dict(scenario=scenario, branches=branches, execution_order=list(POLICY_IDS),
        summaries=summaries,
        information_metrics={branch['id']: information.information_metrics(branch) for branch in branches},
        guard_metrics={branch['id']: guard_metrics(branch) for branch in branches},
        diagnostics={branch['id']: _diagnostics(scenario, branch) for branch in branches},
        responsiveness_metrics={branch['id']: responsiveness_metrics(branch) for branch in branches},
        paired_comparisons={name:dict(baseline=left, comparison=right,
            metrics=_paired(scenario, by_id[left], by_id[right]),
            arrival_details=_paired_tails(scenario, by_id[left], by_id[right]))
            for name,left,right in COMPARISON_PAIRS})
    return dict(schema_version=1, kind='responsive_confirmation_comparison', experiment_id=EXPERIMENT_ID,
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
    branch = dict(protection_enabled=True, grace_minutes=3, prefetch_enabled=False, watch_enabled=False, information_mode='observed', policy={'rule': 'H1'},
        events=events, allocation_decisions=decisions, protection_records=records,
        metrics=dict(protection_wall_seconds=.003, opportunity_guard_calls=3,
                     opportunity_hold_checks=2, opportunity_held_patients=1))
    return scenario, branch


def responsiveness_fixture(*, complete=True):
    """Hand-authored parallel check and hospital-wide follow-up, no allocator."""
    end = 4 if complete else 8
    scenario = dict(max_minutes=end, retry_minutes=3, accept_ttl_minutes=10,
        patients=[dict(id='W', search_start_t=0, priority=4,
            resource_demands={'ed_bed':1, 'team':1}, required_capabilities=['special'])],
        hospitals=[dict(id='H', capabilities=['special'], capacity={'ed_bed':1,'team':1})])
    events = []
    def event(at, kind, **fields):
        row = dict(t=at, type=kind, event_sequence=len(events), **fields)
        events.append(row)
        return row
    event(0, 'resource_report_received', hospital='H',
        resources={'ed_bed':{'capacity':1,'used':0},'team':None}, observed_at=0,
        received_at=0, packet_sequence=0, source='initial', applied=True)
    event(0, 'request', patient='W')
    check = event(0, 'information_check', patient='W', hospital='H',
        purpose='inquiry', eligible=True, reason='UNKNOWN', resources={})
    event(0, 'resource_prefetch', patient='W', hospital='H', reason='UNKNOWN',
          inquiry_check_sequence=check['event_sequence'])
    event(0, 'information_check', patient='W', hospital='H',
        purpose='allocation', eligible=False, reason='UNKNOWN', resources={})
    event(0, 'information_wait', patient='W', hospital='H', reason='UNKNOWN')
    event(0, 'resource_confirmation_request', patient='W', hospital='H', request_id=1)
    for at in range(end + 1):
        report = None
        if at == 3:
            report = event(at, 'resource_report_received', hospital='H',
                resources={'ed_bed':{'capacity':1,'used':0},
                           'team':{'capacity':1,'used':0} if complete else None},
                observed_at=at, received_at=at, packet_sequence=1, source='confirmation', applied=True)
        event(at, 'resource_watch_cycle', watched_hospitals=['H'])
        if at == 3 and complete:
            event(at, 'report_driven_wake', patient='W', hospital='H',
                report_event_sequence=report['event_sequence'], previous_retry_at=8)
            event(at, 'information_check', patient='W', hospital='H',
                purpose='inquiry', eligible=True, reason=None, resources={})
            event(at, 'ask', patient='W', hospital='H')
        if at == 6 and not complete:
            event(at, 'resource_watch_check', patient='W', hospital='H', reason='UNKNOWN')
            event(at, 'information_check', patient='W', hospital='H',
                purpose='allocation', eligible=False, reason='UNKNOWN', resources={})
            event(at, 'information_wait', patient='W', hospital='H', reason='UNKNOWN')
            event(at, 'resource_confirmation_request', patient='W', hospital='H', request_id=2)
    branch = dict(information_mode='observed', policy={'rule':'H1'}, prefetch_enabled=True,
        watch_enabled=True, events=events, metrics=dict(confirmation_intervention_wall_seconds=.002,
        prefetch_checks=1, watch_checks=int(not complete), watch_cycles=end + 1,
        report_driven_wakes=int(complete)))
    return scenario, branch


class ResponsivenessAuditTests(unittest.TestCase):
    def test_parallel_prefetch_and_fresh_report_wake(self):
        scenario, branch = responsiveness_fixture()
        self.assertEqual(audit_responsiveness(scenario, branch)['report_driven_wakes'], 1)

    def test_follow_up_does_not_require_valid_patient_acceptance(self):
        scenario, branch = responsiveness_fixture(complete=False)
        self.assertEqual(audit_responsiveness(scenario, branch)['watch_checks'], 1)
        self.assertFalse(any(e['type']=='accept' for e in branch['events']))

    def test_prefetch_cannot_reference_future_inquiry(self):
        scenario, branch = responsiveness_fixture()
        marker = next(e for e in branch['events'] if e['type']=='resource_prefetch')
        marker['inquiry_check_sequence'] = marker['event_sequence'] + 1
        with self.assertRaisesRegex(ResponsiveAuditError, 'earlier unknown/stale'):
            audit_responsiveness(scenario, branch)

    def test_prefetch_must_emit_failed_check(self):
        scenario, branch = responsiveness_fixture()
        next(e for e in branch['events'] if e['type']=='information_check' and e['purpose']=='allocation')['eligible'] = True
        with self.assertRaisesRegex(ResponsiveAuditError, 'failed allocation check'):
            audit_responsiveness(scenario, branch)

    def test_unknown_inquiry_cannot_silently_skip_prefetch(self):
        scenario, branch = responsiveness_fixture()
        next(e for e in branch['events'] if e['type']=='resource_prefetch')['type'] = 'hidden_prefetch'
        with self.assertRaisesRegex(ResponsiveAuditError, 'unique prefetch'):
            audit_responsiveness(scenario, branch)

    def test_follow_up_cannot_be_silently_omitted(self):
        scenario, branch = responsiveness_fixture(complete=False)
        next(e for e in branch['events'] if e['type']=='resource_watch_check')['type'] = 'hidden_watch'
        with self.assertRaisesRegex(ResponsiveAuditError, 'required checks or wakes'):
            audit_responsiveness(scenario, branch)

    def test_hospital_history_cannot_include_unrequested_hospital(self):
        scenario, branch = responsiveness_fixture()
        next(e for e in branch['events'] if e['type']=='resource_watch_cycle')['watched_hospitals'] = ['OTHER']
        with self.assertRaisesRegex(ResponsiveAuditError, 'hospital history'):
            audit_responsiveness(scenario, branch)

    def test_future_patient_cannot_be_watched(self):
        scenario, branch = responsiveness_fixture(complete=False)
        scenario['patients'][0]['search_start_t'] = 10
        with self.assertRaisesRegex(ResponsiveAuditError, 'future patient'):
            audit_responsiveness(scenario, branch)

    def test_unknown_confirmation_cannot_trigger_wake(self):
        scenario, branch = responsiveness_fixture()
        report = next(e for e in branch['events'] if e['type']=='resource_report_received' and e['source']=='confirmation')
        report['resources']['team'] = None
        with self.assertRaisesRegex(ResponsiveAuditError, 'complete feasible report'):
            audit_responsiveness(scenario, branch)

    def test_report_wake_must_request_new_patient_acceptance(self):
        scenario, branch = responsiveness_fixture()
        next(e for e in branch['events'] if e['type']=='ask')['type'] = 'hidden_ask'
        with self.assertRaisesRegex(ResponsiveAuditError, 'immediately retry'):
            audit_responsiveness(scenario, branch)

    def test_wake_cannot_use_old_report_reference(self):
        scenario, branch = responsiveness_fixture()
        next(e for e in branch['events'] if e['type']=='report_driven_wake')['report_event_sequence'] = 0
        with self.assertRaisesRegex(ResponsiveAuditError, 'current applied complete feasible'):
            audit_responsiveness(scenario, branch)

    def test_watch_cycles_cannot_be_skipped(self):
        scenario, branch = responsiveness_fixture()
        next(e for e in branch['events'] if e['type']=='resource_watch_cycle' and e['t']==1)['type'] = 'hidden_cycle'
        with self.assertRaisesRegex(ResponsiveAuditError, 'duplicated or skipped'):
            audit_responsiveness(scenario, branch)

    def test_response_metrics_recomputed(self):
        scenario, branch = responsiveness_fixture()
        branch['metrics']['watch_checks'] = 100
        with self.assertRaisesRegex(ResponsiveAuditError, 'raw confirmation metric'):
            audit_responsiveness(scenario, branch)

    def test_earlier_first_wait_releases_without_clock_reset(self):
        _, branch = hold_fixture()
        snapshot = deepcopy(branch['protection_records'][0]['snapshot'])
        snapshot['t'] = 3
        self.assertEqual(_expected_guard(snapshot, {'W':0}, {}, grace_minutes=3)['held'], [])
        self.assertEqual(_expected_guard(snapshot, {'W':1}, {}, grace_minutes=3)['held'], ['F'])

    def test_received_incomplete_confirmation_stops_new_hold(self):
        _, branch = hold_fixture()
        snapshot = deepcopy(branch['protection_records'][0]['snapshot'])
        view = snapshot['waiting'][0]['offers'][0]['resources']['team']
        view.update(source='confirmation', observed_at=2)
        self.assertEqual(_expected_guard(snapshot, {'W':2}, {}, abandon_incomplete=True)['held'], [])
        self.assertEqual(_expected_guard(snapshot, {'W':2}, {}, abandon_incomplete=False)['held'], ['F'])


class GuardAuditTests(unittest.TestCase):
    def test_valid_bounded_hold_and_release(self):
        scenario, branch = hold_fixture()
        self.assertEqual(audit_guard(scenario, branch)['opportunity_hold_checks'], 2)

    def test_snapshot_cannot_include_future_patient(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][0]['snapshot']['waiting'][0]['id'] = 'FUTURE'
        with self.assertRaisesRegex(ResponsiveAuditError, 'snapshot'):
            audit_guard(scenario, branch)

    def test_unknown_quantity_cannot_be_invented(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][0]['snapshot']['waiting'][0]['offers'][0]['resources']['team']['free'] = 1
        with self.assertRaisesRegex(ResponsiveAuditError, 'snapshot'):
            audit_guard(scenario, branch)

    def test_waiting_clock_cannot_reset(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][1]['waiting_since']['W'] = 3
        with self.assertRaisesRegex(ResponsiveAuditError, 'waiting lifetime'):
            audit_guard(scenario, branch)

    def test_held_clock_cannot_reset(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][1]['held_since']['F'] = 3
        with self.assertRaisesRegex(ResponsiveAuditError, 'held lifetime'):
            audit_guard(scenario, branch)

    def test_cannot_hold_at_deadline(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][2]['result']['held'] = ['F']
        with self.assertRaisesRegex(ResponsiveAuditError, 'bounded independent'):
            audit_guard(scenario, branch)

    def test_cannot_hide_hold_event(self):
        scenario, branch = hold_fixture()
        hold = next(event for event in branch['events'] if event['type'] == 'opportunity_hold')
        hold['type'] = 'hidden_hold'
        with self.assertRaisesRegex(ResponsiveAuditError, 'immediate raw event'):
            audit_guard(scenario, branch)

    def test_cannot_allocate_held_patient(self):
        scenario, branch = hold_fixture()
        branch['protection_records'][0]['result']['ready_after'] = ['F']
        with self.assertRaisesRegex(ResponsiveAuditError, 'bounded independent'):
            audit_guard(scenario, branch)

    def test_batch_must_keep_current_option_edges(self):
        scenario, branch = hold_fixture()
        branch['allocation_decisions'][0]['inputs']['patients'][0]['options'] = ['NEAR']
        with self.assertRaisesRegex(ResponsiveAuditError, 'current options'):
            audit_guard(scenario, branch)

    def test_cannot_silently_drop_ready_evaluation(self):
        scenario, branch = hold_fixture()
        branch['events'].append(dict(t=6, type='information_check', patient='W', hospital='NEAR',
            purpose='allocation', eligible=True, reason=None, resources={}, event_sequence=len(branch['events'])))
        with self.assertRaisesRegex(ResponsiveAuditError, 'silently dropped'):
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
        with self.assertRaisesRegex(ResponsiveAuditError, 'unprotected baseline'):
            audit_guard(scenario, branch)

    def test_full_branch_composes_frozen_model_audit(self):
        for rule in ('H1', 'C1'):
            with self.subTest(rule=rule):
                scenario, branch = complete_branch(rule)
                self.assertEqual(audit_branch(scenario, branch)['counts']['opportunity_evaluations'], 1)


class ReportAuditTests(unittest.TestCase):
    def test_valid_fourteen_branch_report(self):
        result = audit_report(report_fixture(), expected_source_sha256={'fixture.py': 'abc'})
        self.assertEqual((result['cases'], result['branches']), (1, 14))
        self.assertFalse(result['actual_optimality_claimed'])

    def test_branch_mode_or_protection_mismatch(self):
        report = report_fixture()
        report['results'][0]['branches'][-1]['protection_enabled'] = False
        with self.assertRaisesRegex(ResponsiveAuditError, 'branch protection'):
            audit_report(report)

    def test_no_hidden_policy_tuning(self):
        report = report_fixture()
        report['settings']['policies'][-1]['wait'] = 3
        with self.assertRaisesRegex(ResponsiveAuditError, 'inquiry'):
            audit_report(report)

    def test_source_hash_mismatch(self):
        with self.assertRaisesRegex(ResponsiveAuditError, 'source hashes'):
            audit_report(report_fixture(), expected_source_sha256={'fixture.py': 'different'})

    def test_fixed_guard_setting_mismatch(self):
        report = report_fixture()
        report['settings']['protection']['grace_minutes'] = 4
        with self.assertRaisesRegex(ResponsiveAuditError, 'fixed responsive'):
            audit_report(report)

    def test_missing_branch_or_case_rejected(self):
        report = report_fixture()
        report['results'][0]['branches'].pop()
        with self.assertRaisesRegex(ResponsiveAuditError, 'branch output'):
            audit_report(report)
        report = report_fixture()
        report['results'] = []
        with self.assertRaisesRegex(ResponsiveAuditError, 'missing family'):
            audit_report(report)

    def test_raw_protection_metrics_recomputed(self):
        report = report_fixture()
        report['results'][0]['guard_metrics']['protected_H1']['held_patients'] += 1
        with self.assertRaisesRegex(ResponsiveAuditError, 'raw protection metric'):
            audit_report(report)

    def test_paired_protection_outcome_recomputed(self):
        report = report_fixture()
        report['results'][0]['paired_comparisons']['responsive_H1_vs_protected']['metrics']['arrival']['mean_difference_minutes'] = -99
        with self.assertRaisesRegex(ResponsiveAuditError, 'paired protection'):
            audit_report(report)

    def test_execution_order_enforced(self):
        report = report_fixture()
        report['results'][0]['execution_order'].reverse()
        with self.assertRaisesRegex(ResponsiveAuditError, 'execution order'):
            audit_report(report)

    def test_model_optimality_not_whole_episode(self):
        report = report_fixture()
        report['results'][0]['branches'][-1]['metrics']['whole_episode_optimal_proven'] = True
        with self.assertRaisesRegex(ResponsiveAuditError, 'whole episode'):
            audit_report(report)

    def test_malformed_json_contract_has_audit_error(self):
        with self.assertRaises(ResponsiveAuditError):
            audit_report({'kind': 'responsive_confirmation_comparison', 'experiment_id': EXPERIMENT_ID,
                          'synthetic': True})

    def test_import_does_not_load_simulation_or_guard(self):
        script = '''import importlib.abc, sys
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked=('simulation.opportunity_guard','simulation.opportunity_engine',
                 'simulation.opportunity_experiment','simulation.opportunity_scenarios',
                 'simulation.responsive_confirmation_engine','simulation.responsive_confirmation_experiment',
                 'simulation.responsive_confirmation_scenarios',
                 'simulation.partial_information_engine','simulation.information_observations',
                 'simulation.heuristic_assignment','simulation.sim','ortools')
        if any(fullname == name or fullname.startswith(name+'.') for name in blocked):
            raise RuntimeError('forbidden implementation import: '+fullname)
sys.meta_path.insert(0, Reject())
import simulation.responsive_confirmation_audit
print('independent')
'''
        completed = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                   cwd=Path(__file__).resolve().parents[2])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'independent')


if __name__ == '__main__':
    unittest.main()
