"""Synthetic optimizer, virtual-prefix, binary-output and journal mutations."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from inquiry_lab import joint_budget_audit as audit
from test_inquiry_lab_horizon_audit import output as horizon_output
from test_inquiry_lab_joint_first_audit import pair_output
from test_inquiry_lab_joint_tail_audit import setup as tail_setup, output as tail_output, replay as tail_replay

ROOT = Path(__file__).resolve().parent


def setup(patients=4, hospitals=2, remaining=120):
    events, registry, context, members, tails, _ = tail_setup(hospitals=hospitals)
    for index in range(3, patients):
        pid = chr(ord('A')+index); t = deepcopy(tails[0]); t['patient_id'] = pid
        t['workflow_snapshot']['apply_snapshot']['patient_id'] = pid
        t['workflow_snapshot']['job']['patient_id'] = pid
        tc = t['inputs']['time_context']; tc['patient_id'] = pid; tc['source']['workflow']['patient_id'] = pid
        tc['source']['patient_created_event'].update(patient_id=pid, id=2*index+1)
        tc['source']['workflow_start_event'].update(patient_id=pid, id=2*index+2)
        tails.append(t)
        events += [dict(id=2*index+1, at=audit.EPOCH, type='patient_created', patient_id=pid, data={}),
            dict(id=2*index+2, at=audit.EPOCH, type='inquiry_workflow_started', patient_id=pid,
                data=dict(deadline_seconds=300, policy_name=audit.BASE_POLICY))]
        registry['patients'].append(dict(id=pid, state='WAITING', version=1))
        registry['jobs'].append(dict(registry['jobs'][0], patient_id=pid))
        context['eligible'].append(dict(patient=deepcopy(registry['patients'][-1]),
            job=deepcopy(registry['jobs'][-1]), inquiry_count=0, decision_count=0))
    now = audit.EPOCH+300-remaining; context.update(now=now, event_prefix_max=2*patients)
    for m in members+tails:
        m['inputs']['now'] = m['workflow_snapshot']['apply_snapshot']['now'] = now
        m['inputs']['time_context']['event_prefix_max'] = 2*patients
    # Current pair H plans are explicitly recorded synthetic plans. Only
    # hypothetical future first-contact plans require the new search audit.
    proposals = [horizon_output(m['inputs'], {r['hospital_id']: 0 if i == 0 else None
        for i, r in enumerate(m['inputs']['candidates'])}) for m in members]
    td, te = pair_output([audit.base.alias_member(m) for m in members], audit.base.alias_context(context), proposals)
    d, e = tail_output(members, context, tails, dict(decisions=td, evidence=te))
    original = dict(decisions=d, evidence=e)
    for event in events:
        if event['type'] == 'inquiry_workflow_started': event['data']['policy_name'] = audit.NAME
    for job in registry['jobs']: job['policy_name'] = audit.NAME
    for row in context['eligible']: row['job']['policy_name'] = audit.NAME
    for m in members+tails: m['workflow_snapshot']['job']['policy_name'] = audit.NAME
    return events, registry, context, members, tails, original


def projection(path, context, members, tails, original_joint):
    pc = audit.base.alias_context(audit.alias_context(context))
    pair = [audit.base.alias_member(audit.alias_member(m)) for m in members]
    ts = [audit.base.alias_member(audit.alias_member(m)) for m in tails]
    ledger = deepcopy(pair[0]['inputs']['pending']); initial = deepcopy(ledger)
    def append(ms, sends, kind, step, source):
        out = []
        for i, (m, send) in enumerate(zip(ms, sends)):
            for j, hid in enumerate(send):
                q = dict(hospital=hid, patient=m['patient_id'], submitted_at=context['now']); ledger.append(deepcopy(q))
                out.append(dict(inquiry=q, source=dict(kind=kind, step_index=step, member_index=i,
                    send_index=j, proposal_path=source.format(member=i, send=j))))
        return out
    prefix = 'original_tail_proposal.evidence.original_joint_proposal.'
    if path == 'baseline':
        sends = [p['send'] for p in original_joint['evidence']['baseline_proposals']]
        source = prefix+'evidence.baseline_proposals[{member}].send[{send}]'
    else:
        sends = [p['send'] for p in original_joint['decisions']]; source = prefix+'decisions[{member}].send[{send}]'
    first = append(pair, sends, 'current_pair_'+path, None, source); steps = []
    for index in range(len(ts)//2):
        start = 2*index; ms = deepcopy(ts[start:start+2]); c = deepcopy(pc)
        c['eligible'] = c['eligible'][start+2:]; c['pair_patient_ids'] = [m['patient_id'] for m in ms]
        for m in ms: m['inputs']['pending'] = deepcopy(ledger)
        hs = [horizon_output(m['inputs'], audit.first_contact_search(m['inputs'])['offsets']) for m in ms]
        d, e = pair_output(ms, c, hs); p = dict(decisions=d, evidence=e)
        ap = append(ms, [o['send'] for o in d], 'projected_pair_selected', index,
            'projections.'+path+'.steps['+str(index)+'].proposal.decisions[{member}].send[{send}]')
        steps.append(dict(step_index=index, source_tail_member_indices=[start, start+1],
            joint_context=c, members=ms, proposal=p, appended_inquiries=ap))
    return dict(path=path, initial_pending=initial, initial_appended_inquiries=first,
        paired_patient_ids=[[m['patient_id'] for m in ts[2*i:2*i+2]] for i in range(len(ts)//2)],
        excluded_singleton_patient_ids=[ts[-1]['patient_id']] if len(ts)%2 else [], steps=steps, final_pending=ledger)


def output(members, context, tails, original):
    ev = original['evidence']; jp = ev['original_joint_proposal']; evaluated = ev['vetoed']
    ps = {name: projection(name, context, members, tails, jp) if evaluated else None for name in ('baseline', 'winner')}
    comparisons = []
    if evaluated:
        for step, (b, w) in enumerate(zip(ps['baseline']['steps'], ps['winner']['steps'])):
            for i, m in enumerate(b['members']):
                bk = b['proposal']['evidence']['cardinalities'][i]; wk = w['proposal']['evidence']['cardinalities'][i]
                comparisons.append(dict(patient_id=m['patient_id'], projection_step=step, member_index=i,
                    baseline_k=bk, winner_k=wk, declined=bk < wk))
    declining = [c['patient_id'] for c in comparisons if c['declined']]; cancelled = bool(declining)
    selected = jp['evidence']['selected_index'] if cancelled else ev['actual_selected_index']
    outputs = deepcopy(jp['decisions'] if cancelled else original['decisions'])
    for d in outputs:
        d['evidence']['policy'] = audit.NAME
        d['evidence']['joint_budget'] = dict(schema_version=1, mode='pair_paired_count_budget_guard',
            projection_evaluated=evaluated, original_tail_vetoed=ev['vetoed'], cancelled_tail_veto=cancelled,
            declining_patients=declining, actual_selected_index=selected)
    evidence = dict(schema_version=1, rule=audit.RULE, base_policy=audit.BASE_POLICY,
        base_source_sha256=audit.BASE_SOURCE_SHA256, projection_policy=audit.PROJECTION_POLICY,
        projection_source_sha256=audit.PROJECTION_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME, calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'], copied_only=True, public_identity_unchanged=True),
        joint_context=deepcopy(context), original_tail_proposal=deepcopy(original), projection_evaluated=evaluated,
        projections=ps, paired_count_comparisons=comparisons, declining_patients=declining,
        cancelled_tail_veto=cancelled, actual_selected_index=selected, projection_rule=audit.PROJECTION_RULE,
        singleton_rule=audit.SINGLETON_RULE,
        scope='same-clock public-input inquiry arithmetic only; no future replies, selections or outcome rollout')
    return outputs, evidence


def replay():
    raw = tail_replay(); b = raw['branches'][0]; g = b['joint_groups'][0]
    original = dict(decisions=deepcopy(g['outputs']), evidence=deepcopy(g['evidence']))
    for event in b['db_events']:
        if event['type'] == 'inquiry_workflow_started': event['data']['policy_name'] = audit.NAME
    for job in g['registry_snapshot']['jobs']: job['policy_name'] = audit.NAME
    for row in g['context']['eligible']: row['job']['policy_name'] = audit.NAME
    for m in g['members']+g['tail_members']: m['workflow_snapshot']['job']['policy_name'] = audit.NAME
    g['outputs'], g['evidence'] = output(g['members'], g['context'], g['tail_members'], original)
    for i, (call, decision) in enumerate(zip(b['policy_calls'], b['db_decisions'])):
        call['name'] = decision['policy_name'] = audit.NAME
        call['workflow_snapshot']['job']['policy_name'] = audit.NAME
        if i < 2: call['output'] = deepcopy(g['outputs'][i])
        else:
            call['output']['evidence'].update(policy=audit.NAME,
                joint_budget=dict(schema_version=1, mode='horizon_fallback', projection_evaluated=False))
        decision.update(hospital_ids=deepcopy(call['output']['send']), reason=call['output']['reason'],
            evidence=deepcopy(call['output']['evidence']))
    b.update(policy=audit.NAME, instrumentation=deepcopy(audit.NEW_INSTRUMENTATION)); raw['arms'] = [[audit.NAME, 'immediate']]
    return raw


class IndependentProjectedHSearchTests(unittest.TestCase):
    def test_six_candidate_search_keeps_original_grid_passes_room_and_future_offsets(self):
        _, _, _, members, _, _ = setup(hospitals=6, remaining=300); i = members[0]['inputs']
        expected = dict(H01=0, H02=0, H03=0, H04=0, H05=60, H06=90)
        r = audit.first_contact_search(i); self.assertEqual(r['offsets'], expected)
        self.assertEqual(r['passes'], 2); self.assertFalse(r['forced'])
        checked = audit.audit_first_contact_search(i, horizon_output(i, expected))
        self.assertEqual(checked['projected_H_searches_independently_reexecuted'], 1)

    def test_partial_horizon_force_one_can_raise_objective_and_preserves_stable_ties(self):
        _, _, _, members, _, _ = setup(hospitals=2, remaining=17); i = members[0]['inputs']
        i['candidates'][1]['travel']['duration_seconds'] = i['candidates'][0]['travel']['duration_seconds']
        i['candidates'].reverse(); r = audit.first_contact_search(i)
        self.assertEqual(r['send'], ['H02']); self.assertTrue(r['forced']); self.assertGreater(r['cost'], 1517)
        audit.audit_first_contact_search(i, horizon_output(i, r['offsets']))

    def test_force_one_retains_other_future_plan_instead_of_resetting_it(self):
        _, _, _, members, _, _ = setup(remaining=300); i = members[0]['inputs']
        def cost(model, offsets):
            return {(None, None):100, (15,None):90, (15,30):80, (0,30):85, (15,0):86}.get(
                (offsets['H01'], offsets['H02']), 110)
        with patch.object(audit.horizon, 'plan_cost', side_effect=cost):r = audit.first_contact_search(i)
        self.assertTrue(r['forced']); self.assertEqual(r['offsets'], dict(H01=0,H02=30)); self.assertEqual(r['cost'],85)

    def test_legal_recorded_plan_cannot_forge_a_different_projected_count(self):
        _, _, _, members, _, _ = setup(remaining=300); i = members[0]['inputs']
        forged = horizon_output(i, dict(H01=0, H02=None))
        audit.horizon.audit_horizon(i, forged)  # legal mathematics, wrong frozen optimizer choice
        with self.assertRaisesRegex(audit.JointBudgetAuditError, 'coordinate-search offsets'):
            audit.audit_first_contact_search(i, forged)

    def test_start_offsets_are_strictly_before_A_and_H_and_only_first_six_are_used(self):
        _, _, _, members, _, _ = setup(hospitals=7, remaining=300); i = members[0]['inputs']
        i['time_context']['auto_inquiry_deadline_at'] = i['now']+15
        r = audit.first_contact_search(i)
        self.assertEqual(list(r['offsets']), ['H01','H02','H03','H04','H05','H06'])
        self.assertTrue(all(d in (None,0) for d in r['offsets'].values()))
        for change in ('own', 'last', 'deadline'):
            bad = deepcopy(i)
            if change == 'own': bad['patient_pending'] = [dict(hospital='H01', patient='A', submitted_at=i['now'], travel_seconds=100)]
            elif change == 'last': bad['last_contact_at'] = i['now']
            else: bad['time_context']['auto_inquiry_deadline_at'] = i['now']
            with self.subTest(change=change), self.assertRaises(audit.JointBudgetAuditError):audit.first_contact_search(bad)


class IndependentBudgetProjectionTests(unittest.TestCase):
    def test_complete_two_paths_and_each_individual_count_are_independently_checked(self):
        _, _, context, members, tails, original = setup(); before = deepcopy(original)
        decisions, evidence = output(members, context, tails, original)
        checked = audit.audit_pair(members, context, tails, decisions, evidence)
        self.assertEqual(original, before); self.assertTrue(evidence['projection_evaluated'])
        self.assertEqual(checked['projected_pairs_checked'],2)
        self.assertEqual(checked['projected_H_searches_independently_reexecuted'],4)
        self.assertEqual([r['patient_id'] for r in evidence['paired_count_comparisons']], ['C','D'])
        self.assertEqual([(r['baseline_k'],r['winner_k']) for r in evidence['paired_count_comparisons']],[(1,2),(1,2)])
        self.assertTrue(evidence['cancelled_tail_veto']); self.assertEqual(checked['paired_budget_veto_cancellations'],1)
        self.assertEqual([d['send'] for d in decisions],[['H01'],['H02']])
        self.assertEqual([d['send'] for d in original['decisions']],[['H01'],['H01']])
        self.assertNotIn('joint_tail',decisions[1]['evidence'])

    def test_an_unvetoed_original_is_unchanged_without_any_virtual_search(self):
        _, _, context, members, tails, original = setup(hospitals=1)
        decisions, evidence = output(members,context,tails,original)
        self.assertFalse(evidence['projection_evaluated']); self.assertEqual(evidence['projections'],dict(baseline=None,winner=None))
        with patch.object(audit,'first_contact_search',side_effect=AssertionError('unexpected hypothetical search')):
            audit.audit_pair(members,context,tails,decisions,evidence)
        for actual,wanted in zip(decisions,original['decisions']):
            normalized=deepcopy(actual);normalized['evidence'].pop('joint_budget');normalized['evidence']['policy']=audit.BASE_POLICY
            self.assertEqual(normalized,wanted)

    def test_equal_projected_counts_preserve_the_veto_and_original_baseline_outputs(self):
        _, _, context, members, tails, original = setup(remaining=300)
        decisions,evidence=output(members,context,tails,original)
        audit.audit_pair(members,context,tails,decisions,evidence)
        self.assertEqual([(r['baseline_k'],r['winner_k']) for r in evidence['paired_count_comparisons']],[(2,2),(2,2)])
        self.assertFalse(evidence['cancelled_tail_veto']);self.assertEqual([d['send'] for d in decisions],[['H01'],['H01']])
        self.assertIn('joint_tail',decisions[1]['evidence'])

    def test_last_singleton_keeps_original_TAIL_protection_and_has_no_virtual_decision(self):
        _, _, context, members, tails, original = setup(patients=3, remaining=300)
        decisions, evidence = output(members, context, tails, original)
        checked = audit.audit_pair(members, context, tails, decisions, evidence)
        self.assertTrue(original['evidence']['vetoed']); self.assertFalse(evidence['cancelled_tail_veto'])
        self.assertEqual([d['send'] for d in decisions], [['H01'],['H01']])
        self.assertEqual(evidence['projections']['baseline']['excluded_singleton_patient_ids'],['C'])
        self.assertEqual(evidence['projections']['baseline']['steps'],[])
        self.assertEqual(checked['paired_budget_members_checked'],0)

    def test_multiple_suffix_steps_accumulate_only_selected_immediate_inquiries(self):
        _, _, context, members, tails, original = setup(patients=7, remaining=300)
        decisions, evidence = output(members, context, tails, original)
        checked = audit.audit_pair(members, context, tails, decisions, evidence)
        self.assertEqual(checked['projected_pairs_checked'],4)
        for path in ('baseline','winner'):
            p = evidence['projections'][path]
            self.assertEqual(p['paired_patient_ids'],[['C','D'],['E','F']])
            self.assertEqual(p['excluded_singleton_patient_ids'],['G'])
            self.assertEqual([r['patient']['id'] for r in p['steps'][1]['joint_context']['eligible']],['E','F','G'])
            self.assertEqual(p['steps'][1]['members'][0]['inputs']['pending'],
                [r['inquiry'] for r in p['initial_appended_inquiries']+p['steps'][0]['appended_inquiries']])

    def test_projection_sources_roster_clock_versions_and_full_table_cannot_be_forged(self):
        for change in ('omit_step','reorder','pending','own','now','version','suffix','context_prefix',
                'source_path','future_submit','future_offset','ki','table','comparison','cancel','actual'):
            _, _, context, members, tails, original = setup()
            decisions, e = output(members, context, tails, original)
            p = e['projections']['baseline']; s = p['steps'][0]
            if change == 'omit_step': p['steps'].pop()
            elif change == 'reorder': s['members'].reverse()
            elif change == 'pending': s['members'][1]['inputs']['pending'].pop()
            elif change == 'own': s['members'][0]['inputs']['patient_pending'] = [p['initial_appended_inquiries'][0]['inquiry']]
            elif change == 'now': s['members'][0]['inputs']['now'] += 1
            elif change == 'version': s['members'][0]['workflow_snapshot']['job']['version'] += 1
            elif change == 'suffix': s['joint_context']['eligible'].pop()
            elif change == 'context_prefix': s['members'][0]['inputs']['time_context']['event_prefix_max'] += 1
            elif change == 'source_path': p['initial_appended_inquiries'][0]['source']['proposal_path'] = 'actual.db_events[99]'
            elif change == 'future_submit': p['initial_appended_inquiries'][0]['inquiry']['submitted_at'] += 15
            elif change == 'future_offset': s['proposal']['evidence']['baseline_proposals'][0]['evidence']['candidate_plan'][0]['planned_offset_seconds'] = 15
            elif change == 'ki': s['proposal']['evidence']['cardinalities'][0] += 1
            elif change == 'table': s['proposal']['evidence']['combinations'].pop()
            elif change == 'comparison': e['paired_count_comparisons'][0]['baseline_k'] += 1
            elif change == 'cancel': e['cancelled_tail_veto'] = not e['cancelled_tail_veto']
            else: decisions[1]['send'] = ['H99']
            with self.subTest(change=change), self.assertRaises(audit.JointBudgetAuditError):
                audit.audit_pair(members, context, tails, decisions, e)


class IndependentActualTraceTests(unittest.TestCase):
    def test_full_actual_baseline_fallback_FIFO_and_all_patient_accounting(self):
        raw = replay(); before = deepcopy(raw); c = audit.audit_replay(raw)['branch_audits'][0]
        self.assertEqual(raw,before); self.assertEqual(c['total_capped_selection_wait_seconds'],52)
        self.assertEqual(c['total_arrival_failure_cost_seconds'],372); self.assertEqual(c['review_work_seconds'],42)

    def test_actual_sends_work_wait_fallback_and_complete_tail_public_candidates_are_bound(self):
        for change in ('journal','work','wait','fallback','omit_tail','omit_offer','extra_offer','ledger','version','resource','beforewrite'):
            raw = replay(); b = raw['branches'][0]; g = b['joint_groups'][0]; t = g['tail_members'][0]
            if change == 'journal': next(e for e in b['db_events'] if e['id']==9)['hospital_id']='H02'
            elif change == 'work': b['metrics']['review_work_seconds']+=1
            elif change == 'wait': b['patients'][0]['selection_seconds']+=1
            elif change == 'fallback':
                b['policy_calls'][2]['output']['evidence']['joint_budget']['projection_evaluated']=True
                b['db_decisions'][2]['evidence']=deepcopy(b['policy_calls'][2]['output']['evidence'])
            elif change == 'omit_tail':g['tail_members']=[]
            elif change == 'omit_offer':t['inputs']['candidates'].pop()
            elif change == 'extra_offer':t['inputs']['candidates'].append(dict(hospital_id='H99',travel=dict(duration_seconds=999)))
            elif change == 'ledger':t['inputs']['pending'].append(dict(hospital='H01',patient='Q',submitted_at=audit.EPOCH))
            elif change == 'version':t['workflow_snapshot']['job']['version']+=1
            elif change == 'resource':t['inputs']['candidates'][0]['resources_enough']=False
            else:g['validated_before_write']=False
            with self.subTest(change=change), self.assertRaises(audit.JointBudgetAuditError):audit.audit_replay(raw)

    def test_candidate_six_seven_order_swap_is_rejected(self):
        events, _, _, _, tails, _ = setup(patients=3, remaining=300); t = tails[0]
        t['inputs']['candidates'] += [dict(hospital_id=f'H{i:02d}', travel=dict(duration_seconds=100+(i-1)*20),
            resources_enough=True) for i in range(3,8)]
        scenario = dict(patients=[dict(id='C',at=0,travel_seconds={r['hospital_id']:r['travel']['duration_seconds'] for r in t['inputs']['candidates']})])
        self.assertEqual(audit.audit_member_provenance(scenario,events,t,6,0),1)
        c=t['inputs']['candidates'];c[5],c[6]=c[6],c[5]
        with self.assertRaisesRegex(audit.JointBudgetAuditError,'order/resource'):
            audit.audit_member_provenance(scenario,events,t,6,0)

    def test_clean_import_never_loads_producer_runner_context_server_or_solver(self):
        code=("import json,sys;import inquiry_lab.joint_budget_audit;print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server','simulation','ortools')) or n.endswith(('_policy','_runner','_context','_experiment')))))")
        self.assertEqual(json.loads(subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,text=True)),[])


if __name__=='__main__':unittest.main()
