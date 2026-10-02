# 응급환자 병원 연결 시스템

캡스톤 디자인 시연 프로젝트입니다. 환자·이송자가 필요한 진료 조건을 입력하면 후보 병원을 찾고, 병원에 환자별 수용 여부를 문의한 뒤 목적지 확정을 돕습니다. 이동 중 병원에 다른 환자가 유입되면 영향을 받는 예정 환자를 표시하고 병원 판단을 받아 조정할 수 있습니다.

현재 데이터와 시나리오는 가상입니다. 실제 병원·119 시스템과 연결된 서비스가 아니며, 의료적 진단이나 이송 지시를 제공하지 않습니다.

## 현재 구현

| 구성 | 상태 | 위치 |
|---|---|---|
| 공통 JSON API와 SQLite 서버 | 가상 병원·환자, 문의·응답, 목적지 확정, 이동·도착, 돌발 유입 처리 | [`server/`](server/README.md), [`docs/API.md`](docs/API.md) |
| 배정 솔버 실험 | 환자 우선순위, 이동시간, 진료 자리·전문팀 용량, 계산시간 제한과 대체안 | [`solver_improved.py`](solver_improved.py) |
| B0/B1 가상 비교 | 순차 문의와 병렬 문의·변경 알림 비교, 30개 seed와 민감도 분석 | [`simulation/`](simulation/run.py), [`simulation_results/summary.md`](simulation_results/summary.md) |
| 단독 화면 프로토타입 | 문의·응답 흐름을 보여주는 정적 시연 | [`prototype/index.html`](prototype/index.html) |

병원용 웹과 환자용 모바일 웹은 공통 서버에 연결되지 않았습니다. 서버의 후보 목록은 규칙 기반이며 솔버 결과가 아닙니다. 이동 중 치료 시작시각 재평가(A26), 실제 공공 API 응답 검증·연결, B2(솔버 연결) 비교도 진행 과제입니다. 자세한 내용은 [현재 상태와 다음 작업](docs/PROJECT_STATUS.md)에 기록했습니다.

## 빠른 실행

Python 3.12 이상을 권장합니다. 저장소 루트에서 실행합니다. `--seed`는 기존 `data/demo.db`를 지우고 가상 데이터를 다시 생성하므로 시연용 DB에만 사용하세요.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-portable.txt
.\.venv\Scripts\python.exe -m server.app --seed --scenario sudden --port 8000
```

서버 주소는 `http://127.0.0.1:8000`입니다. 브라우저 화면은 아직 연결되지 않았으므로 API 요청·응답 예시는 [API 명세](docs/API.md)를 참고하세요. 서버 종료는 터미널에서 `Ctrl+C`를 누릅니다. `py` 명령이 없다면 설치된 Python 3.12 이상의 실행 파일로 위 명령을 실행하면 됩니다.

테스트와 실험은 별도 터미널에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m unittest test_server test_solver_improved test_simulation -v
.\.venv\Scripts\python.exe -m simulation.run --seeds 30 --sensitivity
```

솔버 데모는 `.\.venv\Scripts\python.exe solver_improved.py`로 실행할 수 있습니다. 화면 프로토타입은 [`prototype/index.html`](prototype/index.html)을 열어 볼 수 있으며, 서버와 데이터가 자동으로 동기화되지는 않습니다. 기존 `SETUP_WINDOWS.cmd`, `TEST_WINDOWS.cmd`, `RUN_EXPERIMENTS_WINDOWS.cmd`는 USB의 `portable-runtime.zip`을 사용하는 이전 방식입니다. 일반 GitHub 클론에는 ZIP이 포함되지 않습니다.

## 프로젝트 구조

| 경로 | 내용 |
|---|---|
| `server/` | 시연 API, DB, 가상 병원·돌발 유입 시나리오 |
| `simulation/` | 순차 문의(B0)와 병렬 문의(B1) 비교 |
| `prototype/` | 독립형 화면 프로토타입 |
| `docs/` | API 명세, 현재 상태, 문서 안내 |
| `solver_improved.py` | CP-SAT 배정 솔버 실험 |
| `test_*.py` | 서버·솔버·시뮬레이션 테스트 |
| `docs/design/캡스톤_전체구현기능_롱리스트.*` | 핵심 100개·선택 12개 기능 목록 |

조사·제안서·실험 문서는 [문서 안내](docs/README.md)에서 목적별로 찾을 수 있습니다. 연구와 시연의 범위는 [데이터 확보 조사](docs/research/데이터확보_조사_20260930.md)와 [배정 알고리즘 설계](docs/design/배정알고리즘_구체화_20260930.md)에 정리했습니다.

## 데이터와 보안

기본 병원·환자 정보는 모두 가상입니다. 공공 의료 API의 필드명과 실제 응답은 인증키로 아직 검증하지 않았습니다. API 키는 `.env`나 사용자 환경변수에만 보관하고 저장소에 올리지 않습니다. 로그인·병원별 접근 통제가 없는 상태이므로 실제 환자 정보를 입력하거나 인터넷에 공개 배포하지 않습니다.

문의에 대한 `ACCEPTED`는 환자별 수용 응답이고, 자원 점유는 목적지를 확정한 한 곳에만 반영됩니다. 신규 환자가 유입되어도 이동 중 환자의 목적지는 자동으로 바뀌지 않습니다. [API 명세](docs/API.md)에 상태와 예외 조건이 있습니다.
