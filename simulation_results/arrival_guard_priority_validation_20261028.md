# G2 회귀·새 자료 검증

단계: validation · 환경 345개 · 합성 입력

G2는 미배정 가중치가 증가하지 않으면서 배정 수가 늘거나, 동수에서 실험 우선순위4 선정 수가 늘면 기존 H1 안을 쓴다. 나머지는 호출 순서의 거리순 안을 쓴다.
회귀는 고정된 이전 통제군 벡터를 재사용하고 G2만 새로 실행한다. 새 검증은 네 규칙을 모두 새로 실행한다.

| 평가 | 조건 | 환자 | 규칙 | 미배정 | 미도착 | 제한 도착 평균(분) | 제한 도착 p95 |
|---|---|---:|---|---:|---:|---:|---:|
| main | routine_single | 100 | R0 | 0 | 0 | 21.090 | 34 |
| main | routine_single | 100 | R1 | 0 | 0 | 20.180 | 32 |
| main | routine_single | 100 | H1 | 0 | 0 | 20.180 | 32 |
| main | routine_single | 100 | G2 | 0 | 0 | 20.180 | 32 |
| main | routine_stream | 516 | R0 | 2 | 2 | 21.531 | 35 |
| main | routine_stream | 516 | R1 | 2 | 2 | 21.070 | 34 |
| main | routine_stream | 516 | H1 | 2 | 2 | 21.062 | 34 |
| main | routine_stream | 516 | G2 | 2 | 2 | 21.062 | 34 |
| main | surge | 1626 | R0 | 4 | 6 | 31.394 | 74 |
| main | surge | 1626 | R1 | 4 | 6 | 31.298 | 74 |
| main | surge | 1626 | H1 | 5 | 7 | 31.300 | 75 |
| main | surge | 1626 | G2 | 5 | 7 | 31.308 | 75 |
| stress | abundant_control | 20 | R0 | 0 | 0 | 12.400 | 13 |
| stress | abundant_control | 20 | R1 | 0 | 0 | 12.400 | 13 |
| stress | abundant_control | 20 | H1 | 0 | 0 | 12.400 | 13 |
| stress | abundant_control | 20 | G2 | 0 | 0 | 12.400 | 13 |
| stress | arrival_assessment_transfer | 5 | R0 | 0 | 0 | 26.600 | 28 |
| stress | arrival_assessment_transfer | 5 | R1 | 0 | 0 | 26.600 | 28 |
| stress | arrival_assessment_transfer | 5 | H1 | 0 | 0 | 26.600 | 28 |
| stress | arrival_assessment_transfer | 5 | G2 | 0 | 0 | 26.600 | 28 |
| stress | delayed_replies | 15 | R0 | 0 | 0 | 24.000 | 24 |
| stress | delayed_replies | 15 | R1 | 0 | 0 | 12.000 | 12 |
| stress | delayed_replies | 15 | H1 | 0 | 0 | 12.000 | 12 |
| stress | delayed_replies | 15 | G2 | 0 | 0 | 12.000 | 12 |
| stress | large_simultaneous | 76 | R0 | 20 | 20 | 33.947 | 90 |
| stress | large_simultaneous | 76 | R1 | 20 | 20 | 33.947 | 90 |
| stress | large_simultaneous | 76 | H1 | 2 | 2 | 17.934 | 23 |
| stress | large_simultaneous | 76 | G2 | 2 | 2 | 17.934 | 23 |
| stress | no_capacity_control | 15 | R0 | 15 | 15 | 60.000 | 60 |
| stress | no_capacity_control | 15 | R1 | 15 | 15 | 60.000 | 60 |
| stress | no_capacity_control | 15 | H1 | 15 | 15 | 60.000 | 60 |
| stress | no_capacity_control | 15 | G2 | 15 | 15 | 60.000 | 60 |
| stress | resource_change_enroute | 10 | R0 | 0 | 0 | 21.800 | 33 |
| stress | resource_change_enroute | 10 | R1 | 0 | 0 | 21.800 | 33 |
| stress | resource_change_enroute | 10 | H1 | 0 | 0 | 21.800 | 33 |
| stress | resource_change_enroute | 10 | G2 | 0 | 0 | 21.800 | 33 |
| stress | scarce_greedy_trap | 10 | R0 | 5 | 5 | 50.200 | 90 |
| stress | scarce_greedy_trap | 10 | R1 | 5 | 5 | 50.200 | 90 |
| stress | scarce_greedy_trap | 10 | H1 | 0 | 0 | 12.900 | 16 |
| stress | scarce_greedy_trap | 10 | G2 | 0 | 0 | 12.900 | 16 |
| stress | scarce_long_detour | 10 | R0 | 5 | 5 | 50.300 | 90 |
| stress | scarce_long_detour | 10 | R1 | 5 | 5 | 50.300 | 90 |
| stress | scarce_long_detour | 10 | H1 | 0 | 0 | 34.100 | 58 |
| stress | scarce_long_detour | 10 | G2 | 0 | 0 | 34.100 | 58 |
| stress | staggered_singleton | 20 | R0 | 0 | 0 | 10.600 | 11 |
| stress | staggered_singleton | 20 | R1 | 0 | 0 | 10.600 | 11 |
| stress | staggered_singleton | 20 | H1 | 0 | 0 | 10.600 | 11 |
| stress | staggered_singleton | 20 | G2 | 0 | 0 | 10.600 | 11 |

