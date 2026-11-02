# 공통 실행 환경과 통신 계약

기준: **2026-11-02, main `580460a` 기반 구급대원 UI 후속 소스 포함**. 실제 체크아웃과 해당 PR 병합 여부를 확인한다. 이 문서는 dev1/dev2가 Git으로 받은 소스에서 로컬 UI 개발을 시작하는 절차다. 고정 배포 ZIP과 로컬 v1.4 추가 API는 별도다.

## 1. 먼저 각자 PC에서 실행

Git, Python을 준비한다. 처음에는 Python 3.12 이상을 권장한다. 아래 로컬 UI 경로는 Python 표준 라이브러리를 사용하며 OR-Tools, 공공데이터·카카오·Azure 키가 필요 없다. Node는 테스트를 실행할 때 사용한다. 정적 화면을 열기 위한 npm 설치·빌드 단계는 없다.

새 폴더에서 시작한다. 기존 작업 폴더를 reset하거나 덮어쓰지 않는다.

```powershell
git clone --branch main https://github.com/Uree1229/emergency-care-system.git emergency-care-dev1
cd emergency-care-dev1
git rev-parse HEAD
python -X utf8 -c "import sys, sqlite3; print(sys.executable); print(sys.version); print(sqlite3.sqlite_version)"
```

dev2는 복제 폴더 이름을 `emergency-care-dev2`로 바꾼다. 실제 커밋과 Python 경로·버전·SQLite 버전을 기록한다. `main`이 갱신되었다면 위 기준 커밋과 같다고 쓰지 않는다.

저장소 루트에서 **자신의 명령 하나**를 실행한다.

```powershell
# dev1
python -m server.chat_demo --db data/dev1-ui-20261102.db --port 18020
```

```powershell
# dev2
python -m server.chat_demo --db data/dev2-ui-20261102.db --port 18021
```

서버 터미널은 켜 둔다. 종료는 해당 터미널의 Ctrl+C다. 포트가 이미 사용 중이면 빈 포트로 바꾸고 모든 화면의 주소도 함께 바꾼다. 다른 서버를 종료하지 않는다.

| 항목 | 이 로컬 실행의 동작 |
| --- | --- |
| 서버 | `127.0.0.1`에서만 수신 |
| DB | 위에서 선택한 본인 전용 파일. 재시작 시 기존 기록 보존 |
| 최초 자료 | 합성 병원 H01~H10, 운영 자원 20행. H01·H04 포함 |
| 문의 워커 | **ON**. 환자에 자동 문의가 설정되면 문의 진행 |
| 배정 워커 | **OFF**. CP-SAT 추천 완료를 시험하는 모드가 아님 |
| 길찾기 | `TravelEstimator(use_kakao=False)`: 외부 카카오 호출 비활성 |
| 병원 전송 | 로컬 시연. 실제 병원으로 문의·채팅을 보내지 않음 |
| 환자 | 서버 기동만으로 환자를 생성하지 않음. UI 조작은 별도 쓰기 동작 |

[실제 실행 코드](../../../server/chat_demo.py)를 기준으로 한다. 이 명령에는 `--mode`, `--seed`, `--no-planner`를 붙이지 않는다. 다른 DB를 이 실행기로 변환하지 말고 사용하지 않은 전용 경로를 선택한다.

## 2. 같은 서버의 화면과 기동 확인

아래는 dev1 주소다. dev2는 포트를 **18021**로 바꾼다.

| 대상 | 주소 |
| --- | --- |
| 구급대원 입력 | <http://127.0.0.1:18020/paramedic/> |
| Quick 음성 기록 | <http://127.0.0.1:18020/paramedic/quick-test.html> |
| 선택형 잡음 A/B 비교 | <http://127.0.0.1:18020/paramedic/noise-compare.html> |
| 환자·이송 진행 | <http://127.0.0.1:18020/patient/> |
| 병원 H01 | <http://127.0.0.1:18020/hospital/?hid=H01> |
| 병원 H04 | <http://127.0.0.1:18020/hospital/?hid=H04> |
| 현재 프로세스·DB·워커 | <http://127.0.0.1:18020/api/system/status> |
| 병원 목록 | <http://127.0.0.1:18020/api/hospitals> |
| 음성 API 기능·버전 | <http://127.0.0.1:18020/api/voice/capabilities> |

