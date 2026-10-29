"""Copied native pair capture/stale/rollback units for the isolated namespace."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from server import db,inquiry_policies
from inquiry_lab import horizon_runner,cohort_foreignreplywake_policy
from inquiry_lab import cohort_foreignreplywake_workflow as inquiry_workflow,cohort_foreignreplywake_service as service,cohort_foreignreplywake_runner as r


def unit_send(conn,now,pid,hospitals):
    # Seed public in-flight rows only; do not call the old UI view through a
    # delegated public wrapper that has no local experimental policy registry.
    with db.transaction(conn):service._send_inquiries(conn,now,pid,hospitals)


class CohortForeignreplywakeRunnerUnits(unittest.TestCase):
    def test_group_call_records_are_appended_only_after_a_successful_atomic_commit(self):
        group=self.prepare();records=[];original=r._write_member;count=[]
        def broken(conn,now,member,decision):
            count.append(member['patient_id'])
            if len(count)==2:raise RuntimeError('unit fail second')
            return original(conn,now,member,decision)
        before=self.snapshot()
        with patch.object(r,'_write_member',side_effect=broken):
            with self.assertRaises(RuntimeError):r._apply_group(self.conn,1000,group,self.result(group),records=records)
        self.assertEqual(records,[]);self.assertEqual(self.snapshot(),before)
        result=r._apply_group(self.conn,1000,group,self.result(group),records=records)
        self.assertEqual(len(records),2);self.assertEqual([c['db_decision_id'] for c in records],result['db_decision_ids'])

    def test_genuine_first_group_preserves_actual_H_watermark_and_supports_later_wake(self):
        group=self.prepare();records=[]
        result=cohort_foreignreplywake_policy.decide_joint(r.NAME,members=group['members'],joint_context=group['context'],
            tail_members=group['tail_members'],priority_context=group['priority_context'],owner_context=group['owner_context'])
        applied=r._apply_group(self.conn,1000,group,result,records=records)
        self.assertIsNotNone(applied)
        for call in records:
            self.assertEqual(call['output']['evidence']['time_horizon']['context']['event_prefix_max'],group['event_prefix_max'])
        inquiry=self.conn.execute("SELECT id FROM inquiries WHERE patient_id='A' ORDER BY id LIMIT 1").fetchone()[0]
        service.respond(self.conn,1005,inquiry,'decline','unit',reason='기타')
        snapshot,_,wake=inquiry_workflow._capture(self.conn,1005,'A')
        self.assertIsNotNone(snapshot);self.assertIsNotNone(wake)
        self.assertEqual(wake['latest_decision']['id'],applied['db_decision_ids'][0])

    def test_native_first_group_instrumentation_and_control_namespace(self):
        from inquiry_lab import cohort_tailwait_runner as accepted
        self.assertIs(r.control_runner,accepted)
        self.assertIsNot(r.inquiry_workflow,inquiry_workflow.foundation)
        self.assertTrue(r.NEW_INSTRUMENTATION['isolated_execution_namespace'])
        self.assertFalse(r.NEW_INSTRUMENTATION['original_module_globals_or_registry_replaced'])
        self.assertTrue(r.NEW_INSTRUMENTATION['unchanged_H_objective_and_distributions'])

    def test_foreign_arm_is_rejected_before_candidate_or_database_creation(self):
        with patch.object(r,'_candidate_scenario',side_effect=AssertionError('must reject all arm names before executing any')) as execute:
            with self.assertRaisesRegex(ValueError,'candidate arms only'):r.run_scenario({},[(r.NAME,'immediate'),('fixed_3','immediate')])
        execute.assert_not_called()

    def test_grouped_runner_rejects_foreign_active_job_before_native_pair_writes(self):
        service.create_patient(self.conn,1000,'FOREIGN',dict(reporter_role='transporter',consent=True,symptoms='unit',required_capabilities=[],
            resource_demands={'ed_bed':1},lat=35.,lon=127.,auto_inquiry=True,inquiry_policy='fixed_3'))
        before=self.snapshot();groups=[];records=[]
        with self.assertRaisesRegex(ValueError,'candidate active jobs'):r.run_once(self.path,object(),lambda:1000,groups=groups,records=records)
        self.assertEqual(self.snapshot(),before);self.assertEqual(groups,[]);self.assertEqual(records,[])

    def setUp(self):
        self.workspace = Path(__file__).resolve().parent
        self.tmp = tempfile.TemporaryDirectory(prefix='cohort-hedge-unit-', dir=self.workspace)
        self.registry = deepcopy(inquiry_policies._POLICIES)
        self.serial = 0
        self.conn = None
        self.fresh()

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.assertEqual(inquiry_policies._POLICIES,self.registry)
        self.assertTrue(Path(self.tmp.name).resolve().is_relative_to(self.workspace))
        self.tmp.cleanup()

    def fresh(self, people=('A', 'B')):
        if self.conn is not None:
            self.conn.close()
        self.serial += 1
        self.path = Path(self.tmp.name) / ('unit' + str(self.serial) + '.db')
        self.conn = db.connect(self.path)
        db.init(self.conn)
        for hid in ('H1', 'H2'):
            service.upsert_hospital(self.conn, dict(id=hid, name=hid, lat=35.1, lon=127.1, capabilities=[], source='synthetic'))
            service.set_resource(self.conn, 1000, hid, 'ed_bed', 100, 0, source='hospital_input')
        for pid in people:
            service.create_patient(self.conn, 1000, pid, dict(reporter_role='transporter', consent=True, symptoms='synthetic unit', required_capabilities=[], resource_demands={'ed_bed': 1}, lat=35.0, lon=127.0, auto_inquiry=True, inquiry_policy=r.NAME))

    def offers(self, conn, now, pid, estimator):
        self.assertFalse(conn.in_transaction, 'travel/candidates must run outside a transaction')
        return dict(candidates=[dict(hospital_id=h, travel=dict(duration_seconds=100 + i), resources_enough=True) for i, h in enumerate(('H1', 'H2'))])

    def prepare(self, now=1000):
        with patch.object(r.service, 'candidates', side_effect=self.offers):
            return r._prepare_group(self.conn, now, object())

    def result(self, group):
        return dict(decisions=[dict(send=[hid], reason='unit', evidence=dict(policy=r.NAME), next_review_at=group['now'] + 15) for hid in ('H1', 'H2')], evidence={'unit': True})

    def snapshot(self):
        return {table: [tuple(row) for row in self.conn.execute('SELECT * FROM ' + table + ' ORDER BY 1')] for table in ('patients', 'inquiry_workflows', 'inquiries', 'inquiry_policy_decisions', 'events', 'planning_state')}

    def test_common_prefix_order_and_public_registry_capture(self):
        self.fresh(('C', 'B', 'A'))
        group = self.prepare()
        self.assertEqual([m['patient_id'] for m in group['members']], ['A', 'B'])
        self.assertEqual(group['context']['pair_patient_ids'], ['A', 'B'])
        self.assertEqual([p['id'] for p in group['registry_snapshot']['patients']], ['A', 'B', 'C'])
        self.assertEqual([m['patient_id'] for m in group['tail_members']], ['C'])
        for member in group['members']:
            self.assertEqual(len(member['inputs']), 8)
            self.assertEqual(member['inputs']['time_context']['event_prefix_max'], group['event_prefix_max'])
            self.assertEqual(member['inputs']['time_context']['decision_prefix_max'], group['decision_prefix_max'])
            self.assertEqual(member['workflow_snapshot']['apply_snapshot']['revision'], group['revision'])
            self.assertEqual(member['inputs']['patient_pending'], [])
        self.assertFalse(self.conn.in_transaction)

    def test_complete_owner_inventory_includes_queried_disabled_and_missing_job(self):
        self.fresh(('A', 'B', 'C', 'D', 'E'))
        unit_send(self.conn, 1000, 'C', ['H1'])
        self.conn.execute("UPDATE inquiry_workflows SET enabled=0 WHERE patient_id='D'")
        self.conn.execute("DELETE FROM inquiry_workflows WHERE patient_id='E'")
        group = self.prepare()
        owners = {o['patient']['id']: o for o in group['owner_context']['inventory']}
        self.assertEqual(list(owners), ['A', 'B', 'C', 'D', 'E'])
        self.assertEqual(group['owner_context']['first_cohort_patient_ids'], ['A', 'B'])
        self.assertEqual(owners['D']['job']['enabled'], 0)
        self.assertIsNone(owners['E']['job'])
        self.assertEqual(owners['C']['inputs']['max_new'], 9)
        self.assertEqual([c['hospital_id'] for c in owners['C']['inputs']['candidates']], ['H2'])
        self.assertEqual(owners['C']['inputs']['patient_pending'][0]['hospital'], 'H1')
        self.assertEqual(owners['C']['inputs']['patient_pending'][0]['travel_seconds'], 100)
        self.assertEqual([r['patient_id'] for r in group['owner_source_snapshot']['rows']], list(owners))
        self.assertFalse(self.conn.in_transaction)

    def test_residual_owner_silent_full_inquiry_changes_discard_pair(self):
        changes = ('attempts=attempts+1', 'expires_at=999', 'patient_version=patient_version+1', "reason='silent reason'")
        for change in changes:
            with self.subTest(change=change):
                self.fresh(('A', 'B', 'C'))
                unit_send(self.conn, 1000, 'C', ['H1'])
                group = self.prepare()
                revision = group['revision']
                self.conn.execute('UPDATE inquiries SET ' + change + " WHERE patient_id='C'")
                self.conn.execute('UPDATE planning_state SET revision=? WHERE id=1', (revision,))
                before = self.snapshot()
                self.assertIsNone(r._apply_group(self.conn, 1000, group, self.result(group)))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0], 0)

    def test_residual_owner_job_and_priority_silent_drift_discard_pair(self):
        for sql in ("UPDATE inquiry_workflows SET next_evaluation_at=1010 WHERE patient_id='C'", "UPDATE patients SET priority=1,priority_source='expert' WHERE id='C'"):
            with self.subTest(sql=sql):
                self.fresh(('A', 'B', 'C'))
                unit_send(self.conn, 1000, 'C', ['H1'])
                group = self.prepare()
                self.conn.execute(sql)
                self.conn.execute('UPDATE planning_state SET revision=? WHERE id=1', (group['revision'],))
                before = self.snapshot()
                self.assertIsNone(r._apply_group(self.conn, 1000, group, self.result(group)))
                self.assertEqual(self.snapshot(), before)

    def test_residual_owner_drift_during_external_estimation_discards_pair(self):
        self.fresh(('A', 'B', 'C'))
        unit_send(self.conn, 1000, 'C', ['H1'])
        seen = []

        def offers(conn, now, pid, estimator):
            result = self.offers(conn, now, pid, estimator)
            seen.append(pid)
            if pid == 'C':
                conn.execute("UPDATE inquiries SET attempts=attempts+1 WHERE patient_id='C'")
            return result
        with patch.object(r.service, 'candidates', side_effect=offers):
            self.assertIsNone(r._prepare_group(self.conn, 1000, object()))
        self.assertEqual(seen, ['A', 'B', 'C'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0], 0)

    def test_owner_context_omission_is_invalid_before_any_pair_write(self):
        self.fresh(('A', 'B', 'C'))
        unit_send(self.conn, 1000, 'C', ['H1'])
        group = self.prepare()
        group['owner_context']['inventory'].pop()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            r._apply_group(self.conn, 1000, group, self.result(group))
        self.assertEqual(self.snapshot(), before)

    def test_every_current_tail_offer_and_snapshot_is_from_the_shared_prefix(self):
        self.fresh(('D', 'C', 'B', 'A'))
        group = self.prepare()
        self.assertEqual([m['patient_id'] for m in group['members']], ['A', 'B'])
        self.assertEqual([m['patient_id'] for m in group['tail_members']], ['C', 'D'])
        for member in group['members'] + group['tail_members']:
            self.assertEqual(member['inputs']['max_new'], 10)
            self.assertEqual([c['hospital_id'] for c in member['inputs']['candidates']], ['H1', 'H2'])
            self.assertEqual(member['inputs']['time_context']['event_prefix_max'], group['event_prefix_max'])
            self.assertEqual(member['inputs']['time_context']['decision_prefix_max'], group['decision_prefix_max'])
            self.assertEqual(member['workflow_snapshot']['apply_snapshot']['revision'], group['revision'])
        before = self.snapshot()
        missing = deepcopy(group)
        missing['tail_members'].pop()
        self.assertIsNone(r._apply_group(self.conn, 1000, missing, self.result(missing)))
        self.assertEqual(self.snapshot(), before)

    def test_expert_and_unknown_priorities_are_captured_outside_the_legacy_inputs(self):
        self.fresh(('F', 'E', 'D', 'C', 'B', 'A'))
        values = [('A', 4, 'expert', 4), ('B', 2, 'expert', 2), ('C', 1, 'expert', 1), ('D', 1, 'self', 4), ('E', None, 'unknown', 4), ('F', 2, 'unknown', 4)]
        for pid, reported, source, effective in values:
            self.conn.execute('UPDATE patients SET priority=?,priority_source=? WHERE id=?', (reported, source, pid))
        group = self.prepare()
        context = group['priority_context']
        self.assertEqual(set(context), {'schema_version', 'now', 'event_prefix_max', 'decision_prefix_max', 'revision', 'source', 'patients'})
        self.assertEqual(context['schema_version'], 1)
        self.assertEqual(context['source'], r.cohort_foreignreplywake_context.PRIORITY_SOURCE)
        for key in ('now', 'event_prefix_max', 'decision_prefix_max', 'revision'):
            self.assertEqual(context[key], group[key])
        self.assertEqual(context['patients'], [dict(patient_id=pid, patient_version=1, reported_priority=reported, priority_source=source, effective_priority=effective) for pid, reported, source, effective in values])
        for member in group['members'] + group['tail_members']:
            self.assertEqual(set(member), {'patient_id', 'inputs', 'workflow_snapshot'})
            self.assertEqual(len(member['inputs']), 8)
            self.assertNotIn('priority_context', member['inputs'])
            self.assertNotIn('priority', member['inputs'])

    def test_unversioned_reported_priority_and_source_changes_discard_the_whole_group(self):
        changes = ("UPDATE patients SET priority=2 WHERE id='A'", "UPDATE patients SET priority_source='self' WHERE id='B'", "UPDATE patients SET priority=1,priority_source='expert' WHERE id='C'", "UPDATE patients SET priority=4,priority_source='expert' WHERE id='C'")
        for sql in changes:
            with self.subTest(sql=sql):
                self.fresh(('A', 'B', 'C'))
                group = self.prepare()
                self.conn.execute(sql)
                self.conn.execute('UPDATE planning_state SET revision=? WHERE id=1', (group['revision'],))
                self.assertEqual(r._watermarks(self.conn), {k: group[k] for k in ('event_prefix_max', 'decision_prefix_max', 'revision')})
                self.assertEqual(r._registry(self.conn), group['registry_snapshot'])
                before = self.snapshot()
                self.assertIsNone(r._apply_group(self.conn, 1000, group, self.result(group)))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)

    def test_priority_drift_during_unlocked_tail_estimation_discards_without_sends(self):
        self.fresh(('A', 'B', 'C'))
        marks = r._watermarks(self.conn)
        seen = []

        def drift(conn, now, pid, estimator):
            result = self.offers(conn, now, pid, estimator)
            seen.append(pid)
            if pid == 'C':
                conn.execute("UPDATE patients SET priority=2,priority_source='expert' WHERE id='C'")
                conn.execute('UPDATE planning_state SET revision=? WHERE id=1', (marks['revision'],))
            return result
        with patch.object(r.service, 'candidates', side_effect=drift):
            self.assertIsNone(r._prepare_group(self.conn, 1000, object()))
        self.assertEqual(seen, ['A', 'B', 'C'])
        self.assertEqual(r._watermarks(self.conn), marks)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)
        self.assertFalse(self.conn.in_transaction)

    def test_tail_version_state_due_and_eligibility_drift_discard_the_pair_atomically(self):
        changes = ("UPDATE patients SET version=version+1 WHERE id='C'", "UPDATE patients SET state='CANCELLED' WHERE id='C'", "UPDATE inquiry_workflows SET version=version+1 WHERE patient_id='C'", "UPDATE inquiry_workflows SET next_evaluation_at=1015 WHERE patient_id='C'", "DELETE FROM inquiry_workflows WHERE patient_id='C'")
        for sql in changes:
            with self.subTest(sql=sql):
                self.fresh(('A', 'B', 'C'))
                group = self.prepare()
                self.conn.execute(sql)
                before = self.snapshot()
                self.assertIsNone(r._apply_group(self.conn, 1000, group, self.result(group)))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)
        self.fresh(('A', 'B', 'C'))
        group = self.prepare()
        before = self.snapshot()
        with patch.object(inquiry_workflow, '_eligible_now', side_effect=lambda conn, now, p, h: p['id'] != 'C'):
            self.assertIsNone(r._apply_group(self.conn, 1000, group, self.result(group)))
        self.assertEqual(self.snapshot(), before)

    def test_all_tail_matches_are_checked_before_any_send_but_only_pair_is_applied(self):
        self.fresh(('A', 'B', 'C', 'D'))
        group = self.prepare()
        checks = []
        original = inquiry_workflow._matches

        def checked(conn, snapshot):
            checks.append((snapshot['patient_id'], conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0]))
            return original(conn, snapshot)
        with patch.object(inquiry_workflow, '_matches', side_effect=checked):
            self.assertIsNotNone(r._apply_group(self.conn, 1000, group, self.result(group)))
        self.assertEqual(checks, [('A', 0), ('B', 0), ('C', 0), ('D', 0)])
        self.assertEqual([row[0] for row in self.conn.execute('SELECT patient_id FROM inquiries ORDER BY id')], ['A', 'B'])
        self.assertEqual([row[0] for row in self.conn.execute('SELECT patient_id FROM inquiry_policy_decisions ORDER BY id')], ['A', 'B'])
        next_group = self.prepare()
        self.assertEqual([m['patient_id'] for m in next_group['members']], ['C', 'D'])
        self.assertEqual(next_group['tail_members'], [])
        self.assertGreater(next_group['event_prefix_max'], group['event_prefix_max'])
        self.assertGreater(next_group['decision_prefix_max'], group['decision_prefix_max'])
        self.assertGreater(next_group['revision'], group['revision'])

    def test_tail_drift_while_estimating_tail_offers_is_detected_without_sends(self):
        self.fresh(('A', 'B', 'C'))
        seen = []

        def drift(conn, now, pid, estimator):
            result = self.offers(conn, now, pid, estimator)
            seen.append(pid)
            if pid == 'C':
                conn.execute("UPDATE inquiry_workflows SET version=version+1 WHERE patient_id='C'")
            return result
        with patch.object(r.service, 'candidates', side_effect=drift):
            self.assertIsNone(r._prepare_group(self.conn, 1000, object()))
        self.assertEqual(seen, ['A', 'B', 'C'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)
        self.assertFalse(self.conn.in_transaction)

    def test_first_send_revision_does_not_invalidate_prechecked_second_member(self):
        group = self.prepare()
        checks = []
        original = inquiry_workflow._matches

        def checked(conn, snapshot):
            checks.append((snapshot['patient_id'], r._watermarks(conn)['revision'], conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0]))
            return original(conn, snapshot)
        with patch.object(inquiry_workflow, '_matches', side_effect=checked):
            saved = r._apply_group(self.conn, 1000, group, self.result(group))
        self.assertEqual(checks, [('A', group['revision'], 0), ('B', group['revision'], 0)])
        self.assertEqual(saved['db_decision_ids'], [1, 2])
        self.assertTrue(saved['validated_before_write'])
        self.assertEqual([row[0] for row in self.conn.execute('SELECT patient_id FROM inquiries ORDER BY id')], ['A', 'B'])
        self.assertGreater(r._watermarks(self.conn)['revision'], group['revision'])
        self.assertFalse(self.conn.in_transaction)

    def test_failure_of_second_member_rolls_back_decisions_sends_jobs_events_and_revision(self):
        group = self.prepare()
        before = self.snapshot()
        original = r._write_member
        calls = []

        def fail_second(conn, now, member, decision):
            calls.append(member['patient_id'])
            if len(calls) == 2:
                raise RuntimeError('synthetic second-member failure')
            return original(conn, now, member, decision)
        with patch.object(r, '_write_member', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'second-member'):
                r._apply_group(self.conn, 1000, group, self.result(group))
        self.assertEqual(calls, ['A', 'B'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.conn.in_transaction)

    def test_any_member_version_state_due_or_registry_change_discards_whole_group(self):
        changes = ("UPDATE patients SET version=version+1 WHERE id='B'", "UPDATE patients SET state='CANCELLED' WHERE id='B'", "UPDATE inquiry_workflows SET version=version+1 WHERE patient_id='B'", "UPDATE inquiry_workflows SET enabled=0 WHERE patient_id='B'", "UPDATE inquiry_workflows SET next_evaluation_at=1015 WHERE patient_id='B'", "UPDATE inquiry_workflows SET deadline_at=1000 WHERE patient_id='B'", "DELETE FROM inquiry_workflows WHERE patient_id='B'", 'UPDATE planning_state SET revision=revision+1 WHERE id=1')
        for sql in changes:
            with self.subTest(sql=sql):
                self.fresh()
                group = self.prepare()
                self.conn.execute(sql)
                before = self.snapshot()
                self.assertIsNone(r._apply_group(self.conn, 1000, group, self.result(group)))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)

    def test_changed_event_or_decision_prefix_discards_without_partial_send(self):
        for kind in ('event', 'decision', 'inquiry'):
            with self.subTest(kind=kind):
                self.fresh()
                group = self.prepare()
                if kind == 'event':
                    service.log(self.conn, 1000, 'unit', 'unit_public_change', 'B')
                elif kind == 'decision':
                    self.conn.execute('INSERT INTO inquiry_policy_decisions (patient_id,at,policy_name,policy_version,job_version,reason,hospital_ids,evidence) VALUES (?,?,?,?,?,?,?,?)', ('B', 1000, r.NAME, 1, 1, 'unit', '[]', '{}'))
                else:
                    unit_send(self.conn, 1000, 'B', ['H1'])
                before = self.snapshot()
                self.assertIsNone(r._apply_group(self.conn, 1000, group, self.result(group)))
                self.assertEqual(self.snapshot(), before)

    def test_eligibility_change_without_revision_is_rechecked_for_entire_pair(self):
        group = self.prepare()
        before = self.snapshot()

        def eligible(conn, now, patient, hid):
            return patient['id'] != 'B'
        with patch.object(inquiry_workflow, '_eligible_now', side_effect=eligible):
            self.assertIsNone(r._apply_group(self.conn, 1000, group, self.result(group)))
        self.assertEqual(self.snapshot(), before)

    def test_snapshot_drift_during_unlocked_estimation_is_discarded(self):
        seen = []

        def drift(conn, now, pid, estimator):
            result = self.offers(conn, now, pid, estimator)
            seen.append(pid)
            if pid == 'B':
                conn.execute("UPDATE inquiry_workflows SET version=version+1 WHERE patient_id='A'")
            return result
        with patch.object(r.service, 'candidates', side_effect=drift):
            self.assertIsNone(r._prepare_group(self.conn, 1000, object()))
        self.assertEqual(seen, ['A', 'B'])
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0], 0)

    def test_one_current_first_job_and_future_or_not_due_jobs_do_not_pair(self):
        self.fresh(('A',))
        self.assertIsNone(self.prepare())
        self.fresh()
        self.conn.execute("UPDATE inquiry_workflows SET next_evaluation_at=1015 WHERE patient_id='B'")
        self.assertIsNone(self.prepare())
        self.conn.execute("UPDATE inquiry_workflows SET next_evaluation_at=1000,started_at=1015 WHERE patient_id='B'")
        self.assertIsNone(self.prepare())

    def test_invalid_output_fails_before_writes_and_clock_drift_discards_group(self):
        group = self.prepare()
        before = self.snapshot()
        invalid = self.result(group)
        invalid['decisions'][1]['send'] = ['UNKNOWN']
        with self.assertRaises(ValueError):
            r._apply_group(self.conn, 1000, group, invalid)
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(r._apply_group(self.conn, 1001, group, self.result(group)))
        self.assertEqual(self.snapshot(), before)
