"""One prospective recourse candidate; immutable observed controls, new audits.

Known parent stages remain separate training slices.  Cached policy/FIFO
evidence is inherited from the sealed parent, never reexecuted by this driver.
Only newly saved replays receive a semantic audit; subsequent checks bind that
proof to the exact raw bytes and complete frozen source map.
"""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import platform
import random
import time

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.codex-build/eat-cohort-recourse-20261021'
PARENT = ROOT / '.codex-build/eat-cohort-hedge-20261021'
EXPERIMENT = 'eat_cohort_recourse_20261021'
NAMESPACE = 'eat-cohort-recourse-v1-20261021-5255b59a-5b3e-4faa-a337-c1e6a82d58e9'
EXPECTED_BEFORE = 'f80cf799286f2346375b639134cdd1a74fa201e0ac49752fe835d7548fd1bdd2'
EXPECTED_PARENT = 'ef613a6615519df7273583cc3770b9a9d8391dda79375f8734acae35dd0521cd'
NAME = 'eat_cohort_recourse_l15_f'
PRIMARY = NAME + '|immediate'
HEDGE = 'eat_cohort_hedge_l15_f|immediate'
COHORT_SUCCESS = 'eat_cohort_success_l15_f|immediate'
CONTROLS = (('fixed_3', 'immediate'), ('eat_l15_f', 'immediate'),
    ('eat_residual_l15_f', 'immediate'), ('eat_horizon_l15_f', 'immediate'),
    ('eat_joint_first_l15_f', 'immediate'), ('eat_joint_tail_l15_f', 'immediate'),
    ('eat_joint_reselect_l15_f', 'immediate'), ('eat_joint_budget_l15_f', 'immediate'),
    ('v3_w45_h900', 'immediate'), ('eat_l15_f', 'hold120'),
    ('eat_cohort_success_l15_f', 'immediate'), ('eat_cohort_dynamic_l15_f', 'immediate'),
    ('eat_cohort_hedge_l15_f', 'immediate'))
ARMS = CONTROLS + ((NAME, 'immediate'),)
REPAIR_ARMS = ((NAME, 'immediate'),)
ORDER = ('repair', 'development', 'validation')
FIXTURE = 'simulation/fixtures/eat_intent_20261020/mix7.json.gz'
WORKING_REFERENCE = 'docs/EAT_INQUIRY_EXPERIMENT.md'
LEDGER_FILE = 'preservation/control-ledger.json'
CONFIDENCE_SCOPE = 'marginal endpoint-wise95; not joint or familywise-adjusted'
FAMILIES = ('immediate_all_accept', 'delayed_near_fast_far', 'no_response_near',
    'simultaneous_w1', 'staggered_w2', 'all_unavailable_control',
    'historical_shift', 'history_helpful')
# Importing this module performs no preparation, seed generation, or execution.
NEW_SOURCES = tuple('inquiry_lab/cohort_recourse_' + name + '.py'
    for name in ('policy', 'context', 'model', 'runner', 'audit', 'experiment')) + tuple(
    'test_inquiry_lab_cohort_recourse_' + name + '.py'
    for name in ('policy', 'context', 'model', 'runner', 'audit', 'experiment')) + tuple(
    '.codex-build/eat-cohort-recourse-20261021/' + name + '.py'
    for name in ('preserve_parent', 'run_checks', 'preflight_equivalence', 'finalize_phase'))
SETTINGS = dict(candidate_count=1, lambda_seconds=15,
    changed_factor='expected cancellation workahead for simulated not-yet-started first-cohort inquiries; unchanged Hedge menu, conditional CS memberships/counts/order, original-own reply CDF and actual concurrency4',
    objective='success nonincrease in failures and capped selection wait goals; all benchmark priorities unknown/default4',
    known_slices='parent repair240/827 and parent development120/415 are separate training slices; no pooled success/wait gate',
    known_repair_gate='parent-repair827 failed<=135 and wait<=113296',
    known_development_gate='parent-development415 failed<=51 and wait<53498',
    individual_success_identity_preservation_is_hard_gate=False,
    best_control_or_pooled_lexicographic_gate=False,
    fresh_gate='failed<=CS and failed<=Hedge; wait<CS and wait<Hedge',
    validation_gate='each CS/Hedge upper95 deltafail<=0 AND upper95 deltawait<0; all four conditions',
    confidence_scope=CONFIDENCE_SCOPE, bootstrap_replicates=10000, bootstrap_seed=20261021,
    cluster_scope='scenario; each stress role seed is shared across eight families, not seed-block or familywise inference',
    priority_scope='all frozen lab payloads unknown/None/effective4; expert-tier adapter units are not clinical efficacy evidence',
    repair_environments=360, repair_patients=1242, repair_new_branches=360,
    repair_cached_branches=4680, parent_owned_raws=360, ancestor_raws=1320,
    development_environments=120, validation_environments=368, fresh_arms=14,
    maximum_new_branches=7192, manifest_entries=848,
    arrival_scope='recorded elapsed selection+travel; failure1800 arrival cost and both-selected arrival changes are reports, not gates; never subtract created_at again',
    cached_audit_scope='sealed parent patient compacts and independent saved audit/accountant; parent own360 header/outcomes checked once; known827 other12 controls rely on parent evidence',
    new_semantic_audit='once per raw/source digest; later checks verify stored proof and bytes, not an optimizer rerun',
    fresh_role_seeds=425, fresh_seed_reuse=False, generated_unexecuted_prior_roles_are_reserved=True,
    no_result_exclusions=True, all_stage_raw_saved_before_assessment=True,
    fixed_actual_contact_cap=10, fixed_actual_concurrent_cap=4,
    A_H_offers_atomicity_unchanged=True, oracle_input_to_policy=False,
    heldout_retuning=False, operational_adoption=False, live_server_changed=False)


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_new(path, value):
    with Path(path).open('xb') as stream:
        stream.write(value)


