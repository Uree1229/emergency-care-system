"""Independent wait-first staging, exact guard retention and source mutations."""
from copy import deepcopy
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_waitpareto_audit as audit
import test_inquiry_lab_cohort_pareto_audit as prior_tests

ROOT=Path(__file__).resolve().parents[2]


def row(index,probabilities=(.4,.4,.4),wait=100.,ids=('A','B','OLD'),tiers=(4,2,1)):
    members=[dict(patient_id=pid,effective_priority=tier,
        metrics=dict(selection_probability=p,capped_wait_seconds=wait if i==0 else 0.))
        for i,(pid,tier,p) in enumerate(zip(ids,tiers,probabilities))]
    return dict(row_index=index,member_metrics=members,
        tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in members if m['effective_priority']==t),0.0) for t in (4,2,1)},
        sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in members),0.0))


def choose(rows,*,admitted=None,baseline=7,owners=None,menu=None,feasible=None):
    owners=prior_tests.inventory() if owners is None else owners
    menu=[r['row_index'] for r in rows] if menu is None else menu
    feasible=sorted(menu) if feasible is None else feasible
    if admitted is None:
        admitted=audit.foundation.guard_rows(rows,baseline_index=baseline,owner_inventory=owners,
            menu_indices=menu,feasible_row_indices=feasible)['admissible_row_indices']
    return audit.select_protected_wait(rows,admissible_row_indices=admitted,baseline_index=baseline,owner_inventory=owners,
        menu_indices=menu,feasible_row_indices=feasible)


