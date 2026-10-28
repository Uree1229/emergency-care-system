"""NMC 응급실·중증질환 메시지 수집. 표출 상태를 환자 수용 확답으로 바꾸지 않는다.

공식 전국 응급의료정보조회 서비스 V13(2026-08-27)의 9번 오퍼레이션:
getEmrrmSrsillDissMsgInqire. HPID 및 pageNo/numOfRows로 기관별 전체 페이지를 읽는다.
symBlkSttDtm/symBlkEndDtm은 메시지 시작·해제 시각이며 원천 갱신시각이 아니다.
"""
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db
from .public_data import BASE_URL, PublicDataError, _save_snapshot, observed_at, parse_response, text


ENDPOINT = 'nmc_acceptance_messages'
OPERATION = 'getEmrrmSrsillDissMsgInqire'
MESSAGE_FIELDS = ('symBlkMsg', 'symBlkMsgTyp', 'symTypCod', 'symTypCodMag',
                  'symOutDspYon', 'symOutDspMth', 'symBlkSttDtm', 'symBlkEndDtm')


class MessageDataError(PublicDataError):
    """인증키·URL·응답 원문을 포함하지 않는 수집 오류."""
    def __init__(self, message, access_denied=False):
        super().__init__(message)
        self.access_denied = access_denied


def _valid_hpid(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) is not None


def validate_records(hpid, records):
    """여러 메시지는 허용하되 기관이 섞인 전체 결과는 적재하지 않는다."""
    if not _valid_hpid(hpid) or not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise MessageDataError('메시지 조회 기관 ID와 자료 형식을 확인하세요')
    for row in records:
        if text(row, 'hpid') != hpid:
            raise MessageDataError('메시지 자료의 기관 ID가 요청과 다릅니다')
        if text(row, 'emcOrgCod') not in (None, hpid):
            raise MessageDataError('메시지 기관 코드가 기관 ID와 다릅니다')


class NmcMessageClient:
    def __init__(self, key=None, page_size=100, timeout=15, retries=2, max_pages=100,
                 opener=urllib.request.urlopen, sleep=time.sleep):
        key = key or os.environ.get('DATA_GO_KR_KEY', '')
        if not isinstance(key, str) or not key.strip():
            raise MessageDataError('DATA_GO_KR_KEY 환경변수를 설정하세요')
        if not 1 <= page_size <= 1000 or not 1 <= max_pages <= 1000 or timeout <= 0 or not 0 <= retries <= 5:
            raise MessageDataError('API 페이지 크기·시간 제한·재시도 설정을 확인하세요')
        self.key = urllib.parse.unquote(key.strip())
        self.page_size, self.timeout, self.retries, self.max_pages = page_size, timeout, retries, max_pages
        self.opener, self.sleep = opener, sleep

    def _page(self, hpid, number):
        # 기관명·주소로 추정하지 않고 검증된 NMC 기관 ID를 필터로 사용한다.
        params = {'ServiceKey': self.key, 'HPID': hpid, 'pageNo': number, 'numOfRows': self.page_size}
        req = urllib.request.Request(BASE_URL + OPERATION + '?' + urllib.parse.urlencode(params))
        for attempt in range(self.retries + 1):
            try:
                with self.opener(req, timeout=self.timeout) as response:
                    body = response.read().decode('utf-8-sig')
                for secret in {self.key, urllib.parse.quote(self.key, safe=''), urllib.parse.quote_plus(self.key)}:
                    body = body.replace(secret, '***')
                try:
                    return parse_response(body)
                except PublicDataError as exc:
                    message = str(exc)
                    denied = message in ('API 오류 코드: 20', 'API 오류 코드: 30', 'API 오류 코드: 31')
                    raise MessageDataError(message, access_denied=denied) from None
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                message = f'API HTTP 오류: {exc.code}'
                denied = exc.code in (401, 403)
            except (urllib.error.URLError, TimeoutError, OSError, UnicodeError):
                retryable, message, denied = True, 'API 연결 또는 응답 읽기에 실패했습니다', False
            if not retryable or attempt == self.retries:
                raise MessageDataError(message, access_denied=denied) from None
            self.sleep(min(2 ** attempt, 8))

    def fetch_all(self, hpid):
        if not _valid_hpid(hpid):
            raise MessageDataError('메시지 조회 기관 ID가 올바르지 않습니다')
        records, total = [], None
        for number in range(1, self.max_pages + 1):
            page = self._page(hpid, number)
            if total is not None and total != page['total_count']:
                raise MessageDataError('수집 중 전체 메시지 건수가 변경됐습니다. 다음 수집에서 다시 시도합니다')
            total = page['total_count']
            if page['page_no'] not in (None, number):
                raise MessageDataError('API 응답 페이지가 요청과 다릅니다')
            records.extend(page['items'])
            if len(records) == total:
                validate_records(hpid, records)
                return records
            if not page['items'] or len(records) > total:
                raise MessageDataError('API 메시지 페이지 일부가 누락되거나 중복됐습니다')
        raise MessageDataError('최대 페이지 수를 초과했습니다. 부분 메시지는 적재하지 않습니다')


