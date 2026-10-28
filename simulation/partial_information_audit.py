"""Independent raw-JSON audit of the partial-information comparison.

No simulation engine, assignment implementation, observation store or runner
is imported.  Reported observations, model proposals and actual confirmations
are separate contracts; an optimal reported model is never a truth guarantee.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics

from . import online_allocation_audit as base
from .h1_online_audit import audit_h1_branch, _diagnostics, _paired_tails


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = 'partial_information_20261015'
POLICY_IDS = [mode + '_' + rule for mode in ('perfect', 'observed') for rule in ('R1', 'H1', 'C1')]
STALE_AFTER = 10
DEVELOPMENT_SEEDS = [17101, 17102, 17103]
VALIDATION_SEEDS = [18101, 18102, 18103, 18104, 18105, 18106]
FAMILIES = ('fresh_control', 'missing_recheck', 'missing_unresolved', 'stale_shortage_recovery',
            'delayed_optimistic_change', 'delayed_pessimistic_recovery', 'partial_team_missing', 'out_of_order')
AGGREGATE_FIELDS = ('unassigned', 'not_arrived', 'untreated', 'inquiries_per_patient',
    'selection_capped_mean_minutes', 'arrival_capped_mean_minutes', 'treatment_capped_mean_minutes',
    'redirects', 'transfers', 'notifications', 'notification_without_redirect')
FIXED_SETTINGS = dict(stale_after_minutes=10,
    inquiry_policy='fixed_3_first_acceptance_extra_wait_2_minutes',
    observation_scope='hospital capacity/used reports; current server commitments and queues remain known',
    freshness_clock='time since source observation, not since network receipt',
    confirmation_contract='explicit synthetic resource response; patient acceptance does not update quantities',
    transient_shortage_retry_minutes=1,
    execution_order='rotate six branches by seed index plus family index; canonical output order',
    routing='synthetic coordinates/travel; no API', simulation_unit='integer minute',
    compute_time='measured separately, not added to simulation clock', tuning='none')
PREFIX_EVENTS = {'patient_occurs', 'ambulance_dispatched', 'ambulance_arrive',
                 'scene_care_start', 'scene_care_complete'}
COMPLETION_EVENTS = {'treatment_complete', 'observation_end'}
PartialInformationAuditError = base.OnlineAllocationAuditError


def _require(condition, message):
    base._require(condition, message)


def _number(value, name, *, negative=False):
    _require(not isinstance(value, bool) and isinstance(value, (int, float))
             and math.isfinite(value) and (negative or value >= 0), 'invalid ' + name)
    return value


def _quantity(value, name, *, unknown=False):
    if unknown and value is None:
        return None
    return base._integer(value, 'invalid ' + name)


def _indexed_events(branch):
    """Recover simulation emission order after the exported time sort."""
    events = branch['events']
    sequenced = []
    for event in events:
        _number(event['t'], 'event time')
        if 'event_sequence' not in event:
            _require(event['type'] in PREFIX_EVENTS | COMPLETION_EVENTS,
                     'workflow event lacks emission sequence')
            continue
        sequence = base._integer(event['event_sequence'], 'invalid event sequence')
        sequenced.append((sequence, event))
    sequenced.sort(key=lambda pair: pair[0])
    _require([sequence for sequence, _ in sequenced] == list(range(len(sequenced))),
             'event sequences are duplicated or incomplete')
    ordered = [event for _, event in sequenced]
    _require(all(left['t'] <= right['t'] for left, right in zip(ordered, ordered[1:])),
             'emission sequence reverses simulation time')
    return ordered


class _TruthLedger:
    """Rebuild actual quantities from scenario inputs and earlier raw events."""

    def __init__(self, scenario):
        self.capacities = {h['id']: dict(h.get('capacity', {})) for h in scenario['hospitals']}
        self.capabilities = {h['id']: set(h.get('capabilities', [])) for h in scenario['hospitals']}
        self.active = {h['id']: [(a['until'], dict(a['demand']))
            for a in h.get('initial_occupancy', [])] for h in scenario['hospitals']}
        self.committed = {h: {} for h in self.capacities}
        self.queued = {h: {} for h in self.capacities}
        self.patients = {p['id']: dict(demand=dict(p.get('resource_demands', {'ed_bed': 1})),
            duration=p.get('treatment_minutes', 30), capabilities=set(p.get('required_capabilities', [])))
            for p in scenario['patients']}
        self.current_destination = {}
        for index, event in enumerate(scenario.get('exogenous_events', [])):
            if event['type'] == 'external_arrival':
                self.patients['EXTERNAL-' + str(index)] = dict(
                    demand=dict(event.get('demand', {'ed_bed': 1})), duration=event.get('duration', 40), capabilities=set())
        for capacity in self.capacities.values():
            for quantity in capacity.values():
                _quantity(quantity, 'initial capacity')

    def resources(self, hid, resource_names, now):
        _require(hid in self.capacities, 'unknown truth hospital')
        result = {}
        for resource in resource_names:
            capacity = self.capacities[hid].get(resource, 0)
            used = sum(demand.get(resource, 0) for end, demand in self.active[hid] if end > now)
            committed = sum(demand.get(resource, 0) for demand in self.committed[hid].values())
            queued = sum(demand.get(resource, 0) for demand in self.queued[hid].values())
            result[resource] = dict(capacity=capacity, used=used, committed=committed,
                queued=queued, free=capacity - used - committed - queued)
        return result

    def apply(self, event):
        kind, hid, pid, at = event['type'], event.get('hospital'), event.get('patient'), event['t']
        if kind in ('resource_unavailable', 'resource_restored'):
            _require(hid in self.capacities, 'resource event on unknown hospital')
            resource = event['resource']
            delta = -1 if kind == 'resource_unavailable' else 1
            value = self.capacities[hid].get(resource, 0) + delta
            _require(value >= 0, 'negative actual resource capacity')
            self.capacities[hid][resource] = value
        elif kind == 'redirect':
            previous = event.get('previous')
            _require(previous in self.committed, 'redirect has unknown previous hospital')
            self.committed[previous].pop(pid, None)
        elif kind == 'commit':
            _require(pid in self.patients and hid in self.committed, 'invalid actual commitment')
            _require(all(pid not in commitments for commitments in self.committed.values()),
                     'patient committed twice without releasing previous reservation')
            self.committed[hid][pid] = dict(self.patients[pid]['demand'])
            self.current_destination[pid] = hid
        elif kind in ('arrive', 'external_arrival'):
            _require(pid in self.patients and hid in self.queued, 'invalid actual arrival')
            if kind == 'arrive':
                _require(pid in self.committed[hid] and self.current_destination.get(pid) == hid,
                         'arrival differs from actual reserved destination')
            self.committed[hid].pop(pid, None)
            _require(pid not in self.queued[hid], 'duplicate actual queue entry')
            self.queued[hid][pid] = dict(self.patients[pid]['demand'])
        elif kind == 'arrival_unable':
            _require(pid in self.queued.get(hid, {}), 'failed arrival lacks queued demand')
            self.queued[hid].pop(pid)
        elif kind == 'treatment_start':
            _require(pid in self.queued.get(hid, {}), 'treatment starts without queued demand')
            available = self.resources(hid, self.queued[hid][pid], at)
            _require(all(values['capacity'] - values['used'] >= self.queued[hid][pid][resource]
                         for resource, values in available.items())
                     and self.patients[pid]['capabilities'] <= self.capabilities[hid],
                     'treatment exceeds actual physical capacity or capability')
            demand = self.queued[hid].pop(pid)
            self.active[hid].append((at + self.patients[pid]['duration'], demand))


def _observation_view(observations, hid, resource, now, committed, queued):
    """Independent source-time freshness and reservation arithmetic."""
    observation = observations.get((hid, resource))
    if observation is None:
        return dict(capacity=None, used=None, committed=committed, queued=queued, free=None,
            observed_at=None, received_at=None, sequence=None, source=None,
            status='unknown', age_minutes=None)
    _require(observation['received_at'] <= now, 'resource snapshot uses future receipt')
    age = now - observation['observed_at']
    known = observation['capacity'] is not None and observation['used'] is not None
    return dict(observation, committed=committed, queued=queued,
        free=observation['capacity'] - observation['used'] - committed - queued if known else None,
        status=('stale' if age > STALE_AFTER else 'fresh') if known else 'unknown', age_minutes=age)


def _apply_observation(observations, event):
    """Validate a whole packet before any newest-source replacements."""
    at = _number(event['t'], 'report time')
    observed = _number(event['observed_at'], 'source timestamp', negative=True)
    received = _number(event['received_at'], 'received timestamp')
    _require(observed <= received <= at, 'report source/receipt is in the future')
    sequence = base._integer(event['packet_sequence'], 'invalid report packet sequence')
    _require(event['source'] in ('initial', 'feed', 'confirmation'), 'unknown report source')
    _require(isinstance(event['resources'], dict), 'invalid resource report payload')
    incoming = {}
    for resource, payload in event['resources'].items():
        _require(isinstance(resource, str) and resource, 'invalid report resource identifier')
        if payload is None:
            capacity = used = None
        else:
            _require(isinstance(payload, dict) and set(payload) == {'capacity', 'used'},
                     'resource packet requires capacity/used or explicit unknown')
            capacity = _quantity(payload['capacity'], 'reported capacity', unknown=True)
            used = _quantity(payload['used'], 'reported occupancy', unknown=True)
        incoming[event['hospital'], resource] = dict(capacity=capacity, used=used,
            observed_at=observed, received_at=received, sequence=sequence, source=event['source'])
    changed = False
    for key, value in incoming.items():
        previous = observations.get(key)
        if previous is None or (observed, sequence) > (previous['observed_at'], previous['sequence']):
            observations[key] = value
            changed = True
    _require(type(event['applied']) is bool and event['applied'] == changed,
             'report applied flag differs from newest-source replacement')
    return changed


def _views(mode, observations, truth, hid, resources, now):
    physical = truth.resources(hid, resources, now)
    if mode == 'perfect':
        return {resource: dict(values, observed_at=now, received_at=now, sequence=None,
            source='perfect_reference', status='fresh', age_minutes=0)
            for resource, values in physical.items()}
    return {resource: _observation_view(observations, hid, resource, now,
        values['committed'], values['queued']) for resource, values in physical.items()}


def _check_reason(views, demand):
    if any(view['status'] == 'fresh' and view['free'] < demand[resource]
           for resource, view in views.items()):
        return 'FRESH_SHORTAGE'
    if any(view['status'] == 'unknown' for view in views.values()):
        return 'UNKNOWN'
    if any(view['status'] == 'stale' for view in views.values()):
        return 'STALE'
    return None


def _exogenous_contract(scenario, events):
    """Resource losses/restores and direct arrivals must match fixed inputs."""
    capacities = {h['id']: dict(h.get('capacity', {})) for h in scenario['hospitals']}
    end = scenario.get('max_minutes', 180)
    restores, expected, external = [], Counter(), Counter()
    exogenous = scenario.get('exogenous_events', [])
    for index, event in enumerate(exogenous):
        if event['type'] == 'external_arrival' and event['t'] <= end:
            external[event['t'], event['hospital'], 'EXTERNAL-' + str(index)] += 1
    for at in range(end + 1):
        for stop, hid, resource in [item for item in restores if item[0] == at]:
            capacities[hid][resource] = capacities[hid].get(resource, 0) + 1
            expected[at, 'resource_restored', hid, resource, None] += 1
        for event in exogenous:
            if event['type'] != 'resource_unavailable' or event['t'] != at:
                continue
            hid, resource = event['hospital'], event['resource']
            _require(hid in capacities, 'exogenous resource event has unknown hospital')
            stop = base._integer(event['until'], 'invalid resource restoration time', minimum=at + 1)
            if capacities[hid].get(resource, 0) > 0:
                capacities[hid][resource] -= 1
                restores.append((stop, hid, resource))
                expected[at, 'resource_unavailable', hid, resource, stop] += 1
    actual = Counter((event['t'], event['type'], event.get('hospital'), event.get('resource'),
                      event.get('until') if event['type'] == 'resource_unavailable' else None)
                     for event in events if event['type'] in ('resource_unavailable', 'resource_restored'))
    actual_external = Counter((event['t'], event.get('hospital'), event.get('patient'))
                              for event in events if event['type'] == 'external_arrival')
    _require(actual == expected, 'resource loss/restoration events differ from fixed exogenous scenario')
    _require(actual_external == external, 'external arrival events differ from fixed exogenous scenario')


def _initial_reports(scenario):
    configured = scenario.get('observation_config', {}).get('initial_reports')
    if configured is not None:
        return configured
    result = []
    for hospital in scenario['hospitals']:
        resources = {resource: dict(capacity=capacity, used=sum(
            item['demand'].get(resource, 0) for item in hospital.get('initial_occupancy', [])))
            for resource, capacity in sorted(hospital.get('capacity', {}).items())}
        result.append(dict(hospital=hospital['id'], observed_at=0, resources=resources))
    return result


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
            if event['source'] != 'feed':
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
            elif event['source'] == 'feed':
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


def _model_evidence(scenario, branch, decision, snapshot):
    at = decision['t']
    mode = branch['information_mode']
    hospitals = {row['id']: row for row in decision['inputs']['hospitals']}
    needed = {}
    for patient in decision['inputs']['patients']:
        spec = next(p for p in scenario['patients'] if p['id'] == patient['id'])
        _require(patient['required_capabilities'] == sorted(set(spec.get('required_capabilities', []))),
                 'model patient capabilities differ from currently declared requirements')
        for hid in patient['options']:
            needed.setdefault(hid, set()).update(patient['demand'])
            check = snapshot['checks'].get((patient['id'], hid, 'allocation'))
            _require(check is not None and check['t'] == at and check['eligible'],
                     'allocation model edge lacks current eligible information check')
            acceptance = snapshot['accepts'].get((patient['id'], hid))
            _require(acceptance is not None and acceptance['t'] == patient['accepted_at'][hid]
                     and snapshot['offers'].get((patient['id'], hid)) == patient['valid_offer_expiries'][hid],
                     'model uses an acceptance not received before its snapshot')
    _require(set(hospitals) == set(needed) and set(decision['resource_evidence']) == set(needed),
             'model/evidence hospital set differs from supplied current offers')
    expected_evidence = {}
    for hid, resources in needed.items():
        views = _views(mode, snapshot['observations'], snapshot['truth'], hid, resources, at)
        _require(all(view['status'] == 'fresh' and view['free'] is not None for view in views.values()),
                 'unknown or stale resource entered an allocation model')
        expected_evidence[hid] = views
        h = hospitals[hid]
        expected_caps = next(h for h in scenario['hospitals'] if h['id'] == hid).get('capabilities', [])
        _require(h['capabilities'] == sorted(set(expected_caps)),
                 'model hospital capabilities differ from declared physical capabilities')
        for key in ('capacity', 'used', 'committed', 'queued'):
            _require(h[key] == {r: view[key] for r, view in views.items()},
                     'model resource ledger differs from delivered observation evidence: ' + key)
    _require(base._same(decision['resource_evidence'], expected_evidence),
             'decision resource evidence differs from newest received source')


def _objective_dict(objective):
    return dict(unassigned_priority_weight=objective[0], priority_weighted_travel_minutes=objective[1])


def audit_branch(scenario, branch, *, exact_limit=20000):
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


def information_metrics(branch):
    events = branch['events']
    return dict(information_wait_checks=sum(e['type'] == 'information_wait' for e in events),
        resource_confirmation_requests=sum(e['type'] == 'resource_confirmation_request' for e in events),
        confirmation_rejections=sum(e['type'] == 'confirmation_rejected' for e in events),
        ignored_resource_reports=sum(e['type'] == 'resource_report_received' and not e['applied'] for e in events),
        unknown_allocation_checks=sum(e['type'] == 'information_check' and e.get('purpose') == 'allocation'
                                     and e.get('reason') == 'UNKNOWN' for e in events),
        stale_allocation_checks=sum(e['type'] == 'information_check' and e.get('purpose') == 'allocation'
                                   and e.get('reason') == 'STALE' for e in events))


def _settings(report):
    settings = report['settings']
    _require(base._same({k: v for k, v in settings.items() if k != 'policies'}, FIXED_SETTINGS),
             'fixed information experiment settings differ')
    policies = settings['policies']
    _require(isinstance(policies, list) and [p.get('id') for p in policies] == POLICY_IDS,
             'comparison requires six canonical perfect/observed R1/H1/C1 branches')
    for policy in policies:
        mode, code = policy['id'].split('_')
        fields = {'id', 'label', 'mode', 'rule', 'k', 'wait', 'notify'}
        fields.update({'time_limit_seconds', 'max_moves'} if code == 'H1' else
                      {'time_limit_seconds', 'seed', 'require_backend'} if code == 'C1' else set())
        _require(set(policy) == fields and policy['mode'] == mode and policy['rule'] == code,
                 'configured policy field/mode/rule mismatch')
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


def _branch_policy(branch, spec):
    policy = branch['policy']
    _require(branch['information_mode'] == spec['mode'] and policy['rule'] == spec['rule']
             and policy['name'] == branch['id'], 'branch identity/mode/rule differs from configured policy')
    defaults = dict(k=3, wait=2, notify=True, near_minutes=None, expand_after=None,
        inquiry_ranking='travel', load_weight=0, load_window=6, adaptive_inquiry=None, observed_inquiry=None)
    for key, value in defaults.items():
        _require(policy.get(key) == value, 'reported inquiry setting differs: ' + key)
    _require(policy.get('priority_order', 'none') == 'none', 'extra priority inquiry policy')
    if spec['rule'] == 'C1':
        expected = dict(backend='ortools_cp_sat', time_limit_seconds=.05,
                        seed=0, require_backend=True, num_search_workers=1)
        _require(base._same(policy.get('solver'), expected), 'reported solver configuration differs')


def _audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    _require(report.get('kind') == 'partial_information_comparison'
             and report.get('experiment_id') == EXPERIMENT_ID and report.get('synthetic') is True,
             'wrong information report identity or synthetic scope')
    _require(report['phase'] in ('development', 'validation'), 'invalid report phase')
    _require(isinstance(report['source_sha256'], dict) and report['source_sha256'], 'missing source manifest')
    if expected_source_sha256 is not None:
        _require(report['source_sha256'] == expected_source_sha256, 'source hashes differ from expected manifest')
    _require(isinstance(report['runtime_versions'], dict) and report['runtime_versions'], 'missing runtime manifest')
    seeds, families = report['seeds'], report['families']
    _require(seeds and len(set(seeds)) == len(seeds) and
             all(type(seed) is int and seed >= 0 for seed in seeds), 'invalid or duplicated phase seeds')
    _require(families and len(set(families)) == len(families)
             and all(family in FAMILIES for family in families), 'unknown or duplicated information families')
    if lock is not None:
        _require(lock.get('kind') == 'partial_information_lock' and lock.get('experiment_id') == EXPERIMENT_ID,
                 'wrong development lock identity')
        for key in ('source_sha256', 'runtime_versions', 'settings', 'families'):
            _require(report[key] == lock[key], 'report differs from lock: ' + key)
        _require(lock['development_seeds'] == DEVELOPMENT_SEEDS and lock['validation_seeds'] == VALIDATION_SEEDS
                 and lock['families'] == list(FAMILIES), 'lock differs from preregistered information seeds/families')
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
                 'scenario lacks synthetic information comparison identity')
        branches = case['branches']
        _require([branch['id'] for branch in branches] == POLICY_IDS, 'branch output order differs')
        offset = (seeds.index(scenario['seed']) + families.index(scenario['family'])) % len(POLICY_IDS)
        _require(case['execution_order'] == POLICY_IDS[offset:] + POLICY_IDS[:offset],
                 'execution order differs from prescribed rotation')
        for name in ('summaries', 'information_metrics', 'diagnostics'):
            _require(set(case[name]) == set(POLICY_IDS), 'case branch IDs mismatch: ' + name)
        _require(set(case['paired_vs_perfect']) == {'R1', 'H1', 'C1'}
                 and set(case['paired_arrival_details']) == {'R1', 'H1', 'C1'}, 'paired comparison rules mismatch')
        by_id = {}
        for branch in branches:
            code = branch['id']
            by_id[code] = branch
            _require(branch.get('comparison_id') == EXPERIMENT_ID
                     and branch.get('source_experiment') == 'partial_information_comparison',
                     'branch lacks information comparison instrumentation')
            _branch_policy(branch, specs[code])
            checked = audit_branch(scenario, branch, exact_limit=exact_limit)
            _require(base._same(case['summaries'][code], checked['metrics']), 'raw full-flow summary mismatch: ' + code)
            _require(base._same(case['information_metrics'][code], checked['information']),
                     'raw information metric mismatch: ' + code)
            _require(base._same(case['diagnostics'][code], _diagnostics(scenario, branch)),
                     'raw diagnostic mismatch: ' + code)
            totals.update(checked['counts'])
            totals['branches'] += 1
        for rule in ('R1', 'H1', 'C1'):
            left, right = by_id['perfect_' + rule], by_id['observed_' + rule]
            paired = base.paired_common_metrics(scenario, left, right)
            expected = {name: {k: v for k, v in row.items() if k not in ('patient_ids', 'total_difference_minutes')}
                        for name, row in paired.items()}
            _require(base._same(case['paired_vs_perfect'][rule], expected), 'raw paired common-patient mismatch: ' + rule)
            _require(base._same(case['paired_arrival_details'][rule], _paired_tails(scenario, left, right)),
                     'raw paired arrival tail mismatch: ' + rule)
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
                                for case in cases) for field in AGGREGATE_FIELDS})
                _require(base._same(saved, expected), 'aggregate differs from raw case means')
    return dict(totals, experiment_id=EXPERIMENT_ID,
        provenance_checked=expected_source_sha256 is not None or lock is not None,
        assignment_constraint_violations=0, actual_optimality_claimed=False,
        proof_scope='verified_reported_model_only_not_actual_gate_or_whole_episode')


def audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    """Audit saved JSON without executing the simulator, store or assignment rule."""
    _require(type(exact_limit) is int and exact_limit >= 0, 'invalid exact enumeration limit')
    try:
        return _audit_report(report, expected_source_sha256=expected_source_sha256,
                             lock=lock, exact_limit=exact_limit)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise PartialInformationAuditError('malformed partial information artifact') from error


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
