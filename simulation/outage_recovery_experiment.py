"""Locked original backup-work recovery without additional resource reports."""
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
from .existing_info_experiment import SOURCE_FILES as PREVIOUS_SOURCE_FILES
from .total_wait_metrics import waiting_metrics
from .existing_info_engine import early_offer_metrics
from .outage_recovery_engine import DEFAULT_OUTAGE_RECOVERY_POLICIES, EXPERIMENT_ID, run_outage_recovery_replay, backup_metrics
from .outage_recovery_scenarios import FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, build_outage_recovery_scenarios

ROOT=Path(__file__).resolve().parents[1]
DEV_SEEDS=DEVELOPMENT_SEEDS
POLICY_IDS=tuple(p['id'] for p in DEFAULT_OUTAGE_RECOVERY_POLICIES)
LOCK_PATH=ROOT/'config/outage_recovery_lock_20261015.json'
SOURCE_FILES=tuple(sorted(set(PREVIOUS_SOURCE_FILES)|{
    'simulation/outage_recovery_engine.py','simulation/outage_recovery_experiment.py',
    'simulation/outage_recovery_scenarios.py','simulation/outage_recovery_audit.py',
    'tests/research/test_outage_recovery_engine.py','tests/research/test_outage_recovery_experiment.py',
    'tests/research/test_outage_recovery_scenarios.py','tests/research/test_outage_recovery_audit.py',
}))
SETTINGS={'stale_after_minutes': 10,
 'inquiry_policy': 'fixed_3_first_acceptance_extra_wait_2_minutes',
 'observation_scope': 'hospital capacity/used reports; current server commitments and queues remain known',
 'freshness_clock': 'time since source observation, not since network receipt',
 'confirmation_contract': 'explicit synthetic resource response; patient acceptance does not update '
                          'quantities',
 'transient_shortage_retry_minutes': 1,
 'execution_order': 'rotate six branches by seed index plus family index; canonical output order',
 'routing': 'synthetic coordinates/travel; no API',
 'simulation_unit': 'integer minute',
 'compute_time': 'original allocation/confirmation, event/early-offer review and backup receipt bookkeeping '
                 'measured separately; not added to simulation clock',
 'tuning': 'fixed original-work retention and unchanged failed-arrival preparation before new development '
           'and validation',
 'protection': {'grace_minutes': {'protected': 3, 'early': 3, 'watch': 3, 'responsive': 3},
                'resource': 'ed_bed',
                'waiter_structural_choices': 1,
                'competitor_minimum_structural_choices': 2,
                'priority': 'never defer a higher-priority ready patient',
                'clocks': 'lifetime first unknown/stale wait and first actual hold; never reset',
                'offer_expiry': 'release before the waiting offer or last current ready offer expires on the '
                                'next tick',
                'effect': 'defer eligible flexible ready patients in priority order until headroom fits, '
                          'then use ordinary allocation model',
                'reservation': 'no physical reservation or unconfirmed patient allocation'},
 'responsiveness': {'prefetch': 'unknown/stale quantities in parallel with first eligible inquiry; no '
                                'acceptance required',
                    'watch': 'after each tick; currently visible single-hospital SEARCH demand at a '
                             'previously resource-requested hospital',
                    'incomplete': 'stop early/responsive holds after received confirmation still leaves '
                                  'required quantities unknown',
                    'cooldown': 'existing per-hospital retry_minutes and one pending confirmation',
                    'wake': 'same-minute latest applied report; fresh sufficient quantities, no pending ask '
                            'or unexpired offer; require new acceptance',
                    'cold_grace_minutes': 3},
 'stability': {'rule': 'equal_objective_priority_request_id_nearest_baseline',
               'objective': 'exact original lexicographic pair; no epsilon or weighted perturbation',
               'baseline': 'priority descending, request time ascending, patient ID ascending; feasible '
                           'nearest hospital then hospital ID',
               'replacement': 'baseline only if both objective components exactly equal the solver plan',
               'solver': 'original shared 50ms model-and-solve budget; no extra solver stage',
               'proof': 'original two-objective model only; no global tie-canonicality or whole-episode '
                        'guarantee'},
 'resource_updates': {'enabled': False,
                      'source': 'existing initial/feed/confirmation channels only; no new publication or '
                                'proactive quantity query',
                      'comparison': 'same observation configuration, capture schedule, missing masks, source '
                                    'clocks and delays; endogenous occupancy payloads may differ'},
 'event_review': {'trigger': 'latest applied report received this minute; SEARCH with no asks or offers; '
                             'fresh sufficient required resources',
                  'selection': 'priority/request/id patient order; nearest compatible unfailed reported '
                               'hospital then hospital ID',
                  'action': 'reset retry for that hospital only; send a new patient inquiry; acceptance and '
                            'final physical gate remain mandatory',
                  'clock': 'pre_queries and after_treatments; once per patient and report event sequence'},
 'waiting_evaluation': {'cohort': 'all requested patients including untreated at fixed horizon',
                        'waiting': 'integral of SEARCH, QUEUED and TRANSFER_WAIT; transit and treatment '
                                   'excluded',
                        'treatment': 'request-to-treatment censored at observation end if not started; '
                                     'report untreated count and individual details',
                        'factors': 'same original reporting contract; early offers, original backup work '
                                   'retention and preparation-complete same-tick review separately'},
 'early_offers': {'trigger': 'after ordinary replies before original opportunity guard; registered SEARCH '
                             'not ready, pending asks and live offers, first-valid age below two minutes',
                  'input': 'detached current valid offers, observed quantities, static candidate routes and '
                           'all registered SEARCH capability-compatible demand',
                  'decision': 'fresh sufficient closest structural candidate; free each demanded resource '
                              'covers entire compatible registered SEARCH cohort',
                  'action': 'mark early-ready and run unchanged opportunity guard, joint C1 and final '
                            'physical gate',
                  'limitation': 'current dominance does not predict a future outage or guarantee no patient '
                                'harm'},
 'retained_backups': {'trigger': 'successful same-tick early commitment; preserve other already submitted '
                                 'pending patient inquiries and unexpired received patient offers',
                      'input': 'known original hospital/attempt/submission/absolute-timeout/source-event '
                               'handles and actual acceptance/source-event/expiry only; no hidden reply time '
                               'or silence',
                      'delivery': 'original hospital work calendar; retained requests cannot reply after '
                                  'original sender timeout; normal patient acceptance never refreshes '
                                  'resource counts',
                      'healthy_transit': 'receive only; no automatic reroute merely because a backup accepts',
                      'arrival': 'preserve unfinished work and original live offer timestamps across actual '
                                 'failed arrival and unchanged preparation',
                      'preparation': 'same-tick branch enters ordinary SEARCH at original failed-arrival '
                                     'plus preparation time; then unchanged resource checks, opportunity '
                                     'guard, C1 and physical gate',
                      'limitations': 'no future outage prediction; no additional resource reports, shortened '
                                     'route, pause or skipped transfer preparation'},
 'recovery_followup': {'report_reopen': 'registered SEARCH in active backup episode; resource-capacity '
                                        'failure; latest applied report received this minute with source '
                                        'time not before failure; every required resource fresh sufficient '
                                        'and individually sourced at or after failure; reopen failed '
                                        'hospital and send new patient inquiry only',
                       'alert_retry': 'active notified TRANSIT; original backup offer expiration received '
                                      'before current alert; no open or live backup; current fresh '
                                      'sufficient quantities; one fresh patient inquiry per expired backup '
                                      'and alert',
                       'exclusions': 'no capability-failure reopening, future restoration schedule, extra '
                                     'resource query, acceptance renewal or gate bypass'}}
