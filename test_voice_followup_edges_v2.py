"""Guided answers must not silently select one of multiple bare numbers."""
import unittest
from server import db, voice_input as voice

NOW = 2_000_000_000


class FollowupQuantityEdges(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(':memory:')
        db.init(self.conn)
        self.counter = 0

    def tearDown(self):
        self.conn.close()

    def key(self):
        self.counter += 1
        return 'followup-edge-' + str(self.counter)

    def test_multiple_bare_quantities_stay_unresolved_instead_of_picking_one(self):
        for question, text, targets in (
            ('age', '40 50', ('age', 'age_group')),
            ('pulse', '90 100', ('pulse',)),
            ('temperature', '37.2 39.4', ('temperature',)),
            ('glucose', '90 100', ('glucose',)),
            ('blood_pressure', '100/80/60', ('systolic_bp', 'diastolic_bp')),
        ):
            with self.subTest(question=question, text=text):
                s = voice.create(self.conn, NOW, dict(actor_id='review', client_request_id=self.key()))['session']
                result = voice.answer_followup(self.conn, NOW, s['id'], dict(
                    actor_id='review', client_request_id=self.key(), expected_version=s['version'],
                    utterance_id=self.key(), question_id=question, text=text))
                self.assertFalse(result['answer']['accepted'])
                self.assertFalse(set(targets) & result['session']['fields'].keys())
                unresolved = next(q for q in result['followup']['items'] if q['id'] == question)
                self.assertTrue(unresolved['askable'])

    def test_patient_summary_keeps_clarification_flag_without_raw_quote(self):
        s = voice.create(self.conn, NOW, dict(actor_id='review', client_request_id=self.key()))['session']
        s = voice.transcript(self.conn, NOW, s['id'], dict(
            actor_id='review', client_request_id=self.key(), expected_version=s['version'],
            utterance_id=self.key(), is_final=True, text='47세 남성. 혈압120에80.'))['session']
        raw = '혈압이 지금 떨어졌어'
        s = voice.command(self.conn, NOW + 1, s['id'], dict(
            actor_id='review', client_request_id=self.key(), expected_version=s['version'], text=raw))['session']
        self.assertEqual(s['field_metadata']['systolic_bp']['clarification_evidence'], [raw])
        row = self.conn.execute('SELECT * FROM voice_sessions WHERE id=?', (s['id'],)).fetchone()
        projected = voice._patient_patch(row, NOW + 2)['extra']['voice_observations']['field_metadata']
        self.assertTrue(projected['systolic_bp']['needs_clarification'])
        self.assertNotIn('clarification_evidence', projected['systolic_bp'])
        self.assertNotIn('evidence', projected['systolic_bp'])

    def test_other_labelled_observations_are_reported_as_ignored_not_silently_updated(self):
        s = voice.create(self.conn, NOW, dict(actor_id='review', client_request_id=self.key()))['session']
        s = voice.transcript(self.conn, NOW, s['id'], dict(
            actor_id='review', client_request_id=self.key(), expected_version=s['version'],
            utterance_id=self.key(), is_final=True, text='47세 남성. 혈압120에80.'))['session']
        text = '의식 명료. 혈압이 지금100에80까지 떨어졌어'
        result = voice.answer_followup(self.conn, NOW + 1, s['id'], dict(
            actor_id='review', client_request_id=self.key(), expected_version=s['version'],
            utterance_id=self.key(), question_id='consciousness', text=text))
        self.assertTrue(result['answer']['accepted'])
        self.assertEqual(result['answer']['applied_fields'], ['consciousness'])
        self.assertEqual(result['answer']['ignored_fields'], ['diastolic_bp', 'systolic_bp'])
        self.assertEqual(result['session']['fields']['consciousness'], 'alert')
        self.assertEqual(result['session']['fields']['systolic_bp'], 120)
        self.assertTrue(any('질문 대상 외' in w for w in result['session']['warnings']))
        self.assertEqual(result['session']['utterances'][-1]['transcript'], text)


if __name__ == '__main__':
    unittest.main()
