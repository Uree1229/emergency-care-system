"""Independent synthetic membership boundary, parent proof and projection audit."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import math
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_pairshield_audit as audit
from test_inquiry_lab_cohort_pairshield_model import item,people,row,inventory
from test_inquiry_lab_cohort_pairshield_policy import fixture as producer_fixture,invoke,guarded_choice_fixture
import test_inquiry_lab_cohort_pairscope_audit as previous

ROOT=Path(__file__).resolve().parent


def guard(rows,ids=('A','B'),anchor=7):
    indices=[r['row_index'] for r in rows]
    return audit.guard_rows(rows,baseline_index=anchor,current_pair_patient_ids=list(ids),
        menu_indices=indices,feasible_row_indices=sorted(indices))


def select(rows,*,admitted=None,support=None,anchor=7):
    indices=[r['row_index'] for r in rows]
    if admitted is None:
        admitted=audit.wait_selector.foundation.guard_rows(rows,baseline_index=anchor,owner_inventory=inventory(),
            menu_indices=indices,feasible_row_indices=sorted(indices))['admissible_row_indices']
    if support is None:support=guard(rows,anchor=anchor)['admissible_row_indices']
    return audit.select_supported_wait(rows,baseline_index=anchor,owner_inventory=inventory(),menu_indices=indices,
        feasible_row_indices=sorted(indices),probability_admissible_row_indices=admitted,support_admissible_row_indices=support)


@lru_cache(maxsize=4)
def _fixture(count=2,extras=4):
    args,_=producer_fixture(count=count,extras=extras);result=invoke(args)
    j,ms,ts,p,owners,_=args
    return None,None,j,ms,ts,p,owners,result['decisions'],result['evidence']


def fixture(**kwargs):return deepcopy(_fixture(**kwargs))


def from_parent_data(data):
    events,registry,j,ms,ts,p,owners,d,e=data;parent=dict(decisions=d,evidence=e)
    for eligible in j['eligible']:eligible['job']['policy_name']=audit.NAME
    for member in ms+ts:member['workflow_snapshot']['job']['policy_name']=audit.NAME
    for owner in owners['inventory']:
        if owner['job'] is not None and owner['job']['policy_name']==audit.BASE_POLICY:owner['job']['policy_name']=audit.NAME
    args=(j,ms,ts,p,owners,parent);result=invoke(args)
    return events,registry,j,ms,ts,p,owners,result['decisions'],result['evidence']


def inherited_fixture():return from_parent_data(previous.inherited_fixture())


def check(data):
    _,_,j,ms,ts,p,owners,d,e=data
    return audit.audit_pair(ms,j,ts,d,e,p,owners)


class PairShieldIndependentAuditTests(unittest.TestCase):
    def test_removed_and_added_boundary_memberships_are_separate_vetoes(self):
        changed=people();changed[0]['fixed_initial_items']=[item('NEW')]
        changed[1]['fixed_initial_items']=[item('BLOCKED',10.,[0.,0.])]
        source=[row(7),row(2,changed)];before=deepcopy(source);proof=guard(source)
        owner0,owner1=proof['row_checks'][1]['owner_checks']
        self.assertEqual(owner0['removed_baseline_boundary_hospitals'],['X0'])
        self.assertEqual(owner1['added_candidate_boundary_hospitals'],['BLOCKED'])
        self.assertEqual(proof['admissible_row_indices'],[7]);self.assertEqual(source,before)

    def test_shared_hospital_boundary_crossing_is_free_in_both_directions(self):
        changed=people();changed[0]['fixed_initial_items']=[item('X0',0.,[.1,.1])]
        changed[1]['fixed_initial_items']=[item('X1',10.,[0.,0.])]
        proof=guard([row(7),row(2,changed)])
        self.assertEqual(proof['admissible_row_indices'],[7,2])
        self.assertTrue(all(o['shared_boundary_status_changes'] for o in proof['row_checks'][1]['owner_checks']))

    def test_exact_H_zero_mass_and_queued_mode_all_required(self):
        for wait,masses,mode,blocked in ((10.,[0.,0.],'queued',True),
                (math.nextafter(10.,-math.inf),[0.,0.],'queued',False),
                (math.nextafter(10.,math.inf),[0.,0.],'queued',True),
                (10.,[0.,math.nextafter(0.,1.)],'queued',False),(10.,[0.,0.],'residual',False)):
            with self.subTest(wait=wait,masses=masses,mode=mode):
                baseline=people();baseline[0]['fixed_initial_items']=[item('X0',wait,masses,mode)]
                change=deepcopy(baseline);change[0]['fixed_initial_items']=[item('NEW')]
                proof=guard([row(7,baseline),row(2,change)])
                self.assertEqual(proof['row_checks'][1]['admissible'],not blocked)
                self.assertTrue(proof['baseline_always_admissible'])

    def test_typed_ids_and_all_source_owner_partitions_are_bound(self):
        proof=guard([row(7,ids=(1,'1'))],ids=(1,'1'));self.assertEqual(proof['current_owner_count'],2)
        for edit in (lambda r:r[0]['first_cohort_envelope']['patient_scores'].pop(),
                lambda r:r[0]['first_cohort_envelope']['patient_scores'][0].__setitem__('patient_id',True),
                lambda r:r[0]['first_cohort_envelope']['patient_scores'][1].__setitem__('patient_id','A'),
                lambda r:r.append(deepcopy(r[0])),lambda r:r[0].__setitem__('row_index',True)):
            rows=[row(7)];edit(rows)
            with self.assertRaises(ValueError):guard(rows)

    def test_grid_cardinality_mass_and_unique_membership_tampering_rejected(self):
        for edit in (lambda p:p.__setitem__('cell_edges_seconds',[5,11]),
                lambda p:p.__setitem__('cell_edges_seconds',[5,5]),
                lambda p:p['fixed_initial_items'].append(item('EXTRA')),
                lambda p:p['fixed_initial_items'].append(deepcopy(p['fixed_initial_items'][0])),
                lambda p:p['fixed_initial_items'][0].__setitem__('masses',[0.]),
                lambda p:p['fixed_initial_items'][0].__setitem__('wait_seconds',float('nan')),
                lambda p:p['fixed_initial_items'][0].__setitem__('masses',[0.,True])):
            rows=[row(7),row(2)];edit(rows[1]['first_cohort_envelope']['patient_scores'][0])
            with self.assertRaises(ValueError):guard(rows)

    def test_complete_original_P_admission_precedes_explicit_intersection(self):
        changed=people();changed[1]['fixed_initial_items']=[item('NEW',10.,[0.,0.])]
        rows=[row(7),row(2,changed,wait=30.),row(3,wait=20.,probabilities=(.4,.5,.5)),row(4,wait=90.)]
        proof=select(rows)
        self.assertEqual(proof['probability_admissible_row_indices'],[7,2,4])
        self.assertEqual(proof['support_admissible_row_indices'],[7,3,4])
        self.assertEqual(proof['projected_domain_row_indices'],[7,4])
        self.assertEqual(proof['original_probability_domain_selection']['selected_index'],2)
        self.assertEqual(proof['selected_index'],4)
        self.assertEqual(proof['original_menu_indices'],[7,2,3,4])
        self.assertFalse(proof['original_JOINT_rows_reclassified'])
        for admitted,support in (([7,2,3,4],None),(None,[7]),(None,[True,3,4])):
            with self.assertRaises(ValueError):select(rows,admitted=admitted,support=support)

    def test_independent_guard_and_projection_agree_with_synthetic_producer(self):
        from inquiry_lab import cohort_pairshield_model as producer
        changed=people();changed[0]['fixed_initial_items']=[item('NEW')]
        rows=[row(7),row(2,changed,wait=30.),row(4,wait=90.)];indices=[7,2,4]
        kwargs=dict(baseline_index=7,current_pair_patient_ids=['A','B'],menu_indices=indices,feasible_row_indices=sorted(indices))
        self.assertEqual(audit.guard_rows(rows,**kwargs),producer.guard_rows(rows,**kwargs))
        kwargs.pop('current_pair_patient_ids');kwargs.update(owner_inventory=inventory(),probability_admissible_row_indices=indices,support_admissible_row_indices=[7,4])
        self.assertEqual(audit.select_supported_wait(rows,**kwargs),producer.select_supported_wait(rows,**kwargs))

    def test_complete_new_parent_proof_rows_sources_and_all_owner_guard(self):
        data=fixture();before=deepcopy(data);counts=check(data);self.assertEqual(data,before)
        e=data[-1];self.assertEqual(counts['pairshield_groups_guarded'],1)
        self.assertEqual(counts['pairshield_rows_checked'],len(e['original_pairscope_proposal']['evidence']['new_row_scores']))
        self.assertEqual(counts['pairshield_initial_sources_checked'],2*counts['pairshield_rows_checked'])
        self.assertEqual(counts['pairshield_baseline_checks'],1)
        self.assertEqual(counts['pairscope_residual_origins_checked'],4)
        self.assertEqual(e['protected_support_selection']['original_probability_domain_selection'],e['original_pairscope_proposal']['evidence']['protected_wait_selection'])

    def test_complete_baseline_only_domain_and_legacy_empty_offer_boundary(self):
        data=from_parent_data(previous.new_fixture(extras=0,hospitals=1,statuses=False));counts=check(data)
        self.assertEqual(counts['pairshield_rows_checked'],1);self.assertEqual(counts['pairshield_baseline_checks'],1)
        self.assertEqual(data[-1]['protected_support_selection']['projected_domain_row_indices'],[data[-1]['stoploss_anchor_index']])
        empty=from_parent_data(previous.new_fixture(extras=0,hospitals=0,statuses=False))
        self.assertTrue(all(d['send']==[] for d in empty[-2]))
        with self.assertRaisesRegex(ValueError,'baseline_combination.*selection_probability'):check(empty)

    def test_inherited_active_and_one_tail_actions_are_exact_parent_copies(self):
        for data in (inherited_fixture(),fixture(count=3,extras=1)):
            with patch.object(audit,'guard_rows') as guard_call:counts=check(data)
            guard_call.assert_not_called();self.assertEqual(counts['pairshield_groups_inherited'],1)
            self.assertEqual(counts['pairshield_rows_checked'],0);self.assertEqual(counts['pairshield_baseline_checks'],0)
            for output,old in zip(data[-2],data[-1]['original_pairscope_proposal']['decisions']):
                output=deepcopy(output);output['evidence'].pop('cohort_pairshield');output['evidence']['policy']=audit.BASE_POLICY
                self.assertEqual(output,old)

    def test_guard_projection_scope_and_current_outputs_tampering_rejected(self):
        for edit in (lambda e:e.__setitem__('shield_evaluated',False),
                lambda e:e['mean_queue_support_guard']['row_checks'][0].__setitem__('admissible',False),
                lambda e:e['protected_support_selection']['projected_domain_row_indices'].clear(),
                lambda e:e['protected_support_selection']['original_probability_domain_selection'].__setitem__('selected_index',999),
                lambda e:e['scoring_source_sha256'].__setitem__('cohort_pairscope_policy','x')):
            data=fixture();edit(data[-1])
            with self.assertRaises(ValueError):check(data)
        data=fixture();data[-2][0]['send']=['FOREIGN']
        with self.assertRaises(ValueError):check(data)

    def test_original_frozen_F0_H_clock_and_residual_origin_tampering_rejected(self):
        for edit in (lambda e:e['new_row_scores'][0]['first_cohort_envelope']['patient_scores'][0]['cell_edges_seconds'].__setitem__(-1,999),
                lambda e:e['new_row_scores'][0]['first_cohort_envelope']['patient_scores'][0]['fixed_initial_items'][0].__setitem__('wait_seconds',999),
                lambda e:e['new_owner_origins'].pop(),lambda e:e['owner_probability_guard']['admissible_row_indices'].clear()):
            data=fixture();edit(data[-1]['original_pairscope_proposal']['evidence'])
            with self.assertRaises(ValueError):check(data)

    def test_changed_output_orchestration_only_and_full_parent_rejects_mock_scores(self):
        # This deliberate score mock tests only the new current-output layer.
        # It is not a complete independently certified parent proof.
        args,anchor,_=guarded_choice_fixture();result=invoke(args);j,ms,ts,p,owners,_=args
        self.assertTrue(result['evidence']['intervened'])
        with self.assertRaises(ValueError):audit.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,owners)
        with patch.object(audit.foundation,'audit_pair',return_value=dict(joint_groups_applied=1)):
            counts=audit.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,owners)
        self.assertEqual(counts['pairshield_interventions'],1);self.assertGreater(counts['pairshield_removed_zero_vetoes'],0)
        self.assertEqual(result['evidence']['selected_index'],anchor)

    def test_standard_fallback_all_parent_tags_and_caps_retained(self):
        raw=previous.prior_tests.prior_tests.prior_tests.horizon_tests.replay();branch=raw['branches'][0];branch['joint_groups']=[]
        for event in branch['db_events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            for candidate in call['inputs']['candidates']:candidate['resources_enough']=True
            e=call['output']['evidence'];e['policy']=audit.NAME
            e['joint_tail']=dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False)
            e['joint_first']=dict(schema_version=1,mode='horizon_fallback',current_only=False)
            e['joint_budget']=dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False)
            for name in ('cohort_success','cohort_dynamic','cohort_hedge','cohort_recourse','cohort_followplan',
                    'cohort_stoploss','cohort_fullmenu','cohort_pareto','cohort_waitpareto','cohort_envelope','cohort_pairscope','cohort_pairshield'):
                e[name]=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
            decision['evidence']=deepcopy(e)
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        proof=audit.audit_replay(raw);self.assertTrue(proof['passed'])
        self.assertTrue(proof['branch_audits'][0]['first_contact_zero_mass_membership_guard_independently_checked'])
        bad=deepcopy(raw);bad['branches'][0]['policy_calls'][0]['output']['evidence']['cohort_pairscope']['evaluated']=True
        with self.assertRaises(ValueError):audit.audit_replay(bad)

    def test_old_controls_delegate_and_import_has_no_producer(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y')
        expected=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=expected) as call:self.assertIs(audit.audit_replay(raw),expected)
        call.assert_called_once_with(raw)
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_pairshield_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
