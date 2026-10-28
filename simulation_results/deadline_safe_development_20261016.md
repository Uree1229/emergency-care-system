# 진행 중인 문의를 보호하는 늦은 회신 재검증

합성 development · 219환경 · 2190실행

기존 보호 V4·이전 보충 문의 후보·보호 없는 회신 시제품을 각각 기준으로 비교한다.
원 기한은 유지하며, 실제 도착한 회신을 현재 조건으로 재검증한다. 회신을 추가로 기다리지 않는다.

진행 중인 문의가 있으면 선택적인 늦은 제안을 사용하지 않아 기존 회신 대기 시계를 보호한다.

| 분기 | 환자 | 미처치 | 전체 대기 합(환자·분) | 요청→처치 제한 합(분) | 문의 |
|---|---:|---:|---:|---:|---:|
| v4_H1_baseline | 459 | 30 | 4427.00 | 6241.75 | 1269 |
| v4_H1_scarce | 459 | 30 | 4418.25 | 6218.00 | 1275 |
| v4_H1_late | 459 | 30 | 4414.50 | 6220.25 | 1272 |
| v4_H1_safe | 459 | 30 | 4415.25 | 6215.00 | 1272 |
| v4_C1_baseline | 459 | 30 | 4427.00 | 6241.75 | 1269 |
| v4_C1_scarce | 459 | 30 | 4418.25 | 6218.00 | 1275 |
| v4_C1_late | 459 | 30 | 4414.50 | 6220.25 | 1272 |
| v4_C1_safe | 459 | 30 | 4415.25 | 6215.00 | 1272 |
| fixed3_H1_baseline | 459 | 27 | 4130.00 | 6049.25 | 1176 |
| fixed3_C1_baseline | 459 | 27 | 4130.00 | 6049.25 | 1176 |

| 전후 비교 | 처치 단축 | 동일 | 악화 | 최대 악화(분) | 대기 증가 환경 |
|---|---:|---:|---:|---:|---:|---:|
| unprotected_late_recovery_H1 | 3 | 453 | 3 | 1.75 | 0 |
| pending_guard_H1 | 3 | 456 | 0 | 0.00 | 3 |
| late_recovery_H1 | 3 | 456 | 0 | 0.00 | 0 |
| combined_H1 | 15 | 441 | 3 | 16.00 | 3 |
| unprotected_late_recovery_C1 | 3 | 453 | 3 | 1.75 | 0 |
| pending_guard_C1 | 3 | 456 | 0 | 0.00 | 3 |
| late_recovery_C1 | 3 | 456 | 0 | 0.00 | 0 |
| combined_C1 | 15 | 441 | 3 | 16.00 | 3 |
| fixed3_reference_H1 | 43 | 293 | 123 | 56.50 | 70 |
| fixed3_reference_C1 | 43 | 293 | 123 | 56.50 | 70 |

개발에서 고정한 선택:

```json
{
  "H1": {
    "policy": "v4_H1_baseline",
    "candidate": "protected_v4_serial_registration",
    "checks": {
      "vs_protected": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "vs_previous_candidate": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "vs_unguarded_late": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": false
      }
    },
    "gates": {
      "vs_previous_candidate": true,
      "vs_unguarded_late": false,
      "vs_protected_population": false
    }
  },
  "C1": {
    "policy": "v4_C1_baseline",
    "candidate": "protected_v4_serial_registration",
    "checks": {
      "vs_protected": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "vs_previous_candidate": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "vs_unguarded_late": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": false
      }
    },
    "gates": {
      "vs_previous_candidate": true,
      "vs_unguarded_late": false,
      "vs_protected_population": false
    }
  }
}
```

validation_checks는 검증의 진단 결과다. 실제 선택은 개발 잠금값이며 검증으로 재선택하지 않는다.
환자별 지연과 환경별 대기 증가를 모두 공개한다.
대기는 이동을 제외한 탐색·병원 대기·전원 준비 합이다. 요청→처치는 미처치를 동일 관찰 종료로 제한한다.
이전 보충 후보의 환자 간 절충과 새 회신 회복으로 추가된 변화를 분리해 공개한다.
