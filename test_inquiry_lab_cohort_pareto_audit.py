"""Independent owner guard boundaries, partitions, immutable proof and outputs."""
from copy import deepcopy
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_pareto_audit as audit
import test_inquiry_lab_cohort_fullmenu_audit as prior_tests
import test_inquiry_lab_horizon_audit as horizon_tests

ROOT=Path(__file__).resolve().parent


def inventory(ids=('A','B','OLD'),tiers=(4,2,1)):
    return [dict(patient=dict(id=pid,effective_priority=tier)) for pid,tier in zip(ids,tiers)]


def score(index,probabilities,*,ids=('A','B','OLD'),tiers=(4,2,1),wait=100.):
    members=[dict(patient_id=pid,effective_priority=tier,metrics=dict(selection_probability=p))
        for pid,tier,p in zip(ids,tiers,probabilities)]
    return dict(row_index=index,member_metrics=members,
        tier_probabilities={str(t):sum(m['metrics']['selection_probability'] for m in members if m['effective_priority']==t) for t in (4,2,1)},
        sum_capped_wait_seconds=wait)


def guard(rows,*,owners=None,baseline=7,menu=None,feasible=None):
    menu=[r['row_index'] for r in rows] if menu is None else menu
    return audit.guard_rows(rows,baseline_index=baseline,owner_inventory=inventory() if owners is None else owners,
        menu_indices=menu,feasible_row_indices=sorted(menu) if feasible is None else feasible)


