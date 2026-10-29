# 추가 자원 보고 없는 돌발 중단 회복 검증

development · 144사례 × 6분기 · 합성 입력

추가 자원 변화 보고 0건. 원래 환자별 백업 문의·유효 수용을 보존하고 기존 전원 준비시간을 유지한다.
이전 조기 출발과 기존 보고 재검토를 모두 비교한다. 미처치도 관찰 종료까지 전체 대기에 포함한다.

| 조건 | 기존 재검토 | 이전 조기 | 보존만 | 전체 회복 | 이전 대비 감소 | 단축 | 악화 |
|---|---:|---:|---:|---:|---:|---:|---:|
| fresh_control | 9 | 9 | 9 | 9 | +0 | 0 | 0 |
| missing_recheck | 9 | 9 | 9 | 9 | +0 | 0 | 0 |
| missing_unresolved | 540 | 540 | 540 | 540 | +0 | 0 | 0 |
| stale_shortage_recovery | 9 | 9 | 9 | 9 | +0 | 0 | 0 |
| delayed_optimistic_change | 33 | 33 | 33 | 33 | +0 | 0 | 0 |
| delayed_pessimistic_recovery | 63 | 63 | 63 | 63 | +0 | 0 | 0 |
| partial_team_missing | 9 | 9 | 9 | 9 | +0 | 0 | 0 |
| out_of_order | 51 | 51 | 51 | 51 | +0 | 0 | 0 |
| partial_team_unresolved | 186 | 186 | 186 | 186 | +0 | 0 | 0 |
| delayed_team_confirmation | 15 | 15 | 15 | 15 | +0 | 0 | 0 |
| confirmation_beyond_grace | 118 | 118 | 118 | 118 | +0 | 0 | 0 |
| waiting_priority_high | 9 | 9 | 9 | 9 | +0 | 0 | 0 |
| waiting_priority_low | 9 | 9 | 9 | 9 | +0 | 0 | 0 |
| multiple_waiting_patients | 12 | 12 | 12 | 12 | +0 | 0 | 0 |
| no_alternative_hospital | 108 | 108 | 108 | 108 | +0 | 0 | 0 |
| expiring_waiting_offer | 104 | 104 | 104 | 104 | +0 | 0 | 0 |
| slow_confirmation_three | 21 | 21 | 21 | 21 | +0 | 0 | 0 |
| slow_confirmation_eight | 116 | 116 | 116 | 116 | +0 | 0 | 0 |
| persistent_partial_slow | 192 | 192 | 192 | 192 | +0 | 0 | 0 |
| expiry_before_slow_confirmation | 110 | 110 | 110 | 110 | +0 | 0 | 0 |
| watch_outage_recovery | 114 | 114 | 114 | 114 | +0 | 0 | 0 |
| future_patient_not_yet_active | 78 | 78 | 78 | 78 | +0 | 0 | 0 |
| tie_older_request_priority | 107 | 107 | 107 | 107 | +0 | 0 | 0 |
| tie_same_request_identity | 104 | 104 | 104 | 104 | +0 | 0 | 0 |
| tie_equal_distance_hospital_identity | 6 | 6 | 6 | 6 | +0 | 0 | 0 |
| greedy_suboptimal_scarce_option | 6 | 6 | 6 | 6 | +0 | 0 | 0 |
| release_long_retry | 42 | 42 | 42 | 42 | +0 | 0 | 0 |
| release_before_offer_expiry | 15 | 3 | 3 | 3 | +0 | 0 | 0 |
| release_notification_delay | 42 | 42 | 42 | 42 | +0 | 0 | 0 |
| periodic_release_long_retry | 27 | 27 | 27 | 27 | +0 | 0 | 0 |
| intrinsic_single_hospital_queue | 303 | 303 | 303 | 303 | +0 | 0 | 0 |
| dominant_near_slow_other | 9 | 3 | 3 | 3 | +0 | 0 | 0 |
| ample_cohort_dominant_offer | 18 | 6 | 6 | 6 | +0 | 0 | 0 |
| nearer_offer_pending | 6 | 6 | 6 | 6 | +0 | 0 | 0 |
| competing_scarce_pending | 21 | 21 | 21 | 21 | +0 | 0 | 0 |
| dominant_offer_unknown | 12 | 12 | 12 | 12 | +0 | 0 | 0 |
| dominant_offer_stale | 12 | 12 | 12 | 12 | +0 | 0 | 0 |
| future_outage_after_early_offer | 15 | 27 | 15 | 12 | +15 | 3 | 0 |
| expiring_dominant_offer | 15 | 3 | 3 | 3 | +0 | 0 | 0 |
| notify_before_backup_reply | 15 | 27 | 15 | 12 | +15 | 3 | 0 |
| notify_after_backup_reply | 21 | 18 | 3 | 3 | +15 | 3 | 0 |
| expired_backup_after_notify | 6 | 18 | 18 | 3 | +15 | 3 | 0 |
| silent_backup_timeout | 30 | 171 | 171 | 24 | +147 | 3 | 0 |
| late_backup_timeout | 30 | 21 | 162 | 15 | +6 | 3 | 0 |
| competing_backup_capacity | 45 | 173 | 149 | 36 | +137 | 6 | 0 |
| restore_before_failed_arrival | 9 | 3 | 3 | 3 | +0 | 0 | 0 |
| unverified_backup_resources | 180 | 171 | 171 | 171 | +0 | 0 | 0 |
| outage_at_arrival_boundary | 33 | 27 | 15 | 12 | +15 | 3 | 0 |

