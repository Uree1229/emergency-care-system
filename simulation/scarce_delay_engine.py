"""Ask an alternative alongside the original scarce-hospital inquiry.

This isolated extension leaves the locked joint-policy experiment intact.
Current requests are registered as a cohort before any cohort inquiry, and
actual V3/V4 selectors retain their original candidate set and first inquiry.
One bounded supplemental inquiry uses current, verified alternatives when an
equally or more urgent observed patient has only the original hospital.
The extension never changes a route, reply calendar, acceptance or bed count.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time

from .h1_online_engine import _policy_spec
from .heuristic_assignment import HeuristicAssignmentRule
from .joint_policy_engine import (
    JointPolicyReplay, QuarterClockSimulation, _quarter,
    run_joint_policy_branch, validate_joint_scenario,
)
from .partial_information_engine import PartialInformationReplay
from .sim import Policy
from .stable_cp_sat_assignment import StableCPSATAssignmentRule

EXPERIMENT_ID = 'scarce_delay_20261016'
SOURCE_EXPERIMENT = 'joint_policy_comparison'
PENDING_WINDOW_MINUTES = 1


def _fresh_enough(views, demand):
    return all(resource in views and views[resource].get('status') == 'fresh'
               and type(views[resource].get('free')) in (int, float)
               and math.isfinite(views[resource]['free'])
               and views[resource]['free'] >= quantity
               for resource, quantity in demand.items())


def decide_scarce_candidates(snapshot):
    """Return the candidate subset using current registration and report data.

    A pending claim lasts only until its original one-minute/timeout deadline.
    A same-tick newly registered patient may be considered before its first
    inquiry; a failed inquiry does not recreate that initial opportunity.
    No scarce hospital is excluded without a fresh, sufficient alternative.
    """
    if not isinstance(snapshot, dict) or set(snapshot) != {
            't', 'context', 'patient', 'candidates', 'registered_search_patients'}:
        raise ValueError('exact current-opportunity snapshot required')
    t = snapshot['t']
    if not _quarter(t) or snapshot['context'] not in ('event_review', 'inquiry_candidates', 'initial_supplement'):
        raise ValueError('current quarter-minute context required')
    patient = snapshot['patient']
    candidates = snapshot['candidates']
    ids = [row['hospital'] for row in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError('candidate hospitals must be unique')
    for row in candidates:
        if set(row) != {'hospital', 'travel_minutes', 'resources'} \
                or type(row['travel_minutes']) not in (int, float) \
                or not math.isfinite(row['travel_minutes']) or row['travel_minutes'] < 0:
            raise ValueError('candidate must contain original travel and received resources')
    base_keys = {'id', 'priority', 'request_t', 'demand', 'required_capabilities',
                 'state', 'request_event_sequence', 'compatible_hospitals',
                 'inquiry_count', 'pending_inquiries'}
    def validate_patient(row, *, other=False):
        expected = base_keys | ({'resource_views'} if other else set())
        if set(row) != expected or row['state'] != 'SEARCH' \
                or not isinstance(row['id'], str) or not row['id'] \
                or not _quarter(row['request_t']) or row['request_t'] > t \
                or type(row['request_event_sequence']) is not int or row['request_event_sequence'] < 0 \
                or type(row['inquiry_count']) is not int or row['inquiry_count'] < 0 \
                or type(row['priority']) is not int or row['priority'] < 1:
            raise ValueError('only currently registered SEARCH patients are allowed')
        if not isinstance(row['demand'], dict) or any(not isinstance(resource, str)
                or type(quantity) is not int or quantity < 1
                for resource, quantity in row['demand'].items()):
            raise ValueError('positive observed resource demands required')
        if len(row['compatible_hospitals']) != len(set(row['compatible_hospitals'])):
            raise ValueError('structural candidates must be unique')
        for inquiry in row['pending_inquiries']:
            if set(inquiry) != {'hospital', 'attempt', 'submitted_at', 'timeout_at', 'ask_event_sequence'} \
                    or not _quarter(inquiry['submitted_at']) or inquiry['submitted_at'] > t \
                    or not _quarter(inquiry['timeout_at']) or inquiry['timeout_at'] < inquiry['submitted_at'] \
                    or type(inquiry['attempt']) is not int or inquiry['attempt'] < 1 \
                    or type(inquiry['ask_event_sequence']) is not int or inquiry['ask_event_sequence'] < 0:
                raise ValueError('original submitted inquiry metadata required')
    validate_patient(patient)
    observed = snapshot['registered_search_patients']
    if len(observed) != len({row['id'] for row in observed}):
        raise ValueError('registered patients must be unique')
    for row in observed:
        validate_patient(row, other=True)
    if patient['id'] not in {row['id'] for row in observed}:
        raise ValueError('inquiry patient must belong to the actual registered cohort')
    if any(hid not in patient['compatible_hospitals'] for hid in ids):
        raise ValueError('offered candidates must be structurally compatible')
    unchanged = dict(offered_after=list(ids), deferred=[], blocked=[], reason='UNCHANGED')
    own_beds = patient['demand'].get('ed_bed', 0)
    if len(patient['compatible_hospitals']) < 2 or own_beds <= 0:
        return unchanged
    by_hospital = {row['hospital']: row for row in candidates}
    claims = {}
    for waiter in sorted(observed, key=lambda row: (-row['priority'], row['request_t'], row['id'])):
        if waiter['id'] == patient['id'] or waiter['priority'] < patient['priority'] \
                or len(waiter['compatible_hospitals']) != 1 \
                or waiter['demand'].get('ed_bed', 0) <= 0:
            continue
        hid = waiter['compatible_hospitals'][0]
        candidate = by_hospital.get(hid)
        views = waiter['resource_views'].get(hid, {})
        if candidate is None or not _fresh_enough(views, waiter['demand']) \
                or not _fresh_enough(candidate['resources'], patient['demand']):
            continue
        pending = [row for row in waiter['pending_inquiries'] if row['hospital'] == hid]
        if len(pending) == 1:
            inquiry = pending[0]
            deadline = min(inquiry['submitted_at'] + PENDING_WINDOW_MINUTES, inquiry['timeout_at'])
            if t >= deadline:
                continue
            basis, ask_sequence = 'pending', inquiry['ask_event_sequence']
        elif not pending and waiter['request_t'] == t and waiter['inquiry_count'] == 0:
            deadline, basis, ask_sequence = t + PENDING_WINDOW_MINUTES, 'new_registration', None
        else:
            continue
        claims.setdefault(hid, []).append(dict(patient=waiter['id'], priority=waiter['priority'],
            demand=waiter['demand']['ed_bed'], request_event_sequence=waiter['request_event_sequence'],
            basis=basis, ask_event_sequence=ask_sequence, deadline=deadline))
    blocked = []
    for hid in ids:
        waiters = claims.get(hid, [])
        if not waiters:
            continue
        free = by_hospital[hid]['resources']['ed_bed']['free']
        demand = sum(row['demand'] for row in waiters)
        if free < own_beds + demand:
            blocked.append(dict(hospital=hid, known_free=free, patient_demand=own_beds,
                                waiting_demand=demand, waiters=waiters))
    if not blocked:
        return unchanged
    excluded = {row['hospital'] for row in blocked}
    safe = [hid for hid in ids if hid not in excluded
            and _fresh_enough(by_hospital[hid]['resources'], patient['demand'])]
    if not safe:
        return dict(offered_after=list(ids), deferred=[], blocked=blocked,
                    reason='NO_FRESH_ALTERNATIVE')
    return dict(offered_after=safe, deferred=[hid for hid in ids if hid not in safe],
                blocked=blocked, reason='SCARCE_ALTERNATIVE')


def decide_scarce_supplement(inputs):
    """Keep the actual original request and add at most one current alternative."""
    if not isinstance(inputs, dict) or set(inputs) != {'opportunity_snapshot',
            'original_ask_event_sequence', 'original_selector_event_sequence',
            'eligible_new_hospitals', 'max_new', 'episode_contacts', 'open_inquiries'}:
        raise ValueError('exact supplemental inquiry inputs required')
    for name in ('max_new', 'episode_contacts', 'open_inquiries',
                 'original_ask_event_sequence', 'original_selector_event_sequence'):
        if type(inputs[name]) is not int or inputs[name] < 0:
            raise ValueError('actual inquiry identities and budgets required')
    if inputs['open_inquiries'] > 3 or inputs['episode_contacts'] > 10 \
            or inputs['max_new'] != max(0, min(3 - inputs['open_inquiries'], 10 - inputs['episode_contacts'])):
        raise ValueError('original concurrency and episode-contact budget required')
    snapshot = inputs['opportunity_snapshot']
    if snapshot.get('context') != 'initial_supplement':
        raise ValueError('initial supplemental context required')
    opportunity = decide_scarce_candidates(snapshot)
    output = dict(opportunity_result=opportunity, send=[], reason='NO_ACTION')
    p = snapshot['patient']
    originals = [a for a in p['pending_inquiries']
                 if a['ask_event_sequence'] == inputs['original_ask_event_sequence']]
    if len(originals) != 1 or len(p['pending_inquiries']) != 1 \
            or p['inquiry_count'] != 1 or p['request_t'] != snapshot['t'] \
            or originals[0]['submitted_at'] != snapshot['t'] \
            or inputs['open_inquiries'] != 1 or inputs['episode_contacts'] != 1:
        raise ValueError('supplement must follow the actual first same-tick inquiry')
    eligible = inputs['eligible_new_hospitals']
    if len(eligible) != len(set(eligible)) or originals[0]['hospital'] in eligible:
        raise ValueError('supplement must use a unique new hospital')
    candidates = {row['hospital']: row for row in snapshot['candidates']}
    if any(hid not in candidates for hid in eligible):
        raise ValueError('new hospitals must have actual current candidate checks')
    if inputs['max_new'] == 0 or opportunity['reason'] != 'SCARCE_ALTERNATIVE' \
            or originals[0]['hospital'] not in {row['hospital'] for row in opportunity['blocked']}:
        return output
    safe = [hid for hid in opportunity['offered_after'] if hid in eligible]
    if not safe:
        return output
    hid = min(safe, key=lambda h: (candidates[h]['travel_minutes'], h))
    return dict(opportunity_result=opportunity, send=[hid], reason='INITIAL_SCARCE_SUPPLEMENT')


class CohortClockSimulation(QuarterClockSimulation):
    """Same clock order, registering this tick's real requests before asking."""
    def run(self):
        if not self.cohort_registration_enabled:
            return QuarterClockSimulation.run(self)
        end = self.cfg['horizon'] + self.cfg['tail']
        walk, outages, restores = list(self.walkins), list(self.outages), []
        for step in range((end - 1) * 4 + 1):
            t = step // 4 if step % 4 == 0 else step / 4
            self._receive_inquiry_feedback(t)
            self.before_tick(t)
            for h in self.hospitals.values():
                h.active = [a for a in h.active if a[0] > t]
            for item in [x for x in restores if x[0] == t]:
                restores.remove(item)
                self.hospitals[item[1]].cap[item[2]] += 1
                self.ev(t, 'resource_restored', hospital=item[1], resource=item[2])
            while outages and outages[0][0] == t:
                start, stop, hid, res = outages.pop(0)
                h = self.hospitals[hid]
                if h.cap.get(res, 0) > 0:
                    h.cap[res] -= 1
                    restores.append((stop, hid, res))
                    self.ev(t, 'resource_unavailable', hospital=hid, resource=res, until=stop)
                    if self.policy.notify:
                        self.notify_overflow(h, t, cause='resource_unavailable')
            while walk and walk[0][0] == t:
                _, hid, w = walk.pop(0)
                h = self.hospitals[hid]
                w.state, w.queued_t = 'QUEUED', t
                h.queue.append(w)
                self.ev(t, 'external_arrival', hospital=hid, patient=w.id)
                self.start_treatments(h, t)
                if self.policy.notify:
                    self.notify_overflow(h, t)
            requests = []
            while self.pending and self.pending[0].t_req == t:
                requests.append(self.pending.pop(0))
            priority_order = getattr(self.policy, 'priority_order', 'none')
            if priority_order in ('requests', 'all'):
                requests.sort(key=lambda p: (-p.priority, p.t_req, p.id))
            for p in requests:
                self.ev(t, 'request', patient=p.id, priority=p.priority, demand=p.demand, caps=sorted(p.caps))
            if requests:
                patients = [dict(patient=p.id, request_event_sequence=self._request_refs[p.id]) for p in requests]
                self.ev(t, 'request_cohort_registered', patients=deepcopy(patients))
                self.cohort_registration_records.append(dict(t=t, patients=patients,
                                                              event_sequence=self.log[-1]['event_sequence']))
            self._cohort_waiting_initial_asks = {p.id for p in requests}
            for p in requests:
                self._cohort_waiting_initial_asks.discard(p.id)
                self.send_asks(p, t)
            expand_after = getattr(self.policy, 'expand_after', None)
            patients = self.patients.values()
            if priority_order == 'all':
                patients = sorted((p for p in patients if p.t_req <= t),
                                  key=lambda p: (-p.priority, p.t_req, p.id))
            for p in patients:
                if p.state in ('SEARCH', 'TRANSIT'):
                    self.resolve_asks(p, t)
                if p.state == 'SEARCH' and expand_after is not None and not p.expanded and p.t_req <= t \
                        and t - p.t_req >= expand_after:
                    p.expanded = True
                    self.ev(t, 'expand', patient=p.id, reason='time')
                    self.send_asks(p, t)
                if p.state == 'SEARCH' and p.retry_at == t:
                    p.retry_at = None
                    self.send_asks(p, t)
                if getattr(self.policy, 'observed_inquiry', None) is not None and p.t_req <= t \
                        and p.retry_at is None and self._observed_review_at.get(p.id) != t \
                        and (p.state == 'SEARCH' or (p.state == 'TRANSIT' and p.notified and p.arrive_t > t)):
                    self.send_asks(p, t, exclude={p.dest} if p.state == 'TRANSIT' else ())
                if p.state == 'TRANSIT' and p.arrive_t == t:
                    h = self.hospitals[p.dest]
                    h.committed.pop(p.id, None)
                    p.asks, p.state, p.queued_t, p.final_arrive_t = [], 'QUEUED', t, t
                    h.queue.append(p)
                    self.ev(t, 'arrive', patient=p.id, hospital=p.dest)
            self.resolve_batch(t)
            for h in self.hospitals.values():
                self.start_treatments(h, t)
            self.after_tick(t)
        return self.metrics()


