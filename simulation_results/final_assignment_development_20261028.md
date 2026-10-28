# 회신순·거리순·H1 최종 비교

development · 108개 환경 · 세 규칙을 각각 새로 실행 · 합성 입력

동시 문의3곳·첫 수용 뒤 최대2분 대기·변경 알림을 고정했다. H1은 최대2048회 탐색하며 10초 안전 상한 소진은 기술 실패다.

| 평가 | 조건 | 환경 | 환자 | 규칙 | 미배정 | 미도착 | 도착 제한 평균(분) | 도착 제한 p95 | 실제 도착 p95(분모) | 문의 |
|---|---|---:|---:|---|---:|---:|---:|---:|---|---:|
| main | routine_single | 30 | 30 | R0 | 0 | 0 | 20.900 | 35.000 | 35 (30) | 96 |
| main | routine_single | 30 | 30 | R1 | 0 | 0 | 20.000 | 35.000 | 35 (30) | 96 |
| main | routine_single | 30 | 30 | H1 | 0 | 0 | 20.000 | 35.000 | 35 (30) | 96 |
| main | routine_stream | 30 | 156 | R0 | 1 | 1 | 22.795 | 37.000 | 35 (155) | 521 |
| main | routine_stream | 30 | 156 | R1 | 1 | 1 | 21.801 | 35.000 | 35 (155) | 520 |
| main | routine_stream | 30 | 156 | H1 | 1 | 1 | 21.795 | 35.000 | 35 (155) | 520 |
| main | surge | 30 | 464 | R0 | 3 | 3 | 32.466 | 82.000 | 78 (461) | 2948 |
| main | surge | 30 | 464 | R1 | 3 | 3 | 32.381 | 82.000 | 77 (461) | 2977 |
| main | surge | 30 | 464 | H1 | 3 | 3 | 32.403 | 82.000 | 78 (461) | 2967 |
| stress | abundant_control | 2 | 8 | R0 | 0 | 0 | 12.500 | 13.000 | 13 (8) | 24 |
| stress | abundant_control | 2 | 8 | R1 | 0 | 0 | 12.500 | 13.000 | 13 (8) | 24 |
| stress | abundant_control | 2 | 8 | H1 | 0 | 0 | 12.500 | 13.000 | 13 (8) | 24 |
| stress | arrival_assessment_transfer | 2 | 2 | R0 | 0 | 0 | 27.000 | 28.000 | 28 (2) | 8 |
| stress | arrival_assessment_transfer | 2 | 2 | R1 | 0 | 0 | 27.000 | 28.000 | 28 (2) | 8 |
| stress | arrival_assessment_transfer | 2 | 2 | H1 | 0 | 0 | 27.000 | 28.000 | 28 (2) | 8 |
| stress | delayed_replies | 2 | 6 | R0 | 0 | 0 | 24.000 | 24.000 | 24 (6) | 18 |
| stress | delayed_replies | 2 | 6 | R1 | 0 | 0 | 12.000 | 12.000 | 12 (6) | 18 |
| stress | delayed_replies | 2 | 6 | H1 | 0 | 0 | 12.000 | 12.000 | 12 (6) | 18 |
| stress | large_simultaneous | 2 | 28 | R0 | 8 | 8 | 35.357 | 90.000 | 16 (20) | 194 |
| stress | large_simultaneous | 2 | 28 | R1 | 8 | 8 | 35.357 | 90.000 | 16 (20) | 194 |
| stress | large_simultaneous | 2 | 28 | H1 | 0 | 0 | 16.214 | 23.000 | 23 (28) | 68 |
| stress | no_capacity_control | 2 | 6 | R0 | 6 | 6 | 60.000 | 60.000 | — (0) | 0 |
| stress | no_capacity_control | 2 | 6 | R1 | 6 | 6 | 60.000 | 60.000 | — (0) | 0 |
| stress | no_capacity_control | 2 | 6 | H1 | 6 | 6 | 60.000 | 60.000 | — (0) | 0 |
| stress | resource_change_enroute | 2 | 4 | R0 | 0 | 0 | 23.000 | 33.000 | 33 (4) | 16 |
| stress | resource_change_enroute | 2 | 4 | R1 | 0 | 0 | 23.000 | 33.000 | 33 (4) | 16 |
| stress | resource_change_enroute | 2 | 4 | H1 | 0 | 0 | 23.000 | 33.000 | 33 (4) | 16 |
| stress | scarce_greedy_trap | 2 | 4 | R0 | 2 | 2 | 50.000 | 90.000 | 10 (2) | 36 |
| stress | scarce_greedy_trap | 2 | 4 | R1 | 2 | 2 | 50.000 | 90.000 | 10 (2) | 36 |
| stress | scarce_greedy_trap | 2 | 4 | H1 | 0 | 0 | 12.500 | 15.000 | 15 (4) | 6 |
| stress | scarce_long_detour | 2 | 4 | R0 | 2 | 2 | 50.250 | 90.000 | 11 (2) | 34 |
| stress | scarce_long_detour | 2 | 4 | R1 | 2 | 2 | 50.250 | 90.000 | 11 (2) | 34 |
| stress | scarce_long_detour | 2 | 4 | H1 | 0 | 0 | 34.000 | 58.000 | 58 (4) | 6 |
| stress | staggered_singleton | 2 | 8 | R0 | 0 | 0 | 10.500 | 11.000 | 11 (8) | 24 |
| stress | staggered_singleton | 2 | 8 | R1 | 0 | 0 | 10.500 | 11.000 | 11 (8) | 24 |
| stress | staggered_singleton | 2 | 8 | H1 | 0 | 0 | 10.500 | 11.000 | 11 (8) | 24 |

