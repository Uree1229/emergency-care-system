"""Two independent predeclared arms: cheap output screen, then semantic audit.

Failed screens have numeric/invariant evidence only. All diagnostics are known
training inputs, not holdouts, promotion results or live-server changes.
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
import traceback
import types
from . import cohort_wavecap_diagnostic as parent

ROOT=parent.ROOT
BUILD=ROOT/'.codex-build/eat-diverse-20261026'
WAVECAP_BUILD=parent.BUILD
WAVECAP_ARCHIVE='46bdded6048b27ada573da9ad2491b9c7fad5038c88c3686061db1ad19d423aa'
SCREEN=(0,1,2,27,98,100,240,241,242,288,338,340)
INITIAL=parent.INITIAL_INDICES
ARMS={n:dict(name='eat_cohort_'+n+'_l15_f',runner='inquiry_lab.cohort_'+n+'_runner',
             auditor='inquiry_lab.cohort_'+n+'_audit') for n in ('swap','releasealign')}
NEW_SOURCES=tuple('inquiry_lab/cohort_swap_'+n+'.py' for n in ('model','policy','context','runner'))+tuple(
    'test_inquiry_lab_cohort_swap_'+n+'.py' for n in ('model','policy','context','runner'))+tuple(
    'inquiry_lab/cohort_releasealign_'+n+'.py' for n in ('policy','context','runner'))+tuple(
    'test_inquiry_lab_cohort_releasealign_'+n+'.py' for n in ('policy','context','runner'))+(
    'inquiry_lab/cohort_swap_audit.py','test_inquiry_lab_cohort_swap_audit.py',
    'inquiry_lab/cohort_releasealign_audit.py','test_inquiry_lab_cohort_releasealign_audit.py',
    'inquiry_lab/diverse_diagnostic.py','test_inquiry_lab_diverse_diagnostic.py')


def raw(v):return parent.raw(v)
def read(p):return parent.read(p)
def digest(p):return parent.digest(p)
def sha(v):return parent.sha(v)
def write(p,v):return parent.write_new(p,v)
def path(arm,phase,suffix):return BUILD/(arm+'-'+phase+suffix)


def numeric(arm):
    """Same immutable numeric code; candidate globals copied, never patched."""
    g=dict(vars(parent));g.update(NAME=ARMS[arm]['name'],PRIMARY=ARMS[arm]['name']+'|immediate')
    for n in ('comparison','early_gate','legacy_assessments'):
        f=getattr(parent,n);g[n]=types.FunctionType(f.__code__,g,n,f.__defaults__,f.__closure__)
        g[n].__kwdefaults__=deepcopy(f.__kwdefaults__)
    return types.SimpleNamespace(**{n:g[n] for n in ('comparison','early_gate','legacy_assessments')})


def numeric_counters(audits):
    # Preserve exact names, especially wavecap_*; never silently strip prefixes.
    result={}
    for audit in audits:
        for branch in (audit or {}).get('branch_audits',[]):
            for k,v in branch.items():
                if type(v) in (int,float):result[k]=result.get(k,0)+v
    return result


def incremental_research_gate(legacy):
    """Observe harmless partial gains, without replacing any legacy goal."""
    checks=dict(both_observed_training_slices_present=legacy['checks']['both_observed_training_slices_present'])
    strict=[]
    for origin in ('repair','development'):
        scope=legacy['scopes'][origin]
        for label in ('tailwait','pairshield','stoploss'):
            checks[origin+'_'+label+'_success_identities_preserved']=scope[label]['lost_selections']==0
        tw=scope['tailwait']
        checks[origin+'_total_wait_not_greater_than_tailwait']=tw['wait_sum_delta_seconds']<=0
        checks[origin+'_every_patient_wait_not_greater_than_tailwait']=tw['maximum_individual_wait_increase_seconds']==0
        checks[origin+'_every_patient_arrival_cost_not_greater_than_tailwait']=tw['maximum_individual_arrival_cost_increase_seconds']==0
        strict.append(tw['wait_sum_delta_seconds']<0)
    checks['at_least_one_origin_strict_wait_improvement_vs_tailwait']=any(strict)
    return dict(passed=all(checks.values()),checks=checks,reference_views_path='early_gate.scopes',
        scope='known_training_incremental_characterization_only',legacy_goals_replaced=False,
        fresh_validation_allowed=False,operational_adoption=False)


def erratum():
    r=read(WAVECAP_BUILD/'initial-report.json')
    archive=read(WAVECAP_BUILD/'diagnostic-archive.json')
    assert digest(WAVECAP_BUILD/'initial-report.json')==archive['initial_report_sha256']
    values=numeric_counters(c['audit'] for c in r['cases'])
    return dict(scope='Metadata counter-prefix correction only; archived outcomes, gates, source678 and documents are unchanged',
        archived_report_sha256=archive['initial_report_sha256'],original_summary=r['effect_counts'],
        corrected_exact_prefixed_counters={k:v for k,v in values.items() if k.startswith('wavecap_')},
        new_simulation_or_semantic_executions=0,new_raw_decompressions=0)


def bindings():
    assert digest(WAVECAP_BUILD/'diagnostic-archive.json')==WAVECAP_ARCHIVE
    archive=read(WAVECAP_BUILD/'diagnostic-archive.json');assert archive['archive_integrity_passed'] and archive['source_count']==678
    p=parent.bindings();assert p['source_sha256']==archive['source_sha256']
    assert len(NEW_SOURCES)==len(set(NEW_SOURCES))==20 and not set(NEW_SOURCES)&set(p['source_sha256'])
    p['source_sha256'].update({n:digest(ROOT/n) for n in NEW_SOURCES});assert len(p['source_sha256'])==698
    p['binding_sha256'][(WAVECAP_BUILD/'diagnostic-archive.json').relative_to(ROOT).as_posix()]=WAVECAP_ARCHIVE
    p.update(name='eat_diverse_20261026',arms=ARMS,screen_indices=list(SCREEN),initial_indices=list(INITIAL),
        expansion_indices=[i for i in range(360) if i not in INITIAL],heldout=False,observed_training_only=True,
        screen_scope='Output schema and captured cap invariants plus independent stored numeric comparison; no semantic audit',
        characterization_scope='Incremental research gate plus independent receipt permits observed-training expansion only; not legacy acceptance',
        promotion_scope='Original27 always reported unchanged; full old9/retention9/Pareto17 unchanged at360; no automatic fresh validation or adoption; livefixed3 unchanged')
    assert len(SCREEN)==len(set(SCREEN))==12 and set(SCREEN)<=set(INITIAL)
    assert {o:sum(p['entries'][i]['parent_stage']==o for i in SCREEN) for o in ('repair','development')}==dict(repair=6,development=6)
    return p


def freeze(checks_path):
    assert not (BUILD/'source-freeze.json').exists()
    p=bindings();checks=read(checks_path)
    assert checks['passed'] and checks['errors']==checks['failures']==0 and checks['scope']=='targeted_diverse_candidate_tests'
    assert checks['source_sha256_before']==checks['source_sha256_after']==p['source_sha256']
    private=checks['private_files_sha256'];assert private and all(digest(ROOT/n)==v for n,v in private.items())
    assert checks['independent_checker_sha256'] in private.values()
    p['binding_sha256'].update(private);p['binding_sha256'][Path(checks_path).resolve().relative_to(ROOT).as_posix()]=digest(checks_path)
    p['independent_checker_sha256']=checks['independent_checker_sha256']
    write(BUILD/'prior-effect-summary-erratum.json',erratum())
    p['binding_sha256'][(BUILD/'prior-effect-summary-erratum.json').relative_to(ROOT).as_posix()]=digest(BUILD/'prior-effect-summary-erratum.json')
    write(BUILD/'input-manifest.json',p)
    write(BUILD/'source-freeze.json',dict(passed=True,protocol_sha256=digest(BUILD/'input-manifest.json'),
        source_sha256=p['source_sha256'],candidate_branches_before_freeze=0,
        qualification='Targeted new tests only; frozen full suite not rerun, previous10 historical failures not reclassified'))


def frozen():
    f=read(BUILD/'source-freeze.json');p=read(BUILD/'input-manifest.json')
    assert f['passed'] and f['protocol_sha256']==digest(BUILD/'input-manifest.json') and f['source_sha256']==p['source_sha256']
    assert all(digest(ROOT/n)==v for n,v in p['source_sha256'].items())
    assert all(digest(ROOT/n)==v for n,v in p['binding_sha256'].items())
    return p


def output_case(arm,item,entry,replay):
    name=ARMS[arm]['name'];branch=replay['branches'][0]
    assert replay['arms']==[[name,'immediate']] and len(replay['branches'])==1
    assert [branch['policy'],branch['rule']]==[name,'immediate']
    assert sha(raw(replay['scenario']))==entry['input_sha256']
    assert replay['initial_snapshot_sha256']==branch['snapshot_sha256']==entry['snapshot_sha256']
    assert replay['environment_sha256']==branch['environment_sha256']==entry['environment_sha256']
    patients=parent.rows(branch['patients']);before=parent.rows(entry['baseline_branches'][parent.TAILWAIT]['patients'])
    assert patients and [parent.key(p) for p in patients]==[parent.key(p) for p in before]
    assert all(p['inquiry_count']<=10 and len(p['contacted'])==len(set(p['contacted'])) for p in patients)
    calls=branch['policy_calls']
    for call in calls:
        inp,out=call['inputs'],call['output'];now=inp['now'];sent=out['send']
        own=inp['patient_pending'];fresh=[q for q in own if 0<=now-q['submitted_at']<=180]
        assert call['name']==name and len(sent)==len(set(sent)) and len(sent)<=inp['max_new']
        assert len(fresh)+len(sent)<=4
        available={c.get('hospital_id',c.get('hospital')) for c in inp['candidates']}
        assert set(sent)<=available
    compact={k:entry[k] for k in ('kind','seed','family','scenario_id','parent_stage','original_origin')}
    compact['branches']=dict(deepcopy(entry['baseline_branches']),**{name+'|immediate':dict(patients=patients)})
    return dict(item,audit=None,semantic_verified=False,compact=compact,
        output_invariants=dict(passed=True,patients=len(patients),captured_public_cap_calls_checked=len(calls),
            scope='Output/header/contact caps only; not FIFO, provenance, model or complete policy semantics'))


def read_candidate(task):
    arm,phase,item,entry,sources,semantic=task
    folder=item['raw_folder'];assert folder in (arm+'-screen-cases',arm+'-initial24-cases',arm+'-full360-cases')
    assert Path(item['file']).name==item['file'] and item['file']==f"{entry['source_index']:03d}_{entry['seed']}.json.gz"
    packed=(BUILD/folder/item['file']).read_bytes();assert sha(packed)==item['compressed_sha256']
    body=gzip.decompress(packed);assert sha(body)==item['raw_sha256'];replay=json.loads(body);assert raw(replay)==body
    case=output_case(arm,item,entry,replay)
    if semantic:
        module=ARMS[arm]['auditor'];source=module.replace('.','/')+'.py';assert digest(ROOT/source)==sources[source]
        audit=importlib.import_module(module).audit_replay(replay)
        assert audit['passed'] and audit['new_executions']==0 and raw(replay)==body
        case.update(audit=audit,semantic_verified=True)
    return case


def run_candidate(task):
    arm,entry=task
    return importlib.import_module(ARMS[arm]['runner']).run_task((entry['kind'],entry['seed'],((ARMS[arm]['name'],'immediate'),)))


def verify_prior(arm,phase,p):
    r=read(path(arm,phase,'-report.json'))
    assert numeric(arm).early_gate(r['cases'])==r['early_gate']
    assert r['incremental_research_gate']==incremental_research_gate(r['early_gate'])
    assert r['incremental_research_gate']['passed']
    receipt=read(path(arm,phase,'-independent.json'))
    assert r['source_sha256']==p['source_sha256'] and r['source_freeze_sha256']==digest(BUILD/'source-freeze.json')
    expected=list(SCREEN) if phase=='screen' else list(INITIAL)
    assert [c['source_index'] for c in r['cases']]==expected
    assert receipt['passed'] and receipt['candidate']==ARMS[arm]['name'] and receipt['phase']==phase
    assert receipt['checker_sha256']==p['independent_checker_sha256'] and receipt['cases_checked']==len(expected)
    assert receipt['report_sha256']==digest(path(arm,phase,'-report.json'))
    assert receipt['raw_index_sha256']==r['raw_index_sha256']==digest(path(arm,phase,'-raw-index.json'))
    assert receipt['source_freeze_sha256']==r['source_freeze_sha256'] and receipt['early_gate_equal'] and receipt['vectors_equal']
    assert receipt['incremental_gate_equal']
    items=read(path(arm,phase,'-raw-index.json'))
    assert items['all_outcomes_saved'] and [i['source_index'] for i in items['cases']]==expected
    for c,item in zip(r['cases'],items['cases']):
        for k in ('file','raw_folder','source_index','input_sha256','raw_sha256','compressed_sha256'):assert c[k]==item[k]
        entry=p['entries'][c['source_index']];assert c['input_sha256']==entry['input_sha256']
        for k in ('kind','seed','family','scenario_id','parent_stage','original_origin'):assert c['compact'][k]==entry[k]
    if phase=='screen':
        assert r['semantic_verification'] is False and r['independent_semantic_audits']==0
        assert all(c['audit'] is None and not c['semantic_verified'] for c in r['cases'])
    else:
        idx=read(path(arm,phase,'-audit-index.json'));assert idx['passed'] and len(idx['cases'])==24
        assert r['audit_index_sha256']==digest(path(arm,phase,'-audit-index.json'))
        for c,summary in zip(r['cases'],idx['cases']):
            proof_path=path(arm,phase,'-proofs')/summary['proof_file']
            assert Path(summary['proof_file']).name==summary['proof_file'] and digest(proof_path)==summary['proof_sha256']
            proof=read(proof_path)
            assert proof['passed'] and proof['candidate']==ARMS[arm]['name'] and proof['phase']==phase
            for k in ('source_index','input_sha256','raw_sha256','compressed_sha256'):assert proof[k]==c[k]
            assert proof['source_map_sha256']==sha(raw(dict(sorted(p['source_sha256'].items()))))
            assert proof['compact_sha256']==summary['compact_sha256']==sha(raw(c['compact']))
            assert proof['audit']==summary['audit']==c['audit'] and c['semantic_verified']
    return r


def execute(arm,command,workers=2):
    assert arm in ARMS and command in ('screen','expand') and type(workers) is int and 1<=workers<=4
    temp=(BUILD/'tmp').resolve();assert Path(os.environ.get('TEMP','')).resolve()==temp and Path(os.environ.get('TMP','')).resolve()==temp
    p=frozen();prior=None
    if command=='screen':phase='screen';indices=list(SCREEN)
    elif not path(arm,'initial24','-report.json').exists():
        prior=verify_prior(arm,'screen',p);phase='initial24';indices=[i for i in INITIAL if i not in SCREEN]
    else:
        prior=verify_prior(arm,'initial24',p);phase='full360';indices=p['expansion_indices']
    for s in ('-started.json','-failure.json','-cases','-proofs','-raw-index.json','-audit-index.json','-report.json'):
        assert not path(arm,phase,s).exists()
    start=time.perf_counter();items=[];proofs=[];entries=[p['entries'][i] for i in indices]
    write(path(arm,phase,'-started.json'),dict(candidate=ARMS[arm]['name'],phase=phase,indices=indices,
        source_freeze_sha256=digest(BUILD/'source-freeze.json'),new_candidate_branches_scheduled=len(indices),
        known_training=True,heldout=False,controls_reexecuted=0))
    folder=path(arm,phase,'-cases');folder.mkdir()
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for entry,replay in zip(entries,pool.map(run_candidate,[(arm,e) for e in entries],chunksize=1)):
                body=raw(replay);packed=gzip.compress(body,compresslevel=6,mtime=0);file=f"{entry['source_index']:03d}_{entry['seed']}.json.gz"
                with (folder/file).open('xb') as f:f.write(packed)
                items.append(dict(file=file,raw_folder=folder.name,source_index=entry['source_index'],
                    input_sha256=entry['input_sha256'],raw_sha256=sha(body),compressed_sha256=sha(packed)))
        assert len(items)==len(indices)
        if phase=='initial24':
            old=read(path(arm,'screen','-raw-index.json'))['cases'];items=sorted(old+items,key=lambda i:i['source_index'])
        all_items=items
        if phase=='full360':all_items=sorted(read(path(arm,'initial24','-raw-index.json'))['cases']+items,key=lambda i:i['source_index'])
        write(path(arm,phase,'-raw-index.json'),dict(all_outcomes_saved=True,cases=all_items))
        semantic=phase!='screen';cases=[]
        if semantic:path(arm,phase,'-proofs').mkdir()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            tasks=[(arm,phase,i,p['entries'][i['source_index']],p['source_sha256'],semantic) for i in items]
            for c in pool.map(read_candidate,tasks,chunksize=1):
                if semantic:
                    file=c['file']+'.audit.json'
                    proof={k:c[k] for k in ('source_index','input_sha256','raw_sha256','compressed_sha256')}
                    proof.update(passed=True,candidate=ARMS[arm]['name'],phase=phase,audit=c['audit'],
                        source_map_sha256=sha(raw(dict(sorted(p['source_sha256'].items())))),compact_sha256=sha(raw(c['compact'])))
                    write(path(arm,phase,'-proofs')/file,proof)
                    proofs.append(dict(proof_file=file,proof_sha256=digest(path(arm,phase,'-proofs')/file),
                        compact_sha256=proof['compact_sha256'],audit=c['audit']))
                cases.append(c)
        if semantic:write(path(arm,phase,'-audit-index.json'),dict(passed=True,cases=proofs,source_sha256=p['source_sha256']))
        if phase=='full360':cases=sorted(prior['cases']+cases,key=lambda c:c['source_index'])
        assert [c['source_index'] for c in cases]==(list(SCREEN) if phase=='screen' else list(INITIAL) if phase=='initial24' else list(range(360)))
        assert frozen()==p
        n=numeric(arm);gate=n.early_gate(cases);incremental=incremental_research_gate(gate);legacy=n.legacy_assessments(cases,p)
        report=dict(candidate=ARMS[arm]['name'],phase=phase,source_sha256=p['source_sha256'],source_freeze_sha256=digest(BUILD/'source-freeze.json'),
            raw_index_sha256=digest(path(arm,phase,'-raw-index.json')),audit_index_sha256=digest(path(arm,phase,'-audit-index.json')) if semantic else None,
            cases=cases,early_gate=gate,incremental_research_gate=incremental,legacy_full_goals=legacy,semantic_verification=semantic,
            semantic_scope='Full independent audit of this candidate24/336, prior24 reused at360' if semantic else 'No semantic audit; output screen only',
            independent_semantic_audits=len(items) if semantic else 0,semantic_proofs_reused=24 if phase=='full360' else 0,
            reused_semantic_index_sha256=digest(path(arm,'initial24','-audit-index.json')) if phase=='full360' else None,
            audited_numeric_fields=numeric_counters(c['audit'] for c in cases) if semantic else {},
            new_candidate_branches_this_phase=len(indices),cumulative_candidate_branches=len(cases),controls_reexecuted=0,
            heldout=False,observed_training_only=True,operational_adoption=False,live_default='fixed_3',
            elapsed_seconds=time.perf_counter()-start,elapsed_scope='Diagnostic execution/accounting/audit wall time, not policy latency',
            expansion_allowed=phase!='full360' and incremental['passed'],
            characterization_expansion_allowed=phase!='full360' and incremental['passed'],
            expansion_scope='Known-training incremental characterization only; original legacy gates remain unchanged and may fail',
            fresh_validation_allowed=False,promotion_allowed=False)
        write(path(arm,phase,'-report.json'),report)
        print(dict(candidate=arm,phase=phase,cases=len(cases),legacy_gate_passed=gate['passed'],
            incremental_research_gate_passed=incremental['passed'],semantic_verification=semantic,
            report_sha256=digest(path(arm,phase,'-report.json'))),flush=True)
    except BaseException as exc:
        if not path(arm,phase,'-failure.json').exists():
            write(path(arm,phase,'-failure.json'),dict(failed=True,candidate=ARMS[arm]['name'],phase=phase,
                exception_type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc(),raw_prefix_saved=sum(1 for _ in folder.iterdir()),
                durable_audit_proofs=len(proofs),semantic_attempts_started=0 if phase=='screen' else None,
                semantic_attempts_started_measured=phase=='screen',
                automatic_retry=False,operational_adoption=False))
        raise


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('command',choices=('freeze','screen','expand'));a.add_argument('--candidate',choices=tuple(ARMS));a.add_argument('--checks');a.add_argument('--workers',type=int,default=2)
    args=a.parse_args()
    if args.command=='freeze':assert args.checks;freeze(Path(args.checks))
    else:assert args.candidate;execute(args.candidate,args.command,args.workers)
