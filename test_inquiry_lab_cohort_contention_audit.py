"""Independent fixed-F0 future-work recurrence and complete source mutations."""
from copy import deepcopy
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_contention_audit as audit
import test_inquiry_lab_cohort_followplan_audit as prior_tests

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
    ev=original['evidence'];menu=deepcopy(ev['menu_indices']);scores=[];selection=None
    evaluated=ev['evaluated'];base_index=ev['selected_index'];selected=base_index
    hedge=ev['original_recourse_proposal']['evidence']['original_hedge_proposal']
    table=audit.dynamic._nested_joint(hedge['evidence']['original_dynamic_proposal']['evidence']['original_cohort_success_proposal'])['evidence']
    if evaluated:
        for row in ev['row_scores']:
            first=audit.score_first_cohort(row['first_cohort_future']);residual=deepcopy(row['owner_scores'])
            metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics']))
                for s in first['patient_scores']+residual]
            scores.append(dict(row_index=row['row_index'],base_row_score=deepcopy(row),first_cohort_contention=first,
                owner_scores=residual,member_metrics=metrics,
                tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.0) for t in (4,2,1)},
                sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.0)))
        selection=audit.dynamic.base.global_staged_choice(scores,base_index)
        selection['base_selected_tied']=selection.pop('base_budget_tied');selected=selection['selected_index']
    intervened=selected!=base_index;decisions=deepcopy(original['decisions'])
    if intervened:
        chosen=next(r for r in scores if r['row_index']==selected);decisions=deepcopy(table['baseline_proposals'])
        for i,d in enumerate(decisions):
            sends=table['combinations'][selected]['hospitals'][i]
            d.update(send=list(sends),reason='cohort_contention_current_only');d['evidence']['sent_hospitals']=list(sends)
            d['evidence']['planned_expected_cost']=round(chosen['member_metrics'][i]['metrics']['total_cost_seconds'],3)
            for item in d['evidence']['candidate_plan']:item['planned_offset_seconds']=0 if item['hospital'] in sends else None
    for i,d in enumerate(decisions):
        d['evidence']['policy']=audit.NAME
        d['evidence']['cohort_contention']=dict(schema_version=1,mode='pair_cohort_contention',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,selected_index=selected,
            effective_priority=p['patients'][i]['effective_priority'])
    evidence=dict(schema_version=1,rule=audit.RULE,base_policy=audit.BASE_POLICY,base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME,calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_followplan__'),
        joint_context=deepcopy(c),priority_context=deepcopy(p),owner_context=deepcopy(owners),original_followplan_proposal=deepcopy(original),
        evaluated=evaluated,menu_indices=menu,row_scores=scores,selection=selection,base_selected_index=base_index,
        selected_index=selected,intervened=intervened,tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),
        objective=audit.OBJECTIVE,scope=audit.SCOPE)
    return decisions,evidence


def fixture():
    events,registry,c,ms,ts,p,owners,d,e=prior_tests.fixture();original=dict(decisions=deepcopy(d),evidence=deepcopy(e))
    native(events,registry,c,ms,ts,owners);d,e=output(ms,c,ts,p,owners,original)
    return events,registry,c,ms,ts,p,owners,d,e


def initial_item(hospital='FAST',probability=.6,wait=0.,remaining=100):
    item=dict(hospital=hospital,travel_seconds=50.,service_mu=math.log(10.),service_sigma=1.,
        total_probability=probability,mode='queued',wait_seconds=wait,spent_seconds=0.,f0=0.)
    item['masses']=audit.horizon.masses(lambda x:audit.base.descriptor_cdf(item,x),1.,audit.horizon.intervals(remaining))
    return item


def hand_plan(pid,contacts,*,initial=(),remaining=100):
    records=[dict(hospital=h,estimated_wait_seconds=q,service_mu=math.log(10.),service_sigma=1.,
        reply_probability=1.,accept_probability=1.,mean_service_seconds=10.,assumed_workers=1,
        travel_seconds=100.,inquiry_cost_seconds=15.) for h,s,q in contacts]
    return dict(patient_id=pid,effective_priority=4,
        source_owner=dict(evaluation_end_at=1000+remaining,inputs=dict(now=1000)),
        hypothetical_origin=dict(own_items=deepcopy(list(initial)),cell_edges_seconds=[r for _,r in audit.horizon.intervals(remaining)]),
        initial_contact_cost_seconds=float(15*len(initial)),
        continuation=dict(queue_records=records,plan=[dict(hospital=h,offset_seconds=s) for h,s,q in contacts]))


