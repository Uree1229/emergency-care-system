# 11월 2일 팀원 화면 연결 시작 절차

> 적용 범위: 이 문서는 원격 서버 전용 `main` 절차의 기록이다. 이번 `team-common` 통합본에는 구급대원·환자 진행·병원 화면 3개가 포함되며, 통합본의 최신 실행 기준은 [TEAM_RELEASE_20261030.md](TEAM_RELEASE_20261030.md)를 우선한다. 아래 원문의 화면 미포함 설명을 현재 통합본의 상태로 해석하지 않는다.

서버 API와 합성 자료를 먼저 준비하고, 김민석의 구급대원 화면과 김준성의 병원 화면을 같은 실행 서버에 연결한다. 정진은 서버·자료·재현 기록을 담당한다. 실제 병원·119·환자에게 연락하지 않는다.

이 main 서버 릴리즈에는 `/paramedic/` 음성 화면이 **없다**. 현재 상속한 `/patient/`, `/hospital/` 화면도 이번 최신 API를 모두 연결한 화면이라고 보장하지 않는다. Claude의 진행 중인 파서·음성 화면은 별도 작업본이며, 이 절차가 그 코드를 가져오거나 수정하지 않는다. 각 화면의 커밋과 서버 커밋을 따로 기록한다.

## 1. 정진이 당일 준비할 순서

서버 전용 PR의 설치 안내를 따라 작업본과 의존성을 준비한 뒤, 저장소 루트에서 실행한다. Git 커밋과 실제 Python 경로를 먼저 기록한다. 기준은 `fixed_3 + CP-SAT severity_first, 3초`이며, 원래 baseline/profile이나 core 21개 파일의 해시를 새로 만들지 않는다.

```powershell
git rev-parse --short HEAD
python --version
python -m server.team_runtime prepare --db data/team-common-20261102.db
python -m server.team_runtime check --db data/team-common-20261102.db
python -m server.team_runtime serve --db data/team-common-20261102.db --port 18033
```

`team-common-20261102.db`는 전용 합성 병원 10곳을 사용하는 공통 DB다. 이미 있으면 재사용하고, 다른 종류의 DB면 거절한다. `--seed`나 DB 삭제로 기존 이송 건을 지우지 않는다. 기존 `flow_rehearsal`의 `FLOW-H*` DB는 공통 DB 표식·기관이 다르므로 혼용하지 않는다.

다른 터미널에서 API 준비 상태를 확인한다.

```powershell
python -m server.team_readiness check --server http://127.0.0.1:18033
```

이 명령은 다음 다섯 API에 **GET만** 보낸다. 환자 조회, DB 직접 접근, 자원 갱신, 문의·응답·확정 처리는 하지 않는다.

| API | 준비 완료 조건 |
| --- | --- |
| `/api/team-runtime` | full 모드·공통 프로필 정상·합성 자료·외부 병원 발송 없음·고정 기준 |
| `/api/system/status` | 현재 프로세스 정상·실제 planning/inquiry 워커 모두 RUNNING·가동 스레드 확인 |
| `/api/resource-model-scope` | `ed_bed/special_team` 두 자원·현재 작업본과 동일한 치료조건 카탈로그 |
| `/api/data-provenance` | H01~H10 가상 기관·운영 자원 20행·관측 갱신 시각이 최근이며 미래가 아님 |
| `/api/voice/capabilities` | 인식문 API와 음성 계약 버전 확인. Azure 설정 여부는 별도 표시 |

`ready:true`가 아니면 새 사례를 만들지 않는다. `failed_checks` 또는 고정 `error_code`로 원인을 구분한다. 워커가 시작 중이면 잠시 후 check를 다시 실행한다. 오래된 자원 정보는 도구가 최신으로 바꾸지 않는다. 합성 자원을 시연자가 다시 확인해야 할 때만 서버를 정상 종료한 뒤 기존 `team_runtime refresh-fixture-resources` 절차를 명시적으로 사용하고 재기동한다. 이 절차도 실제 병원 상태의 확인이 아니며, 문의 수용 만료나 기존 탐색 마감시간을 연장하는 작업이 아니다.

