"""Origin-clock owner hedge surrogate; success first, then selection wait.

All performance scenarios retain the frozen payload's unknown priority (4).
Expert-priority behavior is checked separately in synthetic adapter units.
"""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import platform
import random
import time

from . import joint_budget_experiment as foundation, cohort_hedge_policy, cohort_hedge_runner

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'.codex-build/eat-cohort-hedge-20261021'
PARENT=ROOT/'.codex-build/eat-cohort-dynamic-20261021'
EXPERIMENT='eat_cohort_hedge_20261021'
NAMESPACE='eat-cohort-hedge-v1-20261021-278df90c-3c56-4ce6-9d38-0f7d62d76513'
PRIMARY=cohort_hedge_policy.NAME+'|immediate'
BUDGET=foundation.PRIMARY
COHORT_SUCCESS='eat_cohort_success_l15_f|immediate'
PREVIOUS='eat_cohort_dynamic_l15_f|immediate'
ARMS=foundation.ARMS+(('eat_cohort_success_l15_f','immediate'),('eat_cohort_dynamic_l15_f','immediate'),(cohort_hedge_policy.NAME,'immediate'))
REPAIR_ARMS=((cohort_hedge_policy.NAME,'immediate'),)
FIXTURE=foundation.FIXTURE
WORKING_REFERENCE=foundation.WORKING_REFERENCE
REFERENCE_EXCLUSION_SHA=foundation.REFERENCE_EXCLUSION_SHA
NEW_SOURCES=tuple('inquiry_lab/cohort_hedge_'+n+'.py' for n in ('policy','context','model','runner','audit','experiment'))+tuple(
    'test_inquiry_lab_cohort_hedge_'+n+'.py' for n in ('policy','context','model','runner','audit','experiment'))+tuple(
    '.codex-build/eat-cohort-hedge-20261021/'+n+'.py' for n in ('run_checks','preflight_equivalence','finalize_phase','build_scaffold'))
raw,sha,read,write_new,arm,wait=foundation.raw,foundation.sha,foundation.read,foundation.write_new,foundation.arm,foundation.wait
SETTINGS=dict(candidate_count=1,changed_factor='unchanged D menu/reference continuation plus residual current WAITING owners with fixed original-own CDF and owner-local H-derived hedge surrogates',
    objective='lexicographic F4,F2,F1,Fall,W; current benchmark all unknown/default4, hence failures then waiting',
    authorization='User chose priority and success count first, then waiting; identity-union targets retired after sealed capacity impossibility proof',
    known='D known240 retaining original B repair120/development120 origins;827 observed training patients',
    old_six_and_297_gain='reported regressions, not identity or unconditional wait gates under revised objective',
    intervention='only intact original D evaluated groups; first-cohort capture and actual actor retained; complete current WAITING owner inventory added for scoring only',
    continuation='intact D conditional reference continuation retained; residual owners use fixed original-prefix own reply distributions and static row-final backlog for private H-derived plans; no other-owner speculative plans appended',
    model_scope='first cohort plus every residual currently registered WAITING owner with positive public H; no future births, queue aging, future observations or hidden outcomes',
    priority='committed reported priority/source/effective priority and versions captured and rechecked; source-bound outside frozen default-priority lab, not journal-reconstructed clinical truth',
    global_optimum_or_clinical_validation_claim=False,oracle_input_to_policy=False,
    changed_current_sends_can_change_actual_later_H=True,template_is_not_reservation_or_future_outcome=True,
    model_tie='global tier4 max-1e-12, then2,then1; minW+1e-9; prefer original D selected row within final band else unchanged D menu order',
    repair_cached_branches_per_environment=12,repair_new_branches_per_environment=1,fresh_arms=13,
    repair_environments=240,development_environments=120,validation_environments=368,manifest_entries=728,maximum_new_branches=6584,
    actual_gate='known pooled F<=136 and W<113374 and lex<best12; fresh pooled lex<best12 and F<=D and W<D; origins and identities report-only',
    scope_reports='origin/mixed/stress/family outcomes and identities separately; old D origin-hard rejection preserved; those hard gates prospectively retired only for this candidate',
    validation_gate='all twelve fixed controls marginal lex CI, plus D upper deltafail<=0 and upper deltaW<0',
    confidence_scope='endpoint-wise marginal percentile95 bounds in a prespecified lexicographic gate; not a joint confidence region or familywise-adjusted twelve-control claim',
    bootstrap_replicates=10000,bootstrap_seed=20261021,hard_resource_contract='unchanged actual contact cap10,concurrent4,offer validity,A/H and atomic all-member apply',
    J_contacts_work_and_old_W='diagnostics, not unconditional gates overriding priority/success',
    fresh_seed_reuse=False,seed_scope='425 fresh role seeds, disjoint from prior namespaces; each stress seed shared across eight separately scored families',
    heldout_retuning=False,operational_adoption=False,live_server_changed=False)


