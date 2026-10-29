# 신규 개선안 1: 최근 부족 보고 뒤의 선택적 재문의 억제

이 실험은 격리된 합성 시뮬레이션이다. 운영 기본 `fixed_3`, 서버, UI, DB는 변경하지 않았다.

## 원인과 변경 범위

이전 개발 입력의 duplicate recovery feeds에서 회복 전 선택적 문의가 실제 첫 임상 시도를 소비했다. 회복 후 문의는 원래 무응답인 두 번째 시도로 넘어가 처치 성공이 사라졌다. 실제 문의를 공짜 확인으로 바꾸거나 시도 횟수를 초기화하지 않고, 선택적 문의의 입장 조건만 바꿨다.

- `renewed`: 원 정책이 고른 병원의 신선한 병상 부족 관측 시각이 최초 실제 문의 시각보다 엄격히 뒤라면 선택적 재문의를 억제한다.
- `off`: 모든 선택적 부족 재문의를 끄는 ablation이다. 선택적 근거를 판단하는 정책으로 설명하지 않는다.
- 원 정상 문의와 normal recovery rank, 실제 접촉 시각·시도의 계산·기록 규칙, FIFO·인력·병상·자원 보고·임상 우선순위·TTL·회차 한도는 유지했다. 문의를 생략한 결과 실제 경로의 접촉 시각과 시도 순서는 달라질 수 있다.

최근 부족 보고가 있더라도 보고 없이 병상이 회복될 수 있다. 같은 관측을 가진 물리적 회복 쌍을 넣어 그 부작용도 비교했다.

## 사전 고정 검증

기존 129개 환경 유형에 경계 6개를 추가했다. 관측한 개발 시드 44101~44103의 405환경에서 신규 1728분기를 실행하고, 기존 전체 원문·완전 감사 증명 2322개를 재사용했다. 재사용 CPU와 증명은 새 실행·새 감사로 세지 않았다.

비교 기준은 직전 normal rank, safe, protected의 H1/C1이다. fixed3·prototype·이전 실패 분기 등은 이전 증거를 보존하고 이번 새 우월성 주장에서는 제외했다.

소스·규칙·선택 기준·입력 해시·독립 감사 코드를 성능 실행 전에 고정했다. 예약 시드 48101~48106은 과거 11,540파일의 seed 필드를 확인한 후 입력만 생성·봉인했다.

전체 테스트 2365개 통과, seed7 전체 원문 통합 540분기 감사 통과. 기존 여섯 개 로컬 HTTP 선택 검사 제외는 그대로 유지했고 새 검사는 전부 포함했다.

통과 기준: 직전 기준 대비 처치 성공 상실·개인 처치/최종 도착 악화·환경별 전체 순대기/제한 처치/문의 증가가 없어야 하며, 전체 순대기와 제한 처치 합은 엄격히 줄어야 한다. 개인 순대기는 별도 진단한다.

## 개발 결과: 직전 normal rank 대비

| 배정 | 문의안 | 처치 지표 개선 | 실제 처치 지연 | 성공 상실 | 새 성공 | 순대기 합 변화(환자·분) | 문의 변화 | 대기 증가 환경 | 1차 기준 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| H1 | renewed | 24 | 8 | 12 | 3 | +352.25 | +221 | 18 | 미통과 |
| H1 | off | 27 | 51 | 12 | 3 | +397.00 | +170 | 64 | 미통과 |
| C1 | renewed | 25 | 9 | 12 | 3 | +393.25 | +222 | 19 | 미통과 |
| C1 | off | 27 | 51 | 12 | 3 | +397.00 | +170 | 64 | 미통과 |

순대기는 실제 이벤트로 재적분한 탐색·병원 대기·전원 준비의 합으로, 실제 이동 시간은 제외한다. 미처치 환자의 제한 처치 지표와 양쪽 모두 실제 처치에 성공한 환자의 처치 지연을 구별했다.

표의 건수는 배정·문의안별 환자 관측 735건을 비교한 결과다. 같은 환경 유형의 시드 반복이므로 독립 임상 표본으로 해석하지 않는다. 실제 처치 지연은 양쪽 모두 처치된 환자만 세며, 성공 상실은 기준에서 처치됐지만 새 안에서는 시뮬레이션 종료까지 처치되지 않은 환자다.

## 사후 원인 분석

