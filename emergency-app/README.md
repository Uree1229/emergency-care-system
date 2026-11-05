# 팀원 환자 입력 앱 · 공통 서버 연결

FastAPI로 환자 프로필 선택·등록과 의식·호흡·증상 확인 화면을 제공하고, 확인한 정보를 공통 서버의 이송 흐름에 연결합니다. 프로필 저장과 공통 이송 기록은 각각의 DB를 사용합니다. 시스템 관계는 [구조 안내](../docs/ARCHITECTURE.md)에 있습니다.

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

환자 입력은 `http://127.0.0.1:18026/`, 병원은 `http://127.0.0.1:18025/hospital/?hid=H01`에서 엽니다. `COMMON_API_URL`을 지정하지 않으면 `http://127.0.0.1:18020`을 사용합니다. 같은 환자의 진행 순서는 [시연 안내](../docs/DEMO.md), 공통 서버 모드는 [실행 안내](../docs/RUNNING.md)를 따릅니다.

`EMERGENCY_PROFILE_DB`는 선택 사항입니다. 지정하지 않으면 앱 폴더의 `emergency.db`를 사용합니다. 로컬 DB의 개인 프로필과 공통 서버의 실제 이송 건은 서로 다른 객체입니다. 로컬 프로필 ID를 공통 서버 환자 ID로 재사용하지 않습니다. 원본 DB를 버전 관리하거나 실제 개인정보를 시연 데이터로 사용하지 마세요.

## 연결 흐름

1. 기존 화면에서 환자를 선택하거나 미등록 환자로 진행하고, 의식·호흡·증상을 확인합니다.
2. `GET /emergency/report`는 현재 정보와 **공통 서버에 이미 저장된 병원 정보**만 표시합니다. 보고 화면을 열거나 새로고침해도 환자나 문의는 생성하지 않습니다. NMC·심평원·카카오 API를 보고 화면에서 직접 호출하지 않습니다.
3. 실제 관측한 위치, 입력자 구분, 확인된 필요 진료 분야를 입력합니다. 위치 확인 버튼은 브라우저의 실제 GPS를 폼에 채웁니다. 위치를 얻을 수 없다면 실제로 확인한 좌표를 직접 입력하며, 예시 위치를 자동으로 채우지 않습니다.
4. 동의 체크박스는 처음에 해제되어 있습니다. 사용자가 동의한 후 **정보 전송 및 병원 탐색 시작**을 눌러야 `POST /emergency/connect`가 공통 서버에 별도의 이송 건을 생성합니다.
5. 성공하면 공통 서버 `/patient/?pid=…`로 이동합니다. 이 화면에서 문의 진행, 추가 질문, 수용 확인, 목적지 확정, 출발, 채팅, 조건 재확인, 대체 문의와 사람이 결정하는 목적지 변경을 이어갑니다.

의식·호흡의 미확인 값을 `없음` 또는 `비정상`으로 추정하지 않습니다. 증상을 진단이나 진료 분야 자동 판정에 사용하지 않습니다. 필요 분야는 저장된 병원 목록의 분야 중 사용자가 직접 선택합니다. 공공 API의 병상 관측값을 운영 자원 총량이나 해당 환자의 수용 확답으로 변환하지 않습니다. 출처, 관측 시각, 수집 시각과 미확인 여부를 표시하고 운영 자원과 공공 관측값을 별도로 보여 줍니다.

## 중복 전송과 실패 복구

보고 화면의 `submission_id`는 불투명한 UUID입니다. 같은 폼을 반복 전송해도 같은 입력이면 공통 서버의 동일 이송 건으로 이어지고, 문의를 중복 생성하지 않습니다. 같은 식별자로 다른 입력을 보내면 충돌로 처리합니다. 입력 오류나 서버 연결 오류가 발생하면 같은 식별자, 좌표, 선택한 분야, 동의 상태를 유지한 폼을 다시 표시합니다.

`fixed_3`이 기본 문의 정책입니다. 개발 시연 설정의 다른 정책과 `special_team` 자원은 알고리즘 비교·합성 데이터 시험을 위한 설정이며 실제 임상 기준이나 의료 자원 예측값이 아닙니다.

공개 데이터 수집은 공통 서버의 별도 수집 작업으로 진행합니다. `/emergency/connect`가 반환한 공통 환자 ID로 이송자·병원 웹의 같은 기록을 조회합니다.

## 화면 검사 선택자

- 전송 폼: `#common-connect-form`
- 실제 좌표: `#connect-latitude`, `#connect-longitude`
- 입력자·동의·정책: `#reporter-role`, `#connect-consent`, `#inquiry-policy`
- 오류·전송: `#connect-error`, `#connect-submit`
- 필요 진료 분야: `input[name="required_capabilities"]`
- 실제 GPS 확인: `#location-button`
