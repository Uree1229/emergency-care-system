# 실제 회복 증거에 따른 정상 문의 순위 개선

합성 실험. 441xx는 이미 관측한 개발 입력이며 저장 참조를 재사용했다. 471xx만 새 검증이다.
고정 3곳은 회차 10회 한도가 없는 전체 스택 참고 기준이다. 기본 fixed_3는 변경하지 않았다.

환경 774, 새 실행/새 완전 원문 감사 10836, 저장 참조 증명 재사용 0

| 분기 | 환경 | 환자 | 미처치 | 순대기 합 | 제한 처치 합 | 실제 문의 |
|---|---:|---:|---:|---:|---:|---:|
| v4_H1_baseline | 774 | 1434 | 198 | 18641.00 | 23828.50 | 7910 |
| v4_H1_safe | 774 | 1434 | 198 | 18624.50 | 23782.00 | 7916 |
| v4_H1_probe | 774 | 1434 | 180 | 17861.50 | 23018.00 | 7523 |
| v4_C1_baseline | 774 | 1434 | 198 | 18641.00 | 23828.50 | 7910 |
| v4_C1_safe | 774 | 1434 | 198 | 18624.50 | 23782.00 | 7916 |
| v4_C1_probe | 774 | 1434 | 180 | 17861.50 | 23018.00 | 7523 |
| fixed3_H1_baseline | 774 | 1434 | 168 | 16907.75 | 22581.25 | 6941 |
| fixed3_C1_baseline | 774 | 1434 | 168 | 16935.75 | 22603.25 | 6942 |
| retry_H1_frontier | 774 | 1434 | 168 | 17377.50 | 22786.00 | 6790 |
| retry_C1_frontier | 774 | 1434 | 168 | 17377.50 | 22786.00 | 6790 |
| recovery_H1_latched | 774 | 1434 | 174 | 17673.00 | 23063.50 | 6736 |
| recovery_C1_latched | 774 | 1434 | 174 | 17673.00 | 23063.50 | 6736 |
| rank_H1_normal | 774 | 1434 | 168 | 17352.00 | 22748.50 | 6772 |
| rank_C1_normal | 774 | 1434 | 168 | 17352.00 | 22748.50 | 6772 |

| 비교 | 처치 지표 개선 | 지표 악화 | 성공 상실 | 새 성공 | 최대 제한 악화 | 순대기 변화 | 대기 증가 환경 | 문의 변화 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| normal_current_H1 | 12 | 0 | 0 | 0 | 0.00 | -25.50 | 0 | -18 |
| normal_safe_H1 | 121 | 43 | 6 | 36 | 48.50 | -1272.50 | 37 | -1144 |
| normal_prototype_H1 | 61 | 35 | 0 | 12 | 3.25 | -509.50 | 41 | -751 |
| normal_protected_H1 | 145 | 49 | 6 | 36 | 48.50 | -1289.00 | 38 | -1138 |
| normal_fixed3_reference_H1 | 232 | 388 | 24 | 24 | 56.50 | +444.25 | 278 | -169 |
| normal_previous_latched_H1 | 24 | 30 | 0 | 6 | 1.50 | -321.00 | 18 | +36 |
| normal_current_C1 | 12 | 0 | 0 | 0 | 0.00 | -25.50 | 0 | -18 |
| normal_safe_C1 | 121 | 43 | 6 | 36 | 48.50 | -1272.50 | 37 | -1144 |
| normal_prototype_C1 | 61 | 35 | 0 | 12 | 3.25 | -509.50 | 41 | -751 |
| normal_protected_C1 | 145 | 49 | 6 | 36 | 48.50 | -1289.00 | 38 | -1138 |
| normal_fixed3_reference_C1 | 233 | 388 | 24 | 24 | 56.50 | +416.25 | 277 | -170 |
| normal_previous_latched_C1 | 24 | 30 | 0 | 6 | 1.50 | -321.00 | 18 | +36 |

개발 고정 연구 선택과 검증 진단:

