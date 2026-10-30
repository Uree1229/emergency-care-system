# 응급환자 병원 연결 시스템

## 팀원 연결본 실행

[설치와 전체 흐름 테스트 가이드](docs/TEAM_TEST_GUIDE.md)를 먼저 확인하세요. API 키 없이 실제 서울 병원20곳의 저장 정보를 사용해 환자·병원 화면을 함께 테스트할 수 있습니다.

```powershell
python -m server.team_demo --db data/team-demo.db --port 18027 --scenario normal
```

브라우저에서 <http://127.0.0.1:18027/public-flow-demo.html>을 엽니다. 병원 문의·응답·채팅은 로컬 DB에서만 처리되며 실제 병원에는 전송되지 않습니다. 원본 환자 입력 앱 실행과 조건 변경 테스트는 가이드에 있습니다. 기존 테스트 DB는 재시작해도 초기화하지 않습니다.

## 구급대원 음성 기록 체험

버튼을 누른 채로 환자 상태를 말하면 초안을 만들고, 빠진 항목을 질문하며, 바뀐 값을 이전 값과 함께 보여 줍니다. [체험 안내(말해 볼 문장·의견 남기는 법 포함)](docs/VOICE_TRYOUT_GUIDE.md)를 따라 하세요. 처음 한 번만 `--seed`를 붙입니다.

```powershell
python -m server.app --db data/voice-tryout.db --seed --port 8000 --no-planner --no-inquiry-worker
```

크롬에서 <http://127.0.0.1:8000/paramedic/quick-test.html>을 열고 마이크를 허용합니다. 가상 환자만 사용하며 환자 등록·병원 문의는 실행하지 않습니다.


캡스톤 디자인 시연 프로젝트입니다. 환자·이송자가 필요한 진료 조건을 입력하면 후보 병원을 찾고, 병원에 환자별 수용 여부를 문의한 뒤 목적지 확정을 돕습니다. 이동 중 병원에 다른 환자가 유입되면 영향을 받는 예정 환자를 표시하고 병원 판단을 받아 조정할 수 있습니다.

병원 기본·등록 정보와 공개 가용 관측은 공공 API에서 수집하고 있습니다. 환자·병원 운영 자원·응답·이송 시나리오는 가상이며, 실제 병원·119 운영 시스템과 연결된 서비스가 아닙니다. 의료적 진단이나 이송 지시를 제공하지 않습니다.

## 현재 구현

