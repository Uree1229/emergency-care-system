# 실제 회복 증거에 따른 정상 문의 순위 개선

합성 실험. 441xx는 이미 관측한 개발 입력이며 저장 참조를 재사용했다. 471xx만 새 검증이다.
고정 3곳은 회차 10회 한도가 없는 전체 스택 참고 기준이다. 기본 fixed_3는 변경하지 않았다.

환경 387, 새 실행/새 완전 원문 감사 1836, 저장 참조 증명 재사용 4356

| 분기 | 환경 | 환자 | 미처치 | 순대기 합 | 제한 처치 합 | 실제 문의 |
|---|---:|---:|---:|---:|---:|---:|
| v4_H1_baseline | 387 | 717 | 99 | 9380.00 | 11951.75 | 3963 |
| v4_H1_safe | 387 | 717 | 99 | 9367.25 | 11924.00 | 3966 |
| v4_H1_probe | 387 | 717 | 90 | 8990.25 | 11547.00 | 3773 |
| v4_C1_baseline | 387 | 717 | 99 | 9380.00 | 11951.75 | 3963 |
| v4_C1_safe | 387 | 717 | 99 | 9367.25 | 11924.00 | 3966 |
| v4_C1_probe | 387 | 717 | 90 | 8990.25 | 11547.00 | 3773 |
| fixed3_H1_baseline | 387 | 717 | 84 | 8504.25 | 11357.50 | 3468 |
| fixed3_C1_baseline | 387 | 717 | 84 | 8504.25 | 11357.50 | 3468 |
| retry_H1_frontier | 387 | 717 | 84 | 8745.50 | 11428.25 | 3405 |
| retry_C1_frontier | 387 | 717 | 84 | 8745.50 | 11428.25 | 3405 |
| recovery_H1_latched | 387 | 717 | 87 | 8893.00 | 11566.75 | 3378 |
| recovery_C1_latched | 387 | 717 | 87 | 8893.00 | 11566.75 | 3378 |
| rank_H1_normal | 387 | 717 | 84 | 8732.50 | 11409.25 | 3396 |
| rank_H1_latched | 387 | 717 | 87 | 8848.75 | 11516.50 | 3453 |
| rank_C1_normal | 387 | 717 | 84 | 8732.50 | 11409.25 | 3396 |
| rank_C1_latched | 387 | 717 | 87 | 8848.75 | 11516.50 | 3453 |

| 비교 | 처치 지표 개선 | 지표 악화 | 성공 상실 | 새 성공 | 최대 제한 악화 | 순대기 변화 | 대기 증가 환경 | 문의 변화 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| normal_current_H1 | 6 | 0 | 0 | 0 | 0.00 | -13.00 | 0 | -9 |
| normal_safe_H1 | 60 | 22 | 3 | 18 | 48.50 | -634.75 | 19 | -570 |
| normal_prototype_H1 | 32 | 16 | 0 | 6 | 3.25 | -257.75 | 19 | -377 |
| normal_protected_H1 | 72 | 25 | 3 | 18 | 48.50 | -647.50 | 19 | -567 |
| normal_fixed3_reference_H1 | 119 | 193 | 12 | 12 | 56.50 | +228.25 | 140 | -72 |
| normal_previous_latched_H1 | 12 | 15 | 0 | 3 | 1.50 | -160.50 | 9 | +18 |
| latched_current_H1 | 21 | 9 | 3 | 0 | 38.50 | +103.25 | 6 | +48 |
| latched_safe_H1 | 57 | 22 | 3 | 15 | 48.50 | -518.50 | 22 | -513 |
| latched_prototype_H1 | 50 | 25 | 3 | 6 | 38.50 | -141.50 | 25 | -320 |
| latched_protected_H1 | 69 | 25 | 3 | 15 | 48.50 | -531.25 | 22 | -510 |
| latched_fixed3_reference_H1 | 122 | 193 | 15 | 12 | 56.50 | +344.50 | 140 | -15 |
| latched_previous_latched_H1 | 12 | 6 | 3 | 3 | 38.50 | -44.25 | 3 | +75 |
| normal_current_C1 | 6 | 0 | 0 | 0 | 0.00 | -13.00 | 0 | -9 |
| normal_safe_C1 | 60 | 22 | 3 | 18 | 48.50 | -634.75 | 19 | -570 |
| normal_prototype_C1 | 32 | 16 | 0 | 6 | 3.25 | -257.75 | 19 | -377 |
| normal_protected_C1 | 72 | 25 | 3 | 18 | 48.50 | -647.50 | 19 | -567 |
| normal_fixed3_reference_C1 | 119 | 193 | 12 | 12 | 56.50 | +228.25 | 140 | -72 |
| normal_previous_latched_C1 | 12 | 15 | 0 | 3 | 1.50 | -160.50 | 9 | +18 |
| latched_current_C1 | 21 | 9 | 3 | 0 | 38.50 | +103.25 | 6 | +48 |
| latched_safe_C1 | 57 | 22 | 3 | 15 | 48.50 | -518.50 | 22 | -513 |
| latched_prototype_C1 | 50 | 25 | 3 | 6 | 38.50 | -141.50 | 25 | -320 |
| latched_protected_C1 | 69 | 25 | 3 | 15 | 48.50 | -531.25 | 22 | -510 |
| latched_fixed3_reference_C1 | 122 | 193 | 15 | 12 | 56.50 | +344.50 | 140 | -15 |
| latched_previous_latched_C1 | 12 | 6 | 3 | 3 | 38.50 | -44.25 | 3 | +75 |

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
  "validation": null
}
```

순대기는 이동을 제외한 탐색·병원·전원 준비 대기다. 미처치는 동일 관찰 종료로 제한한다.
개인 순대기는 별도 진단하며 개인 임상 도착·처치와 환경별 순대기 gate를 구분한다.
cached CPU와 새 CPU는 다른 실행 시점이다. 50ms fallback을 재실행·제외하지 않고 숨기지 않는다.
