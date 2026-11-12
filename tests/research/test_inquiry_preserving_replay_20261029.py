"""Real QI replay, frozen G2 controls, received-ledger and audit regressions."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from simulation import final_assignment_scenarios_20261028 as scenarios
from simulation.arrival_guard_priority_engine_20261028 import run_guard_replay
from simulation import inquiry_preserving_policy_20261029 as planner
from simulation import inquiry_preserving_replay_20261029 as engine
from simulation.inquiry_preserving_audit_20261029 import audit_replay
from simulation.online_allocation_audit import OnlineAllocationAuditError

Q0=dict(id='QI_Q0',refill='none',retry='baseline')
FA=dict(id='QI_FA',refill='all',retry='baseline')
F1=dict(id='QI_F1',refill='one',retry='baseline')


class InquiryPreservingReplayTests(unittest.TestCase):
    def case(self,family,seed=77):
        return next(s for s in scenarios.build_stress_cases(seed) if s['family']==family)

    def fixture(self,declines=1,all_blocked=False):
        source=self.case('abundant_control');source['patients']=source['patients'][:1]
        source['max_minutes']=45;source['response_environment']={};source['exogenous_events']=[]
        hospital=deepcopy(source['hospitals'][0]);source['hospitals']=[]
        for i in range(1,6):
            h=deepcopy(hospital);h['id']=f'FICT-H{i:02d}';h['name']=f'Synthetic {i}'
            if i<=declines or all_blocked:
                h['initial_occupancy']=[dict(until=35,demand=dict(h['capacity']))]
            source['hospitals'].append(h)
            source['response_environment'][h['id']]=dict(delay=1,silent=i in range(declines+1,4) and not all_blocked)
        source['response_environment']={'*':source['response_environment']}
        return source

    def replay(self,source,spec=FA):
        replay=engine.run_inquiry_replay(source,spec)
        audit=audit_replay(replay,source_scenario=source)
        self.assertTrue(audit['passed'])
        self.assertFalse(audit['allocator_or_inquiry_planner_reexecuted'])
        self.assertFalse(audit['whole_episode_nonregression_verified'])
        self.assertFalse(audit['resource_history_independently_reconstructed'])
        self.assertEqual(set(audit['branch_metrics']),{spec['id']})
        return replay,replay['branches'][0]

    def test_q0_matches_actual_frozen_g2_in_three_main_and_nine_stress_cases(self):
        for source in [scenarios.build_case(s,96) for s in scenarios.STRATA]+scenarios.build_stress_cases(77):
            with self.subTest(source=source['id']):
                before=deepcopy(source);old=run_guard_replay(source)['branches'][0]
                replay,new=self.replay(source,Q0)
                self.assertEqual(source,before)
                self.assertEqual(new['patient_outcomes'],old['patient_outcomes'])
                self.assertEqual([d['assignments'] for d in new['allocation_decisions']],
                                 [d['assignments'] for d in old['allocation_decisions']])
                original_events=[e for e in new['events'] if e['type']!='inquiry_retry_policy']
                self.assertEqual(original_events,old['events'])
                self.assertEqual(new['inquiry_refill_decisions'],[])
                self.assertTrue(all(d['rule']=='G2' for d in new['allocation_decisions']))

    def test_all_six_candidates_audit_across_main_and_stress_without_input_mutation(self):
        for spec in planner.SPECS:
            for source in [scenarios.build_case(s,96) for s in scenarios.STRATA]+scenarios.build_stress_cases(77):
                with self.subTest(policy=spec['id'],source=source['id']):
                    before=deepcopy(source);replay,branch=self.replay(source,spec);saved=deepcopy(replay)
                    audit_replay(replay,source_scenario=source)
                    self.assertEqual(source,before);self.assertEqual(replay,saved)
                    self.assertEqual(branch['policy']['rule'],'G2')

    def test_partial_decline_fills_one_slot_while_two_requests_remain_open(self):
        source=self.fixture();_,branch=self.replay(source)
        record=next(r for r in branch['inquiry_refill_decisions'] if r['actual_sent'])
        self.assertEqual(record['t'],7)
        self.assertEqual(record['actual_sent'],['FICT-H04'])
        self.assertEqual(len(record['snapshot']['pending']),2)
        self.assertEqual(record['snapshot']['max_k'],3)
        self.assertTrue(record['snapshot']['received_this_tick'])
        self.assertFalse(record['snapshot']['has_valid_offer'])

    def test_one_and_all_modes_use_one_or_all_vacant_slots(self):
        source=self.fixture(declines=2)
        _,one=self.replay(source,F1);_,all_=self.replay(source,FA)
        one7=[r for r in one['inquiry_refill_decisions'] if r['t']==7]
        all7=[r for r in all_['inquiry_refill_decisions'] if r['t']==7]
        self.assertEqual(one7[0]['actual_sent'],['FICT-H04'])
        self.assertEqual(all7[0]['actual_sent'],['FICT-H04','FICT-H05'])

    def test_last_wave_completion_does_not_duplicate_base_automatic_sends(self):
        source=self.fixture(declines=3);_,branch=self.replay(source)
        asks=[e['hospital'] for e in branch['events'] if e['type']=='ask' and e['t']==7]
        self.assertEqual(asks,['FICT-H04','FICT-H05'])
        self.assertEqual(len(asks),len(set(asks)))
        self.assertFalse(any(r['actual_sent'] for r in branch['inquiry_refill_decisions'] if r['t']==7))

    def test_valid_offer_and_completed_g2_commit_prevent_extra_inquiries(self):
        source=self.fixture();source['response_environment']['*']['FICT-H02']=dict(delay=1,silent=False)
        _,branch=self.replay(source)
        records=[r for r in branch['inquiry_refill_decisions'] if r['t']==7]
        self.assertTrue(records[0]['snapshot']['has_valid_offer'])
        self.assertEqual(records[0]['actual_sent'],[])
        # All responses at once make the patient's valid offer ready immediately.
        source['response_environment']['*']['FICT-H03']=dict(delay=1,silent=False)
        _,branch=self.replay(source)
        events=branch['events'];commit=next(i for i,e in enumerate(events) if e['type']=='commit')
        phase=next(i for i,e in enumerate(events) if e['type']=='G2_batch_phase_complete')
        refill=next(i for i,e in enumerate(events) if e['type']=='inquiry_refill_policy')
        self.assertLess(commit,phase);self.assertLess(phase,refill)
        self.assertEqual(branch['inquiry_refill_decisions'][0]['actual_sent'],[])

    def test_no_inquiry_for_future_patient_even_though_default_state_is_search(self):
        source=self.fixture();future=deepcopy(source['patients'][0]);future.update(id='FUTURE',occur_t=25,
          dispatch_t=26,ambulance_arrive_t=29,search_start_t=31)
        source['patients'].append(future);_,branch=self.replay(source)
        self.assertTrue(all(e['t']>=31 for e in branch['events'] if e['type']=='ask' and e.get('patient')=='FUTURE'))
        self.assertTrue(all(r['t']>=31 for r in branch['inquiry_refill_decisions'] if r['patient']=='FUTURE'))

    def test_retry_rule_changes_only_new_patient_cooldown_and_uses_received_history(self):
        source=self.fixture(all_blocked=True)
        for retry,expected in [('baseline',3),('fixed_2',2),('history',1)]:
            spec=dict(id='QI_'+retry,refill='none',retry=retry);before=deepcopy(source)
            _,branch=self.replay(source,spec)
            first=branch['inquiry_retry_decisions'][0]
            with self.subTest(retry=retry):
                self.assertEqual(first['t'],8)
                self.assertEqual(first['actual_retry_at'],8+expected)
                self.assertEqual(first['snapshot']['baseline_wait'],3)
                self.assertTrue(all(r['patient']==first['patient'] and r['observed_t']<=8 for r in first['snapshot']['observations']))
                asks=[e['t'] for e in branch['events'] if e['type']=='ask' and e['t']>8]
                self.assertEqual(min(asks),8+expected)
                self.assertEqual(source,before)

    def test_private_response_schedule_never_reaches_either_planner(self):
        source=self.fixture(all_blocked=True);refill_seen=[];retry_seen=[]
        def refill(spec,snapshot):
            refill_seen.append(deepcopy(snapshot))
            self.assertEqual(set(snapshot),planner.REFILL_KEYS)
            self.assertTrue(all(set(p)=={'hospital','submitted_t'} for p in snapshot['pending']))
            self.assertTrue(all(set(p)=={'hospital','travel_minutes'} for p in snapshot['candidates']))
            return planner.decide_refill(spec,snapshot)
        def retry(spec,snapshot):
            retry_seen.append(deepcopy(snapshot))
            self.assertEqual(set(snapshot),planner.RETRY_KEYS)
            self.assertTrue(all(set(p)=={'patient','hospital','submitted_t','observed_t','outcome'} for p in snapshot['observations']))
            return planner.decide_retry(spec,snapshot)
        with patch.object(engine,'decide_refill',side_effect=refill),patch.object(engine,'decide_retry',side_effect=retry):
            self.replay(source,dict(id='QI_FA_RA',refill='all',retry='history'))
        self.assertTrue(refill_seen);self.assertTrue(retry_seen)

    def test_queue_late_reply_cannot_resurrect_timed_out_request(self):
        source=self.fixture();source['inquiry_queue']=dict(enabled=True,workers=1,
          background_jobs=[dict(t=0,hospital='FICT-H04',service_minutes=15)])
        _,branch=self.replay(source)
        late=[e for e in branch['events'] if e['type']=='late_reply' and e['hospital']=='FICT-H04']
        self.assertTrue(late)
        self.assertTrue(any(e['type']=='no_response' and e.get('hospital')=='FICT-H04' for e in branch['events']))
        self.assertFalse(any(e['type']=='accept' and e.get('hospital')=='FICT-H04' and e['t']==late[0]['t'] for e in branch['events']))

    def test_audit_does_not_execute_new_allocator_or_planners(self):
        source=self.fixture(all_blocked=True);replay,_=self.replay(source,dict(id='QI_FA_RA',refill='all',retry='history'))
        with (patch.object(engine,'decide_refill',side_effect=AssertionError('planner executed')),
              patch.object(engine,'decide_retry',side_effect=AssertionError('planner executed'))):
            self.assertTrue(audit_replay(replay,source_scenario=source)['passed'])

    def test_audit_rejects_record_ledger_whitelist_policy_and_phase_tampering(self):
        source=self.fixture(all_blocked=True);spec=dict(id='QI_FA_RA',refill='all',retry='history')
        replay,_=self.replay(source,spec)
        mutations=('received','submitted','refill_private','retry_private','wait','future','orphan_refill',
          'retry_index','refill_index','spec','G2_source','branch_label','phase_order','phase_count','guard')
        for mutation in mutations:
            bad=deepcopy(replay);branch=bad['branches'][0]
            r=branch['inquiry_retry_decisions'][0];f=branch['inquiry_refill_decisions'][0]
            if mutation=='received':branch['received_inquiry_ledger'][0]['submitted_t']+=1
            elif mutation=='submitted':branch['submitted_inquiry_ledger'][0]['attempt']+=1
            elif mutation=='refill_private':f['snapshot']['pending'].append(dict(hospital='FAKE',submitted_t=0,silent=True))
            elif mutation=='retry_private':r['snapshot']['observations'][0]['due_t']=999
            elif mutation=='wait':r['decision']['wait_minutes']+=1
            elif mutation=='future':r['snapshot']['observations'][0]['observed_t']=999
            elif mutation=='orphan_refill':branch['inquiry_refill_decisions'].append(deepcopy(f))
            elif mutation=='retry_index':next(e for e in branch['events'] if e['type']=='inquiry_retry_policy')['record_index']=9999
            elif mutation=='refill_index':next(e for e in branch['events'] if e['type']=='inquiry_refill_policy')['record_index']=9999
            elif mutation=='spec':branch['inquiry_spec']['retry']='fixed_2'
            elif mutation=='G2_source':branch['execution_contract']['allocation_source_sha256']['G2_policy']='0'*64
            elif mutation=='branch_label':branch['id']='G2'
            elif mutation=='phase_order':
                events=branch['events'];i=next(i for i,e in enumerate(events) if e['type']=='G2_batch_phase_complete')
                e=events.pop(i);j=next(j for j,e2 in enumerate(events) if e2['type']=='inquiry_refill_policy');events.insert(j+1,e)
            elif mutation=='phase_count':next(e for e in branch['events'] if e['type']=='G2_batch_phase_complete')['allocation_decision_count']+=1
            elif mutation=='guard':branch['guard_records'][0]['actual_assigned_count']+=1
            with self.subTest(mutation=mutation),self.assertRaises((OnlineAllocationAuditError,AssertionError)):
                audit_replay(bad,source_scenario=source)


if __name__=='__main__':unittest.main()
