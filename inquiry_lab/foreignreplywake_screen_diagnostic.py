"""Twelve multi-patient, size-stratified training screens; numeric/output checks only.

This driver never replaces an original module global and never runs controls.
It does not audit policy mathematics, FIFO, or complete execution semantics.
Only the root may invoke the actual screen after source and checker binding.
"""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import argparse
import gzip
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import time
import traceback
import types

ROOT = Path(__file__).resolve().parents[1]
BUILD_REL = '.codex-build/eat-foreignreplywake-20261028'
PRIOR_REL = '.codex-build/eat-predecessorwake-20261028'
PRIOR = ROOT / PRIOR_REL
PRIOR_FREEZE_SHA = 'c8f50be0b4abd8cce517d0217e39ee500d02f94990ec5aa0c095b4a856ba09f0'
PRIOR_MANIFEST_SHA = 'd80ea0d3c054628649e8e8dd1efe83354e92c089b2e63bde435e10a73e0ffa9b'
PRIOR_ARCHIVE_SHA = '0713692a33dfefd2c6b5862484470e752dc58813af74a90bf7cbf708ffabdebf'
BASE_REL = '.codex-build/eat-queue-review-20261027'
BUILD, BASE = ROOT / BUILD_REL, ROOT / BASE_REL
NAME = 'eat_cohort_foreignreplywake_l15_f'
PRIMARY = NAME + '|immediate'
TAILWAIT = 'eat_cohort_tailwait_l15_f|immediate'
PAIRSHIELD = 'eat_cohort_pairshield_l15_f|immediate'
STOPLOSS = 'eat_cohort_stoploss_l15_f|immediate'
BASELINES = (TAILWAIT, PAIRSHIELD, STOPLOSS)
BASE_MANIFEST_SHA = 'ac019fe253319c9eb1446e57339716c33335aefd441b626f69618fa75b6c6b6a'
BASE_FREEZE_SHA = 'd940e7b82270bc5c11c3c0ab4a6327a323171a1f2d7eaba86bf4324d33ed5449'
TW_REPORT_REL = '.codex-build/eat-cohort-tailwait-20261025/repair.json'
TW_REPORT_SHA = 'efbb2dd3e708e16e4573ff8bb054a0c7442bd2a183396643b840d8973661cc15'
PRODUCER_METADATA_SHA = 'a8721441951cb9fa3ccb2dbab40d2cbcae68c0f4ce1aeedc6009407a33aed05a'
RECOVERY_REPORTS = {
    'declinewake': ('.codex-build/eat-declinewake-multi-20261028/screen-report.json', '310362a605b1597b82901da3bc1206759f2d85469c35c16cb383ef1bd2011cb4'),
    'travelwake': ('.codex-build/eat-travelwake-20261028/screen-report.json', '2a7818fca018518d68e98ba6fa567609c5b44c72bd87f31f937ceac745e8697f')}
RECOVERY_PROTOCOL = dict(reference_report_sha256={label: value[1] for label, value in RECOVERY_REPORTS.items()},
    required_existing_success_count=37, reference_wait_seconds=4990, target_wait_seconds=4971,
    minimum_recovered_wait_seconds=19,
    target_patient=dict(source_index=240, patient_id='P02', maximum_arrival_seconds=767),
    direct_wait_targets=[dict(source_index=7, patient_id='P03', maximum_wait_seconds=160),
        dict(source_index=240, patient_id='P02', maximum_wait_seconds=127),
        dict(source_index=265, patient_id='P01', maximum_wait_seconds=236)],
    legacy_goals_replaced=False)
PRODUCER_SOURCES = tuple('inquiry_lab/cohort_foreignreplywake_' + s + '.py' for s in
    ('policy', 'context', 'workflow', 'service', 'execution', 'runner')) + tuple(
    'test_inquiry_lab_cohort_foreignreplywake_' + s + '.py' for s in
    ('policy', 'context', 'workflow', 'service', 'execution', 'runner')) + (
    'inquiry_lab/foreign_reply_wake_evidence.py', 'test_inquiry_lab_foreign_reply_wake_evidence.py')
NEW_SOURCES = ('inquiry_lab/foreignreplywake_screen_diagnostic.py',
               'test_inquiry_lab_foreignreplywake_screen_diagnostic.py')
