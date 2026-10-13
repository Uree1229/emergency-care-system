"""문의 방식·배정 방식 비교 시뮬레이터 (배정 알고리즘 구체화 문서 10절, T10~T13).

비교군
- B0: 순차 문의 + 최근접 수용 병원. 상황 변경 알림 없음.
- B1: 상위 K곳 병렬 문의 + 먼저 수용한 병원 확정(탐욕) + 상황 변경 알림 후 대체 병원 문의.
- B2: B1과 같은 문의·알림 + 솔버 배정. SolverPolicy에 연결 지점만 있고 아직 구현하지 않았다.

공정성
- 같은 seed는 모든 비교군에 같은 환자 발생, 돌발 유입, 치료시간, 응답 지연·미응답, 이동시간 오차를 준다.
  (병원·환자·시도 번호로 난수를 고정해 정책이 달라도 같은 사건이 나온다.)
- 병원 수용 여부는 응답 시점의 실제 상태(치료 중 + 확정된 도착 예정)로 판단한다.
- 도착했는데 자리가 없으면 두 비교군 모두 그 병원에서 대기한다. 알림 유무만 다르다.

한계 (결과 해석 시 함께 적을 것)
- 분 단위 이산 시뮬레이션이며 모든 분포·비율은 실험 가정이다. 임상적 효과를 주장하지 않는다.
- 병원 응답 지연은 문의 방식과 무관하게 같은 분포를 쓴다. 병렬 문의로 늘어나는 병원 부담은 반영하지 않는다.
"""
import math
import random
import statistics
from dataclasses import dataclass, field

from server.synthetic import CENTER, HOSPITALS
from server.travel import synthetic_estimate

CONFIG = dict(
    horizon=240,            # 환자 발생 구간(분)
    tail=600,               # 발생 종료 후 관찰 구간(분). 이때까지 치료 못 받으면 미치료로 보고
    arrival_rate=0.15,      # 분당 환자 발생
    walkin_rate=0.006,      # 병원별 분당 시스템 미사용 환자 유입
    resp_median=3.0,        # 병원 응답 지연 중앙값(분)
    resp_sigma=0.7,
    nonresponse=0.2,        # 미응답 확률
    timeout=10,             # 미응답으로 보는 대기(분)
    k_parallel=3,           # 병렬 문의 병원 수
    retry_wait=5,           # 후보를 다 돌았을 때 다시 찾기까지(분)
    duration_mean=45,       # 치료 점유 평균(분)
    init_occupancy=0.6,
    rural_share=0.1,        # 의료공백지역 발생 비율
    allowance={4: 30, 2: 60, 1: 120},   # 요청~치료 시작 허용 시간(분). 실험 설정
)
CAPS = ['정형외과', '소아청소년과', '심장내과', '신경외과', '외과']


def rng_for(seed, *key):
    return random.Random('|'.join(map(str, (seed,) + key)))


@dataclass
class Hospital:
    id: str
    loc: tuple
    caps: set
    cap: dict
    active: list = field(default_factory=list)      # [종료 시각, 요구 자원]
    queue: list = field(default_factory=list)       # 도착했지만 자리가 없는 환자
    committed: dict = field(default_factory=dict)   # 확정된 도착 예정 환자 id → 요구 자원

    def used(self, r):
        return sum(d.get(r, 0) for _, d in self.active)

    def expected(self, r):
        return sum(d.get(r, 0) for d in self.committed.values())

    def compatible(self, p):
        return p.caps <= self.caps and all(self.cap.get(r, 0) >= q for r, q in p.demand.items())

    def can_accept(self, p):
        return self.compatible(p) and all(self.cap[r] - self.used(r) - self.expected(r) >= q
                                          for r, q in p.demand.items())

    def fits_now(self, demand):
        return all(self.cap.get(r, 0) - self.used(r) >= q for r, q in demand.items())


