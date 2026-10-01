"""시연·화면 개발용 시나리오 (T05, T19).

    python -m server.app --seed --scenario sudden

단계는 누적이다. inquiry → accepted → in_transit → sudden.
- inquiry:    DEMO-A(소아 고열)가 H04·H01에 문의 대기, H01 문의는 60초를 넘겨 강조 대상.
              DEMO-C(흉통, 위험 신호)는 H07이 확인 중이며 추가 질문을 보냄.
- accepted:   H04·H01이 DEMO-A를 수용, H02가 DEMO-D를 수용. 아직 목적지 미확정.
- in_transit: DEMO-A가 H04로 확정·출발(마지막 진료 자리), DEMO-E는 H01로 이동 중.
- sudden:     H04에 시스템 미사용 중증 소아 B가 도착 → DEMO-A가 영향 환자로 표시.
              병원의 유지/조정 판단은 화면에서 하도록 남겨 둔다.
모든 데이터는 가상이다. 시각은 현재로부터 몇 분 전으로 맞춰 경과시간이 자연스럽게 보이게 한다.
"""
from . import service

STAGES = ['inquiry', 'accepted', 'in_transit', 'sudden']
NEAR_CENTER = dict(lat=35.1450, lon=126.8600)


def _iid(conn, pid, hid):
    return conn.execute('SELECT id FROM inquiries WHERE patient_id=? AND hospital_id=?', (pid, hid)).fetchone()[0]


def _patient(conn, at, pid, **kw):
    payload = dict(reporter_role='guardian', consent=True, contact='010-0000-0000', resource_demands={'ed_bed': 1},
                   **NEAR_CENTER)
    payload.update(kw)
    service.create_patient(conn, at, pid, payload, actor='scenario')


def build(conn, now, until='sudden'):
    level = STAGES.index(until)
    t = now - 600
    _patient(conn, t, 'DEMO-A', relation='부모', age=7, symptoms='고열 39.5도, 처짐', onset_at=t - 3600,
             extra={'복용약': '해열제'}, required_capabilities=['소아청소년과'])
    _patient(conn, t + 30, 'DEMO-C', reporter_role='self', age=58, symptoms='가슴 통증 20분 지속',
             danger_signs=['흉통 지속'], required_capabilities=['심장내과'],
             resource_demands={'ed_bed': 1, 'special_team': 1}, lat=35.1700, lon=126.9000)
    _patient(conn, t + 60, 'DEMO-D', reporter_role='transporter', age=34, symptoms='손목 골절 의심',
             priority=1, priority_source='expert', required_capabilities=['정형외과'], lat=35.1900, lon=126.8800)
    _patient(conn, t + 60, 'DEMO-E', reporter_role='transporter', age=71, symptoms='낙상 후 두통',
             priority=2, priority_source='expert', lat=35.1650, lon=126.8200)

    service.send_inquiries(conn, t + 100, 'DEMO-A', ['H04', 'H01'], actor='scenario')
    service.send_inquiries(conn, t + 120, 'DEMO-C', ['H07', 'H02'], actor='scenario')
    service.send_inquiries(conn, t + 130, 'DEMO-E', ['H01'], actor='scenario')
    c7 = _iid(conn, 'DEMO-C', 'H07')
    service.respond(conn, t + 200, c7, 'question', '김간호(H07)', question='흉통이 쉬면 나아지나요? 식은땀이 있나요?')
    service.send_inquiries(conn, t + 210, 'DEMO-D', ['H02', 'H06'], actor='scenario')
    if level < 1:
        return summary(conn, now)

    # 각 단계의 시각이 앞 단계보다 늦도록 맞춰 이벤트 기록이 시간 순서를 지킨다.
    service.respond(conn, t + 250, _iid(conn, 'DEMO-E', 'H01'), 'accept', '이간호(H01)')
    service.respond(conn, now - 200, _iid(conn, 'DEMO-A', 'H04'), 'accept', '박간호(H04)')
    service.respond(conn, now - 195, _iid(conn, 'DEMO-A', 'H01'), 'accept', '이간호(H01)')
    service.respond(conn, now - 190, _iid(conn, 'DEMO-D', 'H02'), 'accept', '최간호(H02)')
    if level < 2:
        return summary(conn, now)

    service.confirm_destination(conn, now - 180, 'DEMO-E', _iid(conn, 'DEMO-E', 'H01'), actor='scenario')
    service.depart(conn, now - 175, 'DEMO-E', _FixedTravel(1200), actor='scenario')
    beds = conn.execute("SELECT capacity FROM resources WHERE hospital_id='H04' AND resource_type='ed_bed'").fetchone()[0]
    service.set_resource(conn, now - 170, 'H04', 'ed_bed', beds, beds - 1, actor='박간호(H04)')   # 마지막 한 자리
    service.confirm_destination(conn, now - 160, 'DEMO-A', _iid(conn, 'DEMO-A', 'H04'), actor='scenario')
    service.depart(conn, now - 150, 'DEMO-A', _FixedTravel(900), actor='scenario')
    if level < 3:
        return summary(conn, now)

    service.register_external_arrival(conn, now - 60, 'H04', {'ed_bed': 1}, note='보호자 차량으로 내원한 중증 소아(시스템 미사용)',
                                      actor='박간호(H04)')
    return summary(conn, now)


class _FixedTravel:
    """시나리오의 도착 예정시각을 실행 시점과 무관하게 고정한다."""

    def __init__(self, seconds):
        self.seconds = seconds

    def estimate(self, origin, destination):
        return dict(duration_seconds=self.seconds, distance_meters=None, provider='scenario', scenario='fixed')


def summary(conn, now):
    out = {}
    for pid in ('DEMO-A', 'DEMO-C', 'DEMO-D', 'DEMO-E'):
        p = service.patient_view(conn, pid, now)
        out[pid] = dict(state=p['state'], destination=p['destination_hospital_id'],
                        inquiries={q['hospital_id']: q['state'] for q in p['inquiries']})
    out['H04_affected'] = [a['patient']['id'] for a in service.affected_patients(conn, now, 'H04')]
    return out
