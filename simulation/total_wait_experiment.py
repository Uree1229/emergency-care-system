"""Locked whole-cohort waiting comparison with separate reporting/review factors."""
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
from .stable_c1_experiment import SOURCE_FILES as PREVIOUS_SOURCE_FILES
from .stable_c1_experiment import SETTINGS as PREVIOUS_SETTINGS, LIMITATIONS as PREVIOUS_LIMITATIONS
from .stable_c1_experiment import COMPARISON_PAIRS as PREVIOUS_PAIRS
from .total_wait_engine import DEFAULT_TOTAL_WAIT_POLICIES, EXPERIMENT_ID, run_total_wait_replay, update_metrics
from .total_wait_metrics import waiting_metrics
from .total_wait_scenarios import FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, build_total_wait_scenarios

ROOT=Path(__file__).resolve().parents[1]
DEV_SEEDS=DEVELOPMENT_SEEDS
POLICY_IDS=tuple(p['id'] for p in DEFAULT_TOTAL_WAIT_POLICIES)
LOCK_PATH=ROOT/'config/total_wait_lock_20261015.json'
SOURCE_FILES=tuple(sorted(set(PREVIOUS_SOURCE_FILES)|{
    'simulation/total_wait_engine.py','simulation/total_wait_metrics.py','simulation/total_wait_experiment.py',
    'simulation/total_wait_scenarios.py','simulation/total_wait_audit.py',
    'test_total_wait_engine.py','test_total_wait_metrics.py','test_total_wait_experiment.py',
    'test_total_wait_scenarios.py','test_total_wait_audit.py',
}))
SETTINGS=deepcopy(PREVIOUS_SETTINGS)
SETTINGS.update(policies=deepcopy(list(DEFAULT_TOTAL_WAIT_POLICIES)),
    execution_order='rotate twenty branches by seed index plus family index; canonical output order',
    compute_time='allocation, protection, confirmation, resource sampling/delivery and event review measured separately; not added to simulation clock',
    tuning='fixed current-change publications and received-event retry before new development and validation')
SETTINGS['resource_updates']=dict(
    source='synthetic hospital present capacity/used changes only; no future release or treatment-duration input',
    capture='sample pre_queries and after_treatments once per minute; publish changed fields only',
    delivery='configured nonnegative integer delay; preserve union of persistent missing masks and hospital silence',
    ordering='publication ID at capture, packet sequence at receipt; source observation clock unchanged')
SETTINGS['event_review']=dict(
    trigger='latest applied report received this minute; SEARCH with no asks or offers; fresh sufficient required resources',
    selection='priority/request/id patient order; nearest compatible unfailed reported hospital then hospital ID',
    action='reset retry for that hospital only; send a new patient inquiry; acceptance and final physical gate remain mandatory',
    clock='pre_queries and after_treatments; once per patient and report event sequence')
SETTINGS['waiting_evaluation']=dict(
    cohort='all requested patients including untreated at fixed horizon',
    waiting='integral of SEARCH, QUEUED and TRANSFER_WAIT; transit and treatment excluded',
    treatment='request-to-treatment censored at observation end if not started; report untreated count and individual details',
    factors='same old reports with/without review; added publications with/without review')
LIMITATIONS=list(PREVIOUS_LIMITATIONS)+[
    '자원 변화 보고는 새 합성 입력 계약이다. 보고 개선 효과와 같은 정보를 받은 재검토 알고리즘 효과를 분리한다.',
    '현재 발생한 변화만 전달한다. 미래 퇴실 시각을 예측하거나 병상·환자·처치시간을 바꾸지 않는다.',
    '전체 대기는 모든 환자의 탐색·병원 내 대기·전원 대기 합이다. 이동시간은 별도이며 요청→처치 도달도 함께 평가한다.',
    '미처치 환자는 관찰 종료까지 포함한다. 처치 완료 사례만의 평균으로 개선을 주장하지 않는다.',
    '병상이 실제 점유된 동안의 물리적 대기는 남는다. 모든 환자의 도착 개선이나 전체 이송 최적성을 보장하지 않는다.',
    '31개 합성 유형의 새 검증 시드이며 운영 서버·팀원 UI에는 적용하지 않는다.',
]
COMPARISON_PAIRS=PREVIOUS_PAIRS+(
    ('review_stable_C1_vs_stable','stable_responsive_C1','review_stable_C1'),
    ('updates_stable_C1_vs_stable','stable_responsive_C1','updates_stable_C1'),
    ('total_wait_C1_vs_stable','stable_responsive_C1','total_wait_C1'),
    ('total_wait_C1_vs_updates','updates_stable_C1','total_wait_C1'),
    ('total_wait_C1_vs_review','review_stable_C1','total_wait_C1'),
)


def source_hashes():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def manifest():
    return dict(schema_version=1,kind='total_wait_lock',experiment_id=EXPERIMENT_ID,
        families=list(FAMILIES),settings=deepcopy(SETTINGS),development_seeds=list(DEV_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS),runtime_versions=runtime_versions(),source_sha256=source_hashes())