def native(events,registry,context,members,tails,owners,source=None):
    for event in events:
        if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
    for job in registry['jobs']:job['policy_name']=audit.NAME
    for r in context['eligible']:r['job']['policy_name']=audit.NAME
    for member in members+tails:member['workflow_snapshot']['job']['policy_name']=audit.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None:owner['job']['policy_name']=audit.NAME
    if source is not None:
        for r in source['rows']:
            if r['job'] is not None:r['job']['policy_name']=audit.NAME
        for event in source['events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME


def output(ms,c,ts,p,owners,original):
    old=original['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    anchor=old['stoploss_anchor_index'];full=old['original_fullmenu_proposal']['evidence'];selection=None
    if evaluated:
        selection=audit.select_protected_wait(full['row_scores'],admissible_row_indices=old['owner_probability_guard']['admissible_row_indices'],
            baseline_index=anchor,owner_inventory=owners['inventory'],menu_indices=full['menu_indices'],feasible_row_indices=full['feasible_row_indices'])
        selected=selection['selected_index']
    intervened=selected!=base_index;decisions=deepcopy(original['decisions'])
    if intervened:
        table=audit.foundation.foundation.parent_views(full['original_stoploss_proposal'])['joint']
        chosen=next(r for r in full['row_scores'] if r['row_index']==selected);decisions=deepcopy(table['baseline_proposals'])
        for i,d in enumerate(decisions):
            sends=table['combinations'][selected]['hospitals'][i]
            d.update(send=list(sends),reason='cohort_waitpareto_current_only');d['evidence']['sent_hospitals']=list(sends)
            d['evidence']['planned_expected_cost']=round(chosen['member_metrics'][i]['metrics']['total_cost_seconds'],3)
            for item in d['evidence']['candidate_plan']:item['planned_offset_seconds']=0 if item['hospital'] in sends else None
    for i,d in enumerate(decisions):
        d['evidence']['policy']=audit.NAME
        d['evidence']['cohort_waitpareto']=dict(schema_version=1,mode='pair_cohort_waitpareto',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,stoploss_anchor_index=anchor,
            selected_index=selected,effective_priority=p['patients'][i]['effective_priority'])
    evidence=dict(schema_version=1,rule=audit.RULE,base_policy=audit.BASE_POLICY,base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME,calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_pareto__'),
        joint_context=deepcopy(c),priority_context=deepcopy(p),owner_context=deepcopy(owners),original_pareto_proposal=deepcopy(original),
        evaluated=evaluated,protected_wait_selection=selection,base_selected_index=base_index,stoploss_anchor_index=anchor,
        selected_index=selected,intervened=intervened,tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),
        objective=audit.OBJECTIVE,scope=audit.SCOPE)
    return decisions,evidence


def fixture():
    events,registry,c,ms,ts,p,owners,d,e=prior_tests.fixture();original=dict(decisions=deepcopy(d),evidence=deepcopy(e))
    native(events,registry,c,ms,ts,owners);d,e=output(ms,c,ts,p,owners,original)
    return events,registry,c,ms,ts,p,owners,d,e


class WaitFirstSelectionTests(unittest.TestCase):
    def test_wait_precedes_large_extra_probability_gains_inside_same_guard(self):
        rows=[row(7,wait=100.),row(2,(.9,.9,.9),120.),row(1,(.5,.5,.5),10.)];before=deepcopy(rows)
        self.assertEqual(audit.dynamic.base.global_staged_choice(rows,7)['selected_index'],2)
        proof=choose(rows);self.assertEqual(proof['selected_index'],1);self.assertEqual(proof['wait_surviving_row_indices'],[1])
        self.assertEqual(rows,before);self.assertFalse(proof['additional_baseline_wait_cap_applied'])
        self.assertLessEqual(proof['selected_sum_capped_wait_seconds'],proof['baseline_sum_capped_wait_seconds']+1e-9)

    def test_owner_probability_loss_stays_excluded_even_at_zero_wait(self):
        rows=[row(7),row(3,(.9,.3,.9),0.),row(2,(.5,.5,.5),10.)]
        proof=choose(rows);self.assertEqual(proof['admissible_row_indices'],[7,2]);self.assertEqual(proof['selected_index'],2)
        with self.assertRaises(ValueError):choose(rows,admitted=[7,3,2])

    def test_wait_band_exact_epsilon_and_next_float(self):
        for wait,selected in ((1e-9,1),(math.nextafter(1e-9,math.inf),7)):
            proof=choose([row(7,wait=0.),row(1,(.5,.5,.5),wait)])
            self.assertEqual(proof['selected_index'],selected)
            self.assertTrue(proof['selected_wait_within_baseline_tolerance'])

    def test_each_priority_band_uses_existing_epsilon_after_wait_filter(self):
        for tier_index in range(3):
            for low,anchor_survives in ((.5-1e-12,True),(math.nextafter(.5-1e-12,-math.inf),False)):
                before=[.5,.5,.5];before[tier_index]=low
                proof=choose([row(7,tuple(before),0.),row(1,(.5,.5,.5),0.)])
                self.assertEqual(proof['selected_index'],7 if anchor_survives else 1)

    def test_stoploss_anchor_then_original_source_order_and_no_new_cap(self):
        proof=choose([row(3,wait=10.),row(7,wait=10.),row(2,wait=10.)]);self.assertEqual(proof['selected_index'],7)
        proof=choose([row(3,(.5,.5,.5),10.),row(7,wait=100.),row(2,(.5,.5,.5),10.)])
        self.assertEqual(proof['selected_index'],3);self.assertEqual(proof['final_surviving_row_indices'],[3,2])
        self.assertFalse(proof['additional_baseline_wait_cap_applied'])

    def test_admissible_omission_extra_reordering_duplicates_and_missing_anchor_rejected(self):
        rows=[row(7),row(2,(.5,.5,.5),10.),row(1,(.6,.6,.6),20.)]
        for admitted in ([7],[7,1,2],[7,2,2,1],[2,1],[True,2,1],[]):
            with self.subTest(admitted=admitted),self.assertRaises(ValueError):choose(rows,admitted=admitted)
        with self.assertRaises(ValueError):choose([row(1)],baseline=1,admitted=[True])

    def test_complete_source_owner_partitions_aggregates_and_finite_values_required(self):
        edits=(lambda r:r['member_metrics'].pop(),lambda r:r['member_metrics'][1].__setitem__('patient_id','A'),
            lambda r:r['member_metrics'][1].__setitem__('effective_priority',4),
            lambda r:r['member_metrics'][0]['metrics'].__setitem__('capped_wait_seconds',float('nan')),
            lambda r:r.__setitem__('sum_capped_wait_seconds',101.),
            lambda r:r['tier_probabilities'].__setitem__('4',.9),lambda r:r['tier_probabilities'].__setitem__('4',True),
            lambda r:r.__setitem__('sum_capped_wait_seconds',True))
        for edit in edits:
            rows=[row(7),row(2)];edit(rows[1])
            with self.assertRaises(ValueError):choose(rows,admitted=[7,2])


class IntactParentAndChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=fixture()

    def check(self,d=None,e=None):
        _,_,c,ms,ts,p,o,ds,ev=self.data
        return audit.audit_pair(ms,c,ts,ds if d is None else d,ev if e is None else e,p,o)

    def test_parent_and_guard_unchanged_and_selected_wait_bound_checked(self):
        before=deepcopy(self.data);proof=self.check();self.assertEqual(self.data,before)
        self.assertEqual(proof['waitpareto_selected_wait_nonregression_checked'],1)
        p=self.data[-1]['protected_wait_selection'];self.assertTrue(p['selected_wait_within_baseline_tolerance'])
        self.assertEqual(proof['waitpareto_rows_checked'],len(self.data[-1]['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']['row_scores']))

    def test_every_selection_proof_field_is_bound(self):
        source=self.data[-1]['protected_wait_selection']
        for key,value in source.items():
            e=deepcopy(self.data[-1]);p=e['protected_wait_selection']
            if type(value) is list:p[key]=value[:-1] if value else ['TAMPER']
            elif type(value) is dict:p[key]=dict(value,invented=True)
            elif type(value) is bool:p[key]=not value
            elif type(value) is str:p[key]=value+'_TAMPER'
            else:p[key]=value+1
            with self.subTest(key=key),self.assertRaises(ValueError):self.check(e=e)

    def test_wait_survivors_priority_maxima_anchor_and_selected_wait_tampering_rejected(self):
        edits=(lambda p:p['priority_filters'][0].__setitem__('maximum_probability',99.),
            lambda p:p['priority_filters'][1]['surviving_row_indices'].clear(),
            lambda p:p['wait_surviving_row_indices'].clear(),lambda p:p.__setitem__('baseline_row_index',999),
            lambda p:p.__setitem__('selected_sum_capped_wait_seconds',0.))
        for edit in edits:
            e=deepcopy(self.data[-1]);edit(e['protected_wait_selection'])
            with self.assertRaises(ValueError):self.check(e=e)

    def test_parent_guard_omission_baseline_identity_future_input_and_scores_are_bound(self):
        edits=(lambda e:e['owner_probability_guard']['admissible_row_indices'].clear(),
            lambda e:e['owner_probability_guard']['baseline_owner_probabilities'].pop(),
            lambda e:e.__setitem__('stoploss_anchor_index',999),
            lambda e:e['original_fullmenu_proposal']['evidence']['row_scores'].pop(),
            lambda e:e['joint_context'].__setitem__('future_outcomes',[1]),
            lambda e:e['owner_context']['inventory'].pop())
        for edit in edits:
            e=deepcopy(self.data[-1]);edit(e['original_pareto_proposal']['evidence'])
            with self.assertRaises(ValueError):self.check(e=e)

    def test_current_outputs_and_parent_actual_index_are_separately_bound(self):
        for key in ('base_selected_index','stoploss_anchor_index','selected_index'):
            e=deepcopy(self.data[-1]);e[key]=999
            with self.assertRaises(ValueError):self.check(e=e)
        for edit in (lambda d:d[0]['send'].append('HIDDEN'),lambda d:d[0]['evidence']['cohort_waitpareto'].__setitem__('selected_index',999),
                lambda d:d[0]['evidence'].__setitem__('planned_expected_cost',-1.)):
            d=deepcopy(self.data[-2]);edit(d)
            with self.assertRaises(ValueError):self.check(d=d)


class DelegationAndFallbackTests(unittest.TestCase):
    def test_saved_singleton_horizon_fallback_source_and_fifo_unchanged(self):
        raw=prior_tests.horizon_tests.replay();b=raw['branches'][0];b['joint_groups']=[]
        for event in b['db_events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
        for call,decision in zip(b['policy_calls'],b['db_decisions']):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            for candidate in call['inputs']['candidates']:candidate['resources_enough']=True
            evidence=call['output']['evidence'];evidence['policy']=audit.NAME
            evidence['joint_tail']=dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False)
            evidence['joint_first']=dict(schema_version=1,mode='horizon_fallback',current_only=False)
            evidence['joint_budget']=dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False)
            for name in ('cohort_success','cohort_dynamic','cohort_hedge','cohort_recourse','cohort_followplan',
                    'cohort_stoploss','cohort_fullmenu','cohort_pareto','cohort_waitpareto'):
                evidence[name]=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
            decision['evidence']=deepcopy(evidence)
        b.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        self.assertTrue(audit.audit_replay(raw)['passed'])
        bad=deepcopy(raw);bad['branches'][0]['policy_calls'][0]['output']['evidence']['cohort_pareto']['evaluated']=True
        with self.assertRaises(ValueError):audit.audit_replay(bad)

    def test_parent_controls_delegate_to_frozen_pareto_chain(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y')
        proof=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=proof) as call:self.assertIs(audit.audit_replay(raw),proof)
        call.assert_called_once_with(raw)

    def test_import_has_no_producer_and_does_not_mutate_frozen_hooks(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_waitpareto_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        value=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(value.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
