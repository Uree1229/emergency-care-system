"""심평원 신고 현황 수집. 현재 당직·가용수·환자별 수용 확답과 구분한다."""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db
from .public_data import PublicDataError, parse_response, text, _save_snapshot

BASIS_URL = 'https://apis.data.go.kr/B551182/hospInfoServicev2/'
DETAIL_URL = 'https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/'
OPERATIONS = {
    'hira_hospital_basis': (BASIS_URL, 'getHospBasisList', 'ServiceKey'),
    'hira_departments': (DETAIL_URL, 'getDgsbjtInfo2.8', 'serviceKey'),
    'hira_specialists': (DETAIL_URL, 'getSpcSbjtSdrInfo2.8', 'serviceKey'),
    'hira_equipment': (DETAIL_URL, 'getMedOftInfo2.8', 'serviceKey'),
    'hira_facilities': (DETAIL_URL, 'getEqpInfo2.8', 'serviceKey'),
    'hira_special_care': (DETAIL_URL, 'getSpclDiagInfo2.8', 'serviceKey'),
    'hira_operating_details': (DETAIL_URL, 'getDtlInfo2.8', 'serviceKey'),
    'hira_designated_specialties': (DETAIL_URL, 'getSpclHospAsgFldList2.8', 'serviceKey'),
    'hira_nursing_grades': (DETAIL_URL, 'getNursigGrdInfo2.8', 'serviceKey'),
    'hira_other_personnel': (DETAIL_URL, 'getEtcHstInfo2.8', 'serviceKey'),
    'hira_transport': (DETAIL_URL, 'getTrnsprtInfo2.8', 'serviceKey'),
    'hira_meal_additions': (DETAIL_URL, 'getFoepAddcInfo2.8', 'serviceKey'),
}
DETAIL_ENDPOINTS = tuple(name for name in OPERATIONS if name != 'hira_hospital_basis')
FACILITY_FIELDS = {
    'emymCnt': 'ed_bed', 'soprmCnt': 'operating_room', 'partumCnt': 'delivery_room',
    'nbySprmCnt': 'nicu_bed', 'aduChldSprmCnt': 'adult_icu_bed',
    'chldSprmCnt': 'pediatric_icu_bed', 'permSbdCnt': 'permitted_bed',
}
CONSULTATION_DAYS = (
    ('Mon', 'monday'), ('Tue', 'tuesday'), ('Wed', 'wednesday'),
    ('Thu', 'thursday'), ('Fri', 'friday'), ('Sat', 'saturday'), ('Sun', 'sunday'),
)


class HiraDataError(PublicDataError):
    def __init__(self, message, access_denied=False):
        super().__init__(message)
        self.access_denied = access_denied


