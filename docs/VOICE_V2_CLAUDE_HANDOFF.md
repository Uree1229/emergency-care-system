# 음성 입력 V2 — Codex / Claude 작업 경계와 연결 계약

최신 서버 연결: [치료조건 검토→배정 재평가 API](VOICE_TREATMENT_REVIEW_API_20261030.md). 기존 환자 세션의 `treatment_review.review_required`를 표시하고 GET변경안/POST직접선택미리보기/POST확인적용을 연결한다. `selection`은 suggested/keep/custom이며 토큰·초안버전·환자버전을 그대로 보낸다. 감소 항목은 명시 확인이 필요하다. 기존 submit은 호환 유지하므로 새 화면에서 검토 경로로 연결해야 한다. `reevaluation.requested`와 계산 완료/워커 상태를 구분한다. Codex는 프론트와 Claude의 자동모드 함수를 수정하지 않았다.

2026-10-30 UI 기록 5.1·5.5 서버 반영 완료: [처리 범위·검토 표시·323개 회귀·18041 적용 기록](VOICE_UI_HANDOFF_SERVER_FIX_20261030.md). capabilities의 `handoff_parser_revision`은 `voice-ui-handoff-20261030-v1`이며 `voice_revision: voice-v2-20261030`은 유지한다. 숫자 없는 “맥박이 떨어졌어”는 이전 값을 보존하고 재확인하며, 질문 답변의 말끝·단위·혈압 쌍·발생 시각·미측정 변형을 받는다. `answer.review_required` / `interpretation_notes`, `field_metadata[field].review_required` / `interpretation_notes`를 화면의 검토 표시에 사용할 수 있다. 이 표시만으로 질문을 반복하지 않는다. 의미가 불명확한 “고급 정장”을 정상으로 추측하는 선택 기능은 추가하지 않았다. Claude 화면 파일·자동모드 함수는 보존했다.

최신 추가: [DB 사전·명령 확장 계약](VOICE_DICTIONARY_AND_COMMANDS_20261030.md). 화면 담당은 새 명령 `remove`, `history`, `help`, `repeat_question`과 `voice_command_needs_clarification` 안내를 표시하면 된다. `command.fields`는 여러 조회·삭제 대상, `command.read_only`는 숫자가 포함돼도 저장값 조회라는 표시다. `GET /api/voice/command-catalog`로 도움말·예문을 읽을 수 있다. Codex는 `paramedic-ui/`를 수정하지 않았고 Claude의 `_auto_mode()` 함수도 보존했다.

최신 서버 보완: [음성 초안→환자정보→병원 문의 연결 검증](VOICE_PATIENT_LINK_VERIFICATION_20261030.md). API 본문 변경 없음. 기존 수동 R1·활력징후 복원, 한 항목 수정 시 다른 값 보존, 명시적 삭제, 재확인·미측정 상태의 세션 간 보존을 반영했다. 버전은 `patient_projection_revision: "voice-patient-link-20261030-v1"`. 화면은 `fields`만이 아니라 `field_metadata`와 `baseline_summary`도 함께 표시한다.

2026-10-30 연결 검증 추가: [서버 수정·12단계 클라이언트 검증·남은 UI 연결](VOICE_CONNECTION_VERIFICATION_20261030.md). 연결 수정 버전은 capabilities의 `connection_revision: "voice-connection-20261030-v1"`로 확인한다. 질문 답변 취소 시 이전 질문 상태를 복원하고, 정상 증상 답변 뒤 오래된 재질문 표시를 해소한다. API 본문 형식은 유지한다.

2026-10-30. 사용자 요청: 긴 음성 인식 보완, 누락 항목 재질문, 자연스러운 상태 변화 반영. **Codex는 서버, Claude는 별도 구급대원 앱을 담당한다.** 기존 팀원의 `patient-ui/`, `hospital-ui/`, `emergency-app/`는 이번 작업 범위가 아니다.

