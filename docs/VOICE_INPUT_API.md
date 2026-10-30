# 구급대원 음성 입력 API

치료조건 검토 연결: [변경안 미리보기·명시 적용·배정 재평가 API](VOICE_TREATMENT_REVIEW_API_20261030.md). 기존 환자 음성 초안 응답에 `session.treatment_review`를 추가했다. `/treatment-review`, `/treatment-review/preview`, `/treatment-review/apply`로 기존 유지·서버 제안·담당자 직접 조건을 검토하고 적용한다. 초안 확인과 치료조건 승인은 별도이며 값·조건·기존 수용·배정 입력 변경을 원자적으로 처리한다. 목적지 확정 전만 지원하고 기존 submit 호환 계약은 유지한다. 관련461개 회귀·초안12단계·가상 병원11단계 통과.

UI 핸드오프 5.1·5.5 반영: [조사 오인식 방지·자연스러운 질문 답변·검증 결과](VOICE_UI_HANDOFF_SERVER_FIX_20261030.md). `capabilities.handoff_parser_revision: "voice-ui-handoff-20261030-v1"`. 기존 `voice_revision`과 요청 형식은 유지한다. 질문 문맥의 `분당 20분`·`37도 2`·`30분 전쯤`처럼 의미를 보완한 답변에는 `answer.review_required`, `answer.interpretation_notes`와 해당 `field_metadata`의 같은 검토 표시를 제공한다. 실제 인식 원문은 발화와 근거에 보존하며, 이 표시는 미응답 재질문이나 수용·이송 판단을 뜻하지 않는다.

DB 사전·명령 확장: [공식 용어 DB와 명령12종·조회/삭제/이력 API](VOICE_DICTIONARY_AND_COMMANDS_20261030.md). KOSTOM v7 진단97,832행·심평원20250930 참고47,796행을 저장하고, 실행용 동의어는 프로젝트 검토 대상만 DB에서 읽는다. 새 `dictionary`, `dictionary/search`, `command-catalog` 조회 API와 명령의 `fields`, `rejected`, `read_only` 응답을 추가했다. 자동모드의 확인·제출은 기존 화면 조작 계약을 유지한다. 관련 회귀403개·초안12단계·가상 병원11단계 통과.

환자정보 연결 보완: [기존 값 보존·삭제·재확인 상태·가상 병원 전체 흐름 검증](VOICE_PATIENT_LINK_VERIFICATION_20261030.md). `capabilities.patient_projection_revision: "voice-patient-link-20261030-v1"`. 기존 API 본문은 유지하며 서버 회귀356개, 초안12단계·가상 환자/병원11단계가 통과했다. 실제 마이크·STT 정확도 시험은 별도다.

서버 연결 검증 업데이트: [전체 검증 결과와 Claude 화면 연결 목록](VOICE_CONNECTION_VERIFICATION_20261030.md). `connection_revision: "voice-connection-20261030-v1"`은 질문 답변 취소·미측정 상태 복원·증상 대안 필드의 재질문 해소 수정을 포함한다. 기존 API 본문은 유지한다. 미측정 상태도 버전 이력에 함께 저장해 값과 질문 상태가 원자적으로 복원된다. 서버 회귀174개·실제 HTTP 클라이언트12단계 통과이며 실제 음성 인식 정확도 검증과는 별개다.

2026-10-30 V2 추가: [서버·Claude 앱의 작업 경계와 상세 연결 계약](VOICE_V2_CLAUDE_HANDOFF.md).

- `mode: "auto"`와 자연스러운 새 상태 보고(`observe`)를 지원한다. 예: “혈압이 지금 100에 80까지 떨어졌어”. 새 관찰과 잘못 입력한 값의 정정(`edit`)을 이력에서 구분한다. 기존 dictation/command도 유지한다.
- 최신 `session`에는 `followup` 질문 계획과 `latest_changes` 변경 전후 값이 추가된다. 현재 값이 모호하면 이전 관찰을 보존하면서 `field_metadata[field].needs_clarification`을 표시한다.
- `GET {SESSION}/followup?actor_id=...&profile=voice_extended_v1`: 현재 누락·재확인 질문. `POST {SESSION}/followup/answers`: `question_id`, `utterance_id`, `text`와 공통 쓰기 필드로 답변한다. 원문·질문 문맥·변경 이력을 남긴다.
- 질문 답변에는 요청한 항목만 반영한다. 답이 모호하면 `answer.accepted:false`, 대상 외 내용은 `answer.ignored_fields`로 알린다. “미측정”은 정상·0으로 채우지 않고 미해결 상태로 보존하며 반복 질문에서 제외한다.
- 기본 12개·축약 8개 질문은 시연용 정보 수집 목록이다. 임상 필수 검사나 제출 허용 판정이 아니다. 기존 R1 필수 칸 검사와 별개다.
- 용어 띄어쓰기·명시적 약어·한글 수치·명시적 단위가 있는 소수 해석을 보완했다. 브라우저 음성 인식 모델 자체를 재학습한 것은 아니다. 인식문에서 사라진 의료용어·수치를 추측으로 복원하지 않는다.
- 자동모드의 “확인”은 화면 확인을 안내하는 `confirmation_requires_tap`이다. “전송” 역시 기존 명시적 제출 API를 별도로 요구한다. 질문·음성 시험만으로 환자 등록·병원 문의를 실행하지 않는다.

