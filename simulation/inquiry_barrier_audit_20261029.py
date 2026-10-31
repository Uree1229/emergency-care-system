"""Independent primary-wave role/clock/ready audit for QJ replays.

Only a deep-copy projection reuses the frozen QI audit for its unchanged G2
decision mathematics and inquiry ledger. QJ is never described as an executed
QI or original whole-episode G2 arm. No allocator or planner is executed here.
"""
from collections import Counter
from copy import deepcopy

from . import inquiry_preserving_audit_20261029 as qi_audit
from . import final_assignment_audit_20261028 as common

EXPERIMENT_ID = 'inquiry_primary_wave_barrier_20261029'
BARRIER_CONTRACT = dict(version='primary_wave_barrier_v1',
    primary='initial_or_normal_retry_wave', auxiliary='refill_hook_requests_only',
    while_primary_pending='wait_for_primary_acceptance_plus_two_or_primary_completion',
    when_primary_complete='primary_offer_now_else_auxiliary_wait_two_or_no_open_requests',
    auxiliary_replies_do_not_start_or_extend_primary_wait_clock=True,
    restore_primary_clock_after_last_auxiliary_offer_expires=True,
    submitted_hospital_work_cancelled=False, unchanged_G2_allocation=True,
    future_response_or_release_information_used=False,
    whole_episode_nonregression_guaranteed=False)
BARRIER_ASSUMPTIONS = [
    'QJ tags initial/normal retry inquiries as primary and refill-hook inquiries as auxiliary.',
    'Auxiliary offers neither start nor extend the primary wave wait clock; received offers are evaluated by unchanged G2 after the primary barrier.',
    'Hospital work already submitted is not cancelled. Current barrier preservation is not a whole-episode or priority4 outcome guarantee.',
]
SNAPSHOT_KEYS = {'t','patient','request_t','state','pending','valid_offers',
                 'primary_wait_minutes','ready_before','first_valid_t_before',
                 'barrier_latched_before','current_auxiliary_present'}
CORE_KEYS = {'allow_current_G2_ready','reason','primary_pending_count',
             'primary_valid_count','primary_first_accept_t','auxiliary_first_accept_t'}
DECISION_KEYS = CORE_KEYS | {'ready_after','removed_premature_ready',
    'added_primary_barrier_ready','recheck_failed_hospitals','applied_first_valid_t','barrier_latched_after'}
require = qi_audit.require


def _time(value, label):
    require(type(value) is int and value >= 0, label)
    return value


