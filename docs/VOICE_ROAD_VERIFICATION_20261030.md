# 최신 음성 흐름의 카카오 도로 통합 검증

2026-10-30. 신규 `server/voice_road_rehearsal.py`는 최신 음성 HTTP API·고정3 문의·3초 CP-SAT·수용·담당자 확정·조건 변경을 실제 카카오 경로 입력과 결합한다. 기존 서버·DB와 동결 21개 소스는 유지한다.

## 확인한 흐름

| 사례 | 결과 | 관찰 종료 상태 |
|---|---|---|
| 정상 수용과 도착 | PASS | ARRIVED, H1 |
| 이송 중 자원 변경과 병원의 수용 유지 | PASS | IN_TRANSIT, H1 |
| 자원 변경과 대체 수용과 담당자의 목적지 변경 | PASS | ARRIVED, H2 |
| 병원 무응답과 문의 마감 | PASS | WAITING, 목적지 없음 |

원본 결과는 `.codex-build/final-readiness-20261030/voice-road-run-002/report.json`이다. 사례별 전용 DB·API 요청·조건 확인·솔버 상태·상태 스냅샷·도로 이동 구간을 보존했다. 소스 무결성과 기존 서버 기준 검사를 함께 통과했다.

## 경로와 시간

합성 출발점은 광주 좌표 35.16, 126.85이며 H1~H3은 합성 병원이다. 출발점→3병원과 첫 경로 중간 관찰점→3병원의 6개 카카오 경로를 먼저 수집한다. 중간 관찰점은 결과를 보기 전에 첫 경로 누적거리의 절반으로 정하고 서버 좌표 정밀도에 맞춰 반올림한다.

실제 API·워커 실행 중에는 이 스냅샷의 좌표 쌍만 허용한다. 누락 쌍은 실패로 처리하며 합성 이동시간으로 대체하지 않는다. 대체 문의를 보내기 전에 현재 위치 관찰을 서버에 반영한다. 도로상 보간 위치는 실제 GPS가 아니며, 합성 병원에 실제 기관의 이름이나 운영 정보를 붙이지 않는다.

일반 자동차의 도로 형상·조회 시각·예상시간과 접속 구간을 보존한다. 도로 밖 접속은 시속 10km 합성 추정으로 분리해 더하고 분 단위로 올림한다. 도착 POST는 출발 또는 명시적 목적지 변경부터 해당 ETA 이후에 호출한다. 환자·병원 담당자의 회신·조작 시각은 통제시계라 실제 업무시간으로 비교하지 않는다.

API 사이의 시계 진행 때문에 기존 합성 검증기의 고정 `NOW+301`은 실제 문의 마감보다 빨랐다. 최초 `voice-road-run-001`의 3 PASS/1 FAIL과 당시 소스를 보존하고, 새 검증기에서 저장된 `deadline_at` 이후로 진행하도록 수정했다. 운영 알고리즘이나 기존 검증기를 바꾼 수정이 아니다. 두 번째 실행은 첫 실행의 유효한 캐시를 명시적으로 재사용했고 **외부 API 재호출 0회**, 6개 경로 모두 서버 입력으로 사용했다.

## 다시 실행

키는 프로세스 환경의 `KAKAO_REST_KEY`로만 전달한다. 로그·프로토콜·보고서에는 값·인증 헤더·원문 응답을 저장하지 않는다.

```powershell
# 새 길찾기 스냅샷과 격리 DB
python -m server.voice_road_rehearsal --output .codex-build/voice-road-new

# 유효기간 안의 명시적 캐시만 사용. 외부 조회 금지
python -m server.voice_road_rehearsal --output .codex-build/voice-road-offline-new --route-cache .codex-build/voice-road-new/route-cache --offline

python -m simulation.voice_replay_adapter_20261030 --input .codex-build/voice-road-new --output outputs/voice-road-new.json
python -m unittest test_voice_road_rehearsal -v
```

기존 출력 폴더는 거절한다. 캐시의 유효기간 검사를 우회하지 않는다. 이미 내보낸 `simulation/viewer/voice_road_replay_data_20261030.json`은 수집 시점의 역사적 재생이므로 길찾기 재호출 없이 볼 수 있다.

지도 표시와 검증 범위는 [최신 지도 재생](VOICE_MAP_REPLAY_20261030.md), 실제 발표 순서는 [시연 진행안](FINAL_PRESENTATION_AND_DEMO_20261030.md)에 있다. 이 결과는 도로 기능 연결의 증거이며 실제 이송시간 감소·음성 인식 정확도·병원 업무시간 감소의 성능 수치가 아니다.