def seeds(role,n):
    return tuple(int.from_bytes(hashlib.sha256(f'{NAMESPACE}|{role}|{i}'.encode()).digest()[:16],'big')|(1<<127) for i in range(n))


def known_rows():
    rows=read(PARENT/'repair.json')['cases']
    assert len(rows)==240
    assert [r['origin_stage'] for r in rows]==['repair']*120+['development']*120
    return [(row['origin_stage'],row) for row in rows]


def tasks(stage):
    if stage=='repair':return [(r['compact']['kind'],r['compact']['seed'],REPAIR_ARMS) for _,r in known_rows()]
    if stage not in ('development','validation'):raise ValueError('known stage required')
    n,stress=(96,3) if stage=='development' else (320,6)
    return [('mix',s,ARMS) for s in seeds(stage+'-mix',n)]+[('family:'+f,s,ARMS)
        for s in seeds(stage+'-stress',stress) for f in foundation.harness.scenarios.original.FAMILY_NAMES]


def source_hashes():
    before=read(BUILD/'preservation/before.json')
    return {n:sha((ROOT/n).read_bytes()) for n in sorted(set(before['old_source_sha256'])|set(NEW_SOURCES)|{FIXTURE})}


def verify_old():
    before=read(BUILD/'preservation/before.json');assert before['passed'] and before['known_environments']==240
    assert len(before['old_source_sha256'])==418 and len(before['reference_files_sha256'])==333
    assert before['known_parent_stage']=='repair' and before['known_origin_stages']==['repair','development']
    assert len(before['known_raw_bindings_by_path'])==1320
    assert sum(v['branches'] for v in before['known_raw_bindings_by_path'].values())==2880
    assert sha((BUILD/'preservation/reference-drift.json').read_bytes())==REFERENCE_EXCLUSION_SHA
    excluded=read(BUILD/'preservation/reference-drift.json')['excluded_working_reference_files']
    assert set(excluded)=={WORKING_REFERENCE}
    assert excluded[WORKING_REFERENCE]['parent_expected_sha256']==before['reference_files_sha256'][WORKING_REFERENCE]
    assert WORKING_REFERENCE not in before['old_source_sha256']
    for group in ('old_source_sha256','reference_files_sha256'):
        for name,digest in before[group].items():
            if name!=WORKING_REFERENCE:assert sha((ROOT/name).read_bytes())==digest,name
    for stem in ('parent_verification','parent_document'):
        assert sha((ROOT/before[stem+'_path']).read_bytes())==before[stem+'_sha256']
    return sha((BUILD/'preservation/before.json').read_bytes())


def regression_targets():
    targets=deepcopy(read(PARENT/'source-freeze.json')['protocol']['regressions_report_only'])
    gained=[];lost=[]
    for _,case in known_rows():
        compact=case['compact'];old={p['id']:p for p in compact['branches'][COHORT_SUCCESS]['patients']}
        for p in compact['branches'][PREVIOUS]['patients']:
            item=dict(seed=compact['seed'],patient=p['id'])
            if not old[p['id']]['failed'] and p['failed']:lost.append(item)
            if old[p['id']]['failed'] and not p['failed']:gained.append(item)
    assert len(gained)==3 and len(lost)==1
    targets.update(prior_dynamic_gained=gained,prior_dynamic_lost=lost)
    assert {k:len(v) for k,v in targets.items()}==dict(old_six=6,prior_development_tail_lost=7,prior_development_budget_gained=4,prior_dynamic_gained=3,prior_dynamic_lost=1)
    return targets


def protocol():
    return dict(experiment=EXPERIMENT,namespace=NAMESPACE,settings=SETTINGS,arms=[list(a) for a in ARMS],
        source_sha256=source_hashes(),parent_binding_sha256=verify_old(),regressions_report_only=regression_targets(),
        priority_performance_scope='all frozen lab payloads unknown/default effective4; expert-tier behavior synthetic units only',
        excluded_working_reference=WORKING_REFERENCE,reference_exclusion_sha256=REFERENCE_EXCLUSION_SHA,python=platform.python_version())


