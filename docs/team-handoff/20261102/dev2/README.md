# dev2 병원 웹 작업 안내

기준일: 2026-11-02. 확인한 main 기준 커밋은 `cef0230`이다. 작업을 시작할 때 원격 main과 실제 코드를 다시 확인한다. [공통 안내](../README.md) · [실행/API](../COMMON_RUNTIME.md) · [현재 상태](../CURRENT_STATE.md) · [AI 시작 프롬프트](AI_START.md)

dev2는 병원 담당자가 문의를 처리하고 이송 중인 환자를 확인하는 `hospital-ui/`를 맡는다. 우선순위는 **H01 채팅 복구 → H02 메인에서 임상변경 검토 발견 → H03 정산·감사 화면**이다. 새 화면을 크게 다시 만드는 대신 현재 흐름에 필요한 기능을 붙인다.

## 먼저 구분할 현재 상태

다음은 위 커밋의 코드에 이미 있는 기능이다. 로컬 미공개 구현을 완료 근거로 사용하지 않았다.

| 영역 | 현재 main에서 확인한 동작 | 확인할 코드 |
|---|---|---|
| 문의 처리 | 3초 조회, 환자 상세, 추가 질문·답변, 확인 중, 수용·불가, 문의 버전 충돌 처리 | `hospital-ui/index.html`: `poll`, `respond`, `renderDetail` |
| 이송·자원 | 도착 예정, 유지·조정 판단, 도착·퇴실, 운영 자원 수정, 수용 제한 등록·해제·연장 | 같은 파일: `hospitalAction`, `saveResource`, `changeRestriction` |
| 병원 대화 | 참여 구간별 조회, 사건·메시지 별도 커서, 중복 제거·정렬, 현재 페이지에서 동일 요청 재시도 | 같은 파일: `pollChat`, `sendChat`, `resetChat` |
| 운영 기록 | 통화 기록·원문 보존 정정·내용 확인, 직접 업무 시작·종료, 외부 유입 목록·개별 퇴실 | `hospital-ui/operations.mjs` |
| 추천·알림 | 선택 환자의 추천·현재 입력 검토, 중요 알림의 수신과 명시 확인 구분 | `hospital-ui/workflow-state.mjs`, `workflow-panel.mjs` |
| 임상변경 검토 | 통화·계측 탭에서 별도 화면으로 이동 가능. 별도 화면에 병원 목록과 유지·조정·반려 판단이 있음 | `operations.mjs`: `render`; 읽기 참고용 `patient-ui/transport-change.{html,mjs}` |

기존 `hospital-ui/README.md`의 “수용 제한 미구현” 같은 과거 문구와 오래된 작업표보다 실제 코드를 우선한다. 수용 제한, 외부 유입 개별 퇴실, 추천, 임상변경 검토 전체를 새로 구현할 과제로 잡지 않는다.

이 안내를 작성한 작업은 문서 인계이며 H01~H03 구현을 완료한 것이 아니다. 공용 작업 폴더의 미커밋 파일·로컬 브랜치·`.codex-build` 결과는 main과 별도다. 최신 main에 이미 합쳐졌는지 확인하기 전에는 복사하거나 완료로 기록하지 않는다.

## 수정 범위와 협업 경계

- 작업 브랜치: 최신 원격 main에서 `codex/dev2-hospital-<짧은작업명>`을 만든다. 다른 사람의 미커밋 변경이 있으면 덮어쓰지 말고 별도 작업 폴더를 쓴다.
- 코드: `hospital-ui/`만 수정한다. 기능은 별도 모듈로 만들고 `index.html` 연결을 작게 유지한다.
- 테스트: 저장소 루트의 `test_hospital*`만 추가·수정한다. 기존 검사 목적을 약하게 만들지 않는다.
- 문서: `docs/team-handoff/20261102/dev2/`만 수정한다. 공통 문서의 수정 요청은 공통 담당자에게 전달한다.
- 읽기는 API 이해에 필요한 다른 폴더까지 가능하다. `patient-ui/`, `paramedic-ui/`, `emergency-app/`, `server/`, DB·스키마, `simulation/`, 솔버·배정 정책, 실험 결과·해시·잠금 파일은 수정하지 않는다.
- 검증 데이터는 새로 만든 합성 DB만 사용한다. 실제·공유·공공 DB를 시드 초기화하거나 수정하지 않는다. 실행별 검증 출력은 자신의 새 `.codex-build/dev2-*` 폴더 또는 임시 폴더에 둔다.

