"""Independent direct raw audit of explicitly bounded bed-shortage rechecks.

Frozen physical, FIFO, allocation and observation contracts remain in force.
Local copies extend only the information-check purpose and inquiry provenance;
raw branches are never projected, patched or executed through an engine/solver.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import math
from . import deadline_safe_audit as prior

base = prior.base
joint = prior.joint
scarce = prior.scarce
recovery = prior.recovery
information = prior.information
_TruthLedger = joint._TruthLedger
_require = prior._require
_indexed_events = prior._indexed_events
_end = prior._end
_views = prior._views
_LateWorkflow = prior._LateWorkflow
_condition = prior._condition
_is_callback_accept = prior._is_callback_accept
_position = joint._position
_reply_environment = joint._reply_environment
_selector_reproduce = joint._selector_reproduce
CLOCK_ORIGIN_SECONDS = joint.CLOCK_ORIGIN_SECONDS
_clock = joint._clock
_number = joint._number
_initial_reports = joint._initial_reports
_apply_observation = joint._apply_observation
_exogenous_contract = joint._exogenous_contract
_check_reason = joint._check_reason
_model_evidence = joint._model_evidence
_objective_dict = joint._objective_dict
information_metrics = joint.information_metrics
raw_branch_metrics = joint.raw_branch_metrics
_problem = joint._problem
audit_h1_branch = joint.audit_h1_branch
ShortageProbeAuditError = prior.DeadlineSafeAuditError
EXPERIMENT_ID = 'shortage_probe_20261017'
SHORTAGE_PROBE_SPEC = dict(maximum_probes_per_patient=1, phase='before_joint_batch',
    initial_episode_only=True, never_committed_only=True, minimum_contact_age_minutes=.5,
    maximum_extra_travel_minutes=2, maximum_open_inquiries=3, maximum_episode_contacts=10,
    reported_bed_shortage_only=True, all_required_resources_fresh=True,
    other_required_resources_sufficient=True, reported_bed_capacity_sufficient=True,
    protect_other_search_pending_and_offers=True, original_selector_unchanged=True,
    actual_fifo_inquiry_required=True, actual_reply_required=True,
    fresh_allocation_gate_unchanged=True, extra_resource_change_publications=False,
    future_release_or_review_schedule_visible=False,
    optional_probe_never_blocks_an_existing_usable_offer=True)

_early_snapshot = joint._early_snapshot
_early_expected = joint._early_expected
_finite = joint._finite
responsive = joint.responsive


class _ProbeWorkflow(_LateWorkflow):
    """Explicit extra readiness leaves every original pending handle intact."""
    def apply(self, event):
        if event['type'] == 'shortage_probe_nonblocking_ready':
            self.promoted.add((event['patient'], event['t']))
        super().apply(event)


_EarlyWorkflow = _ProbeWorkflow


def _difference(left, right, path='snapshot'):
    if type(left) is not type(right):
        return f'{path}: {left!r} != {right!r}'
    if isinstance(left, dict):
        if set(left) != set(right): return f'{path}: different keys'
        for key in left:
            if not base._same(left[key], right[key]): return _difference(left[key], right[key], path + '/' + key)
    elif isinstance(left, list):
        if len(left) != len(right): return f'{path}: different lengths {len(left)} != {len(right)}'
        for index, (a, b) in enumerate(zip(left, right)):
            if not base._same(a, b): return _difference(a, b, path + '/' + str(index))
    return f'{path}: {left!r} != {right!r}'


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
            _require(purpose in ('inquiry', 'allocation', 'shortage_probe'), 'invalid information-check purpose')
            demand = patients[pid].get('resource_demands', {'ed_bed': 1})
            expected = _views(mode, observations, truth, hid, demand, at)
            _require(base._same(event['resources'], expected),
                     'information check differs from delivered reports or current reservations')
            reason = _check_reason(expected, demand)
            eligible = reason != 'FRESH_SHORTAGE' if purpose == 'inquiry' else reason is None
            if purpose == 'shortage_probe':
                _require(reason == 'FRESH_SHORTAGE', 'shortage probe lacks original current fresh shortage')
                eligible, reason = True, 'REPORTED_BED_SHORTAGE_RECHECK'
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
    supplemental_asks, probe_asks = set(), set()
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
                _require(sum(patient == pid for patient, _ in active) <= 3, 'patient concurrent inquiry limit exceeded')
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
        elif kind == 'shortage_probe_review':
            record = branch['shortage_probe_records'][event['record_index']]
            following = indexed.get(seq + 2)
            actual = [following] if following is not None and following['type'] == 'ask' and following.get('shortage_probe_review_event_sequence') == seq else []
            _require([row['hospital'] for row in actual] == record['result']['send'],
                     'probe review differs from its uniquely sourced actual inquiry')
            probe_asks.update(row['event_sequence'] for row in actual)
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
                and e['t'] == at and e['type'] in ('scarce_supplement_review', 'shortage_probe_review') and e.get('patient') == pid), default=float('inf'))
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
        _require(not covered_asks.intersection(supplemental_asks | probe_asks) and not supplemental_asks.intersection(probe_asks) and covered_asks | supplemental_asks | probe_asks ==
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

def _probe_patient_view(workflow, requests, handles, accepted, pid, now):
    return dict(id=pid, state=workflow.states[pid], request_t=requests[pid]['t'],
        request_event_sequence=requests[pid]['event_sequence'],
        pending_inquiries=[deepcopy(handle) for (patient, _), handle in handles.items() if patient == pid],
        valid_offers=[dict(hospital=hid, accepted_at=workflow.accepted[pid, hid], expires_at=expiry,
            accept_event_sequence=accepted[pid, hid]['event_sequence'])
            for (patient, hid), expiry in sorted(workflow.offers.items()) if patient == pid and expiry > now])


def _probe_review_due(snapshot):
    patient, now = snapshot['patient'], snapshot['t']
    return (patient['state'] == 'SEARCH' and patient['first_commit_t'] is None
        and patient['inquiry_episode'] == 0 and patient['inquiry_count'] >= 1
        and bool(patient['pending_inquiries']) and snapshot['probe_count'] == 0
        and patient['first_ask'] is not None
        and now >= patient['first_ask']['submitted_at'] + .5
        and patient['last_contact_at'] is not None and now >= patient['last_contact_at'] + .5
        and patient['episode_contacts'] < 10 and len(patient['pending_inquiries']) < 3)


def _probe_expected(snapshot):
    """Independent public-information rule; the implementation is not imported."""
    now, patient = snapshot['t'], snapshot['patient']
    candidates = snapshot['candidates']
    by_hospital = {candidate['hospital']: candidate for candidate in candidates}
    protected = set()
    for other in snapshot['registered_search_patients']:
        if other['id'] == patient['id']:
            continue
        protected.update(row['hospital'] for row in other['pending_inquiries'] if row['timeout_at'] > now)
        protected.update(row['hospital'] for row in other['valid_offers'] if row['expires_at'] > now)
    result = dict(send=[], reason='NO_ELIGIBLE_SHORTAGE', candidate_checks=[], protected_hospitals=sorted(protected))
    rejection = None
    if patient['state'] != 'SEARCH': rejection = 'STATE'
    elif patient['first_commit_t'] is not None: rejection = 'COMMITTED'
    elif patient['inquiry_episode'] != 0: rejection = 'EPISODE'
    elif patient['first_ask'] is None or patient['inquiry_count'] < 1 or patient['last_contact_at'] is None:
        rejection = 'NO_INITIAL_INQUIRY'
    elif now < patient['first_ask']['submitted_at'] + .5 or now < patient['last_contact_at'] + .5:
        rejection = 'TOO_EARLY'
    elif snapshot['probe_count'] >= 1: rejection = 'ALREADY_PROBED'
    elif patient['episode_contacts'] >= 10: rejection = 'CONTACT_LIMIT'
    elif not patient['pending_inquiries']: rejection = 'NO_PENDING_INQUIRY'
    elif len(patient['pending_inquiries']) >= 3: rejection = 'CONCURRENCY_LIMIT'
    elif any(offer['expires_at'] > now and offer['hospital'] in by_hospital and all(
        view['status'] == 'fresh' and view['free'] is not None and view['free'] >= patient['demand'][resource]
        for resource, view in by_hospital[offer['hospital']]['resources'].items())
        for offer in patient['valid_offers']): rejection = 'USABLE_OFFER'
    if rejection:
        return dict(result, reason=rejection)
    if any(handle['hospital'] not in by_hospital for handle in patient['pending_inquiries']):
        return dict(result, reason='PENDING_NOT_STRUCTURALLY_COMPATIBLE')
    shortest = min(by_hospital[handle['hospital']]['travel_minutes'] for handle in patient['pending_inquiries'])
    own_claims = {handle['hospital'] for handle in patient['pending_inquiries']}
    own_claims.update(offer['hospital'] for offer in patient['valid_offers'] if offer['expires_at'] > now)
    eligible = []
    for candidate in candidates:
        hid, resources = candidate['hospital'], candidate['resources']
        reason = 'RECHECK_ELIGIBLE'
        if hid in patient['tried_hospitals']: reason = 'ALREADY_TRIED'
        elif hid in own_claims: reason = 'PENDING_OR_OFFER'
        elif candidate['travel_minutes'] > shortest + 2: reason = 'TRAVEL_LIMIT'
        elif hid in protected: reason = 'CLAIMED_BY_SEARCH_PATIENT'
        elif patient['demand'].get('ed_bed', 0) <= 0: reason = 'NO_BED_DEMAND'
        elif any(view['status'] == 'unknown' or view['free'] is None for view in resources.values()): reason = 'UNKNOWN_RESOURCE'
        elif any(view['status'] != 'fresh' for view in resources.values()): reason = 'STALE_RESOURCE'
        elif resources['ed_bed']['capacity'] < patient['demand']['ed_bed']: reason = 'INSUFFICIENT_REPORTED_CAPACITY'
        elif resources['ed_bed']['free'] >= patient['demand']['ed_bed']: reason = 'NO_REPORTED_BED_SHORTAGE'
        elif any(view['free'] < patient['demand'][resource] for resource, view in resources.items() if resource != 'ed_bed'):
            reason = 'OTHER_RESOURCE_SHORTAGE'
        result['candidate_checks'].append(dict(hospital=hid, eligible=reason == 'RECHECK_ELIGIBLE', reason=reason))
        if reason == 'RECHECK_ELIGIBLE':
            eligible.append(hid)
    if eligible:
        chosen = min(eligible, key=lambda hid: (by_hospital[hid]['travel_minutes'], hid))
        result.update(send=[chosen], reason='CURRENT_SHORTAGE_PROBE')
    return result


def audit_probes(scenario, branch):
    """Rebuild public probe inputs, every explicit recheck, and its actual ask."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    records = branch['shortage_probe_records']
    _require(isinstance(records, list), 'probe records must be a list')
    workflow = _LateWorkflow(deepcopy(scenario))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    requests, handles, accepted, first_asks, first_commits, last_contacts = {}, {}, {}, {}, {}, {}
    inquiries, contacts, probe_counts = Counter(), Counter(), Counter()
    episodes, tried, used, ask_sequences, check_sequences = {}, {}, set(), set(), set()
    pending_targets, cycles, sent = [], [], 0

    def snapshot(pid, now):
        current = workflow._base_patient(pid)
        point = _position(scenario, branch, pid, now)
        compatible = sorted(hid for hid in current['compatible_hospitals'] if all(
            hospitals[hid].get('capacity', {}).get(resource, 0) >= amount
            for resource, amount in current['demand'].items()))
        patient = _probe_patient_view(workflow, requests, handles, accepted, pid, now)
        patient.update(priority=current['priority'], demand=deepcopy(current['demand']),
            required_capabilities=sorted(current['required_capabilities']),
            inquiry_episode=episodes.get(pid, 0), episode_contacts=contacts[pid, episodes.get(pid, 0)],
            inquiry_count=inquiries[pid], first_commit_t=first_commits.get(pid),
            first_ask=deepcopy(first_asks.get(pid)), tried_hospitals=sorted(tried.get(pid, set())),
            last_contact_at=last_contacts.get(pid))
        candidates = [dict(hospital=hid,
            travel_minutes=recovery._synthetic_minutes(point, hospitals[hid]),
            resources=_views('observed', workflow.observations, workflow.truth, hid,
                             current['demand'], now)) for hid in compatible]
        cohort = [_probe_patient_view(workflow, requests, handles, accepted, other, now)
                  for other in sorted(requests) if workflow.states.get(other) == 'SEARCH']
        for row in cohort:
            row['pending_inquiries'].sort(key=lambda handle: (handle['hospital'], handle['attempt']))
        return dict(t=now, phase='before_joint_batch', patient=patient, candidates=candidates,
                    registered_search_patients=cohort, probe_count=probe_counts[pid])

    for event in events:
        kind, now, sequence = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'request':
            requests[pid] = event
        elif kind == 'ask':
            handle = dict(hospital=hid, attempt=event['attempt'], submitted_at=now,
                timeout_at=event['timeout_at'], ask_event_sequence=sequence)
            first_asks.setdefault(pid, deepcopy(handle))
            handles[pid, hid] = handle
            inquiries[pid] += 1
            episodes[pid] = event['inquiry_episode']
            contacts[pid, event['inquiry_episode']] += 1
            last_contacts[pid] = now
            tried.setdefault(pid, set()).add(hid)
        elif kind in ('accept', 'decline', 'no_response'):
            if not _is_callback_accept(event, indexed):
                handles.pop((pid, hid), None)
            if kind == 'accept':
                accepted[pid, hid] = event
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            if kind == 'commit':
                first_commits.setdefault(pid, now)
            if kind == 'transfer_search':
                episodes[pid] = episodes.get(pid, 0) + 1
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            for handle in event['pending']:
                handles[pid, handle['hospital']] = deepcopy(handle)
        elif kind == 'shortage_probe_cycle':
            _require(not pending_targets and set(event) == {'t', 'type', 'event_sequence'},
                     'previous probe cycle omitted reviews or cycle has unknown fields')
            cycles.append(now)
            pending_targets = [patient for patient in sorted(requests)
                               if _probe_review_due(snapshot(patient, now))]
        elif kind == 'shortage_probe_review':
            _require(pending_targets and pid == pending_targets.pop(0),
                     'probe review is omitted, duplicated or outside the public review preconditions')
            index = event['record_index']
            _require(index == len(used) and index < len(records), 'missing or reordered shortage-probe record')
            used.add(index)
            record = records[index]
            _require(set(event) == {'t', 'type', 'event_sequence', 'patient', 'record_index'}
                     and set(record) == {'snapshot', 'result', 'event_sequence'}
                     and record['event_sequence'] == sequence, 'probe record or marker carries unknown fields')
            expected_snapshot = snapshot(pid, now)
            if not base._same(record['snapshot'], expected_snapshot):
                _require(False, 'probe input differs from public registration, actual inquiries/offers or original shortage reports: '
                         + _difference(record['snapshot'], expected_snapshot))
            expected = _probe_expected(expected_snapshot)
            _require(base._same(record['result'], expected), 'probe decision differs from independent bounded public recheck rule')
            following = indexed.get(sequence + 1)
            if expected['send']:
                chosen = expected['send'][0]
                resources = next(row['resources'] for row in expected_snapshot['candidates'] if row['hospital'] == chosen)
                _require(following is not None and base._same(following, dict(t=now, type='information_check',
                    event_sequence=sequence + 1, patient=pid, hospital=chosen, purpose='shortage_probe',
                    eligible=True, reason='REPORTED_BED_SHORTAGE_RECHECK', resources=resources)),
                    'probe lacks exactly its original fresh-shortage check with explicit new purpose')
                ask = indexed.get(sequence + 2)
                _require(ask is not None and ask['type'] == 'ask' and ask['t'] == now
                    and ask['patient'] == pid and ask['hospital'] == chosen
                    and ask.get('shortage_probe_review_event_sequence') == sequence,
                    'eligible shortage recheck lacks uniquely referenced immediate actual inquiry')
                ask_sequences.add(ask['event_sequence']); check_sequences.add(sequence + 1)
                probe_counts[pid] += 1
                _require(probe_counts[pid] == 1, 'more than one shortage probe per patient')
                sent += 1
            else:
                _require(following is None or following['type'] != 'information_check'
                    or following.get('purpose') != 'shortage_probe', 'ineligible recheck emitted a probe')
        workflow.apply(event)
    _require(not pending_targets and used == set(range(len(records))), 'orphan or omitted shortage-probe record')
    _require(ask_sequences == {row['event_sequence'] for row in events if 'shortage_probe_review_event_sequence' in row},
             'orphan or fabricated shortage-probe inquiry')
    _require(check_sequences == {row['event_sequence'] for row in events
        if row['type'] == 'information_check' and row['purpose'] == 'shortage_probe'},
        'orphan shortage-probe information check')
    _require(cycles == [step / 4 for step in range(int(_end(scenario, branch) * 4) + 1)],
             'missing, extra or reordered ordinary before-batch probe cycle')
    _require(branch['metrics']['shortage_probe_reviews'] == len(records)
             and branch['metrics']['shortage_probe_sent'] == sent, 'probe metrics differ from original raw ledger')
    information._number(branch['metrics']['shortage_probe_wall_seconds'], 'shortage-probe runtime')
    return dict(shortage_probe_cycles=len(cycles), shortage_probe_reviews=len(records), shortage_probe_sent=sent,
                explicit_shortage_checks=len(check_sequences))


