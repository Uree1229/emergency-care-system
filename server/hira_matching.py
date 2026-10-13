"""NMC 병원과 HIRA 기관 ID의 보수적인 연결 후보 판정.

DB·네트워크·환경변수를 사용하지 않는 순수 함수다. 이름의 정확한 정규화
일치, 부가 괄호를 제거한 주소 일치, 양쪽 좌표의 300 m 이내 근접을 모두
요구한다. 별도 공식 근거로 검토한 1:1 기관쌍만 정확한 식별정보 해시,
전화 일치, 유효 좌표 500 m 이내 조건 아래 이름·주소 표기 차이를 허용한다.
해시가 바뀌면 검토 예외가 해제되며 원래의 엄격한 조건으로 다시 평가한다.
이 결과는 기관 식별 연결이며 현재 진료·수용 가능의 근거가 아니다.
"""
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse


MAX_DISTANCE_METERS = 300.0
REVIEWED_MAX_DISTANCE_METERS = 500.0
EARTH_RADIUS_METERS = 6_371_000.0
REVIEWED_LINKS_PATH = Path(__file__).resolve().parents[1] / 'config' / 'hospital_identity_links.json'


def identity_fingerprint(record, provider):
    if provider not in ('nmc', 'hira') or not isinstance(record, dict):
        raise ValueError('기관 식별정보의 제공처와 자료 형식을 확인하세요')
    fields = ('id', 'name', 'address', 'phone', 'lat', 'lon') if provider == 'nmc' else (
        'ykiho', 'yadmNm', 'addr', 'telno', 'XPos', 'YPos')
    value = {field: record.get(field) for field in fields}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def load_reviewed_links(path=REVIEWED_LINKS_PATH):
    path = Path(path)
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict):
        raise ValueError('검토된 기관 연결 문서 형식을 확인하세요')
    links = document.get('links')
    _validate_reviewed_links(links)
    return links


def _validate_reviewed_links(links):
    """파일과 직접 전달한 검토 근거에 같은 1:1·필수정보 계약을 적용한다."""
    if not isinstance(links, list):
        raise ValueError('검토된 기관 연결 목록을 확인하세요')
    pairs, hospitals, institutions = set(), set(), set()
    for link in links:
        if not isinstance(link, dict) or any(not isinstance(link.get(k), str) or not link[k].strip()
                                            for k in ('hospital_id', 'ykiho', 'reviewed_on', 'reason')):
            raise ValueError('검토된 기관 연결 근거를 확인하세요')
        if any(link[key] != link[key].strip() for key in ('hospital_id', 'ykiho', 'reviewed_on')):
            raise ValueError('검토된 기관 연결 식별자와 날짜를 확인하세요')
        try:
            if date.fromisoformat(link['reviewed_on']).isoformat() != link['reviewed_on']:
                raise ValueError
        except ValueError:
            raise ValueError('검토된 기관 연결의 검토 날짜를 확인하세요') from None
        if (not isinstance(link.get('source_urls'), list) or not link['source_urls'] or
                any(not _valid_source_url(url) for url in link['source_urls'])):
            raise ValueError('검토된 기관 연결의 공식 출처를 확인하세요')
        if any(not re.fullmatch(r'[0-9a-f]{64}', str(link.get(k, '')))
               for k in ('nmc_identity_sha256', 'hira_identity_sha256')):
            raise ValueError('검토된 기관 식별정보의 해시를 확인하세요')
        pair = (link['hospital_id'], link['ykiho'])
        if pair in pairs:
            raise ValueError('검토된 기관 연결이 중복됐습니다')
        if link['hospital_id'] in hospitals or link['ykiho'] in institutions:
            raise ValueError('검토된 기관 연결은 병원과 기관 ID가 1:1이어야 합니다')
        pairs.add(pair)
        hospitals.add(link['hospital_id'])
        institutions.add(link['ykiho'])


