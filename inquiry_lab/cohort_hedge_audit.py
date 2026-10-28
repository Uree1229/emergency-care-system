"""Independent original-prefix own CDF and barrier hedge search audit.

Frozen D is checked intact. Scores are reconstructed from public distributions,
not trusted recorded floating-point values. Priority snapshots are row/version
provenance: old public events do not contain the priority values themselves.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from . import cohort_dynamic_audit as base

ROOT = Path(__file__).resolve().parents[1]
BASE_AUDITOR_SHA256 = 'f3ca28f3a7a492c013a09b33a467f96e1faeb0aba7c41ed37ad33cb53381825b'
BASE_SOURCE_SHA256 = '62ef7fbba3ee90015f800aae7fb38f820955c7b92cd46d35df216c9a54afccfb'
if hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest() != BASE_AUDITOR_SHA256:
    raise ValueError('frozen D independent auditor differs')
if hashlib.sha256((ROOT/'inquiry_lab/cohort_dynamic_policy.py').read_bytes()).hexdigest() != BASE_SOURCE_SHA256:
    raise ValueError('frozen D producer source differs')
NAME = 'eat_cohort_hedge_l15_f'
BASE_POLICY = base.NAME
RULE = 'intact_D_menu_plus_complete_waiting_owner_local_origin_H_hedges'
OBJECTIVE = 'global tier 4,2,1 success sums within 1e-12, then capped wait within 1e-9; prefer D actual index then D menu order'
SCOPE = 'complete current WAITING/H-positive roster; first D cohort reference metrics plus residual owner-local static hedges; no future events, owner-future contention or actual future H agreement'
parent, horizon, joint = base.parent, base.horizon, base.joint
CohortHedgeAuditError = base.CohortDynamicAuditError
require, number, EPOCH = base.require, base.number, base.EPOCH
_same, _pid, _integer = base._same, base._pid, base._integer
INPUT_KEYS, CALL_KEYS = base.INPUT_KEYS, base.CALL_KEYS
GROUP_KEYS = base.GROUP_KEYS | {'owner_context', 'owner_source_snapshot'}
PRIORITY_SOURCE = 'captured current patient priority fields with common snapshot provenance'
P_EPSILON = 1e-12
W_EPSILON = 1e-9
NEW_INSTRUMENTATION = dict(base.NEW_INSTRUMENTATION,
    actual_workflow='inquiry_lab.cohort_hedge_runner.run_once',
    public_complete_waiting_owner_context_added=True, origin_clock_owner_hedge_surrogate=True)


def _project_name(name):
    return BASE_POLICY if name == NAME else '__other_policy__:'+str(name)


def alias_context(context):
    value = deepcopy(context)
    for row in value['eligible']:
        require(row['job']['policy_name'] == NAME, 'eligible public job has wrong native policy')
        row['job']['policy_name'] = BASE_POLICY
    return value


def alias_member(member):
    value = deepcopy(member)
    require(value['workflow_snapshot']['job']['policy_name'] == NAME, 'member job has wrong native policy')
    value['workflow_snapshot']['job']['policy_name'] = BASE_POLICY
    return value


def _alias_events(events):
    value = deepcopy(events)
    for event in value:
        if event['type'] in ('inquiry_workflow_started', 'inquiry_workflow_paused', 'inquiry_policy_changed'):
            data = parent._data(event)
            if 'policy_name' in data:
                event['data'] = dict(data, policy_name=_project_name(data['policy_name']))
    return value


def audit_registry(events, decisions, registry, now, event_prefix, decision_prefix):
    projected = deepcopy(registry)
    for job in projected['jobs']:
        job['policy_name'] = _project_name(job['policy_name'])
    context = base.audit_registry(_alias_events(events), decisions, projected, now, event_prefix, decision_prefix)
    for row in context['eligible']:
        row['job']['policy_name'] = NAME
    return context


def audit_time_context(events, inputs, member, event_prefix, decision_prefix, decision=None):
    return base.audit_time_context(_alias_events(events), inputs, alias_member(member),
        event_prefix, decision_prefix, decision)


def validate_tail_members(members, context, tails):
    return base.validate_tail_members([alias_member(m) for m in members], alias_context(context),
        [alias_member(m) for m in tails])


def audit_member_provenance(scenario, events, member, event_prefix, decision_prefix):
    return base.audit_member_provenance(scenario, _alias_events(events), alias_member(member),
        event_prefix, decision_prefix)




def audit_priority_context(priority, context, members, tails, *, lab_defaults=False):
    return base.audit_priority_context(priority, alias_context(context), [alias_member(m) for m in members],
        [alias_member(m) for m in tails], lab_defaults=lab_defaults)


public_model = base.public_model
precise_same = base.precise_same
exact_copy = base.exact_copy



OWNER_SOURCE = 'complete current WAITING inventory and public owner inputs from one committed snapshot'
MODEL_RULE = 'fixed_original_own_CDF_and_static_row_backlog_barrier_H_plan_with_every_wave_cap'
MODEL_SCOPE = 'origin-clock static conditional surrogate; no future replies, timeout observations, queue aging, other-owner future contention or actual future H agreement'
FULL_PATIENT_KEYS = set('id reporter_role relation contact age symptoms onset_at extra danger_signs priority priority_source required_capabilities resource_demands lat lon consent requested_at state version destination_hospital_id departed_at eta_at arrived_at needs_adjustment adjustment_note closed_reason location_updated_at location_version arrival_accounting accounting_reconciliation_id accounting_reconciled_at accounting_reconciled_by transport_baseline name birth address guardian guardian_phone consciousness breathing treatment_duration_seconds max_wait_seconds explicit_required_capabilities explicit_resource_demands'.split())
FULL_JOB_KEYS = set('patient_id enabled state started_at deadline_at next_evaluation_at round_no updated_at version last_error stop_reason policy_name policy_version policy_config'.split())
FULL_INQUIRY_KEYS = set('id patient_id hospital_id state reason question answer created_at last_sent_at attempts responded_at expires_at patient_version handled_by version'.split())
SOURCE_DB_SHA256 = 'd2807adcfe0cacbd40bcc8ac410c323ac93af05962d89ad7b36b1a1d184be69b'
if hashlib.sha256((ROOT/'server/db.py').read_bytes()).hexdigest() != SOURCE_DB_SHA256:
    raise ValueError('frozen full-row schema source differs')
REGISTRY_JOB_KEYS = {'patient_id', 'version', 'started_at', 'deadline_at', 'next_evaluation_at', 'enabled', 'policy_name'}
OWNER_INPUT_KEYS = INPUT_KEYS-{'time_context'}


def owner_key(pid):
    _pid(pid)
    return type(pid).__name__, str(pid)


def validate_owner_context(value, context, members, tails, priority):
    require(type(value) is dict and set(value) == {'schema_version', 'now', 'event_prefix_max',
        'decision_prefix_max', 'revision', 'source', 'first_cohort_patient_ids', 'patient_registry',
        'patient_created_events', 'inventory'}, 'owner context incomplete or unknown')
    require(type(value['schema_version']) is int and value['schema_version'] == 1 and value['source'] == OWNER_SOURCE,
        'owner source differs')
    audit_priority_context(priority, context, members, tails)
    for key in ('now', 'event_prefix_max', 'decision_prefix_max', 'revision'):
        require(type(value[key]) is int and value[key] == priority[key], 'owner common snapshot differs')
    first = members+tails; now = context['now']
    exact_copy(value['first_cohort_patient_ids'], [m['patient_id'] for m in first], 'first cohort partition')
    require(all(type(value[k]) is list for k in ('patient_registry', 'patient_created_events', 'inventory')),
        'complete owner inventory arrays required')
    people = {}
    for row in value['patient_registry']:
        require(type(row) is dict and set(row) == {'id', 'state', 'version'} and type(row['state']) is str,
            'owner patient registry projection differs')
        key = owner_key(row['id']); _integer(row['version'], 'owner patient version', 1)
        require(key not in people, 'duplicate registered owner'); people[key] = row
    births = {}; last = 0
    for row in value['patient_created_events']:
        require(type(row) is dict and set(row) == {'id', 'at', 'type', 'patient_id'} and row['type'] == 'patient_created',
            'owner creation projection differs')
        _integer(row['id'], 'creation event id', 1); _integer(row['at'], 'creation clock')
        key = owner_key(row['patient_id'])
        require(last < row['id'] <= value['event_prefix_max'] and row['at'] <= now and key not in births,
            'future, duplicated or unordered owner creation event')
        births[key] = row; last = row['id']
    require(set(births) == set(people), 'creation roster omitted or invented registered patient')
    expected = sorted((key for key, row in people.items() if row['state'] == 'WAITING' and now < births[key]['at']+300))
    require([owner_key(o['patient']['id']) for o in value['inventory']] == expected,
        'complete current WAITING positive-H inventory differs')
    by_first = {owner_key(m['patient_id']): m for m in first}
    by_priority = {owner_key(p['patient_id']): p for p in priority['patients']}
    require(set(by_first) <= set(expected), 'first cohort outside complete score inventory')
    for owner in value['inventory']:
        require(type(owner) is dict and set(owner) == {'patient', 'patient_created_event', 'evaluation_end_at',
            'job', 'inquiries', 'valid_acceptance_ids', 'inputs', 'apply_snapshot'}, 'owner fields differ')
        patient = owner['patient']
        require(type(patient) is dict and set(patient) == {'id', 'state', 'version', 'reported_priority',
            'priority_source', 'effective_priority'}, 'owner patient fields differ')
        pid = patient['id']; key = owner_key(pid)
        exact_copy({k: patient[k] for k in ('id', 'state', 'version')}, people[key], 'owner current patient')
        reported, source = patient['reported_priority'], patient['priority_source']
        require((reported is None or type(reported) is int and reported in (4, 2, 1))
            and source in ('expert', 'self', 'unknown') and type(source) is str, 'invalid owner priority')
        effective = reported if source == 'expert' and reported in (4, 2, 1) else 4
        require(type(patient['effective_priority']) is int and patient['effective_priority'] == effective,
            'owner effective priority differs')
        exact_copy(owner['patient_created_event'], births[key], 'owner creation source')
        require(type(owner['evaluation_end_at']) is int and owner['evaluation_end_at'] == births[key]['at']+300,
            'owner observation end differs')
        job, snapshot = owner['job'], owner['apply_snapshot']
        require(type(snapshot) is dict and set(snapshot) == horizon.APPLY_KEYS, 'owner apply snapshot fields differ')
        require(_pid(snapshot['patient_id']) == _pid(pid) and type(snapshot['patient_version']) is int
            and snapshot['patient_version'] == patient['version'] and type(snapshot['now']) is int
            and snapshot['now'] == now and type(snapshot['revision']) is int and snapshot['revision'] == value['revision'],
            'owner apply snapshot identity/clock/version differs')
        if job is None:
            require(snapshot['job_version'] is None, 'no-job owner invented job version')
        else:
            require(type(job) is dict and set(job) == REGISTRY_JOB_KEYS and _pid(job['patient_id']) == _pid(pid),
                'owner job projection differs')
            for field in ('version', 'started_at', 'deadline_at', 'next_evaluation_at', 'enabled'):
                _integer(job[field], 'owner job '+field, 1 if field == 'version' else 0)
            require(job['enabled'] in (0, 1) and job['deadline_at'] > job['started_at']
                and type(job['policy_name']) is str and type(snapshot['job_version']) is int
                and snapshot['job_version'] == job['version'], 'owner job state/version differs')
        inquiries = owner['inquiries']; require(type(inquiries) is list, 'owner inquiry inventory required')
        ids = {}; contacted = set(); last_id = 0
        for q in inquiries:
            require(type(q) is dict and set(q) == {'id', 'patient_id', 'hospital_id', 'state', 'last_sent_at'}
                and _pid(q['patient_id']) == _pid(pid) and type(q['hospital_id']) is str
                and q['hospital_id'] and type(q['state']) is str, 'owner inquiry fields differ')
            _integer(q['id'], 'owner inquiry id', 1); _integer(q['last_sent_at'], 'owner last sent')
            require(q['id'] > last_id and q['last_sent_at'] <= now and q['hospital_id'] not in contacted,
                'owner inquiry identity/clock/contact duplicate differs')
            ids[q['id']] = q; contacted.add(q['hospital_id']); last_id = q['id']
        accepted = owner['valid_acceptance_ids']
        require(type(accepted) is list and len(set(accepted)) == len(accepted)
            and all(type(i) is int and i in ids and ids[i]['state'] == 'ACCEPTED' for i in accepted),
            'owner valid acceptance projection differs')
        inputs = owner['inputs']
        require(type(inputs) is dict and set(inputs) == OWNER_INPUT_KEYS and not horizon._contains_private(inputs),
            'private or unknown original owner inputs')
        require(type(inputs['now']) is int and inputs['now'] == now and type(inputs['max_new']) is int
            and inputs['max_new'] == max(0, 10-len(contacted)) and inputs['last_contact_at']
            == max((q['last_sent_at'] for q in inquiries), default=None), 'owner remaining contact history differs')
        for field in ('observations', 'pending'):
            exact_copy(inputs[field], members[0]['inputs'][field], 'owner common '+field)
        require(type(inputs['patient_pending']) is list and type(inputs['candidates']) is list,
            'owner pending/offers missing')
        pending_keys = {(q['hospital'], _pid(q['patient']), q['submitted_at']) for q in inputs['pending']}
        own_keys = set()
        for q in inputs['patient_pending']:
            require(type(q) is dict and set(q) == {'hospital', 'patient', 'submitted_at', 'travel_seconds'}
                and _pid(q['patient']) == _pid(pid), 'owner own pending identity differs')
            k = (q['hospital'], _pid(q['patient']), q['submitted_at'])
            require(k in pending_keys and k not in own_keys, 'owner own pending missing/duplicated global source')
            number(q['travel_seconds'], 'owner pending travel'); own_keys.add(k)
        ranked = horizon._ranked(inputs['candidates'])
        require(not ({q['hospital'] for q in ranked} & contacted), 'owner offered a contacted hospital')
        if key in by_first:
            exact_copy(inputs, {k:v for k,v in by_first[key]['inputs'].items() if k != 'time_context'}, 'first cohort owner inputs')
            exact_copy(snapshot, by_first[key]['workflow_snapshot']['apply_snapshot'], 'first cohort apply snapshot')
            require(not accepted and job is not None, 'first-contact owner has acceptance or no job')
            exact_copy({k: job[k] for k in horizon.JOB_KEYS}, by_first[key]['workflow_snapshot']['job'], 'first owner job')
            eligible = next(r for r in context['eligible'] if owner_key(r['patient']['id']) == key)
            exact_copy(job, eligible['job'], 'first owner full due job projection')
            exact_copy({k: patient[k] for k in ('reported_priority', 'priority_source', 'effective_priority')},
                {k: by_priority[key][k] for k in ('reported_priority', 'priority_source', 'effective_priority')}, 'first owner priority')
    return value


def normalized_history(rows, now):
    return [{key: row[key] for key in ('hospital', 'submitted_at', 'observed_at', 'outcome')}
        for row in rows if type(row) is dict and now-1800 < row['observed_at'] <= now
        and row['submitted_at'] <= row['observed_at'] and row['submitted_at'] <= now
        and row['outcome'] in ('reply', 'accept', 'decline', 'timeout')]


def descriptor_cdf(item, x):
    if x <= 0: return 0.
    mu, sigma = item['service_mu'], item['service_sigma']
    if item['mode'] == 'queued': value = parent._cdf(x-item['wait_seconds'], mu, sigma)
    else: value = (parent._cdf(item['spent_seconds']+x, mu, sigma)-item['f0'])/max(1e-9, 1-item['f0'])
    return min(1., max(0., item['total_probability']*value))


def freeze_owner(owner):
    inputs = owner['inputs']; now = inputs['now']; end = owner['evaluation_end_at']; pid = owner['patient']['id']
    require(type(now) is int and type(end) is int and 0 <= now < end, 'owner remaining horizon invalid')
    history = normalized_history(inputs['observations'], now); beliefs = parent._beliefs(history, now)
    pending = deepcopy(inputs['pending']); own = inputs['patient_pending']; bins = horizon.intervals(end-now)
    items = []
    for row in own:
        hid, submitted = row['hospital'], row['submitted_at']; reply, accept, mu, sigma = beliefs(hid)
        ahead = sum(1. if q['submitted_at'] < submitted else .5 for q in pending
            if q['hospital'] == hid and q['submitted_at'] <= submitted and _pid(q['patient']) != _pid(pid))
        if ahead > 0:
            mode = 'queued'; wait = ahead*math.exp(mu+.5*sigma*sigma); spent = f0 = 0.; total = reply*accept
        else:
            freed = [q['observed_at'] for q in history if q['hospital'] == hid and q['outcome'] != 'timeout'
                and q['submitted_at'] <= submitted]
            spent = max(0., now-max([submitted, *freed])); f0 = parent._cdf(spent, mu, sigma)
            alive = max(1e-9, 1-f0); total = reply*alive/max(1e-9, 1-reply*f0)*accept; mode = 'residual'; wait = 0.
        item = dict(hospital=hid, travel_seconds=row['travel_seconds'], service_mu=mu, service_sigma=sigma,
            total_probability=total, mode=mode, wait_seconds=wait, spent_seconds=spent, f0=f0)
        item['masses'] = horizon.masses(lambda x, item=item: descriptor_cdf(item, x), 1., bins); items.append(item)
    return dict(schema_version=1, patient_id=pid, now=now, evaluation_end_at=end,
        cell_edges_seconds=[r for _,r in bins], original_pending=pending, normalized_history=history,
        original_all_own=[deepcopy(q) for q in pending if _pid(q['patient']) == _pid(pid)], own_items=items)


def occupancy(plan, all_own, now):
    return [dict(offset_seconds=s, original_fresh_count=sum(0 <= now+s-q['submitted_at'] <= 180 for q in all_own),
        planned_fresh_count=sum(t is not None and t <= s and s-t <= 180 for t in plan.values()),
        total_fresh_count=sum(0 <= now+s-q['submitted_at'] <= 180 for q in all_own)
            +sum(t is not None and t <= s and s-t <= 180 for t in plan.values()))
        for s in sorted({s for s in plan.values() if s is not None})]


def owner_metrics(items, bins, end, contact_cost):
    fail = math.prod(max(0., 1-sum(m)) for _,m in items)
    wait = joint._cost([(0,m) for _,m in items], bins, end, 0)
    arrival = joint._cost(items, bins, end, 1500)
    return dict(selection_probability=1-fail, failure_probability=fail, capped_wait_seconds=wait,
        arrival_failure_cost_seconds=arrival, planned_contact_cost_seconds=contact_cost, total_cost_seconds=arrival+contact_cost)


def new_cdf(record, offset, x):
    return record['reply_probability']*record['accept_probability']*parent._cdf(
        x-offset-record['estimated_wait_seconds'], record['service_mu'], record['service_sigma'])


def analytic_contact_cost(frozen, records, plan):
    by_hospital = {row['hospital']: row for row in records}
    value = 0.
    for hid,s in plan.items():
        if s is None: continue
        survival = math.prod(max(0., 1-descriptor_cdf(item,s)) for item in frozen['own_items'])
        survival *= math.prod(max(0., 1-new_cdf(by_hospital[h],t,s)) for h,t in plan.items() if t is not None)
        value += by_hospital[hid]['inquiry_cost_seconds']*survival
    return value


def score_owner(owner, frozen, row_pending):
    inputs = owner['inputs']; now = inputs['now']; end = owner['evaluation_end_at']; pid = owner['patient']['id']
    require(_pid(frozen['patient_id']) == _pid(pid) and frozen['now'] == now and frozen['evaluation_end_at'] == end,
        'fixed owner origin identity differs')
    exact_copy(frozen['original_pending'], inputs['pending'], 'fixed owner original prefix')
    require(type(row_pending) is list and row_pending[:len(inputs['pending'])] == inputs['pending']
        and all(q['submitted_at'] == now for q in row_pending[len(inputs['pending']):]), 'row added future/changed original pending')
    own_current = [(x['travel_seconds'], x['masses']) for x in frozen['own_items']]
    job = owner['job']
    if owner['valid_acceptance_ids']: reason = 'valid_acceptance'
    elif job is None: reason = 'no_job'
    elif not job['enabled']: reason = 'disabled_job'
    elif job['policy_name'] != NAME: reason = 'unsupported_policy'
    elif min(job['deadline_at'], end) <= now: reason = 'closed_new_contact_window'
    elif inputs['max_new'] == 0: reason = 'contact_budget_exhausted'
    else: reason = 'plan'
    pool = horizon._ranked(inputs['candidates'])[:min(6, inputs['max_new'])]
    if reason == 'plan' and not pool: reason = 'no_candidates'
    bins = horizon.intervals(end-now); plan = {q['hospital']: None for q in pool}; delta = None; offsets = []; records = []
    passes = 0; forced = False
    if reason == 'plan':
        barrier = max(now, job['started_at'], job['next_evaluation_at'] if frozen['original_all_own'] else now)
        delta = barrier-now; window = min(job['deadline_at'], end)-now
        offsets = sorted({delta+s for s in (0,15,30,60,90,120,180) if delta+s < window})
        if not offsets: reason = 'barrier_outside_new_contact_window'
    if reason == 'plan':
        rows = horizon.residual.residual_rows(dict(inputs, pending=row_pending)); beliefs = parent._beliefs(frozen['normalized_history'], now)
        for candidate, row in zip(pool, rows):
            # R records age=max(0., now-start). The frozen independent R
            # helper has the same value but emits int0 at a fresh queue.
            # Rebuild the public record's scalar type without relaxing score
            # or probability comparisons, or changing the frozen helper.
            if row['age_seconds'] is not None:
                row = dict(row, age_seconds=max(0., now-row['estimated_start_at']))
            accept = beliefs(candidate['hospital'])[1]
            records.append(dict(row, travel_seconds=candidate['travel_seconds'], accept_probability=accept, inquiry_cost_seconds=15))
    new = {row['hospital']: row for row in records}
    def cdf(hid, offset, x):
        row = new[hid]
        return row['reply_probability']*row['accept_probability']*parent._cdf(x-offset-row['estimated_wait_seconds'], row['service_mu'], row['service_sigma'])
    def items(p):
        return own_current+[(new[h]['travel_seconds'], horizon.masses(lambda x,h=h,s=s: cdf(h,s,x), 1., bins))
            for h,s in p.items() if s is not None]
    def feasible(p): return all(row['total_fresh_count'] <= 4 for row in occupancy(p, frozen['original_all_own'], now))
    def contact_cost(p): return analytic_contact_cost(frozen, records, p)
    def cost(p): return owner_metrics(items(p), bins, end-now, contact_cost(p))['total_cost_seconds']
    if reason == 'plan':
        best = cost(plan)
        for _ in range(2):
            passes += 1; improved = False
            for row in pool:
                hid = row['hospital']
                for offset in [None]+offsets:
                    if offset == plan[hid]: continue
                    trial = dict(plan); trial[hid] = offset
                    if not feasible(trial): continue
                    value = cost(trial)
                    if value < best-1e-9: plan, best, improved = trial, value, True
            if not improved: break
        fresh = sum(0 <= now+delta-q['submitted_at'] <= 180 for q in frozen['original_all_own'])
        if fresh == 0 and not any(s == delta for s in plan.values()) and pool and delta in offsets:
            options = []
            for row in pool:
                trial = dict(plan); trial[row['hospital']] = delta
                if feasible(trial): options.append((cost(trial), row['travel_seconds'], trial))
            if options: plan = min(options, key=lambda r: (r[0],r[1]))[2]; forced = True
        metrics = owner_metrics(items(plan), bins, end-now, contact_cost(plan))
    elif reason == 'valid_acceptance':
        metrics = dict(selection_probability=1., failure_probability=0., capped_wait_seconds=0.,
            arrival_failure_cost_seconds=0., planned_contact_cost_seconds=0., total_cost_seconds=0.)
    else: metrics = owner_metrics(own_current, bins, end-now, 0.)
    return dict(schema_version=1, rule=MODEL_RULE, scope=MODEL_SCOPE, patient_id=pid,
        effective_priority=owner['patient']['effective_priority'], planning_status=reason, barrier_offset_seconds=delta,
        allowed_offsets_seconds=offsets, queue_records=records,
        plan=[dict(hospital=q['hospital'], offset_seconds=plan[q['hospital']]) for q in pool],
        occupancy=occupancy(plan, frozen['original_all_own'], now), passes=passes, forced=forced, metrics=metrics)


def audit_pair(members, context, tails, outputs, evidence, priority_context, owner_context):
    require(type(evidence) is dict and type(evidence['original_dynamic_proposal']) is dict
        and set(evidence['original_dynamic_proposal']) == {'decisions', 'evidence'}, 'missing intact frozen D proposal')
    original = evidence['original_dynamic_proposal']; dm = [alias_member(m) for m in members]
    dt = [alias_member(m) for m in tails]; dc = alias_context(context)
    checked = base.audit_pair(dm, dc, dt, original['decisions'], original['evidence'], priority_context)
    validate_tail_members(members, context, tails)
    priorities = audit_priority_context(priority_context, context, members, tails)
    owners = validate_owner_context(owner_context, context, members, tails, priority_context)
    evaluated = original['evidence']['evaluated']; base_index = original['evidence']['selected_index']
    indices = original['evidence']['menu_indices']; selected = base_index; origins = []; scores = []; selection = None
    first_ids = {owner_key(pid) for pid in owners['first_cohort_patient_ids']}
    residual = [o for o in owners['inventory'] if owner_key(o['patient']['id']) not in first_ids]
    original_joint = base._nested_joint(original['evidence']['original_cohort_success_proposal'])
    plans = original_joint['evidence']['baseline_proposals']; models = [public_model(m['inputs']) for m in members]
    table = joint.enumerate_pairs(models, [p['send'] for p in plans])
    counters = Counter({k: 0 for k in ('hedge_owner_origins_checked', 'hedge_owner_scores_checked', 'hedge_coordinate_searches_checked',
        'hedge_occupancy_waves_checked', 'hedge_non_grid_barriers_checked', 'hedge_score_members_checked')})
    if evaluated:
        origins = [freeze_owner(o) for o in residual]
        require(1 <= len(indices) <= 3 and type(evidence['row_scores']) is list and len(evidence['row_scores']) == len(indices),
            'hedge left the exact D bounded menu')
        for position, (index, original_row, recorded) in enumerate(zip(indices, original['evidence']['row_scores'], evidence['row_scores'])):
            require(type(recorded) is dict and set(recorded) == {'row_index', 'first_cohort_score', 'owner_scores',
                'member_metrics', 'tier_probabilities', 'sum_capped_wait_seconds'}, 'hedge row fields differ')
            first, _ = base.audit_row(original_row, index, position, table['combinations'][index]['hospitals'],
                models, dm, dc, dt, priority_context)
            exact_copy(recorded['first_cohort_score'], original_row, 'intact independently audited D first-cohort row')
            pending = first['projection']['final_pending']
            owner_scores = [score_owner(owner, origin, pending) for owner,origin in zip(residual,origins)]
            metrics = deepcopy(first['member_metrics'])+[dict(patient_id=x['patient_id'],
                effective_priority=x['effective_priority'], metrics=x['metrics']) for x in owner_scores]
            require(sorted(owner_key(r['patient_id']) for r in metrics)
                == sorted(owner_key(o['patient']['id']) for o in owners['inventory']), 'complete scored owner partition differs')
            wanted = dict(row_index=index, first_cohort_score=original_row, owner_scores=owner_scores, member_metrics=metrics,
                tier_probabilities={str(t): sum((r['metrics']['selection_probability'] for r in metrics if r['effective_priority'] == t),0.) for t in (4,2,1)},
                sum_capped_wait_seconds=sum((r['metrics']['capped_wait_seconds'] for r in metrics),0.))
            precise_same(recorded, wanted, 'independent complete owner hedge row')
            scores.append(wanted)
            counters['hedge_owner_scores_checked'] += len(owner_scores)
            counters['hedge_coordinate_searches_checked'] += sum(x['planning_status'] == 'plan' for x in owner_scores)
            counters['hedge_occupancy_waves_checked'] += sum(len(x['occupancy']) for x in owner_scores)
            counters['hedge_non_grid_barriers_checked'] += sum(x['barrier_offset_seconds'] is not None and x['barrier_offset_seconds']%5 != 0 for x in owner_scores)
            counters['hedge_score_members_checked'] += len(metrics)
        counters['hedge_owner_origins_checked'] = len(origins)
        selection = base.base.global_staged_choice(scores, base_index)
        selection['base_selected_tied'] = selection.pop('base_budget_tied'); selected = selection['selected_index']
    else:
        require(indices == [], 'unevaluated D has a menu')
    intervened = selected != base_index
    wanted = dict(schema_version=1, rule=RULE, base_policy=BASE_POLICY, base_source_sha256=BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=NAME, calculation_policy=BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'], copied_only=True, public_identity_unchanged=True),
        joint_context=context, priority_context=priority_context, owner_context=owners, original_dynamic_proposal=original,
        evaluated=evaluated, menu_indices=indices, owner_origins=origins, row_scores=scores, selection=selection,
        base_selected_index=base_index, selected_index=selected, intervened=intervened, tiers=[4,2,1],
        tolerances=dict(probability=P_EPSILON, wait_seconds=W_EPSILON), objective=OBJECTIVE, scope=SCOPE)
    precise_same(evidence, wanted, 'complete hedge priority selection')
    require(type(outputs) is list and len(outputs) == 2, 'actual hedge output coverage differs')
    for index in range(2):
        actual = deepcopy(original['decisions'][index])
        if intervened:
            chosen = table['combinations'][selected]; send = chosen['hospitals'][index]
            actual = deepcopy(plans[index]); actual['send'] = list(send); actual['reason'] = 'cohort_hedge_current_only'
            ev = actual['evidence']; ev['sent_hospitals'] = list(send)
            ev['planned_expected_cost'] = round(chosen['member_metrics'][index]['total_cost_seconds'], 3)
            for row in ev['candidate_plan']: row['planned_offset_seconds'] = 0 if row['hospital'] in send else None
        actual['evidence']['policy'] = NAME
        actual['evidence']['cohort_hedge'] = dict(schema_version=1, mode='pair_cohort_hedge', evaluated=evaluated,
            intervened=intervened, member_index=index, base_selected_index=base_index, selected_index=selected,
            effective_priority=priorities[_pid(members[index]['patient_id'])])
        precise_same(outputs[index], actual, 'actual separately reconstructed hedge decision')
    checked = dict(checked, original_D_groups_applied=checked['joint_groups_applied'],
        joint_groups_applied=int(selected != table['baseline_index']))
    return dict(checked, **dict(counters), hedge_menus_evaluated=int(evaluated), hedge_rows_checked=len(scores),
        hedge_interventions=int(intervened), hedge_complete_inventory_members_checked=len(owners['inventory']))


def public_inquiries(events, now, prefix):
    rows = {}
    for event in events:
        if event['id'] > prefix: continue
        require(event['at'] <= now, 'future inquiry transition inside public prefix')
        typ = event['type']; data = parent._data(event); iid = data.get('inquiry_id')
        if typ in ('inquiry_sent', 'inquiry_retried'):
            _integer(iid, 'public inquiry id', 1)
            if typ == 'inquiry_sent':
                rows[iid] = dict(id=iid, patient_id=event['patient_id'], hospital_id=event['hospital_id'],
                    state='PENDING', last_sent_at=event['at'])
            else:
                require(iid in rows and rows[iid]['patient_id'] == event['patient_id']
                    and rows[iid]['hospital_id'] == event['hospital_id'], 'retry lost inquiry identity')
                rows[iid]['last_sent_at'] = event['at']
        elif iid in rows:
            if typ == 'inquiry_question': rows[iid]['state'] = 'CHECKING'
            elif typ == 'inquiry_responded':
                action = data.get('action')
                if action in ('accept', 'decline', 'checking'):
                    rows[iid]['state'] = {'accept':'ACCEPTED','decline':'DECLINED','checking':'CHECKING'}[action]
            elif typ in ('inquiry_closed', 'inquiry_cancelled'): rows[iid]['state'] = 'CANCELLED'
            elif typ == 'destination_confirmed': rows[iid]['state'] = 'CONFIRMED'
    return [rows[i] for i in sorted(rows)]


def audit_owner_provenance(scenario, events, registry, owners):
    now, prefix = owners['now'], owners['event_prefix_max']
    exact_copy(owners['patient_registry'], registry['patients'], 'complete registry used by owner capture')
    expected_births = [{k:e[k] for k in ('id','at','type','patient_id')}
        for e in events if e['id'] <= prefix and e['type'] == 'patient_created']
    exact_copy(owners['patient_created_events'], expected_births, 'complete prefix creation events')
    jobs = {owner_key(j['patient_id']): j for j in registry['jobs']}
    inquiries = public_inquiries(events, now, prefix)
    specs = {owner_key(p['id']): p for p in scenario['patients']}
    for owner in owners['inventory']:
        pid = owner['patient']['id']; key = owner_key(pid); inputs = owner['inputs']
        require(key in specs, 'owner outside registered scenario')
        spec = specs[key]; travel = spec['travel_seconds']
        exact_copy(owner['job'], jobs.get(key), 'current actual owner job or no-job source')
        expected = [q for q in inquiries if owner_key(q['patient_id']) == key]
        exact_copy(owner['inquiries'], expected, 'owner full contact history/state from public prefix')
        # Frozen lab acceptance TTL600 exceeds each patient H300 and all
        # public resource/capability rows remain sufficient. General live
        # validity still requires actual capture and prewrite source checks.
        exact_copy(owner['valid_acceptance_ids'], [q['id'] for q in expected if q['state'] == 'ACCEPTED'],
            'frozen lab valid public acceptance source')
        patient = owner['patient']
        require(patient['reported_priority'] is None and patient['priority_source'] == 'unknown'
            and type(patient['effective_priority']) is int and patient['effective_priority'] == 4,
            'frozen lab priority default changed')
        history, pending, own, _ = horizon._ledger(events, now, pid, prefix)
        exact_copy(inputs['observations'], history, 'owner prefix observations')
        exact_copy(inputs['pending'], pending, 'owner original global pending')
        exact_copy(inputs['patient_pending'], [dict(q, travel_seconds=travel[q['hospital']]) for q in own], 'complete own pending including outside new pool')
        contacted = {q['hospital_id'] for q in expected}
        ranked = horizon._ranked(inputs['candidates'])
        require([q['hospital'] for q in ranked] == sorted(set(travel)-contacted, key=lambda h:(travel[h],h))
            and all(q['travel_seconds'] == travel[q['hospital']] for q in ranked)
            and all(c.get('resources_enough') is True for c in inputs['candidates']),
            'owner complete uncontacted public offer universe/order differs')
        require(owner['evaluation_end_at'] == EPOCH+spec['at']+300, 'owner H differs from actual registered time')
    return len(owners['inventory'])


_audit_phase_order = base._audit_phase_order
time_context_from_prefix = audit_time_context


def audit_owner_source(source, owners, events):
    require(type(source) is dict and set(source) == {'schema_version', 'now', 'event_prefix_max',
        'decision_prefix_max', 'revision', 'rows', 'events'}, 'complete actual owner source guard missing')
    require(type(source['schema_version']) is int and source['schema_version'] == 1, 'owner source schema differs')
    for key in ('now', 'event_prefix_max', 'decision_prefix_max', 'revision'):
        require(type(source[key]) is int and source[key] == owners[key], 'owner full-source common snapshot differs')
    exact_copy(source['events'], [e for e in events if e['id'] <= owners['event_prefix_max']], 'actual source complete committed event prefix')
    require(type(source['rows']) is list and len(source['rows']) == len(owners['inventory']), 'full source guard omitted an owner')
    for row,owner in zip(source['rows'],owners['inventory']):
        require(type(row) is dict and set(row) == {'patient_id','patient','job','inquiries'}
            and _pid(row['patient_id']) == _pid(owner['patient']['id']), 'full source owner identity/order differs')
        patient = row['patient']; job = row['job']; inquiries = row['inquiries']
        require(type(patient) is dict and set(patient) == FULL_PATIENT_KEYS,
            'full source patient lacks actual priority/version')
        expected = dict(id=patient['id'], state=patient['state'], version=patient['version'],
            reported_priority=patient['priority'], priority_source=patient['priority_source'],
            effective_priority=patient['priority'] if patient['priority_source'] == 'expert' and patient['priority'] in (4,2,1) else 4)
        exact_copy(owner['patient'], expected, 'model owner projected from full actual patient')
        require(type(patient['requested_at']) is int and patient['requested_at'] == owner['patient_created_event']['at'], 'full patient registration clock differs')
        require(job is None or type(job) is dict and set(job) == FULL_JOB_KEYS, 'full source workflow lacks projection')
        exact_copy(owner['job'], None if job is None else {k: job[k] for k in REGISTRY_JOB_KEYS}, 'model owner actual job projection')
        require(type(inquiries) is list and all(type(q) is dict and set(q) == FULL_INQUIRY_KEYS
            and type(q['version']) is int and q['version'] > 0 for q in inquiries), 'full source inquiry versions missing')
        exact_copy(owner['inquiries'], [{k:q[k] for k in ('id','patient_id','hospital_id','state','last_sent_at')} for q in inquiries],
            'model owner full actual inquiry projection')
    return len(source['rows'])


def _audit_calls(scenario, branch):
    events, calls, decisions = branch['db_events'], branch['policy_calls'], branch['db_decisions']
    groups = branch['joint_groups']; specs = {p['id']: p for p in scenario['patients']}
    require(type(events) is list and type(calls) is list and type(decisions) is list
        and len(calls) == len(decisions) and type(groups) is list, 'call/decision/group inventory differs')
    _audit_phase_order(calls)
    require(all(type(e['id']) is int and e['id'] > 0 for e in events)
        and [e['id'] for e in events] == sorted({e['id'] for e in events}), 'event IDs not unique and ordered')
    event_ids = {e['id'] for e in events}; sent = [e for e in events if e['type'] == 'inquiry_sent']
    group_map = {}; checks = Counter()
    for index, group in enumerate(groups, 1):
        require(type(group) is dict and set(group) == GROUP_KEYS and type(group['id']) is int and group['id'] == index,
            'unknown, missing or reordered joint-group fields')
        for key in ('now', 'event_prefix_max', 'decision_prefix_max', 'revision'):
            _integer(group[key], 'group '+key)
        require(type(group['validated_before_write']) is bool and group['validated_before_write'],
            'group lacks all-member validation before first write')
        require(group['event_prefix_max'] in event_ids and group['db_decision_ids']
            == [group['decision_prefix_max']+1, group['decision_prefix_max']+2],
            'joint decisions not the next two recorded decisions at common prefix')
        wanted = audit_registry(events, decisions, group['registry_snapshot'], group['now'],
            group['event_prefix_max'], group['decision_prefix_max'])
        _same(group['context'], wanted, 'complete current public eligibility')
        require(len(wanted['pair_patient_ids']) == 2, 'group created without two eligible current patients')
        checks.update(audit_pair(group['members'], wanted, group['tail_members'], group['outputs'], group['evidence'], group['priority_context'], group['owner_context']))
        checks['hedge_owner_public_provenance_checked'] += audit_owner_provenance(scenario, events, group['registry_snapshot'], group['owner_context'])
        checks['hedge_full_source_owners_checked'] += audit_owner_source(group['owner_source_snapshot'], group['owner_context'], events)
        audit_priority_context(group['priority_context'], wanted, group['members'], group['tail_members'], lab_defaults=True)
        for tail in group['tail_members']:
            checks['tail_public_provenance_checked'] += audit_member_provenance(scenario, events, tail,
                group['event_prefix_max'], group['decision_prefix_max'])
        for member in group['members']+group['tail_members']:
            require(member['workflow_snapshot']['apply_snapshot']['revision'] == group['revision'],
                'member was not captured at common planning revision')
        group_map[group['id']] = group
    declared, actual, group_calls = Counter(), Counter(), Counter()
    previous = 0; last_prefix = 0
    for call, decision in zip(calls, decisions):
        grouped = 'joint_group_id' in call
        expected_keys = CALL_KEYS | {'joint_group_id', 'joint_member_index'} if grouped else CALL_KEYS
        require(type(call) is dict and set(call) == expected_keys, 'unknown or missing policy-call fields')
        pid, at, inputs, output = call['patient_id'], call['at'], call['inputs'], call['output']
        prefix = call['db_event_prefix_max']; now = EPOCH+at
        require(pid in specs and type(at) is int and specs[pid]['at'] <= at < specs[pid]['at']+300,
            'policy used future or out-of-window patient')
        require(type(prefix) is int and prefix >= last_prefix and prefix in event_ids,
            'invalid or reordered committed event prefix')
        last_prefix = prefix
        require(type(inputs) is dict and set(inputs) == INPUT_KEYS and not horizon._contains_private(inputs)
            and inputs['now'] == now, 'private, future or unknown policy input')
        require(call['name'] == branch['policy'] == decision['policy_name'] == NAME
            and decision['patient_id'] == pid and decision['at'] == now and type(decision['id']) is int
            and decision['id'] == call['db_decision_id'] > previous, 'committed policy identity/clock differs')
        _same(output['send'], decision['hospital_ids'], 'committed sends')
        _same(output['reason'], decision['reason'], 'committed reason')
        _same(output['evidence'], decision['evidence'], 'committed evidence')
        if grouped:
            gid, index = call['joint_group_id'], call['joint_member_index']
            require(type(gid) is int and gid in group_map and type(index) is int and index in (0, 1),
                'unknown joint-group or member index')
            group = group_map[gid]; member = group['members'][index]
            require(group['now'] == now and prefix == group['event_prefix_max']
                and decision['id'] == group['db_decision_ids'][index]
                and previous == group['decision_prefix_max']+index,
                'group decisions were interleaved or used different common prefixes')
            _same(member, dict(patient_id=pid, inputs=inputs, workflow_snapshot=call['workflow_snapshot']), 'linked group member')
            _same(output, group['outputs'][index], 'linked group output')
            dprefix = group['decision_prefix_max']; group_calls[gid, index] += 1
        else:
            member = dict(patient_id=pid, inputs=inputs, workflow_snapshot=call['workflow_snapshot'])
            dprefix = previous
            require(output['evidence'].get('joint_tail') == dict(schema_version=1, mode='horizon_fallback', guard_evaluated=False)
                and output['evidence'].get('joint_first') == dict(schema_version=1, mode='horizon_fallback', current_only=False)
                and output['evidence'].get('joint_budget') == dict(schema_version=1, mode='horizon_fallback', projection_evaluated=False)
                and output['evidence'].get('cohort_success') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False)
                and output['evidence'].get('cohort_dynamic') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False)
                and output['evidence'].get('cohort_hedge') == dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False),
                'ungrouped decision changed the original H fallback')
            normalized = deepcopy(output); normalized['evidence'].pop('joint_first')
            normalized['evidence'].pop('joint_tail')
            normalized['evidence'].pop('joint_budget')
            normalized['evidence'].pop('cohort_success')
            normalized['evidence'].pop('cohort_dynamic')
            normalized['evidence'].pop('cohort_hedge')
            normalized['evidence']['policy'] = horizon.NAME
            checks.update(horizon.audit_horizon(inputs, normalized))
        context = time_context_from_prefix(events, inputs, member, prefix, dprefix, decision)
        require(context['evaluation_end_at'] == EPOCH+specs[pid]['at']+300,
            'public H differs from registered scenario patient')
        previous = decision['id']
        history, pending, own, _ = horizon._ledger(events, now, pid, prefix)
        travel = specs[pid]['travel_seconds']
        expected_own = [dict(row, travel_seconds=travel[row['hospital']]) for row in own if row['hospital'] in travel]
        require(inputs['observations'] == history and inputs['pending'] == pending
            and inputs['patient_pending'] == expected_own, 'common-prefix public observation ledger differs')
        earlier = [e for e in sent if e['id'] <= prefix and e['patient_id'] == pid]
        contacted = {e['hospital_id'] for e in earlier}
        require(type(inputs['max_new']) is int and inputs['max_new'] == 10-len(contacted)
            and inputs['last_contact_at'] == max((e['at'] for e in earlier if e['at'] <= now), default=None),
            'original contact budget/history differs')
        ranked = horizon._ranked(inputs['candidates'])
        require({r['hospital'] for r in ranked} == set(travel)-contacted
            and all(r['travel_seconds'] == travel[r['hospital']] for r in ranked),
            'currently eligible uncontacted offers/travel differ from frozen lab')
        require([r['hospital'] for r in ranked] == sorted(set(travel)-contacted, key=lambda h: (travel[h], h))
            and all(c.get('resources_enough') is True for c in inputs['candidates']),
            'actual candidate order/resource eligibility differs from frozen public lab construction')
        require(type(output['send']) is list and len(set(output['send'])) == len(output['send'])
            and len(output['send']) <= inputs['max_new'] and set(output['send']) <= set(travel)-contacted
            and sum(now-r['submitted_at'] <= 180 for r in expected_own)+len(output['send']) <= 4,
            'contact count/candidate or original EAT concurrency constraint differs')
        for hid in output['send']:
            matching = [e for e in sent if e['patient_id'] == pid and e['hospital_id'] == hid
                and e['at'] == now and e['id'] > prefix]
            require(len(matching) == 1, 'decision lacks unique subsequent inquiry send')
            declared[matching[0]['id']] += 1
    require(group_calls == Counter({(gid, index): 1 for gid in group_map for index in (0, 1)}),
        'joint members/committed calls not covered exactly once')
    for group in groups:
        send_ids = [[e['id'] for e in sent if e['patient_id'] == m['patient_id']
            and e['id'] > group['event_prefix_max'] and e['at'] == group['now']]
            for m in group['members']]
        if all(send_ids):
            require(max(send_ids[0]) < min(send_ids[1]), 'new shared FIFO order differs from modeled member order')
    for event in sent:
        if event['patient_id'] in specs: actual[event['id']] += 1
    require(declared == actual, 'actual inquiries not covered exactly once by saved policy calls')
    return dict(policy_calls=len(calls), **dict(checks))


def audit_branch(scenario, branch):
    if branch.get('policy') != NAME:
        return base.audit_branch(scenario, branch)
    try:
        environment = hashlib.sha256(json.dumps(scenario, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        require(branch['environment_sha256'] == environment and branch['rule'] == 'immediate',
            'joint scenario binding or confirmation intervention differs')
        _same(branch['instrumentation'], NEW_INSTRUMENTATION, 'joint adapter provenance')
        checks = _audit_calls(scenario, branch); accounting = horizon._audit_trace_fixed(scenario, branch)
        total_j = sum(1800 if p['failed'] else p['estimated_arrival_seconds'] for p in branch['patients'])
        return dict(passed=True, **checks, **accounting, total_arrival_failure_cost_seconds=total_j,
            mean_arrival_failure_cost_seconds=total_j/len(branch['patients']) if branch['patients'] else None,
            finite_pair_table_independently_enumerated=True, public_registry_independently_reconstructed=True,
            intact_original_joint_proposal_independently_audited=True,
            intact_original_tail_proposal_independently_audited=True,
            intact_original_budget_proposal_independently_audited=True,
            intact_original_CS_proposal_independently_audited=True,
            row_specific_reference_CS_proposals_independently_audited=True,
            conditional_reference_CS_current_H_searches_independently_reexecuted=True,
            all_current_and_continuation_score_models_independently_rebuilt=True,
            tier_success_global_staged_selection_independently_recomputed=True,
            priority_values_reconstructed_from_event_payloads=False,
            priority_scope='captured priority rows bind complete current cohort identities, versions, clock, prefix and revision; frozen lab payload independently implies unknown/None/effective4; general live values require frozen capture/prewrite checks and rollback tests',
            projected_first_contact_H_coordinate_search_independently_reexecuted=True,
            paired_count_comparisons_independently_reconstructed=True,
            actual_hedge_outputs_and_sends_independently_audited=True,
            intact_original_D_proposal_independently_audited=True,
            owner_original_prefix_CDFs_independently_rebuilt=True,
            owner_every_wave_cap_and_analytic_contact_cost_independently_rebuilt=True,
            owner_barrier_coordinate_search_independently_reexecuted=True,
            owner_full_source_projection_independently_bound=True,
            tail_opportunity_scope='zero unresolved public inquiry count after proposed immediate pair; not free workers, beds or a success guarantee',
            H_coordinate_descent_independently_reexecuted=False,
            H_coordinate_descent_scope='actual original CS current H baseline and fallback remain source-bound; each conditional reference-CS current H baseline, its projected first contacts and per-row singleton are independently reexecuted; no clock advance or native-dynamic recursive optimization',
            physical_transaction_boundary_from_raw_proved=False,
            atomicity_evidence_scope='all-member checked common snapshots, linked two next decisions and ordered subsequent sends are audited; physical atomicity requires frozen adapter source and second-member rollback tests',
            silent_workflow_field_scope='next-review/version remain captured public job provenance; zero-journal active review equals latest start; member versions link actual snapshots to committed decisions',
            scope='complete current WAITING/H-positive registry partition, actual source guard and owner input provenance; intact D menu/first-cohort conditional reference rows; original-own CDF fixed across rows; static owner-local full cap-feasible H-derived plans with exact contact survival and no future reservations; actual all-patient FIFO/W/J/failure/contact/work audited')
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, StopIteration) as error:
        raise CohortHedgeAuditError('malformed saved cohort-hedge artifact') from error


def audit_replay(replay):
    require(type(replay) is dict and replay['schema_version'] == 1 and type(replay['branches']) is list,
        'invalid complete replay')
    require(replay['arms'] == [[b['policy'], b['rule']] for b in replay['branches']]
        and len({tuple(a) for a in replay['arms']}) == len(replay['arms']) > 0, 'arm inventory differs')
    require(all(b['environment_sha256'] == replay['environment_sha256']
        and b['snapshot_sha256'] == replay['initial_snapshot_sha256'] for b in replay['branches']),
        'paired initial snapshots/environment differ')
    checked = [audit_branch(replay['scenario'], b) for b in replay['branches']]
    return dict(passed=True, branches=len(checked), branch_audits=checked, new_executions=0)
