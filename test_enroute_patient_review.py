"""Clinical-change procedure tests: synthetic in-memory data, no routing API."""
import copy
import json
import unittest
from unittest.mock import patch

from server import db, service, voice_input as voice, enroute_patient_review as review
from server import transport_review, transport_reassignment
from server.travel import TravelEstimator

NOW = 2_000_000_000


class EnrouteReviewTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(':memory:'); db.init(self.conn)
        self.counter = 0
        self.estimator = TravelEstimator(use_kakao=False)
        for hid, caps in [('ER-H1',['응급의학']), ('ER-H2',['응급의학','심장내과'])]:
            service.upsert_hospital(self.conn,dict(id=hid,name='Synthetic '+hid,lat=35.17,lon=126.85,
                capabilities=caps,source='synthetic'))
            for resource in ('ed_bed','special_team'):
                service.set_resource(self.conn,NOW,hid,resource,4,0,source='synthetic')
        service.create_patient(self.conn,NOW,'ER-P1',dict(reporter_role='transporter',consent=True,
            lat=35.16,lon=126.85,age=47,symptoms='흉통',consciousness='alert',breathing='normal',
            priority=4,priority_source='expert',resource_demands={'ed_bed':1},required_capabilities=[],
            extra=dict(treatment_profile_id='TR_GENERAL_ED',avpu='A',chief_complaint='cardiac',
                age_group='adult',pre_ktas_level=2,stemi_suspected='no',vitals={'hr':90})))
        inquiries=service.send_inquiries(self.conn,NOW,'ER-P1',['ER-H1'])['inquiries']
        service.respond(self.conn,NOW,inquiries[0]['id'],'accept','desk')
        service.confirm_destination(self.conn,NOW,'ER-P1',inquiries[0]['id'])
        service.depart(self.conn,NOW,'ER-P1',self.estimator)
        transport_review.enable(self.conn,NOW,'ER-P1',True)

    def tearDown(self): self.conn.close()

    def common(self,session=None,actor='crew',**body):
        self.counter+=1
        return dict(actor_id=actor,client_request_id=f'er-{self.counter}',
            **({'expected_version':session['version']} if session else {}),**body)

    def draft(self,changes=None):
        s=voice.create(self.conn,NOW,self.common(patient_id='ER-P1'))['session']
        s=voice.edit(self.conn,NOW,s['id'],self.common(s,changes=changes or {'pulse':105},change_kind='observation'))['session']
        return voice.confirm(self.conn,NOW,s['id'],self.common(s,confirmed=True))['session']

    def request(self,changes=None,selection=None):
        s=self.draft(changes)
        p=review.preview(self.conn,NOW,s['id'],'crew',selection)['review']
        body=self.common(s,expected_patient_version=p['patient_version'],review_token=p['token'],
            selection=p['selection'],confirmed=True,allow_reduction=True,reason='새 관찰 확인')
        return s,body,review.request(self.conn,NOW,s['id'],body)

    def decide(self,item,choice='keep',**extra):
        return review.decide(self.conn,NOW,item['id'],'ER-H1',self.common(actor='hospital',
            expected_review_version=item['version'],expected_patient_version=item['current_patient_version'],
            decision_token=item['decision_token'],decision=choice,confirmed=True,reason='현재 병원의 명시 검토',**extra))

    def patient(self): return service._patient(self.conn,'ER-P1')

    def test_preview_and_request_do_not_change_patient_or_destination_and_retry_is_idempotent(self):
        before=self.patient(); revision=self.conn.execute('SELECT revision FROM planning_state').fetchone()[0]
        s,body,result=self.request()
        self.assertEqual(before,self.patient())
        self.assertEqual(revision,self.conn.execute('SELECT revision FROM planning_state').fetchone()[0])
        retry=review.request(self.conn,NOW,s['id'],body)
        self.assertTrue(retry['duplicate']); self.assertEqual(result['review']['id'],retry['review']['id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM enroute_patient_reviews').fetchone()[0],1)

    def test_hospital_keep_applies_observation_without_changing_destination_and_retries_once(self):
        s,_,result=self.request(); item=result['review']; before=self.patient()
        body=self.common(actor='hospital',expected_review_version=item['version'],
            expected_patient_version=item['current_patient_version'],decision_token=item['decision_token'],
            decision='keep',confirmed=True,reason='관찰 확인 후 수용 유지')
        applied=review.decide(self.conn,NOW,item['id'],'ER-H1',body)
        self.assertEqual(applied['review']['state'],'APPLIED_KEEP')
        self.assertEqual(self.patient()['extra']['vitals']['hr'],105)
        self.assertEqual(self.patient()['destination_hospital_id'],'ER-H1')
        self.assertEqual(self.patient()['state'],'IN_TRANSIT')
        self.assertFalse(self.patient()['needs_adjustment'])
        self.assertGreater(self.patient()['version'],before['version'])
        self.assertTrue(review.decide(self.conn,NOW,item['id'],'ER-H1',body)['duplicate'])
        self.assertEqual(voice.get(self.conn,NOW,s['id'],'crew')['session']['state'],'SUBMITTED')

    def test_changed_conditions_need_adjust_and_new_acceptance_before_human_reroute(self):
        _,_,result=self.request({'stemi_suspected':'yes'})
        with self.assertRaises(service.ServiceError) as error: self.decide(result['review'])
        self.assertEqual(error.exception.code,'ENROUTE_KEEP_UNSUPPORTED')
        item=review.get(self.conn,NOW,result['review']['id'])
        applied=self.decide(item,'adjust')
        self.assertEqual(applied['review']['state'],'APPLIED_ADJUST')
        self.assertEqual(self.patient()['extra']['treatment_profile_id'],'TR_CARDIAC_INTERVENTION')
        self.assertTrue(self.patient()['needs_adjustment']);self.assertEqual(self.patient()['destination_hospital_id'],'ER-H1')
        self.assertEqual(transport_reassignment.view(self.conn,NOW,'ER-P1')['state'],'QUEUED')
        fresh=service.send_inquiries(self.conn,NOW,'ER-P1',['ER-H2'])['inquiries'][-1]
        with self.assertRaises(service.ServiceError): service.change_destination(self.conn,NOW,'ER-P1',fresh['id'],self.estimator)
        service.respond(self.conn,NOW,fresh['id'],'accept','desk')
        self.assertEqual(self.patient()['destination_hospital_id'],'ER-H1')
        service.change_destination(self.conn,NOW,'ER-P1',fresh['id'],self.estimator)
        self.assertEqual(self.patient()['destination_hospital_id'],'ER-H2')

    def test_reject_preserves_patient_and_draft(self):
        s,_,result=self.request();before=self.patient()
        self.assertEqual(self.decide(result['review'],'reject')['review']['state'],'REJECTED')
        self.assertEqual(before,self.patient());self.assertEqual(voice.get(self.conn,NOW,s['id'],'crew')['session']['state'],'DRAFT')

    def test_edited_draft_old_token_and_wrong_hospital_cannot_apply(self):
        s,_,result=self.request();before=self.patient()
        voice.edit(self.conn,NOW,s['id'],self.common(s,changes={'pulse':115},change_kind='observation'))
        with self.assertRaises(service.ServiceError): self.decide(result['review'])
        self.assertEqual(before,self.patient())
        self.assertIn('VOICE_DRAFT_CHANGED',review.get(self.conn,NOW,result['review']['id'])['blockers'])
        with self.assertRaises(service.ServiceError): review.get(self.conn,NOW,result['review']['id'],'ER-H2')

    def test_resource_change_invalidates_hospital_token_and_unverified_keep_is_blocked(self):
        _,_,result=self.request();item=result['review'];before=self.patient()
        service.set_resource(self.conn,NOW,'ER-H1','ed_bed',0,0,source='synthetic')
        with self.assertRaises(service.ServiceError) as error: self.decide(item)
        self.assertEqual(error.exception.code,'ENROUTE_REVIEW_STALE')
        with self.assertRaises(service.ServiceError) as error: self.decide(review.get(self.conn,NOW,item['id']))
        self.assertEqual(error.exception.code,'ENROUTE_KEEP_UNVERIFIED');self.assertEqual(before,self.patient())

    def test_new_request_supersedes_pending_and_old_decision_cannot_apply(self):
        _,_,first=self.request();_,_,second=self.request({'pulse':116})
        self.assertEqual(review.get(self.conn,NOW,first['review']['id'])['state'],'SUPERSEDED')
        self.assertEqual(second['review']['state'],'PENDING')
        with self.assertRaises(service.ServiceError): self.decide(first['review'])

    def test_transaction_failure_rolls_back_patient_session_and_inquiries(self):
        _,_,result=self.request();before=self.patient()
        inquiries=[dict(q) for q in self.conn.execute('SELECT * FROM inquiries')]
        with patch.object(service,'hospital_decision',side_effect=RuntimeError('injected fixture failure')):
            with self.assertRaises(RuntimeError): self.decide(result['review'])
        self.assertEqual(before,self.patient());self.assertEqual(inquiries,[dict(q) for q in self.conn.execute('SELECT * FROM inquiries')])
        self.assertEqual(review.get(self.conn,NOW,result['review']['id'])['state'],'PENDING')

    def test_get_without_new_schema_remains_read_only(self):
        self.assertFalse(review.exists(self.conn))
        before=self.conn.total_changes
        self.assertEqual(review.list_reviews(self.conn,NOW,patient_id='ER-P1'),{'reviews':[]})
        self.assertFalse(review.exists(self.conn));self.assertEqual(before,self.conn.total_changes)

    def test_old_waiting_submit_contract_still_blocks_enroute_direct_apply(self):
        s=self.draft();before=self.patient()
        with self.assertRaises(service.ServiceError) as error:
            voice.submit(self.conn,NOW,s['id'],self.common(s))
        self.assertEqual(error.exception.code,'INVALID_STATE');self.assertEqual(before,self.patient())

    def test_successful_decision_retry_cannot_cross_hospital_scope(self):
        _,_,result=self.request();item=result['review']
        body=self.common(actor='hospital',expected_review_version=item['version'],
            expected_patient_version=item['current_patient_version'],decision_token=item['decision_token'],
            decision='keep',confirmed=True,reason='수용 유지 확인')
        review.decide(self.conn,NOW,item['id'],'ER-H1',body)
        with self.assertRaises(service.ServiceError) as error:
            review.decide(self.conn,NOW,item['id'],'ER-H2',body)
        self.assertEqual(error.exception.status,404)

    def test_unsupported_resource_is_preserved_for_manual_review_and_not_applied(self):
        service.set_resource(self.conn,NOW,'ER-H1','nicu_bed',4,0,source='synthetic')
        _,_,result=self.request(selection={'mode':'custom','resource_demands':{'ed_bed':1,'nicu_bed':1}})
        before=self.patient();item=result['review']
        self.assertIn('UNSUPPORTED_SOLVER_RESOURCE',item['blockers'])
        self.assertEqual(item['proposal']['proposed']['resource_demands']['nicu_bed'],1)
        for decision in ('keep','adjust'):
            with self.subTest(decision=decision),self.assertRaises(service.ServiceError):self.decide(item,decision)
        self.assertEqual(before,self.patient())
        self.assertEqual(self.decide(item,'reject')['review']['state'],'REJECTED')

    def test_arrival_during_pending_review_blocks_application(self):
        _,_,result=self.request()
        service.confirm_arrival(self.conn,NOW,'ER-P1','ER-H1')
        arrived=self.patient()
        with self.assertRaises(service.ServiceError):self.decide(result['review'])
        self.assertEqual(arrived,self.patient())

    def test_pause_resume_or_policy_change_invalidates_hospital_decision_token(self):
        _,_,result=self.request({'stemi_suspected':'yes'});item=result['review'];before=self.patient()
        transport_review.enable(self.conn,NOW,'ER-P1',False)
        transport_review.enable(self.conn,NOW,'ER-P1',True,policy_name='fixed_5')
        self.assertNotEqual(item['decision_token'],review.get(self.conn,NOW,item['id'])['decision_token'])
        with self.assertRaises(service.ServiceError) as error:self.decide(item,'adjust')
        self.assertEqual(error.exception.code,'ENROUTE_REVIEW_STALE');self.assertEqual(before,self.patient())

    def test_pending_review_survives_sqlite_backup_restore_and_preserving_init(self):
        _,_,result=self.request();rid=result['review']['id'];before=review.get(self.conn,NOW,rid)
        restored=db.connect(':memory:')
        try:
            self.conn.backup(restored);db.init(restored)
            self.assertEqual(before,review.get(restored,NOW,rid))
            self.assertEqual(service._patient(restored,'ER-P1'),self.patient())
        finally:restored.close()


if __name__=='__main__': unittest.main()
