# 재문의 순회·대기·관측 부하 배분 실험

합성 development · 327환경 · 5886분기

동일 foundation의 직전 capability가 주 비교군이다. 고정3곳은 원래 회차10회 한도가 없는 참고 기준이다.
과거 VAL은 회귀 참고이며, 새 VAL로 정책이나 임계값을 선택하지 않는다. 연구 선택과 운영 반영은 별개다.

| 분기 | 환자 | 관찰시간 내 미처치 | 전체 대기 합 | 요청→처치 제한 합 | 문의 | 중복 접촉 |
|---|---:|---:|---:|---:|---:|---:|
| v4_H1_baseline | 648 | 66 | 7194.75 | 9585.50 | 2817 | 1486 |
| v4_H1_safe | 648 | 66 | 7182.00 | 9557.75 | 2820 | 1486 |
| v4_H1_probe | 648 | 66 | 7148.25 | 9548.00 | 2829 | 1489 |
| v4_C1_baseline | 648 | 66 | 7194.75 | 9585.50 | 2817 | 1486 |
| v4_C1_safe | 648 | 66 | 7182.00 | 9557.75 | 2820 | 1486 |
| v4_C1_probe | 648 | 66 | 7148.25 | 9548.00 | 2829 | 1489 |
| fixed3_H1_baseline | 648 | 63 | 6843.75 | 9525.00 | 2702 | 1433 |
| fixed3_C1_baseline | 648 | 63 | 6843.75 | 9525.00 | 2702 | 1433 |
| v4_H1_cooperative | 648 | 66 | 7157.25 | 9533.00 | 2841 | 1504 |
| v4_C1_cooperative | 648 | 66 | 7157.25 | 9533.00 | 2841 | 1504 |
| v4_H1_capability | 648 | 66 | 7145.25 | 9521.00 | 2844 | 1504 |
| v4_C1_capability | 648 | 66 | 7145.25 | 9521.00 | 2844 | 1504 |
| retry_H1_frontier | 648 | 60 | 6877.50 | 9397.25 | 2628 | 1270 |
| retry_H1_paced | 648 | 60 | 6928.00 | 9447.75 | 2392 | 1034 |
| retry_H1_balanced | 648 | 57 | 6686.50 | 9260.25 | 2267 | 947 |
| retry_C1_frontier | 648 | 60 | 6877.50 | 9397.25 | 2628 | 1270 |
| retry_C1_paced | 648 | 60 | 6928.00 | 9447.75 | 2392 | 1034 |
| retry_C1_balanced | 648 | 57 | 6686.50 | 9260.25 | 2267 | 947 |

