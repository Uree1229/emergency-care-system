# 배정 알고리즘 발표 근거와 시연 데이터

2026-11-05 · 검토한 작업본 `73ac66d`. 발표의 중심은 **현재 수용·자원 제약 안에서 우선순위별 미배정과 예정 치료 시작을 공동 최적화한다**는 점이다. 희소 자원을 두 환자가 공유하는 입력으로 공동 배정의 차이와 한 환자가 감수하는 지연을 함께 보여준다.

## 채택 정책과 최적성의 범위

| 구분 | 서버 기준 | 발표에서 보여줄 내용 |
|---|---|---|
| 문의 | 자동 문의 기본값 `fixed_3` v1. 회차당 최대 3곳, 회신 대기 60초, 최대 연락 10곳, 자동 작업 300초 | 현재 역량·제한·자원 상태로 후보를 거른다. 자원 충족 여부를 먼저 보고 같은 조건에서 예상 이동시간이 짧은 곳을 우선한다. 유효 수용이 생기면 담당자 확정을 기다린다. 이전 회차의 미응답이 남을 수 있다. |
| 배정 | CP-SAT `severity_first`, 기본 3초. `full` 모드의 배정 워커가 실행 | 우선군 4→2→1 각각의 미배정 수를 순서대로 최소화한다. 이 세 값이 같으면 같은 군 순으로 등록부터 예정 치료 시작까지의 시간 합을 줄인다. |
| 제약·상태 | 현재 유효 수용, 응급병상·전문팀, 기존 점유, 목적지 잠금, 분 단위 시간창 | `lex_optimal=true`일 때 현재 모델의 사전식 목표값이 최적이라고 말할 수 있다. 시간 제한 등으로 가능해·대체해·검토 필요가 나올 수 있다. 3초는 계산 설정이다. 추천 이후 목적지 확정은 담당자가 한다. |

