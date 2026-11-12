"""Synthetic protocol/accountant checks; no policy, scenario or clock rollout."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_success_experiment as e


def metric(failed=4,wait=1000,tiers=None):
    return dict(failed=failed,wait_sum_seconds=wait,
        failed_by_priority=tiers or {'4':failed,'2':0,'1':0},J=800,inquiries_per_patient=4,
        review_work_seconds_per_patient=300)


def metrics(failed=4,wait=1000):
    return {e.arm(*a):metric(failed,wait) for a in e.ARMS}


def cis(fupper=0,wupper=-1):
    return {e.arm(*a):dict(ci95_failed_per_patient=[-1,fupper],ci95_wait_per_patient=[-2,wupper])
        for a in e.ARMS if e.arm(*a)!=e.PRIMARY}


def accountant():
    path=e.BUILD/'finalize_phase.py'
    spec=importlib.util.spec_from_file_location('cohort_terminal_accountant_synthetic',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class CohortSuccessExperimentTests(unittest.TestCase):
    def test_priority_failure_vector_dominates_all_lower_tiers_and_wait(self):
        high=metric(100,0,{'4':1,'2':99,'1':0})
        low=metric(2,1000000,{'4':0,'2':2,'1':0})
        self.assertLess(e.lex_vector(low),e.lex_vector(high))
        self.assertLess(e.lex_vector(metric(3,99999)),e.lex_vector(metric(4,0)))

    def test_equal_failures_use_wait_and_exact_tie_is_not_strict_improvement(self):
        m=metrics();self.assertFalse(e.assess(m,{},cis(),'development')['passed'])
        m[e.PRIMARY]['wait_sum_seconds']-=1
        self.assertTrue(e.assess(m,{},cis(),'development')['passed'])

    def test_success_gain_may_take_more_wait_under_the_new_objective(self):
        m=metrics();m[e.PRIMARY]=metric(3,999999)
        self.assertTrue(e.assess(m,{},cis(),'development')['passed'])

    def test_wait_gain_cannot_pay_for_a_failure(self):
        m=metrics();m[e.PRIMARY]=metric(5,0)
        self.assertFalse(e.assess(m,{},cis(),'development')['passed'])

    def test_best_of_all_ten_controls_is_used(self):
        m=metrics();m[e.PRIMARY]=metric(3,800);m[e.arm(*e.ARMS[9])]=metric(2,900)
        self.assertFalse(e.assess(m,{},cis(),'development')['passed'])
        m[e.arm(*e.ARMS[9])]=metric(3,700)
        self.assertFalse(e.assess(m,{},cis(),'development')['passed'])

    def test_J_fixed3_work_and_identity_loss_are_not_retired_hard_gates(self):
        m=metrics();m[e.PRIMARY].update(wait_sum_seconds=999,J=99999,
            inquiries_per_patient=10,review_work_seconds_per_patient=99999,lost_selections=7)
        result=e.assess(m,{},cis(),'development')
        self.assertTrue(result['passed'])
        self.assertEqual(set(result['checks']),{'whole_strictly_better_than_best_control'})

    def test_known_origins_cannot_compensate_for_each_other(self):
        whole=metrics();whole[e.PRIMARY]=metric(3,500)
        origins={o:metrics() for o in ('repair','development')}
        origins['repair'][e.PRIMARY]=metric(2,0)
        origins['development'][e.PRIMARY]=metric(5,0)
        result=e.assess(whole,origins,cis(),'repair')
        self.assertFalse(result['passed']);self.assertFalse(result['checks']['development_whole_no_worse_than_best_control'])

    def test_known_origin_ties_allowed_but_pooled_B_strict_and_both_origins_required(self):
        m=metrics();origins={o:metrics() for o in ('repair','development')}
        self.assertFalse(e.assess(m,origins,cis(),'repair')['passed'])
        m[e.PRIMARY]['wait_sum_seconds']-=1
        self.assertTrue(e.assess(m,origins,cis(),'repair')['passed'])
        self.assertFalse(e.assess(m,{'repair':origins['repair']},cis(),'repair')['passed'])

    def test_validation_failure_upper_strictly_negative_accepts_positive_wait_upper(self):
        m=metrics();m[e.PRIMARY]=metric(3,99999)
        self.assertTrue(e.assess(m,{},cis(-.01,99999),'validation')['passed'])

    def test_validation_zero_failure_upper_requires_strictly_negative_wait_upper(self):
        m=metrics();m[e.PRIMARY]['wait_sum_seconds']-=1
        self.assertTrue(e.assess(m,{},cis(0,-.001),'validation')['passed'])
        self.assertFalse(e.assess(m,{},cis(0,0),'validation')['passed'])

    def test_validation_positive_failure_upper_rejects_even_large_wait_gain(self):
        m=metrics();m[e.PRIMARY]['wait_sum_seconds']-=1
        self.assertFalse(e.assess(m,{},cis(.001,-9999),'validation')['passed'])

    def test_validation_checks_all_ten_marginal_bounds_and_point_improvement(self):
        m=metrics();m[e.PRIMARY]['wait_sum_seconds']-=1
        for name in cis():
            changed=cis();changed[name]['ci95_wait_per_patient'][1]=0
            with self.subTest(control=name):self.assertFalse(e.assess(m,{},changed,'validation')['passed'])
        self.assertFalse(e.assess(metrics(),{},cis(-1,-1),'validation')['passed'])
        with self.assertRaises(ValueError):e.assess(m,{},cis(),'other')

    def test_known_240_and_fresh_task_shapes_and_seed_roles(self):
        tasks={s:e.tasks(s) for s in ('repair','development','validation')}
        self.assertEqual({s:len(v) for s,v in tasks.items()},{'repair':240,'development':120,'validation':368})
        self.assertTrue(all(len(a)==1 for _,_,a in tasks['repair']))
        self.assertTrue(all(len(a)==11 for s in ('development','validation') for _,_,a in tasks[s]))
        roles=[*e.seeds('development-mix',96),*e.seeds('validation-mix',320),
            *e.seeds('development-stress',3),*e.seeds('validation-stress',6)]
        self.assertEqual(len(set(roles)),425)
        self.assertFalse(set(roles)&{s for _,s,_ in tasks['repair']})
        for stage,stress in (('development',3),('validation',6)):
            rows=[r for r in tasks[stage] if r[0]!='mix']
            self.assertEqual(len({s for _,s,_ in rows}),stress)
            self.assertTrue(all(sum(t==s for _,t,_ in rows)==8 for _,s,_ in rows))
        with self.assertRaises(ValueError):e.tasks('unknown')

    def test_protocol_bounds_default_priority_and_report_only_regressions(self):
        p=e.protocol();self.assertEqual(p,json.loads(e.raw(p)))
        self.assertEqual(len(p['source_sha256']),404)
        self.assertEqual({k:len(v) for k,v in p['regressions_report_only'].items()},
            {'old_six':6,'prior_development_tail_lost':7,'prior_development_budget_gained':4})
        self.assertIn('unknown/default',p['priority_performance_scope'])
        self.assertFalse(p['settings']['global_optimum_or_clinical_validation_claim'])
        self.assertEqual(p['settings']['maximum_new_branches'],5608)
        self.assertFalse(p['settings']['operational_adoption'])

    def test_freeze_missing_core_tests_stops_before_input_generation(self):
        report=dict(passed=True,source_sha256_before={'a':'b'},source_sha256_after={'a':'b'},
            module_counts={},errors=0,failures=0,skipped=0)
        with patch.object(e,'protocol',return_value={'source_sha256':{'a':'b'}}),patch.object(e,'read',return_value=report),\
                patch.object(e,'write_new') as write,patch.object(e.foundation.harness,'build') as build:
            with self.assertRaises(AssertionError):e.freeze()
            write.assert_not_called();build.assert_not_called()

    def test_exact_working_doc_exclusion_leaves_every_other_source_and_ref_strict(self):
        self.assertEqual(len(e.verify_old()),64)
        with patch.object(e,'REFERENCE_EXCLUSION_SHA','0'*64):
            with self.assertRaises(AssertionError):e.verify_old()
        read=e.read;before=read(e.BUILD/'preservation/before.json')
        for group in ('old_source_sha256','reference_files_sha256'):
            bad=deepcopy(before);name=next(n for n in bad[group] if n!=e.WORKING_REFERENCE);bad[group][name]='0'*64
            def changed(path):return bad if Path(path).name=='before.json' else read(path)
            with self.subTest(group=group),patch.object(e,'read',side_effect=changed):
                with self.assertRaises(AssertionError):e.verify_old()

    def test_excluded_doc_original_hash_metadata_is_checked(self):
        read=e.read;before=read(e.BUILD/'preservation/before.json')
        before['reference_files_sha256'][e.WORKING_REFERENCE]='0'*64
        def changed(path):return before if Path(path).name=='before.json' else read(path)
        with patch.object(e,'read',side_effect=changed):
            with self.assertRaises(AssertionError):e.verify_old()

    def test_failures_count_as_300_wait_without_reading_missing_selection(self):
        self.assertEqual(e.wait({'failed':True}),300)
        self.assertEqual(e.wait({'failed':False,'selection_seconds':70}),70)

    def _prior(self,mutation=None):
        with tempfile.TemporaryDirectory() as tmp,patch.object(e,'BUILD',Path(tmp)),\
                patch.object(e,'protocol',return_value={'locked':True}),patch.object(e,'source_hashes',return_value={'a':'digest'}),\
                patch.object(e,'stage_cases',return_value=[]) as cases,patch.object(e,'ProcessPoolExecutor') as pool:
            assessment={'passed':mutation!='failed'}
            r=dict(assessment=assessment,protocol={'locked':True},cases=[],prior_stage_evidence_sha256={})
            e.write_new(e.BUILD/'repair.json',e.raw(r));digest=e.sha(e.raw(r))
            a=dict(passed=True,report_sha256=digest,source_sha256={'a':'digest'})
            lock=dict(candidate=e.PRIMARY,assessment=assessment,protocol={'locked':True},next_stage_allowed=assessment['passed'],report_sha256=digest)
            if mutation=='hash':lock['report_sha256']='wrong'
            if mutation=='candidate':lock['candidate']='wrong'
            if mutation=='source':a['source_sha256']={'wrong':'value'}
            if mutation=='protocol':lock['protocol']={'wrong':True}
            e.write_new(e.BUILD/'repair-audit.json',e.raw(a));e.write_new(e.BUILD/'repair-lock.json',e.raw(lock))
            if mutation:
                with patch.object(e,'verify_freeze',return_value={'protocol':{'locked':True}}):
                    with self.assertRaises(AssertionError):e.execute('development')
                self.assertFalse((e.BUILD/'development-cases').exists());cases.assert_not_called()
            else:
                self.assertEqual(len(e.verify_prior('development')),3);cases.assert_called_once_with('repair')
            pool.assert_not_called()

    def test_failed_or_changed_prior_stops_before_performance(self):
        for mutation in ('failed','hash','candidate','source','protocol'):
            with self.subTest(mutation=mutation):self._prior(mutation)

    def test_valid_prior_uses_only_saved_cases(self):self._prior()


@contextmanager
def saved_collector_fixture():
    """Four artificial objects expose folder shadowing in BOTH known layouts."""
    from inquiry_lab import cohort_success_audit
    with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
        root=Path(tmp);build=root/'new';build.mkdir();parents={n:root/n for n in ('budget','reselect','tail','joint','horizon','residual')}
        for path in parents.values():path.mkdir()
        bindings={};todo=[];entries=[];indexed=[];known=[]
        allnames={e.arm(*a) for a in e.foundation.ARMS}
        names={'budget':{e.BUDGET},'reselect':{e.foundation.RESELECT},'tail':{e.foundation.TAIL},'joint':{e.foundation.JOINT},'horizon':{e.foundation.HORIZON}}
        names['residual']=allnames-set().union(*names.values())
        def save(folder,name,replay,bind=False):
            folder.mkdir(exist_ok=True);body=e.raw(replay);packed=gzip.compress(body,mtime=0)
            e.write_new(folder/name,packed)
            metadata=dict(file=name,raw_sha256=e.sha(body),compressed_sha256=e.sha(packed))
            if bind:bindings[(folder/name).relative_to(root).as_posix()]={k:v for k,v in metadata.items() if k!='file'}
            return metadata
        for i,(origin,seed) in enumerate((('repair',101),('repair',202),('development',303),('development',404))):
            name=f'{i:03d}_{seed}.json.gz';scenario=dict(seed=seed,synthetic_collector_only=True)
            shared=dict(scenario=scenario,initial_snapshot_sha256='snapshot'+str(seed),environment_sha256='env'+str(seed))
            compact=dict(kind='mix',seed=seed,branches={e.PRIMARY:dict(marker='new'+str(seed))})
            indexed.append(save(build/'repair-cases',name,dict(**shared,arms=[list(a) for a in e.REPAIR_ARMS],compact=compact)))
            oldbranches={n:dict(marker=n+str(seed)) for n in allnames};oldcompact=dict(kind='mix',seed=seed,branches=oldbranches)
            if origin=='repair':
                refs={}
                for label,arms in names.items():
                    replay=dict(**shared,arms=[n.split('|') for n in sorted(arms)],compact=dict(kind='mix',seed=seed,branches={n:oldbranches[n] for n in arms}))
                    refs[label]=save(parents[label]/('development-cases' if label=='residual' else 'repair-cases'),name,replay,True)
                ref=dict(**refs['budget'],compact=oldcompact,cached_parent_reference={k:v for k,v in refs.items() if k!='budget'})
            else:
                replay=dict(**shared,arms=[list(a) for a in e.foundation.ARMS],compact=oldcompact)
                ref=dict(**save(parents['budget']/'development-cases',name,replay,True),compact=oldcompact)
            known.append((origin,ref));todo.append(('mix',seed,e.REPAIR_ARMS))
            entries.append(dict(stage='repair',origin_stage=origin,kind='mix',seed=seed,sha256=e.sha(e.raw(scenario))))
        e.write_new(build/'input-manifest.json',e.raw(dict(entries=entries)))
        e.write_new(build/'repair-raw-index.json',e.raw(dict(cases=indexed,all_outcomes_saved=True)))
        e.write_new(build/'preservation/before.json',e.raw(dict(known_raw_bindings_by_path=bindings)))
        for name,value in (('ROOT',root),('BUILD',build),('PARENT',parents['budget'])):stack.enter_context(patch.object(e,name,value))
        for name,label in (('PARENT','reselect'),('TAIL_PARENT','tail'),('JOINT_PARENT','joint'),('HORIZON_PARENT','horizon'),('RESIDUAL_PARENT','residual')):
            stack.enter_context(patch.object(e.foundation,name,parents[label]))
        stack.enter_context(patch.object(e,'known_rows',return_value=known));stack.enter_context(patch.object(e,'tasks',return_value=todo))
        stack.enter_context(patch.object(e.foundation.parent,'compact',side_effect=lambda replay:deepcopy(replay['compact'])))
        audit=stack.enter_context(patch.object(cohort_success_audit,'audit_replay',return_value=dict(passed=True)))
        execute=stack.enter_context(patch.object(e.cohort_success_runner,'run_task',side_effect=AssertionError('no rollout')))
        yield dict(root=root,build=build,known=known,bindings=bindings,audit=audit,execute=execute)


class SavedCollectorRegressionTests(unittest.TestCase):
    def test_multiple_cases_in_six_object_and_ten_arm_bundle_layout(self):
        with saved_collector_fixture() as f:
            rows=e.stage_cases('repair')
            self.assertEqual(len(rows),4);self.assertEqual(f['audit'].call_count,18)
            self.assertEqual([r['origin_stage'] for r in rows],['repair','repair','development','development'])
            self.assertEqual(len(rows[0]['cached_parent_reference']),6)
            self.assertEqual(set(rows[2]['cached_parent_reference']),{'bundle'})
            self.assertEqual(rows[3]['compact']['branches'][e.PRIMARY]['marker'],'new404')
            self.assertTrue(all(len(r['compact']['branches'])==11 for r in rows));f['execute'].assert_not_called()

    def test_parent_raw_binding_mismatch_rejected(self):
        with saved_collector_fixture() as f:
            before=e.read(f['build']/'preservation/before.json');next(iter(before['known_raw_bindings_by_path'].values()))['raw_sha256']='0'*64
            read=e.read
            def altered(path):return before if Path(path).name=='before.json' else read(path)
            with patch.object(e,'read',side_effect=altered):
                with self.assertRaises(AssertionError):e.stage_cases('repair')
            f['execute'].assert_not_called()

    def test_known_origin_manifest_mismatch_rejected(self):
        with saved_collector_fixture() as f:
            manifest=e.read(f['build']/'input-manifest.json');manifest['entries'][2]['origin_stage']='repair';read=e.read
            def altered(path):return manifest if Path(path).name=='input-manifest.json' else read(path)
            with patch.object(e,'read',side_effect=altered):
                with self.assertRaises(AssertionError):e.stage_cases('repair')

    def test_cached_environment_identity_mismatch_rejected(self):
        with saved_collector_fixture() as f:
            original=e.foundation.parent.compact
            def altered(replay):
                if len(replay['arms'])==10:replay['environment_sha256']='wrong'
                return original(replay)
            with patch.object(e.foundation.parent,'compact',side_effect=altered):
                with self.assertRaises(AssertionError):e.stage_cases('repair')


class IndependentAccountantTests(unittest.TestCase):
    def test_accountant_imports_only_standard_library(self):
        import ast
        module=accountant();tree=ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        imports=[n for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))]
        names=[a.name.split('.')[0] for n in imports if isinstance(n,ast.Import) for a in n.names]
        names += [n.module.split('.')[0] for n in imports if isinstance(n,ast.ImportFrom)]
        self.assertLessEqual(set(names),{'__future__','argparse','collections','datetime','gzip','hashlib','json','pathlib','random'})

    def test_accountant_lex_assessment_matches_new_goal_not_legacy_gates(self):
        f=accountant();m=metrics();m[e.PRIMARY]=metric(3,999999)
        self.assertEqual(f.independent_assessment(m,{},cis(-.1,999),'validation'),e.assess(m,{},cis(-.1,999),'validation'))
        origins={o:metrics() for o in ('repair','development')}
        self.assertEqual(f.independent_assessment(m,origins,cis(),'repair'),e.assess(m,origins,cis(),'repair'))

    def test_accountant_bootstrap_uses_patient_weighted_scenario_clusters(self):
        f=accountant()
        def p(pid,failed,seconds):return dict(id=pid,failed=failed,selection_seconds=seconds)
        rows=[dict(branches={e.PRIMARY:dict(patients=[p('a',False,20)]),e.BUDGET:dict(patients=[p('a',True,None)])}),
            dict(branches={e.PRIMARY:dict(patients=[p('b',True,None),p('c',False,90)]),e.BUDGET:dict(patients=[p('b',False,80),p('c',False,100)])})]
        actual=f.independent_ci(rows,e.BUDGET);self.assertEqual(actual,e.paired_lex_ci(rows,e.BUDGET))
        self.assertEqual(actual['failed_count_delta'],0);self.assertEqual(actual['wait_sum_delta_seconds'],-70)
        self.assertEqual(actual['ci95_failed_per_patient'],[-1.0,.5])
        self.assertEqual(actual['confidence_scope'],'marginal endpoint-wise95; not joint or familywise-adjusted')

    def test_accountant_cache_specs_bind_both_native_parent_layouts(self):
        f=accountant();ref={'file':'x','cached_parent_reference':{k:{'file':k} for k in ('reselect','tail','joint','horizon','residual')}}
        specs=f.expected_cache_specs('repair',ref)
        self.assertEqual(len(specs),6);self.assertEqual(sum(len(s[3]) for s in specs),10)
        bundle=f.expected_cache_specs('development',ref)
        self.assertEqual(len(bundle),1);self.assertEqual(len(bundle[0][3]),10)

    def test_accountant_raw_reader_verifies_compressed_and_content_hashes(self):
        f=accountant()
        with tempfile.TemporaryDirectory() as tmp,patch.object(f,'ROOT',Path(tmp)):
            body=f.canonical({'synthetic':True});packed=gzip.compress(body,mtime=0);Path(tmp,'x.gz').write_bytes(packed)
            metadata=dict(raw_sha256=f.sha(body),compressed_sha256=f.sha(packed))
            self.assertEqual(f.read_saved('x.gz',metadata)[0],{'synthetic':True})
            for key in metadata:
                changed=dict(metadata);changed[key]='0'*64
                with self.subTest(key=key),self.assertRaises(AssertionError):f.read_saved('x.gz',changed)

    def test_accountant_metric_arithmetic_and_identity_exchanges_match_pure_reports(self):
        f=accountant();rows=[]
        for index in range(2):
            branches={}
            for name in f.NAMES:
                ps=[dict(id='a',failed=False,selection_seconds=20+index,estimated_arrival_seconds=40+index,inquiry_count=2),
                    dict(id='b',failed=True,selection_seconds=None,estimated_arrival_seconds=None,inquiry_count=3)]
                if name==e.PRIMARY:
                    ps[0].update(failed=True,selection_seconds=None,estimated_arrival_seconds=None)
                    ps[1].update(failed=False,selection_seconds=80+index,estimated_arrival_seconds=100+index)
                branches[name]=dict(patients=ps,review_work_seconds=31+index,cancelled_before_start_count=index)
            rows.append(dict(kind='mix',seed=index,branches=branches,
                oracle=dict(arrival_seconds={'a':10,'b':None},exact=index==0)))
        self.assertEqual(f.scope_metrics(rows),e.scope_metrics(rows,f.NAMES))
        actual,exchanges=f.independent_pair(rows,e.BUDGET)
        self.assertEqual(actual,e.foundation.parent.paired(rows,e.BUDGET,after=e.PRIMARY,bootstrap=False))
        self.assertEqual(actual['lost_selections'],2);self.assertEqual(actual['new_selections'],2)
        self.assertEqual(len(exchanges),4)

    def _priority_group(self):
        members=[dict(patient_id=pid,workflow_snapshot=dict(apply_snapshot=dict(patient_version=3,revision=9,now=100)))
            for pid in ('a','b','c')]
        priority=dict(schema_version=1,now=100,event_prefix_max=7,decision_prefix_max=2,revision=9,
            source='captured current patient priority fields with common snapshot provenance',
            patients=[dict(patient_id=m['patient_id'],patient_version=3,reported_priority=None,priority_source='unknown',effective_priority=4) for m in members])
        return dict(now=100,event_prefix_max=7,decision_prefix_max=2,revision=9,members=members[:2],tail_members=members[2:],
            priority_context=priority,evidence=dict(priority_context=deepcopy(priority)))

    def test_accountant_checks_default4_priority_values_order_versions_and_common_prefix(self):
        f=accountant();f.verify_priority_group(self._priority_group())
        mutations={
            'tier':lambda g:g['priority_context']['patients'][2].update(effective_priority=2),
            'reported':lambda g:g['priority_context']['patients'][0].update(reported_priority=4),
            'source':lambda g:g['priority_context']['patients'][0].update(priority_source='expert'),
            'version':lambda g:g['priority_context']['patients'][1].update(patient_version=4),
            'prefix':lambda g:g['priority_context'].update(event_prefix_max=8),
            'revision':lambda g:g['priority_context'].update(revision=10),
            'provenance':lambda g:g['priority_context'].update(source='oracle'),
            'extra':lambda g:g['priority_context']['patients'][0].update(hidden_service=7),
            'order':lambda g:g['priority_context']['patients'].reverse(),
            'snapshot':lambda g:g['tail_members'][0]['workflow_snapshot']['apply_snapshot'].update(revision=10)}
        for label,mutate in mutations.items():
            group=self._priority_group();mutate(group)
            group['evidence']['priority_context']=deepcopy(group['priority_context'])
            with self.subTest(label=label),self.assertRaises(AssertionError):f.verify_priority_group(group)

    def test_accountant_rejects_priority_evidence_not_equal_to_actual_group_capture(self):
        f=accountant();group=self._priority_group();group['evidence']['priority_context']['patients'][0]['effective_priority']=2
        with self.assertRaises(AssertionError):f.verify_priority_group(group)


if __name__=='__main__':unittest.main()