def barrier_math(snapshot):
    """Independent decision core from the exact observed barrier DTO."""
    require(type(snapshot) is dict and set(snapshot) == SNAPSHOT_KEYS,
            'barrier snapshot whitelist differs')
    t = _time(snapshot['t'], 'invalid barrier time')
    request = _time(snapshot['request_t'], 'invalid barrier request time')
    require(request <= t, 'future barrier patient')
    qi_audit.identifier(snapshot['patient'], 'missing barrier patient')
    require(snapshot['state'] == 'SEARCH', 'barrier is limited to SEARCH')
    require(type(snapshot['primary_wait_minutes']) is int and snapshot['primary_wait_minutes'] == 2,
            'primary wait must remain two minutes')
    require(type(snapshot['ready_before']) is bool, 'barrier ready flag must be boolean')
    for key in ('barrier_latched_before','current_auxiliary_present'):
        require(type(snapshot[key]) is bool, 'barrier latch/current-aux flag must be boolean')
    first_before = _time(snapshot['first_valid_t_before'], 'missing/invalid observed first-valid clock')
    require(request <= first_before <= t, 'future or pre-request first-valid clock')
    require(type(snapshot['pending']) is list and type(snapshot['valid_offers']) is list,
            'barrier pending/offers must be lists')
    pending, offers = [], []
    seen = set()
    for row in snapshot['pending']:
        require(type(row) is dict and set(row) == {'hospital','submitted_t','role'},
                'private/unknown pending barrier field')
        hid = qi_audit.identifier(row['hospital'], 'missing pending hospital')
        sent = _time(row['submitted_t'], 'invalid pending submission')
        require(request <= sent <= t, 'future or pre-request pending inquiry')
        require(row['role'] in ('primary','auxiliary'), 'unknown pending role')
        require(hid not in seen, 'duplicate pending hospital')
        seen.add(hid);pending.append(row)
    require(len(pending) <= 3, 'barrier pending inquiry cap exceeded')
    for row in snapshot['valid_offers']:
        require(type(row) is dict and set(row) == {'hospital','accepted_t','role'},
                'private/unknown accepted barrier field')
        hid = qi_audit.identifier(row['hospital'], 'missing offered hospital')
        accepted = _time(row['accepted_t'], 'invalid actual accepted time')
        require(request <= accepted <= t, 'future or pre-request acceptance')
        require(row['role'] in ('primary','auxiliary'), 'unknown accepted role')
        require(hid not in seen, 'duplicate or pending-and-valid hospital')
        seen.add(hid);offers.append(row)
    require(bool(offers), 'barrier review without a current valid offer')
    has_auxiliary = any(row['role'] == 'auxiliary' for row in pending + offers)
    require(snapshot['current_auxiliary_present'] is has_auxiliary,
            'current auxiliary flag differs from public roles')
    require(has_auxiliary or snapshot['barrier_latched_before'],
            'normal never-auxiliary G2 wave must not acquire a barrier review')
    primary_pending = sum(row['role'] == 'primary' for row in pending)
    primary_offers = [row['accepted_t'] for row in offers if row['role'] == 'primary']
    auxiliary_offers = [row['accepted_t'] for row in offers if row['role'] == 'auxiliary']
    first = min(primary_offers, default=None)
    auxiliary_first = min(auxiliary_offers, default=None)
    if not primary_pending:
        allow = bool(primary_offers) or not pending or (auxiliary_first is not None and t >= auxiliary_first + 2)
        reason = 'primary_wave_complete' if allow else 'collect_auxiliary_offers_after_primary_completion'
    elif first is None:
        allow, reason = False, 'wait_for_primary_acceptance'
    elif t >= first + 2:
        allow, reason = True, 'primary_acceptance_wait_complete'
    else:
        allow, reason = False, 'wait_primary_two_minutes'
    return dict(allow_current_G2_ready=allow, reason=reason,
        primary_pending_count=primary_pending, primary_valid_count=len(primary_offers),
        primary_first_accept_t=first,auxiliary_first_accept_t=auxiliary_first)


