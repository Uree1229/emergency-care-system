"""Full raw audit of a received-resource recovery eligibility intervention.

The frozen frontier, clinical response, physical work, timeout, information and
allocation components receive the original complete branch. New verification
reconstructs only the explicitly changed tried-hospital eligibility, recovery
evidence and once-only token use. No replay engine or solver is imported.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from . import retry_dispatch_audit as parent

RecoveryDispatchAuditError = parent.RetryDispatchAuditError
_require = parent._require
_indexed_events = parent._indexed_events
base = parent.base
information = parent.information
cooperative = parent.cooperative
independent = parent.independent
late = parent.late
joint = parent.joint
EXPERIMENT_ID = 'recovery_dispatch_20261018'
VARIANTS = ('eager', 'latched')
NAMESPACE_FIELDS = {'recovery_dispatch_enabled', 'recovery_dispatch_variant', 'recovery_dispatch_spec',
    'recovery_dispatch_foundation_spec', 'recovery_dispatch_declines', 'recovery_dispatch_resource_transitions',
    'recovery_dispatch_reviews', 'recovery_dispatch_reopens', 'recovery_dispatch_consumptions'}
RECOVERY_DISPATCH_SPEC = dict(schema_version=1, variants=list(VARIANTS), base_dispatch='frontier',
    initial_unaccepted_search_only=True, actual_clinical_decline_required=True,
    fresh_shortage_to_fresh_sufficient_content_recovery_required=True,
    all_required_resource_source_times_after_decline=True,
    required_resources_in_same_recovery_report=True,
    recovery_history='actual_applied_semantic_public_report_transitions',
    maximum_actual_reopens_per_patient_condition=1, minimum_original_tried_hospitals=2,
    eager_requires_other_pending=True, latched_requires_no_pending=True,
    latched_requires_only_target_normal_candidate=True,
    latched_may_interrupt_original_retry_once=True, any_valid_offer_blocks=True,
    actual_original_selector_and_review_clock=True, temporary_target_eligibility_only=True,
    restore_target_if_not_actually_sent=True, consume_only_actual_target_inquiry=True,
    same_content_refresh_renews_token=False, old_inquiry_deadline_or_offer_ttl_renewed=False,
    episode_transition_and_contact_budget_unchanged=True, maximum_open_inquiries=3,
    maximum_episode_contacts=10, extra_resources_workers_or_reports=False,
    fifo_work_acceptance_readiness_and_allocation_unchanged=True,
    future_response_or_resource_schedule_visible=False)
RECOVERY_FOUNDATION_SPEC = dict(base_dispatch='frontier',
    base_dispatch_replays=dict(independent_replies='simulation.retry_dispatch_engine.IndependentRetryDispatchReplay',
        cooperative_queue='simulation.retry_dispatch_engine.CooperativeRetryDispatchReplay'),
    actual_recovery_replays=dict(independent_replies='simulation.recovery_dispatch_engine.IndependentRecoveryDispatchReplay',
        cooperative_queue='simulation.recovery_dispatch_engine.CooperativeRecoveryDispatchReplay'),
    replaced_metadata_field='retry_dispatch_foundation_spec', eligibility_changed=True,
    pure_frontier_records_and_original_seven_inputs_retained=True,
    original_physical_clinical_queue_and_readiness_rules_retained=True)


def _literal(actual, expected, message):
    _require(parent.capability._same_literal(actual, expected), message + ': '
             + independent._difference(actual, expected))


def _audit_declines_and_reports(scenario, branch):
    """All received reports remain raw; semantic transitions are derived here."""
    enabled = scenario.get('inquiry_queue', {}).get('enabled', False)
    workflow = (cooperative._CooperativeWorkflow if enabled else independent._ProbeWorkflow)(deepcopy(scenario))
    resource_names = {row['id']: sorted(row.get('capacity', {})) for row in scenario['hospitals']}
    observations, report_refs, contacts, declines, transitions = {}, {}, {}, [], []
    prefix_declines, prefix_transitions = {}, {}

    def public(hid, now):
        return {resource: information._observation_view(observations, hid, resource, now, 0, 0)
                for resource in resource_names[hid]}

    for event in _indexed_events(branch):
        kind, now, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'resource_report_received':
            before = public(hid, now)
            refs = {resource: report_refs.get((hid, resource)) for resource in resource_names[hid]}
            information._apply_observation(observations, event)
            after = public(hid, now)
            if event['applied']:
                for resource in resource_names[hid]:
                    if resource in event['resources'] and after[resource]['sequence'] == event['packet_sequence'] \
                            and after[resource]['observed_at'] == event['observed_at']:
                        report_refs[hid, resource] = seq
                if any(before[resource][key] != after[resource][key]
                       for resource in resource_names[hid] for key in ('capacity', 'used', 'status')):
                    transitions.append(dict(hospital=hid, t=now, report_event_sequence=seq,
                        before_report_event_sequences=refs, before_resources=before, after_resources=after))
        elif kind == 'ask':
            condition = late._condition(workflow, pid)
            contacts[pid, hid] = dict(patient=pid, hospital=hid, attempt=event['attempt'],
                submitted_at=now, ask_event_sequence=seq, condition_fingerprint=late._fingerprint(condition),
                resources=information._views('observed', workflow.observations, workflow.truth,
                                            hid, condition['demand'], now))
        elif kind == 'decline':
            _require((pid, hid) in contacts and workflow.asks[pid, hid] > 0,
                     'clinical recovery token lacks an actual currently retained inquiry')
            contact = contacts[pid, hid]
            condition = late._condition(workflow, pid)
            declines.append(dict(patient=pid, hospital=hid, t=now, decline_event_sequence=seq,
                ask_event_sequence=contact['ask_event_sequence'], attempt=contact['attempt'],
                condition_fingerprint=late._fingerprint(condition),
                resources=information._views('observed', workflow.observations, workflow.truth,
                                            hid, condition['demand'], now)))
        elif kind == 'recovery_reopen_review':
            prefix_declines[seq] = deepcopy([row for row in declines if row['patient'] == pid])
            prefix_transitions[seq] = deepcopy(transitions)
        workflow.apply(event)
    _literal(branch['recovery_dispatch_declines'], declines,
             'clinical decline export differs from real original inquiries and current observed conditions')
    _literal(branch['recovery_dispatch_resource_transitions'], transitions,
             'recovery transition export differs from complete actual applied report history')
    return prefix_declines, prefix_transitions


def _audit_recovery_trace(scenario, branch):
    """Reconstruct public recovery inputs and every temporary tried-set edit."""
    from .recovery_dispatch_policy import decide_recovery_reopen
    prefixes, transition_prefixes = _audit_declines_and_reports(scenario, branch)
    enabled = scenario.get('inquiry_queue', {}).get('enabled', False)
    workflow = (cooperative._CooperativeWorkflow if enabled else independent._ProbeWorkflow)(deepcopy(scenario))
    events = _indexed_events(branch)
    indexed = {row['event_sequence']: row for row in events}
    hospitals = {row['id']: row for row in scenario['hospitals']}
    requests, first_commits, first_accepts, handles = {}, {}, {}, {}
    tried = {row['id']: set() for row in scenario['patients']}
    episodes, episode_contacts, next_reviews, last_contacts = {}, Counter(), {}, {}
    expanded, wake_hint, frames, consumptions = set(), {}, {}, []
    declined_patients = set()
    observed_reviews, clock, phase = {}, None, None
    reviews, reopens = branch['recovery_dispatch_reviews'], branch['recovery_dispatch_reopens']
    _require(type(reviews) is list and type(reopens) is list, 'recovery records must remain ordered lists')
    used_reviews, used_results = set(), set()

    def current_candidates(pid, now, *, narrow=True, include_far=False):
        patient = workflow._base_patient(pid)
        point = parent._position(scenario, branch, pid, now)
        near = branch['policy'].get('near_minutes')
        hint = wake_hint.get(pid) if narrow else None
        rows = []
        for hid in patient['compatible_hospitals']:
            if hid in workflow.failed.get(pid, set()) or any(
                    hospitals[hid].get('capacity', {}).get(r, 0) < q for r, q in patient['demand'].items()):
                continue
            if hint is not None and hint[0] == now and hid != hint[1]:
                continue
            travel = parent.recovery._synthetic_minutes(point, hospitals[hid])
            if not include_far and near is not None and pid not in expanded and travel > near:
                continue
            rows.append(dict(hospital=hid, travel_minutes=travel,
                resources=information._views('observed', workflow.observations, workflow.truth,
                                            hid, patient['demand'], now)))
        return sorted(rows, key=lambda row: (row['travel_minutes'], row['hospital']))

    def own_handles(pid):
        return sorted((deepcopy(row) for (patient, _), row in handles.items() if patient == pid),
                      key=lambda row: (row['hospital'], row['attempt']))

    def normal_entry(pid, now):
        # JointPolicyReplay.send_asks performs this before the inherited
        # retry/duplicate-review guards, even if no selector or ask follows.
        if now >= next_reviews.get(pid, 0) and not tried[pid] \
                and episode_contacts[pid, episodes.get(pid, 0)]:
            episodes[pid] = episodes.get(pid, 0) + 1

    for event in events:
        kind, now, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if clock != now:
            phase = None
        clock = now
        if kind == 'event_review_cycle':
            phase = event['phase']
        if kind == 'request':
            requests[pid] = seq
        elif kind == 'ask':
            episode = event['inquiry_episode']
            if pid in declined_patients and pid not in first_commits and pid not in first_accepts:
                _require(episode == episodes.get(pid, 0), 'actual inquiry invented an unproven recovery-scope episode')
            if episode != episodes.get(pid, 0):
                _require(episode == episodes.get(pid, 0) + 1, 'original inquiry episode jumped')
                tried[pid].clear()
            episodes[pid] = episode
            episode_contacts[pid, episode] += 1
            last_contacts[pid] = now
            handles[pid, hid] = dict(hospital=hid, attempt=event['attempt'], submitted_at=now,
                timeout_at=event['timeout_at'], ask_event_sequence=seq)
            if not enabled or event.get('cooperative_job_id') is not None:
                tried[pid].add(hid)
        elif kind == 'cooperative_probe_not_admitted':
            handles.pop((pid, hid), None)
        elif kind in ('decline', 'no_response', 'accept'):
            handles.pop((pid, hid), None)
            if kind == 'decline':
                declined_patients.add(pid)
            if kind == 'accept':
                first_accepts.setdefault(pid, seq)
        elif kind in ('commit', 'arrive', 'transfer_search', 'treatment_start', 'backup_closed'):
            handles = {key: value for key, value in handles.items() if key[0] != pid}
            if kind == 'commit':
                first_commits.setdefault(pid, now)
            if kind == 'transfer_search':
                episodes[pid] = episodes.get(pid, 0) + 1
                tried[pid] = set(workflow.failed.get(pid, set()))
        elif kind in ('backup_retained', 'backup_arrival_preserved', 'backup_transfer_reused'):
            handles = {key: value for key, value in handles.items() if key[0] != pid}
            for row in event['pending']:
                handles[pid, row['hospital']] = deepcopy(row)
        elif kind in ('resource_event_review', 'report_driven_wake'):
            wake_hint[pid] = (now, hid)
            tried[pid].discard(hid)
            # This raw original wake calls send_asks even when the current
            # tick already reviewed the patient. In that duplicate-review
            # path the recovery wrapper emits no review, but JointPolicy's
            # original empty-tried entry still runs before sim's guard.
            if observed_reviews.get(pid) == now:
                normal_entry(pid, now)
        elif kind in ('backup_failed_reopened', 'backup_alert_retry'):
            tried[pid].discard(hid)
        elif kind == 'expand':
            expanded.add(pid)
        elif kind == 'joint_inquiry_policy':
            row = branch['inquiry_decisions'][event['record_index']]
            normal_entry(pid, now)
            episode = row['inquiry_episode']
            if pid in declined_patients and pid not in first_commits and pid not in first_accepts:
                _require(episode == episodes.get(pid, 0), 'normal selector invented an unproven recovery-scope episode')
            if episode != episodes.get(pid, 0):
                _require(episode == episodes.get(pid, 0) + 1, 'normal selector changed original episode numbering')
                tried[pid].clear()
            episodes[pid] = episode
            # Only the initial unaccepted scope can use the new eligibility.
            # Every offered hospital in that scope must be untried now, either
            # originally or through its explicit temporary recovery marker.
            if pid not in first_commits and pid not in first_accepts:
                _require(not set(row['inputs']['candidates'][i]['hospital_id']
                                 for i in range(len(row['inputs']['candidates']))) & tried[pid],
                         'ordinary selector bypassed the original tried set without an audited recovery reopening')
            next_reviews[pid] = (row['decision']['next_review_at'] - parent.CLOCK_ORIGIN_SECONDS) / 60
            observed_reviews[pid] = now
            wake_hint.pop(pid, None)
            if row['inputs']['max_new'] == 0 and not row['inputs']['patient_pending'] \
                    and not any(patient == pid and expiry > now for (patient, _), expiry in workflow.offers.items()):
                tried[pid].clear()
        elif kind == 'recovery_reopen_review':
            index = event['record_index']
            _require(index == len(used_reviews) and index < len(reviews), 'missing, duplicate or reordered recovery review')
            row = reviews[index]
            _require(set(row) == {'snapshot', 'decision', 'event_sequence'} and row['event_sequence'] == seq,
                     'recovery review carries unknown fields or another source marker')
            snapshot = row['snapshot']
            _require(type(snapshot) is dict and set(snapshot) == {
                't', 'variant', 'patient', 'scheduling', 'tried_hospitals', 'pending_inquiries',
                'valid_offers', 'candidates', 'contacts', 'declines', 'transitions', 'consumptions'},
                'recovery snapshot contains unknown, future or missing fields')
            condition = late._condition(workflow, pid)
            own = own_handles(pid)
            offers = [dict(hospital=hospital, expires_at=expiry)
                      for (patient, hospital), expiry in sorted(workflow.offers.items()) if patient == pid]
            active = workflow.states.get(pid) == 'SEARCH' and pid not in first_commits and pid not in first_accepts
            # The inherited exhausted-pool path clears tried without emitting
            # a dedicated event. Permit that whole-set clear only when no work
            # or offer remains and the entire original structural pool was
            # already tried. Partial historical burden deletion is rejected.
            if active and not snapshot['tried_hospitals'] and tried[pid] and not own \
                    and not any(o['expires_at'] > now for o in offers) \
                    and {r['hospital'] for r in current_candidates(pid, now, narrow=False)} <= tried[pid]:
                tried[pid].clear()
            retry_at = snapshot['scheduling'].get('retry_at')
            _require(retry_at is None or joint._clock(retry_at), 'original retry deadline is not a valid public scheduling clock')
            expected = dict(t=now, variant=branch['recovery_dispatch_variant'],
                patient=dict(id=pid, state=workflow.states[pid], first_commit_t=first_commits.get(pid),
                    first_accept_event_sequence=first_accepts.get(pid), condition=condition,
                    condition_fingerprint=late._fingerprint(condition), request_event_sequence=requests[pid]),
                scheduling=dict(episode_id=episodes.get(pid, 0),
                    episode_contacts=episode_contacts[pid, episodes.get(pid, 0)], retry_at=retry_at,
                    next_policy_review_at=next_reviews.get(pid, 0), last_contact_at=last_contacts.get(pid)),
                tried_hospitals=sorted(tried[pid]) if active else deepcopy(snapshot['tried_hospitals']),
                pending_inquiries=own, valid_offers=offers, candidates=current_candidates(pid, now),
                contacts=[deepcopy(contact) for contact in branch['retry_dispatch_contacts']
                          if contact['patient'] == pid and contact['ask_event_sequence'] < seq],
                declines=prefixes[seq], transitions=transition_prefixes[seq],
                consumptions=[deepcopy(value) for value in consumptions if value['patient'] == pid])
            _literal(snapshot, expected, 'recovery input differs from original public workflow, report history or contact clocks')
            reproduced = decide_recovery_reopen(deepcopy(expected))
            _literal(row['decision'], reproduced, 'recovery decision differs from the fixed current-information rule')
            if active and branch['recovery_dispatch_variant'] == 'latched' and phase == 'after_treatments' \
                    and not (wake_hint.get(pid) is not None and wake_hint[pid][0] == now):
                _require(reproduced['hospital'] is not None,
                         'extra latched after-tick call entered the normal lane without an eligible recovery token')
            _require(event == dict(t=now, type=kind, event_sequence=seq, patient=pid,
                variant=branch['recovery_dispatch_variant'], record_index=index), 'recovery review raw marker differs')
            used_reviews.add(index)
            if reproduced['hospital'] is not None:
                following = indexed.get(seq + 1)
                _require(following is not None and following['type'] == 'recovery_reopen',
                         'eligible recovery did not emit its immediate explicit eligibility reopening')
            else:
                # The snapshot precedes the actual original send_asks call.
                # Reproduce its entry transition and its no-candidate path;
                # that path emits neither a selector record nor an ask.
                normal_entry(pid, now)
                scheduling = expected['scheduling']
                original_ready = scheduling['retry_at'] is None or scheduling['retry_at'] <= now
                no_work = not own and not offers
                pool = {candidate['hospital'] for candidate in current_candidates(pid, now, include_far=True)}
                if now >= next_reviews.get(pid, 0) and original_ready \
                        and observed_reviews.get(pid) != now and no_work and pool and pool <= tried[pid]:
                    tried[pid].clear()
                    observed_reviews[pid] = now
        elif kind == 'recovery_reopen':
            review_seq = event['review_event_sequence']
            source = indexed.get(review_seq)
            _require(source is not None and review_seq == seq - 1 and source['type'] == 'recovery_reopen_review',
                     'recovery reopening lacks its immediately preceding current review')
            row = reviews[source['record_index']]
            decision = row['decision']
            _literal(event, dict(t=now, type=kind, event_sequence=seq, patient=pid, hospital=decision['hospital'],
                token_id=decision['token_id'], review_event_sequence=review_seq), 'recovery action changed its chosen token')
            _require(hid in tried[pid] and len(tried[pid]) >= 2 and pid not in frames,
                     'recovery removed an untried target, reopened overlapping work or renewed an empty episode')
            frames[pid] = dict(snapshot=deepcopy(row['snapshot']), event=deepcopy(event), before_tried=sorted(tried[pid]))
            tried[pid].discard(hid)
        elif kind == 'recovery_retry_wake':
            frame = frames.get(pid)
            _require(frame is not None and branch['recovery_dispatch_variant'] == 'latched',
                     'retry interruption lacks a latched current recovery action')
            scheduling = frame['snapshot']['scheduling']
            _require(scheduling['retry_at'] is not None and scheduling['retry_at'] > now,
                     'recovery wake invented or extended an original retry wait')
            _literal(event, dict(t=now, type=kind, event_sequence=seq, patient=pid,
                hospital=frame['event']['hospital'], token_id=frame['event']['token_id'],
                previous_retry_at=scheduling['retry_at'], review_event_sequence=frame['event']['review_event_sequence']),
                'recovery wake changed original retry scheduling evidence')
        elif kind == 'recovery_reopen_result':
            frame = frames.pop(pid, None)
            _require(frame is not None, 'orphan recovery result without an actual temporary reopening')
            reopen = frame['event']
            seqs = [row['event_sequence'] for row in events
                    if reopen['event_sequence'] < row['event_sequence'] < seq and row['t'] == now
                    and row['type'] == 'ask' and row.get('patient') == pid]
            targets = [number for number in seqs if indexed[number]['hospital'] == hid]
            _require(len(targets) <= 1, 'one recovery token sent multiple actual target inquiries')
            target = targets[0] if targets else None
            consumed = target is not None
            restored = not consumed
            if restored:
                tried[pid].add(hid)
            original = frame['snapshot']['scheduling']
            interrupted = branch['recovery_dispatch_variant'] == 'latched' \
                and original['retry_at'] is not None and original['retry_at'] > now
            wake_events = [row for row in events if reopen['event_sequence'] < row['event_sequence'] < seq
                           and row['type'] == 'recovery_retry_wake' and row.get('patient') == pid]
            _require(len(wake_events) == int(interrupted), 'original retry wait interruption was missing or duplicated')
            _literal(event, dict(t=now, type=kind, event_sequence=seq, patient=pid, hospital=hid,
                review_event_sequence=reopen['review_event_sequence'], reopen_event_sequence=reopen['event_sequence'],
                actual_target_ask_event_sequence=target, target_restored=restored, token_consumed=consumed),
                'recovery result altered actual transmission, restoration or once-only consumption')
            expected_action = dict(review_event_sequence=reopen['review_event_sequence'],
                reopen_event_sequence=reopen['event_sequence'], result_event_sequence=seq,
                before_tried=frame['before_tried'], after_tried=sorted(tried[pid]),
                original_retry_at=original['retry_at'],
                after_retry_at=None if interrupted and seqs else original['retry_at'], retry_interrupted=interrupted,
                original_episode_id=original['episode_id'], after_episode_id=episodes.get(pid, 0),
                original_episode_contacts=original['episode_contacts'],
                after_episode_contacts=episode_contacts[pid, episodes.get(pid, 0)],
                original_inquiry_event_sequences=[row['event_sequence'] for row in events
                    if reopen['event_sequence'] < row['event_sequence'] < seq and row['t'] == now
                    and row['type'] == 'joint_inquiry_policy' and row.get('patient') == pid],
                actual_new_ask_event_sequences=seqs, actual_target_ask_event_sequence=target,
                target_restored=restored, token_consumed=consumed)
            _require(len(used_results) < len(reopens), 'actual reopening result omitted its exported record')
            _literal(reopens[len(used_results)], expected_action, 'recovery record changed the original tried set, budget or real normal inquiries')
            _require(expected_action['original_episode_id'] == expected_action['after_episode_id']
                     and expected_action['after_episode_contacts'] == original['episode_contacts'] + len(seqs),
                     'temporary reopening reset or bypassed the original episode contact budget')
            expected_tried = set(frame['before_tried']) | {indexed[number]['hospital'] for number in seqs}
            _require(set(tried[pid]) == expected_tried, 'recovery erased other tried hospitals or failed to restore its unsent target')
            if consumed:
                consumptions.append(dict(patient=pid, hospital=hid, token_id=reopen['token_id'],
                    condition_fingerprint=frame['snapshot']['patient']['condition_fingerprint'],
                    ask_event_sequence=target, review_event_sequence=reopen['review_event_sequence'],
                    reopen_event_sequence=reopen['event_sequence']))
            used_results.add(seq)
        workflow.apply(event)
    _require(not frames and used_reviews == set(range(len(reviews))) and len(used_results) == len(reopens),
             'orphan recovery reviews, unfinished eligibility edits or extra action records')
    _literal(branch['recovery_dispatch_consumptions'], consumptions, 'token export differs from actual new target contacts')
    counts = Counter((row['patient'], row['condition_fingerprint']) for row in consumptions)
    _require(all(value <= 1 for value in counts.values()), 'one patient condition consumed multiple recovery reopens')
    metrics = branch['metrics']
    _require(metrics['recovery_reopen_reviews'] == len(reviews)
             and metrics['recovery_reopen_attempts'] == len(reopens)
             and metrics['recovery_reopen_sent'] == len(consumptions), 'recovery metrics differ from full raw actions')
    return dict(recovery_reopen_reviews=len(reviews), recovery_reopen_attempts=len(reopens),
                recovery_reopen_sent=len(consumptions), recovery_semantic_report_transitions=len(branch['recovery_dispatch_resource_transitions']))


def audit_branch(scenario, branch, *, exact_limit=20000):
    """Pass the original full branch to the unchanged clinical/physical auditors."""
    namespace = {key for key in branch if key.startswith('recovery_dispatch_')}
    if not namespace:
        _require(not str(branch.get('id', '')).startswith('recovery_'),
                 'recovery candidate omitted its explicit intervention namespace')
        return parent.audit_branch(scenario, branch, exact_limit=exact_limit)
    _require(namespace == NAMESPACE_FIELDS, 'missing or unknown recovery dispatch namespace')
    _require(branch['recovery_dispatch_enabled'] is True and branch['recovery_dispatch_variant'] in VARIANTS,
             'explicit known recovery variant required')
    _literal(branch['recovery_dispatch_spec'], RECOVERY_DISPATCH_SPEC, 'recovery protocol differs')
    retained = parent.NAMESPACE_FIELDS - {'retry_dispatch_foundation_spec'}
    _require({key for key in branch if key.startswith('retry_dispatch_')} == retained,
             'recovery candidate changed retained frontier records or falsely claims an unchanged actual subclass')
    _require(branch['retry_dispatch_enabled'] is True and branch['retry_dispatch_selector'] == 'frontier',
             'recovery requires the frozen frontier kernel')
    _literal(branch['retry_dispatch_spec'], parent.RETRY_DISPATCH_SPEC, 'frozen frontier protocol differs')
    _require(not any(key.startswith('capability_') for key in branch), 'recovery falsely claims original capability delegation')
    queue = scenario.get('inquiry_queue', {})
    _require(type(queue) is dict and type(queue.get('enabled', False)) is bool, 'queue support must remain a declared boolean')
    enabled = queue.get('enabled', False)
    mode = 'cooperative_queue' if enabled else 'independent_replies'
    _literal(branch['recovery_dispatch_foundation_spec'], dict(RECOVERY_FOUNDATION_SPEC,
        base_dispatch_foundation_spec=dict(parent.RETRY_DISPATCH_FOUNDATION_SPEC, mode=mode)),
        'recovery foundation hides its actual subclass or original workflow')
    _require(branch.get('inquiry_selector') == 'v4' and branch.get('policy', {}).get('rule') in ('H1', 'C1')
             and branch['policy'].get('name') == branch.get('id'), 'actual identity, selector slot or allocation changed')
    _require(all(branch.get(flag) is True for flag in ('shortage_probe_enabled', 'late_pending_guard_enabled',
        'late_reply_recovery_enabled', 'cohort_registration_enabled', 'scarce_routing_enabled')),
        'recovery changed the fixed safe-probe foundation')
    expected_scarce = dict(inquiry='v4', allocation=branch['policy']['rule'], cohort_registration=True,
        scarce_routing=True, actual_action='parallel_scarce_supplement', original_selector_candidates_unchanged=False,
        maximum_supplements_per_patient=1, recovery=True, pending_protection=True,
        pending_window_minutes=1, time_step_seconds=15, actual_selector_reused=False,
        original_seven_inputs_retained=True, base_selector_reused_as_kernel=True,
        extra_resource_change_publications=False)
    _literal(branch.get('scarce_delay_spec'), expected_scarce, 'recovery concealed changed eligibility or foundation')
    if enabled:
        _require(branch.get('cooperative_probe_enabled') is True and branch.get('cooperative_queue_enabled') is True
                 and branch.get('comparison_id') == 'cooperative_probe_20261017', 'complete cooperative workflow required')
    else:
        _require(not any(key.startswith('cooperative_') for key in branch)
                 and branch.get('comparison_id') == 'shortage_probe_20261017'
                 and branch.get('inquiry_queue_report') is None, 'independent candidate invented queue workers')
    kinds = {'recovery_reopen_review', 'recovery_reopen', 'recovery_retry_wake', 'recovery_reopen_result'}
    _require(all(not row['type'].startswith('recovery_') or row['type'] in kinds for row in _indexed_events(branch)),
             'unknown recovery event or hidden intervention')
    recovery_counts = _audit_recovery_trace(scenario, branch)
    decisions, frontier_counts = parent._audit_policy_contexts(scenario, branch)
    checked = (parent._audit_cooperative_branch if enabled else parent._audit_independent_branch)(
        scenario, branch, decisions, exact_limit=exact_limit)
    checked['counts'].update(frontier_counts)
    checked['counts'].update(recovery_counts)
    checked['selector_check'] = ('actual frontier and recovery eligibility reproduced from complete raw public inputs; '
        'full raw clinical responses, FIFO work, deadlines, actual contacts and allocation retained')
    checked['recovery_clock_scope'] = ('retry_at is bound to its original raw wake/action value and current clock; '
        'not an independent proof of every unlogged internal retry_at creation')
    return checked


def audit_recovery_dispatch_replay(result, *, exact_limit=20000):
    _require(type(result.get('schema_version')) is int and result['schema_version'] == 1
             and result.get('experiment_id') == EXPERIMENT_ID, 'unknown recovery replay schema')
    ids = [row['id'] for row in result['branches']]
    _require(ids and len(ids) == len(set(ids)) and len(result['execution_order']) == len(ids)
             and set(result['execution_order']) == set(ids), 'invalid newly executed recovery replay branches')
    counts, details = Counter(), {}
    for branch in result['branches']:
        checked = audit_branch(result['scenario'], branch, exact_limit=exact_limit)
        counts.update(checked['counts']); details[branch['id']] = checked
    return dict(passed=True, counts=dict(counts), branches=len(ids), branch_audits=details,
                assignment_constraint_violations=0, actual_optimality_claimed=False)