| 비교 | 단축 | 동일 | 지연 | 최대 지연 | 기존 성공 상실 | 새 성공 | 대기 변화 | 대기 증가 환경 | 문의 변화 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capability_H1 | 39 | 606 | 3 | 1.00 | 0 | 0 | -36.75 | 3 | +24 |
| fallback_H1 | 6 | 642 | 0 | 0.00 | 0 | 0 | -12.00 | 0 | +3 |
| vs_probe_H1 | 24 | 615 | 9 | 1.00 | 0 | 0 | -3.00 | 12 | +15 |
| combined_H1 | 51 | 591 | 6 | 17.00 | 0 | 0 | -49.50 | 3 | +27 |
| previous_cooperative_H1 | 33 | 612 | 3 | 1.00 | 0 | 0 | -24.75 | 3 | +21 |
| previous_probe_H1 | 36 | 585 | 27 | 2.00 | 0 | 0 | -33.75 | 12 | +9 |
| previous_safe_H1 | 15 | 630 | 3 | 17.00 | 0 | 0 | -12.75 | 3 | +3 |
| capability_C1 | 39 | 606 | 3 | 1.00 | 0 | 0 | -36.75 | 3 | +24 |
| fallback_C1 | 6 | 642 | 0 | 0.00 | 0 | 0 | -12.00 | 0 | +3 |
| vs_probe_C1 | 24 | 615 | 9 | 1.00 | 0 | 0 | -3.00 | 12 | +15 |
| combined_C1 | 51 | 591 | 6 | 17.00 | 0 | 0 | -49.50 | 3 | +27 |
| previous_cooperative_C1 | 33 | 612 | 3 | 1.00 | 0 | 0 | -24.75 | 3 | +21 |
| previous_probe_C1 | 36 | 585 | 27 | 2.00 | 0 | 0 | -33.75 | 12 | +9 |
| previous_safe_C1 | 15 | 630 | 3 | 17.00 | 0 | 0 | -12.75 | 3 | +3 |
| fixed3_reference_H1 | 108 | 378 | 162 | 56.50 | 15 | 12 | +301.50 | 112 | +142 |
| fixed3_reference_C1 | 108 | 378 | 162 | 56.50 | 15 | 12 | +301.50 | 112 | +142 |
| frontier_current_H1 | 6 | 642 | 0 | 0.00 | 0 | 6 | -267.75 | 0 | -216 |
| frontier_safe_H1 | 45 | 600 | 3 | 1.00 | 0 | 6 | -304.50 | 3 | -192 |
| frontier_cooperative_H1 | 12 | 636 | 0 | 0.00 | 0 | 6 | -279.75 | 0 | -213 |
| frontier_prototype_H1 | 30 | 609 | 9 | 1.00 | 0 | 6 | -270.75 | 12 | -201 |
| frontier_protected_H1 | 57 | 585 | 6 | 17.00 | 0 | 6 | -317.25 | 3 | -189 |
| frontier_fixed3_reference_H1 | 108 | 378 | 162 | 56.50 | 9 | 12 | +33.75 | 112 | -74 |
| paced_current_H1 | 6 | 619 | 23 | 5.00 | 0 | 6 | -217.25 | 23 | -452 |
| paced_safe_H1 | 45 | 577 | 26 | 5.00 | 0 | 6 | -254.00 | 26 | -428 |
| paced_cooperative_H1 | 12 | 613 | 23 | 5.00 | 0 | 6 | -229.25 | 23 | -449 |
| paced_prototype_H1 | 30 | 586 | 32 | 5.00 | 0 | 6 | -220.25 | 35 | -437 |
| paced_protected_H1 | 57 | 562 | 29 | 17.00 | 0 | 6 | -266.75 | 26 | -425 |
| paced_fixed3_reference_H1 | 108 | 366 | 174 | 56.50 | 9 | 12 | +84.25 | 124 | -310 |
| balanced_current_H1 | 43 | 555 | 50 | 49.25 | 3 | 12 | -458.75 | 30 | -577 |
| balanced_safe_H1 | 82 | 513 | 53 | 49.25 | 3 | 12 | -495.50 | 33 | -553 |
| balanced_cooperative_H1 | 49 | 549 | 50 | 49.25 | 3 | 12 | -470.75 | 30 | -574 |
| balanced_prototype_H1 | 67 | 522 | 59 | 49.25 | 3 | 12 | -461.75 | 42 | -562 |
| balanced_protected_H1 | 94 | 498 | 56 | 49.25 | 3 | 12 | -508.25 | 33 | -550 |
| balanced_fixed3_reference_H1 | 116 | 356 | 176 | 56.50 | 6 | 12 | -157.25 | 126 | -435 |
| frontier_current_C1 | 6 | 642 | 0 | 0.00 | 0 | 6 | -267.75 | 0 | -216 |
| frontier_safe_C1 | 45 | 600 | 3 | 1.00 | 0 | 6 | -304.50 | 3 | -192 |
| frontier_cooperative_C1 | 12 | 636 | 0 | 0.00 | 0 | 6 | -279.75 | 0 | -213 |
| frontier_prototype_C1 | 30 | 609 | 9 | 1.00 | 0 | 6 | -270.75 | 12 | -201 |
| frontier_protected_C1 | 57 | 585 | 6 | 17.00 | 0 | 6 | -317.25 | 3 | -189 |
| frontier_fixed3_reference_C1 | 108 | 378 | 162 | 56.50 | 9 | 12 | +33.75 | 112 | -74 |
| paced_current_C1 | 6 | 619 | 23 | 5.00 | 0 | 6 | -217.25 | 23 | -452 |
| paced_safe_C1 | 45 | 577 | 26 | 5.00 | 0 | 6 | -254.00 | 26 | -428 |
| paced_cooperative_C1 | 12 | 613 | 23 | 5.00 | 0 | 6 | -229.25 | 23 | -449 |
| paced_prototype_C1 | 30 | 586 | 32 | 5.00 | 0 | 6 | -220.25 | 35 | -437 |
| paced_protected_C1 | 57 | 562 | 29 | 17.00 | 0 | 6 | -266.75 | 26 | -425 |
| paced_fixed3_reference_C1 | 108 | 366 | 174 | 56.50 | 9 | 12 | +84.25 | 124 | -310 |
| balanced_current_C1 | 43 | 555 | 50 | 49.25 | 3 | 12 | -458.75 | 30 | -577 |
| balanced_safe_C1 | 82 | 513 | 53 | 49.25 | 3 | 12 | -495.50 | 33 | -553 |
| balanced_cooperative_C1 | 49 | 549 | 50 | 49.25 | 3 | 12 | -470.75 | 30 | -574 |
| balanced_prototype_C1 | 67 | 522 | 59 | 49.25 | 3 | 12 | -461.75 | 42 | -562 |
| balanced_protected_C1 | 94 | 498 | 56 | 49.25 | 3 | 12 | -508.25 | 33 | -550 |
| balanced_fixed3_reference_C1 | 116 | 356 | 176 | 56.50 | 6 | 12 | -157.25 | 126 | -435 |

