"""Current inquiry/allocation composition, checked with structural seed 7 only."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from server import inquiry_policies
from simulation.joint_policy_engine import (
    CLOCK_ORIGIN_SECONDS, DEFAULT_JOINT_POLICIES, JointPolicyReplay,
    QuarterClockSimulation, QuarterInquiryQueue, decide_joint_protection,
    decide_pending_opportunities,
    run_joint_policy_branch, run_joint_policy_replay, validate_joint_scenario,
)
from simulation.heuristic_assignment import HeuristicAssignmentRule
from simulation.sim import Policy
from simulation.opportunity_guard import decide_protection
from simulation.outage_recovery_engine import DEFAULT_OUTAGE_RECOVERY_POLICIES, run_outage_recovery_replay
from simulation.outage_recovery_scenarios import build_outage_recovery_scenarios


def case(family='future_outage_after_early_offer'):
    return build_outage_recovery_scenarios(7,[family])[0]


def events(branch, kind):
    return [e for e in branch['events'] if e['type']==kind]


class JointPolicyClockTests(unittest.TestCase):
    def test_mro_routes_frozen_report_aggregator_through_new_clock(self):
        names=[cls.__name__ for cls in JointPolicyReplay.mro()]
        self.assertLess(names.index('ReplaySimulation'),names.index('QuarterClockSimulation'))
        self.assertLess(names.index('QuarterClockSimulation'),names.index('Simulation'))

    def test_all_48_original_families_reproduce_exact_outcomes_on_calibration_clock(self):
        spec=next(p for p in DEFAULT_OUTAGE_RECOVERY_POLICIES if p['id']=='outage_recovery_C1')
        for scenario in build_outage_recovery_scenarios(7):
            with self.subTest(family=scenario['family']):
                old=run_outage_recovery_replay(scenario,[spec])['branches'][0]
                new=run_joint_policy_branch(scenario,inquiry='fixed_3',allocation='C1',legacy_clock=True,
                                            pending_protection=False)
                self.assertEqual(old['patient_outcomes'],new['patient_outcomes'])
                self.assertEqual(old['resource_snapshots'],new['resource_snapshots'])

    def test_original_failure_preserves_three_minute_preparation_and_exact_motion(self):
        for allocation in ('H1','C1'):
            branch=run_joint_policy_branch(case(),inquiry='fixed_3',allocation=allocation)
            self.assertEqual(branch['patient_outcomes'][0]['treatment_start_t'],13)
            failure=events(branch,'arrival_unable')[0]
            prepared=events(branch,'backup_preparation_ready')[0]
            self.assertEqual(prepared['t']-failure['t'],3)
            self.assertEqual(prepared['preparation_minutes'],3)
            self.assertEqual(events(branch,'transfer_depart')[0]['t'],prepared['t'])
            for gate in events(branch,'confirmation_gate'):
                if gate['passed']:
                    self.assertGreater(gate['offer_expires_at'],gate['t'])
                    self.assertTrue(all(r['free']>=1 for r in gate['resources'].values()))

    def test_quarter_grid_does_not_claim_grid_gain_as_old_clock_reproduction(self):
        s=case('expiring_waiting_offer')
        old=run_joint_policy_branch(s,inquiry='fixed_3',allocation='C1',legacy_clock=True,pending_protection=False)
        new=run_joint_policy_branch(s,inquiry='fixed_3',allocation='C1')
        self.assertEqual(old['time_step_seconds'],60)
        self.assertEqual(new['time_step_seconds'],15)
        self.assertEqual(old['patient_outcomes'][2]['treatment_start_t']-
                         new['patient_outcomes'][2]['treatment_start_t'],.75)

    def test_fractional_response_schedule_and_elapsed_queue_work(self):
        q=QuarterInquiryQueue(['H'])
        first=q.enqueue('H',0,'A',1,.5)
        second=q.enqueue('H',.25,'B',1,.25)
        self.assertEqual((first['finish_t'],second['start_t'],second['finish_t']),(.5,.5,.75))
        frame=q.snapshot(.5)[0]
        self.assertEqual(frame['queue_wait_total_minutes'],.25)
        self.assertEqual(frame['service_total_minutes'],.5)
        self.assertEqual(frame['completed'],1)
        self.assertEqual(q.report(.75)['aggregate']['service_total_minutes'],.75)

    def test_queue_refuses_nongrid_or_backwards_submissions(self):
        q=QuarterInquiryQueue(['H'])
        for value in (.1,-.25,True,float('nan')):
            with self.assertRaises(ValueError):q.enqueue('H',value,'A',1,.5)
        q.enqueue('H',1,'A',1,.5)
        with self.assertRaises(ValueError):q.enqueue('H',.75,'B',1,.5)

    def test_numeric_guard_matches_frozen_integer_guard(self):
        for s in build_outage_recovery_scenarios(7,['partial_team_missing','confirmation_beyond_grace']):
            b=run_joint_policy_branch(s,inquiry='fixed_3',allocation='H1',legacy_clock=True,pending_protection=False)
            for r in b['protection_records']:
                snapshot=deepcopy(r['snapshot'])
                for waiter in snapshot['waiting']:
                    from simulation.responsive_confirmation_engine import incomplete_confirmation
                    waiter['offers']=[o for o in waiter['offers'] if not incomplete_confirmation(
                        o,r['waiting_since'].get(waiter['id'],snapshot['t']))]
                clocks=[r['waiting_since'],r['held_since']]
                frozen=deepcopy(snapshot);frozen['t']=int(frozen['t'])
                self.assertEqual(decide_joint_protection(snapshot,*clocks),decide_protection(frozen,*clocks))

    def test_censor_removes_future_fractional_completion(self):
        s=case();s['max_minutes']=15
        s['response_matrix']={'*':{'NEAR':{'delay':.25},'FAR':{'delay':.5}}}
        s['exogenous_events']=[];s['patients'][0]['treatment_minutes']=12
        b=run_joint_policy_branch(s,inquiry='fixed_3',allocation='H1')
        self.assertTrue(all(e['t']<=15 for e in b['events']))
        self.assertEqual(b['metrics']['observation_end_t'],15)
        self.assertEqual(b['patient_outcomes'][0]['state'],'INCOMPLETE')
        self.assertIsNone(b['patient_outcomes'][0]['treatment_complete_t'])
        self.assertFalse(events(b,'treatment_complete'))

    def test_integer_feed_source_keys_keep_original_per_source_delays(self):
        s=case('out_of_order')
        b=run_joint_policy_branch(s,inquiry='fixed_3',allocation='H1')
        captures=events(b,'resource_report_captured')
        by_source={e['observed_at']:e for e in captures}
        self.assertEqual(by_source[1]['receive_at'],9)
        self.assertEqual(by_source[4]['receive_at'],4)
        self.assertIs(type(by_source[1]['t']),int)

    def test_after_treatment_inquiry_has_same_tick_start_log_and_final_queue_frame(self):
        # A structural hook reproduces the exact phase ordering of the observed
        # development logging defect without altering policy timing or physics.
        from simulation.total_wait_engine import TotalWaitReplay
        s=case();s['inquiry_queue']=dict(enabled=True,workers=1,background_jobs=[])
        policy=Policy(rule=HeuristicAssignmentRule(time_limit_seconds=.05),k=3,wait=2,notify=True)
        engine=JointPolicyReplay(s,policy,inquiry='fixed_3',pending_protection=False)
        original=TotalWaitReplay._event_review
        emitted=[]
        def after_review(instance,t,phase):
            original(instance,t,phase)
            if t==0 and phase=='after_treatments' and not emitted:
                emitted.append(instance._enqueue_review('FAR',t,'P01',99,.5))
        with patch.object(TotalWaitReplay,'_event_review',after_review):
            engine.after_tick(0)
        job=emitted[0]
        self.assertEqual((job['submitted_t'],job['start_t'],job['finish_t']),(0,0,.5))
        started=[e for e in engine.log if e['type']=='review_started' and e['attempt']==99]
        self.assertEqual(len(started),1)
        self.assertEqual(started[0]['t'],0)
        final=engine.inquiry_frames[-1]
        self.assertEqual(final['t'],0)
        far=next(row for row in final['hospitals'] if row['hospital']=='FAR')
        self.assertEqual((far['submitted'],far['processing'],far['completed']),(1,1,0))
        self.assertEqual(engine._previous_inquiry_frame,engine.inquiry_queue.snapshot(0))
        self.assertNotIn(0,engine._review_events)
        self.assertIn(.5,engine._review_events)


class JointPolicySelectorTests(unittest.TestCase):
    def test_actual_server_v3_is_called_at_exact_15_second_spacing(self):
        real=inquiry_policies.decide
        with patch('simulation.joint_policy_engine.inquiry_policies.decide',wraps=real) as spy:
            b=run_joint_policy_branch(case(),inquiry='v3',allocation='H1')
        self.assertTrue(spy.called)
        self.assertTrue(all(call.args[0]=='observed_v3' for call in spy.call_args_list))
        times=[r['inputs']['now'] for r in b['inquiry_decisions']]
        self.assertIn(CLOCK_ORIGIN_SECONDS+15,times)
        self.assertIn(CLOCK_ORIGIN_SECONDS+45,times)
        self.assertTrue(all(now%15==0 for now in times))

    def test_inputs_project_only_received_history_and_submitted_pending_metadata(self):
        b=run_joint_policy_branch(case(),inquiry='v3',allocation='H1')
        for row in b['inquiry_decisions']:
            inputs=row['inputs']
            self.assertEqual(inputs['now'],CLOCK_ORIGIN_SECONDS+row['t']*60)
            self.assertLessEqual(inputs['max_new'],3-len(inputs['patient_pending']))
            for p in inputs['pending']:
                self.assertEqual(set(p),{'hospital','patient','submitted_at'})
                self.assertLessEqual(p['submitted_at'],inputs['now'])
            self.assertTrue(all(o['observed_at']<=inputs['now'] for o in inputs['observations']))

    def test_initial_past_history_is_shifted_by_common_absolute_origin(self):
        s=case();s['initial_inquiry_history']=[dict(hospital='NEAR',patient='HIST',
            submitted_at=-600,observed_at=-580,outcome='reply')]
        b=run_joint_policy_branch(s,inquiry='v3',allocation='H1')
        observation=b['inquiry_decisions'][0]['inputs']['observations'][0]
        self.assertEqual(observation['submitted_at'],CLOCK_ORIGIN_SECONDS-600)
        self.assertEqual(observation['observed_at'],CLOCK_ORIGIN_SECONDS-580)

    def test_fractional_reply_source_does_not_shorten_movement(self):
        s=case();s['response_matrix']={'*':{'NEAR':{'delay':.25},'FAR':{'delay':.5}}}
        s['exogenous_events']=[]
        b=run_joint_policy_branch(s,inquiry='v3',allocation='H1')
        self.assertEqual(events(b,'accept')[0]['t'],.25)
        leg=next(l for l in b['trajectories'][0]['legs'] if l['kind']=='transport')
        self.assertEqual(leg['planned_arrive_t']-leg['depart_t'],leg['estimated_minutes'])
        self.assertEqual(int(leg['estimated_minutes']),leg['estimated_minutes'])

    def test_all_six_combinations_retain_gate_and_forbid_new_resource_publications(self):
        for inquiry in ('fixed_3','v3','v4'):
            for allocation in ('H1','C1'):
                b=run_joint_policy_branch(case(),inquiry=inquiry,allocation=allocation)
                self.assertEqual(b['metrics']['resource_publications'],0)
                self.assertEqual(b['resource_updates_enabled'],False)
                self.assertTrue(events(b,'confirmation_gate'))
                self.assertEqual(b['stable_ties_enabled'],allocation=='C1')
                self.assertEqual(bool(b['heuristic_records']),allocation=='H1')
                self.assertEqual(bool(b['solver_records']),allocation=='C1')

    def test_no_backup_is_same_common_policy_with_only_three_recovery_flags_off(self):
        b=run_joint_policy_branch(case(),inquiry='fixed_3',allocation='H1',recovery=False)
        self.assertFalse(b['retained_backups_enabled'])
        self.assertFalse(b['transfer_same_tick_enabled'])
        self.assertFalse(b['recovery_followup_enabled'])
        self.assertTrue(b['early_offers_enabled'])
        self.assertTrue(b['event_review_enabled'])
        self.assertTrue(b['protection_enabled'])

    def test_healthy_transit_backup_receipts_do_not_redirect(self):
        for allocation in ('H1','C1'):
            b=run_joint_policy_branch(case('restore_before_failed_arrival'),inquiry='fixed_3',allocation=allocation)
            self.assertEqual(b['patient_outcomes'][0]['treatment_start_t'],4)
            self.assertEqual(b['patient_outcomes'][0]['redirects'],0)
            self.assertFalse(events(b,'transfer_depart'))

    def test_expired_backup_token_is_never_passed_to_commit(self):
        for allocation in ('H1','C1'):
            b=run_joint_policy_branch(case('expired_backup_after_notify'),inquiry='fixed_3',allocation=allocation)
            for gate in events(b,'confirmation_gate'):
                if gate['passed']:self.assertGreater(gate['offer_expires_at'],gate['t'])

    def test_execution_order_is_explicit_and_canonical_output_is_preserved(self):
        policies=deepcopy(list(DEFAULT_JOINT_POLICIES[:2]))
        result=run_joint_policy_replay(case(),policies,execution_order=[p['id'] for p in reversed(policies)])
        self.assertEqual([b['id'] for b in result['branches']],[p['id'] for p in policies])
        self.assertEqual(result['execution_order'],[p['id'] for p in reversed(policies)])

    def test_scenario_and_policy_inputs_are_not_mutated(self):
        s=case();before=deepcopy(s)
        run_joint_policy_branch(s,inquiry='v3',allocation='H1')
        self.assertEqual(s,before)

    def test_new_contract_does_not_claim_global_server_workflow_deadline(self):
        b=run_joint_policy_branch(case(),inquiry='v3',allocation='H1')
        self.assertTrue(b['joint_policy_spec']['selector_only'])
        self.assertFalse(b['joint_policy_spec']['server_workflow_reproduced'])
        self.assertFalse(b['joint_policy_spec']['global_300_second_deadline_applied'])
        self.assertEqual(b['joint_policy_spec']['episode_contact_limit'],10)

    def test_invalid_names_fractional_sources_and_future_history_fail_explicitly(self):
        for kwargs in (dict(inquiry='unknown'),dict(allocation='R1'),dict(recovery=1),dict(legacy_clock=True)):
            with self.assertRaises(ValueError):run_joint_policy_branch(case(),**kwargs)
        s=case();s['response_matrix']={'*':{'NEAR':{'delay':.1}}}
        with self.assertRaises(ValueError):validate_joint_scenario(s)
        s=case();s['initial_inquiry_history']=[dict(hospital='NEAR',submitted_at=0,observed_at=1,outcome='reply')]
        with self.assertRaises(ValueError):validate_joint_scenario(s)


def pending_snapshot():
    return dict(t=.25,held_since={},ready=[dict(id='FLEX',priority=4,request_t=0,
        demand={'ed_bed':1},compatible_hospitals=['H1','H2'],options=['H1'],
        eligible_alternative_hospitals=['H1','H2'],alternative_resource_views={
            'H1':{'ed_bed':dict(status='fresh',free=1)},
            'H2':{'ed_bed':dict(status='fresh',free=1)}},
        valid_offer_expiries={'H1':10})],waiting=[dict(id='SCARCE',priority=4,request_t=0,
        demand={'ed_bed':1},compatible_hospitals=['H1'],pending_inquiries=[dict(hospital='H1',
            attempt=1,submitted_at=0,timeout_at=3,ask_event_sequence=1)],
        bed_view=dict(status='fresh',free=1),resource_views={'ed_bed':dict(status='fresh',free=1)})])


class PendingOpportunityTests(unittest.TestCase):
    def test_known_original_pending_handle_protects_scarce_opportunity(self):
        snapshot=pending_snapshot();result=decide_pending_opportunities(snapshot)
        self.assertEqual(result['held'],['FLEX'])
        self.assertEqual(result['holds'][0]['deadline'],1)
        self.assertEqual(result['holds'][0]['ask_event_sequence'],1)
        self.assertEqual(snapshot['held_since'],{})

    def test_original_submission_deadline_does_not_restart_each_review(self):
        snapshot=pending_snapshot();snapshot['t']=1
        self.assertFalse(decide_pending_opportunities(snapshot)['held'])
        snapshot['t']=.75;snapshot['waiting'][0]['pending_inquiries'][0]['timeout_at']=.75
        self.assertFalse(decide_pending_opportunities(snapshot)['held'])

    def test_victim_lifetime_does_not_restart_with_a_new_waiting_handle(self):
        snapshot=pending_snapshot();snapshot['t']=2
        snapshot['held_since']={'FLEX':.25}
        snapshot['waiting'][0]['pending_inquiries'][0].update(submitted_at=1.5,timeout_at=4)
        self.assertFalse(decide_pending_opportunities(snapshot)['held'])

    def test_no_hold_for_higher_priority_or_no_structural_alternative(self):
        snapshot=pending_snapshot();snapshot['ready'][0]['priority']=5
        self.assertFalse(decide_pending_opportunities(snapshot)['held'])
        snapshot=pending_snapshot();snapshot['ready'][0]['compatible_hospitals']=['H1']
        self.assertFalse(decide_pending_opportunities(snapshot)['held'])

    def test_unknown_short_or_noncompeting_beds_do_not_hold(self):
        for changes in (dict(status='unknown',free=None),dict(status='stale',free=1),
                        dict(status='fresh',free=0),dict(status='fresh',free=2)):
            snapshot=pending_snapshot();snapshot['waiting'][0]['bed_view']=changes
            self.assertFalse(decide_pending_opportunities(snapshot)['held'])

    def test_imminent_offer_expiry_or_missing_pending_does_not_hold(self):
        snapshot=pending_snapshot();snapshot['ready'][0]['valid_offer_expiries']['H1']=.5
        self.assertFalse(decide_pending_opportunities(snapshot)['held'])

    def test_fresh_shortage_of_any_waiter_resource_blocks_pending_hold(self):
        snapshot=pending_snapshot();waiter=snapshot['waiting'][0]
        waiter['demand']['special_team']=1
        waiter['resource_views']['special_team']=dict(status='fresh',free=0)
        self.assertFalse(decide_pending_opportunities(snapshot)['held'])
        waiter['resource_views']['special_team']=dict(status='unknown',free=None)
        self.assertEqual(decide_pending_opportunities(snapshot)['held'],['FLEX'])

    def test_all_structural_alternatives_with_current_fresh_shortage_do_not_hold(self):
        snapshot=pending_snapshot();ready=snapshot['ready'][0]
        ready['alternative_resource_views']['H2']['ed_bed']=dict(status='fresh',free=0)
        ready['eligible_alternative_hospitals']=['H1']
        self.assertFalse(decide_pending_opportunities(snapshot)['held'])
        ready['alternative_resource_views']['H2']['ed_bed']=dict(status='unknown',free=None)
        ready['eligible_alternative_hospitals']=['H1','H2']
        self.assertEqual(decide_pending_opportunities(snapshot)['held'],['FLEX'])

    def test_actual_snapshot_carries_current_required_views_for_both_parties(self):
        from simulation.joint_policy_scenarios import make_scenario
        b=run_joint_policy_branch(make_scenario(7,'scarce_options_staggered_replies'),inquiry='v3',allocation='H1')
        row=next(r for r in b['pending_guard_records'] if r['result']['holds'])
        self.assertEqual(set(row['snapshot']['waiting'][0]['resource_views']),{'ed_bed','special_team'})
        self.assertIn('H02',row['snapshot']['ready'][0]['eligible_alternative_hospitals'])
        self.assertIn('H02',row['snapshot']['ready'][0]['alternative_resource_views'])
        snapshot=pending_snapshot();snapshot['waiting'][0]['pending_inquiries']=[]
        self.assertFalse(decide_pending_opportunities(snapshot)['held'])

    def test_held_patient_uses_actual_selector_for_a_new_alternative(self):
        from simulation.joint_policy_scenarios import make_scenario
        for allocation in ('H1','C1'):
            s=make_scenario(7,'scarce_options_staggered_replies')
            old=run_joint_policy_branch(s,inquiry='v3',allocation=allocation,pending_protection=False)
            new=run_joint_policy_branch(s,inquiry='v3',allocation=allocation,pending_protection=True)
            self.assertEqual(old['patient_outcomes'][1]['treatment_start_t'],24.25)
            self.assertEqual(new['patient_outcomes'][1]['treatment_start_t'],3.5)
            hold=events(new,'pending_opportunity_hold')[0]
            self.assertEqual(hold['patient'],'P01')
            self.assertEqual(hold['waiting_patient'],'P02')
            alternative=[e for e in events(new,'ask') if e['patient']=='P01' and e['hospital']=='H02'][0]
            self.assertEqual(alternative['t'],hold['t'])
            self.assertTrue(any(r['t']==alternative['t'] and 'H02' in r['decision']['send']
                                for r in new['inquiry_decisions']))
            for decision in new['allocation_decisions']:
                for row in decision['inputs']['patients']:
                    self.assertTrue(all(expiry>decision['t'] for expiry in row['valid_offer_expiries'].values()))
            self.assertEqual(new['metrics']['resource_publications'],0)

    def test_expired_guard_deferment_does_not_block_forever_with_silent_scarce_ask(self):
        from simulation.joint_policy_scenarios import make_scenario
        s=make_scenario(7,'scarce_options_staggered_replies')
        s['response_matrix']['P02']={'H01':dict(delay=.25,silent=True)}
        b=run_joint_policy_branch(s,inquiry='v3',allocation='H1')
        holds=events(b,'pending_opportunity_hold')
        self.assertTrue(holds)
        self.assertTrue(all(e['t']<e['submitted_at']+1 and e['t']<e['held_since']+1 for e in holds))
        self.assertIsNotNone(b['patient_outcomes'][0]['treatment_start_t'])


if __name__=='__main__':unittest.main()
