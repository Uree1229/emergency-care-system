"""Synthetic independent scores, fixed templates, priority and raw mutations."""
from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import cohort_success_audit as audit
from test_inquiry_lab_joint_budget_audit import setup as budget_setup, output as budget_output, replay as budget_replay
from test_inquiry_lab_joint_tail_audit import output as tail_output
from test_inquiry_lab_horizon_audit import output as horizon_output

ROOT = Path(__file__).resolve().parents[2]


def priority(context, members, tails):
    return dict(schema_version=1, now=context['now'], event_prefix_max=context['event_prefix_max'],
        decision_prefix_max=context['decision_prefix_max'], revision=members[0]['workflow_snapshot']['apply_snapshot']['revision'],
        source=audit.PRIORITY_SOURCE, patients=[dict(patient_id=m['patient_id'],
            patient_version=m['workflow_snapshot']['apply_snapshot']['patient_version'],
            reported_priority=None, priority_source='unknown', effective_priority=4) for m in members+tails])


def native(events, registry, context, members, tails):
    for event in events:
        if event['type'] == 'inquiry_workflow_started': event['data']['policy_name'] = audit.NAME
    for job in registry['jobs']: job['policy_name'] = audit.NAME
    for row in context['eligible']: row['job']['policy_name'] = audit.NAME
    for member in members+tails: member['workflow_snapshot']['job']['policy_name'] = audit.NAME


def fixture(patients=4, remaining=120):
    events, registry, context, members, tails, original = budget_setup(patients=patients, hospitals=3, remaining=remaining)
    # Whole public candidate lists may differ by owner. This synthetic cohort
    # has a third current-pair offer; every tail has only the first two offers.
    # Scenario-level completeness is separately tested by the full replay.
    for tail in tails: tail['inputs']['candidates'] = tail['inputs']['candidates'][:2]
    alias = audit.base.alias_member
    decisions, evidence = tail_output([alias(m) for m in members], audit.base.alias_context(context),
        [alias(m) for m in tails], original['evidence']['original_joint_proposal'])
    decisions, evidence = budget_output(members, context, tails, dict(decisions=decisions, evidence=evidence))
    budget = dict(decisions=decisions, evidence=evidence)
    native(events, registry, context, members, tails)
    return events, registry, context, members, tails, budget


def template(budget, tails):
    saved = budget['evidence']['projections']['winner']
    steps = [dict(step_index=s['step_index'], source_budget_projection_step_index=s['step_index'],
        members=deepcopy(s['members']), horizon_proposals=deepcopy(s['proposal']['evidence']['baseline_proposals']),
        sends=[list(d['send']) for d in s['proposal']['decisions']]) for s in saved['steps']]
    singleton = None
    if len(tails)%2:
        member = audit.first_member(tails[-1]); member['inputs']['pending'] = deepcopy(saved['final_pending'])
        h = horizon_output(member['inputs'], audit.base.first_contact_search(member['inputs'])['offsets'])
        singleton = dict(member=member, horizon_proposal=h, send=list(h['send']))
    return dict(source_path='original_budget_proposal.evidence.projections.winner', paired_steps=steps,
        singleton=singleton, reference_final_pending=deepcopy(saved['final_pending']), scope=audit.TEMPLATE_SCOPE)


