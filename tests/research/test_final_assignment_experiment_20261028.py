"""Driver safety/contracts with tiny recorded fixtures; no experiment is run."""
from copy import deepcopy
from contextlib import ExitStack, redirect_stdout
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from simulation import final_assignment_experiment_20261028 as driver
from test_final_assignment_audit_20261028 import fixture


def entry(value=None):
    value=fixture() if value is None else value
    return dict(case_id='main_routine_stream_000',phase='development',track='main',
        stratum='routine_stream',index=0,seed=2**256+1,patient_count=2,
        scenario_sha256=driver.sha(value['original_scenario']),execution_order=['R0','R1','H1'])


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=driver.BUILD/'tmp')
        self.addCleanup(self.temp.cleanup)
        self.local=Path(self.temp.name)
        self.build=self.local/'build';self.build.mkdir()
        self.results=self.local/'reports';self.results.mkdir()
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(driver,'BUILD',self.build))
        self.stack.enter_context(patch.object(driver,'RESULTS',self.results))

    def bound(self):
        value=fixture();e=entry(value)
        manifest=dict(source_sha256={'unit':'hash'},runtime_versions={'python':'unit'},
            protocol_sha256='protocol',protocol=dict(inference=dict(bootstrap_samples=5000)),
            entries={'development':[e],'validation':[dict(e,phase='validation')]})
        driver.write_json(self.build/'manifest.json',{'unit':'manifest'})
        driver.write_json(self.build/'source-freeze.json',{'unit':'freeze'})
        self.stack.enter_context(patch.object(driver,'frozen',return_value=manifest))
        self.stack.enter_context(patch.object(driver,'regenerate',return_value=value['original_scenario']))
        self.stack.enter_context(patch.object(driver,'_loaded_sources_inside_freeze'))
        fake=SimpleNamespace(analyze=Mock(return_value=dict(groups=[],cases=1)))
        self.stack.enter_context(patch.object(driver.importlib,'import_module',return_value=fake))
        run=self.stack.enter_context(patch.object(driver.engine,'run_final_assignment_replay',return_value=value))
        return value,e,manifest,run

    def store_raw(self,value=None):
        value=fixture() if value is None else value;e=entry(value)
        folder=self.build/'raw';folder.mkdir()
        path=folder/(e['case_id']+'.json.gz');body=driver.canonical(value);packed=gzip.compress(body,mtime=0)
        path.write_bytes(packed)
        item=dict(case_id=e['case_id'],scenario_sha256=e['scenario_sha256'],
            raw_file=path.relative_to(driver.ROOT).as_posix(),raw_sha256=hashlib.sha256(body).hexdigest(),
            compressed_sha256=hashlib.sha256(packed).hexdigest())
        return value,e,item,folder,path

    def test_source_import_closure_contains_runtime_and_old_pure_auditors(self):
        files=driver.source_files()
        for path in ('simulation/sim.py','simulation/heuristic_assignment.py',
            'simulation/online_allocation_audit.py','simulation/h1_online_audit.py',
            driver.PROTOCOL_REL,driver.PLAN_REL):self.assertIn(path,files)
        self.assertEqual(len(files),len(set(files)))
        self.assertNotIn('server/app.py',files)

    def test_source_closure_and_runtime_do_not_import_ortools(self):
        value=driver.runtime_versions();self.assertIsNone(value['ortools'])
        self.assertFalse(value['ortools_used'])

    def test_protocol_is_current_engine_and_generator_without_outcome_selection(self):
        value=driver.protocol()
        self.assertEqual(value['policies'],list(driver.engine.DEFAULT_FINAL_ASSIGNMENT_POLICIES))
        self.assertEqual(value['distribution'],driver.generator.DISTRIBUTION)
        self.assertFalse(value['inference']['validation_results_may_change_parameters'])

    def test_exclusive_json_write_refuses_existing_evidence(self):
        path=self.build/'once.json';driver.write_json(path,{'a':1})
        with self.assertRaises(FileExistsError):driver.write_json(path,{'a':2})
        self.assertEqual(driver.read(path),{'a':1})

    def test_workspace_traversal_or_absolute_binding_is_rejected(self):
        for name in ('../../outside.json',str((self.build/'outside').resolve())):
            with self.assertRaises(ValueError):driver.under(name)

    def test_phase_seed_partitions_and_orders_are_fixed_before_outcomes(self):
        proto=deepcopy(driver.protocol())
        for phase in ('development','validation'):
            proto[phase]=dict(main_seeds_per_stratum=1,stress_seeds=1,main_cases=3,stress_cases=2)
        def main(stratum,seed):return dict(family=stratum,seed=seed,patients=[dict(id='P')])
        def stress(seed):return [dict(family=f,seed=seed,patients=[dict(id='P')]) for f in ('s1','s2')]
        with patch.object(driver.generator,'build_case',side_effect=main),patch.object(driver.generator,'build_stress_cases',side_effect=stress):
            dev=driver.entries_for('development',proto);val=driver.entries_for('validation',proto)
        self.assertEqual(len(dev),5)
        self.assertFalse({r['seed'] for r in dev}&{r['seed'] for r in val})
        self.assertTrue(all(r['seed']>=2**256 for r in dev+val))
        self.assertEqual(dev[1]['execution_order'],['R1','H1','R0'])
        self.assertTrue(all(len(row['case_id'])<80 for row in dev))

    def test_prepare_requires_final_source_test_hashes_and_no_failures(self):
        source={'unit':'source'};checks=self.local/'checks.json'
        driver.write_json(checks,dict(passed=True,failures=0,errors=0,skipped=0,
            source_sha256_before=source,source_sha256_after={'changed':'source'}))
        with patch.object(driver,'source_hashes',return_value=source):
            with self.assertRaises(AssertionError):driver.prepare(checks)
        self.assertFalse((self.build/'manifest.json').exists())

    def test_prepare_writes_both_input_partitions_before_any_execution(self):
        source={'unit':'source'};checks=self.local/'checks.json'
        driver.write_json(checks,dict(passed=True,failures=0,errors=0,skipped=0,
            source_sha256_before=source,source_sha256_after=source))
        fake=lambda phase,value:[dict(case_id=phase,seed=2**256+(1 if phase=='development' else 2))]
        with patch.object(driver,'source_hashes',return_value=source),patch.object(driver,'entries_for',side_effect=fake):
            result=driver.prepare(checks)
        self.assertEqual(set(result['entries']),{'development','validation'})
        self.assertEqual(driver.read(self.build/'source-freeze.json')['candidate_branches_before_freeze'],0)
        self.assertFalse(driver._phase_paths('development')['started'].exists())
        with self.assertRaises(AssertionError):driver.prepare(checks)

    def test_raw_bytes_and_original_input_are_reaudited(self):
        value,e,item,folder,path=self.store_raw()
        with patch.object(driver,'regenerate',return_value=value['original_scenario']):
            result=driver._read_raw(e,item,folder)
        self.assertTrue(result['audit']['passed'])
        self.assertEqual(result['audit']['branch_metrics']['H1']['unassigned'],0)
        self.assertEqual(result['audit']['paired']['R1']['maximum_common_arrival_delay_minutes'],3)

    def test_real_analysis_schema_renders_metrics_intervals_and_runtime(self):
        value,e,item,folder,path=self.store_raw()
        with patch.object(driver,'regenerate',return_value=value['original_scenario']):
            case=driver._read_raw(e,item,folder)
        from simulation import final_assignment_analysis_20261028 as analysis
        report=dict(phase='unit',cases=[case],analysis=analysis.analyze([case],bootstrap_samples=30),
            runtime_diagnostics=driver.runtime_diagnostics([case]))
        rendered=driver.markdown(report)
        self.assertIn('routine_stream',rendered)
        self.assertIn('H1',rendered)
        self.assertIn('p95(ms)',rendered)
        self.assertEqual(report['runtime_diagnostics']['groups'][0]['policies']['R1']['decisions'],2)
        self.assertEqual(report['runtime_diagnostics']['groups'][0]['policies']['R1']['wall_max_ms'],1)

    def test_compressed_raw_or_input_hash_tamper_is_rejected(self):
        value,e,item,folder,path=self.store_raw();path.write_bytes(path.read_bytes()+b'x')
        with self.assertRaises(AssertionError):driver._read_raw(e,item,folder)

    def test_precomputed_attempt_exhaustion_is_a_technical_failure(self):
        value=fixture();value['branches'][0]['events'].append(dict(type='ask',t=1,attempt=65))
        value,e,item,folder,path=self.store_raw(value)
        with patch.object(driver,'regenerate',return_value=value['original_scenario']):
            with self.assertRaisesRegex(AssertionError,'precomputed'):
                driver._read_raw(e,item,folder)

    def test_engine_failure_capsule_is_saved_and_not_selectively_retried(self):
        value,e,manifest,run=self.bound();run.side_effect=RuntimeError('unit-engine-failure')
        with self.assertRaisesRegex(AssertionError,'capsule'):
            driver.execute('development')
        paths=driver._phase_paths('development')
        raws=list(paths['raw'].iterdir());self.assertEqual(len(raws),1)
        saved=json.loads(gzip.decompress(raws[0].read_bytes()))
        self.assertIn('unit-engine-failure',saved['execution_error'])
        failure=driver.read(paths['failure']);self.assertFalse(failure['rerun_allowed'])
        self.assertFalse(paths['report'].exists())
        with self.assertRaises(AssertionError):driver.execute('development')
        self.assertEqual(run.call_count,1)

    def test_complete_replay_is_stored_before_raw_audit_failure(self):
        value,e,manifest,run=self.bound()
        with patch.object(driver,'_read_raw',side_effect=ValueError('unit-audit-failure')):
            with self.assertRaisesRegex(ValueError,'unit-audit-failure'):driver.execute('development')
        path=next(driver._phase_paths('development')['raw'].iterdir())
        self.assertEqual(json.loads(gzip.decompress(path.read_bytes())),value)
        self.assertEqual(run.call_count,1)

    def test_development_unlocks_validation_on_technical_success_only(self):
        value,e,manifest,run=self.bound()
        with redirect_stdout(io.StringIO()):report=driver.execute('development')
        self.assertTrue(report['technical_passed'])
        lock=driver.read(self.build/'validation-lock.json')
        self.assertFalse(lock['development_efficacy_is_unlock_condition'])
        self.assertFalse(lock['parameters_retuned_after_development'])
        self.assertEqual(lock['development_report_sha256'],driver.digest(driver.report_path('development')))

    def test_validation_refuses_missing_or_changed_development_lock(self):
        self.bound()
        with self.assertRaises(FileNotFoundError):driver.execute('validation')
        driver.write_json(self.build/'validation-lock.json',dict(passed=False))
        with self.assertRaises(AssertionError):driver.execute('validation')
        self.assertFalse(driver._phase_paths('validation')['started'].exists())

    def test_validation_requires_stored_development_raw_verification(self):
        self.bound()
        with redirect_stdout(io.StringIO()):driver.execute('development')
        with self.assertRaises(FileNotFoundError):driver.execute('validation')
        self.assertFalse(driver._phase_paths('validation')['started'].exists())

    def test_stored_raw_verification_does_not_execute_any_model(self):
        value,e,manifest,run=self.bound()
        with redirect_stdout(io.StringIO()):driver.execute('development')
        calls=run.call_count;run.side_effect=AssertionError('verification must never execute engine')
        result=driver.verify('development')
        self.assertTrue(result['passed']);self.assertEqual(result['new_policy_or_model_executions'],0)
        self.assertTrue(result['statistical_reproduction_same_implementation'])
        self.assertFalse(result['independent_statistical_mathematics_verified'])
        self.assertEqual(run.call_count,calls)
        with self.assertRaises(AssertionError):driver.verify('development')

    def test_verification_rejects_saved_metric_or_markdown_drift(self):
        self.bound()
        with redirect_stdout(io.StringIO()):driver.execute('development')
        path=driver.report_path('development').with_suffix('.md')
        path.write_text(path.read_text(encoding='utf-8')+'changed',encoding='utf-8')
        with self.assertRaises(AssertionError):driver.verify('development')
        self.assertFalse((self.build/'development-verification.json').exists())

    def test_argparse_requires_phase_for_verification(self):
        with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):driver.main(['verify'])


if __name__=='__main__':unittest.main(verbosity=2)