def arm(policy, rule):
    return policy + '|' + rule


def wait(patient):
    return 300 if patient['failed'] else patient['selection_seconds']


def patient_rows(patients):
    """Only recorded patient outcomes; no oracle or model-dependent compact."""
    answer = []
    ids = set()
    for patient in patients:
        assert patient['id'] not in ids
        ids.add(patient['id'])
        assert type(patient['failed']) is bool
        value = patient['selection_seconds']
        if patient['failed']:
            assert value is None
        else:
            assert type(value) in (int, float) and 0 <= value <= 300
        arrival, travel = patient['estimated_arrival_seconds'], patient['travel_seconds']
        if patient['failed']:
            assert arrival is None and travel is None
        else:
            assert type(arrival) in (int, float) and type(travel) in (int, float) and travel >= 0
            assert abs(arrival - (value + travel)) <= 1e-9
        destination = patient['destination_hospital_id'] if 'destination_hospital_id' in patient else patient['destination']
        contacted = patient['contacted_hospitals'] if 'contacted_hospitals' in patient else patient['contacted']
        if 'destination_hospital_id' in patient and 'destination' in patient:
            assert patient['destination_hospital_id'] == patient['destination']
        if 'contacted_hospitals' in patient and 'contacted' in patient:
            assert patient['contacted_hospitals'] == patient['contacted']
        assert type(patient['inquiry_count']) is int and patient['inquiry_count'] >= 0
        assert type(contacted) is list and patient['inquiry_count'] == len(contacted)
        created = patient['created_at']
        assert type(created) in (int, float) and created >= 0
        answer.append(dict(id=patient['id'], created_at=created,
            failed=patient['failed'], selection_seconds=value, estimated_arrival_seconds=arrival,
            travel_seconds=travel, destination=destination, inquiry_count=patient['inquiry_count'],
            contacted=deepcopy(contacted)))
    return sorted(answer, key=lambda p: (type(p['id']).__name__, str(p['id'])))


def verify_old(check_raw=False):
    path = BUILD / 'preservation/before.json'
    assert file_sha(path) == EXPECTED_BEFORE
    before = read(path)
    assert before['passed'] and before['source_count'] == len(before['parent_sources']) == 442
    assert before['observed_environments'] == 360 and before['observed_patients'] == 1242
    assert before['all_observed_now_training'] is True
    assert before['new_performance_runs'] == 0 and before['operational_adoption'] is False
    assert before['parent_composite_sha256'] == EXPECTED_PARENT
    assert before['inherited_excluded_working_reference']['path'] == WORKING_REFERENCE
    references = before['strict_reference_sha256']
    assert len(references) == 767 and WORKING_REFERENCE not in references
    mapping = dict(references)
    for name, digest in before['parent_sources'].items():
        assert name != WORKING_REFERENCE and references.get(name) == digest
        mapping[name] = digest
    for name, digest in mapping.items():
        assert file_sha(ROOT / name) == digest, name
    assert file_sha(ROOT / before['parent_composite_path']) == EXPECTED_PARENT
    bindings = before['immutable_raw_bindings']
    assert len(bindings) == 1680
    if check_raw:
        for name, value in bindings.items():
            path = ROOT / name
            assert path.stat().st_size == value['compressed_bytes']
            assert file_sha(path) == value['compressed_sha256'], name
    return file_sha(BUILD / 'preservation/before.json')


def source_hashes():
    old = read(BUILD / 'preservation/before.json')['parent_sources']
    assert len(old) == 442 and len(NEW_SOURCES) == len(set(NEW_SOURCES)) == 16
    assert not set(old) & set(NEW_SOURCES)
    return {name: file_sha(ROOT / name) for name in sorted(set(old) | set(NEW_SOURCES))}


def _read_replay(path, metadata):
    packed = Path(path).read_bytes()
    assert sha(packed) == metadata['compressed_sha256']
    body = gzip.decompress(packed)
    assert sha(body) == metadata['raw_sha256']
    replay = json.loads(body)
    assert raw(replay) == body
    return replay, body


def tracked_targets(entries, inherited):
    """Old identifiers are reporting metadata, never additional success gates."""
    answer = {}
    for label, targets in inherited.items():
        original = 'repair' if label == 'old_six' else 'development'
        rows = []
        for target in targets:
            found = [entry for entry in entries if entry['parent_stage'] == 'repair'
                and entry['original_origin'] == original and entry['kind'] == 'mix'
                and entry['seed'] == target['seed']]
            assert len(found) == 1
            assert target['patient'] in {p['id'] for p in found[0]['branches'][HEDGE]['patients']}
            rows.append(dict(parent_stage='repair', original_origin=original, kind='mix',
                             seed=target['seed'], patient=target['patient']))
        answer[label] = rows
    for origin in ('repair', 'development'):
        gained, lost = [], []
        for entry in entries:
            if entry['parent_stage'] != origin:
                continue
            cs = {p['id']: p for p in entry['branches'][COHORT_SUCCESS]['patients']}
            for patient in entry['branches'][HEDGE]['patients']:
                target = dict(parent_stage=origin, original_origin=entry['original_origin'],
                    kind=entry['kind'], seed=entry['seed'], patient=patient['id'])
                if cs[patient['id']]['failed'] and not patient['failed']:
                    gained.append(target)
                if not cs[patient['id']]['failed'] and patient['failed']:
                    lost.append(target)
        answer['parent_' + origin + '_hedge_gained_vs_CS'] = gained
        answer['parent_' + origin + '_hedge_lost_vs_CS'] = lost
    return answer


