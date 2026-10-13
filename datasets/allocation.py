"""관측 입력과 실제 상태를 분리한 합성 스냅샷. 환자 정보는 모두 가상이다."""
import copy
import hashlib
import random

from .io import partition

DEFAULTS = dict(hospitals=10, patients=20, beds=(2, 12), teams=(0, 3), occupied=0.45,
                travel=(1, 30), duration=(10, 60), deadline=(30, 150), horizon=180, now=60,
                special_share=0.3, eligibility=0.65, acceptance_share=0.85, priorities=(4, 2, 1), lock_share=0.0)


def rng_for(profile, seed):
    digest = hashlib.sha256(f'ecs-synthetic-v1/{profile}/{seed}'.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], 'big'))


def make_case(profile, description, changes, seed):
    cfg = dict(DEFAULTS, **changes)
    r = rng_for(profile, seed)
    n, now = cfg['hospitals'], cfg['now']
    data = dict(version=1, now=now, horizon=now + cfg['horizon'], capacities=[], patients=[], blocks=[],
                fixed={}, destination_locks={})
    for h in range(n):
        caps = [r.randint(*cfg['beds']), r.randint(*cfg['teams'])]
        data['capacities'].append(caps)
        for resource, cap in enumerate(caps):
            used = min(cap, round(cap * cfg['occupied']))
            if used:
                demands = [0, 0]
                demands[resource] = used
                duration = cfg['horizon'] + 300 if cfg.get('hold_occupancy') else r.randint(5, 90)
                data['blocks'].append(dict(h=h, start=now, duration=duration, special=resource == 1, demands=demands))
    rows = []
    mode = cfg.get('mode')
    for i in range(cfg['patients']):
        special = r.random() < cfg['special_share']
        compatible = [h for h in range(n) if r.random() < cfg['eligibility']]
        accepted = [h for h in compatible if r.random() < cfg['acceptance_share']]
        release = r.randint(max(0, now - 30), now)
        p = dict(id=f'P{i:03d}', priority=r.choice(cfg['priorities']), special=special, release=release,
                 deadline=r.randint(*cfg['deadline']), duration=r.randint(*cfg['duration']),
                 travel=[r.randint(*cfg['travel']) for _ in range(n)], eligible=accepted,
                 demands=[1, int(special)])
        rows.append(dict(id=p['id'], priority_source='expert', reported_priority=p['priority'],
                         ground_truth_priority=p['priority'], state='WAITING',
                         compatible_hospitals=compatible, accepted_hospitals=accepted,
                         symptoms='가상 실험 입력', resource_source='synthetic'))
        if mode == 'scarce_department' and i % 3 == 0:
            p['eligible'] = [0]
            rows[-1].update(compatible_hospitals=[0], accepted_hospitals=[0])
        if mode == 'expired_acceptances':
            p['eligible'] = []
            rows[-1].update(accepted_hospitals=[], expired_hospitals=accepted)
        if mode in ('unknown_priority', 'self_reported_low'):
            rows[-1].update(priority_source='unknown' if mode == 'unknown_priority' else 'self',
                            reported_priority=None if mode == 'unknown_priority' else 1)
            p['priority'] = 4
        if mode == 'already_overdue':
            p.update(release=r.randint(0, 10), deadline=r.randint(5, 20))
        if mode == 'multi_bed_demand':
            p['demands'][0] = r.randint(2, 4)
        if mode == 'team_only_demand':
            p.update(special=True, demands=[0, 1])
        if cfg['lock_share'] and r.random() < cfg['lock_share']:
            h = r.randrange(n)
            p['eligible'] = sorted(set(p['eligible']) | {h})
            data['destination_locks'][p['id']] = h
            rows[-1].update(state='IN_TRANSIT', confirmed_hospital=h,
                            compatible_hospitals=sorted(set(compatible) | {h}),
                            accepted_hospitals=list(p['eligible']))
            p['arrivals'] = [now + t for t in p['travel']]
            if mode == 'locked_overdue':
                p.update(release=0, deadline=5)
        data['patients'].append(p)
    if mode == 'locked_conflict' and data['patients']:
        p = data['patients'][0]
        p['eligible'] = [0]
        data['destination_locks'][p['id']] = 0
        rows[0].update(state='IN_TRANSIT', confirmed_hospital=0, compatible_hospitals=[0], accepted_hospitals=[0])
        data['capacities'][0] = [0, 0]
        data['blocks'] = [b for b in data['blocks'] if b['h'] != 0]
    if mode == 'known_release':
        for i, b in enumerate(data['blocks']):
            b['duration'] = (5, 10, 20)[i % 3]
    if mode == 'fixed_treatment' and data['patients']:
        p = data['patients'][0]
        p.update(eligible=[0], release=now - 10, deadline=180, special=False, demands=[1, 0], travel=[0] * n)
        data['fixed'][p['id']] = dict(h=0, start=now - 5)
        rows[0].update(state='TREATING', confirmed_hospital=0, compatible_hospitals=[0], accepted_hospitals=[0])
        data['capacities'][0][0] = max(1, data['capacities'][0][0])
        data['blocks'] = [b for b in data['blocks'] if b['h'] != 0]
    actual = copy.deepcopy(data)
    uncertainty = []
    if mode in ('stale_capacity', 'compound_failure'):
        for h, cap in enumerate(data['capacities']):
            cap[0] += r.randint(1, 4)
        uncertainty.append('capacity_overreported')
    if mode == 'pessimistic_capacity':
        for cap in actual['capacities']:
            cap[0] += r.randint(1, 4)
        uncertainty.append('capacity_underreported')
    if mode in ('unobserved_walkin', 'future_walkin', 'compound_failure'):
        h = r.randrange(n)
        actual['blocks'].append(dict(h=h, start=now + (r.randint(1, 15) if mode == 'future_walkin' else 0),
                                     duration=r.randint(20, 90), special=False, demands=[1, 0]))
        uncertainty.append('unobserved_external_arrival')
    if mode in ('travel_underestimate', 'compound_failure', 'travel_overestimate'):
        factor = 0.6 if mode == 'travel_overestimate' else 1.8
        for p in actual['patients']:
            p['travel'] = [max(1, round(t * factor)) for t in p['travel']]
            if 'arrivals' in p:
                p['arrivals'] = [now + t for t in p['travel']]
        uncertainty.append('travel_estimation_error')
    if mode == 'duration_underestimate':
        for p in actual['patients']:
            p['duration'] = round(p['duration'] * 1.8)
        uncertainty.append('duration_estimation_error')
    if mode == 'missing_resource_info':
        h = r.randrange(n)
        data['capacities'][h] = [0, 0]
        data['blocks'] = [b for b in data['blocks'] if b['h'] != h]
        uncertainty.append('unknown_resource_conservatively_blocked')
    return dict(id=f'ALLOC-{profile}-{seed:04d}', kind='allocation', schema_version=1,
                profile=profile, description=description, seed=seed, split=partition(seed),
                source='synthetic', time_unit='minute', config=cfg, observed=data, actual=actual,
                patient_metadata=rows, uncertainty=uncertainty,
                evaluation=dict(oracle_label=None, policy_input='observed', hidden_truth='actual',
                                note='actual은 사후 검증 전용. 정책 입력이나 확정된 최적 정답으로 사용하지 않음'))


