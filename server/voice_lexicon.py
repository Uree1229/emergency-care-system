"""Versioned voice terminology in SQLite.

Project aliases can feed the conservative parser. Imported medical code lists
are reference data only; importing a diagnosis never makes it a symptom alias.
No patient record or operational table is read or changed by this module.
"""
from contextlib import contextmanager
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata
import uuid


ROOT = Path(__file__).resolve().parents[1]
PROJECT_TERMS = 'project.voice_terms'
PROJECT_R1 = 'project.voice_r1'
PROJECT_COMMANDS = 'project.voice_commands'
SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_term_sources (
 source_id TEXT NOT NULL, version TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('project','reference')),
 title TEXT NOT NULL, url TEXT NOT NULL, license TEXT NOT NULL,
 sha256 TEXT NOT NULL, imported_at INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 PRIMARY KEY(source_id,version)
);
CREATE UNIQUE INDEX IF NOT EXISTS voice_term_source_active
 ON voice_term_sources(source_id) WHERE active=1;
CREATE TABLE IF NOT EXISTS voice_terms (
 source_id TEXT NOT NULL, source_version TEXT NOT NULL, code TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('catalog','symptom','measurement','r1','command_config','command','reference')),
 label TEXT NOT NULL, english TEXT, payload TEXT NOT NULL DEFAULT '{}',
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 PRIMARY KEY(source_id,source_version,code),
 FOREIGN KEY(source_id,source_version) REFERENCES voice_term_sources(source_id,version)
);
CREATE INDEX IF NOT EXISTS voice_terms_lookup ON voice_terms(kind,label);
CREATE TABLE IF NOT EXISTS voice_term_aliases (
 source_id TEXT NOT NULL, source_version TEXT NOT NULL, code TEXT NOT NULL,
 alias TEXT NOT NULL, normalized TEXT NOT NULL, position INTEGER NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('seed','reviewed','reference')),
 reviewed_by TEXT, created_at INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 PRIMARY KEY(source_id,source_version,code,alias),
 FOREIGN KEY(source_id,source_version,code) REFERENCES voice_terms(source_id,source_version,code)
);
CREATE INDEX IF NOT EXISTS voice_term_alias_lookup ON voice_term_aliases(normalized);
"""


def _error(code, message, status=400):
    from .service import ServiceError
    raise ServiceError(code, message, status)


def _require(condition, message, code='INVALID_VOICE_DICTIONARY', status=400):
    if not condition:
        _error(code, message, status)


def _text(value, name, maximum=1000, *, empty=False):
    _require(isinstance(value, str) and (empty or bool(value.strip())) and len(value) <= maximum
             and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value), f'{name} 형식이 올바르지 않습니다.')
    return value.strip()


def _normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@contextmanager
def _atomic(conn):
    # Works both alone and inside an outer patient/API transaction.
    name = 'voice_lexicon_' + uuid.uuid4().hex
    conn.execute('SAVEPOINT ' + name)
    try:
        yield
    except BaseException:
        conn.execute('ROLLBACK TO ' + name)
        conn.execute('RELEASE ' + name)
        raise
    else:
        conn.execute('RELEASE ' + name)


def _documents():
    from . import voice_r1
    path = ROOT/'config/paramedic_voice_terms.json'
    catalog = json.loads(path.read_text(encoding='utf-8'))
    r1 = copy.deepcopy(voice_r1.TERMS)
    command_path = ROOT/'config/voice_commands.json'
    commands = json.loads(command_path.read_text(encoding='utf-8')) if command_path.exists() else None
    return catalog, r1, commands


def _source(conn, source_id, version, kind, title, url, license, checksum):
    row = conn.execute('SELECT sha256 FROM voice_term_sources WHERE source_id=? AND version=?',
                       (source_id, version)).fetchone()
    _require(row is None or row['sha256'] == checksum,
             '같은 출처·버전의 내용을 덮어쓸 수 없습니다. 새 버전을 사용하세요.',
             'VOICE_DICTIONARY_VERSION_CONFLICT', 409)
    conn.execute('UPDATE voice_term_sources SET active=0 WHERE source_id=? AND version<>?', (source_id, version))
    conn.execute('INSERT INTO voice_term_sources '
                 '(source_id,version,kind,title,url,license,sha256,imported_at,active) '
                 'VALUES(?,?,?,?,?,?,?,?,1) ON CONFLICT(source_id,version) DO UPDATE SET active=1',
                 (source_id, version, kind, title, url, license, checksum, int(time.time())))


def _entry(conn, sid, version, code, kind, label, payload=None, english=None):
    conn.execute('INSERT OR IGNORE INTO voice_terms '
                 '(source_id,source_version,code,kind,label,english,payload) VALUES(?,?,?,?,?,?,?)',
                 (sid, version, code, kind, label, english, _json(payload or {})))


def _alias(conn, sid, version, code, alias, position, origin='seed', reviewed_by=None):
    alias = _text(alias, 'alias', 120)
    conn.execute('INSERT OR IGNORE INTO voice_term_aliases '
                 '(source_id,source_version,code,alias,normalized,position,origin,reviewed_by,created_at) '
                 'VALUES(?,?,?,?,?,?,?,?,?)',
                 (sid, version, code, alias, _normalized(alias), position, origin, reviewed_by, int(time.time())))


def _project_specs():
    catalog, r1, commands = _documents()
    # Content-addressed suffix means a changed local configuration cannot
    # silently replace the old version's provenance or reviewed aliases.
    catalog_hash = hashlib.sha256(_json(catalog).encode()).hexdigest()
    r1_hash = hashlib.sha256(_json(r1).encode()).hexdigest()
    specs = [(PROJECT_TERMS, str(catalog['version'])+'-'+catalog_hash[:12], catalog_hash,
              '프로젝트 구급대원 용어·동의어', (ROOT/'config/paramedic_voice_terms.json').as_uri(), catalog),
             (PROJECT_R1, 'rules-'+r1_hash[:12], r1_hash,
              '프로젝트 R1 명시 표현 규칙', (ROOT/'server/voice_r1.py').as_uri(), r1)]
    if commands is not None:
        checksum = hashlib.sha256(_json(commands).encode()).hexdigest()
        specs.append((PROJECT_COMMANDS, str(commands.get('version', '1'))+'-'+checksum[:12], checksum,
                      '프로젝트 음성 명령 표현', (ROOT/'config/voice_commands.json').as_uri(), commands))
    return specs


def seed(conn):
    """Seed trusted project terms after SCHEMA initialization, without resets."""
    with _atomic(conn):
        for sid, version, checksum, title, url, document in _project_specs():
            _source(conn, sid, version, 'project', title, url, 'Project-authored; not clinically reviewed', checksum)
            if sid == PROJECT_TERMS:
                _entry(conn, sid, version, '__catalog__', 'catalog', '프로젝트 음성 사전', document)
                for item in document['terms']['symptoms']:
                    code = 'symptom:'+item['canonical']
                    _entry(conn, sid, version, code, 'symptom', item['canonical'])
                    for order, alias in enumerate(item['synonyms']):
                        _alias(conn, sid, version, code, alias, order)
                for key, item in document['terms']['measurements'].items():
                    code = 'measurement:'+key
                    _entry(conn, sid, version, code, 'measurement', item['label'], item)
                    for order, alias in enumerate(item['synonyms']):
                        _alias(conn, sid, version, code, alias, order)
            elif sid == PROJECT_R1:
                for index, item in enumerate(document):
                    code = 'r1:'+str(index)
                    _entry(conn, sid, version, code, 'r1', item['expr'][0], item)
                    for order, alias in enumerate(item['expr']):
                        _alias(conn, sid, version, code, alias, order)
            else:
                _entry(conn, sid, version, '__commands__', 'command_config', '음성 명령 정책', document)
                for action, item in document.get('actions', {}).items():
                    code = 'command:'+action
                    _entry(conn, sid, version, code, 'command', action)
                    for order, alias in enumerate(item.get('aliases', [])):
                        _alias(conn, sid, version, code, alias, order)
    return overview(conn)


def overview(conn):
    sources = []
    for row in conn.execute('SELECT * FROM voice_term_sources WHERE active=1 ORDER BY source_id'):
        item = dict(row)
        args = (item['source_id'], item['version'])
        item['term_count'] = conn.execute('SELECT COUNT(*) FROM voice_terms WHERE source_id=? AND source_version=? AND active=1', args).fetchone()[0]
        item['alias_count'] = conn.execute('SELECT COUNT(*) FROM voice_term_aliases WHERE source_id=? AND source_version=? AND enabled=1', args).fetchone()[0]
        item['runtime_enabled'] = item['kind'] == 'project'
        sources.append(item)
    return dict(revision='voice-lexicon-db-v1', storage='sqlite', sources=sources,
                term_count=sum(item['term_count'] for item in sources),
                alias_count=sum(item['alias_count'] for item in sources),
                reference_terms_are_runtime_aliases=False)


def _aliases(conn, sid, version, code, fallback):
    active = conn.execute('SELECT active FROM voice_terms WHERE source_id=? AND source_version=? AND code=?',
                          (sid, version, code)).fetchone()
    if active is None:
        return list(fallback)
    if not active['active']:
        return []
    # SQL data is literal text only; parser escapes it before matching.
    return [row['alias'] for row in conn.execute('SELECT alias FROM voice_term_aliases '
            'WHERE source_id=? AND source_version=? AND code=? AND enabled=1 ORDER BY position,alias',
            (sid, version, code)) if isinstance(row['alias'], str) and 0 < len(row['alias']) <= 120
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in row['alias'])]


def snapshot(conn):
    """Load only known project schema/targets with DB-managed literal aliases.

    Patient fields, numeric limits and regular-expression templates come from
    trusted application files. Reference payloads cannot inject executable rules.
    """
    catalog, r1, commands = _documents()
    for sid, version, _, _, _, _ in _project_specs():
        row = conn.execute('SELECT version FROM voice_term_sources WHERE source_id=? AND active=1', (sid,)).fetchone()
        if row is None or row['version'] != version:
            continue  # Uninitialized/old DB keeps the deployed app's defaults.
        if sid == PROJECT_TERMS:
            for item in catalog['terms']['symptoms']:
                item['synonyms'] = _aliases(conn, sid, version, 'symptom:'+item['canonical'], item['synonyms'])
            for key, item in catalog['terms']['measurements'].items():
                item['synonyms'] = _aliases(conn, sid, version, 'measurement:'+key, item['synonyms'])
        elif sid == PROJECT_R1:
            for index, item in enumerate(r1):
                item['expr'] = _aliases(conn, sid, version, 'r1:'+str(index), item['expr'])
        elif commands is not None:
            for action, item in commands.get('actions', {}).items():
                item['aliases'] = _aliases(conn, sid, version, 'command:'+action, item.get('aliases', []))
    return dict(catalog=catalog, r1_terms=r1, commands=commands, dictionary=overview(conn))


def add_alias(conn, source_id, code, alias, reviewed_by, *, version=None):
    """Local maintenance helper: explicitly review a literal project alias."""
    _text(source_id, 'source_id', 120)
    _text(code, 'code', 200)
    alias = _text(alias, 'alias', 120)
    reviewed_by = _text(reviewed_by, 'reviewed_by', 120)
    with _atomic(conn):
        source = conn.execute('SELECT * FROM voice_term_sources WHERE source_id=? AND active=1', (source_id,)).fetchone()
        _require(source is not None and source['kind'] == 'project', '프로젝트 사전에만 실행용 동의어를 추가할 수 있습니다.')
        _require(version is None or version == source['version'], '사전 버전이 변경됐습니다.', 'VOICE_DICTIONARY_VERSION_CONFLICT', 409)
        version = source['version']
        target = conn.execute('SELECT kind FROM voice_terms WHERE source_id=? AND source_version=? AND code=? AND active=1',
                              (source_id, version, code)).fetchone()
        _require(target is not None and target['kind'] in ('symptom', 'measurement', 'r1', 'command'), '지원하는 실행 대상이 아닙니다.')
        conflict = conn.execute('SELECT 1 FROM voice_term_aliases a JOIN voice_terms t '
            'ON t.source_id=a.source_id AND t.source_version=a.source_version AND t.code=a.code '
            'WHERE a.source_id=? AND a.source_version=? AND a.code<>? AND a.normalized=? '
            'AND a.enabled=1 AND t.active=1 AND t.kind=? LIMIT 1',
            (source_id, version, code, _normalized(alias), target['kind'])).fetchone()
        _require(conflict is None, '동일한 표현이 다른 대상에 연결돼 있습니다.', 'VOICE_DICTIONARY_ALIAS_CONFLICT', 409)
        position = conn.execute('SELECT COALESCE(MAX(position),0)+1 FROM voice_term_aliases WHERE source_id=? AND source_version=? AND code=?',
                                (source_id, version, code)).fetchone()[0]
        _alias(conn, source_id, version, code, alias, position, 'reviewed', reviewed_by)
    return dict(source_id=source_id, version=version, code=code, alias=alias, reviewed_by=reviewed_by)


def search(conn, query, limit=20, offset=0, source_id=None):
    query = _text(query, 'query', 120)
    if isinstance(limit, str) and re.fullmatch(r'(?:0|[1-9][0-9]{0,6})', limit):
        limit = int(limit)
    if isinstance(offset, str) and re.fullmatch(r'(?:0|[1-9][0-9]{0,6})', offset):
        offset = int(offset)
    _require(type(limit) is int and 1 <= limit <= 100 and type(offset) is int and 0 <= offset <= 1_000_000,
             'limit은 1~100, offset은 0~1000000 정수입니다.')
    if source_id is not None:
        source_id = _text(source_id, 'source_id', 120)
    escaped = _normalized(query).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    pattern = '%'+escaped+'%'
    # Normalized aliases include label/English for imported references.
    where = ('s.active=1 AND t.active=1 AND '
        '(lower(t.code) LIKE ? ESCAPE \'\\\' OR EXISTS '
        '(SELECT 1 FROM voice_term_aliases a WHERE a.source_id=t.source_id AND a.source_version=t.source_version '
        'AND a.code=t.code AND a.enabled=1 AND a.normalized LIKE ? ESCAPE \'\\\'))')
    params = [pattern, pattern]
    if source_id is not None:
        where += ' AND t.source_id=?'
        params.append(source_id)
    joined = ' FROM voice_terms t JOIN voice_term_sources s ON s.source_id=t.source_id AND s.version=t.source_version WHERE '+where
    total = conn.execute('SELECT COUNT(*)'+joined, params).fetchone()[0]
    rows = conn.execute('SELECT t.source_id,t.source_version,t.code,t.kind,t.label,t.english,t.payload,s.title,s.url,s.license'
                        +joined+' ORDER BY t.source_id,t.code LIMIT ? OFFSET ?', (*params, limit, offset)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        payload = json.loads(item.pop('payload'))
        item['term_id'] = item['code']
        if item['kind'] == 'reference':
            item['code'] = payload.get('original_code', item['code'])
            if 'reference_fields' in payload:
                item['reference_fields'] = copy.deepcopy(payload['reference_fields'])
        item.update(runtime_enabled=item['kind'] != 'reference', reference_only=item['kind'] == 'reference')
        items.append(item)
    return dict(query=query, items=items, total=total, limit=limit, offset=offset, has_more=offset+len(items)<total)


def import_reference_csv(conn, path, source_id, version, title, url, license, code_column,
                         korean_column, english_column=None, encoding='utf-8-sig', extra_columns=None):
    """Import a bounded official file as searchable reference terms, atomically."""
    source_id = _text(source_id, 'source_id', 120)
    _require(not source_id.startswith('project.'), '외부 자료는 프로젝트 출처를 덮어쓸 수 없습니다.')
    version = _text(version, 'version', 120)
    title, url, license = _text(title, 'title'), _text(url, 'url', 4096), _text(license, 'license', 2000)
    _require(isinstance(encoding, str) and encoding in ('utf-8-sig', 'utf-8', 'cp949', 'euc-kr'), '지원하지 않는 CSV 인코딩입니다.')
    path = Path(path)
    _require(path.is_file() and path.stat().st_size <= 100*1024*1024, 'CSV는 100MB 이하의 로컬 파일이어야 합니다.')
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    extra_columns = {} if extra_columns is None else extra_columns
    _require(isinstance(extra_columns, dict) and len(extra_columns) <= 20
             and all(isinstance(key, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', key)
                     for key in extra_columns), 'extra_columns는 유효한 메타데이터 키와 CSV 열 이름의 객체입니다.')
    columns = [code_column, korean_column] + ([english_column] if english_column else []) + list(extra_columns.values())
    _require(all(isinstance(name, str) and name for name in columns) and len(set(columns)) == len(columns), 'CSV 열 이름이 올바르지 않습니다.')
    imported = 0
    seen = set()
    with _atomic(conn):
        _source(conn, source_id, version, 'reference', title, url, license, checksum)
        existing = conn.execute('SELECT payload FROM voice_terms WHERE source_id=? AND source_version=? '
                                "AND kind='reference' LIMIT 1", (source_id, version)).fetchone()
        _require(existing is None or json.loads(existing['payload']).get('reference_columns', {}) == extra_columns,
                 '같은 출처·버전의 메타데이터 열 해석을 변경할 수 없습니다. 새 버전을 사용하세요.',
                 'VOICE_DICTIONARY_VERSION_CONFLICT', 409)
        try:
            with path.open(encoding=encoding, newline='') as stream:
                reader = csv.DictReader(stream)
                _require(reader.fieldnames is not None and not (set(columns)-set(reader.fieldnames)), 'CSV 필수 열이 없습니다.')
                for row_number, row in enumerate(reader, 1):
                    _require(row_number <= 250_000, 'CSV 최대 250000개 항목을 초과했습니다.')
                    code = _text(row.get(code_column), 'code', 200)
                    label = _text(row.get(korean_column), 'korean label', 2000)
                    english = _text(row.get(english_column, ''), 'english label', 2000, empty=True) if english_column else None
                    reference_fields = {key: _text(row.get(column), key, 2000, empty=True)
                                        for key, column in extra_columns.items()}
                    # Official disease masters can legitimately contain several
                    # Korean/English names for one billing/classification code.
                    # Keep the real code, and use a stable row identity internally.
                    identity = _json((code, label, english, reference_fields) if extra_columns else (code, label, english))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    term_id = code+'#'+hashlib.sha256(identity.encode()).hexdigest()[:20]
                    payload = {'original_code': code}
                    if extra_columns:
                        payload.update(reference_fields=reference_fields, reference_columns=extra_columns)
                    _entry(conn, source_id, version, term_id, 'reference', label,
                           payload, english=english)
                    for position, alias in enumerate([label]+([english] if english else [])):
                        # Reference titles can exceed executable alias length;
                        # still searchable without ever being parser aliases.
                        conn.execute('INSERT OR IGNORE INTO voice_term_aliases '
                            '(source_id,source_version,code,alias,normalized,position,origin,created_at) '
                            'VALUES(?,?,?,?,?,?,\'reference\',?)',
                            (source_id,version,term_id,alias,_normalized(alias),position,int(time.time())))
                    imported += 1
        except (UnicodeError, csv.Error) as error:
            _error('INVALID_VOICE_DICTIONARY', 'CSV 파일을 읽지 못했습니다: '+type(error).__name__)
        _require(imported > 0, 'CSV에 가져올 용어가 없습니다.')
    return dict(source_id=source_id, version=version, imported=imported, sha256=checksum,
                runtime_enabled=False, reference_only=True)