소스와 규칙을 고정한 뒤 저장된 전체 사건 기록을 읽어 분석했다. 아래 분석으로 정책·입력·결과를 다시 조정하거나 엔진을 재실행하지 않았다.

H1/renewed의 성공 상실 12건은 네 환경 유형의 세 시드에서 각각 한 건씩 발생했다. 해당 유형은 `recovery_dispatch_recovery_then_decline`, `recovery_rank_repeated_recovery_after_decline`, `recovery_rank_unvisited_recovery_alternative`, `recovery_rank_later_patient_fifo`다. 이 유형들의 순대기 증가는 +525.75환자·분, 문의 증가는 +312건이었다. 다른 유형의 순대기 변화 −173.50환자·분을 합쳐도 전체는 +352.25환자·분이다.

첫 시드의 네 유형 원문에서는 선택적 문의를 억제한 뒤 첫 임상 문의가 4분에 제출되고, 실제 병원 업무와 자원 확인을 거쳐 4.5분에 수용·확정됐다. 그러나 병상은 5~8분에 중단됐고, 7.5분 도착 시 실제 용량 부족으로 수용되지 못했다. 이후 전원 탐색과 무응답 문의가 반복됐다. 확정 당시 신선하고 충분했던 보고와 원 TTL은 유효했으며, 미래 중단 정보를 정책이 알고 있던 상황이 아니다. 기존 정책에서는 초기 문의의 거절 이후 다른 시점에 재문의·도착해 처치됐다. 따라서 이 결과를 단순한 TTL 만료나 병원 접수 실패로 설명하지 않는다.

반대로 duplicate recovery feeds 유형에서는 초기 선택적 문의를 아낀 결과 후속 실제 시도의 응답이 달라져 새 처치 성공 3건이 생겼다. 한 유형의 이득이 전체 비악화 기준을 충족시키지는 못했다. 별도 hidden recovery 반례에서는 공개 부족 보고 뒤 실제 회복 보고가 오지 않아, 억제가 수용·재확인 경로를 늦추고 실제 처치를 8.5분 지연시켰다.

C1/renewed가 H1/renewed보다 순대기 +41환자·분, 문의 +1건 더 나빠진 차이는 `delayed_team_confirmation`, 시드 44102의 단일 사례에 해당한다. 시뮬레이션 시각 2분의 기준 C1 배정 기록은 `OPTIMAL`이었고 새 C1의 원문에는 `FALLBACK`, `budget_exhausted_before_stage_one`이 기록됐다. 그 결과 P02/P03의 초기 배정이 달라졌고 P03의 처치는 5분에서 46분으로 늦어졌다. 이 사례의 선택적 probe 전송과 veto는 모두 0이다. 이 차이를 문의 억제 규칙의 임상 효과나 확인되지 않은 호스트 CPU 원인으로 귀속하지 않으며, 기록된 시간 예산 결과 차이로 구분한다. 해당 실행을 다시 돌리거나 집계에서 빼지 않았다.

전체 405환경의 신규 1,728분기는 독립 원문 재감사를 통과했고, 재사용한 2,322개 전체 참조 증명도 결속을 확인했다. `causal-audit.json`은 일부 첫 시드의 초기 진단 기록이며 최종 보고서 생성 전 상태임을 남겼다. 추가 `causal-losses.json`은 최종 개발 보고서 해시와 각 원문에 결속한 분석이다.

## 결정

사전 기준을 모두 통과한 안이 없어 예약 검증 성능 실행을 생략했다. 개발 선택 순위는 실패한 안들의 연구상 순위일 뿐 채택이 아니다. 기존 연구 기준과 운영 기본을 유지했다.

