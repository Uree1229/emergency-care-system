"""Independent all-owner tail-wait law, full parent proofs and domain audit."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import math
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_tailwait_audit as audit
import test_inquiry_lab_cohort_pairshield_audit as previous

ROOT=Path(__file__).resolve().parents[2]


def inventory(ids=('CURRENT','TAIL','QUERIED')):
    return [dict(patient=dict(id=pid,effective_priority=tier)) for pid,tier in zip(ids,(4,2,1))]


def row(index,waits=(10.,20.,30.),probs=(.5,.5,.5),ids=('CURRENT','TAIL','QUERIED')):
    members=[dict(patient_id=pid,effective_priority=tier,metrics=dict(selection_probability=p,capped_wait_seconds=w,total_cost_seconds=100.))
        for pid,tier,w,p in zip(ids,(4,2,1),waits,probs)]
    return dict(row_index=index,member_metrics=members,tier_probabilities={str(t):p for t,p in zip((4,2,1),probs)},sum_capped_wait_seconds=sum(waits,0.))


def kwargs(rows,ids=('CURRENT','TAIL','QUERIED')):
    return dict(baseline_index=7,owner_inventory=inventory(ids),menu_indices=[r['row_index'] for r in rows],feasible_row_indices=sorted(r['row_index'] for r in rows))


def guard(rows,ids=('CURRENT','TAIL','QUERIED')):return audit.guard_rows(rows,**kwargs(rows,ids))


def select(rows,probability=None,waits=None):
    arguments=kwargs(rows)
    if probability is None:probability=audit.wait_selector.foundation.guard_rows(rows,**arguments)['admissible_row_indices']
    if waits is None:waits=guard(rows)['admissible_row_indices']
    return audit.select_wait_guarded(rows,**arguments,probability_admissible_row_indices=probability,wait_admissible_row_indices=waits)


def convert(data):
    from inquiry_lab import cohort_tailwait_policy as producer
    events,registry,j,ms,ts,p,owners,d,e=data;parent=dict(decisions=d,evidence=e)
    for eligible in j['eligible']:eligible['job']['policy_name']=audit.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=audit.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==audit.BASE_POLICY:owner['job']['policy_name']=audit.NAME
    with patch.object(producer.original,'decide_joint',return_value=deepcopy(parent)):
        result=producer.decide_joint(audit.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)
    return events,registry,j,ms,ts,p,owners,result['decisions'],result['evidence']


@lru_cache(maxsize=3)
def _fixture(kind):
    if kind=='active':return convert(previous.inherited_fixture())
    if kind=='new':return convert(previous.fixture())
    if kind=='inactive':return convert(previous.fixture(count=3,extras=1))
    raise ValueError(kind)


def fixture(kind='active'):return deepcopy(_fixture(kind))


def check(data):
    _,_,j,ms,ts,p,owners,d,e=data
    return audit.audit_pair(ms,j,ts,d,e,p,owners)


class TailWaitIndependentAuditTests(unittest.TestCase):
    def test_total_improvement_cannot_hide_tail_or_queried_owner_wait_increase(self):
        rows=[row(7),row(2,(0.,21.,0.)),row(3,(0.,0.,31.)),row(4,(9.,20.,30.))]
        proof=guard(rows);self.assertEqual(proof['admissible_row_indices'],[7,4])
        self.assertEqual(proof['row_checks'][1]['owner_regressions'][0]['patient_id'],'TAIL')
        self.assertEqual(proof['row_checks'][2]['owner_regressions'][0]['patient_id'],'QUERIED')
        self.assertTrue(proof['baseline_always_admissible'])

    def test_exact_existing_wait_epsilon_and_nextafter_boundary(self):
        maximum=20.+1e-9
        for after,allowed in ((maximum,True),(math.nextafter(maximum,math.inf),False),(math.nextafter(maximum,-math.inf),True)):
            with self.subTest(after=after):
                proof=guard([row(7),row(2,(10.,after,30.))]);self.assertEqual(proof['row_checks'][1]['admissible'],allowed)
                self.assertEqual(proof['wait_epsilon'],1e-9)

    def test_complete_typed_all_owner_partition_and_priorities_bound(self):
        self.assertEqual(guard([row(7,ids=(1,'1','R'))],ids=(1,'1','R'))['compared_owner_count'],3)
        for edit in (lambda r:r[1]['member_metrics'].pop(),lambda r:r[1]['member_metrics'][0].__setitem__('patient_id',True),
                lambda r:r[1]['member_metrics'][1].__setitem__('patient_id','CURRENT'),
                lambda r:r[1]['member_metrics'][2].__setitem__('patient_id','FOREIGN'),
                lambda r:r[1]['member_metrics'][1].__setitem__('effective_priority',4)):
            rows=[row(7),row(2)];edit(rows)
            with self.assertRaises(ValueError):guard(rows)

    def test_all_rows_order_feasibility_anchor_and_duplicates_bound(self):
        for mode in ('omitted','order','duplicate','anchor','typed'):
            rows=[row(7),row(2)];arguments=kwargs(rows)
            if mode=='omitted':arguments['feasible_row_indices']=[7]
            elif mode=='order':arguments['menu_indices']=[2,7]
            elif mode=='duplicate':rows[1]['row_index']=7
            elif mode=='anchor':arguments['baseline_index']=999
            else:rows[1]['row_index']=True
            with self.subTest(mode=mode),self.assertRaises(ValueError):audit.guard_rows(rows,**arguments)

    def test_finite_waits_and_inventory_required_without_new_horizon_or_threshold(self):
        for value in (-1.,float('nan'),float('inf'),True,None):
            rows=[row(7),row(2)];rows[1]['member_metrics'][0]['metrics']['capped_wait_seconds']=value
            with self.subTest(value=value),self.assertRaises(ValueError):guard(rows)
        rows=[row(7)];arguments=kwargs(rows);arguments['owner_inventory'].append(deepcopy(arguments['owner_inventory'][0]))
        with self.assertRaises(ValueError):audit.guard_rows(rows,**arguments)
        arguments=kwargs(rows);arguments['owner_inventory'][0]['patient']['effective_priority']=True
        with self.assertRaises(ValueError):audit.guard_rows(rows,**arguments)
        self.assertEqual(guard(rows)['admissible_row_indices'],[7])

    def test_entire_original_probability_domain_checked_before_explicit_wait_projection(self):
        rows=[row(7),row(2,(0.,21.,0.)),row(3,(0.,0.,0.),(.4,.5,.5)),row(4,(9.,20.,30.))]
        proof=select(rows)
        self.assertEqual(proof['probability_admissible_row_indices'],[7,2,4]);self.assertEqual(proof['wait_admissible_row_indices'],[7,3,4])
        self.assertEqual(proof['projected_domain_row_indices'],[7,4]);self.assertEqual(proof['selected_index'],4)
        self.assertEqual(proof['original_probability_domain_selection']['selected_index'],2)
        self.assertFalse(proof['original_JOINT_rows_reclassified']);self.assertEqual(proof['original_menu_indices'],[7,2,3,4])
        for probability,waits in (([7,2,3,4],None),(None,[7]),(None,[True,3,4])):
            with self.assertRaises(ValueError):select(rows,probability,waits)

    def test_independent_law_and_selector_agree_with_synthetic_producer(self):
        from inquiry_lab import cohort_tailwait_model as producer
        rows=[row(7),row(2,(0.,21.,0.)),row(4,(9.,20.,30.))];arguments=kwargs(rows)
        self.assertEqual(audit.guard_rows(rows,**arguments),producer.guard_rows(rows,**arguments))
        arguments.update(probability_admissible_row_indices=[7,2,4],wait_admissible_row_indices=[7,4])
        self.assertEqual(audit.select_wait_guarded(rows,**arguments),producer.select_wait_guarded(rows,**arguments))

    def test_full_intact_active_parent_proof_all_tail_and_residual_owners(self):
        data=fixture();before=deepcopy(data);counts=check(data);self.assertEqual(data,before);e=data[-1]
        self.assertTrue(e['tailwait_evaluated']);self.assertEqual(counts['tailwait_groups_guarded'],1)
        self.assertEqual(counts['tailwait_rows_checked'],3);self.assertEqual(counts['tailwait_owner_comparisons_checked'],15)
        self.assertEqual(counts['tailwait_baseline_checks'],1);self.assertEqual(counts['tailwait_owner_wait_vetoes'],4)
        self.assertEqual(e['protected_tailwait_selection']['original_probability_domain_selection'],e['original_pairshield_proposal']['evidence']['original_pairscope_proposal']['evidence']['original_envelope_proposal']['evidence']['protected_wait_selection'])

    def test_new_no_tail_and_inactive_tail_parent_paths_copy_exact_actions(self):
        for kind in ('new','inactive'):
            data=fixture(kind)
            with patch.object(audit,'guard_rows') as guard_call:counts=check(data)
            guard_call.assert_not_called();self.assertEqual(counts['tailwait_groups_inherited'],1)
            self.assertEqual(counts['tailwait_rows_checked'],0);self.assertIsNone(data[-1]['owner_wait_guard'])
            for actual,parent in zip(data[-2],data[-1]['original_pairshield_proposal']['decisions']):
                actual=deepcopy(actual);actual['evidence'].pop('cohort_tailwait');actual['evidence']['policy']=audit.BASE_POLICY
                self.assertEqual(actual,parent)

    def test_activation_guard_domain_source_and_current_output_mutations_rejected(self):
        for edit in (lambda e:e.__setitem__('tailwait_evaluated',False),
                lambda e:e['owner_wait_guard']['row_checks'][0].__setitem__('admissible',not e['owner_wait_guard']['row_checks'][0]['admissible']),
                lambda e:e['owner_wait_guard']['baseline_owner_waits'].pop(),
                lambda e:e['protected_tailwait_selection']['projected_domain_row_indices'].clear(),
                lambda e:e['protected_tailwait_selection']['original_probability_domain_selection'].__setitem__('selected_index',999),
                lambda e:e['guard_source_sha256'].__setitem__('cohort_pairshield_policy','x')):
            data=fixture();edit(data[-1])
            with self.assertRaises(ValueError):check(data)
        data=fixture();data[-2][0]['send']=['FOREIGN']
        with self.assertRaises(ValueError):check(data)

    def test_parent_score_F0_complete_inventory_clock_and_FIFO_unchanged(self):
        for edit in (lambda e:e['original_pairscope_proposal']['evidence']['original_envelope_proposal']['evidence']['new_row_scores'][0]['member_metrics'][0]['metrics'].__setitem__('capped_wait_seconds',0),
                lambda e:e['original_pairscope_proposal']['evidence']['original_envelope_proposal']['evidence']['new_row_scores'].pop(),
                lambda e:e['original_pairscope_proposal']['evidence'].__setitem__('inherited_evaluated',False)):
            data=fixture();edit(data[-1]['original_pairshield_proposal']['evidence'])
            with self.assertRaises(ValueError):check(data)
        for edit in (lambda o:o['inventory'].pop(),lambda o:o['inventory'][0]['patient'].__setitem__('version',999)):
            data=fixture();edit(data[6])
            with self.assertRaises(ValueError):check(data)

    def test_changed_output_orchestration_explicitly_not_a_full_parent_proof(self):
        from inquiry_lab import cohort_tailwait_policy as producer
        data=fixture();_,_,j,ms,ts,p,owners,_,e=data;old=e['original_pairshield_proposal'];ps=old['evidence']['original_pairscope_proposal']['evidence'];env=ps['original_envelope_proposal']['evidence']
        rows=env['new_row_scores'];anchor=env['stoploss_anchor_index'];target=next(r['row_index'] for r in rows if r['row_index']!=anchor)
        for source in rows:
            for i,m in enumerate(source['member_metrics']):
                value=100. if source['row_index']==anchor else (50. if i==0 else 101. if i==2 else 100.) if source['row_index']==target else 200.
                m['metrics'].update(selection_probability=.5,capped_wait_seconds=value,total_cost_seconds=123.4567)
            source['tier_probabilities']={str(t):sum(m['metrics']['selection_probability'] for m in source['member_metrics'] if m['effective_priority']==t) for t in (4,2,1)}
            source['sum_capped_wait_seconds']=sum(m['metrics']['capped_wait_seconds'] for m in source['member_metrics'])
        full=env['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence'];arguments=dict(baseline_index=anchor,owner_inventory=owners['inventory'],menu_indices=full['menu_indices'],feasible_row_indices=full['feasible_row_indices'])
        env['owner_probability_guard']=producer.model.selector.parent_guard.guard_rows(rows,**arguments)
        env['protected_wait_selection']=producer.model.selector.select_protected_wait(rows,**arguments,admissible_row_indices=env['owner_probability_guard']['admissible_row_indices'])
        env['selected_index']=old['evidence']['selected_index']=target
        with patch.object(producer.original,'decide_joint',return_value=deepcopy(old)):
            result=producer.decide_joint(audit.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)
        self.assertTrue(result['evidence']['intervened']);self.assertEqual(result['evidence']['selected_index'],anchor)
        with self.assertRaises(ValueError):audit.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,owners)
        with patch.object(audit.foundation,'audit_pair',return_value=dict(joint_groups_applied=1)):
            counts=audit.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,owners)
        self.assertEqual(counts['tailwait_interventions'],1);self.assertTrue(all(d['reason']=='cohort_tailwait_current_only' for d in result['decisions']))
        self.assertTrue(all(d['evidence']['planned_expected_cost']==123.457 for d in result['decisions']))

    def test_baseline_only_and_legacy_empty_offer_parent_rejection_retained(self):
        parent=previous.from_parent_data(previous.previous.new_fixture(extras=0,hospitals=1,statuses=False));counts=check(convert(parent))
        self.assertEqual(counts['tailwait_groups_inherited'],1)
        empty=previous.from_parent_data(previous.previous.new_fixture(extras=0,hospitals=0,statuses=False))
        with self.assertRaisesRegex(ValueError,'baseline_combination.*selection_probability'):check(convert(empty))

    def test_standard_singleton_fallback_all_parent_tags_and_FIFO_retained(self):
        raw=previous.previous.prior_tests.prior_tests.prior_tests.horizon_tests.replay();branch=raw['branches'][0];branch['joint_groups']=[]
        for event in branch['db_events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            for candidate in call['inputs']['candidates']:candidate['resources_enough']=True
            e=call['output']['evidence'];e['policy']=audit.NAME
            e['joint_tail']=dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False)
            e['joint_first']=dict(schema_version=1,mode='horizon_fallback',current_only=False)
            e['joint_budget']=dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False)
            for name in ('cohort_success','cohort_dynamic','cohort_hedge','cohort_recourse','cohort_followplan','cohort_stoploss',
                    'cohort_fullmenu','cohort_pareto','cohort_waitpareto','cohort_envelope','cohort_pairscope','cohort_pairshield','cohort_tailwait'):
                e[name]=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
            decision['evidence']=deepcopy(e)
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        proof=audit.audit_replay(raw);self.assertTrue(proof['passed']);self.assertTrue(proof['branch_audits'][0]['inherited_active_tail_owner_wait_guard_independently_checked'])
        bad=deepcopy(raw);bad['branches'][0]['policy_calls'][0]['output']['evidence']['cohort_pairshield']['evaluated']=True
        with self.assertRaises(ValueError):audit.audit_replay(bad)

    def test_old_controls_delegate_and_no_producer_is_imported(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y')
        expected=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=expected) as call:self.assertIs(audit.audit_replay(raw),expected)
        call.assert_called_once_with(raw)
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_tailwait_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
