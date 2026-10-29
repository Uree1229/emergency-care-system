"""Frozen, wholly synthetic allocation inputs for paired policy comparisons.

The snapshot is taken at minute 10. All patients and acceptance replies have
already been observed then. ``travel_minutes`` is an available estimate, not a
future arrival observation. There are no response schedules or future resource
releases, so a policy cannot gain information from events after the snapshot.

The family list is fixed before comparative experiments. Development seeds
8101--8108 and validation seeds 9101--9120 are reserved by the experiment plan;
this module only generates inputs and never selects cases using policy results.
Names, coordinates, clinical labels and capacities are fictitious. Priorities
and resource units are experimental inputs, not a clinical classification.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import math
import random


RESOURCE_KEYS = ('ed_bed', 'special_team')
PRIORITIES = ('high', 'medium', 'low')
DEVELOPMENT_SEEDS = tuple(range(8101, 8109))
VALIDATION_SEEDS = tuple(range(9101, 9121))
FAMILIES = (
    'abundant',
    'first_reply_vs_arrival',
    'scarce_greedy_trap',
    'multi_resource',
    'priority_cardinality',
    'committed_queue',
    'no_offer_control',
    'large_batch',
)
FAMILY_DESCRIPTIONS = {
    'abundant': '자원이 충분하고 이동시간도 같은 대조군: 여러 배정이 동률일 수 있음.',
    'first_reply_vs_arrival': '첫 수용 응답과 짧은 예상 이동시간이 서로 다른 단일 환자.',
    'scarce_greedy_trap': '두 병원에 갈 수 있는 환자와 한 병원만 가능한 환자의 자원 경합.',
    'multi_resource': '응급병상과 전문팀을 동시에 소비하는 환자를 포함한 결합 제약.',
    'priority_cardinality': '전체 배정 수와 높은 우선순위군 배정 수가 충돌하는 설정.',
    'committed_queue': '기존 점유·확정·대기 수요를 반영하면 잔여 자원이 음수가 될 수 있음.',
    'no_offer_control': '수용 응답이 없거나 해당 병원의 자원이 0인 미배정 대조군.',
    'large_batch': '작은 묶음의 전수 열거 한도를 넘는 다수 환자 동시 결정 묶음.',
}


def _quantities(ed_bed=0, special_team=0):
    return {'ed_bed': ed_bed, 'special_team': special_team}


def _hospital(index, *, beds, teams=0, occupied=None, committed=None,
              queued=None, specialist=True):
    return {
        'id': f'FICT-H{index:02d}',
        'name': f'가상병원 {index}',
        'lat': round(35.15 + index * 0.009, 6),
        'lon': round(126.85 + index * 0.012, 6),
        'capabilities': ['general', 'specialist'] if specialist else ['general'],
        'capacity': _quantities(beds, teams),
        'occupied': _quantities(**(occupied or {})),
        'committed': _quantities(**(committed or {})),
        'queued': _quantities(**(queued or {})),
    }


def _patient(index, *, offers, travel, replies=None, requested=0,
             priority='medium', beds=1, teams=0):
    hospital_ids = [f'FICT-H{i:02d}' for i in offers]
    if replies is None:
        replies = {i: max(requested, 2) for i in offers}
    return {
        'id': f'FICT-P{index:03d}',
        't_req': requested,
        'lat': round(35.11 + index * 0.0013, 6),
        'lon': round(126.87 + index * 0.0007, 6),
        'priority': priority,
        'required_capabilities': ['general', 'specialist'] if teams else ['general'],
        'resource_demands': _quantities(beds, teams),
        'accepted_hospitals': hospital_ids,
        'accepted_at': {f'FICT-H{i:02d}': replies[i] for i in offers},
        'travel_minutes': {f'FICT-H{i:02d}': value for i, value in travel.items()},
    }


def _abundant(rng):
    count = rng.randint(3, 5)
    minutes = rng.randint(8, 14)
    hospitals = [_hospital(i, beds=count, teams=count) for i in range(1, 4)]
    patients = [_patient(i, offers=(1, 2, 3), travel={j: minutes for j in (1, 2, 3)},
                         priority=PRIORITIES[(i - 1) % 3])
                for i in range(1, count + 1)]
    return hospitals, patients


def _first_reply_vs_arrival(rng):
    hospitals = [_hospital(i, beds=1) for i in range(1, 4)]
    patients = [_patient(1, offers=(1, 2, 3), requested=1,
                         replies={1: 2, 2: 5, 3: 7},
                         travel={1: rng.randint(42, 49), 2: rng.randint(10, 14),
                                 3: rng.randint(20, 25)})]
    return hospitals, patients


def _scarce_greedy_trap(rng):
    hospitals = [_hospital(1, beds=1), _hospital(2, beds=1)]
    patients = [
        _patient(1, offers=(1, 2), replies={1: 1, 2: 3},
                 travel={1: rng.randint(5, 7), 2: rng.randint(11, 14)}),
        _patient(2, offers=(1,), requested=1, replies={1: 4},
                 travel={1: rng.randint(7, 10), 2: 12}),
    ]
    return hospitals, patients


def _multi_resource(rng):
    hospitals = [_hospital(1, beds=2, teams=1),
                 _hospital(2, beds=2, specialist=False),
                 _hospital(3, beds=1, teams=1)]
    patients = [
        _patient(1, offers=(1, 2), replies={1: 1, 2: 3},
                 travel={1: 5, 2: rng.randint(10, 13), 3: 11}),
        _patient(2, offers=(1, 3), requested=1, teams=1,
                 replies={1: 3, 3: 5}, travel={1: 7, 2: 9, 3: rng.randint(8, 12)}),
        _patient(3, offers=(1, 3), requested=2, teams=1, priority='high',
                 replies={1: 4, 3: 6}, travel={1: 6, 2: 8, 3: rng.randint(8, 12)}),
        _patient(4, offers=(2,), requested=3, replies={2: 5},
                 travel={1: 5, 2: rng.randint(7, 10), 3: 12}),
    ]
    return hospitals, patients


def _priority_cardinality(rng):
    hospitals = [_hospital(1, beds=2, teams=1)]
    patients = [
        _patient(1, offers=(1,), replies={1: 2}, priority='low',
                 travel={1: rng.randint(6, 9)}),
        _patient(2, offers=(1,), requested=1, replies={1: 3}, priority='low',
                 travel={1: rng.randint(6, 9)}),
        _patient(3, offers=(1,), requested=2, replies={1: 4}, priority='high',
                 beds=2, teams=1, travel={1: rng.randint(6, 9)}),
    ]
    return hospitals, patients


def _committed_queue(rng):
    hospitals = [
        _hospital(1, beds=2, teams=1, occupied={'ed_bed': 1},
                  committed={'ed_bed': 1}, queued={'ed_bed': 1}),
        _hospital(2, beds=4, teams=2, occupied={'ed_bed': 1},
                  committed={'ed_bed': 1, 'special_team': 1},
                  queued={'ed_bed': 1}),
        _hospital(3, beds=2, teams=1, occupied={'special_team': 1}),
    ]
    patients = [
        _patient(1, offers=(1, 2, 3), replies={1: 1, 2: 4, 3: 5},
                 travel={1: 4, 2: rng.randint(10, 13), 3: 15}),
        _patient(2, offers=(1, 2, 3), requested=1, replies={1: 2, 2: 5, 3: 6},
                 teams=1, priority='high', travel={1: 5, 2: rng.randint(10, 13), 3: 14}),
        _patient(3, offers=(2, 3), requested=2, replies={2: 6, 3: 7},
                 travel={1: 6, 2: rng.randint(10, 13), 3: 14}),
    ]
    return hospitals, patients


def _no_offer_control(rng):
    # H3 has capacity, but no hospital acceptance was obtained for it. A policy
    # must neither invent an offer nor allocate to zero-capacity H1 or H2.
    hospitals = [_hospital(1, beds=0), _hospital(2, beds=0), _hospital(3, beds=2)]
    patients = [
        _patient(1, offers=(1,), travel={1: 5, 2: 8, 3: rng.randint(10, 14)}),
        _patient(2, offers=(), travel={1: 6, 2: 9, 3: rng.randint(10, 14)}),
        _patient(3, offers=(2,), travel={1: 7, 2: 8, 3: rng.randint(10, 14)}),
    ]
    return hospitals, patients


def _large_batch(rng):
    count = rng.randint(14, 16)
    hospitals = [_hospital(i, beds=5, teams=2) for i in range(1, 5)]
    patients = []
    for i in range(1, count + 1):
        offers = sorted(rng.sample((1, 2, 3, 4), rng.randint(2, 4)))
        requested = rng.randint(0, 5)
        replies = {h: rng.randint(max(2, requested), 9) for h in offers}
        patients.append(_patient(
            i, offers=offers, requested=requested, replies=replies,
            teams=int(i % 4 == 0), priority=PRIORITIES[(i - 1) % 3],
            travel={h: rng.randint(7, 32) for h in range(1, 5)}))
    return hospitals, patients


_BUILDERS = {
    'abundant': _abundant,
    'first_reply_vs_arrival': _first_reply_vs_arrival,
    'scarce_greedy_trap': _scarce_greedy_trap,
    'multi_resource': _multi_resource,
    'priority_cardinality': _priority_cardinality,
    'committed_queue': _committed_queue,
    'no_offer_control': _no_offer_control,
    'large_batch': _large_batch,
}


def _nonnegative_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{name} must be a nonnegative integer')


def _time(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f'{name} must be a finite nonnegative number')


def _coordinates(row, name):
    for key, limit in (('lat', 90), ('lon', 180)):
        value = row.get(key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not -limit <= value <= limit):
            raise ValueError(f'{name}.{key} is invalid')


def _resource_map(row, name):
    if not isinstance(row, Mapping) or set(row) != set(RESOURCE_KEYS):
        raise ValueError(f'{name} must contain exactly {RESOURCE_KEYS}')
    for key, value in row.items():
        _nonnegative_integer(value, f'{name}.{key}')


def validate_allocation_snapshot(snapshot: Mapping) -> None:
    """Reject invalid IDs, resources or information from after ``at``.

    Acceptance is deliberately not interpreted as a reservation: already
    committed/queued demand may exhaust a hospital after an observed offer.
    Such offers remain input facts, while an allocator must check free capacity.
    """
    if not isinstance(snapshot, Mapping) or snapshot.get('synthetic') is not True:
        raise ValueError('snapshot must be explicitly synthetic')
    _nonnegative_integer(snapshot.get('seed'), 'seed')
    if snapshot.get('family') not in FAMILIES:
        raise ValueError('unknown family')
    if not isinstance(snapshot.get('id'), str) or not snapshot['id']:
        raise ValueError('snapshot id must be a nonempty string')
    at = snapshot.get('at')
    _time(at, 'at')
    hospitals = snapshot.get('hospitals')
    patients = snapshot.get('patients')
    if not isinstance(hospitals, list) or not hospitals:
        raise ValueError('hospitals must be a nonempty list')
    if not isinstance(patients, list) or not patients:
        raise ValueError('patients must be a nonempty list')
    hospital_by_id = {}
    for row in hospitals:
        hid = row.get('id')
        if not isinstance(hid, str) or not hid or hid in hospital_by_id:
            raise ValueError('hospital ids must be unique nonempty strings')
        hospital_by_id[hid] = row
        _coordinates(row, hid)
        capabilities = row.get('capabilities')
        if (not isinstance(capabilities, list)
                or any(not isinstance(c, str) or not c for c in capabilities)
                or len(capabilities) != len(set(capabilities))):
            raise ValueError(f'{hid}.capabilities must be unique strings')
        for field in ('capacity', 'occupied', 'committed', 'queued'):
            _resource_map(row.get(field), f'{hid}.{field}')
    patient_ids = set()
    for row in patients:
        pid = row.get('id')
        if not isinstance(pid, str) or not pid or pid in patient_ids:
            raise ValueError('patient ids must be unique nonempty strings')
        patient_ids.add(pid)
        _coordinates(row, pid)
        _time(row.get('t_req'), f'{pid}.t_req')
        if row['t_req'] > at:
            raise ValueError(f'{pid} requested after the snapshot')
        if row.get('priority') not in PRIORITIES:
            raise ValueError(f'{pid}.priority must be one of {PRIORITIES}')
        _resource_map(row.get('resource_demands'), f'{pid}.resource_demands')
        if sum(row['resource_demands'].values()) == 0:
            raise ValueError(f'{pid} must demand at least one resource unit')
        offers = row.get('accepted_hospitals')
        if (not isinstance(offers, list) or any(h not in hospital_by_id for h in offers)
                or len(offers) != len(set(offers))):
            raise ValueError(f'{pid} has invalid or duplicate accepted hospitals')
        accepted_at = row.get('accepted_at')
        if not isinstance(accepted_at, Mapping) or set(accepted_at) != set(offers):
            raise ValueError(f'{pid}.accepted_at must match accepted_hospitals')
        for hid, value in accepted_at.items():
            _time(value, f'{pid}.accepted_at.{hid}')
            if not row['t_req'] <= value <= at:
                raise ValueError(f'{pid} acceptance outside observed request interval')
        travel = row.get('travel_minutes')
        if not isinstance(travel, Mapping) or set(travel) != set(hospital_by_id):
            raise ValueError(f'{pid}.travel_minutes must cover exactly all hospitals')
        for hid, value in travel.items():
            _nonnegative_integer(value, f'{pid}.travel_minutes.{hid}')
        required = row.get('required_capabilities', [])
        if (not isinstance(required, list)
                or any(not isinstance(c, str) or not c for c in required)
                or len(required) != len(set(required))):
            raise ValueError(f'{pid}.required_capabilities must be unique strings')
        for hid in offers:
            if not set(required) <= set(hospital_by_id[hid]['capabilities']):
                raise ValueError(f'{pid} has an accepted offer without required capabilities')


def build_allocation_snapshots(seed: int,
                               families: Iterable[str] | None = None) -> list[dict]:
    """Generate independent reproducible snapshots, without running a policy.

    Subsets retain the caller's order. Each family has its own random stream,
    so requesting it alone yields exactly the same input as requesting all.
    """
    _nonnegative_integer(seed, 'seed')
    if isinstance(families, (str, bytes)):
        raise ValueError('families must be an iterable of family IDs, not a string')
    selected = FAMILIES if families is None else tuple(families)
    if any(family not in FAMILIES for family in selected):
        raise ValueError('unknown allocation family')
    if len(selected) != len(set(selected)):
        raise ValueError('families must not contain duplicates')
    snapshots = []
    for family in selected:
        rng = random.Random(f'allocation-snapshot-v1:{seed}:{family}')
        hospitals, patients = _BUILDERS[family](rng)
        snapshot = {
            'id': f'allocation-{family}-{seed}',
            'family': family,
            'seed': seed,
            'synthetic': True,
            'description': FAMILY_DESCRIPTIONS[family],
            'at': 10,
            'hospitals': hospitals,
            'patients': patients,
        }
        validate_allocation_snapshot(snapshot)
        snapshots.append(snapshot)
    return snapshots
