# 환자·이송자 모바일 웹

각자 PC에서 실행하는 최신 방법은 [팀원 테스트 가이드](../docs/TEAM_TEST_GUIDE.md)를 따릅니다. 아래 포트별 기록은 이전 로컬 시연 예시입니다.


2026-10-13 공통 서버 연결본. 환자 등록부터 후보 탐색·수용 문의·사람 확정·출발·조건 재확인·대체 목적지 변경·이송 대화를 연결한다. 데이터는 공통 서버에만 저장하며 이 폴더에는 별도 DB나 백엔드가 없다.

## 원본과 실행

팀원 브랜치 `origin/Test_emergency_web_app`의 커밋 `181b99a613b943eed661ec8bc0f35bf9b71814d0`에 있는 `emergency-app/templates/index.html`, `templates/register.html`, `main.py`를 읽고 시작 화면, 흰색 카드, 회색 배경, 빨강 응급 버튼, 파랑 등록 버튼의 표현을 이어받았다. 원본 파일과 `.local-preview/patient-app/`는 수정하지 않았다. 기존 `emergency.db`, 이름·증상을 URL로 넘기는 원본 경로, 원본의 별도 FastAPI 저장 경로는 가져오지 않았다.

공통 서버가 이 폴더를 `/patient/`로 제공한다.

```text
http://127.0.0.1:18020/patient/
http://127.0.0.1:18020/patient/?pid=서버의환자ID
```

독립 정적 서버에서도 사용할 수 있다.

```powershell
python -m http.server 18021 --directory patient-ui
```

이 경우 `http://127.0.0.1:18021/?server=http://127.0.0.1:18020`으로 연결한다. 직접 파일을 여는 방식은 지원하지 않는다. `/patient/`로 제공된 화면은 같은 출처를 API 기본값으로 쓰고, 독립 정적 화면의 초기 기본값은 `http://127.0.0.1:18019`다. 하단 서버 설정에서 바꿀 수 있다. `server`와 `pid`만 URL·로컬 저장에 남으며 임상 입력, 연락처, 대화 원문은 로컬 저장하지 않는다. 재접속하면 환자 ID로 공통 서버 기록을 다시 조회한다.

## 연결된 화면 기능

- 환자 정보, 동의, 의식·호흡의 미확인 상태, 전문가·자기신고·미확인 우선순위, 직접 입력한 위치를 `POST /api/patients`로 저장한다. 이름·나이·연락처는 선택이다.
- 등록 병원의 `capabilities` 합집합으로 필요 진료 분야를 표시한다. 증상으로 치료 조건이나 중증도를 추론하지 않는다. 선택하지 않은 진료 분야는 미확인이다. 기본 수요는 `ed_bed: 1`, 전문팀 선택은 가상 자원 실험용 `special_team: 1`이다.
- 후보의 추천 근거, 확인 필요 항목, 갱신 시각과 이동시간을 표시한다. 후보·이동시간은 수용 보장이 아니다. 미확인 정보는 그대로 보여 주며 정상·수용 불가로 바꾸지 않는다.
- 수동 문의, 공개 정책 선택, 자동 문의 시작·일시정지, 미응답 강조, 허용된 재문의, 문의 취소, 병원 추가 질문 답변을 처리한다. 자동 문의 정책의 실제 선택 근거는 서버 워크플로의 최신 판단을 표시한다.
- `usable` 수용 응답으로 사람이 목적지를 확정한다. 출발은 별도 버튼이다. 수용 응답만으로 병원 대화 참여나 출발이 시작되지 않는다. 만료·조건·동시성 충돌은 서버에서 검증하고 화면에 오류를 유지한다.
- 확정 이후 조건 감시의 켜기·일시정지, 환자별 위험 근거, 대체 문의 상태를 표시한다. 직접 관측한 현재 위치는 별도 `transport-position` API로 전송한다. GPS·자동 추적이라고 표시하지 않는다.
- 현재 병원에서 `adjust`를 판단한 경우에만 대체 목적지 변경 버튼을 제공한다. 새 유효 수용 응답으로 사람이 변경한다. 이송자 화면에는 병원 수용 응답·재확인 판단·도착 확인을 대신 실행하는 시연 버튼이 없다.
- 이송 취소, 도착·종료 후 읽기 전용 대화, 현재 참여 병원 표시를 제공한다. 도착 확인은 병원 화면의 역할이다.

