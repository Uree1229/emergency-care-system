"""Full original raw audit of a declared public-resource offer-readiness stage.

The original early-offer stage and fixed clinical/physical/FIFO/allocation
checks remain intact. Only the new readiness proof, the guard's current ready
set and backup readiness provenance are explicitly extended. No engine,
solver, raw projection, compatibility mutation or global patch is imported.
"""
from collections import Counter
from copy import deepcopy
from . import recovery_rank_audit as rank

parent = rank.parent
base, information, cooperative = rank.base, rank.information, rank.cooperative
independent, joint = rank.independent, rank.joint
scarce, recovery, safe = parent.scarce, parent.recovery, parent.safe
responsive = cooperative.responsive
_require, _indexed_events, _literal = rank._require, rank._indexed_events, rank._literal
_end, _views, _position = cooperative._end, cooperative._views, cooperative._position
_is_callback_accept = cooperative._is_callback_accept
_after_queue_events = cooperative._after_queue_events
_pending_rows, _offer_rows = cooperative._pending_rows, cooperative._offer_rows
PublicOfferAuditError = rank.RecoveryRankAuditError
EXPERIMENT_ID = 'public_offer_20261020'
VARIANTS = ('public_blocked',)


class _PublicReadyAppendOrder:
    """Retain the original ready prefix, then replay actual new-stage appends.

The frozen workflow reconstructs ready rows in declared patient order. The
new stage instead appends its promotions after the complete original ready
list. Preserve that boundary from the public cycle and actual marker history;
do not infer the order from the guard record being checked.
"""
    def __init__(self, scenario):
        super().__init__(scenario)
        self.public_ready_prefix = {}
        self.public_ready_appends = {}

    def apply(self, event):
        kind, at = event['type'], event['t']
        if kind == 'public_offer_cycle':
            _require(at not in self.public_ready_prefix,
                     'duplicate public cycle cannot reset actual ready append provenance')
            self.public_ready_prefix[at] = [row['id'] for row in super().snapshot(at)['ready']]
            self.public_ready_appends[at] = []
        elif kind == 'public_offer_ready':
            _require(at in self.public_ready_prefix,
                     'new ready append lacks its actual public stage boundary')
            self.public_ready_appends[at].append((event['event_sequence'], event['patient']))
            self.promoted.add((event['patient'], at))
        super().apply(event)

    def snapshot(self, now):
        snapshot = super().snapshot(now)
        if now not in self.public_ready_prefix:
            return snapshot
        rows = {row['id']: row for row in snapshot['ready']}
        _require(len(rows) == len(snapshot['ready']), 'duplicate current ready patient')
        order = self.public_ready_prefix[now] + [pid for _, pid in self.public_ready_appends[now]]
        ready = []
        for pid in order:
            _require(pid in rows,
                     'original ready prefix or new append lacks its unique current eligible row')
            ready.append(rows.pop(pid))
        _require(not rows, 'current ready row has no original-prefix or new-marker append source')
        snapshot['ready'] = ready
        return snapshot


class _IndependentWorkflow(_PublicReadyAppendOrder, independent._ProbeWorkflow):
    pass


class _CooperativeWorkflow(_PublicReadyAppendOrder, cooperative._CooperativeWorkflow):
    pass


def _workflow(scenario):
    return (_CooperativeWorkflow if scenario.get('inquiry_queue', {}).get('enabled', False)
            else _IndependentWorkflow)(deepcopy(scenario))


def _audit_guard(scenario, branch):
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
    workflow = _workflow(scenario)
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


def _audit_backups(scenario, branch, work=None):
    """Validate original inquiry continuity, receipt, TTL and transfer reuse."""
    retained = branch.get('retained_backups_enabled')
    same_tick = branch.get('transfer_same_tick_enabled')
    followup = branch.get('recovery_followup_enabled')
    _require(type(retained) is bool and type(same_tick) is bool and type(followup) is bool
             and (not same_tick or retained) and (not followup or same_tick),
             'invalid explicit backup retention or preparation mode')
    ordered = information._indexed_events(branch)
    def reply_due(handle):
        return cooperative._work_reply_due(work, handle) if work is not None else joint._joint_reply_due(scenario, ordered, handle)
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
        elif kind in ('early_offer_ready', 'public_offer_ready'):
            early_ready[pid] = event
        elif kind == 'cooperative_probe_not_admitted':
            asks.pop((pid,hid),None)
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
            due, silent = reply_due(handle)
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
                due, _ = reply_due(handle)
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
            check,ask=indexed.get(seq+1),_after_queue_events(indexed,seq+1)
            if ask is not None and ask['type']=='joint_inquiry_policy':
                ask=_after_queue_events(indexed,ask['event_sequence'])
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


