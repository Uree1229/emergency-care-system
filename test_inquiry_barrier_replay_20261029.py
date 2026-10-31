"""Independent QJ primary-wave timing, public-role and audit regressions."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation import inquiry_barrier_replay_20261029 as engine
from simulation import inquiry_preserving_replay_20261029 as qi
from simulation.arrival_guard_priority_engine_20261028 import run_guard_replay
from simulation.online_allocation_audit import OnlineAllocationAuditError


Q0 = dict(id='QJ_Q0', refill='none', retry='baseline')
F1 = dict(id='QJ_F1', refill='one', retry='baseline')
FA = dict(id='QJ_FA', refill='all', retry='baseline')
CORE = ('allow_current_G2_ready', 'reason', 'primary_pending_count',
        'primary_valid_count', 'primary_first_accept_t', 'auxiliary_first_accept_t')


def make_case(delays=None, silent=(), blocked=('H1', 'H3'), timeout=10):
    """Five equidistant synthetic hospitals with an independently specified wave."""
    delays = delays or {'H1':1, 'H2':2, 'H3':2, 'H4':5, 'H5':5}
    hospitals = []
    for index in range(1,6):
        hid = 'H'+str(index)
        hospitals.append(dict(id=hid, name='Synthetic '+hid, lat=35.17, lon=126.85,
            capabilities=['BASIC'], capacity={'ed_bed':1},
            initial_occupancy=[dict(until=100, demand={'ed_bed':1})] if hid in blocked else []))
    return dict(id='qj-unit-wave', family='qj_unit', seed=77,
        hospitals=hospitals, patients=[dict(id='P1', label='Synthetic patient', occur_t=0,
            dispatch_t=0, ambulance_arrive_t=0, search_start_t=0,
            ambulance_origin=[35.16,126.85], location=[35.16,126.85],
            required_capabilities=['BASIC'], resource_demands={'ed_bed':1},
            priority=4, treatment_minutes=8)],
        exogenous_events=[], response_environment={'*':{
            h['id']:dict(delay=delays.get(h['id'],1),silent=h['id'] in silent) for h in hospitals}},
        max_minutes=35, timeout_minutes=timeout, retry_minutes=3, accept_ttl_minutes=10,
        inquiry_queue=dict(enabled=False), travel_error_range=[1.0,1.0])


def audit(replay, source=None):
    from simulation.inquiry_barrier_audit_20261029 import audit_replay
    return audit_replay(replay, source_scenario=source)


def math_snapshot(**changes):
    value = dict(t=10, patient='P1', request_t=0, state='SEARCH',
        pending=[dict(hospital='PRIMARY_OPEN', submitted_t=0, role='primary'),
                 dict(hospital='AUX_OPEN', submitted_t=2, role='auxiliary')],
        valid_offers=[dict(hospital='PRIMARY_VALID', accepted_t=8, role='primary'),
                      dict(hospital='AUX_VALID', accepted_t=4, role='auxiliary')],
        primary_wait_minutes=2, ready_before=False, first_valid_t_before=4,
        barrier_latched_before=True, current_auxiliary_present=True)
    value.update(changes)
    return value


class PrimaryWaveBarrierReplayTests(unittest.TestCase):
    def run_case(self, source, spec=F1):
        before = deepcopy(source)
        replay = engine.run_barrier_replay(source,spec)
        self.assertEqual(source,before)
        saved = deepcopy(replay)
        result = audit(replay,source)
        self.assertTrue(result['passed'])
        self.assertEqual(replay,saved)
        self.assertEqual(set(result['branch_metrics']),{spec['id']})
        self.assertEqual(replay['branches'][0]['policy']['rule'],'G2')
        self.assertFalse(replay['primary_barrier_contract']['whole_episode_nonregression_guaranteed'])
        return replay,replay['branches'][0]

    def commit_time(self,branch,pid='P1'):
        return next(e['t'] for e in branch['events'] if e['type']=='commit' and e['patient']==pid)

    def barrier_at(self,branch,t):
        return next(r for r in branch['primary_barrier_decisions'] if r['t']==t and r['patient']=='P1')

    def test_no_refill_q0_preserves_actual_frozen_G2_semantics(self):
        cases=[make_case(),make_case(silent=('H2','H3'))]
        blocked=make_case(blocked=('H1','H2','H3','H4','H5'))
        for h in blocked['hospitals']:h['initial_occupancy'][0]['until']=15
        cases.append(blocked)
        stream=make_case()
        p2=deepcopy(stream['patients'][0]);p2.update(id='P2',occur_t=1,dispatch_t=1,
            ambulance_arrive_t=2,search_start_t=3,priority=2)
        stream['patients'].append(p2);cases.append(stream)
        queue=make_case()
        queue['inquiry_queue']=dict(enabled=True,workers=1,
            background_jobs=[dict(t=0,hospital='H2',service_minutes=10)])
        cases.append(queue)
        for index,source in enumerate(cases):
            with self.subTest(index=index):
                before=deepcopy(source)
                old=run_guard_replay(source)['branches'][0]
                _,new=self.run_case(source,Q0)
                self.assertEqual(source,before)
                self.assertEqual(new['patient_outcomes'],old['patient_outcomes'])
                self.assertEqual([(d['t'],d['assignments'])for d in new['allocation_decisions']],
                                 [(d['t'],d['assignments'])for d in old['allocation_decisions']])
                events=[{k:v for k,v in e.items()if k!='inquiry_role'}for e in new['events']
                        if e['type']!='inquiry_retry_policy']
                self.assertEqual(events,old['events'])
                self.assertEqual(new['primary_barrier_decisions'],[])
                self.assertTrue(all(r['role']=='primary'for r in new['inquiry_submission_roles']))

    def test_q0_preserves_frozen_G2_in_three_main_and_nine_stress_patterns(self):
        from simulation import final_assignment_scenarios_20261028 as scenarios
        cases=[scenarios.build_case(stratum,96)for stratum in scenarios.STRATA]
        cases+=scenarios.build_stress_cases(77)
        self.assertEqual(len(cases),12)
        for source in cases:
            with self.subTest(case=source['id']):
                before=deepcopy(source)
                original=run_guard_replay(source)['branches'][0]
                _,branch=self.run_case(source,Q0)
                self.assertEqual(source,before)
                self.assertEqual(branch['patient_outcomes'],original['patient_outcomes'])
                self.assertEqual([(d['t'],d['kind'],d['assignments'])for d in branch['allocation_decisions']],
                                 [(d['t'],d['kind'],d['assignments'])for d in original['allocation_decisions']])
                events=[{k:v for k,v in e.items()if k!='inquiry_role'}for e in branch['events']
                        if e['type']!='inquiry_retry_policy']
                self.assertEqual(events,original['events'])
                self.assertEqual(branch['primary_barrier_decisions'],[])

    def test_auxiliary_pending_does_not_delay_completed_primary_wave(self):
        source=make_case() # H1 declines 1; H2 accepts 2; H3 declines 2; aux H4 pending to 6.
        baseline=run_guard_replay(source)['branches'][0]
        _,branch=self.run_case(source)
        self.assertEqual(self.commit_time(baseline),2)
        self.assertEqual(self.commit_time(branch),2)
        r=self.barrier_at(branch,2)
        self.assertEqual(r['decision']['reason'],'primary_wave_complete')
        self.assertEqual(r['decision']['primary_pending_count'],0)
        self.assertTrue(any(p['role']=='auxiliary'for p in r['snapshot']['pending']))
        self.assertTrue(r['decision']['ready_after'])

    def test_only_auxiliary_acceptance_cannot_start_G2_while_primary_is_open(self):
        source=make_case(delays={'H1':1,'H2':1,'H3':1,'H4':1,'H5':1},
                         silent=('H2','H3'),blocked=('H1',),timeout=6)
        _,branch=self.run_case(source)
        for t in range(2,6):
            r=self.barrier_at(branch,t)
            with self.subTest(t=t):
                self.assertFalse(r['decision']['allow_current_G2_ready'])
                self.assertFalse(r['decision']['ready_after'])
                self.assertEqual(r['decision']['reason'],'wait_for_primary_acceptance')
                self.assertIsNone(r['decision']['primary_first_accept_t'])
        self.assertEqual(self.commit_time(branch),6)
        self.assertFalse(any(d['t']<6 and any(p['id']=='P1' for p in d['inputs']['patients'])
                             for d in branch['allocation_decisions']))

    def test_actual_primary_acceptance_clock_wins_over_earlier_auxiliary_acceptance(self):
        source=make_case(delays={'H1':1,'H2':4,'H3':1,'H4':1,'H5':1},
                         silent=('H3',),blocked=('H1',),timeout=10)
        _,branch=self.run_case(source)
        self.assertEqual(self.barrier_at(branch,4)['decision']['primary_first_accept_t'],4)
        r5=self.barrier_at(branch,5);r6=self.barrier_at(branch,6)
        self.assertFalse(r5['decision']['allow_current_G2_ready'])
        self.assertEqual(r5['decision']['reason'],'wait_primary_two_minutes')
        self.assertTrue(r6['decision']['allow_current_G2_ready'])
        self.assertEqual(r6['decision']['reason'],'primary_acceptance_wait_complete')
        self.assertEqual(r6['decision']['primary_pending_count'],1)
        self.assertEqual(self.commit_time(branch),6)

    def test_expiring_auxiliary_offer_cannot_leave_a_stale_primary_wait_clock(self):
        # The aux offer accepted at 2 expires at 12, exactly when the primary
        # offer is received. Its former clock must not allow a commit at 13.
        source=make_case(delays={'H1':1,'H2':12,'H3':1,'H4':1,'H5':1},
                         silent=('H3',),blocked=('H1',),timeout=20)
        original=run_guard_replay(source)['branches'][0]
        _,branch=self.run_case(source)
        self.assertEqual(self.commit_time(original),14)
        self.assertEqual(self.commit_time(branch),14)

    def test_all_primary_complete_releases_aux_offer_despite_another_aux_request_open(self):
        source=make_case(delays={'H1':1,'H2':1,'H3':1,'H4':1,'H5':8},
                         silent=('H3',),blocked=('H1','H2'),timeout=6)
        _,branch=self.run_case(source,FA)
        self.assertEqual(self.commit_time(branch),6)
        r=self.barrier_at(branch,6)
        self.assertEqual(r['decision']['primary_pending_count'],0)
        self.assertEqual(r['decision']['primary_valid_count'],0)
        self.assertTrue(r['decision']['allow_current_G2_ready'])
        self.assertTrue(any(p['hospital']=='H5' and p['role']=='auxiliary'for p in r['snapshot']['pending']))

    def test_new_aux_only_offer_collects_two_minutes_after_primary_completion(self):
        source=make_case(delays={'H1':1,'H2':1,'H3':1,'H4':5,'H5':8},
                         silent=('H3',),blocked=('H1','H2'),timeout=6)
        _,branch=self.run_case(source,FA)
        for t in (6,7):
            r=self.barrier_at(branch,t)
            with self.subTest(t=t):
                self.assertFalse(r['decision']['allow_current_G2_ready'])
                self.assertEqual(r['decision']['reason'],'collect_auxiliary_offers_after_primary_completion')
                self.assertEqual(r['decision']['auxiliary_first_accept_t'],6)
        self.assertEqual(self.commit_time(branch),8)

    def test_role_rows_are_actual_ask_events_and_normal_retries_stay_primary(self):
        source=make_case(silent=('H2','H3'),blocked=('H1','H2','H3','H4','H5'),timeout=6)
        for h in source['hospitals']:h['initial_occupancy'][0]['until']=20
        _,branch=self.run_case(source)
        rows=[dict(patient=e['patient'],hospital=e['hospital'],submitted_t=e['t'],
                   attempt=e['attempt'],role=e['inquiry_role'])for e in branch['events']if e['type']=='ask']
        self.assertEqual(rows,branch['inquiry_submission_roles'])
        self.assertTrue(any(r['role']=='auxiliary' for r in rows))
        self.assertTrue(any(r['attempt']>1 and r['role']=='primary'for r in rows))
        self.assertTrue(all(r['role']=='primary'for r in rows if r['submitted_t']==0))

    def test_retry_modes_are_rejected_and_replay_does_not_mutate_the_caller(self):
        source=make_case();before=deepcopy(source)
        for retry in ('fixed_2','history'):
            with self.subTest(retry=retry),self.assertRaises(ValueError):
                engine.run_barrier_replay(source,dict(id='QJ_BAD',refill='one',retry=retry))
        self.assertEqual(source,before)

    def test_barrier_adapter_reads_only_public_hospital_roles_and_actual_acceptance(self):
        class Ask(dict):
            def __getitem__(self,key):
                if key!='hid':raise AssertionError('private scheduled ask read: '+key)
                return super().__getitem__(key)
        class Accepted(dict):
            def __getitem__(self,key):
                if key!='V':raise AssertionError('stale/unknown acceptance read: '+key)
                return super().__getitem__(key)
        instance=object.__new__(engine.PrimaryWaveReplay)
        p=SimpleNamespace(id='P1',t_req=0,state='SEARCH',
            asks=[Ask(hid='P'),Ask(hid='A')],valid={'V':20},accepted_at=Accepted(V=4),first_valid_t=2)
        instance._latest_submission={('P1','P'):dict(submitted_t=0),('P1','A'):dict(submitted_t=1)}
        instance._latest_role={('P1','P'):dict(role='primary'),('P1','A'):dict(role='auxiliary'),
                               ('P1','V'):dict(role='primary')}
        instance._barrier_latched={'P1'}
        instance.ready=[];instance.primary_barrier_decisions=[]
        instance.hospitals={'V':SimpleNamespace(can_accept=lambda patient:True)}
        observed=[];instance.ev=lambda t,kind,**data:observed.append((t,kind,data))
        with patch.object(qi.InquiryPreservingReplay,'resolve_asks',return_value=None):
            engine.PrimaryWaveReplay.resolve_asks(instance,p,6)
        r=instance.primary_barrier_decisions[0]
        self.assertEqual(r['decision']['primary_first_accept_t'],4)
        self.assertTrue(r['decision']['ready_after'])
        self.assertEqual(p.first_valid_t,4)
        self.assertEqual([q.id for q,_ in instance.ready],['P1'])
        self.assertEqual([kind for _,kind,_ in observed],['inquiry_primary_barrier_review','inquiry_primary_barrier'])

    def test_audit_rejects_role_timing_ready_record_and_order_tampering(self):
        source=make_case(delays={'H1':1,'H2':4,'H3':1,'H4':1,'H5':1},
                         silent=('H3',),blocked=('H1',),timeout=10)
        replay,branch=self.run_case(source)
        self.assertTrue(branch['primary_barrier_decisions'])
        mutations=('role_ledger','role_event','snapshot_role','acceptance_time','private_pending',
            'future_acceptance','allow','ready','latch','latch_after','orphan','record_index','review_missing',
            'apply_before_review','phase','contract','original_arm_label')
        for mutation in mutations:
            bad=deepcopy(replay);b=bad['branches'][0];r=b['primary_barrier_decisions'][0]
            if mutation=='role_ledger':next(x for x in b['inquiry_submission_roles']if x['role']=='auxiliary')['role']='primary'
            elif mutation=='role_event':next(e for e in b['events']if e['type']=='ask'and e['inquiry_role']=='auxiliary')['inquiry_role']='primary'
            elif mutation=='snapshot_role':r['snapshot']['pending'][0]['role']='auxiliary'
            elif mutation=='acceptance_time':r['snapshot']['valid_offers'][0]['accepted_t']-=1
            elif mutation=='private_pending':r['snapshot']['pending'][0]['silent']=False
            elif mutation=='future_acceptance':r['snapshot']['valid_offers'][0]['accepted_t']=999
            elif mutation=='allow':r['decision']['allow_current_G2_ready']=not r['decision']['allow_current_G2_ready']
            elif mutation=='ready':r['decision']['ready_after']=not r['decision']['ready_after']
            elif mutation=='latch':r['snapshot']['barrier_latched_before']=False
            elif mutation=='latch_after':r['decision']['barrier_latched_after']=not r['decision']['barrier_latched_after']
            elif mutation=='orphan':b['primary_barrier_decisions'].append(deepcopy(r))
            elif mutation=='record_index':next(e for e in b['events']if e['type']=='inquiry_primary_barrier')['record_index']=999
            elif mutation=='review_missing':b['events']=[e for e in b['events']if e['type']!='inquiry_primary_barrier_review']
            elif mutation=='apply_before_review':
                events=b['events'];i=next(i for i,e in enumerate(events)if e['type']=='inquiry_primary_barrier')
                apply=events.pop(i);j=next(j for j,e in enumerate(events)if e['type']=='inquiry_primary_barrier_review');events.insert(j,apply)
            elif mutation=='phase':r['phase']='after_G2_batch'
            elif mutation=='contract':bad['primary_barrier_contract']['version']='FAKE'
            elif mutation=='original_arm_label':b['id']='G2'
            with self.subTest(mutation=mutation),self.assertRaises((OnlineAllocationAuditError,AssertionError,ValueError)):
                audit(bad,source)

    def test_audit_requires_the_clock_repair_even_after_last_current_auxiliary_expired(self):
        source=make_case(delays={'H1':1,'H2':12,'H3':1,'H4':1,'H5':1},
                         silent=('H3',),blocked=('H1',),timeout=20)
        replay,branch=self.run_case(source)
        repairs=[(i,r)for i,r in enumerate(branch['primary_barrier_decisions'])
                 if not r['snapshot']['current_auxiliary_present'] and r['snapshot']['barrier_latched_before']]
        self.assertEqual(len(repairs),1)
        index,repair=repairs[0]
        self.assertEqual(repair['t'],12)
        bad=deepcopy(replay);b=bad['branches'][0]
        b['primary_barrier_decisions'].pop(index)
        b['events']=[e for e in b['events']if not(e['type']in('inquiry_primary_barrier_review','inquiry_primary_barrier')
            and e['record_index']==index)]
        for e in b['events']:
            if e['type']in('inquiry_primary_barrier_review','inquiry_primary_barrier') and e['record_index']>index:
                e['record_index']-=1
        with self.assertRaisesRegex(OnlineAllocationAuditError,'missing barrier review'):
            audit(bad,source)


class PrimaryBarrierIndependentMathTests(unittest.TestCase):
    def compute(self,snapshot):
        from simulation.inquiry_barrier_audit_20261029 import barrier_math
        return barrier_math(snapshot)

    def test_truth_table_and_exact_two_minute_boundary(self):
        tests=[(math_snapshot(),True,'primary_acceptance_wait_complete',8),
            (math_snapshot(t=9),False,'wait_primary_two_minutes',8),
            (math_snapshot(valid_offers=[dict(hospital='AUX_VALID',accepted_t=4,role='auxiliary')]),
             False,'wait_for_primary_acceptance',None),
            (math_snapshot(pending=[dict(hospital='AUX_OPEN',submitted_t=2,role='auxiliary')]),
             True,'primary_wave_complete',8),
            (math_snapshot(pending=[],valid_offers=[dict(hospital='AUX_VALID',accepted_t=4,role='auxiliary')]),
             True,'primary_wave_complete',None),
            (math_snapshot(pending=[dict(hospital='AUX_OPEN',submitted_t=2,role='auxiliary')],
                           valid_offers=[dict(hospital='AUX_VALID',accepted_t=9,role='auxiliary')]),
             False,'collect_auxiliary_offers_after_primary_completion',None),
            (math_snapshot(t=11,pending=[dict(hospital='AUX_OPEN',submitted_t=2,role='auxiliary')],
                           valid_offers=[dict(hospital='AUX_VALID',accepted_t=9,role='auxiliary')]),
             True,'primary_wave_complete',None)]
        for snapshot,allow,reason,first in tests:
            before=deepcopy(snapshot)
            with self.subTest(snapshot=snapshot):
                result=self.compute(snapshot)
                self.assertEqual(result['allow_current_G2_ready'],allow)
                self.assertEqual(result['reason'],reason)
                self.assertEqual(result['primary_first_accept_t'],first)
                self.assertEqual(set(result),set(CORE))
                self.assertEqual(snapshot,before)

    def test_auxiliary_acceptance_or_contaminated_base_clock_cannot_change_primary_gate(self):
        original=math_snapshot(t=9)
        expected=self.compute(original)
        for accepted in (0,3,8,9):
            candidate=deepcopy(original);candidate['valid_offers'][1]['accepted_t']=accepted
            for clock in (0,5,9):
                candidate['first_valid_t_before']=clock
                with self.subTest(accepted=accepted,clock=clock):
                    actual=self.compute(candidate)
                    self.assertEqual({k:v for k,v in actual.items()if k!='auxiliary_first_accept_t'},
                                     {k:v for k,v in expected.items()if k!='auxiliary_first_accept_t'})
                    self.assertEqual(actual['auxiliary_first_accept_t'],accepted)

    def test_only_current_primary_offer_sets_clock_not_a_missing_or_expired_offer(self):
        older=math_snapshot(t=9,valid_offers=[dict(hospital='OLD',accepted_t=5,role='primary'),
            dict(hospital='NEW',accepted_t=8,role='primary'),dict(hospital='A',accepted_t=4,role='auxiliary')])
        self.assertTrue(self.compute(older)['allow_current_G2_ready'])
        older['valid_offers']=older['valid_offers'][1:]
        self.assertFalse(self.compute(older)['allow_current_G2_ready'])
        self.assertEqual(self.compute(older)['primary_first_accept_t'],8)

    def test_latched_wave_can_restore_primary_clock_after_current_auxiliary_disappears(self):
        snap=math_snapshot(t=9,pending=[dict(hospital='P',submitted_t=0,role='primary')],
            valid_offers=[dict(hospital='V',accepted_t=8,role='primary')],
            first_valid_t_before=7,current_auxiliary_present=False)
        result=self.compute(snap)
        self.assertFalse(result['allow_current_G2_ready'])
        self.assertEqual(result['primary_first_accept_t'],8)
        snap['barrier_latched_before']=False
        with self.assertRaises((OnlineAllocationAuditError,AssertionError,ValueError)):
            self.compute(snap)

    def test_unknown_private_future_duplicate_and_malformed_inputs_are_rejected(self):
        bad=[]
        for name,value in [('t',True),('primary_wait_minutes',3),('ready_before',1),
                           ('first_valid_t_before',999),('state','TRANSIT'),
                           ('barrier_latched_before',1),('current_auxiliary_present',0),
                           ('current_auxiliary_present',False)]:
            bad.append(math_snapshot(**{name:value}))
        extra=math_snapshot();extra['response_at']=12;bad.append(extra)
        missing=math_snapshot();del missing['pending'];bad.append(missing)
        bad.append(math_snapshot(valid_offers=[]))
        for change in (dict(role='other'),dict(submitted_t=11),dict(at=20),dict(silent=True)):
            snap=math_snapshot();snap['pending'][0].update(change);bad.append(snap)
        snap=math_snapshot();snap['pending'].append(deepcopy(snap['pending'][0]));bad.append(snap)
        snap=math_snapshot();snap['valid_offers'][0]['accepted_t']=11;bad.append(snap)
        snap=math_snapshot();snap['valid_offers'].append(deepcopy(snap['valid_offers'][0]));bad.append(snap)
        for index,snap in enumerate(bad):
            with self.subTest(index=index),self.assertRaises((OnlineAllocationAuditError,AssertionError,ValueError)):
                self.compute(snap)


if __name__=='__main__':
    unittest.main()