def prepare_cache():
    """One header/outcome check of all 360 own-parent replays, never an audit."""
    verify_old()
    before = read(BUILD / 'preservation/before.json')
    manifest = read(PARENT / 'input-manifest.json')
    inputs = {(e['stage'], e['kind'], e['seed']): e['sha256'] for e in manifest['entries']}
    entries = []
    control_names = {arm(*a) for a in CONTROLS}
    for parent_stage, count in (('repair', 240), ('development', 120)):
        path = PARENT / (parent_stage + '.json')
        assert file_sha(path) == before['observed_stages'][parent_stage]['report_sha256']
        report = read(path)
        assert len(report['cases']) == count
        for index, case in enumerate(report['cases']):
            compact = case['compact']
            kind, seed = compact['kind'], compact['seed']
            assert case['file'] == f'{index:03d}_{seed}.json.gz'
            path = PARENT / (parent_stage + '-cases') / case['file']
            relative = path.relative_to(ROOT).as_posix()
            bound = before['immutable_raw_bindings'][relative]
            assert all(case[k] == bound[k] for k in ('raw_sha256', 'compressed_sha256'))
            replay, _ = _read_replay(path, case)
            assert sha(raw(replay['scenario'])) == inputs[parent_stage, kind, seed]
            assert replay['scenario']['seed'] == seed and replay['scenario']['horizon_seconds'] == 300
            assert set(compact['branches']) == control_names
            branches = {name: dict(patients=patient_rows(compact['branches'][name]['patients']))
                        for name in sorted(control_names)}
            expected_ids = {p['id'] for p in replay['scenario']['patients']}
            assert len(expected_ids) == len(replay['scenario']['patients']) == compact['patient_count']
            assert all({p['id'] for p in v['patients']} == expected_ids for v in branches.values())
            expected_arms = [[HEDGE.split('|')[0], 'immediate']] if parent_stage == 'repair' else [list(a) for a in CONTROLS]
            assert replay['arms'] == expected_arms and len(replay['branches']) == len(expected_arms)
            for pair, branch in zip(expected_arms, replay['branches']):
                assert [branch['policy'], branch['rule']] == pair
                assert branch['snapshot_sha256'] == replay['initial_snapshot_sha256']
                assert branch['environment_sha256'] == replay['environment_sha256']
                assert patient_rows(branch['patients']) == branches[arm(*pair)]['patients']
            entries.append(dict(parent_stage=parent_stage, original_origin=case['origin_stage'],
                kind=kind, seed=seed, family=compact['family'], scenario_id=compact['scenario_id'],
                scenario_sha256=inputs[parent_stage, kind, seed], patient_count=compact['patient_count'],
                initial_snapshot_sha256=replay['initial_snapshot_sha256'],
                environment_sha256=replay['environment_sha256'], parent_raw_path=relative,
                raw_sha256=case['raw_sha256'], compressed_sha256=case['compressed_sha256'], branches=branches))
    assert len(entries) == 360
    for stage, expected in (('repair', 827), ('development', 415)):
        assert sum(e['patient_count'] for e in entries if e['parent_stage'] == stage) == expected
    keys = [(e['parent_stage'], e['kind'], e['seed']) for e in entries]
    assert len(set(keys)) == len(keys)
    inherited = read(PARENT / 'source-freeze.json')['protocol']['regressions_report_only']
    value = dict(schema_version=1, passed=True, parent_binding_sha256=EXPECTED_BEFORE,
        parent_composite_sha256=EXPECTED_PARENT, controls=[list(a) for a in CONTROLS],
        environments=360, patients=1242, cached_branches=4680, own_parent_raw_objects_checked=360,
        ancestor_policy_audits_reexecuted=0, policy_model_oracle_executions=0,
        evidence_scope=SETTINGS['cached_audit_scope'], entries=entries,
        regressions_report_only=tracked_targets(entries, inherited))
    write_new(BUILD / LEDGER_FILE, raw(value))
    return value


def control_ledger():
    value = read(BUILD / LEDGER_FILE)
    assert value['passed'] and value['parent_binding_sha256'] == EXPECTED_BEFORE
    assert value['parent_composite_sha256'] == EXPECTED_PARENT
    assert value['controls'] == [list(a) for a in CONTROLS]
    assert value['environments'] == len(value['entries']) == 360
    assert value['patients'] == 1242 and value['cached_branches'] == 4680
    assert len({(e['parent_stage'], e['kind'], e['seed']) for e in value['entries']}) == 360
    for stage, count, patients in (('repair', 240, 827), ('development', 120, 415)):
        entries = [e for e in value['entries'] if e['parent_stage'] == stage]
        assert len(entries) == count and sum(e['patient_count'] for e in entries) == patients
    for entry in value['entries']:
        assert set(entry['branches']) == {arm(*a) for a in CONTROLS}
        expected = {p['id'] for p in entry['branches'][HEDGE]['patients']}
        assert len(expected) == entry['patient_count']
        for branch in entry['branches'].values():
            assert len(branch['patients']) == len(expected) and {p['id'] for p in branch['patients']} == expected
    return value