def _barrier_events(physical, branch):
    patients = {p['id']:p for p in physical['patients']}
    records = branch['primary_barrier_decisions']
    require(type(records) is list, 'barrier decision list required')
    events = branch['events'];counts = Counter()
    # Role provenance comes from actual refill-hook ask positions, not from the
    # role label being audited. The QI projection already checked their order.
    auxiliary_positions = set()
    for index, event in enumerate(events):
        if event['type'] == 'inquiry_refill_policy':
            refill = branch['inquiry_refill_decisions'][event['record_index']]
            for offset in range(1, len(refill['actual_sent']) + 1):
                require(index + offset not in auxiliary_positions, 'overlapping refill ask positions')
                auxiliary_positions.add(index + offset)
    membership = {}
    for decision in branch['allocation_decisions']:
        if decision['kind'] == 'batch':
            for row in decision['inputs']['patients']:
                key = decision['t'], row['id']
                require(key not in membership, 'same patient appears in two G2 batches this minute')
                membership[key] = row
    pending = {pid:{} for pid in patients}
    valid = {pid:{} for pid in patients}
    state = {pid:'SEARCH' for pid in patients}
    clock = {pid:None for pid in patients}
    roles = [];latest = {};reviewed = set();applied = set();active = {};latched = set()
    decisions_by_patient_time = {};last_t = None

    def has_auxiliary(pid):
        return any(row['role'] == 'auxiliary' for row in list(pending[pid].values()) + list(valid[pid].values()))

    def finish_tick(t):
        if t is None:
            return
        require(not active, 'barrier review lacks its immediate apply event')
        for pid in patients:
            if patients[pid]['search_start_t'] <= t and state[pid] == 'SEARCH' and valid[pid] and (has_auxiliary(pid) or pid in latched):
                require((t,pid) in decisions_by_patient_time,
                        'missing barrier review for current auxiliary wave')

    for index, event in enumerate(events):
        t = _time(event['t'], 'invalid barrier event time')
        require(last_t is None or t >= last_t, 'unordered barrier events')
        if last_t is not None and t != last_t:
            finish_tick(last_t)
        last_t = t
        pid, kind = event.get('patient'), event['type']
        if active:
            owner = next(iter(active))
            require(pid == owner and kind in ('recheck_failed','inquiry_primary_barrier'),
                    'barrier review/apply phase interrupted by an unrelated event')
        if pid not in patients:
            continue
        if kind == 'ask':
            expected = 'auxiliary' if index in auxiliary_positions else 'primary'
            require(event.get('inquiry_role') == expected, 'inquiry role differs from actual transmission phase')
            row = dict(patient=pid,hospital=event['hospital'],submitted_t=t,
                       attempt=event['attempt'],role=expected)
            roles.append(row);latest[pid,event['hospital']] = row
            pending[pid][event['hospital']] = dict(hospital=event['hospital'],submitted_t=t,role=expected)
            if expected == 'auxiliary':
                latched.add(pid)
            counts[expected + '_submissions_checked'] += 1
        elif kind in ('accept','decline','no_response'):
            hid = event['hospital']
            require(hid in pending[pid], 'barrier terminal lacks current inquiry')
            sent = pending[pid].pop(hid)
            if kind == 'accept':
                valid[pid][hid] = dict(hospital=hid,accepted_t=t,role=sent['role'])
                if clock[pid] is None:
                    clock[pid] = t
        elif kind in ('expire','recheck_failed'):
            hid = event['hospital']
            require(hid in valid[pid], 'barrier valid-offer removal lacks a current offer')
            if kind == 'expire':
                require(t >= valid[pid][hid]['accepted_t'] + physical.get('accept_ttl_minutes',10),
                        'premature expiry can conceal an auxiliary barrier')
            if pid in active:
                active[pid]['failed'].append(hid)
            del valid[pid][hid]
            if not valid[pid]:
                clock[pid] = None
        elif kind == 'inquiry_primary_barrier_review':
            ri = event.get('record_index')
            require(type(ri) is int and 0 <= ri < len(records) and ri not in reviewed,
                    'missing/reused barrier review record')
            record = records[ri]
            require(type(record) is dict and set(record) == {'t','patient','snapshot','decision','phase'},
                    'barrier record schema differs')
            require(record['t'] == t and record['patient'] == pid and
                    record['phase'] == 'after_received_results_before_G2_batch', 'barrier record identity/phase differs')
            require((t,pid) not in decisions_by_patient_time, 'duplicate barrier review for patient/minute')
            snapshot = record['snapshot'];core = barrier_math(snapshot)
            require(snapshot['t'] == t and snapshot['patient'] == pid and
                    snapshot['request_t'] == patients[pid]['search_start_t'], 'barrier patient metadata differs')
            require(snapshot['state'] == state[pid] == 'SEARCH', 'barrier on non-searching patient')
            require(snapshot['pending'] == list(pending[pid].values()), 'barrier pending differs from actual inquiry ledger')
            require(snapshot['valid_offers'] == list(valid[pid].values()), 'barrier offers/accepted times differ from actual received ledger')
            require(snapshot['barrier_latched_before'] is (pid in latched) and
                    snapshot['current_auxiliary_present'] is has_auxiliary(pid),
                    'barrier latch differs from actual prior auxiliary transmission history')
            require(snapshot['first_valid_t_before'] == clock[pid], 'barrier incoming first-valid clock differs')
            ready_before = bool(valid[pid]) and (not pending[pid] or t-clock[pid] >= 2)
            require(snapshot['ready_before'] is ready_before, 'barrier incoming readiness differs from frozen transport clock')
            require(type(record['decision']) is dict and set(record['decision']) == DECISION_KEYS,
                    'barrier decision schema differs')
            require({k:record['decision'][k] for k in CORE_KEYS} == core, 'barrier independent mathematics differs')
            reviewed.add(ri)
            active[pid] = dict(index=ri,record=record,core=core,failed=[],snapshot=deepcopy(snapshot))
        elif kind == 'inquiry_primary_barrier':
            require(pid in active, 'barrier apply without preceding review')
            item = active.pop(pid);ri = item['index'];record = item['record']
            require(event.get('record_index') == ri and ri not in applied, 'barrier apply record mismatch/reuse')
            decision, snapshot, core = record['decision'], item['snapshot'], item['core']
            before, allow = snapshot['ready_before'], core['allow_current_G2_ready']
            require(decision['recheck_failed_hospitals'] == item['failed'], 'barrier failed rechecks differ from actual intervening events')
            require(not item['failed'] or (allow and not before), 'barrier rechecked when the policy does not request a recheck')
            after = (before or bool(valid[pid])) if allow else False
            expected_clock = (core['primary_first_accept_t'] if core['primary_first_accept_t'] is not None else t) if valid[pid] else None
            expected = dict(ready_after=after,removed_premature_ready=before and not after,
                added_primary_barrier_ready=not before and after,applied_first_valid_t=expected_clock,
                barrier_latched_after=snapshot['barrier_latched_before'] and snapshot['current_auxiliary_present'])
            for key,value in expected.items():
                if type(value) is bool:
                    require(decision[key] is value, 'barrier applied boolean differs: ' + key)
                else:
                    require(decision[key] == value, 'barrier applied clock differs')
            require(event.get('ready_after') is after and event.get('reason') == core['reason'],
                    'barrier apply event differs from decision')
            require(((t,pid) in membership) is after, 'barrier readiness differs from actual same-minute G2 batch participation')
            if after:
                require(set(membership[t,pid]['options']) == set(valid[pid]),
                        'barrier-ready offers differ from actual G2 batch input')
            applied.add(ri);decisions_by_patient_time[t,pid] = decision
            clock[pid] = expected_clock
            if not expected['barrier_latched_after']:
                latched.discard(pid)
            counts['primary_barrier_reviews_checked'] += 1
            counts['primary_barrier_ready_memberships_checked'] += after
            counts['primary_barrier_blocked_reviews_checked'] += not allow
        elif kind == 'commit':
            if state[pid] == 'SEARCH' and valid[pid] and (has_auxiliary(pid) or pid in latched):
                require((t,pid) in decisions_by_patient_time and decisions_by_patient_time[t,pid]['ready_after'],
                        'auxiliary-wave commitment lacks an allowed primary barrier')
            state[pid] = 'TRANSIT';pending[pid].clear();valid[pid].clear();clock[pid] = None;latched.discard(pid)
        elif kind == 'arrive':
            state[pid] = 'QUEUED';pending[pid].clear()
        elif kind == 'treatment_start':
            state[pid] = 'TREATING'
        elif kind == 'arrival_unable':
            state[pid] = 'TRANSFER_WAIT'
        elif kind == 'transfer_search':
            state[pid] = 'SEARCH';pending[pid].clear();valid[pid].clear();clock[pid] = None;latched.discard(pid)
        elif kind == 'inquiry_retry_policy':
            latched.discard(pid)
        elif kind == 'no_compatible':
            state[pid] = 'NO_COMPATIBLE'
    finish_tick(last_t)
    require(reviewed == applied == set(range(len(records))), 'orphan or un-applied barrier record')
    require(roles == branch['inquiry_submission_roles'], 'inquiry role ledger differs from actual ask phases')
    expected_metrics = dict(primary_barrier_reviews=len(records),
        removed_auxiliary_early_decisions=sum(r['decision']['removed_premature_ready'] for r in records),
        released_primary_barrier_decisions=sum(r['decision']['added_primary_barrier_ready'] for r in records))
    for key,value in expected_metrics.items():
        require(type(branch['metrics'][key]) is int and branch['metrics'][key] == value,
                'primary barrier metric mismatch: ' + key)
    return counts


