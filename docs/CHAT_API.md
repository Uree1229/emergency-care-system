# 이송 대화 API: 모바일·병원 화면 연결

2026-10-12 서버 구현, 2026-10-13 `patient-ui/`·`hospital-ui/` 화면 연결. 실제 역할별 화면은 서버의 `/patient/`, `/hospital/`에서 연다. [실행과 통합 검증](UI_INTEGRATION.md)을 참고한다. 환자 생성·문의·수용·목적지 확정 API는 [공통 API](API.md)를 사용한다. 초기 후보 자동 문의는 별도 [문의 워커](INQUIRY_WORKFLOW.md)가 처리하며, 대화 API는 결과를 보여 준다.

## 로컬 시연 화면

```powershell
python -m server.chat_demo
```

브라우저에서 `http://127.0.0.1:18016/conversation-demo.html`을 연다. 가상 데이터 전용 `data/chat-demo.db`를 사용하며 기존 공공 자료 DB는 사용하지 않는다. 이송자·병원 화면을 나란히 표시하고 실제 API로 문의·회신·확정·출발·대화·재배정·도착을 시연한다. 상단 단계 버튼은 가상 역할들의 행동을 실행하는 시연 조작이며 자동 배정 알고리즘이 아니다. 두 화면에서 각 역할을 직접 조작하는 연결본은 [UI 통합](UI_INTEGRATION.md)을 사용한다.

## 시작과 참여

- 환자를 생성하면 이송 건당 대화방 하나가 함께 생성된다. 기존 DB도 서버 시작 시 삭제 없이 확장한다.
- 문의·회신·확정·출발·조정·도착 등 기존 사건이 시스템 항목으로 나타난다. 수용 응답만으로 병원 참여가 시작되지는 않는다.
- 목적지 확정 성공 시 선택 병원이 참여한다. 이송자는 같은 대화방을 계속 사용한다.
- 조정 요청 중에는 기존 병원과 계속 대화한다. 새 목적지 변경이 성공하면 기존 참여 종료와 새 병원 참여가 함께 반영된다.
- 도착 확인·취소·미도착 처리 시 대화가 종료된다. 이전 기록은 조회할 수 있으나 새 메시지는 작성할 수 없다.
- 병원은 자기 참여 구간의 메시지와 사건만 조회한다. 새 병원에는 최신 환자 요약과 조정 사유를 제공하며, 다른 병원과의 자유대화는 제공하지 않는다.

로그인 없는 기존 시연 조건을 유지한다. 병원 ID와 참여 구간을 검사하지만 담당자의 실제 신원을 인증하는 기능은 아니다.

## API 네 경로

| 동작 | 경로 |
|---|---|
| 모바일 대화 조회 | `GET /api/patients/{pid}/conversation` |
| 모바일 메시지 전송 | `POST /api/patients/{pid}/messages` |
| 병원 대화 조회 | `GET /api/hospitals/{hid}/patients/{pid}/conversation` |
| 병원 메시지 전송 | `POST /api/hospitals/{hid}/patients/{pid}/messages` |

조회 쿼리: `event_cursor=0&message_cursor=0&limit=100`. 커서는 0 이상의 정수, `limit`은 1~200이다. 두 스트림에서 각각 최대 `limit`개를 가져오므로 한 응답에는 최대 `2 × limit`개의 항목이 들어온다.

| 조회 응답 필드 | 화면 사용법 |
|---|---|
| `conversation` | `id`, `patient_id`, `state`(`OPEN`/`CLOSED`), `created_at`, `closed_at` |
| `patient` | 모바일·현재 병원에는 최신 환자 요약. 이전 병원에는 참여 당시 스냅샷. 이전 DB에 스냅샷이 없으면 `historical_snapshot_unavailable: true` |
| `inquiries` | 모바일의 병원별 진행 카드. 기존 문의 상태·`overdue`·`can_retry`·`usable` 포함. 병원 응답에서는 빈 배열 |
| `hospital_sessions` | 모바일은 전체 참여 구간, 병원은 자기 구간만 반환 |
| `active_hospital_session` | 현재 참여 병원의 `id`, `hospital_id`, `hospital_name` 등. 미확정 또는 이전 병원 조회에서는 `null` |
| `handoff` | 마지막으로 보이는 참여 구간의 `note`, `joined_at`. 새 병원은 이전 조정 사유 확인 가능 |
| `can_send` | 새 메시지 전송 허용 여부. 이전 병원과 종료된 대화에서는 `false` |
| `items` | 시스템 사건과 자유메시지의 혼합 목록 |
| `cursors` | 다음 요청에 보낼 `event_id`, `message_id` |
| `has_more`, `more` | 더 읽을 항목이 있는지. `more.events`, `more.messages`로 각각 확인 |
| `poll_interval_seconds` | 기본 3초 |

`PENDING`은 서버에 문의가 등록됐다는 뜻이다. 병원의 `checking` 회신이 있어야 확인 중으로 표시한다. 읽음·접속 중·입력 중 정보는 제공하지 않는다.

## 메시지 전송

먼저 조회한 `active_hospital_session.id`를 본문에 넣는다. 병원 미확정 단계에서 이송자가 기록을 남기는 경우에는 `hospital_session_id: null`을 명시한다. 미확정 단계의 자유기록은 이후 참여 병원에 자동 공개되지 않으며 알고리즘 입력에도 반영되지 않는다.

```json
{
  "body": "환자는 의식이 있고 약 12분 후 도착 예정입니다.",
  "client_message_id": "mobile-20261012-001",
  "hospital_session_id": 7,
  "sender_name": "이송자"
}
```

