"""Read-only lock verification for the current server allocation prototype.

This is a reproducibility record, not a runtime policy switch or a signature.
It never rewrites the lock, updates a database, or prevents a running service
from operating. Voice parsing and UI sources are deliberately outside this lock.
"""
import argparse
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import re
import sqlite3

BASELINE_ID = 'server-allocation-baseline-20261030-v1'
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / 'config/server_allocation_baseline_20261030.json'
SOURCE_PATHS = (
    'solver_improved.py', 'server/solver_adapter.py', 'server/worker.py',
    'server/planning.py', 'server/service.py', 'server/db.py', 'server/resource_accounting.py',
    'server/inquiry_workflow.py', 'server/inquiry_worker.py',
    'server/inquiry_policies.py', 'server/inquiry_observations.py',
    'server/inquiry_v3.py', 'simulation/observed_inquiry.py',
    'config/inquiry_v3_selected.json', 'server/transport_review.py',
    'server/transport_reassignment.py', 'server/patient_amendments.py',
    'server/treatment_binding.py', 'server/treatment_data.py',
    'config/treatment_requirements.json', 'server/travel.py',
)
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_TOP_KEYS = {'schema_version', 'baseline_id', 'created_at', 'scope', 'settings',
             'environment', 'sources', 'manifest_sha256'}
_SCOPE = {
    'system': 'current_server_allocation_prototype',
    'research_candidates': 'G2/G5/QI/QJ are separate research implementations; not activated by this lock',
    'excluded': ['voice parsing', 'voice command dictionaries', 'UI', 'live hospital data', 'clinical effectiveness'],
    'integration_dependencies': 'Capture voice/API/UI versions separately for each end-to-end verification run',
    'enforcement': 'explicit read-only check; no runtime policy switch or automatic lock refresh',
}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def manifest_digest(manifest):
    """Stable content identifier, excluding its own digest field."""
    return hashlib.sha256(_canonical({k: v for k, v in manifest.items() if k != 'manifest_sha256'})).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def _safe_path(root, relative):
    """Reject platform escapes, including symlinks resolving outside the root."""
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError('source path must be a relative POSIX path')
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or any(part in ('.', '..') for part in parts.parts):
        raise ValueError('source path escapes root')
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('source path resolves outside root')
    return resolved


def _tree(root, path):
    return ast.parse(_safe_path(root, path).read_text(encoding='utf-8-sig'), filename=path)


def _constant(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('missing literal constant: ' + name)


def _default(tree, function, parameter):
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
    values = dict(zip([arg.arg for arg in node.args.args][-len(node.args.defaults):], node.args.defaults))
    return ast.literal_eval(values[parameter])


def _attribute_assignment(tree, name):
    values = [ast.literal_eval(node.value) for node in ast.walk(tree)
              if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == name for t in node.targets)]
    if len(values) != 1:
        raise ValueError('expected exactly one solver parameter assignment: ' + name)
    return values[0]


def _model_limit(tree, key):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Gt)
                and isinstance(node.left, ast.Call) and isinstance(node.left.func, ast.Name)
                and node.left.func.id == 'len' and len(node.left.args) == 1):
            arg = node.left.args[0]
            if (isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name) and arg.value.id == 'model'
                    and isinstance(arg.slice, ast.Constant) and arg.slice.value == key):
                return ast.literal_eval(node.comparators[0])
    raise ValueError('missing model limit: ' + key)


def current_settings(root=ROOT):
    """Extract selected defaults without importing voice, API, DB or service code."""
    root = Path(root).resolve()
    solver, adapter, worker, workflow, service, travel = (
        _tree(root, path) for path in ('solver_improved.py', 'server/solver_adapter.py',
            'server/worker.py', 'server/inquiry_workflow.py', 'server/service.py', 'server/travel.py'))
    return {
        'solver': {
            'entrypoint': 'server.worker.bounded_solve -> solver_improved.solve',
            'policy': _default(solver, 'solve', 'policy'),
            'seconds': _default(worker, 'run_once', 'seconds'),
            'solver_default_seconds': _default(solver, 'solve', 'seconds'),
            'priority_groups': list(_constant(solver, 'GROUPS')),
            'objective': 'lexicographic unassigned counts by groups 4,2,1; then start-minus-release sums by groups 4,2,1',
            'search_workers': _attribute_assignment(solver, 'num_search_workers'),
            'random_seed': _attribute_assignment(solver, 'random_seed'),
            'fallback': 'existing validated greedy incumbent (seconds=0), destination locks preserved',
            'time_unit': 'integer minutes; model time and arrival use ceil(seconds/60)',
            'max_patients': _model_limit(worker, 'patients'),
            'max_hospitals': _model_limit(worker, 'capacities'),
        },
        'model': {
            'resource_types': list(_constant(adapter, 'RESOURCE_TYPES')),
            'horizon_minutes': _constant(adapter, 'HORIZON_MINUTES'),
            'default_treatment_seconds': _constant(adapter, 'DEFAULT_DURATION'),
            'default_max_wait_seconds': _constant(adapter, 'DEFAULT_MAX_WAIT'),
            'operational_capacity': 'hospital-input resource capacity; public registered counts are not substituted',
            'candidate_confirmation': 'valid patient-version-specific ACCEPTED reply required for initial assignment',
            'destination_change': 'human confirmation with new valid acceptance; never automatic',
        },
        'inquiry': {
            'policy': _default(workflow, 'attach', 'policy_name'),
            'policy_version': '1',
            'batch_size': _constant(workflow, 'BATCH_SIZE'),
            'wait_seconds': _constant(workflow, 'WAIT_SECONDS'),
            'max_contacts': _constant(workflow, 'MAX_CONTACTS'),
            'deadline_seconds': _constant(workflow, 'DEADLINE_SECONDS'),
        },
        'service': {key.lower(): _constant(service, key)
                    for key in ('ACCEPT_TTL', 'PENDING_WARN', 'RETRY_MIN', 'STALE_AFTER')},
        'travel': {'road_factor': _constant(travel, 'ROAD_FACTOR'),
            'speed_kmh': _constant(travel, 'SPEED_KMH'),
            'verification_provider': 'synthetic (use_kakao=False)',
            'live_provider': 'environment-dependent Kakao with synthetic fallback; record per run'},
        'clinical_policy': 'experimental configuration; not clinically validated triage or waiting-time thresholds',
    }


