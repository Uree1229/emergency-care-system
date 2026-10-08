"""문의부터 도착까지의 업무 규칙 (S03, S05, S07, S08).

설계 기준
- 알고리즘·후보 목록은 제안일 뿐이다. 목적지 확정은 해당 환자 정보 버전에 대한
  유효한 ACCEPTED 응답이 있는 병원 한 곳만 가능하다.
- 여러 병원의 수용 응답은 자원을 점유하지 않는다. 확정한 한 곳만 도착 예정 수요가 된다.
- 미응답은 거절이 아니며 확정에도 쓰지 않는다. 미확인 중증도는 낮게 처리하지 않는다.
- 이동 중 목적지는 유지한다. 변경은 새 병원의 수용 확인 뒤에만 가능하다.
모든 쓰기는 BEGIN IMMEDIATE 트랜잭션 안에서 검사와 반영을 함께 한다.
"""
import math
from datetime import date

from .db import dumps, row, rows, transaction

ACCEPT_TTL = 600        # 수용 응답 유효시간(초). 실험 설정값
PENDING_WARN = 60       # 미응답 강조 기준(초)
RETRY_MIN = 120         # 재문의 최소 간격(초)
STALE_AFTER = 600       # 자원 정보가 오래됐다고 표시하는 기준(초)

REPORTER_ROLES = {'self', 'guardian', 'transporter'}
PRIORITIES = {4, 2, 1}
PRIORITY_SOURCES = {'self', 'expert', 'unknown'}
DECLINE_REASONS = {'인력', '장비', '병상', '진료 불가', '기타'}
OPEN_INQUIRY = ('PENDING', 'CHECKING')
ACTIVE_TRANSPORT = ('CONFIRMED', 'IN_TRANSIT')
CANCEL_REASONS = {'다른 병원 확정', '증상 호전', '119 이송', '기타'}


class ServiceError(Exception):
    def __init__(self, code, message, status=409, **details):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details

    def to_dict(self):
        return dict(error=self.code, message=self.message, **self.details)


def _require(cond, code, message, status=400, **details):
    if not cond:
        raise ServiceError(code, message, status, **details)


def log(conn, now, actor, type_, patient_id=None, hospital_id=None, **data):
    conn.execute('INSERT INTO events (at, actor, patient_id, hospital_id, type, data) VALUES (?,?,?,?,?,?)',
                 (now, actor, patient_id, hospital_id, type_, dumps(data)))


def expire_acceptances(conn, now):
    """만료된 수용 응답은 확정에 쓰지 못하게 EXPIRED로 바꾼다."""
    for r in conn.execute("SELECT id, patient_id, hospital_id FROM inquiries "
                          "WHERE state='ACCEPTED' AND expires_at<=?", (now,)).fetchall():
        conn.execute("UPDATE inquiries SET state='EXPIRED', version=version+1 WHERE id=?", (r['id'],))
        log(conn, now, 'system', 'inquiry_expired', r['patient_id'], r['hospital_id'], inquiry_id=r['id'])


# ---------------------------------------------------------------- 조회 도우미

def _hospital(conn, hid):
    h = row(conn.execute('SELECT * FROM hospitals WHERE id=?', (hid,)).fetchone())
    _require(h, 'NOT_FOUND', f'병원 {hid} 없음', 404)
    return h


def _patient(conn, pid):
    p = row(conn.execute('SELECT * FROM patients WHERE id=?', (pid,)).fetchone())
    _require(p, 'NOT_FOUND', f'환자 {pid} 없음', 404)
    return p


def _inquiry(conn, iid):
    q = row(conn.execute('SELECT * FROM inquiries WHERE id=?', (iid,)).fetchone())
    _require(q, 'NOT_FOUND', f'문의 {iid} 없음', 404)
    return q


def effective_priority(p):
    """전문가 평가만 그대로 쓰고, 자기신고·미확인은 높음으로 다룬다 (A02/A19)."""
    return p['priority'] if p['priority_source'] == 'expert' and p['priority'] in PRIORITIES else 4


def restriction_status(r, now):
    if r['cleared_at'] is not None:
        return 'cleared'
    if r['ends_at'] is not None and now >= r['ends_at']:
        return 'recheck'   # 종료 예정이 지나도 자동 복구하지 않는다
    return 'active' if now >= r['starts_at'] else 'scheduled'


def restrictions(conn, hid, now):
    out = []
    for r in rows(conn.execute('SELECT * FROM restrictions WHERE hospital_id=? AND cleared_at IS NULL '
                               'ORDER BY id', (hid,))):
        r['status'] = restriction_status(r, now)
        out.append(r)
    return out


def blocking_restriction(conn, hid, capabilities, now):
    for r in restrictions(conn, hid, now):
        if r['status'] == 'active' and (r['scope'] == 'all' or r['scope'] in capabilities):
            return r
    return None


def expected_demand(conn, hid, exclude_patient=None):
    """확정된 도착 예정 수요 (S08). 수용 응답만 받은 문의는 포함하지 않는다."""
    total = {}
    for p in rows(conn.execute(f"SELECT id, resource_demands FROM patients WHERE destination_hospital_id=? "
                               f"AND state IN {ACTIVE_TRANSPORT}", (hid,))):
        if p['id'] == exclude_patient:
            continue
        for k, v in p['resource_demands'].items():
            total[k] = total.get(k, 0) + v
    return total


def resource_view(conn, hid, now):
    expected = expected_demand(conn, hid)
    out = []
    for r in rows(conn.execute('SELECT * FROM resources WHERE hospital_id=? ORDER BY resource_type', (hid,))):
        known = r['status'] != 'unknown'
        r['expected'] = expected.get(r['resource_type'], 0)
        r['free'] = r['capacity'] - r['occupied'] - r['expected'] if known else None
        r['over_capacity'] = known and r['occupied'] > r['capacity']
        r['stale'] = now - r['updated_at'] > STALE_AFTER
        r['age_seconds'] = now - r['updated_at']
        out.append(r)
    return out


