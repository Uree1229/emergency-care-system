"""Synthetic allocation/strata tests; no person, microphone or API measurements."""
import contextlib
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from usability_eval import order_plan
from usability_eval.evaluate import FIELDS, METRICS, evaluate, load_records, main, run
from test_usability_eval import attempt


def parameters(**overrides):
    values = dict(participants=['P001', 'P002', 'P003'], tasks=['normal', 'change', 'repair'],
        role='paramedic', source='synthetic', seed=7301, attempt=1,
        interface_version='ui-test-v1', manual_engine_version='none', voice_engine_version='browser-test-v1')
    values.update(overrides)
    return values


def observed(plan):
    rows = []
    for index, slot in enumerate(order_plan.expected_slots(plan)):
        row = {field: None for field in FIELDS}
        row.update(slot, record_id='observed-' + str(index), status='completed',
            elapsed_ms=10000 if slot['method'] == 'manual' else 8000)
        rows.append(row)
    return rows


def synthetic(method='manual', **values):
    return attempt(method, source='synthetic', **values)


class OrderPlanTests(unittest.TestCase):
    def test_seed_is_reproducible_and_input_order_does_not_change_allocation(self):
        first = order_plan.make_plan(**parameters())
        second = order_plan.make_plan(**parameters(participants=['P003','P001','P002'],
                                                    tasks=['repair','normal','change']))
        self.assertEqual(first, second)
        self.assertEqual(order_plan.validate_plan(first), first)

    def test_different_seed_has_distinct_provenance_and_known_assignment_changes(self):
        one = order_plan.make_plan(**parameters(seed=1))
        two = order_plan.make_plan(**parameters(seed=2))
        self.assertNotEqual(one['plan_sha256'], two['plan_sha256'])
        self.assertNotEqual(one['schedule'], two['schedule'])

    def test_balance_holds_overall_per_participant_and_per_task_for_odd_even_shapes(self):
        for people, tasks in ((1,1),(1,6),(6,1),(3,5),(4,5),(5,4),(4,4)):
            with self.subTest(people=people, tasks=tasks):
                plan = order_plan.make_plan(**parameters(participants=['P'+str(x) for x in range(people)],
                    tasks=['T'+str(x) for x in range(tasks)]))
                balances = [plan['balance']['overall'], *plan['balance']['per_participant'].values(),
                            *plan['balance']['per_task'].values()]
                self.assertTrue(all(value['absolute_difference'] <= 1 for value in balances))
                self.assertTrue(all({row['manual_order'],row['voice_order']} == {1,2} for row in plan['schedule']))
                self.assertEqual(plan['planned_method_attempts'], people*tasks*2)
                for person in plan['participants']:
                    self.assertEqual(sorted(row['task_position'] for row in plan['schedule'] if row['participant_id']==person),
                                     list(range(1,tasks+1)))

    def test_plan_contains_no_human_performance_even_when_human_is_intended(self):
        plan = order_plan.make_plan(**parameters(source='human'))
        self.assertEqual((plan['actual_participants'],plan['actual_attempts'],plan['measurement_status']),
                         (0,0,'not_measured'))
        self.assertTrue(all(not set(METRICS) & set(row) for row in plan['schedule']))

    def test_invalid_identifiers_parameters_and_resource_bounds_are_rejected(self):
        patches = ({'participants':[]}, {'participants':['P1','P1']}, {'participants':['홍길동']},
            {'participants':['P1\nP2']}, {'participants':'P1'}, {'tasks':[]}, {'tasks':['T1','T1']},
            {'tasks':['T|1']}, {'role':'doctor'}, {'source':'recorded'}, {'seed':True}, {'seed':-1},
            {'seed':1.5}, {'seed':1<<63}, {'attempt':False}, {'attempt':0}, {'attempt':10001},
            {'interface_version':''}, {'voice_engine_version':'v1\nprivate'},
            {'manual_engine_version':None},
            {'participants':['P'+str(x) for x in range(1001)]},
            {'participants':['P'+str(x) for x in range(251)], 'tasks':['T'+str(x) for x in range(200)]})
        for patch in patches:
            with self.subTest(patch=str(patch)[:80]), self.assertRaises(ValueError):
                order_plan.make_plan(**parameters(**patch))

    def test_tampered_schedule_checksum_and_extra_plan_metadata_are_rejected(self):
        original = order_plan.make_plan(**parameters())
        for kind in ('order','sha','extra','boolean_order','boolean_schema'):
            plan = copy.deepcopy(original)
            if kind == 'order':
                plan['schedule'][0]['manual_order'] = 3
            elif kind == 'sha':
                plan['schedule_sha256'] = '0'*64
            elif kind == 'boolean_order':
                plan['schedule'][0]['manual_order'] = bool(plan['schedule'][0]['manual_order'])
            elif kind == 'boolean_schema':
                plan['schema_version'] = True
            else:
                plan['unknown'] = 'not part of the plan schema'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                order_plan.validate_plan(plan)

    def test_plan_files_are_reproducible_empty_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            first, second = Path(directory)/'one', Path(directory)/'two'
            plan = order_plan.create(first, **parameters(source='human'))
            order_plan.create(second, **parameters(source='human'))
            for name in ('plan.json','schedule.csv','planned_measurements.csv','measurement_template.csv',
                         'measurement_template.json','plan.md','report.json','report.md'):
                self.assertEqual((first/name).read_bytes(), (second/name).read_bytes())
            self.assertEqual(load_records(first/'measurement_template.csv'), [])
            self.assertEqual(load_records(first/'measurement_template.json'), [])
            with (first/'planned_measurements.csv').open(encoding='utf-8-sig',newline='') as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, list(FIELDS))
                pending = list(reader)
            self.assertEqual(len(pending), plan['planned_method_attempts'])
            self.assertTrue(all(row['status'] == '' and all(row[key] == '' for key in METRICS) for row in pending))
            self.assertEqual(json.loads((first/'report.json').read_text(encoding='utf-8'))['actual_attempts'], 0)
            with self.assertRaises(ValueError):
                load_records(first/'planned_measurements.csv')
            with self.assertRaises(FileExistsError):
                order_plan.create(first, **parameters())


