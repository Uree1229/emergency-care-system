from copy import deepcopy
import unittest

from inquiry_lab import horizon_context as c


def event(event_id, kind, at, patient='P', **data):
    return dict(id=event_id, at=at, patient_id=patient, type=kind,
                actor='public', hospital_id=None, data=data)


def values(**overrides):
    out = dict(now=30, patient_id='P', job_version=3, event_prefix_max=10,
        decision_prefix_max=50,
        job=dict(patient_id='P', version=3, started_at=0, deadline_at=300,
                 enabled=1, policy_name='eat_horizon_l15_f'),
        events=[event(1, 'patient_created', 0),
                event(2, 'inquiry_workflow_started', 0, deadline_seconds=300)])
    out.update(overrides)
    return out


def view(**overrides):
    return c.build_context(**values(**overrides))


class HorizonContextTests(unittest.TestCase):
    def test_explicit_deadlines_and_minimal_source_provenance(self):
        context = view()
        self.assertEqual(set(context), {'patient_id', 'job_version',
            'event_prefix_max', 'decision_prefix_max',
            'auto_inquiry_deadline_at', 'evaluation_end_at', 'source'})
        self.assertEqual((context['auto_inquiry_deadline_at'], context['evaluation_end_at']),
                         (300, 300))
        self.assertEqual(context['source'], dict(
            workflow=dict(patient_id='P', version=3, started_at=0, deadline_at=300, enabled=1),
            workflow_start_event=dict(id=2, at=0, type='inquiry_workflow_started',
                patient_id='P', deadline_seconds=300, restarted=False),
            patient_created_event=dict(id=1, at=0, type='patient_created', patient_id='P'),
            evaluation_protocol=dict(name='fixed_lab_patient_lifetime', duration_seconds=300)))
        self.assertNotIn('actor', context['source']['patient_created_event'])

    def test_horizon_remains_anchored_after_inquiries_resolve_or_first_contact_is_delayed(self):
        rows = values()['events'] + [event(3, 'inquiry_sent', 100, hospital='H1'),
            event(4, 'inquiry_closed', 150, hospital='H1'),
            event(5, 'inquiry_sent', 200, hospital='H2')]
        self.assertEqual(view(now=250, events=rows)['evaluation_end_at'], 300)
        self.assertEqual(view(now=250)['evaluation_end_at'], 300)

    def test_restart_uses_latest_start_and_current_job_version_but_original_created_H(self):
        rows = values()['events'] + [event(3, 'inquiry_workflow_paused', 60),
            event(4, 'inquiry_workflow_started', 100, deadline_seconds=300, restarted=True)]
        job = dict(patient_id='P', version=8, started_at=100, deadline_at=400, enabled=1)
        context = view(now=120, job_version=8, job=job, events=reversed(rows))
        self.assertEqual(context['auto_inquiry_deadline_at'], 400)
        self.assertEqual(context['evaluation_end_at'], 300)
        self.assertEqual(context['source']['workflow_start_event']['id'], 4)
        self.assertTrue(context['source']['workflow_start_event']['restarted'])

    def test_A60_H300_allows_pending_reply_evaluation_after_A(self):
        rows = [event(1, 'patient_created', 0),
            event(2, 'inquiry_workflow_started', 0, deadline_seconds=60),
            event(3, 'inquiry_sent', 45, hospital='H')]
        job = dict(patient_id='P', version=3, started_at=0, deadline_at=60, enabled=1)
        for now in (60, 75, 120, 299):
            with self.subTest(now=now):
                context = view(now=now, job=job, events=rows)
                self.assertEqual(context['auto_inquiry_deadline_at'], 60)
                self.assertEqual(context['evaluation_end_at'] - now, 300-now)
                self.assertEqual(c.validate_context(context, now=now), context)

    def test_one_to_fourteen_seconds_remaining_is_not_extended(self):
        for remaining in range(1, 15):
            with self.subTest(remaining=remaining):
                context = view(now=300-remaining)
                self.assertEqual(context['evaluation_end_at'] - (300-remaining), remaining)
        for now in (300, 301):
            with self.subTest(now=now), self.assertRaises(ValueError):
                view(now=now)

    def test_created_clock_is_public_and_not_assumed_zero_or_workflow_start(self):
        rows = [event(1, 'patient_created', 1000),
                event(2, 'inquiry_workflow_started', 1050, deadline_seconds=60)]
        job = dict(patient_id='P', version=3, started_at=1050, deadline_at=1110, enabled=1)
        context = view(now=1100, job=job, events=rows)
        self.assertEqual((context['auto_inquiry_deadline_at'], context['evaluation_end_at']),
                         (1110, 1300))

    def test_same_second_events_beyond_watermark_cannot_change_sources(self):
        rows = values()['events'] + [event(11, 'inquiry_workflow_started', 0,
            deadline_seconds=60, restarted=True), event(12, 'patient_created', 0)]
        context = view(events=rows)
        self.assertEqual(context['source']['workflow_start_event']['id'], 2)
        self.assertEqual(context['auto_inquiry_deadline_at'], 300)
        with self.assertRaises(ValueError):
            view(event_prefix_max=11, events=rows)

    def test_no_older_start_fallback_for_mismatched_or_future_latest_public_start(self):
        for newest in (event(3, 'inquiry_workflow_started', 20, deadline_seconds=300,
                             restarted=True),
                       event(3, 'inquiry_workflow_started', 31, deadline_seconds=300,
                             restarted=True)):
            with self.subTest(newest=newest), self.assertRaises(ValueError):
                view(events=values()['events'] + [newest])

    def test_other_patient_sources_do_not_substitute_and_patient_ids_keep_type(self):
        rows = [event(1, 'patient_created', 0, patient='OTHER'),
                event(2, 'inquiry_workflow_started', 0, deadline_seconds=300)]
        with self.assertRaises(ValueError):
            view(events=rows)
        job = dict(patient_id=1, version=3, started_at=0, deadline_at=300, enabled=1)
        rows = [event(1, 'patient_created', 0, patient=1),
                event(2, 'inquiry_workflow_started', 0, patient=1, deadline_seconds=300)]
        self.assertEqual(view(patient_id=1, job=job, events=rows)['patient_id'], 1)
        rows[0]['patient_id'] = True
        with self.assertRaises(ValueError):
            view(patient_id=1, job=job, events=rows)

    def test_missing_duplicate_and_malformed_public_sources_fail_closed(self):
        base = values()['events']
        cases = [[], base[:1], base[1:],
            base + [event(3, 'patient_created', 0)],
            base + [event(2, 'inquiry_sent', 5)],
            [base[0], event(2, 'inquiry_workflow_started', 0)],
            [base[0], event(2, 'inquiry_workflow_started', 0, deadline_seconds=True)],
            [base[0], event(2, 'inquiry_workflow_started', 0, deadline_seconds=300.0)],
            [base[0], event(2, 'inquiry_workflow_started', 0, deadline_seconds=300, restarted=1)],
            [event(1, 'patient_created', 31), base[1]],
            [event(4, 'patient_created', 0), base[1]],
            [event(1, 'patient_created', 1), base[1]]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                view(events=rows)
        raw = deepcopy(base)
        raw[1]['data'] = '{"deadline_seconds":300}'
        with self.assertRaises(ValueError):
            view(events=raw)

    def test_current_job_projection_and_snapshot_identity_are_required(self):
        changes = ({'patient_id': 'OTHER'}, {'version': 4}, {'version': True},
                   {'started_at': 31}, {'deadline_at': 0}, {'deadline_at': 60},
                   {'enabled': 0}, {'enabled': True})
        for change in changes:
            job = deepcopy(values()['job'])
            job.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                view(job=job)
        job = deepcopy(values()['job'])
        del job['version']
        with self.assertRaises(ValueError):
            view(job=job)

    def test_invalid_identity_clock_versions_and_prefix_types(self):
        for override in ({'patient_id': ''}, {'patient_id': False}, {'now': True},
            {'now': -1}, {'now': 1.5}, {'job_version': 0}, {'job_version': True},
            {'event_prefix_max': -1}, {'event_prefix_max': True},
            {'decision_prefix_max': -1}, {'decision_prefix_max': 0.0}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                view(**override)

    def test_validator_binds_all_expected_snapshot_fields(self):
        context = view()
        expected = dict(now=30, patient_id='P', job_version=3,
                        event_prefix_max=10, decision_prefix_max=50)
        self.assertEqual(c.validate_context(context, **expected), context)
        for change in ({'patient_id': 'OTHER'}, {'job_version': 4},
            {'event_prefix_max': 11}, {'decision_prefix_max': 51},
            {'job_version': True}, {'event_prefix_max': True},
            {'decision_prefix_max': False}):
            args = dict(expected, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                c.validate_context(context, **args)

    def test_validator_rejects_tampered_boundaries_source_and_unknown_fields(self):
        replacements = [
            ((), 'auto_inquiry_deadline_at', 301),
            ((), 'evaluation_end_at', 301),
            ((), 'job_version', 4),
            ((), 'patient_id', 'OTHER'),
            ((), 'event_prefix_max', 1),
            ((), 'decision_prefix_max', True),
            ((), 'extra', 'unreviewed'),
            (('source',), 'extra', 'unreviewed'),
            (('source', 'workflow'), 'version', 4),
            (('source', 'workflow'), 'enabled', 0),
            (('source', 'workflow'), 'extra', 'unreviewed'),
            (('source', 'workflow_start_event'), 'deadline_seconds', 60),
            (('source', 'workflow_start_event'), 'at', 1),
            (('source', 'workflow_start_event'), 'restarted', 1),
            (('source', 'workflow_start_event'), 'type', 'inquiry_workflow_paused'),
            (('source', 'workflow_start_event'), 'id', 11),
            (('source', 'workflow_start_event'), 'job_version', 3),
            (('source', 'patient_created_event'), 'patient_id', 'OTHER'),
            (('source', 'patient_created_event'), 'at', 1),
            (('source', 'patient_created_event'), 'id', 2),
            (('source', 'evaluation_protocol'), 'name', 'workflow_deadline'),
            (('source', 'evaluation_protocol'), 'duration_seconds', 60),
            (('source', 'evaluation_protocol'), 'duration_seconds', 300.0)]
        for path, key, value in replacements:
            context = view()
            row = context
            for component in path:
                row = row[component]
            row[key] = value
            with self.subTest(path=path, key=key), self.assertRaises(ValueError):
                c.validate_context(context, now=30)

    def test_no_mutation_one_shot_events_and_independent_validator_copy(self):
        args = values()
        before = deepcopy(args)
        context = c.build_context(**dict(args, events=iter(args['events'])))
        self.assertEqual(args, before)
        validated = c.validate_context(context, now=30)
        self.assertEqual(validated, context)
        validated['source']['workflow']['version'] = 99
        self.assertEqual(context['source']['workflow']['version'], 3)
        context['source']['patient_created_event']['at'] = 99
        self.assertEqual(args, before)


if __name__ == '__main__':
    unittest.main()
