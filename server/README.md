# 공통 서버

Python JSON API와 SQLite로 환자 등록, 문의·응답, 목적지 확정, 이송·도착·퇴실을 처리한다. `server/app.py`가 API와 화면을 제공하고, 문의 워커와 배정 워커는 실행기에 따라 켜거나 끈다. 실행·구조·현재 적용 범위는 [실행 안내](../docs/RUNNING.md), [시스템 구조](../docs/ARCHITECTURE.md), [현재 상태](../docs/PROJECT_STATUS.md)를 따른다.

## 실행과 API

화면으로 같은 환자를 시연할 때는 새 가상 DB를 지정한다.

```powershell
python -B -m server.chat_demo --db data/ui-demo-new.db --port 18020
```

같은 origin의 `/paramedic/`, `/patient/`, `/hospital/?hid=H01`을 사용한다. `chat_demo`는 문의 워커를 켜고 배정 워커를 끈다. 공통 서버의 두 워커를 함께 사용하려면 `python -B -m server.team_runtime serve`를 사용한다. 전용 DB·고정 소스·환경 검사와 백업은 [실행 안내](../docs/RUNNING.md)에 있다.

| 기능 | 계약 |
| --- | --- |
| 환자·후보·수용·목적지·도착 | [공통 API](../docs/API.md) |
| 음성 인식문·초안·수정·명시 확인 | [음성 API](../docs/VOICE_INPUT_API.md) |
| 문의 정책·시작·중지·다음 문의 | [문의 워커](../docs/INQUIRY_WORKFLOW.md) |
| 같은 이송의 대화·병원 참여 구간 | [대화 API](../docs/CHAT_API.md) |
| 대기 환자정보 변경 | [환자 변경 API](../docs/PATIENT_AMENDMENTS_API.md) |
| 통화 기록·상대 기관 확인·정정 | [통화 기록 API](../docs/CALL_RECORD_API.md) |
| 업무 계측·중요 사건 수신·확인 | [업무 계측 API](../docs/BACKEND_MEASUREMENT_API.md) |
| 이송 중 병원 조건 재확인 | [조건 재확인 API](../docs/TRANSPORT_REVIEW.md) |
| 외부 유입·도착·퇴실 원장 | [외부 유입](../docs/EXTERNAL_ARRIVALS.md), [자원 정산](../docs/RESOURCE_ACCOUNTING.md) |

`GET /api/system/status`로 현재 프로세스의 DB·문의·배정 워커 상태를 확인한다. 꺼 둔 워커는 `DISABLED`, 관측기에 등록하지 않은 워커는 `UNKNOWN`이다. 추천 API는 `availability`, 버전·기한과 입력 부족 사유를 반환한다. 양쪽 화면은 현재 추천·입력 검토와 중요 알림을 표시한다.

화면 없이 서버 흐름을 확인하는 명령도 제공한다.

```powershell
python -B -m server.flow_rehearsal run --scenario all
python -B -m server.flow_rehearsal serve --db data/flow-new.db --port 18030
python -B -m server.team_demo --scenario normal
```

음성 텍스트 경로는 별도 STT 키 없이 검증할 수 있다. 서버 WAV 음성 인식은 Azure 설정을 사용하며, 브라우저 인식·TTS와 실제 마이크 검증은 [음성 안내](../docs/VOICE.md)를 따른다.

## 개별 API 서버와 문의 연구

`server.app`은 `--db`, `--scenario`, `--solver-seconds`, `--no-planner`, `--no-inquiry-worker`를 제공한다. `--seed`는 지정한 DB를 초기화하므로 새 가상 DB에만 사용한다. 실제 공개 자료 DB의 실행에는 `--seed`를 붙이지 않는다.

기본 문의 정책은 `fixed_3`이다. `GET /api/inquiry-policies`에서 `fixed_1/3/5`, `observed_v2`, `observed_v3`의 실제 버전·설정·제약을 조회한다. 환자 생성의 `inquiry_policy` 또는 기존 문의 작업의 `policy_name`으로 선택한다. 결정 근거는 워크플로의 `latest_decision`과 `/inquiry-decisions`에서 읽는다.

연구 실행기는 임시 DB 복제본을 사용하며 결과 경로를 새로 지정한다.

```powershell
python -B -m server.inquiry_experiment --output simulation_results/server-inquiry-new
python -B -m server.inquiry_v3_experiment development --output simulation_results/v3-development-new
python -B -m server.inquiry_v3_experiment validation --lock simulation_results/v3-development-new_lock.json --output simulation_results/v3-validation-new
```

