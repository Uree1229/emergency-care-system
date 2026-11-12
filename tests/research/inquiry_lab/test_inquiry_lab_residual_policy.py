"""Residual queue mathematics and the single new-item-wait intervention.

Unit observations and numerical quadrature only; no performance environments,
development seeds, validation seeds or real worker/service data are executed.
"""
import ast
from copy import deepcopy
import inspect
import math
import unittest

from inquiry_lab import eat_policy as old,residual_policy as new


def candidate(hid,travel):
    return dict(hospital_id=hid,travel=dict(duration_seconds=travel),resources_enough=True)


def inputs(n=8,**kwargs):
    value=dict(now=1000,candidates=[candidate(f'H{i:02}',300+10*i) for i in range(1,n+1)],
        observations=[],pending=[],patient_pending=[],max_new=10)
    value.update(kwargs)
    return value


def core(value):
    result=deepcopy(value)
    result['evidence'].pop('residual_queue',None)
    result['evidence']['policy']='eat_l15_f'
    return result


def beliefs(history=(),now=1000):
    return old.Beliefs(list(history),now,old.spec('eat_l15_f')['config'])


def queue(now=1000,rows=(),history=(),hospital='H01'):
    normalized=old.inquiry_policies._observations(history,now,1800)
    current=old.inquiry_policies._pending(rows,now)
    current=[r for r in current if r['hospital']==hospital]
    return new._queue_wait_estimate(hospital,now,current,normalized,beliefs(normalized,now),1)


class ResidualMathTests(unittest.TestCase):
    def test_age_zero_is_full_mean_for_every_reply_probability(self):
        mu,sigma=math.log(66),.7
        expected=math.exp(mu+sigma*sigma/2)
        for r in (0,.2,.79,1-1e-12,1):
            self.assertEqual(new.head_expected_remaining(mu,sigma,r,0),expected)

    def test_matches_direct_stop_loss_where_subtraction_is_well_conditioned(self):
        mu,sigma=math.log(66),.7;mean=math.exp(mu+sigma*sigma/2)
        for age in (1,20,60,180,300,600,1800):
            z=(math.log(age)-mu)/sigma
            tail=.5*math.erfc(z/math.sqrt(2))
            numerator=mean*.5*math.erfc((z-sigma)/math.sqrt(2))-age*tail
            for r in (0,.2,.79,1-1e-12,1):
                expected=numerator/((1-r)+r*tail)
                actual=new.head_expected_remaining(mu,sigma,r,age)
                self.assertTrue(math.isclose(actual,expected,rel_tol=1e-10,abs_tol=1e-12))

    def test_independent_normal_density_quadrature_checks_the_numerator(self):
        mu,sigma=math.log(66),.7
        for age,r in ((20,.79),(180,.79),(300,1.)):
            lower=(math.log(age)-mu)/sigma;upper=12.;n=8192;step=(upper-lower)/n
            def integrand(z):
                return max(0.,math.exp(mu+sigma*z)-age)*math.exp(-z*z/2)/math.sqrt(2*math.pi)
            total=integrand(lower)+integrand(upper)
            total += sum((4 if i%2 else 2)*integrand(lower+i*step) for i in range(1,n))
            numerator=total*step/3
            tail=.5*math.erfc(lower/math.sqrt(2))
            expected=numerator/((1-r)+r*tail)
            actual=new.head_expected_remaining(mu,sigma,r,age)
            self.assertTrue(math.isclose(actual,expected,rel_tol=1e-9,abs_tol=1e-9))

    def test_silent_possibility_reduces_residual_without_hard_timeout(self):
        mu,sigma=math.log(66),.7
        values=[new.head_expected_remaining(mu,sigma,r,180) for r in (0,.79,1)]
        self.assertTrue(0<values[0]<values[1]<values[2])
        self.assertGreater(new.head_expected_remaining(mu,sigma,.79,181),0)
        self.assertGreater(new.head_expected_remaining(mu,sigma,.79,300),0)

    def test_lognormal_responder_tail_is_not_assumed_to_decrease_forever(self):
        mu,sigma=math.log(66),.7
        self.assertGreater(new.head_expected_remaining(mu,sigma,1,600),
            new.head_expected_remaining(mu,sigma,1,180))

    def test_far_tail_and_reply_near_one_remain_finite(self):
        for age in (1e9,1e100):
            for r in (0.,.79,1-1e-12,1.):
                value=new.head_expected_remaining(math.log(66),.7,r,age)
                self.assertTrue(math.isfinite(value));self.assertGreaterEqual(value,0)
            self.assertGreater(new.head_expected_remaining(math.log(66),.7,1.,age),0)
        self.assertGreater(new.head_expected_remaining(math.log(66),.7,1-1e-12,1e9),0)

    def test_rejects_invalid_distribution_and_age_values(self):
        for arguments in ((0,0,.79,10),(0,-1,.79,10),(0,.7,-.1,10),(0,.7,1.1,10),
            (0,.7,.79,-1),(True,.7,.79,10),(0,.7,True,10),
            (float('nan'),.7,.79,10),(0,.7,.79,float('inf'))):
            with self.assertRaises(ValueError):new.head_expected_remaining(*arguments)


