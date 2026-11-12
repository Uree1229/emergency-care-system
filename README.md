# 응급환자 병원 연결 시스템

구급대가 환자 상태와 치료 조건을 기록하면 공통 서버가 후보 병원에 수용 문의를 보냅니다. 병원은 수용 여부를 회신하고 이송자는 목적지를 확정합니다. 출발, 이송 중 조건 재검토, 도착과 퇴실까지 같은 환자 ID로 이어지는 캡스톤 프로젝트입니다.

## 빠른 시연

Windows PowerShell에서 Python 3.12를 준비한 뒤 저장소 루트에서 실행합니다. 아래 명령은 실행할 때마다 새 가상 DB를 만듭니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements/requirements-server.txt
$demoDb = 'data/demo-' + [guid]::NewGuid().ToString('N') + '.db'
.\.venv\Scripts\python.exe -B -m server.chat_demo --db $demoDb --port 18020
```

1. [구급대 화면](http://127.0.0.1:18020/paramedic/)에서 가상 환자를 입력하고 등록합니다.
2. 등록 결과의 이송 링크에서 문의를 시작합니다. [병원 H01](http://127.0.0.1:18020/hospital/?hid=H01)에 문의가 들어오면 수용으로 회신합니다. 문의를 받은 다른 가상 병원도 상단에서 선택할 수 있습니다.
3. 이송 화면에서 목적지를 확정하고 출발합니다. 해당 병원 화면의 **도착 예정 · 이송 대화**에서 같은 환자의 도착을 확인합니다.

이 시연은 가상 병원과 거리 기반 이동시간을 사용합니다. 문의·배정 워커를 함께 켜거나 다른 기기에서 연결하려면 [실행 안내](docs/RUNNING.md)를 참고하세요. [전체 시연 순서](docs/DEMO.md)

## 저장소 구조

| 경로 | 역할 |
| --- | --- |
| `server/`, `config/` | 공통 JSON API, SQLite, 문의·배정 워커, 이송 상태와 자원 처리 |
| `paramedic-ui/`, `patient-ui/`, `hospital-ui/` | 구급대 기록, 이송 진행, 병원 수용·검토 화면 |
| `emergency-app/` | 별도 환자 프로필·음성 문진 앱(FastAPI). 접수를 공통 이송 흐름으로 연결 |
| `simulation/`, `inquiry_lab/`, `simulation_results/` | 배정 재생, 문의 정책 실험과 결과 |
| `speech_eval/`, `voice_lab/`, `usability_eval/` | 음성·명령·사용성 평가 도구 |
| `tests/core/`, `tests/browser/`, `tests/research/` | 서버·팀 실행, 화면, 알고리즘 연구 테스트 |
| `scripts/`, `requirements/` | 공통 검증·시연 실행 도구와 용도별 Python 의존성 |
| `docs/`, `datasets/`, `archive/` | 팀 문서와 발표 근거, 공개·합성 자료, 이전 산출물 |

`solver_improved.py`는 서버 배정과 검증 프로필이 경로·해시를 고정하는 실행 모듈이다.

## 개발과 검증

PR을 검토할 때는 현재 구현 상태와 팀 인계에서 화면별 작업 범위를 먼저 확인하세요. 요청·응답 계약과 연구 자료는 문서 목차에 모았습니다. [현재 구현 상태](docs/PROJECT_STATUS.md) · [팀 인계](docs/team-handoff/20261105/README.md) · [문서 목차](docs/README.md)

아래 검증 명령은 Git으로 클론한 저장소에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements/requirements-server.txt -r requirements/requirements-intake-test.txt
.\.venv\Scripts\python.exe scripts/test_suites.py list
.\.venv\Scripts\python.exe scripts/test_suites.py core
```

화면 테스트에는 Node 24가 필요합니다. 공통 실행·배정 기준을 검사할 때는 CI와 같은 고정 Python·SQLite·OR-Tools 버전을 사용하세요. `core`는 `main` 대상 PR과 `main` 푸시에서 실행합니다. [검증 안내](docs/TESTING.md)