우선군과 시간 제약은 프로젝트의 실험 설정이다. 이 최적성은 현재 입력으로 계산한 예정 시작에 적용된다. 미래 회신·자원 변화·실제 도착이나 치료 효과까지 설명하려면 별도의 평가가 필요하다. [서버 동결 기준과 통합 검증](ALLOCATION_BASELINE_AND_E2E_20261030.md) · [목적함수 및 증명 코드](../solver_improved.py#L148) · [워커](../server/worker.py#L22)

## 왜 이 정책을 유지했나

**문의.** 실제 문의 워크플로를 이용한 96개 합성 시나리오에서는 V3가 고정3보다 유형 균등 제한 대기를 94.79→87.07초, 문의를 환자당 4.95→2.57건으로 줄였다. 선정 실패는 300초로 넣었다. 자원과 이동을 포함한 별도 336환경 연구의 유형 균등 제한 처치시간은 고정3+회복 12.4531분, V3/V4+회복·진행 문의 보호에서 각각 12.6387분·12.6298분이었다. 이 연구의 고정3은 시뮬레이터 문의 루프를, V3/V4는 서버의 순수 선택 함수를 사용했다. 전체 300초 제한을 적용하지 않았고 에피소드당 10곳 한도는 V3/V4에 적용했다. 문의 단계에서 본 V3의 이득을 전체 이송 정책의 채택 근거로 쓰기에는 부족해 고정3을 기본으로 유지했다. [서버 V3 문의 비교](INQUIRY_V3_EXPERIMENT.md#검증-결과) · [전체 이송의 문의 비교](JOINT_POLICY_EXPERIMENT.md#새-검증-결과)

**배정.** 실제 서버의 9개 합성 통합 시나리오에서 솔버 호출 10회가 `OPTIMAL`, `lex_optimal=true`, 대체 0회였다. 아래의 설명용 2인 입력도 현재 솔버와 전수 비교로 목표값을 확인했다. 이는 구현과 현재 배정 모형의 증거다. 역사적 C1 스냅샷 비교는 160사례 중 작은 140사례에서 정확 열거와 목적값이 같았다. 큰 20사례는 R3가 조합 한도를 넘어 쓴 순차 대체안 대비 8개 우수·12개 동률이었다. C1의 가중 미배정·이동 목표는 현재 서버와 다르므로 각각 제시한다. [서버 통합 검증](ALLOCATION_BASELINE_AND_E2E_20261030.md#2-전체-흐름-검증-결과) · [C1 비교](ALLOCATION_BENCHMARK.md#검증-결과)

**채택하지 않은 연구.** 새 36환경·방식당 같은 603명에서 H1은 거리순 R1 대비 도착 제한시간 27명 단축·34명 증가·542명 동일이었다. 동일 중 40명은 양쪽 모두 미도착이다. R1·H1은 각각 미배정 23명·미도착 40명이었고 H1의 개인 최대 지연은 +67분, 우선군4 최대는 +51분이었다. H1은 첫 회신 R0 대비 도착 및 치료 시작 기록도 1명 잃었다. 후속 W1은 새 60환경·987명에서 H1 대비 개선 0명이고, 해당 자료에서 H1/W1의 R1 대비 제한시간 최대 증가는 +124분이었다. 36환경 평가에는 실제 서버 분기가 없다. [최종 결정](FINAL_ALGORITHM_DECISION_20261030.md) · [현재 채택·실패 평가 근거](FINAL_ALGORITHM_DECISION_20261030.md) · [W1](WAITING_PROTECTION_20261030.md)

## 저장소에서 확인할 근거

[저장된 50개 입력 검사 결과](evidence/20261105/allocation-seed9.json), [출처·해시 manifest](evidence/20261105/manifest.json), [업무량 사례별 발췌](evidence/20261105/resource-workload.json)를 함께 보관한다. 아래 명령은 저장된 수치와 해시를 검증한다.

```powershell
python docs/evidence/20261105/verify.py
```

46은 관측 입력에서 해가 있고 순차안과 비교할 수 있는 사례 수다. 숨겨진 사후 상태에서도 유효했던 것은 41개이며, 나머지 5개를 같은 의미의 성공으로 세지 않는다. 원래 결과 파일에는 실행 당시 소스·런타임 해시가 없으므로 manifest의 소스 커밋과 입력 해시는 재현용 참조다. 이 문서의 재계산은 저장된 결과 집계이며 새 성능 실험이 아니다. 3초 탐색을 다시 실행하면 환경에 따라 최적성 증명·개선 건수가 바뀔 수 있다.

## 현재 솔버의 추가 스냅샷 비교

기존 합성 코퍼스의 `test` 분할에서 파일 순서상 첫 seed `9`의 50개 프로필을 전부 사용했다. 같은 관측 입력을 순차안과 현재 `severity_first` 솔버에 주고, 솔버에 3초 탐색 예산을 줬다. 이 실행은 `solver_improved.solve`를 직접 호출한다. 운영 워커의 프로세스 시작 시간과 문의·이송은 포함하지 않는다. [코퍼스](../datasets/README.md#스냅샷-성능-확인) · [실행기](../datasets/evaluate.py#L15)

| 항목 | 결과 |
|---|---:|
| 양쪽 모두 유효한 해가 있는 경우 | 46개 |
| 순차안 대비 사전식 목적값 개선 / 동률 | 20개 / 26개 |
| 개선된 첫 항목이 미배정 수 / 예정 시작 지연 합 | 10개 / 10개 |
| 솔버가 전체 사전식 최적성을 증명 / 가능해를 반환 | 26개 / 20개 |
| 기존 목적지·점유 충돌 검토 / 서버 크기 제한 초과 제외 | 3개 / 1개 |
| 관측 입력에서 유효한 해가 숨겨진 사후 상태에서 제약을 어긴 경우 | 5개 / 46개 |

순차안은 솔버가 보존하는 초기 해라, 비교된 사례의 비열세는 설계상 기대되는 결과다. 개선 20개와 `OPTIMAL` 26개는 서로 다른 집계다. 숨겨진 상태 검사는 자원 과대 보고, 미관측 유입, 이동·처치시간 과소 추정 등이 있을 때 입력의 최적 해가 실제 조건에서는 실행 불가능할 수 있음을 보여준다. 첫 seed 한 개의 기술통계로 제시한다. 전체 600개 실행은 중단했으며 표에 포함한 값은 완료한 50개에서만 계산했다.

```powershell
python -m datasets.evaluate --kind allocation --split test --limit 50 --seconds 3 --output data/allocation-presentation-seed9.json
```

## 본 시연 데이터 — 희소 수용 후보가 겹친 두 환자

기존 `GOLD-scarce_choice`의 구조를 사용한다. 두 병원에 응급병상과 전문팀이 각각 한 자리이고 두 환자는 모두 같은 우선군4에 속한다. 각 환자는 두 자원을 30분 사용하며 등록 후 20분 이내에 시작해야 한다는 합성 조건이다.

| 환자 | A병원 | B병원 | 서버 순차 대체안 | 서버 CP-SAT `severity_first` |
|---|---|---|---|---|
| P01 | 수용, 이동 5분 | 수용, 이동 12분 | A, 예정 시작 5분 | B, 예정 시작 12분 |
| P02 | 수용, 이동 7분 | 수용 없음 | 미배정 | A, 예정 시작 7분 |

미배정 우선군4가 **1→0명**으로 줄고, P01의 예정 시작은 **+7분**이다. 순차 대체안은 P01을 가까운 A에 먼저 놓으므로 P02는 시작 상한을 넘는다. CP-SAT은 P02의 유일한 수용을 남겨 두도록 두 환자를 함께 배정한다. 목표값은 `[1,0,0,5,0,0] → [0,0,0,19,0,0]`이다. 첫 항목의 개선이 뒤 시간 합보다 우선하며, 5분과 19분의 분모도 다르다. 비교 기준은 현재 서버의 순차 대체안이다. [기존 정답 사례](../datasets/allocation.py#L145) · [순차 대체안 코드](../solver_improved.py#L155)

저장소 루트에서 OR-Tools가 설치된 Python으로 실행한다. 추천 계산 시점의 모델을 운영 워커의 계산 함수에 넣고 전수 비교 결과와 대조한다. 이 PC에서는 번들 Python 3.12.14와 기본 체크아웃의 `.solver_deps`를 사용했다. 아래 명령의 `python` 대신 `& $demoPython`을 사용하면 같은 환경으로 실행할 수 있다.

```powershell
$env:PYTHONPATH = 'C:/Users/PC/Documents/ChatGPT/캡스톤 디자인/.solver_deps'
$demoPython = 'C:/Users/PC/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
```

```powershell
$demoCode = @'
from server.worker import bounded_solve
from solver_improved import exhaustive, score, validate

data = dict(version=1, now=0, horizon=120, capacities=[[1, 1], [1, 1]],
            blocks=[], fixed={}, patients=[
    dict(id='P01', priority=4, special=True, release=0, deadline=20,
         duration=30, travel=[5, 12], eligible=[0, 1]),
    dict(id='P02', priority=4, special=True, release=0, deadline=20,
         duration=30, travel=[7, 10], eligible=[0]),
])
for seconds in (0, 3):
    plan, meta = bounded_solve(data, seconds=seconds)
    assert validate(data, plan)
    if seconds == 3:
        assert meta['lex_optimal'] and not meta['fallback']
        policy = 'severity_first'
        assert score(data, plan, policy) == score(data, exhaustive(data, policy), policy)
    print(seconds, plan, meta['objective'], meta['status'], meta['lex_optimal'])
'@
python -c $demoCode
```

대조 입력은 A 용량을 `[2,2]`로 늘리는 경우다. 두 방식 모두 A에 5분·7분으로 배정한다. 두 환자의 `eligible`을 비우면 두 방식 모두 미배정이다. 같은 CP-SAT에서 목표만 바꾸는 질문에는 기존 `GOLD-priority_tradeoff`를 쓴다. `severity_first`는 우선군4 한 명, `total_first`는 우선군1 세 명을 배정한다. 두 해 모두 각 목표에서 최적이므로 우선순위 보호와 총 배정 인원 사이의 선택을 설명할 수 있다. [목적 비교 회귀](../tests/core/test_solver_improved.py#L7)

## 지도 시연과 반례

지도 비교는 별도 연구 C1의 저장 결과를 재생한다. C1은 가중 미배정과 가중 이동을 최소화하고, 현재 서버는 우선군별 미배정과 예정 시작을 최소화한다. 아래 지도의 도착 차이와 본 시연의 예정 시작 +7분은 서로 다른 입력의 결과다.

| 용도 | 저장 시나리오 | 화면에서 확인할 결과 |
|---|---|---|
| 공동 배정이 보이는 지도 | `online-scarce_greedy_trap-11101`, `lab.html#story`에서 기준 A2 예상 도착 우선(R1)·비교 A5 공동 배정 · CP-SAT(C1)를 선택하고 마지막 단계로 이동 | R1은 P01→H01, t10 도착·P02 미배정. C1은 P01→H02, t15 도착·P02→H01, t10 도착. 2명 중 1→2명 배정. 자원 경합을 고려한 R2와 공동 배정 R3도 C1과 같았다. |
| 공동 배정의 우회 비용 | `online-scarce_long_detour-11101`, `lab.html#compare` | C1과 R3는 P02를 추가 배정하지만 P01 도착은 t10→t57로 47분 늦어진다. R2는 R1과 같았다. |
| 문의 정책의 상충 | `server-no_response_near-7101` 및 `server-simultaneous_w1-7101` | 가까운 병원 무응답에서 P01 선정은 고정3 26초, V3 116초, V2 미선정이다. 병원 검토 담당자가 1명인 동시 발생에서는 고정3 7/8명, V3 8/8명이 선정된다. |
| 현재 서버 업무 흐름 | `voice-road-normal_arrival`, 기본 지도 | 저장된 정상 건에서 문의→수용→사람 확정→도착을 보여준다. 다른 정책과 비교하는 장면은 위 연구 지도에서 보여준다. |

`simulation/viewer/map_replay_data.json`이 있는 체크아웃에서 `python -m simulation.viewer_server --reuse-data --no-browser --port 18045`로 띄운다. 이 PC에서는 `C:/Users/PC/Documents/ChatGPT/캡스톤 디자인`에 기본 지도와 비교 자료가 있다. [희소 경합 지도](http://127.0.0.1:18045/lab.html?scenario=online-scarce_greedy_trap-11101#story) · [긴 우회 지도](http://127.0.0.1:18045/lab.html?scenario=online-scarce_long_detour-11101#compare) · [정상 흐름 지도](http://127.0.0.1:18045/?scenario=voice-road-normal_arrival) · [지도 비교 설명](ONLINE_ALLOCATION.md)

문의 질문에는 [가까운 병원 무응답](http://127.0.0.1:18045/?scenario=server-no_response_near-7101)과 [병원 검토 혼잡](http://127.0.0.1:18045/?scenario=server-simultaneous_w1-7101)을 묶어 보여준다. 두 사례는 서버 문의 실험의 저장 기록이다. [문의 재생 안내](SERVER_REPLAY.md)

발표 순서는 **현재 솔버 2인 배정 → 희소 경합 지도 → 긴 우회 또는 H1 미채택 근거**다. H1 질문에는 `raw/003 P02`를 보여준다. 희소 병원 유형의 도착 제한시간 평균은 67.071→65.667분이었다. 미도착은 관측 종료까지 포함한다. 이 유형의 P02는 같은 병원까지 이동 10분으로 같았고 선정 시점이 t19→t86으로 늦어졌다. 이 사례는 배정에 따른 후속 자원 점유와 회신 변화까지 포함한 대응 결과다. [사례 원인과 한계](FINAL_ALGORITHM_DECISION_20261030.md#사례와-최초-선택의-의미)

> “수용 응답을 얻은 환자들을 한 명씩 가까운 곳에 보내면, 다른 환자에게 유일한 병원이 먼저 찰 수 있습니다. 현재 자원과 수용 조건을 함께 계산해 이 사례에서는 미배정이 한 명에서 0명으로 줄었습니다. 대신 우회한 환자의 예정 시작은 7분 늦어졌습니다. 저희는 배정 수와 개별 지연을 함께 검토했습니다.”

## 이번 확인 범위

동결 검사는 기준 파일 21개와 실행 환경의 일치를 확인했다. 문의·계획·솔버 회귀 57개를 실행했고 모두 통과했다. 본 시연의 문서 명령을 그대로 실행해 두 해의 유효성, 3초 워커의 최적 증명, 전수 열거 목적값 일치를 확인했다. 저장 지도는 로컬 HTTP로 각 페이지와 데이터가 제공되는지 확인했고 시나리오별 결과는 JSON에서 대조했다. 브라우저에서 직접 발표하는 리허설은 별도다.
