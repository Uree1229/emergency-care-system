# 추가 자원 보고 없는 돌발 중단 회복 검증

validation · 288사례 × 6분기 · 합성 입력

추가 자원 변화 보고 0건. 원래 환자별 백업 문의·유효 수용을 보존하고 기존 전원 준비시간을 유지한다.
이전 조기 출발과 기존 보고 재검토를 모두 비교한다. 미처치도 관찰 종료까지 전체 대기에 포함한다.

| 조건 | 기존 재검토 | 이전 조기 | 보존만 | 전체 회복 | 이전 대비 감소 | 단축 | 악화 |
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
| confirmation_beyond_grace | 252 | 252 | 252 | 252 | +0 | 0 | 0 |
| waiting_priority_high | 18 | 18 | 18 | 18 | +0 | 0 | 0 |
| waiting_priority_low | 18 | 18 | 18 | 18 | +0 | 0 | 0 |
| multiple_waiting_patients | 24 | 24 | 24 | 24 | +0 | 0 | 0 |
| no_alternative_hospital | 236 | 236 | 236 | 236 | +0 | 0 | 0 |
| expiring_waiting_offer | 233 | 233 | 233 | 233 | +0 | 0 | 0 |
| slow_confirmation_three | 42 | 42 | 42 | 42 | +0 | 0 | 0 |
| slow_confirmation_eight | 276 | 276 | 276 | 276 | +0 | 0 | 0 |
| persistent_partial_slow | 384 | 384 | 384 | 384 | +0 | 0 | 0 |
| expiry_before_slow_confirmation | 264 | 264 | 264 | 264 | +0 | 0 | 0 |
| watch_outage_recovery | 246 | 246 | 246 | 246 | +0 | 0 | 0 |
| future_patient_not_yet_active | 174 | 174 | 174 | 174 | +0 | 0 | 0 |
| tie_older_request_priority | 208 | 208 | 208 | 208 | +0 | 0 | 0 |
| tie_same_request_identity | 202 | 202 | 202 | 202 | +0 | 0 | 0 |
| tie_equal_distance_hospital_identity | 12 | 12 | 12 | 12 | +0 | 0 | 0 |
| greedy_suboptimal_scarce_option | 12 | 12 | 12 | 12 | +0 | 0 | 0 |
| release_long_retry | 84 | 84 | 84 | 84 | +0 | 0 | 0 |
| release_before_offer_expiry | 30 | 6 | 6 | 6 | +0 | 0 | 0 |
| release_notification_delay | 84 | 84 | 84 | 84 | +0 | 0 | 0 |
| periodic_release_long_retry | 54 | 54 | 54 | 54 | +0 | 0 | 0 |
| intrinsic_single_hospital_queue | 588 | 588 | 588 | 588 | +0 | 0 | 0 |
| dominant_near_slow_other | 18 | 6 | 6 | 6 | +0 | 0 | 0 |
| ample_cohort_dominant_offer | 36 | 12 | 12 | 12 | +0 | 0 | 0 |
| nearer_offer_pending | 12 | 12 | 12 | 12 | +0 | 0 | 0 |
| competing_scarce_pending | 42 | 42 | 42 | 42 | +0 | 0 | 0 |
| dominant_offer_unknown | 24 | 24 | 24 | 24 | +0 | 0 | 0 |
| dominant_offer_stale | 24 | 24 | 24 | 24 | +0 | 0 | 0 |
| future_outage_after_early_offer | 30 | 54 | 30 | 24 | +30 | 6 | 0 |
| expiring_dominant_offer | 30 | 6 | 6 | 6 | +0 | 0 | 0 |
| notify_before_backup_reply | 30 | 54 | 30 | 24 | +30 | 6 | 0 |
| notify_after_backup_reply | 42 | 36 | 6 | 6 | +30 | 6 | 0 |
| expired_backup_after_notify | 12 | 36 | 36 | 6 | +30 | 6 | 0 |
| silent_backup_timeout | 60 | 342 | 342 | 48 | +294 | 6 | 0 |
| late_backup_timeout | 60 | 42 | 324 | 30 | +12 | 6 | 0 |
| competing_backup_capacity | 90 | 343 | 295 | 72 | +271 | 12 | 0 |
| restore_before_failed_arrival | 18 | 6 | 6 | 6 | +0 | 0 | 0 |
| unverified_backup_resources | 360 | 342 | 342 | 342 | +0 | 0 | 0 |
| outage_at_arrival_boundary | 66 | 54 | 30 | 24 | +30 | 6 | 0 |

