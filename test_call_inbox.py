"""Hospital-wide call inbox with synthetic cases; reading has no medical effects."""
import json

from server import call_inbox as inbox, call_records as calls, service
from server.service import ServiceError
from test_server import Base, T0


class CallInboxTests(Base):
    def setUp(self):
        super().setUp()
        self.patient('A', name='PRIVATE-A', contact='010-SECRET')
        self.patient('B', name='PRIVATE-B')

    def make(self, pid='A', hid='H1', key='call-1', now=T0 + 30, **changes):
        payload = dict(actor_id='crew-1', client_record_id=key, hospital_id=hid,
                       call_started_at=T0 + 10, call_ended_at=T0 + 20,
                       reported_outcome='checking', counterparty_role='emergency_staff',
                       note=f'shared-with-{hid}')
        payload.update(changes)
        return calls.create(self.conn, now, pid, payload)['record']

    def confirm(self, record, decision='confirm', now=T0 + 40):
        return calls.confirm(self.conn, now, record['patient_id'], record['id'],
                             dict(actor_id='desk-1', client_confirmation_id=f'check-{record["id"]}',
                                  decision=decision, reason='content differs' if decision == 'dispute' else ''),
                             hospital_id=record['hospital_id'])

    def list(self, now=T0 + 100, hid='H1', **kwargs):
        return inbox.list_records(self.conn, now, hid, **kwargs)

    def error(self, code, **kwargs):
        with self.assertRaises(ServiceError) as caught:
            self.list(**kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_empty_existing_hospital_and_unknown_hospital(self):
        result = self.list()
        self.assertEqual(result['records'], [])
        self.assertFalse(result['has_more'])
        self.assertEqual(result['cursor_mode'], 'latest')
        self.error('NOT_FOUND', hid='MISSING')

    def test_records_from_multiple_cases_are_visible_only_to_target_hospital(self):
        first = self.make()
        other = self.make(hid='H2', key='h2', note='OTHER-HOSPITAL-SECRET')
        second = self.make(pid='B', key='case-b')
        result = self.list()
        self.assertEqual([r['id'] for r in result['records']], [second['id'], first['id']])
        self.assertEqual([r['id'] for r in self.list(hid='H2')['records']], [other['id']])
        text = json.dumps(result, ensure_ascii=False)
        for private in ('PRIVATE-A', 'PRIVATE-B', '010-SECRET', 'OTHER-HOSPITAL-SECRET',
                        'request_fingerprint', 'author_scope'):
            self.assertNotIn(private, text)

    def test_descending_before_pagination(self):
        records = [self.make(key=f'call-{i}') for i in range(4)]
        first = self.list(limit=2)
        self.assertEqual([r['id'] for r in first['records']], [r['id'] for r in records[2:][::-1]])
        self.assertTrue(first['has_more'])
        self.assertEqual(first['next_before_id'], records[2]['id'])
        second = self.list(limit=2, before_id=first['next_before_id'])
        self.assertEqual([r['id'] for r in second['records']], [r['id'] for r in records[:2][::-1]])
        self.assertFalse(second['has_more'])
        self.assertIsNone(second['next_before_id'])

    def test_ascending_new_record_cursor_and_empty_page(self):
        first = self.make()
        self.make(hid='H2', key='other')
        second = self.make(pid='B', key='next')
        page = self.list(after_id=0, limit=1)
        self.assertEqual([r['id'] for r in page['records']], [first['id']])
        self.assertEqual(page['cursor_mode'], 'new_records')
        self.assertTrue(page['has_more'])
        self.assertIsNone(page['next_before_id'])
        next_page = self.list(after_id=page['next_after_id'])
        self.assertEqual([r['id'] for r in next_page['records']], [second['id']])
        empty = self.list(after_id=next_page['next_after_id'])
        self.assertEqual(empty['records'], [])
        self.assertEqual(empty['next_after_id'], second['id'])

    def test_confirmation_filters_are_hospital_wide_and_current(self):
        unconfirmed = self.make()
        confirmed = self.make(pid='B', key='confirmed')
        disputed = self.make(key='disputed')
        self.confirm(confirmed)
        self.confirm(disputed, decision='dispute')
        for status, expected in (('unconfirmed', unconfirmed), ('confirmed', confirmed), ('disputed', disputed)):
            with self.subTest(status=status):
                result = self.list(confirmation_status=status)
                self.assertEqual([r['id'] for r in result['records']], [expected['id']])
                self.assertEqual(result['records'][0]['confirmation_status'], status)

    def test_future_confirmation_does_not_change_historical_filter(self):
        record = self.make()
        self.confirm(record, now=T0 + 80)
        before = self.list(now=T0 + 50, confirmation_status='unconfirmed')
        self.assertEqual([r['id'] for r in before['records']], [record['id']])
        self.assertIsNone(before['records'][0]['confirmation'])
        self.assertEqual(self.list(now=T0 + 50, confirmation_status='confirmed')['records'], [])

    def test_current_only_hides_old_record_after_correction_and_preserves_audit(self):
        original = self.make()
        self.confirm(original)
        corrected = self.make(key='corrected', now=T0 + 60, supersedes_id=original['id'], note='corrected')
        self.assertEqual([r['id'] for r in self.list()['records']], [corrected['id']])
        audit = self.list(current_only=False)
        self.assertEqual([r['id'] for r in audit['records']], [corrected['id'], original['id']])
        self.assertEqual(audit['records'][1]['confirmation_status'], 'confirmed')
        self.assertEqual(audit['records'][1]['superseded_by_id'], corrected['id'])
        self.assertEqual(self.list(confirmation_status='confirmed')['records'], [])
        self.assertEqual(self.list(confirmation_status='unconfirmed')['records'][0]['id'], corrected['id'])

    def test_future_record_and_future_correction_do_not_hide_current_original(self):
        original = self.make()
        self.make(pid='B', key='future', now=T0 + 90)
        self.make(key='future-correction', now=T0 + 80, supersedes_id=original['id'])
        historical = self.list(now=T0 + 50)
        self.assertEqual([r['id'] for r in historical['records']], [original['id']])
        self.assertIsNone(historical['records'][0]['superseded_by_id'])

    def test_patient_filter_does_not_reveal_other_case_details(self):
        self.make()
        case_b = self.make(pid='B', key='case-b')
        result = self.list(patient_id='B')
        self.assertEqual([r['id'] for r in result['records']], [case_b['id']])
        self.assertEqual(result['scope'], {'hospital_id': 'H1', 'patient_id': 'B'})
        self.assertEqual(self.list(patient_id='MISSING')['records'], [])

    def test_new_record_cursor_does_not_signal_later_confirmation_changes(self):
        record = self.make()
        cursor = self.list(after_id=0)['next_after_id']
        self.confirm(record)
        self.assertEqual(self.list(after_id=cursor)['records'], [])
        self.assertEqual(self.list(confirmation_status='unconfirmed')['records'], [])
        self.assertTrue(self.list()['stream']['confirmation_changes_require_refresh'])

    def test_author_filter_separates_hospital_actionable_and_paramedic_pending_records(self):
        incoming = self.make()
        own_payload = dict(actor_id='desk-1', client_record_id='hospital-own',
                           call_started_at=T0 + 10, call_ended_at=T0 + 20,
                           reported_outcome='checking', counterparty_role='paramedic')
        own = calls.create(self.conn, T0 + 40, 'A', own_payload, hospital_id='H1')['record']
        actionable = self.list(confirmation_status='unconfirmed', author_role='paramedic')
        self.assertEqual([r['id'] for r in actionable['records']], [incoming['id']])
        awaiting_other_side = self.list(confirmation_status='unconfirmed', author_role='hospital')
        self.assertEqual([r['id'] for r in awaiting_other_side['records']], [own['id']])

    def test_string_query_values_are_normalized(self):
        original = self.make()
        replacement = self.make(key='replacement', now=T0 + 50, supersedes_id=original['id'])
        self.assertEqual([r['id'] for r in self.list(limit='1', after_id='0')['records']], [replacement['id']])
        self.assertEqual(len(self.list(current_only='false')['records']), 2)
        self.assertEqual(len(self.list(current_only='1')['records']), 1)

    def test_invalid_cursors_filters_and_values_are_rejected(self):
        bad = [dict(limit=0), dict(limit=101), dict(limit=True), dict(limit=''),
               dict(before_id=0), dict(after_id=-1), dict(after_id=True),
               dict(after_id='9' * 4301), dict(before_id=1, after_id=0),
               dict(confirmation_status=''), dict(confirmation_status='accepted'),
               dict(confirmation_status=[]), dict(current_only=''), dict(current_only=1),
               dict(author_role=''), dict(author_role='doctor'), dict(author_role=[]),
               dict(current_only=None), dict(patient_id=''), dict(patient_id=' A'),
               dict(patient_id='x' * 129), dict(now=True), dict(now=-1)]
        for values in bad:
            with self.subTest(values={k: str(v)[:30] for k, v in values.items()}):
                self.error('INVALID', **values)

    def test_reading_creates_no_receipts_or_clinical_state_changes(self):
        record = self.make()
        self.confirm(record)
        tables = ('patients', 'inquiries', 'resources', 'events', 'planning_state',
                  'conversation_hospital_sessions', 'work_activities', 'important_event_receipts',
                  'call_records', 'call_record_confirmations')
        def snapshot():
            return {name: [tuple(r) for r in self.conn.execute(f'SELECT * FROM {name} ORDER BY rowid')]
                    for name in tables}
        before = snapshot()
        changes = self.conn.total_changes
        self.list()
        self.list(after_id=0)
        self.list(current_only=False, confirmation_status='confirmed')
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.conn.total_changes, changes)
        self.assertFalse(self.conn.in_transaction)
        self.assertFalse(self.list()['stream']['read_marks_seen'])

    def test_callers_existing_transaction_is_not_committed_or_rolled_back(self):
        self.make()
        before = service._patient(self.conn, 'A')['symptoms']
        self.conn.execute('BEGIN')
        self.conn.execute("UPDATE patients SET symptoms='temporary' WHERE id='A'")
        try:
            self.assertEqual(len(self.list()['records']), 1)
            self.assertTrue(self.conn.in_transaction)
            self.assertEqual(service._patient(self.conn, 'A')['symptoms'], 'temporary')
        finally:
            self.conn.rollback()
        self.assertEqual(service._patient(self.conn, 'A')['symptoms'], before)


if __name__ == '__main__':
    import unittest
    unittest.main()
