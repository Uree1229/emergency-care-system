"""Continue the frozen Releasealign arm in a new, independently bound build.

The twelve archived screen performances are inherited read-only.  All twenty
four initial semantic proofs are newly evaluated; no archived screen is
retroactively labelled audited.  No frozen module globals are changed.
"""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import argparse
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import time
import traceback

from . import diverse_diagnostic as previous

ROOT = previous.ROOT
BUILD_REL = '.codex-build/eat-queue-review-20261027'
OLD_BUILD_REL = '.codex-build/eat-diverse-20261026'
BUILD, OLD_BUILD = ROOT / BUILD_REL, ROOT / OLD_BUILD_REL
ARCHIVE_SHA = 'f924a5db49e72f3c8d11294e89783500cb3ee8ca8be3b1c882b7e3cf9afa7fd4'
OLD_FREEZE_SHA = '3255c8df081ebf8e95f8d86d5966df0f3642b5b81212ebe02fd671c356d53c38'
ARM = 'releasealign'
NAME = previous.ARMS[ARM]['name']
SCREEN, INITIAL = previous.SCREEN, previous.INITIAL
NEW_SOURCES = ('inquiry_lab/queue_review_diagnostic.py', 'test_inquiry_lab_queue_review_diagnostic.py')
BASE_FIELDS = ('file', 'raw_folder', 'source_index', 'input_sha256', 'raw_sha256', 'compressed_sha256')
GEN_FIELDS = ('generation_source_freeze_sha256', 'generation_source_count')
SCOPE = 'Known-training incremental characterization only; original legacy gates remain unchanged and may fail'
raw, read, digest, sha, write = previous.raw, previous.read, previous.digest, previous.sha, previous.write


def path(phase, suffix):
    return BUILD / (ARM + '-' + phase + suffix)


def phase_plan(phase):
    if phase == 'initial24':
        return [i for i in INITIAL if i not in SCREEN]
    if phase == 'full360':
        return [i for i in range(360) if i not in INITIAL]
    raise ValueError('only initial24/full360 continuations are allowed')


def with_generation(item, freeze_sha):
    result = {k: item[k] for k in BASE_FIELDS}
    inherited = result['source_index'] in SCREEN
    result.update(generation_source_freeze_sha256=OLD_FREEZE_SHA if inherited else freeze_sha,
                  generation_source_count=698 if inherited else 700)
    return result


def resolve_raw(item):
    index = item['source_index']
    assert type(index) is int and 0 <= index < 360
    assert Path(item['file']).name == item['file']
    if index in SCREEN:
        assert item['raw_folder'] == 'releasealign-screen-cases'
        root = OLD_BUILD
    else:
        wanted = 'releasealign-initial24-cases' if index in INITIAL else 'releasealign-full360-cases'
        assert item['raw_folder'] == wanted
        root = BUILD
    return root / item['raw_folder'] / item['file']


def continuation():
    assert digest(OLD_BUILD / 'diagnostic-archive.json') == ARCHIVE_SHA
    archive = read(OLD_BUILD / 'diagnostic-archive.json')
    assert archive['archive_integrity_passed'] and archive['source_count'] == 698
    for name in ('source-freeze.json', 'input-manifest.json', 'releasealign-screen-report.json',
                 'releasealign-screen-independent.json', 'releasealign-screen-raw-index.json',
                 'diagnosis/diverse_independent.py'):
        relative = OLD_BUILD_REL + '/' + name
        assert digest(ROOT / relative) == archive['private_files_sha256'][relative]
    assert digest(OLD_BUILD / 'source-freeze.json') == OLD_FREEZE_SHA
    index = read(OLD_BUILD / 'releasealign-screen-raw-index.json')
    assert index['all_outcomes_saved'] and [i['source_index'] for i in index['cases']] == list(SCREEN)
    for item in index['cases']:
        assert item['raw_folder'] == 'releasealign-screen-cases'
        assert Path(item['file']).name == item['file']
        relative = OLD_BUILD_REL + '/' + item['raw_folder'] + '/' + item['file']
        assert item['compressed_sha256'] == archive['own_compressed_raw_sha256'][relative]
        assert digest(ROOT / relative) == item['compressed_sha256']
    return dict(archive_relative=OLD_BUILD_REL + '/diagnostic-archive.json', archive_sha256=ARCHIVE_SHA,
        old_build_relative=OLD_BUILD_REL, old_source_freeze_sha256=OLD_FREEZE_SHA,
        old_input_manifest_sha256=digest(OLD_BUILD / 'input-manifest.json'),
        old_screen_report_sha256=digest(OLD_BUILD / 'releasealign-screen-report.json'),
        old_screen_receipt_sha256=digest(OLD_BUILD / 'releasealign-screen-independent.json'),
        old_screen_raw_index_sha256=digest(OLD_BUILD / 'releasealign-screen-raw-index.json'),
        inherited_screen_indices=list(SCREEN), inherited_screen_raws=deepcopy(index['cases']))


