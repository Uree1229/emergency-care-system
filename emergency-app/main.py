from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from urllib.parse import quote

from database import create_database, save_patient, get_patients, get_patient


app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent

create_database()


# ==================================================
# templates 폴더 연결
# ==================================================

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "templates"),
    name="static"
)


# ==================================================
# 메인 화면
# ==================================================

@app.get("/", response_class=HTMLResponse)
def home():

    html_file = BASE_DIR / "templates" / "index.html"

    return html_file.read_text(encoding="utf-8")


# ==================================================
# 환자 등록 화면
# ==================================================

@app.get("/register", response_class=HTMLResponse)
def register_page():

    html_file = BASE_DIR / "templates" / "register.html"

    return html_file.read_text(encoding="utf-8")


# ==================================================
# 환자 등록 처리
# ==================================================

@app.post("/register")
def register_patient(
    name: str = Form(...),
    birth: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    guardian: str = Form(""),
    guardian_phone: str = Form("")
):

    save_patient(
        name,
        birth,
        phone,
        address,
        guardian,
        guardian_phone
    )

    return HTMLResponse("""
        <!DOCTYPE html>

        <html lang="ko">

        <head>
            <meta charset="UTF-8">
            <title>등록 완료</title>
        </head>

        <body>

            <h1>환자 등록 완료!</h1>

            <p>
                환자 정보가 데이터베이스에 저장되었습니다.
            </p>

            <a href="/register">
                다시 등록하기
            </a>

            <br><br>

            <a href="/patients">
                환자 목록 보기
            </a>

        </body>

        </html>
    """)


# ==================================================
# 환자 목록
# ==================================================

@app.get("/patients", response_class=HTMLResponse)
def patients_page():

    patients = get_patients()

    rows = ""

    for patient in patients:

        rows += f"""
        <tr>

            <td>{patient[0]}</td>

            <td>
                <a href="/patients/{patient[0]}">
                    {patient[1]}
                </a>
            </td>

            <td>{patient[2] or ""}</td>

            <td>{patient[3] or ""}</td>

        </tr>
        """

    return HTMLResponse(f"""
        <!DOCTYPE html>

        <html lang="ko">

        <head>

            <meta charset="UTF-8">

            <title>환자 조회</title>

            <style>

                body {{
                    font-family: Arial, sans-serif;
                    padding: 40px;
                    background-color: #f2f4f7;
                }}

                table {{
                    width: 100%;
                    border-collapse: collapse;
                    background-color: white;
                }}

                th, td {{
                    border: 1px solid #ccc;
                    padding: 12px;
                    text-align: center;
                }}

                th {{
                    background-color: #f2f2f2;
                }}

                a {{
                    color: #1976d2;
                    text-decoration: none;
                }}

            </style>

        </head>

        <body>

            <h1>환자 목록</h1>

            <table>

                <tr>
                    <th>ID</th>
                    <th>이름</th>
                    <th>생년월일</th>
                    <th>전화번호</th>
                </tr>

                {rows}

            </table>

            <br>

            <a href="/">
                메인 화면으로
            </a>

        </body>

        </html>
    """)


# ==================================================
# 환자 상세정보
# ==================================================

@app.get("/patients/{patient_id}", response_class=HTMLResponse)
def patient_detail(patient_id: int):

    patient = get_patient(patient_id)

    if patient is None:

        return HTMLResponse("""
            <h1>환자를 찾을 수 없습니다.</h1>

            <a href="/patients">
                환자 목록으로 돌아가기
            </a>
        """)

    return HTMLResponse(f"""
        <!DOCTYPE html>

        <html lang="ko">

        <head>

            <meta charset="UTF-8">

            <title>환자 상세정보</title>

            <style>

                body {{
                    font-family: Arial, sans-serif;
                    padding: 40px;
                    background-color: #f2f4f7;
                }}

                .container {{
                    max-width: 600px;
                    margin: auto;
                    padding: 30px;
                    background-color: white;
                    border-radius: 15px;
                }}

                .info {{
                    margin-bottom: 15px;
                    padding: 15px;
                    background-color: #f5f5f5;
                    border-radius: 8px;
                }}

                .emergency-button {{
                    display: block;
                    width: 100%;
                    padding: 15px;
                    margin-top: 30px;
                    background-color: #d32f2f;
                    color: white;
                    text-align: center;
                    text-decoration: none;
                    border-radius: 8px;
                    font-weight: bold;
                }}

            </style>

        </head>

        <body>

            <div class="container">

                <h1>환자 상세정보</h1>

                <div class="info">
                    이름: {patient[1]}
                </div>

                <div class="info">
                    생년월일: {patient[2] or "등록되지 않음"}
                </div>

                <div class="info">
                    전화번호: {patient[3] or "등록되지 않음"}
                </div>

                <div class="info">
                    주소: {patient[4] or "등록되지 않음"}
                </div>

                <div class="info">
                    보호자: {patient[5] or "등록되지 않음"}
                </div>

                <div class="info">
                    보호자 전화번호:
                    {patient[6] or "등록되지 않음"}
                </div>

                <a
                    class="emergency-button"
                    href="/emergency"
                >
                    🚨 응급상황 시작
                </a>

                <br>

                <a href="/patients">
                    환자 목록으로 돌아가기
                </a>

            </div>

        </body>

        </html>
    """)


