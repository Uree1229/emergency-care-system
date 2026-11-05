# 검증과 테스트

2026-11-05 기준. 테스트는 저장소 루트에서 실행한다. 현재 소스의 기능 회귀, 과거 실험 재현, 실제 사용자 측정을 구분해 기록한다.

## 기본 실행

```powershell
python -m pip install -r requirements-server.txt
python scripts/test_suites.py list
python scripts/test_suites.py core
```

`list`는 현재 파일명으로 테스트를 분류한다. 모듈 수와 통과 수는 실행 결과를 기준으로 읽는다. `core`는 Python 제품 검사와 Node 화면 검사를 함께 실행한다. Node는 `--node` 인자, `TEST_NODE`, PATH 순서로 선택한다.

| 묶음 | 목적 | 필요한 자료 |
|---|---|---|
| `core` | 서버·음성·환자/병원 화면·팀 실행·자료 계약 회귀 | 저장소 소스와 설치한 Python·Node 의존성 |
| `local-data` | 저장된 재생·발표 사례 검증 | Git 제외 `simulation_results/`, `outputs/` 원자료 |
| `research` | 문의 정책·배정 알고리즘의 봉인된 실험 재현 | 해당 실험의 원본 소스·입력·잠금·로컬 산출물 |
| `all` | Python 전체 묶음과 Node 화면 검사 | 위 자료와 각 실험의 실행 환경 |

```powershell
python scripts/test_suites.py local-data
python scripts/test_suites.py research
python scripts/test_suites.py all
```

실행기는 API 키 환경변수를 제거하고 결과 JSON에 실패·오류·생략·환경 일치 여부를 기록한다. `config/research_expected_failures_20261101.json`에 등록된 연구 모듈의 실패는 따로 세며 종료 코드에서 제외한다. 종료 코드 0과 함께 `expected_failures`, `unexpected_failures`, `environment.match`도 확인한다. 파일명에 따라 분류하므로 새 검사는 `scripts/test_suites.py`의 `CORE_PREFIXES`와 `LOCAL_DATA`를 함께 점검한다. `test_hospital_operations_http.mjs`는 Python HTTP 하네스가 실행한다.

## 실행 환경과 CI

공통 실행·배정 기준 검사는 `config/server_allocation_baseline_20261030.json`의 CPython 3.12.14, SQLite 3.53.1, OR-Tools 9.15.6755에 고정돼 있다. 다른 환경에서 발생한 기준 불일치는 기능 오류와 함께 별도 기록한다. 현재 터미널의 `python`이 고정 런타임인지 실행 전에 확인한다.

`.github/workflows/core-tests.yml`은 main 대상 PR과 main 반영 시 Node 24 및 SHA-256으로 고정한 Python 배포본으로 `core`를 실행한다. 새 클론에서 필요한 의존성을 설치하고 이 결과를 제품 회귀의 기준으로 삼는다. 과거 실행의 누적 통과 수를 최신 실행 결과로 사용하지 않는다.

연구 검사는 정확한 소스 바이트와 Git 제외 산출물에 묶인 경우가 있다. 과거 잠금을 현재 소스에 맞춰 다시 쓰면 원래 실험을 재현할 수 없으므로, 해당 소스·입력·결과를 함께 복원해 검사한다. 새 실험의 소스 해시는 `scripts/source_hash.py`를 사용한다. 재현 가능한 작은 입력은 저장소 안에 두고, 큰 원자료는 별도 백업의 위치·해시를 기록한다.

## 변경 범위별 확인

전체 검증 전에 변경한 부분을 먼저 확인할 수 있다. 다음 검사는 각각 새로운 임시 자료를 사용한다. 구체적인 실행 환경과 네트워크 허용 여부는 해당 검사의 설정을 따른다.

```powershell
python -m unittest test_voice_input test_voice_r1 test_voice_followup test_voice_connection_http -v
node --test test_paramedic_recording_control.mjs test_paramedic_critical_review.mjs test_paramedic_patient_records.mjs test_paramedic_draft_recovery.mjs
python -m unittest test_conversations test_transport_review test_arrival_accounting -v
```

음성 변경은 일반 항목 정확도 외에 수치 오답·누락, 증상 있음/없음/불확실, 시간·인용 문맥, 명령 오탐·누락을 확인한다. 저장·통합 변경은 같은 환자 ID, 버전 충돌, 응답 유실 후 동일 요청 재시도, 환자 전환 중 오래된 응답, 새로고침 복원을 확인한다. 임시 DB와 localhost HTTP를 쓰는 검사가 있어 샌드박스의 소켓 차단은 별도 실행 제한으로 기록한다.

## 음성 자료와 사람 평가

고정 합성 대본의 파서 결과와 실제 인식문 재처리는 회귀 자료다. 원음·화자·장비·시험 조건을 보관한 새 발화와 구분한다. CER/WER, 수치·부정 오류, 명령 오탐을 함께 보고, 실패·중단·무인식·결측을 원자료에 남긴다. 사용 흐름과 자료 수집은 [음성 안내](VOICE.md)를 따른다.

```powershell
python -m speech_eval evaluate --output outputs/speech_eval/new_reference_run
python -m usability_eval prepare --output outputs/usability_eval/new_preparation
python -m unittest test_usability_eval -v
```

사용성 시험은 같은 참여자·과업에서 수동/음성 순서를 교차 배치하고 입력부터 수정·검토·전송 완료까지 측정한다. 실제 관찰이 없는 준비 양식은 미측정이다. 화면·인식 엔진 버전이 다른 짝은 분리해 평가하며, 병원 문의 수를 병원의 검토시간으로 대신 쓰지 않는다.

남은 실제 환경 검증은 새 발화의 조용함/실제 소음 짝시험, 실제 마이크·휴대전화·다른 물리 PC, 같은 Wi-Fi 연결, 절전·탭 전환·통신 장애 복구, 수동/음성 사용성과 병원 업무시간 실측이다. 자동 DOM·합성 음원·로컬 HTTP 검사의 결과는 각각 수행한 범위로 보고한다.

## 병원 어댑터 통합 시연

최신 전체 이송·오류 대조·재전송 검증본은 로컬 `.codex-build/hospital-full-demo-20261105/`에 있다. Git의 기본 `server/`·화면과 별도로 구성된 실행본이며 새 가상 DB를 사용한다. 구급대 등록부터 H01 수용·출발·도착, 가상 수신기 순서 오류, 선행 이벤트 복구와 동일 이벤트 재전송을 검증했다. 해당 폴더의 README와 `composition.json`, 실행별 `ready.json` 및 검증 영수증을 함께 확인한다. Git 클론만으로 이 로컬 폴더가 설치되지는 않는다.

검증 기록에는 사용한 커밋/manifest, 환자 ID, 실행 모드와 워커 상태, 데이터 출처, 원본·결과 해시, 검사 명령과 종료 코드를 남긴다. 실제 기기의 3분 완주 리허설과 예비 녹화는 직접 시간을 재서 완료 여부를 정한다.