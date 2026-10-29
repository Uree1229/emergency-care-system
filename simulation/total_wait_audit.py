"""Independent raw audit of current-resource updates and event-driven retry.

The frozen raw-stream and model verifier are copied locally only to support a
separate publication marker on the existing feed source. No simulator, assignment implementation, runner, scenario builder,
observation store, OR-Tools, future release schedule or treatment duration enters
policy verification. Physical truth is used only for independent source/gate
checks and evaluation after the experiment.
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
from . import partial_information_audit as information
from . import responsive_confirmation_audit as responsive
from . import stable_c1_audit as stable
from .h1_online_audit import audit_h1_branch, _diagnostics, _finite, _paired_tails

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = 'total_wait_20261015'
POLICY_IDS = stable.POLICY_IDS + ['review_stable_C1', 'updates_stable_C1', 'total_wait_C1']
COMPARISON_PAIRS = stable.COMPARISON_PAIRS + (
    ('review_stable_C1_vs_stable', 'stable_responsive_C1', 'review_stable_C1'),
    ('updates_stable_C1_vs_stable', 'stable_responsive_C1', 'updates_stable_C1'),
    ('total_wait_C1_vs_stable', 'stable_responsive_C1', 'total_wait_C1'),
    ('total_wait_C1_vs_updates', 'updates_stable_C1', 'total_wait_C1'),
    ('total_wait_C1_vs_review', 'review_stable_C1', 'total_wait_C1'))
DEVELOPMENT_SEEDS = [25101, 25102, 25103]
VALIDATION_SEEDS = [26101, 26102, 26103, 26104, 26105, 26106]
FAMILIES = stable.FAMILIES + ('release_long_retry', 'release_before_offer_expiry',
    'release_notification_delay', 'periodic_release_long_retry',
    'intrinsic_single_hospital_queue')
TotalWaitAuditError = base.OnlineAllocationAuditError
_number = information._number
_quantity = information._quantity
_indexed_events = information._indexed_events
_TruthLedger = information._TruthLedger
_views = information._views
_check_reason = information._check_reason
_exogenous_contract = information._exogenous_contract
_initial_reports = information._initial_reports
_model_evidence = information._model_evidence
_objective_dict = information._objective_dict
information_metrics = information.information_metrics
guard_metrics = responsive.guard_metrics
responsiveness_metrics = responsive.responsiveness_metrics
stability_metrics = stable.stability_metrics
_branch_guard_counts = responsive._branch_guard_counts
_apply_observation = information._apply_observation
audit_guard = responsive.audit_guard
audit_responsiveness = responsive.audit_responsiveness


def _require(condition, message):
    base._require(condition, message)


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



def _update_config(scenario):
    config = scenario.get('resource_update_config')
    _require(isinstance(config, dict) and set(config) == {'delay_minutes', 'missing_resources', 'silent'},
             'invalid current-change publication contract')
    base._integer(config['delay_minutes'], 'invalid current-change notification delay')
    _require(type(config['silent']) is bool, 'invalid current-change silence flag')
    missing = config['missing_resources']
    _require(isinstance(missing, list) and missing == sorted(set(missing))
             and all(isinstance(resource, str) and resource for resource in missing),
             'invalid current-change missing-resource mask')
    observation = scenario.get('observation_config', {})
    expected = sorted(set(observation.get('feed_missing_resources', ()))
                      | set(observation.get('confirmation_missing_resources', ())))
    _require(missing == expected and config['silent'] == observation.get('confirmation_silent', False),
             'new source bypasses permanent masks or hospital silence')
    return config


def _physical_snapshot(truth, at):
    return {hid: {resource: {'capacity': row['capacity'], 'used': row['used']}
                  for resource, row in truth.resources(hid, truth.capacities[hid], at).items()}
            for hid in truth.capacities}


def audit_publications(scenario, branch):
    """Rebuild current changes independently; no future release time is sent.

    Source cycles fix the observation point within a minute. The private truth
    ledger supplies the expected *current changed fields* at that point only.
    Delivery is separately checked against delay, mask and newest-source rules.
    """
    enabled = branch['resource_updates_enabled']
    _require(type(enabled) is bool, 'missing explicit current-change mode')
    _require(not enabled or branch['information_mode'] == 'observed',
             'current-change publication outside observed mode')
    config = _update_config(scenario)
    truth = _TruthLedger(scenario)
    previous = _physical_snapshot(truth, 0)
    ordered = _indexed_events(branch)
    captures, delivered, cycles, expected_captures = {}, set(), [], {}
    for event in ordered:
        kind, at = event['type'], event['t']
        if kind == 'resource_update_cycle':
            _require(enabled, 'publication cycle on disabled branch')
            _require(not expected_captures, 'previous publication cycle omitted changed hospital capture')
            phase = event['phase']
            _require(phase in ('pre_queries', 'after_treatments'), 'unknown current-change sampling phase')
            _require(set(event) == {'t','type','event_sequence','phase'},
                     'current-change cycle carries unknown source fields')
            cycles.append((at, phase))
            current = _physical_snapshot(truth, at)
            changed = {hid: {resource: None if resource in config['missing_resources'] else value
                            for resource, value in resources.items()
                            if value != previous[hid].get(resource)}
                       for hid, resources in current.items()}
            expected_captures = {} if config['silent'] else {
                hid: resources for hid, resources in changed.items() if resources}
            previous = current
        elif kind == 'resource_update_captured':
            _require(enabled and event['hospital'] in expected_captures,
                     'unexpected publication without a current changed field')
            hid = event['hospital']
            identifier = base._integer(event['publication_id'], 'invalid publication identifier')
            _require(identifier == len(captures),
                     'duplicated or nonsequential publication identifier')
            _require(event['observed_at'] == at
                     and event['receive_at'] == at + config['delay_minutes'],
                     'publication source/receipt differs from current time and delay')
            _require(base._same(event['resources'], expected_captures.pop(hid)),
                     'publication payload differs from current changed fields or permanent mask')
            _require(set(event) == {'t', 'type', 'event_sequence', 'hospital', 'publication_id',
                     'resources', 'observed_at', 'receive_at'},
                     'publication carries future schedule or unknown source fields')
            captures[identifier] = event
        elif kind == 'resource_report_received' and 'publication_id' in event:
            identifier = base._integer(event['publication_id'], 'invalid received publication identifier')
            capture = captures.get(identifier)
            _require(enabled and event['source'] == 'feed' and capture is not None
                     and identifier not in delivered
                     and capture['event_sequence'] < event['event_sequence'],
                     'publication receipt lacks unique earlier current source')
            _require(event['hospital'] == capture['hospital']
                     and event['observed_at'] == capture['observed_at']
                     and event['received_at'] == at == capture['receive_at']
                     and base._same(event['resources'], capture['resources']),
                     'received publication differs from source payload or delivery time')
            delivered.add(identifier)
        truth.apply(event)
    _require(not expected_captures, 'last current-change cycle omitted changed hospital capture')
    end = scenario.get('max_minutes', 180)
    expected_cycles = [(at, phase) for at in range(end + 1)
                       for phase in ('pre_queries', 'after_treatments')] if enabled else []
    _require(cycles == expected_cycles, 'missing/extra current-change sampling cycle')
    _require(delivered == {identifier for identifier, capture in captures.items()
                           if capture['receive_at'] <= end},
             'missing/extra due current-change publication delivery')
    return dict(resource_update_cycles=len(cycles), resource_update_captures=len(captures),
        resource_update_receipts=len(delivered),
        ignored_resource_updates=sum(e['type'] == 'resource_report_received'
                                     and 'publication_id' in e and not e['applied'] for e in ordered))



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
    workflow = responsive._KnownWorkflow(scenario)
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


def audit_branch(scenario, branch, *, exact_limit=20000):
    checked = _audit_information_branch(scenario, branch, exact_limit=exact_limit)
    checked['counts'].update(audit_guard(scenario, branch))
    checked['counts'].update(audit_responsiveness(scenario, branch))
    checked['counts'].update(stable.audit_stability(scenario, branch))
    publications = audit_publications(scenario, branch)
    reviews = audit_event_review(scenario, branch)
    checked['counts'].update(publications)
    checked['counts'].update(reviews)
    updates = update_metrics(branch)
    for key, value in updates.items():
        _require(base._same(branch['metrics'][key], value), 'raw update/review metric mismatch: ' + key)
    for flag, field in (('resource_updates_enabled', 'update_wall_seconds'),
                        ('event_review_enabled', 'event_review_wall_seconds')):
        _finite(branch['metrics'][field], field)
        _require(branch[flag] or branch['metrics'][field] == 0,
                 'disabled intervention reports computation time')
    checked.update(guard=guard_metrics(branch), responsiveness=responsiveness_metrics(branch),
                   stability=stability_metrics(branch), waiting=waiting_metrics(scenario, branch),
                   updates=updates)
    return checked


def update_metrics(branch):
    events = branch['events']
    return dict(resource_update_cycles=sum(e['type'] == 'resource_update_cycle' for e in events),
        resource_publications=sum(e['type'] == 'resource_update_captured' for e in events),
        resource_updates_received=sum(e['type'] == 'resource_report_received' and 'publication_id' in e for e in events),
        event_review_cycles=sum(e['type'] == 'event_review_cycle' for e in events),
        event_review_requests=sum(e['type'] == 'resource_event_review' for e in events),
        update_wall_seconds=branch['metrics']['update_wall_seconds'],
        event_review_wall_seconds=branch['metrics']['event_review_wall_seconds'])


def _audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    _require(report.get('kind') == 'total_wait_comparison'
             and report.get('experiment_id') == EXPERIMENT_ID and report.get('synthetic') is True,
             'wrong total-wait report identity or synthetic scope')
    _require(report['phase'] in ('development', 'validation'), 'invalid report phase')
    _require(isinstance(report['source_sha256'], dict) and report['source_sha256'], 'missing source manifest')
    if expected_source_sha256 is not None:
        _require(report['source_sha256'] == expected_source_sha256, 'source hashes differ from expected manifest')
    _require(isinstance(report['runtime_versions'], dict) and report['runtime_versions'], 'missing runtime manifest')
    seeds, families = report['seeds'], report['families']
    _require(seeds and len(set(seeds)) == len(seeds)
             and all(type(seed) is int and seed >= 0 for seed in seeds), 'invalid or duplicated phase seeds')
    _require(families and len(set(families)) == len(families)
             and all(family in FAMILIES for family in families), 'unknown or duplicated total-wait families')
    if lock is not None:
        _require(lock.get('kind') == 'total_wait_lock' and lock.get('experiment_id') == EXPERIMENT_ID,
                 'wrong development lock identity')
        for key in ('source_sha256', 'runtime_versions', 'settings', 'families'):
            _require(report[key] == lock[key], 'report differs from lock: ' + key)
        _require(lock['development_seeds'] == DEVELOPMENT_SEEDS
                 and lock['validation_seeds'] == VALIDATION_SEEDS
                 and lock['families'] == list(FAMILIES), 'lock differs from preregistered total-wait seeds/families')
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
                 'scenario lacks synthetic total-wait comparison identity')
        branches = case['branches']
        _require([branch['id'] for branch in branches] == POLICY_IDS, 'branch output order differs')
        offset = (seeds.index(scenario['seed']) + families.index(scenario['family'])) % len(POLICY_IDS)
        _require(case['execution_order'] == POLICY_IDS[offset:] + POLICY_IDS[:offset],
                 'execution order differs from prescribed rotation')
        for name in ('summaries', 'information_metrics', 'guard_metrics',
                     'responsiveness_metrics', 'stability_metrics', 'waiting_metrics', 'update_metrics', 'diagnostics'):
            _require(set(case[name]) == set(POLICY_IDS), 'case branch IDs mismatch: ' + name)
        _require(set(case['paired_comparisons']) == {name for name, _, _ in COMPARISON_PAIRS},
                 'total-wait paired comparison IDs mismatch')
        by_id = {}
        for branch in branches:
            code = branch['id']
            by_id[code] = branch
            _require(branch.get('comparison_id') == EXPERIMENT_ID
                     and branch.get('source_experiment') == 'total_wait_comparison',
                     'branch lacks total-wait comparison instrumentation')
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
                     and branch['event_review_enabled'] == spec['event_review'],
                     'branch confirmation/stability flags/grace differ from configured policy')
            checked = audit_branch(scenario, branch, exact_limit=exact_limit)
            for saved, actual in (('summaries', 'metrics'), ('information_metrics', 'information'),
                                  ('guard_metrics', 'guard'), ('responsiveness_metrics', 'responsiveness'),
                                  ('stability_metrics', 'stability'), ('waiting_metrics', 'waiting'),
                                  ('update_metrics', 'updates')):
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
                     'raw paired total-wait/common-patient mismatch: ' + name)
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
        source_contract='current capacity/used delta fields, masks and silence retained, no future release times',
        waiting_scope='all patients SEARCH+QUEUED+TRANSFER_WAIT through fixed horizon; transit excluded',
        proof_scope='verified_reported_model_only_not_canonical_tie_actual_gate_or_whole_episode')


def audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    """Audit saved artifacts without executing the experiment or allocation rule."""
    _require(type(exact_limit) is int and exact_limit >= 0, 'invalid exact enumeration limit')
    try:
        return _audit_report(report, expected_source_sha256=expected_source_sha256,
                             lock=lock, exact_limit=exact_limit)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise TotalWaitAuditError('malformed total-wait artifact') from error


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



FIXED_SETTINGS = deepcopy(stable.FIXED_SETTINGS)
FIXED_SETTINGS.update(
    execution_order='rotate twenty branches by seed index plus family index; canonical output order',
    compute_time='allocation, protection, confirmation, resource sampling/delivery and event review measured separately; not added to simulation clock',
    tuning='fixed current-change publications and received-event retry before new development and validation')
FIXED_SETTINGS['resource_updates'] = dict(
    source='synthetic hospital present capacity/used changes only; no future release or treatment-duration input',
    capture='sample pre_queries and after_treatments once per minute; publish changed fields only',
    delivery='configured nonnegative integer delay; preserve union of persistent missing masks and hospital silence',
    ordering='publication ID at capture, packet sequence at receipt; source observation clock unchanged')
FIXED_SETTINGS['event_review'] = dict(
    trigger='latest applied report received this minute; SEARCH with no asks or offers; fresh sufficient required resources',
    selection='priority/request/id patient order; nearest compatible unfailed reported hospital then hospital ID',
    action='reset retry for that hospital only; send a new patient inquiry; acceptance and final physical gate remain mandatory',
    clock='pre_queries and after_treatments; once per patient and report event sequence')
FIXED_SETTINGS['waiting_evaluation'] = dict(
    cohort='all requested patients including untreated at fixed horizon',
    waiting='integral of SEARCH, QUEUED and TRANSFER_WAIT; transit and treatment excluded',
    treatment='request-to-treatment censored at observation end if not started; report untreated count and individual details',
    factors='same old reports with/without review; added publications with/without review')


def _settings(report):
    settings = report['settings']
    _require(base._same({key:value for key,value in settings.items() if key!='policies'}, FIXED_SETTINGS),
             'fixed total-wait experiment settings differ')
    policies = settings['policies']
    _require(isinstance(policies,list) and [p.get('id') for p in policies]==POLICY_IDS,
             'comparison requires twenty canonical baseline/factor branches')
    projected = deepcopy(stable.FIXED_SETTINGS)
    originals = []
    normalized = {}
    for policy in policies:
        _require(type(policy.get('resource_updates')) is bool and type(policy.get('event_review')) is bool,
                 'explicit update/review policy flags required')
        code = policy['id']
        expected_updates = code in ('updates_stable_C1','total_wait_C1')
        expected_review = code in ('review_stable_C1','total_wait_C1')
        _require(policy['resource_updates']==expected_updates and policy['event_review']==expected_review,
                 'policy update/review ablation differs')
        row=deepcopy(policy)
        row.pop('resource_updates')
        row.pop('event_review')
        normalized[code]=row
        if code in stable.POLICY_IDS:
            originals.append(row)
    projected['policies']=originals
    stable._settings(dict(settings=projected))
    for code in POLICY_IDS[len(stable.POLICY_IDS):]:
        branch_projection=deepcopy(projected)
        row=deepcopy(normalized[code])
        row['id']='stable_responsive_C1'
        branch_projection['policies'][-1]=row
        stable._settings(dict(settings=branch_projection))
    return {policy['id']:policy for policy in policies}


if __name__ == '__main__':
    main()


