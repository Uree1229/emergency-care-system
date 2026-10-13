# 병원용 UI

각자 PC에서 실행하는 최신 방법은 [팀원 테스트 가이드](../docs/TEAM_TEST_GUIDE.md)를 따릅니다. 아래 포트별 기록은 이전 로컬 시연 예시입니다.


병원 담당자가 환자별 수용 문의를 확인하고, 수용 가능 여부와 불가 사유를 응답하는 웹 화면입니다.

- 서버 연동 기준: [`docs/API.md`](../docs/API.md)
- 바로 맡을 작업과 통합 완료 기준: [화면과 서버 통합 개발 기준](../docs/TEAM_IMPLEMENTATION.md#병원-화면-담당)
- 현재 서버 구현: [`server/`](../server/README.md)
- 공통 화면 흐름 참고: [`prototype/`](../prototype/)

## 실행

1. 저장소 루트에서 서버를 켭니다.
   ```powershell
   py -3 -m server.app --seed --scenario sudden --port 8000
   ```
2. 서버의 `http://127.0.0.1:8000/hospital/?hid=H07`을 엽니다. 같은 서버 주소를 자동 사용합니다. `hospital-ui/index.html`을 직접 열어 서버 주소를 입력하는 기존 방식도 사용할 수 있습니다. 빌드나 설치가 필요 없습니다.
3. 위쪽에서 병원을 고르고 담당자 이름을 입력합니다. 시연 시나리오에서는 H02, H07에 대기 문의가 있습니다.

주소 뒤에 `?hid=H07&server=http://127.0.0.1:8000`을 붙이면 병원과 서버 주소를 바로 정할 수 있습니다.

## 구현된 기능 (롱리스트 ID)

| 기능 | ID | 사용 API |
|---|---|---|
| 신규 문의 목록, 3초마다 자동 갱신, 새 문의 표시 | H15 | `GET /api/hospitals/{hid}/inquiries` |
| 경과시간, 환자 상태, 도착 예정 표시 | H16 | `GET /api/hospitals/{hid}/inquiries` |
| 환자 정보 상세, 위험 신호 | H17 | `GET /api/hospitals/{hid}/inquiries` |
| 자기신고·전문가·미확인 구분 | H18 | `GET /api/hospitals/{hid}/inquiries` |
| 확인 중 회신 | H19 | `POST /api/inquiries/{iid}/respond` |
| 추가 질문 보내기, 답변 확인 | H20 | `POST /api/inquiries/{iid}/respond` |
| 수용 가능 응답 | H21 | `POST /api/inquiries/{iid}/respond` |
| 수용 불가와 사유 선택(인력·장비·병상·진료 불가·기타) | H22 | `POST /api/inquiries/{iid}/respond` |
| 중복 응답 처리, 다른 담당자 선처리 시 새로고침 | H24, H25 | `duplicate`, `409 CONFLICT` |
| 60초 이상 미응답 강조 | H26 | `overdue` |
| 환자 측 취소·다른 병원 확정 표시 | H28 | `recent`의 `CANCELLED` |

## 공통 서버 통합 (2026-10-13)

기존 문의 화면 디자인과 응답 흐름을 유지하고, 상단에 **도착 예정 · 이송 대화**, **병원 자원** 탭을 추가했습니다. 담당자 이름을 입력한 뒤 변경 작업을 수행합니다. 병원 ID는 시연 역할을 구분하며 실제 사용자 인증은 하지 않습니다.

| 기능 | API / 처리 |
|---|---|
| 확정·이송·도착 환자 목록 | `GET /api/hospitals/{hid}/incoming`, 3초 조회 |
| 환자별 조건 재확인 사유 | `incoming[].transport_review`의 상태와 `evidence.issues` |
| 수용 유지·조정 필요 | 최신 `GET /api/patients/{pid}/transport-review` 확인 후 병원 `decision` API. 활성 감시는 `expected_review_version`, `expected_risk_signature`를 함께 전송 |
| 도착 확인·퇴실 | 병원 `arrive`, `discharge` API |
| 이송자와 병원 대화 | 병원 전용 `conversation`, `messages` API. 서버의 `can_send`와 참여 구간 사용 |
| 운영 자원 총량·점유·상태 수정 | 병원 자원 `PUT`, 편집을 시작한 `expected_version`을 보존 |
| 가상 돌발 유입 | `external-arrivals`, `source=synthetic` 병원만 UI에서 조작 가능 |

수용 응답만으로 병원이 대화에 참여하지 않습니다. 이송자가 목적지를 확정해야 목록과 대화에 나타납니다. 이전 병원은 자기 참여 구간의 기록만 조회하며, 목적지 변경·도착 이후에는 새 메시지를 작성하지 않습니다. 자유대화에 “받겠습니다”라고 적어도 수용·배정 상태는 바뀌지 않습니다.

대화는 사건·메시지 커서를 각각 관리하고 추가 페이지를 읽습니다. ID로 중복을 제거하고 `order_key`로 정렬합니다. 전송 응답만으로 조회 커서를 이동하지 않습니다. 네트워크 오류는 같은 메시지 ID·본문·참여 구간으로 재시도하며, 서버가 상태 충돌을 알리면 최신 상태를 조회하고 사람의 재전송을 기다립니다. 화면에서 환자나 병원을 바꾸면 이전 조회 결과를 무시합니다.

병원에서 감시를 대신 켜거나 목적지를 변경하지 않습니다. 감시가 켜진 건의 `adjust` 판단은 대체 문의를 시작하지만, 새 목적지 확정은 이송자 화면에서 수행합니다. 감시가 꺼진 건은 기존 수동 판단 계약을 유지합니다. 조회·판단 사이 조건이 바뀌거나 `409` 충돌이 생기면 최신 사유를 다시 확인한 뒤 클릭해야 합니다.

자원의 신규 여유는 총량에서 실제 점유와 확정 이송 수요를 뺀 서버 값을 표시합니다. 신규 여유 0을 기존 환자의 치료 불가능으로 단정하지 않습니다. 관련 자원·역량의 재확인 사유는 환자별로 표시합니다. 공공 등록 정보와 병원 운영 자원을 혼합하지 않습니다.

수용 제한 등록·해제 화면(H03~H04 등), 실제 담당자 인증, 실시간 GPS·의료기관 연계는 아직 남아 있습니다. API 계약은 [이송 대화](../docs/CHAT_API.md), [수용 조건 재확인](../docs/TRANSPORT_REVIEW.md)을 참고하세요.
