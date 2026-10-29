from fastapi import FastAPI, Form, Query

from fastapi.responses import HTMLResponse, RedirectResponse

from fastapi.staticfiles import StaticFiles

from pathlib import Path

import math

import os

import json

import urllib.parse

import urllib.request

import urllib.error

import xml.etree.ElementTree as ET

from urllib.parse import quote
from html import escape
from datetime import datetime, timedelta, timezone
from common_server import (CommonServerError, hospital_catalog, new_submission_id,
                           build_episode_payload, create_episode, transport_url)
from api_integrations import enrich_hospitals, enrich_hospitals_with_hira, enrich_hospitals_with_severe



# .env 파일이 있으면 환경변수를 불러옵니다. 별도 패키지는 필요하지 않습니다.

# .env 파일은 GitHub에 올리지 마세요.

def load_local_env():

    env_file = Path(__file__).resolve().parent / ".env"

    if not env_file.exists():

        return

    for line in env_file.read_text(encoding="utf-8").splitlines():

        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:

            continue

        key, value = line.split("=", 1)

        key = key.strip()

        value = value.strip().strip('"').strip("'")

        if key and value:

            os.environ.setdefault(key, value)



load_local_env()



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

                    {escape(str(patient[1]))}

                </a>

            </td>



            <td>{escape(str(patient[2] or ""))}</td>



            <td>{escape(str(patient[3] or ""))}</td>



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

                    이름: {escape(str(patient[1]))}

                </div>



                <div class="info">

                    생년월일: {escape(str(patient[2] or "등록되지 않음"))}

                </div>



                <div class="info">

                    전화번호: {escape(str(patient[3] or "등록되지 않음"))}

                </div>



                <div class="info">

                    주소: {escape(str(patient[4] or "등록되지 않음"))}

                </div>



                <div class="info">

                    보호자: {escape(str(patient[5] or "등록되지 않음"))}

                </div>



                <div class="info">

                    보호자 전화번호:

                    {escape(str(patient[6] or "등록되지 않음"))}

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

            {escape(str(patient[1]))}

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