def bindings():
    p = deepcopy(previous.frozen())
    old = read(OLD_BUILD / 'diagnostic-archive.json')
    assert p['source_sha256'] == old['source_sha256'] and len(p['source_sha256']) == 698
    p['source_sha256'].update({n: digest(ROOT / n) for n in NEW_SOURCES})
    assert len(p['source_sha256']) == 700
    for name in ('diagnostic-archive.json', 'source-freeze.json', 'input-manifest.json',
                 'releasealign-screen-report.json', 'releasealign-screen-independent.json', 'releasealign-screen-raw-index.json',
                 'diagnosis/diverse_independent.py'):
        p['binding_sha256'][OLD_BUILD_REL + '/' + name] = digest(OLD_BUILD / name)
    p.update(name='eat_queue_review_20261027', continuation=continuation(),
             execution_candidates=[NAME], inherited_candidate_performances=12,
             source_inventory_scope='Inherited698 unchanged plus continuation driver/test2; screen raw generation remains698')
    return p


def freeze(checks_path):
    assert not (BUILD / 'source-freeze.json').exists()
    assert not any(BUILD.glob('releasealign-*-started.json'))
    p, checks = bindings(), read(checks_path)
    assert checks['passed'] and checks['failures'] == checks['errors'] == checks['skips'] == 0
    assert checks['scope'] == 'targeted_queue_review_tests'
    assert checks['source_sha256_before'] == checks['source_sha256_after'] == p['source_sha256']
    private = checks['private_files_sha256']
    assert private and all(digest(ROOT / n) == v for n, v in private.items())
    assert checks['independent_checker_sha256'] in private.values()
    p['binding_sha256'].update(private)
    p['binding_sha256'][Path(checks_path).resolve().relative_to(ROOT).as_posix()] = digest(checks_path)
    p['independent_checker_sha256'] = checks['independent_checker_sha256']
    write(BUILD / 'input-manifest.json', p)
    write(BUILD / 'source-freeze.json', dict(passed=True, protocol_sha256=digest(BUILD / 'input-manifest.json'),
        source_sha256=p['source_sha256'], candidate_branches_before_freeze=0,
        inherited_archived_performances=12, inherited_archived_semantic_proofs=0,
        qualification='Continuation on observed training only; historical test failures not reclassified'))


def frozen():
    f, p = read(BUILD / 'source-freeze.json'), read(BUILD / 'input-manifest.json')
    assert f['passed'] and f['candidate_branches_before_freeze'] == 0
    assert f['protocol_sha256'] == digest(BUILD / 'input-manifest.json')
    assert f['source_sha256'] == p['source_sha256'] and len(p['source_sha256']) == 700
    assert all(digest(ROOT / n) == v for n, v in p['source_sha256'].items())
    assert all(digest(ROOT / n) == v for n, v in p['binding_sha256'].items())
    assert p['continuation'] == continuation()
    inherited = read(OLD_BUILD / 'diagnostic-archive.json')['source_sha256']
    assert {n:p['source_sha256'][n] for n in inherited} == inherited
    return p


def proof_payload(case, phase, sources):
    value = {k:case[k] for k in ('source_index', 'input_sha256', 'raw_sha256', 'compressed_sha256') + GEN_FIELDS}
    value.update(passed=True, candidate=NAME, phase=phase, audit=case['audit'],
        source_map_sha256=sha(raw(dict(sorted(sources.items())))), compact_sha256=sha(raw(case['compact'])))
    return value