@dataclass
class Patient:
    id: str
    t_req: int
    loc: tuple
    caps: set
    demand: dict
    priority: int
    duration: int
    external: bool = False
    state: str = 'SEARCH'
    asks: list = field(default_factory=list)
    attempts: dict = field(default_factory=dict)
    tried: set = field(default_factory=set)
    retry_at: int = None
    dest: str = None
    origin: tuple = None
    depart_t: int = None
    arrive_t: int = None
    first_arrive_plan: int = None
    t_commit: int = None
    t_start: int = None
    queued_t: int = None
    notified: bool = False
    inquiries: int = 0
    redirects: int = 0
    no_compatible: bool = False


def make_world(seed, cfg=CONFIG):
    """정책과 무관한 사건을 미리 만든다."""
    r = rng_for(seed, 'world')
    hospitals = []
    for hid, _, (dlat, dlon), caps, beds, teams in HOSPITALS:
        h = Hospital(hid, (CENTER[0] + dlat, CENTER[1] + dlon), set(caps), {'ed_bed': beds, 'special_team': teams})
        for res, n in h.cap.items():
            for _ in range(round(n * cfg['init_occupancy'] * r.uniform(0.6, 1.2))):
                if h.used(res) < n:
                    h.active.append([r.randint(5, 90), {res: 1}])
        hospitals.append(h)
    patients, t, i = [], 0.0, 0
    while cfg['arrival_rate'] > 0:
        t += r.expovariate(cfg['arrival_rate'])
        if t >= cfg['horizon']:
            break
        i += 1
        rural = r.random() < cfg['rural_share']
        base = (CENTER[0] - 0.2, CENTER[1] - 0.17) if rural else CENTER
        spread = 0.05 if rural else 0.15
        caps = set(r.choice([[], [], []] + [[c] for c in CAPS]))
        demand = {'ed_bed': 1, **({'special_team': 1} if caps and r.random() < 0.3 else {})}
        patients.append(Patient(f'S{seed}-{i:03d}', int(t), (base[0] + r.uniform(-spread, spread),
                                base[1] + r.uniform(-spread, spread)), caps, demand,
                                r.choices([4, 2, 1], weights=[0.25, 0.45, 0.30])[0],
                                max(10, round(r.lognormvariate(math.log(cfg['duration_mean']), 0.5)))))
    walkins = []
    for h in hospitals:
        t, j = 0.0, 0
        while cfg['walkin_rate'] > 0:
            t += r.expovariate(cfg['walkin_rate'])
            if t >= cfg['horizon']:
                break
            j += 1
            walkins.append((int(t), h.id, Patient(f'W{seed}-{h.id}-{j}', int(t), h.loc, set(), {'ed_bed': 1}, 4,
                                                  max(10, round(r.lognormvariate(math.log(60), 0.5))),
                                                  external=True)))
    return hospitals, patients, sorted(walkins, key=lambda w: w[0])


def travel_minutes(seed, pid, hid, leg, a, b):
    base = synthetic_estimate(a, b)['duration_seconds'] / 60
    return max(1, math.ceil(base * rng_for(seed, 'travel', pid, hid, leg).uniform(0.9, 1.3)))


# ---------------------------------------------------------------- 정책

class SequentialPolicy:
    """B0: 가까운 순서로 한 곳씩 문의하고, 처음 수용한 병원으로 간다."""
    name, batch_size, notify = 'B0_sequential', 1, False

    def choose(self, sim, p, accepted, t):
        return accepted[0]


class ParallelGreedyPolicy:
    """B1: 상위 K곳에 동시에 문의하고, 먼저 수용한 병원을 확정한다. 상황 변경 알림을 받는다."""
    name, notify = 'B1_parallel_greedy', True

    def __init__(self, k=CONFIG['k_parallel']):
        self.batch_size = k

    def choose(self, sim, p, accepted, t):
        return min(accepted, key=lambda hid: sim.travel(p, hid, p.loc))


