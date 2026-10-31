# 배정 알고리즘 비교 시뮬레이터

발표 대본(2026-10-10) 6·7·9장과 배정 알고리즘 검증계획(`outputs/배정알고리즘_검증계획_20261010.txt`, 로컬 파일)에 맞춘 비교 도구입니다.
모든 사건과 분포는 가상이며, 결과는 "정의한 시뮬레이션 조건에서의 정책 비교"입니다. 현장 효과의 증거가 아닙니다.

## 비교 축 (서로 독립적으로 조합)

| 축 | 선택지 | 대본 |
|---|---|---|
| 배정 규칙 | R0 첫 유효 수용 · R1 예상 도착 우선 · R2 자원 경쟁 고려 · R3 가중합 공동 배정 · R4 우선순위별 사전식 공동 배정 | 6·9장 |
| 문의 정책 | 동시 문의 수 `k`, 확정 대기 `wait`(첫 수용 뒤 남은 응답을 기다리는 분), 초기 탐색 반경 `near_minutes`, 시간 기준 확대 `expand_after` | 6·9장 |
| 조건 변경 대응 | `notify` 켬/끔. 돌발 유입·장비 일시 사용 불가 시 그 자원이 필요한 이송 중 환자만 재확인·대체 문의 | 7·9장 |

```python
from simulation.sim import Policy, Simulation
Simulation(seed=3, policy=Policy(rule='R1', k=3, wait=2, near_minutes=15)).run()
```

- R3는 같은 분에 확정할 환자들을 묶어 정확히 열거합니다(미배정 우선순위 합 → 우선순위 가중 이동시간 합). 묶음이 크면 R2로 대체하고 `joint_fallbacks`에 셉니다. 같은 목적의 CP-SAT은 `Policy(rule='C1')`으로 연결했습니다. 현재 입력의 배정 비교·개발/검증 분리·최적성 기록은 [CP-SAT 비교](../docs/ALLOCATION_BENCHMARK.md)를 참고하세요. 예전 `SolverPolicy`는 별도 단일결정 확장 자리입니다.
- R4는 같은 분의 유효 수용 응답이 준비된 환자만 대상으로 우선순위 4→2→1의 미배정 수, 이후 각 군의 예상 이동시간 합을 사전식으로 최소화합니다. 조합 20,000개를 넘으면 우선순위 순 최근접 배정으로 대체합니다. 이때 정확한 최적해는 보장하지 않습니다. 우선순위는 임상 분류가 아닌 실험값이며 높은 군 보호가 전체 치료 인원 최대화와 충돌할 수 있습니다.
- `priority_order='requests'`는 같은 분의 신규 문의만, `'all'`은 이미 발생한 환자의 문의·회신·재문의 처리 순서도 우선순위로 정합니다. 이미 제출한 병원 FIFO 작업과 확정된 이송은 취소하거나 선점하지 않습니다. `high_priority_hedge_after`는 관측형 정책의 실험 우선순위4 추가 문의 검토 시점만 바꿉니다. 기본값은 이전 정책과 같습니다.
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
- 기존 대량 비교 엔진의 병원 간 전원(도착 후 자리가 없으면 그 병원에서 대기). 아래 지도 재생 확장에서 별도 구현
- C1 CP-SAT의 고정 입력 비교와 전체 흐름9조건 지도 비교를 완료했습니다. [전체 이송 결과](../docs/ONLINE_ALLOCATION.md). 다음은50/100명 규모·짧은 계산 제한·정보 누락입니다. 기존 대량 비교 엔진의 실제 병원·도로 데이터 적용은 별도 과제입니다(아래 기존 지도 재생에는 연결).

## 지도에서 전체 이송 과정 재생

`map_replay.py`는 기존 문의·응답·선택 엔진을 재사용하고 119 출동, 현장 도착·처치, 이동 궤적, 도착 후 전원 절차와 합성 치료 점유 종료 기록을 추가한다. `viewer/`는 그 기록을 공통 시간축으로 재생한다. 기존 `simulation.experiment`의 대량 비교 결과와는 별도의 시연 입력이다.

저장소 루트의 **`RUN_MAP_SIMULATION.cmd`**를 실행하거나 다음 명령을 사용한다. Python 표준 라이브러리만 필요하다.

```powershell
python -m simulation.viewer_server --reuse-data
```

주소는 `http://127.0.0.1:18005/`이며 기존 병원·모바일·공통 API 서버와 독립적으로 실행된다. `--port 18006`으로 포트를 바꿀 수 있다. `--no-browser`는 자동 브라우저 열기를 생략한다. `--reuse-data`는 저장된 고정 시나리오를 그대로 사용하므로 공공 DB나 API 키 없이 재생할 수 있다.

