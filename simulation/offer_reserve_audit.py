"""Independent whole-raw audit of cohort-plus-own-demand early-offer reserve.

The retained original public-stage input is rebuilt from received reports,
registered patients, original inquiry handles and current commitments.  The
reserve is reproduced here without importing its producer policy or engine.
Every unmodified complete branch also reaches the frozen original clinical,
work, FIFO, physical, allocation, early, pending and backup auditors.
"""
from collections import Counter
from copy import deepcopy

from . import public_offer_audit as prior


rank, base, information, cooperative = prior.rank, prior.base, prior.information, prior.cooperative
independent, joint, parent = prior.independent, prior.joint, prior.parent
_require, _indexed_events, _literal = prior._require, prior._indexed_events, prior._literal
_workflow, _end, _views = prior._workflow, prior._end, prior._views
_is_callback_accept = prior._is_callback_accept
OfferReserveAuditError = prior.PublicOfferAuditError
EXPERIMENT_ID = 'offer_reserve_20261020'
VARIANTS = ('cohort_reserve',)
NAMESPACE_FIELDS = set(prior.NAMESPACE_FIELDS) | {'offer_reserve_base_contract'}

# Independent literal contracts: these do not import the producer.
PUBLIC_OFFER_SPEC = dict(
    schema_version=1, variants=list(VARIANTS),
    base_policy='simulation.public_offer_policy.decide_public_offer',
    base_variant='public_blocked', all_original_public_checks_retained=True,
    snapshot='unchanged complete public-offer snapshot',
    every_actual_usable_option_checked=True, actual_options_unchanged=True,
    actual_option_order_unchanged=True,
    option_check_order='original snapshot valid_offers order; actual marker keeps acceptance order',
    reserve_own_demand_copies=1,
    new_threshold=True, fixed_threshold='free >= compatible SEARCH cohort demand + own demand',
    compatible_cohort='all original registered SEARCH rows, including self and all priorities',
    all_current_patient_required_resources_checked=True,
    original_registered_search_shared_capacity_check=True,
    actual_pending_and_other_offer_backups_retained=True,
    original_opportunity_pending_and_physical_gates_retained=True,
    inquiry_selector_and_clock_attempt_fifo_workers_resources_ttl_rules_retained=True,
    actual_downstream_paths_and_counts_may_change=True,
    extra_resource_change_publications=False,
    future_work_or_response_information=False,
    unregistered_patient_nonworsening_guarantee=False)

PUBLIC_OFFER_FOUNDATION_SPEC = dict(
    actual_replays=dict(
        independent_replies='simulation.offer_reserve_engine.IndependentOfferReserveReplay',
        cooperative_queue='simulation.offer_reserve_engine.CooperativeOfferReserveReplay'),
    base_replays=dict(
        independent_replies='simulation.public_offer_engine.IndependentPublicOfferReplay',
        cooperative_queue='simulation.public_offer_engine.CooperativePublicOfferReplay'),
    joint_stage='simulation.public_offer_engine.OfferReadyJointStage',
    dynamic_new_stage='simulation.offer_reserve_engine.OfferReserveMixin._public_offer_stage',
    rank_variant='normal', actual_normal_selector_changed=False, actual_readiness_changed=True,
    original_joint_resolve_body_explicitly_retained=True,
    new_stage='after_original_early_loop_before_opportunity_and_pending_guards',
    original_probe_ack_late_and_original_early_stages_retained=True,
    new_marker_registers_original_backup_reference=True,
    actual_options_follow_original_valid_offer_order=True,
    actual_options_not_filtered=True, fixed_current_cohort_reserve_own_demand_copies=1,
    original_inquiry_work_clock_attempt_and_physical_accounting_rules_retained=True,
    actual_downstream_paths_and_counts_may_change=True,
    inherited_protocol_descriptions_are_nested_base_contract=True,
    extra_resource_change_publications=False, added_workers_or_beds=False)


def _base_contract(mode):
    return dict(base_variant='public_blocked',
        descriptors_describe_frozen_base_not_actual_new_rule=True,
        public_offer_spec=deepcopy(prior.PUBLIC_OFFER_SPEC),
        public_offer_foundation_spec=dict(deepcopy(prior.PUBLIC_OFFER_FOUNDATION_SPEC), mode=mode))