class ScarceDelayReplay(JointPolicyReplay, CohortClockSimulation):
    def __init__(self, *args, cohort_registration=True, scarce_routing=True, **kwargs):
        if type(cohort_registration) is not bool or type(scarce_routing) is not bool \
                or (scarce_routing and not cohort_registration):
            raise ValueError('scarce routing requires explicit same-tick cohort registration')
        self.cohort_registration_enabled = cohort_registration
        self.scarce_routing_enabled = scarce_routing
        self.cohort_registration_records, self.scarce_supplement_records = [], []
        self._supplement_reviewed_patients = set()
        self._request_refs = {}
        self._cohort_waiting_initial_asks = set()
        self._in_ordinary_event_review = False
        self.scarce_routing_seconds = 0.0
        super().__init__(*args, **kwargs)

    def ev(self, t, typ, **fields):
        super().ev(t, typ, **fields)
        if typ == 'request':
            self._request_refs[fields['patient']] = self.log[-1]['event_sequence']

    def _current_patient_view(self, p):
        compatible = sorted(hid for hid, h in self.hospitals.items()
            if p.caps <= h.caps and all(self.structural_capacities[hid].get(r, 0) >= q
                                      for r, q in p.demand.items()))
        return dict(id=p.id, priority=p.priority, request_t=p.t_req, demand=deepcopy(p.demand),
            required_capabilities=sorted(p.caps), state=p.state,
            request_event_sequence=self._request_refs[p.id], compatible_hospitals=compatible,
            inquiry_count=p.inquiries,
            pending_inquiries=[dict(hospital=a['hid'], attempt=a['attempt'], submitted_at=a['submitted_at'],
                timeout_at=a['timeout_at'], ask_event_sequence=a['ask_event_sequence']) for a in p.asks])

    def _event_review(self, t, phase):
        # Registration makes same-tick demand visible without allowing a
        # report wake to submit later cohort members ahead of their old turn.
        previous = self._in_ordinary_event_review
        self._in_ordinary_event_review = True
        try:
            return super()._event_review(t, phase)
        finally:
            self._in_ordinary_event_review = previous

    def candidates(self, p, loc, exclude=()):
        if self._in_ordinary_event_review and p.id in self._cohort_waiting_initial_asks:
            return []
        return super().candidates(p, loc, exclude=exclude)

    def send_asks(self, p, t, exclude=()):
        # Actual selector output and its original inquiry remain unchanged.
        super().send_asks(p, t, exclude=exclude)
        self._maybe_scarce_supplement(p, t)

    def _maybe_scarce_supplement(self, p, t):
        if not self.scarce_routing_enabled or self.inquiry_name not in ('v3', 'v4') \
                or p.id not in self._registered or p.state != 'SEARCH' \
                or p.id in self._supplement_reviewed_patients or p.t_req != t \
                or p.inquiries != 1 or len(p.asks) != 1 or p.asks[0]['submitted_at'] != t:
            return
        original = p.asks[0]
        selector = next((row for row in reversed(self.inquiry_decisions)
            if row['patient'] == p.id and row['t'] == t
            and row['decision']['send'] == [original['hid']]), None)
        if selector is None:
            return
        self._supplement_reviewed_patients.add(p.id)
        started = time.perf_counter()
        position = self.position(p, t)
        open_hospitals = {a['hid'] for a in p.asks}
        candidates = [hid for hid in self._pool(p, position, self.candidates(p, position))
            if hid not in p.tried and hid not in open_hospitals and hid not in p.valid]
        # A read-only ordinary inquiry eligibility check. Unknown data is not
        # promoted to fresh data and no new resource-query mechanism is used.
        self._check(p, original['hid'], t, 'inquiry')
        eligible = PartialInformationReplay.rank_inquiry_candidates(self, p, position, candidates, t)
        pool = [original['hid'], *eligible]
        patients = []
        for pid in sorted(self._registered):
            q = self.patients[pid]
            if q.state != 'SEARCH':
                continue
            row = self._current_patient_view(q)
            row['resource_views'] = {hid: deepcopy(self._views(hid, q.demand, t))
                for hid in row['compatible_hospitals']} if len(row['compatible_hospitals']) == 1 else {}
            patients.append(row)
        snapshot = dict(t=t, context='initial_supplement', patient=self._current_patient_view(p),
            candidates=[dict(hospital=hid, travel_minutes=self.travel(p, hid, position),
                             resources=deepcopy(self._views(hid, p.demand, t))) for hid in pool],
            registered_search_patients=patients)
        inputs = dict(opportunity_snapshot=snapshot, original_ask_event_sequence=original['ask_event_sequence'],
            original_selector_event_sequence=selector['event_sequence'], eligible_new_hospitals=list(eligible),
            max_new=max(0, min(self.policy.batch_size - len(p.asks), 10 - self._episode_contacts.get(p.id, 0))),
            episode_contacts=self._episode_contacts.get(p.id, 0), open_inquiries=len(p.asks))
        result = decide_scarce_supplement(inputs)
        self.ev(t, 'scarce_supplement_review', patient=p.id,
                record_index=len(self.scarce_supplement_records))
        self.scarce_supplement_records.append(dict(inputs=inputs, result=deepcopy(result),
                                                  event_sequence=self.log[-1]['event_sequence']))
        for hid in result['send']:
            attempt = p.attempts.get(hid, 0) + 1
            p.attempts[hid] = attempt
            scheduled = self.schedule_ask(p, hid, attempt, t)
            p.asks.append(scheduled)
            self._inquiry_feedback_pending.append(dict(submitted_t=t, due_t=scheduled['at'],
                hospital=hid, patient=p.id, silent=scheduled['silent']))
            self.inquiry_history[hid].append(t)
            p.tried.add(hid)
            p.inquiries += 1
            self.hosp_asks[hid] += 1
            self.ev(t, 'ask', patient=p.id, hospital=hid, attempt=attempt)
        self.scarce_routing_seconds += time.perf_counter() - started

    def run_replay(self):
        branch = super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID, source_experiment=SOURCE_EXPERIMENT,
            cohort_registration_enabled=self.cohort_registration_enabled,
            scarce_routing_enabled=self.scarce_routing_enabled,
            cohort_registration_records=deepcopy(self.cohort_registration_records),
            scarce_supplement_records=deepcopy(self.scarce_supplement_records))
        branch['metrics'].update(
            cohort_registration_batches=len(self.cohort_registration_records),
            scarce_supplement_reviews=len(self.scarce_supplement_records),
            scarce_supplement_sent=sum(len(row['result']['send']) for row in self.scarce_supplement_records),
            scarce_routing_wall_seconds=self.scarce_routing_seconds)
        return branch