- H1/renewed 1차 실패 항목: no_lost_treatment, no_individual_treatment_worsening, no_individual_final_arrival_worsening, no_case_total_wait_increase, no_case_capped_treatment_increase, inquiry_ratio_bounded, strict_total_wait_reduction, no_case_inquiry_increase, strict_capped_treatment_reduction. safe/protected 포함 배포 기준: 미통과.
- H1/off 1차 실패 항목: no_lost_treatment, no_individual_treatment_worsening, no_individual_final_arrival_worsening, no_case_total_wait_increase, no_case_capped_treatment_increase, inquiry_ratio_bounded, strict_total_wait_reduction, no_case_inquiry_increase, strict_capped_treatment_reduction. safe/protected 포함 배포 기준: 미통과.
- C1/renewed 1차 실패 항목: no_lost_treatment, no_individual_treatment_worsening, no_individual_final_arrival_worsening, no_case_total_wait_increase, no_case_capped_treatment_increase, inquiry_ratio_bounded, strict_total_wait_reduction, no_case_inquiry_increase, strict_capped_treatment_reduction. safe/protected 포함 배포 기준: 미통과.
- C1/off 1차 실패 항목: no_lost_treatment, no_individual_treatment_worsening, no_individual_final_arrival_worsening, no_case_total_wait_increase, no_case_capped_treatment_increase, inquiry_ratio_bounded, strict_total_wait_reduction, no_case_inquiry_increase, strict_capped_treatment_reduction. safe/protected 포함 배포 기준: 미통과.

개발 입력을 이미 관측했다는 점과 가족/시드 반복이 독립 임상 표본이 아니라는 점을 명시한다. 범용 우월성이나 임상 효과, 서로 다른 시점의 CPU 속도 개선을 주장하지 않는다. C1의 실제 50ms solver 결과와 fallback은 원문 그대로 보존하며 재실행·제외하지 않았다.

## 다음 순서

다음 두 기제는 아직 구현·성능 검증하지 않았으며, 실패한 억제안과 섞지 않고 유지한 normal 기준에서 각각 별도로 비교한다.

1. **유효 수용 응답 뒤의 불필요 대기 축소.** 유효한 수용 응답이 실제 pending 문의들보다 이동시간에서 불리하지 않을 때 확정 준비를 허용하는 규칙을 먼저 검토한다. 기존 TTL·자원 신선도·공유 용량·임상 우선순위·기회 보호 조건을 모두 유지하고, 이미 제출된 병원 업무도 원장에 남긴다. 미문의 후보·후발 환자 경합·관측되지 않은 미래 중단 때문에 빠른 확정이 악화되는 경계를 함께 검사한다. 이번 12건 상실의 최초 원인은 준비 대기가 아니므로 이를 그 사례의 구제책이라고 주장하지 않는다.
2. **문의 후보가 있는데 진행을 못 하는 경우 해소.** 전체 최소 접촉 tier와 offered 후보의 교집합이 없어 멈추는 경우, 원 offered 후보 안에서 진행을 허용하는 별도 규칙이다. 현재 확인한 원문에는 남은 후보가 모두 무응답인 사례도 있어, 문의량만 늘어나는 반례를 함께 검사해야 한다.

## 증거와 구현

- `simulation/probe_guard_policy.py`, `probe_guard_engine.py`: 새 순수 규칙과 실제 재생 wrapper.
- `simulation/probe_guard_audit.py`: 새 엔진을 import하지 않는 독립 이력 재구축. 동일한 전체 branch를 기존 물리·배정·FIFO·실제 업무 감사에 전달한다.
- `simulation/probe_guard_experiment.py`, `probe_guard_scenarios.py`: 사전 고정 비교·선택 및 경계 입력.
- `simulation_results/probe_guard_development_20261020.json` 및 같은 이름의 `_cases/`: 전체 신규 원문과 저장 참조 증명 연결.
- `.codex-build/next-improvements-20261020/development-independent-audit.json`: 전체 원문 재감사, 이벤트 기반 순대기·실제 처치/도착·실제 문의 업무 변화.
- 같은 build의 `causal-audit.json`, `causal-losses.json`: 저장된 원문의 사건·업무·관측·solver 기록에 근거한 사후 원인 분석. 정책 입력이나 추가 엔진 실행으로 사용하지 않았다.
- 같은 build의 `pre-development-freeze.json`, `seed-audit/`, `preservation/`, `verification.json`: 사전 고정·노출 검사·과거 바이트 보존·최종 봉인.

초기 seed7 도달 선언 실패와 설명 메타데이터 정정, 기존 resource-event wake로 특정 병원을 tried 목록에서 제거하는 동작을 새 감사가 누락한 문제는 별도 기록으로 남겼다. 감사만 보정한 뒤 저장한 445분기를 모두 다시 감사하고 나머지 95분기를 이어 실행했다. 원 C1 실행 결과를 다시 돌리거나 제외하지 않았다. 최종 성능 비교 전에 전체 검사를 다시 통과했으며 규칙·성과를 검증 데이터로 조정하지 않았다.
