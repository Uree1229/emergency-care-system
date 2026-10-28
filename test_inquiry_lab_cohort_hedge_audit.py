"""Synthetic owner-origin CDF, arbitrary-time search and full-source mutations."""
from collections import Counter
from copy import deepcopy
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_hedge_audit as audit
from test_inquiry_lab_cohort_dynamic_audit import fixture as dynamic_fixture, output as dynamic_output, replay as dynamic_replay

ROOT=Path(__file__).resolve().parent


def owner_fixture(remaining=300,age=0,count=1,candidates=2,delta=7):
    now=1000;birth=now+remaining-300;pid='OWNER'
    pending=[dict(hospital='OLD'+str(i),patient=pid,submitted_at=now-age) for i in range(count)]
    offers=[dict(hospital_id='NEW'+str(i),travel=dict(duration_seconds=100+10*i),resources_enough=True) for i in range(candidates)]
    return dict(patient=dict(id=pid,state='WAITING',version=1,reported_priority=None,priority_source='unknown',effective_priority=4),
        patient_created_event=dict(id=1,at=birth,type='patient_created',patient_id=pid),evaluation_end_at=now+remaining,
        job=dict(patient_id=pid,version=1,started_at=birth,deadline_at=now+remaining,next_evaluation_at=now+delta,enabled=1,policy_name=audit.NAME),
        inquiries=[dict(id=i+1,patient_id=pid,hospital_id=q['hospital'],state='PENDING',last_sent_at=q['submitted_at']) for i,q in enumerate(pending)],
        valid_acceptance_ids=[],inputs=dict(now=now,candidates=offers,observations=[],pending=deepcopy(pending),
            patient_pending=[dict(q,travel_seconds=90) for q in pending],max_new=10-count,last_contact_at=now-age if count else None),
        apply_snapshot=dict(patient_id=pid,patient_version=1,job_version=1,revision=0,now=now))


def native(events,registry,context,members,tails):
    for event in events:
        if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
    for job in registry['jobs']:job['policy_name']=audit.NAME
    for row in context['eligible']:row['job']['policy_name']=audit.NAME
    for m in members+tails:m['workflow_snapshot']['job']['policy_name']=audit.NAME


def inventory(context,members,tails,p,registry,events,extra=None):
    priority={audit.owner_key(r['patient_id']):r for r in p['patients']}
    births={audit.owner_key(e['patient_id']):{k:e[k] for k in ('id','at','type','patient_id')} for e in events if e['type']=='patient_created'}
    by_eligible={audit.owner_key(r['patient']['id']):r for r in context['eligible']};rows=[]
    for m in members+tails:
        pid=m['patient_id'];key=audit.owner_key(pid);person=by_eligible[key]['patient'];pr=priority[key]
        rows.append(dict(patient=dict(person,reported_priority=pr['reported_priority'],priority_source=pr['priority_source'],effective_priority=pr['effective_priority']),
            patient_created_event=deepcopy(births[key]),evaluation_end_at=births[key]['at']+300,
            job=deepcopy(by_eligible[key]['job']),inquiries=[],valid_acceptance_ids=[],
            inputs={k:deepcopy(v) for k,v in m['inputs'].items() if k!='time_context'},apply_snapshot=deepcopy(m['workflow_snapshot']['apply_snapshot'])))
    if extra is not None:rows.append(deepcopy(extra))
    rows.sort(key=lambda r:audit.owner_key(r['patient']['id']))
    return dict(schema_version=1,now=context['now'],event_prefix_max=context['event_prefix_max'],
        decision_prefix_max=context['decision_prefix_max'],revision=p['revision'],source=audit.OWNER_SOURCE,
        first_cohort_patient_ids=[m['patient_id'] for m in members+tails],patient_registry=deepcopy(registry['patients']),
        patient_created_events=sorted(births.values(),key=lambda e:e['id']),inventory=rows)


