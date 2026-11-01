# 테스트 묶음과 CI

2026-11-01. 루트의 테스트 파일은 옮기지 않는다. 실험 잠금이 파일 경로로 해시를 고정하고 있어서다. 대신 `scripts/test_suites.py`가 이름으로 묶음을 나눈다.

## 묶음

| 묶음 | 대상 | 어디서 |
|---|---|---|
| `core` | 서버·음성·팀 실행·자료·화면 테스트 (Python 86개 모듈 + Node 33개 파일) | 새 클론(고정 실행 환경), CI |
| `local-data` | git 밖 자료가 필요한 3개 (`test_server_replay_adapter`, `test_server_replay_regression`, `test_demo_casebook_20261030`) | 공용 작업 PC |
| `research` | 알고리즘·문의 정책 실험 검증 (454개 모듈) | 공용 작업 PC |
| `all` | 전부 | 공용 작업 PC |

```powershell
python scripts/test_suites.py list
python scripts/test_suites.py core --node <node 실행 파일>
python scripts/test_suites.py research
```

`--node`를 생략하면 `TEST_NODE` 환경변수나 PATH의 `node`를 쓴다. 서버 의존성은 `requirements-server.txt`로 설치하거나, 공용 작업 PC처럼 `.solver_deps`에 둔다. 결과는 JSON으로 출력하고 실패가 있으면 종료 코드 1이다.

새 제품 테스트는 `test_server`, `test_voice_`, `test_team_`, `test_paramedic` 등 `CORE_PREFIXES` 접두어로 이름을 지으면 `core`와 CI에 자동으로 들어간다. 그 밖의 이름은 `research`로 분류된다.

## CI

`.github/workflows/core-tests.yml`이 main으로 가는 PR과 main 반영마다 Ubuntu·Node 24에서 `core`를 실행한다. 11-01 새 클론 조건에서 Node 2,011개와 Python 1,256개가 통과했고 약 4분 걸렸다.

### 고정 실행 환경

팀 실행·배정 기준 테스트 10개 모듈(`test_team_runtime`, `test_team_lan`, `test_allocation_baseline`, `test_voice_allocation_rehearsal` 등)은 고정 실행 환경이 아니면 시작을 거부한다. 기준은 `config/server_allocation_baseline_20261030.json`의 Python 3.12.14, SQLite 3.53.1, OR-Tools 9.15.6755다.

공용 작업 PC의 Codex 런타임은 python-build-standalone `3.12.14+20260825` 빌드다. CI도 같은 릴리스의 Linux 빌드를 SHA-256 확인 후 내려받아 쓴다. 다른 Python으로 `core`를 돌리면 이 10개 모듈이 실패하고, 실행기 결과의 `environment.match`가 `false`로 나온다.

## 연구 검증이 공용 작업 PC에서만 재현되는 이유

11-01에 main을 새로 체크아웃해 전체 6,414개를 실행했더니 241개 모듈이 실패했다. 3개를 뺀 238개가 연구 검증이고, 같은 모듈이 공용 작업 폴더에서는 통과한다.

- 실험 잠금은 공용 작업 폴더 파일의 정확한 바이트로 해시를 고정했다. 이 폴더는 도구가 쓴 LF 파일(약 1,165개)과 git이 체크아웃한 CRLF 파일(약 393개)이 섞여 있어, LF든 CRLF든 한 방식의 체크아웃으로는 모두 재현되지 않는다.
- 52개가 넘는 연구 모듈이 git 밖 자료를 읽는다. `.codex-build`(Codex 작업 공간), `simulation_results` 원자료, `outputs/`다.
- 서버 기준 소스 21개는 `.gitattributes`의 `-text` 지정으로 새 클론에서도 기준 해시가 맞는다.

**기존 잠금과 해시는 고치지 않는다.** 공용 작업 PC가 바뀌면 연구 검증은 백업한 작업 폴더를 그대로 복원해 실행한다.

## 새 실험 규칙

- 잠금에 넣는 소스 해시는 `scripts/source_hash.py`(CRLF를 LF로 통일한 SHA-256)로 계산한다. 정확한 바이트가 꼭 필요하면 해당 파일을 `.gitattributes`에서 `-text`로 지정한 뒤 잠근다.
- 재현에 필요한 입력은 git에 넣을 수 있는 크기로 저장소 안에 둔다. `.codex-build`나 `outputs/` 같은 git 밖 경로를 코드에서 직접 읽지 않는다.

## 알려진 문제

| 테스트 | 상태 | 원인 |
|---|---|---|
| `test_public_offer_audit`, `test_c1_incumbent_audit`, `test_offer_reserve_audit` | 공용 작업 PC에서도 실패. 실행기가 '알려진 과거 감사 실패'로 따로 보고 | 10-29에 `server/inquiry_policies.py`가 legacy `reply`를 결정 시간 표본에서 빼도록 바뀌었다(피어리뷰 대응). 10-20 전후 실험은 바꾸기 전 규칙으로 회신 표본을 셌으므로 현재 코드로 다시 계산하면 `global_reply_samples`가 1에서 0으로 달라진다. 재현하려면 10-28 버전(`6fc079b`)의 서버 모듈로 다시 계산해야 한다 |
| `test_server_inquiry_experiment` | 11-01 해결 | DB 초기화가 음성 사전 seed 행에 현재 시각을 기록해(`server/voice_lexicon.py`), 실행이 1초 경계를 넘으면 초기 스냅샷 해시가 달라졌다. 테스트에서 그 모듈의 시계만 고정했다. 서버 코드와 잠금 파일은 바꾸지 않았다 |
| `test_hospital_operations_http.mjs` | 단독 실행 대상 아님 | `test_hospital_operations_http.py`가 격리 서버를 띄운 뒤 실행한다. 실행기는 Node 단독 실행에서 뺀다 |