## H01 — 최우선: 채팅의 응답 유실 복구

**현재 문제:** `index.html`의 `chatPending`은 메모리 변수다. 전송 결과를 모르는 상태에서 새로고침하거나 다른 환자를 선택하면 `resetChat`으로 원래 메시지 ID·본문·참여 구간이 사라진다. 서버는 정확한 재전송을 지원하지만 화면은 이를 지속해서 복구하지 못한다.

**구현 범위:** `hospital-ui/`에 채팅 상태·저장·재시도 모듈을 추가하고 기존 채팅에 연결한다. 새 `tests/browser/test_hospital_chat*.mjs` 등 병원 테스트를 추가한다. 기존 통화·업무 계측과 중요 알림 모듈을 함께 재작성하지 않는다.

**완료 기준:**

1. POST 전에 원래 서버·병원·환자·참여 구간과 요청 본문을 복구 가능한 저장소에 보존한다. 저장 실패 시 복구 불가능한 POST를 시작하지 않는다.
2. 응답 유실 → 새로고침 → 원래 범위 복귀 후 같은 `client_message_id`, `body`, `hospital_session_id`, `sender_name`으로 명시 재시도한다. 다른 환자·병원·서버의 요청을 보내지 않는다. 담당자 표시명을 바꿔도 저장된 본문을 고쳐 재전송하지 않는다.
3. 이미 저장됐지만 참여가 종료된 메시지의 정확한 재전송은 `duplicate:true`로 확인한다. 참여 종료 뒤 새 메시지는 차단한다.
4. 더블클릭으로 중복 POST를 만들지 않는다. 4xx로 거절된 요청은 자동 재생하지 않고 최신 상태를 확인하게 한다. 네트워크 오류·5xx의 결과 미확인 요청은 원래 내용으로 유지한다.
5. 늦게 도착한 응답이 다른 선택의 화면을 바꾸지 않는다. POST 성공만으로 조회 커서를 앞당기지 않는다. 저장 실패·응답 유실·복귀·범위 변경·참여 종료·409를 자동 테스트한다.

**대화 API:**

```text
GET /api/hospitals/{hid}/patients/{pid}/conversation
    ?event_cursor=0&message_cursor=0&limit=100
POST /api/hospitals/{hid}/patients/{pid}/messages
```

```json
{"body":"확인할 내용","client_message_id":"hospital-dev2-message-001","hospital_session_id":7,"sender_name":"담당자 표시명"}
```

`hospital_session_id`는 조회의 `active_hospital_session.id`를 사용한다. `sender_role`은 서버가 경로로 결정한다. 같은 ID의 본문·구간·명시한 이름을 바꾸면 충돌한다. `can_send`를 따른다. 항목은 `id`로 합치고 `order_key`로 정렬하며, GET의 `cursors.event_id/message_id`를 각각 유지한다. `has_more`면 추가 페이지를 읽는다.

`body`는 빈 값이 아닌 최대 2,000자, `client_message_id`는 영문·숫자·`_ . : -`로 된 1~128자다. `sender_name`은 선택 항목이며 최대 80자다. 예시의 ID·구간 숫자는 재사용하지 말고 실제 생성·조회값을 사용한다.

| 오류 | 화면 처리 |
|---|---|
| 400 `INVALID` | 입력·식별자·구간 확인 |
| 403 `NOT_PARTICIPANT` | 새 발송 차단, 참여 상태 안내 |
| 409 `STALE_SESSION` | 재조회 후 사람이 새 참여 구간을 확인 |
| 409 `MESSAGE_CONFLICT` | 같은 ID의 다른 내용으로 재전송하지 않음 |
| 409 `CONVERSATION_CLOSED` | 읽기 전용 전환 |

