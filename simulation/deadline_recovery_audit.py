"""Independent raw audit of nonblocking, explicitly received late offers.

Frozen physical, observation, model-proof and motion audits remain in use.
Workflow functions are copied locally solely to add callback offers without
inventing pending inquiries. No replay engine, solver or observation store is
imported. Raw emissions are neither transformed nor hidden.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
import math
from . import joint_policy_audit as joint
from . import scarce_delay_audit as scarce

base = joint.base
recovery = joint.recovery
information = joint.information
responsive = joint.responsive
_TruthLedger = joint._TruthLedger
_indexed_events = joint._indexed_events
_require = joint._require
_end = joint._end
_position = joint._position
_reply_environment = joint._reply_environment
_selector_reproduce = joint._selector_reproduce
_views = joint._views
_check_reason = joint._check_reason
_early_snapshot = joint._early_snapshot
_early_expected = joint._early_expected
_pending_guard_expected = joint._pending_guard_expected
_pending_rows = joint._pending_rows
_offer_rows = joint._offer_rows
_joint_reply_due = joint._joint_reply_due
_finite = joint._finite
_scarce_expected = scarce._scarce_expected
CLOCK_ORIGIN_SECONDS = joint.CLOCK_ORIGIN_SECONDS
DeadlineRecoveryAuditError = joint.JointPolicyAuditError
EXPERIMENT_ID = 'deadline_recovery_20261016'


class _LateWorkflow(recovery._EarlyWorkflow):
    """Track received callback offers separately from real pending inquiries."""
    def __init__(self, scenario):
        super().__init__(scenario)
        self.late_adoptions = {}

    def apply(self, event):
        kind, pid, hid, at = event['type'], event.get('patient'), event.get('hospital'), event['t']
        key = (pid, hid)
        if kind == 'late_offer_accepted':
            _require(key not in self.late_adoptions, 'duplicate unconsumed callback adoption')
            self.late_adoptions[key] = event
        elif kind == 'accept' and key in self.late_adoptions:
            adopted = self.late_adoptions.pop(key)
            _require(adopted['t'] == at and adopted['event_sequence'] + 1 == event['event_sequence'],
                     'callback adoption lacks immediate actual acceptance')
            self.offers[key] = at + self.scenario.get('accept_ttl_minutes', 10)
            self.accepted[key] = at
            self.first_valid[pid] = at
            self.truth.apply(event)
            return
        super().apply(event)


_EarlyWorkflow = _LateWorkflow


def _is_callback_accept(event, indexed):
    """A new received offer is distinct from a second reply to an old ask."""
    if event['type'] != 'accept' or 'late_offer_event_sequence' not in event:
        return False
    marker = indexed.get(event['event_sequence'] - 1)
    _require(marker is not None and marker['type'] == 'late_offer_accepted'
             and marker['event_sequence'] == event['late_offer_event_sequence']
             and marker['receipt_event_sequence'] == event['receipt_event_sequence']
             and (marker['patient'], marker['hospital'], marker['t']) ==
             (event['patient'], event['hospital'], event['t']),
             'late acceptance lacks its immediately preceding received-offer adoption')
    return True

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
            ready_after = list(expected['ready_after'])
            if ready_after and branch.get('pending_protection_enabled'):
                pending_marker = by_sequence.get(next_sequence)
                _require(pending_marker is not None and pending_marker['type'] == 'pending_opportunity_guard'
                         and pending_marker['t'] == at, 'remaining ready set lacks pending guard')
                pending_record = branch['pending_guard_records'][pending_marker['record_index']]
                _require([row['id'] for row in pending_record['snapshot']['ready']] == ready_after,
                         'pending guard changed original guard ready set')
                ready_after = pending_record['result']['ready_after']
                next_sequence = next((s for s, raw in by_sequence.items() if s > next_sequence
                    and raw['t'] == at and raw['type'] == 'allocation_proposal'), None)
            if ready_after:
                marker = by_sequence.get(next_sequence)
                decision = decisions.get(next_sequence)
                _require(marker is not None and marker['type'] == 'allocation_proposal'
                         and marker['t'] == at and decision is not None and decision['kind'] == 'batch',
                         'remaining protected ready patients lack immediate ordinary batch')
                expected_rows = [row for row in expected_snapshot['ready']
                                 if row['id'] in ready_after]
                model_rows = decision['inputs']['patients']
                _require([row['id'] for row in model_rows] == ready_after
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
    expected_cycles = [i/4 for i in range(scenario.get('max_minutes', 180)*4+1)] if enabled else []
    _require(cycles == expected_cycles, 'missing, duplicated or extra early-offer cycle')
    return dict(early_offer_cycles=len(cycles), early_offer_reviews=len(records),
                early_offer_ready=successful)

def audit_pending_guard(scenario, branch):
    enabled = branch.get('pending_protection_enabled')
    _require(type(enabled) is bool, 'missing explicit pending guard mode')
    records = branch['pending_guard_records']
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    markers = [event for event in events if event['type'] == 'pending_opportunity_guard']
    raw_holds = [event for event in events if event['type'] == 'pending_opportunity_hold']
    if not enabled:
        _require(not records and not markers and not raw_holds, 'disabled pending guard contains interventions')
        return dict(pending_guard_reviews=0, pending_guard_holds=0)
    workflow = _EarlyWorkflow(scenario)
    handles, first, used, hold_sequences = {}, {}, set(), set()
    ready = []
    specs = {row['id']: row for row in scenario['patients']}
    hospitals = {row['id']: row for row in scenario['hospitals']}

    def patient_view(pid):
        row = workflow._base_patient(pid)
        row['compatible_hospitals'] = sorted(hid for hid, hospital in hospitals.items()
            if set(row['required_capabilities']) <= set(hospital.get('capabilities', []))
            and all(hospital.get('capacity', {}).get(resource, 0) >= quantity
                    for resource, quantity in row['demand'].items()))
        return row

    for event in events:
        kind, at, pid, hid, seq = event['type'], event['t'], event.get('patient'), event.get('hospital'), event['event_sequence']
        if kind == 'ask':
            handles[pid, hid] = dict(hospital=hid, attempt=event['attempt'], submitted_at=at,
                timeout_at=event['timeout_at'], ask_event_sequence=seq)
        elif kind in ('accept', 'decline', 'no_response'):
            handles.pop((pid, hid), None)
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key: row for key, row in handles.items() if key[0] != pid}
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key: row for key, row in handles.items() if key[0] != pid}
            for row in event['pending']:
                handles[pid, row['hospital']] = deepcopy(row)
        elif kind == 'opportunity_guard':
            original = branch['protection_records'][event['record_index']]
            by_id = {row['id']: row for row in original['snapshot']['ready']}
            ready = []
            for target in original['result']['ready_after']:
                row = patient_view(target)
                row.update(options=by_id[target]['options'], valid_offer_expiries=by_id[target]['valid_offer_expiries'])
                alternatives = {hid: _views('observed', workflow.observations, workflow.truth,
                    hid, row['demand'], at) for hid in row['compatible_hospitals']}
                row.update(alternative_resource_views=alternatives,
                    eligible_alternative_hospitals=[hid for hid, views in alternatives.items()
                        if _check_reason(views, row['demand']) != 'FRESH_SHORTAGE'])
                ready.append(row)
        elif kind == 'pending_opportunity_guard':
            index = event['record_index']
            _require(index == len(used) and index < len(records), 'missing/reordered pending guard record')
            used.add(index)
            record = records[index]
            _require(set(record) == {'snapshot', 'result', 'event_sequence'} and record['event_sequence'] == seq,
                     'pending guard record has unknown fields or wrong emission sequence')
            _require(ready, 'pending guard has no ready patients after original guard')
            waiting = []
            ready_ids = {row['id'] for row in ready}
            for target in sorted(specs):
                if workflow.states.get(target) != 'SEARCH' or target in ready_ids:
                    continue
                row = patient_view(target)
                if len(row['compatible_hospitals']) != 1:
                    continue
                only = row['compatible_hospitals'][0]
                requests = [value for (patient, hospital), value in handles.items() if patient == target and hospital == only]
                if not requests:
                    continue
                views = _views('observed', workflow.observations, workflow.truth, only, row['demand'], at)
                row.update(pending_inquiries=deepcopy(requests), resource_views=views,
                           bed_view=views.get('ed_bed'))
                waiting.append(row)
            snapshot = dict(t=at, ready=deepcopy(ready), waiting=waiting, held_since=deepcopy(first))
            _require(base._same(record['snapshot'], snapshot),
                     'pending guard input differs from original raw inquiries, received beds or ready set')
            expected = _pending_guard_expected(snapshot)
            _require(base._same(record['result'], expected), 'pending guard renewed lifetime or changed resource/priority rule')
            first = deepcopy(expected['held_since_after'])
            for offset, hold in enumerate(expected['holds'], 1):
                raw = indexed.get(seq + offset)
                _require(raw is not None and raw['type'] == 'pending_opportunity_hold' and raw['t'] == at
                         and base._same({k: v for k, v in raw.items() if k not in ('t', 'type', 'event_sequence')}, hold),
                         'pending guard hold differs from bounded independent result')
                hold_sequences.add(seq + offset)
            ready = []
        workflow.apply(event)
    _require(used == set(range(len(records))) and hold_sequences == {event['event_sequence'] for event in raw_holds},
             'orphan pending guard or pending hold')
    _require(branch['metrics']['pending_guard_reviews'] == len(records)
             and branch['metrics']['pending_guard_holds'] == len(raw_holds), 'pending guard metrics differ from raw events')
    return dict(pending_guard_reviews=len(records), pending_guard_holds=len(raw_holds))

def audit_backups(scenario, branch):
    """Validate original inquiry continuity, receipt, TTL and transfer reuse."""
    retained = branch.get('retained_backups_enabled')
    same_tick = branch.get('transfer_same_tick_enabled')
    followup = branch.get('recovery_followup_enabled')
    _require(type(retained) is bool and type(same_tick) is bool and type(followup) is bool
             and (not same_tick or retained) and (not followup or same_tick),
             'invalid explicit backup retention or preparation mode')
    ordered = information._indexed_events(branch)
    indexed = {event['event_sequence']: event for event in ordered}
    specs = {p['id']: p for p in scenario['patients']}
    truth = information._TruthLedger(scenario)
    states, destinations, asks, offers, episodes, notifications = {}, {}, {}, {}, {}, set()
    pending_commit, early_ready, failures, expected_reply = {}, {}, {}, None
    observations, reports, resource_failures, expires, terminals, latest_notify, alert_retries = {}, {}, {}, {}, {}, {}, set()
    pending_ready=set()
    counts = Counter(backup_episodes=0, retained_pending_inquiries=0,
        retained_live_offers=0, backup_replies=0, backup_arrivals_preserved=0,
        backup_preparations_ready=0, backup_transfers_reused=0, backup_episodes_closed=0,
        backup_report_reopens=0, backup_alert_retries=0)
    marker_names = {'backup_retained', 'backup_reply_received', 'backup_arrival_preserved',
                    'backup_preparation_ready', 'backup_transfer_reused', 'backup_closed',
                    'backup_failed_reopened', 'backup_alert_retry'}
    for event in ordered:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind in marker_names:
            _require(retained and pid in specs, 'backup event on disabled branch or undeclared patient')
        if expected_reply is not None:
            _require(kind == expected_reply['outcome'] and pid == expected_reply['patient']
                     and hid == expected_reply['hospital'] and at == expected_reply['t'],
                     'backup receipt lacks immediate matching ordinary reply')
            expected_reply = None
        if kind == 'resource_report_received':
            information._apply_observation(observations,event)
            if event['applied']:reports[hid]=event
        elif kind == 'information_check' and event['purpose']=='allocation' and event['eligible'] and states.get(pid)=='SEARCH':
            pending_ready.add(pid)
        elif kind == 'opportunity_guard':
            row=branch['protection_records'][event['record_index']]
            pending_ready.difference_update(patient['id'] for patient in row['snapshot']['ready'])
        elif kind == 'allocation_proposal':
            row=branch['allocation_decisions'][event['decision_index']]
            pending_ready.difference_update(patient['id'] for patient in row['inputs']['patients'])
        elif kind == 'request':
            states[pid] = 'SEARCH'
        elif kind == 'early_offer_ready':
            early_ready[pid] = event
        elif kind == 'ask':
            _require((pid, hid) not in asks, 'overlapping active patient inquiry')
            _require(pid not in episodes or (pid,hid) not in resource_failures,
                     'new inquiry to resource-failed hospital lacks received-report reopening')
            asks[pid, hid] = dict(hospital=hid, attempt=event['attempt'], submitted_at=at,
                timeout_at=at + scenario.get('timeout_minutes', 6), ask_event_sequence=seq)
        elif kind == 'backup_reply_received':
            _require(pid in episodes and (pid, hid) in asks,
                     'backup reply resurrects a cancelled or unknown inquiry')
            handle = asks[pid, hid]
            _require(set(event) == {'t','type','event_sequence','patient','hospital','attempt',
                'ask_event_sequence','outcome','phase'}, 'backup reply reveals future schedule or unknown fields')
            _require(event['attempt'] == handle['attempt'] and event['ask_event_sequence'] == handle['ask_event_sequence']
                     and at <= handle['timeout_at'], 'backup reply changed original request or absolute deadline')
            phase = 'transit' if states.get(pid) == 'TRANSIT' else 'preparation' if states.get(pid) == 'TRANSFER_WAIT' else None
            _require(event['phase'] == phase and phase is not None,
                     'receive-only backup reply outside transit or preparation')
            due, silent = _joint_reply_due(scenario, ordered, handle)
            _require(due is None or at == due, 'backup reply changed original hospital work or delivery time')
            demand = specs[pid].get('resource_demands', {'ed_bed':1})
            quantities = truth.resources(hid, demand, at)
            compatible = truth.patients[pid]['capabilities'] <= truth.capabilities[hid]
            outcome = 'no_response' if silent else 'accept' if compatible and all(
                values['free'] >= demand[resource] for resource, values in quantities.items()) else 'decline'
            _require(event['outcome'] in ('accept','decline','no_response')
                     and (silent is None or event['outcome'] == outcome),
                     'backup reply differs from original silence or current physical acceptance')
            expected_reply = dict(event)
            counts['backup_replies'] += 1
        elif kind in ('accept','decline','no_response'):
            callback = _is_callback_accept(event, indexed)
            if callback:
                _require((pid, hid) not in asks and pid not in episodes,
                         'late callback overlaps a real inquiry or retained backup episode')
            else:
                _require((pid, hid) in asks, 'ordinary reply lacks original active inquiry')
                handle = asks.pop((pid, hid))
            if pid in episodes:
                if states.get(pid)=='TRANSFER_WAIT' or states.get(pid)=='TRANSIT' and pid not in notifications:
                    previous=indexed.get(seq-1)
                    _require(previous is not None and previous['type']=='backup_reply_received',
                             'receive-only original reply omitted backup receipt marker')
                due, _ = _joint_reply_due(scenario, ordered, handle)
                _require(at <= handle['timeout_at'] and (due is None or at == due),
                         'retained original inquiry reply after timeout or at changed time')
            if kind == 'accept':
                offers[pid, hid] = dict(hospital=hid, accepted_at=at,
                    expires_at=at + scenario.get('accept_ttl_minutes', 10), accept_event_sequence=seq)
            else:terminals[pid,hid]=event
        elif kind == 'expire':
            offers.pop((pid, hid), None)
            expires[pid,hid]=event
            terminals[pid,hid]=event
        elif kind == 'confirmation_gate' and not event['passed']:
            offers.pop((pid, hid), None)
        elif kind == 'notify':
            if pid in episodes:
                _require(states.get(pid) == 'TRANSIT' and destinations.get(pid) == hid,
                         'backup notification differs from actual in-transit destination')
                _require(set(event) == {'t','type','event_sequence','patient','hospital','resource','cause'},
                         'patient notification carries extra quantities or future restoration')
                notifications.add(pid)
                latest_notify[pid]=event
                if followup:
                    original=episodes[pid]
                    known={row['hospital'] for row in original['pending']+original['offers']}
                    required=[]
                    for backup in sorted(known):
                        terminal=terminals.get((pid,backup))
                        if backup==hid or (pid,backup) in asks or (pid,backup) in offers or terminal is None \
                                or terminal['type']!='expire' or terminal['t']>at \
                                or (pid,backup,seq) in alert_retries:continue
                        demand=specs[pid].get('resource_demands',{'ed_bed':1})
                        views=information._views('observed',observations,truth,backup,demand,at)
                        if information._check_reason(views,demand) is None:required.append(backup)
                    for offset,backup in enumerate(required,1):
                        following=indexed.get(seq+offset)
                        _require(following is not None and following['type']=='backup_alert_retry'
                                 and following['patient']==pid and following['hospital']==backup
                                 and following['notify_event_sequence']==seq,
                                 'current alert omitted eligible expired-backup retry')
        elif kind == 'commit':
            if pid in episodes:
                _require(states.get(pid) == 'SEARCH' or pid in notifications,
                         'receive-only healthy transit automatically rerouted to a backup')
            pending_commit[pid] = dict(event=event,
                pending=_pending_rows(asks,pid,at,exclude={hid}),
                offers=_offer_rows(offers,pid,at,exclude={hid}),
                early=early_ready.get(pid))
            following=indexed.get(seq+1)
            if pid in episodes:
                _require(following is not None and following['type']=='backup_closed'
                         and following['patient']==pid and following['cause_event_sequence']==seq,
                         'later commitment omitted backup closure')
            elif retained and pid not in destinations and early_ready.get(pid,{}).get('t')==at \
                    and (pending_commit[pid]['pending'] or pending_commit[pid]['offers']):
                _require(following is not None and following['type']=='backup_retained'
                         and following['patient']==pid and following['commit_event_sequence']==seq,
                         'eligible early commitment omitted original backup retention')
            asks = {key: value for key,value in asks.items() if key[0] != pid}
            offers = {key: value for key,value in offers.items() if key[0] != pid}
            states[pid], destinations[pid] = 'TRANSIT', hid
        elif kind == 'backup_retained':
            saved = pending_commit.get(pid)
            _require(saved is not None and saved['event']['event_sequence'] == event['commit_event_sequence']
                     and saved['event']['event_sequence'] == seq - 1 and saved['event']['t'] == at
                     and saved['event']['hospital'] == hid and pid not in episodes,
                     'backup retention lacks immediately preceding eligible early commitment')
            first = saved['early']
            _require(first is not None and first['t'] == at
                     and first['event_sequence'] == event['early_ready_event_sequence'],
                     'backup retention is not a successful same-tick early commitment')
            _require(set(event) == {'t','type','event_sequence','patient','hospital','commit_event_sequence',
                'early_ready_event_sequence','pending','offers'}, 'backup retention exposes future inquiry details')
            _require(base._same(event['pending'],saved['pending']) and base._same(event['offers'],saved['offers']),
                     'retained pending inquiries or live offers differ from original known work')
            for row in event['pending']:
                asks[pid,row['hospital']] = deepcopy(row)
            for row in event['offers']:
                offers[pid,row['hospital']] = deepcopy(row)
            episodes[pid] = event
            notifications.discard(pid)
            counts.update(backup_episodes=1, retained_pending_inquiries=len(event['pending']),
                          retained_live_offers=len(event['offers']))
        elif kind == 'arrive':
            states[pid] = 'QUEUED'
            if pid in episodes:
                following=indexed.get(seq+1)
                _require(following is not None and following['type']=='backup_arrival_preserved'
                         and following['patient']==pid and following['arrive_event_sequence']==seq,
                         'arrival omitted preservation of retained backup work')
        elif kind == 'backup_arrival_preserved':
            prior = indexed.get(event['arrive_event_sequence'])
            _require(pid in episodes and prior is not None and prior['event_sequence'] == seq-1
                     and prior['type'] == 'arrive' and prior['patient'] == pid and prior['hospital'] == hid
                     and prior['t'] == at, 'backup arrival preservation lacks immediate original arrival')
            _require(set(event) == {'t','type','event_sequence','patient','hospital','arrive_event_sequence','pending','offers'},
                     'backup arrival preservation carries unknown policy fields')
            _require(base._same(event['pending'],_pending_rows(asks,pid,at))
                     and base._same(event['offers'],_offer_rows(offers,pid,at)),
                     'arrival discarded, fabricated or renewed original backup work')
            counts['backup_arrivals_preserved'] += 1
        elif kind == 'arrival_unable':
            states[pid] = 'TRANSFER_WAIT'
            failures[pid] = event
            if event['reason']=='resource_capacity':resource_failures[pid,hid]=event
        elif kind == 'backup_preparation_ready':
            prior = failures.get(pid)
            _require(same_tick and pid in episodes and states.get(pid) == 'TRANSFER_WAIT'
                     and prior is not None and prior['event_sequence'] == event['arrival_unable_event_sequence']
                     and prior['hospital'] == hid and event['preparation_minutes'] == scenario.get('transfer_preparation_minutes',3)
                     and event['ready_at'] == at == prior['transfer_search_at'],
                     'backup preparation skipped original failed-arrival preparation')
            _require(set(event) == {'t','type','event_sequence','patient','hospital','arrival_unable_event_sequence',
                                  'preparation_minutes','ready_at'}, 'backup preparation exposes future or unknown fields')
            following = indexed.get(seq+1)
            _require(following is not None and following['type'] == 'transfer_search'
                     and following['patient'] == pid and following['hospital'] == hid and following['t'] == at,
                     'preparation-ready marker lacks immediate ordinary transfer search')
            counts['backup_preparations_ready'] += 1
        elif kind == 'transfer_search':
            states[pid] = 'SEARCH'
            if pid in episodes:
                prior=indexed.get(seq-1);following=indexed.get(seq+1)
                _require(not same_tick or prior is not None and prior['type']=='backup_preparation_ready'
                         and prior['patient']==pid, 'same-tick transfer omitted original preparation-ready marker')
                _require(following is not None and following['type']=='backup_transfer_reused'
                         and following['patient']==pid and following['transfer_event_sequence']==seq,
                         'transfer search omitted reuse of original backup work')
            if pid not in episodes:
                asks = {key:value for key,value in asks.items() if key[0] != pid}
                offers = {key:value for key,value in offers.items() if key[0] != pid}
        elif kind == 'backup_transfer_reused':
            prior, failure = indexed.get(event['transfer_event_sequence']), failures.get(pid)
            _require(pid in episodes and prior is not None and prior['event_sequence'] == seq-1
                     and prior['type'] == 'transfer_search' and prior['patient'] == pid and prior['hospital'] == hid
                     and prior['t'] == at and failure is not None
                     and failure['event_sequence'] == event['arrival_unable_event_sequence']
                     and at == failure['transfer_search_at'], 'backup reuse precedes original ordinary transfer search')
            _require(set(event) == {'t','type','event_sequence','patient','hospital','transfer_event_sequence',
                                  'arrival_unable_event_sequence','pending','offers'},
                     'backup transfer reuse carries future or unknown policy fields')
            _require(base._same(event['pending'],_pending_rows(asks,pid,at))
                     and base._same(event['offers'],_offer_rows(offers,pid,at)),
                     'transfer reuse discarded, fabricated, expired or renewed original backup work')
            counts['backup_transfers_reused'] += 1
        elif kind == 'treatment_start' and pid in specs:
            states[pid] = 'TREATING'
            if pid in episodes:
                following=indexed.get(seq+1)
                _require(following is not None and following['type']=='backup_closed'
                         and following['patient']==pid and following['cause_event_sequence']==seq,
                         'healthy treatment omitted backup closure')
        elif kind == 'backup_failed_reopened':
            failure=resource_failures.get((pid,hid));report=reports.get(hid)
            _require(followup and pid in episodes and states.get(pid)=='SEARCH' and pid not in pending_ready
                     and (pid,hid) not in asks and (pid,hid) not in offers
                     and failure is not None and report is not None,
                     'report reopening lacks eligible current resource-failure waiter')
            _require(set(event)=={'t','type','event_sequence','patient','hospital',
                'arrival_unable_event_sequence','report_event_sequence'}, 'report reopening carries unknown or future fields')
            _require(failure['event_sequence']==event['arrival_unable_event_sequence']
                     and report['event_sequence']==event['report_event_sequence'] and report['t']==at
                     and report['observed_at']>=failure['t'],
                     'report reopening lacks latest same-minute applied source after resource failure')
            demand=specs[pid].get('resource_demands',{'ed_bed':1})
            views=information._views('observed',observations,truth,hid,demand,at)
            _require(information._check_reason(views,demand) is None
                     and all(view['observed_at']>=failure['t'] for view in views.values()),
                     'report reopening used stale, unknown, insufficient or pre-failure quantities')
            resource_failures.pop((pid,hid))
            check,ask=indexed.get(seq+1),indexed.get(seq+2)
            if ask is not None and ask['type']=='joint_inquiry_policy':
                ask=indexed.get(seq+3)
            _require(check is not None and check['type']=='information_check' and check['purpose']=='inquiry'
                     and check['eligible'] is True and check['patient']==pid and check['hospital']==hid
                     and check['t']==at and ask is not None and ask['type']=='ask' and ask['patient']==pid
                     and ask['hospital']==hid and ask['t']==at, 'report reopening failed to send a fresh patient inquiry')
            counts['backup_report_reopens']+=1
        elif kind == 'backup_alert_retry':
            notice=latest_notify.get(pid);expired=expires.get((pid,hid))
            original=episodes.get(pid)
            backup_hids={row['hospital'] for row in original['pending']+original['offers']} if original else set()
            _require(followup and original is not None and states.get(pid)=='TRANSIT' and pid in notifications
                     and hid in backup_hids and notice is not None and expired is not None
                     and (pid,hid) not in asks and not any(patient==pid and hospital==hid and row['expires_at']>at
                         for (patient,hospital),row in offers.items()),
                     'alert retry lacks expired original backup and notified transit')
            _require(set(event)=={'t','type','event_sequence','patient','hospital','notify_event_sequence','expire_event_sequence'},
                     'alert retry carries unknown or future fields')
            _require(notice['event_sequence']==event['notify_event_sequence'] and expired['event_sequence']==event['expire_event_sequence']
                     and expired['t']<=notice['t'] and expired['event_sequence']<notice['event_sequence'],
                     'alert retry uses an expiration not received before the current notification')
            _require(terminals.get((pid,hid),{}).get('event_sequence')==expired['event_sequence'],
                     'alert retry resurrects a newer declined or timed-out backup')
            key=pid,hid,notice['event_sequence']
            _require(key not in alert_retries,'same alert retried an expired backup more than once')
            alert_retries.add(key)
            demand=specs[pid].get('resource_demands',{'ed_bed':1})
            views=information._views('observed',observations,truth,hid,demand,at)
            _require(information._check_reason(views,demand) is None,
                     'alert retry uses unknown, stale or insufficient current quantities')
            counts['backup_alert_retries']+=1
        elif kind=='event_review_cycle' and followup:
            for (patient,hospital),failure in resource_failures.items():
                if patient not in episodes or states.get(patient)!='SEARCH' or patient in pending_ready \
                        or (patient,hospital) in asks or (patient,hospital) in offers:continue
                report=reports.get(hospital)
                if report is None or report['t']!=at or report['observed_at']<failure['t']:continue
                demand=specs[patient].get('resource_demands',{'ed_bed':1})
                views=information._views('observed',observations,truth,hospital,demand,at)
                eligible=information._check_reason(views,demand) is None and all(
                    view['observed_at']>=failure['t'] for view in views.values())
                _require(not eligible,'current complete post-failure report omitted required reopening')
        elif kind == 'backup_closed':
            cause = indexed.get(event['cause_event_sequence'])
            _require(pid in episodes and cause is not None and cause['patient'] == pid
                     and cause['event_sequence'] == seq-1 and cause['t'] == at
                     and (event['reason'],cause['type']) in (('COMMITTED','commit'),('TREATMENT_STARTED','treatment_start')),
                     'backup closure lacks immediate actual commitment or treatment')
            _require(set(event) == {'t','type','event_sequence','patient','reason','cause_event_sequence'},
                     'backup closure carries unknown policy fields')
            episodes.pop(pid)
            asks = {key:value for key,value in asks.items() if key[0] != pid}
            offers = {key:value for key,value in offers.items() if key[0] != pid}
            notifications.discard(pid)
            counts['backup_episodes_closed'] += 1
        truth.apply(event)
    _require(expected_reply is None, 'last backup receipt lacks ordinary reply')
    for pid in episodes:
        _require(states.get(pid) not in ('TREATING',), 'healthy treatment failed to close retained work')
    return dict(counts)

def audit_supplements(scenario, branch):
    """Rebuild additional inquiries without changing the original selector pool."""
    enabled = branch.get('scarce_routing_enabled', False)
    _require(type(enabled) is bool, 'invalid scarce supplement mode')
    records = branch.get('scarce_supplement_records', [])
    events = _indexed_events(branch)
    markers = [event for event in events if event['type'] == 'scarce_supplement_review']
    _require(not any(event['type'] == 'scarce_candidate_review' for event in events)
             and not branch.get('scarce_candidate_records'), 'candidate exclusion is not the supplemental intervention')
    if not enabled:
        _require(not records and not markers, 'baseline contains scarce supplemental intervention')
        return dict(scarce_supplement_reviews=0, scarce_supplement_sent=0)
    _require(branch.get('cohort_registration_enabled') is True, 'scarce supplement lacks cohort registration')
    workflow = _EarlyWorkflow(deepcopy(scenario))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    requests, counts, handles, checks, used, reviewed = {}, Counter(), {}, [], set(), set()
    selectors = {row['event_sequence']: row for row in branch['inquiry_decisions']}
    indexed = {event['event_sequence']: event for event in events}
    contacts, episodes = Counter(), {}
    sent = 0

    def patient_view(pid):
        _require(pid in requests and requests[pid]['event_sequence'] < current_sequence,
                 'supplement input used unregistered or future patient')
        source = workflow._base_patient(pid)
        compatible = sorted(hid for hid in source['compatible_hospitals'] if all(
            hospitals[hid].get('capacity', {}).get(resource, 0) >= amount
            for resource, amount in source['demand'].items()))
        return dict(id=pid, priority=source['priority'], request_t=source['request_t'],
            demand=source['demand'], required_capabilities=source['required_capabilities'],
            state=workflow.states[pid], request_event_sequence=requests[pid]['event_sequence'],
            compatible_hospitals=compatible, inquiry_count=counts[pid],
            pending_inquiries=[deepcopy(value) for (patient, _), value in handles.items() if patient == pid])

    for event in events:
        kind, at, current_sequence = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'request':
            _require(pid not in requests, 'duplicate registered supplement patient')
            requests[pid] = event
        elif kind == 'ask':
            counts[pid] += 1
            episodes[pid] = event['inquiry_episode']
            contacts[pid, event['inquiry_episode']] += 1
            handles[pid, hid] = dict(hospital=hid, attempt=event['attempt'], submitted_at=at,
                timeout_at=event['timeout_at'], ask_event_sequence=current_sequence)
        elif kind in ('accept', 'decline', 'no_response'):
            handles.pop((pid, hid), None)
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key: value for key, value in handles.items() if key[0] != pid}
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key: value for key, value in handles.items() if key[0] != pid}
            for row in event['pending']:
                handles[pid, row['hospital']] = deepcopy(row)
        elif kind == 'information_check' and event['purpose'] == 'inquiry':
            checks.append(event)
        elif kind == 'scarce_supplement_review':
            index = event['record_index']
            _require(index == len(used) and index < len(records) and pid not in reviewed,
                     'missing, duplicated or reordered scarce supplement review')
            used.add(index); reviewed.add(pid)
            record = records[index]
            _require(set(event) == {'t', 'type', 'event_sequence', 'patient', 'record_index'}
                     and set(record) == {'inputs', 'result', 'event_sequence'}
                     and record['event_sequence'] == current_sequence, 'supplement record/marker has unknown fields')
            _require(pid in requests and workflow.states.get(pid) == 'SEARCH'
                     and at == requests[pid]['t'] and counts[pid] == 1,
                     'supplement did not follow the first currently registered same-tick inquiry')
            patient = patient_view(pid)
            _require(len(patient['pending_inquiries']) == 1, 'original inquiry not retained during supplementation')
            original = patient['pending_inquiries'][0]
            original_sequence = original['ask_event_sequence']
            ask = indexed[original_sequence]
            _require(ask['type'] == 'ask' and ask['patient'] == pid and ask['t'] == at
                     and original['submitted_at'] == at and original_sequence < current_sequence,
                     'supplement lacks an actual preceding first same-tick inquiry')
            selector_sequence = record['inputs']['original_selector_event_sequence']
            _require(selector_sequence in selectors and selector_sequence < original_sequence,
                     'supplement references missing or future original selector')
            selector = selectors[selector_sequence]
            _require(selector['patient'] == pid and selector['t'] == at
                     and selector['decision']['send'] == [original['hospital']],
                     'supplement rewrote or substituted original selector action')
            position = _position(scenario, branch, pid, at)
            structural_alternatives = [target for target in patient['compatible_hospitals']
                if target != original['hospital']]
            structural_alternatives.sort(key=lambda target: (
                recovery._synthetic_minutes(position, hospitals[target]), target))
            current_checks = [row for row in checks if row['patient'] == pid and row['t'] == at
                              and row['event_sequence'] > original_sequence]
            _require([row['hospital'] for row in current_checks] ==
                     [original['hospital'], *structural_alternatives],
                     'supplement candidate checks omitted, duplicated or reordered structural alternatives')
            eligible = [row['hospital'] for row in current_checks[1:] if row['eligible']]
            cohort = []
            for target in sorted(requests):
                if workflow.states.get(target) != 'SEARCH':
                    continue
                row = patient_view(target)
                row['resource_views'] = {only: information._views('observed', workflow.observations,
                    workflow.truth, only, row['demand'], at) for only in row['compatible_hospitals']} \
                    if len(row['compatible_hospitals']) == 1 else {}
                cohort.append(row)
            candidates = [dict(hospital=target,
                travel_minutes=recovery._synthetic_minutes(position, hospitals[target]),
                resources=information._views('observed', workflow.observations, workflow.truth,
                                             target, patient['demand'], at))
                for target in [original['hospital'], *eligible]]
            snapshot = dict(t=at, context='initial_supplement', patient=patient, candidates=candidates,
                            registered_search_patients=cohort)
            open_count = len(patient['pending_inquiries'])
            episode_count = contacts[pid, episodes[pid]]
            inputs = dict(opportunity_snapshot=snapshot, original_ask_event_sequence=original_sequence,
                original_selector_event_sequence=selector_sequence, eligible_new_hospitals=eligible,
                max_new=max(0, min(3-open_count, 10-episode_count)),
                episode_contacts=episode_count, open_inquiries=open_count)
            _require(base._same(record['inputs'], inputs),
                     'supplement input differs from prior registration, original requests or received resources')
            opportunity = _scarce_expected(snapshot)
            safe = [target for target in opportunity['offered_after'] if target in eligible]
            change = (inputs['max_new'] > 0 and opportunity['reason'] == 'SCARCE_ALTERNATIVE'
                and original['hospital'] in {row['hospital'] for row in opportunity['blocked']} and bool(safe))
            chosen = min(safe, key=lambda target: (
                recovery._synthetic_minutes(position, hospitals[target]), target)) if change else None
            expected = dict(opportunity_result=opportunity, send=[] if chosen is None else [chosen],
                            reason='NO_ACTION' if chosen is None else 'INITIAL_SCARCE_SUPPLEMENT')
            _require(base._same(record['result'], expected),
                     'supplement differs from independent fresh alternative, priority, deficit or deadline criterion')
            following = indexed.get(current_sequence+1)
            if chosen is not None:
                _require(following is not None and following['type'] == 'ask'
                    and following['patient'] == pid and following['hospital'] == chosen and following['t'] == at,
                    'selected supplement lacks immediate actual new inquiry')
                sent += 1
            else:
                _require(following is None or following['type'] != 'ask' or following.get('patient') != pid,
                         'no-action supplement generated an inquiry')
        workflow.apply(event)
    _require(used == set(range(len(records))) and len(records) == len(markers),
             'orphan scarce supplement record or marker')
    first_asks = {}
    for event in events:
        if event['type'] == 'ask':
            first_asks.setdefault(event['patient'], event)
    eligible_reviews = {pid for pid, event in first_asks.items()
        if event['t'] == requests[pid]['t'] and any(row['patient'] == pid and row['t'] == event['t']
            and row['event_sequence'] < event['event_sequence'] and row['decision']['send'] == [event['hospital']]
            for row in selectors.values())}
    _require(reviewed == eligible_reviews, 'first same-tick single inquiry omitted its required supplement review')
    _require(branch['metrics']['scarce_supplement_reviews'] == len(records)
             and branch['metrics']['scarce_supplement_sent'] == sent, 'supplement metrics differ from raw events')
    return dict(scarce_supplement_reviews=len(records), scarce_supplement_sent=sent)

def audit_inquiries(scenario, branch):
    """Rebuild FIFO work, feedback and selector inputs from earlier emissions."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    end = _end(scenario, branch)
    selector = branch['inquiry_selector']
    records = branch['inquiry_decisions']
    if selector == 'fixed_3':
        _require(not records, 'fixed policy contains selector records')
    else:
        _require(selector in ('v2', 'v3', 'v4'), 'unknown inquiry selector')
    _require(branch['initial_inquiry_history'] == scenario.get('initial_inquiry_history', []),
             'initial inquiry history differs from input')
    _require(branch['time_origin_seconds'] == CLOCK_ORIGIN_SECONDS
             and branch['time_step_seconds'] == 15, 'selector clock differs from protocol')
    specs = {row['id']: row for row in scenario['patients']}
    hospitals = {row['id']: row for row in scenario['hospitals']}
    queue = scenario.get('inquiry_queue', {})
    available = {hid: [0] * queue.get('workers', 1) for hid in hospitals}
    jobs, handles, active, unresolved, feedback, latest, checks = [], {}, {}, {}, [], {}, []
    truth = _TruthLedger(scenario)
    expected_review, used_records, episode_contacts = Counter(), set(), Counter()
    covered_asks = set()
    supplemental_asks = set()
    current_episode = {pid: 0 for pid in specs}
    background = sorted(enumerate(queue.get('background_jobs', [])), key=lambda pair: pair[1]['t'])
    scheduled_background = set()

    def seconds(value):
        return CLOCK_ORIGIN_SECONDS + int(value * 60)

    def enqueue(hid, at, pid, attempt, service, silent=False, kind='inquiry'):
        workers = available[hid]
        worker = min(range(len(workers)), key=lambda i: (workers[i], i))
        start = max(at, workers[worker])
        finish = start + service
        workers[worker] = finish
        job = dict(hospital=hid, patient=pid, attempt=attempt, submitted_t=at,
                   start_t=start, finish_t=finish, service_minutes=service, kind=kind, silent=silent)
        jobs.append(job)
        for when, typ in ((start, 'review_started'), (finish, 'review_completed')):
            if when <= end:
                expected_review[when, typ, hid, pid, attempt, kind] += 1
        return finish

    for event in events:
        at, kind, seq = event['t'], event['type'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if queue.get('enabled'):
            for index, row in background:
                if index not in scheduled_background and row['t'] <= at:
                    enqueue(row['hospital'], row['t'], None, 0, row['service_minutes'], kind='background')
                    scheduled_background.add(index)
        if kind == 'ask':
            _require(pid in specs and hid in hospitals, 'inquiry target is undeclared')
            key = (pid, hid, event['attempt'])
            _require(key not in handles and (pid, hid) not in active, 'duplicate active inquiry attempt')
            _require(event['submitted_at'] == at and event['timeout_at'] == at + scenario.get('timeout_minutes', 6),
                     'inquiry absolute handle or deadline changed')
            silent, delay = _reply_environment(scenario, pid, hid, event['attempt'])
            if queue.get('enabled'):
                finish = enqueue(hid, at, pid, event['attempt'], delay, silent)
                late = finish > event['timeout_at']
                due = event['timeout_at'] if silent else min(event['timeout_at'], finish)
                silent = silent or late
                if late and not _reply_environment(scenario, pid, hid, event['attempt'])[0] and finish <= end:
                    expected_review[finish, 'late_reply', hid, pid, event['attempt'], None] += 1
            else:
                due = event['timeout_at'] if silent else at + delay
            handle = dict(hospital=hid, patient=pid, submitted_at=seconds(at),
                          submitted_minute=at, due=due, silent=silent, attempt=event['attempt'],
                          ask_event_sequence=seq)
            handles[key] = handle
            active[pid, hid] = handle
            unresolved[seq] = handle
            latest[pid] = at
            episode = event['inquiry_episode']
            _require(type(episode) is int and episode >= current_episode[pid], 'inquiry episode moved backwards')
            current_episode[pid] = episode
            episode_contacts[pid, episode] += 1
            if selector != 'fixed_3':
                _require(episode_contacts[pid, episode] <= 10, 'episode contact budget exceeded')
        elif kind == 'inquiry_feedback':
            matches = [key for key, handle in unresolved.items() if handle['patient'] == pid
                       and handle['hospital'] == hid and handle['submitted_minute'] == event['submitted_t']]
            _require(len(matches) == 1, 'feedback lacks a unique submitted inquiry')
            handle = unresolved.pop(matches[0])
            _require(at == event['observed_t'] == handle['due']
                     and event['outcome'] == ('timeout' if handle['silent'] else 'reply'),
                     'feedback timing or result differs from original FIFO/deadline')
            feedback.append(dict(hospital=hid, patient=pid, submitted_at=handle['submitted_at'],
                                 observed_at=seconds(at), outcome=event['outcome']))
        elif kind in ('accept', 'decline', 'no_response'):
            callback = _is_callback_accept(event, indexed)
            if callback:
                _require((pid, hid) not in active, 'late callback overlaps a real pending inquiry')
            else:
                _require((pid, hid) in active, 'patient reply lacks currently retained inquiry')
                handle = active.pop((pid, hid))
                _require(at == handle['due'], 'patient reply changed original delivery time')
                _require((kind == 'no_response') == handle['silent'], 'silent inquiry returned a fabricated reply')
            if kind != 'no_response':
                demand = specs[pid].get('resource_demands', {'ed_bed': 1})
                physical = truth.resources(hid, demand, at)
                can_accept = (truth.patients[pid]['capabilities'] <= truth.capabilities[hid]
                    and all(values['free'] >= demand[resource] for resource, values in physical.items()))
                _require((kind == 'accept') == can_accept,
                         'patient acceptance/decline differs from current physical capacity/capability')
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            active = {key: value for key, value in active.items() if key[0] != pid}
            if kind == 'transfer_search':
                current_episode[pid] += 1
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            active = {key: value for key, value in active.items() if key[0] != pid}
            for row in event['pending']:
                key = (pid, row['hospital'], row['attempt'])
                _require(key in handles and row['ask_event_sequence'] == handles[key]['ask_event_sequence'],
                         'backup inquiry did not preserve original attempt')
                active[pid, row['hospital']] = handles[key]
        elif kind == 'information_check' and event['purpose'] == 'inquiry':
            checks.append(event)
        elif kind == 'scarce_supplement_review':
            record = branch['scarce_supplement_records'][event['record_index']]
            following = [e for e in events if e['event_sequence'] == seq + 1 and e['type'] == 'ask'
                         and e['t'] == at and e.get('patient') == pid]
            _require([e['hospital'] for e in following] == record['result']['send'],
                     'supplement marker differs from immediately submitted inquiry')
            supplemental_asks.update(e['event_sequence'] for e in following)
        elif kind == 'joint_inquiry_policy':
            index = event['record_index']
            _require(index == len(used_records) and index < len(records), 'missing or reordered inquiry record')
            used_records.add(index)
            row = records[index]
            _require(set(row) == {'t', 'patient', 'inquiry', 'inputs', 'decision', 'event_sequence',
                                  'inquiry_episode', 'ask_event_sequences', 'resource_check_sequences'},
                     'inquiry record contains future or unknown fields')
            _require((row['t'], row['patient'], row['inquiry'], row['event_sequence']) ==
                     (at, pid, selector, seq), 'inquiry record marker differs')
            point = _position(scenario, branch, pid, at)
            current_checks = [check for check in checks if check['t'] == at and check['patient'] == pid]
            _require(row['resource_check_sequences'] == [check['event_sequence'] for check in current_checks],
                     'inquiry source check references differ from earlier emitted checks')
            allowed = [check['hospital'] for check in current_checks if check['eligible']]
            _require(len(allowed) == len(set(allowed)), 'duplicate candidate checks in one inquiry review')
            pending = [{k: value[k] for k in ('submitted_at', 'hospital', 'patient')}
                       for value in unresolved.values()]
            own_handles = [value for (patient, _), value in active.items() if patient == pid]
            own = [dict(submitted_at=value['submitted_at'], hospital=value['hospital'], patient=pid,
                        travel_seconds=recovery._synthetic_minutes(point, hospitals[value['hospital']]) * 60)
                   for value in own_handles]
            history = [dict(hospital=value['hospital'], patient=value.get('patient'),
                            submitted_at=CLOCK_ORIGIN_SECONDS + value['submitted_at'],
                            observed_at=CLOCK_ORIGIN_SECONDS + value['observed_at'], outcome=value['outcome'])
                       for value in scenario.get('initial_inquiry_history', [])]
            history.extend(feedback)
            episode = row['inquiry_episode']
            _require(type(episode) is int and episode >= current_episode[pid], 'selector episode moved backwards')
            current_episode[pid] = episode
            expected = dict(now=seconds(at), candidates=[dict(hospital_id=target,
                travel=dict(duration_seconds=recovery._synthetic_minutes(point, hospitals[target]) * 60))
                for target in allowed], observations=history, pending=pending, patient_pending=own,
                max_new=max(0, min(3 - len(own), 10 - episode_contacts[pid, episode])),
                last_contact_at=seconds(latest[pid]) if pid in latest else None)
            _require(base._same(row['inputs'], expected),
                     'selector input differs from earlier raw inquiry/feedback/resource-check ledger')
            _require(row['ask_event_sequences'] == [value['ask_event_sequence'] for value in own_handles],
                     'selector active handles differ from original requests')
            reproduced = _selector_reproduce(selector, expected)
            _require(base._same(row['decision'], reproduced), 'actual pure selector did not reproduce decision')
            next_supplement = min((e['event_sequence'] for e in events if e['event_sequence'] > seq
                and e['t'] == at and e['type'] == 'scarce_supplement_review' and e.get('patient') == pid), default=float('inf'))
            following = [e for e in events if seq < e['event_sequence'] < next_supplement and e['t'] == at
                         and e['type'] == 'ask' and e.get('patient') == pid]
            _require([e['hospital'] for e in following] == reproduced['send'], 'selector send differs from actual inquiries')
            _require(not covered_asks.intersection(e['event_sequence'] for e in following),
                     'one inquiry was covered by multiple selector decisions')
            covered_asks.update(e['event_sequence'] for e in following)
        if kind == 'condition_update' and pid in truth.patients:
            truth.patients[pid]['capabilities'] = set(event['required_capabilities'])
        truth.apply(event)
    _require(used_records == set(range(len(records))), 'orphan inquiry policy record')
    if selector != 'fixed_3':
        _require(not covered_asks.intersection(supplemental_asks) and covered_asks | supplemental_asks ==
                 {e['event_sequence'] for e in events if e['type'] == 'ask'},
                 'actual inquiry lacks a unique reproduced selector decision')
    _require(all(handle['due'] > end for handle in unresolved.values()), 'missing due inquiry feedback')
    actual_reviews = Counter((e['t'], e['type'], e.get('hospital'), e.get('patient'), e.get('attempt'),
                              e.get('kind')) for e in events
                             if e['type'] in ('review_started', 'review_completed', 'late_reply'))
    _require(actual_reviews == expected_review, 'hospital FIFO work changed, disappeared or used future completion')
    if queue.get('enabled'):
        rows = []
        for hid in hospitals:
            local = [job for job in jobs if job['hospital'] == hid and job['submitted_t'] <= end]
            rows.append(dict(hospital=hid, workers=queue.get('workers', 1),
                waiting=sum(end < job['start_t'] for job in local),
                processing=sum(job['start_t'] <= end < job['finish_t'] for job in local),
                completed=sum(job['finish_t'] <= end for job in local), submitted=len(local),
                queue_wait_total_minutes=sum(max(0, min(end, j['start_t']) - j['submitted_t']) for j in local),
                service_total_minutes=sum(max(0, min(end, j['finish_t']) - j['start_t']) for j in local)))
        aggregate = {key: sum(row[key] for row in rows) for key in rows[0] if key != 'hospital'}
        _require(base._same(branch['inquiry_queue_report'], dict(end_t=end, aggregate=aggregate, by_hospital=rows)),
                 'FIFO queue aggregate differs from independently rebuilt nonpreemptive work')
    return dict(selector_reproductions=len(records), original_inquiry_handles=len(handles),
                fifo_jobs=len(jobs), current_feedback_receipts=len(feedback))


def _fingerprint(condition):
    return sha256(json.dumps(condition, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')).hexdigest()


def _condition(workflow, pid):
    row = workflow._base_patient(pid)
    return dict(id=pid, priority=row['priority'], demand=deepcopy(row['demand']),
                required_capabilities=sorted(row['required_capabilities']))


def _fresh(views, demand):
    return all(views.get(resource, {}).get('status') == 'fresh'
        and type(views[resource].get('free')) in (int, float)
        and math.isfinite(views[resource]['free']) and views[resource]['free'] >= amount
        for resource, amount in demand.items())


def _late_expected(snapshot):
    """Independent arithmetic over received evidence, never the policy function."""
    at, reply, patient = snapshot['t'], snapshot['receipt'], snapshot['patient']
    result = dict(eligible=False, hospital=None, expires_at=None,
                  protected_patients=[], reason='OUTSIDE_WINDOW')
    if not reply['timeout_at'] < at <= reply['timeout_at'] + .25:
        return result
    if patient['state'] != 'SEARCH':
        return dict(result, reason='NOT_SEARCHING')
    if patient['first_commit_t'] is not None:
        return dict(result, reason='PREVIOUS_COMMITMENT')
    if patient['condition_fingerprint'] != reply['condition_fingerprint']:
        return dict(result, reason='CONDITION_CHANGED')
    if patient['latest_attempt'] != reply['attempt']:
        return dict(result, reason='NEWER_INQUIRY')
    demand = patient['condition']['demand']
    if any(offer['expires_at'] > at and _fresh(offer['resources'], demand)
           for offer in patient['valid_offers']):
        return dict(result, reason='OTHER_VALID_OFFER')
    if reply['outcome'] != 'accept':
        return dict(result, reason='HOSPITAL_DECLINED')
    if not _fresh(snapshot['resources'], demand):
        return dict(result, reason='RESOURCE_NOT_VERIFIED')
    claims = []
    for other in snapshot['registered_search_patients']:
        if other['id'] == patient['id'] or other['condition']['priority'] < patient['condition']['priority'] \
                or other['compatible_hospitals'] != [reply['hospital']]:
            continue
        other_demand = other['condition']['demand']
        if other_demand.get('ed_bed', 0) <= 0 or not _fresh(
                other['resource_views'].get(reply['hospital'], {}), other_demand):
            continue
        pending = any(handle['hospital'] == reply['hospital']
            and at < min(handle['submitted_at'] + 1, handle['timeout_at'])
            for handle in other['pending_inquiries'])
        offered = any(offer['hospital'] == reply['hospital'] and offer['expires_at'] > at
                      for offer in other['valid_offers'])
        if pending or offered:
            claims.append(other)
    if demand.get('ed_bed', 0) and claims and snapshot['resources']['ed_bed']['free'] < \
            demand['ed_bed'] + sum(row['condition']['demand']['ed_bed'] for row in claims):
        return dict(result, reason='SCARCE_OPPORTUNITY', protected_patients=sorted(row['id'] for row in claims))
    return dict(eligible=True, hospital=reply['hospital'],
                expires_at=at + snapshot['accept_ttl_minutes'],
                protected_patients=[], reason='RECEIVED_LATE_OFFER')


def _fifo_deliveries(scenario, events):
    """Audit-only hospital calendar; it is never included in decision inputs."""
    queue = scenario.get('inquiry_queue', {})
    workers = {row['id']: [0] * queue.get('workers', 1) for row in scenario['hospitals']}
    background = sorted(enumerate(queue.get('background_jobs', [])), key=lambda pair: pair[1]['t'])
    done, deliveries = set(), {}

    def enqueue(hid, at, service):
        available = workers[hid]
        index = min(range(len(available)), key=lambda item: (available[item], item))
        finish = max(at, available[index]) + service
        available[index] = finish
        return finish

    for event in events:
        if queue.get('enabled'):
            for index, row in background:
                if index not in done and row['t'] <= event['t']:
                    enqueue(row['hospital'], row['t'], row['service_minutes'])
                    done.add(index)
        if event['type'] != 'ask':
            continue
        silent, delay = _reply_environment(scenario, event['patient'], event['hospital'], event['attempt'])
        finish = enqueue(event['hospital'], event['t'], delay) if queue.get('enabled') else event['t'] + delay
        deliveries[event['event_sequence']] = dict(finish=finish, silent=silent)
    return deliveries


def audit_late_offers(scenario, branch):
    """Check every callback and new acceptance against original raw emissions."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    enabled = branch.get('late_reply_recovery_enabled', False)
    _require(type(enabled) is bool, 'late recovery mode is not explicit')
    marker_names = {'late_offer_callback_registered', 'hospital_reply_received',
                    'late_offer_review', 'late_offer_accepted'}
    if not enabled:
        _require(not branch.get('late_offer_records') and not branch.get('late_offer_callbacks')
                 and not any(event['type'] in marker_names or 'late_offer_event_sequence' in event for event in events),
                 'disabled branch contains late callbacks or acceptances')
        return dict(late_offer_callbacks=0, late_offer_receipts=0, late_offer_accepted=0,
                    late_offer_rejected=0, callback_acceptance_proofs=0)
    records, exports = branch['late_offer_records'], branch['late_offer_callbacks']
    _require(isinstance(records, list) and isinstance(exports, list), 'invalid callback export')
    workflow = _LateWorkflow(deepcopy(scenario))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    deliveries = _fifo_deliveries(scenario, events)
    callbacks, requests, attempts, handles = {}, {}, {}, {}
    first_commits, accepts, used, receipts, adoption_sequences = {}, {}, set(), {}, set()
    timed_out_handles, timeout_feedback = {}, set()
    supplementary = {row['event_sequence']: row for row in branch['scarce_supplement_records']}
    expected_callback_asks = set()
    for row in branch['scarce_supplement_records']:
        if row['result']['send']:
            event = indexed.get(row['event_sequence'] + 1)
            _require(event is not None and event['type'] == 'ask', 'callback source supplement omitted actual ask')
            expected_callback_asks.add(event['event_sequence'])

    def views(hid, pid, at):
        return _views('observed', workflow.observations, workflow.truth, hid,
                      workflow._base_patient(pid)['demand'], at)

    def observed_search(at):
        result = []
        for pid in sorted(requests):
            if workflow.states.get(pid) != 'SEARCH':
                continue
            row = workflow._base_patient(pid)
            compatible = sorted(hid for hid in row['compatible_hospitals'] if all(
                hospitals[hid].get('capacity', {}).get(resource, 0) >= amount
                for resource, amount in row['demand'].items()))
            offers = [dict(hospital=hid, accepted_at=workflow.accepted[pid, hid], expires_at=expiry,
                accept_event_sequence=accepts[pid, hid]['event_sequence'])
                for (patient, hid), expiry in sorted(workflow.offers.items()) if patient == pid]
            result.append(dict(id=pid, condition=_condition(workflow, pid), state='SEARCH',
                request_t=row['request_t'], request_event_sequence=requests[pid]['event_sequence'],
                compatible_hospitals=compatible,
                pending_inquiries=[deepcopy(value) for (patient, _), value in handles.items() if patient == pid],
                valid_offers=offers,
                resource_views={hid:views(hid, pid, at) for hid in compatible} if len(compatible) == 1 else {}))
        return result

    for event in events:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'request':
            requests[pid] = event
        elif kind == 'ask':
            attempts[pid, hid] = event['attempt']
            handles[pid, hid] = dict(hospital=hid, attempt=event['attempt'], submitted_at=at,
                timeout_at=event['timeout_at'], ask_event_sequence=seq)
        elif kind == 'inquiry_feedback' and event['outcome'] == 'timeout':
            timeout_feedback.add((pid, hid, event['submitted_t'], event['observed_t']))
        elif kind == 'late_offer_callback_registered':
            source = indexed.get(seq - 1)
            _require(source is not None and source['type'] == 'ask' and source['event_sequence'] in expected_callback_asks
                     and pid not in {row['patient'] for row in callbacks.values()},
                     'callback did not follow its one actual initial supplemental inquiry')
            review = indexed.get(source['event_sequence'] - 1)
            _require(review is not None and review['type'] == 'scarce_supplement_review'
                     and review['event_sequence'] in supplementary, 'callback lacks an actual supplement review')
            condition = _condition(workflow, pid)
            expected = dict(patient=pid, hospital=source['hospital'], attempt=source['attempt'],
                submitted_at=source['t'], timeout_at=source['timeout_at'], ask_event_sequence=source['event_sequence'],
                supplement_review_event_sequence=review['event_sequence'], condition_fingerprint=_fingerprint(condition))
            _require(base._same(event, dict(expected, condition=condition, t=at, type=kind, event_sequence=seq)),
                     'callback registration changed original handle, condition or deadline')
            callbacks[source['event_sequence']] = expected
            exported = dict(expected, condition=condition, event_sequence=seq)
            _require(len(callbacks) <= len(exports) and base._same(exports[len(callbacks) - 1], exported),
                     'callback export differs from original registration')
        elif kind == 'hospital_reply_received':
            ask_sequence = event['ask_event_sequence']
            _require(ask_sequence in callbacks and ask_sequence not in receipts,
                     'callback receipt is duplicated or lacks registration')
            callback = callbacks[ask_sequence]
            delivery = deliveries[ask_sequence]
            _require(scenario.get('inquiry_queue', {}).get('enabled')
                     and not delivery['silent'] and delivery['finish'] == at > callback['timeout_at'],
                     'received late callback lacks actual non-silent hospital completion after original timeout')
            _require((pid, hid, callback['submitted_at'], callback['timeout_at']) in timeout_feedback,
                     'late callback omitted the unchanged original timeout feedback')
            previous, completed = indexed.get(seq - 1), indexed.get(seq - 2)
            _require(previous is not None and completed is not None
                     and base._same(previous, dict(t=at, type='late_reply', patient=pid, hospital=hid,
                                                  attempt=callback['attempt'], ignored=True, event_sequence=seq - 1))
                     and base._same(completed, dict(t=at, type='review_completed', patient=pid, hospital=hid,
                                                    attempt=callback['attempt'], kind='inquiry', event_sequence=seq - 2)),
                     'callback receipt lacks immediate current completed work and expired-original late reply')
            physical = workflow.truth.resources(hid, workflow._base_patient(pid)['demand'], at)
            condition = _condition(workflow, pid)
            can_accept = set(condition['required_capabilities']) <= workflow.truth.capabilities[hid] \
                and all(values['free'] >= condition['demand'][resource] for resource, values in physical.items())
            expected = dict(callback, received_at=at, outcome='accept' if can_accept else 'decline')
            _require(base._same(event, dict(expected, t=at, type=kind, event_sequence=seq)),
                     'received hospital result differs from actual current physical capacity or original handle')
            receipts[ask_sequence] = dict(expected, receipt_event_sequence=seq)
        elif kind == 'late_offer_review':
            index = event['record_index']
            _require(index == len(used) and index < len(records), 'missing or reordered late-offer record')
            used.add(index)
            record = records[index]
            _require(set(record) == {'snapshot', 'result', 'event_sequence'} and record['event_sequence'] == seq,
                     'late-offer record has unknown fields or wrong raw marker')
            receipt = record['snapshot']['receipt']
            _require(receipt['ask_event_sequence'] in receipts
                     and base._same(receipt, receipts[receipt['ask_event_sequence']])
                     and receipt['receipt_event_sequence'] < seq and receipt['received_at'] == at,
                     'late-offer review used unreceived or different hospital outcome')
            _require(base._same(event, dict(t=at, type=kind, event_sequence=seq, patient=pid, hospital=hid, record_index=index)),
                     'late-offer marker has unknown or mismatched fields')
            condition = _condition(workflow, pid)
            snapshot = dict(t=at, receipt=deepcopy(receipt), patient=dict(id=pid,
                state=workflow.states[pid], condition=condition, condition_fingerprint=_fingerprint(condition),
                latest_attempt=attempts[pid, hid], first_commit_t=first_commits.get(pid),
                valid_offers=[dict(hospital=target, expires_at=expiry, resources=views(target, pid, at))
                    for (patient, target), expiry in sorted(workflow.offers.items()) if patient == pid],
                registered_request_sequence=requests[pid]['event_sequence']),
                resources=views(hid, pid, at), accept_ttl_minutes=scenario.get('accept_ttl_minutes', 10),
                registered_search_patients=observed_search(at))
            _require(base._same(record['snapshot'], snapshot),
                     'late-offer input differs from raw current registration, condition, offers, original inquiries or reports')
            expected = _late_expected(snapshot)
            _require(base._same(record['result'], expected),
                     'late-offer decision differs from independent window, lifecycle, resource or opportunity check')
            following = indexed.get(seq + 1)
            if expected['eligible']:
                original_timeout = timed_out_handles.get(receipt['ask_event_sequence'])
                _require(original_timeout is not None and original_timeout['t'] == receipt['timeout_at'],
                         'adopted late offer did not terminate its original inquiry with unchanged no-response')
                marker = dict(t=at, type='late_offer_accepted', event_sequence=seq + 1, patient=pid, hospital=hid,
                    attempt=receipt['attempt'], ask_event_sequence=receipt['ask_event_sequence'],
                    receipt_event_sequence=receipt['receipt_event_sequence'], review_event_sequence=seq,
                    expires_at=expected['expires_at'])
                _require(following is not None and base._same(following, marker),
                         'eligible callback lacks immediate new-offer adoption with actual receipt lifetime')
                ordinary = indexed.get(seq + 2)
                _require(ordinary is not None and base._same(ordinary, dict(t=at, type='accept', event_sequence=seq + 2,
                    patient=pid, hospital=hid, late_offer_event_sequence=seq + 1,
                    receipt_event_sequence=receipt['receipt_event_sequence'])),
                    'late-offer adoption lacks exactly one immediate separately sourced ordinary acceptance')
                adoption_sequences.add(seq + 1)
            else:
                _require(following is None or following['type'] != 'late_offer_accepted',
                         'ineligible late reply created a new acceptance')
        elif kind in ('accept', 'decline', 'no_response'):
            if not _is_callback_accept(event, indexed):
                if kind == 'no_response' and (pid, hid) in handles:
                    timed_out_handles[handles[pid, hid]['ask_event_sequence']] = event
                handles.pop((pid, hid), None)
            if kind == 'accept':
                accepts[pid, hid] = event
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            if kind == 'commit':
                first_commits.setdefault(pid, at)
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            for handle in event['pending']:
                handles[pid, handle['hospital']] = deepcopy(handle)
        workflow.apply(event)
    _require(set(callbacks) == expected_callback_asks and len(exports) == len(callbacks),
             'missing, orphan or duplicated supplemental callback registration')
    expected_receipts = {sequence for sequence, callback in callbacks.items()
        if scenario.get('inquiry_queue', {}).get('enabled') and not deliveries[sequence]['silent']
        and callback['timeout_at'] < deliveries[sequence]['finish'] <= _end(scenario, branch)}
    _require(set(receipts) == expected_receipts and used == set(range(len(records)))
             and len(records) == len(receipts), 'actual late delivery or its required review is missing')
    _require(adoption_sequences == {event['event_sequence'] for event in events if event['type'] == 'late_offer_accepted'},
             'orphan callback adoption marker')
    actual_acceptances = {event['event_sequence'] for event in events if 'late_offer_event_sequence' in event}
    _require(actual_acceptances == {seq + 1 for seq in adoption_sequences}, 'orphan or fabricated callback acceptance')
    _require(not workflow.late_adoptions, 'unconsumed callback adoption')
    counts = dict(late_offer_callbacks=len(callbacks), late_offer_receipts=len(receipts),
        late_offer_accepted=len(adoption_sequences), late_offer_rejected=len(receipts) - len(adoption_sequences),
        callback_acceptance_proofs=len(adoption_sequences))
    for key in ('late_offer_receipts', 'late_offer_accepted', 'late_offer_rejected'):
        _require(branch['metrics'][key] == counts[key], 'callback endpoint count differs from raw evidence: ' + key)
    information._number(branch['metrics']['late_offer_wall_seconds'], 'late-offer execution time')
    return counts


def audit_branch(scenario, branch, *, exact_limit=20000):
    """Audit original, unmodified emissions including separately received offers."""
    ordered = _indexed_events(branch)
    _require(all(joint._clock(event['t']) for event in ordered), 'off-grid event clock')
    _require(branch.get('ordered_emissions', ordered) == ordered, 'ordered emission export differs')
    _require(branch.get('resource_updates_enabled') is False and not any(
        event['type'] in ('resource_update_captured', 'resource_update_cycle')
        or (event['type'] == 'resource_report_received' and 'publication_id' in event)
        for event in ordered), 'extra resource publication channel was enabled')
    if branch.get('cohort_registration_enabled', False) or branch.get('scarce_routing_enabled', False):
        expected_spec = dict(inquiry=branch['inquiry_selector'], allocation=branch['policy']['rule'],
            cohort_registration=branch['cohort_registration_enabled'], scarce_routing=branch['scarce_routing_enabled'],
            actual_action='parallel_scarce_supplement', original_selector_candidates_unchanged=True,
            maximum_supplements_per_patient=1, recovery=True, pending_protection=True,
            pending_window_minutes=1, time_step_seconds=15, actual_selector_reused=True,
            extra_resource_change_publications=False)
        _require(branch.get('scarce_delay_spec') == expected_spec, 'scarce intervention protocol differs')
    if branch.get('late_reply_recovery_enabled', False):
        expected_spec = dict(late_reply_recovery=True, scope='one_initial_scarce_supplement',
            late_reply_window_minutes=.25, original_timeout_unchanged=True,
            original_timeout_feedback_unchanged=True, blocking_wait_added=False,
            hospital_work_calendar_unchanged=True, patient_condition_fingerprint_required=True,
            new_offer_ttl_from_actual_receipt=True, actual_reply_outcome_required=True,
            current_fresh_resource_check_required=True, current_valid_offer_rejects=True,
            newer_same_hospital_attempt_rejects=True)
        _require(branch.get('deadline_recovery_spec') == expected_spec, 'late-offer fixed protocol differs')
    late = audit_late_offers(deepcopy(scenario), branch)
    result = joint._audit_information_branch(deepcopy(scenario), branch, exact_limit=exact_limit)
    counts = dict(result['counts'])
    counts.update(late)
    counts.update(scarce.audit_cohorts(deepcopy(scenario), branch))
    counts.update(audit_supplements(deepcopy(scenario), branch))
    counts.update(audit_inquiries(deepcopy(scenario), branch))
    counts.update(joint.audit_stability(deepcopy(scenario), branch))
    counts.update(recovery.audit_motion(deepcopy(scenario), branch))
    counts.update(audit_backups(deepcopy(scenario), branch))
    counts.update(audit_guard(deepcopy(scenario), branch))
    counts.update(audit_pending_guard(deepcopy(scenario), branch))
    counts.update(audit_early_offers(deepcopy(scenario), branch))
    counts.update(recovery.audit_no_added_source(deepcopy(scenario), branch))
    waiting = joint.waiting_metrics(deepcopy(scenario), branch)
    for key, value in result['metrics'].items():
        if key in branch['metrics']:
            _require(base._same(branch['metrics'][key], value), 'whole-cohort endpoint metric mismatch: ' + key)
    return dict(passed=True, counts=counts, waiting=waiting, metrics=result['metrics'],
        information=result['information'], assignment_constraint_violations=0,
        selector_check='actual pure function reproduction from independently reconstructed raw inputs',
        actual_optimality_claimed=False)


def audit_deadline_recovery_replay(result, *, exact_limit=20000):
    _require(result.get('schema_version') == 1 and result.get('experiment_id') == EXPERIMENT_ID,
             'unknown deadline-recovery replay schema or experiment')
    branches = result['branches']
    ids = [row['id'] for row in branches]
    _require(ids and len(ids) == len(set(ids)) and len(result['execution_order']) == len(ids)
             and set(result['execution_order']) == set(ids), 'missing or duplicated replay branch')
    counts, details = Counter(), {}
    for branch in branches:
        checked = audit_branch(result['scenario'], branch, exact_limit=exact_limit)
        details[branch['id']] = checked
        counts.update(checked['counts'])
    return dict(passed=True, counts=dict(counts), branches=len(branches), branch_audits=details,
                assignment_constraint_violations=0, actual_optimality_claimed=False)