확인 버전: `/api/voice/capabilities`의 `voice_revision`은 `voice-v2-20261030`이다. 현재 로컬 18032 서버에 반영했고 기존 시험 기록은 백업·보존했다. V2 경계·기존 음성·평가 도구·로컬 HTTP 검증 총 161개 통과. 이는 실제 새 음성 인식 정확도 측정이 아니다.

별도 구현 앱: [이송 노트 실행·시험 방법](../paramedic-ui/README.md). 공통 서버의 `/paramedic/`에서 열며 팀원 앱과는 독립된 화면입니다.

2026년 10월 30일. 구급대원이 말한 관찰 내용을 구조화된 초안으로 확인하고, 정정·읽어주기·명시적 확인 뒤 기존 환자 등록 또는 수정 API에 연결하기 위한 서버 기능이다. 기존 구급대원·병원 화면은 수정하지 않았다. 문서의 환자·작업자·음성 예시는 모두 시연용이다.

## 1 지원 범위와 연결 방식

**서버가 마이크를 실시간으로 듣거나 WebSocket으로 음성을 스트리밍하지는 않는다.** 실시간 화면은 앱의 STT 부분·최종 결과 콜백을 `transcripts` API에 연결한다. 서버 `audio` API는 짧은 WAV 파일을 Azure STT로 인식하는 최종 결과 전용 경로다. 기본 `MediaRecorder`가 만드는 WebM 파일을 그대로 보낼 수 없으므로 앱에 WAV 인코더를 붙이거나 승인된 STT SDK 경로를 사용한다.

| 앱 동작 | 서버 연결 |
|---|---|
| 앱 STT에서 인식 중인 문장 표시 | `transcripts`, `is_final: false`. 미리보기이며 확정 초안·명령을 바꾸지 않음 |
| 앱 STT의 최종 문장 반영 | 같은 `utterance_id`의 `transcripts`, `is_final: true` |
| 짧은 녹음 파일 인식 | `audio`. 서버 Azure STT 설정이 있을 때만 사용 |
| 잘못 인식된 값 정정 또는 새 관찰 입력 | `fields` 또는 최종 `command` 문장 |
| 입력 내용 읽어주기 | `readback`의 `speak_text`를 앱 TTS로 재생 |
| 담당자 확인과 실제 저장 | `confirm` 다음 `submit` |

서버는 TTS 음성 파일을 만들지 않는다. 앱은 반환된 텍스트를 화면에 표시하고 필요한 경우 OS·브라우저 TTS를 사용한다. 인식 정확도와 실제 임상 판단을 보증하는 기능이 아니며, 증상에서 진단·우선순위·필요 진료과를 자동 추론하지 않는다.

`actor_id`는 세션을 만든 작업자 범위를 구분하는 시연용 값이다. 최대 80자이며 `client_request_id`는 최대 128자다. 실제 로그인·인증 토큰이 아니므로 운영 인증으로 취급하지 않는다. 원본 오디오는 저장하지 않으며 세션에는 초안·원문 근거·변경 이력·발화 상태가 남는다.

## 2 API 목록

```text
BASE = /api/voice
SESSION = /api/voice/sessions/{session_id}
```

| 메서드·경로 | 목적 | 요청 |
|---|---|---|
| `GET {BASE}/capabilities` | 텍스트·오디오 기능과 Azure 설정 가능 여부 조회 | 없음. 비밀키는 반환하지 않음 |
| `GET {BASE}/terms` | 앱 표시·인식에 사용할 용어사전 조회 | 없음 |
| `POST {BASE}/sessions` | 새 초안 또는 기존 환자 연결 세션 만들기 | `actor_id`, `client_request_id`, 선택 `patient_id` |
| `GET {SESSION}?actor_id=crew-1` | 현재 세션 조회 | `actor_id` 필수 |
| `GET {SESSION}/history?actor_id=crew-1` | 초안 변경 이력 조회 | `actor_id` 필수 |
| `POST {SESSION}/transcripts` | 부분·최종 인식 텍스트 반영 | 공통 필드, `utterance_id`, `text`, `is_final`, `mode` |
| `POST {SESSION}/audio` | WAV 짧은 녹음 최종 인식 | 공통 필드, `utterance_id`, `audio_base64`, `content_type`, `mode` |
| `PATCH {SESSION}/fields` | 담당자의 필드 정정 또는 새 관찰 | 공통 필드, `changes`, `change_kind`, 선택 `unset_fields` |
| `POST {SESSION}/commands` | 명시적으로 말한 음성 명령 실행 | 공통 필드, `text` |
| `GET {SESSION}/readback?actor_id=crew-1` | 현재 초안 읽어주기 텍스트 | 선택 `field`로 한 항목만 요청 |
| `POST {SESSION}/undo` | 직전 지원 변경 되돌리기 | 공통 필드 |
| `POST {SESSION}/confirm` | 담당자가 현재 초안을 확인했음을 기록 | 공통 필드, `confirmed: true` |
| `POST {SESSION}/submit` | 확인한 초안을 기존 등록·수정 계약에 적용 | 공통 필드, `context`, `start_inquiry`, 선택 `inquiry_policy` |

