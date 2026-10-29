# 처리 지원 여부에 맞춘 재확인 개선 및 검증

2026-10-17 실험, 2026-10-18 독립 감사 완료. 최대 2분 늦어진 기존 12건을 복구하는 협력형 재확인 실험을 진행하고, 작업 큐가 없는 환경까지 안전 후보로 되돌려 이득을 잃던 부작용을 별도 후속 실험으로 수정했다. 앞선 실험과 원시 기록은 모두 보존했다.

## 최종 구현

- 입력에 선언된 `inquiry_queue.enabled`가 참이면 앞서 검증한 협력형 엔진을 그대로 실행한다.
- 큐가 없는 독립 응답 환경은 기존 재확인 엔진을 그대로 실행한다. 새 작업자·상태 ACK를 만들어 넣지 않는다.
- 두 부모 엔진의 선택기·응답·작업 일정·자원 검증은 바꾸지 않고, 실제 실행 원문 전체에 설명용 namespace 3개만 추가한다.
- 협력형 엔진은 일반 문의의 실제 도착 시 선택적 심사를 중단·재개하고, 현재 작업 완료 ACK와 실제 수용 회신을 구분한다.
- 비접수 후 새로 받은 신선하고 충분한 보고에 대한 정상 재문의는 별도 출처로 기록한다. 실제 접촉 시각·예산은 보존하고, 접촉 attempt와 실제 임상 심사 attempt를 구분한다.

자원 정보는 기존 초기·정기·확인 채널을 사용한다. 문의 행동에 따라 확인 요청과 수신 시점은 달라질 수 있으며, 실제 요청 수와 받은 원문을 함께 기록했다. 새 자원 변화 푸시 채널은 추가하지 않았다.

## 요청한 12건의 결과

| 대상 | 직전 재확인 시제품 | 최종 후보 | 재확인 전 안전 후보 |
|---|---:|---:|---:|
| 늦은 회신 6건의 P01 처치 시작 | 7.50분 | 6.75분 | 6.75분 |
| FIFO 지연 6건의 P02 처치 시작 | 6.25분 | 4.25분 | 4.25분 |
| 위 FIFO 사례의 P01 | 7.50분 | 8.25분 | 8.75분 |

시간은 동일 시나리오 시작 기준이다. H1/C1에서 같은 12건을 각각 확인했으며 별도 24건으로 중복 집계하지 않는다. 대상 12건 전체 대기 합은 직전 시제품 대비 +0.00환자·분, 안전 후보 대비 -3.00환자·분이다. FIFO에서 앞 환자의 시제품 대비 0.75분 이득을 양보하지만, 안전 후보보다 0.5분 빠르다. 기존 19개 회귀 사례의 개선 효과도 유지했다.

## 평가 설계

기존 95개 환경의 값은 바꾸지 않았다. 앞선 개발 시드 41101–41103은 이미 관측한 개발 데이터로 명시적으로 재사용했다. 후속 수정은 이 개발 결과에 나타난 지원 여부 처리 오류를 근거로 정했고, 후속 소스 136개를 고정한 뒤 새 검증 시드 43101–43106을 실행했다. 앞선 421xx 검증의 결과로 후속 정책을 선택하거나 431xx 결과로 재튜닝하지 않았다.

## 새 합성 검증 결과

| 비교, H1 | 단축 | 동일 | 처치 지연 | 최대 지연 | 기존 성공 상실 | 새 성공 | 전체 대기 합 변화 | 대기 증가 환경 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 재확인 전 안전 후보 | 78 | 1014 | 0 | 0.00분 | 0 | 0 | -79.50 | 0 |
| 첫 협력형 후보 | 12 | 1080 | 0 | 0.00분 | 0 | 0 | -24.00 | 0 |
| 직전 재확인 시제품 | 48 | 1026 | 18 | 1.00분 | 0 | 0 | -6.00 | 24 |
| 기존 보호 V4 | 102 | 984 | 6 | 17.00분 | 0 | 0 | -108.00 | 0 |
| 고정 3곳 참고 기준 | 149 | 670 | 273 | 56.50분 | 18 | 12 | +546.25 | 197 |

직전 시제품 대비 남은 개인 지연 18명은 선택적 재문의의 주인 P01이다. 일반 문의를 먼저 처리한 뒤 선택적 작업을 재개하면서 P01이 회신 또는 원래 만료 시점까지 더 기다린다. 기존 FIFO 가족과 중단·재개/일반 문의 2건 경계 가족에서 발생하며, 이 환경들의 전체 대기 합은 감소했다.

환경별 대기 합이 증가한 집단은 위 개인 지연 집단과 다르다. 늦은 회신·실행 중 회신·접수 거부 후 재문의 환경에서 이동 5분 병원 대신 3분 병원의 실제 회신을 기다려 탐색 대기는 늘지만 도착과 처치는 빨라졌다. 두 종류의 절충을 같은 사례로 합쳐 계산하지 않았다.

