"""HTTP treatment review through a real local allocation worker cycle.

All patients/hospitals are synthetic. No UI, microphone, external routing or
real medical service is used. The worker uses its bounded seconds=0 fallback.
"""
import copy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from server import db,service,worker
from server.app import make_handler
from server.travel import TravelEstimator

NOW=2_000_000_000


class Runtime:
    def snapshot(self):return {'workers':{'planning':{'state':'RUNNING'}}}


class VoiceTreatmentReviewHttp(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='treatment-review-http-')
        self.path=str(Path(self.tmp.name)/'fixture.db')
        self.conn=db.connect(self.path);db.init(self.conn)
        self.estimator=TravelEstimator(use_kakao=False)
        for hid,caps,lat in [('TR-GENERAL',['응급의학'],35.161),
                             ('TR-CARDIAC',['응급의학','심장내과'],35.162)]:
            service.upsert_hospital(self.conn,dict(id=hid,name='Synthetic '+hid,lat=lat,lon=126.85,
                capabilities=caps,source='synthetic'))
            for kind in ('ed_bed','special_team'):
                service.set_resource(self.conn,NOW,hid,kind,3,0,source='synthetic')
        self.counter=0;self.actor='TREATMENT-HTTP-QA'
        self.http=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.path,self.estimator,
            clock=lambda:NOW,runtime=Runtime()))
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.origin=f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=3)
        self.conn.close();self.tmp.cleanup()

    def call(self,method,path,body=None):
        request=urllib.request.Request(self.origin+path,method=method,
            data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=8) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as response:
            with response:return response.code,json.load(response)

    def ok(self,method,path,body=None):
        status,data=self.call(method,path,body);self.assertEqual(status,200,data);return data

    def common(self,s=None,**extra):
        self.counter+=1
        return dict(actor_id=self.actor,client_request_id=f'treatment-http-{self.counter}',
                    **({'expected_version':s['version']} if s else {}),**extra)

    def route(self,s,suffix):return f'/api/voice/sessions/{s["id"]}/{suffix}'

    def post(self,s,suffix,**extra):return self.ok('POST',self.route(s,suffix),self.common(s,**extra))

    def say(self,s,text):
        return self.post(s,'transcripts',text=text,is_final=True,mode='auto',utterance_id=f'treatment-u-{self.counter}')['session']

    def create_patient(self):
        s=self.ok('POST','/api/voice/sessions',self.common())['session']
        s=self.say(s,'47세 남성. 흉통. 의식 명료. 호흡 정상. 케이타스 2. 심전도 정상. 맥박 90회.')
        s=self.post(s,'confirm',confirmed=True)['session']
        result=self.post(s,'submit',context={'consent':True,'lat':35.16,'lon':126.85})
        self.pid=result['patient']['id']
        return result['patient']

    def linked(self):
        return self.ok('POST','/api/voice/sessions',self.common(patient_id=self.pid))['session']

    def preview(self,s):return self.ok('GET',self.route(s,'treatment-review')+'?actor_id='+self.actor)['review']

    def apply_body(self,s,review,**extra):
        return self.common(s,expected_patient_version=review['patient_version'],review_token=review['token'],
            confirmed=True,selection=copy.deepcopy(review['selection']),allow_reduction=True,**extra)

    def accept(self,hid):
        p=self.ok('POST',f'/api/patients/{self.pid}/inquiries',{'hospital_ids':[hid]})
        q=next(q for q in p['inquiries'] if q['hospital_id']==hid)
        result=self.ok('POST',f'/api/inquiries/{q["id"]}/respond',
            {'action':'accept','handled_by':'SYNTHETIC-DESK','expected_version':q['version']})
        return result['inquiry']

    def recompute(self):
        self.assertTrue(worker.run_once(self.path,self.estimator,clock=lambda:NOW,seconds=0))
        return self.ok('GET',f'/api/patients/{self.pid}/recommendation')

    def test_state_report_review_approval_changes_conditions_and_recalculates_recommendation(self):
        p=self.create_patient();old=self.accept('TR-GENERAL')
        initial=self.recompute()
        self.assertEqual(initial['status'],'READY')
        self.assertEqual(initial['result']['hospital_id'],'TR-GENERAL')
        s=self.say(self.linked(),'ST 상승 확인됨')
        self.assertTrue(s['treatment_review']['review_required'])
        self.assertEqual(s['treatment_review']['suggested_profile_id'],'TR_CARDIAC_INTERVENTION')
        review=self.preview(s)
        self.assertEqual(service._inquiry(self.conn,old['id'])['state'],'ACCEPTED')
        self.assertEqual(service._patient(self.conn,self.pid)['version'],p['version'])
        self.assertFalse(review['can_apply'])
        s=self.post(s,'confirm',confirmed=True)['session']
        # Confirmation alone does not change values/conditions, so preview token stays usable.
        self.assertEqual(self.preview(s)['token'],review['token'])
        applied=self.ok('POST',self.route(s,'treatment-review/apply'),self.apply_body(s,review))
        self.assertTrue(applied['reevaluation']['requested'])
        self.assertEqual(applied['patient']['extra']['treatment_profile_id'],'TR_CARDIAC_INTERVENTION')
        self.assertEqual(service._inquiry(self.conn,old['id'])['state'],'CANCELLED')
        stale=self.ok('GET',f'/api/patients/{self.pid}/recommendation')
        self.assertEqual(stale['status'],'STALE');self.assertIsNone(stale['result'])
        candidates=self.ok('GET',f'/api/patients/{self.pid}/candidates')
        self.assertEqual([c['hospital_id'] for c in candidates['candidates']],['TR-CARDIAC'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0],1)
        no_acceptance=self.recompute()
        self.assertEqual(no_acceptance['result']['status'],'UNASSIGNED')
        self.accept('TR-CARDIAC')
        recommended=self.recompute()
        self.assertEqual(recommended['status'],'READY')
        self.assertEqual(recommended['result']['hospital_id'],'TR-CARDIAC')
        self.assertTrue(recommended['result']['confirmable_now'])
        self.assertEqual(service._patient(self.conn,self.pid)['state'],'WAITING')
        # The worker recommends; it does not confirm or move the patient.
        self.assertIsNone(service._patient(self.conn,self.pid)['destination_hospital_id'])

    def test_custom_preview_is_read_only_and_requires_exact_selection_on_apply(self):
        self.create_patient();s=self.say(self.linked(),'맥박이 지금 96으로 올랐어')
        s=self.post(s,'confirm',confirmed=True)['session']
        selection={'mode':'custom','required_capabilities':['심장내과'],'resource_demands':{'ed_bed':2}}
        count=self.conn.execute('SELECT COUNT(*) FROM voice_requests').fetchone()[0]
        result=self.post(s,'treatment-review/preview',selection=selection)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM voice_requests').fetchone()[0],count)
        review=result['review'];body=self.apply_body(s,review)
        body['selection']['resource_demands']['ed_bed']=3
        status,error=self.call('POST',self.route(s,'treatment-review/apply'),body)
        self.assertEqual((status,error['error']),(409,'TREATMENT_REVIEW_STALE'))
        self.assertEqual(service._patient(self.conn,self.pid)['resource_demands'],{'ed_bed':1})

    def test_preview_queries_and_apply_types_are_checked_without_changes(self):
        self.create_patient();s=self.linked()
        for suffix in ('', '?actor_id=other', '?actor_id='+self.actor+'&surprise=1'):
            status,_=self.call('GET',self.route(s,'treatment-review')+suffix)
            self.assertIn(status,(400,403))
        s=self.post(s,'confirm',confirmed=True)['session'];review=self.preview(s)
        for changes in ({'expected_patient_version':True},{'allow_reduction':'true'},{'confirmed':1},
                        {'selection':{'mode':'custom','required_capabilities':'심장내과'}},
                        {'selection':{'mode':'custom','resource_demands':{'ed_bed':True}}}):
            body=self.apply_body(s,review);body.update(changes)
            status,_=self.call('POST',self.route(s,'treatment-review/apply'),body)
            self.assertIn(status,(400,409))
        self.assertEqual(service._patient(self.conn,self.pid)['version'],1)

    def test_completed_or_destination_confirmed_patient_cannot_apply_condition_change(self):
        self.create_patient();q=self.accept('TR-GENERAL')
        s=self.say(self.linked(),'ST 상승 확인됨');s=self.post(s,'confirm',confirmed=True)['session']
        self.ok('POST',f'/api/patients/{self.pid}/confirm',{'inquiry_id':q['id']})
        current=self.linked();current=self.say(current,'맥박이 지금 96으로 올랐어')
        current=self.post(current,'confirm',confirmed=True)['session'];review=self.preview(current)
        status,error=self.call('POST',self.route(current,'treatment-review/apply'),self.apply_body(current,review))
        self.assertEqual((status,error['error']),(409,'INVALID_STATE'))
        self.assertEqual(service._patient(self.conn,self.pid)['state'],'CONFIRMED')

    def test_unsupported_resource_is_kept_for_manual_review_not_silently_optimized(self):
        self.create_patient();s=self.linked();s=self.post(s,'confirm',confirmed=True)['session']
        choice={'mode':'custom','resource_demands':{'ed_bed':1,'icu_bed':1}}
        review=self.post(s,'treatment-review/preview',selection=choice)['review']
        self.assertEqual(review['unsupported_solver_resources'],['icu_bed'])
        applied=self.ok('POST',self.route(s,'treatment-review/apply'),self.apply_body(s,review))
        self.assertEqual(applied['patient']['resource_demands']['icu_bed'],1)
        result=self.recompute()
        self.assertEqual(result['result']['status'],'NEEDS_REVIEW')
        self.assertIsNone(result['result']['hospital_id'])


if __name__=='__main__':unittest.main()
