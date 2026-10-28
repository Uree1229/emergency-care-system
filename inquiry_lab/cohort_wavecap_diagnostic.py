"""Bounded, already-observed training diagnostic. No live-server changes.

The root executes this driver only after source/test binding. Initial24 is an
early rejection screen, never a holdout or promotion result. Expansion runs
only the remaining336; it cannot rerun the initial24 or weaken old goals.
"""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import argparse
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import time
import types
import traceback

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'.codex-build/eat-cohort-wavecap-20261026'
TW_BUILD=ROOT/'.codex-build/eat-cohort-tailwait-20261025'
BATCH_BUILD=ROOT/'.codex-build/eat-cohort-batchwave-20261025'
NAME='eat_cohort_wavecap_l15_f'
PRIMARY=NAME+'|immediate'
TAILWAIT='eat_cohort_tailwait_l15_f|immediate'
PAIRSHIELD='eat_cohort_pairshield_l15_f|immediate'
STOPLOSS='eat_cohort_stoploss_l15_f|immediate'
BASELINES=(TAILWAIT,PAIRSHIELD,STOPLOSS)
TW_SEAL='3158907de3612918a685a18dab25ddf4559f36710b0da906121fae9d765946e7'
TW_REPORT='efbb2dd3e708e16e4573ff8bb054a0c7442bd2a183396643b840d8973661cc15'
TW_LEDGER='0e5da095034256b47a0413f71f4a0a37ff61820bfb5b8034fd3687259df2893c'
TW_INPUTS='1096e2b8a9c4772e81df4748b459ab4cf21838669a4e5e73f591a308126df217'
BATCH_ARCHIVE='6e18c41f8b914ed93d3270f51907acee9e13ac50d1e7c4bbb8263b91b714263c'
INITIAL_INDICES=(0,1,2,27,96,97,98,99,100,101,102,103,240,241,242,288,336,337,338,339,340,341,342,343)
FAMILIES=('immediate_all_accept','delayed_near_fast_far','no_response_near','simultaneous_w1',
          'staggered_w2','all_unavailable_control','historical_shift','history_helpful')
NEW_SOURCES=tuple('inquiry_lab/cohort_wavecap_'+n+'.py' for n in ('horizon','policy','context','runner','audit','diagnostic'))+tuple(
    'test_inquiry_lab_cohort_wavecap_'+n+'.py' for n in ('horizon','policy','context','runner','audit','diagnostic'))
LEGACY_FUNCTIONS=('patient_rows','scope_metrics','paired','paired_lex_ci','assess','regression_outcomes','evaluate',
    'retention_view','retention_for_compacts','retention_assessment','pareto_view','pareto_for_compacts','pareto_retention_assessment')