## 파일 소유권

| 담당 | 수정할 파일 | 책임 |
|---|---|---|
| Codex | `server/voice_terms.py`, `server/voice_input.py`, `server/voice_followup.py`, `server/voice_audio.py`, `server/app.py`, `config/paramedic_voice_terms.json`, `test_voice*`, 음성 API 문서 | 명시적 용어·수치 해석, 상태 변화와 정정 구분, 이전 값 이력, 질문 계획·답변 처리, API·동시성 검증 |
| Claude | `paramedic-ui/` 전체, `test_paramedic*` | 마이크·브라우저 STT, 긴 발화 UI, 문맥 힌트 적용, 질문 표시·TTS, 답변 전송, 변경 전후 표시, 앱 테스트 |

현재 예외: **진행 중인 자동모드 작업은 Claude가 마무리한다.** `server/voice_input.py`의 `_auto_mode()`와 자동 확인 시 화면 조작을 요구하는 분기, `test_voice_r1.py`의 자동모드 테스트는 보존한다. 이후에는 위 파일 경계로 분리한다. Codex의 같은 파일 내 질문·답변·관찰 이력 변경을 파일 전체 덮어쓰기로 지우지 않는다.

상대 파일은 수정하지 않고 필요한 계약 변경을 제안한다. 두 도구가 `HANDOFF.md`를 함께 고치지 않는다. Codex 기록은 이 문서와 `docs/VOICE_INPUT_API.md`, Claude 기록은 별도 `docs/VOICE_UI_CLAUDE_HANDOFF.md`에 둔다. 테스트·화면 캡처는 서로 다른 하위 폴더를 사용한다. Git 전체 add/reset/checkout으로 상대 변경을 포함·삭제하지 않는다.

## Claude에게 전달할 요청

> `docs/VOICE_V2_CLAUDE_HANDOFF.md`를 읽고 현재 자동모드 판별 수정까지만 마무리한 뒤 **paramedic-ui/와 test_paramedic*만** 작업해줘. 서버·용어 설정은 Codex가 맡는다. 긴 음성 입력 뒤 누락 질문을 보여주고 읽어주며, 답변을 아래 API에 연결해줘. 자연스러운 상태 변경은 mode:auto로 보내고, session.latest_changes로 이전·현재 값을 보여줘. 서버가 제안한 질문과 명시적인 인식 결과만 사용하고, 깨진 음성을 임의로 정상 소견으로 채우지 마. 실제 마이크 시험과 가짜 SpeechRecognition 테스트 결과는 구분해줘.

## 실행본 버전 확인

연결 서버의 `GET /api/voice/capabilities`에 `voice_revision: "voice-v2-20261030"`이 있어야 한다. 없으면 실행 프로세스가 이전 코드를 읽고 있는 상태다. 키나 실제 환자정보를 채팅·문서에 쓰지 않는다. 기존 시연 DB는 초기화하지 않는다. 실제 병원 문의는 이 시험 흐름에서 호출하지 않는다.

현재 `http://127.0.0.1:18032`에 V2를 반영했다. 원래 시험 DB는 `.codex-build/voice-v2-20261030/before-server-restart.db`에 백업했고 환자·문의·발화 수와 기존 긴 문장 결과가 그대로임을 확인했다. 기존 실제 발화에 질문 계획을 조회하면 의식·호흡·혈압·맥박·호흡수 5개 질문이 나온다. 원문이나 저장값을 수정해 시험 결과를 개선한 것은 아니다.

서버·기존 음성·평가 도구·로컬 HTTP 회귀 **161개 통과**, 실행 전후 소스 해시 일치. 상세: `.codex-build/voice-v2-20261030/verification.json`, `verification.log`, `live-health.json`. 합성 문자열·가짜 서비스 테스트이며 실제 새 마이크 시험은 아직 하지 않았다. Claude의 자동모드 판별·화면 확인 분기는 보존했다. UI 연결과 실제 STT 재시험은 Claude 작업 뒤 별도로 진행한다.

