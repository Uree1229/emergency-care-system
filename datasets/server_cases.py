"""공통 서버 업무 규칙으로 재생 가능한 입력과 기대 결과. 시간 단위는 초."""
from .allocation import rng_for
from .io import partition

PROFILES = {
    'normal': '정상 문의·수용·확정·출발·도착·퇴실',
    'parallel_acceptance': '복수 수용 응답 후 목적지 하나 확정',
    'nonresponse_retry': '미응답 60초 경고·120초 재문의',
    'expired_acceptance': '수용 응답 만료 후 확정 차단',
    'last_bed_race': '마지막 한 자리에 두 환자가 수용 응답을 받음',
    'walkin_redirect': 'A 이동 중 B 유입·조정 요청·다른 병원 수용·목적지 변경',
    'walkin_keep': '돌발 유입 후 병원 수용 유지·자동 변경 없음',
    'restriction_after_accept': '수용 뒤 병원 전체 제한·확정 차단',
    'restriction_recheck': '제한 종료 예정 이후 재확인 필요 상태',
    'unknown_resource': '자원 미확인 경고·추천에서 제외',
    'stale_resource': '갱신이 늦은 자원 정보·추천에서 제외',
    'unknown_patient': '나이·위치·의식·호흡을 모르는 입력',
    'danger_signs': '위험 신호 입력 시 119 안내',
    'decline_all': '후보 병원 모두 수용 불가',
    'question_answer': '병원 추가 질문·환자 답변 후 수용',
    'cancel_waiting': '문의·수용 응답 뒤 환자 취소',
    'cancel_transit': '이동 중 취소·예정 수요 해제',
    'duplicate_response': '수용 버튼 중복 처리',
    'no_show': '확정·출발 후 병원 미도착 처리',
    'invalid_consent': '동의 없는 입력 거절·환자 미생성',
}


