# 조기 확정의 후발 환자 지연 개선 실험

기존 조기 확정 안에서 후발 단독 후보 환자의 처치가 최대 22분 늦어진 문제를 대상으로, 현재 확인된 자원에 여유를 요구하는 규칙 하나를 별도 구현했다. 격리된 합성 시뮬레이션이며 운영 기본은 유지했다.

원래 반례는 H1·C1 모두 세 시드에서 normal 수준으로 회복했다. 그러나 새로 추가한 더 큰 수요의 후발 환자 반례가 남아 전체 기준은 미통과다. normal 대비 순대기는 H1 +39.5환자·분, C1 +74.5환자·분이며, C1에는 새 조기 승격 없이 발생한 시간 제한 fallback 차이가 포함된다. 후보는 미채택이다.

## 변경한 규칙

기존 public_blocked의 모든 조건을 통과한 경우에도 실제 배정 가능한 모든 유효 수용 응답의 필수 자원마다 다음 조건을 요구한다.

`현재 free >= 해당 병원과 호환되는 등록 SEARCH 환자 전체 수요 합 + 현재 환자 수요`

현재 검색 중인 호환 환자 전원의 수요를 반영한 뒤에도 현재 환자와 같은 수요 한 명분이 남아야 한다. 한 명분 여유는 성능 실험 전에 고정한 새 규칙이며 계수 탐색은 하지 않았다. 원래 snapshot과 실제 배정 옵션의 순서·원소, 문의 업무·재시도·대기 기한·자원 보고·보호·백업·최종 물리 검사는 유지했다. 조건 실패 시 새 조기 승격만 보류한다.

정책 증거의 병원 순서는 원 snapshot 순서이고, 실제 배정 옵션은 실제 수용 응답 순서다. 두 순서를 구분해 각각 감사한다. 미래 환자나 병상 중단·회복 시각, 예정된 병원 작업 완료 정보는 정책 입력에 넣지 않는다.

한 명분 여유가 더 큰 수요, 여러 후발 환자 또는 미래 병상 중단의 무악화를 보장하지는 않는다. 이 한계를 새 경계 입력에 포함했다.

## 고정한 검증

기존 141환경 유형은 바이트 단위로 유지하고 경계 8개를 추가했다. 공개된 개발 시드 44101~44103의 447환경에서 신규 1086분기를 실행하고 과거 전체 원문과 완전 감사 증명 3384개를 재사용했다. 재사용 CPU·실행·감사를 신규로 세지 않았다.

직전 normal과 미채택 public 조기 확정 안을 각각 비교하고, safe/protected 배포 조건도 유지했다. 기존 성공 상실·개인 처치/최종 도착 악화·환경별 순대기/제한 처치/문의 증가가 없어야 하며, normal 대비 전체 순대기와 제한 처치 합이 엄격히 감소해야 한다.

자동 검사 2520개 통과, seed7 사전 통합 330분기 원문 감사 통과. 기존 여섯 개 선택적 로컬 HTTP 검사 제외는 유지했고 새 검사는 모두 포함했다. 예약 시드 52101~52106은 과거 seed 필드 노출을 조사한 뒤 소스를 고정하고 입력만 생성했다.

## 개발 결과

| 기준 | 배정 | 처치 지표 개선 | 실제 처치 지연 | 성공 상실 | 새 성공 | 순대기 변화(환자·분) | 문의 변화 | 최대 실제 지연(분) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 기존 normal | H1 | 20 | 3 | 0 | 0 | +39.50 | +0 | 22.00 |
| 기존 normal | C1 | 21 | 4 | 0 | 0 | +74.50 | +1 | 35.00 |
| 이전 조기 확정 | H1 | 6 | 18 | 0 | 0 | -36.50 | +0 | 2.00 |
| 이전 조기 확정 | C1 | 7 | 19 | 0 | 0 | -1.50 | +1 | 35.00 |

순대기는 사건에서 재적분한 탐색·병원 대기·전원 준비의 합으로 실제 이동 시간을 제외한다. 실제 처치 지연은 양쪽 모두 처치된 환자의 시각을 비교한다. 미처치에 제한값을 적용한 지표와 구분하며 반복 시드는 독립 임상 표본으로 해석하지 않는다.

표의 환자 관측은 배정 방식별 각각 837건이다. H1·C1의 관측을 합쳐 독립 환자 수로 세지 않는다. 이전 조기 확정 대비 개선과 기존 normal 대비 개선을 구분한다.

## 원래 22분 지연 환경과 새 경계

