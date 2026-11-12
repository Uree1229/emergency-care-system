"""Multi-patient synthetic driver contracts; actual candidate/benchmark executions zero."""
from copy import deepcopy
import gzip
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from inquiry_lab import travelrecall_screen_diagnostic as d


def patient(pid='P', selection=100, travel=60):
    return dict(id=pid, created_at=250, failed=selection is None, selection_seconds=selection,
        estimated_arrival_seconds=None if selection is None else selection + travel,
        travel_seconds=None if selection is None else travel,
        destination=None if selection is None else 'H', inquiry_count=1, contacted=['H'])


def entry(index, count=3):
    return dict(source_index=index, kind='unit', seed=index, family='synthetic', scenario_id='unit',
        parent_stage='repair' if index < 240 else 'development', original_origin=None,
        snapshot_sha256='snapshot', environment_sha256='environment',
        baseline_branches={key: dict(patients=[patient('P' + str(i)) for i in range(count)]) for key in d.BASELINES})


def replay(value):
    patients = deepcopy(value['baseline_branches'][d.TAILWAIT]['patients'])
    for p in patients:
        p['selection_seconds'] = 95
        p['estimated_arrival_seconds'] = 155
    scenario = dict(id='unit', seed=value['seed'], patients=[dict(id=p['id']) for p in patients])
    value['input_sha256'] = d.sha(d.raw(scenario))
    state = dict(bindings={name: {'unit': 1} for name in d.ORIGINAL_MODULES}, registry_sha256='registry', installed=[])
    return dict(schema_version=1, scenario=scenario, initial_snapshot_sha256='snapshot', environment_sha256='environment',
        arms=[[d.NAME, 'immediate']], branches=[dict(policy=d.NAME, rule='immediate', snapshot_sha256='snapshot',
            environment_sha256='environment', patients=patients, policy_calls=[], db_decisions=[], db_events=[])],
        screen_namespace_proof=dict(passed=True, before=deepcopy(state), after=deepcopy(state), scope='synthetic'))


def item(value, result):
    body = d.raw(result)
    return dict(file=f"{value['source_index']:03d}_{value['seed']}.json.gz", raw_folder='screen-cases',
        source_index=value['source_index'], input_sha256=value['input_sha256'], raw_sha256=d.sha(body),
        compressed_sha256=d.sha(gzip.compress(body, mtime=0)))


def public_call(result, own=0, sends=1, wake=False):
    inp = dict(now=1000, candidates=[dict(hospital_id='H' + str(i)) for i in range(sends)], observations=[], pending=[],
        patient_pending=[dict(hospital='O' + str(i), submitted_at=1000) for i in range(own)],
        max_new=10, last_contact_at=None, time_context={})
    if wake:
        inp['wake_context'] = None
    out = dict(send=['H' + str(i) for i in range(sends)], reason='synthetic', evidence={})
    call = dict(name=d.NAME, patient_id='P0', db_decision_id=1, inputs=inp, output=out)
    decision = dict(id=1, patient_id='P0', policy_name=d.NAME, at=1000, hospital_ids=deepcopy(out['send']),
                    reason='synthetic', evidence={})
    result['branches'][0]['policy_calls'] = [call]
    result['branches'][0]['db_decisions'] = [decision]
    return call, decision


class NoOutcome(dict):
    def __getitem__(self, key):
        if key != 'id':
            raise AssertionError('selection must not read any outcome')
        return super().__getitem__(key)


