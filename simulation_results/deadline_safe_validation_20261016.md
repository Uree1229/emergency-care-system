# 진행 중인 문의를 보호하는 늦은 회신 재검증

합성 validation · 438환경 · 4380실행

기존 보호 V4·이전 보충 문의 후보·보호 없는 회신 시제품을 각각 기준으로 비교한다.
원 기한은 유지하며, 실제 도착한 회신을 현재 조건으로 재검증한다. 회신을 추가로 기다리지 않는다.

진행 중인 문의가 있으면 선택적인 늦은 제안을 사용하지 않아 기존 회신 대기 시계를 보호한다.

| 분기 | 환자 | 미처치 | 전체 대기 합(환자·분) | 요청→처치 제한 합(분) | 문의 |
|---|---:|---:|---:|---:|---:|
| v4_H1_baseline | 918 | 60 | 8915.25 | 12524.75 | 2553 |
| v4_H1_scarce | 918 | 60 | 8901.75 | 12481.25 | 2565 |
| v4_H1_late | 918 | 60 | 8894.25 | 12485.75 | 2559 |
| v4_H1_safe | 918 | 60 | 8895.75 | 12475.25 | 2559 |
| v4_C1_baseline | 918 | 60 | 8915.25 | 12524.75 | 2553 |
| v4_C1_scarce | 918 | 60 | 8901.75 | 12481.25 | 2565 |
| v4_C1_late | 918 | 60 | 8894.25 | 12485.75 | 2559 |
| v4_C1_safe | 918 | 60 | 8895.75 | 12475.25 | 2559 |
| fixed3_H1_baseline | 918 | 54 | 8317.00 | 12139.50 | 2347 |
| fixed3_C1_baseline | 918 | 54 | 8317.00 | 12139.50 | 2347 |

| 전후 비교 | 처치 단축 | 동일 | 악화 | 최대 악화(분) | 대기 증가 환경 |
|---|---:|---:|---:|---:|---:|---:|
| unprotected_late_recovery_H1 | 6 | 906 | 6 | 1.75 | 0 |
| pending_guard_H1 | 6 | 912 | 0 | 0.00 | 6 |
| late_recovery_H1 | 6 | 912 | 0 | 0.00 | 0 |
| combined_H1 | 30 | 882 | 6 | 17.00 | 7 |
| unprotected_late_recovery_C1 | 6 | 906 | 6 | 1.75 | 0 |
| pending_guard_C1 | 6 | 912 | 0 | 0.00 | 6 |
| late_recovery_C1 | 6 | 912 | 0 | 0.00 | 0 |
| combined_C1 | 30 | 882 | 6 | 17.00 | 7 |
| fixed3_reference_H1 | 80 | 590 | 248 | 56.50 | 142 |
| fixed3_reference_C1 | 80 | 590 | 248 | 56.50 | 142 |

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