개발에서 고정한 연구 선택:

```json
{
  "H1": {
    "variant": "frontier",
    "policy": "retry_H1_frontier",
    "primary_checks": {
      "no_lost_treatments": true,
      "no_individual_treatment_worsening": true,
      "no_individual_final_arrival_worsening": true,
      "no_case_total_wait_increase": true,
      "no_case_capped_treatment_increase": true,
      "bounded_inquiry_cost": true,
      "strict_total_wait_reduction": true,
      "no_case_inquiry_increase": true,
      "strict_total_inquiry_reduction": true
    },
    "inherited_checks": {
      "safe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "cooperative": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "probe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "baseline": {
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
      "vs_current": true,
      "vs_previous_safe": false,
      "vs_previous_cooperative": true,
      "vs_previous_probe_population": false,
      "vs_protected_population": false
    },
    "primary_passed": true,
    "deployment_eligible": false,
    "ranking": [
      false,
      0,
      0,
      0,
      6877.5,
      9397.25,
      2628,
      0
    ],
    "selection_scope": "development research choice only",
    "all_candidates": [
      {
        "variant": "frontier",
        "policy": "retry_H1_frontier",
        "primary_checks": {
          "no_lost_treatments": true,
          "no_individual_treatment_worsening": true,
          "no_individual_final_arrival_worsening": true,
          "no_case_total_wait_increase": true,
          "no_case_capped_treatment_increase": true,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true,
          "no_case_inquiry_increase": true,
          "strict_total_inquiry_reduction": true
        },
        "inherited_checks": {
          "safe": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "cooperative": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": true,
            "no_individual_final_arrival_worsening": true,
            "no_case_total_wait_increase": true,
            "no_case_capped_treatment_increase": true,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "probe": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": true,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "baseline": {
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
          "vs_current": true,
          "vs_previous_safe": false,
          "vs_previous_cooperative": true,
          "vs_previous_probe_population": false,
          "vs_protected_population": false
        },
        "primary_passed": true,
        "deployment_eligible": false,
        "ranking": [
          false,
          0,
          0,
          0,
          6877.5,
          9397.25,
          2628,
          0
        ]
      },
      {
        "variant": "paced",
        "policy": "retry_H1_paced",
        "primary_checks": {
          "no_lost_treatments": true,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true,
          "no_case_inquiry_increase": true,
          "strict_total_inquiry_reduction": true
        },
        "inherited_checks": {
          "safe": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "cooperative": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "probe": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "baseline": {
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
          "vs_current": false,
          "vs_previous_safe": false,
          "vs_previous_cooperative": false,
          "vs_previous_probe_population": false,
          "vs_protected_population": false
        },
        "primary_passed": false,
        "deployment_eligible": false,
        "ranking": [
          true,
          0,
          23,
          23,
          6928.0,
          9447.75,
          2392,
          1
        ]
      },
      {
        "variant": "balanced",
        "policy": "retry_H1_balanced",
        "primary_checks": {
          "no_lost_treatments": false,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true,
          "no_case_inquiry_increase": true,
          "strict_total_inquiry_reduction": true
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
          "cooperative": {
            "no_lost_treatments": false,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "probe": {
            "no_lost_treatments": false,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "baseline": {
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
          "vs_previous_safe": false,
          "vs_previous_cooperative": false,
          "vs_previous_probe_population": false,
          "vs_protected_population": false
        },
        "primary_passed": false,
        "deployment_eligible": false,
        "ranking": [
          true,
          3,
          50,
          30,
          6686.5,
          9260.25,
          2267,
          2
        ]
      }
    ],
    "live_default": "fixed_3",
    "adopted": false
  },
  "C1": {
    "variant": "frontier",
    "policy": "retry_C1_frontier",
    "primary_checks": {
      "no_lost_treatments": true,
      "no_individual_treatment_worsening": true,
      "no_individual_final_arrival_worsening": true,
      "no_case_total_wait_increase": true,
      "no_case_capped_treatment_increase": true,
      "bounded_inquiry_cost": true,
      "strict_total_wait_reduction": true,
      "no_case_inquiry_increase": true,
      "strict_total_inquiry_reduction": true
    },
    "inherited_checks": {
      "safe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": false,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "cooperative": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "probe": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": false,
        "no_individual_final_arrival_worsening": false,
        "no_case_total_wait_increase": false,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true
      },
      "baseline": {
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
      "vs_current": true,
      "vs_previous_safe": false,
      "vs_previous_cooperative": true,
      "vs_previous_probe_population": false,
      "vs_protected_population": false
    },
    "primary_passed": true,
    "deployment_eligible": false,
    "ranking": [
      false,
      0,
      0,
      0,
      6877.5,
      9397.25,
      2628,
      0
    ],
    "selection_scope": "development research choice only",
    "all_candidates": [
      {
        "variant": "frontier",
        "policy": "retry_C1_frontier",
        "primary_checks": {
          "no_lost_treatments": true,
          "no_individual_treatment_worsening": true,
          "no_individual_final_arrival_worsening": true,
          "no_case_total_wait_increase": true,
          "no_case_capped_treatment_increase": true,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true,
          "no_case_inquiry_increase": true,
          "strict_total_inquiry_reduction": true
        },
        "inherited_checks": {
          "safe": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "cooperative": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": true,
            "no_individual_final_arrival_worsening": true,
            "no_case_total_wait_increase": true,
            "no_case_capped_treatment_increase": true,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "probe": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": true,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "baseline": {
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
          "vs_current": true,
          "vs_previous_safe": false,
          "vs_previous_cooperative": true,
          "vs_previous_probe_population": false,
          "vs_protected_population": false
        },
        "primary_passed": true,
        "deployment_eligible": false,
        "ranking": [
          false,
          0,
          0,
          0,
          6877.5,
          9397.25,
          2628,
          0
        ]
      },
      {
        "variant": "paced",
        "policy": "retry_C1_paced",
        "primary_checks": {
          "no_lost_treatments": true,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true,
          "no_case_inquiry_increase": true,
          "strict_total_inquiry_reduction": true
        },
        "inherited_checks": {
          "safe": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "cooperative": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "probe": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "baseline": {
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
          "vs_current": false,
          "vs_previous_safe": false,
          "vs_previous_cooperative": false,
          "vs_previous_probe_population": false,
          "vs_protected_population": false
        },
        "primary_passed": false,
        "deployment_eligible": false,
        "ranking": [
          true,
          0,
          23,
          23,
          6928.0,
          9447.75,
          2392,
          1
        ]
      },
      {
        "variant": "balanced",
        "policy": "retry_C1_balanced",
        "primary_checks": {
          "no_lost_treatments": false,
          "no_individual_treatment_worsening": false,
          "no_individual_final_arrival_worsening": false,
          "no_case_total_wait_increase": false,
          "no_case_capped_treatment_increase": false,
          "bounded_inquiry_cost": true,
          "strict_total_wait_reduction": true,
          "no_case_inquiry_increase": true,
          "strict_total_inquiry_reduction": true
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
          "cooperative": {
            "no_lost_treatments": false,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "probe": {
            "no_lost_treatments": false,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true
          },
          "baseline": {
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
          "vs_previous_safe": false,
          "vs_previous_cooperative": false,
          "vs_previous_probe_population": false,
          "vs_protected_population": false
        },
        "primary_passed": false,
        "deployment_eligible": false,
        "ranking": [
          true,
          3,
          50,
          30,
          6686.5,
          9260.25,
          2267,
          2
        ]
      }
    ],
    "live_default": "fixed_3",
    "adopted": false
  }
}
```

validation_diagnostics는 고정한 선택의 검증 결과이며 재선택이 아니다. 운영 기본 fixed_3는 변경하지 않았다.
대기는 이동을 제외한 탐색·병원 대기·전원 준비 합이다. 미처치는 같은 관찰 종료로 제한한다.
협력형 부모의 ACK 지연과 작업 전환 비용 0분 가정은 그대로 남아 있다. 실제 임상 효과·범용 최적성 주장이 아니다.
