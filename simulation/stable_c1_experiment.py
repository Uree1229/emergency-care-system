"""Locked C1 equal-objective stability comparisons on unused scenario seeds."""
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
from .responsive_confirmation_experiment import SOURCE_FILES as PREVIOUS_SOURCE_FILES
from .responsive_confirmation_experiment import SETTINGS as PREVIOUS_SETTINGS, LIMITATIONS as PREVIOUS_LIMITATIONS
from .responsive_confirmation_experiment import COMPARISON_PAIRS as PREVIOUS_PAIRS, responsiveness_metrics
from .stable_c1_engine import DEFAULT_STABLE_C1_POLICIES, EXPERIMENT_ID, run_stable_c1_replay, stability_metrics
from .stable_c1_scenarios import FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, build_stable_c1_scenarios


ROOT=Path(__file__).resolve().parents[1]
DEV_SEEDS=DEVELOPMENT_SEEDS
POLICY_IDS=tuple(p['id'] for p in DEFAULT_STABLE_C1_POLICIES)
LOCK_PATH=ROOT/'config/stable_c1_lock_20261015.json'
SOURCE_FILES=tuple(sorted(set(PREVIOUS_SOURCE_FILES)|{
    'simulation/stable_cp_sat_assignment.py','simulation/stable_c1_engine.py',
    'simulation/stable_c1_experiment.py','simulation/stable_c1_scenarios.py','simulation/stable_c1_audit.py',
    'test_stable_cp_sat_assignment.py','test_stable_c1_engine.py','test_stable_c1_experiment.py',
    'test_stable_c1_scenarios.py','test_stable_c1_audit.py',
}))
SETTINGS=deepcopy(PREVIOUS_SETTINGS)
SETTINGS.update(policies=deepcopy(list(DEFAULT_STABLE_C1_POLICIES)),
    execution_order='rotate seventeen branches by seed index plus family index; canonical output order',
    compute_time='allocation including tie postselection, protection and confirmation intervention measured separately, not added to simulation clock',
    tuning='fixed equal-objective baseline tie rule before new development and validation')
SETTINGS['stability']=dict(rule='equal_objective_priority_request_id_nearest_baseline',
    objective='exact original lexicographic pair; no epsilon or weighted perturbation',
    baseline='priority descending, request time ascending, patient ID ascending; feasible nearest hospital then hospital ID',
    replacement='baseline only if both objective components exactly equal the solver plan',
    solver='original shared 50ms model-and-solve budget; no extra solver stage',
    proof='original two-objective model only; no global tie-canonicality or whole-episode guarantee')
LIMITATIONS=list(PREVIOUS_LIMITATIONS)+[
    '동률 안정화는 기존 C1의 현재 미배정 우선순위 합·가중 이동시간 두 목적값을 모두 유지하는 기준안만 선택한다.',
    '기준안보다 좋은 C1 해는 유지한다. 기준안이 더 나쁜 경우 다른 동률 해들의 전역 정규화를 보장하지 않는다.',
    '높은 우선순위·빠른 요청 시각·환자 ID 기준은 실험의 재현 가능한 선택 순서이며 임상적 우열을 추가 판정하지 않는다.',
    '실제로 병상이 부족하면 누군가는 기다린다. 환자별 추가 지연·대기 환자 변경·전체 꼬리 시간을 함께 해석한다.',
    '최대 37분이라는 이전 결과는 이전 검증 시드 기준이다. 새 전후 비교는 같은 새 검증 시드의 동결 C1과 개선 C1을 사용한다.',
    '동률 후처리는 별도 솔버를 추가하지 않는다. 계산 오버헤드는 전체 호출시간에 포함하고 별도 계측한다.',
    '26개 합성 유형의 새 시드 검증이다. 실제 운영 서버·임상적 안전성·전체 이송 최적성을 입증하지 않는다.',
]
COMPARISON_PAIRS=PREVIOUS_PAIRS+(
    ('stable_observed_C1_vs_observed','observed_C1','stable_observed_C1'),
    ('stable_protected_C1_vs_protected','protected_C1','stable_protected_C1'),
    ('stable_responsive_C1_vs_responsive','responsive_C1','stable_responsive_C1'),
    ('stable_responsive_C1_vs_protected','protected_C1','stable_responsive_C1'),
    ('stable_responsive_C1_vs_observed','observed_C1','stable_responsive_C1'),
    ('stable_responsive_C1_vs_responsive_H1','responsive_H1','stable_responsive_C1'),
)


def source_hashes():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def manifest():
    return dict(schema_version=1,kind='stable_c1_lock',experiment_id=EXPERIMENT_ID,
        families=list(FAMILIES),settings=deepcopy(SETTINGS),development_seeds=list(DEV_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS),runtime_versions=runtime_versions(),source_sha256=source_hashes())


