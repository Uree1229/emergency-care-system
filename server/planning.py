"""배정 작업의 스냅샷·버전 검증·결과 조회. 추천은 목적지 확정과 별개다."""
from . import db, service


def capture(conn, now):
    # 호출자가 트랜잭션을 연 상태에서 읽는다. 지도 요청과 솔버는 이후에 실행한다.
    return dict(now=now, revision=conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0],
                hospitals=db.rows(conn.execute('SELECT * FROM hospitals ORDER BY id')),
                resources=db.rows(conn.execute('SELECT * FROM resources')),
                restrictions=db.rows(conn.execute('SELECT * FROM restrictions')),
                inquiries=db.rows(conn.execute('SELECT * FROM inquiries')),
                public_snapshots=[dict(row) for row in conn.execute('SELECT * FROM public_api_snapshots')],
                patients=db.rows(conn.execute("SELECT * FROM patients WHERE state IN ('WAITING','CONFIRMED','IN_TRANSIT')")))


def publish(conn, snapshot, results, now, owner):
    # 미확인 자원 정보의 유효시간과 수용 응답 만료시각도 결과 유효기간에 반영한다.
    limits = [snapshot['now'] + 30]
    limits += [q['expires_at'] for q in snapshot['inquiries']
               if q['state'] == 'ACCEPTED' and q['expires_at'] is not None and q['expires_at'] > snapshot['now']]
    limits += [r['updated_at'] + service.STALE_AFTER + 1 for r in snapshot['resources']
               if r['updated_at'] + service.STALE_AFTER >= snapshot['now']]
    limits += [r['starts_at'] for r in snapshot['restrictions']
               if service.restriction_status(r, snapshot['now']) == 'scheduled']
    limits += [r['source_observed_at'] + service.STALE_AFTER + 1 for r in snapshot.get('public_snapshots', [])
               if r['source_observed_at'] is not None and
               r['source_observed_at'] + service.STALE_AFTER >= snapshot['now']]
    expires = min(limits)
    with db.transaction(conn):
        state = conn.execute('SELECT * FROM planning_state WHERE id=1').fetchone()
        if state['lease_owner'] != owner:
            return False
        if state['revision'] != snapshot['revision'] or now >= expires:
            conn.execute('UPDATE planning_state SET lease_until=0, lease_owner=NULL WHERE id=1')
            return False
        conn.execute('DELETE FROM planning_results')
        for pid, data in results.items():
            conn.execute('INSERT INTO planning_results VALUES(?,?,?,?,?)',
                         (pid, snapshot['revision'], now, expires, db.dumps(data)))
        conn.execute('UPDATE planning_state SET completed_revision=?, lease_until=0, lease_owner=NULL, last_error=NULL WHERE id=1',
                     (snapshot['revision'],))
    return True


def patient_result(conn, now, pid):
    p = service._patient(conn, pid)
    if p['state'] not in ('WAITING',) + service.ACTIVE_TRANSPORT:
        return dict(status='NOT_APPLICABLE', patient_state=p['state'], result=None)
    state = dict(conn.execute('SELECT * FROM planning_state WHERE id=1').fetchone())
    r = db.row(conn.execute('SELECT * FROM planning_results WHERE patient_id=?', (pid,)).fetchone())
    if r is None:
        return dict(status='ERROR' if state['last_error'] else 'QUEUED', revision=state['revision'], result=None)
    if r['revision'] != state['revision'] or now >= r['expires_at']:
        return dict(status='STALE', revision=state['revision'], result=None)
    return dict(status='READY', revision=r['revision'], created_at=r['created_at'], expires_at=r['expires_at'], result=r['data'])


def hospital_results(conn, now, hid):
    service._hospital(conn, hid)
    return [dict(patient_id=p['id'], **patient_result(conn, now, p['id'])) for p in conn.execute(
        "SELECT id FROM patients WHERE destination_hospital_id=? AND state IN ('CONFIRMED','IN_TRANSIT')", (hid,))]


def request(conn, now, pid):
    p = service._patient(conn, pid)
    service._require(p['state'] in ('WAITING',) + service.ACTIVE_TRANSPORT, 'INVALID_STATE',
                     '도착 전 환자만 재계산할 수 있습니다', 409)
    with db.transaction(conn):
        conn.execute('UPDATE planning_state SET revision=revision+1 WHERE id=1')
    return patient_result(conn, now, pid)