class HiraClient:
    def __init__(self, key=None, page_size=100, timeout=15, retries=2, max_pages=100,
                 opener=urllib.request.urlopen, sleep=time.sleep):
        key = key or os.environ.get('DATA_GO_KR_KEY', '')
        if not isinstance(key, str) or not key.strip():
            raise HiraDataError('DATA_GO_KR_KEY 환경변수를 설정하세요')
        if not 1 <= page_size <= 1000 or not 1 <= max_pages <= 1000 or timeout <= 0 or not 0 <= retries <= 5:
            raise HiraDataError('API 페이지 크기·시간 제한·재시도 설정을 확인하세요')
        self.key = urllib.parse.unquote(key.strip())
        self.page_size, self.timeout, self.retries, self.max_pages = page_size, timeout, retries, max_pages
        self.opener, self.sleep = opener, sleep

    def _page(self, endpoint, params, number):
        base, operation, key_name = OPERATIONS[endpoint]
        query = {key_name:self.key, 'pageNo':number, 'numOfRows':self.page_size, **params}
        req = urllib.request.Request(base + operation + '?' + urllib.parse.urlencode(query))
        for attempt in range(self.retries + 1):
            try:
                with self.opener(req, timeout=self.timeout) as response:
                    body = response.read().decode('utf-8-sig')
                for secret in {self.key, urllib.parse.quote(self.key, safe=''), urllib.parse.quote_plus(self.key)}:
                    body = body.replace(secret, '***')
                try:
                    return parse_response(body)
                except PublicDataError as exc:
                    code = str(exc).rsplit(': ', 1)[-1]
                    raise HiraDataError(str(exc), access_denied=code in ('20','30','31')) from None
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                message, denied = f'심평원 API HTTP 오류: {exc.code}', exc.code in (401,403)
            except (urllib.error.URLError, TimeoutError, OSError, UnicodeError):
                retryable, message, denied = True, '심평원 API 연결 또는 응답 읽기에 실패했습니다', False
            if not retryable or attempt == self.retries:
                raise HiraDataError(message, access_denied=denied) from None
            self.sleep(min(2 ** attempt, 8))

    def fetch_all(self, endpoint, **params):
        if endpoint not in OPERATIONS:
            raise HiraDataError('심평원 수집 대상을 확인하세요')
        allowed = {'yadmNm', 'sidoCd', 'sgguCd', 'xPos', 'yPos', 'radius'} if endpoint == 'hira_hospital_basis' else {'ykiho'}
        if not params or set(params) - allowed or any(not isinstance(v, str) or not v.strip() for v in params.values()):
            raise HiraDataError('심평원 조회 항목을 확인하세요')
        if endpoint != 'hira_hospital_basis' and not params.get('ykiho'):
            raise HiraDataError('상세 조회에 심평원 기관 ID가 필요합니다')
        records, total = [], None
        for number in range(1, self.max_pages + 1):
            page = self._page(endpoint, params, number)
            if total is not None and total != page['total_count']:
                raise HiraDataError('수집 중 심평원 전체 건수가 변경됐습니다')
            total = page['total_count']
            if page['page_no'] not in (None, number):
                raise HiraDataError('심평원 응답 페이지가 요청과 다릅니다')
            records.extend(page['items'])
            if len(records) == total:
                return records
            if not page['items'] or len(records) > total:
                raise HiraDataError('심평원 페이지 일부가 누락되거나 중복됐습니다')
        raise HiraDataError('심평원 최대 페이지 수를 초과했습니다. 부분 자료는 적재하지 않습니다')


def _count(record, field):
    value = text(record, field)
    return int(value) if value is not None and re.fullmatch(r'[0-9]+', value) else None


def _registered_yn(record, field):
    """명세에 값 코드표가 없는 숫자 코드를 Y/N으로 추정하지 않는다."""
    raw = text(record, field)
    value = raw.upper() if raw is not None else None
    known = value in ('Y', 'N')
    return {'source_field':field, 'raw_value':raw,
            'reported_operating':value == 'Y' if known else None,
            'status':'reported' if known else 'unknown'}


def _operating_details(record):
    # 모두 신고된 운영 안내다. 외래 시간으로 응급실·전문의 현재 가용 여부를
    # 판정하거나, 단위가 명시되지 않은 안내 거리를 도로 거리로 바꾸지 않는다.
    return {
        'emergency_department':{
            'day':_registered_yn(record, 'emyDayYn'),
            'night':_registered_yn(record, 'emyNgtYn'),
        },
        'emergency_contacts':{
            'day':[v for f in ('emyDayTelNo1','emyDayTelNo2') if (v := text(record,f))],
            'night':[v for f in ('emyNgtTelNo1','emyNgtTelNo2') if (v := text(record,f))],
        },
        'consultation_hours':[
            {'day':day, 'start_field':f'trmt{code}Start', 'end_field':f'trmt{code}End',
             'start_raw':text(record,f'trmt{code}Start'), 'end_raw':text(record,f'trmt{code}End')}
            for code,day in CONSULTATION_DAYS
        ],
        'reception_hours':{'week_raw':text(record,'rcvWeek'), 'saturday_raw':text(record,'rcvSat')},
        'closures':{'sunday_raw':text(record,'noTrmtSun'), 'holiday_raw':text(record,'noTrmtHoli')},
        'lunch_hours':{'week_raw':text(record,'lunchWeek'), 'saturday_raw':text(record,'lunchSat')},
        'location':{'landmark_raw':text(record,'plcNm'), 'direction_raw':text(record,'plcDir'),
                    'distance_raw':text(record,'plcDist')},
        'parking':{'reported_count':_count(record,'parkQty'), 'operation_cost_raw':text(record,'parkXpnsYn'),
                   'note':text(record,'parkEtc')},
    }


