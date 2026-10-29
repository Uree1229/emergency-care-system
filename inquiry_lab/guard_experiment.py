"""Residual recovery preservation with a conditional same-size batch guard.

Known repair is training evidence, followed by fresh development and a locked
held-out validation. Preserve every outcome; do not retune on held-out results.
"""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import platform
import time
from . import residual_experiment as parent, harness, guard_policy, guard_runner

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'.codex-build/eat-residual-guard-20261020'
PARENT=ROOT/'.codex-build/eat-residual-20261020'
EXPERIMENT='eat_guarded_recovery_20261020'
NAMESPACE='eat-guard-v1-20261020-9fde3199-cd40-4531-b005-15b22448b683'
PRIMARY='eat_guard_l15_f|immediate'
RESIDUAL='eat_residual_l15_f|immediate'
EAT='eat_l15_f|immediate'
FIXED='fixed_3|immediate'
ARMS=(('fixed_3','immediate'),('eat_l15_f','immediate'),('eat_residual_l15_f','immediate'),
    (guard_policy.NAME,'immediate'),('v3_w45_h900','immediate'),('eat_l15_f','hold120'))
REPAIR_ARMS=((guard_policy.NAME,'immediate'),)
NEW_SOURCES=tuple('inquiry_lab/guard_'+n+'.py' for n in ('policy','runner','audit','experiment'))+tuple(
    'test_inquiry_lab_guard_'+n+'.py' for n in ('policy','runner','audit','experiment'))+(
    '.codex-build/eat-residual-guard-20261020/run_checks.py',)
FIXTURE='simulation/fixtures/eat_guard_20261020/mix7.json.gz'
raw,sha,read,write_new=parent.raw,parent.sha,parent.read,parent.write_new
arm,wait=parent.arm,parent.wait
SETTINGS=dict(candidate_count=1,changed_factor='same-size immediate hospital replacement only for a full-capacity future-plan conflict',
    residual_head_model_and_old_pending_CDF_unchanged=True,nearest_six_and_lambda15_unchanged=True,
    actual_concurrency4_and_confirmation_immediate_unchanged=True,
    tie='within 1e-9 of the minimum among equal-size subsets, original pool order; replace only for >1e-9 improvement',
    original_residual_timing_and_immediate_count_preserved_at_identical_input=True,
    guard_trigger='current capacity full and an action within the next existing review interval exceeds capacity without a reply',
    guard_window='existing residual review_seconds (15); later plans are left to the next re-evaluation',
    primary='all-patient hospital-selection waiting; failures capped at 300 seconds',
    primary_excludes_travel_and_post_arrival_treatment_wait=True,
    repair='known previous residual development, all 120 environments; exact four recovered patients preserved',
    repair_new_branches_per_environment=1,repair_cached_reference_branches_per_environment=5,
    fresh_development_mix=96,fresh_validation_mix=320,stress_seeds=[3,6],fresh_arms=6,
    gates=['W lower than residual and EAT','no residual success lost','failures no more than residual and EAT',
        'J no more than residual and EAT','inquiries and review work no more than fixed3'],
    residual_success_preservation_scope='every patient in mixed and stress environments at every stage',
    validation_additional_gate='scenario-cluster 10000 resamples: upper95 deltaW < 0 vs residual and EAT',
    individual_delays_and_lost_selections_reported=True,universal_zero_harm_not_claimed=True,
    known_training_not_independent_validation=True,heldout_not_used_for_retuning=True,
    stress_excluded_from_primary=True,fresh_reference_branches_not_cached=True,
    operational_adoption=False,live_server_UI_database_changed=False,
    parent_preservation_scope='named parent source/reference binding; reuse transitive seal, no global scan')


def seeds(role,n):
    return tuple(int.from_bytes(hashlib.sha256(f'{NAMESPACE}|{role}|{i}'.encode()).digest()[:16],'big')|(1<<127)
        for i in range(n))


def tasks(stage):
    if stage=='repair':
        return [(c['compact']['kind'],c['compact']['seed'],REPAIR_ARMS) for c in read(PARENT/'development.json')['cases']]
    if stage not in ('development','validation'):raise ValueError('known stage required')
    count,n=(96,3) if stage=='development' else (320,6)
    return [('mix',s,ARMS) for s in seeds(stage+'-mix',count)]+[
        ('family:'+f,s,ARMS) for s in seeds(stage+'-stress',n) for f in harness.scenarios.original.FAMILY_NAMES]


def source_hashes():
    old=read(BUILD/'preservation/before.json')['old_source_sha256']
    return {n:sha((ROOT/n).read_bytes()) for n in sorted(set(old)|set(NEW_SOURCES)|{FIXTURE})}