class ResidualQueueTests(unittest.TestCase):
    def test_empty_queue_is_zero_and_has_no_invented_head(self):
        wait,record=queue()
        self.assertEqual(wait,0);self.assertEqual(record['queue_count'],0)
        self.assertIsNone(record['head']);self.assertIsNone(record['age_seconds'])
        self.assertIsNone(record['prior_terminal_reply']);self.assertIsNone(record['estimated_start_at'])
        self.assertEqual(record['head_expected_remaining_seconds'],0)
        self.assertEqual(record['original_full_wait_seconds'],0)

    def test_multiple_fresh_jobs_keep_the_exact_full_wait(self):
        rows=[dict(hospital='H01',patient=f'P{i}',submitted_at=1000) for i in range(5)]
        wait,record=queue(rows=rows)
        self.assertEqual(record['age_seconds'],0)
        self.assertEqual(wait,record['original_full_wait_seconds'])
        self.assertEqual(record['queued_behind_count'],4)

    def test_terminal_reply_can_move_the_estimated_start_but_checking_cannot(self):
        rows=[dict(hospital='H01',patient='HEAD',submitted_at=900),
            dict(hospital='H01',patient='FOLLOWER',submitted_at=940)]
        terminal=dict(hospital='H01',submitted_at=800,observed_at=980,outcome='accept')
        checking=dict(hospital='H01',submitted_at=850,observed_at=995,outcome='reply')
        tied=dict(hospital='H01',submitted_at=900,observed_at=998,outcome='decline')
        unrelated=dict(hospital='H02',submitted_at=700,observed_at=999,outcome='accept')
        _,record=queue(rows=rows,history=[terminal,checking,tied,unrelated])
        self.assertEqual(record['estimated_start_at'],980)
        self.assertEqual(record['age_seconds'],20)
        self.assertEqual(record['prior_terminal_reply'],terminal)
        _,only_checking=queue(rows=rows,history=[checking,tied,unrelated])
        self.assertEqual(only_checking['estimated_start_at'],900)
        self.assertIsNone(only_checking['prior_terminal_reply'])

    def test_accept_and_decline_are_both_terminal_evidence(self):
        rows=[dict(hospital='H01',patient='P',submitted_at=900)]
        for outcome in ('accept','decline'):
            _,record=queue(rows=rows,history=[dict(hospital='H01',submitted_at=800,observed_at=990,outcome=outcome)])
            self.assertEqual(record['estimated_start_at'],990)
            self.assertEqual(record['age_seconds'],10)

    def test_same_second_head_is_deterministic_and_does_not_use_hidden_order(self):
        rows=[dict(hospital='H01',patient=p,submitted_at=900) for p in ('B','A',2)]
        one,trace1=queue(rows=rows);two,trace2=queue(rows=list(reversed(rows)))
        self.assertEqual(one,two);self.assertEqual(trace1,trace2)
        self.assertEqual(trace1['head']['patient'],2)
        self.assertEqual(trace1['queued_behind_count'],2)

    def test_old_backlog_keeps_full_follower_work_and_does_not_erase_180s_rows(self):
        rows=[dict(hospital='H01',patient=f'P{i}',submitted_at=800+i) for i in range(5)]
        wait,trace=queue(rows=rows)
        self.assertEqual(trace['queue_count'],5)
        self.assertEqual(trace['queued_behind_count'],4)
        self.assertGreater(trace['head_expected_remaining_seconds'],0)
        self.assertGreater(wait,4*trace['mean_service_seconds'])
        self.assertAlmostEqual(wait,trace['head_expected_remaining_seconds']+4*trace['mean_service_seconds'])

    def test_later_terminal_or_future_information_does_not_rewrite_the_head_age(self):
        rows=[dict(hospital='H01',patient='P',submitted_at=900)]
        base=queue(rows=rows)
        misleading=[dict(hospital='H01',submitted_at=901,observed_at=990,outcome='accept'),
            dict(hospital='H01',submitted_at=800,observed_at=1001,outcome='decline')]
        # The later submitted terminal still legitimately updates beliefs. Its
        # completion cannot be used as the assumed release before the head.
        _,trace=queue(rows=rows,history=misleading)
        self.assertEqual(trace['estimated_start_at'],900)
        self.assertIsNone(trace['prior_terminal_reply'])
        self.assertEqual(base,queue(rows=rows,history=[misleading[1]]))

    def test_same_visible_input_does_not_gain_access_to_worker_or_service_truth(self):
        base=[dict(hospital='H01',patient='P',submitted_at=800)]
        private=[dict(base[0],workers=99,potential_service_seconds=900,
            actual_completed_at=999,future_release_at=1001)]
        self.assertEqual(queue(rows=base),queue(rows=private))