정책·목적함수·채택 상태는 [알고리즘 안내](../docs/ALGORITHMS.md)에 있다. 연구 잠금과 과거 결과는 해당 시점의 소스·설정으로 재현한다.

## 구현 범위 (롱리스트 ID)
| 영역 | 구현 | 비고 |
|---|---|---|
| 환자 입력 | P02, P05, P07, P09, P10, P12, P13 | 위험 신호가 있으면 `call_119`. 미확인 중증도는 높음으로 처리(A02) |
| 탐색·문의 | P16~P29 (P23 포함) | 후보는 규칙 기반 필터. 솔버 배정이 아님 |
| 이동·도착 | P31~P41 | 조정 요청과 새 병원 수용 확인이 있어야 목적지 변경 가능 |
| 병원 정보 | H03~H05, H07, H11 | 제한 종료 예정이 지나도 자동 복구하지 않고 재확인 필요로 표시 |
| 문의 응답 | H15~H28 (H20 포함) | 중복 클릭은 그대로 성공, 다른 담당자와 충돌하면 CONFLICT |
| 도착 예정·돌발 | H29~H39 | B 유입 시 영향 환자 표시는 수량 기준 1차 판단 |
| 서버 | S01~S08, S15 | `BEGIN IMMEDIATE` 트랜잭션 안에서 검사와 반영 |
| 데이터 | A01, A05, S16, T01 | `sync_public.py` 지역별 수집·주기 갱신, `sync_region.py` 8개 시 일괄 수집·실험 스냅샷. 실제 병원 34곳 확보 |
| 배정 연결 | A26, S12의 1차 구현 | 수용 응답 기반 모델·목적지 고정·시작시각 재평가·스냅샷 검사·추천 API |

## 상태
- 이송 대화는 시스템 사건과 자유메시지를 함께 조회하고, 목적지 확정 후 현재 병원이 참여합니다. 목적지 변경 시 병원 참여 구간이 전환됩니다. [대화 API](../docs/CHAT_API.md)
- 문의: `PENDING → CHECKING → ACCEPTED / DECLINED`, `ACCEPTED → CONFIRMED / EXPIRED / CANCELLED`
- 환자: `WAITING → CONFIRMED → IN_TRANSIT → ARRIVED → COMPLETED`, 중간에 `CANCELLED`
- 수용 응답은 자원을 점유하지 않습니다. 목적지를 확정한 한 곳만 도착 예정 수요가 됩니다.
- 도착 확인 시 예정 수요는 실제 점유로 바뀌므로 이중으로 계산되지 않습니다.

## 공공 API 데이터 적재·갱신

