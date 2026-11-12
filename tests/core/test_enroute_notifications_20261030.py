"""Scoped transport-change notices and actual-worker HTTP recovery, synthetic only."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, urlopen

from server import conversations, db, important_events as notices, service
from server.team_backup import backup_database, inspect_state, restore_database
from server.team_runtime import CommonRuntime
from server.travel import TravelEstimator


NOTIFICATION_EVIDENCE = {}
CHANGE_TYPES = ('enroute_patient_change_requested', 'enroute_patient_change_decided')
NOW = 2_000_000_000
REPORT = ('47세 남성. 흉통. 의식 명료. 호흡 정상. 케이타스 2. 심전도 정상. '
          '혈압 120에 80. 맥박 90회. 산소포화도 98퍼센트.')


class EnrouteNotificationScopeTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(':memory:')
        self.addCleanup(self.conn.close)
        db.init(self.conn)
        self.estimator = TravelEstimator(use_kakao=False)
        for hid in ('N-H1', 'N-H2', 'N-H3'):
            service.upsert_hospital(self.conn, dict(id=hid, name='Synthetic '+hid,
                lat=35.16, lon=126.85, capabilities=['응급의학'], source='synthetic'))
            service.set_resource(self.conn, NOW, hid, 'ed_bed', 4, 0, source='synthetic')
        service.create_patient(self.conn, NOW, 'N-P', dict(reporter_role='transporter',
            consent=True, symptoms='Synthetic case', lat=35.16, lon=126.85,
            required_capabilities=['응급의학'], resource_demands={'ed_bed': 1}))
        self.inquiries = service.send_inquiries(self.conn, NOW+1, 'N-P', ['N-H1', 'N-H2'])['inquiries']
        first = next(q for q in self.inquiries if q['hospital_id'] == 'N-H1')
        service.respond(self.conn, NOW+2, first['id'], 'accept', 'synthetic-desk')
        service.confirm_destination(self.conn, NOW+3, 'N-P', first['id'])

    def emit(self, kind, hid='N-H1', **payload):
        return service.log(self.conn, NOW+10, 'synthetic-role', kind, 'N-P', hid,
            review_id='SYNTHETIC-REVIEW', reason='private synthetic free-text reason', **payload)

    def listing(self, hid=None):
        return notices.list_events(self.conn, NOW+100, 'N-P', actor_id='synthetic-recipient',
                                   hospital_id=hid)['items']

    def test_new_notice_and_conversation_gets_are_nonmutating_and_pending_hospital_is_not_a_recipient(self):
        eid = self.emit(CHANGE_TYPES[0])
        before = self.conn.total_changes
        denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                  sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_PRAGMA}
        self.conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK)
        try:
            crew, current, pending = self.listing(), self.listing('N-H1'), self.listing('N-H2')
            crew_room = conversations.get_conversation(self.conn, NOW+100, 'N-P')
            hospital_room = conversations.get_conversation(self.conn, NOW+100, 'N-P', hospital_id='N-H1')
        finally:
            self.conn.set_authorizer(None)
        self.assertEqual(before, self.conn.total_changes)
        for items in (crew, current):
            item = next(item for item in items if item['event_id'] == eid)
            self.assertIn('검토', item['summary'])
            self.assertIsNone(item['delivered_at'])
            self.assertIsNone(item['acknowledged_at'])
            self.assertNotIn('data', item)
            self.assertNotIn('private synthetic', item['summary'])
        self.assertNotIn(eid, [item['event_id'] for item in pending])
        for room in (crew_room, hospital_room):
            item = next(item for item in room['items'] if item.get('event_id') == eid)
            self.assertEqual(item['text'], notices.event_summary(dict(type=CHANGE_TYPES[0])))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM important_event_receipts').fetchone()[0], 0)

    def test_departed_hospital_cannot_receive_or_ack_new_session_events_even_in_the_same_second(self):
        old_event = self.emit(CHANGE_TYPES[0])
        service.hospital_decision(self.conn, NOW+10, 'N-P', 'N-H1', 'adjust', 'Synthetic reassignment')
        second = next(q for q in service.send_inquiries(self.conn, NOW+10, 'N-P', ['N-H2'])['inquiries']
                      if q['hospital_id'] == 'N-H2')
        service.respond(self.conn, NOW+10, second['id'], 'accept', 'synthetic-desk')
        service.change_destination(self.conn, NOW+10, 'N-P', second['id'], self.estimator)
        new_event = self.emit(CHANGE_TYPES[0], 'N-H2')
        new_decision = self.emit(CHANGE_TYPES[1], 'N-H2', decision='keep')
        late_old_scope = self.emit(CHANGE_TYPES[1], 'N-H1', decision='reject')
        old, new = self.listing('N-H1'), self.listing('N-H2')
        self.assertIn(old_event, [item['event_id'] for item in old])
        self.assertFalse({new_event, new_decision, late_old_scope} & {item['event_id'] for item in old})
        self.assertTrue({new_event, new_decision} <= {item['event_id'] for item in new})
        self.assertNotIn(old_event, [item['event_id'] for item in new])
        for eid in (new_event, new_decision, late_old_scope):
            with self.assertRaises(service.ServiceError) as error:
                notices.mark_delivered(self.conn, NOW+100, 'N-P', eid,
                    actor_id='synthetic-old-desk', hospital_id='N-H1')
            self.assertEqual(error.exception.code, 'NOT_FOUND')
        old_room = conversations.get_conversation(self.conn, NOW+100, 'N-P', hospital_id='N-H1')
        self.assertFalse(old_room['can_send'])
        self.assertFalse({new_event, new_decision, late_old_scope} &
                         {item.get('event_id') for item in old_room['items']})
        with self.assertRaises(service.ServiceError) as error:
            notices.list_events(self.conn, NOW+100, 'N-P', actor_id='unrelated', hospital_id='N-H3')
        self.assertEqual(error.exception.code, 'NOT_PARTICIPANT')

    def test_decision_summaries_are_distinct_safe_and_shared_with_the_conversation(self):
        summaries = []
        for decision in ('keep', 'adjust', 'reject'):
            eid = self.emit(CHANGE_TYPES[1], decision=decision)
            notice = next(item for item in self.listing() if item['event_id'] == eid)
            room = conversations.get_conversation(self.conn, NOW+100, 'N-P')
            item = next(item for item in room['items'] if item.get('event_id') == eid)
            self.assertEqual(item['text'], notice['summary'])
            self.assertIn('목적지', notice['summary'])
            self.assertNotIn('private synthetic', notice['summary'])
            summaries.append(notice['summary'])
        self.assertEqual(len(set(summaries)), 3)
        for payload in ('invalid JSON', '[]', None, {'decision': []}, {'decision': {}}):
            self.assertEqual(notices.event_summary(dict(type=CHANGE_TYPES[1], data=payload)),
                             notices.SUMMARIES[CHANGE_TYPES[1]])


def _route(path):
    url = urlsplit(path)
    parts = url.path.split('/')
    for key, replacement in (('patients', '{patient}'), ('hospitals', '{hospital}'),
                              ('sessions', '{session}'), ('inquiries', '{inquiry}'),
                              ('transport-change-reviews', '{review}'), ('important-events', '{event}')):
        for index, value in enumerate(parts[:-1]):
            if value == key:
                parts[index+1] = replacement
    return '/'.join(parts), sorted({key for key, _ in parse_qsl(url.query)})


class EnrouteNotificationHttpTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2]/'.codex-build/enroute-notifications/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='http-', dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtimes, self.requests, self.checks, self.publications = [], [], [], []
        self.addCleanup(self.close_all)
        self.counter = 0
        self.crew, self.desk = 'SYNTHETIC-NOTICE-CREW', 'SYNTHETIC-NOTICE-DESK'
        self.start_runtime(self.root/'source.db')
        self.start_health = self.health()

    def start_runtime(self, path):
        self.path = Path(path)
        self.active = CommonRuntime(path, port=0, mode='full')
        self.runtimes.append(self.active)
        self.active.start()

    def close_all(self):
        for runtime in reversed(self.runtimes):
            runtime.close()

    def tearDown(self):
        end = self.api('GET', '/api/system/status')
        self.close_all()
        NOTIFICATION_EVIDENCE[self.id()] = dict(synthetic=True, actual_hospital_requests=0,
            actual_speech_count=0, manual_worker_calls=0, mocked_solver_results=0,
            worker_mode='actual_background_threads', runtime_starts=len(self.runtimes),
            requests=list(self.requests), checks=list(self.checks), publications=list(self.publications),
            workers_start={key: self.start_health['workers'][key]['state'] for key in ('planning', 'inquiry')},
            workers_end={key: end['workers'][key]['state'] for key in ('planning', 'inquiry')},
            workers_stopped=all(not worker.thread.is_alive() for runtime in self.runtimes
                for worker in (runtime.planner, runtime.inquiry_worker)))

    def check(self, name, condition):
        self.assertTrue(condition, name)
        self.checks.append(name)

    def api(self, method, path, body=None, *, status=200, error=None):
        request = Request(self.active.base_url+path, method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=8) as response:
                actual, data = response.status, json.load(response)
        except HTTPError as response:
            with response:
                actual, data = response.code, json.load(response)
        path, keys = _route(path)
        self.requests.append(dict(method=method, path=path, query_keys=keys, status=actual))
        self.assertEqual(actual, status, method+' '+path)
        if error:
            self.assertEqual(data.get('error'), error, 'The error code must follow the notice contract')
        return data

    def wait(self, getter, condition, label, timeout=20):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            result = getter()
            if condition(result):
                return result
            time.sleep(.05)
        self.fail(label)

    def health(self):
        return self.wait(lambda: self.api('GET', '/api/system/status'),
            lambda item: all(item['workers'][key]['state'] == 'RUNNING' for key in ('planning', 'inquiry')),
            'Both actual workers must be RUNNING')

    def common(self, session=None, *, actor=None, **body):
        self.counter += 1
        return dict(actor_id=actor or self.crew, client_request_id='notice-'+str(self.counter),
                    **({'expected_version': session['version']} if session else {}), **body)

    def patient(self):
        return self.api('GET', f'/api/patients/{self.pid}')

    def plan(self, after_revision=-1):
        result = self.wait(lambda: self.api('GET', f'/api/patients/{self.pid}/recommendation'),
            lambda item: item['status'] == 'READY' and item['revision'] > after_revision and
                item['expires_at'] > int(time.time()) and isinstance(item['result'].get('solver'), dict),
            'The actual planner did not publish fresh READY')
        self.publications.append(dict(revision=result['revision'], created_at=result['created_at'],
            expires_at=result['expires_at'], solver_status=result['result']['solver'].get('status')))
        return result

    def saved(self, *tables):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            return {table: conn.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall() for table in tables}

    def event_list(self, hospital=False, *, unacknowledged=False):
        path = (f'/api/hospitals/H04/patients/{self.pid}' if hospital else f'/api/patients/{self.pid}')
        query = dict(actor_id=self.desk if hospital else self.crew, limit=100)
        if unacknowledged:
            query['unacknowledged_only'] = 'true'
        return self.api('GET', path+'/important-events?'+urlencode(query))['items']

    def receipt(self, eid, action, hospital=False, **expectation):
        path = (f'/api/hospitals/H04/patients/{self.pid}' if hospital else f'/api/patients/{self.pid}')
        return self.api('POST', path+f'/important-events/{eid}/{action}',
                        {'actor_id': self.desk if hospital else self.crew}, **expectation)

    def room(self, hospital=False):
        path = f'/api/hospitals/H04/patients/{self.pid}' if hospital else f'/api/patients/{self.pid}'
        return self.api('GET', path+'/conversation')

    def test_actual_review_notices_delivery_ack_restore_and_idempotent_hospital_decision(self):
        session = self.api('POST', '/api/voice/sessions', self.common())['session']
        session = self.api('POST', f'/api/voice/sessions/{session["id"]}/transcripts',
            self.common(session, text=REPORT, mode='auto', is_final=True, utterance_id='synthetic-report'))['session']
        session = self.api('POST', f'/api/voice/sessions/{session["id"]}/confirm',
                           self.common(session, confirmed=True))['session']
        result = self.api('POST', f'/api/voice/sessions/{session["id"]}/submit', self.common(session,
            start_inquiry=True, inquiry_policy='fixed_3', context=dict(consent=True, lat=35.1355, lon=126.86,
                required_capabilities=['응급의학'], resource_demands={'ed_bed': 1})))
        self.pid = result['patient']['id']
        waiting = self.wait(self.patient, lambda patient: len(patient['inquiries']) == 3,
                            'The actual inquiry worker did not send the initial batch')
        inquiry = next(item for item in waiting['inquiries'] if item['hospital_id'] == 'H04')
        self.api('POST', f'/api/inquiries/{inquiry["id"]}/respond',
            dict(action='accept', handled_by=self.desk, expected_version=inquiry['version']))
        self.plan()
        self.api('POST', f'/api/patients/{self.pid}/confirm', {'inquiry_id': inquiry['id']})
        self.api('POST', f'/api/patients/{self.pid}/transport-review', {'enabled': True})
        self.api('POST', f'/api/patients/{self.pid}/depart', {})
        before_plan = self.plan()
        draft = self.api('POST', '/api/voice/sessions', self.common(patient_id=self.pid))['session']
        draft = self.api('PATCH', f'/api/voice/sessions/{draft["id"]}/fields',
            self.common(draft, changes={'pulse': 105}, change_kind='observation'))['session']
        draft = self.api('POST', f'/api/voice/sessions/{draft["id"]}/confirm',
                         self.common(draft, confirmed=True))['session']
        base = f'/api/voice/sessions/{draft["id"]}/transport-change-review'
        preview = self.api('GET', base+'?'+urlencode({'actor_id': self.crew}))['review']
        body = self.common(draft, expected_patient_version=preview['patient_version'],
            review_token=preview['token'], selection=preview['selection'], confirmed=True,
            allow_reduction=True, reason='Synthetic observation review')
        requested = self.api('POST', base+'/request', body)
        duplicate = self.api('POST', base+'/request', copy.deepcopy(body))
        self.check('one_explicit_review_request_survives_an_exact_response_loss_retry',
                   duplicate.get('duplicate') is True and requested['review']['id'] == duplicate['review']['id'])
        core_tables = ('patients', 'inquiries', 'resources', 'voice_sessions', 'important_event_receipts')
        before_get = self.saved(*core_tables)
        crew, hospital = self.event_list(), self.event_list(True)
        requested_event = next(item for item in crew if item['type'] == CHANGE_TYPES[0])
        eid = requested_event['event_id']
        hospital_event = next(item for item in hospital if item['event_id'] == eid)
        for recipient in (False, True):
            room_item = next(item for item in self.room(recipient)['items'] if item.get('event_id') == eid)
            self.check('request_is_readable_in_'+('hospital' if recipient else 'crew')+'_server_conversation',
                       room_item['text'] == requested_event['summary'] and '검토' in room_item['text'])
        self.check('notification_gets_do_not_deliver_acknowledge_or_apply_patient_information',
                   before_get == self.saved(*core_tables) and hospital_event['delivered_at'] is None and
                   requested_event['delivered_at'] is None and self.patient()['extra']['vitals']['hr'] == 90)
        self.receipt(eid, 'acknowledge', True, status=409, error='DELIVERY_REQUIRED')
        self.receipt(eid, 'delivered')
        crew_ack = self.receipt(eid, 'acknowledge')['event']
        hospital_delivery = self.receipt(eid, 'delivered', True)['event']
        self.check('crew_acknowledgement_is_not_hospital_review_or_automatic_rerouting',
                   self.patient()['extra']['vitals']['hr'] == 90 and self.patient()['destination_hospital_id'] == 'H04' and
                   next(item for item in self.event_list(True, unacknowledged=True) if item['event_id'] == eid)['acknowledged_at'] is None)

        backup = backup_database(self.path, self.root/'backup')
        self.active.close()
        target = self.root/'restored.db'
        restored = restore_database(self.root/'backup', target)
        self.check('restore_preserves_snapshot_business_state_including_pending_review_and_receipts',
                   restored['business_state_preserved'] and
                   inspect_state(Path(backup['snapshot_path']))['business_sha256'] == inspect_state(target)['business_sha256'])
        self.start_runtime(target)
        self.health()
        recovered = next(item for item in self.event_list(True, unacknowledged=True) if item['event_id'] == eid)
        crew_recovered = next(item for item in self.event_list() if item['event_id'] == eid)
        self.check('restarted_hospital_recovers_unacknowledged_delivery_with_original_server_timestamps',
                   recovered['delivered_at'] == hospital_delivery['delivered_at'] and recovered['acknowledged_at'] is None and
                   crew_recovered['acknowledged_at'] == crew_ack['acknowledged_at'])
        reviews = self.api('GET', '/api/hospitals/H04/transport-change-reviews')['reviews']
        review = next(item for item in reviews if item['id'] == requested['review']['id'])
        decision = self.common(actor=self.desk, expected_review_version=review['version'],
            expected_patient_version=review['current_patient_version'], decision_token=review['decision_token'],
            decision='keep', confirmed=True, reason='Synthetic current hospital keeps acceptance')
        path = f'/api/hospitals/H04/transport-change-reviews/{review["id"]}/decision'
        applied = self.api('POST', path, decision)
        self.api('POST', path, copy.deepcopy(decision))
        for recipient in (False, True):
            event = next(item for item in self.event_list(recipient) if item['type'] == CHANGE_TYPES[1])
            room_event = next(item for item in self.room(recipient)['items'] if item.get('event_id') == event['event_id'])
            self.check('hospital_keep_decision_is_readable_for_'+('hospital' if recipient else 'crew'),
                       event['summary'] == room_event['text'] and '수용을 유지' in event['summary'])
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            counts = dict(conn.execute('SELECT type,COUNT(*) FROM events WHERE patient_id=? AND type IN (?,?) '
                'GROUP BY type', (self.pid, *CHANGE_TYPES)).fetchall())
        self.check('request_and_successful_decision_retries_log_each_business_event_once',
                   counts == {CHANGE_TYPES[0]: 1, CHANGE_TYPES[1]: 1})
        self.receipt(eid, 'acknowledge', True)
        after_apply = self.saved(*core_tables)
        decided = next(item for item in self.event_list() if item['type'] == CHANGE_TYPES[1])
        self.receipt(decided['event_id'], 'delivered')
        self.receipt(decided['event_id'], 'acknowledge')
        after_receipts = self.saved(*core_tables)
        self.check('acknowledging_the_decision_never_changes_patient_destination_or_other_business_rows',
                   all(after_apply[table] == after_receipts[table] for table in core_tables if table != 'important_event_receipts') and
                   applied['patient']['destination_hospital_id'] == 'H04' and
                   applied['patient']['extra']['vitals']['hr'] == 105)
        fresh = self.plan(after_revision=before_plan['revision'])
        self.check('actual_restored_planner_keeps_the_explicit_destination_after_hospital_apply',
                   fresh['result']['destination_locked'] and fresh['result']['status'] == 'DESTINATION_HELD')


if __name__ == '__main__':
    unittest.main()
