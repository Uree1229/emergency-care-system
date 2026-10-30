# 공통 서버 (시연용)

별도 구급대원 앱 **이송 노트**는 `/paramedic/`에서 제공합니다. 팀원 화면과 분리해 새로 만든 음성 입력·수정·확인·환자 등록·병원 후보 조회 앱입니다. [실행·사용 방법](../paramedic-ui/README.md).

2026-10-30: [구급대원 음성 입력·수정·확인 API](../docs/VOICE_INPUT_API.md)를 추가했습니다. 음성 인식문에서 검토용 초안을 만들고, 항목별 수정·되돌리기·읽어주기 텍스트·현재 버전 확인 뒤 기존 환자 등록/수정으로 연결합니다. 앱 연결용 요청·응답·JavaScript 예제와 Azure 설정은 해당 문서에 있습니다. 텍스트 경로는 별도 STT 키 없이 검증할 수 있고, WAV 음성 인식은 Azure 키가 필요합니다. 실제 오디오 스트리밍과 TTS 재생은 앱 연계 대상입니다.

2026-10-29: 팀원 프론트엔드를 비워 두고 서버 흐름만 확인하려면 `python -m server.flow_rehearsal run --scenario all`을 실행합니다. API만 계속 켜려면 `python -m server.flow_rehearsal serve --db data/flow-server.db --port 18030`을 사용합니다. [전체 연결 흐름·역할별 API·결과 보고서](../docs/FRONTEND_FREE_FLOW.md).

2026-10-29: [응답 관측 수정·업무 계측·중요 사건 확인 API](../docs/BACKEND_MEASUREMENT_API.md)를 추가했습니다. 화면 연결과 실제 사용자 인증은 별도입니다.
같은 이송 건의 [통화 결과 기록·상대 기관측 내용 확인·정정 API](../docs/CALL_RECORD_API.md)도 제공합니다. 통화 기록은 수용·배정 상태를 자동으로 바꾸지 않습니다.
문의 후에는 [버전 기반 환자정보 수정](../docs/PATIENT_AMENDMENTS_API.md)을, 이송 ID를 모르는 새 통화 발견에는 `GET /api/hospitals/{hid}/call-records`를 사용합니다. 일반 정보 수정은 목적지 확정 전 단계만 지원합니다.

키 없이 전체 연결 흐름을 테스트하려면 `python -m server.team_demo --scenario normal`을 실행합니다. [설치·실행·역할별 조작 순서](../docs/TEAM_TEST_GUIDE.md).


병원 웹과 환자 웹이 함께 쓰는 JSON API 서버입니다. API·DB는 Python 표준 라이브러리를 사용하고, 배정 워커는 OR-Tools를 사용합니다.
임상 현장과 연결된 시스템이 아니고, `--seed` 시연 데이터는 모두 가상(`source='synthetic'`)입니다. 2026-10-11 공공 API 실제 인증 호출로 경기남부·천안·아산 8개 시의 NMC 병원 목록·상세 34곳, 가용병상·중증질환 관측 각 28곳을 `data/public.db`에 적재했습니다. 추가로 심평원 34곳의 기본정보·상세 11종, 총 408개 관측과 NMC 34곳의 수용 관련 메시지 조회 결과(63개 메시지)를 저장했습니다. 중증질환 코드는 원문 보존 단계이며, 병원별 실제 수용 확답이나 현재 전문팀 가용 정보는 확보하지 않았습니다.

## 실행

2026-10-14 추천 API는 현재 프로세스의 워커 `availability`와 유효한 수용 응답별 입력 부족 사유 `eligibility_reasons`를 제공한다. 워커 비활성 시 재계산은 409로 거부하며, 운영 자원이 없는 병원의 유효한 수용 응답을 “응답 없음”으로 표시하지 않는다. 양쪽 UI의 추천 패널은 팀원 후속 연결 대상이다. [연결 기준](../docs/TEAM_RECOMMENDATION_INTEGRATION.md).

2026-10-14 `GET /api/system/status`로 DB 읽기, 현재 프로세스의 문의·배정 워커, 작업 오류와 추천·자료 갱신 현황을 확인할 수 있다. 조회는 업무 데이터를 변경하지 않는다. 꺼 둔 워커는 `DISABLED`, 별도 실행기에서 관측기에 등록하지 않은 워커는 `UNKNOWN`이다. [상태 해석과 연결](../docs/SYSTEM_STATUS.md).

2026-10-14 외부 유입의 등록·조회·개별 퇴실 API를 추가했다. 같은 요청 재전송으로 점유가 중복 증감하지 않으며 과거 기록과 수동 집계 보정은 자동 차감하지 않는다. 병원 웹의 목록·퇴실 UI 연결은 후속 작업이다. [API 계약과 연결 기준](../docs/EXTERNAL_ARRIVALS.md).

