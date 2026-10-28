# 늦게 도착한 병원 회신 재검증

합성 development · 216환경 · 1728실행

기존 보호 V4와 이전 보충 문의 후보를 각각 기준으로 늦은 회신 재검증을 비교한다.
원 기한은 유지하며, 실제 도착한 회신을 현재 조건으로 재검증한다. 회신을 추가로 기다리지 않는다.

| 분기 | 환자 | 미처치 | 전체 대기 합(환자·분) | 요청→처치 제한 합(분) | 문의 |
|---|---:|---:|---:|---:|---:|
| v4_H1_baseline | 453 | 30 | 4453.75 | 6240.50 | 1269 |
| v4_H1_scarce | 453 | 30 | 4433.25 | 6214.00 | 1272 |
| v4_H1_late | 453 | 30 | 4430.25 | 6211.00 | 1269 |
| v4_C1_baseline | 453 | 30 | 4453.75 | 6240.50 | 1269 |
| v4_C1_scarce | 453 | 30 | 4433.25 | 6214.00 | 1272 |
| v4_C1_late | 453 | 30 | 4430.25 | 6211.00 | 1269 |
| fixed3_H1_baseline | 453 | 27 | 4126.50 | 6029.75 | 1162 |
| fixed3_C1_baseline | 453 | 27 | 4126.50 | 6029.75 | 1162 |

| 전후 비교 | 처치 단축 | 동일 | 악화 | 최대 악화(분) | 대기 증가 환경 |
|---|---:|---:|---:|---:|---:|---:|
| previous_candidate_H1 | 12 | 435 | 6 | 16.00 | 3 |
| late_recovery_H1 | 3 | 450 | 0 | 0.00 | 0 |
| combined_H1 | 12 | 438 | 3 | 16.00 | 0 |
| previous_candidate_C1 | 12 | 435 | 6 | 16.00 | 3 |
| late_recovery_C1 | 3 | 450 | 0 | 0.00 | 0 |
| combined_C1 | 12 | 438 | 3 | 16.00 | 0 |
| fixed3_reference_H1 | 37 | 287 | 129 | 56.50 | 72 |
| fixed3_reference_C1 | 37 | 287 | 129 | 56.50 | 72 |

개발에서 고정한 선택:

```json
{
  "H1": {
    "policy": "v4_H1_late",
    "candidate": "bounded_late_reply",
    "checks": {
      "vs_protected": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": true,
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
      }
    },
    "gates": {
      "vs_previous_candidate": true,
      "vs_protected_population": true
    }
  },
  "C1": {
    "policy": "v4_C1_late",
    "candidate": "bounded_late_reply",
    "checks": {
      "vs_protected": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": true,
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
      }
    },
    "gates": {
      "vs_previous_candidate": true,
      "vs_protected_population": true
    }
  }
}
```

validation_checks는 검증의 진단 결과다. 실제 선택은 개발 잠금값이며 검증으로 재선택하지 않는다.
환자별 지연과 환경별 대기 증가를 모두 공개한다.
대기는 이동을 제외한 탐색·병원 대기·전원 준비 합이다. 요청→처치는 미처치를 동일 관찰 종료로 제한한다.
이전 보충 후보의 환자 간 절충과 새 회신 회복으로 추가된 변화를 분리해 공개한다.