def freeze():
    p=protocol();tests=read(BUILD/'tests.json')
    assert tests['passed'] and tests['source_sha256_before']==tests['source_sha256_after']==p['source_sha256']
    required={'test_inquiry_lab_cohort_hedge_'+n for n in ('policy','context','model','runner','audit','experiment')}
    assert required<=set(tests['module_counts']) and all(tests['module_counts'][n]>0 for n in required)
    assert tests['errors']==tests['failures']==tests['skipped']==0
    roles=[*seeds('development-mix',96),*seeds('validation-mix',320),*seeds('development-stress',3),*seeds('validation-stress',6)]
    assert len(set(roles))==425 and not set(roles)&{s for _,s,_ in tasks('repair')}
    # Preserve every prior namespace, including generated-but-unexecuted heldout inputs.
    old_manifests=[PARENT/'input-manifest.json']+[ROOT/n for n in read(BUILD/'preservation/before.json')['reference_files_sha256'] if n.endswith('/input-manifest.json')]
    for path in set(old_manifests):assert not set(roles)&{x['seed'] for x in read(path)['entries']}
    for name in ('inquiry_lab_validation_20261020.json','inquiry_lab_confirm_validation_20261020.json'):
        assert not set(roles)&{x['seed'] for x in read(ROOT/'simulation_results'/name)['results']}
    preflight=read(BUILD/'preflight/parent-equivalence.json');assert preflight['passed'] and preflight['full_raw_equal'] and len(preflight['controls'])==12
    write_new(BUILD/'source-freeze.json',raw(dict(passed=True,protocol=p,formal_performance_runs_before_freeze=0,
        old_observed_stages_training=True,fresh_inputs_generated_before_freeze=False,preflight_seed7_fixture=FIXTURE,
        additional_adapter_family_seed7='simultaneous_w1',preflight_used_for_tuning=False)))
    previous={(e['origin_stage'],e['kind'],e['seed']):e['sha256'] for e in read(PARENT/'input-manifest.json')['entries'] if e['stage']=='repair'}
    known=known_rows();entries=[]
    for stage in ('repair','development','validation'):
        for i,(kind,seed,_) in enumerate(tasks(stage)):
            origin=known[i][0] if stage=='repair' else None
            digest=previous[origin,kind,seed] if origin else sha(raw(foundation.harness.build(kind,seed)))
            entries.append(dict(stage=stage,origin_stage=origin,kind=kind,seed=seed,sha256=digest))
    assert len(entries)==728
    write_new(BUILD/'input-manifest.json',raw(dict(entries=entries,fresh_inputs_generated_after_freeze=True)))
    names=['preservation/before.json','preservation/reference-drift.json','tests.json','source-freeze.json','input-manifest.json','preflight/parent-equivalence.json']
    names += [f.relative_to(BUILD).as_posix() for f in sorted((BUILD/'diagnosis').rglob('*')) if f.is_file()]
    write_new(BUILD/'pre-development.json',raw(dict(passed=True,protocol=p,evidence_sha256={n:sha((BUILD/n).read_bytes()) for n in names})))
    print(dict(frozen=True,sources=len(p['source_sha256']),inputs=len(entries)),flush=True)


def verify_freeze():
    p=read(BUILD/'pre-development.json');assert p['passed'] and p['protocol']==protocol()
    for name,digest in p['evidence_sha256'].items():assert sha((BUILD/name).read_bytes())==digest
    return p


def lex_vector(metric):
    return tuple(metric['failed_by_priority'][str(t)] for t in (4,2,1))+(metric['failed'],metric['wait_sum_seconds'])


def scope_metrics(compacts,names):
    result=foundation.scope_metrics(compacts,names)
    for name,metric in result.items():metric['failed_by_priority']={'4':metric['failed'],'2':0,'1':0}
    return result


def paired_lex_ci(compacts,before):
    rows=[]
    for c in compacts:
        new=c['branches'][PRIMARY]['patients'];old=c['branches'][before]['patients']
        assert {p['id'] for p in new}=={p['id'] for p in old}
        rows.append((len(new),sum(p['failed'] for p in new)-sum(p['failed'] for p in old),sum(wait(p) for p in new)-sum(wait(p) for p in old)))
    rng=random.Random(20261021);fs=[];ws=[]
    for _ in range(10000):
        n=df=dw=0
        for _ in rows:
            r=rng.choice(rows);n+=r[0];df+=r[1];dw+=r[2]
        fs.append(df/n);ws.append(dw/n)
    fs.sort();ws.sort()
    return dict(failed_count_delta=sum(r[1] for r in rows),wait_sum_delta_seconds=sum(r[2] for r in rows),
        ci95_failed_per_patient=[fs[250],fs[9749]],ci95_wait_per_patient=[ws[250],ws[9749]],
        replicates=10000,cluster='scenario',all_priorities_default4=True,
        confidence_scope='marginal endpoint-wise95; not joint or familywise-adjusted')


