"""Extract a small casebook from existing locked records; stdlib only.

No policy/simulator is imported or executed. Existing evidence and output files
are never rewritten. The generated globalThis payload also works via file://.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STUDY='outputs/waiting_protection_20261030/study-v1'
RECEIPT='outputs/waiting_protection_20261030/independent-audit-v1/receipt.json'
DECISION='config/waiting_decision_20261030.json'
POLICIES=('R0','R1','H1','W1')
CASE_SPECS=(
    dict(id='h1-improvement',index=21,profile='scarce_capacity',profile_label='자원이 부족한 동시 발생',
        patients=['P17','P04'],baseline='R1',candidate='H1',deltas=[-77,51],
        title='한 환자는77분 빨라지고 다른 환자는51분 늦어진 사례',
        explanation='같은 실험 가중치4 환자 중 현재 이동시간이 짧은 환자를 먼저 선택했다. P17의 도착은77분 빨라졌지만 P04는51분 늦어졌다.',
        caveat='특정 환자의 단축을 전체 개선으로 제시하지 않는다. 실험 가중치4는 임상적으로 검증한 중증도 등급이 아니다.'),
    dict(id='h1-worsening',index=16,profile='simultaneous',profile_label='동시 발생',
        patients=['P05','P17'],baseline='R1',candidate='H1',deltas=[67,-39],
        title='가중치가 다른 환자가 경합하며67분 지연이 남은 사례',
        explanation='H1은 현재 미배정 가중치 합을 먼저 줄이는 선택을 했다. P17은39분 빨라졌지만 P05의67분 지연은 W1에서도 그대로 남았다.',
        caveat='현재 배치의 목적함수 개선이 모든 환자의 최종 도착 개선을 보장하지 않는다.'),
    dict(id='w1-waiting',index=3,profile='sparse',profile_label='병원이 적고 거리가 긴 환경',
        patients=['P02','P05'],baseline='H1',candidate='W1',deltas=[-67,26],
        title='기다린 환자를 보호했지만 다른 환자에게26분을 전가한 사례',
        explanation='W1의 과거 대기시간 보호로 P02는 H1보다67분 빨리 도착했다. 같은 기록에서 P05는26분 늦어졌다.',
        caveat='이미 관측한 개발 사례의 부분 개선이다. 이 사례로 신규 검증 성공이나 운영 채택을 주장하지 않는다.'),
    dict(id='w1-transfer',index=31,profile='sparse_adverse',profile_label='병원이 적고 미응답·지연·자원 중단이 겹친 환경',
        patients=['P05','P12'],baseline='H1',candidate='W1',deltas=[-60,83],
        title='60분 단축과83분 악화가 함께 생긴 사례',
        explanation='W1에서 P05는 H1보다60분 빨라졌지만 P12는83분 늦어졌다. 개선을 다른 환자의 지연과 함께 보여준다.',
        caveat='이 악화 때문에 개인에게 지연을 전가하지 않는 사전 연구 보호 기준을 통과하지 못했다.'),
    dict(id='zero-capped-loss',index=27,profile='arrival_condition',profile_label='도착 평가 후 추가 조건',
        patients=['P19'],baseline='R0',candidate='W1',deltas=[0],
        title='시간차0이 도착·치료 성공 상실을 가리는 사례',
        explanation='R0는 관측 종료인 t180에 도착·치료를 시작했다. W1은 끝까지 도착하지 못했다. 두 도착 제한값은126분으로 같지만 성공 결과는 다르다.',
        caveat='미도착의 제한값은 실제 도착시간이 아니다. 이 상실은 H1에서도 있었고 W1이 새로 만든 악화로 해석하지 않는다.'),
)


def require(condition,message):
    if not condition:raise ValueError(message)


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            require(key not in result,'duplicate JSON key')
            result[key]=value
        return result
    def reject(value):raise ValueError('nonfinite JSON constant: '+value)
    return json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=unique,parse_constant=reject)


def contained(root,name):
    require(isinstance(name,str) and not Path(name).is_absolute() and '\\' not in name
        and ':' not in name and '..' not in Path(name).parts,'invalid evidence relative path')
    path=(Path(root)/name).resolve()
    require(path.is_relative_to(Path(root).resolve()),'evidence path escapes root')
    return path


def recorded_endpoints(scenario,branch):
    """Reconstruct endpoints, then compare with independently audited vectors."""
    specs={p['id']:p for p in scenario['patients']}
    require(len(specs)==len(scenario['patients']),'duplicate patient')
    end=branch['metrics']['observation_end_t']
    require(type(end) is int and end==scenario['max_minutes'],'observation end differs')
    rows={pid:dict(selected_t=None,arrive_t=None,treatment_start_t=None) for pid in specs}
    previous=-1
    for event in branch['events']:
        at=event['t'];require(type(at) is int and previous<=at<=end,'invalid event time/order')
        previous=at;row=rows.get(event.get('patient'))
        if row is None:continue
        if event['type']=='commit' and row['selected_t'] is None:row['selected_t']=at
        elif event['type']=='arrive':row['arrive_t']=at
        elif event['type']=='arrival_unable':row['arrive_t']=None
        elif event['type']=='treatment_start':row['treatment_start_t']=at
    outcomes={p['id']:p for p in branch['patient_outcomes']}
    require(set(outcomes)==set(specs),'recorded outcome partition differs')
    result={}
    for pid,row in rows.items():
        spec=specs[pid];outcome=outcomes[pid]
        for our,their in [('selected_t','first_commit_t'),('arrive_t','final_arrive_t'),('treatment_start_t','treatment_start_t')]:
            require(row[our]==outcome[their],'raw endpoint/outcome differs')
        result[pid]=dict(**row,selected=row['selected_t'] is not None,arrived=row['arrive_t'] is not None,
            treated=row['treatment_start_t'] is not None,observation_end_t=end,
            destination_hospital_id=outcome.get('destination_hospital_id'),
            arrival_capped_minutes=(row['arrive_t'] if row['arrive_t'] is not None else end)-spec.get('occur_t',0))
    return result


def comparison(before,after):
    return dict(delta_capped_minutes=after['arrival_capped_minutes']-before['arrival_capped_minutes'],
        actual_arrival_delta_minutes=after['arrive_t']-before['arrive_t'] if before['arrived'] and after['arrived'] else None,
        success_lost=[key for key in ('selected','arrived','treated') if before[key] and not after[key]],
        success_gained=[key for key in ('selected','arrived','treated') if not before[key] and after[key]])


def build_casebook(root=ROOT):
    root=Path(root).resolve();protected={}
    def track(name,expected=None):
        path=contained(root,name);actual=digest(path)
        require(expected is None or actual==expected,'evidence checksum differs: '+name)
        protected[name]=actual
        return path
    report_path=track(STUDY+'/report.json');report=read(report_path)
    lock_path=track(STUDY+'/execution-lock.json');lock=read(lock_path)
    receipt_path=track(RECEIPT);receipt=read(receipt_path)
    decision_path=track(DECISION);decision=read(decision_path)
    selection=read(track(STUDY+'/selection-review.json'))
    verification=read(track(STUDY+'/verification.json'))
    require(report['passed'] is True and receipt['passed'] is True and verification['passed'] is True,'source run/audit did not pass')
    require(receipt['report_sha256']==protected[STUDY+'/report.json']
        and receipt['lock_sha256']==protected[STUDY+'/execution-lock.json']
        and verification['report_sha256']==protected[STUDY+'/report.json'],'audit receipts do not bind this report/lock')
    require(report['lock_sha256']==protected[STUDY+'/execution-lock.json']
        and report['protocol_sha256']==hashlib.sha256(canonical(lock['protocol'])).hexdigest(),'report protocol binding differs')
    require(selection['authoritative_decision_path']==DECISION
        and selection['authoritative_decision_sha256']==protected[DECISION]
        and selection['independent_receipt_path']==RECEIPT
        and selection['independent_receipt_sha256']==protected[RECEIPT],'waiting decision/receipt binding differs')
    require(decision['status']=='not_adopted' and decision['research_recommendation_eligible'] is False
        and decision['operational_policy_changed'] is False,'decision is not the recorded retained-policy decision')
    for reference in decision['references']:track(reference['path'],reference['sha256'])
    for name,checksum in lock['sources'].items():track(STUDY+'/sources/'+name,checksum)
    track(STUDY+'/seed-inventory.json',lock['seed_inventory_sha256'])
    require(len(report['cases'])==len(report['raw_index'])==len(lock['entries'])==96,'complete original schedule required')
    receipt_raw={p['path']:p for p in receipt['raw_hashes']}
    records=[];phase_rows={phase:{code:[] for code in POLICIES} for phase in ('development_known','validation_new')}
    phase_counts=Counter();fresh_equal=fresh_dest_equal=both_arrived=both_missing=0
    for index,(case,item,entry) in enumerate(zip(report['cases'],report['raw_index'],lock['entries'])):
        require(item==receipt_raw.get(item['path']),'raw index differs from independent receipt')
        packed=track(STUDY+'/'+item['path'],item['sha256']).read_bytes();body=gzip.decompress(packed)
        require(hashlib.sha256(body).hexdigest()==item['raw_sha256'],'decompressed raw checksum differs')
        raw=json.loads(body);scenario=raw['original_scenario']
        input_path=track(STUDY+f'/inputs/{index:03}.json');input_data=read(input_path)
        require(hashlib.sha256(canonical(scenario)).hexdigest()==entry['scenario_sha256']
            and canonical(input_data)==canonical(scenario),'raw/input differs from original lock')
        require(case['phase']==entry['phase'] and case['profile']==entry['profile']
            and raw['execution_order']==entry['execution_order'],'phase/profile/order differs')
        phase=entry['phase'];phase_counts[phase]+=1
        branches={b['id']:b for b in raw['branches']}
        require(set(branches)==set(POLICIES) and len(raw['branches'])==4,'four-policy partition differs')
        endpoints={code:recorded_endpoints(scenario,branches[code]) for code in POLICIES}
        specs={p['id']:p for p in scenario['patients']}
        for code in POLICIES:
            saved={p['id']:p for p in case['audit']['patient_vectors'][code]}
            require(set(saved)==set(specs),'saved patient partition differs')
            for pid,point in endpoints[code].items():
                for field,stored in [('selected_t','first_commit_t'),('arrive_t','final_arrive_t'),
                    ('treatment_start_t','treatment_start_t'),('arrival_capped_minutes','arrival_capped_minutes'),
                    ('selected','selected'),('arrived','arrived')]:
                    require(point[field]==saved[pid][stored],'endpoint differs from audited report')
                phase_rows[phase][code].append(dict(priority=specs[pid]['priority'],**point))
        if phase=='validation_new':
            for pid in specs:
                a,b=endpoints['H1'][pid],endpoints['W1'][pid]
                fields=('selected_t','arrive_t','treatment_start_t','arrival_capped_minutes','selected','arrived','treated','observation_end_t')
                fresh_equal+=all(a[key]==b[key] for key in fields)
                fresh_dest_equal+=all(a[key]==b[key] for key in (*fields,'destination_hospital_id'))
                both_arrived+=a['arrived'] and b['arrived'];both_missing+=not a['arrived'] and not b['arrived']
        if phase=='development_known' and index in {s['index'] for s in CASE_SPECS}:
            records.append(dict(index=index,scenario=scenario,endpoints=endpoints,item=item,entry=entry))
    require(dict(phase_counts)=={'development_known':36,'validation_new':60},'cohort counts differ')
    cases=[]
    for spec in CASE_SPECS:
        source=next(r for r in records if r['index']==spec['index'])
        require(source['entry']['profile']==spec['profile'],'case profile label differs from locked source')
        patients=[]
        for pid,expected_delta in zip(spec['patients'],spec['deltas']):
            p=next(p for p in source['scenario']['patients'] if p['id']==pid)
            points={code:source['endpoints'][code][pid] for code in POLICIES}
            pairs={candidate+'_minus_'+baseline:comparison(points[baseline],points[candidate])
                for baseline,candidate in [('R1','H1'),('H1','W1'),('R1','W1'),('R0','W1')]}
            require(pairs[spec['candidate']+'_minus_'+spec['baseline']]['delta_capped_minutes']==expected_delta,
                'expected explanatory case delta differs from verified source')
            patients.append(dict(id=pid,priority=p['priority'],occur_t=p.get('occur_t',0),search_start_t=p['search_start_t'],
                observation_end_t=source['scenario']['max_minutes'],endpoints=points,comparisons=pairs))
        cases.append(dict(id=spec['id'],title=spec['title'],profile=spec['profile'],profile_label=spec['profile_label'],
            explanation=spec['explanation'],caveat=spec['caveat'],phase='development_known',
            comparison=dict(baseline=spec['baseline'],candidate=spec['candidate']),
            preferred_comparison=spec['candidate']+'_minus_'+spec['baseline'],
            raw_path=STUDY+'/'+source['item']['path'],raw_sha256=source['item']['sha256'],
            raw_body_sha256=source['item']['raw_sha256'],scenario_sha256=source['entry']['scenario_sha256'],
            observation_end_t=source['scenario']['max_minutes'],patients=patients))
    def counts(rows):
        return dict(patients=len(rows),selected=sum(p['selected'] for p in rows),unassigned=sum(not p['selected'] for p in rows),
            arrived=sum(p['arrived'] for p in rows),not_arrived=sum(not p['arrived'] for p in rows),
            treated=sum(p['treated'] for p in rows),untreated=sum(not p['treated'] for p in rows))
    study_summaries={phase:dict(environments=phase_counts[phase],patients_per_policy=len(policies['H1']),
        research_gate_passed=receipt['phase_gates'][phase]['research_recommendation_eligible'],
        policies={code:dict(**counts(rows),priority4=counts([p for p in rows if p['priority']==4])) for code,rows in policies.items()})
        for phase,policies in phase_rows.items()}
    def delta_summary(phase,reference):
        pairs=receipt['phase_gates'][phase]['comparisons'][reference]['patient_differences']
        return dict(improved=sum(p['delta_capped_minutes']<0 for p in pairs),worsened=sum(p['delta_capped_minutes']>0 for p in pairs),
            equal=sum(p['delta_capped_minutes']==0 for p in pairs),maximum_positive_minutes=max([0]+[p['delta_capped_minutes'] for p in pairs]))
    fresh=receipt['phase_gates']['validation_new'];dev=delta_summary('development_known','H1')
    summary=dict(environments=60,patients=study_summaries['validation_new']['patients_per_policy'],
        H1_W1_endpoint_equal=fresh_equal,H1_W1_endpoint_and_destination_equal=fresh_dest_equal,
        both_arrived=both_arrived,both_not_arrived=both_missing,
        max_W1_minus_R1=fresh['comparisons']['R1']['maximum_positive_capped_difference'],
        priority4_max_W1_minus_R1=fresh['by_priority']['R1']['4']['maximum_positive_capped_difference'],
        max_W1_minus_H1=fresh['comparisons']['H1']['maximum_positive_capped_difference'],
        improved_vs_H1=delta_summary('validation_new','H1')['improved'],
        research_gate_passed=fresh['research_recommendation_eligible'],
        known_development=dict(environments=36,patients=603,improved_vs_H1=dev['improved'],
            worsened_vs_H1=dev['worsened'],equal=dev['equal'],max_W1_minus_H1=dev['maximum_positive_minutes']),
        equality_scope='최초 선정·최종 도착·치료시작·관측 종료·성공 표시·도착 제한값의 동일성이다. 전체 경로나 임상 결과의 동일성을 뜻하지 않는다.')
    require((summary['patients'],fresh_equal,both_arrived,both_missing)==(987,987,902,85),'fresh endpoint denominator differs')
    require((summary['max_W1_minus_R1'],summary['priority4_max_W1_minus_R1'])==(124,112),'fresh maxima differ')
    data=dict(schema_version=1,id='demo-casebook-20261030-v1',policies=list(POLICIES),
        generated_from=dict(study_directory=STUDY,study_report_sha256=protected[STUDY+'/report.json'],
            execution_lock_sha256=protected[STUDY+'/execution-lock.json'],independent_receipt_sha256=protected[RECEIPT],
            waiting_decision_sha256=protected[DECISION],verified_raw_cases=96,verified_archived_sources=len(lock['sources'])),
        scope=dict(recorded_replay_only=True,new_policy_executions=0,synthetic=True,
            algorithm_work_closed=True,final_presentation_preparation=False,
            operating_policy='fixed_3 + CP-SAT severity_first (3초) 유지',
            W1_adopted=False,clinical_safety_validated=False,real_hospital_requests=0,
            limitation='기존 합성 개발·검증 기록의 시연 정리다. 새 실험·현장 효과·전체 온라인 최적성·최종 발표 준비 완료를 뜻하지 않는다.',
            time_unit='integer simulated minutes',time_origin='absolute scenario t; elapsed arrival is t - occur_t',
            capped_time='미도착은 관측 종료 - 발생시각으로 표시하며 실제 도착시간이 아니다.'),
        cases=cases,study_summaries=study_summaries,fresh_summary=summary,
        limitations=decision['limitations'])
    return data,protected


def write_casebook(output,viewer_script,*,root=ROOT):
    root=Path(root).resolve();output=Path(output).resolve();viewer_script=Path(viewer_script).resolve()
    require(output.is_relative_to(root) and viewer_script.is_relative_to(root),'outputs must remain inside the project root')
    if output.exists() or viewer_script.exists():raise FileExistsError('Use a new output directory and a new viewer script path')
    data,protected=build_casebook(root)
    for name,expected in protected.items():require(digest(root/name)==expected,'source changed during extraction: '+name)
    output.mkdir(parents=True);viewer_script.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    (output/'casebook.json').write_text(payload,encoding='utf-8')
    with viewer_script.open('x',encoding='utf-8',newline='\n') as stream:
        stream.write('// Generated from verified existing records; no new policy execution.\n')
        stream.write('globalThis.DEMO_CASEBOOK = '+payload.rstrip().replace('\u2028','\\u2028').replace('\u2029','\\u2029')+';\n')
    (output/'extractor-source.py').write_bytes(Path(__file__).read_bytes())
    for name,expected in protected.items():require(digest(root/name)==expected,'source changed while saving casebook: '+name)
    manifest=dict(schema_version=1,new_policy_executions=0,original_sources_unchanged=True,
        source_files=protected,artifacts={'casebook.json':digest(output/'casebook.json'),
            'extractor-source.py':digest(output/'extractor-source.py')},
        viewer_script=viewer_script.relative_to(root).as_posix(),viewer_script_sha256=digest(viewer_script))
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return data


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--viewer-script',required=True,type=Path)
    args=parser.parse_args(argv);data=write_casebook(args.output,args.viewer_script)
    print(json.dumps(dict(cases=len(data['cases']),fresh_patients=data['fresh_summary']['patients'],new_policy_executions=0)))


if __name__=='__main__':main()
