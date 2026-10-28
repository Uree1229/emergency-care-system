"""V3를 보존한 저부하·관측 부족 문의 후보 V4.

운영 레지스트리에 등록하지 않은 합성 실험용 순수 계산이다. 기존 V3의
순위·최초 문의·45초 대기 상한을 그대로 호출하고, 다른 환자의 미해결
문의가 없을 때만 관측이 부족한 가까운 대안을 30초부터 한 곳 보완한다.
이는 수용 확률 또는 병원 회복 시각의 예측이 아니며 실제 문의를 취소하거나
회신을 만들어 내지 않는다. 호출자는 이미 문의한 병원을 후보에서 제외하고
유효한 수용 응답이 있으면 이 함수를 호출하기 전에 문의를 중단해야 한다.
"""
from copy import deepcopy

from . import inquiry_policies


POLICY_NAME = 'observed_v4'
BASE_POLICY = 'v3_w45_h900'
SETTINGS = {
    'cold_start_wait_seconds': 30,
    'cold_start_travel_margin_seconds': 120,
    'max_other_patient_pending': 0,
    'max_candidate_pending': 0,
    'reply_samples_below': 2,
}


def spec():
    """고정 후보의 독립 명세. 기존 V3 선택 파일이나 레지스트리를 바꾸지 않는다."""
    policy = inquiry_policies.spec(BASE_POLICY)
    policy.update(name=POLICY_NAME, label='저부하 관측 보완 V4', version='1',
                  research_only=True, source_policy=BASE_POLICY)
    policy['config'].update(deepcopy(SETTINGS))
    return policy


def decide(*, now, candidates, observations=(), pending=(), patient_pending=(),
           max_new, last_contact_at=None):
    """V3와 동일한 초 단위 입력·출력 계약으로 추가 문의를 제안한다.

    기존 V3가 문의를 제안하면 그대로 유지한다. V3가 보류한 경우에는 가장
    낮은 V3 비용의 후보를 유지한 채 다음 조건을 모두 확인한다: 최근 전송
    후 30초 이상, 대기 선택지 1~2곳, 타환자의 미해결 문의 0건, 후보 병원의
    미해결 문의 0건, 후보와 대기 병원 각각 최근 유효 회신 표본 2건 미만,
    후보 이동시간이 현재 최단 대기 병원의 이동시간보다 120초 이내로 길다.
    전체 문의 한도와 동시 대기 한도는 원래 V3의 제한을 그대로 적용한다.

    입력은 변경하지 않고 원래 V3의 검증·미래 행 제외·중복 제거를 재사용한다.
    evidence에는 환자 ID, 환자 본문, 환경의 예정 회신 시각을 남기지 않는다.
    """
    # Generator를 포함한 같은 입력을 V3와 현재 원장 재검사에 한 번씩 사용한다.
    candidate_rows, observation_rows = list(candidates), list(observations)
    pending_rows, own_rows = list(pending), list(patient_pending)
    output = inquiry_policies.decide(
        BASE_POLICY, now=now, candidates=candidate_rows,
        observations=observation_rows, pending=pending_rows,
        patient_pending=own_rows, max_new=max_new,
        last_contact_at=last_contact_at)
    evidence = output['evidence']
    own = inquiry_policies._pending(own_rows, now, own=True)
    other_count = evidence['unresolved_inquiry_count'] - len(own)
    candidate_scores = evidence['candidate_scores']
    waiting_scores = evidence['pending_scores']
    hospital_stats = {row['hospital']: row for row in evidence['hospitals']}
    best = candidate_scores[0] if candidate_scores else None
    best_stats = hospital_stats[best['hospital']] if best else None
    shortest_waiting_travel = min(
        (row['travel_minutes'] * 60 for row in waiting_scores), default=None)
    extra_travel = (best['travel_minutes'] * 60 - shortest_waiting_travel
                    if best and shortest_waiting_travel is not None else None)
    latest_age = evidence['last_contact_age_seconds']
    waiting_samples = [hospital_stats[row['hospital']]['reply_samples']
                       for row in waiting_scores]
    checks = {
        'base_waiting': not output['send'],
        'contact_budget': max_new > 0,
        'candidate_available': best is not None,
        'waiting_within_cap': 0 < len(waiting_scores) < evidence['max_k'],
        'contact_age_due': (latest_age is not None
                            and latest_age >= SETTINGS['cold_start_wait_seconds']),
        'no_other_patient_pending': other_count <= SETTINGS['max_other_patient_pending'],
        'candidate_unloaded': (best_stats is not None and best_stats['unresolved_inquiries']
                               <= SETTINGS['max_candidate_pending']),
        'candidate_reply_samples_low': (best_stats is not None and best_stats['reply_samples']
                                        < SETTINGS['reply_samples_below']),
        'waiting_reply_samples_low': bool(waiting_samples) and all(
            count < SETTINGS['reply_samples_below'] for count in waiting_samples),
        'travel_margin': (extra_travel is not None and extra_travel
                          <= SETTINGS['cold_start_travel_margin_seconds']),
    }
    supplement = all(checks.values())
    evidence.update(policy=POLICY_NAME, base_policy=BASE_POLICY,
                    cold_start_supplement=dict(
                        settings=deepcopy(SETTINGS), checks=checks,
                        other_patient_pending_count=other_count,
                        candidate=best['hospital'] if best else None,
                        candidate_unresolved_inquiries=(best_stats['unresolved_inquiries']
                                                       if best_stats else None),
                        candidate_reply_samples=(best_stats['reply_samples']
                                                 if best_stats else None),
                        waiting_reply_samples=waiting_samples,
                        additional_travel_seconds=extra_travel,
                        applied=supplement))
    if supplement:
        evidence['base_reason'] = output['reason']
        output['send'] = [best['hospital']]
        output['reason'] = 'cold_start_low_load_expansion'
        evidence['sent_hospitals'] = list(output['send'])
    return output
