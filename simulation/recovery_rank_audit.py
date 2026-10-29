"""Full raw recovery-rank audit without replay engines, projection or solvers.

The original inquiry inputs, delivered observations and actual contact history
are independently reconstructed. Only the declared selector reproduction is
replaced; frozen clinical, physical, FIFO and allocation auditors receive the
original complete branch.
"""
from collections import Counter
from copy import deepcopy

from . import recovery_dispatch_audit as recovery

parent = recovery.parent
RecoveryRankAuditError = recovery.RecoveryDispatchAuditError
_require = recovery._require
_indexed_events = recovery._indexed_events
_literal = recovery._literal
base = recovery.base
information = recovery.information
cooperative = recovery.cooperative
independent = recovery.independent
late = recovery.late
joint = recovery.joint
EXPERIMENT_ID = 'recovery_rank_20261018'
VARIANTS = ('normal', 'latched')
NAMESPACE_FIELDS = {'recovery_rank_enabled', 'recovery_rank_variant', 'recovery_rank_selector',
    'recovery_rank_spec', 'recovery_rank_foundation_spec', 'recovery_rank_base_contract',
    'recovery_rank_declines', 'recovery_rank_resource_transitions', 'recovery_rank_records',
    'recovery_rank_consumptions'}
RECOVERY_RANK_FOUNDATION_SPEC = dict(
    actual_replays=dict(
        normal_independent='simulation.recovery_rank_engine.IndependentNormalRecoveryRankReplay',
        normal_cooperative='simulation.recovery_rank_engine.CooperativeNormalRecoveryRankReplay',
        latched_independent='simulation.recovery_rank_engine.IndependentLatchedRecoveryRankReplay',
        latched_cooperative='simulation.recovery_rank_engine.CooperativeLatchedRecoveryRankReplay'),
    base_replays=dict(
        normal_independent='simulation.retry_dispatch_engine.IndependentRetryDispatchReplay',
        normal_cooperative='simulation.retry_dispatch_engine.CooperativeRetryDispatchReplay',
        latched_independent='simulation.recovery_dispatch_engine.IndependentRecoveryDispatchReplay',
        latched_cooperative='simulation.recovery_dispatch_engine.CooperativeRecoveryDispatchReplay'),
    original_seven_inputs_and_full_raw_retained=True,
    inherited_retry_dispatch_records='actual recovery_rank output with original factual frontier context',
    inherited_retry_selector_spec_and_foundation_are_nested_base_contract=True,
    normal_reopening_or_added_after_tick=False, latched_eligibility_unchanged=True,
    actual_contact_accounting_episode_rules_clock_fifo_readiness_and_resources_unchanged=True,
    actual_selector_changed=True)
RECOVERY_RANK_SPEC = dict(schema_version=1, variants=list(VARIANTS), base_dispatch='frontier',
    normal_foundation='frontier_without_reopening_or_added_after_tick_calls',
    latched_foundation='unchanged_recovery_dispatch_latched',
    initial_unaccepted_search_only=True, no_current_valid_offer=True, no_own_pending=True,
    original_offered_candidates_only=True, latest_actual_contact_must_be_latest_clinical_decline=True,
    unchanged_patient_condition_required=True, current_fresh_sufficient_required=True,
    actual_fresh_shortage_to_fresh_sufficient_quantity_recovery=True,
    required_resources_in_same_recovery_packet=True, recovery_source_after_actual_decline=True,
    later_semantic_loss_invalidates_recovery=True, other_unvisited_eligible_candidate_blocks=True,
    already_in_base_frontier_is_not_an_exception=True, actual_contact_counts_unchanged=True,
    maximum_actual_rank_asks_per_patient_condition=1,
    rank_consumption_separate_from_initial_reopen_consumption=True,
    original_v4_kernel_on_one_recovery_tier=True, original_seven_inputs_retained=True,
    rank_exception_adds_query_calls=False, rank_exception_changes_review_clock=False,
    rank_exception_changes_original_tried_or_episode=False,
    maximum_open_inquiries=3, maximum_episode_contacts=10,
    original_timeout_offer_ttl_fifo_readiness_resources_and_workers=True,
    extra_resource_change_publications=False, future_work_or_response_information=False)