class ResidualPolicyTests(unittest.TestCase):
    def test_spec_changes_only_identity_and_is_detached(self):
        before=old.spec('eat_l15_f');after=new.spec(new.NAME)
        for key in ('name','label'):before.pop(key);after.pop(key)
        self.assertEqual(before,after)
        after['config']['lambda_seconds']=999
        self.assertEqual(new.spec()['config']['lambda_seconds'],15)
        for name in (None,'eat_l15_f','eat_residual_l30_f'):
            with self.assertRaises(ValueError):new.spec(name)

    def test_all_probability_and_schedule_kernels_remain_original(self):
        for name in ('Beliefs','expected_cost','_lognorm_cdf','_masses','_no_accept_by','_shift'):
            self.assertIs(getattr(new,name),getattr(old,name))

    def test_no_visible_queue_keeps_original_output_core_exactly(self):
        for n in (0,1,6,10):
            for limit in (0,1,10):
                args=inputs(n,max_new=limit)
                self.assertEqual(core(new.decide(new.NAME,**args)),old.decide('eat_l15_f',**args))

    def test_fresh_queues_keep_original_output_core_exactly(self):
        rows=[dict(hospital=f'H{i:02}',patient=f'O{i}-{j}',submitted_at=1000)
            for i in range(1,7) for j in range(3)]
        args=inputs(8,pending=rows)
        self.assertEqual(core(new.decide(new.NAME,**args)),old.decide('eat_l15_f',**args))

    def test_evidence_covers_only_original_nearest_six_and_all_numbers_are_public(self):
        rows=[dict(hospital='H01',patient='OTHER',submitted_at=800)]
        result=new.decide(new.NAME,**inputs(10,pending=rows))
        evidence=result['evidence']['residual_queue']
        self.assertEqual(evidence['schema_version'],1)
        self.assertEqual(evidence['rule'],'head_residual_plus_full_followers')
        self.assertEqual([r['hospital'] for r in evidence['hospitals']],[f'H{i:02}' for i in range(1,7)])
        self.assertNotIn('shortlist',result['evidence'])
        for trace in evidence['hospitals']:
            self.assertEqual(trace['assumed_workers'],1)
            self.assertGreaterEqual(trace['estimated_wait_seconds'],0)
            self.assertTrue(math.isfinite(trace['estimated_wait_seconds']))
            self.assertEqual(trace['original_full_wait_seconds'],trace['queue_count']*trace['mean_service_seconds'])

    def test_current_pending_item_and_concurrency_behavior_are_unchanged(self):
        pending=[dict(hospital=f'OWN{i}',patient='P',submitted_at=995) for i in range(4)]
        own=[dict(p,travel_seconds=300+i) for i,p in enumerate(pending)]
        args=inputs(8,pending=pending,patient_pending=own,last_contact_at=995)
        actual=new.decide(new.NAME,**args)
        self.assertEqual(actual['send'],[])
        self.assertEqual(actual['reason'],'eat_concurrency_cap')
        self.assertEqual(core(actual),old.decide('eat_l15_f',**args))

    def test_current_queue_inputs_and_outputs_are_deterministic_and_unmodified(self):
        rows=[dict(hospital='H01',patient='OTHER',submitted_at=800)]
        args=inputs(8,pending=rows);frozen=deepcopy(args)
        first=new.decide(new.NAME,**args);second=new.decide(new.NAME,**args)
        self.assertEqual(first,second);self.assertEqual(args,frozen)

    def test_future_ledger_rows_are_excluded_before_the_residual_estimate(self):
        args=inputs(8,pending=[dict(hospital='H01',patient='OTHER',submitted_at=800)])
        result=new.decide(new.NAME,**args)
        args['pending'].append(dict(hospital='H01',patient='FUTURE',submitted_at=1001))
        args['observations'].append(dict(hospital='H01',submitted_at=800,observed_at=1001,outcome='decline'))
        self.assertEqual(new.decide(new.NAME,**args),result)

    def test_zero_budget_preserves_original_no_send_and_has_no_invented_pool_records(self):
        args=inputs(8,max_new=0,pending=[dict(hospital='H01',patient='OTHER',submitted_at=800)])
        result=new.decide(new.NAME,**args)
        self.assertEqual(result['send'],[])
        self.assertEqual(result['evidence']['residual_queue']['hospitals'],[])
        self.assertEqual(core(result),old.decide('eat_l15_f',**args))

    def test_ast_changes_only_new_item_wait_and_its_recorded_evidence(self):
        original=ast.parse(inspect.getsource(old.decide)).body[0]
        changed=ast.parse(inspect.getsource(new.decide)).body[0]
        old_item=next(n for n in original.body if isinstance(n,ast.FunctionDef) and n.name=='new_item')
        new_item=next(n for n in changed.body if isinstance(n,ast.FunctionDef) and n.name=='new_item')
        old_wait=next(n for n in old_item.body if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='wait' for t in n.targets))
        replacements=0
        for i,n in enumerate(new_item.body):
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Tuple)
                    and any(isinstance(v,ast.Name) and v.id=='wait' for v in t.elts) for t in n.targets):
                new_item.body[i]=deepcopy(old_wait);replacements+=1
        self.assertEqual(replacements,1)
        changed.body=[n for n in changed.body if not (isinstance(n,ast.Assign)
            and any((isinstance(t,ast.Name) and t.id=='residual_queue_records') or
                (isinstance(t,ast.Subscript) and isinstance(t.value,ast.Name) and t.value.id=='evidence'
                 and isinstance(t.slice,ast.Constant) and t.slice.value=='residual_queue') for t in n.targets))]
        self.assertEqual(ast.dump(changed,include_attributes=False),ast.dump(original,include_attributes=False))


if __name__=='__main__':unittest.main()
