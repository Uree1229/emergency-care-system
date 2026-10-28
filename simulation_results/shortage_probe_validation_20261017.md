# 현재 병상 부족 보고의 제한적 수용 재확인

합성 validation · 498환경 · 3984실행

직전 안전 회신 후보와 기존 보호 V4를 각각 기준으로 제한적 만석 병원 재확인을 비교한다.
현재 병상 부족 보고를 가용으로 바꾸지 않고, 실제 수용 재확인 문의를 최대 한 번 제출한다.

실제 수용 회신·신선하고 충분한 자원·기존 실제 자원 게이트를 모두 통과해야 배정한다.

| 분기 | 환자 | 미처치 | 전체 대기 합(환자·분) | 요청→처치 제한 합(분) | 문의 |
|---|---:|---:|---:|---:|---:|
| v4_H1_baseline | 990 | 60 | 9133.00 | 13041.50 | 2666 |
| v4_H1_safe | 990 | 60 | 9114.50 | 12993.00 | 2672 |
| v4_H1_probe | 990 | 60 | 9059.00 | 12949.50 | 2684 |
| v4_C1_baseline | 990 | 60 | 9133.00 | 13041.50 | 2666 |
| v4_C1_safe | 990 | 60 | 9114.50 | 12993.00 | 2672 |
| v4_C1_probe | 990 | 60 | 9059.00 | 12949.50 | 2684 |
| fixed3_H1_baseline | 990 | 54 | 8515.00 | 12692.50 | 2425 |
| fixed3_C1_baseline | 990 | 54 | 8515.00 | 12692.50 | 2425 |

| 전후 비교 | 처치 단축 | 동일 | 악화 | 최대 악화(분) | 대기 증가 환경 |
|---|---:|---:|---:|---:|---:|---:|
| probe_H1 | 42 | 936 | 12 | 2.00 | 6 |
| combined_H1 | 66 | 906 | 18 | 16.00 | 7 |
| previous_safe_H1 | 30 | 954 | 6 | 16.00 | 7 |
| probe_C1 | 42 | 936 | 12 | 2.00 | 6 |
| combined_C1 | 66 | 906 | 18 | 16.00 | 7 |
| previous_safe_C1 | 30 | 954 | 6 | 16.00 | 7 |
| fixed3_reference_H1 | 107 | 620 | 263 | 56.50 | 154 |
| fixed3_reference_C1 | 107 | 620 | 263 | 56.50 | 154 |

개발에서 고정한 선택:

```json
{
  "H1": {
    "policy": "v4_H1_baseline",
    "candidate": "protected_v4_serial_registration",
    "checks": {
      "vs_previous_safe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "vs_protected": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      }
    },
    "gates": {
      "vs_previous_safe": false,
      "vs_protected_population": false
    }
  },
  "C1": {
    "policy": "v4_C1_baseline",
    "candidate": "protected_v4_serial_registration",
    "checks": {
      "vs_previous_safe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "vs_protected": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      }
    },
    "gates": {
      "vs_previous_safe": false,
      "vs_protected_population": false
    }
  }
}
```

validation_checks는 검증의 진단 결과다. 실제 선택은 개발 잠금값이며 검증으로 재선택하지 않는다.
환자별 지연과 환경별 대기 증가를 모두 공개한다.
대기는 이동을 제외한 탐색·병원 대기·전원 준비 합이다. 요청→처치는 미처치를 동일 관찰 종료로 제한한다.
직전 후보에 대한 새 변화와 기존 보호 기준의 환자 간 절충을 분리해 공개한다.