```json
{
  "selected": {
    "H1": {
      "variant": "normal",
      "policy": "rank_H1_normal",
      "primary_checks": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true,
        "no_case_inquiry_increase": true,
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
        "vs_current": true,
        "vs_safe": false,
        "vs_prototype_population": false,
        "vs_protected_population": false
      },
      "primary_passed": true,
      "deployment_eligible": false,
      "ranking": [
        false,
        0,
        0,
        0,
        11409.25,
        8732.5,
        3396,
        0
      ],
      "all_candidates": [
        {
          "variant": "normal",
          "policy": "rank_H1_normal",
          "primary_checks": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": true,
            "no_individual_final_arrival_worsening": true,
            "no_case_total_wait_increase": true,
            "no_case_capped_treatment_increase": true,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true,
            "no_case_inquiry_increase": true,
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
            "vs_current": true,
            "vs_safe": false,
            "vs_prototype_population": false,
            "vs_protected_population": false
          },
          "primary_passed": true,
          "deployment_eligible": false,
          "ranking": [
            false,
            0,
            0,
            0,
            11409.25,
            8732.5,
            3396,
            0
          ]
        },
        {
          "variant": "latched",
          "policy": "rank_H1_latched",
          "primary_checks": {
            "no_lost_treatments": false,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": false,
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
              "no_lost_treatments": false,
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
            3,
            18,
            6,
            11516.5,
            8848.75,
            3453,
            1
          ]
        }
      ],
      "selection_scope": "development research candidate only",
      "live_default": "fixed_3",
      "adopted": false
    },
    "C1": {
      "variant": "normal",
      "policy": "rank_C1_normal",
      "primary_checks": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true,
        "no_case_inquiry_increase": true,
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
        "vs_current": true,
        "vs_safe": false,
        "vs_prototype_population": false,
        "vs_protected_population": false
      },
      "primary_passed": true,
      "deployment_eligible": false,
      "ranking": [
        false,
        0,
        0,
        0,
        11409.25,
        8732.5,
        3396,
        0
      ],
      "all_candidates": [
        {
          "variant": "normal",
          "policy": "rank_C1_normal",
          "primary_checks": {
            "no_lost_treatments": true,
            "no_individual_treatment_worsening": true,
            "no_individual_final_arrival_worsening": true,
            "no_case_total_wait_increase": true,
            "no_case_capped_treatment_increase": true,
            "bounded_inquiry_cost": true,
            "strict_total_wait_reduction": true,
            "no_case_inquiry_increase": true,
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
            "vs_current": true,
            "vs_safe": false,
            "vs_prototype_population": false,
            "vs_protected_population": false
          },
          "primary_passed": true,
          "deployment_eligible": false,
          "ranking": [
            false,
            0,
            0,
            0,
            11409.25,
            8732.5,
            3396,
            0
          ]
        },
        {
          "variant": "latched",
          "policy": "rank_C1_latched",
          "primary_checks": {
            "no_lost_treatments": false,
            "no_individual_treatment_worsening": false,
            "no_individual_final_arrival_worsening": false,
            "no_case_total_wait_increase": false,
            "no_case_capped_treatment_increase": false,
            "bounded_inquiry_cost": false,
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
              "no_lost_treatments": false,
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
            3,
            18,
            6,
            11516.5,
            8848.75,
            3453,
            1
          ]
        }
      ],
      "selection_scope": "development research candidate only",
      "live_default": "fixed_3",
      "adopted": false
    }
  },
  "validation": {
    "H1": {
      "variant": "normal",
      "policy": "rank_H1_normal",
      "primary_checks": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true,
        "no_case_inquiry_increase": true,
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
        "vs_current": true,
        "vs_safe": false,
        "vs_prototype_population": false,
        "vs_protected_population": false
      },
      "primary_passed": true,
      "deployment_eligible": false,
      "ranking": [
        false,
        0,
        0,
        0,
        22748.5,
        17352.0,
        6772,
        0
      ]
    },
    "C1": {
      "variant": "normal",
      "policy": "rank_C1_normal",
      "primary_checks": {
        "no_lost_treatments": true,
        "no_individual_treatment_worsening": true,
        "no_individual_final_arrival_worsening": true,
        "no_case_total_wait_increase": true,
        "no_case_capped_treatment_increase": true,
        "bounded_inquiry_cost": true,
        "strict_total_wait_reduction": true,
        "no_case_inquiry_increase": true,
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
        "vs_current": true,
        "vs_safe": false,
        "vs_prototype_population": false,
        "vs_protected_population": false
      },
      "primary_passed": true,
      "deployment_eligible": false,
      "ranking": [
        false,
        0,
        0,
        0,
        22748.5,
        17352.0,
        6772,
        0
      ]
    }
  }
}
```

순대기는 이동을 제외한 탐색·병원·전원 준비 대기다. 미처치는 동일 관찰 종료로 제한한다.
개인 순대기는 별도 진단하며 개인 임상 도착·처치와 환경별 순대기 gate를 구분한다.
cached CPU와 새 CPU는 다른 실행 시점이다. 50ms fallback을 재실행·제외하지 않고 숨기지 않는다.
