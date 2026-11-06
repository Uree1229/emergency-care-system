# 설치·실행·백업

2026-11-05 기준으로 저장소의 실행 명령을 정리했다. 각 실행기는 같은 폴더의 `server/`, 화면, 설정을 사용한다. 소스와 별도 시연 사본의 기능은 [현재 상태](PROJECT_STATUS.md)에서 확인한다.

## 설치

Python 3.12 이상에서 프로젝트 루트를 연다. 전체 서버와 솔버를 사용할 환경은 가상환경에 준비한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-server.txt
```

전체 실행 환경의 고정 버전은 `requirements-server.txt`에 있다. 음성 전용 서버는 Python 표준 라이브러리로 실행하지만 실제 마이크 인식은 브라우저 지원과 권한에 의존한다. 음성·원음 평가용 추가 의존성은 해당 평가 안내를 따른다.

## 가상 환자·병원 화면 시연

수용 문의부터 도착까지 화면을 점검할 때 새 가상 DB 경로를 지정한다. 아래는 한 번의 새 시연용 이름이다.

```powershell
.\.venv\Scripts\python.exe -B -m server.chat_demo --db data/demo-new.db --port 18020
```

같은 서버의 `/paramedic/`, `/patient/`, `/hospital/?hid=H01`을 연다. 구급대에서 등록한 환자는 반환된 환자 ID와 진행 링크로 이어간다. `chat_demo`는 문의 워커를 켜고 배정 워커는 끈다. 병원의 수용 응답과 이송자의 목적지 확정은 화면에서 명시적으로 수행한다. 기본 이동시간은 거리 추정이다.

## 공통 서버와 두 워커

문의·배정 워커를 함께 점검할 때는 `team_runtime`을 사용한다. 이 실행기는 전용 합성 DB 표식, 기관 목록, 고정 배정 소스·설정과 실행 환경을 검사한다.

```powershell
.\.venv\Scripts\python.exe -B -m server.team_runtime prepare --db data/team-common.db
.\.venv\Scripts\python.exe -B -m server.team_runtime check --db data/team-common.db
.\.venv\Scripts\python.exe -B -m server.team_runtime serve --db data/team-common.db --port 18033
```

`/api/team-runtime`에서 실제 DB·모드·릴리즈 정보를, `/api/system/status`에서 문의와 배정 워커 상태를 확인한다. `full` 모드는 고정 소스 및 환경 검사와 두 워커를 사용한다. `--mode voice-only`는 두 워커를 모두 끄며 음성 API·화면 점검에 사용한다. 환경 차이 허용 옵션은 실행 환경이 고정 실험 환경과 다름을 기록하는 선택이다.

기존 포트가 사용 중이면 실행 중인 프로그램을 확인하고 새 포트를 정한다. 휴대폰에서 `127.0.0.1`은 휴대폰 자체를 가리킨다. 다른 기기와 마이크를 점검할 때는 별도의 LAN HTTPS 절차와 기기 권한이 필요하다.

## 같은 Wi-Fi의 다른 기기

공통 서버를 loopback에서 실행한 상태로 LAN HTTPS 게이트웨이를 준비한다. PC의 실제 사설 IP를 아래 변수에 넣는다. 인증서 생성에는 OpenSSL이 필요하다.

```powershell
.\.venv\Scripts\python.exe -B -m server.team_lan addresses
$demoIp = '192.168.0.10'
.\.venv\Scripts\python.exe -B -m server.team_lan prepare --ip $demoIp --output data/team-lan-new
.\.venv\Scripts\python.exe -B -m server.team_lan serve --bundle data/team-lan-new --upstream-port 18033 --port 18443
```

생성한 공개 CA 인증서를 시험 기기에서 신뢰하도록 설정하고 `https://해당IP:18443`에 접속한다. 인증서·브라우저 권한·방화벽 허용은 해당 기기에서 직접 확인한다. 개인키와 제어용 토큰은 공유하지 않는다. 화면과 API의 origin을 게이트웨이 주소로 맞추고, 휴대폰에서 실제 녹음·복구 동작을 별도로 검증한다.

## 검증된 소스 묶음

`TEAM_START.ps1`과 `TEAM_VERIFY.ps1`은 `release-manifest.json`이 있는 릴리즈의 `source/`에서 사용한다. 개발 저장소에서 바로 실행할 명령은 위 Python 명령이다. 소스 묶음은 새 폴더에 만들고 검증한다.

```powershell
.\.venv\Scripts\python.exe -B -m server.team_release_20261030 package --output outputs/team-release-new
.\.venv\Scripts\python.exe -B -m server.team_release_20261030 verify --release outputs/team-release-new
```

패키저는 허용된 소스·설정·화면·문서와 파일 해시를 기록한다. DB, 자격 증명, 설치된 의존성은 따로 준비한다. 소스 검사에서 실패하면 표시된 차이를 먼저 검토한다.

## 백업과 종료

실행 중인 공통 DB는 온라인 백업 도구로 새 폴더에 저장한다. 복원도 새 DB 파일에 수행한다.

```powershell
.\.venv\Scripts\python.exe -B -m server.team_backup backup --db data/team-common.db --output data/backups/run-new
.\.venv\Scripts\python.exe -B -m server.team_backup verify --backup data/backups/run-new
.\.venv\Scripts\python.exe -B -m server.team_backup restore --backup data/backups/run-new --db data/restored-new.db
```

일반 서버는 실행 터미널에서 `Ctrl+C`로 종료한다. 별도 시연 실행기의 종료 방식은 해당 실행기 안내를 따른다. 저장한 DB·초안·원음의 보관 위치를 확인한 뒤 새 시험을 시작한다.

관련 조작 순서는 [전체 시연](DEMO.md), 검사 명령은 [검증](TESTING.md)에 있다.
