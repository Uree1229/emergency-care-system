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
from . import residual_experiment as parent, harness, joint_budget_policy, joint_budget_runner

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'.codex-build/eat-joint-budget-20261021'
PARENT=ROOT/'.codex-build/eat-joint-reselect-20261021'
TAIL_PARENT=ROOT/'.codex-build/eat-joint-tail-20261021'
JOINT_PARENT=ROOT/'.codex-build/eat-joint-first-20261021'
HORIZON_PARENT=ROOT/'.codex-build/eat-horizon-20261021'
RESIDUAL_PARENT=ROOT/'.codex-build/eat-residual-20261020'
EXPERIMENT='eat_joint_budget_recovery_20261021'
NAMESPACE='eat-joint-budget-v1-20261021-4959c447-825c-4909-9a9f-b48e4db70e65'
PRIMARY='eat_joint_budget_l15_f|immediate'
HORIZON='eat_horizon_l15_f|immediate'
JOINT='eat_joint_first_l15_f|immediate'
TAIL='eat_joint_tail_l15_f|immediate'
RESELECT='eat_joint_reselect_l15_f|immediate'
RESIDUAL='eat_residual_l15_f|immediate'
EAT='eat_l15_f|immediate'
FIXED='fixed_3|immediate'
ARMS=(('fixed_3','immediate'),('eat_l15_f','immediate'),('eat_residual_l15_f','immediate'),
    ('eat_horizon_l15_f','immediate'),('eat_joint_first_l15_f','immediate'),('eat_joint_tail_l15_f','immediate'),('eat_joint_reselect_l15_f','immediate'),(joint_budget_policy.NAME,'immediate'),('v3_w45_h900','immediate'),('eat_l15_f','hold120'))
REPAIR_ARMS=((joint_budget_policy.NAME,'immediate'),)
NEW_SOURCES=tuple('inquiry_lab/joint_budget_'+n+'.py' for n in ('policy','runner','audit','context','experiment'))+tuple(
    'test_inquiry_lab_joint_budget_'+n+'.py' for n in ('policy','runner','audit','context','experiment'))+(
    '.codex-build/eat-joint-budget-20261021/run_checks.py',
    '.codex-build/eat-joint-budget-20261021/preflight_equivalence.py',
    '.codex-build/eat-joint-budget-20261021/finalize_phase.py',
    '.codex-build/eat-joint-budget-20261021/build_scaffold.py',)
