"""Independent full-raw audit of an explicitly guarded optional probe.

Only optional-probe completeness is replaced by a reconstructed guard rule.
Every frozen physical, information, FIFO, clinical, ordinary-inquiry and
allocation auditor receives the exact same original branch object. No engine,
solver, raw projection or compatibility mutation is used by this module.
"""
from collections import Counter
from copy import deepcopy
from . import recovery_rank_audit as rank

parent = rank.parent
base, information, cooperative = rank.base, rank.information, rank.cooperative
independent, joint = rank.independent, rank.joint
scarce, recovery, safe = parent.scarce, parent.recovery, parent.safe
_require, _indexed_events, _literal = rank._require, rank._indexed_events, rank._literal
_end, _views, _position = cooperative._end, cooperative._views, cooperative._position
_is_callback_accept = cooperative._is_callback_accept
_probe_patient_view = cooperative._probe_patient_view
_probe_review_due, _probe_expected = cooperative._probe_review_due, cooperative._probe_expected
_busy_expected, _after_queue_events = cooperative._busy_expected, cooperative._after_queue_events
_difference = independent._difference
ProbeGuardAuditError = rank.RecoveryRankAuditError
EXPERIMENT_ID = 'probe_guard_20261020'
VARIANTS = ('off', 'renewed')
NAMESPACE_FIELDS = {'probe_guard_enabled', 'probe_guard_variant', 'probe_guard_spec',
    'probe_guard_foundation_spec', 'probe_guard_base_contract', 'probe_guard_records'}


PROBE_GUARD_SPEC = dict(
    schema_version=1, variants=list(VARIANTS),
    original_probe_policy='simulation.shortage_probe_engine.decide_shortage_probe',
    original_probe_decision_is_shadow_only=True,
    off='veto_every_due_optional_shortage_probe_review',
    renewed='veto_original_selected_bed_shortage_observed_strictly_after_first_actual_ask',
    comparison_clock='ed_bed.observed_at > patient.first_ask.submitted_at',
    additional_age_or_quantity_threshold=False,
    original_full_snapshot_retained=True, original_probe_selection_unchanged=True,
    delegate_calls_original_probe_hook=True, veto_submits_no_probe=True,
    normal_recovery_rank_selector_unchanged=True,
    original_due_cycles_contacts_attempts_clock_fifo_workers_and_resources_retained=True,
    extra_resource_change_publications=False, future_response_or_resource_information=False)

PROBE_GUARD_FOUNDATION_SPEC = dict(
    actual_replays=dict(
        independent_replies='simulation.probe_guard_engine.IndependentProbeGuardReplay',
        cooperative_queue='simulation.probe_guard_engine.CooperativeProbeGuardReplay'),
    base_replays=dict(
        independent_replies='simulation.recovery_rank_engine.IndependentNormalRecoveryRankReplay',
        cooperative_queue='simulation.recovery_rank_engine.CooperativeNormalRecoveryRankReplay'),
    rank_variant='normal', actual_normal_selector_changed=False,
    changed_hook='_maybe_shortage_probe',
    original_due_review_records='full snapshot, shadow original decision and actual guard decision',
    shadow_decision_executes_no_inquiry=True,
    inherited_protocol_descriptions_are_nested_base_contract=True,
    original_normal_calls_readiness_offers_routing_and_queue_rules_unchanged=True,
    actual_contact_attempt_episode_and_last_contact_accounting_unchanged=True,
    extra_resource_change_publications=False, added_workers_or_beds=False)


def _guard_expected(variant, snapshot, shadow):
    """Reconstruct the new rule without importing the producer's pure guard."""
    chosen = shadow['send'][0] if shadow['send'] else None
    first = snapshot['patient']['first_ask']
    first_t = first['submitted_at'] if first is not None else None
    observed = None
    if chosen is not None:
        row = next(c for c in snapshot['candidates'] if c['hospital'] == chosen)
        observed = row['resources']['ed_bed']['observed_at']
    if variant == 'off':
        action, reason = 'veto', 'ALL_OPTIONAL_PROBES_DISABLED'
    elif chosen is None:
        action, reason = 'delegate', 'NO_ORIGINAL_PROBE_SELECTED'
    elif observed > first_t:
        action, reason = 'veto', 'POST_INITIAL_ASK_SHORTAGE_OBSERVATION'
    else:
        action, reason = 'delegate', 'ORIGINAL_PROBE_REVIEW_ALLOWED'
    return dict(schema_version=1, variant=variant, action=action, reason=reason,
        original_send=list(shadow['send']), selected_hospital=chosen,
        first_ask_submitted_at=first_t, selected_bed_observed_at=observed)