단위는 환자·분이며 각 행은 해당 단계 시드 합계다. 양수 감소가 단축이다.

## 기존 재검토 대비 개별 결과

| 조건 | 요청→처치 합 감소 | 처치 단축 | 처치 지연 | 최대 추가 지연 | 미처치 기존→회복 |
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
| periodic_release_long_retry | +0 | 0 | 0 | 0 | 0→0 |
| intrinsic_single_hospital_queue | +0 | 0 | 0 | 0 | 0→0 |
| dominant_near_slow_other | +12 | 6 | 0 | 0 | 0→0 |
| ample_cohort_dominant_offer | +24 | 12 | 0 | 0 | 0→0 |
| nearer_offer_pending | +0 | 0 | 0 | 0 | 0→0 |
| competing_scarce_pending | +0 | 0 | 0 | 0 | 0→0 |
| dominant_offer_unknown | +0 | 0 | 0 | 0 | 0→0 |
| dominant_offer_stale | +0 | 0 | 0 | 0 | 0→0 |
| future_outage_after_early_offer | +2 | 2 | 0 | 0 | 0→0 |
| expiring_dominant_offer | +59 | 6 | 0 | 0 | 0→0 |
| notify_before_backup_reply | +2 | 2 | 0 | 0 | 0→0 |
| notify_after_backup_reply | +39 | 6 | 0 | 0 | 0→0 |
| expired_backup_after_notify | +5 | 5 | 0 | 0 | 0→0 |
| silent_backup_timeout | +6 | 6 | 0 | 0 | 0→0 |
| late_backup_timeout | +24 | 6 | 0 | 0 | 0→0 |
| competing_backup_capacity | +8 | 8 | 0 | 0 | 0→0 |
| restore_before_failed_arrival | +12 | 6 | 0 | 0 | 0→0 |
| unverified_backup_resources | +0 | 0 | 0 | 0 | 6→6 |
| outage_at_arrival_boundary | +42 | 6 | 0 | 0 | 0→0 |

관찰 종료로 제한한 지표와 미처치 수를 함께 해석한다. 원시 JSON에 모든 환자 결과와 준비·문의·수용 근거를 보존한다.

## 범위와 한계

- 추가 자원 변화 보고는 모든 분기에서 0건이며 기존 초기·주기·재확인 및 환자별 문의 회신만 사용한다.
- 보존한 문의는 원래 절대 시간 초과 이후 수용으로 되살리지 않는다. 실제 병원 작업은 취소하지 않는다.
- 수용의 원래 수신·만료 시각을 유지하며 정상 이송 중 백업 수용만으로 자동 방향 변경하지 않는다.
- 실패 도착 평가·물리적 이동·기존 전원 준비시간을 그대로 두고 준비 완료 분에 기존 검증·공동 배정을 실행한다.
- 실패 병원 재문의는 현재 받은 기존 보고의 모든 필수 수량이 실패 후 관측되고 신선·충분할 때만 허용한다.
- 만료 백업의 새 문의는 기존 환자별 알림과 만료 증거에 근거한다. 새 수용 응답과 최종 충돌 검사가 필요하다.
- 전체 환자 대기와 관찰 종료로 제한한 처치 도달을 함께 평가한다. 실제 병상 점유 대기와 지속 누락은 남는다.
- 48개 합성 환경의 새 검증 시드이며 운영 서버·API·팀원 UI나 실제 임상 안전성·전역 이송 최적성을 검증하지 않는다.
