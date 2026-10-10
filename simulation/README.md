# 배정 알고리즘 비교 시뮬레이터

발표 대본(2026-10-10) 6·7·9장과 배정 알고리즘 검증계획(`outputs/배정알고리즘_검증계획_20261010.txt`, 로컬 파일)에 맞춘 비교 도구입니다.
모든 사건과 분포는 가상이며, 결과는 "정의한 시뮬레이션 조건에서의 정책 비교"입니다. 현장 효과의 증거가 아닙니다.

## 비교 축 (서로 독립적으로 조합)

| 축 | 선택지 | 대본 |
|---|---|---|
| 배정 규칙 | R0 첫 유효 수용 · R1 예상 도착 우선 · R2 자원 경쟁 고려 · R3 다수 환자 동시 계산 | 6·9장 |
| 문의 정책 | 동시 문의 수 `k`, 확정 대기 `wait`(첫 수용 뒤 남은 응답을 기다리는 분), 초기 탐색 반경 `near_minutes`, 시간 기준 확대 `expand_after` | 6·9장 |
| 조건 변경 대응 | `notify` 켬/끔. 돌발 유입·장비 일시 사용 불가 시 그 자원이 필요한 이송 중 환자만 재확인·대체 문의 | 7·9장 |

```python
from simulation.sim import Policy, Simulation
Simulation(seed=3, policy=Policy(rule='R1', k=3, wait=2, near_minutes=15)).run()
```

- R3는 같은 분에 확정할 환자들을 묶어 정확히 열거합니다(미배정 우선순위 합 → 우선순위 가중 도착 합). 묶음이 크면 R2로 대체하고 `joint_fallbacks`에 셉니다. CP-SAT(`solver_improved.py`) 연결 자리는 `SolverPolicy`(B2)로 남겨 두었습니다.
- 기존 B0(순차)·B1(병렬+탐욕+알림)은 그대로이며 결과가 바뀌지 않았음을 테스트로 확인합니다.

## 실행 순서 (검증계획 5단계)

```
python -m simulation.experiment compare                 # R0~R3 비교 (train)
python -m simulation.experiment tune --rule R1          # k·wait·반경 조정 (train → validation 확인)
python -m simulation.experiment freeze --rule R1 --k 3 --wait 0 --radius 15
python -m simulation.experiment ablate                  # 조건 변경 대응 켬/끔 (validation)
python -m simulation.experiment final                   # 고정한 정책을 test seed로 한 번 평가
python -m simulation.experiment trace --seed 3          # 같은 사건을 두 정책으로 재생한 사건 기록(JSON)
```

결과는 `simulation_results/experiments/`에 JSON과 표(md)로 저장됩니다.

- seed는 `datasets.io.partition`과 같은 train 80% / validation 10% / test 10%로 나눕니다.
- 차이는 같은 seed·시나리오끼리 짝지어(정책 − 기준) 평균, 95% 구간, 나빠진 seed 비율을 함께 보고합니다.
- 공식 전북 병원 선정시간(8분 40초)은 현실성 점검용 참고값이며 보정·개선율 계산에 쓰지 않습니다.
- `final`은 `freeze`로 고정한 정책만 평가합니다. 결과를 보고 다시 조정하면 그 test seed는 개발용으로 취급합니다.

시나리오: `normal`, `slow_response`(응답 중앙값 6분), `low_response`(미응답 52%), `surge`(환자 3배·초기 점유 85%),
`resource_change`(장비 일시 사용 불가·돌발 유입 증가), `compound`(복합).

## 지금까지의 결과 (가상, 2026-10-10)

**규칙 비교** (train 160 seed, 공통 문의 정책: 동시 3곳·확정 대기 2분, 기준 R0)
- R1(예상 도착 우선)은 R0보다 최종 병원 도착이 시나리오별 평균 1.0~1.8분 빨랐습니다. 확정 시간은 거의 같았습니다.
- R2·R3는 R1과 비슷했고, 환자가 몰리는 조건에서만 R2가 0.1분 정도 더 나았습니다. 지금 모델에서는 같은 분에 확정되는 환자 묶음이 작아 R3의 이점이 거의 드러나지 않습니다.
- 확정 대기가 0이면 R0과 R1은 결과가 같습니다(같은 분에 온 응답 중에서만 고르기 때문).

**문의 정책 조정** (R1, train 30 seed로 조정)
- 시나리오가 바뀌어도 손해가 가장 작은 설정은 동시 3곳·대기 0분·반경 15분이었습니다(환자당 문의 2.88건).
- validation에서 기준(R0·동시 3곳·대기 0) 대비 평상시 도착 −0.6분, 확정 +0.7분이었고, **응답률이 낮은 조건에서는 도착이 +0.6분으로 오히려 나빠졌습니다.**
- 동시 1곳(순차)은 모든 조건에서 가장 나빴습니다(최대 손해 8.7분).

**조건 변경 대응**: 지금 시나리오에서 알림에 따른 목적지 변경은 회당 0.3~0.8건이라 효과 크기가 작습니다. `ablate`로 켬/끔 차이를 확인해야 합니다.

## 아직 반영하지 않은 것
- 초 단위 사건 처리(현재 분 단위). 대본 6장의 "10초 뒤/20초 뒤" 예시는 분 단위로 단순화해 시험합니다.
- 문의가 몰릴 때 병원 응답이 늦어지는 효과(병원별 업무 대기열)
- 병원 간 전원(도착 후 자리가 없으면 그 병원에서 대기)
- CP-SAT 솔버 연결(B2), 실제 병원·도로 데이터
- 재생 화면(현재는 사건 기록 JSON까지)