## 2. PC·휴대폰에 같은 API origin 제공

같은 PC의 API 기능 확인은 `http://127.0.0.1:18033`으로 한다. 휴대폰에서 `127.0.0.1`은 휴대폰 자체이므로 PC의 서버로 연결되지 않는다. 휴대폰 마이크는 신뢰하는 HTTPS origin에서 별도로 확인한다.

```powershell
python -m server.team_lan addresses
python -m server.team_lan prepare --ip 192.168.X.X --output data/team-lan-20261102
python -m server.team_lan serve --bundle data/team-lan-20261102 --upstream-port 18033 --port 18443
```

표시된 실제 사설 IP로 `192.168.X.X`를 바꾼다. 첫 터미널의 공통 서버는 계속 실행한다. prepare는 OpenSSL을 사용하며 필요하면 해당 도구의 `--openssl` 옵션으로 실행 파일을 선택한다. gateway는 `https://선택IP:18443`을 제공하고, loopback의 기존 API로 연결한다. 같은 Wi-Fi·OS 인증서 신뢰·필요한 방화벽 허용은 PC·휴대폰에서 별도로 설정하고 확인한다. 공개 터널이나 인터넷 포트포워딩을 사용하지 않는다.

CA·개인키·토큰의 범위를 구분한다.

- `rootCA.crt`: PC/시험 기기가 신뢰할 공개 CA 인증서. HTTPS의 IP 이름도 일치해야 한다.
- `rootCA.key`, `server.key`: PC에 보관하는 개인키. 휴대폰·Git·채팅에 공유하지 않는다.
- `access-token.txt`: Basic 인증의 비밀 토큰 한 줄. 사용자 이름은 `team`이다. API 키가 아니며 로그·영수증에 저장하지 않는다.

```powershell
python -m server.team_readiness check --server https://192.168.X.X:18443 --ca data/team-lan-20261102/rootCA.crt --token-file data/team-lan-20261102/access-token.txt
```

readiness는 loopback 또는 IPv4 RFC1918 사설 IP만 허용하고, 사설 IP에는 HTTPS를 요구한다. HTTPS 검증을 끄는 옵션은 없다. 프록시 환경변수와 리다이렉트를 따르지 않는다. 이 gateway의 공용 팀 토큰은 실제 의료진 로그인·병원별 권한을 구현한 것이 아니다.

화면도 같은 HTTPS origin에서 제공하거나, 담당자가 개발 프록시·CORS와 인증 전달을 명시적으로 연결해야 한다. 서로 다른 origin의 화면에서 API 주소만 바꾸고 연결 완료라고 판단하지 않는다. main의 `/paramedic/` 404는 음성 서버 장애가 아니라 미포함 화면 문제다. 휴대폰에서 실제 화면을 연 뒤 `isSecureContext`, 마이크 권한·녹음·전송을 확인한다. API preflight만으로 마이크 성공을 주장하지 않는다.

## 3. 당일 새 합성 사례 만들기

새 run ID와 새 영수증 경로를 사용한다. run ID에는 영문·숫자로 시작하는 1~40자의 ASCII 영문·숫자·`_`·`-`만 허용한다.

```powershell
python -m server.team_readiness seed --server http://127.0.0.1:18033 --case general --run-id nov02-general-01 --output outputs/team_start_20261102/general-01.json
python -m server.team_readiness seed --server http://127.0.0.1:18033 --case cardiac --run-id nov02-cardiac-01 --output outputs/team_start_20261102/cardiac-01.json
python -m server.team_readiness seed --server http://127.0.0.1:18033 --case simultaneous --run-id nov02-pair-01 --output outputs/team_start_20261102/pair-01.json
```

gateway를 거칠 때는 `--server`를 HTTPS 주소로 바꾸고 위와 같은 `--ca`, `--token-file`을 추가한다. 토큰 값을 명령줄 인수에 직접 넣지 않는다.