def native(events,registry,context,members,tails,owners,source=None):
    for event in events:
        if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
    for job in registry['jobs']:job['policy_name']=audit.NAME
    for row in context['eligible']:row['job']['policy_name']=audit.NAME
    for member in members+tails:member['workflow_snapshot']['job']['policy_name']=audit.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None:owner['job']['policy_name']=audit.NAME
    if source is not None:
        for row in source['rows']:
            if row['job'] is not None:row['job']['policy_name']=audit.NAME
        for event in source['events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME


def output(ms,c,ts,p,owners,original):
    old=original['evidence'];evaluated=old['evaluated'];base_index=old['selected_index'];selected=base_index
    anchor=old['base_selected_index'];proof=None;selection=None
    if evaluated:
        proof=audit.guard_rows(old['row_scores'],baseline_index=anchor,owner_inventory=owners['inventory'],
            menu_indices=old['menu_indices'],feasible_row_indices=old['feasible_row_indices'])
        admitted=set(proof['admissible_row_indices']);rows=[r for r in old['row_scores'] if r['row_index'] in admitted]
        selection=audit.dynamic.base.global_staged_choice(rows,anchor)
        selection['base_selected_tied']=selection.pop('base_budget_tied');selected=selection['selected_index']
    intervened=selected!=base_index;decisions=deepcopy(original['decisions'])
    if intervened:
        table=audit.foundation.parent_views(old['original_stoploss_proposal'])['joint']
        chosen=next(r for r in old['row_scores'] if r['row_index']==selected);decisions=deepcopy(table['baseline_proposals'])
        for i,d in enumerate(decisions):
            sends=table['combinations'][selected]['hospitals'][i]
            d.update(send=list(sends),reason='cohort_pareto_current_only');d['evidence']['sent_hospitals']=list(sends)
            d['evidence']['planned_expected_cost']=round(chosen['member_metrics'][i]['metrics']['total_cost_seconds'],3)
            for item in d['evidence']['candidate_plan']:item['planned_offset_seconds']=0 if item['hospital'] in sends else None
    for i,d in enumerate(decisions):
        d['evidence']['policy']=audit.NAME
        d['evidence']['cohort_pareto']=dict(schema_version=1,mode='pair_cohort_pareto',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,stoploss_anchor_index=anchor,
            selected_index=selected,effective_priority=p['patients'][i]['effective_priority'])
    evidence=dict(schema_version=1,rule=audit.RULE,base_policy=audit.BASE_POLICY,base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME,calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_fullmenu__'),
        joint_context=deepcopy(c),priority_context=deepcopy(p),owner_context=deepcopy(owners),original_fullmenu_proposal=deepcopy(original),
        evaluated=evaluated,owner_probability_guard=proof,selection=selection,base_selected_index=base_index,
        stoploss_anchor_index=anchor,selected_index=selected,intervened=intervened,tiers=[4,2,1],
        tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=audit.OBJECTIVE,scope=audit.SCOPE)
    return decisions,evidence


def fixture():
    events,registry,c,ms,ts,p,owners,d,e=prior_tests.fixture();original=dict(decisions=deepcopy(d),evidence=deepcopy(e))
    native(events,registry,c,ms,ts,owners);d,e=output(ms,c,ts,p,owners,original)
    return events,registry,c,ms,ts,p,owners,d,e


class IndependentOwnerGuardTests(unittest.TestCase):
    def test_net_gain_and_higher_tier_gain_cannot_mask_one_owner_loss(self):
        rows=[score(7,[.4,.4,.4]),score(3,[.9,.3,.9],wait=0.),score(2,[.5,.5,.5],wait=1.)]
        before=deepcopy(rows);proof=guard(rows)
        self.assertEqual(proof['admissible_row_indices'],[7,2]);self.assertEqual(proof['rejected_row_indices'],[3])
        regression=proof['row_checks'][1]['owner_regressions'];self.assertEqual([r['patient_id'] for r in regression],['B'])
        self.assertEqual(rows,before)
        self.assertEqual(audit.dynamic.base.global_staged_choice([rows[0],rows[2]],7)['selected_index'],2)

    def test_every_priority_tier_and_residual_owner_is_guarded(self):
        for owner_index in range(3):
            values=[.5,.5,.5];values[owner_index]=.49
            proof=guard([score(7,[.5,.5,.5]),score(1,values)])
            self.assertEqual(proof['rejected_row_indices'],[1]);self.assertEqual(proof['row_checks'][1]['owner_regressions'][0]['effective_priority'],(4,2,1)[owner_index])

    def test_exact_existing_epsilon_boundary_and_adjacent_floats(self):
        for baseline_probability in (0.,.5,1.):
            threshold=baseline_probability-1e-12
            if threshold<0:
                self.assertEqual(guard([score(7,[0.,0.,0.]),score(1,[0.,0.,0.])])['admissible_row_indices'],[7,1]);continue
            for candidate,accepted in ((threshold,True),(math.nextafter(threshold,math.inf),True),(math.nextafter(threshold,-math.inf),False)):
                proof=guard([score(7,[baseline_probability]*3),score(1,[candidate]*3)])
                self.assertEqual(1 in proof['admissible_row_indices'],accepted)

    def test_typed_integer_and_string_ids_do_not_alias_and_order_is_captured(self):
        ids=(1,'1','OLD');owners=inventory(ids=ids)
        proof=guard([score(7,[.3,.4,.5],ids=ids),score(2,[.3,.4,.5],ids=ids)],owners=owners)
        self.assertEqual([p['patient_id'] for p in proof['baseline_owner_probabilities']],[1,'1','OLD'])
        self.assertEqual(proof['compared_owner_count'],3)
        with self.assertRaises(ValueError):guard([score(7,[.3,.4,.5],ids=(True,'1','OLD'))],owners=owners)

    def test_missing_duplicate_extra_owner_and_priority_partition_rejected_in_every_row(self):
        edits=(lambda r:r['member_metrics'].pop(),lambda r:r['member_metrics'].append(deepcopy(r['member_metrics'][0])),
            lambda r:r['member_metrics'][1].__setitem__('patient_id','A'),
            lambda r:r['member_metrics'][1].__setitem__('patient_id','HIDDEN'),
            lambda r:r['member_metrics'][1].__setitem__('effective_priority',4))
        for row_index in (0,1):
            for edit in edits:
                rows=[score(7,[.5]*3),score(2,[.5]*3)];edit(rows[row_index])
                with self.assertRaises(ValueError):guard(rows)

    def test_omitted_reordered_duplicate_menu_feasible_or_baseline_row_rejected(self):
        rows=[score(7,[.5]*3),score(2,[.5]*3)]
        for kwargs in (dict(menu=[7]),dict(menu=[2,7]),dict(menu=[7,7]),dict(feasible=[7]),dict(feasible=[7,2,2]),dict(baseline=99),dict(baseline=True)):
            with self.assertRaises(ValueError):guard(rows,**kwargs)
        with self.assertRaises(ValueError):guard([rows[0],deepcopy(rows[0])],menu=[7,2])

    def test_invalid_probability_or_inventory_is_not_silently_filtered(self):
        for invalid in (-1e-10,1.00001,float('nan'),float('inf'),True,None):
            rows=[score(7,[.5]*3),score(2,[.5]*3)];rows[1]['member_metrics'][0]['metrics']['selection_probability']=invalid
            with self.assertRaises(ValueError):guard(rows)
        for owners in ([],inventory(ids=('A','A','OLD')),inventory(tiers=(4,2,True))):
            with self.assertRaises(ValueError):guard([score(7,[.5]*3)],owners=owners)

    def test_baseline_always_admissible_and_guard_source_proof_is_detached(self):
        rows=[score(7,[.5]*3),score(2,[.4]*3)]
        p=guard(rows);self.assertTrue(p['baseline_always_admissible']);self.assertEqual(p['admissible_row_indices'],[7])
        p['source_sha256']['cohort_fullmenu_model']='changed'
        self.assertNotEqual(guard(rows)['source_sha256']['cohort_fullmenu_model'],'changed')


class CompleteParetoPairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=fixture()

    def check(self,d=None,e=None):
        _,_,c,ms,ts,p,o,ds,ev=self.data
        return audit.audit_pair(ms,c,ts,ds if d is None else d,ev if e is None else e,p,o)

    def test_parent_scores_coverage_unchanged_and_allowners_checked(self):
        before=deepcopy(self.data);proof=self.check();self.assertEqual(self.data,before)
        e=self.data[-1];guardproof=e['owner_probability_guard'];old=e['original_fullmenu_proposal']['evidence']
        self.assertEqual(guardproof['checked_row_count'],len(old['row_scores']))
        self.assertEqual(proof['pareto_owner_comparisons_checked'],len(old['row_scores'])*len(e['owner_context']['inventory']))
        self.assertEqual(e['stoploss_anchor_index'],old['original_stoploss_proposal']['evidence']['selected_index'])
        self.assertIn(e['stoploss_anchor_index'],guardproof['admissible_row_indices'])

    def test_every_guard_summary_and_baseline_probability_field_is_bound(self):
        original=self.data[-1]['owner_probability_guard']
        for key,value in original.items():
            e=deepcopy(self.data[-1]);p=e['owner_probability_guard']
            if type(value) is list:p[key]=value[:-1] if value else ['TAMPER']
            elif type(value) is dict:p[key]=dict(value,invented=True)
            elif type(value) is bool:p[key]=not value
            elif type(value) is str:p[key]=value+'_TAMPER'
            else:p[key]=value+1
            with self.subTest(key=key),self.assertRaises(ValueError):self.check(e=e)
        for key in original['baseline_owner_probabilities'][0]:
            e=deepcopy(self.data[-1]);p=e['owner_probability_guard']['baseline_owner_probabilities'][0]
            p[key]=p[key]+'_x' if type(p[key]) is str else p[key]+1
            with self.subTest(key=key),self.assertRaises(ValueError):self.check(e=e)

    def test_perrow_flags_regressions_and_admissible_coverage_tampering_rejected(self):
        changes=(lambda p:p['row_checks'][0].__setitem__('admissible',not p['row_checks'][0]['admissible']),
            lambda p:p['row_checks'][0].__setitem__('row_index',999),
            lambda p:p['row_checks'][0].__setitem__('verified_owner_count',999),
            lambda p:p['row_checks'][0].__setitem__('is_baseline',not p['row_checks'][0]['is_baseline']),
            lambda p:p['row_checks'][0]['owner_regressions'].append(dict(patient_id='A')),
            lambda p:p['admissible_row_indices'].clear(),lambda p:p['rejected_row_indices'].append(999))
        for edit in changes:
            e=deepcopy(self.data[-1]);edit(e['owner_probability_guard'])
            with self.assertRaises(ValueError):self.check(e=e)
        regression=next(r for r in self.data[-1]['owner_probability_guard']['row_checks'] if r['owner_regressions'])['owner_regressions'][0]
        for key,value in regression.items():
            e=deepcopy(self.data[-1]);actual=next(r for r in e['owner_probability_guard']['row_checks'] if r['owner_regressions'])['owner_regressions'][0]
            actual[key]=value+'_TAMPER' if type(value) is str else value+1
            with self.subTest(regression_field=key),self.assertRaises(ValueError):self.check(e=e)

    def test_parent_row_omission_identity_future_input_and_finalscore_tampering_rejected(self):
        changes=(lambda p:p['evidence']['row_scores'].pop(),
            lambda p:p['evidence']['row_scores'][0]['member_metrics'][0].__setitem__('patient_id','HIDDEN'),
            lambda p:p['evidence']['row_scores'][0]['member_metrics'][0]['metrics'].__setitem__('selection_probability',0.),
            lambda p:p['evidence']['joint_context'].__setitem__('future_outcomes',[1]),
            lambda p:p['evidence']['owner_context']['inventory'].pop())
        for edit in changes:
            e=deepcopy(self.data[-1]);edit(e['original_fullmenu_proposal'])
            with self.assertRaises(ValueError):self.check(e=e)

    def test_anchor_parent_actual_selection_and_current_outputs_are_distinctly_bound(self):
        for key in ('base_selected_index','stoploss_anchor_index','selected_index'):
            e=deepcopy(self.data[-1]);e[key]=999
            with self.assertRaises(ValueError):self.check(e=e)
        for edit in (lambda d:d[0]['send'].append('HIDDEN'),lambda d:d[0]['evidence']['cohort_pareto'].__setitem__('stoploss_anchor_index',999),
                lambda d:d[0]['evidence'].__setitem__('planned_expected_cost',-1.)):
            d=deepcopy(self.data[-2]);edit(d)
            with self.assertRaises(ValueError):self.check(d=d)


class FrozenDelegationTests(unittest.TestCase):
    def test_unchanged_singleton_fallback_actual_source_and_fifo(self):
        raw=horizon_tests.replay();branch=raw['branches'][0];branch['joint_groups']=[]
        for event in branch['db_events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            call['name']=decision['policy_name']=audit.NAME
            call['workflow_snapshot']['job']['policy_name']=audit.NAME
            for candidate in call['inputs']['candidates']:candidate['resources_enough']=True
            evidence=call['output']['evidence'];evidence['policy']=audit.NAME
            evidence['joint_tail']=dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False)
            evidence['joint_first']=dict(schema_version=1,mode='horizon_fallback',current_only=False)
            evidence['joint_budget']=dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False)
            for name in ('cohort_success','cohort_dynamic','cohort_hedge','cohort_recourse','cohort_followplan',
                    'cohort_stoploss','cohort_fullmenu','cohort_pareto'):
                evidence[name]=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
            decision['evidence']=deepcopy(evidence)
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        self.assertTrue(audit.audit_replay(raw)['passed'])
        for edit in (lambda r:r['branches'][0]['policy_calls'][0]['output']['evidence']['cohort_fullmenu'].__setitem__('evaluated',True),
                lambda r:r['branches'][0]['policy_calls'][0]['workflow_snapshot']['job'].__setitem__('policy_name','foreign'),
                lambda r:r['branches'][0]['policy_calls'][0].__setitem__('db_event_prefix_max',999)):
            value=deepcopy(raw);edit(value)
            with self.assertRaises(ValueError):audit.audit_replay(value)

    def test_parent_controls_delegate_to_frozen_fullmenu_chain(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y')
        proof=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=proof) as call:self.assertIs(audit.audit_replay(raw),proof)
        call.assert_called_once_with(raw)

    def test_import_has_no_producer_and_does_not_mutate_frozen_hooks(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_pareto_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        value=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(value.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