def assess(metrics,origin_metrics,ci,stage):
    controls=[arm(*a) for a in ARMS if arm(*a)!=PRIMARY]
    c=metrics[PRIMARY];d=metrics[PREVIOUS]
    candidate=lex_vector(c);best=min(lex_vector(metrics[n]) for n in controls)
    if stage=='repair':
        checks=dict(both_known_origins_present=set(origin_metrics)=={'repair','development'},
            retained_at_least_two_net_successes_vs_CS=c['failed']<=136,
            selection_wait_below_D=c['wait_sum_seconds']<113374,
            whole_strictly_better_than_best_control=candidate<best)
    elif stage in ('development','validation'):
        checks=dict(whole_strictly_better_than_best_control=candidate<best,
            failure_count_no_worse_than_D=c['failed']<=d['failed'],selection_wait_below_D=c['wait_sum_seconds']<d['wait_sum_seconds'])
    else:raise ValueError('known stage required')
    if stage=='validation':
        for name in controls:
            f=ci[name]['ci95_failed_per_patient'][1];w=ci[name]['ci95_wait_per_patient'][1]
            checks['priority_success_then_wait_CI_vs_'+name]=f<0 or f<=0 and w<0
        checks['D_CI_failure_nonincrease']=ci[PREVIOUS]['ci95_failed_per_patient'][1]<=0
        checks['D_CI_wait_strict_decrease']=ci[PREVIOUS]['ci95_wait_per_patient'][1]<0
    return dict(passed=all(checks.values()),checks=checks,operational_adoption=False,
        objective='priority failures first, then capped selection wait; pooled user goal; origins and identity exchange reported')


def evaluate(cases,stage):
    whole=[c['compact'] for c in cases];names=[arm(*a) for a in ARMS];controls=[n for n in names if n!=PRIMARY]
    scopes={'whole':[c for c in whole],'mixed':[c for c in whole if c['kind']=='mix'],'stress':[c for c in whole if c['kind']!='mix']}
    metrics={scope:scope_metrics(compacts,names) for scope,compacts in scopes.items()}
    paired={scope:{n:foundation.parent.paired(compacts,n,after=PRIMARY,bootstrap=False) for n in controls} for scope,compacts in scopes.items()}
    origins={}
    if stage=='repair':
        for origin in ('repair','development'):
            selected=[c['compact'] for c in cases if c['origin_stage']==origin]
            origins[origin]={scope:scope_metrics([c for c in selected if scope=='whole' or (c['kind']=='mix')==(scope=='mixed')],names) for scope in ('whole','mixed','stress')}
    ci={n:paired_lex_ci(whole,n) for n in controls}
    regression={}
    if stage=='repair':
        lookup={(c['origin_stage'],c['compact']['seed'],p['id']):p for c in cases for p in c['compact']['branches'][PRIMARY]['patients']}
        for label,targets in regression_targets().items():
            origin='repair' if label=='old_six' else 'development'
            regression[label]=[dict(**x,selected=not lookup[origin,x['seed'],x['patient']]['failed'],selection_seconds=lookup[origin,x['seed'],x['patient']]['selection_seconds']) for x in targets]
    return dict(metrics=metrics,origin_metrics=origins,paired=paired,whole_lex_ci=ci,regressions_report_only=regression,
        family_metrics={f:scope_metrics([c for c in scopes['stress'] if c['family']==f],names) for f in foundation.harness.scenarios.original.FAMILY_NAMES},
        assessment=assess(metrics['whole'],{o:m['whole'] for o,m in origins.items()},ci,stage),
        priority_performance_scope='all default4: no tier-diverse clinical efficacy claim')


