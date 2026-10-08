# 병원용 UI

병원 담당자가 환자별 수용 문의를 확인하고, 수용 가능 여부와 불가 사유를 응답하는 웹 화면입니다.

- 서버 연동 기준: [`docs/API.md`](../docs/API.md)
- 현재 서버 구현: [`server/`](../server/README.md)
- 공통 화면 흐름 참고: [`prototype/`](../prototype/)

## 실행

1. 저장소 루트에서 서버를 켭니다.
   ```powershell
   py -3 -m server.app --seed --scenario sudden --port 8000
   ```
2. `hospital-ui/index.html`을 브라우저로 엽니다(더블클릭). 빌드나 설치가 필요 없습니다.
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

아직 없는 화면: 도착 예정 환자·돌발 유입(H29~H39), 자원·제한 관리(H03~H11).