def _audit_contexts(scenario, branch):
    """Rebuild every public selector input and actual once-only rank send."""
    from .recovery_rank_policy import decide_recovery_rank
    enabled = scenario.get('inquiry_queue', {}).get('enabled', False)
    workflow = (cooperative._CooperativeWorkflow if enabled else independent._ProbeWorkflow)(deepcopy(scenario))
    events = _indexed_events(branch)
    indexed = {event['event_sequence']: event for event in events}
    records = branch['recovery_rank_records']
    dispatch_records = branch['retry_dispatch_records']
    _require(type(records) is list and type(dispatch_records) is list, 'complete ordered selector records required')
    by_sequence, dispatch_by_sequence = {}, {}
    for rows, target, keys in ((records, by_sequence, {'inquiry_event_sequence', 'rank_context', 'decision'}),
            (dispatch_records, dispatch_by_sequence, {'inquiry_event_sequence', 'context', 'decision'})):
        for record in rows:
            _require(type(record) is dict and set(record) == keys, 'unknown or missing selector record fields')
            seq = record['inquiry_event_sequence']
            _require(type(seq) is int and seq >= 0 and seq not in target, 'duplicate selector source identity')
            target[seq] = record
    _require(list(by_sequence) == list(dispatch_by_sequence), 'rank and original-context records differ in coverage/order')
    hospitals = {row['id']: row for row in scenario['hospitals']}
    resource_names = {hid: sorted(row.get('capacity', {})) for hid, row in hospitals.items()}
    observations, report_refs = {}, {}
    first_commits, first_accepts, latest_selector = {}, {}, {}
    contacts, wakes, declines, transitions, consumptions = [], [], [], [], []
    attempts, episode_contacts, decisions, expanded, used = Counter(), Counter(), {}, set(), set()

    def views(pid, hid, now):
        demand = workflow._base_patient(pid)['demand']
        return information._views('observed', workflow.observations, workflow.truth, hid, demand, now)

    def global_views(hid, now):
        return {resource: information._observation_view(observations, hid, resource, now, 0, 0)
                for resource in resource_names[hid]}

    def universe(pid, now):
        patient = workflow._base_patient(pid)
        point = parent._position(scenario, branch, pid, now)
        near = branch['policy'].get('near_minutes')
        rows = []
        for hid in patient['compatible_hospitals']:
            if any(hospitals[hid].get('capacity', {}).get(r, 0) < q for r, q in patient['demand'].items()):
                continue
            if workflow.asks.get((pid, hid), 0) or workflow.offers.get((pid, hid), -1) > now \
                    or hid in workflow.failed.get(pid, set()):
                continue
            travel = parent.recovery._synthetic_minutes(point, hospitals[hid])
            if near is not None and pid not in expanded and travel > near:
                continue
            current = views(pid, hid, now)
            if any(v['status'] == 'fresh' and v['free'] < patient['demand'][r] for r, v in current.items()):
                continue
            rows.append(dict(hospital=hid, travel_minutes=travel, resources=current))
        return sorted(rows, key=lambda row: (row['travel_minutes'], row['hospital']))

    for event in events:
        kind, now, seq = event['type'], event['t'], event['event_sequence']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind == 'resource_report_received':
            before = global_views(hid, now)
            refs = {r: report_refs.get((hid, r)) for r in resource_names[hid]}
            information._apply_observation(observations, event)
            after = global_views(hid, now)
            if event['applied']:
                for resource in resource_names[hid]:
                    if resource in event['resources'] and after[resource]['sequence'] == event['packet_sequence'] \
                            and after[resource]['observed_at'] == event['observed_at']:
                        report_refs[hid, resource] = seq
                if any(before[r][key] != after[r][key] for r in resource_names[hid] for key in ('capacity', 'used', 'status')):
                    transitions.append(dict(hospital=hid, t=now, report_event_sequence=seq,
                        before_report_event_sequences=refs, before_resources=before, after_resources=after))
        elif kind == 'ask':
            condition = late._condition(workflow, pid)
            attempts[pid, hid] += 1
            _require(type(event['attempt']) is int and event['attempt'] == attempts[pid, hid],
                     'actual contact attempt was skipped, reused or reset')
            contacts.append(dict(patient=pid, hospital=hid, attempt=event['attempt'], submitted_at=now,
                ask_event_sequence=seq, condition_fingerprint=late._fingerprint(condition), resources=views(pid, hid, now)))
            episode_contacts[pid, event['inquiry_episode']] += 1
            source_seq = latest_selector.get(pid)
            if source_seq is not None and indexed[source_seq]['t'] == now:
                detail = decisions[source_seq]['evidence']['recovery_rank']
                if hid in detail['rank_send']:
                    proof = detail['selected_recovery_proofs'][hid]
                    consumptions.append(dict(patient=pid, hospital=hid, token_id=proof['token_id'],
                        condition_fingerprint=late._fingerprint(condition), ask_event_sequence=seq,
                        inquiry_event_sequence=source_seq, decline_event_sequence=proof['decline_event_sequence'],
                        report_event_sequence=proof['report_event_sequence']))
        elif kind == 'decline':
            prior_contacts = [c for c in contacts if c['patient'] == pid and c['hospital'] == hid]
            _require(prior_contacts and workflow.asks[pid, hid] > 0, 'clinical decline lacks its actual original inquiry')
            contact = prior_contacts[-1]
            condition = late._condition(workflow, pid)
            declines.append(dict(patient=pid, hospital=hid, t=now, decline_event_sequence=seq,
                ask_event_sequence=contact['ask_event_sequence'], attempt=contact['attempt'],
                condition_fingerprint=late._fingerprint(condition), resources=views(pid, hid, now)))
        elif kind in ('resource_event_review', 'report_driven_wake'):
            report = indexed.get(event['report_event_sequence'])
            _require(report is not None and report['event_sequence'] < seq and report['type'] == 'resource_report_received'
                     and report['hospital'] == hid and report['applied'] is True and report['t'] == now,
                     'retry wake lacks its original same-time applied public report')
            previous = event['previous_retry_at']
            _require(previous is None or joint._clock(previous), 'invalid original retry-wait deadline')
            condition = late._condition(workflow, pid)
            wakes.append(dict(patient=pid, hospital=hid, received_t=now, wake_event_sequence=seq,
                report_event_sequence=event['report_event_sequence'], previous_retry_at=previous,
                condition_fingerprint=late._fingerprint(condition), resources=views(pid, hid, now)))
        elif kind == 'joint_inquiry_policy':
            _require(seq in by_sequence, 'actual selector omitted its rank context')
            row = branch['inquiry_decisions'][event['record_index']]
            _require(row['event_sequence'] == seq, 'selector source record points to another inquiry')
            condition = late._condition(workflow, pid)
            context = dict(schema_version=1, evaluated_t=now,
                patient=dict(id=pid, state=workflow.states[pid], first_commit_t=first_commits.get(pid),
                    first_accept_event_sequence=first_accepts.get(pid), condition=condition,
                    condition_fingerprint=late._fingerprint(condition)),
                episode=dict(id=row['inquiry_episode'], contacts=episode_contacts[pid, row['inquiry_episode']]),
                universe=universe(pid, now), contacts=[deepcopy(c) for c in contacts if c['patient'] == pid],
                retry_barriers=[deepcopy(w) for w in wakes if w['patient'] == pid])
            rank_context = dict(schema_version=1, evaluated_t=now, variant=branch['recovery_rank_variant'],
                valid_offers=[dict(hospital=h, expires_at=expiry) for (p, h), expiry in sorted(workflow.offers.items()) if p == pid],
                declines=[deepcopy(d) for d in declines if d['patient'] == pid], transitions=deepcopy(transitions),
                consumptions=[deepcopy(c) for c in consumptions if c['patient'] == pid])
            _literal(dispatch_by_sequence[seq]['context'], context, 'rank changed the complete original public context')
            _literal(by_sequence[seq]['rank_context'], rank_context, 'rank evidence differs from actual public raw history')
            decision = decide_recovery_rank(inputs=deepcopy(row['inputs']), context=deepcopy(context), rank_context=deepcopy(rank_context))
            _literal(by_sequence[seq]['decision'], decision, 'rank record differs from fixed pure selector')
            _literal(dispatch_by_sequence[seq]['decision'], decision, 'original-context record differs from actual rank decision')
            _literal(row['decision'], decision, 'actual selector differs from independently reconstructed rank inputs')
            decisions[seq] = decision; used.add(seq); latest_selector[pid] = seq
        elif kind == 'commit': first_commits.setdefault(pid, now)
        elif kind == 'accept': first_accepts.setdefault(pid, seq)
        elif kind == 'expand': expanded.add(pid)
        workflow.apply(event)
    _require(list(by_sequence) == sorted(used) and used == set(by_sequence), 'orphan, duplicated or reordered rank records')
    for name, expected in (('retry_dispatch_contacts', contacts), ('retry_dispatch_wakes', wakes),
            ('recovery_rank_declines', declines), ('recovery_rank_resource_transitions', transitions),
            ('recovery_rank_consumptions', consumptions)):
        _literal(branch[name], expected, 'exported rank/contact history differs from all actual original raw events: ' + name)
    counts = Counter((row['patient'], row['condition_fingerprint']) for row in consumptions)
    _require(all(count <= 1 for count in counts.values()), 'one patient condition consumed multiple actual rank inquiries')
    _require(branch['metrics']['retry_dispatch_reviews'] == len(records), 'rank changed original review accounting')
    _require(branch['metrics']['retry_dispatch_frontier_deferrals'] == sum(d['reason'] == 'frontier_not_offered' for d in decisions.values())
             and branch['metrics']['retry_dispatch_wait_preservations'] == sum(d['reason'] == 'retry_wait_preserved' for d in decisions.values()),
             'rank changed original selector reason accounting')
    _require(branch['metrics']['recovery_rank_reviews'] == len(records)
             and branch['metrics']['recovery_rank_tier_reviews'] == sum(bool(d['evidence']['recovery_rank']['recovery_tier']) for d in decisions.values())
             and branch['metrics']['recovery_rank_sent'] == len(consumptions), 'rank metrics differ from actual selector and inquiries')
    return decisions, dict(recovery_rank_reviews=len(records), recovery_rank_sent=len(consumptions),
        recovery_rank_semantic_report_transitions=len(transitions), retry_dispatch_reviews=len(records),
        retry_dispatch_contacts=len(contacts), retry_dispatch_wakes=len(wakes))


