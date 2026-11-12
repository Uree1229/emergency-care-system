"""Independent exact-pair activation, fresh residual origins and full score audit."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_pairscope_audit as audit
import test_inquiry_lab_cohort_envelope_audit as prior_tests

ROOT=Path(__file__).resolve().parents[2]


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


@lru_cache(maxsize=1)
def _source_inherited_fixture():
    from inquiry_lab import cohort_pairscope_policy as producer
    events,registry,c,ms,ts,p,owners,d,e=prior_tests.fixture();original=dict(decisions=deepcopy(d),evidence=deepcopy(e))
    native(events,registry,c,ms,ts,owners)
    with patch.object(producer.original,'decide_joint',return_value=deepcopy(original)):
        result=producer.decide_joint(producer.NAME,members=ms,joint_context=c,tail_members=ts,priority_context=p,owner_context=owners)
    return events,registry,c,ms,ts,p,owners,result['decisions'],result['evidence']


def inherited_fixture():return deepcopy(_source_inherited_fixture())


def check(data):
    events,registry,c,ms,ts,p,owners,d,e=data
    return audit.audit_pair(ms,c,ts,d,e,p,owners)


@lru_cache(maxsize=12)
def _source_new_fixture(count=2,extras=4,hospitals=3,statuses=True):
    from test_inquiry_lab_cohort_pairscope_policy import fixture as producer_fixture,invoke
    args,_=producer_fixture(count=count,extras=extras,hospitals=hospitals,statuses=statuses)
    j,ms,ts,p,owners,parent=args;result=invoke(args)
    return None,None,j,ms,ts,p,owners,result['decisions'],result['evidence']


def new_fixture(**kwargs):return deepcopy(_source_new_fixture(**kwargs))


class PairScopeAuditTests(unittest.TestCase):
    def test_inherited_active_path_copies_parent_without_new_scores_or_origins(self):
        data=inherited_fixture();before=deepcopy(data);counts=check(data)
        self.assertEqual(data,before);self.assertEqual(counts['pairscope_groups_inherited'],1)
        self.assertEqual(counts['pairscope_rows_checked'],0);self.assertEqual(counts['pairscope_residual_origins_checked'],0)
        e=data[-1];self.assertEqual(e['new_row_scores'],[]);self.assertEqual(e['new_owner_origins'],[])
        self.assertEqual(e['owner_probability_guard'],e['original_envelope_proposal']['evidence']['owner_probability_guard'])
        for actual,parent in zip(data[-2],e['original_envelope_proposal']['decisions']):
            actual=deepcopy(actual);actual['evidence'].pop('cohort_pairscope');actual['evidence']['policy']=audit.BASE_POLICY
            self.assertEqual(actual,parent)

    def test_inherited_activation_and_parent_action_cannot_be_mutated(self):
        for key in ('evaluated','inherited_evaluated','newly_activated'):
            data=inherited_fixture();data[-1][key]=not data[-1][key]
            with self.subTest(key=key),self.assertRaises(ValueError):check(data)
        data=inherited_fixture();data[-1]['new_row_scores']=[{}]
        with self.assertRaises(ValueError):check(data)
        data=inherited_fixture();data[-2][0]['send']=['FOREIGN']
        with self.assertRaises(ValueError):check(data)

    def test_exact_pair_with_queried_disabled_missing_foreign_owners_all_frozen(self):
        data=new_fixture();before=deepcopy(data);counts=check(data);self.assertEqual(data,before)
        e=data[-1];self.assertFalse(e['original_envelope_proposal']['evidence']['evaluated'])
        self.assertTrue(e['newly_activated']);self.assertEqual(counts['pairscope_groups_newly_evaluated'],1)
        self.assertEqual(counts['pairscope_residual_origins_checked'],4);self.assertEqual(counts['pairscope_anchor_checked'],1)
        self.assertEqual([o['patient_id'] for o in e['new_owner_origins']],['OWNER0','OWNER1','OWNER2','OWNER3'])
        self.assertEqual(e['new_owner_origins'][0]['original_all_own'][0]['patient'],'OWNER0')
        self.assertEqual(e['new_coverage']['captured_owner_count'],6);self.assertEqual(e['new_coverage']['first_contact_count'],2)
        wanted={o['patient']['id'] for o in data[6]['inventory']}
        for row in e['new_row_scores']:
            self.assertEqual({r['patient_id'] for r in row['member_metrics']},wanted)
            self.assertEqual(row['dynamic_row_projection']['projection']['steps'],[])
            self.assertIsNone(row['dynamic_row_projection']['projection']['singleton'])
        self.assertEqual(counts.get('fullmenu_rows_checked',0),0,'new rows must not masquerade as old parent rows')
        self.assertEqual(counts['pairscope_score_owner_comparisons_checked'],len(e['new_row_scores'])*6)

    def test_pair_without_residuals_keeps_original_table_anchor_and_full_menu(self):
        data=new_fixture(extras=0,hospitals=5,statuses=False);e=data[-1];counts=check(data)
        self.assertGreater(len(e['new_menu_indices']),1)
        self.assertEqual(e['new_menu_indices'][0],e['stoploss_anchor_index'])
        self.assertEqual(set(e['new_menu_indices']),set(e['new_feasible_row_indices']))
        self.assertEqual(e['new_owner_origins'],[]);self.assertEqual(counts['pairscope_residual_origins_checked'],0)
        for row in e['new_row_scores']:self.assertEqual(len(row['member_metrics']),2)

    def test_one_tail_stays_inactive_and_cannot_invent_new_scores(self):
        data=new_fixture(count=3,extras=1,statuses=False);e=data[-1]
        with patch.object(audit,'prepare') as prepare:counts=check(data)
        prepare.assert_not_called();self.assertFalse(e['evaluated']);self.assertEqual(e['activation_reason'],'inherited_inactive_with_tail')
        self.assertEqual(counts['pairscope_groups_still_unevaluated'],1);self.assertEqual(counts['pairscope_rows_checked'],0)
        for key in ('newly_activated','evaluated'):
            changed=deepcopy(data);changed[-1][key]=True
            with self.subTest(key=key),self.assertRaises(ValueError):check(changed)
        changed=deepcopy(data);changed[-1]['new_owner_origins']=[{}]
        with self.assertRaises(ValueError):check(changed)

    def test_baseline_only_original_table_remains_valid(self):
        data=new_fixture(extras=0,hospitals=1,statuses=False);counts=check(data);e=data[-1]
        self.assertEqual(len(e['new_menu_indices']),1);self.assertEqual(e['new_menu_indices'],[e['stoploss_anchor_index']])
        self.assertEqual(e['selected_index'],e['stoploss_anchor_index']);self.assertEqual(counts['pairscope_anchor_checked'],1)

    def test_empty_offer_producer_path_does_not_waive_legacy_parent_numeric_type_rejection(self):
        # Empty offers are valid for the producer. The immutable original
        # JOINT auditor expects int0 where that producer emits float0.0.
        # Preserve and expose this legacy boundary; do not normalize evidence,
        # patch an ancestor comparison, or falsely certify a full empty replay.
        data=new_fixture(extras=0,hospitals=0,statuses=False);e=data[-1]
        self.assertEqual(e['new_menu_indices'],[e['stoploss_anchor_index']])
        self.assertTrue(all(d['send']==[] for d in data[-2]))
        with self.assertRaisesRegex(ValueError,'baseline_combination.*selection_probability'):check(data)

    def test_origin_omission_clock_source_and_foreign_ID_tampering_rejected(self):
        edits=(lambda e:e['new_owner_origins'].pop(),
            lambda e:e['new_owner_origins'][0].__setitem__('now',e['new_owner_origins'][0]['now']+1),
            lambda e:e['new_owner_origins'][0].__setitem__('patient_id','FOREIGN'),
            lambda e:e['new_owner_origins'][0]['own_items'].clear())
        for edit in edits:
            changed=new_fixture();edit(changed[-1])
            with self.assertRaises(ValueError):check(changed)

    def test_new_dynamic_projection_cannot_add_tail_singleton_or_change_FIFO_prefix(self):
        edits=(lambda r:r['dynamic_row_projection']['projection']['steps'].append({}),
            lambda r:r['dynamic_row_projection']['projection'].__setitem__('singleton',{}),
            lambda r:r['dynamic_row_projection']['projection']['initial_pending'].clear(),
            lambda r:r.__setitem__('first_plan_source_sha256','x'))
        for edit in edits:
            changed=new_fixture();edit(changed[-1]['new_row_scores'][0])
            with self.assertRaises(ValueError):check(changed)

    def test_original_feasibility_anchor_coverage_and_source_metadata_bound(self):
        for edit in (lambda e:e['new_menu_indices'].append(e['new_menu_indices'][0]),lambda e:e['new_row_scores'].pop(),
            lambda e:e.__setitem__('stoploss_anchor_index',9999),
            lambda e:e['new_coverage'].__setitem__('fresh_residual_origin_count',0),
            lambda e:e['scoring_source_sha256'].__setitem__('cohort_hedge_model','x')):
            changed=new_fixture();edit(changed[-1])
            with self.assertRaises(ValueError):check(changed)

    def test_multirow_source_order_cannot_be_reversed(self):
        changed=new_fixture(extras=0,hospitals=5,statuses=False)
        self.assertGreater(len(changed[-1]['new_menu_indices']),1)
        changed[-1]['new_menu_indices'].reverse()
        with self.assertRaises(ValueError):check(changed)

    def test_new_residual_scores_guard_and_wait_staging_bound(self):
        for edit in (lambda e:e['new_row_scores'][0]['unchanged_law_residual_member_metrics'].pop(),
            lambda e:e['new_row_scores'][0]['member_metrics'].pop(),
            lambda e:e['owner_probability_guard']['admissible_row_indices'].clear(),
            lambda e:e['protected_wait_selection'].__setitem__('minimum_wait_seconds',-1),
            lambda e:e['new_row_scores'][0]['first_cohort_envelope']['patient_scores'][0]['metrics'].__setitem__('selection_probability',.123)):
            changed=new_fixture();edit(changed[-1])
            with self.assertRaises(ValueError):check(changed)

    def test_complete_owner_capture_version_and_priority_mutations_rejected(self):
        for edit in (lambda o:o['inventory'].pop(),lambda o:o['inventory'][0]['patient'].__setitem__('version',999),
            lambda o:o['inventory'][-1]['patient'].__setitem__('effective_priority',1)):
            changed=new_fixture();edit(changed[6])
            with self.assertRaises(ValueError):check(changed)

    def test_standard_singleton_fallback_all_parent_tags_and_FIFO_retained(self):
        raw=prior_tests.prior_tests.prior_tests.horizon_tests.replay();branch=raw['branches'][0];branch['joint_groups']=[]
        for event in branch['db_events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            for candidate in call['inputs']['candidates']:candidate['resources_enough']=True
            evidence=call['output']['evidence'];evidence['policy']=audit.NAME
            evidence['joint_tail']=dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False)
            evidence['joint_first']=dict(schema_version=1,mode='horizon_fallback',current_only=False)
            evidence['joint_budget']=dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False)
            for name in ('cohort_success','cohort_dynamic','cohort_hedge','cohort_recourse','cohort_followplan',
                         'cohort_stoploss','cohort_fullmenu','cohort_pareto','cohort_waitpareto','cohort_envelope','cohort_pairscope'):
                evidence[name]=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
            decision['evidence']=deepcopy(evidence)
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        proof=audit.audit_replay(raw);self.assertTrue(proof['passed'])
        self.assertTrue(proof['branch_audits'][0]['new_activation_exactly_no_tail_first_pair'])
        bad=deepcopy(raw);bad['branches'][0]['policy_calls'][0]['output']['evidence']['cohort_envelope']['evaluated']=True
        with self.assertRaises(ValueError):audit.audit_replay(bad)

    def test_old_controls_delegate_and_import_has_no_producer(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y')
        expected=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=expected) as call:self.assertIs(audit.audit_replay(raw),expected)
        call.assert_called_once_with(raw)
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_pairscope_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        value=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(value.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
