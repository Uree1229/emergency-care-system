"""화면 연결용 추천 응답. 계산 결과 신선도와 워커 가동 상태를 분리한다."""
from . import planning, service


def availability(runtime=None):
    state = runtime.snapshot()['workers']['planning']['state'] if runtime is not None else 'UNKNOWN'
    reasons = {'DISABLED': 'PLANNER_DISABLED', 'STOPPED': 'PLANNER_STOPPED', 'STOPPING': 'PLANNER_STOPPING',
               'ERROR': 'PLANNER_ERROR', 'STALLED': 'PLANNER_STALLED', 'UNKNOWN': 'PLANNER_UNOBSERVED'}
    accepting = None if state == 'UNKNOWN' else state not in ('DISABLED', 'STOPPED', 'STOPPING')
    return dict(worker_state=state, accepting_requests=accepting, reason_code=reasons.get(state))


def patient_result(conn, now, pid, runtime=None):
    return dict(planning.patient_result(conn, now, pid), availability=availability(runtime))


def hospital_results(conn, now, hid, runtime=None, observed=None):
    observed = availability(runtime) if observed is None else observed
    return [dict(result, availability=dict(observed)) for result in planning.hospital_results(conn, now, hid)]


def request(conn, now, pid, runtime=None):
    patient = service._patient(conn, pid)
    service._require(patient['state'] in ('WAITING',) + service.ACTIVE_TRANSPORT,
                     'INVALID_STATE', '도착 전 환자만 재계산할 수 있습니다', 409)
    observed = availability(runtime)
    service._require(observed['accepting_requests'] is not False, observed['reason_code'],
                     '현재 서버의 배정 계산이 비활성 또는 중단 상태입니다. 서버 담당자가 실행 상태를 확인해야 합니다.',
                     409, availability=observed)
    return dict(planning.request(conn, now, pid), availability=observed)