def seeds(role, count):
    return tuple(int.from_bytes(hashlib.sha256(f'{NAMESPACE}|{role}|{i}'.encode()).digest()[:16], 'big')
                 | (1 << 127) for i in range(count))


def tasks(stage):
    if stage == 'repair':
        return [(e['kind'], e['seed'], REPAIR_ARMS) for e in control_ledger()['entries']]
    if stage not in ('development', 'validation'):
        raise ValueError('unknown stage')
    mixed, stress = (96, 3) if stage == 'development' else (320, 6)
    return [('mix', seed, ARMS) for seed in seeds(stage + '-mix', mixed)] + [
        ('family:' + family, seed, ARMS)
        for seed in seeds(stage + '-stress', stress) for family in FAMILIES]


def protocol():
    return dict(experiment=EXPERIMENT, namespace=NAMESPACE, settings=SETTINGS,
        arms=[list(a) for a in ARMS], source_sha256=source_hashes(),
        parent_binding_sha256=verify_old(), control_ledger_sha256=file_sha(BUILD / LEDGER_FILE),
        parent_composite_sha256=EXPECTED_PARENT, python=platform.python_version(),
        excluded_nonexecution_working_reference=WORKING_REFERENCE)


def reserved_seeds():
    """Read only committed manifests/results, including unexecuted role inputs."""
    before = read(BUILD / 'preservation/before.json')
    reserved = set()
    for name in sorted(before['strict_reference_sha256']):
        path = ROOT / name
        if name.endswith('/input-manifest.json'):
            reserved.update(e['seed'] for e in read(path)['entries'])
        elif name.endswith(('inquiry_lab_validation_20261020.json',
                            'inquiry_lab_confirm_validation_20261020.json')):
            reserved.update(e['seed'] for e in read(path)['results'])
    reserved.update(e['seed'] for e in control_ledger()['entries'])
    return reserved


def reserved_namespaces():
    before = read(BUILD / 'preservation/before.json')
    answer = set()
    for name in before['strict_reference_sha256']:
        if name.endswith(('/source-freeze.json', '/pre-development.json')):
            value = read(ROOT / name)
            namespace = value.get('protocol', {}).get('namespace')
            if namespace is not None:
                answer.add(namespace)
    return answer


def freeze():
    if not (BUILD / LEDGER_FILE).exists():
        prepare_cache()
    p = protocol()
    tests = read(BUILD / 'tests.json')
    assert tests['passed'] and tests['errors'] == tests['failures'] == tests['skipped'] == 0
    assert tests['source_sha256_before'] == tests['source_sha256_after'] == p['source_sha256']
    for name in ('policy', 'context', 'model', 'runner', 'audit', 'experiment'):
        assert tests['module_counts']['test_inquiry_lab_cohort_recourse_' + name] > 0
    preflight = read(BUILD / 'preflight/parent-equivalence.json')
    assert preflight['passed'] and preflight['full_raw_equal']
    assert preflight['controls'] == [list(a) for a in CONTROLS]
    assert preflight['formal_performance_runs'] == 0 and preflight['used_to_tune_policy_or_gates'] is False
    assert preflight['source_sha256_before'] == preflight['source_sha256_after'] == p['source_sha256']
    candidate_checks = preflight['candidate_checks']
    assert len(candidate_checks) == 2 and {x['label'] for x in candidate_checks} == {'saved_seed7', 'family_seed7'}
    for check in candidate_checks:
        assert check['audit']['passed'] and file_sha(BUILD / 'preflight' / check['file']) == check['compressed_sha256']
    roles = [*seeds('development-mix', 96), *seeds('validation-mix', 320),
             *seeds('development-stress', 3), *seeds('validation-stress', 6)]
    assert NAMESPACE not in reserved_namespaces()
    assert len(roles) == len(set(roles)) == 425 and not set(roles) & reserved_seeds()
    write_new(BUILD / 'source-freeze.json', raw(dict(passed=True, protocol=p,
        formal_performance_runs_before_freeze=0, all_parent_observed_stages_are_training=True,
        fresh_inputs_generated_before_freeze=False, reserved_seed_overlap=0)))
    from . import harness
    entries = []
    known = control_ledger()['entries']
    for stage in ORDER:
        for index, (kind, seed, _) in enumerate(tasks(stage)):
            parent_stage = known[index]['parent_stage'] if stage == 'repair' else None
            digest = known[index]['scenario_sha256'] if stage == 'repair' else sha(raw(harness.build(kind, seed)))
            entries.append(dict(stage=stage, parent_stage=parent_stage, kind=kind, seed=seed, sha256=digest))
    assert len(entries) == 848
    write_new(BUILD / 'input-manifest.json', raw(dict(entries=entries,
        fresh_inputs_generated_after_freeze=True)))
    names = ['preservation/before.json', LEDGER_FILE, 'tests.json', 'source-freeze.json',
             'input-manifest.json', 'preflight/parent-equivalence.json']
    names += ['preflight/' + x['file'] for x in candidate_checks]
    names += [q.relative_to(BUILD).as_posix() for q in sorted((BUILD / 'diagnosis').rglob('*')) if q.is_file()]
    write_new(BUILD / 'pre-development.json', raw(dict(passed=True, protocol=p,
        evidence_sha256={name: file_sha(BUILD / name) for name in names})))
    print(dict(frozen=True, sources=len(p['source_sha256']), inputs=848), flush=True)