화면은 지도·재생바와 정책 비교 패널로 구성된다. 우측에서 환자를 선택하고, 각 정책의 **진행 상황 · 판단 근거**를 펼쳐 응답 근거·환자별 상태·정책 설정을 확인한다. 지도는 휠 또는 `+`/`−` 버튼으로 확대·축소하고 전체 보기 버튼으로 되돌린다. 입력 컨트롤 밖에서 `Space`는 재생·일시정지, 좌우 방향키는 1분 이동이다. 상단 메뉴로 결과 비교와 실험 기록에 이동할 수 있다. 좁은 화면에서는 비교 패널이 지도 아래로 이동한다.

새 화면은 같은 서버의 `/lab.html`이다. **지도 재생 · 결과 비교 · 발표** 세 탭으로 나뉘고, 비교·발표 내용은 저장 기록에서 자동으로 만든다. 기존 화면을 대체하기 전 시험판이다. [새 화면 안내](../docs/SIM_VIEWER_LAB_20261030.md)

### 시연 흐름과 비교 조건

1. 고정 위치에서 가상 환자 발생 → 구급차 출동 → 119 현장 도착 → 현장 처치 완료.
2. 같은 환자·잠재 응답 환경·자원 변화 사건을 복사한 독립 분기로 나뉨.
3. 각 정책의 병원 문의·수용 응답·확정·구급차 이동을 같은 지도에 표시.
4. 자원 변화 시 재확인·새 수용 확인 후 목적지 변경. 도착 후 치료 불가를 확인한 경우 공통 준비시간 뒤 전원 탐색·수용 확인·병원 간 이동.
5. 최종 병원 도착과 합성 치료 시작·종료, 문의 수·목적지 변경·전원 횟수를 비교.

| 분기 | 문의 수 | 첫 유효 수용 이후 대기 | 선택 규칙 | 변경 알림 |
|---|---:|---:|---|---|
| 순차 문의 | 1곳 | 0분 | R0: 첫 수용 | 켬 |
| 병렬 문의·첫 수용 순서 | 3곳 | 최대 2분 | R0: 수집한 유효 응답 중 첫 수용 순서 | 켬 |
| 병렬 문의·예상 도착 우선 | 3곳 | 최대 2분 | R1: 수집한 유효 응답 중 예상 이동시간 우선 | 켬 |

두 병렬 분기는 같은 문의·대기 조건에서 선택 규칙만 비교한다. 순차와 병렬의 차이는 정책 묶음의 차이이며, 알고리즘 효과 하나로 해석하지 않는다. 기본 분기에 모두 변경 알림을 제공하므로 현행 시스템에 알림 기능이 없다고 가정하지 않는다. CP-SAT 최적화 솔버는 이 재생에 아직 연결하지 않았다.

기본 시나리오는 단일 환자 4개와 다수 환자 4개다. 저장된 화면에는 반복 실험에서 골라낸 개선·악화 사례 2개도 들어 있다. 교육·발표용으로 조건을 배치한 사례이며 대표적인 발생 빈도를 뜻하지 않는다. 더 늦어지거나 미배정인 결과도 유지한다. 화면의 결과는 현재 재생 시각까지 발생한 사건부터 공개한다.

### 실제 데이터와 가정

- 실제 자료: 공개 DB에서 가져온 병원 이름·좌표 8곳, geoBoundaries의 2020년 행정경계, OSRM/OpenStreetMap의 자동차 도로 경로·거리·예상시간. 경계 출처·라이선스는 [지도 배경 안내](viewer/assets/README.md).
- 합성 자료: 환자·진료 역량·자원·응답 지연·출동 지연 및 현장처치 시간·돌발 변화·치료 점유시간·전원 준비시간.
- 도로 형상과 예상시간은 출동, 후보 정렬·선택, 이송, 목적지 변경, 전원에 모두 사용한다. 목적지 변경은 기존 도로 위 현재 위치에서 새 경로를 구하고, 이전 이동 흔적은 그 지점까지 자른다. 각 분기는 같은 도로 캐시를 공유한다.
- 자동차 예상시간은 실시간 교통·119 우선주행·구급차 실측시간이 아니다. 분 단위 올림으로 이동시간을 반영하고, 구간 안의 위치는 누적거리 비례로 보간하므로 구간별 정지·가감속까지 재현하지 않는다.
- 환자·병원 좌표와 길찾기의 도로 접속점이 다르면 접속 구간을 점선으로 표시하고 시속 10km 추정시간을 별도로 더한다. 이 구간은 실제 진입도로를 확보한 것이 아니다. 도로 경로는 실선, 명시적으로 선택한 조회 실패 대체는 직선 점선이다.
- 병원에 부여한 가상 역량·수용 상태는 해당 실제 기관의 운영 상태를 뜻하지 않는다. 치료 시작·종료는 자원 점유 기록이며 회복·생존·예후를 뜻하지 않는다.
- 한 사례의 가시적 재생으로 임상 효과나 기존 119 체계 대비 개선율을 검증할 수 없다. 이후 같은 시나리오 집합에서 여러 seed의 결과를 비교하고 실패·악화 사례를 함께 평가한다.