def output(members, context, tails, budget, priorities):
    evaluated = budget['evidence']['cancelled_tail_veto']; b_index = budget['evidence']['actual_selected_index']
    jp = budget['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']
    models = [audit.public_model(m['inputs']) for m in members]
    table = audit.joint.enumerate_pairs(models, [p['send'] for p in jp['evidence']['baseline_proposals']])
    weights = audit.audit_priority_context(priorities, context, members, tails)
    saved = template(budget, tails) if evaluated else None
    scores = [audit.score_row(i, r['hospitals'], members, models, saved, weights, context['now'])
        for i, r in enumerate(table['combinations']) if r['feasible']] if evaluated else []
    selection = audit.global_staged_choice(scores, b_index) if evaluated else None
    selected = selection['selected_index'] if evaluated else b_index
    intervened = selected != b_index
    decisions = deepcopy(budget['decisions'])
    if intervened:
        row = table['combinations'][selected]
        decisions = deepcopy(jp['evidence']['baseline_proposals'])
        for i, decision in enumerate(decisions):
            send = row['hospitals'][i]; decision['send'] = list(send); decision['reason'] = 'cohort_success_current_only'
            decision['evidence']['sent_hospitals'] = list(send)
            decision['evidence']['planned_expected_cost'] = round(row['member_metrics'][i]['total_cost_seconds'], 3)
            for item in decision['evidence']['candidate_plan']:
                item['planned_offset_seconds'] = 0 if item['hospital'] in send else None
    for i, decision in enumerate(decisions):
        decision['evidence']['policy'] = audit.NAME
        decision['evidence']['cohort_success'] = dict(schema_version=1, mode='pair_cohort_success',
            evaluated=evaluated, intervened=intervened, member_index=i, base_budget_selected_index=b_index,
            selected_index=selected, effective_priority=weights[audit._pid(members[i]['patient_id'])])
    evidence = dict(schema_version=1, rule=audit.RULE, base_policy=audit.BASE_POLICY, base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME, calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'], copied_only=True, public_identity_unchanged=True),
        joint_context=deepcopy(context), priority_context=deepcopy(priorities), original_budget_proposal=deepcopy(budget),
        evaluated=evaluated, continuation_template=saved, row_scores=scores, selection=selection,
        base_budget_selected_index=b_index, selected_index=selected, intervened=intervened, tiers=[4,2,1],
        tolerances=dict(probability=1e-12, wait_seconds=1e-9), objective=audit.OBJECTIVE, scope=audit.SCOPE)
    return decisions, evidence


def replay():
    raw = budget_replay(); b = raw['branches'][0]; group = b['joint_groups'][0]
    budget = dict(decisions=deepcopy(group['outputs']), evidence=deepcopy(group['evidence']))
    native(b['db_events'], group['registry_snapshot'], group['context'], group['members'], group['tail_members'])
    group['priority_context'] = priority(group['context'], group['members'], group['tail_members'])
    group['outputs'], group['evidence'] = output(group['members'], group['context'], group['tail_members'], budget, group['priority_context'])
    for i, (call, decision) in enumerate(zip(b['policy_calls'], b['db_decisions'])):
        call['name'] = decision['policy_name'] = audit.NAME
        call['workflow_snapshot']['job']['policy_name'] = audit.NAME
        if i < 2: call['output'] = deepcopy(group['outputs'][i])
        else:
            call['output']['evidence'].update(policy=audit.NAME,
                cohort_success=dict(schema_version=1, mode='horizon_fallback', evaluated=False, intervened=False))
        decision.update(hospital_ids=deepcopy(call['output']['send']), reason=call['output']['reason'], evidence=deepcopy(call['output']['evidence']))
    b.update(policy=audit.NAME, instrumentation=deepcopy(audit.NEW_INSTRUMENTATION)); raw['arms'] = [[audit.NAME, 'immediate']]
    return raw


