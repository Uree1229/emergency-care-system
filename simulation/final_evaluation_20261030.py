"""Prespecified distribution-shift wrapper; frozen engines are never edited.

python -m simulation.final_evaluation_20261030 run --output <NEW DIRECTORY>
python -m simulation.final_evaluation_20261030 verify --output <DIRECTORY>
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import sys
import time
import traceback

from .final_assignment_engine_20261028 import run_final_assignment_replay
from .final_assignment_audit_20261028 import audit_case
from .final_assignment_scenarios_20261028 import build_case as parent_case, CENTER
from .map_replay import validate_scenario
from .sim import rng_for

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'config/final_evaluation_protocol_20261030.json'
CODES = ('R0', 'R1', 'H1')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def seed_for(protocol, profile, replicate):
    token = f"{protocol['seed_namespace']}|{profile}|{replicate}".encode()
    return 2**512 + int.from_bytes(hashlib.sha512(token).digest(), 'big')


def build_case(profile, seed, observation_minutes=180):
    value = parent_case('surge', seed)
    value['id'] = f"final-evaluation-{profile['id']}-{seed}"
    value['label'] = profile['id']
    value['family'] = value['stratum'] = profile['id']
    value['max_minutes'] = observation_minutes
    random = rng_for(seed, 'final-evaluation-profile-v1')
    # Build enough independent fictional hospitals using the existing generator;
    # cloning is avoided to preserve independent initial occupancies/capabilities.
    from .final_assignment_scenarios_20261028 import _hospitals
    occupancy = random.uniform(.2, .7)
    value['hospitals'] = _hospitals(random, profile['hospital_count'], occupancy)
    for hospital in value['hospitals']:
        hospital['lat'] = CENTER[0] + (hospital['lat'] - CENTER[0]) * profile['radius_scale']
        hospital['lon'] = CENTER[1] + (hospital['lon'] - CENTER[1]) * profile['radius_scale']
        for resource, quantity in list(hospital['capacity'].items()):
            quantity = math.floor(quantity * profile['capacity_scale'])
            hospital['capacity'][resource] = quantity
            occupied = [x for x in hospital['initial_occupancy'] if resource in x['demand']]
            for item in occupied[quantity:]:
                hospital['initial_occupancy'].remove(item)
    for index, patient in enumerate(value['patients']):
        mode = profile['arrival']
        occur = 0 if mode == 'simultaneous' else (index % 3 * 3 if mode == 'waves' else index * 3)
        patient.update(occur_t=occur, dispatch_t=occur+1, ambulance_arrive_t=occur+4, search_start_t=occur+6)
    value['response_environment'] = {}
    for patient in value['patients']:
        responses = value['response_environment'][patient['id']] = {}
        for hospital in value['hospitals']:
            keyed = rng_for(seed, 'final-evaluation-replies-v1', patient['id'], hospital['id'])
            responses[hospital['id']] = {'attempts': [dict(delay=keyed.randint(*profile['delay']),
                silent=keyed.random() < profile['silence']) for _ in range(64)]}
    value['exogenous_events'] = []
    if profile['change'] == 'outage':
        value['exogenous_events'] = [dict(t=9, type='resource_unavailable',
            hospital=h['id'], resource='ed_bed', until=90) for h in value['hospitals'][:2]
            if h['capacity']['ed_bed'] > 0]
    elif profile['change'] == 'arrival_condition':
        extra = '합성 도착평가 추가조건'
        value['hospitals'][-1]['capabilities'].append(extra)
        for patient in value['patients'][::3]:
            patient['arrival_assessment_adds_capabilities'] = [extra]
    value['nonresponse'] = profile['silence']
    value['experiment'] = dict(wrapper='final_evaluation_20261030_v1', profile=deepcopy(profile),
        seed=seed, patient_count=len(value['patients']), clinical_values='synthetic',
        outcome_selection_used=False, parent_generator='final_assignment_scenarios_20261028',
        policy_information='current received offers/current resources only',
        acceptance_rule='endogenous current capabilities and resources',
        resource_observation_delay_supported=False)
    value['assumptions'].append('별도 profile 변환: 부모 생성기의 원래 분포 가정 대신 experiment.profile을 따른다. 새 현장 데이터가 아니다.')
    validate_scenario(value)
    return value


def source_hashes():
    """Static local import closure plus actual entrypoint; no broad live UI capture."""
    pending = [Path(__file__), ROOT/'simulation/final_assignment_audit_20261028.py']
    seen = set()
    while pending:
        path = pending.pop().resolve()
        if path in seen or not path.is_file() or not path.is_relative_to(ROOT):
            continue
        seen.add(path)
        try:
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = path.parent if node.level else ROOT
                for _ in range(max(0, node.level-1)):
                    base = base.parent
                if node.module:
                    target = base.joinpath(*node.module.split('.'))
                    pending.append(target.with_suffix('.py'))
                else:
                    pending.extend(base/(alias.name+'.py') for alias in node.names)
    seen.add(PROTOCOL)
    seen.add(ROOT/'test_final_evaluation_20261030.py')
    return {p.relative_to(ROOT).as_posix(): file_sha(p) for p in sorted(seen)}


def seed_inventory(proposed):
    """Scan stored uncompressed JSON evidence and Python source literals.

    Chunked reads include large reports without loading their patient matrices.
    Compressed raw files are not read; their metadata/index seeds are scanned.
    This scope is stated rather than claiming inaccessible/global history.
    """
    roots = ('config', 'simulation_results', 'optimization_results', 'optimization_results_v2', '.codex-build', 'outputs')
    pattern = re.compile(rb'"seed"\s*:\s*(\d+)')
    found = set(); files = []; total_bytes = 0
    for name in roots:
        folder = ROOT/name
        if not folder.exists():
            continue
        for path in sorted(folder.rglob('*.json')):
            digest = hashlib.sha256(); tail = b''
            with path.open('rb') as stream:
                while chunk := stream.read(1024*1024):
                    digest.update(chunk); total_bytes += len(chunk)
                    data = tail+chunk
                    found.update(int(m) for m in pattern.findall(data))
                    tail = data[-1024:]
            files.append(dict(path=path.relative_to(ROOT).as_posix(), sha256=digest.hexdigest()))
    overlap = sorted(set(proposed) & found)
    if overlap:
        raise ValueError('Proposed seed already exists in local JSON evidence')
    return dict(scope='stored uncompressed JSON: '+','.join(roots), files=files,
        scanned_bytes=total_bytes, existing_unique_seed_count=len(found),
        existing_max_seed_bits=max((x.bit_length() for x in found), default=0),
        proposed_min_seed_bits=min(s.bit_length() for s in proposed),
        exact_seed_intersection=overlap, passed=True,
        limitations='Compressed raw bytes, deleted files, other machines and inaccessible history not scanned; seed independence is not proof of real-world generalization')


def describe(values):
    values = sorted(values)
    if not values:
        return dict(denominator=0, mean=None, p95=None, p99=None, maximum=None)
    return dict(denominator=len(values), mean=math.fsum(values)/len(values),
        p95=values[math.ceil(.95*len(values))-1], p99=values[math.ceil(.99*len(values))-1], maximum=values[-1])


def branch_metrics(scenario, branch, vectors, thresholds):
    counts = Counter(e['hospital'] for e in branch['events'] if e['type'] == 'ask')
    for h in scenario['hospitals']:
        counts.setdefault(h['id'], 0)
    asks = sum(counts.values())
    decisions = branch['allocation_decisions']
    replies = Counter(e['type'] for e in branch['events'] if e['type'] in ('accept','decline','no_response'))
    observed = replies['accept'] + replies['decline']
    retention = [r for r in branch.get('heuristic_records', []) if not r['accepted_moves']]
    def patient_stats(rows):
        return dict(patients=len(rows), unassigned=sum(not r['selected'] for r in rows),
            not_arrived=sum(not r['arrived'] for r in rows),
            untreated=sum(r['treatment_start_t'] is None for r in rows),
            selection_capped_minutes=describe([r['selection_capped_minutes'] for r in rows]),
            arrival_capped_minutes=describe([r['arrival_capped_minutes'] for r in rows]),
            conditional_arrival_minutes=describe([r['arrival_elapsed_minutes'] for r in rows if r['arrived']]),
            arrival_within_experimental_threshold={str(t):dict(success=sum(r['arrived'] and r['arrival_elapsed_minutes']<=t for r in rows),
                denominator=len(rows)) for t in thresholds})
    return dict(**patient_stats(vectors), priority_groups={str(p):patient_stats([r for r in vectors if r['priority']==p]) for p in (4,2,1)},
        inquiries=asks, inquiries_by_hospital=dict(sorted(counts.items())),
        concentration=dict(max_share=max(counts.values())/asks if asks else None,
            hhi=sum((v/asks)**2 for v in counts.values()) if asks else None),
        observed_replies=dict(replies), observed_acceptance_ratio=replies['accept']/observed if observed else None,
        change_events=dict(Counter(e['type'] for e in branch['events'] if e['type'] in
            ('condition_update','redirect','transfer_search','resource_unavailable','notify'))),
        resource_invalid_transit_minutes=branch['metrics']['resource_invalid_transit_minutes'],
        allocation_wall_ms=describe([d['elapsed_seconds']*1000 for d in decisions]),
        allocation_wall_total_seconds=branch['metrics']['allocation_wall_seconds'],
        heuristic_calls=len(branch.get('heuristic_records', [])),
        heuristic_baseline_retained_calls=len(retention),
        heuristic_baseline_retained_note='No improving candidate adopted; not a CP-SAT timeout/fallback',
        solver_fallback_rate=None, constraint_violations=0,
        constraint_violation_scope='successful recorded-current-input auditor; not full independent resource-history replay')


def compare(left, right):
    before = {r['id']:r for r in left}; after = {r['id']:r for r in right}
    if set(before) != set(after):
        raise ValueError('paired patient partition differs')
    pairs = [dict(patient_id=p, priority=before[p]['priority'],
        delta_capped_minutes=after[p]['arrival_capped_minutes']-before[p]['arrival_capped_minutes'],
        before_arrived=before[p]['arrived'], after_arrived=after[p]['arrived'],
        delta_common_arrival_minutes=after[p]['arrival_elapsed_minutes']-before[p]['arrival_elapsed_minutes']
            if before[p]['arrived'] and after[p]['arrived'] else None) for p in before]
    return dict(patient_differences=pairs,
        selected_lost_ids=[p for p in before if before[p]['selected'] and not after[p]['selected']],
        arrived_lost_ids=[p for p in before if before[p]['arrived'] and not after[p]['arrived']],
        all_patient_capped_delta_minutes=describe([p['delta_capped_minutes'] for p in pairs]),
        common_arrival_delta_minutes=describe([p['delta_common_arrival_minutes'] for p in pairs if p['delta_common_arrival_minutes'] is not None]),
        high_priority_maximum_capped_worsening_minutes=max([0]+[p['delta_capped_minutes'] for p in pairs if p['priority']==4]))


def case_result(replay, thresholds):
    audit = audit_case(replay)
    scenario = replay['original_scenario']
    return dict(case_id=scenario['id'], profile=scenario['stratum'], seed=scenario['seed'],
        scenario_sha256=sha(scenario), audit=audit,
        metrics={b['id']:branch_metrics(scenario,b,audit['patient_vectors'][b['id']], thresholds) for b in replay['branches']},
        paired_H1_vs={c:compare(audit['patient_vectors'][c],audit['patient_vectors']['H1']) for c in ('R0','R1')})


def summarize(cases):
    groups = []
    for profile in sorted({c['profile'] for c in cases}):
        group = [c for c in cases if c['profile']==profile]
        policies = {}
        for code in CODES:
            rows = [p for c in group for p in c['audit']['patient_vectors'][code]]
            policies[code] = dict(patients=len(rows), unassigned=sum(not p['selected'] for p in rows),
                not_arrived=sum(not p['arrived'] for p in rows),
                inquiries=sum(c['metrics'][code]['inquiries'] for c in group),
                arrival_capped_minutes=describe([p['arrival_capped_minutes'] for p in rows]))
        differences = {code:describe([p['delta_capped_minutes'] for c in group
            for p in c['paired_H1_vs'][code]['patient_differences']]) for code in ('R0','R1')}
        groups.append(dict(profile=profile,cases=len(group),policies=policies,paired_H1_vs=differences))
    examples = {}
    pairs = [dict(case_id=c['case_id'],profile=c['profile'],**p) for c in cases
             for p in c['paired_H1_vs']['R1']['patient_differences']]
    for kind, condition, reverse in (('improved',lambda p:p['delta_capped_minutes']<0,False),
        ('worsened',lambda p:p['delta_capped_minutes']>0,True),('unchanged',lambda p:p['delta_capped_minutes']==0,False)):
        eligible = sorted((p for p in pairs if condition(p)),key=lambda p:p['delta_capped_minutes'],reverse=reverse)
        examples[kind] = eligible[:3]
    return dict(groups=groups,examples_H1_vs_R1=examples,pooled_real_world_efficacy_estimate=False)


def markdown(report):
    lines = ['# 사전 고정한 합성 환경 변화 평가', '',
        '실제 서버와 다른 연구용 R0/R1/H1 배정 비교다. 새 정책 선택·서버 기본값 변경은 하지 않았다.', '',
        f"{len(report['cases'])}환경 · {len(report['cases'])*3}분기. 각 환경의 같은 환자·잠재 회신·자원 조건에서 실행했다.", '',
        '도착 제한시간은 미도착자를 관측 종료까지 포함한다. profile당 3환경의 기술통계이며 우월성·동등성 검증이 아니다.', '',
        '| 환경 | 환자 | R1 미배정/미도착 | H1 미배정/미도착 | R1 제한평균(분) | H1 제한평균(분) | H1−R1 개인최대 증가(분) |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for group in report['summary']['groups']:
        a,b=group['policies']['R1'],group['policies']['H1']
        lines.append(f"| {group['profile']} | {a['patients']} | {a['unassigned']}/{a['not_arrived']} | {b['unassigned']}/{b['not_arrived']} | {a['arrival_capped_minutes']['mean']:.3f} | {b['arrival_capped_minutes']['mean']:.3f} | {max(0,group['paired_H1_vs']['R1']['maximum']):g} |")
    lines += ['', '개인별 변화·좋은/나쁜/동일 사례는 report.json, 원 사건과 계산 기록은 raw/*.json.gz에 보존한다.', '',
        '실험상 30/60/120분 기준은 임상 골든타임이 아니다. 미응답·미도착을 분모에서 제외하지 않았다.', '',
        '병원 문의 집중(HHI·최대 점유율), 계산시간·H1 기준해 유지, 우선군별 결과와 p95/p99는 각 환경의 JSON에 기록했다.', '',
        'CP-SAT 대체해율·자원 갱신 지연·실제 검토시간·실제 도로·임상 결과는 이번 비교에서 미측정이다.']
    return '\n'.join(lines)+'\n'


def run(output):
    output=output.resolve()
    if output.exists():
        raise FileExistsError('Use a new output directory; evidence is never overwritten')
    protocol=read(PROTOCOL); sources=source_hashes()
    entries=[]
    for profile in protocol['profiles']:
        for index in range(protocol['replicates']):
            seed=seed_for(protocol,profile['id'],index)
            scenario=build_case(profile,seed,protocol['observation_minutes'])
            shift=len(entries)%3
            entries.append(dict(profile=profile['id'],replicate=index,seed=seed,
                scenario_sha256=sha(scenario),execution_order=list(CODES[shift:]+CODES[:shift])))
    inventory=seed_inventory([x['seed'] for x in entries])
    output.mkdir(parents=True); (output/'raw').mkdir(); (output/'sources').mkdir()
    for name in sources:
        target=output/'sources'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/name).read_bytes())
        if file_sha(target)!=sources[name]:
            raise RuntimeError('source snapshot differs from pre-run digest')
    write(output/'seed-inventory.json',inventory)
    lock=dict(schema_version=1,created_at=datetime.now(timezone.utc).isoformat(),
        protocol=protocol,protocol_sha256=sha(protocol),sources=sources,entries=entries,
        environment=dict(python=sys.version,platform=platform.platform()),
        seed_inventory_sha256=file_sha(output/'seed-inventory.json'),
        server_baseline_sha256=file_sha(ROOT/'config/server_allocation_baseline_20261030.json'),
        phases='prespecified holdout; no development candidate selection',
        selective_reruns=False,operational_adoption=False)
    write(output/'execution-lock.json',lock)
    if source_hashes()!=sources:
        raise RuntimeError('Source changed before first branch')
    cases=[]; index=[];started=time.perf_counter()
    try:
        for entry in entries:
            profile=next(p for p in protocol['profiles'] if p['id']==entry['profile'])
            scenario=build_case(profile,entry['seed'],protocol['observation_minutes'])
            if sha(scenario)!=entry['scenario_sha256']:
                raise ValueError('input differs from pre-execution lock')
            replay=run_final_assignment_replay(scenario,execution_order=entry['execution_order'])
            raw=canonical(replay); path=output/'raw'/(str(len(cases)).zfill(3)+'.json.gz')
            with path.open('xb') as stream:
                stream.write(gzip.compress(raw,mtime=0))
            index.append(dict(path=path.relative_to(output).as_posix(),sha256=file_sha(path),raw_sha256=hashlib.sha256(raw).hexdigest()))
            result=case_result(replay,protocol['threshold_minutes']);cases.append(result)
            print(json.dumps(dict(done=len(cases),total=len(entries),profile=entry['profile'])),flush=True)
        if source_hashes()!=sources:
            raise RuntimeError('Source changed during execution')
        report=dict(schema_version=1,protocol_sha256=sha(protocol),lock_sha256=file_sha(output/'execution-lock.json'),
            passed=True,synthetic=True,cases=cases,raw_index=index,summary=summarize(cases),
            elapsed_seconds=time.perf_counter()-started,unavailable=protocol['unavailable'],
            source_before_after_equal=True,operational_adoption=False,full_resource_history_replayed=False)
        write(output/'report.json',report)
        with (output/'report.md').open('x',encoding='utf-8') as stream:
            stream.write(markdown(report))
        return report
    except Exception:
        write(output/'failure.json',dict(passed=False,error=traceback.format_exc(),completed_cases=cases,
            raw_index=index,selective_reruns=False,operational_adoption=False))
        raise


def verify(output):
    lock=read(output/'execution-lock.json');report=read(output/'report.json')
    if source_hashes()!=lock['sources']:
        raise ValueError('current verifier/dependencies differ from frozen sources')
    if file_sha(output/'execution-lock.json')!=report['lock_sha256'] or sha(lock['protocol'])!=report['protocol_sha256']:
        raise ValueError('protocol/lock differs')
    for name,checksum in lock['sources'].items():
        if file_sha(output/'sources'/name)!=checksum:
            raise ValueError('frozen source differs: '+name)
    if file_sha(output/'seed-inventory.json')!=lock['seed_inventory_sha256']:
        raise ValueError('seed inventory differs')
    if len(report['cases'])!=len(lock['entries']) or len(report['raw_index'])!=len(lock['entries']):
        raise ValueError('scheduled cases missing')
    cases=[]
    for entry,item in zip(lock['entries'],report['raw_index']):
        path=(output/item['path']).resolve()
        if not path.is_relative_to(output.resolve()) or file_sha(path)!=item['sha256']:
            raise ValueError('raw path or checksum differs')
        body=gzip.decompress(path.read_bytes())
        if hashlib.sha256(body).hexdigest()!=item['raw_sha256']:
            raise ValueError('decompressed raw hash differs')
        replay=json.loads(body)
        if sha(replay['original_scenario'])!=entry['scenario_sha256'] or replay['execution_order']!=entry['execution_order']:
            raise ValueError('input/execution order differs from locked entry')
        cases.append(case_result(replay,lock['protocol']['threshold_minutes']))
    if canonical(cases)!=canonical(report['cases']) or summarize(cases)!=report['summary']:
        raise ValueError('recorded events/current decisions or summary failed re-audit')
    if (output/'report.md').read_text(encoding='utf-8')!=markdown(report):
        raise ValueError('markdown differs')
    value=dict(passed=True,cases=len(cases),raw_reaudits=len(cases),new_policy_executions=0,
        full_resource_history_replayed=False,independent_statistical_inference=False,
        report_sha256=file_sha(output/'report.json'))
    write(output/'verification.json',value)
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('run','verify'));parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    value=run(args.output) if args.command=='run' else verify(args.output)
    print(json.dumps(dict(passed=value['passed'],cases=len(value['cases']) if isinstance(value.get('cases'),list) else value['cases'])))


if __name__=='__main__':
    main()