처음에는 `/api/system/status`의 `database.state: OK`, `workers.inquiry.state: RUNNING`, `workers.planning.state: DISABLED`를 확인한다. 시작 직후 문의 워커가 `STARTING`이면 잠시 후 다시 조회한다. 이 실행에서 planning `DISABLED`는 설정된 동작이다. HTTP 200만 보지 말고 본문의 `status`, `issues`도 확인한다.

모든 화면의 API origin을 자신이 실행한 서버로 맞춘다. API 주소는 `http://127.0.0.1:18020`처럼 `/api`나 화면 경로를 붙이지 않은 origin이다. 입력·문의·병원 응답·진행은 서버가 반환한 **같은 환자 ID**로 연결한다. 병원 ID는 이 DB의 H01~H10을 사용한다. 다른 공개병원 시연 DB의 `NMC-...` ID를 섞지 않는다.

`/conversation-demo.html`도 제공한다. 이 페이지는 브라우저에서 실행되면 합성 사례를 생성하고 시연 제어로 병원 응답·이송 절차를 호출하는 도구이므로, 읽기 전용 기동 확인에는 위 상태 API를 사용한다. 자신의 합성 DB에서만 연다.

현재 main은 세 역할 정적 경로를 모두 제공한다([등록 코드](../../../server/app.py)). 이전 [11월 2일 시작 문서](../../TEAM_START_20261102.md)의 “main에는 `/paramedic/`가 없다”는 문장은 이 기준 커밋의 상태와 다르다. 화면이 존재하는 것과 모든 최신 API가 화면에 연결되어 있는 것은 각각 확인한다.

## 3. 공통 통신 계약

- **JSON·폴링:** UTF-8 JSON을 사용하고 화면 상태를 약 3초마다 GET으로 조회한다. 시각은 UTC epoch 초 정수다. 화면에서 한국시간으로 표시한다. 모든 응답에 공통 `data` 래퍼가 있는 구조가 아니다. 예를 들어 음성 세션은 `{"session": {...}}`, 오류는 `{"error": "...", "message": "..."}`이며 HTTP 상태도 확인한다.
- **작업자:** `actor_id`는 `dev1-crew`, `dev2-hospital-H01` 같은 안정된 불투명 식별자다. 음성 세션은 최대 80자이며 영문·숫자로 시작하고 영문·숫자·`._:@-`를 사용한다. 병원 처리에는 `handled_by`를 보낸다. 이 값들은 시연 문맥·감사 기록이며 실제 로그인이나 병원별 권한 인증이 아니다.
- **버전:** 음성 쓰기는 반환된 `session.version`을 `expected_version`으로 사용한다. 환자·문의·자원 버전과 섞거나 앱에서 다음 버전을 계산하지 않는다. 병원 응답에도 최신 문의의 `expected_version`을 보낸다. 409이면 최신 자료를 읽고 사람이 다시 판단한다. 버전 숫자만 바꿔 옛 결정을 자동 재전송하지 않는다.
- **재시도:** 음성 쓰기의 한 논리적 요청은 같은 `actor_id`, `client_request_id`, 작업, 버전, 전체 본문으로 재시도한다. 요청 ID는 최대 128자다. 응답 유실을 새 환자나 새 ID 생성으로 숨기지 않는다. 중복 성공은 과거 영수증일 수 있으므로 현재 세션·환자를 GET으로 다시 읽는다. 같은 세션의 쓰기는 순서대로 처리한다.
- **상태·사람의 결정:** 후보·추천·수용 응답·목적지 확정은 별개다. 병원 수용 뒤 구급대원이 명시적으로 확정·출발한다. 목적지는 자동 변경하지 않는다. 자유채팅 문장으로 수용·확정 API를 대신하지 않는다. planning이 꺼져 있으면 추천 요청의 `PLANNER_DISABLED`를 숨기지 않는다.
- **알림:** 실제 화면 표시 후 `delivered`, 사용자의 확인 후 `acknowledge`를 호출한다. 재연결 시 전진 커서와 과거 미확인 사건 조회를 각각 복구한다. GET 성공을 사용자 확인으로 처리하지 않는다.