def shortfalls(conn, hid, demands, exclude_patient=None):
    """확정 직전 자원 재확인 (H23). 상태를 모르는 자원은 막지 않고 확인 필요로 돌려준다."""
    expected = expected_demand(conn, hid, exclude_patient)
    res = {r['resource_type']: r for r in rows(conn.execute('SELECT * FROM resources WHERE hospital_id=?', (hid,)))}
    short, unknown = [], []
    for k, need in demands.items():
        r = res.get(k)
        if r is None or r['status'] == 'unknown':
            unknown.append(k)
            continue
        free = r['capacity'] - r['occupied'] - expected.get(k, 0)
        if free < need:
            short.append(dict(resource_type=k, free=free, need=need))
    return short, unknown


def patient_summary(p):
    """병원에 보여줄 환자 정보. 출처(자기신고/전문가/미확인)를 함께 표시한다 (H18)."""
    return dict(id=p['id'], age=p['age'], symptoms=p['symptoms'], onset_at=p['onset_at'], extra=p['extra'],
                danger_signs=p['danger_signs'], priority=p['priority'], priority_source=p['priority_source'],
                effective_priority=effective_priority(p), required_capabilities=p['required_capabilities'],
                resource_demands=p['resource_demands'], reporter_role=p['reporter_role'],
                contact=p['contact'], state=p['state'], eta_at=p['eta_at'], version=p['version'],
                **{k: p.get(k) for k in ('name', 'birth', 'address', 'guardian', 'guardian_phone',
                                        'consciousness', 'breathing')})


# ---------------------------------------------------------------- 병원 정보

def upsert_hospital(conn, h):
    conn.execute('INSERT OR REPLACE INTO hospitals (id, name, address, phone, lat, lon, capabilities, source, '
                 'source_ids) VALUES (?,?,?,?,?,?,?,?,?)',
                 (h['id'], h['name'], h.get('address'), h.get('phone'), h['lat'], h['lon'],
                  dumps(h.get('capabilities', [])), h['source'], dumps(h.get('source_ids', {}))))


def set_resource(conn, now, hid, rtype, capacity, occupied, status='available', source='hospital_input',
                 actor='hospital', expected_version=None, source_observed_at=None):
    _require(type(capacity) is int and capacity >= 0, 'INVALID', 'capacity는 0 이상의 정수')
    _require(type(occupied) is int and occupied >= 0, 'INVALID', 'occupied는 0 이상의 정수')
    _require(status in ('available', 'limited', 'unknown'), 'INVALID', 'status 값 오류')
    with transaction(conn):
        _hospital(conn, hid)
        cur = conn.execute('SELECT version FROM resources WHERE hospital_id=? AND resource_type=?',
                           (hid, rtype)).fetchone()
        if expected_version is not None and (cur is None or cur['version'] != expected_version):
            raise ServiceError('CONFLICT', '다른 담당자가 먼저 자원 정보를 바꿨습니다. 새로고침 후 다시 입력하세요.')
        version = (cur['version'] + 1) if cur else 1
        conn.execute('INSERT OR REPLACE INTO resources (hospital_id, resource_type, capacity, occupied, status, '
                     'source, source_observed_at, updated_at, version) VALUES (?,?,?,?,?,?,?,?,?)',
                     (hid, rtype, capacity, occupied, status, source, source_observed_at, now, version))
        log(conn, now, actor, 'resource_updated', hospital_id=hid, resource_type=rtype,
            capacity=capacity, occupied=occupied, status=status)
    return hospital_view(conn, hid, now)


def add_restriction(conn, now, hid, scope, reason, ends_at=None, starts_at=None, actor='hospital'):
    _require(scope and reason, 'INVALID', '제한 범위와 사유가 필요합니다')
    with transaction(conn):
        _hospital(conn, hid)
        cur = conn.execute('INSERT INTO restrictions (hospital_id, scope, reason, starts_at, ends_at) '
                           'VALUES (?,?,?,?,?)', (hid, scope, reason, starts_at or now, ends_at))
        log(conn, now, actor, 'restriction_added', hospital_id=hid, restriction_id=cur.lastrowid,
            scope=scope, reason=reason, ends_at=ends_at)
    return hospital_view(conn, hid, now)


def clear_restriction(conn, now, hid, rid, actor='hospital'):
    with transaction(conn):
        n = conn.execute('UPDATE restrictions SET cleared_at=? WHERE id=? AND hospital_id=? AND cleared_at IS NULL',
                         (now, rid, hid)).rowcount
        _require(n == 1, 'NOT_FOUND', '해제할 제한이 없습니다', 404)
        log(conn, now, actor, 'restriction_cleared', hospital_id=hid, restriction_id=rid)
    return hospital_view(conn, hid, now)


def hospital_view(conn, hid, now):
    h = _hospital(conn, hid)
    res = resource_view(conn, hid, now)
    h['resources'] = res
    h['restrictions'] = restrictions(conn, hid, now)
    h['last_updated_at'] = max((r['updated_at'] for r in res), default=None)
    return h


def list_hospitals(conn, now):
    return [hospital_view(conn, r['id'], now) for r in conn.execute('SELECT id FROM hospitals ORDER BY id')]


# ---------------------------------------------------------------- 환자 입력