세션 생성 이외의 쓰기 요청 공통 필드는 다음과 같다. `expected_version`은 세션 버전이며 환자 API의 `version`과 구분한다.

```json
{
  "actor_id": "crew-1",
  "client_request_id": "voice-demo-002",
  "expected_version": 1
}
```

모든 쓰기는 멱등 키를 사용한다. 한 논리적 요청의 재시도에는 같은 ID·버전·본문을 그대로 사용한다. 같은 키를 다른 내용에 재사용하면 409다. 정확한 중복 요청은 최초 응답 스냅샷에 `duplicate: true`를 붙여 반환한다. 최초 요청 뒤 다른 작업이 있었다면 중복 응답은 최신 세션이 아닐 수 있으므로 새 편집 전 `GET {SESSION}`으로 다시 조회한다.

쓰기 시 현재 세션 버전과 정확히 일치하는 `expected_version`을 보낸다. 초안 편집은 버전을 올리지만 부분 미리보기·읽어주기·확인·제출은 항상 올리는 것이 아니므로 숫자를 앱에서 계산하지 않고 반환된 `session.version`을 사용한다. 409가 오면 최신 내용을 다시 읽고 담당자가 변경을 재검토한다. 최신 버전만 끼워 넣어 예전 요청을 자동 재전송하지 않는다. 같은 세션의 쓰기는 앱에서 순서대로 처리한다.

세션 생성·조회와 일반 쓰기의 응답은 `{"session": {...}}`을 기본으로 하며, 쓰기는 `duplicate`를 추가한다. `session`의 주요 필드는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `id`, `actor_id`, `patient_id` | 세션·작성자·연결 환자. 새 초안의 `patient_id`는 제출 전 `null` |
| `version`, `base_patient_version` | 현재 초안 버전·기존 환자 연결 시점의 환자 버전 |
| `state` | `DRAFT` 또는 적용 완료 `SUBMITTED` |
| `fields`, `field_metadata`, `warnings` | 구조화 값·항목별 출처/관찰 시각/원문 근거/변경 종류·검토 안내 |
| `baseline_summary` | 기존 환자 연결 시 원래 증상 자유서술 등 기준 기록. 구조화 초안과 구분 |
| `confirmed`, `confirmed_version`, `confirmed_at` | 현재 버전 확인 여부·확인한 버전·시각 |
| `can_undo`, `speak_text` | 되돌리기 가능 여부·현재 초안 읽어주기 텍스트 |
| `utterances` | 최종 발화의 `utterance_id`, `transcript`, `mode`, `provider`, 관찰·기록 시각 |
| `created_at`, `updated_at`, `submitted_at` | 서버 기록 시각. UTC epoch 초이며 제출 전 시각은 `null` 가능 |

`field_metadata`의 `source`는 `patient_record`, `voice`, `manual`, `voice_command` 등 값의 유입 경로이고 담당자 확인 여부는 세션의 확인 필드로 판단한다. `observed_at`은 서버가 발화를 받거나 편집을 처리한 시각이다. 실제 기기 측정 시각을 검증한 값이 아니며 기존 기록을 불러온 항목은 `null`일 수 있다. `history`는 `session_id`, `revisions`를 반환하며 각 변경의 이전·이후 값과 근거를 제공한다. 개인정보가 포함될 수 있으므로 일반 알림에 원문을 복사하지 않는다.

기존 자유서술 증상을 사전의 표준 증상으로 억지 변환하지 않는다. 원문은 `baseline_summary`에 보존하고 전체·증상 읽어주기에서 "기존 기록 증상"과 "현재 구조화 초안"으로 구분한다.

## 3 부분 인식, 최종 인식과 명령

```json
{
  "actor_id": "crew-1",
  "client_request_id": "voice-demo-partial-1",
  "expected_version": 1,
  "utterance_id": "utterance-1",
  "text": "환자 60세 남성 흉통",
  "is_final": false,
  "mode": "dictation"
}
```

- `utterance_id`는 한 번 말한 발화를 식별한다. 부분 결과와 그 최종 결과에 같은 값을 사용하되 각 요청에는 별도 `client_request_id`를 쓴다.
- `is_final: false`는 미리보기다. 초안의 확정 값과 확인 상태를 바꾸지 않고, 말 속에 명령이 있어도 실행하지 않는다.
- `is_final: true`, `mode: "dictation"`은 최종 텍스트의 지원 항목을 초안에 반영한다. 인식하지 못한 내용까지 사실로 저장하지 않는다.
- `mode: "command"`는 명시적인 명령 경로다. 일반 서술과 명령을 앱에서 구분해 보낸다. `commands` API도 같은 목적이다.
- 이미 최종 처리한 발화와 네트워크 재시도를 구분한다. 새 발화에는 새 `utterance_id`를 사용한다.