### 데이터 다시 생성 또는 사용자 시나리오 사용

```powershell
# 공개 DB 이름·좌표를 읽기 전용 조회하고 OSRM 도로 경로·예상시간 연결
python -m simulation.map_replay --routing osrm

# 저장된 도로 snapshot만 사용해 재생 JSON 재생성 (외부 호출 없음)
python -m simulation.map_replay --routing osrm --offline

# 반복 실험의 설명용 개선·악화 사례도 추가 (기본 8개 + 2개)
python -m simulation.map_replay --offline --experiment-replays simulation_results/map_comparison_replays.json

# 다수 환자 4개만 / 단일 환자 4개만 생성
python -m simulation.map_replay --offline --suite multi
python -m simulation.map_replay --offline --suite single

# 기존 생성기 입력 구조에 맞춘 고정 시나리오 JSON 사용
python -m simulation.map_replay --scenario path/to/scenarios.json --routing osrm

# 사용자 입력의 출동 소요시간도 도로 예상시간으로 재계산, 현장처치 간격 유지
python -m simulation.map_replay --scenario path/to/scenarios.json --dispatch-timing route

# 이전 직선 추정 모델을 별도 비교 파일로 명시 생성
python -m simulation.map_replay --routing synthetic --output simulation/viewer/synthetic_replay.json

# 재생 관련 불변조건과 기존 시뮬레이터 회귀 검사
python -m unittest test_map_replay test_map_road_routes test_road_routes test_simulation -v
```

공개 DB가 없는 다른 PC에서는 생성 명령 대신 저장된 JSON을 `--reuse-data`로 재생한다. 외부 지도·API 호출 없이 재생 가능하며, 실제 병원 웹·모바일 화면의 입력과 자동 동기화되지는 않는다.

### 도로 조회와 재현성

`road_routes.py`는 인증키 없이 공개 OSRM 데모를 조회한다. [공식 운영정책](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server)에 따라 요청 시작 사이에 최소 1.1초를 두고, 방향·좌표·제공자별 응답을 `route_cache/`에 고정 저장한다. 공개 서버는 발표용 소규모 수집에만 사용하고 대량 알고리즘 실험에는 자체 도로 서버나 수집된 snapshot을 사용한다. API에는 가상 출발지·공개 병원 좌표만 보내며 환자 상태·신원은 보내지 않는다.

실패 시 기본 동작은 생성 중단이며 기존 재생 JSON을 유지한다. `--allow-synthetic-fallback`을 명시하면 직선 추정으로 대체하고 후보 평가까지 포함한 대체 건수를 `routing_summary`와 화면에 남긴다. 기본 네 사례는 실제 도로 22개를 수집했고 직선 대체 없이 생성했다. 같은 cache를 다시 사용하면 네트워크나 지도 갱신으로 분기 입력이 달라지지 않는다. 새로운 도로 snapshot은 `--route-cache`로 새 폴더를 지정해 수집한다.

기본 사례의 119 도착시각은 도로 예상시간으로 계산하고 현장처치 3분은 유지한다. 자원 변화는 탐색 시작 4분 뒤의 공통 외생 사건으로 고정한다. 사용자 사례의 외생 사건 시각은 자동 변경하지 않으며, 출동시각도 기본적으로 입력값을 유지한다. `--dispatch-timing route`로 출동시간만 재산정하는 경우 사건 위치·시각을 다시 검토해야 한다.

## 다수 환자 비교와 반복 실험

`multi_scenarios.py`는 같은 현장·기존 도로 입력에 독립 구급차 5대를 놓고 다음 조건을 만든다. 실제 기관에 부여된 진료 역량과 자원은 모두 가상이다.

