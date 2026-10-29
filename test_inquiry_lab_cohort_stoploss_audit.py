"""Independent stop-loss integration, fixed evidence and complete mutations."""
from copy import deepcopy
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_stoploss_audit as audit
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
            scores.append(dict(row_index=row['row_index'],base_row_score=deepcopy(row),first_cohort_stoploss=first,
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
            d.update(send=list(sends),reason='cohort_stoploss_current_only');d['evidence']['sent_hospitals']=list(sends)
            d['evidence']['planned_expected_cost']=round(chosen['member_metrics'][i]['metrics']['total_cost_seconds'],3)
            for item in d['evidence']['candidate_plan']:item['planned_offset_seconds']=0 if item['hospital'] in sends else None
    for i,d in enumerate(decisions):
        d['evidence']['policy']=audit.NAME
        d['evidence']['cohort_stoploss']=dict(schema_version=1,mode='pair_cohort_stoploss',evaluated=evaluated,
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
    records=[queue_row(h,q) for h,s,q in contacts]
    return dict(patient_id=pid,effective_priority=4,
        source_owner=dict(evaluation_end_at=1000+remaining,inputs=dict(now=1000)),
        hypothetical_origin=dict(own_items=deepcopy(list(initial)),cell_edges_seconds=[r for _,r in audit.horizon.intervals(remaining)]),
        initial_contact_cost_seconds=float(15*len(initial)),
        continuation=dict(queue_records=records,plan=[dict(hospital=h,offset_seconds=s) for h,s,q in contacts]))


def queue_row(hospital='X',q0=30.,*,age=0.,mu=math.log(10.),sigma=1.,reply=.7):
    mean=math.exp(mu+.5*sigma*sigma)
    if age is None:
        head=None;mR=0.;count=0
    else:
        head=dict(hospital=hospital,patient='VISIBLE',submitted_at=1000-age);count=1
        if age==0:mR=mean
        else:
            z=(math.log(age)-mu)/sigma
            J=mean*.5*math.erfc((z-sigma)/math.sqrt(2))-age*.5*math.erfc(z/math.sqrt(2))
            denominator=1-reply+reply*.5*math.erfc(z/math.sqrt(2))
            mR=J/denominator
    return dict(hospital=hospital,estimated_wait_seconds=q0,service_mu=mu,service_sigma=sigma,
        reply_probability=reply,accept_probability=1.,mean_service_seconds=mean,assumed_workers=1,
        travel_seconds=100.,inquiry_cost_seconds=15.,queue_count=count,head=head,age_seconds=age,
        head_expected_remaining_seconds=mR)


def integrate_stoploss(age,mu,sigma):
    """Independent Simpson integral over standard-normal service draws."""
    lower=-12. if age==0 else max(-12.,(math.log(age)-mu)/sigma)
    upper=max(12.,lower+12.);cells=24000;step=(upper-lower)/cells
    def integrand(z):
        return max(0.,math.exp(mu+sigma*z)-age)*math.exp(-z*z/2)/math.sqrt(2*math.pi)
    total=integrand(lower)+integrand(upper)
    total+=sum((4 if i%2 else 2)*integrand(lower+i*step) for i in range(1,cells))
    return total*step/3


class IndependentStoplossMathTests(unittest.TestCase):
    def test_shift_thinning_and_origin_preserve_parent_mean(self):
        for q0 in (0.,3.,16.48721270700128,50.):
            source=queue_row(q0=q0);before=deepcopy(source)
            d=audit.base_stoploss_decomposition(source);mR=source['head_expected_remaining_seconds']
            self.assertEqual(d['deterministic_follower_seconds'],max(q0-mR,0.))
            self.assertEqual(d['head_inclusion_probability'],min(1.,q0/mR))
            self.assertAlmostEqual(d['reconstructed_mean_seconds'],q0,places=14)
            self.assertEqual(audit.base_stoploss_at_elapsed(d,0)['remaining_seconds'],q0)
            self.assertEqual(source,before)

    def test_numeric_quadrature_agrees_for_shifted_and_thinned_laws(self):
        for q0,age,reply in ((40.,3.,.7),(2.,20.,.4),(20.,0.,1.),(10.,35.,0.)):
            row=queue_row(q0=q0,age=age,reply=reply);d=audit.base_stoploss_decomposition(row)
            F,p=d['deterministic_follower_seconds'],d['head_inclusion_probability']
            D=1-reply+reply*.5*math.erfc((math.log(age)-row['service_mu'])/row['service_sigma']/math.sqrt(2)) if age else 1.
            for s in (0.,1.,15.,60.):
                wanted=(F-s+p*row['head_expected_remaining_seconds'] if s<F else
                    p*integrate_stoploss(age+s-F,row['service_mu'],row['service_sigma'])/D)
                with self.subTest(q0=q0,age=age,s=s):
                    self.assertAlmostEqual(audit.base_stoploss_at_elapsed(d,s)['remaining_seconds'],wanted,delta=2e-8)

    def test_future_denominator_remains_current_and_no_reply_multiplier(self):
        row=queue_row(q0=20.,age=40.,reply=.8);d=audit.base_stoploss_decomposition(row)
        s=d['deterministic_follower_seconds']+30.;value=audit.base_stoploss_at_elapsed(d,s)
        D=d['current_no_reply_probability'];future_age=row['age_seconds']+30.
        J=integrate_stoploss(future_age,row['service_mu'],row['service_sigma'])
        self.assertAlmostEqual(value['remaining_seconds'],d['head_inclusion_probability']*J/D,delta=2e-8)
        future_D=1-row['reply_probability']+row['reply_probability']*.5*math.erfc((math.log(future_age)-row['service_mu'])/row['service_sigma']/math.sqrt(2))
        self.assertGreater(abs(J/D-J/future_D),1e-4)
        self.assertEqual(value['fixed_log_no_reply_probability'],d['current_log_no_reply_probability'])

    def test_monotone_convex_Jensen_bounds_and_positive_tail_after_mean(self):
        for q0,age,reply in ((0.,0.,0.),(2.,0.,.7),(50.,10.,1.),(10.,30.,0.)):
            d=audit.base_stoploss_decomposition(queue_row(q0=q0,age=age,reply=reply))
            values=[audit.base_stoploss_at_elapsed(d,s)['remaining_seconds'] for s in range(121)]
            for s,value in enumerate(values):
                self.assertGreaterEqual(value,max(q0-s,0.));self.assertLessEqual(value,q0)
            self.assertTrue(all(a>=b for a,b in zip(values,values[1:])))
            self.assertTrue(all(a-2*b+c>=-1e-11 for a,b,c in zip(values,values[1:],values[2:])))
            if q0:self.assertGreater(values[120],0.)

    def test_stable_late_tail_r_one_and_silent_zero_reply(self):
        for reply in (0.,.999999999999,1.):
            row=queue_row(q0=10.,age=1e8,reply=reply)
            row['head_expected_remaining_seconds']=math.exp(audit._log_stoploss(row['service_mu'],row['service_sigma'],row['age_seconds'])-
                audit._current_log_denominator(row['service_mu'],row['service_sigma'],reply,row['age_seconds']))
            d=audit.base_stoploss_decomposition(row);value=audit.base_stoploss_at_elapsed(d,1e8)
            self.assertTrue(math.isfinite(value['remaining_seconds']))
            self.assertGreater(value['remaining_seconds'],0.)

    def test_empty_head_and_underflow_zero_head_remain_consistent(self):
        d=audit.base_stoploss_decomposition(queue_row(q0=0.,age=None))
        self.assertEqual(d['current_log_no_reply_probability'],None)
        self.assertEqual(audit.base_stoploss_at_elapsed(d,50.)['remaining_seconds'],0.)
        row=queue_row(q0=12.,age=0.,reply=0.);row['age_seconds']=1e200;row['head_expected_remaining_seconds']=0.
        d=audit.base_stoploss_decomposition(row)
        self.assertEqual(d['decomposition_branch'],'deterministic_zero_head')
        self.assertEqual(audit.base_stoploss_at_elapsed(d,5.)['remaining_seconds'],7.)
        self.assertEqual(audit.base_stoploss_at_elapsed(d,30.)['remaining_seconds'],0.)
        for row in (queue_row(q0=1.,age=None),dict(queue_row(),head=None),dict(queue_row(),age_seconds=None),
                dict(queue_row(),head_expected_remaining_seconds=999.),dict(queue_row(),queue_count=.5)):
            with self.assertRaises(ValueError):audit.base_stoploss_decomposition(row)

    def test_fixed_F0_maximum_chronology_and_initial_descriptors(self):
        plans=[hand_plan('A',[('X',10,30.),('Y',30,10.)],initial=[initial_item()]),hand_plan('B',[('X',15,30.)])]
        before=deepcopy(plans);result=audit.score_first_cohort(plans);jobs=result['jobs']
        self.assertEqual([j['send_offset_seconds'] for j in jobs],[10,15,30])
        completion={}
        for j in jobs:
            prior=completion.get(j['hospital'],0.);s=j['send_offset_seconds']
            self.assertEqual(j['base_remaining_seconds'],j['base_stoploss_at_send']['remaining_seconds'])
            self.assertEqual(j['fixed_F0_wait_seconds'],j['base_remaining_seconds'])
            self.assertEqual(j['corrected_wait_seconds'],max(j['base_remaining_seconds'],prior-s,0.))
            self.assertAlmostEqual(j['future_completion_after_seconds'],s+j['corrected_wait_seconds']+j['expected_work_seconds'],places=14)
            completion[j['hospital']]=j['future_completion_after_seconds']
        self.assertEqual(result['patient_scores'][0]['fixed_initial_items'],plans[0]['hypothetical_origin']['own_items'])
        self.assertEqual(plans,before)

    def test_empty_future_partial_H_and_reordered_jobs(self):
        plan=hand_plan('A',[],initial=[initial_item(remaining=17)],remaining=17)
        value=audit.score_first_cohort([plan]);self.assertEqual(value['jobs'],[])
        self.assertEqual(value['patient_scores'][0]['cell_edges_seconds'],[5,10,15,17])
        jobs=audit.flatten_future_jobs([hand_plan('A',[('X',10,30.)]),hand_plan('B',[('Y',15,30.)])]);jobs.reverse()
        with self.assertRaises(ValueError):audit.expected_future_work(jobs,[])

    def test_numerical_clamp_discloses_ulp_bound_and_rejects_material_error(self):
        d=audit.base_stoploss_decomposition(queue_row(q0=30.))
        value=audit.base_stoploss_at_elapsed(d,25.)
        tolerance=32*max(math.ulp(d['q0_seconds']),math.ulp(value['raw_remaining_seconds']),
            math.ulp(value['baseline_positive_part_seconds']))
        if value['log_stoploss_numeric_error_bound'] is not None:
            tolerance=max(tolerance,abs(value['raw_remaining_seconds'])*math.expm1(value['log_stoploss_numeric_error_bound']+
                32*math.ulp(value['fixed_log_no_reply_probability'])))
        self.assertEqual(value['numeric_bound_tolerance_seconds'],tolerance)
        self.assertLessEqual(abs(value['numeric_bound_correction_seconds']),tolerance)
        changed=deepcopy(d);changed['q0_seconds']=1.
        with self.assertRaises(ValueError):audit.base_stoploss_at_elapsed(changed,25.)

    def test_high_age_roundoff_is_disclosed_without_masking_false_head_underflow(self):
        row=queue_row(age=1e9,reply=1.)
        mean=math.exp(audit._log_stoploss(row['service_mu'],row['service_sigma'],row['age_seconds'])-
            audit._current_log_denominator(row['service_mu'],row['service_sigma'],1.,row['age_seconds']))
        row['estimated_wait_seconds']=row['head_expected_remaining_seconds']=mean
        d=audit.base_stoploss_decomposition(row);v=audit.base_stoploss_at_elapsed(d,15.)
        self.assertGreater(v['log_stoploss_numeric_error_bound'],0.)
        self.assertLessEqual(abs(v['numeric_bound_correction_seconds']),v['numeric_bound_tolerance_seconds'])
        self.assertGreaterEqual(v['remaining_seconds'],mean-15.)
        false=queue_row(age=1e8,reply=0.);false['head_expected_remaining_seconds']=0.
        with self.assertRaises(ValueError):audit.base_stoploss_decomposition(false)


class IndependentStoplossPairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=fixture()

    def check(self,d=None,e=None):
        _,_,c,ms,ts,p,o,decisions,evidence=self.data
        return audit.audit_pair(ms,c,ts,decisions if d is None else d,evidence if e is None else e,p,o)

    def test_complete_parent_and_fixed_plans_leave_inputs_unchanged(self):
        before=deepcopy(self.data);result=self.check();self.assertEqual(self.data,before)
        self.assertGreater(result['stoploss_future_jobs_checked'],0)
        self.assertEqual(result['stoploss_rows_checked'],len(self.data[-1]['menu_indices']))
        for row in self.data[-1]['row_scores']:self.assertEqual(row['owner_scores'],row['base_row_score']['owner_scores'])

    def test_fixed_source_F0_forward_work_corrected_CDF_and_contact_cost_tampering_rejected(self):
        changes=(lambda f:f['jobs'][0].__setitem__('base_wait_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('fixed_F0_wait_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('elapsed_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('base_remaining_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('prior_future_completion_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('prior_future_remaining_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('future_completion_after_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('corrected_wait_seconds',999.),
            lambda f:f['jobs'][0].__setitem__('expected_work_seconds',0.),
            lambda f:f['jobs'][0]['source_plan'].__setitem__('offset_seconds',999),
            lambda f:f['jobs'][0]['source_queue_record'].__setitem__('assumed_workers',.5),
            lambda f:f['patient_scores'][0]['fixed_initial_items'][0].__setitem__('total_probability',0.),
            lambda f:f['patient_scores'][0]['future_items'][0].__setitem__('total_probability',0.),
            lambda f:f['patient_scores'][0].__setitem__('future_contact_cost_seconds',-1.))
        for i,edit in enumerate(changes):
            e=deepcopy(self.data[-1]);edit(e['row_scores'][0]['first_cohort_stoploss'])
            with self.subTest(i=i),self.assertRaises(ValueError):self.check(e=e)

    def test_every_decomposition_and_at_send_field_is_reconstructed(self):
        source=self.data[-1]['row_scores'][0]['first_cohort_stoploss']['jobs'][0]
        def changed(value):
            if value is None:return 1.
            if type(value) is str:return value+'_TAMPER'
            if type(value) is bool:return not value
            return value+1
        for section in ('base_stoploss_decomposition','base_stoploss_at_send'):
            for key,value in source[section].items():
                e=deepcopy(self.data[-1]);job=e['row_scores'][0]['first_cohort_stoploss']['jobs'][0]
                job[section][key]=changed(value)
                with self.subTest(section=section,key=key),self.assertRaises(ValueError):self.check(e=e)
            e=deepcopy(self.data[-1]);e['row_scores'][0]['first_cohort_stoploss']['jobs'][0][section]['invented_field']=True
            with self.subTest(section=section,key='extra'),self.assertRaises(ValueError):self.check(e=e)

    def test_every_frozen_source_queue_field_is_bound_to_parent(self):
        row=self.data[-1]['row_scores'][0]['first_cohort_stoploss']['jobs'][0]['source_queue_record']
        for key,value in row.items():
            e=deepcopy(self.data[-1]);actual=e['row_scores'][0]['first_cohort_stoploss']['jobs'][0]['source_queue_record']
            if value is None:actual[key]='TAMPER'
            elif type(value) is dict:actual[key]=dict(value,invented=True)
            elif type(value) is str:actual[key]=value+'_TAMPER'
            elif type(value) is bool:actual[key]=not value
            else:actual[key]=value+1
            with self.subTest(key=key),self.assertRaises(ValueError):self.check(e=e)

    def test_parent_contacts_residuals_partition_and_priority_choice_are_bound(self):
        changes=(lambda e:e['original_followplan_proposal']['evidence']['row_scores'][0]['first_cohort_future'][0]['continuation']['plan'][0].__setitem__('offset_seconds',999),
            lambda e:e['row_scores'][0]['owner_scores'].pop(),lambda e:e['row_scores'][0]['member_metrics'].pop(),
            lambda e:e['selection'].__setitem__('selected_index',999),lambda e:e['row_scores'].pop())
        for i,edit in enumerate(changes):
            e=deepcopy(self.data[-1]);edit(e)
            with self.subTest(i=i),self.assertRaises(ValueError):self.check(e=e)

    def test_actual_outputs_are_separately_reconstructed(self):
        for edit in (lambda d:d[0]['send'].append('HIDDEN'),lambda d:d[0]['evidence']['cohort_stoploss'].__setitem__('selected_index',999),
                lambda d:d[0]['evidence'].__setitem__('planned_expected_cost',-1.)):
            d=deepcopy(self.data[-2]);edit(d)
            with self.assertRaises(ValueError):self.check(d=d)


class ParentDelegationAndTraceTests(unittest.TestCase):
    def test_parent_controls_delegate_to_frozen_Advance_chain(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y')
        proof=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=proof) as call:self.assertIs(audit.audit_replay(raw),proof)
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
            else:call['output']['evidence'].update(policy=audit.NAME,cohort_stoploss=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
            decision.update(hospital_ids=deepcopy(call['output']['send']),reason=call['output']['reason'],evidence=deepcopy(call['output']['evidence']))
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        result=audit.audit_replay(raw);self.assertTrue(result['passed']);self.assertEqual(result['new_executions'],0)
        for edit in (lambda r:r['branches'][0]['policy_calls'][0].__setitem__('db_decision_id',999),
                lambda r:r['branches'][0]['joint_groups'][0]['owner_source_snapshot']['rows'][0]['patient'].__setitem__('version',999)):
            value=deepcopy(raw);edit(value)
            with self.assertRaises(ValueError):audit.audit_replay(value)

    def test_import_has_no_producer_or_mutated_hook(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_stoploss_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        value=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(value.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
