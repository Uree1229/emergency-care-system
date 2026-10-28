# 재문의 순회·간격·동시 문의 배분 개선

2026-10-18. 중단됐던 앞선 독립 감사를 동일 소스·동일 원시 기록으로 처음부터 완료하고, 새 재문의 정책을 분리 구현해 개발·미사용 검증 입력에서 평가했다. 앞선 결과는 보존했다.

개발에서 고정한 후보의 새 합성 검증 결과는 다음과 같다. 모두 같은 기반의 직전 capability 대비이며, 실제 임상 효과나 고정 3곳 대비 우월성의 주장이 아니다.

- H1: frontier, 문의 -432건, 순대기 -535.50환자·분, 새 처치 12명, 기존 처치 상실 0명, 처치 지표 악화 0명.
- C1: frontier, 문의 -432건, 순대기 -535.50환자·분, 새 처치 12명, 기존 처치 상실 0명, 처치 지표 악화 0명.

연구 선택과 운영 반영은 구분했다. 전체 반영 기준의 충족 여부와 남은 악화는 아래에 공개하며 기본 fixed_3는 유지했다.

## 바꾼 판단

문의 회차가 갱신되면 앞쪽 병원부터 다시 시작해 뒤쪽 후보에 닿지 못하는 현상을 출발점으로 삼았다. 새 판단은 최초 수용 응답을 받기 전의 미확정 탐색에만 적용하며, 수용·확정 이후에는 기존 동작을 따른다.

- **frontier:** 현재 환자 조건에 대한 실제 접촉 이력을 회차 너머로 유지해 미탐색·최소 접촉 후보를 먼저 찾는다.
- **paced:** frontier에 더해 동일 내용의 정기 보고가 기존 재문의 대기 시점을 취소하지 못하게 한다. 관측 자원 내용의 변경이나 미확인·오래된 정보의 최신화는 구분한다. 새 지수형 대기시간을 추가한 것은 아니다.
- **balanced:** paced 후보 안에서 현재 공개 원장의 미해결 문의가 적은 병원을 먼저 선택한다.

환자별 미해결 문의 3건·환자별 회차당 실제 접촉 10회 한도, 제어 접촉과 실제 심사 시도의 구분, 원래 문의 만료·수용 유효기간, FIFO 작업량·작업자 수·물리 자원·입력 우선순위를 유지했다. 미래 환자·회신 소요시간·숨은 작업 잔량은 판단에 제공하지 않았다.

## 평가와 선택

주 비교 대상은 같은 기반 기능의 직전 capability 후보다. 기존 fixed3에는 회차당 10회 제한이 없고 일부 기반 기능도 달라 전체 스택 참고 비교로만 제시한다. 두 방식의 차이를 새 문의 규칙의 순수 효과로 해석하지 않는다.

기존 95개 환경을 그대로 두고 경계 환경 14개를 추가했다. 새 환경은 마지막 후보 탐색·전체 무응답·두 번째 회신·거부 후 회복·3분 기한 전/일치/후·느린 실제 작업·8명 동시 요청·우선순위·미래 등록·지속 미확인/오래된 보고·역순 보고를 다룬다.

- 개발: 44101–44103, 327환경 × 18분기 = 5,886회. 세 후보를 모두 비교했다.
- 새 검증: 45101–45106, 654환경 × 14분기 = 9,156회. 개발에서 고정한 H1/C1 후보만 평가했다.
- 소스 146개를 고정한 뒤 981개 입력 해시를 생성했다. 예약 시드의 선행 사용을 검사했고 검증 결과로 재선택·재튜닝하지 않았다.

개인별 처치·최종 도착 비악화, 기존 처치 성공 보존, 환경별 전체 순대기·제한 처치 합 비증가, 환경별 문의 비증가, 전체 문의와 전체 순대기의 엄격한 감소를 주 기준으로 삼았다. 개인 순대기는 별도 공개하며 개인 비증가 gate로 사용하지 않았다. 기존 비교군에 대한 추가 반영 기준도 유지했다. safe·cooperative에는 개인 도착·처치 조건까지 적용하고, prototype·protected에는 성공 보존·환경별 전체 순대기/제한 처치 합·문의 비용·총 순대기 감소 조건을 적용한다. 아래 미충족 체크는 진단 항목 전체이며 prototype·protected의 개인 악화 자체는 전체 반영 기준의 별도 gate로 사용하지 않는다.

| 배정 | 개발 고정 후보 | 개발 주 기준 | 개발 전체 반영 기준 | 검증 주 기준 | 검증 전체 반영 기준 |
| --- | --- | --- | --- | --- | --- |
| H1 | frontier | True | False | True | False |
| C1 | frontier | True | False | True | False |