# ==================================================
# 🚨 응급상황 시작
# ==================================================

@app.get("/emergency", response_class=HTMLResponse)
def emergency_page():

    patients = get_patients()

    patient_options = ""

    for patient in patients:

        patient_options += f"""
        <option value="{patient[0]}">
            {patient[1]}
        </option>
        """

    return HTMLResponse(f"""
        <!DOCTYPE html>

        <html lang="ko">

        <head>

            <meta charset="UTF-8">

            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >

            <title>응급상황 시작</title>

            <style>

                * {{
                    box-sizing: border-box;
                }}

                body {{
                    margin: 0;
                    font-family: Arial, sans-serif;
                    background-color: #f2f4f7;

                    min-height: 100vh;

                    display: flex;
                    justify-content: center;
                    align-items: center;
                }}

                .container {{
                    width: 90%;
                    max-width: 500px;

                    background-color: white;

                    padding: 35px;

                    border-radius: 20px;

                    text-align: center;
                }}

                .icon {{
                    font-size: 60px;
                    margin-bottom: 20px;
                }}

                h1 {{
                    margin-bottom: 15px;
                }}

                .description {{
                    color: #666;
                    line-height: 1.6;
                    margin-bottom: 30px;
                }}

                select {{
                    width: 100%;

                    padding: 16px;

                    font-size: 17px;

                    border: 1px solid #ccc;

                    border-radius: 10px;

                    margin-bottom: 15px;
                }}

                button {{
                    width: 100%;

                    padding: 18px;

                    background-color: #d32f2f;

                    color: white;

                    border: none;

                    border-radius: 12px;

                    font-size: 18px;

                    font-weight: bold;

                    cursor: pointer;
                }}

                button:hover {{
                    background-color: #b71c1c;
                }}

                .guest-button {{
                    display: block;

                    width: 100%;

                    padding: 17px;

                    margin-top: 12px;

                    background-color: #555;

                    color: white;

                    text-decoration: none;

                    border-radius: 12px;

                    font-size: 17px;

                    font-weight: bold;
                }}

                .notice {{
                    margin-top: 20px;

                    font-size: 13px;

                    color: #888;
                }}

            </style>

        </head>

        <body>

            <div class="container">

                <div class="icon">
                    🚑
                </div>

                <h1>
                    응급상황 시작
                </h1>

                <p class="description">
                    등록된 환자라면 환자 정보를 불러올 수 있습니다.<br>
                    등록되지 않은 환자도 바로 진행할 수 있습니다.
                </p>

                <form action="/emergency/check" method="get">

                    <select name="patient_id">

                        <option value="">
                            기존 환자 선택
                        </option>

                        {patient_options}

                    </select>

                    <button type="submit">
                        선택한 환자로 시작
                    </button>

                </form>

                <a
                    href="/emergency/check?patient_id=0"
                    class="guest-button"
                >
                    👤 미등록 환자로 진행
                </a>

                <p class="notice">
                    ※ 실제 응급상황에서는 필요한 경우
                    즉시 119에 신고하세요.
                </p>

            </div>

        </body>

        </html>
    """)


# ==================================================
# 🚨 의식 확인
# ==================================================