def _clean_patient(payload, partial=False):
    out = {}
    if not partial or 'reporter_role' in payload:
        _require(isinstance(payload.get('reporter_role'), str) and payload['reporter_role'] in REPORTER_ROLES,
                 'INVALID', '입력자 구분(self/guardian/transporter) 필요')
        out['reporter_role'] = payload['reporter_role']
    if not partial:
        _require(payload.get('consent') is True, 'CONSENT_REQUIRED', '개인정보 수집 및 병원 전달 동의가 필요합니다')
        out['consent'] = 1
    for k in ('relation', 'contact', 'symptoms', 'name', 'address', 'guardian', 'guardian_phone'):
        if k in payload:
            _require(payload[k] is None or isinstance(payload[k], str), 'INVALID', f'{k}는 문자열')
            out[k] = payload[k]
    if 'birth' in payload:
        value = payload['birth']
        try:
            valid = value is None or (isinstance(value, str) and date.fromisoformat(value).isoformat() == value)
        except ValueError:
            valid = False
        _require(valid, 'INVALID', 'birth는 YYYY-MM-DD 또는 null')
        out['birth'] = value
    for key, values in (('consciousness', {'alert', 'impaired', 'unresponsive', 'unknown'}),
                        ('breathing', {'normal', 'abnormal', 'absent', 'unknown'})):
        if key in payload:
            _require(payload[key] is None or (isinstance(payload[key], str) and payload[key] in values),
                     'INVALID', f'{key} 값 오류')
            out[key] = payload[key]
    for key in ('treatment_duration_seconds', 'max_wait_seconds'):
        if key in payload:
            _require(payload[key] is None or (type(payload[key]) is int and 0 < payload[key] <= 86400),
                     'INVALID', f'{key}는 1~86400 실험 설정값 또는 null')
            out[key] = payload[key]
    for k in ('age', 'onset_at'):
        if k in payload:   # None은 '모르겠음' (P09)
            _require(payload[k] is None or (type(payload[k]) is int and payload[k] >= 0), 'INVALID', f'{k} 값 오류')
            out[k] = payload[k]
    for k in ('lat', 'lon'):
        if k in payload:
            limit = 90 if k == 'lat' else 180
            _require(payload[k] is None or (type(payload[k]) in (int, float) and math.isfinite(payload[k])
                                           and -limit <= payload[k] <= limit), 'INVALID', f'{k} 값 오류')
            out[k] = payload[k]
    if 'extra' in payload:
        _require(isinstance(payload['extra'], dict), 'INVALID', 'extra는 객체')
        out['extra'] = dumps(payload['extra'])
    for k in ('danger_signs', 'required_capabilities'):
        if k in payload:
            _require(isinstance(payload[k], list) and all(isinstance(x, str) for x in payload[k]),
                     'INVALID', f'{k}는 문자열 목록')
            out[k] = dumps(payload[k])
    if 'resource_demands' in payload:
        d = payload['resource_demands']
        _require(isinstance(d, dict) and d and all(type(v) is int and v > 0 for v in d.values()),
                 'INVALID', 'resource_demands는 {자원: 양의 정수}')
        out['resource_demands'] = dumps(d)
    if 'priority_source' in payload or 'priority' in payload:
        src = payload.get('priority_source', 'unknown')
        pr = payload.get('priority')
        _require(isinstance(src, str) and src in PRIORITY_SOURCES, 'INVALID', 'priority_source 값 오류')
        _require(pr is None or (type(pr) is int and pr in PRIORITIES), 'INVALID', 'priority는 4/2/1 또는 null')
        _require(src != 'expert' or pr is not None, 'INVALID', '전문가 평가는 priority가 필요합니다')
        out['priority'], out['priority_source'] = pr, src if pr is not None else 'unknown'
    return out


def create_patient(conn, now, pid, payload, actor='patient_web'):
    fields = _clean_patient(payload)
    _require(isinstance(pid, str) and pid, 'INVALID', '환자 ID 필요')
    fields.update(id=pid, requested_at=now, state='WAITING')
    with transaction(conn):
        _require(conn.execute('SELECT 1 FROM patients WHERE id=?', (pid,)).fetchone() is None,
                 'DUPLICATE', '이미 있는 환자 ID', 409)
        cols = ', '.join(fields)
        conn.execute(f'INSERT INTO patients ({cols}) VALUES ({",".join("?" * len(fields))})', tuple(fields.values()))
        log(conn, now, actor, 'patient_created', pid, danger=bool(payload.get('danger_signs')))
    return patient_view(conn, pid, now)


def update_patient(conn, now, pid, payload, actor='patient_web'):
    """병원 전송 전 요약·수정 (P13). 문의를 보낸 뒤에는 바꾸지 않는다."""
    fields = _clean_patient(payload, partial=True)
    _require(fields, 'INVALID', '바꿀 항목이 없습니다')
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['state'] == 'WAITING', 'INVALID_STATE', '대기 상태에서만 수정할 수 있습니다', 409)
        sent = conn.execute('SELECT COUNT(*) FROM inquiries WHERE patient_id=?', (pid,)).fetchone()[0]
        _require(sent == 0, 'ALREADY_SENT', '병원에 보낸 뒤에는 수정하지 않습니다. 필요하면 문의를 취소하세요.', 409)
        sets = ', '.join(f'{k}=?' for k in fields)
        conn.execute(f'UPDATE patients SET {sets}, version=version+1 WHERE id=?', (*fields.values(), pid))
        log(conn, now, actor, 'patient_updated', pid, fields=sorted(fields))
    return patient_view(conn, pid, now)


NOTIFY_TYPES = ('inquiry_responded', 'inquiry_expired', 'hospital_keeps', 'adjustment_needed', 'arrival_confirmed',
                'destination_changed', 'inquiry_question', 'patient_cancelled', 'no_show')


