"""국립중앙의료원 XML 수집·검증·원자적 적재. 공공 관측과 병원 운영값은 분리한다."""
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import db

BASE_URL = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/'
OPERATIONS = {
    'nmc_hospital_list': ('getEgytListInfoInqire', 'Q0'),
    'nmc_hospital_detail': ('getEgytBassInfoInqire', 'HPID'),
    'nmc_rltm_beds': ('getEmrrmRltmUsefulSckbdInfoInqire', 'STAGE1'),
    'nmc_severe_accept': ('getSrsillDissAceptncPosblInfoInqire', 'STAGE1'),
}
KST = timezone(timedelta(hours=9))
BED_FIELDS = {'hvec': 'ed_bed', 'hvoc': 'operating_room', 'hvicc': 'icu_bed',
              'hvncc': 'nicu_bed', 'hv42': 'delivery_room'}


class PublicDataError(Exception):
    """URL・인증키・원문을 포함하지 않는 수집 오류."""


def _tag(element):
    return element.tag.rsplit('}', 1)[-1]


def parse_response(body, require_total=True):
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise PublicDataError('API 응답이 유효한 XML이 아닙니다') from None
    values = {_tag(e): (e.text or '').strip() for e in root.iter()
              if _tag(e) in ('resultCode', 'returnReasonCode', 'totalCount', 'pageNo')}
    code = values.get('resultCode', values.get('returnReasonCode'))
    if code not in ('00', '0'):
        safe_code = code if code and re.fullmatch(r'[A-Za-z0-9_]{1,40}', code) else 'UNKNOWN'
        raise PublicDataError(f'API 오류 코드: {safe_code}')
    items = [{_tag(e): (e.text or '').strip() for e in item}
             for item in root.iter() if _tag(item) == 'item']
    try:
        total = int(values['totalCount']) if require_total or 'totalCount' in values else len(items)
        page = int(values['pageNo']) if values.get('pageNo') else None
        if total < 0 or (page is not None and page < 1):
            raise ValueError
    except (ValueError, KeyError):
        raise PublicDataError('API totalCount/pageNo 값이 올바르지 않습니다') from None
    return dict(items=items, total_count=total, page_no=page)


