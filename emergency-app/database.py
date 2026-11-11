import sqlite3
import os
import secrets
from contextlib import closing
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("EMERGENCY_PROFILE_DB", str(BASE_DIR / "emergency.db")))


# 데이터베이스 생성
def create_database():

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            birth TEXT,
            phone TEXT,
            address TEXT,
            guardian TEXT,
            guardian_phone TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS intake_retry_key (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            secret BLOB NOT NULL
        )
    """)
    cursor.execute('INSERT OR IGNORE INTO intake_retry_key VALUES (1, ?)', (secrets.token_bytes(32),))

    conn.commit()
    conn.close()


def get_retry_signing_key():
    """Keep retry signatures valid across restarts using the same profile DB."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        return conn.execute('SELECT secret FROM intake_retry_key WHERE id = 1').fetchone()[0]


# 환자 정보 저장
def save_patient(
    name,
    birth,
    phone,
    address,
    guardian,
    guardian_phone
):

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO patients
        (name, birth, phone, address, guardian, guardian_phone)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        name,
        birth,
        phone,
        address,
        guardian,
        guardian_phone
    ))

    conn.commit()
    conn.close()


# 환자 목록 가져오기
def get_patients():

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            birth,
            phone,
            address,
            guardian,
            guardian_phone
        FROM patients
        ORDER BY id DESC
    """)

    patients = cursor.fetchall()

    conn.close()

    return patients


# 특정 환자 정보 가져오기
def get_patient(patient_id):

    conn = sqlite3.connect(DB_PATH)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            birth,
            phone,
            address,
            guardian,
            guardian_phone
        FROM patients
        WHERE id = ?
    """, (patient_id,))

    patient = cursor.fetchone()

    conn.close()

    return patient


# 데이터베이스 생성
create_database()
