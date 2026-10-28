# 지도 배경 출처

`regional-boundaries.geojson`은 실제 행정경계를 그리기 위한 정적 배경이다. 도로망, 길찾기 결과, 구급차 실측 경로를 포함하지 않는다.

- 제공: geoBoundaries, `gbOpen / KOR / ADM2`
- 경계 식별자: `KOR-ADM2-91817680`
- 경계 기준 연도: **2020**. 현재 행정구역의 정확한 경계나 명칭을 보장하지 않는다.
- 원자료: geoBoundaries, citypopulation.de
- 원자료 라이선스: **Creative Commons Attribution 3.0**. geoBoundaries 메타데이터의 `boundaryLicense` 값을 따른다.
- 메타데이터: https://www.geoboundaries.org/api/current/gbOpen/KOR/ADM2/
- 원자료 이용조건: https://www.citypopulation.de/en/help/termsofuse/
- 라이선스: https://creativecommons.org/licenses/by/3.0/
- geoBoundaries 인용 안내: https://www.geoboundaries.org/index.html#citation
- 고정 버전: https://github.com/wmgeolab/geoBoundaries/tree/9469f09/releaseData/gbOpen/KOR/ADM2
- 수집한 파일: https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/KOR/ADM2/geoBoundaries-KOR-ADM2_simplified.geojson

원본의 단순화된 Polygon/MultiPolygon 좌표를 유지하고, 경도 126.79–127.31 / 위도 36.74–37.33의 지도 범위와 경계 상자가 겹치는 18개 구역만 추출했다. 실제 구역 모양을 자른 것은 아니므로 화면에서 지정 범위 밖의 모양을 클립한다. 좌표 순서는 GeoJSON 규격의 **경도, 위도**다.

지도 화면에는 `행정경계: geoBoundaries / citypopulation.de, CC BY 3.0, 2020`을 표시하고 출처 또는 라이선스 링크를 제공한다. 직선 이동을 사용하는 시뮬레이션에서는 별도로 **직선 이동 모형이며 실제 도로 경로가 아님**을 표시한다.

OpenStreetMap 도로 추출은 공개 서버의 HTTP 504/500 오류로 확보하지 못했다. 지도 타일 일괄 다운로드는 하지 않았다. 이 파일은 런타임에 외부 서버 호출 없이 로컬에서 읽는다.

## 2026-10-14 일반 지도 배경 추가

실제 좌표 사례에서는 `basemap.js`의 Mercator 계산으로 현재 화면에 필요한 OpenStreetMap 표준 타일을 표시한다. 타일은 브라우저가 `https://tile.openstreetmap.org/{z}/{x}/{y}.png`에서 직접 읽으며 기본 HTTP 캐시를 사용한다. 이 디렉터리에 타일을 저장하거나 전체 지역을 사전 수집하지 않는다. 지도 출처를 화면에 표시한다. [이용 정책](https://operations.osmfoundation.org/policies/tiles/).

일반지도 배경과 저장된 OSRM 이송 경로는 독립적이다. 일반지도 연결에 실패해도 이 로컬 행정경계와 경로·재생을 사용할 수 있다. 가상 좌표 사례에는 실제 타일·행정경계·지역명을 표시하지 않는다. 별도 실제 도로 5개 정책 시연은 [사용 안내](../../../docs/ROAD_ONLINE_REPLAY.md)를 따른다.