| 사례 | 미리 정한 시연 조건 | 생성 ID |
| --- | --- | --- |
| `general` | 카탈로그의 `TR_GENERAL_ED`: 응급의학·진료 자리 요구 | `TEAM-<run-id>-A` |
| `cardiac` | 카탈로그의 `TR_CARDIAC_INTERVENTION`: 심장내과·진료 자리·전문팀 요구 | `TEAM-<run-id>-A` |
| `simultaneous` | 일반 1건과 심장 중재시술 조건 1건을 연속 등록한 뒤 함께 문의 대기 | `TEAM-<run-id>-A`, `TEAM-<run-id>-B` |

프로필은 증상을 진단한 결과가 아니라 사람이 미리 선택한 **synthetic 과제**다. 나이·관찰·우선순위와 expert 표기도 가상 입력이다. 임상 중증도 판정이나 실제 시술 가능성 검증이 아니다. 카탈로그에서 요구 조건을 읽으며, 공개 병상 수를 운영 자원으로 변환하지 않는다.

도구는 다섯 API의 preflight를 통과한 뒤에만 `POST /api/patients`로 생성한다. 서버의 현재 시각으로 `requested_at`이 부여되고 실제 반환 ID·시각을 영수증에 기록한다. 생성 과정의 `auto_inquiry:true`, `inquiry_policy:fixed_3`에 따라 기존 워커가 문의한다. `fixed_3`은 최대 3곳인 첫 배치다. 적합한 후보가 적으면 1~2곳 문의도 정상이며, 3곳을 억지로 채우지 않는다.

기본 15초 동안 작업 시작과 첫 문의 1건 이상을 기다린다. `--wait-seconds`는 최대 30초다. 후보를 확보하지 못한 작업 종료는 `NO_INITIAL_INQUIRY_AVAILABLE`, 워커 작업 오류는 `INQUIRY_WORKFLOW_ERROR`, 제한시간 안 첫 배치를 관측하지 못하면 `INITIAL_BATCH_TIMEOUT`으로 분리한다. timeout만으로 후보가 없었다고 단정하지 않는다.

성공 상태는 `READY_FOR_MANUAL_RESPONSE`다. **아직 병원 수용 응답이나 목적지 확정·출발을 만든 것이 아니다.** 이후 두 화면에서 병원 응답과 구급대원의 명시적 결정을 연결한다. 도구는 자원·병원 응답·도착·기존 환자를 변경하는 API를 호출하지 않는다. 다만 기존 환자 조회 API 자체의 정상 만료 처리는 서버의 원래 계약을 따른다.

영수증은 origin·도구 actor·요청/반환 환자 ID·상태·시각과 오류 코드만 보존한다. 관찰 원문·연락처·토큰·키·서버 DB 경로는 저장하지 않는다. `TEAM-READINESS`는 도구 문맥이며, 기존 환자 생성 API의 서버 감사 actor `patient_web`를 이 값으로 바꿨다는 뜻이 아니다.

기존 환자 ID가 하나라도 발견되면 그 사례 전체의 새 등록을 중단한다. 기존 출력 파일도 덮어쓰지 않는다. 두 환자 중 하나만 생성됐거나 POST 응답을 받지 못했어도 영수증을 남긴다.

- `CREATED`: 생성 응답에서 실제 ID를 확인했다.
- `POST_OUTCOME_UNKNOWN`: 요청은 보냈지만 생성 결과를 확정하지 못했다. 자동 재전송하지 않는다.
- `EXISTING_NOT_CHANGED`: 기존 ID를 발견했으며 그 환자는 덮어쓰지 않았다.

부분 실패 뒤에는 영수증의 원래 ID로 서버 상태를 조회해 확인한다. 응답 불명을 숨기려고 새 ID로 같은 환자를 다시 만들거나 DB를 지우지 않는다. 이 도구는 부분 사례를 자동 이어 만들지 않는다. 이어갈지, 별도의 새 과제를 시작할지는 확인 후 결정하고 이전 영수증을 보존한다.

