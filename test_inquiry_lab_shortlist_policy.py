"""Fixed shortlist membership intervention; unit inputs only, no formal seeds."""
import ast
from copy import deepcopy
import inspect
import math
import unittest

from inquiry_lab import eat_policy as original,shortlist_policy as new


def candidate(hid,travel):
    return dict(hospital_id=hid,travel=dict(duration_seconds=travel),resources_enough=True)


def candidates(n):
    return [candidate(f'H{i:02}',300+10*i) for i in range(1,n+1)]


def history(n,good,now=1000):
    # Valid received history: better candidates have actual prior acceptances.
    return [dict(hospital=f'H{i:02}',patient=f'OLD-{i}-{j}',submitted_at=now-405+j*15,
        observed_at=now-400+j*15,outcome='accept' if i in good else 'decline')
        for i in range(1,n+1) for j in range(20)]


def args(n=10,**kw):
    value=dict(now=1000,candidates=candidates(n),observations=[],pending=[],patient_pending=[],max_new=10)
    value.update(kw)
    return value


def core(result):
    value=deepcopy(result)
    value['evidence'].pop('shortlist',None)
    value['evidence']['policy']=original.spec('eat_l15_f')['name']
    return value


class ShortlistPolicyTests(unittest.TestCase):
    def test_spec_changes_identity_only_and_returns_detached_config(self):
        old=original.spec('eat_l15_f');one=new.spec(new.NAME);two=new.spec(new.NAME)
        for key in ('name','label'):
            old.pop(key);one.pop(key)
        self.assertEqual(one,old)
        one['config']['lambda_seconds']=999
        self.assertEqual(two['config']['lambda_seconds'],15)
        self.assertEqual(two['config']['max_concurrent'],4)
        self.assertEqual(two['config']['max_candidates_evaluated'],6)
        self.assertFalse(two['config']['congestion_aware'])

    def test_name_is_one_fixed_candidate(self):
        self.assertEqual(new.NAME,'eat_shortlist_l15_f')
        for name in ('eat_l15_f','eat_shortlist_l30_f','eat_shortlist_l15_c',None,7):
            with self.assertRaises(ValueError):new.spec(name)

    def test_probability_and_planning_kernels_are_original_objects(self):
        for name in ('Beliefs','expected_cost','_lognorm_cdf','_masses','_no_accept_by','_shift'):
            self.assertIs(getattr(new,name),getattr(original,name))

    def test_seventh_candidate_can_enter_without_reordering_the_plan(self):
        value=args(7,observations=history(7,{7}))
        old=original.decide('eat_l15_f',**value);actual=new.decide(new.NAME,**value)
        trace=actual['evidence']['shortlist']
        self.assertNotIn('H07',[r['hospital'] for r in old['evidence']['candidate_plan']])
        self.assertIn('H07',trace['selected'])
        self.assertIn('H07',actual['send'])
        self.assertTrue(trace['membership_changed'])
        self.assertEqual(len(trace['selected']),6)

    def test_tenth_candidate_with_received_support_is_not_travel_pruned(self):
        value=args(10,observations=history(10,{10}))
        actual=new.decide(new.NAME,**value)
        trace=actual['evidence']['shortlist']
        self.assertIn('H10',trace['selected'])
        self.assertIn('H10',actual['send'])
        self.assertEqual(len(trace['scores']),10)
        self.assertEqual(trace['original_nearest'],[f'H{i:02}' for i in range(1,7)])

    def test_score_selects_membership_but_coordinate_order_stays_input_order(self):
        value=args(10,observations=history(10,{9,10}))
        actual=new.decide(new.NAME,**value);trace=actual['evidence']['shortlist']
        ranked=sorted(trace['scores'],key=lambda r:(r['score'],r['travel_seconds'],r['hospital']))
        membership={r['hospital'] for r in ranked[:6]}
        expected=[r['hospital_id'] for r in value['candidates'] if r['hospital_id'] in membership]
        self.assertEqual(trace['selected'],expected)
        self.assertEqual([r['hospital'] for r in actual['evidence']['candidate_plan']],expected)
        self.assertNotEqual(trace['selected'],[r['hospital'] for r in ranked[:6]])

    def test_current_public_pending_can_promote_an_uncongested_seventh_option(self):
        pending=[dict(hospital=f'H{i:02}',patient=f'OTHER-{i}-{j}',submitted_at=990)
            for i in range(1,7) for j in range(3)]
        actual=new.decide(new.NAME,**args(7,pending=pending))
        self.assertIn('H07',actual['evidence']['shortlist']['selected'])
        self.assertIn('H07',[r['hospital'] for r in actual['evidence']['candidate_plan']])
        self.assertLessEqual(len(actual['send']),4)

    def test_shortlist_has_a_complete_finite_score_for_every_current_candidate(self):
        value=args(10,observations=history(10,{7,10}))
        actual=new.decide(new.NAME,**value);trace=actual['evidence']['shortlist']
        self.assertEqual(trace['rule'],'expected_single_addition_cost')
        self.assertEqual([r['hospital'] for r in trace['scores']],
            [r['hospital_id'] for r in value['candidates']])
        for row in trace['scores']:
            self.assertEqual(set(row),{'hospital','travel_seconds','expected_arrival_cost','inquiry_cost','score','selected'})
            self.assertTrue(math.isfinite(row['score']))
            self.assertEqual(row['score'],row['expected_arrival_cost']+row['inquiry_cost'])
            self.assertEqual(row['inquiry_cost'],15)
            self.assertEqual(row['selected'],row['hospital'] in trace['selected'])
        self.assertEqual(set(actual['send'])-set(trace['selected']),set())

    def test_all_candidates_fitting_pool_keep_exact_original_core_output(self):
        for n in range(7):
            for with_history in (False,True):
                with self.subTest(n=n,history=with_history):
                    value=args(n,observations=history(n,{n}) if with_history else [])
                    actual=new.decide(new.NAME,**value)
                    self.assertEqual(core(actual),original.decide('eat_l15_f',**value))
                    self.assertFalse(actual['evidence']['shortlist']['membership_changed'])

    def test_unchanged_nearest_membership_keeps_exact_core_even_with_ten_candidates(self):
        value=args(10)
        actual=new.decide(new.NAME,**value)
        self.assertFalse(actual['evidence']['shortlist']['membership_changed'])
        self.assertEqual(core(actual),original.decide('eat_l15_f',**value))

    def test_zero_contact_budget_and_empty_candidates_preserve_original_core(self):
        for value in (args(10,max_new=0),args(0)):
            actual=new.decide(new.NAME,**value)
            self.assertEqual(actual['send'],[])
            self.assertEqual(actual['evidence']['shortlist']['selected'],[])
            self.assertEqual(core(actual),original.decide('eat_l15_f',**value))

    def test_contact_budget_limits_pool_and_immediate_send(self):
        value=args(10,observations=history(10,{10}),max_new=1)
        actual=new.decide(new.NAME,**value)
        self.assertEqual(actual['evidence']['shortlist']['limit'],1)
        self.assertEqual(actual['evidence']['shortlist']['selected'],['H10'])
        self.assertEqual(actual['send'],['H10'])

    def test_original_concurrency_room_and_stale_count_are_retained(self):
        pending=[dict(hospital=f'OWN{i}',patient='P',submitted_at=995) for i in range(4)]
        own=[dict(p,travel_seconds=300+i) for i,p in enumerate(pending)]
        value=args(10,pending=pending,patient_pending=own)
        actual=new.decide(new.NAME,**value)
        self.assertEqual(actual['send'],[])
        self.assertEqual(actual['reason'],'eat_concurrency_cap')
        self.assertEqual(actual['evidence']['own_waiting_count'],4)
        pending=[dict(p,submitted_at=800) for p in pending]
        own=[dict(p,travel_seconds=300+i) for i,p in enumerate(pending)]
        stale=new.decide(new.NAME,**args(10,pending=pending,patient_pending=own))
        self.assertEqual(stale['evidence']['own_observation_timed_out_count'],4)
        self.assertGreaterEqual(len(stale['send']),1)
        self.assertLessEqual(len(stale['send']),4)

    def test_existing_pending_small_pool_matches_original_exactly(self):
        for submitted in (995,880,800):
            pending=[dict(hospital='OLD',patient='P',submitted_at=submitted)]
            own=[dict(pending[0],travel_seconds=320)]
            value=args(4,pending=pending,patient_pending=own,last_contact_at=900)
            self.assertEqual(core(new.decide(new.NAME,**value)),original.decide('eat_l15_f',**value))

    def test_future_observations_and_pending_do_not_enter_scores(self):
        value=args(10,observations=history(10,{10}))
        base=new.decide(new.NAME,**value)
        value['observations'].append(dict(hospital='H01',patient='FUTURE',submitted_at=1001,observed_at=1010,outcome='accept'))
        value['pending'].append(dict(hospital='H01',patient='FUTURE',submitted_at=1001))
        self.assertEqual(new.decide(new.NAME,**value),base)

    def test_inputs_are_unchanged_and_results_repeat_exactly(self):
        value=args(10,observations=history(10,{10}))
        before=deepcopy(value)
        one=new.decide(new.NAME,**value);two=new.decide(new.NAME,**value)
        self.assertEqual(one,two);self.assertEqual(value,before)

    def test_equal_scores_use_travel_then_hospital_for_membership(self):
        rows=[candidate(h,300) for h in ('Z','Y','X','W','V','U','A')]
        actual=new.decide(new.NAME,**args(0,candidates=rows))
        selected=actual['evidence']['shortlist']['selected']
        self.assertNotIn('Z',selected)
        self.assertEqual(selected,['Y','X','W','V','U','A'])

    def test_original_validation_errors_are_retained(self):
        for value in (args(1,now=True),args(1,max_new=-1),args(1,max_new=True),
            args(0,candidates=[candidate('H',1),candidate('H',2)]),
            args(1,patient_pending=[dict(hospital='H01',patient='P',submitted_at=990,travel_seconds=300)])):
            with self.assertRaises(ValueError):new.decide(new.NAME,**value)

    def test_ast_changes_only_pool_screen_and_added_shortlist_evidence(self):
        old=ast.parse(inspect.getsource(original.decide)).body[0]
        changed=ast.parse(inspect.getsource(new.decide)).body[0]
        start=next(i for i,n in enumerate(changed.body) if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='shortlist_limit' for t in n.targets))
        end=next(i for i,n in enumerate(changed.body[start:],start) if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='items' for t in n.targets))
        old_pool=next(n for n in old.body if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='pool' for t in n.targets))
        changed.body[start:end]=[deepcopy(old_pool)]
        changed.body=[n for n in changed.body if not (isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Subscript) and isinstance(t.value,ast.Name)
                and t.value.id=='evidence' and isinstance(t.slice,ast.Constant)
                and t.slice.value=='shortlist' for t in n.targets))]
        self.assertEqual(ast.dump(changed,include_attributes=False),ast.dump(old,include_attributes=False))


if __name__=='__main__':unittest.main()