FIXTURE='simulation/fixtures/eat_intent_20261020/mix7.json.gz'
WORKING_REFERENCE='docs/EAT_INQUIRY_EXPERIMENT.md'
REFERENCE_EXCLUSION_SHA='b81f22c45cd3fa37065530f2af9ec2f8122ba35c95b45864c1b46a707db08197'
raw,sha,read,write_new=parent.raw,parent.sha,parent.read,parent.write_new
arm,wait=parent.arm,parent.wait
SETTINGS=dict(candidate_count=1,
    changed_factor_relative_to_tail='retain a TAIL veto only if same-clock sequential JOINT projection does not reduce any later paired current patient immediate count',
    base_policy='frozen TAIL proposal and nested original JOINT table, H fallback and complete atomic adapter',
    eligibility='unchanged complete currently registered enabled due WAITING zero-inquiry zero-decision roster',
    tail_candidates='unchanged current normalized candidate order min(6,max_new)',
    guard='original TAIL zero-public-pending veto is immutable proposal evidence; actual veto is declined when any later paired projected first-contact count shrinks under baseline',
    selection='only original TAIL baseline or intact original JOINT winner; never search a second-best row',
    tie='count comparison is exact integer: any later paired individual baseline count lower than winner count declines the veto',
    original_tail_and_joint_proposals_preserved_intact=True,
    actual_outputs='separate copied original TAIL actual or intact nested JOINT winner; original proposals never rewritten',
    public_zero_pending_not_physical_idle_worker_or_free_bed=True,
    counts_and_order='current pair common-prefix H counts unchanged; same pair-before-fallback order, concurrency4, nearest6; downstream recalculation may change on altered ledgers',
    fallback='unchanged complete H kernel for unpaired and subsequent decisions',
    current_only=True,future_proposal_offsets_not_treated_as_reservations=True,
    atomic_apply='unchanged complete pair+tail identities/versions/eligibility/prefix/candidate checks before writes, then one transaction',
    future_patients_potentials_workers_or_new_resource_reports_not_policy_inputs=True,
    original_workflow_order_and_seven_inputs_not_claimed_unchanged=True,
    no_ablation_to_decompose_downstream_path_changes=True,
    numerical_tolerance=dict(wait_cost_seconds=1e-9,selection_probability=1e-12),
    public_time_context='unchanged soft automatic inquiry stop A and hard lab measurement end H',
    primary='mixed all-patient hospital-selection waiting; failures capped at300 seconds',
    primary_excludes_travel_and_post_arrival_treatment_wait=True,
    additional_scopes='whole=mixed+crafted stress; stress aggregate and family outcomes reported separately',
    scope_weights='actual fixed stage bundle patients, not a clinical patient-distribution prevalence claim',
    repair='all120 known environments, cached rejected-reselect1+tail1+joint1+horizon1+residual5',
    repair_new_branches_per_environment=1,repair_cached_reference_branches_per_environment=9,
    fresh_development_mix=96,fresh_validation_mix=320,stress_seeds=[3,6],fresh_arms=10,
    gates=['mixed W strict lower than EAT/R/H and no greater than joint/tail',
        'whole W strict lower than joint/tail; stress W no greater than joint/tail',
        'all R/H/joint/tail successes retained including stress; no exchange of successful identities',
        'exact original4 and former090/P05 and current064/P06 successful in repair',
        'mixed failures/J no greater than EAT/R/H/joint/tail; contacts/work no greater than fixed3'],
    validation_additional_gate='10000 scenario-cluster bootstrap: mixed upper95 deltaW EAT/R/H<0, joint/tail<=0; whole joint/tail<0',
    stress_ci_not_used=True,stress_point_nonincrease_and_all_family_outcomes_required=True,
    known_259_gain='repair mixed W<=tail51649; no absolute259 claim on fresh data',
    known_whole_regression='repair whole W<joint57304 and<tail57695; stress W<=joint5551 and<=tail6046',
    individual_delays_and_lost_selections_reported=True,universal_zero_harm_not_claimed=True,
    known_training_not_independent_validation=True,heldout_not_used_for_retuning=True,
    fresh_reference_branches_not_cached=True,operational_adoption=False,live_server_UI_database_changed=False,
    parent_preservation_scope='latest rejected reselect seal and named dependencies including stored-only accounting addendum; one inherited non-executable explanation exception',
    virtual_projection='same now, current public inputs, copied suffix context; append immediate inquiries only; frozen JOINT without recursive TAIL or budget',
    singleton='excluded only from later paired contact-count comparison; kept in complete TAIL capture and actual prewrite checks',
    independent_H_projection_search=True,projection_count_guard_not_observed_success_or_wait_guarantee=True,
    prior_candidates_this_user_turn=['reselect tested and rejected','count-only arithmetic diagnostic','cohort-wait arithmetic diagnostic rejected without performance'],
    prerepair_diagnostics='7 named old TAIL public raw prefixes only; B20 JOINT arithmetic calls, C20 JOINT plus4 singleton H; no outcome or clock rollout',
    rejected_reselect_not_an_additional_success_preservation_gate=True)


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
        recovered_patient_regression_identifiers=recoveries(),
        former_joint_loss_regression_identifier=dict(seed=235769399510989085999300528562655985418,patient='P05'),
        former_tail_loss_regression_identifier=dict(seed=275623723515674800776867121243373893786,patient='P06'),python=platform.python_version(),
        excluded_non_execution_working_reference=WORKING_REFERENCE,
        reference_exclusion_sha256=REFERENCE_EXCLUSION_SHA)