def verify_prior(phase, p):
    if phase == 'screen':
        old = previous.frozen()
        assert old['entries'] == p['entries']
        result = previous.verify_prior(ARM, 'screen', old)
        assert result['semantic_verification'] is False and result['independent_semantic_audits'] == 0
        assert result['incremental_research_gate']['passed']
        return result
    assert phase == 'initial24'
    result, receipt = read(path(phase, '-report.json')), read(path(phase, '-independent.json'))
    assert result['candidate'] == receipt['candidate'] == NAME
    assert result['phase'] == receipt['phase'] == phase
    assert result['source_sha256'] == p['source_sha256'] and result['continuation'] == p['continuation']
    assert result['source_freeze_sha256'] == digest(BUILD / 'source-freeze.json')
    assert receipt['source_freeze_sha256'] == result['source_freeze_sha256']
    assert receipt['source_sha256'] == p['source_sha256'] and receipt['source_count'] == 700
    assert receipt['input_manifest_sha256'] == digest(BUILD / 'input-manifest.json')
    assert receipt['continuation'] == p['continuation'] and receipt['prior_candidate_branches_reused'] == 12
    assert receipt['semantic_verification'] is True and receipt['stored_semantic_proofs_verified'] == 24
    assert result['early_gate'] == previous.numeric(ARM).early_gate(result['cases'])
    assert result['incremental_research_gate'] == previous.incremental_research_gate(result['early_gate'])
    assert result['incremental_research_gate']['passed'] and result['semantic_verification'] is True
    assert receipt['passed'] and receipt['checker_sha256'] == p['independent_checker_sha256']
    assert receipt['report_sha256'] == digest(path(phase, '-report.json')) and receipt['cases_checked'] == 24
    assert receipt['early_gate_equal'] and receipt['incremental_gate_equal'] and receipt['vectors_equal']
    index, audits = read(path(phase, '-raw-index.json')), read(path(phase, '-audit-index.json'))
    assert result['raw_index_sha256'] == receipt['raw_index_sha256'] == digest(path(phase, '-raw-index.json'))
    assert result['audit_index_sha256'] == receipt['audit_index_sha256'] == digest(path(phase, '-audit-index.json'))
    assert [c['source_index'] for c in result['cases']] == [i['source_index'] for i in index['cases']] == list(INITIAL)
    assert audits['passed'] and len(audits['cases']) == 24 and audits['source_sha256'] == p['source_sha256']
    assert len(result['cases']) == 24
    for case, item, summary in zip(result['cases'], index['cases'], audits['cases']):
        assert case['semantic_verified'] is True and case['audit']['passed'] is True
        assert {k:case[k] for k in BASE_FIELDS + GEN_FIELDS} == item
        assert with_generation(item, result['source_freeze_sha256']) == item
        assert digest(resolve_raw(item)) == item['compressed_sha256']
        proof_path = path(phase, '-proofs') / summary['proof_file']
        assert Path(summary['proof_file']).name == summary['proof_file']
        assert summary['proof_file'] == case['file'] + '.audit.json'
        assert digest(proof_path) == summary['proof_sha256']
        proof = proof_payload(case, phase, p['source_sha256'])
        assert read(proof_path) == proof
        assert summary['compact_sha256'] == proof['compact_sha256'] and summary['audit'] == case['audit']
    return result


def read_candidate(task):
    phase, item, entry, sources, freeze_sha = task
    assert with_generation(item, freeze_sha) == item
    assert item['file'] == f"{entry['source_index']:03d}_{entry['seed']}.json.gz"
    packed = resolve_raw(item).read_bytes()
    assert sha(packed) == item['compressed_sha256']
    body = gzip.decompress(packed)
    assert sha(body) == item['raw_sha256']
    replay = json.loads(body)
    assert raw(replay) == body
    case = previous.output_case(ARM, item, entry, replay)
    module = previous.ARMS[ARM]['auditor']
    assert digest(ROOT / (module.replace('.', '/') + '.py')) == sources[module.replace('.', '/') + '.py']
    audit = importlib.import_module(module).audit_replay(replay)
    assert audit['passed'] and audit['new_executions'] == 0 and raw(replay) == body
    case.update(audit=audit, semantic_verified=True)
    return case