상세 계약은 [기본 API](../../API.md), [음성 API](../../VOICE_INPUT_API.md), [재전송·경합·복구](../../TEAM_CONNECTION_CHECK_20261030.md)를 참고한다. `GET /api/patients/{pid}/recommendation`은 조회이고 `POST .../replan`은 명시적인 재계산 요청이다. 폴링마다 POST하지 않는다.

**CORS:** 위 `chat_demo`는 서버와 화면을 같은 origin에서 제공하며 기본 handler의 `Access-Control-Allow-Origin: *`를 사용한다. 별도 개발 서버에서 화면을 열 때는 실제 브라우저의 요청 URL·preflight·응답을 확인한다. 이후 `team_runtime`은 CORS 헤더를 보내지 않으므로 이 설정을 그대로 가정하지 않는다. 가능한 한 공통 서버가 제공하는 같은 origin의 화면을 사용한다.

## 4. main에서 사용할 수 없는 로컬 v1.4 계약

이 기준 main의 `server.app.ROUTES`에는 95개 경로가 등록되어 있다. 아래는 **로컬 커밋 `3081217`의 추가 계약이며 main에는 없다.**

| main에 없는 경로 묶음 | 구분 |
| --- | --- |
| 음성 세션의 `/workflow` | 기존 환자 `/inquiry-workflow`와 별개 |
| 음성 세션의 `/record-review`, `/record-review/acknowledge` | main의 로컬 화면 검토 기능과 별개 |
| 음성 세션의 `/handoff`, `/handoff/share` | main의 인계 문장 파서·클라이언트 요약과 별개 |
| `/api/voice/input-tasks` 및 상세·finish | main의 work-activities 계측 API와 별개 |

이 경로를 main UI의 필수 API로 연결하지 않는다. `capabilities.handoff_parser_revision`이 있다는 이유로 `/handoff/share`가 있다고 판단하지 않는다. [이전 브랜치 검토의 범위](../../reviews/BRANCH_REVIEW_20261031.md)도 v1.4 로컬 추가분을 제외한다. 이번 기동 검사에서는 위 묶음의 대표 GET 네 개가 실제 404임을 확인했다.

## 5. 음성 초안만 연습할 때

병원·자동 문의 없이 음성 연습 화면만 필요하면 별도 전용 DB로 실행한다.

```powershell
python -m server.app --db data/voice-dev1-20261102.db --port 8000 --no-planner --no-inquiry-worker
```

`/paramedic/quick-test.html` 또는 `/paramedic/command-practice.html`을 연다. dev2는 DB 이름을 바꾼다. 이 명령은 **문의·배정 워커 모두 OFF**이며 `--seed`를 붙이지 않는다. [긴 문장 음성 시험 안내](../../TEAM_VOICE_TEST_GUIDE_20261031.md)를 따른다. 실제 브라우저 음성 인식은 브라우저·인터넷·권한에 따라 달라진다. 키 없는 텍스트 API 시험을 실제 마이크 성공으로 기록하지 않는다.

메인의 미저장 입력·불확실 요청 복구는 같은 탭의 `sessionStorage`를 사용하며 자동 재전송하지 않는다. Quick은 기본 800ms 후속 인식 보류와 버튼 전용 관찰을 제공한다. 이 값은 실험 설정이지 검증된 최적값이 아니다. `noise-compare.html`은 원본과 RNNoise 처리본을 로컬에서 비교하는 선택형 도구이며 업무 STT에 처리본을 자동 연결하지 않는다.

`server.app` 일반 실행은 환경에 카카오 키가 있으면 외부 길찾기를 사용할 수 있다. 병원 연결 UI 연습은 외부 길찾기가 명시적으로 꺼진 1번 `chat_demo`를 사용한다. Azure 음성은 서버 설정이 있는 별도 경로이며 키를 UI 코드·Git에 넣지 않는다.

## 6. 이후 공통 full 서버 연결은 중앙 서버 담당자와 진행

로컬 UI 개발이 준비되면 중앙 서버 담당자가 공유할 Git 커밋·origin·DB·기능 버전을 맞춘다. 다음 세 실행을 혼동하지 않는다.