def fixture():
    events,registry,c,ms,ts,p,cs=dynamic_fixture()
    # A current registered owner with no inquiry and a prior no-send decision
    # can be outside first-contact eligibility. It must still be inventoried.
    # This pure policy fixture uses next_review>now as the existing D exclusion;
    # its H-derived owner probe correctly ignores that barrier with no pending.
    prefix=c['event_prefix_max']+2;now=c['now'];birth=events[0]['at']
    events += [dict(id=prefix-1,at=birth,type='patient_created',patient_id='R',data={}),
        dict(id=prefix,at=birth,type='inquiry_workflow_started',patient_id='R',data=dict(deadline_seconds=300,policy_name=audit.BASE_POLICY))]
    def prefix_update(value):
        if type(value) is dict:
            for k,v in value.items():
                if k=='event_prefix_max':value[k]=prefix
                else:prefix_update(v)
        elif type(value) is list:
            for v in value:prefix_update(v)
    for value in (c,ms,ts,cs,p):prefix_update(value)
    d,e=dynamic_output(ms,c,ts,p,cs);original=dict(decisions=d,evidence=e)
    native(events,registry,c,ms,ts)
    job=dict(registry['jobs'][0],patient_id='R',next_evaluation_at=now+7)
    registry['patients'].append(dict(id='R',state='WAITING',version=1));registry['jobs'].append(job)
    extra=dict(patient=dict(id='R',state='WAITING',version=1,reported_priority=None,priority_source='unknown',effective_priority=4),
        patient_created_event={k:events[-2][k] for k in ('id','at','type','patient_id')},evaluation_end_at=birth+300,
        job=deepcopy(job),inquiries=[],valid_acceptance_ids=[],inputs={k:deepcopy(v) for k,v in ms[0]['inputs'].items() if k!='time_context'},
        apply_snapshot=dict(patient_id='R',patient_version=1,job_version=1,revision=p['revision'],now=now))
    owners=inventory(c,ms,ts,p,registry,events,extra)
    return events,registry,c,ms,ts,p,owners,original


