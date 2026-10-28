# 회신순·거리순·H1 최종 비교

validation · 345개 환경 · 세 규칙을 각각 새로 실행 · 합성 입력

동시 문의3곳·첫 수용 뒤 최대2분 대기·변경 알림을 고정했다. H1은 최대2048회 탐색하며 10초 안전 상한 소진은 기술 실패다.

| 평가 | 조건 | 환경 | 환자 | 규칙 | 미배정 | 미도착 | 도착 제한 평균(분) | 도착 제한 p95 | 실제 도착 p95(분모) | 문의 |
|---|---|---:|---:|---|---:|---:|---:|---:|---|---:|
| main | routine_single | 100 | 100 | R0 | 0 | 0 | 20.820 | 33.000 | 33 (100) | 319 |
| main | routine_single | 100 | 100 | R1 | 0 | 0 | 20.030 | 33.000 | 33 (100) | 319 |
| main | routine_single | 100 | 100 | H1 | 0 | 0 | 20.030 | 33.000 | 33 (100) | 319 |
| main | routine_stream | 100 | 567 | R0 | 0 | 0 | 22.009 | 37.000 | 37 (567) | 1985 |
| main | routine_stream | 100 | 567 | R1 | 0 | 0 | 21.453 | 37.000 | 37 (567) | 1987 |
| main | routine_stream | 100 | 567 | H1 | 0 | 0 | 21.422 | 37.000 | 37 (567) | 1986 |
| main | surge | 100 | 1603 | R0 | 16 | 17 | 33.840 | 80.000 | 77 (1586) | 10069 |
| main | surge | 100 | 1603 | R1 | 16 | 16 | 33.641 | 80.000 | 77 (1587) | 10040 |
| main | surge | 100 | 1603 | H1 | 15 | 16 | 33.533 | 83.000 | 77 (1587) | 10019 |
| stress | abundant_control | 5 | 20 | R0 | 0 | 0 | 12.400 | 13.000 | 13 (20) | 60 |
| stress | abundant_control | 5 | 20 | R1 | 0 | 0 | 12.400 | 13.000 | 13 (20) | 60 |
| stress | abundant_control | 5 | 20 | H1 | 0 | 0 | 12.400 | 13.000 | 13 (20) | 60 |
| stress | arrival_assessment_transfer | 5 | 5 | R0 | 0 | 0 | 27.800 | 29.000 | 29 (5) | 20 |
| stress | arrival_assessment_transfer | 5 | 5 | R1 | 0 | 0 | 27.800 | 29.000 | 29 (5) | 20 |
| stress | arrival_assessment_transfer | 5 | 5 | H1 | 0 | 0 | 27.800 | 29.000 | 29 (5) | 20 |
| stress | delayed_replies | 5 | 15 | R0 | 0 | 0 | 24.000 | 24.000 | 24 (15) | 45 |
| stress | delayed_replies | 5 | 15 | R1 | 0 | 0 | 12.000 | 12.000 | 12 (15) | 45 |
| stress | delayed_replies | 5 | 15 | H1 | 0 | 0 | 12.000 | 12.000 | 12 (15) | 45 |
| stress | large_simultaneous | 5 | 75 | R0 | 20 | 20 | 34.213 | 90.000 | 22 (55) | 515 |
| stress | large_simultaneous | 5 | 75 | R1 | 20 | 20 | 34.213 | 90.000 | 22 (55) | 515 |
| stress | large_simultaneous | 5 | 75 | H1 | 2 | 2 | 18.053 | 23.000 | 23 (73) | 269 |
| stress | no_capacity_control | 5 | 15 | R0 | 15 | 15 | 60.000 | 60.000 | — (0) | 0 |
| stress | no_capacity_control | 5 | 15 | R1 | 15 | 15 | 60.000 | 60.000 | — (0) | 0 |
| stress | no_capacity_control | 5 | 15 | H1 | 15 | 15 | 60.000 | 60.000 | — (0) | 0 |
| stress | resource_change_enroute | 5 | 10 | R0 | 0 | 0 | 22.600 | 33.000 | 33 (10) | 40 |
| stress | resource_change_enroute | 5 | 10 | R1 | 0 | 0 | 22.600 | 33.000 | 33 (10) | 40 |
| stress | resource_change_enroute | 5 | 10 | H1 | 0 | 0 | 22.600 | 33.000 | 33 (10) | 40 |
| stress | scarce_greedy_trap | 5 | 10 | R0 | 5 | 5 | 50.300 | 90.000 | 11 (5) | 100 |
| stress | scarce_greedy_trap | 5 | 10 | R1 | 5 | 5 | 50.300 | 90.000 | 11 (5) | 100 |
| stress | scarce_greedy_trap | 5 | 10 | H1 | 0 | 0 | 13.100 | 16.000 | 16 (10) | 15 |
| stress | scarce_long_detour | 5 | 10 | R0 | 5 | 5 | 50.300 | 90.000 | 11 (5) | 97 |
| stress | scarce_long_detour | 5 | 10 | R1 | 5 | 5 | 50.300 | 90.000 | 11 (5) | 97 |
| stress | scarce_long_detour | 5 | 10 | H1 | 0 | 0 | 34.100 | 58.000 | 58 (10) | 15 |
| stress | staggered_singleton | 5 | 20 | R0 | 0 | 0 | 10.600 | 11.000 | 11 (20) | 60 |
| stress | staggered_singleton | 5 | 20 | R1 | 0 | 0 | 10.600 | 11.000 | 11 (20) | 60 |
| stress | staggered_singleton | 5 | 20 | H1 | 0 | 0 | 10.600 | 11.000 | 11 (20) | 60 |

