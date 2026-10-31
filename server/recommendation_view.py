"""화면 연결용 추천 응답. 계산 결과 신선도와 워커 가동 상태를 분리한다."""
from contextlib import contextmanager
import uuid

from . import planning, service, allocation_input_review


@contextmanager
def _read_snapshot(conn):
    """One deferred read snapshot; do not acquire an IMMEDIATE write lock."""
    name = 'allocation_input_view_' + uuid.uuid4().hex
    conn.execute('SAVEPOINT ' + name)
    try:
        yield
    except BaseException:
        conn.execute('ROLLBACK TO ' + name)
        raise
    finally:
        conn.execute('RELEASE ' + name)


def patient_input_review(conn, now, pid):
    with _read_snapshot(conn):
        patient = service._patient(conn, pid)
        return dict(patient_id=pid, patient_version=patient['version'], patient_state=patient['state'],
                    input_review=allocation_input_review.assess(patient, now))


def candidates(conn, now, pid, estimator):
    with _read_snapshot(conn):
        result = service.candidates(conn, now, pid, estimator)
        patient = service._patient(conn, pid)
        return dict(result, input_review=allocation_input_review.assess(patient, now))


def availability(runtime=None):
    state = runtime.snapshot()['workers']['planning']['state'] if runtime is not None else 'UNKNOWN'
    reasons = {'DISABLED': 'PLANNER_DISABLED', 'STOPPED': 'PLANNER_STOPPED', 'STOPPING': 'PLANNER_STOPPING',
               'ERROR': 'PLANNER_ERROR', 'STALLED': 'PLANNER_STALLED', 'UNKNOWN': 'PLANNER_UNOBSERVED'}
    accepting = None if state == 'UNKNOWN' else state not in ('DISABLED', 'STOPPED', 'STOPPING')
    return dict(worker_state=state, accepting_requests=accepting, reason_code=reasons.get(state))


def patient_result(conn, now, pid, runtime=None):
    with _read_snapshot(conn):
        result = planning.patient_result(conn, now, pid)
        patient = service._patient(conn, pid)
        # Top-level is current input guidance, including when no fresh plan is
        # available. The saved worker result stays an unmodified snapshot.
        return dict(result, input_review=allocation_input_review.assess(patient, now),
                    availability=availability(runtime))


def hospital_results(conn, now, hid, runtime=None, observed=None):
    observed = availability(runtime) if observed is None else observed
    with _read_snapshot(conn):
        return [dict(result, availability=dict(observed),
                     input_review=allocation_input_review.assess(service._patient(conn, result['patient_id']), now))
                for result in planning.hospital_results(conn, now, hid)]


def request(conn, now, pid, runtime=None):
    patient = service._patient(conn, pid)
    service._require(patient['state'] in ('WAITING',) + service.ACTIVE_TRANSPORT,
                     'INVALID_STATE', '도착 전 환자만 재계산할 수 있습니다', 409)
    observed = availability(runtime)
    service._require(observed['accepting_requests'] is not False, observed['reason_code'],
                     '현재 서버의 배정 계산이 비활성 또는 중단 상태입니다. 서버 담당자가 실행 상태를 확인해야 합니다.',
                     409, availability=observed)
    planning.request(conn, now, pid)
    return patient_result(conn, now, pid, runtime)
