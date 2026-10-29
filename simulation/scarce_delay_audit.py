"""Independent raw audit for registered-cohort scarce-bed inquiry routing.

Frozen audit primitives verify resources, real movement, offers and assignment.
Only current cohort and candidate-pool reconstruction are added here. No replay
engine, solver, mutable observation store, or scarce routing implementation is
imported; selector calls are deterministic reproduction, not optimum evidence.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from . import joint_policy_audit as joint

base = joint.base
recovery = joint.recovery
information = joint.information
_TruthLedger = joint._TruthLedger
_indexed_events = joint._indexed_events
_require = joint._require
_end = joint._end
_position = joint._position
_reply_environment = joint._reply_environment
_selector_reproduce = joint._selector_reproduce
CLOCK_ORIGIN_SECONDS = joint.CLOCK_ORIGIN_SECONDS
ScarceDelayAuditError = joint.JointPolicyAuditError
EXPERIMENT_ID = 'scarce_delay_20261016'


def _fresh_sufficient(views, demand):
    return bool(demand) and all(resource in views and views[resource]['status'] == 'fresh'
        and type(views[resource]['free']) in (int, float)
        and views[resource]['free'] >= quantity for resource, quantity in demand.items())


def _scarce_expected(snapshot):
    """Recompute deficit arithmetic; actual policy code is deliberately unused."""
    now, patient = snapshot['t'], snapshot['patient']
    candidates = snapshot['candidates']
    order = [row['hospital'] for row in candidates]
    ordinary = dict(offered_after=order, deferred=[], blocked=[], reason='UNCHANGED')
    own = patient['demand'].get('ed_bed', 0)
    if own <= 0 or len(patient['compatible_hospitals']) < 2:
        return ordinary
    by_hospital = {row['hospital']: row for row in candidates}
    protected = {}
    for other in sorted(snapshot['registered_search_patients'],
                        key=lambda row: (-row['priority'], row['request_t'], row['id'])):
        if other['id'] == patient['id'] or other['priority'] < patient['priority'] \
                or len(other['compatible_hospitals']) != 1 or other['demand'].get('ed_bed', 0) <= 0:
            continue
        target = other['compatible_hospitals'][0]
        if target not in by_hospital or not _fresh_sufficient(
                by_hospital[target]['resources'], patient['demand']) or not _fresh_sufficient(
                other['resource_views'].get(target, {}), other['demand']):
            continue
        handles = [row for row in other['pending_inquiries'] if row['hospital'] == target]
        if len(handles) == 1:
            handle = handles[0]
            deadline = min(handle['submitted_at'] + 1, handle['timeout_at'])
            if deadline <= now:
                continue
            basis, source = 'pending', handle['ask_event_sequence']
        elif not handles and other['inquiry_count'] == 0 and other['request_t'] == now:
            deadline, basis, source = now + 1, 'new_registration', None
        else:
            continue
        protected.setdefault(target, []).append(dict(patient=other['id'], priority=other['priority'],
            demand=other['demand']['ed_bed'], request_event_sequence=other['request_event_sequence'],
            basis=basis, ask_event_sequence=source, deadline=deadline))
    blocks = []
    for target in order:
        claims = protected.get(target, [])
        if claims and by_hospital[target]['resources']['ed_bed']['free'] < own + sum(
                row['demand'] for row in claims):
            blocks.append(dict(hospital=target, known_free=by_hospital[target]['resources']['ed_bed']['free'],
                patient_demand=own, waiting_demand=sum(row['demand'] for row in claims), waiters=claims))
    if not blocks:
        return ordinary
    competing = {row['hospital'] for row in blocks}
    alternative = [target for target in order if target not in competing
                   and _fresh_sufficient(by_hospital[target]['resources'], patient['demand'])]
    if not alternative:
        return dict(offered_after=order, deferred=[], blocked=blocks, reason='NO_FRESH_ALTERNATIVE')
    return dict(offered_after=alternative, deferred=[target for target in order if target not in alternative],
                blocked=blocks, reason='SCARCE_ALTERNATIVE')


def audit_cohorts(scenario, branch):
    """Registration changes order only; future requests cannot join the cohort."""
    enabled = branch.get('cohort_registration_enabled', False)
    _require(type(enabled) is bool, 'invalid cohort registration mode')
    records = branch.get('cohort_registration_records', [])
    events = _indexed_events(branch)
    markers = [row for row in events if row['type'] == 'request_cohort_registered']
    if not enabled:
        _require(not records and not markers, 'baseline contains cohort intervention')
        return dict(cohort_registration_batches=0, cohort_registered_patients=0)
    specs = {row['id']: row for row in scenario['patients']}
    expected_times = {row['search_start_t'] for row in specs.values()}
    requests, marked, consumed = {}, {}, set()
    for event in events:
        kind, at, seq = event['type'], event['t'], event['event_sequence']
        pid = event.get('patient')
        if kind == 'request':
            _require(pid in specs and pid not in requests and at == specs[pid]['search_start_t'],
                     'registration has unknown, repeated, early or delayed patient')
            _require(event['priority'] == specs[pid].get('priority', 4)
                     and event['demand'] == specs[pid].get('resource_demands', {'ed_bed': 1})
                     and event['caps'] == sorted(set(specs[pid].get('required_capabilities', []))),
                     'registered patient attributes differ from observed request')
            requests[pid] = event
        elif kind == 'request_cohort_registered':
            _require(at in expected_times and at not in marked, 'extra or repeated cohort registration')
            expected = [dict(patient=row['id'], request_event_sequence=requests[row['id']]['event_sequence'])
                for row in scenario['patients'] if row['search_start_t'] == at and row['id'] in requests]
            _require(len(expected) == sum(row['search_start_t'] == at for row in specs.values()),
                     'cohort marker precedes a current request or includes future registration')
            _require(set(event) == {'t', 'type', 'event_sequence', 'patients'}
                     and event['patients'] == expected and all(row['request_event_sequence'] < seq for row in expected),
                     'cohort marker differs from exact earlier current requests')
            index = len(consumed)
            _require(index < len(records) and records[index] == dict(t=at, patients=expected, event_sequence=seq),
                     'cohort record differs from raw marker')
            consumed.add(index)
            marked[at] = seq
        elif kind in ('ask', 'scarce_candidate_review', 'joint_inquiry_policy') and pid in requests:
            request_at = requests[pid]['t']
            _require(request_at in marked and requests[pid]['event_sequence'] < marked[request_at] < seq,
                     'inquiry or routing occurred before complete current cohort registration')
    _require(set(requests) == set(specs) and set(marked) == expected_times
             and consumed == set(range(len(records))), 'missing cohort request, marker or record')
    for at, marker_sequence in marked.items():
        boundary = min((event['event_sequence'] for event in events if event['t'] == at
                        and event['type'] == 'early_offer_cycle'), default=float('inf'))
        original_order = [row['id'] for row in scenario['patients'] if row['search_start_t'] == at]
        submitted = []
        for event in events:
            if event['type'] == 'ask' and event['t'] == at and marker_sequence < event['event_sequence'] < boundary \
                    and event['patient'] in original_order and event['patient'] not in submitted:
                submitted.append(event['patient'])
        _require(submitted == [pid for pid in original_order if pid in submitted],
                 'cohort registration reordered original same-tick first inquiry submissions')
    _require(branch['metrics']['cohort_registration_batches'] == len(records), 'cohort metric differs from raw batches')
    return dict(cohort_registration_batches=len(records), cohort_registered_patients=len(requests),
                original_cohort_inquiry_orders=len(marked))


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
    workflow = joint._EarlyWorkflow(deepcopy(scenario))
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


def audit_branch(scenario, branch, *, exact_limit=20000):
    """Inspect original raw events without projection or intervention flags changed."""
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
        _require(branch.get('scarce_delay_spec') == expected_spec, 'scarce intervention protocol differs from fixed contract')
    result = joint._audit_information_branch(deepcopy(scenario), branch, exact_limit=exact_limit)
    counts = dict(result['counts'])
    counts.update(audit_cohorts(deepcopy(scenario), branch))
    counts.update(audit_supplements(deepcopy(scenario), branch))
    counts.update(audit_inquiries(deepcopy(scenario), branch))
    counts.update(joint.audit_stability(deepcopy(scenario), branch))
    counts.update(recovery.audit_motion(deepcopy(scenario), branch))
    counts.update(joint.audit_backups(deepcopy(scenario), branch))
    counts.update(joint.audit_guard(deepcopy(scenario), branch))
    counts.update(joint.audit_pending_guard(deepcopy(scenario), branch))
    counts.update(joint.audit_early_offers(deepcopy(scenario), branch))
    counts.update(recovery.audit_no_added_source(deepcopy(scenario), branch))
    waiting = joint.waiting_metrics(deepcopy(scenario), branch)
    for key, value in result['metrics'].items():
        if key in branch['metrics']:
            _require(base._same(branch['metrics'][key], value), 'whole-cohort endpoint metric mismatch: ' + key)
    return dict(passed=True, counts=counts, waiting=waiting, metrics=result['metrics'],
        information=result['information'], assignment_constraint_violations=0,
        selector_check='actual pure function reproduction from independently reconstructed raw inputs',
        actual_optimality_claimed=False)


def audit_scarce_delay_replay(result, *, exact_limit=20000):
    _require(result.get('schema_version') == 1 and result.get('experiment_id') == EXPERIMENT_ID,
             'unknown scarce-delay replay schema or experiment')
    branches = result['branches']
    ids = [row['id'] for row in branches]
    _require(ids and len(ids) == len(set(ids)) and len(result['execution_order']) == len(ids)
             and set(result['execution_order']) == set(ids), 'missing or duplicated replay branch')
    counts = Counter()
    details = {}
    for branch in branches:
        checked = audit_branch(result['scenario'], branch, exact_limit=exact_limit)
        details[branch['id']] = checked
        counts.update(checked['counts'])
    return dict(passed=True, counts=dict(counts), branches=len(branches), branch_audits=details,
                assignment_constraint_violations=0, actual_optimality_claimed=False)
