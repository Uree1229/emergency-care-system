"""Soft public-intent veto unit tests, without workflow/performance replay.

Guard outputs below are genuine frozen-guard calculations on small unit rows.
Journal views are synthetic API inputs, not saved formal-performance evidence.
No scenario, development seed or held-out seed is executed.
"""
import ast
from copy import deepcopy
import hashlib
import inspect
from pathlib import Path
import unittest
from unittest.mock import patch

from inquiry_lab import guard_policy as old, intent_policy as new


def inputs(load=1,**kwargs):
    value=dict(now=1000,candidates=[dict(hospital_id=f'H{i:02}',
        travel=dict(duration_seconds=300+10*i),resources_enough=True) for i in range(1,9)],
        observations=[],pending=[dict(hospital=f'H{i:02}',patient=f'Q{i}',submitted_at=900)
            for i in range(1,load+1)],patient_pending=[],max_new=10,last_contact_at=None)
    value.update(kwargs)
    return value


def context(hospitals=('H05',),owner='Q1',decision=3,at=995):
    plans=[dict(patient=owner,decision_id=decision,at=at,candidate_plan=[
        dict(hospital=h,planned_offset_seconds=120) for h in hospitals])] if hospitals else []
    return dict(patient_id='P',event_prefix_max=8,decision_prefix_max=decision,plans=plans)


def stripped(output,identity):
    result=deepcopy(output)
    result['evidence'].pop('intent')
    result['evidence']['policy']=identity
    return result


class IntentVetoTests(unittest.TestCase):
    def test_overlap_veto_restores_current_R_proposal_entire_core(self):
        args=inputs();view=context()
        guarded=old.decide(old.NAME,**args)
        self.assertTrue(guarded['evidence']['guard']['applied'])
        result=new.decide(new.NAME,**args,public_context=view)
        metadata=result['evidence']['intent']
        proposal=guarded['evidence']['guard']['proposal']
        self.assertEqual(stripped(result,proposal['evidence']['policy']),proposal)
        self.assertEqual(metadata['guard_output'],guarded)
        self.assertEqual(metadata['public_context'],view)
        self.assertTrue(metadata['guard_applied']);self.assertTrue(metadata['vetoed'])
        self.assertEqual(metadata['added_hospitals'],['H05'])
        self.assertEqual(metadata['protected_hospitals'],['H05'])
        self.assertEqual(metadata['conflicts'],[dict(hospital='H05',patient='Q1',decision_id=3,
            at=995,planned_offset_seconds=120)])
        self.assertEqual(metadata['core_origin'],'residual_proposal')
        self.assertNotIn('guard',result['evidence'])

    def test_nonoverlap_retains_entire_guard_output(self):
        args=inputs();view=context(('H06',))
        guarded=old.decide(old.NAME,**args)
        result=new.decide(new.NAME,**args,public_context=view)
        metadata=result['evidence']['intent']
        self.assertEqual(stripped(result,old.NAME),guarded)
        self.assertTrue(metadata['guard_applied']);self.assertFalse(metadata['vetoed'])
        self.assertEqual(metadata['conflicts'],[])
        self.assertEqual(metadata['core_origin'],'guard_output')
        self.assertIn('guard',result['evidence'])

    def test_only_added_membership_can_be_vetoed_not_common_proposal_contacts(self):
        args=inputs();view=context(('H02',))
        guarded=old.decide(old.NAME,**args)
        self.assertIn('H02',guarded['send'])
        self.assertIn('H02',guarded['evidence']['guard']['proposal']['send'])
        result=new.decide(new.NAME,**args,public_context=view)
        self.assertFalse(result['evidence']['intent']['vetoed'])
        self.assertEqual(stripped(result,old.NAME),guarded)

    def test_no_guard_application_cannot_change_original_core(self):
        args=inputs(load=0)
        args['pending']=[dict(hospital='REMOTE',patient='Q1',submitted_at=900)]
        guarded=old.decide(old.NAME,**args)
        self.assertFalse(guarded['evidence']['guard']['applied'])
        result=new.decide(new.NAME,**args,public_context=context())
        self.assertEqual(stripped(result,old.NAME),guarded)
        self.assertEqual(result['evidence']['intent']['added_hospitals'],[])
        self.assertFalse(result['evidence']['intent']['vetoed'])

    def test_empty_view_preserves_guard_without_a_second_candidate_search(self):
        args=inputs();guarded=old.decide(old.NAME,**args)
        with patch.object(old,'decide',wraps=old.decide) as called:
            result=new.decide(new.NAME,**args,public_context=context(()))
        called.assert_called_once_with(old.NAME,**args)
        self.assertEqual(stripped(result,old.NAME),guarded)
        self.assertEqual(result['evidence']['intent']['protected_hospitals'],[])

    def test_one_overlap_vetoes_whole_swap_without_choosing_a_second_best(self):
        args=inputs(load=2)
        guarded=old.decide(old.NAME,**args)
        self.assertEqual(guarded['send'],['H03','H04','H05','H06'])
        view=context(('H05',))
        result=new.decide(new.NAME,**args,public_context=view)
        self.assertEqual(result['send'],['H01','H02','H03','H04'])
        self.assertEqual(result['evidence']['intent']['added_hospitals'],['H05','H06'])
        self.assertTrue(result['evidence']['intent']['vetoed'])

    def test_collision_witnesses_keep_added_order_then_journal_and_plan_order(self):
        args=inputs(load=2)
        view=context(('H06','H05'))
        view['plans'].append(dict(patient='Q2',decision_id=7,at=990,candidate_plan=[
            dict(hospital='H05',planned_offset_seconds=15),dict(hospital='H06',planned_offset_seconds=180)]))
        view['decision_prefix_max']=7
        result=new.decide(new.NAME,**args,public_context=view)
        metadata=result['evidence']['intent']
        self.assertEqual(metadata['protected_hospitals'],['H05','H06'])
        self.assertEqual([(r['hospital'],r['patient']) for r in metadata['conflicts']],
            [('H05','Q1'),('H05','Q2'),('H06','Q1'),('H06','Q2')])
        self.assertEqual(metadata['public_context']['plans'][0]['candidate_plan'],view['plans'][0]['candidate_plan'])

    def test_all_original_positive_offsets_are_tentative_intent_without_new_ETA_cutoff(self):
        args=inputs()
        for offset in (15,30,60,90,120,180):
            view=context();view['plans'][0]['candidate_plan'][0]['planned_offset_seconds']=offset
            result=new.decide(new.NAME,**args,public_context=view)
            self.assertTrue(result['evidence']['intent']['vetoed'])

    def test_journal_freshness_includes_now_and_exact_next_review_boundary(self):
        args=inputs()
        for at in (1000,985):
            result=new.decide(new.NAME,**args,public_context=context(at=at))
            self.assertTrue(result['evidence']['intent']['vetoed'])

    def test_timing_and_same_input_inquiry_count_remain_original_guard_values(self):
        args=inputs();guarded=old.decide(old.NAME,**args)
        for view in (context(),context(('H06',)),context(())):
            result=new.decide(new.NAME,**args,public_context=view)
            self.assertEqual(len(result['send']),len(guarded['send']))
            self.assertEqual(result['next_review_at'],guarded['next_review_at'])
            self.assertEqual(result['reason'],guarded['reason'])