ITEM_FIELDS = ('file', 'raw_folder', 'source_index', 'input_sha256', 'raw_sha256', 'compressed_sha256')
ENTRY_FIELDS = ('kind', 'seed', 'family', 'scenario_id', 'parent_stage', 'original_origin')
ORIGINAL_MODULES = ('server.inquiry_workflow', 'server.inquiry_policies',
                    'inquiry_lab.horizon_runner', 'server.service', 'inquiry_lab.harness')
INPUT_FIELDS = {'now', 'candidates', 'observations', 'pending', 'patient_pending',
                'max_new', 'last_contact_at', 'time_context'}
SELECTION_RULE = dict(origins=['repair', 'development'], per_origin=6,
    order='source_index_ascending', min_patients=3, max_patients=4,
    strata=[dict(patients=3, per_origin=3), dict(patients=4, per_origin=3)], count_baseline=TAILWAIT,
    used_outcome_fields=False)


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def read(path):
    return json.loads(Path(path).read_bytes(), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))


def write(path, value):
    with Path(path).open('xb') as stream:
        stream.write(raw(value))


def typed_id(value):
    assert type(value) in (str, int) and (type(value) is int or value), 'invalid public identifier'
    return type(value).__name__, str(value)


def patient_ids(patients):
    assert type(patients) is list and patients
    ids = [typed_id(p['id']) for p in patients]
    assert len(ids) == len(set(ids)), 'duplicate typed patient identifier'
    return sorted(ids)


def small_entry(entry):
    """Size/identity eligibility only: no success, wait, or reply field is read."""
    branches = entry['baseline_branches']
    assert set(BASELINES) <= set(branches)
    ids = patient_ids(branches[TAILWAIT]['patients'])
    assert all(patient_ids(b['patients']) == ids for b in branches.values())
    return SELECTION_RULE['min_patients'] <= len(ids) <= SELECTION_RULE['max_patients']


def select_entries(entries):
    assert type(entries) is list and len(entries) == 360
    indices = [e['source_index'] for e in entries]
    assert all(type(i) is int for i in indices) and sorted(indices) == list(range(360))
    assert all(e['parent_stage'] in SELECTION_RULE['origins'] for e in entries)
    selected = []
    for origin in SELECTION_RULE['origins']:
        eligible = [e for e in sorted(entries, key=lambda x: x['source_index'])
                    if e['parent_stage'] == origin and small_entry(e)]
        chosen = []
        for stratum in SELECTION_RULE['strata']:
            sized = [e for e in eligible if len(patient_ids(e['baseline_branches'][TAILWAIT]['patients'])) == stratum['patients']]
            assert len(sized) >= stratum['per_origin'], 'three environments required for each origin and patient-count stratum'
            chosen.extend(sized[:stratum['per_origin']])
        assert len(chosen) == SELECTION_RULE['per_origin']
        selected.extend(deepcopy(sorted(chosen, key=lambda x: x['source_index'])))
    assert len(selected) == 12 and len({e['source_index'] for e in selected}) == 12
    return selected


def numeric():
    """Reuse frozen pure arithmetic in a new globals dictionary, never patch it."""
    parent = importlib.import_module('inquiry_lab.cohort_wavecap_diagnostic')
    common = importlib.import_module('inquiry_lab.diverse_diagnostic')
    namespace = dict(vars(parent), NAME=NAME, PRIMARY=PRIMARY)
    for name in ('comparison', 'early_gate'):
        function = getattr(parent, name)
        namespace[name] = types.FunctionType(function.__code__, namespace, name,
                                            function.__defaults__, function.__closure__)
        namespace[name].__kwdefaults__ = deepcopy(function.__kwdefaults__)
    return types.SimpleNamespace(rows=parent.rows, early_gate=namespace['early_gate'],
        comparison=namespace['comparison'], incremental_research_gate=common.incremental_research_gate)