부분 응답은 `is_final: false`, `preview`, `preview_text`, `operational_effect: "preview_only"`를 반환한다. 최종 서술 응답은 `is_final: true`, `extraction: {fields, evidence, warnings}`, `operational_effect: "draft_only"`를 반환한다. `preview`를 `session.fields`에 합쳐 저장하지 않는다.

| 명령 예시 | 결과 |
|---|---|
| "읽어줘", "정리해줘", "현재 환자정보 읽어줘" | 현재 초안 읽어주기 텍스트 |
| "혈압 얼마로 저장됐어?" | 수축기·이완기 혈압을 함께 읽어주기 |
| "산소포화도만 알려줘" | 산소포화도만 읽어주기 |
| "나이 61세로 수정", "혈압110에70으로수정해" | 지원 값 추출에 성공하면 초안 정정 |
| "추가 호흡곤란 없음" | 새 관찰 추가 |
| "마지막 입력 취소", "방금 수정 취소해" | 지원되는 직전 초안 변경 되돌리기 |
| "확인 완료" | 현재 초안 버전 확인 |
| "전송해줘" | `explicit_submit_api_required`와 `next_endpoint` 안내. 실제 전송은 `submit` API로 수행 |

명령 응답의 `command`는 인식한 동작·대상 항목·명령 텍스트를 담는다. "확인, 전송해"처럼 확인과 전송을 한꺼번에 요구하는 문장은 지원하지 않으며 422 `VOICE_COMMAND_UNSUPPORTED`다. 지원 목록 밖의 명령이나 값이 해석되지 않은 정정은 실패로 표시하고 담당자가 직접 수정하게 한다.

인식 결과에 없는 증상은 부정으로 취급하지 않는다. 예를 들어 "흉통"만 말했을 때 "호흡곤란 없음"이 자동 생성되지 않는다. 긍정·부정·불확실한 증상과 각 근거를 구분해 표시하고, 앱은 원문과 구조화 값을 함께 보여 담당자가 확인할 수 있게 한다.

## 4 초안 필드와 수동 정정

| 항목 | 필드·값 |
|---|---|
| 나이·구분 | `age`: 0~130 정수, `age_group`: `0대`~`120대`의 십 년 단위, `sex`: `male`, `female`, `unknown` |
| 증상 | `symptoms`, `denied_symptoms`, `uncertain_symptoms`: 용어사전의 표준 증상 문자열 목록 |
| 의식 | `consciousness`: `alert`, `impaired`, `unresponsive`, `unknown` |
| 호흡 | `breathing`: `normal`, `abnormal`, `absent`, `unknown` |
| 혈압·맥박 | `systolic_bp`, `diastolic_bp`, `pulse` |
| 기타 측정 | `spo2`, `temperature`, `respiratory_rate`, `glucose` |
| 발생 시각 | `onset_at`: UTC epoch 초 정수. 미래 시각은 400 |

지원 필드·자료형·범위를 벗어나는 수동 입력은 오류로 처리한다. 수치 항목의 단위·범위는 `GET /api/voice/terms`의 `terms.measurements`에서 읽는다. 범위는 입력 형식 검사이며 정상·이상 판정 기준이 아니다. 같은 증상을 있음·없음·불확실 목록에 동시에 보내거나 `null`로 관찰을 대신하지 않는다.

```json
{
  "actor_id": "crew-1",
  "client_request_id": "voice-demo-correction-1",
  "expected_version": 2,
  "change_kind": "correction",
  "changes": {"age": 61, "spo2": 97},
  "unset_fields": ["temperature"]
}
```

`correction`은 잘못 입력·인식한 값의 정정이고, `observation`은 새로 확인한 관찰이다. `fields`의 목록 입력은 해당 목록을 교체한다. 최종 서술과 추가 명령은 새 증상을 합치고 같은 증상의 기존 긍정·부정·불확실 상태를 정리한다. 기존 값을 지울 때는 `unset_fields`를 명시한다. 서버는 삭제 의도를 이력에 남기며 제출 시 기존 나이·의식·호흡·발생 시각 등 대응 필드를 `null`로 실제 삭제한다. 누락된 키를 삭제나 정상 소견으로 해석하지 않는다.

정확한 나이가 있던 초안에 `age_group`만 새로 입력하면 기존 `age`를 추정값처럼 남기지 않고 삭제한다. 기존 환자에 적용할 때도 정확한 나이는 `null`이 된다. 정정·관찰·되돌리기 등 초안 변경이 생기면 종전 확인은 무효가 된다.

## 5 읽어주기, 확인과 실제 적용

