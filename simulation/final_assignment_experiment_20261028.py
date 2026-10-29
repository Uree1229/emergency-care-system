"""Once-only, source-bound final R0/R1/H1 comparison and stored-raw verification.

The old algorithms and experiment artifacts remain immutable. Only the new
driver's exclusive output paths are written. Development checks technical
contracts without tuning; validation is unlocked solely by those contracts.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import platform
import sys
import time
import traceback

from . import final_assignment_engine_20261028 as engine
from . import final_assignment_scenarios_20261028 as generator
from . import final_assignment_audit_20261028 as auditor

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.codex-build/final-assignment-20261028'
RESULTS = ROOT / 'simulation_results'
PROTOCOL_REL = 'config/final_assignment_protocol_20261028.json'
PLAN_REL = 'docs/FINAL_ASSIGNMENT_PROTOCOL_20261028.md'
EXPERIMENT_ID = 'final_assignment_20261028'
POLICY_IDS = ('R0','R1','H1')
TEST_FILES = tuple('test_final_assignment_'+part+'_20261028.py' for part in
    ('engine','scenarios','audit','analysis','experiment','integration'))
START_FILES = tuple('simulation/final_assignment_'+part+'_20261028.py' for part in
    ('engine','scenarios','audit','analysis','experiment')) + TEST_FILES


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',',':')).encode('utf-8')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes(), parse_constant=lambda value:
        (_ for _ in ()).throw(ValueError('nonfinite JSON '+value)))


def under(relative):
    if type(relative) is not str or not relative or Path(relative).is_absolute():
        raise ValueError('canonical relative workspace path required')
    path=(ROOT/relative).resolve()
    if not path.is_relative_to(ROOT.resolve()):raise ValueError('path escapes workspace')
    return path


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(canonical(value))
    return digest(path)


def write_text(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:stream.write(value)


def _module_paths(name):
    if not (name.startswith(('server.','simulation.','test_')) or name in ('server','simulation')):
        return []
    root=Path(*name.split('.'))
    return [p.as_posix() for p in (root.with_suffix('.py'),root/'__init__.py') if (ROOT/p).is_file()]


def source_files():
    """AST import closure is stable across subprocesses and test imports."""
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
            for module in modules:
                pending.extend(p for p in _module_paths(module) if p not in seen)
    return tuple(sorted(seen|{PROTOCOL_REL,PLAN_REL}))


def source_hashes():
    return {name:digest(under(name)) for name in source_files()}


def runtime_versions():
    return dict(python=sys.version.split()[0],implementation=platform.python_implementation(),
        platform=platform.platform(),machine=platform.machine(),executable=Path(sys.executable).as_posix(),
        ortools=None,ortools_used=False)


def protocol():
    value=read(under(PROTOCOL_REL))
    assert value['experiment_id']==EXPERIMENT_ID
    assert value['policies']==list(engine.DEFAULT_FINAL_ASSIGNMENT_POLICIES)
    assert value['distribution']==generator.DISTRIBUTION
    assert value['strata']==list(generator.STRATA) and value['seed_namespaces']==generator.SEED_NAMESPACES
    assert value['inference']['bootstrap_samples']==5000
    assert value['primary_H1_budget']==dict(max_moves=2048,safety_seconds=10,
        hardware_limit_hit_invalidates_case=True,previous_selected_50ms_version_reused_exactly=False)
    return value


def regenerate(entry):
    if entry['track']=='main':return generator.build_case(entry['stratum'],entry['seed'])
    values=generator.build_stress_cases(entry['seed'])
    rows=[row for row in values if row['family']==entry['stratum']]
    assert len(rows)==1
    return rows[0]


def entries_for(phase,value=None):
    value=protocol() if value is None else value
    assert phase in ('development','validation')
    rows=[];cfg=value[phase]
    for stratum in value['strata']:
        for index in range(cfg['main_seeds_per_stratum']):
            seed=generator.seed_for(phase,stratum,index)
            scenario=generator.build_case(stratum,seed)
            rows.append(dict(case_id=f'main_{stratum}_{index:03d}',phase=phase,
                track='main',stratum=stratum,index=index,seed=seed,
                patient_count=len(scenario['patients']),scenario_sha256=sha(scenario)))
    for index in range(cfg['stress_seeds']):
        seed=generator.seed_for(phase,'stress',index)
        for scenario in generator.build_stress_cases(seed):
            family=scenario['family']
            rows.append(dict(case_id=f'stress_{family}_{index:03d}',phase=phase,
                track='stress',stratum=family,index=index,seed=seed,
                patient_count=len(scenario['patients']),scenario_sha256=sha(scenario)))
    assert len(rows)==cfg['main_cases']+cfg['stress_cases']
    assert len({r['case_id'] for r in rows})==len(rows)
    for i,row in enumerate(rows):
        offset=i%len(POLICY_IDS)
        row['execution_order']=list(POLICY_IDS[offset:]+POLICY_IDS[:offset])
    return rows


def report_path(phase):
    return RESULTS/f'final_assignment_{phase}_20261028.json'


def _phase_paths(phase):
    return dict(started=BUILD/(phase+'-started.json'),raw=BUILD/(phase+'-raw'),
        index=BUILD/(phase+'-raw-index.json'),failure=BUILD/(phase+'-failure.json'),
        report=report_path(phase),markdown=report_path(phase).with_suffix('.md'))


def prepare(checks_path):
    for name in ('manifest.json','source-freeze.json','validation-lock.json'):
        assert not (BUILD/name).exists(),'preparation cannot overwrite existing evidence'
    for phase in ('development','validation'):
        assert not any(path.exists() for path in _phase_paths(phase).values())
    checks_path=Path(checks_path).resolve();checks=read(checks_path)
    before=source_hashes();value=protocol()
    assert checks['passed'] is True and checks['failures']==checks['errors']==0
    assert checks.get('skips',checks.get('skipped',0))==0
    assert checks['source_sha256_before']==checks['source_sha256_after']==before
    private={checks_path.relative_to(ROOT).as_posix():digest(checks_path)}
    for relative,expected in checks.get('private_files_sha256',{}).items():
        assert digest(under(relative))==expected
        private[relative]=expected
    entries={phase:entries_for(phase,value) for phase in ('development','validation')}
    dev={r['seed'] for r in entries['development']};val={r['seed'] for r in entries['validation']}
    assert not dev&val and all(seed>=2**256 for seed in dev|val)
    assert source_hashes()==before
    manifest=dict(schema_version=1,experiment_id=EXPERIMENT_ID,protocol=value,
        protocol_sha256=digest(under(PROTOCOL_REL)),plan_sha256=digest(under(PLAN_REL)),
        source_sha256=before,binding_sha256=private,runtime_versions=runtime_versions(),
        entries=entries,controls_reused=False,all_three_arms_fresh=True,
        observed_development_outcomes_before_freeze=0,validation_outcomes_before_freeze=0,
        real_world_prevalence_claimed=False,whole_online_optimality_claimed=False,
        operational_default='fixed_3',operational_adoption=False)
    BUILD.mkdir(parents=True,exist_ok=True)
    write_json(BUILD/'manifest.json',manifest)
    write_json(BUILD/'source-freeze.json',dict(passed=True,manifest_sha256=digest(BUILD/'manifest.json'),
        source_sha256=before,protocol_sha256=manifest['protocol_sha256'],runtime_versions=manifest['runtime_versions'],
        candidate_branches_before_freeze=0,validation_outcomes_before_freeze=0))
    return manifest


def frozen():
    manifest=read(BUILD/'manifest.json');seal=read(BUILD/'source-freeze.json')
    assert seal['passed'] is True and seal['candidate_branches_before_freeze']==0
    assert seal['manifest_sha256']==digest(BUILD/'manifest.json')
    assert seal['source_sha256']==manifest['source_sha256']==source_hashes()
    assert manifest['protocol']==protocol() and manifest['runtime_versions']==runtime_versions()
    assert seal['runtime_versions']==manifest['runtime_versions']
    for relative,expected in manifest['binding_sha256'].items():assert digest(under(relative))==expected
    return manifest


def _loaded_sources_inside_freeze(manifest):
    actual=set()
    for module in list(sys.modules.values()):
        filename=getattr(module,'__file__',None)
        if not filename:continue
        path=Path(filename).resolve()
        if path.suffix=='.py' and path.is_relative_to(ROOT):
            relative=path.relative_to(ROOT).as_posix()
            if relative.startswith(('server/','simulation/')):actual.add(relative)
    assert actual<=set(manifest['source_sha256']), 'runtime project import absent from source freeze: '+str(actual-set(manifest['source_sha256']))


def _read_raw(entry,item,folder):
    assert item['case_id']==entry['case_id'] and item['scenario_sha256']==entry['scenario_sha256']
    path=under(item['raw_file']);assert path.parent==folder.resolve()
    assert digest(path)==item['compressed_sha256']
    body=gzip.decompress(path.read_bytes());assert hashlib.sha256(body).hexdigest()==item['raw_sha256']
    replay=json.loads(body);assert canonical(replay)==body
    original=regenerate(entry);assert sha(original)==entry['scenario_sha256']
    assert len(original['patients'])==entry['patient_count']
    if entry['track']=='main':
        for branch in replay['branches']:
            assert all(type(e.get('attempt')) is int and 1<=e['attempt']<=64
                for e in branch['events'] if e['type']=='ask'), 'main replay exhausted precomputed response attempts'
    result=auditor.audit_case(replay,expected_order=entry['execution_order'],
        scenario_sha256=entry['scenario_sha256'],source_scenario=original)
    return dict(case_id=entry['case_id'],track=entry['track'],stratum=entry['stratum'],
        seed=entry['seed'],scenario_sha256=entry['scenario_sha256'],
        raw_file=item['raw_file'],raw_sha256=item['raw_sha256'],compressed_sha256=item['compressed_sha256'],
        audit=result,recorded_allocation_wall_ms={branch['id']:[d['elapsed_seconds']*1000
            for d in branch['allocation_decisions']] for branch in replay['branches']})


def runtime_diagnostics(cases):
    groups=[]
    for track,stratum in sorted({(case['track'],case['stratum']) for case in cases}):
        rows=[case for case in cases if (case['track'],case['stratum'])==(track,stratum)]
        values={code:[wall for case in rows for wall in case['recorded_allocation_wall_ms'][code]]
            for code in POLICY_IDS}
        groups.append(dict(track=track,stratum=stratum,policies={code:dict(
            decisions=len(walls),wall_p95_ms=auditor.percentile(walls),wall_max_ms=max(walls,default=None))
            for code,walls in values.items()}))
    return dict(groups=groups,unit='milliseconds',wall_times_added_to_simulation_clock=False,
        hardware_specific=True,whole_invocation_strict_10_second_guarantee=False)


def markdown(report):
    lines=['# 회신순·거리순·H1 최종 비교', '',
        f"{report['phase']} · {len(report['cases'])}개 환경 · 세 규칙을 각각 새로 실행 · 합성 입력", '',
        '동시 문의3곳·첫 수용 뒤 최대2분 대기·변경 알림을 고정했다. H1은 최대2048회 탐색하며 10초 안전 상한 소진은 기술 실패다.', '',
        '| 평가 | 조건 | 환경 | 환자 | 규칙 | 미배정 | 미도착 | 도착 제한 평균(분) | 도착 제한 p95 | 실제 도착 p95(분모) | 문의 |',
        '|---|---|---:|---:|---|---:|---:|---:|---:|---|---:|']
    for group in report['analysis']['groups']:
        for code in POLICY_IDS:
            row=group['policies'][code]
            observed=row['conditional_arrival_p95_minutes']
            denominator=row['conditional_arrival_denominator']
            lines.append(f"| {group['track']} | {group['stratum']} | {group['cases']} | {group['patients']} | {code} | "
                f"{row['unassigned']} | {row['not_arrived']} | {row['arrival_capped_mean_minutes']:.3f} | "
                f"{row['arrival_capped_p95_minutes']:.3f} | {observed if observed is not None else '—'} ({denominator}) | {row['inquiries']} |")
    lines+=['','## H1 비교 판정','','| 평가 | 조건 | 기준 | 주 지표 차이 | 보정 구간 | 판정 | 공통 도착 지연 평균 | 최대 지연 |','|---|---|---|---|---|---|---|---|']
    for group in report['analysis']['groups']:
        for code,row in group['comparisons'].items():
            bootstrap=row.get('bootstrap');delta='—';interval='서술 통계'
            if bootstrap:
                primary=bootstrap['metrics']['scenario_mean_unassigned_priority_fraction']
                delta=f"{primary['mean_delta']:+.6f}"
                ci=primary['familywise_adjusted_ci'];interval=f"[{ci['lower']:+.6f}, {ci['upper']:+.6f}]"
            common=row['common_arrival']
            lines.append(f"| {group['track']} | {group['stratum']} | {code} | {delta} | {interval} | "
                f"{row['classification']} | {common['mean_delta_minutes']} | {common['maximum_delay_minutes']} |")
    lines+=['','주 평가는 층별 환경 동등 가중이며 스트레스와 합산하지 않는다. 위 환자 평균은 해당 층의 기술 통계다. 실패는 관측 종료 경과시간으로 제한 평균에 포함한다.',
        '기술 감사 통과와 효과 판정은 구분한다. 공통 도착자 비교는 미도착자를 제외한 조건부 결과이므로 성공·실패 변화와 함께 읽는다.',
        '전체 자원 이력 재생이나 온라인 최적성을 증명하지 않았다. H1 이전50ms설정과 달리2048회가 주 탐색 예산이다. 실제 병원·임상 효과가 아니다.',
        '통계 재현 검사는 동일 분석 구현의 재실행이며 별도의 독립 통계 수학 검증으로 주장하지 않는다.','']
    lines+=['## 배정 호출 계산시간','','| 평가 | 조건 | 규칙 | 결정 수 | p95(ms) | 최대(ms) |','|---|---|---|---:|---:|---:|']
    for group in report.get('runtime_diagnostics',{}).get('groups',[]):
        for code in POLICY_IDS:
            row=group['policies'][code]
            lines.append(f"| {group['track']} | {group['stratum']} | {code} | {row['decisions']} | {row['wall_p95_ms']} | {row['wall_max_ms']} |")
    lines+=['','계산시간은 해당 PC의 관측값이며 모의 도착시간에 가산하지 않았다.','']
    return '\n'.join(lines)


def execute(phase):
    assert phase in ('development','validation')
    manifest=frozen();paths=_phase_paths(phase)
    assert not any(path.exists() for path in paths.values()), 'phase cannot be rerun or overwrite evidence'
    if phase=='validation':
        lock=read(BUILD/'validation-lock.json')
        assert lock['passed'] is True and lock['manifest_sha256']==digest(BUILD/'manifest.json')
        assert lock['source_freeze_sha256']==digest(BUILD/'source-freeze.json')
        assert lock['development_report_sha256']==digest(report_path('development'))
        dev=read(report_path('development'))
        assert dev['technical_passed'] is True and len(dev['cases'])==len(manifest['entries']['development'])
        verification=read(BUILD/'development-verification.json')
        assert verification['passed'] is True and verification['phase']=='development'
        assert verification['report_sha256']==lock['development_report_sha256']
        assert verification['manifest_sha256']==lock['manifest_sha256']
        assert verification['source_freeze_sha256']==lock['source_freeze_sha256']
    entries=manifest['entries'][phase];started=time.perf_counter()
    write_json(paths['started'],dict(experiment_id=EXPERIMENT_ID,phase=phase,scheduled_cases=len(entries),
        scheduled_branches=len(entries)*3,manifest_sha256=digest(BUILD/'manifest.json'),
        source_freeze_sha256=digest(BUILD/'source-freeze.json'),workers=1,selective_retries_allowed=False))
    paths['raw'].mkdir();items=[];cases=[];current=None
    try:
        for entry in entries:
            current=entry;scenario=regenerate(entry)
            assert sha(scenario)==entry['scenario_sha256']
            try:
                replay=engine.run_final_assignment_replay(scenario,execution_order=entry['execution_order'])
            except Exception:
                replay=dict(execution_error=traceback.format_exc(),entry=entry,original_scenario=scenario,
                    completed_arms_unavailable=True,performance_valid=False)
            body=canonical(replay);packed=gzip.compress(body,compresslevel=6,mtime=0)
            path=paths['raw']/(entry['case_id']+'.json.gz')
            with path.open('xb') as stream:stream.write(packed)
            item=dict(case_id=entry['case_id'],scenario_sha256=entry['scenario_sha256'],
                raw_file=path.relative_to(ROOT).as_posix(),raw_sha256=hashlib.sha256(body).hexdigest(),
                compressed_sha256=hashlib.sha256(packed).hexdigest())
            items.append(item)
            assert 'execution_error' not in replay, 'engine failure capsule stored; no selective retry'
            cases.append(_read_raw(entry,item,paths['raw']))
            print(json.dumps(dict(phase=phase,done=len(cases),total=len(entries),case_id=entry['case_id'])),flush=True)
        write_json(paths['index'],dict(phase=phase,cases=items,all_scheduled_raws_saved=True))
        _loaded_sources_inside_freeze(manifest);assert frozen()==manifest
        analysis=importlib.import_module('simulation.final_assignment_analysis_20261028')
        report=dict(schema_version=1,experiment_id=EXPERIMENT_ID,phase=phase,synthetic=True,
            technical_passed=True,source_sha256=manifest['source_sha256'],runtime_versions=manifest['runtime_versions'],
            manifest_sha256=digest(BUILD/'manifest.json'),source_freeze_sha256=digest(BUILD/'source-freeze.json'),
            raw_index_sha256=digest(paths['index']),protocol_sha256=manifest['protocol_sha256'],
            cases=cases,audit_totals=auditor.audit_aggregate(dict(cases=cases)),
            analysis=analysis.analyze(cases,bootstrap_samples=manifest['protocol']['inference']['bootstrap_samples']),
            runtime_diagnostics=runtime_diagnostics(cases),
            elapsed_seconds=time.perf_counter()-started,workers=1,
            phase_candidate_branches=len(cases)*3,controls_reused=False,all_three_arms_fresh=True,
            source_before_after_equal=True,operational_adoption=False,real_world_prevalence_claimed=False,
            whole_online_optimality_claimed=False,full_resource_history_replayed=False)
        rendered=markdown(report)
        write_json(paths['report'],report);write_text(paths['markdown'],rendered)
        if phase=='development':
            write_json(BUILD/'validation-lock.json',dict(passed=True,
                manifest_sha256=digest(BUILD/'manifest.json'),source_freeze_sha256=digest(BUILD/'source-freeze.json'),
                development_report_sha256=digest(paths['report']),development_technical_passed=True,
                development_efficacy_is_unlock_condition=False,parameters_retuned_after_development=False))
        return report
    except Exception:
        write_json(paths['failure'],dict(phase=phase,passed=False,error=traceback.format_exc(),current_entry=current,
            stored_raws=items,completed_cases=cases,technical_passed=False,
            rerun_allowed=False,selective_retries_allowed=False,operational_adoption=False))
        raise


def verify(phase):
    manifest=frozen();paths=_phase_paths(phase);report=read(paths['report']);index=read(paths['index'])
    target=BUILD/(phase+'-verification.json');assert not target.exists()
    assert report['phase']==index['phase']==phase and report['technical_passed'] is True
    assert report['source_sha256']==manifest['source_sha256']
    assert report['manifest_sha256']==digest(BUILD/'manifest.json')
    assert report['source_freeze_sha256']==digest(BUILD/'source-freeze.json')
    assert report['raw_index_sha256']==digest(paths['index'])
    entries=manifest['entries'][phase];assert len(entries)==len(index['cases'])==len(report['cases'])
    cases=[_read_raw(entry,item,paths['raw']) for entry,item in zip(entries,index['cases'])]
    assert canonical(cases)==canonical(report['cases']), 'stored raw re-audit differs'
    assert canonical(auditor.audit_aggregate(dict(cases=cases)))==canonical(report['audit_totals'])
    assert canonical(runtime_diagnostics(cases))==canonical(report['runtime_diagnostics'])
    analysis=importlib.import_module('simulation.final_assignment_analysis_20261028')
    repeated=analysis.analyze(cases,bootstrap_samples=manifest['protocol']['inference']['bootstrap_samples'])
    assert canonical(repeated)==canonical(report['analysis']), 'deterministic statistical reproduction differs'
    assert paths['markdown'].read_text(encoding='utf-8')==markdown(report)
    assert frozen()==manifest
    value=dict(passed=True,phase=phase,cases_checked=len(cases),raw_decompressions=len(cases),
        source_sha256=manifest['source_sha256'],report_sha256=digest(paths['report']),
        raw_index_sha256=digest(paths['index']),manifest_sha256=digest(BUILD/'manifest.json'),
        source_freeze_sha256=digest(BUILD/'source-freeze.json'),new_policy_or_model_executions=0,
        source_before_after_equal=True,stored_events_and_current_decisions_reaudited=True,
        statistical_reproduction_same_implementation=True,independent_statistical_mathematics_verified=False,
        full_resource_history_replayed=False,whole_online_optimality_verified=False,operational_adoption=False)
    write_json(target,value);return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subs=parser.add_subparsers(dest='command',required=True)
    prep=subs.add_parser('prepare');prep.add_argument('--checks',type=Path,required=True)
    subs.add_parser('development');subs.add_parser('validation')
    check=subs.add_parser('verify');check.add_argument('--phase',choices=('development','validation'),required=True)
    args=parser.parse_args(argv)
    if args.command=='prepare':value=prepare(args.checks)
    elif args.command=='verify':value=verify(args.phase)
    else:value=execute(args.command)
    print(json.dumps(dict(command=args.command,passed=True,
        cases=len(value.get('cases',[])) if isinstance(value.get('cases'),list) else value.get('cases_checked'),
        source_count=len(value['source_sha256'])),ensure_ascii=False))


if __name__=='__main__':main()
