"""Trace provenance, no inferred movement, failure inclusion, and state boundaries."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from simulation.voice_replay_adapter_20261030 import convert_run, digest, main


class VoiceReplayAdapterTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parent/'.codex-build/final-readiness-20261030/test-tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.tmp.name)
        case = self.root/'normal_arrival'
        case.mkdir()
        self.path = case/'fixture.db'
        conn = sqlite3.connect(self.path)
        conn.executescript('''
        CREATE TABLE events(id INTEGER,at INTEGER,actor TEXT,patient_id TEXT,hospital_id TEXT,type TEXT,data TEXT);
        CREATE TABLE patients(id TEXT,lat REAL,lon REAL,state TEXT,destination_hospital_id TEXT,needs_adjustment INTEGER,extra TEXT,required_capabilities TEXT,resource_demands TEXT,danger_signs TEXT);
        CREATE TABLE hospitals(id TEXT,name TEXT,lat REAL,lon REAL,capabilities TEXT);
        CREATE TABLE voice_sessions(id TEXT,patient_id TEXT);
        CREATE TABLE voice_utterances(session_id TEXT,utterance_id TEXT,transcript TEXT,recorded_at INTEGER);
        CREATE TABLE voice_requests(response TEXT,recorded_at INTEGER);
        ''')
        initial = dict(id='P', lat=35.16, lon=126.85, state='WAITING', destination_hospital_id=None,
            needs_adjustment=0, extra={}, required_capabilities=['응급의학'], resource_demands={'ed_bed': 1}, danger_signs=[])
        conn.execute('INSERT INTO patients VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('P',35.165,126.85,'ARRIVED','H',0,'{}','["응급의학"]','{"ed_bed":1}','[]'))
        conn.execute('INSERT INTO hospitals VALUES(?,?,?,?,?)', ('H','합성 병원',35.17,126.85,'["응급의학"]'))
        conn.execute('INSERT INTO voice_sessions VALUES(?,?)', ('S','P'))
        conn.execute('INSERT INTO voice_utterances VALUES(?,?,?,?)', ('S','U','47세 흉통',100))
        conn.execute('INSERT INTO voice_requests VALUES(?,?)', (json.dumps({'patient':initial}),100))
        events = [(1,100,'patient_created',None,{}), (2,100,'inquiry_sent','H',{'inquiry_id':1}),
            (3,100,'inquiry_responded','H',{'action':'accept','inquiry_id':1}),
            (4,100,'destination_confirmed','H',{}), (5,101,'departed','H',{'eta_at':500}),
            (6,103,'arrival_confirmed','H',{})]
        conn.executemany('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
            [(i,at,'synthetic','P',hid,kind,json.dumps(payload)) for i,at,kind,hid,payload in events])
        conn.commit();conn.close()
        self.report = {'pass':True,'sources_stable':True,'planned_cases':['normal_arrival'],
            'cases':[{'name':'normal_arrival','pass':True,'solver_runs':[{'case_time':100,'status':'OPTIMAL','fallback':False,'lex_optimal':True}]}]}
        self.save_report()

    def tearDown(self):
        self.tmp.cleanup()

    def save_report(self):
        (self.root/'report.json').write_text(json.dumps(self.report),encoding='utf-8')

    def scenario(self):
        return convert_run(self.root)['scenarios'][0]

    def test_conversion_is_read_only_and_preserves_every_database_event_exactly(self):
        before = digest(self.path)
        first = self.scenario();second = self.scenario()
        self.assertEqual(first, second)
        self.assertEqual(before, digest(self.path))
        events = [e for e in first['branches'][0]['events'] if e.get('stream')=='database_events']
        self.assertEqual([e['server_event_id'] for e in events], [1,2,3,4,5,6])
        self.assertEqual(events[-2]['payload'], {'eta_at':500})
        self.assertEqual([e['source_at'] for e in events], [100,100,100,100,101,103])

    def test_same_second_acceptance_and_confirmation_are_separately_seekable(self):
        branch = self.scenario()['branches'][0]
        accepted = next(e for e in branch['events'] if e['type']=='accept')
        committed = next(e for e in branch['events'] if e['type']=='commit')
        self.assertLess(accepted['t'], committed['t'])
        at_accept = next(f for f in branch['transport_snapshots'] if f['t']==accepted['t'])
        at_commit = next(f for f in branch['transport_snapshots'] if f['t']==committed['t'])
        self.assertIsNone(at_accept['patients'][0]['destination_hospital_id'])
        self.assertEqual(at_commit['patients'][0]['destination_hospital_id'], 'H')

    def test_eta_never_creates_motion_or_arrival_and_initial_location_is_historical(self):
        sc = self.scenario()
        self.assertEqual(sc['scenario']['patients'][0]['location'], [35.16,126.85])
        self.assertEqual(sc['branches'][0]['trajectories'], [])
        conn=sqlite3.connect(self.path)
        conn.execute('DELETE FROM events WHERE type=?',('arrival_confirmed',))
        conn.execute("UPDATE patients SET state='IN_TRANSIT'")
        conn.commit();conn.close()
        self.assertFalse(any(e['type']=='arrive' for e in self.scenario()['branches'][0]['events']))

    def test_failed_and_no_patient_cases_remain_visible(self):
        self.report['pass']=False;self.report['cases'][0].update({'pass':False,'error':'expected failure'})
        self.save_report()
        sc = self.scenario()
        self.assertFalse(sc['scenario']['source']['case_pass'])
        self.assertIn('expected failure',sc['branches'][0]['events'][-1]['reason'])
        conn=sqlite3.connect(self.path);conn.execute('DELETE FROM patients');conn.execute('DELETE FROM voice_requests');conn.commit();conn.close()
        self.assertEqual(self.scenario()['scenario']['patients'],[])

    def test_missing_planned_case_is_rejected_instead_of_silently_selected(self):
        self.report['planned_cases'].append('missing_r1_rejected');self.save_report()
        with self.assertRaises(ValueError):convert_run(self.root)

    def test_captured_road_geometry_and_boundaries_are_preserved(self):
        side = {'snapshots':[{'at':102,'patients':[{'id':'P','state':'IN_TRANSIT','destination_hospital_id':'H'}]}],
            'routing_summary':{'provider':'kakao'},'trajectories':[{'patient':'P','legs':[{
                'source_depart_at':101,'source_arrive_at':103,'origin':[35.16,126.85],
                'destination':[35.17,126.85],'geometry':[[35.16,126.85],[35.165,126.851],[35.17,126.85]],
                'provider':'kakao','geometry_kind':'road','route':{'fetched_at':'2026-10-30T00:00:00Z'}}]}]}
        path=self.path.parent/'replay_observations.json';path.write_text(json.dumps(side),encoding='utf-8')
        sc=self.scenario();leg=sc['branches'][0]['trajectories'][0]['legs'][0]
        self.assertTrue(sc['scenario']['voice_road_replay'])
        self.assertEqual(leg['geometry'],side['trajectories'][0]['legs'][0]['geometry'])
        self.assertEqual((leg['source_depart_at'],leg['source_arrive_at']),(101,103))
        self.assertLess(leg['depart_t'],leg['arrive_t'])
        observed=next(e for e in sc['branches'][0]['events'] if e['type']=='state_observed')
        self.assertEqual(observed['source_at'],102)

    def test_output_cannot_replace_original_or_existing_result(self):
        with self.assertRaises(SystemExit):main(['--input',str(self.root),'--output',str(self.root/'report.json')])

    def test_alternative_acceptance_preserves_old_destination_until_change_event(self):
        conn=sqlite3.connect(self.path)
        conn.execute('DELETE FROM events WHERE id=6')
        events=[(6,102,'transport_recheck_required','H',{}),
            (7,102,'transport_alternative_requested','H',{}),
            (8,102,'inquiry_responded','H2',{'action':'accept','inquiry_id':2}),
            (9,102,'transport_inquiry_ready','H',{'accepted_inquiry_ids':[2]}),
            (10,103,'destination_changed','H2',{'from_hospital':'H','inquiry_id':2})]
        conn.executemany('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
            [(i,at,'synthetic','P',hid,kind,json.dumps(payload)) for i,at,kind,hid,payload in events])
        conn.execute("UPDATE patients SET state='IN_TRANSIT',destination_hospital_id='H2'")
        conn.commit();conn.close()
        branch=self.scenario()['branches'][0]
        ready=next(e for e in branch['events'] if e['type']=='alternative_ready')
        changed=next(e for e in branch['events'] if e['type']=='redirect')
        snapshot=lambda t:next(f['patients'][0] for f in branch['transport_snapshots'] if f['t']==t)
        self.assertEqual(snapshot(ready['t'])['destination_hospital_id'],'H')
        self.assertEqual(snapshot(ready['t'])['transport_reassignment']['state'],'AWAITING_CHANGE')
        self.assertEqual(snapshot(changed['t'])['destination_hospital_id'],'H2')
        self.assertFalse(any(e['type']=='arrive' for e in branch['events']))


if __name__=='__main__':
    unittest.main()
