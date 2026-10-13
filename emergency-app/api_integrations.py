"""캡스톤 응급의료 API 연동 모듈. 키가 없어도 앱은 실행됩니다."""
import os
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed


def _request(url, params=None, headers=None, method='GET', body=None):
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=8) as response:
        raw = response.read()
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        root = ET.fromstring(raw)
        code = root.findtext('.//resultCode')
        if code and code not in ('00', '0'):
            raise ValueError('API 응답 오류: ' + str(code))
        return [{e.tag: (e.text or '').strip() for e in item} for item in root.findall('.//item')]


def _key(name):
    val = os.getenv(name, '').strip()
    return val if val and not val.startswith('발급') else ''


def _items(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    body = data.get('response', {}).get('body', {}) if isinstance(data.get('response'), dict) else data.get('body', {})
    items = body.get('items', {}) if isinstance(body, dict) else {}
    if isinstance(items, dict):
        return items.get('item', []) if isinstance(items.get('item', []), list) else [items['item']] if items.get('item') else []
    return items if isinstance(items, list) else []


def _hira_request(url_env, **params):
    key = _key('HIRA_API_KEY')
    endpoint = os.getenv(url_env, '').strip()
    if not key or not endpoint:
        return [], '심평원 키 또는 상세기능 URL 설정 대기'
    try:
        data = _request(endpoint, {'ServiceKey': key, 'numOfRows': 100, 'pageNo': 1, **params})
        return _items(data), None
    except Exception as e:
        return [], f'심평원 조회 실패: {type(e).__name__}'


def hira_hospital_info(name):
    """공공데이터포털에서 복사한 실제 상세기능 URL을 .env에 지정해야 작동."""
    if not _key('HIRA_API_KEY'):
        return {'status': 'API 키 발급 대기'}
    rows, err = _hira_request('HIRA_HOSPITAL_URL', yadmNm=name)
    if err:
        return {'status': err}
    matches = [r for r in rows if r.get('yadmNm', '').replace(' ', '') == name.replace(' ', '')]
    if len(matches) != 1:
        return {'status': '병원 식별자 매칭 확인 필요'}
    return {'status': '조회됨', 'ykiho': matches[0].get('ykiho', ''), 'details': matches[0]}


def hira_details(ykiho):
    """진료과·인력·시설·장비 각각의 상세기능 URL과 응답 형식 확인 후 연결."""
    if not ykiho:
        return {'status': '병원 식별자 미확인'}
    result = {}
    for label, env in [('진료과·인력', 'HIRA_DEPARTMENT_URL'), ('시설·장비', 'HIRA_EQUIPMENT_URL')]:
        rows, err = _hira_request(env, ykiho=ykiho)
        result[label] = {'status': err or '조회됨', 'items': rows[:20]}
    return result


def kakao_route(origin_lat, origin_lon, dest_lat, dest_lon):
    key = _key('KAKAO_REST_API_KEY')
    if not key:
        return {'status': '카카오 API 키 발급 대기'}
    try:
        data = _request('https://apis-navi.kakaomobility.com/v1/directions', {
            'origin': f'{origin_lon},{origin_lat}',
            'destination': f'{dest_lon},{dest_lat}',
            'priority': 'RECOMMEND', 'summary': 'true'
        }, {'Authorization': 'KakaoAK ' + key})
        routes = data.get('routes', [])
        summary = routes[0].get('summary', {}) if routes else {}
        if not summary or summary.get('distance') is None:
            return {'status': '경로 탐색 결과 없음'}
        return {'status': '조회됨', 'distance_km': round(summary['distance'] / 1000, 1),
                'duration_min': max(1, round(summary['duration'] / 60))}
    except Exception as e:
        return {'status': '카카오 길찾기 조회 실패: ' + type(e).__name__}


def enrich_hospitals(hospitals, lat, lon):
    """기존 병원 검색 결과를 유지하며 도로거리·시간 및 HIRA 상태를 덧붙임."""
    for hospital in hospitals:
        hospital['road'] = {'status': '연동 대기'}
        hospital['hira'] = {'status': '연동 대기'}
    if _key('KAKAO_REST_API_KEY'):
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(kakao_route, lat, lon, h['lat'], h['lon']): h for h in hospitals}
            for future in as_completed(futures):
                futures[future]['road'] = future.result()
    # HIRA 병원명만으로 자동 병합하면 동명이원 오매칭 위험이 있으므로 별도 검증 후 연결.
    return hospitals


# ===== HIRA 진료과목 API 2.8 (실제 사용자 테스트 성공) =====
import re
import time
import threading

_HIRA_CACHE = {}
_HIRA_CACHE_LOCK = threading.Lock()
_HIRA_CACHE_SECONDS = 1800
_HIRA_HOSP_URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
_HIRA_DEPT_URL = "https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getDgsbjtInfo2.8"
_HIRA_FACILITY_URL = "https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getEqpInfo2.8"
_HIRA_EQUIPMENT_URL = "https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getMedOftInfo2.8"