자세한 계약은 `docs/CHAT_API.md`와 `server/conversations.py`를 읽는다. 채팅의 “수용합니다”는 수용 응답이나 목적지 확정이 아니다.

저장 범위는 합성 시연의 결과 미확인 원요청 한 건으로 제한하고 성공 확인 후 해당 요청만 정리한다. 채팅 전체 영구 사본을 쌓지 않는다. 다른 범위나 오래된 요청을 자동 재전송하지 않으며 저장 수명·정리 기준과 안내를 PR에 명시한다.

## H02 — 메인에서 임상변경 검토 발견

**현재 문제:** 별도 검토 화면과 선택 환자의 중요 알림은 이미 있지만, 병원 메인은 병원별 변경 검토 목록을 직접 조회하지 않는다. 기존 별도 화면 링크는 병원 ID만 전달한다. 담당자가 어떤 환자의 검토를 처리해야 하는지 발견하고 같은 환자로 이어지는 흐름을 보강한다.

**구현 범위:** 병원 전용 목록·표시 모듈과 `index.html`의 최소 연결, `test_hospital*` 테스트. 다른 역할의 화면은 수정하지 않는다. 기존 별도 화면을 재사용할 때는 실제 진입 계약을 먼저 읽고 `pid/hid`를 보존한다. 구급대용 `actor_id`에 병원 작업자를 대신 넣지 않는다. 병원 메인에 판단을 붙일 경우 아래 병원 판단 계약을 그대로 사용한다.

```text
GET /api/hospitals/{hid}/transport-change-reviews
POST /api/hospitals/{hid}/transport-change-reviews/{rid}/decision
```

```json
{"actor_id":"hospital-dev2-demo","client_request_id":"hospital-dev2-review-001","expected_review_version":1,"expected_patient_version":3,"decision_token":"목록에서 받은 토큰","decision":"keep","confirmed":true,"reason":"직접 확인한 판단 사유"}
```

`decision`은 `keep/adjust/reject`다. 표시된 검토의 `version`, `current_patient_version`, `decision_token`을 사용한다. 실제 조회값을 예시 숫자로 대체하지 않는다. actor ID는 로그인 대신 쓰는 시연 식별자이며 기존 표시명과 구분한다.

목록 응답은 `{reviews:[...]}`이며 현재 서버는 최신 100건만 반환한다. 서버에 없는 페이지·필터 API를 추정해서 만들지 않는다. 더 오래된 대기 건까지 보장해야 하면 서버 담당자와 조회 계약을 먼저 협의한다.

**완료 기준:**

1. 현재 병원의 대기 검토를 발견할 수 있고 다른 병원·이전 조회의 결과가 섞이지 않는다. 환자 ID·변경 전후·검토 상태·blocker를 표시한다.
2. 판단 전 사유와 명시 확인이 필요하다. `keep/adjust`는 `can_decide`와 `monitoring_enabled`를 확인한다. 병원 화면이 이송자의 감시를 대신 켜지 않는다.
3. 409이면 최신 목록과 조건을 다시 읽고 이전 확인을 해제한다. `ENROUTE_REVIEW_STALE`, `ENROUTE_REVIEW_CLOSED`, `REVIEW_DISABLED`, `VOICE_REQUEST_CONFLICT`를 무조건 재시도하지 않는다.
4. 응답 유실은 원래 작업자·경로·ID·본문으로만 재시도한다. 반려는 환자정보를 유지하고, 승인·조정 자체로 목적지를 바꾸지 않는다. 수용 확인·알림 확인·통화 내용 확인을 이 판단으로 대체하지 않는다.
5. 지원 외 자원의 `UNSUPPORTED_SOLVER_RESOURCE`를 숨기거나 자동 승인하지 않는다. 현재 모델의 자원 범위는 `ed_bed/special_team`이다.

계약은 `docs/ENROUTE_PATIENT_CHANGE_20261030.md`, `server/enroute_patient_review.py`의 `list_reviews/get/decide`를 읽는다. 요청 대기 중에는 환자정보가 바뀌지 않으며, 승인 후에만 서버가 반영한다. 새 목적지는 구급대의 기존 확정 절차로 정한다.