def verify_old():
    before=read(BUILD/'preservation/before.json');assert before['passed']
    for group in ('old_source_sha256','reference_files_sha256'):
        for name,digest in before[group].items():assert sha((ROOT/name).read_bytes())==digest,name
    return sha((BUILD/'preservation/before.json').read_bytes())


def recoveries():
    answer=[]
    for c in read(PARENT/'development.json')['cases']:
        r=c['compact']
        if r['kind']!='mix':continue
        old={p['id']:p for p in r['branches'][EAT]['patients']}
        for p in r['branches'][RESIDUAL]['patients']:
            if old[p['id']]['failed'] and not p['failed']:
                answer.append(dict(seed=r['seed'],patient=p['id']))
    assert len(answer)==4
    return answer


def protocol():
    return dict(experiment=EXPERIMENT,settings=SETTINGS,arms=[list(a) for a in ARMS],
        namespace=NAMESPACE,source_sha256=source_hashes(),parent_binding_sha256=verify_old(),
        recovered_patient_regression_identifiers=recoveries(),python=platform.python_version())


def freeze():
    p=protocol();tests=read(BUILD/'tests.json')
    assert tests['passed'] and tests['source_sha256_before']==tests['source_sha256_after']==p['source_sha256']
    roles=[*seeds('development-mix',96),*seeds('validation-mix',320),
        *seeds('development-stress',3),*seeds('validation-stress',6)]
    assert len(set(roles))==425
    for path in (PARENT/'input-manifest.json',ROOT/'.codex-build/eat-shortlist-20261020/input-manifest.json',
            ROOT/'.codex-build/eat-residual-followup-20261020/input-manifest.json'):
        assert not set(roles)&{x['seed'] for x in read(path)['entries']}
    for path in ('inquiry_lab_validation_20261020.json','inquiry_lab_confirm_validation_20261020.json'):
        assert not set(roles)&{x['seed'] for x in read(ROOT/'simulation_results'/path)['results']}
    write_new(BUILD/'source-freeze.json',raw(dict(passed=True,protocol=p,formal_performance_runs_before_freeze=0,
        known_previous_repair_data_inspected=True,fresh_performance_inputs_generated_before_freeze=False,
        preflight_seed7_fixture_only=FIXTURE,recorded_at=time.strftime('%Y-%m-%d %H:%M:%S'))))
    entries=[]
    old_manifest={(x['kind'],x['seed']):x['sha256'] for x in read(PARENT/'input-manifest.json')['entries'] if x['stage']=='development'}
    for stage in ('repair','development','validation'):
        for kind,seed,_ in tasks(stage):
            digest=old_manifest[kind,seed] if stage=='repair' else sha(raw(harness.build(kind,seed)))
            entries.append(dict(stage=stage,kind=kind,seed=seed,sha256=digest))
    write_new(BUILD/'input-manifest.json',raw(dict(entries=entries,fresh_inputs_generated_after_freeze=True)))
    names=['preservation/before.json','tests.json','source-freeze.json','input-manifest.json',
        'preflight/parent-equivalence.json']
    names += [p.relative_to(BUILD).as_posix() for p in sorted((BUILD/'diagnosis').rglob('*')) if p.is_file()]
    evidence={n:sha((BUILD/n).read_bytes()) for n in names}
    write_new(BUILD/'pre-development.json',raw(dict(passed=True,protocol=p,evidence_sha256=evidence)))
    print(dict(frozen=True,sources=len(p['source_sha256']),inputs=len(entries)),flush=True)


def verify_freeze():
    p=read(BUILD/'pre-development.json');assert p['passed'] and p['protocol']==protocol()
    for name,digest in p['evidence_sha256'].items():assert sha((BUILD/name).read_bytes())==digest
    return p


def assess(metrics,pairs,stage,recovery_check=None,all_case_preservation=None):
    if stage not in ('repair','development','validation'):raise ValueError('known stage required')
    c=metrics[PRIMARY];r=metrics[RESIDUAL];e=metrics[EAT];f=metrics[FIXED]
    checks=dict(wait_lower_than_residual=pairs[RESIDUAL]['wait_sum_delta_seconds']<0,
        wait_lower_than_EAT=pairs[EAT]['wait_sum_delta_seconds']<0,
        no_residual_success_lost=pairs[RESIDUAL]['lost_selections']==0 and all_case_preservation is not None
            and all_case_preservation['lost_selections']==0,
        failures_not_increased=c['failed']<=min(r['failed'],e['failed']),
        J_not_increased=c['J']<=min(r['J'],e['J']),
        inquiry_not_more_than_fixed3=c['inquiries_per_patient']<=f['inquiries_per_patient'],
        work_not_more_than_fixed3=c['review_work_seconds_per_patient']<=f['review_work_seconds_per_patient'])
    if stage=='repair':checks['all_four_exact_recovered_patients_preserved']=recovery_check is not None and all(x['preserved'] for x in recovery_check) and len(recovery_check)==4
    if stage=='validation':
        checks.update(wait_CI_upper_below_zero_vs_residual=pairs[RESIDUAL]['ci95_wait'][1]<0,
            wait_CI_upper_below_zero_vs_EAT=pairs[EAT]['ci95_wait'][1]<0)
    return dict(passed=all(checks.values()),checks=checks,operational_adoption=False)


