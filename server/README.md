# 공통 서버 (시연용)

병원 웹과 환자 웹이 함께 쓰는 JSON API 서버입니다. Python 표준 라이브러리(sqlite3, http.server)만 사용합니다.
임상 현장과 연결된 시스템이 아니고, 기본 데이터는 모두 가상(`source='synthetic'`)입니다.

## 실행
```
python -m server.app --db data/demo.db --seed --scenario sudden --port 8000
python -m unittest test_server -v
```
`--seed`를 주면 가상 병원 10곳을 새로 넣습니다. 이때 기존 DB는 지워집니다.
`KAKAO_REST_KEY` 환경변수가 있으면 이동시간을 카카오 길찾기로 구하고, 없으면 직선거리 보정 추정을 씁니다.

## 구현 범위 (롱리스트 ID)
| 영역 | 구현 | 비고 |
|---|---|---|
| 환자 입력 | P02, P05, P07, P09, P10, P12, P13 | 위험 신호가 있으면 `call_119`. 미확인 중증도는 높음으로 처리(A02) |
| 탐색·문의 | P16~P29 (P23 포함) | 후보는 규칙 기반 필터. 솔버 배정이 아님 |
| 이동·도착 | P31~P41 | 조정 요청과 새 병원 수용 확인이 있어야 목적지 변경 가능 |
| 병원 정보 | H03~H05, H07, H11 | 제한 종료 예정이 지나도 자동 복구하지 않고 재확인 필요로 표시 |
| 문의 응답 | H15~H28 (H20 포함) | 중복 클릭은 그대로 성공, 다른 담당자와 충돌하면 CONFLICT |
| 도착 예정·돌발 | H29~H39 | B 유입 시 영향 환자 표시는 수량 기준 1차 판단 |
| 서버 | S01~S08, S15 | `BEGIN IMMEDIATE` 트랜잭션 안에서 검사와 반영 |
| 데이터 | A01, A05, S16(가상), T01 | 실제 공공 API 적재는 `probe_apis.py` 검증 후 |

## 상태
- 문의: `PENDING → CHECKING → ACCEPTED / DECLINED`, `ACCEPTED → CONFIRMED / EXPIRED / CANCELLED`
- 환자: `WAITING → CONFIRMED → IN_TRANSIT → ARRIVED → COMPLETED`, 중간에 `CANCELLED`
- 수용 응답은 자원을 점유하지 않습니다. 목적지를 확정한 한 곳만 도착 예정 수요가 됩니다.
- 도착 확인 시 예정 수요는 실제 점유로 바뀌므로 이중으로 계산되지 않습니다.

## 아직 안 한 것
- 화면(병원 웹·환자 웹): 다른 팀원 담당. API 명세는 `docs/API.md`
- 솔버 연결과 A26(이동 중 치료 시작시각 재평가): `affected_patients`가 수량만 비교함
- 공공 API 실응답 검증: 인증키가 필요함. `api_field_mapping.md` 참고
- 접근 권한(O01): 로그인이 없어서 병원 ID 경로로만 구분함