def raw(v):return json.dumps(v,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
def sha(v):return hashlib.sha256(v).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def digest(p):return sha(Path(p).read_bytes())
def write_new(p,v):
    with Path(p).open('xb') as f:f.write(raw(v))
def key(p):return type(p['id']).__name__,str(p['id'])
def wait(p):return 300 if p['failed'] else p['selection_seconds']
def arrival(p):return 1800 if p['failed'] else p['estimated_arrival_seconds']


def rows(patients):
    result=[];seen=set()
    for p in patients:
        assert key(p) not in seen;seen.add(key(p))
        assert type(p['failed']) is bool and type(p['created_at']) in (int,float) and p['created_at']>=0
        s,a,t=p['selection_seconds'],p['estimated_arrival_seconds'],p['travel_seconds']
        if p['failed']:assert s is a is t is None
        else:
            assert type(s) in (int,float) and 0<=s<=300
            assert type(a) in (int,float) and type(t) in (int,float) and t>=0 and abs(a-s-t)<=1e-9
        contacted=p.get('contacted_hospitals',p.get('contacted'))
        destination=p.get('destination_hospital_id',p.get('destination'))
        if 'contacted_hospitals' in p and 'contacted' in p:assert p['contacted_hospitals']==p['contacted']
        if 'destination_hospital_id' in p and 'destination' in p:assert p['destination_hospital_id']==p['destination']
        assert type(p['inquiry_count']) is int and p['inquiry_count']>=0
        assert type(contacted) is list and len(contacted)==p['inquiry_count']
        result.append(dict(id=p['id'],created_at=p['created_at'],failed=p['failed'],selection_seconds=s,
            estimated_arrival_seconds=a,travel_seconds=t,destination=destination,
            inquiry_count=p['inquiry_count'],contacted=deepcopy(contacted)))
    return sorted(result,key=key)


def comparison(cases,baseline):
    vectors=[];lost=[];gained=[]
    for c in cases:
        b=rows(c['compact']['branches'][baseline]['patients']);a=rows(c['compact']['branches'][PRIMARY]['patients'])
        assert b and [key(p) for p in b]==[key(p) for p in a],'empty or different patient partition'
        for old,new in zip(b,a):
            ident=dict(parent_stage=c['compact']['parent_stage'],source_index=c['source_index'],
                kind=c['compact']['kind'],seed=c['compact']['seed'],patient=new['id'])
            if not old['failed'] and new['failed']:lost.append(ident)
            if old['failed'] and not new['failed']:gained.append(ident)
            vectors.append(dict(**ident,before_failed=old['failed'],after_failed=new['failed'],
                before_wait_seconds=wait(old),after_wait_seconds=wait(new),wait_delta_seconds=wait(new)-wait(old),
                before_arrival_cost_seconds=arrival(old),after_arrival_cost_seconds=arrival(new),
                arrival_cost_delta_seconds=arrival(new)-arrival(old),both_selected=not old['failed'] and not new['failed']))
    w=[v for v in vectors if v['wait_delta_seconds']>0];a=[v for v in vectors if v['arrival_cost_delta_seconds']>0]
    both=[v for v in a if v['both_selected']]
    return dict(before=baseline,after=PRIMARY,patients=len(vectors),lost_selections=len(lost),new_selections=len(gained),
        lost_identifiers=lost,gained_identifiers=gained,
        before_wait_sum_seconds=sum(v['before_wait_seconds'] for v in vectors),after_wait_sum_seconds=sum(v['after_wait_seconds'] for v in vectors),
        wait_sum_delta_seconds=sum(v['wait_delta_seconds'] for v in vectors),
        wait_increase_patient_count=len(w),wait_increase_sum_seconds=sum(v['wait_delta_seconds'] for v in w),
        maximum_individual_wait_increase_seconds=max([0]+[v['wait_delta_seconds'] for v in w]),wait_increase_identifiers=w,
        arrival_cost_sum_delta_seconds=sum(v['arrival_cost_delta_seconds'] for v in vectors),
        arrival_increase_patient_count=len(a),arrival_increase_sum_seconds=sum(v['arrival_cost_delta_seconds'] for v in a),
        maximum_individual_arrival_cost_increase_seconds=max([0]+[v['arrival_cost_delta_seconds'] for v in a]),arrival_increase_identifiers=a,
        both_selected_arrival_increase_patient_count=len(both),both_selected_arrival_increase_sum_seconds=sum(v['arrival_cost_delta_seconds'] for v in both),
        maximum_both_selected_arrival_increase_seconds=max([0]+[v['arrival_cost_delta_seconds'] for v in both]),
        both_selected_arrival_increase_identifiers=both,patient_comparisons=vectors)


def early_gate(cases):
    assert len({c['source_index'] for c in cases})==len(cases),'duplicate source cases'
    assert all(c['compact']['parent_stage'] in ('repair','development') for c in cases)
    groups={o:[c for c in cases if c['compact']['parent_stage']==o] for o in ('repair','development')}
    checks=dict(both_observed_training_slices_present=all(groups.values()));scopes={}
    labels={TAILWAIT:'tailwait',PAIRSHIELD:'pairshield',STOPLOSS:'stoploss'}
    for origin,selected in groups.items():
        views={labels[b]:comparison(selected,b) for b in BASELINES}
        for label,v in views.items():
            checks[origin+'_'+label+'_success_identities_preserved']=v['lost_selections']==0
            checks[origin+'_wait_not_greater_than_'+label]=v['wait_sum_delta_seconds']<=0
            checks[origin+'_individual_wait_not_worse_than_'+label]=v['maximum_individual_wait_increase_seconds']==0
            checks[origin+'_both_selected_arrival_not_worse_than_'+label]=v['maximum_both_selected_arrival_increase_seconds']==0
        checks[origin+'_wait_strictly_smaller_than_tailwait']=views['tailwait']['wait_sum_delta_seconds']<0
        scopes[origin]=views
    return dict(passed=all(checks.values()),checks=checks,scopes=scopes,operational_adoption=False,
        scope='Early rejection on observed training only; never substitutes original9/retention9/Pareto17 or heldout validation')


def select_initial(cases):
    indices=[]
    for origin in ('repair','development'):
        group=[(i,c['compact']) for i,c in enumerate(cases) if c['compact']['parent_stage']==origin]
        for family in FAMILIES:indices.append(next(i for i,c in group if c['kind']!='mix' and c['family']==family))
        indices.extend([i for i,c in group if c['kind']=='mix'][:3])
    indices.append(288)
    indices.append(next(i for i,c in enumerate(cases) if c['compact']['parent_stage']=='repair' and c['compact']['seed']==321989570880066374450133724575947802359))
    assert len(indices)==len(set(indices))==24
    assert tuple(sorted(indices))==INITIAL_INDICES
    return list(INITIAL_INDICES)


def bindings():
    paths={TW_BUILD/'verification.json':TW_SEAL,TW_BUILD/'repair.json':TW_REPORT,
        TW_BUILD/'preservation/control-ledger.json':TW_LEDGER,TW_BUILD/'input-manifest.json':TW_INPUTS,
        BATCH_BUILD/'failure-archive.json':BATCH_ARCHIVE}
    assert all(digest(p)==v for p,v in paths.items())
    archive=read(BATCH_BUILD/'failure-archive.json')
    assert archive['archive_integrity_passed'] and not archive['original_protocol_passed']
    assert archive['original_formal_attempt_status']=='audit_failed' and archive['supplemental_semantic_verification']
    assert archive['live_default']=='fixed_3' and not archive['operational_adoption']
    sources=archive['source_sha256'];assert len(sources)==666
    assert not set(sources)&set(NEW_SOURCES) and len(NEW_SOURCES)==12
    sources=dict(sources,**{n:digest(ROOT/n) for n in NEW_SOURCES})
    assert len(sources)==678 and all(digest(ROOT/n)==v for n,v in sources.items())
    for name in ('parent-regression.json','parent-regression-output.txt','producer-synthetic-tests.json','auditor-synthetic-tests.json'):
        p=BUILD/name;paths[p]=digest(p)
    report=read(TW_BUILD/'repair.json');seal=read(TW_BUILD/'verification.json')
    assert seal['passed'] and seal['source_count']==634 and len(report['cases'])==360
    assert report['operational_adoption'] is False
    ledger=read(TW_BUILD/'preservation/control-ledger.json')
    original=[x for x in read(TW_BUILD/'input-manifest.json')['entries'] if x['stage']=='repair']
    assert len(original)==len(ledger['entries'])==360
    selected=select_initial(report['cases']);entries=[]
    for i,(case,entry,reference) in enumerate(zip(report['cases'],original,ledger['entries'])):
        c=case['compact'];assert (c['parent_stage'],c['kind'],c['seed'])==(entry['parent_stage'],entry['kind'],entry['seed'])
        assert entry['sha256']==reference['scenario_sha256']
        branches={n:dict(patients=rows(v['patients'])) for n,v in c['branches'].items()}
        assert len(branches)==25 and set(BASELINES)<=set(branches)
        entries.append(dict(source_index=i,kind=c['kind'],seed=c['seed'],family=c['family'],scenario_id=c['scenario_id'],
            parent_stage=c['parent_stage'],original_origin=c['original_origin'],input_sha256=entry['sha256'],
            snapshot_sha256=reference['initial_snapshot_sha256'],environment_sha256=reference['environment_sha256'],
            baseline_branches=branches,baseline_sha256=sha(raw(branches))))
    return dict(schema_version=1,name=NAME,source_sha256=sources,binding_sha256={p.relative_to(ROOT).as_posix():v for p,v in paths.items()},
        initial_indices=selected,expansion_indices=[i for i in range(360) if i not in selected],entries=entries,
        observed_training_only=True,heldout=False,success_parent='immutable Tailwait source634',
        batchwave_lineage='qualified failed archive; reporting only, not a successful original protocol',
        driver_parent_raw_decompressions=0,controls_reexecuted=0,
        root_prior_parent_raw_read_disclosure=dict(reported_decompressions=2,source_indices=[0,288],
            reported_cap_bad_call_counts=[39,40],driver_reverified_raws=False,
            qualification='Root read-only motivation before new candidate; not an overall parent-read-once claim'),
        old_full_goals=dict(original_counts=[9,7,13],prior_retention_counts=[9,5],pareto_counts=[17,9],
            repair_failed_max=135,repair_wait_max=113296,development_failed_max=51,development_wait_strict_max=53498,
            known_wait_caps=dict(repair=113405,development=54557),subset_may_not_claim_full_goals=True),
        operational_adoption=False,live_default='fixed_3')


def freeze(checks_path):
    assert not (BUILD/'diagnostic-source-freeze.json').exists()
    checks=read(checks_path);protocol=bindings()
    assert checks['passed'] and checks['errors']==checks['failures']==0
    assert checks['scope']=='targeted_wavecap_candidate_tests'
    assert checks['source_sha256_before']==checks['source_sha256_after']==protocol['source_sha256']
    protocol['targeted_checks_sha256']=digest(checks_path)
    protocol['binding_sha256'][Path(checks_path).resolve().relative_to(ROOT).as_posix()]=digest(checks_path)
    protocol['historical_full_suite']='not rerun; previous10 historical README failures are not reclassified as passes'
    write_new(BUILD/'input-manifest.json',protocol)
    write_new(BUILD/'initial-manifest.json',dict(indices=protocol['initial_indices'],count=24,
        entries=[protocol['entries'][i] for i in protocol['initial_indices']],input_manifest_sha256=digest(BUILD/'input-manifest.json')))
    write_new(BUILD/'diagnostic-source-freeze.json',dict(passed=True,protocol_sha256=digest(BUILD/'input-manifest.json'),
        initial_manifest_sha256=digest(BUILD/'initial-manifest.json'),source_sha256=protocol['source_sha256'],
        candidate_runs_before_freeze=0,observed_training_only=True,heldout=False))


def frozen():
    value=read(BUILD/'diagnostic-source-freeze.json');p=read(BUILD/'input-manifest.json')
    assert value['passed'] and value['protocol_sha256']==digest(BUILD/'input-manifest.json')
    assert value['initial_manifest_sha256']==digest(BUILD/'initial-manifest.json')
    assert value['source_sha256']==p['source_sha256'] and all(digest(ROOT/n)==v for n,v in p['source_sha256'].items())
    assert all(digest(ROOT/n)==v for n,v in p['binding_sha256'].items())
    return p


def candidate_worker(entry):
    runner=importlib.import_module('inquiry_lab.cohort_wavecap_runner')
    return runner.run_task((entry['kind'],entry['seed'],((NAME,'immediate'),)))


def audit_worker(task):
    phase,item,entry,sources=task
    packed=(BUILD/(phase+'-cases')/item['file']).read_bytes();assert sha(packed)==item['compressed_sha256']
    body=gzip.decompress(packed);assert sha(body)==item['raw_sha256'];replay=json.loads(body);assert raw(replay)==body
    assert sha(raw(replay['scenario']))==entry['input_sha256']
    assert replay['initial_snapshot_sha256']==entry['snapshot_sha256'] and replay['environment_sha256']==entry['environment_sha256']
    assert replay['arms']==[[NAME,'immediate']] and len(replay['branches'])==1
    b=replay['branches'][0];assert [b['policy'],b['rule']]==[NAME,'immediate']
    assert b['snapshot_sha256']==entry['snapshot_sha256'] and b['environment_sha256']==entry['environment_sha256']
    patients=rows(b['patients']);expected=rows(entry['baseline_branches'][TAILWAIT]['patients'])
    assert patients and [key(p) for p in patients]==[key(p) for p in expected]
    auditor_path='inquiry_lab/cohort_wavecap_audit.py';assert digest(ROOT/auditor_path)==sources[auditor_path]
    audit=importlib.import_module('inquiry_lab.cohort_wavecap_audit').audit_replay(replay)
    assert audit['passed'] and audit['new_executions']==0 and raw(replay)==body
    compact=dict(kind=entry['kind'],seed=entry['seed'],family=entry['family'],scenario_id=entry['scenario_id'],
        parent_stage=entry['parent_stage'],original_origin=entry['original_origin'],
        branches=dict(deepcopy(entry['baseline_branches']),**{PRIMARY:dict(patients=patients)}))
    return dict(item,audit=audit,compact=compact)


def legacy_assessments(cases,protocol):
    if len(cases)!=360:
        return dict(evaluated=False,reason='Initial24 is an early rejection subset; original full-slice goals remain unchanged',goals=protocol['old_full_goals'])
    assert {o:sum(c['compact']['parent_stage']==o for c in cases) for o in ('repair','development')}==dict(repair=240,development=120)
    assert {o:sum(len(c['compact']['branches'][PRIMARY]['patients']) for c in cases if c['compact']['parent_stage']==o) for o in ('repair','development')}==dict(repair=827,development=415)
    original=importlib.import_module('inquiry_lab.cohort_batchwave_experiment')
    namespace=dict(vars(original));namespace.update(NAME=NAME,PRIMARY=PRIMARY,
        ARMS=tuple(tuple(n.split('|')) for n in protocol['entries'][0]['baseline_branches'])+((NAME,'immediate'),),
        control_ledger=lambda:read(TW_BUILD/'preservation/control-ledger.json'))
    for n in LEGACY_FUNCTIONS:
        fn=getattr(original,n);namespace[n]=types.FunctionType(fn.__code__,namespace,n,fn.__defaults__,fn.__closure__)
        namespace[n].__kwdefaults__=deepcopy(fn.__kwdefaults__)
    value=namespace['evaluate'](cases,'repair')
    return dict(evaluated=True,source='immutable666 original numeric functions with separate candidate globals; no frozen module mutation',
        assessment=value['assessment'],retention_assessment=namespace['retention_assessment'](cases,'repair'),
        pareto_retention_assessment=namespace['pareto_retention_assessment'](cases,'repair'),
        origin_metrics=value['origin_metrics'],origin_paired=value['origin_paired'],family_metrics=value['family_metrics'])


def _execute(phase,workers=2):
    assert phase in ('initial','expand') and type(workers) is int and 1<=workers<=4
    task_tmp=(BUILD/'tmp').resolve();assert Path(os.environ.get('TEMP','')).resolve()==task_tmp and Path(os.environ.get('TMP','')).resolve()==task_tmp
    protocol=frozen();prior=None
    if phase=='expand':
        prior=read(BUILD/'initial-report.json')
        assert prior['early_gate']['passed'] and prior['source_sha256']==protocol['source_sha256']
        assert prior['source_freeze_sha256']==digest(BUILD/'diagnostic-source-freeze.json')
        assert [c['source_index'] for c in prior['cases']]==protocol['initial_indices']
        assert early_gate(prior['cases'])==prior['early_gate']
        assert prior['raw_index_sha256']==digest(BUILD/'initial-raw-index.json')
        assert prior['audit_index_sha256']==digest(BUILD/'initial-audit-index.json')
        old_index=read(BUILD/'initial-audit-index.json')
        assert old_index['passed'] and old_index['source_sha256']==protocol['source_sha256']
        proofs=old_index['cases']
        raw_index=read(BUILD/'initial-raw-index.json')
        assert raw_index['all_outcomes_saved'] and [x['source_index'] for x in raw_index['cases']]==protocol['initial_indices']
        raw_items={x['source_index']:x for x in raw_index['cases']}
        assert len(proofs)==24
        for case,proof in zip(prior['cases'],proofs):
            assert proof['compact_sha256']==sha(raw(case['compact'])) and proof['audit']==case['audit']
            entry=protocol['entries'][case['source_index']];item=raw_items[case['source_index']]
            assert proof['proof_file']==case['file']+'.audit.json' and Path(proof['proof_file']).name==proof['proof_file']
            assert digest(BUILD/'initial-proofs'/proof['proof_file'])==proof['proof_sha256']
            saved=read(BUILD/'initial-proofs'/proof['proof_file'])
            assert saved['passed'] is True and saved['phase']=='initial' and saved['source_index']==case['source_index']
            assert saved['source_map_sha256']==sha(raw(dict(sorted(protocol['source_sha256'].items()))))
            assert saved['compact_sha256']==proof['compact_sha256'] and saved['audit']==case['audit']
            assert case['file']==item['file']==f"{case['source_index']:03d}_{entry['seed']}.json.gz"
            for k in ('raw_sha256','compressed_sha256','input_sha256'):assert saved[k]==case[k]==item[k]
            assert proof['raw_sha256']==case['raw_sha256'] and case['input_sha256']==entry['input_sha256']
            for k in ('kind','seed','family','scenario_id','parent_stage','original_origin'):assert case['compact'][k]==entry[k]
    for suffix in ('-cases','-proofs','-raw-index.json','-audit-index.json','-report.json','-started.json','-failure.json'):
        assert not (BUILD/(phase+suffix)).exists()
    indices=protocol['initial_indices'] if phase=='initial' else protocol['expansion_indices']
    assert len(indices)==(24 if phase=='initial' else 336)
    todo=[protocol['entries'][i] for i in indices];start=time.perf_counter()
    write_new(BUILD/(phase+'-started.json'),dict(indices=indices,source_freeze_sha256=digest(BUILD/'diagnostic-source-freeze.json'),
        diagnostic_candidate_branches_scheduled=len(todo),heldout=False,controls_reexecuted=0))
    folder=BUILD/(phase+'-cases');folder.mkdir();items=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for entry,replay in zip(todo,pool.map(candidate_worker,todo,chunksize=1)):
            body=raw(replay);packed=gzip.compress(body,compresslevel=6,mtime=0);name=f"{entry['source_index']:03d}_{entry['seed']}.json.gz"
            write_path=folder/name
            with write_path.open('xb') as f:f.write(packed)
            items.append(dict(file=name,source_index=entry['source_index'],input_sha256=entry['input_sha256'],
                raw_sha256=sha(body),compressed_sha256=sha(packed)))
    assert len(items)==len(todo)
    write_new(BUILD/(phase+'-raw-index.json'),dict(all_outcomes_saved=True,cases=items))
    # No audit starts until every raw for this phase has been saved.
    proof_folder=BUILD/(phase+'-proofs');proof_folder.mkdir();cases=[];proofs=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for case in pool.map(audit_worker,[(phase,item,entry,protocol['source_sha256']) for item,entry in zip(items,todo)],chunksize=1):
            proof_file=case['file']+'.audit.json'
            proof=dict(passed=True,phase=phase,source_index=case['source_index'],raw_sha256=case['raw_sha256'],
                compressed_sha256=case['compressed_sha256'],input_sha256=case['input_sha256'],
                source_map_sha256=sha(raw(dict(sorted(protocol['source_sha256'].items())))),
                audit=case['audit'],compact_sha256=sha(raw(case['compact'])))
            write_new(proof_folder/proof_file,proof)
            proofs.append(dict(file=case['file'],raw_sha256=case['raw_sha256'],audit=case['audit'],
                compact_sha256=proof['compact_sha256'],proof_file=proof_file,proof_sha256=digest(proof_folder/proof_file)))
            cases.append(case)
    write_new(BUILD/(phase+'-audit-index.json'),dict(passed=True,cases=proofs,
        source_sha256=protocol['source_sha256'],independent_semantic_audit_executions=len(cases),independent_public_model_reconstruction=True))
    if prior is not None:cases=sorted(prior['cases']+cases,key=lambda c:c['source_index'])
    assert [c['source_index'] for c in cases]==(protocol['initial_indices'] if phase=='initial' else list(range(360)))
    assert frozen()==protocol
    gate=early_gate(cases);legacy=legacy_assessments(cases,protocol)
    effect_keys=('wavecap_fallback_calls_checked','original_over_cap_plans_checked','triggered_corrections_checked',
        'unchanged_fallbacks_checked','immediate_send_changes','plan_changes','next_review_changes',
        'wavecap_groups_copied_checked','wavecap_group_members_copied_checked')
    effects={k:sum(branch.get(k,0) for c in cases for branch in c['audit'].get('branch_audits',[])) for k in effect_keys}
    report=dict(name=NAME,phase=phase,source_sha256=protocol['source_sha256'],source_freeze_sha256=digest(BUILD/'diagnostic-source-freeze.json'),
        elapsed_seconds=time.perf_counter()-start,elapsed_scope='diagnostic simulation plus independent audits and accounting, not policy latency',
        cases=cases,early_gate=gate,legacy_full_goals=legacy,observed_training_only=True,heldout=False,
        effect_counts=effects,model_plan_effect_observed=effects['plan_changes']>0,
        current_action_or_review_effect_observed=effects['immediate_send_changes']+effects['next_review_changes']>0,
        diagnostic_candidate_branches_this_phase=len(todo),cumulative_candidate_branches=len(cases),
        audit_proofs_reused=0 if prior is None else 24,controls_reexecuted=0,parent_raw_decompressions_by_driver=0,
        raw_index_sha256=digest(BUILD/(phase+'-raw-index.json')),audit_index_sha256=digest(BUILD/(phase+'-audit-index.json')),
        operational_adoption=False,live_default='fixed_3',expansion_allowed=phase=='initial' and gate['passed'])
    write_new(BUILD/(phase+'-report.json'),report)
    print(dict(phase=phase,cases=len(cases),early_gate_passed=gate['passed'],expansion_allowed=report['expansion_allowed'],
        legacy_full_goals_evaluated=legacy['evaluated'],report_sha256=digest(BUILD/(phase+'-report.json'))),flush=True)


def execute(phase,workers=2):
    try:return _execute(phase,workers)
    except BaseException as exc:
        marker=BUILD/(str(phase)+'-started.json');failure=BUILD/(str(phase)+'-failure.json')
        if marker.exists() and not failure.exists() and not (BUILD/(str(phase)+'-report.json')).exists():
            folder=BUILD/(str(phase)+'-cases');proofs=BUILD/(str(phase)+'-proofs')
            write_new(failure,dict(failed=True,phase=phase,exception_type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc(),
                raw_prefix_saved=sum(1 for _ in folder.iterdir()) if folder.exists() else 0,
                durable_completed_audit_proofs=sum(1 for _ in proofs.iterdir()) if proofs.exists() else 0,
                all_phase_outcomes_saved=(BUILD/(str(phase)+'-raw-index.json')).exists(),
                total_started_semantic_attempt_count=None,total_started_semantic_attempt_count_measured=False,
                automatic_performance_retry=False,operational_adoption=False))
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=('freeze','initial','expand'));p.add_argument('--checks');p.add_argument('--workers',type=int,default=2)
    a=p.parse_args()
    if a.phase=='freeze':
        assert a.checks;freeze(Path(a.checks))
    else:execute(a.phase,a.workers)