def run_scarce_delay_branch(scenario, *, inquiry='v4', allocation='H1',
                           cohort_registration=True, scarce_routing=True,
                           routing=None, branch_id=None, label=None):
    validate_joint_scenario(scenario)
    if inquiry not in ('fixed_3', 'v3', 'v4') or allocation not in ('H1', 'C1') \
            or type(cohort_registration) is not bool or type(scarce_routing) is not bool \
            or (scarce_routing and not cohort_registration):
        raise ValueError('valid inquiry, allocation and explicit intervention flags required')
    code = branch_id or f'{inquiry}_{allocation}' + (
        '_scarce' if scarce_routing else '_cohort' if cohort_registration else '_baseline')
    if not cohort_registration and not scarce_routing:
        return run_joint_policy_branch(scenario, inquiry=inquiry, allocation=allocation,
            recovery=True, pending_protection=True, routing=routing, branch_id=code, label=label or code)
    rule = HeuristicAssignmentRule(time_limit_seconds=.05, max_moves=2048) if allocation == 'H1' else \
        StableCPSATAssignmentRule(time_limit_seconds=.05, seed=0, require_backend=True)
    policy = Policy(rule=rule, k=3, wait=2, notify=True, name=code,
                    observed_inquiry={} if inquiry != 'fixed_3' else None)
    simulation = ScarceDelayReplay(deepcopy(scenario), policy, routing=routing, inquiry=inquiry,
        recovery=True, pending_protection=True, cohort_registration=cohort_registration,
        scarce_routing=scarce_routing)
    branch = simulation.run_replay()
    branch.update(id=code, label=label or code, policy=_policy_spec(policy),
        scarce_delay_spec=dict(inquiry=inquiry, allocation=allocation,
            cohort_registration=cohort_registration, scarce_routing=scarce_routing,
            actual_action='parallel_scarce_supplement', original_selector_candidates_unchanged=True,
            maximum_supplements_per_patient=1,
            recovery=True, pending_protection=True, pending_window_minutes=PENDING_WINDOW_MINUTES,
            time_step_seconds=15, actual_selector_reused=True, extra_resource_change_publications=False))
    return branch