## H03 — 후순위: 수동 정산 확인과 감사 조회

**현재 문제:** 병원 UI에는 무차감 종결 API와 감사 목록·상세 호출이 없다. 정상 퇴실이 집계 충돌이나 과거 원장 문제로 거절된 경우 정식 확인 절차가 필요하다. H01/H02 이후 진행한다.

**구현 범위:** `hospital-ui/`의 별도 정산 패널, 최소 연결, `test_hospital*`. 외부 유입 선택은 기존 `operations.mjs`와 연결하되 합계 점유를 직접 차감하지 않는다.

```text
POST /api/hospitals/{hid}/patients/{pid}/reconcile-accounting
POST /api/hospitals/{hid}/external-arrivals/{eid}/reconcile-accounting
GET  /api/hospitals/{hid}/accounting-reconciliations?kind=all&limit=100
GET  /api/hospitals/{hid}/accounting-reconciliations/{id}
```

```json
{"request_id":"생성한 UUID","reason":"운영 점유 정산을 확인한 근거","handled_by":"담당자 표시명","expected_version":1,"expected_resource_versions":{"ed_bed":3,"special_team":2},"confirm_no_resource_release":true}
```

**완료 기준:** 대상 최신 버전과 **병원 운영 자원 전체**의 버전을 조회한 뒤 담당자가 별도로 확인·사유를 입력한다. 정상 퇴실의 409를 자동으로 이 API로 우회하지 않는다. 응답 유실은 원래 ID·본문으로 재시도한다. 400은 입력, 404는 범위, 409 `CONFLICT/INVALID_STATE`는 최신 상태를 재확인한다. 성공은 추가 자원 차감 0이며 일반 환자는 `COMPLETED`, 외부 유입은 `RECONCILED`다. 성공 응답은 대상 요약이므로 상세·목록을 다시 읽는다. 감사 목록의 `next_before_id`를 `before_id`로 보내며 다른 병원 기록을 표시하지 않는다.

계약은 `docs/ACCOUNTING_RECONCILIATION.md`를 따른다. 공공 병상 관측값은 `expected_resource_versions`에 넣지 않는다. 이 API는 실제 정산을 수행하는 기능이 아니라 담당자의 정산 확인 후 추가 차감 없이 기록을 종결하는 기능이다.

## README 실행 안내 갱신 — 후순위 (2026-11-06 추가)

