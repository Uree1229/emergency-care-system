"""Predeclared synthetic scaling cases for current-information allocation.

The seven families and three sizes are fixed before results are inspected.
Seeds 12101--12102 are development; 13101--13104 are untouched validation.
No future events, treatment outcomes, real patient data or actual 119 policy
are represented. Priorities and resource quantities are experimental inputs.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
import random

from .allocation_scenarios import validate_allocation_snapshot


FAMILIES = ('singleton_trap', 'multi_resource', 'dense_random', 'sparse_random',
            'scarce_long_detour', 'abundant_control', 'no_capacity_control')
SIZES = (20, 50, 100)
DEVELOPMENT_SEEDS = (12101, 12102)
VALIDATION_SEEDS = (13101, 13102, 13103, 13104)
PRIORITIES = ('high', 'medium', 'low')
DESCRIPTIONS = {
    'singleton_trap': '같은 우선순위에서 유연한 환자가 먼저 도착하는 두 병원 자원 경합 묶음.',
    'multi_resource': '병상·전문팀 두 자원과 1~2단위 수요가 함께 경합하는 합성 입력.',
    'dense_random': '수용 후보가 많은 조밀한 무작위 자원 경합 입력.',
    'sparse_random': '환자별 유효 후보가 1~3곳인 희소한 무작위 입력.',
    'scarce_long_detour': '가까운 자원이 부족하며 일부 환자는 먼 대체병원도 가능한 입력.',
    'abundant_control': '모든 환자를 받을 수 있는 충분한 자원과 병원 간 동일 이동시간 대조군.',
    'no_capacity_control': '수용 응답 여부와 무관하게 현재 잔여 자원이 0인 대조군.',
}


def _hospital(index, beds, teams, *, occupied=None, committed=None, queued=None):
    zero = {'ed_bed': 0, 'special_team': 0}
    return dict(id=f'SYN-H{index:03d}', name=f'합성병원 {index}',
                lat=round(35.1 + index * .001, 6), lon=round(126.8 + index * .001, 6),
                capabilities=['general', 'specialist'],
                capacity=dict(ed_bed=beds, special_team=teams),
                occupied=zero | (occupied or {}), committed=zero | (committed or {}),
                queued=zero | (queued or {}))


def _patient(index, hospitals, offers, travel, rng, *, priority=None,
             beds=1, teams=0, requested=None):
    requested = rng.randint(0, 5) if requested is None else requested
    return dict(id=f'SYN-P{index:03d}', t_req=requested,
                lat=round(35.12 + index * .0002, 6), lon=round(126.81 + index * .0002, 6),
                priority=priority or rng.choice(PRIORITIES),
                required_capabilities=['general', 'specialist'] if teams else ['general'],
                resource_demands=dict(ed_bed=beds, special_team=teams),
                accepted_hospitals=[hospitals[i]['id'] for i in sorted(offers)],
                accepted_at={hospitals[i]['id']: rng.randint(requested, 10) for i in sorted(offers)},
                travel_minutes={h['id']: travel[i] for i, h in enumerate(hospitals)})


def _build(family, size, rng):
    if family == 'singleton_trap':
        hospitals = [_hospital(i + 1, 1, 0) for i in range(size)]
        patients = []
        for block in range(size // 2):
            a, b = block * 2, block * 2 + 1
            minutes = [rng.randint(25, 45) for _ in hospitals]
            minutes[a], minutes[b] = rng.randint(4, 7), rng.randint(11, 15)
            patients.append(_patient(a + 1, hospitals, [a, b], minutes, rng,
                                     priority='medium', requested=0))
            minutes = [rng.randint(25, 45) for _ in hospitals]
            minutes[a] = rng.randint(6, 9)
            patients.append(_patient(b + 1, hospitals, [a], minutes, rng,
                                     priority='medium', requested=1))
        return hospitals, patients
    if family == 'abundant_control':
        hospitals = [_hospital(i + 1, size, size) for i in range(4)]
        patients = [_patient(i + 1, hospitals, range(4), [rng.randint(5, 30)] * 4, rng,
                             priority=PRIORITIES[i % 3], teams=int(i % 4 == 0))
                    for i in range(size)]
        return hospitals, patients
    if family == 'no_capacity_control':
        hospitals = [_hospital(i + 1, 2, 1, occupied={'ed_bed': 1, 'special_team': 1},
                               committed={'ed_bed': 1}) for i in range(5)]
        patients = [_patient(i + 1, hospitals, range(5) if i % 2 else [],
                             [rng.randint(5, 30) for _ in hospitals], rng,
                             priority=PRIORITIES[i % 3], teams=int(i % 4 == 0))
                    for i in range(size)]
        return hospitals, patients

    count = max(4, math.ceil(size / (8 if family == 'dense_random' else 6)))
    if family == 'scarce_long_detour':
        count = max(5, math.ceil(size / 4))
    per_hospital = max(1, math.ceil(size * .9 / count))
    hospitals = []
    for i in range(count):
        beds = max(1, per_hospital + rng.randint(-1, 1))
        teams = max(1, math.ceil(beds * .4))
        # Leave some currently observed occupied/committed demand. Offers do
        # not reserve these resources; every allocator must check current free.
        occupied = {'ed_bed': int(i % 5 == 0), 'special_team': 0}
        committed = {'ed_bed': int(i % 7 == 0), 'special_team': 0}
        hospitals.append(_hospital(i + 1, beds, teams,
                                   occupied=occupied, committed=committed))
    patients = []
    for i in range(size):
        if family == 'dense_random':
            offers = sorted(rng.sample(range(count), rng.randint(max(2, count // 2), count)))
        elif family == 'sparse_random':
            offers = sorted(rng.sample(range(count), rng.randint(1, min(3, count))))
        elif family == 'scarce_long_detour':
            close = i % max(2, count // 3)
            offers = [close] if i % 3 == 0 else sorted({close, rng.randrange(count // 3, count)})
        else:
            offers = sorted(rng.sample(range(count), rng.randint(2, min(5, count))))
        travel = [rng.randint(4, 50) for _ in hospitals]
        if family == 'scarce_long_detour':
            travel = [rng.randint(4, 9) if h < max(2, count // 3) else rng.randint(45, 100)
                      for h in range(count)]
        teams = int(rng.random() < (.55 if family == 'multi_resource' else .3))
        beds = 2 if family == 'multi_resource' and rng.random() < .3 else 1
        patients.append(_patient(i + 1, hospitals, offers, travel, rng,
                                 priority=PRIORITIES[i % 3], beds=beds, teams=teams))
    return hospitals, patients


def validate_stress_snapshot(snapshot):
    """Validate every current-information field, without accepting future data."""
    if not isinstance(snapshot, Mapping):
        raise ValueError('snapshot must be a mapping')
    if not isinstance(snapshot.get('family'), str) or snapshot['family'] not in FAMILIES:
        raise ValueError('unknown stress family')
    if type(snapshot.get('size')) is not int or snapshot['size'] not in SIZES:
        raise ValueError('size must be one of the predeclared sizes')
    if set(snapshot) != {'id', 'family', 'seed', 'size', 'synthetic', 'description',
                         'at', 'hospitals', 'patients'}:
        raise ValueError('unknown or missing snapshot fields')
    if not isinstance(snapshot['description'], str) or not snapshot['description'].strip():
        raise ValueError('description must be nonblank')
    for field in ('patients', 'hospitals'):
        if not isinstance(snapshot.get(field), list) or not snapshot[field]:
            raise ValueError(f'{field} must be a nonempty list')
        if any(not isinstance(row, Mapping) for row in snapshot[field]):
            raise ValueError(f'{field} records must be mappings')
    if len(snapshot['patients']) != snapshot['size']:
        raise ValueError('patient count does not match declared size')
    patient_fields = {'id', 't_req', 'lat', 'lon', 'priority', 'required_capabilities',
                      'resource_demands', 'accepted_hospitals', 'accepted_at', 'travel_minutes'}
    hospital_fields = {'id', 'name', 'lat', 'lon', 'capabilities', 'capacity',
                       'occupied', 'committed', 'queued'}
    for row in snapshot['patients']:
        if set(row) != patient_fields:
            raise ValueError('unknown or missing patient fields')
    for row in snapshot['hospitals']:
        if set(row) != hospital_fields or not isinstance(row['name'], str) or not row['name'].strip():
            raise ValueError('unknown, missing or invalid hospital fields')
    # Reuse the frozen validator for all IDs, coordinates, exact resource keys,
    # current request/reply times, integer travel and capability-compatible offers.
    # Only its old family whitelist is substituted in a fresh shallow mapping.
    try:
        validate_allocation_snapshot(dict(snapshot, family='large_batch'))
    except (TypeError, AttributeError) as exc:
        raise ValueError('malformed snapshot field type') from exc


def build_stress_snapshots(seed, families=None, sizes=None):
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a nonnegative integer')
    if isinstance(families, (str, bytes)) or isinstance(sizes, (str, bytes)):
        raise ValueError('family and size selections must be sequences')
    families = FAMILIES if families is None else tuple(families)
    sizes = SIZES if sizes is None else tuple(sizes)
    if any(not isinstance(f, str) or f not in FAMILIES for f in families) or len(families) != len(set(families)):
        raise ValueError('invalid or duplicate families')
    if any(type(s) is not int or s not in SIZES for s in sizes) or len(sizes) != len(set(sizes)):
        raise ValueError('invalid or duplicate sizes')
    result = []
    for family in families:
        for size in sizes:
            rng = random.Random(f'allocation-stress-v1:{seed}:{family}:{size}')
            hospitals, patients = _build(family, size, rng)
            snapshot = dict(id=f'allocation-stress-{family}-{size}-{seed}', family=family,
                            seed=seed, size=size, synthetic=True, description=DESCRIPTIONS[family],
                            at=10, hospitals=hospitals, patients=patients)
            validate_stress_snapshot(snapshot)
            result.append(snapshot)
    return result
