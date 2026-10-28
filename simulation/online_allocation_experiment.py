"""같은 문의 정책·잠재 응답 환경에서 전체 이송 정책을 비교한다.

python -m simulation.online_allocation_experiment --phase development --lock
python -m simulation.online_allocation_experiment --phase validation
python -m simulation.online_allocation_experiment --export-replay <검증 JSON>
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics
import sys

from .online_allocation_engine import run_online_replay, DEFAULT_ONLINE_POLICIES as DEFAULT_POLICIES
from .online_allocation_scenarios import build_online_scenarios, FAMILIES

ROOT=Path(__file__).resolve().parents[1]
DEV_SEEDS=tuple(range(10101,10107))
VALIDATION_SEEDS=tuple(range(11101,11113))
SOURCE_FILES=(
    'simulation/online_allocation_experiment.py','simulation/online_allocation_engine.py',
    'simulation/online_allocation_scenarios.py','simulation/online_allocation_audit.py',
    'simulation/sim.py','simulation/cp_sat_assignment.py','simulation/priority_assignment.py',
    'simulation/map_replay.py','simulation/road_routes.py','simulation/inquiry_queue.py',
    'simulation/adaptive_inquiry.py','simulation/observed_inquiry.py',
    'server/travel.py','server/synthetic.py',
)
SETTINGS=dict(policies=deepcopy(list(DEFAULT_POLICIES)),
              comparison_scope='same_potential_environment_not_identical_offers',
              routing='synthetic straight-line time estimates; no external API',
              simulation_unit='integer minute',
              solver_wall_time='measured separately; not added to simulation clock',
              replay_selection='each family lowest validation seed, independent of results')
LIMITATIONS=[
    '병원·위치·환자·역량·자원·출동·응답·상태 변화·이동·치료시간은 모두 합성 입력이다.',
    '모든 방식에 같은 문의 설정·잠재 회신 환경·외부 사건을 제공한다. 배정 결과로 이후 자원과 실제 회신이 달라질 수 있다.',
    '순차 규칙은 환자별로 확정하고 공동 규칙은 같은 분의 준비 환자를 함께 확정한다. 관측된 수용 응답 목록까지 동일한 실험은 아니다.',
    'R3/C1은 미배정 우선순위 합, 이후 우선순위 가중 이동시간 합을 줄인다. 임상 기준이나 전체 이송 과정 최적성을 뜻하지 않는다.',
    'CP-SAT 최적성·시간 제한은 개별 배정 묶음에만 적용된다. 계산시간은 별도 측정하며 분 단위 모의 시간에 가산하지 않는다.',
    '병원 변경·전원은 모든 분기에 같은 합성 규칙을 사용한다. 실제 서버의 사람 승인 절차나 현행 119 체계를 재현하지 않는다.',
    '조건부 평균은 양쪽 모두 도착/치료한 환자 차이와 미배정·미도착·치료 미시작 수, 전체 환자의 제한 경과시간 평균과 함께 본다.',
    '같은 유형의 새 seed 검증이며 실제 의료기관 또는 새로운 유형으로의 일반화를 입증하지 않는다.',
]


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def source_hashes():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def runtime_versions():
    from ortools import __version__
    return dict(python=sys.version.split()[0],ortools=__version__)


def manifest():
    return dict(schema_version=1,kind='online_allocation_lock',families=list(FAMILIES),
                settings=SETTINGS,development_seeds=list(DEV_SEEDS),validation_seeds=list(VALIDATION_SEEDS),
                runtime_versions=runtime_versions(),source_sha256=source_hashes())


def verify_lock(lock):
    if lock!=manifest():
        raise ValueError('소스·설정·런타임·검증 seed가 잠금과 달라 검증을 중단합니다.')


def summary_metrics(scenario,branch):
    outcomes=branch['patient_outcomes']; events=branch['events']
    end=branch['metrics']['observation_end_t']; n=len(outcomes)
    result=dict(patients=n,
        unassigned=sum(p['first_commit_t'] is None for p in outcomes),
        not_arrived=sum(p['final_arrive_t'] is None for p in outcomes),
        untreated=sum(p['treatment_start_t'] is None for p in outcomes),
        high_priority_unassigned=sum(p['priority']==4 and p['first_commit_t'] is None for p in outcomes),
        high_priority_untreated=sum(p['priority']==4 and p['treatment_start_t'] is None for p in outcomes),
        inquiries=sum(e['type']=='ask' for e in events),
        redirects=sum(e['type']=='redirect' for e in events),
        transfers=sum(e['type']=='transfer_depart' for e in events))
    result['inquiries_per_patient']=result['inquiries']/n if n else None
    for label,field,origin in [('selection','first_commit_t','search_start_t'),
                               ('arrival','final_arrive_t','occur_t'),
                               ('treatment','treatment_start_t','occur_t')]:
        values=[p[field]-p[origin] for p in outcomes if p[field] is not None]
        capped=[p[field]-p[origin] if p[field] is not None else max(0,end-p[origin]) for p in outcomes]
        result[label+'_denominator']=len(values)
        result[label+'_mean_minutes']=statistics.mean(values) if values else None
        result[label+'_capped_mean_minutes']=statistics.mean(capped) if capped else None
    notifications=[(i,e) for i,e in enumerate(events) if e['type']=='notify']
    lags=[]
    for index,event in notifications:
        change=next((e for e in events[index+1:] if e['type']=='redirect' and
                     e.get('patient')==event.get('patient')),None)
        if change is not None: lags.append(change['t']-event['t'])
    result.update(notifications=len(notifications),notification_redirect_pairs=len(lags),
                  notification_without_redirect=len(notifications)-len(lags),
                  notify_to_redirect_mean_minutes=statistics.mean(lags) if lags else None)
    return result


def common_patient_metrics(scenario,baseline,comparison):
    left={p['id']:p for p in baseline['patient_outcomes']}
    right={p['id']:p for p in comparison['patient_outcomes']}
    result={}
    for label,field,origin in [('selection','first_commit_t','search_start_t'),
                              ('arrival','final_arrive_t','occur_t'),
                              ('treatment','treatment_start_t','occur_t')]:
        shared=[pid for pid in left if left[pid][field] is not None and right[pid][field] is not None]
        values=[right[pid][field]-right[pid][origin]-(left[pid][field]-left[pid][origin]) for pid in shared]
        result[label]=dict(denominator=len(values),mean_difference_minutes=statistics.mean(values) if values else None,
            comparison_only=sum(left[pid][field] is None and right[pid][field] is not None for pid in left),
            baseline_only=sum(left[pid][field] is not None and right[pid][field] is None for pid in left))
    return result


def aggregate(results):
    groups=[]
    for family in FAMILIES:
        rows=[r for r in results if r['scenario']['family']==family]
        policies={}
        for spec in DEFAULT_POLICIES:
            code=spec['id']; records=[r['summaries'][code] for r in rows]
            # Each seed is one independent paired case; do not pool easy
            # patients from policies with different completion denominators.
            keys=['unassigned','not_arrived','untreated','inquiries_per_patient',
                  'selection_capped_mean_minutes','arrival_capped_mean_minutes','treatment_capped_mean_minutes',
                  'redirects','transfers','notifications','notification_without_redirect']
            policies[code]=dict(cases=len(records),**{k:statistics.mean(p[k] for p in records) for k in keys})
        groups.append(dict(family=family,policies=policies))
    return groups


def run(phase,lock=None):
    if phase not in ('development','validation'): raise ValueError('unknown phase')
    if phase=='validation': verify_lock(lock)
    from ortools.sat.python import cp_model  # load before measured decisions
    seeds=DEV_SEEDS if phase=='development' else VALIDATION_SEEDS
    results=[]
    for seed in seeds:
        for scenario in build_online_scenarios(seed):
            replay=run_online_replay(scenario)
            branches=replay['branches']
            summaries={b['id']:summary_metrics(replay['scenario'],b) for b in branches}
            for branch in branches: branch['online_summary']=summaries[branch['id']]
            baseline=next(b for b in branches if b['id']=='R1')
            results.append(dict(scenario=replay['scenario'],branches=branches,summaries=summaries,
                duration_minutes=replay['duration_minutes'],routing_summary=replay['routing_summary'],
                paired_vs_r1={b['id']:common_patient_metrics(replay['scenario'],baseline,b) for b in branches}))
    return dict(schema_version=1,kind='online_allocation_comparison',phase=phase,synthetic=True,
        seeds=list(seeds),families=list(FAMILIES),settings=SETTINGS,limitations=LIMITATIONS,
        runtime_versions=runtime_versions(),source_sha256=source_hashes(),
        lock_sha256=digest(lock) if lock else None,results=results,summary=aggregate(results))


def markdown(report):
    lines=['# 전체 이송 과정의 배정 정책 비교','',f"{report['phase']} · {len(report['results'])}사례 × 5규칙. 전부 합성 모형이다.",'',
           '문의 규모3곳·첫 수용 후 추가 대기2분·변경 알림 설정을 같게 유지했다. 배정 결과에 따라 이후 자원과 회신은 달라질 수 있다.','',
           '| 조건 | 규칙 | 평균 미배정 | 평균 미도착 | 평균 치료 미시작 | 선정 제한 경과분 | 도착 제한 경과분 | 환자당 문의 |',
           '|---|---|---:|---:|---:|---:|---:|---:|']
    for row in report['summary']:
        for code,p in row['policies'].items():
            lines.append(f"| {row['family']} | {code} | {p['unassigned']:.2f} | {p['not_arrived']:.2f} | {p['untreated']:.2f} | {p['selection_capped_mean_minutes']:.2f} | {p['arrival_capped_mean_minutes']:.2f} | {p['inquiries_per_patient']:.2f} |")
    lines+=['','선정은 현장 평가 완료부터, 도착은 환자 발생부터의 모의 경과시간이다. 관찰 종료까지 사건이 없으면 종료시각까지의 경과시간을 넣는 제한 평균이며 실제 미도착 시간 추정이 아니다.','',
            '미배정·미도착이 있는 정책을 조건부 평균만으로 더 빠르다고 해석하면 안 된다. 원시 JSON의 공통 환자 비교·분모·우선순위 결과도 함께 확인한다.','',
            '## 범위와 한계','']+['- '+t for t in report['limitations']]
    return '\n'.join(lines)+'\n'


def export_replay(report,path,source_path):
    if report['phase']!='validation': raise ValueError('정해진 검증 첫 seed만 지도 자료로 내보냅니다.')
    chosen=[]
    for family in report['families']:
        rows=[r for r in report['results'] if r['scenario']['family']==family]
        row=deepcopy(min(rows,key=lambda r:r['scenario']['seed']))
        scenario=row['scenario']
        scenario['label']='[온라인 배정 비교] '+scenario['label']
        scenario['source']=dict(file=str(Path(source_path).resolve().relative_to(ROOT)),
            report_kind=report['kind'],phase=report['phase'],selection_rule=SETTINGS['replay_selection'])
        scenario.setdefault('assumptions',[]).extend(report['limitations'])
        chosen.append(dict(schema_version=2,scenario=scenario,branches=row['branches'],
            duration_minutes=row['duration_minutes'],routing_summary=row['routing_summary']))
    payload=dict(schema_version=2,source_kind='online_allocation_comparison',
                 source_sha256=hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),scenarios=chosen)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(replay=str(path),scenarios=len(chosen)),ensure_ascii=False))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--phase',choices=('development','validation'),default='development')
    parser.add_argument('--lock',action='store_true')
    parser.add_argument('--lock-file',type=Path,default=ROOT/'config/online_allocation_lock.json')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--export-replay',type=Path)
    parser.add_argument('--replay-output',type=Path,default=ROOT/'simulation/viewer/online_allocation_data.json')
    args=parser.parse_args(argv)
    if args.export_replay:
        export_replay(json.loads(args.export_replay.read_text(encoding='utf-8')),args.replay_output,args.export_replay)
        return
    if args.phase=='validation' and args.lock: parser.error('검증 중 설정 잠금을 덮어쓸 수 없습니다.')
    lock=json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase=='validation' else None
    report=run(args.phase,lock)
    output=args.output or ROOT/f'simulation_results/online_allocation_{args.phase}_20261013.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    output.with_suffix('.md').write_text(markdown(report),encoding='utf-8')
    if args.lock:
        args.lock_file.parent.mkdir(parents=True,exist_ok=True)
        args.lock_file.write_text(json.dumps(manifest(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(output=str(output),phase=args.phase,cases=len(report['results']),branches=len(report['results'])*5),ensure_ascii=False))


if __name__=='__main__': main()