def _hira_xml(url, params):
    key = _key("HIRA_API_KEY")
    if not key:
        raise ValueError("심평원 API 키 미설정")
    query = urllib.parse.urlencode({"serviceKey": key, "pageNo": 1, "numOfRows": 100, "_type": "xml", **params})
    request = urllib.request.Request(url + "?" + query, headers={"User-Agent": "EmergencyPatientSupportCapstone/1.0"})
    with urllib.request.urlopen(request, timeout=7) as response:
        root = ET.fromstring(response.read())
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("00", "0"):
        raise ValueError("심평원 응답 코드 " + (code or "없음"))
    return [{child.tag: (child.text or "").strip() for child in item} for item in root.findall(".//items/item")]


def _normalize_hospital_name(value):
    # 법인명/지점명이 달라질 수 있으므로 임의로 삭제하지 않습니다.
    return re.sub(r"[^0-9a-z가-힣]", "", (value or "").lower())


def _address_compatible(a, b):
    # 행정구역 + 도로명(가능하면 건물번호)까지 비교; 불충분하면 매칭 보류
    def tokens(address):
        address = re.sub(r"\([^)]*\)", "", address or "")
        address = address.replace(",", " ")
        return [re.sub(r"[^0-9a-z가-힣]", "", x.lower()) for x in address.split() if x.strip()]
    aa, bb = tokens(a), tokens(b)
    if len(aa) < 4 or len(bb) < 4:
        return False
    # 시/도 표기 차이 허용, 시군구와 도로명+건물번호 일치 필수
    district_a = next((t for t in aa if t.endswith(("구", "군", "시")) and t not in ("성남시",)), None)
    district_b = next((t for t in bb if t.endswith(("구", "군", "시")) and t not in ("성남시",)), None)
    if district_a and district_b and district_a != district_b:
        return False
    road_a = next(((aa[i], aa[i+1]) for i in range(len(aa)-1) if aa[i].endswith(("로", "길")) and aa[i+1].isdigit()), None)
    road_b = next(((bb[i], bb[i+1]) for i in range(len(bb)-1) if bb[i].endswith(("로", "길")) and bb[i+1].isdigit()), None)
    return road_a is not None and road_a == road_b


def _hira_search_terms(name):
    """Search full name first, then conservative shorter variants."""
    clean = _normalize_hospital_name(name)
    terms = [name]
    # Legal entity prefixes are frequently omitted or spaced differently in HIRA.
    for marker in ("의료재단", "의료법인", "재단법인"):
        if marker in clean:
            suffix = clean.split(marker, 1)[-1]
            if len(suffix) >= 4:
                terms.append(suffix)
    if "성남정병원" in clean:
        terms.append("성남정병원")
    # Avoid querying broad one/two-syllable fragments.
    return list(dict.fromkeys(t for t in terms if len(_normalize_hospital_name(t)) >= 4))


def _name_compatible(a, b):
    aa, bb = _normalize_hospital_name(a), _normalize_hospital_name(b)
    if aa == bb:
        return True
    # Only permit meaningful suffix equivalence when both names identify a hospital.
    shorter, longer = sorted((aa, bb), key=len)
    return len(shorter) >= 5 and shorter.endswith("병원") and longer.endswith(shorter)


