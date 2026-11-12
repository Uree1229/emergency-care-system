"""SQLite-only adapter units; seed7 smoke requires an explicit root opt-in."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from inquiry_lab import horizon_policy, horizon_runner, residual_runner, harness
from server import inquiry_policies, inquiry_workflow


class HorizonRunnerUnitTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(__file__).resolve().parents[2]
        self.tmp = tempfile.TemporaryDirectory(prefix='horizon-adapter-unit-', dir=self.workspace)
        self.assertTrue(Path(self.tmp.name).resolve().is_relative_to(self.workspace))
        path = Path(self.tmp.name)/'public.db'
        self.applying = sqlite3.connect(path)
        self.applying.row_factory = sqlite3.Row
        self.applying.executescript('''
            CREATE TABLE patients(id TEXT PRIMARY KEY,state TEXT,version INTEGER);
            CREATE TABLE planning_state(id INTEGER PRIMARY KEY,revision INTEGER);
            CREATE TABLE inquiry_workflows(patient_id TEXT PRIMARY KEY,version INTEGER,
                started_at INTEGER,deadline_at INTEGER,enabled INTEGER,policy_name TEXT);
            CREATE TABLE events(id INTEGER PRIMARY KEY,at INTEGER,type TEXT,patient_id TEXT,data TEXT);
            CREATE TABLE inquiry_policy_decisions(id INTEGER PRIMARY KEY);
        ''')
        self.applying.execute('INSERT INTO patients VALUES (?,?,?)', ('P','WAITING',3))
        self.applying.execute('INSERT INTO planning_state VALUES (?,?)', (1,7))
        self.applying.execute('INSERT INTO inquiry_workflows VALUES (?,?,?,?,?,?)',
            ('P',4,1000,1300,1,horizon_policy.NAME))
        self.applying.execute('INSERT INTO events VALUES (?,?,?,?,?)',
            (1,1000,'patient_created','P',json.dumps({})))
        self.applying.execute('INSERT INTO events VALUES (?,?,?,?,?)',
            (2,1000,'inquiry_workflow_started','P',json.dumps({'deadline_seconds':300})))
        self.applying.execute('INSERT INTO inquiry_policy_decisions VALUES (?)',(8,))
        self.applying.commit()
        self.inspection = sqlite3.connect(path)
        self.inspection.row_factory = sqlite3.Row
        self.snapshot = dict(patient_id='P',job_version=4,patient_version=3,revision=7,now=1000)
        self.active = dict(conn=self.applying,snapshot=self.snapshot,now=1015)
        self.applying.execute('BEGIN')

    def tearDown(self):
        self.inspection.close()
        self.applying.close()
        self.assertTrue(Path(self.tmp.name).resolve().is_relative_to(self.workspace))
        self.tmp.cleanup()

    def test_committed_context_and_actual_apply_snapshot_are_linked(self):
        before = deepcopy(self.snapshot)
        context, source = horizon_runner._capture_time_context(self.inspection,self.active,1015)
        self.assertEqual(self.snapshot,before)
        self.assertEqual(context['patient_id'],'P')
        self.assertEqual(context['job_version'],4)
        self.assertEqual(context['event_prefix_max'],2)
        self.assertEqual(context['decision_prefix_max'],8)
        self.assertEqual(context['auto_inquiry_deadline_at'],1300)
        self.assertEqual(context['evaluation_end_at'],1300)
        self.assertEqual(source['apply_snapshot'],before)
        self.assertEqual(source['job']['version'],4)
        self.assertEqual(source['job']['policy_name'],horizon_policy.NAME)
        self.assertFalse(self.inspection.in_transaction)
        self.assertTrue(self.applying.in_transaction)

    def test_current_version_and_patient_state_fail_closed(self):
        for key,value in (('job_version',5),('patient_version',4),('revision',8)):
            active = dict(self.active,snapshot=dict(self.snapshot,**{key:value}))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,'no longer matches'):
                horizon_runner._capture_time_context(self.inspection,active,1015)
        self.applying.execute("UPDATE patients SET state='CANCELLED' WHERE id='P'")
        self.applying.commit()
        self.applying.execute('BEGIN')
        with self.assertRaisesRegex(ValueError,'no longer matches'):
            horizon_runner._capture_time_context(self.inspection,self.active,1015)

    def test_uncommitted_job_change_cannot_enter_public_context(self):
        self.applying.execute('UPDATE inquiry_workflows SET deadline_at=1400 WHERE patient_id=?',('P',))
        with self.assertRaisesRegex(ValueError,'committed public job differs'):
            horizon_runner._capture_time_context(self.inspection,self.active,1015)
        self.assertFalse(self.inspection.in_transaction)
        self.assertTrue(self.applying.in_transaction)

    def test_missing_identity_and_clock_mismatch_fail_closed(self):
        for active in (None, {}, dict(self.active,now=1014),
                       dict(self.active,snapshot=dict(self.snapshot,hidden_worker_count=20))):
            with self.subTest(active=type(active).__name__), self.assertRaises(ValueError):
                horizon_runner._capture_time_context(self.inspection,active,1015)
        self.assertFalse(self.inspection.in_transaction)

    def test_public_source_exception_rolls_back_inspection_transaction(self):
        self.applying.execute('UPDATE events SET at=1020 WHERE id=2')
        self.applying.commit()
        self.applying.execute('BEGIN')
        with self.assertRaises(ValueError):
            horizon_runner._capture_time_context(self.inspection,self.active,1015)
        self.assertFalse(self.inspection.in_transaction)

    def test_missing_applying_transaction_fails_closed(self):
        self.applying.rollback()
        with self.assertRaisesRegex(ValueError,'guarded transaction'):
            horizon_runner._capture_time_context(self.inspection,self.active,1015)

    def test_registry_and_apply_are_restored_on_invalid_arm_without_scenario_run(self):
        old_decide, old_apply = inquiry_policies.decide, inquiry_workflow._apply
        registry = deepcopy(inquiry_policies._POLICIES)
        installed = set(harness._INSTALLED)
        with patch.object(horizon_runner.ie,'validate',return_value=None):
            with self.assertRaises(ValueError):
                horizon_runner.run_scenario({},[('unknown','immediate')])
        self.assertIs(inquiry_policies.decide,old_decide)
        self.assertIs(inquiry_workflow._apply,old_apply)
        self.assertEqual(inquiry_policies._POLICIES,registry)
        self.assertEqual(harness._INSTALLED,installed)


@unittest.skipUnless(os.environ.get('HORIZON_SEED7_SMOKE') == '1',
    'Root must explicitly authorize the seed7 scenario smoke')
class HorizonRunnerSeed7SmokeTests(unittest.TestCase):
    def test_old_five_controls_are_full_raw_equal_to_original_adapter(self):
        arms = [('fixed_3','immediate'),('eat_l15_f','immediate'),
            ('eat_residual_l15_f','immediate'),('v3_w45_h900','immediate'),('eat_l15_f','hold120')]
        scenario = harness.build('mix',7)
        before = deepcopy(scenario)
        old_apply = inquiry_workflow._apply
        self.assertEqual(horizon_runner.run_scenario(scenario,arms),residual_runner.run_scenario(scenario,arms))
        self.assertEqual(scenario,before)
        self.assertIs(inquiry_workflow._apply,old_apply)

    def test_new_candidate_context_matches_committed_decision_and_snapshot(self):
        old_decide, old_apply = inquiry_policies.decide, inquiry_workflow._apply
        registry = deepcopy(inquiry_policies._POLICIES)
        scenario = harness.build('mix',7)
        before = deepcopy(scenario)
        branch = horizon_runner.run_scenario(scenario,[(horizon_policy.NAME,'immediate')])['branches'][0]
        self.assertEqual(branch['instrumentation'],horizon_runner.NEW_INSTRUMENTATION)
        self.assertFalse(branch['instrumentation']['record_only'])
        self.assertTrue(branch['policy_calls'])
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            context = call['inputs']['time_context']
            source = call['workflow_snapshot']
            self.assertEqual(len(call['inputs']),8)
            self.assertEqual(context['patient_id'],decision['patient_id'])
            self.assertEqual(context['event_prefix_max'],call['db_event_prefix_max'])
            self.assertLess(context['decision_prefix_max'],decision['id'])
            self.assertEqual(context['job_version'],decision['job_version'])
            self.assertEqual(context['job_version'],source['apply_snapshot']['job_version'])
            self.assertEqual(context['job_version'],source['job']['version'])
        self.assertEqual(scenario,before)
        self.assertIs(inquiry_policies.decide,old_decide)
        self.assertIs(inquiry_workflow._apply,old_apply)
        self.assertEqual(inquiry_policies._POLICIES,registry)


if __name__ == '__main__': unittest.main()