@app.get("/emergency/check", response_class=HTMLResponse)
def emergency_check(patient_id: int | None = None):

    patient = None

    # 0은 미등록 환자로 사용
    if patient_id is not None and patient_id != 0:

        patient = get_patient(patient_id)

    if patient is None:

        patient_name = "미등록 환자"

        patient_id_value = "0"

    else:

        patient_name = patient[1]

        patient_id_value = str(patient[0])

    return HTMLResponse(f"""
        <!DOCTYPE html>

        <html lang="ko">

        <head>

            <meta charset="UTF-8">

            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >

            <title>의식 확인</title>

            <style>

                * {{
                    box-sizing: border-box;
                }}

                body {{
                    margin: 0;

                    font-family: Arial, sans-serif;

                    background-color: #f2f4f7;

                    min-height: 100vh;

                    display: flex;

                    justify-content: center;

                    align-items: center;
                }}

                .container {{
                    width: 90%;

                    max-width: 500px;

                    background-color: white;

                    padding: 35px;

                    border-radius: 20px;

                    text-align: center;
                }}

                .icon {{
                    font-size: 60px;

                    margin-bottom: 20px;
                }}

                .patient {{
                    color: #555;

                    margin-bottom: 30px;
                }}

                .question {{
                    font-size: 22px;

                    font-weight: bold;

                    margin-bottom: 25px;
                }}

                .answer {{
                    display: block;

                    width: 100%;

                    padding: 18px;

                    margin-bottom: 15px;

                    border-radius: 12px;

                    text-decoration: none;

                    font-size: 18px;

                    font-weight: bold;
                }}

                .yes {{
                    background-color: #1976d2;

                    color: white;
                }}

                .no {{
                    background-color: #d32f2f;

                    color: white;
                }}

            </style>

        </head>

        <body>

            <div class="container">

                <div class="icon">
                    🚑
                </div>

                <h1>
                    현재 상태 확인
                </h1>

                <div class="patient">
                    환자:
                    <strong>{patient_name}</strong>
                </div>

                <div class="question">
                    환자가 의식이 있습니까?
                </div>

                <a
                    href="/emergency/breathing?patient_id={patient_id_value}&consciousness=yes"
                    class="answer yes"
                >
                    예
                </a>

                <a
                    href="/emergency/breathing?patient_id={patient_id_value}&consciousness=no"
                    class="answer no"
                >
                    아니오
                </a>

            </div>

        </body>

        </html>
    """)


# ==================================================
# 🫁 호흡 확인
# ==================================================

@app.get("/emergency/breathing", response_class=HTMLResponse)
def emergency_breathing(
    patient_id: int | None = None,
    consciousness: str = ""
):

    patient = None

    if patient_id is not None and patient_id != 0:

        patient = get_patient(patient_id)

    if patient is None:

        patient_name = "미등록 환자"

        patient_id_value = "0"

    else:

        patient_name = patient[1]

        patient_id_value = str(patient[0])

    return HTMLResponse(f"""
        <!DOCTYPE html>

        <html lang="ko">

        <head>

            <meta charset="UTF-8">

            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >

            <title>호흡 확인</title>

            <style>

                * {{
                    box-sizing: border-box;
                }}

                body {{
                    margin: 0;

                    font-family: Arial, sans-serif;

                    background-color: #f2f4f7;

                    min-height: 100vh;

                    display: flex;

                    justify-content: center;

                    align-items: center;
                }}

                .container {{
                    width: 90%;

                    max-width: 500px;

                    background-color: white;

                    padding: 35px;

                    border-radius: 20px;

                    text-align: center;
                }}

                .icon {{
                    font-size: 60px;

                    margin-bottom: 20px;
                }}

                .patient {{
                    color: #555;

                    margin-bottom: 30px;
                }}

                .question {{
                    font-size: 22px;

                    font-weight: bold;

                    margin: 30px 0;
                }}

                .answer {{
                    display: block;

                    width: 100%;

                    padding: 18px;

                    margin-bottom: 15px;

                    border-radius: 12px;

                    text-decoration: none;

                    font-size: 18px;

                    font-weight: bold;
                }}

                .yes {{
                    background-color: #1976d2;

                    color: white;
                }}

                .no {{
                    background-color: #d32f2f;

                    color: white;
                }}

            </style>

        </head>

        <body>

            <div class="container">

                <div class="icon">
                    🫁
                </div>

                <h1>
                    호흡 확인
                </h1>

                <div class="patient">
                    환자:
                    <strong>{patient_name}</strong>
                </div>

                <div class="question">
                    환자가 정상적으로<br>
                    호흡하고 있습니까?
                </div>

                <a
                    href="/emergency/symptoms?patient_id={patient_id_value}&consciousness={consciousness}&breathing=yes"
                    class="answer yes"
                >
                    예
                </a>

                <a
                    href="/emergency/symptoms?patient_id={patient_id_value}&consciousness={consciousness}&breathing=no"
                    class="answer no"
                >
                    아니오
                </a>

            </div>

        </body>

        </html>
    """)


