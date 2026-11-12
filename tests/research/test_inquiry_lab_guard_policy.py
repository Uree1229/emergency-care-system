"""Same-count imminent-capacity guard unit tests; no performance replay.

Controlled proposal/cost-table tests exercise a wrapper boundary, not actual
performance evidence. They never execute a scenario, DEV seed or held-out seed.
"""
import ast
from copy import deepcopy
import hashlib
import inspect
from pathlib import Path
import unittest
from unittest.mock import patch

from inquiry_lab import residual_policy as old, batch_policy as batch, guard_policy as new


HOSPITALS = [f'H{i:02}' for i in range(1, 7)]


def inputs(n=8, **kwargs):
    value = dict(now=1000, candidates=[dict(hospital_id=f'H{i:02}',
        travel=dict(duration_seconds=300+10*i), resources_enough=True) for i in range(1, n+1)],
        observations=[], pending=[], patient_pending=[], max_new=10, last_contact_at=None)
    value.update(kwargs)
    return value


def with_own(count, age=5, **kwargs):
    rows = [dict(hospital=f'OWN{i}', patient='P', submitted_at=1000-age) for i in range(count)]
    return inputs(pending=rows,
        patient_pending=[dict(row, travel_seconds=9000+i) for i,row in enumerate(rows)], **kwargs)


def proposal(args, send=HOSPITALS[:4], future=(('H05', 15), ('H06', 120))):
    """Controlled unit proposal with genuine frozen residual ancillary fields."""
    value = old.decide(old.NAME, **args)
    plan = dict(future)
    plan.update({h:0 for h in send})
    value['send'] = list(send)
    value['evidence']['sent_hospitals'] = list(send)
    for row in value['evidence']['candidate_plan']:
        row['planned_offset_seconds'] = plan.get(row['hospital'])
    return value


def utility(args, costs=None, default=500.):
    """Controlled unit table; this is not a valid formal-performance artifact."""
    value = batch.decide(batch.NAME, **args)
    costs = costs or {}
    for row in value['evidence']['batch']['subsets']:
        row['cost'] = costs.get(tuple(row['hospitals']), default)
    return value


def core(result):
    value = deepcopy(result)
    value['evidence'].pop('guard')
    value['evidence']['policy'] = old.NAME
    return value


def run_controlled(args, proposed, table=None):
    with patch.object(old, 'decide', return_value=proposed) as original_call:
        with patch.object(batch, 'decide', return_value=table) as batch_call:
            result = new.decide(new.NAME, **args)
    return result, original_call, batch_call