def _nonblocking_expected(snapshot):
    patient, now = snapshot['patient'], snapshot['t']
    result = dict(eligible=False, options=[], reason='NO_USABLE_OFFER')
    if patient['state'] != 'SEARCH': return dict(result, reason='STATE')
    if patient['first_commit_t'] is not None: return dict(result, reason='COMMITTED')
    if not patient['pending_inquiries']: return dict(result, reason='NO_PENDING_INQUIRY')
    originals = {row['ask_event_sequence']: row for row in snapshot['probe_asks']}
    if any(originals.get(row['ask_event_sequence']) != row for row in patient['pending_inquiries']):
        return dict(result, reason='ORDINARY_INQUIRY_PENDING')
    candidates = {row['hospital']: row['resources'] for row in snapshot['candidates']}
    options = sorted(offer['hospital'] for offer in patient['valid_offers']
        if offer['expires_at'] > now and offer['hospital'] in candidates and all(
            view['status'] == 'fresh' and view['free'] is not None and view['free'] >= patient['demand'][resource]
            for resource, view in candidates[offer['hospital']].items()))
    return dict(eligible=True, options=options, reason='OPTIONAL_PROBE_NONBLOCKING') if options else result


def audit_nonblocking(scenario, branch):
    """Only real optional work may be excluded from readiness maturity."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    records = branch['shortage_probe_nonblocking_records']
    _require(isinstance(records, list), 'nonblocking records must be a list')
    workflow = _ProbeWorkflow(deepcopy(scenario))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    handles, accepted, first_commits, probe_asks = {}, {}, {}, {}
    used, ready_sequences, used_checks, targets = set(), set(), set(), []

    def snapshot(pid, now):
        row = workflow._base_patient(pid)
        compatible = sorted(hid for hid in row['compatible_hospitals'] if all(
            hospitals[hid].get('capacity', {}).get(resource, 0) >= amount
            for resource, amount in row['demand'].items()))
        offers = [dict(hospital=hid, accepted_at=workflow.accepted[pid, hid], expires_at=expiry,
            accept_event_sequence=accepted[pid, hid]['event_sequence'])
            for (patient, hid), expiry in sorted(workflow.offers.items()) if patient == pid and expiry > now]
        return dict(t=now, patient=dict(id=pid, state=workflow.states[pid], first_commit_t=first_commits.get(pid),
            pending_inquiries=[deepcopy(handle) for (patient, _), handle in handles.items() if patient == pid],
            valid_offers=offers, demand=deepcopy(row['demand'])), probe_asks=deepcopy(probe_asks.get(pid, [])),
            candidates=[dict(hospital=hid, resources=_views('observed', workflow.observations, workflow.truth,
                hid, row['demand'], now)) for hid in compatible])

    for event in events:
        kind, now, sequence = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'ask':
            handle = dict(hospital=hid, attempt=event['attempt'], submitted_at=now,
                timeout_at=event['timeout_at'], ask_event_sequence=sequence)
            handles[pid, hid] = handle
            if 'shortage_probe_review_event_sequence' in event:
                probe_asks.setdefault(pid, []).append(deepcopy(handle))
        elif kind in ('accept', 'decline', 'no_response'):
            if not _is_callback_accept(event, indexed):
                handles.pop((pid, hid), None)
            if kind == 'accept':
                accepted[pid, hid] = event
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            if kind == 'commit': first_commits.setdefault(pid, now)
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key:value for key, value in handles.items() if key[0] != pid}
            for handle in event['pending']:
                handles[pid, handle['hospital']] = deepcopy(handle)
        elif kind == 'shortage_probe_cycle':
            _require(not targets, 'previous nonblocking cycle omitted reviews')
            normal_ready = {row['id'] for row in workflow.snapshot(now)['ready']}
            targets = [patient for patient in sorted(workflow.states)
                if workflow.states[patient] == 'SEARCH' and patient not in first_commits and patient not in normal_ready
                and any(other == patient for other, _ in handles)
                and any(other == patient for other, _ in workflow.offers) and probe_asks.get(patient)]
        elif kind == 'shortage_probe_nonblocking_review':
            _require(targets and pid == targets.pop(0), 'omitted, duplicated or unsolicited nonblocking review')
            index = event['record_index']
            _require(index == len(used) and index < len(records), 'missing or reordered nonblocking record')
            used.add(index)
            record = records[index]
            _require(set(event) == {'t', 'type', 'event_sequence', 'patient', 'record_index'}
                and set(record) == {'snapshot', 'result', 'event_sequence'} and record['event_sequence'] == sequence,
                'nonblocking review carries unknown fields or different marker')
            expected_snapshot = snapshot(pid, now)
            _require(base._same(record['snapshot'], expected_snapshot),
                     'nonblocking input differs from original pending work, actual probe identity, offers or reports')
            expected = _nonblocking_expected(expected_snapshot)
            _require(base._same(record['result'], expected), 'nonblocking rule ignored real ordinary work or current fresh offer')
            if expected['eligible']:
                for offset, hospital in enumerate(expected['options'], 1):
                    check = indexed.get(sequence + offset)
                    resources = next(row['resources'] for row in expected_snapshot['candidates'] if row['hospital'] == hospital)
                    _require(check is not None and base._same(check, dict(t=now, type='information_check',
                        event_sequence=sequence + offset, patient=pid, hospital=hospital, purpose='allocation',
                        eligible=True, reason=None, resources=resources)),
                        'nonblocking offer lacks immediate unchanged fresh allocation check')
                    used_checks.add(sequence + offset)
                following_sequence = sequence + len(expected['options']) + 1
                marker = indexed.get(following_sequence)
                _require(marker is not None and base._same(marker, dict(t=now, type='shortage_probe_nonblocking_ready',
                    event_sequence=following_sequence, patient=pid, options=expected['options'], review_event_sequence=sequence)),
                    'nonblocking review lacks its unique explicitly proven ready marker')
                ready_sequences.add(following_sequence)
            else:
                following = indexed.get(sequence + 1)
                _require(following is None or following['type'] != 'shortage_probe_nonblocking_ready',
                         'ineligible nonblocking review promoted readiness')
        workflow.apply(event)
    _require(not targets and used == set(range(len(records))), 'orphan or omitted nonblocking record')
    _require(ready_sequences == {row['event_sequence'] for row in events if row['type'] == 'shortage_probe_nonblocking_ready'},
             'orphan nonblocking readiness marker')
    _require(branch['metrics']['shortage_probe_nonblocking_ready'] == len(ready_sequences),
             'nonblocking metric differs from raw proven readiness')
    return dict(shortage_probe_nonblocking_reviews=len(records), shortage_probe_nonblocking_ready=len(ready_sequences),
                nonblocking_current_allocation_checks=len(used_checks))


def audit_branch(scenario, branch, *, exact_limit=20000):
    """Audit original, unmodified emissions including separately received offers."""
    probe_enabled = branch.get('shortage_probe_enabled', False)
    _require(type(probe_enabled) is bool, 'shortage probe mode must be explicit')
    if not probe_enabled:
        _require(not branch.get('shortage_probe_spec') and not branch.get('shortage_probe_records')
                 and not branch.get('shortage_probe_nonblocking_records')
                 and not any(event['type'].startswith('shortage_probe_') or 'shortage_probe_review_event_sequence' in event
                     or (event['type'] == 'information_check' and event['purpose'] == 'shortage_probe')
                     for event in _indexed_events(branch)), 'disabled branch contains shortage intervention')
        return prior.audit_branch(scenario, branch, exact_limit=exact_limit)
    _require(branch.get('shortage_probe_spec') == SHORTAGE_PROBE_SPEC,
             'shortage-probe protocol differs from the explicit bounded recheck contract')
    enabled = branch.get('late_pending_guard_enabled', False)
    _require(type(enabled) is bool, 'pending-work protection mode must be explicit')
    if not enabled:
        _require(not branch.get('deadline_safe_spec'), 'disabled branch contains pending-work protocol')
        raise ShortageProbeAuditError('probe intervention requires pending-safe base')
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
    probe_counts = audit_probes(deepcopy(scenario), branch)
    nonblocking_counts = audit_nonblocking(deepcopy(scenario), branch)
    late = prior.audit_late_offers(deepcopy(scenario), branch)
    result = _audit_information_branch(deepcopy(scenario), branch, exact_limit=exact_limit)
    counts = dict(result['counts'])
    counts.update(late)
    counts.update(probe_counts)
    counts.update(nonblocking_counts)
    counts.update(scarce.audit_cohorts(deepcopy(scenario), branch))
    counts.update(prior.audit_supplements(deepcopy(scenario), branch))
    counts.update(audit_inquiries(deepcopy(scenario), branch))
    counts.update(joint.audit_stability(deepcopy(scenario), branch))
    counts.update(recovery.audit_motion(deepcopy(scenario), branch))
    counts.update(prior.audit_backups(deepcopy(scenario), branch))
    counts.update(audit_guard(deepcopy(scenario), branch))
    counts.update(prior.audit_pending_guard(deepcopy(scenario), branch))
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

def audit_shortage_probe_replay(result, *, exact_limit=20000):
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