SETTINGS['policies']=deepcopy(list(DEFAULT_OUTAGE_RECOVERY_POLICIES))
LIMITATIONS=['추가 자원 변화 보고는 모든 분기에서 0건이며 기존 초기·주기·재확인 및 환자별 문의 회신만 사용한다.',
 '보존한 문의는 원래 절대 시간 초과 이후 수용으로 되살리지 않는다. 실제 병원 작업은 취소하지 않는다.',
 '수용의 원래 수신·만료 시각을 유지하며 정상 이송 중 백업 수용만으로 자동 방향 변경하지 않는다.',
 '실패 도착 평가·물리적 이동·기존 전원 준비시간을 그대로 두고 준비 완료 분에 기존 검증·공동 배정을 실행한다.',
 '실패 병원 재문의는 현재 받은 기존 보고의 모든 필수 수량이 실패 후 관측되고 신선·충분할 때만 허용한다.',
 '만료 백업의 새 문의는 기존 환자별 알림과 만료 증거에 근거한다. 새 수용 응답과 최종 충돌 검사가 필요하다.',
 '전체 환자 대기와 관찰 종료로 제한한 처치 도달을 함께 평가한다. 실제 병상 점유 대기와 지속 누락은 남는다.',
 '48개 합성 환경의 새 검증 시드이며 운영 서버·API·팀원 UI나 실제 임상 안전성·전역 이송 최적성을 검증하지 않는다.']