def _valid_source_url(value):
    if not isinstance(value, str) or value != value.strip() or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlparse(value)
        return (parsed.scheme == 'https' and bool(parsed.hostname) and
                parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def _reviewed_evidence(hospital, record, links):
    for link in links or []:
        if link.get('hospital_id') != hospital.get('id') or link.get('ykiho') != record.get('ykiho'):
            continue
        if (identity_fingerprint(hospital, 'nmc') != link.get('nmc_identity_sha256') or
                identity_fingerprint(record, 'hira') != link.get('hira_identity_sha256')):
            return None
        a = _coordinates(hospital.get('lat'), hospital.get('lon'))
        b = _coordinates(record.get('YPos'), record.get('XPos'))
        # 검토된 단일 기관쌍도 유효 좌표·대표전화 일치·500m 상한을 유지한다.
        if (not _name(hospital.get('name')) or not _name(record.get('yadmNm')) or
                not _address(hospital.get('address')) or not _address(record.get('addr')) or
                a is None or b is None or _distance(a, b) > REVIEWED_MAX_DISTANCE_METERS + 1e-6 or
                not _phone(hospital.get('phone')) or
                _phone(hospital.get('phone')) != _phone(record.get('telno'))):
            return None
        return {key: link[key] for key in ('reviewed_on', 'reason', 'source_urls',
                                          'nmc_identity_sha256', 'hira_identity_sha256')}
    return None


def _string(value):
    return value.strip() if isinstance(value, str) else ''


def _name(value):
    # 법인명·진료부서·분원명은 제거하지 않는다. 부분 이름으로도 비교하지 않는다.
    value = unicodedata.normalize('NFKC', _string(value))
    return ''.join(c for c in value if not c.isspace() and not unicodedata.category(c).startswith('P'))


def _address(value):
    value = unicodedata.normalize('NFKC', _string(value))
    # 짝이 맞는 괄호 안의 동 이름·건물명만 제거한다. 불완전한 괄호는 추측하지 않는다.
    depth = 0
    outside = []
    for char in value:
        if char == '(':
            depth += 1
        elif char == ')':
            if depth == 0:
                return ''
            depth -= 1
        elif depth == 0:
            outside.append(char)
    if depth:
        return ''
    # 건물번호의 하이픈, 층·호 등은 유지해 다른 위치를 합치지 않는다.
    # 괄호 앞 쉼표만 남은 '도로 12-3, (동 이름)'의 끝 구분자는 주소 정보가 아니다.
    # 주소 내부의 쉼표, 건물번호의 하이픈, 층·호는 그대로 비교한다.
    return re.sub(r'\s+', ' ', ''.join(outside)).strip().rstrip(',').strip()


def _coordinates(lat, lon):
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if (not math.isfinite(lat) or not math.isfinite(lon) or
            not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat, lon) == (0, 0)):
        return None
    return lat, lon


def _distance(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    haversine = (math.sin((lat2 - lat1) / 2) ** 2 +
                 math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))


def _phone(value):
    return re.sub(r'[^0-9]', '', unicodedata.normalize('NFKC', _string(value)))


def _unique(values):
    return list(dict.fromkeys(values))