def make_server_case(profile, seed):
    r = rng_for(profile, seed)
    base = dict(reporter_role='guardian', consent=True, name='가상 환자 A', contact='010-0000-0000',
                age=r.randint(1, 90), symptoms='가상 실험 증상', consciousness='unknown', breathing='unknown',
                resource_demands={'ed_bed': 1}, lat=35.16, lon=126.85)
    patients = {'A': base}
    # 문의 ID는 실행 당시 patient_id/hospital_id로 찾는다. 서로 다른 DB에서도 재생 가능하다.
    def step(at, operation, **kw):
        return dict(at=at, operation=operation, **kw)
    steps = [step(0, 'create', patient_id='A'), step(10, 'inquire', patient_id='A', hospital_ids=['H1', 'H2'])]
    steps += [step(20, 'respond', patient_id='A', hospital_id='H1', action='accept')]
    checks = []
    beds, occupied = 2, 0
    if profile == 'normal':
        steps += [step(30, 'confirm', patient_id='A', hospital_id='H1'), step(40, 'depart', patient_id='A'),
                  step(180, 'arrive', patient_id='A', hospital_id='H1'), step(400, 'discharge', patient_id='A', hospital_id='H1')]
        checks = [dict(kind='patient_state', patient_id='A', value='COMPLETED'), dict(kind='occupied', hospital_id='H1', value=0)]
    elif profile == 'parallel_acceptance':
        steps += [step(21, 'respond', patient_id='A', hospital_id='H2', action='accept'),
                  step(30, 'confirm', patient_id='A', hospital_id='H1')]
        checks = [dict(kind='inquiry_state', patient_id='A', hospital_id='H2', value='CANCELLED')]
    elif profile == 'nonresponse_retry':
        steps = steps[:2] + [step(70, 'view', patient_id='A'), step(130, 'retry', patient_id='A', hospital_id='H1')]
        checks = [dict(kind='attempts', patient_id='A', hospital_id='H1', value=2)]
    elif profile == 'expired_acceptance':
        steps += [step(621, 'confirm', patient_id='A', hospital_id='H1', expected_error='ACCEPTANCE_EXPIRED')]
        checks = [dict(kind='patient_state', patient_id='A', value='WAITING')]
    elif profile == 'last_bed_race':
        beds = 1
        patients['B'] = dict(base, name='가상 환자 B')
        steps += [step(21, 'create', patient_id='B'), step(22, 'inquire', patient_id='B', hospital_ids=['H1']),
                  step(23, 'respond', patient_id='B', hospital_id='H1', action='accept'),
                  step(30, 'confirm', patient_id='A', hospital_id='H1'),
                  step(30, 'confirm', patient_id='B', hospital_id='H1', expected_error='RESOURCE_CONFLICT')]
        checks = [dict(kind='patient_state', patient_id='B', value='WAITING')]
    elif profile in ('walkin_redirect', 'walkin_keep'):
        beds = 1
        steps += [step(30, 'confirm', patient_id='A', hospital_id='H1'), step(40, 'depart', patient_id='A'),
                  step(50, 'external_arrival', hospital_id='H1', resource_demands={'ed_bed': 1})]
        if profile == 'walkin_redirect':
            steps += [step(60, 'decision', patient_id='A', hospital_id='H1', decision='adjust'),
                      step(70, 'inquire', patient_id='A', hospital_ids=['H2']),
                      step(80, 'respond', patient_id='A', hospital_id='H2', action='accept'),
                      step(90, 'change', patient_id='A', hospital_id='H2', lat=35.18, lon=126.85)]
            checks = [dict(kind='destination', patient_id='A', value='H2')]
        else:
            steps += [step(60, 'decision', patient_id='A', hospital_id='H1', decision='keep')]
            checks = [dict(kind='destination', patient_id='A', value='H1')]
    elif profile in ('restriction_after_accept', 'restriction_recheck'):
        steps += [step(25, 'restrict', hospital_id='H1', scope='all', reason='가상 인력 부족',
                       ends_after=35 if profile == 'restriction_recheck' else None)]
        if profile == 'restriction_after_accept':
            steps += [step(30, 'confirm', patient_id='A', hospital_id='H1', expected_error='RESTRICTED')]
        else:
            steps += [step(61, 'view', patient_id='A')]
            checks = [dict(kind='restriction_status', hospital_id='H1', value='recheck')]
    elif profile in ('unknown_resource', 'stale_resource'):
        steps = steps[:2]
        if profile == 'unknown_resource':
            steps += [step(15, 'resource', hospital_id='H1', capacity=beds, occupied=0, status='unknown'),
                      step(20, 'respond', patient_id='A', hospital_id='H1', action='accept')]
        else:
            steps += [step(605, 'respond', patient_id='A', hospital_id='H1', action='accept')]
        checks = [dict(kind='resource_flag', hospital_id='H1', key='status' if profile == 'unknown_resource' else 'stale',
                       value='unknown' if profile == 'unknown_resource' else True)]
    elif profile == 'unknown_patient':
        base.update(age=None, lat=None, lon=None, consciousness=None, breathing=None)
        steps = steps[:2]
        checks = [dict(kind='patient_field', patient_id='A', key='consciousness', value=None),
                  dict(kind='patient_field', patient_id='A', key='call_119', value=False)]
    elif profile == 'danger_signs':
        base.update(consciousness='unresponsive', breathing='absent', danger_signs=['가상 위험 신호'])
        checks = [dict(kind='patient_field', patient_id='A', key='call_119', value=True)]
    elif profile == 'decline_all':
        steps = steps[:2] + [step(20, 'respond', patient_id='A', hospital_id='H1', action='decline', reason='병상'),
                             step(21, 'respond', patient_id='A', hospital_id='H2', action='decline', reason='인력')]
        checks = [dict(kind='inquiry_state', patient_id='A', hospital_id=hid, value='DECLINED') for hid in ('H1', 'H2')]
    elif profile == 'question_answer':
        steps = steps[:2] + [step(20, 'respond', patient_id='A', hospital_id='H1', action='question', question='가상 복용약 확인'),
                             step(30, 'answer', patient_id='A', hospital_id='H1', answer='가상 추가 정보'),
                             step(40, 'respond', patient_id='A', hospital_id='H1', action='accept')]
        checks = [dict(kind='inquiry_field', patient_id='A', hospital_id='H1', key='answer', value='가상 추가 정보')]
    elif profile == 'cancel_waiting':
        steps += [step(30, 'cancel', patient_id='A')]
        checks = [dict(kind='patient_state', patient_id='A', value='CANCELLED'),
                  dict(kind='inquiry_state', patient_id='A', hospital_id='H1', value='CANCELLED')]
    elif profile in ('cancel_transit', 'no_show'):
        steps += [step(30, 'confirm', patient_id='A', hospital_id='H1'), step(40, 'depart', patient_id='A'),
                  step(50, 'cancel' if profile == 'cancel_transit' else 'no_show', patient_id='A', hospital_id='H1')]
        checks = [dict(kind='patient_state', patient_id='A', value='CANCELLED'),
                  dict(kind='expected_demand', hospital_id='H1', value=0)]
    elif profile == 'duplicate_response':
        steps += [step(25, 'respond', patient_id='A', hospital_id='H1', action='accept', expected_output={'duplicate': True})]
        checks = [dict(kind='inquiry_field', patient_id='A', hospital_id='H1', key='version', value=2)]
    elif profile == 'invalid_consent':
        base['consent'] = False
        steps = [step(0, 'create', patient_id='A', expected_error='CONSENT_REQUIRED')]
        checks = [dict(kind='patient_count', value=0)]
    return dict(id=f'API-{profile}-{seed:04d}', kind='server', schema_version=1, profile=profile,
                description=PROFILES[profile], seed=seed, split=partition(seed), source='synthetic',
                time_unit='second', epoch=1_790_000_000 + seed * 86400,
                hospitals=[dict(id='H1', name='가상 병원 1', lat=35.17, lon=126.85, capabilities=['응급의학'],
                                capacity=beds, occupied=occupied),
                           dict(id='H2', name='가상 병원 2', lat=35.20, lon=126.85, capabilities=['응급의학'],
                                capacity=4, occupied=0)], patients=patients, steps=steps,
                checks=checks, concurrency_note='동시시각 요청은 한 순서로 직렬화하여 충돌 규칙을 검증. 실제 경쟁은 test_server에서 검증')