class IndependentCohortSelectionTests(unittest.TestCase):
    def test_valid_frozen_B_cancel_selects_third_membership_and_preserves_proposal(self):
        _, _, c, ms, ts, b = fixture(); before = deepcopy(b); p = priority(c, ms, ts)
        self.assertTrue(b['evidence']['cancelled_tail_veto'])
        d, e = output(ms, c, ts, b, p)
        checked = audit.audit_pair(ms, c, ts, d, e, p)
        self.assertTrue(e['intervened']); self.assertEqual([x['send'] for x in d], [['H01'], ['H03']])
        self.assertEqual(e['selected_index'], 2)
        self.assertEqual(checked['cohort_success_menus_evaluated'], 1)
        self.assertEqual(checked['cohort_score_members_checked'], len(e['row_scores'])*4)
        self.assertEqual(b, before); self.assertEqual(e['original_budget_proposal'], before)
        for decision in d:
            self.assertNotIn('joint_first', decision['evidence'])
            self.assertNotIn('joint_budget', decision['evidence'])
            self.assertNotIn('joint_tail', decision['evidence'])

    def test_singleton_reference_search_once_and_score_includes_whole_cohort(self):
        _, _, c, ms, ts, b = fixture(patients=5, remaining=120); p = priority(c, ms, ts)
        self.assertTrue(b['evidence']['cancelled_tail_veto'])
        d, e = output(ms, c, ts, b, p)
        result = audit.audit_pair(ms, c, ts, d, e, p)
        self.assertEqual(result['cohort_singleton_H_searches_checked'], 1)
        self.assertEqual([m['patient_id'] for m in e['row_scores'][0]['member_metrics']], ['A','B','C','D','E'])
        self.assertEqual(e['continuation_template']['singleton']['member']['inputs']['pending'],
            b['evidence']['projections']['winner']['final_pending'])

    def test_all_conditional_models_use_new_ledger_but_never_rerun_optimizer_per_row(self):
        _, _, c, ms, ts, b = fixture(); p = priority(c, ms, ts); d, e = output(ms, c, ts, b, p)
        original = audit.base.audit_first_contact_search
        with patch.object(audit.base, 'audit_first_contact_search', wraps=original) as h:
            audit.audit_pair(ms, c, ts, d, e, p)
        # Only frozen B's two paths x one tail pair x2 owners. No row-searches.
        self.assertEqual(h.call_count, 4)
        first = e['row_scores'][0]['member_metrics'][2]['metrics']['selection_probability']
        self.assertTrue(any(abs(r['member_metrics'][2]['metrics']['selection_probability']-first)>1e-8 for r in e['row_scores']))

    def test_forged_row_score_priority_sum_wait_or_selection_rejected(self):
        _, _, c, ms, ts, b = fixture(); p = priority(c, ms, ts); d, e = output(ms, c, ts, b, p)
        mutations = [lambda x:x['row_scores'].pop(), lambda x:x['row_scores'].reverse(),
            lambda x:x['row_scores'][0]['member_metrics'][0]['metrics'].__setitem__('selection_probability',
                x['row_scores'][0]['member_metrics'][0]['metrics']['selection_probability']+5e-10),
            lambda x:x['row_scores'][0]['tier_probabilities'].__setitem__('4',x['row_scores'][0]['tier_probabilities']['4']+5e-10),
            lambda x:x['row_scores'][0].__setitem__('sum_capped_wait_seconds',0.),
            lambda x:x.__setitem__('selected_index',x['base_budget_selected_index']),
            lambda x:x['selection']['priority_filters'][0]['surviving_row_indices'].clear()]
        for mutation_index, mutate in enumerate(mutations):
            forged = deepcopy(e); mutate(forged)
            with self.subTest(mutation_index=mutation_index), self.assertRaises(ValueError): audit.audit_pair(ms,c,ts,d,forged,p)

    def test_tiny_legacy_metric_error_is_not_a_source_for_current_scores(self):
        _, _, c, ms, ts, b = fixture(); p = priority(c, ms, ts); d, e = output(ms, c, ts, b, p)
        forged = deepcopy(e)
        table = forged['original_budget_proposal']['evidence']['original_tail_proposal']['evidence']['original_joint_proposal']['evidence']['combinations']
        table[0]['member_metrics'][0]['selection_probability'] += 5e-10
        # Old legacy equality permits this display-level change; new current
        # metrics are reconstructed and keep exactly the original new selection.
        audit.audit_pair(ms,c,ts,d,forged,p)
        forged['row_scores'][0]['member_metrics'][0]['metrics']['selection_probability'] += 5e-10
        with self.assertRaises(ValueError): audit.audit_pair(ms,c,ts,d,forged,p)

    def test_fixed_template_missing_reordered_send_or_fake_reference_rejected(self):
        _, _, c, ms, ts, b = fixture(patients=5); p = priority(c,ms,ts); d,e = output(ms,c,ts,b,p)
        mutations = [lambda x:x['paired_steps'].clear(), lambda x:x['paired_steps'][0]['sends'][0].append('H03'),
            lambda x:x['reference_final_pending'].pop(), lambda x:x.__setitem__('source_path','future'),
            lambda x:x['paired_steps'][0]['members'][0]['workflow_snapshot']['job'].__setitem__('version',2),
            lambda x:x['singleton']['member']['inputs']['pending'].pop(),
            lambda x:x.__setitem__('singleton',None),
            lambda x:x['singleton'].__setitem__('send',[])]
        for mutation_index, mutate in enumerate(mutations):
            forged=deepcopy(e); mutate(forged['continuation_template'])
            with self.subTest(mutation_index=mutation_index), self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,forged,p)

    def test_partial_cell_current_metrics_match_direct_log_normal_probability_and_wait(self):
        _,_,_,ms,_,_=budget_setup(patients=4,hospitals=2,remaining=17)
        model=audit.public_model(ms[0]['inputs']);row=model['hospitals'][0]
        actual=audit.joint.member_metrics(model,[row['hospital']],{})
        def cdf(t):
            return 0. if t<=0 else .5*(1+math.erf((math.log(t)-row['service_mu'])/(row['service_sigma']*math.sqrt(2))))
        q=row['reply_probability']*row['accept_probability']
        masses=[q*(cdf(right)-cdf(left)) for left,right in [(0,5),(5,10),(10,15),(15,17)]]
        probability=q*cdf(17);wait=sum(mid*mass for mid,mass in zip([2.5,7.5,12.5,16],masses))+17*(1-probability)
        self.assertEqual(model['cell_edges_seconds'],[5,10,15,17])
        self.assertAlmostEqual(actual['selection_probability'],probability,places=15)
        self.assertAlmostEqual(actual['capped_wait_seconds'],wait,places=12)
        self.assertAlmostEqual(actual['arrival_failure_cost_seconds'],wait+row['travel_seconds']*probability+1500*(1-probability),places=12)

    def test_legal_singleton_plan_different_from_reference_optimizer_rejected(self):
        _,_,c,ms,ts,b=fixture(patients=5);p=priority(c,ms,ts);d,e=output(ms,c,ts,b,p)
        singleton=e['continuation_template']['singleton'];inputs=singleton['member']['inputs']
        hospitals=[r['hospital_id'] for r in inputs['candidates']]
        selected=[hospitals[-1]] if singleton['send']!=[hospitals[-1]] else [hospitals[0]]
        alternative=horizon_output(inputs,{hid:0 if hid in selected else None for hid in hospitals})
        audit.horizon.audit_horizon(inputs,alternative) # legal plan, wrong frozen optimum
        forged=deepcopy(e);saved=forged['continuation_template']['singleton']
        saved.update(horizon_proposal=alternative,send=list(alternative['send']))
        with self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,d,forged,p)

    def test_changed_actual_sends_or_legacy_relabel_cannot_hide_intervention(self):
        _, _, c, ms, ts, b=fixture();p=priority(c,ms,ts);d,e=output(ms,c,ts,b,p)
        for key, value in [('send',['H02']),('reason','joint_tail_veto_baseline_current_only')]:
            forged=deepcopy(d);forged[0][key]=value
            with self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,forged,e,p)
        forged=deepcopy(d);forged[0]['evidence']['joint_budget']={}
        with self.assertRaises(ValueError):audit.audit_pair(ms,c,ts,forged,e,p)


