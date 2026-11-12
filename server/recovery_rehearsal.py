"""Rehearse release, preserving migration, SQLite backup and recovery in a NEW folder.

No existing data DB or running server is touched. CommonRuntime remains loopback
only. Dependencies are reused explicitly, without downloads or public hosting.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time
import traceback
from urllib.request import urlopen

from .team_release_info import SERVER_REQUIREMENTS

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'recovery-rehearsal-20261030-v1'
VOLATILE_TABLES = {'planning_state', 'planning_results', 'sqlite_sequence'}
LEGACY_COLUMNS = ('requires_review_version', 'review_response_fingerprint')
EXTENSIONS = {'.py', '.json', '.html', '.js', '.mjs', '.css', '.svg', '.png',
              '.jpg', '.jpeg', '.woff', '.woff2', '.ico'}


def _json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Artifact path must remain inside its dedicated output folder.')
    return path


def _readonly(path):
    path = Path(path).resolve()
    conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=10)
    conn.execute('PRAGMA query_only=ON')
    return conn


def logical_state(path, projection=None):
    """Canonical business rows, excluding explicitly transient planning caches.

    Projection preserves ALL old columns when testing an additive migration.
    Blob cells are tagged to avoid lossy conversion; rows use canonical sorting.
    """
    with closing(_readonly(path)) as conn:
        integrity = [row[0] for row in conn.execute('PRAGMA integrity_check')]
        if integrity != ['ok']:
            raise ValueError('SQLite integrity check failed.')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite foreign-key check failed.')
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                 if not r[0].startswith('sqlite_') and r[0] not in VOLATILE_TABLES]
        tables = {}
        for name in names if projection is None else sorted(projection):
            # Identifiers come only from SQLite metadata or a previous metadata
            # projection, and are quoted rather than treated as SQL fragments.
            quote = lambda value: '"'+value.replace('"','""')+'"'
            columns = [r[1] for r in conn.execute('PRAGMA table_info('+quote(name)+')')] if projection is None else projection[name]
            rows = []
            for row in conn.execute('SELECT '+','.join(map(quote,columns))+' FROM '+quote(name)):
                rows.append([{'sqlite_blob_hex': v.hex()} if isinstance(v,bytes) else v for v in row])
            rows.sort(key=lambda row: json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')))
            tables[name] = dict(columns=columns, count=len(rows), sha256=_hash(json.dumps(
                rows,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()))
    return dict(sha256=_hash(json.dumps(tables,sort_keys=True,separators=(',',':')).encode()), tables=tables,
                excluded_tables=sorted(VOLATILE_TABLES))


def backup_database(source, target, *, artifact_root):
    """SQLite snapshot API, safe with WAL; target must be a new artifact path."""
    from .team_runtime import inspect_database
    source, target = _inside(artifact_root,source), _inside(artifact_root,target)
    inspect_database(source)
    # Validate the source before creating any destination file.
    logical_state(source)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.touch(exist_ok=False)
    with closing(_readonly(source)) as original, closing(sqlite3.connect(target)) as destination:
        original.backup(destination)
    inspect_database(target)
    state = logical_state(target)
    return dict(source=str(source),target=str(target),sqlite_backup_api=True,
                file_sha256=_hash(target.read_bytes()),business_state=state)


def migrate_fixture(path):
    """Make only this synthetic fixture legacy, then prove additive preservation."""
    from . import db, service, team_runtime, voice_input
    team_runtime.inspect_database(path)
    with team_runtime.DatabaseLease(path):
        conn = db.connect(str(path))
        try:
            now = int(time.time())
            service.create_patient(conn,now,'RECOVERY-FIXTURE',dict(reporter_role='transporter',
                consent=True,priority=2,priority_source='expert',lat=35.16,lon=126.85,
                symptoms='synthetic recovery fixture',auto_inquiry=False,
                extra={'synthetic_recovery_fixture':True}))
            service.send_inquiries(conn,now,'RECOVERY-FIXTURE',['H01'])
            service.set_resource(conn,now,'H02','ed_bed',7,2,status='limited',source='synthetic')
            voice_input.create(conn,now,dict(actor_id='SYNTHETIC-RECOVERY',client_request_id='recovery-voice'))
            # This is a manufactured old-schema fixture, not an authentic past
            # release DB. No existing application DB is downgraded.
            for column in LEGACY_COLUMNS:
                conn.execute('ALTER TABLE inquiries DROP COLUMN '+column)
            before = logical_state(path)
            projection = {name: value['columns'] for name,value in before['tables'].items()}
            db.init(conn)
            projected_after = logical_state(path,projection)
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(inquiries)')}
            after = logical_state(path)
            db.init(conn)
            repeated = logical_state(path)
        finally:
            conn.close()
    return dict(pass_=before == projected_after and set(LEGACY_COLUMNS) <= columns and after == repeated,
        manufactured_legacy_fixture=True,added_columns=list(LEGACY_COLUMNS),
        before_projection=before,after_projection=projected_after,after=after,
        repeated_migration_preserved=after == repeated)


def copy_release(output):
    output = Path(output)
    output.mkdir(exist_ok=False)
    sources = []
    for directory in ('server','config','simulation','patient-ui','hospital-ui','paramedic-ui'):
        sources.extend(p for p in (ROOT/directory).rglob('*') if p.is_file()
            and p.suffix.lower() in EXTENSIONS and '__pycache__' not in p.parts
            and 'node_modules' not in p.parts and not p.is_symlink())
    sources.extend(ROOT/name for name in ('solver_improved.py', SERVER_REQUIREMENTS))
    manifest = {}
    for source in sorted(sources):
        relative = source.relative_to(ROOT)
        content = source.read_bytes()
        target = output/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(content)
        manifest[str(relative).replace('\\','/')] = dict(bytes=len(content),sha256=_hash(content))
    return manifest


def manifest_changes(root,manifest):
    changes = []
    for name,expected in manifest.items():
        try:
            actual = _hash((Path(root)/name).read_bytes())
        except OSError:
            actual = None
        if actual != expected['sha256']:
            changes.append(dict(path=name,before_sha256=expected['sha256'],after_sha256=actual))
    return changes


def _environment(solver_deps):
    env = os.environ.copy()
    # Do not inherit unrelated project imports; reuse only this explicitly
    # identified installed dependency directory and the selected Python bundle.
    env['PYTHONPATH'] = str(Path(solver_deps).resolve())
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def _get(base_url,path):
    if not base_url.startswith('http://127.0.0.1:'):
        raise ValueError('Only private loopback HTTP is allowed.')
    with urlopen(base_url+path,timeout=5) as response:
        return json.load(response)


def dependency_versions():
    result = {}
    for line in (ROOT/SERVER_REQUIREMENTS).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name,expected = line.split('==')
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        result[name] = dict(expected=expected,actual=actual,matches=actual==expected)
    return result


class ChildRuntime:
    def __init__(self,root,copy,python,env,path,label,mode):
        self.root,self.copy,self.python,self.env = root,copy,python,env
        self.path,self.mode = path,mode
        self.directory = root/label
        self.directory.mkdir(exist_ok=False)
        self.process = self.log = self.info = None

    def __enter__(self):
        self.log = (self.directory/'process.log').open('wb')
        self.process = subprocess.Popen([self.python,'-u','-m','server.recovery_rehearsal','_serve',
            '--db',str(self.path),'--artifact-root',str(self.root),'--mode',self.mode,
            '--output',str(self.directory)],cwd=self.copy,env=self.env,
            stdin=subprocess.DEVNULL,stdout=self.log,stderr=subprocess.STDOUT)
        try:
            until = time.monotonic()+20
            while time.monotonic()<until:
                ready = self.directory/'ready.json'
                if ready.exists():
                    self.info = json.loads(ready.read_text(encoding='utf-8'))
                    self.url = self.info['base_url']
                    while time.monotonic()<until:
                        status = _get(self.url,'/api/system/status')
                        target = 'RUNNING' if self.mode=='full' else 'DISABLED'
                        if all(status['workers'][name]['state']==target for name in ('planning','inquiry')):
                            self.info['observed_worker_status'] = status
                            _json(self.directory/'observed-ready.json',self.info)
                            return self
                        time.sleep(.1)
                    raise TimeoutError('Worker health did not reach the declared mode.')
                if self.process.poll() is not None:
                    raise RuntimeError('Child runtime failed; inspect '+str(self.directory/'process.log'))
                time.sleep(.1)
            raise TimeoutError('Child runtime startup exceeded 20 seconds.')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                (self.directory/'stop').touch(exist_ok=True)
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                    raise RuntimeError('Owned child required forced termination; shutdown is not verified.')
            if self.process.returncode != 0:
                raise RuntimeError('Child runtime exited unsuccessfully; inspect process.log')
        if self.log is not None:
            self.log.close()
            self.log = None

    def __exit__(self,*_):
        self.close()


def _operation(root,copy,python,env,kind,path,label,mode,other=None):
    args = [python,'-m','server.recovery_rehearsal','_operate','--kind',kind,
        '--db',str(path),'--artifact-root',str(root),'--mode',mode]
    if other is not None:
        args.extend(['--other',str(other)])
    result = subprocess.run(args,cwd=copy,env=env,capture_output=True,timeout=30)
    (root/(label+'.stdout.json')).write_bytes(result.stdout)
    (root/(label+'.stderr.log')).write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError('Isolated operation failed: '+label)
    return json.loads(result.stdout.decode('utf-8'))


def run(output,*,python_path=None,solver_deps=None,mode='full'):
    if mode not in ('full','voice-only'):
        raise ValueError('Unsupported mode.')
    root = Path(output).resolve()
    root.mkdir(parents=True,exist_ok=False)
    python = str(Path(python_path or sys.executable).resolve())
    dependencies = Path(solver_deps or ROOT/'.solver_deps').resolve()
    if not Path(python).is_file() or not dependencies.is_dir():
        raise ValueError('Selected Python or preinstalled dependency directory does not exist.')
    release = root/'release-copy'
    manifest = copy_release(release)
    _json(root/'release-manifest.json',manifest)
    env = _environment(dependencies)
    report = dict(revision=REVISION,pass_=False,mode=mode,loopback_only=True,
        network_install=False,dependencies_shared=True,dependency_directory=str(dependencies),
        python_executable=python,clean_virtualenv=False,another_physical_pc_tested=False,
        public_deployment=False,existing_business_databases_accessed=False,
        release_file_count=len(manifest),steps=[],limitations=[
            'Same physical Windows machine, clean source copy and separate Python processes.',
            'Existing bundled Python and installed dependencies reused; no clean dependency installation.',
            'Manufactured two-column legacy fixture; not a comprehensive history of past schema versions.',
            'Planning caches and SQLite sequence are excluded from business-data equality.',
            'Authentication, hospital authorization and HTTPS are prerequisites for external publication; not implemented here.'])
    def check(name,condition,**evidence):
        report['steps'].append(dict(name=name,passed=bool(condition),**evidence))
        _json(root/'report.json',report)
        if not condition:
            raise AssertionError(name)
    path = root/'fixture'/'common.db'
    try:
        with ChildRuntime(root,release,python,env,path,'fresh-start',mode) as runtime:
            info = _get(runtime.url,'/api/team-runtime')
            check('fresh_database_and_mode',info['database']['counts']['patients']==0
                and info['database']['hospital_count']==10 and info['mode']==mode,
                process_id=runtime.process.pid,profile=info['profile'])
            report['dependency_versions'] = runtime.info['dependency_versions']
            check('pinned_server_dependencies' if mode=='full' else 'voice_only_dependency_scope',
                all(v['matches'] for v in report['dependency_versions'].values()) if mode=='full' else True,
                dependency_install_performed=False)
        migrated = _operation(root,release,python,env,'migrate',path,'migration',mode)
        check('preserving_and_idempotent_additive_migration',migrated['pass_'],evidence='migration.stdout.json')
        before_restart = logical_state(path)
        with ChildRuntime(root,release,python,env,path,'preserving-restart',mode) as runtime:
            patient = _get(runtime.url,'/api/patients/RECOVERY-FIXTURE')
            check('restarted_patient_is_preserved',patient['id']=='RECOVERY-FIXTURE'
                and len(patient['inquiries'])==1 and patient['state']=='WAITING')
            backup = _operation(root,release,python,env,'backup',path,'online-backup',mode,root/'backups'/'verified.db')
            check('sqlite_online_backup_business_data',backup['business_state']==before_restart,
                sqlite_backup_api=True,backup_sha256=backup['file_sha256'])
        check('source_business_rows_survive_restart',logical_state(path)==before_restart)
        unknown = root/'unknown.db'
        with closing(sqlite3.connect(unknown)) as conn,conn:
            conn.execute('CREATE TABLE unrelated(value TEXT)')
            conn.execute("INSERT INTO unrelated VALUES('preserve unknown database')")
        corrupted = root/'corrupted.db'
        corrupted.write_bytes(b'SYNTHETIC CORRUPTED DATABASE - MUST NOT INITIALIZE')
        for label,bad in (('unknown',unknown),('corrupted',corrupted)):
            before_bytes = bad.read_bytes()
            result = _operation(root,release,python,env,'reject',bad,'reject-'+label,mode)
            check(label+'_database_startup_rejected_unchanged',result['rejected']
                and bad.read_bytes()==before_bytes,evidence='reject-'+label+'.stdout.json')
        restored = root/'restored'/'common-restored.db'
        recovered = _operation(root,release,python,env,'restore',root/'backups'/'verified.db',
                               'restore-new-path',mode,restored)
        check('restore_new_file_matches_backup',recovered['business_state']==before_restart)
        with ChildRuntime(root,release,python,env,restored,'restored-restart',mode) as runtime:
            patient = _get(runtime.url,'/api/patients/RECOVERY-FIXTURE')
            check('restored_database_restarts_and_serves_patient',patient['id']=='RECOVERY-FIXTURE'
                and _get(runtime.url,'/api/team-runtime')['database']['valid'])
        check('restored_business_hash_matches_original',logical_state(restored)==before_restart,
            business_sha256=before_restart['sha256'])
        source_changes = manifest_changes(ROOT,manifest)
        copy_changes = manifest_changes(release,manifest)
        check('release_sources_unchanged',not source_changes and not copy_changes,
              source_changes=source_changes,copy_changes=copy_changes)
        report['pass_'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        report['error'] = str(error)
        (root/'failure-traceback.log').write_text(traceback.format_exc(),encoding='utf-8')
    report['pass'] = report.pop('pass_')
    _json(root/'report.json',report)
    lines = ['# 별도 작업사본 배포·복구 재현','',f"결과: {'PASS' if report['pass'] else 'FAIL'} · 모드 {mode}",
        '', '새 작업사본·별도 Python 프로세스·127.0.0.1 전용이며 실제 업무 DB와 기존 실행 서버를 변경하지 않았다.',
        '기존 번들 Python과 설치된 의존성을 재사용했다. 네트워크 설치·외부 공개·다른 물리 PC 검증은 수행하지 않았다.',
        '', '| 확인 | 결과 |','|---|---|']
    lines += [f"| {step['name']} | {'PASS' if step['passed'] else 'FAIL'} |" for step in report['steps']]
    if not report['pass']:
        lines += ['', '실패 기록: failure-traceback.log. 기존 실패 산출물은 보존한다.']
    lines += ['', '검증 범위: 합성 환자·문의·음성 초안·자원·사건을 포함한 업무 테이블의 논리 해시.',
        '제외: planning_state/planning_results 캐시, sqlite_sequence. SQLite 파일 바이트는 WAL·체크포인트 때문에 동일성을 요구하지 않는다.',
        '인증·병원별 권한·HTTPS는 이번 작업에 포함하지 않았다. 외부 공개 배포 준비 완료를 뜻하지 않는다.','']
    (root/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return report


def child_serve(args):
    from .team_runtime import CommonRuntime
    path = _inside(args.artifact_root,args.db)
    directory = _inside(args.artifact_root,args.output)
    with CommonRuntime(path,port=0,mode=args.mode) as runtime:
        info = runtime.info()
        _json(directory/'ready-writing.json',dict(base_url=runtime.base_url,process_id=os.getpid(),
            python=platform.python_version(),sqlite=sqlite3.sqlite_version,profile=info['profile'],
            dependency_versions=dependency_versions()))
        (directory/'ready-writing.json').replace(directory/'ready.json')
        deadline = time.monotonic()+90
        while not (directory/'stop').exists():
            if time.monotonic()>deadline:
                raise TimeoutError('Owned rehearsal server exceeded its lifetime.')
            time.sleep(.1)
    _json(directory/'stopped.json',dict(graceful=True,process_id=os.getpid()))


def child_operate(args):
    from .team_runtime import CommonRuntime
    from .team_runtime_profile import verify_profile
    path = _inside(args.artifact_root,args.db)
    if args.kind == 'migrate':
        result = migrate_fixture(path)
    elif args.kind in ('backup','restore'):
        result = backup_database(path,_inside(args.artifact_root,args.other),artifact_root=args.artifact_root)
    else:
        # A source/environment mismatch must not be misreported as successful
        # rejection of a damaged database.
        profile = verify_profile(args.mode)
        if not profile['ok']:
            raise ValueError('Runtime profile failed before database rejection check.')
        before = path.read_bytes()
        try:
            with CommonRuntime(path,port=0,mode=args.mode):
                pass
        except (ValueError,OSError,sqlite3.DatabaseError) as error:
            result = dict(rejected=True,error_type=type(error).__name__,unchanged=path.read_bytes()==before)
        else:
            result = dict(rejected=False,unchanged=path.read_bytes()==before)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('run','_serve','_operate'))
    parser.add_argument('--output')
    parser.add_argument('--python',dest='python_path')
    parser.add_argument('--solver-deps')
    parser.add_argument('--mode',choices=('full','voice-only'),default='full')
    parser.add_argument('--artifact-root')
    parser.add_argument('--db')
    parser.add_argument('--other')
    parser.add_argument('--kind',choices=('migrate','backup','restore','reject'))
    args = parser.parse_args(argv)
    if args.action=='_serve':
        child_serve(args)
        return 0
    if args.action=='_operate':
        child_operate(args)
        return 0
    if not args.output:
        parser.error('--output is required')
    report = run(args.output,python_path=args.python_path,solver_deps=args.solver_deps,mode=args.mode)
    print(json.dumps(dict(passed=report['pass'],steps=len(report['steps']),mode=report['mode'])))
    return 0 if report['pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