| 환경 | 배정 | normal 대비 순대기 변화 | 이전 조기 확정 대비 순대기 변화 | normal 대비 실제 처치 지연 | normal 대비 최대 지연 |
|---|---|---:|---:|---:|---:|
| public_offer_later_scarce_patient | H1 | +0.00 | -56.00 | 0 | 0.00 |
| public_offer_later_scarce_patient | C1 | +0.00 | -56.00 | 0 | 0.00 |
| offer_reserve_ample_normal | H1 | -3.00 | +0.00 | 0 | 0.00 |
| offer_reserve_ample_normal | C1 | -3.00 | +0.00 | 0 | 0.00 |
| offer_reserve_below_boundary | H1 | +0.00 | +3.00 | 0 | 0.00 |
| offer_reserve_below_boundary | C1 | +0.00 | +3.00 | 0 | 0.00 |
| offer_reserve_same_demand_future | H1 | -3.00 | +0.00 | 0 | 0.00 |
| offer_reserve_same_demand_future | C1 | -3.00 | +0.00 | 0 | 0.00 |
| offer_reserve_greater_demand_future | H1 | +56.00 | +0.00 | 3 | 22.00 |
| offer_reserve_greater_demand_future | C1 | +56.00 | +0.00 | 3 | 22.00 |
| offer_reserve_repeated_future | H1 | -6.00 | +0.00 | 0 | 0.00 |
| offer_reserve_repeated_future | C1 | -6.00 | +0.00 | 0 | 0.00 |
| offer_reserve_simultaneous_current | H1 | +0.00 | +1.50 | 0 | 0.00 |
| offer_reserve_simultaneous_current | C1 | +0.00 | +1.50 | 0 | 0.00 |
| offer_reserve_second_resource_failure | H1 | +0.00 | +4.50 | 0 | 0.00 |
| offer_reserve_second_resource_failure | C1 | +0.00 | +4.50 | 0 | 0.00 |
| offer_reserve_ample_future_outage | H1 | -3.00 | +0.00 | 0 | 0.00 |
| offer_reserve_ample_future_outage | C1 | -3.00 | +0.00 | 0 | 0.00 |

## 사후 원인 분석

저장된 원문만 읽어 분석했으며 정책·엔진·solver·감사기를 다시 실행하지 않았다. `posthoc-target.json`과 `posthoc-boundaries.json`은 최종 보고서·독립 감사·사전 고정 소스와 입력·각 원문 해시에 결속했다. 아래 시각은 시뮬레이션 시작 기준 분이다.

### 원래 반례의 회복

| 시드 | 이전 조기 확정 P03 처치 시각 | 새 규칙 P03 처치 시각 | normal P03 처치 시각 | 이전 안 대비 회복 |
|---|---:|---:|---:|---:|
| 44101 | 28.5 | 6.5 | 6.5 | 22분 |
| 44102 | 24.5 | 6.5 | 6.5 | 18분 |
| 44103 | 25.5 | 6.5 | 6.5 | 19분 |

위 결과는 H1·C1 각각에서 같았다. 1.5분의 원래 공개 snapshot과 기본 public 결정은 같았고, 등록 환자는 P01·P02뿐이었다. 새 규칙은 H03의 free 2개에 대해 현재 호환 코호트 수요 2개와 추가 본인 수요 2개, 합계 4개가 필요하므로 P01의 조기 확정을 보류했다.

2분에 발생한 P03은 이전 안에서 H03 committed 2/free 0 때문에 문의가 막혔지만, 새 안에서는 committed 0/free 2로 실제 문의를 제출했다. 문의 시각은 24·20·21분에서 모두 2분으로, 확정 시각은 24.5·20.5·21.5분에서 모두 2.5분으로 회복했다. 실제 검토 업무 0.5분은 유지했다. P01이 이전 안에서 얻던 2분 단축은 반납해 normal과 같아졌으며, 세 환자의 전체 결과도 시드별 normal과 같았다. 목표 환경의 순대기 변화는 이전 안 대비 시드별 −21·−17·−18, 배정 방식별 합계 −56환자·분이다.

### 더 큰 수요에서는 남는 한계

새 `offer_reserve_greater_demand_future`는 H03 capacity/free가 4개인 환경이다. 최초 결정은 현재 수요 2개와 여유 2개를 만족해 조기 확정을 허용했다. 그러나 2분에 등록된 P03은 병상 3개가 필요해, committed 2/free 2의 공개 보고에서는 문의할 수 없었다.

첫 시드에서는 normal이 2.5분에 P01을 H02, P03을 H03으로 배정했다. 새 안에서는 P01이 이미 H03으로 이송 중이었고, P03의 실제 문의가 2분에서 24분으로 밀렸다. P03 처치는 6.5분에서 28.5분으로 22분 늦어졌다. 세 시드의 P03 지연은 22·18·19분, P01의 단축은 각각 2분이다. 이 환경 유형의 처치까지 시간 합은 +53환자·분, 순대기는 +56환자·분이며 H1·C1에서 같았다. 문의 수와 검토 업무량 증가는 없었다.