## H1 비교 판정

| 평가 | 조건 | 기준 | 주 지표 차이 | 보정 구간 | 판정 | 공통 도착 지연 평균 | 최대 지연 |
|---|---|---|---|---|---|---|---|
| main | routine_single | R0 | +0.000000 | [+0.000000, +0.000000] | inconclusive | -0.79 | 0 |
| main | routine_single | R1 | +0.000000 | [+0.000000, +0.000000] | inconclusive | 0.0 | 0 |
| main | routine_stream | R0 | +0.000000 | [+0.000000, +0.000000] | inconclusive | -0.5873015873015873 | 26 |
| main | routine_stream | R1 | +0.000000 | [+0.000000, +0.000000] | inconclusive | -0.031746031746031744 | 3 |
| main | surge | R0 | -0.001111 | [-0.004444, +0.000000] | inconclusive | -0.3192429022082019 | 100 |
| main | surge | R1 | -0.001111 | [-0.004444, +0.000000] | inconclusive | -0.14627994955863807 | 100 |
| stress | abundant_control | R0 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | abundant_control | R1 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | arrival_assessment_transfer | R0 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | arrival_assessment_transfer | R1 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | delayed_replies | R0 | — | 서술 통계 | descriptive_only_stress | -12.0 | 0 |
| stress | delayed_replies | R1 | — | 서술 통계 | descriptive_only_stress | 0.0 | 0 |
| stress | large_simultaneous | R0 | — | 서술 통계 | descriptive_only_stress | 4.528301886792453 | 7 |
| stress | large_simultaneous | R1 | — | 서술 통계 | descriptive_only_stress | 4.528301886792453 | 7 |
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
| main | routine_single | R0 | 100 | 0.01080000220099464 | 0.016199999663513154 |
| main | routine_single | R1 | 100 | 0.012600001355167478 | 0.01920000067912042 |
| main | routine_single | H1 | 100 | 0.14840000221738592 | 0.20000000222353265 |
| main | routine_stream | R0 | 567 | 0.012399999832268804 | 0.1325999983237125 |
| main | routine_stream | R1 | 567 | 0.01309999788645655 | 0.10579999798210338 |
| main | routine_stream | H1 | 550 | 0.13619999663205817 | 0.2175999979954213 |
| main | surge | R0 | 1587 | 0.010000003385357559 | 0.048800000513438135 |
| main | surge | R1 | 1587 | 0.009900002623908222 | 9.611699999368284 |
| main | surge | H1 | 1013 | 0.21149999520275742 | 1.5295999983209185 |
| stress | abundant_control | R0 | 20 | 0.03459999425103888 | 0.04839999746764079 |
| stress | abundant_control | R1 | 20 | 0.032199997804127634 | 0.03550000110408291 |
| stress | abundant_control | H1 | 5 | 0.4661999992094934 | 0.4661999992094934 |
| stress | arrival_assessment_transfer | R0 | 10 | 0.012899996363557875 | 0.012899996363557875 |
| stress | arrival_assessment_transfer | R1 | 10 | 0.012200005585327744 | 0.012200005585327744 |
| stress | arrival_assessment_transfer | H1 | 10 | 0.15410000196425244 | 0.15410000196425244 |
| stress | delayed_replies | R0 | 15 | 0.013999997463542968 | 0.013999997463542968 |
| stress | delayed_replies | R1 | 15 | 0.019699997210409492 | 0.019699997210409492 |
| stress | delayed_replies | H1 | 5 | 0.3118999957223423 | 0.3118999957223423 |
| stress | large_simultaneous | R0 | 55 | 0.01920000067912042 | 0.036900004488416016 |
| stress | large_simultaneous | R1 | 55 | 0.024599998141638935 | 0.32509999437024817 |
| stress | large_simultaneous | H1 | 5 | 2.606399997603148 | 2.606399997603148 |
| stress | no_capacity_control | R0 | 0 | None | None |
| stress | no_capacity_control | R1 | 0 | None | None |
| stress | no_capacity_control | H1 | 0 | None | None |
| stress | resource_change_enroute | R0 | 15 | 0.017899998056236655 | 0.017899998056236655 |
| stress | resource_change_enroute | R1 | 15 | 0.012599994079209864 | 0.012599994079209864 |
| stress | resource_change_enroute | H1 | 10 | 0.2744000012171455 | 0.2744000012171455 |
| stress | scarce_greedy_trap | R0 | 5 | 0.008100003469735384 | 0.008100003469735384 |
| stress | scarce_greedy_trap | R1 | 5 | 0.008499999239575118 | 0.008499999239575118 |
| stress | scarce_greedy_trap | H1 | 5 | 0.16569999570492655 | 0.16569999570492655 |
| stress | scarce_long_detour | R0 | 5 | 0.015199999324977398 | 0.015199999324977398 |
| stress | scarce_long_detour | R1 | 5 | 0.01170000177808106 | 0.01170000177808106 |
| stress | scarce_long_detour | H1 | 5 | 0.17799999477574602 | 0.17799999477574602 |
| stress | staggered_singleton | R0 | 20 | 0.016299993149004877 | 0.022899999748915434 |
| stress | staggered_singleton | R1 | 20 | 0.014700002793688327 | 0.015500001609325409 |
| stress | staggered_singleton | H1 | 20 | 0.17389999993611127 | 0.1875000016298145 |

계산시간은 해당 PC의 관측값이며 모의 도착시간에 가산하지 않았다.