단위는 환자·분이며 각 행은 해당 단계 시드 합계다. 양수 감소가 단축이다.

## 기존 재검토 대비 개별 결과

| 조건 | 요청→처치 합 감소 | 처치 단축 | 처치 지연 | 최대 추가 지연 | 미처치 기존→회복 |
|---|---:|---:|---:|---:|---:|
| fresh_control | +0 | 0 | 0 | 0 | 0→0 |
| missing_recheck | +0 | 0 | 0 | 0 | 0→0 |
| missing_unresolved | +0 | 0 | 0 | 0 | 9→9 |
| stale_shortage_recovery | +0 | 0 | 0 | 0 | 0→0 |
| delayed_optimistic_change | +0 | 0 | 0 | 0 | 0→0 |
| delayed_pessimistic_recovery | +0 | 0 | 0 | 0 | 0→0 |
| partial_team_missing | +0 | 0 | 0 | 0 | 0→0 |
| out_of_order | +0 | 0 | 0 | 0 | 0→0 |
| partial_team_unresolved | +0 | 0 | 0 | 0 | 3→3 |
| delayed_team_confirmation | +0 | 0 | 0 | 0 | 0→0 |
| confirmation_beyond_grace | +0 | 0 | 0 | 0 | 0→0 |
| waiting_priority_high | +0 | 0 | 0 | 0 | 0→0 |
| waiting_priority_low | +0 | 0 | 0 | 0 | 0→0 |
| multiple_waiting_patients | +0 | 0 | 0 | 0 | 0→0 |
| no_alternative_hospital | +0 | 0 | 0 | 0 | 0→0 |
| expiring_waiting_offer | +0 | 0 | 0 | 0 | 0→0 |
| slow_confirmation_three | +0 | 0 | 0 | 0 | 0→0 |
| slow_confirmation_eight | +0 | 0 | 0 | 0 | 0→0 |
| persistent_partial_slow | +0 | 0 | 0 | 0 | 3→3 |
| expiry_before_slow_confirmation | +0 | 0 | 0 | 0 | 0→0 |
| watch_outage_recovery | +0 | 0 | 0 | 0 | 0→0 |
| future_patient_not_yet_active | +0 | 0 | 0 | 0 | 0→0 |
| tie_older_request_priority | +0 | 0 | 0 | 0 | 0→0 |
| tie_same_request_identity | +0 | 0 | 0 | 0 | 0→0 |
| tie_equal_distance_hospital_identity | +0 | 0 | 0 | 0 | 0→0 |
| greedy_suboptimal_scarce_option | +0 | 0 | 0 | 0 | 0→0 |
| release_long_retry | +0 | 0 | 0 | 0 | 0→0 |
| release_before_offer_expiry | +28 | 3 | 0 | 0 | 0→0 |
| release_notification_delay | +0 | 0 | 0 | 0 | 0→0 |
| periodic_release_long_retry | +0 | 0 | 0 | 0 | 0→0 |
| intrinsic_single_hospital_queue | +0 | 0 | 0 | 0 | 0→0 |
| dominant_near_slow_other | +6 | 3 | 0 | 0 | 0→0 |
| ample_cohort_dominant_offer | +12 | 6 | 0 | 0 | 0→0 |
| nearer_offer_pending | +0 | 0 | 0 | 0 | 0→0 |
| competing_scarce_pending | +0 | 0 | 0 | 0 | 0→0 |
| dominant_offer_unknown | +0 | 0 | 0 | 0 | 0→0 |
| dominant_offer_stale | +0 | 0 | 0 | 0 | 0→0 |
| future_outage_after_early_offer | +1 | 1 | 0 | 0 | 0→0 |
| expiring_dominant_offer | +28 | 3 | 0 | 0 | 0→0 |
| notify_before_backup_reply | +1 | 1 | 0 | 0 | 0→0 |
| notify_after_backup_reply | +18 | 3 | 0 | 0 | 0→0 |
| expired_backup_after_notify | +1 | 1 | 0 | 0 | 0→0 |
| silent_backup_timeout | +3 | 3 | 0 | 0 | 0→0 |
| late_backup_timeout | +12 | 3 | 0 | 0 | 0→0 |
| competing_backup_capacity | +4 | 4 | 0 | 0 | 0→0 |
| restore_before_failed_arrival | +6 | 3 | 0 | 0 | 0→0 |
| unverified_backup_resources | +0 | 0 | 0 | 0 | 3→3 |
| outage_at_arrival_boundary | +21 | 3 | 0 | 0 | 0→0 |

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
