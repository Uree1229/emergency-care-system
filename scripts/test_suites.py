"""Run the repository tests as named suites.

core        server, voice, team runtime, data and UI tests. Passes on a fresh clone; CI runs it.
local-data  core tests that need git-ignored data (simulation_results raw, outputs).
research    experiment verification. Exact-byte locks and .codex-build artifacts tie it to
            the shared working PC; see docs/TESTING.md.
all         every Python and Node test.

API key variables are removed before any test runs. Failures listed in
config/research_expected_failures_20261101.json are reported but do not fail the run.

    python scripts/test_suites.py list
    python scripts/test_suites.py core [--node PATH]
    python scripts/test_suites.py python test_server test_inquiry_policies
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import unittest
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_TESTS = ROOT / 'tests' / 'core'
RESEARCH_TESTS = ROOT / 'tests' / 'research'
INQUIRY_LAB_TESTS = RESEARCH_TESTS / 'inquiry_lab'
BROWSER_TESTS = ROOT / 'tests' / 'browser'
PYTHON_TEST_DIRS = (CORE_TESTS, RESEARCH_TESTS, INQUIRY_LAB_TESTS)
# Core tests that read git-ignored data; they fail on a fresh clone by construction.
LOCAL_DATA = {'test_server_replay_adapter', 'test_server_replay_regression', 'test_demo_casebook_20261030'}
# Node files that a Python harness launches with a loopback fixture; not runnable alone.
HARNESS_ONLY = {'test_hospital_operations_http.mjs'}
# Research checks sealed before the 2026-10-29~30 server changes; they fail with the current server.
EXPECTED_FAILURES = ROOT / 'config' / 'research_expected_failures_20261101.json'
# Tests must not reach live services with the team's keys (test_road_online_replay used Kakao when one was set).
API_KEYS = ('KAKAO_REST_KEY', 'DATA_GO_KR_KEY', 'AZURE_SPEECH_KEY')


def python_modules():
    locations = {}
    for directory in PYTHON_TEST_DIRS:
        for path in directory.glob('test_*.py'):
            if path.stem in locations:
                raise ValueError(f'Duplicate Python test module: {path.stem}')
            locations[path.stem] = directory
    return locations


def node_files():
    return sorted(path.relative_to(ROOT).as_posix() for path in BROWSER_TESTS.glob('test_*')
                  if path.is_file() and path.suffix in ('.mjs', '.cjs'))


def classify():
    core, local_data, research = [], [], []
    for name, directory in sorted(python_modules().items()):
        if name in LOCAL_DATA:
            local_data.append(name)
        elif directory == CORE_TESTS:
            core.append(name)
        else:
            research.append(name)
    nodes = [name for name in node_files() if Path(name).name not in HARNESS_ONLY]
    return dict(core=core, local_data=local_data, research=research, node=nodes)


def run_python(names, *, stream=None, verbosity=0):
    """Load each module separately so one import-time failure does not stop the rest."""
    summary = dict(modules=len(names), tests=0, failures=0, errors=0, skipped=0, failing={})
    with (nullcontext(stream) if stream is not None else open(os.devnull, 'w')) as sink:
        for name in names:
            try:
                suite = unittest.defaultTestLoader.loadTestsFromName(name)
            except Exception as exc:
                if stream is not None:
                    traceback.print_exc(file=sink)
                summary['errors'] += 1
                summary['failing'][name] = f'import: {type(exc).__name__}: {str(exc)[:160]}'
                continue
            result = unittest.TextTestRunner(stream=sink, verbosity=verbosity).run(suite)
            summary['tests'] += result.testsRun
            summary['failures'] += len(result.failures)
            summary['errors'] += len(result.errors)
            summary['skipped'] += len(result.skipped)
            problems = result.errors + result.failures
            if problems:
                last = problems[0][1].strip().splitlines()[-1]
                summary['failing'][name] = f'{len(result.failures)}F/{len(result.errors)}E {last[:160]}'
    return summary


def pinned_environment():
    """Team runtime and allocation baseline tests refuse to run outside this frozen runtime."""
    lock = json.loads((ROOT / 'config' / 'server_allocation_baseline_20261030.json').read_text(encoding='utf-8'))
    try:
        from server.allocation_baseline import current_environment
        actual = current_environment()
    except ImportError as exc:
        actual = f'unavailable: {exc}'
    return dict(match=actual == lock['environment'], expected=lock['environment'], actual=actual)


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
    parser.add_argument('suite', choices=('list', 'core', 'local-data', 'research', 'all', 'python'))
    parser.add_argument('modules', nargs='*', metavar='MODULE', help='Python test modules or qualified test names')
    parser.add_argument('--node', help='node executable for the UI tests (default: TEST_NODE or PATH)')
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument('-v', '--verbose', dest='verbosity', action='store_const', const=2)
    verbosity.add_argument('-q', '--quiet', dest='verbosity', action='store_const', const=0)
    args = parser.parse_intermixed_args(argv)
    groups = classify()
    if args.suite == 'python':
        if not args.modules:
            parser.error('the python suite requires at least one module name')
        known = set(groups['core'] + groups['local_data'] + groups['research'])
        unknown = sorted({name for name in args.modules if name.split('.', 1)[0] not in known})
        if unknown:
            parser.error(f'unknown Python test modules: {", ".join(unknown)}')
    elif args.modules or args.verbosity is not None:
        parser.error('module names and verbosity options require the python suite')
    if args.suite == 'list':
        print(json.dumps({k: dict(count=len(v), names=v) for k, v in groups.items()}, ensure_ascii=False, indent=1))
        return 0
    for key in API_KEYS:
        os.environ.pop(key, None)
    sys.path[:0] = [str(path) for path in (ROOT, ROOT / '.solver_deps', *PYTHON_TEST_DIRS)]
    os.chdir(ROOT)
    started = time.time()
    report = dict(suite=args.suite, environment=pinned_environment())
    if args.suite in ('core', 'all'):
        report['node'] = run_node(groups['node'], args.node)
    names = dict(core=groups['core'], local_data=groups['local_data'], research=groups['research'],
                 all=groups['core'] + groups['local_data'] + groups['research'], python=args.modules)
    selected = names[args.suite.replace('-', '_')]
    if args.suite == 'python':
        report['python'] = run_python(selected, stream=sys.stderr, verbosity=1 if args.verbosity is None else args.verbosity)
        expected = set()
    else:
        report['python'] = run_python(selected)
        expected = set(json.loads(EXPECTED_FAILURES.read_text(encoding='utf-8'))['modules'])
    unexpected = {k: v for k, v in report['python']['failing'].items() if k not in expected}
    report['expected_failures'] = len(set(report['python']['failing']) & expected)
    report['unexpected_failures'] = sorted(unexpected)
    report['seconds'] = round(time.time() - started, 1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    node_ok = report.get('node', {}).get('ok', True)
    return 0 if node_ok and not unexpected else 1


if __name__ == '__main__':
    raise SystemExit(main())
