# 다양한 응급환자 배정 가상 데이터셋

이 코퍼스는 배정 알고리즘과 공통 서버의 다양한 실패·경계조건을 시험하기 위한 합성 데이터입니다. 실제 병원 통계·환자 기록·임상 기준을 사용하지 않았으며, 의료 모델의 학습 정답이나 현장 효과의 증거가 아닙니다.

## 제공 데이터

기본 생성 설정은 총 **6,815건**입니다. 파일은 GitHub에 함께 저장하므로 내려받은 뒤 바로 사용할 수 있습니다. 압축 JSONL은 한 줄이 한 사례이며 [로더](io.py)로 하나씩 읽습니다.

| 파일 | 건수 | 목적 |
|---|---:|---|
| [allocation.jsonl.gz](generated/allocation.jsonl.gz) | 6,000 | 50가지 프로필 × 120 seed. `observed`를 현재 솔버에 바로 입력 |
| [episodes.jsonl.gz](generated/episodes.jsonl.gz) | 600 | 20가지 프로필 × 30 seed. 환자 발생·미등록 유입·응답·치료·이동 사건 재생 |
| [server_cases.jsonl.gz](generated/server_cases.jsonl.gz) | 200 | 20가지 서버 흐름 × 10 seed. 공통 서비스의 입력·상태·예상 오류 검증 |
| [golden_cases.jsonl.gz](generated/golden_cases.jsonl.gz) | 15 | 최적 결과를 작은 완전탐색으로 확인하는 반례·경계조건 |
| [manifest.json](generated/manifest.json) | — | 프로필·건수·분할·해시·생성 설정 |
| [examples.json](generated/examples.json) | — | 압축을 풀지 않고 확인할 입력 예시 |

### 포함한 변화

- 규모: 환자 1·5·12·20·64명, 스트레스 128명; 병원 1·3·4·5·10·32곳, 스트레스 64곳.
- 수요와 가용량: 한산함, 급증·다수 환자, 거의 만실·완전 만실·용량 0, 병원별 큰 용량 차이.
- 치료 조건: 일부 병원만 가능, 한 병원에만 갈 수 있는 환자, 수용 응답 없음·일부 응답·만료.
- 자원: 자리 부족, 전문팀 부족·없음, 여러 자원 요구, 자원 양 경계조건, 점유 종료를 아는 경우·모르는 경우.
- 시간: 긴 이동, 짧은 대기 설정, 이미 기한 초과, 긴·짧은 치료, 짧은 계획 기간, 점유 종료와 다음 시작의 동시시각.
- 정보 오류: 가동 용량 과대·과소 표시, 자원 미확인, 미관측 환자 유입, 이동·치료시간 추정 오차.
- 기존 환자: 목적지 유지·대기 재평가, 기존 목적지 충돌, 치료 시작 구간 고정, 새 환자가 먼저 도착.
- 환자 정보: 전문가 확인·미확인·낮은 자기신고 구분, 모르는 의식·호흡·위치, 위험 신호의 119 안내.
- 이벤트: 응답 속도·변동·미응답 70%·100%, 외부 유입 0·급증, 의료공백지역, 병렬 문의 1·5곳, 복합 스트레스.

여러 자리 요구·전문팀만 요구하는 입력은 추상 자원 모델의 경계시험입니다. 일반 환자가 실제로 이런 자원을 사용한다는 가정이 아닙니다. 프로필은 선택한 조건의 조합이며 가능한 모든 상황을 포괄하지 않습니다.

## 생성과 검증

저장소 루트에서 실행합니다. Python 환경과 OR-Tools 설치는 [프로젝트 README](../README.md)를 따릅니다. 생성 자체는 표준 라이브러리를 쓰고, 검증·평가는 기존 솔버를 사용합니다.

```powershell
python -m datasets.generate
python -m datasets.validate --output datasets/generated/validation_report.json
python scripts/test_suites.py python test_server test_solver_improved test_simulation test_planning test_datasets -v
```

전체 구조·ID·분할·출처·파일 해시를 검사합니다. 서버 흐름 200건은 메모리 DB에서 실제 서비스 함수로 재생하고, 작은 정답 사례 15건은 완전탐색과 비교합니다. 결과는 [검증 보고](generated/validation_report.json)에 저장합니다. 큰 무작위 사례에 임의의 `OPTIMAL` 정답을 붙이지 않습니다. 가용량을 초과하는 기존 점유나 고정 목적지 충돌은 의도된 유효 입력입니다.

환자별 `accepted_hospitals`는 배정 입력의 현재 `eligible`과 일치해야 하며, 목적지를 확정하거나 치료 구간을 고정한 사례에도 같은 규칙을 적용합니다. 이벤트의 환자 요청 시각은 `patients.requested_at`과 일치하는지 확인합니다.

추가 생성 예시:

```powershell
python -m datasets.generate --output data/datasets-large --allocation-seeds 1000 --episode-seeds 100 --server-seeds 100
```

