"""One prospective public-initial review-slot matching candidate; immutable controls and audits.

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
BUILD = ROOT / '.codex-build/eat-cohort-startslots-20261025'
PARENT = ROOT / '.codex-build/eat-cohort-tailwait-20261025'
EXPERIMENT = 'eat_cohort_startslots_20261025'
NAMESPACE = 'eat-cohort-startslots-v1-20261025-b4e5157a-4e93-4cd0-ae54-f84d54fe3143'
EXPECTED_BEFORE = 'f7b44419588d39dd87dc7d30bb5316872a6983a58cfc8163e0b9b973338970c8'
EXPECTED_PARENT = '3158907de3612918a685a18dab25ddf4559f36710b0da906121fae9d765946e7'
TAILWAIT_BEFORE = '44b09666fd653d746d2fbf5eeeff01ce672c39c94f4311dee2e70752b5a5bade'
PAIRSHIELD_BEFORE = '68d94ae99759dc113b04f1c0074f93fbff0350d0a69aff9f7cb64640d0bc5e58'
PAIRSCOPE_BEFORE = 'bc8c93a3fbfdf27414bb32bdfe8f7cbdbdf32c9277da8774793f1c3821ea52f2'
ENVELOPE_BEFORE = '45e15d59e11c2637744bd7245ceef132f55d4d1ab3c962464e952e5c17db0f77'
WAITPARETO_BEFORE = '496e91aae3b532517d588df8b471e7c644474ef75d9d093644378f523d0aa0ae'
PARETO_BEFORE = 'e688d1c8ba96eb41879933bbac72e50c1e8097da6ce85fcb44cd60195ae8d5be'
FULLMENU_BEFORE = '3f5246d7f1c759e07be41fbd1c71ef4139d0390b0409eeaa50ed17334865b53d'
STOPLOSS_BEFORE = 'ae93423254b498184fb0eb69cc751a3d041453a403458c4448207b6d849cff68'
ADVANCE_BEFORE = '3327a8b7331e0a8ff873555c76b725a662fb106d0f8354f8f2a9859b7c660175'
CONTENTION_BEFORE = '7d50f3b595cd3ca3c5f4f523ca963af4e82945b639f7489d49a98c8b8a5ecf61'
INITIAL_BEFORE = '4ffc669bf53230549903b048ac6f4f074cee7b5cc07b9083e6fc757fe402fd22'
INDEX_BEFORE = '4c47c94bc3bdbaed2423f189fa1da3953e6b42c063000105f3febb6d919dd774'
INDEX_AFTER = 'd9e28990af5dadd6b9e8577904729cfbaeb4f53ade5faacecbfe94ed9ec093f2'
NAME = 'eat_cohort_startslots_l15_f'
PRIMARY = NAME + '|immediate'
HEDGE = 'eat_cohort_hedge_l15_f|immediate'
COHORT_SUCCESS = 'eat_cohort_success_l15_f|immediate'
RECOURSE = 'eat_cohort_recourse_l15_f|immediate'
FOLLOWPLAN = 'eat_cohort_followplan_l15_f|immediate'
CONTENTION = 'eat_cohort_contention_l15_f|immediate'
ADVANCE = 'eat_cohort_advance_l15_f|immediate'
STOPLOSS = 'eat_cohort_stoploss_l15_f|immediate'
FULLMENU = 'eat_cohort_fullmenu_l15_f|immediate'
PARETO = 'eat_cohort_pareto_l15_f|immediate'
WAITPARETO = 'eat_cohort_waitpareto_l15_f|immediate'
ENVELOPE = 'eat_cohort_envelope_l15_f|immediate'
PAIRSCOPE = 'eat_cohort_pairscope_l15_f|immediate'
PAIRSHIELD = 'eat_cohort_pairshield_l15_f|immediate'
TAILWAIT = 'eat_cohort_tailwait_l15_f|immediate'
CONTROLS = (('fixed_3', 'immediate'), ('eat_l15_f', 'immediate'),
    ('eat_residual_l15_f', 'immediate'), ('eat_horizon_l15_f', 'immediate'),
    ('eat_joint_first_l15_f', 'immediate'), ('eat_joint_tail_l15_f', 'immediate'),
    ('eat_joint_reselect_l15_f', 'immediate'), ('eat_joint_budget_l15_f', 'immediate'),
    ('v3_w45_h900', 'immediate'), ('eat_l15_f', 'hold120'),
    ('eat_cohort_success_l15_f', 'immediate'), ('eat_cohort_dynamic_l15_f', 'immediate'),
    ('eat_cohort_hedge_l15_f', 'immediate'), ('eat_cohort_recourse_l15_f', 'immediate'), ('eat_cohort_followplan_l15_f', 'immediate'), ('eat_cohort_contention_l15_f', 'immediate'), ('eat_cohort_advance_l15_f', 'immediate'), ('eat_cohort_stoploss_l15_f', 'immediate'), ('eat_cohort_fullmenu_l15_f', 'immediate'), ('eat_cohort_pareto_l15_f', 'immediate'), ('eat_cohort_waitpareto_l15_f', 'immediate'), ('eat_cohort_envelope_l15_f', 'immediate'), ('eat_cohort_pairscope_l15_f', 'immediate'), ('eat_cohort_pairshield_l15_f', 'immediate'), ('eat_cohort_tailwait_l15_f', 'immediate'))
ARMS = CONTROLS + ((NAME, 'immediate'),)
REPAIR_ARMS = ((NAME, 'immediate'),)
ORDER = ('repair', 'development', 'validation')
FIXTURE = 'simulation/fixtures/eat_intent_20261020/mix7.json.gz'
WORKING_REFERENCE = 'docs/EAT_INQUIRY_EXPERIMENT.md'
LEDGER_FILE = 'preservation/control-ledger.json'
CONFIDENCE_SCOPE = 'marginal endpoint-wise95; not joint or familywise-adjusted'
HISTORICAL_REFERENCE_CASE_IDS = tuple(sorted(
    ['test_inquiry_lab_joint_' + variant + '_experiment.' + cls + '.test_json_protocol_and_preserved_parent'
     for variant, cls in (('first', 'JointFirstExperimentTests'), ('tail', 'JointTailExperimentTests'),
                          ('reselect', 'JointReselectExperimentTests'), ('budget', 'JointBudgetExperimentTests'))]
    + ['test_inquiry_lab_cohort_' + variant + '_experiment.' + cls + '.' + method
       for variant, cls in (('success', 'CohortSuccessExperimentTests'), ('dynamic', 'CohortDynamicExperimentTests'),
                            ('hedge', 'CohortHedgeExperimentTests'))
       for method in ('test_exact_working_doc_exclusion_leaves_every_other_source_and_ref_strict',
                      'test_protocol_bounds_default_priority_and_report_only_regressions')]))

FAMILIES = ('immediate_all_accept', 'delayed_near_fast_far', 'no_response_near',
    'simultaneous_w1', 'staggered_w2', 'all_unavailable_control',
    'historical_shift', 'history_helpful')
# Importing this module performs no preparation, seed generation, or execution.
NEW_SOURCES = tuple('inquiry_lab/cohort_startslots_' + name + '.py'
    for name in ('policy', 'context', 'model', 'runner', 'audit', 'experiment')) + tuple(
    'test_inquiry_lab_cohort_startslots_' + name + '.py'
    for name in ('policy', 'context', 'model', 'runner', 'audit', 'experiment')) + tuple(
    '.codex-build/eat-cohort-startslots-20261025/' + name + '.py'
    for name in ('preserve_parent', 'run_checks', 'preflight_equivalence', 'finalize_phase'))
SETTINGS = dict(candidate_count=1, lambda_seconds=15,
    changed_factor='only public initial roster4..16 at common started_at with global pending empty: bounded review-slot mincost matching covers one contact per owner using original assumed_workers, then positive benefit extras within original immediate count and caps4/10; emit current two only; native FIFO ordering and all other Tailwait paths retained',
    initial_cohort_review_slot_matching=True,
    exhaustive_feasible_current_rows=False,
    per_owner_predicted_probability_nonregression=False,
    tail_cohort_owner_wait_nonregression=False,
    hard_zero_first_wave_membership_guard=True,
    no_tail_first_pair_evaluation=True,
    expected_future_queue_envelope=True,
    protected_wait_first_selection=False,
    parent_guard_flags_scope='copied paths or embedded Tailwait reference only; not new slot path',
    current_slot_path_original_JOINT_domain=False,
    current_slot_path_original_probability_wait_guards=False,
    protected_selection_order='copied parent paths only; new slot path uses coverage matching and positive marginal extras',
    full_menu_activation_scope='initial public roster4..16 same started_at/global pending empty gets explicit new slot-matching countsets outside old JOINT/Pguards; original Tailwait proposal retained as reference; all other paths copied',
    owner_probability_guard_scope='copied paths and embedded Tailwait reference only retain all owner guards; new slot path uses a separate count domain; actual success identities and outcomes excluded from policy input',
    objective='preserve every parent Stoploss success identity and strictly lower capped selection wait per slice; retain existing success/wait gates; all benchmark priorities unknown/default4',
    known_slices='parent repair240/827 and parent development120/415 are separate training slices; no pooled success/wait gate',
    known_repair_gate='parent-repair827 failed<=135 and wait<=113296',
    known_development_gate='parent-development415 failed<=51 and wait<53498',
    individual_success_identity_preservation_is_hard_gate=True,
    parent_success_identity_baseline=STOPLOSS,
    parent_success_identity_scope='each observed training slice separately and each fresh stage; accounting-only identities never passed to policy',
    parent_wait_gate='strict lower total capped selection wait than Stoploss separately per training slice and each fresh stage',
    best_control_or_pooled_lexicographic_gate=False,
    fresh_gate='zero lost Stoploss success identities; failed<=Stoploss,CS,Hedge; wait<Stoploss,CS,Hedge',
    validation_gate='each Stoploss/CS/Hedge upper95 deltafail<=0 AND upper95 deltawait<0; all six conditions plus zero lost Stoploss success identities',
    confidence_scope=CONFIDENCE_SCOPE, bootstrap_replicates=10000, bootstrap_seed=20261021,
    cluster_scope='scenario; each stress role seed is shared across eight families, not seed-block or familywise inference',
    priority_scope='all frozen lab payloads unknown/None/effective4; expert-tier adapter units are not clinical efficacy evidence',
    repair_environments=360, repair_patients=1242, repair_new_branches=360,
    repair_cached_branches=9000, parent_owned_raws=360, ancestor_raws=5640,
    development_environments=120, validation_environments=368, fresh_arms=26,
    maximum_new_branches=13048, manifest_entries=848,
    arrival_scope='recorded elapsed selection+travel; new separate all-patient capped arrival nonregression gate versus both Tailwait and Pairshield; failure1800; both-selected arrival changes separately reported; never subtract created_at again',
    cached_audit_scope='sealed Tailwait training compacts plus inherited24-control ledger; Tailwait own360 raw headers/outcomes checked once; old24 controls rely on sealed ancestor evidence',
    new_semantic_audit='parallel independent saved-raw audits; one durable xb proof per raw/source digest, reused on resume; later checks never rerun valid proofs',
    semantic_audit_workers=4,
    fresh_role_seeds=425, fresh_seed_reuse=False, generated_unexecuted_prior_roles_are_reserved=True,
    no_result_exclusions=True, all_stage_raw_saved_before_assessment=True,
    fixed_actual_contact_cap=10, fixed_actual_concurrent_cap=4,
    A_H_offers_atomicity_unchanged=True, oracle_input_to_policy=False,
    heldout_retuning=False, operational_adoption=False, live_server_changed=False)
SETTINGS.update(retention_assessment_separate_from_original_promotion=True,
    retention_success_baselines=[STOPLOSS, PAIRSHIELD],
    retention_wait_baseline=PAIRSHIELD, retention_personal_delay_baseline=STOPLOSS,
    known_retained_wait_seconds=dict(repair=113405, development=54569),
    known_personal_maximum_delay_seconds=dict(repair=1, development=0),
    fresh_retention_rule='zero lost Stoploss and Pairshield successes; total wait no greater than fresh Pairshield; maximum positive wait delta versus Stoploss strictly smaller than Pairshield if its maximum is positive, otherwise zero',
    retention_scope='separate observed training slices or whole fresh stage; all matched patients with failed waiting capped at300; outcome identities are accounting only',
    stage_advancement_requires_original_and_retention=True)
SETTINGS.update(pareto_retention_assessment_separate_from_prior_gates=True,
    pareto_success_baselines=[STOPLOSS, PAIRSHIELD, TAILWAIT],
    pareto_wait_baseline=TAILWAIT,
    pareto_individual_baselines=[TAILWAIT, PAIRSHIELD],
    pareto_known_wait_caps_seconds=dict(repair=113405, development=54557),
    pareto_maximum_wait_increase_seconds=0,
    pareto_maximum_arrival_increase_seconds=0,
    pareto_failure_wait_seconds=300, pareto_failure_arrival_seconds=1800,
    pareto_scope='each observed training slice separately or whole fresh stage; every matched patient, including failures; both-selected arrivals separately reported',
    stage_advancement_requires_original_retention_and_pareto=True)


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


def verify_reference_rebinding(before):
    """Check the already sealed historical index edit; perform no new rebind."""
    immediate = before['immediate_parent_preservation']
    immediate_path = ROOT / immediate['path']
    assert file_sha(immediate_path) == immediate['sha256']
    assert before['strict_reference_sha256'].get(immediate['path']) == immediate['sha256']
    immediate_before = read(immediate_path)
    assert before['inherited_reference_drift_provenance'] == immediate_before['inherited_reference_drift_provenance']
    assert before['non_executable_reference_drift'] == immediate_before['non_executable_reference_drift']
    assert all(before['strict_reference_sha256'].get(n) == d for n, d in immediate_before['strict_reference_sha256'].items())
    assert all(before['parent_sources'].get(n) == d for n, d in immediate_before['parent_sources'].items())
    assert all(before['immutable_raw_bindings'].get(n) == v for n, v in immediate_before['immutable_raw_bindings'].items())
    provenance = before['inherited_reference_drift_provenance']
    parent_path = ROOT / provenance['parent_before_path']
    initial_path = ROOT / provenance['initial_preservation_path']
    assert file_sha(parent_path) == provenance['parent_before_sha256']
    assert file_sha(initial_path) == provenance['initial_preservation_sha256'] == INITIAL_BEFORE
    previous, initial = read(parent_path), read(initial_path)
    assert previous['initial_preservation_sha256'] == INITIAL_BEFORE
    assert previous['parent_sources'] == initial['parent_sources']
    assert previous['immutable_raw_bindings'] == initial['immutable_raw_bindings']
    original, current = initial['strict_reference_sha256'], previous['strict_reference_sha256']
    assert set(original) == set(current)
    assert {name for name in original if original[name] != current[name]} == {'docs/README.md'}
    drift = before['non_executable_reference_drift']
    assert drift == previous['non_executable_reference_drift']
    assert drift['path'] == 'docs/README.md' and drift['path'] not in before['parent_sources']
    assert drift['sealed_reference_sha256'] == original['docs/README.md'] == INDEX_BEFORE
    assert drift['current_sha256'] == current['docs/README.md'] == INDEX_AFTER
    assert type(drift['reason']) is str and drift['reason']
    assert all(before['strict_reference_sha256'].get(name) == digest for name, digest in current.items())
    assert before['strict_reference_sha256'].get(provenance['parent_before_path']) == provenance['parent_before_sha256']
    assert before['strict_reference_sha256'].get(provenance['initial_preservation_path']) == INITIAL_BEFORE
    assert all(before['parent_sources'].get(name) == digest for name, digest in previous['parent_sources'].items())
    assert all(before['immutable_raw_bindings'].get(name) == value for name, value in previous['immutable_raw_bindings'].items())


def verify_old(check_raw=False):
    path = BUILD / 'preservation/before.json'
    assert file_sha(path) == EXPECTED_BEFORE
    before = read(path)
    verify_reference_rebinding(before)
    expected_chain=(
        ('.codex-build/eat-cohort-tailwait-20261025/preservation/before.json',TAILWAIT_BEFORE,618,5640,4522),
        ('.codex-build/eat-cohort-pairshield-20261025/preservation/before.json',PAIRSHIELD_BEFORE,602,5280,4114),
        ('.codex-build/eat-cohort-pairscope-20261025/preservation/before.json',PAIRSCOPE_BEFORE,586,4920,3704),
        ('.codex-build/eat-cohort-envelope-20261025/preservation/before.json',ENVELOPE_BEFORE,570,4560,3290),
        ('.codex-build/eat-cohort-waitpareto-20261024/preservation/before.json',WAITPARETO_BEFORE,554,4200,2885),
        ('.codex-build/eat-cohort-pareto-20261024/preservation/before.json',PARETO_BEFORE,538,3840,2480),
        ('.codex-build/eat-cohort-fullmenu-20261024/preservation/before.json',FULLMENU_BEFORE,522,3480,2071),
        ('.codex-build/eat-cohort-stoploss-20261024/preservation/before.json',STOPLOSS_BEFORE,506,3120,1666),
        ('.codex-build/eat-cohort-advance-20261022/preservation/before.json',ADVANCE_BEFORE,490,2760,1263),
        ('.codex-build/eat-cohort-contention-20261022/preservation/before.json',CONTENTION_BEFORE,474,2400,859))
    link=before['immediate_parent_preservation']
    for index,(name,digest,source_count,raw_count,reference_count) in enumerate(expected_chain):
        assert link==dict(path=name,sha256=digest) and file_sha(ROOT/name)==digest
        ancestor=read(ROOT/name)
        assert ancestor['source_count']==len(ancestor['parent_sources'])==source_count
        assert ancestor['strict_reference_count']==len(ancestor['strict_reference_sha256'])==reference_count
        assert len(ancestor['immutable_raw_bindings'])==raw_count
        assert ancestor['inherited_reference_drift_provenance']==before['inherited_reference_drift_provenance']
        assert ancestor['non_executable_reference_drift']==before['non_executable_reference_drift']
        for key in ('parent_sources','strict_reference_sha256','immutable_raw_bindings'):
            assert all(before[key].get(n)==v for n,v in ancestor[key].items())
        if index+1<len(expected_chain):link=ancestor['immediate_parent_preservation']
    assert before['passed'] and before['source_count'] == len(before['parent_sources']) == 634
    assert before['observed_environments'] == 360 and before['observed_patients'] == 1242
    assert before['all_observed_now_training'] is True
    assert before['new_performance_runs'] == 0 and before['operational_adoption'] is False
    assert before['parent_composite_sha256'] == EXPECTED_PARENT
    assert before['inherited_excluded_working_reference']['path'] == WORKING_REFERENCE
    references = before['strict_reference_sha256']
    assert len(references) == before['strict_reference_count'] and WORKING_REFERENCE not in references
    mapping = dict(references)
    for name, digest in before['parent_sources'].items():
        assert name != WORKING_REFERENCE and references.get(name) == digest
        mapping[name] = digest
    for name, digest in mapping.items():
        assert file_sha(ROOT / name) == digest, name
    assert file_sha(ROOT / before['parent_composite_path']) == EXPECTED_PARENT
    bindings = before['immutable_raw_bindings']
    assert len(bindings) == 6000
    if check_raw:
        for name, value in bindings.items():
            path = ROOT / name
            assert path.stat().st_size == value['compressed_bytes']
            assert file_sha(path) == value['compressed_sha256'], name
    return file_sha(BUILD / 'preservation/before.json')


def source_hashes():
    old = read(BUILD / 'preservation/before.json')['parent_sources']
    assert len(old) == 634 and len(NEW_SOURCES) == len(set(NEW_SOURCES)) == 16
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
    """Preserve nine already declared report groups; no new identity gate."""
    lookup = {(entry['parent_stage'], entry['kind'], entry['seed']): entry for entry in entries}
    answer = deepcopy(inherited)
    for targets in answer.values():
        for target in targets:
            key = (target['parent_stage'], target['kind'], target['seed'])
            assert key in lookup
            entry = lookup[key]
            assert target['original_origin'] == entry['original_origin']
            assert target['patient'] in {p['id'] for p in entry['branches'][HEDGE]['patients']}
    return answer


def prepare_cache():
    """Check own-parent360 bytes once; reuse old24 sealed accounting and proofs."""
    verify_old()
    before = read(BUILD / 'preservation/before.json')
    prior = read(PARENT / LEDGER_FILE)
    assert prior['passed'] and prior['environments'] == len(prior['entries']) == 360
    assert prior['patients'] == 1242 and prior['cached_branches'] == 8640
    assert prior['controls'] == [list(a) for a in CONTROLS[:-1]]
    manifest = read(PARENT / 'input-manifest.json')
    inputs = {(e['stage'], e['kind'], e['seed']): e['sha256'] for e in manifest['entries']}
    report = read(PARENT / 'repair.json')
    audit = read(PARENT / 'repair-audit.json')
    assert audit['passed'] and audit['report_sha256'] == file_sha(PARENT / 'repair.json')
    assert len(report['cases']) == 360
    assert report['new_branches'] == 360 and report['cached_branches'] == 8640
    for origin in ('repair', 'development'):
        assert file_sha(PARENT / 'repair.json') == before['observed_stages'][origin]['report_sha256']
    entries = []
    control_names = {arm(*a) for a in CONTROLS}
    for index, (case, inherited) in enumerate(zip(report['cases'], prior['entries'])):
        compact = case['compact']
        parent_stage, kind, seed = compact['parent_stage'], compact['kind'], compact['seed']
        assert (parent_stage, kind, seed) == (inherited['parent_stage'], inherited['kind'], inherited['seed'])
        assert compact['original_origin'] == inherited['original_origin']
        assert case['file'] == f'{index:03d}_{seed}.json.gz'
        path = PARENT / 'repair-cases' / case['file']
        relative = path.relative_to(ROOT).as_posix()
        bound = before['immutable_raw_bindings'][relative]
        assert all(case[k] == bound[k] for k in ('raw_sha256', 'compressed_sha256'))
        replay, _ = _read_replay(path, case)
        assert sha(raw(replay['scenario'])) == inputs['repair', kind, seed] == inherited['scenario_sha256']
        assert replay['scenario']['seed'] == seed and replay['scenario']['horizon_seconds'] == 300
        assert compact['family'] == inherited['family'] and compact['scenario_id'] == inherited['scenario_id']
        assert set(compact['branches']) == control_names
        branches = {name: dict(patients=patient_rows(compact['branches'][name]['patients']))
                    for name in sorted(control_names)}
        for name in control_names - {TAILWAIT}:
            assert branches[name]['patients'] == patient_rows(inherited['branches'][name]['patients'])
        expected_ids = {p['id'] for p in replay['scenario']['patients']}
        assert len(expected_ids) == len(replay['scenario']['patients']) == inherited['patient_count']
        assert all({p['id'] for p in value['patients']} == expected_ids for value in branches.values())
        expected_arms = [[TAILWAIT.split('|')[0], 'immediate']]
        assert replay['arms'] == expected_arms and len(replay['branches']) == 1
        branch = replay['branches'][0]
        assert [branch['policy'], branch['rule']] == expected_arms[0]
        assert branch['snapshot_sha256'] == replay['initial_snapshot_sha256'] == inherited['initial_snapshot_sha256']
        assert branch['environment_sha256'] == replay['environment_sha256'] == inherited['environment_sha256']
        assert patient_rows(branch['patients']) == branches[TAILWAIT]['patients']
        entries.append(dict(parent_stage=parent_stage, original_origin=compact['original_origin'],
            kind=kind, seed=seed, family=compact['family'], scenario_id=compact['scenario_id'],
            scenario_sha256=inputs['repair', kind, seed], patient_count=len(expected_ids),
            initial_snapshot_sha256=replay['initial_snapshot_sha256'],
            environment_sha256=replay['environment_sha256'], parent_raw_path=relative,
            raw_sha256=case['raw_sha256'], compressed_sha256=case['compressed_sha256'], branches=branches))
    assert len(entries) == 360
    for stage, expected in (('repair', 827), ('development', 415)):
        assert sum(entry['patient_count'] for entry in entries if entry['parent_stage'] == stage) == expected
    keys = [(entry['parent_stage'], entry['kind'], entry['seed']) for entry in entries]
    assert len(set(keys)) == len(keys)
    value = dict(schema_version=1, passed=True, parent_binding_sha256=EXPECTED_BEFORE,
        parent_composite_sha256=EXPECTED_PARENT, controls=[list(a) for a in CONTROLS],
        environments=360, patients=1242, cached_branches=9000, own_parent_raw_objects_checked=360,
        ancestor_policy_audits_reexecuted=0, policy_model_oracle_executions=0,
        evidence_scope=SETTINGS['cached_audit_scope'], entries=entries,
        regressions_report_only=tracked_targets(entries, prior['regressions_report_only']))
    write_new(BUILD / LEDGER_FILE, raw(value))
    return value


def control_ledger():
    value = read(BUILD / LEDGER_FILE)
    assert value['passed'] and value['parent_binding_sha256'] == EXPECTED_BEFORE
    assert value['parent_composite_sha256'] == EXPECTED_PARENT
    assert value['controls'] == [list(a) for a in CONTROLS]
    assert value['environments'] == len(value['entries']) == 360
    assert value['patients'] == 1242 and value['cached_branches'] == 9000
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


def verify_tests_for_freeze(tests):
    """Keep ten historical README assertions failed; never classify them as passes."""
    assert tests['errors'] == tests['skipped'] == 0
    assert tests['source_sha256_before'] == tests['source_sha256_after']
    details = tests.get('failure_details', [])
    if tests['passed'] is True:
        assert tests['failures'] == 0 and not details
        return
    assert tests['passed'] is False and tests['failures'] == 10
    assert type(details) is list and len(details) == 10
    assert sorted(row['test_id'] for row in details) == list(HISTORICAL_REFERENCE_CASE_IDS)
    for row in details:
        trace = row['traceback']
        assert type(trace) is str and 'verify_old' in trace
        assert trace.rstrip().endswith('AssertionError: docs/README.md')
    q = tests['historical_reference_qualification']
    assert tests['eligible_for_candidate_freeze'] is q['eligible'] is True
    assert q['path'] == 'docs/README.md' and q['sealed_sha256'] == INDEX_BEFORE
    assert q['current_sha256'] == q['current_before_sha256'] == q['current_after_sha256'] == INDEX_AFTER
    assert file_sha(ROOT / q['path']) == INDEX_AFTER
    assert q['case_ids'] == list(HISTORICAL_REFERENCE_CASE_IDS)
    assert q['assertions_unchanged'] is q['failures_retained'] is True


def freeze():
    if not (BUILD / LEDGER_FILE).exists():
        prepare_cache()
    p = protocol()
    tests = read(BUILD / 'tests.json')
    verify_tests_for_freeze(tests)
    assert tests['source_sha256_before'] == tests['source_sha256_after'] == p['source_sha256']
    for name in ('policy', 'context', 'model', 'runner', 'audit', 'experiment'):
        assert tests['module_counts']['test_inquiry_lab_cohort_startslots_' + name] > 0
    preflight = read(BUILD / 'preflight/parent-equivalence.json')
    assert preflight['passed'] and preflight['full_raw_equal']
    assert preflight['controls'] == [list(a) for a in CONTROLS]
    assert preflight['formal_performance_runs'] == 0 and preflight['used_to_tune_policy_or_gates'] is False
    assert preflight['source_sha256_before'] == preflight['source_sha256_after'] == p['source_sha256']
    candidate_checks = preflight['candidate_checks']
    assert len(candidate_checks) == 3 and {x['label'] for x in candidate_checks} == {'saved_seed7', 'family_seed7', 'matching_seed7'}
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
    names = ['preservation/before.json', LEDGER_FILE, 'tests.json', 'tests-output.txt', 'source-freeze.json',
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


def assess(metrics, origin_metrics, ci, stage, *, paired_metrics=None, origin_paired_metrics=None):
    if stage == 'repair':
        checks = dict(both_observed_training_slices_present=set(origin_metrics) == {'repair', 'development'})
        for origin, cap, limit, strict in (('repair', 135, 113296, False), ('development', 51, 53498, True)):
            value = origin_metrics.get(origin, {}).get(PRIMARY)
            checks[origin + '_failure_goal'] = value is not None and value['failed'] <= cap
            checks[origin + '_wait_goal'] = value is not None and (
                value['wait_sum_seconds'] < limit if strict else value['wait_sum_seconds'] <= limit)
            parent = origin_metrics.get(origin, {}).get(STOPLOSS)
            comparison = (origin_paired_metrics or {}).get(origin, {}).get(STOPLOSS)
            checks[origin + '_parent_stoploss_success_identities_preserved'] = (
                comparison is not None and comparison['lost_selections'] == 0)
            checks[origin + '_wait_strict_decrease_vs_parent_stoploss'] = (
                value is not None and parent is not None and value['wait_sum_seconds'] < parent['wait_sum_seconds'])
    elif stage in ('development', 'validation'):
        candidate = metrics[PRIMARY]
        checks = {}
        for before in (COHORT_SUCCESS, HEDGE, STOPLOSS):
            checks['failure_nonincrease_vs_' + before] = candidate['failed'] <= metrics[before]['failed']
            checks['wait_strict_decrease_vs_' + before] = candidate['wait_sum_seconds'] < metrics[before]['wait_sum_seconds']
        comparison = (paired_metrics or {}).get(STOPLOSS)
        checks['success_identities_preserved_vs_' + STOPLOSS] = (
            comparison is not None and comparison['lost_selections'] == 0)
        if stage == 'validation':
            for before in (COHORT_SUCCESS, HEDGE, STOPLOSS):
                item = ci.get(before)
                checks['failure_CI_nonincrease_vs_' + before] = item is not None and item['ci95_failed_per_patient'][1] <= 0
                checks['wait_CI_strict_decrease_vs_' + before] = item is not None and item['ci95_wait_per_patient'][1] < 0
    else:
        raise ValueError('unknown stage')
    return dict(passed=all(checks.values()), checks=checks, operational_adoption=False,
                objective='separate known-slice success and waiting goals plus zero lost parent Stoploss successes and strict lower wait; fresh Stoploss/CS/Hedge failure and wait conjunction')


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
    ci = {name: paired_lex_ci(all_compacts, name) for name in (COHORT_SUCCESS, HEDGE, STOPLOSS)} if stage == 'validation' else {}
    return dict(metrics=metrics, origin_metrics=origins, paired=pairs, origin_paired=origin_pairs,
        whole_lex_ci=ci, family_metrics=families,
        regressions_report_only=regression_outcomes(cases) if stage == 'repair' else {},
        assessment=assess(metrics.get('whole', {}), {o: m['whole'] for o, m in origins.items()}, ci, stage,
            paired_metrics=pairs.get('whole', {}),
            origin_paired_metrics={o: p['whole'] for o, p in origin_pairs.items()}),
        priority_performance_scope='all default4; no clinical tier-diversity claim',
        known_pooled_metrics_not_used_or_reported=True)


def retention_view(compacts, before, after):
    """Stored outcome comparison only; never supplied to policy inputs."""
    value = dict(before=before, after=after, scenarios=len(compacts), patients=0,
        before_wait_sum_seconds=0, after_wait_sum_seconds=0, wait_sum_delta_seconds=0,
        lost_selections=0, new_selections=0, lost_identifiers=[], gained_identifiers=[],
        maximum_individual_wait_increase_seconds=0, wait_increase_patient_count=0,
        wait_increase_sum_seconds=0, wait_increase_identifiers=[])
    for case in compacts:
        old = {(type(p['id']).__name__, str(p['id'])): p
               for p in patient_rows(case['branches'][before]['patients'])}
        new = {(type(p['id']).__name__, str(p['id'])): p
               for p in patient_rows(case['branches'][after]['patients'])}
        assert old.keys() == new.keys(), 'retention patient identity partition differs'
        for key, current in new.items():
            previous = old[key]
            b, a = wait(previous), wait(current)
            delta = a - b
            identity = dict(parent_stage=case.get('parent_stage'), kind=case['kind'],
                            seed=case['seed'], patient=current['id'])
            value['patients'] += 1
            value['before_wait_sum_seconds'] += b
            value['after_wait_sum_seconds'] += a
            value['wait_sum_delta_seconds'] += delta
            if not previous['failed'] and current['failed']:
                value['lost_selections'] += 1
                value['lost_identifiers'].append(identity)
            if previous['failed'] and not current['failed']:
                value['new_selections'] += 1
                value['gained_identifiers'].append(identity)
            if delta > 0:
                value['wait_increase_patient_count'] += 1
                value['wait_increase_sum_seconds'] += delta
                value['maximum_individual_wait_increase_seconds'] = max(
                    value['maximum_individual_wait_increase_seconds'], delta)
                value['wait_increase_identifiers'].append(dict(**identity,
                    before_wait_seconds=b, after_wait_seconds=a,
                    before_failed=previous['failed'], after_failed=current['failed'],
                    wait_increase_seconds=delta))
    return value


def retention_for_compacts(compacts, stage):
    """Additional declared retention goals; original assessment is unchanged."""
    if stage not in ORDER:
        raise ValueError('unknown retention stage')
    groups = ({origin: [c for c in compacts if c['parent_stage'] == origin]
               for origin in ('repair', 'development')}
              if stage == 'repair' else {'whole': list(compacts)})
    checks = {}; scopes = {}
    if stage == 'repair':
        checks['both_observed_training_slices_present'] = all(groups.values())
    else:
        checks['fresh_stage_data_present'] = bool(compacts)
    for scope, selected in groups.items():
        original = retention_view(selected, STOPLOSS, PRIMARY)
        retained = retention_view(selected, PAIRSHIELD, PRIMARY)
        baseline = retention_view(selected, STOPLOSS, PAIRSHIELD)
        maximum = original['maximum_individual_wait_increase_seconds']
        baseline_maximum = baseline['maximum_individual_wait_increase_seconds']
        if stage == 'repair':
            limit = 1 if scope == 'repair' else 0
            personal_passed = maximum <= limit
            strict = False
        else:
            limit = baseline_maximum
            strict = baseline_maximum > 0
            personal_passed = maximum < limit if strict else maximum == 0
        checks[scope + '_stoploss_successes_preserved'] = original['lost_selections'] == 0
        checks[scope + '_pairshield_successes_preserved'] = retained['lost_selections'] == 0
        checks[scope + '_wait_retained_vs_pairshield'] = retained['wait_sum_delta_seconds'] <= 0
        checks[scope + '_personal_delay_goal'] = personal_passed
        scopes[scope] = dict(candidate_vs_stoploss=original,
            candidate_vs_pairshield=retained, pairshield_vs_stoploss=baseline,
            required_maximum_delay_seconds=limit,
            maximum_delay_requires_strict_decrease=strict)
    return dict(passed=all(checks.values()), checks=checks, scopes=scopes,
        reference=dict(successes=[STOPLOSS, PAIRSHIELD], aggregate_wait=PAIRSHIELD,
                       personal_wait=STOPLOSS), operational_adoption=False)


def retention_assessment(cases, stage):
    return retention_for_compacts([case['compact'] for case in cases], stage)


def pareto_view(compacts, before, after):
    """Full stored per-patient wait/arrival vectors; no policy input or forecasts."""
    value = retention_view(compacts, before, after)
    value.update(before_arrival_cost_sum_seconds=0, after_arrival_cost_sum_seconds=0,
        arrival_cost_sum_delta_seconds=0,
        maximum_individual_arrival_cost_increase_seconds=0,
        arrival_increase_patient_count=0, arrival_increase_sum_seconds=0,
        arrival_increase_identifiers=[], both_selected=0,
        maximum_both_selected_arrival_increase_seconds=0,
        both_selected_arrival_increase_patient_count=0,
        both_selected_arrival_increase_sum_seconds=0,
        both_selected_arrival_increase_identifiers=[], patient_comparisons=[])
    for case in compacts:
        old = {(type(p['id']).__name__, str(p['id'])): p
               for p in patient_rows(case['branches'][before]['patients'])}
        new = {(type(p['id']).__name__, str(p['id'])): p
               for p in patient_rows(case['branches'][after]['patients'])}
        assert old.keys() == new.keys(), 'pareto patient identity partition differs'
        for key, current in new.items():
            previous = old[key]
            previous_arrival = 1800 if previous['failed'] else previous['estimated_arrival_seconds']
            current_arrival = 1800 if current['failed'] else current['estimated_arrival_seconds']
            delta = current_arrival - previous_arrival
            both = not previous['failed'] and not current['failed']
            identity = dict(parent_stage=case.get('parent_stage'), kind=case['kind'],
                            seed=case['seed'], patient=current['id'])
            vector = dict(**identity, before_failed=previous['failed'], after_failed=current['failed'],
                before_wait_seconds=wait(previous), after_wait_seconds=wait(current),
                wait_delta_seconds=wait(current)-wait(previous),
                before_arrival_cost_seconds=previous_arrival,
                after_arrival_cost_seconds=current_arrival, arrival_cost_delta_seconds=delta,
                both_selected=both)
            value['patient_comparisons'].append(vector)
            value['before_arrival_cost_sum_seconds'] += previous_arrival
            value['after_arrival_cost_sum_seconds'] += current_arrival
            value['arrival_cost_sum_delta_seconds'] += delta
            if delta > 0:
                harm = dict(**vector, arrival_cost_increase_seconds=delta)
                value['arrival_increase_patient_count'] += 1
                value['arrival_increase_sum_seconds'] += delta
                value['maximum_individual_arrival_cost_increase_seconds'] = max(
                    value['maximum_individual_arrival_cost_increase_seconds'], delta)
                value['arrival_increase_identifiers'].append(harm)
                if both:
                    value['both_selected_arrival_increase_patient_count'] += 1
                    value['both_selected_arrival_increase_sum_seconds'] += delta
                    value['maximum_both_selected_arrival_increase_seconds'] = max(
                        value['maximum_both_selected_arrival_increase_seconds'], delta)
                    value['both_selected_arrival_increase_identifiers'].append(harm)
            value['both_selected'] += both
    return value


def pareto_for_compacts(compacts, stage):
    """Best-of-both preservation is a new layer; all earlier gates stay intact."""
    if stage not in ORDER:
        raise ValueError('unknown pareto stage')
    groups = ({origin: [c for c in compacts if c['parent_stage'] == origin]
               for origin in ('repair', 'development')}
              if stage == 'repair' else {'whole': list(compacts)})
    checks = ({'both_observed_training_slices_present': all(groups.values())}
              if stage == 'repair' else {'fresh_stage_data_present': bool(compacts)})
    scopes = {}
    for scope, selected in groups.items():
        views = {label: pareto_view(selected, baseline, PRIMARY)
                 for label, baseline in (('stoploss', STOPLOSS), ('pairshield', PAIRSHIELD), ('tailwait', TAILWAIT))}
        for label in ('stoploss', 'pairshield', 'tailwait'):
            checks[scope + '_' + label + '_successes_preserved'] = views[label]['lost_selections'] == 0
        checks[scope + '_wait_retained_vs_tailwait'] = views['tailwait']['wait_sum_delta_seconds'] <= 0
        for label in ('tailwait', 'pairshield'):
            checks[scope + '_no_individual_wait_increase_vs_' + label] = (
                views[label]['maximum_individual_wait_increase_seconds'] == 0)
            checks[scope + '_no_individual_arrival_increase_vs_' + label] = (
                views[label]['maximum_individual_arrival_cost_increase_seconds'] == 0)
        scopes[scope] = dict(candidate_vs_stoploss=views['stoploss'],
            candidate_vs_pairshield=views['pairshield'], candidate_vs_tailwait=views['tailwait'],
            required_maximum_wait_increase_seconds=0, required_maximum_arrival_increase_seconds=0)
    return dict(passed=all(checks.values()), checks=checks, scopes=scopes,
        reference=dict(successes=[STOPLOSS, PAIRSHIELD, TAILWAIT], aggregate_wait=TAILWAIT,
            personal_wait_and_arrival=[TAILWAIT, PAIRSHIELD],
            failed_wait_seconds=300, failed_arrival_cost_seconds=1800), operational_adoption=False)


def pareto_retention_assessment(cases, stage):
    return pareto_for_compacts([case['compact'] for case in cases], stage)


def semantic_path(stage):
    return BUILD / (stage + '-semantic-audit.json')


def source_map_digest(sources):
    return sha(raw({name: sources[name] for name in sorted(sources)}))


def semantic_case_path(stage, item):
    assert stage in ORDER and Path(item['file']).name == item['file']
    return BUILD / (stage + '-semantic-proofs') / (item['file'] + '.audit.json')


def verify_semantic_case(proof, stage, item, sources, index_sha256):
    assert proof['passed'] is True and proof['stage'] == stage
    assert all(proof[key] == item[key] for key in ('file', 'raw_sha256', 'compressed_sha256'))
    assert proof['raw_index_sha256'] == index_sha256
    assert proof['source_map_sha256'] == source_map_digest(sources)
    assert proof['new_performance_runs'] == 0 and proof['semantic_audit_executions'] == 1
    assert proof['audit']['passed'] and proof['audit']['new_executions'] == 0
    return proof


def semantic_worker(task):
    """Audit saved bytes only; no runner, generator, optimizer or simulator call."""
    stage, item, entry, arms, sources, index_sha256, reference = task
    assert source_hashes() == sources
    assert file_sha(BUILD / (stage + '-raw-index.json')) == index_sha256
    index = read(BUILD / (stage + '-raw-index.json'))
    assert index['all_outcomes_saved'] is True and item in index['cases']
    path = semantic_case_path(stage, item)
    raw_path = BUILD / (stage + '-cases') / item['file']
    if path.exists():
        proof = verify_semantic_case(read(path), stage, item, sources, index_sha256)
        assert file_sha(raw_path) == item['compressed_sha256']
        return proof
    replay, body = _read_replay(raw_path, item)
    assert sha(raw(replay['scenario'])) == entry['sha256']
    assert replay['scenario']['seed'] == entry['seed'] and replay['scenario']['horizon_seconds'] == 300
    assert replay['arms'] == [list(a) for a in arms] and len(replay['branches']) == len(arms)
    expected = {patient['id'] for patient in replay['scenario']['patients']}
    assert len(expected) == len(replay['scenario']['patients'])
    for pair, branch in zip(arms, replay['branches']):
        assert [branch['policy'], branch['rule']] == list(pair)
        assert branch['snapshot_sha256'] == replay['initial_snapshot_sha256']
        assert branch['environment_sha256'] == replay['environment_sha256']
        assert {p['id'] for p in patient_rows(branch['patients'])} == expected
    if reference is not None:
        assert entry['parent_stage'] == reference['parent_stage']
        assert (entry['kind'], entry['seed'], entry['sha256']) == (reference['kind'], reference['seed'], reference['scenario_sha256'])
        assert replay['initial_snapshot_sha256'] == reference['initial_snapshot_sha256']
        assert replay['environment_sha256'] == reference['environment_sha256']
        assert all({p['id'] for p in value['patients']} == expected for value in reference['branches'].values())
    else:
        assert entry['parent_stage'] is None
    from .cohort_startslots_audit import audit_replay
    audit = audit_replay(replay)
    assert audit['passed'] and audit['new_executions'] == 0 and raw(replay) == body
    assert source_hashes() == sources
    proof = dict(passed=True, stage=stage, **item, raw_index_sha256=index_sha256,
        source_map_sha256=source_map_digest(sources), audit=audit,
        semantic_audit_executions=1, new_performance_runs=0)
    write_new(path, raw(proof))
    return proof


def prepare_semantic_proofs(stage, workers=4):
    """Only after all raw saves and the prior gate; resume durable valid proofs."""
    if type(workers) is not int or workers <= 0:
        raise ValueError('workers must be a positive integer')
    if stage not in ORDER:
        raise ValueError('unknown stage')
    frozen = verify_freeze()
    prior = verify_prior(stage)  # Before any proof folder, task list or pool.
    if semantic_path(stage).exists():
        return verify_semantic_index(stage)
    index_path = BUILD / (stage + '-raw-index.json')
    index = read(index_path)
    assert index['all_outcomes_saved'] is True
    todo = tasks(stage)
    manifest = [entry for entry in read(BUILD / 'input-manifest.json')['entries'] if entry['stage'] == stage]
    assert len(index['cases']) == len(todo) == len(manifest)
    folder = BUILD / (stage + '-cases')
    assert {p.name for p in folder.iterdir()} == {item['file'] for item in index['cases']}
    sources = source_hashes()
    assert sources == frozen['protocol']['source_sha256']
    index_sha256 = file_sha(index_path)
    known = control_ledger()['entries'] if stage == 'repair' else None
    if known is not None:assert len(known) == len(todo)
    proofs, missing = {}, []
    for position, (item, entry, (kind, seed, arms)) in enumerate(zip(index['cases'], manifest, todo)):
        assert item['file'] == f'{position:03d}_{seed}.json.gz'
        assert (entry['kind'], entry['seed']) == (kind, seed)
        path = semantic_case_path(stage, item)
        if path.exists():
            proof = verify_semantic_case(read(path), stage, item, sources, index_sha256)
            assert file_sha(folder / item['file']) == item['compressed_sha256']
            proofs[item['file']] = proof
        else:
            missing.append((stage, item, entry, arms, sources, index_sha256, None if known is None else known[position]))
    assert prior == verify_prior(stage) and source_hashes() == sources
    if missing:
        (BUILD / (stage + '-semantic-proofs')).mkdir(exist_ok=True)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for proof in pool.map(semantic_worker, missing, chunksize=1):
                assert proof['file'] not in proofs
                proofs[proof['file']] = proof
    assert len(proofs) == len(index['cases'])
    assert source_hashes() == sources and prior == verify_prior(stage)
    records = []
    for item in index['cases']:
        proof = verify_semantic_case(proofs[item['file']], stage, item, sources, index_sha256)
        path = semantic_case_path(stage, item)
        assert read(path) == proof
        records.append(dict(**item, audit=proof['audit'], semantic_proof_file=path.name,
                            semantic_proof_sha256=file_sha(path)))
    value = dict(passed=True, stage=stage, all_cases_audited=True,
        raw_index_sha256=index_sha256, source_sha256=sources, cases=records,
        new_performance_runs=0, semantic_audit_executions=len(records),
        semantic_audits_in_this_collection=len(missing), semantic_audit_proofs_reused=len(records)-len(missing),
        semantic_audit_worker_limit=workers, ancestor_policy_audits_reexecuted=0)
    write_new(semantic_path(stage), raw(value))
    return value


def verify_semantic_index(stage):
    value = read(semantic_path(stage))
    index = read(BUILD / (stage + '-raw-index.json'))
    assert value['passed'] and value['stage'] == stage and value['all_cases_audited'] is True
    assert value['source_sha256'] == source_hashes()
    assert value['raw_index_sha256'] == file_sha(BUILD / (stage + '-raw-index.json'))
    assert len(value['cases']) == len(index['cases']) == len(tasks(stage))
    assert value['semantic_audit_executions'] == len(index['cases'])
    assert value['semantic_audits_in_this_collection'] + value['semantic_audit_proofs_reused'] == len(index['cases'])
    assert type(value['semantic_audit_worker_limit']) is int and value['semantic_audit_worker_limit'] > 0
    assert value['ancestor_policy_audits_reexecuted'] == 0
    for proof, item in zip(value['cases'], index['cases']):
        assert all(proof[k] == item[k] for k in ('file', 'raw_sha256', 'compressed_sha256'))
        assert proof['audit']['passed'] and proof['audit']['new_executions'] == 0
        path = semantic_case_path(stage, item)
        assert path.name == proof['semantic_proof_file'] and file_sha(path) == proof['semantic_proof_sha256']
        case = verify_semantic_case(read(path), stage, item, value['source_sha256'], value['raw_index_sha256'])
        assert case['audit'] == proof['audit']
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
        prepare_semantic_proofs(stage, workers=SETTINGS['semantic_audit_workers'])
        existing = verify_semantic_index(stage)
    sources = source_hashes()
    known = control_ledger()['entries'] if stage == 'repair' else None
    cases = []
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
        proof = existing['cases'][position]['audit']
        compact = dict(kind=kind, seed=seed, family=replay['scenario']['family'],
            scenario_id=replay['scenario']['id'], parent_stage=parent_stage,
            original_origin=original_origin, branches=branches)
        cases.append(dict(**item, parent_stage=parent_stage, original_origin=original_origin,
                          audit=proof, compact=compact))
    assert source_hashes() == sources
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
    assert report['assessment']['passed'] and report['retention_assessment']['passed'] and report['pareto_retention_assessment']['passed'] and audit['passed'] and lock['next_stage_allowed'] is True
    assert file_sha(BUILD / (previous + '.json')) == audit['report_sha256'] == lock['report_sha256']
    assert lock['candidate'] == PRIMARY and lock['assessment'] == report['assessment']
    assert lock['retention_assessment'] == report['retention_assessment']
    assert lock['pareto_retention_assessment'] == report['pareto_retention_assessment']
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
        new_branches=len(cases) * (1 if stage == 'repair' else 26),
        cached_branches=9000 if stage == 'repair' else 0,
        semantic_audit_sha256=file_sha(semantic_path(stage)),
        prior_stage_evidence_sha256=prior, operational_adoption=False,
        retention_assessment=retention_assessment(cases, stage),
        pareto_retention_assessment=pareto_retention_assessment(cases, stage), **result)
    assert protocol() == frozen['protocol'] and prior == prior_evidence(stage)
    write_new(BUILD / (stage + '.json'), raw(report))
    if stage != 'validation':
        write_new(BUILD / (stage + '-lock.json'), raw(dict(candidate=PRIMARY,
            protocol=frozen['protocol'], report_sha256=sha(raw(report)),
            assessment=report['assessment'], retention_assessment=report['retention_assessment'],
            pareto_retention_assessment=report['pareto_retention_assessment'],
            next_stage_allowed=report['assessment']['passed'] and report['retention_assessment']['passed'] and report['pareto_retention_assessment']['passed'])))
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
    from . import cohort_startslots_runner
    todo = tasks(stage)
    folder = BUILD / (stage + '-cases')
    folder.mkdir()
    items = []
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for index, replay in enumerate(pool.map(cohort_startslots_runner.run_task, todo, chunksize=1)):
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
    print(dict(stage=stage, assessment=report['assessment'], retention_assessment={
        key: report['retention_assessment'][key] for key in ('passed', 'checks')},
        pareto_retention_assessment={key: report['pareto_retention_assessment'][key]
                                    for key in ('passed', 'checks')}), flush=True)


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
    assert report['new_branches'] == len(cases) * (1 if stage == 'repair' else 26)
    assert report['cached_branches'] == (9000 if stage == 'repair' else 0)
    assert report['semantic_audit_sha256'] == file_sha(semantic_path(stage))
    for key, value in evaluate(cases, stage).items():
        assert report[key] == value, key
    assert report['retention_assessment'] == retention_assessment(cases, stage)
    assert report['pareto_retention_assessment'] == pareto_retention_assessment(cases, stage)
    value = dict(passed=True, report_sha256=file_sha(BUILD / (stage + '.json')),
        semantic_audit_sha256=file_sha(semantic_path(stage)), source_sha256=source_hashes(),
        cases=len(cases), new_branches_checked=report['new_branches'],
        cached_control_branches_bound=report['cached_branches'], new_performance_runs=0,
        retention_assessment_verified=True,
        pareto_retention_assessment_verified=True,
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