def verify_lock(lock):
    if lock!=manifest():raise ValueError('전체 대기 실험의 소스·설정·런타임·seed가 잠금과 다릅니다.')


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
        for fi,scenario in enumerate(build_total_wait_scenarios(seed)):
            offset=(si+fi)%len(POLICY_IDS)
            replay=run_total_wait_replay(scenario,execution_order=POLICY_IDS[offset:]+POLICY_IDS[:offset])
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
                diagnostics={b['id']:diagnostic_metrics(replay['scenario'],b) for b in branches},
                paired_comparisons={name:dict(baseline=left,comparison=right,
                    metrics=common_patient_metrics(replay['scenario'],by[left],by[right]),
                    arrival_details=paired_arrival_details(by[left],by[right]))
                    for name,left,right in COMPARISON_PAIRS}))
            if progress:progress(len(results),total,scenario['id'])
    return dict(schema_version=1,kind='total_wait_comparison',experiment_id=EXPERIMENT_ID,
        phase=phase,synthetic=True,seeds=list(seeds),families=list(FAMILIES),settings=deepcopy(SETTINGS),
        limitations=list(LIMITATIONS),runtime_versions=runtime_versions(),source_sha256=source_hashes(),
        lock_sha256=digest(lock) if lock else None,results=results,summary=aggregate(results))


def markdown(report):
    lines=['# 전체 환자 대기시간 감소 검증','',
        f"{report['phase']} · {len(report['results'])}사례 × 20분기 · 합성 입력",'',
        '모든 환자의 탐색·병원 내 대기·전원 대기 합(환자·분). 미처치는 관찰 종료까지 포함한다.',
        '기존 안정화 C1 / 같은 기존 보고 즉시 재검토 / 자원 변화 보고 / 보고+재검토를 분리한다.',
        '추가 보고는 현재 변화에 관한 새 입력 계약이며 C1 목적함수만의 개선이 아니다.','',
        '| 조건 | 기존 | 재검토만 | 보고만 | 보고+재검토 | 전체 감소 | 미처치 기존→개선 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    codes=('stable_responsive_C1','review_stable_C1','updates_stable_C1','total_wait_C1')
    for family in report['families']:
        cases=[c for c in report['results'] if c['scenario']['family']==family]
        sums=[sum(c['waiting_metrics'][code]['total_wait_sum_minutes'] for c in cases) for code in codes]
        untreated=[sum(c['waiting_metrics'][code]['untreated'] for c in cases) for code in (codes[0],codes[-1])]
        lines.append('| '+family+' | '+' | '.join(str(n) for n in sums)+f' | {sums[0]-sums[-1]:+} | {untreated[0]}→{untreated[1]} |')
    lines+=['','전체 감소가 양수면 단축이다. 각 행은 해당 단계 시드의 합계이며 평균이 아니다.','',
        '## 환자별 결과','',
        '| 조건 | 빨라진 환자 | 늦어진 환자 | 동일 | 요청→처치 합 감소(환자·분) |',
        '|---|---:|---:|---:|---:|']
    for family in report['families']:
        cases=[c for c in report['results'] if c['scenario']['family']==family];delta=[];treatment=0
        for case in cases:
            a,b=(case['waiting_metrics'][code] for code in (codes[0],codes[-1]))
            treatment+=a['request_to_treatment_capped_sum_minutes']-b['request_to_treatment_capped_sum_minutes']
            delta.extend(y['total_wait_minutes']-x['total_wait_minutes'] for x,y in zip(a['patient_details'],b['patient_details']))
        lines.append(f"| {family} | {sum(d<0 for d in delta)} | {sum(d>0 for d in delta)} | {sum(d==0 for d in delta)} | {treatment:+} |")
    lines+=['','미처치 수가 바뀌는 경우 요청→처치 합은 관찰 종료로 제한한 값이다. 원시 JSON에 환자별 항목을 보존한다.',
        '병상 점유가 만든 물리적 대기는 제거할 수 없다. 대기 환자를 바꾼 결과와 전체 합 단축을 구분한다.','',
        '## 범위와 한계','']+['- '+s for s in report['limitations']]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('development','validation'),default='development')
    parser.add_argument('--lock',action='store_true');parser.add_argument('--lock-file',type=Path,default=LOCK_PATH)
    parser.add_argument('--output',type=Path);args=parser.parse_args(argv)
    if args.phase=='validation' and args.lock:parser.error('validation cannot overwrite development lock')
    output=args.output or ROOT/f'simulation_results/total_wait_{args.phase}_20261015.json'
    if output.suffix.lower()!='.json':parser.error('report output must use a .json suffix')
    paths=[output,output.with_suffix('.md'),output.with_name(output.stem+'_audit.json')]
    if args.lock:paths.append(args.lock_file)
    if len({p.resolve() for p in paths})!=len(paths):parser.error('report, Markdown, audit and lock paths must be distinct')
    if any(p.exists() for p in paths):parser.error('output already exists; choose new explicit paths')
    lock=json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase=='validation' else None
    report=run(args.phase,lock,progress=lambda done,total,name:print(json.dumps(dict(done=done,total=total,scenario=name)),flush=True))
    from .total_wait_audit import audit_report
    audit=audit_report(report,expected_source_sha256=source_hashes(),lock=lock)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as target:target.write(json.dumps(report,ensure_ascii=False,allow_nan=False)+'\n')
    with output.with_suffix('.md').open('x',encoding='utf-8') as target:target.write(markdown(report))
    with output.with_name(output.stem+'_audit.json').open('x',encoding='utf-8') as target:target.write(json.dumps(audit,indent=2)+'\n')
    if args.lock:
        args.lock_file.parent.mkdir(parents=True,exist_ok=True)
        with args.lock_file.open('x',encoding='utf-8') as target:target.write(json.dumps(manifest(),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(output=str(output),phase=args.phase,cases=len(report['results']),branches=len(report['results'])*len(POLICY_IDS),audit=audit)))


if __name__=='__main__':main()