def verify_freeze():
    value = read(BUILD / 'pre-development.json')
    frozen = read(BUILD / 'source-freeze.json')
    assert value['passed'] and frozen['passed'] and value['protocol'] == frozen['protocol'] == protocol()
    for name, digest in value['evidence_sha256'].items():
        assert file_sha(BUILD / name) == digest, name
    return value


def scope_metrics(compacts, names):
    result = {}
    for name in names:
        patients = [p for c in compacts for p in c['branches'][name]['patients']]
        failed = sum(p['failed'] for p in patients)
        arrival_cost = sum(1800 if p['failed'] else p['estimated_arrival_seconds'] for p in patients)
        selected = [p for p in patients if not p['failed']]
        result[name] = dict(patients=len(patients), successes=len(patients) - failed,
            failed=failed, wait_sum_seconds=sum(wait(p) for p in patients),
            failed_by_priority={'4': failed, '2': 0, '1': 0}, arrival_cost_sum_seconds=arrival_cost,
            J=arrival_cost / len(patients) if patients else None,
            arrival_mean_selected=sum(p['estimated_arrival_seconds'] for p in selected) / len(selected) if selected else None,
            inquiries_per_patient=sum(p['inquiry_count'] for p in patients) / len(patients) if patients else None)
    return result


def paired(compacts, before):
    result = dict(before=before, after=PRIMARY, patients=0, scenarios=len(compacts),
        failed_count_delta=0, wait_sum_delta_seconds=0, lost_selections=0, new_selections=0,
        wait_improved=0, wait_worsened=0, wait_same=0,
        maximum_individual_wait_increase_seconds=0, lost_identifiers=[], gained_identifiers=[],
        arrival_cost_sum_delta_seconds=0, maximum_individual_arrival_cost_increase_seconds=0,
        maximum_both_selected_arrival_increase_seconds=0, both_selected=0)
    for c in compacts:
        old = {p['id']: p for p in c['branches'][before]['patients']}
        new = {p['id']: p for p in c['branches'][PRIMARY]['patients']}
        assert set(old) == set(new)
        for pid, patient in new.items():
            prior = old[pid]
            delta = wait(patient) - wait(prior)
            result['patients'] += 1
            result['failed_count_delta'] += int(patient['failed']) - int(prior['failed'])
            result['wait_sum_delta_seconds'] += delta
            result['wait_improved'] += delta < 0
            result['wait_worsened'] += delta > 0
            result['wait_same'] += delta == 0
            result['maximum_individual_wait_increase_seconds'] = max(
                result['maximum_individual_wait_increase_seconds'], delta)
            arrival_delta = (1800 if patient['failed'] else patient['estimated_arrival_seconds']) - (
                1800 if prior['failed'] else prior['estimated_arrival_seconds'])
            result['arrival_cost_sum_delta_seconds'] += arrival_delta
            result['maximum_individual_arrival_cost_increase_seconds'] = max(
                result['maximum_individual_arrival_cost_increase_seconds'], arrival_delta)
            if not patient['failed'] and not prior['failed']:
                result['both_selected'] += 1
                result['maximum_both_selected_arrival_increase_seconds'] = max(
                    result['maximum_both_selected_arrival_increase_seconds'],
                    patient['estimated_arrival_seconds'] - prior['estimated_arrival_seconds'])
            identity = dict(parent_stage=c.get('parent_stage'), kind=c['kind'], seed=c['seed'],
                patient=pid, previous_selection_seconds=prior['selection_seconds'],
                selection_seconds=patient['selection_seconds'],
                previous_estimated_arrival_seconds=prior['estimated_arrival_seconds'],
                estimated_arrival_seconds=patient['estimated_arrival_seconds'],
                previous_destination=prior.get('destination'), destination=patient.get('destination'))
            if not prior['failed'] and patient['failed']:
                result['lost_selections'] += 1
                result['lost_identifiers'].append(identity)
            if prior['failed'] and not patient['failed']:
                result['new_selections'] += 1
                result['gained_identifiers'].append(identity)
    result['J_mean_delta_seconds'] = result['arrival_cost_sum_delta_seconds'] / result['patients'] if result['patients'] else None
    return result


def paired_lex_ci(compacts, before, *, replicates=10000, seed=20261021):
    assert type(replicates) is int and replicates > 0 and compacts
    rows = []
    for c in compacts:
        new, old = c['branches'][PRIMARY]['patients'], c['branches'][before]['patients']
        assert {p['id'] for p in new} == {p['id'] for p in old} and new
        rows.append((len(new), sum(p['failed'] for p in new) - sum(p['failed'] for p in old),
                     sum(wait(p) for p in new) - sum(wait(p) for p in old)))
    rng = random.Random(seed)
    failures, waits = [], []
    for _ in range(replicates):
        count = df = dw = 0
        for _ in rows:
            n, f, w = rng.choice(rows)
            count += n
            df += f
            dw += w
        failures.append(df / count)
        waits.append(dw / count)
    failures.sort()
    waits.sort()
    lo, hi = int(.025 * replicates), max(0, int(.975 * replicates) - 1)
    return dict(failed_count_delta=sum(x[1] for x in rows),
        wait_sum_delta_seconds=sum(x[2] for x in rows),
        ci95_failed_per_patient=[failures[lo], failures[hi]],
        ci95_wait_per_patient=[waits[lo], waits[hi]], replicates=replicates,
        bootstrap_seed=seed, cluster='scenario', all_priorities_default4=True,
        confidence_scope=CONFIDENCE_SCOPE)


