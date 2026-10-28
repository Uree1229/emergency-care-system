"""One factor: expected-cost shortlist, paired against unchanged EAT.

Primary W is capped hospital-selection waiting, not travel or treatment wait.
Freeze precedes all performance inputs. One development and one held-out run.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import platform
import random
import time

from . import experiment as old_metrics, harness, shortlist_policy, shortlist_runner

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'.codex-build/eat-shortlist-20261020'
EXPERIMENT='eat_shortlist_20261020'
NAMESPACE='eat-shortlist-v1-20261020-48d8b5c8-7861-458f-8af5-fa1be7d8fec3'
ARMS=(('fixed_3','immediate'),('eat_l15_f','immediate'),
      (shortlist_policy.NAME,'immediate'),('v3_w45_h900','immediate'),('eat_l15_f','hold120'))
PRIMARY='eat_shortlist_l15_f|immediate'
INCUMBENT='eat_l15_f|immediate'
ANCHOR='fixed_3|immediate'
BOOTSTRAP=10000
NEW_SOURCES=tuple('inquiry_lab/shortlist_'+n+'.py' for n in ('policy','runner','audit','experiment'))+tuple(
    'test_inquiry_lab_shortlist_'+n+'.py' for n in ('policy','runner','audit','experiment'))+(
    '.codex-build/eat-shortlist-20261020/run_checks.py',)
FIXTURE='simulation/fixtures/eat_shortlist_20261020/mix7.json.gz'
SETTINGS=dict(candidate_count=1,changed_factor='membership of up to six EAT planning candidates',
    primary='all-patient selection waiting; unsuccessful selection capped at 300 seconds',
    primary_not_travel_or_post_arrival_treatment_wait=True,lambda_seconds=15,
    eat_concurrency=4,other_eat_kernel_and_confirmation_unchanged=True,
    development_mix=96,validation_mix=320,stress_family_seeds=[3,6],bootstrap_resamples=BOOTSTRAP,
    development_gates=['W strictly lower than EAT','failures no more than EAT','J no more than EAT',
        'inquiry count no more than fixed3','hospital review work no more than fixed3'],
    validation_gate='development gates plus scenario-cluster 95% upper confidence limit of delta W < 0',
    individual_worsenings_and_lost_successes_reported=True,global_zero_individual_harm_not_claimed=True,
    heldout_not_used_to_reselect_or_retune=True,stress_excluded_from_primary=True,
    seed_derivation='SHA256 unique named stage/index namespace; first128 bits with high bit set',
    seed_scope='disjoint from reported EAT/confirm seeds; no repeated global historical gzip scan',
    baseline_cache_reuse=False,all_comparisons_same_fresh_scenario=True,
    server_or_ui_or_live_database_changed=False,operational_adoption=False)


def raw(x):return json.dumps(x,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
def sha(b):return hashlib.sha256(b).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def write_new(p,b):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('xb') as f:f.write(b)
def arm(policy,rule):return policy+'|'+rule
def seeds(role,count):
    return tuple(int.from_bytes(hashlib.sha256(f'{NAMESPACE}|{role}|{i}'.encode()).digest()[:16],'big')|(1<<127)
        for i in range(count))
def tasks(stage):
    if stage not in ('development','validation'):raise ValueError('known stage required')
    count=96 if stage=='development' else 320;n=3 if stage=='development' else 6
    mix=[('mix',s,ARMS) for s in seeds(stage+'-mix',count)]
    return mix+[(f'family:{f}',s,ARMS) for s in seeds(stage+'-stress',n)
        for f in harness.scenarios.original.FAMILY_NAMES]
def source_files():
    before=read(BUILD/'preservation/before.json')
    return tuple(sorted(set(before['old_source_sha256'])|set(NEW_SOURCES)|{FIXTURE}))
def source_hashes():return {n:sha((ROOT/n).read_bytes()) for n in source_files()}
def verify_old():
    before=read(BUILD/'preservation/before.json');assert before['passed']
    for group in ('old_source_sha256','reference_files_sha256'):
        for n,d in before[group].items():
            if sha((ROOT/n).read_bytes())!=d:raise ValueError('bound original changed: '+n)
    return sha((BUILD/'preservation/before.json').read_bytes())
def protocol():
    return dict(experiment=EXPERIMENT,settings=SETTINGS,arms=[list(a) for a in ARMS],namespace=NAMESPACE,
        source_sha256=source_hashes(),parent_binding_sha256=verify_old(),python=platform.python_version())


def freeze():
    p=protocol();tests=read(BUILD/'tests.json')
    assert tests['passed'] and tests['source_sha256_before']==tests['source_sha256_after']==p['source_sha256']
    all_seeds=[s for stage in ('development','validation') for kind,s,_ in tasks(stage)]
    distinct_roles=[*seeds('development-mix',96),*seeds('validation-mix',320),
        *seeds('development-stress',3),*seeds('validation-stress',6)]
    assert len(set(distinct_roles))==425 and min(all_seeds)>=1<<127
    for result in ('inquiry_lab_validation_20261020.json','inquiry_lab_confirm_validation_20261020.json'):
        old=read(ROOT/'simulation_results'/result)
        assert not set(distinct_roles)&{r['seed'] for r in old['results']}
    write_new(BUILD/'source-freeze.json',raw(dict(passed=True,protocol=p,
        recorded_at=time.strftime('%Y-%m-%d %H:%M:%S'),formal_performance_runs_before_freeze=0,
        preflight=dict(seed=7,fixture=FIXTURE,used_for_adapter_and_audit_tests=True,
            used_to_tune_policy_or_gates=False),
        validation_scenarios_generated_before_freeze=False)))
    # Generate and hash inputs only after fixing source and all gates.
    entries=[]
    for stage in ('development','validation'):
        for kind,seed,_ in tasks(stage):
            scenario=harness.build(kind,seed)
            entries.append(dict(stage=stage,kind=kind,seed=seed,sha256=sha(raw(scenario))))
    evidence={f:sha((BUILD/f).read_bytes()) for f in ('preservation/before.json','tests.json','source-freeze.json')}
    write_new(BUILD/'input-manifest.json',raw(dict(entries=entries,generated_after_freeze=True)))
    evidence['input-manifest.json']=sha((BUILD/'input-manifest.json').read_bytes())
    write_new(BUILD/'pre-development.json',raw(dict(passed=True,protocol=p,evidence_sha256=evidence)))
    print(dict(frozen=True,sources=len(p['source_sha256']),inputs=len(entries)),flush=True)


def verify_freeze():
    f=read(BUILD/'pre-development.json')
    assert f['passed'] and f['protocol']==protocol()
    for n,d in f['evidence_sha256'].items():assert sha((BUILD/n).read_bytes())==d
    return f


def wait(p):return 300 if p['failed'] else p['selection_seconds']
def compact(replay):
    s=replay['scenario'];branches={}
    for b in replay['branches']:
        x=harness.compact(b);x['rule']=b['rule']
        branches[arm(b['policy'],b['rule'])]=x
    return dict(kind='mix' if s['family']==harness.scenarios.MIX_FAMILY else 'family:'+s['family'],
        seed=s['seed'],family=s['family'],scenario_id=s['id'],mix=s.get('mix'),patient_count=len(s['patients']),
        oracle=harness.scenarios.oracle(s),branches=branches)


def paired(rows,before,after=PRIMARY,bootstrap=False):
    clusters=[];better=worse=same=lost=gained=0;max_wait=0;max_J=0
    for r in rows:
        old={p['id']:p for p in r['branches'][before]['patients']}
        new={p['id']:p for p in r['branches'][after]['patients']}
        assert set(old)==set(new)
        dw=dj=0
        for pid,p in new.items():
            delta=wait(p)-wait(old[pid]);dw+=delta
            dJ=harness.patient_cost(p)-harness.patient_cost(old[pid]);dj+=dJ
            better+=delta<0;worse+=delta>0;same+=delta==0
            max_wait=max(max_wait,delta);max_J=max(max_J,dJ)
            lost+=not old[pid]['failed'] and p['failed'];gained+=old[pid]['failed'] and not p['failed']
        clusters.append((dw,dj,len(old)))
    count=sum(n for _,_,n in clusters)
    out=dict(before=before,after=after,patients=count,scenarios=len(rows),
        wait_sum_delta_seconds=sum(d for d,_,_ in clusters),wait_mean_delta_seconds=sum(d for d,_,_ in clusters)/count,
        J_mean_delta_seconds=sum(d for _,d,_ in clusters)/count,wait_improved=better,wait_worsened=worse,wait_same=same,
        lost_selections=lost,new_selections=gained,maximum_individual_wait_increase_seconds=max_wait,
        maximum_individual_J_increase_seconds=max_J,
        scenarios_wait_increased=sum(d>0 for d,_,_ in clusters),ci95_wait=None,ci95_J=None)
    if bootstrap:
        rng=random.Random(2026102003);ws=[];js=[];k=len(clusters)
        for _ in range(BOOTSTRAP):
            sample=[clusters[rng.randrange(k)] for _ in range(k)];n=sum(r[2] for r in sample)
            ws.append(sum(r[0] for r in sample)/n);js.append(sum(r[1] for r in sample)/n)
        ws.sort();js.sort();lo=int(.025*BOOTSTRAP);hi=int(.975*BOOTSTRAP)-1
        out.update(ci95_wait=[ws[lo],ws[hi]],ci95_J=[js[lo],js[hi]])
    return out


def assess(metrics,pair,stage):
    if stage not in ('development','validation'):raise ValueError('known stage required')
    c=metrics[PRIMARY];b=metrics[INCUMBENT];fixed=metrics[ANCHOR]
    checks=dict(wait_strictly_lower=pair['wait_sum_delta_seconds']<0,
        failures_not_increased=c['failed']<=b['failed'],arrival_J_not_increased=c['J']<=b['J'],
        inquiries_not_more_than_fixed3=c['inquiries_per_patient']<=fixed['inquiries_per_patient'],
        review_work_not_more_than_fixed3=c['review_work_seconds_per_patient']<=fixed['review_work_seconds_per_patient'])
    if stage=='validation':checks['wait_cluster_ci_upper_below_zero']=pair['ci95_wait'][1]<0
    return dict(passed=all(checks.values()),checks=checks,operational_adoption=False)


def evaluate(cases,stage):
    mix=[c['compact'] for c in cases if c['compact']['kind']=='mix']
    stress=[c['compact'] for c in cases if c['compact']['kind']!='mix']
    names=[arm(*a) for a in ARMS];metrics={n:old_metrics.policy_metrics(mix,n) for n in names}
    for n,m in metrics.items():
        values=[wait(p) for c in mix for p in c['branches'][n]['patients']]
        m.update(wait_sum_seconds=sum(values),wait_p90=old_metrics._quantile(values,.9),
            wait_cvar90=sum(sorted(values)[int(.9*len(values)):])/len(sorted(values)[int(.9*len(values)):]))
    pairs={n:paired(mix,n,bootstrap=n==INCUMBENT) for n in names if n!=PRIMARY}
    return dict(mix_metrics=metrics,paired=pairs,assessment=assess(metrics,pairs[INCUMBENT],stage),
        sensitivity={str(p):{n:old_metrics.policy_metrics(mix,n,p)['J'] for n in names} for p in (600,1500,3000)},
        stress_metrics={f:{n:old_metrics.policy_metrics([r for r in stress if r['family']==f],n) for n in names}
            for f in harness.scenarios.original.FAMILY_NAMES})


def execute(stage,workers=4):
    from .shortlist_audit import audit_replay
    frozen=verify_freeze();folder=BUILD/(stage+'-cases');report_path=BUILD/(stage+'.json')
    if folder.exists() or report_path.exists():raise FileExistsError('no performance overwrite or retry')
    if stage=='validation':
        dev=read(BUILD/'development.json');audit=read(BUILD/'development-audit.json')
        lock=read(BUILD/'development-lock.json');dev_sha=sha((BUILD/'development.json').read_bytes())
        assert dev['assessment']['passed'] and audit['passed'] and audit['report_sha256']==dev_sha
        assert dev['protocol']==lock['protocol']==frozen['protocol']
        assert lock['report_sha256']==dev_sha and lock['candidate']==PRIMARY
        assert lock['assessment']==dev['assessment'] and lock['validation_allowed'] is True
        assert audit['source_sha256']==frozen['protocol']['source_sha256']
    folder.mkdir();todo=tasks(stage);manifest={(x['stage'],x['kind'],x['seed']):x['sha256'] for x in read(BUILD/'input-manifest.json')['entries']}
    cases=[];started=time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for index,((kind,seed,_),replay) in enumerate(zip(todo,pool.map(shortlist_runner.run_task,todo,chunksize=1))):
            assert sha(raw(replay['scenario']))==manifest[stage,kind,seed]
            content=raw(replay);zipped=gzip.compress(content,compresslevel=6,mtime=0)
            name=f'{index:03d}_{seed}.json.gz';write_new(folder/name,zipped)
            cases.append(dict(file=name,raw_sha256=sha(content),compressed_sha256=sha(zipped)))
            if len(cases)%8==0:print(f'{stage} {len(cases)}/{len(todo)} environments',flush=True)
    # Preserve every fresh performance outcome before any assertion by the auditor.
    write_new(BUILD/(stage+'-raw-index.json'),raw(dict(cases=cases,all_outcomes_saved=True)))
    for c in cases:
        content=gzip.decompress((folder/c['file']).read_bytes());replay=json.loads(content)
        proof=audit_replay(replay);assert proof['passed'] and raw(replay)==content
        c.update(audit=proof,compact=compact(replay))
    assert protocol()==frozen['protocol']
    report=dict(schema_version=1,experiment=EXPERIMENT,stage=stage,protocol=frozen['protocol'],
        elapsed_seconds=time.perf_counter()-started,cases=cases,artifact_directory=str(folder),
        new_branches=len(cases)*len(ARMS),cached_branches=0,operational_adoption=False,**evaluate(cases,stage))
    write_new(report_path,raw(report))
    if stage=='development':write_new(BUILD/'development-lock.json',raw(dict(protocol=frozen['protocol'],
        report_sha256=sha(raw(report)),candidate=PRIMARY,assessment=report['assessment'],
        validation_allowed=report['assessment']['passed'])))
    print(dict(stage=stage,cases=len(cases),assessment=report['assessment']),flush=True)
    return report


def audit_saved(stage):
    from .shortlist_audit import audit_replay
    verify_freeze();p=BUILD/(stage+'.json');report=read(p);rows=[]
    assert report['protocol']==protocol() and report['stage']==stage and report['experiment']==EXPERIMENT
    todo=tasks(stage);assert len(report['cases'])==len(todo)
    assert report['new_branches']==len(todo)*len(ARMS) and report['cached_branches']==0
    manifest={(x['stage'],x['kind'],x['seed']):x['sha256'] for x in read(BUILD/'input-manifest.json')['entries']}
    expected_folder=BUILD/(stage+'-cases');assert Path(report['artifact_directory'])==expected_folder
    assert {x.name for x in expected_folder.iterdir()}=={c['file'] for c in report['cases']}
    for index,((kind,seed,_),c) in enumerate(zip(todo,report['cases'])):
        assert c['file']==f'{index:03d}_{seed}.json.gz'
        z=(Path(report['artifact_directory'])/c['file']).read_bytes();assert sha(z)==c['compressed_sha256']
        b=gzip.decompress(z);assert sha(b)==c['raw_sha256'];r=json.loads(b)
        assert r['arms']==[list(a) for a in ARMS]
        assert sha(raw(r['scenario']))==manifest[stage,kind,seed]
        assert audit_replay(r)==c['audit'] and compact(r)==c['compact'] and raw(r)==b
        assert (c['compact']['kind'],c['compact']['seed'])==(kind,seed)
        rows.append(c)
    derived=evaluate(rows,stage)
    for k,v in derived.items():assert report[k]==v
    result=dict(passed=True,report_sha256=sha(p.read_bytes()),cases=len(rows),new_full_branch_audits=len(rows)*len(ARMS),
        source_sha256=source_hashes(),new_performance_runs=0,scope='stored full raw verification; no policy or solver execution')
    write_new(BUILD/(stage+'-audit.json'),raw(result));print(dict(audit=stage,passed=True,cases=len(rows)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('freeze','development','validation','audit-development','audit-validation'))
    parser.add_argument('--workers',type=int,default=4);args=parser.parse_args()
    if args.stage=='freeze':freeze()
    elif args.stage.startswith('audit-'):audit_saved(args.stage[6:])
    else:execute(args.stage,args.workers)
