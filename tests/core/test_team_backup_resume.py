"""Restore real HTTP/background work from an isolated synthetic team snapshot.

The pre-backup clock adjustment is an explicit test fixture: an existing pending
batch is due, and a vanished planner owns a future lease. No live demo database,
UI, microphone, external hospital service or hand-run worker is used here.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from server.team_backup import (backup_database, inspect_state, restore_database,
                                verify_backup)
from server.team_runtime import CommonRuntime


BACKUP_RESUME_EVIDENCE = {}
REPORT = ('47세 남성. 흉통. 의식 명료. 호흡 정상. 케이타스 2. 심전도 정상. '
          '혈압 120에 80. 맥박 90회. 산소포화도 98퍼센트.')
CORE_TABLES = ('patients', 'resources', 'voice_sessions', 'voice_utterances',
               'voice_revisions', 'voice_requests', 'important_event_receipts')


class TeamBackupResumeTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2]/'.codex-build/team-backup-20261030/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='resume-', dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.target = self.root/'source.db', self.root/'restored.db'
        self.output = self.root/'backup'
        self.runtimes = []
        # Registered after TemporaryDirectory cleanup so all handles close first.
        self.addCleanup(self.close_runtimes)
        self.actor = 'SYNTHETIC-BACKUP-CREW'
        self.sequence = self.request_count = 0
        self.checks = []

    def close_runtimes(self):
        for runtime in reversed(self.runtimes):
            runtime.close()

    def runtime(self, path):
        result = CommonRuntime(path, port=0, mode='full')
        self.runtimes.append(result)
        result.start()
        self.active = result
        return result

    def check(self, name, condition):
        self.assertTrue(condition, name)
        self.checks.append(name)

    def api(self, method, path, body=None):
        request = Request(self.active.base_url+path, method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=8) as response:
                status, data = response.status, json.load(response)
        except HTTPError as response:
            with response:
                status, data = response.code, json.load(response)
        self.request_count += 1
        # Do not include patient payloads, utterances or event contents in output.
        self.assertEqual(status, 200, 'A synthetic recovery HTTP operation failed')
        return data

    def wait(self, getter, condition, label, timeout=15):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            result = getter()
            if condition(result):
                return result
            time.sleep(.05)
        self.fail(label)

    def health(self):
        return self.wait(lambda: self.api('GET', '/api/system/status'),
            lambda status: all(status['workers'][name]['state'] == 'RUNNING'
                               for name in ('planning', 'inquiry')),
            'The actual restored background workers did not become RUNNING')

    def common(self, session=None, **extra):
        self.sequence += 1
        return dict(actor_id=self.actor, client_request_id='backup-'+str(self.sequence),
                    **({'expected_version': session['version']} if session else {}), **extra)

    def say(self, session, text):
        return self.api('POST', f'/api/voice/sessions/{session["id"]}/transcripts',
            self.common(session, text=text, is_final=True, mode='auto',
                        utterance_id='backup-u-'+str(self.sequence)))['session']

    def patient(self, pid):
        return self.api('GET', f'/api/patients/{pid}')

    def rows(self, path, *tables):
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            return {table: [tuple(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in tables}

    def sql_row(self, path, sql, params=()):
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def test_restore_clears_abandoned_planning_lease_and_resumes_persisted_work(self):
        source_runtime = self.runtime(self.source)
        initial_health = self.health()

        voice = self.api('POST', '/api/voice/sessions', self.common())['session']
        voice = self.say(voice, REPORT)
        voice = self.api('POST', f'/api/voice/sessions/{voice["id"]}/confirm',
                         self.common(voice, confirmed=True))['session']
        submitted = self.api('POST', f'/api/voice/sessions/{voice["id"]}/submit',
            self.common(voice, start_inquiry=True, inquiry_policy='fixed_3',
                        context={'consent': True, 'lat': 35.1595, 'lon': 126.8526}))
        voice_pid = submitted['patient']['id']
        original = self.wait(lambda: self.patient(voice_pid),
            lambda patient: len(patient['inquiries']) == 3,
            'The source inquiry worker did not produce the initial voice batch')
        questioned, accepted = original['inquiries'][:2]
        self.api('POST', f'/api/inquiries/{questioned["id"]}/respond',
            dict(action='question', handled_by='SYNTHETIC-DESK',
                 expected_version=questioned['version'], question='Synthetic follow-up'))
        accepted = self.api('POST', f'/api/inquiries/{accepted["id"]}/respond',
            dict(action='accept', handled_by='SYNTHETIC-DESK',
                 expected_version=accepted['version']))['inquiry']
        events = self.api('GET', f'/api/patients/{voice_pid}/important-events?'+
                          urlencode({'actor_id': self.actor, 'limit': 100}))
        event_id = events['items'][0]['event_id']
        delivery = self.api('POST',
            f'/api/patients/{voice_pid}/important-events/{event_id}/delivered',
            {'actor_id': self.actor})['event']
        draft = self.api('POST', '/api/voice/sessions',
                         self.common(patient_id=voice_pid))['session']
        draft = self.say(draft, '맥박이 지금 96으로 올랐어')
        self.check('unsubmitted_voice_draft_is_staged_before_backup',
                   draft['fields']['pulse'] == 96 and
                   self.patient(voice_pid)['extra']['vitals']['hr'] == 90)

        # A second general emergency case has ten eligible synthetic hospitals,
        # making a real resumed expansion observable without changing a policy.
        pending_pid = 'SYNTHETIC-BACKUP-PENDING'
        self.api('POST', '/api/patients', dict(id=pending_pid,
            reporter_role='transporter', consent=True, age=47,
            symptoms='[synthetic] general emergency', priority=4, priority_source='expert',
            consciousness='alert', breathing='normal', lat=35.1595, lon=126.8526,
            required_capabilities=['응급의학'], resource_demands={'ed_bed': 1},
            auto_inquiry=True, inquiry_policy='fixed_3',
            extra=dict(treatment_profile_id='TR_GENERAL_ED', chief_complaint='other',
                       avpu='A', age_group='adult', pre_ktas_level=2,
                       stemi_suspected='no', vitals={'hr': 90}, synthetic_fixture=True)))
        pending = self.wait(lambda: self.patient(pending_pid),
            lambda patient: len(patient['inquiries']) == 3,
            'The source inquiry worker did not produce the pending batch')
        self.wait(lambda: self.api('GET', f'/api/patients/{voice_pid}/recommendation'),
            lambda result: result['status'] == 'READY',
            'The source planner did not create a cache to invalidate on restoration')
        source_runtime.close()

        # Explicit private-fixture timestamps model an elapsed pending wait and
        # a vanished process. Operational restore must not rewrite these values.
        now = int(time.time())
        abandoned_until = now+300
        with closing(sqlite3.connect(self.source)) as conn:
            with conn:
                conn.execute('UPDATE inquiries SET last_sent_at=? WHERE patient_id=?',
                             (now-90, pending_pid))
                conn.execute('UPDATE inquiry_workflows SET next_evaluation_at=? WHERE patient_id=?',
                             (now-1, pending_pid))
                conn.execute("UPDATE planning_state SET lease_owner='SYNTHETIC-VANISHED-PROCESS',"
                             'lease_until=?,completed_revision=revision WHERE id=1', (abandoned_until,))
        source_core = self.rows(self.source, *CORE_TABLES)
        source_inquiries = self.rows(self.source, 'inquiries')['inquiries']
        source_state = inspect_state(self.source)
        original_workflow = self.sql_row(self.source,
            'SELECT * FROM inquiry_workflows WHERE patient_id=?', (pending_pid,))
        source_cache_count = self.sql_row(self.source,
            'SELECT COUNT(*) AS count FROM planning_results')['count']
        self.check('snapshot_contains_an_abandoned_future_lease_and_cached_plan',
                   source_cache_count > 0 and original_workflow['enabled'] == 1)

        backup = backup_database(self.source, self.output)
        verified = verify_backup(self.output)
        restored = restore_database(self.output, self.target)
        target_state = inspect_state(self.target)
        self.check('backup_and_restore_are_verified_before_worker_start',
                   backup['verified'] and verified['verified'] and restored['verified'] and
                   restored['business_state_preserved'])
        self.check('restored_schema_and_all_business_tables_match_the_source_snapshot',
                   source_state['schema_sha256'] == target_state['schema_sha256'] and
                   source_state['business_sha256'] == target_state['business_sha256'])
        self.check('restore_preserves_original_core_rows_and_workflow_deadline',
                   source_core == self.rows(self.target, *CORE_TABLES) and
                   original_workflow == self.sql_row(self.target,
                       'SELECT * FROM inquiry_workflows WHERE patient_id=?', (pending_pid,)))
        reset = self.sql_row(self.target, 'SELECT * FROM planning_state WHERE id=1')
        self.check('restore_discards_captured_lease_error_and_cached_recommendations',
                   reset['lease_until'] == 0 and reset['lease_owner'] is None and
                   reset['completed_revision'] == -1 and reset['last_error'] is None and
                   self.sql_row(self.target, 'SELECT COUNT(*) AS count FROM planning_results')['count'] == 0 and
                   restored['planning_reset']['cache_rows_removed'] == source_cache_count and
                   restored['planning_reset']['previous_lease_until'] == abandoned_until)

        # Both workers now run normally; no run_once, solver mock or worker
        # monkeypatch is used to manufacture resumed work or recommendation READY.
        resumed_at = int(time.time())
        elapsed_start = time.monotonic()
        restored_runtime = self.runtime(self.target)
        resumed_health = self.health()
        expanded = self.wait(lambda: self.patient(pending_pid),
            lambda patient: len(patient['inquiries']) >= 6,
            'The restored inquiry worker did not expand the persisted pending batch')
        recommendation = self.wait(
            lambda: self.api('GET', f'/api/patients/{voice_pid}/recommendation'),
            lambda result: result['status'] == 'READY' and result['created_at'] >= resumed_at and
                           result['expires_at'] > int(time.time()) and
                           isinstance(result.get('result', {}).get('solver'), dict),
            'The restored planner did not publish a fresh READY recommendation')
        completed = self.wait(lambda: self.api('GET', '/api/system/status'),
            lambda status: status['planning']['pending'] is False and
                           status['planning']['revision'] == status['planning']['completed_revision'] and
                           status['planning']['result_counts']['fresh'] > 0,
            'The restored planner did not complete the current input revision')
        resume_seconds = time.monotonic()-elapsed_start
        self.check('both_background_workers_run_and_publish_before_the_old_lease_expires',
                   resume_seconds < 15 and int(time.time()) < abandoned_until and
                   all(resumed_health['workers'][name]['state'] == 'RUNNING'
                       for name in ('planning', 'inquiry')) and
                   recommendation['status'] == 'READY' and
                   recommendation['expires_at'] > int(time.time()) and
                   isinstance(recommendation['result']['solver'], dict) and
                   completed['planning']['revision'] == completed['planning']['completed_revision'] and
                   completed['planning']['result_counts']['fresh'] > 0 and
                   not completed['planning']['pending'])
        resumed_workflow = self.sql_row(self.target,
            'SELECT * FROM inquiry_workflows WHERE patient_id=?', (pending_pid,))
        self.check('persisted_pending_work_advances_without_restarting_its_deadline',
                   resumed_workflow['round_no'] > original_workflow['round_no'] and
                   resumed_workflow['started_at'] == original_workflow['started_at'] and
                   resumed_workflow['deadline_at'] == original_workflow['deadline_at'] and
                   len(expanded['inquiries']) >= 6)
        current_inquiries = {row[0]: row for row in self.rows(self.target, 'inquiries')['inquiries']}
        self.check('all_preexisting_inquiries_keep_their_original_response_and_expiry_timestamps',
                   all(current_inquiries[row[0]] == row for row in source_inquiries))
        self.check('background_recovery_does_not_refresh_resources_or_apply_unsubmitted_voice',
                   source_core == self.rows(self.target, *CORE_TABLES))
        recovered_draft = self.api('GET', f'/api/voice/sessions/{draft["id"]}?'+
                                   urlencode({'actor_id': self.actor}))['session']
        recovered_events = self.api('GET', f'/api/patients/{voice_pid}/important-events?'+
            urlencode({'actor_id': self.actor, 'unacknowledged_only': 'true', 'limit': 100}))
        recovered_event = next(item for item in recovered_events['items'] if item['event_id'] == event_id)
        recovered_acceptance = next(item for item in self.patient(voice_pid)['inquiries']
                                    if item['id'] == accepted['id'])
        self.check('http_readback_preserves_staged_voice_unacknowledged_receipt_and_original_acceptance',
                   recovered_draft['version'] == draft['version'] and
                   recovered_draft['fields']['pulse'] == 96 and
                   recovered_event['delivered_at'] == delivery['delivered_at'] and
                   recovered_event['acknowledged_at'] is None and
                   recovered_acceptance['responded_at'] == accepted['responded_at'] and
                   recovered_acceptance['expires_at'] == accepted['expires_at'] and
                   recovered_acceptance['state'] == 'ACCEPTED')

        restored_runtime.close()
        self.check('all_real_http_and_worker_threads_stop_after_the_private_rehearsal',
                   all(not runtime.thread.is_alive() and
                       all(not worker.thread.is_alive() for worker in
                           (runtime.planner, runtime.inquiry_worker)) for runtime in self.runtimes))
        BACKUP_RESUME_EVIDENCE[self.id()] = dict(
            test_id=self.id(), synthetic=True, actual_speech_count=0,
            actual_hospital_requests=0, manual_worker_calls=0,
            worker_mode='actual_background_threads', http_requests=self.request_count,
            checks=list(self.checks), pre_backup_time_adjustment=True,
            original_lease_seconds=300, resume_seconds=round(resume_seconds, 3),
            source_pending_count=len(pending['inquiries']), restored_pending_count=len(expanded['inquiries']),
            pending_round_before=original_workflow['round_no'], pending_round_after=resumed_workflow['round_no'],
            removed_planning_cache_rows=source_cache_count,
            schema_sha256=source_state['schema_sha256'], business_sha256=source_state['business_sha256'],
            worker_states_start={name: initial_health['workers'][name]['state'] for name in ('planning', 'inquiry')},
            worker_states_restored={name: resumed_health['workers'][name]['state'] for name in ('planning', 'inquiry')},
            restored_recommendation_status=recommendation['status'],
            restored_solver_status=recommendation['result']['solver'].get('status'),
            fresh_planning_result_count=completed['planning']['result_counts']['fresh'],
            planning_revision=completed['planning']['revision'],
            planning_completed_revision=completed['planning']['completed_revision'],
            planning_pending_after_resume=completed['planning']['pending'],
            final_workers_stopped=True)


if __name__ == '__main__':
    unittest.main()