| 구성 | 상태 | 위치 |
|---|---|---|
| 공통 JSON API와 SQLite 서버 | 문의·응답·이송, 백그라운드 배정, 결과 조회, 돌발 유입 재평가 | [`server/`](server/README.md), [`docs/API.md`](docs/API.md) |
| 모바일·병원 연결 화면 | 공통 저장·문의·질문·수용·사람 확정·대화·조건 재확인·대체 목적지 변경·도착/퇴실 | [`통합 실행과 검증`](docs/UI_INTEGRATION.md), [`patient-ui/`](patient-ui/README.md), [`hospital-ui/`](hospital-ui/README.md) |
| 팀원 원본 환자 앱 연결 | 등록·상태·초기 GPS·동의 전송 후 공통 이송 화면으로 연결 | [`945c413 연결과 검증`](docs/TEAM_PATIENT_CONNECTION_20261013.md), [`emergency-app/`](emergency-app/README.md) |
| 배정 솔버 실험 | 환자 우선순위, 이동시간, 진료 자리·전문팀 용량, 계산시간 제한과 대체안 | [`solver_improved.py`](solver_improved.py) |
| B0/B1 가상 비교 | 순차 문의와 병렬 문의·변경 알림 비교, 30개 seed와 민감도 분석 | [`simulation/`](simulation/run.py), [`simulation_results/summary.md`](simulation_results/summary.md) |
| 지도 기반 이송 재생 | 실제 도로·119 출동·이송·전원, 총 21사례·82분기(도로 10개+합성 문의 실험 11개), 현재 상태·군별 대기 표시 | [`simulation/README.md`](simulation/README.md), [`RUN_MAP_SIMULATION.cmd`](RUN_MAP_SIMULATION.cmd) |
| 실제 도로 입력 반복 비교 | 개발 seed 20개·4가지 다수 환자 조건·800개 정책 분기, 문의/선정/도착/미배정 비교 | [`실험 보고서`](simulation_results/map_comparison.md), [`실험 실행기`](simulation/replay_experiment.py) |
| 문의 부하·조건 변경 검증 | FIFO 검토 대기열, 개발/검증 분리, 문의 규모·변경 알림·대기열 효과 672개 분기 비교 | [`검증 결과`](simulation_results/workload_validation.md), [`핸드오프`](docs/HANDOFF_20261011.md) |
| 관측 응답 기반 문의 규모 | 고정 1/3/5곳·관측형 4후보, 신규 seed·8조건·1,312분기 검증, 현재 효과 미미 | [`관측형 검증`](simulation_results/adaptive_inquiry.md), [`실행기`](simulation/adaptive_experiment.py) |
| 병원별 관측 문의 V2 | 새 16조건·2,624분기 비교, 고정1곳 대비 가상 치료 시작17.27→14.84분·문의량 유사, 높은 우선순위 미시작1명 증가 | [`병원별 관측 검증`](simulation_results/observed_inquiry.md), [`실행기`](simulation/observed_experiment.py) |
| 우선순위 보호 정책 | 새 16조건·2,624분기 비교, V2 대비 높은 군 미시작 346→223명, 중간·낮은 군 합계 122명 증가. 전체 842→841명·문의 감소 없음 | [`검증 결과`](simulation_results/priority_protection_corrected.md), [`독립 감사`](simulation_results/priority_protection_corrected_audit.json) |
| 단독 화면 프로토타입 | 문의·응답 흐름을 보여주는 정적 시연 | [`prototype/index.html`](prototype/index.html) |
| 다양한 가상 데이터셋 | 배정 6,000건·이벤트 600건·서버 200건·경계조건 15건 | [`datasets/`](datasets/README.md) |

모바일·병원 연결본은 같은 공통 서버와 DB를 사용합니다. `patient-ui/`에는 별도 환자 DB가 없습니다. 후보 목록은 문의할 병원을 찾는 규칙 기반 목록이고, 수용 응답 이후 배정안은 별도의 추천 API로 제공합니다. 공공 API의 실제 수집·치료조건 평가 연결은 완료한 범위가 있으며, 실제 점유 종료시각 연계와 B2 비교는 진행 과제입니다. 자세한 내용은 [현재 상태와 다음 작업](docs/PROJECT_STATUS.md)에 기록했습니다.

## 빠른 실행

연결된 두 화면을 가상 데이터로 바로 실행하려면 `python -m server.chat_demo --db data/ui-integration-demo.db --port 18020`을 사용합니다. 같은 서버의 `/patient/`, `/hospital/?hid=H04`를 엽니다. 기존 DB를 초기화하지 않으며 [화면 역할과 실행](docs/UI_INTEGRATION.md)에 전체 흐름을 정리했습니다.

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

시연용 DB와 실제 공공 자료 DB를 구분합니다. 2026-10-11 기준 병원 34곳에 NMC·심평원 자료를 연결했으며, 상세한 제공·누락 범위는 [수집 결과](docs/research/API추가수집_치료조건판정연결_20261011.md)에 기록했습니다. 등록 시설·인력 수는 현재 환자의 수용 확답이 아닙니다. API 키는 `.env`나 사용자 환경변수에만 보관하고 저장소에 올리지 않습니다. 로그인·병원별 접근 통제가 없는 상태이므로 실제 환자 정보를 입력하거나 인터넷에 공개 배포하지 않습니다.

문의에 대한 `ACCEPTED`는 환자별 수용 응답이고, 자원 점유는 목적지를 확정한 한 곳에만 반영됩니다. 신규 환자가 유입되어도 이동 중 환자의 목적지는 자동으로 바뀌지 않습니다. [API 명세](docs/API.md)에 상태와 예외 조건이 있습니다.
