"""Independent raw audit of earlier dispatch using existing delivered evidence.

Only frozen audit helpers are imported. No replay engine, allocation rule,
scenario builder, resource store or solver is executed by this verifier.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics

from . import online_allocation_audit as base
from . import partial_information_audit as information
from . import responsive_confirmation_audit as responsive
from . import stable_c1_audit as stable
from . import total_wait_audit as total
from .h1_online_audit import _diagnostics, _finite, _paired_tails

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = 'existing_info_20261015'
POLICY_IDS = ['stable_responsive_C1', 'review_stable_C1', 'early_stable_C1', 'existing_info_C1']
DEVELOPMENT_SEEDS = [27101, 27102, 27103]
VALIDATION_SEEDS = [28101, 28102, 28103, 28104, 28105, 28106]
FAMILIES = total.FAMILIES + ('dominant_near_slow_other', 'ample_cohort_dominant_offer',
    'nearer_offer_pending', 'competing_scarce_pending', 'dominant_offer_unknown',
    'dominant_offer_stale', 'future_outage_after_early_offer', 'expiring_dominant_offer')
ExistingInfoAuditError = base.OnlineAllocationAuditError
COMPARISON_PAIRS = (
    ('review_vs_stable', 'stable_responsive_C1', 'review_stable_C1'),
    ('early_vs_stable', 'stable_responsive_C1', 'early_stable_C1'),
    ('existing_vs_stable', 'stable_responsive_C1', 'existing_info_C1'),
    ('existing_vs_review', 'review_stable_C1', 'existing_info_C1'),
    ('existing_vs_early', 'early_stable_C1', 'existing_info_C1'))


def _require(condition, message):
    base._require(condition, message)


class _EarlyWorkflow(responsive._KnownWorkflow):
    """Use emitted early eligibility markers only for ready-set maturity."""

    def __init__(self, scenario):
        super().__init__(scenario)
        self.specs = deepcopy(self.specs)
        self.promoted = set()
        self.positions = {p['id']: p.get('location') for p in scenario['patients']}
        self.failed = {pid: set() for pid in self.specs}

    def apply(self, event):
        kind, pid, hid, at = event['type'], event.get('patient'), event.get('hospital'), event['t']
        if kind == 'early_offer_ready':
            self.promoted.add((pid, at))
        elif kind == 'condition_update' and pid in self.specs:
            self.specs[pid]['required_capabilities'] = event['required_capabilities']
        elif kind in ('arrive', 'transfer_search') and pid in self.positions:
            self.positions[pid] = [self.hospitals[hid]['lat'], self.hospitals[hid]['lon']]
        elif kind == 'arrival_unable' and pid in self.failed:
            self.failed[pid].add(hid)
        super().apply(event)

    def snapshot(self, now):
        ready, waiting = [], []
        for pid in self.specs:
            if self.states.get(pid) != 'SEARCH':
                continue
            row = self._base_patient(pid)
            valid = [hid for (patient, hid), expiry in self.offers.items()
                     if patient == pid and expiry > now]
            if not valid:
                continue
            eligible, blocked, checked = [], [], []
            for hid in valid:
                views = information._views('observed', self.observations, self.truth,
                                           hid, row['demand'], now)
                reason = information._check_reason(views, row['demand'])
                if reason is None:
                    eligible.append(hid)
                elif reason in ('UNKNOWN', 'STALE'):
                    blocked.append(dict(hospital=hid, accepted_at=self.accepted[pid, hid],
                        expires_at=self.offers[pid, hid], reason=reason, resources=views))
                check = self.allocation_checks.get((pid, hid))
                if check is not None and check['t'] == now and check['eligible']:
                    checked.append(hid)
            pending = sum(count for (patient, _), count in self.asks.items() if patient == pid)
            mature = not pending or now - self.first_valid[pid] >= 2 or (pid, now) in self.promoted
            if checked and mature:
                ready.append(dict(row, options=checked,
                    valid_offer_expiries={hid: self.offers[pid, hid] for hid in checked}))
            elif not eligible and blocked:
                waiting.append(dict(row, offers=blocked))
        return dict(t=now, ready=ready, waiting=waiting,
            hospitals=[dict(id=hid, capabilities=sorted(set(h.get('capabilities', []))))
                       for hid, h in self.hospitals.items()])


def _early_snapshot(workflow, pid, at):
    """Rebuild the detached policy input from past reports and current workflow."""
    row = workflow._base_patient(pid)
    patient = {key: row[key] for key in
               ('id', 'priority', 'request_t', 'demand', 'required_capabilities')}
    origin = workflow.positions[pid]
    _require(isinstance(origin, (tuple, list)) and len(origin) == 2,
             'early review lacks declared current patient position')
    candidates = [hid for hid in row['compatible_hospitals']
        if all(workflow.hospitals[hid].get('capacity', {}).get(resource, 0) >= amount
                for resource, amount in row['demand'].items())]
    candidates.sort()
    structural = [dict(hospital=hid,
        travel_minutes=total._synthetic_minutes(origin, workflow.hospitals[hid])) for hid in candidates]
    offers = [dict(hospital=hid, accepted_at=workflow.accepted[pid, hid], expires_at=expiry,
        resources=information._views('observed', workflow.observations, workflow.truth,
                                     hid, row['demand'], at),
        travel_minutes=total._synthetic_minutes(origin, workflow.hospitals[hid]))
        for (patient_id, hid), expiry in sorted(workflow.offers.items())
        if patient_id == pid and expiry > at]
    cohort = [workflow._base_patient(patient_id) for patient_id in workflow.specs
              if workflow.states.get(patient_id) == 'SEARCH']
    cohort.sort(key=lambda other: other['id'])
    pending = sorted(hid for (patient_id, hid), count in workflow.asks.items()
                     if patient_id == pid for _ in range(count))
    return dict(t=at, patient=patient, first_valid_t=workflow.first_valid[pid],
                pending_hospitals=pending, valid_offers=offers,
                structural_candidates=structural, search_patients=cohort)


def _early_expected(snapshot):
    """Independent current-evidence dominance test; no future response schedule."""
    demand, at = snapshot['patient']['demand'], snapshot['t']
    available = [(offer['travel_minutes'], offer['hospital'], offer)
        for offer in snapshot['valid_offers'] if offer['expires_at'] > at
        and information._check_reason(offer['resources'], demand) is None]
    if not available:
        return dict(eligible=False, hospital=None, reason='NO_FRESH_OFFER')
    minutes, hid, offer = min(available, key=lambda item: item[:2])
    if any((row['travel_minutes'], row['hospital']) < (minutes, hid)
           for row in snapshot['structural_candidates']):
        return dict(eligible=False, hospital=None, reason='NOT_DOMINANT')
    cohort = [row for row in snapshot['search_patients'] if hid in row['compatible_hospitals']]
    if any(offer['resources'][resource]['free'] < sum(row['demand'].get(resource, 0) for row in cohort)
           for resource in demand):
        return dict(eligible=False, hospital=None, reason='SHARED_CAPACITY')
    return dict(eligible=True, hospital=hid, reason='EARLY_READY')


def audit_no_added_source(scenario, branch):
    _require(branch.get('resource_updates_enabled') is False,
             'existing-information branch enabled an additional reporting source')
    for event in branch['events']:
        _require(event['type'] not in ('resource_update_cycle', 'resource_update_captured')
                 and 'publication_id' not in event,
                 'existing-information branch contains an additional resource publication')
        if event['type'] == 'resource_report_received':
            _require(event['source'] in ('initial', 'feed', 'confirmation'),
                     'unknown additional resource reporting channel')
    return dict(added_resource_publications=0)


def audit_early_offers(scenario, branch):
    """Check every detached early-review input, decision and resulting check."""
    enabled = branch['early_offers_enabled']
    _require(type(enabled) is bool, 'missing explicit early-offer mode')
    records = branch['early_offer_records']
    _require(isinstance(records, list), 'invalid early-offer records')
    workflow = _EarlyWorkflow(scenario)
    events = information._indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    expected, cycles, used = [], [], set()
    successful = 0
    for event in events:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        if kind == 'early_offer_cycle':
            _require(enabled, 'early-offer cycle on disabled branch')
            _require(not expected, 'previous early cycle omitted eligible review target')
            _require(set(event) == {'t', 'type', 'event_sequence'},
                     'early-offer cycle carries unknown policy fields')
            cycles.append(at)
            normal_ready = {row['id'] for row in workflow.snapshot(at)['ready']}
            for pid in sorted(workflow.specs, key=lambda patient: (
                    -workflow.specs[patient].get('priority', 4),
                    workflow.specs[patient].get('search_start_t', 0), patient)):
                if workflow.states.get(pid) != 'SEARCH' or pid in normal_ready:
                    continue
                pending = sum(count for (patient, _), count in workflow.asks.items() if patient == pid)
                valid = any(patient == pid and expiry > at for (patient, _), expiry in workflow.offers.items())
                first = workflow.first_valid.get(pid)
                if pending and valid and first is not None and at - first < 2:
                    expected.append(pid)
        elif kind == 'early_offer_review':
            _require(enabled and expected, 'unexpected early-offer review target')
            index = base._integer(event['record_index'], 'invalid early-offer record index')
            _require(index == len(used) and index < len(records),
                     'missing, duplicated or reordered early-offer record')
            used.add(index)
            record = records[index]
            pid = expected.pop(0)
            _require(set(event) == {'t', 'type', 'event_sequence', 'record_index'},
                     'early-offer marker carries unknown or future policy fields')
            _require(record['event_sequence'] == seq, 'early-offer record event sequence differs')
            _require(base._same(record['snapshot'], _early_snapshot(workflow, pid, at)),
                     'early-offer snapshot differs from earlier delivered evidence or current cohort')
            result = _early_expected(record['snapshot'])
            _require(base._same(record['result'], result),
                     'early-offer decision differs from independent current-information criterion')
            _finite(record['elapsed_seconds'], 'early-offer evaluation runtime')
            _require(set(record) == {'snapshot', 'result', 'event_sequence', 'elapsed_seconds'},
                     'early-offer record carries unknown policy fields')
            if result['eligible']:
                successful += 1
                following = indexed.get(seq + 1)
                _require(following is not None and following['type'] == 'early_offer_ready'
                         and following['t'] == at and following['patient'] == pid
                         and following['hospital'] == result['hospital']
                         and following['review_event_sequence'] == seq,
                         'eligible early offer lacks immediate ready marker')
                _require(set(following) == {'t', 'type', 'event_sequence', 'patient', 'hospital',
                                           'review_event_sequence'},
                         'early-ready marker carries unknown or future fields')
        elif kind == 'early_offer_ready':
            previous = indexed.get(seq - 1)
            _require(previous is not None and previous['type'] == 'early_offer_review'
                     and previous['event_sequence'] == event['review_event_sequence']
                     and records[previous['record_index']]['result']['eligible'],
                     'early-ready marker lacks immediately preceding positive review')
            row = workflow._base_patient(event['patient'])
            options = [hid for (patient, hid), expiry in workflow.offers.items()
                if patient == event['patient'] and expiry > at and information._check_reason(
                    information._views('observed', workflow.observations, workflow.truth,
                                       hid, row['demand'], at), row['demand']) is None]
            for offset, hid in enumerate(options, 1):
                check = indexed.get(seq + offset)
                _require(check is not None and check['type'] == 'information_check'
                         and check['t'] == at and check['patient'] == event['patient']
                         and check['hospital'] == hid and check['purpose'] == 'allocation'
                         and check['eligible'] is True and check['reason'] is None,
                         'early-ready offer lacks immediate current allocation checks for every eligible offer')
        workflow.apply(event)
    _require(not expected, 'last early cycle omitted eligible review target')
    _require(used == set(range(len(records))), 'orphan early-offer record')
    expected_cycles = list(range(scenario.get('max_minutes', 180) + 1)) if enabled else []
    _require(cycles == expected_cycles, 'missing, duplicated or extra early-offer cycle')
    return dict(early_offer_cycles=len(cycles), early_offer_reviews=len(records),
                early_offer_ready=successful)


def early_offer_metrics(branch):
    events, records = branch['events'], branch['early_offer_records']
    return dict(early_offer_cycles=sum(event['type'] == 'early_offer_cycle' for event in events),
        early_offer_reviews=len(records),
        early_offer_ready=sum(record['result']['eligible'] for record in records),
        early_offer_wall_seconds=branch['metrics']['early_offer_wall_seconds'])


# Frozen protection verifier copied locally solely for earlier ready maturity.
def audit_guard(scenario, branch):
    """Check every protection snapshot, lifetime clock, hold and model subset."""
    enabled = branch.get('protection_enabled')
    grace = branch.get('grace_minutes')
    _require((enabled and type(grace) is int and grace == 3)
             or (enabled is False and grace is None), 'invalid branch protection grace')
    _require(type(enabled) is bool, 'missing explicit protection mode')
    records = branch['protection_records']
    _require(isinstance(records, list), 'invalid protection record list')
    ordered = information._indexed_events(branch)
    by_sequence = {event['event_sequence']: event for event in ordered}
    guard_events = [event for event in ordered if event['type'] == 'opportunity_guard']
    hold_events = [event for event in ordered if event['type'] == 'opportunity_hold']
    if not enabled:
        _require(not records and not guard_events and not hold_events,
                 'unprotected baseline contains opportunity intervention')
        _require(branch['metrics']['protection_wall_seconds'] == 0,
                 'unprotected baseline reports opportunity runtime')
        for key, value in responsive._branch_guard_counts(branch).items():
            _require(branch['metrics'][key] == value, 'raw protection metric mismatch: ' + key)
        return dict(opportunity_evaluations=0, opportunity_hold_checks=0,
                    opportunity_held_patients=0)
    _require(branch['information_mode'] == 'observed' and branch['policy']['rule'] in ('H1', 'C1'),
             'protection applies outside observed H1/C1')
    information._number(branch['metrics']['protection_wall_seconds'], 'protection runtime')
    workflow = _EarlyWorkflow(scenario)
    used_guards, used_holds, used_batches = set(), set(), set()
    eligible_search_checks, covered_ready_checks = {}, set()
    decisions = {row['event_sequence']: row for row in branch['allocation_decisions']}
    for event in ordered:
        if event['type'] == 'information_check' and event['purpose'] == 'allocation' \
                and event['eligible'] and workflow.states.get(event['patient']) == 'SEARCH':
            eligible_search_checks[event['event_sequence']] = event
        if event['type'] == 'opportunity_guard':
            seq, at = event['event_sequence'], event['t']
            index = base._integer(event['record_index'], 'invalid opportunity record index')
            _require(index == len(used_guards) and index < len(records),
                     'missing, duplicated or reordered protection record')
            used_guards.add(index)
            record = records[index]
            expected_snapshot = workflow.snapshot(at)
            _require(expected_snapshot['ready'], 'opportunity guard without current ready patients')
            _require(base._same(record['snapshot'], expected_snapshot),
                     'protection snapshot differs from earlier observed workflow/reports')
            ready_ids = {row['id'] for row in expected_snapshot['ready']}
            covered_ready_checks.update(check_sequence for check_sequence, check in eligible_search_checks.items()
                                        if check['t'] == at and check['patient'] in ready_ids)
            _require(record['grace_minutes'] == grace and record['event_sequence'] == seq,
                     'protection grace/record event differs')
            _require(record['waiting_since'] == workflow.waiting_since,
                     'waiting lifetime clock differs from first observed information block')
            _require(record['held_since'] == workflow.held_since,
                     'held lifetime clock was reset or differs from first actual hold')
            expected = responsive._expected_guard(expected_snapshot, workflow.waiting_since,
                                       deepcopy(workflow.held_since), grace_minutes=grace,
                                       abandon_incomplete=branch.get('prefetch_enabled', False))
            _require(base._same(record['result'], expected),
                     'protection decision differs from bounded independent present-information guard')
            workflow.held_since = deepcopy(expected['held_since_after'])
            next_sequence = seq + 1
            for hold in expected['holds']:
                raw = by_sequence.get(next_sequence)
                _require(raw is not None and raw['type'] == 'opportunity_hold'
                         and raw['t'] == at and base._same(
                             {key: value for key, value in raw.items()
                              if key not in ('t', 'type', 'event_sequence')}, hold),
                         'protection hold lacks unique immediate raw event')
                _require(next_sequence not in used_holds, 'protection hold event reused')
                used_holds.add(next_sequence)
                next_sequence += 1
            if expected['ready_after']:
                marker = by_sequence.get(next_sequence)
                decision = decisions.get(next_sequence)
                _require(marker is not None and marker['type'] == 'allocation_proposal'
                         and marker['t'] == at and decision is not None and decision['kind'] == 'batch',
                         'remaining protected ready patients lack immediate ordinary batch')
                expected_rows = [row for row in expected_snapshot['ready']
                                 if row['id'] in expected['ready_after']]
                model_rows = decision['inputs']['patients']
                _require([row['id'] for row in model_rows] == expected['ready_after']
                         and all(row['options'] == expected_row['options']
                                 for row, expected_row in zip(model_rows, expected_rows)),
                         'ordinary batch differs from protected ready subset/current options')
                used_batches.add(next_sequence)
            else:
                following = by_sequence.get(next_sequence)
                _require(following is None or following['type'] != 'allocation_proposal'
                         or following['t'] != at, 'fully held batch still entered allocation model')
        workflow.apply(event)
    _require(used_guards == set(range(len(records))), 'orphan protection record')
    _require(used_holds == {event['event_sequence'] for event in hold_events}, 'orphan protection hold event')
    _require(used_batches == {row['event_sequence'] for row in branch['allocation_decisions']
                              if row['kind'] == 'batch'}, 'protected batch lacks guard evaluation')
    _require(covered_ready_checks == set(eligible_search_checks),
             'eligible searching patient was silently dropped without guard evaluation')
    for key, value in responsive._branch_guard_counts(branch).items():
        _require(branch['metrics'][key] == value, 'raw protection metric mismatch: ' + key)
    return dict(opportunity_evaluations=len(used_guards),
        opportunity_hold_checks=len(used_holds),
        opportunity_held_patients=len({event['patient'] for event in hold_events}))


def audit_branch(scenario, branch, *, exact_limit=20000):
    added = audit_no_added_source(scenario, branch)
    early = audit_early_offers(scenario, branch)
    checked = total._audit_information_branch(scenario, branch, exact_limit=exact_limit)
    checked['counts'].update(audit_guard(scenario, branch))
    checked['counts'].update(responsive.audit_responsiveness(scenario, branch))
    checked['counts'].update(stable.audit_stability(scenario, branch))
    checked['counts'].update(total.audit_publications(scenario, branch))
    checked['counts'].update(total.audit_event_review(scenario, branch))
    checked['counts'].update(added)
    checked['counts'].update(early)
    updates, early_metrics = total.update_metrics(branch), early_offer_metrics(branch)
    for key, value in {**updates, **early_metrics}.items():
        _require(base._same(branch['metrics'][key], value), 'raw existing-info metric mismatch: ' + key)
    for flag, field in (('resource_updates_enabled', 'update_wall_seconds'),
                        ('event_review_enabled', 'event_review_wall_seconds'),
                        ('early_offers_enabled', 'early_offer_wall_seconds')):
        _finite(branch['metrics'][field], field)
        _require(branch[flag] or branch['metrics'][field] == 0,
                 'disabled intervention reports computation time')
    _require(branch['metrics']['early_offer_wall_seconds'] >=
             sum(record['elapsed_seconds'] for record in branch['early_offer_records']),
             'early-offer total computation time is below evaluations')
    checked.update(guard=responsive.guard_metrics(branch),
        responsiveness=responsive.responsiveness_metrics(branch),
        stability=stable.stability_metrics(branch), waiting=total.waiting_metrics(scenario, branch),
        updates=updates, early_offers=early_metrics)
    return checked




def _audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    _require(report.get('kind') == 'existing_info_comparison'
             and report.get('experiment_id') == EXPERIMENT_ID and report.get('synthetic') is True,
             'wrong existing-info report identity or synthetic scope')
    _require(report['phase'] in ('development', 'validation'), 'invalid report phase')
    _require(isinstance(report['source_sha256'], dict) and report['source_sha256'], 'missing source manifest')
    if expected_source_sha256 is not None:
        _require(report['source_sha256'] == expected_source_sha256, 'source hashes differ from expected manifest')
    _require(isinstance(report['runtime_versions'], dict) and report['runtime_versions'], 'missing runtime manifest')
    seeds, families = report['seeds'], report['families']
    _require(seeds and len(set(seeds)) == len(seeds)
             and all(type(seed) is int and seed >= 0 for seed in seeds), 'invalid or duplicated phase seeds')
    _require(families and len(set(families)) == len(families)
             and all(family in FAMILIES for family in families), 'unknown or duplicated existing-info families')
    if lock is not None:
        _require(lock.get('kind') == 'existing_info_lock' and lock.get('experiment_id') == EXPERIMENT_ID,
                 'wrong development lock identity')
        for key in ('source_sha256', 'runtime_versions', 'settings', 'families'):
            _require(report[key] == lock[key], 'report differs from lock: ' + key)
        _require(lock['development_seeds'] == DEVELOPMENT_SEEDS
                 and lock['validation_seeds'] == VALIDATION_SEEDS
                 and lock['families'] == list(FAMILIES), 'lock differs from preregistered existing-info seeds/families')
        _require(seeds == lock[report['phase'] + '_seeds'], 'phase seed list differs from lock')
        if report['phase'] == 'validation':
            _require(report.get('lock_sha256') == base._digest(lock), 'validation lock digest differs')
    specs = _settings(report)
    seen, totals = set(), Counter()
    for case in report['results']:
        scenario = case['scenario']
        key = scenario['family'], scenario['seed']
        _require(key not in seen and key[0] in families and key[1] in seeds,
                 'unexpected or duplicated family/seed case')
        seen.add(key)
        _require(scenario.get('synthetic') is True and scenario.get('comparison_id') == EXPERIMENT_ID,
                 'scenario lacks synthetic existing-info comparison identity')
        branches = case['branches']
        _require([branch['id'] for branch in branches] == POLICY_IDS, 'branch output order differs')
        offset = (seeds.index(scenario['seed']) + families.index(scenario['family'])) % len(POLICY_IDS)
        _require(case['execution_order'] == POLICY_IDS[offset:] + POLICY_IDS[:offset],
                 'execution order differs from prescribed rotation')
        for name in ('summaries', 'information_metrics', 'guard_metrics',
                     'responsiveness_metrics', 'stability_metrics', 'waiting_metrics', 'update_metrics', 'early_offer_metrics', 'diagnostics'):
            _require(set(case[name]) == set(POLICY_IDS), 'case branch IDs mismatch: ' + name)
        _require(set(case['paired_comparisons']) == {name for name, _, _ in COMPARISON_PAIRS},
                 'existing-info paired comparison IDs mismatch')
        by_id = {}
        for branch in branches:
            code = branch['id']
            by_id[code] = branch
            _require(branch.get('comparison_id') == EXPERIMENT_ID
                     and branch.get('source_experiment') == 'existing_info_comparison',
                     'branch lacks existing-info comparison instrumentation')
            spec = specs[code]
            information._branch_policy(branch, spec)
            _require(type(branch['protection_enabled']) is bool
                     and branch['protection_enabled'] == spec['protection'],
                     'branch protection differs from configured policy')
            _require(type(branch['prefetch_enabled']) is bool and branch['prefetch_enabled'] == spec['prefetch']
                     and type(branch['watch_enabled']) is bool and branch['watch_enabled'] == spec['watch']
                     and branch['grace_minutes'] == spec['grace_minutes']
                     and type(branch['stable_ties_enabled']) is bool
                     and branch['stable_ties_enabled'] == spec['stable_ties']
                     and type(branch['resource_updates_enabled']) is bool
                     and branch['resource_updates_enabled'] == spec['resource_updates']
                     and type(branch['event_review_enabled']) is bool
                     and branch['event_review_enabled'] == spec['event_review']
                     and type(branch['early_offers_enabled']) is bool
                     and branch['early_offers_enabled'] == spec['early_offers'],
                     'branch confirmation/stability flags/grace differ from configured policy')
            checked = audit_branch(scenario, branch, exact_limit=exact_limit)
            for saved, actual in (('summaries', 'metrics'), ('information_metrics', 'information'),
                                  ('guard_metrics', 'guard'), ('responsiveness_metrics', 'responsiveness'),
                                  ('stability_metrics', 'stability'), ('waiting_metrics', 'waiting'),
                                  ('update_metrics', 'updates'), ('early_offer_metrics', 'early_offers')):
                _require(base._same(case[saved][code], checked[actual]), 'raw metric mismatch: ' + saved + '/' + code)
            _require(base._same(case['diagnostics'][code], _diagnostics(scenario, branch)),
                     'raw diagnostic mismatch: ' + code)
            totals.update(checked['counts'])
            totals['branches'] += 1
        for name, left_id, right_id in COMPARISON_PAIRS:
            pair = case['paired_comparisons'][name]
            left, right = by_id[left_id], by_id[right_id]
            _require(pair['baseline'] == left_id and pair['comparison'] == right_id,
                     'paired comparison labels differ: ' + name)
            _require(base._same(pair['metrics'], responsive._paired(scenario, left, right)),
                     'raw paired existing-info/common-patient mismatch: ' + name)
            _require(base._same(pair['arrival_details'], _paired_tails(scenario, left, right)),
                     'raw paired arrival tail mismatch: ' + name)
        totals['cases'] += 1
    _require(seen == {(family, seed) for family in families for seed in seeds},
             'missing family/seed comparison cases')
    if 'summary' in report:
        summary = {row['family']: row for row in report['summary']}
        _require(len(summary) == len(report['summary']) and set(summary) == set(families),
                 'aggregate family IDs mismatch')
        for family, group in summary.items():
            cases = [case for case in report['results'] if case['scenario']['family'] == family]
            _require(set(group['policies']) == set(POLICY_IDS), 'aggregate policy IDs mismatch')
            for code, saved in group['policies'].items():
                expected = dict(cases=len(cases), **{field: statistics.mean(case['summaries'][code][field]
                                for case in cases) for field in information.AGGREGATE_FIELDS})
                _require(base._same(saved, expected), 'aggregate differs from raw case means')
    return dict(totals, experiment_id=EXPERIMENT_ID,
        provenance_checked=expected_source_sha256 is not None or lock is not None,
        assignment_constraint_violations=0, actual_optimality_claimed=False,
        stability_contract='same two objective values only; priority/request/id nearest baseline; no third solve',
        source_contract='existing initial/feed/confirmation only; no added publications; same capture/delay/mask/source clocks',
        waiting_scope='all patients SEARCH+QUEUED+TRANSFER_WAIT through fixed horizon; transit excluded',
        proof_scope='verified_reported_model_only_not_canonical_tie_actual_gate_or_whole_episode')


def audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    """Audit saved artifacts without executing the experiment or allocation rule."""
    _require(type(exact_limit) is int and exact_limit >= 0, 'invalid exact enumeration limit')
    try:
        return _audit_report(report, expected_source_sha256=expected_source_sha256,
                             lock=lock, exact_limit=exact_limit)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise ExistingInfoAuditError('malformed existing-info artifact') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('report', type=Path)
    parser.add_argument('--lock', '--lock-file', dest='lock', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--exact-limit', type=int, default=20000)
    args = parser.parse_args(argv)
    if args.exact_limit < 0:
        parser.error('--exact-limit must be nonnegative')
    payload = json.loads(args.report.read_text(encoding='utf-8'))
    lock = json.loads(args.lock.read_text(encoding='utf-8')) if args.lock else None
    expected = {}
    for name in payload['source_sha256']:
        source = (ROOT / name).resolve()
        _require(source.is_relative_to(ROOT), 'source manifest path escapes project')
        expected[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    result = dict(passed=True, **audit_report(payload, expected_source_sha256=expected,
        lock=lock, exact_limit=args.exact_limit),
        report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
        auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        _require(not args.output.exists(), 'audit output already exists')
        _require(args.output.resolve() not in {args.report.resolve(), args.lock.resolve() if args.lock else None},
                 'audit output would overwrite report or lock')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as output:
            output.write(rendered)
    print(rendered, end='')


FIXED_SETTINGS = deepcopy(total.FIXED_SETTINGS)
FIXED_SETTINGS.update(
    execution_order='rotate four branches by seed index plus family index; canonical output order',
    compute_time='original allocation/confirmation and event/early-offer reviews measured separately; not added to simulation clock',
    tuning='fixed dominant current offer and full registered SEARCH demand check before new development and validation')
FIXED_SETTINGS['resource_updates'] = dict(enabled=False,
    source='existing initial/feed/confirmation channels only; no new publication or proactive quantity query',
    comparison='same observation configuration, capture schedule, missing masks, source clocks and delays; endogenous occupancy payloads may differ')
FIXED_SETTINGS['early_offers'] = dict(
    trigger='after ordinary replies before original opportunity guard; registered SEARCH not ready, pending asks and live offers, first-valid age below two minutes',
    input='detached current valid offers, observed quantities, static candidate routes and all registered SEARCH capability-compatible demand',
    decision='fresh sufficient closest structural candidate; free each demanded resource covers entire compatible registered SEARCH cohort',
    action='mark early-ready and run unchanged opportunity guard, joint C1 and final physical gate',
    limitation='current dominance does not predict a future outage or guarantee no patient harm')
FIXED_SETTINGS['waiting_evaluation']['factors'] = \
    'no resource-change publications; same-report event review and early-offer review separately'


def _settings(report):
    settings = report['settings']
    _require(base._same({key: value for key, value in settings.items() if key != 'policies'}, FIXED_SETTINGS),
             'fixed existing-information experiment settings differ')
    policies = settings['policies']
    _require(isinstance(policies, list) and [policy.get('id') for policy in policies] == POLICY_IDS,
             'comparison requires four canonical existing-information branches')
    fields = {'id', 'label', 'mode', 'rule', 'k', 'wait', 'notify', 'protection',
        'prefetch', 'watch', 'grace_minutes', 'stable_ties', 'time_limit_seconds',
        'seed', 'require_backend', 'resource_updates', 'event_review', 'early_offers'}
    for policy in policies:
        _require(set(policy) == fields and policy['mode'] == 'observed' and policy['rule'] == 'C1',
                 'configured existing-information policy fields/mode/rule mismatch')
        for name in ('notify', 'protection', 'prefetch', 'watch', 'stable_ties', 'require_backend'):
            _require(policy[name] is True, 'existing-information baseline setting differs: ' + name)
        _require(policy['k'] == 3 and policy['wait'] == 2 and policy['grace_minutes'] == 3
                 and policy['time_limit_seconds'] == .05 and policy['seed'] == 0,
                 'existing-information inquiry, protection or solver setting differs')
        _require(policy['resource_updates'] is False,
                 'additional resource reporting source enabled in policy')
        _require(type(policy['event_review']) is bool and
                 policy['event_review'] == (policy['id'] in ('review_stable_C1','existing_info_C1'))
                 and type(policy['early_offers']) is bool and
                 policy['early_offers'] == (policy['id'] in ('early_stable_C1','existing_info_C1')),
                 'existing-information policy ablation differs')
    return {policy['id']: policy for policy in policies}


if __name__ == '__main__':
    main()