def verify_lock(lock):
    if lock!=manifest():raise ValueError('C1 동률 안정화 실험의 소스·설정·런타임·seed가 잠금과 다릅니다.')


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
        for fi,scenario in enumerate(build_stable_c1_scenarios(seed)):
            offset=(si+fi)%len(POLICY_IDS)
            replay=run_stable_c1_replay(scenario,execution_order=POLICY_IDS[offset:]+POLICY_IDS[:offset])
            branches=replay['branches'];by={b['id']:b for b in branches}
            results.append(dict(scenario=replay['scenario'],branches=branches,execution_order=replay['execution_order'],
                routing_summary=replay['routing_summary'],
                summaries={b['id']:summary_metrics(replay['scenario'],b) for b in branches},
                information_metrics={b['id']:information_metrics(b) for b in branches},
                guard_metrics={b['id']:guard_metrics(b) for b in branches},
                responsiveness_metrics={b['id']:responsiveness_metrics(b) for b in branches},
                stability_metrics={b['id']:stability_metrics(b) for b in branches},
                diagnostics={b['id']:diagnostic_metrics(replay['scenario'],b) for b in branches},
                paired_comparisons={name:dict(baseline=left,comparison=right,
                    metrics=common_patient_metrics(replay['scenario'],by[left],by[right]),
                    arrival_details=paired_arrival_details(by[left],by[right]))
                    for name,left,right in COMPARISON_PAIRS}))
            if progress:progress(len(results),total,scenario['id'])
    return dict(schema_version=1,kind='stable_c1_comparison',experiment_id=EXPERIMENT_ID,
        phase=phase,synthetic=True,seeds=list(seeds),families=list(FAMILIES),settings=deepcopy(SETTINGS),
        limitations=list(LIMITATIONS),runtime_versions=runtime_versions(),source_sha256=source_hashes(),
        lock_sha256=digest(lock) if lock else None,results=results,summary=aggregate(results))


def _paired_mean(cases,key):
    pairs=[c['paired_comparisons'][key]['metrics']['arrival'] for c in cases]
    n=sum(p['denominator'] for p in pairs)
    total=sum(p['denominator']*p['mean_difference_minutes'] for p in pairs if p['denominator'])
    return n,total/n if n else None


def markdown(report):
    lines=['# C1 동률 배정 안정화 검증','',f"{report['phase']} · {len(report['results'])}사례 × 17분기 · 합성 입력",'',
        '같은 현재 목적값일 때만 우선순위·요청 시각·환자 ID 기준안을 선택한다. 기준안보다 좋은 C1 해는 유지한다.',
        '평균과 함께 환자별 대기 전환 및 추가 지연을 확인한다. 병상 부족으로 발생하는 대기 자체는 남을 수 있다.','',
        '| 조건 | 기존 C1 미도착 | 안정화 C1 미도착 | 공통 도착 | 안정화−기존(분) | 빨라짐 | 늦어짐 | 동률 선택 변경 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for f in report['families']:
        cases=[c for c in report['results'] if c['scenario']['family']==f]
        n,change=_paired_mean(cases,'stable_responsive_C1_vs_responsive')
        delta='—' if change is None else f'{change:+.3f}'
        left,right='responsive_C1','stable_responsive_C1'
        missing=[sum(c['summaries'][k]['not_arrived'] for c in cases) for k in (left,right)]
        details=[c['paired_comparisons']['stable_responsive_C1_vs_responsive']['arrival_details'] for c in cases]
        replacements=sum(c['stability_metrics'][right]['stability_tie_replacements'] for c in cases)
        lines.append(f"| {f} | {missing[0]} | {missing[1]} | {n} | {delta} | {sum(d['earlier'] for d in details)} | {sum(d['delayed'] for d in details)} | {replacements} |")
    lines+=['','음수는 단축, 양수는 지연이다. 공통 도착과 한쪽에서만 도착한 실행 수는 원시 JSON에 별도 보존한다.',
        '동률 선택 변경 수는 솔버의 실제 제안에서 다른 기준안으로 교체한 배정 호출 수이며 개선 환자 수가 아니다.','',
        '## 범위와 한계','']+['- '+s for s in report['limitations']]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('development','validation'),default='development')
    parser.add_argument('--lock',action='store_true');parser.add_argument('--lock-file',type=Path,default=LOCK_PATH)
    parser.add_argument('--output',type=Path);args=parser.parse_args(argv)
    if args.phase=='validation' and args.lock:parser.error('validation cannot overwrite development lock')
    output=args.output or ROOT/f'simulation_results/stable_c1_{args.phase}_20261015.json'
    if output.suffix.lower()!='.json':parser.error('report output must use a .json suffix')
    paths=[output,output.with_suffix('.md'),output.with_name(output.stem+'_audit.json')]
    if args.lock:paths.append(args.lock_file)
    if len({p.resolve() for p in paths})!=len(paths):parser.error('report, Markdown, audit and lock paths must be distinct')
    if any(p.exists() for p in paths):parser.error('output already exists; choose new explicit paths')
    lock=json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase=='validation' else None
    report=run(args.phase,lock,progress=lambda done,total,name:print(json.dumps(dict(done=done,total=total,scenario=name)),flush=True))
    from .stable_c1_audit import audit_report
    audit=audit_report(report,expected_source_sha256=source_hashes(),lock=lock)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    output.with_suffix('.md').write_text(markdown(report),encoding='utf-8')
    output.with_name(output.stem+'_audit.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
    if args.lock:
        args.lock_file.parent.mkdir(parents=True,exist_ok=True)
        args.lock_file.write_text(json.dumps(manifest(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(output=str(output),phase=args.phase,cases=len(report['results']),branches=len(report['results'])*len(POLICY_IDS),audit=audit)))


if __name__=='__main__':main()
