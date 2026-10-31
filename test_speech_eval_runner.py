"""Evaluation integrity and separation of actual speech from synthetic examples."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from speech_eval import runner


class EvaluationIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = {'schema_version': 1, 'dataset_id': 'test-development',
            'purpose': 'development_synthetic', 'observed_at': 1793332800,
            'cases': [
                {'id': 'a', 'family': 'observations', 'template_group': 'a', 'mode': 'dictation',
                 'reference_text': '혈압 120에 80.', 'tags': [],
                 'expected': {'action': 'dictation', 'fields': {'systolic_bp': 120, 'diastolic_bp': 80}}},
                {'id': 'b', 'family': 'negation', 'template_group': 'b', 'mode': 'dictation',
                 'reference_text': '흉통 없음.', 'tags': [],
                 'expected': {'action': 'dictation', 'fields': {'denied_symptoms': ['흉통']}}},
                {'id': 'c', 'family': 'command', 'template_group': 'c', 'mode': 'command',
                 'reference_text': '맥박 92로 수정해.', 'tags': [],
                 'expected': {'action': 'edit', 'fields': {'pulse': 92}}}]}
        self.corpus = self.root / 'corpus.json'
        self.write(self.corpus, self.data)
        self.data['_path'] = str(self.corpus)

    def write(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def record(self, **overrides):
        value = {'recording_id': 'r1', 'case_id': 'a', 'source': 'human_voice',
            'engine': 'test-stt', 'speaker_id': 'S01', 'condition': 'quiet', 'status': 'ok',
            'hypothesis': '혈압 120에 80.', 'reference_verified': True, 'timing': {}}
        return value | overrides

    def package(self, rows):
        path = self.root / 'recordings.json'
        self.write(path, {'schema_version': 1, 'dataset_id': self.data['dataset_id'],
            'corpus_sha256': hashlib.sha256(self.corpus.read_bytes()).hexdigest(), 'recordings': rows})
        return path

    def test_modes_follow_the_actual_app_and_edit_metadata_is_not_a_review_target(self):
        report = runner.evaluate(self.data, [])
        self.assertEqual(report['reference_baseline']['overall']['exact_count'], 3)
        self.assertEqual(report['human_speech_status'], 'NOT_MEASURED')
        self.assertEqual(report['observations_by_source'], {})
        case = self.data['cases'][2]
        modified = copy.deepcopy(case); modified['mode'] = 'dictation'
        self.assertEqual(runner.predict(modified, case['reference_text'], self.data['observed_at'])['action'], 'dictation')

    def test_recognition_failures_count_in_attempts_and_structure_accuracy(self):
        rows = [self.record(), self.record(recording_id='r2', status='no_match', hypothesis='')]
        report = runner.evaluate(self.data, rows)
        human = report['observations_by_source']['human_voice']
        self.assertEqual((human['attempts'], human['failed_recognitions']), (2, 1))
        self.assertEqual(human['metrics']['overall']['exact_accuracy'], 0.5)
        self.assertEqual(human['text']['measured_n'], 1)
        self.assertEqual(human['timing']['methods']['voice']['total_input_ms']['n'], 0)
        self.assertIsNone(human['timing']['methods']['voice']['total_input_ms']['mean'])

    def test_synthetic_corruption_never_becomes_real_voice_accuracy(self):
        simulated = self.record(source='synthetic_corruption', hypothesis='혈압 90에 60.')
        report = runner.evaluate(self.data, [simulated])
        self.assertEqual(report['human_speech_status'], 'NOT_MEASURED')
        self.assertNotIn('human_voice', report['observations_by_source'])
        self.assertEqual(report['recording_rows'][0]['paired_outcome'], 'stt_associated_regression')
        self.assertEqual(len(report['recording_rows'][0]['numeric_errors']), 2)

    def test_polarity_flip_is_separate_from_no_mention(self):
        report = runner.evaluate(self.data, [self.record(case_id='b', hypothesis='흉통 있음.')])
        row = report['recording_rows'][0]
        self.assertEqual(len(row['symptom_polarity_flips']), 1)
        self.assertEqual(row['field_missing'], ['denied_symptoms'])
        self.assertEqual(row['field_unexpected'][0]['field'], 'symptoms')

    def test_unknown_duplicate_unverified_and_empty_success_records_are_rejected(self):
        for rows in ([self.record(), self.record()], [self.record(case_id='unknown')],
                     [self.record(reference_verified=False)], [self.record(hypothesis='')],
                     [self.record(source='unspecified')]):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    runner.observations(self.package(rows), self.data)

    def test_empty_real_recording_template_is_valid_and_not_a_success(self):
        self.assertEqual(runner.observations(self.package([]), self.data), [])
        report = runner.run(self.corpus, None, self.root / 'result')
        self.assertEqual(report['human_speech_status'], 'NOT_MEASURED')
        self.assertEqual(json.loads((self.root / 'result/recording_template.json').read_text())['recordings'], [])
        with self.assertRaises(ValueError):
            runner.run(self.corpus, None, self.root / 'result')

    def test_source_change_prevents_report_publication(self):
        real = runner.fingerprints
        before = real(self.corpus)
        after = dict(before, corpus='changed')
        with patch.object(runner, 'fingerprints', side_effect=[before, after]):
            with self.assertRaisesRegex(ValueError, 'changed'):
                runner.run(self.corpus, None, self.root / 'changed')
        self.assertTrue((self.root / 'changed/source-lock.json').exists())
        self.assertFalse((self.root / 'changed/report.json').exists())

    def test_public_prompt_export_has_no_gold_fields_and_is_hash_bound(self):
        path = self.root / 'public.json'
        runner.prompts(self.corpus, path)
        result = json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn('expected', result['cases'][0])
        self.assertEqual(len(result['corpus_sha256']), 64)
        self.assertEqual(len(result['cases']), 3)

    def test_html_escapes_imported_text_and_csv_escapes_formula_cells(self):
        record = self.record(hypothesis='<script>alert(1)</script>')
        report = runner.evaluate(self.data, [record])
        rendered = runner.html_report(report)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', rendered)
        self.assertNotIn('<script>alert(1)</script>', rendered)
        report['recording_rows'][0]['recording']['hypothesis'] = '=SUM(1,2)'
        self.assertIn("'=SUM", runner._csv(report['recording_rows']))

    def test_empty_hypothesis_must_have_explicit_failure_status(self):
        with self.assertRaises(ValueError):
            runner.observations(self.package([self.record(status='error', hypothesis='not empty')]), self.data)
        self.assertEqual(len(runner.observations(self.package([self.record(status='error', hypothesis='')]), self.data)), 1)

    def test_jsonl_cannot_bypass_corpus_version_binding(self):
        path = self.root / 'records.jsonl'
        path.write_text(json.dumps(self.record()), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'matching dataset_id'):
            runner.observations(path, self.data)
        row = self.record() | {'dataset_id': self.data['dataset_id'],
            'corpus_sha256': hashlib.sha256(self.corpus.read_bytes()).hexdigest()}
        path.write_text(json.dumps(row), encoding='utf-8')
        self.assertEqual(len(runner.observations(path, self.data)), 1)

    def test_production_integrated_extract_is_executed_but_unlabeled_r1_fields_are_separate(self):
        case = self.data['cases'][0]
        integrated = {'fields': {'breathing': 'abnormal', 'chief_complaint': 'dyspnea'}, 'warnings': [], 'evidence': {}}
        with patch.object(runner.voice_input, '_extract', return_value=integrated) as called:
            result = runner.predict(case, '호흡곤란', self.data['observed_at'])
        called.assert_called_once()
        self.assertEqual(result['fields'], {'breathing': 'abnormal'})
        self.assertEqual(result['unscored_fields'], {'chief_complaint': 'dyspnea'})

    def test_malformed_case_and_record_entries_fail_with_validation_errors(self):
        for data in ([], self.data | {'cases': [None]}, self.data | {'cases': [self.data['cases'][0] | {'expected': []}]}):
            self.write(self.corpus, data)
            with self.assertRaises(ValueError):
                runner.corpus(self.corpus)
        self.write(self.corpus, self.data)
        with self.assertRaises(ValueError):
            runner.observations(self.package([None]), self.data)

    def test_qa_mock_downloads_cannot_be_counted_as_measured_human_speech(self):
        path = self.package([self.record(qa_fixture=True)])
        with self.assertRaisesRegex(ValueError, 'mocked QA'):
            runner.observations(path, self.data)
        path = self.package([self.record()])
        package = json.loads(path.read_text(encoding='utf-8'))
        package['qa_fixture'] = {'not_real_audio': True}
        self.write(path, package)
        with self.assertRaisesRegex(ValueError, 'QA-only'):
            runner.observations(path, self.data)