| 사례 | 바뀌는 조건 | 확인할 것 |
|---|---|---|
| 동시 자원 경쟁 | 환자 5명이 같은 시간에 같은 후보에 문의 | 환자 순서와 우선순위에 따른 배정 차이 |
| 희소 치료조건 | 높은 우선순위 환자 1명만 한 병원의 특정 역량 필요 | 일반 환자에게 대안을 줘 희소 자원을 남기는지 |
| 응답 지연·무응답 | 가까운 후보의 응답이 늦거나 오지 않음 | 첫 수용과 빠른 도착 사이의 차이 |
| 총자원 부족 | 5명에 2자리, 관찰 중 자원 반환 없음 | 모든 정책에서 미배정이 남는 반례 |

문의 3곳·대기 2분·변경 알림은 같게 유지하고 R0 첫 수용, R1 예상 도착, R2 자원 경쟁 비용, R3 다수 환자 동시 계산을 비교한다. R2의 경쟁 비용 가중치는 설정값이다. R3는 같은 분에 유효 응답을 확보한 환자 묶음의 `(미배정 우선순위 합, 배정 환자의 우선순위 가중 이동시간 합)`을 사전식으로 최소화한다. 조합 수 20,000을 넘으면 R2로 대체한다. 아직 CP-SAT 연결이나 미래 사건을 아는 전역 최적화가 아니다. 배정 시점이 다른 환자까지 모두 동시에 최적화하는 것도 아니다.

다수 환자 시험으로 발견한 자원 회계 오류를 수정했다. 환자가 도착해 치료 대기열로 옮겨진 동안에도 그 수요를 여유 자원에서 빼서, 같은 분의 후속 배정이 그 자리를 재사용하지 못하게 했다. 지도 자원 표는 엔진에서 기록한 점유·이송 확정·도착 대기 snapshot을 사용한다.

```powershell
# 실제 도로 캐시와 합성 응답 분포로 개발 비교, 외부 API 호출 없음
python -m simulation.replay_experiment --seeds 20 --offline --replay-output simulation_results/map_comparison_replays.json

# 관련 불변조건·회귀 검사
python -m unittest test_simulation test_map_replay test_map_road_routes test_road_routes test_viewer_server test_multi_scenarios test_replay_experiment test_replay_observability -v
```

[실험 보고서](../simulation_results/map_comparison.md)와 JSON에 20개 seed × 4조건 × (규칙 4개 + 문의 정책 6개), 총 800개 분기를 저장한다. 규칙 비교와 문의 정책 비교를 분리한다. 문의 정책은 R1을 고정하고 `k=1/3/5`, `wait=0/2`를 비교하며 탐색 범위 확대 시점 조정은 아직 포함하지 않았다.

반복 실험의 응답 지연·미응답은 새 잠재 응답 분포로 만든다. 고정 시연 사례의 무응답 표시 대신 20% 합성 확률을 쓰며 실제 응답률 추정이 아니다. 같은 seed·환자·병원·문의 회차는 같은 잠재 응답을 받는다. 각 정책이 같은 절대시각에 문의해야 한다는 뜻은 아니다. 현재 병원 문의량이 응답률이나 지연에 미치는 영향은 모델링하지 않았다.

시간 평균에 앞서 미배정·미도착·치료 미시작·관찰 내 완료·우선순위별 결과를 본다. 시간 차이는 양쪽 정책에서 모두 관측된 같은 환자를 짝지어 계산하고 분모를 보존한다. JSON에는 95% seed 부트스트랩 구간, 악화 seed 비율, 최악 seed, 우선순위별 최장 지연도 담는다.

현재 결과는 조건에 따라 다르다. R3는 희소 치료조건에서 R0보다 평균 도착이 0.75분 빨랐고 동시 자원 경쟁에서는 0.56분 늦었다. 총자원 부족에서는 모두 3명 미배정이다. 이 수치로 현행 119 시스템보다 우수하거나 최적 정책을 찾았다고 주장하지 않는다. 다음 단계는 목표와 정책을 고정한 뒤 미사용 seed·다른 환자 위치·자원 밀도·응답 조건으로 평가하는 것이다.

## 문의 처리 부하와 조건 변경의 분리 검증

기존 반복 비교에는 문의량이 많아져도 응답이 느려지지 않는 한계가 있었다. `inquiry_queue.py`와 지도 엔진의 선택적 hook으로 병원별 FIFO 문의 검토 대기열을 추가했다. 같은 병원의 처리 인원이 이미 일하고 있으면 새 문의가 기다리며, 서비스가 끝난 시점의 실제 자원 상태를 기준으로 수용 여부를 답한다. `scenario.inquiry_queue.enabled`를 끄면 기존 방식으로 돌아간다.