def cache_specs(origin,ref):
    assert origin in ('repair','development')
    specs=[('cohort_dynamic',PARENT/'repair-cases',ref,{PREVIOUS})]
    residual={arm(*a) for a in foundation.ARMS if a[0] not in ('eat_joint_budget_l15_f','eat_joint_reselect_l15_f','eat_joint_tail_l15_f','eat_joint_first_l15_f','eat_horizon_l15_f')}
    layout=[('cohort_success',ROOT/'.codex-build/eat-cohort-success-20261021/repair-cases',{COHORT_SUCCESS})]
    layout+= [('bundle',ROOT/'.codex-build/eat-joint-budget-20261021/development-cases',{arm(*a) for a in foundation.ARMS})] if origin=='development' else [
        ('budget',ROOT/'.codex-build/eat-joint-budget-20261021/repair-cases',{BUDGET}),
        ('reselect',foundation.PARENT/'repair-cases',{foundation.RESELECT}),
        ('tail',foundation.TAIL_PARENT/'repair-cases',{foundation.TAIL}),
        ('joint',foundation.JOINT_PARENT/'repair-cases',{foundation.JOINT}),
        ('horizon',foundation.HORIZON_PARENT/'repair-cases',{foundation.HORIZON}),
        ('residual',foundation.RESIDUAL_PARENT/'development-cases',residual)]
    assert set(ref['cached_parent_reference'])=={label for label,_,_ in layout}
    for label,folder,names in layout:
        inherited=ref['cached_parent_reference'][label]
        path=ROOT/inherited['path'];assert path.parent==folder and path.name.endswith('.json.gz')
        specs.append((label,folder,dict(inherited,file=path.name),names))
    return specs


def stage_cases(stage):
    from .cohort_hedge_audit import audit_replay
    manifest={(r['stage'],r['kind'],r['seed']):r for r in read(BUILD/'input-manifest.json')['entries']}
    new_folder=BUILD/(stage+'-cases');index=read(BUILD/(stage+'-raw-index.json'));todo=tasks(stage)
    assert index['all_outcomes_saved'] and len(index['cases'])==len(todo)
    assert {p.name for p in new_folder.iterdir()}=={r['file'] for r in index['cases']}
    old=known_rows() if stage=='repair' else None;result=[]
    bindings=read(BUILD/'preservation/before.json')['known_raw_bindings_by_path']
    for i,((kind,seed,arms),item) in enumerate(zip(todo,index['cases'])):
        assert item['file']==f'{i:03d}_{seed}.json.gz'
        z=(new_folder/item['file']).read_bytes();body=gzip.decompress(z)
        assert sha(z)==item['compressed_sha256'] and sha(body)==item['raw_sha256']
        replay=json.loads(body);assert raw(replay)==body and replay['arms']==[list(a) for a in arms]
        entry=manifest[stage,kind,seed];assert sha(raw(replay['scenario']))==entry['sha256']
        proof=audit_replay(replay);assert proof['passed'] and raw(replay)==body
        compact=foundation.parent.compact(replay);cache={};origin=None
        if old is not None:
            origin,ref=old[i];assert origin==entry['origin_stage']
            for label,cache_folder,metadata,expected in cache_specs(origin,ref):
                path=cache_folder/metadata['file'];bound=bindings[path.relative_to(ROOT).as_posix()]
                packed=path.read_bytes();content=gzip.decompress(packed)
                assert sha(packed)==metadata['compressed_sha256']==bound['compressed_sha256']
                assert sha(content)==metadata['raw_sha256']==bound['raw_sha256']
                cached=json.loads(content);assert raw(cached)==content
                c=foundation.parent.compact(cached);assert set(c['branches'])==expected
                assert cached['scenario']==replay['scenario'] and cached['initial_snapshot_sha256']==replay['initial_snapshot_sha256'] and cached['environment_sha256']==replay['environment_sha256']
                assert all(v==ref['compact']['branches'][k] for k,v in c['branches'].items())
                checked=audit_replay(cached);assert checked['passed'] and raw(cached)==content
                cache[label]=dict(path=path.relative_to(ROOT).as_posix(),raw_sha256=sha(content),compressed_sha256=sha(packed),audit=checked)
            combined=deepcopy(ref['compact']);combined['branches'].update(compact['branches']);compact=combined
        assert set(compact['branches'])=={arm(*a) for a in ARMS} and (compact['kind'],compact['seed'])==(kind,seed)
        result.append(dict(**item,origin_stage=origin,audit=proof,compact=compact,cached_parent_reference=cache))
    return result


def prior_evidence(stage):
    if stage=='repair':return {}
    previous='repair' if stage=='development' else 'development'
    return {previous+s:sha((BUILD/(previous+s)).read_bytes()) for s in ('.json','-audit.json','-lock.json')}