def bindings():
    assert digest(BASE / 'source-freeze.json') == BASE_FREEZE_SHA
    assert digest(BASE / 'input-manifest.json') == BASE_MANIFEST_SHA
    old, old_freeze = read(BASE / 'input-manifest.json'), read(BASE / 'source-freeze.json')
    assert old_freeze['passed'] and old_freeze['protocol_sha256'] == BASE_MANIFEST_SHA
    assert old_freeze['source_sha256'] == old['source_sha256'] and len(old['source_sha256']) == 700
    assert all(digest(ROOT / n) == v for n, v in old['source_sha256'].items())
    assert digest(ROOT / TW_REPORT_REL) == TW_REPORT_SHA
    metadata_path = BUILD / 'producer-synthetic-tests.json'
    assert digest(metadata_path) == PRODUCER_METADATA_SHA
    producer = read(metadata_path)
    assert producer['status'] == 'producer_synthetic_complete_source_stop'
    assert producer['final_current_source_tests']['tests_run'] == 127
    assert producer['final_current_source_tests']['success'] and not producer['actual_benefit_validated']
    assert set(producer['source_sha256']) == set(PRODUCER_SOURCES)
    assert digest(PRIOR / 'screen-manifest.json') == PRIOR_MANIFEST_SHA
    assert digest(PRIOR / 'screen-source-freeze.json') == PRIOR_FREEZE_SHA
    assert digest(PRIOR / 'screen-archive.json') == PRIOR_ARCHIVE_SHA
    prior = read(PRIOR / 'screen-manifest.json')
    prior_freeze = read(PRIOR / 'screen-source-freeze.json')
    assert prior_freeze['passed'] and prior_freeze['manifest_sha256'] == PRIOR_MANIFEST_SHA
    assert prior_freeze['source_sha256'] == prior['source_sha256'] and len(prior['source_sha256']) == 760
    assert {n: prior['source_sha256'][n] for n in old['source_sha256']} == old['source_sha256']
    sources = dict(prior['source_sha256'])
    assert not set(sources) & set(PRODUCER_SOURCES + NEW_SOURCES)
    sources.update(producer['source_sha256'])
    sources.update({name: digest(ROOT / name) for name in NEW_SOURCES})
    assert len(sources) == 776 and all(digest(ROOT / n) == v for n, v in sources.items())
    private = {BASE_REL + '/source-freeze.json': BASE_FREEZE_SHA,
               BASE_REL + '/input-manifest.json': BASE_MANIFEST_SHA,
               TW_REPORT_REL: TW_REPORT_SHA,
               BUILD_REL + '/producer-synthetic-tests.json': PRODUCER_METADATA_SHA,
               PRIOR_REL + '/screen-manifest.json': PRIOR_MANIFEST_SHA,
               PRIOR_REL + '/screen-source-freeze.json': PRIOR_FREEZE_SHA,
               PRIOR_REL + '/screen-archive.json': PRIOR_ARCHIVE_SHA}
    private[BUILD_REL + '/helper-only-source-tests.json'] = '7f7c2adc72c8cb609cf42c9ee5ae4a836ecc5705b00388bfe8d99a354b371008'
    private.update({path: expected for path, expected in RECOVERY_REPORTS.values()})
    private.update(producer['namespace_proofs'])
    rel = producer['final_current_source_tests']['result_file']
    private[rel] = producer['final_current_source_tests']['result_sha256']
    private[BUILD_REL + '/' + producer['final_current_source_tests']['text_file']] = producer['final_current_source_tests']['text_sha256']
    assert all(digest(ROOT / n) == v for n, v in private.items())
    selected = select_entries(old['entries'])
    assert selected == prior['entries'], 'paired multi12 input selection must be unchanged'
    assert all(len(e['baseline_branches']) == 25 for e in selected)
    return dict(schema_version=1, name='foreignreplywake_screen_20261028', candidate=NAME,
        phase='foreignreply12', recovery_protocol=deepcopy(RECOVERY_PROTOCOL), selection_rule=deepcopy(SELECTION_RULE), selected_indices=[e['source_index'] for e in selected],
        entries=selected, source_sha256=sources, binding_sha256=private,
        base_manifest_sha256=BASE_MANIFEST_SHA, base_source_freeze_sha256=BASE_FREEZE_SHA,
        producer_metadata_sha256=PRODUCER_METADATA_SHA, observed_training_only=True, heldout=False,
        semantic_verification=False, operational_adoption=False, automatic_expansion=False,
        fresh_validation_allowed=False, cached_baselines_only=True, controls_reexecuted=0,
        cached_baseline_branches_per_environment=25,
        numeric_function_sources=['inquiry_lab/cohort_wavecap_diagnostic.py', 'inquiry_lab/diverse_diagnostic.py'])


