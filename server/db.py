"""SQLite 저장소. 시각은 모두 UTC epoch 초(int)로 저장한다 (S14)."""
import json
import sqlite3
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
    source_ids TEXT NOT NULL DEFAULT '{}'      -- 사람이 확인한 출처별 식별자만 저장
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
"""

JSON_FIELDS = {'capabilities', 'source_ids', 'extra', 'danger_signs', 'required_capabilities',
               'resource_demands', 'data'}


def connect(path):
    conn = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    if path != ':memory:':
        conn.execute('PRAGMA journal_mode = WAL')
    return conn


def init(conn):
    conn.executescript(SCHEMA)


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
        out[k] = json.loads(out[k])
    return out


def rows(rs):
    return [row(r) for r in rs]


def dumps(value):
    return json.dumps(value, ensure_ascii=False)