def _audit_independent_branch(scenario, branch, policy_decisions, *, exact_limit=20000):
    """Audit original, unmodified emissions including separately received offers."""
    probe_enabled = branch.get('shortage_probe_enabled', False)
    _require(type(probe_enabled) is bool, 'shortage probe mode must be explicit')
    if not probe_enabled:
        _require(not branch['public_offer_base_contract'].get('shortage_probe_spec') and not branch.get('shortage_probe_records')
                 and not branch.get('shortage_probe_nonblocking_records')
                 and not any(event['type'].startswith('shortage_probe_') or 'shortage_probe_review_event_sequence' in event
                     or (event['type'] == 'information_check' and event['purpose'] == 'shortage_probe')
                     for event in _indexed_events(branch)), 'disabled branch contains shortage intervention')
        return independent.prior.audit_branch(scenario, branch, exact_limit=exact_limit)
    _require(branch['public_offer_base_contract'].get('shortage_probe_spec') == independent.SHORTAGE_PROBE_SPEC,
             'shortage-probe protocol differs from the explicit bounded recheck contract')
    enabled = branch.get('late_pending_guard_enabled', False)
    _require(type(enabled) is bool, 'pending-work protection mode must be explicit')
    if not enabled:
        _require(not branch.get('deadline_safe_spec'), 'disabled branch contains pending-work protocol')
        raise PublicOfferAuditError('probe intervention requires pending-safe base')
    expected_safe_spec = dict(late_pending_guard=True, pending_input='original_public_inquiry_handles',
        pending_action='ignore_optional_late_reply', blocking_wait_added=False, original_timeout_unchanged=True)
    _require(branch.get('deadline_safe_spec') == expected_safe_spec
             and branch.get('late_reply_recovery_enabled') is True,
             'pending-work protection protocol differs from the fixed contract')
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
            actual_action='parallel_scarce_supplement', original_selector_candidates_unchanged=False,
            maximum_supplements_per_patient=1, recovery=True, pending_protection=True,
            pending_window_minutes=1, time_step_seconds=15, actual_selector_reused=False,
            original_seven_inputs_retained=True, base_selector_reused_as_kernel=True,
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
    probe_counts = independent.audit_probes(deepcopy(scenario), branch)
    nonblocking_counts = independent.audit_nonblocking(deepcopy(scenario), branch)
    late = independent.prior.audit_late_offers(deepcopy(scenario), branch)
    result = independent._audit_information_branch(deepcopy(scenario), branch, exact_limit=exact_limit)
    counts = dict(result['counts'])
    counts.update(late)
    counts.update(probe_counts)
    counts.update(nonblocking_counts)
    counts.update(scarce.audit_cohorts(deepcopy(scenario), branch))
    counts.update(independent.prior.audit_supplements(deepcopy(scenario), branch))
    counts.update(parent._audit_independent_inquiries(deepcopy(scenario), branch, policy_decisions))
    counts.update(joint.audit_stability(deepcopy(scenario), branch))
    counts.update(recovery.audit_motion(deepcopy(scenario), branch))
    counts.update(_audit_backups(deepcopy(scenario), branch))
    counts.update(_audit_guard(deepcopy(scenario), branch))
    counts.update(independent.prior.audit_pending_guard(deepcopy(scenario), branch))
    counts.update(independent.audit_early_offers(deepcopy(scenario), branch))
    counts.update(recovery.audit_no_added_source(deepcopy(scenario), branch))
    waiting = joint.waiting_metrics(deepcopy(scenario), branch)
    for key, value in result['metrics'].items():
        if key in branch['metrics']:
            _require(base._same(branch['metrics'][key], value), 'whole-cohort endpoint metric mismatch: ' + key)
    return dict(passed=True, counts=counts, waiting=waiting, metrics=result['metrics'],
        information=result['information'], assignment_constraint_violations=0,
        selector_check='actual pure function reproduction from independently reconstructed raw inputs',
        actual_optimality_claimed=False)


