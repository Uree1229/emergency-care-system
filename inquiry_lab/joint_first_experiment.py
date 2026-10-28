"""Atomic first-query coordination for pairs of currently registered patients.

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
from . import residual_experiment as parent, harness, joint_first_policy, joint_first_runner

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'.codex-build/eat-joint-first-20261021'
PARENT=ROOT/'.codex-build/eat-horizon-20261021'
RESIDUAL_PARENT=ROOT/'.codex-build/eat-residual-20261020'
EXPERIMENT='eat_joint_first_recovery_20261021'
NAMESPACE='eat-joint-first-v1-20261021-6b39d3d8-82a5-4dd3-93c6-91f64ba761dc'
PRIMARY='eat_joint_first_l15_f|immediate'
HORIZON='eat_horizon_l15_f|immediate'
RESIDUAL='eat_residual_l15_f|immediate'
EAT='eat_l15_f|immediate'
FIXED='fixed_3|immediate'
ARMS=(('fixed_3','immediate'),('eat_l15_f','immediate'),('eat_residual_l15_f','immediate'),
    ('eat_horizon_l15_f','immediate'),(joint_first_policy.NAME,'immediate'),('v3_w45_h900','immediate'),('eat_l15_f','hold120'))
REPAIR_ARMS=((joint_first_policy.NAME,'immediate'),)
NEW_SOURCES=tuple('inquiry_lab/joint_first_'+n+'.py' for n in ('policy','runner','audit','context','experiment'))+tuple(
    'test_inquiry_lab_joint_first_'+n+'.py' for n in ('policy','runner','audit','context','experiment'))+(
    '.codex-build/eat-joint-first-20261021/run_checks.py',
    '.codex-build/eat-joint-first-20261021/preflight_equivalence.py',
    '.codex-build/eat-joint-first-20261021/finalize_phase.py',
    '.codex-build/eat-joint-first-20261021/build_scaffold.py',)
FIXTURE='simulation/fixtures/eat_intent_20261020/mix7.json.gz'
WORKING_REFERENCE='docs/EAT_INQUIRY_EXPERIMENT.md'
REFERENCE_EXCLUSION_SHA='b81f22c45cd3fa37065530f2af9ec2f8122ba35c95b45864c1b46a707db08197'
raw,sha,read,write_new=parent.raw,parent.sha,parent.read,parent.write_new
arm,wait=parent.arm,parent.wait
SETTINGS=dict(candidate_count=1,
    changed_factor_relative_to_horizon='joint first inquiry membership for two currently registered patients',
    base_policy='eat_horizon_l15_f; all subsequent reviews use unchanged horizon kernel',
    eligibility='enabled due WAITING jobs with no inquiries and no decisions; all currently registered eligible patients included in public roster',
    grouping='deterministic started_at,patient_id order, pairs of at most two; refreshed committed snapshot per pair; singleton uses horizon',
    worker_precedence='registered first-contact pairs run before frozen fallback reviews; relative decision and send order may differ',
    first_proposals='horizon proposals independently on the same committed pair prefix; not original sequential trajectory proposals',
    contacts='preserve each common-prefix horizon immediate proposal count ki; nearest6 and concurrency4 unchanged',
    objective='minimize sum modeled capped first-acceptance waiting among same-ki immediate subset pairs',
    modeled_constraints='each patient selection probability no lower and original arrival/failure/contact expected cost no higher than common-prefix baseline pair',
    shared_queue='later member sharing a hospital adds earlier member one believed mean job / unchanged assumed workers; no actual worker data',
    current_only=True,future_proposal_offsets_not_treated_as_reservations=True,
    adoption='strict sum wait improvement >1e-9, lexicographic candidate subset enumeration tie; otherwise keep common-prefix baseline',
    numerical_tolerance=dict(wait_cost_seconds=1e-9,selection_probability=1e-12),
    atomic_apply='all pair identities, versions, revision, prefixes, due status, first-contact status and current eligibility checked before writes; one transaction for both',
    original_workflow_order_and_seven_inputs_not_claimed_unchanged=True,
    no_ablation_to_isolate_membership_from_common_prefix_and_atomic_workflow_change=True,
    future_patients_potentials_workers_or_new_resource_reports_not_policy_inputs=True,
    public_time_context='inherited separate soft auto-stop A and hard lab measurement H',
    primary='all-patient hospital-selection waiting; failures capped at 300 seconds',
    primary_excludes_travel_and_post_arrival_treatment_wait=True,
    repair='all 120 known residual environments, using cached R five branches and cached horizon branch',
    repair_new_branches_per_environment=1,repair_cached_reference_branches_per_environment=6,
    fresh_development_mix=96,fresh_validation_mix=320,stress_seeds=[3,6],fresh_arms=7,
    gates=['W lower than residual, horizon and EAT','no residual or horizon success lost',
        'failures and J no more than residual, horizon and EAT','inquiries and review work no more than fixed3'],
    success_preservation_scope='every mixed and stress patient for both residual and horizon at every stage',
    validation_additional_gate='10000 scenario-cluster resamples: upper95 deltaW < 0 versus residual, horizon and EAT',
    individual_delays_and_lost_selections_reported=True,universal_zero_harm_not_claimed=True,
    known_training_not_independent_validation=True,heldout_not_used_for_retuning=True,
    stress_excluded_from_primary=True,fresh_reference_branches_not_cached=True,
    operational_adoption=False,live_server_UI_database_changed=False,
    parent_preservation_scope='latest horizon seal and named source/reference dependencies; inherit one changing non-executable explanation exclusion')


def seeds(role,n):
    return tuple(int.from_bytes(hashlib.sha256(f'{NAMESPACE}|{role}|{i}'.encode()).digest()[:16],'big')|(1<<127)
        for i in range(n))


def tasks(stage):
    if stage=='repair':
        return [(c['compact']['kind'],c['compact']['seed'],REPAIR_ARMS) for c in read(PARENT/'repair.json')['cases']]
    if stage not in ('development','validation'):raise ValueError('known stage required')
    count,n=(96,3) if stage=='development' else (320,6)
    return [('mix',s,ARMS) for s in seeds(stage+'-mix',count)]+[
        ('family:'+f,s,ARMS) for s in seeds(stage+'-stress',n) for f in harness.scenarios.original.FAMILY_NAMES]


def source_hashes():
    old=read(BUILD/'preservation/before.json')['old_source_sha256']
    return {n:sha((ROOT/n).read_bytes()) for n in sorted(set(old)|set(NEW_SOURCES)|{FIXTURE})}


def verify_old():
    before=read(BUILD/'preservation/before.json');assert before['passed']
    exclusion=read(BUILD/'preservation/reference-drift.json')
    assert sha((BUILD/'preservation/reference-drift.json').read_bytes())==REFERENCE_EXCLUSION_SHA
    excluded=exclusion['excluded_working_reference_files']
    assert set(excluded)=={WORKING_REFERENCE}
    assert excluded[WORKING_REFERENCE]['parent_expected_sha256']==before['reference_files_sha256'][WORKING_REFERENCE]
    assert WORKING_REFERENCE not in before['old_source_sha256']
    for name,digest in before['old_source_sha256'].items():assert sha((ROOT/name).read_bytes())==digest,name
    for name,digest in before['reference_files_sha256'].items():
        if name!=WORKING_REFERENCE:assert sha((ROOT/name).read_bytes())==digest,name
    return sha((BUILD/'preservation/before.json').read_bytes())


def recoveries():
    answer=[]
    for c in read(PARENT/'repair.json')['cases']:
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
        recovered_patient_regression_identifiers=recoveries(),python=platform.python_version(),
        excluded_non_execution_working_reference=WORKING_REFERENCE,
        reference_exclusion_sha256=REFERENCE_EXCLUSION_SHA)


def freeze():
    p=protocol();tests=read(BUILD/'tests.json')
    assert tests['passed'] and tests['source_sha256_before']==tests['source_sha256_after']==p['source_sha256']
    required={'test_inquiry_lab_joint_first_'+name for name in ('policy','context','runner','audit','experiment')}
    assert required<=set(tests['module_counts']) and all(tests['module_counts'][name]>0 for name in required)
    assert tests['errors']==tests['failures']==tests['skipped']==0
    roles=[*seeds('development-mix',96),*seeds('validation-mix',320),
        *seeds('development-stress',3),*seeds('validation-stress',6)]
    assert len(set(roles))==425
    for path in (PARENT/'input-manifest.json',RESIDUAL_PARENT/'input-manifest.json',ROOT/'.codex-build/eat-shortlist-20261020/input-manifest.json',
            ROOT/'.codex-build/eat-residual-followup-20261020/input-manifest.json',
            ROOT/'.codex-build/eat-residual-guard-20261020/input-manifest.json',
            ROOT/'.codex-build/eat-residual-preserve-20261020/input-manifest.json',
            ROOT/'.codex-build/eat-reservation-20261020/input-manifest.json'):
        assert not set(roles)&{x['seed'] for x in read(path)['entries']}
    for path in ('inquiry_lab_validation_20261020.json','inquiry_lab_confirm_validation_20261020.json'):
        assert not set(roles)&{x['seed'] for x in read(ROOT/'simulation_results'/path)['results']}
    write_new(BUILD/'source-freeze.json',raw(dict(passed=True,protocol=p,formal_performance_runs_before_freeze=0,
        known_previous_repair_data_inspected=True,fresh_performance_inputs_generated_before_freeze=False,
        preflight_seed7_fixture=FIXTURE,additional_adapter_family_seed7='simultaneous_w1',
        preflight_scope='fixed seed7 adapter/audit integration only; no policy or gate tuning',
        recorded_at=time.strftime('%Y-%m-%d %H:%M:%S'))))
    entries=[]
    old_manifest={(x['kind'],x['seed']):x['sha256'] for x in read(PARENT/'input-manifest.json')['entries'] if x['stage']=='repair'}
    for stage in ('repair','development','validation'):
        for kind,seed,_ in tasks(stage):
            digest=old_manifest[kind,seed] if stage=='repair' else sha(raw(harness.build(kind,seed)))
            entries.append(dict(stage=stage,kind=kind,seed=seed,sha256=digest))
    write_new(BUILD/'input-manifest.json',raw(dict(entries=entries,fresh_inputs_generated_after_freeze=True)))
    names=['preservation/before.json','preservation/reference-drift.json','tests.json','source-freeze.json','input-manifest.json',
        'preflight/parent-equivalence.json']
    names += [p.relative_to(BUILD).as_posix() for p in sorted((BUILD/'diagnosis').rglob('*')) if p.is_file()]
    evidence={n:sha((BUILD/n).read_bytes()) for n in names}
    write_new(BUILD/'pre-development.json',raw(dict(passed=True,protocol=p,evidence_sha256=evidence)))
    print(dict(frozen=True,sources=len(p['source_sha256']),inputs=len(entries)),flush=True)


def verify_freeze():
    p=read(BUILD/'pre-development.json');assert p['passed'] and p['protocol']==protocol()
    for name,digest in p['evidence_sha256'].items():assert sha((BUILD/name).read_bytes())==digest
    return p


def assess(metrics,pairs,stage,recovery_check=None,all_case_preservation=None,all_horizon_preservation=None):
    if stage not in ('repair','development','validation'):raise ValueError('known stage required')
    c=metrics[PRIMARY];r=metrics[RESIDUAL];h=metrics[HORIZON];e=metrics[EAT];f=metrics[FIXED]
    checks=dict(wait_lower_than_residual=pairs[RESIDUAL]['wait_sum_delta_seconds']<0,
        wait_lower_than_EAT=pairs[EAT]['wait_sum_delta_seconds']<0,
        wait_lower_than_horizon=pairs[HORIZON]['wait_sum_delta_seconds']<0,
        no_residual_success_lost=pairs[RESIDUAL]['lost_selections']==0 and all_case_preservation is not None
            and all_case_preservation['lost_selections']==0,
        no_horizon_success_lost=pairs[HORIZON]['lost_selections']==0 and all_horizon_preservation is not None
            and all_horizon_preservation['lost_selections']==0,
        failures_not_increased=c['failed']<=min(r['failed'],h['failed'],e['failed']),
        J_not_increased=c['J']<=min(r['J'],h['J'],e['J']),
        inquiry_not_more_than_fixed3=c['inquiries_per_patient']<=f['inquiries_per_patient'],
        work_not_more_than_fixed3=c['review_work_seconds_per_patient']<=f['review_work_seconds_per_patient'])
    if stage=='repair':checks['all_four_exact_recovered_patients_preserved']=recovery_check is not None and all(x['preserved'] for x in recovery_check) and len(recovery_check)==4
    if stage=='validation':
        checks.update(wait_CI_upper_below_zero_vs_residual=pairs[RESIDUAL]['ci95_wait'][1]<0,
            wait_CI_upper_below_zero_vs_horizon=pairs[HORIZON]['ci95_wait'][1]<0,
            wait_CI_upper_below_zero_vs_EAT=pairs[EAT]['ci95_wait'][1]<0)
    return dict(passed=all(checks.values()),checks=checks,operational_adoption=False)


def evaluate(cases,stage):
    mix=[c['compact'] for c in cases if c['compact']['kind']=='mix'];stress=[c['compact'] for c in cases if c['compact']['kind']!='mix']
    names=[arm(*a) for a in ARMS];metrics={n:parent.old_metrics.policy_metrics(mix,n) for n in names}
    for n,m in metrics.items():
        values=[wait(p) for x in mix for p in x['branches'][n]['patients']];tail=sorted(values)[int(.9*len(values)):]
        m.update(wait_sum_seconds=sum(values),wait_p90=parent.old_metrics._quantile(values,.9),wait_cvar90=sum(tail)/len(tail))
    pairs={n:parent.paired(mix,n,after=PRIMARY,bootstrap=n in (RESIDUAL,HORIZON,EAT)) for n in names if n!=PRIMARY}
    recovery_check=[]
    if stage=='repair':
        lookup={(x['seed'],p['id']):p for x in mix for p in x['branches'][PRIMARY]['patients']}
        recovery_check=[dict(**x,preserved=not lookup[x['seed'],x['patient']]['failed'],
            selection_seconds=lookup[x['seed'],x['patient']]['selection_seconds']) for x in recoveries()]
    all_case_preservation=parent.paired([c['compact'] for c in cases],RESIDUAL,after=PRIMARY,bootstrap=False)
    all_horizon_preservation=parent.paired([c['compact'] for c in cases],HORIZON,after=PRIMARY,bootstrap=False)
    return dict(mix_metrics=metrics,paired=pairs,recovery_check=recovery_check,
        all_case_residual_preservation=all_case_preservation,
        all_case_horizon_preservation=all_horizon_preservation,
        assessment=assess(metrics,pairs,stage,recovery_check,all_case_preservation,all_horizon_preservation),
        sensitivity={str(p):{n:parent.old_metrics.policy_metrics(mix,n,p)['J'] for n in names} for p in (600,1500,3000)},
        stress_metrics={f:{n:parent.old_metrics.policy_metrics([x for x in stress if x['family']==f],n) for n in names}
            for f in harness.scenarios.original.FAMILY_NAMES})


def stage_cases(stage):
    from .joint_first_audit import audit_replay
    manifest={(x['stage'],x['kind'],x['seed']):x['sha256'] for x in read(BUILD/'input-manifest.json')['entries']}
    folder=BUILD/(stage+'-cases');index=read(BUILD/(stage+'-raw-index.json'));todo=tasks(stage)
    assert index['all_outcomes_saved'] and len(index['cases'])==len(todo)
    assert {p.name for p in folder.iterdir()}=={x['file'] for x in index['cases']}
    old=read(PARENT/'repair.json')['cases'] if stage=='repair' else None;cases=[]
    for i,((kind,seed,arms),row) in enumerate(zip(todo,index['cases'])):
        assert row['file']==f'{i:03d}_{seed}.json.gz'
        zipped=(folder/row['file']).read_bytes();assert sha(zipped)==row['compressed_sha256']
        content=gzip.decompress(zipped);assert sha(content)==row['raw_sha256'];replay=json.loads(content)
        assert raw(replay)==content and replay['arms']==[list(a) for a in arms]
        assert sha(raw(replay['scenario']))==manifest[stage,kind,seed]
        proof=audit_replay(replay);assert proof['passed'] and raw(replay)==content
        compact=parent.compact(replay);cache={}
        if old is not None:
            ref=old[i];z=(PARENT/'repair-cases'/ref['file']).read_bytes()
            assert sha(z)==ref['compressed_sha256'];b=gzip.decompress(z);assert sha(b)==ref['raw_sha256']
            cached=json.loads(b)
            cached_compact=parent.compact(cached)
            assert set(cached_compact['branches'])=={HORIZON}
            assert cached_compact['branches'][HORIZON]==ref['compact']['branches'][HORIZON]
            assert cached['scenario']==replay['scenario'] and cached['initial_snapshot_sha256']==replay['initial_snapshot_sha256']
            cached_proof=audit_replay(cached);assert cached_proof['passed'] and raw(cached)==b
            residual_ref=ref['cached_parent_reference']
            rz=(RESIDUAL_PARENT/'development-cases'/residual_ref['file']).read_bytes()
            assert sha(rz)==residual_ref['compressed_sha256']
            rb=gzip.decompress(rz);assert sha(rb)==residual_ref['raw_sha256']
            residual_cached=json.loads(rb);residual_compact=parent.compact(residual_cached)
            assert residual_cached['scenario']==replay['scenario']
            assert residual_cached['initial_snapshot_sha256']==replay['initial_snapshot_sha256']
            assert len(residual_compact['branches'])==5
            assert all(v==ref['compact']['branches'][k] for k,v in residual_compact['branches'].items())
            residual_proof=audit_replay(residual_cached)
            assert residual_proof['passed'] and raw(residual_cached)==rb
            data=deepcopy(ref['compact']);data['branches'].update(compact['branches']);compact=data
            cache=dict(horizon=dict(file=ref['file'],compressed_sha256=ref['compressed_sha256'],
                raw_sha256=ref['raw_sha256'],audit=cached_proof),
                residual=dict(file=residual_ref['file'],compressed_sha256=residual_ref['compressed_sha256'],
                raw_sha256=residual_ref['raw_sha256'],audit=residual_proof))
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
        for i,((kind,seed,_),replay) in enumerate(zip(todo,pool.map(joint_first_runner.run_task,todo,chunksize=1))):
            assert sha(raw(replay['scenario']))==manifest[stage,kind,seed]
            content=raw(replay);zipped=gzip.compress(content,compresslevel=6,mtime=0);name=f'{i:03d}_{seed}.json.gz'
            write_new(folder/name,zipped);rows.append(dict(file=name,raw_sha256=sha(content),compressed_sha256=sha(zipped)))
            if len(rows)%8==0:print(f'{stage} {len(rows)}/{len(todo)} environments',flush=True)
    write_new(BUILD/(stage+'-raw-index.json'),raw(dict(cases=rows,all_outcomes_saved=True)))
    cases=stage_cases(stage);assert protocol()==frozen['protocol']
    report=dict(schema_version=1,experiment=EXPERIMENT,stage=stage,protocol=frozen['protocol'],
        elapsed_seconds=time.perf_counter()-started,cases=cases,artifact_directory=str(folder),
        new_branches=len(todo)*(1 if stage=='repair' else len(ARMS)),
        cached_branches=len(todo)*6 if stage=='repair' else 0,operational_adoption=False,
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
    assert r['new_branches']==len(cases)*(1 if stage=='repair' else len(ARMS))
    assert r['cached_branches']==(len(cases)*6 if stage=='repair' else 0)
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
