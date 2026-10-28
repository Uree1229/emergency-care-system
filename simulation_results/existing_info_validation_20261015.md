# 기존 정보만으로 전체 대기 개선 검증

validation · 234사례 × 4분기 · 합성 입력

추가 자원 변화 보고는 전 분기에서 0건. 기존 초기·주기·재확인 보고와 유효 수용만 사용한다.
모든 환자의 대기 합을 사용하며 미처치는 관찰 종료까지 포함한다. 이동은 대기 합에서 제외한다.
조기 출발 뒤 미래 병상 중단이 발생하는 반례도 포함한다. 전체 단축과 개별 악화를 함께 해석한다.

| 조건 | 기존 | 재검토만 | 조기 검토만 | 함께 적용 | 전체 감소 | 단축 환자 | 악화 환자 |
|---|---:|---:|---:|---:|---:|---:|---:|
| fresh_control | 18 | 18 | 18 | 18 | +0 | 0 | 0 |
| missing_recheck | 18 | 18 | 18 | 18 | +0 | 0 | 0 |
| missing_unresolved | 1080 | 1080 | 1080 | 1080 | +0 | 0 | 0 |
| stale_shortage_recovery | 18 | 18 | 18 | 18 | +0 | 0 | 0 |
| delayed_optimistic_change | 66 | 66 | 66 | 66 | +0 | 0 | 0 |
| delayed_pessimistic_recovery | 126 | 126 | 126 | 126 | +0 | 0 | 0 |
| partial_team_missing | 18 | 18 | 18 | 18 | +0 | 0 | 0 |
| out_of_order | 102 | 102 | 102 | 102 | +0 | 0 | 0 |
| partial_team_unresolved | 372 | 372 | 372 | 372 | +0 | 0 | 0 |
| delayed_team_confirmation | 30 | 30 | 30 | 30 | +0 | 0 | 0 |
| confirmation_beyond_grace | 244 | 244 | 244 | 244 | +0 | 0 | 0 |
| waiting_priority_high | 18 | 18 | 18 | 18 | +0 | 0 | 0 |
| waiting_priority_low | 18 | 18 | 18 | 18 | +0 | 0 | 0 |
| multiple_waiting_patients | 24 | 24 | 24 | 24 | +0 | 0 | 0 |
| no_alternative_hospital | 224 | 224 | 224 | 224 | +0 | 0 | 0 |
| expiring_waiting_offer | 233 | 233 | 233 | 233 | +0 | 0 | 0 |
| slow_confirmation_three | 42 | 42 | 42 | 42 | +0 | 0 | 0 |
| slow_confirmation_eight | 265 | 265 | 265 | 265 | +0 | 0 | 0 |
| persistent_partial_slow | 384 | 384 | 384 | 384 | +0 | 0 | 0 |
| expiry_before_slow_confirmation | 253 | 253 | 253 | 253 | +0 | 0 | 0 |
| watch_outage_recovery | 234 | 234 | 234 | 234 | +0 | 0 | 0 |
| future_patient_not_yet_active | 162 | 162 | 162 | 162 | +0 | 0 | 0 |
| tie_older_request_priority | 212 | 212 | 212 | 212 | +0 | 0 | 0 |
| tie_same_request_identity | 206 | 206 | 206 | 206 | +0 | 0 | 0 |
| tie_equal_distance_hospital_identity | 12 | 12 | 12 | 12 | +0 | 0 | 0 |
| greedy_suboptimal_scarce_option | 12 | 12 | 12 | 12 | +0 | 0 | 0 |
| release_long_retry | 84 | 84 | 84 | 84 | +0 | 0 | 0 |
| release_before_offer_expiry | 30 | 30 | 6 | 6 | +24 | 6 | 0 |
| release_notification_delay | 84 | 84 | 84 | 84 | +0 | 0 | 0 |
| periodic_release_long_retry | 84 | 54 | 84 | 54 | +30 | 6 | 0 |
| intrinsic_single_hospital_queue | 600 | 600 | 600 | 600 | +0 | 0 | 0 |
| dominant_near_slow_other | 18 | 18 | 6 | 6 | +12 | 6 | 0 |
| ample_cohort_dominant_offer | 36 | 36 | 12 | 12 | +24 | 12 | 0 |
| nearer_offer_pending | 12 | 12 | 12 | 12 | +0 | 0 | 0 |
| competing_scarce_pending | 42 | 42 | 42 | 42 | +0 | 0 | 0 |
| dominant_offer_unknown | 24 | 24 | 24 | 24 | +0 | 0 | 0 |
| dominant_offer_stale | 24 | 24 | 24 | 24 | +0 | 0 | 0 |
| future_outage_after_early_offer | 30 | 30 | 54 | 54 | -24 | 0 | 6 |
| expiring_dominant_offer | 30 | 30 | 6 | 6 | +24 | 6 | 0 |

전체 감소의 양수는 단축이다. 각 행은 해당 단계 시드 합계, 단위는 환자·분이다.

## 환자별 처치 도달과 미처치