class ScreenTests(unittest.TestCase):
    def temporary(self):
        return tempfile.TemporaryDirectory(dir=d.BUILD / 'tmp')

    def synthetic_entries(self):
        entries = [entry(i, 4 if i % 4 == 0 else (3 if i % 3 == 0 else 1)) for i in range(360)]
        for value in entries:
            replay(value)
        return entries

    def test_size_only_first_six_each_origin_uses_no_outcomes(self):
        values = self.synthetic_entries()
        for value in values:
            for branch in value['baseline_branches'].values():
                branch['patients'] = [NoOutcome(id=p['id']) for p in branch['patients']]
        result = d.select_entries(list(reversed(values)))
        self.assertEqual([e['source_index'] for e in result], [0, 3, 4, 6, 8, 9, 240, 243, 244, 246, 248, 249])
        self.assertEqual(d.SELECTION_RULE['used_outcome_fields'], False)

    def test_selection_rejects_missing_origin_duplicate_index_and_partition_drift(self):
        for mode in ('origin', 'duplicate', 'partition'):
            values = self.synthetic_entries()
            if mode == 'origin':
                for value in values:
                    value['parent_stage'] = 'repair'
            elif mode == 'duplicate':
                values[1]['source_index'] = 0
            else:
                values[1]['baseline_branches'][d.STOPLOSS]['patients'][0]['id'] = 'OTHER'
            with self.subTest(mode=mode), self.assertRaises(AssertionError):
                d.select_entries(values)

    def test_exact_size_strata_are_required_without_outcome_fallback(self):
        values = self.synthetic_entries()
        selected = d.select_entries(values)
        for origin in d.SELECTION_RULE['origins']:
            counts = [len(e['baseline_branches'][d.TAILWAIT]['patients'])
                      for e in selected if e['parent_stage'] == origin]
            self.assertEqual(sorted(counts), [3, 3, 3, 4, 4, 4])
        for value in values:
            if value['parent_stage'] == 'development':
                value['baseline_branches'] = entry(240, 3)['baseline_branches']
        with self.assertRaises(AssertionError):
            d.select_entries(values)

    def test_single_and_five_patient_inputs_are_ineligible(self):
        self.assertFalse(d.small_entry(entry(0, 1)))
        self.assertFalse(d.small_entry(entry(0, 5)))
        self.assertTrue(d.small_entry(entry(0, 3)))
        self.assertTrue(d.small_entry(entry(0, 4)))

    def test_typed_identifier_partition_distinguishes_integer_and_string(self):
        self.assertNotEqual(d.typed_id(1), d.typed_id('1'))
        with self.assertRaises(AssertionError):
            d.typed_id(True)
        with self.assertRaises(AssertionError):
            d.patient_ids([dict(id='P'), dict(id='P')])

    def test_numeric_code_uses_separate_globals_and_keeps_legacy_27_incremental_14(self):
        from inquiry_lab import cohort_wavecap_diagnostic as original
        before = original.PRIMARY
        cases = []
        for index in [0, 1, 2, 3, 4, 5, 240, 241, 242, 243, 244, 245]:
            value = entry(index)
            result = replay(value)
            cases.append(d.output_case(item(value, result), value, result))
        numeric = d.numeric()
        legacy = numeric.early_gate(cases)
        incremental = numeric.incremental_research_gate(legacy)
        self.assertTrue(legacy['passed'])
        self.assertEqual(len(legacy['checks']), 27)
        self.assertTrue(incremental['passed'])
        self.assertEqual(len(incremental['checks']), 14)
        self.assertEqual(original.PRIMARY, before)
        self.assertFalse(incremental['operational_adoption'])

    def test_output_screen_is_explicitly_not_semantic(self):
        value = entry(0)
        result = replay(value)
        case = d.output_case(item(value, result), value, result)
        self.assertIsNone(case['audit'])
        self.assertFalse(case['semantic_verified'])
        self.assertTrue(case['output_invariants']['namespace_preserved'])
        self.assertIn('not FIFO', case['output_invariants']['scope'])

    def test_output_rejects_heavy_input_before_accepting_cheap_header(self):
        value = entry(0, 5)
        result = replay(value)
        with self.assertRaises(AssertionError):
            d.output_case(item(value, result), value, result)

    def test_header_snapshot_environment_input_and_patient_tampers_rejected(self):
        for mode in ('snapshot', 'environment', 'input', 'patient', 'name', 'created_at'):
            value = entry(0)
            result = replay(value)
            if mode == 'snapshot':
                result['branches'][0]['snapshot_sha256'] = 'changed'
            elif mode == 'environment':
                result['environment_sha256'] = 'changed'
            elif mode == 'input':
                result['scenario']['seed'] = 123
            elif mode == 'patient':
                result['branches'][0]['patients'][0]['id'] = 'foreign'
            elif mode == 'name':
                result['branches'][0]['policy'] = 'fixed_3'
            else:
                result['branches'][0]['patients'][0]['created_at'] += 1
            with self.subTest(mode=mode), self.assertRaises(AssertionError):
                d.output_case(item(value, result), value, result)

    def test_current_cap_inclusive_180_budget10_and_candidate_membership(self):
        for mode in ('fresh_cap', 'total_cap', 'duplicate', 'available', 'budget'):
            value = entry(0)
            result = replay(value)
            call, decision = public_call(result, own=3, sends=2)
            if mode == 'total_cap':
                result['branches'][0]['patients'][0].update(inquiry_count=11, contacted=['H' + str(i) for i in range(11)])
                call['inputs']['patient_pending'] = []
            elif mode == 'duplicate':
                call['output']['send'] = ['H0', 'H0']
                decision['hospital_ids'] = ['H0', 'H0']
            elif mode == 'available':
                call['inputs']['candidates'] = []
            elif mode == 'budget':
                call['inputs']['max_new'] = 1
            with self.subTest(mode=mode), self.assertRaises(AssertionError):
                d.output_case(item(value, result), value, result)
        value = entry(0)
        result = replay(value)
        call, _ = public_call(result, own=4, sends=1)
        for q in call['inputs']['patient_pending']:
            q['submitted_at'] = 819
        self.assertTrue(d.output_case(item(value, result), value, result)['output_invariants']['passed'])
        for q in call['inputs']['patient_pending']:
            q['submitted_at'] = 820
        with self.assertRaises(AssertionError):
            d.output_case(item(value, result), value, result)

    def test_captured_call_count_and_committed_decision_fields_must_match(self):
        for mode in ('count', 'id', 'patient', 'time', 'send', 'reason', 'evidence', 'schema', 'name'):
            value = entry(0)
            result = replay(value)
            call, decision = public_call(result)
            if mode == 'count':
                result['branches'][0]['db_decisions'] = []
            elif mode == 'schema':
                call['inputs']['unknown'] = 1
            elif mode == 'name':
                call['name'] = 'fixed_3'
            else:
                field = dict(id='id', patient='patient_id', time='at', send='hospital_ids', reason='reason', evidence='evidence')[mode]
                decision[field] = {'id': 2, 'patient': 'foreign', 'time': 999, 'send': [], 'reason': 'other', 'evidence': {'changed': 1}}[mode]
            with self.subTest(mode=mode), self.assertRaises(AssertionError):
                d.output_case(item(value, result), value, result)

    def test_joint_eight_and_fallback_nine_input_shapes_are_accepted(self):
        for wake in (False, True):
            value = entry(0)
            result = replay(value)
            public_call(result, wake=wake)
            self.assertTrue(d.output_case(item(value, result), value, result)['output_invariants']['passed'])

    def test_workflow_technical_errors_preserved_separately_from_normal_exhaustion(self):
        value = entry(0)
        result = replay(value)
        result['branches'][0]['db_events'] = [dict(id=i, type='inquiry_workflow_exhausted', data=dict(reason=reason))
            for i, reason in enumerate(('DEADLINE', 'CONTACT_LIMIT', 'NO_FRESH_CANDIDATES', 'CALCULATION_ERROR', 'STATE_ERROR'), 1)]
        case = d.output_case(item(value, result), value, result)
        self.assertFalse(case['output_invariants']['passed'])
        self.assertEqual(case['execution_diagnostics']['workflow_error_count'], 2)
        self.assertEqual([e['id'] for e in case['execution_diagnostics']['workflow_error_events']], [4, 5])
        self.assertIsNone(case['audit'])

    def test_namespace_change_rejects_instead_of_becoming_an_algorithm_failure(self):
        value = entry(0)
        result = replay(value)
        result['screen_namespace_proof']['after']['bindings']['server.inquiry_workflow']['unit'] = 2
        with self.assertRaises(AssertionError):
            d.output_case(item(value, result), value, result)

    def test_canonical_raw_hash_and_compressed_hash_are_both_checked(self):
        value = entry(0)
        result = replay(value)
        with self.temporary() as directory, patch.object(d, 'BUILD', Path(directory)):
            folder = d.BUILD / 'screen-cases'
            folder.mkdir()
            source = item(value, result)
            packed = gzip.compress(d.raw(result), mtime=0)
            (folder / source['file']).write_bytes(packed)
            self.assertTrue(d.read_candidate(source, value)['output_invariants']['passed'])
            for key in ('raw_sha256', 'compressed_sha256'):
                wrong = dict(source, **{key: 'changed'})
                with self.assertRaises(AssertionError):
                    d.read_candidate(wrong, value)

    def test_nonfinite_numbers_fail_canonical_output(self):
        for bad in (float('nan'), float('inf'), -float('inf')):
            with self.assertRaises(ValueError):
                d.raw(dict(value=bad))

    def test_only_one_worker_and_workspace_temp_are_allowed_before_execution(self):
        with patch.object(d, 'frozen', side_effect=AssertionError('must reject before reading protocol')) as frozen:
            for worker in (0, 2, True):
                with self.assertRaises(AssertionError):
                    d.execute(worker)
            with patch.dict(os.environ, {'TEMP': str(d.BUILD), 'TMP': str(d.BUILD)}):
                with self.assertRaises(AssertionError):
                    d.execute(1)
            frozen.assert_not_called()

    def test_candidate_exception_is_returned_as_preservable_error_raw(self):
        with patch.object(d.importlib, 'import_module', side_effect=RuntimeError('synthetic import failure')):
            value = d.run_candidate(dict(source_index=4, input_sha256='input'))
        self.assertEqual(value['source_index'], 4)
        self.assertIn('synthetic import failure', value['screen_execution_error'])

    def test_runtime_actual_input_size_is_rechecked_before_candidate_database_creation(self):
        value = entry(0, 5)
        scenario = dict(patients=[dict(id='P' + str(i)) for i in range(5)])
        value['input_sha256'] = d.sha(d.raw(scenario))
        execute = Mock(side_effect=AssertionError('large input must not reach candidate runner'))
        modules = {'inquiry_lab.cohort_travelrecall_runner': types.SimpleNamespace(run_scenario=execute),
                   'inquiry_lab.harness': types.SimpleNamespace(build=lambda *args: scenario)}
        with patch.object(d.importlib, 'import_module', side_effect=lambda name: modules[name]):
            result = d.run_candidate(value)
        self.assertIn('screen_execution_error', result)
        execute.assert_not_called()

    def test_real_manifest_selection_and_744_source_binding_are_read_only(self):
        result = d.bindings()
        old = d.read(d.PRIOR / 'screen-source-freeze.json')['source_sha256']
        self.assertEqual(len(result['source_sha256']), 744)
        self.assertEqual({key: result['source_sha256'][key] for key in old}, old)
        self.assertEqual(len(result['entries']), 12)
        self.assertEqual(result['cached_baseline_branches_per_environment'], 25)
        self.assertEqual([e['source_index'] for e in result['entries']], result['selected_indices'])
        self.assertTrue(all(d.small_entry(e) for e in result['entries']))
        self.assertFalse(result['automatic_expansion'])
        self.assertFalse(result['semantic_verification'])

    def test_all_twelve_raws_are_written_before_any_output_validation_and_failed_raw_retained(self):
        values = [entry(i) for i in (0, 1, 2, 3, 4, 5, 240, 241, 242, 243, 244, 245)]
        results = [replay(e) for e in values]
        results[0] = dict(screen_execution_error='synthetic candidate exception')
        protocol = dict(entries=values, selected_indices=[e['source_index'] for e in values], source_sha256={})
        class Pool:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def map(self, function, entries, **kwargs):
                return iter(results)
        with self.temporary() as directory, patch.object(d, 'BUILD', Path(directory)):
            (d.BUILD / 'tmp').mkdir()
            (d.BUILD / 'screen-manifest.json').write_text('{}')
            (d.BUILD / 'screen-source-freeze.json').write_text('{}')
            original = d.read_candidate
            def checked(source, value):
                self.assertEqual(len(list((d.BUILD / 'screen-cases').glob('*.gz'))), 12)
                return original(source, value)
            env = {'TEMP': str(d.BUILD / 'tmp'), 'TMP': str(d.BUILD / 'tmp')}
            with patch.dict(os.environ, env), patch.object(d, 'frozen', return_value=protocol), \
                 patch.object(d, 'ProcessPoolExecutor', Pool), patch.object(d, 'read_candidate', side_effect=checked):
                report = d.execute(1)
            self.assertEqual(report['raw_outcomes_saved'], 12)
            self.assertEqual(len(report['output_errors']), 1)
            self.assertEqual(len(report['cases']), 11)
            self.assertIsNone(report['early_gate'])
            self.assertFalse(report['automatic_expansion'])
            self.assertFalse(report['semantic_verification'])
            self.assertEqual(d.read(d.BUILD / 'screen-raw-index.json')['cases'][0]['source_index'], 0)
            self.assertTrue((d.BUILD / 'screen-cases' / '000_0.json.gz').exists())

    def test_freeze_rejects_missing_checker_or_changed_source_check_before_writing(self):
        protocol = dict(source_sha256={'source': 'hash'}, binding_sha256={})
        checks = dict(passed=True, failures=0, errors=0, skips=0, scope='targeted_travelrecall_screen_tests',
            source_sha256_before={}, source_sha256_after={}, private_files_sha256={})
        with self.temporary() as directory, patch.object(d, 'BUILD', Path(directory)), \
             patch.object(d, 'bindings', return_value=protocol), patch.object(d, 'read', return_value=checks):
            with self.assertRaises(AssertionError):
                d.freeze('checks')
            self.assertEqual(list(d.BUILD.iterdir()), [])

    def test_technical_workflow_error_keeps_all_twelve_vectors_but_output_gate_fails(self):
        values = [entry(i) for i in (0, 1, 2, 3, 4, 5, 240, 241, 242, 243, 244, 245)]
        results = [replay(e) for e in values]
        results[0]['branches'][0]['db_events'] = [dict(id=1, type='inquiry_workflow_exhausted', data=dict(reason='STATE_ERROR'))]
        protocol = dict(entries=values, selected_indices=[e['source_index'] for e in values], source_sha256={})
        class Pool:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def map(self, function, entries, **kwargs):
                return iter(results)
        with self.temporary() as directory, patch.object(d, 'BUILD', Path(directory)):
            (d.BUILD / 'tmp').mkdir()
            (d.BUILD / 'screen-manifest.json').write_text('{}')
            (d.BUILD / 'screen-source-freeze.json').write_text('{}')
            env = {'TEMP': str(d.BUILD / 'tmp'), 'TMP': str(d.BUILD / 'tmp')}
            with patch.dict(os.environ, env), patch.object(d, 'frozen', return_value=protocol), patch.object(d, 'ProcessPoolExecutor', Pool):
                report = d.execute(1)
            self.assertEqual(len(report['cases']), 12)
            self.assertEqual(report['workflow_error_count'], 1)
            self.assertTrue(report['numeric_accounting_complete'])
            self.assertFalse(report['output_checks_passed'])
            self.assertEqual(len(report['early_gate']['checks']), 27)
            self.assertEqual(len(report['incremental_research_gate']['checks']), 14)
            self.assertFalse(report['semantic_verification'])


if __name__ == '__main__':
    unittest.main()
