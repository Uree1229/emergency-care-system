"""Independent local-envelope algebra, all-row audit and source mutations."""
from copy import deepcopy
from functools import lru_cache
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_envelope_audit as audit
from test_inquiry_lab_cohort_stoploss_model import queue_row,first
import test_inquiry_lab_cohort_waitpareto_audit as prior_tests

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


@lru_cache(maxsize=1)
def _source_fixture():
    from inquiry_lab import cohort_envelope_policy as producer
    events,registry,c,ms,ts,p,owners,d,e=prior_tests.fixture()
    original=dict(decisions=deepcopy(d),evidence=deepcopy(e))
    native(events,registry,c,ms,ts,owners)
    with patch.object(producer.original,'decide_joint',return_value=deepcopy(original)):
        result=producer.decide_joint(producer.NAME,members=ms,joint_context=c,tail_members=ts,priority_context=p,owner_context=owners)
    return events,registry,c,ms,ts,p,owners,result['decisions'],result['evidence']


def fixture():return deepcopy(_source_fixture())


def check(data):
    events,registry,c,ms,ts,p,owners,d,e=data
    return audit.audit_pair(ms,c,ts,d,e,p,owners)


class LocalEnvelopeTests(unittest.TestCase):
    def test_discrete_declared_law_matches_direct_expectation_and_exposes_Jensen_gap(self):
        atoms=((0.,.5),(200.,.5))
        def J(d,t):return dict(remaining_seconds=sum(prob*max(c-t,0.) for c,prob in atoms),numeric_bound_tolerance_seconds=0.)
        with patch.object(audit.stoploss,'base_stoploss_at_elapsed',side_effect=J):
            for send,prior in ((0.,100.),(50.,100.),(100.,20.),(200.,300.),(300.,100.)):
                proof=audit.envelope_wait({},send,prior)
                expected=sum(prob*max(max(c,prior)-send,0.) for c,prob in atoms)
                self.assertEqual(proof['envelope_wait_seconds'],expected)
                self.assertFalse(proof['numeric_clamp_applied'])
            proof=audit.envelope_wait({},0.,100.)
            self.assertEqual(proof['old_max_of_means_seconds'],100.)
            self.assertEqual(proof['envelope_wait_seconds'],150.)
            self.assertEqual(proof['envelope_minus_old_max_seconds'],50.)

    def test_T_before_send_and_deterministic_C0_reduce_to_parent_max(self):
        for c in (0.,13.,200.):
            def J(d,t):return dict(remaining_seconds=max(c-t,0.),numeric_bound_tolerance_seconds=0.)
            with patch.object(audit.stoploss,'base_stoploss_at_elapsed',side_effect=J):
                for send,prior in ((0.,0.),(20.,10.),(10.,20.),(20.,400.)):
                    proof=audit.envelope_wait({},send,prior)
                    self.assertEqual(proof['envelope_wait_seconds'],max(c-send,prior-send,0.))

    def test_real_shifted_thinned_law_matches_producer_without_mutation(self):
        from inquiry_lab import cohort_envelope_model as producer
        for q in (0.,1.,40.,100.):
            d=audit.stoploss.base_stoploss_decomposition(queue_row(q))
            for send,prior in ((0.,0.),(5.,0.),(20.,30.),(70.,80.),(100.,200.)):
                before=deepcopy(d);proof=audit.envelope_wait(d,send,prior)
                audit.precise_same(proof,producer.envelope_wait(d,send,prior),'synthetic independent local envelope')
                self.assertEqual(d,before)
                tol=proof['numeric_bound_tolerance_seconds']
                self.assertGreaterEqual(proof['envelope_wait_seconds'],proof['old_max_of_means_seconds']-tol)
                self.assertLessEqual(proof['envelope_wait_seconds'],proof['local_upper_bound_seconds']+tol)

    def test_invalid_times_and_material_local_bound_violations_rejected(self):
        d=audit.stoploss.base_stoploss_decomposition(queue_row(40.))
        for bad in (True,-1.,math.nan,math.inf):
            for args in ((bad,0.),(0.,bad)):
                with self.assertRaises(ValueError):audit.envelope_wait(d,*args)
        def broken(d,t):return dict(remaining_seconds=100. if t==0 else 0.,numeric_bound_tolerance_seconds=0.)
        with patch.object(audit.stoploss,'base_stoploss_at_elapsed',side_effect=broken):
            with self.assertRaises(ValueError):audit.envelope_wait({},0.,10.)

    def test_frozen_plans_fixed_F0_initial_CDFs_and_source_copies_unchanged(self):
        from inquiry_lab import cohort_envelope_model as producer
        plans=[first('A',[('X',15,40.),('Y',35,20.)]),first('B',[('X',20,40.)])]
        before=deepcopy(plans);actual=audit.score_first_cohort(plans)
        audit.precise_same(actual,producer.score_first_cohort(plans),'synthetic whole first score')
        self.assertEqual(plans,before)
        old=audit.stoploss.flatten_future_jobs(plans)
        for previous,new in zip(old,actual['jobs']):
            self.assertEqual(previous['fixed_F0_wait_seconds'],new['fixed_F0_wait_seconds'])
            self.assertEqual(previous['source_plan'],new['source_plan'])
            self.assertEqual(new['queue_envelope']['prior_is_deterministic_forecast'],True)
            self.assertFalse(new['queue_envelope']['numeric_clamp_applied'])
        for previous,new in zip(plans,actual['patient_scores']):
            self.assertEqual(previous['hypothetical_origin']['own_items'],new['fixed_initial_items'])