| 실행 | 문의 워커 | 배정 워커 | 환경 |
| --- | --- | --- | --- |
| 이 문서의 `server.chat_demo` | ON | OFF | 표준 라이브러리, 로컬 UI 개발 |
| `server.team_runtime --mode full` | ON | ON | 고정 의존성과 프로필 검사, 공통 통합 |
| `server.team_runtime --mode voice-only` | OFF | OFF | 표준 라이브러리, 음성 초안 시험 |

`team_runtime`에서는 action 뒤에 옵션을 쓴다. 기본 full의 진입점은 다음과 같으며, **지정한 서버 환경에서 중앙 서버 담당자가 수행한다.**

```powershell
python -m pip install -r requirements-server.txt
python -m server.team_runtime prepare --db data/team-common-20261102.db
python -m server.team_runtime check --db data/team-common-20261102.db
python -m server.team_runtime serve --db data/team-common-20261102.db --port 18033
```

기준 환경은 **CPython 3.12.14 / SQLite 3.53.1 / OR-Tools 9.15.6755**다. [requirements-server.txt](../../../requirements-server.txt)의 10개 정확 버전과 [고정 프로필](../../../config/team_runtime_profile_20261030.json)을 검사한다. 일반 Python 3.12 설치가 같은 SQLite 버전을 뜻하지 않는다. `--allow-environment-difference`는 명시적인 통합 시험에만 사용하며, 의존성 누락·잘못된 패키지 핀·보호 소스 변경을 허용하지 않는다. 이 옵션을 쓴 실행을 고정 환경 성능 실험으로 보고하지 않는다.

일반 Git clone에서는 `TEAM_START.ps1`·`TEAM_VERIFY.ps1`를 실행하지 않는다. 두 스크립트는 이웃 `release-manifest.json`과 `source/` 구조를 갖춘 검증 배포 묶음용이다. README의 `outputs/...` 고정 묶음 링크가 새 clone에 없을 수 있다. main 소스 실행은 위 Python 모듈 명령으로 한다.

공통 DB는 전용 표식·기관을 검사하고 재사용하며 같은 DB의 중복 실행을 잠근다. `chat_demo` DB를 공통 DB로 돌려 쓰지 않는다. 환자·문의·음성 기록을 지우는 `--seed`·DB 삭제로 연결 문제를 해결하지 않는다. 오래된 합성 자원의 명시 갱신·새 사례 생성·백업은 중앙 서버 담당자가 [공통 실행 규칙](../../TEAM_COMMON_RUNTIME_20261030.md)에 따라 수행한다. 공통 full의 `/api/team-runtime` 및 `team_readiness` 사전 점검은 `chat_demo`의 기동 검사와 다르다.

휴대폰의 `127.0.0.1`은 휴대폰 자신이다. 같은 Wi-Fi 공유는 중앙 서버 담당자가 [HTTPS LAN 절차](../../TEAM_LAN_20261102.md)에 따라 별도로 준비한다. `team_runtime`은 loopback에서 실행하고 LAN gateway로 연결한다. 기기별 인증서 신뢰·팀 토큰·실제 마이크 권한을 확인한다. 토큰은 병원별 로그인 인증을 대신하지 않는다. 개인키·토큰·외부 API 키를 저장소나 채팅에 공유하지 않는다. 이 문서의 PC 로컬 기동 성공을 휴대폰 성공으로 기록하지 않는다.

## 7. 테스트 묶음

```powershell
python scripts/test_suites.py list
```

분류와 개수는 위 `list` 명령의 실제 출력으로 확인한다. 새 테스트가 추가되므로 과거 개수를 완료 근거로 복사하지 않는다. core 전체는 위 고정 Python·SQLite·OR-Tools 환경과 **Node 24**에서 실행한다.

```powershell
node --version
$teamNode = (Get-Command node).Source
python scripts/test_suites.py core --node $teamNode
```

`--node`를 생략하면 `TEST_NODE` 또는 PATH의 node를 쓴다. [CI](../../../.github/workflows/core-tests.yml)는 SHA-256을 확인한 python-build-standalone `20260825`의 Python 3.12.14와 Node 24를 사용한다. 고정 환경이 아니면 일부 core 모듈이 실패하므로 로컬 `chat_demo` 기동과 core 통과를 분리해 기록한다. core 통과를 맞추려고 baseline·profile·연구 잠금 파일을 다시 만들지 않는다.