def verify_prior(stage):
    if stage=='repair':return {}
    previous='repair' if stage=='development' else 'development'
    r=read(BUILD/(previous+'.json'));a=read(BUILD/(previous+'-audit.json'));lock=read(BUILD/(previous+'-lock.json'))
    assert r['assessment']['passed'] and a['passed'] and lock['next_stage_allowed']
    assert sha((BUILD/(previous+'.json')).read_bytes())==a['report_sha256']==lock['report_sha256']
    assert lock['candidate']==PRIMARY and lock['assessment']==r['assessment'] and lock['protocol']==r['protocol']==protocol()
    assert a['source_sha256']==source_hashes() and stage_cases(previous)==r['cases']
    assert r['prior_stage_evidence_sha256']==prior_evidence(previous)
    for name,digest in r['prior_stage_evidence_sha256'].items():assert sha((BUILD/name).read_bytes())==digest
    return prior_evidence(stage)


def execute(stage,workers=4):
    frozen=verify_freeze();prior=verify_prior(stage);folder=BUILD/(stage+'-cases')
    assert all(not (BUILD/(stage+s)).exists() for s in ('.json','-raw-index.json','-audit.json','-lock.json','-cases'))
    todo=tasks(stage);folder.mkdir();items=[];started=time.perf_counter()
    manifest={(r['stage'],r['kind'],r['seed']):r['sha256'] for r in read(BUILD/'input-manifest.json')['entries']}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i,((kind,seed,_),replay) in enumerate(zip(todo,pool.map(cohort_hedge_runner.run_task,todo,chunksize=1))):
            assert sha(raw(replay['scenario']))==manifest[stage,kind,seed]
            body=raw(replay);packed=gzip.compress(body,compresslevel=6,mtime=0);name=f'{i:03d}_{seed}.json.gz'
            write_new(folder/name,packed);items.append(dict(file=name,raw_sha256=sha(body),compressed_sha256=sha(packed)))
            if len(items)%8==0:print(f'{stage} {len(items)}/{len(todo)} environments',flush=True)
    write_new(BUILD/(stage+'-raw-index.json'),raw(dict(cases=items,all_outcomes_saved=True)))
    cases=stage_cases(stage);assert protocol()==frozen['protocol']
    report=dict(schema_version=1,experiment=EXPERIMENT,stage=stage,protocol=frozen['protocol'],
        elapsed_seconds=time.perf_counter()-started,cases=cases,artifact_directory=str(folder),
        new_branches=len(todo)*(1 if stage=='repair' else len(ARMS)),cached_branches=len(todo)*12 if stage=='repair' else 0,
        prior_stage_evidence_sha256=prior,operational_adoption=False,**evaluate(cases,stage))
    assert prior==prior_evidence(stage)
    write_new(BUILD/(stage+'.json'),raw(report))
    if stage!='validation':write_new(BUILD/(stage+'-lock.json'),raw(dict(candidate=PRIMARY,protocol=frozen['protocol'],
        report_sha256=sha(raw(report)),assessment=report['assessment'],next_stage_allowed=report['assessment']['passed'])))
    print(dict(stage=stage,assessment=report['assessment']),flush=True)


def audit_saved(stage):
    verify_freeze();path=BUILD/(stage+'.json');r=read(path)
    assert r['protocol']==protocol() and r['stage']==stage and r['experiment']==EXPERIMENT
    assert r['operational_adoption'] is False and r['artifact_directory']==str(BUILD/(stage+'-cases'))
    assert r['prior_stage_evidence_sha256']==prior_evidence(stage)
    cases=stage_cases(stage);assert cases==r['cases']
    assert r['new_branches']==len(cases)*(1 if stage=='repair' else len(ARMS))
    assert r['cached_branches']==(len(cases)*12 if stage=='repair' else 0)
    for key,value in evaluate(cases,stage).items():assert r[key]==value
    write_new(BUILD/(stage+'-audit.json'),raw(dict(passed=True,report_sha256=sha(path.read_bytes()),
        source_sha256=source_hashes(),cases=len(cases),new_branches_audited=r['new_branches'],
        cached_branches_reaudited=r['cached_branches'],new_performance_runs=0)))
    print(dict(audit=stage,passed=True,cases=len(cases)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('freeze','repair','development','validation','audit-repair','audit-development','audit-validation'))
    p.add_argument('--workers',type=int,default=4);args=p.parse_args()
    if args.stage=='freeze':freeze()
    elif args.stage.startswith('audit-'):audit_saved(args.stage[6:])
    else:execute(args.stage,args.workers)
