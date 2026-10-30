# DB 용어사전 연계와 음성 명령 확장

2026-10-30. 서버 담당 작업. `paramedic-ui/`와 팀원의 프론트엔드는 수정하지 않았다. Claude의 `_auto_mode()` 함수와 자동모드의 화면 확인 요구도 유지했다.

## 1. 연결한 자료와 역할

| 자료 | 저장한 값 | 적재한 용어행 | 용도 |
|---|---|---:|---|
| 보건복지부 KOSTOM 보건의료용어표준 진단 v7.0 | 용어코드·개념코드·한글명·영문명 | 97,832 | 표준 용어·동의 개념을 조회하는 참고 DB |
| 심평원 상병마스터 20250930 | 상병기호·한글명·영문명 | 47,796 | 코드·명칭 조회. 원본 47,798행 중 완전 중복 2행 제외 |
| 프로젝트 증상·측정 사전 | 기존 21개 증상, 7개 측정 필드의 동의어 | 실행 대상 28개 | 인식문을 현재 환자정보 칸에 연결 |
| 프로젝트 R1 규칙 | AVPU·주증상·연령 구분 등 명시적 표현 | 규칙 묶음 35개 | 기존 R1 추출 규칙의 표현을 DB에서 관리 |
| 프로젝트 명령 풀 v1.0.1 | 의도·고정 표현·문법·항목명·라벨 예문 | 명령 의도 12종 | 조회·수정·삭제·취소 등 분류 |

용어행 수는 서로 다른 의학 개념 수나 지원 가능한 발화 수가 아니다. 같은 개념의 여러 명칭이 별도 용어행일 수 있다.

공식 출처:

- [HINS 보건의료용어표준 다운로드](https://www.hins.or.kr/menu/viewMenu.do?menuNo=4050100): `보건의료용어표준_V7.0_진단.xlsx`, v7.0은 2022년 고시 2022-229호 자료다. 공개 다운로드의 공공누리 제1유형 출처표시 조건을 확인했다. KOSTOM 자체 4열만 가져왔고 SNOMED CT 등 다른 체계의 참조 열은 가져오지 않았다.
- [공공데이터포털 심평원 상병마스터](https://www.data.go.kr/data/15067467/fileData.do): 20250930 공개 CSV, CP949. 공공누리 제1유형 출처표시. KCD-8 기반 파일이므로 2026년 최신 KCD-9 자료라고 표시하지 않는다.

원본 XLSX·CSV와 KOSTOM 4열 변환본은 `data/reference/`에 보관한다. 내려받은 파일·변환본의 SHA256과 출처는 `.codex-build/voice-dictionary-20261030/kostom-source.json`, `reference-source.json`에 기록했다. `data/`는 기존 저장소 정책에 따라 Git에서 제외된다.

**공식 진단명 전체를 환자의 증상이나 명령으로 자동 실행하지 않는다.** 참고 용어 검색과 실행용 동의어를 구분했다. 같은 개념코드의 동의어도 프로젝트 항목에 연결하려면 의미와 입력 문맥을 검토해야 한다. 근거가 없는 진단·중증도·숫자를 생성하지 않는다.

## 2. DB 구조와 실행 연결

| 테이블 | 내용 |
|---|---|
| `voice_term_sources` | 출처 ID, 버전, 이용조건, URL, 원본/입력 파일 해시, 활성 버전 |
| `voice_terms` | 출처별 용어 ID·종류·한글명·영문명·개념코드 등 참고 메타데이터 |
| `voice_term_aliases` | 용어에 연결된 표현, 정규화된 검색어, 사용 여부, 검토자·추가 시각 |

기존 SQLite DB에 테이블을 추가한다. 환자·음성 시험 테이블을 초기화하지 않는다. 기존 JSON·R1 코드·명령 설정을 버전별로 seed하고 실제 요청에서는 해당 DB의 활성 동의어를 읽는다.

한 요청의 처리에는 분리된 사전 스냅샷을 사용한다. 다른 DB에서 추가한 표현이 전역 변수로 퍼지지 않는다. 추출할 수 있는 환자 필드·값 형식·수치 범위·명령 정규식은 서버 코드와 검토된 설정으로 고정하며, 참고 CSV의 내용이 코드나 정규식으로 실행되지 않는다.

같은 버전의 반복 초기화에서는 검토해 추가한 동의어와 비활성 상태를 유지한다. 설정 내용이 바뀌면 내용 해시를 포함한 새 버전을 활성화하고 이전 버전은 보관한다. 이전 버전의 사용자 추가 동의어는 새 버전에 자동 이관하지 않으므로 별도 재검토·등록이 필요하다.

공식 자료의 동일 코드에 여러 명칭이 있는 경우를 보존한다. 내부 `term_id`와 실제 `code`를 구분하며 KOSTOM 개념코드는 `reference_fields.concept_code`에 있다. 참고 자료 import는 트랜잭션으로 처리하여 중간에 잘못된 행이 있으면 새 버전 활성화와 행 적재를 모두 되돌린다.

## 3. 확장한 명령

고정 표현 **148개**, 라벨 예문 **117개**, 항목 이름과 존댓말·구어체를 처리하는 문법을 두었다. 아래 예문 외에도 지원하는 형태가 있지만 일반 자연어 전체를 이해한다고 보장하지 않는다.

| 의도 | 예시 | 서버 동작 |
|---|---|---|
| `review` 조회 | 혈압과 맥박 알려줘 / 케이타스 읽어줘 | 한 항목·여러 항목·전체 초안 읽기 |
| `edit` 정정 | 맥박을 92회로 고쳐 주세요 | 정정 이력으로 저장 |
| `add` 추가 | 체온 37.2도 기록해줘 | 명시한 정보를 추가 |
| `observe` 새 관찰 | 혈압이 지금 100에 80까지 떨어졌어 | 이전 값과 새 관찰 이력 보존 |
| `undo` 취소 | 방금 입력 취소해 주세요 | 지원하는 직전 초안 변경 되돌리기 |
| `missing` 누락 확인 | 빠진 항목 알려줘 | R1 누락과 다음 확인 질문 안내 |
| `repeat_question` 질문 반복 | 질문 다시 해줘 | 현재 질문을 다시 안내 |
| `remove` 항목 삭제 | 혈압 기록 삭제해 주세요 / 혈압과 맥박 지워줘 | 명시된 초안 항목만 삭제. 되돌리기 가능 |
| `history` 이력 | 변경 이력 알려줘 / 맥박 변경 이력 알려줘 | 전체 또는 해당 항목 변경 이력 최대 최근 10건 |
| `help` 도움말 | 사용법 알려줘 | 명령 예시와 지원 의도 안내 |
| `confirm` 확인 의도 | 확인 완료 | 자동모드에서는 화면 확인 안내 유지 |
| `submit` 제출 의도 | 병원에 보내 주세요 | 명시적 제출 API 안내. 음성만으로 환자 등록·문의하지 않음 |

명령 파서는 의도만 분류한다. 수치는 기존 의료용어·숫자 추출기로 다시 검사하고, 값이 없거나 모호하면 수정하지 않는다. DB에 검토해 추가한 측정 항목 동의어는 입력뿐 아니라 해당 항목 조회·삭제의 이름으로도 사용할 수 있다.

### 자동모드에서 특히 보완한 경계

- `맥박 95로 저장됐는지 알려줘`: 기록 조회이며 맥박을 95로 바꾸지 않는다. `read_only:true` 조회는 숫자가 있어도 조회로 라우팅한다.
- `혈압 120에80 확인해`: 기존 숫자 보고 규칙을 유지한다. 저장값 조회임을 명시한 문장과 구분한다.
- `맥박 95로 수정하지 마`, `맥박 95는 넣지 말고 확인해줘`: 부정 명령을 새 관찰로 우회하지 않는다.
- `맥박 95라고 말했을 때 수정해줘`: 조건부 명령을 실행하지 않는다.
- 인용·과거 명령·여러 제어 동작·전체 환자 삭제·범위가 불명확한 삭제는 실행하지 않는다. `구토 없음` 같은 일반 임상 부정은 계속 추출한다.

명확하지 않은 자동모드 제어 발화에는 `command.rejected:true`, `command.reason`, `operational_effect: "voice_command_needs_clarification"`, 안내용 `speak_text`를 반환한다. 원문 발화는 기록하되 초안 값·버전·확인 상태는 바꾸지 않는다. 부분 인식문은 기존처럼 미리보기이며 명령을 실행하지 않는다.

`remove`는 초안 항목 삭제다. 환자 삭제·이송 취소·병원 수용 취소가 아니다. `증상에서 흉통 삭제`처럼 목록의 일부만 지우는 표현은 현재 전체 증상 삭제로 해석하지 않고 재확인을 요구한다. 이 형태의 세부 목록 편집은 추가 구현 대상이다.

## 4. 앱에 추가로 제공하는 조회 API

```text
GET /api/voice/dictionary
GET /api/voice/dictionary/search?q=흉통&limit=10&offset=0
GET /api/voice/dictionary/search?q=흉통&source_id=kostom.diagnosis&limit=10
GET /api/voice/command-catalog
```

- `dictionary`: 출처별 버전·용어행 수·표현 수·참고 자료 여부.
- `search`: 실제 코드, 내부 term_id, 한글명·영문명, 개념코드, 출처·이용조건을 반환. q 필수, limit 1~100, offset 0~1000000.
- `command-catalog`: 의도별 표현·필드 이름·예문. 앱의 도움말에 연결 가능.
- 기존 `GET /api/voice/terms`도 DB의 동의어를 반영한다.
- capabilities의 `dictionary_revision`은 `voice-dictionary-20261030-v1`, `command_revision`은 `voice-commands-20261030-v3`다. 실제 명령 풀 버전은 command-catalog의 `version`을 확인한다.

세션의 기존 `transcripts`·`commands` API와 공통 멱등성·버전 계약은 유지한다. 화면은 새 의도와 재확인 안내를 표시하고, TTS와 마이크가 겹치지 않도록 해야 한다. 이번 작업은 서버이며 화면 연결 완료를 의미하지 않는다.

## 5. 로컬 관리 명령

기존 DB 경로를 명시한다. 다음 도구는 외부 서버에 연결하거나 환자정보를 변경하지 않는다.

```powershell
python -m server.voice_dictionary_cli --db .codex-build/paramedic-ui-20261030/demo.db status
python -m server.voice_dictionary_cli --db .codex-build/paramedic-ui-20261030/demo.db search --query 흉통 --source-id kostom.diagnosis --limit 10
python -m server.voice_dictionary_cli --db .codex-build/paramedic-ui-20261030/demo.db import-kostom --file data/reference/kostom_v7_diagnosis_terms.csv --version 7.0
python -m server.voice_dictionary_cli --db .codex-build/paramedic-ui-20261030/demo.db import-hira --file data/reference/hira_disease_master_20250930.csv --version 20250930
```

KOSTOM CSV 필수 열은 `용어코드,개념코드,영문명,한글명`이며 UTF-8 BOM 형식이다. HIRA CSV는 공개 원본 CP949와 원본 열 이름을 사용한다. 내려받은 표준 용어 자료는 최신 버전 여부와 이용조건을 확인하고 실제 버전으로 가져온다.

표현을 검토한 뒤 기존 실행 대상에 동의어를 추가하는 예시:

```powershell
python -m server.voice_dictionary_cli --db .codex-build/paramedic-ui-20261030/demo.db add-alias --source-id project.voice_terms --code measurement:pulse --alias 심박동수 --reviewed-by project-review
```

이 예시는 프로젝트 검토 표시이며 의료진 검수를 받았다는 뜻이 아니다. 같은 표현을 같은 종류의 다른 대상에 붙이는 충돌은 차단한다. 원격 사전 수정 API는 제공하지 않는다.

## 6. 검증과 기록

DB 사전·CSV import·버전·사용자 표현 보존, DB 동의어를 실제 HTTP 추출에 사용, R1·명령 분류, 삭제·되돌리기, 조회의 비변경성, 부정·조건 명령의 미적용을 검사했다. 실제 음성인식 정확도나 현장 사용시간을 측정한 시험은 아니다.

새 테스트는 `test_voice_lexicon.py`, `test_voice_commands_v3.py`, `test_voice_dictionary_commands_api.py`다. **관련 회귀403개 통과, 실패·오류0**, 실행 전후 관련 소스 해시 일치. 전체 결과는 `.codex-build/voice-dictionary-20261030/full-v2.json`, `full-v2.log`에 보관한다. 공식 파일 전체 적재 검증은 `official-import.json`, 기존 시연 DB 적용 결과는 `live-import.json`, 최종 실행본·보존 확인은 `live-verification.json`에 보관한다.

기존 12단계 음성 초안 검사와 11단계 가상 병원 흐름도 모두 통과했다. 최종 결과는 각각 `draft-flow-final-v2`, `patient-flow-final-v2` 폴더에 분리한다. 합성 문자열·가상 병원만 사용하며 실제 환자 또는 병원에 시험 문의를 넣지 않는다.

로컬 실행 DB는 이전과 같은 `.codex-build/paramedic-ui-20261030/demo.db`이며 백업은 `.codex-build/voice-dictionary-20261030/before-dictionary-migration.db`다. 테이블 추가와 사전 적재 전후 환자·문의·음성 시험 기록의 해시와 건수가 같음을 확인했다.
