"""가상 병원·환자 데이터 생성기 (T01).

공개 API 실응답을 확보하기 전에도 같은 내부 스키마로 화면·서버를 연결하기 위한 데이터다.
모든 레코드는 source='synthetic'이며 실제 병원·환자를 대표하지 않는다.
지역(광주 일대 좌표)과 규모(병원 10곳, 환자 20명 이하)는 실험 설정이다.
"""
import random

from . import service

CAPABILITIES = ['응급의학', '소아청소년과', '정형외과', '신경외과', '심장내과', '외과']
CENTER = (35.1595, 126.8526)   # 광주광역시청 부근, 좌표만 사용

HOSPITALS = [
    # id, 이름, 위도·경도 오프셋, 진료 분야, 진료 자리, 전문팀
    ('H01', '가상 권역응급의료센터', (0.012, -0.020), CAPABILITIES, 12, 3),
    ('H02', '가상 지역응급의료센터 동부', (0.030, 0.035), ['응급의학', '정형외과', '외과', '심장내과'], 8, 2),
    ('H03', '가상 지역응급의료센터 서부', (-0.010, -0.060), ['응급의학', '신경외과', '외과'], 8, 2),
    ('H04', '가상 소아전문응급센터', (-0.025, 0.010), ['응급의학', '소아청소년과'], 6, 1),
    ('H05', '가상 지역응급의료기관 남부', (-0.050, 0.020), ['응급의학', '외과'], 5, 1),
    ('H06', '가상 지역응급의료기관 북부', (0.060, -0.005), ['응급의학', '정형외과'], 5, 1),
    ('H07', '가상 심뇌혈관센터', (0.005, 0.050), ['응급의학', '심장내과', '신경외과'], 6, 2),
    ('H08', '가상 군 지역응급의료기관', (0.180, 0.150), ['응급의학'], 3, 0),
    ('H09', '가상 지역응급의료기관 광산', (0.040, -0.110), ['응급의학', '외과', '정형외과'], 4, 1),
    ('H10', '가상 의료공백지역 보건의료원', (-0.220, -0.180), ['응급의학'], 2, 0),
]


def seed_hospitals(conn, now, rng=None, occupancy=0.5):
    rng = rng or random.Random(0)
    for hid, name, (dlat, dlon), caps, beds, teams in HOSPITALS:
        service.upsert_hospital(conn, dict(id=hid, name=name, address=f'가상 주소 {hid}', phone=f'000-0000-{hid[1:]}00',
                                           lat=CENTER[0] + dlat, lon=CENTER[1] + dlon, capabilities=caps,
                                           source='synthetic'))
        for rtype, cap in (('ed_bed', beds), ('special_team', teams)):
            occupied = min(cap, round(cap * occupancy * rng.uniform(0.6, 1.4)))
            service.set_resource(conn, now, hid, rtype, cap, occupied, source='synthetic', actor='seed')


def random_patient(rng, i):
    caps = rng.choice([[], ['정형외과'], ['소아청소년과'], ['심장내과'], ['신경외과'], ['외과']])
    expert = rng.random() < 0.5
    special = bool(caps) and rng.random() < 0.4
    return dict(reporter_role=rng.choice(['self', 'guardian', 'transporter']), consent=True,
                relation=None, contact=f'010-0000-{i:04d}', age=rng.choice([None, rng.randint(1, 90)]),
                symptoms=rng.choice(['복통', '흉통', '골절 의심', '고열', '두통·구토', '호흡곤란']),
                priority=rng.choice([4, 2, 1]) if expert else None,
                priority_source='expert' if expert else 'unknown',
                required_capabilities=caps,
                resource_demands={'ed_bed': 1, **({'special_team': 1} if special else {})},
                lat=CENTER[0] + rng.uniform(-0.15, 0.15), lon=CENTER[1] + rng.uniform(-0.15, 0.15))


def seed_patients(conn, now, n=20, rng=None):
    rng = rng or random.Random(1)
    return [service.create_patient(conn, now, f'SYN-{i:03d}', random_patient(rng, i), actor='seed')['id']
            for i in range(1, n + 1)]