class PriorityAndComparatorTests(unittest.TestCase):
    def test_normalizer_all_expert_tiers_self_and_unknown_and_complete_binding(self):
        _,_,c,ms,ts,_=fixture(patients=5);p=priority(c,ms,ts)
        for row, val, src, effective in zip(p['patients'],[2,1,None,1,2],['expert','expert','expert','self','unknown'],[2,1,4,4,4]):
            row.update(reported_priority=val,priority_source=src,effective_priority=effective)
        self.assertEqual(list(audit.audit_priority_context(p,c,ms,ts).values()),[2,1,4,4,4])
        with self.assertRaises(ValueError):audit.audit_priority_context(p,c,ms,ts,lab_defaults=True)
        mutations=[lambda x:x['patients'].pop(),lambda x:x['patients'].reverse(),
            lambda x:x['patients'][0].__setitem__('patient_version',2),lambda x:x.__setitem__('revision',1),
            lambda x:x.__setitem__('event_prefix_max',100),lambda x:x['patients'][0].__setitem__('effective_priority',4),
            lambda x:x['patients'][0].__setitem__('reported_priority',True)]
        for mutation_index, mutate in enumerate(mutations):
            forged=deepcopy(p);mutate(forged)
            with self.subTest(mutation_index=mutation_index),self.assertRaises(ValueError):audit.audit_priority_context(forged,c,ms,ts)

    def test_tier4_then2_then1_and_success_precedes_wait(self):
        def row(i,p4,p2,p1,w):return dict(row_index=i,tier_probabilities={'4':p4,'2':p2,'1':p1},sum_capped_wait_seconds=w)
        rows=[row(0,.8,1,2,1),row(1,.9,0,0,1000),row(2,.9,.2,0,2000),row(3,.9,.2,.1,3000)]
        r=audit.global_staged_choice(rows,0);self.assertEqual(r['selected_index'],3)
        self.assertEqual([f['surviving_row_indices'] for f in r['priority_filters']],[[1,2,3],[2,3],[3]])

    def test_global_probability_band_is_not_pairwise_chained(self):
        rows=[dict(row_index=i,tier_probabilities={'4':p,'2':0.,'1':0.},sum_capped_wait_seconds=w)
            for i,p,w in [(0,1.-1.5e-12,0),(1,1.-.75e-12,1),(2,1.,2)]]
        r=audit.global_staged_choice(rows,0)
        self.assertEqual(r['priority_filters'][0]['surviving_row_indices'],[1,2]);self.assertEqual(r['selected_index'],1)

    def test_global_wait_band_then_B_or_original_enumeration(self):
        rows=[dict(row_index=i,tier_probabilities={'4':1.,'2':0.,'1':0.},sum_capped_wait_seconds=w)
            for i,w in [(0,1.+1.5e-9),(1,1.+.75e-9),(2,1.)]]
        r=audit.global_staged_choice(rows,0);self.assertEqual(r['wait_surviving_row_indices'],[1,2]);self.assertEqual(r['selected_index'],1)
        self.assertEqual(audit.global_staged_choice(rows,2)['selected_index'],2)
        with self.assertRaises(ValueError):audit.global_staged_choice(rows,9)


