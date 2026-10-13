# 응급환자 병원 연결 시스템

## 팀원 연결본 실행

[설치와 전체 흐름 테스트 가이드](docs/TEAM_TEST_GUIDE.md)를 먼저 확인하세요. API 키 없이 실제 서울 병원20곳의 저장 정보를 사용해 환자·병원 화면을 함께 테스트할 수 있습니다.

```powershell
python -m server.team_demo --db data/team-demo.db --port 18027 --scenario normal
```

브라우저에서 <http://127.0.0.1:18027/public-flow-demo.html>을 엽니다. 병원 문의·응답·채팅은 로컬 DB에서만 처리되며 실제 병원에는 전송되지 않습니다. 원본 환자 입력 앱 실행과 조건 변경 테스트는 가이드에 있습니다. 기존 테스트 DB는 재시작해도 초기화하지 않습니다.


캡스톤 디자인 시연 프로젝트입니다. 환자·이송자가 필요한 진료 조건을 입력하면 후보 병원을 찾고, 병원에 환자별 수용 여부를 문의한 뒤 목적지 확정을 돕습니다. 이동 중 병원에 다른 환자가 유입되면 영향을 받는 예정 환자를 표시하고 병원 판단을 받아 조정할 수 있습니다.

현재 데이터와 시나리오는 가상입니다. 실제 병원·119 시스템과 연결된 서비스가 아니며, 의료적 진단이나 이송 지시를 제공하지 않습니다.

## 현재 구현

| 구성 | 상태 | 위치 |
|---|---|---|
| 공통 JSON API와 SQLite 서버 | 문의·응답·이송, 백그라운드 배정, 결과 조회, 돌발 유입 재평가 | [`server/`](server/README.md), [`docs/API.md`](docs/API.md) |
| 배정 솔버 실험 | 환자 우선순위, 이동시간, 진료 자리·전문팀 용량, 계산시간 제한과 대체안 | [`solver_improved.py`](solver_improved.py) |
| B0/B1 가상 비교 | 순차 문의와 병렬 문의·변경 알림 비교, 30개 seed와 민감도 분석 | [`simulation/`](simulation/run.py), [`simulation_results/summary.md`](simulation_results/summary.md) |
| 단독 화면 프로토타입 | 문의·응답 흐름을 보여주는 정적 시연 | [`prototype/index.html`](prototype/index.html) |
| 다양한 가상 데이터셋 | 배정 6,000건·이벤트 600건·서버 200건·경계조건 15건 | [`datasets/`](datasets/README.md) |

병원 웹은 공통 서버에 연결되어 있습니다. 환자 웹의 독립 DB를 공통 API로 전환하는 작업은 남아 있습니다. 후보 목록은 문의할 병원을 찾는 규칙 기반 목록이고, 수용 응답 이후 배정안은 별도의 추천 API로 제공합니다. 실제 공공 API 응답 검증·연결, 실제 점유 종료시각 연계, B2 비교는 진행 과제입니다. 자세한 내용은 [현재 상태와 다음 작업](docs/PROJECT_STATUS.md)에 기록했습니다.

## 빠른 실행

Python 3.12 이상을 권장합니다. 저장소 루트에서 실행합니다. `--seed`는 기존 `data/demo.db`를 지우고 가상 데이터를 다시 생성하므로 시연용 DB에만 사용하세요.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-portable.txt
.\.venv\Scripts\python.exe -m server.app --seed --scenario sudden --port 8000
```

서버 주소는 `http://127.0.0.1:8000`입니다. 배정 워커도 함께 시작합니다. 병원 웹 실행은 [화면 README](hospital-ui/README.md), 환자 웹 연결은 [API 명세](docs/API.md)를 참고하세요. 서버 종료는 터미널에서 `Ctrl+C`를 누릅니다. `py` 명령이 없다면 설치된 Python 3.12 이상의 실행 파일로 위 명령을 실행하면 됩니다.

테스트와 실험은 별도 터미널에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m unittest test_server test_solver_improved test_simulation test_planning test_datasets -v
.\.venv\Scripts\python.exe -m simulation.run --seeds 30 --sensitivity
```

솔버 데모는 `.\.venv\Scripts\python.exe solver_improved.py`로 실행할 수 있습니다. 화면 프로토타입은 [`prototype/index.html`](prototype/index.html)을 열어 볼 수 있으며, 서버와 데이터가 자동으로 동기화되지는 않습니다. 기존 `SETUP_WINDOWS.cmd`, `TEST_WINDOWS.cmd`, `RUN_EXPERIMENTS_WINDOWS.cmd`는 USB의 `portable-runtime.zip`을 사용하는 이전 방식입니다. 일반 GitHub 클론에는 ZIP이 포함되지 않습니다.

## 프로젝트 구조

| 경로 | 내용 |
|---|---|
| `server/` | 시연 API, DB, 가상 병원·돌발 유입 시나리오 |
| `hospital-ui/` | 병원 담당자용 웹 화면 개발 공간 |
| `patient-ui/` | 환자·이송자용 모바일 웹 화면 개발 공간 |
| `simulation/` | 순차 문의(B0)와 병렬 문의(B1) 비교 |
| `datasets/` | 다양한 합성 입력·사건·서버 시나리오, 생성·검증·재생 도구 |
| `prototype/` | 독립형 화면 프로토타입 |
| `docs/` | API 명세, 현재 상태, 문서 안내 |
| `solver_improved.py` | CP-SAT 배정 솔버 실험 |
| `test_*.py` | 서버·솔버·시뮬레이션 테스트 |
| `docs/design/캡스톤_전체구현기능_롱리스트.*` | 핵심 100개·선택 12개 기능 목록 |

조사·제안서·실험 문서는 [문서 안내](docs/README.md)에서 목적별로 찾을 수 있습니다. 연구와 시연의 범위는 [데이터 확보 조사](docs/research/데이터확보_조사_20260930.md)와 [배정 알고리즘 설계](docs/design/배정알고리즘_구체화_20260930.md)에 정리했습니다.

## 데이터와 보안

기본 병원·환자 정보는 모두 가상입니다. 공공 의료 API의 필드명과 실제 응답은 인증키로 아직 검증하지 않았습니다. API 키는 `.env`나 사용자 환경변수에만 보관하고 저장소에 올리지 않습니다. 로그인·병원별 접근 통제가 없는 상태이므로 실제 환자 정보를 입력하거나 인터넷에 공개 배포하지 않습니다.

문의에 대한 `ACCEPTED`는 환자별 수용 응답이고, 자원 점유는 목적지를 확정한 한 곳에만 반영됩니다. 신규 환자가 유입되어도 이동 중 환자의 목적지는 자동으로 바뀌지 않습니다. [API 명세](docs/API.md)에 상태와 예외 조건이 있습니다.