def normalize_records(endpoint, records):
    if endpoint not in OPERATIONS or not isinstance(records, list) or any(not isinstance(r,dict) for r in records):
        raise HiraDataError('심평원 정규화 대상을 확인하세요')
    result = {'source':'public', 'provider':'hira', 'data_kind':'registered',
              'source_observed_at':None, 'record_count':len(records),
              'empty_result_not_absence':True,
              'note':'심평원 신고 현황이며 현재 당직·가용수·환자별 수용 확답이 아닙니다'}
    if endpoint == 'hira_hospital_basis':
        if len(records) != 1:
            raise HiraDataError('매칭한 심평원 기본정보는 한 기관이어야 합니다')
        r = records[0]
        result['institution'] = {key:text(r, field) for key,field in
                                 (('ykiho','ykiho'),('name','yadmNm'),('address','addr'),('phone','telno'))}
        result['reported_total_doctors'] = _count(r, 'drTotCnt')
        result['reported_medical_specialists'] = _count(r, 'mdeptSdrCnt')
    elif endpoint in ('hira_departments','hira_specialists'):
        count_field = 'dgsbjtPrSdrCnt' if endpoint == 'hira_departments' else 'dtlSdrCnt'
        field = 'departments' if endpoint == 'hira_departments' else 'specialists'
        result[field] = [{'code':text(r,'dgsbjtCd'), 'name':text(r,'dgsbjtCdNm'),
                          'source_field':count_field, 'reported_count':_count(r,count_field),
                          'count_kind':'doctors' if endpoint == 'hira_departments' else 'specialists'} for r in records]
        result['department_names'] = list(dict.fromkeys(r['name'] for r in result[field] if r['name']))
    elif endpoint == 'hira_equipment':
        result['registered_equipment'] = [{'code':text(r,'oftCd'), 'name':text(r,'oftCdNm'),
                                           'reported_count':_count(r,'oftCnt'), 'source_field':'oftCnt'} for r in records]
    elif endpoint == 'hira_facilities':
        result['facilities'] = [{'facility_type':kind, 'source_field':field,
                                  'reported_count':_count(r,field),
                                  'status':'reported' if _count(r,field) is not None else 'unknown'}
                                 for r in records for field,kind in FACILITY_FIELDS.items()]
    elif endpoint == 'hira_special_care':
        result['special_care'] = [{'code':text(r,'srchCd'), 'name':text(r,'srchCdNm'),
                                   'status':'registered_uninterpreted'} for r in records]
    elif endpoint == 'hira_operating_details':
        result['operating_details'] = [_operating_details(r) for r in records]
    elif endpoint == 'hira_designated_specialties':
        result['designated_specialties'] = [
            {'code':text(r,'srchCd'), 'name':text(r,'srchCdNm'),
             'status':'registered_uninterpreted'} for r in records]
    elif endpoint == 'hira_nursing_grades':
        result['nursing_grades'] = [
            {'code':text(r,'tyCd'), 'name':text(r,'tyCdNm'), 'reported_grade':text(r,'careGrd')}
            for r in records]
    elif endpoint == 'hira_other_personnel':
        result['other_personnel'] = [
            {'code':text(r,'gnlNopDtlCd'), 'name':text(r,'dtlGnlNopCdNm'),
             'reported_count':_count(r,'gnlNopCnt'), 'source_field':'gnlNopCnt',
             'count_kind':'registered_other_personnel'} for r in records]
    elif endpoint == 'hira_transport':
        result['transport_guidance'] = [
            {'mode':text(r,'trafNm'), 'line_number':text(r,'lineNo'),
             'arrival_place':text(r,'arivPlc'), 'direction':text(r,'dir'),
             'distance_raw':text(r,'dist'), 'note':text(r,'rmk')} for r in records]
    elif endpoint == 'hira_meal_additions':
        result['meal_additions'] = [
            {'code':text(r,'tyCd'), 'name':text(r,'tyCdNm'),
             'reported_grade':text(r,'trmealGrd'), 'reported_personnel_count':_count(r,'calcNopCnt'),
             'addition_raw':text(r,'gnmAddcYn')} for r in records]
    return result