def _audit_normal_episodes(scenario, branch):
    """Original normal-call/tried accounting without inventing recovery rows."""
    enabled = scenario.get('inquiry_queue', {}).get('enabled', False)
    workflow = (cooperative._CooperativeWorkflow if enabled else independent._ProbeWorkflow)(deepcopy(scenario))
    hospitals = {h['id']: h for h in scenario['hospitals']}
    tried = {p['id']: set() for p in scenario['patients']}
    handles, episodes, contacts, reviews, accepted, committed = {}, Counter(), Counter(), {}, set(), set()
    checked = 0

    def active(pid): return pid not in accepted and pid not in committed
    def own(pid): return any(p == pid for p, _ in handles)
    def has_offer(pid): return any(p == pid for p, _ in workflow.offers)
    def pool(pid):
        p = workflow._base_patient(pid)
        return {h for h in p['compatible_hospitals'] if h not in workflow.failed.get(pid, set())
            and all(hospitals[h].get('capacity', {}).get(r, 0) >= q for r, q in p['demand'].items())}
    def entry(pid, now):
        if now >= reviews.get(pid, 0) and not tried[pid] and contacts[pid, episodes[pid]]:
            episodes[pid] += 1

    for event in _indexed_events(branch):
        kind, now = event['type'], event['t']
        pid, hid = event.get('patient'), event.get('hospital')
        if kind in ('resource_event_review', 'report_driven_wake') and active(pid):
            tried[pid].discard(hid)
            entry(pid, now)
        elif kind == 'joint_inquiry_policy' and active(pid):
            entry(pid, now)
            row = branch['inquiry_decisions'][event['record_index']]
            _require(row['inquiry_episode'] == episodes[pid], 'normal rank invented an original selector episode reset')
            _require(not {c['hospital_id'] for c in row['inputs']['candidates']} & tried[pid],
                     'normal rank reopened an originally tried hospital without an original raw report wake')
            reviews[pid] = (row['decision']['next_review_at'] - parent.CLOCK_ORIGIN_SECONDS) / 60
            checked += 1
            if row['inputs']['max_new'] == 0 and not own(pid) and not has_offer(pid):
                tried[pid].clear()
        elif kind == 'ask':
            if active(pid):
                _require(event['inquiry_episode'] == episodes[pid], 'normal actual contact changed its original episode')
            contacts[pid, event['inquiry_episode']] += 1
            handles[pid, hid] = event['event_sequence']
            if not enabled or event.get('cooperative_job_id') is not None:
                tried[pid].add(hid)
        elif kind == 'cooperative_probe_not_admitted': handles.pop((pid, hid), None)
        elif kind in ('decline', 'no_response', 'accept'):
            handles.pop((pid, hid), None)
            if kind == 'accept': accepted.add(pid)
        elif kind == 'commit': committed.add(pid)
        workflow.apply(event)
        if kind in ('decline', 'no_response', 'expire') and active(pid) and not own(pid) and not has_offer(pid) \
                and now >= reviews.get(pid, 0) and pool(pid) and pool(pid) <= tried[pid]:
            # resolve_asks immediately enters the original no-candidate path.
            # Its source-order review guard runs before another selector exists.
            tried[pid].clear()
    return dict(recovery_rank_normal_episode_proofs=checked)


