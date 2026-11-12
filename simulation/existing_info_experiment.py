"""Locked existing-information-only waiting experiment with adverse cases."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics

from .h1_online_experiment import AGGREGATE_FIELDS, diagnostic_metrics, paired_arrival_details, runtime_versions
from .online_allocation_experiment import common_patient_metrics, summary_metrics
from .opportunity_experiment import guard_metrics
from .partial_information_experiment import digest, information_metrics
from .responsive_confirmation_experiment import responsiveness_metrics
from .stable_c1_engine import stability_metrics
from .total_wait_engine import update_metrics
from .total_wait_experiment import SOURCE_FILES as PREVIOUS_SOURCE_FILES
from .total_wait_experiment import SETTINGS as PREVIOUS_SETTINGS
from .total_wait_metrics import waiting_metrics
from .existing_info_engine import DEFAULT_EXISTING_INFO_POLICIES, EXPERIMENT_ID, run_existing_info_replay, early_offer_metrics
from .existing_info_scenarios import FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, build_existing_info_scenarios

ROOT=Path(__file__).resolve().parents[1]
DEV_SEEDS=DEVELOPMENT_SEEDS
POLICY_IDS=tuple(p['id'] for p in DEFAULT_EXISTING_INFO_POLICIES)
LOCK_PATH=ROOT/'config/existing_info_lock_20261015.json'
SOURCE_FILES=tuple(sorted(set(PREVIOUS_SOURCE_FILES)|{
    'simulation/existing_info_engine.py','simulation/existing_info_experiment.py',
    'simulation/existing_info_scenarios.py','simulation/existing_info_audit.py',
    'tests/research/test_existing_info_engine.py','tests/research/test_existing_info_experiment.py',
    'tests/research/test_existing_info_scenarios.py','tests/research/test_existing_info_audit.py',
}))
SETTINGS=deepcopy(PREVIOUS_SETTINGS)
SETTINGS.update(policies=deepcopy(list(DEFAULT_EXISTING_INFO_POLICIES)),
    execution_order='rotate four branches by seed index plus family index; canonical output order',
    compute_time='original allocation/confirmation and event/early-offer reviews measured separately; not added to simulation clock',
    tuning='fixed dominant current offer and full registered SEARCH demand check before new development and validation')
SETTINGS['resource_updates']=dict(enabled=False,
    source='existing initial/feed/confirmation channels only; no new publication or proactive quantity query',
    comparison='same observation configuration, capture schedule, missing masks, source clocks and delays; endogenous occupancy payloads may differ')
SETTINGS['early_offers']=dict(
    trigger='after ordinary replies before original opportunity guard; registered SEARCH not ready, pending asks and live offers, first-valid age below two minutes',
    input='detached current valid offers, observed quantities, static candidate routes and all registered SEARCH capability-compatible demand',
    decision='fresh sufficient closest structural candidate; free each demanded resource covers entire compatible registered SEARCH cohort',
    action='mark early-ready and run unchanged opportunity guard, joint C1 and final physical gate',
    limitation='current dominance does not predict a future outage or guarantee no patient harm')
SETTINGS['waiting_evaluation']['factors']='no resource-change publications; same-report event review and early-offer review separately'
LIMITATIONS=[
    '추가 자원 변화 보고를 모든 분기에서 끈다. 기존 정보 계약의 초기 보고·주기 피드·기존 재확인만 사용한다.',
    '조기 검토 규칙은 자원 수량 재요청을 직접 수행하지 않는다. 환자별 수용과 실제 최종 충돌 검사는 필요하다.',
    '분기별 처치 시각 변화로 기존 주기 피드의 점유량은 달라질 수 있다. 동일한 미래 점유량을 정책에 재생하지 않는다.',
    '현재 최단 수용의 우월성은 미래의 안전성을 보장하지 않는다. 미래 병상 중단 반례의 개별 악화도 보존한다.',
    '전체 합이 줄어도 모든 환자에게 이롭다는 뜻은 아니다. 대기 증가와 처치 도달 증가를 별도 평가한다.',
    '전체 대기는 모든 환자의 탐색·병원 대기·전원 대기 합이다. 미처치는 관찰 종료까지 포함하고 이동은 별도 평가한다.',
    '병상 수·환자 수·처치시간·물리적 사건을 바꾸지 않는다. 실제로 점유된 자원 때문에 발생하는 대기는 남는다.',
    'C1은 현재 모형의 미배정 우선순위 합과 가중 이동시간을 최적화하며 전체 이송 시간의 전역 최적성을 보장하지 않는다.',
    '최종 충돌 검사는 즉시 권위 있는 자원을 확인한다는 기존 실험 가정이며 실제 운영 성능 검증이 아니다.',
    '39개 합성 환경의 새 시드 비교이며 운영 서버·API·팀원 UI에는 적용하지 않는다.',
]
COMPARISON_PAIRS=(
    ('review_vs_stable','stable_responsive_C1','review_stable_C1'),
    ('early_vs_stable','stable_responsive_C1','early_stable_C1'),
    ('existing_vs_stable','stable_responsive_C1','existing_info_C1'),
    ('existing_vs_review','review_stable_C1','existing_info_C1'),
    ('existing_vs_early','early_stable_C1','existing_info_C1'))


def source_hashes():
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in SOURCE_FILES}


def manifest():
    return dict(schema_version=1,kind='existing_info_lock',experiment_id=EXPERIMENT_ID,
        families=list(FAMILIES),settings=deepcopy(SETTINGS),development_seeds=list(DEV_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS),runtime_versions=runtime_versions(),source_sha256=source_hashes())


def verify_lock(lock):
    if lock!=manifest():raise ValueError('기존 정보 실험의 소스·설정·런타임·시드가 잠금과 다릅니다.')


def aggregate(results):
    return [dict(family=f,policies={code:dict(cases=len(cases),**{
        field:statistics.mean(c['summaries'][code][field] for c in cases)
        for field in AGGREGATE_FIELDS}) for code in POLICY_IDS})
        for f in FAMILIES for cases in [[r for r in results if r['scenario']['family']==f]]]


def run(phase,lock=None,progress=None):
    if phase not in ('development','validation'):raise ValueError('unknown phase')
    if phase=='validation':verify_lock(lock)
    from ortools.sat.python import cp_model
    seeds=DEV_SEEDS if phase=='development' else VALIDATION_SEEDS
    results=[];total=len(seeds)*len(FAMILIES)
    for si,seed in enumerate(seeds):
        for fi,scenario in enumerate(build_existing_info_scenarios(seed)):
            offset=(si+fi)%len(POLICY_IDS)
            replay=run_existing_info_replay(scenario,execution_order=POLICY_IDS[offset:]+POLICY_IDS[:offset])
            branches=replay['branches'];by={b['id']:b for b in branches}
            results.append(dict(scenario=replay['scenario'],branches=branches,execution_order=replay['execution_order'],
                routing_summary=replay['routing_summary'],
                summaries={b['id']:summary_metrics(replay['scenario'],b) for b in branches},
                information_metrics={b['id']:information_metrics(b) for b in branches},
                guard_metrics={b['id']:guard_metrics(b) for b in branches},
                responsiveness_metrics={b['id']:responsiveness_metrics(b) for b in branches},
                stability_metrics={b['id']:stability_metrics(b) for b in branches},
                waiting_metrics={b['id']:waiting_metrics(replay['scenario'],b) for b in branches},
                update_metrics={b['id']:update_metrics(b) for b in branches},
                early_offer_metrics={b['id']:early_offer_metrics(b) for b in branches},
                diagnostics={b['id']:diagnostic_metrics(replay['scenario'],b) for b in branches},
                paired_comparisons={name:dict(baseline=a,comparison=b,
                    metrics=common_patient_metrics(replay['scenario'],by[a],by[b]),
                    arrival_details=paired_arrival_details(by[a],by[b])) for name,a,b in COMPARISON_PAIRS}))
            if progress:progress(len(results),total,scenario['id'])
    return dict(schema_version=1,kind='existing_info_comparison',experiment_id=EXPERIMENT_ID,
        phase=phase,synthetic=True,seeds=list(seeds),families=list(FAMILIES),settings=deepcopy(SETTINGS),
        limitations=list(LIMITATIONS),runtime_versions=runtime_versions(),source_sha256=source_hashes(),
        lock_sha256=digest(lock) if lock else None,results=results,summary=aggregate(results))


def markdown(report):
    lines=['# 기존 정보만으로 전체 대기 개선 검증','',
        f"{report['phase']} · {len(report['results'])}사례 × 4분기 · 합성 입력",'',
        '추가 자원 변화 보고는 전 분기에서 0건. 기존 초기·주기·재확인 보고와 유효 수용만 사용한다.',
        '모든 환자의 대기 합을 사용하며 미처치는 관찰 종료까지 포함한다. 이동은 대기 합에서 제외한다.',
        '조기 출발 뒤 미래 병상 중단이 발생하는 반례도 포함한다. 전체 단축과 개별 악화를 함께 해석한다.','',
        '| 조건 | 기존 | 재검토만 | 조기 검토만 | 함께 적용 | 전체 감소 | 단축 환자 | 악화 환자 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for f in report['families']:
        cases=[c for c in report['results'] if c['scenario']['family']==f]
        sums=[sum(c['waiting_metrics'][code]['total_wait_sum_minutes'] for c in cases) for code in POLICY_IDS]
        differences=[]
        for case in cases:
            a,b=[case['waiting_metrics'][code]['patient_details'] for code in ('stable_responsive_C1','existing_info_C1')]
            differences.extend(y['total_wait_minutes']-x['total_wait_minutes'] for x,y in zip(a,b))
        lines.append('| '+f+' | '+' | '.join(str(n) for n in sums)+f' | {sums[0]-sums[-1]:+} | {sum(d<0 for d in differences)} | {sum(d>0 for d in differences)} |')
    lines+=['','전체 감소의 양수는 단축이다. 각 행은 해당 단계 시드 합계, 단위는 환자·분이다.','',
        '## 환자별 처치 도달과 미처치','',
        '| 조건 | 요청→처치 합 감소 | 처치 도달 단축 | 처치 도달 악화 | 최대 추가 지연 | 미처치 기존→개선 |',
        '|---|---:|---:|---:|---:|---:|']
    for f in report['families']:
        cases=[c for c in report['results'] if c['scenario']['family']==f];deltas=[];change=0;missing=[0,0]
        for c in cases:
            a,b=[c['waiting_metrics'][code] for code in ('stable_responsive_C1','existing_info_C1')]
            change+=a['request_to_treatment_capped_sum_minutes']-b['request_to_treatment_capped_sum_minutes']
            missing[0]+=a['untreated'];missing[1]+=b['untreated']
            deltas.extend(y['request_to_treatment_capped_minutes']-x['request_to_treatment_capped_minutes']
                          for x,y in zip(a['patient_details'],b['patient_details']))
        lines.append(f'| {f} | {change:+} | {sum(d<0 for d in deltas)} | {sum(d>0 for d in deltas)} | {max([0]+deltas)} | {missing[0]}→{missing[1]} |')
    lines+=['','미처치 환자의 요청→처치는 관찰 종료로 제한한다. 원시 JSON에 모든 환자의 대기·도달 시각을 보존한다.',
        '## 범위와 한계','']+['- '+s for s in report['limitations']]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('development','validation'),default='development')
    parser.add_argument('--lock',action='store_true');parser.add_argument('--lock-file',type=Path,default=LOCK_PATH)
    parser.add_argument('--output',type=Path);args=parser.parse_args(argv)
    if args.phase=='validation' and args.lock:parser.error('validation cannot relock')
    output=args.output or ROOT/f'simulation_results/existing_info_{args.phase}_20261015.json'
    if output.suffix.lower()!='.json':parser.error('report output must use .json suffix')
    paths=[output,output.with_suffix('.md'),output.with_name(output.stem+'_audit.json')]
    if args.lock:paths.append(args.lock_file)
    if len({p.resolve() for p in paths})!=len(paths):parser.error('report, Markdown, audit and lock paths must be distinct')
    if any(p.exists() for p in paths):parser.error('output already exists; choose new explicit paths')
    lock=json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase=='validation' else None
    report=run(args.phase,lock,progress=lambda done,total,name:print(json.dumps(dict(done=done,total=total,scenario=name)),flush=True))
    from .existing_info_audit import audit_report
    audit=audit_report(report,expected_source_sha256=source_hashes(),lock=lock)
    output.parent.mkdir(parents=True,exist_ok=True)
    for path,body in ((output,json.dumps(report,ensure_ascii=False,allow_nan=False)+'\n'),
        (output.with_suffix('.md'),markdown(report)),
        (output.with_name(output.stem+'_audit.json'),json.dumps(audit,indent=2)+'\n')):
        with path.open('x',encoding='utf-8') as target:target.write(body)
    if args.lock:
        args.lock_file.parent.mkdir(parents=True,exist_ok=True)
        with args.lock_file.open('x',encoding='utf-8') as target:target.write(json.dumps(manifest(),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(output=str(output),phase=args.phase,cases=len(report['results']),branches=len(report['results'])*len(POLICY_IDS),audit=audit)))


if __name__=='__main__':main()