## 사전 고정 목표

통과: True

| 항목 | 통과 | 후보 | 기준 |
|---|---|---:|---:|
| main/routine_single/unassigned | True | 0 | 0 |
| main/routine_single/unassigned_priority_weight | True | 0 | 0 |
| main/routine_single/priority4_unassigned | True | 0 | 0 |
| main/routine_stream/unassigned | True | 2 | 2 |
| main/routine_stream/unassigned_priority_weight | True | 4 | 4 |
| main/routine_stream/priority4_unassigned | True | 0 | 0 |
| main/surge/unassigned | True | 5 | 5 |
| main/surge/unassigned_priority_weight | True | 11 | 11 |
| main/surge/priority4_unassigned | True | 1 | 1 |
| stress/abundant_control/unassigned | True | 0 | 0 |
| stress/abundant_control/unassigned_priority_weight | True | 0 | 0 |
| stress/abundant_control/priority4_unassigned | True | 0 | 0 |
| stress/arrival_assessment_transfer/unassigned | True | 0 | 0 |
| stress/arrival_assessment_transfer/unassigned_priority_weight | True | 0 | 0 |
| stress/arrival_assessment_transfer/priority4_unassigned | True | 0 | 0 |
| stress/delayed_replies/unassigned | True | 0 | 0 |
| stress/delayed_replies/unassigned_priority_weight | True | 0 | 0 |
| stress/delayed_replies/priority4_unassigned | True | 0 | 0 |
| stress/large_simultaneous/unassigned | True | 2 | 2 |
| stress/large_simultaneous/unassigned_priority_weight | True | 8 | 8 |
| stress/large_simultaneous/priority4_unassigned | True | 2 | 2 |
| stress/no_capacity_control/unassigned | True | 15 | 15 |
| stress/no_capacity_control/unassigned_priority_weight | True | 60 | 60 |
| stress/no_capacity_control/priority4_unassigned | True | 15 | 15 |
| stress/resource_change_enroute/unassigned | True | 0 | 0 |
| stress/resource_change_enroute/unassigned_priority_weight | True | 0 | 0 |
| stress/resource_change_enroute/priority4_unassigned | True | 0 | 0 |
| stress/scarce_greedy_trap/unassigned | True | 0 | 0 |
| stress/scarce_greedy_trap/unassigned_priority_weight | True | 0 | 0 |
| stress/scarce_greedy_trap/priority4_unassigned | True | 0 | 0 |
| stress/scarce_long_detour/unassigned | True | 0 | 0 |
| stress/scarce_long_detour/unassigned_priority_weight | True | 0 | 0 |
| stress/scarce_long_detour/priority4_unassigned | True | 0 | 0 |
| stress/staggered_singleton/unassigned | True | 0 | 0 |
| stress/staggered_singleton/unassigned_priority_weight | True | 0 | 0 |
| stress/staggered_singleton/priority4_unassigned | True | 0 | 0 |

## 거리순 고정 도착자 추적

고정 R1 도착자 1620명: 늦어진 115명, 최대 제한 지연 130분, G2 미도착 2명.
기존 지연 대상 134명: 여전히 지연 107명. 현재 공통 도착자 최대 지연 59분.

원래 H1과 G2은 실제 네 라벨로 보존했다. 동결 분석의 H1 별칭에 G2을 넣은 투영만 부트스트랩에 사용하며 별칭표를 JSON에 명시한다.
층·스트레스 계열을 합쳐 손실을 상쇄하지 않는다. 선정·도착 성공 ID 손실/획득과 모든 우선순위 그룹을 JSON에 별도로 보존한다.
미도착자는 관측 종료 제한값을 유지한다. 실제 임상 효과·온라인 최적성·전체 자원 이력 재생을 주장하지 않는다.
저장 원시자료 감사와 동일 분석 구현의 통계 재현을 수행한다. 별도의 독립 통계 수학 검증은 아니다.
