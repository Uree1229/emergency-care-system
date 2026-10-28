"""공개 진료정보의 정규화와 보수적인 환자조건 비교.

공개된 진료과목은 현재 당직·수술팀·환자별 수용 확답과 다르다. 이 모듈은
DB나 운영 자원을 변경하지 않으며, 확인되지 않은 정보는 불가로 추정하지 않는다.
"""
import copy
import math
import re
import time
from datetime import datetime, timedelta, timezone


MATCH = 'MATCH'
EXCLUDED = 'EXCLUDED'
NEEDS_CONFIRMATION = 'NEEDS_CONFIRMATION'

EQUIPMENT_FIELDS = {
    'hvctayn': 'ct', 'hvmriayn': 'mri', 'hvangioayn': 'angiography',
    'hvventiayn': 'ventilator', 'hvventisoayn': 'neonatal_ventilator',
    'hvincuayn': 'incubator',
}
FACILITY_FIELDS = {
    'hperyn': 'ed_bed', 'hpicuyn': 'general_icu_bed', 'hpcuyn': 'neurological_icu_bed',
    'hpccuyn': 'thoracic_icu_bed', 'hpnicuyn': 'nicu_bed',
    'hpopyn': 'operating_room', 'hpgryn': 'inpatient_bed',
}
# 검증된 심평원 장비 코드와 이름만 연결한다. 콘빔 CT, 일반 인공호흡기 등을
# 다른 장비/신생아 치료 역량으로 확대 해석하지 않는다.
HIRA_EQUIPMENT = {
    'ct': {'codes': {'B108'}, 'names': {'CT'}},
    'mri': {'codes': {'B301'}, 'names': {'MRI'}},
    'ventilator': {'codes': {'D202'}, 'names': {'인공호흡기'}},
}
# 일반 ICU와 성인 ICU 등록 수는 별도 종류다. 병합하여 가용수로 사용하지 않는다.
RESOURCE_FACILITY_TYPES = {'icu_bed': {'general_icu_bed', 'adult_icu_bed'}}


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def normalize_detail(item):
    """명세에서 확인한 진료과목만 해석한다. 장비·질환 코드는 별도 검증 대상이다."""
    raw = _text(item.get('dgidIdName'))
    names = list(dict.fromkeys(name.strip() for name in raw.split(',') if name.strip()))
    facilities = []
    for field, facility_type in FACILITY_FIELDS.items():
        value = _text(item.get(field))
        count = int(value) if re.fullmatch(r'[0-9]+', value) else None
        facilities.append({'facility_type': facility_type, 'source_field': field,
                           'reported_count': count,
                           'status': 'reported' if count is not None else 'unknown'})
    return {
        'departments': names,
        'departments_status': 'reported' if names else 'unknown',
        'departments_source_field': 'dgidIdName',
        'equipment': [],
        'facilities': facilities,
        'source': 'public',
        'source_observed_at': None,
        'note': '등록 진료과목·시설수이며 현재 의료진·장비 가용성, 운영 자원 총량, 환자별 수용 확답이 아닙니다',
    }


def normalize_equipment_beds(item):
    """V13 가용병상 API의 검증된 장비 필드만 Y/N 관측으로 해석한다."""
    equipment = []
    for field, equipment_type in EQUIPMENT_FIELDS.items():
        value = _text(item.get(field))
        available = True if value == 'Y' else False if value == 'N' else None
        equipment.append({'equipment_type': equipment_type, 'source_field': field,
                          'reported_available': available,
                          'status': 'reported' if available is not None else 'unknown'})
    stamp = None
    for fmt in ('%Y%m%d%H%M%S', '%Y%m%d%H%M', '%Y-%m-%d %H:%M:%S'):
        try:
            stamp = int(datetime.strptime(_text(item.get('hvidate')), fmt)
                        .replace(tzinfo=timezone(timedelta(hours=9))).timestamp())
            break
        except ValueError:
            pass
    return {'equipment': equipment, 'source': 'public', 'source_observed_at': stamp,
            'note': '공개 장비 가용 관측이며 보유 대수·예약·환자별 수용 확답이 아닙니다'}