**현재 문제:** `hospital-ui/README.md`의 실행 절차는 10-13 시연 기준이다. 그대로 따라 하면 기존 DB 파일이 지워진다. 11-05 문서 정리(PR #20)는 팀원 폴더를 고치지 않았으므로 dev2가 직접 갱신한다. H01~H03과 다른 PR로 진행한다.

| README의 현재 문구 | 문제 | 바꿀 내용 |
|---|---|---|
| `py -3 -m server.app --seed --scenario sudden --port 8000` | `--seed`는 `--db`(기본 `data/demo.db`)와 `-wal`·`-shm` 파일을 지운 뒤 가상 병원 10곳으로 새로 만든다 | [실행 안내](../../../RUNNING.md#가상-환자병원-화면-시연)처럼 새 DB 경로로 `server.chat_demo` 실행 |
| `?hid=H07`, "H02, H07에 대기 문의" | `sudden` 시나리오에만 맞는 값 | 새 실행의 `/hospital/?hid=H01`과 구급대 화면에서 문의한 건 |
| "수용 제한 등록·해제 화면(H03~H04 등)… 아직 남아 있습니다" | 지금 병원 화면에는 수용 제한 탭이 있다 | 남은 범위는 [현재 상태](../../../PROJECT_STATUS.md)로 연결 |

**완료 기준:** README만 따라 새 DB로 서버를 켜고 병원 화면에서 문의를 확인할 수 있다. 기존 DB를 지우는 명령이 남아 있지 않다. Codex 정리안(커밋 `7f9d27e`)의 [hospital-ui/README.md](https://github.com/Uree1229/emergency-care-system/blob/7f9d27ea582b74c1c894a0d508ef12f903afc97d/hospital-ui/README.md)를 초안으로 참고할 수 있다.

## 서버 담당자에게 전달할 의존 사항

- **제한 해제 감사 담당자:** UI의 `changeRestriction`은 DELETE 본문에 `handled_by`를 보내지만 `server/app.py`의 제한 해제 route는 본문을 읽지 않고 `clear_restriction` 기본 actor `hospital`을 사용한다. 화면 본문만 바꿔 해결할 수 없다. 재현과 실제 요청을 서버 담당자에게 전달한다.
- **제한 연장·응답 유실:** 현재 POST로 새 제한 생성 후 DELETE로 이전 제한 해제한다. 등록에는 요청 ID 중복방지 계약이 없고 두 요청은 원자적이지 않다. 결과 미확인 상태에서 새 POST를 자동 반복하지 않는다. 원자적 연장이나 멱등 등록은 서버 계약 협의가 먼저다.
- 병원 ID·actor ID는 실제 사용자 인증·병원별 로그인 권한을 구현하지 않는다. dev2 범위에서 인증 완료라고 표시하지 않는다.
- 미도착 `POST /api/hospitals/{hid}/patients/{pid}/no-show`는 서버에 있고 병원 UI에는 없다. H01~H03과 분리된 후속 범위다. 자동 미도착 판단을 추가하지 않는다.

서버 오류를 발견하면 요청·응답·재현 순서·기대 계약을 기록한다. 서버/DB/배정 로직을 직접 고치거나 프론트에서 성공으로 꾸미지 않는다.

## 검증과 완료 보고

저장소 루트에서 실행한다. 설치·서버 실행·고정 런타임은 [실행/API](../COMMON_RUNTIME.md)를 따른다. `npm test`나 별도 프론트 빌드가 필요한 구조가 아니다.

```powershell
node --test tests/browser/test_hospital_operations.mjs tests/browser/test_hospital_workflow.mjs tests/browser/test_patient_workflow_transport_change_retry.mjs
python -B scripts/test_suites.py python test_conversations test_enroute_patient_review test_accounting_reconciliation -v
python scripts/test_suites.py core --node "<node.exe 경로>"
```

첫 명령은 이 인계 시점에 **50개 통과**를 확인했다. 병원 모듈 40개와 별도 임상변경 재시도 10개다. 두 Python 명령은 여기서 새로 실행한 결과가 아니라 후속 검증 명령이다. `core`는 공통 문서의 고정 Python/SQLite/OR-Tools 환경에서 실행해야 한다. 새 테스트도 `test_hospital*`로 지으면 현재 제품 테스트 묶음에 포함된다.

실제 HTTP 운영 모듈 검증은 아래 harness가 새 합성 DB와 loopback 서버를 만든다. 출력 경로가 이미 있으면 거절하므로 실행마다 새 이름을 쓴다. `test_hospital_operations_http.mjs`를 단독 실행하지 않는다.

```powershell
python tests/core/test_hospital_operations_http.py --node "<node.exe 경로>" --output ".codex-build/dev2-hospital-http-20261102-01"
```

위 명령은 기존 운영 모듈 검증이며 새 H01/H02/H03의 완료 증거를 대신하지 않는다. 새 기능에는 해당 오류·복구 경로의 테스트를 추가하고 새 합성 DB에서 실제 화면/API 연결을 확인한다. 50개 통과는 병원 메인 전체의 브라우저·다른 PC·실제 의료기관 검증을 뜻하지 않는다.

PR 또는 작업 결과는 다음 5개로 보고한다.

1. 확인한 main 커밋과 작업 브랜치, 완료한 H 번호.
2. 바뀐 사용자 동작과 재현 전후 결과.
3. 변경 파일과 소유 범위 준수 여부.
4. 실행한 검증 명령·실제 결과·합성 DB/증거 위치, 실행하지 않은 검사.
5. 남은 문제와 서버/다른 역할 담당자에게 필요한 요청.
