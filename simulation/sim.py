"""문의 방식·배정 방식 비교 시뮬레이터 (배정 알고리즘 검증계획 2026-10-10, T10~T13).

비교 축은 서로 독립적으로 조합한다.
- 배정 규칙: R0 첫 유효 수용 / R1 예상 도착 우선 / R2 자원 경쟁 고려 / R3 가중합 / R4 사전식 우선순위 공동 배정 / C1 CP-SAT 공동 배정
- 문의 정책: 동시 문의 수 k, 확정 대기 wait(첫 수용 뒤 남은 응답을 기다리는 분), 초기 탐색 반경과 확대 시점
- 조건 변경 대응: notify 켬/끔 (돌발 유입·장비 일시 사용 불가 시 영향 환자만 재확인·대체 문의)

기존 비교군(B0 순차+최근접, B1 병렬+탐욕+알림)은 SequentialPolicy·ParallelGreedyPolicy로 그대로 남긴다.
CP-SAT은 Policy(rule='C1')으로 같은 분의 배정에 연결된다. R3와 같은 목적을 쓰며
현재 배정 묶음에 한해 최적성을 기록한다. 기존 SolverPolicy는 예전 단일결정 확장 자리다.
R3는 솔버 없이 작은 묶음을 정확히 열거하며, 묶음이 크면 R2로 대체한다.

공정성
- 같은 seed는 모든 정책에 같은 환자 발생, 돌발 유입, 장비 사용 불가, 치료시간, 응답 지연·미응답, 이동시간 오차를 준다.
- 정책은 문의한 병원의 응답과 현재 병원 점유(치료 중 + 확정된 도착 예정)만 본다. 아직 발생하지 않은 환자,
  문의하지 않은 병원의 응답, 실제 이동시간 오차는 넘기지 않는다.
- 수용 응답은 만료(accept_ttl) 전까지 유효하고, 확정 시점에 병원의 실제 수용 가능 여부를 다시 확인한다.
- 도착했는데 자리가 없으면 모든 정책에서 그 병원에서 대기한다. 병원 간 전원은 구현하지 않았다.

한계 (결과 해석 시 함께 적을 것)
- 분 단위 이산 시뮬레이션이며 모든 분포·비율은 실험 가정이다. 임상적 효과를 주장하지 않는다.
- 기본 응답 지연은 문의량과 무관하다. 지도 확장의 inquiry_queue를 켜면 별도 FIFO 검토 대기열을 적용한다.
"""
import math
import random
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from itertools import product

from .adaptive_inquiry import decide_inquiry_limit, validate_adaptive_config
from .observed_inquiry import plan_inquiries, validate_observed_config
from .priority_assignment import PriorityJointAssignmentRule
from .cp_sat_assignment import CPSATAssignmentRule
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
    accept_ttl=10,          # 수용 응답 유효시간(분). 서버 ACCEPT_TTL과 같은 값
    outage_rate=0.0,        # 병원별 분당 장비·전문팀 일시 사용 불가 발생률 (0이면 없음)
    outage_resource='special_team',
    outage_minutes=(20, 60),
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

    def waiting(self, r):
        # Arrival removes a commitment before treatment starts. The patient in
        # that queue still needs the resource and must not create a free slot.
        return sum(p.demand.get(r, 0) for p in self.queue)

    def free(self, r):
        return self.cap.get(r, 0) - self.used(r) - self.expected(r) - self.waiting(r)

    def compatible(self, p):
        return p.caps <= self.caps and all(self.cap.get(r, 0) >= q for r, q in p.demand.items())

    def can_accept(self, p):
        return self.compatible(p) and all(self.free(r) >= q for r, q in p.demand.items())

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
    valid: dict = field(default_factory=dict)       # 유효한 수용 응답: 병원 → 만료 시각
    accepted_at: dict = field(default_factory=dict)  # 병원 → 수용 응답 시각 (R0 순서)
    first_valid_t: int = None
    expanded: bool = False
    final_arrive_t: int = None


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