def _audit_cooperative_branch(scenario, branch, policy_decisions, *, exact_limit=20000):
    enabled=branch.get('cooperative_probe_enabled',False)
    _require(type(enabled) is bool,'cooperative workflow flag is not explicit boolean')
    if not enabled:
        _require(not branch['public_offer_base_contract'].get('cooperative_probe_spec') and not branch.get('cooperative_job_ledger')
            and not branch.get('cooperative_control_acks') and not branch.get('cooperative_ready_records')
            and not branch.get('cooperative_busy_retry_records')
            and not any(event['type'].startswith('cooperative_') or 'cooperative_job_id' in event
                for event in _indexed_events(branch)), 'disabled reference branch contains cooperative behavior')
        return independent.audit_branch(scenario,branch,exact_limit=exact_limit)
    _require(branch['public_offer_base_contract'].get('cooperative_probe_spec') == cooperative.COOPERATIVE_PROBE_SPEC,
             'cooperative workflow differs from the explicit current-control and conserved-work protocol')
    _require(branch['public_offer_base_contract'].get('shortage_probe_spec')==cooperative.COOPERATIVE_SHORTAGE_SPEC,
             'cooperative optional-work readiness was incorrectly claimed as unchanged FIFO behavior')
    expected_deadline_spec=dict(late_reply_recovery=True,scope='one_initial_scarce_supplement',
        late_reply_window_minutes=.25,original_timeout_unchanged=True,original_timeout_feedback_unchanged=True,
        blocking_wait_added=False,hospital_work_calendar_unchanged=False,patient_condition_fingerprint_required=True,
        new_offer_ttl_from_actual_receipt=True,actual_reply_outcome_required=True,current_fresh_resource_check_required=True,
        current_valid_offer_rejects=True,newer_same_hospital_attempt_rejects=True)
    _require(branch.get('deadline_recovery_spec')==expected_deadline_spec,
             'cooperative branch changed original clinical deadlines or concealed its changed hospital work calendar')
    _require(branch.get('shortage_probe_enabled') is True and branch.get('late_pending_guard_enabled') is True
        and branch.get('late_reply_recovery_enabled') is True and branch.get('cohort_registration_enabled') is True
        and branch.get('scarce_routing_enabled') is True,'cooperative branch changed its fixed safe-probe foundation')
    ordered=_indexed_events(branch)
    allowed_control_types={'cooperative_job_submitted','cooperative_job_started','cooperative_job_paused',
        'cooperative_job_resumed','cooperative_job_completed','cooperative_probe_control_ack',
        'cooperative_probe_not_admitted','cooperative_probe_ready_review','cooperative_probe_nonblocking_ready',
        'cooperative_busy_retry_review'}
    _require(all(not event['type'].startswith('cooperative_') or event['type'] in allowed_control_types for event in ordered),
             'unknown cooperative control or physical transition type')
    _require(branch.get('ordered_emissions',ordered)==ordered
        and all(joint._clock(event['t']) for event in ordered),'raw emission order or quarter-minute clock differs')
    _require(not branch.get('shortage_probe_nonblocking_records') and not any(
        event['type'] in ('shortage_probe_nonblocking_review','shortage_probe_nonblocking_ready') for event in ordered),
        'legacy nonblocking shortcut bypassed the current completion acknowledgement')
    _require(branch['metrics']['shortage_probe_nonblocking_ready']==0,'legacy optional-readiness count was fabricated')
    work=cooperative.audit_work(deepcopy(scenario),branch)
    if branch['cooperative_queue_enabled']:
        probe=cooperative.audit_probes(deepcopy(scenario),branch)
        callback=cooperative.audit_late_offers(deepcopy(scenario),branch,work)
        inquiries=parent._audit_cooperative_inquiries(deepcopy(scenario),branch,work,policy_decisions)
        backups=_audit_backups(deepcopy(scenario),branch,work)
    else:
        _require(not branch['shortage_probe_records'] and not any(
            event.get('shortage_probe_review_event_sequence') is not None for event in ordered),
            'queue-disabled environment gained an optional clinical inquiry')
        _require(not branch.get('cooperative_busy_retry_records'),'queue-disabled environment contains orphan busy retry records')
        probe=dict(shortage_probe_reviews=0,shortage_probe_sent=0)
        callback=safe.audit_late_offers(deepcopy(scenario),branch)
        inquiries=parent._audit_independent_inquiries(deepcopy(scenario),branch,policy_decisions)
        backups=_audit_backups(deepcopy(scenario),branch)
    ready=cooperative.audit_ready(deepcopy(scenario),branch)
    result=cooperative._audit_information_branch(deepcopy(scenario),branch,exact_limit=exact_limit)
    counts=dict(result['counts'])
    for values in (work['counts'],probe,callback,inquiries,backups,ready,
        scarce.audit_cohorts(deepcopy(scenario),branch),cooperative.audit_supplements(deepcopy(scenario),branch),
        joint.audit_stability(deepcopy(scenario),branch),recovery.audit_motion(deepcopy(scenario),branch),
        _audit_guard(deepcopy(scenario),branch),cooperative.audit_pending_guard(deepcopy(scenario),branch),
        cooperative.audit_early_offers(deepcopy(scenario),branch),recovery.audit_no_added_source(deepcopy(scenario),branch)):
        counts.update(values)
    for metric,key in (('cooperative_control_acks','cooperative_control_acks'),
        ('cooperative_probe_not_admitted','cooperative_admission_refusals'),
        ('cooperative_preemptions','cooperative_preemptions')):
        _require(branch['metrics'][metric]==work['counts'][key], 'cooperative metric differs from raw actual work: '+metric)
    waiting=joint.waiting_metrics(deepcopy(scenario),branch)
    for key,value in result['metrics'].items():
        if key in branch['metrics']:
            _require(base._same(branch['metrics'][key],value),'whole-cohort endpoint metric mismatch: '+key)
    return dict(passed=True,counts=counts,waiting=waiting,metrics=result['metrics'],
        information=result['information'],assignment_constraint_violations=0,
        selector_check='pure selector reproduced from original raw public inquiries and delivered reports',
        actual_optimality_claimed=False)