def assess(metrics, origin_metrics, ci, stage):
    if stage == 'repair':
        checks = dict(both_observed_training_slices_present=set(origin_metrics) == {'repair', 'development'})
        for origin, cap, limit, strict in (('repair', 135, 113296, False), ('development', 51, 53498, True)):
            value = origin_metrics.get(origin, {}).get(PRIMARY)
            checks[origin + '_failure_goal'] = value is not None and value['failed'] <= cap
            checks[origin + '_wait_goal'] = value is not None and (
                value['wait_sum_seconds'] < limit if strict else value['wait_sum_seconds'] <= limit)
    elif stage in ('development', 'validation'):
        candidate = metrics[PRIMARY]
        checks = {}
        for before in (COHORT_SUCCESS, HEDGE):
            checks['failure_nonincrease_vs_' + before] = candidate['failed'] <= metrics[before]['failed']
            checks['wait_strict_decrease_vs_' + before] = candidate['wait_sum_seconds'] < metrics[before]['wait_sum_seconds']
        if stage == 'validation':
            for before in (COHORT_SUCCESS, HEDGE):
                item = ci.get(before)
                checks['failure_CI_nonincrease_vs_' + before] = item is not None and item['ci95_failed_per_patient'][1] <= 0
                checks['wait_CI_strict_decrease_vs_' + before] = item is not None and item['ci95_wait_per_patient'][1] < 0
    else:
        raise ValueError('unknown stage')
    return dict(passed=all(checks.values()), checks=checks, operational_adoption=False,
                objective='separate known-slice success and waiting goals; fresh CS/Hedge failure and wait conjunction')


def regression_outcomes(cases, targets=None):
    if targets is None:
        targets = control_ledger()['regressions_report_only']
    lookup = {(c['compact']['parent_stage'], c['compact']['kind'], c['compact']['seed'], p['id']): p
              for c in cases for p in c['compact']['branches'][PRIMARY]['patients']}
    result = {}
    for label, rows in targets.items():
        outcomes = []
        for target in rows:
            p = lookup[target['parent_stage'], target['kind'], target['seed'], target['patient']]
            outcomes.append(dict(**target, selected=not p['failed'],
                selection_seconds=p['selection_seconds'], estimated_arrival_seconds=p['estimated_arrival_seconds'],
                destination=p.get('destination')))
        result[label] = outcomes
    return result


def evaluate(cases, stage):
    names = [arm(*a) for a in ARMS]
    controls = names[:-1]
    all_compacts = [c['compact'] for c in cases]

    def scopes(compacts):
        return {'whole': compacts, 'mixed': [c for c in compacts if c['kind'] == 'mix'],
                'stress': [c for c in compacts if c['kind'] != 'mix']}

    metrics, pairs, origins, origin_pairs, families = {}, {}, {}, {}, {}
    if stage == 'repair':
        for origin in ('repair', 'development'):
            selected = [c for c in all_compacts if c['parent_stage'] == origin]
            if not selected:
                continue
            origins[origin] = {s: scope_metrics(v, names) for s, v in scopes(selected).items()}
            origin_pairs[origin] = {s: {n: paired(v, n) for n in controls} for s, v in scopes(selected).items()}
            families[origin] = {f: scope_metrics([c for c in selected if c['kind'] != 'mix' and c['family'] == f], names) for f in FAMILIES}
    else:
        metrics = {s: scope_metrics(v, names) for s, v in scopes(all_compacts).items()}
        pairs = {s: {n: paired(v, n) for n in controls} for s, v in scopes(all_compacts).items()}
        families = {f: scope_metrics([c for c in all_compacts if c['kind'] != 'mix' and c['family'] == f], names) for f in FAMILIES}
    ci = {name: paired_lex_ci(all_compacts, name) for name in (COHORT_SUCCESS, HEDGE)} if stage == 'validation' else {}
    return dict(metrics=metrics, origin_metrics=origins, paired=pairs, origin_paired=origin_pairs,
        whole_lex_ci=ci, family_metrics=families,
        regressions_report_only=regression_outcomes(cases) if stage == 'repair' else {},
        assessment=assess(metrics.get('whole', {}), {o: m['whole'] for o, m in origins.items()}, ci, stage),
        priority_performance_scope='all default4; no clinical tier-diversity claim',
        known_pooled_metrics_not_used_or_reported=True)


def semantic_path(stage):
    return BUILD / (stage + '-semantic-audit.json')


def verify_semantic_index(stage):
    value = read(semantic_path(stage))
    index = read(BUILD / (stage + '-raw-index.json'))
    assert value['passed'] and value['stage'] == stage and value['all_cases_audited'] is True
    assert value['source_sha256'] == source_hashes()
    assert value['raw_index_sha256'] == file_sha(BUILD / (stage + '-raw-index.json'))
    assert len(value['cases']) == len(index['cases']) == len(tasks(stage))
    assert value['semantic_audit_executions'] == len(index['cases'])
    assert value['ancestor_policy_audits_reexecuted'] == 0
    for proof, item in zip(value['cases'], index['cases']):
        assert all(proof[k] == item[k] for k in ('file', 'raw_sha256', 'compressed_sha256'))
        assert proof['audit']['passed'] and proof['audit']['new_executions'] == 0
    assert value['new_performance_runs'] == 0
    return value


