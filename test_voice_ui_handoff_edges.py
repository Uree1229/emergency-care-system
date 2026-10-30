"""Adversarial boundaries for permissive guided answers; synthetic text only."""
import unittest
from server import db, voice_input as voice, voice_terms as terms

NOW=2_000_000_000

class VoiceHandoffEdgeTests(unittest.TestCase):
    def test_unit_prefixes_and_count_prose_do_not_supply_measurements(self):
        for text in ('맥박이 회복됐어', '호흡수이 회복됐어', '맥박이 번쩍 올라갔어',
                     '혈당이 도망갔어', '혈압이 한 번 떨어졌어', '혈압이 한번 떨어졌어',
                     '혈압이 한 차례 떨어졌어', '혈압이 한동안 떨어졌어',
                     '혈압 한 두번 떨어졌어', '맥박 이 지금 떨어졌어'):
            with self.subTest(text=text):
                self.assertEqual(terms.extract(text,NOW)['fields'],{})

    def test_bp_pair_with_following_particle_still_has_exact_values(self):
        for text in ('혈압 120에 80이 나왔어', '혈압 120 80이 나왔어',
                     '혈압 120 80 이 나왔어', '혈압 120에80이야'):
            with self.subTest(text=text):
                self.assertEqual(terms.extract(text,NOW)['fields'],{'systolic_bp':120,'diastolic_bp':80})

    def test_third_quantity_never_leaves_a_selected_bp_pair(self):
        for text in ('혈압 120 80 92회', '혈압 120 80 92로 나왔어',
                     '혈압 120 80 92입니다', '혈압 120 80 92요',
                     '혈압 120 80 92라고 했어', '혈압 120 80 92었어', '혈압 120 80 구십이래',
                     '혈압 120에80/60', '혈압 120 80 오', '혈압 120 80 삼'):
            with self.subTest(text=text):
                self.assertFalse({'systolic_bp','diastolic_bp'} & terms.extract(text,NOW)['fields'].keys())

    def test_symptom_polarity_answer_is_not_mistaken_for_a_polite_ending(self):
        question={'id':'symptoms','uncertain_items':['흉통']}
        self.assertEqual(voice._answer_context(question,'아니요'),'흉통 없음')
        self.assertEqual(voice._answer_context(question,'네'),'흉통 있음')

    def test_semantic_normalizations_are_reviewable_but_not_repeated_as_missing(self):
        conn=db.connect(':memory:');db.init(conn)
        try:
            for index,(qid,text,field,value) in enumerate((
                ('pulse','20분임','pulse',20),('pulse','아흔둘분','pulse',92),
                ('respiratory_rate','분당 20분','respiratory_rate',20),
                ('temperature','37도 2','temperature',37.2),
                ('onset_at','30분 전쯤','onset_at',NOW-1800))):
                with self.subTest(text=text):
                    s=voice.create(conn,NOW,dict(actor_id='edge',client_request_id=f'create-{index}'))['session']
                    result=voice.answer_followup(conn,NOW,s['id'],dict(actor_id='edge',client_request_id=f'answer-{index}',
                        expected_version=s['version'],utterance_id=f'utterance-{index}',question_id=qid,text=text))
                    meta=result['session']['field_metadata'][field]
                    self.assertEqual(result['session']['fields'][field],value)
                    self.assertTrue(result['answer']['accepted'])
                    self.assertTrue(result['answer']['review_required'])
                    self.assertTrue(meta['review_required'])
                    self.assertTrue(meta['interpretation_notes'])
                    self.assertEqual(meta['evidence'],[text])
                    self.assertNotIn(qid,[q['id'] for q in result['followup']['items']])
                    self.assertEqual(result['operational_effect'],'draft_only')
                    self.assertIsNone(result['session']['confirmed_version'])
                    self.assertIsNone(result['session']['patient_id'])
        finally:conn.close()

    def test_garbled_states_remain_unresolved_instead_of_becoming_normal(self):
        for qid,text in (('breathing','고급 정장'),('breathing','정상 아님'),
                         ('consciousness','의식은 역류'),('consciousness','명료 아님')):
            with self.subTest(text=text):
                self.assertIsNone(voice._answer_context({'id':qid},text))

    def test_new_parser_revision_keeps_existing_voice_revision_compatible(self):
        cap=voice.capabilities()
        self.assertEqual(cap['voice_revision'],'voice-v2-20261030')
        self.assertEqual(cap['handoff_parser_revision'],'voice-ui-handoff-20261030-v1')

if __name__=='__main__':unittest.main()
