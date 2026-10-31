"""Evidence-bound, post-hoc policy decision report; no policy is executed/switched.

python -m simulation.algorithm_decision_20261030 --output outputs/algorithm_decision_20261030/NEW
"""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REGISTRY=ROOT/'config/final_algorithm_decision_20261030.json'
RUN='outputs/final_evaluation_20261030/holdout-v1'


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def file_hash(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    def reject(value): raise ValueError('non-finite JSON constant: '+value)
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError('duplicate JSON key: '+key)
            result[key]=value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'),parse_constant=reject,object_pairs_hook=unique)


def safe_path(root,name):
    if not isinstance(name,str) or Path(name).is_absolute() or '\\' in name or ':' in name or '..' in Path(name).parts:
        raise ValueError('evidence path must be a contained relative path')
    result=(root/name).resolve()
    if not result.is_relative_to(root.resolve()): raise ValueError('evidence path escapes its directory')
    return result


def reconstruct(raw):
    """Rebuild recorded endpoints, including an arrival exactly at observation end."""
    scenario=raw['original_scenario']; specs={p['id']:p for p in scenario['patients']}
    if len(specs)!=len(scenario['patients']):raise ValueError('duplicate scenario patient')
    out={}
    for branch in raw['branches']:
        code=branch['id']
        if code in out:raise ValueError('duplicate policy branch')
        end=branch['metrics']['observation_end_t']
        history={pid:dict(first_commit_t=None,final_arrive_t=None,treatment_start_t=None,inquiries=0) for pid in specs}
        for e in branch['events']:
            if type(e['t']) not in (int,float) or not math.isfinite(e['t']) or not 0<=e['t']<=end:
                raise ValueError('event outside observation window')
            h=history.get(e.get('patient'))
            if h is None:continue
            if e['type']=='ask':h['inquiries']+=1
            if e['type']=='commit' and h['first_commit_t'] is None:h['first_commit_t']=e['t']
            if e['type']=='arrive':h['final_arrive_t']=e['t']
            if e['type']=='arrival_unable':h['final_arrive_t']=None
            if e['type']=='treatment_start':h['treatment_start_t']=e['t']
        out[code]={}
        for pid,spec in specs.items():
            h=history[pid]; selected=h['first_commit_t'] is not None;arrived=h['final_arrive_t'] is not None
            occur=spec.get('occur_t',0);search=spec['search_start_t']
            out[code][pid]=dict(id=pid,priority=spec.get('priority',4),occur_t=occur,search_start_t=search,
                observation_end_t=end,**h,selected=selected,arrived=arrived,
                selection_elapsed_minutes=h['first_commit_t']-search if selected else None,
                arrival_elapsed_minutes=h['final_arrive_t']-occur if arrived else None,
                selection_capped_minutes=(h['first_commit_t'] if selected else end)-search,
                arrival_capped_minutes=(h['final_arrive_t'] if arrived else end)-occur)
    if set(out)!={'R0','R1','H1'}:raise ValueError('missing or unexpected comparison branch')
    return out


def pair(before,after,*,profile,raw_path,case_id,baseline):
    if before['id']!=after['id'] or before['priority']!=after['priority']:
        raise ValueError('paired identity/priority differs')
    delta=after['arrival_capped_minutes']-before['arrival_capped_minutes']
    transitions={key:dict(before=before[field] is not None,after=after[field] is not None)
        for key,field in [('selected','first_commit_t'),('arrived','final_arrive_t'),('treated','treatment_start_t')]}
    lost=[key for key,v in transitions.items() if v['before'] and not v['after']]
    gained=[key for key,v in transitions.items() if not v['before'] and v['after']]
    category=('success_lost' if lost else 'success_gained' if gained else 'both_not_arrived'
        if not before['arrived'] and not after['arrived'] else 'earlier' if delta<0 else 'later' if delta>0 else 'equal_arrival')
    return dict(case_id=case_id,profile=profile,raw_path=raw_path,patient_id=before['id'],priority=before['priority'],baseline=baseline,
        time_direction='earlier_capped' if delta<0 else 'later_capped' if delta>0 else 'equal_capped',
        delta_capped_minutes=delta,category=category,success_lost=lost,success_gained=gained,
        outcome_transitions=transitions,baseline_endpoint=before,candidate_endpoint=after,
        delta_selection_wait_minutes=after['selection_elapsed_minutes']-before['selection_elapsed_minutes']
            if before['selected'] and after['selected'] else None,
        delta_selection_to_arrival_minutes=(after['final_arrive_t']-after['first_commit_t'])-(before['final_arrive_t']-before['first_commit_t'])
            if before['arrived'] and after['arrived'] else None)


def stats(rows):
    values=sorted(r['delta_capped_minutes'] for r in rows)
    return dict(patients=len(rows),time_direction=dict(Counter(r['time_direction'] for r in rows)),
        outcome_categories=dict(Counter(r['category'] for r in rows)),
        success_lost={k:sum(k in r['success_lost'] for r in rows) for k in ('selected','arrived','treated')},
        success_gained={k:sum(k in r['success_gained'] for r in rows) for k in ('selected','arrived','treated')},
        both_not_arrived=sum(not r['baseline_endpoint']['arrived'] and not r['candidate_endpoint']['arrived'] for r in rows),
        mean_capped_difference=math.fsum(values)/len(values) if values else None,
        maximum_positive_capped_difference=max([0]+values),
        high_priority_maximum_positive_capped_difference=max([0]+[r['delta_capped_minutes'] for r in rows if r['priority']==4]))


def load_evidence(registry,root=ROOT):
    if registry['decision_stage']!='posthoc_evidence_based_retention' or registry['prespecified_adoption_test'] is not False:
        raise ValueError('existing results cannot be relabeled as a prespecified adoption test')
    policy=registry['operational_policy']
    if (policy['inquiry'],policy['assignment'],policy['solver_policy'],policy['budget_seconds'])!=('fixed_3','CP-SAT','severity_first',3):
        raise ValueError('registry differs from the retained operational baseline')
    if registry['research_candidate']['H1']['adopted'] is not False or registry['automatic_runtime_switch'] is not False:
        raise ValueError('unvalidated H1 adoption/runtime switching is prohibited by this decision')
    for name,expected in registry['pinned_evidence'].items():
        if file_hash(safe_path(root,name))!=expected:raise ValueError('pinned evidence changed: '+name)
    directory=safe_path(root,registry['evidence_directory']);report=read(directory/'report.json');lock=read(directory/'execution-lock.json')
    if report['passed'] is not True or report['source_before_after_equal'] is not True:
        raise ValueError('source report is not a valid completed evaluation')
    if report['lock_sha256']!=file_hash(directory/'execution-lock.json'):raise ValueError('execution lock changed')
    if report['protocol_sha256']!=hashlib.sha256(canonical(lock['protocol'])).hexdigest():raise ValueError('protocol differs')
    for name,expected in lock['sources'].items():
        if file_hash(safe_path(directory/'sources',name))!=expected:raise ValueError('frozen source snapshot changed: '+name)
    if len(report['cases'])!=len(report['raw_index']) or len(report['cases'])!=len(lock['entries']):
        raise ValueError('scheduled cases missing')
    rows=[]
    for case,index,entry in zip(report['cases'],report['raw_index'],lock['entries']):
        path=safe_path(directory,index['path'])
        if file_hash(path)!=index['sha256']:raise ValueError('raw file changed: '+index['path'])
        body=gzip.decompress(path.read_bytes())
        if hashlib.sha256(body).hexdigest()!=index['raw_sha256']:raise ValueError('raw body changed')
        raw=json.loads(body);scenario=raw['original_scenario']
        if hashlib.sha256(canonical(scenario)).hexdigest()!=entry['scenario_sha256'] or case['scenario_sha256']!=entry['scenario_sha256']:
            raise ValueError('input does not match locked scenario')
        vectors=reconstruct(raw)
        for code,values in vectors.items():
            stored={v['id']:v for v in case['audit']['patient_vectors'][code]}
            if canonical(values)!=canonical(stored):raise ValueError('recorded endpoints differ from report')
        for baseline in ('R0','R1'):
            for pid in vectors[baseline]:
                rows.append(pair(vectors[baseline][pid],vectors['H1'][pid],profile=case['profile'],
                    raw_path=index['path'],case_id=case['case_id'],baseline=baseline))
    return report,rows


def markdown(result):
    lines=['# 최종 알고리즘 정책과 한계','',
        '**운영 최종 정책은 fixed_3 + CP-SAT severity_first(3초)를 유지한다. 별도 H1은 운영 기본값으로 채택하지 않는다.**','',
        '기존 결과를 해석한 사후 결정이며 새로운 채택 가설검정이나 알고리즘 성능 개선 실험이 아니다. 정책 재실행·운영 전환은 0회다.','',
        '| 비교 | 환자 | 제한시간 단축/증가/같음 | 도착 기록 상실 | 치료 시작 기록 상실 | 개인 최대 증가(분) | 우선군4 최대 증가(분) |',
        '|---|---:|---|---:|---:|---:|---:|']
    for baseline,s in result['comparisons'].items():
        d=s['time_direction'];lines.append(f'| H1−{baseline} | {s["patients"]} | {d.get("earlier_capped",0)}/{d.get("later_capped",0)}/{d.get("equal_capped",0)} | {s["success_lost"]["arrived"]} | {s["success_lost"]["treated"]} | {s["maximum_positive_capped_difference"]} | {s["high_priority_maximum_positive_capped_difference"]} |')
    lines+=['','H1−R1의 제한값 동일 542명에는 양쪽 모두 미도착한 40명도 포함된다. 시간차 0은 전체 처리 과정 동일이나 성공을 뜻하지 않는다. R0의 종료 시점 도착 성공을 H1이 상실한 사례는 별도 표시한다.','',
        '| 사례 | 기준 | 환자 | 기준 선정→도착 t | H1 선정→도착 t | 선정대기 차이 | 선정 후 도착까지 차이 | 도착 제한값 차이 | 판정 |',
        '|---|---|---|---|---|---:|---:|---:|---|']
    label={'success_lost':'성공 상실','both_not_arrived':'양쪽 미도착','earlier':'단축','later':'지연','equal_arrival':'도착시간 동일','success_gained':'성공 획득'}
    fmt=lambda v:'미관측' if v is None else str(v)
    for row in result['case_cards']:
        a,b=row['baseline_endpoint'],row['candidate_endpoint']
        lines.append(f'| {row["raw_path"]} | {row["baseline"]} | {row["patient_id"]} | {fmt(a["first_commit_t"])}→{fmt(a["final_arrive_t"])} | {fmt(b["first_commit_t"])}→{fmt(b["final_arrive_t"])} | {fmt(row["delta_selection_wait_minutes"])} | {fmt(row["delta_selection_to_arrival_minutes"])} | {row["delta_capped_minutes"]} | {label[row["category"]]} |')
    lines+=['','시각은 합성 시계의 절대 분이다. +67분 두 사례는 선정 대기 +67분·선정 후 이동 구간 차이 0분이다. 정적 배치 목적 개선과 장기 대기·개별 결과 보호는 다르다.','',
        '구체적인 최초 판단과 이익·손해 환자는 같은 폴더의 causal-cases.md를 참고한다. 향후 대기 보호 개선은 후보로 남기며 검증된 해결책으로 표시하지 않는다.','',
        '603명은 36개 합성 환경에 묶인 자료이고 profile당 환경은 3개다. 프로필별 결과·우선군·성공 전환은 report.json과 paired-patients.csv에 보존한다. 전체 합계를 현장 대표 효과나 독립 환자 603명의 우월성 검정으로 해석하지 않는다.','',
        '실제 서버와 연구 H1의 목적·치료 일정·계산 예산은 다르다. 현재 배치 제약 감사는 전체 온라인 최적성·미래 결과·임상 안전성의 증명이 아니다.','',
        '이미 관측한 이 자료는 향후 후보의 개발·회귀 자료다. 새 후보의 독립 최종 평가에는 목표·허용선·실패 처리·비교 예산을 먼저 고정하고 미사용 자료를 사용해야 한다.','']
    return '\n'.join(lines)


def run(output,registry_path=REGISTRY):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError('Use a new output directory')
    source_bytes=Path(__file__).read_bytes()
    registry_path=Path(registry_path);registry_bytes=registry_path.read_bytes();registry=read(registry_path)
    report,rows=load_evidence(registry)
    from server.allocation_baseline import verify
    baseline=verify()
    if not baseline['ok'] or baseline['manifest_sha256']!=registry['operational_policy']['baseline_manifest_sha256']:
        raise ValueError('current operational baseline changed; decision cannot certify it')
    comparisons={code:stats([r for r in rows if r['baseline']==code]) for code in ('R0','R1')}
    # All records survive export. These examples are explanatory post-hoc cases,
    # not a selected sample used to estimate effectiveness.
    choices=(('R1','raw/000.json.gz','P01'),('R1','raw/003.json.gz','P02'),
             ('R1','raw/016.json.gz','P05'),('R1','raw/021.json.gz','P17'),
             ('R1','raw/031.json.gz','P12'),('R0','raw/027.json.gz','P19'))
    cards=[next(r for r in rows if (r['baseline'],r['raw_path'],r['patient_id'])==choice) for choice in choices]
    result=dict(schema_version=1,decision_id=registry['decision_id'],posthoc_evidence_review=True,
        prespecified_adoption_test=False,algorithm_executions=0,runtime_policy_changed=False,
        audit_passed=True,operational_policy=registry['operational_policy'],research_policy=registry['research_candidate'],
        cases=len(report['cases']),comparisons=comparisons,case_cards=cards,
        by_priority={code:{str(p):stats([r for r in rows if r['baseline']==code and r['priority']==p]) for p in (4,2,1)} for code in ('R0','R1')},
        by_profile={code:{profile:stats([r for r in rows if r['baseline']==code and r['profile']==profile]) for profile in sorted({r['profile'] for r in rows})} for code in ('R0','R1')},
        paired_patients=rows,registry_sha256=hashlib.sha256(registry_bytes).hexdigest(),
        baseline_check=baseline,evidence=registry['pinned_evidence'],
        limitations=registry['limitations'])
    if registry_path.read_bytes()!=registry_bytes or Path(__file__).read_bytes()!=source_bytes:
        raise ValueError('decision registry/reporter changed during analysis')
    for name,expected in registry['pinned_evidence'].items():
        if file_hash(safe_path(ROOT,name))!=expected:raise ValueError('evidence changed during analysis')
    output.mkdir(parents=True)
    (output/'decision-registry.json').write_bytes(registry_bytes)
    (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    (output/'report.md').write_text(markdown(result),encoding='utf-8')
    note=('동일 542명은 **도착 제한값 동일**이며 양쪽 미도착 40명이 포함된다. 전체 처리 과정이나 성공의 동일을 뜻하지 않는다. '
          'R0 대비 도착·치료 시작 기록 상실 1명은 report.md에서 별도로 확인한다. 아래 원문은 R1 비교의 사후 사례 분석이다.\n\n')
    (output/'causal-cases.md').write_bytes(note.encode()+(ROOT/registry['causal_case_document']).read_bytes())
    with (output/'paired-patients.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        keys=('profile','raw_path','patient_id','priority','baseline','time_direction','delta_capped_minutes','category','success_lost','success_gained','delta_selection_wait_minutes','delta_selection_to_arrival_minutes')
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader()
        for r in rows:writer.writerow({k:json.dumps(r[k],ensure_ascii=False) if isinstance(r[k],list) else r[k] for k in keys})
    (output/'reporter-source.py').write_bytes(source_bytes)
    artifacts={p.name:file_hash(p) for p in output.iterdir() if p.is_file()}
    (output/'manifest.json').write_text(json.dumps(dict(files=artifacts,new_policy_executions=0),indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run(args.output)
    print(json.dumps(dict(decision=result['decision_id'],operational_policy_retained=True,H1_adopted=False,
        cases=result['cases'],algorithm_executions=0),ensure_ascii=False))


if __name__=='__main__':main()
