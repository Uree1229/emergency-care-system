"""Bounded C1 incumbent experiment; prior scientific evidence is immutable.

441xx is exposed development; 541xx is reserved only after the historical seed audit.
The single candidate has no tuned thresholds and is not adopted by default.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import gzip
import json
from pathlib import Path
from . import offer_reserve_experiment as parent
from .c1_incumbent_engine import run_c1_incumbent_branch
from .c1_incumbent_scenarios import FAMILIES, ORIGINAL_FAMILIES, ADDITIONAL_FAMILIES, make_scenario

ROOT = parent.ROOT
BUILD = ROOT / '.codex-build/c1-incumbent-20261020'
EXPERIMENT_ID = 'c1_incumbent_20261020'
VARIANTS = ('single_augment',)
DEVELOPMENT_SEEDS = (44101, 44102, 44103)
VALIDATION_SEEDS = (54101, 54102, 54103, 54104, 54105, 54106)
NEW_SOURCES = tuple('simulation/c1_incumbent_' + n + '.py' for n in
    ('assignment','engine','audit','scenarios','experiment')) + tuple('test_c1_incumbent_' + n + '.py' for n in
    ('assignment','engine','audit','scenarios','experiment'))
TEST_FIXTURES = tuple(p.relative_to(ROOT).as_posix() for p in sorted(
    (ROOT/'simulation/fixtures/c1_incumbent_20261020').glob('*.json.gz')))
SOURCE_FILES = tuple(sorted(set(parent.SOURCE_FILES) | set(NEW_SOURCES) | set(TEST_FIXTURES)))
FREEZE_EVIDENCE = ('preservation/preservation-before-v2.json','preservation/source-handoff-v2.json',
    'preservation/preservation-closed.json','seed-audit/seed-audit-history.json',
    'seed-audit/seed-audit-current-coverage.json','tests.json','smoke.json',
    'source-freeze.json','scenario-input-manifest.json','run_tests.py','smoke.py',
    'audit_artifacts.py','freeze.py','run_stages.py','final_checks.py','write_document.py','design-plan.json')
raw, sha, read, write_new = parent.raw, parent.sha, parent.read, parent.write_new
summarize_branch = parent.summarize_branch
compare = parent.compare
totals = parent.totals
comparisons = parent.comparisons

SETTINGS = dict(variants=list(VARIANTS),mechanism='one validated blocker relocation before CP model construction',
    allocations=['C1'],foundation='unchanged normal recovery rank; no public early-readiness candidates',
    original_greedy_and_stable_exact_tie_unchanged=True,
    maximum_single_augmentation=1,maximum_initializer_candidates=128,initializer_cap_seconds=.005,
    shared_budget='initializer plus model plus both native stages; one deadline after original lazy import',
    normal_c1_budget_seconds=.05,native_stress_c1_budget_seconds=.001,
    h1_benchmark_budget_seconds=.05,h1_not_a_budget_matched_stress_superiority_claim=True,
    original_inquiry_work_clock_fifo_resources_priority_ttl_and_physical_gates_retained=True,
    no_public_early_policy_combined=True,no_added_resource_reports=True,no_future_information=True,
    references=['normal stable C1, paired budget','normal H1, diagnostic benchmark'],
    omitted_references=['safe','protected','rejected public and reserve readiness'],
    omitted_scope='complete old evidence preserved; no new superiority or deployment claim against omitted references',
    cache='exact complete inherited149 references on exposed441xx; CPU and execution not new',
    primary_gates=['no lost treatment','no individual capped treatment/final arrival worsening',
        'no case pure wait/capped treatment/inquiry increase','strict aggregate pure wait and capped treatment reduction'],
    native_50ms_and_1ms_stress_reported_separately=True,
    no_primary_pass='preserve complete failed development; skip reserved validation',
    validation_not_used_for_tuning=True,operational_adoption_evaluated=False,
    deployment_eligible=False,adopted=False,live_default='fixed_3')

def candidate_id(a,v):
    if a != 'C1' or v not in VARIANTS: raise ValueError('unknown candidate')
    return f'seeded_{a}_{v}'

def references():
    return [dict(id=f'rank_{a}_normal',allocation=a,recovery_rank=True,variant='normal') for a in ('H1','C1')]

def policies_for(phase,selection=None):
    if phase not in ('development','validation'):raise ValueError('unknown phase')
    if phase=='validation' and (not isinstance(selection,dict) or set(selection)!={'C1'}):
        raise ValueError('locked development choice required')
    variants=VARIANTS if phase=='development' else (selection['C1']['variant'],)
    return references()+[dict(id=candidate_id('C1',v),allocation='C1',variant=v,c1_incumbent=True) for v in variants]

def pairs_for(phase,selection=None):
    variants=VARIANTS if phase=='development' else (selection['C1']['variant'],)
    return tuple((f'{v}_{tag}_C1',before,candidate_id('C1',v)) for v in variants
        for tag,before in (('current','rank_C1_normal'),('h1','rank_H1_normal')))

def parent_binding():
    seal=parent.BUILD/'verification.json';proof=read(seal)
    if not proof['passed']: raise ValueError('completed parent seal required')
    if any(sha((ROOT/n).read_bytes())!=d for n,d in proof['source_sha256'].items()):
        raise ValueError('parent scientific source changed')
    report=ROOT/'simulation_results/offer_reserve_development_20261020.json'
    if sha(report.read_bytes())!=proof['final_output_sha256'][report.relative_to(ROOT).as_posix()]:
        raise ValueError('parent report changed')
    audit=parent.BUILD/'development-independent-audit.json'
    if sha(audit.read_bytes())!=proof['private_evidence_sha256'][audit.name] or not read(audit)['passed']:
        raise ValueError('parent complete independent proof differs')
    return dict(seal=str(seal),seal_sha256=sha(seal.read_bytes()),report=str(report),
        report_sha256=sha(report.read_bytes()),audit=str(audit),audit_sha256=sha(audit.read_bytes()))

def protocol():
    return dict(experiment_id=EXPERIMENT_ID,families=list(FAMILIES),inherited_families=list(ORIGINAL_FAMILIES),
        additional_families=list(ADDITIONAL_FAMILIES),development_seeds=list(DEVELOPMENT_SEEDS),
        validation_seeds=list(VALIDATION_SEEDS),development_previously_observed=True,
        settings=deepcopy(SETTINGS),source_sha256={n:sha((ROOT/n).read_bytes()) for n in SOURCE_FILES},
        parent=parent_binding(),runtime_versions=parent.protocol()['runtime_versions'])

class SavedReferences:
    def __init__(self):
        self.binding=parent_binding(); self.report=read(self.binding['report'])
        self.index={(c['seed'],c['family']):c for c in self.report['cases']}
        self.nested=parent.SavedReferences()

    def load(self,scenario):
        case=self.index[scenario['seed'],scenario['family']]
        path=Path(self.report['artifact_directory'])/case['file'];zipped=path.read_bytes()
        if sha(zipped)!=case['compressed_sha256']: raise ValueError('cached compressed raw differs')
        content=gzip.decompress(zipped);replay=json.loads(content)
        if sha(content)!=case['raw_sha256'] or replay['scenario']!=scenario:
            raise ValueError('cached full raw/scenario differs')
        available={b['id']:b for b in replay['branches']};nested_proof=None
        if not all(p['id'] in available for p in references()):
            nested,_,nested_proof=self.nested.load(scenario);available.update(nested)
        branches={p['id']:available[p['id']] for p in references()}
        audits={code:deepcopy(case['audits'][code]) for code in branches}
        for code,b in branches.items():
            if summarize_branch(scenario,b)!=case['summaries'][code] or audits[code]['passed'] is not True:
                raise ValueError('complete cached branch/summary/audit differs')
        provenance=dict(**self.binding,source_case_file=case['file'],source_compressed_sha256=sha(zipped),
            source_raw_sha256=sha(content),source_execution_order=case['actual_execution_order'],
            cached_branch_ids=list(branches),cached_branch_sha256={c:sha(raw(b)) for c,b in branches.items()},
            cached_audit_sha256={c:sha(raw(audits[c])) for c in branches},nested_provenance=nested_proof,
            new_execution_count=0,new_audit_count=0)
        return branches,audits,provenance

def budget_for(scenario):
    value=scenario.get('experiment',{}).get('c1_budget_seconds',.05)
    if type(value) not in (int,float) or value not in (.001,.05):raise ValueError('fixed native budget required')
    return float(value)

def run_policy(scenario,p):
    return run_c1_incumbent_branch(scenario,allocation=p['allocation'],seeded=bool(p.get('c1_incumbent')),
        budget_seconds=budget_for(scenario),branch_id=p['id'],label=p['id'])

def assess(cases,a,v):
    after=candidate_id(a,v);rows=[compare(c['summaries']['rank_C1_normal'],c['summaries'][after]) for c in cases]
    checks=dict(no_lost_treatment=all(r['lost_treatments']==0 for r in rows),
        no_individual_treatment_worsening=all(r['worsened']==0 for r in rows),
        no_individual_final_arrival_worsening=all(r['final_arrival_worsened']==0 for r in rows),
        no_case_total_wait_increase=all(r['total_wait_delta_minutes']<=0 for r in rows),
        no_case_capped_treatment_increase=all(r['capped_treatment_delta_minutes']<=0 for r in rows),
        no_case_inquiry_increase=all(r['inquiry_delta']<=0 for r in rows),
        strict_total_wait_reduction=sum(r['total_wait_delta_minutes'] for r in rows)<0,
        strict_capped_treatment_reduction=sum(r['capped_treatment_delta_minutes'] for r in rows)<0)
    return dict(variant=v,policy=after,primary_checks=checks,primary_passed=all(checks.values()),
        deployment_eligible=False,operational_adoption_evaluated=False,
        ranking=[not all(checks.values()),sum(r['lost_treatments'] for r in rows),
            sum(r['worsened'] for r in rows),sum(r['total_wait_delta_minutes'] for r in rows),
            sum(r['capped_treatment_delta_minutes'] for r in rows)])

def select_policy(cases):
    options=[assess(cases,'C1',v) for v in VARIANTS];winner=min(options,key=lambda r:r['ranking'])
    return dict(C1=dict(**winner,all_candidates=options,adopted=False))

def verify_freeze():
    frozen=read(BUILD/'pre-development-freeze.json')
    if not frozen['passed'] or frozen['protocol']!=protocol(): raise ValueError('source/protocol freeze differs')
    if not set(FREEZE_EVIDENCE)<=set(frozen.get('evidence_sha256',{})):
        raise ValueError('complete prerequisite evidence is required')
    for n,d in frozen['evidence_sha256'].items():
        target=(BUILD/n).resolve()
        if not target.is_relative_to(BUILD.resolve()) or sha(target.read_bytes())!=d:
            raise ValueError('frozen prerequisite differs: '+n)
    return frozen

def verify_development_lock(lock):
    if lock['protocol']!=protocol() or not lock['validation_allowed']:
        raise ValueError('locked passing development choice required')
    content=Path(lock['development_report']).read_bytes();dev=json.loads(content)
    if sha(content)!=lock['development_report_sha256'] or dev['protocol']!=lock['protocol'] or \
            dev['phase']!='development' or select_policy(dev['cases'])!=lock['selected_policy'] or \
            not all(r['primary_passed'] for r in lock['selected_policy'].values()):
        raise ValueError('locked development differs')
    proof=read(BUILD/'development-independent-audit.json')
    if not proof['passed'] or proof['report_sha256']!=lock['development_report_sha256']:
        raise ValueError('independent full development proof required')

def execute(phase,output,lock_path):
    from .c1_incumbent_audit import audit_branch as guard_audit
    from .recovery_rank_audit import audit_branch as original_audit
    
    output,lock_path=Path(output),Path(lock_path);verify_freeze();frozen=protocol()
    if phase not in ('development','validation'): raise ValueError('unknown phase')
    folder=output.parent/(output.name+'_cases');report_path=output.with_suffix('.json')
    if folder.exists() or report_path.exists() or (phase=='development' and lock_path.exists()):
        raise ValueError('scientific evidence cannot be overwritten')
    lock=read(lock_path) if phase=='validation' else None;selection=None if lock is None else lock['selected_policy']
    if lock:
        verify_development_lock(lock)
    policies=policies_for(phase,selection);pairs=pairs_for(phase,selection);cache=SavedReferences() if lock is None else None
    manifest={(r['seed'],r['family']):r['sha256'] for r in read(BUILD/'scenario-input-manifest.json')['entries']}
    folder.mkdir(parents=True);write_new(folder/'protocol.json',raw(frozen));cases=[];runs=reused=0
    seeds=DEVELOPMENT_SEEDS if lock is None else VALIDATION_SEEDS
    for si,seed in enumerate(seeds):
        for fi,family in enumerate(FAMILIES):
            scenario=make_scenario(seed,family)
            if sha(raw(scenario))!=manifest[seed,family]: raise ValueError('frozen scenario differs')
            cached=lock is None and family in ORIGINAL_FAMILIES
            branches,audits,provenance=cache.load(scenario) if cached else ({},{},None)
            fresh=[p for p in policies if p['id'] not in branches];offset=(si+fi)%len(fresh)
            order=fresh[offset:]+fresh[:offset];new={p['id']:run_policy(scenario,p) for p in order}
            replay=dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=deepcopy(scenario),
                branches=[new[p['id']] for p in fresh],execution_order=[p['id'] for p in order],
                cached_reference_provenance=provenance)
            content=raw(replay);zipped=gzip.compress(content,compresslevel=6,mtime=0)
            name=f'{seed}_{fi:03d}_{family}.json.gz';write_new(folder/name,zipped)
            for p in fresh:
                code=p['id'];b=new[code]
                audits[code]=guard_audit(scenario,b)
                if audits[code]['passed'] is not True: raise ValueError('full raw audit failed')
                branches[code]=b
            summaries={p['id']:summarize_branch(scenario,branches[p['id']]) for p in policies}
            if any(b.get('resource_updates_enabled') is not False for b in branches.values()) or any(
                r[k] for r in summaries.values() for k in ('added_resource_publications','added_resource_update_receipts','added_resource_update_cycles')):
                raise ValueError('extra resource publication appeared')
            runs+=len(fresh);reused+=len(policies)-len(fresh)
            cases.append(dict(seed=seed,family=family,file=name,compressed_sha256=sha(zipped),raw_sha256=sha(content),
                original_scenario_sha256=sha(raw(scenario)),c1_budget_seconds=budget_for(scenario),
                stratum='native_1ms' if budget_for(scenario)==.001 else 'native_50ms',branch_ids=[p['id'] for p in policies],
                freshly_executed_branch_ids=[p['id'] for p in fresh],actual_execution_order=[p['id'] for p in order],
                cached_reference_provenance=provenance,summaries=summaries,audits=audits,
                comparisons={tag:compare(summaries[a],summaries[b]) for tag,a,b in pairs}))
            print(f'{phase} {len(cases)}/{len(seeds)*len(FAMILIES)} new={runs} cached={reused} {family}',flush=True)
    if protocol()!=frozen: raise ValueError('source/protocol changed during experiment')
    selected=select_policy(cases) if lock is None else selection
    report=dict(schema_version=1,experiment_id=EXPERIMENT_ID,synthetic=True,phase=phase,protocol=frozen,
        artifact_directory=str(folder.resolve()),cases=cases,runs=runs,cached_reference_branches=reused,
        total_compared_branches=runs+reused,pairs=[list(p) for p in pairs],policies=policies,
        comparisons=comparisons(cases,pairs),
        comparisons_by_stratum={name:comparisons([c for c in cases if c['stratum']==name],pairs)
            for name in ('native_50ms','native_1ms')},totals=totals(cases,[p['id'] for p in policies]),
        selected_policy=selected,validation_allowed=all(r['primary_passed'] for r in selected.values()),
        validation_diagnostics=None if lock is None else {a:assess(cases,a,selected[a]['variant']) for a in ('C1',)},
        adopted=False,live_default='fixed_3')
    data=raw(report);write_new(report_path,data)
    if lock is None: write_new(lock_path,raw(dict(protocol=frozen,development_report=str(report_path.resolve()),
        development_report_sha256=sha(data),selected_policy=selected,validation_allowed=report['validation_allowed'])))
    print(dict(phase=phase,cases=len(cases),runs=runs,cached=reused,validation_allowed=report['validation_allowed']),flush=True)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--phase',required=True,choices=('development','validation'))
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--lock',type=Path,required=True)
    args=parser.parse_args();execute(args.phase,args.output,args.lock)
