# 응급환자 병원 연결 시스템

구급대·이송자가 환자 상태와 치료 조건을 기록하고, 후보 병원에 문의한 뒤 병원의 수용 응답을 확인해 목적지를 확정하는 캡스톤 프로젝트입니다. 이송 중 조건 재확인, 대체 병원 문의, 도착·퇴실과 자원 점유를 같은 환자 기록으로 관리합니다.

현재 기능과 실행 범위는 [현재 상태](docs/PROJECT_STATUS.md), 전체 문서의 시작점은 [문서 안내](docs/README.md)에서 확인합니다. 2026-11-05 기준으로 정리했습니다.

## 빠른 실행

Python 3.12 이상 환경에서 저장소 루트에서 실행합니다. 실행마다 새 DB 이름을 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-server.txt
.\.venv\Scripts\python.exe -m server.chat_demo --db data/demo-new.db --port 18020
```

같은 서버의 구급대 입력 `/paramedic/quick-test.html`, 이송 진행 `/patient/`, 병원 `/hospital/?hid=H01`을 사용합니다. 환자를 등록한 뒤 생성된 같은 환자의 진행 링크로 이동합니다. 이 실행은 가상 병원과 문의 워커를 사용합니다. 병원 수용과 목적지 확정은 화면에서 담당자가 처리합니다.

배정 워커를 포함하는 공통 실행, 음성 전용 시험과 백업·복구 절차는 [실행 안내](docs/RUNNING.md)에 있습니다. 공공 API 적재와 필드 매핑은 [서버 안내](server/README.md)에서 확인합니다.

## 문서

| 주제 | 안내 |
|---|---|
| 구현 상태와 남은 작업 | [PROJECT_STATUS](docs/PROJECT_STATUS.md) |
| 설치·서버·기기 연결·백업 | [RUNNING](docs/RUNNING.md) |
| 시스템 구성과 상태 전환 | [ARCHITECTURE](docs/ARCHITECTURE.md) |
| 음성 입력·명령·자료 수집 | [VOICE](docs/VOICE.md) |
| 문의·배정 알고리즘과 연구 | [ALGORITHMS](docs/ALGORITHMS.md) |
| 자동 검사와 사람·기기 검증 | [TESTING](docs/TESTING.md) |
| 같은 환자의 전체 시연 | [DEMO](docs/DEMO.md) |
| 요청·응답 계약과 세부 자료 | [문서 안내](docs/README.md) |

## 구성

| 경로 | 역할 |
|---|---|
| `server/` | JSON HTTP API, SQLite, 문의·배정 워커, 이송 상태와 자원 원장 |
| `paramedic-ui/` | 구급대 입력, 기록 검토, 명령 연습과 원음 수집 |
| `patient-ui/` | 후보 조회·문의·목적지 확정·출발·대화 |
| `hospital-ui/` | 수용 응답·조건 판단·도착·퇴실·병원 운영 |
| `emergency-app/` | 팀원 환자 앱의 연결 어댑터 |
| `simulation/`, `inquiry_lab/` | 재생과 알고리즘 비교 실험 |
| `speech_eval/`, `voice_lab/`, `usability_eval/` | 음성·명령·사용성 평가 |
| `datasets/`, `config/` | 합성 사례·공개 명부와 고정 실험 설정 |
| `simulation_results/` | 연구 결과·실패 사례와 비교 근거 |

검사 실행은 `python scripts/test_suites.py --help`에서 선택합니다. 자료 출처, 가상 운영값, 저장된 실험 결과와 실제 사람·기기 측정 범위는 문서별로 표시합니다.
