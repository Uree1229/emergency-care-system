"""Committed input uncertainty display across allocation and recommendation APIs.

Synthetic in-memory/temporary DB only. These tests verify display contracts and preserve
the existing solver model/acceptance behavior; they add no clinical thresholds.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import db, planning, recommendation_view, service, solver_adapter
from server import voice_input as voice
from server.travel import TravelEstimator
from solver_improved import solve


NOW = 2_000_000_000
EST = TravelEstimator(use_kakao=False)


class AllocationInputReviewIntegration(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(':memory:')
        db.init(self.conn)
        self.counter = 0
        self.actor = 'INPUT-REVIEW-QA'
        service.upsert_hospital(self.conn, dict(id='INPUT-H1', name='Synthetic input hospital',
            lat=35.161, lon=126.85, capabilities=['응급의학', '심장내과'], source='synthetic'))
        for resource in ('ed_bed', 'special_team'):
            service.set_resource(self.conn, NOW, 'INPUT-H1', resource, 5, 0, source='synthetic')

    def tearDown(self):
        self.conn.close()

    def extra(self, **changes):
        value = dict(treatment_profile_id='TR_GENERAL_ED', avpu='A', chief_complaint='cardiac',
            age_group='adult', pre_ktas_level=2, stemi_suspected='no',
            vitals={'sbp':120, 'dbp':80, 'hr':90})
        value.update(copy.deepcopy(changes))
        return value

    def patient(self, **changes):
        payload = dict(reporter_role='transporter', consent=True, age=47, symptoms='흉통',
            consciousness='alert', breathing='normal', priority=4, priority_source='expert',
            lat=35.16, lon=126.85, required_capabilities=[], resource_demands={'ed_bed':1},
            extra=self.extra())
        payload.update(changes)
        return service.create_patient(self.conn, NOW, 'INPUT-P1', payload)

    def common(self, session=None, **extra):
        self.counter += 1
        return dict(actor_id=self.actor, client_request_id='input-review-'+str(self.counter),
            **({'expected_version':session['version']} if session else {}), **extra)

    def linked(self):
        return voice.create(self.conn, NOW, self.common(patient_id='INPUT-P1'))['session']

    def edit(self, session, changes):
        return voice.edit(self.conn, NOW, session['id'], self.common(session,
            changes=changes, change_kind='observation'))['session']

    def apply(self, session):
        session = voice.confirm(self.conn, NOW, session['id'],
            self.common(session, confirmed=True))['session']
        return voice.submit(self.conn, NOW, session['id'], self.common(session))

    def result(self, now=NOW):
        return recommendation_view.patient_result(self.conn, now, 'INPUT-P1')

    def accept(self):
        service.send_inquiries(self.conn, NOW, 'INPUT-P1', ['INPUT-H1'])
        iid = self.conn.execute('SELECT id FROM inquiries WHERE patient_id=?', ('INPUT-P1',)).fetchone()[0]
        service.respond(self.conn, NOW, iid, 'accept', 'SYNTHETIC-DESK')
        return iid

    def current_plan(self):
        snapshot = planning.capture(self.conn, NOW)
        model, context = solver_adapter.build(snapshot, EST)
        plan, meta = solve(model, seconds=0)
        return snapshot, model, context, solver_adapter.present(snapshot, model, context, plan, meta)

    def publish(self):
        snapshot, _, _, results = self.current_plan()
        self.conn.execute("UPDATE planning_state SET lease_owner='input-review-test' WHERE id=1")
        self.assertTrue(planning.publish(self.conn, snapshot, results, NOW, 'input-review-test'))
        return results

    def state(self):
        tables = ('patients', 'inquiries', 'planning_state', 'planning_results', 'events',
                  'voice_sessions', 'voice_utterances', 'voice_revisions', 'voice_requests',
                  'voice_followup_answers', 'patient_amendments')
        return {table:[tuple(row) for row in self.conn.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                for table in tables}

    @staticmethod
    def items(review, field):
        return [item for item in review['items']
                if item.get('field') == field or field in item.get('fields', ())]

    def test_current_input_review_is_available_when_no_recommendation_exists(self):
        self.patient(priority=None, priority_source='unknown')
        before = self.state()
        result = self.result()
        self.assertEqual(result['status'], 'QUEUED')
        self.assertIsNone(result['result'])
        self.assertTrue(result['input_review']['requires_review'])
        self.assertFalse(result['input_review']['blocking'])
        self.assertTrue(any(item['code'] == 'PRIORITY_UNVERIFIED' for item in result['input_review']['items']))
        self.assertEqual(self.state(), before)

    def test_error_stale_and_terminal_envelopes_keep_current_review_and_hide_result(self):
        self.patient(priority=None, priority_source='unknown')
        self.conn.execute("UPDATE planning_state SET last_error='SYNTHETIC_FAILURE' WHERE id=1")
        error = self.result()
        self.assertEqual(error['status'], 'ERROR')
        self.assertIsNone(error['result'])
        self.assertTrue(error['input_review']['requires_review'])
        self.conn.execute('UPDATE planning_state SET last_error=NULL WHERE id=1')
        self.accept()
        self.publish()
        before = self.state()
        stale = self.result(NOW + 31)
        self.assertEqual(stale['status'], 'STALE')
        self.assertIsNone(stale['result'])
        self.assertTrue(stale['input_review']['requires_review'])
        self.assertEqual(self.state(), before)
        service.cancel_patient(self.conn, NOW, 'INPUT-P1', 'Synthetic cancellation')
        before = self.state()
        terminal = self.result()
        self.assertEqual(terminal['status'], 'NOT_APPLICABLE')
        self.assertIsNone(terminal['result'])
        self.assertIn('input_review', terminal)
        self.assertEqual(self.state(), before)

    def test_known_expert_priority_does_not_require_raw_prektas_to_exist(self):
        extra = self.extra()
        extra.pop('pre_ktas_level')
        self.patient(extra=extra)
        review = self.result()['input_review']
        self.assertFalse(review['requires_review'])
        self.assertFalse(any(item['code'] == 'PRIORITY_UNVERIFIED' for item in review['items']))

    def test_optional_unmeasured_observation_does_not_remove_valid_acceptance_or_recommendation(self):
        extra = self.extra(voice_observations={
            'fields':{'pulse':90}, 'field_metadata':{
                'pulse':{'needs_clarification':True, 'followup_unavailable_for':'pulse', 'observed_at':NOW - 5000}}})
        self.patient(extra=extra)
        iid = self.accept()
        results = self.publish()
        review = results['INPUT-P1']['input_review']
        self.assertEqual(review['status'], 'INFORMATION_ONLY')
        self.assertFalse(review['requires_review'])
        self.assertTrue(self.items(review, 'pulse'))
        self.assertTrue(all(item['level'] == 'info' for item in self.items(review, 'pulse')))
        self.assertEqual(results['INPUT-P1']['status'], 'RECOMMENDED')
        self.assertTrue(results['INPUT-P1']['confirmable_now'])
        self.assertEqual(service._inquiry(self.conn, iid)['state'], 'ACCEPTED')

    def test_selected_complaint_uncertainty_is_displayed_without_changing_solver_inputs(self):
        self.patient(extra=self.extra(stemi_suspected='unmeasured'))
        iid = self.accept()
        snapshot, model, context, results = self.current_plan()
        self.assertTrue(context['INPUT-P1']['input_review']['requires_review'])
        items = self.items(results['INPUT-P1']['input_review'], 'stemi_suspected')
        self.assertTrue(items)
        self.assertTrue(any(item['level'] == 'review' for item in items))
        resolved = copy.deepcopy(snapshot)
        resolved['patients'][0]['extra']['stemi_suspected'] = 'no'
        clean_model, _ = solver_adapter.build(resolved, EST)
        self.assertEqual(model, clean_model)
        self.assertEqual(results['INPUT-P1']['status'], 'RECOMMENDED')
        self.assertTrue(results['INPUT-P1']['confirmable_now'])
        self.assertEqual(service._inquiry(self.conn, iid)['state'], 'ACCEPTED')

    def test_irrelevant_secondary_unknown_does_not_become_a_treatment_requirement(self):
        extra = self.extra(chief_complaint='trauma', stemi_suspected='unmeasured', injury_region='limb')
        self.patient(extra=extra)
        review = self.result()['input_review']
        self.assertFalse(review['requires_review'])
        self.assertTrue(all(item['level'] == 'info' for item in self.items(review, 'stemi_suspected')))
        self.assertEqual(service._patient(self.conn, 'INPUT-P1')['resource_demands'], {'ed_bed':1})

    def test_unsubmitted_voice_draft_is_not_mixed_into_allocation_review(self):
        self.patient()
        before = self.result()['input_review']
        session = self.edit(self.linked(), {'stemi_suspected':'unmeasured'})
        after = self.result()['input_review']
        self.assertEqual(after, before)
        self.assertEqual(service._patient(self.conn, 'INPUT-P1')['extra']['stemi_suspected'], 'no')
        self.assertEqual(session['state'], 'DRAFT')

    def test_confirmed_patient_correction_resolves_committed_review_item(self):
        self.patient(extra=self.extra(stemi_suspected='unmeasured'))
        self.assertTrue(self.items(self.result()['input_review'], 'stemi_suspected'))
        self.apply(self.edit(self.linked(), {'stemi_suspected':'no'}))
        result = self.result()
        self.assertFalse(self.items(result['input_review'], 'stemi_suspected'))
        self.assertFalse(result['input_review']['requires_review'])
        self.assertEqual(service._patient(self.conn, 'INPUT-P1')['extra']['stemi_suspected'], 'no')

    def test_new_manual_value_does_not_inherit_old_voice_clarification(self):
        extra = self.extra(voice_observations={'fields':{'stemi_suspected':'unmeasured'},
            'field_metadata':{'stemi_suspected':{'needs_clarification':True,
                'clarification_evidence':['PRIVATE-OLD-VOICE-TEXT'], 'observed_at':NOW - 60}}})
        # Current canonical value is 'no'; voice snapshot belongs to an earlier observation.
        self.patient(extra=extra)
        review = self.result()['input_review']
        self.assertFalse(self.items(review, 'stemi_suspected'))
        self.assertFalse(review['requires_review'])

    def test_public_review_excludes_private_voice_provenance_and_raw_quotes(self):
        extra = self.extra(stemi_suspected='unmeasured', voice_observations={
            'session_id':'PRIVATE-VOICE-SESSION-999', 'actor_id':'PRIVATE-OWNER-999',
            'fields':{'stemi_suspected':'unmeasured'}, 'field_metadata':{'stemi_suspected':{
                'needs_clarification':True, 'evidence':['PRIVATE-RAW-QUOTE-999'],
                'clarification_evidence':['PRIVATE-CLARIFICATION-999'], 'observed_at':NOW - 5}}})
        self.patient(extra=extra)
        review = self.result()['input_review']
        encoded = json.dumps(review, ensure_ascii=False)
        for secret in ('PRIVATE-VOICE-SESSION-999', 'PRIVATE-OWNER-999',
                       'PRIVATE-RAW-QUOTE-999', 'PRIVATE-CLARIFICATION-999'):
            self.assertNotIn(secret, encoded)
        self.assertTrue(review['requires_review'])

    def test_ready_result_preserves_worker_snapshot_and_adds_current_top_level_review(self):
        self.patient(extra=self.extra(stemi_suspected='unmeasured'))
        self.accept()
        self.publish()
        stored = planning.patient_result(self.conn, NOW, 'INPUT-P1')
        self.assertIn('input_review', stored['result'])
        before = self.state()
        result = self.result(NOW + 5)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['result'], stored['result'])
        self.assertTrue(result['input_review']['requires_review'])
        self.assertEqual(self.state(), before)

    def test_hospital_result_contains_current_input_review_without_patient_or_planning_mutation(self):
        self.patient(extra=self.extra(stemi_suspected='unmeasured'))
        iid = self.accept()
        service.confirm_destination(self.conn, NOW, 'INPUT-P1', iid)
        before = self.state()
        rows = recommendation_view.hospital_results(self.conn, NOW, 'INPUT-H1')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['patient_id'], 'INPUT-P1')
        self.assertTrue(rows[0]['input_review']['requires_review'])
        self.assertFalse(rows[0]['input_review']['blocking'])
        self.assertEqual(self.state(), before)

    def test_wal_read_snapshot_keeps_plan_and_current_review_consistent_without_blocking_writer(self):
        scratch = Path(__file__).resolve().parents[2] / '.codex-build' / 'allocation-input-review-20261030' / 'tmp'
        scratch.mkdir(parents=True, exist_ok=True)
        original_conn = self.conn
        with tempfile.TemporaryDirectory(prefix='input-review-wal-', dir=scratch) as directory:
            path = str(Path(directory) / 'snapshot.db')
            reader = db.connect(path)
            writer = None
            try:
                original_conn.backup(reader)
                self.conn = reader
                self.patient()
                self.publish()
                saved = planning.patient_result(reader, NOW, 'INPUT-P1')
                self.assertEqual(saved['status'], 'READY')
                self.assertFalse(saved['result']['input_review']['requires_review'])
                initial_version = service._patient(reader, 'INPUT-P1')['version']
                self.assertEqual(reader.execute('PRAGMA journal_mode').fetchone()[0], 'wal')

                writer = db.connect(path)
                # A mistakenly acquired IMMEDIATE lock must fail this write promptly.
                writer.execute('PRAGMA busy_timeout=100')
                real_patient_result = planning.patient_result
                commits = []

                def change_patient_after_plan_read(conn, now, pid):
                    envelope = real_patient_result(conn, now, pid)
                    self.assertTrue(reader.in_transaction)
                    service.update_patient(writer, NOW + 1, pid,
                        {'extra':self.extra(stemi_suspected='unmeasured')}, actor=self.actor)
                    self.assertFalse(writer.in_transaction)
                    commits.append(service._patient(writer, pid)['version'])
                    return envelope

                with patch.object(planning, 'patient_result', side_effect=change_patient_after_plan_read):
                    first = self.result()

                # The writer committed between reads, but this response uses one old snapshot.
                self.assertEqual(commits, [initial_version + 1])
                self.assertEqual(first['status'], 'READY')
                self.assertEqual(first['result'], saved['result'])
                self.assertEqual(first['input_review'], saved['result']['input_review'])
                self.assertFalse(first['input_review']['requires_review'])
                self.assertFalse(reader.in_transaction)

                # The next request observes both the new patient and invalidated plan.
                second = self.result(NOW + 1)
                self.assertEqual(second['status'], 'STALE')
                self.assertIsNone(second['result'])
                self.assertTrue(second['input_review']['requires_review'])
                self.assertTrue(self.items(second['input_review'], 'stemi_suspected'))
                self.assertEqual(service._patient(reader, 'INPUT-P1')['version'], initial_version + 1)
                self.assertFalse(reader.in_transaction)
            finally:
                self.conn = original_conn
                if writer is not None:
                    writer.close()
                reader.close()


if __name__ == '__main__':
    unittest.main()
