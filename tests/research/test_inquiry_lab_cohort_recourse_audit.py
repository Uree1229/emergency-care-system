"""Synthetic hand recurrence, immutable-source and actual-output mutations."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_recourse_audit as audit
from test_inquiry_lab_cohort_hedge_audit import fixture as hedge_fixture,output as hedge_output,owner_fixture,replay as hedge_replay

ROOT=Path(__file__).resolve().parents[2]
B=audit.base


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
    """Audit-only valid artifact builder; hand tests below check the new math."""
    ev=original['evidence'];menu=deepcopy(ev['menu_indices']);origins=deepcopy(ev['owner_origins']);scores=[];selection=None
    evaluated=ev['evaluated'];base_index=ev['selected_index'];selected=base_index
    residual=[o for o in owners['inventory'] if o['patient']['id'] not in owners['first_cohort_patient_ids']]
    if evaluated:
        for row in ev['row_scores']:
            first=audit.first_cohort_score(row['first_cohort_score'],ms,p);discounts={}
            for job in first['jobs']:discounts[job['hospital']]=discounts.get(job['hospital'],0.)+job['discount_seconds']
            pending=row['first_cohort_score']['projection']['final_pending']
            os=[audit.owner_score(o,f,pending,discounts) for o,f in zip(residual,origins)]
            metrics=deepcopy(first['member_metrics'])+[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in os]
            scores.append(dict(row_index=row['row_index'],base_row_score=deepcopy(row),first_cohort=first,owner_scores=os,member_metrics=metrics,
                tier_probabilities={str(t):sum((x['metrics']['selection_probability'] for x in metrics if x['effective_priority']==t),0.) for t in (4,2,1)},
                sum_capped_wait_seconds=sum((x['metrics']['capped_wait_seconds'] for x in metrics),0.)))
        selection=audit.dynamic.base.global_staged_choice(scores,base_index);selection['base_selected_tied']=selection.pop('base_budget_tied');selected=selection['selected_index']
    intervened=selected!=base_index;decisions=deepcopy(original['decisions'])
    if intervened:
        table=audit.dynamic._nested_joint(ev['original_dynamic_proposal']['evidence']['original_cohort_success_proposal'])['evidence']
        chosen=next(r for r in scores if r['row_index']==selected);decisions=deepcopy(table['baseline_proposals'])
        for i,d in enumerate(decisions):
            sends=table['combinations'][selected]['hospitals'][i]
            d.update(send=list(sends),reason='cohort_recourse_current_only');d['evidence']['sent_hospitals']=list(sends)
            d['evidence']['planned_expected_cost']=round(chosen['member_metrics'][i]['metrics']['total_cost_seconds'],3)
            for row in d['evidence']['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in sends else None
    for i,d in enumerate(decisions):
        d['evidence']['policy']=audit.NAME
        d['evidence']['cohort_recourse']=dict(schema_version=1,mode='pair_cohort_recourse',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,selected_index=selected,effective_priority=p['patients'][i]['effective_priority'])
    evidence=dict(schema_version=1,rule=audit.RULE,base_policy=audit.BASE_POLICY,base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME,calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_hedge__'),
        joint_context=deepcopy(c),priority_context=deepcopy(p),owner_context=deepcopy(owners),original_hedge_proposal=deepcopy(original),
        evaluated=evaluated,menu_indices=menu,owner_origins=origins,row_scores=scores,selection=selection,
        base_selected_index=base_index,selected_index=selected,intervened=intervened,tiers=[4,2,1],
        tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=audit.OBJECTIVE,scope=audit.SCOPE)
    return decisions,evidence


def fixture():
    events,registry,c,ms,ts,p,owners,old=hedge_fixture()
    with audit.inherited_empty_scalars():d,e=hedge_output(ms,c,ts,p,owners,old)
    original=dict(decisions=d,evidence=e)
    native(events,registry,c,ms,ts,owners)
    d,e=output(ms,c,ts,p,owners,original)
    return events,registry,c,ms,ts,p,owners,d,e


def hand_job(index,patient,hospital,q0,base=0.,horizon=100.):
    return dict(job_index=index,patient_id=patient,hospital=hospital,pass0_wait_seconds=q0,
        base_residual_wait_seconds=base,full_mean_work_seconds=10.,assumed_workers=1,
        owner_horizon_seconds=horizon,reply_probability=1.,accept_probability=1.,service_mu=math.log(10.),service_sigma=1.)


class ExpectedWorkHandTests(unittest.TestCase):
    def test_forward_q1_precedes_probe_and_uses_fixed_F0_without_feedback(self):
        jobs=[hand_job(0,'A','FAST',0.),hand_job(1,'B','SLOW',0.),hand_job(2,'A','SLOW',10.),
            hand_job(3,'C','SLOW',20.),hand_job(4,'C','FAST',10.)];before=deepcopy(jobs)
        rows=audit.expected_virtual_work(jobs)
        self.assertAlmostEqual(rows[2]['survival_probability'],.5,places=14)
        self.assertAlmostEqual(rows[2]['discount_seconds'],5.,places=14)
        self.assertAlmostEqual(rows[3]['pass1_wait_seconds'],15.,places=14)
        cdf5=.5*(1+math.erf(math.log(.5)/math.sqrt(2)))
        self.assertAlmostEqual(rows[3]['survival_probability'],1-cdf5,places=14)
        # Probing the old q0 would produce .5; probing an updated F1 would
        # read a later forward result. Both violate the one-pass contract.
        self.assertGreater(rows[3]['survival_probability'],.5)
        self.assertEqual(rows[4]['other_hospital_probabilities'][0]['selection_probability_at_probe'],0.)
        self.assertEqual(jobs,before)

    def test_own_hospital_excluded_H_clamps_probe_but_does_not_erase_work(self):
        jobs=[hand_job(0,'A','FAST',0.,horizon=2.),hand_job(1,'B','SLOW',0.),hand_job(2,'A','SLOW',10.,horizon=2.)]
        rows=audit.expected_virtual_work(jobs);last=rows[-1]
        self.assertEqual(last['cancellation_probe_seconds'],2.)
        self.assertEqual([p['job_index'] for p in last['other_hospital_probabilities']],[0])
        self.assertGreater(last['expected_work_seconds'],0.)
        self.assertEqual(rows[0]['survival_probability'],1.)
        same=[hand_job(0,'A','X',0.),hand_job(1,'A','X',10.)]
        self.assertEqual(audit.expected_virtual_work(same)[1]['other_hospital_probabilities'],[])

    def test_actual_pending_lower_bound_integer_workers_and_source_order(self):
        jobs=[hand_job(0,'A','FAST',0.),hand_job(1,'B','SLOW',100.,base=100.),hand_job(2,'A','SLOW',110.,base=100.)]
        rows=audit.expected_virtual_work(jobs)
        self.assertGreaterEqual(rows[-1]['pass1_wait_seconds'],100.)
        for change in (lambda j:j[0].__setitem__('assumed_workers',.5),
                lambda j:j[1].__setitem__('base_residual_wait_seconds',101.),lambda j:j.reverse()):
            forged=deepcopy(jobs);change(forged)
            with self.assertRaises(ValueError):audit.expected_virtual_work(forged)

    def test_u1_matches_unchanged_first_metric_and_no_metric_float_is_trusted(self):
        _,_,c,ms,ts,p,owners,d,e=fixture();row=e['row_scores'][0];first=row['first_cohort']
        altered=deepcopy(row['base_row_score']['first_cohort_score'])
        altered['member_metrics'][0]['metrics']['selection_probability']+=1e-11
        rebuilt=audit.first_cohort_score(altered,ms,p)
        self.assertEqual(rebuilt,first)
        # Two owners with identical queues at every selected hospital have
        # F0_other(q1)==0, so all u=1 and old uncancelled metrics must agree.
        pair=deepcopy(ms);sends=[['H1','H2'],['H1','H2']];appends=[]
        for m in pair:
            m['inputs'].update(observations=[],pending=[],patient_pending=[],last_contact_at=None,
                candidates=[dict(hospital_id='H1',travel={'duration_seconds':100},resources_enough=True),
                    dict(hospital_id='H2',travel={'duration_seconds':110},resources_enough=True)])
            for hid in sends[0]:appends.append(dict(inquiry=dict(hospital=hid,patient=m['patient_id'],submitted_at=m['inputs']['now']),source={}))
        same=dict(row_index=0,projection=dict(initial_pending=[],current_pair_send=sends,
            initial_appended_inquiries=appends,steps=[],singleton=None,final_pending=[x['inquiry'] for x in appends]))
        scored=audit.first_cohort_score(same,pair,p)
        self.assertTrue(all(j['survival_probability']==1. and j['discount_seconds']==0. for j in scored['jobs']))
        old=audit.joint.combination([B.public_model(m['inputs']) for m in pair],sends)['member_metrics']
        audit.precise_same([x['metrics'] for x in scored['member_metrics']],old,'u1 unchanged independent old metrics')


class IndependentRecoursePairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=fixture()

    def check(self,d=None,e=None,owners=None):
        _,_,c,ms,ts,p,o,decisions,evidence=self.data
        return audit.audit_pair(ms,c,ts,decisions if d is None else d,evidence if e is None else e,p,o if owners is None else owners)

    def test_complete_valid_parent_proposal_and_new_rows_leave_inputs_unchanged(self):
        before=deepcopy(self.data);r=self.check();self.assertEqual(self.data,before)
        self.assertGreater(r['recourse_virtual_jobs_checked'],0);self.assertEqual(r['recourse_rows_checked'],len(self.data[-1]['menu_indices']))

    def test_F0_belief_own_hospital_discount_source_and_current_metric_mutations_rejected(self):
        changes=[lambda e:e['row_scores'][0]['first_cohort']['jobs'][0].__setitem__('reply_probability',.01),
            lambda e:e['row_scores'][0]['first_cohort']['jobs'][0].__setitem__('pass0_wait_seconds',999.),
            lambda e:e['row_scores'][0]['first_cohort']['jobs'][0].__setitem__('survival_probability',.5),
            lambda e:e['row_scores'][0]['first_cohort']['jobs'][0]['source']['source'].__setitem__('proposal_path','forged'),
            lambda e:e['row_scores'][0]['first_cohort']['jobs'].reverse(),
            lambda e:e['row_scores'][0]['first_cohort']['member_metrics'][0]['metrics'].__setitem__('selection_probability',
                e['row_scores'][0]['first_cohort']['member_metrics'][0]['metrics']['selection_probability']+2e-13)]
        for i,change in enumerate(changes):
            e=deepcopy(self.data[-1]);change(e)
            with self.subTest(i=i),self.assertRaises(ValueError):self.check(e=e)

    def test_original_H_counter_proposal_and_complete_owner_partition_remain_bound(self):
        mutations=[lambda e:e['original_hedge_proposal']['evidence']['original_dynamic_proposal']['evidence']['row_scores'][0]['projection']['steps'][0]['proposal']['decisions'][0]['send'].append('HIDDEN'),
            lambda e:e['owner_origins'][0]['own_items'].append({'hospital':'invented'}),
            lambda e:e['row_scores'][0]['base_row_score']['first_cohort_score']['projection']['final_pending'].pop(),
            lambda e:e['row_scores'][0]['owner_scores'].pop(),lambda e:e['row_scores'][0]['member_metrics'].pop()]
        for i,change in enumerate(mutations):
            e=deepcopy(self.data[-1]);change(e)
            with self.subTest(i=i),self.assertRaises(ValueError):self.check(e=e)

    def test_owner_discount_plan_occupancy_contact_survival_and_selection_mutations_rejected(self):
        for i,change in enumerate((lambda e:e['row_scores'][0]['owner_scores'][0]['queue_records'][0].__setitem__('virtual_discount_seconds',500.),
                lambda e:e['row_scores'][0]['owner_scores'][0]['occupancy'][0].__setitem__('total_fresh_count',.5),
                lambda e:e['row_scores'][0]['owner_scores'][0]['metrics'].__setitem__('planned_contact_cost_seconds',-1.),
                lambda e:e['selection'].__setitem__('selected_index',999))):
            e=deepcopy(self.data[-1]);change(e)
            with self.subTest(i=i),self.assertRaises(ValueError):self.check(e=e)

    def test_actual_outputs_are_reconstructed_separately_from_intact_proposal(self):
        for change in (lambda d:d[0]['send'].append('HIDDEN'),lambda d:d[0]['evidence']['cohort_recourse'].__setitem__('selected_index',999),
                lambda d:d[0]['evidence'].__setitem__('planned_expected_cost',-1.)):
            d=deepcopy(self.data[-2]);change(d)
            with self.assertRaises(ValueError):self.check(d=d)

    def test_empty_product_parent_correction_and_hook_restoration_are_preserved(self):
        old=B.owner_metrics;self.check();self.assertIs(B.owner_metrics,old)
        forged=deepcopy(self.data[-1]);forged['original_hedge_proposal']['evidence']['selected_index']=999
        with self.assertRaises(ValueError):self.check(e=forged)
        self.assertIs(B.owner_metrics,old)

    def test_comparison_labels_preserve_existing_probability_and_cost_domains(self):
        # The sealed parent's comparison dispatches by the nested field name:
        # P has 1e-13, costs have 1e-10. An ancestor label containing the word
        # 'probabilities' incorrectly applies the P domain to every cost.
        # This regression restores that existing contract; no EPS or math is
        # changed and the freshly rebuilt values still determine selection.
        e=deepcopy(self.data[-1]);m=e['row_scores'][0]['owner_scores'][0]['metrics']
        m['arrival_failure_cost_seconds']=math.nextafter(m['arrival_failure_cost_seconds'],math.inf)
        self.check(e=e)
        for key,delta in [('selection_probability',2e-13),('arrival_failure_cost_seconds',2e-10)]:
            forged=deepcopy(self.data[-1]);metric=forged['row_scores'][0]['owner_scores'][0]['metrics'];metric[key]+=delta
            with self.subTest(field=key),self.assertRaises(ValueError):self.check(e=forged)


class OwnerBoundaryAndDelegationTests(unittest.TestCase):
    def test_owner_original_CDF_partial_cells_arbitrary_barrier_and_no_fractional_cap(self):
        owner=owner_fixture(remaining=17,age=181,candidates=1,delta=7);owner['job']['policy_name']=audit.NAME
        origin=B.freeze_owner(owner);before=deepcopy(origin)
        pending=deepcopy(owner['inputs']['pending'])+[dict(hospital='NEW0',patient='FIRST',submitted_at=1000)]
        first=audit.owner_score(owner,origin,pending,{'NEW0':20.});self.assertEqual(origin,before)
        self.assertEqual(first['allowed_offsets_seconds'],[7]);self.assertEqual(first['plan'],[dict(hospital='NEW0',offset_seconds=7)])
        record=first['queue_records'][0];newP=B.new_cdf(record,7,17);oldF=math.prod(1-sum(x['masses']) for x in origin['own_items'])
        self.assertAlmostEqual(first['metrics']['selection_probability'],1-oldF*(1-newP),places=14)
        self.assertTrue(all(type(r['total_fresh_count']) is int and r['total_fresh_count']<=4 for r in first['occupancy']))
        self.assertEqual(origin['cell_edges_seconds'],[5,10,15,17])
        with self.assertRaises(ValueError):audit.owner_score(owner,origin,pending,{'NEW0':10000.})

    def test_foreign_actual_Hedge_owner_does_not_become_supported_after_alias(self):
        owner=owner_fixture(candidates=1);owner['job']['policy_name']=audit.BASE_POLICY
        value={'inventory':[owner]};before=deepcopy(value)
        projected=audit.alias_owners(value);self.assertEqual(projected['inventory'][0]['job']['policy_name'],'__foreign_cohort_hedge__')
        self.assertEqual(audit.owner_score(owner,B.freeze_owner(owner),owner['inputs']['pending'],{})['planning_status'],'unsupported_policy')
        self.assertEqual(value,before)

    def test_all_thirteen_parent_controls_delegate_to_numeric_history_chain(self):
        raw={'schema_version':1,'arms':[['old','immediate']],'branches':[{'policy':'old','rule':'immediate','environment_sha256':'x','snapshot_sha256':'y'}],
            'environment_sha256':'x','initial_snapshot_sha256':'y'};proof={'passed':True,'new_executions':0,'native_parent':True}
        with patch.object(audit.numeric,'audit_replay',return_value=proof) as called:
            self.assertIs(audit.audit_replay(raw),proof)
        called.assert_called_once_with(raw)

    def test_same_policy_immediate_and_hold_control_proofs_cannot_overwrite_each_other(self):
        controls=[dict(policy='eat_l15_f',rule=rule,environment_sha256='x',snapshot_sha256='y') for rule in ('immediate','hold120')]
        candidate=dict(policy=audit.NAME,rule='immediate',environment_sha256='x',snapshot_sha256='y')
        raw=dict(schema_version=1,scenario={},arms=[[b['policy'],b['rule']] for b in controls+[candidate]],
            branches=controls+[candidate],environment_sha256='x',initial_snapshot_sha256='y')
        proofs=[{'kind':'immediate proof'},{'kind':'hold120 proof'}]
        with patch.object(audit.numeric,'audit_replay',return_value=dict(passed=True,branch_audits=proofs)), \
                patch.object(audit,'audit_branch',return_value={'kind':'candidate proof'}):r=audit.audit_replay(raw)
        self.assertEqual([p['kind'] for p in r['branch_audits']],['immediate proof','hold120 proof','candidate proof'])

    def test_complete_synthetic_DB_calls_sends_sources_and_FIFO_are_checked(self):
        raw=hedge_replay();branch=raw['branches'][0];g=branch['joint_groups'][0]
        # The old minimal trace fixture omitted unused coordinates. The sealed
        # history supplement requires the real public first-origin schema.
        for patient in raw['scenario']['patients']:patient.update(lat=35.,lon=127.)
        environment=hashlib.sha256(json.dumps(raw['scenario'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        raw['environment_sha256']=branch['environment_sha256']=environment
        for row in g['owner_source_snapshot']['rows']:row['patient'].update(lat=35.,lon=127.)
        original=dict(decisions=deepcopy(g['outputs']),evidence=deepcopy(g['evidence']))
        native(branch['db_events'],g['registry_snapshot'],g['context'],g['members'],g['tail_members'],g['owner_context'],g['owner_source_snapshot'])
        g['outputs'],g['evidence']=output(g['members'],g['context'],g['tail_members'],g['priority_context'],g['owner_context'],original)
        self.assertEqual([x['send'] for x in g['outputs']],[x['send'] for x in original['decisions']])
        for i,(call,decision) in enumerate(zip(branch['policy_calls'],branch['db_decisions'])):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            if i<2:call['output']=deepcopy(g['outputs'][i])
            else:call['output']['evidence'].update(policy=audit.NAME,cohort_recourse=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
            decision.update(hospital_ids=deepcopy(call['output']['send']),reason=call['output']['reason'],evidence=deepcopy(call['output']['evidence']))
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        result=audit.audit_replay(raw)
        self.assertTrue(result['passed']);self.assertEqual(result['new_executions'],0)
        self.assertEqual(result['branch_audits'][0]['patients'],len(raw['scenario']['patients']))
        for change in (lambda r:r['branches'][0]['joint_groups'][0]['owner_source_snapshot']['rows'][0]['patient'].__setitem__('version',99),
                lambda r:r['branches'][0]['policy_calls'][0].__setitem__('db_decision_id',999),
                lambda r:r['branches'][0]['db_events'][-1].__setitem__('at',audit.EPOCH-1)):
            forged=deepcopy(raw);change(forged)
            with self.assertRaises(ValueError):audit.audit_replay(forged)

    def test_import_has_no_producer_model_or_runner_and_installs_no_hook(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_recourse_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        r=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(r.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
