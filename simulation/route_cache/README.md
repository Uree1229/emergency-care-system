# 도로 경로 snapshot

2026-10-11 지도 시연용으로 수집한 OSRM 자동차 경로 응답이다. 출발지는 가상이고 병원 좌표는 공개 DB에서 가져왔다. 실제 구급차 이송 기록이 아니다.

- 제공: https://router.project-osrm.org (OSRM 공개 데모), 도로 데이터 © OpenStreetMap contributors.
- 운영정책: https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server
- 각 JSON의 `request`는 방향과 좌표, `route`는 도로 형상·거리·예상시간·수집시각·출처를 담는다.
- 모든 알고리즘은 같은 응답을 사용한다. 기존 파일은 자동 갱신하지 않는다.
- `python -m simulation.map_replay --offline`으로 캐시만 사용해 재생 데이터를 생성한다.
- 도로 좌표는 `[위도, 경도]`이며 실제 GeoJSON의 순서와 다르다. 도로 접속점 차이는 `connectors`에 분리한다.
- 실시간 교통·구급차 우선주행·병원의 현재 수용 능력은 포함하지 않는다.
- `.request_lock`과 `.last_request.json`은 로컬 수집 제어 파일이고 재현 입력이 아니다.