class GuardTriggerTests(unittest.TestCase):
    def test_not_full_preserves_proposal_and_never_calls_batch_even_if_future_overflows(self):
        args = inputs()
        proposed = proposal(args, send=HOSPITALS[:3], future=(('H04',15),('H05',15)))
        result, original_call, batch_call = run_controlled(args, proposed)
        meta = result['evidence']['guard']
        self.assertEqual(core(result), proposed)
        self.assertFalse(meta['currently_full']); self.assertFalse(meta['triggered'])
        self.assertEqual(meta['conflict_offsets_seconds'], [15])
        self.assertEqual(meta['evaluation_skip_reason'], 'not_triggered')
        self.assertFalse(meta['batch_evaluated'])
        original_call.assert_called_once_with(old.NAME, **args)
        batch_call.assert_not_called()

    def test_far_future_plan_does_not_justify_current_membership_change(self):
        args = inputs()
        proposed = proposal(args, future=(('H05',120),('H06',180)))
        result, _, batch_call = run_controlled(args, proposed)
        meta = result['evidence']['guard']
        self.assertTrue(meta['currently_full']); self.assertFalse(meta['triggered'])
        self.assertEqual(meta['future_checks'], [])
        self.assertEqual(meta['proposal'], proposed)
        self.assertEqual(core(result), proposed)
        batch_call.assert_not_called()

    def test_next_review_boundary_is_inclusive_and_later_offsets_are_excluded(self):
        args = inputs()
        for offset, expected in ((1,True),(15,True),(16,False),(120,False),(180,False)):
            proposed = proposal(args, future=(('H05',offset),))
            table = utility(args) if expected else None
            result, _, batch_call = run_controlled(args, proposed, table)
            meta = result['evidence']['guard']
            self.assertEqual(meta['triggered'], expected)
            self.assertEqual(meta['batch_evaluated'], expected)
            self.assertEqual(meta['review_window_seconds'], old.spec(old.NAME)['review_seconds'])
            self.assertEqual(batch_call.call_count, int(expected))

    def test_zero_offsets_and_none_are_not_future_conflicts(self):
        args = inputs()
        proposed = proposal(args, future=())
        result, _, batch_call = run_controlled(args, proposed)
        self.assertEqual(result['evidence']['guard']['future_checks'], [])
        self.assertFalse(result['evidence']['guard']['triggered'])
        self.assertEqual(core(result), proposed)
        batch_call.assert_not_called()

    def test_existing_pending_expires_only_after_inclusive_180_second_boundary(self):
        for age, expected in ((165,True),(166,False)):
            args = with_own(1,age=age)
            proposed = proposal(args,send=HOSPITALS[:3],future=(('H04',15),))
            table = utility(args) if expected else None
            result, _, batch_call = run_controlled(args, proposed, table)
            meta = result['evidence']['guard']; check = meta['future_checks'][0]
            self.assertTrue(meta['currently_full'])
            self.assertEqual(check['fresh_own_count'], int(expected))
            self.assertEqual(check['proposal_fresh_count'], 4)
            self.assertEqual(check['total_fresh_count'], 5 if expected else 4)
            self.assertEqual(meta['triggered'], expected)
            self.assertEqual(batch_call.call_count, int(expected))

    def test_future_check_counts_only_submitted_or_scheduled_fresh_contacts(self):
        args = with_own(1,age=170)
        proposed = proposal(args,send=HOSPITALS[:3],future=(('H04',15),('H05',15),('H06',60)))
        own = old.inquiry_policies._pending(args['patient_pending'],1000,own=True)
        checks = new._future_checks(proposed,own,1000,15,4)
        self.assertEqual(len(checks),1)
        self.assertEqual(checks[0]['existing_fresh_hospitals'],[])
        self.assertEqual(checks[0]['scheduled_fresh_hospitals'],HOSPITALS[:5])
        self.assertEqual(checks[0]['total_fresh_count'],5)
        self.assertTrue(checks[0]['conflict'])

    def test_zero_cardinality_conflict_is_recorded_without_useless_batch_execution(self):
        args = with_own(4)
        proposed = proposal(args,send=[],future=(('H01',15),))
        result, _, batch_call = run_controlled(args, proposed)
        meta = result['evidence']['guard']
        self.assertTrue(meta['currently_full']); self.assertTrue(meta['triggered'])
        self.assertFalse(meta['batch_evaluated']); self.assertFalse(meta['applied'])
        self.assertEqual(meta['evaluation_skip_reason'],'zero_cardinality')
        self.assertEqual(meta['same_count_costs'],[])
        self.assertIsNone(meta['batch_output']); self.assertIsNone(meta['proposal_immediate_cost'])
        self.assertIsNone(meta['best_same_count_hospitals']); self.assertIsNone(meta['improvement_seconds'])
        self.assertEqual(core(result),proposed)
        batch_call.assert_not_called()

    def test_stale_pending_does_not_make_current_plan_full(self):
        args = with_own(1,age=181)
        proposed = proposal(args,send=HOSPITALS[:3],future=(('H04',15),('H05',15)))
        result, _, batch_call = run_controlled(args,proposed)
        self.assertEqual(result['evidence']['guard']['fresh_own_count'],0)
        self.assertFalse(result['evidence']['guard']['currently_full'])
        self.assertEqual(core(result),proposed)
        batch_call.assert_not_called()


