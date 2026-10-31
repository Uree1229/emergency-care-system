# 최신 음성 입력·배정·조건 변경 지도 기록

2026-10-30. 기존 10월 13일 서버 조건 변경 시연과 별도로 최신 음성 API와 현재 서버 배정 워커의 처리 기록을 지도에 연결했다. 음성 원음을 새로 인식한 검사가 아니라 합성 인식문을 실제 로컬 HTTP API로 보낸 기능 검사다.

## 실행과 선택

```powershell
# 새 격리 DB에서 최신 코드의 9가지 처리 흐름 실행
python -m server.voice_allocation_rehearsal --output .codex-build/voice-map-new
# 원본 DB를 읽기 전용으로 변환. 기존 파일에는 덮어쓰지 않는다.
python -m simulation.voice_replay_adapter_20261030 --input .codex-build/voice-map-new --output outputs/voice-map-new.json
# 이미 생성한 지도 자료 재생
python -m simulation.viewer_server --reuse-data --no-browser --port 18005
```

현재 뷰어는 `simulation/viewer/voice_replay_data_20261030.json`을 선택적으로 읽는다. 시나리오 선택창의 **최신 음성 → 배정 → 조건 변경 기록** 그룹 또는 `http://127.0.0.1:18005/?scenario=voice-waiting_treatment_change`에서 연다.

현재 저장본의 원본은 `.codex-build/final-readiness-20261030/voice-map-run-002/`다. 실제 서버 HTTP·문의 워커·3초 CP-SAT 워커를 새 격리 DB에서 실행해 9개 시나리오와 실행 전후 소스 무결성 검사를 통과했다. 기본 시스템 Python 3.14는 프로젝트 OR-Tools 3.12 바이너리와 맞지 않으므로, 이 환경에서는 bundled Python 3.12를 사용했다.

| 시나리오 ID 뒤 이름 | 기록 범위 |
|---|---|
| `normal_arrival` | 음성 초안·화면 확인·제출·병원 수용·배정 추천·사람의 확정·출발·도착 |
| `waiting_treatment_change` | 대기 중 새 음성 치료조건 승인·기존 수용 종료·새 수용 확보·새 배정 |
| `enroute_resource_keep` | 이동 중 자원 부족 식별·기존 목적지 유지·병원의 수용 유지 판단 |
| `enroute_resource_reroute` | 변경 필요 판단·대체 문의·새 수용·사람의 목적지 변경·도착 |
| `optional_unavailable` | 선택 항목 미측정이 정보 안내로 남고 배정을 막지 않음 |
| `no_hospital_response` | 무응답·기한 종료·목적지 미확정 |
| `no_available_capacity` | 가용 자원 없음·문의 및 배정 미생성 |
| `stale_treatment_approval` | 오래된 승인 토큰 거부·환자 및 기존 수용 보존 |
| `missing_r1_rejected` | 필수 입력 누락 시 환자·문의 생성 차단, 지도에 가상 환자를 추가하지 않음 |

## 시간·위치·상태의 의미

가로축의 **#번호는 기록 탐색 순서**다. 대본이 같은 초 안에 여러 API 호출을 실행하므로, 초 단위 타임라인만 사용하면 수용 응답과 명시적 확정 사이를 검토할 수 없다. 원본 epoch, 원본 경과초, DB 사건 ID, 원시 payload는 각각 보존한다. 사건 상세에 원본 경과초가 나온다. #번호나 대본상 경과초를 실제 사용자 업무시간·실제 차량 이동시간으로 해석하지 않는다.

DB 사건은 ID 순서를 유지한다. 음성 발화·솔버 기록은 서로 다른 저장 스트림이다. 같은 초의 서로 다른 스트림은 화면 표시를 위해 결정적으로 정렬했으며 정확한 교차 선후를 입증한 전체 감사 로그가 아니다. 따라서 같은 초에 저장된 추천 기록의 화면 위치를 근거로 환자 생성 전에 솔버가 실제 실행됐다고 해석할 수 없다. 전체 처리의 실제 순서는 원본 보고서의 요청 배열과 시나리오 실행기에서 확인한다.