COMPARISON_PAIRS=(('review_vs_stable', 'stable_responsive_C1', 'review_stable_C1'),
 ('early_vs_stable', 'stable_responsive_C1', 'early_stable_C1'),
 ('existing_vs_stable', 'stable_responsive_C1', 'existing_info_C1'),
 ('existing_vs_review', 'review_stable_C1', 'existing_info_C1'),
 ('existing_vs_early', 'early_stable_C1', 'existing_info_C1'),
 ('retained_vs_existing', 'existing_info_C1', 'retained_backup_C1'),
 ('recovery_vs_existing', 'existing_info_C1', 'outage_recovery_C1'),
 ('recovery_vs_review', 'review_stable_C1', 'outage_recovery_C1'),
 ('recovery_vs_retained', 'retained_backup_C1', 'outage_recovery_C1'))


def source_hashes():
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in SOURCE_FILES}


def manifest():
    return dict(schema_version=1,kind='outage_recovery_lock',experiment_id=EXPERIMENT_ID,
        families=list(FAMILIES),settings=deepcopy(SETTINGS),development_seeds=list(DEV_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS),runtime_versions=runtime_versions(),source_sha256=source_hashes())


def verify_lock(lock):
    if lock!=manifest():raise ValueError('돌발 중단 회복 실험의 소스·설정·런타임·시드가 잠금과 다릅니다.')


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
        for fi,scenario in enumerate(build_outage_recovery_scenarios(seed)):
            offset=(si+fi)%len(POLICY_IDS)
            replay=run_outage_recovery_replay(scenario,execution_order=POLICY_IDS[offset:]+POLICY_IDS[:offset])
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
                backup_metrics={b['id']:backup_metrics(b) for b in branches},
                diagnostics={b['id']:diagnostic_metrics(replay['scenario'],b) for b in branches},
                paired_comparisons={name:dict(baseline=a,comparison=b,
                    metrics=common_patient_metrics(replay['scenario'],by[a],by[b]),
                    arrival_details=paired_arrival_details(by[a],by[b])) for name,a,b in COMPARISON_PAIRS}))
            if progress:progress(len(results),total,scenario['id'])
    return dict(schema_version=1,kind='outage_recovery_comparison',experiment_id=EXPERIMENT_ID,
        phase=phase,synthetic=True,seeds=list(seeds),families=list(FAMILIES),settings=deepcopy(SETTINGS),
        limitations=list(LIMITATIONS),runtime_versions=runtime_versions(),source_sha256=source_hashes(),
        lock_sha256=digest(lock) if lock else None,results=results,summary=aggregate(results))


