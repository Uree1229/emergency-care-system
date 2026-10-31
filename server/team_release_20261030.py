"""Create or verify a source-only team release in a NEW directory.

This tool never installs packages, runs a policy, opens a database, or starts a
server. SHA-256 identifies the reviewed bytes; it is not a digital signature.
Run with Python -B so verification does not create bytecode in the payload.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'team-release-20261030-v1'
MANIFEST = 'release-manifest.json'
PAYLOAD = 'source'
ENTRYPOINTS = ('server.team_runtime', 'server.team_lan', 'server.team_readiness',
    'simulation.viewer_server', 'server.team_release_20261030')
STATIC_EXTENSIONS = frozenset(('.html', '.js', '.mjs', '.css', '.svg', '.png', '.jpg', '.jpeg', '.ico', '.woff', '.woff2', '.geojson'))
VIEWER_DATA = (
    'map_replay_data.json', 'online_allocation_data.json', 'road_online_allocation_data.json',
    'server_replay_data.json', 'transport_replay_data.json', 'voice_replay_data_20261030.json',
    'voice_road_replay_data_20261030.json',
)
DOCS = (
    'TEAM_RELEASE_20261030.md', 'TEAM_COMMON_RUNTIME_20261030.md', 'TEAM_CONNECTION_CHECK_20261030.md',
    'TEAM_DATABASE_BACKUP_20261030.md', 'RECOVERY_AND_RELEASE_20261030.md',
    'TEAM_RECOMMENDATION_INTEGRATION.md', 'TEAM_FEEDBACK_API_HANDOFF_20261029.md',
    'ALLOCATION_INPUT_REVIEW_API_20261030.md', 'DATA_AND_RESOURCE_SCOPE_20261030.md',
    'DEMO_RUNBOOK_20261030.md', 'FINAL_ALGORITHM_DECISION_20261030.md', 'WAITING_PROTECTION_20261030.md',
    'TEAM_LAN_20261102.md', 'TEAM_START_20261102.md', 'MAIN_UI_INTEGRATION_20261030.md',
    'CURRENT_REMAINING_WORK_20261030.md', 'TEAM_IMPLEMENTATION.md', 'VOICE_MID_TRIGGER_20261030.md',
    'VOICE_TREATMENT_REVIEW_API_20261030.md', 'ENROUTE_PATIENT_CHANGE_20261030.md',
    'HOSPITAL_OPERATIONS_20261030.md', 'USABILITY_EVALUATION_20261030.md', 'API.md',
    'SERVER_INTEGRATION_20261030.md', 'SERVER_DOMAIN_20261102.md',
)
PYTHON_TESTS = (
    'test_team_release_20261030.py', 'test_team_release_info.py', 'test_team_runtime.py', 'test_team_runtime_profile.py',
    'test_team_backup.py', 'test_team_backup_resume.py', 'test_team_connection_check.py',
    'test_team_connection_races.py', 'test_team_connection_recovery.py', 'test_team_demo.py',
    'test_allocation_input_review.py', 'test_allocation_input_review_http.py', 'test_allocation_input_review_integration.py',
    'test_important_events.py', 'test_enroute_patient_review.py', 'test_recommendation_contract.py',
    'test_voice_treatment_review.py', 'test_voice_treatment_review_http.py',
    'test_patient_amendments.py', 'test_patient_commit_bridge.py', 'test_patient_update_and_inbox_api.py',
    'test_server.py', 'test_inquiry_workflow.py',
    'test_team_lan.py', 'test_team_readiness.py', 'test_enroute_notifications_20261030.py',
    'test_data_provenance_20261030.py', 'test_hospital_operations_http.py',
)
EXACT_FILES = ('solver_improved.py', 'requirements-server.txt', 'README.md', 'TEAM_START.ps1', 'TEAM_VERIFY.ps1',
    'datasets/demo/seoul_public_catalog.json', 'datasets/examples/cau_public_flow_cases.json',
    'datasets/examples/cau_seoul_patient_001.json')
EXACT_DOC_FILES = ('docs/api/SERVER_SCHEMA_20261102.json',)
REQUIRED = frozenset((*EXACT_FILES, *EXACT_DOC_FILES, 'server/team_runtime.py', 'server/team_runtime_profile.py',
    'server/team_release_20261030.py', 'config/server_allocation_baseline_20261030.json',
    'config/team_runtime_profile_20261030.json', 'paramedic-ui/index.html', 'patient-ui/index.html',
    'hospital-ui/index.html', 'simulation/viewer_server.py', 'simulation/observed_inquiry.py',
    'server/team_release_info.py', 'test_team_release_info.py', 'docs/TEAM_RELEASE_20261030.md',
    'server/team_lan.py', 'server/team_readiness.py',
    'config/team_release_source_refs_20261030.json', 'paramedic-ui/evaluation-cases.json',
    'simulation/viewer/index.html', 'simulation/viewer/demo.html',
    *(f'simulation/viewer/{name}' for name in VIEWER_DATA), 'test_team_release_20261030.py'))
SKIP_PARTS = frozenset(('.git', '.codex-build', '.solver_deps', 'node_modules', '__pycache__',
    'data', 'outputs', 'simulation_results', 'route_cache', '.venv', 'venv', '.claude'))
POLICY = dict(inquiry='fixed_3', assignment='CP-SAT', objective='severity_first', solver_seconds=3,
    research_policy_activation=False, data='public identity catalog and synthetic/saved viewer records only',
    databases=False, credentials=False, dependency_binaries=False, installation=False,
    runtime_artifact_exclusions=['source/data/**', 'source/.solver_deps/**', 'source/**/*.pyc'])


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_record(path):
    h = hashlib.sha256(); size = 0
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk); size += len(chunk)
    return dict(bytes=size, sha256=h.hexdigest())


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key: '+key)
        value[key] = item
    return value


def _json(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def relative_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Unsafe relative path.')
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or any(part in ('', '.', '..') for part in name.split('/')):
        raise ValueError('Unsafe relative path: '+name)
    return p


def _reparse(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def safe_file(root, name):
    root = Path(root).resolve(); parts = relative_name(name).parts; target = root
    for part in parts:
        target /= part
        if _reparse(target):
            raise ValueError('Symlink/junction/reparse input is not allowed: '+name)
    if not target.is_file() or not target.resolve().is_relative_to(root):
        raise ValueError('Expected an ordinary source file: '+name)
    return target


def role(name):
    p = relative_name(name); parts = p.parts; suffix = p.suffix.lower()
    if any(part in SKIP_PARTS or part.startswith('.env') for part in parts):
        return None
    if name in EXACT_DOC_FILES: return 'documentation'
    if name in EXACT_FILES: return 'runtime-or-public-fixture'
    if name in PYTHON_TESTS: return 'verification-test'
    if len(parts) == 1 and suffix == '.mjs' and re.fullmatch(r'test_(?:paramedic_.+|hospital_.+|patient_workflow_.+)\.mjs', name): return 'verification-test'
    if len(parts) == 2 and parts[0] in ('server', 'simulation') and suffix in ('.py', '.mjs'): return 'source'
    if len(parts) == 2 and parts[0] == 'config' and suffix == '.json': return 'configuration'
    if parts[0] in ('paramedic-ui', 'patient-ui', 'hospital-ui') and (suffix in STATIC_EXTENSIONS or len(parts) == 2 and parts[-1] in ('README.md', 'manifest.json') or name == 'paramedic-ui/evaluation-cases.json'): return 'ui'
    if len(parts) >= 3 and parts[:2] == ('server', 'static') and (suffix in STATIC_EXTENSIONS or name == 'server/static/public-flow-demo.json'): return 'server-static'
    if len(parts) >= 3 and parts[:2] == ('simulation', 'viewer') and (suffix in STATIC_EXTENSIONS or len(parts) == 3 and parts[-1] in VIEWER_DATA or name == 'simulation/viewer/assets/README.md'): return 'viewer'
    if len(parts) == 2 and parts[0] == 'docs' and parts[-1] in DOCS: return 'documentation'
    if name in ('server/README.md', 'simulation/README.md'): return 'documentation'
    return None


def _walk(directory, *, skip=False, runtime=False):
    if not directory.exists(): return
    if _reparse(directory): raise ValueError('Reparse directory is not allowed: '+str(directory))
    for base, dirs, files in os.walk(directory, followlinks=False):
        base = Path(base)
        dirs[:] = sorted(name for name in dirs if (not skip or name not in SKIP_PARTS)
            and not (runtime and base == directory and name in ('data', '.solver_deps')))
        for name in dirs:
            if _reparse(base/name): raise ValueError('Reparse directory is not allowed: '+str(base/name))
        for name in sorted(files):
            path = base/name
            if _reparse(path): raise ValueError('Reparse file is not allowed: '+str(path))
            if runtime and path.suffix == '.pyc': continue
            yield path


def selected_paths(root):
    root = Path(root).resolve(); candidates = set()
    for directory in ('server', 'simulation', 'config', 'docs'):
        folder = root/directory
        if folder.exists():
            if _reparse(folder): raise ValueError('Reparse source directory: '+directory)
            candidates.update(p.relative_to(root).as_posix() for p in folder.iterdir() if p.is_file())
    for directory in ('paramedic-ui', 'patient-ui', 'hospital-ui', 'server/static', 'simulation/viewer'):
        candidates.update(p.relative_to(root).as_posix() for p in _walk(root/directory, skip=True))
    candidates.update(p.name for p in root.iterdir() if p.is_file())
    candidates.update(EXACT_FILES); candidates.update(EXACT_DOC_FILES)
    names = sorted(name for name in candidates if role(name))
    if len({name.casefold() for name in names}) != len(names): raise ValueError('Case-insensitive path collision.')
    missing = sorted(REQUIRED-set(names))
    if missing: raise ValueError('Required release inputs missing: '+', '.join(missing))
    for name in names: safe_file(root, name)
    return names


def inventory(root, names):
    return {name:{**file_record(safe_file(root, name)), 'role':role(name)} for name in names}


class _Assets(HTMLParser):
    def __init__(self): super().__init__(); self.refs = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script' and a.get('src'): self.refs.append(a['src'])
        if tag == 'link' and 'stylesheet' in a.get('rel', '').lower().split() and a.get('href'): self.refs.append(a['href'])


def dependency_check(root, names):
    """Static local import and script/style closure; no payload code executes."""
    root = Path(root).resolve(); included = set(names); errors = []; seen = set(); external = set(); todo = list(ENTRYPOINTS)
    def module_file(module):
        stem = module.replace('.', '/')
        return next((name for name in (stem+'.py', stem+'/__init__.py') if name in included), None)
    while todo:
        module = todo.pop()
        if module in seen: continue
        seen.add(module); name = module_file(module)
        if name is None: errors.append('Missing local Python module: '+module); continue
        tree = ast.parse(safe_file(root, name).read_text(encoding='utf-8-sig'), filename=name)
        package = module.split('.') if name.endswith('/__init__.py') else module.split('.')[:-1]
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import): targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = package[:len(package)-node.level+1] if node.level else []
                stem = '.'.join(prefix+([node.module] if node.module else []))
                targets = [stem] if node.module else [stem+'.'+alias.name for alias in node.names]
                if node.module:
                    targets += [stem+'.'+alias.name for alias in node.names if module_file(stem+'.'+alias.name)]
            for target in targets:
                top = target.split('.')[0]
                if top in ('server', 'simulation', 'solver_improved'): todo.append(target)
                elif top and top not in sys.stdlib_module_names and top != '__future__': external.add(top)
    test_helpers = set()
    for name in sorted(included):
        if not name.startswith('test_') or not name.endswith('.py'): continue
        for node in ast.walk(ast.parse(safe_file(root, name).read_text(encoding='utf-8-sig'), filename=name)):
            targets = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for module in targets:
                if module.startswith('test_'):
                    test_helpers.add(module)
                    if module.replace('.', '/')+'.py' not in included: errors.append(f'Missing Python test helper: {name} -> {module}')
    for name in sorted(included):
        if not name.endswith(('.html', '.js', '.mjs')): continue
        text = safe_file(root, name).read_text(encoding='utf-8-sig'); refs = []
        if name.endswith('.html'):
            parser = _Assets(); parser.feed(text); refs = parser.refs
        else:
            refs = re.findall(r"(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)['\"]([./][^'\"]+)['\"]", text)
        for ref in refs:
            url = urlsplit(ref)
            if url.scheme or url.netloc: continue
            path = unquote(url.path)
            if not path: continue
            if path.startswith('/'):
                routes = {'paramedic':'paramedic-ui', 'patient':'patient-ui', 'hospital':'hospital-ui'}
                pieces = path.strip('/').split('/'); target = root/routes[pieces[0]]/Path(*pieces[1:]) if pieces[0] in routes else root/'server/static'/path.lstrip('/')
            else: target = (root/name).parent/path
            target = target.resolve()
            if not target.is_relative_to(root) or target.relative_to(root).as_posix() not in included:
                errors.append(f'Missing local script/style/module: {name} -> {ref}')
    pins = set(re.findall(r'^([A-Za-z0-9_.-]+)==[^\s]+$', (root/'requirements-server.txt').read_text(encoding='utf-8'), re.MULTILINE))
    import_packages = {'ortools':'ortools', 'absl':'absl-py', 'immutabledict':'immutabledict', 'numpy':'numpy',
        'pandas':'pandas', 'google':'protobuf', 'dateutil':'python-dateutil', 'six':'six',
        'typing_extensions':'typing_extensions', 'tzdata':'tzdata'}
    for module in sorted(external):
        if import_packages.get(module) not in pins: errors.append('External runtime import has no mapped exact requirement pin: '+module)
    return dict(ok=not errors, entrypoints=list(ENTRYPOINTS), local_modules=sorted(seen), test_helper_modules=sorted(test_helpers), external_import_roots=sorted(external), errors=errors,
        scope='Static Python imports and literal HTML script/style + JavaScript module imports; dynamic URLs and optional research commands are not certified.')


def source_profile(root):
    from .team_runtime_profile import verify_profile
    value = verify_profile('voice-only', root=root)
    return {key:value.get(key) for key in ('ok', 'baseline_id', 'profile_id', 'source_equivalence', 'source_count', 'profile_sha256', 'baseline_manifest_sha256', 'issues')}


def _manifest(files, label, dependencies, profile):
    content = dict(revision=REVISION, policy=POLICY, files=files)
    content_id = digest(content)
    value = dict(schema_version=1, revision=REVISION, label=label, release_id='team-20261030-'+content_id[:16], content_sha256=content_id,
        created_at=datetime.now(timezone.utc).isoformat(), policy=POLICY, file_count=len(files), total_bytes=sum(v['bytes'] for v in files.values()),
        files=files, dependencies=dependencies, baseline_source_profile=profile,
        preservation=dict(source_bytes='exact SHA-256, no EOL normalization', original_sources_modified=False,
            existing_databases_accessed=False, runtime_servers_accessed=False, package_install_performed=False),
        exclusions=['DB/data, WAL/SHM/locks', '.env/credentials/keys', '.solver_deps/venv/node_modules', '.codex-build/private outputs',
            'research raw results/fixtures and route caches', 'installed Python/dependency binaries'],
        limits=['Not a digital signature or proof of authorship.', 'Full runtime environment/install and separate-PC verification are separate checks.',
            'Saved viewer JSON is included explicitly; it is not live clinical/hospital data.', 'Research source code is carried for traceability; no new policy or experiment is run.'])
    value['manifest_sha256'] = digest(value)
    return value


def verify_release(release, *, source_root=None):
    release = Path(release).resolve(); report = dict(ok=False, revision=REVISION, issues=[])
    try:
        if _reparse(release/MANIFEST) or _reparse(release/PAYLOAD): raise ValueError('Reparse release metadata/payload is not allowed.')
        with (release/MANIFEST).open(encoding='utf-8') as stream: manifest = json.load(stream, object_pairs_hook=_object)
        if not isinstance(manifest, dict): raise ValueError('Release manifest must be an object.')
        if manifest.get('revision') != REVISION or type(manifest.get('schema_version')) is not int or manifest.get('schema_version') != 1 or manifest.get('policy') != POLICY: raise ValueError('Release revision/policy mismatch.')
        expected_digest = manifest.get('manifest_sha256'); unsigned = {k:v for k,v in manifest.items() if k != 'manifest_sha256'}
        if digest(unsigned) != expected_digest: raise ValueError('Manifest digest mismatch.')
        files = manifest['files']
        if not isinstance(files, dict) or not REQUIRED <= set(files): raise ValueError('Required payload paths missing from manifest.')
        if len({name.casefold() for name in files}) != len(files): raise ValueError('Case-insensitive manifest path collision.')
        for name, item in files.items():
            if not role(name) or set(item) != {'bytes', 'sha256', 'role'} or item['role'] != role(name) or type(item['bytes']) is not int or item['bytes'] < 0 or not re.fullmatch('[0-9a-f]{64}', str(item['sha256'])): raise ValueError('Invalid allowlist/file record: '+str(name))
        content_id = digest(dict(revision=REVISION, policy=POLICY, files=files))
        if manifest.get('content_sha256') != content_id or manifest.get('release_id') != 'team-20261030-'+content_id[:16] or manifest.get('file_count') != len(files) or manifest.get('total_bytes') != sum(v['bytes'] for v in files.values()): raise ValueError('Release content identity/count mismatch.')
        payload = release/PAYLOAD
        actual_names = {p.relative_to(payload).as_posix() for p in _walk(payload, runtime=True)}
        for name in sorted(actual_names-set(files)): report['issues'].append('Unexpected payload file: '+name)
        for name in sorted(set(files)-actual_names): report['issues'].append('Missing payload file: '+name)
        for name in sorted(actual_names & set(files)):
            if file_record(safe_file(payload, name)) != {k:files[name][k] for k in ('bytes', 'sha256')}: report['issues'].append('Payload bytes changed: '+name)
        report['dependencies'] = dependency_check(payload, files)
        report['issues'].extend(report['dependencies']['errors'])
        if manifest.get('dependencies') != report['dependencies']: report['issues'].append('Recorded static dependency evidence differs.')
        report['baseline_source_profile'] = source_profile(payload)
        if not report['baseline_source_profile']['ok']: report['issues'].append('Frozen allocation source/profile check failed.')
        if manifest.get('baseline_source_profile') != report['baseline_source_profile']: report['issues'].append('Recorded frozen-source profile evidence differs.')
        if source_root is not None:
            current = inventory(source_root, selected_paths(source_root))
            report['source_matches'] = current == files
            if not report['source_matches']: report['issues'].append('Source root differs from the frozen release inventory/bytes.')
        report.update(release_id=manifest['release_id'], manifest_sha256=expected_digest, content_sha256=content_id,
            file_count=len(files), total_bytes=manifest['total_bytes'])
    except (OSError, ValueError, TypeError, KeyError, SyntaxError) as error:
        report['issues'].append(str(error))
    report['ok'] = not report['issues']
    return report


def package_release(output, *, source_root=ROOT, label='team-20261030-v1'):
    root = Path(source_root).resolve(); output = Path(output).resolve()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', label): raise ValueError('Release label must be a short ASCII identifier.')
    if output.exists(): raise ValueError('Use a NEW release output directory; existing files are never overwritten.')
    if output == root or any(output.is_relative_to(root/p) for p in ('server', 'simulation', 'config', 'docs', 'paramedic-ui', 'patient-ui', 'hospital-ui', 'datasets')): raise ValueError('Output must not be inside a packaged source tree.')
    names = selected_paths(root); before = inventory(root, names); dependencies = dependency_check(root, names)
    if not dependencies['ok']: raise ValueError('; '.join(dependencies['errors']))
    profile = source_profile(root)
    if not profile['ok']: raise ValueError('Frozen allocation profile failed: '+json.dumps(profile['issues']))
    manifest = _manifest(before, label, dependencies, profile)
    output.mkdir(parents=True, exist_ok=False); _json(output/MANIFEST, manifest)
    for name in names:
        original = safe_file(root, name); target = output/PAYLOAD/name
        target.parent.mkdir(parents=True, exist_ok=True)
        with original.open('rb') as src, target.open('xb') as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b''): dst.write(chunk)
    report = verify_release(output, source_root=root)
    report['source_stable_during_snapshot'] = inventory(root, selected_paths(root)) == before
    if not report['source_stable_during_snapshot']:
        report['issues'].append('Original sources changed while taking the snapshot.'); report['ok'] = False
    _json(output/'verification.json', report)
    if not report['ok']: raise ValueError('Release snapshot failed; preserve its manifest/verification and use a new output path.')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    create = commands.add_parser('package', help='Create a new allowlisted snapshot; no installation or DB access.')
    create.add_argument('--output', required=True); create.add_argument('--source-root', default=str(ROOT)); create.add_argument('--label', default='team-20261030-v1')
    verify = commands.add_parser('verify', help='Read-only exact-byte payload verification, optionally compare a source root.')
    verify.add_argument('--release', required=True); verify.add_argument('--source-root')
    args = parser.parse_args(argv)
    try:
        report = package_release(args.output, source_root=args.source_root, label=args.label) if args.action == 'package' else verify_release(args.release, source_root=args.source_root)
    except (OSError, ValueError) as error:
        print(json.dumps(dict(ok=False, error=str(error)), ensure_ascii=False)); return 1
    print(json.dumps(report, ensure_ascii=False, indent=2)); return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
