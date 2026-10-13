"""문의 사건을 현재 시각까지 재구성한다. 미래 상태와 환자 본문은 사용하지 않는다."""
from . import db

EVENT_TYPES = ('inquiry_sent', 'inquiry_retried', 'inquiry_responded',
               'inquiry_question', 'inquiry_cancelled', 'inquiry_closed')


def capture(conn, now, pid, timeout_seconds=180, window_seconds=1800):
    """회신은 attempt의 첫 병원 응답 한 번만 센다. timeout은 거절이 아니다.

    확인 중·추가 질문도 첫 회신으로 기록하지만 pending에서는 유지한다.
    제한시간 뒤 첫 회신은 timeout 관측을 바꾸지 않는다. 실제 늦은 수용 응답의
    유효성 판정은 기존 업무 규칙이 별도로 수행한다. 이 함수는 DB에 쓰지 않는다.
    """
    lower = now - window_seconds
    latest, observations = {}, []

    def finish(attempt):
        submitted, first, ended = attempt['submitted_at'], attempt['first_reply'], attempt['ended_at']
        threshold = submitted + timeout_seconds
        if first is not None and first['at'] <= threshold:
            observed, outcome = first['at'], first['outcome']
        elif threshold <= now and (ended is None or ended >= threshold):
            observed, outcome = threshold, 'timeout'
        else:
            return
        if lower < observed <= now:
            observations.append(dict(hospital=attempt['hospital'], patient=attempt['patient'],
                                     submitted_at=submitted, observed_at=observed, outcome=outcome))

    marks = ','.join('?' for _ in EVENT_TYPES)
    # SQLite 사건 ID가 동일 초 내 순서를 보존한다. at>now인 기록은 먼저 제외한다.
    for event in conn.execute(f'SELECT * FROM events WHERE type IN ({marks}) AND at<=? ORDER BY id',
                              (*EVENT_TYPES, now)):
        event = db.row(event)
        key = (event['patient_id'], event['hospital_id'])
        data, typ, at = event['data'], event['type'], event['at']
        if not all(key):
            continue
        if typ in ('inquiry_sent', 'inquiry_retried'):
            if key in latest:
                if latest[key]['ended_at'] is None:
                    latest[key]['ended_at'] = at
                finish(latest[key])
            latest[key] = dict(hospital=key[1], patient=key[0], submitted_at=at,
                               inquiry_id=data.get('inquiry_id'), first_reply=None,
                               ended_at=None, unresolved=True)
            continue
        attempt = latest.get(key)
        if (attempt is None or at < attempt['submitted_at'] or not attempt['unresolved'] or
                (data.get('inquiry_id') is not None and
                 data['inquiry_id'] != attempt['inquiry_id'])):
            continue
        if typ in ('inquiry_responded', 'inquiry_question'):
            action = data.get('action')
            if typ == 'inquiry_responded' and action not in ('checking', 'accept', 'decline'):
                continue
            if attempt['first_reply'] is None:
                attempt['first_reply'] = dict(at=at, outcome=action if action in ('accept', 'decline') else 'reply')
            if action in ('accept', 'decline'):
                attempt.update(ended_at=at, unresolved=False)
        else:
            attempt.update(ended_at=at, unresolved=False)

    pending = []
    for attempt in latest.values():
        finish(attempt)
        if attempt['unresolved']:
            pending.append({k: attempt[k] for k in ('hospital', 'patient', 'submitted_at')})
    # 이전 DB에 송신 사건이 없으면 현재 미해결 문의만 보완한다. 회신 이력은 만들지 않는다.
    for q in conn.execute("SELECT patient_id,hospital_id,last_sent_at FROM inquiries "
                          "WHERE state IN ('PENDING','CHECKING') AND last_sent_at<=?", (now,)):
        key = (q['patient_id'], q['hospital_id'])
        if key not in latest:
            pending.append(dict(hospital=q['hospital_id'], patient=q['patient_id'], submitted_at=q['last_sent_at']))
    observations.sort(key=lambda o: (o['observed_at'], o['submitted_at'], o['hospital'], o['patient']))
    pending.sort(key=lambda o: (o['submitted_at'], o['hospital'], o['patient']))
    return dict(observations=observations, pending=pending,
                patient_pending=[q for q in pending if q['patient'] == pid])