권장 순서는 **입력 → 원문·구조화 값 검토 → 정정 → 읽어주기 → 담당자 확인 → 적용**이다. 읽어주기는 조회이며 담당자 확인을 대신하지 않는다. `confirm`의 확인은 해당 세션 버전에 묶인다. 확인 이후 내용이 바뀌면 다시 읽고 확인한 뒤 제출한다.

한 항목은 `GET {SESSION}/readback?actor_id=crew-1&field=spo2`처럼 조회한다. 허용 항목은 용어사전의 `readback_fields`이며 `blood_pressure`는 수축기·이완기 혈압을 함께 읽는 전용 항목이다. 쓰기 `changes`에는 `blood_pressure` 대신 `systolic_bp`, `diastolic_bp`를 사용한다.

```json
{
  "actor_id": "crew-1",
  "client_request_id": "voice-demo-confirm-1",
  "expected_version": 3,
  "confirmed": true
}
```

`submit`은 확인한 초안을 기존 업무 API에 연결하는 단계다. 새 환자 등록에는 `context`로 동의와 위치를 명시한다. 이송자 역할은 `transporter`로 처리한다. `required_capabilities`, `resource_demands`, 진료 프로필은 담당자가 별도로 명시한 값만 연결하며 증상 문장으로 만들어내지 않는다.

```json
{
  "actor_id": "crew-1",
  "client_request_id": "voice-demo-submit-1",
  "expected_version": 4,
  "context": {
    "consent": true,
    "lat": 35.145,
    "lon": 126.86,
    "required_capabilities": ["응급의학"],
    "resource_demands": {"ed_bed": 1}
  },
  "start_inquiry": false
}
```

새 환자의 `context`에서 지원하는 항목은 `consent`, `lat`, `lon`, `contact`, `relation`, `required_capabilities`, `resource_demands`, `treatment_profile_id`다. 진료 프로필은 명시한 ID만 기존 프로필 검증에 연결한다. 기존 환자 제출은 빈 `context` 또는 생략만 허용하며 원래 환자의 요구·프로필을 유지한다.

자동 문의를 시작하려는 경우에만 `start_inquiry: true`와 지원 `inquiry_policy`(예: `fixed_3`)를 명시한다. 음성 입력·확인·제출로 수용 응답이나 목적지가 자동 확정되지는 않는다.

| 연결 대상 상태 | 적용 결과 |
|---|---|
| 새 환자 | 동의·위치 등 기존 환자 생성 검사 후 등록 |
| 기존 환자, 목적지 확정 전 `WAITING`, 초안 값이 최초와 같음 | 음성 세션 기록만 저장. 환자 버전·기존 문의·수용 응답 유지 |
| 기존 환자, 목적지 확정 전 `WAITING`, 실제 변경 있음 | 기존 [환자정보 수정 계약](PATIENT_AMENDMENTS_API.md)으로 적용. 열린 기존 문의·수용 응답 종료와 병원 정보 변경 안내 발생 |
| 목적지 확정 뒤 | 새 초안·읽어주기는 가능하나 실제 적용은 현재 수정 계약에 따라 409 |

초안 편집만으로 기존 환자정보를 조용히 덮어쓰지 않는다. 기존 건의 실제 변경은 적용 전 문의 종료·재검토 효과를 앱에서 안내한다. 입력 없이 읽고 확인만 했거나 되돌리기로 최초 값에 돌아온 경우에는 불필요하게 기존 응답을 종료하지 않는다. 목적지 확정 뒤 임상정보를 바꾸는 별도 절차는 이번 기능에 포함하지 않는다.

기존 환자의 `version`이 세션 생성 이후 바뀌면 `VOICE_PATIENT_VERSION_CONFLICT`다. 새 세션에서 최신 환자정보를 검토한다. 기존 환자 필드인 나이·의식·호흡·발생 시각과 증상 요약을 연결하고 상세 측정값·긍정/부정/불확실 증상·출처·서버 시각·변경 종류는 `extra.voice_observations`에 함께 남긴다. 긴 `evidence` 인용 원문은 작업자 범위의 음성 세션·이력에 보존하고 병원에 공유하는 환자 요약에는 복사하지 않는다. 이는 기존 수정 API의 JSON 변경 본문 16,384자 제한과 필요한 정보만 공유하는 계약을 따른다.

성공 응답에는 `session`, `patient`, `operational_effect`, `candidates_endpoint`, `recommendation_endpoint`, `treatment_conditions_require_confirmation: true`가 있다. `operational_effect`는 새 등록·실제 변경이면 `patient_information_applied`, 기존 값이 같으면 `voice_record_saved_no_patient_change`다. 두 번째 경우는 환자 버전과 기존 수용 응답을 유지했음을 표시한다. 앱은 반환된 환자와 조회 경로로 기존 추천 흐름을 이어간다. 적용 완료 세션은 추가 편집할 수 없으므로 후속 관찰은 해당 `patient_id`로 새 세션을 만든다.

## 6 앱 연결 예시