def _message_time(value):
    # 이 오퍼레이션의 명세 형식은 14자리. 시작/해제 시각을 관측시각으로 쓰지 않는다.
    return observed_at(value) if isinstance(value, str) and re.fullmatch(r'\d{14}', value) else None


def normalize_messages(records):
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise MessageDataError('메시지 정규화 자료 형식을 확인하세요')
    messages = []
    for row in records:
        flag, method = text(row, 'symOutDspYon'), text(row, 'symOutDspMth')
        messages.append(dict(
            message=text(row, 'symBlkMsg'), message_type_raw=text(row, 'symBlkMsgTyp'),
            disease_code=text(row, 'symTypCod'), disease_name=text(row, 'symTypCodMag'),
            display_state_raw=flag,
            display_state={'Y': 'released', '해제': 'released', 'N': 'blocked', '차단': 'blocked'}.get(flag, 'unknown'),
            display_method_raw=method,
            display_method={'A': 'automatic', '자동': 'automatic', 'B': 'manual', '수동': 'manual'}.get(method, 'unknown'),
            starts_at=_message_time(text(row, 'symBlkSttDtm')),
            ends_at=_message_time(text(row, 'symBlkEndDtm')),
            source_observed_at=None,
            source_fields={field: row.get(field) for field in MESSAGE_FIELDS},
        ))
    return dict(source='public', provider='nmc', data_kind='acceptance_messages',
                record_count=len(records), empty_result_not_absence=True,
                source_observed_at=None, messages=messages,
                note='메시지와 표출 상태의 공개 관측. 원천 갱신시각 미상. 시작·해제 시각은 갱신시각이 아니며, '
                     '빈 결과·자유문·표출 차단을 현재 환자의 수용 가능/불가 확답으로 사용하지 않음')


def hospital_targets(conn):
    """공공 병원만 수집한다. 하나의 NMC ID가 여러 기관에 연결됐으면 중단한다."""
    hospitals = db.rows(conn.execute('SELECT * FROM hospitals ORDER BY id'))
    seen, targets, skipped = set(), [], []
    for hospital in hospitals:
        hpid = hospital['source_ids'].get('nmc')
        if hpid:
            if not _valid_hpid(hpid):
                raise MessageDataError('저장된 NMC 기관 ID 형식이 올바르지 않습니다')
            if hpid in seen:
                raise MessageDataError('하나의 NMC 기관 ID가 여러 병원에 연결돼 있습니다')
            seen.add(hpid)
        if hospital['source'] == 'public':
            if not hpid:
                skipped.append(hospital['id'])
            else:
                targets.append({'hospital_id': hospital['id'], 'hpid': hpid})
    return targets, skipped


def apply_bundles(conn, now, bundles):
    """완전히 받은 병원별 메시지 묶음을 원자적으로 저장하고 운영값은 보존한다."""
    if not isinstance(bundles, list):
        raise MessageDataError('메시지 적재 묶음 형식을 확인하세요')
    stats = dict(hospitals_updated=0, snapshots=0, message_count=0, empty_results=0, skipped_older=0)
    with db.transaction(conn):
        targets, _ = hospital_targets(conn)
        lookup = {target['hospital_id']: target['hpid'] for target in targets}
        seen = set()
        for bundle in bundles:
            if not isinstance(bundle, dict):
                raise MessageDataError('메시지 적재 묶음 형식을 확인하세요')
            hid, hpid, records = bundle.get('hospital_id'), bundle.get('hpid'), bundle.get('records')
            if not isinstance(hid, str) or hid in seen or lookup.get(hid) != hpid:
                raise MessageDataError('메시지의 내부 병원과 NMC 기관 연결을 확인하세요')
            seen.add(hid)
            validate_records(hpid, records)
            old = conn.execute('SELECT fetched_at FROM public_api_snapshots WHERE hospital_id=? AND endpoint=?',
                               (hid, ENDPOINT)).fetchone()
            if old is not None and old['fetched_at'] > now:
                stats['skipped_older'] += 1
                continue
            _save_snapshot(conn, hid, ENDPOINT, now, None, normalize_messages(records), {'items': records})
            stats['hospitals_updated'] += 1
            stats['snapshots'] += 1
            stats['message_count'] += len(records)
            stats['empty_results'] += not records
    return stats
