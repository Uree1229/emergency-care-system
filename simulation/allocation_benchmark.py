"""같은 관측 배정 입력에서 R0/R1/R2/R3와 CP-SAT C1을 비교한다.

실행: python -m simulation.allocation_benchmark --phase development
설정 잠금: --lock / 검증: --phase validation --lock-file <개발 잠금 JSON>
문의·회신·이송 과정의 성능은 이 고정 스냅샷 실험에서 측정하지 않는다.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace

from .allocation_scenarios import build_allocation_snapshots, validate_allocation_snapshot, FAMILIES
from .cp_sat_assignment import CPSATAssignmentRule
from .sim import FirstAcceptRule, NearestArrivalRule, CompetitionAwareRule, JointAssignmentRule

ROOT = Path(__file__).resolve().parents[1]
DEV_SEEDS = tuple(range(8101, 8109))
VALIDATION_SEEDS = tuple(range(9101, 9121))
POLICIES = ('R0', 'R1', 'R2', 'R3', 'C1')
PRIORITY_WEIGHTS = dict(high=4, medium=2, low=1)
SETTINGS = dict(at=10, r2_lambda=15.0, r3_enumeration_limit=20000,
                cp_sat_seconds=1.0, cp_sat_seed=0, cp_sat_workers=1,
                objective=['unassigned_priority_weight', 'priority_weighted_travel_minutes'],
                priority_weights=PRIORITY_WEIGHTS,
                order='priority descending, request time ascending, patient ID ascending',
                inquiry='fixed frozen valid offers; inquiry/search time not simulated')
SOURCE_FILES = ('simulation/allocation_benchmark.py', 'simulation/allocation_scenarios.py',
                'simulation/cp_sat_assignment.py', 'simulation/sim.py', 'simulation/priority_assignment.py',
                'simulation/allocation_benchmark_audit.py')
LIMITATIONS = [
    '전부 합성된 같은 시각의 환자·유효 수용 응답·자원·예상 이동시간을 비교한다. 현행 시스템 재현이 아니다.',
    '모든 정책에 완전한 현재 자원 정보가 있다고 가정한다. 실제 병원 데이터의 누락·지연 효과를 검증하지 않는다.',
    '문의가 끝난 배정 스냅샷만 다루므로 병원 선정시간, 병원 응답 부담, 도착·치료·전원 시간의 개선율을 계산하지 않는다.',
    '우선순위 4·2·1은 실험 가중치이며 임상 기준이 아니다. 목적은 미배정 인원이 아니라 미배정 우선순위 합을 먼저 줄이는 것이다.',
    'R3와 C1의 목적은 같다. C1의 최적성 증명은 해당 시각의 입력·목적에만 적용하며 전체 미래 이송의 전역 최적값이 아니다.',
    'R0/R1/R2의 순서는 모든 사례에서 고정한다. 임의 환자 처리 순서로 결과가 바뀔 수 있다.',
    '이동시간 평균은 배정된 환자만 포함한다. 미배정 수·우선순위별 결과·양쪽 공통 배정 환자 차이와 함께 해석한다.',
    '검증은 사전 정의한 같은 유형의 새 seed다. 실제 의료기관이나 새로운 사례 유형으로의 일반화를 증명하지 않는다.',
]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sources():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


class ObservedHospital:
    def __init__(self, data):
        self.id = data['id']; self.cap = dict(data['capacity'])
        self.caps = set(data.get('capabilities', []))
        self.occupied = dict(data.get('occupied', {}))
        self.committed = dict(data.get('committed', {}))
        self.queued = dict(data.get('queued', {}))
        self.taken = Counter()

    def free(self, resource):
        return (self.cap.get(resource, 0) - self.occupied.get(resource, 0) -
                self.committed.get(resource, 0) - self.queued.get(resource, 0) - self.taken[resource])

    def can_accept(self, patient):
        return patient.caps <= self.caps and all(self.free(r) >= q for r, q in patient.demand.items())


class ObservedWorld:
    """현재 정보만 노출한다. 원본 시나리오·미래 사건·회신 스케줄은 없다."""
    def __init__(self, snapshot):
        self.hospitals = {h['id']: ObservedHospital(h) for h in snapshot['hospitals']}
        self.patients = [SimpleNamespace(id=p['id'], t_req=p['t_req'], loc=(p['lat'], p['lon']),
                            caps=set(p.get('required_capabilities', [])),
                            demand={r:q for r,q in p['resource_demands'].items() if q},
                            priority=PRIORITY_WEIGHTS[p['priority']], state='SEARCH', accepted_at=dict(p['accepted_at']))
                         for p in snapshot['patients']]
        self.travel_times = {(p['id'], hid): value for p in snapshot['patients']
                             for hid, value in p['travel_minutes'].items()}
        offers = {p['id']: list(p['accepted_hospitals']) for p in snapshot['patients']}
        self.ready = [(p, [hid for hid in offers[p.id] if self.hospitals[hid].can_accept(p)]) for p in self.patients]

    def position(self, patient, at):
        return patient.loc

    def travel(self, patient, hospital_id, origin):
        return self.travel_times[patient.id, hospital_id]

    def visible_patients(self, at):
        return [p for p in self.patients if p.t_req <= at]


def audit_assignment(snapshot, plan):
    world = ObservedWorld(snapshot)
    offered = {p.id: set(options) for p, options in world.ready}
    if set(plan) != set(offered):
        raise ValueError('배정 결과에 환자 누락 또는 알 수 없는 환자가 있습니다.')
    consumed = Counter()
    for patient in world.patients:
        hid = plan[patient.id]
        if hid is None:
            continue
        if hid not in offered[patient.id]:
            raise ValueError('유효한 수용 응답 밖의 배정입니다.')
        for resource, quantity in patient.demand.items():
            if quantity:
                consumed[hid, resource] += quantity
    if any(q > world.hospitals[hid].free(resource) for (hid, resource), q in consumed.items()):
        raise ValueError('현재 자원 여유를 초과한 배정입니다.')
    return True


def metrics(snapshot, plan):
    assigned = [p for p in snapshot['patients'] if plan[p['id']] is not None]
    unassigned = [p for p in snapshot['patients'] if plan[p['id']] is None]
    times = [p['travel_minutes'][plan[p['id']]] for p in assigned]
    return dict(patient_count=len(snapshot['patients']), assigned_count=len(assigned),
                unassigned_count=len(unassigned),
                unassigned_priority_weight=sum(PRIORITY_WEIGHTS[p['priority']] for p in unassigned),
                priority_weighted_travel_minutes=sum(PRIORITY_WEIGHTS[p['priority']] * p['travel_minutes'][plan[p['id']]] for p in assigned),
                unassigned_by_priority={str(tier):sum(PRIORITY_WEIGHTS[p['priority']] == tier for p in unassigned) for tier in (4, 2, 1)},
                travel_denominator=len(times), travel_mean_minutes=statistics.mean(times) if times else None,
                travel_max_minutes=max(times) if times else None,
                travel_p95_minutes=sorted(times)[math.ceil(.95 * len(times)) - 1] if times else None,
                constraint_violations=0)


def run_policy(snapshot, code):
    world = ObservedWorld(snapshot)
    constructors = dict(R0=FirstAcceptRule, R1=NearestArrivalRule,
                        R2=lambda: CompetitionAwareRule(lam=SETTINGS['r2_lambda']),
                        R3=lambda: JointAssignmentRule(limit=SETTINGS['r3_enumeration_limit']),
                        C1=lambda: CPSATAssignmentRule(time_limit_seconds=SETTINGS['cp_sat_seconds'],
                                                      seed=SETTINGS['cp_sat_seed'], require_backend=True))
    rule = constructors[code]()
    started = time.perf_counter()
    if getattr(rule, 'joint', False):
        plan = rule.decide_batch(world, world.ready, snapshot['at'])
    else:
        plan = {}
        for p, options in sorted(world.ready, key=lambda row: (-row[0].priority, row[0].t_req, row[0].id)):
            feasible = [hid for hid in options if world.hospitals[hid].can_accept(p)]
            hid = rule.choose(world, p, feasible, snapshot['at']) if feasible else None
            plan[p.id] = hid
            if hid is not None:
                world.hospitals[hid].taken.update(p.demand)
                p.state = 'CONFIRMED'
    wall = time.perf_counter() - started
    audit_assignment(snapshot, plan)
    return dict(policy=code, assignments=plan, metrics=metrics(snapshot, plan), wall_seconds=wall,
                fallbacks=getattr(rule, 'fallbacks', 0), solver_records=deepcopy(getattr(rule, 'records', [])),
                input_sha256=digest(snapshot))


def paired_common_travel(snapshot, baseline, comparison):
    shared = [p for p in snapshot['patients'] if baseline['assignments'][p['id']] is not None
              and comparison['assignments'][p['id']] is not None]
    values = [p['travel_minutes'][comparison['assignments'][p['id']]] -
              p['travel_minutes'][baseline['assignments'][p['id']]] for p in shared]
    return dict(denominator=len(values), mean_difference_minutes=statistics.mean(values) if values else None,
                total_difference_minutes=sum(values), comparison_only=sum(
                    baseline['assignments'][p['id']] is None and comparison['assignments'][p['id']] is not None
                    for p in snapshot['patients']), baseline_only=sum(
                    baseline['assignments'][p['id']] is not None and comparison['assignments'][p['id']] is None
                    for p in snapshot['patients']))


def summarize(results):
    summary = []
    for family in FAMILIES:
        rows = [r for r in results if r['snapshot']['family'] == family]
        policies = {}
        for code in POLICIES:
            branches = [next(b for b in r['branches'] if b['policy'] == code) for r in rows]
            policies[code] = dict(cases=len(branches),
                mean_unassigned_count=statistics.mean(b['metrics']['unassigned_count'] for b in branches),
                mean_unassigned_priority_weight=statistics.mean(b['metrics']['unassigned_priority_weight'] for b in branches),
                mean_priority_weighted_travel_minutes=statistics.mean(b['metrics']['priority_weighted_travel_minutes'] for b in branches),
                fallbacks=sum(b['fallbacks'] for b in branches),
                wall_median_seconds=statistics.median(b['wall_seconds'] for b in branches),
                wall_p95_seconds=sorted(b['wall_seconds'] for b in branches)[math.ceil(.95 * len(branches))-1],
                wall_max_seconds=max(b['wall_seconds'] for b in branches))
        better, equal, worse = 0, 0, 0
        for r in rows:
            base = next(b for b in r['branches'] if b['policy'] == 'R3')['metrics']
            cp = next(b for b in r['branches'] if b['policy'] == 'C1')['metrics']
            key = lambda m:(m['unassigned_priority_weight'], m['priority_weighted_travel_minutes'])
            better += key(cp) < key(base); equal += key(cp) == key(base); worse += key(cp) > key(base)
        summary.append(dict(family=family, policies=policies, cp_vs_r3=dict(better=better,equal=equal,worse=worse)))
    return summary


def manifest():
    from ortools import __version__ as ortools_version
    return dict(schema_version=1, kind='allocation_benchmark_lock', settings=SETTINGS,
                families=list(FAMILIES), development_seeds=list(DEV_SEEDS), validation_seeds=list(VALIDATION_SEEDS),
                runtime_versions=dict(python=sys.version.split()[0], ortools=ortools_version),
                source_sha256=sources())


def verify_lock(lock):
    actual = manifest()
    if lock != actual:
        raise ValueError('개발 잠금과 현재 설정·소스·검증 seed가 다릅니다. 검증 실행을 중단합니다.')


def run(phase, lock=None):
    if phase not in ('development', 'validation'):
        raise ValueError('phase must be development or validation')
    if phase == 'validation':
        verify_lock(lock)
    # Load the optional backend before timing policies. Its first import cost
    # is an application startup cost, not an allocation algorithm difference.
    from ortools import __version__ as ortools_version
    from ortools.sat.python import cp_model  # noqa: F401
    seeds = DEV_SEEDS if phase == 'development' else VALIDATION_SEEDS
    results = []
    for seed in seeds:
        for snapshot in build_allocation_snapshots(seed):
            validate_allocation_snapshot(snapshot)
            branches = [run_policy(snapshot, code) for code in POLICIES]
            results.append(dict(snapshot=snapshot, branches=branches,
                common_travel_vs_r1={b['policy']: paired_common_travel(snapshot, branches[1], b) for b in branches},
                combinations=math.prod(len(options) + 1 for _, options in ObservedWorld(snapshot).ready)))
    return dict(schema_version=1, kind='frozen_allocation_benchmark', phase=phase, synthetic=True,
                settings=SETTINGS, families=list(FAMILIES), seeds=list(seeds), limitations=LIMITATIONS,
                runtime=dict(python=sys.version, ortools=ortools_version, solver_import='loaded before branch timing'),
                runtime_versions=dict(python=sys.version.split()[0], ortools=ortools_version),
                source_sha256=sources(), lock_sha256=digest(lock) if lock else None,
                results=results, summary=summarize(results))


def markdown(report):
    lines = ['# 고정 입력의 배정 알고리즘 비교', '', f"단계: {report['phase']} · 사례 {len(report['results'])}개 × 규칙5종. 모든 값은 합성 실험이다.", '',
             '같은 시점의 유효 수용 응답·현재 자원·예상 이동시간을 복사해 비교했다. 병원 선정시간이나 실제 도착시간의 개선율이 아니다.', '',
             '목적: **미배정 우선순위 합 → 배정 환자의 우선순위 가중 이동시간 합** 사전식 최소화. R3와 C1은 같은 목적이다.', '',
             '| 조건 | 규칙 | 평균 미배정 인원 | 평균 미배정 우선순위 합 | 평균 가중 이동분 합 | 대체 실행 수 | 계산 중앙값(ms) |',
             '|---|---|---:|---:|---:|---:|---:|']
    for s in report['summary']:
        for code,p in s['policies'].items():
            lines.append(f"| {s['family']} | {code} | {p['mean_unassigned_count']:.2f} | {p['mean_unassigned_priority_weight']:.2f} | {p['mean_priority_weighted_travel_minutes']:.2f} | {p['fallbacks']} | {p['wall_median_seconds']*1000:.2f} |")
    lines += ['', '## C1과 R3의 목적값 비교', '', '| 조건 | C1 우수 | 동률 | C1 열세 |', '|---|---:|---:|---:|']
    for s in report['summary']:
        p=s['cp_vs_r3']; lines.append(f"| {s['family']} | {p['better']} | {p['equal']} | {p['worse']} |")
    records = [r for result in report['results'] for b in result['branches'] if b['policy']=='C1' for r in b['solver_records']]
    solved=[r for r in records if r['stages']]
    empty=[r for r in records if r['status']=='OPTIMAL_EMPTY']
    lines += ['', f"CP-SAT 결정 기록 {len(records)}개: 실제 솔버 실행 {len(solved)}개 중 두 목적의 최적성 증명 {sum(bool(r.get('optimal_proven')) for r in solved)}개, 유효 배정 후보가 없는 자명한 미배정 {len(empty)}개. 원시 JSON에 단계별 상태·목적·경계값·대체 사유를 저장했다.", '',
              '## 해석 범위', ''] + ['- '+text for text in report['limitations']]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--phase',choices=('development','validation'),default='development')
    parser.add_argument('--lock-file',type=Path,default=ROOT/'config/allocation_benchmark_lock.json')
    parser.add_argument('--lock',action='store_true',help='개발 실행 후 소스·설정·새 검증 seed를 잠근다')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    if args.phase=='validation' and args.lock:
        parser.error('검증 실행에서 설정 잠금을 덮어쓸 수 없습니다.')
    lock=json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase=='validation' else None
    report=run(args.phase,lock)
    output=args.output or ROOT/f'simulation_results/allocation_{args.phase}_20261013.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    output.with_suffix('.md').write_text(markdown(report),encoding='utf-8')
    if args.lock:
        args.lock_file.parent.mkdir(parents=True,exist_ok=True)
        args.lock_file.write_text(json.dumps(manifest(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(output=str(output),phase=args.phase,cases=len(report['results']),branches=len(report['results'])*5),ensure_ascii=False))


if __name__=='__main__':
    main()
