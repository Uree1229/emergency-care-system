"""Independent exhaustive coverage, compact row reconstruction and mutations."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_fullmenu_audit as audit
import test_inquiry_lab_cohort_stoploss_audit as prior_tests
import test_inquiry_lab_cohort_dynamic_audit as dynamic_tests

ROOT=Path(__file__).resolve().parent


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
    parent_menu=deepcopy(old['menu_indices']);menu=[];added=[];feasible=[];scores=[];selection=None
    table=audit.parent_views(original)['joint'];calculated=[]
    if evaluated:
        prepared=audit.prepare(original,members=[audit.alias_member(m) for m in ms],
            tail_members=[audit.alias_member(m) for m in ts],joint_context=audit.alias_context(c),priority_context=p)
        menu,added,feasible=audit.full_feasible_menu(parent_menu,prepared['independent_table']['combinations'])
        for position,index in enumerate(menu):
            if position<len(parent_menu):
                row=deepcopy(old['row_scores'][position]);drow=row['base_row_score']['base_row_score']['base_row_score']['first_cohort_score']
            else:
                drow=dynamic_tests.row_output(index,position,table['combinations'][index]['hospitals'],
                    prepared['current_models'],prepared['dynamic_members'],prepared['dynamic_context'],prepared['dynamic_tails'],p)
            rebuilt,_=audit.rebuild_row(drow,index,position,prepared);calculated.append(rebuilt)
            scores.append(row if position<len(parent_menu) else rebuilt)
        selection=audit.dynamic.base.global_staged_choice(calculated,base_index)
        selection['base_selected_tied']=selection.pop('base_budget_tied');selected=selection['selected_index']
    intervened=selected!=base_index;decisions=deepcopy(original['decisions'])
    if intervened:
        chosen=next(r for r in calculated if r['row_index']==selected);decisions=deepcopy(table['baseline_proposals'])
        for i,d in enumerate(decisions):
            sends=table['combinations'][selected]['hospitals'][i]
            d.update(send=list(sends),reason='cohort_fullmenu_current_only');d['evidence']['sent_hospitals']=list(sends)
            d['evidence']['planned_expected_cost']=round(chosen['member_metrics'][i]['metrics']['total_cost_seconds'],3)
            for item in d['evidence']['candidate_plan']:item['planned_offset_seconds']=0 if item['hospital'] in sends else None
    for i,d in enumerate(decisions):
        d['evidence']['policy']=audit.NAME
        d['evidence']['cohort_fullmenu']=dict(schema_version=1,mode='pair_cohort_fullmenu',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,selected_index=selected,
            effective_priority=p['patients'][i]['effective_priority'])
    evidence=dict(schema_version=1,rule=audit.RULE,base_policy=audit.BASE_POLICY,base_source_sha256=audit.BASE_SOURCE_SHA256,
        scoring_source_sha256=deepcopy(audit.SCORING_SOURCE_SHA256),
        coverage=dict(original_joint_row_count=len(table['combinations']) if evaluated else 0,parent_menu_row_count=len(parent_menu),
            evaluated_row_count=len(scores),expanded_row_count=len(added),feasible_row_count=len(feasible)),
        identity_projection=dict(source_policy=audit.NAME,calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_stoploss__'),
        joint_context=deepcopy(c),priority_context=deepcopy(p),owner_context=deepcopy(owners),original_stoploss_proposal=deepcopy(original),
        evaluated=evaluated,parent_menu_indices=parent_menu,menu_indices=menu,added_row_indices=added,feasible_row_indices=feasible,
        row_scores=scores,selection=selection,base_selected_index=base_index,selected_index=selected,intervened=intervened,
        tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=audit.OBJECTIVE,scope=audit.SCOPE)
    return decisions,evidence


def fixture():
    events,registry,c,ms,ts,p,owners,d,e=prior_tests.fixture();original=dict(decisions=deepcopy(d),evidence=deepcopy(e))
    native(events,registry,c,ms,ts,owners);d,e=output(ms,c,ts,p,owners,original)
    return events,registry,c,ms,ts,p,owners,d,e


class ExhaustiveMenuTests(unittest.TestCase):
    def test_every_feasible_row_once_parent_prefix_then_table_order(self):
        table=[dict(feasible=i%4!=0) for i in range(225)]
        menu,added,feasible=audit.full_feasible_menu([7,2,1],table)
        self.assertEqual(menu[:3],[7,2,1]);self.assertEqual(menu[3:],[i for i in range(225) if i%4 and i not in (7,2,1)])
        self.assertEqual(set(menu),set(feasible));self.assertEqual(len(menu),len(set(menu)));self.assertEqual(added,menu[3:])
        self.assertGreater(len(menu),3)

    def test_four_hundred_rows_allowed_and_invalid_inventories_rejected(self):
        self.assertEqual(len(audit.full_feasible_menu([399], [dict(feasible=True) for _ in range(400)])[0]),400)
        for parent,table in (([],[dict(feasible=True)]),([0,0],[dict(feasible=True)]),([True],[dict(feasible=True)]),
                ([1],[dict(feasible=True)]),([0],[dict(feasible=False)]),([0],[dict(feasible=1)]),
                ([0],[dict(feasible=True) for _ in range(401)]),([0],[])):
            with self.subTest(parent=parent),self.assertRaises(ValueError):audit.full_feasible_menu(parent,table)

    def test_selector_anchors_stoploss_then_retains_original_fullmenu_order(self):
        rows=[dict(row_index=i,tier_probabilities={'4':1.,'2':0.,'1':0.},sum_capped_wait_seconds=10.) for i in (8,2,0,1,3)]
        self.assertEqual(audit.dynamic.base.global_staged_choice(rows,2)['selected_index'],2)
        rows[1]['sum_capped_wait_seconds']=11.
        self.assertEqual(audit.dynamic.base.global_staged_choice(rows,2)['selected_index'],8)
        with self.assertRaises(ValueError):audit.dynamic.base.global_staged_choice(rows,99)


class CompletePairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=fixture()

    def check(self,d=None,e=None):
        _,_,c,ms,ts,p,owners,decisions,evidence=self.data
        return audit.audit_pair(ms,c,ts,decisions if d is None else d,evidence if e is None else e,p,owners)

    def test_original_proposal_and_rows_immutable_full_coverage_independent(self):
        before=deepcopy(self.data);proof=self.check();self.assertEqual(self.data,before)
        e=self.data[-1];self.assertEqual(proof['fullmenu_rows_checked'],len(e['feasible_row_indices']))
        self.assertEqual(e['row_scores'][:len(e['parent_menu_indices'])],e['original_stoploss_proposal']['evidence']['row_scores'])
        self.assertEqual(set(e['menu_indices']),set(e['feasible_row_indices']))

    def test_missing_duplicate_reordered_or_infeasible_menu_and_coverage_rejected(self):
        edits=(lambda e:e['menu_indices'].pop(),lambda e:e['menu_indices'].append(e['menu_indices'][0]),
            lambda e:e['menu_indices'].reverse(),lambda e:e['feasible_row_indices'].append(999),
            lambda e:e['added_row_indices'].append(999),lambda e:e['parent_menu_indices'].pop(),
            lambda e:e['coverage'].__setitem__('feasible_row_count',999),lambda e:e['row_scores'].pop())
        for edit in edits:
            e=deepcopy(self.data[-1]);edit(e)
            with self.assertRaises(ValueError):self.check(e=e)

    def test_parent_prefix_final_metrics_scalar_partition_and_choice_tampering_rejected(self):
        edits=(lambda e:e['row_scores'][0]['member_metrics'].pop(),
            lambda e:e['row_scores'][0]['tier_probabilities'].__setitem__('4',99.),
            lambda e:e['row_scores'][0].__setitem__('sum_capped_wait_seconds',0.),
            lambda e:e['selection'].__setitem__('selected_index',999),
            lambda e:e['scoring_source_sha256'].__setitem__('cohort_stoploss_model','x'),
            lambda e:e['original_stoploss_proposal']['evidence'].__setitem__('selected_index',999))
        for edit in edits:
            e=deepcopy(self.data[-1]);edit(e)
            with self.assertRaises(ValueError):self.check(e=e)

    def test_current_outputs_cannot_add_future_or_hidden_contacts(self):
        for edit in (lambda d:d[0]['send'].append('HIDDEN'),lambda d:d[0]['evidence']['cohort_fullmenu'].__setitem__('selected_index',999),
                lambda d:d[0]['evidence'].__setitem__('planned_expected_cost',-1.)):
            d=deepcopy(self.data[-2]);edit(d)
            with self.assertRaises(ValueError):self.check(d=d)

    def test_rebuilt_old_scores_do_not_trust_recorded_final_float_values(self):
        old=self.data[-1]['original_stoploss_proposal'];_,_,c,ms,ts,p,_,_,_=self.data
        prepared=audit.prepare(old,members=[audit.alias_member(m) for m in ms],tail_members=[audit.alias_member(t) for t in ts],
            joint_context=audit.alias_context(c),priority_context=p)
        r=old['evidence']['row_scores'][0];drow=r['base_row_score']['base_row_score']['base_row_score']['first_cohort_score']
        rebuilt,_=audit.rebuild_row(drow,r['row_index'],0,prepared)
        self.assertEqual(rebuilt['member_metrics'],r['member_metrics'])

    def test_dynamic_projection_provenance_and_future_input_tampering_rejected(self):
        old=self.data[-1]['original_stoploss_proposal'];_,_,c,ms,ts,p,_,_,_=self.data
        prepared=audit.prepare(old,members=[audit.alias_member(m) for m in ms],tail_members=[audit.alias_member(t) for t in ts],
            joint_context=audit.alias_context(c),priority_context=p)
        r=old['evidence']['row_scores'][0];source=r['base_row_score']['base_row_score']['base_row_score']['first_cohort_score']
        edits=(lambda d:d['projection']['final_pending'].pop(),
            lambda d:d['projection']['steps'][0]['members'][0]['inputs'].__setitem__('future_service_draw',1.),
            lambda d:d['projection']['steps'][0]['proposal']['decisions'][0]['send'].append('HIDDEN'),
            lambda d:d['projection']['initial_appended_inquiries'][0]['inquiry'].__setitem__('submitted_at',999999),
            lambda d:d['projection']['steps'][0]['appended_inquiries'][0]['source'].__setitem__('proposal_path','wrong'))
        for edit in edits:
            value=deepcopy(source);edit(value)
            with self.assertRaises(ValueError):audit.rebuild_row(value,r['row_index'],0,prepared)


class DelegationAndImportTests(unittest.TestCase):
    def test_old_controls_delegate_to_frozen_stoploss_chain(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y')
        proof=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=proof) as call:self.assertIs(audit.audit_replay(raw),proof)
        call.assert_called_once_with(raw)

    def test_import_has_no_producer_and_does_not_mutate_frozen_hooks(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_fullmenu_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        value=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(value.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