def _expected(snapshot, variant):
    """Independent cohort reserve arithmetic on the original public snapshot."""
    _require(variant in VARIANTS, 'unknown cohort-reserve audit variant')
    underlying = prior._expected(snapshot, 'public_blocked')
    output = dict(schema_version=1, variant=variant, base_decision=underlying,
        option_checks=[], eligible=False, hospital=None,
        reason='BASE_PUBLIC_OFFER_INELIGIBLE', usable_options=[])
    if not underlying['eligible']:
        return output
    original = snapshot['original_snapshot']
    demand = original['patient']['demand']
    usable = [offer for offer in original['valid_offers'] if all(
        view['status'] == 'fresh' and view['free'] is not None and view['free'] >= demand[resource]
        for resource, view in offer['resources'].items())]
    _require(bool(usable), 'eligible base lacks an actual fresh sufficient offer')
    output['usable_options'] = [offer['hospital'] for offer in usable]
    for offer in usable:
        contenders = [patient for patient in original['search_patients']
            if offer['hospital'] in patient['compatible_hospitals']]
        resources = {}
        for resource, own in demand.items():
            cohort = sum(patient['demand'].get(resource, 0) for patient in contenders)
            free = offer['resources'][resource]['free']
            resources[resource] = dict(cohort_demand=cohort, own_demand=own, free=free,
                required_free=cohort + own, residual_after_cohort=free - cohort,
                passed=free >= cohort + own)
        output['option_checks'].append(dict(hospital=offer['hospital'], resources=resources,
            passed=all(row['passed'] for row in resources.values())))
    if not all(row['passed'] for row in output['option_checks']):
        return dict(output, reason='COHORT_RESERVE_INSUFFICIENT')
    return dict(output, eligible=True, hospital=underlying['hospital'], reason='COHORT_RESERVE_READY')

