"""Independently audit responsive resource confirmation from saved raw JSON.

Only frozen audit helpers are imported. No engine, resource store, allocation
implementation, guard implementation, scenario builder or runner is executed.
Reported model proofs remain separate from actual gate and episode outcomes.
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
from .h1_online_audit import _diagnostics, _paired_tails


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = 'responsive_confirmation_20261015'
POLICY_IDS = information.POLICY_IDS + [prefix + '_' + rule for prefix in ('protected', 'early', 'watch', 'responsive') for rule in ('H1', 'C1')]
DEVELOPMENT_SEEDS = [21101, 21102, 21103]
VALIDATION_SEEDS = [22101, 22102, 22103, 22104, 22105, 22106]
FAMILIES = information.FAMILIES + (
    'partial_team_unresolved', 'delayed_team_confirmation', 'confirmation_beyond_grace',
    'waiting_priority_high', 'waiting_priority_low', 'multiple_waiting_patients',
    'no_alternative_hospital', 'expiring_waiting_offer',
    'slow_confirmation_three', 'slow_confirmation_eight', 'persistent_partial_slow',
    'expiry_before_slow_confirmation', 'watch_outage_recovery', 'future_patient_not_yet_active',
)
GRACE_BY_PREFIX = {'perfect': None, 'observed': None, 'protected': 3, 'early': 3, 'watch': 3, 'responsive': 3}
FIXED_SETTINGS = dict(information.FIXED_SETTINGS,
    execution_order='rotate fourteen branches by seed index plus family index; canonical output order',
    compute_time='allocation, protection and confirmation intervention measured separately, not added to simulation clock',
    tuning='fixed parallel-confirmation rule before new development and validation',
    protection=dict(grace_minutes={'protected':3,'early':3,'watch':3,'responsive':3}, resource='ed_bed',
        waiter_structural_choices=1, competitor_minimum_structural_choices=2,
        priority='never defer a higher-priority ready patient',
        clocks='lifetime first unknown/stale wait and first actual hold; never reset',
        offer_expiry='release before the waiting offer or last current ready offer expires on the next tick',
        effect='defer eligible flexible ready patients in priority order until headroom fits, then use ordinary allocation model',
        reservation='no physical reservation or unconfirmed patient allocation'),
    responsiveness=dict(
        prefetch='unknown/stale quantities in parallel with first eligible inquiry; no acceptance required',
        watch='after each tick; currently visible single-hospital SEARCH demand at a previously resource-requested hospital',
        incomplete='stop early/responsive holds after received confirmation still leaves required quantities unknown',
        cooldown='existing per-hospital retry_minutes and one pending confirmation',
        wake='same-minute latest applied report; fresh sufficient quantities, no pending ask or unexpired offer; require new acceptance',
        cold_grace_minutes=3))
COMPARISON_PAIRS = tuple((f'observed_{r}_vs_perfect', f'perfect_{r}', f'observed_{r}')
                        for r in ('R1','H1','C1')) + tuple(
    (f'{mode}_{r}_vs_{baseline}', f'{baseline}_{r}', f'{mode}_{r}')
    for mode, baseline in (('protected','observed'),('early','protected'),('watch','protected'),
                           ('responsive','protected'),('responsive','observed'))
    for r in ('H1','C1'))
ResponsiveAuditError = base.OnlineAllocationAuditError


def _require(condition, message):
    base._require(condition, message)


class _KnownWorkflow:
    """Current workflow using only events already emitted before a snapshot.

    The physical ledger is used solely for current server commitments/queues,
    which this experiment declares immediately known. Resource capacities and
    occupancy in guard inputs always come from delivered source reports.
    """

    def __init__(self, scenario):
        self.scenario = scenario
        self.truth = information._TruthLedger(scenario)
        self.observations = {}
        self.specs = {p['id']: p for p in scenario['patients']}
        self.hospitals = {h['id']: h for h in scenario['hospitals']}
        self.states, self.offers, self.accepted = {}, {}, {}
        self.first_valid, self.asks = {}, Counter()
        self.waiting_since, self.held_since = {}, {}
        self.allocation_checks = {}

    def apply(self, event):
        kind, pid, hid, at = event['type'], event.get('patient'), event.get('hospital'), event['t']
        if kind == 'resource_report_received':
            information._apply_observation(self.observations, event)
        elif kind == 'request':
            _require(pid in self.specs, 'opportunity request for undeclared patient')
            _require(self.specs[pid].get('search_start_t', 0) <= at,
                     'opportunity workflow observes a future patient')
            self.states[pid] = 'SEARCH'
        elif kind == 'ask':
            self.asks[pid, hid] += 1
        elif kind in ('accept', 'decline', 'no_response'):
            self.asks[pid, hid] -= 1
            _require(self.asks[pid, hid] >= 0, 'opportunity reply lacks pending inquiry')
            if kind == 'accept':
                self.offers[pid, hid] = at + self.scenario.get('accept_ttl_minutes', 10)
                self.accepted[pid, hid] = at
                self.first_valid.setdefault(pid, at)
        elif kind == 'expire':
            self.offers.pop((pid, hid), None)
            if not any(key[0] == pid for key in self.offers):
                self.first_valid.pop(pid, None)
        elif kind == 'information_wait' and event['reason'] in ('UNKNOWN', 'STALE'):
            if self.states.get(pid) == 'SEARCH':
                self.waiting_since.setdefault(pid, at)
        elif kind == 'information_check' and event['purpose'] == 'allocation':
            self.allocation_checks[pid, hid] = event
        elif kind == 'confirmation_gate' and not event['passed']:
            self.offers.pop((pid, hid), None)
            if not any(key[0] == pid for key in self.offers):
                self.first_valid.pop(pid, None)
        elif kind == 'commit':
            self.states[pid] = 'TRANSIT'
            self.offers = {key: value for key, value in self.offers.items() if key[0] != pid}
            self.asks = Counter({key: value for key, value in self.asks.items() if key[0] != pid})
            self.first_valid.pop(pid, None)
        elif kind == 'arrive':
            self.states[pid] = 'QUEUED'
        elif kind == 'arrival_unable':
            self.states[pid] = 'TRANSFER_WAIT'
        elif kind == 'transfer_search':
            self.states[pid] = 'SEARCH'
            self.offers = {key: value for key, value in self.offers.items() if key[0] != pid}
            self.asks = Counter({key: value for key, value in self.asks.items() if key[0] != pid})
            self.first_valid.pop(pid, None)
        elif kind == 'treatment_start':
            if pid in self.specs:
                self.states[pid] = 'TREATING'
        elif kind == 'no_compatible':
            self.states[pid] = 'NO_COMPATIBLE'
        self.truth.apply(event)

    def _base_patient(self, pid):
        spec = self.specs[pid]
        caps = set(spec.get('required_capabilities', []))
        return dict(id=pid, priority=spec.get('priority', 4),
            request_t=spec.get('search_start_t', 0),
            demand=dict(spec.get('resource_demands', {'ed_bed': 1})),
            required_capabilities=sorted(caps),
            compatible_hospitals=sorted(hid for hid, hospital in self.hospitals.items()
                                  if caps <= set(hospital.get('capabilities', []))))

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
            mature = not pending or now - self.first_valid[pid] >= 2
            if checked and mature:
                ready.append(dict(row, options=checked,
                    valid_offer_expiries={hid: self.offers[pid, hid] for hid in checked}))
            elif not eligible and blocked:
                waiting.append(dict(row, offers=blocked))
        return dict(t=now, ready=ready, waiting=waiting,
            hospitals=[dict(id=hid, capabilities=sorted(set(h.get('capabilities', []))))
                       for hid, h in self.hospitals.items()])


def guard_metrics(branch):
    """Recompute guard metrics from exported raw evaluations (schema below)."""
    records = branch.get('protection_records', [])
    events = branch['events']
    holds = [event for event in events if event['type'] == 'opportunity_hold']
    return dict(guard_calls=len(records), hold_checks=len(holds),
        held_patients=len({event['patient'] for event in holds}),
        max_hold_age_minutes=max((event['t'] - event['held_since'] for event in holds), default=0),
        max_hold_deadline_minutes=max((event['deadline'] - event['held_since'] for event in holds), default=0),
        protection_wall_seconds=branch['metrics']['protection_wall_seconds'])


def _expected_guard(snapshot, waiting_since, held_since, grace_minutes=3, abandon_incomplete=False):
    """Independent deterministic bounded hold, using detached observations.

    No missing resource quantity is supplied to the assignment model or guessed
    here. A protected patient's latest known bed is only a synchronization
    opportunity; the ordinary fresh-information allocator still decides later.
    """
    now = snapshot['t']
    ready = snapshot['ready']
    held, holds, reservations = [], [], []
    reserved = Counter()
    for waiting in sorted(snapshot['waiting'],
                          key=lambda row: (-row['priority'], row['request_t'], row['id'])):
        wid = waiting['id']
        start = waiting_since.get(wid)
        if start is None or now >= start + grace_minutes \
                or len(waiting['compatible_hospitals']) != 1:
            continue
        hid = waiting['compatible_hospitals'][0]
        offers = waiting['offers']
        if abandon_incomplete:
            offers = [offer for offer in offers if not any(
                view['status'] == 'unknown' and view['source'] == 'confirmation'
                and view['observed_at'] >= start for view in offer['resources'].values())]
        offer = next((offer for offer in offers if offer['hospital'] == hid), None)
        quantity = waiting['demand'].get('ed_bed', 0)
        if offer is None or offer['expires_at'] <= now + 1 or not quantity:
            continue
        bed = offer['resources'].get('ed_bed')
        if bed is None or bed['status'] != 'fresh' or bed['free'] is None or bed['free'] < quantity:
            continue
        remaining = [row for row in ready if row['id'] not in held and hid in row['options']]
        potential = sum(row['demand'].get('ed_bed', 0) for row in remaining)
        deficit = potential + reserved[hid] + quantity - bed['free']
        victims = [row for row in remaining if len(row['compatible_hospitals']) > 1
                   and row['priority'] <= waiting['priority']
                   and row['demand'].get('ed_bed', 0) > 0
                   and max(row['valid_offer_expiries'].values()) > now + 1
                   and now < held_since.get(row['id'], now) + grace_minutes]
        # Lowest priority, newest request, then largest identifier are delayed.
        victims.sort(key=lambda row: row['id'], reverse=True)
        victims.sort(key=lambda row: row['request_t'], reverse=True)
        victims.sort(key=lambda row: row['priority'])
        selected, released = [], 0
        for victim in victims:
            if released >= deficit:
                break
            selected.append(victim)
            released += victim['demand'].get('ed_bed', 0)
        if released < deficit:
            continue
        for victim in selected:
            pid = victim['id']
            first = held_since.setdefault(pid, now)
            held.append(pid)
            holds.append(dict(patient=pid, waiting_patient=wid, hospital=hid,
                resource='ed_bed', known_free=bed['free'], demand=victim['demand']['ed_bed'],
                waiting_since=start, waiting_deadline=start + grace_minutes, held_since=first,
                held_deadline=first + grace_minutes,
                deadline=min(start + grace_minutes, first + grace_minutes,
                             offer['expires_at'] - 1, max(victim['valid_offer_expiries'].values()) - 1)))
        reserved[hid] += quantity
        reservations.append(dict(waiting_patient=wid, hospital=hid, resource='ed_bed',
            quantity=quantity, priority=waiting['priority'], known_free=bed['free']))
    return dict(held=sorted(held), holds=holds, reservations=reservations,
        ready_after=[row['id'] for row in ready if row['id'] not in held],
        held_since_after=deepcopy(held_since))


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
        for key, value in _branch_guard_counts(branch).items():
            _require(branch['metrics'][key] == value, 'raw protection metric mismatch: ' + key)
        return dict(opportunity_evaluations=0, opportunity_hold_checks=0,
                    opportunity_held_patients=0)
    _require(branch['information_mode'] == 'observed' and branch['policy']['rule'] in ('H1', 'C1'),
             'protection applies outside observed H1/C1')
    information._number(branch['metrics']['protection_wall_seconds'], 'protection runtime')
    workflow = _KnownWorkflow(scenario)
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
            expected = _expected_guard(expected_snapshot, workflow.waiting_since,
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
    for key, value in _branch_guard_counts(branch).items():
        _require(branch['metrics'][key] == value, 'raw protection metric mismatch: ' + key)
    return dict(opportunity_evaluations=len(used_guards),
        opportunity_hold_checks=len(used_holds),
        opportunity_held_patients=len({event['patient'] for event in hold_events}))


def _branch_guard_counts(branch):
    values = guard_metrics(branch)
    return dict(opportunity_guard_calls=values['guard_calls'],
        opportunity_hold_checks=values['hold_checks'], opportunity_held_patients=values['held_patients'])


def _paired(scenario, left, right):
    raw = base.paired_common_metrics(scenario, left, right)
    return {name: {key: value for key, value in row.items()
                   if key not in ('patient_ids', 'total_difference_minutes')}
            for name, row in raw.items()}


def responsiveness_metrics(branch):
    events = branch['events']
    return dict(prefetch_checks=sum(e['type'] == 'resource_prefetch' for e in events),
        watch_checks=sum(e['type'] == 'resource_watch_check' for e in events),
        watch_cycles=sum(e['type'] == 'resource_watch_cycle' for e in events),
        report_driven_wakes=sum(e['type'] == 'report_driven_wake' for e in events),
        confirmation_intervention_wall_seconds=branch['metrics']['confirmation_intervention_wall_seconds'])


def audit_responsiveness(scenario, branch):
    """Rebuild eligible intervention targets using only the earlier raw stream.

    Hospital-wide request history, receipt freshness, source timestamps,
    commitments, patient workflow and request cooldowns are reconstructed.
    Neither delayed response schedules nor future physical capacity are used
    to decide which inquiries, follow-ups or report-driven wakes should occur.
    """
    prefetch, watch = branch.get('prefetch_enabled'), branch.get('watch_enabled')
    _require(type(prefetch) is bool and type(watch) is bool, 'missing explicit confirmation flags')
    _require(not (prefetch or watch) or (branch['information_mode'] == 'observed'
             and branch['policy']['rule'] in ('H1', 'C1')), 'confirmation intervention outside observed H1/C1')
    information._number(branch['metrics']['confirmation_intervention_wall_seconds'], 'confirmation intervention runtime')
    ordered = information._indexed_events(branch)
    indexed = {e['event_sequence']: e for e in ordered}
    workflow = _KnownWorkflow(scenario)
    targeted, pending, cooldown, reports, wakes = set(), {}, {}, {}, set()
    expected_watches, expected_wakes = [], set()
    used_prefetch, required_prefetch, last_cycle, cycles = set(), set(), None, []

    def eligible_targets(at):
        rows = []
        for pid in workflow.specs:
            if workflow.states.get(pid) != 'SEARCH':
                continue
            row = workflow._base_patient(pid)
            if row['request_t'] <= at and len(row['compatible_hospitals']) == 1 \
                    and row['compatible_hospitals'][0] in targeted:
                rows.append(row)
        return sorted(rows, key=lambda r: (-r['priority'], r['request_t'], r['id']))

    def view_reason(row, hid, at):
        return information._check_reason(information._views('observed', workflow.observations,
            workflow.truth, hid, row['demand'], at), row['demand'])

    def check_failed_sequence(event, marker_kind):
        seq, pid, hid, at = event['event_sequence'], event['patient'], event['hospital'], event['t']
        check, wait = indexed.get(seq + 1), indexed.get(seq + 2)
        _require(check is not None and check['type'] == 'information_check'
                 and check['purpose'] == 'allocation' and check['eligible'] is False
                 and check['reason'] == event['reason'] and check['patient'] == pid
                 and check['hospital'] == hid and check['t'] == at,
                 marker_kind + ' lacks immediate failed allocation check')
        _require(wait is not None and wait['type'] == 'information_wait'
                 and wait['patient'] == pid and wait['hospital'] == hid
                 and wait['reason'] == event['reason'] and wait['t'] == at,
                 marker_kind + ' lacks immediate information wait')

    for event in ordered:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'resource_report_received':
            if event['applied']:
                reports[hid] = event
            if event['source'] == 'confirmation':
                pending.pop(hid, None)
                cooldown[hid] = at + scenario.get('retry_minutes', 3)
        elif kind == 'resource_confirmation_timeout':
            pending.pop(hid, None)
            cooldown[hid] = at + scenario.get('retry_minutes', 3)
        elif kind == 'resource_confirmation_request':
            targeted.add(hid)
            pending[hid] = event
        elif kind == 'information_check' and event['purpose'] == 'inquiry' \
                and event['reason'] in ('UNKNOWN', 'STALE') and prefetch:
            required_prefetch.add(seq)
        elif kind == 'resource_prefetch':
            _require(prefetch, 'prefetch event on disabled branch')
            inquiry = indexed.get(event['inquiry_check_sequence'])
            _require(inquiry is not None and inquiry['event_sequence'] < seq
                     and inquiry['type'] == 'information_check' and inquiry['purpose'] == 'inquiry'
                     and inquiry['patient'] == pid and inquiry['hospital'] == hid
                     and inquiry['t'] == at and inquiry['reason'] == event['reason']
                     and event['reason'] in ('UNKNOWN', 'STALE'),
                     'prefetch lacks earlier unknown/stale inquiry evidence')
            _require(workflow.states.get(pid) in ('SEARCH', 'TRANSIT')
                     and workflow.specs[pid].get('search_start_t', 0) <= at,
                     'prefetch observes inactive or future patient')
            _require(inquiry['event_sequence'] not in used_prefetch, 'prefetch reused inquiry evidence')
            used_prefetch.add(inquiry['event_sequence'])
            check_failed_sequence(event, 'prefetch')
        elif kind == 'resource_watch_cycle':
            _require(watch, 'watch cycle on disabled branch')
            _require(not expected_watches and not expected_wakes, 'watch cycle omitted required checks or wakes')
            _require(last_cycle is None or at == last_cycle + 1, 'watch cycles duplicated or skipped')
            _require(event['watched_hospitals'] == sorted(targeted), 'watch hospital history differs from earlier requests')
            last_cycle = at
            cycles.append(at)
            simulated_pending = set(pending)
            for row in eligible_targets(at):
                patient, hospital = row['id'], row['compatible_hospitals'][0]
                reason = view_reason(row, hospital, at)
                if reason is not None and hospital not in simulated_pending and cooldown.get(hospital, 0) <= at:
                    expected_watches.append((patient, hospital, reason))
                    simulated_pending.add(hospital)
                report = reports.get(hospital)
                if report is not None and report['t'] == at and reason is None \
                        and (patient, report['event_sequence']) not in wakes \
                        and not sum(n for (p, _), n in workflow.asks.items() if p == patient) \
                        and not any(p == patient and expiry > at for (p, _), expiry in workflow.offers.items()):
                    expected_wakes.add((patient, hospital, report['event_sequence']))
        elif kind == 'resource_watch_check':
            _require(watch and last_cycle == at and expected_watches,
                     'unexpected watch check without eligible cycle target')
            _require((pid, hid, event['reason']) == expected_watches.pop(0),
                     'watch target/order/reason differs from independent observed workflow')
            check_failed_sequence(event, 'watch')
        elif kind == 'report_driven_wake':
            _require(watch and last_cycle == at and not expected_watches,
                     'report wake precedes required watch checks')
            key = (pid, hid, event['report_event_sequence'])
            _require(key in expected_wakes, 'report wake lacks current applied complete feasible report or eligible waiter')
            expected_wakes.remove(key)
            wakes.add((pid, event['report_event_sequence']))
            previous = event['previous_retry_at']
            _require(previous is None or type(previous) is int and previous >= 0,
                     'invalid previous retry clock')
            check, ask = indexed.get(seq + 1), indexed.get(seq + 2)
            _require(check is not None and check['type'] == 'information_check'
                     and check['purpose'] == 'inquiry' and check['eligible'] is True
                     and check['t'] == at and check['patient'] == pid and check['hospital'] == hid
                     and ask is not None and ask['type'] == 'ask' and ask['t'] == at
                     and ask['patient'] == pid and ask['hospital'] == hid,
                     'report wake did not immediately retry the informed hospital')
        workflow.apply(event)
    _require(not expected_watches and not expected_wakes, 'last watch cycle omitted required checks or wakes')
    _require(used_prefetch == required_prefetch, 'unknown/stale inquiry lacks unique prefetch')
    _require(cycles == list(range(scenario.get('max_minutes', 180) + 1)) if watch else not cycles,
             'missing or extra watch cycle')
    values = responsiveness_metrics(branch)
    for key, value in values.items():
        _require(branch['metrics'][key] == value, 'raw confirmation metric mismatch: ' + key)
    if not prefetch and not watch:
        _require(values['confirmation_intervention_wall_seconds'] == 0, 'disabled confirmation intervention reports runtime')
    return {key: value for key, value in values.items() if key != 'confirmation_intervention_wall_seconds'}


def audit_branch(scenario, branch, *, exact_limit=20000):
    checked = information.audit_branch(scenario, branch, exact_limit=exact_limit)
    checked['counts'].update(audit_guard(scenario, branch))
    checked['counts'].update(audit_responsiveness(scenario, branch))
    checked['guard'] = guard_metrics(branch)
    checked['responsiveness'] = responsiveness_metrics(branch)
    return checked


def _settings(report):
    settings = report['settings']
    _require(base._same({key: value for key, value in settings.items() if key != 'policies'}, FIXED_SETTINGS),
             'fixed responsive experiment settings differ')
    policies = settings['policies']
    _require(isinstance(policies, list) and [p.get('id') for p in policies] == POLICY_IDS,
             'comparison requires fourteen canonical baseline/intervention branches')
    for policy in policies:
        prefix, code = policy['id'].split('_')
        protected = prefix in ('protected','early','watch','responsive')
        mode = 'observed' if protected else prefix
        fields = {'id', 'label', 'mode', 'rule', 'k', 'wait', 'notify', 'protection',
                  'prefetch','watch','grace_minutes'}
        fields.update({'time_limit_seconds', 'max_moves'} if code == 'H1' else
                      {'time_limit_seconds', 'seed', 'require_backend'} if code == 'C1' else set())
        _require(set(policy) == fields and policy['mode'] == mode and policy['rule'] == code
                 and type(policy['protection']) is bool and policy['protection'] == protected
                 and type(policy['prefetch']) is bool and policy['prefetch'] == (prefix in ('early','responsive'))
                 and type(policy['watch']) is bool and policy['watch'] == (prefix in ('watch','responsive'))
                 and policy['grace_minutes'] == GRACE_BY_PREFIX[prefix],
                 'configured responsive policy fields/mode/rule/protection mismatch')
        _require(policy['k'] == 3 and policy['wait'] == 2 and policy['notify'] is True,
                 'inquiry or notification policy differs')
        if code in ('H1', 'C1'):
            _require(policy['time_limit_seconds'] == .05, 'model search budget differs')
        if code == 'H1':
            _require(policy['max_moves'] == 2048, 'heuristic move limit differs')
        if code == 'C1':
            _require(policy['seed'] == 0 and policy['require_backend'] is True,
                     'CP-SAT seed or required backend differs')
    return {policy['id']: policy for policy in policies}


def _audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    _require(report.get('kind') == 'responsive_confirmation_comparison'
             and report.get('experiment_id') == EXPERIMENT_ID and report.get('synthetic') is True,
             'wrong opportunity report identity or synthetic scope')
    _require(report['phase'] in ('development', 'validation'), 'invalid report phase')
    _require(isinstance(report['source_sha256'], dict) and report['source_sha256'], 'missing source manifest')
    if expected_source_sha256 is not None:
        _require(report['source_sha256'] == expected_source_sha256, 'source hashes differ from expected manifest')
    _require(isinstance(report['runtime_versions'], dict) and report['runtime_versions'], 'missing runtime manifest')
    seeds, families = report['seeds'], report['families']
    _require(seeds and len(set(seeds)) == len(seeds) and
             all(type(seed) is int and seed >= 0 for seed in seeds), 'invalid or duplicated phase seeds')
    _require(families and len(set(families)) == len(families)
             and all(family in FAMILIES for family in families), 'unknown or duplicated opportunity families')
    if lock is not None:
        _require(lock.get('kind') == 'responsive_confirmation_lock' and lock.get('experiment_id') == EXPERIMENT_ID,
                 'wrong development lock identity')
        for key in ('source_sha256', 'runtime_versions', 'settings', 'families'):
            _require(report[key] == lock[key], 'report differs from lock: ' + key)
        _require(lock['development_seeds'] == DEVELOPMENT_SEEDS and lock['validation_seeds'] == VALIDATION_SEEDS
                 and lock['families'] == list(FAMILIES), 'lock differs from preregistered opportunity seeds/families')
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
                 'scenario lacks synthetic opportunity comparison identity')
        branches = case['branches']
        _require([branch['id'] for branch in branches] == POLICY_IDS, 'branch output order differs')
        offset = (seeds.index(scenario['seed']) + families.index(scenario['family'])) % len(POLICY_IDS)
        _require(case['execution_order'] == POLICY_IDS[offset:] + POLICY_IDS[:offset],
                 'execution order differs from prescribed rotation')
        for name in ('summaries', 'information_metrics', 'guard_metrics', 'responsiveness_metrics', 'diagnostics'):
            _require(set(case[name]) == set(POLICY_IDS), 'case branch IDs mismatch: ' + name)
        _require(set(case['paired_comparisons']) == {name for name, _, _ in COMPARISON_PAIRS},
                 'responsive paired comparison IDs mismatch')
        by_id = {}
        for branch in branches:
            code = branch['id']
            by_id[code] = branch
            _require(branch.get('comparison_id') == EXPERIMENT_ID
                     and branch.get('source_experiment') == 'responsive_confirmation_comparison',
                     'branch lacks opportunity comparison instrumentation')
            spec = specs[code]
            information._branch_policy(branch, spec)
            _require(type(branch['protection_enabled']) is bool
                     and branch['protection_enabled'] == spec['protection'],
                     'branch protection differs from configured policy')
            _require(type(branch['prefetch_enabled']) is bool and branch['prefetch_enabled'] == spec['prefetch']
                     and type(branch['watch_enabled']) is bool and branch['watch_enabled'] == spec['watch']
                     and branch['grace_minutes'] == spec['grace_minutes'],
                     'branch confirmation flags/grace differ from configured policy')
            checked = audit_branch(scenario, branch, exact_limit=exact_limit)
            _require(base._same(case['summaries'][code], checked['metrics']), 'raw full-flow summary mismatch: ' + code)
            _require(base._same(case['information_metrics'][code], checked['information']),
                     'raw information metric mismatch: ' + code)
            _require(base._same(case['guard_metrics'][code], checked['guard']),
                     'raw protection metric mismatch: ' + code)
            _require(base._same(case['responsiveness_metrics'][code], checked['responsiveness']),
                     'raw confirmation metric mismatch: ' + code)
            _require(base._same(case['diagnostics'][code], _diagnostics(scenario, branch)),
                     'raw diagnostic mismatch: ' + code)
            totals.update(checked['counts'])
            totals['branches'] += 1
        for name, left_id, right_id in COMPARISON_PAIRS:
            pair = case['paired_comparisons'][name]
            left, right = by_id[left_id], by_id[right_id]
            _require(pair['baseline'] == left_id and pair['comparison'] == right_id,
                     'paired comparison labels differ: ' + name)
            _require(base._same(pair['metrics'], _paired(scenario, left, right)),
                     'raw paired protection/common-patient mismatch: ' + name)
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
        protection_contract='fresh known bed only; fixed 3min lifetime grace; no higher-priority hold',
        confirmation_contract='parallel inquiries and source-current reports; hospital cooldown and explicit new acceptance',
        proof_scope='verified_reported_model_only_not_actual_gate_or_whole_episode')


def audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    """Audit saved artifacts without executing the simulator or protection code."""
    _require(type(exact_limit) is int and exact_limit >= 0, 'invalid exact enumeration limit')
    try:
        return _audit_report(report, expected_source_sha256=expected_source_sha256,
                             lock=lock, exact_limit=exact_limit)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise ResponsiveAuditError('malformed opportunity artifact') from error


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


if __name__ == '__main__':
    main()

