"""Source-bound G2 recovery experiment and gated, new four-arm validation."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

from . import final_assignment_experiment_20261028 as original_driver
from . import final_assignment_scenarios_20261028 as generator
from . import arrival_guard_priority_engine_20261028 as engine
from . import arrival_guard_priority_audit_20261028 as auditor
from . import arrival_guard_priority_analysis_20261028 as analysis

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.codex-build/arrival-guard-priority-20261028'
RESULTS = ROOT / 'simulation_results'
PROTOCOL_REL = '.codex-build/arrival-guard-priority-20261028/protocol.json'
PLAN_REL = 'docs/ARRIVAL_GUARD_PRIORITY_PROTOCOL_20261028.md'
PROTOCOL_SHA256 = '6c9734b8412dd69ee7d9c844e587d66682de5af4cacf943e2eae718b13fbf4b9'
EXPERIMENT_ID = 'arrival_guard_priority_20261028'
POLICY_IDS = ('R0','R1','H1','G2')
CONTROL_IDS = POLICY_IDS[:-1]
REGRESSION_PHASES = ('regression_original','regression_g1validation')
PHASES = REGRESSION_PHASES + ('validation',)
TEST_FILES = tuple('test_arrival_guard_priority_'+part+'_20261028.py' for part in
    ('policy','engine','audit','analysis','experiment','integration'))
START_FILES = tuple('simulation/arrival_guard_priority_'+part+'_20261028.py' for part in
    ('policy','engine','audit','analysis','experiment')) + TEST_FILES
PARENT_PATHS = {
    'manifest_sha256': '.codex-build/final-assignment-20261028/manifest.json',
    'validation_report_sha256': 'simulation_results/final_assignment_validation_20261028.json',
    'archive_sha256': 'outputs/final_assignment_20261028_manifest.json',
    'source_freeze_sha256': '.codex-build/final-assignment-20261028/source-freeze.json',
    'validation_verification_sha256': '.codex-build/final-assignment-20261028/validation-verification.json'}
canonical = original_driver.canonical
digest = original_driver.digest
sha = original_driver.sha
read = original_driver.read
under = original_driver.under
write_json = original_driver.write_json
write_text = original_driver.write_text
runtime_versions = original_driver.runtime_versions
regenerate = original_driver.regenerate


def source_files():
    pending=list(START_FILES);seen=set()
    while pending:
        name=pending.pop()
        if name in seen:continue
        path=under(name)
        if not path.is_file():raise ValueError('required experiment source missing: '+name)
        seen.add(name);package=name.split('/')[:-1]
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'),filename=name)):
            modules=[]
            if isinstance(node,ast.Import):modules=[value.name for value in node.names]
            elif isinstance(node,ast.ImportFrom):
                prefix=package[:len(package)-node.level+1] if node.level else []
                base='.'.join(prefix+([node.module] if node.module else []))
                modules=[base]+[base+'.'+value.name for value in node.names if base and value.name!='*']
            for module in modules:pending.extend(p for p in original_driver._module_paths(module) if p not in seen)
    return tuple(sorted(seen|{PROTOCOL_REL,PLAN_REL}))


def source_hashes():return {name:digest(under(name)) for name in source_files()}


def protocol():
    assert digest(under(PROTOCOL_REL)) == PROTOCOL_SHA256, 'preregistered protocol changed'
    value=read(under(PROTOCOL_REL))
    assert value['experiment_id']==EXPERIMENT_ID and value['candidate']=='G2'
    assert value['regression_cases']==value['validation_cases']==345
    assert value['regression_phases']==list(REGRESSION_PHASES)
    assert value['regression_phase_cases']=={phase:345 for phase in REGRESSION_PHASES}
    assert value['bootstrap_samples']==5000
    assert value['validation']==dict(main_seeds_per_stratum=100,stress_seeds=5,
        main_cases=300,stress_cases=45,all_four_arms_fresh=True,source_and_parameters_unchanged_after_regression=True)
    assert value['fresh_seed_namespace']=='arrival_guard_priority_20261028_validation_v1'
    assert value['inquiry_settings']==dict(k=3,wait=2,notify=True,unchanged=True)
    return value


def parent_bindings(value):
    bindings={}
    for key,relative in PARENT_PATHS.items():
        assert digest(under(relative))==value['parents'][key], 'immutable parent changed: '+relative
        bindings[relative]=value['parents'][key]
    seal=read(under(PARENT_PATHS['source_freeze_sha256']))
    assert seal['passed'] is True
    for relative,expected in seal['source_sha256'].items():
        assert digest(under(relative))==expected, 'old frozen source changed: '+relative
        bindings[relative]=expected
    for relative,expected in value['additional_parent_sha256'].items():
        assert digest(under(relative))==expected, 'G1 evidence parent changed: '+relative
        bindings[relative]=expected
    g1_seal=read(under('.codex-build/arrival-guard-20261028/source-freeze.json'))
    assert g1_seal['passed'] is True
    for relative,expected in g1_seal['source_sha256'].items():
        assert digest(under(relative))==expected, 'G1 frozen source changed: '+relative
        bindings[relative]=expected
    return bindings


def fresh_seed(namespace,stratum,index):
    assert type(index) is int and index>=0
    return 2**256+int(hashlib.sha256(f'{namespace}|{stratum}|{index}'.encode('utf-8')).hexdigest(),16)


def entries_for(phase,value=None):
    value=protocol() if value is None else value
    assert phase in PHASES
    if phase in REGRESSION_PHASES:
        path=PARENT_PATHS['manifest_sha256'] if phase=='regression_original' else '.codex-build/arrival-guard-20261028/manifest.json'
        original=read(under(path))
        rows=deepcopy(original['entries']['validation'])
        assert len(rows)==value['regression_phase_cases'][phase]
        for row in rows:
            row['original_phase']=row['phase'];row['phase']=phase
            row['controls_source']='cached_known'
            row['known_reference']='original' if phase=='regression_original' else 'g1validation'
        return rows
    rows=[];cfg=value['validation'];namespace=value['fresh_seed_namespace']
    for stratum in generator.STRATA:
        for index in range(cfg['main_seeds_per_stratum']):
            seed=fresh_seed(namespace,stratum,index);scenario=generator.build_case(stratum,seed)
            rows.append(dict(case_id=f'main_{stratum}_{index:03d}',phase=phase,track='main',stratum=stratum,
                index=index,seed=seed,patient_count=len(scenario['patients']),scenario_sha256=sha(scenario)))
    for index in range(cfg['stress_seeds']):
        seed=fresh_seed(namespace,'stress',index)
        for scenario in generator.build_stress_cases(seed):
            family=scenario['family']
            rows.append(dict(case_id=f'stress_{family}_{index:03d}',phase=phase,track='stress',stratum=family,
                index=index,seed=seed,patient_count=len(scenario['patients']),scenario_sha256=sha(scenario)))
    assert len(rows)==value['validation_cases'] and len({r['case_id'] for r in rows})==len(rows)
    for i,row in enumerate(rows):
        offset=i%3;row['execution_order']=list(CONTROL_IDS[offset:]+CONTROL_IDS[:offset]);row['controls_source']='fresh'
    return rows


def report_path(phase):return RESULTS/f'arrival_guard_priority_{phase}_20261028.json'


def _phase_paths(phase):
    return dict(started=BUILD/(phase+'-started.json'),raw=BUILD/(phase+'-raw'),
        index=BUILD/(phase+'-raw-index.json'),failure=BUILD/(phase+'-failure.json'),
        report=report_path(phase),markdown=report_path(phase).with_suffix('.md'))


def prepare(checks_path):
    for name in ('manifest.json','source-freeze.json','validation-lock.json'):
        assert not (BUILD/name).exists(),'cannot overwrite existing preparation evidence'
    for phase in PHASES:
        assert not any(path.exists() for path in _phase_paths(phase).values())
    checks_path=Path(checks_path).resolve();checks=read(checks_path);before=source_hashes();value=protocol()
    assert checks['passed'] is True and checks['failures']==checks['errors']==0
    assert checks.get('skips',checks.get('skipped',0))==0
    assert checks['source_sha256_before']==checks['source_sha256_after']==before
    bindings=parent_bindings(value);bindings[checks_path.relative_to(ROOT).as_posix()]=digest(checks_path)
    for relative,expected in checks.get('private_files_sha256',{}).items():
        assert digest(under(relative))==expected;bindings[relative]=expected
    entries={phase:entries_for(phase,value) for phase in PHASES}
    seeds={phase:{r['seed'] for r in rows} for phase,rows in entries.items()}
    for i,phase in enumerate(PHASES):
        assert all(seed>=2**256 for seed in seeds[phase])
        assert all(not seeds[phase]&seeds[other] for other in PHASES[i+1:])
    assert source_hashes()==before
    manifest=dict(schema_version=1,experiment_id=EXPERIMENT_ID,protocol=value,
        protocol_sha256=digest(under(PROTOCOL_REL)),plan_sha256=digest(under(PLAN_REL)),
        source_sha256=before,binding_sha256=bindings,runtime_versions=runtime_versions(),entries=entries,
        regression_old_controls_reused=True,regression_new_G2_arms_per_phase=345,regression_total_new_G2_arms=690,
        known_cohorts_pooled=False,validation_four_arms_fresh=True,
        known_parent_outcomes_observed_before_candidate_design=True,new_candidate_arms_before_freeze=0,
        fresh_validation_outcomes_before_freeze=0,operational_adoption=False)
    BUILD.mkdir(parents=True,exist_ok=True);write_json(BUILD/'manifest.json',manifest)
    write_json(BUILD/'source-freeze.json',dict(passed=True,manifest_sha256=digest(BUILD/'manifest.json'),
        source_sha256=before,protocol_sha256=manifest['protocol_sha256'],runtime_versions=manifest['runtime_versions'],
        candidate_branches_before_freeze=0,validation_outcomes_before_freeze=0))
    return manifest


def frozen():
    manifest=read(BUILD/'manifest.json');seal=read(BUILD/'source-freeze.json')
    assert seal['passed'] is True and seal['candidate_branches_before_freeze']==0
    assert seal['manifest_sha256']==digest(BUILD/'manifest.json')
    assert seal['source_sha256']==manifest['source_sha256']==source_hashes()
    assert manifest['protocol']==protocol() and manifest['runtime_versions']==runtime_versions()==seal['runtime_versions']
    for relative,expected in manifest['binding_sha256'].items():assert digest(under(relative))==expected
    return manifest


def _loaded_sources_inside_freeze(manifest):return original_driver._loaded_sources_inside_freeze(manifest)


def _read_raw(entry,item,folder):
    assert item['case_id']==entry['case_id'] and item['scenario_sha256']==entry['scenario_sha256']
    path=under(item['raw_file']);assert path.parent==folder.resolve()
    assert digest(path)==item['compressed_sha256']
    body=gzip.decompress(path.read_bytes());assert hashlib.sha256(body).hexdigest()==item['raw_sha256']
    bundle=json.loads(body);assert canonical(bundle)==body
    original=regenerate(entry);assert sha(original)==entry['scenario_sha256']
    assert len(original['patients'])==entry['patient_count']
    assert (bundle['controls'] is None)==(entry['phase'] in REGRESSION_PHASES)
    replays=[bundle['candidate']]+([] if bundle['controls'] is None else [bundle['controls']])
    if entry['track']=='main':
        for replay in replays:
            for branch in replay['branches']:
                assert all(type(e.get('attempt')) is int and 1<=e['attempt']<=64 for e in branch['events'] if e['type']=='ask'), 'main response attempts exhausted'
    result=auditor.audit_bundle(bundle,source_scenario=original,scenario_sha256=entry['scenario_sha256'],
        known_case_id=entry['case_id'] if entry['phase'] in REGRESSION_PHASES else None,
        known_reference=entry.get('known_reference'),
        expected_control_order=entry['execution_order'] if entry['phase']=='validation' else None)
    return dict(case_id=entry['case_id'],track=entry['track'],stratum=entry['stratum'],seed=entry['seed'],
        scenario_sha256=entry['scenario_sha256'],raw_file=item['raw_file'],raw_sha256=item['raw_sha256'],
        compressed_sha256=item['compressed_sha256'],audit=result,
        recorded_allocation_wall_ms={branch['id']:[d['elapsed_seconds']*1000 for d in branch['allocation_decisions']]
            for replay in replays for branch in replay['branches']})


def audit_totals(cases):
    counters=Counter()
    for case in cases:
        assert case['audit']['passed'] is True;counters.update(case['audit']['counters'])
    return dict(passed=True,cases=len(cases),current_raw_branches=sum(c['audit']['current_phase_raw_branches_audited'] for c in cases),
        counters=dict(counters),canonical_labels=list(POLICY_IDS),cached_controls_not_raw_reaudited_this_phase=any(c['audit']['controls_source']['mode']=='cached_known' for c in cases))


def runtime_diagnostics(cases):
    groups=[]
    for track,stratum in sorted({(c['track'],c['stratum']) for c in cases}):
        rows=[c for c in cases if (c['track'],c['stratum'])==(track,stratum)]
        actual=sorted(set().union(*(set(c['recorded_allocation_wall_ms']) for c in rows)))
        policies={}
        for code in actual:
            values=[v for c in rows for v in c['recorded_allocation_wall_ms'][code]]
            policies[code]=dict(decisions=len(values),wall_p95_ms=analysis.reference.percentile(values),wall_max_ms=max(values,default=None))
        groups.append(dict(track=track,stratum=stratum,policies=policies))
    return dict(groups=groups,unit='milliseconds',wall_times_added_to_simulation_clock=False,hardware_specific=True,
        regression_control_wall_times_reused_or_invented=False)


def markdown(report):
    lines=['# G2 회귀·새 자료 검증', '', f"단계: {report['phase']} · 환경 {len(report['cases'])}개 · 합성 입력", '',
        'G2는 미배정 가중치가 증가하지 않으면서 배정 수가 늘거나, 동수에서 실험 우선순위4 선정 수가 늘면 기존 H1 안을 쓴다. 나머지는 호출 순서의 거리순 안을 쓴다.',
        '회귀는 고정된 이전 통제군 벡터를 재사용하고 G2만 새로 실행한다. 새 검증은 네 규칙을 모두 새로 실행한다.', '',
        '| 평가 | 조건 | 환자 | 규칙 | 미배정 | 미도착 | 제한 도착 평균(분) | 제한 도착 p95 |',
        '|---|---|---:|---|---:|---:|---:|---:|']
    for group in report['analysis']['groups']:
        for code in POLICY_IDS:
            row=group['policies'][code]
            lines.append(f"| {group['track']} | {group['stratum']} | {group['patients']} | {code} | {row['unassigned']} | {row['not_arrived']} | {row['arrival_capped_mean_minutes']:.3f} | {row['arrival_capped_p95_minutes']} |")
    lines+=['','## 사전 고정 목표', '',f"통과: {report['analysis']['goals']['passed']}", '', '| 항목 | 통과 | 후보 | 기준 |','|---|---|---:|---:|']
    for row in report['analysis']['goals']['checks']:
        limit=row.get('baseline',row.get('inclusive_limit',row.get('exclusive_limit')))
        lines.append(f"| {row['name']} | {row['passed']} | {row.get('candidate')} | {limit} |")
    for group in report['analysis']['groups']:
        if group['track']=='main' and group['stratum']=='surge':
            tail=group['arrival_tail_cohorts'];fixed=tail['fixed_R1_arrived'];target=tail['original_delayed_targets'];common=tail['actual_shared_arrival']
            lines+=['', '## 거리순 고정 도착자 추적', '',
                f"고정 R1 도착자 {fixed['denominator']}명: 늦어진 {fixed['delayed_count']}명, 최대 제한 지연 {fixed['maximum_delay_minutes']}분, G2 미도착 {fixed['candidate_missing_count']}명.",
                f"기존 지연 대상 {target['denominator']}명: 여전히 지연 {target['delayed_count']}명. 현재 공통 도착자 최대 지연 {common['maximum_delay_minutes']}분."]
    lines+=['', '원래 H1과 G2은 실제 네 라벨로 보존했다. 동결 분석의 H1 별칭에 G2을 넣은 투영만 부트스트랩에 사용하며 별칭표를 JSON에 명시한다.',
        '층·스트레스 계열을 합쳐 손실을 상쇄하지 않는다. 선정·도착 성공 ID 손실/획득과 모든 우선순위 그룹을 JSON에 별도로 보존한다.',
        '미도착자는 관측 종료 제한값을 유지한다. 실제 임상 효과·온라인 최적성·전체 자원 이력 재생을 주장하지 않는다.',
        '저장 원시자료 감사와 동일 분석 구현의 통계 재현을 수행한다. 별도의 독립 통계 수학 검증은 아니다.', '']
    return '\n'.join(lines)


def _validation_unlocked(manifest):
    lock=read(BUILD/'validation-lock.json')
    assert lock['passed'] is True and lock['all_regression_goals_passed'] is True
    assert set(lock['regression_phases'])==set(REGRESSION_PHASES)
    for phase in REGRESSION_PHASES:
        verification=read(BUILD/(phase+'-verification.json'));report=read(report_path(phase));binding=lock['regression_phases'][phase]
        assert lock['manifest_sha256']==verification['manifest_sha256']==digest(BUILD/'manifest.json')
        assert lock['source_freeze_sha256']==verification['source_freeze_sha256']==digest(BUILD/'source-freeze.json')
        assert binding['report_sha256']==verification['report_sha256']==digest(report_path(phase))
        assert binding['verification_sha256']==digest(BUILD/(phase+'-verification.json'))
        assert verification['passed'] is True and verification['phase']==phase and verification['all_regression_goals_passed'] is True
        assert report['technical_passed'] is True and report['analysis']['goals']['passed'] is True
        assert len(report['cases'])==len(manifest['entries'][phase])==345


def _maybe_unlock():
    if not all((BUILD/(phase+'-verification.json')).exists() for phase in REGRESSION_PHASES):return None
    receipts={phase:read(BUILD/(phase+'-verification.json')) for phase in REGRESSION_PHASES}
    if not all(row['passed'] is True and row['all_regression_goals_passed'] is True for row in receipts.values()):return None
    value=dict(passed=True,all_regression_goals_passed=True,manifest_sha256=digest(BUILD/'manifest.json'),
        source_freeze_sha256=digest(BUILD/'source-freeze.json'),regression_phases={phase:dict(
            report_sha256=digest(report_path(phase)),verification_sha256=digest(BUILD/(phase+'-verification.json'))) for phase in REGRESSION_PHASES},
        parameters_retuned_after_regression=False,fresh_validation_outcomes_used_for_selection=False,known_cohorts_pooled=False)
    write_json(BUILD/'validation-lock.json',value);return value


def _analyze(cases,phase,manifest):
    return analysis.analyze(cases,phase='regression' if phase=='regression_original' else 'validation',
        goals=manifest['protocol']['goals'],bootstrap_samples=manifest['protocol']['bootstrap_samples'])


def execute(phase):
    assert phase in PHASES
    manifest=frozen();paths=_phase_paths(phase)
    assert not any(path.exists() for path in paths.values()),'phase cannot rerun or overwrite evidence'
    if phase=='validation':_validation_unlocked(manifest)
    entries=manifest['entries'][phase];started=time.perf_counter()
    write_json(paths['started'],dict(experiment_id=EXPERIMENT_ID,phase=phase,scheduled_cases=len(entries),
        scheduled_new_branches=len(entries)*(1 if phase in REGRESSION_PHASES else 4),workers=1,
        manifest_sha256=digest(BUILD/'manifest.json'),source_freeze_sha256=digest(BUILD/'source-freeze.json'),selective_retries_allowed=False))
    paths['raw'].mkdir();items=[];cases=[];current=None
    try:
        for entry in entries:
            current=entry;scenario=regenerate(entry);assert sha(scenario)==entry['scenario_sha256']
            try:bundle=engine.run_comparison(scenario,include_controls=phase=='validation',
                control_execution_order=entry['execution_order'] if phase=='validation' else None)
            except Exception:bundle=dict(execution_error=traceback.format_exc(),entry=entry,original_scenario=scenario,performance_valid=False)
            body=canonical(bundle);packed=gzip.compress(body,compresslevel=6,mtime=0);path=paths['raw']/(entry['case_id']+'.json.gz')
            with path.open('xb') as stream:stream.write(packed)
            item=dict(case_id=entry['case_id'],scenario_sha256=entry['scenario_sha256'],raw_file=path.relative_to(ROOT).as_posix(),
                raw_sha256=hashlib.sha256(body).hexdigest(),compressed_sha256=hashlib.sha256(packed).hexdigest())
            items.append(item);assert 'execution_error' not in bundle,'execution failure capsule saved; no selective retries'
            cases.append(_read_raw(entry,item,paths['raw']))
            print(json.dumps(dict(phase=phase,done=len(cases),total=len(entries),case_id=entry['case_id'])),flush=True)
        write_json(paths['index'],dict(phase=phase,cases=items,all_scheduled_raws_saved=True))
        _loaded_sources_inside_freeze(manifest);assert frozen()==manifest
        stats=_analyze(cases,phase,manifest)
        report=dict(schema_version=1,experiment_id=EXPERIMENT_ID,phase=phase,synthetic=True,technical_passed=True,
            source_sha256=manifest['source_sha256'],runtime_versions=manifest['runtime_versions'],
            manifest_sha256=digest(BUILD/'manifest.json'),source_freeze_sha256=digest(BUILD/'source-freeze.json'),
            raw_index_sha256=digest(paths['index']),protocol_sha256=manifest['protocol_sha256'],
            cases=cases,audit_totals=audit_totals(cases),analysis=stats,runtime_diagnostics=runtime_diagnostics(cases),
            elapsed_seconds=time.perf_counter()-started,workers=1,phase_new_branches=len(cases)*(1 if phase in REGRESSION_PHASES else 4),
            original_controls_reused=phase in REGRESSION_PHASES,known_reference=entries[0].get('known_reference'),
            known_cohorts_pooled=False,all_four_arms_fresh=phase=='validation',
            source_before_after_equal=True,operational_adoption=False,full_resource_history_replayed=False)
        write_json(paths['report'],report);write_text(paths['markdown'],markdown(report));return report
    except Exception:
        write_json(paths['failure'],dict(phase=phase,passed=False,error=traceback.format_exc(),current_entry=current,
            stored_raws=items,completed_cases=cases,technical_passed=False,rerun_allowed=False,selective_retries_allowed=False))
        raise


def verify(phase):
    assert phase in PHASES
    manifest=frozen();paths=_phase_paths(phase);report=read(paths['report']);index=read(paths['index'])
    target=BUILD/(phase+'-verification.json');assert not target.exists()
    assert report['phase']==index['phase']==phase and report['technical_passed'] is True
    assert report['source_sha256']==manifest['source_sha256']
    assert report['manifest_sha256']==digest(BUILD/'manifest.json') and report['source_freeze_sha256']==digest(BUILD/'source-freeze.json')
    assert report['raw_index_sha256']==digest(paths['index'])
    entries=manifest['entries'][phase];assert len(entries)==len(index['cases'])==len(report['cases'])==345
    cases=[_read_raw(entry,item,paths['raw']) for entry,item in zip(entries,index['cases'])]
    assert canonical(cases)==canonical(report['cases']),'stored raw re-audit differs'
    assert canonical(audit_totals(cases))==canonical(report['audit_totals'])
    assert canonical(runtime_diagnostics(cases))==canonical(report['runtime_diagnostics'])
    repeated=_analyze(cases,phase,manifest)
    assert canonical(repeated)==canonical(report['analysis']),'same implementation deterministic statistics differ'
    assert paths['markdown'].read_text(encoding='utf-8')==markdown(report)
    assert frozen()==manifest
    receipt=dict(passed=True,phase=phase,cases_checked=len(cases),raw_decompressions=len(cases),source_sha256=manifest['source_sha256'],
        report_sha256=digest(paths['report']),raw_index_sha256=digest(paths['index']),manifest_sha256=digest(BUILD/'manifest.json'),
        source_freeze_sha256=digest(BUILD/'source-freeze.json'),new_policy_or_model_executions=0,source_before_after_equal=True,
        all_regression_goals_passed=repeated['goals']['passed'] if phase in REGRESSION_PHASES else None,
        statistical_reproduction_same_implementation=True,independent_statistical_mathematics_verified=False,operational_adoption=False)
    write_json(target,receipt)
    if phase in REGRESSION_PHASES:_maybe_unlock()
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0]);subs=parser.add_subparsers(dest='command',required=True)
    prep=subs.add_parser('prepare');prep.add_argument('--checks',type=Path,required=True)
    for phase in PHASES:subs.add_parser(phase)
    check=subs.add_parser('verify');check.add_argument('--phase',choices=PHASES,required=True)
    args=parser.parse_args(argv)
    if args.command=='prepare':value=prepare(args.checks)
    elif args.command=='verify':value=verify(args.phase)
    else:value=execute(args.command)
    print(json.dumps(dict(command=args.command,technical_passed=True,
        cases=len(value['cases']) if isinstance(value.get('cases'),list) else value.get('cases_checked'),
        source_count=len(value['source_sha256'])),ensure_ascii=False))


if __name__=='__main__':main()