환자·병원 연결본은 `python -m server.chat_demo --db data/ui-integration-demo.db --port 18020`으로 실행한다. 같은 서버에서 `http://127.0.0.1:18020/patient/`, `http://127.0.0.1:18020/hospital/?hid=H04`를 연다. 별도 환자 DB를 사용하지 않으며 기존 DB는 삭제하지 않는다. 가상 병원만 있는 전용 DB에서 초기 문의·대화·조건 재확인·사람의 목적지 변경·도착과 퇴실을 진행한다. [화면별 기능과 통합 검증](../docs/UI_INTEGRATION.md).

수용 확정 이후 조건 재확인과 대체병원 문의는 `python -m server.chat_demo --db data/transport-review-demo.db --port 18019`로 별도 가상 DB에서 시연합니다. 환자별 감시를 켠 뒤 병원 조건 변경을 등록하면 재확인 대상으로 표시하고, 병원이 조정 필요를 판단하면 서버가 대체병원에 문의합니다. 새로운 목적지는 사람이 확정합니다. [흐름·API·위치 갱신·검증](../docs/TRANSPORT_REVIEW.md).
초기 자동 문의와 대화를 함께 시연하려면 `python -m server.chat_demo --db data/auto-inquiry-demo.db --port 18017` 실행 후 `http://127.0.0.1:18017/conversation-demo.html`을 엽니다. [자동 문의 규칙과 API](../docs/INQUIRY_WORKFLOW.md).
대화 서버만 가상 데이터로 시연하려면 `python -m server.chat_demo` 실행 후 `http://127.0.0.1:18016/conversation-demo.html`을 엽니다. 실제 API에 연결된 별도 시연 화면이며 기존 환자·병원 UI 통합과 구분합니다.

```
python -m server.app --db data/demo.db --seed --scenario sudden --port 8000
python -m unittest test_server test_solver_improved test_simulation test_planning test_public_data test_treatment_data test_regional_data test_hira_matching test_hira_data test_hira_sync test_nmc_messages -v
```
`--seed`를 주면 가상 병원 10곳을 새로 넣습니다. 이때 기존 DB는 지워집니다.
`KAKAO_REST_KEY` 환경변수가 있으면 이동시간을 카카오 길찾기로 구하고, 없으면 직선거리 보정 추정을 씁니다.
배정 워커는 기본으로 함께 시작하며 솔버를 별도 프로세스로 실행합니다. `--solver-seconds 0`은 순차 배정만 사용하고, `--no-planner`는 워커를 끕니다. 기존 DB는 새 필드를 추가하는 마이그레이션으로 보존합니다.
초기 문의 워커는 솔버 워커와 독립적으로 실행합니다. `auto_inquiry: true`인 WAITING 환자만 처리하며, `--no-inquiry-worker`로 끌 수 있습니다. 기본 3곳·60초 정책은 개발 설정이며 수용과 목적지는 자동 확정하지 않습니다.