def _audit_stage(scenario, branch):
    """Rebuild every new stage from original requests, handles and reports."""
    events = _indexed_events(branch)
    indexed = {event['event_sequence']:event for event in events}
    records = branch['public_offer_records']
    _require(type(records) is list, 'ordered complete public-offer records required')
    workflow = _workflow(scenario)
    enabled = scenario.get('inquiry_queue',{}).get('enabled',False)
    mode = 'cooperative_queue' if enabled else 'independent_replies'
    handles, first_commits = {}, {}
    cycles, targets, used, promotions, checked = [], [], set(), set(), set()
    original_cycles, original_last = {}, {}
    cycle_t = None
    for event in events:
        if event['type'] in ('early_offer_cycle','early_offer_review','early_offer_ready'):
            original_last[event['t']] = event['event_sequence']
    def snapshot(patient, now):
        original = cooperative._early_snapshot(workflow,patient,now)
        demand = workflow._base_patient(patient)['demand']
        return dict(schema_version=1,evaluated_t=now,mode=mode,
            patient_state=workflow.states[patient],first_commit_t=first_commits.get(patient),
            wait_minutes=branch['policy']['wait'],original_snapshot=original,
            pending_inquiries=sorted([deepcopy(row) for (pid,_),row in handles.items() if pid==patient],
                key=lambda row:(row['hospital'],row['attempt'])),
            structural_resource_views={row['hospital']:_views('observed',workflow.observations,
                workflow.truth,row['hospital'],demand,now) for row in original['structural_candidates']})
    for event in events:
        kind, now, seq = event['type'],event['t'],event['event_sequence']
        pid,hid = event.get('patient'),event.get('hospital')
        if kind=='ask':
            handles[pid,hid] = dict(hospital=hid,attempt=event['attempt'],submitted_at=now,
                timeout_at=event['timeout_at'],ask_event_sequence=seq)
        elif kind=='cooperative_probe_not_admitted':
            handles.pop((pid,hid),None)
        elif kind in ('accept','decline','no_response'):
            if not _is_callback_accept(event,indexed):
                handles.pop((pid,hid),None)
        elif kind in ('commit','arrive','transfer_search','treatment_start','backup_closed'):
            handles = {key:row for key,row in handles.items() if key[0]!=pid}
            if kind=='commit':first_commits.setdefault(pid,now)
        elif kind in ('backup_retained','backup_arrival_preserved','backup_transfer_reused'):
            handles = {key:row for key,row in handles.items() if key[0]!=pid}
            for row in event['pending']:
                handles[pid,row['hospital']]=deepcopy(row)
        elif kind=='early_offer_cycle':
            _require(now not in original_cycles,'duplicate original early cycle')
            original_cycles[now]=seq
        elif kind=='opportunity_guard':
            _require(cycle_t==now and not targets,
                'opportunity guard precedes completion of the new public-offer stage')
        elif kind=='public_offer_cycle':
            _require(not targets and set(event)=={'t','type','event_sequence'},
                'previous new cycle omitted a review or cycle exposes unknown fields')
            _require(now in original_cycles and original_last[now]<seq,
                'new stage precedes completion of the original early stage')
            cycle_t=now;cycles.append(now)
            ready = {row['id'] for row in workflow.snapshot(now)['ready']}
            targets=[]
            for patient in sorted(workflow.specs,key=lambda target:(
                    -workflow.specs[target].get('priority',4),
                    workflow.specs[target].get('search_start_t',0),target)):
                if workflow.states.get(patient)!='SEARCH' or patient in ready:
                    continue
                pending=any(other==patient for other,_ in handles)
                valid=any(other==patient and expiry>now for (other,_),expiry in workflow.offers.items())
                first=workflow.first_valid.get(patient)
                if pending and valid and first is not None and now-first<branch['policy']['wait']:
                    targets.append(patient)
        elif kind=='public_offer_review':
            _require(cycle_t==now and targets and pid==targets.pop(0),
                'unsolicited, reordered, duplicate or omitted public-offer review')
            index=event['record_index']
            _require(type(index) is int and index==len(used) and index<len(records),
                'missing/reordered public-offer record')
            used.add(index);record=records[index]
            _require(set(event)=={'t','type','event_sequence','patient','variant','record_index'}
                and event['variant']==branch['public_offer_variant']
                and set(record)=={'event_sequence','snapshot','shadow_decision','decision'}
                and record['event_sequence']==seq,'public-offer record/marker has unknown fields or wrong identity')
            current=snapshot(pid,now)
            _literal(record['snapshot'],current,'public-offer input differs from full original public raw history')
            shadow=cooperative._early_expected(current['original_snapshot'])
            _literal(record['shadow_decision'],shadow,'original full early policy shadow differs')
            expected=_expected(current,branch['public_offer_variant'])
            _literal(record['decision'],expected,'public-offer decision differs from independent public quantity/rank rule')
            if expected['eligible']:
                demand=workflow._base_patient(pid)['demand']
                options=[hospital for (patient,hospital),expiry in workflow.offers.items() if patient==pid
                    and expiry>now and information._check_reason(_views('observed',workflow.observations,
                        workflow.truth,hospital,demand,now),demand) is None]
                marker=indexed.get(seq+1)
                _literal(marker,dict(t=now,type='public_offer_ready',event_sequence=seq+1,patient=pid,
                    hospital=expected['hospital'],options=options,review_event_sequence=seq),
                    'eligible new stage lacks its unique immediate actual readiness marker')
                promotions.add(seq+1)
                for offset,hospital in enumerate(options,2):
                    check=indexed.get(seq+offset)
                    _literal(check,dict(t=now,type='information_check',event_sequence=seq+offset,
                        patient=pid,hospital=hospital,purpose='allocation',eligible=True,reason=None,
                        resources=_views('observed',workflow.observations,workflow.truth,hospital,demand,now)),
                        'new ready option lacks immediate original current allocation check')
                    checked.add(seq+offset)
            else:
                following=indexed.get(seq+1)
                _require(following is None or following['type']!='public_offer_ready',
                    'ineligible new stage promoted readiness')
        workflow.apply(event)
    _require(not targets and used==set(range(len(records))),'orphan or omitted new readiness review')
    _require(promotions=={event['event_sequence'] for event in events if event['type']=='public_offer_ready'},
        'orphan or fabricated new readiness marker')
    _require(cycles==[step/4 for step in range(int(_end(scenario,branch)*4)+1)],
        'missing, duplicate or extra public-offer cycle')
    _require(type(branch['metrics']['public_offer_cycles']) is int and branch['metrics']['public_offer_cycles']==len(cycles)
        and type(branch['metrics']['public_offer_reviews']) is int and branch['metrics']['public_offer_reviews']==len(records)
        and type(branch['metrics']['public_offer_ready']) is int and branch['metrics']['public_offer_ready']==len(promotions),
        'public-offer counts differ from actual complete raw cycles/reviews/readiness')
    return dict(public_offer_cycles=len(cycles),public_offer_reviews=len(records),
        public_offer_ready=len(promotions),public_offer_current_allocation_checks=len(checked))