def freeze(checks_path):
    assert not (BUILD / 'screen-manifest.json').exists() and not (BUILD / 'screen-source-freeze.json').exists()
    assert not (BUILD / 'screen-started.json').exists()
    protocol, checks = bindings(), read(checks_path)
    assert checks['passed'] and checks['failures'] == checks['errors'] == checks['skips'] == 0
    assert checks['scope'] == 'targeted_foreignreplywake_screen_tests'
    assert checks['source_sha256_before'] == checks['source_sha256_after'] == protocol['source_sha256']
    private = checks['private_files_sha256']
    assert private and all(digest(ROOT / n) == v for n, v in private.items())
    assert checks['independent_checker_sha256'] in private.values()
    protocol['binding_sha256'].update(private)
    protocol['binding_sha256'][Path(checks_path).resolve().relative_to(ROOT).as_posix()] = digest(checks_path)
    protocol['independent_checker_sha256'] = checks['independent_checker_sha256']
    write(BUILD / 'screen-manifest.json', protocol)
    write(BUILD / 'screen-source-freeze.json', dict(passed=True,
        manifest_sha256=digest(BUILD / 'screen-manifest.json'), source_sha256=protocol['source_sha256'],
        candidate_branches_before_freeze=0, semantic_verification=False,
        qualification='Twelve size-stratified multi-patient known training environments; output/numeric screen only'))


def frozen():
    protocol, seal = read(BUILD / 'screen-manifest.json'), read(BUILD / 'screen-source-freeze.json')
    assert seal['passed'] and seal['candidate_branches_before_freeze'] == 0
    assert seal['manifest_sha256'] == digest(BUILD / 'screen-manifest.json')
    assert seal['source_sha256'] == protocol['source_sha256'] and len(protocol['source_sha256']) == 776
    expected = bindings()
    for key in expected:
        if key != 'binding_sha256':
            assert protocol[key] == expected[key]
    assert all(protocol['binding_sha256'].get(n) == v for n, v in expected['binding_sha256'].items())
    assert protocol['independent_checker_sha256'] in protocol['binding_sha256'].values()
    assert all(digest(ROOT / n) == v for n, v in protocol['source_sha256'].items())
    assert all(digest(ROOT / n) == v for n, v in protocol['binding_sha256'].items())
    return protocol


def namespace_state():
    modules = {name: importlib.import_module(name) for name in ORIGINAL_MODULES}
    policies, harness = modules['server.inquiry_policies'], modules['inquiry_lab.harness']
    return dict(bindings={name: {key: id(value) for key, value in vars(module).items()}
                         for name, module in modules.items()},
        registry_sha256=sha(raw(policies._POLICIES)), installed=sorted(harness._INSTALLED))


def run_candidate(entry):
    """One candidate only; confirm the built multi-patient size before DB creation."""
    try:
        runner = importlib.import_module('inquiry_lab.cohort_foreignreplywake_runner')
        harness = importlib.import_module('inquiry_lab.harness')
        scenario = harness.build(entry['kind'], entry['seed'])
        assert sha(raw(scenario)) == entry['input_sha256']
        assert 3 <= len(scenario['patients']) <= 4 and small_entry(entry)
        assert patient_ids(scenario['patients']) == patient_ids(entry['baseline_branches'][TAILWAIT]['patients'])
        before = namespace_state()
        replay = runner.run_scenario(scenario, ((NAME, 'immediate'),))
        after = namespace_state()
        replay['screen_namespace_proof'] = dict(passed=before == after, before=before, after=after,
            scope='All original namespace binding identities plus policy registry contents and installed set; not recursive mutable state')
        return replay
    except Exception:
        return dict(screen_execution_error=traceback.format_exc(), source_index=entry['source_index'],
                    input_sha256=entry['input_sha256'])


