# 서버 공통 실행본 정리용 파일 분류와 공유 상태

2026-10-30. 현재 작업 폴더와 **로컬에 있는 Git 참조**만 읽어 확인했다. 원격 fetch, checkout, stage, commit, 기존 파일 수정은 하지 않았다. 이번 문서는 서버 정리 범위를 구분하기 위한 기록이며, 원격의 실시간 최신 상태를 보장하지 않는다.

## 확인한 공유 기준

| 대상 | 확인한 커밋/상태 |
|---|---|
| 현재 작업 브랜치 | `codex/peer-review-server-20261029`, `38d2144` |
| 로컬 Claude 공유 브랜치 | `claude/voice-v2-ui-20261030`, `8e85895` |
| 로컬 원격 추적 참조 | `origin/claude/voice-v2-ui-20261030`도 `8e85895` |
| 로컬 `origin/main` 참조 | `32be3b3` |

Claude 인계 문서는 서버 스냅숏 `0d89a0b`, 음성 화면 `3abb9a9`, 연구 코드 `1028132`, 체험 안내 `8e85895`를 기록한다. 복사 당시 Codex 서버 작업이 계속 중이었으므로 공유 스냅숏 이후 변경은 별도로 대조해야 한다. 공유 브랜치에는 main에서 상속한 기존 구급대·병원 화면과 Claude 음성 화면이 이미 있다. 서버 작업에서 UI를 새로 복사하지 않는 것과 배포 기준 브랜치에 UI가 없는 것은 다르다.

현재 작업 폴더에는 기존 서버 수정 외에 팀원 UI·Claude 음성 화면·연구 실험·문서·검증 산출물이 섞여 있다. `git add .`, 작업 폴더 전체 복사, 기존 작업 폴더에서의 강제 브랜치 전환으로 정리하면 담당 범위 밖 변경을 함께 포함하거나 덮어쓸 수 있다. 서버 정리에는 명시한 파일 목록과 별도 검토 기준을 사용한다.

## 파일 그룹

| 그룹 | 포함할 항목 | 처리 기준 |
|---|---|---|
| 공통 업무 런타임 | `server/app.py`, `db.py`, `service.py`, `planning.py`, `solver_adapter.py`, `worker.py`, `inquiry_*`, `transport_*`, 자원 집계·대화·중요 사건·통화 기록 모듈 | 실제 import 경로와 변경 차이를 대조. 기존 파일 전체를 새 구현으로 바꾸지 않음 |
| 음성 API 런타임 | `server/voice_input.py`, `voice_terms.py`, `voice_r1.py`, `voice_followup.py`, `voice_audio.py`, `voice_commands.py`, `voice_lexicon.py`, `voice_treatment_review.py` | 현재 검증된 사본을 포함할 수 있지만 Claude와 공유한 해석 코드는 이번 작업에서 수정하지 않음 |
| 배정 미확정 표시 | `server/allocation_input_review.py`, `recommendation_view.py`와 해당 app/solver 연결 | 공유 스냅숏의 본문과 이미 일치하는 부분을 중복 변경으로 만들지 않음 |
| 서버 설정 | `config/paramedic_voice_terms.json`, `voice_commands.json`, `treatment_requirements.json`, `inquiry_v3_selected.json`, 새 baseline 잠금 | 서버 실행에 필요한 항목만 포함. 비밀키·운영 DB는 설정 파일로 복제하지 않음 |
| 솔버·문의 의존성 | `solver_improved.py`, `simulation/observed_inquiry.py` | 후자는 연구 디렉터리에 있지만 서버 문의 정책이 직접 import하므로 런타임 의존성에서 제외하면 안 됨 |
| 새 기준/검증 도구 | `server/allocation_baseline.py`, `voice_allocation_rehearsal.py`, 서버 공통 실행기와 전용 설정 | 새 도구를 명시적으로 추가하고 기존 알고리즘 v1의 목적함수·기본값은 유지 |
| 서버 시험 | `test_server*`, `test_planning*`, 문의·이송·대화·환자 수정·음성 API·기준 잠금·전체 흐름 시험 | 실제 실행한 모듈 목록과 수를 기록. 새 도구 시험과 기존 회귀를 합쳐 검사 |
| 서버 문서 | API·기준 잠금·전체 흐름 검증·공통 실행/복구 안내 | 현재 서버 계약을 설명. UI 담당 문서를 덮어써 최신 UI 상태를 지우지 않음 |
| UI 담당 범위 | `paramedic-ui/`, `patient-ui/`, `hospital-ui/`, `emergency-app/`, `test_paramedic*` | 기존 공유 기준의 UI를 사용하되 이번 서버 정리에서는 수정·자동 stage하지 않음 |
| 연구 별도 그룹 | `voice_lab/`, `inquiry_lab/`, 새 `simulation/g2_*`, `protected_*`, `inquiry_*` 실험과 결과 | 서버 런타임에 실제 import되는 예외를 제외하고 별도 연구 릴리스로 분리. 미채택 후보를 기본 정책에 넣지 않음 |
| 로컬 산출물 | `.codex-build/`, `build/`, `data/`, `outputs/`, DB/WAL/SHM, 녹음·백업·덤프 | 실행 자료와 검증 근거. 자동 커밋 대상이 아님. 특히 현재 `build/`는 미추적이므로 내용 분류 없이 포함하지 않음 |

Node용 `server/voice_*_probe.mjs`는 서버 운영 자체에는 필요 없지만 실제 `VoiceClient` 계약 검증 도구의 의존성이다. 이 검증은 기존 `paramedic-ui/voice-client.mjs`를 읽기 전용 import하므로, 서버만 검사한 시험과 클라이언트 계약 시험을 구분한다.

## Claude 공유 스냅숏과 현재 서버의 차이