## 4. 버전·키 허용 행렬

| 단계/화면 | 반드시 기록·확인 | 필요한 접근 정보 | 완료로 보지 않는 것 |
| --- | --- | --- | --- |
| 서버 시작 | Git 커밋, 실제 Python, `/api/team-runtime`의 runtime/baseline/profile, 양 워커 RUNNING | 합성 API 시험에는 외부 API 키 없음 | 환경 차이 허용 실행을 고정 환경 성능 실험으로 간주 |
| readiness | `ready`, 실패 check, runtime·voice·commands 버전, 최근 자원 시각 | loopback은 없음, gateway는 CA·팀 토큰 파일 | 자원을 몰래 최신화하거나 오래된 문의 만료 연장 |
| 김민석 구급대원 화면 | UI 커밋·API origin·음성 capabilities와 사용 엔진·기기/브라우저 | 브라우저 인식은 권한/HTTPS, 서버 Azure STT는 서버 환경변수 설정 | 합성 인식문 API 통과를 실제 발화 성공으로 간주 |
| 김준성 병원 화면 | UI 커밋·API origin·현재 문의/환자/자원 버전·결정 토큰 | gateway 팀 토큰; 실제 병원 로그인 기능은 별도 | 팀 토큰을 병원별 역할 인증으로 간주 |
| 도로·공개 자료 | 이번 합성 연결 시험에서는 비활성·저장 자료와 구분 | 카카오·공공데이터 키는 이번 시험에 불필요 | 공개 관측을 실제 수용 확답으로 간주 |
| 실제 음성 측정 | 입력 시작~수정~명시 확인~전송까지 실제 시각·실패도 보존 | Azure 사용 시 `AZURE_SPEECH_KEY/ENDPOINT`는 서버에만 설정 | gateway 토큰과 Azure 키를 서로 대체·공유 |

확인할 API 계약과 기본 버전은 [음성 API](VOICE_INPUT_API.md), [이송 중 변경](ENROUTE_PATIENT_CHANGE_20261030.md), [서버 통합](SERVER_INTEGRATION_20261030.md)을 따른다. 새 Claude 파서가 합쳐졌다고 가정하지 말고 실제 `voice_revision/command_revision/handoff_parser_revision`과 UI 커밋을 대조한다. 새 STT·소음 억제·자동모드 UI 개선은 각 작업본에서 별도로 검증한다.

## 5. 각 화면의 연결 완료 기준

**김민석 · 구급대원 화면**

- 같은 이송 ID로 입력·초안 확인·필수정보 안내·수정을 제공하고, 제출 결과가 불명확하면 원 요청 ID/본문 계약으로 복구한다. GET 재조회와 명시적인 쓰기를 구분한다.
- 어느 병원에 문의했고 확인 중/추가 질문/수용/불가인지 서버 상태를 표시한다. 오래된 추천을 확정 가능한 최신 응답처럼 표시하지 않는다.
- 병원 수용 뒤 구급대원이 명시적으로 확정·출발한다. 이송 중 새 관찰은 별도 변경 검토 API로 보내며 현재 병원의 판단 전 기존 환자정보와 목적지를 유지한다.
- 새 수용이 생겨도 목적지를 자동으로 바꾸지 않는다. 최신 위치·유효 응답을 확인한 담당자의 변경 결정으로만 전환한다.
- 중요 사건을 실제로 표시한 뒤 delivered, 사용자가 확인한 뒤 acknowledge를 호출한다. 재연결에서는 과거 미확인 사건도 복구한다.

**김준성 · 병원 화면**