def patient_view(conn, pid, now):
    expire_acceptances(conn, now)
    p = _patient(conn, pid)
    p['effective_priority'] = effective_priority(p)
    p['call_119'] = bool(p['danger_signs']) or p.get('consciousness') == 'unresponsive' or p.get('breathing') in ('abnormal', 'absent')
    inquiries = rows(conn.execute('SELECT i.*, h.name AS hospital_name, h.phone AS hospital_phone '
                                  'FROM inquiries i JOIN hospitals h ON h.id=i.hospital_id '
                                  'WHERE patient_id=? ORDER BY i.id', (pid,)))
    for q in inquiries:
        q['elapsed_seconds'] = now - q['last_sent_at']
        q['overdue'] = q['state'] in OPEN_INQUIRY and q['elapsed_seconds'] >= PENDING_WARN
        q['can_retry'] = q['state'] in OPEN_INQUIRY and q['elapsed_seconds'] >= RETRY_MIN
        q['usable'] = q['state'] == 'ACCEPTED' and q['patient_version'] == p['version']
    p['inquiries'] = inquiries
    if p['destination_hospital_id']:
        h = _hospital(conn, p['destination_hospital_id'])
        p['destination'] = dict(id=h['id'], name=h['name'], address=h['address'], phone=h['phone'],
                                lat=h['lat'], lon=h['lon'], source=h['source'])
    p['notifications'] = rows(conn.execute(
        f"SELECT id, at, hospital_id, type, data FROM events WHERE patient_id=? AND type IN {NOTIFY_TYPES} "
        f"ORDER BY id DESC LIMIT 20", (pid,)))
    return p


# ---------------------------------------------------------------- 후보 병원 (규칙 기반 필터)

def candidates(conn, now, pid, estimator):
    """적합성·명시적 제한으로 후보를 거르고 이동시간 순으로 정렬한다 (A03, A04, A10, P17~P20).

    배정 최적화(솔버)가 아니다. 문의할 병원을 고르는 화면용 목록이다.
    """
    p = _patient(conn, pid)
    _require(p['lat'] is not None and p['lon'] is not None, 'LOCATION_REQUIRED', '현재 위치를 입력하세요')
    need = set(p['required_capabilities'])
    listed, excluded = [], []
    for h in list_hospitals(conn, now):
        missing = sorted(need - set(h['capabilities']))
        if missing:
            excluded.append(dict(hospital_id=h['id'], name=h['name'], reason='NO_COMPATIBLE_CAPABILITY',
                                 detail=missing))
            continue
        block = blocking_restriction(conn, h['id'], need, now)
        if block:
            excluded.append(dict(hospital_id=h['id'], name=h['name'], reason='RESTRICTED',
                                 detail=f"{block['scope']}: {block['reason']}"))
            continue
        travel = estimator.estimate((p['lat'], p['lon']), (h['lat'], h['lon']))
        res = {r['resource_type']: r for r in h['resources']}
        reasons = [f"필요 진료 분야 보유: {', '.join(sorted(need))}"] if need else []
        checks = []
        enough = True
        for k, qty in p['resource_demands'].items():
            r = res.get(k)
            if r is None or r['free'] is None:
                checks.append(f'{k} 정보 없음')
                enough = False
            elif r['free'] < qty:
                checks.append(f"{k} 여유 {r['free']} (필요 {qty})")
                enough = False
            else:
                reasons.append(f"{k} 여유 {r['free']}")
            if r is not None and r['stale']:
                checks.append(f"{k} 정보 {r['age_seconds'] // 60}분 전 갱신")
        for r in h['restrictions']:
            if r['status'] == 'recheck':
                checks.append(f"제한 종료 예정 경과({r['scope']}), 재확인 필요")
        if h['source'] == 'synthetic':
            checks.append('가상 운영 상태')
        reasons.append(f"이동 약 {travel['duration_seconds'] // 60}분 ({travel['provider']})")
        listed.append(dict(hospital_id=h['id'], name=h['name'], address=h['address'], phone=h['phone'],
                           lat=h['lat'], lon=h['lon'], travel=travel, last_updated_at=h['last_updated_at'],
                           resources_enough=enough, reasons=reasons, checks=checks))
    listed.sort(key=lambda c: (not c['resources_enough'], c['travel']['duration_seconds']))
    if not listed:
        code = 'NO_COMPATIBLE_HOSPITAL' if all(e['reason'] == 'NO_COMPATIBLE_CAPABILITY' for e in excluded) \
            else 'ALL_RESTRICTED_OR_INCOMPATIBLE'
    elif not any(c['resources_enough'] for c in listed):
        code = 'RESOURCE_SHORT_OR_UNCONFIRMED'
    else:
        code = None
    return dict(patient_id=pid, candidates=listed, excluded=excluded, empty_reason=code,
                note='추천 목록은 수용 보장이 아닙니다. 병원의 환자별 수용 확인이 필요합니다.')


# ---------------------------------------------------------------- 수용 문의 (환자 웹)

def _can_inquire(p):
    return p['state'] == 'WAITING' or (p['state'] in ACTIVE_TRANSPORT and p['needs_adjustment'])


def send_inquiries(conn, now, pid, hospital_ids, actor='patient_web'):
    """같은 병원에 진행 중인 문의가 있으면 새로 만들지 않는다 (S06 대신 UNIQUE + 재사용)."""
    _require(isinstance(hospital_ids, list) and hospital_ids, 'INVALID', '문의할 병원을 고르세요')
    created = []
    with transaction(conn):
        p = _patient(conn, pid)
        _require(_can_inquire(p), 'INVALID_STATE', '지금 상태에서는 문의할 수 없습니다', 409, state=p['state'])
        for hid in dict.fromkeys(hospital_ids):
            _hospital(conn, hid)
            _require(hid != p['destination_hospital_id'], 'INVALID', '현재 목적 병원에는 다시 문의하지 않습니다')
            cur = row(conn.execute('SELECT * FROM inquiries WHERE patient_id=? AND hospital_id=?',
                                   (pid, hid)).fetchone())
            if cur and cur['state'] in OPEN_INQUIRY + ('ACCEPTED',) and cur['patient_version'] == p['version']:
                created.append(cur['id'])
                continue
            if cur:
                conn.execute("UPDATE inquiries SET state='PENDING', reason=NULL, question=NULL, answer=NULL, "
                             "last_sent_at=?, attempts=attempts+1, responded_at=NULL, expires_at=NULL, "
                             "patient_version=?, handled_by=NULL, version=version+1 WHERE id=?",
                             (now, p['version'], cur['id']))
                iid = cur['id']
            else:
                iid = conn.execute('INSERT INTO inquiries (patient_id, hospital_id, state, created_at, last_sent_at, '
                                   "patient_version) VALUES (?,?,'PENDING',?,?,?)",
                                   (pid, hid, now, now, p['version'])).lastrowid
            created.append(iid)
            log(conn, now, actor, 'inquiry_sent', pid, hid, inquiry_id=iid)
    return patient_view(conn, pid, now)