대기는 이동을 제외한 탐색·병원 대기·전원 준비의 합이다. 더 가까운 병원의 실제 회신을 기다리면 대기는 늘어도 도착·처치는 빨라질 수 있다. 이동·처치 시간과 미처치 여부도 별도로 검사했다. 미처치는 동일 관찰 종료로 제한해 합산한다.

| 분기 | 환자 | 미처치 | 전체 대기 합 | 요청→처치 제한 합 | 실제 문의 |
|---|---:|---:|---:|---:|---:|
| fixed3 | 1092 | 54 | 8748.75 | 13368.25 | 2531 |
| baseline | 1092 | 60 | 9403.00 | 13680.50 | 2852 |
| safe | 1092 | 60 | 9374.50 | 13622.00 | 2858 |
| probe | 1092 | 60 | 9301.00 | 13596.50 | 2870 |
| cooperative | 1092 | 60 | 9319.00 | 13566.50 | 2888 |
| capability | 1092 | 60 | 9295.00 | 13542.50 | 2894 |

H1/C1의 전체 비교·환자별 차이는 각 검증 JSON에 별도로 보존했다. 전체 합이 줄어도 개별 환경의 대기 증가나 다른 환자의 지연을 숨기지 않았다. 고정 3곳 참고 기준 대비 기존 성공 상실과 새 성공, 미처치 총계도 함께 제시했으며 이 기준보다 우수하다고 결론 내리지 않았다.

## 통과 기준과 반영

안전 후보와 첫 협력형 후보 대비 개별 도착·처치 비악화, 환경별 전체 대기·제한 처치 합 비증가를 요구했다. 직전 시제품과 기존 보호 V4 대비 추가 환경별 gate, 문의 비용 1.10배 한도, 엄격한 총 대기 감소 기준도 완화하지 않았다. 사례별 대기 절충이 남아 최종 기본 반영은 보류했다.

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

## 검증 근거

- 기존 검사와 새 검사 1,885개 통과. 앞선 실험과 동일한 선택적 로컬 HTTP 바인딩 검사 6개만 제외했다.
- 과거 31개 입력의 새 후보 62분기를 실제 실행·저장하고, 별도 프로세스에서 엔진/솔버 import 없이 원문 전체를 감사했다. 저장 참조 310분기는 읽기만 했다.
- small seed 7/8의 새 후보 380분기를 감사하고, 기존 참조 760분기를 읽어 선택된 부모와 임상·문의·작업·ACK 기록의 동일성을 확인했다.
- 개발 285환경 × 12 = 3420분기, 새 검증 570환경 × 12 = 6840분기를 저장 원문에서 독립 재검증했다.
- 검증 감사의 첫 stdout 리디렉션 실행은 진행 출력 없이 예상 시간을 넘겨 중단했다. 원인을 단정하지 않았고, 이 중단 실행은 통과 수에 넣지 않았다. 같은 보고서·원시 기록·감사 코드 SHA로 출력을 직접 확인하며 재실행한 완료 감사만 근거로 사용했다.
- 직접 출력한 두 번째 감사도 380/570 진행 확인 후 사용자 취침 요청으로 중단했다. 부분 진행은 통과에 포함하지 않았다. 2026-10-18 재개 승인 후 같은 감사 코드와 저장 입력으로 전체 570환경을 처음부터 다시 감사한 완료 결과만 봉인했다.
- 새 431xx 시드는 프로젝트 JSON/GZIP 5,194개 실제 값 검사에서 기존 사용이 없었다. 고정 core의 JSON/GZIP 3,833개도 모두 검사·해시 대조했다.
- 개발 전 core 4,291개와 앞선 실험 완료 후 봉인한 선행 파일 5071개가 보존됐다. 선행 130개 소스는 변경하지 않았고, 후속 6개 파일만 추가했다.

## 적용 범위

합성 환경의 결과다. 실제 병원 효과나 범용 최적성을 주장하지 않는다. 협력형 모드는 병원에 접수·현재 상태 통지·작업 중단/재개 지원이 필요하고, ACK 지연과 작업 전환 비용은 0분으로 둔 모형이다. 실제 서버·UI·DB 및 기본 문의 정책 fixed_3는 변경하지 않았다.

## 파일

- `simulation/capability_probe_engine.py`, `simulation/capability_probe_audit.py`, `simulation/capability_probe_experiment.py`
- `simulation_results/capability_probe_development_20261017.json` / `.md` 및 `_cases/`
- `simulation_results/capability_probe_validation_20261017.json` / `.md` 및 `_cases/`
- `config/capability_probe_lock_20261017.json`
- `.codex-build/capability-probe-20261017/verification.json`와 원문·검사·보존·독립 감사 기록