## 대화와 비동기 처리

독립 사건·메시지 커서로 모든 `has_more` 페이지를 읽고 `id`로 합친 뒤 `order_key` 세 정수로 정렬한다. POST 성공 항목을 즉시 합치지만 GET 커서를 앞당기지 않는다. 3초 폴링은 중복 실행하지 않으며 환자·서버 전환 이전의 상세·대화 응답은 화면에 적용하지 않는다.

발송 요청은 원래 `client_message_id`, 본문, 참여 구간을 고정한다. 새 전송 직전에 참여 구간을 확인하고, 변경됐으면 초안을 유지한 채 중단한다. POST 결과를 잃은 재시도는 같은 요청을 그대로 보내므로 서버의 중복 응답으로 성공을 확인할 수 있다. 요청을 새 병원으로 자동 바꾸지 않는다. 명시적인 실패 요청 취소 후 새 참여 병원을 확인해 새 메시지로 보낼 수 있다. 종료된 대화에서는 신규 전송을 막되, 결과가 불확실한 기존 요청의 정확한 재시도는 허용한다.

동적 원문은 HTML 이스케이프 또는 `textContent`로 표시한다. 질문 답변 초안과 입력 포커스를 폴링 때 유지한다. 서버 오류는 다음 명시적 조작 전까지 유지해 만료·상태 변경·잘못된 입력을 확인할 수 있다.

환자·서버를 전환하면 이전 환자의 위치 입력과 문의·대화 상태를 비우고 새 기록을 조회한다. 같은 환자를 폴링할 때는 편집 중인 위치를 유지한다. 변경 API가 실행 중일 때 처음 화면·불러오기·서버 연결 조작을 잠그며, 환자 생성 결과에도 생성 당시의 서버와 화면 세대를 확인해 이전 요청이 새 서버 화면에 반영되지 않도록 한다.

## 화면 검증용 선택자

| 과정 | 선택자 |
|---|---|
| 등록 시작·저장 | `#start-intake`, `#intake-form`, `#submit-intake` |
| 등록 항목 | `#patient-name`, `#age`, `#contact`, `#symptoms`, `#consciousness`, `#breathing`, `#priority-source`, `#priority`, `[name=capability]`, `#special-team`, `#intake-lat`, `#intake-lon`, `#consent` |
| 환자 상태 | `#patient-summary [data-patient-state]`, `#patient-id` |
| 후보 문의 | `.hospital-card[data-hospital-id] [data-action=inquire]` |
| 문의 상태·사람 확정 | `.inquiry-card[data-inquiry-id][data-state]`, `[data-action=confirm]`, `[data-action=change]` |
| 질문 답변 | `.question-form[data-iid]`, `.question-answer` |
| 초기 자동 문의 | `#inquiry-policy`, `#auto-start`, `#auto-pause`, `#workflow-state[data-state]` |
| 출발·조건 감시 | `#depart-button`, `#monitor-enable`, `#monitor-pause`, `#transport-review-state[data-state]` |
| 위치 직접 갱신 | `#position-lat`, `#position-lon`, `#position-submit` |
| 대화 | `#message-body`, `#send-message`, `#discard-message`, `#chat-items .chat-entry[data-item-id][data-kind][data-role]` |
| 취소·오류 | `#cancel-reason`, `#cancel-button`, `#error`, `#notice` |

API 원본: [공통 API](../docs/API.md), [대화 API](../docs/CHAT_API.md), [조건 재확인 API](../docs/TRANSPORT_REVIEW.md). 실제 의료기관 연계·의료진 사용성 검증·계정 인증은 이번 시연 범위에 없다.