def retry_inquiry(conn, now, pid, iid, actor='patient_web'):
    with transaction(conn):
        q = _inquiry(conn, iid)
        _require(q['patient_id'] == pid, 'NOT_FOUND', '문의 없음', 404)
        _require(q['state'] in OPEN_INQUIRY, 'INVALID_STATE', '응답 대기 중인 문의만 재문의합니다', 409)
        _require(now - q['last_sent_at'] >= RETRY_MIN, 'TOO_SOON', f'재문의는 {RETRY_MIN}초 뒤에 가능합니다', 409,
                 wait_seconds=RETRY_MIN - (now - q['last_sent_at']))
        conn.execute('UPDATE inquiries SET last_sent_at=?, attempts=attempts+1, version=version+1 WHERE id=?',
                     (now, iid))
        log(conn, now, actor, 'inquiry_retried', pid, q['hospital_id'], inquiry_id=iid, attempt=q['attempts'] + 1)
    return patient_view(conn, pid, now)


def cancel_inquiry(conn, now, pid, iid, reason, actor='patient_web'):
    _require(reason, 'INVALID', '취소 사유를 입력하세요')
    with transaction(conn):
        q = _inquiry(conn, iid)
        _require(q['patient_id'] == pid, 'NOT_FOUND', '문의 없음', 404)
        _require(q['state'] in OPEN_INQUIRY + ('ACCEPTED',), 'INVALID_STATE', '이미 끝난 문의입니다', 409)
        conn.execute("UPDATE inquiries SET state='CANCELLED', reason=?, version=version+1 WHERE id=?", (reason, iid))
        log(conn, now, actor, 'inquiry_cancelled', pid, q['hospital_id'], inquiry_id=iid, reason=reason)
    return patient_view(conn, pid, now)


def answer_question(conn, now, pid, iid, answer, actor='patient_web'):
    _require(isinstance(answer, str) and answer.strip(), 'INVALID', '답변을 입력하세요')
    with transaction(conn):
        q = _inquiry(conn, iid)
        _require(q['patient_id'] == pid and q['question'], 'INVALID_STATE', '답할 질문이 없습니다', 409)
        _require(q['state'] in OPEN_INQUIRY, 'INVALID_STATE', '이미 끝난 문의입니다', 409)
        conn.execute('UPDATE inquiries SET answer=?, version=version+1 WHERE id=?', (answer, iid))
        log(conn, now, actor, 'inquiry_answered', pid, q['hospital_id'], inquiry_id=iid)
    return patient_view(conn, pid, now)


# ---------------------------------------------------------------- 병원 응답 (병원 웹)

def respond(conn, now, iid, action, handled_by, reason=None, question=None, expected_version=None):
    """확인 중 / 수용 / 불가 / 추가 질문 응답 (H19~H25).

    같은 결과를 다시 누르면 그대로 성공(중복 클릭), 다른 담당자가 먼저 처리했으면 CONFLICT.
    """
    _require(action in ('checking', 'accept', 'decline', 'question'), 'INVALID', 'action 값 오류')
    _require(handled_by, 'INVALID', '처리자 이름이 필요합니다')
    target = {'accept': 'ACCEPTED', 'decline': 'DECLINED'}.get(action)
    with transaction(conn):
        expire_acceptances(conn, now)
        q = _inquiry(conn, iid)
        if target and q['state'] == target:
            return dict(inquiry=q, duplicate=True)
        if expected_version is not None and q['version'] != expected_version:
            raise ServiceError('CONFLICT', '다른 담당자가 먼저 처리했습니다', 409, current=q)
        _require(q['state'] in OPEN_INQUIRY, 'INVALID_STATE', '이미 끝난 문의입니다', 409, current=q)
        p = _patient(conn, q['patient_id'])
        _require(p['state'] not in ('CANCELLED', 'COMPLETED'), 'INVALID_STATE', '환자 측에서 종료된 문의입니다', 409)
        warning = None
        if action == 'accept':
            block = blocking_restriction(conn, q['hospital_id'], set(p['required_capabilities']), now)
            _require(block is None, 'RESTRICTED', f"현재 제한 중입니다: {block and block['reason']}", 409)
            short, unknown = shortfalls(conn, q['hospital_id'], p['resource_demands'])
            _require(not short, 'RESOURCE_CONFLICT', '자원 여유가 부족합니다. 자원 상태를 갱신하거나 불가로 응답하세요.',
                     409, shortfalls=short)
            warning = f"자원 상태 미확인: {', '.join(unknown)}" if unknown else None
            conn.execute("UPDATE inquiries SET state='ACCEPTED', responded_at=?, expires_at=?, handled_by=?, "
                         "patient_version=?, version=version+1 WHERE id=?",
                         (now, now + ACCEPT_TTL, handled_by, p['version'], iid))
        elif action == 'decline':
            _require(reason in DECLINE_REASONS, 'INVALID', f'불가 사유를 고르세요: {sorted(DECLINE_REASONS)}')
            conn.execute("UPDATE inquiries SET state='DECLINED', reason=?, responded_at=?, handled_by=?, "
                         "version=version+1 WHERE id=?", (reason, now, handled_by, iid))
        elif action == 'checking':
            conn.execute("UPDATE inquiries SET state='CHECKING', handled_by=?, version=version+1 WHERE id=?",
                         (handled_by, iid))
        else:
            _require(isinstance(question, str) and question.strip(), 'INVALID', '질문 내용을 입력하세요')
            conn.execute("UPDATE inquiries SET state='CHECKING', question=?, answer=NULL, handled_by=?, "
                         "version=version+1 WHERE id=?", (question, handled_by, iid))
        log(conn, now, handled_by, 'inquiry_question' if action == 'question' else 'inquiry_responded',
            q['patient_id'], q['hospital_id'], inquiry_id=iid, action=action, reason=reason, warning=warning)
        q = _inquiry(conn, iid)
    return dict(inquiry=q, duplicate=False, warning=warning)