아래 예시는 가상 내용의 호출 순서다. `makeWrite`는 요청 본문을 한 번 고정한다. 네트워크 오류 재시도는 반환된 같은 함수를 다시 실행해 ID·버전·본문을 유지한다. 409는 재시도 대신 최신 세션을 읽어 검토한다. 실제 앱은 버튼 중복 클릭과 동시 쓰기를 막는다.

```javascript
const base = "http://localhost:8000/api/voice";
const actorId = "crew-1";

async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(base + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.message), { status: response.status, data });
  return data;
}

const created = await api("/sessions", {
  method: "POST",
  body: { actor_id: actorId, client_request_id: crypto.randomUUID() },
});
let session = created.session;
const sessionPath = `/sessions/${encodeURIComponent(session.id)}`;

function makeWrite(action, extra = {}, method = "POST") {
  const body = {
    actor_id: actorId,
    client_request_id: crypto.randomUUID(),
    expected_version: session.version,
    ...extra,
  };
  return async () => {
    const result = await api(`${sessionPath}/${action}`, { method, body });
    session = result.session;
    return result;
  };
}

// 앱 STT의 최종 콜백에서 호출한다. 부분 콜백은 is_final: false로 별도 요청한다.
const sendFinal = makeWrite("transcripts", {
  utterance_id: "utterance-demo-1",
  text: "환자 60세 남성. 흉통. 호흡곤란 없음. 산소포화도 97",
  is_final: true,
  mode: "dictation",
});
await sendFinal();

const correctAge = makeWrite("fields", {
  changes: { age: 61 },
  change_kind: "correction",
}, "PATCH");
await correctAge();

const readback = await api(`${sessionPath}/readback?actor_id=${encodeURIComponent(actorId)}`);
// 화면에도 readback.speak_text를 일반 텍스트로 표시한다.
if ("speechSynthesis" in window) {
  const speech = new SpeechSynthesisUtterance(readback.speak_text);
  speech.lang = "ko-KR";
  speechSynthesis.speak(speech);
}

// 앱에서 담당자가 내용을 확인하고 '확인 후 적용'을 누른 경우에만 호출한다.
async function onConfirmAndSubmitClick() {
  const confirmDraft = makeWrite("confirm", { confirmed: true });
  await confirmDraft();
  const submitDraft = makeWrite("submit", {
    context: {
      consent: true, lat: 35.145, lon: 126.86,
      required_capabilities: ["응급의학"],
      resource_demands: { ed_bed: 1 },
    },
    start_inquiry: false,
  });
  return await submitDraft();
}
```

실제 환자정보를 브라우저 콘솔·URL·일반 로그로 복사하지 않는다. 입력 텍스트와 `speak_text`는 HTML이 아닌 일반 텍스트로 렌더링한다.

이 예시는 새 환자 등록 흐름이다. 기존 환자는 세션 생성에 `patient_id`를 넣고 제출 `context`는 비운다. `voice_record_saved_no_patient_change`가 오면 "확인 기록 저장, 환자정보 유지"로 표시하고 기존 수용 응답을 종료된 것으로 표시하지 않는다.

## 7 Azure 짧은 녹음 설정과 WAV 조건

텍스트 `transcripts` 경로는 Azure 키 없이 사용할 수 있다. `audio`는 서버 프로세스에 다음 두 환경변수가 있어야 한다.

- `AZURE_SPEECH_KEY`: Azure Speech 리소스 키.
- `AZURE_SPEECH_ENDPOINT`: `https://RESOURCE.cognitiveservices.azure.com` 형식의 해당 리소스 엔드포인트.

설정이 없으면 오디오 요청은 HTTP 503 `STT_NOT_CONFIGURED`다. 앱은 먼저 `capabilities`를 조회해 사용 가능한 경로를 표시한다. 서버에서 키를 설정하며 앱 소스·프런트엔드 환경변수·JSON 요청에 키를 넣지 않는다.

서버 PC의 PowerShell에서 실행하는 예시다. 키는 프롬프트에 입력하고 대화나 명령 문자열에 적지 않는다. `RESOURCE`는 실제 리소스 이름으로 바꾼다.

```powershell
$voiceSpeechKey = Read-Host 'Azure Speech 키' -AsSecureString
$voiceSpeechPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($voiceSpeechKey)
try {
    [Environment]::SetEnvironmentVariable(
        'AZURE_SPEECH_KEY',
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($voiceSpeechPointer),
        'User'
    )
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($voiceSpeechPointer)
    $voiceSpeechKey.Dispose()
}
[Environment]::SetEnvironmentVariable(
    'AZURE_SPEECH_ENDPOINT',
    'https://RESOURCE.cognitiveservices.azure.com',
    'User'
)

# 현재 PowerShell에서 서버를 시작하려면 사용자 설정을 현재 프로세스에 읽어온다.
$env:AZURE_SPEECH_KEY = [Environment]::GetEnvironmentVariable('AZURE_SPEECH_KEY', 'User')
$env:AZURE_SPEECH_ENDPOINT = [Environment]::GetEnvironmentVariable('AZURE_SPEECH_ENDPOINT', 'User')
python -m server.app --port 8000
```