## 1. 긴 설명·자연스러운 변경·제어 명령

기존 `POST /api/voice/sessions/{sid}/transcripts`를 사용한다.

```json
{
  "actor_id": "crew-demo",
  "client_request_id": "new-request-id",
  "expected_version": 2,
  "utterance_id": "new-utterance-id",
  "text": "혈압이 지금 100에 80까지 떨어졌어",
  "is_final": true,
  "mode": "auto"
}
```

- `auto`: 지원 명령이면 command, 그 외에는 dictation으로 처리한다. 값을 말한 문장은 읽기 명령과 구분한다. 응답의 `requested_mode`·`effective_mode`로 구분한다. 기존 dictation/command도 유지한다. 자동모드에서 음성 확인은 `confirmation_requires_tap`으로 화면 확인을 안내한다.
- 자연스러운 현재 상태 보고는 `command.action: "observe"`이며, 새 관찰 이력으로 남긴다. “수정해”는 `edit`로 구분한다.
- `session.fields`는 현재 값, `session.latest_changes`는 최근 변경의 `field`, `previous`, `current`, `change_kind`, `recorded_at`이다. 두 값이 숫자이면 `direction`도 반환한다. 변화 방향은 수치 비교이며 임상적 악화 판정이 아니다.
- 구체적 값 없이 “혈압이 지금 떨어졌어”라고 하면 이전 관찰값을 지어내거나 삭제하지 않는다. 해당 `field_metadata`에 `needs_clarification:true`를 표시하고 질문 대상으로 다시 올린다. 화면에서도 이전 값과 현재 미확인 상태를 구분한다.
- 부분 인식문은 미리보기이며 초안·명령을 실행하지 않는다. 긴 문장은 여러 final 조각을 합쳐 종료 시 한 번 보낸다.
- `auto`의 “전송”도 제출 API를 별도로 요구한다. 음성 시험 화면에서 `/submit`, 환자 생성·병원 문의를 자동 호출하지 않는다.

기존 `QuickSession.apply()`는 dictation/command만 검증하므로 Claude가 auto 허용과 관련 테스트를 함께 추가해야 한다.

## 2. 다음 질문

모든 최신 `session`에 `followup`이 포함된다. 따로 읽을 때:

`GET /api/voice/sessions/{sid}/followup?actor_id=crew-demo&profile=voice_extended_v1`

```json
{
  "session_id": "VS-...",
  "version": 2,
  "followup": {
    "profile": "voice_extended_v1",
    "purpose": "demonstration_collection_checklist",
    "next_question": {
      "id": "blood_pressure",
      "fields": ["systolic_bp", "diastolic_bp"],
      "question": "혈압의 수축기와 이완기 값을 함께 알려 주세요.",
      "answer_hint": "예: 혈압 120에 80 / 혈압 미측정",
      "reason": "missing",
      "askable": true
    },
    "items": [],
    "complete": false,
    "collection_complete": false
  }
}
```

위 `items`는 형식 설명용이며 실제로는 미해결 질문 전체가 들어 있다. `next_question`만 차례로 제시하고 `question`을 한국어 TTS로 읽는다. 마이크를 닫고 읽어주어 앱의 목소리가 다시 입력되지 않게 한다. TTS 종료 뒤 사용자 버튼으로 다음 답변을 받는 흐름이 기본이다. 마이크·서버 요청 중 초안·질문·모드를 전환하지 않는다.