def stage_cases(stage, *, allow_new_audit=False):
    """Collect new bytes and bound proofs; no ancestor decompression/re-audit."""
    if stage not in ORDER:
        raise ValueError('unknown stage')
    index_path = BUILD / (stage + '-raw-index.json')
    index = read(index_path)
    todo = tasks(stage)
    assert index['all_outcomes_saved'] is True and len(index['cases']) == len(todo)
    folder = BUILD / (stage + '-cases')
    assert {p.name for p in folder.iterdir()} == {x['file'] for x in index['cases']}
    manifest = [e for e in read(BUILD / 'input-manifest.json')['entries'] if e['stage'] == stage]
    assert len(manifest) == len(todo)
    existing = verify_semantic_index(stage) if semantic_path(stage).exists() else None
    if existing is None and not allow_new_audit:
        raise ValueError('a bound semantic audit is required before saved collection')
    if existing is None:
        from .cohort_recourse_audit import audit_replay
    sources = source_hashes()
    known = control_ledger()['entries'] if stage == 'repair' else None
    cases, proofs = [], []
    for position, ((kind, seed, arms), item, entry) in enumerate(zip(todo, index['cases'], manifest)):
        assert item['file'] == f'{position:03d}_{seed}.json.gz'
        assert (entry['kind'], entry['seed']) == (kind, seed)
        replay, body = _read_replay(folder / item['file'], item)
        assert replay['arms'] == [list(a) for a in arms] and len(replay['branches']) == len(arms)
        assert sha(raw(replay['scenario'])) == entry['sha256'] and replay['scenario']['horizon_seconds'] == 300
        expected = {p['id'] for p in replay['scenario']['patients']}
        assert len(expected) == len(replay['scenario']['patients'])
        branches = {}
        for pair, branch in zip(arms, replay['branches']):
            assert [branch['policy'], branch['rule']] == list(pair)
            assert branch['snapshot_sha256'] == replay['initial_snapshot_sha256']
            assert branch['environment_sha256'] == replay['environment_sha256']
            rows = patient_rows(branch['patients'])
            assert {p['id'] for p in rows} == expected
            branches[arm(*pair)] = dict(patients=rows)
        parent_stage = None
        original_origin = None
        if known is not None:
            reference = known[position]
            parent_stage = reference['parent_stage']
            original_origin = reference['original_origin']
            assert entry['parent_stage'] == parent_stage
            assert (kind, seed, entry['sha256']) == (reference['kind'], reference['seed'], reference['scenario_sha256'])
            assert replay['initial_snapshot_sha256'] == reference['initial_snapshot_sha256']
            assert replay['environment_sha256'] == reference['environment_sha256']
            assert all({p['id'] for p in v['patients']} == expected for v in reference['branches'].values())
            branches = dict(deepcopy(reference['branches']), **branches)
        else:
            assert entry['parent_stage'] is None
        assert set(branches) == {arm(*a) for a in ARMS}
        if existing is None:
            proof = audit_replay(replay)
            assert proof['passed'] and proof['new_executions'] == 0 and raw(replay) == body
            proofs.append(dict(**item, audit=proof))
        else:
            proof = existing['cases'][position]['audit']
        compact = dict(kind=kind, seed=seed, family=replay['scenario']['family'],
            scenario_id=replay['scenario']['id'], parent_stage=parent_stage,
            original_origin=original_origin, branches=branches)
        cases.append(dict(**item, parent_stage=parent_stage, original_origin=original_origin,
                          audit=proof, compact=compact))
    assert source_hashes() == sources
    if existing is None:
        write_new(semantic_path(stage), raw(dict(passed=True, stage=stage, all_cases_audited=True,
            raw_index_sha256=file_sha(index_path), source_sha256=sources, cases=proofs,
            new_performance_runs=0, semantic_audit_executions=len(proofs),
            ancestor_policy_audits_reexecuted=0)))
    return cases


def prior_evidence(stage):
    if stage == 'repair':
        return {}
    if stage not in ORDER:
        raise ValueError('unknown stage')
    previous = 'repair' if stage == 'development' else 'development'
    return {previous + suffix: file_sha(BUILD / (previous + suffix))
            for suffix in ('.json', '-audit.json', '-lock.json', '-raw-index.json', '-semantic-audit.json')}


def verify_prior(stage):
    if stage == 'repair':
        return {}
    if stage not in ORDER:
        raise ValueError('unknown stage')
    previous = 'repair' if stage == 'development' else 'development'
    report = read(BUILD / (previous + '.json'))
    audit = read(BUILD / (previous + '-audit.json'))
    lock = read(BUILD / (previous + '-lock.json'))
    assert report['assessment']['passed'] and audit['passed'] and lock['next_stage_allowed'] is True
    assert file_sha(BUILD / (previous + '.json')) == audit['report_sha256'] == lock['report_sha256']
    assert lock['candidate'] == PRIMARY and lock['assessment'] == report['assessment']
    assert lock['protocol'] == report['protocol'] == protocol() and audit['source_sha256'] == source_hashes()
    assert audit['semantic_audit_sha256'] == report['semantic_audit_sha256'] == file_sha(semantic_path(previous))
    verify_semantic_index(previous)
    assert report['prior_stage_evidence_sha256'] == prior_evidence(previous)
    for name, digest in report['prior_stage_evidence_sha256'].items():
        assert file_sha(BUILD / name) == digest
    return prior_evidence(stage)


