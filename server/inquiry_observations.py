"""진행 신호와 최종 결정을 분리한 읽기 전용 문의 관측 장부."""
from . import db

EVENT_TYPES = ('inquiry_sent', 'inquiry_retried', 'inquiry_responded',
               'inquiry_question', 'inquiry_cancelled', 'inquiry_closed')


def capture(conn, now, pid, timeout_seconds=180, window_seconds=1800):
    """한 시도당 최종 결정 또는 결정 대기 timeout을 최대 한 번 관측한다.

    확인 중·질문은 attempts의 단계 기록이며 빠른 결정으로 해석하지 않는다.
    제한시간 뒤 실제 결정은 단계에 보존하되 이미 관측한 timeout을 바꾸지 않는다.
    제한 전 취소·재문의는 결정 실패로 세지 않는다. 업무 상태는 수정하지 않는다.
    attempts는 관측창 내 활동이 있거나 미해결인 시도이며 질문·답변 본문과 처리자
    이름을 포함하지 않는다. stages에는 사건 ID·단계·시각만 보존한다.
    """
    for value, name, minimum in ((now, 'now', 0), (timeout_seconds, 'timeout_seconds', 1),
                                 (window_seconds, 'window_seconds', 1)):
        if type(value) is not int or value < minimum:
            raise ValueError(f'{name} must be an integer >= {minimum}')
    lower = now - window_seconds
    latest, observations, attempts = {}, [], []

    def finish(attempt):
        submitted, decision, ended = attempt['submitted_at'], attempt['decision_at'], attempt['ended_at']
        threshold = submitted + timeout_seconds
        if decision is not None and decision <= threshold:
            observed, outcome = decision, attempt['decision']
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
        if not all(key) or not isinstance(data, dict):
            continue
        if typ in ('inquiry_sent', 'inquiry_retried'):
            if key in latest:
                if latest[key]['ended_at'] is None:
                    latest[key].update(ended_at=at, end_reason='retried', unresolved=False)
                finish(latest[key])
            attempt = dict(hospital=key[1], patient=key[0], submitted_at=at,
                           inquiry_id=data.get('inquiry_id'), attempt_event_id=event['id'],
                           first_reply_at=None, first_checking_at=None, first_question_at=None,
                           decision_at=None, decision=None, ended_at=None, end_reason=None,
                           decision_deadline_at=at + timeout_seconds, unresolved=True, stages=[])
            latest[key] = attempt
            attempts.append(attempt)
            continue
        attempt = latest.get(key)
        if (attempt is None or at < attempt['submitted_at'] or not attempt['unresolved'] or
                (data.get('inquiry_id') is not None and
                 data['inquiry_id'] != attempt['inquiry_id'])):
            continue
        if typ in ('inquiry_responded', 'inquiry_question'):
            action = 'question' if typ == 'inquiry_question' else data.get('action')
            if action not in ('checking', 'question', 'accept', 'decline'):
                continue
            attempt['stages'].append(dict(event_id=event['id'], at=at, stage=action))
            if attempt['first_reply_at'] is None:
                attempt['first_reply_at'] = at
            if action == 'checking' and attempt['first_checking_at'] is None:
                attempt['first_checking_at'] = at
            elif action == 'question' and attempt['first_question_at'] is None:
                attempt['first_question_at'] = at
            elif action in ('accept', 'decline'):
                attempt.update(decision_at=at, decision=action, ended_at=at,
                               end_reason=action, unresolved=False)
        else:
            attempt.update(ended_at=at, end_reason='cancelled' if typ == 'inquiry_cancelled' else 'closed',
                           unresolved=False)

    pending = []
    for attempt in latest.values():
        finish(attempt)
        if attempt['unresolved']:
            pending.append({k: attempt[k] for k in ('hospital', 'patient', 'submitted_at')})
    # 이전 DB에 송신 사건이 없으면 미해결 문의만 보완하고 단계·결정은 만들지 않는다.
    for q in conn.execute("SELECT patient_id,hospital_id,last_sent_at FROM inquiries "
                          "WHERE state IN ('PENDING','CHECKING') AND last_sent_at<=?", (now,)):
        key = (q['patient_id'], q['hospital_id'])
        if key not in latest:
            pending.append(dict(hospital=q['hospital_id'], patient=q['patient_id'], submitted_at=q['last_sent_at']))
    observations.sort(key=lambda o: (o['observed_at'], o['submitted_at'], o['hospital'], o['patient']))
    pending.sort(key=lambda o: (o['submitted_at'], o['hospital'], o['patient']))
    recent_attempts = [attempt for attempt in attempts if attempt['unresolved'] or
                       max(attempt['submitted_at'], attempt['ended_at'] or attempt['submitted_at'],
                           *(stage['at'] for stage in attempt['stages'])) > lower]
    return dict(schema_version=2, observation_basis='final_decision', observations=observations,
                attempts=recent_attempts, pending=pending,
                patient_pending=[q for q in pending if q['patient'] == pid])