PUBLIC_OFFER_SPEC = dict(schema_version=1, variants=list(VARIANTS),
    original_early_policy='simulation.existing_info_engine.decide_early_offer',
    original_not_dominant_only=True, original_full_early_snapshot_retained=True,
    best_offer_rank=['travel_minutes', 'hospital'], all_actual_pending_must_not_rank_better=True,
    each_better_nonpending_structural_candidate_requires_fresh_known_shortage=True,
    shortage='at least one required resource has fresh free quantity below current demand',
    better_candidate_without_any_fresh_required_resource_shortage_blocks=True,
    other_required_resource_unknown_or_stale_does_not_erase_a_fresh_shortage=True,
    original_registered_search_shared_capacity_check=True,
    original_search_pending_valid_first_valid_wait_scope=True, new_threshold=False,
    initial_uncommitted_patient_only=True,
    actual_valid_offer_order_and_allocation_checks_retained=True,
    original_opportunity_pending_and_physical_gates_retained=True,
    actual_pending_and_other_offer_backups_retained=True,
    original_probe_ack_and_late_reply_stages_retained=True,
    inquiry_selector_and_clock_attempt_fifo_workers_resources_ttl_rules_retained=True,
    actual_downstream_paths_and_counts_may_change=True,
    extra_resource_change_publications=False, future_work_or_response_information=False)



PUBLIC_OFFER_FOUNDATION_SPEC = dict(
    actual_replays=dict(
        independent_replies='simulation.public_offer_engine.IndependentPublicOfferReplay',
        cooperative_queue='simulation.public_offer_engine.CooperativePublicOfferReplay'),
    base_replays=dict(
        independent_replies='simulation.recovery_rank_engine.IndependentNormalRecoveryRankReplay',
        cooperative_queue='simulation.recovery_rank_engine.CooperativeNormalRecoveryRankReplay'),
    joint_stage='simulation.public_offer_engine.OfferReadyJointStage',
    rank_variant='normal', actual_normal_selector_changed=False, actual_readiness_changed=True,
    original_joint_resolve_body_explicitly_retained=True,
    new_stage='after_original_early_loop_before_opportunity_and_pending_guards',
    original_probe_ack_late_and_original_early_stages_retained=True,
    new_marker_registers_original_backup_reference=True,
    actual_options_follow_original_valid_offer_order=True,
    original_inquiry_work_clock_attempt_and_physical_accounting_rules_retained=True,
    actual_downstream_paths_and_counts_may_change=True,
    inherited_protocol_descriptions_are_nested_base_contract=True,
    extra_resource_change_publications=False, added_workers_or_beds=False)


NAMESPACE_FIELDS = {'public_offer_enabled', 'public_offer_variant', 'public_offer_spec',
    'public_offer_foundation_spec', 'public_offer_base_contract', 'public_offer_records'}


