"""Synthetic prefix/suffix, row-conditioned CS, score and actual-output audit."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_dynamic_audit as audit
from test_inquiry_lab_cohort_success_audit import fixture as cs_fixture, output as cs_output, priority, replay as cs_replay
from test_inquiry_lab_joint_budget_audit import output as budget_output
from test_inquiry_lab_joint_tail_audit import output as tail_output
from test_inquiry_lab_joint_first_audit import pair_output
from test_inquiry_lab_horizon_audit import output as horizon_output

ROOT=Path(__file__).resolve().parent
CS=audit.base
BUDGET=CS.base
TAIL=BUDGET.base


def reference_CS(members,context,tails,p,overrides=None):
    """An actual valid reference proposal using independent stdlib math only."""
    plans=[horizon_output(m['inputs'],BUDGET.first_contact_search(m['inputs'])['offsets']) for m in members]
    if overrides:
        for index,plan in overrides.items():plans[index]=deepcopy(plan)
    first_context=TAIL.alias_context(BUDGET.alias_context(CS.alias_context(context)))
    d,e=pair_output([CS.first_member(m) for m in members],first_context,plans)
    tail_context=BUDGET.alias_context(CS.alias_context(context))
    tail_ms=[BUDGET.alias_member(CS.alias_member(m)) for m in members]
    tail_ts=[BUDGET.alias_member(CS.alias_member(m)) for m in tails]
    d,e=tail_output(tail_ms,tail_context,tail_ts,dict(decisions=d,evidence=e))
    d,e=budget_output([CS.alias_member(m) for m in members],CS.alias_context(context),
        [CS.alias_member(m) for m in tails],dict(decisions=d,evidence=e))
    d,e=cs_output(members,context,tails,dict(decisions=d,evidence=e),p)
    return dict(decisions=d,evidence=e)


def native(events,registry,context,members,tails):
    for event in events:
        if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
    for job in registry['jobs']:job['policy_name']=audit.NAME
    for row in context['eligible']:row['job']['policy_name']=audit.NAME
    for m in members+tails:m['workflow_snapshot']['job']['policy_name']=audit.NAME


def fixture(patients=4,remaining=120,priority_tiers=None):
    events,registry,c,ms,ts,b=cs_fixture(patients=patients,remaining=remaining)
    p=priority(c,ms,ts)
    if priority_tiers is not None:
        for row,tier in zip(p['patients'],priority_tiers):row.update(reported_priority=tier,priority_source='expert',effective_priority=tier)
    d,e=cs_output(ms,c,ts,b,p);original=dict(decisions=d,evidence=e)
    native(events,registry,c,ms,ts)
    return events,registry,c,ms,ts,p,original


def row_output(index,position,hospitals,models,members,context,tails,p):
    now=context['now'];values=[];ledger=deepcopy(members[0]['inputs']['pending']);initial=deepcopy(ledger)
    weights={audit._pid(r['patient_id']):r['effective_priority'] for r in p['patients']}
    def collect(people,metrics):
        for m,metric in zip(people,metrics):values.append(dict(patient_id=m['patient_id'],effective_priority=weights[audit._pid(m['patient_id'])],metrics=metric))
    def append(people,sends,kind,step,path):
        rows=[]
        for i,(m,send) in enumerate(zip(people,sends)):
            for j,hid in enumerate(send):
                q=dict(hospital=hid,patient=m['patient_id'],submitted_at=now);ledger.append(deepcopy(q))
                rows.append(dict(inquiry=q,source=dict(kind=kind,step_index=step,member_index=i,send_index=j,
                    proposal_path=path.format(member=i,send=j))))
        return rows
    collect(members,audit.joint.combination(models,hospitals)['member_metrics'])
    initial_appends=append(members,hospitals,'current_pair_row',None,
        'original_cohort_success_proposal.evidence.original_budget_proposal.evidence.original_tail_proposal.evidence.original_joint_proposal.evidence.combinations['+str(index)+'].hospitals[{member}][{send}]')
    steps=[];cs_context=audit.alias_context(context);cs_tails=[audit.alias_member(t) for t in tails]
    for step_index in range(len(tails)//2):
        start=2*step_index;remaining=deepcopy(cs_tails[start:])
        for m in remaining:m['inputs']['pending']=deepcopy(ledger)
        pair,tail=remaining[:2],remaining[2:]
        suffix=deepcopy(cs_context);suffix['eligible']=suffix['eligible'][start+2:];suffix['pair_patient_ids']=[m['patient_id'] for m in pair]
        subpriority=deepcopy(p);subpriority['patients']=subpriority['patients'][start+2:]
        proposal=reference_CS(pair,suffix,tail,subpriority)
        sends=[d['send'] for d in proposal['decisions']]
        collect(pair,audit.joint.combination([CS.public_model(m['inputs']) for m in pair],sends)['member_metrics'])
        appends=append(pair,sends,'reference_CS_selected',step_index,
            'row_scores['+str(position)+'].projection.steps['+str(step_index)+'].proposal.decisions[{member}].send[{send}]')
        steps.append(dict(step_index=step_index,source_tail_member_indices=[start,start+1],joint_context=suffix,
            priority_context=subpriority,members=pair,tail_members=tail,proposal=proposal,appended_inquiries=appends))
    singleton=None
    if len(tails)%2:
        m=deepcopy(cs_tails[-1]);m['inputs']['pending']=deepcopy(ledger)
        proposal=horizon_output(m['inputs'],BUDGET.first_contact_search(m['inputs'])['offsets'])
        collect([m],[audit.joint.member_metrics(CS.public_model(m['inputs']),proposal['send'],{})])
        appends=append([m],[proposal['send']],'reference_H_singleton',len(steps),
            'row_scores['+str(position)+'].projection.singleton.proposal.send[{send}]')
        singleton=dict(member=m,proposal=proposal,appended_inquiries=appends)
    return dict(row_index=index,member_metrics=values,tier_probabilities={str(tier):sum((m['metrics']['selection_probability']
        for m in values if m['effective_priority']==tier),0.) for tier in (4,2,1)},
        sum_capped_wait_seconds=sum((m['metrics']['capped_wait_seconds'] for m in values),0.),
        projection=dict(current_pair_send=hospitals,initial_pending=initial,initial_appended_inquiries=initial_appends,
            steps=steps,singleton=singleton,final_pending=ledger))


def output(members,context,tails,p,original,isolated_score_index=None):
    evaluated=original['evidence']['evaluated'];base_index=original['evidence']['selected_index']
    menu=audit.menu_indices(original);joint=original['evidence']['original_budget_proposal']['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']
    models=[CS.public_model(m['inputs']) for m in members]
    table=audit.joint.enumerate_pairs(models,[d['send'] for d in joint['evidence']['baseline_proposals']])
    scores=[row_output(index,pos,table['combinations'][index]['hospitals'],models,members,context,tails,p) for pos,index in enumerate(menu)]
    if isolated_score_index is not None:
        # Test only: isolate the final actual-output transformation from score
        # mathematics. These altered scores are not a valid model artifact.
        for score in scores:score['tier_probabilities']['4']=100. if score['row_index']==isolated_score_index else 0.
    selection=CS.global_staged_choice(scores,base_index) if evaluated else None
    if selection is not None:selection['base_selected_tied']=selection.pop('base_budget_tied')
    selected=selection['selected_index'] if evaluated else base_index;intervened=selected!=base_index
    decisions=deepcopy(original['decisions'])
    if intervened:
        decisions=deepcopy(joint['evidence']['baseline_proposals']);row=table['combinations'][selected]
        for i,d in enumerate(decisions):
            send=row['hospitals'][i];d['send']=list(send);d['reason']='cohort_dynamic_current_only'
            d['evidence']['sent_hospitals']=list(send);d['evidence']['planned_expected_cost']=round(row['member_metrics'][i]['total_cost_seconds'],3)
            for c in d['evidence']['candidate_plan']:c['planned_offset_seconds']=0 if c['hospital'] in send else None
    weights={audit._pid(r['patient_id']):r['effective_priority'] for r in p['patients']}
    for i,d in enumerate(decisions):
        d['evidence']['policy']=audit.NAME
        d['evidence']['cohort_dynamic']=dict(schema_version=1,mode='pair_cohort_dynamic',evaluated=evaluated,intervened=intervened,
            member_index=i,base_selected_index=base_index,selected_index=selected,effective_priority=weights[audit._pid(members[i]['patient_id'])])
    evidence=dict(schema_version=1,rule=audit.RULE,base_policy=audit.BASE_POLICY,base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME,calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name','members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'],copied_only=True,public_identity_unchanged=True),
        joint_context=deepcopy(context),priority_context=deepcopy(p),original_cohort_success_proposal=deepcopy(original),evaluated=evaluated,
        menu_indices=menu,row_scores=scores,selection=selection,base_selected_index=base_index,selected_index=selected,intervened=intervened,
        tiers=[4,2,1],tolerances=dict(probability=1e-12,wait_seconds=1e-9),objective=audit.OBJECTIVE,scope=audit.SCOPE)
    return decisions,evidence


def replay():
    raw=cs_replay();b=raw['branches'][0];g=b['joint_groups'][0]
    original=dict(decisions=deepcopy(g['outputs']),evidence=deepcopy(g['evidence']))
    native(b['db_events'],g['registry_snapshot'],g['context'],g['members'],g['tail_members'])
    g['outputs'],g['evidence']=output(g['members'],g['context'],g['tail_members'],g['priority_context'],original)
    for i,(call,decision) in enumerate(zip(b['policy_calls'],b['db_decisions'])):
        call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
        if i<2:call['output']=deepcopy(g['outputs'][i])
        else:call['output']['evidence'].update(policy=audit.NAME,cohort_dynamic=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False))
        decision.update(hospital_ids=deepcopy(call['output']['send']),reason=call['output']['reason'],evidence=deepcopy(call['output']['evidence']))
    b.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
    return raw


class IndependentDynamicContinuationTests(unittest.TestCase):
    def test_unique_three_source_menu_and_actual_reference_sends_recomputed_per_row(self):
        _,_,c,ms,ts,p,original=fixture();before=deepcopy(original);d,e=output(ms,c,ts,p,original)
        self.assertEqual(e['menu_indices'],[2,1,0]);self.assertTrue(e['evaluated'])
        result=audit.audit_pair(ms,c,ts,d,e,p)
        self.assertEqual(result['dynamic_rows_checked'],3)
        self.assertEqual(result['dynamic_reference_CS_calls_checked'],3)
        self.assertEqual(result['dynamic_reference_H_searches_checked'],6)
        self.assertEqual(result['dynamic_score_members_checked'],12)
        counts=[[len(d['send']) for d in r['projection']['steps'][0]['proposal']['decisions']] for r in e['row_scores']]
        self.assertGreater(len({tuple(x) for x in counts}),1)
        self.assertEqual(original,before);self.assertEqual(e['original_cohort_success_proposal'],before)

    def test_isolated_intervention_output_path_does_not_reinterpret_legacy_flags(self):
        _,_,c,ms,ts,p,original=fixture();d,e=output(ms,c,ts,p,original,isolated_score_index=1)
        self.assertTrue(e['intervened'])
        # All real-math fixtures and mutations are checked without this mock.
        # Only the scored-row boundary is isolated here to exercise actual
        # output construction; no full-math intervention is claimed.
        with patch.object(audit,'audit_row',side_effect=lambda recorded,*args:(recorded,Counter())):
            audit.audit_pair(ms,c,ts,d,e,p)
            forged=deepcopy(d);forged[0]['evidence']['cohort_success']={}
            with self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,forged,e,p)
        for decision in d:
            self.assertEqual(decision['reason'],'cohort_dynamic_current_only')
            for key in ('joint_first','joint_tail','joint_budget','cohort_success'):
                self.assertNotIn(key,decision['evidence'])
            self.assertEqual(set(decision['evidence']['cohort_dynamic']),{'schema_version','mode','evaluated','intervened',
                'member_index','base_selected_index','selected_index','effective_priority'})

    def test_singleton_is_evaluated_separately_per_row_and_its_sends_enter_final_ledger_once(self):
        _,_,c,ms,ts,p,original=fixture(patients=5);d,e=output(ms,c,ts,p,original)
        result=audit.audit_pair(ms,c,ts,d,e,p)
        self.assertEqual(result['dynamic_singleton_H_searches_checked'],len(e['menu_indices']))
        for row in e['row_scores']:
            projection=row['projection'];singleton=projection['singleton']
            self.assertEqual([m['patient_id'] for m in row['member_metrics']],['A','B','C','D','E'])
            self.assertEqual(projection['final_pending'][-len(singleton['proposal']['send']):],
                [x['inquiry'] for x in singleton['appended_inquiries']])
            keys=[(x['patient'],x['hospital']) for x in projection['final_pending']]
            self.assertEqual(len(keys),len(set(keys)))

    def test_full_suffix_priority_clock_and_version_sources_are_not_replaceable(self):
        _,_,c,ms,ts,p,original=fixture(patients=6);d,e=output(ms,c,ts,p,original)
        mutations=[lambda s:s['joint_context']['eligible'].pop(),lambda s:s['priority_context']['patients'].pop(),
            lambda s:s['members'][0]['inputs'].__setitem__('now',c['now']+1),
            lambda s:s['members'][0]['workflow_snapshot']['job'].__setitem__('version',2),
            lambda s:s['tail_members'][0]['inputs']['pending'].clear(),
            lambda s:s['joint_context']['pair_patient_ids'].reverse(),
            lambda s:s['priority_context']['patients'][0].__setitem__('effective_priority',1),
            lambda s:s['members'][0]['workflow_snapshot']['job'].__setitem__('policy_name',audit.NAME)]
        for index,mutate in enumerate(mutations):
            forged=deepcopy(e);mutate(forged['row_scores'][0]['projection']['steps'][0])
            with self.subTest(index=index),self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,forged,p)

    def test_row_ledgers_cannot_share_baseline_state_duplicate_append_or_invent_future_send(self):
        _,_,c,ms,ts,p,original=fixture(patients=5);d,e=output(ms,c,ts,p,original)
        changes=[lambda x:x['initial_appended_inquiries'].pop(),lambda x:x['final_pending'].append(deepcopy(x['final_pending'][0])),
            lambda x:x['steps'][0]['appended_inquiries'][0]['source'].__setitem__('proposal_path','future'),
            lambda x:x['steps'][0]['members'][0]['inputs']['pending'].append(dict(hospital='H01',patient='Z',submitted_at=c['now']+15)),
            lambda x:x['singleton']['member']['inputs']['pending'].pop(),lambda x:x['singleton'].__setitem__('appended_inquiries',[])]
        for index,change in enumerate(changes):
            forged=deepcopy(e);change(forged['row_scores'][0]['projection'])
            with self.subTest(index=index),self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,forged,p)
        forged=deepcopy(e);forged['row_scores'][0]['projection']['steps']=deepcopy(e['row_scores'][1]['projection']['steps'])
        with self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,forged,p)

    def test_wrong_conditional_H_optimizer_plan_is_rejected_even_when_math_is_legal(self):
        _,_,c,ms,ts,p,original=fixture();d,e=output(ms,c,ts,p,original)
        forged=deepcopy(e);step=forged['row_scores'][0]['projection']['steps'][0]
        jp=step['proposal']['evidence']['original_budget_proposal']['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']
        inputs=step['members'][0]['inputs'];hospitals=[x['hospital_id'] for x in inputs['candidates']]
        changed=horizon_output(inputs,{hid:0 if i==0 else None for i,hid in enumerate(hospitals)})
        if changed['send']==jp['evidence']['baseline_proposals'][0]['send']:
            changed=horizon_output(inputs,{hid:0 if i==1 else None for i,hid in enumerate(hospitals)})
        audit.horizon.audit_horizon(inputs,changed)
        replacement=reference_CS(step['members'],step['joint_context'],step['tail_members'],step['priority_context'],overrides={0:changed})
        # Whole CS mathematics accepts any legal actual H baseline in its
        # source-bound contract. Dynamic hypothetical calls must additionally
        # match the independently reexecuted frozen coordinate search.
        CS.audit_pair(step['members'],step['joint_context'],step['tail_members'],replacement['decisions'],replacement['evidence'],step['priority_context'])
        step['proposal']=replacement
        with self.assertRaisesRegex(ValueError,'independent projected H coordinate-search offsets'):
            audit.audit_pair(ms,c,ts,d,forged,p)

    def test_conditional_selected_send_score_is_rebuilt_not_taken_from_CS_template_or_float(self):
        _,_,c,ms,ts,p,original=fixture();d,e=output(ms,c,ts,p,original)
        for index,mutate in enumerate([lambda x:x['row_scores'][0]['member_metrics'][0]['metrics'].__setitem__('selection_probability',.5),
                lambda x:x['row_scores'][0]['tier_probabilities'].__setitem__('4',10.),
                lambda x:x['row_scores'][0].__setitem__('sum_capped_wait_seconds',0.),
                lambda x:x.__setitem__('selected_index',-1),lambda x:x['row_scores'].reverse(),lambda x:x['menu_indices'].reverse()]):
            forged=deepcopy(e);mutate(forged)
            with self.subTest(index=index),self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,forged,p)
        forged=deepcopy(e);forged['row_scores'][0]['member_metrics'][0]['metrics']['selection_probability']+=5e-10
        with self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,forged,p)

    def test_menu_deduplicates_only_the_three_source_rows_in_order(self):
        _,_,_,_,_,_,original=fixture();self.assertEqual(audit.menu_indices(original),[2,1,0])
        original['evidence']['selected_index']=1;self.assertEqual(audit.menu_indices(original),[1,0])
        original['evidence']['original_budget_proposal']['evidence']['original_tail_proposal']['evidence']['actual_selected_index']=1
        self.assertEqual(audit.menu_indices(original),[1])
        original['evidence']['evaluated']=False;self.assertEqual(audit.menu_indices(original),[])

    def test_later_step_must_include_every_preceding_reference_send(self):
        _,_,c,ms,ts,p,original=fixture(patients=6);d,e=output(ms,c,ts,p,original)
        audit.audit_pair(ms,c,ts,d,e,p)
        first=e['row_scores'][0]['projection']['steps'][0]
        second=e['row_scores'][0]['projection']['steps'][1]
        self.assertGreater(len(second['members'][0]['inputs']['pending']),len(first['members'][0]['inputs']['pending']))
        forged=deepcopy(e)
        for member in forged['row_scores'][0]['projection']['steps'][1]['members']:
            member['inputs']['pending']=deepcopy(first['members'][0]['inputs']['pending'])
        with self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,forged,p)

    def test_singleton_legal_but_nonoptimal_H_proposal_is_rejected(self):
        _,_,c,ms,ts,p,original=fixture(patients=5);d,e=output(ms,c,ts,p,original)
        forged=deepcopy(e);singleton=forged['row_scores'][0]['projection']['singleton']
        inputs=singleton['member']['inputs'];hospitals=[x['hospital_id'] for x in inputs['candidates']]
        selected=[hospitals[-1]] if singleton['proposal']['send']!=[hospitals[-1]] else [hospitals[0]]
        alternative=horizon_output(inputs,{hid:0 if hid in selected else None for hid in hospitals})
        audit.horizon.audit_horizon(inputs,alternative)
        singleton['proposal']=alternative
        with self.assertRaisesRegex(ValueError,'independent projected H coordinate-search offsets'):
            audit.audit_pair(ms,c,ts,d,forged,p)

    def test_non_evaluated_CS_uses_no_projection_and_exact_original_decision(self):
        raw=replay();g=raw['branches'][0]['joint_groups'][0]
        self.assertFalse(g['evidence']['evaluated']);self.assertEqual(g['evidence']['menu_indices'],[])
        result=audit.audit_replay(raw)['branch_audits'][0]
        self.assertEqual(result['dynamic_rows_checked'],0);self.assertEqual(result['dynamic_menus_evaluated'],0)
        self.assertEqual(result['total_arrival_failure_cost_seconds'],372)
        self.assertTrue(result['conditional_reference_CS_current_H_searches_independently_reexecuted'])
        self.assertFalse(result['physical_transaction_boundary_from_raw_proved'])

    def test_all_eleven_controls_still_use_frozen_CS_dispatch(self):
        raw=cs_replay();self.assertEqual(audit.audit_replay(raw),CS.audit_replay(raw))

    def test_full_native_raw_priority_candidate_ledger_and_real_work_mutations_fail(self):
        raw=replay()
        changes=[lambda x:x['branches'][0]['joint_groups'][0]['tail_members'][0]['inputs']['candidates'].reverse(),
            lambda x:x['branches'][0]['joint_groups'][0]['priority_context']['patients'].pop(),
            lambda x:x['branches'][0]['joint_groups'][0]['registry_snapshot']['patients'].pop(),
            lambda x:x['branches'][0]['policy_calls'][2]['output']['evidence'].pop('cohort_dynamic'),
            lambda x:x['branches'][0]['trace'][0].__setitem__('hospital_id','H02')]
        for index,change in enumerate(changes):
            forged=deepcopy(raw);change(forged)
            with self.subTest(index=index),self.assertRaises(ValueError):audit.audit_replay(forged)

    def test_clean_import_never_loads_production_policy_runner_context_or_simulation(self):
        code="import sys;from inquiry_lab import cohort_dynamic_audit;print([n for n in sys.modules if n.startswith('inquiry_lab.') and n.endswith(('_policy','_runner','_context','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