class IntentContextValidationTests(unittest.TestCase):
    def assert_bad(self,view,args=None):
        args=args or inputs()
        before=deepcopy(view)
        with patch.object(old,'decide',wraps=old.decide) as called:
            with self.assertRaises(ValueError):new.decide(new.NAME,**args,public_context=view)
        called.assert_called_once_with(old.NAME,**args)
        self.assertEqual(view,before)

    def test_exact_context_and_nested_key_whitelists_reject_private_or_unknown_fields(self):
        for location in ('top','plan','candidate'):
            view=context()
            if location=='top':view['workers']=2
            elif location=='plan':view['plans'][0]['actual_reply_at']=1001
            else:view['plans'][0]['candidate_plan'][0]['service_seconds']=1
            self.assert_bad(view)
        for key in ('patient_id','event_prefix_max','decision_prefix_max','plans'):
            view=context();view.pop(key);self.assert_bad(view)
        self.assert_bad(None)

    def test_nonnegative_integer_prefixes_and_bounded_decision_ids(self):
        for key in ('event_prefix_max','decision_prefix_max'):
            for value in (-1,True,3.,None):
                view=context();view[key]=value;self.assert_bad(view)
        for value in (0,-1,True,3.,4):
            view=context();view['plans'][0]['decision_id']=value;self.assert_bad(view)

    def test_event_and_decision_prefixes_are_independent_journal_domains(self):
        view=context();view['event_prefix_max']=1;view['decision_prefix_max']=10000
        result=new.decide(new.NAME,**inputs(),public_context=view)
        self.assertEqual(result['evidence']['intent']['public_context'],view)

    def test_plans_must_have_distinct_other_currently_unresolved_owners(self):
        for owner in ('P','MISSING','',True,None):
            view=context(owner=owner);self.assert_bad(view)
        view=context();view['plans'].append(dict(view['plans'][0],decision_id=4));view['decision_prefix_max']=4
        self.assert_bad(view)

    def test_owner_plan_rows_are_strictly_ordered_by_distinct_decision_ids(self):
        args=inputs(load=2)
        for second_id in (3,2):
            view=context();view['plans'].append(dict(patient='Q2',decision_id=second_id,at=995,
                candidate_plan=[dict(hospital='H05',planned_offset_seconds=15)]))
            self.assert_bad(view,args)

    def test_future_expired_negative_or_noninteger_plan_clocks_are_rejected(self):
        for at in (1001,984,-1,995.,True,None):self.assert_bad(context(at=at))

    def test_filtered_owner_plan_must_be_nonempty_list(self):
        view=context();view['plans']=tuple(view['plans']);self.assert_bad(view)
        for rows in ([],(),None):
            view=context();view['plans'][0]['candidate_plan']=rows;self.assert_bad(view)

    def test_only_positive_original_grid_offsets_are_permitted(self):
        for offset in (0,None,-15,5,16,181,15.,True):
            view=context();view['plans'][0]['candidate_plan'][0]['planned_offset_seconds']=offset
            self.assert_bad(view)

    def test_duplicate_or_invalid_hospitals_and_already_pending_contacts_are_rejected(self):
        for hospital in ('',None,True,'H01'):
            view=context((hospital,));self.assert_bad(view)
        view=context(('H05','H05'));self.assert_bad(view)

    def test_current_patient_identity_matches_own_pending_when_present(self):
        args=inputs()
        row=dict(hospital='OWN',patient='P',submitted_at=995)
        args['pending'].append(row);args['patient_pending']=[dict(row,travel_seconds=300)]
        view=context();view['patient_id']='DIFFERENT'
        self.assert_bad(view,args)