def _expected(snapshot, variant):
    """Independent public-quantity/rank rule; no producer policy import."""
    original = snapshot['original_snapshot']
    shadow = cooperative._early_expected(original)
    output = dict(schema_version=1, variant=variant, eligible=False, hospital=None,
        reason='ORIGINAL_NOT_NOT_DOMINANT', original_reason=shadow['reason'],
        best_offer_hospital=None, pending_checks=[], candidate_checks=[])
    now, demand = snapshot['evaluated_t'], original['patient']['demand']
    if snapshot['patient_state'] != 'SEARCH' or snapshot['first_commit_t'] is not None \
            or not snapshot['pending_inquiries'] \
            or not original['valid_offers'] or original['first_valid_t'] is None \
            or now-original['first_valid_t'] >= snapshot['wait_minutes']:
        return dict(output, reason='OUTSIDE_ORIGINAL_READINESS_SCOPE')
    if shadow['reason'] != 'NOT_DOMINANT':
        return output
    usable = [offer for offer in original['valid_offers'] if all(
        view['status']=='fresh' and view['free'] is not None and view['free'] >= demand[resource]
        for resource,view in offer['resources'].items())]
    best = min(usable, key=lambda offer:(offer['travel_minutes'],offer['hospital']))
    offer_rank = best['travel_minutes'], best['hospital']
    output['best_offer_hospital'] = best['hospital']
    structural = {row['hospital']:row for row in original['structural_candidates']}
    pending = {row['hospital'] for row in snapshot['pending_inquiries']}
    output['pending_checks'] = [dict(hospital=row['hospital'], at_least_offer_rank=
        (structural[row['hospital']]['travel_minutes'],row['hospital']) >= offer_rank)
        for row in snapshot['pending_inquiries']]
    for row in original['structural_candidates']:
        hospital = row['hospital']
        if (row['travel_minutes'],hospital) >= offer_rank:
            continue
        views = snapshot['structural_resource_views'][hospital]
        shortages = sorted(resource for resource,view in views.items()
            if view['status']=='fresh' and view['free'] is not None and view['free'] < demand[resource])
        output['candidate_checks'].append(dict(hospital=hospital,pending=hospital in pending,
            shortage_resources=shortages,publicly_blocked=hospital not in pending and bool(shortages)))
    if any(not row['at_least_offer_rank'] for row in output['pending_checks']):
        return dict(output,reason='BETTER_ACTUAL_PENDING')
    if any(not row['publicly_blocked'] for row in output['candidate_checks']):
        return dict(output,reason='BETTER_STRUCTURAL_NOT_PUBLICLY_BLOCKED')
    contenders = [row for row in original['search_patients'] if best['hospital'] in row['compatible_hospitals']]
    if any(best['resources'][resource]['free'] < sum(row['demand'].get(resource,0) for row in contenders)
           for resource in demand):
        return dict(output,reason='SHARED_CAPACITY')
    return dict(output,eligible=True,hospital=best['hospital'],reason='PUBLICLY_BLOCKED_CLOSER_ALTERNATIVES')


