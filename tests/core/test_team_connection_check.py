"""Report-runner guards; these fixtures are not HTTP integration evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import team_connection_check as check


class TeamConnectionCheckTests(unittest.TestCase):
    def setUp(self):
        parent = check.ROOT/'.codex-build/team-connection-check-20261030/runner-tests'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent, prefix='report-guard-')
        self.output = Path(self.tmp.name)/'new-run'

    def tearDown(self):
        self.tmp.cleanup()

    def suite(self, outcome='pass'):
        class ReportFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                if outcome == 'class_error':
                    raise RuntimeError('deliberate class setup fixture')

            def id(self):
                return 'test_team_connection_races.ReportFixture.test_contract'

            def runTest(self):
                if outcome == 'fail':
                    raise AssertionError('deliberate runner guard fixture')
                if outcome == 'skip':
                    raise unittest.SkipTest('deliberate runner guard skip')
        return unittest.TestSuite([ReportFixture()])

    def valid_evidence(self):
        health = {name:dict(state='RUNNING', enabled=True, thread_alive=True) for name in ('planning','inquiry')}
        return {check.TEST_MODULES[0]: {'test_team_connection_races.ReportFixture.test_contract': dict(
            requests=[dict(method='GET',path='/api/system/status',status=200)],
            checks=[{'name':'explicit dummy runner guard; not real HTTP evidence','pass':True}],
            workers_start=health, workers_stopped=True)}, check.TEST_MODULES[1]: {}}

    def run_fixture(self, outcome='pass', *, evidence=None):
        evidence = self.valid_evidence() if evidence is None else evidence
        # Dummy records exist only in report-unit fixtures, not integration proof.
        with patch.object(check, 'collect_evidence', return_value=evidence), \
                patch.object(check.team_runtime_profile, 'verify_profile',
                    return_value={'ok':True,'scope':'report_unit_fixture'}), patch.object(
                    check, 'load_suite', return_value=self.suite(outcome)):
            return check.run(self.output)

    def test_existing_output_is_untouched_and_profile_not_called(self):
        self.output.mkdir()
        sentinel = self.output/'keep.txt'
        sentinel.write_text('keep', encoding='utf-8')
        with patch.object(check.team_runtime_profile, 'verify_profile') as verify:
            with self.assertRaises(FileExistsError):
                check.run(self.output)
            verify.assert_not_called()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_invalid_profile_stops_before_any_output_or_database(self):
        with patch.object(check.team_runtime_profile, 'verify_profile',
                return_value={'ok':False,'issues':[{'code':'SOURCE_CHANGED'}]}):
            with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED'):
                check.run(self.output)
        self.assertFalse(self.output.exists())

    def test_success_records_protocol_snapshot_test_and_followup_contract(self):
        report = self.run_fixture()
        self.assertTrue(report['pass'])
        self.assertEqual(report['tests_run'], 1)
        self.assertEqual(report['test_results'][0]['status'], 'PASS')
        self.assertEqual(report['source_hashes'], report['snapshot_hashes'])
        protocol = json.loads((self.output/'protocol.json').read_text(encoding='utf-8'))
        self.assertEqual(protocol['modules'], list(check.TEST_MODULES))
        self.assertFalse(protocol['browser_offline_test'])
        self.assertFalse(protocol['clinical_effectiveness'])
        contract = json.loads((self.output/'connection-contract.json').read_text(encoding='utf-8'))
        self.assertTrue(any(row.get('error') == 'DELIVERY_REQUIRED' for row in contract))

    def test_failed_test_keeps_log_json_and_nonpassing_result(self):
        report = self.run_fixture('fail')
        self.assertFalse(report['pass'])
        self.assertEqual((report['failures'], report['errors']), (1, 0))
        self.assertEqual(report['test_results'][0]['status'], 'FAIL')
        self.assertIn('deliberate runner guard fixture', (self.output/'tests.log').read_text(encoding='utf-8'))
        self.assertFalse(json.loads((self.output/'report.json').read_text(encoding='utf-8'))['pass'])

    def test_skipped_test_is_not_a_successful_contract_check(self):
        report = self.run_fixture('skip')
        self.assertFalse(report['pass'])
        self.assertEqual(report['skipped'], 1)
        self.assertEqual(report['test_results'][0]['status'], 'SKIP')

    def test_source_drift_invalidates_an_otherwise_successful_run(self):
        before = check.fingerprints()
        after = {**before, 'server/app.py':'concurrent-change-fixture'}
        with patch.object(check, 'fingerprints', side_effect=[before, after]):
            report = self.run_fixture()
        self.assertEqual(report['test_results'][0]['status'], 'PASS')
        self.assertFalse(report['sources_stable'])
        self.assertFalse(report['pass'])

    def test_final_source_read_failure_keeps_invalidated_report(self):
        before = check.fingerprints()
        with patch.object(check, 'fingerprints', side_effect=[before, FileNotFoundError('fixture')]):
            report = self.run_fixture()
        self.assertFalse(report['pass'])
        self.assertEqual(report['integrity_errors'], [{'stage':'final_sources','error':'FileNotFoundError'}])
        self.assertTrue((self.output/'report.json').is_file())

    def test_passing_unittest_without_api_evidence_cannot_pass(self):
        report = self.run_fixture(evidence={name:{} for name in check.TEST_MODULES})
        self.assertEqual(report['test_results'][0]['status'], 'PASS')
        self.assertFalse(report['pass'])
        self.assertEqual(report['evidence_errors'][0]['code'], 'EVIDENCE_MISSING')

    def test_missing_running_worker_proof_cannot_pass(self):
        evidence = self.valid_evidence()
        record = next(iter(evidence[check.TEST_MODULES[0]].values()))
        record['workers_start']['planning']['state'] = 'DISABLED'
        report = self.run_fixture(evidence=evidence)
        self.assertFalse(report['pass'])
        self.assertIn('RUNNING_WORKER_EVIDENCE_MISSING', [item['code'] for item in report['evidence_errors']])

    def test_class_setup_error_is_reported_without_losing_artifacts(self):
        report = self.run_fixture('class_error')
        self.assertFalse(report['pass'])
        self.assertEqual(report['errors'], 1)
        self.assertEqual(report['tests_run'], 0)
        self.assertEqual(report['test_results'][0]['status'], 'ERROR')
        self.assertIsNone(report['test_results'][0]['verification_wall_seconds'])
        self.assertIn('deliberate class setup fixture', (self.output/'tests.log').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
