"""Pure screen and receipt contracts; no real producer/auditor execution."""
from copy import deepcopy
import gzip
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
from inquiry_lab import diverse_diagnostic as d

def patient(pid='P',selection=100,travel=60):
    return dict(id=pid,created_at=250,failed=selection is None,selection_seconds=selection,
        estimated_arrival_seconds=None if selection is None else selection+travel,
        travel_seconds=None if selection is None else travel,destination=None if selection is None else 'H',inquiry_count=1,contacted=['H'])

def entry(i):
    return dict(source_index=i,seed=i,kind='mix',family='synthetic',scenario_id='unit',parent_stage='repair' if i<240 else 'development',
        original_origin=None,snapshot_sha256='snapshot',environment_sha256='environment',baseline_branches={n:dict(patients=[patient()]) for n in d.parent.BASELINES})

def replay(arm,e):
    name=d.ARMS[arm]['name'];scenario=dict(id='unit',family='synthetic',seed=e['seed'],patients=[dict(id='P')]);e['input_sha256']=d.sha(d.raw(scenario))
    return dict(scenario=scenario,arms=[[name,'immediate']],initial_snapshot_sha256='snapshot',environment_sha256='environment',branches=[dict(
        policy=name,rule='immediate',snapshot_sha256='snapshot',environment_sha256='environment',patients=[patient(selection=95)],policy_calls=[])])

def cases(arm):
    result=[]
    for i in d.SCREEN:
        e=entry(i);r=replay(arm,e)
        result.append(d.output_case(arm,dict(file=f'{i:03d}_{i}.json.gz',source_index=i,raw_folder=arm+'-screen-cases',input_sha256=e['input_sha256'],raw_sha256='raw',compressed_sha256='packed'),e,r))
    return result