`GET /api/inquiry-policies`에서 `fixed_1`, `fixed_3`(기본), `fixed_5`, `observed_v2`, `observed_v3` 명세를 조회합니다. 생성 시 `inquiry_policy`, 기존 작업에는 `/api/patients/{pid}/inquiry-workflow`의 `policy_name`으로 선택하며, 실행 중 정책 변경은 먼저 일시정지해야 합니다. 선택한 이름·버전·설정을 DB에 저장하고 `latest_decision`과 `/inquiry-decisions`로 결정 근거를 조회합니다. 요청 예시는 [API 명세](../docs/API.md#초기-자동-문의-정책-선택), 계산·관측 규칙은 [자동 문의 명세](../docs/INQUIRY_WORKFLOW.md)를 참고하세요.

`inquiry_policies.py`는 고정 정책과 실험 V2의 순수 계산 어댑터이고, `inquiry_observations.py`는 이미 발생한 첫 회신·타임아웃·미해결 문의를 재구성합니다. V2의 점수는 수용 확률·임상 예후 예측값이 아니며 서버 적용의 성능 개선은 별도로 검증해야 합니다. 기능 회귀 검증은 `python -m unittest test_inquiry_workflow test_inquiry_policies -v`로 실행합니다.

`python -m server.inquiry_experiment --output simulation_results/server_inquiry_comparison_new`는 임시 DB 복제본에서 서버 문의 정책을 같은 합성 조건으로 비교합니다. 2026-10-12 기본 64개 시나리오·256분기의 결과와 반례는 [서버 정책 비교](../docs/INQUIRY_EXPERIMENT.md)에 정리했습니다. V2는 문의 혼잡에서 유리했지만 무응답·과거 상태 변화에서는 실패했습니다. 범용 성능 개선이나 실제 의료기관 효과가 입증됐다는 뜻은 아닙니다.

V3 문의 정책은 V2의 무응답 대기와 오래된 이력 의존을 개선하는 실험 정책입니다. 최근 문의 이후의 추가 문의 대기 상한과 이력 사용 범위를 개발 데이터에서 선택하고, 별도 seed로 평가했습니다. 공개 `observed_v3`의 실제 설정은 API 명세의 `source_variant`와 `config`를 읽으세요. 기본 정책은 계속 `fixed_3`입니다. [선택 절차·결과·한계](../docs/INQUIRY_V3_EXPERIMENT.md).

```powershell
python -m server.inquiry_v3_experiment development --output simulation_results/my_v3_development
python -m server.inquiry_v3_experiment validation --lock simulation_results/my_v3_development_lock.json --output simulation_results/my_v3_validation
```

이미 존재하는 결과는 덮어쓰지 않습니다. 개발 잠금은 소스 지문·전체 설정·개발 결과 해시를 확인하며, 검증 결과로 설정을 다시 고르지 않습니다. 고정한 기존 4종의 비교 CLI는 계속 4개 정책만 실행합니다.

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
- 이송 대화: 시스템 사건과 자유메시지 통합 조회·저장, 목적지 확정 후 병원 참여, 재배정 시 참여 구간 전환. [대화 API와 화면 연결 방법](../docs/CHAT_API.md). 양쪽 UI 연결은 별도 작업입니다.
- 문의: `PENDING → CHECKING → ACCEPTED / DECLINED`, `ACCEPTED → CONFIRMED / EXPIRED / CANCELLED`
- 환자: `WAITING → CONFIRMED → IN_TRANSIT → ARRIVED → COMPLETED`, 중간에 `CANCELLED`
- 수용 응답은 자원을 점유하지 않습니다. 목적지를 확정한 한 곳만 도착 예정 수요가 됩니다.
- 도착 확인 시 예정 수요는 실제 점유로 바뀌므로 이중으로 계산되지 않습니다.

## 공공 API 데이터 적재·갱신 (2026-10-11)

공식 명세와 필드 매핑은 [api_field_mapping.md](../api_field_mapping.md)를 참고하세요. 인증키는 공공데이터포털의 [국립중앙의료원 전국 응급의료기관 정보 조회 서비스](https://www.data.go.kr/data/15000563/openapi.do) 활용 신청 후 발급받습니다. 실호출 검증 범위는 안성·평택·오산·화성·용인·수원·천안·아산이며, 다른 지역·다중 페이지·장기 갱신은 추가 검증 대상입니다.

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

2026-10-11 현재 병원 목록·상세 34곳, 가용병상·중증질환 관측 각 28곳을 확보했습니다. 34곳 모두 주소·전화·유효 좌표가 있고 기관 ID 중복·미매칭은 없습니다. 병상 관측 28곳 중 신생아 중환자실 가용수는 5곳에서 수치가 확인됐습니다. 분만실은 수량 미확인이나 Y 10곳·N 1곳·누락 17곳을 별도 `service_availability`로 보존합니다. Y/N을 슬롯 1/0으로 바꾸지 않습니다. 현재 주기 갱신 프로세스는 가동하지 않았습니다. 상세 결과와 파일 위치는 [데이터확보 결과](../docs/research/경기남부_데이터확보_결과_20261011.md)를 참고하세요.

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

공공 관측 추가·수정·삭제는 `planning_state.revision`을 갱신해 이전 추천을 무효화합니다. 계산과 결과 저장 사이에 변경되거나 공개 가용 관측의 신선도 기준을 넘은 결과는 그대로 게시하지 않습니다. 실제 도로 이동시간은 `KAKAO_REST_KEY`가 없어 추가 수집하지 않았으며 기존 직선거리 보정 추정이 유지됩니다. API 필드와 요청 예시는 [API 명세](../docs/API.md)를 참고하세요.

```sql
SELECT * FROM hospitals WHERE source = 'public';
SELECT * FROM public_api_snapshots;
SELECT * FROM public_sync_runs ORDER BY id DESC;
```

## 아직 안 한 것
- 환자 웹을 독립 DB 대신 공통 API에 연결하고, 두 화면에 배정 결과를 표시하는 작업: 다른 팀원 담당. API 명세는 `docs/API.md`
- 실제 점유 종료시각·질환별 자원 일정 연계: 현재 점유 종료시각은 알 수 없어 계획 기간 전체를 차단함. 치료시간·최대 대기는 실험 설정값
- 기존 `affected_patients`는 수량 기준이며, 추가된 `incoming.planning`은 솔버 평가 결과. 기존 목적지가 불가능한 모델은 전체 계획을 `NEEDS_REVIEW`로 반환
- 공공 API 후속 검증: 수집 범위 밖 지역, 다중 페이지 실응답, 중증질환 코드 의미·부가조건, 장기 갱신. 8개 시 목록·상세·가용병상·중증질환 수집은 확인함
- 실제 데이터와 실험·화면 연결: 현재 시뮬레이션은 별도 가상 데이터를 사용. 배정 워커는 병원 최대 32곳, 운영 자원 `ed_bed`·`special_team`만 지원하므로 34곳 전체와 추가 자원 유형을 바로 적용할 수 없음
- 접근 권한(O01): 로그인이 없어서 병원 ID 경로로만 구분함