def normalize_severe(item):
    """버전별 의미가 다른 중증질환 코드와 원문 값을 해석 없이 보존한다.

    필드 대소문자는 정규화하지 않는다. 예를 들어 MKioskTy1과 mkioskty1이
    함께 있으면 두 필드를 모두 남겨 충돌을 숨기지 않는다.
    """
    fields = []
    messages = []
    for field, value in item.items():
        if not isinstance(field, str):
            continue
        if re.fullmatch(r'mkioskty\d+', field, re.IGNORECASE):
            fields.append({'source_field': field, 'reported_value': value,
                           'status': 'uninterpreted'})
        elif re.fullmatch(r'mkioskty\d+msg', field, re.IGNORECASE):
            messages.append({'source_field': field, 'reported_value': value})
    return {'source': 'public', 'code_version': 'unverified', 'fields': fields,
            'messages': messages, 'source_observed_at': None,
            'note': '질환 코드의 의미·값·부가조건은 검증 전이며 자동 불가 판정에 사용하지 않습니다'}


def _observation(value):
    """snapshot_view의 {data, ...} 형식과 순수 normalized dict를 모두 받는다."""
    if value is None:
        return {}, {}
    if not isinstance(value, dict):
        return {}, {}
    if isinstance(value.get('data'), dict):
        return value['data'], value
    return value, value


def _freshness(data, meta, now, max_age_seconds):
    if meta.get('stale') is True:
        return 'stale'
    stamp = meta.get('source_observed_at', data.get('source_observed_at'))
    if (not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or
            not math.isfinite(stamp)):
        return 'unknown'
    if stamp > now + 300:
        return 'unknown'
    return 'stale' if now - stamp > max_age_seconds else 'fresh'


def _names(value, label):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(label + '는 비어 있지 않은 문자열 목록이어야 합니다')
    return list(dict.fromkeys(v.strip() for v in value))


def _provenance(endpoint, snapshot, now, max_age_seconds):
    data, meta = _observation(snapshot)
    return {'endpoint': endpoint,
            'provider': data.get('provider', 'nmc' if endpoint.startswith('nmc_') else 'hira'),
            'source_observed_at': meta.get('source_observed_at', data.get('source_observed_at')),
            'fetched_at': meta.get('fetched_at'),
            'freshness': _freshness(data, meta, now, max_age_seconds),
            'explicitly_stale': meta.get('stale') is True}