class GuardSameCountTests(unittest.TestCase):
    def test_only_same_count_membership_can_replace_proposal(self):
        args=inputs(); proposed=proposal(args)
        target=('H01','H02','H03','H05')
        table=utility(args,{tuple(HOSPITALS[:4]):200.,target:100.,():0.,('H06',):1.})
        before=deepcopy(proposed);before_table=deepcopy(table)
        result,original_call,batch_call=run_controlled(args,proposed,table)
        meta=result['evidence']['guard']
        self.assertEqual(result['send'],list(target))
        self.assertEqual(len(result['send']),len(proposed['send']))
        self.assertEqual(meta['same_count_minimum_cost'],100.)
        self.assertEqual(meta['proposal_immediate_cost'],200.)
        self.assertEqual(meta['improvement_seconds'],100.)
        self.assertTrue(meta['applied']);self.assertTrue(meta['batch_evaluated'])
        self.assertEqual(meta['evaluation_skip_reason'],None)
        self.assertEqual(meta['output_cost_scope'],'immediate_same_count_batch')
        self.assertTrue(all(len(row['hospitals'])==4 for row in meta['same_count_costs']))
        self.assertEqual(len(meta['same_count_costs']),15)
        self.assertEqual(meta['batch_output'],table)
        self.assertEqual(meta['proposal'],before)
        self.assertEqual(proposed,before);self.assertEqual(table,before_table)
        original_call.assert_called_once_with(old.NAME,**args)
        batch_call.assert_called_once_with(batch.NAME,**args)

    def test_applied_top_plan_is_immediate_and_every_unrelated_output_field_is_original(self):
        args=inputs();proposed=proposal(args)
        target=('H01','H02','H03','H05')
        table=utility(args,{tuple(HOSPITALS[:4]):200.,target:100.123456})
        result,_,_=run_controlled(args,proposed,table)
        self.assertEqual(result['evidence']['planned_expected_cost'],100.123)
        self.assertEqual(result['evidence']['sent_hospitals'],list(target))
        restored=core(result)
        restored['send']=deepcopy(proposed['send'])
        restored['evidence']['sent_hospitals']=deepcopy(proposed['evidence']['sent_hospitals'])
        restored['evidence']['planned_expected_cost']=proposed['evidence']['planned_expected_cost']
        for actual,wanted in zip(restored['evidence']['candidate_plan'],proposed['evidence']['candidate_plan']):
            actual['planned_offset_seconds']=wanted['planned_offset_seconds']
        self.assertEqual(restored,proposed)
        self.assertTrue(all(row['planned_offset_seconds'] in (0,None)
            for row in result['evidence']['candidate_plan']))
        self.assertEqual(result['next_review_at'],proposed['next_review_at'])
        self.assertEqual(result['reason'],proposed['reason'])

    def test_no_strict_cost_improvement_keeps_entire_original_future_plan(self):
        args=inputs();proposed=proposal(args)
        table=utility(args,{tuple(HOSPITALS[:4]):100.})
        result,_,_=run_controlled(args,proposed,table)
        meta=result['evidence']['guard']
        self.assertTrue(meta['triggered']);self.assertTrue(meta['batch_evaluated'])
        self.assertFalse(meta['applied']);self.assertEqual(meta['improvement_seconds'],0.)
        self.assertEqual(meta['output_cost_scope'],'original_residual_future_plan')
        self.assertEqual(core(result),proposed)
        self.assertEqual(result['evidence']['candidate_plan'][4]['planned_offset_seconds'],15)
        self.assertEqual(result['evidence']['candidate_plan'][5]['planned_offset_seconds'],120)

    def test_exact_one_nanosecond_threshold_is_not_a_strict_improvement(self):
        args=inputs();original_set=HOSPITALS[1:5];better=tuple(HOSPITALS[:4])
        proposed=proposal(args,send=original_set,future=(('H01',15),))
        for improvement,applied in ((.5e-9,False),(1e-9,False),(2e-9,True)):
            table=utility(args,{tuple(original_set):improvement,better:0.})
            result,_,_=run_controlled(args,proposed,table)
            meta=result['evidence']['guard']
            self.assertEqual(meta['best_same_count_hospitals'],list(better))
            self.assertEqual(meta['improvement_seconds'],improvement)
            self.assertEqual(meta['applied'],applied)
            if not applied:self.assertEqual(core(result),proposed)

    def test_same_count_epsilon_tie_is_global_then_original_pool_order(self):
        args=inputs();original_set=HOSPITALS[2:6]
        proposed=proposal(args,send=original_set,future=(('H01',15),))
        table=utility(args,{tuple(original_set):10.,tuple(HOSPITALS[:4]):1.5e-9,
            ('H01','H02','H03','H05'):.75e-9,('H01','H02','H03','H06'):0.})
        result,_,_=run_controlled(args,proposed,table)
        meta=result['evidence']['guard']
        self.assertEqual(meta['same_count_minimum_cost'],0.)
        self.assertEqual(meta['best_same_count_hospitals'],['H01','H02','H03','H05'])
        self.assertTrue(meta['applied'])

    def test_positive_room_with_one_own_pending_keeps_exactly_three_new_contacts(self):
        args=with_own(1)
        proposed=proposal(args,send=HOSPITALS[:3],future=(('H04',15),))
        target=('H01','H02','H04')
        table=utility(args,{tuple(HOSPITALS[:3]):200.,target:100.})
        result,_,_=run_controlled(args,proposed,table)
        self.assertEqual(result['send'],list(target))
        self.assertEqual(result['evidence']['guard']['fresh_own_count'],1)
        self.assertEqual(result['evidence']['guard']['proposal_send_count'],3)
        self.assertTrue(all(len(row['hospitals'])==3
            for row in result['evidence']['guard']['same_count_costs']))

    def test_residual_proposal_precedes_table_and_both_receive_identical_public_arguments(self):
        args=inputs();proposed=proposal(args);table=utility(args)
        order=[]
        def r_call(name,**kwargs):
            order.append(('R',name,deepcopy(kwargs)));return proposed
        def b_call(name,**kwargs):
            order.append(('B',name,deepcopy(kwargs)));return table
        frozen=deepcopy(args)
        with patch.object(old,'decide',side_effect=r_call),patch.object(batch,'decide',side_effect=b_call):
            new.decide(new.NAME,**args)
        self.assertEqual(order,[('R',old.NAME,args),('B',batch.NAME,args)])
        self.assertEqual(args,frozen)


