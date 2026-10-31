"""Validation and missingness tests for observed paired task measurements."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from usability_eval.evaluate import evaluate, load_records, prepare, run, validate, FIELDS


def attempt(method='manual', **overrides):
    row = dict(record_id=method, participant_id='P001', participant_role='paramedic',
        task_id='normal', attempt=1, method=method, source='human', status='completed',
        order=1 if method == 'manual' else 2, elapsed_ms=10000 if method == 'manual' else 8000,
        error_count=0, correction_count=1, touch_count=10, hospital_review_ms=None,
        inquiry_count=3, interface_version='test-only-v1', engine_version='none', failure_reason=None)
    row.update(overrides)
    return row


class UsabilityEvaluationTests(unittest.TestCase):
    def test_empty_is_not_measured(self):
        result = evaluate([])
        self.assertEqual(result['measurement_status'], 'not_measured')
        self.assertEqual(result['actual_attempts'], 0)
        self.assertIsNone(result['groups'][0]['completion_rate'])
        self.assertIsNone(result['paired_summary'][0]['deltas']['elapsed_ms']['mean'])

    def test_pair_is_same_participant_task_and_repeat(self):
        result = evaluate([attempt(), attempt('voice'), attempt('voice', record_id='unpaired', task_id='change')])
        self.assertEqual(len(result['pairs']), 1)
        self.assertEqual(result['pairs'][0]['deltas']['elapsed_ms'], -2000)
        self.assertEqual(len(result['incomplete_pairs']), 1)
        self.assertEqual(result['actual_attempts'], 3)

    def test_failure_is_preserved_and_excluded_from_time_delta(self):
        result = evaluate([attempt(), attempt('voice', status='failed', failure_reason='STT unavailable', elapsed_ms=15000)])
        self.assertFalse(result['pairs'][0]['completed_pair'])
        self.assertIsNone(result['pairs'][0]['deltas']['elapsed_ms'])
        voice = next(g for g in result['groups'] if g['source']=='human' and g['participant_role']=='paramedic' and g['method']=='voice')
        self.assertEqual((voice['attempts'],voice['failed'],voice['completion_rate']), (1,1,0))
        self.assertEqual(voice['attempt_metrics']['elapsed_ms']['mean'],15000)
        self.assertIsNone(voice['completed_metrics']['elapsed_ms']['mean'])

    def test_missing_counts_stay_missing(self):
        result = evaluate([attempt(), attempt('voice', correction_count=None, touch_count=None)])
        self.assertIsNone(result['pairs'][0]['deltas']['touch_count'])
        metric = result['paired_summary'][0]['deltas']['correction_count']
        self.assertEqual((metric['n'],metric['missing'],metric['mean']), (0,1,None))

    def test_synthetic_and_human_cannot_pair_or_pool(self):
        result = evaluate([attempt(), attempt('voice',source='synthetic')])
        self.assertEqual(result['actual_attempts'],1)
        self.assertEqual(result['synthetic_attempts'],1)
        self.assertEqual(result['pairs'],[])
        self.assertEqual(len(result['incomplete_pairs']),2)
        self.assertEqual(evaluate([attempt(source='synthetic')])['measurement_status'],'not_measured')

    def test_hospital_review_is_separate_observed_metric(self):
        rows = [attempt(participant_role='hospital',hospital_review_ms=9000),
                attempt('voice',participant_role='hospital',hospital_review_ms=6000,inquiry_count=3)]
        report = evaluate(rows)
        self.assertEqual(report['pairs'][0]['deltas']['hospital_review_ms'],-3000)
        self.assertEqual(report['pairs'][0]['deltas']['inquiry_count'],0)
        with self.assertRaises(ValueError):
            evaluate([attempt(hospital_review_ms=100)])
        with self.assertRaises(ValueError):
            evaluate([attempt(participant_role='hospital',hospital_review_ms=10001)])

    def test_invalid_numbers_and_missing_required_fields(self):
        for patch in ({'elapsed_ms':float('nan')}, {'elapsed_ms':float('inf')},
                      {'elapsed_ms':True}, {'elapsed_ms':-1}, {'elapsed_ms':10**1000},
                      {'error_count':-1}, {'touch_count':1.5}, {'attempt':0},
                      {'correction_count':True}, {'participant_id':''}, {'method':'PTT'},
                      {'status':'failed'}, {'status':'completed','failure_reason':'error'}, {'order':3}):
            with self.subTest(patch=str(patch)[:80]), self.assertRaises(ValueError):
                validate([attempt(**patch)])

    def test_duplicate_id_or_measurement_cell_is_rejected(self):
        for row in (attempt('voice',record_id='manual'), attempt(record_id='different')):
            with self.assertRaises(ValueError):
                evaluate([attempt(),row])

    def test_pair_same_order_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([attempt(),attempt('voice',order=1)])

    def test_repeats_and_counterbalance_preserved(self):
        result = evaluate([attempt(),attempt('voice'),
            attempt(record_id='m2',attempt=2,order=2,elapsed_ms=5000),
            attempt('voice',record_id='v2',attempt=2,order=1,elapsed_ms=7000)])
        summary = result['paired_summary'][0]
        self.assertEqual((summary['pairs'],summary['participants'],summary['voice_first'],summary['manual_first']), (2,1,1,1))
        self.assertEqual(summary['deltas']['elapsed_ms']['mean'],0)
        self.assertEqual(summary['deltas']['elapsed_ms']['maximum'],2000)

    def test_template_can_be_evaluated_without_invented_results(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            base = Path(directory)
            folder = prepare(base/'prepare')
            self.assertEqual(load_records(folder/'measurement_template.csv'),[])
            result = run(folder/'measurement_template.csv',base/'report')
            self.assertEqual(result['measurement_status'],'not_measured')
            self.assertTrue((base/'report'/'sources'/'evaluate.py').exists())
            self.assertIn('미측정',(base/'report'/'report.md').read_text(encoding='utf-8'))
            with self.assertRaises(FileExistsError):
                prepare(folder)
            with self.assertRaises(FileExistsError):
                run(folder/'measurement_template.csv',base/'report')

    def test_csv_roundtrip_json_schema_and_unknown_field(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory)/'input.csv'
            with path.open('w',encoding='utf-8-sig',newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerow(attempt())
            self.assertEqual(load_records(path)[0]['elapsed_ms'],10000)
            js = path.with_suffix('.json')
            js.write_text(json.dumps({'schema_version':True,'records':[]}),encoding='utf-8')
            with self.assertRaises(ValueError):
                load_records(js)
            with self.assertRaises(ValueError):
                validate([dict(attempt(),unrecognized='value')])


if __name__ == '__main__':
    unittest.main()