def hospital_inquiries(conn, now, hid):
    expire_acceptances(conn, now)
    _hospital(conn, hid)
    items = []
    for q in rows(conn.execute('SELECT * FROM inquiries WHERE hospital_id=? ORDER BY id DESC LIMIT 100', (hid,))):
        p = _patient(conn, q['patient_id'])
        q['patient'] = patient_summary(p)
        q['elapsed_seconds'] = now - q['last_sent_at']
        q['overdue'] = q['state'] in OPEN_INQUIRY and q['elapsed_seconds'] >= PENDING_WARN
        q['closed_by_patient'] = q['state'] == 'CANCELLED'   # 환자 취소·다른 병원 확정 (H28)
        items.append(q)
    open_ = [q for q in items if q['state'] in OPEN_INQUIRY]
    open_.sort(key=lambda q: (not q['overdue'], -q['patient']['effective_priority'], q['last_sent_at']))
    return dict(open=open_, recent=[q for q in items if q['state'] not in OPEN_INQUIRY][:30])


# ---------------------------------------------------------------- 목적지 확정과 이송

def _check_acceptance(conn, now, p, q):
    _require(q['patient_id'] == p['id'], 'NOT_FOUND', '문의 없음', 404)
    # 만료 표시는 다음 조회의 expire_acceptances가 남긴다 (여기서 쓰면 롤백된다).
    _require(not (q['state'] == 'ACCEPTED' and q['expires_at'] <= now), 'ACCEPTANCE_EXPIRED',
             '수용 응답이 만료됐습니다. 다시 문의하세요.', 409)
    _require(q['state'] == 'ACCEPTED', 'NOT_ACCEPTED', '수용 응답을 받은 병원만 목적지로 정할 수 있습니다', 409,
             state=q['state'])
    _require(q['patient_version'] == p['version'], 'STALE_ACCEPTANCE', '환자 정보가 바뀌어 다시 확인이 필요합니다', 409)
    block = blocking_restriction(conn, q['hospital_id'], set(p['required_capabilities']), now)
    _require(block is None, 'RESTRICTED', '병원이 수용 제한을 등록했습니다. 병원에 확인하세요.', 409)
    short, _ = shortfalls(conn, q['hospital_id'], p['resource_demands'], exclude_patient=p['id'])
    _require(not short, 'RESOURCE_CONFLICT', '그 사이 다른 환자가 확정되어 자원이 부족합니다. 병원 재확인이 필요합니다.',
             409, shortfalls=short)


def _close_other_inquiries(conn, now, pid, keep_id, reason):
    for q in rows(conn.execute(f"SELECT id, hospital_id FROM inquiries WHERE patient_id=? AND id!=? "
                               f"AND state IN ('PENDING','CHECKING','ACCEPTED','CONFIRMED')", (pid, keep_id))):
        conn.execute("UPDATE inquiries SET state='CANCELLED', reason=?, version=version+1 WHERE id=?",
                     (reason, q['id']))
        log(conn, now, 'system', 'inquiry_closed', pid, q['hospital_id'], inquiry_id=q['id'], reason=reason)


def confirm_destination(conn, now, pid, iid, actor='patient_web'):
    """수용 확인된 병원 한 곳을 목적지로 확정하고 다른 문의를 닫는다 (P27, P28, S05, S07)."""
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['state'] == 'WAITING', 'INVALID_STATE', '이미 목적지가 정해졌습니다', 409, state=p['state'])
        q = _inquiry(conn, iid)
        _check_acceptance(conn, now, p, q)
        conn.execute("UPDATE patients SET state='CONFIRMED', destination_hospital_id=?, version=version+1 "
                     "WHERE id=? AND version=?", (q['hospital_id'], pid, p['version']))
        conn.execute("UPDATE inquiries SET state='CONFIRMED', version=version+1 WHERE id=?", (iid,))
        _close_other_inquiries(conn, now, pid, iid, '다른 병원 확정')
        log(conn, now, actor, 'destination_confirmed', pid, q['hospital_id'], inquiry_id=iid)
    return patient_view(conn, pid, now)


def _origin(p, lat, lon):
    _require((lat is None) == (lon is None), 'INVALID', '위도와 경도를 함께 보내세요')
    origin = (lat, lon) if lat is not None else (p['lat'], p['lon'])
    _require(None not in origin, 'LOCATION_REQUIRED', '현재 위치가 필요합니다')
    _require(all(type(v) in (int, float) and math.isfinite(v) and -limit <= v <= limit
                 for v, limit in zip(origin, (90, 180))), 'INVALID', '위치 값 오류')
    return origin


