"""명시된 환자 치료조건과 공개 관측을 서비스·배정 모델에 연결한다.

프로필은 증상으로 추정하지 않는다. 등록 수량을 운영 자원으로 바꾸거나
공개 정보만으로 환자 수용을 확정하지 않는다.
"""
import copy
import json
from functools import lru_cache
from pathlib import Path

from .treatment_data import evaluate_requirements

CATALOG_PATH = Path(__file__).resolve().parents[1] / 'config' / 'treatment_requirements.json'


@lru_cache(maxsize=4)
def _catalog(path, modified_ns):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def catalog():
    return copy.deepcopy(_catalog(str(CATALOG_PATH), CATALOG_PATH.stat().st_mtime_ns))


def profile(profile_id):
    if not isinstance(profile_id, str) or not profile_id:
        raise ValueError('treatment_profile_id는 유효한 프로필 ID 문자열이어야 합니다')
    selected = next((p for p in catalog()['profiles'] if p['id'] == profile_id), None)
    if selected is None:
        raise ValueError('등록되지 않은 treatment_profile_id입니다')
    return selected


def requirements_for(patient):
    """카탈로그와 명시 요구의 합집합. 같은 자원은 큰 필요량을 유지한다."""
    extra = patient.get('extra') or {}
    profile_id = extra.get('treatment_profile_id') if isinstance(extra, dict) else None
    selected = profile(profile_id) if profile_id is not None else {}
    explicit_capabilities = patient.get('explicit_required_capabilities')
    if explicit_capabilities is None:
        explicit_capabilities = patient.get('required_capabilities') or []
    explicit_demands = patient.get('explicit_resource_demands')
    if explicit_demands is None:
        explicit_demands = patient.get('resource_demands') or {'ed_bed':1}
    result = copy.deepcopy(selected)
    result['required_capabilities'] = list(dict.fromkeys(
        list(selected.get('required_capabilities', [])) + list(explicit_capabilities)))
    demands = {}
    for values in (selected.get('resource_demands', {}), selected.get('extended_resource_demands', {}),
                   explicit_demands):
        for key, value in values.items():
            demands[key] = max(demands.get(key, 0), value)
    result['resource_demands'] = demands
    result['profile_id'] = profile_id
    result['requirement_source'] = 'explicit_profile' if selected else 'explicit_patient_fields'
    result['explicit_required_capabilities'] = copy.deepcopy(explicit_capabilities)
    result['explicit_resource_demands'] = copy.deepcopy(explicit_demands)
    # 사용자 지정 진료 분야도 정확한 이름으로 검사하며 프로필 밖 조건을 버리지 않는다.
    evidence = result.setdefault('department_evidence', {'match_mode':'all_capabilities_exact_name','groups':[]})
    grouped = {g['capability'] for g in evidence.get('groups', [])}
    exact_names = {row['id']:row['registered_department_names']
                   for row in catalog().get('capability_dictionary', [])}
    for capability in result['required_capabilities']:
        if capability not in grouped:
            evidence.setdefault('groups', []).append(
                {'capability':capability,'any_of_exact_names':exact_names.get(capability,[capability])})
    if not selected:
        result['human_confirmation'] = ['환자별 치료조건과 수용 가능 여부']
    return result


def bind_patient(patient):
    result = copy.deepcopy(patient)
    requirements = requirements_for(patient)
    result['required_capabilities'] = requirements['required_capabilities']
    result['resource_demands'] = requirements['resource_demands']
    return result


def observation_map(snapshots, now, hospital_id=None):
    """snapshot_view 또는 동일 DB 트랜잭션에서 읽은 원시 행을 판정 입력으로 만든다."""
    result = {}
    for value in snapshots or []:
        if hospital_id is not None and value.get('hospital_id') != hospital_id:
            continue
        snapshot = copy.deepcopy(value)
        normalized = snapshot.get('data', snapshot.get('normalized', {}))
        if isinstance(normalized, str):
            normalized = json.loads(normalized)
        snapshot['data'] = normalized
        stamp = snapshot.get('source_observed_at')
        snapshot.setdefault('stale', None if stamp is None else now - stamp > 600)
        snapshot.setdefault('observation_time_unknown', stamp is None)
        result[snapshot['endpoint']] = snapshot
    return result


def evaluate_patient(patient, observations, now):
    requirements = requirements_for(patient)
    result = evaluate_requirements(requirements, observations=observations, now=now)
    result.update(profile_id=requirements['profile_id'],
                  profile_label=requirements.get('label'),
                  requirement_source=requirements['requirement_source'])
    return _requested_condition_evidence(result)


def _requested_condition_evidence(evaluation):
    """API에서는 요청 조건과 관련된 등록 근거만 모아 중복 전체 재고를 줄인다.

    전체 원문·등록 재고는 public_data와 연구용 evaluator 결과에 유지한다.
    개별 evidence의 근거, 출처·시각·충돌 정보는 그대로 보존한다.
    """
    available = evaluation.get('registered_evidence', [])
    relevant, seen = [], set()
    for item in evaluation.get('evidence', []) + evaluation.get('service_availability_evidence', []):
        for row in item.get('registered_evidence', []):
            identity = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if identity not in seen:
                relevant.append(row)
                seen.add(identity)
    evaluation['available_registered_evidence_count'] = len(available)
    evaluation['registered_evidence'] = relevant
    evaluation['registered_evidence_scope'] = 'requested_conditions'
    return evaluation


def evaluate_profiles(observations, now):
    return {p['id']:_requested_condition_evidence({
                     **evaluate_requirements(p, observations=observations, now=now),
                     'profile_id':p['id'], 'profile_label':p['label']})
            for p in catalog()['profiles']}


def confirmation_checks(evaluation):
    return list(dict.fromkeys('치료조건 확인 필요: ' + str(row['requirement'])
                              for row in evaluation['unknown']))
