# dev2 AI 시작 프롬프트

아래 내용을 새 GPT/Claude 대화의 첫 메시지로 붙여넣는다. 저장소에 접근할 수 있는 개발 환경에서 실행한다.

```text
당신은 이 저장소의 dev2 병원 웹 담당 개발 에이전트다. 설명만 제안하지 말고 아래 범위에서 재현·구현·검증까지 수행하라. 실명은 사용하지 말고 dev2라는 역할명만 사용하라.

먼저 현재 저장소의 작업 지침(AGENTS.md 등이 있으면 해당 범위의 지침)과 다음 문서를 읽어라.
- docs/team-handoff/20261102/README.md
- docs/team-handoff/20261102/COMMON_RUNTIME.md
- docs/team-handoff/20261102/CURRENT_STATE.md
- docs/team-handoff/20261102/dev2/README.md
- docs/team-handoff/20261102/dev2/AI_START.md (이 프롬프트 자체)

문서의 기준일은 2026-11-02, 확인 당시 main 커밋은 cef0230이다. 이를 최신이라고 가정하지 마라. 원격 main의 최신 커밋과 실제 코드를 먼저 확인하고, 문서와 다르면 현재 코드의 근거를 적어라. 공용 폴더의 미커밋 파일·로컬 브랜치·.codex-build 결과가 main에 포함됐다고 추정하지 마라. 기존 작업자의 변경을 삭제·리셋·덮어쓰기 하지 마라. 최신 원격 main에서 별도 codex/dev2-hospital-<짧은작업명> 브랜치로 작업하라. 공용 작업자의 변경과 충돌할 수 있으면 별도 clone/worktree를 사용하라.

첫 응답에는 읽은 기준 커밋, 병원 UI의 현재 구현 상태, 허용 수정 범위, H01 재현 계획을 간단히 요약한 뒤 바로 H01 재현부터 진행하라. 문서만 다시 쓰거나 계획만 제시하고 멈추지 마라. 이미 main에서 해결된 항목은 실제 코드와 테스트로 확인하고 남은 요구부터 처리하라.

수정 소유 범위:
- hospital-ui/의 병원 웹 코드
- 저장소 루트의 test_hospital* 테스트
- docs/team-handoff/20261102/dev2/ 문서
다른 폴더는 계약 이해를 위한 읽기만 허용한다. patient-ui/, paramedic-ui/, emergency-app/, server/, DB와 스키마, simulation/, 솔버와 배정 정책, 실험 결과·해시·잠금 파일, 공통 인계 문서는 수정하지 마라. 검사 스크립트도 수정하지 말고 기존 명령을 실행하라. 병원 기능은 별도 모듈로 만들고 index.html 연결은 최소화하라. 기존 operations.mjs와 workflow 모듈을 통째로 재작성하지 마라.

우선순위:

H01(최우선) — 채팅의 응답 유실 복구
현재 hospital-ui/index.html의 chatPending은 메모리뿐이고 resetChat에서 지워진다. 서버에 메시지가 저장됐으나 응답이 유실된 뒤 새로고침/환자 변경 시 원래 요청 ID가 사라지는지 먼저 재현하라. POST 전에 원래 서버·병원·환자·참여 구간과 본문을 복구 가능하게 저장하고, 복귀 뒤 동일 client_message_id/body/hospital_session_id/sender_name으로 명시 재시도하게 하라. 저장 실패면 POST하지 마라. 다른 범위로 재생하거나 저장된 표시명을 바꾸지 마라. 참여 종료 후 이미 저장된 요청은 duplicate:true로 확인하고, 종료 후 새 메시지는 막아라. 더블클릭, 늦은 응답, 네트워크 오류/5xx, 4xx, 복귀와 범위 변경을 테스트하라. POST 성공으로 GET 커서를 앞당기지 마라.
계약은 docs/CHAT_API.md, server/conversations.py와 현재 index.html의 pollChat/sendChat/resetChat을 읽어라.
GET /api/hospitals/{hid}/patients/{pid}/conversation?event_cursor=0&message_cursor=0&limit=100
POST /api/hospitals/{hid}/patients/{pid}/messages
본문 {body,client_message_id,hospital_session_id,sender_name}; 역할은 서버가 경로로 결정한다. can_send와 참여 구간, 두 조회 커서, has_more, ID 중복 제거, order_key 정렬을 유지하라. 403 NOT_PARTICIPANT와 409 STALE_SESSION/MESSAGE_CONFLICT/CONVERSATION_CLOSED를 성공으로 처리하거나 자동 우회하지 마라.

저장 범위는 합성 시연의 결과 미확인 원요청 한 건으로 제한하고 성공 확인 후 해당 요청만 정리한다. 채팅 전체 영구 사본을 쌓지 않는다. 다른 범위나 오래된 요청을 자동 재전송하지 않으며 저장 수명·정리 기준과 안내를 PR에 명시한다.

H02 — 병원 메인에서 임상변경 검토 발견
별도 patient-ui/transport-change.html/mjs에 병원 목록·유지·조정·반려 UI가 이미 있고 operations.mjs에서 링크한다. 전체 미구현이라고 다시 만들지 마라. 병원 메인에서 대기 검토를 발견하고 같은 환자·병원으로 이어지는 흐름을 보강하라. 다른 역할 파일은 수정하지 마라. 별도 화면을 재사용한다면 실제 query/actor 계약을 읽어라. 구급대 actor_id에 병원 작업자를 끼워 넣지 마라.
GET /api/hospitals/{hid}/transport-change-reviews
POST /api/hospitals/{hid}/transport-change-reviews/{rid}/decision
필수 본문 {actor_id,client_request_id,expected_review_version,expected_patient_version,decision_token,decision,confirmed:true,reason}; decision은 keep/adjust/reject다. 조회한 version/current_patient_version/decision_token을 그대로 사용하라. 사유·명시 확인, can_decide/blockers/monitoring_enabled를 존중하라. 감시 활성화는 이송자 역할에 맡겨라. 409면 재조회하고 확인을 해제한 뒤 사람이 재판단하게 하라. 응답 유실은 원래 ID·작업자·본문으로만 재시도하라. 반려는 기존 정보 유지, 승인/조정은 자동 목적지 변경이 아니다. 지원 외 자원을 자동 승인하지 마라.
docs/ENROUTE_PATIENT_CHANGE_20261030.md와 server/enroute_patient_review.py를 읽어라.

H03(후순위) — 수동 정산 확인과 감사 조회
H01/H02 검증 뒤 진행하라. 현재 병원 UI에 reconcile-accounting과 감사 조회는 연결되지 않았다.
POST /api/hospitals/{hid}/patients/{pid}/reconcile-accounting
POST /api/hospitals/{hid}/external-arrivals/{eid}/reconcile-accounting
GET /api/hospitals/{hid}/accounting-reconciliations 및 /{id}
본문 {request_id,reason,handled_by,expected_version,expected_resource_versions,confirm_no_resource_release:true}. expected_resource_versions는 병원 운영 자원 전체의 최신 버전이다. 공공 관측값을 넣지 마라. 정상 퇴실의 서버 409를 자동으로 이 API로 우회하거나 자원을 직접 차감하지 마라. 별도 명시 확인과 사유, 동일 요청 재시도, 버전 충돌 재검토, 성공 후 상세 재조회와 감사 페이지를 구현하라. 성공 시 추가 차감은 0이다.
docs/ACCOUNTING_RECONCILIATION.md를 먼저 읽어라.

README 갱신(후순위, 11-06 추가) — hospital-ui/README.md 실행 안내
H01~H03과 다른 PR로 진행하라. 지금 README의 `server.app --seed`는 기존 DB 파일을 지운다. docs/RUNNING.md의 새 DB 경로 server.chat_demo 실행과 /hospital/?hid=H01 기준으로 고치고, 수용 제한 화면을 미구현으로 적은 문장을 현재 상태에 맞게 바꿔라. 세부 기준은 dev2/README.md에 있다.

서버 의존 사항:
- 제한 해제 route가 DELETE의 handled_by를 사용하지 않아 감사 actor가 기본 hospital로 남는 문제가 있다. 서버 코드를 고치지 말고 재현·요청·응답을 서버 담당자에게 전달하라.
- 제한 연장은 현재 POST→DELETE 두 요청이고 등록에 멱등 요청 ID 계약이 없다. 응답 유실을 새 POST 자동 반복으로 처리하지 마라. 서버 계약 협의가 필요한 부분을 분리하라.
- 병원 ID/actor ID는 실제 로그인 인증이 아니다. 지원 모델 자원은 ed_bed/special_team이며 지원 외 자원을 지원하는 것처럼 표시하지 마라.
- 수용 제한·외부 유입 개별 퇴실·추천·중요 알림은 이미 구현돼 있다. 과거 README 문구만 보고 미구현으로 되돌리지 마라.
- 미도착 UI는 별도 후속 범위이며 H01~H03에 임의로 섞지 마라.

검증은 새로 만든 합성 DB만 사용하라. 공유/실제/공공 DB를 수정하거나 초기화하지 마라. 실제 병원·전화·길찾기·음성 외부 서비스를 호출하는 평가를 수행하지 마라. 자신의 새 .codex-build/dev2-* 또는 임시 폴더에만 검증 출력을 둬라. 공통 런타임 문서의 설치·실행 방법과 고정 환경을 따라라.

저장소 루트의 현재 회귀 명령:
node --test test_hospital_operations.mjs test_hospital_workflow.mjs test_patient_workflow_transport_change_retry.mjs
python -B -m unittest test_conversations test_enroute_patient_review test_accounting_reconciliation -v
python scripts/test_suites.py core --node "<실제 node.exe 경로>"

인계 시 첫 Node 명령 50개 통과를 확인했지만 현재 작업에서도 다시 확인하라. 새 H01/H02/H03에는 test_hospital* 테스트를 추가하라. 실제 HTTP 운영 모듈 검증은 python test_hospital_operations_http.py --node "<실제 node.exe 경로>" --output "<존재하지 않는 새 출력 폴더>"로 실행하라. test_hospital_operations_http.mjs를 단독 실행하지 마라. 이 harness는 기존 운영 모듈 검사이므로 새 기능의 완료 검증을 대신하지 않는다. 실행하지 않은 검사·브라우저·다른 PC·실제 사용자 검증을 통과했다고 쓰지 마라.

구현과 검증을 마치면 결과를 다음 5개 항목으로 보고하라.
1. 확인한 main 커밋, 작업 브랜치, 완료한 H 번호.
2. 사용자 동작의 변경과 재현 전후 결과.
3. 변경 파일과 소유 범위 준수 여부.
4. 실행한 명령·실제 결과·합성 DB/증거 위치와 실행하지 않은 검사.
5. 남은 문제와 서버/다른 역할 담당자에게 필요한 요청.
```

사람이 확인할 세부 완료 기준은 [dev2 작업 안내](README.md), 공통 실행은 [실행/API](../COMMON_RUNTIME.md), 다른 담당자와의 경계는 [공통 안내](../README.md), 반영 상태는 [현재 상태](../CURRENT_STATE.md)를 따른다.
