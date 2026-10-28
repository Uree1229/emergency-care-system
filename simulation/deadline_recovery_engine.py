"""Revalidate a received late supplemental reply without extending its inquiry.

The original handle expires and produces its original timeout observation. Work
continues in the unchanged hospital FIFO. Only an actually received non-silent
reply, at most fifteen seconds late, may become a separate new offer after
current patient, resource and lifecycle checks. Nothing waits for this callback.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
import time

from .h1_online_engine import _policy_spec
from .heuristic_assignment import HeuristicAssignmentRule
from .joint_policy_engine import validate_joint_scenario, _quarter
from .scarce_delay_engine import ScarceDelayReplay, run_scarce_delay_branch
from .sim import Policy
from .stable_cp_sat_assignment import StableCPSATAssignmentRule


EXPERIMENT_ID = 'deadline_recovery_20261016'
LATE_REPLY_WINDOW_MINUTES = .25


def condition_fingerprint(patient):
    """Hash only currently observed identity, priority, requirements and demands."""
    required = {'id', 'priority', 'demand', 'required_capabilities'}
    if not isinstance(patient, dict) or set(patient) != required \
            or not isinstance(patient['id'], str) or not patient['id'] \
            or type(patient['priority']) is not int or patient['priority'] < 1 \
            or not isinstance(patient['demand'], dict) or not patient['demand'] \
            or any(not isinstance(r, str) or type(q) is not int or q < 1
                   for r, q in patient['demand'].items()) \
            or not isinstance(patient['required_capabilities'], list) \
            or any(not isinstance(c, str) for c in patient['required_capabilities']) \
            or len(patient['required_capabilities']) != len(set(patient['required_capabilities'])):
        raise ValueError('observed patient identity, priority and requirements required')
    row = deepcopy(patient)
    row['required_capabilities'].sort()
    return sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')).hexdigest()


def decide_late_offer(snapshot):
    """Decide from a received reply and public current observations only."""
    if not isinstance(snapshot, dict) or set(snapshot) != {
            't', 'receipt', 'patient', 'resources', 'accept_ttl_minutes', 'registered_search_patients'}:
        raise ValueError('exact received late-offer snapshot required')
    t, receipt, patient = snapshot['t'], snapshot['receipt'], snapshot['patient']
    receipt_keys = {'patient', 'hospital', 'attempt', 'submitted_at', 'timeout_at',
        'ask_event_sequence', 'supplement_review_event_sequence', 'condition_fingerprint',
        'received_at', 'outcome', 'receipt_event_sequence'}
    patient_keys = {'id', 'state', 'condition', 'condition_fingerprint', 'latest_attempt', 'first_commit_t',
                    'valid_offers', 'registered_request_sequence'}
    if not _quarter(t) or not isinstance(receipt, dict) or set(receipt) != receipt_keys \
            or not isinstance(patient, dict) or set(patient) != patient_keys:
        raise ValueError('current public patient and received reply required')
    if any(not isinstance(receipt[k], str) or not receipt[k] for k in ('patient', 'hospital')) \
            or any(type(receipt[k]) is not int or receipt[k] < 0 for k in
                ('ask_event_sequence', 'supplement_review_event_sequence', 'receipt_event_sequence')) \
            or type(receipt['attempt']) is not int or receipt['attempt'] < 1 \
            or any(not _quarter(receipt[k]) for k in ('submitted_at', 'timeout_at', 'received_at')) \
            or receipt['timeout_at'] < receipt['submitted_at'] \
            or receipt['received_at'] != t or receipt['outcome'] not in ('accept', 'decline'):
        raise ValueError('original public handle and actual reply receipt required')
    if patient['id'] != receipt['patient'] or patient['condition']['id'] != patient['id'] \
            or patient['condition_fingerprint'] != condition_fingerprint(patient['condition']) \
            or type(patient['latest_attempt']) is not int or patient['latest_attempt'] < receipt['attempt'] \
            or type(patient['registered_request_sequence']) is not int \
            or patient['registered_request_sequence'] < 0 \
            or type(snapshot['accept_ttl_minutes']) not in (int, float) \
            or not math.isfinite(snapshot['accept_ttl_minutes']) or snapshot['accept_ttl_minutes'] <= 0:
        raise ValueError('registered current patient and actual offer lifetime required')
    if not isinstance(patient['valid_offers'], list) or any(set(o) != {'hospital', 'expires_at', 'resources'}
            or not isinstance(o['hospital'], str) or not _quarter(o['expires_at'])
            for o in patient['valid_offers']):
        raise ValueError('current offer identities and original expiries required')
    if not isinstance(snapshot['resources'], dict):
        raise ValueError('received resource views required')
    observed = snapshot['registered_search_patients']
    if not isinstance(observed, list) or len(observed) != len({q['id'] for q in observed}):
        raise ValueError('unique actually registered SEARCH patients required')
    for q in observed:
        if set(q) != {'id', 'condition', 'state', 'request_t', 'request_event_sequence',
                'compatible_hospitals', 'pending_inquiries', 'valid_offers', 'resource_views'} \
                or q['id'] != q['condition']['id'] or q['state'] != 'SEARCH' \
                or not _quarter(q['request_t']) or q['request_t'] > t \
                or type(q['request_event_sequence']) is not int or q['request_event_sequence'] < 0:
            raise ValueError('current registered demands and original inquiries required')
        condition_fingerprint(q['condition'])
        if len(q['compatible_hospitals']) != len(set(q['compatible_hospitals'])):
            raise ValueError('unique structural hospitals required')
        for ask in q['pending_inquiries']:
            if set(ask) != {'hospital', 'attempt', 'submitted_at', 'timeout_at', 'ask_event_sequence'} \
                    or not _quarter(ask['submitted_at']) or ask['submitted_at'] > t \
                    or not _quarter(ask['timeout_at']) or ask['timeout_at'] < ask['submitted_at'] \
                    or type(ask['attempt']) is not int or ask['attempt'] < 1 \
                    or type(ask['ask_event_sequence']) is not int or ask['ask_event_sequence'] < 0:
                raise ValueError('pending opportunity needs original public handle')
        for offer in q['valid_offers']:
            if set(offer) != {'hospital', 'accepted_at', 'expires_at', 'accept_event_sequence'} \
                    or not _quarter(offer['accepted_at']) or offer['accepted_at'] > t \
                    or not _quarter(offer['expires_at']) \
                    or type(offer['accept_event_sequence']) is not int or offer['accept_event_sequence'] < 0:
                raise ValueError('valid opportunity needs received acceptance and expiry')
    result = dict(eligible=False, hospital=None, expires_at=None,
                  protected_patients=[], reason='OUTSIDE_WINDOW')
    if not receipt['timeout_at'] < t <= receipt['timeout_at'] + LATE_REPLY_WINDOW_MINUTES:
        return result
    if patient['state'] != 'SEARCH':
        return dict(result, reason='NOT_SEARCHING')
    if patient['first_commit_t'] is not None:
        return dict(result, reason='PREVIOUS_COMMITMENT')
    if patient['condition_fingerprint'] != receipt['condition_fingerprint']:
        return dict(result, reason='CONDITION_CHANGED')
    if patient['latest_attempt'] != receipt['attempt']:
        return dict(result, reason='NEWER_INQUIRY')
    if any(o['expires_at'] > t and all(v.get('status') == 'fresh'
            and type(v.get('free')) in (int, float) and math.isfinite(v['free'])
            and v['free'] >= quantity for resource, quantity in patient['condition']['demand'].items()
            for v in [o['resources'].get(resource, {})]) for o in patient['valid_offers']):
        return dict(result, reason='OTHER_VALID_OFFER')
    if receipt['outcome'] != 'accept':
        return dict(result, reason='HOSPITAL_DECLINED')
    for resource, quantity in patient['condition']['demand'].items():
        view = snapshot['resources'].get(resource, {})
        if view.get('status') != 'fresh' or type(view.get('free')) not in (int, float) \
                or not math.isfinite(view['free']) or view['free'] < quantity:
            return dict(result, reason='RESOURCE_NOT_VERIFIED')
    claims = []
    own_beds = patient['condition']['demand'].get('ed_bed', 0)
    for q in observed:
        if q['id'] == patient['id'] or q['condition']['priority'] < patient['condition']['priority'] \
                or q['compatible_hospitals'] != [receipt['hospital']]:
            continue
        demand = q['condition']['demand']
        views = q['resource_views'].get(receipt['hospital'], {})
        if demand.get('ed_bed', 0) <= 0 or any(v.get('status') != 'fresh'
                or type(v.get('free')) not in (int, float) or not math.isfinite(v['free'])
                or v['free'] < demand[r] for r in demand for v in [views.get(r, {})]):
            continue
        pending = any(a['hospital'] == receipt['hospital']
            and t < min(a['submitted_at'] + 1, a['timeout_at']) for a in q['pending_inquiries'])
        offered = any(o['hospital'] == receipt['hospital'] and o['expires_at'] > t
                      for o in q['valid_offers'])
        if pending or offered:
            claims.append(q)
    if own_beds and claims and snapshot['resources']['ed_bed']['free'] < own_beds + sum(
            q['condition']['demand']['ed_bed'] for q in claims):
        return dict(result, reason='SCARCE_OPPORTUNITY',
                    protected_patients=sorted(q['id'] for q in claims))
    return dict(eligible=True, hospital=receipt['hospital'],
                expires_at=t + snapshot['accept_ttl_minutes'],
                protected_patients=[], reason='RECEIVED_LATE_OFFER')


class DeadlineRecoveryReplay(ScarceDelayReplay):
    def __init__(self, *args, late_reply_recovery=True, **kwargs):
        if type(late_reply_recovery) is not bool or not late_reply_recovery \
                or not kwargs.get('cohort_registration', True) or not kwargs.get('scarce_routing', True):
            raise ValueError('late recovery requires an initial scarce supplement')
        self.late_reply_recovery_enabled = True
        self.late_offer_records, self.late_offer_callbacks = [], []
        self._late_callbacks, self._callback_patients = {}, set()
        self._received_callback_keys = set()
        self.late_offer_seconds = 0.0
        super().__init__(*args, **kwargs)

    @staticmethod
    def _condition(p):
        return dict(id=p.id, priority=p.priority, demand=deepcopy(p.demand),
                    required_capabilities=sorted(p.caps))

    def ev(self, t, typ, **fields):
        super().ev(t, typ, **fields)
        if typ != 'ask' or fields['patient'] in self._callback_patients:
            return
        p = self.patients[fields['patient']]
        row = next((r for r in reversed(self.scarce_supplement_records)
            if r['inputs']['opportunity_snapshot']['patient']['id'] == p.id
            and r['inputs']['opportunity_snapshot']['t'] == t
            and fields['hospital'] in r['result']['send']), None)
        if row is None:
            return
        ask = self.log[-1]
        condition = self._condition(p)
        callback = dict(patient=p.id, hospital=fields['hospital'], attempt=fields['attempt'],
            submitted_at=t, timeout_at=ask['timeout_at'], ask_event_sequence=ask['event_sequence'],
            supplement_review_event_sequence=row['event_sequence'],
            condition_fingerprint=condition_fingerprint(condition))
        self._callback_patients.add(p.id)
        self._late_callbacks[p.id, fields['hospital'], fields['attempt']] = callback
        self.ev(t, 'late_offer_callback_registered', **deepcopy(callback), condition=condition)
        self.late_offer_callbacks.append(dict(callback, condition=condition,
                                              event_sequence=self.log[-1]['event_sequence']))

    def _receive_late_offers(self, p, t):
        # Read the current delivery slot only. Future slots, potential service
        # times, silence flags and worker calendars never enter this policy.
        current = self._review_events.get(t, [])
        arrivals = [e for e in current if e['type'] == 'late_reply'
            and e.get('patient') == p.id
            and (p.id, e['hospital'], e['attempt']) in self._late_callbacks]
        for late in arrivals:
            key = (p.id, late['hospital'], late['attempt'])
            if key in self._received_callback_keys:
                raise AssertionError('late callback delivered twice')
            callback = self._late_callbacks[key]
            completed = next((e for e in current if e['type'] == 'review_completed'
                and e.get('patient') == p.id and e.get('hospital') == late['hospital']
                and e.get('attempt') == late['attempt']), None)
            if completed is None:
                raise AssertionError('late reply lacks current completed hospital work')
            self._received_callback_keys.add(key)
            current.remove(completed)
            self.ev(t, 'review_completed', **{k:v for k,v in completed.items() if k != 'type'})
            current.remove(late)
            # ignored=True continues to refer to the expired original ask.
            self.ev(t, 'late_reply', **{k:v for k,v in late.items() if k != 'type'})
            outcome = 'accept' if self.hospitals[late['hospital']].can_accept(p) else 'decline'
            receipt = dict(deepcopy(callback), received_at=t, outcome=outcome)
            self.ev(t, 'hospital_reply_received', **deepcopy(receipt))
            receipt['receipt_event_sequence'] = self.log[-1]['event_sequence']
            started = time.perf_counter()
            self._information_cycle(t)
            condition = self._condition(p)
            observed = []
            for pid in sorted(self._registered):
                q = self.patients[pid]
                if q.state != 'SEARCH':
                    continue
                view = self._current_patient_view(q)
                observed.append(dict(id=q.id, condition=self._condition(q), state=q.state,
                    request_t=q.t_req, request_event_sequence=self._request_refs[q.id],
                    compatible_hospitals=view['compatible_hospitals'],
                    pending_inquiries=view['pending_inquiries'],
                    valid_offers=[dict(hospital=hid, accepted_at=q.accepted_at[hid], expires_at=expiry,
                        accept_event_sequence=self._accept_refs[q.id, hid])
                        for hid, expiry in sorted(q.valid.items())],
                    resource_views={hid:deepcopy(self._views(hid,q.demand,t))
                        for hid in view['compatible_hospitals']} if len(view['compatible_hospitals']) == 1 else {}))
            snapshot = dict(t=t, receipt=receipt, patient=dict(id=p.id, state=p.state,
                condition=condition, condition_fingerprint=condition_fingerprint(condition),
                latest_attempt=p.attempts[late['hospital']], first_commit_t=p.t_commit,
                valid_offers=[dict(hospital=hid, expires_at=expiry,
                                  resources=deepcopy(self._views(hid,p.demand,t)))
                    for hid, expiry in sorted(p.valid.items())],
                registered_request_sequence=self._request_refs[p.id]),
                resources=deepcopy(self._views(late['hospital'], p.demand, t)),
                accept_ttl_minutes=self.cfg['accept_ttl'], registered_search_patients=observed)
            result = decide_late_offer(snapshot)
            self.ev(t, 'late_offer_review', patient=p.id, hospital=late['hospital'],
                    record_index=len(self.late_offer_records))
            record = dict(snapshot=snapshot, result=deepcopy(result),
                          event_sequence=self.log[-1]['event_sequence'])
            self.late_offer_records.append(record)
            if result['eligible']:
                p.valid[late['hospital']] = result['expires_at']
                p.accepted_at[late['hospital']] = t
                # Old TTL entries can remain in the ledger while no longer
                # being usable. Their earlier wait clock cannot accelerate a
                # newly received late offer ahead of nearer pending replies.
                p.first_valid_t = t
                self.ev(t, 'late_offer_accepted', patient=p.id, hospital=late['hospital'],
                    attempt=late['attempt'], ask_event_sequence=callback['ask_event_sequence'],
                    receipt_event_sequence=receipt['receipt_event_sequence'],
                    review_event_sequence=record['event_sequence'], expires_at=result['expires_at'])
                late_accept_sequence = self.log[-1]['event_sequence']
                self.ev(t, 'accept', patient=p.id, hospital=late['hospital'],
                        late_offer_event_sequence=late_accept_sequence,
                        receipt_event_sequence=receipt['receipt_event_sequence'])
            self.late_offer_seconds += time.perf_counter() - started

    def resolve_asks(self, p, t):
        self._receive_late_offers(p, t)
        return super().resolve_asks(p, t)

    def resolve_batch(self, t):
        # Terminal and transfer-wait patients do not enter resolve_asks. Their
        # already submitted hospital work still finishes; record the rejection.
        for pid in sorted(self._callback_patients):
            p = self.patients[pid]
            if p.state not in ('SEARCH', 'TRANSIT'):
                self._receive_late_offers(p, t)
        return super().resolve_batch(t)

    def run_replay(self):
        branch = super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID, late_reply_recovery_enabled=True,
            late_offer_records=deepcopy(self.late_offer_records),
            late_offer_callbacks=deepcopy(self.late_offer_callbacks))
        branch['metrics'].update(late_offer_receipts=len(self.late_offer_records),
            late_offer_accepted=sum(r['result']['eligible'] for r in self.late_offer_records),
            late_offer_rejected=sum(not r['result']['eligible'] for r in self.late_offer_records),
            late_offer_wall_seconds=self.late_offer_seconds)
        return branch


def run_deadline_recovery_branch(scenario, *, inquiry='v4', allocation='H1',
        cohort_registration=True, scarce_routing=True, late_reply_recovery=False,
        routing=None, branch_id=None, label=None):
    validate_joint_scenario(scenario)
    if inquiry not in ('fixed_3', 'v3', 'v4') or allocation not in ('H1', 'C1') \
            or any(type(flag) is not bool for flag in
                   (cohort_registration, scarce_routing, late_reply_recovery)) \
            or (scarce_routing and not cohort_registration) \
            or (late_reply_recovery and (not scarce_routing or inquiry == 'fixed_3')):
        raise ValueError('explicit compatible inquiry and late-recovery flags required')
    code = branch_id or f'{inquiry}_{allocation}_' + (
        'late' if late_reply_recovery else 'scarce' if scarce_routing else
        'cohort' if cohort_registration else 'baseline')
    if not late_reply_recovery:
        return run_scarce_delay_branch(scenario, inquiry=inquiry, allocation=allocation,
            cohort_registration=cohort_registration, scarce_routing=scarce_routing,
            routing=routing, branch_id=code, label=label or code)
    rule = HeuristicAssignmentRule(time_limit_seconds=.05, max_moves=2048) if allocation == 'H1' else \
        StableCPSATAssignmentRule(time_limit_seconds=.05, seed=0, require_backend=True)
    policy = Policy(rule=rule, k=3, wait=2, notify=True, name=code, observed_inquiry={})
    replay = DeadlineRecoveryReplay(deepcopy(scenario), policy, routing=routing, inquiry=inquiry,
        recovery=True, pending_protection=True, cohort_registration=True, scarce_routing=True,
        late_reply_recovery=True)
    branch = replay.run_replay()
    branch.update(id=code, label=label or code, policy=_policy_spec(policy),
        scarce_delay_spec=dict(inquiry=inquiry, allocation=allocation,
            cohort_registration=True, scarce_routing=True,
            actual_action='parallel_scarce_supplement', original_selector_candidates_unchanged=True,
            maximum_supplements_per_patient=1, recovery=True, pending_protection=True,
            pending_window_minutes=1, time_step_seconds=15, actual_selector_reused=True,
            extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True,
            scope='one_initial_scarce_supplement', late_reply_window_minutes=LATE_REPLY_WINDOW_MINUTES,
            original_timeout_unchanged=True, original_timeout_feedback_unchanged=True,
            blocking_wait_added=False, hospital_work_calendar_unchanged=True,
            patient_condition_fingerprint_required=True, new_offer_ttl_from_actual_receipt=True,
            actual_reply_outcome_required=True, current_fresh_resource_check_required=True,
            current_valid_offer_rejects=True, newer_same_hospital_attempt_rejects=True))
    return branch


DEFAULT_DEADLINE_RECOVERY_POLICIES = tuple(dict(id=f'v4_{allocation}_{stage}',
    label=f'v4_{allocation}_{stage}', inquiry='v4', allocation=allocation,
    cohort_registration=stage != 'baseline', scarce_routing=stage != 'baseline',
    late_reply_recovery=stage == 'late')
    for allocation in ('H1', 'C1') for stage in ('baseline', 'scarce', 'late')) + tuple(
    dict(id=f'fixed3_{allocation}_baseline', label=f'fixed3_{allocation}_baseline',
         inquiry='fixed_3', allocation=allocation, cohort_registration=False,
         scarce_routing=False, late_reply_recovery=False) for allocation in ('H1', 'C1'))


def run_deadline_recovery_replay(scenario, policies=None, execution_order=None, routing=None):
    validate_joint_scenario(scenario)
    specs = deepcopy(list(DEFAULT_DEADLINE_RECOVERY_POLICIES if policies is None else policies))
    ids = [row['id'] for row in specs]
    if not ids or len(ids) != len(set(ids)) or any(not isinstance(code, str) or not code for code in ids):
        raise ValueError('unique nonempty policy IDs required')
    order = list(ids if execution_order is None else execution_order)
    if len(order) != len(ids) or set(order) != set(ids):
        raise ValueError('each policy must execute once')
    by_id, output = {row['id']: row for row in specs}, {}
    for code in order:
        row = by_id[code]
        if not {'id', 'inquiry', 'allocation', 'cohort_registration', 'scarce_routing',
                'late_reply_recovery'} <= set(row) or set(row) - {'id', 'label', 'inquiry',
                'allocation', 'cohort_registration', 'scarce_routing', 'late_reply_recovery'}:
            raise ValueError('exact deadline-recovery policy row required')
        output[code] = run_deadline_recovery_branch(scenario, inquiry=row['inquiry'],
            allocation=row['allocation'], cohort_registration=row['cohort_registration'],
            scarce_routing=row['scarce_routing'], late_reply_recovery=row['late_reply_recovery'],
            routing=routing, branch_id=code, label=row.get('label', code))
    return dict(schema_version=1, experiment_id=EXPERIMENT_ID, scenario=deepcopy(scenario),
                branches=[output[code] for code in ids], execution_order=order)
