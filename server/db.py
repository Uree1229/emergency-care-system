"""SQLite 저장소. 시각은 모두 UTC epoch 초(int)로 저장한다 (S14)."""
import json
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS hospitals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    capabilities TEXT NOT NULL DEFAULT '[]',   -- JSON 진료 분야 목록
    source TEXT NOT NULL,                      -- public / hospital_input / synthetic
    source_ids TEXT NOT NULL DEFAULT '{}'      -- 검증된 출처별 기관 연결만 저장
);
CREATE TABLE IF NOT EXISTS resources (
    hospital_id TEXT NOT NULL REFERENCES hospitals(id),
    resource_type TEXT NOT NULL,               -- ed_bed(진료 자리) / special_team(전문팀) 등
    capacity INTEGER NOT NULL,
    occupied INTEGER NOT NULL,                 -- 실제 점유(치료 중). 도착 예정 수요는 이송에서 계산
    status TEXT NOT NULL DEFAULT 'available',  -- available / limited / unknown
    source TEXT NOT NULL,
    source_observed_at INTEGER,                -- 원천 관측시각. 모르면 NULL
    updated_at INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (hospital_id, resource_type)
);
CREATE TABLE IF NOT EXISTS restrictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hospital_id TEXT NOT NULL REFERENCES hospitals(id),
    scope TEXT NOT NULL,                       -- 'all' 또는 진료 분야 이름
    reason TEXT NOT NULL,
    starts_at INTEGER NOT NULL,
    ends_at INTEGER,                           -- 종료 예정. 지나도 자동 복구하지 않고 재확인 필요
    cleared_at INTEGER
);
CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    reporter_role TEXT NOT NULL,               -- self / guardian / transporter
    relation TEXT,
    contact TEXT,
    age INTEGER,
    symptoms TEXT,
    onset_at INTEGER,
    extra TEXT NOT NULL DEFAULT '{}',          -- 질환·복용약 등. 모르면 키 자체를 넣지 않음
    danger_signs TEXT NOT NULL DEFAULT '[]',
    priority INTEGER,                          -- 4 높음 / 2 중간 / 1 낮음 / NULL 미확인
    priority_source TEXT NOT NULL DEFAULT 'unknown',  -- self / expert / unknown
    required_capabilities TEXT NOT NULL DEFAULT '[]',
    resource_demands TEXT NOT NULL DEFAULT '{"ed_bed": 1}',
    lat REAL,
    lon REAL,
    consent INTEGER NOT NULL DEFAULT 0,
    requested_at INTEGER NOT NULL,
    state TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    destination_hospital_id TEXT REFERENCES hospitals(id),
    departed_at INTEGER,
    eta_at INTEGER,
    arrived_at INTEGER,
    needs_adjustment INTEGER NOT NULL DEFAULT 0,
    adjustment_note TEXT,
    closed_reason TEXT
);
CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    hospital_id TEXT NOT NULL REFERENCES hospitals(id),
    state TEXT NOT NULL,                       -- PENDING / CHECKING / ACCEPTED / DECLINED / EXPIRED / CANCELLED / CONFIRMED(목적지 확정)
    reason TEXT,
    question TEXT,
    answer TEXT,
    created_at INTEGER NOT NULL,
    last_sent_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    responded_at INTEGER,
    expires_at INTEGER,
    patient_version INTEGER NOT NULL,
    handled_by TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (patient_id, hospital_id)
);
CREATE TABLE IF NOT EXISTS inquiry_workflows (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    state TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    deadline_at INTEGER NOT NULL,
    next_evaluation_at INTEGER NOT NULL,
    round_no INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_error TEXT,
    stop_reason TEXT
);
CREATE TABLE IF NOT EXISTS inquiry_policy_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    at INTEGER NOT NULL,
    policy_name TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    job_version INTEGER NOT NULL,
    reason TEXT NOT NULL,
    hospital_ids TEXT NOT NULL,
    evidence TEXT NOT NULL,
    UNIQUE(patient_id, job_version)
);
CREATE INDEX IF NOT EXISTS inquiry_decisions_patient ON inquiry_policy_decisions(patient_id,id);
CREATE TABLE IF NOT EXISTS transport_reviews (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    state TEXT NOT NULL,
    hospital_id TEXT REFERENCES hospitals(id),
    policy_name TEXT NOT NULL DEFAULT 'fixed_3',
    baseline TEXT NOT NULL DEFAULT '{}',
    risk_signature TEXT,
    acknowledged_signature TEXT,
    evidence TEXT NOT NULL DEFAULT '{}',
    last_checked_at INTEGER,
    updated_at INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS transport_inquiry_workflows (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id),
    source_hospital_id TEXT NOT NULL REFERENCES hospitals(id),
    episode_start_event INTEGER NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    state TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    deadline_at INTEGER NOT NULL,
    next_evaluation_at INTEGER NOT NULL,
    round_no INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_error TEXT,
    stop_reason TEXT,
    policy_name TEXT NOT NULL DEFAULT 'fixed_3',
    policy_version TEXT NOT NULL DEFAULT '1',
    policy_config TEXT
);
CREATE TABLE IF NOT EXISTS external_arrivals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hospital_id TEXT NOT NULL REFERENCES hospitals(id),
    occurred_at INTEGER NOT NULL,
    observed_at INTEGER NOT NULL,
    resource_demands TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    actor TEXT NOT NULL,
    patient_id TEXT,
    hospital_id TEXT,
    type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_patient ON events(patient_id, id);
