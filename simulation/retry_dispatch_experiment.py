"""Frozen development selection and fresh validation of inquiry progress/pacing.

References retain their exact preceding implementations. New inquiry variants
share the capability parent's foundation, physical resources and clinical rules.
The development research choice is separate from deployment eligibility.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import json
from pathlib import Path

from . import capability_probe_experiment as old
from .joint_policy_experiment import ROOT, SUMMARY_FIELDS, _raw, _sha, _write_new, compare, read_case, runtime_versions, summarize
from .retry_dispatch_engine import run_retry_dispatch_branch
from .retry_dispatch_scenarios import FAMILIES, ORIGINAL_FAMILIES, ADDITIONAL_FAMILIES, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, make_scenario

EXPERIMENT_ID='retry_dispatch_20261018'
VARIANTS=('frontier','paced','balanced')
REFERENCES=deepcopy(tuple(old.POLICIES))
NEW_SOURCES=('simulation/retry_dispatch_policy.py','simulation/retry_dispatch_engine.py',
    'simulation/retry_dispatch_audit.py','simulation/retry_dispatch_scenarios.py',
    'simulation/retry_dispatch_experiment.py','tests/research/test_retry_dispatch_policy.py',
    'tests/research/test_retry_dispatch_engine.py','tests/research/test_retry_dispatch_audit.py',
    'tests/research/test_retry_dispatch_scenarios.py','tests/research/test_retry_dispatch_experiment.py')
SOURCE_FILES=tuple(sorted(set(old.SOURCE_FILES)|set(NEW_SOURCES)))
EXTRA_FIELDS=('distinct_contact_pairs','repeated_contacts')
AGGREGATE_FIELDS=tuple(SUMMARY_FIELDS)+EXTRA_FIELDS
SELECTION_RULE=dict(research_population='development_only',maximum_primary_inquiry_ratio=1.0,
    require_case_inquiry_nonincrease=True,require_strict_total_inquiry_reduction=True,
    maximum_lost_treatments=0,maximum_individual_treatment_worsening_minutes=0,
    maximum_individual_final_arrival_worsening_minutes=0,maximum_case_total_wait_increase_minutes=0,
    maximum_case_capped_treatment_increase_minutes=0,require_strict_total_wait_reduction=True,
    inherited_additional_inquiry_ratio=1.10,
    inherited_individual_comparators=['safe','cooperative'],
    inherited_population_comparators=['probe','baseline'],
    fixed3_scope='reference only; original fixed3 has no ten-contact episode cap and different foundation',
    ranking=['primary_gate_passes','lost_treatments','individual_worsenings','case_wait_increases',
             'total_wait','capped_treatment','inquiries','less_policy_change'],
    fallback='retain fixed_3 live default; research choice may still fail deployment gates')
SETTINGS=dict(variants=list(VARIANTS),time_step_seconds=15,candidate_patient_open_cap=3,
    candidate_episode_contact_cap=10,reference_fixed3_episode_limit='original uncapped',
    original_episode_transition=True,original_timeouts_and_offer_ttl=True,
    physical_supply_and_workers_unchanged=True,clinical_priority_unchanged=True,
    additional_resource_report_channel=False,hidden_future_information=False,
    development_branch_rule='all twelve preceding references plus all six new variants',
    validation_branch_rule='same twelve references plus development-locked research variant for each allocation',
    identical_foundation_comparator='v4_{allocation}_capability',
    isolated_simulation=True,live_server_ui_db_or_default_changed=False,
    selection_rule=deepcopy(SELECTION_RULE),prior_validation_now_regression_only=True,
    validation_not_used_for_tuning=True)

def candidate_id(allocation,variant):
    if allocation not in ('H1','C1') or variant not in VARIANTS: raise ValueError('unknown retry variant')
    return f'retry_{allocation}_{variant}'

def policies_for(phase,selection=None):
    if phase not in ('development','validation'): raise ValueError('unknown phase')
    if phase=='validation' and (not isinstance(selection,dict) or set(selection)!= {'H1','C1'}):
        raise ValueError('validation needs development-locked choices')
    candidates=[dict(id=candidate_id(a,v),allocation=a,variant=v) for a in ('H1','C1')
        for v in (VARIANTS if phase=='development' else (selection[a]['variant'],))]
    return deepcopy(list(REFERENCES))+candidates

def pairs_for(policies):
    pairs=list(old.PAIRS)
    for p in policies:
        if 'variant' not in p: continue
        a,v=p['allocation'],p['variant']
        for tag,stage in (('current','capability'),('safe','safe'),('cooperative','cooperative'),
                          ('prototype','probe'),('protected','baseline')):
            pairs.append((f'{v}_{tag}_{a}',f'v4_{a}_{stage}',p['id']))
        pairs.append((f'{v}_fixed3_reference_{a}',f'fixed3_{a}_baseline',p['id']))
    return tuple(pairs)

def protocol():
    return dict(experiment_id=EXPERIMENT_ID,families=list(FAMILIES),original_families=list(ORIGINAL_FAMILIES),
        additional_families=list(ADDITIONAL_FAMILIES),development_seeds=list(DEVELOPMENT_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS),settings=deepcopy(SETTINGS),
        source_sha256={n:_sha((ROOT/n).read_bytes()) for n in SOURCE_FILES},runtime_versions=runtime_versions())

def summarize_branch(scenario,branch):
    row=summarize(scenario,branch)
    asks=[e for e in branch['events'] if e['type']=='ask']
    row['distinct_contact_pairs']=len({(e['patient'],e['hospital']) for e in asks})
    row['repeated_contacts']=len(asks)-row['distinct_contact_pairs']
    return row

def totals(cases,policies):
    return {p['id']:{f:sum(c['summaries'][p['id']][f] for c in cases) for f in AGGREGATE_FIELDS} for p in policies}

def combined_pairs(cases,pairs):
    fields=('patients','improved','worsened','same','newly_treated','lost_treatments','total_wait_delta_minutes',
        'capped_treatment_delta_minutes','inquiry_delta','selection_improved','selection_worsened',
        'final_arrival_improved','final_arrival_worsened')
    maximum=('maximum_treatment_worsening_minutes','maximum_selection_worsening_minutes','maximum_final_arrival_worsening_minutes')
    return {name:dict(before=a,after=b,
        **{f:sum(c['comparisons'][name][f] for c in cases) for f in fields},
        **{f:max((c['comparisons'][name][f] for c in cases),default=0) for f in maximum},
        cases_with_longer_total_wait=sum(c['comparisons'][name]['total_wait_delta_minutes']>0 for c in cases),
        cases_with_longer_capped_treatment=sum(c['comparisons'][name]['capped_treatment_delta_minutes']>0 for c in cases),
        cases_with_more_inquiries=sum(c['comparisons'][name]['inquiry_delta']>0 for c in cases)) for name,a,b in pairs}

def checks(cases,allocation,variant,stage='capability',*,primary=False):
    before=f'v4_{allocation}_{stage}'; after=candidate_id(allocation,variant)
    changes=[compare(c['summaries'][before],c['summaries'][after]) for c in cases]
    inquiries=sum(c['summaries'][after]['inquiries'] for c in cases)
    reference=sum(c['summaries'][before]['inquiries'] for c in cases)
    result=dict(no_lost_treatments=all(c['lost_treatments']==0 for c in changes),
        no_individual_treatment_worsening=all(c['worsened']==0 for c in changes),
        no_individual_final_arrival_worsening=all(c['final_arrival_worsened']==0 for c in changes),
        no_case_total_wait_increase=all(c['total_wait_delta_minutes']<=0 for c in changes),
        no_case_capped_treatment_increase=all(c['capped_treatment_delta_minutes']<=0 for c in changes),
        bounded_inquiry_cost=inquiries<=reference*(1.0 if primary else 1.10),
        strict_total_wait_reduction=sum(c['total_wait_delta_minutes'] for c in changes)<0)
    if primary:
        result.update(no_case_inquiry_increase=all(c['inquiry_delta']<=0 for c in changes),
            strict_total_inquiry_reduction=inquiries<reference)
    return result

def assess(cases,allocation,variant):
    primary=checks(cases,allocation,variant,primary=True)
    inherited={s:checks(cases,allocation,variant,s) for s in ('safe','cooperative','probe','baseline')}
    population=('no_lost_treatments','no_case_total_wait_increase','no_case_capped_treatment_increase',
                'bounded_inquiry_cost','strict_total_wait_reduction')
    gates=dict(vs_current=all(primary.values()),vs_previous_safe=all(inherited['safe'].values()),
        vs_previous_cooperative=all(inherited['cooperative'].values()),
        vs_previous_probe_population=all(inherited['probe'][k] for k in population),
        vs_protected_population=all(inherited['baseline'][k] for k in population))
    before=f'v4_{allocation}_capability';after=candidate_id(allocation,variant)
    changes=[compare(c['summaries'][before],c['summaries'][after]) for c in cases]
    ranking=(not gates['vs_current'],sum(c['lost_treatments'] for c in changes),
        sum(c['worsened'] for c in changes),sum(c['total_wait_delta_minutes']>0 for c in changes),
        sum(c['summaries'][after]['total_wait_sum_minutes'] for c in cases),
        sum(c['summaries'][after]['request_to_treatment_capped_sum_minutes'] for c in cases),
        sum(c['summaries'][after]['inquiries'] for c in cases),VARIANTS.index(variant))
    return dict(variant=variant,policy=after,primary_checks=primary,inherited_checks=inherited,
        gates=gates,primary_passed=gates['vs_current'],deployment_eligible=all(gates.values()),ranking=list(ranking))

def select_policy(cases):
    result={}
    for allocation in ('H1','C1'):
        candidates=[assess(cases,allocation,v) for v in VARIANTS]
        selected=min(candidates,key=lambda r:r['ranking'])
        result[allocation]=dict(**selected,selection_scope='development research choice only',
            all_candidates=candidates,live_default='fixed_3',adopted=False)
    return result

def verify_predevelopment_freeze(freeze_path=None):
    path=Path(freeze_path) if freeze_path is not None else ROOT/'.codex-build/retry-dispatch-20261018/pre-development-freeze.json'
    if not path.is_file(): raise ValueError('source/seed/input freeze is required before reserved execution')
    frozen=json.loads(path.read_bytes())
    if not frozen.get('passed') or frozen.get('frozen_protocol')!=protocol():
        raise ValueError('pre-development sources/settings differ')
    evidence=frozen.get('evidence_sha256',{})
    mandatory={'source-freeze.json','scenario-input-manifest.json','tests.json',
               'preservation-before.json','seed-audit-history.json','seed-audit-late-coverage.json'}
    if not mandatory<=set(evidence): raise ValueError('missing seed/input/preservation evidence')
    for name,value in evidence.items():
        target=(path.parent/name).resolve()
        if not target.is_relative_to(path.parent.resolve()) or _sha(target.read_bytes())!=value:
            raise ValueError('frozen prerequisite evidence changed: '+name)
    return frozen

def verify_lock(lock,*,verify_artifacts=True):
    if lock.get('protocol')!=protocol(): raise ValueError('locked sources/settings/seeds/runtime changed')
    path=Path(lock['development_report']);data=path.read_bytes()
    if _sha(data)!=lock['development_report_sha256']: raise ValueError('development report changed')
    report=json.loads(data)
    if select_policy(report['cases'])!=lock['selected_policy']: raise ValueError('development selection changed')
    if verify_artifacts:
        for index,c in enumerate(report['cases']):
            replay=read_case(c,report['artifact_directory'])
            if _sha(_raw(replay['scenario']))!=c['original_scenario_sha256']: raise ValueError('raw input changed')
            if {b['id']:summarize_branch(replay['scenario'],b) for b in replay['branches']}!=c['summaries']:
                raise ValueError('raw outcome summary changed')
            if (index+1)%50==0: print(f'lock raw verification {index+1}/{len(report["cases"])}',flush=True)

def markdown(report):
    lines=['# 재문의 순회·대기·관측 부하 배분 실험','',
        f"합성 {report['phase']} · {len(report['cases'])}환경 · {report['runs']}분기",'',
        '동일 foundation의 직전 capability가 주 비교군이다. 고정3곳은 원래 회차10회 한도가 없는 참고 기준이다.',
        '과거 VAL은 회귀 참고이며, 새 VAL로 정책이나 임계값을 선택하지 않는다. 연구 선택과 운영 반영은 별개다.','',
        '| 분기 | 환자 | 관찰시간 내 미처치 | 전체 대기 합 | 요청→처치 제한 합 | 문의 | 중복 접촉 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for code,r in report['totals'].items():
        lines.append(f"| {code} | {r['patients']} | {r['untreated']} | {r['total_wait_sum_minutes']:.2f} | {r['request_to_treatment_capped_sum_minutes']:.2f} | {r['inquiries']} | {r['repeated_contacts']} |")
    lines+=['','| 비교 | 단축 | 동일 | 지연 | 최대 지연 | 기존 성공 상실 | 새 성공 | 대기 변화 | 대기 증가 환경 | 문의 변화 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for code,r in report['comparisons'].items():
        lines.append(f"| {code} | {r['improved']} | {r['same']} | {r['worsened']} | {r['maximum_treatment_worsening_minutes']:.2f} | {r['lost_treatments']} | {r['newly_treated']} | {r['total_wait_delta_minutes']:+.2f} | {r['cases_with_longer_total_wait']} | {r['inquiry_delta']:+} |")
    lines+=['','개발에서 고정한 연구 선택:','', '```json',json.dumps(report['selected_policy'],ensure_ascii=False,indent=2),'```','',
        'validation_diagnostics는 고정한 선택의 검증 결과이며 재선택이 아니다. 운영 기본 fixed_3는 변경하지 않았다.',
        '대기는 이동을 제외한 탐색·병원 대기·전원 준비 합이다. 미처치는 같은 관찰 종료로 제한한다.',
        '협력형 부모의 ACK 지연과 작업 전환 비용 0분 가정은 그대로 남아 있다. 실제 임상 효과·범용 최적성 주장이 아니다.','']
    return '\n'.join(lines)

def execute(phase,output,lock_path):
    from .retry_dispatch_audit import audit_branch
    output,lock_path=Path(output),Path(lock_path)
    if phase not in ('development','validation'): raise ValueError('unknown phase')
    report_path=output.with_suffix('.json');artifact_dir=output.parent/(output.name+'_cases')
    if any(p.exists() for p in (report_path,output.with_suffix('.md'),artifact_dir)):
        raise ValueError('evidence cannot be overwritten')
    if phase=='development' and lock_path.exists(): raise ValueError('development lock already exists')
    verify_predevelopment_freeze()
    lock=json.loads(lock_path.read_bytes()) if phase=='validation' else None
    if lock: verify_lock(lock)
    frozen=protocol();policies=policies_for(phase,None if lock is None else lock['selected_policy'])
    ids=tuple(p['id'] for p in policies);by_id={p['id']:p for p in policies};pairs=pairs_for(policies)
    seeds=DEVELOPMENT_SEEDS if phase=='development' else VALIDATION_SEEDS
    artifact_dir.mkdir(parents=True);_write_new(artifact_dir/'protocol.json',_raw(frozen));cases=[]
    for si,seed in enumerate(seeds):
        for fi,family in enumerate(FAMILIES):
            scenario=make_scenario(seed,family);offset=(si+fi)%len(ids);order=ids[offset:]+ids[:offset]
            branches={}
            for code in order:
                p=by_id[code]
                if 'variant' in p:
                    branch=run_retry_dispatch_branch(scenario,allocation=p['allocation'],variant=p['variant'],branch_id=code,label=code)
                else:
                    branch=old.run_capability_probe_replay(scenario,policies=[deepcopy(p)],execution_order=[code])['branches'][0]
                branches[code]=branch
            replay=dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=deepcopy(scenario),
                branches=[branches[code] for code in ids],execution_order=list(order))
            summaries,audits={},{}
            for code in ids:
                branch=branches[code];audits[code]=audit_branch(scenario,branch)
                if not audits[code]['passed']: raise ValueError('raw branch audit failed')
                summaries[code]=summarize_branch(scenario,branch)
                if branch.get('resource_updates_enabled') is not False or any(summaries[code][k]
                    for k in ('added_resource_publications','added_resource_update_receipts','added_resource_update_cycles')):
                    raise ValueError('extra resource report channel forbidden')
            raw=_raw(replay);zipped=gzip.compress(raw,compresslevel=6,mtime=0);name=f'{seed}_{fi:02d}_{family}.json.gz'
            _write_new(artifact_dir/name,zipped)
            cases.append(dict(seed=seed,family=family,file=name,compressed_sha256=_sha(zipped),raw_sha256=_sha(raw),
                original_scenario_sha256=_sha(_raw(scenario)),execution_order=list(order),summaries=summaries,audits=audits,
                comparisons={n:compare(summaries[a],summaries[b]) for n,a,b in pairs}))
            print(f'{phase} {len(cases)}/{len(seeds)*len(FAMILIES)} {seed} {family}',flush=True)
    if protocol()!=frozen: raise ValueError('sources or settings changed during execution')
    selected=select_policy(cases) if lock is None else lock['selected_policy']
    diagnostics=None if lock is None else {a:assess(cases,a,selected[a]['variant']) for a in ('H1','C1')}
    report=dict(schema_version=1,experiment_id=EXPERIMENT_ID,phase=phase,synthetic=True,protocol=frozen,
        artifact_directory=str(artifact_dir.resolve()),policies=policies,pairs=[list(p) for p in pairs],cases=cases,
        runs=len(cases)*len(policies),totals=totals(cases,policies),comparisons=combined_pairs(cases,pairs),
        selected_policy=selected,validation_diagnostics=diagnostics,
        families=[dict(family=f,cases=len(group),totals=totals(group,policies),comparisons=combined_pairs(group,pairs))
            for f in FAMILIES if (group:=[c for c in cases if c['family']==f])])
    report['contributions']={label:dict(cases=len(group),totals=totals(group,policies),comparisons=combined_pairs(group,pairs))
        for label,group in (('inherited_95',[c for c in cases if c['family'] in ORIGINAL_FAMILIES]),
                           ('new_retry_boundaries',[c for c in cases if c['family'] in ADDITIONAL_FAMILIES]))}
    raw=_raw(report);_write_new(report_path,raw);_write_new(output.with_suffix('.md'),markdown(report).encode('utf-8'))
    if lock is None:
        _write_new(lock_path,_raw(dict(protocol=frozen,development_report=str(report_path.resolve()),
            development_report_sha256=_sha(raw),selected_policy=selected)))
    else: verify_lock(lock,verify_artifacts=False)
    print(json.dumps(dict(output=str(report_path),cases=len(cases),runs=report['runs'],
        choices={a:dict(variant=r['variant'],primary_passed=r['primary_passed'],deployment_eligible=r['deployment_eligible'])
                 for a,r in selected.items()}),ensure_ascii=False),flush=True)
    return report

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('development','validation'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--lock',type=Path,default=ROOT/'config/retry_dispatch_lock_20261018.json')
    args=parser.parse_args(argv);execute(args.phase,args.output,args.lock)

if __name__=='__main__': main()