`workload_scenarios.py`는 문의부하 4개와 조건변경 4개의 통제 입력을 만든다. 충분한 치료 자원에서 문의 담당자 1명/2명, 희소 치료조건, 총자원 부족을 비교한다. 조건변경은 문의 대기열을 끄고 변화 없음·짧은 중단·지속 중단·시스템 밖 환자 유입만 바꾼다. 변경 병원과 시각은 정책의 실제 배정 결과를 보고 옮기지 않는다.

후보 분산 정책은 `예상 이동시간 + load_weight × 지난 load_window분의 문의 수`로 후보를 정렬한다. 현재까지 시스템이 보낸 요청 시각만 사용하며 실제 대기열·미래 완료시간·외생 업무·복구 시각을 미리 보지 않는다. 이 정책도 규칙 기반 후보이며 최적해를 보장하지 않는다.

```powershell
# 새 모델: 개발 12개 seed에서 가중치를 선택하고 검증 20개 seed에서는 고정
python -m simulation.workload_experiment --routing synthetic

# 기존 OSRM 사례를 유지하고 신규 합성 이동 실험 예시만 별도로 앞에 추가
python -m simulation.map_replay --offline --experiment-replays simulation_results/map_comparison_replays.json --synthetic-experiment-replays simulation_results/workload_validation_replays.json

# 새 메커니즘·통합·정보 누설·검증 집합 분리 검사
python -m unittest test_inquiry_queue test_workload_scenarios test_workload_integration test_condition_change_metrics test_workload_experiment
```

결과는 [문의부하·조건변경 보고서](../simulation_results/workload_validation.md)와 JSON에 저장한다. 개발 입력과 검증 입력은 환자 수·발생 간격·서비스 소요·자원량·배경 업무가 다르며 seed도 겹치지 않는다. 기관과 현장 좌표는 공유한다. 새 반복 실험은 기본적으로 직선거리 기반 이동 추정을 사용하며, 기존 지도 사례의 OSRM 도로 실험과 구분한다. 지도에 추가하는 예시는 측정 당시와 같은 이동 모드로 다시 생성한다.

정책 선택 목적은 `(치료 미시작 수, 미도착 수, 조건부 평균 치료 시작시간 + 0.25 × 환자당 문의 수)` 순서로 미리 고정한다. 0.25는 합성 실험의 시간 환산 가중치이며 임상적으로 검증된 값이 아니다. 검증 결과로 가중치를 다시 고르지 않는다. 정책 비교, 변경 알림 켬/끔, 문의 대기열 켬/끔을 분리해 저장하고 공통 관측 환자의 시간 차이와 전체 미도착·미치료를 함께 본다.

대기열 가정도 결과와 함께 해석해야 한다. 무응답 요청도 검토 용량을 사용하고, 다른 병원으로 확정된 뒤 남은 요청도 자동 취소하지 않는다. 문의 제한시간 이후의 회신은 배정에 쓰지 않지만 병원 업무는 계속 점유한다. 누적 업무량은 관찰 종료까지 실제 경과한 인력·분이며 미처리 요청의 미래 업무를 포함하지 않는다. 이 가정은 실제 병원의 취소 처리나 무응답 원인 분포를 추정한 값이 아니다.

`resource_invalid_transit_minutes`는 병원 자원이 확정 수요를 감당하지 못하는 동안 영향 대상으로 계산된 환자가 이송 중이었던 시간의 합이다. 단위는 환자·분이며 임상적 위해·생존 지표가 아니다. 목적지 변경에는 새 병원의 수용 확인이 필요하며 실험에서는 조정 결정이 즉시 이루어진다고 단순화했다. 실제 의료진·구급대의 판단 소요와 현장 절차는 별도 검토가 필요하다. 알림 비활성 대조는 구성요소의 효과를 분리하기 위한 것으로, 현행 시스템에 알림이 없다는 주장이 아니다.

## 관측 응답으로 문의 규모 조절

`adaptive_inquiry.py`는 현재까지 받은 회신·타임아웃, 이미 보낸 미해결 문의의 경과시간으로 다음 문의 한도를 정한다. `Policy(adaptive_inquiry={...}, k=5)`로 선택하며 기존 고정 문의 정책은 그대로 유지한다. 최근 회신이 느리면 1곳, 빠르면 3곳을 선택한다. 느린 회신은 없지만 타임아웃이 많은 경우 최대 5곳으로 늘린다. 관측이 부족하면 초기 설정을 사용하되 오래 대기 중인 문의가 쌓이면 1곳으로 줄인다. 숫자는 개발용 설정이며 의료 기준이 아니다.

