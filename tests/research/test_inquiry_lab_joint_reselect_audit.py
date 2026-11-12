"""Synthetic nested-proposal, all-row constraint and actual-journal tests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from inquiry_lab import joint_reselect_audit as audit
from test_inquiry_lab_joint_tail_audit import setup as tail_setup, output as tail_output
from test_inquiry_lab_horizon_audit import output as horizon_output

ROOT = Path(__file__).resolve().parents[2]


def setup(hospitals=3, tail_hospitals=2):
    events, registry, context, members, tails, original_joint = tail_setup(hospitals=hospitals)
    tails[0]['inputs']['candidates'] = tails[0]['inputs']['candidates'][:tail_hospitals]
    decisions, evidence = tail_output(members, context, tails, original_joint)
    original = dict(decisions=decisions, evidence=evidence)
    for event in events:
        if event['type'] == 'inquiry_workflow_started': event['data']['policy_name'] = audit.NAME
    for job in registry['jobs']: job['policy_name'] = audit.NAME
    for row in context['eligible']: row['job']['policy_name'] = audit.NAME
    for member in members+tails: member['workflow_snapshot']['job']['policy_name'] = audit.NAME
    return events, registry, context, members, tails, original


def output(members, context, tails, original):
    """Build a synthetic recording independently of the audit entry point."""
    joint = original['evidence']['original_joint_proposal']['evidence']
    table = joint['combinations']; pending = members[0]['inputs']['pending']
    opportunities = [dict(patient_id=t['patient_id'], hospitals=[r['hospital_id'] for r in
        t['inputs']['candidates'][:min(6, t['inputs']['max_new'])]]) for t in tails]
    def checks(sends):
        rows = []
        for t in opportunities:
            pool = t['hospitals']
            def counts(batches):
                return {h: sum(q['hospital'] == h for q in pending)+sum(h in b for b in batches) for h in pool}
            before = counts(joint['baseline_combination']['hospitals']); after = counts(sends)
            bz = [h for h in pool if before[h] == 0]; az = [h for h in pool if after[h] == 0]
            rows.append(dict(patient_id=t['patient_id'], candidate_pool=pool, baseline_pending_counts=before,
                selected_pending_counts=after, baseline_zero_pending_hospitals=bz,
                selected_zero_pending_hospitals=az, triggered=bool(bz) and not az))
        return rows
    feasibility = []
    for i, row in enumerate(table):
        blocked = [r['patient_id'] for r in checks(row['hospitals']) if r['triggered']]
        feasibility.append(dict(index=i, joint_feasible=row['feasible'], tail_feasible=not blocked,
            feasible=row['feasible'] and not blocked, blocked_tail_patients=blocked))
    feasible = [r['index'] for r in feasibility if r['feasible']]
    minimum = min(table[i]['sum_capped_wait_seconds'] for i in feasible)
    best = next(i for i in feasible if table[i]['sum_capped_wait_seconds'] <= minimum+1e-9)
    baseline = joint['baseline_index']; tied = table[baseline]['sum_capped_wait_seconds'] <= minimum+1e-9
    selected = baseline if tied else best
    applied = table[baseline]['sum_capped_wait_seconds']-table[selected]['sum_capped_wait_seconds'] > 1e-9
    if not applied: selected = baseline
    decisions = []
    for i, proposal in enumerate(joint['baseline_proposals']):
        d = deepcopy(proposal); row = table[selected]; send = row['hospitals'][i]
        d.update(send=list(send), reason='joint_reselect_current_only' if applied else 'joint_reselect_baseline_current_only')
        e = d['evidence']; e.update(policy=audit.NAME, sent_hospitals=list(send),
            planned_expected_cost=round(row['member_metrics'][i]['total_cost_seconds'], 3))
        for c in e['candidate_plan']: c['planned_offset_seconds'] = 0 if c['hospital'] in send else None
        e['joint_first'] = dict(schema_version=1, mode='pair_current_only', member_index=i,
            current_only=True, applied=applied, baseline_send=list(joint['baseline_combination']['hospitals'][i]),
            selected_send=list(send), baseline_metrics=deepcopy(joint['baseline_combination']['member_metrics'][i]),
            selected_metrics=deepcopy(row['member_metrics'][i]),
            constraint_reference='common-prefix simultaneous immediate-only H sends')
        e['joint_reselect'] = dict(schema_version=1, mode='pair_tail_safe_reselection', member_index=i,
            selected_index=selected, baseline_index=baseline, original_joint_selected_index=joint['selected_index'],
            original_tail_selected_index=original['evidence']['actual_selected_index'], tail_safe=True, applied=applied)
        decisions.append(d)
    evidence = dict(schema_version=1, rule=audit.RULE, base_policy=audit.BASE_POLICY,
        base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME, calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name',
                'tail_members[].workflow_snapshot.job.policy_name'], copied_only=True, public_identity_unchanged=True),
        joint_context=deepcopy(context), original_tail_proposal=deepcopy(original), tail_opportunities=opportunities,
        row_feasibility=feasibility, minimum_feasible_wait_seconds=minimum, best_index=best,
        baseline_best_tied=tied, selected_index=selected, applied=applied, selected_tail_checks=checks(table[selected]['hospitals']),
        wait_epsilon_seconds=1e-9,
        tie_rule='baseline when best-tied; otherwise first original-pool row within 1e-9 of global feasible minimum',
        feasibility_rule='unchanged original pair P/cost feasibility and no elimination of a tail last public zero-pending option',
        scope='all original rows; current public options only; no changes to counts, model, grouping, order or H fallback')
    return decisions, evidence


def replay(hospitals=3):
    events, registry, context, members, tails, original = setup(hospitals=hospitals)
    outputs, evidence = output(members, context, tails, original)
    h2 = 'H03' if hospitals == 3 else 'H01'; end_b = 20 if hospitals == 3 else 30
    assert [d['send'] for d in outputs] == [['H01'], [h2]]
    group = dict(id=1, now=audit.EPOCH, event_prefix_max=6, decision_prefix_max=0, revision=0,
        context=context, registry_snapshot=registry, members=members, tail_members=tails,
        outputs=outputs, evidence=evidence, db_decision_ids=[1, 2], validated_before_write=True)
    for i, (pid, hid) in enumerate((('A', 'H01'), ('B', h2))):
        events += [dict(id=7+i*2, at=audit.EPOCH, type='inquiry_sent', patient_id=pid, hospital_id=hid, data=dict(inquiry_id=i+1)),
            dict(id=8+i*2, at=audit.EPOCH, type='inquiry_workflow_batch', patient_id=pid, data={})]
    fallback = deepcopy(tails[0]); fallback['inputs']['pending'] = [
        dict(hospital=h, patient=p, submitted_at=audit.EPOCH) for p, h in (('A', 'H01'), ('B', h2))]
    fallback['inputs']['time_context'].update(event_prefix_max=10, decision_prefix_max=2)
    fallback['workflow_snapshot']['apply_snapshot']['revision'] = 2
    fallback_output = horizon_output(fallback['inputs'], dict(H01=None, H02=0))
    fallback_output['evidence'].update(policy=audit.NAME,
        joint_first=dict(schema_version=1, mode='horizon_fallback', current_only=False),
        joint_tail=dict(schema_version=1, mode='horizon_fallback', guard_evaluated=False),
        joint_reselect=dict(schema_version=1, mode='horizon_fallback', reselection_evaluated=False))
    events += [dict(id=11, at=audit.EPOCH, type='inquiry_sent', patient_id='C', hospital_id='H02', data=dict(inquiry_id=3)),
        dict(id=12, at=audit.EPOCH, type='inquiry_workflow_batch', patient_id='C', data={})]
    for eid, pid, hid, iid, at in ((13, 'A', 'H01', 1, 10), (15, 'C', 'H02', 3, 12), (17, 'B', h2, 2, end_b)):
        events += [dict(id=eid, at=audit.EPOCH+at, type='inquiry_responded', patient_id=pid, hospital_id=hid,
                data=dict(inquiry_id=iid, action='accept')),
            dict(id=eid+1, at=audit.EPOCH+at, type='destination_confirmed', patient_id=pid, hospital_id=hid, data=dict(inquiry_id=iid))]
    travel = {f'H{i+1:02d}': 100+i*20 for i in range(hospitals)}
    scenario = dict(id='synthetic-reselection-audit-only',
        patients=[dict(id=p, at=0, travel_seconds=deepcopy(travel if p != 'C' else dict(H01=100, H02=120))) for p in ('A', 'B', 'C')],
        hospitals=[dict(id=h, workers=1) for h in travel],
        potentials=[dict(patient_id=p, hospital_id=h, service_seconds=20 if p == 'B' else 10 if h == 'H01' else 12,
            no_response=False, accept=True) for p in ('A', 'B', 'C') for h in travel])
    digest = hashlib.sha256(json.dumps(scenario, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    calls, decisions = [], []
    for i, (m, d) in enumerate(zip(members+[fallback], outputs+[fallback_output])):
        call = dict(patient_id=m['patient_id'], at=0, name=audit.NAME, inputs=deepcopy(m['inputs']), output=deepcopy(d),
            workflow_snapshot=deepcopy(m['workflow_snapshot']), db_decision_id=i+1, db_event_prefix_max=6 if i < 2 else 10)
        if i < 2: call.update(joint_group_id=1, joint_member_index=i)
        calls.append(call)
        decisions.append(dict(id=i+1, at=audit.EPOCH, patient_id=m['patient_id'], policy_name=audit.NAME,
            job_version=1, hospital_ids=deepcopy(d['send']), reason=d['reason'], evidence=deepcopy(d['evidence'])))
    def trace(at, typ, pid, hid, iid, **extra):
        return dict(at=at, type=typ, patient_id=pid, hospital_id=hid, inquiry_id=iid, **extra)
    traces = [trace(0, 'queue_added', 'A', 'H01', 1), trace(0, 'queue_added', 'B', h2, 2), trace(0, 'queue_added', 'C', 'H02', 3),
        trace(0, 'review_started', 'A', 'H01', 1, service_seconds=10, due_at=10)]
    if hospitals == 3: traces.append(trace(0, 'review_started', 'B', h2, 2, service_seconds=20, due_at=20))
    traces += [trace(0, 'review_started', 'C', 'H02', 3, service_seconds=12, due_at=12),
        trace(10, 'review_finished', 'A', 'H01', 1, started_at=0, service_seconds=10),
        trace(10, 'reply_accept', 'A', 'H01', 1), trace(10, 'destination_confirmed', 'A', 'H01', 1)]
    if hospitals == 2: traces.append(trace(10, 'review_started', 'B', h2, 2, service_seconds=20, due_at=30))
    traces += [trace(12, 'review_finished', 'C', 'H02', 3, started_at=0, service_seconds=12),
        trace(12, 'reply_accept', 'C', 'H02', 3), trace(12, 'destination_confirmed', 'C', 'H02', 3),
        trace(end_b, 'review_finished', 'B', h2, 2, started_at=0 if hospitals == 3 else 10, service_seconds=20),
        trace(end_b, 'reply_accept', 'B', h2, 2), trace(end_b, 'destination_confirmed', 'B', h2, 2)]
    patients = [dict(id=p, created_at=0, selected_at=at, selection_seconds=at, travel_seconds=t,
        estimated_arrival_seconds=at+t, destination_hospital_id=h, failed=False, inquiry_count=1, contacted_hospitals=[h])
        for p, h, at, t in (('A', 'H01', 10, 100), ('B', h2, end_b, travel[h2]), ('C', 'H02', 12, 120))]
    w = 22+end_b; j = sum(p['estimated_arrival_seconds'] for p in patients)
    branch = dict(policy=audit.NAME, rule='immediate', environment_sha256=digest, snapshot_sha256='0'*64,
        instrumentation=deepcopy(audit.NEW_INSTRUMENTATION), joint_groups=[group], db_events=events,
        db_inquiries=[dict(id=i+1, patient_id=p, hospital_id=h) for i, (p, h) in enumerate((('A', 'H01'), ('B', h2), ('C', 'H02')))],
        db_decisions=decisions, policy_calls=calls, patients=patients, trace=traces, holds=[],
        metrics=dict(patient_count=3, selected_count=3, failed_count=0, selection_seconds_mean=w/3,
            selection_wait_capped_mean=w/3, inquiries_per_patient=1, estimated_arrival_seconds_mean=j/3,
            review_work_seconds=42, review_started_count=3, cancelled_before_start_count=0, reply_suppressed_count=0))
    return dict(schema_version=1, scenario=scenario, environment_sha256=digest, initial_snapshot_sha256='0'*64,
        arms=[[audit.NAME, 'immediate']], branches=[branch])


class IndependentConstrainedSelectionTests(unittest.TestCase):
    def test_blocked_original_winner_selects_a_different_improving_feasible_row(self):
        _, _, context, members, tails, original = setup(); before = deepcopy(original)
        decisions, evidence = output(members, context, tails, original)
        checked = audit.audit_pair(members, context, tails, decisions, evidence)
        self.assertEqual(original, before)
        self.assertTrue(original['evidence']['vetoed'])
        self.assertEqual([d['send'] for d in original['decisions']], [['H01'], ['H01']])
        self.assertEqual([d['send'] for d in decisions], [['H01'], ['H03']])
        self.assertTrue(evidence['applied']); self.assertEqual(checked['constrained_rows_checked'], 9)
        self.assertEqual(checked['tail_reselected_groups'], 1)
        self.assertEqual(evidence['selected_tail_checks'][0]['selected_zero_pending_hospitals'], ['H02'])

    def test_no_tail_safe_strict_improvement_keeps_baseline(self):
        _, _, context, members, tails, original = setup(hospitals=2)
        decisions, evidence = output(members, context, tails, original)
        self.assertFalse(evidence['applied']); self.assertEqual(evidence['selected_index'], 0)
        self.assertEqual([d['send'] for d in decisions], [['H01'], ['H01']])
        audit.audit_pair(members, context, tails, decisions, evidence)

    def test_no_new_zero_loss_or_no_tails_preserves_original_winner(self):
        for mode in ('remaining_zero', 'no_tail'):
            _, _, context, members, tails, original = setup(tail_hospitals=3)
            if mode == 'no_tail':
                context['eligible'] = context['eligible'][:2]; tails = []
                # Rebuild genuine no-tail proposal, not a rewritten old winner.
                from test_inquiry_lab_joint_first_audit import pair_output
                from test_inquiry_lab_joint_tail_audit import audit as tail_audit
                tc = audit.alias_context(context); tm = [audit.alias_member(m) for m in members]
                props = original['evidence']['original_joint_proposal']['evidence']['baseline_proposals']
                d, e = pair_output([tail_audit.alias_member(m) for m in tm], tail_audit.alias_context(tc), props)
                td, te = tail_output(tm, tc, [], dict(decisions=d, evidence=e)); original = dict(decisions=td, evidence=te)
            decisions, evidence = output(members, context, tails, original)
            audit.audit_pair(members, context, tails, decisions, evidence)
            self.assertEqual([d['send'] for d in decisions], [['H01'], ['H02']])

    def test_all_rows_are_checked_even_when_original_joint_did_not_apply(self):
        _, _, context, members, tails, original = setup(hospitals=1, tail_hospitals=1)
        decisions, evidence = output(members, context, tails, original)
        self.assertFalse(original['evidence']['original_joint_proposal']['evidence']['applied'])
        self.assertEqual(len(evidence['row_feasibility']), 1)
        self.assertEqual(audit.audit_pair(members, context, tails, decisions, evidence)['constrained_rows_checked'], 1)

    def test_global_minimum_tolerance_is_not_a_running_pairwise_tie(self):
        _, _, _, members, _, _ = setup()
        joint = dict(baseline_index=3, baseline_combination=dict(hospitals=[[], []]), combinations=[
            dict(hospitals=[[], []], feasible=True, sum_capped_wait_seconds=w)
            for w in (100+1.5e-9, 100+.75e-9, 100, 200)])
        result = audit.constrained_table(members, [], joint)
        self.assertEqual(result['best_index'], 1); self.assertEqual(result['selected_index'], 1)

    def test_baseline_best_tie_has_priority_over_an_earlier_enumerated_row(self):
        _, _, _, members, _, _ = setup()
        joint = dict(baseline_index=1, baseline_combination=dict(hospitals=[[], []]), combinations=[
            dict(hospitals=[[], []], feasible=True, sum_capped_wait_seconds=w) for w in (100, 100+.5e-9)])
        result = audit.constrained_table(members, [], joint)
        self.assertEqual(result['best_index'], 0); self.assertTrue(result['baseline_best_tied'])
        self.assertEqual(result['selected_index'], 1); self.assertFalse(result['applied'])

    def test_rows_and_nested_proposals_cannot_be_omitted_or_rewritten(self):
        for change in ('joint_table', 'tail_winner', 'tail_veto', 'row_omit', 'joint_flag', 'tail_flag', 'blocked',
                'selected_index', 'minimum', 'selected_send', 'baseline_tie', 'tail_pool', 'selected_counts'):
            _, _, context, members, tails, original = setup(); decisions, evidence = output(members, context, tails, original)
            inner = evidence['original_tail_proposal']; joint = inner['evidence']['original_joint_proposal']['evidence']
            if change == 'joint_table': joint['combinations'].pop()
            elif change == 'tail_winner': inner['decisions'][1]['send'] = ['H02']
            elif change == 'tail_veto': inner['evidence']['vetoed'] = False
            elif change == 'row_omit': evidence['row_feasibility'].pop()
            elif change == 'joint_flag': evidence['row_feasibility'][0]['joint_feasible'] = False
            elif change == 'tail_flag': evidence['row_feasibility'][1]['tail_feasible'] = True
            elif change == 'blocked': evidence['row_feasibility'][1]['blocked_tail_patients'] = []
            elif change == 'selected_index': evidence['selected_index'] = 0
            elif change == 'minimum': evidence['minimum_feasible_wait_seconds'] += 1
            elif change == 'selected_send': decisions[1]['send'] = ['H01']
            elif change == 'baseline_tie': evidence['baseline_best_tied'] = True
            elif change == 'tail_pool': evidence['tail_opportunities'][0]['hospitals'].pop()
            else: evidence['selected_tail_checks'][0]['selected_pending_counts']['H02'] = 1
            with self.subTest(change=change), self.assertRaises(audit.JointReselectAuditError):
                audit.audit_pair(members, context, tails, decisions, evidence)


class IndependentActualTraceTests(unittest.TestCase):
    def test_actual_reselected_and_baseline_full_replays_have_independent_accounting(self):
        for hospitals, wait, arrival in ((3, 42, 402), (2, 52, 372)):
            raw = replay(hospitals); before = deepcopy(raw); checked = audit.audit_replay(raw)['branch_audits'][0]
            self.assertEqual(raw, before); self.assertEqual(checked['tail_public_provenance_checked'], 1)
            self.assertEqual(checked['total_capped_selection_wait_seconds'], wait)
            self.assertEqual(checked['total_arrival_failure_cost_seconds'], arrival)
            self.assertEqual(checked['review_work_seconds'], 42)

    def test_public_tail_roster_candidates_order_and_ledger_are_bound(self):
        for change in ('omit_tail', 'omit_offer', 'extra_offer', 'hidden', 'ledger', 'version', 'prefix', 'eligibility'):
            raw = replay(); g = raw['branches'][0]['joint_groups'][0]; t = g['tail_members'][0]
            if change == 'omit_tail': g['tail_members'] = []
            elif change == 'omit_offer': t['inputs']['candidates'].pop()
            elif change == 'extra_offer': t['inputs']['candidates'].append(dict(hospital_id='H99', travel=dict(duration_seconds=999)))
            elif change == 'hidden': t['inputs']['workers'] = 1
            elif change == 'ledger': t['inputs']['pending'].append(dict(hospital='H01', patient='Q', submitted_at=audit.EPOCH))
            elif change == 'version': t['workflow_snapshot']['job']['version'] += 1
            elif change == 'prefix': t['inputs']['time_context']['event_prefix_max'] += 1
            else: t['inputs']['candidates'][0]['resources_enough'] = False
            with self.subTest(change=change), self.assertRaises(audit.JointReselectAuditError): audit.audit_replay(raw)
        events, _, _, _, tails, _ = setup(); t = tails[0]
        t['inputs']['candidates'] += [dict(hospital_id=f'H{i:02d}', travel=dict(duration_seconds=100+(i-1)*20),
            resources_enough=True) for i in range(3, 8)]
        scenario = dict(patients=[dict(id='C', at=0, travel_seconds={r['hospital_id']: r['travel']['duration_seconds']
            for r in t['inputs']['candidates']})])
        self.assertEqual(audit.audit_member_provenance(scenario, events, t, 6, 0), 1)
        cs = t['inputs']['candidates']; cs[5], cs[6] = cs[6], cs[5]
        with self.assertRaisesRegex(audit.JointReselectAuditError, 'order/resource'):
            audit.audit_member_provenance(scenario, events, t, 6, 0)

    def test_actual_journal_fifo_work_fallback_and_atomic_provenance_are_not_nested_proposals(self):
        for change in ('journal', 'service', 'wait', 'work', 'fallback', 'beforewrite', 'revision', 'output'):
            raw = replay(); b = raw['branches'][0]
            if change == 'journal': next(e for e in b['db_events'] if e['id'] == 9)['hospital_id'] = 'H01'
            elif change == 'service': b['trace'][4]['service_seconds'] += 1
            elif change == 'wait': b['patients'][1]['selection_seconds'] += 1
            elif change == 'work': b['metrics']['review_work_seconds'] += 1
            elif change == 'fallback':
                b['policy_calls'][2]['output']['evidence']['joint_reselect']['reselection_evaluated'] = True
                b['db_decisions'][2]['evidence'] = deepcopy(b['policy_calls'][2]['output']['evidence'])
            elif change == 'beforewrite': b['joint_groups'][0]['validated_before_write'] = False
            elif change == 'revision': b['joint_groups'][0]['tail_members'][0]['workflow_snapshot']['apply_snapshot']['revision'] += 1
            else: b['policy_calls'][1]['output']['send'] = ['H01']
            with self.subTest(change=change), self.assertRaises(audit.JointReselectAuditError): audit.audit_replay(raw)

    def test_clean_import_has_no_producer_runner_context_or_solver_execution(self):
        code = ("import json,sys; import inquiry_lab.joint_reselect_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server','simulation','ortools')) or n.endswith(('_policy','_runner','_context','_experiment')))))")
        result = subprocess.check_output([sys.executable, '-B', '-c', code], cwd=ROOT, text=True)
        self.assertEqual(json.loads(result), [])


if __name__ == '__main__': unittest.main()
