import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "emergency.db"


# 데이터베이스 생성
def create_database():

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

    conn.commit()
    conn.close()


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