def depart(conn, now, pid, estimator, lat=None, lon=None, actor='patient_web'):
    """이동 시작과 도착예정시간 전달 (P31, P34)."""
    before = _patient(conn, pid)
    _require(before['state'] == 'CONFIRMED', 'INVALID_STATE', '목적지 확정 후 출발할 수 있습니다', 409)
    origin = _origin(before, lat, lon)
    h = _hospital(conn, before['destination_hospital_id'])
    travel = estimator.estimate(origin, (h['lat'], h['lon']))
    eta = now + travel['duration_seconds']
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['version'] == before['version'], 'CONFLICT', '이동시간 계산 중 환자 상태가 바뀌었습니다', 409)
        conn.execute("UPDATE patients SET state='IN_TRANSIT', departed_at=?, eta_at=?, lat=?, lon=?, "
                     "version=version+1 WHERE id=?", (now, eta, origin[0], origin[1], pid))
        log(conn, now, actor, 'departed', pid, h['id'], eta_at=eta, provider=travel['provider'])
    return patient_view(conn, pid, now)


def change_destination(conn, now, pid, iid, estimator, lat=None, lon=None, actor='patient_web'):
    """조정 필요 상태에서 새 병원의 수용 확인 뒤에만 목적지를 바꾼다 (P38, A25 원칙)."""
    before = _patient(conn, pid)
    _require(before['state'] in ACTIVE_TRANSPORT, 'INVALID_STATE', '확정·이동 중인 환자만 변경합니다', 409)
    _require(before['needs_adjustment'], 'NOT_REQUESTED', '병원의 조정 요청이 없으면 목적지를 바꾸지 않습니다', 409)
    inquiry = _inquiry(conn, iid)
    _require(inquiry['hospital_id'] != before['destination_hospital_id'], 'INVALID', '같은 병원입니다')
    _check_acceptance(conn, now, before, inquiry)
    eta = None
    if before['state'] == 'IN_TRANSIT':
        origin = _origin(before, lat, lon)
        h = _hospital(conn, inquiry['hospital_id'])
        eta = now + estimator.estimate(origin, (h['lat'], h['lon']))['duration_seconds']
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['version'] == before['version'] and p['needs_adjustment'], 'CONFLICT',
                 '이동시간 계산 중 환자 상태가 바뀌었습니다', 409)
        q = _inquiry(conn, iid)
        _check_acceptance(conn, now, p, q)
        old = p['destination_hospital_id']
        if p['state'] == 'IN_TRANSIT':
            conn.execute('UPDATE patients SET lat=?, lon=? WHERE id=?', (origin[0], origin[1], pid))
        conn.execute('UPDATE patients SET destination_hospital_id=?, eta_at=?, needs_adjustment=0, '
                     'adjustment_note=NULL, version=version+1 WHERE id=?', (q['hospital_id'], eta, pid))
        conn.execute("UPDATE inquiries SET state='CONFIRMED', version=version+1 WHERE id=?", (iid,))
        _close_other_inquiries(conn, now, pid, iid, '목적지 변경')
        log(conn, now, actor, 'destination_changed', pid, q['hospital_id'], from_hospital=old, inquiry_id=iid,
            eta_at=eta)
        log(conn, now, actor, 'patient_redirected_away', pid, old, to_hospital=q['hospital_id'])
    return patient_view(conn, pid, now)


def cancel_patient(conn, now, pid, reason, actor='patient_web'):
    """이송 취소. 도착 예정 수요는 상태 변경과 함께 자동으로 빠진다 (P41, H28)."""
    _require(reason, 'INVALID', '취소 사유를 입력하세요')
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['state'] in ('WAITING',) + ACTIVE_TRANSPORT, 'INVALID_STATE', '취소할 수 없는 상태입니다', 409)
        conn.execute("UPDATE patients SET state='CANCELLED', closed_reason=?, version=version+1 WHERE id=?",
                     (reason, pid))
        _close_other_inquiries(conn, now, pid, -1, '환자 측 취소')
        log(conn, now, actor, 'patient_cancelled', pid, p['destination_hospital_id'], reason=reason)
    return patient_view(conn, pid, now)


def _adjust_occupied(conn, now, hid, demands, sign):
    for k, v in demands.items():
        cur = conn.execute('SELECT occupied FROM resources WHERE hospital_id=? AND resource_type=?',
                           (hid, k)).fetchone()
        if cur is None:
            continue
        conn.execute('UPDATE resources SET occupied=?, updated_at=?, version=version+1 '
                     'WHERE hospital_id=? AND resource_type=?', (max(0, cur['occupied'] + sign * v), now, hid, k))


def confirm_arrival(conn, now, pid, hid, actor='hospital'):
    """도착 예정 점유를 실제 점유로 바꾼다. 상태가 바뀌므로 예정 수요와 이중 계산되지 않는다 (H37, H38)."""
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['destination_hospital_id'] == hid and p['state'] in ACTIVE_TRANSPORT, 'INVALID_STATE',
                 '이 병원으로 오는 환자가 아닙니다', 409)
        conn.execute("UPDATE patients SET state='ARRIVED', arrived_at=?, needs_adjustment=0, version=version+1 "
                     "WHERE id=?", (now, pid))
        _adjust_occupied(conn, now, hid, p['resource_demands'], +1)
        log(conn, now, actor, 'arrival_confirmed', pid, hid)
    return patient_view(conn, pid, now)


def mark_no_show(conn, now, pid, hid, reason='미도착', actor='hospital'):
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['destination_hospital_id'] == hid and p['state'] in ACTIVE_TRANSPORT, 'INVALID_STATE',
                 '이 병원으로 오는 환자가 아닙니다', 409)
        conn.execute("UPDATE patients SET state='CANCELLED', closed_reason=?, version=version+1 WHERE id=?",
                     (reason, pid))
        _close_other_inquiries(conn, now, pid, -1, reason)
        log(conn, now, actor, 'no_show', pid, hid, reason=reason)
    return patient_view(conn, pid, now)