def freeze():
    p=protocol();tests=read(BUILD/'tests.json')
    assert tests['passed'] and tests['source_sha256_before']==tests['source_sha256_after']==p['source_sha256']
    required={'test_inquiry_lab_joint_budget_'+name for name in ('policy','context','runner','audit','experiment')}
    assert required<=set(tests['module_counts']) and all(tests['module_counts'][name]>0 for name in required)
    assert tests['errors']==tests['failures']==tests['skipped']==0
    roles=[*seeds('development-mix',96),*seeds('validation-mix',320),
        *seeds('development-stress',3),*seeds('validation-stress',6)]
    assert len(set(roles))==425
    for path in (PARENT/'input-manifest.json',TAIL_PARENT/'input-manifest.json',JOINT_PARENT/'input-manifest.json',HORIZON_PARENT/'input-manifest.json',RESIDUAL_PARENT/'input-manifest.json',ROOT/'.codex-build/eat-shortlist-20261020/input-manifest.json',
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


def assess(metrics,pairs,stage,recovery_check=None,all_case_preservation=None,all_horizon_preservation=None,
           all_joint_preservation=None,former_loss_check=None,all_tail_preservation=None,new_loss_check=None,
           whole_pairs=None,stress_pairs=None):
    if stage not in ('repair','development','validation'):raise ValueError('known stage required')
    c=metrics[PRIMARY];r=metrics[RESIDUAL];h=metrics[HORIZON];j=metrics[JOINT];t=metrics[TAIL];e=metrics[EAT];f=metrics[FIXED]
    checks=dict(wait_lower_than_residual=pairs[RESIDUAL]['wait_sum_delta_seconds']<0,
        wait_lower_than_EAT=pairs[EAT]['wait_sum_delta_seconds']<0,
        wait_lower_than_horizon=pairs[HORIZON]['wait_sum_delta_seconds']<0,
        wait_not_increased_vs_joint=pairs[JOINT]['wait_sum_delta_seconds']<=0,
        wait_not_increased_vs_tail=pairs[TAIL]['wait_sum_delta_seconds']<=0,
        whole_wait_lower_than_joint=whole_pairs is not None and whole_pairs[JOINT]['wait_sum_delta_seconds']<0,
        whole_wait_lower_than_tail=whole_pairs is not None and whole_pairs[TAIL]['wait_sum_delta_seconds']<0,
        stress_wait_not_increased_vs_joint=stress_pairs is not None and stress_pairs[JOINT]['wait_sum_delta_seconds']<=0,
        stress_wait_not_increased_vs_tail=stress_pairs is not None and stress_pairs[TAIL]['wait_sum_delta_seconds']<=0,
        no_residual_success_lost=pairs[RESIDUAL]['lost_selections']==0 and all_case_preservation is not None and all_case_preservation['lost_selections']==0,
        no_horizon_success_lost=pairs[HORIZON]['lost_selections']==0 and all_horizon_preservation is not None and all_horizon_preservation['lost_selections']==0,
        no_joint_success_lost=pairs[JOINT]['lost_selections']==0 and all_joint_preservation is not None and all_joint_preservation['lost_selections']==0,
        no_tail_success_lost=pairs[TAIL]['lost_selections']==0 and all_tail_preservation is not None and all_tail_preservation['lost_selections']==0,
        failures_not_increased=c['failed']<=min(r['failed'],h['failed'],j['failed'],t['failed'],e['failed']),
        J_not_increased=c['J']<=min(r['J'],h['J'],j['J'],t['J'],e['J']),
        inquiry_not_more_than_fixed3=c['inquiries_per_patient']<=f['inquiries_per_patient'],
        work_not_more_than_fixed3=c['review_work_seconds_per_patient']<=f['review_work_seconds_per_patient'])
    if stage=='repair':
        checks['all_four_exact_recovered_patients_preserved']=recovery_check is not None and len(recovery_check)==4 and all(x['preserved'] for x in recovery_check)
        checks['former_joint_loss_recovered']=former_loss_check is not None and former_loss_check['preserved']
        checks['former_tail_loss_recovered']=new_loss_check is not None and new_loss_check['preserved']
    if stage=='validation':
        checks.update(wait_CI_upper_below_zero_vs_residual=pairs[RESIDUAL]['ci95_wait'][1]<0,
            wait_CI_upper_below_zero_vs_horizon=pairs[HORIZON]['ci95_wait'][1]<0,
            wait_CI_upper_below_zero_vs_EAT=pairs[EAT]['ci95_wait'][1]<0,
            wait_CI_upper_not_above_zero_vs_joint=pairs[JOINT]['ci95_wait'][1]<=0,
            wait_CI_upper_not_above_zero_vs_tail=pairs[TAIL]['ci95_wait'][1]<=0,
            whole_wait_CI_upper_below_zero_vs_joint=whole_pairs is not None and whole_pairs[JOINT]['ci95_wait'][1]<0,
            whole_wait_CI_upper_below_zero_vs_tail=whole_pairs is not None and whole_pairs[TAIL]['ci95_wait'][1]<0)
    return dict(passed=all(checks.values()),checks=checks,operational_adoption=False)


def scope_metrics(compacts,names):
    metrics={n:parent.old_metrics.policy_metrics(compacts,n) for n in names}
    for n,m in metrics.items():
        values=[wait(p) for x in compacts for p in x['branches'][n]['patients']];tail=sorted(values)[int(.9*len(values)):]
        m.update(wait_sum_seconds=sum(values),wait_p90=parent.old_metrics._quantile(values,.9),wait_cvar90=sum(tail)/len(tail))
    return metrics


def evaluate(cases,stage):
    whole=[c['compact'] for c in cases];mix=[x for x in whole if x['kind']=='mix'];stress=[x for x in whole if x['kind']!='mix']
    names=[arm(*a) for a in ARMS];metrics=scope_metrics(mix,names)
    pairs={n:parent.paired(mix,n,after=PRIMARY,bootstrap=n in (RESIDUAL,HORIZON,EAT,JOINT,TAIL)) for n in names if n!=PRIMARY}
    whole_pairs={n:parent.paired(whole,n,after=PRIMARY,bootstrap=n in (JOINT,TAIL)) for n in names if n!=PRIMARY}
    stress_pairs={n:parent.paired(stress,n,after=PRIMARY,bootstrap=False) for n in names if n!=PRIMARY}
    recovery_check=[];former_loss_check=new_loss_check=None
    if stage=='repair':
        lookup={(x['seed'],p['id']):p for x in mix for p in x['branches'][PRIMARY]['patients']}
        recovery_check=[dict(**x,preserved=not lookup[x['seed'],x['patient']]['failed'],selection_seconds=lookup[x['seed'],x['patient']]['selection_seconds']) for x in recoveries()]
        x=dict(seed=235769399510989085999300528562655985418,patient='P05');row=lookup[x['seed'],x['patient']]
        former_loss_check=dict(**x,preserved=not row['failed'],selection_seconds=row['selection_seconds'])
        x=dict(seed=275623723515674800776867121243373893786,patient='P06');row=lookup[x['seed'],x['patient']]
        new_loss_check=dict(**x,preserved=not row['failed'],selection_seconds=row['selection_seconds'])
    preserve={n:parent.paired(whole,n,after=PRIMARY,bootstrap=False) for n in (RESIDUAL,HORIZON,JOINT,TAIL)}
    return dict(mix_metrics=metrics,whole_metrics=scope_metrics(whole,names),stress_aggregate_metrics=scope_metrics(stress,names),
        paired=pairs,whole_paired=whole_pairs,stress_paired=stress_pairs,recovery_check=recovery_check,former_loss_check=former_loss_check,new_loss_check=new_loss_check,
        all_case_residual_preservation=preserve[RESIDUAL],all_case_horizon_preservation=preserve[HORIZON],
        all_case_joint_preservation=preserve[JOINT],all_case_tail_preservation=preserve[TAIL],
        assessment=assess(metrics,pairs,stage,recovery_check,preserve[RESIDUAL],preserve[HORIZON],preserve[JOINT],former_loss_check,preserve[TAIL],new_loss_check,whole_pairs,stress_pairs),
        sensitivity={str(p):{n:parent.old_metrics.policy_metrics(mix,n,p)['J'] for n in names} for p in (600,1500,3000)},
        stress_metrics={f:{n:parent.old_metrics.policy_metrics([x for x in stress if x['family']==f],n) for n in names} for f in harness.scenarios.original.FAMILY_NAMES})

def stage_cases(stage):
    from .joint_budget_audit import audit_replay
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
            ref=old[i];cache={}
            metadata=dict(reselect=ref,**ref['cached_parent_reference'])
            assert set(metadata)=={'reselect','tail','joint','horizon','residual'}
            for label,cache_folder,expected_names in (
                ('reselect',PARENT/'repair-cases',{RESELECT}),('tail',TAIL_PARENT/'repair-cases',{TAIL}),('joint',JOINT_PARENT/'repair-cases',{JOINT}),
                ('horizon',HORIZON_PARENT/'repair-cases',{HORIZON}),
                ('residual',RESIDUAL_PARENT/'development-cases',{arm(*a) for a in ARMS if a[0] not in (joint_budget_policy.NAME,'eat_joint_reselect_l15_f','eat_joint_tail_l15_f','eat_joint_first_l15_f','eat_horizon_l15_f')})):
                item=metadata[label];zipped_cache=(cache_folder/item['file']).read_bytes();assert sha(zipped_cache)==item['compressed_sha256']
                body=gzip.decompress(zipped_cache);assert sha(body)==item['raw_sha256'];cached=json.loads(body);cp=parent.compact(cached)
                assert set(cp['branches'])==expected_names
                assert all(v==ref['compact']['branches'][k] for k,v in cp['branches'].items())
                assert cached['scenario']==replay['scenario'] and cached['initial_snapshot_sha256']==replay['initial_snapshot_sha256']
                proof_cache=audit_replay(cached);assert proof_cache['passed'] and raw(cached)==body
                cache[label]=dict(file=item['file'],compressed_sha256=item['compressed_sha256'],raw_sha256=item['raw_sha256'],audit=proof_cache)
            data=deepcopy(ref['compact']);data['branches'].update(compact['branches']);compact=data
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
        for i,((kind,seed,_),replay) in enumerate(zip(todo,pool.map(joint_budget_runner.run_task,todo,chunksize=1))):
            assert sha(raw(replay['scenario']))==manifest[stage,kind,seed]
            content=raw(replay);zipped=gzip.compress(content,compresslevel=6,mtime=0);name=f'{i:03d}_{seed}.json.gz'
            write_new(folder/name,zipped);rows.append(dict(file=name,raw_sha256=sha(content),compressed_sha256=sha(zipped)))
            if len(rows)%8==0:print(f'{stage} {len(rows)}/{len(todo)} environments',flush=True)
    write_new(BUILD/(stage+'-raw-index.json'),raw(dict(cases=rows,all_outcomes_saved=True)))
    cases=stage_cases(stage);assert protocol()==frozen['protocol']
    report=dict(schema_version=1,experiment=EXPERIMENT,stage=stage,protocol=frozen['protocol'],
        elapsed_seconds=time.perf_counter()-started,cases=cases,artifact_directory=str(folder),
        new_branches=len(todo)*(1 if stage=='repair' else len(ARMS)),
        cached_branches=len(todo)*9 if stage=='repair' else 0,operational_adoption=False,
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
    assert r['cached_branches']==(len(cases)*9 if stage=='repair' else 0)
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
