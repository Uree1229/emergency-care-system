# 관측 회복을 기억하는 재문의 실험

합성 실험. 441xx는 이미 관측한 개발 입력이며 저장 참조를 재사용했다. 461xx만 새 검증이다.
고정 3곳은 회차 10회 한도가 없는 전체 스택 참고 기준이다. 기본 fixed_3는 변경하지 않았다.

환경 363, 새 실행/새 완전 원문 감사 1812, 저장 참조 증명 재사용 4578

| 분기 | 환경 | 환자 | 미처치 | 순대기 합 | 제한 처치 합 | 실제 문의 |
|---|---:|---:|---:|---:|---:|---:|
| v4_H1_baseline | 363 | 690 | 81 | 8273.50 | 10776.25 | 3378 |
| v4_H1_safe | 363 | 690 | 81 | 8260.75 | 10748.50 | 3381 |
| v4_H1_probe | 363 | 690 | 81 | 8271.00 | 10773.75 | 3452 |
| v4_C1_baseline | 363 | 690 | 81 | 8273.50 | 10776.25 | 3378 |
| v4_C1_safe | 363 | 690 | 81 | 8260.75 | 10748.50 | 3381 |
| v4_C1_probe | 363 | 690 | 81 | 8271.00 | 10773.75 | 3452 |
| fixed3_H1_baseline | 363 | 690 | 75 | 7789.50 | 10582.75 | 3174 |
| fixed3_C1_baseline | 363 | 690 | 75 | 7789.50 | 10582.75 | 3174 |
| v4_H1_cooperative | 327 | 648 | 66 | 7157.25 | 9533.00 | 2841 |
| v4_C1_cooperative | 327 | 648 | 66 | 7157.25 | 9533.00 | 2841 |
| v4_H1_capability | 327 | 648 | 66 | 7145.25 | 9521.00 | 2844 |
| v4_C1_capability | 327 | 648 | 66 | 7145.25 | 9521.00 | 2844 |
| retry_H1_frontier | 363 | 690 | 75 | 8003.50 | 10626.25 | 3162 |
| retry_C1_frontier | 363 | 690 | 75 | 8003.50 | 10626.25 | 3162 |
| recovery_H1_eager | 363 | 690 | 75 | 7997.50 | 10620.25 | 3153 |
| recovery_H1_latched | 363 | 690 | 75 | 8004.25 | 10627.00 | 3147 |
| recovery_C1_eager | 363 | 690 | 75 | 7997.50 | 10620.25 | 3153 |
| recovery_C1_latched | 363 | 690 | 75 | 8004.25 | 10627.00 | 3147 |

| 비교 | 처치 지표 개선 | 지표 악화 | 성공 상실 | 새 성공 | 최대 제한 악화 | 순대기 변화 | 대기 증가 환경 | 문의 변화 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| eager_current_H1 | 12 | 3 | 0 | 0 | 2.00 | -6.00 | 3 | -9 |
| eager_safe_H1 | 48 | 16 | 3 | 9 | 48.50 | -263.25 | 13 | -228 |
| eager_prototype_H1 | 44 | 16 | 0 | 6 | 2.00 | -273.50 | 19 | -299 |
| eager_protected_H1 | 60 | 19 | 3 | 9 | 48.50 | -276.00 | 13 | -225 |
| eager_fixed3_reference_H1 | 113 | 184 | 12 | 12 | 56.50 | +208.00 | 131 | -21 |
| latched_current_H1 | 12 | 3 | 0 | 0 | 1.25 | +0.75 | 3 | -15 |
| latched_safe_H1 | 48 | 19 | 3 | 9 | 48.50 | -256.50 | 16 | -234 |
| latched_prototype_H1 | 44 | 16 | 0 | 6 | 1.50 | -266.75 | 19 | -305 |
| latched_protected_H1 | 60 | 22 | 3 | 9 | 48.50 | -269.25 | 16 | -231 |
| latched_fixed3_reference_H1 | 113 | 184 | 12 | 12 | 56.50 | +214.75 | 131 | -27 |
| eager_current_C1 | 12 | 3 | 0 | 0 | 2.00 | -6.00 | 3 | -9 |
| eager_safe_C1 | 48 | 16 | 3 | 9 | 48.50 | -263.25 | 13 | -228 |
| eager_prototype_C1 | 44 | 16 | 0 | 6 | 2.00 | -273.50 | 19 | -299 |
| eager_protected_C1 | 60 | 19 | 3 | 9 | 48.50 | -276.00 | 13 | -225 |
| eager_fixed3_reference_C1 | 113 | 184 | 12 | 12 | 56.50 | +208.00 | 131 | -21 |
| latched_current_C1 | 12 | 3 | 0 | 0 | 1.25 | +0.75 | 3 | -15 |
| latched_safe_C1 | 48 | 19 | 3 | 9 | 48.50 | -256.50 | 16 | -234 |
| latched_prototype_C1 | 44 | 16 | 0 | 6 | 1.50 | -266.75 | 19 | -305 |
| latched_protected_C1 | 60 | 22 | 3 | 9 | 48.50 | -269.25 | 16 | -231 |
| latched_fixed3_reference_C1 | 113 | 184 | 12 | 12 | 56.50 | +214.75 | 131 | -27 |

개발 고정 연구 선택과 검증 진단:

```json
{
  "selected": {
    "H1": {
      "variant": "eager",
      "policy": "recovery_H1_eager",
      "primary_checks": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true,
        "no_case_inquiry_increase": false,
        "strict_capped_treatment_reduction": true
      },
      "inherited_checks": {
        "safe": {
          "no_lost_treatments": false,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true
        },
        "prototype": {
          "no_lost_treatments": true,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true
        },
        "protected": {
          "no_lost_treatments": false,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true
        }
      },
      "gates": {
        "vs_current": false,
        "vs_safe": false,
        "vs_prototype_population": false,
        "vs_protected_population": false
      },
      "primary_passed": false,
      "deployment_eligible": false,
      "ranking": [
        true,
        0,
        6,
        3,
        10620.25,
        7997.5,
        3153,
        0
      ],
      "all_candidates": [
        {
          "variant": "eager",
          "policy": "recovery_H1_eager",
          "primary_checks": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true,
            "no_case_inquiry_increase": false,
            "strict_capped_treatment_reduction": true
          },
          "inherited_checks": {
            "safe": {
              "no_lost_treatments": false,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            },
            "prototype": {
              "no_lost_treatments": true,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            },
            "protected": {
              "no_lost_treatments": false,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            }
          },
          "gates": {
            "vs_current": false,
            "vs_safe": false,
            "vs_prototype_population": false,
            "vs_protected_population": false
          },
          "primary_passed": false,
          "deployment_eligible": false,
          "ranking": [
            true,
            0,
            6,
            3,
            10620.25,
            7997.5,
            3153,
            0
          ]
        },
        {
          "variant": "latched",
          "policy": "recovery_H1_latched",
          "primary_checks": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": false,
            "no_case_inquiry_increase": false,
            "strict_capped_treatment_reduction": false
          },
          "inherited_checks": {
            "safe": {
              "no_lost_treatments": false,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            },
            "prototype": {
              "no_lost_treatments": true,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            },
            "protected": {
              "no_lost_treatments": false,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            }
          },
          "gates": {
            "vs_current": false,
            "vs_safe": false,
            "vs_prototype_population": false,
            "vs_protected_population": false
          },
          "primary_passed": false,
          "deployment_eligible": false,
          "ranking": [
            true,
            0,
            6,
            3,
            10627.0,
            8004.25,
            3147,
            1
          ]
        }
      ],
      "selection_scope": "development research candidate only",
      "live_default": "fixed_3",
      "adopted": false
    },
    "C1": {
      "variant": "eager",
      "policy": "recovery_C1_eager",
      "primary_checks": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true,
        "no_case_inquiry_increase": false,
        "strict_capped_treatment_reduction": true
      },
      "inherited_checks": {
        "safe": {
          "no_lost_treatments": false,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true
        },
        "prototype": {
          "no_lost_treatments": true,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true
        },
        "protected": {
          "no_lost_treatments": false,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true
        }
      },
      "gates": {
        "vs_current": false,
        "vs_safe": false,
        "vs_prototype_population": false,
        "vs_protected_population": false
      },
      "primary_passed": false,
      "deployment_eligible": false,
      "ranking": [
        true,
        0,
        6,
        3,
        10620.25,
        7997.5,
        3153,
        0
      ],
      "all_candidates": [
        {
          "variant": "eager",
          "policy": "recovery_C1_eager",
          "primary_checks": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true,
            "no_case_inquiry_increase": false,
            "strict_capped_treatment_reduction": true
          },
          "inherited_checks": {
            "safe": {
              "no_lost_treatments": false,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            },
            "prototype": {
              "no_lost_treatments": true,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            },
            "protected": {
              "no_lost_treatments": false,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            }
          },
          "gates": {
            "vs_current": false,
            "vs_safe": false,
            "vs_prototype_population": false,
            "vs_protected_population": false
          },
          "primary_passed": false,
          "deployment_eligible": false,
          "ranking": [
            true,
            0,
            6,
            3,
            10620.25,
            7997.5,
            3153,
            0
          ]
        },
        {
          "variant": "latched",
          "policy": "recovery_C1_latched",
          "primary_checks": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": false,
            "no_case_inquiry_increase": false,
            "strict_capped_treatment_reduction": false
          },
          "inherited_checks": {
            "safe": {
              "no_lost_treatments": false,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            },
            "prototype": {
              "no_lost_treatments": true,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            },
            "protected": {
              "no_lost_treatments": false,
              "no_individual_treatment_worsening": false,
              "no_individual_final_arrival_worsening": false,
              "no_case_total_wait_increase": false,
              "no_case_capped_treatment_increase": false,
              "bounded_inquiry_cost": true,
              "strict_total_wait_reduction": true
            }
          },
          "gates": {
            "vs_current": false,
            "vs_safe": false,
            "vs_prototype_population": false,
            "vs_protected_population": false
          },
          "primary_passed": false,
          "deployment_eligible": false,
          "ranking": [
            true,
            0,
            6,
            3,
            10627.0,
            8004.25,
            3147,
            1
          ]
        }
      ],
      "selection_scope": "development research candidate only",
      "live_default": "fixed_3",
      "adopted": false
    }
  },
  "validation": null
}
```

순대기는 이동을 제외한 탐색·병원·전원 준비 대기다. 미처치는 동일 관찰 종료로 제한한다.
개인 순대기는 별도 진단하며 개인 임상 도착·처치와 환경별 순대기 gate를 구분한다.
cached CPU와 새 CPU는 다른 실행 시점이다. 50ms fallback을 재실행·제외하지 않고 숨기지 않는다.