def discharge(conn, now, pid, hid, actor='hospital'):
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['destination_hospital_id'] == hid and p['state'] == 'ARRIVED', 'INVALID_STATE',
                 '도착한 환자만 퇴실 처리합니다', 409)
        conn.execute("UPDATE patients SET state='COMPLETED', version=version+1 WHERE id=?", (pid,))
        _adjust_occupied(conn, now, hid, p['resource_demands'], -1)
        log(conn, now, actor, 'discharged', pid, hid)
    return patient_view(conn, pid, now)


def incoming(conn, now, hid):
    """수용 확인된 환자 목록과 도착 상태 (H29, H30)."""
    _hospital(conn, hid)
    out = []
    for p in rows(conn.execute("SELECT * FROM patients WHERE destination_hospital_id=? AND state IN "
                               "('CONFIRMED','IN_TRANSIT','ARRIVED') ORDER BY COALESCE(eta_at, 9e18), id", (hid,))):
        s = patient_summary(p)
        s.update(needs_adjustment=bool(p['needs_adjustment']), adjustment_note=p['adjustment_note'],
                 departed_at=p['departed_at'], arrived_at=p['arrived_at'],
                 eta_remaining_seconds=None if p['eta_at'] is None else max(0, p['eta_at'] - now))
        out.append(s)
    return out


# ---------------------------------------------------------------- 돌발 유입 (B 시나리오)

def affected_patients(conn, now, hid):
    """돌발 유입·자원 감소 뒤 현재 여유로 다 받을 수 없는 예정 환자 (H34).

    시간축 없이 자원 수량만 비교하는 1차 표시다. 도착 시점별 치료 시작시각 재평가(A26)는
    솔버 연결 후 이 목록을 대체·보완한다. 결과는 병원 판단(H35)의 참고자료다.
    """
    res = {r['resource_type']: r for r in rows(conn.execute('SELECT * FROM resources WHERE hospital_id=?', (hid,)))}
    free = {k: r['capacity'] - r['occupied'] for k, r in res.items() if r['status'] != 'unknown'}
    order = rows(conn.execute(f"SELECT * FROM patients WHERE destination_hospital_id=? AND state IN "
                              f"{ACTIVE_TRANSPORT} ORDER BY state='CONFIRMED', COALESCE(eta_at, 9e18), id", (hid,)))
    used, out = {}, []
    for p in order:
        short = []
        for k, v in p['resource_demands'].items():
            if k not in free:
                continue
            if used.get(k, 0) + v > free[k]:
                short.append(dict(resource_type=k, free_after_earlier=free[k] - used.get(k, 0), need=v))
        if short:
            out.append(dict(patient=patient_summary(p), shortfalls=short,
                            needs_adjustment=bool(p['needs_adjustment'])))
        else:
            for k, v in p['resource_demands'].items():
                used[k] = used.get(k, 0) + v
    return out


def register_external_arrival(conn, now, hid, demands, occurred_at=None, note=None, actor='hospital'):
    """시스템을 쓰지 않은 환자의 유입 등록 (H33). 실제 점유로 바로 반영한다."""
    _require(isinstance(demands, dict) and demands and all(type(v) is int and v > 0 for v in demands.values()),
             'INVALID', 'resource_demands는 {자원: 양의 정수}')
    with transaction(conn):
        _hospital(conn, hid)
        eid = conn.execute('INSERT INTO external_arrivals (hospital_id, occurred_at, observed_at, resource_demands, '
                           'note) VALUES (?,?,?,?,?)', (hid, occurred_at or now, now, dumps(demands), note)).lastrowid
        _adjust_occupied(conn, now, hid, demands, +1)
        affected = affected_patients(conn, now, hid)
        log(conn, now, actor, 'external_arrival', hospital_id=hid, external_id=eid, demands=demands,
            affected=[a['patient']['id'] for a in affected])
    return dict(external_arrival_id=eid, affected=affected, hospital=hospital_view(conn, hid, now))


def hospital_decision(conn, now, pid, hid, decision, note=None, actor='hospital'):
    """기존 수용 유지 / 조정 필요 판단과 이송자 통보 (H35, H36, P37).

    조정 필요여도 목적지를 자동으로 바꾸거나 환자를 지우지 않는다. 이송자는 대체 병원에
    문의하고, 새 수용 확인 후 change_destination으로만 바꾼다.
    """
    _require(decision in ('keep', 'adjust'), 'INVALID', 'decision은 keep 또는 adjust')
    with transaction(conn):
        p = _patient(conn, pid)
        _require(p['destination_hospital_id'] == hid and p['state'] in ACTIVE_TRANSPORT, 'INVALID_STATE',
                 '이 병원으로 오는 환자가 아닙니다', 409)
        if decision == 'adjust':
            conn.execute('UPDATE patients SET needs_adjustment=1, adjustment_note=?, version=version+1 WHERE id=?',
                         (note, pid))
            log(conn, now, actor, 'adjustment_needed', pid, hid, note=note)
        else:
            conn.execute('UPDATE patients SET needs_adjustment=0, adjustment_note=NULL WHERE id=?', (pid,))
            log(conn, now, actor, 'hospital_keeps', pid, hid, note=note)
    return patient_view(conn, pid, now)


def events_since(conn, since_id=0, patient_id=None, hospital_id=None, limit=200):
    sql, args = 'SELECT * FROM events WHERE id>?', [since_id]
    if patient_id:
        sql += ' AND patient_id=?'
        args.append(patient_id)
    if hospital_id:
        sql += ' AND hospital_id=?'
        args.append(hospital_id)
    return rows(conn.execute(sql + ' ORDER BY id LIMIT ?', (*args, limit)))
