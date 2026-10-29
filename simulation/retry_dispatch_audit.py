"""Independent, full-raw audit of the retry/dispatch inquiry intervention.

Frozen physical work, observation, clinical reply and allocation auditors still
receive the original complete branch. Only the explicitly changed inquiry
policy is reconstructed here. No replay engine or solver is imported.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from . import capability_probe_audit as capability
from . import cooperative_probe_audit as cooperative
from . import shortage_probe_audit as independent

RetryDispatchAuditError = capability.CapabilityProbeAuditError
_require = cooperative._require
_indexed_events = cooperative._indexed_events
_end = cooperative._end
_TruthLedger = cooperative._TruthLedger
_position = cooperative._position
_reply_environment = cooperative._reply_environment
_is_callback_accept = cooperative._is_callback_accept
_after_queue_events = cooperative._after_queue_events
base = cooperative.base
joint = cooperative.joint
scarce = cooperative.scarce
recovery = cooperative.recovery
information = cooperative.information
safe = cooperative.safe
late = cooperative.late
CLOCK_ORIGIN_SECONDS = cooperative.CLOCK_ORIGIN_SECONDS
EXPERIMENT_ID = 'retry_dispatch_20261018'
VARIANTS = ('frontier', 'paced', 'balanced')
NAMESPACE_FIELDS = {'retry_dispatch_enabled', 'retry_dispatch_selector', 'retry_dispatch_spec',
                    'retry_dispatch_records', 'retry_dispatch_contacts', 'retry_dispatch_wakes',
                    'retry_dispatch_foundation_spec'}
RETRY_DISPATCH_FOUNDATION_SPEC = dict(
    capability_source='scenario.inquiry_queue.enabled',
    inherited_replays=dict(
        cooperative_queue='simulation.cooperative_probe_engine.CooperativeProbeReplay',
        independent_replies='simulation.shortage_probe_engine.ShortageProbeReplay'),
    actual_replays=dict(
        cooperative_queue='simulation.retry_dispatch_engine.CooperativeRetryDispatchReplay',
        independent_replies='simulation.retry_dispatch_engine.IndependentRetryDispatchReplay'),
    cohort_registration=True, scarce_routing=True, late_reply_recovery=True,
    late_pending_guard=True, shortage_probe=True, recovery=True, pending_protection=True,
    selector_changed=True, internal_inquiry_slot='v4',
    hospital_workflow_inherited_without_change=True,
    extra_resource_change_publications=False, worker_count_unchanged=True,
    original_fifo_timeout_offer_ttl_and_acceptance_unchanged=True)
# Independent literal contract. Importing a producer's specification would make
# a producer edit silently relax this verifier's claimed protocol.
RETRY_DISPATCH_SPEC = dict(
    schema_version=1, variants=list(VARIANTS), internal_inquiry_slot='v4',
    actual_selector_prefix='retry_dispatch_', base_kernel='server.inquiry_v4.decide',
    original_seven_inputs_retained=True, initial_unaccepted_search_only=True,
    frontier='least_actual_contacts_for_current_condition_across_current_eligible_universe',
    pacing='preserve_actual_future_previous_retry_at_from_existing_report_wakes',
    pacing_change_exceptions=['capacity_or_used_changed', 'unknown_or_stale_to_fresh'],
    timestamps_alone_release_retry_wait=False, backoff_added=False,
    balancing='least_current_public_unresolved_hospital_count_after_frontier_and_pacing',
    kernel_sends_only_original_offered_candidates=True,
    maximum_open_inquiries=3, maximum_episode_contacts=10,
    episode_transition_unchanged=True, actual_contact_clock_unchanged=True,
    original_fifo_timeout_offer_ttl_and_acceptance_unchanged=True,
    workers_added=False, extra_resource_change_publications=False,
    hidden_work_calendar_visible=False)


def _audit_policy_contexts(scenario, branch):
    """Rebuild cumulative contact burdens and public views in emission order.

    The original seven inputs are independently checked by the inquiry audit
    below. This additional pass proves every new context field and source ref;
    the pure selector is then reproduced on those jointly verified inputs.
    """
    from .retry_dispatch_policy import decide

    cooperative_mode = scenario.get('inquiry_queue', {}).get('enabled', False)
    workflow = (cooperative._CooperativeWorkflow if cooperative_mode
                else independent._ProbeWorkflow)(deepcopy(scenario))
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    records = branch['retry_dispatch_records']
    _require(isinstance(records, list), 'dispatch records must retain every actual selector review')
    by_sequence = {}
    for record in records:
        _require(type(record) is dict and set(record) == {'inquiry_event_sequence', 'context', 'decision'},
                 'dispatch record contains unknown or missing fields')
        seq = record['inquiry_event_sequence']
        _require(type(seq) is int and seq >= 0 and seq not in by_sequence,
                 'duplicate or invalid dispatch review identity')
        by_sequence[seq] = record
    hospitals = {row['id']: row for row in scenario['hospitals']}
    first_commits, first_accepts = {}, {}
    contacts, wakes, decisions, used = [], [], {}, set()
    contact_attempts, episode_contacts = Counter(), Counter()
    expanded = set()

    def views(pid, hid, now):
        demand = workflow._base_patient(pid)['demand']
        return information._views('observed', workflow.observations, workflow.truth, hid, demand, now)

    def universe(pid, now):
        current = workflow._base_patient(pid)
        point = _position(scenario, branch, pid, now)
        near = branch['policy'].get('near_minutes')
        rows = []
        for hid in current['compatible_hospitals']:
            if any(hospitals[hid].get('capacity', {}).get(resource, 0) < amount
                   for resource, amount in current['demand'].items()):
                continue
            if workflow.asks.get((pid, hid), 0) or workflow.offers.get((pid, hid), -1) > now \
                    or hid in workflow.failed.get(pid, set()):
                continue
            travel = recovery._synthetic_minutes(point, hospitals[hid])
            if near is not None and pid not in expanded and travel > near:
                continue
            current_views = views(pid, hid, now)
            if any(value['status'] == 'fresh' and value['free'] < current['demand'][resource]
                   for resource, value in current_views.items()):
                continue
            rows.append(dict(hospital=hid, travel_minutes=travel, resources=current_views))
        return sorted(rows, key=lambda row: (row['travel_minutes'], row['hospital']))

    for event in events:
        kind, now, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'ask':
            condition = late._condition(workflow, pid)
            contact_attempts[pid, hid] += 1
            _require(type(event['attempt']) is int and event['attempt'] == contact_attempts[pid, hid],
                     'actual contact attempt was reused, skipped or reset across episodes')
            contacts.append(dict(patient=pid, hospital=hid, attempt=event['attempt'], submitted_at=now,
                ask_event_sequence=seq, condition_fingerprint=late._fingerprint(condition),
                resources=views(pid, hid, now)))
            episode_contacts[pid, event['inquiry_episode']] += 1
        elif kind in ('resource_event_review', 'report_driven_wake'):
            report = indexed.get(event['report_event_sequence'])
            _require(report is not None and report['event_sequence'] < seq
                     and report['type'] == 'resource_report_received' and report['hospital'] == hid
                     and report['applied'] is True and report['t'] == now,
                     'retry wake lacks its earlier same-time applied public report')
            previous = event['previous_retry_at']
            _require(previous is None or joint._clock(previous), 'invalid original retry-wait deadline')
            condition = late._condition(workflow, pid)
            wakes.append(dict(patient=pid, hospital=hid, received_t=now, wake_event_sequence=seq,
                report_event_sequence=event['report_event_sequence'], previous_retry_at=previous,
                condition_fingerprint=late._fingerprint(condition), resources=views(pid, hid, now)))
        elif kind == 'joint_inquiry_policy':
            _require(seq in by_sequence, 'actual inquiry selector omitted its new dispatch context')
            record = by_sequence[seq]
            row = branch['inquiry_decisions'][event['record_index']]
            _require(row['event_sequence'] == seq, 'dispatch context references another inquiry review')
            condition = late._condition(workflow, pid)
            expected = dict(schema_version=1, evaluated_t=now,
                patient=dict(id=pid, state=workflow.states[pid], first_commit_t=first_commits.get(pid),
                    first_accept_event_sequence=first_accepts.get(pid), condition=condition,
                    condition_fingerprint=late._fingerprint(condition)),
                episode=dict(id=row['inquiry_episode'], contacts=episode_contacts[pid, row['inquiry_episode']]),
                universe=universe(pid, now),
                contacts=[deepcopy(contact) for contact in contacts if contact['patient'] == pid],
                retry_barriers=[deepcopy(wake) for wake in wakes if wake['patient'] == pid])
            _require(capability._same_literal(record['context'], expected),
                     'dispatch context differs from current public universe or complete earlier contact/wake ledger: '
                     + independent._difference(record['context'], expected))
            reproduced = decide(branch['retry_dispatch_selector'], inputs=deepcopy(row['inputs']),
                                context=deepcopy(expected))
            _require(base._same(record['decision'], reproduced)
                     and base._same(row['decision'], reproduced),
                     'actual dispatch decision differs from the fixed pure selector on audited public inputs')
            decisions[seq] = reproduced
            used.add(seq)
        elif kind == 'commit':
            first_commits.setdefault(pid, now)
        elif kind == 'accept':
            first_accepts.setdefault(pid, seq)
        elif kind == 'expand':
            expanded.add(pid)
        workflow.apply(event)

    _require(list(by_sequence) == sorted(used) and used == set(by_sequence),
             'dispatch record is orphan, reordered or duplicated')
    _require(capability._same_literal(branch['retry_dispatch_contacts'], contacts),
             'exported dispatch contacts erased, invented or changed actual historical burden')
    _require(capability._same_literal(branch['retry_dispatch_wakes'], wakes),
             'exported retry barriers differ from original wake reports or retry deadlines')
    metrics = branch['metrics']
    _require(metrics['retry_dispatch_reviews'] == len(records)
             and metrics['retry_dispatch_frontier_deferrals'] == sum(
                 row['reason'] == 'frontier_not_offered' for row in decisions.values())
             and metrics['retry_dispatch_wait_preservations'] == sum(
                 row['reason'] == 'retry_wait_preserved' for row in decisions.values()),
             'dispatch metrics differ from actual reproduced selector decisions')
    return decisions, dict(retry_dispatch_reviews=len(records), retry_dispatch_contacts=len(contacts),
                           retry_dispatch_wakes=len(wakes))



def _audit_independent_inquiries(scenario, branch, policy_decisions):
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
            reproduced = policy_decisions[seq]
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


def _audit_cooperative_inquiries(scenario, branch, work, policy_decisions):
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
    handles, active, unresolved, feedback, latest, checks = {}, {}, {}, [], {}, []
    truth = _TruthLedger(scenario)
    used_records, episode_contacts = set(), Counter()
    covered_asks, supplemental_asks, probe_asks, retry_asks = set(), set(), set(), set()
    current_episode = {pid: 0 for pid in specs}
    def seconds(value):
        return CLOCK_ORIGIN_SECONDS + int(value * 60)

    for event in events:
        at, kind, seq = event['t'], event['type'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'ask':
            _require(pid in specs and hid in hospitals, 'inquiry target is undeclared')
            key = (pid, hid, event['attempt'])
            _require(key not in handles and (pid, hid) not in active, 'duplicate active inquiry attempt')
            _require(event['submitted_at'] == at and event['timeout_at'] == at + scenario.get('timeout_minutes', 6),
                     'inquiry absolute handle or deadline changed')
            delivery = work['deliveries'][seq]
            admitted = delivery['admitted']
            finish = delivery['finish']
            silent = delivery['silent'] or finish is None or finish > event['timeout_at'] if admitted else None
            due = event['timeout_at'] if silent else finish
            handle = dict(hospital=hid, patient=pid, submitted_at=seconds(at),
                          submitted_minute=at, due=due, silent=silent, attempt=event['attempt'],
                          ask_event_sequence=seq)
            handles[key] = handle
            if admitted:
                active[pid, hid] = handle
                unresolved[seq] = handle
                latest[pid] = at
            if not admitted: latest[pid] = at
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
            candidate = _after_queue_events(indexed, seq)
            following = [candidate] if candidate is not None and candidate['type']=='ask' and candidate['t']==at and candidate.get('patient')==pid else []
            _require([e['hospital'] for e in following] == record['result']['send'],
                     'supplement marker differs from immediately submitted inquiry')
            supplemental_asks.update(e['event_sequence'] for e in following)
        elif kind == 'shortage_probe_review':
            record = branch['shortage_probe_records'][event['record_index']]
            following = next((row for row in events if row.get('shortage_probe_review_event_sequence') == seq), None)
            actual = [following] if following is not None and following['type'] == 'ask' and following.get('shortage_probe_review_event_sequence') == seq else []
            _require([row['hospital'] for row in actual] == record['result']['send'],
                     'probe review differs from its uniquely sourced actual inquiry')
            probe_asks.update(row['event_sequence'] for row in actual)
        elif kind == 'cooperative_busy_retry_review':
            record=branch['cooperative_busy_retry_records'][event['record_index']]
            actual=[row for row in events if row.get('cooperative_busy_retry_review_event_sequence')==seq]
            _require([row['hospital'] for row in actual]==record['result']['send'],'busy retry actual inquiry source differs')
            retry_asks.update(row['event_sequence'] for row in actual)
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
            reproduced = policy_decisions[seq]
            _require(base._same(row['decision'], reproduced), 'actual pure selector did not reproduce decision')
            next_supplement = min((e['event_sequence'] for e in events if e['event_sequence'] > seq
                and e['t'] == at and e['type'] in ('scarce_supplement_review', 'shortage_probe_review', 'cooperative_busy_retry_review') and e.get('patient') == pid), default=float('inf'))
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
        _require(not covered_asks.intersection(supplemental_asks | probe_asks | retry_asks) and not supplemental_asks.intersection(probe_asks | retry_asks) and not probe_asks.intersection(retry_asks) and covered_asks | supplemental_asks | probe_asks | retry_asks ==
                 {e['event_sequence'] for e in events if e['type'] == 'ask'},
                 'actual inquiry lacks a unique reproduced selector decision')
    _require(all(handle['due'] > end for handle in unresolved.values()), 'missing due inquiry feedback')
    expected_late = Counter()
    for source in events:
        if source['type'] != 'ask': continue
        delivery = work['deliveries'][source['event_sequence']]
        if delivery['admitted'] and not delivery['silent'] and delivery['finish'] is not None \
                and delivery['finish'] > source['timeout_at']:
            expected_late[delivery['finish'],'late_reply',source['hospital'],source['patient'],source['attempt'],None] += 1
    actual_late = Counter((event['t'],event['type'],event['hospital'],event['patient'],event['attempt'],None)
                         for event in events if event['type'] == 'late_reply')
    _require(actual_late == expected_late, 'late clinical delivery differs from independently conserved actual job completion')
    return dict(selector_reproductions=len(records), original_inquiry_handles=len(handles),
                fifo_jobs=work['counts']['cooperative_jobs'], current_feedback_receipts=len(feedback))


def _audit_independent_branch(scenario, branch, policy_decisions, *, exact_limit=20000):
    """Audit original, unmodified emissions including separately received offers."""
    probe_enabled = branch.get('shortage_probe_enabled', False)
    _require(type(probe_enabled) is bool, 'shortage probe mode must be explicit')
    if not probe_enabled:
        _require(not branch.get('shortage_probe_spec') and not branch.get('shortage_probe_records')
                 and not branch.get('shortage_probe_nonblocking_records')
                 and not any(event['type'].startswith('shortage_probe_') or 'shortage_probe_review_event_sequence' in event
                     or (event['type'] == 'information_check' and event['purpose'] == 'shortage_probe')
                     for event in _indexed_events(branch)), 'disabled branch contains shortage intervention')
        return independent.prior.audit_branch(scenario, branch, exact_limit=exact_limit)
    _require(branch.get('shortage_probe_spec') == independent.SHORTAGE_PROBE_SPEC,
             'shortage-probe protocol differs from the explicit bounded recheck contract')
    enabled = branch.get('late_pending_guard_enabled', False)
    _require(type(enabled) is bool, 'pending-work protection mode must be explicit')
    if not enabled:
        _require(not branch.get('deadline_safe_spec'), 'disabled branch contains pending-work protocol')
        raise RetryDispatchAuditError('probe intervention requires pending-safe base')
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
    counts.update(_audit_independent_inquiries(deepcopy(scenario), branch, policy_decisions))
    counts.update(joint.audit_stability(deepcopy(scenario), branch))
    counts.update(recovery.audit_motion(deepcopy(scenario), branch))
    counts.update(independent.prior.audit_backups(deepcopy(scenario), branch))
    counts.update(independent.audit_guard(deepcopy(scenario), branch))
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
        _require(not branch.get('cooperative_probe_spec') and not branch.get('cooperative_job_ledger')
            and not branch.get('cooperative_control_acks') and not branch.get('cooperative_ready_records')
            and not branch.get('cooperative_busy_retry_records')
            and not any(event['type'].startswith('cooperative_') or 'cooperative_job_id' in event
                for event in _indexed_events(branch)), 'disabled reference branch contains cooperative behavior')
        return independent.audit_branch(scenario,branch,exact_limit=exact_limit)
    _require(branch.get('cooperative_probe_spec') == cooperative.COOPERATIVE_PROBE_SPEC,
             'cooperative workflow differs from the explicit current-control and conserved-work protocol')
    _require(branch.get('shortage_probe_spec')==cooperative.COOPERATIVE_SHORTAGE_SPEC,
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
        inquiries=_audit_cooperative_inquiries(deepcopy(scenario),branch,work,policy_decisions)
        backups=cooperative.audit_backups(deepcopy(scenario),branch,work)
    else:
        _require(not branch['shortage_probe_records'] and not any(
            event.get('shortage_probe_review_event_sequence') is not None for event in ordered),
            'queue-disabled environment gained an optional clinical inquiry')
        _require(not branch.get('cooperative_busy_retry_records'),'queue-disabled environment contains orphan busy retry records')
        probe=dict(shortage_probe_reviews=0,shortage_probe_sent=0)
        callback=safe.audit_late_offers(deepcopy(scenario),branch)
        inquiries=_audit_independent_inquiries(deepcopy(scenario),branch,policy_decisions)
        backups=safe.audit_backups(deepcopy(scenario),branch)
    ready=cooperative.audit_ready(deepcopy(scenario),branch)
    result=cooperative._audit_information_branch(deepcopy(scenario),branch,exact_limit=exact_limit)
    counts=dict(result['counts'])
    for values in (work['counts'],probe,callback,inquiries,backups,ready,
        scarce.audit_cohorts(deepcopy(scenario),branch),cooperative.audit_supplements(deepcopy(scenario),branch),
        joint.audit_stability(deepcopy(scenario),branch),recovery.audit_motion(deepcopy(scenario),branch),
        cooperative.audit_guard(deepcopy(scenario),branch),cooperative.audit_pending_guard(deepcopy(scenario),branch),
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


def audit_branch(scenario, branch, *, exact_limit=20000):
    """Audit one unchanged full raw reference or explicitly changed candidate."""
    namespace = {key for key in branch if key.startswith('retry_dispatch_')}
    if not namespace:
        _require(not str(branch.get('id', '')).startswith(('v5_', 'retry_')),
                 'retry dispatch candidate omitted its explicit intervention namespace')
        return capability.audit_branch(scenario, branch, exact_limit=exact_limit)
    _require(namespace == NAMESPACE_FIELDS, 'missing or unknown retry dispatch raw namespace')
    _require(branch['retry_dispatch_enabled'] is True
             and branch['retry_dispatch_selector'] in VARIANTS,
             'explicit known retry dispatch variant required')
    _require(capability._same_literal(branch['retry_dispatch_spec'], RETRY_DISPATCH_SPEC),
             'retry dispatch contract differs from the fixed public-input ablation')
    _require(not any(key.startswith('capability_') for key in branch),
             'new selector falsely claims exact frozen capability delegation')
    queue = scenario.get('inquiry_queue', {})
    _require(type(queue) is dict and type(queue.get('enabled', False)) is bool,
             'declared inquiry queue support must be boolean')
    enabled = queue.get('enabled', False)
    mode = 'cooperative_queue' if enabled else 'independent_replies'
    _require(capability._same_literal(branch['retry_dispatch_foundation_spec'],
                                     dict(RETRY_DISPATCH_FOUNDATION_SPEC, mode=mode)),
             'retry dispatch foundation or queue mode differs from the declared selector-changing subclass')
    _require(branch.get('inquiry_selector') == 'v4'
             and branch.get('policy', {}).get('rule') in ('H1', 'C1')
             and branch.get('policy', {}).get('name') == branch.get('id'),
             'retry dispatch changed its declared internal selector slot, allocation rule or actual identity')
    _require(all(branch.get(flag) is True for flag in (
        'shortage_probe_enabled', 'late_pending_guard_enabled', 'late_reply_recovery_enabled',
        'cohort_registration_enabled', 'scarce_routing_enabled')),
        'retry dispatch changed its complete safe-probe foundation')
    expected_scarce_spec = dict(inquiry='v4', allocation=branch['policy']['rule'],
        cohort_registration=True, scarce_routing=True, actual_action='parallel_scarce_supplement',
        original_selector_candidates_unchanged=False, maximum_supplements_per_patient=1,
        recovery=True, pending_protection=True, pending_window_minutes=1, time_step_seconds=15,
        actual_selector_reused=False, original_seven_inputs_retained=True,
        base_selector_reused_as_kernel=True, extra_resource_change_publications=False)
    _require(capability._same_literal(branch.get('scarce_delay_spec'), expected_scarce_spec),
             'retry candidate concealed changed candidate tiers or changed the scarce-foundation contract')
    if enabled:
        _require(branch.get('cooperative_probe_enabled') is True
                 and branch.get('cooperative_queue_enabled') is True
                 and branch.get('comparison_id') == 'cooperative_probe_20261017',
                 'queue-supported retry candidate omitted its complete cooperative raw protocol')
    else:
        _require(not any(key.startswith('cooperative_') for key in branch)
                 and branch.get('comparison_id') == 'shortage_probe_20261017'
                 and branch.get('inquiry_queue_report') is None,
                 'independent retry candidate invented queue workers or cooperative controls')
    decisions, extra_counts = _audit_policy_contexts(scenario, branch)
    # The original complete object reaches every frozen physical/clinical
    # component. There is no projection, raw rewriting or compatibility patch.
    checked = (_audit_cooperative_branch if enabled else _audit_independent_branch)(
        scenario, branch, decisions, exact_limit=exact_limit)
    checked['counts'].update(extra_counts)
    checked['selector_check'] = (
        'new selector reproduced on raw-reconstructed original seven inputs, cumulative contacts, '
        'past wake deadlines and current public universe; full raw work and allocation retained')
    return checked


def audit_retry_dispatch_replay(result, *, exact_limit=20000):
    _require(type(result.get('schema_version')) is int and result['schema_version'] == 1
             and result.get('experiment_id') == EXPERIMENT_ID, 'unknown retry dispatch replay schema')
    ids = [row['id'] for row in result['branches']]
    _require(ids and all(type(code) is str and code for code in ids) and len(ids) == len(set(ids))
             and len(result['execution_order']) == len(ids) and set(result['execution_order']) == set(ids),
             'missing, duplicated or invalid retry dispatch replay branch')
    counts, details = Counter(), {}
    for branch in result['branches']:
        checked = audit_branch(result['scenario'], branch, exact_limit=exact_limit)
        counts.update(checked['counts'])
        details[branch['id']] = checked
    return dict(passed=True, counts=dict(counts), branches=len(ids), branch_audits=details,
                assignment_constraint_violations=0, actual_optimality_claimed=False)
