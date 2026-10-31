"""Prespecified W1 waiting-protection experiment; frozen engines stay unchanged.

python -m simulation.waiting_experiment_20261030 run --output NEW-DIRECTORY
python -m simulation.waiting_experiment_20261030 verify --output DIRECTORY
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import traceback

from . import final_evaluation_20261030 as inherited
from . import final_assignment_engine_20261028 as controls
from . import final_assignment_audit_20261028 as control_audit
from . import online_allocation_audit as current_audit
from .map_replay import ReplaySimulation, RouteSnapshot, validate_scenario
from .online_allocation_engine import OnlineAllocationReplay, _ObservedCPSATBatch, SOURCE_EXPERIMENT
from .sim import Policy, Simulation

ROOT=Path(__file__).resolve().parents[1]
PROTOCOL=ROOT/'config/waiting_protocol_20261030.json'
EXPERIMENT_ID='waiting-protection-20261030-v1'
CODES=('R0','R1','H1','W1')
canonical=inherited.canonical
sha=inherited.sha
file_sha=inherited.file_sha
read=inherited.read
write=inherited.write


class WaitingReplay(OnlineAllocationReplay):
    def __init__(self,scenario,policy,routing=None):
        super().__init__(scenario,policy,routing=routing)
        self.waiting_records=[]

    def resolve_batch(self,t):
        if not self.ready:return
        ready=list(self.ready);inputs=self._observed_input(ready,t);rule=self.policy.rule
        if rule.code!='W1':raise ValueError('W1 required')
        ages={row['id']:t-row['request_t'] for row in inputs['patients']}
        if any(type(age) is not int or age<0 for age in ages.values()):raise ValueError('invalid past-only waiting age')
        offset,event_offset,seconds=len(rule.records),len(self.log),self.decision_seconds
        try:
            # The frozen adapter builds detached SimpleNamespace objects from
            # current offers/resources. Its p.t_req is the observed search start.
            self.policy.rule=_ObservedCPSATBatch(rule,deepcopy(inputs))
            Simulation.resolve_batch(self,t)
        finally:self.policy.rule=rule
        actual={p.id:None for p,_ in ready}
        for event in self.log[event_offset:]:
            if event['type']=='commit' and event.get('patient') in actual:actual[event['patient']]=event['hospital']
        records=rule.records[offset:]
        if len(records)!=1 or records[0]['assignments']!=actual:raise AssertionError('W1 proposal differs from actual commit')
        record=deepcopy(records[0]);index=len(self.allocation_decisions)
        record.update(t=t,decision_index=index,observed_waiting_ages=ages)
        self.waiting_records.append(record)
        self.allocation_decisions.append(dict(t=t,kind='batch',rule='W1',scope='current_same_minute_accepted_offers',
            inputs=deepcopy(inputs),waiting_ages=ages,assignments=actual,objective=self._objective(inputs,actual),
            method='waiting_protection',elapsed_seconds=self.decision_seconds-seconds,optimal_proven=False,
            waiting_record_index=len(self.waiting_records)-1))
        self.decision_batches.append(dict(t=t,patients=[p.id for p,_ in ready],method='waiting_protection',
            assignments=deepcopy(actual),scope='same_minute_ready_patients_only'))

    def run_replay(self):
        value=ReplaySimulation.run_replay(self)
        value.update(allocation_decisions=self.allocation_decisions,waiting_records=self.waiting_records,
            solver_records=[],source_experiment=SOURCE_EXPERIMENT,comparison_id=EXPERIMENT_ID,
            execution_contract=dict(inquiry='fixed_three_wait_two_notify',current_inputs_only=True,
                age='t - observed request_t/search_start_t',max_work_units=2048,technical_safety_seconds=10,
                safety_exhaustion_invalidates_run=True,wall_time_added_to_simulation_clock=False,
                whole_episode_optimal_proven=False,whole_episode_nonregression_guaranteed=False))
        value['metrics'].update(allocation_wall_seconds=self.decision_seconds,
            allocation_decisions=len(self.allocation_decisions),waiting_calls=len(self.waiting_records),
            solver_calls=0,solver_wall_seconds=0,solver_fallbacks=0,whole_episode_optimal_proven=False)
        return value


def _candidate(scenario):
    from .waiting_assignment_20261030 import WaitingAssignmentRule
    physical=deepcopy(scenario)
    physical.update(online_allocation_comparison=True,source_experiment=SOURCE_EXPERIMENT,comparison_id=EXPERIMENT_ID)
    routing=RouteSnapshot()
    policy=Policy(rule=WaitingAssignmentRule(max_moves=2048,time_limit_seconds=10.0),k=3,wait=2,notify=True,name='W1')
    branch=WaitingReplay(physical,policy,routing=routing).run_replay()
    branch.update(id='W1',label='W1 과거 대기시간 보호',policy=policy.spec(),
        original_scenario_sha256=sha(scenario),routing_input_sha256=controls._routing_template(None)[1])
    return branch,routing.summary(scenario_only=True)


def run_replay(scenario,execution_order=CODES):
    validate_scenario(scenario);original=canonical(scenario);order=list(execution_order)
    if len(order)!=4 or set(order)!=set(CODES):raise ValueError('execute every policy exactly once')
    by_id={};routes={};control_template=None
    for code in order:
        if code=='W1':by_id[code],routes[code]=_candidate(deepcopy(scenario))
        else:
            spec=next(spec for spec in controls.DEFAULT_FINAL_ASSIGNMENT_POLICIES if spec['id']==code)
            replay=controls.run_final_assignment_replay(deepcopy(scenario),policies=[deepcopy(spec)],execution_order=[code])
            if control_template is None:control_template=deepcopy(replay)
            by_id[code]=replay['branches'][0];routes[code]=replay['routing_summary']['by_branch'][code]
    control_template.update(branches=[deepcopy(by_id[code]) for code in ('R0','R1','H1')],
        execution_order=[code for code in order if code!='W1'])
    control_template['routing_summary']['by_branch']={code:deepcopy(routes[code]) for code in ('R0','R1','H1')}
    control_template['duration_minutes']=max((e['t'] for b in control_template['branches'] for e in b['events']),default=0)
    if canonical(scenario)!=original:raise AssertionError('caller input mutated')
    return dict(schema_version=2,comparison_id=EXPERIMENT_ID,source_experiment=SOURCE_EXPERIMENT,
        original_scenario=deepcopy(scenario),scenario=deepcopy(scenario),input_scenario_sha256=sha(scenario),
        original_scenario_sha256=sha(scenario),execution_order=order,branches=[by_id[code] for code in CODES],
        control_replay=control_template,duration_minutes=max((e['t'] for b in by_id.values() for e in b['events']),default=0),
        routing_input_sha256=control_template['routing_input_sha256'],
        routing_summary=dict(provider='synthetic',scope='independent_per_branch',by_branch=routes,
            live_traffic=False,ambulance_priority=False,external_route_provider_allowed=False))


def audit_replay(replay):
    source=replay['original_scenario']
    if replay['comparison_id']!=EXPERIMENT_ID or [b['id'] for b in replay['branches']]!=list(CODES):raise ValueError('wrong replay identity')
    if sha(source)!=replay['input_scenario_sha256']:raise ValueError('source hash differs')
    checked=control_audit.audit_case(replay['control_replay'],source_scenario=source,
        expected_order=[code for code in replay['execution_order'] if code!='W1'])
    by_id={b['id']:b for b in replay['branches']}
    for branch in replay['control_replay']['branches']:
        if canonical(branch)!=canonical(by_id[branch['id']]):raise ValueError('top-level control differs from frozen control replay')
    branch=by_id['W1'];audit=current_audit.audit_branch(source,branch,exact_limit=0)
    if len(branch['allocation_decisions'])!=len(branch['waiting_records']):raise ValueError('W1 record count differs')
    for index,(decision,record) in enumerate(zip(branch['allocation_decisions'],branch['waiting_records'])):
        expected={p['id']:decision['t']-p['request_t'] for p in decision['inputs']['patients']}
        if expected!=decision['waiting_ages'] or expected!=record['observed_waiting_ages']:raise ValueError('past waiting age differs')
        if record['assignments']!=decision['assignments'] or record['decision_index']!=index:raise ValueError('W1 record/decision differs')
        if record.get('search_stop_reason') in ('time_limit','time_limit_zero'):raise ValueError('W1 technical deadline invalidates result')
        if record.get('evaluated_moves',0)>2048:raise ValueError('W1 fixed work budget exceeded')
    vectors=deepcopy(checked['patient_vectors']);vectors['W1']=control_audit._vectors(source,branch)
    for rows in vectors.values():
        for row in rows:
            row['treatment_capped_minutes']=(row['treatment_start_t'] if row['treatment_start_t'] is not None else row['observation_end_t'])-row['occur_t']
    return dict(passed=True,patient_vectors=vectors,control_audit=checked,
        W1_current_input_event_audit=audit,scope='frozen R0/R1/H1 audit plus W1 recorded-current-input/age/commit/event audit; W1 optimization math independently audited separately',
        W1_mathematics_reexecuted=False,whole_resource_history_replayed=False)


def compare(before,after):
    value=inherited.compare(before,after);left={p['id']:p for p in before};right={p['id']:p for p in after}
    value.update(selected_gained_ids=[pid for pid in left if not left[pid]['selected'] and right[pid]['selected']],
        arrived_gained_ids=[pid for pid in left if not left[pid]['arrived'] and right[pid]['arrived']],
        treatment_started_lost_ids=[pid for pid in left if left[pid]['treatment_start_t'] is not None and right[pid]['treatment_start_t'] is None],
        treatment_started_gained_ids=[pid for pid in left if left[pid]['treatment_start_t'] is None and right[pid]['treatment_start_t'] is not None],
        zero_capped_delta_arrival_loss_ids=[pid for pid in value['arrived_lost_ids'] if left[pid]['arrival_capped_minutes']==right[pid]['arrival_capped_minutes']],
        treatment_capped_delta_minutes=inherited.describe([right[pid]['treatment_capped_minutes']-left[pid]['treatment_capped_minutes'] for pid in left]),
        endpoint_transitions=dict(arrival={f'{int(a)}->{int(b)}':sum(p['arrived']==a and right[p['id']]['arrived']==b for p in before) for a in (False,True) for b in (False,True)}))
    return value


def case_result(replay,thresholds):
    from .waiting_audit_20261030 import audit_replay as independent_audit
    audit=audit_replay(replay);scenario=replay['original_scenario'];vectors=audit['patient_vectors']
    independent=independent_audit(replay)
    original_vectors={code:[{k:v for k,v in row.items() if k!='treatment_capped_minutes'} for row in rows] for code,rows in vectors.items()}
    if canonical(original_vectors)!=canonical(independent['patient_vectors']):raise ValueError('independent endpoint reconstruction differs')
    audit['independent_W1_math_and_events']=dict(passed=independent['passed'],waiting_audit=independent['waiting_audit'],
        endpoint_vectors_sha256=sha(independent['patient_vectors']),
        scope='independent current waiting-objective/H1-reference/age/work-budget/commit and small-input oracle; no full future resource-history proof')
    metrics={b['id']:inherited.branch_metrics(scenario,b,vectors[b['id']],thresholds) for b in replay['branches']}
    for code,rows in vectors.items():metrics[code]['treatment_capped_minutes']=inherited.describe([r['treatment_capped_minutes'] for r in rows])
    metrics['W1']['waiting_calls']=len(replay['branches'][-1]['waiting_records'])
    records=replay['branches'][-1]['waiting_records']
    metrics['W1']['waiting_baseline_retained_calls']=sum(not r.get('accepted_moves') for r in records)
    metrics['W1']['waiting_baseline_retained_note']='No W1 improvement after the current-input H1 reference; not a CP-SAT timeout/fallback'
    metrics['W1']['waiting_total_evaluated_work']=inherited.describe([r['evaluated_moves'] for r in records])
    metrics['W1']['waiting_search_seconds']=inherited.describe([r['search_seconds'] for r in records])
    return dict(case_id=scenario['id'],profile=scenario['stratum'],seed=scenario['seed'],scenario_sha256=sha(scenario),
        audit=audit,metrics=metrics,paired_W1_vs={code:compare(vectors[code],vectors['W1']) for code in ('R0','R1','H1')})


def summarize(cases):
    groups=[]
    for profile in sorted({c['profile'] for c in cases}):
        rows=[c for c in cases if c['profile']==profile];policies={}
        for code in CODES:
            pts=[p for c in rows for p in c['audit']['patient_vectors'][code]]
            policies[code]=dict(patients=len(pts),unassigned=sum(not p['selected'] for p in pts),not_arrived=sum(not p['arrived'] for p in pts),
                untreated=sum(p['treatment_start_t'] is None for p in pts),arrival_capped_minutes=inherited.describe([p['arrival_capped_minutes'] for p in pts]),
                treatment_capped_minutes=inherited.describe([p['treatment_capped_minutes'] for p in pts]),
                priority4_unassigned=sum(p['priority']==4 and not p['selected'] for p in pts),
                priority4_not_arrived=sum(p['priority']==4 and not p['arrived'] for p in pts),
                inquiries=sum(c['metrics'][code]['inquiries'] for c in rows))
        comparisons={}
        for code in ('R0','R1','H1'):
            pairs=[p for c in rows for p in c['paired_W1_vs'][code]['patient_differences']]
            comparisons[code]=dict(capped_delta=inherited.describe([p['delta_capped_minutes'] for p in pairs]),
                selected_lost=sum(len(c['paired_W1_vs'][code]['selected_lost_ids']) for c in rows),
                arrived_lost=sum(len(c['paired_W1_vs'][code]['arrived_lost_ids']) for c in rows),
                treatment_lost=sum(len(c['paired_W1_vs'][code]['treatment_started_lost_ids']) for c in rows),
                zero_capped_delta_arrival_losses=sum(len(c['paired_W1_vs'][code]['zero_capped_delta_arrival_loss_ids']) for c in rows))
        groups.append(dict(profile=profile,cases=len(rows),policies=policies,paired_W1_vs=comparisons))
    return dict(groups=groups,pooled_real_world_efficacy_claimed=False)


def source_hashes():
    pending=[Path(__file__),ROOT/'simulation/waiting_assignment_20261030.py',ROOT/'simulation/waiting_audit_20261030.py'];seen=set()
    while pending:
        path=pending.pop().resolve()
        if path in seen or not path.is_file() or not path.is_relative_to(ROOT):continue
        seen.add(path);tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if not isinstance(node,ast.ImportFrom):continue
            base=path.parent if node.level else ROOT
            for _ in range(max(0,node.level-1)):base=base.parent
            if node.module:pending.append(base.joinpath(*node.module.split('.')).with_suffix('.py'))
            else:pending.extend(base/(alias.name+'.py') for alias in node.names)
    for name in ('config/waiting_protocol_20261030.json','config/final_evaluation_protocol_20261030.json',
                 'test_waiting_experiment_20261030.py','test_waiting_assignment_20261030.py','test_waiting_audit_20261030.py'):
        path=ROOT/name
        if path.is_file():seen.add(path)
    return {p.relative_to(ROOT).as_posix():file_sha(p) for p in sorted(seen)}


def seed_for(protocol,profile,replicate):
    token=f"{protocol['seed_namespace']}|{profile}|{replicate}".encode()
    return 2**640+int.from_bytes(hashlib.sha512(token).digest(),'big')


def planned_entries(protocol):
    profiles=read(ROOT/'config/final_evaluation_protocol_20261030.json')['profiles']
    old=read(ROOT/protocol['development_source_lock'])
    entries=[]
    for phase in ('development_known','validation_new'):
        specifications=old['entries'] if phase=='development_known' else [dict(profile=p['id'],replicate=i,
            seed=seed_for(protocol,p['id'],i)) for p in profiles for i in range(protocol['validation_replicates'])]
        for item in specifications:
            profile=next(p for p in profiles if p['id']==item['profile'])
            scenario=inherited.build_case(profile,item['seed'],protocol['observation_minutes'])
            checksum=sha(scenario)
            if phase=='development_known' and checksum!=item['scenario_sha256']:raise ValueError('known input changed from old holdout lock')
            shift=len(entries)%len(CODES)
            entries.append(dict(phase=phase,profile=item['profile'],replicate=item['replicate'],seed=item['seed'],
                scenario_sha256=checksum,execution_order=list(CODES[shift:]+CODES[:shift])))
    return entries


def markdown(report):
    lines=['# W1 과거 대기 보호 단일 후보 실험','',
        '기존 서버·동결 알고리즘 무수정. 개발 자료와 새로운 합성 검증은 합산하지 않는다. 임상 효과·일반적 우위를 주장하지 않는다.','']
    for phase in report['phases']:
        lines.extend([f'## {phase}', '', '| 환경 | 환자 | H1 미배정/미도착 | W1 미배정/미도착 | H1 제한평균 | W1 제한평균 | W1−R1 개인최대 추가지연 |','|---|---:|---:|---:|---:|---:|---:|'])
        for group in report['phases'][phase]['summary']['groups']:
            a,b=group['policies']['H1'],group['policies']['W1'];maximum=group['paired_W1_vs']['R1']['capped_delta']['maximum']
            lines.append(f"| {group['profile']} | {a['patients']} | {a['unassigned']}/{a['not_arrived']} | {b['unassigned']}/{b['not_arrived']} | {a['arrival_capped_minutes']['mean']:.3f} | {b['arrival_capped_minutes']['mean']:.3f} | {max(0,maximum):g} |")
        lines.append('')
    lines.extend(['미도착자는 관측 종료로 cap한다. 상태 상실은 cap 차이가 0이어도 별도 실패로 남긴다.',
        '문의 수는 병원 업무 proxy이며 실제 업무시간이 아니다. CP-SAT 분기는 없으므로 solver fallback은 null이다.',
        '현재 자원·수용·과거 대기 age의 감사이며 전체 자원 이력·모든 미래에서의 보호 또는 최적성을 증명하지 않는다.'])
    return '\n'.join(lines)+'\n'


def run(output):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError('NEW output directory required; never overwrite prior evidence')
    from .waiting_audit_20261030 import GATE_CRITERIA
    protocol=read(PROTOCOL);sources=source_hashes();entries=planned_entries(protocol)
    inventory=inherited.seed_inventory([entry['seed'] for entry in entries if entry['phase']=='validation_new'])
    output.mkdir(parents=True);(output/'raw').mkdir();(output/'sources').mkdir();(output/'inputs').mkdir()
    write(output/'seed-inventory.json',inventory)
    for name,checksum in sources.items():
        target=output/'sources'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
        if file_sha(target)!=checksum:raise RuntimeError('source copy differs before execution')
    profiles=read(ROOT/'config/final_evaluation_protocol_20261030.json')['profiles']
    for i,entry in enumerate(entries):
        profile=next(p for p in profiles if p['id']==entry['profile']);scenario=inherited.build_case(profile,entry['seed'],protocol['observation_minutes'])
        if sha(scenario)!=entry['scenario_sha256']:raise ValueError('locked input reconstruction mismatch')
        write(output/'inputs'/f'{i:03}.json',scenario)
    lock=dict(schema_version=1,created_at_utc=datetime.now(timezone.utc).isoformat(),protocol=protocol,
        protocol_sha256=sha(protocol),sources=sources,entries=entries,selective_reruns=False,operational_adoption=False,
        seed_inventory_sha256=file_sha(output/'seed-inventory.json'),environment=dict(python=sys.version,platform=platform.platform()),
        development_source_lock_sha256=file_sha(ROOT/protocol['development_source_lock']),
        independent_gate_criteria=deepcopy(GATE_CRITERIA),independent_gate_criteria_sha256=sha(GATE_CRITERIA),
        phase_rule='Performance failure in known development does not stop prespecified single-candidate validation; technical/audit/source-integrity failure aborts entire run')
    write(output/'execution-lock.json',lock)
    cases=[];index=[];started=time.perf_counter()
    try:
        if source_hashes()!=sources:raise RuntimeError('source changed before execution')
        for i,entry in enumerate(entries):
            scenario=read(output/'inputs'/f'{i:03}.json')
            if sha(scenario)!=entry['scenario_sha256']:raise ValueError('input differs from execution lock')
            replay=run_replay(scenario,entry['execution_order']);body=canonical(replay);path=output/'raw'/f'{i:03}.json.gz'
            with path.open('xb') as stream:stream.write(gzip.compress(body,mtime=0))
            index.append(dict(path=path.relative_to(output).as_posix(),sha256=file_sha(path),raw_sha256=hashlib.sha256(body).hexdigest()))
            result=case_result(replay,protocol['threshold_minutes']);result['phase']=entry['phase'];cases.append(result)
            print(json.dumps(dict(done=len(cases),total=len(entries),phase=entry['phase'],profile=entry['profile'])),flush=True)
        if source_hashes()!=sources:raise RuntimeError('source changed during execution')
        phases={phase:dict(cases=[c for c in cases if c['phase']==phase],summary=summarize([c for c in cases if c['phase']==phase])) for phase in ('development_known','validation_new')}
        report=dict(schema_version=1,passed=True,synthetic=True,protocol_sha256=sha(protocol),lock_sha256=file_sha(output/'execution-lock.json'),
            cases=cases,raw_index=index,phases=phases,source_before_after_equal=True,elapsed_seconds=time.perf_counter()-started,
            operational_adoption=False,selection_status='requires_predeclared_independent_gate_review',unavailable=protocol['unavailable'])
        write(output/'report.json',report)
        with (output/'report.md').open('x',encoding='utf-8') as stream:stream.write(markdown(report))
        write(output/'selection.json',dict(selected_candidate=None,operational_adoption=False,single_prespecified_candidate='W1',
            status='technical_execution_completed_pending_independent_predeclared_gates',no_post_validation_tuning=True,
            development_is_known=True,new_validation_not_reusable_as_future_unseen=True))
        return report
    except Exception:
        write(output/'failure.json',dict(passed=False,error=traceback.format_exc(),completed_cases=cases,raw_index=index,
            planned_cases=len(entries),selective_reruns=False,operational_adoption=False));raise


def verify(output):
    from .waiting_audit_20261030 import GATE_CRITERIA
    output=Path(output).resolve();lock=read(output/'execution-lock.json');report=read(output/'report.json')
    if source_hashes()!=lock['sources']:raise ValueError('verifier/source drift from frozen execution')
    if file_sha(output/'execution-lock.json')!=report['lock_sha256'] or sha(lock['protocol'])!=report['protocol_sha256']:raise ValueError('protocol/lock drift')
    if canonical(lock['protocol'])!=canonical(read(PROTOCOL)) or canonical(lock['entries'])!=canonical(planned_entries(lock['protocol'])):raise ValueError('protocol or scheduled entries differ from prespecified study')
    if file_sha(ROOT/lock['protocol']['development_source_lock'])!=lock['development_source_lock_sha256']:raise ValueError('known development source lock changed')
    if lock['independent_gate_criteria']!=GATE_CRITERIA or lock['independent_gate_criteria_sha256']!=sha(GATE_CRITERIA):raise ValueError('pre-results independent criteria changed')
    for name,checksum in lock['sources'].items():
        if file_sha(output/'sources'/name)!=checksum:raise ValueError('source snapshot changed: '+name)
    if file_sha(output/'seed-inventory.json')!=lock['seed_inventory_sha256']:raise ValueError('seed inventory changed')
    if len(report['cases'])!=len(lock['entries']) or len(report['raw_index'])!=len(lock['entries']):raise ValueError('scheduled cases missing')
    cases=[]
    for i,(entry,item) in enumerate(zip(lock['entries'],report['raw_index'])):
        path=(output/item['path']).resolve()
        if not path.is_relative_to(output) or file_sha(path)!=item['sha256']:raise ValueError('raw checksum/path changed')
        body=gzip.decompress(path.read_bytes())
        if hashlib.sha256(body).hexdigest()!=item['raw_sha256']:raise ValueError('raw decompressed checksum changed')
        replay=json.loads(body)
        if sha(read(output/'inputs'/f'{i:03}.json'))!=entry['scenario_sha256'] or sha(replay['original_scenario'])!=entry['scenario_sha256']:raise ValueError('input changed')
        if replay['execution_order']!=entry['execution_order']:raise ValueError('execution order changed')
        result=case_result(replay,lock['protocol']['threshold_minutes']);result['phase']=entry['phase'];cases.append(result)
    phases={phase:dict(cases=[c for c in cases if c['phase']==phase],summary=summarize([c for c in cases if c['phase']==phase])) for phase in ('development_known','validation_new')}
    if canonical(cases)!=canonical(report['cases']) or canonical(phases)!=canonical(report['phases']):raise ValueError('summary does not match raw re-audit')
    if (output/'report.md').read_text(encoding='utf-8')!=markdown(report):raise ValueError('markdown differs')
    value=dict(passed=True,cases=len(cases),new_policy_executions=0,report_sha256=file_sha(output/'report.json'),
        audit_scope='recorded current inputs/commit/events and endpoint arithmetic; separate independent W1 mathematical and adoption audit required')
    write(output/'verification.json',value);return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('run','verify'));parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);value=run(args.output) if args.command=='run' else verify(args.output)
    print(json.dumps(dict(passed=value['passed'],cases=len(value['cases']) if isinstance(value['cases'],list) else value['cases'])))


if __name__=='__main__':main()
