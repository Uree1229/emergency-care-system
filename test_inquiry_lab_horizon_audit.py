"""Synthetic math/journal/trace mutations only; no policy or performance run."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from inquiry_lab import horizon_audit as audit

ROOT = Path(__file__).resolve().parent


def journals(now=None, age=100, duration=300, version=1):
    now = audit.EPOCH+100 if now is None else now
    birth = now-age
    events = [dict(id=1, at=birth, type='patient_created', patient_id='P', data={}),
        dict(id=2, at=birth, type='inquiry_workflow_started', patient_id='P',
            data=dict(deadline_seconds=duration, policy_name=audit.NAME))]
    job = dict(patient_id='P', version=version, started_at=birth, deadline_at=birth+duration,
        enabled=1, policy_name=audit.NAME)
    return now, events, job


def inputs(horizon=200, own=False):
    now, events, job = journals(age=300-horizon)
    context = audit.context_from_prefix(events, now, 'P', 1, 2, 0, job)
    pending = [dict(hospital='OLD', patient='P', submitted_at=now-1)] if own else []
    return dict(now=now, candidates=[dict(hospital_id='H01', travel=dict(duration_seconds=100))],
        observations=[], pending=deepcopy(pending),
        patient_pending=[dict(row, travel_seconds=120) for row in pending],
        max_new=10, last_contact_at=now-1 if own else None, time_context=context)


def output(value, offsets=None):
    """Construct recorded synthetic evidence; this is not a policy search."""
    model = audit.model_from_inputs(value)
    offsets = offsets if offsets is not None else {row['hospital']: 0 for row in model['pool']}
    beliefs = audit.parent._beliefs(value['observations'], value['now'])
    r, a, mu, _ = beliefs(object()); rows = []
    for row in model['pool']:
        hid = row['hospital']; hr, ha, _, _ = beliefs(hid)
        rows.append(dict(hospital=hid, planned_offset_seconds=offsets[hid], inquiry_cost=15,
            reply_probability=round(hr, 4), accept_probability=round(ha, 4),
            queue_length=sum(q['hospital'] == hid for q in value['pending']), travel_seconds=row['travel_seconds']))
    send = [hid for hid, offset in offsets.items() if offset == 0]
    context = value['time_context']; window = min(context['auto_inquiry_deadline_at'], context['evaluation_end_at'])-value['now']
    if window <= 0: reason = 'automatic_inquiry_deadline'
    elif value['max_new'] == 0: reason = 'contact_budget'
    elif send: reason = 'eat_hedge' if value['patient_pending'] else 'eat_initial'
    elif not value['candidates']: reason = 'no_candidates'
    elif model['room'] <= 0: reason = 'eat_concurrency_cap'
    else: reason = 'eat_wait'
    evidence = dict(evaluated_at=value['now'], policy=audit.NAME, horizon_seconds=model['horizon'],
        lambda_seconds=15, congestion_aware=False,
        expected_cost_without_new=round(audit.expected_cost(model['current'], model['bins'], model['horizon']), 3),
        planned_expected_cost=round(audit.plan_cost(model, offsets), 3),
        reply_probability_global=round(r, 4), accept_probability_global=round(a, 4),
        service_median_seconds=round(math.exp(mu), 2), history_used_count=len(value['observations']),
        own_waiting_count=model['fresh'], own_observation_timed_out_count=len(value['patient_pending'])-model['fresh'],
        unresolved_inquiry_count=len(value['pending']), candidate_plan=rows, sent_hospitals=list(send),
        residual_queue=dict(schema_version=1, rule='head_residual_plus_full_followers', hospitals=model['residual_rows']),
        time_horizon=dict(schema_version=1, rule=audit.RULE, source_sha256=dict(audit.SOURCE_SHA256),
            context=deepcopy(context), observation_remaining_seconds=model['horizon'],
            new_contact_window_seconds=window, cell_edges_seconds=[b for _, b in model['bins']],
            cell_representatives_seconds=[(a+b)/2 for a, b in model['bins']],
            allowed_plan_offsets_seconds=model['offsets'],
            pending_reply_scope='retain A-after replies through inclusive lab H; A stops only new inquiries',
            evaluation_scope='fixed lab measurement only; no live pending-reply expiry at A'))
    return dict(send=send, reason=reason, evidence=evidence, next_review_at=value['now']+15)


def snapshot(now, job):
    return dict(apply_snapshot=dict(patient_id='P', job_version=job['version'], patient_version=1,
        revision=0, now=now), job=deepcopy(job))


def replay():
    """A manually declared one-inquiry FIFO trace, not simulator output."""
    now, events, job = journals(now=audit.EPOCH, age=0)
    value = inputs(horizon=300); value['now'] = now
    value['time_context'] = audit.context_from_prefix(events, now, 'P', 1, 2, 0, job)
    result = output(value)
    events += [dict(id=3, at=now, type='inquiry_sent', patient_id='P', hospital_id='H01', data=dict(inquiry_id=1)),
        dict(id=4, at=now, type='inquiry_workflow_batch', patient_id='P', data={}),
        dict(id=5, at=now+10, type='inquiry_responded', patient_id='P', hospital_id='H01', data=dict(inquiry_id=1, action='accept')),
        dict(id=6, at=now+10, type='destination_confirmed', patient_id='P', hospital_id='H01', data=dict(inquiry_id=1))]
    scenario = dict(id='synthetic-auditor-unit-only', patients=[dict(id='P', at=0, travel_seconds=dict(H01=100))],
        hospitals=[dict(id='H01', workers=1)],
        potentials=[dict(patient_id='P', hospital_id='H01', service_seconds=10, no_response=False, accept=True)])
    digest = hashlib.sha256(json.dumps(scenario, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    decision = dict(id=1, at=now, patient_id='P', policy_name=audit.NAME, job_version=1,
        reason=result['reason'], hospital_ids=deepcopy(result['send']), evidence=deepcopy(result['evidence']))
    def trace(at, kind, **kw):
        return dict(at=at, type=kind, patient_id='P', hospital_id='H01', inquiry_id=1, **kw)
    branch = dict(policy=audit.NAME, rule='immediate', environment_sha256=digest, snapshot_sha256='0'*64,
        instrumentation=deepcopy(audit.NEW_INSTRUMENTATION), db_events=events,
        db_inquiries=[dict(id=1, patient_id='P', hospital_id='H01')], db_decisions=[decision],
        policy_calls=[dict(patient_id='P', at=0, name=audit.NAME, inputs=value, output=result,
            db_decision_id=1, db_event_prefix_max=2, workflow_snapshot=snapshot(now, job))],
        trace=[trace(0, 'queue_added'), trace(0, 'review_started', service_seconds=10, due_at=10),
            trace(10, 'review_finished', started_at=0, service_seconds=10), trace(10, 'reply_accept'),
            trace(10, 'destination_confirmed')], holds=[],
        patients=[dict(id='P', created_at=0, selected_at=10, selection_seconds=10, travel_seconds=100,
            estimated_arrival_seconds=110, destination_hospital_id='H01', failed=False,
            inquiry_count=1, contacted_hospitals=['H01'])],
        metrics=dict(patient_count=1, selected_count=1, failed_count=0, selection_seconds_mean=10,
            selection_wait_capped_mean=10, inquiries_per_patient=1, estimated_arrival_seconds_mean=110,
            review_work_seconds=10, review_started_count=1, cancelled_before_start_count=0, reply_suppressed_count=0))
    return dict(schema_version=1, scenario=scenario, environment_sha256=digest,
        initial_snapshot_sha256='0'*64, arms=[[audit.NAME, 'immediate']], branches=[branch])


class IndependentBoundaryMathTests(unittest.TestCase):
    def test_partial_interval_has_actual_midpoint_and_no_minimum_fifteen_seconds(self):
        self.assertEqual(audit.intervals(7), [(0, 5), (5, 7)])
        self.assertEqual(audit.intervals(1), [(0, 1)])
        self.assertEqual(audit.intervals(15), [(0, 5), (5, 10), (10, 15)])
        with self.assertRaises(audit.HorizonAuditError): audit.intervals(0)

    def test_partial_shift_uses_two_seconds_not_the_original_five_seconds(self):
        cdf = lambda x: min(1., max(0., x/20))
        self.assertEqual(audit.masses(cdf, 1., audit.intervals(17), 15), [0., 0., 0., .1])

    def test_exact_H_atom_is_included(self):
        self.assertEqual(audit.masses(lambda x: float(x >= 17), .4, audit.intervals(17)), [0., 0., 0., .4])

    def test_hand_computed_partial_cell_expected_cost(self):
        expected = .2*12.5 + .3*16 + .5*1507
        self.assertAlmostEqual(audit.expected_cost([(10, [.2, .3])], audit.intervals(7), 7), expected)

    def test_two_hospital_first_acceptance_nearest_tie_hand_calculation(self):
        expected = .2*12.5 + .8*.4*22.5 + .8*.6*1505
        self.assertAlmostEqual(audit.expected_cost([(20, [.4]), (10, [.2])], audit.intervals(5), 5), expected)

    def test_soft_A_does_not_remove_pending_probability_between_A_and_H(self):
        value = inputs(horizon=117, own=True); early = deepcopy(value)
        early['time_context']['auto_inquiry_deadline_at'] = value['now']+1
        a, b = audit.model_from_inputs(value), audit.model_from_inputs(early)
        self.assertEqual(a['current'], b['current'])
        self.assertGreater(sum(b['current'][0][1][1:]), 0)
        self.assertEqual(b['offsets'], [0])

    def test_A_and_H_independently_bound_new_start_offsets(self):
        value = inputs(horizon=17, own=True)
        value['time_context']['auto_inquiry_deadline_at'] = value['now']+400
        self.assertEqual(audit.model_from_inputs(value)['offsets'], [0, 15])
        value['time_context']['auto_inquiry_deadline_at'] = value['now']+15
        self.assertEqual(audit.model_from_inputs(value)['offsets'], [0])

    def test_recorded_partial_delayed_plan_and_unchanged_residual_math(self):
        value = inputs(horizon=17, own=True); result = output(value, dict(H01=15))
        checked = audit.audit_horizon(value, result)
        self.assertEqual(checked['partial_final_cells_checked'], 1)
        result['evidence']['planned_expected_cost'] += 1
        with self.assertRaises(audit.HorizonAuditError): audit.audit_horizon(value, result)

    def test_old_floor_cells_late_offsets_and_combined_policy_are_rejected(self):
        value = inputs(horizon=7, own=True)
        for change in ('edge', 'offset', 'pool', 'work', 'extra', 'review', 'source'):
            result = output(value, dict(H01=None))
            if change == 'edge': result['evidence']['time_horizon']['cell_edges_seconds'][-1] = 10
            elif change == 'offset': result['evidence']['candidate_plan'][0]['planned_offset_seconds'] = 15
            elif change == 'pool': result['evidence']['candidate_plan'][0]['hospital'] = 'other'
            elif change == 'work': result['evidence']['residual_queue']['hospitals'][0]['estimated_wait_seconds'] += 1
            elif change == 'extra': result['evidence']['guard'] = {}
            elif change == 'review': result['next_review_at'] += 1
            else: result['evidence']['time_horizon']['source_sha256']['residual_policy'] = '0'*64
            with self.subTest(change=change), self.assertRaises(audit.HorizonAuditError):
                audit.audit_horizon(value, result)

    def test_after_A_pure_edge_keeps_pending_but_sends_nothing(self):
        value = inputs(horizon=117, own=True); value['time_context']['auto_inquiry_deadline_at'] = value['now']
        result = output(value, dict(H01=None))
        self.assertEqual(result['reason'], 'automatic_inquiry_deadline')
        self.assertEqual(audit.audit_horizon(value, result)['soft_deadline_distinct_calls_checked'], 1)


class IndependentPublicContextTests(unittest.TestCase):
    def test_current_public_creation_start_and_version_reconstructed(self):
        now, events, job = journals()
        context = audit.context_from_prefix(events, now, 'P', 1, 2, 0, job)
        self.assertEqual(context['auto_inquiry_deadline_at'], audit.EPOCH+300)
        self.assertEqual(context['evaluation_end_at'], audit.EPOCH+300)
        self.assertEqual(audit.audit_context(dict(now=now, time_context=context), 'P', events, 2, 0,
            dict(id=1, job_version=1), snapshot(now, job)), 1)

    def test_latest_restart_changes_A_but_never_H(self):
        now, events, job = journals(duration=60, version=5)
        events += [dict(id=3, at=now-10, type='inquiry_workflow_started', patient_id='P',
            data=dict(deadline_seconds=60, restarted=True, policy_name=audit.NAME))]
        job.update(started_at=now-10, deadline_at=now+50)
        context = audit.context_from_prefix(events, now, 'P', 5, 3, 9, job)
        self.assertEqual(context['auto_inquiry_deadline_at'], now+50)
        self.assertEqual(context['evaluation_end_at'], audit.EPOCH+300)
        self.assertTrue(context['source']['workflow_start_event']['restarted'])

    def test_same_clock_after_prefix_start_is_invisible(self):
        now, events, job = journals()
        events += [dict(id=3, at=now, type='inquiry_workflow_started', patient_id='P', data=dict(deadline_seconds=900))]
        self.assertEqual(audit.context_from_prefix(events, now, 'P', 1, 2, 0, job)['auto_inquiry_deadline_at'], audit.EPOCH+300)

    def test_future_inside_prefix_does_not_restore_older_start(self):
        now, events, job = journals()
        events += [dict(id=3, at=now+1, type='inquiry_workflow_started', patient_id='P', data=dict(deadline_seconds=300))]
        with self.assertRaises(audit.HorizonAuditError): audit.context_from_prefix(events, now, 'P', 1, 3, 0, job)

    def test_wrong_patient_missing_creation_missing_start_and_boolean_version_rejected(self):
        now, events, job = journals()
        for change in ('patient', 'creation', 'start', 'version', 'enabled'):
            altered, current = deepcopy(events), deepcopy(job)
            if change == 'patient': altered[0]['patient_id'] = 'other'
            elif change == 'creation': altered = altered[1:]
            elif change == 'start': altered = altered[:1]
            elif change == 'version': current['version'] = True
            else: current['enabled'] = True
            with self.subTest(change=change), self.assertRaises(audit.HorizonAuditError):
                audit.context_from_prefix(altered, now, 'P', 1, 2, 0, current)

    def test_context_wrong_H_A_prefix_version_or_current_decision_rejected(self):
        now, events, job = journals(); base = audit.context_from_prefix(events, now, 'P', 1, 2, 0, job)
        for key in ('patient_id', 'job_version', 'event_prefix_max', 'decision_prefix_max',
                    'evaluation_end_at', 'auto_inquiry_deadline_at'):
            context = deepcopy(base); context[key] = 'other' if key == 'patient_id' else context[key]+1
            with self.subTest(key=key), self.assertRaises(audit.HorizonAuditError):
                audit.audit_context(dict(now=now, time_context=context), 'P', events, 2, 0,
                    dict(id=1, job_version=1), snapshot(now, job))


class IndependentSyntheticFullTraceTests(unittest.TestCase):
    def test_complete_synthetic_trace_W_J_work_and_read_only(self):
        raw = replay(); before = deepcopy(raw); checked = audit.audit_replay(raw)
        self.assertEqual(raw, before); branch = checked['branch_audits'][0]
        self.assertEqual(branch['total_capped_selection_wait_seconds'], 10)
        self.assertEqual(branch['total_arrival_failure_cost_seconds'], 110)
        self.assertEqual(branch['review_work_seconds'], 10)
        self.assertFalse(branch['policy_kernel_independently_reexecuted'])

    def test_reply_exactly_at_H_is_success_and_not_failure(self):
        raw = replay(); branch = raw['branches'][0]
        raw['scenario']['potentials'][0]['service_seconds'] = 300
        for event in branch['db_events']:
            if event['id'] >= 5: event['at'] = audit.EPOCH+300
        branch['trace'][1].update(service_seconds=300, due_at=300)
        branch['trace'][2].update(at=300, service_seconds=300)
        branch['trace'][3]['at'] = branch['trace'][4]['at'] = 300
        branch['patients'][0].update(selected_at=300, selection_seconds=300, estimated_arrival_seconds=400)
        branch['metrics'].update(selection_seconds_mean=300, selection_wait_capped_mean=300,
            estimated_arrival_seconds_mean=400, review_work_seconds=300)
        digest = hashlib.sha256(json.dumps(raw['scenario'], ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        raw['environment_sha256'] = branch['environment_sha256'] = digest
        checked = audit.audit_replay(raw)['branch_audits'][0]
        self.assertEqual(checked['failed'], 0)
        self.assertEqual(checked['total_capped_selection_wait_seconds'], 300)
        self.assertEqual(checked['total_arrival_failure_cost_seconds'], 400)

    def test_failure_is_capped_at_300_and_started_work_survives_cancellation(self):
        raw = replay(); branch = raw['branches'][0]
        raw['scenario']['potentials'][0]['service_seconds'] = 310
        branch['db_events'] = branch['db_events'][:4]+[
            dict(id=5, at=audit.EPOCH+300, type='inquiry_closed', patient_id='P', hospital_id='H01', data=dict(inquiry_id=1)),
            dict(id=6, at=audit.EPOCH+300, type='patient_cancelled', patient_id='P', data={})]
        branch['trace'][1].update(service_seconds=310, due_at=310)
        branch['trace'][2].update(at=310, service_seconds=310)
        branch['trace'][3].update(at=310, type='reply_suppressed_after_cancel')
        branch['trace'].pop()
        branch['patients'][0].update(failed=True, selected_at=None, selection_seconds=None,
            travel_seconds=None, estimated_arrival_seconds=None, destination_hospital_id=None)
        branch['metrics'].update(selected_count=0, failed_count=1, selection_seconds_mean=None,
            selection_wait_capped_mean=300, estimated_arrival_seconds_mean=None,
            review_work_seconds=310, reply_suppressed_count=1)
        digest = hashlib.sha256(json.dumps(raw['scenario'], ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        raw['environment_sha256'] = branch['environment_sha256'] = digest
        checked = audit.audit_replay(raw)['branch_audits'][0]
        self.assertEqual(checked['failed'], 1)
        self.assertEqual(checked['total_capped_selection_wait_seconds'], 300)
        self.assertEqual(checked['total_arrival_failure_cost_seconds'], 1800)
        self.assertEqual(checked['review_work_seconds'], 310)

    def test_forged_snapshot_context_decision_version_and_future_source_rejected(self):
        for change in ('snapshot', 'job', 'decision', 'future', 'missing', 'private', 'pending', 'prefix'):
            raw = replay(); branch = raw['branches'][0]; call = branch['policy_calls'][0]
            if change == 'snapshot': call['workflow_snapshot']['apply_snapshot']['patient_id'] = 'other'
            elif change == 'job': call['workflow_snapshot']['job']['version'] += 1
            elif change == 'decision': branch['db_decisions'][0]['job_version'] += 1
            elif change == 'future': branch['db_events'][0]['at'] += 1
            elif change == 'missing': del call['workflow_snapshot']
            elif change == 'private': call['inputs']['time_context']['workers'] = 2
            elif change == 'pending': call['inputs']['pending'].append(dict(hospital='H01', patient='future', submitted_at=audit.EPOCH))
            else: call['db_event_prefix_max'] = 3
            with self.subTest(change=change), self.assertRaises(audit.HorizonAuditError): audit.audit_replay(raw)

    def test_actual_fifo_reply_work_wait_and_environment_mutations_rejected(self):
        for change in ('start', 'reply', 'work', 'wait', 'environment', 'record_only'):
            raw = replay(); branch = raw['branches'][0]
            if change == 'start': branch['trace'][1]['service_seconds'] += 1
            elif change == 'reply': branch['trace'][3]['type'] = 'reply_decline'
            elif change == 'work': branch['metrics']['review_work_seconds'] += 1
            elif change == 'wait': branch['patients'][0]['selection_seconds'] += 1
            elif change == 'environment': branch['environment_sha256'] = '1'*64
            else: branch['instrumentation']['record_only'] = True
            with self.subTest(change=change), self.assertRaises(audit.HorizonAuditError): audit.audit_replay(raw)

    def test_clean_import_has_no_policy_context_runner_server_or_simulation(self):
        code = ("import json,sys; import inquiry_lab.horizon_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server', 'simulation', 'ortools')) or n in {'inquiry_lab.horizon_policy', "
            "'inquiry_lab.horizon_runner','inquiry_lab.horizon_context','inquiry_lab.residual_policy', 'inquiry_lab.eat_policy'})))")
        result = subprocess.check_output([sys.executable, '-B', '-c', code], cwd=ROOT, text=True)
        self.assertEqual(json.loads(result), [])


if __name__ == '__main__':
    unittest.main()