class NmcClient:
    def __init__(self, key=None, page_size=100, timeout=15, retries=2, max_pages=100, district=None,
                 opener=urllib.request.urlopen, sleep=time.sleep):
        key = key or os.environ.get('DATA_GO_KR_KEY', '')
        if not key.strip():
            raise PublicDataError('DATA_GO_KR_KEY 환경변수를 설정하세요')
        if not 1 <= page_size <= 1000 or not 1 <= max_pages <= 1000 or timeout <= 0 or not 0 <= retries <= 5:
            raise PublicDataError('API 페이지 크기·시간 제한·재시도 설정을 확인하세요')
        # 포털의 Encoding 키도 한 번만 디코딩한다. + 문자는 공백으로 바꾸지 않는다.
        self.key = urllib.parse.unquote(key.strip())
        self.district = district.strip() if isinstance(district, str) and district.strip() else None
        self.page_size, self.timeout, self.retries, self.max_pages = page_size, timeout, retries, max_pages
        self.opener, self.sleep = opener, sleep

    def _page(self, operation, region, number):
        path, region_param = OPERATIONS[operation]
        params = {'ServiceKey': self.key, region_param: region, 'pageNo': number, 'numOfRows': self.page_size}
        if operation in ('nmc_rltm_beds', 'nmc_severe_accept'):
            if not self.district:
                raise PublicDataError('가용병상·중증질환 조회에 시군구(--district)가 필요합니다')
            params['STAGE2'] = self.district
        elif operation == 'nmc_hospital_list' and self.district:
            params['Q1'] = self.district
        req = urllib.request.Request(BASE_URL + path + '?' + urllib.parse.urlencode(params))
        for attempt in range(self.retries + 1):
            try:
                with self.opener(req, timeout=self.timeout) as response:
                    body = response.read().decode('utf-8-sig')
                for secret in {self.key, urllib.parse.quote(self.key, safe=''), urllib.parse.quote_plus(self.key)}:
                    body = body.replace(secret, '***')
                return parse_response(body, require_total=operation != 'nmc_hospital_detail')
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                message = f'API HTTP 오류: {exc.code}'
            except (urllib.error.URLError, TimeoutError, OSError, UnicodeError):
                retryable, message = True, 'API 연결 또는 응답 읽기에 실패했습니다'
            if not retryable or attempt == self.retries:
                raise PublicDataError(message) from None
            self.sleep(min(2 ** attempt, 8))

    def fetch_all(self, operation, region):
        if operation not in OPERATIONS or not isinstance(region, str) or not region.strip():
            raise PublicDataError('수집 대상과 시도 이름을 확인하세요')
        records, total = [], None
        for number in range(1, self.max_pages + 1):
            page = self._page(operation, region, number)
            if total is not None and total != page['total_count']:
                raise PublicDataError('수집 중 전체 건수가 변경됐습니다. 다음 수집에서 다시 시도합니다')
            total = page['total_count']
            if page['page_no'] not in (None, number):
                raise PublicDataError('API 응답 페이지가 요청과 다릅니다')
            records.extend(page['items'])
            if len(records) == total:
                return records
            if not page['items'] or len(records) > total:
                raise PublicDataError('API 페이지 일부가 누락되거나 중복됐습니다')
        raise PublicDataError('최대 페이지 수를 초과했습니다. 부분 자료는 적재하지 않습니다')

    def fetch_hospitals(self, region):
        records = self.fetch_all('nmc_hospital_list', region)
        result = []
        for item in records:
            hpid = text(item, 'hpid')
            # 목록만으로 좌표를 확보하지 못하면 HPID별 기본정보 조회로 보완한다.
            if normalize_hospital(item) is None and hpid and re.fullmatch(r'[A-Za-z0-9_-]+', hpid):
                detail = self.fetch_detail(hpid)
                item = {**item, **{k: v for k, v in detail.items() if v}}
            result.append(item)
        return result

    def fetch_detail(self, hpid):
        if not isinstance(hpid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', hpid):
            raise PublicDataError('상세 조회 기관 ID가 올바르지 않습니다')
        detail = self._page('nmc_hospital_detail', hpid, 1)['items']
        if len(detail) != 1 or text(detail[0], 'hpid') != hpid:
            raise PublicDataError('병원 상세정보의 기관 ID가 요청과 다릅니다')
        return detail[0]


def text(item, field):
    value = item.get(field)
    return value.strip() or None if isinstance(value, str) else None


def observed_at(value):
    if not isinstance(value, str):
        return None
    for fmt in ('%Y%m%d%H%M%S', '%Y%m%d%H%M', '%Y-%m-%d %H:%M:%S'):
        try:
            return int(datetime.strptime(value.strip(), fmt).replace(tzinfo=KST).timestamp())
        except ValueError:
            pass
    return None


def normalize_hospital(item):
    hpid, name = text(item, 'hpid'), text(item, 'dutyName')
    if not hpid or not re.fullmatch(r'[A-Za-z0-9_-]+', hpid) or not name:
        return None
    try:
        lat, lon = float(item['wgs84Lat']), float(item['wgs84Lon'])
        if not all(math.isfinite(v) for v in (lat, lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        if (lat, lon) == (0, 0):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return dict(hpid=hpid, name=name, address=text(item, 'dutyAddr'),
                phone=text(item, 'dutyTel3') or text(item, 'dutyTel1'), lat=lat, lon=lon)


def normalize_beds(item):
    from .treatment_data import normalize_equipment_beds
    resources = []
    for field, resource_type in BED_FIELDS.items():
        raw = text(item, field)
        # 음수·공백·특수코드는 가용 0으로 추정하지 않는다. 기준 병상 HVS01은 사용하지 않는다.
        available = int(raw) if raw and re.fullmatch(r'[0-9]+', raw) else None
        resources.append(dict(resource_type=resource_type, source_field=field,
                              reported_available=available, status='reported' if available is not None else 'unknown'))
    # hv42에는 수량 외 Y/N이 함께 관측된다. boolean을 수량 1/0으로 변환하지 않는다.
    delivery_flag = text(item, 'hv42')
    delivery_available = True if delivery_flag == 'Y' else False if delivery_flag == 'N' else None
    service_availability = [dict(service_type='delivery_room', source_field='hv42',
                                 reported_available=delivery_available,
                                 status='reported' if delivery_available is not None else 'unknown')]
    return dict(resources=resources, equipment=normalize_equipment_beds(item)['equipment'],
                service_availability=service_availability,
                source_observed_at=observed_at(text(item, 'hvidate')),
                note='공공 가용수 관측이며 운영 총량·예약·환자별 수용 확답이 아닙니다')


def snapshot_view(conn, hid, now):
    result = []
    for r in conn.execute('SELECT * FROM public_api_snapshots WHERE hospital_id=? ORDER BY endpoint', (hid,)):
        stamp = r['source_observed_at']
        result.append(dict(endpoint=r['endpoint'], source_observed_at=stamp, fetched_at=r['fetched_at'],
                           age_seconds=None if stamp is None else max(0, now - stamp),
                           stale=None if stamp is None else now - stamp > 600,
                           observation_time_unknown=stamp is None, data=json.loads(r['normalized'])))
    return result


def apply_records(conn, now, basic_records=None, bed_records=None, detail_records=None, severe_records=None):
    """완전히 수집한 페이지를 한 트랜잭션에 적재한다. 병원 수동값·기존 환자를 보존한다."""
    stats = dict(hospitals_created=0, hospitals_updated=0, snapshots=0, skipped_invalid=0, unmatched_beds=0)
    with db.transaction(conn):
        lookup = {}
        for h in db.rows(conn.execute('SELECT * FROM hospitals')):
            hpid = h['source_ids'].get('nmc')
            if hpid:
                if hpid in lookup:
                    raise PublicDataError('한 공공 기관 ID가 여러 내부 병원에 연결돼 있습니다')
                lookup[hpid] = h
        seen = set()
        for item in basic_records or []:
            h = normalize_hospital(item)
            if h is None:
                stats['skipped_invalid'] += 1
                continue
            if h['hpid'] in seen:
                raise PublicDataError('기관 목록에 중복 기관 ID가 있습니다')
            seen.add(h['hpid'])
            old = lookup.get(h['hpid'])
            hid = old['id'] if old else 'NMC-' + h['hpid']
            if old is None:
                if conn.execute('SELECT 1 FROM hospitals WHERE id=?', (hid,)).fetchone():
                    raise PublicDataError('내부 병원 ID가 공공 기관 ID와 충돌합니다')
                conn.execute('INSERT INTO hospitals(id,name,address,phone,lat,lon,capabilities,source,source_ids,capabilities_status) '
                             "VALUES(?,?,?,?,?,?,'[]','public',?,'unknown')",
                             (hid, h['name'], h['address'], h['phone'], h['lat'], h['lon'], db.dumps({'nmc': h['hpid']})))
                stats['hospitals_created'] += 1
            elif old['source'] == 'public':
                # 누락된 선택 필드로 기존 연락처 등을 지우지 않는다. 수동 진료분야는 손대지 않는다.
                values = (h['name'], h['address'] or old['address'], h['phone'] or old['phone'], h['lat'], h['lon'])
                if values != tuple(old[k] for k in ('name', 'address', 'phone', 'lat', 'lon')):
                    conn.execute('UPDATE hospitals SET name=?,address=?,phone=?,lat=?,lon=? WHERE id=?', (*values, hid))
                    stats['hospitals_updated'] += 1
            _save_snapshot(conn, hid, 'nmc_hospital_list', now, None, h, item)
            lookup[h['hpid']] = {'id': hid}
            stats['snapshots'] += 1
        seen = set()
        for item in bed_records or []:
            hpid = text(item, 'hpid')
            if not hpid or hpid not in lookup:
                stats['unmatched_beds'] += 1
                continue
            if hpid in seen:
                raise PublicDataError('가용병상 목록에 중복 기관 ID가 있습니다')
            seen.add(hpid)
            normalized = normalize_beds(item)
            stamp = normalized['source_observed_at']
            if stamp is not None and stamp > now + 300:
                normalized['source_observed_at'] = stamp = None
                for r in normalized['resources']:
                    r.update(reported_available=None, status='unknown')
                for equipment in normalized['equipment']:
                    equipment.update(reported_available=None, status='unknown')
                for service_flag in normalized['service_availability']:
                    service_flag.update(reported_available=None, status='unknown')
            old = conn.execute('SELECT source_observed_at FROM public_api_snapshots '
                               "WHERE hospital_id=? AND endpoint='nmc_rltm_beds'", (lookup[hpid]['id'],)).fetchone()
            if old and old['source_observed_at'] is not None and (stamp is None or stamp < old['source_observed_at']):
                stats['skipped_invalid'] += 1
                continue
            _save_snapshot(conn, lookup[hpid]['id'], 'nmc_rltm_beds', now, stamp, normalized, item)
            stats['snapshots'] += 1
        from .treatment_data import normalize_detail, normalize_severe
        for endpoint, records, normalizer in (
                ('nmc_hospital_detail', detail_records, normalize_detail),
                ('nmc_severe_accept', severe_records, normalize_severe)):
            seen = set()
            for item in records or []:
                hpid = text(item, 'hpid')
                if not hpid or hpid not in lookup:
                    raise PublicDataError('상세·중증질환 자료의 기관 ID가 병원 목록과 일치하지 않습니다')
                if hpid in seen:
                    raise PublicDataError('상세·중증질환 자료에 중복 기관 ID가 있습니다')
                seen.add(hpid)
                # 등록 진료과·시설과 원문 코드 관측은 별도 보존한다. 운영 역량을 추정하지 않는다.
                _save_snapshot(conn, lookup[hpid]['id'], endpoint, now, None, normalizer(item), item)
                stats['snapshots'] += 1
    return stats


def _save_snapshot(conn, hid, endpoint, now, stamp, normalized, raw):
    conn.execute('INSERT INTO public_api_snapshots VALUES(?,?,?,?,?,?) '
                 'ON CONFLICT(hospital_id,endpoint) DO UPDATE SET source_observed_at=excluded.source_observed_at, '
                 'fetched_at=excluded.fetched_at, normalized=excluded.normalized, raw_data=excluded.raw_data',
                 (hid, endpoint, stamp, now, db.dumps(normalized), db.dumps(raw)))


def sync_once(path, region, client=None, include_basic=True, clock=time.time,
              include_details=False, include_severe=False):
    client = client or NmcClient()
    conn = db.connect(str(path))
    db.init(conn)
    started = int(clock())
    district = getattr(client, 'district', None)
    run_id = conn.execute("INSERT INTO public_sync_runs(region,district,started_at,status) VALUES(?,?,?,'RUNNING')",
                          (region, district, started)).lastrowid
    try:
        # 통신은 쓰기 트랜잭션 밖에서 수행한다. 한쪽 호출 실패 시 부분 적재하지 않는다.
        if include_basic:
            basic = client.fetch_hospitals(region) if hasattr(client, 'fetch_hospitals') else client.fetch_all('nmc_hospital_list', region)
        else:
            basic = None
        beds = client.fetch_all('nmc_rltm_beds', region)
        details = [client.fetch_detail(text(item, 'hpid')) for item in basic] if include_details and basic is not None else None
        if include_details and basic is None:
            raise PublicDataError('상세 수집에는 병원 목록 수집이 필요합니다')
        severe = client.fetch_all('nmc_severe_accept', region) if include_severe else None
        stats = apply_records(conn, int(clock()), basic, beds, details, severe)
        conn.execute("UPDATE public_sync_runs SET finished_at=?,status='SUCCESS',stats=? WHERE id=?",
                     (int(clock()), db.dumps(stats), run_id))
        return dict(run_id=run_id, region=region, district=district, status='SUCCESS', **stats)
    except Exception as exc:
        # 예상치 못한 예외에는 URL/인증키가 들어 있을 수 있어 원문을 기록하지 않는다.
        message = str(exc) if isinstance(exc, PublicDataError) else '적재 처리 중 오류가 발생했습니다'
        conn.execute("UPDATE public_sync_runs SET finished_at=?,status='ERROR',error=? WHERE id=?",
                     (int(clock()), message, run_id))
        raise PublicDataError(message) from None
    finally:
        conn.close()