정상 여유·같은 수요 후발·큰 수요 후발·반복 후발·미래 중단 다섯 환경은 각 시드의 첫 승격 snapshot과 결정이 완전히 같았다. 당시 입력에 미래 환자가 없으므로 이번 규칙이 더 큰 미래 수요를 예상해 병상을 남겼다고 설명할 수 없다.

같은 수요 후발에서는 순대기 합이 −3환자·분, 반복 후발에서는 −6환자·분이었다. 이번 미래 중단은 병상 1개가 중단돼 capacity 4→3이 되는 환경으로, 현재 수요 2개를 계속 수용할 수 있어 순대기 −3환자·분이었다. 이를 재이송 실패 반례로 분류하지 않는다. 새 8환경 유형 전체의 순대기는 배정 방식별 각각 +41환자·분이고, 기존 141유형의 H1 이득 −1.5를 합친 전체 H1 변화는 +39.5환자·분이다.

### C1의 별도 시간 제한 경로

H1과 C1의 환경별 임상 변화 차이는 시드 44103의 `out_of_order` 한 환경에서 발생했다. 새 조기 승격은 0회였다. 7분의 배정 입력은 기준과 완전히 같았지만, 기준은 `OPTIMAL`로 P02→FAR·P03→NEAR를 선택했고 새 실행에는 50ms 예산의 `FALLBACK`, `budget_exhausted_before_stage_one`이 기록돼 P02→NEAR·P03→미배정이 됐다.

P02 처치는 16→10분, P03은 10→45분으로 변했다. 10.25분의 P03 배정 제안은 기존 확인 gate가 `RESOURCE_CONFLICT`로 거부했고, 실제 재문의 38분·확정 42분·처치 45분으로 이어졌다. 이 환경의 순대기 +35환자·분, 처치까지 시간 합 +29환자·분, 문의 +1건이 전체 C1과 H1의 차이다. 해당 실행을 집계에서 빼거나 다시 돌리지 않았다. 이를 새 여유 수량 규칙의 인과 효과나 확인되지 않은 호스트 CPU 원인으로 귀속하지 않는다.

## 판정

- H1: normal 대비 1차 기준 미통과, safe/protected 포함 배포 조건 미통과. 실패 항목: no_individual_treatment_worsening, no_individual_final_arrival_worsening, no_case_total_wait_increase, no_case_capped_treatment_increase, strict_total_wait_reduction, strict_capped_treatment_reduction.
- C1: normal 대비 1차 기준 미통과, safe/protected 포함 배포 조건 미통과. 실패 항목: no_individual_treatment_worsening, no_individual_final_arrival_worsening, no_case_total_wait_increase, no_case_capped_treatment_increase, inquiry_ratio_bounded, strict_total_wait_reduction, no_case_inquiry_increase, strict_capped_treatment_reduction.

개발의 사전 기준을 통과하지 못해 예약 검증 성능 실행은 생략했다.

`adopted=False`이며 운영 기본 `fixed_3`를 유지했다. 기존 앱·서버·DB에는 적용하지 않았다. C1의 실제 50ms 계산 결과와 fallback을 모두 보존했으며 실행을 반복하거나 불리한 결과를 제외하지 않았다. 캐시 기준과의 모든 C1 경로 차이를 정책 효과로 귀속하지 않는다.

## 증거

- `simulation/offer_reserve_{policy,engine,audit,scenarios,experiment}.py`와 대응 검사: 새 규칙·실제 단계·독립 감사·고정 입력·비교 절차.
- `simulation_results/offer_reserve_development_20261020.json` 및 `_cases/`: 신규 전체 원문과 기존 증명 결속.
- `.codex-build/offer-reserve-20261020/development-independent-audit.json`: 전체 원문 재감사와 사건 기반 대기·문의 업무 집계.
- 같은 build의 `posthoc-target.json`, `posthoc-boundaries.json`: 목표 반례의 회복, 새 경계와 C1 시간 제한 경로를 원문·최종 보고서·독립 감사에 결속한 분석.
- 같은 build의 `preservation/`, `seed-audit/`, `pre-development-freeze.json`, `verification.json`: 선행 기록 보존·예약 seed 확인·사전 고정·최종 봉인.
- [이전 조기 확정 실험](PUBLIC_OFFER_EXPERIMENT.md): 최대 22분 악화가 나온 선행 결과.