# ==================================================
# 🩺 증상 확인
# ==================================================

@app.get("/emergency/symptoms", response_class=HTMLResponse)
def emergency_symptoms(
    patient_id: int | None = None,
    consciousness: str = "",
    breathing: str = ""
):

    patient = None

    if patient_id is not None and patient_id != 0:

        patient = get_patient(patient_id)

    if patient is None:

        patient_name = "미등록 환자"

        patient_id_value = "0"

    else:

        patient_name = patient[1]

        patient_id_value = str(patient[0])

    return HTMLResponse(f"""
        <!DOCTYPE html>

        <html lang="ko">

        <head>

            <meta charset="UTF-8">

            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >

            <title>증상 확인</title>

            <style>

                * {{
                    box-sizing: border-box;
                }}

                body {{
                    margin: 0;

                    font-family: Arial, sans-serif;

                    background-color: #f2f4f7;

                    min-height: 100vh;

                    display: flex;

                    justify-content: center;

                    align-items: center;
                }}

                .container {{
                    width: 90%;

                    max-width: 500px;

                    background-color: white;

                    padding: 35px;

                    border-radius: 20px;

                    text-align: center;
                }}

                .icon {{
                    font-size: 60px;

                    margin-bottom: 20px;
                }}

                .patient {{
                    color: #555;

                    margin-bottom: 30px;
                }}

                .question {{
                    font-size: 22px;

                    font-weight: bold;

                    margin: 30px 0;
                }}

                button {{
                    display: block;

                    width: 100%;

                    padding: 17px;

                    margin-bottom: 12px;

                    border: none;

                    border-radius: 12px;

                    background-color: #1976d2;

                    color: white;

                    font-size: 17px;

                    font-weight: bold;

                    cursor: pointer;
                }}

                button:hover {{
                    background-color: #125aa0;
                }}

            </style>

        </head>

        <body>

            <div class="container">

                <div class="icon">
                    🩺
                </div>

                <h1>
                    증상 확인
                </h1>

                <div class="patient">
                    환자:
                    <strong>{patient_name}</strong>
                </div>

                <div class="question">
                    현재 가장 주요한 증상은 무엇인가요?
                </div>

                <form action="/emergency/result" method="get">

                    <input
                        type="hidden"
                        name="patient_id"
                        value="{patient_id_value}"
                    >

                    <input
                        type="hidden"
                        name="consciousness"
                        value="{consciousness}"
                    >

                    <input
                        type="hidden"
                        name="breathing"
                        value="{breathing}"
                    >

                    <button
                        type="submit"
                        name="symptom"
                        value="가슴 통증"
                    >
                        ❤️ 가슴 통증
                    </button>

                    <button
                        type="submit"
                        name="symptom"
                        value="호흡 곤란"
                    >
                        🫁 호흡 곤란
                    </button>

                    <button
                        type="submit"
                        name="symptom"
                        value="출혈"
                    >
                        🩸 출혈
                    </button>

                    <button
                        type="submit"
                        name="symptom"
                        value="의식 저하"
                    >
                        😵 의식 저하
                    </button>

                    <button
                        type="submit"
                        name="symptom"
                        value="기타"
                    >
                        기타
                    </button>

                </form>

            </div>

        </body>

        </html>
    """)


# ==================================================
# 📋 구급대 전달 정보 (GPS 자동 취득 기능 포함)
# ==================================================

