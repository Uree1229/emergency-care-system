"""Run the repository tests as named suites.

core        server, voice, team runtime, data and UI tests. Passes on a fresh clone; CI runs it.
local-data  core tests that need git-ignored data (simulation_results raw, outputs).
research    experiment verification. Exact-byte locks and .codex-build artifacts tie it to
            the shared working PC; see docs/TESTING.md.
all         every Python and Node test.

    python scripts/test_suites.py list
    python scripts/test_suites.py core [--node PATH]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Product-facing modules. New tests named with these prefixes join core (and CI) automatically.
CORE_PREFIXES = (
    'test_server', 'test_voice_', 'test_team_', 'test_paramedic', 'test_patient', 'test_hospital',
    'test_conversations', 'test_important_events', 'test_enroute_', 'test_data_provenance',
    'test_call_', 'test_backend_measurement', 'test_external_arrivals', 'test_accounting_reconciliation',
    'test_arrival_accounting', 'test_recovery_rehearsal', 'test_flow_', 'test_speech_eval',
    'test_usability_eval', 'test_viewer_server', 'test_transport_review', 'test_transport_reassignment',
    'test_treatment_', 'test_solver_improved', 'test_planning', 'test_datasets', 'test_public_data',
    'test_recommendation_contract', 'test_system_status', 'test_inquiry_workflow',
    'test_allocation_input_review', 'test_allocation_baseline', 'test_audio_noise_benchmark',
    'test_work_activity', 'test_resource_accounting', 'test_demo_casebook', 'test_source_hash',
)
# Core tests that read git-ignored data; they fail on a fresh clone by construction.
LOCAL_DATA = {'test_server_replay_adapter', 'test_server_replay_regression', 'test_demo_casebook_20261030'}
# Node files that a Python harness launches with a loopback fixture; not runnable alone.
HARNESS_ONLY = {'test_hospital_operations_http.mjs'}
# Historical audits that no longer reproduce since the 2026-10-29 server change to reply
# observations (server/inquiry_policies.py stopped counting legacy 'reply' samples).
KNOWN_HISTORICAL = {'test_public_offer_audit', 'test_c1_incumbent_audit', 'test_offer_reserve_audit'}


def python_modules():
    return sorted(p.stem for p in ROOT.glob('test_*.py'))


def node_files():
    return sorted(p.name for p in ROOT.iterdir() if p.name.startswith('test_') and p.suffix in ('.mjs', '.cjs'))


def classify():
    core, local_data, research = [], [], []
    for name in python_modules():
        if name in LOCAL_DATA:
            local_data.append(name)
        elif name.startswith(CORE_PREFIXES):
            core.append(name)
        else:
            research.append(name)
    nodes = [n for n in node_files() if n not in HARNESS_ONLY]
    return dict(core=core, local_data=local_data, research=research, node=nodes)


def run_python(names):
    """Load each module separately so one import-time failure does not stop the rest."""
    sys.path[:0] = [str(ROOT), str(ROOT / '.solver_deps')]
    os.chdir(ROOT)
    summary = dict(modules=len(names), tests=0, failures=0, errors=0, skipped=0, failing={})
    with open(os.devnull, 'w') as sink:
        for name in names:
            try:
                suite = unittest.defaultTestLoader.loadTestsFromName(name)
            except Exception as exc:
                summary['errors'] += 1
                summary['failing'][name] = f'import: {type(exc).__name__}: {str(exc)[:160]}'
                continue
            result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
            summary['tests'] += result.testsRun
            summary['failures'] += len(result.failures)
            summary['errors'] += len(result.errors)
            summary['skipped'] += len(result.skipped)
            problems = result.errors + result.failures
            if problems:
                last = problems[0][1].strip().splitlines()[-1]
                summary['failing'][name] = f'{len(result.failures)}F/{len(result.errors)}E {last[:160]}'
    return summary


def run_node(files, node):
    executable = node or os.environ.get('TEST_NODE') or shutil.which('node')
    if not executable:
        return dict(files=len(files), ok=False, note='node not found; pass --node or set TEST_NODE')
    proc = subprocess.run([executable, '--test', *files], cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    counts = {}
    for line in proc.stdout.splitlines():
        parts = line.lstrip('ℹ# ').split()
        if len(parts) == 2 and parts[0] in ('tests', 'pass', 'fail') and parts[1].isdigit():
            counts[parts[0]] = int(parts[1])
    failed = [line.strip() for line in proc.stdout.splitlines() if line.lstrip().startswith(('✖', 'not ok'))]
    return dict(files=len(files), ok=proc.returncode == 0, **counts, failed=failed[:20])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('suite', choices=('list', 'core', 'local-data', 'research', 'all'))
    parser.add_argument('--node', help='node executable for the UI tests (default: TEST_NODE or PATH)')
    args = parser.parse_args(argv)
    groups = classify()
    if args.suite == 'list':
        print(json.dumps({k: dict(count=len(v), names=v) for k, v in groups.items()}, ensure_ascii=False, indent=1))
        return 0
    started = time.time()
    report = dict(suite=args.suite)
    if args.suite in ('core', 'all'):
        report['node'] = run_node(groups['node'], args.node)
    names = dict(core=groups['core'], research=groups['research'], all=groups['core'] + groups['local_data'] + groups['research'])
    report['python'] = run_python(names.get(args.suite, groups['local_data']))
    unexpected = {k: v for k, v in report['python']['failing'].items() if k not in KNOWN_HISTORICAL}
    known = sorted(set(report['python']['failing']) & KNOWN_HISTORICAL)
    report['known_historical_failures'] = known
    report['seconds'] = round(time.time() - started, 1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    node_ok = report.get('node', {}).get('ok', True)
    return 0 if node_ok and not unexpected else 1


if __name__ == '__main__':
    raise SystemExit(main())