오디오 본문은 공통 쓰기 필드와 `utterance_id`, `mode`, `content_type: "audio/wav"`, WAV 파일 바이트의 `audio_base64`를 포함한다. `text`와 `is_final`은 보내지 않는다. WAV는 PCM, 16 kHz, 모노, 16비트, 최대 60초다. Azure의 짧은 음성 REST API도 직접 전송 음성을 최대 60초로 제한하며 최종 결과만 제공한다. [Microsoft 공식 짧은 음성 REST API 문서](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text-short).

Data URL 접두어를 붙인 문자열이나 WebM·MP3 파일은 이 서버 계약의 입력이 아니다. 서버는 인식 후 원본 오디오를 보관하지 않는다.

## 8 오류 처리

일반 오류 응답은 `{"error": "코드", "message": "설명"}`이다. `capabilities.speech.configured`는 서버 설정 형식 여부이며 외부 STT 연결 성공을 보장하지 않는다.

| HTTP·코드 | 앱 처리 |
|---|---|
| 400 `INVALID`, `INVALID_VOICE_FIELDS` | 요청 모양·필드·범위를 확인하고 담당자에게 보완 항목 표시 |
| 403 `FORBIDDEN`, 404 `NOT_FOUND` | 작업자 범위·세션 ID 확인 |
| 409 `VOICE_VERSION_CONFLICT` | 최신 세션을 조회하고 변경 재검토 |
| 409 `VOICE_REQUEST_CONFLICT` | 요청 ID를 다른 작업에 재사용했는지 확인 |
| 409 `VOICE_UTTERANCE_FINALIZED` | 확정 발화를 덮어쓰지 말고 새 발화·수정 API 사용 |
| 409 `VOICE_SESSION_SUBMITTED` | 적용 완료 세션 대신 새 세션 생성 |
| 409 `VOICE_SESSION_FULL` | 최종 발화 100개 제한. 새 세션 사용 |
| 409 `VOICE_NOTHING_TO_UNDO` | 되돌릴 지원 변경이 없음을 표시 |
| 400 `VOICE_EMPTY_DRAFT` | 값이 없는 초안에 대한 확인을 막고 보완 |
| 400/409 `VOICE_CONFIRMATION_REQUIRED` | `confirmed: true`와 현재 버전의 명시 확인 필요 |
| 409 `VOICE_PATIENT_VERSION_CONFLICT` | 새 세션에서 최신 환자정보로 재검토 |
| 422 `VOICE_COMMAND_UNSUPPORTED`, `VOICE_COMMAND_UNRESOLVED` | 지원 명령으로 다시 말하거나 필드 직접 수정 |
| 400 `STT_INVALID_AUDIO`, `STT_AUDIO_TOO_LARGE` | WAV 형식·길이를 고쳐 새 녹음 |
| 503 `STT_NOT_CONFIGURED` | 서버 설정을 확인하고 텍스트 STT 경로 사용 |
| 422 `STT_NO_MATCH` | 인식 문장 없음. 다시 녹음하거나 텍스트 보완 |
| 503 `STT_UNAVAILABLE`, 504 `STT_TIMEOUT`, 502 `STT_UPSTREAM_ERROR` | 외부 인식 실패를 표시하고 같은 요청 재시도 또는 텍스트 보완 |

동의·환자 상태·진료 프로필·자동 문의 등 기존 API에서 발생한 오류도 그대로 적용한다. 음성 API가 그 검사를 우회하지 않는다.

## 9 화면 담당자 확인 항목

- 부분 결과는 임시 표시하고 최종 초안과 구분한다. 부분 문장으로 정정·삭제·확인 명령을 실행하지 않는다.
- 구조화 값·원문·출처·확인 상태를 함께 검토할 수 있게 한다. 언급되지 않은 증상을 "없음"으로 채우지 않는다.
- 실패한 네트워크 요청은 같은 본문·키로 재시도한다. 409면 최신 세션을 읽고 담당자의 재검토를 받는다.
- 확인 후 입력이 바뀌면 확인 표시를 해제한다. 새 확인 없이 제출 버튼을 활성화하지 않는다.
- 읽어주기는 앱 TTS이고 현재 값 확인은 별도 담당자 동작임을 표시한다.
- 기존 환자의 실제 변경 제출에는 문의 종료·병원 재검토 효과를 안내하고, 값이 같은 제출은 환자정보 유지로 표시한다. 확정 후 적용 제한도 표시한다.
- Azure 설정이 없으면 텍스트 경로로 연결하고 오디오 인식이 성공한 것처럼 표시하지 않는다.

기존 API의 상세 계약은 [공통 API](API.md), [환자정보 수정](PATIENT_AMENDMENTS_API.md), [자동 문의](INQUIRY_WORKFLOW.md)를 참고한다.

## 10 검증 범위

