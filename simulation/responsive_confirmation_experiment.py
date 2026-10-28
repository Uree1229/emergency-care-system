"""Locked parallel resource-confirmation comparison with unused holdout seeds."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics

from .h1_online_experiment import AGGREGATE_FIELDS, diagnostic_metrics, paired_arrival_details, runtime_versions
from .online_allocation_experiment import common_patient_metrics, summary_metrics
from .opportunity_experiment import SOURCE_FILES as PREVIOUS_SOURCE_FILES, SETTINGS as PREVIOUS_SETTINGS
from .opportunity_experiment import LIMITATIONS as PREVIOUS_LIMITATIONS, guard_metrics
from .partial_information_experiment import digest, information_metrics
from .responsive_confirmation_engine import DEFAULT_RESPONSIVE_POLICIES, EXPERIMENT_ID, run_responsive_confirmation_replay
from .responsive_confirmation_scenarios import FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, build_responsive_confirmation_scenarios


ROOT=Path(__file__).resolve().parents[1]
DEV_SEEDS=DEVELOPMENT_SEEDS
POLICY_IDS=tuple(p['id'] for p in DEFAULT_RESPONSIVE_POLICIES)
LOCK_PATH=ROOT/'config/responsive_confirmation_lock_20261015.json'
SOURCE_FILES=tuple(sorted(set(PREVIOUS_SOURCE_FILES)|{
    'simulation/responsive_confirmation_engine.py','simulation/responsive_confirmation_experiment.py',
    'simulation/responsive_confirmation_scenarios.py','simulation/responsive_confirmation_audit.py',
    'test_responsive_confirmation_engine.py','test_responsive_confirmation_scenarios.py',
    'test_responsive_confirmation_experiment.py','test_responsive_confirmation_audit.py',
}))
SETTINGS=deepcopy(PREVIOUS_SETTINGS)
SETTINGS.update(policies=deepcopy(list(DEFAULT_RESPONSIVE_POLICIES)),
    execution_order='rotate fourteen branches by seed index plus family index; canonical output order',
    compute_time='allocation, protection and confirmation intervention measured separately, not added to simulation clock',
    tuning='fixed parallel-confirmation rule before new development and validation')
SETTINGS['protection']['grace_minutes']={'protected':3,'early':3,'watch':3,'responsive':3}
SETTINGS['responsiveness']=dict(
    prefetch='unknown/stale quantities in parallel with first eligible inquiry; no acceptance required',
    watch='after each tick; currently visible single-hospital SEARCH demand at a previously resource-requested hospital',
    incomplete='stop early/responsive holds after received confirmation still leaves required quantities unknown',
    cooldown='existing per-hospital retry_minutes and one pending confirmation',
    wake='same-minute latest applied report; fresh sufficient quantities, no pending ask or unexpired offer; require new acceptance',
    cold_grace_minutes=3)
LIMITATIONS=list(PREVIOUS_LIMITATIONS)+[
    '개선 분기는 병원 수용 문의와 자원 수량 재확인을 병행한다. 미래 회신 지연이나 실제 미보고 수량은 정책에 제공하지 않는다.',
    '빠른 확인은 보존하지만 초기 정보가 같으면 빠른 회신·긴 회신·무응답을 미리 구별할 수 없다. 모든 환자의 비악화는 보장하지 않는다.',
    'early/responsive도 3분 시계를 첫 실제 자원 확인 대기부터 센다. 초기 문의와 병행해 수용 회신 시점보다 먼저 시작한다. 3분 이내 회신의 기존 이점을 보존하도록 예산 자체는 줄이지 않는다.',
    '확인 응답에도 필수 수량이 unknown이면 early/responsive 보호를 중단한다. 무응답과 실제 부족은 다른 사건이다.',
    '추적은 희소 병원의 현재 SEARCH 수요가 있는 동안만 수행한다. 새 자원 요청이 늘 수 있으며 수용 문의 감소와 별도 집계한다.',
    '만료된 수용 회신을 자원 보고로 되살리지 않는다. 최신 가용 보고를 받아도 새 문의와 새 수용 응답을 거쳐야 한다.',
    '22개 합성 유형의 새 시드 비교이며 현장 임상적 안전성·최적성이나 운영 서버의 자동 감지를 검증한 것이 아니다.',
]
COMPARISON_PAIRS=tuple((f'observed_{r}_vs_perfect',f'perfect_{r}',f'observed_{r}') for r in ('R1','H1','C1'))+tuple(
    (f'{mode}_{r}_vs_{baseline}',f'{baseline}_{r}',f'{mode}_{r}')
    for mode,baseline in (('protected','observed'),('early','protected'),('watch','protected'),
                          ('responsive','protected'),('responsive','observed')) for r in ('H1','C1'))


def source_hashes():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def manifest():
    return dict(schema_version=1,kind='responsive_confirmation_lock',experiment_id=EXPERIMENT_ID,
        families=list(FAMILIES),settings=deepcopy(SETTINGS),development_seeds=list(DEV_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS),runtime_versions=runtime_versions(),source_sha256=source_hashes())


def verify_lock(lock):
    if lock!=manifest():raise ValueError('병행 재확인 실험의 소스·설정·런타임·seed가 잠금과 다릅니다.')


def responsiveness_metrics(branch):
    events=branch['events']
    return dict(prefetch_checks=sum(e['type']=='resource_prefetch' for e in events),
        watch_checks=sum(e['type']=='resource_watch_check' for e in events),
        watch_cycles=sum(e['type']=='resource_watch_cycle' for e in events),
        report_driven_wakes=sum(e['type']=='report_driven_wake' for e in events),
        confirmation_intervention_wall_seconds=branch['metrics']['confirmation_intervention_wall_seconds'])


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
        for fi,scenario in enumerate(build_responsive_confirmation_scenarios(seed)):
            offset=(si+fi)%len(POLICY_IDS)
            replay=run_responsive_confirmation_replay(scenario,execution_order=POLICY_IDS[offset:]+POLICY_IDS[:offset])
            branches=replay['branches'];by={b['id']:b for b in branches}
            comparisons={name:dict(baseline=left,comparison=right,
                metrics=common_patient_metrics(replay['scenario'],by[left],by[right]),
                arrival_details=paired_arrival_details(by[left],by[right])) for name,left,right in COMPARISON_PAIRS}
            results.append(dict(scenario=replay['scenario'],branches=branches,execution_order=replay['execution_order'],
                routing_summary=replay['routing_summary'],
                summaries={b['id']:summary_metrics(replay['scenario'],b) for b in branches},
                information_metrics={b['id']:information_metrics(b) for b in branches},
                guard_metrics={b['id']:guard_metrics(b) for b in branches},
                responsiveness_metrics={b['id']:responsiveness_metrics(b) for b in branches},
                diagnostics={b['id']:diagnostic_metrics(replay['scenario'],b) for b in branches},
                paired_comparisons=comparisons))
            if progress:progress(len(results),total,scenario['id'])
    return dict(schema_version=1,kind='responsive_confirmation_comparison',experiment_id=EXPERIMENT_ID,
        phase=phase,synthetic=True,seeds=list(seeds),families=list(FAMILIES),settings=deepcopy(SETTINGS),
        limitations=list(LIMITATIONS),runtime_versions=runtime_versions(),source_sha256=source_hashes(),
        lock_sha256=digest(lock) if lock else None,results=results,summary=aggregate(results))


def _paired_mean(cases,key):
    pairs=[c['paired_comparisons'][key]['metrics']['arrival'] for c in cases]
    n=sum(p['denominator'] for p in pairs)
    total=sum(p['denominator']*p['mean_difference_minutes'] for p in pairs if p['denominator'])
    return n,total/n if n else None


def markdown(report):
    lines=['# 재확인 지연 대응 개선 검증','',f"{report['phase']} · {len(report['results'])}사례 × 14분기 · 합성 입력",'',
        '기존 3분 보호와 더 이른 병행 확인·독립 자원 추적을 비교한다. 수용 문의와 수량 재확인은 별도 집계한다.','',
        '| 조건 | 규칙 | 기존 보호 미도착 | 개선 미도착 | 공통 도착 | 개선−기존 보호(분) | 개선−무보호(분) | 기존/개선 자원 요청 |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for f in report['families']:
        cases=[c for c in report['results'] if c['scenario']['family']==f]
        for r in ('H1','C1'):
            n,change=_paired_mean(cases,f'responsive_{r}_vs_protected')
            _,unprotected=_paired_mean(cases,f'responsive_{r}_vs_observed')
            left,right=f'protected_{r}',f'responsive_{r}'
            missing=[sum(c['summaries'][k]['not_arrived'] for c in cases) for k in (left,right)]
            requests=[sum(c['information_metrics'][k]['resource_confirmation_requests'] for c in cases) for k in (left,right)]
            delta='—' if change is None else f'{change:+.3f}'
            raw='—' if unprotected is None else f'{unprotected:+.3f}'
            lines.append(f'| {f} | {r} | {missing[0]} | {missing[1]} | {n} | {delta} | {raw} | {requests[0]}/{requests[1]} |')
    lines+=['','음수는 단축, 양수는 지연이다. 각 차이는 해당 쌍에서 공통 도착한 환자의 평균이며 한쪽만 도착한 수는 원시 JSON에 보존한다.',
        'early/watch 분기로 병행 확인과 독립 추적의 효과를 별도로 기록한다. 개선의 전역 최적성·모든 환자의 비악화는 주장하지 않는다.','',
        '## 범위와 한계','']+['- '+s for s in report['limitations']]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('development','validation'),default='development')
    parser.add_argument('--lock',action='store_true');parser.add_argument('--lock-file',type=Path,default=LOCK_PATH)
    parser.add_argument('--output',type=Path);args=parser.parse_args(argv)
    if args.phase=='validation' and args.lock:parser.error('validation cannot overwrite development lock')
    output=args.output or ROOT/f'simulation_results/responsive_confirmation_{args.phase}_20261015.json'
    if output.suffix.lower()!='.json':parser.error('report output must use a .json suffix')
    paths=[output,output.with_suffix('.md'),output.with_name(output.stem+'_audit.json')]
    if args.lock:paths.append(args.lock_file)
    if len({p.resolve() for p in paths})!=len(paths):parser.error('report, Markdown, audit and lock paths must be distinct')
    if any(p.exists() for p in paths):parser.error('output already exists; choose new explicit paths')
    lock=json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase=='validation' else None
    report=run(args.phase,lock,progress=lambda done,total,name:print(json.dumps(dict(done=done,total=total,scenario=name)),flush=True))
    from .responsive_confirmation_audit import audit_report
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
