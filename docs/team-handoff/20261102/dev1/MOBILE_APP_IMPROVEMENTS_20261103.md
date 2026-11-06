# dev1 · 구급대원 화면 모바일 앱 전환 개선사항

작성일: 2026-11-03. 기준: main `f42fb29`, Chrome 모바일 에뮬레이션 375×812, 음성 전용 서버와 새 임시 DB. **실제 휴대폰·마이크 시험이 아니다.** 아래 수치는 화면 구조를 비교하기 위한 것이다.

구급대원 화면은 결국 휴대폰 앱으로 쓴다. 지금 메인 화면(`/paramedic/`)은 PC 화면을 세로로 늘어놓은 구조라 그대로 앱에 넣기 어렵다. 간편 음성 화면(`/paramedic/quick-test.html`)은 위쪽 앱 바와 아래쪽 큰 녹음 버튼 구조라 앱 화면의 출발점으로 쓰기 좋다.

이 문서의 M 항목은 [dev1 README](README.md)의 D01·D02(P0)와 별도 작업이다. 둘 사이 순서는 팀이 정한다.

## 1. 측정 결과

| 항목 | 메인 `/paramedic/` | 간편 음성 `quick-test.html` |
|---|---|---|
| 페이지 길이 | 화면 4.6장 (4단계를 한 페이지에 세로로 쌓음) | 화면 1장 |
| 녹음 버튼 | 1.4화면 아래, 스크롤해야 보임 (174×44) | 첫 화면 아래쪽 (102×99) |
| 44px보다 작은 터치 대상 | 75개 중 47개 | 15개 중 11개 |
| 입력칸 / 글씨 16px 미만 | 41개 / 3개 (아이폰에서 누르면 화면이 확대됨) | 1개 / 0개 |
| 개발용 항목 노출 | 서버 주소 입력과 `연결 확인`, 인식 엔진 선택 | 없음 |
| 위치 입력 | 위도·경도 직접 입력 | 해당 없음 |
| 설치·오프라인 | manifest·service worker 없음 | 같음 |

간편 음성의 작은 대상 예: 환자 선택 195×41, `선택 환자 열기` 122×40, `새 환자 시작` 108×40, `환자별 보관·정리` 323×32. 메인의 펼치기 줄(`기존 환자 이어서 기록`, `임시 보관 범위·복구 한계`)은 높이가 16~19px다.

## 2. 앱으로 감쌀 때 막히는 기술 문제

| 문제 | 근거 | 영향 |
|---|---|---|
| 웹 음성 인식 | [speech.mjs](../../../../paramedic-ui/speech.mjs)가 `SpeechRecognition`/`webkitSpeechRecognition`을 쓴다 | iOS·Android 앱의 WebView에서는 쓸 수 없는 것으로 알려져 있다. 시제품에서 먼저 확인하고, 네이티브 음성 플러그인이나 서버 인식(`POST V/audio`)으로 바꾼다 |
| 초안 저장 | [app.js](../../../../paramedic-ui/app.js)·[main-recovery.mjs](../../../../paramedic-ui/main-recovery.mjs)가 `sessionStorage`(같은 탭 12시간)를 쓴다 | 휴대폰이 앱을 백그라운드에서 종료하면 초안이 사라진다 |
| 화면 꺼짐 | Wake Lock을 쓰지 않는다 | 녹음·이송 중에 화면이 꺼진다 |
| 길게 누르기 | `touch-action`, `user-select`, `-webkit-touch-callout`, `contextmenu` 처리가 없다 | PTT 중 문자 선택이나 메뉴가 뜰 수 있다 |
| 마이크 권한 | 브라우저는 HTTPS나 localhost에서만 마이크를 허용한다 | 같은 WiFi 시연에서는 휴대폰마다 자체 인증서를 설치해야 한다. 앱은 네이티브 권한으로 해결된다 |
| 설치·오프라인 | manifest·service worker가 없다 | 네트워크가 끊긴 상태에서 다시 열면 화면이 뜨지 않는다 |

## 3. 개선 항목

