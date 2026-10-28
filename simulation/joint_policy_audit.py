"""Raw audit of joint inquiry and allocation experiments.

No replay engine, assignment implementation, solver or observation store is
imported or executed. Current-information, physical execution and whole-cohort
metrics are reconstructed independently. Server inquiry selector calls check
reproduction only and do not constitute independent optimality evidence.
Frozen pure audit routines are copied locally where quarter-minute validation
replaces the old integer-minute clock; prior sources remain unchanged.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import math
import random
from . import online_allocation_audit as base
from . import partial_information_audit as information
from . import responsive_confirmation_audit as responsive
from . import outage_recovery_audit as recovery
from . import h1_online_audit as h1
from .stable_c1_audit import STABILITY_RULE
from .h1_online_audit import _finite

JointPolicyAuditError = base.OnlineAllocationAuditError
CLOCK_ORIGIN_SECONDS = 86400
_TruthLedger = information._TruthLedger
_number = information._number
_quantity = information._quantity
_indexed_events = information._indexed_events
_views = information._views
_check_reason = information._check_reason
_initial_reports = information._initial_reports
_apply_observation = information._apply_observation
_exogenous_contract = information._exogenous_contract
_model_evidence = information._model_evidence
_objective_dict = information._objective_dict
information_metrics = information.information_metrics
_EarlyWorkflow = recovery._EarlyWorkflow
_early_snapshot = recovery._early_snapshot
_early_expected = recovery._early_expected
_greedy = h1._greedy
OBJECTIVES = h1.OBJECTIVES
BUDGET_SCOPE = h1.BUDGET_SCOPE

def _require(condition, message):
    base._require(condition, message)

def _clock(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0 and value*4 == int(value*4)

def _end(scenario, branch):
    end = scenario.get('max_minutes', 180)
    _require(branch['metrics']['observation_end_t'] == end, 'observation horizon mismatch')
    return end



def _event_rows(scenario, branch):
    """Recover endpoints from events, then verify the separately saved outcomes."""
    end = _end(scenario, branch)
    patients = {p['id']: p for p in scenario['patients']}
    _require(len(patients) == len(scenario['patients']), 'duplicate scenario patients')
    outcomes = {p['id']: p for p in branch['patient_outcomes']}
    _require(len(outcomes) == len(branch['patient_outcomes']) and set(outcomes) == set(patients),
             'outcome patient IDs differ from scenario')
    rows = {pid: dict(id=pid, first_commit_t=None, final_arrive_t=None,
        treatment_start_t=None, treatment_complete_t=None, inquiries=0, redirects=0,
        transfers=0, commit_events=0) for pid in patients}
    last_t = -1
    notifications = []
    events = branch['events']
    for index, event in enumerate(events):
        at = _number(event['t'], 'event time')
        _require(last_t <= at <= end, 'events unordered or outside observation window')
        last_t = at
        pid, kind = event.get('patient'), event['type']
        if pid not in patients:
            _require(pid is None or event.get('external') or str(pid).startswith('EXTERNAL-'),
                     'event contains unknown patient')
            continue
        spec, row = patients[pid], rows[pid]
        occur, search = spec.get('occur_t', 0), spec['search_start_t']
        _require(at >= occur, 'event occurs before patient exists')
        if kind in ('ask', 'commit', 'accept', 'decline', 'no_response', 'notify',
                    'redirect', 'transfer_depart', 'arrive', 'treatment_start'):
            _require(at >= search, 'clinical workflow event precedes search start')
        if kind == 'ask':
            row['inquiries'] += 1
        elif kind == 'commit':
            row['commit_events'] += 1
            if row['first_commit_t'] is None:
                row['first_commit_t'] = at
        elif kind == 'redirect':
            _require(row['first_commit_t'] is not None, 'redirect without earlier selection')
            row['redirects'] += 1
        elif kind == 'transfer_depart':
            _require(row['first_commit_t'] is not None, 'transfer without earlier selection')
            row['transfers'] += 1
        elif kind == 'arrive':
            _require(row['first_commit_t'] is not None, 'arrival without selection')
            row['final_arrive_t'] = at
        elif kind == 'arrival_unable':
            _require(row['final_arrive_t'] == at, 'failed arrival without matching arrival')
            row['final_arrive_t'] = None
        elif kind == 'treatment_start':
            _require(row['final_arrive_t'] is not None, 'treatment without successful final arrival')
            _require(row['treatment_start_t'] is None, 'duplicate patient treatment start')
            row['treatment_start_t'] = at
        elif kind == 'treatment_complete':
            _require(row['treatment_start_t'] is not None, 'completion without treatment')
            row['treatment_complete_t'] = at
        elif kind == 'notify':
            notifications.append((index, event))
    for pid, row in rows.items():
        outcome, spec = outcomes[pid], patients[pid]
        _require(spec['search_start_t'] <= end, 'future, unobserved patient in denominator')
        _require(outcome['occur_t'] == spec.get('occur_t', 0) and
                 outcome['search_start_t'] == spec['search_start_t'] and
                 outcome['priority'] == spec.get('priority', 4), 'outcome patient inputs mismatch')
        for field in ('first_commit_t', 'final_arrive_t', 'treatment_start_t',
                      'treatment_complete_t', 'inquiries', 'redirects', 'transfers'):
            _require(outcome[field] == row[field], f'{pid}: outcome/event mismatch: {field}')
        _require(row['commit_events'] == int(row['first_commit_t'] is not None) +
                 row['redirects'] + row['transfers'], 'commits do not match first selection and destination changes')
        expected_state = ('COMPLETE' if row['treatment_complete_t'] is not None else
            'INCOMPLETE' if row['treatment_start_t'] is not None else
            'UNASSIGNED' if row['first_commit_t'] is None else 'NOT_TREATED')
        _require(outcome['state'] == expected_state, 'outcome state inconsistent with endpoints')
    lags = []
    for index, event in notifications:
        next_redirect = next((later for later in events[index + 1:]
            if later['type'] == 'redirect' and later.get('patient') == event['patient']), None)
        if next_redirect is not None:
            lags.append(next_redirect['t'] - event['t'])
    return rows, lags, len(notifications)


def raw_branch_metrics(scenario, branch):
    """Recompute full-flow metrics from raw events, with explicit denominators."""
    rows, notification_lags, notifications = _event_rows(scenario, branch)
    patients = {p['id']: p for p in scenario['patients']}
    end = _end(scenario, branch)
    actual, capped = {name: [] for name in ('selection', 'arrival', 'treatment')}, {
        name: [] for name in ('selection', 'arrival', 'treatment')}
    for pid, row in rows.items():
        p = patients[pid]
        for name, field, origin in (
            ('selection', 'first_commit_t', p['search_start_t']),
            ('arrival', 'final_arrive_t', p.get('occur_t', 0)),
            ('treatment', 'treatment_start_t', p.get('occur_t', 0))):
            endpoint = row[field]
            elapsed = max(0, end - origin) if endpoint is None else endpoint - origin
            if endpoint is not None:
                actual[name].append(elapsed)
            capped[name].append(elapsed)
    high = [row for pid, row in rows.items() if patients[pid].get('priority', 4) == 4]
    n = len(rows)
    inquiry_count = sum(row['inquiries'] for row in rows.values())
    result = dict(patients=n,
        unassigned=sum(row['first_commit_t'] is None for row in rows.values()),
        not_arrived=sum(row['final_arrive_t'] is None for row in rows.values()),
        untreated=sum(row['treatment_start_t'] is None for row in rows.values()),
        high_priority_unassigned=sum(row['first_commit_t'] is None for row in high),
        high_priority_untreated=sum(row['treatment_start_t'] is None for row in high),
        inquiries=inquiry_count, inquiries_per_patient=inquiry_count / n if n else 0,
        redirects=sum(row['redirects'] for row in rows.values()),
        transfers=sum(row['transfers'] for row in rows.values()), notifications=notifications,
        notification_redirect_pairs=len(notification_lags),
        notification_without_redirect=notifications - len(notification_lags),
        notify_to_redirect_mean_minutes=base._mean(notification_lags))
    for name in actual:
        result[name + '_denominator'] = len(actual[name])
        result[name + '_mean_minutes'] = base._mean(actual[name])
        result[name + '_capped_mean_minutes'] = base._mean(capped[name])
    return result


def _problem(scenario, decision):
    at = _number(decision['t'], 'decision time')
    inputs = decision['inputs']
    _require(set(inputs) <= {'patients', 'hospitals', 'free_resources', 'travel_minutes'},
             'non-current or unknown input field in decision snapshot')
    patients = inputs['patients']
    _require(len({p['id'] for p in patients}) == len(patients), 'duplicate decision patients')
    scenario_patients = {p['id']: p for p in scenario['patients']}
    hospitals = {h['id']: h for h in inputs['hospitals']}
    _require(len(hospitals) == len(inputs['hospitals']), 'duplicate decision hospitals')
    free = {}
    for hid, h in hospitals.items():
        _require(set(h) <= {'id', 'capacity', 'used', 'committed', 'queued', 'capabilities'},
                 'future or unknown hospital snapshot field')
        for name in ('capacity', 'used', 'committed', 'queued'):
            for quantity in h.get(name, {}).values():
                base._integer(quantity, 'invalid current resource ledger')
        resources = set().union(*(set(h.get(name, {})) for name in ('capacity', 'used', 'committed', 'queued')))
        for resource in resources:
            free[hid, resource] = (h.get('capacity', {}).get(resource, 0) -
                h.get('used', {}).get(resource, 0) - h.get('committed', {}).get(resource, 0) -
                h.get('queued', {}).get(resource, 0))
    saved_free = {(r['hospital'], r['resource']): r['free'] for r in inputs['free_resources']}
    _require(len(saved_free) == len(inputs['free_resources']), 'duplicate free-resource keys')
    _require(all(key in free and free[key] == quantity for key, quantity in saved_free.items()),
             'free resources differ from capacity - used - committed - queued')
    travel = {(r['patient'], r['hospital']): base._integer(r['minutes'], 'invalid observed travel estimate')
              for r in inputs['travel_minutes']}
    _require(len(travel) == len(inputs['travel_minutes']), 'duplicate travel edges')
    options = {}
    ttl = scenario.get('accept_ttl_minutes', 10)
    for p in patients:
        _require(set(p) <= {'id', 'priority', 'demand', 'required_capabilities', 'position',
                            'request_t', 'options', 'accepted_at', 'valid_offer_expiries'},
                 'future or unknown patient snapshot field')
        _require(p['id'] in scenario_patients, 'decision contains unknown patient')
        spec = scenario_patients[p['id']]
        _require(p['request_t'] == spec['search_start_t'] <= at, 'future decision patient')
        _require(p['priority'] == spec.get('priority', 4) and p['priority'] in (1, 2, 4), 'decision priority mismatch')
        _require(p['demand'] == spec.get('resource_demands', {'ed_bed': 1}), 'decision resource demand mismatch')
        _require(len(set(p['options'])) == len(p['options']), 'duplicate decision offers')
        for quantity in p['demand'].values():
            base._integer(quantity, 'invalid patient demand')
        feasible = []
        for hid in p['options']:
            _require(hid in hospitals, 'unknown offered hospital')
            _require((p['id'], hid) in travel, 'missing offered travel estimate')
            accepted = p['accepted_at'].get(hid)
            _require(accepted is not None and p['request_t'] <= accepted <= at < accepted + ttl,
                     'future, missing or expired acceptance')
            if 'valid_offer_expiries' in p:
                _require(p['valid_offer_expiries'].get(hid) == accepted + ttl,
                         'offer expiry differs from observed acceptance and configured TTL')
            h = hospitals[hid]
            compatible = set(p.get('required_capabilities', [])) <= set(h.get('capabilities', []))
            if compatible and all(free.get((hid, resource), 0) >= quantity
                                  for resource, quantity in p['demand'].items() if quantity > 0):
                feasible.append(hid)
        options[p['id']] = feasible
    return patients, free, options, travel


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
                _require(_clock(source_at) and interval > 0
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


def audit_h1_branch(scenario, branch, spec):
    """Check H1 evidence after the frozen raw trajectory audit has succeeded."""
    decisions = branch.get('allocation_decisions', [])
    records = branch.get('heuristic_records', [])
    _require(isinstance(records, list), 'invalid heuristic records')
    _require(branch['metrics'].get('whole_episode_optimal_proven') is False,
             'H1 whole-episode optimality claim')
    code = branch['policy']['rule']
    if code != 'H1':
        _require(not records, 'heuristic records on non-H1 branch')
        _require(all('heuristic_record_index' not in d for d in decisions),
                 'heuristic record reference on non-H1 branch')
        return dict(heuristic_batches=0, heuristic_baselines_checked=0,
                    heuristic_accepted_moves=0, heuristic_evaluated_moves=0)
    metadata = branch['policy'].get('heuristic', {})
    expected = dict(backend='bounded_joint_heuristic',
                    time_limit_seconds=spec['time_limit_seconds'], max_moves=spec['max_moves'],
                    time_budget_scope=BUDGET_SCOPE)
    _require(base._same(metadata, expected), 'reported heuristic settings differ from configured policy')
    _require(not branch.get('solver_records'), 'H1 records mixed with CP-SAT solver records')
    used = set()
    totals = dict(heuristic_batches=0, heuristic_baselines_checked=0,
                  heuristic_accepted_moves=0, heuristic_evaluated_moves=0)
    for index, decision in enumerate(decisions):
        _require(decision.get('rule') == 'H1', 'H1 decision rule mismatch')
        if decision['kind'] != 'batch':
            _require('heuristic_record_index' not in decision, 'single decision references heuristic batch')
            continue
        _require(decision.get('method') == 'heuristic', 'H1 batch method is not heuristic')
        _require(decision.get('optimal_proven') is False, 'H1 decision falsely claims optimality')
        record_index = decision.get('heuristic_record_index')
        base._integer(record_index, 'invalid heuristic record index')
        _require(record_index < len(records), 'missing heuristic record')
        _require(record_index not in used, 'heuristic record reused by multiple decisions')
        used.add(record_index)
        record = records[record_index]
        _require(isinstance(record, dict), 'invalid heuristic record')
        _require(record.get('decision_index') == index and record.get('t') == decision['t'],
                 'heuristic record points to another decision')
        _require(record.get('scope') == 'current_same_minute_accepted_offers',
                 'wrong heuristic record scope')
        _require(record.get('status') == 'HEURISTIC' and record.get('optimal_proven') is False,
                 'H1 record falsely claims optimality')
        plan = decision['assignments']
        _require(record.get('assignments') == plan, 'heuristic proposal differs from actual commits')
        if 'proposed_assignments' in record:
            _require(record['proposed_assignments'] == plan, 'heuristic proposal differs from actual commits')
        patients, free, options, travel = _problem(scenario, decision)
        objective = base._objective(patients, plan, travel)
        _require(record.get('objective_names') == OBJECTIVES and record.get('objective') == objective,
                 'heuristic objective differs from raw plan')
        _require(record.get('batch_size') == len(patients)
                 and record.get('edge_count') == sum(map(len, options.values()))
                 and record.get('assigned_count') == sum(hid is not None for hid in plan.values()),
                 'heuristic problem size or assigned count mismatch')
        baseline = _greedy(patients, free, options, travel)
        baseline_objective = base._objective(patients, baseline, travel)
        _require(record.get('baseline_assignments') == baseline,
                 'heuristic baseline differs from independently rebuilt greedy plan')
        _require(record.get('baseline_objective') == baseline_objective
                 and record.get('baseline_assigned_count') == sum(hid is not None for hid in baseline.values()),
                 'heuristic baseline objective or count mismatch')
        _require(objective <= baseline_objective, 'heuristic worsens its safe greedy baseline')
        _require(record.get('time_limit_seconds') == spec['time_limit_seconds']
                 and record.get('max_moves') == spec['max_moves']
                 and record.get('time_budget_scope') == BUDGET_SCOPE,
                 'heuristic record settings differ from configured policy')
        evaluated = base._integer(record.get('evaluated_moves'), 'invalid evaluated move count')
        accepted = base._integer(record.get('accepted_moves'), 'invalid accepted move count')
        _require(accepted <= evaluated <= spec['max_moves'], 'heuristic move counts exceed limits')
        wall = _finite(record.get('wall_seconds'), 'heuristic wall time')
        preprocessing = _finite(record.get('preprocessing_seconds'), 'heuristic preprocessing time')
        search = _finite(record.get('search_seconds'), 'heuristic search time')
        _require(math.isclose(wall, preprocessing + search, rel_tol=1e-9, abs_tol=1e-9),
                 'heuristic timing components do not sum to wall time')
        _require(wall <= _finite(decision['elapsed_seconds'], 'decision wall time') + 1e-9,
                 'heuristic wall time exceeds enclosing decision time')
        # The optional budget is cooperative, not a hard bound on total wall time.
        improvements = record.get('improvements', [])
        move_types = record.get('accepted_move_types', [])
        _require(isinstance(improvements, list) and isinstance(move_types, list)
                 and len(improvements) == len(move_types) == accepted,
                 'heuristic improvement trace count mismatch')
        last_evaluation, previous = 0, baseline_objective
        for step, kind in zip(improvements, move_types):
            evaluation = base._integer(step.get('evaluation'), 'invalid improvement evaluation', minimum=1)
            current = step.get('objective')
            _require(last_evaluation < evaluation <= evaluated and step.get('kind') == kind,
                     'heuristic improvement trace order mismatch')
            _require(isinstance(current, list) and len(current) == 2
                     and all(type(v) is int and v >= 0 for v in current) and current < previous,
                     'heuristic improvement trace is not strictly improving')
            last_evaluation, previous = evaluation, current
        _require(previous == objective, 'heuristic final objective differs from improvement trace')
        totals['heuristic_batches'] += 1
        totals['heuristic_baselines_checked'] += 1
        totals['heuristic_accepted_moves'] += accepted
        totals['heuristic_evaluated_moves'] += evaluated
    _require(used == set(range(len(records))), 'orphan heuristic record')
    return totals


def _audit_information_branch(scenario, branch, *, exact_limit=20000):
    raw_metrics = raw_branch_metrics(scenario, branch)
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
        patients, free, options, travel = _problem(scenario, decision)
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


def waiting_metrics(scenario, branch):
    """Integrate all waiting patients over the fixed observation window.

    SEARCH, hospital QUEUED and TRANSFER_WAIT contribute; transit and treatment
    do not. Missing completions remain in the denominator and are censored only
    at the declared observation end. This is an evaluation, never policy input.
    """
    outcomes, _, _ = _event_rows(scenario, branch)
    end = _end(scenario, branch)
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


def _reply_environment(scenario, patient, hospital, attempt):
    matrix = scenario.get('response_matrix', {})
    entry = matrix.get(patient, {}).get(hospital, matrix.get('*', {}).get(hospital))
    quarter = entry is not None
    if entry is None:
        env = scenario.get('response_environment', {})
        entry = env.get(patient, {}).get(hospital, env.get('*', {}).get(hospital, {}))
    if entry:
        entries = entry.get('attempts')
        if entries:
            entry = entries[min(attempt - 1, len(entries) - 1)]
        delay = entry.get('delay', 2)
        return entry.get('silent', False), delay if quarter else max(1, int(delay))
    rng = random.Random('|'.join(map(str, (scenario.get('seed', 17), 'map_response',
                                          patient, hospital, attempt))))
    return rng.random() < scenario.get('nonresponse', 0), rng.randint(1, 3)


def _position(scenario, branch, patient, at):
    spec = next(row for row in scenario['patients'] if row['id'] == patient)
    legs = next(row['legs'] for row in branch['trajectories'] if row['patient'] == patient)
    prior = [leg for leg in legs[1:] if leg['depart_t'] <= at]
    if not prior:
        return spec['location']
    leg = prior[-1]
    duration = leg['arrive_t'] - leg['depart_t']
    ratio = 1 if duration <= 0 else min(1, max(0, (at - leg['depart_t']) / duration))
    return [leg['origin'][i] + (leg['destination'][i] - leg['origin'][i]) * ratio
            for i in (0, 1)]


def _selector_reproduce(selector, inputs):
    # Deliberate reproduction check. These calls are NOT independent evidence
    # that V3/V4 is optimal. Inputs are reconstructed independently below.
    from server import inquiry_policies, inquiry_v4
    if selector == 'v4':
        return inquiry_v4.decide(**deepcopy(inputs))
    name = {'v2': 'observed_v2', 'v3': 'observed_v3'}[selector]
    return inquiry_policies.decide(name, **deepcopy(inputs))


def audit_inquiries(scenario, branch):
    """Rebuild FIFO work, feedback and selector inputs from earlier emissions."""
    events = _indexed_events(branch)
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
            following = [e for e in events if seq < e['event_sequence'] and e['t'] == at
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
        _require(covered_asks == {e['event_sequence'] for e in events if e['type'] == 'ask'},
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


def audit_branch(scenario, branch, *, exact_limit=20000):
    """Audit raw data, raising JointPolicyAuditError on any contradiction."""
    ordered = _indexed_events(branch)
    _require(all(_clock(event['t']) for event in ordered), 'off-grid event clock')
    _require(branch.get('ordered_emissions', ordered) == ordered, 'ordered emission export differs')
    _require(branch.get('resource_updates_enabled') is False and not any(
        event['type'] in ('resource_update_captured', 'resource_update_cycle')
        or (event['type'] == 'resource_report_received' and 'publication_id' in event)
        for event in ordered), 'extra resource publication channel was enabled')
    result = _audit_information_branch(scenario, branch, exact_limit=exact_limit)
    counts = dict(result['counts'])
    counts.update(audit_inquiries(scenario, branch))
    counts.update(audit_stability(scenario, branch))
    counts.update(recovery.audit_motion(scenario, branch))
    counts.update(audit_backups(scenario, branch))
    counts.update(audit_guard(scenario, branch))
    counts.update(audit_pending_guard(scenario, branch))
    counts.update(audit_early_offers(scenario, branch))
    counts.update(recovery.audit_no_added_source(scenario, branch))
    waiting = waiting_metrics(scenario, branch)
    for key, value in result['metrics'].items():
        if key in branch['metrics']:
            _require(base._same(branch['metrics'][key], value), 'whole-cohort endpoint metric mismatch: ' + key)
    return dict(passed=True, counts=counts, waiting=waiting, metrics=result['metrics'],
                information=result['information'], assignment_constraint_violations=0,
                selector_check='actual pure function reproduction from independently reconstructed raw inputs',
                actual_optimality_claimed=False)


def _pending_guard_expected(snapshot):
    """Independent deficit arithmetic using one-minute original lifetimes."""
    now = snapshot['t']
    first = deepcopy(snapshot['held_since'])
    held, holds, reserved = set(), [], Counter()
    for waiter in sorted(snapshot['waiting'], key=lambda row: (-row['priority'], row['request_t'], row['id'])):
        choices, demand = waiter['compatible_hospitals'], waiter['demand'].get('ed_bed', 0)
        if len(choices) != 1 or demand <= 0:
            continue
        hid = choices[0]
        requests = [row for row in waiter['pending_inquiries'] if row['hospital'] == hid]
        if len(requests) != 1:
            continue
        request = requests[0]
        deadline = min(request['submitted_at'] + 1, request['timeout_at'])
        bed = waiter['bed_view']
        if any(view['status'] == 'fresh' and view['free'] < waiter['demand'][resource]
               for resource, view in waiter['resource_views'].items()):
            continue
        if now >= deadline or bed['status'] != 'fresh' or bed['free'] is None or bed['free'] < demand:
            continue
        contenders = [row for row in snapshot['ready'] if row['id'] not in held and hid in row['options']]
        deficit = sum(row['demand'].get('ed_bed', 0) for row in contenders) + reserved[hid] + demand - bed['free']
        victims = [row for row in contenders if len(row['compatible_hospitals']) >= 2
                   and row['priority'] <= waiter['priority'] and row['demand'].get('ed_bed', 0) > 0
                   and now < first.get(row['id'], now) + 1
                   and row['valid_offer_expiries'][hid] > now + .25
                   and any(target != hid for target in row['eligible_alternative_hospitals'])]
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
        reserved[hid] += demand
        for victim in selected:
            target = victim['id']
            first.setdefault(target, now)
            held.add(target)
            holds.append(dict(patient=target, waiting_patient=waiter['id'], hospital=hid,
                ask_event_sequence=request['ask_event_sequence'], attempt=request['attempt'],
                submitted_at=request['submitted_at'], timeout_at=request['timeout_at'], held_since=first[target],
                deadline=min(deadline, first[target] + 1), known_free=bed['free'], waiting_demand=demand,
                patient_priority=victim['priority'], waiting_priority=waiter['priority']))
    return dict(held=sorted(held), holds=holds,
                ready_after=[row['id'] for row in snapshot['ready'] if row['id'] not in held],
                held_since_after=first)


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


_pending_rows = recovery._pending_rows
_offer_rows = recovery._offer_rows

def _joint_reply_due(scenario, ordered, handle):
    source = next(event for event in ordered if event['event_sequence'] == handle['ask_event_sequence'])
    silent, delay = _reply_environment(scenario, source['patient'], handle['hospital'], handle['attempt'])
    if silent:
        return handle['timeout_at'], True
    if scenario.get('inquiry_queue', {}).get('enabled'):
        completed = next((event['t'] for event in ordered if event['type']=='review_completed'
            and event.get('kind')=='inquiry' and event.get('patient')==source['patient']
            and event.get('hospital')==handle['hospital'] and event.get('attempt')==handle['attempt']), None)
        return (handle['timeout_at'], True) if completed is None or completed > handle['timeout_at'] else (completed, False)
    return handle['submitted_at'] + delay, False

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


def audit_stability(scenario, branch):
    enabled = branch['stable_ties_enabled']
    _require(type(enabled) is bool, 'invalid stable-tie enabled flag')
    code = branch['policy']['rule']
    _require(not enabled or code == 'C1', 'stable-tie rule on a non-C1 branch')
    counts = Counter(stable_batches=0, stable_equal_objective_batches=0,
        stable_changed_batches=0, stable_solver_original_proofs=0)
    records = branch['solver_records']
    if not enabled:
        _require(all('stability' not in record for record in records),
                 'stability trace on frozen baseline branch')
        return dict(counts)
    used = set()
    for decision in branch['allocation_decisions']:
        if decision['kind'] != 'batch':
            continue
        index = base._integer(decision['solver_record_index'], 'invalid stable solver record index')
        _require(index < len(records) and index not in used, 'missing or reused stable solver record')
        used.add(index)
        record = records[index]
        trace = record.get('stability')
        _require(isinstance(trace, dict) and set(trace) == {
            'rule', 'solver_assignments', 'baseline_assignments', 'baseline_objective',
            'solver_objective', 'selected_assignments', 'applied', 'reason', 'elapsed_seconds',
            'optimality_claimed'},
            'missing or unknown stability trace fields')
        _require(trace['rule'] == STABILITY_RULE, 'wrong stable-tie rule identity')
        patients, free, options, travel = _problem(scenario, decision)
        solver, baseline = trace['solver_assignments'], _greedy(patients, free, options, travel)
        _require(base._plan_feasible(patients, free, options, solver),
                 'original CP-SAT plan is outside current feasible model')
        _require(trace['baseline_assignments'] == baseline,
                 'stable baseline differs from independently rebuilt priority/request/id nearest plan')
        _require(base._plan_feasible(patients, free, options, baseline), 'rebuilt baseline is infeasible')
        solver_objective = base._objective(patients, solver, travel)
        baseline_objective = base._objective(patients, baseline, travel)
        _require(trace['solver_objective'] == solver_objective
                 and trace['baseline_objective'] == baseline_objective,
                 'stability objective differs from raw model plan')
        _require(solver_objective <= baseline_objective,
                 'original C1 objective is worse than its deterministic feasible baseline')
        equal = solver_objective == baseline_objective
        selected = baseline if equal else solver
        applied = equal and solver != baseline
        _require(type(trace['applied']) is bool and trace['applied'] == applied,
                 'stability applied flag differs from exact-objective plan selection')
        expected_reason = 'equal_objective_baseline' if applied else \
            'equal_objective_unchanged' if equal else 'solver_strictly_better'
        _require(trace['reason'] == expected_reason, 'stability reason differs from raw objective equality')
        _require(trace['selected_assignments'] == selected
                 and record['assignments'] == selected
                 and decision['proposed_assignments'] == selected,
                 'stability final proposal differs from prescribed equal-objective selection')
        _finite(trace['elapsed_seconds'], 'stability overhead seconds')
        _finite(record['wall_seconds'], 'solver total wall seconds')
        _finite(record['solver_pipeline_wall_seconds'], 'original solver pipeline seconds')
        _require(record['wall_seconds'] + 1e-9 >= trace['elapsed_seconds'] + record['solver_pipeline_wall_seconds'],
                 'stability overhead omitted from solver wall time')
        _finite(decision['elapsed_seconds'], 'whole allocation elapsed seconds')
        _require(decision['elapsed_seconds'] + 1e-9 >= record['wall_seconds'],
                 'solver and postselection wall time omitted from allocation metrics')
        _require(record['assigned_count'] == sum(hid is not None for hid in selected.values()),
                 'selected assigned count differs from stable plan')
        _require(trace['optimality_claimed'] is False,
                 'stability postselection falsely claims an additional optimum')
        original_record = deepcopy(record)
        original_record.update(assignments=deepcopy(solver), objective=solver_objective)
        original_decision = deepcopy(decision)
        original_decision.update(assignments=deepcopy(solver),
            objective=information._objective_dict(solver_objective),
            optimal_proven=decision['model_optimal_proven'])
        proof = base._solver_proof(original_record, original_decision, patients, options, solver_objective)
        _require(not any(record.get(field, False) for field in (
            'tie_optimal_proven', 'canonical_optimal_proven', 'whole_episode_optimal_proven',
            'actual_optimal_proven', 'global_optimal_proven')),
            'current-model two-objective proof overstated as global, canonical or actual optimum')
        counts.update(stable_batches=1, stable_equal_objective_batches=int(equal),
            stable_changed_batches=int(applied), stable_solver_original_proofs=int(proof != 'unproven'))
    _require(used == set(range(len(records))), 'orphan stable solver record')
    return dict(counts)