- `voice_core_v1`: 나이/연령대, 성별, 증상, 의식, 호흡, 혈압, 맥박, 산소포화도.
- `voice_extended_v1`(기본): core에 체온, 호흡수, 혈당, 발생 시각을 더한다.
- 혈압은 수축기·이완기 한 쌍을 한 질문으로 묻는다. 증상을 말하지 않았다고 “없음”으로 채우지 않는다.
- 이 목록은 시연 입력 체크리스트다. 의료진이 정한 임상 필수 항목이나 환자 등록 가능 판정으로 표시하지 않는다. 기존 R1 필수 5칸 검사와도 별개다.
- `complete`: 미해결 관찰이 없음. `collection_complete`: 미측정·확인 불가 답변까지 받아 더 묻지 않음. 둘은 다르다.

## 3. 질문에 답변

`POST /api/voice/sessions/{sid}/followup/answers`

```json
{
  "actor_id": "crew-demo",
  "client_request_id": "new-answer-request-id",
  "expected_version": 3,
  "utterance_id": "new-answer-utterance-id",
  "question_id": "blood_pressure",
  "profile": "voice_extended_v1",
  "text": "100에 80"
}
```

기존 `VoiceClient.write('followup/answers', body)`를 사용할 수 있다. 질문 ID와 **질문이 제시된 초안 버전**을 함께 보낸다. 서버가 현재 질문과 허용 답변 범위를 검증한다.

- 질문에 대한 짧은 답(“100에 80”, “92회”, “명료”)에는 제공된 질문 문맥을 붙여 해석한다. 원문과 `contextual_answer` 출처를 별도로 남긴다.
- 질문 대상 외의 항목은 이 경로에서 수정하지 않는다. 혈압 질문에 “맥박 92”라고 답해도 이를 혈압이나 맥박에 임의 반영하지 않는다.
- “미측정”, “모르겠어”는 0·정상·없음으로 바꾸지 않는다. 미해결 상태로 보존하며 같은 질문을 무한히 반복하지 않는다. 버튼 답변은 `unavailable:true`도 가능하다.
- 성공 응답은 `answer.accepted`, `answer.applied_fields`, `answer.unavailable`, `answer.ignored_fields`, 최신 `session`·`followup`·`speak_text`를 포함한다. 값 해석 실패는 `accepted:false`이며 질문을 유지한다. 질문 외 항목이 `ignored_fields`에 있으면 반영되지 않았다고 화면에 알려 일반 입력으로 다시 받는다. 여러 수치를 나열한 모호한 답변은 첫 값만 골라 반영하지 않는다.
- 409는 최신 초안·질문을 읽어 사용자에게 다시 확인한다. 응답 유실은 같은 client_request_id·utterance_id·본문·expected_version으로 재시도한다.
- 질문 중 다른 새 상태 보고나 제어 명령을 하려면 일반 transcripts(auto) 흐름을 사용한다. 한 질문의 답변을 임의로 전역 명령으로 실행하지 않는다.

## 음성 인식 자체의 보완과 한계

`GET /api/voice/terms`의 `speech_recognition_hints`와 capabilities의 `speech_context_hints`를 브라우저가 지원하는 문맥 힌트에 사용할 수 있다. 지원 여부를 확인하고 없으면 일반 인식을 유지한다. 힌트 지원과 실제 개선 효과를 동일시하지 않는다. 브라우저/STT SDK의 최신 공식 문서를 확인하고 적용한다.

서버는 명시적 약어·띄어쓰기·한글 수와 소수를 더 잘 해석한다. 그러나 `의식 명료`가 `2시간 후면`으로 바뀌거나 맥박이라는 단어가 사라진 결과에서 원래 값을 복원했다고 주장하지 않는다. 문장을 짧은 호흡 단위로 표시하거나, 인식 대안을 보존하거나, 더 적합한 STT 엔진을 비교하는 것은 Claude의 입력 단계 작업이다. 외부 엔진의 인증키가 없으면 있다고 가정하지 않는다.

이번 실제 시험 원문과 이전 잠금 보고서는 보존한다. 수정 뒤 같은 원문으로 통과했다고 실제 음성 인식 정확도가 개선됐다고 말하지 않는다. 새 녹음으로 재시험해야 한다.