알고리즘에 실제 문의 처리 인원, 병원 내부 대기열, 예약된 회신 시각·무응답 여부, 아직 발생하지 않은 환자는 전달하지 않는다. 별도의 비공개 스케줄에서 **회신 도착 또는 제한시간 경과 이후에만** 관측 기록을 만든다. 다른 병원 수용 확정으로 환자의 요청 목록을 비우더라도 남은 병원 업무는 취소하지 않는 기존 가정에 따라 그 회신 기록은 나중에 도착한다. 해당 기록은 다음 문의의 판단 근거이며 새로운 수용 확정이 아니다.

`adaptive_experiment.py`는 고정 1/3/5곳과 관측형 후보 4개를 개발 seed 101~112에서 비교한다. 고정 정책군과 관측형 정책군에서 각각 하나를 선택한 뒤 신규 검증 seed 2001~2020에서 고정해 비교한다. 무응답 0%/20%, 충분 자원 담당 1명/2명, 희소 조건, 총자원 부족의 8조건이다. 앞선 검증 1001~1020을 재사용하지 않으며 발생 간격·서비스 시간·배경 업무·타임아웃도 바꿨다. 이전 결과는 덮어쓰지 않는다.

```powershell
python -m simulation.adaptive_experiment --routing synthetic

# 기본 도로 사례와 앞선 업무량 실험을 보존하고 관측형 문의 대조 두 건을 추가
python -m simulation.map_replay --offline --experiment-replays simulation_results/map_comparison_replays.json --synthetic-experiment-replays simulation_results/adaptive_inquiry_replays.json --synthetic-experiment-replays simulation_results/workload_validation_replays.json

python -m unittest test_adaptive_inquiry test_adaptive_integration test_adaptive_experiment
```

결과는 [관측형 문의 검증 보고서](../simulation_results/adaptive_inquiry.md), 상세 기록은 같은 이름의 JSON에 저장한다. 지도 예시는 성과를 보고 고르지 않고 첫 검증 seed의 충분 자원·무응답 0·담당 1명/2명으로 고정한다. 재생 시각까지의 문의 한도·이유·관측 근거를 보여 주며, 검증 전체의 대표 효과와 구분한다. `adaptive_mean_limit`은 정책이 요청한 한도의 평균이며 실제 처리한 문의 수나 실제 동시 문의 수가 아니다. 이 규칙도 비교 대상인 휴리스틱으로, 최적해를 보장하지 않는다.

## 병원별 관측과 대기 중 추가 문의 판단 (V2)

첫 관측형 규칙은 병원 전체의 회신 중앙값을 사용했고, 검증 160개 분기 중 153개에서 계속 1곳만 선택했다. `observed_inquiry.py`는 병원별 회신 이력과 해당 환자의 진행 중 문의를 분리해 평가한다. 최근 30분의 병원별 회신 중앙값을 사용하고 표본이 부족하면 전체 회신 중앙값, 그마저 부족하면 사전 설정값을 사용한다. 현재 시스템이 보낸 미해결 문의 수와 이미 관측된 타임아웃도 비용에 반영한다.

새 후보의 비용은 `예상 이동시간 + 추정 회신시간 + 타임아웃 비용`이다. 처음에는 비용이 가장 작은 병원 한 곳에 문의한다. 대기 중에는 매 분 현재 문의의 경과시간과 대체 후보 비용을 비교하며, 추가 문의가 유리하다고 계산되는 경우 한 번에 최대 1곳, 현재 배정 판단의 동시 문의 최대 3곳으로 제한해 추가한다. 오래 기다릴수록 회신이 곧 온다고 단정하지 않도록 지연 비용을 늘리는 규칙을 둔다. 이것은 경험적 비용 규칙이며 수용 가능성 예측모델이나 최적성 증명이 아니다.

실제 문의 처리 인원·내부 대기열·예정된 회신 시각·미래 환자와 업무 일정은 전달하지 않는다. 회신이 도착하거나 제한시간이 지난 뒤에만 관측으로 사용한다. 답변 처리 이전의 조기 재평가는 유예하고, 시간 기준 탐색 확대도 재문의 대기시간을 우회하지 않도록 했다. 다른 병원으로 확정된 뒤 남은 병원 업무는 기존 실험 가정에 따라 취소하지 않는다. 동시 문의 한도는 현재 배정 판단에 쓰이는 요청 기준이며, 확정 이후 남은 검토 작업까지 포함하는 한도가 아니다.

