# 공통 서버 (시연용)

병원 웹과 환자 웹이 함께 쓰는 JSON API 서버입니다. API·DB는 Python 표준 라이브러리를 사용하고, 배정 워커는 OR-Tools를 사용합니다.
임상 현장과 연결된 시스템이 아니고, 기본 데이터는 모두 가상(`source='synthetic'`)입니다.

## 실행
```
python -m server.app --db data/demo.db --seed --scenario sudden --port 8000
python -m unittest test_server test_solver_improved test_simulation test_planning -v
```
`--seed`를 주면 가상 병원 10곳을 새로 넣습니다. 이때 기존 DB는 지워집니다.
`KAKAO_REST_KEY` 환경변수가 있으면 이동시간을 카카오 길찾기로 구하고, 없으면 직선거리 보정 추정을 씁니다.
배정 워커는 기본으로 함께 시작하며 솔버를 별도 프로세스로 실행합니다. `--solver-seconds 0`은 순차 배정만 사용하고, `--no-planner`는 워커를 끕니다. 기존 DB는 새 필드를 추가하는 마이그레이션으로 보존합니다.

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
| 배정 연결 | A26, S12의 1차 구현 | 수용 응답 기반 모델·목적지 고정·시작시각 재평가·스냅샷 검사·추천 API |

## 상태
- 문의: `PENDING → CHECKING → ACCEPTED / DECLINED`, `ACCEPTED → CONFIRMED / EXPIRED / CANCELLED`
- 환자: `WAITING → CONFIRMED → IN_TRANSIT → ARRIVED → COMPLETED`, 중간에 `CANCELLED`
- 수용 응답은 자원을 점유하지 않습니다. 목적지를 확정한 한 곳만 도착 예정 수요가 됩니다.
- 도착 확인 시 예정 수요는 실제 점유로 바뀌므로 이중으로 계산되지 않습니다.

## 아직 안 한 것
- 환자 웹을 독립 DB 대신 공통 API에 연결하고, 두 화면에 배정 결과를 표시하는 작업: 다른 팀원 담당. API 명세는 `docs/API.md`
- 실제 점유 종료시각·질환별 자원 일정 연계: 현재 점유 종료시각은 알 수 없어 계획 기간 전체를 차단함. 치료시간·최대 대기는 실험 설정값
- 기존 `affected_patients`는 수량 기준이며, 추가된 `incoming.planning`은 솔버 평가 결과. 기존 목적지가 불가능한 모델은 전체 계획을 `NEEDS_REVIEW`로 반환
- 공공 API 실응답 검증: 인증키가 필요함. `api_field_mapping.md` 참고
- 접근 권한(O01): 로그인이 없어서 병원 ID 경로로만 구분함