def apply_bundles(conn, now, bundles, *, reviewed_links=None):
    """검증된 기관쌍의 전체 상세 응답만 원자적으로 반영. 운영값은 변경하지 않는다."""
    seen_hospitals, seen_ids = set(), set()
    with db.transaction(conn):
        for bundle in bundles:
            if not isinstance(bundle,dict):
                raise HiraDataError('심평원 적재 항목을 확인하세요')
            hid, ykiho = bundle.get('hospital_id'), bundle.get('ykiho')
            if any(not isinstance(v,str) or not v.strip() or len(v)>512 for v in (hid,ykiho)):
                raise HiraDataError('심평원 기관 식별자를 확인하세요')
            if hid in seen_hospitals or ykiho in seen_ids:
                raise HiraDataError('심평원 기관 매칭이 중복됐습니다')
            seen_hospitals.add(hid)
            seen_ids.add(ykiho)
            hospital = db.row(conn.execute('SELECT * FROM hospitals WHERE id=?', (hid,)).fetchone())
            if not hospital or hospital['source'] != 'public':
                raise HiraDataError('심평원 연계할 공공 병원을 찾을 수 없습니다')
            source_ids = hospital['source_ids']
            if source_ids.get('hira') not in (None,ykiho):
                raise HiraDataError('기존 심평원 기관 연결과 충돌합니다')
            for other in db.rows(conn.execute('SELECT source_ids FROM hospitals WHERE id<>?', (hid,))):
                if other['source_ids'].get('hira') == ykiho:
                    raise HiraDataError('심평원 기관 ID가 다른 병원에 연결돼 있습니다')
            records = bundle.get('records')
            if not isinstance(records,dict) or set(records) != set(OPERATIONS):
                raise HiraDataError('심평원 상세 응답 일부가 누락됐습니다')
            if any(not isinstance(values,list) or any(not isinstance(r,dict) for r in values)
                   for values in records.values()):
                raise HiraDataError('심평원 상세 응답 형식이 올바르지 않습니다')
            if len(records['hira_hospital_basis']) != 1 or text(records['hira_hospital_basis'][0],'ykiho') != ykiho:
                raise HiraDataError('심평원 기본정보 기관 ID가 요청과 다릅니다')
            from .hira_matching import match_hospitals
            check = match_hospitals([hospital],records['hira_hospital_basis'],reviewed_links=reviewed_links)
            if len(check['matches']) != 1:
                raise HiraDataError('심평원 기관 연결 근거가 현재 병원 정보와 일치하지 않습니다')
            for endpoint, values in records.items():
                for record in values:
                    if text(record,'ykiho') not in (None,ykiho):
                        raise HiraDataError('심평원 상세정보 기관 ID가 요청과 다릅니다')
                normalized = normalize_records(endpoint, values)
                if endpoint == 'hira_hospital_basis':
                    normalized['match_evidence'] = check['matches'][0]['evidence']
                _save_snapshot(conn,hid,endpoint,now,None,normalized,{'items':values})
            if source_ids.get('hira') != ykiho:
                conn.execute('UPDATE hospitals SET source_ids=? WHERE id=?',
                             (db.dumps({**source_ids,'hira':ykiho}),hid))
    return {'hospitals_linked':len(bundles), 'snapshots':len(bundles)*len(OPERATIONS)}
