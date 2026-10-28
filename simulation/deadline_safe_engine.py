"""Ignore an optional late offer while any real inquiry remains unresolved.

The prior late-reply experiment is immutable. This extension retains its actual
receipt, timeout and resource checks, with one conservative current-information
guard. Ignoring a callback does not cancel, delay or create hospital work.
"""
from __future__ import annotations

from copy import deepcopy
import time

from . import deadline_recovery_engine as v1


EXPERIMENT_ID = 'deadline_safe_20261016'
DEADLINE_SAFE_SPEC = dict(late_pending_guard=True,
    pending_input='original_public_inquiry_handles',
    pending_action='ignore_optional_late_reply', blocking_wait_added=False,
    original_timeout_unchanged=True)


def decide_safe_late_offer(snapshot):
    """Use actual current pending handles, never their hidden delivery times."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('patient'), dict) \
            or 'pending_inquiries' not in snapshot['patient']:
        raise ValueError('current original inquiry handles required')
    base = deepcopy(snapshot)
    pending = base['patient'].pop('pending_inquiries')
    if not isinstance(pending, list):
        raise ValueError('pending inquiries must be a list of public handles')
    identities = set()
    for ask in pending:
        if not isinstance(ask, dict) or set(ask) != {
                'hospital', 'attempt', 'submitted_at', 'timeout_at', 'ask_event_sequence'} \
                or not isinstance(ask['hospital'], str) or not ask['hospital'] \
                or type(ask['attempt']) is not int or ask['attempt'] < 1 \
                or not v1._quarter(ask['submitted_at']) or ask['submitted_at'] > base['t'] \
                or not v1._quarter(ask['timeout_at']) or ask['timeout_at'] < ask['submitted_at'] \
                or type(ask['ask_event_sequence']) is not int or ask['ask_event_sequence'] < 0:
            raise ValueError('original submitted public inquiry handle required')
        key = ask['hospital'], ask['attempt']
        if key in identities:
            raise ValueError('pending inquiry handles must be unique')
        identities.add(key)
    # Run the unchanged parser and prior checks too. The new guard is a rejection
    # only; no previous failure can become an acceptance through this extension.
    result = v1.decide_late_offer(base)
    if pending:
        return dict(eligible=False, hospital=None, expires_at=None,
                    protected_patients=[], reason='PENDING_INQUIRY')
    return result


class DeadlineSafeReplay(v1.DeadlineRecoveryReplay):
    def __init__(self, *args, late_pending_guard=True, **kwargs):
        if type(late_pending_guard) is not bool or not late_pending_guard:
            raise ValueError('the safe replay requires an explicit pending-inquiry guard')
        self.late_pending_guard_enabled = True
        super().__init__(*args, **kwargs)

    def _receive_late_offers(self, p, t):
        # This local hook adds only original public pending handles to the raw
        # V1 snapshot. It does not patch V1 functions or rewrite saved evidence.
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
                condition=condition, condition_fingerprint=v1.condition_fingerprint(condition),
                latest_attempt=p.attempts[late['hospital']], first_commit_t=p.t_commit,
                valid_offers=[dict(hospital=hid, expires_at=expiry,
                                  resources=deepcopy(self._views(hid,p.demand,t)))
                    for hid, expiry in sorted(p.valid.items())],
                registered_request_sequence=self._request_refs[p.id],
                pending_inquiries=self._pending_view(p.asks)),
                resources=deepcopy(self._views(late['hospital'], p.demand, t)),
                accept_ttl_minutes=self.cfg['accept_ttl'], registered_search_patients=observed)
            result = decide_safe_late_offer(snapshot)
            self.ev(t, 'late_offer_review', patient=p.id, hospital=late['hospital'],
                    record_index=len(self.late_offer_records))
            record = dict(snapshot=snapshot, result=deepcopy(result),
                          event_sequence=self.log[-1]['event_sequence'])
            self.late_offer_records.append(record)
            if result['eligible']:
                p.valid[late['hospital']] = result['expires_at']
                p.accepted_at[late['hospital']] = t
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

    def run_replay(self):
        branch = super().run_replay()
        branch.update(comparison_id=EXPERIMENT_ID, late_pending_guard_enabled=True,
                      deadline_safe_spec=deepcopy(DEADLINE_SAFE_SPEC))
        return branch


def run_deadline_safe_branch(scenario, *, inquiry='v4', allocation='H1',
        cohort_registration=True, scarce_routing=True, late_reply_recovery=False,
        late_pending_guard=False, routing=None, branch_id=None, label=None):
    v1.validate_joint_scenario(scenario)
    if type(late_pending_guard) is not bool or (late_pending_guard and not late_reply_recovery):
        raise ValueError('the safe guard requires actual late-reply recovery')
    code = branch_id or f'{inquiry}_{allocation}_' + (
        'safe' if late_pending_guard else 'late' if late_reply_recovery else
        'scarce' if scarce_routing else 'cohort' if cohort_registration else 'baseline')
    if not late_pending_guard:
        return v1.run_deadline_recovery_branch(scenario, inquiry=inquiry, allocation=allocation,
            cohort_registration=cohort_registration, scarce_routing=scarce_routing,
            late_reply_recovery=late_reply_recovery, routing=routing,
            branch_id=code, label=label or code)
    if inquiry not in ('v3', 'v4') or allocation not in ('H1', 'C1') \
            or any(type(flag) is not bool for flag in
                   (cohort_registration, scarce_routing, late_reply_recovery)) \
            or not cohort_registration or not scarce_routing or not late_reply_recovery:
        raise ValueError('explicit compatible inquiry and safe-recovery flags required')
    rule = v1.HeuristicAssignmentRule(time_limit_seconds=.05, max_moves=2048) if allocation == 'H1' else \
        v1.StableCPSATAssignmentRule(time_limit_seconds=.05, seed=0, require_backend=True)
    policy = v1.Policy(rule=rule, k=3, wait=2, notify=True, name=code, observed_inquiry={})
    replay = DeadlineSafeReplay(deepcopy(scenario), policy, routing=routing, inquiry=inquiry,
        recovery=True, pending_protection=True, cohort_registration=True, scarce_routing=True,
        late_reply_recovery=True, late_pending_guard=True)
    branch = replay.run_replay()
    branch.update(id=code, label=label or code, policy=v1._policy_spec(policy),
        scarce_delay_spec=dict(inquiry=inquiry, allocation=allocation,
            cohort_registration=True, scarce_routing=True,
            actual_action='parallel_scarce_supplement', original_selector_candidates_unchanged=True,
            maximum_supplements_per_patient=1, recovery=True, pending_protection=True,
            pending_window_minutes=1, time_step_seconds=15, actual_selector_reused=True,
            extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True,
            scope='one_initial_scarce_supplement', late_reply_window_minutes=v1.LATE_REPLY_WINDOW_MINUTES,
            original_timeout_unchanged=True, original_timeout_feedback_unchanged=True,
            blocking_wait_added=False, hospital_work_calendar_unchanged=True,
            patient_condition_fingerprint_required=True, new_offer_ttl_from_actual_receipt=True,
            actual_reply_outcome_required=True, current_fresh_resource_check_required=True,
            current_valid_offer_rejects=True, newer_same_hospital_attempt_rejects=True))
    return branch


DEFAULT_DEADLINE_SAFE_POLICIES = tuple(dict(id=f'v4_{allocation}_{stage}',
    label=f'v4_{allocation}_{stage}', inquiry='v4', allocation=allocation,
    cohort_registration=stage != 'baseline', scarce_routing=stage != 'baseline',
    late_reply_recovery=stage in ('late', 'safe'), late_pending_guard=stage == 'safe')
    for allocation in ('H1', 'C1') for stage in ('baseline', 'scarce', 'late', 'safe')) + tuple(
    dict(id=f'fixed3_{allocation}_baseline', label=f'fixed3_{allocation}_baseline',
         inquiry='fixed_3', allocation=allocation, cohort_registration=False,
         scarce_routing=False, late_reply_recovery=False, late_pending_guard=False)
    for allocation in ('H1', 'C1'))


def run_deadline_safe_replay(scenario, policies=None, execution_order=None, routing=None):
    v1.validate_joint_scenario(scenario)
    specs = deepcopy(list(DEFAULT_DEADLINE_SAFE_POLICIES if policies is None else policies))
    ids = [row['id'] for row in specs]
    if not ids or len(ids) != len(set(ids)) or any(not isinstance(code, str) or not code for code in ids):
        raise ValueError('unique nonempty policy IDs required')
    order = list(ids if execution_order is None else execution_order)
    if len(order) != len(ids) or set(order) != set(ids):
        raise ValueError('each policy must execute once')
    by_id, output = {row['id']: row for row in specs}, {}
    for code in order:
        row = by_id[code]
        keys = {'id', 'inquiry', 'allocation', 'cohort_registration', 'scarce_routing',
                'late_reply_recovery', 'late_pending_guard'}
        if not keys <= set(row) or set(row) - (keys | {'label'}):
            raise ValueError('exact deadline-safe policy row required')
        output[code] = run_deadline_safe_branch(scenario, inquiry=row['inquiry'],
            allocation=row['allocation'], cohort_registration=row['cohort_registration'],
            scarce_routing=row['scarce_routing'], late_reply_recovery=row['late_reply_recovery'],
            late_pending_guard=row['late_pending_guard'], routing=routing,
            branch_id=code, label=row.get('label', code))
    return dict(schema_version=1, experiment_id=EXPERIMENT_ID, scenario=deepcopy(scenario),
                branches=[output[code] for code in ids], execution_order=order)