def match_hospitals(hospitals, records, reviewed_links=None):
    """기관 연결 결과를 matches/unmatched/ambiguous 세 목록으로 반환한다.

    hospitals: NMC 내부 id/name/address/phone/lat/lon/source_ids dict 목록.
    records: HIRA ykiho/yadmNm/addr/telno/XPos(경도)/YPos(위도) dict 목록.

    자동 연결 조건은 정규화 이름·주소의 완전 일치와 양쪽 유효 좌표의
    300 m 이내 거리다. 전화는 보조 근거이며 나머지 필수조건을 대체하지
    않는다. 응급전화와 대표전화가 다를 수 있어 차이는 evidence에 남긴다.
    중복 ID, 복수 후보, 두 NMC 병원의 동일 HIRA ID 사용, 기존 매핑과의
    충돌은 ambiguous로 보류한다. 기존 source_ids도 수정하지 않는다.

    reviewed_links는 별도로 검토한 1:1 기관쌍에만 적용한다. 양쪽 식별정보
    해시가 일치하고 전화가 같으며 유효 좌표가 500 m 이내일 때 표기 차이와
    일반 거리 제한을 예외 처리한다. 해시가 달라지면 예외만 해제하며,
    원래 300 m·이름·주소 조건을 충족하는 경우에는 일반 매칭이 가능하다.

    unmatched의 candidates에는 동일 이름의 레코드와 충족하지 못한 조건을
    남긴다. 검토자가 없는 좌표·주소를 추정해서 채우지 않도록 하기 위함이다.
    """
    if not isinstance(hospitals, (list, tuple)) or not isinstance(records, (list, tuple)):
        raise TypeError('hospitals와 records는 dict 목록이어야 합니다')
    if any(not isinstance(item, dict) for item in (*hospitals, *records)):
        raise TypeError('hospitals와 records의 각 항목은 dict여야 합니다')
    reviewed_links = [] if reviewed_links is None else reviewed_links
    _validate_reviewed_links(reviewed_links)

    prepared = []
    for index, record in enumerate(records):
        prepared.append(dict(index=index, ykiho=_string(record.get('ykiho')),
                             name=_name(record.get('yadmNm')), address=_address(record.get('addr')),
                             coordinates=_coordinates(record.get('YPos'), record.get('XPos')),
                             phone=_phone(record.get('telno'))))
    duplicate_hira = {key for key, count in Counter(r['ykiho'] for r in prepared if r['ykiho']).items()
                      if count > 1}
    duplicate_hospital_ids = {key for key, count in Counter(_string(h.get('id')) for h in hospitals).items()
                              if key and count > 1}
    possible_owners = defaultdict(set)
    evaluations = []
    for hospital_index, hospital in enumerate(hospitals):
        hid = _string(hospital.get('id'))
        normalized_name = _name(hospital.get('name'))
        normalized_address = _address(hospital.get('address'))
        coordinates = _coordinates(hospital.get('lat'), hospital.get('lon'))
        phone = _phone(hospital.get('phone'))
        source_ids = hospital.get('source_ids') or {}
        invalid_source_ids = not isinstance(source_ids, dict)
        raw_existing = source_ids.get('hira') if not invalid_source_ids else None
        existing = _string(raw_existing)
        invalid_existing = raw_existing is not None and not isinstance(raw_existing, str)
        if existing:
            possible_owners[existing].add(hospital_index)
        candidates, eligible = [], []
        for record in prepared:
            reviewed = _reviewed_evidence(hospital, records[record['index']], reviewed_links)
            if (not normalized_name or record['name'] != normalized_name) and not reviewed:
                continue
            reasons = []
            if not record['ykiho']:
                reasons.append('HIRA_ID_MISSING')
            if not normalized_address or not record['address']:
                reasons.append('ADDRESS_MISSING_OR_INVALID')
            elif normalized_address != record['address']:
                reasons.append('ADDRESS_MISMATCH')
            distance = None
            if coordinates is None or record['coordinates'] is None:
                reasons.append('COORDINATES_MISSING_OR_INVALID')
            else:
                distance = _distance(coordinates, record['coordinates'])
                if distance > MAX_DISTANCE_METERS + 1e-6:
                    reasons.append('DISTANCE_EXCEEDED')
            phone_comparison = ('missing' if not phone or not record['phone'] else
                                ('same' if phone == record['phone'] else 'different'))
            automatic_reasons = list(reasons)
            if reviewed:
                reasons = [reason for reason in reasons if reason not in ('ADDRESS_MISMATCH', 'DISTANCE_EXCEEDED')]
            evidence = dict(normalized_name=normalized_name, normalized_address=normalized_address,
                            hira_normalized_address=record['address'],
                            name_exact_match=record['name'] == normalized_name,
                            address_exact_match=bool(normalized_address and normalized_address == record['address']),
                            coordinates_valid=coordinates is not None and record['coordinates'] is not None,
                            distance_meters=None if distance is None else round(distance, 3),
                            distance_within_limit=distance is not None and distance <= MAX_DISTANCE_METERS + 1e-6,
                            maximum_distance_meters=MAX_DISTANCE_METERS,
                            strict_match=not automatic_reasons and record['name'] == normalized_name,
                            identity_verified=not reasons,
                            match_method='reviewed_identity_link' if reviewed else 'exact_name_address_coordinates',
                            reviewed_identity=reviewed,
                            reviewed_maximum_distance_meters=REVIEWED_MAX_DISTANCE_METERS if reviewed else None,
                            phone_comparison=phone_comparison,
                            phone_is_identity_requirement=bool(reviewed))
            candidate = dict(ykiho=record['ykiho'] or None, record_index=record['index'],
                             reasons=reasons, evidence=evidence)
            candidates.append(candidate)
            if not reasons:
                eligible.append(candidate)
                possible_owners[record['ykiho']].add(hospital_index)
        evaluations.append(dict(hospital_id=hid or None, hospital_index=hospital_index,
                                normalized_name=normalized_name, existing_ykiho=existing or None,
                                invalid_source_ids=invalid_source_ids, invalid_existing=invalid_existing,
                                candidates=candidates, eligible=eligible))

    result = dict(matches=[], unmatched=[], ambiguous=[])
    for item in evaluations:
        eligible = item['eligible']
        reasons = []
        if item['hospital_id'] is None:
            reasons.append('HOSPITAL_ID_MISSING')
        elif item['hospital_id'] in duplicate_hospital_ids:
            reasons.append('DUPLICATE_HOSPITAL_ID')
        if item['invalid_source_ids']:
            reasons.append('INVALID_SOURCE_IDS')
        if item['invalid_existing']:
            reasons.append('INVALID_EXISTING_HIRA_ID')
        identifiers = {candidate['ykiho'] for candidate in eligible}
        if item['existing_ykiho']:
            identifiers.add(item['existing_ykiho'])
        if any(identifier in duplicate_hira for identifier in identifiers):
            reasons.append('DUPLICATE_HIRA_ID')
        if len(eligible) > 1:
            reasons.append('MULTIPLE_MATCHING_RECORDS')
        if item['existing_ykiho'] and any(c['ykiho'] != item['existing_ykiho'] for c in eligible):
            reasons.append('EXISTING_HIRA_ID_CONFLICT')
        if any(len(possible_owners[identifier]) > 1 for identifier in identifiers):
            reasons.append('SHARED_HIRA_ID')
        common = dict(hospital_id=item['hospital_id'], existing_ykiho=item['existing_ykiho'])
        if reasons:
            result['ambiguous'].append({**common, 'reasons': _unique(reasons), 'candidates': item['candidates']})
        elif eligible:
            candidate = eligible[0]
            result['matches'].append({**common, 'ykiho': candidate['ykiho'],
                                      'evidence': candidate['evidence']})
        else:
            rejected = [reason for candidate in item['candidates'] for reason in candidate['reasons']]
            if not item['normalized_name']:
                rejected.append('HOSPITAL_NAME_MISSING')
            elif not item['candidates']:
                rejected.append('NO_EXACT_NAME_MATCH')
            result['unmatched'].append({**common, 'reasons': _unique(rejected), 'candidates': item['candidates']})
    return result