class OrderEvaluationTests(unittest.TestCase):
    def test_interface_mismatch_retains_raw_pair_but_excludes_differences(self):
        rows = [synthetic(), synthetic('voice',interface_version='another-ui')]
        report = evaluate(rows)
        pair = report['pairs'][0]
        self.assertTrue(pair['outcome_completed_pair'])
        self.assertFalse(pair['completed_pair'])
        self.assertIn('interface_version_mismatch', pair['exclusion_reasons'])
        self.assertTrue(all(value is None for value in pair['deltas'].values()))
        self.assertEqual(report['records'][1]['interface_version'], 'another-ui')
        self.assertEqual(sum(group['excluded_completed_pairs'] for group in report['paired_summary']), 1)

    def test_engine_profiles_do_not_pool_manual_or_voice_results(self):
        rows = [synthetic(engine_version='none'), synthetic('voice',engine_version='voice-v1'),
            synthetic(record_id='m2',participant_id='P002',engine_version='none'),
            synthetic('voice',record_id='v2',participant_id='P002',engine_version='voice-v2',elapsed_ms=20000)]
        report = evaluate(rows)
        summaries = [group for group in report['paired_summary'] if group['pairs']]
        self.assertEqual(len(summaries), 2)
        self.assertEqual({group['deltas']['elapsed_ms']['mean'] for group in summaries}, {-2000,10000})
        self.assertTrue(all(group['pairs'] == 1 for group in summaries))
        self.assertTrue(all(group['attempts'] == 1 for group in report['groups'] if group['attempts']))

    def test_source_role_and_interface_profiles_remain_separate(self):
        rows = []
        for index,(source,role,version) in enumerate((('human','paramedic','v1'),
                ('synthetic','paramedic','v1'),('synthetic','hospital','v1'),('synthetic','paramedic','v2'))):
            for method in ('manual','voice'):
                rows.append(attempt(method,record_id=f'{index}-{method}',source=source,
                    participant_id='P'+str(index), participant_role=role,interface_version=version))
        report = evaluate(rows)
        self.assertEqual(len([group for group in report['paired_summary'] if group['pairs']]),4)
        self.assertTrue(all(group['pairs']==1 for group in report['paired_summary'] if group['pairs']))

    def test_order_strata_report_opposite_differences_separately(self):
        report = evaluate([synthetic(),synthetic('voice'),
            synthetic(record_id='m2',participant_id='P002',order=2),
            synthetic('voice',record_id='v2',participant_id='P002',order=1,elapsed_ms=13000)])
        groups = [group for group in report['order_summary'] if group['pairs']]
        self.assertEqual({group['first_method']:group['deltas']['elapsed_ms']['mean'] for group in groups},
                         {'manual':-2000,'voice':3000})
        self.assertEqual(report['order_warnings'], [])

    def test_order_imbalance_and_single_order_are_explicit_warnings(self):
        rows = []
        for person in range(3):
            rows += [synthetic(record_id=f'{person}-m',participant_id='P'+str(person)),
                     synthetic('voice',record_id=f'{person}-v',participant_id='P'+str(person))]
        report = evaluate(rows)
        self.assertEqual({item['code'] for item in report['order_warnings']}, {'ORDER_IMBALANCE','SINGLE_ORDER_ONLY'})
        self.assertEqual(report['order_warnings'][0]['absolute_difference'],3)

    def test_failed_abandoned_missing_metrics_and_unpaired_attempts_stay_visible(self):
        rows = [synthetic(correction_count=None), synthetic('voice',status='failed',failure_reason='stopped',elapsed_ms=12000),
            synthetic(record_id='m2',participant_id='P002',status='abandoned',failure_reason='interrupted',touch_count=None),
            synthetic('voice',record_id='v2',participant_id='P002'),
            synthetic(record_id='unpaired',participant_id='P003',error_count=None)]
        report = evaluate(rows)
        self.assertEqual(len(report['records']),5)
        self.assertEqual(len(report['incomplete_pairs']),1)
        self.assertEqual(sum(group['completed_pairs'] for group in report['paired_summary']),0)
        self.assertTrue(all(all(value is None for value in pair['deltas'].values()) for pair in report['pairs']))
        self.assertIsNone(report['records'][0]['correction_count'])
        self.assertEqual(sum(group['failed'] for group in report['groups']),1)
        self.assertEqual(sum(group['abandoned'] for group in report['groups']),1)

    def test_planned_complete_synthetic_records_have_no_fabricated_human_measurements(self):
        plan = order_plan.make_plan(**parameters())
        report = evaluate(observed(plan), plan=plan)
        self.assertTrue(report['plan_assessment']['fully_compliant'])
        self.assertEqual((report['actual_participants'],report['actual_attempts'],report['measurement_status']),
                         (0,0,'not_measured'))
        self.assertEqual(report['synthetic_attempts'],18)
        self.assertEqual(sum(group['completed_pairs'] for group in report['paired_summary']),9)
        self.assertFalse(any(item['code']=='ORDER_IMBALANCE' for item in report['order_warnings']))

    def test_empty_plan_evaluation_reports_missing_slots_without_imputation(self):
        plan = order_plan.make_plan(**parameters(source='human'))
        report = evaluate([], plan=plan)
        self.assertEqual(len(report['plan_assessment']['missing_slots']),18)
        self.assertEqual(report['records'],[])
        self.assertEqual(report['actual_attempts'],0)
        self.assertTrue(all(group['deltas']['elapsed_ms']['mean'] is None for group in report['paired_summary']))

    def test_missing_one_planned_method_preserves_incomplete_pair(self):
        plan = order_plan.make_plan(**parameters(participants=['P1'],tasks=['T1']))
        rows = observed(plan)[:1]
        report = evaluate(rows, plan=plan)
        self.assertEqual(len(report['plan_assessment']['missing_slots']),1)
        self.assertEqual(len(report['incomplete_pairs']),1)
        self.assertEqual(report['pairs'],[])

    def test_swapped_order_is_reported_and_excluded_from_planned_differences(self):
        plan = order_plan.make_plan(**parameters(participants=['P1'],tasks=['T1']))
        rows = observed(plan)
        for row in rows:
            row['order'] = 3-row['order']
        report = evaluate(rows, plan=plan)
        self.assertEqual(len(report['plan_assessment']['deviations']),2)
        self.assertIn('plan_deviation',report['pairs'][0]['exclusion_reasons'])
        self.assertIsNone(report['pairs'][0]['deltas']['elapsed_ms'])

    def test_plan_version_and_engine_deviations_do_not_merge_into_planned_profile(self):
        plan = order_plan.make_plan(**parameters(participants=['P1'],tasks=['T1']))
        for field in ('interface_version','engine_version'):
            rows = observed(plan)
            if field == 'interface_version':
                for row in rows:
                    row[field] = 'another-ui'
            else:
                rows[1][field] = 'another-voice-engine'
            report = evaluate(rows, plan=plan)
            self.assertTrue(report['plan_assessment']['deviations'])
            self.assertIn('plan_deviation', report['pairs'][0]['exclusion_reasons'])
            self.assertIsNone(report['pairs'][0]['deltas']['elapsed_ms'])
            self.assertEqual(len(report['records']),2)

    def test_unexpected_source_and_task_are_not_silently_adopted(self):
        plan = order_plan.make_plan(**parameters(participants=['P1'],tasks=['T1']))
        rows = observed(plan)
        for row in rows:
            row['task_id'] = 'unplanned'
        report = evaluate(rows, plan=plan)
        self.assertEqual(len(report['plan_assessment']['unexpected_records']),2)
        self.assertEqual(len(report['plan_assessment']['missing_slots']),2)
        self.assertIsNone(report['pairs'][0]['deltas']['elapsed_ms'])

    def test_planned_failure_is_an_observed_outcome_not_a_plan_deviation(self):
        plan = order_plan.make_plan(**parameters(participants=['P1'],tasks=['T1']))
        rows = observed(plan)
        rows[1].update(status='failed',failure_reason='recognizer unavailable')
        report = evaluate(rows, plan=plan)
        self.assertTrue(report['plan_assessment']['fully_compliant'])
        self.assertEqual(report['pairs'][0]['exclusion_reasons'],['outcome_not_completed'])

    def test_run_preserves_input_plan_and_snapshots_all_evaluator_sources(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            base = Path(directory)
            plan = order_plan.create(base/'plan', **parameters())
            input_path = base/'input.json'
            input_path.write_text(json.dumps(dict(schema_version=1,records=observed(plan))),encoding='utf-8')
            before_input = input_path.read_bytes()
            before_plan = (base/'plan/plan.json').read_bytes()
            report = run(input_path,base/'result',plan_path=base/'plan/plan.json')
            self.assertEqual(input_path.read_bytes(),before_input)
            self.assertEqual((base/'plan/plan.json').read_bytes(),before_plan)
            self.assertEqual(report['plan_file_sha256'],hashlib.sha256(before_plan).hexdigest())
            self.assertIn('order_plan.py',report['source_hashes'])
            self.assertIn('계획 대비 점검',(base/'result/report.md').read_text(encoding='utf-8'))

    def test_prepare_plan_and_evaluate_cli_remain_compatible(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            base = Path(directory)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['prepare','--output',str(base/'prepare')]),0)
                self.assertEqual(main(['plan','--output',str(base/'plan'), '--participants','P1','P2',
                    '--tasks','T1','T2','--role','paramedic','--seed','9','--interface-version','ui-test',
                    '--voice-engine-version','voice-test']),0)
                self.assertEqual(main(['evaluate','--input',str(base/'prepare/measurement_template.json'),
                    '--output',str(base/'result'),'--plan',str(base/'plan/plan.json')]),0)
            report = json.loads((base/'result/report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['actual_attempts'],0)
            self.assertEqual(len(report['plan_assessment']['missing_slots']),8)


if __name__ == '__main__':
    unittest.main()