def _audit_contract(branch, enabled):
    """Bind the actual wrapper and its honestly nested inherited protocols."""
    namespace = {key for key in branch if key.startswith('public_offer_')}
    _require(namespace == NAMESPACE_FIELDS - {'offer_reserve_base_contract'} and branch['public_offer_enabled'] is True,
             'missing, unknown or disabled public-offer namespace')
    _require(branch['public_offer_variant'] in VARIANTS,
             'known explicit public-offer variant required')
    _literal(branch['public_offer_spec'], PUBLIC_OFFER_SPEC, 'public-offer rule contract differs')
    mode = 'cooperative_queue' if enabled else 'independent_replies'
    _require({key for key in branch if key.startswith('offer_reserve_')} == {'offer_reserve_base_contract'},
             'missing or unknown cohort-reserve namespace')
    _literal(branch['offer_reserve_base_contract'], _base_contract(mode),
             'cohort reserve changed or concealed its complete original public-stage contract')
    _literal(branch['public_offer_foundation_spec'], dict(PUBLIC_OFFER_FOUNDATION_SPEC, mode=mode),
             'public-offer stage conceals its actual wrapper, mode or unchanged normal selector')
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
    _literal(branch['public_offer_base_contract'], inherited,
             'public-offer stage changed or concealed its complete inherited base contract')
    popped_rank = {'recovery_rank_selector', 'recovery_rank_spec',
        'recovery_rank_foundation_spec', 'recovery_rank_base_contract'}
    _require({key for key in branch if key.startswith('recovery_rank_')}
             == rank.NAMESPACE_FIELDS - popped_rank
             and branch['recovery_rank_enabled'] is True and branch['recovery_rank_variant'] == 'normal',
             'public-offer stage must retain actual normal-rank facts and nest inherited protocol descriptions')
    _require(not any(key in branch for key in ('shortage_probe_spec', 'cooperative_probe_spec'))
             and branch.get('comparison_id') == EXPERIMENT_ID,
             'actual public-offer stage falsely claims an unchanged probe protocol identity')
    _require({key for key in branch if key.startswith('retry_dispatch_')} == {
        'retry_dispatch_enabled', 'retry_dispatch_records', 'retry_dispatch_contacts', 'retry_dispatch_wakes'}
        and branch['retry_dispatch_enabled'] is True,
        'public-offer stage drops factual dispatch records or falsely claims unchanged selector metadata')
    _require(not any(key.startswith(('capability_', 'recovery_dispatch_', 'probe_guard_')) for key in branch),
             'public-offer stage invented a reopening or exact capability delegation')
    _require(branch.get('inquiry_selector') == 'v4' and branch.get('policy', {}).get('rule') in ('H1', 'C1')
             and branch['policy'].get('name') == branch.get('id')
             and type(branch.get('id')) is str and branch['id'],
             'public-offer execution identity, selector slot or allocation rule changed')
    _require(all(branch.get(flag) is True for flag in ('shortage_probe_enabled', 'late_pending_guard_enabled',
        'late_reply_recovery_enabled', 'cohort_registration_enabled', 'scarce_routing_enabled')),
        'public-offer stage changed a fixed complete foundation flag')
    _literal(branch.get('scarce_delay_spec'), dict(inquiry='v4', allocation=branch['policy']['rule'],
        cohort_registration=True, scarce_routing=True, actual_action='parallel_scarce_supplement',
        original_selector_candidates_unchanged=False, maximum_supplements_per_patient=1,
        recovery=True, pending_protection=True, pending_window_minutes=1, time_step_seconds=15,
        actual_selector_reused=False, original_seven_inputs_retained=True, base_selector_reused_as_kernel=True,
        extra_resource_change_publications=False), 'public-offer stage changed its unchanged scarce foundation')
    if enabled:
        _require(branch.get('cooperative_probe_enabled') is True and branch.get('cooperative_queue_enabled') is True,
                 'public-offer stage omitted the actual complete cooperative workflow')
    else:
        _require(not any(key.startswith('cooperative_') for key in branch)
                 and branch.get('inquiry_queue_report') is None, 'independent public-offer stage invented hospital workers')
    events = _indexed_events(branch)
    _require(not any(event['type'].startswith('recovery_') for event in events),
             'public-offer stage gained a recovery reopening or extra call')
    _require(all(not event['type'].startswith('public_offer_') or event['type'] in ('public_offer_cycle','public_offer_review','public_offer_ready')
                 for event in events), 'unknown public-offer raw intervention event')