class SolverPolicy(ParallelGreedyPolicy):
    """B2 연결 지점. 솔버 담당이 choose를 구현한다.

    choose(sim, p, accepted, t)는 환자 p에 대해 지금 유효한 수용 병원 목록을 받아 확정할 병원 ID를 돌려준다.
    None을 돌려주면 확정을 미루고 다음 응답을 기다린다. sim.hospitals(치료 중·도착 예정 포함),
    sim.patients(다른 환자 상태)를 읽어 전체 배정안을 만들 수 있다. 미래 사건은 넘기지 않는다.
    """
    name = 'B2_solver'

    def choose(self, sim, p, accepted, t):
        raise NotImplementedError('solver_improved.py 연결 필요')


# ---------------------------------------------------------------- 시뮬레이터

class Simulation:
    def __init__(self, seed, policy, cfg=CONFIG, world=None):
        self.seed, self.policy, self.cfg = seed, policy, cfg
        hs, ps, self.walkins = make_world(seed, cfg) if world is None else world
        self.hospitals = {h.id: h for h in hs}
        self.patients = {p.id: p for p in ps}
        self.pending = sorted(ps, key=lambda p: p.t_req)

    def travel(self, p, hid, loc, leg=0):
        return travel_minutes(self.seed, p.id, hid, leg, loc, self.hospitals[hid].loc)

    def candidates(self, p, loc, exclude=()):
        ok = [h.id for h in self.hospitals.values() if h.compatible(p) and h.id not in exclude]
        return sorted(ok, key=lambda hid: self.travel(p, hid, loc))

    def position(self, p, t):
        if p.state != 'TRANSIT':
            return p.loc
        frac = min(1.0, (t - p.depart_t) / max(1, p.arrive_t - p.depart_t))
        d = self.hospitals[p.dest].loc
        return (p.origin[0] + (d[0] - p.origin[0]) * frac, p.origin[1] + (d[1] - p.origin[1]) * frac)

    def send_asks(self, p, t, exclude=()):
        loc = self.position(p, t)
        open_ = {a['hid'] for a in p.asks}
        cands = [h for h in self.candidates(p, loc, exclude) if h not in p.tried and h not in open_]
        if not cands and not p.asks:
            if not self.candidates(p, loc, exclude):
                p.no_compatible = p.state == 'SEARCH'
                if p.no_compatible:
                    p.state = 'NO_COMPATIBLE'
                return
            p.tried.clear()        # 다 돌았으면 잠시 뒤 처음부터 다시 문의
            p.retry_at = t + self.cfg['retry_wait']
            return
        for hid in cands[:self.policy.batch_size - len(p.asks)]:
            n = p.attempts.get(hid, 0) + 1
            p.attempts[hid] = n
            r = rng_for(self.seed, 'response', p.id, hid, n)
            silent = r.random() < self.cfg['nonresponse']
            delay = max(1, round(r.lognormvariate(math.log(self.cfg['resp_median']), self.cfg['resp_sigma'])))
            p.asks.append(dict(hid=hid, at=t + (self.cfg['timeout'] if silent else delay), silent=silent))
            p.tried.add(hid)
            p.inquiries += 1

    def commit(self, p, hid, t, redirect=False):
        loc = self.position(p, t)
        if redirect:
            self.hospitals[p.dest].committed.pop(p.id, None)
            p.redirects += 1
        p.state, p.dest, p.origin, p.depart_t = 'TRANSIT', hid, loc, t
        p.arrive_t = t + self.travel(p, hid, loc, leg=p.redirects)
        p.first_arrive_plan = p.first_arrive_plan or p.arrive_t
        p.t_commit = p.t_commit if p.t_commit is not None else t
        p.asks, p.notified = [], False
        self.hospitals[hid].committed[p.id] = p.demand

    def resolve_asks(self, p, t):
        due = [a for a in p.asks if a['at'] == t]
        if not due:
            return
        p.asks = [a for a in p.asks if a['at'] != t]
        accepted = [a['hid'] for a in due if not a['silent'] and self.hospitals[a['hid']].can_accept(p)]
        if accepted:
            hid = self.policy.choose(self, p, accepted, t)
            if hid is not None:
                self.commit(p, hid, t, redirect=p.state == 'TRANSIT')
                return
        if p.state == 'SEARCH' and not p.asks:
            self.send_asks(p, t)

    def notify_overflow(self, h, t):
        """B1: 돌발 유입으로 확정 수요가 여유를 넘으면 늦게 도착할 환자부터 알리고 대체 병원 문의를 시작한다."""
        for r in h.cap:
            over = h.used(r) + h.expected(r) - h.cap[r]
            for pid in sorted(h.committed, key=lambda x: -self.patients[x].arrive_t):
                if over <= 0:
                    break
                p = self.patients[pid]
                if p.notified or p.demand.get(r, 0) == 0 or p.arrive_t <= t:
                    continue
                p.notified = True
                over -= p.demand[r]
                self.send_asks(p, t, exclude={h.id})

    def start_treatments(self, h, t):
        h.queue.sort(key=lambda p: (-p.priority, p.queued_t))
        for p in list(h.queue):
            if h.fits_now(p.demand):
                h.queue.remove(p)
                h.active.append([t + p.duration, p.demand])
                p.state, p.t_start = 'TREATING', t

    def run(self):
        end = self.cfg['horizon'] + self.cfg['tail']
        walk = list(self.walkins)
        for t in range(end):
            for h in self.hospitals.values():
                h.active = [a for a in h.active if a[0] > t]
            while walk and walk[0][0] == t:
                _, hid, w = walk.pop(0)
                h = self.hospitals[hid]
                w.state, w.queued_t = 'QUEUED', t
                h.queue.append(w)
                self.start_treatments(h, t)
                if self.policy.notify:
                    self.notify_overflow(h, t)
            while self.pending and self.pending[0].t_req == t:
                self.send_asks(self.pending.pop(0), t)
            for p in self.patients.values():
                if p.state in ('SEARCH', 'TRANSIT'):
                    self.resolve_asks(p, t)
                if p.state == 'SEARCH' and p.retry_at == t:
                    p.retry_at = None
                    self.send_asks(p, t)
                if p.state == 'TRANSIT' and p.arrive_t == t:
                    h = self.hospitals[p.dest]
                    h.committed.pop(p.id, None)
                    p.asks, p.state, p.queued_t = [], 'QUEUED', t
                    h.queue.append(p)
            for h in self.hospitals.values():
                self.start_treatments(h, t)
        return self.metrics()

    def metrics(self):
        ps = list(self.patients.values())
        out = dict(policy=self.policy.name, seed=self.seed, patients=len(ps))
        treated = [p for p in ps if p.t_start is not None]
        waits = [p.t_start - p.t_req for p in treated]
        out.update(treated=len(treated), untreated=len(ps) - len(treated),
                   no_compatible=sum(p.no_compatible for p in ps),
                   mean_wait=statistics.mean(waits) if waits else None,
                   median_wait=statistics.median(waits) if waits else None,
                   p90_wait=sorted(waits)[int(0.9 * (len(waits) - 1))] if waits else None,
                   inquiries=sum(p.inquiries for p in ps),
                   redirects=sum(p.redirects for p in ps),
                   time_to_commit=statistics.mean([p.t_commit - p.t_req for p in ps if p.t_commit is not None] or [0]),
                   extra_travel=sum(p.arrive_t - p.first_arrive_plan for p in ps if p.redirects))
        for g in (4, 2, 1):
            grp = [p for p in ps if p.priority == g]
            gw = [p.t_start - p.t_req for p in grp if p.t_start is not None]
            out[f'g{g}'] = dict(total=len(grp), untreated=sum(p.t_start is None for p in grp),
                                over_allowance=sum(p.t_start is None or p.t_start - p.t_req > self.cfg['allowance'][g]
                                                   for p in grp),
                                mean_wait=statistics.mean(gw) if gw else None,
                                max_wait=max(gw) if gw else None)
        return out