공식 명세와 필드 매핑은 [api_field_mapping.md](../docs/api/api_field_mapping.md)를 참고하세요. 인증키는 공공데이터포털의 [국립중앙의료원 전국 응급의료기관 정보 조회 서비스](https://www.data.go.kr/data/15000563/openapi.do) 활용 신청 후 발급받습니다. 실호출 검증 범위는 안성·평택·오산·화성·용인·수원·천안·아산이며, 다른 지역·다중 페이지·장기 갱신은 추가 검증 대상입니다.

PowerShell에서 키를 입력하고 한 번 적재합니다.

```powershell
$publicApiKey = Read-Host '공공데이터포털 Decoding 인증키' -AsSecureString
$env:DATA_GO_KR_KEY = [System.Net.NetworkCredential]::new('', $publicApiKey).Password
python -m server.sync_public --db data/public.db --region 광주광역시 --district 동구
```

같은 PC에서 반복 사용할 때는 키를 사용자 환경변수에 한 번 등록할 수 있습니다.

```powershell
$publicApiKey = Read-Host '공공데이터포털 Decoding 인증키' -AsSecureString
[Environment]::SetEnvironmentVariable('DATA_GO_KR_KEY', [System.Net.NetworkCredential]::new('', $publicApiKey).Password, 'User')
```

이미 실행 중인 터미널·앱은 등록 값을 바로 상속받지 못할 수 있습니다. 수집을 실행할 터미널에서 아래처럼 사용자 등록 값을 현재 프로세스로 불러옵니다. 값은 출력하지 않습니다.

```powershell
$env:DATA_GO_KR_KEY = [Environment]::GetEnvironmentVariable('DATA_GO_KR_KEY', 'User')
python -m server.sync_public --db data/public.db --region 경기도 --district 평택시
```

실험 권역은 [experiment_region.json](../config/experiment_region.json)으로 고정했습니다. 환자 발생지는 안성·평택, 병원 수집 범위는 위 8개 시입니다. 등록된 사용자 인증키를 현재 터미널로 불러온 뒤 전체 권역을 한 번 수집하거나, 추가 호출 없이 현재 DB를 동결할 수 있습니다.

```powershell
$env:DATA_GO_KR_KEY = [Environment]::GetEnvironmentVariable('DATA_GO_KR_KEY', 'User')
python -m server.sync_region --db data/public.db
python -m server.sync_region --db data/public.db --export-only
```

2026-10-11 수집 당시에는 병원 목록·상세 34곳, 가용병상·중증질환 관측 각 28곳을 확보했습니다. 34곳 모두 주소·전화·유효 좌표가 있고 기관 ID 중복·미매칭은 없습니다. 병상 관측 28곳 중 신생아 중환자실 가용수는 5곳에서 수치가 확인됐습니다. 분만실은 수량 미확인이나 Y 10곳·N 1곳·누락 17곳을 별도 `service_availability`로 보존합니다. Y/N을 슬롯 1/0으로 바꾸지 않습니다. 그 수집 기록에서는 주기 갱신을 실행하지 않았습니다. 실제 현재 갱신 여부는 실행 중인 수집 프로세스와 DB 실행 기록으로 확인합니다. 상세 결과와 파일 위치는 [데이터확보 결과](../docs/research/경기남부_데이터확보_결과_20261011.md)를 참고하세요.

일괄 수집은 병원 상세·중증질환 조회를 포함합니다. 단일 시군구 수집은 `--details --severe`를 추가하면 같은 항목을 확보합니다. 지역별 자료를 모두 검증한 뒤 그 지역을 한 트랜잭션으로 적재하며, 한 지역의 실패가 다른 지역 수집을 중단시키지는 않습니다. 스냅샷에는 동결한 SQLite DB, 원본·정규화 JSON, 치료조건 평가와 SHA-256 검증값을 함께 저장합니다.

병원 기본정보는 기관 ID로 추가·갱신하고, 목록에 좌표가 없으면 상세 조회로 보완합니다. 가용병상 요청의 필수 시도·시군구를 모두 보냅니다. 여러 지역을 적재하려면 같은 DB 경로에 다른 `--region`, `--district`로 실행하세요. 기존 지역의 병원은 지우지 않습니다.

적재한 DB를 서버에서 사용합니다. 공공 DB를 실행할 때는 가상 DB를 새로 만드는 `--seed`를 붙이지 않습니다.

```powershell
python -m server.app --db data/public.db --port 8000
```

다른 터미널에서 키를 설정하고 주기 갱신 프로세스를 실행할 수 있습니다. 기본정보는 기본 24시간마다, 가용병상은 아래 예시에서 5분마다 수집합니다. 이 간격은 프로젝트 설정값이며 실제 호출 허용량에 맞춰 조정합니다. 프로세스를 종료하면 갱신도 멈춥니다.

```powershell
python -m server.sync_public --db data/public.db --region 광주광역시 --district 동구 --interval 300
```

저장 구조:

- `hospitals`: 기관 ID, 병원명·주소·전화·좌표. 신규 진료 역량은 `capabilities_status='unknown'`.
- `public_api_snapshots`: 목록·상세·가용병상·중증질환 원문과 정규화 값. 응급실·수술실·일반 중환자실·신생아 중환자실·분만실 가용수, 장비 Y/N, 등록 진료과·시설 수를 구분해 저장. 원천 입력시각이 없는 상세·중증질환은 미상으로 유지.
- `public_sync_runs`: 성공·실패, 수집 시각, 적재/누락 건수. 인증키와 인증키가 포함된 URL은 저장하지 않음.
- 기존 `resources`: 병원 웹의 운영 총량·점유. 공공 가용수로 덮어쓰지 않음.

병원 상세·후보 응답의 `public_data`에서 공개 관측을 확인할 수 있습니다. 공개 가용수는 환자별 수용 확답이나 자원 예약이 아닙니다. 운영 자원이 미확인이면 솔버가 공공 가용수를 임의의 총량으로 사용하지 않습니다. 솔버 추천에는 해당 병원의 유효한 환자별 응답과 확인된 운영 자원이 필요합니다. 수동 수용·확정은 기존 정책대로 병원 응답을 근거로 하고, 미확인 운영 자원은 경고 대상으로 다룹니다.

페이지 전체를 수집·검증한 뒤 한 트랜잭션으로 적재합니다. 실패·빈 응답으로 기존 병원을 삭제하지 않고, 병원 수동 자원·수용 제한·진료 역량·기존 문의·배정을 보존합니다. 중증질환 코드는 버전별 의미를 자동 해석하지 않고 원문으로 보존합니다. 심평원도 활용 신청 후 실제 수집을 완료했으며 세부 연계 범위는 아래와 같습니다.

[치료조건 카탈로그](../docs/research/치료조건_카탈로그_20261011.md)는 일반 응급·외상/정형·심장 중재·신경외과·소아·고위험 분만/신생아의 실험용 조건 6종입니다. `server/treatment_data.py`는 등록 근거·현재 공개 가용 관측·운영 문맥을 분리해 평가하고, `server/treatment_binding.py`가 이를 병원 조회·환자 후보·배정 결과에 연결합니다. 누락·오래된 값·공개 가용수 0은 기본적으로 재확인 대상으로 남깁니다. 환자별 조건은 임상 진단이나 공식 이송 지침이 아닙니다.

## 심평원 신고정보 연계

`server/hira_data.py`는 병원정보서비스 기본 조회와 의료기관별상세정보서비스 2.8의 상세 11종을 사용합니다. 진료과·의사/전문의 수·장비·시설·특수진료·응급실 운영과 진료시간·전문병원 지정 분야·간호등급·기타 인력·교통 안내·식대 가산을 수집합니다. `getEqpInfo2.8`은 시설정보이며 장비정보는 `getMedOftInfo2.8`입니다. 간호등급은 인원수가 아니며 교통 안내와 식대 가산은 치료조건으로 사용하지 않습니다. 신고 현황은 현재 당직·운영 가용수로 사용하지 않습니다.

2026-10-11 두 API의 실제 인증 호출로 34개 기관×12종, 총 408개 관측을 저장했습니다. 이름 검색에서 후보가 없는 기관은 좌표 주변 1km 목록으로 보완합니다. 기본 자동 연결 기준은 이름·주소 정규화 일치와 좌표 300m 이내이며 31곳이 이를 통과했습니다. 나머지 3곳은 공식 출처로 동일 기관을 확인한 뒤 [hospital_identity_links.json](../config/hospital_identity_links.json)에 기관쌍과 양쪽 식별정보 해시를 고정했습니다. 개별 검토 연결에서도 대표전화와 좌표 500m 상한을 검사하며, 다른 병원에 자동 매칭 완화를 적용하지 않습니다. 원본 명칭·주소·좌표는 보존합니다. 재수집 명령은 다음과 같습니다.

```powershell
$env:DATA_GO_KR_KEY = [Environment]::GetEnvironmentVariable('DATA_GO_KR_KEY', 'User')
python -m server.sync_hira --db data/public.db
python -m server.sync_region --db data/public.db --export-only
```

결과는 `data/hira_sync/latest.json`, DB 실행 기록은 `hira_sync_runs`, 수집 성공 관측은 `public_api_snapshots`에 저장합니다. 기존 병원 조회·후보 응답의 `public_data`에서도 읽습니다. 전체 성공은 종료 코드 0, 부분 성공·접근 오류는 1이며 미연결 기관을 자동으로 수용 불가 처리하지 않습니다. 세부 상태는 [추가 수집·판정 연결 결과](../docs/research/API추가수집_치료조건판정연결_20261011.md)를 참고하세요.

## NMC 수용 관련 메시지 수집

기존 NMC 기관 ID별로 수용 관련 메시지를 조회합니다. 병원 목록 수집 후 다음 명령을 실행합니다.

```powershell
$env:DATA_GO_KR_KEY = [Environment]::GetEnvironmentVariable('DATA_GO_KR_KEY', 'User')
python -m server.sync_messages --db data/public.db
python -m server.sync_region --db data/public.db --export-only
```

2026-10-11 34곳 모두 조회·저장에 성공했습니다. 9곳에서 63개 메시지를 받았고 25곳은 빈 결과였습니다. `nmc_acceptance_messages` 관측에 메시지 원문·질환 코드와 이름·표출 상태·적용 시작과 종료시각을 보존합니다. 메시지의 자유문장이나 빈 조회 결과를 환자별 수용·불가 확답으로 해석하지 않으며, 적용 기간은 원천 갱신시각을 대신하지 않습니다.

실행 결과는 `data/nmc_message_sync/latest.json`, DB 실행 기록은 `nmc_message_sync_runs`입니다. 수집 성공 관측은 다른 공공 자료와 함께 `public_api_snapshots`에 저장합니다. 재수집이 실패한 기관의 기존 관측은 보존합니다. 실행은 한 번 수집하는 방식이며 자동 주기 실행을 시작하지 않습니다.

## 치료조건 판정 연결

증상을 보고 치료 프로필을 자동 선택하지 않습니다. 가상 시나리오 또는 구급대·의료진 검토로 `POST /api/patients`의 `extra.treatment_profile_id`에 [카탈로그](../config/treatment_requirements.json)의 ID를 명시합니다. 예: `{"extra":{"treatment_profile_id":"TR_HIGH_RISK_BIRTH_NEONATAL"}}`를 환자 생성 본문에 포함합니다. 프로필을 지정하지 않으면 명시한 `required_capabilities`, `resource_demands`를 사용합니다.

선택한 프로필의 진료 분야와 추가 명시 분야는 합집합, 기본·확장·추가 자원 수요는 자원별 최댓값으로 병합해 DB에 저장합니다. 프로필의 ICU·수술실·분만실 수요를 생략하지 않습니다. 현재 솔버가 지원하지 않는 자원이 포함되면 `UNSUPPORTED_RESOURCE` 경고와 `NEEDS_REVIEW`로 반환합니다.

| 조회 위치 | 추가 필드 |
|---|---|
| `GET /api/hospitals/{hid}` 및 병원 목록 | `treatment_evaluations`: 실험 프로필 ID별 평가 |
| `GET /api/patients/{pid}/candidates`의 각 후보 | `treatment_evaluation`, `treatment_requirements`, 확인 필요 조건을 담은 `checks` |
| 환자 배정 조회의 `result` | 적용 조건 `treatment_requirements`, 선택 병원 `treatment_evaluation`, 병원별 `treatment_evaluations` |

평가의 `registered_evidence`는 등록 진료과·전문의·장비·시설·지정 분야 등을, `current_evidence`는 현재 가용성 비교를, `operational_context`는 응급실 운영 등록 정보와 NMC 메시지를 담습니다. 원본 필드·제공처·코드·수량·수집시각·원천시각을 구분합니다. 등록 진료과는 정확한 이름으로 비교하며 내과를 심장내과로 추정하지 않습니다. 등록 전문의 수를 현재 팀 가용수로, 등록 시설 수를 현재 병상 가용수로 바꾸지 않습니다.

공개 근거의 `MATCH`는 환자 수용 확답이 아닙니다. 현재 가용 관측이 누락되거나 오래됐으면 `NEEDS_CONFIRMATION`과 확인 항목을 반환하며, 이러한 미확인만으로 후보를 제거하지 않습니다. 솔버에는 별도의 유효한 환자별 수용 응답과 확인된 운영 자원이 필요합니다. 기존 명시적 병원 제한·운영 진료 역량 필터와 수동 수용·확정 정책은 유지합니다.

공공 관측 추가·수정·삭제는 `planning_state.revision`을 갱신해 이전 추천을 무효화합니다. 계산과 결과 저장 사이에 변경되거나 공개 가용 관측의 신선도 기준을 넘은 결과는 그대로 게시하지 않습니다. 이동시간의 실제 출처는 선택한 실행기와 응답을 확인합니다. `server.app`은 `KAKAO_REST_KEY` 설정 시 카카오 길찾기를 사용할 수 있고, 가상 화면 실행기 `chat_demo`는 거리 추정을 사용합니다. API 필드와 요청 예시는 [API 명세](../docs/API.md)를 참고하세요.

```sql
SELECT * FROM hospitals WHERE source = 'public';
SELECT * FROM public_api_snapshots;
SELECT * FROM public_sync_runs ORDER BY id DESC;
```

## 검증과 남은 범위

서버·솔버·수집기 검사는 [검증 안내](../docs/TESTING.md)를 따른다. 실제 기기·새 발화·사용성 측정, 실제 병원 연결과 권한 검증의 완료 여부는 [현재 상태](../docs/PROJECT_STATUS.md)에 정리한다. 이 README에 적힌 2026-10-11 기관·관측 수는 당시 수집 기록이다. 현재 가용수나 실제 환자 수용 결과는 각 조회의 출처·시각·미확인 상태로 확인한다.