DEFAULT_SCARCE_DELAY_POLICIES = tuple(dict(id=f'v4_{allocation}_{stage}', label=f'v4_{allocation}_{stage}',
    inquiry='v4', allocation=allocation, cohort_registration=stage != 'baseline', scarce_routing=stage == 'scarce')
    for allocation in ('H1', 'C1') for stage in ('baseline', 'cohort', 'scarce')) + tuple(
    dict(id=f'fixed3_{allocation}_baseline', label=f'fixed3_{allocation}_baseline',
         inquiry='fixed_3', allocation=allocation, cohort_registration=False, scarce_routing=False)
    for allocation in ('H1', 'C1'))


def run_scarce_delay_replay(scenario, policies=None, execution_order=None, routing=None):
    validate_joint_scenario(scenario)
    specs = deepcopy(list(DEFAULT_SCARCE_DELAY_POLICIES if policies is None else policies))
    ids = [row['id'] for row in specs]
    if not ids or len(ids) != len(set(ids)) or any(not isinstance(code, str) or not code for code in ids):
        raise ValueError('unique nonempty policy IDs required')
    order = list(ids if execution_order is None else execution_order)
    if len(order) != len(ids) or set(order) != set(ids):
        raise ValueError('each policy must execute once')
    by_id = {row['id']: row for row in specs}
    output = {}
    for code in order:
        row = by_id[code]
        if not {'id', 'inquiry', 'allocation', 'cohort_registration', 'scarce_routing'} <= set(row) \
                or set(row) - {'id', 'label', 'inquiry', 'allocation', 'cohort_registration', 'scarce_routing'}:
            raise ValueError('exact scarce-delay policy row required')
        output[code] = run_scarce_delay_branch(scenario, inquiry=row['inquiry'], allocation=row['allocation'],
            cohort_registration=row['cohort_registration'], scarce_routing=row['scarce_routing'], routing=routing,
            branch_id=code, label=row.get('label', code))
    return dict(schema_version=1, experiment_id=EXPERIMENT_ID, scenario=deepcopy(scenario),
                branches=[output[code] for code in ids], execution_order=order)