문서의 JavaScript를 가상 환자·임시 DB·실제 로컬 HTTP 서버로 실행해 생성→최종 인식→정정→읽어주기→확인→제출 순서를 확인했다. 기존 환자의 변경 없는 확인과 편집 후 되돌리기 제출에서 환자 버전·기존 수용 응답이 유지되는 것도 확인했다. 명령 예시와 `blood_pressure` 읽기 항목은 실제 파서·용어사전으로 대조했다. Azure 실서비스의 실제 녹음 인식 정확도는 이 로컬 검증에 포함하지 않는다.

## 11 R1 최소 입력 항목(2026-10-30 추가)

[R1 최소 입력 항목](R1_MINIMUM_INPUT.md)에 맞춰 초안에 다음 칸이 추가됐다. 받아쓰기·미리보기·수정 명령에서 `server/voice_r1.py`가 찾아 더한다. `voice_terms.extract`의 결과 자체는 그대로다.

| 칸 | 값 | 비고 |
|---|---|---|
| `avpu` | `A`·`V`·`P`·`U`·`unknown` | `consciousness`와 자동으로 맞춘다(A↔alert, V·P→impaired, U↔unresponsive). '의식 있음'만으로는 A를 넣지 않는다 |
| `chief_complaint` | `cardiac`·`stroke`·`altered_seizure`·`dyspnea`·`trauma`·`bleeding`·`abdominal`·`obstetric`·`toxic_env`·`other` | 말한 증상을 R1 10묶음으로 옮긴 제안. 여러 개면 먼저 말한 것을 고르고 경고한다 |
| `age_category` | `adult`·`child`·`neonate`·`pregnant`·`unknown` | 나이·`age_group`(10년 단위)·말한 표현으로 정한다. 기존 `age_group`은 그대로 10년 단위다 |
| `pre_ktas` | 1~5·`unclassified` | 말한 경우에만 채운다. 시스템이 추론하지 않는다 |
| 2차 고려사항 | `stemi_suspected`, `injury_region`, `labor`, `pregnancy_weeks`, `seizure_ongoing`, `bleeding_risk`, `high_risk_mechanism` | R1 6절 |
| 측정값 | `pain_score`(0~10), `gcs`(3~15) | |

- **명령:** `빠진 항목 알려줘` → `action: "missing"`. 응답의 `missing_fields`와 `speak_text`로 빠진 R1 필수 칸만 읽어 준다. 되돌리기·다시 읽기·전송도 자연스러운 변형 몇 가지를 인식한다.
- **제출:**
  - `start_inquiry: true`이면 필수 5칸(`avpu`·`breathing`·`chief_complaint`·`age_category`·`pre_ktas`)이 모두 있어야 한다. 하나라도 비면 409 `R1_FIELDS_REQUIRED`와 `missing_fields`를 돌려준다.
  - 환자 반영 시 Pre-KTAS → `priority`(1·2 → 4, 3 → 2, 4·5 → 1, `priority_source: "expert"`)로 바꾼다.
  - `extra`에는 `avpu`·`chief_complaint`·`age_group`(R1 구분)·`pre_ktas_level`·`vitals`·2차 고려사항을 넣는다.
  - 새 환자이고 `context`에 진료 요구가 없으면 R1 4절 규칙으로 `treatment_profile_id`를 고른다. 앱이 `treatment_profile_id`나 `required_capabilities`·`resource_demands`를 보내면 앱 값을 따른다. 기존 환자의 진료 요구는 바꾸지 않는다.
- `GET /api/voice/capabilities`에 `r1_required_fields`, `r1_fields`가 추가됐다.
- **자동 모드(무전기 방식):**
  - `mode: "auto"`로 보내면 서버가 명령과 받아쓰기를 가른다. 응답에 `requested_mode`·`effective_mode`가 있다.
  - 숫자나 값이 있는 문장은 받아쓰기로 처리한다.
  - 자동 모드의 음성 '확인'은 실행하지 않고 `confirmation_requires_tap`을 돌려준다.
  - 자세한 규칙은 [무전기 방식 설계](VOICE_PTT_DESIGN.md) 4절에 있다.
- **판단 범위:** 예전에는 불확실·대략값 표현을 문장 전체에 적용했다.
  - 과거력과 질문은 그 쉼표 구간만 뺀다.
  - 불확실·대략값 표현은 꾸미는 값 주변에만 적용한다. 뒤에 붙는 표현은 값 끝에서 10자 안, '대략·약'은 값 앞 10자 안이다.
  - '이상·이하·미만·넘음'은 수에 붙을 때만 대략값이다. '발음 이상', '넘어짐'은 대략값이 아니다.
  - 가족이 주어인 나열('어머니는 70세 흉통, 기침')은 '환자·본인·이번·현재·지금'이 나올 때까지 계속 가족 정보로 본다.
- 정확도와 남은 과제는 [R1 맞춤 목록](VOICE_R1_ALIGNMENT.md) 0절을 본다. 화면에서 새 칸을 보여 주고 고치는 부분은 앱 담당이 연결한다.
