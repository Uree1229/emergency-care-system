"""Synthetic registry, finite-pair mathematics and full-trace mutation tests.

No producer policy, runner, scenario generator, saved performance raw or solver
is imported/executed. H evidence is an explicitly synthetic recorded plan.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from inquiry_lab import joint_first_audit as audit
from test_inquiry_lab_horizon_audit import output as horizon_output
from test_inquiry_lab_horizon_audit import replay as horizon_replay

ROOT = Path(__file__).resolve().parents[2]


def setup(hospitals=2, count=1, horizon=300):
    now = audit.EPOCH+300-horizon
    events, people, jobs = [], [], []
    for index, pid in enumerate(('A', 'B')):
        events += [dict(id=index*2+1, at=audit.EPOCH, type='patient_created', patient_id=pid, data={}),
            dict(id=index*2+2, at=audit.EPOCH, type='inquiry_workflow_started', patient_id=pid,
                data=dict(deadline_seconds=300, policy_name=audit.NAME))]
        people.append(dict(id=pid, state='WAITING', version=1))
        jobs.append(dict(patient_id=pid, version=1, started_at=audit.EPOCH, deadline_at=audit.EPOCH+300,
            next_evaluation_at=audit.EPOCH, enabled=1, policy_name=audit.NAME))
    registry = dict(patients=people, jobs=jobs, inquiries=[], decisions=[])
    context = audit.context_from_registry(now, 4, 0, registry); members = []
    for index, (pid, public_job) in enumerate(zip(('A', 'B'), jobs)):
        job = {k: public_job[k] for k in audit.horizon.JOB_KEYS}
        snapshot = dict(apply_snapshot=dict(patient_id=pid, job_version=1, patient_version=1, revision=0, now=now), job=job)
        time_context = dict(patient_id=pid, job_version=1, event_prefix_max=4, decision_prefix_max=0,
            auto_inquiry_deadline_at=audit.EPOCH+300, evaluation_end_at=audit.EPOCH+300, source=dict(
                workflow={k: job[k] for k in ('patient_id', 'version', 'started_at', 'deadline_at', 'enabled')},
                workflow_start_event=dict(id=index*2+2, at=audit.EPOCH, type='inquiry_workflow_started',
                    patient_id=pid, deadline_seconds=300, restarted=False),
                patient_created_event=dict(id=index*2+1, at=audit.EPOCH, type='patient_created', patient_id=pid),
                evaluation_protocol=dict(name='fixed_lab_patient_lifetime', duration_seconds=300)))
        inputs = dict(now=now, candidates=[dict(hospital_id=f'H{i+1:02d}', travel=dict(duration_seconds=100+i*20))
                for i in range(hospitals)], observations=[], pending=[], patient_pending=[], max_new=10,
            last_contact_at=None, time_context=time_context)
        members.append(dict(patient_id=pid, inputs=inputs, workflow_snapshot=snapshot))
    plans = [{f'H{i+1:02d}': 0 if i < count else None for i in range(hospitals)} for _ in members]
    proposals = [horizon_output(m['inputs'], plan) for m, plan in zip(members, plans)]
    return events, registry, context, members, proposals


def pair_output(members, context, proposals):
    models = [audit.member_model(m['inputs'], p) for m, p in zip(members, proposals)]
    table = audit.enumerate_pairs(models, [p['send'] for p in proposals])
    evidence = dict(schema_version=1, rule=audit.RULE, base_policy=audit.BASE_POLICY,
        base_source_sha256=audit.BASE_SOURCE_SHA256, joint_context=deepcopy(context),
        baseline_proposals=deepcopy(proposals), model_inputs=models, **table,
        tolerances=dict(wait_seconds=1e-9, cost_seconds=1e-9, probability=1e-12),
        tie_rule='first original-pool combination within 1e-9 of global feasible minimum',
        scope='current-only immediate inquiries; H future plans set counts only; no future feasibility guarantee',
        externality='one earlier new job adds its belief mean / assumed workers to later shared hospital')
    selected = table['combinations'][table['selected_index']]; decisions = []
    for index, proposal in enumerate(proposals):
        decision = deepcopy(proposal); hospitals = selected['hospitals'][index]
        decision['send'] = list(hospitals)
        decision['reason'] = 'joint_first_current_only' if table['applied'] else 'joint_first_baseline_current_only'
        ev = decision['evidence']; ev['policy'] = audit.NAME; ev['sent_hospitals'] = list(hospitals)
        ev['planned_expected_cost'] = round(selected['member_metrics'][index]['total_cost_seconds'], 3)
        for row in ev['candidate_plan']:
            row['planned_offset_seconds'] = 0 if row['hospital'] in hospitals else None
        ev['joint_first'] = dict(schema_version=1, mode='pair_current_only', member_index=index,
            current_only=True, applied=table['applied'], baseline_send=list(proposal['send']),
            selected_send=list(hospitals), baseline_metrics=table['baseline_combination']['member_metrics'][index],
            selected_metrics=selected['member_metrics'][index],
            constraint_reference='common-prefix simultaneous immediate-only H sends')
        decisions.append(decision)
    return decisions, evidence


def replay():
    events, registry, context, members, proposals = setup()
    outputs, evidence = pair_output(members, context, proposals)
    assert [d['send'] for d in outputs] == [['H01'], ['H02']]
    group = dict(id=1, now=audit.EPOCH, event_prefix_max=4, decision_prefix_max=0, revision=0,
        context=context, registry_snapshot=registry, members=members, outputs=outputs,
        evidence=evidence, db_decision_ids=[1, 2], validated_before_write=True)
    for index, (pid, hid) in enumerate((('A', 'H01'), ('B', 'H02'))):
        events += [dict(id=5+index*2, at=audit.EPOCH, type='inquiry_sent', patient_id=pid, hospital_id=hid,
                data=dict(inquiry_id=index+1)),
            dict(id=6+index*2, at=audit.EPOCH, type='inquiry_workflow_batch', patient_id=pid, data={})]
    events += [dict(id=9, at=audit.EPOCH+10, type='inquiry_responded', patient_id='A', hospital_id='H01', data=dict(inquiry_id=1, action='accept')),
        dict(id=10, at=audit.EPOCH+10, type='destination_confirmed', patient_id='A', hospital_id='H01', data=dict(inquiry_id=1)),
        dict(id=11, at=audit.EPOCH+15, type='inquiry_responded', patient_id='B', hospital_id='H02', data=dict(inquiry_id=2, action='accept')),
        dict(id=12, at=audit.EPOCH+15, type='destination_confirmed', patient_id='B', hospital_id='H02', data=dict(inquiry_id=2))]
    scenario = dict(id='synthetic-pair-auditor-only', patients=[dict(id=p, at=0, travel_seconds=dict(H01=100, H02=120)) for p in ('A', 'B')],
        hospitals=[dict(id=h, workers=1) for h in ('H01', 'H02')],
        potentials=[dict(patient_id=p, hospital_id=h, service_seconds=10 if h == 'H01' else 15,
            no_response=False, accept=True) for p in ('A', 'B') for h in ('H01', 'H02')])
    digest = hashlib.sha256(json.dumps(scenario, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    calls, decisions, queries, rows = [], [], [], []
    for index, (pid, hid, selected_at, travel) in enumerate((('A', 'H01', 10, 100), ('B', 'H02', 15, 120))):
        output = outputs[index]
        calls.append(dict(patient_id=pid, at=0, name=audit.NAME, inputs=deepcopy(members[index]['inputs']),
            output=deepcopy(output), workflow_snapshot=deepcopy(members[index]['workflow_snapshot']),
            db_decision_id=index+1, db_event_prefix_max=4, joint_group_id=1, joint_member_index=index))
        decisions.append(dict(id=index+1, at=audit.EPOCH, patient_id=pid, policy_name=audit.NAME, job_version=1,
            hospital_ids=deepcopy(output['send']), reason=output['reason'], evidence=deepcopy(output['evidence'])))
        queries.append(dict(id=index+1, patient_id=pid, hospital_id=hid))
        rows.append(dict(id=pid, created_at=0, selected_at=selected_at, selection_seconds=selected_at,
            travel_seconds=travel, estimated_arrival_seconds=selected_at+travel, destination_hospital_id=hid,
            failed=False, inquiry_count=1, contacted_hospitals=[hid]))
    def trace(at, typ, pid, hid, iid, **extra):
        return dict(at=at, type=typ, patient_id=pid, hospital_id=hid, inquiry_id=iid, **extra)
    records = [trace(0, 'queue_added', 'A', 'H01', 1), trace(0, 'queue_added', 'B', 'H02', 2),
        trace(0, 'review_started', 'A', 'H01', 1, service_seconds=10, due_at=10),
        trace(0, 'review_started', 'B', 'H02', 2, service_seconds=15, due_at=15),
        trace(10, 'review_finished', 'A', 'H01', 1, started_at=0, service_seconds=10),
        trace(10, 'reply_accept', 'A', 'H01', 1), trace(10, 'destination_confirmed', 'A', 'H01', 1),
        trace(15, 'review_finished', 'B', 'H02', 2, started_at=0, service_seconds=15),
        trace(15, 'reply_accept', 'B', 'H02', 2), trace(15, 'destination_confirmed', 'B', 'H02', 2)]
    branch = dict(policy=audit.NAME, rule='immediate', environment_sha256=digest, snapshot_sha256='0'*64,
        instrumentation=deepcopy(audit.NEW_INSTRUMENTATION), joint_groups=[group], db_events=events,
        policy_calls=calls, db_decisions=decisions, db_inquiries=queries, patients=rows, trace=records, holds=[],
        metrics=dict(patient_count=2, selected_count=2, failed_count=0, selection_seconds_mean=12.5,
            selection_wait_capped_mean=12.5, inquiries_per_patient=1, estimated_arrival_seconds_mean=122.5,
            review_work_seconds=25, review_started_count=2, cancelled_before_start_count=0, reply_suppressed_count=0))
    return dict(schema_version=1, scenario=scenario, environment_sha256=digest,
        initial_snapshot_sha256='0'*64, arms=[[audit.NAME, 'immediate']], branches=[branch])


class IndependentRegistryTests(unittest.TestCase):
    def test_complete_zero_journal_registered_pair_reconstruction(self):
        events, registry, context, _, _ = setup()
        self.assertEqual(audit.audit_registry(events, [], registry, audit.EPOCH, 4, 0), context)
        self.assertEqual(context['pair_patient_ids'], ['A', 'B'])

    def test_current_registry_omission_extra_future_or_disabled_forgery_rejected(self):
        for change in ('omit', 'future', 'enabled', 'policy', 'due', 'version', 'state'):
            events, registry, _, _, _ = setup()
            if change == 'omit': registry['patients'].pop(); registry['jobs'].pop()
            elif change == 'future': events[2]['at'] += 1
            elif change == 'enabled': registry['jobs'][0]['enabled'] = 0
            elif change == 'policy': registry['jobs'][0]['policy_name'] = 'other'
            elif change == 'due': registry['jobs'][0]['next_evaluation_at'] += 1
            elif change == 'version': registry['jobs'][0]['version'] += 1
            else: registry['patients'][0]['state'] = 'CANCELLED'
            with self.subTest(change=change), self.assertRaises(audit.JointFirstAuditError):
                audit.audit_registry(events, [], registry, audit.EPOCH, 4, 0)

    def test_inquiry_and_decision_prefix_inventory_cannot_be_hidden(self):
        events, registry, _, _, _ = setup()
        events.append(dict(id=5, at=audit.EPOCH, type='inquiry_sent', patient_id='A', hospital_id='H01', data=dict(inquiry_id=1)))
        with self.assertRaises(audit.JointFirstAuditError): audit.audit_registry(events, [], registry, audit.EPOCH, 5, 0)
        registry['inquiries'] = [dict(patient_id='A')]
        decision = dict(id=1, at=audit.EPOCH, patient_id='A')
        with self.assertRaises(audit.JointFirstAuditError): audit.audit_registry(events, [decision], registry, audit.EPOCH, 5, 1)
        registry['decisions'] = [decision]
        self.assertEqual(audit.audit_registry(events, [decision], registry, audit.EPOCH, 5, 1)['pair_patient_ids'], [])

    def test_context_uses_due_order_and_does_not_invent_pair_with_one_member(self):
        _, registry, _, _, _ = setup()
        registry['jobs'][0]['next_evaluation_at'] += 1
        context = audit.context_from_registry(audit.EPOCH, 4, 0, registry)
        self.assertEqual(context['pair_patient_ids'], [])
        self.assertEqual([r['patient']['id'] for r in context['eligible']], ['B'])


class IndependentPairMathTests(unittest.TestCase):
    def test_current_only_baseline_shared_mean_and_selected_pair(self):
        _, _, context, members, proposals = setup()
        outputs, evidence = pair_output(members, context, proposals)
        checked = audit.audit_pair(members, context, outputs, evidence)
        self.assertEqual(checked['joint_combinations_checked'], 4)
        self.assertTrue(evidence['applied'])
        self.assertEqual([o['send'] for o in outputs], [['H01'], ['H02']])
        baseline = evidence['baseline_combination']
        mean = math.exp(math.log(66)+.7*.7/2)
        self.assertAlmostEqual(baseline['later_member_extra_wait_seconds']['H01'], mean)
        self.assertEqual(baseline['member_metrics'][0]['immediate_contact_cost_seconds'], 15)
        self.assertGreaterEqual(evidence['combinations'][evidence['selected_index']]['member_metrics'][1]['selection_probability'],
            baseline['member_metrics'][1]['selection_probability'])

    def test_all_400_memberships_enumerated_without_policy_search(self):
        _, _, context, members, proposals = setup(hospitals=6, count=3)
        outputs, evidence = pair_output(members, context, proposals)
        self.assertEqual(len(evidence['combinations']), 400)
        self.assertEqual(audit.audit_pair(members, context, outputs, evidence)['joint_combinations_checked'], 400)
        self.assertEqual(evidence['cardinalities'], [3, 3])

    def test_identical_only_choice_preserves_baseline_on_tie(self):
        _, _, context, members, proposals = setup(hospitals=1)
        outputs, evidence = pair_output(members, context, proposals)
        self.assertFalse(evidence['applied']); self.assertEqual(evidence['selected_index'], evidence['baseline_index'])
        self.assertEqual(audit.audit_pair(members, context, outputs, evidence)['joint_groups_applied'], 0)

    def test_partial_H_edges_and_current_only_travel_tie_are_preserved(self):
        _, _, context, members, proposals = setup(horizon=17)
        outputs, evidence = pair_output(members, context, proposals)
        self.assertEqual(evidence['model_inputs'][0]['cell_edges_seconds'], [5, 10, 15, 17])
        audit.audit_pair(members, context, outputs, evidence)

    def test_table_omission_externality_worker_feasibility_and_winner_forgery_rejected(self):
        for change in ('omit', 'mean', 'workers', 'feasible', 'winner', 'cardinality', 'cost', 'probability', 'scope'):
            _, _, context, members, proposals = setup()
            outputs, evidence = pair_output(members, context, proposals)
            if change == 'omit': evidence['combinations'].pop()
            elif change == 'mean': evidence['baseline_combination']['later_member_extra_wait_seconds']['H01'] += 1
            elif change == 'workers': evidence['model_inputs'][0]['hospitals'][0]['assumed_workers'] = 2
            elif change == 'feasible': evidence['combinations'][0]['feasible'] = False
            elif change == 'winner': evidence['selected_index'] = evidence['baseline_index']
            elif change == 'cardinality': evidence['cardinalities'][0] += 1
            elif change == 'cost': evidence['combinations'][0]['member_metrics'][0]['total_cost_seconds'] += 1
            elif change == 'probability': evidence['combinations'][0]['member_metrics'][0]['selection_probability'] += .01
            else: evidence['scope'] = 'future guaranteed'
            with self.subTest(change=change), self.assertRaises(audit.JointFirstAuditError):
                audit.audit_pair(members, context, outputs, evidence)

    def test_output_counts_plan_review_and_model_objective_are_checked(self):
        for change in ('send', 'plan', 'review', 'cost', 'mode', 'proposal'):
            _, _, context, members, proposals = setup()
            outputs, evidence = pair_output(members, context, proposals)
            if change == 'send': outputs[0]['send'] = ['H01', 'H02']
            elif change == 'plan': outputs[0]['evidence']['candidate_plan'][0]['planned_offset_seconds'] = 15
            elif change == 'review': outputs[0]['next_review_at'] += 1
            elif change == 'cost': outputs[0]['evidence']['planned_expected_cost'] += 1
            elif change == 'mode': outputs[0]['evidence']['joint_first']['current_only'] = False
            else: evidence['baseline_proposals'][0]['evidence']['planned_expected_cost'] += 1
            with self.subTest(change=change), self.assertRaises(audit.JointFirstAuditError):
                audit.audit_pair(members, context, outputs, evidence)


class IndependentFullTraceTests(unittest.TestCase):
    def test_same_clock_groups_precede_fallback_but_next_clock_can_form_new_group(self):
        calls = [dict(at=0, joint_group_id=1), dict(at=0, joint_group_id=1), dict(at=0),
            dict(at=15, joint_group_id=2), dict(at=15, joint_group_id=2), dict(at=15)]
        audit._audit_phase_order(calls)
        with self.assertRaisesRegex(audit.JointFirstAuditError, 'same-clock fallback'):
            audit._audit_phase_order([dict(at=0), dict(at=0, joint_group_id=1)])

    def test_manually_declared_two_member_trace_and_all_patient_accounting(self):
        raw = replay(); before = deepcopy(raw); checked = audit.audit_replay(raw)['branch_audits'][0]
        self.assertEqual(raw, before); self.assertEqual(checked['joint_members_checked'], 2)
        self.assertEqual(checked['total_capped_selection_wait_seconds'], 25)
        self.assertEqual(checked['total_arrival_failure_cost_seconds'], 245)
        self.assertEqual(checked['review_work_seconds'], 25)
        self.assertFalse(checked['physical_transaction_boundary_from_raw_proved'])

    def test_ungrouped_first_or_subsequent_decision_uses_bound_H_math(self):
        raw = horizon_replay(); branch = raw['branches'][0]; call = branch['policy_calls'][0]
        raw['arms'] = [[audit.NAME, 'immediate']]; branch['policy'] = audit.NAME
        branch['joint_groups'] = []; branch['instrumentation'] = deepcopy(audit.NEW_INSTRUMENTATION)
        call['name'] = audit.NAME; call['workflow_snapshot']['job']['policy_name'] = audit.NAME
        branch['db_events'][1]['data']['policy_name'] = audit.NAME
        call['output']['evidence']['policy'] = audit.NAME
        call['output']['evidence']['joint_first'] = dict(schema_version=1, mode='horizon_fallback', current_only=False)
        branch['db_decisions'][0]['policy_name'] = audit.NAME
        branch['db_decisions'][0]['evidence'] = deepcopy(call['output']['evidence'])
        checked = audit.audit_replay(raw)['branch_audits'][0]
        self.assertEqual(checked['horizon_calls_checked'], 1)
        self.assertEqual(checked['total_capped_selection_wait_seconds'], 10)
        call['output']['evidence']['joint_first']['current_only'] = True
        branch['db_decisions'][0]['evidence'] = deepcopy(call['output']['evidence'])
        with self.assertRaises(audit.JointFirstAuditError): audit.audit_replay(raw)

    def test_common_prefix_snapshot_revision_and_atomic_group_link_forgery_rejected(self):
        for change in ('prefix', 'revision', 'member', 'before_write', 'decisionids', 'remove', 'dprefix', 'roster'):
            raw = replay(); branch = raw['branches'][0]; group = branch['joint_groups'][0]
            if change == 'prefix': branch['policy_calls'][1]['db_event_prefix_max'] = 5
            elif change == 'revision': group['members'][1]['workflow_snapshot']['apply_snapshot']['revision'] += 1
            elif change == 'member': branch['policy_calls'][1]['joint_member_index'] = 0
            elif change == 'before_write': group['validated_before_write'] = False
            elif change == 'decisionids': group['db_decision_ids'] = [1, 3]
            elif change == 'remove': branch['joint_groups'] = []
            elif change == 'dprefix': group['context']['decision_prefix_max'] = 1
            else: group['registry_snapshot']['patients'].append(dict(id='future', state='WAITING', version=1))
            with self.subTest(change=change), self.assertRaises(audit.JointFirstAuditError): audit.audit_replay(raw)

    def test_FIFOs_worker_outcome_wait_and_work_mutations_rejected(self):
        for change in ('start', 'outcome', 'wait', 'work', 'snapshot', 'version', 'private'):
            raw = replay(); branch = raw['branches'][0]
            if change == 'start': branch['trace'][2]['service_seconds'] += 1
            elif change == 'outcome': branch['trace'][5]['type'] = 'reply_decline'
            elif change == 'wait': branch['patients'][0]['selection_seconds'] += 1
            elif change == 'work': branch['metrics']['review_work_seconds'] += 1
            elif change == 'snapshot': branch['policy_calls'][0]['workflow_snapshot']['job']['patient_id'] = 'other'
            elif change == 'version': branch['db_decisions'][1]['job_version'] += 1
            else: branch['policy_calls'][0]['inputs']['workers'] = 2
            with self.subTest(change=change), self.assertRaises(audit.JointFirstAuditError): audit.audit_replay(raw)

    def test_clean_import_never_loads_producers_server_simulator_or_solver(self):
        code = ("import json,sys; import inquiry_lab.joint_first_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server','simulation','ortools')) or n in {'inquiry_lab.joint_first_policy', "
            "'inquiry_lab.joint_first_runner','inquiry_lab.joint_first_context','inquiry_lab.horizon_policy', "
            "'inquiry_lab.horizon_runner','inquiry_lab.horizon_context','inquiry_lab.eat_policy'})))")
        output = subprocess.check_output([sys.executable, '-B', '-c', code], cwd=ROOT, text=True)
        self.assertEqual(json.loads(output), [])


if __name__ == '__main__': unittest.main()
