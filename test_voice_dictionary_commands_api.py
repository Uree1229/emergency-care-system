"""DB-backed command/extraction HTTP regressions. Synthetic data, no microphone."""
import csv
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from server import db, voice_lexicon, voice_terms
from server.app import make_handler
from server.travel import TravelEstimator


class VoiceDictionaryCommandsHttp(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='voice-dictionary-http-')
        self.path=str(Path(self.tmp.name)/'fixture.db')
        self.conn=db.connect(self.path);db.init(self.conn)
        self.counter=0;self.actor='DICTIONARY-TEST'
        self.http=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.path,TravelEstimator(use_kakao=False)))
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.origin=f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=2)
        self.conn.close();self.tmp.cleanup()

    def call(self,method,path,body=None):
        request=urllib.request.Request(self.origin+path,method=method,
            data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=8) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as response:
            with response:return response.code,json.load(response)

    def body(self,s=None,**extra):
        self.counter+=1
        return dict(actor_id=self.actor,client_request_id='dictionary-'+str(self.counter),
                    **({'expected_version':s['version']} if s else {}),**extra)

    def create(self):
        status,data=self.call('POST','/api/voice/sessions',self.body())
        self.assertEqual(status,200,data);return data['session']

    def say(self,s,text):
        body=self.body(s,text=text,mode='auto',is_final=True,utterance_id='u-'+str(self.counter))
        status,data=self.call('POST',f'/api/voice/sessions/{s["id"]}/transcripts',body)
        self.assertEqual(status,200,data);return data

    def test_reviewed_database_alias_is_used_by_http_extraction_without_restart(self):
        s=self.create()
        voice_lexicon.add_alias(self.conn,'project.voice_terms','measurement:pulse','심박동수','project-test-review')
        result=self.say(s,'심박동수 94회')
        self.assertEqual(result['session']['fields']['pulse'],94)
        status,catalog=self.call('GET','/api/voice/terms')
        self.assertEqual(status,200)
        self.assertIn('심박동수',catalog['terms']['measurements']['pulse']['synonyms'])
        self.assertEqual(catalog['dictionary']['storage'],'sqlite')
        # A DB-specific alias must not leak into standalone/default extraction.
        self.assertNotIn('pulse',voice_terms.extract('심박동수 94회',2_000_000_000)['fields'])
        removed=self.say(result['session'],'심박동수 지워줘')
        self.assertEqual(removed['command']['action'],'remove')
        self.assertNotIn('pulse',removed['session']['fields'])

    def test_reviewed_r1_alias_is_used_without_changing_its_target(self):
        code=self.conn.execute("SELECT code FROM voice_terms WHERE source_id='project.voice_r1' AND label='목소리에 반응'").fetchone()[0]
        voice_lexicon.add_alias(self.conn,'project.voice_r1',code,'소리에눈떠요','project-test-review')
        result=self.say(self.create(),'소리에눈떠요')
        self.assertEqual(result['session']['fields']['avpu'],'V')
        self.assertEqual(result['session']['fields']['consciousness'],'impaired')

    def test_external_reference_search_preserves_code_concept_and_does_not_execute(self):
        path=Path(self.tmp.name)/'reference.csv'
        with path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['용어코드','개념코드','한글명','영문명'])
            writer.writerow(['QA-TERM','QA-CONCEPT','가상표준용어','Synthetic reference'])
        voice_lexicon.import_reference_csv(self.conn,path,'test.reference','1','Synthetic reference','https://example.test/source',
            'Synthetic test only','용어코드','한글명','영문명',extra_columns={'concept_code':'개념코드'})
        status,data=self.call('GET','/api/voice/dictionary/search?q=QA-TERM&limit=5')
        self.assertEqual(status,200,data);self.assertEqual(data['total'],1)
        item=data['items'][0]
        self.assertEqual(item['code'],'QA-TERM');self.assertEqual(item['reference_fields']['concept_code'],'QA-CONCEPT')
        self.assertTrue(item['reference_only']);self.assertFalse(item['runtime_enabled'])
        self.assertEqual(self.say(self.create(),'가상표준용어')['session']['fields'],{})

    def test_multi_field_and_r1_readback_do_not_change_draft(self):
        s=self.say(self.create(),'의식 명료. 케이타스 2. 혈압 120에 80. 맥박 90회.')['session']
        for text,expected in [('혈압과 맥박 알려줘',['120','90']),('케이타스 알려줘',['2'])]:
            with self.subTest(text=text):
                result=self.say(s,text)
                self.assertEqual(result['command']['action'],'review')
                self.assertEqual(result['session']['version'],s['version'])
                self.assertEqual(result['session']['fields'],s['fields'])
                for value in expected:self.assertIn(value,result['speak_text'])

    def test_explicit_field_removal_can_be_undone_without_clearing_others(self):
        s=self.say(self.create(),'혈압 120에 80. 맥박 90회.')['session']
        result=self.say(s,'혈압 기록 삭제해 주세요');removed=result['session']
        self.assertEqual(result['command']['action'],'remove')
        self.assertNotIn('systolic_bp',removed['fields']);self.assertNotIn('diastolic_bp',removed['fields'])
        self.assertEqual(removed['fields']['pulse'],90)
        restored=self.say(removed,'방금 입력 취소해 주세요')['session']
        self.assertEqual(restored['fields'],s['fields'])

    def test_multi_field_removal_is_limited_to_named_targets(self):
        s=self.say(self.create(),'혈압 120에 80. 맥박 90회. 체온 37.2도.')['session']
        result=self.say(s,'혈압과 맥박 지워줘')
        self.assertEqual(result['command']['action'],'remove')
        self.assertEqual(result['session']['fields'],{'temperature':37.2})

    def test_negated_quoted_conditional_or_compound_numeric_command_is_not_dictation(self):
        s=self.say(self.create(),'혈압 120에 80. 맥박 90회.')['session']
        for text in ('혈압 100에 70으로 수정하지 마','맥박 100으로 수정하고 전송해',
                     '혈압 100에 70으로 수정하면 알려줘','"맥박 100으로 수정해"라고 했어',
                     '맥박 95라고 말했을 때 수정해줘','맥박 95가 아니라고 했잖아 수정해줘',
                     '맥박 95는 넣지 말고 확인해줘'):
            with self.subTest(text=text):
                result=self.say(s,text)
                self.assertTrue(result['command']['rejected'])
                self.assertEqual(result['operational_effect'],'voice_command_needs_clarification')
                self.assertEqual(result['session']['fields'],s['fields'])
                self.assertEqual(result['session']['version'],s['version'])

    def test_number_in_saved_record_question_never_becomes_a_new_measurement(self):
        s=self.say(self.create(),'맥박 90회')['session']
        for text in ('맥박 95로 저장됐는지 알려줘','맥박 95로 기록되어 있는 값 확인해줘'):
            with self.subTest(text=text):
                result=self.say(s,text)
                self.assertEqual(result['command']['action'],'review')
                self.assertEqual(result['effective_mode'],'command')
                self.assertEqual(result['session']['fields']['pulse'],90)
                self.assertEqual(result['session']['version'],s['version'])
                self.assertIn('90',result['speak_text'])

    def test_named_field_history_filters_revisions_and_does_not_read_only_current_value(self):
        s=self.say(self.create(),'맥박 90회. 혈압 120에 80.')['session']
        s=self.say(s,'맥박이 지금 96으로 올랐어')['session']
        result=self.say(s,'맥박 변경 이력 알려줘')
        self.assertEqual(result['command']['action'],'history')
        self.assertEqual(result['command']['field'],'pulse')
        self.assertEqual(result['session']['version'],s['version'])
        self.assertTrue(result['revisions'])
        self.assertEqual(result['revisions'][-1]['after_fields']['pulse'],96)

    def test_clinical_negation_remains_an_observation(self):
        result=self.say(self.create(),'흉통 있음. 구토 없음.')
        self.assertEqual(result['session']['fields']['symptoms'],['흉통'])
        self.assertEqual(result['session']['fields']['denied_symptoms'],['구토'])

    def test_help_history_repeat_question_and_submit_intent_do_not_submit(self):
        s=self.say(self.create(),'맥박 90회')['session']
        s=self.say(s,'맥박이 지금 96으로 올랐어')['session']
        for text,action in [('사용법 알려줘','help'),('변경 이력 알려줘','history'),
                            ('질문 다시 해줘','repeat_question'),('병원에 보내 주세요','submit')]:
            with self.subTest(text=text):
                result=self.say(s,text)
                self.assertEqual(result['command']['action'],action)
                self.assertEqual(result['session']['version'],s['version'])
                self.assertEqual(result['session']['state'],'DRAFT')
                self.assertIsNone(result['session']['patient_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM patients').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0],0)

    def test_dictionary_endpoint_validation_and_metadata_are_bounded(self):
        for path in ('/api/voice/dictionary/search?q=x&limit=0',
                     '/api/voice/dictionary/search?q=x&offset=-1',
                     '/api/voice/dictionary/search?q=x&surprise=1',
                     '/api/voice/dictionary/search', '/api/voice/dictionary?extra=1'):
            self.assertEqual(self.call('GET',path)[0],400,path)
        status,data=self.call('GET','/api/voice/command-catalog')
        self.assertEqual(status,200);self.assertIn('remove',data['actions'])
        status,data=self.call('GET','/api/voice/dictionary')
        self.assertEqual(status,200);self.assertFalse(data['reference_terms_are_runtime_aliases'])


if __name__=='__main__':unittest.main()
