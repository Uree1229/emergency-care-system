// Generated from verified existing records; no new policy execution.
globalThis.DEMO_CASEBOOK = {
  "schema_version": 1,
  "id": "demo-casebook-20261030-v1",
  "policies": [
    "R0",
    "R1",
    "H1",
    "W1"
  ],
  "generated_from": {
    "study_directory": "outputs/waiting_protection_20261030/study-v1",
    "study_report_sha256": "8ae84f394be70971ef5ee7da52c097685426ac5b798369d2fa542c22d309a232",
    "execution_lock_sha256": "9780253ef1a731a3c35df0679d8d4722e1daeda457a0bb72a326599e2fbcf8bf",
    "independent_receipt_sha256": "4531a00be5f9ae69dc2054c0004631cb008330da62d87dc88c5559b63b950769",
    "waiting_decision_sha256": "6278b53eb0b89b0e3ac11ad606df63c871ce2f932d4b2b58a1cf3ffeb340077c",
    "verified_raw_cases": 96,
    "verified_archived_sources": 59
  },
  "scope": {
    "recorded_replay_only": true,
    "new_policy_executions": 0,
    "synthetic": true,
    "algorithm_work_closed": true,
    "final_presentation_preparation": false,
    "operating_policy": "fixed_3 + CP-SAT severity_first (3초) 유지",
    "W1_adopted": false,
    "clinical_safety_validated": false,
    "real_hospital_requests": 0,
    "limitation": "기존 합성 개발·검증 기록의 시연 정리다. 새 실험·현장 효과·전체 온라인 최적성·최종 발표 준비 완료를 뜻하지 않는다.",
    "time_unit": "integer simulated minutes",
    "time_origin": "absolute scenario t; elapsed arrival is t - occur_t",
    "capped_time": "미도착은 관측 종료 - 발생시각으로 표시하며 실제 도착시간이 아니다."
  },
  "cases": [
    {
      "id": "h1-improvement",
      "title": "한 환자는77분 빨라지고 다른 환자는51분 늦어진 사례",
      "profile": "scarce_capacity",
      "profile_label": "자원이 부족한 동시 발생",
      "explanation": "같은 실험 가중치4 환자 중 현재 이동시간이 짧은 환자를 먼저 선택했다. P17의 도착은77분 빨라졌지만 P04는51분 늦어졌다.",
      "caveat": "특정 환자의 단축을 전체 개선으로 제시하지 않는다. 실험 가중치4는 임상적으로 검증한 중증도 등급이 아니다.",
      "phase": "development_known",
      "comparison": {
        "baseline": "R1",
        "candidate": "H1"
      },
      "preferred_comparison": "H1_minus_R1",
      "raw_path": "outputs/waiting_protection_20261030/study-v1/raw/021.json.gz",
      "raw_sha256": "a44c2d0e42f71015f90d6e1038ac26a51a1f04ec7c405a09f0132c3a51c296ea",
      "raw_body_sha256": "4ec9b94daec5a1c7f40c37c900ece710dec707c9e2925746bf99fd8a5ace8d15",
      "scenario_sha256": "6175b5237f66ab56109f728aa99ac6c72a20ff2ee3254f3b3ec826bb8985e132",
      "observation_end_t": 180,
      "patients": [
        {
          "id": "P17",
          "priority": 4,
          "occur_t": 0,
          "search_start_t": 6,
          "observation_end_t": 180,
          "endpoints": {
            "R0": {
              "selected_t": 83,
              "arrive_t": 92,
              "treatment_start_t": 92,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 92
            },
            "R1": {
              "selected_t": 83,
              "arrive_t": 92,
              "treatment_start_t": 92,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 92
            },
            "H1": {
              "selected_t": 9,
              "arrive_t": 15,
              "treatment_start_t": 15,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 15
            },
            "W1": {
              "selected_t": 9,
              "arrive_t": 15,
              "treatment_start_t": 15,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 15
            }
          },
          "comparisons": {
            "H1_minus_R1": {
              "delta_capped_minutes": -77,
              "actual_arrival_delta_minutes": -77,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_H1": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": 0,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R1": {
              "delta_capped_minutes": -77,
              "actual_arrival_delta_minutes": -77,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R0": {
              "delta_capped_minutes": -77,
              "actual_arrival_delta_minutes": -77,
              "success_lost": [],
              "success_gained": []
            }
          }
        },
        {
          "id": "P04",
          "priority": 4,
          "occur_t": 0,
          "search_start_t": 6,
          "observation_end_t": 180,
          "endpoints": {
            "R0": {
              "selected_t": 9,
              "arrive_t": 16,
              "treatment_start_t": 16,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 16
            },
            "R1": {
              "selected_t": 9,
              "arrive_t": 16,
              "treatment_start_t": 16,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 16
            },
            "H1": {
              "selected_t": 55,
              "arrive_t": 67,
              "treatment_start_t": 67,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H04",
              "arrival_capped_minutes": 67
            },
            "W1": {
              "selected_t": 55,
              "arrive_t": 67,
              "treatment_start_t": 67,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H04",
              "arrival_capped_minutes": 67
            }
          },
          "comparisons": {
            "H1_minus_R1": {
              "delta_capped_minutes": 51,
              "actual_arrival_delta_minutes": 51,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_H1": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": 0,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R1": {
              "delta_capped_minutes": 51,
              "actual_arrival_delta_minutes": 51,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R0": {
              "delta_capped_minutes": 51,
              "actual_arrival_delta_minutes": 51,
              "success_lost": [],
              "success_gained": []
            }
          }
        }
      ]
    },
    {
      "id": "h1-worsening",
      "title": "가중치가 다른 환자가 경합하며67분 지연이 남은 사례",
      "profile": "simultaneous",
      "profile_label": "동시 발생",
      "explanation": "H1은 현재 미배정 가중치 합을 먼저 줄이는 선택을 했다. P17은39분 빨라졌지만 P05의67분 지연은 W1에서도 그대로 남았다.",
      "caveat": "현재 배치의 목적함수 개선이 모든 환자의 최종 도착 개선을 보장하지 않는다.",
      "phase": "development_known",
      "comparison": {
        "baseline": "R1",
        "candidate": "H1"
      },
      "preferred_comparison": "H1_minus_R1",
      "raw_path": "outputs/waiting_protection_20261030/study-v1/raw/016.json.gz",
      "raw_sha256": "c2d0c1a4f34e0075706472c0da890fbac1b194c40712b6fc70dc20a510f770d1",
      "raw_body_sha256": "3b3d84ce50f61c880032d1b54b5536bbf57a3313fa0fdb0bc8404b43d4b01613",
      "scenario_sha256": "01be6097ba4303c5f2ac135e49b41c0fffe43e03090eee94b765c496aa7a0bc1",
      "observation_end_t": 180,
      "patients": [
        {
          "id": "P05",
          "priority": 1,
          "occur_t": 0,
          "search_start_t": 6,
          "observation_end_t": 180,
          "endpoints": {
            "R0": {
              "selected_t": 8,
              "arrive_t": 15,
              "treatment_start_t": 15,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 15
            },
            "R1": {
              "selected_t": 8,
              "arrive_t": 15,
              "treatment_start_t": 15,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 15
            },
            "H1": {
              "selected_t": 75,
              "arrive_t": 82,
              "treatment_start_t": 82,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 82
            },
            "W1": {
              "selected_t": 75,
              "arrive_t": 82,
              "treatment_start_t": 82,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 82
            }
          },
          "comparisons": {
            "H1_minus_R1": {
              "delta_capped_minutes": 67,
              "actual_arrival_delta_minutes": 67,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_H1": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": 0,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R1": {
              "delta_capped_minutes": 67,
              "actual_arrival_delta_minutes": 67,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R0": {
              "delta_capped_minutes": 67,
              "actual_arrival_delta_minutes": 67,
              "success_lost": [],
              "success_gained": []
            }
          }
        },
        {
          "id": "P17",
          "priority": 2,
          "occur_t": 0,
          "search_start_t": 6,
          "observation_end_t": 180,
          "endpoints": {
            "R0": {
              "selected_t": 47,
              "arrive_t": 53,
              "treatment_start_t": 53,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 53
            },
            "R1": {
              "selected_t": 47,
              "arrive_t": 53,
              "treatment_start_t": 53,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 53
            },
            "H1": {
              "selected_t": 8,
              "arrive_t": 14,
              "treatment_start_t": 14,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 14
            },
            "W1": {
              "selected_t": 8,
              "arrive_t": 14,
              "treatment_start_t": 14,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H06",
              "arrival_capped_minutes": 14
            }
          },
          "comparisons": {
            "H1_minus_R1": {
              "delta_capped_minutes": -39,
              "actual_arrival_delta_minutes": -39,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_H1": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": 0,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R1": {
              "delta_capped_minutes": -39,
              "actual_arrival_delta_minutes": -39,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R0": {
              "delta_capped_minutes": -39,
              "actual_arrival_delta_minutes": -39,
              "success_lost": [],
              "success_gained": []
            }
          }
        }
      ]
    },
    {
      "id": "w1-waiting",
      "title": "기다린 환자를 보호했지만 다른 환자에게26분을 전가한 사례",
      "profile": "sparse",
      "profile_label": "병원이 적고 거리가 긴 환경",
      "explanation": "W1의 과거 대기시간 보호로 P02는 H1보다67분 빨리 도착했다. 같은 기록에서 P05는26분 늦어졌다.",
      "caveat": "이미 관측한 개발 사례의 부분 개선이다. 이 사례로 신규 검증 성공이나 운영 채택을 주장하지 않는다.",
      "phase": "development_known",
      "comparison": {
        "baseline": "H1",
        "candidate": "W1"
      },
      "preferred_comparison": "W1_minus_H1",
      "raw_path": "outputs/waiting_protection_20261030/study-v1/raw/003.json.gz",
      "raw_sha256": "c07cdb8ce3f9c840cd91619391c40f03fab9fac775c9c288b0ee065ac6e005c0",
      "raw_body_sha256": "f3a930cecba7f811d6f8cf65d1803beb38edd891ade9da911cb94970bfab8adb",
      "scenario_sha256": "3274b9708f6ee73391342c57cff2ce3c2a9aabf39bd8e3f5828de7ab19a2b900",
      "observation_end_t": 180,
      "patients": [
        {
          "id": "P02",
          "priority": 1,
          "occur_t": 3,
          "search_start_t": 9,
          "observation_end_t": 180,
          "endpoints": {
            "R0": {
              "selected_t": 19,
              "arrive_t": 29,
              "treatment_start_t": 29,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 26
            },
            "R1": {
              "selected_t": 19,
              "arrive_t": 29,
              "treatment_start_t": 29,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 26
            },
            "H1": {
              "selected_t": 86,
              "arrive_t": 96,
              "treatment_start_t": 96,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 93
            },
            "W1": {
              "selected_t": 19,
              "arrive_t": 29,
              "treatment_start_t": 29,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 26
            }
          },
          "comparisons": {
            "H1_minus_R1": {
              "delta_capped_minutes": 67,
              "actual_arrival_delta_minutes": 67,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_H1": {
              "delta_capped_minutes": -67,
              "actual_arrival_delta_minutes": -67,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R1": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": 0,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R0": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": 0,
              "success_lost": [],
              "success_gained": []
            }
          }
        },
        {
          "id": "P05",
          "priority": 1,
          "occur_t": 12,
          "search_start_t": 18,
          "observation_end_t": 180,
          "endpoints": {
            "R0": {
              "selected_t": 69,
              "arrive_t": 77,
              "treatment_start_t": 77,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 65
            },
            "R1": {
              "selected_t": 69,
              "arrive_t": 77,
              "treatment_start_t": 77,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 65
            },
            "H1": {
              "selected_t": 19,
              "arrive_t": 27,
              "treatment_start_t": 27,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 15
            },
            "W1": {
              "selected_t": 45,
              "arrive_t": 53,
              "treatment_start_t": 53,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H02",
              "arrival_capped_minutes": 41
            }
          },
          "comparisons": {
            "H1_minus_R1": {
              "delta_capped_minutes": -50,
              "actual_arrival_delta_minutes": -50,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_H1": {
              "delta_capped_minutes": 26,
              "actual_arrival_delta_minutes": 26,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R1": {
              "delta_capped_minutes": -24,
              "actual_arrival_delta_minutes": -24,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R0": {
              "delta_capped_minutes": -24,
              "actual_arrival_delta_minutes": -24,
              "success_lost": [],
              "success_gained": []
            }
          }
        }
      ]
    },
    {
      "id": "w1-transfer",
      "title": "60분 단축과83분 악화가 함께 생긴 사례",
      "profile": "sparse_adverse",
      "profile_label": "병원이 적고 미응답·지연·자원 중단이 겹친 환경",
      "explanation": "W1에서 P05는 H1보다60분 빨라졌지만 P12는83분 늦어졌다. 개선을 다른 환자의 지연과 함께 보여준다.",
      "caveat": "이 악화 때문에 개인에게 지연을 전가하지 않는 사전 연구 보호 기준을 통과하지 못했다.",
      "phase": "development_known",
      "comparison": {
        "baseline": "H1",
        "candidate": "W1"
      },
      "preferred_comparison": "W1_minus_H1",
      "raw_path": "outputs/waiting_protection_20261030/study-v1/raw/031.json.gz",
      "raw_sha256": "abc5304e7a1ba7b491a5888e9d343dba258b7df8e656a1f29f5be289d3ad027b",
      "raw_body_sha256": "6f344445973048ab6c80eda161d9e5dd58d6e55ad261121f15987d61a025e7cd",
      "scenario_sha256": "65d4a8d544c3ca4ad3525bad62436cd45e7a2bde04919b4baaee3f152c7b1191",
      "observation_end_t": 180,
      "patients": [
        {
          "id": "P05",
          "priority": 2,
          "occur_t": 3,
          "search_start_t": 9,
          "observation_end_t": 180,
          "endpoints": {
            "R0": {
              "selected_t": 59,
              "arrive_t": 79,
              "treatment_start_t": 79,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H03",
              "arrival_capped_minutes": 76
            },
            "R1": {
              "selected_t": 59,
              "arrive_t": 79,
              "treatment_start_t": 79,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H03",
              "arrival_capped_minutes": 76
            },
            "H1": {
              "selected_t": 119,
              "arrive_t": 139,
              "treatment_start_t": 139,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H03",
              "arrival_capped_minutes": 136
            },
            "W1": {
              "selected_t": 59,
              "arrive_t": 79,
              "treatment_start_t": 79,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H03",
              "arrival_capped_minutes": 76
            }
          },
          "comparisons": {
            "H1_minus_R1": {
              "delta_capped_minutes": 60,
              "actual_arrival_delta_minutes": 60,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_H1": {
              "delta_capped_minutes": -60,
              "actual_arrival_delta_minutes": -60,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R1": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": 0,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R0": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": 0,
              "success_lost": [],
              "success_gained": []
            }
          }
        },
        {
          "id": "P12",
          "priority": 2,
          "occur_t": 6,
          "search_start_t": 12,
          "observation_end_t": 180,
          "endpoints": {
            "R0": {
              "selected_t": 141,
              "arrive_t": 160,
              "treatment_start_t": 160,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H03",
              "arrival_capped_minutes": 154
            },
            "R1": {
              "selected_t": 141,
              "arrive_t": 160,
              "treatment_start_t": 160,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H03",
              "arrival_capped_minutes": 154
            },
            "H1": {
              "selected_t": 59,
              "arrive_t": 78,
              "treatment_start_t": 78,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H03",
              "arrival_capped_minutes": 72
            },
            "W1": {
              "selected_t": 142,
              "arrive_t": 161,
              "treatment_start_t": 161,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H03",
              "arrival_capped_minutes": 155
            }
          },
          "comparisons": {
            "H1_minus_R1": {
              "delta_capped_minutes": -82,
              "actual_arrival_delta_minutes": -82,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_H1": {
              "delta_capped_minutes": 83,
              "actual_arrival_delta_minutes": 83,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R1": {
              "delta_capped_minutes": 1,
              "actual_arrival_delta_minutes": 1,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R0": {
              "delta_capped_minutes": 1,
              "actual_arrival_delta_minutes": 1,
              "success_lost": [],
              "success_gained": []
            }
          }
        }
      ]
    },
    {
      "id": "zero-capped-loss",
      "title": "시간차0이 도착·치료 성공 상실을 가리는 사례",
      "profile": "arrival_condition",
      "profile_label": "도착 평가 후 추가 조건",
      "explanation": "R0는 관측 종료인 t180에 도착·치료를 시작했다. W1은 끝까지 도착하지 못했다. 두 도착 제한값은126분으로 같지만 성공 결과는 다르다.",
      "caveat": "미도착의 제한값은 실제 도착시간이 아니다. 이 상실은 H1에서도 있었고 W1이 새로 만든 악화로 해석하지 않는다.",
      "phase": "development_known",
      "comparison": {
        "baseline": "R0",
        "candidate": "W1"
      },
      "preferred_comparison": "W1_minus_R0",
      "raw_path": "outputs/waiting_protection_20261030/study-v1/raw/027.json.gz",
      "raw_sha256": "ce5b4f9c8420a69d9adae754877e2b069689b3a6eed345a07b15529f1fe79997",
      "raw_body_sha256": "dfcb5fefa88e61ecbe505ec93d35b42bc837905a9cfd5d64d4328e9cca248338",
      "scenario_sha256": "4d871994bb5b024f02632b9a97f7611a8018f2afef61163ae85dcf990498780c",
      "observation_end_t": 180,
      "patients": [
        {
          "id": "P19",
          "priority": 1,
          "occur_t": 54,
          "search_start_t": 60,
          "observation_end_t": 180,
          "endpoints": {
            "R0": {
              "selected_t": 64,
              "arrive_t": 180,
              "treatment_start_t": 180,
              "selected": true,
              "arrived": true,
              "treated": true,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H07",
              "arrival_capped_minutes": 126
            },
            "R1": {
              "selected_t": 64,
              "arrive_t": null,
              "treatment_start_t": null,
              "selected": true,
              "arrived": false,
              "treated": false,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H07",
              "arrival_capped_minutes": 126
            },
            "H1": {
              "selected_t": 64,
              "arrive_t": null,
              "treatment_start_t": null,
              "selected": true,
              "arrived": false,
              "treated": false,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H07",
              "arrival_capped_minutes": 126
            },
            "W1": {
              "selected_t": 64,
              "arrive_t": null,
              "treatment_start_t": null,
              "selected": true,
              "arrived": false,
              "treated": false,
              "observation_end_t": 180,
              "destination_hospital_id": "SYN-H07",
              "arrival_capped_minutes": 126
            }
          },
          "comparisons": {
            "H1_minus_R1": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": null,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_H1": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": null,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R1": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": null,
              "success_lost": [],
              "success_gained": []
            },
            "W1_minus_R0": {
              "delta_capped_minutes": 0,
              "actual_arrival_delta_minutes": null,
              "success_lost": [
                "arrived",
                "treated"
              ],
              "success_gained": []
            }
          }
        }
      ]
    }
  ],
  "study_summaries": {
    "development_known": {
      "environments": 36,
      "patients_per_policy": 603,
      "research_gate_passed": false,
      "policies": {
        "R0": {
          "patients": 603,
          "selected": 580,
          "unassigned": 23,
          "arrived": 564,
          "not_arrived": 39,
          "treated": 564,
          "untreated": 39,
          "priority4": {
            "patients": 143,
            "selected": 136,
            "unassigned": 7,
            "arrived": 133,
            "not_arrived": 10,
            "treated": 133,
            "untreated": 10
          }
        },
        "R1": {
          "patients": 603,
          "selected": 580,
          "unassigned": 23,
          "arrived": 563,
          "not_arrived": 40,
          "treated": 563,
          "untreated": 40,
          "priority4": {
            "patients": 143,
            "selected": 136,
            "unassigned": 7,
            "arrived": 133,
            "not_arrived": 10,
            "treated": 133,
            "untreated": 10
          }
        },
        "H1": {
          "patients": 603,
          "selected": 580,
          "unassigned": 23,
          "arrived": 563,
          "not_arrived": 40,
          "treated": 563,
          "untreated": 40,
          "priority4": {
            "patients": 143,
            "selected": 136,
            "unassigned": 7,
            "arrived": 133,
            "not_arrived": 10,
            "treated": 133,
            "untreated": 10
          }
        },
        "W1": {
          "patients": 603,
          "selected": 580,
          "unassigned": 23,
          "arrived": 563,
          "not_arrived": 40,
          "treated": 563,
          "untreated": 40,
          "priority4": {
            "patients": 143,
            "selected": 136,
            "unassigned": 7,
            "arrived": 133,
            "not_arrived": 10,
            "treated": 133,
            "untreated": 10
          }
        }
      }
    },
    "validation_new": {
      "environments": 60,
      "patients_per_policy": 987,
      "research_gate_passed": false,
      "policies": {
        "R0": {
          "patients": 987,
          "selected": 935,
          "unassigned": 52,
          "arrived": 902,
          "not_arrived": 85,
          "treated": 902,
          "untreated": 85,
          "priority4": {
            "patients": 248,
            "selected": 240,
            "unassigned": 8,
            "arrived": 230,
            "not_arrived": 18,
            "treated": 230,
            "untreated": 18
          }
        },
        "R1": {
          "patients": 987,
          "selected": 932,
          "unassigned": 55,
          "arrived": 902,
          "not_arrived": 85,
          "treated": 902,
          "untreated": 85,
          "priority4": {
            "patients": 248,
            "selected": 237,
            "unassigned": 11,
            "arrived": 228,
            "not_arrived": 20,
            "treated": 228,
            "untreated": 20
          }
        },
        "H1": {
          "patients": 987,
          "selected": 937,
          "unassigned": 50,
          "arrived": 902,
          "not_arrived": 85,
          "treated": 902,
          "untreated": 85,
          "priority4": {
            "patients": 248,
            "selected": 242,
            "unassigned": 6,
            "arrived": 234,
            "not_arrived": 14,
            "treated": 234,
            "untreated": 14
          }
        },
        "W1": {
          "patients": 987,
          "selected": 937,
          "unassigned": 50,
          "arrived": 902,
          "not_arrived": 85,
          "treated": 902,
          "untreated": 85,
          "priority4": {
            "patients": 248,
            "selected": 242,
            "unassigned": 6,
            "arrived": 234,
            "not_arrived": 14,
            "treated": 234,
            "untreated": 14
          }
        }
      }
    }
  },
  "fresh_summary": {
    "environments": 60,
    "patients": 987,
    "H1_W1_endpoint_equal": 987,
    "H1_W1_endpoint_and_destination_equal": 987,
    "both_arrived": 902,
    "both_not_arrived": 85,
    "max_W1_minus_R1": 124,
    "priority4_max_W1_minus_R1": 112,
    "max_W1_minus_H1": 0,
    "improved_vs_H1": 0,
    "research_gate_passed": false,
    "known_development": {
      "environments": 36,
      "patients": 603,
      "improved_vs_H1": 3,
      "worsened_vs_H1": 5,
      "equal": 595,
      "max_W1_minus_H1": 83
    },
    "equality_scope": "최초 선정·최종 도착·치료시작·관측 종료·성공 표시·도착 제한값의 동일성이다. 전체 경로나 임상 결과의 동일성을 뜻하지 않는다."
  },
  "limitations": [
    "Recorded current resources and event endpoints; not full independent resource-history reconstruction.",
    "96 synthetic environments; development 36 already observed, new validation 60, five per profile.",
    "Individual patients are nested in environments; no independent-patient inference or clinical safety claim.",
    "Finite-cohort predeclared research protection; all observed results become seen data for future candidates."
  ]
};
