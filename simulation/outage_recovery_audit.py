"""Independent raw audit of alert-driven transport pause and retained inquiry work.

Frozen pure validators are copied locally only to support the explicit pause and
continuation contract. No replay engine, solver, observation store or scenario
builder is imported or executed by this verifier.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import hashlib,json,math,statistics
from pathlib import Path
from . import online_allocation_audit as base
from . import partial_information_audit as information
from . import responsive_confirmation_audit as responsive
from . import stable_c1_audit as stable
from . import total_wait_audit as total
from . import existing_info_audit as existing
from .h1_online_audit import _diagnostics,_finite,_paired_tails
from .h1_online_audit import audit_h1_branch
ROOT=Path(__file__).resolve().parents[1]
EXPERIMENT_ID='outage_recovery_20261015'
OutageRecoveryAuditError=base.OnlineAllocationAuditError
POLICY_IDS=existing.POLICY_IDS+['retained_backup_C1','outage_recovery_C1']
DEVELOPMENT_SEEDS=[29101,29102,29103]
VALIDATION_SEEDS=[30101,30102,30103,30104,30105,30106]
FAMILIES=existing.FAMILIES+('notify_before_backup_reply','notify_after_backup_reply',
    'expired_backup_after_notify','silent_backup_timeout','late_backup_timeout',
    'competing_backup_capacity','restore_before_failed_arrival',
    'unverified_backup_resources','outage_at_arrival_boundary')
COMPARISON_PAIRS=existing.COMPARISON_PAIRS+(
    ('retained_vs_existing','existing_info_C1','retained_backup_C1'),
    ('recovery_vs_existing','existing_info_C1','outage_recovery_C1'),
    ('recovery_vs_review','review_stable_C1','outage_recovery_C1'),
    ('recovery_vs_retained','retained_backup_C1','outage_recovery_C1'))
_TruthLedger=information._TruthLedger
_number=information._number
_quantity=information._quantity
_indexed_events=information._indexed_events
_views=information._views
_check_reason=information._check_reason
_exogenous_contract=information._exogenous_contract
_initial_reports=information._initial_reports
_model_evidence=information._model_evidence
_objective_dict=information._objective_dict
information_metrics=information.information_metrics
_apply_observation=information._apply_observation
responsiveness_metrics=responsive.responsiveness_metrics

def _require(condition,message):base._require(condition,message)


def _pending_rows(asks, patient, at, exclude=()):
    return sorted((deepcopy(row) for (pid, hid), row in asks.items()
                   if pid == patient and hid not in exclude and row['timeout_at'] >= at),
                  key=lambda row: row['hospital'])


def _offer_rows(offers, patient, at, exclude=()):
    return sorted((deepcopy(row) for (pid, hid), row in offers.items()
                   if pid == patient and hid not in exclude and row['expires_at'] > at),
                  key=lambda row: row['hospital'])


def _replace_known_backup(workflow, event):
    """Apply an independently verified continuation marker to a pure ledger."""
    pid = event['patient']
    workflow.asks = Counter({key: value for key, value in workflow.asks.items() if key[0] != pid})
    workflow.offers = {key: value for key, value in workflow.offers.items() if key[0] != pid}
    for row in event['pending']:
        workflow.asks[pid, row['hospital']] += 1
    for row in event['offers']:
        workflow.offers[pid, row['hospital']] = row['expires_at']
        workflow.accepted[pid, row['hospital']] = row['accepted_at']
    if event['offers']:
        workflow.first_valid[pid] = min(row['accepted_at'] for row in event['offers'])
    else:
        workflow.first_valid.pop(pid, None)


def _reply_due(scenario, ordered, handle):
    """Audit-only environment reconstruction, never a policy input."""
    pid = next(event['patient'] for event in ordered
               if event['event_sequence'] == handle['ask_event_sequence'])
    hid, attempt = handle['hospital'], handle['attempt']
    env = scenario.get('response_environment', {})
    entry = env.get(pid, {}).get(hid, env.get('*', {}).get(hid, {}))
    entries = entry.get('attempts')
    if entries:
        entry = entries[min(attempt - 1, len(entries) - 1)]
    if entry.get('silent', False):
        return handle['timeout_at'], True
    if scenario.get('inquiry_queue', {}).get('enabled'):
        completion = next((event['t'] for event in ordered if event['type'] == 'review_completed'
            and event.get('kind') == 'inquiry' and event.get('patient') == pid
            and event.get('hospital') == hid and event.get('attempt') == attempt), None)
        if completion is None or completion > handle['timeout_at']:
            return handle['timeout_at'], True
        return completion, False
    if entry:
        due = handle['submitted_at'] + max(1, int(entry.get('delay', 2)))
        return min(due, handle['timeout_at']), due > handle['timeout_at']
    # Frozen keyed stochastic cases declare no future reply calendar. Their
    # retained handle is still bounded by its public absolute timeout.
    return None, None


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
            due, silent = _reply_due(scenario, ordered, handle)
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
            _require((pid, hid) in asks, 'ordinary reply lacks original active inquiry')
            handle = asks.pop((pid, hid))
            if pid in episodes:
                if states.get(pid)=='TRANSFER_WAIT' or states.get(pid)=='TRANSIT' and pid not in notifications:
                    previous=indexed.get(seq-1)
                    _require(previous is not None and previous['type']=='backup_reply_received',
                             'receive-only original reply omitted backup receipt marker')
                due, _ = _reply_due(scenario, ordered, handle)
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


def audit_motion(scenario, branch):
    """Reconstruct the unchanged synthetic movement and transfer preparation.

    A retained inquiry is not permission to teleport, shorten a route, skip
    failed-arrival preparation, or turn a resource event into a motion oracle.
    """
    patients = {p['id']: p for p in scenario['patients']}
    hospitals = {h['id']: h for h in scenario['hospitals']}
    trajectories = branch['trajectories']
    _require(isinstance(trajectories, list) and len(trajectories) == len(patients)
             and {row['patient'] for row in trajectories} == set(patients),
             'trajectory patients differ from declared patients')
    ordered = information._indexed_events(branch)
    _require(not any(event['type'] in ('transport_pause', 'notification_pause') for event in ordered),
             'inquiry retention changed movement by pausing transport')
    failures, transfer_searches, movements = {}, 0, 0
    for event in ordered:
        pid, kind = event.get('patient'), event['type']
        if pid not in patients:
            continue
        if kind == 'arrival_unable':
            expected = event['t'] + scenario.get('transfer_preparation_minutes', 3)
            _require(event['transfer_search_at'] == expected,
                     'failed arrival shortened declared transfer preparation')
            failures[pid] = event
        elif kind == 'transfer_search':
            prior = failures.get(pid)
            _require(prior is not None and event['t'] == prior['transfer_search_at']
                     and event['hospital'] == prior['hospital'],
                     'transfer search skipped unchanged preparation or failed arrival')
            transfer_searches += 1
    for trajectory in trajectories:
        pid, legs = trajectory['patient'], trajectory['legs']
        spec = patients[pid]
        _require(isinstance(legs, list) and legs and legs[0]['kind'] == 'dispatch',
                 'trajectory lacks declared common dispatch leg')
        dispatch = legs[0]
        _require(dispatch['depart_t'] == spec.get('dispatch_t', spec.get('occur_t', 0))
                 and dispatch['arrive_t'] == spec['ambulance_arrive_t']
                 and base._same(dispatch['origin'], spec['ambulance_origin'])
                 and base._same(dispatch['destination'], spec['location']),
                 'common dispatch timing or location changed')
        commits = [event for event in ordered if event['type'] == 'commit' and event.get('patient') == pid]
        _require(len(legs) - 1 == len(commits), 'trajectory movement legs differ from committed routes')
        origin = spec['location']
        for index, (leg, commit) in enumerate(zip(legs[1:], commits)):
            hid, at = commit['hospital'], commit['t']
            destination = [hospitals[hid]['lat'], hospitals[hid]['lon']]
            _require(leg['kind'] in ('transport', 'transfer') and leg['provider'] == 'synthetic'
                     and leg['geometry_kind'] == 'synthetic_straight_line'
                     and leg['depart_t'] == at and leg['hospital'] == hid
                     and base._same(leg['origin'], origin),
                     'committed synthetic route changed origin, provider or departure')
            estimate = _synthetic_minutes(origin, hospitals[hid])
            _require(leg['estimated_minutes'] == estimate and commit['estimated_eta'] == at + estimate
                     and leg['planned_duration_seconds'] == estimate * 60,
                     'route estimate differs from independent unchanged distance model')
            factor = leg['realized_travel_factor']
            _finite(factor, 'realized travel factor')
            bounds = scenario.get('travel_error_range', [1.0, 1.0])
            _require(bounds[0] <= factor <= bounds[1], 'realized travel factor outside unchanged environment')
            planned = at + max(1, math.ceil(estimate * factor))
            _require(commit['eta'] == leg['planned_arrive_t'] == planned,
                     'actual route duration shortened after selection')
            ended = leg['ended_by']
            if ended == 'redirect':
                _require(index + 1 < len(commits), 'truncated redirect lacks following commitment')
                next_at = commits[index + 1]['t']
                ratio = (next_at - at) / max(1, planned - at)
                _require(0 <= ratio <= 1 and leg['arrive_t'] == next_at,
                         'redirect truncation outside original journey')
                position = [origin[k] + (destination[k] - origin[k]) * ratio for k in (0, 1)]
                _require(base._same(leg['destination'], position),
                         'redirect changed current physical position')
                origin = position
            else:
                _require(ended in ('arrival', None) and leg['arrive_t'] == planned
                         and base._same(leg['destination'], destination),
                         'retention paused or shortened an ordinary route')
                if ended == 'arrival':
                    _require(any(event['type'] == 'arrive' and event.get('patient') == pid
                                 and event['hospital'] == hid and event['t'] == planned for event in ordered),
                             'completed route lacks corresponding physical arrival')
                origin = destination
            _require(base._same(leg['motion_geometry'][0], leg['origin'])
                     and base._same(leg['motion_geometry'][-1], leg['destination']),
                     'movement geometry endpoints differ from physical route')
            if leg['kind'] == 'transfer':
                _require(commit.get('transfer') is True and any(event['type'] == 'transfer_depart'
                    and event.get('patient') == pid and event['hospital'] == hid and event['t'] == at
                    for event in ordered), 'transfer motion lacks ordinary transfer departure')
            movements += 1
    return dict(movement_legs=movements, unchanged_transfer_preparations=transfer_searches)


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
        if kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            _replace_known_backup(self, event)
        elif kind == 'backup_closed':
            self.offers = {key:value for key,value in self.offers.items() if key[0] != pid}
            self.asks = Counter({key:value for key,value in self.asks.items() if key[0] != pid})
            self.first_valid.pop(pid, None)
        elif kind == 'resource_report_received':
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

class _EarlyWorkflow(_KnownWorkflow):
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
        elif kind == 'backup_failed_reopened' and pid in self.failed:
            self.failed[pid].discard(hid)
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

def _stream(scenario, branch):
    """Check observations and every actual gate in emission order."""
    mode = branch['information_mode']
    _require(mode in ('perfect', 'observed'), 'invalid information mode')
    ordered = _indexed_events(branch)
    _exogenous_contract(scenario, ordered)
    by_sequence = {event['event_sequence']: event for event in ordered}
    truth, observations = _TruthLedger(scenario), {}
    patients = {p['id']: p for p in scenario['patients']}
    snapshots, gates, offers, accepts, reports = {}, {}, {}, {}, []
    last_checks, last_waits, pending_requests, request_ids = {}, {}, {}, set()
    initial = _initial_reports(scenario) if mode == 'observed' else []
    initial_index = 0
    config = scenario.get('observation_config', {})
    captured, delivered_feeds, created_packets, cooldown, used_waits = {}, set(), set(), {}, set()
    failed_checks = set()
    for event in ordered:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'resource_report_captured':
            _require(mode == 'observed' and hid in truth.capacities, 'capture on perfect mode or unknown hospital')
            packet = base._integer(event['packet_sequence'], 'invalid captured packet sequence')
            _require(packet == len(created_packets), 'packet creation sequence is duplicated or incomplete')
            created_packets.add(packet)
            interval = config.get('feed_interval_minutes', 1)
            delay = config.get('feed_delay_by_source_minute', {}).get(str(at), config.get('feed_delay_minutes', 0))
            _require(interval > 0 and at % interval == 0 and event['observed_at'] == at
                     and event['receive_at'] == at + delay, 'capture differs from configured feed schedule')
            physical = truth.resources(hid, truth.capacities[hid], at)
            missing = config.get('feed_missing_resources', ())
            payload = {resource: None if resource in missing else
                dict(capacity=values['capacity'], used=values['used']) for resource, values in physical.items()}
            _require(base._same(event['resources'], payload), 'captured feed payload differs from physical source ledger')
            captured[packet] = event
        elif kind == 'resource_report_received':
            _require(mode == 'observed' and hid in truth.capacities,
                     'report on perfect mode or unknown hospital')
            _require(event['received_at'] == at, 'report receipt differs from event time')
            packet = base._integer(event['packet_sequence'], 'invalid received packet sequence')
            if event['source'] != 'feed' or 'publication_id' in event:
                _require(packet == len(created_packets), 'packet creation sequence is duplicated or incomplete')
                created_packets.add(packet)
            if event['source'] == 'initial':
                _require(at == 0 and initial_index < len(initial), 'unexpected initial resource report')
                configured = initial[initial_index]
                _require(event['hospital'] == configured['hospital']
                         and event['observed_at'] == configured['observed_at']
                         and base._same(event['resources'], configured['resources']),
                         'initial resource report differs from configured raw packet')
                initial_index += 1
            elif event['source'] == 'feed' and 'publication_id' not in event:
                interval = config.get('feed_interval_minutes', 1)
                source_at = event['observed_at']
                _require(type(source_at) is int and source_at >= 0 and interval > 0
                         and source_at % interval == 0, 'feed source time differs from configured interval')
                delay = config.get('feed_delay_by_source_minute', {}).get(str(source_at),
                    config.get('feed_delay_minutes', 0))
                _require(at == source_at + delay, 'feed receipt differs from configured network delay')
                source = captured.get(packet)
                _require(packet not in delivered_feeds and source is not None
                         and source['event_sequence'] < seq and source['hospital'] == hid
                         and source['observed_at'] == source_at and source['receive_at'] == at
                         and base._same(source['resources'], event['resources']),
                         'received feed differs from earlier captured raw source packet')
                delivered_feeds.add(packet)
            _apply_observation(observations, event)
            reports.append(event)
            if event['source'] == 'confirmation':
                _require(hid in pending_requests, 'resource reply without explicit confirmation request')
                request = pending_requests[hid]
                _require(not config.get('confirmation_silent', False) and event['observed_at'] == at
                         and at == request['t'] + config.get('confirmation_delay_minutes', 1),
                         'resource confirmation reply differs from configured source time or delay')
                physical = truth.resources(hid, truth.capacities[hid], at)
                missing = config.get('confirmation_missing_resources', ())
                payload = {resource: None if resource in missing else
                           dict(capacity=values['capacity'], used=values['used'])
                           for resource, values in physical.items()}
                _require(base._same(event['resources'], payload),
                         'resource confirmation payload differs from current physical source')
                pending_requests.pop(hid)
                cooldown[hid] = at + scenario.get('retry_minutes', 3)
        elif kind == 'information_check':
            _require(pid in patients and hid in truth.capacities, 'invalid information-check target')
            purpose = event['purpose']
            _require(purpose in ('inquiry', 'allocation'), 'invalid information-check purpose')
            demand = patients[pid].get('resource_demands', {'ed_bed': 1})
            expected = _views(mode, observations, truth, hid, demand, at)
            _require(base._same(event['resources'], expected),
                     'information check differs from delivered reports or current reservations')
            reason = _check_reason(expected, demand)
            eligible = reason != 'FRESH_SHORTAGE' if purpose == 'inquiry' else reason is None
            _require(type(event['eligible']) is bool and event['eligible'] == eligible
                     and event['reason'] == reason, 'incorrect information eligibility or reason')
            last_checks[pid, hid, purpose] = event
            if purpose == 'allocation' and not eligible:
                failed_checks.add(seq)
        elif kind == 'information_wait':
            _require(pid in patients and hid in truth.capacities, 'invalid information wait target')
            check = last_checks.get((pid, hid, 'allocation'))
            gate = next((g for g in reversed(list(gates.values()))
                         if g['patient'] == pid and g['hospital'] == hid and g['t'] == at), None)
            if check is not None and check['t'] == at and not check['eligible'] and event['reason'] == check['reason']:
                source_sequence = check['event_sequence']
            else:
                _require(gate is not None and not gate['passed'] and event['reason'] == gate['reason'],
                         'information wait lacks a preceding failed check or gate')
                source_sequence = gate['event_sequence']
            _require(source_sequence not in used_waits, 'failed check/gate produced duplicate information waits')
            used_waits.add(source_sequence)
            if mode == 'observed' and hid not in pending_requests and cooldown.get(hid, 0) <= at:
                following = by_sequence.get(seq + 1)
                _require(following is not None and following['type'] == 'resource_confirmation_request'
                         and following.get('patient') == pid and following.get('hospital') == hid,
                         'unverified/failed allocation lacks required explicit resource confirmation request')
            last_waits[pid, hid] = event
        elif kind == 'candidate_deferred':
            check = last_checks.get((pid, hid, 'inquiry'))
            _require(check is not None and check['t'] == at and not check['eligible']
                     and check['reason'] == event['reason'], 'candidate deferral lacks a shortage check')
        elif kind == 'resource_confirmation_request':
            _require(mode == 'observed' and (pid, hid) in last_waits
                     and last_waits[pid, hid]['t'] == at, 'resource request lacks information wait')
            request = base._integer(event['request_id'], 'invalid resource request ID', minimum=1)
            _require(request not in request_ids and hid not in pending_requests,
                     'duplicate resource request or overlapping hospital request')
            _require(cooldown.get(hid, 0) <= at, 'resource request precedes configured retry cooldown')
            request_ids.add(request)
            pending_requests[hid] = event
        elif kind == 'resource_confirmation_timeout':
            request = pending_requests.get(hid)
            _require(request is not None and request['request_id'] == event['request_id']
                     and config.get('confirmation_silent', False)
                     and at == request['t'] + config.get('confirmation_timeout_minutes', 6),
                     'confirmation timeout lacks matching request or configured deadline')
            pending_requests.pop(hid)
            cooldown[hid] = at + scenario.get('retry_minutes', 3)
        elif kind == 'accept':
            _require(pid in patients and hid in truth.capacities, 'unknown accepted-offer target')
            accepts[pid, hid] = event
            offers[pid, hid] = at + scenario.get('accept_ttl_minutes', 10)
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            offers = {key:expiry for key,expiry in offers.items() if key[0] != pid}
            for row in event['offers']:
                source = by_sequence.get(row['accept_event_sequence'])
                _require(source is not None and source['type'] == 'accept'
                         and source['patient'] == pid and source['hospital'] == row['hospital']
                         and source['t'] == row['accepted_at'] < row['expires_at']
                         and source['event_sequence'] < seq,
                         'backup reuse lacks an earlier actual patient acceptance')
                accepts[pid,row['hospital']] = source
                offers[pid,row['hospital']] = row['expires_at']
        elif kind == 'backup_closed':
            offers = {key:expiry for key,expiry in offers.items() if key[0] != pid}
        elif kind == 'expire':
            offers.pop((pid, hid), None)
        elif kind == 'allocation_proposal':
            snapshots[seq] = dict(observations=deepcopy(observations), truth=deepcopy(truth),
                                  accepts=deepcopy(accepts), offers=deepcopy(offers),
                                  checks=deepcopy(last_checks))
        elif kind == 'confirmation_gate':
            _require(pid in patients and hid in truth.capacities, 'invalid confirmation target')
            demand = patients[pid].get('resource_demands', {'ed_bed': 1})
            expected = truth.resources(hid, demand, at)
            _require(base._same(event['resources'], expected),
                     'confirmation gate truth ledger differs from raw resource/reservation events')
            accepted = accepts.get((pid, hid))
            expiry = offers.get((pid, hid))
            _require(event.get('accepted_at') == (accepted['t'] if accepted else None)
                     and event.get('offer_expires_at') == expiry,
                     'confirmation gate offer evidence differs from earlier received acceptance')
            reason = 'OFFER_EXPIRED' if expiry is None or expiry <= at else \
                'RESOURCE_CONFLICT' if any(values['free'] < demand[resource]
                    for resource, values in expected.items()) or not truth.patients[pid]['capabilities'] <= truth.capabilities[hid] else None
            _require(type(event['passed']) is bool and event['passed'] == (reason is None)
                     and event['reason'] == reason, 'incorrect physical confirmation gate result')
            gates[seq] = event
            if reason is not None:
                offers.pop((pid, hid), None)
        elif kind == 'confirmation_rejected':
            previous = gates.get(seq - 1)
            _require(previous is not None and not previous['passed'] and previous['patient'] == pid
                     and previous['hospital'] == hid and previous['reason'] == event['reason'],
                     'confirmation rejection lacks matching failed gate')
        truth.apply(event)
        if kind == 'commit':
            offers = {key: expiry for key, expiry in offers.items() if key[0] != pid}
    _require(base._same(branch['observation_records'], reports),
             'observation records differ from raw received packet stream')
    _require(initial_index == len(initial), 'missing configured initial resource report')
    _require(failed_checks <= used_waits
             and {seq for seq, gate in gates.items() if not gate['passed']} <= used_waits,
             'failed information checks/gates lack explicit waiting events')
    interval = config.get('feed_interval_minutes', 1)
    expected_captures = Counter((at, hid) for at in range(scenario.get('max_minutes', 180) + 1)
        if interval and at % interval == 0 for hid in truth.capacities) if mode == 'observed' else Counter()
    _require(Counter((event['t'], event['hospital']) for event in captured.values()) == expected_captures,
             'missing/extra configured feed capture')
    expected_deliveries = {packet for packet, event in captured.items()
                           if event['receive_at'] <= scenario.get('max_minutes', 180)}
    _require(delivered_feeds == expected_deliveries, 'missing/extra configured feed delivery')
    for hid, request in pending_requests.items():
        delay = config.get('confirmation_timeout_minutes', 6) if config.get('confirmation_silent', False) else \
            config.get('confirmation_delay_minutes', 1)
        _require(request['t'] + delay > scenario.get('max_minutes', 180),
                 'missing due resource confirmation response/timeout')
    return dict(events=by_sequence, snapshots=snapshots, gates=gates)

def _audit_information_branch(scenario, branch, *, exact_limit=20000):
    raw_metrics = base.raw_branch_metrics(scenario, branch)
    _require(branch['metrics'].get('whole_episode_optimal_proven') is False,
             'model proof incorrectly applied to whole episode')
    stream = _stream(scenario, branch)
    decisions = branch['allocation_decisions']
    code = branch['policy']['rule']
    _require(code in ('R1', 'H1', 'C1'), 'unknown allocation rule')
    counts = Counter(decisions=0, proposal_patients=0, physical_gate_passes=0,
                     physical_gate_rejections=0, cp_sat_model_proofs=0, exact_model_rechecks=0)
    solver_records = branch['solver_records']
    used_solvers, used_gates, used_markers, covered_commits = set(), set(), set(), set()
    shadow = deepcopy(branch)
    for index, decision in enumerate(decisions):
        seq = base._integer(decision['event_sequence'], 'invalid decision emission sequence')
        marker = stream['events'].get(seq)
        _require(marker is not None and marker['type'] == 'allocation_proposal'
                 and marker.get('decision_index') == index and marker.get('rule') == code
                 and marker['t'] == decision['t'] and seq not in used_markers,
                 'decision snapshot lacks unique matching proposal event')
        used_markers.add(seq)
        _model_evidence(scenario, branch, decision, stream['snapshots'][seq])
        patients, free, options, travel = base._problem(scenario, decision)
        proposed, actual = decision['proposed_assignments'], decision['assignments']
        _require(base._plan_feasible(patients, free, options, proposed),
                 'model proposal is outside verified offers or reported resource constraints')
        proposed_objective = base._objective(patients, proposed, travel)
        _require(decision['proposed_objective'] == _objective_dict(proposed_objective),
                 'proposal objective differs from raw model plan')
        _require(set(actual) == set(proposed) and all(actual[pid] in (None, hid)
                 for pid, hid in proposed.items()), 'actual confirmation is not a subset of proposal')
        _require(decision['objective'] == _objective_dict(base._objective(patients, actual, travel)),
                 'actual objective differs from confirmed plan')
        _require(decision['actual_optimal_proven'] is False and not decision.get('optimal_proven', False),
                 'model optimum incorrectly claimed for actual confirmation')
        _number(decision['elapsed_seconds'], 'allocation wall time')
        _require(decision['rule'] == code and decision['kind'] in ('single', 'batch'),
                 'decision rule or kind mismatch')
        expected_scope = 'current_patient_accepted_offers' if decision['kind'] == 'single' else \
            'current_same_minute_accepted_offers'
        _require(decision['scope'] == expected_scope, 'incorrect decision scope')
        if decision['kind'] == 'single':
            _require(len(patients) == 1 and decision['model_optimal_proven'] is False,
                     'single decision claims a batch optimum')
            patient = patients[0]
            eligible = options[patient['id']]
            expected = min(eligible, key=lambda hid: (travel[patient['id'], hid], hid)) if eligible else None
            _require(proposed[patient['id']] == expected and decision['method'] == 'nearest_current_offer',
                     'single decision differs from nearest current verified offer')
        planned = {pid: hid for pid, hid in proposed.items() if hid is not None}
        gates = decision['gate_records']
        _require(len(gates) == len(planned) and {gate['patient'] for gate in gates} == set(planned),
                 'proposal lacks exactly one physical gate per proposed patient')
        for gate in gates:
            gs = base._integer(gate['event_sequence'], 'invalid gate event sequence')
            raw = stream['gates'].get(gs)
            _require(gs > seq and gs not in used_gates and raw is not None
                     and base._same({k: v for k, v in gate.items() if k != 'commit_event_sequence'}, raw),
                     'gate record is reused or differs from raw confirmation event')
            used_gates.add(gs)
            pid, hid = gate['patient'], gate['hospital']
            _require(gate['t'] == decision['t'] and planned[pid] == hid,
                     'physical gate differs from proposed patient/hospital')
            commit_sequence = gate['commit_event_sequence']
            if gate['passed']:
                cs = base._integer(commit_sequence, 'invalid actual commit sequence')
                commit = stream['events'].get(cs)
                _require(cs > gs and cs not in covered_commits and commit is not None
                         and commit['type'] == 'commit' and commit['patient'] == pid
                         and commit['hospital'] == hid and commit['t'] == gate['t']
                         and actual[pid] == hid, 'passed gate lacks unique matching actual commitment')
                _require(not any(e['type'] in ('confirmation_gate', 'allocation_proposal')
                         for es, e in stream['events'].items() if gs < es < cs),
                         'another proposal/gate interleaves a passed gate and reservation')
                covered_commits.add(cs)
                counts['physical_gate_passes'] += 1
            else:
                _require(commit_sequence is None and actual[pid] is None,
                         'failed physical gate became an actual commitment')
                counts['physical_gate_rejections'] += 1
        shadow_decision = shadow['allocation_decisions'][index]
        shadow_decision.update(assignments=deepcopy(proposed), objective=decision['proposed_objective'],
                               optimal_proven=decision['model_optimal_proven'])
        if code == 'C1' and decision['kind'] == 'batch':
            ri = base._integer(decision['solver_record_index'], 'invalid model solver record index')
            _require(ri < len(solver_records) and ri not in used_solvers, 'missing/reused model solver record')
            used_solvers.add(ri)
            record = solver_records[ri]
            _require(record['decision_index'] == index, 'solver record points to another proposal')
            proof = base._solver_proof(record, shadow_decision, patients, options, proposed_objective)
            _require(decision['model_optimal_proven'] is bool(record['optimal_proven']),
                     'decision model proof differs from solver record')
            _require(decision['method'] == ('cp_sat_fallback' if record.get('fallback') else 'cp_sat'),
                     'incorrect CP-SAT method/fallback label')
            if proof != 'unproven':
                counts['cp_sat_model_proofs'] += 1
                if math.prod(len(options[p['id']]) + 1 for p in patients) <= exact_limit:
                    _require(proposed_objective == base._exact(patients, free, options, travel),
                             'claimed model optimum differs from independent enumeration')
                    counts['exact_model_rechecks'] += 1
        else:
            _require(decision['model_optimal_proven'] is False,
                     'non-CP-SAT decision claims model optimality')
        counts.update(decisions=1, proposal_patients=len(planned))
    _require(used_solvers == set(range(len(solver_records))), 'orphan model solver record')
    _require(used_gates == set(stream['gates']), 'orphan physical confirmation gate')
    _require(used_markers == {seq for seq, e in stream['events'].items() if e['type'] == 'allocation_proposal'},
             'orphan allocation proposal marker')
    _require(covered_commits == {seq for seq, e in stream['events'].items() if e['type'] == 'commit'},
             'actual commitments are not covered exactly once by passed gates')
    if code == 'H1':
        spec = dict(time_limit_seconds=.05, max_moves=2048)
        counts.update(audit_h1_branch(scenario, shadow, spec))
    else:
        _require(not branch['heuristic_records'], 'heuristic record on non-H1 branch')
    _require(branch['metrics']['allocation_decisions'] == len(decisions)
             and base._same(branch['metrics']['allocation_wall_seconds'],
                           sum(d['elapsed_seconds'] for d in decisions)), 'allocation runtime metrics mismatch')
    _require(branch['metrics']['solver_calls'] == len(solver_records)
             and branch['metrics']['heuristic_calls'] == len(branch['heuristic_records']),
             'allocation call counts mismatch')
    information = information_metrics(branch)
    for key in ('information_wait_checks', 'resource_confirmation_requests',
                'confirmation_rejections', 'ignored_resource_reports'):
        _require(branch['metrics'][key] == information[key], 'information metric mismatch: ' + key)
    return dict(counts=dict(counts), metrics=raw_metrics, information=information)

def waiting_metrics(scenario, branch):
    """Integrate all waiting patients over the fixed observation window.

    SEARCH, hospital QUEUED and TRANSFER_WAIT contribute; transit and treatment
    do not. Missing completions remain in the denominator and are censored only
    at the declared observation end. This is an evaluation, never policy input.
    """
    outcomes, _, _ = base._event_rows(scenario, branch)
    end = base._end(scenario, branch)
    specs = {p['id']: p for p in scenario['patients']}
    state = {}
    last = {}
    waits = {pid: Counter(search=0, queue=0, transfer=0) for pid in specs}
    for event in information._indexed_events(branch):
        pid = event.get('patient')
        if pid not in specs:
            continue
        kind, at = event['type'], event['t']
        if pid in state:
            elapsed = at - last[pid]
            _require(elapsed >= 0, 'waiting state time reverses')
            if state[pid] in ('search', 'queue', 'transfer'):
                waits[pid][state[pid]] += elapsed
            last[pid] = at
        if kind == 'request':
            _require(pid not in state, 'duplicate initial waiting request')
            state[pid], last[pid] = 'search', at
        elif kind == 'commit':
            _require(state.get(pid) in ('search', 'transit'), 'commit without searching/transit state')
            state[pid] = 'transit'
        elif kind == 'arrive':
            _require(state.get(pid) == 'transit', 'arrival without transit waiting state')
            state[pid] = 'queue'
        elif kind == 'arrival_unable':
            _require(state.get(pid) == 'queue', 'arrival failure outside queue waiting state')
            state[pid] = 'transfer'
        elif kind == 'transfer_search':
            _require(state.get(pid) == 'transfer', 'transfer search without transfer waiting state')
            state[pid] = 'search'
        elif kind == 'treatment_start':
            _require(state.get(pid) == 'queue', 'treatment start outside queue waiting state')
            state[pid] = 'treating'
    for pid in specs:
        _require(pid in state, 'missing patient request in waiting denominator')
        if state[pid] in ('search', 'queue', 'transfer'):
            waits[pid][state[pid]] += end - last[pid]
    details = []
    for pid in sorted(specs):
        row, spec = outcomes[pid], specs[pid]
        endpoint = row['treatment_start_t']
        duration = (endpoint if endpoint is not None else end) - spec['search_start_t']
        _require(duration >= 0, 'negative request to treatment duration')
        details.append(dict(patient=pid, priority=spec.get('priority', 4),
            search_wait_minutes=waits[pid]['search'],
            queue_wait_minutes=waits[pid]['queue'],
            transfer_wait_minutes=waits[pid]['transfer'],
            total_wait_minutes=sum(waits[pid].values()),
            request_to_treatment_minutes=None if endpoint is None else duration,
            request_to_treatment_capped_minutes=duration,
            treated=endpoint is not None))
    return dict(patients=len(details), untreated=sum(not row['treated'] for row in details),
        treated_denominator=sum(row['treated'] for row in details),
        search_wait_sum_minutes=sum(row['search_wait_minutes'] for row in details),
        queue_wait_sum_minutes=sum(row['queue_wait_minutes'] for row in details),
        transfer_wait_sum_minutes=sum(row['transfer_wait_minutes'] for row in details),
        total_wait_sum_minutes=sum(row['total_wait_minutes'] for row in details),
        priority_weighted_total_wait_sum_minutes=sum(row['priority'] * row['total_wait_minutes'] for row in details),
        max_patient_wait_minutes=max((row['total_wait_minutes'] for row in details), default=0),
        request_to_treatment_sum_minutes=sum(row['request_to_treatment_minutes'] for row in details if row['treated']),
        request_to_treatment_capped_sum_minutes=sum(row['request_to_treatment_capped_minutes'] for row in details),
        priority_weighted_request_to_treatment_capped_sum_minutes=sum(
            row['priority'] * row['request_to_treatment_capped_minutes'] for row in details),
        patient_details=details)

def _synthetic_minutes(origin, hospital):
    """Independent declared straight-distance estimator; no route provider."""
    lat1, lon1 = origin
    lat2, lon2 = hospital['lat'], hospital['lon']
    p1, p2 = math.radians(lat1), math.radians(lat2)
    delta_lat, delta_lon = p2 - p1, math.radians(lon2 - lon1)
    value = math.sin(delta_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(delta_lon / 2) ** 2
    distance = 2 * 6371.0 * math.asin(math.sqrt(value)) * 1.3
    return max(1, math.ceil(distance / 40.0 * 60))

def audit_event_review(scenario, branch):
    """Require each retry to follow a delivered current feasible report."""
    enabled = branch['event_review_enabled']
    _require(type(enabled) is bool, 'missing explicit event-review mode')
    _require(not enabled or branch['information_mode'] == 'observed',
             'event review outside observed information mode')
    workflow = _EarlyWorkflow(scenario)
    reports, used, cycles = {}, set(), []
    expected = {}
    ordered = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in ordered}
    positions = {p['id']: p.get('location') for p in scenario['patients']}
    failed_hospitals = {pid: set() for pid in positions}
    hospitals = {h['id']: h for h in scenario['hospitals']}
    for event in ordered:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'resource_report_received' and event['applied']:
            reports[hid] = event
        elif kind == 'arrive' and pid in positions:
            positions[pid] = [hospitals[hid]['lat'], hospitals[hid]['lon']]
        elif kind == 'arrival_unable' and pid in failed_hospitals:
            failed_hospitals[pid].add(hid)
        elif kind == 'backup_failed_reopened' and pid in failed_hospitals:
            failed_hospitals[pid].discard(hid)
        elif kind == 'event_review_cycle':
            _require(enabled, 'event-review cycle on disabled branch')
            _require(not expected, 'previous event-review cycle omitted eligible current-report retry')
            phase = event['phase']
            _require(phase in ('pre_queries', 'after_treatments'), 'unknown event-review phase')
            _require(set(event) == {'t','type','event_sequence','phase'},
                     'event-review cycle carries unknown policy fields')
            cycles.append((at, phase))
            for patient in sorted(workflow.specs, key=lambda key: (
                    -workflow.specs[key].get('priority',4),
                    workflow.specs[key].get('search_start_t',0), key)):
                if workflow.states.get(patient) != 'SEARCH':
                    continue
                if any(p == patient and count > 0 for (p, _), count in workflow.asks.items()) \
                        or any(p == patient for p, _ in workflow.offers):
                    continue
                row = workflow._base_patient(patient)
                choices = []
                for hospital in row['compatible_hospitals']:
                    if hospital in failed_hospitals[patient]:
                        continue
                    source = reports.get(hospital)
                    if source is None or source['t'] != at \
                            or (patient, source['event_sequence']) in used:
                        continue
                    physical_structure = hospitals[hospital].get('capacity', {})
                    if any(physical_structure.get(resource, 0) < quantity
                           for resource, quantity in row['demand'].items()):
                        continue
                    views = information._views('observed', workflow.observations, workflow.truth,
                                               hospital, row['demand'], at)
                    if information._check_reason(views, row['demand']) is None:
                        choices.append(hospital)
                if choices:
                    origin = positions[patient]
                    _require(isinstance(origin, (list, tuple)) and len(origin) == 2,
                             'event-review patient lacks declared current position')
                    chosen = min(choices, key=lambda target: (
                        _synthetic_minutes(origin, hospitals[target]), target))
                    expected[patient] = chosen, reports[chosen]['event_sequence']
        elif kind == 'resource_event_review':
            _require(enabled and pid == next(iter(expected),None) and
                     (hid, event['report_event_sequence']) == expected.pop(pid),
                     'event retry lacks latest same-minute complete feasible report or eligible current waiter')
            key = pid, event['report_event_sequence']
            _require(key not in used, 'same source packet retried a patient more than once')
            used.add(key)
            previous = event['previous_retry_at']
            _require(previous is None or type(previous) is int and previous >= 0,
                     'invalid previous retry clock')
            check, ask = indexed.get(seq + 1), indexed.get(seq + 2)
            _require(check is not None and check['type'] == 'information_check'
                     and check['purpose'] == 'inquiry' and check['eligible'] is True
                     and check['reason'] is None and check['t'] == at
                     and check['patient'] == pid and check['hospital'] == hid
                     and ask is not None and ask['type'] == 'ask' and ask['t'] == at
                     and ask['patient'] == pid and ask['hospital'] == hid,
                     'event review did not immediately send a new patient inquiry')
            _require(set(event) == {'t', 'type', 'event_sequence', 'patient', 'hospital',
                                   'report_event_sequence', 'previous_retry_at'},
                     'event review carries unknown or future policy fields')
        workflow.apply(event)
    _require(not expected, 'last event-review cycle omitted eligible retry')
    end = scenario.get('max_minutes', 180)
    expected_cycles = [(at, phase) for at in range(end + 1)
                       for phase in ('pre_queries', 'after_treatments')] if enabled else []
    _require(cycles == expected_cycles, 'missing/extra event-review cycle')
    return dict(event_review_cycles=len(cycles), event_review_retries=len(used))


def backup_metrics(branch):
    events=branch['events']
    return dict(backup_retained_episodes=sum(e['type']=='backup_retained' for e in events),
        backup_retained_queries=sum(len(e['pending']) for e in events if e['type']=='backup_retained'),
        backup_retained_offers=sum(len(e['offers']) for e in events if e['type']=='backup_retained'),
        backup_reply_receipts=sum(e['type']=='backup_reply_received' for e in events),
        backup_transfer_reuses=sum(e['type']=='backup_transfer_reused' for e in events),
        backup_preparation_ready=sum(e['type']=='backup_preparation_ready' for e in events),
        backup_closed_episodes=sum(e['type']=='backup_closed' for e in events),
        backup_report_reopens=sum(e['type']=='backup_failed_reopened' for e in events),
        backup_alert_retries=sum(e['type']=='backup_alert_retry' for e in events),
        backup_wall_seconds=branch['metrics']['backup_wall_seconds'])


def audit_branch(scenario,branch,*,exact_limit=20000):
    backups=audit_backups(scenario,branch)
    added=audit_no_added_source(scenario,branch)
    early=audit_early_offers(scenario,branch)
    checked=_audit_information_branch(scenario,branch,exact_limit=exact_limit)
    checked['counts'].update(audit_guard(scenario,branch))
    checked['counts'].update(audit_responsiveness(scenario,branch))
    checked['counts'].update(stable.audit_stability(scenario,branch))
    checked['counts'].update(total.audit_publications(scenario,branch))
    checked['counts'].update(audit_event_review(scenario,branch))
    checked['counts'].update(added)
    checked['counts'].update(early)
    checked['counts'].update(backups)
    checked['counts'].update(audit_motion(scenario,branch))
    updates,early_metrics,backup=total.update_metrics(branch),early_offer_metrics(branch),backup_metrics(branch)
    for key,value in {**updates,**early_metrics,**backup}.items():
        _require(base._same(branch['metrics'][key],value),'raw recovery metric mismatch: '+key)
    for flag,field in (('resource_updates_enabled','update_wall_seconds'),
                       ('event_review_enabled','event_review_wall_seconds'),
                       ('early_offers_enabled','early_offer_wall_seconds'),
                       ('retained_backups_enabled','backup_wall_seconds')):
        _finite(branch['metrics'][field],field)
        _require(branch[flag] or branch['metrics'][field]==0,'disabled intervention reports computation time')
    _require(branch['metrics']['early_offer_wall_seconds']>=sum(r['elapsed_seconds'] for r in branch['early_offer_records']),
             'early-offer total computation time below evaluations')
    checked.update(guard=responsive.guard_metrics(branch),responsiveness=responsive.responsiveness_metrics(branch),
        stability=stable.stability_metrics(branch),waiting=waiting_metrics(scenario,branch),
        updates=updates,early_offers=early_metrics,backups=backup)
    return checked


def _audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    _require(report.get('kind') == 'outage_recovery_comparison'
             and report.get('experiment_id') == EXPERIMENT_ID and report.get('synthetic') is True,
             'wrong outage-recovery report identity or synthetic scope')
    _require(report['phase'] in ('development', 'validation'), 'invalid report phase')
    _require(isinstance(report['source_sha256'], dict) and report['source_sha256'], 'missing source manifest')
    if expected_source_sha256 is not None:
        _require(report['source_sha256'] == expected_source_sha256, 'source hashes differ from expected manifest')
    _require(isinstance(report['runtime_versions'], dict) and report['runtime_versions'], 'missing runtime manifest')
    seeds, families = report['seeds'], report['families']
    _require(seeds and len(set(seeds)) == len(seeds)
             and all(type(seed) is int and seed >= 0 for seed in seeds), 'invalid or duplicated phase seeds')
    _require(families and len(set(families)) == len(families)
             and all(family in FAMILIES for family in families), 'unknown or duplicated outage-recovery families')
    if lock is not None:
        _require(lock.get('kind') == 'outage_recovery_lock' and lock.get('experiment_id') == EXPERIMENT_ID,
                 'wrong development lock identity')
        for key in ('source_sha256', 'runtime_versions', 'settings', 'families'):
            _require(report[key] == lock[key], 'report differs from lock: ' + key)
        _require(lock['development_seeds'] == DEVELOPMENT_SEEDS
                 and lock['validation_seeds'] == VALIDATION_SEEDS
                 and lock['families'] == list(FAMILIES), 'lock differs from preregistered outage-recovery seeds/families')
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
                 'scenario lacks synthetic outage-recovery comparison identity')
        branches = case['branches']
        _require([branch['id'] for branch in branches] == POLICY_IDS, 'branch output order differs')
        offset = (seeds.index(scenario['seed']) + families.index(scenario['family'])) % len(POLICY_IDS)
        _require(case['execution_order'] == POLICY_IDS[offset:] + POLICY_IDS[:offset],
                 'execution order differs from prescribed rotation')
        for name in ('summaries', 'information_metrics', 'guard_metrics',
                     'responsiveness_metrics', 'stability_metrics', 'waiting_metrics', 'update_metrics', 'early_offer_metrics', 'backup_metrics', 'diagnostics'):
            _require(set(case[name]) == set(POLICY_IDS), 'case branch IDs mismatch: ' + name)
        _require(set(case['paired_comparisons']) == {name for name, _, _ in COMPARISON_PAIRS},
                 'outage-recovery paired comparison IDs mismatch')
        by_id = {}
        for branch in branches:
            code = branch['id']
            by_id[code] = branch
            _require(branch.get('comparison_id') == EXPERIMENT_ID
                     and branch.get('source_experiment') == 'outage_recovery_comparison',
                     'branch lacks outage-recovery comparison instrumentation')
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
                     and branch['early_offers_enabled'] == spec['early_offers']
                     and type(branch['retained_backups_enabled']) is bool
                     and branch['retained_backups_enabled'] == spec['retained_backups']
                     and type(branch['transfer_same_tick_enabled']) is bool
                     and branch['transfer_same_tick_enabled'] == spec['transfer_same_tick']
                     and type(branch['recovery_followup_enabled']) is bool
                     and branch['recovery_followup_enabled'] == spec['recovery_followup'],
                     'branch confirmation/stability flags/grace differ from configured policy')
            checked = audit_branch(scenario, branch, exact_limit=exact_limit)
            for saved, actual in (('summaries', 'metrics'), ('information_metrics', 'information'),
                                  ('guard_metrics', 'guard'), ('responsiveness_metrics', 'responsiveness'),
                                  ('stability_metrics', 'stability'), ('waiting_metrics', 'waiting'),
                                  ('update_metrics', 'updates'), ('early_offer_metrics', 'early_offers'), ('backup_metrics', 'backups')):
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
                     'raw paired outage-recovery/common-patient mismatch: ' + name)
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
        source_contract='existing initial/feed/confirmation and original patient inquiry replies only; no added publications; no future work or restoration calendar',
        waiting_scope='all patients SEARCH+QUEUED+TRANSFER_WAIT through fixed horizon; transit excluded',
        proof_scope='verified_reported_model_only_not_canonical_tie_actual_gate_or_whole_episode')

def audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    """Audit saved artifacts without executing the experiment or allocation rule."""
    _require(type(exact_limit) is int and exact_limit >= 0, 'invalid exact enumeration limit')
    try:
        return _audit_report(report, expected_source_sha256=expected_source_sha256,
                             lock=lock, exact_limit=exact_limit)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise OutageRecoveryAuditError('malformed outage-recovery artifact') from error

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

def _settings(report):
    settings = report['settings']
    _require(base._same({key: value for key, value in settings.items() if key != 'policies'}, FIXED_SETTINGS),
             'fixed outage-recovery experiment settings differ')
    policies = settings['policies']
    _require(isinstance(policies, list) and [policy.get('id') for policy in policies] == POLICY_IDS,
             'comparison requires six canonical outage-recovery branches')
    fields = {'id', 'label', 'mode', 'rule', 'k', 'wait', 'notify', 'protection',
        'prefetch', 'watch', 'grace_minutes', 'stable_ties', 'time_limit_seconds',
        'seed', 'require_backend', 'resource_updates', 'event_review', 'early_offers', 'retained_backups', 'transfer_same_tick','recovery_followup'}
    for policy in policies:
        _require(set(policy) == fields and policy['mode'] == 'observed' and policy['rule'] == 'C1',
                 'configured outage-recovery policy fields/mode/rule mismatch')
        for name in ('notify', 'protection', 'prefetch', 'watch', 'stable_ties', 'require_backend'):
            _require(policy[name] is True, 'outage-recovery baseline setting differs: ' + name)
        _require(policy['k'] == 3 and policy['wait'] == 2 and policy['grace_minutes'] == 3
                 and policy['time_limit_seconds'] == .05 and policy['seed'] == 0,
                 'outage-recovery inquiry, protection or solver setting differs')
        _require(policy['resource_updates'] is False,
                 'additional resource reporting source enabled in policy')
        _require(type(policy['event_review']) is bool and
                 policy['event_review'] == (policy['id'] in ('review_stable_C1','existing_info_C1','retained_backup_C1','outage_recovery_C1'))
                 and type(policy['early_offers']) is bool and
                 policy['early_offers'] == (policy['id'] in ('early_stable_C1','existing_info_C1','retained_backup_C1','outage_recovery_C1')),
                 'outage-recovery policy ablation differs')
        _require(type(policy['retained_backups']) is bool and policy['retained_backups'] == (policy['id'] in ('retained_backup_C1','outage_recovery_C1'))
                 and type(policy['transfer_same_tick']) is bool and policy['transfer_same_tick'] == (policy['id']=='outage_recovery_C1'),
                 'outage-recovery retention/preparation ablation differs')
        _require(type(policy['recovery_followup']) is bool and policy['recovery_followup']==(policy['id']=='outage_recovery_C1'),
                 'outage-recovery received-evidence followup ablation differs')
    return {policy['id']: policy for policy in policies}


FIXED_SETTINGS=deepcopy(existing.FIXED_SETTINGS)
FIXED_SETTINGS.update(
    execution_order='rotate six branches by seed index plus family index; canonical output order',
    compute_time='original allocation/confirmation, event/early-offer review and backup receipt bookkeeping measured separately; not added to simulation clock',
    tuning='fixed original-work retention and unchanged failed-arrival preparation before new development and validation')
FIXED_SETTINGS['retained_backups']=dict(
    trigger='successful same-tick early commitment; preserve other already submitted pending patient inquiries and unexpired received patient offers',
    input='known original hospital/attempt/submission/absolute-timeout/source-event handles and actual acceptance/source-event/expiry only; no hidden reply time or silence',
    delivery='original hospital work calendar; retained requests cannot reply after original sender timeout; normal patient acceptance never refreshes resource counts',
    healthy_transit='receive only; no automatic reroute merely because a backup accepts',
    arrival='preserve unfinished work and original live offer timestamps across actual failed arrival and unchanged preparation',
    preparation='same-tick branch enters ordinary SEARCH at original failed-arrival plus preparation time; then unchanged resource checks, opportunity guard, C1 and physical gate',
    limitations='no future outage prediction; no additional resource reports, shortened route, pause or skipped transfer preparation')
FIXED_SETTINGS['waiting_evaluation']['factors']='same original reporting contract; early offers, original backup work retention and preparation-complete same-tick review separately'
FIXED_SETTINGS['recovery_followup']=dict(
    report_reopen='registered SEARCH in active backup episode; resource-capacity failure; latest applied report received this minute with source time not before failure; every required resource fresh sufficient and individually sourced at or after failure; reopen failed hospital and send new patient inquiry only',
    alert_retry='active notified TRANSIT; original backup offer expiration received before current alert; no open or live backup; current fresh sufficient quantities; one fresh patient inquiry per expired backup and alert',
    exclusions='no capability-failure reopening, future restoration schedule, extra resource query, acceptance renewal or gate bypass')


if __name__=='__main__':
    main()