class GuardFoundationTests(unittest.TestCase):
    def test_spec_changes_identity_only_and_has_no_new_tuning_parameter(self):
        before=old.spec(old.NAME);after=new.spec(new.NAME)
        for key in ('name','label'):before.pop(key);after.pop(key)
        self.assertEqual(before,after)
        after['config']['lambda_seconds']=999
        self.assertEqual(new.spec()['config']['lambda_seconds'],15)
        for name in (None,old.NAME,batch.NAME,'eat_guard_l30_f'):
            with self.assertRaises(ValueError):new.spec(name)

    def test_source_bindings_match_frozen_files_and_every_decision_records_them(self):
        for key,module in (('residual_policy',old),('batch_policy',batch)):
            self.assertEqual(hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),new.SOURCE_SHA256[key])
        result=new.decide(new.NAME,**inputs(n=0))
        self.assertEqual(result['evidence']['guard']['source_sha256'],new.SOURCE_SHA256)

    def test_actual_small_or_zero_budget_inputs_are_exact_original_core_without_batch(self):
        for args in (inputs(n=0),inputs(n=1),inputs(max_new=0)):
            expected=old.decide(old.NAME,**args)
            with patch.object(batch,'decide',side_effect=AssertionError('inactive guard evaluated table')):
                result=new.decide(new.NAME,**args)
            self.assertEqual(core(result),expected)

    def test_actual_unit_output_uses_frozen_cost_table_without_changing_count_or_clock(self):
        args=inputs(pending=[dict(hospital='H01',patient='OTHER',submitted_at=800)])
        proposed=old.decide(old.NAME,**args)
        result=new.decide(new.NAME,**args)
        meta=result['evidence']['guard']
        self.assertEqual(meta['proposal'],proposed)
        self.assertEqual(len(result['send']),len(proposed['send']))
        self.assertEqual(result['next_review_at'],proposed['next_review_at'])
        if meta['batch_evaluated']:
            self.assertEqual(meta['batch_output'],batch.decide(batch.NAME,**args))
            costs=meta['same_count_costs']
            minimum=min(row['cost'] for row in costs)
            chosen=next(row for row in costs if row['cost']<=minimum+1e-9)
            self.assertEqual(meta['best_same_count_hospitals'],chosen['hospitals'])
            self.assertEqual(meta['applied'],meta['improvement_seconds']>1e-9)
        if not meta['applied']:self.assertEqual(core(result),proposed)

    def test_decisions_inputs_and_nested_evidence_have_no_mutable_aliases(self):
        args=inputs();before=deepcopy(args)
        first=new.decide(new.NAME,**args);second=new.decide(new.NAME,**args)
        self.assertEqual(first,second);self.assertEqual(args,before)
        first['evidence']['guard']['proposal']['evidence']['candidate_plan'].clear()
        self.assertTrue(first['evidence']['candidate_plan'])
        self.assertTrue(second['evidence']['guard']['proposal']['evidence']['candidate_plan'])

    def test_future_and_private_fields_do_not_become_policy_inputs(self):
        args=inputs(pending=[dict(hospital='H01',patient='OTHER',submitted_at=800)])
        expected=new.decide(new.NAME,**args)
        args['pending'][0].update(workers=99,potential_service_seconds=1,next_accept_at=1001)
        args['pending'].append(dict(hospital='H01',patient='FUTURE',submitted_at=1001))
        args['observations'].append(dict(hospital='H01',submitted_at=800,observed_at=1001,outcome='accept'))
        self.assertEqual(new.decide(new.NAME,**args),expected)

    def test_actual_generic_iterables_and_materialized_lists_have_identical_outputs(self):
        args=with_own(1,age=165)
        args['pending'].append(dict(hospital='H01',patient='OTHER',submitted_at=800))
        args['observations'].append(dict(hospital='H01',submitted_at=700,observed_at=900,outcome='decline'))
        expected=new.decide(new.NAME,**args)
        generic=dict(args)
        for key in ('candidates','observations','pending','patient_pending'):
            generic[key]=(row for row in deepcopy(args[key]))
        self.assertEqual(new.decide(new.NAME,**generic),expected)

    def test_active_guard_materializes_each_generator_once_for_all_three_readers(self):
        args=with_own(1,age=165)
        args['observations'].append(dict(hospital='H06',submitted_at=700,observed_at=900,outcome='decline'))
        proposed=proposal(args,send=HOSPITALS[:3],future=(('H04',15),))
        table=utility(args,{tuple(HOSPITALS[:3]):200.,('H01','H02','H04'):100.})
        expected,_,_=run_controlled(args,deepcopy(proposed),deepcopy(table))
        counts={key:0 for key in ('candidates','observations','pending','patient_pending')}
        def one_pass(key):
            for row in deepcopy(args[key]):
                counts[key]+=1
                yield row
        generic=dict(args)
        for key in counts:generic[key]=one_pass(key)
        result,original_call,batch_call=run_controlled(generic,proposed,table)
        self.assertEqual(result,expected)
        self.assertTrue(result['evidence']['guard']['applied'])
        self.assertEqual(counts,{key:len(args[key]) for key in counts})
        original_call.assert_called_once_with(old.NAME,**args)
        batch_call.assert_called_once_with(batch.NAME,**args)

    def test_materialization_retains_malformed_public_row_validation(self):
        malformed=[dict(candidates=[dict(hospital_id='H',travel=dict(duration_seconds=-1))]),
            dict(candidates=[dict(hospital_id='H',travel=dict(duration_seconds=300))]*2),
            dict(pending=[dict(hospital='OWN0',patient='P',submitted_at=995)],
                patient_pending=[dict(hospital='OWN0',patient='P',submitted_at=995,travel_seconds=-1)])]
        for changed in malformed:
            args=inputs(**changed)
            for key in ('candidates','observations','pending','patient_pending'):
                args[key]=(row for row in args[key])
            with patch.object(batch,'decide',side_effect=AssertionError('malformed rows evaluated table')):
                with self.assertRaises(ValueError):new.decide(new.NAME,**args)
        for key in ('candidates','observations','pending','patient_pending'):
            args=inputs(**{key:None})
            with patch.object(batch,'decide',side_effect=AssertionError('noniterable input evaluated table')):
                with self.assertRaises(TypeError):new.decide(new.NAME,**args)

    def test_original_validation_errors_do_not_evaluate_batch(self):
        for changed in ({'now':True},{'now':-1},{'max_new':False},{'max_new':-1},
                {'patient_pending':[dict(hospital='X',patient='P',submitted_at=900,travel_seconds=300)]}):
            with patch.object(batch,'decide',side_effect=AssertionError('invalid input evaluated table')):
                with self.assertRaises(ValueError):new.decide(new.NAME,**inputs(**changed))

    def test_wrapper_reuses_only_two_source_bound_policy_endpoints(self):
        tree=ast.parse(inspect.getsource(new.decide)).body[0]
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Attribute) and n.func.attr=='decide']
        self.assertEqual([n.func.value.id for n in calls],['original','batch'])
        actual=inspect.signature(new.decide)
        self.assertEqual(actual,inspect.signature(old.decide))
        self.assertEqual(actual,inspect.signature(batch.decide))


if __name__=='__main__':unittest.main()