def audit_branch(scenario, branch, *, exact_limit=20000):
    """Audit exact whole references or an honestly declared rank intervention."""
    namespace = {key for key in branch if key.startswith('recovery_rank_')}
    if not namespace:
        _require(not str(branch.get('id', '')).startswith(('rank_', 'recovery_rank_')),
                 'rank candidate omitted its actual intervention namespace')
        return recovery.audit_branch(scenario, branch, exact_limit=exact_limit)
    _require(namespace == NAMESPACE_FIELDS and branch['recovery_rank_enabled'] is True,
             'missing, unknown or disabled rank namespace')
    variant = branch['recovery_rank_variant']
    _require(variant in VARIANTS and branch['recovery_rank_selector'] == 'recovery_rank_' + variant,
             'actual rank selector/variant must be explicit')
    _literal(branch['recovery_rank_spec'], RECOVERY_RANK_SPEC, 'rank intervention contract differs')
    _require({key for key in branch if key.startswith('retry_dispatch_')} == {
        'retry_dispatch_enabled', 'retry_dispatch_records', 'retry_dispatch_contacts', 'retry_dispatch_wakes'}
        and branch['retry_dispatch_enabled'] is True, 'rank falsely claims an unchanged actual frontier selector or drops factual records')
    _require(not any(key.startswith('capability_') for key in branch), 'rank falsely claims exact capability delegation')
    queue = scenario.get('inquiry_queue', {})
    _require(type(queue) is dict and type(queue.get('enabled', False)) is bool, 'queue support must remain declared boolean')
    enabled = queue.get('enabled', False)
    mode = 'cooperative_queue' if enabled else 'independent_replies'
    _literal(branch['recovery_rank_foundation_spec'], dict(RECOVERY_RANK_FOUNDATION_SPEC, mode=mode),
             'rank hides its actual subclass or conserved original workflow')
    expected_base = dict(retry_dispatch_selector='frontier', retry_dispatch_spec=deepcopy(parent.RETRY_DISPATCH_SPEC),
        retry_dispatch_foundation_spec=dict(parent.RETRY_DISPATCH_FOUNDATION_SPEC, mode=mode))
    recovery_namespace = {key for key in branch if key.startswith('recovery_dispatch_')}
    if variant == 'latched':
        _require(recovery_namespace == recovery.NAMESPACE_FIELDS - {'recovery_dispatch_foundation_spec'}
                 and branch['recovery_dispatch_enabled'] is True and branch['recovery_dispatch_variant'] == 'latched',
                 'latched rank changed or concealed original eligibility behavior')
        _literal(branch['recovery_dispatch_spec'], recovery.RECOVERY_DISPATCH_SPEC, 'original latched eligibility contract changed')
        expected_base['recovery_dispatch_foundation_spec'] = dict(recovery.RECOVERY_FOUNDATION_SPEC,
            base_dispatch_foundation_spec=dict(parent.RETRY_DISPATCH_FOUNDATION_SPEC, mode=mode))
    else:
        _require(not recovery_namespace and not any(e['type'].startswith('recovery_') for e in _indexed_events(branch)),
                 'normal rank gained a reopening or extra recovery call')
    _literal(branch['recovery_rank_base_contract'], expected_base, 'nested original base contract differs')
    _require(branch.get('inquiry_selector') == 'v4' and branch.get('policy', {}).get('rule') in ('H1', 'C1')
             and branch['policy'].get('name') == branch.get('id'), 'actual rank identity, original slot or allocation changed')
    _require(all(branch.get(flag) is True for flag in ('shortage_probe_enabled', 'late_pending_guard_enabled',
        'late_reply_recovery_enabled', 'cohort_registration_enabled', 'scarce_routing_enabled')), 'fixed complete foundation changed')
    _literal(branch.get('scarce_delay_spec'), dict(inquiry='v4', allocation=branch['policy']['rule'],
        cohort_registration=True, scarce_routing=True, actual_action='parallel_scarce_supplement',
        original_selector_candidates_unchanged=False, maximum_supplements_per_patient=1,
        recovery=True, pending_protection=True, pending_window_minutes=1, time_step_seconds=15,
        actual_selector_reused=False, original_seven_inputs_retained=True, base_selector_reused_as_kernel=True,
        extra_resource_change_publications=False), 'rank changed its explicit scarce-probe foundation')
    if enabled:
        _require(branch.get('cooperative_probe_enabled') is True and branch.get('cooperative_queue_enabled') is True
                 and branch.get('comparison_id') == 'cooperative_probe_20261017', 'complete cooperative workflow required')
    else:
        _require(not any(k.startswith('cooperative_') for k in branch) and branch.get('comparison_id') == 'shortage_probe_20261017'
                 and branch.get('inquiry_queue_report') is None, 'independent rank invented queue workers')
    allowed_recovery_events = {'recovery_reopen_review', 'recovery_reopen', 'recovery_retry_wake', 'recovery_reopen_result'}
    _require(all(not event['type'].startswith('recovery_') or variant == 'latched' and event['type'] in allowed_recovery_events
                 for event in _indexed_events(branch)), 'unknown rank/recovery raw intervention event')
    decisions, counts = _audit_contexts(scenario, branch)
    if variant == 'latched':
        counts.update(recovery._audit_recovery_trace(scenario, branch))
    else:
        counts.update(_audit_normal_episodes(scenario, branch))
    # These frozen components receive the complete unfiltered original object.
    result = (parent._audit_cooperative_branch if enabled else parent._audit_independent_branch)(
        scenario, branch, decisions, exact_limit=exact_limit)
    result['counts'].update(counts)
    result['selector_check'] = ('actual rank selector reproduced on independently reconstructed original seven inputs, '
        'full actual contact/report/decline history and real once-only rank sends; original full raw workflow retained')
    result['recovery_clock_scope'] = ('original raw wake value and frozen producer binding; '
        'not an independent proof of every unlogged internal retry_at assignment')
    return result


def audit_recovery_rank_replay(result, *, exact_limit=20000):
    _require(result.get('schema_version') == 1 and type(result['schema_version']) is int
             and result.get('experiment_id') == EXPERIMENT_ID, 'unknown rank replay schema')
    ids = [b['id'] for b in result['branches']]
    _require(ids and len(ids) == len(set(ids)) and len(result['execution_order']) == len(ids)
             and set(result['execution_order']) == set(ids), 'invalid newly executed rank replay branches')
    counts, details = Counter(), {}
    for branch in result['branches']:
        checked = audit_branch(result['scenario'], branch, exact_limit=exact_limit)
        counts.update(checked['counts']); details[branch['id']] = checked
    return dict(passed=True, branches=len(ids), counts=dict(counts), branch_audits=details,
        assignment_constraint_violations=0, actual_optimality_claimed=False)