CREATE INDEX IF NOT EXISTS events_hospital ON events(hospital_id, id);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL UNIQUE REFERENCES patients(id),
    state TEXT NOT NULL DEFAULT 'OPEN' CHECK(state IN ('OPEN','CLOSED')),
    created_at INTEGER NOT NULL,
    closed_at INTEGER
);
CREATE TABLE IF NOT EXISTS conversation_hospital_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    hospital_id TEXT NOT NULL REFERENCES hospitals(id),
    inquiry_id INTEGER REFERENCES inquiries(id),
    joined_at INTEGER NOT NULL,
    left_at INTEGER,
    joined_event_id INTEGER NOT NULL,
    left_event_id INTEGER,
    patient_snapshot TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS conversation_one_active_hospital
    ON conversation_hospital_sessions(conversation_id) WHERE left_at IS NULL;
CREATE INDEX IF NOT EXISTS conversation_hospital_history
    ON conversation_hospital_sessions(conversation_id, hospital_id, id);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    hospital_session_id INTEGER REFERENCES conversation_hospital_sessions(id),
    sender_role TEXT NOT NULL CHECK(sender_role IN ('patient','hospital')),
    sender_name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    client_message_id TEXT NOT NULL,
    event_watermark INTEGER NOT NULL DEFAULT 0,
    UNIQUE(conversation_id, sender_role, client_message_id)
);
CREATE INDEX IF NOT EXISTS chat_messages_conversation ON chat_messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS chat_messages_session ON chat_messages(hospital_session_id, id);
"""

JSON_FIELDS = {'capabilities', 'source_ids', 'extra', 'danger_signs', 'required_capabilities',
               'resource_demands', 'data', 'explicit_required_capabilities', 'explicit_resource_demands',
               'patient_snapshot', 'policy_config', 'hospital_ids', 'evidence', 'baseline', 'transport_baseline',
               'request_payload', 'applied_resource_demands', 'applied_resource_epochs', 'arrival_accounting'}


def connect(path):
    conn = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    if path != ':memory:':
        conn.execute('PRAGMA journal_mode = WAL')
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS accounting_reconciliations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hospital_id TEXT NOT NULL REFERENCES hospitals(id),
        target_kind TEXT NOT NULL CHECK(target_kind IN ('patient','external_arrival')),
        target_id TEXT NOT NULL, request_id TEXT NOT NULL, created_at INTEGER NOT NULL,
        handled_by TEXT NOT NULL, reason TEXT NOT NULL, expected_version INTEGER NOT NULL,
        prior_state TEXT NOT NULL, prior_version INTEGER NOT NULL,
        result_state TEXT NOT NULL, result_version INTEGER NOT NULL,
        request_payload TEXT NOT NULL, original_accounting TEXT NOT NULL,
        resource_snapshot TEXT NOT NULL,
        UNIQUE(hospital_id,request_id)
    );
    CREATE INDEX IF NOT EXISTS accounting_reconciliation_hospital
        ON accounting_reconciliations(hospital_id,id);
    CREATE TRIGGER IF NOT EXISTS accounting_reconciliation_no_update
        BEFORE UPDATE ON accounting_reconciliations
        BEGIN SELECT RAISE(ABORT, 'accounting reconciliation audit is immutable'); END;
    CREATE TRIGGER IF NOT EXISTS accounting_reconciliation_no_delete
        BEFORE DELETE ON accounting_reconciliations
        BEGIN SELECT RAISE(ABORT, 'accounting reconciliation audit is immutable'); END;
    CREATE TRIGGER IF NOT EXISTS accounting_reconciliation_no_replace
        BEFORE INSERT ON accounting_reconciliations
        WHEN EXISTS (SELECT 1 FROM accounting_reconciliations
                     WHERE id=NEW.id OR (hospital_id=NEW.hospital_id AND request_id=NEW.request_id))
        BEGIN SELECT RAISE(ABORT, 'accounting reconciliation audit is immutable'); END;
    """)
    # 과거 돌발 유입은 이미 수동 정산됐을 수 있어 자동 퇴실 대상으로 추정하지 않는다.
    arrival_columns = {r['name'] for r in conn.execute('PRAGMA table_info(external_arrivals)')}
    for name, definition in {
        'state': "TEXT NOT NULL DEFAULT 'LEGACY'",
        'discharged_at': 'INTEGER', 'version': 'INTEGER NOT NULL DEFAULT 1',
        'registered_by': 'TEXT', 'discharged_by': 'TEXT', 'request_id': 'TEXT',
        'accounting_reconciliation_id': 'INTEGER',
        'accounting_reconciled_at': 'INTEGER', 'accounting_reconciled_by': 'TEXT',
        'request_payload': 'TEXT',
        'applied_resource_demands': "TEXT NOT NULL DEFAULT '{}'",
        'applied_resource_epochs': "TEXT NOT NULL DEFAULT '{}'",
    }.items():
        if name not in arrival_columns:
            conn.execute(f'ALTER TABLE external_arrivals ADD COLUMN {name} {definition}')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS external_arrival_request '
                 'ON external_arrivals(hospital_id,request_id) WHERE request_id IS NOT NULL')
    conn.execute('CREATE INDEX IF NOT EXISTS external_arrival_state '
                 'ON external_arrivals(hospital_id,state,id)')
    resource_columns = {r['name'] for r in conn.execute('PRAGMA table_info(resources)')}
    if 'occupancy_epoch' not in resource_columns:
        conn.execute("ALTER TABLE resources ADD COLUMN occupancy_epoch TEXT NOT NULL DEFAULT ''")
    location_columns = {r['name'] for r in conn.execute('PRAGMA table_info(patients)')}
    for name, definition in {'location_updated_at': 'INTEGER',
                             'location_version': 'INTEGER NOT NULL DEFAULT 0',
                             'arrival_accounting': 'TEXT',
                             'accounting_reconciliation_id': 'INTEGER',
                             'accounting_reconciled_at': 'INTEGER', 'accounting_reconciled_by': 'TEXT',
                             'transport_baseline': "TEXT NOT NULL DEFAULT '{}'"}.items():
        if name not in location_columns:
            conn.execute(f'ALTER TABLE patients ADD COLUMN {name} {definition}')
    workflow_columns = {r['name'] for r in conn.execute('PRAGMA table_info(inquiry_workflows)')}
    for name, definition in {'policy_name': "TEXT NOT NULL DEFAULT 'fixed_3'",
                             'policy_version': "TEXT NOT NULL DEFAULT '1'",
                             'policy_config': 'TEXT'}.items():
        if name not in workflow_columns:
            conn.execute(f'ALTER TABLE inquiry_workflows ADD COLUMN {name} {definition}')
    hospital_columns = {r['name'] for r in conn.execute('PRAGMA table_info(hospitals)')}
    if 'capabilities_status' not in hospital_columns:
        conn.execute("ALTER TABLE hospitals ADD COLUMN capabilities_status TEXT NOT NULL DEFAULT 'known'")
    # 공공 가용수는 총량/실제 점유와 의미가 다르므로 운영 resources를 덮어쓰지 않는다.
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS public_api_snapshots (
        hospital_id TEXT NOT NULL REFERENCES hospitals(id), endpoint TEXT NOT NULL,
        source_observed_at INTEGER, fetched_at INTEGER NOT NULL,
        normalized TEXT NOT NULL, raw_data TEXT NOT NULL,
        PRIMARY KEY (hospital_id, endpoint)
    );
    CREATE TABLE IF NOT EXISTS public_sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, region TEXT NOT NULL, district TEXT,
        started_at INTEGER NOT NULL, finished_at INTEGER,
        status TEXT NOT NULL, stats TEXT NOT NULL DEFAULT '{}', error TEXT
    );
    """)
    sync_columns = {r['name'] for r in conn.execute('PRAGMA table_info(public_sync_runs)')}
    if 'district' not in sync_columns:
        conn.execute('ALTER TABLE public_sync_runs ADD COLUMN district TEXT')
    # 기존 시연 DB도 삭제 없이 확장한다. 여러 번 실행해도 데이터는 보존된다.
    columns = {r['name'] for r in conn.execute('PRAGMA table_info(patients)')}
    additions = {
        'name': 'TEXT', 'birth': 'TEXT', 'address': 'TEXT', 'guardian': 'TEXT',
        'guardian_phone': 'TEXT', 'consciousness': 'TEXT', 'breathing': 'TEXT',
        'treatment_duration_seconds': 'INTEGER', 'max_wait_seconds': 'INTEGER',
        'explicit_required_capabilities': 'TEXT', 'explicit_resource_demands': 'TEXT',
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE patients ADD COLUMN {name} {definition}')
    # 대화 이력은 배정 입력이 아니다. 아래 planning 트리거 대상에 넣지 않는다.
    session_columns = {r['name'] for r in conn.execute('PRAGMA table_info(conversation_hospital_sessions)')}
    if 'patient_snapshot' not in session_columns:
        conn.execute("ALTER TABLE conversation_hospital_sessions ADD COLUMN patient_snapshot TEXT NOT NULL DEFAULT '{}'")
    message_columns = {r['name'] for r in conn.execute('PRAGMA table_info(chat_messages)')}
    if 'event_watermark' not in message_columns:
        conn.execute('ALTER TABLE chat_messages ADD COLUMN event_watermark INTEGER NOT NULL DEFAULT 0')
    with transaction(conn):
        for p in rows(conn.execute('SELECT * FROM patients WHERE id NOT IN (SELECT patient_id FROM conversations)')):
            ensure_conversation(conn, p, int(time.time()))
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS planning_state (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0,
        completed_revision INTEGER NOT NULL DEFAULT -1, lease_until INTEGER NOT NULL DEFAULT 0,
        last_error TEXT, lease_owner TEXT
    );
    INSERT OR IGNORE INTO planning_state(id) VALUES(1);
    CREATE TABLE IF NOT EXISTS planning_results (
        patient_id TEXT PRIMARY KEY REFERENCES patients(id), revision INTEGER NOT NULL,
        created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, data TEXT NOT NULL
    );
    """)
    # 입력 변경과 재계산 요청은 같은 트랜잭션에서 기록된다. 결과 저장은 재계산을 유발하지 않는다.
    for table in ('hospitals', 'resources', 'restrictions', 'patients', 'inquiries', 'external_arrivals',
                  'public_api_snapshots'):
        for action in ('INSERT', 'UPDATE', 'DELETE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS planning_{table}_{action}
                AFTER {action} ON {table} BEGIN
                UPDATE planning_state SET revision=revision+1 WHERE id=1;
                END''')


@contextmanager
def transaction(conn):
    """쓰기 잠금을 먼저 잡아 검사와 반영 사이에 다른 요청이 끼어들지 못하게 한다 (S05)."""
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
    except BaseException:
        conn.execute('ROLLBACK')
        raise
    conn.execute('COMMIT')


def row(r):
    if r is None:
        return None
    out = dict(r)
    for k in JSON_FIELDS & out.keys():
        if out[k] is not None:
            try:
                out[k] = json.loads(out[k])
            except (ValueError, TypeError):
                if k != 'arrival_accounting':
                    raise
                # 과거 정산 원장의 손상은 자동 차감 409로 다룬다. 원본은 수정하지 않는다.
                out[k] = {'invalid': True}
    return out


def rows(rs):
    return [row(r) for r in rs]


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def conversation_patient_snapshot(patient, handoff_note=None):
    """참여 시점의 환자 요약만 보존한다. 다른 병원과의 대화는 인계하지 않는다."""
    fields = ('id', 'reporter_role', 'contact', 'age', 'symptoms', 'onset_at', 'extra', 'danger_signs',
              'priority', 'priority_source', 'required_capabilities', 'resource_demands', 'lat', 'lon',
              'state', 'version', 'destination_hospital_id', 'departed_at', 'eta_at', 'arrived_at',
              'needs_adjustment', 'adjustment_note', 'name', 'birth', 'address', 'guardian',
              'guardian_phone', 'consciousness', 'breathing')
    snapshot = {key: patient.get(key) for key in fields}
    if handoff_note is not None:
        snapshot['handoff_note'] = handoff_note
    return snapshot


def join_conversation_hospital(conn, conversation_id, now, hospital_id, inquiry_id,
                               event_id, patient, handoff_note=None):
    """호출자의 업무 트랜잭션에서 수용 확정과 함께 참여 구간을 연다."""
    return conn.execute('INSERT INTO conversation_hospital_sessions '
                        '(conversation_id,hospital_id,inquiry_id,joined_at,joined_event_id,patient_snapshot) '
                        'VALUES (?,?,?,?,?,?)',
                        (conversation_id, hospital_id, inquiry_id, now, event_id,
                         dumps(conversation_patient_snapshot(patient, handoff_note)))).lastrowid


def leave_conversation_hospital(conn, conversation_id, now, event_id):
    conn.execute('UPDATE conversation_hospital_sessions SET left_at=?, left_event_id=? '
                 'WHERE conversation_id=? AND left_at IS NULL', (now, event_id, conversation_id))


def close_conversation(conn, conversation_id, now, event_id):
    leave_conversation_hospital(conn, conversation_id, now, event_id)
    conn.execute("UPDATE conversations SET state='CLOSED', closed_at=COALESCE(closed_at,?) WHERE id=?",
                 (now, conversation_id))


def ensure_conversation(conn, patient, now):
    """기존 환자도 삭제 없이 복구한다. 쓰기 호출자는 같은 트랜잭션에서 실행한다.

    과거 업무 사건의 ID를 참여 경계로 삼아 같은 초의 재배정도 구분한다. 과거
    환자정보 스냅샷은 복구할 수 없으므로 현재 환자정보를 옛 병원에 노출하지 않는다.
    """
    existing = row(conn.execute('SELECT * FROM conversations WHERE patient_id=?', (patient['id'],)).fetchone())
    if existing:
        return existing
    cid = conn.execute('INSERT INTO conversations(patient_id,created_at) VALUES(?,?)',
                       (patient['id'], patient['requested_at'])).lastrowid
    history = rows(conn.execute('SELECT * FROM events WHERE patient_id=? ORDER BY id', (patient['id'],)))
    active = None
    for event in history:
        if event['type'] in ('destination_confirmed', 'destination_changed') and event['hospital_id']:
            if active:
                leave_conversation_hospital(conn, cid, event['at'], event['id'])
            # 역사상 원문 스냅샷이 없으므로 임의로 최신 개인정보를 과거 구간에 넣지 않는다.
            sid = conn.execute('INSERT INTO conversation_hospital_sessions '
                               '(conversation_id,hospital_id,inquiry_id,joined_at,joined_event_id,patient_snapshot) '
                               'VALUES (?,?,?,?,?,?)',
                               (cid, event['hospital_id'], event['data'].get('inquiry_id'), event['at'],
                                event['id'], dumps({'id': patient['id'], 'historical_snapshot_unavailable': True}))).lastrowid
            active = dict(id=sid, hospital_id=event['hospital_id'])
        elif event['type'] == 'patient_redirected_away':
            previous = conn.execute('SELECT id FROM conversation_hospital_sessions '
                                    'WHERE conversation_id=? AND hospital_id=? AND left_at IS NOT NULL '
                                    'ORDER BY id DESC LIMIT 1', (cid, event['hospital_id'])).fetchone()
            if previous:
                conn.execute('UPDATE conversation_hospital_sessions SET left_at=?,left_event_id=? WHERE id=?',
                             (event['at'], event['id'], previous['id']))
        elif event['type'] in ('arrival_confirmed', 'patient_cancelled', 'no_show', 'discharged'):
            close_conversation(conn, cid, event['at'], event['id'])
            active = None
    last_id = history[-1]['id'] if history else 0
    last_at = history[-1]['at'] if history else now
    if patient['state'] in ('ARRIVED', 'COMPLETED', 'CANCELLED'):
        close_conversation(conn, cid, patient.get('arrived_at') or last_at, last_id)
    elif patient['state'] in ('CONFIRMED', 'IN_TRANSIT') and patient['destination_hospital_id']:
        if not active or active['hospital_id'] != patient['destination_hospital_id']:
            leave_conversation_hospital(conn, cid, now, last_id)
            # 출발 사건 자체가 누락된 이전 DB는 복구 이후 사건만 공유한다.
            next_id = conn.execute('SELECT COALESCE(MAX(id),0)+1 FROM events').fetchone()[0]
            join_conversation_hospital(conn, cid, now, patient['destination_hospital_id'], None,
                                       next_id, patient)
        else:
            conn.execute('UPDATE conversation_hospital_sessions SET patient_snapshot=? WHERE id=?',
                         (dumps(conversation_patient_snapshot(patient)), active['id']))
    return row(conn.execute('SELECT * FROM conversations WHERE id=?', (cid,)).fetchone())
