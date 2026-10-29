"""Raw audit of conservative optional late offers with pending-work protection.

V1 remains frozen. This module copies only the late-callback ledger to include
original current pending handles. All physical, assignment-proof, FIFO, motion
and workflow checks consume the same unmodified raw records through V1 helpers.
No engine, solver, raw-record projection, or mutable module patch is used.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from . import deadline_recovery_audit as prior

base = prior.base
joint = prior.joint
scarce = prior.scarce
recovery = prior.recovery
information = prior.information
_require = prior._require
_indexed_events = prior._indexed_events
_end = prior._end
_views = prior._views
_LateWorkflow = prior._LateWorkflow
_condition = prior._condition
_fingerprint = prior._fingerprint
_fifo_deliveries = prior._fifo_deliveries
_is_callback_accept = prior._is_callback_accept
audit_supplements = prior.audit_supplements
audit_inquiries = prior.audit_inquiries
audit_backups = prior.audit_backups
audit_guard = prior.audit_guard
audit_pending_guard = prior.audit_pending_guard
audit_early_offers = prior.audit_early_offers
DeadlineSafeAuditError = prior.DeadlineRecoveryAuditError
EXPERIMENT_ID = 'deadline_safe_20261016'


def _late_expected(snapshot):
    # Original pending work is not cancelled or accelerated by an optional reply.
    if snapshot['patient']['pending_inquiries']:
        return dict(eligible=False, hospital=None, expires_at=None,
                    protected_patients=[], reason='PENDING_INQUIRY')
    return prior._late_expected(snapshot)


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
                pending_inquiries=sorted([deepcopy(value) for (patient, _), value in handles.items() if patient == pid],
                                         key=lambda handle:(handle['hospital'], handle['attempt'])),
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
    enabled = branch.get('late_pending_guard_enabled', False)
    _require(type(enabled) is bool, 'pending-work protection mode must be explicit')
    if not enabled:
        _require(not branch.get('deadline_safe_spec'), 'disabled branch contains pending-work protocol')
        return prior.audit_branch(scenario, branch, exact_limit=exact_limit)
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

def audit_deadline_safe_replay(result, *, exact_limit=20000):
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