| 조건 | 요청→처치 합 감소 | 처치 도달 단축 | 처치 도달 악화 | 최대 추가 지연 | 미처치 기존→개선 |
|---|---:|---:|---:|---:|---:|
| fresh_control | +0 | 0 | 0 | 0 | 0→0 |
| missing_recheck | +0 | 0 | 0 | 0 | 0→0 |
| missing_unresolved | +0 | 0 | 0 | 0 | 18→18 |
| stale_shortage_recovery | +0 | 0 | 0 | 0 | 0→0 |
| delayed_optimistic_change | +0 | 0 | 0 | 0 | 0→0 |
| delayed_pessimistic_recovery | +0 | 0 | 0 | 0 | 0→0 |
| partial_team_missing | +0 | 0 | 0 | 0 | 0→0 |
| out_of_order | +0 | 0 | 0 | 0 | 0→0 |
| partial_team_unresolved | +0 | 0 | 0 | 0 | 6→6 |
| delayed_team_confirmation | +0 | 0 | 0 | 0 | 0→0 |
| confirmation_beyond_grace | +0 | 0 | 0 | 0 | 0→0 |
| waiting_priority_high | +0 | 0 | 0 | 0 | 0→0 |
| waiting_priority_low | +0 | 0 | 0 | 0 | 0→0 |
| multiple_waiting_patients | +0 | 0 | 0 | 0 | 0→0 |
| no_alternative_hospital | +0 | 0 | 0 | 0 | 0→0 |
| expiring_waiting_offer | +0 | 0 | 0 | 0 | 0→0 |
| slow_confirmation_three | +0 | 0 | 0 | 0 | 0→0 |
| slow_confirmation_eight | +0 | 0 | 0 | 0 | 0→0 |
| persistent_partial_slow | +0 | 0 | 0 | 0 | 6→6 |
| expiry_before_slow_confirmation | +0 | 0 | 0 | 0 | 0→0 |
| watch_outage_recovery | +0 | 0 | 0 | 0 | 0→0 |
| future_patient_not_yet_active | +0 | 0 | 0 | 0 | 0→0 |
| tie_older_request_priority | +0 | 0 | 0 | 0 | 0→0 |
| tie_same_request_identity | +0 | 0 | 0 | 0 | 0→0 |
| tie_equal_distance_hospital_identity | +0 | 0 | 0 | 0 | 0→0 |
| greedy_suboptimal_scarce_option | +0 | 0 | 0 | 0 | 0→0 |
| release_long_retry | +0 | 0 | 0 | 0 | 0→0 |
| release_before_offer_expiry | +59 | 6 | 0 | 0 | 0→0 |
| release_notification_delay | +0 | 0 | 0 | 0 | 0→0 |
| periodic_release_long_retry | +30 | 6 | 0 | 0 | 0→0 |
| intrinsic_single_hospital_queue | +0 | 0 | 0 | 0 | 0→0 |
| dominant_near_slow_other | +12 | 6 | 0 | 0 | 0→0 |
| ample_cohort_dominant_offer | +24 | 12 | 0 | 0 | 0→0 |
| nearer_offer_pending | +0 | 0 | 0 | 0 | 0→0 |
| competing_scarce_pending | +0 | 0 | 0 | 0 | 0→0 |
| dominant_offer_unknown | +0 | 0 | 0 | 0 | 0→0 |
| dominant_offer_stale | +0 | 0 | 0 | 0 | 0→0 |
| future_outage_after_early_offer | -30 | 0 | 6 | 5 | 0→0 |
| expiring_dominant_offer | +59 | 6 | 0 | 0 | 0→0 |

미처치 환자의 요청→처치는 관찰 종료로 제한한다. 원시 JSON에 모든 환자의 대기·도달 시각을 보존한다.
## 범위와 한계

- 추가 자원 변화 보고를 모든 분기에서 끈다. 기존 정보 계약의 초기 보고·주기 피드·기존 재확인만 사용한다.
- 조기 검토 규칙은 자원 수량 재요청을 직접 수행하지 않는다. 환자별 수용과 실제 최종 충돌 검사는 필요하다.
- 분기별 처치 시각 변화로 기존 주기 피드의 점유량은 달라질 수 있다. 동일한 미래 점유량을 정책에 재생하지 않는다.
- 현재 최단 수용의 우월성은 미래의 안전성을 보장하지 않는다. 미래 병상 중단 반례의 개별 악화도 보존한다.
- 전체 합이 줄어도 모든 환자에게 이롭다는 뜻은 아니다. 대기 증가와 처치 도달 증가를 별도 평가한다.
- 전체 대기는 모든 환자의 탐색·병원 대기·전원 대기 합이다. 미처치는 관찰 종료까지 포함하고 이동은 별도 평가한다.
- 병상 수·환자 수·처치시간·물리적 사건을 바꾸지 않는다. 실제로 점유된 자원 때문에 발생하는 대기는 남는다.
- C1은 현재 모형의 미배정 우선순위 합과 가중 이동시간을 최적화하며 전체 이송 시간의 전역 최적성을 보장하지 않는다.
- 최종 충돌 검사는 즉시 권위 있는 자원을 확인한다는 기존 실험 가정이며 실제 운영 성능 검증이 아니다.
- 39개 합성 환경의 새 시드 비교이며 운영 서버·API·팀원 UI에는 적용하지 않는다.