def emergency_check(patient_id: str = ""):



    patient = None



    # 선택하지 않았거나 0이면 미등록 환자로 처리합니다.

    # HTML select에서 빈 문자열이 넘어와도 422 오류가 발생하지 않도록

    # 여기서 안전하게 숫자로 변환합니다.

    try:

        patient_id_int = int(patient_id) if patient_id.strip() else 0

    except ValueError:

        patient_id_int = 0



    if patient_id_int != 0:



        patient = get_patient(patient_id_int)



    if patient is None:



        patient_name = "미등록 환자"



        patient_id_value = "0"



    else:



        patient_name = escape(str(patient[1]))



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



    consciousness = consciousness if consciousness in ("yes", "no") else "unknown"
    patient = None



    if patient_id is not None and patient_id != 0:



        patient = get_patient(patient_id)



    if patient is None:



        patient_name = "미등록 환자"



        patient_id_value = "0"



    else:



        patient_name = escape(str(patient[1]))



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



    consciousness = consciousness if consciousness in ("yes", "no") else "unknown"
    breathing = breathing if breathing in ("yes", "no") else "unknown"
    patient = None



    if patient_id is not None and patient_id != 0:



        patient = get_patient(patient_id)



    if patient is None:



        patient_name = "미등록 환자"



        patient_id_value = "0"



    else:



        patient_name = escape(str(patient[1]))



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



                            .symptom-options {{ display:grid; gap:10px; margin-bottom:22px; text-align:left; }}

                .symptom-choice {{ display:flex; align-items:center; gap:12px; padding:16px; border:2px solid #d9e3f0; border-radius:12px; background:#f8fafc; font-size:17px; cursor:pointer; }}

                .symptom-choice:has(input:checked) {{ border-color:#1976d2; background:#e3f2fd; font-weight:bold; }}

                .symptom-choice input {{ width:21px; height:21px; accent-color:#1976d2; }}

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



                <div class="question">해당하는 증상을 모두 선택해주세요.</div>

                <form action="/emergency/result" method="get">

                    <input type="hidden" name="patient_id" value="{patient_id_value}">

                    <input type="hidden" name="consciousness" value="{consciousness}">

                    <input type="hidden" name="breathing" value="{breathing}">

                    <div class="symptom-options">

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="가슴 통증">❤️ 가슴 통증</label>

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="호흡 곤란">🫁 호흡 곤란</label>

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="출혈">🩸 출혈</label>

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="의식 저하">😵 의식 저하</label>

                        <label class="symptom-choice"><input type="checkbox" name="symptom" value="기타">➕ 기타</label>

                    </div>

                    <button type="submit">선택 완료 · 다음으로</button>

                </form>



            </div>



        </body>



        </html>

    """)





# ==================================================

# 🏥 전국 응급의료기관 API 연동

# 공공데이터포털: 국립중앙의료원 전국 응급의료기관 정보 조회 서비스

# 인증키는 환경변수 EMERGENCY_API_KEY에 보관합니다.

# ==================================================



API_BASE = "https://apis.data.go.kr/B552657/ErmctInfoInqireService"





def calculate_distance_km(lat1, lon1, lat2, lon2):

    """두 GPS 좌표 사이의 대략적인 직선거리를 km 단위로 계산합니다."""

    earth_radius_km = 6371

    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

    dlat = lat2 - lat1

    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2

    return earth_radius_km * 2 * math.asin(math.sqrt(a))





def reverse_geocode(lat, lon):

    """GPS 좌표를 시도/시군구로 변환합니다. 외부 지오코딩 서비스 장애 시 None을 반환합니다."""

    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode({

        "format": "jsonv2", "lat": lat, "lon": lon, "zoom": 10, "addressdetails": 1, "accept-language": "ko"

    })

    request = urllib.request.Request(url, headers={"User-Agent": "EmergencyPatientSupportCapstone/1.0 (student project)"})

    try:

        with urllib.request.urlopen(request, timeout=8) as response:

            payload = json.loads(response.read().decode("utf-8"))

        address = payload.get("address", {})

        stage1 = address.get("state") or address.get("province") or address.get("city")

        stage2 = address.get("city_district") or address.get("county") or address.get("city") or address.get("town") or address.get("municipality")

        if not stage1 or not stage2:

            return None, None

        # 국내 API에서 자주 사용하는 행정구역 명칭 형태로 정리합니다.

        stage1_aliases = {

            "서울": "서울특별시", "서울특별시": "서울특별시",

            "부산": "부산광역시", "부산광역시": "부산광역시",

            "대구": "대구광역시", "대구광역시": "대구광역시",

            "인천": "인천광역시", "인천광역시": "인천광역시",

            "광주": "광주광역시", "광주광역시": "광주광역시",

            "대전": "대전광역시", "대전광역시": "대전광역시",

            "울산": "울산광역시", "울산광역시": "울산광역시",

            "세종": "세종특별자치시", "세종특별자치시": "세종특별자치시",

            "경기도": "경기도", "강원특별자치도": "강원도", "강원도": "강원도",

            "충청북도": "충청북도", "충청남도": "충청남도", "전북특별자치도": "전라북도",

            "전라북도": "전라북도", "전라남도": "전라남도", "경상북도": "경상북도",

            "경상남도": "경상남도", "제주특별자치도": "제주특별자치도"

        }

        stage1 = stage1_aliases.get(stage1, stage1)

        # 응급의료 API는 시군구 공백을 생략한 이름도 사용하므로 함께 정리합니다.

        stage2 = stage2.replace(" ", "")

        return stage1, stage2

    except Exception:

        return None, None





def api_xml(operation, params, timeout=10):

    """공공데이터 XML을 요청하고 응답 오류를 검사합니다."""

    url = API_BASE + "/" + operation + "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(

        url, headers={"User-Agent": "EmergencyPatientSupportCapstone/1.0"}

    )

    with urllib.request.urlopen(request, timeout=timeout) as response:

        root = ET.fromstring(response.read())

    code = root.findtext(".//header/resultCode", default="")

    if code and code != "00":

        msg = root.findtext(".//header/resultMsg", default="알 수 없는 API 오류")

        raise ValueError(f"API 오류 {code}: {msg}")

    return root





def fetch_hospital_basic(hpid, api_key):

    """실시간 병상 API에 없는 주소/좌표/진료과를 기관 기본정보에서 조회합니다."""

    root = api_xml("getEgytBassInfoInqire",

                   {"serviceKey": api_key, "HPID": hpid}, timeout=6)

    item = root.find(".//body/items/item")

    if item is None:

        return None



    lat_text = (item.findtext("wgs84Lat") or "").strip()

    lon_text = (item.findtext("wgs84Lon") or "").strip()

    try:

        lat, lon = float(lat_text), float(lon_text)

        if not (33 <= lat <= 39 and 124 <= lon <= 132):

            lat, lon = None, None

    except ValueError:

        lat, lon = None, None



    departments = []

    # dg1\~dg12는 서비스 응답에 따라 코드/설명일 수 있으므로

    # 명확한 과목명이 아닌 값은 임의로 해석하지 않습니다.

    for i in range(1, 13):

        value = (item.findtext(f"dg{i}") or "").strip()

        if value and value not in ("Y", "N", "1", "0") and value not in departments:

            departments.append(value)



    return {

        "lat": lat, "lon": lon,

        "address": (item.findtext("dutyAddr") or "").strip(),

        "phone": (item.findtext("dutyTel3") or "").strip(),

        "departments": ", ".join(departments) if departments else "미확인",

    }





def fetch_emergency_hospitals(lat, lon):

    """실시간 병상 데이터와 병원 기본정보를 HPID로 연결합니다."""

    api_key = os.getenv("EMERGENCY_API_KEY", "").strip()

    if not api_key:

        return [], "EMERGENCY_API_KEY가 설정되지 않았습니다."



    stage1, stage2 = reverse_geocode(lat, lon)

    if not stage1 or not stage2:

        return [], "GPS 좌표의 행정구역을 확인하지 못했습니다."



    # 확인된 실시간 API는 '경기', '성남시'처럼 축약 시도명을 허용합니다.

    stage1_short = {

        "경기도": "경기", "서울특별시": "서울", "부산광역시": "부산",

        "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",

        "대전광역시": "대전", "울산광역시": "울산",

        "강원도": "강원", "충청북도": "충북", "충청남도": "충남",

        "전라북도": "전북", "전라남도": "전남",

        "경상북도": "경북", "경상남도": "경남",

        "제주특별자치도": "제주", "세종특별자치시": "세종",

    }.get(stage1, stage1)



    try:

        root = api_xml(

            "getEmrrmRltmUsefulSckbdInfoInqire",

            {"serviceKey": api_key, "STAGE1": stage1_short,

             "STAGE2": stage2, "pageNo": 1, "numOfRows": 100},

            timeout=15

        )

        items = root.findall(".//body/items/item")

        if not items:

            return [], f"{stage1} {stage2}: 실시간 응급실 데이터가 없습니다."



        hospitals = []

        errors = 0

        # 먼저 실시간 API로 병원 식별번호를 가져오고,

        # 기관 기본정보 API에서 좌표/주소를 보완합니다.

        for item in items[:30]:

            hpid = (item.findtext("hpid") or "").strip()

            if not hpid:

                continue

            try:

                basic = fetch_hospital_basic(hpid, api_key)

            except Exception:

                errors += 1

                continue

            if not basic or basic["lat"] is None or basic["lon"] is None:

                continue



            beds_raw = (item.findtext("hvec") or "").strip()

            try:

                beds_num = int(beds_raw)

                beds = str(beds_num) if beds_num >= 0 else "미확인 (음수 값)"

            except ValueError:

                beds = "미확인"



            # 실시간 API의 원본 필드: 응급실 병상과 같은 응답에서 추가 자원 확인
            # 음수는 가용 자원으로 오해하지 않도록 그대로 수치화하지 않습니다.
            resource_fields = {
                "operating_room": "hvoc",
                "icu_general": "hvicc",
                "icu_neuro": "hvcc",
            }
            resource_values = {}
            for label, tag in resource_fields.items():
                raw = (item.findtext(tag) or "").strip()
                try:
                    number = int(raw)
                    resource_values[label] = str(number) if number >= 0 else "미확인 (음수 값)"
                except ValueError:
                    resource_values[label] = "미확인"

            def availability(tag):
                raw = (item.findtext(tag) or "").strip().upper()
                if raw == "Y":
                    return "가능 표시 (병원 확인 필요)"
                if raw == "N":
                    return "불가 표시"
                return "미확인"

            resource_values["ct"] = availability("hvctayn")
            resource_values["mri"] = availability("hvmriayn")

            hospitals.append({

                "name": (item.findtext("dutyName") or "이름 미확인").strip(),

                "lat": basic["lat"], "lon": basic["lon"],

                "emergency_beds": beds,
                "resources": resource_values,

                "address": basic["address"] or "주소 미확인",

                "phone": basic["phone"] or (item.findtext("dutyTel3") or ""),

                "departments": basic["departments"],

                "hpid": hpid

            })



        if not hospitals:

            return [], (

                f"{stage1} {stage2} 실시간 데이터 {len(items)}건은 받았으나 "

                f"병원 기본정보/좌표 연결에 실패했습니다. "

                f"(기본정보 API 요청 실패: {errors}건) "

                "해당 API의 기본정보 조회 권한과 응답 필드를 확인해주세요."

            )



        hospitals.sort(

            key=lambda h: calculate_distance_km(lat, lon, h["lat"], h["lon"])

        )

        return hospitals[:10], None

    except urllib.error.HTTPError as e:

        return [], f"공공 API HTTP 오류: {e.code}"

    except urllib.error.URLError:

        return [], "공공 API 네트워크 연결에 실패했습니다."

    except (ET.ParseError, ValueError) as e:

        return [], f"공공 API 응답 처리 오류: {e}"

    except Exception as e:

        return [], f"병원 데이터 처리 오류: {type(e).__name__}"





# ==================================================

# 📋 구급대 전달 정보

# ==================================================



def _observed_state(value, yes_text, no_text):
    if value == "yes":
        return yes_text, "normal"
    if value == "no":
        return no_text, "warning"
    return "미확인", "unknown"


def _display_value(value):
    return escape(str(value)) if value is not None and value != "" else "미확인"


def _display_time(value):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "미확인"
    try:
        return datetime.fromtimestamp(value, timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S KST")
    except (ValueError, OverflowError, OSError):
        return "미확인"


def _stored_hospital_cards(hospitals, latitude, longitude):
    """Read-only display: public observations never become operational capacities."""
    resource_labels = {'ed_bed': '진료 자리', 'special_team': '전문팀'}
    state_labels = {'available': '확인됨', 'limited': '제한', 'unknown': '미확인'}
    source_labels = {'synthetic': '가상 시연', 'hospital_input': '병원 입력', 'public': '공공 관측'}
    def distance(hospital):
        if latitude is None or longitude is None:
            return math.inf
        try:
            return calculate_distance_km(latitude, longitude, float(hospital['lat']), float(hospital['lon']))
        except (TypeError, ValueError, KeyError):
            return math.inf

    listed = sorted(hospitals, key=lambda item: (distance(item), str(item.get('id', ''))))
    cards = []
    for hospital in listed[:5]:
        km = distance(hospital)
        location = f"약 {km:.1f}km (직선거리)" if math.isfinite(km) else "현재 위치 미확인 · 거리 미표시"
        caps = hospital.get('capabilities') or []
        capability_text = ', '.join(str(item) for item in caps) or "미확인"
        if hospital.get('capabilities_status') == 'unknown':
            capability_text += " · 진료 역량 미확인"
        operational = []
        for resource in hospital.get('resources') or []:
            operational.append(
                '<div class="hospital-detail">' + _display_value(resource_labels.get(resource.get('resource_type'), resource.get('resource_type')))
                + ' · 상태 ' + _display_value(state_labels.get(resource.get('status'), resource.get('status')))
                + ' · 총량 ' + _display_value(resource.get('capacity'))
                + ' · 점유 ' + _display_value(resource.get('occupied'))
                + ' · 갱신 ' + _display_time(resource.get('updated_at'))
                + ' · 출처 ' + _display_value(source_labels.get(resource.get('source'), resource.get('source'))) + '</div>')
        observations = []
        for snapshot in hospital.get('public_data') or []:
            detail = escape(json.dumps(snapshot.get('data'), ensure_ascii=False, indent=2))
            observations.append(
                '<details class="hospital-detail"><summary>' + _display_value(snapshot.get('endpoint'))
                + '</summary><div>관측 시각: ' + _display_time(snapshot.get('source_observed_at'))
                + '<br>서버 수집 시각: ' + _display_time(snapshot.get('fetched_at'))
                + '<br>갱신 상태: ' + ('미확인' if snapshot.get('stale') is None else '오래된 관측' if snapshot.get('stale') else '관측 기준 이내')
                + '</div><pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;">'
                + detail + '</pre></details>')
        cards.append(
            '<div class="hospital-card"><div class="hospital-name">🏥 ' + _display_value(hospital.get('name'))
            + '</div><div class="hospital-tag">공통 서버 저장정보 · ' + _display_value(hospital.get('source'))
            + '</div><div class="hospital-detail"><strong>거리:</strong> ' + escape(location)
            + '</div><div class="hospital-detail"><strong>응급실 전화:</strong> ' + _display_value(hospital.get('phone'))
            + '</div><div class="hospital-address">' + _display_value(hospital.get('address'))
            + '</div><div class="hospital-detail"><strong>등록된 진료 분야:</strong> ' + escape(capability_text)
            + '</div><div class="hospital-detail muted">저장된 정보는 해당 환자에 대한 수용 확답이 아닙니다. 공공 관측값과 병원이 입력한 운영 자원은 별도입니다.</div>'
            + '<details class="hospital-detail"><summary><strong>병원 입력 운영 자원</strong></summary>'
            + (''.join(operational) or '<div>운영 자원 미확인</div>')
            + '</details><details class="hospital-detail"><summary><strong>공공 API 저장 관측정보 (현재 수용 확약 아님)</strong></summary>'
            + (''.join(observations) or '<div>저장된 관측정보 없음 · 미확인</div>')
            + '</details></div>')
    return ''.join(cards) or '<div class="location">공통 서버에 저장된 병원 목록이 없습니다.</div>'


def _connect_form_html(hospitals, patient_id, consciousness, breathing, symptom, latitude, longitude,
                       submission_id, reporter_role, required_capabilities, special_team, consent, inquiry_policy, error):
    selected = set(required_capabilities or [])
    capabilities = sorted({str(cap) for hospital in hospitals for cap in hospital.get('capabilities', [])})
    # Keep values visible on an error even if the hospital catalogue became unavailable.
    capabilities = sorted(set(capabilities) | selected)
    labels = {'pediatrics': '소아 진료', 'obstetrics': '산과', 'cardiology': '심장 진료',
              'neurology': '신경 진료', 'trauma': '외상', 'surgery': '외과', 'emergency': '응급 진료'}
    options = ''.join('<label style="display:block;margin:9px 0;"><input type="checkbox" name="required_capabilities" value="'
                      + escape(cap, quote=True) + '"' + (' checked' if cap in selected else '')
                      + '> ' + escape(labels.get(cap, cap)) + '</label>' for cap in capabilities)
    roles = ''.join('<option value="' + value + '"' + (' selected' if reporter_role == value else '')
                    + '>' + label + '</option>' for value, label in (('', '입력자 선택'), ('self', '환자 본인'),
                                                                   ('guardian', '보호자'), ('transporter', '구급대·이송자')))
    policies = ''.join('<option value="' + value + '"' + (' selected' if inquiry_policy == value else '')
                       + '>' + label + '</option>' for value, label in (('fixed_1', '고정 1곳'), ('fixed_3', '고정 3곳 (기본)'),
                                                                      ('fixed_5', '고정 5곳'), ('observed_v2', '관측 기반 V2'), ('observed_v3', '관측 기반 V3')))
    error_html = '<div id="connect-error" role="alert" style="padding:14px;border:1px solid #d32f2f;border-radius:10px;color:#b71c1c;">' + escape(error) + '</div>' if error else ''
    return f"""<section style="margin-top:25px;">
        <div class="section-title">🔗 병원 탐색 요청</div>
        {error_html}
        <div class="location">위 화면은 저장정보 확인입니다. 아래 버튼을 눌러야 별도의 이송 건이 공통 서버에 생성되고 병원 문의가 시작됩니다.</div>
        <form id="common-connect-form" action="/emergency/connect" method="post">
            <input type="hidden" name="submission_id" value="{escape(submission_id, quote=True)}">
            <input type="hidden" name="patient_id" value="{escape(str(patient_id or 0), quote=True)}">
            <input type="hidden" name="consciousness" value="{escape(consciousness, quote=True)}">
            <input type="hidden" name="breathing" value="{escape(breathing, quote=True)}">
            <input type="hidden" name="symptom" value="{escape(symptom, quote=True)}">
            <div class="info"><label for="reporter-role">입력자</label><br>
                <select id="reporter-role" name="reporter_role" required style="width:100%;padding:12px;margin-top:8px;">{roles}</select></div>
            <div class="info"><strong>환자가 실제로 있는 위치</strong><p class="muted">현재 위치 확인 버튼으로 측정하거나, 실제 관측한 좌표를 입력하세요. 예시 좌표는 자동으로 채우지 않습니다.</p>
                <label for="connect-latitude">위도</label><input id="connect-latitude" name="latitude" inputmode="decimal" required value="{escape(str(latitude if latitude is not None else ''), quote=True)}" style="width:100%;padding:12px;margin:8px 0;">
                <label for="connect-longitude">경도</label><input id="connect-longitude" name="longitude" inputmode="decimal" required value="{escape(str(longitude if longitude is not None else ''), quote=True)}" style="width:100%;padding:12px;margin:8px 0;"></div>
            <div class="info"><strong>확인된 필요 진료 분야 (직접 선택)</strong><p class="muted">증상으로 진단하거나 필요한 분야를 자동 추정하지 않습니다. 확인되지 않은 분야는 선택하지 마세요.</p>
                {options or '<div class="muted">등록된 진료 분야 없음 · 미확인</div>'}</div>
            <details class="info"><summary>개발 시연 설정</summary>
                <label style="display:block;margin:12px 0;"><input name="special_team" type="checkbox" value="yes"{' checked' if special_team else ''}> 가상 전문팀 1개 필요 (합성 데이터 시험 전용)</label>
                <label for="inquiry-policy">문의 정책 (실험 설정)</label><select id="inquiry-policy" name="inquiry_policy" style="width:100%;padding:12px;margin-top:8px;">{policies}</select></details>
            <label style="display:flex;gap:10px;align-items:flex-start;padding:15px 0;"><input id="connect-consent" type="checkbox" name="consent" value="yes" required{' checked' if consent else ''}> 입력한 환자 상태·위치·연락 정보를 수용 확인에 필요한 병원과 공통 서버에 전달하는 것에 동의합니다.</label>
            <button id="connect-submit" class="button" type="submit" style="border:0;cursor:pointer;width:100%;">정보 전송 및 병원 탐색 시작</button>
        </form></section>"""


@app.get("/emergency/report", response_class=HTMLResponse)
def emergency_report(patient_id: int | None = None, consciousness: str = "", breathing: str = "", symptom: str = "",
                     latitude: float | None = None, longitude: float | None = None, accuracy: float | None = None):
    # This GET reads stored common-server data only. It cannot create an episode or inquiry.
    return _render_emergency_report(patient_id, consciousness, breathing, symptom, latitude, longitude, accuracy)


def _render_emergency_report(patient_id=None, consciousness="", breathing="", symptom="", latitude=None, longitude=None,
                             accuracy=None, *, submission_id=None, reporter_role="", required_capabilities=None,
                             special_team=False, consent=False, inquiry_policy="fixed_3", error="", status_code=200):
    patient = get_patient(patient_id) if patient_id not in (None, 0) else None
    values = [escape(str(item or "등록되지 않음")) for item in patient[1:7]] if patient else ["미등록 환자"] + ["등록되지 않음"] * 5
    patient_name, birth, phone, address, guardian, guardian_phone = values
    consciousness = consciousness if consciousness in ("yes", "no") else "unknown"
    breathing = breathing if breathing in ("yes", "no") else "unknown"
    consciousness_text, consciousness_class = _observed_state(consciousness, "있음", "없음")
    breathing_text, breathing_class = _observed_state(breathing, "정상", "비정상")
    symptom_text = escape(symptom or "확인되지 않음")
    hospitals = []
    try:
        hospitals = hospital_catalog()
    except CommonServerError as exc:
        catalog_error = str(exc)
    else:
        catalog_error = ""
    # Retain bad coordinate strings on validation errors without calculating a fake distance.
    try:
        lat = float(latitude) if latitude not in (None, "") else None
        lon = float(longitude) if longitude not in (None, "") else None
        if lat is None or lon is None or not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            lat = lon = None
    except (TypeError, ValueError):
        lat = lon = None
    hospital_cards = _stored_hospital_cards(hospitals, lat, lon)
    if catalog_error:
        hospital_cards = '<div class="location" role="status">저장된 병원 정보를 읽지 못했습니다. ' + escape(catalog_error) + '</div>'
    token = submission_id if submission_id is not None else new_submission_id()
    connect_form = _connect_form_html(hospitals, patient_id, consciousness, breathing, symptom, latitude, longitude,
                                      token, reporter_role, required_capabilities, special_team, consent, inquiry_policy, error)
    location_summary = ('<strong>위치 확인 완료</strong><br>위도: ' + escape(str(latitude)) + '<br>경도: '
                        + escape(str(longitude)) + ('<br>정확도: 약 ' + escape(str(round(accuracy, 1))) + 'm'
                        if isinstance(accuracy, (int, float)) and math.isfinite(accuracy) else '')) if lat is not None and lon is not None else "현재 위치를 확인하거나 아래 전송 폼에 실제 좌표를 입력해주세요."
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

                    background-color: #f5f5f5;

                    padding: 18px;

                    border-radius: 10px;

                    color: #555;

                    line-height: 1.6;

                }}



                .location-button {{

                    display: block;

                    width: 100%;

                    padding: 15px;

                    margin-top: 10px;

                    background-color: #1976d2;

                    color: white;

                    border: none;

                    border-radius: 10px;

                    font-size: 16px;

                    font-weight: bold;

                    cursor: pointer;

                }}



                .location-button:disabled {{

                    background-color: #999;

                    cursor: not-allowed;

                }}



                .hospital-card {{

                    border: 1px solid #e0e0e0;

                    border-radius: 12px;

                    padding: 16px;

                    margin-bottom: 12px;

                    background: #fff;

                }}



                .hospital-name {{

                    font-size: 18px;

                    font-weight: bold;

                    margin-bottom: 8px;

                    color: #1f2937;

                }}



                .hospital-tag {{

                    display: inline-block;

                    background: #e3f2fd;

                    color: #1565c0;

                    padding: 4px 8px;

                    border-radius: 6px;

                    font-size: 12px;

                    margin-bottom: 10px;

                }}



                .hospital-detail {{

                    font-size: 14px;

                    line-height: 1.6;

                    margin-top: 5px;

                    color: #333;

                    word-break: keep-all;

                }}



                .hospital-address {{

                    margin-top: 8px;

                    color: #666;

                    font-size: 12px;

                    line-height: 1.5;

                }}



                .muted {{

                    color: #777;

                    font-size: 12px;

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

                    📍 현재 위치

                </div>



                <div class="location" id="location-box">

                    {location_summary}

                </div>



                <button

                    type="button"

                    class="location-button"

                    id="location-button"

                    onclick="getCurrentLocation()"

                >

                    📍 현재 위치 확인

                </button>



                {connect_form}

                <details style="margin-top:24px;"><summary style="font-weight:bold;cursor:pointer;">저장된 주변 병원 정보 보기</summary>
                <div class="section-title">

                    🏥 주변 응급실

                </div>



                <div class="location" style="margin-bottom: 12px;">

                    공통 서버에 저장된 병원 정보를 확인합니다. 위치가 있으면 직선거리순 최대 5곳을 표시합니다.<br>

                    관측 출처와 갱신 시각을 함께 표시하며, 없는 정보는 미확인으로 유지합니다. 조회만으로 수용 여부를 판단하지 않습니다.

                </div>



                {hospital_cards}
                </details>



                <script>



                    function getCurrentLocation() {{



                        const locationBox = document.getElementById("location-box");

                        const locationButton = document.getElementById("location-button");



                        if (!navigator.geolocation) {{



                            locationBox.innerHTML =

                                "이 브라우저에서는 위치 정보를 사용할 수 없습니다.";



                            return;

                        }}



                        locationButton.disabled = true;

                        locationButton.textContent = "📍 현재 위치 확인 중...";



                        locationBox.innerHTML =

                            "현재 위치를 확인하고 있습니다.<br>잠시만 기다려주세요.";



                        navigator.geolocation.getCurrentPosition(



                            function(position) {{



                                const latitude = position.coords.latitude;

                                const longitude = position.coords.longitude;

                                const accuracy = position.coords.accuracy;



                                document.getElementById("connect-latitude").value = latitude;
                                document.getElementById("connect-longitude").value = longitude;
                                locationBox.textContent = "위치 확인 완료: 위도 " + latitude + ", 경도 " + longitude + " (정확도 약 " + Math.round(accuracy) + "m). 아래 폼에서 확인 후 전송하세요.";
                                locationButton.disabled = false;
                                locationButton.textContent = "📍 위치 다시 확인";

                            }},



                            function(error) {{



                                let message = "현재 위치를 확인할 수 없습니다.";



                                if (error.code === 1) {{

                                    message = "위치 정보 사용 권한이 거부되었습니다. 브라우저의 위치 권한을 허용해주세요.";

                                }} else if (error.code === 2) {{

                                    message = "현재 위치를 확인할 수 없습니다.";

                                }} else if (error.code === 3) {{

                                    message = "위치 확인 시간이 초과되었습니다. 다시 시도해주세요.";

                                }}



                                locationBox.innerHTML = message;

                                locationButton.disabled = false;

                                locationButton.textContent = "📍 다시 위치 확인";

                            }},



                            {{

                                enableHighAccuracy: true,

                                timeout: 10000,

                                maximumAge: 0

                            }}

                        );

                    }}



                </script>



                <a

                    href="/"

                    class="button"

                >

                    🏠 처음 화면으로

                </a>



                <a

                    href="/emergency"

                    class="home"

                >

                    응급상황 다시 시작

                </a>



            </div>



        </body>



        </html>

    """, status_code=status_code)





@app.post("/emergency/connect", response_class=HTMLResponse)
def emergency_connect(submission_id: str = Form(""), patient_id: str = Form("0"),
                      consciousness: str = Form("unknown"), breathing: str = Form("unknown"),
                      symptom: str = Form(""), latitude: str = Form(""), longitude: str = Form(""),
                      reporter_role: str = Form(""), consent: str = Form(""),
                      required_capabilities: list[str] = Form(default=[]), special_team: str = Form(""),
                      inquiry_policy: str = Form("fixed_3")):
    local_id = None
    try:
        try:
            local_id = int(patient_id or "0")
        except (TypeError, ValueError) as exc:
            raise CommonServerError("선택한 로컬 환자 정보가 올바르지 않습니다.", 400) from exc
        profile = get_patient(local_id) if local_id else None
        if local_id and profile is None:
            raise CommonServerError("선택한 환자 프로필을 찾을 수 없습니다. 환자를 다시 선택해주세요.", 400)
        try:
            lat, lon = float(latitude), float(longitude)
        except (TypeError, ValueError) as exc:
            raise CommonServerError("환자의 실제 위도와 경도를 입력해주세요.", 400) from exc
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0, 0):
            raise CommonServerError("환자의 실제 위치 범위에 맞는 좌표를 입력해주세요.", 400)
        catalog = hospital_catalog()
        known_caps = {str(cap) for hospital in catalog for cap in hospital.get('capabilities', [])}
        if not set(required_capabilities) <= known_caps:
            raise CommonServerError("선택한 진료 분야가 현재 저장된 병원 목록에 없습니다. 정보를 확인해주세요.", 400)
        payload = build_episode_payload(profile=profile, reporter_role=reporter_role, consciousness=consciousness,
                                        breathing=breathing, symptoms=symptom, required_capabilities=required_capabilities,
                                        special_team=special_team == "yes", lat=lat, lon=lon, consent=consent == "yes",
                                        inquiry_policy=inquiry_policy)
        patient = create_episode(payload, submission_id)
    except CommonServerError as exc:
        return _render_emergency_report(local_id, consciousness, breathing, symptom, latitude, longitude,
                                         submission_id=submission_id, reporter_role=reporter_role,
                                         required_capabilities=required_capabilities, special_team=special_team == "yes",
                                         consent=consent == "yes", inquiry_policy=inquiry_policy,
                                         error=str(exc), status_code=exc.status)
    return RedirectResponse(transport_url(patient['id']), status_code=303)


# 📋 응급환자 최종 정보

# ==================================================



@app.get("/emergency/result", response_class=HTMLResponse)

def emergency_result(

    patient_id: int | None = None,

    consciousness: str = "",

    breathing: str = "",

    symptom: list[str] = Query(default=[])

):



    allowed_symptoms = {"가슴 통증", "호흡 곤란", "출혈", "의식 저하", "기타"}

    selected = list(dict.fromkeys(x for x in symptom if x in allowed_symptoms))

    symptom = ", ".join(selected) if selected else "확인되지 않음"



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



        patient_name = escape(str(patient[1]))



        birth = escape(str(patient[2] or "등록되지 않음"))

        phone = escape(str(patient[3] or "등록되지 않음"))

        address = escape(str(patient[4] or "등록되지 않음"))

        guardian = escape(str(patient[5] or "등록되지 않음"))

        guardian_phone = escape(str(patient[6] or "등록되지 않음"))





    consciousness = consciousness if consciousness in ("yes", "no") else "unknown"
    breathing = breathing if breathing in ("yes", "no") else "unknown"
    consciousness_text, consciousness_class = _observed_state(consciousness, "있음", "없음")
    breathing_text, breathing_class = _observed_state(breathing, "정상", "비정상")

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
