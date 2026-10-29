"""'확인 중' 뒤의 최종 결정을 관측 장부에 덧붙이는 확장(4차 2단계, 서버 개선 제안의 실험용 구현).

서버 `inquiry_observations.capture`는 문의 시도마다 첫 회신만 기록한다. 병원이 먼저 '확인 중'을
누르면 그 시도의 최종 수용·불가와 시각은 장부에 남지 않아, 관측형 정책이 수용률·처리시간을 배울 수
없다. 이 모듈은 그런 시도의 최종 결정을 outcome='checked_accept'|'checked_decline' 행으로 덧붙인다.

- 기존 정책(V2·V3·EAT)은 inquiry_policies._observations에서 모르는 outcome을 버리므로 영향이 없다.
- 서버 파일은 수정하지 않는다. 실험 프로세스 안에서만 capture를 감싼다(install/uninstall).
- 실제 서버에 넣으려면 capture가 같은 행을 내도록 고치면 된다(제안).
"""
from server import db, inquiry_observations

_ORIGINAL_CAPTURE = inquiry_observations.capture
TYPES = ('inquiry_sent', 'inquiry_retried', 'inquiry_responded')


def checked_decisions(conn, now, window_seconds=1800):
    lower = now - window_seconds
    attempts, rows = {}, []
    marks = ','.join('?' for _ in TYPES)
    for event in conn.execute(f'SELECT * FROM events WHERE type IN ({marks}) AND at<=? ORDER BY id', (*TYPES, now)):
        event = db.row(event)
        key = (event['patient_id'], event['hospital_id'])
        if not all(key):
            continue
        data = event['data'] or {}
        if event['type'] in ('inquiry_sent', 'inquiry_retried'):
            attempts[key] = dict(submitted_at=event['at'], inquiry_id=data.get('inquiry_id'), checked_at=None, done=False)
            continue
        attempt = attempts.get(key)
        if (attempt is None or attempt['done'] or
                (data.get('inquiry_id') is not None and data['inquiry_id'] != attempt['inquiry_id'])):
            continue
        action = data.get('action')
        if action == 'checking' and attempt['checked_at'] is None:
            attempt['checked_at'] = event['at']
        elif action in ('accept', 'decline'):
            attempt['done'] = True
            if attempt['checked_at'] is not None and lower < event['at'] <= now:
                rows.append(dict(hospital=key[1], patient=key[0], submitted_at=attempt['submitted_at'],
                                 checked_at=attempt['checked_at'], observed_at=event['at'],
                                 outcome='checked_' + action))
    return rows


def capture(conn, now, pid, timeout_seconds=180, window_seconds=1800):
    result = _ORIGINAL_CAPTURE(conn, now, pid, timeout_seconds=timeout_seconds, window_seconds=window_seconds)
    extra = checked_decisions(conn, now, window_seconds)
    return dict(result, observations=list(result['observations']) + extra)


def install():
    inquiry_observations.capture = capture


def uninstall():
    inquiry_observations.capture = _ORIGINAL_CAPTURE