## H1 비교 판정

| 평가 | 조건 | 기준 | 주 지표 차이 | 보정 구간 | 판정 | 공통 도착 지연 평균 | 최대 지연 |
|---|---|---|---|---|---|---|---|
| main | routine_single | R0 | +0.000000 | [+0.000000, +0.000000] | inconclusive | -0.9 | 0 |
| main | routine_single | R1 | +0.000000 | [+0.000000, +0.000000] | inconclusive | 0.0 | 0 |
| main | routine_stream | R0 | +0.000000 | [+0.000000, +0.000000] | inconclusive | -1.0064516129032257 | 6 |
| main | routine_stream | R1 | +0.000000 | [+0.000000, +0.000000] | inconclusive | -0.0064516129032258064 | 0 |
| main | surge | R0 | +0.001389 | [+0.000000, +0.005556] | inconclusive | 0.07608695652173914 | 69 |
| main | surge | R1 | +0.001389 | [+0.000000, +0.005556] | inconclusive | 0.1608695652173913 | 69 |
| stress | abundant_control | R0 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | abundant_control | R1 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | arrival_assessment_transfer | R0 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | arrival_assessment_transfer | R1 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | delayed_replies | R0 | — | 서술 통계 | descriptive_only_stress | -12.0 | 0 |
| stress | delayed_replies | R1 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | large_simultaneous | R0 | — | 서술 통계 | descriptive_only_stress | 4.8 | 7 |
| stress | large_simultaneous | R1 | — | 서술 통계 | descriptive_only_stress | 4.8 | 7 |
| stress | no_capacity_control | R0 | — | 서술 통계 | descriptive_only_stress | None | None |
| stress | no_capacity_control | R1 | — | 서술 통계 | descriptive_only_stress | None | None |
| stress | resource_change_enroute | R0 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | resource_change_enroute | R1 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | scarce_greedy_trap | R0 | — | 서술 통계 | descriptive_only_stress | 5.0 | 5 |
| stress | scarce_greedy_trap | R1 | — | 서술 통계 | descriptive_only_stress | 5.0 | 5 |
| stress | scarce_long_detour | R0 | — | 서술 통계 | descriptive_only_stress | 47.0 | 47 |
| stress | scarce_long_detour | R1 | — | 서술 통계 | descriptive_only_stress | 47.0 | 47 |
| stress | staggered_singleton | R0 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | staggered_singleton | R1 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |

주 평가는 층별 환경 동등 가중이며 스트레스와 합산하지 않는다. 위 환자 평균은 해당 층의 기술 통계다. 실패는 관측 종료 경과시간으로 제한 평균에 포함한다.
기술 감사 통과와 효과 판정은 구분한다. 공통 도착자 비교는 미도착자를 제외한 조건부 결과이므로 성공·실패 변화와 함께 읽는다.
전체 자원 이력 재생이나 온라인 최적성을 증명하지 않았다. H1 이전50ms설정과 달리2048회가 주 탐색 예산이다. 실제 병원·임상 효과가 아니다.
통계 재현 검사는 동일 분석 구현의 재실행이며 별도의 독립 통계 수학 검증으로 주장하지 않는다.

