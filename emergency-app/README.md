# 팀원 환자 입력 앱 · 공통 서버 연결

환자 등록·선택, 단계별 의식·호흡·증상 확인, 인체 그림과 아픈 부위 선택, 구급대 전달 정보, 위치·병원 조회 화면을 공통 서버의 이송 흐름에 연결합니다. 선택 기능인 음성 문진은 인식문과 추출 결과를 사람이 확인한 뒤 참고 정보로 전달합니다. 각자 PC에서 연결할 때는 [팀원 테스트 가이드](../docs/TEAM_TEST_GUIDE.md)도 참고하세요.

## 실행

저장소 루트에서 실행합니다. 먼저 필요한 패키지를 설치하고 공통 서버를 실행합니다.

```powershell
python -m pip install -r emergency-app/requirements.txt
python -m server.chat_demo --db data/teammate-patient-demo.db --port 18025
```

별도 터미널에서도 저장소 루트를 기준으로 실행합니다.

```powershell
$env:COMMON_API_URL = 'http://127.0.0.1:18025'
$env:EMERGENCY_PROFILE_DB = Join-Path (Get-Location) 'data/teammate-profiles-demo.db'
python -m uvicorn main:app --app-dir emergency-app --host 127.0.0.1 --port 18026
```

환자 입력은 `http://127.0.0.1:18026/`, 병원은 `http://127.0.0.1:18025/hospital/?hid=H04`에서 엽니다. `COMMON_API_URL`을 지정하지 않으면 `http://127.0.0.1:18020`을 사용합니다. 연결 배경과 이전 검증 기록은 [연결 문서](../docs/TEAM_PATIENT_CONNECTION_20261013.md)에 있습니다.

`EMERGENCY_PROFILE_DB`는 선택 사항입니다. 지정하지 않으면 앱 폴더의 `emergency.db`를 사용합니다. 로컬 DB의 개인 프로필과 공통 서버의 실제 이송 건은 서로 다른 객체입니다. 로컬 프로필 ID를 공통 서버 환자 ID로 재사용하지 않습니다. 원본 DB를 버전 관리하거나 실제 개인정보를 시연 데이터로 사용하지 마세요.

## 연결 흐름

1. `/emergency`에서 등록 환자를 선택하거나 미등록 환자로 시작합니다. 선택한 환자와 흐름 식별자를 유지하며 `/emergency/check` → `/emergency/breathing` → `/emergency/symptoms` → `/emergency/bodymap` 순서로 진행합니다. 증상과 아픈 부위는 여러 개 선택할 수 있습니다.
2. `/emergency/report`에서 구급대에 전달할 정보를 확인하고 `/emergency/hospitals`로 이동합니다. 이 GET 화면들을 열거나 새로고침해도 공통 서버의 환자·문의는 생성되지 않습니다. 위치를 확인하면 기존 공개 API의 주변 병원·이동시간·시설·자원 참고 조회를 수행합니다. 이 참고 목록과 별도로, 실제 문의 등록 폼의 진료 분야는 공통 서버에 저장된 병원 목록에서 읽습니다.
3. 실제 관측한 위치, 입력자 구분, 확인된 필요 진료 분야를 입력합니다. 위치 확인 버튼은 브라우저의 GPS 관측값을 채웁니다. 위치를 얻을 수 없다면 직접 확인한 좌표를 입력하며 예시 위치를 자동으로 채우지 않습니다. 진료 분야는 사용자가 직접 선택합니다.
4. 동의 체크박스는 처음에 해제되어 있습니다. 사용자가 동의한 후 **정보 전송 및 병원 탐색 시작**을 눌러야 `POST /emergency/connect`가 공통 서버에 별도의 이송 건을 생성합니다. 선택한 아픈 부위는 `extra.body_parts`에 보존합니다.
5. 성공하면 공통 서버 `/patient/?pid=…`로 이동합니다. 이 화면에서 문의 진행·추가 질문·수용 확인·목적지 확정·출발·채팅·조건 재확인을 이어갑니다. 병원 담당자는 공통 서버의 `/hospital/`에서 수용 응답과 도착 확인을 수행합니다. 대체 문의 후 목적지 변경도 사람이 결정합니다.

의식·호흡의 미확인 값을 `없음` 또는 `비정상`으로 추정하지 않습니다. 증상·아픈 부위·음성에서 중증도, 진료 분야, 자원 요구를 자동 판정하지 않습니다. 병원 목록이나 공공 병상 관측값은 해당 환자의 수용 확답이 아니며 운영 자원과 구분합니다.

## 음성 확인과 보관 범위

