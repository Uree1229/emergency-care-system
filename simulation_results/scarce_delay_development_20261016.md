# 희소 병상 보호 지연 개선 검증

합성 development · 201환경 · 1608실행

기존 V4 + 병상 기회 보호를 기준으로 동시 요청 등록과 초기 병행 보충 문의를 분리해 비교한다.
원래 문의를 보존하고, 현재 정보로 확인되는 대안 병원에 최초 문의 시 한 건만 보충한다.

| 분기 | 환자 | 미처치 | 전체 대기 합(환자·분) | 요청→처치 제한 합(분) | 문의 |
|---|---:|---:|---:|---:|---:|
| v4_H1_baseline | 420 | 30 | 4299.25 | 5965.00 | 1181 |
| v4_H1_cohort | 420 | 30 | 4299.25 | 5965.00 | 1181 |
| v4_H1_scarce | 420 | 30 | 4297.00 | 5962.75 | 1184 |
| v4_C1_baseline | 420 | 30 | 4299.25 | 5965.00 | 1181 |
| v4_C1_cohort | 420 | 30 | 4299.25 | 5965.00 | 1181 |
| v4_C1_scarce | 420 | 30 | 4297.00 | 5962.75 | 1184 |
| fixed3_H1_baseline | 420 | 27 | 4016.75 | 5796.00 | 1075 |
| fixed3_C1_baseline | 420 | 27 | 4016.75 | 5796.00 | 1075 |

| 전후 비교 | 처치 단축 | 동일 | 악화 | 최대 악화(분) | 대기 증가 환경 |
|---|---:|---:|---:|---:|---:|---:|
| registration_H1 | 0 | 420 | 0 | 0.00 | 0 |
| routing_increment_H1 | 9 | 408 | 3 | 1.00 | 3 |
| combined_H1 | 9 | 408 | 3 | 1.00 | 3 |
| registration_C1 | 0 | 420 | 0 | 0.00 | 0 |
| routing_increment_C1 | 9 | 408 | 3 | 1.00 | 3 |
| combined_C1 | 9 | 408 | 3 | 1.00 | 3 |
| fixed3_reference_H1 | 33 | 264 | 123 | 56.50 | 72 |
| fixed3_reference_C1 | 33 | 264 | 123 | 56.50 | 72 |

개발에서 고정한 선택:

```json
{
  "H1": {
    "policy": "v4_H1_baseline",
    "candidate": "protected_v4_serial_registration",
    "checks": {
      "registration_only": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": false
      },
      "registration_and_routing": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      }
    }
  },
  "C1": {
    "policy": "v4_C1_baseline",
    "candidate": "protected_v4_serial_registration",
    "checks": {
      "registration_only": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": false
      },
      "registration_and_routing": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      }
    }
  }
}
```

validation_checks는 검증의 진단 결과다. 실제 선택은 개발 잠금값이며 검증으로 재선택하지 않는다.
환자별 지연과 환경별 대기 증가를 모두 공개한다.
대기는 이동을 제외한 탐색·병원 대기·전원 준비 합이다. 요청→처치는 미처치를 동일 관찰 종료로 제한한다.
기존 보호를 끈 결과보다 일부 유연 환자의 이동이 길어지는 물리적 상충은 제거했다고 주장하지 않는다.