`observed_experiment.py`는 고정 1/3/5곳, 이전 V1의 고정 설정, V2 후보 4개를 비교한다. 개발 seed 201~208에서 고정 정책과 V2를 각각 선택하고 검증 seed 3001~3020에서는 설정을 바꾸지 않는다. 동시 발생·시간차 발생·배경 업무 변화의 담당자 1명/2명, 희소 조건, 총자원 부족에 무응답 0%/20%를 교차한 16조건이다. 앞선 검증 seed는 재사용하지 않으며 개발 12명에서 검증 16명으로 늘리고 발생 간격·업무 소요·타임아웃도 바꿨다.

```powershell
python -m simulation.observed_experiment --development-only
# 저장된 개발 결과의 설정·coverage를 검사하고, 선택을 유지한 채 검증만 실행
python -m simulation.observed_experiment --development-input simulation_results/observed_inquiry_development.json
# 개발부터 새로 실행하려면 --development-input 없이 실행
python -m unittest test_observed_inquiry test_observed_integration test_observed_scenarios test_observed_experiment test_observed_audit
python -m simulation.observed_audit
```

결과는 [병원별 관측 정책 보고서](../simulation_results/observed_inquiry.md)와 같은 이름의 JSON에 저장한다. 기존 실험 결과는 보존한다. 지도 예시는 첫 검증 seed·무응답 0%의 동시 발생·담당 1명, 시간차 발생·담당 2명, 배경 업무 변화·담당 1명 세 조건을 사전에 지정하며, 다섯 정책을 함께 보여 준다. 새 실험의 위치는 공개 병원 자료지만 환자·진료조건·자원·업무와 이동 추정은 합성이다. 기존 OSRM 도로 사례와 구분해 해석한다.

신규 검증 결과는 고정1곳 대비 탐색 시작→가상 치료 시작 조건부 평균17.27→14.84분, 문의3.1357→3.1305건/환자다. 문의 감소 구간은0을 포함하므로 문의 감소를 입증한 것으로 부르지 않는다. 양쪽 모두5,120명 중4,600명에서 치료 시작을 관측했지만, 총자원 부족 조건에서 실험 우선순위4 미시작이162→163명으로1명 늘었다. 평균 개선을 우선순위 보호나 최적성으로 해석하지 않는다. [독립 감사](../simulation_results/observed_inquiry_audit.json)는 공통환자4,597명·20seed의 차이, 분모, 잠근 정책, 지도15분기의 결과 일치를 검사한다. 당시 지도 전체18사례/67분기이며 새 세 예시 중 두 건은 차이0이다.

## 우선순위 보호와 R4 비교

`priority_experiment.py`는 잠근 V2를 기준으로 신규 문의 순서, 현재 환자의 문의·회신·재검토 순서, 높은 군 추가 문의 시점, R4 공동 배정을 비교한다. 8계열×무응답0/20%=16조건이며 개발301~308·환자12명에서 후보를 선택하고 새 검증5001~5020·환자16명에서는 설정을 바꾸지 않았다. 이미 보낸 FIFO 작업과 확정 이송은 취소하지 않고 미래 환자 일정은 정책에 제공하지 않는다. 우선순위4·2·1은 실험 설정이며 임상 기준이 아니다.

공동 배정 대기 중 불필요한 추가 문의 버그를 발견해 첫 holdout4001~4020은 수치 결과 열람 전에 중단했다. 그 범위 전체를 제외하고 수정 후 개발을 다시 실행했다. [중단 기록](../simulation_results/priority_protection_interrupted.json)에 원인·회귀 검사·소비한 입력을 보존했다. 수정 후 개발에서 R4와 단순한 `priority_all`의 결과가 같아 사전 동률 규칙에 따라 `priority_all`을 잠갔다. 검증에서 R4의 추가 효과를 확인했다고 주장하지 않는다.

검증 결과는 V2→선택 정책의 높은 군 미시작346→223명, 중간258→280명, 낮은238→338명, 전체842→841명이다. 주된 효과는 치료 기회의 재분배다. 공통 환자 치료 시작은 높은 군1.33분 단축, 낮은 군0.68분 지연이며, 문의는7.1885→7.1961건/환자로 감소를 확인하지 못했다. 자원 부족·후발 높은 군·자원 변화의 첫 검증seed5001을 사전 지정해 지도에 추가했다. 뒤의 두 예시는 공통 환자 시간 차이0이다. 현재 지도는 **21사례·82분기(도로10·합성11사례)**이며 현재까지의 군별 대기와 재생 종료 후 미시작을 구분한다.