def output_case(item, entry, replay):
    assert set(item) == set(ITEM_FIELDS) and small_entry(entry)
    assert replay['arms'] == [[NAME, 'immediate']] and len(replay['branches']) == 1
    branch = replay['branches'][0]
    assert [branch['policy'], branch['rule']] == [NAME, 'immediate']
    assert sha(raw(replay['scenario'])) == entry['input_sha256'] == item['input_sha256']
    assert 3 <= len(replay['scenario']['patients']) <= 4
    assert replay['initial_snapshot_sha256'] == branch['snapshot_sha256'] == entry['snapshot_sha256']
    assert replay['environment_sha256'] == branch['environment_sha256'] == entry['environment_sha256']
    n = numeric()
    patients = n.rows(branch['patients'])
    expected = n.rows(entry['baseline_branches'][TAILWAIT]['patients'])
    assert patient_ids(patients) == patient_ids(expected) == patient_ids(replay['scenario']['patients'])
    assert all(a['created_at'] == b['created_at'] for a, b in zip(patients, expected))
    assert all(p['inquiry_count'] <= 10 and len(p['contacted']) == len({typed_id(h) for h in p['contacted']}) for p in patients)
    proof = replay['screen_namespace_proof']
    assert proof['passed'] and proof['before'] == proof['after']
    assert set(proof['before']['bindings']) == set(ORIGINAL_MODULES)
    calls = branch['policy_calls']
    decisions = branch['db_decisions']
    assert len(calls) == len(decisions)
    assert len({d['id'] for d in decisions}) == len(decisions)
    for call, decision in zip(calls, decisions):
        inp, out = call['inputs'], call['output']
        assert set(inp) in (INPUT_FIELDS, INPUT_FIELDS | {'wake_context'})
        assert call['name'] == NAME and type(inp['max_new']) is int and 0 <= inp['max_new'] <= 10
        assert call['name'] == decision['policy_name'] and call['db_decision_id'] == decision['id']
        assert typed_id(call['patient_id']) == typed_id(decision['patient_id'])
        assert inp['now'] == decision['at'] and out['send'] == decision['hospital_ids']
        assert out['reason'] == decision['reason'] and out['evidence'] == decision['evidence']
        now, sent = inp['now'], out['send']
        assert type(now) in (int, float) and math.isfinite(now)
        assert type(sent) is list and len(sent) == len({typed_id(h) for h in sent}) and len(sent) <= inp['max_new']
        own = inp['patient_pending']
        assert all(type(q['submitted_at']) in (int, float) and math.isfinite(q['submitted_at']) for q in own)
        fresh = [q for q in own if 0 <= now - q['submitted_at'] <= 180]
        assert len(fresh) + len(sent) <= 4
        available = {typed_id(c.get('hospital_id', c.get('hospital'))) for c in inp['candidates']}
        assert {typed_id(h) for h in sent} <= available
    errors = [deepcopy(e) for e in branch['db_events'] if e['type'] == 'inquiry_workflow_exhausted'
              and e['data'].get('reason') in ('CALCULATION_ERROR', 'STATE_ERROR')]
    compact = {key: deepcopy(entry[key]) for key in ENTRY_FIELDS}
    compact['branches'] = dict(deepcopy(entry['baseline_branches']), **{PRIMARY: dict(patients=patients)})
    return dict(item, audit=None, semantic_verified=False, compact=compact,
        output_invariants=dict(passed=not errors, patients=len(patients), captured_public_cap_calls_checked=len(calls),
            namespace_preserved=True, scope='Output/header/contact caps and namespace identities only; not FIFO, provenance, model or complete policy semantics'),
        execution_diagnostics=dict(workflow_error_count=len(errors), workflow_error_events=errors,
                                   committed_decisions_checked=len(decisions), policy_calls_checked=len(calls)))


