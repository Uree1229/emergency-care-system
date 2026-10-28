# 일반 문의를 보호하는 선택적 재확인 처리

합성 validation · 570환경 · 5700실행

직전 안전 후보·재확인 후보·기존 보호 V4를 각각 기준으로 새 접수·작업 처리 규칙을 비교한다.
병원이 바쁘면 선택적 재확인을 비접수 처리하고, 새 일반 문의가 오면 재확인 작업을 중단·재개한다.

실제 수용 회신·신선하고 충분한 자원·기존 실제 자원 게이트를 모두 통과해야 배정한다.

| 분기 | 환자 | 미처치 | 전체 대기 합(환자·분) | 요청→처치 제한 합(분) | 문의 |
|---|---:|---:|---:|---:|---:|
| v4_H1_baseline | 1092 | 60 | 9369.50 | 13612.00 | 2861 |
| v4_H1_safe | 1092 | 60 | 9351.00 | 13563.50 | 2867 |
| v4_H1_probe | 1092 | 60 | 9277.50 | 13538.00 | 2879 |
| v4_C1_baseline | 1092 | 60 | 9401.75 | 13639.25 | 2865 |
| v4_C1_safe | 1092 | 60 | 9351.00 | 13563.50 | 2867 |
| v4_C1_probe | 1092 | 60 | 9277.50 | 13538.00 | 2879 |
| fixed3_H1_baseline | 1092 | 54 | 8724.00 | 13318.50 | 2534 |
| fixed3_C1_baseline | 1092 | 54 | 8724.00 | 13318.50 | 2534 |
| v4_H1_cooperative | 1092 | 60 | 9295.50 | 13508.00 | 2897 |
| v4_C1_cooperative | 1092 | 60 | 9295.50 | 13508.00 | 2897 |

| 전후 비교 | 처치 단축 | 동일 | 악화 | 최대 악화(분) | 대기 증가 환경 |
|---|---:|---:|---:|---:|---:|---:|
| cooperative_H1 | 66 | 1026 | 0 | 0.00 | 0 |
| workflow_H1 | 48 | 1014 | 30 | 2.00 | 36 |
| combined_H1 | 90 | 996 | 6 | 17.00 | 0 |
| previous_probe_H1 | 72 | 972 | 48 | 2.00 | 18 |
| previous_safe_H1 | 30 | 1056 | 6 | 17.00 | 6 |
| cooperative_C1 | 66 | 1026 | 0 | 0.00 | 0 |
| workflow_C1 | 48 | 1014 | 30 | 2.00 | 36 |
| combined_C1 | 91 | 994 | 7 | 17.00 | 0 |
| previous_probe_C1 | 72 | 972 | 48 | 2.00 | 18 |
| previous_safe_C1 | 31 | 1054 | 7 | 17.00 | 6 |
| fixed3_reference_H1 | 146 | 674 | 272 | 56.50 | 198 |
| fixed3_reference_C1 | 146 | 674 | 272 | 56.50 | 198 |

개발에서 고정한 선택:

```json
{
  "H1": {
    "policy": "v4_H1_baseline",
    "candidate": "protected_v4_serial_registration",
    "checks": {
      "vs_previous_safe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "vs_previous_probe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": false
      },
      "vs_protected": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      }
    },
    "gates": {
      "vs_previous_safe": true,
      "vs_previous_probe_population": false,
      "vs_protected_population": true
    }
  },
  "C1": {
    "policy": "v4_C1_baseline",
    "candidate": "protected_v4_serial_registration",
    "checks": {
      "vs_previous_safe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "vs_previous_probe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": false
      },
      "vs_protected": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      }
    },
    "gates": {
      "vs_previous_safe": true,
      "vs_previous_probe_population": false,
      "vs_protected_population": true
    }
  }
}
```

validation_checks는 검증의 진단 결과다. 실제 선택은 개발 잠금값이며 검증으로 재선택하지 않는다.
환자별 지연과 환경별 대기 증가를 모두 공개한다.
대기는 이동을 제외한 탐색·병원 대기·전원 준비 합이다. 요청→처치는 미처치를 동일 관찰 종료로 제한한다.
현재 작업 상태 ACK와 선점 처리는 새 병원 업무 계약이다. 실제 병원 지원이 없으면 이 효과를 주장할 수 없다.