[실행기](../../../scripts/test_suites.py)는 테스트 전에 카카오·공공데이터·Azure 키 환경변수를 제거한다. `test_hospital_operations_http.mjs`는 Python harness가 띄운 격리 서버에서 실행하므로 Node 단독 목록에서 제외한다. 새 개발자는 `research`·`all`을 일반 통과 기준으로 사용하지 않는다. 연구 묶음에는 Git 밖 자료·예전 정확 바이트 잠금과 **106개 예상 실패 모듈**이 있다. 예상 실패는 실행기 종료 코드에서 제외되므로 종료 코드 0을 실패 0건으로 해석하지 않는다. 원인과 보존 원칙은 [테스트 안내](../../TESTING.md)를 따른다. `archive/usb_transfer/TEST_WINDOWS.cmd`는 이전 USB 방식이다.

## 8. 이번 문서 작성 중 확인한 범위

2026-11-02, 이번 UI 후속 변경 전 main의 문서 작성 기준(`cef0230`)에서 Python **3.14.3 / SQLite 3.50.4**, OR-Tools 미설치 상태로 격리된 `chat_demo`를 기동했다. 이 절은 당시 실행 기록이며 이번 UI 수정의 회귀 결과를 대신하지 않는다. 공통 full의 고정 환경 검증 결과가 아니다.

실제 실행 명령은 저장소 루트에서 다음과 같았다. 아래 절대 경로는 이 PC의 검증 기록이며 다른 PC에 복사할 설치 경로가 아니다. 각자의 개발 실행에는 1번 명령을 사용한다.

```powershell
& 'C:\Users\PC\AppData\Local\Python\pythoncore-3.14-64\python.exe' -B -X utf8 -m server.chat_demo --db 'C:\Users\PC\Documents\ChatGPT\캡스톤 디자인\.codex-build\team-ai-handoff-20261102\runtime-smoke\chat-demo-20261102-123022\chat-demo.db' --port 1414
```

포트는 OS가 고른 빈 loopback 포트이며 기존 서버를 종료하거나 포트를 재사용하지 않았다. 외부 API 키를 제거하고 HTTP 프록시를 비활성화한 GET 검사만 수행했다.

| 검사 | 실제 결과 |
| --- | --- |
| 기동·상태 API | stdout 기동 주소 확인, HTTP 200, status OK, DB OK |
| 워커 | inquiry RUNNING·thread_alive true, planning DISABLED |
| 합성 초기 자료 | 병원 10, 자원 20, 환자·문의·음성 세션 각 0 |
| 병원 목록·음성 capabilities | 각각 HTTP 200 |
| 세 역할 HTML | `/paramedic/`, `/patient/`, `/hospital/?hid=H01`, H04 모두 HTTP 200 |
| CSS·JS·폰트 전달 | 환자·병원·구급대 CSS, voice-client.mjs, reading-studio.css, 로컬 WOFF2 모두 HTTP 200·해당 MIME 확인 |
| v1.4 미포함 | workflow·record-review·handoff·input-tasks 대표 GET 네 개 모두 HTTP 404 |
| 정리 | 이번 검사에서 만든 프로세스만 종료, 기존 DB·환자·서버 미접근 |

앞선 sandbox 실행 두 번은 localhost 요청이 Windows 오류 10013으로 차단되었다. 별도 허용된 동일 격리 검사에서 위 HTTP 결과를 확인했으며, 실패 기록과 성공 기록을 구분해 보존했다. 성공 원자료는 문서 배포에 포함되지 않는 로컬 `runtime-smoke/chat-demo-20261102-123022/report.json`에 있다.

이 검사는 모든 95개 API의 기능 검사나 브라우저 렌더링 검사가 아니다. 환자 생성·병원 응답·확정·전체 흐름·core 회귀·실제 음성·휴대폰·사람의 사용성·임상 효과는 실행하지 않았다. 폰트 HTTP 전달 확인은 브라우저에서의 실제 글꼴 적용 확인과 별개다.