현재 상태는 원본 DB 사건으로 확인할 수 있는 전이만 재구성한다. 수용 응답만으로 목적지를 넣지 않고, `destination_confirmed`·`destination_changed` 성공 사건에서만 바꾼다. 최종 DB 값은 관찰 종료에서만 표시한다. 원본 서버 상태 스냅샷이 함께 있으면 기록된 시점 이후에만 이를 사용한다. 접수 당시 좌표와 요구는 최초 음성 제출 영수증에서 읽고 이송 중 갱신된 최종 좌표로 과거 발생지를 바꾸지 않는다.

기본 9종에는 이동 관측이 없으므로 **차량 이동 궤적을 생성하지 않는다**. ETA가 있어도 그 시각에 도착이나 치료를 만들어 넣지 않는다. 병원 도착은 저장된 `arrival_confirmed` 사건이 있을 때만 표시한다.

## 실제 도로 결합 계약

같은 변환기는 신규 `server/voice_road_rehearsal.py`의 결과도 읽는다. 각 사례 폴더의 `replay_observations.json`에 원본 시각의 서버 상태 스냅샷과 수집된 도로 경로가 있으면 이를 사용한다.

- `snapshots`: `{at: epoch, patients: [원본 patient_view]}`.
- `trajectories`: `{patient: id, legs: [{source_depart_at, source_arrive_at, origin, destination, geometry, geometry_kind, provider, route}]}`.
- `routing_summary`: 제공자·수집시각·합성 대체 여부 등의 원본 출처.
- `assumptions`: 실제 경로·일반차량 ETA와 합성 병원·환자·역할 입력의 구분.

원본 도로 형상과 `route`를 보존한다. 도로에 접속하는 좌표 연결 구간은 별도로 보존하며 기존 도로 재생 형식으로 점선 표시한다. #번호 사이의 차량 위치는 설명용 재생이며 실시간 GPS·구급차 우선주행이 아니다. 카카오 저장본의 재생은 신규 길찾기 호출을 하지 않는다. 병원 이름·역량·수용 판단이 합성이면 실제 도로 위에 표시하더라도 실제 병원 정보로 소개하지 않는다.

실제 도로 결합 자료는 `voice_road_replay_data_20261030.json`과 **최신 음성 흐름 · 카카오 도로 결합** 그룹으로 구분한다. 원본 `.codex-build/final-readiness-20261030/voice-road-run-002/`에서 정상 도착·수용 유지·목적지 변경·무응답 4종과 소스 무결성 검사를 통과했다. 앞 실행에서 수집한 카카오 6개 경로를 새 유효 캐시로 재사용해 두 번째 실행의 외부 재호출은 0회다. `http://127.0.0.1:18005/?scenario=voice-road-enroute_resource_reroute`에서 사람의 목적지 변경과 도로 경로를 함께 확인한다. 기존 10월 14일 R0/R1/R2/R3/C1 도로 시연은 별도 자료로 유지한다.

## 검증·보존

새 변환기 시험 8개, 기존 조건 변경 변환기 7개, 뷰어 MIME 시험 2개가 통과했다. 원본 DB 파일 해시와 전체 사건 보존, 같은 초의 수용·확정 분리, 대체 수용 이후 명시적 변경 전까지 기존 목적지 유지, 과거 위치 보존, ETA로 이동·도착 미생성, 실패·환자 미생성 사례 포함, 원본 경로 및 시각 보존, 원본·기존 출력 덮어쓰기 거부를 검사했다. 뷰어 JavaScript 구문 검사도 통과했다.

초기 `voice-map-run-001`은 Windows sandbox가 로컬 HTTP 연결을 차단해 9종 모두 실패했다. 실패 보고서를 그대로 보존했다. 동일한 코드로 승인된 로컬 루프백 검사 `voice-map-run-002`를 새 디렉터리에서 실행해 9종 모두 통과했다. 실패를 삭제하거나 통과 시나리오만 골라 변환하지 않는다.

이 자료는 **기능 연결 검증**이다. 문의 정책이나 배정 해법의 공정한 성능 비교, 독립 최종 평가, 실제 음성 정확도, 실제 사람의 사용성 평가를 대체하지 않는다.