class DiverseDiagnosticTests(unittest.TestCase):
    def temporary(self):return tempfile.TemporaryDirectory(dir=d.BUILD/'tmp')

    def test_fixed_twelve_subset_with_six_per_slice_and_no_heavy_families(self):
        self.assertEqual(len(d.SCREEN),12);self.assertEqual(len(set(d.SCREEN)),12);self.assertLessEqual(set(d.SCREEN),set(d.INITIAL))
        self.assertEqual(sum(i<240 for i in d.SCREEN),6);self.assertNotIn(99,d.SCREEN);self.assertNotIn(339,d.SCREEN)

    def test_separate_candidate_numeric_globals_and_twenty_seven_checks(self):
        old=d.parent.PRIMARY
        for arm in d.ARMS:
            v=d.numeric(arm).early_gate(cases(arm));self.assertTrue(v['passed']);self.assertEqual(len(v['checks']),27)
            self.assertEqual(v['scopes']['repair']['tailwait']['after'],d.ARMS[arm]['name']+'|immediate')
        self.assertEqual(d.parent.PRIMARY,old)

    def test_exact_prefixed_counters_not_silently_zeroed(self):
        v=d.numeric_counters([dict(branch_audits=[dict(wavecap_immediate_send_changes=8,wavecap_plan_changes=368,passed=True)])])
        self.assertEqual(v,dict(wavecap_immediate_send_changes=8,wavecap_plan_changes=368));self.assertNotIn('immediate_send_changes',v)

    def test_screen_semantics_explicitly_unverified(self):
        c=cases('swap')[0];self.assertIsNone(c['audit']);self.assertFalse(c['semantic_verified']);self.assertTrue(c['output_invariants']['passed'])
        self.assertIn('not FIFO',c['output_invariants']['scope'])

    def test_empty_missing_duplicate_and_failure_caps_not_weakened(self):
        n=d.numeric('swap');self.assertFalse(n.early_gate([])['passed']);c=cases('swap');self.assertFalse(n.early_gate(c[:6])['passed'])
        self.assertFalse(d.incremental_research_gate(n.early_gate([]))['passed'])
        with self.assertRaises(AssertionError):n.early_gate([c[0],c[0]])
        for b in d.parent.BASELINES:c[0]['compact']['branches'][b]['patients']=[patient('A'),patient('B',None)]
        c[0]['compact']['branches'][d.ARMS['swap']['name']+'|immediate']['patients']=[patient('A',None),patient('B',90)]
        v=n.comparison([c[0]],d.parent.TAILWAIT);self.assertEqual(v['maximum_individual_wait_increase_seconds'],200)
        self.assertEqual(v['maximum_individual_arrival_cost_increase_seconds'],1640);self.assertEqual(v['lost_selections'],1);self.assertFalse(n.early_gate(c)['passed'])
        self.assertFalse(d.incremental_research_gate(n.early_gate(c))['passed'])

    def test_inherited_pairshield_failure_can_characterize_harmless_one_origin_gain(self):
        c=cases('releasealign');primary=d.ARMS['releasealign']['name']+'|immediate'
        for row in c:
            row['compact']['branches'][d.parent.PAIRSHIELD]['patients']=[patient(travel=0)]
            if row['compact']['parent_stage']=='development':row['compact']['branches'][primary]['patients']=[patient()]
        legacy=d.numeric('releasealign').early_gate(c);incremental=d.incremental_research_gate(legacy)
        self.assertFalse(legacy['passed']);self.assertTrue(incremental['passed']);self.assertEqual(len(incremental['checks']),14)
        self.assertFalse(legacy['checks']['development_wait_strictly_smaller_than_tailwait'])
        self.assertFalse(incremental['legacy_goals_replaced']);self.assertFalse(incremental['fresh_validation_allowed'])
        self.assertFalse(incremental['operational_adoption'])

    def test_incremental_rejects_new_personal_wait_or_all_patient_arrival_harm(self):
        primary=d.ARMS['swap']['name']+'|immediate'
        for changed in (patient(selection=101),patient(selection=95,travel=300)):
            c=cases('swap');c[0]['compact']['branches'][primary]['patients']=[changed]
            self.assertFalse(d.incremental_research_gate(d.numeric('swap').early_gate(c))['passed'])
        c=cases('swap')
        for b in d.parent.BASELINES:c[0]['compact']['branches'][b]['patients']=[patient(selection=None)]
        c[0]['compact']['branches'][primary]['patients']=[patient(selection=300,travel=1700)]
        legacy=d.numeric('swap').early_gate(c);incremental=d.incremental_research_gate(legacy)
        self.assertEqual(legacy['scopes']['repair']['tailwait']['lost_selections'],0)
        self.assertFalse(incremental['checks']['repair_every_patient_arrival_cost_not_greater_than_tailwait'])
        self.assertFalse(incremental['passed'])

    def test_incremental_still_requires_a_strict_origin_gain(self):
        c=cases('swap');primary=d.ARMS['swap']['name']+'|immediate'
        for row in c:row['compact']['branches'][primary]['patients']=[patient()]
        v=d.incremental_research_gate(d.numeric('swap').early_gate(c))
        self.assertFalse(v['checks']['at_least_one_origin_strict_wait_improvement_vs_tailwait']);self.assertFalse(v['passed'])

    def test_per_slice_personal_wait_and_both_selected_arrival_guard(self):
        c=cases('swap');primary=d.ARMS['swap']['name']+'|immediate';c[0]['compact']['branches'][primary]['patients']=[patient(selection=101)]
        self.assertFalse(d.numeric('swap').early_gate(c)['checks']['repair_individual_wait_not_worse_than_tailwait'])
        c=cases('swap');c[0]['compact']['branches'][primary]['patients']=[patient(selection=50,travel=300)]
        self.assertFalse(d.numeric('swap').early_gate(c)['checks']['repair_both_selected_arrival_not_worse_than_tailwait'])

    def test_output_contact_duplicates_and_captured_concurrency_rejected(self):
        e=entry(0);r=replay('swap',e);r['branches'][0]['patients'][0].update(inquiry_count=2,contacted=['H','H'])
        with self.assertRaises(AssertionError):d.output_case('swap',{},e,r)
        e=entry(0);r=replay('swap',e);r['branches'][0]['policy_calls']=[dict(name=d.ARMS['swap']['name'],inputs=dict(now=200,max_new=2,
            patient_pending=[dict(submitted_at=200) for _ in range(4)],candidates=[dict(hospital_id='H')]),output=dict(send=['H']))]
        with self.assertRaises(AssertionError):d.output_case('swap',{},e,r)

    def test_screen_read_never_imports_an_auditor(self):
        with self.temporary() as directory:
            b=Path(directory);(b/'swap-screen-cases').mkdir();e=entry(0);r=replay('swap',e);body=d.raw(r);packed=gzip.compress(body,mtime=0)
            (b/'swap-screen-cases/000_0.json.gz').write_bytes(packed)
            item=dict(file='000_0.json.gz',raw_folder='swap-screen-cases',source_index=0,input_sha256=e['input_sha256'],raw_sha256=d.sha(body),compressed_sha256=d.sha(packed))
            with patch.object(d,'BUILD',b),patch.object(d.importlib,'import_module',side_effect=AssertionError('audit reached')) as module:
                c=d.read_candidate(('swap','screen',item,e,{},False));module.assert_not_called();self.assertIsNone(c['audit'])

    def test_run_task_is_single_arm_not_combined_change(self):
        f=Mock(return_value={})
        with patch.object(d.importlib,'import_module',return_value=types.SimpleNamespace(run_task=f)):d.run_candidate(('swap',entry(0)))
        self.assertEqual(f.call_args.args[0][2],((d.ARMS['swap']['name'],'immediate'),))

    def test_failed_screen_blocks_before_receipt_pool_or_started(self):
        with self.temporary() as directory:
            b=Path(directory);(b/'tmp').mkdir();c=cases('swap')
            c[0]['compact']['branches'][d.ARMS['swap']['name']+'|immediate']['patients']=[patient(selection=101)]
            legacy=d.numeric('swap').early_gate(c)
            d.write(b/'swap-screen-report.json',dict(cases=c,early_gate=legacy,incremental_research_gate=d.incremental_research_gate(legacy)))
            with patch.object(d,'BUILD',b),patch.dict(os.environ,{'TEMP':str(b/'tmp'),'TMP':str(b/'tmp')}),patch.object(d,'frozen',return_value={}), \
                 patch.object(d,'ProcessPoolExecutor',side_effect=AssertionError('pool reached')) as pool:
                with self.assertRaises(AssertionError):d.execute('swap','expand')
                pool.assert_not_called()
            self.assertFalse((b/'swap-initial24-started.json').exists());self.assertFalse((b/'swap-initial24-cases').exists())

    def test_changed_report_cannot_match_independent_receipt(self):
        with self.temporary() as directory:
            b=Path(directory);d.write(b/'source-freeze.json',{});p=dict(source_sha256={},independent_checker_sha256='checker')
            r=dict(candidate=d.ARMS['swap']['name'],phase='screen',cases=cases('swap'),source_sha256={},source_freeze_sha256=d.digest(b/'source-freeze.json'))
            r['early_gate']=d.numeric('swap').early_gate(r['cases']);r['incremental_research_gate']=d.incremental_research_gate(r['early_gate'])
            d.write(b/'swap-screen-report.json',r)
            d.write(b/'swap-screen-independent.json',dict(passed=True,candidate=r['candidate'],phase='screen',checker_sha256='checker',cases_checked=12,report_sha256='wrong'))
            with patch.object(d,'BUILD',b):
                with self.assertRaises(AssertionError):d.verify_prior('swap','screen',p)

    def test_no_full_historical_promotion_claim_on_screen_subset(self):
        v=d.numeric('swap').legacy_assessments(cases('swap'),dict(old_full_goals={'unchanged':True}))
        self.assertFalse(v['evaluated']);self.assertEqual(v['goals'],{'unchanged':True})

if __name__=='__main__':unittest.main()
