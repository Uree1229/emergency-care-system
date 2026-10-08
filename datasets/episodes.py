"""기존 B0/B1 시뮬레이터에 그대로 재생할 수 있는 사전 생성 사건 데이터."""
import math

from simulation.sim import CONFIG, Hospital, Patient, make_world, rng_for, travel_minutes

from .io import partition


def patient_dict(p):
    return dict(id=p.id, requested_at=p.t_req, location=list(p.loc), capabilities=sorted(p.caps),
                resource_demands=p.demand, priority=p.priority, duration=p.duration, external=p.external)


def decode_patient(p):
    return Patient(p['id'], p['requested_at'], tuple(p['location']), set(p['capabilities']),
                   dict(p['resource_demands']), p['priority'], p['duration'], external=p['external'])


def make_episode(profile, description, changes, seed):
    cfg = dict(CONFIG, horizon=120, **changes)
    hs, ps, walkins = make_world(seed, cfg)
    events = [dict(at=p.t_req, type='patient_request', patient_id=p.id) for p in ps]
    events += [dict(at=at, type='external_arrival', hospital_id=hid, patient=patient_dict(p)) for at, hid, p in walkins]
    responses, travel = [], []
    for p in ps:
        for h in hs:
            travel.append(dict(patient_id=p.id, hospital_id=h.id,
                               actual_minutes=travel_minutes(seed, p.id, h.id, 0, p.loc, h.loc)))
            for attempt in (1, 2):
                r = rng_for(seed, 'response', p.id, h.id, attempt)
                silent = r.random() < cfg['nonresponse']
                delay = max(1, round(r.lognormvariate(math.log(cfg['resp_median']), cfg['resp_sigma'])))
                responses.append(dict(patient_id=p.id, hospital_id=h.id, attempt=attempt, silent=silent,
                                      delay_minutes=cfg['timeout'] if silent else delay))
    return dict(id=f'EP-{profile}-{seed:04d}', kind='episode', schema_version=1, profile=profile,
                description=description, seed=seed, split=partition(seed), source='synthetic', time_unit='minute',
                config=cfg, hospitals=[dict(id=h.id, location=list(h.loc), capabilities=sorted(h.caps),
                                           capacities=h.cap, initial_occupancy=h.active) for h in hs],
                patients=[patient_dict(p) for p in ps], events=sorted(events, key=lambda e: (e['at'], e['type'], e.get('patient_id', ''))),
                latent_response_oracle=responses, latent_travel_oracle=travel,
                oracle_rule='simulation.sim.rng_for(seed, key); attempts>2 and redirected legs generated on demand',
                visibility='미래 사건·숨겨진 응답·실제 이동시간은 정책 입력 금지. 발생·응답 시점에 공개',
                evaluation=dict(oracle_label=None, available_comparisons=['B0', 'B1'],
                                note='B2의 전체 시스템 평가 연결은 별도 과제'))


def world_from_episode(record):
    """호출할 때마다 새 객체를 만들어 정책 간 상태 변경이 공유되지 않게 한다."""
    hs = [Hospital(h['id'], tuple(h['location']), set(h['capabilities']), dict(h['capacities']),
                   active=[[end, dict(demand)] for end, demand in h['initial_occupancy']]) for h in record['hospitals']]
    ps = [decode_patient(p) for p in record['patients']]
    walkins = [(e['at'], e['hospital_id'], decode_patient(e['patient']))
               for e in record['events'] if e['type'] == 'external_arrival']
    return hs, ps, walkins


def episode_config(record):
    cfg = dict(record['config'])
    cfg['allowance'] = {int(k): v for k, v in cfg['allowance'].items()}
    return cfg