def make_outages(seed, cfg, hospitals):
    """장비·전문팀 일시 사용 불가 사건 (start, end, 병원, 자원). make_world와 다른 난수열을 써서 기존 사건을 바꾸지 않는다."""
    rate, res = cfg.get('outage_rate', 0), cfg.get('outage_resource', 'special_team')
    if not rate:
        return []
    lo, hi = cfg.get('outage_minutes', (20, 60))
    out = []
    for h in hospitals:
        if h.cap.get(res, 0) <= 0:
            continue
        r, t = rng_for(seed, 'outage', h.id), 0.0
        while True:
            t += r.expovariate(rate)
            if t >= cfg['horizon']:
                break
            out.append((int(t), int(t) + r.randint(lo, hi), h.id, res))
    return sorted(out)


def travel_minutes(seed, pid, hid, leg, a, b):
    base = synthetic_estimate(a, b)['duration_seconds'] / 60
    return max(1, math.ceil(base * rng_for(seed, 'travel', pid, hid, leg).uniform(0.9, 1.3)))


def p95(values):
    values = sorted(values)
    return values[min(len(values) - 1, math.ceil(0.95 * len(values)) - 1)] if values else None


# ---------------------------------------------------------------- 기존 비교군

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
    sim.visible_patients(t)(이미 발생한 다른 환자)를 읽어 전체 배정안을 만들 수 있다. 미래 사건은 넘기지 않는다.
    """
    name = 'B2_solver'

    def choose(self, sim, p, accepted, t):
        raise NotImplementedError('solver_improved.py 연결 필요')


# ---------------------------------------------------------------- 배정 규칙 R0~R4 (R4는 priority_assignment.py)

class FirstAcceptRule:
    """R0: 가장 먼저 도착한 유효 수용 응답을 고른다. 같은 분이면 문의 순서(가까운 순)."""
    code = 'R0'

    def choose(self, sim, p, options, t):
        return min(options, key=lambda hid: (p.accepted_at[hid], sim.travel(p, hid, sim.position(p, t))))


class NearestArrivalRule:
    """R1: 지금 유효한 수용 병원 중 예상 도착이 가장 빠른 곳을 고른다."""
    code = 'R1'

    def choose(self, sim, p, options, t):
        loc = sim.position(p, t)
        return min(options, key=lambda hid: (sim.travel(p, hid, loc), hid))


class CompetitionAwareRule:
    """R2: 예상 도착 + 희소 자원 경쟁 비용.

    p가 병원 h의 마지막 남은 자원(reserve 이하로 남김)을 가져가면, 그 자원이 필요하고 h에 갈 수 있는
    다른 대기 환자 q마다 1/(q가 갈 수 있는 병원 수)를 더한다. 비용 단위는 분이며 lam으로 환산한다.
    이미 발생한 환자와 현재 병원 점유만 쓴다. 휴리스틱이며 최적을 보장하지 않는다.
    """
    code = 'R2'

    def __init__(self, lam=15.0, reserve=0):
        self.lam, self.reserve = lam, reserve

    def penalty(self, sim, p, h, t):
        scarce = [r for r, q in p.demand.items() if h.free(r) - q <= self.reserve]
        if not scarce:
            return 0.0
        cost = 0.0
        for q in sim.visible_patients(t):
            if q is p or q.state != 'SEARCH' or not h.can_accept(q) or not any(r in q.demand for r in scarce):
                continue
            alts = sum(1 for g in sim.hospitals.values() if g.can_accept(q))
            cost += 1.0 / max(1, alts)
        return cost

    def score(self, sim, p, hid, t):
        h = sim.hospitals[hid]
        return sim.travel(p, hid, sim.position(p, t)) + self.lam * self.penalty(sim, p, h, t)

    def choose(self, sim, p, options, t):
        return min(options, key=lambda hid: (self.score(sim, p, hid, t), hid))


class JointAssignmentRule:
    """R3: 같은 분에 확정할 환자들을 묶어 함께 배정한다.

    목적: (미배정 환자의 우선순위 합, 우선순위 가중 예상 도착 합)을 사전식으로 최소화.
    제약: 병원·자원별 남은 여유(치료 중 + 확정 도착 예정 제외). 조합 수가 limit를 넘으면 R2 순차 배정으로 대체한다.
    CP-SAT 솔버(solver_improved)로 바꿀 수 있는 자리이며, 현재는 작은 묶음의 정확 열거다.
    """
    code = 'R3'
    joint = True

    def __init__(self, limit=20000, fallback=None):
        self.limit, self.fallback = limit, fallback or CompetitionAwareRule()
        self.fallbacks = 0

    def choose(self, sim, p, options, t):        # 이송 중 대체 병원 선택 등 단일 결정
        return NearestArrivalRule().choose(sim, p, options, t)

    def decide_batch(self, sim, ready, t):
        """ready: [(patient, options)]. 반환: {patient_id: hospital_id 또는 None}."""
        ready = sorted(ready, key=lambda x: (-x[0].priority, x[0].t_req, x[0].id))
        sizes = [len(opts) + 1 for _, opts in ready]
        if math.prod(sizes) > self.limit:
            self.fallbacks += 1
            return self._greedy(sim, ready, t)
        free = {(h.id, r): h.free(r) for h in sim.hospitals.values() for r in h.cap}
        travel = {(p.id, hid): sim.travel(p, hid, sim.position(p, t)) for p, opts in ready for hid in opts}
        best, best_key = None, None
        for combo in product(*[list(opts) + [None] for _, opts in ready]):
            use = Counter()
            for (p, _), hid in zip(ready, combo):
                if hid is not None:
                    for r, q in p.demand.items():
                        use[(hid, r)] += q
            if any(use[k] > free.get(k, 0) for k in use):
                continue
            key = (sum(p.priority for (p, _), hid in zip(ready, combo) if hid is None),
                   sum(p.priority * travel[(p.id, hid)] for (p, _), hid in zip(ready, combo) if hid is not None))
            if best_key is None or key < best_key:
                best, best_key = combo, key
        return {p.id: hid for (p, _), hid in zip(ready, best)}

    def _greedy(self, sim, ready, t):
        out, taken = {}, Counter()
        for p, opts in ready:
            ok = [hid for hid in opts if all(sim.hospitals[hid].free(r) - taken[(hid, r)] >= q for r, q in p.demand.items())]
            hid = self.fallback.choose(sim, p, ok, t) if ok else None
            out[p.id] = hid
            if hid:
                for r, q in p.demand.items():
                    taken[(hid, r)] += q
        return out


RULES = {'R0': FirstAcceptRule, 'R1': NearestArrivalRule, 'R2': CompetitionAwareRule,
         'R3': JointAssignmentRule, 'R4': PriorityJointAssignmentRule, 'C1': CPSATAssignmentRule}


class Policy:
    """배정 규칙 × 문의 정책 × 조건 변경 대응의 조합.

    k: 동시 문의 수 / wait: 첫 유효 수용 뒤, 남은 문의 응답을 기다리는 최대 분(0이면 즉시 확정)
    near_minutes: 처음에는 예상 이동 이 시간 이내 병원만 문의(None이면 전체)
    expand_after: 요청 후 이 분이 지나면 반경 밖까지 확대(None이면 반경 안 후보가 소진될 때만)
    notify: 돌발 유입·장비 사용 불가로 확정 수요가 여유를 넘으면 영향 환자만 대체 병원 문의
    priority_order: none(기존) / requests(같은 분 신규 문의) / all(현재 발생 환자의 회신·재문의 포함)
    high_priority_hedge_after: 관측형 문의의 실험 우선순위4 최소 추가 검토시간만 변경
    """

    def __init__(self, rule='R1', k=3, wait=0, near_minutes=None, expand_after=None, notify=True, name=None,
                 inquiry_ranking='travel', load_weight=0, load_window=6, adaptive_inquiry=None,
                 observed_inquiry=None, priority_order='none', high_priority_hedge_after=None, **rule_args):
        if inquiry_ranking not in ('travel', 'recent_load'):
            raise ValueError('inquiry_ranking은 travel 또는 recent_load여야 합니다.')
        if not math.isfinite(load_weight) or load_weight < 0:
            raise ValueError('load_weight는 유한한 0 이상의 값이어야 합니다.')
        if not isinstance(load_window, int) or load_window < 1:
            raise ValueError('load_window는 양의 정수여야 합니다.')
        if adaptive_inquiry is not None and observed_inquiry is not None:
            raise ValueError('adaptive_inquiry와 observed_inquiry는 함께 사용할 수 없습니다.')
        if priority_order not in ('none', 'requests', 'all'):
            raise ValueError('priority_order는 none, requests 또는 all이어야 합니다.')
        if high_priority_hedge_after is not None:
            if observed_inquiry is None or isinstance(high_priority_hedge_after, bool) \
                    or not isinstance(high_priority_hedge_after, (int, float)) \
                    or not math.isfinite(high_priority_hedge_after) or high_priority_hedge_after <= 0:
                raise ValueError('high_priority_hedge_after는 관측형 정책의 양수여야 합니다.')
        self.rule = RULES[rule](**rule_args) if isinstance(rule, str) else rule
        self.batch_size, self.wait, self.notify = k, wait, notify
        self.near_minutes, self.expand_after = near_minutes, expand_after
        self.inquiry_ranking, self.load_weight, self.load_window = inquiry_ranking, load_weight, load_window
        self.priority_order = priority_order
        self.high_priority_hedge_after = high_priority_hedge_after
        self.adaptive_inquiry = (validate_adaptive_config(adaptive_inquiry, max_k=k)
                                 if adaptive_inquiry is not None else None)
        self.observed_inquiry = (validate_observed_config(observed_inquiry, max_k=k)
                                if observed_inquiry is not None else None)
        self.joint = getattr(self.rule, 'joint', False)
        self.name = name or (f"{self.rule.code}_k{k}_w{wait}" + (f"_r{near_minutes}" if near_minutes else '')
                             + (f"_x{expand_after}" if expand_after else '') + ('' if notify else '_nonotify'))

    def choose(self, sim, p, options, t):
        return self.rule.choose(sim, p, options, t)

    def spec(self):
        result = dict(name=self.name, rule=self.rule.code, k=self.batch_size, wait=self.wait,
                    near_minutes=self.near_minutes, expand_after=self.expand_after, notify=self.notify,
                    inquiry_ranking=self.inquiry_ranking, load_weight=self.load_weight, load_window=self.load_window,
                    adaptive_inquiry=dict(self.adaptive_inquiry) if self.adaptive_inquiry is not None else None,
                    observed_inquiry=dict(self.observed_inquiry) if self.observed_inquiry is not None else None)
        # Preserve saved legacy development locks and policy JSON exactly.
        if self.priority_order != 'none':
            result['priority_order'] = self.priority_order
        if self.high_priority_hedge_after is not None:
            result['high_priority_hedge_after'] = self.high_priority_hedge_after
        return result


# ---------------------------------------------------------------- 시뮬레이터

class Simulation:
    def __init__(self, seed, policy, cfg=CONFIG, world=None, record=False, response_fn=None, outages=None):
        self.seed, self.policy, self.cfg = seed, policy, cfg
        hs, ps, self.walkins = make_world(seed, cfg) if world is None else world
        self.hospitals = {h.id: h for h in hs}
        self.patients = {p.id: p for p in ps}
        self.pending = sorted(ps, key=lambda p: p.t_req)
        self.outages = make_outages(seed, cfg, hs) if outages is None else list(outages)
        self.response_fn = response_fn       # 시연·시험용 응답 주입: (p, hid, attempt) -> (silent, delay)
        self.record, self.log = record, []
        self.hosp_asks = Counter()
        self.decision_seconds, self.decisions = 0.0, 0
        self.ready = []
        self.notifications = 0
        self.inquiry_history = {hid: [] for hid in self.hospitals}
        # Response scheduling remains private. The controller receives only
        # feedback whose delivery/deadline has already occurred, and submitted
        # timestamps for unresolved requests. Clearing a patient's assignment
        # requests does not cancel hospital review work or its later feedback.
        self._inquiry_feedback_pending = []
        self.inquiry_observations = []
        self.adaptive_decisions = []
        self.observed_decisions = []
        self._observed_review_at = {}

    def _feedback_enabled(self):
        return (getattr(self.policy, 'adaptive_inquiry', None) is not None
                or getattr(self.policy, 'observed_inquiry', None) is not None)

    def observed_inquiry_plan(self, p, candidates, loc, t):
        # Inquiry entries carry private response schedules. Project only their
        # submitted time and known route estimate, never at/silent/finish time.
        pending = [dict(submitted_t=e['submitted_t'], hospital=e['hospital'], patient=e['patient'])
                   for e in self._inquiry_feedback_pending if e['submitted_t'] <= t]
        own = [dict(submitted_t=a['submitted_t'], hospital=a['hid'], patient=p.id,
                    travel_minutes=self.travel(p, a['hid'], loc)) for a in p.asks]
        available = [dict(hospital=hid, travel_minutes=self.travel(p, hid, loc)) for hid in candidates]
        config = dict(self.policy.observed_inquiry)
        high_hedge = getattr(self.policy, 'high_priority_hedge_after', None)
        if p.priority == 4 and high_hedge is not None:
            config['hedge_after'] = high_hedge
        decision = plan_inquiries(config, t=t, candidates=available,
            observations=self.inquiry_observations, pending=pending, patient_pending=own,
            max_k=self.policy.batch_size)
        send = decision['send']
        # A planner cannot bypass candidate filtering or the concurrency cap.
        if len(set(send)) != len(send) or not set(send) <= set(candidates) \
                or len(send) + len(p.asks) > self.policy.batch_size:
            raise ValueError('문의 계획이 후보 또는 동시 문의 한도를 벗어났습니다.')
        event = dict(t=t, patient=p.id, policy_kind='observed',
                     limit=len(p.asks) + len(send), open_requests=len(p.asks), **decision)
        if getattr(self.policy, 'priority_order', 'none') != 'none' or high_hedge is not None:
            event['priority_order'] = getattr(self.policy, 'priority_order', 'none')
            event['patient_priority'] = p.priority
            event['effective_hedge_after'] = config['hedge_after']
        self.observed_decisions.append(event)
        self.ev(t, 'inquiry_policy', **{k: v for k, v in event.items() if k != 't'})
        return send

    def _receive_inquiry_feedback(self, t):
        remaining = []
        for entry in self._inquiry_feedback_pending:
            if entry['due_t'] > t:
                remaining.append(entry)
                continue
            observation = dict(submitted_t=entry['submitted_t'], observed_t=entry['due_t'],
                               hospital=entry['hospital'], patient=entry['patient'],
                               outcome='timeout' if entry['silent'] else 'reply')
            self.inquiry_observations.append(observation)
            self.ev(entry['due_t'], 'inquiry_feedback', **observation)
        self._inquiry_feedback_pending = remaining

    def inquiry_limit(self, p, t):
        config = getattr(self.policy, 'adaptive_inquiry', None)
        if config is None:
            return self.policy.batch_size
        # Explicit data boundary: no due_t, silent, queue jobs, worker counts,
        # not-yet-born patients, or future events reach the decision function.
        pending = [dict(submitted_t=e['submitted_t'], hospital=e['hospital'], patient=e['patient'])
                   for e in self._inquiry_feedback_pending if e['submitted_t'] <= t]
        decision = decide_inquiry_limit(config, t=t, observations=self.inquiry_observations,
                                       pending=pending, max_k=self.policy.batch_size)
        event = dict(t=t, patient=p.id, **decision)
        self.adaptive_decisions.append(event)
        self.ev(t, 'inquiry_policy', patient=p.id, **decision)
        return decision['limit']

    def before_tick(self, t):
        """Optional environment hook; policies must not receive future schedules."""

    def after_tick(self, t):
        """Optional trace hook after this minute's decisions and arrivals."""

    def schedule_ask(self, p, hid, attempt, t):
        """Default keyed response; replay can add an opt-in review queue."""
        if self.response_fn:
            silent, delay = self.response_fn(p, hid, attempt)
        else:
            r = rng_for(self.seed, 'response', p.id, hid, attempt)
            silent = r.random() < self.cfg['nonresponse']
            delay = max(1, round(r.lognormvariate(math.log(self.cfg['resp_median']), self.cfg['resp_sigma'])))
        return dict(hid=hid, at=t + (self.cfg['timeout'] if silent else delay), silent=silent)

    def rank_inquiry_candidates(self, p, loc, candidates, t):
        if getattr(self.policy, 'inquiry_ranking', 'travel') != 'recent_load':
            return candidates
        # Only timestamps of requests already submitted by this system are visible.
        # No queue finish times, background-work schedule or future response is used.
        window, weight = self.policy.load_window, self.policy.load_weight
        def score(hid):
            recent = sum(t - window < sent <= t for sent in self.inquiry_history[hid])
            return self.travel(p, hid, loc) + weight * recent, self.travel(p, hid, loc), hid
        return sorted(candidates, key=score)

    # ---- 관측 가능한 정보
    def visible_patients(self, t):
        return [q for q in self.patients.values() if q.t_req <= t]

    def ev(self, t, type_, **data):
        if self.record:
            self.log.append(dict(t=t, type=type_, **data))

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

    def _pool(self, p, loc, cands):
        near = getattr(self.policy, 'near_minutes', None)
        if near is None or p.expanded:
            return cands
        return [hid for hid in cands if self.travel(p, hid, loc) <= near]

    def send_asks(self, p, t, exclude=()):
        # A valid offer is waiting for this minute's joint decision. Do not
        # enqueue work that would be immediately abandoned by that commitment.
        # resolve_batch clears ready first, so an unassigned patient can then
        # continue searching normally.
        if any(ready_patient is p for ready_patient, _ in self.ready):
            return
        observed = getattr(self.policy, 'observed_inquiry', None) is not None
        if observed:
            if p.retry_at is not None and p.retry_at > t:
                return
            if self._observed_review_at.get(p.id) == t:
                return
            # Feedback is delivered before environment hooks, but resolve_asks
            # consumes the patient's corresponding entries later in the tick.
            # Defer an early hook until those received replies are processed.
            # This uses ledger membership, never the hidden response schedule.
            unresolved = {(e['patient'], e['hospital'], e['submitted_t'])
                          for e in self._inquiry_feedback_pending}
            if any((p.id, a['hid'], a['submitted_t']) not in unresolved for a in p.asks):
                return
            self._observed_review_at[p.id] = t
        loc = self.position(p, t)
        open_ = {a['hid'] for a in p.asks}
        every = self.candidates(p, loc, exclude)
        cands = [h for h in self._pool(p, loc, every) if h not in p.tried and h not in open_ and h not in p.valid]
        if not cands and not p.asks and getattr(self.policy, 'near_minutes', None) is not None and not p.expanded \
                and any(h not in p.tried for h in every):
            p.expanded = True                 # 반경 안 후보를 다 썼으면 범위를 넓힌다
            self.ev(t, 'expand', patient=p.id, reason='pool_exhausted')
            cands = [h for h in every if h not in p.tried and h not in open_ and h not in p.valid]
        if not cands and not p.asks:
            if not every:
                p.no_compatible = p.state == 'SEARCH'
                if p.no_compatible:
                    p.state = 'NO_COMPATIBLE'
                    self.ev(t, 'no_compatible', patient=p.id)
                return
            if p.valid:
                return
            p.tried.clear()        # 다 돌았으면 잠시 뒤 처음부터 다시 문의
            p.retry_at = t + self.cfg['retry_wait']
            return
        cands = self.rank_inquiry_candidates(p, loc, cands, t)
        if observed:
            selected = self.observed_inquiry_plan(p, cands, loc, t)
        else:
            limit = self.inquiry_limit(p, t) if cands else self.policy.batch_size
            selected = cands[:max(0, limit - len(p.asks))]
        for hid in selected:
            n = p.attempts.get(hid, 0) + 1
            p.attempts[hid] = n
            scheduled = self.schedule_ask(p, hid, n, t)
            if observed:
                scheduled['submitted_t'] = t
            p.asks.append(scheduled)
            if self._feedback_enabled():
                self._inquiry_feedback_pending.append(dict(submitted_t=t, due_t=scheduled['at'],
                    hospital=hid, patient=p.id, silent=scheduled['silent']))
            self.inquiry_history[hid].append(t)
            p.tried.add(hid)
            p.inquiries += 1
            self.hosp_asks[hid] += 1
            self.ev(t, 'ask', patient=p.id, hospital=hid, attempt=n)

    def commit(self, p, hid, t, redirect=False):
        loc = self.position(p, t)
        if redirect:
            self.hospitals[p.dest].committed.pop(p.id, None)
            p.redirects += 1
            self.ev(t, 'redirect', patient=p.id, hospital=hid, previous=p.dest)
        p.state, p.dest, p.origin, p.depart_t = 'TRANSIT', hid, loc, t
        p.arrive_t = t + self.travel(p, hid, loc, leg=p.redirects)
        p.first_arrive_plan = p.first_arrive_plan or p.arrive_t
        p.t_commit = p.t_commit if p.t_commit is not None else t
        p.asks, p.notified = [], False
        p.valid, p.first_valid_t = {}, None
        self.hospitals[hid].committed[p.id] = p.demand
        self.ev(t, 'commit', patient=p.id, hospital=hid, eta=p.arrive_t)

    def _decide(self, p, options, t):
        started = time.perf_counter()
        hid = self.policy.choose(self, p, options, t)
        self.decision_seconds += time.perf_counter() - started
        self.decisions += 1
        return hid

    def resolve_asks(self, p, t):
        due = [a for a in p.asks if a['at'] == t]
        if not due and not p.valid:
            return                    # 이번 분에 바뀐 것이 없다 (재시도 대기·미발생 환자 포함)
        changed = bool(due)
        if due:
            p.asks = [a for a in p.asks if a['at'] != t]
            for a in due:
                if a['silent']:
                    self.ev(t, 'no_response', patient=p.id, hospital=a['hid'])
                elif self.hospitals[a['hid']].can_accept(p):
                    p.valid[a['hid']] = t + self.cfg.get('accept_ttl', 10)
                    p.accepted_at[a['hid']] = t
                    p.first_valid_t = p.first_valid_t if p.first_valid_t is not None else t
                    self.ev(t, 'accept', patient=p.id, hospital=a['hid'])
                else:
                    self.ev(t, 'decline', patient=p.id, hospital=a['hid'])
        for hid in [h for h, exp in p.valid.items() if exp <= t]:
            del p.valid[hid]
            changed = True
            self.ev(t, 'expire', patient=p.id, hospital=hid)
        if not p.valid:
            p.first_valid_t = None
        elif due or getattr(self.policy, 'wait', 0) or getattr(self.policy, 'joint', False):
            wait = getattr(self.policy, 'wait', 0)
            if p.state == 'TRANSIT' or wait == 0 or not p.asks or t - p.first_valid_t >= wait:
                options = [hid for hid in p.valid if self.hospitals[hid].can_accept(p)]   # 확정 직전 재확인
                for hid in set(p.valid) - set(options):
                    del p.valid[hid]
                    changed = True
                    self.ev(t, 'recheck_failed', patient=p.id, hospital=hid)
                if options and getattr(self.policy, 'joint', False) and p.state == 'SEARCH':
                    self.ready.append((p, options))
                    return
                if options:
                    hid = self._decide(p, options, t)
                    if hid is not None:
                        self.commit(p, hid, t, redirect=p.state == 'TRANSIT')
                        return
                if not p.valid:
                    p.first_valid_t = None
        if changed and p.state == 'SEARCH' and not p.asks and not p.valid:
            self.send_asks(p, t)

    def resolve_batch(self, t):
        if not self.ready:
            return
        ready, self.ready = self.ready, []
        started = time.perf_counter()
        plan = self.policy.rule.decide_batch(self, ready, t)
        self.decision_seconds += time.perf_counter() - started
        self.decisions += 1
        for p, _ in ready:
            hid = plan.get(p.id)
            if hid is not None and self.hospitals[hid].can_accept(p):
                self.commit(p, hid, t)
            elif not p.asks:
                self.send_asks(p, t)      # 묶음 안에서 자리를 못 받으면 남은 유효 응답을 들고 계속 탐색

    def notify_overflow(self, h, t, cause='external_arrival'):
        """확정 수요가 여유를 넘으면, 부족한 자원이 필요한 환자 중 늦게 도착할 환자부터 알리고 대체 병원 문의를 시작한다."""
        for r in h.cap:
            over = h.used(r) + h.expected(r) + h.waiting(r) - h.cap[r]
            for pid in sorted(h.committed, key=lambda x: -self.patients[x].arrive_t):
                if over <= 0:
                    break
                p = self.patients[pid]
                if p.notified or p.demand.get(r, 0) == 0 or p.arrive_t <= t:
                    continue
                p.notified = True
                over -= p.demand[r]
                self.notifications += 1
                self.ev(t, 'notify', patient=p.id, hospital=h.id, resource=r, cause=cause)
                self.send_asks(p, t, exclude={h.id})

    def start_treatments(self, h, t):
        h.queue.sort(key=lambda p: (-p.priority, p.queued_t))
        for p in list(h.queue):
            if h.fits_now(p.demand):
                h.queue.remove(p)
                h.active.append([t + p.duration, p.demand])
                p.state, p.t_start = 'TREATING', t
                self.ev(t, 'treatment_start', patient=p.id, hospital=h.id, external=p.external)

    def run(self):
        end = self.cfg['horizon'] + self.cfg['tail']
        walk = list(self.walkins)
        outages = list(self.outages)
        restores = []
        for t in range(end):
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

    def metrics(self):
        ps = list(self.patients.values())
        out = dict(policy=self.policy.name, seed=self.seed, patients=len(ps))
        treated = [p for p in ps if p.t_start is not None]
        waits = [p.t_start - p.t_req for p in treated]
        commits = [p.t_commit - p.t_req for p in ps if p.t_commit is not None]
        arrivals = [p.final_arrive_t - p.t_req for p in ps if p.final_arrive_t is not None]
        out.update(treated=len(treated), untreated=len(ps) - len(treated),
                   no_compatible=sum(p.no_compatible for p in ps),
                   mean_wait=statistics.mean(waits) if waits else None,
                   median_wait=statistics.median(waits) if waits else None,
                   p90_wait=sorted(waits)[int(0.9 * (len(waits) - 1))] if waits else None,
                   inquiries=sum(p.inquiries for p in ps),
                   redirects=sum(p.redirects for p in ps),
                   time_to_commit=statistics.mean(commits or [0]),
                   extra_travel=sum(p.arrive_t - p.first_arrive_plan for p in ps if p.redirects))
        out.update(unassigned=sum(p.t_commit is None for p in ps),
                   not_arrived=sum(p.final_arrive_t is None for p in ps),
                   commit_median=statistics.median(commits) if commits else None,
                   commit_p95=p95(commits),
                   arrive_mean=statistics.mean(arrivals) if arrivals else None,
                   arrive_median=statistics.median(arrivals) if arrivals else None,
                   arrive_p95=p95(arrivals),
                   wait_p95=p95(waits),
                   inquiries_per_patient=sum(p.inquiries for p in ps) / len(ps) if ps else 0,
                   max_hospital_inquiries=max(self.hosp_asks.values(), default=0),
                   retries=sum(max(0, n - 1) for p in ps for n in p.attempts.values()),
                   notifications=self.notifications,
                   outages=len(self.outages),
                   decisions=self.decisions,
                   decision_ms_mean=1000 * self.decision_seconds / self.decisions if self.decisions else 0.0,
                   joint_fallbacks=getattr(getattr(self.policy, 'rule', None), 'fallbacks', 0))
        if getattr(self.policy, 'adaptive_inquiry', None) is not None:
            limits = [d['limit'] for d in self.adaptive_decisions]
            out.update(adaptive_decisions=len(limits),
                       adaptive_mean_limit=statistics.mean(limits) if limits else None,
                       adaptive_min_limit=min(limits, default=None),
                       adaptive_max_limit=max(limits, default=None),
                       adaptive_feedback_received=len(self.inquiry_observations))
        if getattr(self.policy, 'observed_inquiry', None) is not None:
            out.update(observed_decisions=len(self.observed_decisions),
                       observed_dispatch_reviews=sum(bool(d['send']) for d in self.observed_decisions),
                       observed_hedges=sum(bool(d['send']) and d['open_requests'] > 0
                                           for d in self.observed_decisions),
                       observed_sent=sum(len(d['send']) for d in self.observed_decisions),
                       observed_feedback_received=len(self.inquiry_observations),
                       observed_max_in_flight=max((d['limit'] for d in self.observed_decisions), default=0))
        for g in (4, 2, 1):
            grp = [p for p in ps if p.priority == g]
            gw = [p.t_start - p.t_req for p in grp if p.t_start is not None]
            out[f'g{g}'] = dict(total=len(grp), untreated=sum(p.t_start is None for p in grp),
                                over_allowance=sum(p.t_start is None or p.t_start - p.t_req > self.cfg['allowance'][g]
                                                   for p in grp),
                                mean_wait=statistics.mean(gw) if gw else None,
                                max_wait=max(gw) if gw else None)
        return out
