"""Recovery contracts over real HTTP/background workers and private synthetic DBs.

Retries deliberately discard an already committed response and resend its exact
key/body. These are API recovery tests, not a browser, microphone or offline UI
test. No current demo database or external medical/routing service is accessed.
"""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from server.team_runtime import CommonRuntime


CONNECTION_EVIDENCE = {}
RECOVERY_EVIDENCE = CONNECTION_EVIDENCE
REPORT = ('47세 남성. 흉통. 의식 명료. 호흡 정상. 케이타스 2. 심전도 정상. '
          '혈압 120에 80. 맥박 90회. 산소포화도 98퍼센트.')


class TeamConnectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parent/'.codex-build/team-connection-check-20261030/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='recovery-http-', dir=parent)
        # unittest skips tearDown when setUp/start/health polling fails, but
        # registered cleanups still run. LIFO closes the runtime before files.
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'private.db'
        self.runtime = CommonRuntime(self.path, port=0, mode='full')
        self.addCleanup(lambda: self.runtime.close())
        self.actor = 'SYNTHETIC-RECOVERY-CREW'
        self.sequence = 0
        self.requests, self.checks = [], []
        self.runtime.start()
        self.start_health = self.wait_health()
        self.pid = None

    def tearDown(self):
        end_health = None
        try:
            if self.runtime.http is not None:
                end_health = self.api('GET', '/api/system/status')
        finally:
            self.runtime.close()
            CONNECTION_EVIDENCE[self.id()] = dict(test_id=self.id(), synthetic=True,
                actual_speech_count=0, actual_hospital_requests=0,
                manual_worker_calls=0, worker_mode='actual_background_threads',
                requests=copy.deepcopy(self.requests), checks=list(self.checks),
                workers_start=self.start_health['workers'],
                workers_end=end_health['workers'] if end_health else None,
                workers_stopped=all(not worker.thread.is_alive() for worker in
                    (self.runtime.planner, self.runtime.inquiry_worker) if worker is not None))

    def check(self, name, condition):
        self.assertTrue(condition, name)
        self.checks.append(name)

    def api(self, method, path, body=None, *, status=200):
        request = Request(self.runtime.base_url+path, method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=8) as response:
                actual, result = response.status, json.load(response)
        except HTTPError as response:
            with response:
                actual, result = response.code, json.load(response)
        self.requests.append(dict(method=method, path=path, status=actual))
        self.assertEqual(actual, status, result)
        return result

    def wait(self, getter, condition, label, timeout=10):
        until = time.monotonic()+timeout
        last = None
        while time.monotonic() < until:
            last = getter()
            if condition(last):
                return last
            time.sleep(.05)
        self.fail(label+': '+repr(last))

    def wait_health(self):
        return self.wait(lambda: self.api('GET', '/api/system/status'),
            lambda data: all(data['workers'][name]['state'] == 'RUNNING'
                             for name in ('planning', 'inquiry')),
            'The real workers did not reach RUNNING')

    def common(self, session=None, **extra):
        self.sequence += 1
        return dict(actor_id=self.actor, client_request_id='recovery-'+str(self.sequence),
                    **({'expected_version': session['version']} if session else {}), **extra)

    def post_voice(self, session, suffix, **extra):
        return self.api('POST', f'/api/voice/sessions/{session["id"]}/{suffix}',
                        self.common(session, **extra))

    def say(self, session, text):
        return self.post_voice(session, 'transcripts', text=text, is_final=True,
            mode='auto', utterance_id='recovery-u-'+str(self.sequence))['session']

    def patient(self):
        return self.api('GET', f'/api/patients/{self.pid}')

    def linked(self):
        return self.api('POST', '/api/voice/sessions', self.common(patient_id=self.pid))['session']

    def create_patient(self, *, retry=False):
        session = self.api('POST', '/api/voice/sessions', self.common())['session']
        session = self.say(session, REPORT)
        session = self.post_voice(session, 'confirm', confirmed=True)['session']
        body = self.common(session, start_inquiry=True, inquiry_policy='fixed_3',
                           context={'consent': True, 'lat': 35.1595, 'lon': 126.8526})
        path = f'/api/voice/sessions/{session["id"]}/submit'
        committed = self.api('POST', path, body)
        # The caller regards the committed response as lost. Its retry must use
        # precisely the same request identifier, expected version and body.
        result = self.api('POST', path, copy.deepcopy(body)) if retry else committed
        self.pid = result['patient']['id']
        self.wait(self.patient, lambda p: len(p['inquiries']) == 3,
                  'The real inquiry worker did not send its initial batch')
        return session, result

    def rows(self, *tables):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            return {table: [tuple(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in tables}

    def scalar(self, sql, params=()):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def make_questions(self):
        self.create_patient()
        questions = []
        for inquiry in self.patient()['inquiries'][:2]:
            result = self.api('POST', f'/api/inquiries/{inquiry["id"]}/respond',
                dict(action='question', handled_by='SYNTHETIC-DESK',
                     expected_version=inquiry['version'], question='Synthetic follow-up question'))
            self.assertEqual(result['inquiry']['state'], 'CHECKING')
            questions.append(inquiry['hospital_id'])
        return questions

    def event_list(self, *, actor=None, **query):
        query = dict(actor_id=actor or self.actor, **query)
        return self.api('GET', f'/api/patients/{self.pid}/important-events?'+urlencode(query))

    def receipt(self, event_id, action, *, actor=None, status=200):
        return self.api('POST', f'/api/patients/{self.pid}/important-events/{event_id}/{action}',
                        {'actor_id': actor or self.actor}, status=status)

    def test_lost_submit_response_retry_recovers_one_patient_and_latest_linked_draft(self):
        source, result = self.create_patient(retry=True)
        self.check('same_submit_key_is_an_idempotent_retry', result.get('duplicate') is True)
        self.check('one_patient_and_one_applied_event',
            self.scalar('SELECT COUNT(*) FROM patients') == 1 and self.scalar(
                "SELECT COUNT(*) FROM events WHERE patient_id=? AND type='voice_information_applied'",
                (self.pid,)) == 1)
        reopened = self.api('GET', f'/api/voice/sessions/{source["id"]}?'+urlencode({'actor_id':self.actor}))['session']
        patient = self.patient()
        self.check('new_get_restores_submitted_session_identity',
            reopened['id'] == source['id'] and reopened['state'] == 'SUBMITTED'
            and reopened['patient_id'] == self.pid)
        linked = self.linked()
        self.check('linked_draft_uses_current_patient_version',
            linked['base_patient_version'] == patient['version'] and linked['patient_id'] == self.pid)
        self.check('readback_preserves_all_original_measurements',
            linked['fields']['systolic_bp'] == 120 and linked['fields']['diastolic_bp'] == 80
            and linked['fields']['pulse'] == 90 and linked['fields']['spo2'] == 98)

    def test_event_cursor_and_reload_recovery_do_not_imply_delivery_or_medical_acknowledgement(self):
        self.make_questions()
        before = self.rows('patients', 'inquiries', 'resources', 'voice_sessions', 'events')
        first = self.event_list(limit=1)
        second = self.event_list(after_id=first['next_after_id'], limit=1)
        self.check('stream_cursor_pages_without_duplicates', first['has_more'] and not second['has_more']
            and len(first['items']) == len(second['items']) == 1
            and first['items'][0]['event_id'] != second['items'][0]['event_id'])
        end = self.event_list(after_id=second['next_after_id'])
        self.assertEqual(end['items'], [])
        recovered = self.event_list(unacknowledged_only='true', limit=1)
        self.check('reload_recovers_unacknowledged_event_beyond_stream_cursor',
            recovered['items'][0]['event_id'] == first['items'][0]['event_id']
            and recovered['next_after_id'] is None and recovered['has_more'])
        self.check('get_is_not_a_delivery_receipt', self.scalar(
            'SELECT COUNT(*) FROM important_event_receipts') == 0
            and recovered['items'][0]['delivered_at'] is None)
        eid = first['items'][0]['event_id']
        denied = self.receipt(eid, 'acknowledge', status=409)
        self.assertEqual(denied['error'], 'DELIVERY_REQUIRED')
        delivered = self.receipt(eid, 'delivered')
        again = self.receipt(eid, 'delivered')
        acknowledged = self.receipt(eid, 'acknowledge')
        repeated = self.receipt(eid, 'acknowledge')
        self.check('delivery_and_ack_retries_preserve_first_server_timestamps',
            not delivered['duplicate'] and again['duplicate']
            and delivered['event']['delivered_at'] == again['event']['delivered_at']
            and not acknowledged['duplicate'] and repeated['duplicate']
            and acknowledged['event']['acknowledged_at'] == repeated['event']['acknowledged_at'])
        other = self.event_list(actor='SYNTHETIC-OTHER-CREW', unacknowledged_only='true')
        self.check('other_actor_receipts_are_independent',
            other['items'][0]['event_id'] == eid and other['items'][0]['delivered_at'] is None
            and other['items'][0]['acknowledged_at'] is None)
        remaining = self.event_list(unacknowledged_only='true')
        self.check('acknowledged_item_leaves_only_own_recovery_list',
            [item['event_id'] for item in remaining['items']] == [second['items'][0]['event_id']])
        self.check('notification_operations_preserve_medical_and_inquiry_state',
                   before == self.rows(*before))

    def test_full_runtime_restart_preserves_voice_patient_inquiry_resources_and_receipts(self):
        self.make_questions()
        event = self.event_list(limit=1)['items'][0]
        self.receipt(event['event_id'], 'delivered')
        self.runtime.close()
        before = self.rows('patients', 'inquiries', 'resources', 'voice_sessions', 'voice_utterances',
                           'voice_revisions', 'voice_requests', 'important_event_receipts')
        resource_times = self.scalar('SELECT MIN(updated_at) FROM resources')
        self.runtime = CommonRuntime(self.path, port=0, mode='full').start()
        self.wait_health()
        patient = self.patient()
        restored = self.event_list(unacknowledged_only='true')
        self.check('restart_preserves_saved_identifiers_and_unconfirmed_destination',
            patient['id'] == self.pid and patient['state'] == 'WAITING'
            and patient['destination_hospital_id'] is None)
        self.check('restart_preserves_unacknowledged_delivery_receipt',
            restored['items'][0]['event_id'] == event['event_id']
            and restored['items'][0]['delivered_at'] is not None
            and restored['items'][0]['acknowledged_at'] is None)
        self.check('restart_does_not_reset_business_rows_or_refresh_resources',
            before == self.rows(*before) and self.scalar('SELECT MIN(updated_at) FROM resources') == resource_times)

    def test_corrected_voice_retry_applies_once_and_new_draft_reads_current_values(self):
        self.create_patient()
        before = self.patient()
        session = self.say(self.linked(), '맥박이 지금 96으로 올랐어')
        self.check('unsubmitted_correction_does_not_change_committed_patient',
            self.patient()['version'] == before['version'] and self.patient()['extra']['vitals']['hr'] == 90)
        session = self.post_voice(session, 'confirm', confirmed=True)['session']
        body = self.common(session)
        path = f'/api/voice/sessions/{session["id"]}/submit'
        changed = self.api('POST', path, body)
        retried = self.api('POST', path, copy.deepcopy(body))
        current = self.patient()
        self.check('correction_retry_keeps_one_patient_version_and_amendment',
            retried.get('duplicate') is True and current['version'] == before['version']+1
            and current['version'] == changed['patient']['version']
            and self.scalar('SELECT COUNT(*) FROM patient_amendments WHERE patient_id=?', (self.pid,)) == 1)
        linked = self.linked()
        self.check('new_draft_reads_corrected_value_and_preserves_other_vitals',
            linked['base_patient_version'] == current['version'] and linked['fields']['pulse'] == 96
            and linked['fields']['systolic_bp'] == 120 and linked['fields']['diastolic_bp'] == 80
            and linked['fields']['spo2'] == 98)


if __name__ == '__main__':
    unittest.main()
