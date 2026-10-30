"""HTTP concurrency guards against actual full-mode background workers.

All fixtures are synthetic, private and local. No source/operational DB changes
or external hospital/routing/speech requests are performed by these checks.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from server.team_runtime import CommonRuntime

CONNECTION_EVIDENCE = {}


class TeamConnectionRaceTests(unittest.TestCase):
    def setUp(self):
        CONNECTION_EVIDENCE[self.id()] = dict(requests=[], checks=[], worker_health={})
        directory = Path(__file__).resolve().parent/'.codex-build/team-connection-check-20261030/tmp'
        directory.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='connection-race-', dir=directory)
        self.path = Path(self.tmp.name)/'private.db'
        self.runtime = CommonRuntime(self.path, port=0, mode='full')
        try:
            self.runtime.start()
            status = self.wait_for(lambda: self.get('/api/system/status'), lambda result: all(
                result['workers'][name]['state'] == 'RUNNING' for name in ('planning', 'inquiry')))
            CONNECTION_EVIDENCE[self.id()]['worker_health']['start'] = status['workers']
        except BaseException:
            self.runtime.close()
            self.tmp.cleanup()
            raise

    def tearDown(self):
        CONNECTION_EVIDENCE[self.id()]['worker_health']['before_close'] = self.runtime.runtime.snapshot()['workers']
        self.runtime.close()
        CONNECTION_EVIDENCE[self.id()]['worker_health']['threads_stopped'] = all(
            not worker.thread.is_alive() for worker in (self.runtime.planner, self.runtime.inquiry_worker))
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        request = urllib.request.Request(self.runtime.base_url+path, method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                status, result = response.status, json.load(response)
        except urllib.error.HTTPError as response:
            with response:
                status, result = response.code, json.load(response)
        except OSError as error:
            CONNECTION_EVIDENCE[self.id()]['requests'].append(dict(
                method=method, path=path, status=None, error=type(error).__name__))
            raise
        CONNECTION_EVIDENCE[self.id()]['requests'].append(dict(method=method, path=path, status=status))
        return status, result

    def checked(self, *names):
        CONNECTION_EVIDENCE[self.id()]['checks'].extend({'name': name, 'pass': True} for name in names)

    def ok(self, method, path, body=None):
        status, result = self.call(method, path, body)
        self.assertEqual(status, 200, result)
        return result

    def get(self, path):
        return self.ok('GET', path)

    def rows(self, query, parameters=()):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query, parameters)]

    def wait_for(self, fetch, predicate, seconds=9):
        deadline = time.monotonic()+seconds
        result = None
        while time.monotonic() < deadline:
            result = fetch()
            if predicate(result):
                return result
            time.sleep(.05)
        self.fail('Expected server condition was not observed: '+repr(result))

    def race(self, first, second):
        barrier = threading.Barrier(2)
        def send(request):
            barrier.wait(timeout=4)
            return self.call(*request)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send, request) for request in (first, second)]
            return [future.result(timeout=10) for future in futures]

    def patient(self, pid):
        return self.ok('POST', '/api/patients', dict(id=pid, reporter_role='transporter', consent=True,
            age=47, symptoms='[가상] 흉통', consciousness='alert', breathing='normal',
            priority=4, priority_source='expert', lat=35.1595, lon=126.8526,
            required_capabilities=[], resource_demands={'ed_bed': 1},
            extra=dict(treatment_profile_id='TR_GENERAL_ED', chief_complaint='cardiac',
                avpu='A', age_group='adult', pre_ktas_level=2, stemi_suspected='no', synthetic_fixture=True)))

    def inquiry(self, pid):
        patient = self.ok('POST', f'/api/patients/{pid}/inquiries', {'hospital_ids': ['H01']})
        return next(q for q in patient['inquiries'] if q['hospital_id'] == 'H01')

    def accept(self, inquiry, handled_by='SYNTHETIC-DESK'):
        return self.ok('POST', f'/api/inquiries/{inquiry["id"]}/respond', dict(
            action='accept', handled_by=handled_by, expected_version=inquiry['version']))['inquiry']

    def amendment(self, version, key='synthetic-amendment'):
        return dict(actor_id='SYNTHETIC-CREW', client_request_id=key,
            expected_patient_version=version, reason='가상 추가 관찰 확인',
            changes={'symptoms': '[가상] 흉통과 구토'})

    def bed(self):
        return next(row for row in self.get('/api/hospitals/H01')['resources']
                    if row['resource_type'] == 'ed_bed')

    def test_two_acceptances_cannot_both_confirm_the_last_available_place(self):
        resource = self.bed()
        self.ok('PUT', '/api/hospitals/H01/resources/ed_bed', dict(
            capacity=1, occupied=0, expected_version=resource['version'], handled_by='SYNTHETIC-DESK'))
        inquiries = []
        for pid in ('RACE-LAST-A', 'RACE-LAST-B'):
            self.patient(pid)
            inquiries.append(self.accept(self.inquiry(pid)))
        # Hospital acceptance itself reserves no place. Both replies are valid
        # before either destination confirmation acquires the resource.
        self.assertTrue(all(q['state'] == 'ACCEPTED' for q in inquiries))
        self.checked('both_acceptances_valid_before_destination_confirmation')
        responses = self.race(
            ('POST', '/api/patients/RACE-LAST-A/confirm', {'inquiry_id': inquiries[0]['id']}),
            ('POST', '/api/patients/RACE-LAST-B/confirm', {'inquiry_id': inquiries[1]['id']}))
        self.assertEqual(sorted(code for code, result in responses), [200, 409])
        self.assertEqual(next(result for code, result in responses if code == 409)['error'], 'RESOURCE_CONFLICT')
        patients = [self.get('/api/patients/'+pid) for pid in ('RACE-LAST-A', 'RACE-LAST-B')]
        self.assertEqual(sorted(p['state'] for p in patients), ['CONFIRMED', 'WAITING'])
        self.assertEqual(sum(p['destination_hospital_id'] == 'H01' for p in patients), 1)
        resource = self.bed()
        self.assertEqual((resource['capacity'], resource['occupied'], resource['expected'], resource['free']), (1, 0, 1, 0))
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM inquiries WHERE state='CONFIRMED'")[0]['n'], 1)
        self.checked('one_confirmation_and_one_resource_conflict', 'only_one_patient_consumes_last_place')

    def test_stale_hospital_screen_cannot_accept_reused_inquiry_after_patient_amendment(self):
        pid = 'RACE-UPDATED'
        patient = self.patient(pid)
        old_screen = self.inquiry(pid)
        self.accept(old_screen)
        amendment = self.ok('POST', f'/api/patients/{pid}/amendments', self.amendment(patient['version']))
        self.assertEqual(amendment['patient']['version'], patient['version']+1)
        current = self.inquiry(pid)
        self.assertEqual(current['id'], old_screen['id'])
        self.assertEqual(current['state'], 'PENDING')
        self.assertTrue(current['requires_review_version'])
        self.assertEqual(current['patient_version'], amendment['patient']['version'])
        code, rejected = self.call('POST', f'/api/inquiries/{current["id"]}/respond', dict(
            action='accept', handled_by='SYNTHETIC-OLD-SCREEN', expected_version=old_screen['version']))
        self.assertEqual(code, 409)
        self.assertEqual(rejected['error'], 'CONFLICT')
        latest = next(q for q in self.get(f'/api/patients/{pid}')['inquiries'] if q['id'] == current['id'])
        self.assertEqual((latest['state'], latest['version']), ('PENDING', current['version']))
        self.checked('stale_reused_inquiry_response_rejected_without_mutation')
        accepted = self.accept(latest, handled_by='SYNTHETIC-NEW-SCREEN')
        self.assertEqual(accepted['patient_version'], amendment['patient']['version'])
        recommendation = self.wait_for(lambda: self.get(f'/api/patients/{pid}/recommendation'), lambda r:
            r['status'] == 'READY' and r['result']['confirmable_now'])
        self.assertEqual(recommendation['result']['hospital_id'], 'H01')
        self.assertEqual(recommendation['result']['inquiry_id'], current['id'])
        CONNECTION_EVIDENCE[self.id()]['solver'] = recommendation['result']['solver']
        final = self.get(f'/api/patients/{pid}')
        self.assertEqual(final['state'], 'WAITING')
        self.assertIsNone(final['destination_hospital_id'])
        self.checked('current_version_acceptance_has_fresh_confirmable_recommendation',
                     'solver_recommendation_does_not_confirm_destination')

    def test_two_resource_updates_with_one_version_commit_exactly_once(self):
        old = self.bed()
        events_before = self.rows("SELECT COUNT(*) AS n FROM events WHERE type='resource_updated'")[0]['n']
        path = '/api/hospitals/H01/resources/ed_bed'
        first = dict(capacity=2, occupied=0, status='available', expected_version=old['version'], handled_by='SYNTHETIC-A')
        second = dict(first, capacity=3, handled_by='SYNTHETIC-B')
        responses = self.race(('PUT', path, first), ('PUT', path, second))
        self.assertEqual(sorted(code for code, result in responses), [200, 409])
        self.assertEqual(next(result for code, result in responses if code == 409)['error'], 'CONFLICT')
        winner = next(index for index, response in enumerate(responses) if response[0] == 200)
        final = self.bed()
        self.assertEqual(final['version'], old['version']+1)
        self.assertEqual(final['capacity'], (first, second)[winner]['capacity'])
        self.assertEqual(final['occupied'], 0)
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM events WHERE type='resource_updated'")[0]['n'], events_before+1)
        self.checked('one_resource_version_update_and_one_conflict',
                     'resource_version_and_event_increment_exactly_once')

    def test_patient_amendment_and_old_destination_confirmation_cannot_both_commit(self):
        pid = 'RACE-AMEND-VS-CONFIRM'
        patient = self.patient(pid)
        accepted = self.accept(self.inquiry(pid))
        responses = self.race(
            ('POST', f'/api/patients/{pid}/amendments', self.amendment(patient['version'], 'amend-confirm-race')),
            ('POST', f'/api/patients/{pid}/confirm', {'inquiry_id': accepted['id']}))
        self.assertEqual(sorted(code for code, result in responses), [200, 409])
        current = self.get('/api/patients/'+pid)
        inquiry = next(q for q in current['inquiries'] if q['id'] == accepted['id'])
        records = self.rows('SELECT * FROM patient_amendments WHERE patient_id=?', (pid,))
        if current['state'] == 'CONFIRMED':
            self.assertEqual(current['destination_hospital_id'], 'H01')
            self.assertEqual(inquiry['state'], 'CONFIRMED')
            self.assertEqual(len(records), 0)
        else:
            self.assertEqual(current['state'], 'WAITING')
            self.assertIsNone(current['destination_hospital_id'])
            self.assertEqual(inquiry['state'], 'CANCELLED')
            self.assertEqual(len(records), 1)
            self.assertEqual(current['version'], patient['version']+1)
        self.checked('amendment_and_old_destination_confirmation_cannot_both_commit',
                     'winning_transaction_has_consistent_patient_inquiry_and_amendment_rows')


if __name__ == '__main__':
    unittest.main()