class IndependentFutureWorkHandTests(unittest.TestCase):
    def test_future_front_service_still_conditions_on_initial_reply_by_send_time(self):
        initial=initial_item(remaining=17);plans=[hand_plan('A',[('X',15,0.)],initial=[initial],remaining=17)]
        before=deepcopy(plans);result=audit.score_first_cohort(plans);job=result['jobs'][0]
        expected_survival=1-audit.base.descriptor_cdf(initial,15)
        self.assertEqual(job['corrected_wait_seconds'],0.)
        self.assertAlmostEqual(job['no_prior_accept_probability'],expected_survival,places=14)
        self.assertLess(job['expected_work_seconds'],10.)
        own=result['patient_scores'][0];future_probability=audit.parent._cdf(2,math.log(10.),1.)
        initial_probability=audit.base.descriptor_cdf(initial,17)
        expected=1-(1-initial_probability)*(1-future_probability)
        self.assertAlmostEqual(own['metrics']['selection_probability'],expected,places=14)
        self.assertAlmostEqual(own['future_contact_cost_seconds'],15*expected_survival,places=14)
        self.assertEqual(own['cell_edges_seconds'],[5,10,15,17]);self.assertEqual(plans,before)

    def test_chronological_ties_source_indices_and_static_unaged_work(self):
        plans=[hand_plan('A',[('X',15,0.)]),hand_plan('B',[('X',15,0.)]),hand_plan('C',[('X',45,0.)])]
        result=audit.score_first_cohort(plans);jobs=result['jobs']
        self.assertEqual([(j['send_offset_seconds'],j['first_patient_index'],j['plan_candidate_index']) for j in jobs],[(15,0,0),(15,1,0),(45,2,0)])
        self.assertEqual([j['corrected_wait_seconds'] for j in jobs],[0.,10.,20.])
        self.assertEqual(jobs[2]['expected_work_seconds'],10.)
        self.assertEqual([p['job_indices'] for p in result['patient_scores']],[[0],[1],[2]])

    def test_future_survival_uses_fixed_other_hospital_F0_and_clamps_only_probe_to_H(self):
        plans=[hand_plan('B',[('Y',0,0.)]),hand_plan('A',[('X',15,30.),('Y',15,0.)])]
        result=audit.score_first_cohort(plans);jobs=result['jobs'];x=jobs[1];y=jobs[2]
        self.assertEqual(y['corrected_wait_seconds'],10.)
        factor=next(f for f in x['fixed_F0_survival_factors'] if f['kind']=='future')
        expected=audit.parent._cdf(30.,math.log(10.),1.)
        self.assertAlmostEqual(factor['selection_probability_at_probe'],expected,places=14)
        self.assertNotAlmostEqual(factor['selection_probability_at_probe'],audit.future_cdf(y,45.,'corrected_wait_seconds'),places=10)
        short=[hand_plan('A',[('X',15,30.)],initial=[initial_item(remaining=17)],remaining=17)]
        row=audit.score_first_cohort(short)['jobs'][0]
        self.assertEqual(row['probe_offset_seconds'],17)
        self.assertGreater(row['expected_work_seconds'],0.)

    def test_empty_future_plan_preserves_fixed_initial_CDF_and_cost(self):
        parent=hand_plan('A',[],initial=[initial_item()]);value=audit.score_first_cohort([parent])
        self.assertEqual(value['jobs'],[]);self.assertEqual(value['patient_scores'][0]['future_contact_cost_seconds'],0.)
        self.assertEqual(value['patient_scores'][0]['fixed_initial_items'],parent['hypothetical_origin']['own_items'])
        self.assertAlmostEqual(value['patient_scores'][0]['metrics']['selection_probability'],sum(parent['hypothetical_origin']['own_items'][0]['masses']),places=14)

    def test_invalid_fractional_worker_wait_partial_H_and_reordered_jobs_rejected(self):
        for edit in (lambda p:p[0]['continuation']['queue_records'][0].__setitem__('assumed_workers',.5),
                lambda p:p[0]['continuation']['queue_records'][0].__setitem__('estimated_wait_seconds',-1.),
                lambda p:p[0]['continuation']['plan'][0].__setitem__('offset_seconds',100),
                lambda p:p[0]['hypothetical_origin'].__setitem__('cell_edges_seconds',[100])):
            plans=[hand_plan('A',[('X',15,0.)])];edit(plans)
            with self.assertRaises(ValueError):audit.score_first_cohort(plans)
        plans=[hand_plan('A',[('X',15,0.)]),hand_plan('B',[('X',15,0.)])]
        jobs=audit.flatten_future_jobs(plans);jobs.reverse()
        with self.assertRaises(ValueError):audit.expected_future_work(jobs,plans)


class IndependentContentionPairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=fixture()

    def check(self,d=None,e=None):
        _,_,c,ms,ts,p,o,decisions,evidence=self.data
        return audit.audit_pair(ms,c,ts,decisions if d is None else d,evidence if e is None else e,p,o)

    def test_complete_parent_and_fixed_plans_leave_inputs_unchanged(self):
        before=deepcopy(self.data);result=self.check();self.assertEqual(self.data,before)
        self.assertGreater(result['contention_future_jobs_checked'],0)
        self.assertEqual(result['contention_rows_checked'],len(self.data[-1]['menu_indices']))
        for row in self.data[-1]['row_scores']:self.assertEqual(row['owner_scores'],row['base_row_score']['owner_scores'])

    def test_fixed_source_F0_forward_work_corrected_CDF_and_contact_cost_tampering_rejected(self):
        changes=(lambda f:f['jobs'][0].__setitem__('base_wait_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('corrected_wait_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('expected_work_seconds',0.),
            lambda f:f['jobs'][0]['source_plan'].__setitem__('offset_seconds',999),
            lambda f:f['jobs'][0]['source_queue_record'].__setitem__('assumed_workers',.5),
            lambda f:f['patient_scores'][0]['fixed_initial_items'][0].__setitem__('total_probability',0.),
            lambda f:f['patient_scores'][0]['future_items'][0].__setitem__('total_probability',0.),
            lambda f:f['patient_scores'][0].__setitem__('future_contact_cost_seconds',-1.))
        for i,edit in enumerate(changes):
            e=deepcopy(self.data[-1]);edit(e['row_scores'][0]['first_cohort_contention'])
            with self.subTest(i=i),self.assertRaises(ValueError):self.check(e=e)

    def test_parent_contacts_residuals_partition_and_priority_choice_are_bound(self):
        changes=(lambda e:e['original_followplan_proposal']['evidence']['row_scores'][0]['first_cohort_future'][0]['continuation']['plan'][0].__setitem__('offset_seconds',999),
            lambda e:e['row_scores'][0]['owner_scores'].pop(),lambda e:e['row_scores'][0]['member_metrics'].pop(),
            lambda e:e['selection'].__setitem__('selected_index',999),lambda e:e['row_scores'].pop())
        for i,edit in enumerate(changes):
            e=deepcopy(self.data[-1]);edit(e)
            with self.subTest(i=i),self.assertRaises(ValueError):self.check(e=e)

    def test_actual_outputs_are_separately_reconstructed(self):
        for edit in (lambda d:d[0]['send'].append('HIDDEN'),lambda d:d[0]['evidence']['cohort_contention'].__setitem__('selected_index',999),
                lambda d:d[0]['evidence'].__setitem__('planned_expected_cost',-1.)):
            d=deepcopy(self.data[-2]);edit(d)
            with self.assertRaises(ValueError):self.check(d=d)


class ParentDelegationAndTraceTests(unittest.TestCase):
    def test_parent_controls_delegate_to_frozen_Followplan_chain(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y')
        proof=dict(passed=True,new_executions=0)
        with patch.object(audit.foundation,'audit_replay',return_value=proof) as call:self.assertIs(audit.audit_replay(raw),proof)
        call.assert_called_once_with(raw)

    def test_complete_saved_calls_capture_and_all_patient_FIFO(self):
        saved=[];original_audit=audit.foundation.audit_replay
        def capture(replay):
            proof=original_audit(replay);saved.append(deepcopy(replay));return proof
        with patch.object(audit.foundation,'audit_replay',side_effect=capture):
            prior_tests.ParentDelegationAndImportTests().test_complete_saved_DB_calls_source_capture_sends_and_FIFO()
        self.assertEqual(len(saved),1);raw=saved[0];branch=raw['branches'][0];group=branch['joint_groups'][0]
        original=dict(decisions=deepcopy(group['outputs']),evidence=deepcopy(group['evidence']))
        native(branch['db_events'],group['registry_snapshot'],group['context'],group['members'],group['tail_members'],group['owner_context'],group['owner_source_snapshot'])
        group['outputs'],group['evidence']=output(group['members'],group['context'],group['tail_members'],group['priority_context'],group['owner_context'],original)
        self.assertEqual([d['send'] for d in group['outputs']],[d['send'] for d in original['decisions']])
        for i,(call,decision) in enumerate(zip(branch['policy_calls'],branch['db_decisions'])):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            if i<2:call['output']=deepcopy(group['outputs'][i])
            else:call['output']['evidence'].update(policy=audit.NAME,cohort_contention=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
            decision.update(hospital_ids=deepcopy(call['output']['send']),reason=call['output']['reason'],evidence=deepcopy(call['output']['evidence']))
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        result=audit.audit_replay(raw);self.assertTrue(result['passed']);self.assertEqual(result['new_executions'],0)
        for edit in (lambda r:r['branches'][0]['policy_calls'][0].__setitem__('db_decision_id',999),
                lambda r:r['branches'][0]['joint_groups'][0]['owner_source_snapshot']['rows'][0]['patient'].__setitem__('version',999)):
            value=deepcopy(raw);edit(value)
            with self.assertRaises(ValueError):audit.audit_replay(value)

    def test_import_has_no_producer_or_mutated_hook(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_contention_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        value=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(value.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