def _audit_stage(scenario, branch):
    """Rebuild every new stage from original requests, handles and reports."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']:event for event in events}
    records = branch['public_offer_records']
    _require(type(records) is list, 'ordered complete public-offer records required')
    workflow = _workflow(scenario)
    enabled = scenario.get('inquiry_queue',{}).get('enabled',False)
    mode = 'cooperative_queue' if enabled else 'independent_replies'
    handles, first_commits = {}, {}
    cycles, targets, used, promotions, checked = [], [], set(), set(), set()
    original_cycles, original_last = {}, {}
    cycle_t = None
    for event in events:
        if event['type'] in ('early_offer_cycle','early_offer_review','early_offer_ready'):
            original_last[event['t']] = event['event_sequence']
    def snapshot(patient, now):
        original = cooperative._early_snapshot(workflow,patient,now)
        demand = workflow._base_patient(patient)['demand']
        return dict(schema_version=1,evaluated_t=now,mode=mode,
            patient_state=workflow.states[patient],first_commit_t=first_commits.get(patient),
            wait_minutes=branch['policy']['wait'],original_snapshot=original,
            pending_inquiries=sorted([deepcopy(row) for (pid,_),row in handles.items() if pid==patient],
                key=lambda row:(row['hospital'],row['attempt'])),
            structural_resource_views={row['hospital']:_views('observed',workflow.observations,
                workflow.truth,row['hospital'],demand,now) for row in original['structural_candidates']})
    for event in events:
        kind, now, seq = event['type'],event['t'],event['event_sequence']
        pid,hid = event.get('patient'),event.get('hospital')
        if kind=='ask':
            handles[pid,hid] = dict(hospital=hid,attempt=event['attempt'],submitted_at=now,
                timeout_at=event['timeout_at'],ask_event_sequence=seq)
        elif kind=='cooperative_probe_not_admitted':
            handles.pop((pid,hid),None)
        elif kind in ('accept','decline','no_response'):
            if not _is_callback_accept(event,indexed):
                handles.pop((pid,hid),None)
        elif kind in ('commit','arrive','transfer_search','treatment_start','backup_closed'):
            handles = {key:row for key,row in handles.items() if key[0]!=pid}
            if kind=='commit':first_commits.setdefault(pid,now)
        elif kind in ('backup_retained','backup_arrival_preserved','backup_transfer_reused'):
            handles = {key:row for key,row in handles.items() if key[0]!=pid}
            for row in event['pending']:
                handles[pid,row['hospital']]=deepcopy(row)
        elif kind=='early_offer_cycle':
            _require(now not in original_cycles,'duplicate original early cycle')
            original_cycles[now]=seq
        elif kind=='opportunity_guard':
            _require(cycle_t==now and not targets,
                'opportunity guard precedes completion of the new public-offer stage')
        elif kind=='public_offer_cycle':
            _require(not targets and set(event)=={'t','type','event_sequence'},
                'previous new cycle omitted a review or cycle exposes unknown fields')
            _require(now in original_cycles and original_last[now]<seq,
                'new stage precedes completion of the original early stage')
            cycle_t=now;cycles.append(now)
            ready = {row['id'] for row in workflow.snapshot(now)['ready']}
            targets=[]
            for patient in sorted(workflow.specs,key=lambda target:(
                    -workflow.specs[target].get('priority',4),
                    workflow.specs[target].get('search_start_t',0),target)):
                if workflow.states.get(patient)!='SEARCH' or patient in ready:
                    continue
                pending=any(other==patient for other,_ in handles)
                valid=any(other==patient and expiry>now for (other,_),expiry in workflow.offers.items())
                first=workflow.first_valid.get(patient)
                if pending and valid and first is not None and now-first<branch['policy']['wait']:
                    targets.append(patient)
        elif kind=='public_offer_review':
            _require(cycle_t==now and targets and pid==targets.pop(0),
                'unsolicited, reordered, duplicate or omitted public-offer review')
            index=event['record_index']
            _require(type(index) is int and index==len(used) and index<len(records),
                'missing/reordered public-offer record')
            used.add(index);record=records[index]
            _require(set(event)=={'t','type','event_sequence','patient','variant','record_index'}
                and event['variant']==branch['public_offer_variant']
                and set(record)=={'event_sequence','snapshot','shadow_decision','decision'}
                and record['event_sequence']==seq,'public-offer record/marker has unknown fields or wrong identity')
            current=snapshot(pid,now)
            _literal(record['snapshot'],current,'public-offer input differs from full original public raw history')
            shadow=cooperative._early_expected(current['original_snapshot'])
            _literal(record['shadow_decision'],shadow,'original full early policy shadow differs')
            expected=_expected(current,branch['public_offer_variant'])
            _literal(record['decision'],expected,'public-offer decision differs from independent public quantity/rank rule')
            if expected['eligible']:
                demand=workflow._base_patient(pid)['demand']
                options=[hospital for (patient,hospital),expiry in workflow.offers.items() if patient==pid
                    and expiry>now and information._check_reason(_views('observed',workflow.observations,
                        workflow.truth,hospital,demand,now),demand) is None]
                marker=indexed.get(seq+1)
                _literal(marker,dict(t=now,type='public_offer_ready',event_sequence=seq+1,patient=pid,
                    hospital=expected['hospital'],options=options,review_event_sequence=seq),
                    'eligible new stage lacks its unique immediate actual readiness marker')
                promotions.add(seq+1)
                for offset,hospital in enumerate(options,2):
                    check=indexed.get(seq+offset)
                    _literal(check,dict(t=now,type='information_check',event_sequence=seq+offset,
                        patient=pid,hospital=hospital,purpose='allocation',eligible=True,reason=None,
                        resources=_views('observed',workflow.observations,workflow.truth,hospital,demand,now)),
                        'new ready option lacks immediate original current allocation check')
                    checked.add(seq+offset)
            else:
                following=indexed.get(seq+1)
                _require(following is None or following['type']!='public_offer_ready',
                    'ineligible new stage promoted readiness')
        workflow.apply(event)
    _require(not targets and used==set(range(len(records))),'orphan or omitted new readiness review')
    _require(promotions=={event['event_sequence'] for event in events if event['type']=='public_offer_ready'},
        'orphan or fabricated new readiness marker')
    _require(cycles==[step/4 for step in range(int(_end(scenario,branch)*4)+1)],
        'missing, duplicate or extra public-offer cycle')
    _require(type(branch['metrics']['public_offer_cycles']) is int and branch['metrics']['public_offer_cycles']==len(cycles)
        and type(branch['metrics']['public_offer_reviews']) is int and branch['metrics']['public_offer_reviews']==len(records)
        and type(branch['metrics']['public_offer_ready']) is int and branch['metrics']['public_offer_ready']==len(promotions),
        'public-offer counts differ from actual complete raw cycles/reviews/readiness')
    return dict(public_offer_cycles=len(cycles),public_offer_reviews=len(records),
        public_offer_ready=len(promotions),public_offer_current_allocation_checks=len(checked))


def _audit_contract(branch, enabled):
    """Bind the actual wrapper and its honestly nested inherited protocols."""
    namespace = {key for key in branch if key.startswith('public_offer_')}
    _require(namespace == NAMESPACE_FIELDS and branch['public_offer_enabled'] is True,
             'missing, unknown or disabled public-offer namespace')
    _require(branch['public_offer_variant'] in VARIANTS,
             'known explicit public-offer variant required')
    _literal(branch['public_offer_spec'], PUBLIC_OFFER_SPEC, 'public-offer rule contract differs')
    mode = 'cooperative_queue' if enabled else 'independent_replies'
    _literal(branch['public_offer_foundation_spec'], dict(PUBLIC_OFFER_FOUNDATION_SPEC, mode=mode),
             'public-offer stage conceals its actual wrapper, mode or unchanged normal selector')
    inherited = dict(recovery_rank_selector='recovery_rank_normal',
        recovery_rank_spec=deepcopy(rank.RECOVERY_RANK_SPEC),
        recovery_rank_foundation_spec=dict(rank.RECOVERY_RANK_FOUNDATION_SPEC, mode=mode),
        recovery_rank_base_contract=dict(retry_dispatch_selector='frontier',
            retry_dispatch_spec=deepcopy(parent.RETRY_DISPATCH_SPEC),
            retry_dispatch_foundation_spec=dict(parent.RETRY_DISPATCH_FOUNDATION_SPEC, mode=mode)),
        shortage_probe_spec=deepcopy(cooperative.COOPERATIVE_SHORTAGE_SPEC if enabled
                                    else independent.SHORTAGE_PROBE_SPEC),
        comparison_id='cooperative_probe_20261017' if enabled else 'shortage_probe_20261017')
    if enabled:
        inherited['cooperative_probe_spec'] = deepcopy(cooperative.COOPERATIVE_PROBE_SPEC)
    _literal(branch['public_offer_base_contract'], inherited,
             'public-offer stage changed or concealed its complete inherited base contract')
    popped_rank = {'recovery_rank_selector', 'recovery_rank_spec',
        'recovery_rank_foundation_spec', 'recovery_rank_base_contract'}
    _require({key for key in branch if key.startswith('recovery_rank_')}
             == rank.NAMESPACE_FIELDS - popped_rank
             and branch['recovery_rank_enabled'] is True and branch['recovery_rank_variant'] == 'normal',
             'public-offer stage must retain actual normal-rank facts and nest inherited protocol descriptions')
    _require(not any(key in branch for key in ('shortage_probe_spec', 'cooperative_probe_spec'))
             and branch.get('comparison_id') == EXPERIMENT_ID,
             'actual public-offer stage falsely claims an unchanged probe protocol identity')
    _require({key for key in branch if key.startswith('retry_dispatch_')} == {
        'retry_dispatch_enabled', 'retry_dispatch_records', 'retry_dispatch_contacts', 'retry_dispatch_wakes'}
        and branch['retry_dispatch_enabled'] is True,
        'public-offer stage drops factual dispatch records or falsely claims unchanged selector metadata')
    _require(not any(key.startswith(('capability_', 'recovery_dispatch_', 'probe_guard_')) for key in branch),
             'public-offer stage invented a reopening or exact capability delegation')
    _require(branch.get('inquiry_selector') == 'v4' and branch.get('policy', {}).get('rule') in ('H1', 'C1')
             and branch['policy'].get('name') == branch.get('id')
             and type(branch.get('id')) is str and branch['id'],
             'public-offer execution identity, selector slot or allocation rule changed')
    _require(all(branch.get(flag) is True for flag in ('shortage_probe_enabled', 'late_pending_guard_enabled',
        'late_reply_recovery_enabled', 'cohort_registration_enabled', 'scarce_routing_enabled')),
        'public-offer stage changed a fixed complete foundation flag')
    _literal(branch.get('scarce_delay_spec'), dict(inquiry='v4', allocation=branch['policy']['rule'],
        cohort_registration=True, scarce_routing=True, actual_action='parallel_scarce_supplement',
        original_selector_candidates_unchanged=False, maximum_supplements_per_patient=1,
        recovery=True, pending_protection=True, pending_window_minutes=1, time_step_seconds=15,
        actual_selector_reused=False, original_seven_inputs_retained=True, base_selector_reused_as_kernel=True,
        extra_resource_change_publications=False), 'public-offer stage changed its unchanged scarce foundation')
    if enabled:
        _require(branch.get('cooperative_probe_enabled') is True and branch.get('cooperative_queue_enabled') is True,
                 'public-offer stage omitted the actual complete cooperative workflow')
    else:
        _require(not any(key.startswith('cooperative_') for key in branch)
                 and branch.get('inquiry_queue_report') is None, 'independent public-offer stage invented hospital workers')
    events = _indexed_events(branch)
    _require(not any(event['type'].startswith('recovery_') for event in events),
             'public-offer stage gained a recovery reopening or extra call')
    _require(all(not event['type'].startswith('public_offer_') or event['type'] in ('public_offer_cycle','public_offer_review','public_offer_ready')
                 for event in events), 'unknown public-offer raw intervention event')



def audit_branch(scenario, branch, *, exact_limit=20000):
    """Audit a whole frozen reference or the exact new readiness branch."""
    namespace = {key for key in branch if key.startswith('public_offer_')}
    if not namespace:
        _require(not str(branch.get('id', '')).startswith('public_offer_')
                 and branch.get('comparison_id') != EXPERIMENT_ID
                 and not any(event['type'].startswith('public_offer_') for event in _indexed_events(branch))
                 and not any(key.startswith('public_offer_') for key in branch.get('metrics', {})),
                 'public-offer candidate omitted its actual intervention namespace')
        return rank.audit_branch(scenario, branch, exact_limit=exact_limit)
    queue = scenario.get('inquiry_queue', {})
    _require(type(queue) is dict and type(queue.get('enabled', False)) is bool,
             'original queue support must remain an explicit boolean')
    enabled = queue.get('enabled', False)
    _audit_contract(branch, enabled)
    _require(branch.get('early_offers_enabled') is True
             and type(branch['policy'].get('wait')) is int and branch['policy']['wait']==2
             and type(branch['policy'].get('k')) is int and branch['policy']['k']==3,
             'public-offer stage changed the fixed original early, wait or concurrency foundation')
    decisions, counts = rank._audit_contexts(scenario, branch)
    counts.update(rank._audit_normal_episodes(scenario, branch))
    counts.update(_audit_stage(scenario, branch))
    # Each component below receives this same unfiltered full branch. The added public readiness
    # proof and its ready-set/backup provenance are explicit new components.
    checked = (_audit_cooperative_branch if enabled else _audit_independent_branch)(
        scenario, branch, decisions, exact_limit=exact_limit)
    checked['counts'].update(counts)
    checked['selector_check'] = ('unchanged normal recovery-rank selector on reconstructed original seven inputs; '
        'independent public offer readiness, original early shadow and every actual due review; '
        'same whole raw branch reaches frozen clinical, FIFO, physical and allocation auditors')
    return checked


def audit_public_offer_replay(result, *, exact_limit=20000):
    _require(type(result.get('schema_version')) is int and result['schema_version'] == 1
             and result.get('experiment_id') == EXPERIMENT_ID, 'unknown public-offer replay schema')
    ids = [branch['id'] for branch in result['branches']]
    _require(ids and all(type(code) is str and code for code in ids) and len(ids) == len(set(ids))
             and len(result['execution_order']) == len(ids) and set(result['execution_order']) == set(ids),
             'missing, duplicated or reordered public-offer replay identity')
    counts, details = Counter(), {}
    for branch in result['branches']:
        checked = audit_branch(result['scenario'], branch, exact_limit=exact_limit)
        counts.update(checked['counts']); details[branch['id']] = checked
    return dict(passed=True, counts=dict(counts), branches=len(ids), branch_audits=details,
        assignment_constraint_violations=0, actual_optimality_claimed=False)