def evaluate(cases,stage):
    mix=[c['compact'] for c in cases if c['compact']['kind']=='mix'];stress=[c['compact'] for c in cases if c['compact']['kind']!='mix']
    names=[arm(*a) for a in ARMS];metrics={n:parent.old_metrics.policy_metrics(mix,n) for n in names}
    for n,m in metrics.items():
        values=[wait(p) for x in mix for p in x['branches'][n]['patients']];tail=sorted(values)[int(.9*len(values)):]
        m.update(wait_sum_seconds=sum(values),wait_p90=parent.old_metrics._quantile(values,.9),wait_cvar90=sum(tail)/len(tail))
    pairs={n:parent.paired(mix,n,after=PRIMARY,bootstrap=n in (RESIDUAL,EAT)) for n in names if n!=PRIMARY}
    recovery_check=[]
    if stage=='repair':
        lookup={(x['seed'],p['id']):p for x in mix for p in x['branches'][PRIMARY]['patients']}
        recovery_check=[dict(**x,preserved=not lookup[x['seed'],x['patient']]['failed'],
            selection_seconds=lookup[x['seed'],x['patient']]['selection_seconds']) for x in recoveries()]
    all_case_preservation=parent.paired([c['compact'] for c in cases],RESIDUAL,after=PRIMARY,bootstrap=False)
    return dict(mix_metrics=metrics,paired=pairs,recovery_check=recovery_check,
        all_case_residual_preservation=all_case_preservation,
        assessment=assess(metrics,pairs,stage,recovery_check,all_case_preservation),
        sensitivity={str(p):{n:parent.old_metrics.policy_metrics(mix,n,p)['J'] for n in names} for p in (600,1500,3000)},
        stress_metrics={f:{n:parent.old_metrics.policy_metrics([x for x in stress if x['family']==f],n) for n in names}
            for f in harness.scenarios.original.FAMILY_NAMES})


def stage_cases(stage):
    from .guard_audit import audit_replay
    manifest={(x['stage'],x['kind'],x['seed']):x['sha256'] for x in read(BUILD/'input-manifest.json')['entries']}
    folder=BUILD/(stage+'-cases');index=read(BUILD/(stage+'-raw-index.json'));todo=tasks(stage)
    assert index['all_outcomes_saved'] and len(index['cases'])==len(todo)
    assert {p.name for p in folder.iterdir()}=={x['file'] for x in index['cases']}
    old=read(PARENT/'development.json')['cases'] if stage=='repair' else None;cases=[]
    for i,((kind,seed,arms),row) in enumerate(zip(todo,index['cases'])):
        assert row['file']==f'{i:03d}_{seed}.json.gz'
        zipped=(folder/row['file']).read_bytes();assert sha(zipped)==row['compressed_sha256']
        content=gzip.decompress(zipped);assert sha(content)==row['raw_sha256'];replay=json.loads(content)
        assert raw(replay)==content and replay['arms']==[list(a) for a in arms]
        assert sha(raw(replay['scenario']))==manifest[stage,kind,seed]
        proof=audit_replay(replay);assert proof['passed'] and raw(replay)==content
        compact=parent.compact(replay);cache={}
        if old is not None:
            ref=old[i];z=(PARENT/'development-cases'/ref['file']).read_bytes()
            assert sha(z)==ref['compressed_sha256'];b=gzip.decompress(z);assert sha(b)==ref['raw_sha256']
            cached=json.loads(b);assert parent.compact(cached)==ref['compact']
            assert cached['scenario']==replay['scenario'] and cached['initial_snapshot_sha256']==replay['initial_snapshot_sha256']
            cached_proof=audit_replay(cached);assert cached_proof['passed'] and raw(cached)==b
            data=deepcopy(ref['compact']);data['branches'].update(compact['branches']);compact=data
            cache=dict(file=ref['file'],compressed_sha256=ref['compressed_sha256'],raw_sha256=ref['raw_sha256'],audit=cached_proof)
        assert (compact['kind'],compact['seed'])==(kind,seed)
        cases.append(dict(**row,audit=proof,compact=compact,cached_parent_reference=cache))
    return cases