| ID | 우선 | 내용 | 완료 기준 |
|---|---|---|---|
| M01 | P1 | **화면 구조.** 메인의 4단계를 화면 단위로 나눈다: 환자 선택 → 말하기 → 중요 수치 확인 → 병원 후보. 간편 음성의 앱 바·아래쪽 녹음 버튼 구조로 메인 흐름을 합친다 | 375×812에서 스크롤 없이 녹음을 시작한다. 화면마다 주 동작이 하나다 |
| M02 | P1 | **개발용 설정 분리.** 서버 주소, 연결 확인, 인식 엔진 선택, 로컬 시연 안내를 설정 화면(또는 시연 모드)으로 옮긴다 | 첫 화면에 서버 주소·엔진 선택이 없다. 설정값은 지금처럼 보존된다 |
| M03 | P1 | **터치·글씨 크기.** 터치 대상 44px 이상(가능하면 48px), 입력 글씨 16px 이상, 펼치기 줄 높이 확대 | 아래 측정 스크립트에서 `under_44px`와 `field_font_under_16px`가 0이다 |
| M04 | P1 | **초안 영구 저장.** `sessionStorage` 대신 앱을 다시 열어도 남는 저장소를 쓴다(웹은 IndexedDB, 앱은 Capacitor Preferences나 SQLite) | 강제 종료 후 다시 열어도 같은 초안과 원요청이 복구된다. 12시간 만료, 저장 실패 시 쓰기 차단, 범위·편집 버전 충돌 차단은 그대로다 |
| M05 | P1 | **음성 인식 경로.** `GET /api/voice/capabilities`로 쓸 수 있는 엔진을 확인하고, 앱에서는 네이티브 음성 플러그인이나 서버 인식(`V/audio`)을 쓴다 | 앱 안에서 녹음 → 인식문 → 초안 반영이 된다. 서버 인식 설정(키 등)이 필요하면 서버 담당에게 요청한다 |
| M06 | P2 | **위치 자동 입력.** 위도·경도 직접 입력 대신 GPS를 쓴다. 권한 거부·실패 시 수동 입력으로 돌아가고 값의 출처를 표시한다 | 권한을 허용하면 입력 없이 채워진다. 거부해도 수동 입력으로 등록할 수 있다 |
| M07 | P2 | **녹음 중 동작.** 화면 꺼짐 방지(Wake Lock 또는 앱 플러그인), PTT 버튼의 길게 누르기 처리 | 실제 휴대폰에서 30초 PTT 동안 선택 메뉴가 뜨지 않고 화면이 꺼지지 않는다 |
| M08 | P2 | **설명 글 줄이기.** "같은 탭의 임시 복구" 같은 구현 설명은 도움말로 옮기고, 상태는 짧은 배지로 보인다 | 첫 화면 설명 글이 2줄 이하다 |
| M09 | P3 | **앱 포장 시제품.** Capacitor로 안드로이드 APK를 만든다. 같은 WiFi 서버 접속은 HTTPS(`team_lan` 인증서)를 쓰거나, 허용한 주소만 평문 접속을 연다. iOS는 Mac과 애플 개발자 계정이 필요하다 | APK 설치 → 공통 서버 접속 → 합성 환자 1명 등록까지 된다 |

## 4. 지킬 것

- [dev1 README](README.md) 4절의 오류·식별자 계약(VoiceClient 재시도, 409 처리, 원요청 보존 등)은 화면 구조를 바꿔도 그대로 둔다.
- `server/`는 dev1 동결 영역이다. 서버 인식 설정이나 새 API가 필요하면 계약 요청으로 남긴다.
- 화면 구조를 바꾸면 `test_paramedic_*` 회귀를 함께 고친다. 기존 시험을 지워서 통과시키지 않는다.
- 공통 실행본에 들어간 UI 파일을 바꾸면 새 후보 실행본이 필요하다. 서버·공통 담당과 맞춘다.
- [사용성 측정](../../../USABILITY_EVALUATION_20261030.md) 중에는 화면을 바꾸지 않는다. 측정 전에 쓸 버전을 고정한다.
- 실제 휴대폰 시험 결과와 에뮬레이션 결과를 구분해 기록한다.

## 5. 다시 재는 방법

음성 전용 서버를 새 임시 DB로 띄운다(예: `python -m server.app --db <임시 DB> --port 18042 --no-planner --no-inquiry-worker`). Chrome 개발자 도구에서 기기 크기를 375×812로 맞추고 `/paramedic/`과 `/paramedic/quick-test.html`을 연 뒤 콘솔에 붙인다.

```js
(() => {
  const vh = innerHeight, doc = document.documentElement;
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const taps = [...document.querySelectorAll('button, a[href], input, select, textarea, summary, [role=button]')].filter(visible);
  const small = taps.filter(el => { const r = el.getBoundingClientRect(); return r.height < 44 || r.width < 44; });
  const fields = [...document.querySelectorAll('input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea')].filter(visible);
  const record = [...document.querySelectorAll('button')].filter(visible)
    .find(b => /음성 입력 시작|말하기|녹음 시작/.test(b.textContent));
  return {
    screens_tall: +(doc.scrollHeight / vh).toFixed(1),
    record_button_screen: record ? +((record.getBoundingClientRect().top + scrollY) / vh).toFixed(2) : null,
    tap_targets: taps.length,
    under_44px: small.length,
    fields: fields.length,
    field_font_under_16px: fields.filter(el => parseFloat(getComputedStyle(el).fontSize) < 16).length,
    manifest: !!document.querySelector('link[rel=manifest]'),
  };
})()
```