def _registered_inventory(observations, now, max_age_seconds):
    """등록 근거를 현재 가용 관측과 분리한다. 결측/빈 결과를 부재로 바꾸지 않는다."""
    result = []
    schemas = {
        'hira_departments': ('departments', 'registered_department'),
        'hira_specialists': ('specialists', 'registered_specialists'),
        'hira_equipment': ('registered_equipment', 'registered_equipment'),
        'hira_facilities': ('facilities', 'registered_facility'),
        'hira_special_care': ('special_care', 'registered_special_care'),
        'hira_designated_specialties': ('designated_specialties', 'registered_designated_specialty'),
        'hira_nursing_grades': ('nursing_grades', 'registered_nursing_grade'),
        'hira_other_personnel': ('other_personnel', 'registered_other_personnel'),
    }
    for endpoint, (field, kind) in schemas.items():
        snapshot = observations.get(endpoint)
        data, _ = _observation(snapshot)
        rows = data.get(field, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = copy.deepcopy(row)
            if endpoint in ('hira_departments', 'hira_specialists'):
                item['source_fields'] = {'code': 'dgsbjtCd', 'name': 'dgsbjtCdNm',
                                         'reported_count': row.get('source_field')}
            elif endpoint == 'hira_equipment':
                item['source_fields'] = {'code': 'oftCd', 'name': 'oftCdNm', 'reported_count': 'oftCnt'}
            elif endpoint in ('hira_special_care', 'hira_designated_specialties'):
                item['source_fields'] = {'code': 'srchCd', 'name': 'srchCdNm'}
                item['source_field'] = 'srchCdNm'
            elif endpoint == 'hira_nursing_grades':
                item['source_fields'] = {'code': 'tyCd', 'name': 'tyCdNm', 'reported_grade': 'careGrd'}
                item['source_field'] = 'careGrd'
            item.update(_provenance(endpoint, snapshot, now, max_age_seconds))
            item.update(kind=kind, evidence_scope='registered_only',
                        current_availability='unknown', patient_acceptance_required=True)
            if kind == 'registered_equipment':
                item['equipment_type'] = None
                item['equipment_candidates'] = []
                item['mapping_conflict'] = False
                for equipment, mapping in HIRA_EQUIPMENT.items():
                    code, name = row.get('code'), row.get('name')
                    # 알려진 코드와 이름이 서로 모순되면 자동 연결하지 않는다.
                    code_match, name_match = code in mapping['codes'], name in mapping['names']
                    code_conflict = bool(code) and not code_match
                    name_conflict = bool(name) and not name_match
                    if code_match or name_match:
                        item['equipment_candidates'].append(equipment)
                        item['mapping_conflict'] = code_conflict or name_conflict
                    if (code_match or name_match) and not code_conflict and not name_conflict:
                        item['equipment_type'] = equipment
                        item['mapping'] = 'verified_exact_code_and_name'
                        break
            result.append(item)
    basis_snapshot = observations.get('hira_hospital_basis')
    basis, _ = _observation(basis_snapshot)
    for field, source_field, label in (
            ('reported_total_doctors', 'drTotCnt', 'total_doctors'),
            ('reported_medical_specialists', 'mdeptSdrCnt', 'medical_specialists')):
        if field in basis:
            result.append({'kind': 'registered_personnel_total', 'name': label,
                           'source_field': source_field, 'reported_count': basis[field],
                           'evidence_scope': 'registered_only', 'current_availability': 'unknown',
                           'patient_acceptance_required': True,
                           **_provenance('hira_hospital_basis', basis_snapshot, now, max_age_seconds)})
    snapshot = observations.get('nmc_hospital_detail')
    detail, _ = _observation(snapshot)
    for name in detail.get('departments', []):
        if isinstance(name, str):
            result.append({'kind': 'registered_department', 'name': name,
                           'source_field': 'dgidIdName', 'evidence_scope': 'registered_only',
                           'current_availability': 'unknown', 'patient_acceptance_required': True,
                           **_provenance('nmc_hospital_detail', snapshot, now, max_age_seconds)})
    for row in detail.get('facilities', []):
        if isinstance(row, dict):
            result.append({**copy.deepcopy(row), 'kind': 'registered_facility',
                           'evidence_scope': 'registered_only', 'current_availability': 'unknown',
                           'patient_acceptance_required': True,
                           **_provenance('nmc_hospital_detail', snapshot, now, max_age_seconds)})
    # 같은 제공처/항목의 모순된 중복 수를 합산하거나 마지막 값으로 덮지 않는다.
    for item in result:
        identity = item.get('code') or item.get('facility_type') or item.get('name')
        peers = [row for row in result if row['endpoint'] == item['endpoint']
                 and row['kind'] == item['kind'] and
                 (row.get('code') or row.get('facility_type') or row.get('name')) == identity]
        counts = {row.get('reported_count') for row in peers if
                  isinstance(row.get('reported_count'), int) and not isinstance(row.get('reported_count'), bool)}
        names = {row.get('name') for row in peers if row.get('name')}
        item['conflicting_registered_records'] = len(counts) > 1 or len(names) > 1
    return result


def _registered_for(inventory, kind, name, alternatives=None):
    if kind == 'department':
        return [row for row in inventory if row['kind'] in
                ('registered_department', 'registered_specialists') and
                row.get('name') in (alternatives or [name])]
    if kind == 'equipment':
        return [row for row in inventory if row['kind'] == 'registered_equipment' and
                (row.get('equipment_type') == name or name in row.get('equipment_candidates', []))]
    if kind == 'resource':
        types = RESOURCE_FACILITY_TYPES.get(name, {name})
        return [row for row in inventory if row['kind'] == 'registered_facility' and
                row.get('facility_type') in types]
    return []


def _current_observation(data, field, kind_field, kind):
    rows = [row for row in data.get(field, []) if isinstance(row, dict) and row.get(kind_field) == kind]
    if not rows:
        return {}, False
    first = rows[0]
    # bool과 int를 같다고 취급하지 않도록 형식까지 비교한다.
    expected = (type(first.get('reported_available')), first.get('reported_available'), first.get('status'))
    conflict = any((type(row.get('reported_available')), row.get('reported_available'), row.get('status'))
                   != expected for row in rows[1:])
    return first, conflict


def _operational_context(observations, now, max_age_seconds):
    """진료시간·수용메시지는 판단에 필요한 원문 문맥이며 수용 확답이 아니다."""
    result = []
    for endpoint, field in (('hira_operating_details', 'operating_details'),
                            ('nmc_acceptance_messages', 'messages')):
        snapshot = observations.get(endpoint)
        data, _ = _observation(snapshot)
        rows = data.get(field, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                result.append({**copy.deepcopy(row),
                               **_provenance(endpoint, snapshot, now, max_age_seconds),
                               'kind': 'acceptance_message' if field == 'messages' else 'operating_details',
                               'interpretation': 'context_only_requires_confirmation',
                               'patient_acceptance_required': True})
    return result


def evaluate_requirements(requirements, detail=None, beds=None, severe=None, *,
                          now=None, max_age_seconds=600, exclude_public_unavailability=False,
                          observations=None):
    """공개 정보만으로 조건을 비교해 3가지 결과와 항목별 근거를 반환한다.

    requirements는 departments 또는 required_capabilities(진료과 이름의 정확한
    일치), equipment(장비 이름 목록), resources 또는 resource_demands(수량 dict),
    severe_fields(원본 코드 필드 목록)를 받는다. extended_resource_demands도
    병합하며, required_services와 human_confirmation 항목은 사람이 확인해야 한다.
    코드·별칭의 의미를 추측하지 않는다.

    observations는 endpoint -> snapshot_view 또는 normalized dict의 매핑이다.
    심평원 등록 인력/장비/시설은 항목별 등록 근거로 연결하고 현재 가용수와
    분리한다. 진료시간과 NMC 수용메시지는 원문 문맥으로 표시한다. 등록 전문의
    수나 수용 메시지의 자유문장으로 현재 팀 가용성·수용 여부를 추정하지 않는다.

    department_evidence.groups[].any_of_exact_names가 있으면 카탈로그가 명시한
    진료과 이름 집합과 정확히 비교한다. 부분 문자열이나 상위 진료과로 추정하지 않는다.

    MATCH는 요청한 공개 관측 항목의 일치일 뿐, 병원의 수용 확답이 아니다.
    최신 공개 관측의 가용수 부족도 기본은 NEEDS_CONFIRMATION이다.
    exclude_public_unavailability=True를 명시한 실험에서만 현재 문의 차수의
    잠정 EXCLUDED(reason=temporary_unavailable)를 허용한다.
    만료되거나 시각을 알 수 없는 동적 가용 관측은 NEEDS_CONFIRMATION이다.
    등록 진료과의 정확한 이름 일치는 등록 근거의 MATCH이며, 등록 갱신 시각의
    불명 여부는 별도 표시하고 현재 의료진 가용성은 항상 unknown으로 남긴다.
    """
    now = time.time() if now is None else now
    if max_age_seconds < 0:
        raise ValueError('max_age_seconds는 0 이상이어야 합니다')
    if not isinstance(exclude_public_unavailability, bool):
        raise TypeError('exclude_public_unavailability는 bool이어야 합니다')
    requirements = requirements or {}
    if not isinstance(requirements, dict):
        raise TypeError('requirements는 dict여야 합니다')
    if observations is not None and not isinstance(observations, dict):
        raise TypeError('observations는 endpoint -> 관측 dict여야 합니다')
    observation_map = dict(observations or {})
    for endpoint, value in (('nmc_hospital_detail', detail), ('nmc_rltm_beds', beds),
                            ('nmc_severe_accept', severe)):
        if value is not None:
            observation_map[endpoint] = value
    detail = observation_map.get('nmc_hospital_detail')
    beds = observation_map.get('nmc_rltm_beds')
    severe = observation_map.get('nmc_severe_accept')
    detail_data, detail_meta = _observation(detail)
    bed_data, bed_meta = _observation(beds)
    severe_data, severe_meta = _observation(severe)
    registered_evidence = _registered_inventory(observation_map, now, max_age_seconds)
    operational_context = _operational_context(observation_map, now, max_age_seconds)
    evidence = []

    def add(kind, name, status, reason, **extra):
        evidence.append({'kind': kind, 'requirement': name, 'status': status,
                         'reason': reason, 'source': 'public', **extra})

    departments = _names(requirements.get('departments', requirements.get('required_capabilities', [])),
                         'departments 또는 required_capabilities')
    department_evidence = requirements.get('department_evidence') or {}
    if not isinstance(department_evidence, dict):
        raise TypeError('department_evidence는 dict여야 합니다')
    if department_evidence.get('match_mode') not in (None, 'all_capabilities_exact_name'):
        raise ValueError('지원하지 않는 진료과 비교 모드입니다')
    groups = department_evidence.get('groups') or []
    if not isinstance(groups, list):
        raise TypeError('department_evidence.groups는 목록이어야 합니다')
    exact_names = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('capability'), str):
            raise ValueError('진료과 비교 그룹에 capability 문자열이 필요합니다')
        capability = group['capability'].strip()
        names = _names(group.get('any_of_exact_names'), 'any_of_exact_names')
        if not capability or not names or capability in exact_names:
            raise ValueError('진료과 비교 그룹의 이름이 없거나 capability가 중복됐습니다')
        exact_names[capability] = names
    if not departments and exact_names:
        departments = list(exact_names)
    for department in departments:
        alternatives = exact_names.get(department, [department])
        registered = _registered_for(registered_evidence, 'department', department, alternatives)
        usable = [row for row in registered if row['kind'] == 'registered_department'
                  and not row['explicitly_stale'] and row['freshness'] != 'stale'
                  and not row['conflicting_registered_records']]
        matches = [name for name in alternatives if any(row.get('name') == name for row in usable)]
        endpoint = next((row['endpoint'] for row in usable if row.get('name') in matches),
                        'nmc_hospital_detail')
        field = 'dgsbjtCdNm' if endpoint == 'hira_departments' else 'dgidIdName'
        department_extra = {'source_field': field, 'endpoint': endpoint,
                            'accepted_exact_names': alternatives,
                            'matched_department_names': matches,
                            'registered_evidence': registered,
                            'current_team_availability': 'unknown',
                            'evidence_scope': 'registered_department_only'}
        if any(row['conflicting_registered_records'] for row in registered):
            add('department', department, NEEDS_CONFIRMATION, 'conflicting_registered_records', **department_extra)
        elif not matches and (detail_meta.get('stale') is True or any(
                row['explicitly_stale'] or row['freshness'] == 'stale' for row in registered)):
            add('department', department, NEEDS_CONFIRMATION, 'stale', **department_extra)
        elif matches:
            add('department', department, MATCH, 'department_reported', **department_extra)
        else:
            add('department', department, NEEDS_CONFIRMATION, 'department_not_confirmed',
                **department_extra)

    freshness = _freshness(bed_data, bed_meta, now, max_age_seconds)
    for equipment in _names(requirements.get('equipment'), 'equipment'):
        observed, conflict = _current_observation(bed_data, 'equipment', 'equipment_type', equipment)
        available = observed.get('reported_available')
        extra = {'reported_available': available, 'source_field': observed.get('source_field'),
                 **_provenance('nmc_rltm_beds', beds, now, max_age_seconds),
                 'registered_evidence': _registered_for(registered_evidence, 'equipment', equipment)}
        if conflict:
            add('equipment', equipment, NEEDS_CONFIRMATION, 'conflicting_public_observations', **extra)
        elif any(row.get('mapping_conflict') or row['conflicting_registered_records'] for row in extra['registered_evidence']):
            add('equipment', equipment, NEEDS_CONFIRMATION, 'conflicting_registered_records', **extra)
        elif freshness != 'fresh':
            add('equipment', equipment, NEEDS_CONFIRMATION,
                'stale' if freshness == 'stale' else 'observation_time_unknown', **extra)
        elif available is None or not isinstance(available, bool) or observed.get('status') != 'reported':
            add('equipment', equipment, NEEDS_CONFIRMATION, 'equipment_availability_unknown', **extra)
        elif available:
            add('equipment', equipment, MATCH, 'public_availability_reported', **extra)
        else:
            status = EXCLUDED if exclude_public_unavailability else NEEDS_CONFIRMATION
            add('equipment', equipment, status, 'temporary_unavailable',
                exclusion_scope='current_inquiry_round' if exclude_public_unavailability else None,
                **extra)

    resources = {}
    for field in ('resource_demands', 'resources', 'extended_resource_demands'):
        demands = requirements.get(field) or {}
        if not isinstance(demands, dict):
            raise TypeError(field + '는 수량 dict여야 합니다')
        for resource_type, quantity in demands.items():
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
                raise ValueError('필요 자원 수량은 0 이상의 정수여야 합니다')
            resources[resource_type] = max(resources.get(resource_type, 0), quantity)
    for resource_type, quantity in resources.items():
        if quantity == 0:
            continue
        observed, conflict = _current_observation(bed_data, 'resources', 'resource_type', resource_type)
        available = observed.get('reported_available')
        registered = _registered_for(registered_evidence, 'resource', resource_type)
        if resource_type == 'special_team':
            # 등록 전문의 수는 해당 프로필에 관련된 참고 근거이며 현재 팀 수가 아니다.
            related_names = {name for department in departments for name in
                             exact_names.get(department, [department])}
            registered = [row for row in registered_evidence if
                          row['kind'] == 'registered_specialists' and row.get('name') in related_names]
        extra = {'required_quantity': quantity, 'reported_available': available,
                 'source_field': observed.get('source_field'),
                 **_provenance('nmc_rltm_beds', beds, now, max_age_seconds),
                 'registered_evidence': registered,
                 'registered_counts_are_current_capacity': False}
        if conflict:
            add('resource', resource_type, NEEDS_CONFIRMATION, 'conflicting_public_observations', **extra)
        elif freshness != 'fresh':
            add('resource', resource_type, NEEDS_CONFIRMATION,
                'stale' if freshness == 'stale' else 'observation_time_unknown', **extra)
        elif (not isinstance(available, int) or isinstance(available, bool) or
              available < 0 or observed.get('status') != 'reported'):
            add('resource', resource_type, NEEDS_CONFIRMATION, 'available_quantity_unknown', **extra)
        elif available < quantity:
            status = EXCLUDED if exclude_public_unavailability else NEEDS_CONFIRMATION
            add('resource', resource_type, status, 'temporary_unavailable',
                exclusion_scope='current_inquiry_round' if exclude_public_unavailability else None,
                **extra)
        else:
            add('resource', resource_type, MATCH, 'public_availability_reported', **extra)

    required_services = _names(requirements.get('required_services'), 'required_services')
    service_availability_evidence = []
    if resources.get('delivery_room', 0) > 0 or 'delivery_room' in required_services:
        # hv42의 서비스 Y/N은 분만실 슬롯 수량을 대신하지 않는 별도의 관측 근거다.
        observed = next((row for row in bed_data.get('service_availability', [])
                         if isinstance(row, dict) and row.get('service_type') == 'delivery_room'
                         and row.get('source_field') == 'hv42'), {})
        available = observed.get('reported_available')
        status, reason = NEEDS_CONFIRMATION, 'service_availability_unknown'
        if freshness != 'fresh':
            reason = 'stale' if freshness == 'stale' else 'observation_time_unknown'
        elif isinstance(available, bool) and observed.get('status') == 'reported':
            if available:
                status, reason = MATCH, 'public_service_availability_reported'
            else:
                reason = 'temporary_unavailable'
                if exclude_public_unavailability:
                    status = EXCLUDED
        service_availability_evidence.append({
            'kind': 'service_availability', 'requirement': 'delivery_room',
            'status': status, 'reason': reason, 'source': 'public', 'source_field': 'hv42',
            'reported_available': available, 'freshness': freshness,
            **_provenance('nmc_rltm_beds', beds, now, max_age_seconds),
            'registered_evidence': _registered_for(registered_evidence, 'resource', 'delivery_room'),
            'exclusion_scope': 'current_inquiry_round' if status == EXCLUDED else None,
            'interpretation': 'public_service_flag_not_quantity',
            'patient_acceptance_required': True,
        })

    for field in _names(requirements.get('severe_fields'), 'severe_fields'):
        observed = next((r for r in severe_data.get('fields', [])
                         if r.get('source_field') == field), None)
        add('severe', field, NEEDS_CONFIRMATION, 'severe_code_version_unverified',
            source_field=field,
            **_provenance('nmc_severe_accept', severe, now, max_age_seconds),
            reported_value=observed.get('reported_value') if observed else None)

    for service in required_services:
        if service == 'delivery_room':
            # 정확히 지정된 서비스만 비교하며 고위험 분만·산과수술로 확대 해석하지 않는다.
            evidence.append(dict(service_availability_evidence[0], kind='service'))
        else:
            add('service', service, NEEDS_CONFIRMATION, 'patient_specific_service_confirmation_required',
                registered_evidence=[row for row in registered_evidence if row['kind'] in
                                     ('registered_special_care', 'registered_designated_specialty')
                                     and row.get('name') == service])
    human_confirmation = requirements.get('human_confirmation',
                                          requirements.get('human_confirmation_required'))
    human_items = ['clinical_acceptance'] if human_confirmation is True else (
        [] if human_confirmation is False else _names(human_confirmation, 'human_confirmation'))
    for item in human_items:
        add('human_confirmation', item, NEEDS_CONFIRMATION, 'human_confirmation_required')

    statuses = {row['status'] for row in evidence}
    status = EXCLUDED if EXCLUDED in statuses else (
        NEEDS_CONFIRMATION if NEEDS_CONFIRMATION in statuses or not evidence else MATCH)
    return {'status': status, 'evidence': evidence,
            'registered_evidence': registered_evidence,
            'current_evidence': [copy.deepcopy(row) for row in evidence if
                                 row['kind'] in ('resource', 'equipment', 'severe', 'service')],
            'operational_context': operational_context,
            'service_availability_evidence': service_availability_evidence,
            'unknown': [row for row in evidence if row['status'] == NEEDS_CONFIRMATION],
            'excluded': [row for row in evidence if row['status'] == EXCLUDED],
            'stale': any(row['reason'] == 'stale' for row in evidence),
            'patient_acceptance_required': True,
            'note': '공개 관측 비교 결과이며 환자별 수용 확답·운영 자원 확보를 대신하지 않습니다'}