`/voice-intake`에서 브라우저 음성인식 또는 직접 입력으로 문장을 작성하고 추출 결과를 확인합니다. **명시적으로 확인한 결과만** 최종 전송 폼의 `voice_payload`·`voice_confirmed=yes`를 거쳐 `extra.voice_intake`에 전달합니다. 이 참고 정보로 기존 증상·의식·호흡·중증도를 자동 덮어쓰지 않습니다. 음성 정보가 없으면 단계식 입력만으로 진행할 수 있습니다.

음성 초안은 현재 탭의 `sessionStorage`에서 `emergencyVoiceIntake.v2:<flow_token>`별로 보관하며 생성 후 2시간 동안 유효합니다. 선택한 환자에 한 번 연결한 초안은 다른 환자·새 흐름으로 넘기지 않습니다. 새로고침 시 같은 탭·흐름의 유효한 기록을 복원할 수 있으며, 초기화·저장소 삭제·탭 종료 후에는 복원을 보장하지 않습니다. 이전 전역 키 `emergencyVoiceIntake`의 기록은 읽지 않습니다. 원음 파일을 이 앱의 DB에 저장하는 기능은 없습니다.

전송 시에는 환자·흐름·유효기간과 명시 확인을 서버에서도 검사합니다. 확인하지 않은 음성, 다른 환자·흐름의 기록, 만료된 기록이 첨부되면 등록을 차단하고 재확인을 안내합니다. 지원 브라우저와 마이크 권한이 필요하며 브라우저 제공자의 음성인식 처리 환경에 영향을 받습니다.

## 중복 전송과 실패 복구

최종 전송 폼의 `submission_id`는 불투명한 UUID입니다. 같은 폼을 반복 전송해도 같은 입력이면 공통 서버의 동일 이송 건으로 이어지고 문의를 중복 생성하지 않습니다. 같은 식별자로 다른 입력을 보내면 충돌로 처리합니다. 입력 오류나 서버 연결 오류가 발생하면 같은 식별자·환자·선택 증상·아픈 부위·좌표·선택 분야·동의 상태를 유지한 폼을 다시 표시합니다. 응답만 유실된 경우에도 공통 서버에 저장된 같은 이송 건을 확인합니다. 새 입력 화면을 여는 동작은 이전 전송 폼의 식별자를 영구 보관하는 기능이 아닙니다.

`fixed_3`이 기본 문의 정책입니다. 개발 시연 설정의 다른 정책과 `special_team` 자원은 알고리즘 비교·합성 데이터 시험을 위한 설정이며 실제 임상 기준이나 의료 자원 예측값이 아닙니다.

공개 참고 조회는 앱의 NMC·심평원·카카오 연동 설정을 사용합니다. 공통 서버의 병원 목록·운영 자원·수용 응답과 구분하며, 공개 조회 결과로 문의 등록의 진료 분야나 자원 수요를 자동 변경하지 않습니다. 전송 실패 폼 복원은 외부 참고 조회를 다시 실행하지 않습니다. 공통 서버의 기존 병원 웹과 환자 웹은 연결 대상으로 사용합니다.

## 합성 통합 검증

저장소 루트에서 테스트 의존성을 설치하고 실행합니다.

```powershell
python -m pip install -r requirements-intake-test.txt
python -m unittest test_patient_workflow_dev1_http test_patient_workflow_dev1_intake test_patient_workflow_dev1_profile test_patient_commit_bridge -v
node --test test_emergency_intake_voice_core.mjs test_emergency_intake_voice_ui.mjs
```

새 통합 검증은 FastAPI의 실제 ASGI 경로와 별도 `ThreadingHTTPServer` 공통 서버, 분리된 임시 프로필·이송 DB를 사용합니다. 단계별 화면의 링크·폼, 명시 동의, 동일 요청 재시도, 응답 유실, 환자·흐름 격리, 확인 음성의 참고 정보 전달, 병원 수용→확정→출발→도착을 검사하며 외부 API 요청은 차단합니다. 렌더링한 인라인 JavaScript는 Node로 문법을 검사합니다.

이 검증의 좌표·음성 인식문은 합성 입력입니다. 실제 마이크/STT 정확도·소음 억제·GPS 센서·브라우저 화면 조작·임상 효과는 별도 사람 시험이 필요합니다.

## 화면 검사 선택자

- 전송 폼: `#common-connect-form`
- 실제 좌표: `#connect-latitude`, `#connect-longitude`
- 입력자·동의·정책: `#reporter-role`, `#connect-consent`, `#inquiry-policy`
- 오류·전송: `#connect-error`, `#connect-submit`
- 필요 진료 분야: `input[name="required_capabilities"]`
- 실제 GPS 확인: `#location-button`