- H1 개발 주 기준 미충족: 없음. 기존 비교군 체크 미충족: safe:no_individual_treatment_worsening, safe:no_individual_final_arrival_worsening, safe:no_case_total_wait_increase, safe:no_case_capped_treatment_increase, probe:no_individual_treatment_worsening, probe:no_individual_final_arrival_worsening, probe:no_case_total_wait_increase, baseline:no_individual_treatment_worsening, baseline:no_individual_final_arrival_worsening, baseline:no_case_total_wait_increase, baseline:no_case_capped_treatment_increase.
- H1 검증 주 기준 미충족: 없음. 기존 비교군 체크 미충족: safe:no_individual_treatment_worsening, safe:no_individual_final_arrival_worsening, safe:no_case_total_wait_increase, safe:no_case_capped_treatment_increase, probe:no_individual_treatment_worsening, probe:no_individual_final_arrival_worsening, probe:no_case_total_wait_increase, baseline:no_individual_treatment_worsening, baseline:no_individual_final_arrival_worsening, baseline:no_case_total_wait_increase, baseline:no_case_capped_treatment_increase.
- C1 개발 주 기준 미충족: 없음. 기존 비교군 체크 미충족: safe:no_individual_treatment_worsening, safe:no_individual_final_arrival_worsening, safe:no_case_total_wait_increase, safe:no_case_capped_treatment_increase, probe:no_individual_treatment_worsening, probe:no_individual_final_arrival_worsening, probe:no_case_total_wait_increase, baseline:no_individual_treatment_worsening, baseline:no_individual_final_arrival_worsening, baseline:no_case_total_wait_increase, baseline:no_case_capped_treatment_increase.
- C1 검증 주 기준 미충족: 없음. 기존 비교군 체크 미충족: safe:no_individual_treatment_worsening, safe:no_individual_final_arrival_worsening, safe:no_case_total_wait_increase, safe:no_case_capped_treatment_increase, probe:no_individual_treatment_worsening, probe:no_individual_final_arrival_worsening, probe:no_case_total_wait_increase, baseline:no_individual_treatment_worsening, baseline:no_individual_final_arrival_worsening, baseline:no_case_total_wait_increase, baseline:no_case_capped_treatment_increase.

값이 false인 기준은 미충족으로 보존했다. 운영 서버·화면·DB와 기본 문의 정책 fixed_3는 변경하지 않았다. 연구 후보 선택과 운영 반영은 별개이며 adopted는 false다.

## 개발 후보 비교

| 배정 | 후보 | 처치 지표 개선 | 처치 지표 악화 | 기존 성공 상실 | 전체 순대기 변화 | 대기 증가 환경 | 문의 변화 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1 | frontier | 6 | 0 | 0 | -267.75 | 0 | -216 |
| H1 | paced | 6 | 23 | 0 | -217.25 | 23 | -452 |
| H1 | balanced | 43 | 50 | 3 | -458.75 | 30 | -577 |
| C1 | frontier | 6 | 0 | 0 | -267.75 | 0 | -216 |
| C1 | paced | 6 | 23 | 0 | -217.25 | 23 | -452 |
| C1 | balanced | 43 | 50 | 3 | -458.75 | 30 | -577 |

개발 원문 검토에서 paced는 마지막 접촉과 현재 사용량이 같아진 회복을 놓쳤다. periodic_release_long_retry의 중간 used=1 이후 used=0 복귀를 기다림 해제 사유로 보존하지 못해 처치가 12분에서 17분으로 늦어졌다. balanced는 느린 심사 환경에서 새 처치를 얻는 대신 기존 처치 기회를 잃었다. 이 두 후보는 검증으로 넘기지 않았다. balanced의 개발 최대 제한 지표 악화 49.25분은 미처치를 관찰 종료로 제한한 차이이며 실제 관측된 지연시간이 아니다.


## 새 검증: 고정 후보와 모든 참고군

