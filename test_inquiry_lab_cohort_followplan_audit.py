"""Independent first-continuation math, public source, and trace mutations."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_followplan_audit as audit
from test_inquiry_lab_cohort_recourse_audit import fixture as old_fixture,output as recourse_output,native as recourse_native
from test_inquiry_lab_cohort_hedge_audit import owner_fixture,replay as hedge_replay

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
    """Construct saved synthetic evidence solely with independent equations."""
    ev=original['evidence'];menu=deepcopy(ev['menu_indices']);scores=[];selection=None
    evaluated=ev['evaluated'];base_index=ev['selected_index'];selected=base_index
    hedge=ev['original_hedge_proposal'];dynamic=hedge['evidence']['original_dynamic_proposal']
    table=audit.dynamic._nested_joint(dynamic['evidence']['original_cohort_success_proposal'])['evidence']
    by_owner={audit.base.owner_key(o['patient']['id']):o for o in hedge['evidence']['owner_context']['inventory']}
    if evaluated:
        for row in ev['row_scores']:
            first=row['base_row_score']['first_cohort_score'];initial=row['first_cohort']
            reviews=audit.first_review_sources(first,ms,table['baseline_proposals']);plans=[]
            for review in reviews:
                pid=review['patient_id'];jobs=[j for j in initial['jobs'] if audit.base.owner_key(j['patient_id'])==audit.base.owner_key(pid)]
                plans.append(audit.first_patient_score(by_owner[audit.base.owner_key(pid)],jobs,
                    first['projection']['final_pending'],initial['jobs'],review['next_review_at']))
            residual=deepcopy(row['owner_scores'])
            metrics=[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in plans+residual]
            scores.append(dict(row_index=row['row_index'],base_row_score=deepcopy(row),first_review_sources=reviews,
                first_cohort_future=plans,owner_scores=residual,member_metrics=metrics,
                tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.0) for t in (4,2,1)},
                sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.0)))
        selection=audit.dynamic.base.global_staged_choice(scores,base_index)
        selection['base_selected_tied']=selection.pop('base_budget_tied');selected=selection['selected_index']
    intervened=selected!=base_index;decisions=deepcopy(original['decisions'])
    if intervened:
        chosen=next(r for r in scores if r['row_index']==selected);decisions=deepcopy(table['baseline_proposals'])
        for i,d in enumerate(decisions):
            sends=table['combinations'][selected]['hospitals'][i]
            d.update(send=list(sends),reason='cohort_followplan_current_only');d['evidence']['sent_hospitals']=list(sends)
            d['evidence']['planned_expected_cost']=round(chosen['member_metrics'][i]['metrics']['total_cost_seconds'],3)
            for item in d['evidence']['candidate_plan']:item['planned_offset_seconds']=0 if item['hospital'] in sends else None
    for i,d in enumerate(decisions):
        d['evidence']['policy']=audit.NAME
        d['evidence']['cohort_followplan']=dict(schema_version=1,mode='pair_cohort_followplan',evaluated=evaluated,
            intervened=intervened,member_index=i,base_selected_index=base_index,selected_index=selected,
            effective_priority=p['patients'][i]['effective_priority'])
    evidence=dict(schema_version=1,rule=audit.RULE,base_policy=audit.BASE_POLICY,base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME,calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name','owner_context.inventory[].job.policy_name'],
            copied_only=True,public_identity_unchanged=True,foreign_base_owner_label='__foreign_cohort_recourse__'),
        joint_context=deepcopy(c),priority_context=deepcopy(p),owner_context=deepcopy(owners),original_recourse_proposal=deepcopy(original),
        evaluated=evaluated,menu_indices=menu,row_scores=scores,selection=selection,base_selected_index=base_index,
        selected_index=selected,intervened=intervened,tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),
        objective=audit.OBJECTIVE,scope=audit.SCOPE)
    return decisions,evidence


def fixture():
    events,registry,c,ms,ts,p,owners,d,e=old_fixture();original=dict(decisions=deepcopy(d),evidence=deepcopy(e))
    native(events,registry,c,ms,ts,owners)
    d,e=output(ms,c,ts,p,owners,original)
    return events,registry,c,ms,ts,p,owners,d,e


def hand(remaining=300,count=1,candidates=3,probability=.6,wait=10.):
    owner=owner_fixture(remaining=remaining,count=0,candidates=candidates);now=owner['inputs']['now']
    jobs=[]
    for i in range(count):
        jobs.append(dict(job_index=i,patient_id=owner['patient']['id'],hospital='NEW'+str(i),
            source=dict(inquiry=dict(hospital='NEW'+str(i),patient=owner['patient']['id'],submitted_at=now),source={}),
            cell_edges_seconds=[r for _,r in audit.horizon.intervals(remaining)],owner_horizon_seconds=remaining,
            travel_seconds=100+10*i,service_mu=math.log(10.),service_sigma=1.,reply_probability=1.,
            accept_probability=probability,pass1_wait_seconds=wait,discount_seconds=0.,inquiry_cost_seconds=15.))
    pending=owner['inputs']['pending']+[j['source']['inquiry'] for j in jobs]
    return owner,jobs,pending


class FirstContinuationHandTests(unittest.TestCase):
    def test_initial_CDF_exact_shift_partial_H_and_fixed_immediate_cost(self):
        owner,jobs,pending=hand(remaining=17,count=1,candidates=1,probability=.6,wait=7.)
        before=deepcopy((owner,jobs,pending));result=audit.first_patient_score(owner,jobs,pending,jobs,1007)
        expected=.6*.5 # lognormal median10 at H17 minus q1=7
        self.assertAlmostEqual(result['metrics']['selection_probability'],expected,places=14)
        self.assertEqual(result['source_origin']['cell_edges_seconds'],[5,10,15,17])
        self.assertEqual(result['hypothetical_origin']['own_items'][0]['wait_seconds'],7.)
        self.assertEqual(result['continuation']['planning_status'],'no_candidates')
        self.assertEqual(result['metrics']['immediate_contact_cost_seconds'],15.)
        self.assertAlmostEqual(result['metrics']['total_cost_seconds'],result['continuation']['metrics']['total_cost_seconds']+15.)
        self.assertEqual((owner,jobs,pending),before)

    def test_four_initial_contacts_preserve_integer_occupancy_until_after_timeout(self):
        owner,jobs,pending=hand(count=4,candidates=10,probability=0.,wait=0.)
        result=audit.first_patient_score(owner,jobs,pending,jobs,1015)
        plan=result['continuation'];sent=[r for r in plan['plan'] if r['offset_seconds'] is not None]
        self.assertTrue(sent)
        self.assertTrue(all(r['offset_seconds']>=195 for r in sent))
        self.assertEqual([r['hospital'] for r in plan['plan']],['NEW4','NEW5','NEW6','NEW7','NEW8','NEW9'])
        self.assertTrue(all(type(r['total_fresh_count']) is int and r['total_fresh_count']<=4 for r in plan['occupancy']))
        self.assertEqual(len(result['hypothetical_origin']['original_all_own']),4)
        self.assertEqual(result['hypothetical_owner']['inputs']['max_new'],6)

    def test_zero_initial_contacts_ignore_nextreview_like_actual_empty_pending_capture(self):
        owner,jobs,pending=hand(count=0,candidates=1)
        result=audit.first_patient_score(owner,jobs,pending,jobs,1073)
        self.assertEqual(result['continuation']['barrier_offset_seconds'],0)
        self.assertEqual(result['metrics']['immediate_contact_cost_seconds'],0.)
        self.assertEqual(result['hypothetical_owner']['inputs']['last_contact_at'],owner['inputs']['last_contact_at'])

    def test_public_contact_window_strictly_precedes_A_and_H(self):
        owner,jobs,pending=hand(remaining=17,count=1,candidates=2)
        result=audit.first_patient_score(owner,jobs,pending,jobs,1017)
        self.assertEqual(result['continuation']['planning_status'],'barrier_outside_new_contact_window')
        self.assertEqual(result['continuation']['allowed_offsets_seconds'],[])
        self.assertTrue(all(r['offset_seconds'] is None for r in result['continuation']['plan']))
        self.assertEqual(result['hypothetical_owner']['job']['version'],owner['job']['version'])
        self.assertEqual(result['hypothetical_owner']['inquiries'],[])

    def test_hidden_or_omitted_initial_source_and_existing_inquiry_rejected(self):
        for change in (lambda o,j,p:j[0]['source']['inquiry'].__setitem__('submitted_at',999),
                lambda o,j,p:o['inputs']['patient_pending'].append({'hospital':'HIDDEN'}),
                lambda o,j,p:j[0].__setitem__('owner_horizon_seconds',301)):
            owner,jobs,pending=hand();change(owner,jobs,pending)
            with self.assertRaises(ValueError):audit.first_patient_score(owner,jobs,pending,jobs,1015)
        owner,jobs,pending=hand()
        with self.assertRaises(ValueError):audit.first_patient_score(owner,[],pending,jobs,1015)


class IndependentFirstPlanPairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=fixture()

    def check(self,d=None,e=None):
        _,_,c,ms,ts,p,o,decisions,evidence=self.data
        return audit.audit_pair(ms,c,ts,decisions if d is None else d,evidence if e is None else e,p,o)

    def test_complete_original_proposal_residuals_and_new_first_plans(self):
        before=deepcopy(self.data);result=self.check()
        self.assertEqual(self.data,before)
        self.assertEqual(result['followplan_rows_checked'],len(self.data[-1]['menu_indices']))
        self.assertGreater(result['followplan_first_patient_plans_checked'],0)
        for row in self.data[-1]['row_scores']:
            self.assertEqual(row['owner_scores'],row['base_row_score']['owner_scores'])

    def test_hypothetical_source_CDF_contact_budget_and_public_clock_mutations_rejected(self):
        changes=(lambda r:r['source_owner']['patient'].__setitem__('version',999),
            lambda r:r['hypothetical_owner']['job'].__setitem__('version',999),
            lambda r:r['hypothetical_owner']['inputs'].__setitem__('max_new',999),
            lambda r:r['hypothetical_origin']['own_items'][0].__setitem__('wait_seconds',999.),
            lambda r:r['hypothetical_origin']['original_pending'].append({'hospital':'HIDDEN'}),
            lambda r:r.__setitem__('next_review_at',r['next_review_at']+1),
            lambda r:r.__setitem__('initial_contact_cost_seconds',0.),
            lambda r:r['continuation']['metrics'].__setitem__('selection_probability',0.))
        for i,change in enumerate(changes):
            evidence=deepcopy(self.data[-1]);change(evidence['row_scores'][0]['first_cohort_future'][0])
            with self.subTest(i=i),self.assertRaises(ValueError):self.check(e=evidence)

    def test_preserved_model_review_sources_residual_partition_and_choice_tampering_rejected(self):
        changes=(lambda e:e['original_recourse_proposal']['evidence']['row_scores'][0]['first_cohort']['jobs'][0].__setitem__('pass1_wait_seconds',999.),
            lambda e:e['row_scores'][0]['first_review_sources'][0].__setitem__('next_review_at',999),
            lambda e:e['row_scores'][0]['owner_scores'].pop(),
            lambda e:e['row_scores'][0]['member_metrics'].pop(),
            lambda e:e['selection'].__setitem__('selected_index',999))
        for i,change in enumerate(changes):
            evidence=deepcopy(self.data[-1]);change(evidence)
            with self.subTest(i=i),self.assertRaises(ValueError):self.check(e=evidence)

    def test_actual_output_reconstruction_is_separate_from_score_claims(self):
        for change in (lambda d:d[0]['send'].append('HIDDEN'),
                lambda d:d[0]['evidence']['cohort_followplan'].__setitem__('selected_index',999),
                lambda d:d[0]['evidence'].__setitem__('planned_expected_cost',-1.)):
            decisions=deepcopy(self.data[-2]);change(decisions)
            with self.assertRaises(ValueError):self.check(d=decisions)


class ParentDelegationAndImportTests(unittest.TestCase):
    def test_all_parent_controls_delegate_to_frozen_Recourse_chain(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],
            environment_sha256='x',initial_snapshot_sha256='y')
        proof=dict(passed=True,new_executions=0,native_parent=True)
        with patch.object(audit.recourse,'audit_replay',return_value=proof) as call:self.assertIs(audit.audit_replay(raw),proof)
        call.assert_called_once_with(raw)

    def test_duplicate_policy_different_rules_keep_separate_control_proofs(self):
        controls=[dict(policy='eat_l15_f',rule=r,environment_sha256='x',snapshot_sha256='y') for r in ('immediate','hold120')]
        candidate=dict(policy=audit.NAME,rule='immediate',environment_sha256='x',snapshot_sha256='y')
        raw=dict(schema_version=1,scenario={},arms=[[b['policy'],b['rule']] for b in controls+[candidate]],branches=controls+[candidate],
            environment_sha256='x',initial_snapshot_sha256='y')
        with patch.object(audit.recourse,'audit_replay',return_value=dict(passed=True,branch_audits=[{'kind':'immediate'},{'kind':'hold120'}])), \
                patch.object(audit,'audit_branch',return_value={'kind':'candidate'}):result=audit.audit_replay(raw)
        self.assertEqual([p['kind'] for p in result['branch_audits']],['immediate','hold120','candidate'])

    def test_import_has_no_producer_and_installs_no_arithmetic_hook(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_followplan_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'[]')

    def test_complete_saved_DB_calls_source_capture_sends_and_FIFO(self):
        raw=hedge_replay();branch=raw['branches'][0];group=branch['joint_groups'][0]
        for patient in raw['scenario']['patients']:patient.update(lat=35.,lon=127.)
        environment=hashlib.sha256(json.dumps(raw['scenario'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        raw['environment_sha256']=branch['environment_sha256']=environment
        for row in group['owner_source_snapshot']['rows']:row['patient'].update(lat=35.,lon=127.)
        original=dict(decisions=deepcopy(group['outputs']),evidence=deepcopy(group['evidence']))
        recourse_native(branch['db_events'],group['registry_snapshot'],group['context'],group['members'],group['tail_members'],
            group['owner_context'],group['owner_source_snapshot'])
        group['outputs'],group['evidence']=recourse_output(group['members'],group['context'],group['tail_members'],
            group['priority_context'],group['owner_context'],original)
        for i,(call,decision) in enumerate(zip(branch['policy_calls'],branch['db_decisions'])):
            call['name']=decision['policy_name']=audit.BASE_POLICY
            call['workflow_snapshot']['job']['policy_name']=audit.BASE_POLICY
            if i<2:call['output']=deepcopy(group['outputs'][i])
            else:call['output']['evidence'].update(policy=audit.BASE_POLICY,
                cohort_recourse=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
            decision.update(hospital_ids=deepcopy(call['output']['send']),reason=call['output']['reason'],evidence=deepcopy(call['output']['evidence']))
        branch.update(policy=audit.BASE_POLICY,instrumentation=deepcopy(audit.recourse.NEW_INSTRUMENTATION))
        raw['arms']=[[audit.BASE_POLICY,'immediate']]
        self.assertTrue(audit.recourse.audit_replay(raw)['passed'])
        original=dict(decisions=deepcopy(group['outputs']),evidence=deepcopy(group['evidence']))
        native(branch['db_events'],group['registry_snapshot'],group['context'],group['members'],group['tail_members'],
            group['owner_context'],group['owner_source_snapshot'])
        group['outputs'],group['evidence']=output(group['members'],group['context'],group['tail_members'],
            group['priority_context'],group['owner_context'],original)
        self.assertEqual([d['send'] for d in group['outputs']],[d['send'] for d in original['decisions']])
        for i,(call,decision) in enumerate(zip(branch['policy_calls'],branch['db_decisions'])):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            if i<2:call['output']=deepcopy(group['outputs'][i])
            else:call['output']['evidence'].update(policy=audit.NAME,
                cohort_followplan=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
            decision.update(hospital_ids=deepcopy(call['output']['send']),reason=call['output']['reason'],evidence=deepcopy(call['output']['evidence']))
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        result=audit.audit_replay(raw)
        self.assertTrue(result['passed']);self.assertEqual(result['new_executions'],0)
        self.assertEqual(result['branch_audits'][0]['patients'],len(raw['scenario']['patients']))
        for change in (lambda v:v['branches'][0]['joint_groups'][0]['owner_source_snapshot']['rows'][0]['patient'].__setitem__('version',999),
                lambda v:v['branches'][0]['policy_calls'][0].__setitem__('db_decision_id',999),
                lambda v:v['branches'][0]['db_events'][-1].__setitem__('at',audit.EPOCH-1)):
            forged=deepcopy(raw);change(forged)
            with self.assertRaises(ValueError):audit.audit_replay(forged)


if __name__=='__main__':unittest.main()