def read_candidate(item, entry):
    assert item['raw_folder'] == 'screen-cases' and Path(item['file']).name == item['file']
    assert item['file'] == f"{entry['source_index']:03d}_{entry['seed']}.json.gz"
    packed = (BUILD / item['raw_folder'] / item['file']).read_bytes()
    assert sha(packed) == item['compressed_sha256']
    body = gzip.decompress(packed)
    assert sha(body) == item['raw_sha256']
    replay = json.loads(body, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    assert raw(replay) == body
    return output_case(item, entry, replay)


def execute(workers=1):
    assert type(workers) is int and workers == 1, 'only one worker is allowed'
    temp = (BUILD / 'tmp').resolve()
    assert temp.is_dir() and Path(os.environ.get('TEMP', '')).resolve() == temp and Path(os.environ.get('TMP', '')).resolve() == temp
    protocol = frozen()
    for name in ('screen-started.json', 'screen-cases', 'screen-raw-index.json', 'screen-report.json', 'screen-failure.json'):
        assert not (BUILD / name).exists(), 'screen cannot be reexecuted'
    entries = protocol['entries']
    assert len(entries) == 12 and all(small_entry(e) for e in entries)
    start = time.perf_counter()
    write(BUILD / 'screen-started.json', dict(candidate=NAME, phase='foreignreply12',
        selected_indices=protocol['selected_indices'], source_freeze_sha256=digest(BUILD / 'screen-source-freeze.json'),
        manifest_sha256=digest(BUILD / 'screen-manifest.json'), new_candidate_branches_scheduled=12,
        known_training=True, heldout=False, controls_reexecuted=0, workers=1))
    folder = BUILD / 'screen-cases'
    folder.mkdir()
    items = []
    try:
        with ProcessPoolExecutor(max_workers=1) as pool:
            for entry, replay in zip(entries, pool.map(run_candidate, entries, chunksize=1)):
                body = raw(replay)
                packed = gzip.compress(body, compresslevel=6, mtime=0)
                file = f"{entry['source_index']:03d}_{entry['seed']}.json.gz"
                with (folder / file).open('xb') as stream:
                    stream.write(packed)
                items.append(dict(file=file, raw_folder='screen-cases', source_index=entry['source_index'],
                    input_sha256=entry['input_sha256'], raw_sha256=sha(body), compressed_sha256=sha(packed)))
                print(json.dumps(dict(saved_raws=len(items), scheduled_raws=12,
                                      source_index=entry['source_index'])), flush=True)
        assert len(items) == 12
        write(BUILD / 'screen-raw-index.json', dict(all_outcomes_saved=True, candidate=NAME,
            phase='foreignreply12', source_freeze_sha256=digest(BUILD / 'screen-source-freeze.json'), cases=items))
        cases, errors = [], []
        for item, entry in zip(items, entries):
            try:
                cases.append(read_candidate(item, entry))
            except Exception:
                errors.append(dict(item, error=traceback.format_exc()))
        assert frozen() == protocol
        n = numeric()
        gate = n.early_gate(cases) if not errors else None
        incremental = n.incremental_research_gate(gate) if gate is not None else None
        report = dict(candidate=NAME, phase='foreignreply12', selected_indices=protocol['selected_indices'],
            source_sha256=protocol['source_sha256'], source_freeze_sha256=digest(BUILD / 'screen-source-freeze.json'),
            manifest_sha256=digest(BUILD / 'screen-manifest.json'), raw_index_sha256=digest(BUILD / 'screen-raw-index.json'),
            cases=cases, output_errors=errors,
            output_checks_passed=not errors and all(c['output_invariants']['passed'] for c in cases), early_gate=gate,
            numeric_accounting_complete=not errors,
            incremental_research_gate=incremental, semantic_verification=False, independent_semantic_audits=0,
            audit_index_sha256=None, controls_reexecuted=0, new_candidate_branches=12, raw_outcomes_saved=12,
            source_before_after_equal=True, namespace_checks=len(cases), observed_training_only=True, heldout=False,
            workflow_error_count=sum(c['execution_diagnostics']['workflow_error_count'] for c in cases),
            operational_adoption=False, automatic_expansion=False, expansion_allowed=False,
            fresh_validation_allowed=False, promotion_allowed=False, elapsed_seconds=time.perf_counter() - start,
            scope='Size-stratified multi-patient numeric/output screen only; neither complete semantic audit nor operational evidence')
        write(BUILD / 'screen-report.json', report)
        return report
    except Exception:
        write(BUILD / 'screen-failure.json', dict(error=traceback.format_exc(), saved_raws=len(items),
            source_index_saved=[i['source_index'] for i in items], semantic_verification=False,
            operational_adoption=False, automatic_expansion=False))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('freeze', 'screen'))
    parser.add_argument('--checks')
    parser.add_argument('--workers', type=int, default=1)
    args = parser.parse_args()
    if args.command == 'freeze':
        assert args.checks and args.workers == 1
        freeze(args.checks)
    else:
        assert args.checks is None
        execute(args.workers)


if __name__ == '__main__':
    main()