class CompleteSavedRawTests(unittest.TestCase):
    def test_full_raw_fallback_commonprefix_fifo_and_all_patient_accounting(self):
        raw=replay();r=audit.audit_replay(raw);b=r['branch_audits'][0]
        self.assertTrue(r['passed']);self.assertEqual(r['new_executions'],0)
        self.assertEqual(b['policy_calls'],3);self.assertEqual(b['cohort_success_menus_evaluated'],0)
        self.assertEqual(b['cohort_success_rows_checked'],0);self.assertEqual(b['total_arrival_failure_cost_seconds'],372)
        self.assertTrue(b['all_current_and_continuation_score_models_independently_rebuilt'])
        self.assertFalse(b['priority_values_reconstructed_from_event_payloads'])
        self.assertFalse(b['physical_transaction_boundary_from_raw_proved'])

    def test_frozen_B_and_all_inherited_controls_dispatch(self):
        raw=budget_replay();expected=audit.base.audit_replay(raw)
        self.assertEqual(audit.audit_replay(raw),expected)

    def test_complete_priority_roster_tail_candidates_and_linked_raw_mutations(self):
        raw=replay()
        changes=[lambda x:x['branches'][0]['joint_groups'][0]['priority_context']['patients'].pop(),
            lambda x:x['branches'][0]['joint_groups'][0]['tail_members'][0]['inputs']['candidates'].reverse(),
            lambda x:x['branches'][0]['joint_groups'][0]['registry_snapshot']['patients'].pop(),
            lambda x:x['branches'][0]['joint_groups'][0]['priority_context']['patients'][0].update(reported_priority=2,priority_source='expert',effective_priority=2),
            lambda x:x['branches'][0]['policy_calls'][2]['output']['evidence'].pop('cohort_success'),
            lambda x:x['branches'][0]['trace'][0].__setitem__('hospital_id','H02')]
        for change in changes:
            forged=deepcopy(raw);change(forged)
            with self.subTest(change=change),self.assertRaises(ValueError):audit.audit_replay(forged)

    def test_native_lab_priority_rejects_expert_even_if_all_recordings_coherently_forged(self):
        raw=replay();b=raw['branches'][0];g=b['joint_groups'][0];p=g['priority_context']
        p['patients'][0].update(reported_priority=2,priority_source='expert',effective_priority=2)
        g['evidence']['priority_context']=deepcopy(p)
        for out in (g['outputs'][0],b['policy_calls'][0]['output']):out['evidence']['cohort_success']['effective_priority']=2
        b['db_decisions'][0]['evidence']=deepcopy(b['policy_calls'][0]['output']['evidence'])
        # Pure captured-context audit allows it; frozen lab's payload cannot.
        audit.audit_pair(g['members'],g['context'],g['tail_members'],g['outputs'],g['evidence'],p)
        with self.assertRaises(ValueError):audit.audit_replay(raw)

    def test_clean_import_contains_no_production_policy_context_runner_or_rollout(self):
        code="import sys; from inquiry_lab import cohort_success_audit; print([n for n in sys.modules if n.startswith('inquiry_lab.') and n.endswith(('_policy','_context','_runner','_experiment'))])"
        p=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(p.stdout.strip().replace("'",'"')),[])


if __name__=='__main__':unittest.main()