class CompleteEnvelopeAuditTests(unittest.TestCase):
    def test_independent_all_row_rebuild_matches_synthetic_producer_and_parent_immutable(self):
        data=fixture();before=deepcopy(data);counts=check(data)
        self.assertEqual(data,before)
        e=data[-1];old=e['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
        self.assertEqual(counts['envelope_rows_checked'],len(old['row_scores']))
        self.assertEqual([r['row_index'] for r in e['new_row_scores']],old['menu_indices'])
        self.assertEqual(counts['envelope_guard_anchor_rescored_checked'],1)
        self.assertEqual(counts['envelope_local_barrier_checks'],counts['envelope_jobs_checked'])
        self.assertTrue(all(k in counts for k in ('envelope_groups_evaluated','envelope_groups_unevaluated','envelope_interventions')))

    def test_every_local_queue_proof_field_is_bound_and_tampering_rejected(self):
        data=fixture();jobs=next(r['first_cohort_envelope']['jobs'] for r in data[-1]['new_row_scores'] if r['first_cohort_envelope']['jobs'])
        self.assertTrue(jobs)
        for key in ('envelope_wait_seconds','old_max_of_means_seconds','barrier_elapsed_seconds','numeric_bound_tolerance_seconds',
                    'local_bounds_within_allowance','numeric_clamp_applied','prior_is_deterministic_forecast'):
            changed=fixture();job=next(r['first_cohort_envelope']['jobs'][0] for r in changed[-1]['new_row_scores'] if r['first_cohort_envelope']['jobs'])
            value=job['queue_envelope'][key];job['queue_envelope'][key]=not value if isinstance(value,bool) else value+1.
            with self.subTest(key=key),self.assertRaises(ValueError):check(changed)

    def test_F0_source_plan_chronology_and_initial_items_tampering_rejected(self):
        for key in ('fixed_F0_wait_seconds','send_offset_seconds','prior_future_completion_seconds'):
            changed=fixture();job=next(r['first_cohort_envelope']['jobs'][0] for r in changed[-1]['new_row_scores'] if r['first_cohort_envelope']['jobs'])
            job[key]+=1.
            with self.subTest(key=key),self.assertRaises(ValueError):check(changed)
        changed=fixture();firstscore=changed[-1]['new_row_scores'][0]['first_cohort_envelope']['patient_scores'][0]
        firstscore['initial_contact_cost_seconds']+=1.
        with self.assertRaises(ValueError):check(changed)

    def test_omitted_row_or_unrepaired_anchor_rejected(self):
        changed=fixture();changed[-1]['new_row_scores'].pop()
        with self.assertRaises(ValueError):check(changed)
        changed=fixture();e=changed[-1];anchor=e['stoploss_anchor_index']
        old=e['original_waitpareto_proposal']['evidence']['original_pareto_proposal']['evidence']['original_fullmenu_proposal']['evidence']
        index=next(i for i,r in enumerate(e['new_row_scores']) if r['row_index']==anchor)
        e['new_row_scores'][index]=deepcopy(next(r for r in old['row_scores'] if r['row_index']==anchor))
        with self.assertRaises(ValueError):check(changed)

    def test_source_binding_residual_partition_and_guard_mutations_rejected(self):
        for edit in (lambda e:e['new_row_scores'][0].__setitem__('first_plan_source_sha256','x'),
            lambda e:e['new_row_scores'][0]['member_metrics'].pop(),
            lambda e:e['owner_probability_guard']['admissible_row_indices'].pop(),
            lambda e:e['new_row_scores'][0]['unchanged_residual_member_metrics'].append({'patient_id':'FOREIGN'}),
            lambda e:e['scoring_source_sha256'].__setitem__('cohort_stoploss_model','x')):
            changed=fixture();edit(changed[-1])
            with self.assertRaises(ValueError):check(changed)

    def test_parent_evidence_and_actual_current_output_mutations_rejected(self):
        changed=fixture();changed[-1]['original_waitpareto_proposal']['evidence']['selected_index']+=1
        with self.assertRaises(ValueError):check(changed)
        changed=fixture();changed[-2][0]['send']=[] if changed[-2][0]['send'] else ['FOREIGN']
        with self.assertRaises(ValueError):check(changed)

    def test_protected_wait_selection_minimum_and_anchor_fields_bound(self):
        for key in ('minimum_wait_seconds','baseline_sum_capped_wait_seconds','selected_sum_capped_wait_seconds'):
            changed=fixture();changed[-1]['protected_wait_selection'][key]+=1.
            with self.subTest(key=key),self.assertRaises(ValueError):check(changed)
        changed=fixture();changed[-1]['stoploss_anchor_index']+=1
        with self.assertRaises(ValueError):check(changed)

    def test_standard_singleton_fallback_all_parent_tags_and_FIFO_retained(self):
        raw=prior_tests.prior_tests.horizon_tests.replay();branch=raw['branches'][0];branch['joint_groups']=[]
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
                         'cohort_stoploss','cohort_fullmenu','cohort_pareto','cohort_waitpareto','cohort_envelope'):
                evidence[name]=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
            decision['evidence']=deepcopy(evidence)
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        proof=audit.audit_replay(raw);self.assertTrue(proof['passed'])
        self.assertTrue(proof['branch_audits'][0]['envelope_exact_only_for_declared_random_C0_deterministic_T'])
        self.assertFalse(proof['branch_audits'][0]['global_trajectory_queue_nonregression_claimed'])
        bad=deepcopy(raw);bad['branches'][0]['policy_calls'][0]['output']['evidence']['cohort_waitpareto']['evaluated']=True
        with self.assertRaises(ValueError):audit.audit_replay(bad)

    def test_old_controls_delegate_and_import_has_no_producer(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y')
        expected=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=expected) as call:self.assertIs(audit.audit_replay(raw),expected)
        call.assert_called_once_with(raw)
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_envelope_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        value=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(value.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
