"""Synthetic saved-proposal, tail-prefix and actual-send mutation tests only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from inquiry_lab import joint_tail_audit as audit
from test_inquiry_lab_joint_first_audit import setup as first_setup, pair_output as first_pair_output
from test_inquiry_lab_horizon_audit import output as horizon_output

ROOT = Path(__file__).resolve().parents[2]


def setup(hospitals=2):
    events, registry, context, members, _ = first_setup(hospitals=hospitals)
    for event in events:
        if event['type'] == 'inquiry_workflow_started': event['data']['policy_name'] = audit.NAME
    for job in registry['jobs']: job['policy_name'] = audit.NAME
    for member in members: member['workflow_snapshot']['job']['policy_name'] = audit.NAME
    for row in context['eligible']: row['job']['policy_name'] = audit.NAME
    tail = deepcopy(members[0]); tail['patient_id'] = 'C'
    snap = tail['workflow_snapshot']; snap['apply_snapshot']['patient_id'] = snap['job']['patient_id'] = 'C'
    t = tail['inputs']['time_context']; t['patient_id'] = 'C'
    t['source']['workflow']['patient_id'] = 'C'
    t['source']['patient_created_event'].update(patient_id='C', id=5)
    t['source']['workflow_start_event'].update(patient_id='C', id=6)
    events += [dict(id=5, at=audit.EPOCH, type='patient_created', patient_id='C', data={}),
        dict(id=6, at=audit.EPOCH, type='inquiry_workflow_started', patient_id='C',
            data=dict(deadline_seconds=300, policy_name=audit.NAME))]
    registry['patients'].append(dict(id='C', state='WAITING', version=1))
    registry['jobs'].append(dict(registry['jobs'][0], patient_id='C'))
    context['eligible'].append(dict(patient=deepcopy(registry['patients'][-1]),
        job=deepcopy(registry['jobs'][-1]), inquiry_count=0, decision_count=0))
    context['event_prefix_max'] = 6
    for member in members+[tail]: member['inputs']['time_context']['event_prefix_max'] = 6
    for member in members+[tail]:
        for candidate in member['inputs']['candidates']: candidate['resources_enough'] = True
    plans = [{f'H{i+1:02d}': 0 if i == 0 else None for i in range(hospitals)} for _ in members]
    proposals = [horizon_output(m['inputs'], p) for m, p in zip(members, plans)]
    original_decisions, original_evidence = first_pair_output(
        [audit.alias_member(m) for m in members], audit.alias_context(context), proposals)
    return events, registry, context, members, [tail], dict(decisions=original_decisions, evidence=original_evidence)


def output(members, context, tails, original):
    original = deepcopy(original); original_ev = original['evidence']
    evaluated = original_ev['applied']
    checks = audit.tail_opportunities(members, tails, original) if evaluated else []
    triggered = [r['patient_id'] for r in checks if r['triggered']]
    vetoed = evaluated and bool(triggered)
    selected = original_ev['baseline_index'] if vetoed else original_ev['selected_index']
    decisions = []
    for index, proposal in enumerate(original['decisions']):
        actual = deepcopy(proposal)
        if vetoed:
            actual = deepcopy(original_ev['baseline_proposals'][index])
            baseline = original_ev['combinations'][original_ev['baseline_index']]
            send = baseline['hospitals'][index]
            actual['send'] = list(send); actual['reason'] = 'joint_tail_veto_baseline_current_only'
            ev = actual['evidence']; ev['sent_hospitals'] = list(send)
            ev['planned_expected_cost'] = round(baseline['member_metrics'][index]['total_cost_seconds'], 3)
            for row in ev['candidate_plan']: row['planned_offset_seconds'] = 0 if row['hospital'] in send else None
            ev['joint_first'] = dict(schema_version=1, mode='pair_current_only', member_index=index,
                current_only=True, applied=False, baseline_send=list(send), selected_send=list(send),
                baseline_metrics=original_ev['baseline_combination']['member_metrics'][index],
                selected_metrics=baseline['member_metrics'][index],
                constraint_reference='common-prefix simultaneous immediate-only H sends')
        actual['evidence']['policy'] = audit.NAME
        actual['evidence']['joint_tail'] = dict(schema_version=1, mode='pair_tail_guard',
            guard_evaluated=evaluated, vetoed=vetoed, triggered_tail_patients=triggered,
            original_joint_applied=original_ev['applied'], selected_index=selected)
        decisions.append(actual)
    evidence = dict(schema_version=1, rule=audit.RULE, base_policy=audit.BASE_POLICY,
        base_source_sha256=audit.BASE_SOURCE_SHA256,
        identity_projection=dict(source_policy=audit.NAME, calculation_policy=audit.BASE_POLICY,
            fields=['joint_context.eligible[].job.policy_name', 'members[].workflow_snapshot.job.policy_name'],
            copied_only=True, public_identity_unchanged=True),
        joint_context=deepcopy(context), original_joint_proposal=original, guard_evaluated=evaluated,
        tail_checks=checks, triggered_tail_patients=triggered, vetoed=vetoed, actual_selected_index=selected,
        tail_pool_rule='unchanged normalized nearest min(6,max_new) current candidate pool',
        pending_scope='current unresolved public inquiries plus each proposed immediate pair inquiry once',
        zero_scope='public pending count only; not idle workers, beds or future availability',
        fallback_rule='whole current pair baseline; no second-best search; unchanged when original applied=False')
    return decisions, evidence


def replay():
    events, registry, context, members, tails, original = setup()
    outputs, evidence = output(members, context, tails, original)
    assert evidence['vetoed'] and [o['send'] for o in outputs] == [['H01'], ['H01']]
    group = dict(id=1, now=audit.EPOCH, event_prefix_max=6, decision_prefix_max=0, revision=0,
        context=context, registry_snapshot=registry, members=members, tail_members=tails,
        outputs=outputs, evidence=evidence, db_decision_ids=[1, 2], validated_before_write=True)
    for index, pid in enumerate(('A', 'B')):
        events += [dict(id=7+index*2, at=audit.EPOCH, type='inquiry_sent', patient_id=pid, hospital_id='H01',
                data=dict(inquiry_id=index+1)),
            dict(id=8+index*2, at=audit.EPOCH, type='inquiry_workflow_batch', patient_id=pid, data={})]
    fallback = deepcopy(tails[0]); fallback['inputs']['pending'] = [
        dict(hospital='H01', patient=p, submitted_at=audit.EPOCH) for p in ('A', 'B')]
    fallback['inputs']['time_context'].update(event_prefix_max=10, decision_prefix_max=2)
    fallback['workflow_snapshot']['apply_snapshot']['revision'] = 2
    fallback_output = horizon_output(fallback['inputs'], dict(H01=None, H02=0))
    fallback_output['evidence'].update(policy=audit.NAME,
        joint_first=dict(schema_version=1, mode='horizon_fallback', current_only=False),
        joint_tail=dict(schema_version=1, mode='horizon_fallback', guard_evaluated=False))
    events += [dict(id=11, at=audit.EPOCH, type='inquiry_sent', patient_id='C', hospital_id='H02', data=dict(inquiry_id=3)),
        dict(id=12, at=audit.EPOCH, type='inquiry_workflow_batch', patient_id='C', data={})]
    for eid, pid, hid, iid, at in ((13, 'A', 'H01', 1, 10), (15, 'C', 'H02', 3, 12), (17, 'B', 'H01', 2, 30)):
        events += [dict(id=eid, at=audit.EPOCH+at, type='inquiry_responded', patient_id=pid, hospital_id=hid,
                data=dict(inquiry_id=iid, action='accept')),
            dict(id=eid+1, at=audit.EPOCH+at, type='destination_confirmed', patient_id=pid, hospital_id=hid, data=dict(inquiry_id=iid))]
    scenario = dict(id='synthetic-tail-audit-only',
        patients=[dict(id=p, at=0, travel_seconds=dict(H01=100, H02=120)) for p in ('A', 'B', 'C')],
        hospitals=[dict(id=h, workers=1) for h in ('H01', 'H02')],
        potentials=[dict(patient_id=p, hospital_id=h, service_seconds=(20 if p == 'B' else 10) if h == 'H01' else 12,
            no_response=False, accept=True) for p in ('A', 'B', 'C') for h in ('H01', 'H02')])
    digest = hashlib.sha256(json.dumps(scenario, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    calls, decisions = [], []
    for index, (member, decision) in enumerate(zip(members+[fallback], outputs+[fallback_output])):
        call = dict(patient_id=member['patient_id'], at=0, name=audit.NAME,
            inputs=deepcopy(member['inputs']), output=deepcopy(decision),
            workflow_snapshot=deepcopy(member['workflow_snapshot']), db_decision_id=index+1,
            db_event_prefix_max=6 if index < 2 else 10)
        if index < 2: call.update(joint_group_id=1, joint_member_index=index)
        calls.append(call)
        decisions.append(dict(id=index+1, at=audit.EPOCH, patient_id=member['patient_id'], policy_name=audit.NAME,
            job_version=1, hospital_ids=deepcopy(decision['send']), reason=decision['reason'], evidence=deepcopy(decision['evidence'])))
    def trace(at, typ, pid, hid, iid, **extra):
        return dict(at=at, type=typ, patient_id=pid, hospital_id=hid, inquiry_id=iid, **extra)
    traces = [trace(0, 'queue_added', 'A', 'H01', 1), trace(0, 'queue_added', 'B', 'H01', 2), trace(0, 'queue_added', 'C', 'H02', 3),
        trace(0, 'review_started', 'A', 'H01', 1, service_seconds=10, due_at=10),
        trace(0, 'review_started', 'C', 'H02', 3, service_seconds=12, due_at=12),
        trace(10, 'review_finished', 'A', 'H01', 1, started_at=0, service_seconds=10),
        trace(10, 'reply_accept', 'A', 'H01', 1), trace(10, 'destination_confirmed', 'A', 'H01', 1),
        trace(10, 'review_started', 'B', 'H01', 2, service_seconds=20, due_at=30),
        trace(12, 'review_finished', 'C', 'H02', 3, started_at=0, service_seconds=12),
        trace(12, 'reply_accept', 'C', 'H02', 3), trace(12, 'destination_confirmed', 'C', 'H02', 3),
        trace(30, 'review_finished', 'B', 'H01', 2, started_at=10, service_seconds=20),
        trace(30, 'reply_accept', 'B', 'H01', 2), trace(30, 'destination_confirmed', 'B', 'H01', 2)]
    patients = [dict(id=p, created_at=0, selected_at=at, selection_seconds=at, travel_seconds=travel,
        estimated_arrival_seconds=at+travel, destination_hospital_id=h, failed=False, inquiry_count=1, contacted_hospitals=[h])
        for p, h, at, travel in (('A', 'H01', 10, 100), ('B', 'H01', 30, 100), ('C', 'H02', 12, 120))]
    branch = dict(policy=audit.NAME, rule='immediate', environment_sha256=digest, snapshot_sha256='0'*64,
        instrumentation=deepcopy(audit.NEW_INSTRUMENTATION), joint_groups=[group], db_events=events,
        db_inquiries=[dict(id=i+1, patient_id=p, hospital_id=h) for i, (p, h) in enumerate((('A', 'H01'), ('B', 'H01'), ('C', 'H02')))],
        db_decisions=decisions, policy_calls=calls, patients=patients, trace=traces, holds=[],
        metrics=dict(patient_count=3, selected_count=3, failed_count=0, selection_seconds_mean=52/3,
            selection_wait_capped_mean=52/3, inquiries_per_patient=1, estimated_arrival_seconds_mean=124,
            review_work_seconds=42, review_started_count=3, cancelled_before_start_count=0, reply_suppressed_count=0))
    return dict(schema_version=1, scenario=scenario, environment_sha256=digest, initial_snapshot_sha256='0'*64,
        arms=[[audit.NAME, 'immediate']], branches=[branch])


class IndependentTailGuardTests(unittest.TestCase):
    def test_last_public_empty_option_veto_restores_whole_baseline_and_keeps_original(self):
        _, _, context, members, tails, original = setup(); before = deepcopy(original)
        decisions, evidence = output(members, context, tails, original)
        checked = audit.audit_pair(members, context, tails, decisions, evidence)
        self.assertEqual(original, before)
        self.assertEqual(checked['original_joint_groups_applied'], 1)
        self.assertEqual(checked['joint_groups_applied'], 0)
        self.assertEqual(checked['tail_vetoed_groups'], 1)
        self.assertEqual([d['send'] for d in evidence['original_joint_proposal']['decisions']], [['H01'], ['H02']])
        self.assertEqual([d['send'] for d in decisions], [['H01'], ['H01']])
        self.assertEqual(evidence['tail_checks'][0]['baseline_zero_pending_hospitals'], ['H02'])
        self.assertEqual(evidence['tail_checks'][0]['winner_zero_pending_hospitals'], [])

    def test_nontrigger_and_unapplied_original_preserve_the_complete_original_decisions(self):
        for mode in ('still_empty', 'no_original_apply', 'no_tail'):
            _, _, context, members, tails, original = setup(hospitals=1 if mode == 'no_original_apply' else 2)
            if mode == 'still_empty':
                tails[0]['inputs']['candidates'].append(dict(hospital_id='H03', travel=dict(duration_seconds=140)))
            elif mode == 'no_tail':
                context['eligible'] = context['eligible'][:2]; tails = []
                # The original proposal context must match this genuine two-person roster.
                props = original['evidence']['baseline_proposals']
                d, e = first_pair_output([audit.alias_member(m) for m in members], audit.alias_context(context), props)
                original = dict(decisions=d, evidence=e)
            decisions, evidence = output(members, context, tails, original)
            self.assertFalse(evidence['vetoed'])
            audit.audit_pair(members, context, tails, decisions, evidence)
            for actual, expected in zip(decisions, original['decisions']):
                normalized = deepcopy(actual); normalized['evidence'].pop('joint_tail')
                normalized['evidence']['policy'] = audit.BASE_POLICY
                self.assertEqual(normalized, expected)

    def test_already_no_zero_options_is_not_a_new_loss(self):
        _, _, context, members, tails, original = setup()
        tails[0]['inputs']['candidates'] = tails[0]['inputs']['candidates'][:1]
        decisions, evidence = output(members, context, tails, original)
        self.assertFalse(evidence['tail_checks'][0]['triggered'])
        audit.audit_pair(members, context, tails, decisions, evidence)

    def test_only_original_nearest_six_count_and_public_stale_pending_still_counts(self):
        _, _, context, members, tails, original = setup()
        pending = [dict(hospital=f'H{i:02d}', patient='Q', submitted_at=audit.EPOCH-200) for i in range(3, 7)]
        for m in members+tails: m['inputs']['pending'] = deepcopy(pending)
        tails[0]['inputs']['candidates'] += [dict(hospital_id=f'H{i:02d}', travel=dict(duration_seconds=100+i*20)) for i in range(3, 9)]
        props = [horizon_output(m['inputs'], dict(H01=0, H02=None)) for m in members]
        d, e = first_pair_output([audit.alias_member(m) for m in members], audit.alias_context(context), props)
        original = dict(decisions=d, evidence=e)
        decisions, evidence = output(members, context, tails, original)
        self.assertEqual(evidence['tail_checks'][0]['candidate_pool'], [f'H{i:02d}' for i in range(1, 7)])
        self.assertTrue(evidence['vetoed'])
        audit.audit_pair(members, context, tails, decisions, evidence)

    def test_original_winner_table_cannot_be_rewritten_to_justify_actual_baseline(self):
        for change in ('winner', 'original_applied', 'table', 'new_index', 'actual_winner', 'one_member', 'counts'):
            _, _, context, members, tails, original = setup(); decisions, evidence = output(members, context, tails, original)
            if change == 'winner': evidence['original_joint_proposal']['decisions'][1]['send'] = ['H01']
            elif change == 'original_applied': evidence['original_joint_proposal']['evidence']['applied'] = False
            elif change == 'table': evidence['original_joint_proposal']['evidence']['combinations'].pop()
            elif change == 'new_index': evidence['actual_selected_index'] = evidence['original_joint_proposal']['evidence']['selected_index']
            elif change == 'actual_winner': decisions[1]['send'] = ['H02']
            elif change == 'one_member': decisions[0]['evidence']['joint_tail']['vetoed'] = False
            else: evidence['tail_checks'][0]['winner_pending_counts']['H02'] = 0
            with self.subTest(change=change), self.assertRaises(audit.JointTailAuditError):
                audit.audit_pair(members, context, tails, decisions, evidence)


class IndependentActualTraceTests(unittest.TestCase):
    def test_tail_candidate_six_seven_order_swap_cannot_change_the_guard_pool(self):
        events, _, _, _, tails, _ = setup(); tail = tails[0]
        tail['inputs']['candidates'] += [dict(hospital_id=f'H{i:02d}',
            travel=dict(duration_seconds=100+(i-1)*20), resources_enough=True) for i in range(3, 8)]
        travel = {r['hospital_id']: r['travel']['duration_seconds'] for r in tail['inputs']['candidates']}
        scenario = dict(patients=[dict(id='C', at=0, travel_seconds=travel)])
        self.assertEqual(audit.audit_member_provenance(scenario, events, tail, 6, 0), 1)
        candidates = tail['inputs']['candidates']; candidates[5], candidates[6] = candidates[6], candidates[5]
        with self.assertRaisesRegex(audit.JointTailAuditError, 'order/resource'):
            audit.audit_member_provenance(scenario, events, tail, 6, 0)

    def test_actual_vetoed_sends_fifo_all_patient_W_J_and_work_are_checked(self):
        raw = replay(); before = deepcopy(raw); checked = audit.audit_replay(raw)['branch_audits'][0]
        self.assertEqual(raw, before)
        self.assertEqual(checked['tail_vetoed_groups'], 1)
        self.assertEqual(checked['tail_public_provenance_checked'], 1)
        self.assertEqual(checked['total_capped_selection_wait_seconds'], 52)
        self.assertEqual(checked['total_arrival_failure_cost_seconds'], 372)
        self.assertEqual(checked['review_work_seconds'], 42)

    def test_tail_roster_offer_omission_extra_or_hidden_and_ledger_forgery_rejected(self):
        for change in ('omit_tail', 'omit_offer', 'extra_offer', 'hidden', 'ledger', 'version', 'prefix', 'patient', 'eligibility'):
            raw = replay(); group = raw['branches'][0]['joint_groups'][0]; tail = group['tail_members'][0]
            if change == 'omit_tail': group['tail_members'] = []
            elif change == 'omit_offer': tail['inputs']['candidates'].pop()
            elif change == 'extra_offer': tail['inputs']['candidates'].append(dict(hospital_id='H99', travel=dict(duration_seconds=999)))
            elif change == 'hidden': tail['inputs']['workers'] = 1
            elif change == 'ledger': tail['inputs']['pending'].append(dict(hospital='H01', patient='Q', submitted_at=audit.EPOCH))
            elif change == 'version': tail['workflow_snapshot']['job']['version'] += 1
            elif change == 'prefix': tail['inputs']['time_context']['event_prefix_max'] = 7
            elif change == 'patient': tail['patient_id'] = 'future'
            else: tail['inputs']['candidates'][0]['resources_enough'] = False
            with self.subTest(change=change), self.assertRaises(audit.JointTailAuditError): audit.audit_replay(raw)

    def test_actual_send_journal_is_not_replaced_with_nested_original_winner(self):
        raw = replay(); branch = raw['branches'][0]
        # Replacing an actual B query with the original winning hospital must
        # fail even though the original proposal itself chose H02 for B.
        next(e for e in branch['db_events'] if e['id'] == 9)['hospital_id'] = 'H02'
        with self.assertRaises(audit.JointTailAuditError): audit.audit_replay(raw)

    def test_actual_FIFO_wait_work_fallback_and_transaction_provenance_mutations_rejected(self):
        for change in ('service', 'work', 'wait', 'fallback', 'beforewrite', 'revision'):
            raw = replay(); branch = raw['branches'][0]
            if change == 'service': branch['trace'][3]['service_seconds'] += 1
            elif change == 'work': branch['metrics']['review_work_seconds'] += 1
            elif change == 'wait': branch['patients'][2]['selection_seconds'] += 1
            elif change == 'fallback':
                branch['policy_calls'][2]['output']['evidence']['joint_tail']['guard_evaluated'] = True
                branch['db_decisions'][2]['evidence'] = deepcopy(branch['policy_calls'][2]['output']['evidence'])
            elif change == 'beforewrite': branch['joint_groups'][0]['validated_before_write'] = False
            else: branch['joint_groups'][0]['tail_members'][0]['workflow_snapshot']['apply_snapshot']['revision'] += 1
            with self.subTest(change=change), self.assertRaises(audit.JointTailAuditError): audit.audit_replay(raw)

    def test_clean_import_has_no_producer_or_server_execution_path(self):
        code = ("import json,sys; import inquiry_lab.joint_tail_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server','simulation','ortools')) or n.endswith(('_policy','_runner','_context','_experiment')))))")
        result = subprocess.check_output([sys.executable, '-B', '-c', code], cwd=ROOT, text=True)
        self.assertEqual(json.loads(result), [])


if __name__ == '__main__': unittest.main()
