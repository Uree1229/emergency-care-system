"""Identify an exact release at startup without trusting an unverified label."""
import json
import importlib.metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_dependencies(mode='full', root=None):
    """Check all exact server pins; voice-only intentionally needs no solver."""
    if mode == 'voice-only':
        return dict(checked=False, reason='voice-only', packages={})
    if mode != 'full':
        raise ValueError('Unknown runtime mode.')
    packages = {}
    for line in (Path(root or ROOT)/'requirements-server.txt').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, expected = line.split('==')
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        packages[name] = dict(expected=expected, actual=actual, matches=actual == expected)
    mismatches = [name for name, value in packages.items() if not value['matches']]
    if mismatches:
        raise ValueError('Pinned server dependency mismatch: ' + ', '.join(mismatches))
    return dict(checked=True, packages=packages)


def release_info(root=None):
    root = Path(root or ROOT).resolve()
    release = root.parent
    manifest = release / 'release-manifest.json'
    if not manifest.exists():
        return dict(status='unpackaged', verified=False,
                    note='Development source; no common release manifest.')
    # A neighbouring file is not enough: only the package's source directory
    # can claim the identity, and its complete inventory must match.
    if root.name != 'source':
        raise ValueError('Release manifest requires its source directory.')
    from .team_release_20261030 import verify_release
    try:
        report = verify_release(release)
        if not report['ok']:
            raise ValueError('Release source integrity failed: ' + '; '.join(report['issues'][:4]))
        value = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Release manifest is unavailable or invalid.') from error
    return dict(status='verified_at_startup', verified=True,
        release_id=report['release_id'], label=value.get('label', report['release_id']),
        manifest_sha256=report['manifest_sha256'], content_sha256=report['content_sha256'],
        file_count=report['file_count'], verification_scope='source inventory and bytes at process startup')
