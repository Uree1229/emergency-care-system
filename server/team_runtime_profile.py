"""Read-only compatibility check for the shared server launcher.

This profile keeps the original allocation v1 lock unchanged. It allows ONLY
CRLF/LF checkout differences in source bytes. Environment differences require
an explicit full-mode override and never count as an exact benchmark match.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import sqlite3

from . import allocation_baseline as baseline

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ID = 'team-runtime-profile-20261030-v1'
BASELINE_DIGEST = '642e0cf3b18c9129a3ac17af9cbc7d04e9cda12d47093a5af1b773b03290ac0d'
EXPECTED_PROFILE_DIGEST = '91b9767d651ec361bc248b9302257725c24704de2301efbfb17561e488727665'
PROFILE_PATH = 'config/team_runtime_profile_20261030.json'
_TOP_KEYS = {'schema_version', 'profile_id', 'baseline_id', 'baseline_manifest_sha256',
             'normalization', 'sources', 'profile_sha256'}
NORMALIZATION = 'CRLF bytes replaced with LF only; no other normalization'


def normalized(data):
    return data.replace(b'\r\n', b'\n')


def profile_digest(profile):
    value = {key: value for key, value in profile.items() if key != 'profile_sha256'}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: '+key)
        result[key] = value
    return result


def _load(value):
    return deepcopy(value) if isinstance(value, dict) else json.loads(
        Path(value).read_text(encoding='utf-8'), object_pairs_hook=_object)


def verify_profile(mode='full', allow_environment_difference=False, *, root=None, profile=None):
    """Return source/config/environment compatibility without modifying files.

    `root`/`profile` support isolated verification fixtures. Voice-only checks
    source and settings but does not import OR-Tools or validate runtime versions.
    The original baseline verifier is supplied its recorded environment ONLY
    for this source/settings stage; actual full-mode environment is checked below.
    """
    if mode not in ('full', 'voice-only'):
        raise ValueError('mode must be full or voice-only')
    if type(allow_environment_difference) is not bool:
        raise ValueError('allow_environment_difference must be bool')
    root = Path(root or ROOT).resolve()
    report = dict(ok=False, baseline_id=baseline.BASELINE_ID, profile_id=PROFILE_ID,
        exact_baseline_ok=False, source_equivalence='changed', environment_checked=False,
        environment_match=None, issues=[], warnings=[], mode=mode,
        source_validation_scope='original baseline manifest/settings and pinned CRLF/LF source profile',
        baseline_environment_stage='recorded environment supplied for source/settings validation only',
        benchmark_environment=False)
    try:
        lock = _load(root/'config/server_allocation_baseline_20261030.json')
        value = _load(root/PROFILE_PATH if profile is None else profile)
        if (not isinstance(value, dict) or set(value) != _TOP_KEYS
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['profile_id'] != PROFILE_ID or value['baseline_id'] != baseline.BASELINE_ID
                or value['baseline_manifest_sha256'] != BASELINE_DIGEST
                or value['normalization'] != NORMALIZATION
                or profile_digest(value) != value['profile_sha256']
                or value['profile_sha256'] != EXPECTED_PROFILE_DIGEST):
            raise ValueError('profile identity/schema/digest mismatch')
        if (not isinstance(lock, dict) or lock.get('manifest_sha256') != BASELINE_DIGEST
                or baseline.manifest_digest(lock) != BASELINE_DIGEST):
            raise ValueError('original baseline manifest differs')
        if not isinstance(value['sources'], dict) or set(value['sources']) != set(baseline.SOURCE_PATHS):
            raise ValueError('source whitelist differs')
        for path, entry in value['sources'].items():
            baseline._safe_path(root, path)
            if (not isinstance(entry, dict) or set(entry) != {
                    'baseline_sha256', 'baseline_bytes', 'lf_sha256', 'lf_bytes'}
                    or entry['baseline_sha256'] != lock['sources'][path]['sha256']
                    or entry['baseline_bytes'] != lock['sources'][path]['bytes']):
                raise ValueError('source profile differs from original baseline: '+path)
        base = baseline.verify(root, lock, environment=lock['environment'])
    except (OSError, ValueError, TypeError, KeyError) as error:
        report['issues'].append(dict(code='INVALID_RUNTIME_PROFILE', detail=str(error)))
        return report

    equivalent = True
    eol_paths = []
    for path, expected in value['sources'].items():
        try:
            raw = baseline._safe_path(root, path).read_bytes()
        except (OSError, ValueError):
            equivalent = False
            continue  # The original verifier contains the precise missing path.
        exact = (len(raw) == expected['baseline_bytes']
                 and hashlib.sha256(raw).hexdigest() == expected['baseline_sha256'])
        canonical = normalized(raw)
        if len(canonical) != expected['lf_bytes'] or hashlib.sha256(canonical).hexdigest() != expected['lf_sha256']:
            equivalent = False
        elif not exact:
            eol_paths.append(path)
    for issue in base['issues']:
        if issue['code'] == 'SOURCE_CHANGED' and issue['path'] in eol_paths:
            continue
        report['issues'].append(issue)
    report['source_equivalence'] = ('eol_only' if eol_paths else 'exact_bytes') if equivalent else 'changed'
    if eol_paths:
        report['warnings'].append(dict(code='EOL_ONLY_SOURCE_DIFFERENCE', paths=eol_paths,
            note='Shared runtime compatible; original byte-exact baseline remains different.'))
    if not equivalent and not report['issues']:
        report['issues'].append(dict(code='SOURCE_CONTENT_CHANGED'))
    report['source_count'] = len(value['sources'])
    report['profile_sha256'] = value['profile_sha256']
    report['baseline_manifest_sha256'] = BASELINE_DIGEST
    report['expected_environment'] = lock['environment']
    if mode == 'voice-only':
        report['actual_environment'] = dict(python=platform.python_version(),
            python_implementation=platform.python_implementation(), sqlite=sqlite3.sqlite_version,
            ortools=None)
        report['warnings'].append(dict(code='ENVIRONMENT_NOT_CHECKED_VOICE_ONLY',
            note='Planning and inquiry workers must be disabled. No exact allocation benchmark claim.'))
    else:
        report['environment_checked'] = True
        try:
            current = baseline.current_environment()
            report['actual_environment'] = current
            report['environment_match'] = current == lock['environment']
            if not report['environment_match']:
                item = dict(code='ENVIRONMENT_DIFFERENCE', expected=lock['environment'], actual=current)
                report['warnings' if allow_environment_difference else 'issues'].append(item)
        except (ImportError, AttributeError, OSError) as error:
            report['environment_match'] = False
            report['actual_environment'] = None
            report['issues'].append(dict(code='SOLVER_ENVIRONMENT_UNAVAILABLE', detail=type(error).__name__))
    report['ok'] = not report['issues'] and equivalent
    report['exact_baseline_ok'] = (report['ok'] and mode == 'full'
        and report['source_equivalence'] == 'exact_bytes' and report['environment_match'] is True)
    report['benchmark_environment'] = report['exact_baseline_ok']
    if mode == 'full' and allow_environment_difference and report['environment_match'] is False:
        report['warnings'].append(dict(code='NOT_FROZEN_BENCHMARK_ENVIRONMENT',
            note='Explicit runtime compatibility override; do not report this as the frozen experiment environment.'))
    return report
