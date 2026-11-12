from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from server import data_provenance as provenance,db,planning,public_data,service,solver_adapter
from server.travel import TravelEstimator

ROOT=Path(__file__).resolve().parents[2]
NOW=1793368800


def hospital(conn,hid='H1',source='public'):
    service.upsert_hospital(conn,dict(id=hid,name='공개기관 저장본' if source=='public' else '가상기관',
        lat=37.5,lon=127.,source=source,capabilities=[],capabilities_status='unknown',source_ids={'nmc':'TEST-PUBLIC'}))


def resource(conn,hid='H1',source='synthetic'):
    service.set_resource(conn,NOW,hid,'ed_bed',4,0,source=source)


def observe(conn,hid='H1'):
    public_data._save_snapshot(conn,hid,'hira_facilities',NOW-86400,None,
        {'facilities':[{'facility_type':'ed_bed','source_field':'emymCnt','reported_count':999,'status':'reported'}]},
        {'fixture':True,'emymCnt':'999'})
    public_data._save_snapshot(conn,hid,'nmc_rltm_beds',NOW-86400,NOW-90000,
        {'resources':[{'resource_type':'ed_bed','source_field':'hvec','reported_available':888,'status':'reported'}]},
        {'fixture':True,'hvec':'888'})


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.conn=db.connect(':memory:');db.init(self.conn)

    def tearDown(self):self.conn.close()

    def test_inventory_is_select_only_and_preserves_unknown_observation_time(self):
        hospital(self.conn);observe(self.conn)
        before=self.conn.total_changes
        denied={sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_ALTER_TABLE,sqlite3.SQLITE_PRAGMA}
        def readonly(action,*_):return sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK
        self.conn.set_authorizer(readonly)
        with patch.object(db,'init',side_effect=AssertionError('no migrations')),patch('socket.create_connection',side_effect=AssertionError('no network')):
            view=provenance.inventory(self.conn,NOW)
        self.conn.set_authorizer(None)
        self.assertEqual(before,self.conn.total_changes)
        item=view['hospitals'][0]
        self.assertEqual([],item['operating_resources'])
        registered=next(v for v in item['public_evidence'] if v['endpoint']=='hira_facilities')
        self.assertIsNone(registered['as_of_utc'])
        self.assertEqual('unknown',registered['time_status'])
        self.assertTrue(registered['collection_is_not_observation_time'])
        self.assertFalse(registered['operating_capacity_projection'])
        self.assertEqual(999,registered['evidence'][0]['reported_count'])
        bed=next(v for v in item['public_evidence'] if v['endpoint']=='nmc_rltm_beds')
        self.assertTrue(bed['stale']);self.assertFalse(bed['patient_acceptance_confirmed'])
        self.assertNotIn('raw_data',json.dumps(view))
        self.assertEqual(0,view['external_api_calls'])

    def test_hospital_input_on_synthetic_identity_is_demo_not_actual_measurement(self):
        hospital(self.conn,source='synthetic');resource(self.conn,source='hospital_input')
        value=provenance.inventory(self.conn,NOW)['hospitals'][0]['operating_resources'][0]
        self.assertEqual('manual_demo_entry',value['origin']['classification'])
        self.assertFalse(value['origin']['verified_real_hospital_measurement'])
        self.assertIsNone(value['as_of_epoch']);self.assertEqual(NOW,value['updated_at'])

    def test_manual_public_resource_is_unverified_and_public_source_table_is_flagged(self):
        hospital(self.conn);resource(self.conn,source='hospital_input')
        value=provenance.inventory(self.conn,NOW)['hospitals'][0]['operating_resources'][0]
        self.assertEqual('manual_unverified_operation',value['origin']['classification'])
        self.conn.execute("UPDATE resources SET source='public'")
        value=provenance.inventory(self.conn,NOW)['hospitals'][0]['operating_resources'][0]
        self.assertTrue(value['public_source_in_operating_table_requires_review'])
        self.assertIsNone(value['capacity_is_public_observation'])

    def test_public_reference_does_not_supply_model_capacity(self):
        hospital(self.conn);observe(self.conn)
        model,_=solver_adapter.build(planning.capture(self.conn,NOW),TravelEstimator(use_kakao=False))
        self.assertEqual([[0,0]],model['capacities'])
        self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM resources').fetchone()[0])

    def test_supported_resource_manifest_and_icu_nicu_delivery_require_review(self):
        scope=provenance.resource_scope()
        self.assertEqual(['ed_bed','special_team'],scope['model_resources'])
        self.assertFalse(scope['facility_registration_is_available_capacity'])
        hospital(self.conn);resource(self.conn)
        for extra_resource in ('icu_bed','nicu_bed','delivery_room','operating_room'):
            pid='TEST-'+extra_resource
            patient=service.create_patient(self.conn,NOW,pid,dict(reporter_role='transporter',consent=True,
                symptoms='합성 검증',priority=4,priority_source='expert',required_capabilities=[],
                resource_demands={'ed_bed':1,extra_resource:1},lat=37.5,lon=127.))
            snapshot=planning.capture(self.conn,NOW)
            model,context=solver_adapter.build(snapshot,TravelEstimator(use_kakao=False))
            self.assertNotIn(pid,[p['id'] for p in model['patients']])
            result=solver_adapter.present(snapshot,model,context,{},dict(test='adapter_scope_check'))[pid]
            self.assertEqual('NEEDS_REVIEW',result['status'])
            self.assertIn('UNSUPPORTED_RESOURCE',result['warnings'])
            self.assertFalse(result['confirmable_now'])
            self.assertEqual(1,result['treatment_requirements']['resource_demands'][extra_resource])

    def test_empty_or_filtered_inventory_and_future_unknown_source(self):
        self.assertEqual([],provenance.inventory(self.conn,NOW)['hospitals'])
        hospital(self.conn);resource(self.conn)
        self.assertEqual([],provenance.inventory(self.conn,NOW,'missing')['hospitals'])
        self.conn.execute('UPDATE resources SET source_observed_at=?',(NOW+100,))
        item=provenance.inventory(self.conn,NOW)['hospitals'][0]['operating_resources'][0]
        self.assertEqual('future',item['time_status']);self.assertTrue(item['stale'])


