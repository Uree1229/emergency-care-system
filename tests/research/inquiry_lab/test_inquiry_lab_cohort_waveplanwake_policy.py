"""Unchanged Tailwait mathematics and old-review clipping, synthetic inputs only."""
from copy import deepcopy
from functools import lru_cache
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_waveplanwake_policy as policy
from test_inquiry_lab_cohort_tailwait_policy import fixture as old_fixture,invoke as old_invoke,promote
from test_inquiry_lab_horizon_policy import inputs
from test_inquiry_lab_cohort_waveplanwake_workflow import DBFixture,offers
from server import inquiry_observations


@lru_cache(maxsize=3)
def _source(kind='inherited'):
    args,_=old_fixture(kind);return args[:5]+(old_invoke(args),)


def fixture(kind='inherited'):
    args=promote(_source(kind),policy.BASE_POLICY,policy.NAME)
    return args,args[-1]['evidence']['stoploss_anchor_index']


def invoke(args):
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        return policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)


class WaveplanwakePolicyTests(unittest.TestCase):
    def test_only_spec_identity_changes_and_fixed_name_required(self):
        a=policy.original.spec();b=policy.spec()
        for key in ('name','label'):a.pop(key);b.pop(key)
        self.assertEqual(a,b)
        with self.assertRaises(ValueError):policy.spec(policy.BASE_POLICY)

    def test_initial_groups_inherited_new_and_one_tail_preserve_full_Tailwait_actions(self):
        for kind in ('inherited','new','one_tail'):
            args,_=fixture(kind);before=deepcopy(args);result=invoke(args)
            self.assertEqual(args,before);self.assertEqual(result['evidence']['original_tailwait_proposal'],args[-1])
            for value,parent in zip(result['decisions'],args[-1]['decisions']):
                self.assertFalse(value['evidence'].pop('cohort_waveplanwake')['triggered'])
                value['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(value,parent)

    def test_untriggered_H_fallback_is_exact_parent_including_no_candidate_and_budget(self):
        for args in (inputs(),inputs(max_new=0),inputs(candidates=[])):
            parent=policy.original.decide(policy.BASE_POLICY,**deepcopy(args));result=policy.decide(policy.NAME,**deepcopy(args))
            proof=result['evidence'].pop('cohort_waveplanwake');self.assertFalse(proof['triggered'])
            self.assertEqual(proof['original_tailwait_proposal'],parent)
            result['evidence']['policy']=policy.BASE_POLICY;self.assertEqual(result,parent)


class TriggeredMathTests(DBFixture):
    def test_trigger_keeps_genuine_H_math_but_filters_extra_send_and_keeps_review_clip(self):
        from inquiry_lab import cohort_waveplanwake_workflow as w
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P')
        temporal,_=w._time_context(self.conn,1005,snapshot,w._job(self.conn,'P'))
        ledger=inquiry_observations.capture(self.conn,1005,'P');travel={row['hospital_id']:row['travel']['duration_seconds'] for row in offers()['candidates']}
        args=dict(now=1005,candidates=offers()['candidates'][3:],observations=ledger['observations'],pending=ledger['pending'],
            patient_pending=[dict(q,travel_seconds=travel[q['hospital']]) for q in ledger['patient_pending']],max_new=7,last_contact_at=1000,time_context=temporal)
        parent=policy.original.decide(policy.BASE_POLICY,**deepcopy(args));before=deepcopy((args,wake))
        result=policy.decide(policy.NAME,**args,wake_context=wake)
        self.assertEqual((args,wake),before);proof=result['evidence'].pop('cohort_waveplanwake')
        self.assertTrue(proof['triggered']);self.assertEqual(proof['original_tailwait_proposal'],parent)
        self.assertEqual(result['evidence'].pop('prefetch'),proof['prefetch'])
        pre=deepcopy(proof['original_declinewake_proposal']);pre['evidence'].pop('cohort_waveplanwake')
        pre['evidence']['policy']=policy.BASE_POLICY;pre['next_review_at']=parent['next_review_at']
        self.assertEqual(pre,parent)
        self.assertEqual(result['send'],proof['travel_guard']['allowed_sends'])
        self.assertEqual(result['evidence']['sent_hospitals'],result['send'])
        result['send']=parent['send'];result['evidence']['sent_hospitals']=parent['evidence']['sent_hospitals']
        self.assertEqual(result['next_review_at'],1015);result['next_review_at']=parent['next_review_at'];result['evidence']['policy']=policy.BASE_POLICY
        self.assertEqual(result,parent)

    def test_changed_context_prefix_or_identity_is_rejected(self):
        from inquiry_lab import cohort_waveplanwake_workflow as w
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P');temporal,_=w._time_context(self.conn,1005,snapshot,w._job(self.conn,'P'))
        args=inputs(now=1005,time_context=temporal)
        for changed in ('prefix','patient'):
            bad=deepcopy(wake)
            if changed=='prefix':bad['event_prefix_max']+=1
            else:bad['patient_id']='OTHER'
            with self.assertRaises(ValueError):policy.decide(policy.NAME,**args,wake_context=bad)


class PublicTravelGuardTests(unittest.TestCase):
    def call(self,*,new=(50,100,101),own=(100,),ages=None,missing=(),queues=1):
        ages=[10]*len(own) if ages is None else ages
        pending=[dict(hospital='OLD'+str(i),patient='P',submitted_at=1000-age) for i,age in enumerate(ages)]
        captured=[dict(row,travel_seconds=travel) for i,(row,travel) in enumerate(zip(pending,own)) if i not in missing]
        candidates=[dict(hospital_id='NEW'+str(i),travel=dict(duration_seconds=travel)) for i,travel in enumerate(new)]
        for candidate in candidates:
            pending.extend(dict(hospital=candidate['hospital_id'],patient='PEER'+str(i),submitted_at=990) for i in range(queues))
        args=dict(now=1000,patient_id='P',candidates=candidates,pending=pending,patient_pending=captured,
                  proposed_sends=[row['hospital_id'] for row in candidates])
        before=deepcopy(args);result=policy.travel_guard(**args);self.assertEqual(args,before)
        return result

    def test_nearer_and_equal_allowed_farther_rejected_in_parent_order(self):
        guard=self.call()
        self.assertEqual(guard['allowed_sends'],['NEW0','NEW1']);self.assertEqual(guard['rejected_hospital_ids'],['NEW2'])
        self.assertEqual(guard['minimum_fresh_pending_travel_seconds'],100)

    def test_multiple_own_pending_uses_minimum_not_maximum(self):
        guard=self.call(new=(90,100,150),own=(100,200))
        self.assertEqual(guard['allowed_sends'],['NEW0','NEW1'])

    def test_age180_included181_excluded(self):
        self.assertEqual(self.call(new=(101,),ages=[180])['allowed_sends'],[])
        self.assertEqual(self.call(new=(101,),ages=[181])['allowed_sends'],['NEW0'])

    def test_missing_own_travel_from_public_full_pending_blocks_extra_send(self):
        guard=self.call(new=(1,),own=(100,200),missing=(0,))
        self.assertTrue(guard['unknown_fresh_pending_travel']);self.assertEqual(guard['allowed_sends'],[])

    def test_unknown_stale_own_travel_does_not_invent_a_fresh_reference(self):
        guard=self.call(new=(999,),ages=[181],missing=(0,))
        self.assertFalse(guard['unknown_fresh_pending_travel']);self.assertEqual(guard['allowed_sends'],['NEW0'])

    def test_unknown_candidate_travel_blocks_even_without_own_pending(self):
        for value in (None,True,float('inf'),float('nan')):
            # NaN is intentionally not passed through the pure-input equality helper.
            args=dict(now=1000,patient_id='P',pending=[],patient_pending=[],proposed_sends=['N'],
                candidates=[dict(hospital_id='N',travel=dict(duration_seconds=value))])
            guard=policy.travel_guard(**args)
            self.assertEqual(guard['allowed_sends'],[])
            self.assertEqual(guard['rows'][0]['reason'],'unknown_new_travel')

    def test_known_candidate_without_fresh_own_is_allowed(self):
        self.assertEqual(self.call(new=(999,),own=())['allowed_sends'],['NEW0'])

    def test_foreign_or_typed_other_patient_pending_does_not_set_reference(self):
        for pid in ('OTHER',1):
            guard=policy.travel_guard(now=1000,patient_id='1',pending=[dict(patient=pid,hospital='H',submitted_at=990)],
                patient_pending=[],proposed_sends=['N'],candidates=[dict(hospital_id='N',travel=dict(duration_seconds=999))])
            self.assertEqual(guard['allowed_sends'],['N']);self.assertFalse(guard['unknown_fresh_pending_travel'])

    def test_zero_and_fractional_travel_not_rounded(self):
        self.assertEqual(self.call(new=(0,0.5),own=(0,))['allowed_sends'],['NEW0'])
        self.assertEqual(self.call(new=(0.5,0.6),own=(0.5,))['allowed_sends'],['NEW0'])

    def test_duplicate_candidate_source_is_unknown(self):
        candidate=dict(hospital_id='N',travel=dict(duration_seconds=1))
        guard=policy.travel_guard(now=1000,patient_id='P',pending=[],patient_pending=[],proposed_sends=['N'],
            candidates=[candidate,deepcopy(candidate)])
        self.assertEqual(guard['allowed_sends'],[])

    def test_known_farther_candidate_empty_foreign_queue_recalls_original_H_send(self):
        guard=self.call(new=(1000,),own=(100,),queues=0)
        self.assertEqual(guard['allowed_sends'],['NEW0'])
        self.assertEqual(guard['original_travelwake_guard']['allowed_sends'],[])
        self.assertEqual(guard['original_travelwake_guard']['rejected_hospital_ids'],['NEW0'])
        self.assertEqual(guard['queue_exception_applied_hospital_ids'],['NEW0'])
        self.assertTrue(guard['rows'][0]['queue_exception_applied'])

    def test_farther_candidate_foreign_queue_still_keeps_distance_guard(self):
        guard=self.call(new=(101,),queues=2)
        self.assertEqual(guard['allowed_sends'],[])
        self.assertEqual(guard['rows'][0]['foreign_pending_count'],2)
        self.assertFalse(guard['rows'][0]['queue_exception_applied'])

    def test_unknown_own_travel_not_overridden_by_empty_queue(self):
        guard=self.call(new=(1,),missing=(0,),queues=0)
        self.assertEqual(guard['allowed_sends'],[])
        self.assertEqual(guard['queue_exception_applied_hospital_ids'],[])

    def test_unresolved_foreign_queue_age181_still_blocks_distance_exception(self):
        guard=policy.travel_guard(now=1000,patient_id='P',pending=[dict(patient='P',hospital='OLD',submitted_at=990),
            dict(patient='OTHER',hospital='N',submitted_at=819)],patient_pending=[dict(patient='P',hospital='OLD',submitted_at=990,travel_seconds=100)],
            proposed_sends=['N'],candidates=[dict(hospital_id='N',travel=dict(duration_seconds=101))])
        self.assertEqual(guard['allowed_sends'],[]);self.assertEqual(guard['rows'][0]['foreign_pending_count'],1)

    def test_unrelated_foreign_hospital_does_not_block_known_empty_target(self):
        guard=policy.travel_guard(now=1000,patient_id='P',pending=[dict(patient='P',hospital='OLD',submitted_at=990),
            dict(patient='OTHER',hospital='DIFFERENT',submitted_at=990)],patient_pending=[dict(patient='P',hospital='OLD',submitted_at=990,travel_seconds=100)],
            proposed_sends=['N'],candidates=[dict(hospital_id='N',travel=dict(duration_seconds=101))])
        self.assertEqual(guard['allowed_sends'],['N']);self.assertTrue(guard['rows'][0]['queue_exception_applied'])

    def test_unknown_or_future_public_queue_cannot_be_assumed_empty(self):
        for extra in ({'hospital':'OTHER'},dict(patient='OTHER',hospital='N',submitted_at=1001),
                      dict(patient=True,hospital='N',submitted_at=990)):
            pending=[dict(patient='P',hospital='OLD',submitted_at=990),extra]
            guard=policy.travel_guard(now=1000,patient_id='P',pending=pending,
                patient_pending=[dict(patient='P',hospital='OLD',submitted_at=990,travel_seconds=100)],
                proposed_sends=['NEAR','FAR'],candidates=[dict(hospital_id='NEAR',travel=dict(duration_seconds=50)),dict(hospital_id='FAR',travel=dict(duration_seconds=101))])
            self.assertEqual(guard['allowed_sends'],['NEAR']);self.assertFalse(guard['public_queue_known'])

    def test_recalled_and_distance_allowed_sends_preserve_original_order(self):
        guard=self.call(new=(999,50,100),queues=0)
        self.assertEqual(guard['allowed_sends'],['NEW0','NEW1','NEW2'])
        self.assertEqual(guard['queue_exception_applied_hospital_ids'],['NEW0'])


class GuardedPlanPrefetchTests(unittest.TestCase):
    def args(self,*,plan=None,parent_send=()):
        pending=[dict(hospital='OLD',patient='P',submitted_at=990)]
        return dict(now=1000,patient_id='P',wake_context={'rule':policy.context.WAKE_RULE},
            parent_proposal=dict(send=list(parent_send),evidence=dict(candidate_plan=[dict(hospital='N0',planned_offset_seconds=30),
                dict(hospital='N1',planned_offset_seconds=15),dict(hospital='N0',planned_offset_seconds=15)] if plan is None else plan)),
            candidates=[dict(hospital_id='N0',travel=dict(duration_seconds=110)),dict(hospital_id='N1',travel=dict(duration_seconds=90))],
            pending=pending,patient_pending=[dict(pending[0],travel_seconds=100)],max_new=9)

    def call(self,args):
        before=deepcopy(args);out=policy.prefetch_plan(**args);self.assertEqual(args,before)
        self.assertFalse(out['model_or_cost_recomputed']);self.assertFalse(out['search_after_first_blocked'])
        self.assertLessEqual(len(out['added_hospital_ids']),1)
        return out

    def test_earliest_positive_offset_beats_native_list_position(self):
        out=self.call(self.args());self.assertTrue(out['applied'])
        self.assertEqual(out['selected_plan_index'],1);self.assertEqual(out['added_hospital_ids'],['N1'])

    def test_equal_offsets_use_native_plan_index_even_if_second_is_closer(self):
        out=self.call(self.args(plan=[dict(hospital='N0',planned_offset_seconds=15),dict(hospital='N1',planned_offset_seconds=15)]))
        self.assertEqual(out['selected_plan_index'],0);self.assertEqual(out['added_hospital_ids'],['N0'])
        self.assertEqual(out['recall_travel_guard']['queue_exception_applied_hospital_ids'],['N0'])

    def test_nonempty_original_H_send_disables_prefetch(self):
        out=self.call(self.args(parent_send=['N0']));self.assertFalse(out['applied'])
        self.assertEqual(out['reason'],'original_H_has_current_send');self.assertIsNone(out['selected_plan_index'])

    def test_no_own_wake_keeps_regular_decision_unchanged(self):
        args=self.args();args['wake_context']=None;out=self.call(args)
        self.assertFalse(out['applied']);self.assertEqual(out['reason'],'not_own_decline_wake')

    def test_empty_zero_or_invalid_future_offsets_do_not_create_plan(self):
        for plan in ([],[dict(hospital='N0',planned_offset_seconds=None)],[dict(hospital='N0',planned_offset_seconds=0)],
                     [dict(hospital='N0',planned_offset_seconds=-1)],[dict(hospital='N0',planned_offset_seconds=True)]):
            out=self.call(self.args(plan=plan));self.assertFalse(out['applied'])
            self.assertEqual(out['reason'],'no_positive_finite_future_plan')

    def test_nonfinite_offsets_ignored_without_rounding_a_valid_fractional_offset(self):
        args=self.args(plan=[dict(hospital='N0',planned_offset_seconds=float('inf')),
            dict(hospital='N0',planned_offset_seconds=10.5),dict(hospital='N1',planned_offset_seconds=10.0),
            dict(hospital='N0',planned_offset_seconds=10.0)])
        out=self.call(args);self.assertEqual(out['selected_plan_index'],2)
        self.assertEqual(out['selected_plan_row']['planned_offset_seconds'],10.0)

    def test_current_fresh_own_four_blocks_and_age181_no_longer_counts(self):
        args=self.args();args['pending']=[dict(hospital='O'+str(i),patient='P',submitted_at=820) for i in range(4)]
        args['patient_pending']=[dict(q,travel_seconds=100) for q in args['pending']]
        out=self.call(args);self.assertFalse(out['applied']);self.assertEqual(out['fresh_own_count'],4)
        for q in args['pending']:q['submitted_at']=819
        args['patient_pending']=[dict(q,travel_seconds=100) for q in args['pending']]
        out=self.call(args);self.assertTrue(out['applied']);self.assertEqual(out['fresh_own_count'],0)

    def test_contact_budget_zero_or_boolean_does_not_allow_one_more_send(self):
        for value in (0,True,-1,11):
            args=self.args();args['max_new']=value;out=self.call(args)
            self.assertFalse(out['applied']);self.assertEqual(out['reason'],'no_supported_remaining_contact_budget')

    def test_first_planned_target_with_foreign_queue_does_not_try_next(self):
        args=self.args();args['pending'].append(dict(hospital='N1',patient='PEER',submitted_at=800))
        out=self.call(args);self.assertFalse(out['applied'])
        self.assertEqual(out['selected_plan_index'],1);self.assertEqual(out['target_foreign_pending_count'],1)
        self.assertEqual(out['reason'],'target_has_foreign_unresolved_inquiries')

    def test_unknown_first_candidate_travel_does_not_try_later_known_candidate(self):
        args=self.args();args['candidates'][1]['travel']['duration_seconds']=None
        out=self.call(args);self.assertFalse(out['applied']);self.assertEqual(out['selected_plan_index'],1)
        self.assertEqual(out['reason'],'unknown_new_travel')

    def test_missing_or_duplicate_first_candidate_is_not_replaced_by_another(self):
        for duplicate in (False,True):
            args=self.args()
            if duplicate:args['candidates'].append(deepcopy(args['candidates'][1]))
            else:args['candidates'].pop()
            out=self.call(args);self.assertFalse(out['applied']);self.assertEqual(out['selected_plan_index'],1)
            self.assertEqual(out['reason'],'missing_or_ambiguous_current_candidate')

    def test_unknown_own_travel_retains_Recall_hard_block(self):
        args=self.args();args['patient_pending']=[];out=self.call(args)
        self.assertFalse(out['applied']);self.assertEqual(out['reason'],'Recall_travel_guard_rejected')
        self.assertTrue(out['recall_travel_guard']['unknown_fresh_pending_travel'])

    def test_unknown_public_queue_disables_prefetch_instead_of_guessing_empty(self):
        args=self.args();args['pending'].append(dict(hospital='N0'))
        out=self.call(args);self.assertFalse(out['applied']);self.assertEqual(out['reason'],'unknown_public_pending_queue')


    def test_singleton_earliest_wave_does_not_search_later_shared_wave(self):
        args=self.args(plan=[dict(hospital='N1',planned_offset_seconds=10),
            dict(hospital='N0',planned_offset_seconds=15),dict(hospital='N1',planned_offset_seconds=15)])
        out=self.call(args)
        self.assertFalse(out['applied']);self.assertEqual(out['reason'],'future_wave_has_no_distinct_peer')
        self.assertEqual(out['earliest_wave_offset_seconds'],10)
        self.assertEqual(out['earliest_wave_plan_indices'],[0])
        self.assertEqual(out['earliest_wave_distinct_hospital_ids'],['N1'])

    def test_duplicate_hospital_and_malformed_peer_do_not_make_shared_wave(self):
        for peer in ('N1','',None,True,7):
            args=self.args(plan=[dict(hospital='N1',planned_offset_seconds=15),
                dict(hospital=peer,planned_offset_seconds=15)])
            out=self.call(args)
            self.assertFalse(out['applied']);self.assertFalse(out['earliest_wave_has_distinct_peer'])
            self.assertEqual(out['reason'],'future_wave_has_no_distinct_peer')
            self.assertEqual(out['earliest_wave_distinct_hospital_ids'],['N1'])

    def test_numeric_tie_is_exact_without_fractional_rounding(self):
        args=self.args(plan=[dict(hospital='N1',planned_offset_seconds=10),
            dict(hospital='N0',planned_offset_seconds=10.0000001)])
        out=self.call(args)
        self.assertFalse(out['applied']);self.assertEqual(out['earliest_wave_plan_indices'],[0])
        args['parent_proposal']['evidence']['candidate_plan'][1]['planned_offset_seconds']=10.0
        out=self.call(args)
        self.assertTrue(out['applied']);self.assertEqual(out['earliest_wave_plan_indices'],[0,1])

    def test_wave_evidence_uses_copied_rows_and_native_distinct_order(self):
        args=self.args(plan=[dict(hospital='N1',planned_offset_seconds=15),
            dict(hospital='N0',planned_offset_seconds=15),dict(hospital='N1',planned_offset_seconds=15)])
        out=self.call(args)
        self.assertTrue(out['applied']);self.assertEqual(out['added_hospital_ids'],['N1'])
        self.assertEqual(out['earliest_wave_plan_indices'],[0,1,2])
        self.assertEqual(out['earliest_wave_distinct_hospital_ids'],['N1','N0'])
        args['parent_proposal']['evidence']['candidate_plan'][0]['hospital']='MUTATED'
        self.assertEqual(out['earliest_wave_rows'][0]['hospital'],'N1')

    def test_invalid_first_identifier_never_falls_through_despite_two_valid_peers(self):
        args=self.args(plan=[dict(hospital=None,planned_offset_seconds=15),
            dict(hospital='N1',planned_offset_seconds=15),dict(hospital='N0',planned_offset_seconds=15)])
        out=self.call(args)
        self.assertFalse(out['applied']);self.assertEqual(out['reason'],'unknown_first_planned_hospital')
        self.assertEqual(out['selected_plan_index'],0)
        self.assertTrue(out['earliest_wave_has_distinct_peer'])


if __name__=='__main__':unittest.main()