| 배정 | 기준 | 처치 단축 | 동일 | 처치 지표 악화 | 제한 처치 최대 악화(분) | 최종 도착 지표 악화 | 제한 도착 최대 악화(분) | 기존 성공 상실 | 새 성공 | 순대기 변화(환자·분) | 대기 증가 환경 | 문의 변화 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| H1 | current | 12 | 1284 | 0 | 0.00 | 0 | 0.00 | 0 | 12 | -535.50 | 0 | -432 |
| H1 | safe | 90 | 1200 | 6 | 1.00 | 6 | 1.00 | 0 | 12 | -609.00 | 6 | -384 |
| H1 | cooperative | 24 | 1272 | 0 | 0.00 | 0 | 0.00 | 0 | 12 | -559.50 | 0 | -426 |
| H1 | prototype | 60 | 1218 | 18 | 1.00 | 18 | 1.00 | 0 | 12 | -541.50 | 24 | -402 |
| H1 | protected | 114 | 1170 | 12 | 17.00 | 12 | 17.00 | 0 | 12 | -630.50 | 6 | -378 |
| H1 | fixed3_reference | 224 | 750 | 322 | 56.50 | 322 | 56.50 | 18 | 25 | -37.00 | 219 | -240 |
| C1 | current | 12 | 1284 | 0 | 0.00 | 0 | 0.00 | 0 | 12 | -535.50 | 0 | -432 |
| C1 | safe | 90 | 1200 | 6 | 1.00 | 6 | 1.00 | 0 | 12 | -609.00 | 6 | -384 |
| C1 | cooperative | 24 | 1272 | 0 | 0.00 | 0 | 0.00 | 0 | 12 | -559.50 | 0 | -426 |
| C1 | prototype | 60 | 1218 | 18 | 1.00 | 18 | 1.00 | 0 | 12 | -541.50 | 24 | -402 |
| C1 | protected | 115 | 1168 | 13 | 17.00 | 13 | 17.00 | 0 | 12 | -670.50 | 6 | -379 |
| C1 | fixed3_reference | 224 | 750 | 322 | 56.50 | 322 | 56.50 | 18 | 25 | -37.00 | 219 | -240 |

단축·지연은 동일 입력 환자의 요청→처치 시각을 비교한다. 미처치는 동일 관찰 종료로 제한한다. 성공 상실이 포함된 제한 지표의 최대 악화는 실제 관측한 처치·도착 지연시간이 아니다. 같은 입력을 H1/C1로 반복한 결과를 서로 독립인 임상 사례처럼 합산하지 않는다.

safe 대비 1분 악화 6건은 새 거부 후 회복 환경에서 current와 frontier가 동일하게 갖는 실패다. prototype 대비 1분 악화 18명과 보호 기준 대비 최대 17분 악화도 기존 기반에서 이어졌다. 직전 후보를 개선했다는 결과를 과거 모든 기준보다 우수하다는 의미로 확장하지 않는다.

검증 45105의 out_of_order에서는 보호 기준 C1의 원래 50ms 탐색 예산이 stage 1 전에 소진돼 원래 greedy 대안이 실행됐다. 그 기준의 P02는 10분·P03는 50분에 처치됐고, current와 frontier는 둘 다 P02 15분·P03 10분이었다. 보호 기준 대비 C1에 추가로 보이는 P02 5분 악화는 신규 문의 정책의 추가 악화가 아니라 원본 배정기의 시간 예산 대안이 섞인 참고군 차이다. 이 실행을 다시 돌리거나 제외하지 않았다.

## 실제 작업·문의 총계

| 분기 | 환자 | 미처치 | 순대기 합 | 제한 처치 합 | 실제 문의 | 반복 접촉 | 자원 재확인 요청 | 환자 병원대기 | 모형 내 문의 심사 작업량 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v4_H1_capability | 1296 | 131 | 14073.75 | 18896.25 | 5619 | 2971 | 1121 | 0.00 | 3574.00 |
| retry_H1_frontier | 1296 | 119 | 13538.25 | 18648.75 | 5187 | 2503 | 1121 | 0.00 | 3412.00 |
| v4_C1_capability | 1296 | 131 | 14073.75 | 18896.25 | 5619 | 2971 | 1121 | 0.00 | 3574.00 |
| retry_C1_frontier | 1296 | 119 | 13538.25 | 18648.75 | 5187 | 2503 | 1121 | 0.00 | 3412.00 |

마지막 작업량은 큐가 있는 환경에서 관찰 종료까지 실제 수행한 문의 심사 시간이며 배경 작업은 제외한다. 큐 없는 독립 응답 환경의 기본값 0은 실제 검토 노동이 없다는 뜻이 아니다.

### 병원 문의 심사 큐의 별도 진단

| 분기 | 큐 기록 있는 환경 | 문의 심사 큐 대기 합 | 배경 포함 수행 작업시간 | 제출 작업 | 완료 | 종료 시 대기 | 종료 시 처리 중 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v4_H1_capability | 372 | 23427.50 | 3838.00 | 4370 | 3661 | 679 | 30 |
| retry_H1_frontier | 372 | 23426.00 | 3676.00 | 3938 | 3229 | 679 | 30 |
| v4_C1_capability | 372 | 23427.50 | 3838.00 | 4370 | 3661 | 679 | 30 |
| retry_C1_frontier | 372 | 23426.00 | 3676.00 | 3938 | 3229 | 679 | 30 |