def golden_cases():
    """작고 해석 가능한 반례·경계조건. expected는 해당 고정 사례에만 적용한다."""
    def patient(pid, priority=4, **kw):
        return dict(id=pid, priority=priority, special=False, demands=[1, 0], release=0,
                    deadline=0, duration=1, travel=[0, 0], eligible=[0, 1], **kw)

    base = dict(version=1, now=0, horizon=0, capacities=[[1, 0], [1, 0]], patients=[patient('A'), patient('B')],
                fixed={}, destination_locks={}, blocks=[])
    cases = []
    def add(name, description, data, expected):
        cases.append(dict(id='GOLD-' + name, kind='golden', schema_version=1, profile=name,
                          description=description, seed=None, split='regression', source='synthetic',
                          time_unit='minute', observed=data, actual=copy.deepcopy(data), expected=expected))
    d = copy.deepcopy(base)
    d['patients'][1]['eligible'] = [0]
    add('scarce_choice', 'A는 두 병원, B는 H0만 가능: 전체 배정의 이점', d, {'assigned': 2, 'assignment': {'A': 1, 'B': 0}})
    d = copy.deepcopy(base)
    d['capacities'] = [[0, 0], [0, 0]]
    add('zero_capacity', '용량 0이면 배정하지 않음', d, {'assigned': 0})
    d = copy.deepcopy(base)
    for p in d['patients']:
        p['eligible'] = []
    add('no_acceptance', '수용 응답이 없으면 배정하지 않음', d, {'assigned': 0})
    d = copy.deepcopy(base)
    d['patients'] = []
    add('no_patients', '환자 0명 경계조건', d, {'assigned': 0})
    d = copy.deepcopy(base)
    d['patients'] = d['patients'][:1]
    d.update(horizon=2)
    d['patients'][0].update(deadline=2, travel=[2, 3])
    add('deadline_equal', '도착과 최대 대기 경계가 정확히 같음', d, {'assigned': 1, 'starts': {'A': 2}})
    d = copy.deepcopy(d)
    d['patients'][0]['travel'] = [3, 3]
    add('deadline_one_late', '최대 대기보다 한 단위 늦음', d, {'assigned': 0})
    d = copy.deepcopy(base)
    d['patients'] = d['patients'][:1]
    d['patients'][0].update(eligible=[0], deadline=2)
    d.update(horizon=2, blocks=[dict(h=0, start=0, duration=2, special=False)])
    add('release_equals_start', '점유 종료와 다음 시작이 같은 시각', d, {'assigned': 1, 'starts': {'A': 2}})
    d = copy.deepcopy(d)
    d['patients'][0]['deadline'] = 0
    d['destination_locks'] = {'A': 0}
    add('lock_can_delay', '기존 목적지 유지·시작시각 지연 가능', d, {'assigned': 1, 'assignment': {'A': 0}, 'starts': {'A': 2}})
    d = copy.deepcopy(d)
    d['capacities'][0] = [0, 0]
    d['blocks'] = []
    add('lock_impossible', '기존 목적지가 불가능하면 버리지 않고 재확인', d, {'needs_review': True})
    d = copy.deepcopy(base)
    d['patients'] = d['patients'][:1]
    d['patients'][0].update(special=True, demands=[1, 1])
    add('team_absent', '자리만 있어도 전문팀이 없으면 미배정', d, {'assigned': 0})
    d = copy.deepcopy(base)
    d['patients'][0]['demands'] = [2, 0]
    add('multi_demand', '요구량이 병원 용량보다 큼', d, {'assigned': 1})
    d = copy.deepcopy(base)
    d['patients'] = d['patients'][:1]
    d['patients'][0].update(special=True, demands=[0, 1])
    d['capacities'] = [[0, 1], [0, 0]]
    add('team_only', '전문팀만 사용하는 추상 자원 경계', d, {'assigned': 1})
    d = copy.deepcopy(base)
    d['patients'] = [dict(id='HIGH', priority=4, special=False, release=0, deadline=0, duration=3,
                          travel=[0], eligible=[0])] + [dict(id=f'LOW{i}', priority=1, special=False, release=0,
                          deadline=2, duration=1, travel=[0], eligible=[0]) for i in range(3)]
    d.update(capacities=[[1, 0]], horizon=2)
    add('priority_tradeoff', '총 인원과 높은 군 우선 목표의 차이', d, {'high_assigned': 1, 'assigned': 1})
    d = copy.deepcopy(base)
    d['patients'] = d['patients'][:1]
    d['fixed'] = {'A': {'h': 0, 'start': 0}}
    d['blocks'] = [dict(h=0, start=0, duration=1, special=False)]
    add('fixed_collision', '이미 고정된 점유의 충돌', d, {'needs_review': True})
    d = copy.deepcopy(base)
    d['patients'] = d['patients'][:1]
    d.update(now=5, horizon=10)
    add('elapsed_not_reset', '재계산해도 최초 요청 대기를 초기화하지 않음', d, {'assigned': 0})
    return cases