- 같은 DB의 H01~H10 중 담당 기관을 선택해 새 사례의 문의 상세·필수조건·정보 버전을 확인한다. 실제 외부 병원에 문의가 갔다는 표시를 하지 않는다.
- 확인 중·질문·수용·불가 응답에 최신 문의 버전과 사유를 사용한다. 409 충돌은 재조회하고, 버전 숫자만 바꿔 옛 판단을 자동 재전송하지 않는다.
- 자원 변경은 최신 자원 버전으로 저장하고 관련 이송 검토를 확인한다. 실제 점유와 도착 예정 수요를 혼동하지 않는다.
- 현재 병원에 도착한 환자정보 변경안을 조회해 최신 토큰으로 keep/adjust/reject를 명시한다. adjust 후에도 새 수용과 구급대원의 결정 전 목적지는 유지된다.
- 담당 이송의 대화·현재 병원 세션·도착/퇴실 상태가 서버와 일치해야 한다. 다른 병원 경로로 과거 영수증을 재사용하지 않는다.

화면 연결 후 정상·추가 질문·수정 충돌·조건 변경·무응답을 실제로 조작한다. 첫 성공만 남기지 말고 실패·수정·재시도와 UI/서버 버전을 함께 기록한다. 사용성 시간·터치·오류·병원 실제 검토시간은 사람이 수행한 과업에서 별도 측정한다.

## 6. 채팅의 세 참여자

서버는 병원 탐색·문의·응답·조건 변경의 **자동 사건**을 전달한다. 사람이 응답한 것처럼 서버 메시지를 표시하지 않는다. 병원 선정 전에는 구급대원과 서버 진행 사건이 중심이고, 목적지를 확정하면 **구급대원 + 현재 수용 병원 + 서버 자동 사건**이 같은 이송 대화에 참여하는 구조다.

`GET /api/patients/{pid}/conversation`, `POST /api/patients/{pid}/messages`와 병원 범위의 conversation API를 연결한다. 수용 응답·목적지 확정·중요 알림 확인은 채팅 문장으로 대신 처리하지 않는다. 목적지가 바뀌면 이전 병원과 현재 병원의 세션·이력을 구분한다. 단체 채팅 기능이 의료적 수용 보장이나 운영 로그인 권한을 대신하지 않는다.

## 7. 이 도구에서 확인한 범위

`test_team_readiness`는 주소·인증·리다이렉트·신선도·전체 사전 확인·중복/부분 실패 보존과, 전용 합성 DB에서 실제 full 워커/HTTP의 GET 보존·일반/심장 두 사례 첫 배치를 검증한다. 실제 마이크, 휴대폰 조작, 현직 사용자의 업무시간이나 임상 효과를 검증한 결과가 아니다.

```powershell
python -m unittest test_team_readiness -v
```

기존 [공통 실행](TEAM_COMMON_RUNTIME_20261030.md), [재전송·경합 계약](TEAM_CONNECTION_CHECK_20261030.md), [백업·신규 복원](TEAM_DATABASE_BACKUP_20261030.md)을 함께 따른다. 이 문서와 새 readiness 도구는 기존 운영 DB·배정 기준·Claude 코드·두 팀원 화면을 수정하지 않는다.

## 서버 내부 구조와 회귀 목록

객체·DB 관계는 [서버 도메인 인계](SERVER_DOMAIN_20261102.md), 실제 열·FK·인덱스는 [스키마 JSON](api/SERVER_SCHEMA_20261102.json)에 있다. 기술용 `team_runtime_meta`와 SQLite 내부 테이블은 이 업무 스키마 목록에서 제외했다. 화면은 DB를 직접 수정하지 않는다.

동일 기준 환경에서 다음 목록으로 서버 회귀를 실행한다. 이 명령은 실제 UI·마이크를 자동 조작하지 않는다.

```powershell
$teamServerTests = (Get-Content docs/api/SERVER_TESTS_20261102.json -Raw | ConvertFrom-Json).modules
python -X utf8 -m unittest -v $teamServerTests
```

전체 회귀의 실제 full 실행기 시험은 기록된 Python·SQLite·OR-Tools 환경을 요구한다. 다른 환경의 기능 연결 시험은 공통 실행기의 명시적 환경 차이 옵션을 사용하고 preflight를 확인하며, 이를 동일 성능 실험으로 표시하지 않는다. 없는 UI·실제 사람 측정·휴대폰 시험을 통과 수에 포함하지 않는다.