@app.get("/emergency/report", response_class=HTMLResponse)
def emergency_report(
    patient_id: int | None = None,
    consciousness: str = "",
    breathing: str = "",
    symptom: str = ""
):

    patient = None

    if patient_id is not None and patient_id != 0:
        patient = get_patient(patient_id)

    if patient is None:
        patient_name = "미등록 환자"
        birth = "등록되지 않음"
        phone = "등록되지 않음"
        address = "등록되지 않음"
        guardian = "등록되지 않음"
        guardian_phone = "등록되지 않음"
    else:
        patient_name = patient[1]
        birth = patient[2] or "등록되지 않음"
        phone = patient[3] or "등록되지 않음"
        address = patient[4] or "등록되지 않음"
        guardian = patient[5] or "등록되지 않음"
        guardian_phone = patient[6] or "등록되지 않음"

    if consciousness == "yes":
        consciousness_text = "있음"
        consciousness_class = "normal"
    else:
        consciousness_text = "없음"
        consciousness_class = "warning"

    if breathing == "yes":
        breathing_text = "정상"
        breathing_class = "normal"
    else:
        breathing_text = "비정상"
        breathing_class = "warning"

    symptom_text = symptom or "확인되지 않음"

    return HTMLResponse(f"""
        <!DOCTYPE html>
        <html lang="ko">

        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>구급대 전달 정보</title>

            <style>
                * {{
                    box-sizing: border-box;
                }}

                body {{
                    margin: 0;
                    font-family: Arial, sans-serif;
                    background-color: #f2f4f7;
                    min-height: 100vh;
                    padding: 30px 0;
                }}

                .container {{
                    width: 90%;
                    max-width: 500px;
                    margin: auto;
                    background-color: white;
                    padding: 30px;
                    border-radius: 20px;
                }}

                h1 {{
                    text-align: center;
                    margin-bottom: 10px;
                }}

                .description {{
                    text-align: center;
                    color: #666;
                    line-height: 1.5;
                    margin-bottom: 30px;
                }}

                .section-title {{
                    font-size: 19px;
                    font-weight: bold;
                    margin-top: 25px;
                    margin-bottom: 12px;
                }}

                .info {{
                    background-color: #f5f5f5;
                    padding: 15px;
                    margin-bottom: 10px;
                    border-radius: 10px;
                }}

                .label {{
                    font-weight: bold;
                    margin-bottom: 5px;
                }}

                .value {{
                    font-size: 17px;
                    word-break: break-word;
                }}

                .emergency {{
                    padding: 16px;
                    margin-bottom: 10px;
                    border-radius: 10px;
                }}

                .normal {{
                    background-color: #e3f2fd;
                    border-left: 5px solid #1976d2;
                }}

                .warning {{
                    background-color: #ffebee;
                    border-left: 5px solid #d32f2f;
                }}

                .symptom {{
                    background-color: #fff8e1;
                    border-left: 5px solid #f9a825;
                    padding: 16px;
                    margin-bottom: 10px;
                    border-radius: 10px;
                }}

                .location {{
                    background-color: #e8f5e9;
                    border-left: 5px solid #2e7d32;
                    padding: 18px;
                    border-radius: 10px;
                }}

                .map-btn {{
                    display: inline-block;
                    margin-top: 10px;
                    padding: 10px 15px;
                    background-color: #2e7d32;
                    color: white;
                    text-decoration: none;
                    border-radius: 6px;
                    font-weight: bold;
                    font-size: 14px;
                }}

                .button {{
                    display: block;
                    width: 100%;
                    padding: 18px;
                    margin-top: 25px;
                    background-color: #d32f2f;
                    color: white;
                    text-decoration: none;
                    text-align: center;
                    border-radius: 12px;
                    font-size: 18px;
                    font-weight: bold;
                }}

                .home {{
                    display: block;
                    text-align: center;
                    margin-top: 20px;
                    color: #555;
                    text-decoration: none;
                }}
            </style>
        </head>

        <body>

            <div class="container">

                <h1>🚑 구급대 전달 정보</h1>

                <div class="description">
                    현재 확인된 환자 정보를<br>
                    구급대 전달용으로 정리했습니다.
                </div>

                <div class="section-title">
                    👤 환자 정보
                </div>

                <div class="info">
                    <div class="label">이름</div>
                    <div class="value">{patient_name}</div>
                </div>

                <div class="info">
                    <div class="label">생년월일</div>
                    <div class="value">{birth}</div>
                </div>

                <div class="info">
                    <div class="label">전화번호</div>
                    <div class="value">{phone}</div>
                </div>

                <div class="info">
                    <div class="label">주소</div>
                    <div class="value">{address}</div>
                </div>

                <div class="info">
                    <div class="label">보호자</div>
                    <div class="value">{guardian}</div>
                </div>

                <div class="info">
                    <div class="label">보호자 전화번호</div>
                    <div class="value">{guardian_phone}</div>
                </div>

                <div class="section-title">
                    🚨 응급 상태
                </div>

                <div class="emergency {consciousness_class}">
                    <div class="label">의식</div>
                    <div class="value">{consciousness_text}</div>
                </div>

                <div class="emergency {breathing_class}">
                    <div class="label">호흡</div>
                    <div class="value">{breathing_text}</div>
                </div>

                <div class="symptom">
                    <div class="label">주요 증상</div>
                    <div class="value">{symptom_text}</div>
                </div>

                <div class="section-title">
                    📍 현재 위치 (GPS)
                </div>

                <div class="location" id="gps-box">
                    <div id="gps-status">위치 정보를 불러오는 중입니다...</div>
                    <div id="gps-coords" class="value" style="font-weight: bold; margin-top: 5px;"></div>
                    <a id="map-link" class="map-btn" href="#" target="_blank" style="display:none;">지도에서 위치 확인</a>
                </div>

                <a href="/" class="button">🏠 처음 화면으로</a>
                <a href="/emergency" class="home">응급상황 다시 시작</a>

            </div>

            <script>
                // 브라우저 GPS 위치 추적
                if ("geolocation" in navigator) {{
                    navigator.geolocation.getCurrentPosition(
                        function(position) {{
                            const lat = position.coords.latitude;
                            const lon = position.coords.longitude;
                            const accuracy = Math.round(position.coords.accuracy);

                            document.getElementById("gps-status").innerText = "현재 GPS 위치 수신 완료 (오차 ±" + accuracy + "m)";
                            document.getElementById("gps-coords").innerText = "위도: " + lat.toFixed(6) + " / 경도: " + lon.toFixed(6);

                            const mapBtn = document.getElementById("map-link");
                            mapBtn.href = "https://map.kakao.com/link/map/현재위치," + lat + "," + lon;
                            mapBtn.style.display = "inline-block";
                        }},
                        function(error) {{
                            document.getElementById("gps-status").innerText = "위치 정보를 가져올 수 없습니다. (권한 거부 또는 측정 불가)";
                        }},
                        {{
                            enableHighAccuracy: true,
                            timeout: 10000,
                            maximumAge: 0
                        }}
                    );
                }} else {{
                    document.getElementById("gps-status").innerText = "이 브라우저는 GPS 위치 정보를 지원하지 않습니다.";
                }}
            </script>

        </body>

        </html>
    """)