- `body`: 비어 있지 않은 문자열, 최대 2,000자. 일반 텍스트로 렌더링하며 HTML로 삽입하지 않는다.
- `client_message_id`: 전송 전에 생성하는 1~128자 식별자. 영문·숫자·`_ . : -` 사용 가능. 브라우저 `crypto.randomUUID()` 권장.
- `hospital_session_id`: 필수. 미확정은 `null`, 확정 후에는 현재 참여 구간 ID.
- `sender_name`: 선택, 최대 80자. 생략하면 신고자 역할 또는 병원명을 사용한다.
- `sender_role`은 서버가 경로에 따라 `patient`/`hospital`로 정한다. 본문으로 역할이나 시스템 메시지를 지정할 수 없다.

성공 응답은 `{ "message": { ... }, "duplicate": false }`다. 같은 요청 재전송은 원래 메시지와 `duplicate: true`를 반환한다. 같은 식별자로 내용·구간·명시한 이름을 바꾸면 409 오류다. 식별자는 대화방과 발신 역할 안에서 고유해야 하며 새 병원 참여 때도 새 식별자를 만든다.

참여가 끝난 병원도 이미 저장한 자기 메시지의 정확한 재전송은 성공 확인을 받을 수 있다. 새 메시지 저장은 차단된다. 이는 응답을 잃어 재시도한 경우에 중복 저장이나 잘못된 실패 표시를 막기 위한 처리다.

## 사건과 메시지 항목

```json
{
  "id": "event:123",
  "kind": "system",
  "event_id": 123,
  "type": "inquiry_responded",
  "sender_role": "system",
  "sender_name": "시스템",
  "at": 1790000000,
  "text": "시연병원에서 수용 가능으로 응답했습니다. 목적지 확정 전입니다.",
  "hospital_id": "H01",
  "hospital_name": "시연병원",
  "data": {"inquiry_id": 5, "action": "accept", "reason": null, "warning": null},
  "order_key": [123, 0, 123]
}
```

자유메시지는 `id: "message:45"`, `kind: "message"`, `message_id`, `conversation_id`, `hospital_session_id`, `sender_role`, `sender_name`, `body`, `text`, `at`, `client_message_id`, `order_key`를 반환한다. 질문·답변 원문은 신규 `inquiry_question.data.question`, `inquiry_answered.data.answer`에 보존된다. 과거 원문이 없는 사건에서 현재 질문으로 과거 내용을 추정하지 않는다.

## 폴링·재접속·페이지 처리

1. 처음에는 두 커서를 0으로 조회한다.
2. 항목을 `id` 기준으로 합치고, 전체 보관 목록을 `order_key` 세 정수의 사전식 순서로 정렬한다.
3. 응답의 `cursors.event_id`, `cursors.message_id`를 각각 다음 요청에 사용한다.
4. `has_more: true`이면 같은 방식으로 다음 페이지를 바로 읽는다. 모두 읽은 뒤 3초 간격 조회로 전환한다.
5. POST 성공 항목은 즉시 같은 목록에 합친다. 이후 GET에 다시 나타나도 ID 기준으로 중복 표시하지 않는다. **POST 성공만으로 조회 커서를 앞당기지 않는다.**
6. 네트워크 오류 시 커서를 유지하고 재조회한다. POST 재시도는 기존 `client_message_id`와 본문을 그대로 쓴다.

두 스트림의 페이지는 독립적이므로 페이지를 단순히 끝에 붙이면 전체 순서가 틀릴 수 있다. 추가 페이지를 읽을 때마다 누적 목록을 다시 정렬한다. 화면을 다시 열 때 전체 기록을 비웠다면 커서도 0부터 시작한다. 참여 병원이 변경되면 발송 전 대화를 다시 조회하고 새 구간 ID를 확인한다.

| 오류 | HTTP | 대응 |
|---|---|---|
| `INVALID` | 400 | 내용·식별자·구간·커서 입력 수정 |
| `NOT_FOUND` | 404 | 환자·병원 ID 확인 |
| `NOT_PARTICIPANT` | 403 | 아직 참여 전이거나 참여가 종료된 병원. 발송 차단 |
| `STALE_SESSION` | 409 | 대화 재조회. 원래 대상과 달라졌으므로 사용자 확인 후 새 메시지로 전송 |
| `MESSAGE_CONFLICT` | 409 | 같은 ID의 다른 요청. 기존 전송 결과를 확인 |
| `CONVERSATION_CLOSED` | 409 | 읽기 전용으로 전환 |

수용·불가·목적지 확정·출발·조정·환자정보 수정은 기존 구조화된 API로 처리한다. 채팅에 “받겠습니다”라고 입력해도 수용 상태는 바뀌지 않는다. 자유메시지는 환자 입력 버전이나 배정 재계산을 변경하지 않는다.

## 저장·검증

`conversations`, `conversation_hospital_sessions`, `chat_messages`를 추가했다. 병원 참여는 시각만으로 나누지 않고 사건 ID 경계로 구분해 같은 초의 재배정도 처리한다. 메시지는 저장 당시의 사건 ID를 순서 기준으로 보관한다.

```powershell
python -m unittest test_conversations test_server test_planning -v
```

테스트는 임시 가상 DB를 사용한다. 병원 참여 시점, 재배정 전후 조회 범위, 동시 재전송, 기록 페이지 처리, 기존 DB 확장, 업무 상태와 배정 버전 유지, HTTP 경로를 검증한다.