def audit_probes(scenario, branch):
    """Rebuild public probe inputs, every explicit recheck, and its actual ask."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    records = branch['shortage_probe_records']
    _require(isinstance(records, list), 'probe records must be a list')
    enabled = scenario.get('inquiry_queue', {}).get('enabled', False)
    workflow = (cooperative._LateWorkflow if enabled else independent._LateWorkflow)(deepcopy(scenario))
    hospitals = {row['id']: row for row in scenario['hospitals']}
    requests, handles, accepted, first_asks, first_commits, last_contacts = {}, {}, {}, {}, {}, {}
    inquiries, contacts, probe_counts = Counter(), Counter(), Counter()
    episodes, tried, used, ask_sequences, check_sequences = {}, {}, set(), set(), set()
    pending_targets, cycles, sent = [], [], 0
    retry_records=branch.get('cooperative_busy_retry_records', [])
    guard_records = branch['probe_guard_records']
    _require(type(guard_records) is list, 'complete ordered guard records required')
    guard_used, delegated, vetoed = set(), {}, 0
    used_retries,retry_counts,retry_ask_sequences=set(),Counter(),set()
    latest_acks,seen_selectors,retry_targets={},{},[]
    normal_reviews, ever_accepted = {}, set()

    def initial_search(pid):
        return pid not in ever_accepted and pid not in first_commits

    def own_pending(pid):
        return any(patient == pid for patient, _ in handles)

    def has_offer(pid):
        return any(patient == pid for patient, _ in workflow.offers)

    def structural_pool(pid):
        patient = workflow._base_patient(pid)
        return {hospital for hospital in patient['compatible_hospitals']
            if hospital not in workflow.failed.get(pid, set()) and all(
                hospitals[hospital].get('capacity', {}).get(resource, 0) >= amount
                for resource, amount in patient['demand'].items())}

    def enter_normal_review(pid, now):
        # Original no-candidate/retry entry advances an episode only after
        # its tried set was genuinely cleared and prior actual contacts exist.
        episode = episodes.get(pid, 0)
        if now >= normal_reviews.get(pid, 0) and not tried.get(pid, set()) and contacts[pid, episode]:
            episodes[pid] = episode + 1

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
            tried.setdefault(pid, set())
        elif kind in ('resource_event_review', 'report_driven_wake') and initial_search(pid):
            # A real original report wake reopens precisely its own hospital.
            # It is not a cumulative-contact discount or a rank intervention.
            tried.setdefault(pid, set()).discard(hid)
            enter_normal_review(pid, now)
        elif kind == 'joint_inquiry_policy':
            seen_selectors[pid]=branch['inquiry_decisions'][event['record_index']]
            if initial_search(pid):
                enter_normal_review(pid, now)
                row = seen_selectors[pid]
                _require(row['inquiry_episode'] == episodes.get(pid, 0),
                         'guard history differs from original normal episode entry')
                _require(not {candidate['hospital_id'] for candidate in row['inputs']['candidates']}
                         & tried.get(pid, set()), 'guard history reopened an originally tried normal candidate')
                normal_reviews[pid] = (row['decision']['next_review_at'] - parent.CLOCK_ORIGIN_SECONDS) / 60
                if row['inputs']['max_new'] == 0 and not own_pending(pid) and not has_offer(pid):
                    tried.setdefault(pid, set()).clear()
        elif kind == 'cooperative_probe_control_ack':
            compact={key:deepcopy(event[key]) for key in ('hospital','attempt','ask_event_sequence','job_id','state','received_at')}
            compact['control_event_sequence']=sequence
            latest_acks[pid,hid,event['attempt']]=compact
        elif kind == 'ask':
            handle = dict(hospital=hid, attempt=event['attempt'], submitted_at=now,
                timeout_at=event['timeout_at'], ask_event_sequence=sequence)
            first_asks.setdefault(pid, deepcopy(handle))
            handles[pid, hid] = handle
            inquiries[pid] += 1
            if initial_search(pid):
                _require(event['inquiry_episode'] == episodes.get(pid, 0),
                         'guard actual contact differs from reconstructed normal episode')
            episodes[pid] = event['inquiry_episode']
            contacts[pid, event['inquiry_episode']] += 1
            last_contacts[pid] = now
            if not enabled or event.get('cooperative_job_id') is not None:
                tried.setdefault(pid, set()).add(hid)
        elif kind == 'cooperative_probe_not_admitted':
            handles.pop((pid, hid), None)
        elif kind in ('accept', 'decline', 'no_response'):
            if not _is_callback_accept(event, indexed):
                handles.pop((pid, hid), None)
            if kind == 'accept':
                accepted[pid, hid] = event
                ever_accepted.add(pid)
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
            _require(not pending_targets and not retry_targets and set(event) == {'t', 'type', 'event_sequence'},
                     'previous probe cycle omitted reviews or cycle has unknown fields')
            cycles.append(now)
            pending_targets = [patient for patient in sorted(requests)
                               if _probe_review_due(snapshot(patient, now))]
            normal_ready={row['id'] for row in workflow.snapshot(now)['ready']}
            for patient in sorted(requests):
                current=snapshot(patient,now);p=current['patient']
                acks=[ack for (other,_,_),ack in latest_acks.items() if other==patient and ack['state']=='not_admitted']
                selector=seen_selectors.get(patient)
                if p['state']=='SEARCH' and p['first_commit_t'] is None and patient not in normal_ready \
                    and p['pending_inquiries'] and p['inquiry_episode']==0 and retry_counts[patient]==0 \
                    and len(acks)==1 and selector is not None and selector['t']==now:
                    views=_views('observed',workflow.observations,workflow.truth,acks[0]['hospital'],p['demand'],now)
                    if any(view['received_at']==now for view in views.values()):retry_targets.append(patient)
        elif kind == 'cooperative_busy_retry_review':
            _require(retry_targets and pid==retry_targets.pop(0), 'busy retry omitted current evidence or its required review')
            index=event['record_index']
            _require(index==len(used_retries) and index<len(retry_records), 'missing or reordered busy retry record')
            used_retries.add(index);record=retry_records[index]
            _require(set(event)=={'t','type','event_sequence','patient','record_index'}
                and set(record)=={'snapshot','result','event_sequence'} and record['event_sequence']==sequence,
                'busy retry marker or record has unknown fields')
            ack=next(ack for (other,_,_),ack in latest_acks.items() if other==pid and ack['state']=='not_admitted')
            selector=seen_selectors[pid]
            expected_snapshot=dict(opportunity_snapshot=snapshot(pid,now),not_admitted_ack=deepcopy(ack),
                retry_count=retry_counts[pid],ordinary_selector_event_sequence=selector['event_sequence'],
                offered_hospitals=[row['hospital_id'] for row in selector['inputs']['candidates']],
                ordinary_send=selector['decision']['send'],max_new=selector['inputs']['max_new'])
            _require(base._same(record['snapshot'],expected_snapshot),
                'busy retry rewrote the actual contact clock, selector, control ACK, normal tried set, budget or new resource report')
            expected=_busy_expected(expected_snapshot)
            _require(base._same(record['result'],expected), 'busy retry lacks an independent newly sufficient public report')
            actual=[row for row in events if row.get('cooperative_busy_retry_review_event_sequence')==sequence]
            _require([row['hospital'] for row in actual]==expected['send'], 'busy retry differs from its actual normal contacts')
            if expected['send']:
                target=expected['send'][0]
                resources=next(row['resources'] for row in expected_snapshot['opportunity_snapshot']['candidates'] if row['hospital']==target)
                check=indexed.get(sequence+1);ask=_after_queue_events(indexed,sequence+1)
                _require(check is not None and base._same(check,dict(t=now,type='information_check',event_sequence=sequence+1,
                    patient=pid,hospital=target,purpose='inquiry',eligible=True,reason=None,resources=resources)),
                    'new normal retry bypassed its unchanged sufficient inquiry-resource check')
                _require(ask is not None and ask==actual[0] and ask['patient']==pid and ask['t']==now
                    and ask.get('cooperative_probe_request') is False and ask.get('cooperative_job_id') is not None,
                    'busy retry lacks an actual ordinary admitted clinical job')
                retry_counts[pid]+=1;retry_ask_sequences.add(ask['event_sequence'])
        elif kind == 'probe_guard_review':
            _require(pending_targets and pid == pending_targets.pop(0),
                     'guard review missing, duplicate or outside original due cycle')
            index = event['record_index']
            _require(type(index) is int and index == len(guard_used) and index < len(guard_records),
                     'missing or reordered guard record')
            guard_used.add(index)
            record = guard_records[index]
            _require(set(event) == {'t', 'type', 'event_sequence', 'patient', 'variant', 'record_index'}
                     and event['variant'] == branch['probe_guard_variant']
                     and set(record) == {'event_sequence', 'snapshot', 'shadow_decision', 'guard_decision'}
                     and record['event_sequence'] == sequence, 'guard marker/record schema or variant differs')
            current = snapshot(pid, now)
            _literal(record['snapshot'], current, 'guard snapshot differs from raw public history')
            shadow = _probe_expected(current)
            _literal(record['shadow_decision'], shadow, 'guard shadow differs from independent original probe rule')
            expected_guard = _guard_expected(branch['probe_guard_variant'], current, shadow)
            _literal(record['guard_decision'], expected_guard, 'guard differs from independent timestamp rule')
            following = indexed.get(sequence + 1)
            if expected_guard['action'] == 'delegate':
                _require(following is not None and following['type'] == 'shortage_probe_review'
                         and following['patient'] == pid and following['t'] == now,
                         'delegated guard omitted its immediate original probe review')
                delegated[sequence + 1] = current, shadow
            else:
                vetoed += 1
                _require(following is None or following['type'] != 'shortage_probe_review',
                         'veto emitted original optional probe review')
        elif kind == 'shortage_probe_review':
            _require(sequence in delegated, 'original probe review lacks a prior actual delegated guard')
            index = event['record_index']
            _require(index == len(used) and index < len(records), 'missing or reordered shortage-probe record')
            used.add(index)
            record = records[index]
            _require(set(event) == {'t', 'type', 'event_sequence', 'patient', 'record_index'}
                     and set(record) == {'snapshot', 'result', 'event_sequence'}
                     and record['event_sequence'] == sequence, 'probe record or marker carries unknown fields')
            expected_snapshot, expected = delegated[sequence]
            _literal(snapshot(pid, now), expected_snapshot, 'delegation changed the public probe snapshot')
            if not base._same(record['snapshot'], expected_snapshot):
                _require(False, 'probe input differs from public registration, actual inquiries/offers or original shortage reports: '
                         + _difference(record['snapshot'], expected_snapshot))
            _require(base._same(record['result'], expected), 'probe decision differs from independent bounded public recheck rule')
            following = indexed.get(sequence + 1)
            if expected['send']:
                chosen = expected['send'][0]
                resources = next(row['resources'] for row in expected_snapshot['candidates'] if row['hospital'] == chosen)
                _require(following is not None and base._same(following, dict(t=now, type='information_check',
                    event_sequence=sequence + 1, patient=pid, hospital=chosen, purpose='shortage_probe',
                    eligible=True, reason='REPORTED_BED_SHORTAGE_RECHECK', resources=resources)),
                    'probe lacks exactly its original fresh-shortage check with explicit new purpose')
                matching = [row for row in events if row.get('shortage_probe_review_event_sequence') == sequence]
                _require(len(matching) == 1, 'probe does not have exactly one real inquiry')
                ask = matching[0]
                _require(ask is not None and ask['type'] == 'ask' and ask['t'] == now
                    and ask['patient'] == pid and ask['hospital'] == chosen
                    and ask.get('shortage_probe_review_event_sequence') == sequence
                    and ask['event_sequence'] > sequence + 1
                    and (enabled or ask['event_sequence'] == sequence + 2)
                    and all(indexed[number]['type'].startswith('cooperative_job_')
                        or indexed[number]['type']=='cooperative_probe_control_ack'
                        for number in range(sequence+2,ask['event_sequence'])),
                    'eligible shortage recheck lacks uniquely referenced immediate actual inquiry')
                ask_sequences.add(ask['event_sequence']); check_sequences.add(sequence + 1)
                probe_counts[pid] += 1
                _require(probe_counts[pid] == 1, 'more than one shortage probe per patient')
                sent += 1
            else:
                _require(following is None or following['type'] != 'information_check'
                    or following.get('purpose') != 'shortage_probe', 'ineligible recheck emitted a probe')
        workflow.apply(event)
        if kind in ('decline', 'no_response', 'expire') and initial_search(pid) \
                and not own_pending(pid) and not has_offer(pid) and now >= normal_reviews.get(pid, 0):
            pool = structural_pool(pid)
            if pool and pool <= tried.get(pid, set()):
                # resolve_asks enters the unchanged exhausted-candidate path
                # immediately, before a subsequent selector or guard review.
                tried.setdefault(pid, set()).clear()
    _require(not pending_targets and not retry_targets and used == set(range(len(records)))
        and used_retries==set(range(len(retry_records)))
        and guard_used == set(range(len(guard_records)))
        and len(delegated) == len(used), 'orphan or omitted guard/probe/retry record')
    _require(retry_ask_sequences=={row['event_sequence'] for row in events if 'cooperative_busy_retry_review_event_sequence' in row},
        'orphan or fabricated busy-retry normal inquiry')
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
    if enabled:
        _require(branch['metrics']['cooperative_busy_retries']==sum(retry_counts.values()), 'busy retry counter differs from actual new normal work')
    _require(branch['metrics']['probe_guard_reviews'] == len(guard_records)
             and branch['metrics']['probe_guard_vetoes'] == vetoed
             and branch['metrics']['probe_guard_delegations'] == len(delegated)
             and branch['metrics']['probe_guard_shadow_selected'] == sum(bool(r['shadow_decision']['send']) for r in guard_records),
             'guard metrics differ from actual reviews/vetoes/delegations')
    return dict(shortage_probe_cycles=len(cycles), shortage_probe_reviews=len(records), shortage_probe_sent=sent,
                explicit_shortage_checks=len(check_sequences),cooperative_busy_retry_reviews=len(retry_records),
                cooperative_busy_retries=sum(retry_counts.values()),
                probe_guard_reviews=len(guard_records), probe_guard_vetoes=vetoed)



def _audit_independent_branch(scenario, branch, policy_decisions, *, exact_limit=20000):
    """Audit original, unmodified emissions including separately received offers."""
    probe_enabled = branch.get('shortage_probe_enabled', False)
    _require(type(probe_enabled) is bool, 'shortage probe mode must be explicit')
    if not probe_enabled:
        _require(not branch['probe_guard_base_contract'].get('shortage_probe_spec') and not branch.get('shortage_probe_records')
                 and not branch.get('shortage_probe_nonblocking_records')
                 and not any(event['type'].startswith('shortage_probe_') or 'shortage_probe_review_event_sequence' in event
                     or (event['type'] == 'information_check' and event['purpose'] == 'shortage_probe')
                     for event in _indexed_events(branch)), 'disabled branch contains shortage intervention')
        return independent.prior.audit_branch(scenario, branch, exact_limit=exact_limit)
    _require(branch['probe_guard_base_contract'].get('shortage_probe_spec') == independent.SHORTAGE_PROBE_SPEC,
             'shortage-probe protocol differs from the explicit bounded recheck contract')
    enabled = branch.get('late_pending_guard_enabled', False)
    _require(type(enabled) is bool, 'pending-work protection mode must be explicit')
    if not enabled:
        _require(not branch.get('deadline_safe_spec'), 'disabled branch contains pending-work protocol')
        raise ProbeGuardAuditError('probe intervention requires pending-safe base')
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
    probe_counts = audit_probes(deepcopy(scenario), branch)
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
        _require(not branch['probe_guard_base_contract'].get('cooperative_probe_spec') and not branch.get('cooperative_job_ledger')
            and not branch.get('cooperative_control_acks') and not branch.get('cooperative_ready_records')
            and not branch.get('cooperative_busy_retry_records')
            and not any(event['type'].startswith('cooperative_') or 'cooperative_job_id' in event
                for event in _indexed_events(branch)), 'disabled reference branch contains cooperative behavior')
        return independent.audit_branch(scenario,branch,exact_limit=exact_limit)
    _require(branch['probe_guard_base_contract'].get('cooperative_probe_spec') == cooperative.COOPERATIVE_PROBE_SPEC,
             'cooperative workflow differs from the explicit current-control and conserved-work protocol')
    _require(branch['probe_guard_base_contract'].get('shortage_probe_spec')==cooperative.COOPERATIVE_SHORTAGE_SPEC,
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
        probe=audit_probes(deepcopy(scenario),branch)
        callback=cooperative.audit_late_offers(deepcopy(scenario),branch,work)
        inquiries=parent._audit_cooperative_inquiries(deepcopy(scenario),branch,work,policy_decisions)
        backups=cooperative.audit_backups(deepcopy(scenario),branch,work)
    else:
        _require(not branch['shortage_probe_records'] and not any(
            event.get('shortage_probe_review_event_sequence') is not None for event in ordered),
            'queue-disabled environment gained an optional clinical inquiry')
        _require(not branch.get('cooperative_busy_retry_records'),'queue-disabled environment contains orphan busy retry records')
        probe=dict(shortage_probe_reviews=0,shortage_probe_sent=0)
        callback=safe.audit_late_offers(deepcopy(scenario),branch)
        inquiries=parent._audit_independent_inquiries(deepcopy(scenario),branch,policy_decisions)
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


def _audit_contract(branch, enabled):
    """Bind the actual wrapper and its honestly nested inherited protocols."""
    namespace = {key for key in branch if key.startswith('probe_guard_')}
    _require(namespace == NAMESPACE_FIELDS and branch['probe_guard_enabled'] is True,
             'missing, unknown or disabled probe-guard namespace')
    _require(branch['probe_guard_variant'] in VARIANTS,
             'known explicit optional-probe guard variant required')
    _literal(branch['probe_guard_spec'], PROBE_GUARD_SPEC, 'probe-guard rule contract differs')
    mode = 'cooperative_queue' if enabled else 'independent_replies'
    _literal(branch['probe_guard_foundation_spec'], dict(PROBE_GUARD_FOUNDATION_SPEC, mode=mode),
             'guard conceals its actual wrapper, mode or unchanged normal selector')
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
    _literal(branch['probe_guard_base_contract'], inherited,
             'guard changed or concealed its complete inherited base contract')
    popped_rank = {'recovery_rank_selector', 'recovery_rank_spec',
        'recovery_rank_foundation_spec', 'recovery_rank_base_contract'}
    _require({key for key in branch if key.startswith('recovery_rank_')}
             == rank.NAMESPACE_FIELDS - popped_rank
             and branch['recovery_rank_enabled'] is True and branch['recovery_rank_variant'] == 'normal',
             'guard must retain actual normal-rank facts and nest inherited protocol descriptions')
    _require(not any(key in branch for key in ('shortage_probe_spec', 'cooperative_probe_spec'))
             and branch.get('comparison_id') == EXPERIMENT_ID,
             'actual guard falsely claims an unchanged probe protocol identity')
    _require({key for key in branch if key.startswith('retry_dispatch_')} == {
        'retry_dispatch_enabled', 'retry_dispatch_records', 'retry_dispatch_contacts', 'retry_dispatch_wakes'}
        and branch['retry_dispatch_enabled'] is True,
        'guard drops factual dispatch records or falsely claims unchanged selector metadata')
    _require(not any(key.startswith(('capability_', 'recovery_dispatch_')) for key in branch),
             'normal guard invented a reopening or exact capability delegation')
    _require(branch.get('inquiry_selector') == 'v4' and branch.get('policy', {}).get('rule') in ('H1', 'C1')
             and branch['policy'].get('name') == branch.get('id')
             and type(branch.get('id')) is str and branch['id'],
             'guard actual execution identity, selector slot or allocation rule changed')
    _require(all(branch.get(flag) is True for flag in ('shortage_probe_enabled', 'late_pending_guard_enabled',
        'late_reply_recovery_enabled', 'cohort_registration_enabled', 'scarce_routing_enabled')),
        'guard changed a fixed complete foundation flag')
    _literal(branch.get('scarce_delay_spec'), dict(inquiry='v4', allocation=branch['policy']['rule'],
        cohort_registration=True, scarce_routing=True, actual_action='parallel_scarce_supplement',
        original_selector_candidates_unchanged=False, maximum_supplements_per_patient=1,
        recovery=True, pending_protection=True, pending_window_minutes=1, time_step_seconds=15,
        actual_selector_reused=False, original_seven_inputs_retained=True, base_selector_reused_as_kernel=True,
        extra_resource_change_publications=False), 'guard changed its unchanged scarce foundation')
    if enabled:
        _require(branch.get('cooperative_probe_enabled') is True and branch.get('cooperative_queue_enabled') is True,
                 'guard omitted the actual complete cooperative workflow')
    else:
        _require(not any(key.startswith('cooperative_') for key in branch)
                 and branch.get('inquiry_queue_report') is None, 'independent guard invented hospital workers')
    events = _indexed_events(branch)
    _require(not any(event['type'].startswith('recovery_') for event in events),
             'normal guard gained a recovery reopening or extra call')
    _require(all(not event['type'].startswith('probe_guard_') or event['type'] == 'probe_guard_review'
                 for event in events), 'unknown guard raw intervention event')


def audit_branch(scenario, branch, *, exact_limit=20000):
    """Audit a whole frozen reference or the exact new guarded raw branch."""
    namespace = {key for key in branch if key.startswith('probe_guard_')}
    if not namespace:
        _require(not str(branch.get('id', '')).startswith('probe_guard_')
                 and branch.get('comparison_id') != EXPERIMENT_ID,
                 'guard candidate omitted its actual intervention namespace')
        return rank.audit_branch(scenario, branch, exact_limit=exact_limit)
    queue = scenario.get('inquiry_queue', {})
    _require(type(queue) is dict and type(queue.get('enabled', False)) is bool,
             'original queue support must remain an explicit boolean')
    enabled = queue.get('enabled', False)
    _audit_contract(branch, enabled)
    decisions, counts = rank._audit_contexts(scenario, branch)
    counts.update(rank._audit_normal_episodes(scenario, branch))
    # Each component below receives this same unfiltered full branch. Only the
    # optional-probe completeness component is replaced by audit_probes above.
    checked = (_audit_cooperative_branch if enabled else _audit_independent_branch)(
        scenario, branch, decisions, exact_limit=exact_limit)
    checked['counts'].update(counts)
    checked['selector_check'] = ('unchanged normal recovery-rank selector on reconstructed original seven inputs; '
        'independent full-history optional guard, original shadow and every actual delegated/vetoed due review; '
        'same whole raw branch reaches frozen clinical, FIFO, physical and allocation auditors')
    return checked


def audit_probe_guard_replay(result, *, exact_limit=20000):
    _require(type(result.get('schema_version')) is int and result['schema_version'] == 1
             and result.get('experiment_id') == EXPERIMENT_ID, 'unknown probe-guard replay schema')
    ids = [branch['id'] for branch in result['branches']]
    _require(ids and all(type(code) is str and code for code in ids) and len(ids) == len(set(ids))
             and len(result['execution_order']) == len(ids) and set(result['execution_order']) == set(ids),
             'missing, duplicated or reordered guard replay identity')
    counts, details = Counter(), {}
    for branch in result['branches']:
        checked = audit_branch(result['scenario'], branch, exact_limit=exact_limit)
        counts.update(checked['counts']); details[branch['id']] = checked
    return dict(passed=True, counts=dict(counts), branches=len(ids), branch_audits=details,
        assignment_constraint_violations=0, actual_optimality_claimed=False)