위 설정은 배정 50,000건·이벤트 2,000건·서버 2,000건·경계조건 15건을 만듭니다. 출력 폴더의 생성 파일을 갱신하므로 실험별 새 경로를 권장합니다. 원본 가상 데이터에는 솔버 정답이나 평가 결과를 저장하지 않습니다.

## 알고리즘에서 읽기

```python
from datasets.io import read_jsonl
from solver_improved import solve, validate

for case in read_jsonl('datasets/generated/allocation.jsonl.gz', split='test'):
    # actual은 정책에 전달하지 않는다.
    plan, meta = solve(case['observed'], seconds=0.1)
    if plan is not None:
        observed_ok = validate(case['observed'], plan)
        actual_ok = validate(case['actual'], plan)  # 숨겨진 상태에서의 사후 검사
    break
```

`observed`와 `actual`의 시간 단위는 **분**, 서버 시나리오는 **epoch 초 + 단계의 초 오프셋**입니다. 배정 입력의 `eligible`은 현재 유효한 수용 응답이 있다고 가정한 병원 인덱스입니다. 이미 확정된 목적지는 `destination_locks`, 시작까지 고정된 치료는 `fixed`입니다. `patient_metadata`의 입력 출처와 실제 우선순위는 부가 실험 정보이며 `actual`의 우선순위도 현재 모델의 보수적인 분류 규칙을 유지합니다.

규모 제한을 초과하는 프로필은 단독 솔버 스트레스 시험용입니다. 현재 공통 서버의 환자 64명·병원 32곳 제한과 구분하세요. 임상 허용 대기·치료시간·수용 응답 분포는 모두 실험 설정입니다.

## 스냅샷 성능 확인

```powershell
python -m datasets.evaluate --kind allocation --limit 200 --seconds 0.1 --output data/allocation-evaluation.json
```

seed 순서로 파일을 기록하므로 앞 200건에도 모든 프로필이 포함됩니다. 미배정·기존 충돌·큰 규모 제외를 각각 보고하고, 유효한 순차안과 솔버안의 목적값을 비교합니다. `actual`에서 실패하면 정보 오차로 계획이 실행 불가능해진 사례입니다. 이 결과는 전체 이송 서비스의 B1/B2 효과 비교가 아닙니다.

실험 조정용 `train` 80%, 검증용 `validation` 10%, 최종 확인용 `test` 10%로 나눕니다. 같은 seed의 모든 파생 프로필은 같은 분할에 포함하며, 서버 코퍼스는 seed 수가 작아 검증·최종 확인에 각 20건만 있습니다. 명칭이 학습된 의료 모델을 의미하지 않습니다. `golden`은 별도 `regression` 분할입니다. 최종 확인 분할에 맞춰 설정을 반복 조정하지 마세요.

## 저장된 사건으로 B0/B1 재생

```powershell
python -m datasets.evaluate --kind episode --limit 20 --output data/episode-replay.json
```

두 정책이 같은 환자·초기 점유·미등록 유입을 사용합니다. 응답·이동 난수도 동일한 키 규칙을 사용하며 정책별 객체는 새로 생성합니다. JSON에 저장한 응답 oracle은 최초 2회, 이동 oracle은 최초 구간입니다. 추가 문의·목적지 변경 구간은 같은 seed 규칙으로 재생합니다. 미래 사건·응답 oracle을 정책이 미리 읽으면 안 됩니다. B2 연결은 아직 별도 작업입니다.

기존 B0/B1 시뮬레이터는 후보 순위와 실제 이동에 같은 이동시간 함수를 사용합니다. 따라서 이 사건 재생을 **미래의 실제 이동시간을 숨긴 상태에서 추정 오차에 대응하는 정책의 검증**으로 해석할 수는 없습니다. 그 실험에는 예상 이동시간과 실제 이동시간을 분리한 정책 입력이 필요하며, 이번 데이터셋에서는 기존 시뮬레이터의 선택 규칙을 변경하지 않았습니다.

비교할 때는 같은 사례의 두 정책 결과를 짝지으세요. 서로 다른 프로필은 같은 seed여도 같은 환자·사건을 보장하지 않으므로, 프로필 간 차이를 특정 요인 하나의 인과효과로 해석하지 않습니다.

## 병원·환자 화면 시연용 DB

```powershell
python -m datasets.replay --case API-walkin_redirect-0000 --db data/walkin-demo.db
python -m server.app --db data/walkin-demo.db --port 8000
```

새 DB에만 재생하며 기존 DB는 덮어쓰지 않습니다. 단계 시각은 실행 시점에 맞춰 옮깁니다. `--seed`를 추가하면 재생한 DB가 초기화되므로 위 두 번째 명령 그대로 실행하세요. `walkin_keep`, `last_bed_race`, `expired_acceptance`, `unknown_resource` 등 다른 ID도 사용할 수 있습니다. 서버 사례는 일부 시나리오의 **최종 상태**를 만들며, 실시간 UI 클릭 자동화나 실제 HTTP 부하 검증은 아닙니다.

전체 구성은 [manifest](generated/manifest.json), 생성 규칙은 [profiles.py](profiles.py), 서버 흐름은 [server_cases.py](server_cases.py)에 있습니다.