def prior_evidence(stage):
    if stage=='repair':return {}
    previous='repair' if stage=='development' else 'development'
    return {previous+suffix:sha((BUILD/(previous+suffix)).read_bytes()) for suffix in ('.json','-audit.json','-lock.json')}


def verify_prior(stage):
    if stage=='repair':return {}
    previous='repair' if stage=='development' else 'development'
    p=BUILD/(previous+'.json');r=read(p);a=read(BUILD/(previous+'-audit.json'));lock=read(BUILD/(previous+'-lock.json'))
    assert r['assessment']['passed'] and a['passed'] and lock['next_stage_allowed'] is True
    assert sha(p.read_bytes())==a['report_sha256']==lock['report_sha256']
    assert lock['candidate']==PRIMARY and lock['assessment']==r['assessment']
    assert lock['protocol']==r['protocol']==protocol() and a['source_sha256']==source_hashes()
    for name,digest in r['prior_stage_evidence_sha256'].items():assert sha((BUILD/name).read_bytes())==digest
    assert stage_cases(previous)==r['cases']
    return prior_evidence(stage)


def execute(stage,workers=4):
    frozen=verify_freeze();prior=verify_prior(stage);folder=BUILD/(stage+'-cases');path=BUILD/(stage+'.json')
    assert all(not p.exists() for p in (folder,path,BUILD/(stage+'-raw-index.json'),BUILD/(stage+'-audit.json'),BUILD/(stage+'-lock.json')))
    todo=tasks(stage);folder.mkdir();rows=[];started=time.perf_counter()
    manifest={(x['stage'],x['kind'],x['seed']):x['sha256'] for x in read(BUILD/'input-manifest.json')['entries']}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i,((kind,seed,_),replay) in enumerate(zip(todo,pool.map(guard_runner.run_task,todo,chunksize=1))):
            assert sha(raw(replay['scenario']))==manifest[stage,kind,seed]
            content=raw(replay);zipped=gzip.compress(content,compresslevel=6,mtime=0);name=f'{i:03d}_{seed}.json.gz'
            write_new(folder/name,zipped);rows.append(dict(file=name,raw_sha256=sha(content),compressed_sha256=sha(zipped)))
            if len(rows)%8==0:print(f'{stage} {len(rows)}/{len(todo)} environments',flush=True)
    write_new(BUILD/(stage+'-raw-index.json'),raw(dict(cases=rows,all_outcomes_saved=True)))
    cases=stage_cases(stage);assert protocol()==frozen['protocol']
    report=dict(schema_version=1,experiment=EXPERIMENT,stage=stage,protocol=frozen['protocol'],
        elapsed_seconds=time.perf_counter()-started,cases=cases,artifact_directory=str(folder),
        new_branches=len(todo)*(1 if stage=='repair' else len(ARMS)),
        cached_branches=len(todo)*5 if stage=='repair' else 0,operational_adoption=False,
        prior_stage_evidence_sha256=prior,**evaluate(cases,stage))
    assert prior_evidence(stage)==prior
    write_new(path,raw(report))
    if stage in ('repair','development'):
        write_new(BUILD/(stage+'-lock.json'),raw(dict(protocol=frozen['protocol'],candidate=PRIMARY,
            report_sha256=sha(raw(report)),assessment=report['assessment'],next_stage_allowed=report['assessment']['passed'])))
    print(dict(stage=stage,assessment=report['assessment']),flush=True)


def audit_saved(stage):
    verify_freeze();path=BUILD/(stage+'.json');r=read(path)
    assert r['protocol']==protocol() and r['stage']==stage and r['experiment']==EXPERIMENT
    assert r['prior_stage_evidence_sha256']==prior_evidence(stage)
    cases=stage_cases(stage);assert cases==r['cases']
    assert r['new_branches']==len(cases)*(1 if stage=='repair' else 6)
    assert r['cached_branches']==(len(cases)*5 if stage=='repair' else 0)
    for key,value in evaluate(cases,stage).items():assert r[key]==value
    assert r['artifact_directory']==str(BUILD/(stage+'-cases'))
    write_new(BUILD/(stage+'-audit.json'),raw(dict(passed=True,report_sha256=sha(path.read_bytes()),
        source_sha256=source_hashes(),cases=len(cases),new_branches_audited=r['new_branches'],
        cached_branches_reaudited=r['cached_branches'],new_performance_runs=0)))
    print(dict(audit=stage,passed=True,cases=len(cases)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('freeze','repair','development','validation',
        'audit-repair','audit-development','audit-validation'));p.add_argument('--workers',type=int,default=4);a=p.parse_args()
    if a.stage=='freeze':freeze()
    elif a.stage.startswith('audit-'):audit_saved(a.stage[6:])
    else:execute(a.stage,a.workers)