def output(members,context,tails,p,owners,original,isolated_score_index=None):
    evaluated=original['evidence']['evaluated'];base_index=original['evidence']['selected_index'];selected=base_index
    indices=deepcopy(original['evidence']['menu_indices']);origins=[];scores=[];selection=None
    if evaluated:
        first={audit.owner_key(pid) for pid in owners['first_cohort_patient_ids']}
        residual=[o for o in owners['inventory'] if audit.owner_key(o['patient']['id']) not in first]
        origins=[audit.freeze_owner(o) for o in residual]
        for original_row in original['evidence']['row_scores']:
            owner_scores=[audit.score_owner(o,f,original_row['projection']['final_pending']) for o,f in zip(residual,origins)]
            metrics=deepcopy(original_row['member_metrics'])+[dict(patient_id=s['patient_id'],effective_priority=s['effective_priority'],metrics=deepcopy(s['metrics'])) for s in owner_scores]
            scores.append(dict(row_index=original_row['row_index'],first_cohort_score=deepcopy(original_row),owner_scores=owner_scores,member_metrics=metrics,
                tier_probabilities={str(t):sum((m['metrics']['selection_probability'] for m in metrics if m['effective_priority']==t),0.) for t in (4,2,1)},
                sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in metrics),0.)))
        if isolated_score_index is not None:
            for row in scores:row['tier_probabilities']['4']=100. if row['row_index']==isolated_score_index else 0.
        selection=audit.base.base.global_staged_choice(scores,base_index);selection['base_selected_tied']=selection.pop('base_budget_tied');selected=selection['selected_index']
    intervened=selected!=base_index;d=deepcopy(original['decisions'])
    if intervened:
        joint=audit.base._nested_joint(original['evidence']['original_cohort_success_proposal'])['evidence'];row=joint['combinations'][selected]
        d=deepcopy(joint['baseline_proposals'])
        for i,decision in enumerate(d):
            send=row['hospitals'][i];decision.update(send=list(send),reason='cohort_hedge_current_only')
            decision['evidence']['sent_hospitals']=list(send);decision['evidence']['planned_expected_cost']=round(row['member_metrics'][i]['total_cost_seconds'],3)
            for r in decision['evidence']['candidate_plan']:r['planned_offset_seconds']=0 if r['hospital'] in send else None
    weights={audit.owner_key(r['patient_id']):r['effective_priority'] for r in p['patients']}
    for i,decision in enumerate(d):
        decision['evidence']['policy']=audit.NAME
        decision['evidence']['cohort_hedge']=dict(schema_version=1,mode='pair_cohort_hedge',evaluated=evaluated,intervened=intervened,
            member_index=i,base_selected_index=base_index,selected_index=selected,effective_priority=weights[audit.owner_key(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=audit.RULE,base_policy=audit.BASE_POLICY,base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME,calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name','tail_members[].workflow_snapshot.job.policy_name'],
            copied_only=True,public_identity_unchanged=True),joint_context=deepcopy(context),priority_context=deepcopy(p),owner_context=deepcopy(owners),
        original_dynamic_proposal=deepcopy(original),evaluated=evaluated,menu_indices=indices,owner_origins=origins,row_scores=scores,selection=selection,
        base_selected_index=base_index,selected_index=selected,intervened=intervened,tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=audit.OBJECTIVE,scope=audit.SCOPE)
    return d,evidence


def source_snapshot(owners,events):
    # Complete-shape synthetic rows for raw/projection mutations. These values
    # are not represented as a real DB execution or stale-guard proof.
    rows=[]
    for owner in owners['inventory']:
        p=owner['patient'];patient={k:None for k in audit.FULL_PATIENT_KEYS}
        patient.update(id=p['id'],state=p['state'],version=p['version'],priority=p['reported_priority'],priority_source=p['priority_source'],requested_at=owner['patient_created_event']['at'])
        job=None if owner['job'] is None else dict({k:None for k in audit.FULL_JOB_KEYS},**owner['job'])
        inquiries=[]
        for q in owner['inquiries']:
            full={k:None for k in audit.FULL_INQUIRY_KEYS};full.update(q,version=1);inquiries.append(full)
        rows.append(dict(patient_id=p['id'],patient=patient,job=job,inquiries=inquiries))
    return dict(schema_version=1,**{k:owners[k] for k in ('now','event_prefix_max','decision_prefix_max','revision')},rows=rows,
        events=[deepcopy(e) for e in events if e['id']<=owners['event_prefix_max']])


def replay():
    raw=dynamic_replay();b=raw['branches'][0];g=b['joint_groups'][0]
    original=dict(decisions=deepcopy(g['outputs']),evidence=deepcopy(g['evidence']))
    native(b['db_events'],g['registry_snapshot'],g['context'],g['members'],g['tail_members'])
    g['owner_context']=inventory(g['context'],g['members'],g['tail_members'],g['priority_context'],g['registry_snapshot'],
        [e for e in b['db_events'] if e['id']<=g['event_prefix_max']])
    g['owner_source_snapshot']=source_snapshot(g['owner_context'],b['db_events'])
    g['outputs'],g['evidence']=output(g['members'],g['context'],g['tail_members'],g['priority_context'],g['owner_context'],original)
    for i,(call,decision) in enumerate(zip(b['policy_calls'],b['db_decisions'])):
        call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
        if i<2:call['output']=deepcopy(g['outputs'][i])
        else:call['output']['evidence'].update(policy=audit.NAME,cohort_hedge=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        decision.update(hospital_ids=deepcopy(call['output']['send']),reason=call['output']['reason'],evidence=deepcopy(call['output']['evidence']))
    b.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
    return raw


class IndependentOwnerMathTests(unittest.TestCase):
    def test_arbitrary_barrier_partial_cell_and_single_survival_weight(self):
        owner=owner_fixture(remaining=17,age=181,candidates=1,delta=7);f=audit.freeze_owner(owner)
        r=audit.score_owner(owner,f,owner['inputs']['pending']);self.assertEqual(r['allowed_offsets_seconds'],[7])
        self.assertEqual(r['plan'],[dict(hospital='NEW0',offset_seconds=7)]);self.assertTrue(r['forced'])
        record=r['queue_records'][0];new_p=audit.new_cdf(record,7,17)
        old_fail=math.prod(1-sum(i['masses']) for i in f['own_items'])
        expected=1-old_fail*(1-new_p)
        self.assertAlmostEqual(r['metrics']['selection_probability'],expected,places=14)
        old_at_barrier=math.prod(1-audit.descriptor_cdf(i,7) for i in f['own_items'])
        wrong=1-old_fail*(1-old_at_barrier*new_p)
        self.assertGreater(abs(expected-wrong),1e-15)
        self.assertAlmostEqual(r['metrics']['planned_contact_cost_seconds'],15*old_at_barrier,places=13)
        self.assertEqual(f['cell_edges_seconds'],[5,10,15,17])
        c=lambda t:audit.new_cdf(record,7,t)
        self.assertAlmostEqual(c(17)-c(15),record['reply_probability']*record['accept_probability']*(audit.parent._cdf(10,record['service_mu'],record['service_sigma'])-audit.parent._cdf(8,record['service_mu'],record['service_sigma'])),places=15)

    def test_later_same_second_virtual_job_never_changes_original_own_CDF(self):
        owner=owner_fixture(candidates=1,delta=0);f=audit.freeze_owner(owner);before=deepcopy(f)
        one=audit.score_owner(owner,f,owner['inputs']['pending'])
        row=deepcopy(owner['inputs']['pending'])+[dict(hospital='OLD0',patient='LATER',submitted_at=1000)]
        two=audit.score_owner(owner,f,row)
        self.assertEqual(f,before);self.assertEqual(one['metrics'],two['metrics'])
        self.assertEqual(f['own_items'][0]['mode'],'residual')

    def test_every_wave_counts_all_original_and_previous_planned_contacts_at_180_181(self):
        own=[dict(hospital='OLD'+str(i),patient='P',submitted_at=1000) for i in range(4)]
        r=audit.occupancy(dict(A=180,B=181,C=181),own,1000)
        self.assertEqual([x['total_fresh_count'] for x in r],[5,3])
        old=[dict(q,submitted_at=820) for q in own]
        self.assertEqual(audit.occupancy(dict(A=0),old,1000)[0]['total_fresh_count'],5)
        self.assertEqual(audit.occupancy(dict(A=1),old,1000)[0]['total_fresh_count'],1)
        owner=owner_fixture(count=4,candidates=6,delta=7);f=audit.freeze_owner(owner);r=audit.score_owner(owner,f,owner['inputs']['pending'])
        self.assertTrue(all(x['total_fresh_count']<=4 for x in r['occupancy']))
        self.assertTrue(all(x['offset_seconds'] is None or x['offset_seconds']>180 for x in r['plan']))

    def test_exact_contact_survival_includes_earlier_hedge_only_once(self):
        owner=owner_fixture(count=0,candidates=2);f=audit.freeze_owner(owner)
        records=[dict(hospital=h,reply_probability=.8,accept_probability=.5,estimated_wait_seconds=0,
            service_mu=math.log(10),service_sigma=.7,inquiry_cost_seconds=15) for h in ('A','B')]
        plan=dict(A=7,B=22);p=.4*audit.parent._cdf(15,math.log(10),.7)
        self.assertAlmostEqual(audit.analytic_contact_cost(f,records,plan),15+15*(1-p),places=14)
        self.assertNotAlmostEqual(audit.analytic_contact_cost(f,records,plan),30,places=8)

    def test_disabled_nojob_closed_unsupported_accepted_are_explicit_pending_only(self):
        owner=owner_fixture(count=1);f=audit.freeze_owner(owner)
        mutations=[('disabled_job',lambda o:o['job'].__setitem__('enabled',0)),('no_job',lambda o:o.__setitem__('job',None)),
            ('closed_new_contact_window',lambda o:o['job'].__setitem__('deadline_at',1000)),
            ('unsupported_policy',lambda o:o['job'].__setitem__('policy_name','manual')),
            ('contact_budget_exhausted',lambda o:o['inputs'].__setitem__('max_new',0))]
        baseline=audit.owner_metrics([(x['travel_seconds'],x['masses']) for x in f['own_items']],audit.horizon.intervals(300),300,0)
        for reason,mutate in mutations:
            other=deepcopy(owner);mutate(other);r=audit.score_owner(other,f,other['inputs']['pending'])
            with self.subTest(reason=reason):
                self.assertEqual(r['planning_status'],reason);self.assertEqual(r['metrics'],baseline)
                self.assertEqual(r['queue_records'],[]);self.assertEqual(r['passes'],0)
        accepted=deepcopy(owner);accepted['valid_acceptance_ids']=[1];r=audit.score_owner(accepted,f,accepted['inputs']['pending'])
        self.assertEqual(r['metrics']['selection_probability'],1);self.assertEqual(r['metrics']['capped_wait_seconds'],0)

    def test_barrier_at_A_forbids_new_contact_but_pending_mass_survives_to_H(self):
        owner=owner_fixture(remaining=120,delta=10);owner['job']['deadline_at']=1010
        f=audit.freeze_owner(owner);r=audit.score_owner(owner,f,owner['inputs']['pending'])
        self.assertEqual(r['planning_status'],'barrier_outside_new_contact_window')
        self.assertEqual(r['allowed_offsets_seconds'],[]);self.assertGreater(r['metrics']['selection_probability'],0)
        self.assertEqual(r['barrier_offset_seconds'],10)

    def test_timedout_own_probability_retained_while_freshness_releases_capacity(self):
        owner=owner_fixture(remaining=120,age=181,count=4,candidates=1,delta=0)
        frozen=audit.freeze_owner(owner);r=audit.score_owner(owner,frozen,owner['inputs']['pending'])
        self.assertTrue(all(sum(item['masses'])>0 for item in frozen['own_items']))
        self.assertTrue(r['forced'] or any(p['offset_seconds']==0 for p in r['plan']))
        self.assertEqual(r['occupancy'][0]['original_fresh_count'],0)
        old_p=1-math.prod(1-sum(i['masses']) for i in frozen['own_items'])
        self.assertGreater(r['metrics']['selection_probability'],old_p)

    def test_all_previous_declines_still_consume_nine_contact_slots(self):
        owner=owner_fixture(remaining=17,age=181,count=9,candidates=6,delta=7)
        for inquiry in owner['inquiries']:inquiry['state']='DECLINED'
        owner['inputs']['pending']=[];owner['inputs']['patient_pending']=[]
        f=audit.freeze_owner(owner);r=audit.score_owner(owner,f,owner['inputs']['pending'])
        self.assertEqual(owner['inputs']['max_new'],1);self.assertEqual(len(r['plan']),1)
        self.assertEqual(sum(p['offset_seconds'] is not None for p in r['plan']),1)

    def test_fresh_queue_age_zero_preserves_R_public_float_scalar_without_widening_comparison(self):
        owner=owner_fixture(count=0,candidates=1,delta=0);f=audit.freeze_owner(owner)
        row=[dict(hospital='NEW0',patient='EARLIER',submitted_at=1000)]
        expected=audit.score_owner(owner,f,row);age=expected['queue_records'][0]['age_seconds']
        self.assertEqual(age,0.);self.assertIs(type(age),float)
        actual=deepcopy(expected);actual['queue_records'][0]['age_seconds']=0.0
        audit.precise_same(actual,expected,'fresh R record')
        actual['queue_records'][0]['age_seconds']=1.0
        with self.assertRaises(ValueError):audit.precise_same(actual,expected,'fresh R record')
        actual['queue_records'][0]['age_seconds']=False
        with self.assertRaises(ValueError):audit.precise_same(actual,expected,'fresh R record')

    def test_no_pending_ignores_next_review_and_remaining_budget_never_resets(self):
        owner=owner_fixture(count=0,candidates=6,delta=20);owner['inputs']['max_new']=1
        f=audit.freeze_owner(owner);r=audit.score_owner(owner,f,owner['inputs']['pending'])
        self.assertEqual(r['barrier_offset_seconds'],0);self.assertEqual(len(r['plan']),1)
        self.assertEqual(r['plan'][0]['offset_seconds'],0)


class CompleteOwnerAuditTests(unittest.TestCase):
    def test_valid_D_rows_plus_complete_residual_owner_are_independently_recomputed(self):
        _,_,c,ms,ts,p,owners,original=fixture();d,e=output(ms,c,ts,p,owners,original)
        before=deepcopy(original);r=audit.audit_pair(ms,c,ts,d,e,p,owners)
        self.assertEqual(r['hedge_owner_origins_checked'],1);self.assertEqual(r['hedge_owner_scores_checked'],3)
        self.assertEqual(r['hedge_score_members_checked'],15);self.assertEqual(original,before)
        self.assertEqual([x['patient_id'] for x in e['row_scores'][0]['member_metrics']],['A','B','C','D','R'])
        self.assertEqual(e['owner_origins'][0]['patient_id'],'R')

    def test_omitted_zero_query_owner_prefix_priority_and_budget_tampering_fail(self):
        _,_,c,ms,ts,p,owners,original=fixture();d,e=output(ms,c,ts,p,owners,original)
        changes=[lambda x:x['inventory'].pop(),lambda x:x['patient_created_events'].pop(),
            lambda x:x.__setitem__('event_prefix_max',1),lambda x:x['inventory'][-1]['patient'].__setitem__('effective_priority',1),
            lambda x:x['inventory'][-1]['inputs'].__setitem__('max_new',9),
            lambda x:x['inventory'][-1]['apply_snapshot'].__setitem__('job_version',2),
            lambda x:x['inventory'][-1]['inputs']['candidates'].append(dict(hospital_id='hidden',travel={'duration_seconds':1},future=True))]
        for index,change in enumerate(changes):
            forged=deepcopy(owners);change(forged)
            with self.subTest(index=index),self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,e,p,forged)

    def test_origin_and_score_float_search_plan_and_occupancy_forgery_fail(self):
        _,_,c,ms,ts,p,owners,original=fixture();d,e=output(ms,c,ts,p,owners,original)
        changes=[lambda x:x['owner_origins'][0].__setitem__('now',1),
            lambda x:x['row_scores'][0]['owner_scores'][0]['plan'][0].__setitem__('offset_seconds',999),
            lambda x:x['row_scores'][0]['owner_scores'][0]['metrics'].__setitem__('selection_probability',.5),
            lambda x:x['row_scores'][0]['tier_probabilities'].__setitem__('4',100.),
            lambda x:x['row_scores'][0]['first_cohort_score']['projection']['final_pending'].pop(),
            lambda x:x.__setitem__('selected_index',-1)]
        for index,change in enumerate(changes):
            forged=deepcopy(e);change(forged)
            with self.subTest(index=index),self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,forged,p,owners)

    def test_full_native_replay_snapshot_link_fifo_and_all_patient_accounting(self):
        raw=replay();r=audit.audit_replay(raw);b=r['branch_audits'][0]
        self.assertTrue(r['passed']);self.assertEqual(b['total_arrival_failure_cost_seconds'],372)
        self.assertEqual(b['hedge_full_source_owners_checked'],3);self.assertEqual(b['hedge_owner_public_provenance_checked'],3)
        self.assertFalse(b['physical_transaction_boundary_from_raw_proved'])

    def test_full_source_capture_and_context_must_share_current_actual_projection(self):
        raw=replay();changes=[lambda g:g['owner_source_snapshot']['rows'][0]['patient'].__setitem__('priority',2),
            lambda g:g['owner_source_snapshot']['rows'][0]['job'].__setitem__('next_evaluation_at',999),
            lambda g:g['owner_source_snapshot']['events'].pop(),lambda g:g['owner_source_snapshot']['rows'].pop(),
            lambda g:g['owner_context']['inventory'][0]['inputs']['candidates'].reverse(),
            lambda g:g['owner_source_snapshot']['rows'][0]['patient'].pop('required_capabilities'),
            lambda g:g['owner_source_snapshot']['rows'][0]['patient'].__setitem__('future_potential',True)]
        for index,change in enumerate(changes):
            forged=deepcopy(raw);change(forged['branches'][0]['joint_groups'][0])
            with self.subTest(index=index),self.assertRaises(ValueError):audit.audit_replay(forged)

    def test_old_twelve_control_dispatch_and_clean_stdlib_only_import(self):
        raw=dynamic_replay();self.assertEqual(audit.audit_replay(raw),audit.base.audit_replay(raw))
        code="import sys;from inquiry_lab import cohort_hedge_audit;print([n for n in sys.modules if n.startswith('inquiry_lab.') and n.endswith(('_policy','_runner','_context','_model','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