# 📋 응급환자 최종 정보
# ==================================================

@app.get("/emergency/result", response_class=HTMLResponse)
def emergency_result(
    patient_id: int | None = None,
    consciousness: str = "",
    breathing: str = "",
    symptom: str = ""
):

    patient = None

    if patient_id is not None and patient_id != 0:

        patient = get_patient(patient_id)

    if patient is None:

        patient_name = "미등록 환자"

        birth = "등록되지 않음"
        phone = "등록되지 않음"
        address = "등록되지 않음"
        guardian = "등록되지 않음"
        guardian_phone = "등록되지 않음"

    else:

        patient_name = patient[1]

        birth = patient[2] or "등록되지 않음"
        phone = patient[3] or "등록되지 않음"
        address = patient[4] or "등록되지 않음"
        guardian = patient[5] or "등록되지 않음"
        guardian_phone = patient[6] or "등록되지 않음"


    # 의식 상태
    if consciousness == "yes":

        consciousness_text = "있음"

        consciousness_class = "normal"

    else:

        consciousness_text = "없음"

        consciousness_class = "warning"


    # 호흡 상태
    if breathing == "yes":

        breathing_text = "정상"

        breathing_class = "normal"

    else:

        breathing_text = "비정상"

        breathing_class = "warning"


    return HTMLResponse(f"""
        <!DOCTYPE html>

        <html lang="ko">

        <head>

            <meta charset="UTF-8">

            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >

            <title>응급환자 정보</title>

            <style>

                * {{
                    box-sizing: border-box;
                }}

                body {{
                    margin: 0;

                    font-family: Arial, sans-serif;

                    background-color: #f2f4f7;

                    min-height: 100vh;

                    padding: 30px 0;
                }}

                .container {{
                    width: 90%;

                    max-width: 500px;

                    margin: auto;

                    background-color: white;

                    padding: 30px;

                    border-radius: 20px;
                }}

                h1 {{
                    text-align: center;

                    margin-bottom: 30px;
                }}

                .section-title {{
                    font-size: 19px;

                    font-weight: bold;

                    margin-top: 25px;

                    margin-bottom: 12px;
                }}

                .info {{
                    background-color: #f5f5f5;

                    padding: 16px;

                    margin-bottom: 10px;

                    border-radius: 10px;
                }}

                .label {{
                    font-weight: bold;

                    margin-bottom: 5px;
                }}

                .value {{
                    font-size: 17px;
                }}

                .emergency {{
                    padding: 16px;

                    margin-bottom: 10px;

                    border-radius: 10px;
                }}

                /* 정상 상태 */

                .normal {{
                    background-color: #e3f2fd;

                    border-left: 5px solid #1976d2;
                }}

                /* 주의가 필요한 상태 */

                .warning {{
                    background-color: #ffebee;

                    border-left: 5px solid #d32f2f;
                }}

                /* 증상 */

                .symptom {{
                    background-color: #fff8e1;

                    border-left: 5px solid #f9a825;

                    padding: 16px;

                    margin-bottom: 10px;

                    border-radius: 10px;
                }}

                .button {{
                    display: block;

                    width: 100%;

                    padding: 18px;

                    margin-top: 25px;

                    background-color: #d32f2f;

                    color: white;

                    text-decoration: none;

                    text-align: center;

                    border-radius: 12px;

                    font-size: 18px;

                    font-weight: bold;
                }}

                .home {{
                    display: block;

                    text-align: center;

                    margin-top: 20px;

                    color: #555;

                    text-decoration: none;
                }}

            </style>

        </head>

        <body>

            <div class="container">

                <h1>
                    🚨 응급환자 정보
                </h1>


                <!-- ============================ -->
                <!-- 환자 정보 -->
                <!-- ============================ -->

                <div class="section-title">
                    👤 환자 정보
                </div>

                <div class="info">

                    <div class="label">
                        이름
                    </div>

                    <div class="value">
                        {patient_name}
                    </div>

                </div>

                <div class="info">

                    <div class="label">
                        생년월일
                    </div>

                    <div class="value">
                        {birth}
                    </div>

                </div>

                <div class="info">

                    <div class="label">
                        전화번호
                    </div>

                    <div class="value">
                        {phone}
                    </div>

                </div>

                <div class="info">

                    <div class="label">
                        주소
                    </div>

                    <div class="value">
                        {address}
                    </div>

                </div>

                <div class="info">

                    <div class="label">
                        보호자
                    </div>

                    <div class="value">
                        {guardian}
                    </div>

                </div>

                <div class="info">

                    <div class="label">
                        보호자 전화번호
                    </div>

                    <div class="value">
                        {guardian_phone}
                    </div>

                </div>


                <!-- ============================ -->
                <!-- 현재 응급상태 -->
                <!-- ============================ -->

                <div class="section-title">
                    🚨 현재 응급상태
                </div>


                <div class="emergency {consciousness_class}">

                    <div class="label">
                        의식
                    </div>

                    <div class="value">
                        {consciousness_text}
                    </div>

                </div>


                <div class="emergency {breathing_class}">

                    <div class="label">
                        호흡
                    </div>

                    <div class="value">
                        {breathing_text}
                    </div>

                </div>


                <div class="symptom">

                    <div class="label">
                        주요 증상
                    </div>

                    <div class="value">
                        {symptom}
                    </div>

                </div>


                <!-- ============================ -->
                <!-- 구급대 전달 -->
                <!-- ============================ -->

                <a
                    href="/emergency/report?patient_id={patient_id or 0}&consciousness={consciousness}&breathing={breathing}&symptom={quote(symptom)}"
                    class="button"
                >
                    📋 구급대 전달 정보 확인
                </a>


                <a
                    href="/"
                    class="home"
                >
                    처음으로 돌아가기
                </a>

            </div>

        </body>

        </html>
    """)