이 표는 전체 환자 순대기와 다른 지표다. 저장된 원시 큐 보고에서 다시 합산한 사후 진단이며 선택 기준으로 추가하지 않았다. 큐 기록이 없는 환경은 합계에서 0으로 처리하되 노동·부하가 0이라고 주장하지 않는다. 작업 수와 시간에는 원래 배경 작업도 포함한다.


반복 접촉은 같은 환자·병원에 두 번째 이후 보낸 모든 실제 문의다. 치료조건 변경이나 타임아웃 후 필요한 재문의도 포함하며 전부 낭비로 보지 않는다. 추가 자원 변화 푸시는 없지만, 실제 문의·재확인 행동이 달라지면 기존 채널의 요청 수와 수신 시점도 달라질 수 있다.

## 악화와 개인 순대기 공개

- H1, 직전 후보 대비: 개인 순대기 증가 0명, 최대 0.00분. 처치 지연·성공 상실·환경 순대기/문의 증가가 있는 환경 0개.
- C1, 직전 후보 대비: 개인 순대기 증가 0명, 최대 0.00분. 처치 지연·성공 상실·환경 순대기/문의 증가가 있는 환경 0개.

순대기는 이동을 제외한 탐색·병원 대기·전원 준비의 합이다. 가까운 병원의 회신을 더 기다리면 순대기는 늘면서 도착·처치는 빨라질 수 있어 지표를 합쳐 해석하지 않는다. 모든 환자별 차이와 모든 비교군의 악화는 analysis-validation.json과 원 보고서에 남겼다.

## 회귀·독립 감사와 범위

- 중단됐던 앞선 검증 전체 570환경·6,840분기의 독립 감사를 동일 소스로 완료하고 앞선 결과를 봉인했다.
- 기존 지연 12건과 앞선 19개 입력에서 새 세 전략 × H1/C1 = 186분기를 실행했다. 직전 capability의 개인 처치·최종 도착·전체 순대기·문의 결과를 모두 보존했고 별도 원문 감사도 통과했다.
- 2,028개 검사 통과. 앞선 실험과 동일한 선택적 로컬 HTTP 바인딩 검사 6개만 제외했다.
- 작은 시드 7의 109환경 × 6후보 = 654분기를 먼저 실행·저장하고 원문 감사를 통과했다. 이 스모크는 새 모집단 효과의 증거로 합치지 않는다.
- 개발·검증 모두 저장한 전체 원문을 별도 프로세스에서 감사했다. 감사는 엔진·솔버를 import하거나 재실행하지 않는다. 원래 판단 입력을 삭제하거나 기존 정책으로 위장한 투영 기록을 만들지 않는다.
- 동결 후 만든 사후 큐 집계 도구의 첫 실행은 닫는 괄호 하나의 문법 오류로 파싱 단계에서 실패했다. 실패 원문과 수정 전후 해시를 보존하고 구문 검사 후 다시 실행했다. 이 도구는 정책 선택 기준에 사용하지 않았다.
- 독립 감사는 원래 보고 이벤트의 previous_retry_at와 새 판단 기록의 1:1 연결·시간 조건·정책 재현을 검사한다. 원엔진 내부 retry_at의 모든 설정·취소 전이를 독립 재구성한 증명까지 포함하지는 않는다.
- 앞선 파일 6,214개, 부모 소스 136개를 보존하고 신규 과학 소스 10개만 추가했다. 스모크 실행기의 TEMP가 앞선 빈 tmp 폴더를 가리킨 실수는 기록했고, 앞선 폴더 파일 추가 0개·해시 변경 0개를 확인한 뒤 새 폴더로 바로잡았다.

환자·병원·응답·자원·이동시간은 합성 모형이다. 우선순위는 입력 가중치이며 KTAS를 자동 추론하지 않는다. 15초 재평가, 협력형 부모의 ACK 지연·작업 전환 비용 0분 가정은 남아 있다. 실제 임상 효과나 범용 최적성을 주장하지 않는다.

## 근거 파일

- `simulation/retry_dispatch_{policy,engine,audit,scenarios,experiment}.py`와 대응 검사 5개
- `simulation_results/retry_dispatch_development_20261018.json` / `.md` 및 `_cases/`
- `simulation_results/retry_dispatch_validation_20261018.json` / `.md` 및 `_cases/`
- `config/retry_dispatch_lock_20261018.json`
- `.codex-build/retry-dispatch-20261018/analysis-{development,validation}.json`
- 같은 폴더의 개발·검증 독립 감사, 사전 동결·입력·시드·보존·스모크·과거 회귀 증거와 최종 `verification.json`
