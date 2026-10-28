"""Source binding, once-only outputs, and failed-raw retention contracts."""
from copy import deepcopy
import gzip
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import arrival_guard_priority_experiment_20261028 as e


class ArrivalGuardExperimentTests(unittest.TestCase):
    def test_preregistered_protocol_and_parent_bindings(self):
        value=e.protocol();bindings=e.parent_bindings(value)
        self.assertEqual(value['candidate'],'G2')
        self.assertIn(e.PARENT_PATHS['validation_report_sha256'],bindings)
        self.assertEqual(bindings[e.PARENT_PATHS['manifest_sha256']],value['parents']['manifest_sha256'])

    def test_regression_entries_reuse_exact_previous_inputs(self):
        original=e.read(e.under(e.PARENT_PATHS['manifest_sha256']))['entries']['validation']
        rows=e.entries_for('regression_original');self.assertEqual(len(rows),345)
        for before,after in zip(original,rows):
            self.assertEqual(after['phase'],'regression_original');self.assertEqual(after['original_phase'],'validation')
            for key in ('case_id','track','stratum','index','seed','patient_count','scenario_sha256','execution_order'):
                self.assertEqual(before[key],after[key])

    def test_second_regression_reuses_G1_validation_inputs_without_pooling(self):
        original=e.read(e.under('.codex-build/arrival-guard-20261028/manifest.json'))['entries']['validation']
        rows=e.entries_for('regression_g1validation');self.assertEqual(len(rows),345)
        for before,after in zip(original,rows):
            self.assertEqual(after['phase'],'regression_g1validation')
            self.assertEqual(after['known_reference'],'g1validation')
            for key in ('case_id','track','stratum','seed','patient_count','scenario_sha256','execution_order'):
                self.assertEqual(before[key],after[key])
        first=e.entries_for('regression_original')
        self.assertFalse({r['seed'] for r in first}&{r['seed'] for r in rows})

    def test_only_both_separately_verified_known_cohorts_unlock(self):
        parent=e.ROOT/'.codex-build/arrival-guard-priority-20261028/tmp';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            base=Path(tmp);manifest=dict(entries={phase:[None]*345 for phase in e.REGRESSION_PHASES})
            with patch.object(e,'BUILD',base/'build'),patch.object(e,'RESULTS',base/'results'):
                e.write_json(e.BUILD/'manifest.json',manifest);e.write_json(e.BUILD/'source-freeze.json',{'fixture':True})
                self.assertIsNone(e._maybe_unlock())
                for phase in e.REGRESSION_PHASES:
                    e.write_json(e.report_path(phase),dict(technical_passed=True,cases=[None]*345,analysis={'goals':{'passed':True}}))
                    receipt=dict(passed=True,phase=phase,all_regression_goals_passed=True,
                        manifest_sha256=e.digest(e.BUILD/'manifest.json'),source_freeze_sha256=e.digest(e.BUILD/'source-freeze.json'),
                        report_sha256=e.digest(e.report_path(phase)))
                    e.write_json(e.BUILD/(phase+'-verification.json'),receipt)
                    if phase==e.REGRESSION_PHASES[0]:
                        self.assertIsNone(e._maybe_unlock());self.assertFalse((e.BUILD/'validation-lock.json').exists())
                lock=e._maybe_unlock();self.assertTrue(lock['passed']);e._validation_unlocked(manifest)
                bad=e.read(e.report_path(e.REGRESSION_PHASES[1]));bad['analysis']['goals']['passed']=False
                with patch.object(e,'read',side_effect=lambda p:bad if Path(p)==e.report_path(e.REGRESSION_PHASES[1]) else __import__('json').loads(Path(p).read_bytes())):
                    with self.assertRaises(AssertionError):e._validation_unlocked(manifest)

    def test_seed_derivation_and_disjoint_namespace(self):
        name='arrival_guard_priority_20261028_validation_v1'
        expected=2**256+int(hashlib.sha256((name+'|surge|7').encode()).hexdigest(),16)
        self.assertEqual(e.fresh_seed(name,'surge',7),expected)
        self.assertNotEqual(expected,e.fresh_seed(name,'surge',8))
        self.assertNotEqual(expected,e.fresh_seed(name,'routine_single',7))

    def test_source_closure_includes_all_new_tests_and_parent_sources(self):
        files=e.source_files()
        self.assertTrue(set(e.START_FILES)<=set(files))
        self.assertIn('test_arrival_guard_priority_integration_20261028.py',files)
        self.assertIn('simulation/final_assignment_analysis_20261028.py',files)
        self.assertIn(e.PROTOCOL_REL,files);self.assertIn(e.PLAN_REL,files)

    def test_rejected_validation_lock_prevents_any_model_execution(self):
        manifest={'entries':{'regression':[None]*345}}
        lock=dict(passed=True,all_regression_goals_passed=False)
        with patch.object(e,'read',return_value=lock):
            with self.assertRaises(AssertionError):e._validation_unlocked(manifest)

    def test_execution_failure_saves_gzip_capsule_before_failing_no_retry(self):
        parent=e.ROOT/'.codex-build/arrival-guard-priority-20261028/tmp';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            base=Path(tmp);scenario={'patients':[{'id':'A'}]};fingerprint=e.sha(scenario)
            entry=dict(case_id='fixture',phase='regression_original',track='main',stratum='surge',seed=1,patient_count=1,scenario_sha256=fingerprint)
            manifest=dict(entries={'regression_original':[entry]})
            with patch.object(e,'BUILD',base/'build'),patch.object(e,'RESULTS',base/'results'),patch.object(e,'frozen',return_value=manifest),\
                patch.object(e,'digest',return_value='a'*64),patch.object(e,'regenerate',return_value=scenario),\
                patch.object(e.engine,'run_comparison',side_effect=RuntimeError('fixture engine failure')) as run:
                with self.assertRaisesRegex(AssertionError,'capsule saved'):e.execute('regression_original')
                run.assert_called_once()
                failure=e.read(base/'build/regression_original-failure.json')
                self.assertFalse(failure['technical_passed']);self.assertFalse(failure['rerun_allowed'])
                self.assertEqual(len(failure['stored_raws']),1)
                packed=(base/'build/regression_original-raw/fixture.json.gz').read_bytes()
                body=gzip.decompress(packed)
                self.assertIn(b'fixture engine failure',body)
                self.assertEqual(hashlib.sha256(body).hexdigest(),failure['stored_raws'][0]['raw_sha256'])
                with self.assertRaisesRegex(AssertionError,'cannot rerun'):e.execute('regression_original')
                self.assertEqual(run.call_count,1)

    def test_exclusive_json_write_cannot_overwrite(self):
        parent=e.ROOT/'.codex-build/arrival-guard-priority-20261028/tmp';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            path=Path(tmp)/'evidence.json';e.write_json(path,{'original':1})
            with self.assertRaises(FileExistsError):e.write_json(path,{'replacement':2})
            self.assertEqual(e.read(path),{'original':1})

    def test_workspace_escape_rejected(self):
        with self.assertRaises(ValueError):e.under('../outside.json')
        with self.assertRaises(ValueError):e.under(str(e.ROOT/'absolute.json'))


if __name__=='__main__':unittest.main()