class FixtureTests(unittest.TestCase):
    def test_new_fixture_copies_public_reference_dates_without_patients_or_operating_projection(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.codex-build') as folder:
            source_path=Path(folder)/'source.db';source=db.connect(str(source_path));db.init(source)
            hospital(source);observe(source);resource(source,source='hospital_input')
            hospital(source,'SYN',source='synthetic');source.close()
            before=hashlib.sha256(source_path.read_bytes()).hexdigest()
            output=Path(folder)/'new-fixture'
            result=provenance.create_fixture(source_path,output,NOW,capacity=3,teams=1)
            self.assertEqual(before,hashlib.sha256(source_path.read_bytes()).hexdigest())
            view=provenance.read_inventory(result['database'],NOW)
            self.assertEqual(1,len(view['hospitals']))
            item=view['hospitals'][0]
            self.assertEqual('public_identity_frozen',item['identity']['origin']['classification'])
            self.assertTrue(item['mixed_public_identity_and_synthetic_operations'])
            self.assertEqual([3,1],[r['capacity'] for r in item['operating_resources']])
            self.assertTrue(all(r['origin']['classification']=='synthetic_fixture' for r in item['operating_resources']))
            self.assertEqual(NOW-86400,item['public_evidence'][0]['fetched_at'])
            self.assertIsNone(item['public_evidence'][0]['as_of_epoch'])
            check=sqlite3.connect(Path(result['database']).as_uri()+'?mode=ro',uri=True)
            try:
                self.assertEqual(0,check.execute('SELECT COUNT(*) FROM patients').fetchone()[0])
                self.assertEqual(0,check.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0])
                with self.assertRaises(sqlite3.OperationalError):check.execute('DELETE FROM resources')
            finally:check.close()
            with self.assertRaises(FileExistsError):provenance.create_fixture(source_path,output,NOW)

    def test_missing_source_is_not_created(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.codex-build') as folder:
            path=Path(folder)/'missing.db'
            with self.assertRaises(FileNotFoundError):provenance.read_inventory(path)
            self.assertFalse(path.exists())


if __name__=='__main__':unittest.main()