def audit_branch(scenario, branch, *, exact_limit=20000):
    """Audit a complete frozen reference or a declared actual reserve branch."""
    reserve_namespace = {key for key in branch if key.startswith('offer_reserve_')}
    if not reserve_namespace:
        _require(not str(branch.get('id', '')).startswith('offer_reserve_')
                 and branch.get('comparison_id') != EXPERIMENT_ID
                 and branch.get('public_offer_variant') not in VARIANTS,
                 'cohort-reserve candidate concealed its actual intervention contract')
        return prior.audit_branch(scenario, branch, exact_limit=exact_limit)
    queue = scenario.get('inquiry_queue', {})
    _require(type(queue) is dict and type(queue.get('enabled', False)) is bool,
             'original queue support must remain an explicit boolean')
    enabled = queue.get('enabled', False)
    _audit_contract(branch, enabled)
    _require(branch.get('early_offers_enabled') is True
             and type(branch['policy'].get('wait')) is int and branch['policy']['wait'] == 2
             and type(branch['policy'].get('k')) is int and branch['policy']['k'] == 3,
             'reserve stage changed original early readiness, wait or concurrency')
    decisions, counts = rank._audit_contexts(scenario, branch)
    counts.update(rank._audit_normal_episodes(scenario, branch))
    counts.update(_audit_stage(scenario, branch))
    # These frozen components receive the SAME complete raw branch.  No
    # metadata projection, event filtering, engine replay or monkeypatch occurs.
    checked = (prior._audit_cooperative_branch if enabled else prior._audit_independent_branch)(
        scenario, branch, decisions, exact_limit=exact_limit)
    checked['counts'].update(counts)
    checked['selector_check'] = (
        'unchanged normal selector on independently reconstructed seven inputs; '
        'independent public base and all usable options times every own resource cohort reserve; '
        'same whole raw branch reaches frozen clinical, work, FIFO, physical and allocation auditors')
    return checked


def audit_offer_reserve_replay(result, *, exact_limit=20000):
    _require(type(result.get('schema_version')) is int and result['schema_version'] == 1
             and result.get('experiment_id') == EXPERIMENT_ID,
             'unknown cohort-reserve replay schema')
    ids = [branch['id'] for branch in result['branches']]
    _require(ids and all(type(code) is str and code for code in ids) and len(ids) == len(set(ids))
             and len(result['execution_order']) == len(ids) and set(result['execution_order']) == set(ids),
             'missing, duplicate or invalid cohort-reserve replay identity')
    counts, details = Counter(), {}
    for branch in result['branches']:
        checked = audit_branch(result['scenario'], branch, exact_limit=exact_limit)
        counts.update(checked['counts'])
        details[branch['id']] = checked
    return dict(passed=True, counts=dict(counts), branches=len(ids), branch_audits=details,
        assignment_constraint_violations=0, actual_optimality_claimed=False)