```powershell
python -m simulation.priority_experiment --development-only
python -m simulation.priority_experiment --development-input simulation_results/priority_protection_corrected_development.json
python -m simulation.map_replay --offline --experiment-replays simulation_results/map_comparison_replays.json --synthetic-experiment-replays simulation_results/priority_protection_corrected_replays.json --synthetic-experiment-replays simulation_results/observed_inquiry_replays.json --synthetic-experiment-replays simulation_results/adaptive_inquiry_replays.json --synthetic-experiment-replays simulation_results/workload_validation_replays.json
python -m simulation.priority_audit
python -m unittest test_priority_assignment test_priority_scenarios test_priority_integration test_priority_experiment test_priority_audit
```

위 명령은 완료한 실험의 재현이다. 정책 수정 후에는 새 파일명과 미사용 검증 입력이 필요하다. 검증1001~1020·2001~2020·3001~3020·5001~5020 및 중단4001~4020은 재사용하지 않는다. [전체 결과](../simulation_results/priority_protection_corrected.md), [독립 감사](../simulation_results/priority_protection_corrected_audit.json), [핸드오프](../docs/HANDOFF_20261011.md)에 군별 분모·공통 환자 구간·악화 조건을 남겼다. 관련301개 테스트와 전체 지도 실제 HTTP 재생·정지·필터·미래정보 차단·화면 검사를 통과했다. 실제 의료 효과나 현행 119 체계 대비 개선을 입증한 결과는 아니다.

## 서버 문의 정책 기록 재생 (2026-10-12)

고정3·V2·V3를 유형별 첫 검증seed7101의8사례에서 재생한다. 서버 사례는 정보 접수부터 시작하고, 도착은 합성 예상 도착으로 표시한다. 초 단위 결과표와 환자 필터, 이전/다음 핵심 사건 이동을 사용할 수 있다. 기존21사례와 함께 총29사례이며 좋은 결과와 느린 반례를 모두 보존했다. [사용 방법·범위·시간 기준](../docs/SERVER_REPLAY.md).

```powershell
python -m simulation.server_replay_adapter
python -m simulation.viewer_server --reuse-data --no-browser --port 18005
python -m unittest test_server_replay_adapter test_server_replay_regression test_map_replay test_viewer_server
```

`http://127.0.0.1:18005/?scenario=server-simultaneous_w1-7101`로 바로 연다. 변환기는 원본 서버 보고서와 기존 `map_replay_data.json`을 수정하지 않는다. 서로 다른 시각에 발생하는 환자의 예상 도착은 환자 생성시각+환자별 예상 도착 경과시간으로 공통 시각에 맞춘다. 실제119출동·현장처치·치료·전원은 원본에 없어 서버 사례에 만들지 않는다.

`python -m simulation.server_replay_adapter`는 서버 V3 별도 seed 검증 기록을 재계산 없이 지도 재생 형식(`simulation/viewer/server_replay_data.json`)으로 바꾼다. 뷰어는 이 파일이 있으면 기존 사례 뒤에 덧붙인다. 유형마다 가장 낮은 검증 seed 1개를 결과와 무관하게 고르며, 고정3·V2·V3를 같은 사례에서 나란히 재생한다. 병원·환자·이동시간은 서버 실험의 합성값이고, 치료·전원은 기록하지 않는다. 변환 일치 검사는 `python -m unittest test_server_replay_adapter`.

## 실제 지도 배경과 5개 정책 도로 시연 (2026-10-14)

실제 병원 좌표의 사례는 지도 상단 **일반지도 / 간략지도**로 OpenStreetMap 배경을 전환한다. 도로·지역명 배경은 인터넷이 필요하지만 저장된 OSRM 경로와 재생은 배경 연결 실패 시에도 동작한다. 가상 좌표 사례에는 실제 지도 배경을 표시하지 않는다.

`http://127.0.0.1:18005/?scenario=road-online-change-enroute`에서 새 5개 정책 비교를 연다. 동시 발생·이송 중 조건 변경·도착 후 전원 3종을 실제 병원 이름·좌표와 도로시간으로 입력부터 별도 실행했다. 현재 자료는 카카오 자동차 길찾기 19경로의 조회시점 예상시간을 사용한다. 이전 OSRM 시연은 별도 백업했다. 환자·자원·역량·응답은 가상이며 설명용 사례다. 기본 화면은 환자 발생지 핀을 강조하고 지도 아래의 정책 선택·환자 위치 보기·상세 표시로 필요한 정보를 골라 볼 수 있다. 기존 합성 검증 자료와 수치를 보존한다. [사용·재생성·검사 안내](../docs/ROAD_ONLINE_REPLAY.md).
