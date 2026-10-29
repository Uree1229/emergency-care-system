# 일반 문의를 보호하는 선택적 재확인 처리 실험

2026-10-17. 기존 미채택 재확인 후보에서 최대 2분 늦어진 12건을 저장된 원본 입력 그대로 재생했다. 이번 구현은 병원의 접수·작업 상태 ACK와 선택적 심사 선점을 지원하는 **새 업무 계약**이다. 기존 정보만으로 문의 후보를 달리 고른 효과로 해석하면 안 된다.

## 변경한 처리

- 기존 작업자가 바쁘면 선택적 재확인을 비접수 ACK로 종료한다. 임상 응답이나 병상 정보로 해석하지 않는다.
- 일반 문의가 실제 들어오면 선택적 작업을 중단하고 잔여 작업을 보존해 재개한다. 일반 문의끼리는 원 제출 순서의 FIFO를 유지한다.
- 선택적 작업이 실제 완료됐다는 ACK를 받은 뒤에만 그 선택적 회신을 기다리지 않고 이미 확보한 수용 응답을 검증할 수 있다.
- 비접수 후 새로 도착한 자원 보고가 충분하면 제한적으로 정상 문의를 한 번 재개한다. 원 문의 정책 입력·실제 마지막 접촉 시각·동시/총 문의 한도는 보존한다.
- 통신 접촉 attempt와 실제 접수된 임상 심사 attempt를 분리했다. 비접수는 접촉 예산을 소비하지만 임상 작업이나 임상 응답 표본을 소비하지 않는다.
- 작업자 수와 각 심사의 원래 필요 작업량을 유지하고, 추가 문의의 실제 작업도 원장에 기록했다. 원 회신 기한·수용 응답 TTL·신선한 자원 확인·실제 자원 검증·담당자 확정 흐름을 유지한다.
- 처리 상태 채널이 없는 queue-disabled 환경에는 이 기능을 만들어 넣지 않는다. 기존 안전 후보의 정상 문의 처리를 유지한다.

## 기존 12건 재생

| 대상 | 직전 재확인 후보 | 개선 후 | 재확인 전 안전 후보 |
|---|---:|---:|---:|
| 늦은 회신 6건, P01 처치 시작 | 7.50분 | 6.75분 | 6.75분 |
| 뒤 환자 FIFO 6건, P02 처치 시작 | 6.25분 | 4.25분 | 4.25분 |
| 위 FIFO 사례의 P01 | 7.50분 | 8.25분 | 8.75분 |

표의 시간은 같은 시나리오 시작 시점 기준 처치 시작 시각이다. H1/C1에서 동일한 대상 12건을 각각 확인했다. 24명의 별도 사례로 중복 집계하지 않는다. FIFO에서 앞 환자의 직전 후보 대비 0.75분 단축 효과는 양보하지만, 재확인 전 안전 후보보다는 여전히 0.5분 빠르다.

대기는 이동을 제외한 탐색·병원 대기·전원 준비의 합이다. 늦은 회신 사례에서는 가까운 병원을 더 기다리므로 직전 후보보다 순수 대기는 1.25분 늘고 도착·처치는 0.75분 빨라진다. 이 절충을 숨기지 않았다. 대상 12건 전체 대기 합 변화: 안전 후보 대비 -3.00환자·분, 직전 재확인 후보 대비 +0.00환자·분.

## 새 입력의 검증

기존 83개 환경을 값 그대로 상속하고 새 업무 계약 경계 12개를 추가했다. small seed 7/8에서 구조와 원장을 확인한 뒤 소스·실험 조건을 고정했다. DEV 41101–41103에서 선택을 잠그고 VAL 42101–42106에서는 다시 선택하거나 튜닝하지 않았다. 기존 파일 3,932개를 보존하고 seed 값/배열 전수 검사로 새 seed의 기존 사용이 없음을 확인했다.

| 비교, H1 기준 | 처치 단축 | 동일 | 악화 | 최대 처치 지연 | 전체 대기 합 변화 | 대기 증가 환경 |
|---|---:|---:|---:|---:|---:|---:|
| 안전 후보 대비 | 66 | 1026 | 0 | 0.00분 | -55.50 | 0 |
| 직전 재확인 후보 대비 | 48 | 1014 | 30 | 2.00분 | +18.00 | 36 |
| 기존 보호 V4 대비 | 90 | 996 | 6 | 17.00분 | -74.00 | 0 |

H1/C1의 전체 수치는 검증 JSON에서 각각 확인할 수 있다. 미처치는 동일 관찰 종료 시점으로 제한해 요청→처치 합을 계산한다.

## 검증과 반영 판단

- 새 검증: 570환경 × 10분기 = 5700실행.
- 단위·기존 회귀 검사: 1827개 통과. 앞선 실험과 동일한 선택적 로컬 HTTP 바인딩 검사 6개만 제외했고 새 기능 검사는 전부 포함했다.
- 저장된 원시 입력·이벤트·접수/작업 원장·실제 자원·배정 모델 증거를 엔진/solver를 불러오지 않는 별도 프로세스에서 재검증했다.
- 기존 31개 저장 입력은 310분기를 재생했고, 공통 8분기 × 31건 = 248개의 환자 결과/문의 상태 기록을 원본과 대조했다.
- 기본 반영 여부는 아래 고정 gate 결과를 따른다. 안전 후보 대비 개별 지연·환경별 대기 증가를 금지하고, 직전 재확인 후보·기존 보호 V4 대비 추가 환경별 gate도 그대로 유지한다.

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

순수 대기와 이동·처치 도달 시간은 다른 지표다. 일부 환경의 대기 증가를 다른 환경의 절감으로 감춰 gate를 통과시키지 않았다.

## 적용 범위와 남은 한계

합성 실험이며 실제 병원 데이터·임상 판단 성능 또는 현장 개선 효과를 입증하지 않는다. ACK 전송 지연과 작업 전환 비용은 0분인 소프트웨어 제어 모형이다. 실제 병원은 상태 통지·비접수·작업 중단/재개를 지원해야 하며, 양의 통신/전환 비용을 반영한 성능은 이번 결과로 주장하지 않는다. 실제 서버·병원/환자 UI·DB와 기본 문의 정책 fixed_3는 변경하지 않았다. H1/C1 자체의 수학적 목적함수나 임상 우선순위를 바꾼 실험도 아니다.

## 결과 파일

- `simulation_results/cooperative_probe_development_20261017.json` / `.md` 및 `_cases/`
- `simulation_results/cooperative_probe_validation_20261017.json` / `.md` 및 `_cases/`
- `config/cooperative_probe_lock_20261017.json`
- `.codex-build/probe-recovery-20261017/verification.json`와 독립 원시 감사·기존 31건·구조/seed 검사 기록