def _hira_department_for_hospital(hospital):
    name, address = hospital.get("name", ""), hospital.get("address", "")
    if not name or not address or address == "주소 미확인":
        return {"status": "병원명 또는 주소 미확인"}
    cache_key = (_normalize_hospital_name(name), address)
    with _HIRA_CACHE_LOCK:
        cached = _HIRA_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _HIRA_CACHE_SECONDS:
            return cached[1]
    try:
        candidates = {}
        for term in _hira_search_terms(name):
            rows = _hira_xml(_HIRA_HOSP_URL, {"yadmNm": term})
            for row in rows:
                if not row.get("ykiho"):
                    continue
                if not _name_compatible(name, row.get("yadmNm", "")):
                    continue
                if not _address_compatible(address, row.get("addr", "")):
                    continue
                candidates[row["ykiho"]] = row
            # An unambiguous match with verified address needs no broader query.
            if len(candidates) == 1:
                break
        if len(candidates) != 1:
            result = {"status": "병원 식별자 매칭 확인 필요"}
        else:
            ykiho = next(iter(candidates))
            # 진료과, 시설, 장비는 독립적으로 조회: 하나가 실패해도 나머지는 표시합니다.
            result = {"status": "조회됨", "departments": [],
                      "facility": {"status": "미확인", "items": {}},
                      "equipment": {"status": "미확인", "items": []}}
            try:
                details = _hira_xml(_HIRA_DEPT_URL, {"ykiho": ykiho})
                for d in details:
                    subject = d.get("dgsbjtCdNm", "").strip()
                    if not subject:
                        continue
                    try:
                        specialists = int(d.get("dgsbjtPrSdrCnt", ""))
                    except (ValueError, TypeError):
                        specialists = None
                    result["departments"].append({"name": subject, "specialists": specialists})
                if not result["departments"]:
                    result["department_status"] = "진료과목 응답 없음"
            except Exception as exc:
                result["department_status"] = "진료과목 조회 실패 (" + type(exc).__name__ + ")"
            try:
                facilities = _hira_xml(_HIRA_FACILITY_URL, {"ykiho": ykiho})
                if facilities:
                    # 확인된 공식 응답 필드만 사용합니다. 보유량은 실시간 가용량이 아닙니다.
                    field_names = {
                        "permSbdCnt": "허가 병상", "stdSickbdCnt": "일반 입원병상",
                        "soprmCnt": "수술실", "partumCnt": "분만실",
                        "ptrmCnt": "물리치료실",
                    }
                    parsed = {}
                    for field, label in field_names.items():
                        raw = facilities[0].get(field, "").strip()
                        if raw.isdigit():
                            parsed[label] = int(raw)
                    result["facility"] = {"status": "조회됨" if parsed else "표시 가능한 시설 항목 없음", "items": parsed}
                else:
                    result["facility"] = {"status": "시설정보 응답 없음", "items": {}}
            except Exception as exc:
                result["facility"] = {"status": "시설 조회 실패 (" + type(exc).__name__ + ")", "items": {}}
            try:
                equipment = _hira_xml(_HIRA_EQUIPMENT_URL, {"ykiho": ykiho})
                parsed = []
                for item in equipment:
                    label = item.get("oftCdNm", "").strip()
                    raw = item.get("oftCnt", "").strip()
                    if label and raw.isdigit():
                        parsed.append({"name": label, "count": int(raw)})
                result["equipment"] = {"status": "조회됨" if parsed else "장비정보 응답 없음", "items": parsed}
            except Exception as exc:
                result["equipment"] = {"status": "장비 조회 실패 (" + type(exc).__name__ + ")", "items": []}
    except Exception as e:
        result = {"status": "심평원 조회 실패 (" + type(e).__name__ + ")"}
    with _HIRA_CACHE_LOCK:
        _HIRA_CACHE[cache_key] = (time.monotonic(), result)
    return result


def enrich_hospitals_with_hira(hospitals):
    if not _key("HIRA_API_KEY"):
        for hospital in hospitals:
            hospital["hira"] = {"status": "HIRA_API_KEY 미설정"}
        return hospitals
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_hira_department_for_hospital, hospital): hospital for hospital in hospitals}
        for future in as_completed(futures):
            hospital = futures[future]
            try:
                hospital["hira"] = future.result()
            except Exception:
                hospital["hira"] = {"status": "심평원 조회 실패"}
    return hospitals


# V6: 국립중앙의료원 중증질환 수용정보 (병원 식별자 우선 매칭)
def enrich_hospitals_with_severe(hospitals, stage1, stage2):
    """행정구역의 중증질환 정보를 조회해 HPID가 일치하는 병원에만 붙입니다.

    API가 HPID를 제공하지 않는 경우, 병원명이 정확히 일치하고 유일한 경우에만
    보조적으로 매칭합니다. 결과는 병원 수용 확약이 아닙니다.
    """
    for hospital in hospitals:
        hospital['severe'] = {'status': '조회되지 않음', 'items': []}
    key = _key('EMERGENCY_API_KEY')
    if not key or not stage1 or not stage2 or not hospitals:
        return hospitals
    endpoint = ('https://apis.data.go.kr/B552657/ErmctInfoInqireService/'
                'getSrsillDissAceptncPosblInfoInqire')
    rows = []
    try:
        for page in range(1, 6):
            chunk = _request(endpoint, {
                'serviceKey': key, 'STAGE1': stage1, 'STAGE2': stage2,
                'pageNo': page, 'numOfRows': 100
            })
            if not isinstance(chunk, list):
                break
            rows.extend(chunk)
            if len(chunk) < 100:
                break
    except Exception:
        for hospital in hospitals:
            hospital['severe']['status'] = '중증질환 API 조회 실패'
        return hospitals

    def norm(value):
        return ''.join(str(value or '').split()).lower()

    for hospital in hospitals:
        hid = norm(hospital.get('hpid'))
        matches = [r for r in rows if hid and norm(r.get('hpid') or r.get('HPID')) == hid]
        if not matches and not any(norm(r.get('hpid') or r.get('HPID')) for r in rows):
            name = norm(hospital.get('name'))
            matches = [r for r in rows if name and norm(r.get('dutyName')) == name]
        if len(matches) != 1:
            hospital['severe']['status'] = '일치 병원 없음' if not matches else '병원 매칭 중복'
            continue
        row = matches[0]
        values = []
        for i in range(1, 29):
            code = f'MKioskTy{i}'
            value = (row.get(code) or '').strip()
            msg = (row.get(code + 'Msg') or '').strip()
            if value or msg:
                values.append({'code': code, 'value': value or '정보미제공', 'message': msg})
        hospital['severe'] = {'status': '조회됨' if values else '제공 항목 없음', 'items': values}
    return hospitals
