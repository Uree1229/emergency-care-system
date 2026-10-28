"""Bounded synchronization of reported bed opportunities, never a reservation.

This policy consumes a detached present-information snapshot only. Unknown
resource quantities are not filled in, and an unconfirmed patient never enters
the quantitative allocation model. The narrow rule protects a patient with
exactly one structurally compatible hospital for at most three minutes while
an explicit resource confirmation is pending.
"""
from __future__ import annotations

from copy import deepcopy


GRACE_MINUTES = 3


def decide_protection(snapshot, waiting_since, held_since, *, grace_minutes=GRACE_MINUTES):
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
    if type(t) is not int or t < 0:
        raise ValueError('nonnegative integer snapshot time required')
    for clocks in (waiting_since, held_since):
        if not isinstance(clocks, dict) or any(not isinstance(pid, str)
                or type(start) is not int or start < 0 or start > t
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