def execute(workers=1):
    assert type(workers) is int and workers == 1, 'One worker protects the available memory budget'
    temp = (BUILD / 'tmp').resolve()
    assert Path(os.environ.get('TEMP', '')).resolve() == Path(os.environ.get('TMP', '')).resolve() == temp
    p = frozen()
    phase = 'full360' if path('initial24', '-report.json').exists() else 'initial24'
    prior = verify_prior('initial24' if phase == 'full360' else 'screen', p)
    indices = phase_plan(phase)
    for suffix in ('-started.json', '-failure.json', '-cases', '-proofs', '-raw-index.json', '-audit-index.json', '-report.json'):
        assert not path(phase, suffix).exists()
    start, items, proofs = time.perf_counter(), [], []
    freeze_sha = digest(BUILD / 'source-freeze.json')
    write(path(phase, '-started.json'), dict(candidate=NAME, phase=phase, indices=indices,
        source_freeze_sha256=freeze_sha, new_candidate_branches_scheduled=len(indices),
        prior_candidate_branches_reused=12, performance_reuse_scope='Archived screen12 only; prior-stage reuse reported separately',
        known_training=True, heldout=False, controls_reexecuted=0))
    folder = path(phase, '-cases')
    folder.mkdir()
    entries = [p['entries'][i] for i in indices]
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for entry, replay in zip(entries, pool.map(previous.run_candidate, [(ARM,e) for e in entries], chunksize=1)):
                body = raw(replay)
                packed = gzip.compress(body, compresslevel=6, mtime=0)
                filename = f"{entry['source_index']:03d}_{entry['seed']}.json.gz"
                with (folder / filename).open('xb') as stream:
                    stream.write(packed)
                item = dict(file=filename, raw_folder=folder.name, source_index=entry['source_index'],
                    input_sha256=entry['input_sha256'], raw_sha256=sha(body), compressed_sha256=sha(packed))
                items.append(with_generation(item, freeze_sha))
        assert len(items) == len(indices)
        if phase == 'initial24':
            items = sorted(items + [with_generation(i, freeze_sha) for i in p['continuation']['inherited_screen_raws']], key=lambda i:i['source_index'])
        all_items = items
        if phase == 'full360':
            all_items = sorted(read(path('initial24', '-raw-index.json'))['cases'] + items, key=lambda i:i['source_index'])
        write(path(phase, '-raw-index.json'), dict(all_outcomes_saved=True, cases=all_items))
        path(phase, '-proofs').mkdir()
        cases = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            tasks = [(phase, i, p['entries'][i['source_index']], p['source_sha256'], freeze_sha) for i in items]
            for case in pool.map(read_candidate, tasks, chunksize=1):
                proof = proof_payload(case, phase, p['source_sha256'])
                filename = case['file'] + '.audit.json'
                write(path(phase, '-proofs') / filename, proof)
                proofs.append(dict(proof_file=filename, proof_sha256=digest(path(phase, '-proofs') / filename),
                    compact_sha256=proof['compact_sha256'], audit=case['audit']))
                cases.append(case)
        write(path(phase, '-audit-index.json'), dict(passed=True, cases=proofs, source_sha256=p['source_sha256']))
        if phase == 'full360':
            cases = sorted(prior['cases'] + cases, key=lambda c:c['source_index'])
        assert [c['source_index'] for c in cases] == (list(INITIAL) if phase == 'initial24' else list(range(360)))
        assert frozen() == p
        n = previous.numeric(ARM)
        gate = n.early_gate(cases)
        incremental = previous.incremental_research_gate(gate)
        report = dict(candidate=NAME, phase=phase, source_sha256=p['source_sha256'], source_freeze_sha256=freeze_sha,
            continuation=deepcopy(p['continuation']), raw_index_sha256=digest(path(phase, '-raw-index.json')),
            audit_index_sha256=digest(path(phase, '-audit-index.json')), cases=cases, early_gate=gate,
            incremental_research_gate=incremental, legacy_full_goals=n.legacy_assessments(cases,p), semantic_verification=True,
            semantic_scope='Fresh semantic24 including inherited screen12; fresh336 and initial24 proof reuse at360',
            independent_semantic_audits=len(items), semantic_proofs_reused=24 if phase == 'full360' else 0,
            reused_semantic_index_sha256=digest(path('initial24','-audit-index.json')) if phase == 'full360' else None,
            audited_numeric_fields=previous.numeric_counters(c['audit'] for c in cases),
            new_candidate_branches_this_phase=len(indices), cumulative_candidate_branches=len(cases),
            prior_candidate_branches_reused=12, prior_stage_candidate_branches_reused=24 if phase == 'full360' else 12,
            performance_reuse_scope='prior_candidate_branches_reused counts archived screen12 only',
            controls_reexecuted=0, heldout=False, observed_training_only=True, operational_adoption=False, live_default='fixed_3',
            elapsed_seconds=time.perf_counter()-start, elapsed_scope='Diagnostic execution/accounting/audit wall time, not policy latency',
            expansion_allowed=phase != 'full360' and incremental['passed'],
            characterization_expansion_allowed=phase != 'full360' and incremental['passed'], expansion_scope=SCOPE,
            fresh_validation_allowed=False, promotion_allowed=False)
        write(path(phase, '-report.json'), report)
        print(dict(candidate=ARM, phase=phase, cases=len(cases), legacy_gate_passed=gate['passed'],
            incremental_research_gate_passed=incremental['passed'], semantic_verification=True,
            report_sha256=digest(path(phase, '-report.json'))), flush=True)
    except BaseException as exc:
        if not path(phase, '-failure.json').exists():
            write(path(phase, '-failure.json'), dict(failed=True, candidate=NAME, phase=phase,
                exception_type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc(),
                raw_prefix_saved=sum(1 for _ in folder.iterdir()), durable_audit_proofs=len(proofs),
                semantic_attempts_started=None, semantic_attempts_started_measured=False,
                automatic_retry=False, operational_adoption=False))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('freeze', 'expand'))
    parser.add_argument('--checks', type=Path)
    parser.add_argument('--workers', type=int, default=1)
    args = parser.parse_args()
    if args.command == 'freeze':
        assert args.checks
        freeze(args.checks)
    else:
        execute(args.workers)