def audit_replay(replay, source_scenario=None):
    """Audit QJ evidence without executing an inquiry or allocation policy."""
    original = replay['original_scenario'];spec = qi_audit.spec_check(replay['policy_spec'])
    require(spec['retry'] == 'baseline', 'QJ must isolate the barrier from retry changes')
    if source_scenario is not None:
        require(original == source_scenario, 'original QJ scenario differs')
    fingerprint = common.canonical_sha256(original)
    require(replay['schema_version'] == 1 and replay['comparison_id'] == EXPERIMENT_ID and
            replay['source_experiment'] == 'online_allocation_comparison', 'QJ replay identity differs')
    require(replay['input_scenario_sha256'] == replay['original_scenario_sha256'] == fingerprint,
            'QJ input hash differs')
    physical = deepcopy(original)
    physical.update(online_allocation_comparison=True,source_experiment='online_allocation_comparison',
                    comparison_id=EXPERIMENT_ID)
    physical.setdefault('assumptions',[]).extend(qi_audit.ASSUMPTIONS + BARRIER_ASSUMPTIONS)
    require(replay['scenario'] == physical, 'QJ modified physical scenario inputs')
    require(replay['primary_barrier_contract'] == BARRIER_CONTRACT, 'top-level barrier contract differs')
    require(len(replay['branches']) == 1, 'single QJ branch required')
    branch = replay['branches'][0]
    require(branch['comparison_id'] == EXPERIMENT_ID and branch['primary_barrier_contract'] == BARRIER_CONTRACT,
            'QJ branch identity/contract differs')
    require(branch['execution_contract'].get('primary_wave_barrier') == BARRIER_CONTRACT,
            'QJ execution barrier differs')
    # Reuse only the old audit's G2/ledger checks on a detached projection. Its
    # branch ID stays QJ, and no executed old-QI control is fabricated.
    projected = deepcopy(replay)
    projected['comparison_id'] = qi_audit.EXPERIMENT_ID
    projected['scenario']['comparison_id'] = qi_audit.EXPERIMENT_ID
    projected['scenario']['assumptions'] = deepcopy(original.get('assumptions',[])) + deepcopy(qi_audit.ASSUMPTIONS)
    projected_branch = projected['branches'][0]
    projected_branch['comparison_id'] = qi_audit.EXPERIMENT_ID
    projected_branch['execution_contract'].pop('primary_wave_barrier')
    inherited = qi_audit.audit_replay(projected,source_scenario=original)
    counts = Counter(inherited['counters'])
    counts.update(_barrier_events(physical,branch))
    return dict(schema_version=1,passed=True,input_sha256=fingerprint,
        branch_metrics={spec['id']:common.branch_metrics(physical,branch)},
        patient_vectors={spec['id']:common._vectors(physical,branch)},counters=dict(counts),
        allocator_or_inquiry_planner_reexecuted=False,
        executed_QI_control_arm_claimed=False,executed_control_G2_arm_claimed=False,
        QI_projection_scope='unchanged G2 mathematics and actual inquiry ledger only; not an executed QI episode',
        barrier_scope='current actual submitted roles, received offers and same-minute G2-ready membership',
        whole_episode_nonregression_verified=False,resource_history_independently_reconstructed=False,
        synthetic_current_travel_measurement_independently_verified=False,clinical_effect_verified=False)