def collect_saved(stage, elapsed_seconds=None):
    """Accounting recovery may inspect saved outcomes, never invoke the runner."""
    collection_started = time.perf_counter()
    if stage not in ORDER:
        raise ValueError('unknown stage')
    frozen = verify_freeze()
    prior = verify_prior(stage)
    assert not (BUILD / (stage + '.json')).exists()
    assert not (BUILD / (stage + '-lock.json')).exists()
    cases = stage_cases(stage, allow_new_audit=True)
    result = evaluate(cases, stage)
    report = dict(schema_version=1, experiment=EXPERIMENT, stage=stage, protocol=frozen['protocol'],
        elapsed_seconds=None if elapsed_seconds is None else elapsed_seconds + time.perf_counter() - collection_started,
        elapsed_scope='complete simulation and collection when elapsed is supplied; unretained original duration is null on saved-only recovery',
        cases=cases, artifact_directory=str(BUILD / (stage + '-cases')),
        new_branches=len(cases) * (1 if stage == 'repair' else 14),
        cached_branches=4680 if stage == 'repair' else 0,
        semantic_audit_sha256=file_sha(semantic_path(stage)),
        prior_stage_evidence_sha256=prior, operational_adoption=False, **result)
    assert protocol() == frozen['protocol'] and prior == prior_evidence(stage)
    write_new(BUILD / (stage + '.json'), raw(report))
    if stage != 'validation':
        write_new(BUILD / (stage + '-lock.json'), raw(dict(candidate=PRIMARY,
            protocol=frozen['protocol'], report_sha256=sha(raw(report)),
            assessment=report['assessment'], next_stage_allowed=report['assessment']['passed'])))
    return report


def execute(stage, workers=4):
    if type(workers) is not int or workers <= 0:
        raise ValueError('workers must be a positive integer')
    if stage not in ORDER:
        raise ValueError('unknown stage')
    verify_freeze()
    verify_prior(stage)  # Before mkdir, executor creation, or runner import.
    for suffix in ('.json', '-raw-index.json', '-audit.json', '-lock.json', '-cases', '-semantic-audit.json'):
        assert not (BUILD / (stage + suffix)).exists()
    from . import cohort_recourse_runner
    todo = tasks(stage)
    folder = BUILD / (stage + '-cases')
    folder.mkdir()
    items = []
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for index, replay in enumerate(pool.map(cohort_recourse_runner.run_task, todo, chunksize=1)):
            assert index < len(todo)
            body = raw(replay)
            packed = gzip.compress(body, compresslevel=6, mtime=0)
            name = f'{index:03d}_{todo[index][1]}.json.gz'
            write_new(folder / name, packed)
            items.append(dict(file=name, raw_sha256=sha(body), compressed_sha256=sha(packed)))
            if len(items) % 8 == 0:
                print(f'{stage}: saved {len(items)}/{len(todo)}', flush=True)
    assert len(items) == len(todo)
    write_new(BUILD / (stage + '-raw-index.json'), raw(dict(cases=items, all_outcomes_saved=True)))
    # All outcomes exist before the first semantic audit or performance gate.
    report = collect_saved(stage, elapsed_seconds=time.perf_counter() - started)
    print(dict(stage=stage, assessment=report['assessment']), flush=True)


def audit_saved(stage):
    verify_freeze()
    prior = verify_prior(stage)
    report = read(BUILD / (stage + '.json'))
    assert report['protocol'] == protocol() and report['stage'] == stage and report['experiment'] == EXPERIMENT
    assert report['operational_adoption'] is False
    assert report['artifact_directory'] == str(BUILD / (stage + '-cases'))
    assert report['prior_stage_evidence_sha256'] == prior
    verify_semantic_index(stage)
    cases = stage_cases(stage)
    assert cases == report['cases']
    assert report['new_branches'] == len(cases) * (1 if stage == 'repair' else 14)
    assert report['cached_branches'] == (4680 if stage == 'repair' else 0)
    assert report['semantic_audit_sha256'] == file_sha(semantic_path(stage))
    for key, value in evaluate(cases, stage).items():
        assert report[key] == value, key
    value = dict(passed=True, report_sha256=file_sha(BUILD / (stage + '.json')),
        semantic_audit_sha256=file_sha(semantic_path(stage)), source_sha256=source_hashes(),
        cases=len(cases), new_branches_checked=report['new_branches'],
        cached_control_branches_bound=report['cached_branches'], new_performance_runs=0,
        semantic_audits_reexecuted=0, ancestor_policy_audits_reexecuted=0,
        scope='stored new raw integrity and patient accounting; semantic policy/FIFO proof reused by exact raw and frozen source digest; cached parent controls rely on their sealed evidence')
    write_new(BUILD / (stage + '-audit.json'), raw(value))
    print(dict(audit=stage, passed=True, cases=len(cases)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare-cache', 'freeze', *ORDER,
        'audit-repair', 'audit-development', 'audit-validation'))
    parser.add_argument('--workers', type=int, default=4)
    arguments = parser.parse_args()
    if arguments.stage == 'prepare-cache':
        prepare_cache()
    elif arguments.stage == 'freeze':
        freeze()
    elif arguments.stage.startswith('audit-'):
        audit_saved(arguments.stage.removeprefix('audit-'))
    else:
        execute(arguments.stage, arguments.workers)