def current_environment():
    # Resolve exactly the OR-Tools package chosen by the real server solver.
    import solver_improved  # noqa: F401
    import ortools
    return {'python': platform.python_version(), 'python_implementation': platform.python_implementation(),
            'ortools': ortools.__version__, 'sqlite': sqlite3.sqlite_version}


def verify(root=ROOT, manifest=None, *, environment=None):
    """Return a drift report, reading files only. Fixtures may supply root/dict.

    Runtime version differences are a failed reproducibility check, not proof
    that an algorithm is medically wrong. The service is not changed or stopped.
    """
    root = Path(root).resolve()
    issues = []
    def issue(code, path, expected=None, actual=None):
        issues.append(dict(code=code, path=path, expected=expected, actual=actual))
    try:
        if manifest is None:
            manifest = root / 'config/server_allocation_baseline_20261030.json'
        if isinstance(manifest, dict):
            lock = deepcopy(manifest)
        else:
            lock = json.loads(Path(manifest).read_text(encoding='utf-8'), object_pairs_hook=_object)
        if not isinstance(lock, dict) or set(lock) != _TOP_KEYS:
            raise ValueError('unexpected manifest fields')
        if type(lock['schema_version']) is not int or lock['schema_version'] != 1:
            raise ValueError('unsupported schema_version')
        if lock['baseline_id'] != BASELINE_ID or lock['scope'] != _SCOPE:
            raise ValueError('baseline identity or scope differs')
        if not isinstance(lock['created_at'], str) or not lock['created_at']:
            raise ValueError('missing creation timestamp')
        if not isinstance(lock['settings'], dict) or not isinstance(lock['environment'], dict):
            raise ValueError('settings/environment must be objects')
        if not isinstance(lock['sources'], dict):
            raise ValueError('sources must be an object')
        # Validate every supplied path before any source read, even an extra key.
        for path, entry in lock['sources'].items():
            _safe_path(root, path)
            if (not isinstance(entry, dict) or set(entry) != {'sha256', 'bytes'}
                    or not isinstance(entry['sha256'], str) or not _HEX.fullmatch(entry['sha256'])
                    or type(entry['bytes']) is not int or entry['bytes'] < 0):
                raise ValueError('invalid source fingerprint: ' + path)
        if set(lock['sources']) != set(SOURCE_PATHS):
            raise ValueError('required source whitelist differs')
        if not isinstance(lock['manifest_sha256'], str) or not _HEX.fullmatch(lock['manifest_sha256']):
            raise ValueError('invalid manifest digest')
        if manifest_digest(lock) != lock['manifest_sha256']:
            raise ValueError('manifest content digest mismatch')
    except (OSError, ValueError, TypeError) as error:
        issue('INVALID_MANIFEST', 'manifest', actual=str(error))
        return {'baseline_id': BASELINE_ID, 'ok': False, 'issues': issues}

    for relative, expected in lock['sources'].items():
        try:
            data = _safe_path(root, relative).read_bytes()
        except (OSError, ValueError):
            issue('SOURCE_MISSING_OR_UNREADABLE', relative, expected=expected)
            continue
        actual = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        if expected != actual:
            issue('SOURCE_CHANGED', relative, expected, actual)
    try:
        actual = current_settings(root)
        if lock['settings'] != actual:
            issue('SETTINGS_CHANGED', 'settings', lock['settings'], actual)
    except (OSError, ValueError, TypeError, KeyError, StopIteration, SyntaxError) as error:
        issue('SETTINGS_UNREADABLE', 'settings', actual=str(error))
    try:
        actual = current_environment() if environment is None else environment
        if lock['environment'] != actual:
            issue('ENVIRONMENT_CHANGED', 'environment', lock['environment'], actual)
    except (ImportError, AttributeError, OSError) as error:
        issue('ENVIRONMENT_UNAVAILABLE', 'environment', actual=type(error).__name__)
    return {'baseline_id': BASELINE_ID, 'ok': not issues, 'issues': issues,
            'manifest_sha256': lock['manifest_sha256'], 'source_count': len(lock['sources'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check',))
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args(argv)
    result = verify()
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(('PASS ' if result['ok'] else 'FAIL ') + result['baseline_id'])
        for item in result['issues']:
            print(item['code'] + ': ' + item['path'])
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
