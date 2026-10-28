# 재문의 순회·대기·관측 부하 배분 실험

합성 validation · 654환경 · 9156분기

동일 foundation의 직전 capability가 주 비교군이다. 고정3곳은 원래 회차10회 한도가 없는 참고 기준이다.
과거 VAL은 회귀 참고이며, 새 VAL로 정책이나 임계값을 선택하지 않는다. 연구 선택과 운영 반영은 별개다.

| 분기 | 환자 | 관찰시간 내 미처치 | 전체 대기 합 | 요청→처치 제한 합 | 문의 | 중복 접촉 |
|---|---:|---:|---:|---:|---:|---:|
| v4_H1_baseline | 1296 | 131 | 14168.75 | 19021.25 | 5565 | 2935 |
| v4_H1_safe | 1296 | 131 | 14147.25 | 18969.75 | 5571 | 2935 |
| v4_H1_probe | 1296 | 131 | 14079.75 | 18950.25 | 5589 | 2941 |
| v4_C1_baseline | 1296 | 131 | 14208.75 | 19056.25 | 5566 | 2936 |
| v4_C1_safe | 1296 | 131 | 14147.25 | 18969.75 | 5571 | 2935 |
| v4_C1_probe | 1296 | 131 | 14079.75 | 18950.25 | 5589 | 2941 |
| fixed3_H1_baseline | 1296 | 126 | 13575.25 | 18929.75 | 5427 | 2887 |
| fixed3_C1_baseline | 1296 | 126 | 13575.25 | 18929.75 | 5427 | 2887 |
| v4_H1_cooperative | 1296 | 131 | 14097.75 | 18920.25 | 5613 | 2971 |
| v4_C1_cooperative | 1296 | 131 | 14097.75 | 18920.25 | 5613 | 2971 |
| v4_H1_capability | 1296 | 131 | 14073.75 | 18896.25 | 5619 | 2971 |
| v4_C1_capability | 1296 | 131 | 14073.75 | 18896.25 | 5619 | 2971 |
| retry_H1_frontier | 1296 | 119 | 13538.25 | 18648.75 | 5187 | 2503 |
| retry_C1_frontier | 1296 | 119 | 13538.25 | 18648.75 | 5187 | 2503 |

| 비교 | 단축 | 동일 | 지연 | 최대 지연 | 기존 성공 상실 | 새 성공 | 대기 변화 | 대기 증가 환경 | 문의 변화 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capability_H1 | 78 | 1212 | 6 | 1.00 | 0 | 0 | -73.50 | 6 | +48 |
| fallback_H1 | 12 | 1284 | 0 | 0.00 | 0 | 0 | -24.00 | 0 | +6 |
| vs_probe_H1 | 48 | 1230 | 18 | 1.00 | 0 | 0 | -6.00 | 24 | +30 |
| combined_H1 | 102 | 1182 | 12 | 17.00 | 0 | 0 | -95.00 | 6 | +54 |
| previous_cooperative_H1 | 66 | 1224 | 6 | 1.00 | 0 | 0 | -49.50 | 6 | +42 |
| previous_probe_H1 | 72 | 1170 | 54 | 2.00 | 0 | 0 | -67.50 | 24 | +18 |
| previous_safe_H1 | 30 | 1260 | 6 | 17.00 | 0 | 0 | -21.50 | 6 | +6 |
| capability_C1 | 78 | 1212 | 6 | 1.00 | 0 | 0 | -73.50 | 6 | +48 |
| fallback_C1 | 12 | 1284 | 0 | 0.00 | 0 | 0 | -24.00 | 0 | +6 |
| vs_probe_C1 | 48 | 1230 | 18 | 1.00 | 0 | 0 | -6.00 | 24 | +30 |
| combined_C1 | 103 | 1180 | 13 | 17.00 | 0 | 0 | -135.00 | 6 | +53 |
| previous_cooperative_C1 | 66 | 1224 | 6 | 1.00 | 0 | 0 | -49.50 | 6 | +42 |
| previous_probe_C1 | 72 | 1170 | 54 | 2.00 | 0 | 0 | -67.50 | 24 | +18 |
| previous_safe_C1 | 31 | 1258 | 7 | 17.00 | 0 | 0 | -61.50 | 6 | +5 |
| fixed3_reference_H1 | 224 | 750 | 322 | 56.50 | 30 | 25 | +498.50 | 219 | +192 |
| fixed3_reference_C1 | 224 | 750 | 322 | 56.50 | 30 | 25 | +498.50 | 219 | +192 |
| frontier_current_H1 | 12 | 1284 | 0 | 0.00 | 0 | 12 | -535.50 | 0 | -432 |
| frontier_safe_H1 | 90 | 1200 | 6 | 1.00 | 0 | 12 | -609.00 | 6 | -384 |
| frontier_cooperative_H1 | 24 | 1272 | 0 | 0.00 | 0 | 12 | -559.50 | 0 | -426 |
| frontier_prototype_H1 | 60 | 1218 | 18 | 1.00 | 0 | 12 | -541.50 | 24 | -402 |
| frontier_protected_H1 | 114 | 1170 | 12 | 17.00 | 0 | 12 | -630.50 | 6 | -378 |
| frontier_fixed3_reference_H1 | 224 | 750 | 322 | 56.50 | 18 | 25 | -37.00 | 219 | -240 |
| frontier_current_C1 | 12 | 1284 | 0 | 0.00 | 0 | 12 | -535.50 | 0 | -432 |
| frontier_safe_C1 | 90 | 1200 | 6 | 1.00 | 0 | 12 | -609.00 | 6 | -384 |
| frontier_cooperative_C1 | 24 | 1272 | 0 | 0.00 | 0 | 12 | -559.50 | 0 | -426 |
| frontier_prototype_C1 | 60 | 1218 | 18 | 1.00 | 0 | 12 | -541.50 | 24 | -402 |
| frontier_protected_C1 | 115 | 1168 | 13 | 17.00 | 0 | 12 | -670.50 | 6 | -379 |
| frontier_fixed3_reference_C1 | 224 | 750 | 322 | 56.50 | 18 | 25 | -37.00 | 219 | -240 |

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