class IntentFoundationTests(unittest.TestCase):
    def test_spec_changes_only_identity_and_frozen_guard_source_is_bound(self):
        before=old.spec(old.NAME);after=new.spec(new.NAME)
        for key in ('name','label'):before.pop(key);after.pop(key)
        self.assertEqual(before,after)
        self.assertEqual(hashlib.sha256(Path(old.__file__).read_bytes()).hexdigest(),new.GUARD_SOURCE_SHA256)
        for name in (None,old.NAME,'eat_intent_l30_f'):
            with self.assertRaises(ValueError):new.spec(name)

    def test_guard_output_is_computed_before_public_context_validation(self):
        args=inputs();order=[]
        actual_guard=old.decide
        def recorded_guard(name,**kwargs):
            order.append('guard');return actual_guard(name,**kwargs)
        actual_validation=new._validate_context
        def recorded_validation(*args):
            order.append('context');return actual_validation(*args)
        with patch.object(old,'decide',side_effect=recorded_guard),patch.object(new,'_validate_context',side_effect=recorded_validation):
            new.decide(new.NAME,**args,public_context=context())
        self.assertEqual(order,['guard','context'])

    def test_original_seven_kwargs_are_unchanged_and_generator_inputs_are_consumed_once(self):
        args=inputs();view=context();before=deepcopy(args);before_view=deepcopy(view)
        expected=new.decide(new.NAME,**args,public_context=view)
        counts={key:0 for key in ('candidates','observations','pending','patient_pending')}
        def generate(key):
            for row in deepcopy(args[key]):counts[key]+=1;yield row
        generic=dict(args)
        for key in counts:generic[key]=generate(key)
        with patch.object(old,'decide',wraps=old.decide) as called:
            actual=new.decide(new.NAME,**generic,public_context=view)
        self.assertEqual(actual,expected)
        self.assertEqual(counts,{key:len(args[key]) for key in counts})
        called.assert_called_once_with(old.NAME,**args)
        self.assertEqual(args,before);self.assertEqual(view,before_view)

    def test_public_context_and_nested_guard_output_do_not_alias_final_core(self):
        view=context();args=inputs();result=new.decide(new.NAME,**args,public_context=view)
        expected=new.decide(new.NAME,**args,public_context=view)
        result['evidence']['intent']['public_context']['plans'].clear()
        self.assertEqual(view,context())
        result['evidence']['intent']['guard_output']['evidence']['guard']['proposal']['send'].clear()
        self.assertEqual(result['send'],expected['send'])
        self.assertTrue(expected['evidence']['intent']['guard_output']['evidence']['guard']['proposal']['send'])

    def test_base_validation_and_required_eighth_keyword_are_retained(self):
        for change in (dict(now=True),dict(now=-1),dict(max_new=False),dict(max_new=-1)):
            with self.assertRaises(ValueError):new.decide(new.NAME,**inputs(**change),public_context=context())
        with self.assertRaises(TypeError):new.decide(new.NAME,**inputs())
        parameters=list(inspect.signature(new.decide).parameters)
        self.assertEqual(parameters[:-1],list(inspect.signature(old.decide).parameters))
        self.assertEqual(parameters[-1],'public_context')

    def test_wrapper_has_one_frozen_guard_endpoint_and_no_second_search(self):
        tree=ast.parse(inspect.getsource(new.decide))
        calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call)
            and isinstance(node.func,ast.Attribute) and node.func.attr=='decide']
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].func.value.id,'original')
        self.assertEqual(calls[0].args[0].id,'BASE_POLICY')


if __name__=='__main__':unittest.main()
