# 처리 지원 여부에 맞춘 재확인 문의 보존

합성 validation · 570환경 · 6840실행

지원되는 큐에는 협력형 처리를 유지하고, 큐가 없는 환경은 직전 재확인 엔진을 그대로 사용한다.
원 입력의 inquiry_queue.enabled만으로 부모 엔진을 선택하며 새로운 작업자나 ACK를 만들어 넣지 않는다.

실제 수용 회신·신선하고 충분한 자원·기존 실제 자원 게이트를 모두 통과해야 배정한다.

| 분기 | 환자 | 미처치 | 전체 대기 합(환자·분) | 요청→처치 제한 합(분) | 문의 |
|---|---:|---:|---:|---:|---:|
| v4_H1_baseline | 1092 | 60 | 9403.00 | 13680.50 | 2852 |
| v4_H1_safe | 1092 | 60 | 9374.50 | 13622.00 | 2858 |
| v4_H1_probe | 1092 | 60 | 9301.00 | 13596.50 | 2870 |
| v4_C1_baseline | 1092 | 60 | 9403.00 | 13680.50 | 2852 |
| v4_C1_safe | 1092 | 60 | 9374.50 | 13622.00 | 2858 |
| v4_C1_probe | 1092 | 60 | 9301.00 | 13596.50 | 2870 |
| fixed3_H1_baseline | 1092 | 54 | 8748.75 | 13368.25 | 2531 |
| fixed3_C1_baseline | 1092 | 54 | 8748.75 | 13368.25 | 2531 |
| v4_H1_cooperative | 1092 | 60 | 9319.00 | 13566.50 | 2888 |
| v4_C1_cooperative | 1092 | 60 | 9319.00 | 13566.50 | 2888 |
| v4_H1_capability | 1092 | 60 | 9295.00 | 13542.50 | 2894 |
| v4_C1_capability | 1092 | 60 | 9295.00 | 13542.50 | 2894 |

| 전후 비교 | 처치 단축 | 동일 | 악화 | 최대 악화(분) | 대기 증가 환경 |
|---|---:|---:|---:|---:|---:|---:|
| capability_H1 | 78 | 1014 | 0 | 0.00 | 0 |
| fallback_H1 | 12 | 1080 | 0 | 0.00 | 0 |
| vs_probe_H1 | 48 | 1026 | 18 | 1.00 | 24 |
| combined_H1 | 102 | 984 | 6 | 17.00 | 0 |
| previous_cooperative_H1 | 66 | 1026 | 0 | 0.00 | 0 |
| previous_probe_H1 | 72 | 972 | 48 | 2.00 | 18 |
| previous_safe_H1 | 30 | 1056 | 6 | 17.00 | 6 |
| capability_C1 | 78 | 1014 | 0 | 0.00 | 0 |
| fallback_C1 | 12 | 1080 | 0 | 0.00 | 0 |
| vs_probe_C1 | 48 | 1026 | 18 | 1.00 | 24 |
| combined_C1 | 102 | 984 | 6 | 17.00 | 0 |
| previous_cooperative_C1 | 66 | 1026 | 0 | 0.00 | 0 |
| previous_probe_C1 | 72 | 972 | 48 | 2.00 | 18 |
| previous_safe_C1 | 30 | 1056 | 6 | 17.00 | 6 |
| fixed3_reference_H1 | 149 | 670 | 273 | 56.50 | 197 |
| fixed3_reference_C1 | 149 | 670 | 273 | 56.50 | 197 |

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
      "vs_previous_cooperative": {
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
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
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
      "vs_previous_cooperative": true,
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
      "vs_previous_cooperative": {
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
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
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
      "vs_previous_cooperative": true,
      "vs_previous_probe_population": false,
      "vs_protected_population": true
    }
  }
}
```

validation_checks는 검증의 진단 결과다. 실제 선택은 개발 잠금값이며 검증으로 재선택하지 않는다.
환자별 지연과 환경별 대기 증가를 모두 공개한다.
대기는 이동을 제외한 탐색·병원 대기·전원 준비 합이다. 요청→처치는 미처치를 동일 관찰 종료로 제한한다.
기존 411xx 개발 입력을 재사용하며, 새로운 431xx 검증 결과로 재선택하거나 규칙을 바꾸지 않는다.