공통 실행기 작성 전의 서버 Python 모듈과 주요 설정·솔버·문의 의존성 **69개 경로**를 `git show claude/voice-v2-ui-20261030:<path>` 결과와 대조했다. 새 공통 실행기 파일은 이 집계에 포함하지 않았다.

| 분류 | 수 | 경로 |
|---|---:|---|
| 바이트 동일 | 58 | 나머지 서버/설정 의존성 |
| CRLF/LF만 다름 | 7 | `config/inquiry_v3_selected.json`, `config/treatment_requirements.json`, `config/voice_commands.json`, `server/app.py`, `server/db.py`, `server/scenario.py`, `server/service.py` |
| 실제 내용 차이 | 1 | `server/voice_connection_check.py` |
| 공유 참조에 없음 | 3 | `config/server_allocation_baseline_20261030.json`, `server/allocation_baseline.py`, `server/voice_allocation_rehearsal.py` |

따라서 이 참조 기준에서는 최신 치료조건 검토·배정 입력 미확정 표시의 서버 본문이 이미 공유돼 있다. 전체 음성 서버를 다시 커밋하는 것보다 새 기준·전체 흐름 도구·검증기 차이·공통 실행기를 작은 변경 묶음으로 정리할 수 있다. 이것은 69개 경로의 대조 결과이며, 저장소 전체가 동일하다는 뜻이 아니다.

새 검증 중 공유 참조에 없는 시험은 `test_allocation_input_review_http.py`, `test_allocation_baseline.py`, `test_voice_allocation_rehearsal.py`, `test_voice_allocation_transport_flow.py`다. 미확정 표시의 단위/통합 시험과 치료조건 검토 단위/HTTP 시험은 이미 공유 참조에 있다.

재대조 시 읽기 전용 명령 예시:

```powershell
git rev-parse claude/voice-v2-ui-20261030 origin/claude/voice-v2-ui-20261030 origin/main
git status --short --untracked-files=all
git diff claude/voice-v2-ui-20261030 -- server/voice_connection_check.py
git ls-tree -r --name-only claude/voice-v2-ui-20261030 -- server config
```

`git diff <ref>`만으로 미추적 파일의 내용을 모두 볼 수는 없다. 새 파일 목록도 따로 검사해야 한다. 실제 공유 전 root 담당자가 원격을 갱신했다면 위 로컬 참조 집계를 다시 계산한다.

## 의존성과 실행 환경

루트에 **`requirements-portable.txt`가 이미 존재**하며 다음이 들어 있다.

```text
ortools==9.15.6755
python-docx
```

- 서버의 HTTP·SQLite·공공 API·Azure 짧은 음성 REST·카카오 경로 코드는 Python 표준 라이브러리를 사용한다.
- CP-SAT에는 OR-Tools가 필요하다. `solver_improved.py`는 프로젝트 `.solver_deps`를 먼저 import 경로에 추가하므로 일반 Python 환경과 실제 선택된 OR-Tools 경로가 다를 수 있다.
- `python-docx`는 문서 작업용이며 공통 업무 서버 기동의 필수 모듈은 아니다.
- Azure 음성 REST는 별도 Azure SDK를 설치하는 구조가 아니다. 키·endpoint는 환경변수로만 제공한다. 합성 텍스트·브라우저 인식문 시험에는 Azure 키가 필요 없다.
- Node는 클라이언트 계약·화면 시험에 사용하며 공통 API 서버 기동의 필수 조건은 아니다.
- 동결 기준 환경은 Python 3.12.14, OR-Tools 9.15.6755, SQLite 3.53.1이다. Python과 SQLite의 정확한 버전 차이도 재현 검사 실패로 보고되므로 ‘실행 가능’과 ‘기준 환경 일치’를 구분한다.

## 줄바꿈과 기준 잠금의 충돌 주의

현재 Git 설정은 `core.autocrlf=true`다. 확인한 작업 파일은 `server/solver_adapter.py`·`solver_improved.py`가 LF, `server/app.py`·`server/db.py`가 mixed, `config/treatment_requirements.json`이 CRLF였다. 인덱스의 해당 파일들은 LF다. `.gitattributes`는 확인되지 않았다.

기준 v1은 **정규화하지 않은 실제 바이트 SHA**를 잠근다. 따라서 새 체크아웃을 전부 LF로 받는 조치만으로 현재 v1과 일치한다고 보장할 수 없다. 반대로 Git의 자동 CRLF 변환도 원본 잠금을 바꿀 수 있다. 단순히 원본 잠금 해시를 새 환경에 맞춰 갱신하면 이전 검증 근거와 연결이 끊어진다.

공유본은 다음을 분리해 기록해야 한다.

1. 기존 v1 잠금·검증 소스 사본 보존.
2. 공유 코드의 실제 바이트와 줄바꿈 정책 확인.
3. 동결 일치 여부와 실행 호환 여부를 별도 진단.
4. 필요 시 이유를 남긴 새 기준 버전. 이전 결과를 새 기준 결과로 바꾸지 않음.

## 기존 검증 근거와 이번 정리의 한계

직전 서버 검증에는 관련 회귀 559개와 합성 전체 흐름 9개·114개 확인이 기록돼 있다. 이 목록은 `.codex-build/voice-allocation-e2e-20261030/server-verification.json`에 있다. 새 공통 실행기 도입 후에는 실행기 고유 시험과 두 실제 워커 기동·재시작 보존 확인을 추가해야 한다. 이 문서를 작성한 것 자체를 새 실행 검증 통과로 세지 않는다.

현재 조사에서는 비밀키 값, 실제 발화 원문, 업무 DB의 환자 값을 출력하지 않았다. 서버 공통 실행기 작성·Git 정리·공유 판단은 root 담당자가 진행한다.