def markdown(report):
    lines=['# 추가 자원 보고 없는 돌발 중단 회복 검증','',
        f"{report['phase']} · {len(report['results'])}사례 × 6분기 · 합성 입력",'',
        '추가 자원 변화 보고 0건. 원래 환자별 백업 문의·유효 수용을 보존하고 기존 전원 준비시간을 유지한다.',
        '이전 조기 출발과 기존 보고 재검토를 모두 비교한다. 미처치도 관찰 종료까지 전체 대기에 포함한다.','',
        '| 조건 | 기존 재검토 | 이전 조기 | 보존만 | 전체 회복 | 이전 대비 감소 | 단축 | 악화 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    codes=('review_stable_C1','existing_info_C1','retained_backup_C1','outage_recovery_C1')
    for family in report['families']:
        cases=[c for c in report['results'] if c['scenario']['family']==family]
        sums=[sum(c['waiting_metrics'][code]['total_wait_sum_minutes'] for c in cases) for code in codes]
        differences=[]
        for case in cases:
            a,b=[case['waiting_metrics'][code]['patient_details'] for code in (codes[1],codes[-1])]
            differences.extend(y['total_wait_minutes']-x['total_wait_minutes'] for x,y in zip(a,b))
        lines.append('| '+family+' | '+' | '.join(str(n) for n in sums)+f' | {sums[1]-sums[-1]:+} | {sum(d<0 for d in differences)} | {sum(d>0 for d in differences)} |')
    lines+=['','단위는 환자·분이며 각 행은 해당 단계 시드 합계다. 양수 감소가 단축이다.','',
        '## 기존 재검토 대비 개별 결과','',
        '| 조건 | 요청→처치 합 감소 | 처치 단축 | 처치 지연 | 최대 추가 지연 | 미처치 기존→회복 |',
        '|---|---:|---:|---:|---:|---:|']
    for family in report['families']:
        cases=[c for c in report['results'] if c['scenario']['family']==family];deltas=[];change=0;missing=[0,0]
        for case in cases:
            a,b=[case['waiting_metrics'][code] for code in (codes[0],codes[-1])]
            change+=a['request_to_treatment_capped_sum_minutes']-b['request_to_treatment_capped_sum_minutes']
            missing[0]+=a['untreated'];missing[1]+=b['untreated']
            deltas.extend(y['request_to_treatment_capped_minutes']-x['request_to_treatment_capped_minutes'] for x,y in zip(a['patient_details'],b['patient_details']))
        lines.append(f'| {family} | {change:+} | {sum(d<0 for d in deltas)} | {sum(d>0 for d in deltas)} | {max([0]+deltas)} | {missing[0]}→{missing[1]} |')
    lines+=['','관찰 종료로 제한한 지표와 미처치 수를 함께 해석한다. 원시 JSON에 모든 환자 결과와 준비·문의·수용 근거를 보존한다.','',
        '## 범위와 한계','']+['- '+s for s in report['limitations']]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('development','validation'),default='development')
    parser.add_argument('--lock',action='store_true');parser.add_argument('--lock-file',type=Path,default=LOCK_PATH)
    parser.add_argument('--output',type=Path);args=parser.parse_args(argv)
    if args.phase=='validation' and args.lock:parser.error('validation cannot relock')
    output=args.output or ROOT/f'simulation_results/outage_recovery_{args.phase}_20261015.json'
    if output.suffix.lower()!='.json':parser.error('report output must use .json suffix')
    paths=[output,output.with_suffix('.md'),output.with_name(output.stem+'_audit.json')]
    if args.lock:paths.append(args.lock_file)
    if len({p.resolve() for p in paths})!=len(paths):parser.error('report, Markdown, audit and lock paths must be distinct')
    if any(p.exists() for p in paths):parser.error('output already exists; choose new explicit paths')
    lock=json.loads(args.lock_file.read_text(encoding='utf-8')) if args.phase=='validation' else None
    report=run(args.phase,lock,progress=lambda done,total,name:print(json.dumps(dict(done=done,total=total,scenario=name)),flush=True))
    from .outage_recovery_audit import audit_report
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