## 배정 호출 계산시간

| 평가 | 조건 | 규칙 | 결정 수 | p95(ms) | 최대(ms) |
|---|---|---|---:|---:|---:|
| main | routine_single | R0 | 30 | 0.011399999493733048 | 0.01170000177808106 |
| main | routine_single | R1 | 30 | 0.011699994502123445 | 0.011800002539530396 |
| main | routine_single | H1 | 30 | 0.13020000187680125 | 0.13079999916953966 |
| main | routine_stream | R0 | 155 | 0.015500001609325409 | 0.11630000517470762 |
| main | routine_stream | R1 | 155 | 0.012099997547920793 | 0.022400003217626363 |
| main | routine_stream | H1 | 151 | 0.14319999900180846 | 0.3116000007139519 |
| main | surge | R0 | 461 | 0.010299998393747956 | 0.10479999764356762 |
| main | surge | R1 | 461 | 0.010399999155197293 | 0.026500005333218724 |
| main | surge | H1 | 266 | 0.24929999926825985 | 0.4563999973470345 |
| stress | abundant_control | R0 | 8 | 0.03619999915827066 | 0.03619999915827066 |
| stress | abundant_control | R1 | 8 | 0.017299993487540632 | 0.017299993487540632 |
| stress | abundant_control | H1 | 2 | 0.3032999957213178 | 0.3032999957213178 |
| stress | arrival_assessment_transfer | R0 | 4 | 0.01080000220099464 | 0.01080000220099464 |
| stress | arrival_assessment_transfer | R1 | 4 | 0.011100004485342652 | 0.011100004485342652 |
| stress | arrival_assessment_transfer | H1 | 4 | 0.35339999885763973 | 0.35339999885763973 |
| stress | delayed_replies | R0 | 6 | 0.04429999535204843 | 0.04429999535204843 |
| stress | delayed_replies | R1 | 6 | 0.009900002623908222 | 0.009900002623908222 |
| stress | delayed_replies | H1 | 2 | 0.26700000307755545 | 0.26700000307755545 |
| stress | large_simultaneous | R0 | 20 | 0.010099996870849282 | 0.01790000533219427 |
| stress | large_simultaneous | R1 | 20 | 0.01290000363951549 | 0.014299999747890979 |
| stress | large_simultaneous | H1 | 2 | 1.4824000027147122 | 1.4824000027147122 |
| stress | no_capacity_control | R0 | 0 | None | None |
| stress | no_capacity_control | R1 | 0 | None | None |
| stress | no_capacity_control | H1 | 0 | None | None |
| stress | resource_change_enroute | R0 | 6 | 0.018799997633323073 | 0.018799997633323073 |
| stress | resource_change_enroute | R1 | 6 | 0.013299999409355223 | 0.013299999409355223 |
| stress | resource_change_enroute | H1 | 4 | 0.2553999947849661 | 0.2553999947849661 |
| stress | scarce_greedy_trap | R0 | 2 | 0.007399998139590025 | 0.007399998139590025 |
| stress | scarce_greedy_trap | R1 | 2 | 0.007899994670879096 | 0.007899994670879096 |
| stress | scarce_greedy_trap | H1 | 2 | 0.12319999950705096 | 0.12319999950705096 |
| stress | scarce_long_detour | R0 | 2 | 0.00689999433234334 | 0.00689999433234334 |
| stress | scarce_long_detour | R1 | 2 | 0.009199997293762863 | 0.009199997293762863 |
| stress | scarce_long_detour | H1 | 2 | 0.15469999925699085 | 0.15469999925699085 |
| stress | staggered_singleton | R0 | 8 | 0.016800004232209176 | 0.016800004232209176 |
| stress | staggered_singleton | R1 | 8 | 0.011399999493733048 | 0.011399999493733048 |
| stress | staggered_singleton | H1 | 8 | 0.1706000039121136 | 0.1706000039121136 |

계산시간은 해당 PC의 관측값이며 모의 도착시간에 가산하지 않았다.
