"""Compose actual server inquiry selectors with current-information H1/C1.

Clock values remain real minutes. A 15-second decision grid preserves original
minute-rounded movement, treatment, observation and three-minute transfer rules.
The server pure selectors are reused; this is not the server workflow/DB replay.
All new hooks remain isolated from previously locked experiment sources.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time

from server import inquiry_policies
from .h1_online_engine import _policy_spec
from .heuristic_assignment import HeuristicAssignmentRule
from .inquiry_queue import InquiryQueue
from .map_replay import RouteSnapshot, validate_scenario
from .opportunity_guard import GRACE_MINUTES
from .outage_recovery_engine import OutageRecoveryReplay
from .partial_information_engine import PartialInformationReplay
from .responsive_confirmation_engine import incomplete_confirmation
from .sim import Policy, Simulation
from .stable_cp_sat_assignment import StableCPSATAssignmentRule

EXPERIMENT_ID = 'joint_policy_20261016'
CLOCK_ORIGIN_SECONDS = 86400
SELECTOR_NAMES = {'v2': 'observed_v2', 'v3': 'observed_v3'}

def _quarter(value, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and value >= (0.25 if positive else 0) and value * 4 == int(value * 4))


class QuarterClockSimulation(Simulation):
    """Frozen run ordering, with quarter-minute observation/decision steps."""
    def run(self):
        end = self.cfg['horizon'] + self.cfg['tail']
        walk = list(self.walkins)
        outages = list(self.outages)
        restores = []
        steps_per_minute = 1 if getattr(self, 'legacy_clock', False) else 4
        for step in range((end - 1) * steps_per_minute + 1):
            t = step // steps_per_minute if step % steps_per_minute == 0 else step / steps_per_minute
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
                self.send_asks(p, t)
            expand_after = getattr(self.policy, 'expand_after', None)
            patients = self.patients.values()
            if priority_order == 'all':
                # Only already observed patients. Submitted FIFO jobs and
                # existing commitments are never reordered or preempted.
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


def decide_joint_protection(snapshot, waiting_since, held_since, *, grace_minutes=3):
    """Return patient deferrals using known fresh beds and lifetime deadlines.

    A ready patient with a higher priority or only one structurally compatible
    hospital cannot be deferred by this rule. Potential consumption is counted
    conservatively over current accepted options, not future replies. A hold
    synchronizes a later ordinary batch; it does not force that batch to select
    the waiting patient and is not a clinical priority override.
    """
    t = snapshot['t']
    if type(grace_minutes) is not int or grace_minutes < 1:
        raise ValueError('positive integer grace_minutes required')
    if not _quarter(t):
        raise ValueError('nonnegative integer snapshot time required')
    for clocks in (waiting_since, held_since):
        if not isinstance(clocks, dict) or any(not isinstance(pid, str)
                or not _quarter(start) or start > t
                for pid, start in clocks.items()):
            raise ValueError('lifetime clocks must be observed times')
    ready = snapshot['ready']
    held, holds, reservations, budgets = set(), [], [], {}
    next_held = deepcopy(held_since)
    waiters = sorted(snapshot['waiting'], key=lambda p: (-p['priority'], p['request_t'], p['id']))
    for waiter in waiters:
        pid = waiter['id']
        first = waiting_since.get(pid)
        compatible = waiter['compatible_hospitals']
        demand = waiter['demand'].get('ed_bed', 0)
        if first is None or t >= first + grace_minutes or len(compatible) != 1 or demand <= 0:
            continue
        hid = compatible[0]
        offer = next((o for o in waiter['offers'] if o['hospital'] == hid
                      and o['expires_at'] > t + 1 and o['reason'] in ('UNKNOWN', 'STALE')), None)
        if offer is None:
            continue
        bed = offer['resources'].get('ed_bed')
        if not bed or bed['status'] != 'fresh' or bed['free'] is None or bed['free'] < demand:
            continue
        free = bed['free']
        contenders = [p for p in ready if p['id'] not in held and hid in p['options']]
        total = sum(p['demand'].get('ed_bed', 0) for p in contenders)
        reserved = budgets.get(hid, 0)
        victims = [p for p in contenders if len(p['compatible_hospitals']) > 1
                   and p['priority'] <= waiter['priority']
                   and p['demand'].get('ed_bed', 0) > 0
                   and t < next_held.get(p['id'], t) + grace_minutes
                   and max(p['valid_offer_expiries'][h] for h in p['options']) > t + 1]
        # Least urgent first; latest request and largest ID break equal ties.
        victims.sort(key=lambda p: p['id'], reverse=True)
        victims.sort(key=lambda p: p['request_t'], reverse=True)
        victims.sort(key=lambda p: p['priority'])
        chosen = []
        remaining = total
        for patient in victims:
            if remaining + reserved + demand <= free:
                break
            chosen.append(patient)
            remaining -= patient['demand'].get('ed_bed', 0)
        if remaining + reserved + demand > free:
            continue
        budgets[hid] = reserved + demand
        reservations.append(dict(waiting_patient=pid, hospital=hid, resource='ed_bed',
            quantity=demand, priority=waiter['priority'], known_free=free))
        for patient in chosen:
            target = patient['id']
            next_held.setdefault(target, t)
            held.add(target)
            holds.append(dict(patient=target, waiting_patient=pid, hospital=hid, resource='ed_bed',
                known_free=free, demand=patient['demand']['ed_bed'], waiting_since=first,
                waiting_deadline=first + grace_minutes, held_since=next_held[target],
                held_deadline=next_held[target] + grace_minutes,
                deadline=min(first + grace_minutes, next_held[target] + grace_minutes,
                             offer['expires_at'] - 1,
                             max(patient['valid_offer_expiries'][h] for h in patient['options']) - 1)))
    return dict(held=sorted(held), ready_after=[p['id'] for p in ready if p['id'] not in held],
                holds=holds, reservations=reservations, held_since_after=next_held)


def decide_pending_opportunities(snapshot):
    """Briefly defer flexible ready patients for already submitted scarce asks.

    This does not reserve capacity or put an unaccepted patient into a model.
    The only clocks are the original request metadata and a ready patient's
    lifetime first hold. Neither clock is renewed by a later evaluation.
    """
    t=snapshot['t']
    if not _quarter(t):raise ValueError('quarter-minute snapshot time required')
    first=deepcopy(snapshot['held_since'])
    if any(not isinstance(pid,str) or not _quarter(at) or at>t for pid,at in first.items()):
        raise ValueError('hold clocks must be observed times')
    ready=snapshot['ready'];held=set();holds=[];budgets={}
    def fresh_shortage(views,demand):
        return any(view['status']=='fresh' and view['free'] is not None
                   and view['free']<demand[resource] for resource,view in views.items())
    waiting=sorted(snapshot['waiting'],key=lambda p:(-p['priority'],p['request_t'],p['id']))
    for waiter in waiting:
        compatible=waiter['compatible_hospitals'];demand=waiter['demand'].get('ed_bed',0)
        if len(compatible)!=1 or demand<=0:continue
        hid=compatible[0]
        asks=[a for a in waiter['pending_inquiries'] if a['hospital']==hid]
        if len(asks)!=1:continue
        ask=asks[0];submitted=ask['submitted_at'];timeout=ask['timeout_at']
        if not _quarter(submitted) or not _quarter(timeout) or submitted>t or timeout<submitted \
                or type(ask['ask_event_sequence']) is not int or ask['ask_event_sequence']<0:
            raise ValueError('pending handle must contain original submitted metadata')
        pending_deadline=min(submitted+1,timeout)
        if t>=pending_deadline:continue
        bed=waiter['bed_view']
        if bed['status']!='fresh' or bed['free'] is None or bed['free']<demand:continue
        if fresh_shortage(waiter['resource_views'],waiter['demand']):continue
        free=bed['free'];reserved=budgets.get(hid,0)
        contenders=[p for p in ready if p['id'] not in held and hid in p['options']]
        remaining=sum(p['demand'].get('ed_bed',0) for p in contenders)
        victims=[p for p in contenders if len(p['compatible_hospitals'])>=2
            and p['priority']<=waiter['priority'] and p['demand'].get('ed_bed',0)>0
            and t<first.get(p['id'],t)+1 and p['valid_offer_expiries'][hid]>t+.25
            and any(other!=hid for other in p['eligible_alternative_hospitals'])]
        victims.sort(key=lambda p:p['id'],reverse=True)
        victims.sort(key=lambda p:p['request_t'],reverse=True)
        victims.sort(key=lambda p:p['priority'])
        selected=[]
        for patient in victims:
            if remaining+reserved+demand<=free:break
            selected.append(patient);remaining-=patient['demand'].get('ed_bed',0)
        if remaining+reserved+demand>free:continue
        budgets[hid]=reserved+demand
        for patient in selected:
            pid=patient['id'];first.setdefault(pid,t);held.add(pid)
            holds.append(dict(patient=pid,waiting_patient=waiter['id'],hospital=hid,
                ask_event_sequence=ask['ask_event_sequence'],attempt=ask['attempt'],
                submitted_at=submitted,timeout_at=timeout,held_since=first[pid],
                deadline=min(pending_deadline,first[pid]+1),known_free=free,
                waiting_demand=demand,patient_priority=patient['priority'],
                waiting_priority=waiter['priority']))
    return dict(held=sorted(held),holds=holds,
        ready_after=[p['id'] for p in ready if p['id'] not in held],held_since_after=first)


class QuarterInquiryQueue(InquiryQueue):
    """Same FCFS queue, accepting exact quarter-minute submission times."""

    def enqueue(self, hospital, t, patient, attempt, service_minutes,
                kind='inquiry', silent=False):
        if hospital not in self._available:
            raise ValueError('unknown hospital')
        if not _quarter(t) or not _quarter(service_minutes, positive=True):
            raise ValueError('queue timing must be nonnegative quarter minutes')
        if t < self._last_submission[hospital] or type(silent) is not bool:
            raise ValueError('invalid queue ordering or silence')
        available = self._available[hospital]
        worker = min(range(self.workers), key=lambda index: (available[index], index))
        start = max(t, available[worker])
        finish = start + service_minutes
        job = dict(hospital=hospital, patient=patient, attempt=attempt,
                   submitted_t=t, start_t=start, finish_t=finish,
                   service_minutes=service_minutes, queue_wait_minutes=start-t,
                   kind=kind, silent=silent)
        available[worker] = finish
        self._last_submission[hospital] = t
        self.jobs.append(job)
        self._jobs_by_hospital[hospital].append(job)
        return dict(job)

    def snapshot(self, t):
        if not _quarter(t):
            raise ValueError('snapshot time must be quarter minutes')
        result = []
        for hospital in self.hospital_ids:
            jobs = [job for job in self._jobs_by_hospital[hospital] if job['submitted_t'] <= t]
            row = dict(hospital=hospital, workers=self.workers, waiting=0, processing=0,
                       completed=0, submitted=len(jobs), queue_wait_total_minutes=0,
                       service_total_minutes=0)
            for job in jobs:
                row['completed' if job['finish_t'] <= t else
                    'processing' if job['start_t'] <= t else 'waiting'] += 1
                row['queue_wait_total_minutes'] += max(0, min(t, job['start_t'])-job['submitted_t'])
                row['service_total_minutes'] += max(0, min(t, job['finish_t'])-job['start_t'])
            result.append(row)
        return result


def validate_joint_scenario(scenario):
    """Keep old fields intact and validate only the new fractional reply source."""
    validate_scenario(scenario)
    hospitals = {h['id'] for h in scenario['hospitals']}
    matrix = scenario.get('response_matrix', {})
    if not isinstance(matrix, dict):
        raise ValueError('response_matrix must be a mapping')
    for rows in matrix.values():
        if not isinstance(rows, dict) or not set(rows) <= hospitals:
            raise ValueError('invalid response_matrix hospitals')
        for row in rows.values():
            if not isinstance(row, dict):
                raise ValueError('response entry must be a mapping')
            entries = row.get('attempts', [row])
            if not isinstance(entries, list) or not entries:
                raise ValueError('nonempty response attempts required')
            for entry in entries:
                if not isinstance(entry, dict) or not _quarter(entry.get('delay', 2), positive=True) \
                        or type(entry.get('silent', False)) is not bool:
                    raise ValueError('reply delay must be positive quarter minutes')
    for row in scenario.get('initial_inquiry_history', []):
        if not isinstance(row, dict) or row.get('hospital') not in hospitals \
                or row.get('outcome') not in ('reply', 'accept', 'decline', 'timeout'):
            raise ValueError('invalid initial inquiry history')
        submitted, observed = row.get('submitted_at'), row.get('observed_at')
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (submitted, observed)) \
                or not -CLOCK_ORIGIN_SECONDS <= submitted <= observed <= 0:
            raise ValueError('history must be observed before replay origin')


class JointPolicyReplay(OutageRecoveryReplay, QuarterClockSimulation):
    """Generalize the isolated C1 interventions to either current joint rule.

    Explicit state initialization avoids pretending H1 is the C1-only branch
    required by the old experimental constructors. Actual inherited observation,
    recovery, movement and final-gate methods remain the same.
    """

    def __init__(self, scenario, policy, routing=None, *, inquiry='v3', recovery=True,
                 inquiry_decider=None, legacy_clock=False,pending_protection=True):
        if inquiry not in ('fixed_3', 'v2', 'v3', 'v4') or type(recovery) is not bool:
            raise ValueError('unsupported inquiry selector or recovery flag')
        if policy.rule.code not in ('H1', 'C1'):
            raise ValueError('joint H1/C1 required')
        if type(legacy_clock) is not bool or (legacy_clock and inquiry!='fixed_3'):
            raise ValueError('legacy clock is only a fixed inquiry calibration')
        if type(pending_protection) is not bool:
            raise ValueError('pending_protection must be an explicit boolean')
        self.legacy_clock=legacy_clock
        self.pending_protection_enabled=pending_protection
        self.pending_guard_records=[]
        self._pending_held_since={}
        self.inquiry_name = inquiry
        self.inquiry_decider = inquiry_decider
        self.inquiry_decisions = []
        self._next_policy_review = {}
        self._last_contact_t = {}
        self._episode_contacts = {}
        self._episode_ids = {}
        self.initial_inquiry_history = deepcopy(scenario.get('initial_inquiry_history', []))
        self.retained_backups_enabled = self.transfer_same_tick_enabled = self.recovery_followup_enabled = recovery
        self._early_ready_refs, self._accept_refs, self._episodes = {}, {}, {}
        self._gate_events, self._terminal_refs = {}, {}
        self.backup_seconds = 0.0
        self.early_offers_enabled = True
        self.early_offer_records = []
        self.early_offer_seconds = 0.0
        self.resource_updates_enabled = False
        self.event_review_enabled = True
        self.update_config = deepcopy(scenario.get('resource_update_config', {}))
        self._publication_sequence = 0
        self._publications = []
        self._registered, self._reviewed_packets, self._review_phases = set(), set(), set()
        self.update_seconds = self.event_review_seconds = 0.0
        self.stable_ties_enabled = policy.rule.code == 'C1'
        self.prefetch_enabled = self.watch_enabled = True
        self.grace_minutes = GRACE_MINUTES
        self.watched_hospitals, self._woken_reports = set(), set()
        self.confirmation_intervention_seconds = 0.0
        self._watch_timing_active = False
        self.protection_enabled = True
        self.waiting_since, self.held_since = {}, {}
        self.protection_records = []
        self.protection_seconds = 0.0
        PartialInformationReplay.__init__(self, scenario, policy, routing=routing, mode='observed')
        self._previous_physical = {hid:self._physical_report(hid) for hid in self.hospitals}
        if self.inquiry_queue is not None:
            self.inquiry_queue = QuarterInquiryQueue(list(self.hospitals), self.inquiry_queue.workers)

    def _feedback_enabled(self):
        return True

    def _response(self, p, hid, attempt):
        if 'response_matrix' not in self.scenario:
            return super()._response(p, hid, attempt)
        matrix = self.scenario['response_matrix']
        entry = matrix.get(p.id, {}).get(hid, matrix.get('*', {}).get(hid))
        if entry is None:
            return super()._response(p, hid, attempt)
        entries = entry.get('attempts')
        if entries:
            entry = entries[min(attempt-1, len(entries)-1)]
        return entry.get('silent', False), entry.get('delay', 2)

    def schedule_ask(self, p, hid, attempt, t):
        ask = super().schedule_ask(p, hid, attempt, t)
        # Public handle metadata also exists when backup recovery is disabled.
        ask.update(submitted_t=t, attempt=attempt, submitted_at=t,
                   timeout_at=t+self.cfg['timeout'], ask_event_sequence=None)
        return ask

    def ev(self, t, typ, **fields):
        pid = fields.get('patient')
        if typ == 'transfer_search':
            self._episode_contacts[pid] = 0
            self._episode_ids[pid] = self._episode_ids.get(pid, 0)+1
            self._next_policy_review.pop(pid, None)
        super().ev(t, typ, **fields)
        if typ == 'ask':
            self._last_contact_t[pid] = t
            self._episode_contacts[pid] = self._episode_contacts.get(pid, 0)+1
            self._episode_ids.setdefault(pid, 0)
            self.log[-1].update(submitted_at=t, timeout_at=t+self.cfg['timeout'],
                                inquiry_episode=self._episode_ids[pid])
            for ask in self.patients[pid].asks:
                if ask['hid'] == fields['hospital'] and ask['attempt'] == fields['attempt']:
                    ask['ask_event_sequence'] = self.log[-1]['event_sequence']

    def send_asks(self, p, t, exclude=()):
        if self.inquiry_name != 'fixed_3':
            if t < self._next_policy_review.get(p.id, 0):
                return
            if not p.tried and self._episode_contacts.get(p.id, 0):
                self._episode_contacts[p.id] = 0
                self._episode_ids[p.id] = self._episode_ids.get(p.id, 0)+1
        return super().send_asks(p, t, exclude=exclude)

    def observed_inquiry_plan(self, p, candidates, loc, t):
        def seconds(value):
            return CLOCK_ORIGIN_SECONDS + int(value*60)
        pending = [dict(submitted_at=seconds(e['submitted_t']), hospital=e['hospital'], patient=e['patient'])
                   for e in self._inquiry_feedback_pending if e['submitted_t'] <= t]
        own = [dict(submitted_at=seconds(a['submitted_t']), hospital=a['hid'], patient=p.id,
                    travel_seconds=self.travel(p, a['hid'], loc)*60) for a in p.asks]
        history = [dict(hospital=e['hospital'], patient=e.get('patient'),
                        submitted_at=CLOCK_ORIGIN_SECONDS+e['submitted_at'],
                        observed_at=CLOCK_ORIGIN_SECONDS+e['observed_at'], outcome=e['outcome'])
                   for e in self.initial_inquiry_history]
        history.extend(dict(hospital=e['hospital'], patient=e['patient'],
                            submitted_at=seconds(e['submitted_t']), observed_at=seconds(e['observed_t']),
                            outcome=e['outcome']) for e in self.inquiry_observations)
        inputs = dict(now=seconds(t), candidates=[dict(hospital_id=hid,
            travel=dict(duration_seconds=self.travel(p,hid,loc)*60)) for hid in candidates],
            observations=history, pending=pending, patient_pending=own,
            max_new=max(0, min(self.policy.batch_size-len(p.asks),10-self._episode_contacts.get(p.id,0))),
            last_contact_at=seconds(self._last_contact_t[p.id]) if p.id in self._last_contact_t else None)
        if self.inquiry_name == 'v4':
            if self.inquiry_decider is None:
                from server.inquiry_v4 import decide
            else:
                decide = self.inquiry_decider
            decision = decide(**deepcopy(inputs))
        else:
            decision = inquiry_policies.decide(SELECTOR_NAMES[self.inquiry_name], **deepcopy(inputs))
        send = decision['send']
        if len(send) != len(set(send)) or not set(send) <= set(candidates) or len(send) > inputs['max_new']:
            raise ValueError('selector exceeded offered candidates or concurrency/contact budget')
        next_at = decision['next_review_at']
        if type(next_at) is not int or next_at < inputs['now']+15 or (next_at-CLOCK_ORIGIN_SECONDS)%15:
            raise ValueError('selector next review must follow the quarter-minute clock')
        self._next_policy_review[p.id] = (next_at-CLOCK_ORIGIN_SECONDS)/60
        self.ev(t,'joint_inquiry_policy',patient=p.id,record_index=len(self.inquiry_decisions))
        record = dict(t=t, patient=p.id, inquiry=self.inquiry_name, inputs=deepcopy(inputs),
                      decision=deepcopy(decision), event_sequence=self.log[-1]['event_sequence'],
                      inquiry_episode=self._episode_ids.get(p.id,0),
                      ask_event_sequences=[a['ask_event_sequence'] for a in p.asks],
                      resource_check_sequences=[e['event_sequence'] for e in self.log
                        if e['t']==t and e['type']=='information_check' and e.get('patient')==p.id
                        and e.get('purpose')=='inquiry'])
        self.inquiry_decisions.append(record)
        self.observed_decisions.append(dict(t=t,patient=p.id,limit=len(p.asks)+len(send),
                                            open_requests=len(p.asks),**deepcopy(decision)))
        if inputs['max_new']==0 and not p.asks and not p.valid:
            p.tried.clear()
            p.retry_at=t+self.cfg['retry_wait']
        return list(send)

    def _pending_patient_view(self,p):
        row=self._patient_view(p)
        row['compatible_hospitals']=sorted(hid for hid,h in self.hospitals.items()
            if p.caps<=h.caps and all(self.structural_capacities[hid].get(r,0)>=q
                                      for r,q in p.demand.items()))
        return row

    def _pending_snapshot(self,t):
        ready=[dict(self._pending_patient_view(p),options=list(options),
                    valid_offer_expiries={hid:p.valid[hid] for hid in options})
               for p,options in self.ready]
        ready_ids={p['id'] for p in ready};waiting=[]
        for row in ready:
            patient=self.patients[row['id']]
            row['alternative_resource_views']={hid:deepcopy(self._views(hid,patient.demand,t))
                                               for hid in row['compatible_hospitals']}
            row['eligible_alternative_hospitals']=[hid for hid,views in row['alternative_resource_views'].items()
                if not any(view['status']=='fresh' and view['free'] is not None
                           and view['free']<patient.demand[resource] for resource,view in views.items())]
        for pid in sorted(self._registered):
            p=self.patients[pid]
            if p.state!='SEARCH' or pid in ready_ids or not p.asks:continue
            row=self._pending_patient_view(p)
            if len(row['compatible_hospitals'])!=1:continue
            hid=row['compatible_hospitals'][0]
            handles=[dict(hospital=a['hid'],attempt=a['attempt'],submitted_at=a['submitted_at'],
                          timeout_at=a['timeout_at'],ask_event_sequence=a['ask_event_sequence'])
                     for a in p.asks if a['hid']==hid]
            if not handles:continue
            views=deepcopy(self._views(hid,p.demand,t))
            row.update(pending_inquiries=handles,resource_views=views,bed_view=deepcopy(views['ed_bed']))
            waiting.append(row)
        return dict(t=t,ready=ready,waiting=waiting,held_since=deepcopy(self._pending_held_since))

    def _pending_guard(self,t):
        if not self.pending_protection_enabled or not self.ready:return
        snapshot=self._pending_snapshot(t);result=decide_pending_opportunities(snapshot)
        self._pending_held_since=deepcopy(result['held_since_after'])
        self.ev(t,'pending_opportunity_guard',record_index=len(self.pending_guard_records))
        self.pending_guard_records.append(dict(snapshot=snapshot,result=deepcopy(result),
                                               event_sequence=self.log[-1]['event_sequence']))
        for hold in result['holds']:self.ev(t,'pending_opportunity_hold',**hold)
        keep=set(result['ready_after']);removed=[p for p,_ in self.ready if p.id not in keep]
        self.ready=[(p,options) for p,options in self.ready if p.id in keep]
        # Ordinary selector and candidate/quantity checks choose any new ask.
        # No forced alternative edge or synthetic acceptance is introduced.
        for patient in removed:self.send_asks(patient,t)

    def resolve_batch(self, t):
        # Reuse early readiness, while replacing only the integer-only guard.
        # ResponsiveConfirmationReplay.resolve_batch invokes the guard by name;
        # reproduce that small stage here and call the unchanged quantitative gate.
        if self.early_offers_enabled:
            from .existing_info_engine import decide_early_offer
            started=time.perf_counter();self.ev(t,'early_offer_cycle')
            ready_ids={p.id for p,_ in self.ready}
            for p in sorted((self.patients[pid] for pid in self._registered),key=lambda p:(-p.priority,p.t_req,p.id)):
                if p.state!='SEARCH' or p.id in ready_ids or not p.asks or not p.valid \
                        or p.first_valid_t is None or t-p.first_valid_t>=self.policy.wait:
                    continue
                snapshot=self._early_snapshot(p,t);decision_started=time.perf_counter()
                result=decide_early_offer(snapshot);elapsed=time.perf_counter()-decision_started
                self.ev(t,'early_offer_review',record_index=len(self.early_offer_records))
                self.early_offer_records.append(dict(snapshot=snapshot,result=result,
                    event_sequence=self.log[-1]['event_sequence'],elapsed_seconds=elapsed))
                if not result['eligible']:continue
                self.ev(t,'early_offer_ready',patient=p.id,hospital=result['hospital'],
                        review_event_sequence=self.early_offer_records[-1]['event_sequence'])
                options=[]
                from .responsive_confirmation_engine import information_reason
                for hid in p.valid:
                    if information_reason(self._views(hid,p.demand,t),p.demand) is None:
                        eligible,reason,_=self._check(p,hid,t,'allocation')
                        if not eligible or reason is not None:raise AssertionError('early view changed')
                        options.append(hid)
                self.ready.append((p,options));ready_ids.add(p.id)
            self.early_offer_seconds+=time.perf_counter()-started
        if not self.ready:return
        started=time.perf_counter();snapshot=self._protection_snapshot(t)
        first,held=deepcopy(self.waiting_since),deepcopy(self.held_since)
        filtered=deepcopy(snapshot)
        for waiter in filtered['waiting']:
            waiter['offers']=[offer for offer in waiter['offers']
                if not incomplete_confirmation(offer,first.get(waiter['id'],t))]
        result=decide_joint_protection(filtered,first,held,grace_minutes=self.grace_minutes)
        self.held_since=deepcopy(result['held_since_after'])
        self.ev(t,'opportunity_guard',record_index=len(self.protection_records))
        self.protection_records.append(dict(snapshot=snapshot,waiting_since=first,held_since=held,
            result=deepcopy(result),grace_minutes=self.grace_minutes,event_sequence=self.log[-1]['event_sequence']))
        for hold in result['holds']:self.ev(t,'opportunity_hold',**hold)
        keep=set(result['ready_after']);self.ready=[(p,options) for p,options in self.ready if p.id in keep]
        self.protection_seconds+=time.perf_counter()-started
        self._pending_guard(t)
        PartialInformationReplay.resolve_batch(self,t)

    def after_tick(self, t):
        previous_risk=list(self.risk_patients)
        frames_before=len(self.risk_frames)
        super().after_tick(t)
        for pid in previous_risk:
            self.invalid_transit_minutes[pid]-=0 if self.legacy_clock else .75
        if len(self.risk_frames)>frames_before:
            self.risk_frames[-1]['invalid_transit_minutes']=sum(self.invalid_transit_minutes.values())
        # The after-treatment review can submit work after MapReplay's first
        # logging pass. Publish only events that have already started at t;
        # their physical FIFO calendar and later replies are unchanged.
        for event in self._review_events.pop(t,[]):
            kind=event.pop('type')
            self.ev(t,kind,**event)
        if self.inquiry_queue is not None:
            frame=self.inquiry_queue.snapshot(t)
            if frame!=self._previous_inquiry_frame:
                if self.inquiry_frames and self.inquiry_frames[-1]['t']==t:
                    self.inquiry_frames[-1]['hospitals']=frame
                    if len(self.inquiry_frames)>1 and self.inquiry_frames[-2]['hospitals']==frame:
                        self.inquiry_frames.pop()
                else:
                    self.inquiry_frames.append(dict(t=t,hospitals=frame))
                self._previous_inquiry_frame=frame

    def run_replay(self):
        branch=super().run_replay()
        # The frozen integer aggregator's `< end` meant `<= end - 1`.
        # On the fractional grid censor at exactly the same physical instant.
        censor=self.cfg['horizon']+self.cfg['tail']-1
        corrected=[]
        for row in branch['patient_outcomes']:
            completion=row['treatment_complete_t']
            if completion is not None and completion>censor:
                row['treatment_complete_t']=None;row['state']='INCOMPLETE'
                corrected.append(row)
        if corrected:
            branch['events']=[e for e in branch['events'] if e['t']<=censor]
            branch['events'].extend(dict(t=censor,type='observation_end',patient=row['id'],
                hospital=row['destination_hospital_id'],state='INCOMPLETE',reason='observation_limit')
                for row in corrected)
            branch['events'].sort(key=lambda e:e['t'])
            branch['metrics']['completed']=sum(p['state']=='COMPLETE' for p in branch['patient_outcomes'])
        branch.update(comparison_id=EXPERIMENT_ID,source_experiment='joint_policy_comparison',
                      time_step_seconds=60 if self.legacy_clock else 15,
                      legacy_clock_calibration=self.legacy_clock,time_origin_seconds=CLOCK_ORIGIN_SECONDS,
                      inquiry_decisions=deepcopy(self.inquiry_decisions),inquiry_selector=self.inquiry_name,
                      initial_inquiry_history=deepcopy(self.initial_inquiry_history),
                      ordered_emissions=deepcopy(self.log),pending_protection_enabled=self.pending_protection_enabled,
                      pending_guard_records=deepcopy(self.pending_guard_records))
        branch['metrics'].update(inquiry_selector_reviews=len(self.inquiry_decisions),
                                inquiry_selector_sent=sum(len(r['decision']['send']) for r in self.inquiry_decisions),
                                pending_guard_reviews=len(self.pending_guard_records),
                                pending_guard_holds=sum(len(r['result']['holds']) for r in self.pending_guard_records))
        return branch


def run_joint_policy_branch(scenario, *, inquiry='v3', allocation='H1', recovery=True,
                            routing=None, inquiry_decider=None, branch_id=None, label=None,
                            legacy_clock=False,pending_protection=True):
    validate_joint_scenario(scenario)
    if allocation not in ('H1','C1'):
        raise ValueError('allocation must be H1 or C1')
    code=branch_id or f'{inquiry}_{allocation}'+('' if recovery else '_no_backup')
    rule=HeuristicAssignmentRule(time_limit_seconds=.05,max_moves=2048) if allocation=='H1' else \
        StableCPSATAssignmentRule(time_limit_seconds=.05,seed=0,require_backend=True)
    policy=Policy(rule=rule,k=3,wait=2,notify=True,name=code,
                  observed_inquiry={} if inquiry!='fixed_3' else None)
    simulation=JointPolicyReplay(deepcopy(scenario),policy,routing=routing,
                                  inquiry=inquiry,recovery=recovery,inquiry_decider=inquiry_decider,
                                  legacy_clock=legacy_clock,pending_protection=pending_protection)
    branch=simulation.run_replay()
    branch.update(id=code,label=label or code,policy=_policy_spec(policy))
    selector_spec=None
    if inquiry in SELECTOR_NAMES:selector_spec=inquiry_policies.spec(SELECTOR_NAMES[inquiry])
    elif inquiry=='v4':
        from server.inquiry_v4 import spec
        selector_spec=spec()
    branch['joint_policy_spec']=dict(inquiry=inquiry,allocation=allocation,recovery=recovery,
        inquiry_policy=selector_spec,selector_only=inquiry!='fixed_3',server_workflow_reproduced=False,
        global_300_second_deadline_applied=False,episode_contact_limit=10 if inquiry!='fixed_3' else None,
        common_max_concurrent=3,common_acceptance_wait_minutes=2,original_movement_rounding_minutes=1,
        no_backup_disables='retained_backups,transfer_same_tick,recovery_followup',
        early_offers=True,event_review=True,protection=True,prefetch=True,watch=True,
        extra_resource_change_publications=False,pending_protection=pending_protection,
        pending_protection_max_minutes=1,pending_protection_offer_lookahead_minutes=.25)
    return branch


DEFAULT_JOINT_POLICIES=tuple(dict(id=code,label=code,inquiry=inquiry,allocation=allocation,recovery=recovery,
                                  pending_protection=pending)
    for inquiry,allocation,recovery,code,pending in (
        ('fixed_3','H1',False,'fixed3_H1_no_backup',True),('fixed_3','C1',False,'fixed3_C1_no_backup',True),
        ('fixed_3','H1',True,'fixed3_H1',True),('fixed_3','C1',True,'fixed3_C1',True),
        ('v3','H1',True,'v3_H1',True),('v3','C1',True,'v3_C1',True),
        ('v4','H1',True,'v4_H1',True),('v4','C1',True,'v4_C1',True),
        ('v3','H1',True,'v3_H1_no_pending_guard',False),('v3','C1',True,'v3_C1_no_pending_guard',False)))


def run_joint_policy_replay(scenario, policies=None, execution_order=None, routing=None,
                             inquiry_decider=None):
    validate_joint_scenario(scenario)
    specs=deepcopy(list(DEFAULT_JOINT_POLICIES if policies is None else policies))
    ids=[s['id'] for s in specs]
    if not ids or len(ids)!=len(set(ids)):
        raise ValueError('unique nonempty policy IDs required')
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):
        raise ValueError('each branch must execute exactly once')
    routing=routing or RouteSnapshot();routing.used_keys.clear();by_id={}
    for code in order:
        row=next(s for s in specs if s['id']==code)
        by_id[code]=run_joint_policy_branch(scenario,inquiry=row['inquiry'],allocation=row['allocation'],
            recovery=row['recovery'],routing=routing,inquiry_decider=inquiry_decider,
            branch_id=code,label=row.get('label',code),pending_protection=row.get('pending_protection',True))
    return dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=deepcopy(scenario),
                branches=[by_id[code] for code in ids],execution_order=order,
                routing_summary=routing.summary(scenario_only=True))

