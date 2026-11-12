# 공공 API 원본 필드 → 내부 필드 매핑

검토일: 2026-10-11

**공식 명세 확인과 실제 인증 호출 검증을 구분한다.** 수집·적재 코드는 `server/public_data.py`, 지역별 실행은 `server/sync_public.py`, 일괄 수집·실험 동결은 `server/sync_region.py`에 있다. 2026-10-11 안성·평택·오산·화성·용인·수원·천안·아산에서 목록·상세 34곳, 가용병상·중증질환 관측 각 28곳을 실제 인증 호출로 확보했다. 중증질환은 통신과 원문 저장을 확인한 범위이며 코드 의미 자동 해석은 하지 않는다. 다른 지역·다중 페이지·장기 갱신은 추가 실호출 검증 대상이다.

## 1. 공식 근거와 명세 차이

- [공공데이터포털: 국립중앙의료원 전국 응급의료기관 정보 조회 서비스(15000563)](https://www.data.go.kr/data/15000563/openapi.do)
- 위 페이지의 참고문서 **NIA-IFT-OpenAPI활용가이드-01.국립중앙의료원-응급의료정보조회서비스_V13.hwp**를 직접 내려받아 확인했다. 개정이력의 최종 일자는 **2026-08-27**이다.
- 같은 페이지의 Swagger 상세기능·응답 모델도 확인했다. Swagger에는 일부 오래된 항목이 남아 있고, 목록 응답 모델이 빈 객체로 표시되는 문제가 있다. 따라서 최신 활용가이드의 응답표와 XML 예제를 함께 대조한다.
- 실제 응답이 명세와 다르면 원본을 보존하고 매핑을 다시 검토한다. 특히 중증질환의 숫자 코드는 API·버전별로 의미가 달라질 수 있다.

## 2. 조회 API 구분

공식 Base URL: `https://apis.data.go.kr/B552657/ErmctInfoInqireService/`

| 용도 | 오퍼레이션 | 주요 요청 항목 | 구현 범위 |
|---|---|---|---|
| 병원 목록 | `getEgytListInfoInqire` | `Q0` 시도, `Q1` 시군구, `pageNo`, `numOfRows` | 기본 수집 대상 |
| 병원 상세 | `getEgytBassInfoInqire` | `HPID` 기관 ID | 좌표 보완 및 등록 진료과·시설 수 수집 |
| 위치 기반 조회 | `getEgytLcinfoInqire` | `WGS84_LON`, `WGS84_LAT` | 이번 적재 경로에서 사용하지 않음 |
| 실시간 가용병상 | `getEmrrmRltmUsefulSckbdInfoInqire` | `STAGE1` 시도, `STAGE2` 시군구, 페이지 항목 | 병원별 공공 관측으로 저장 |
| 중증질환 수용 가능 | `getSrsillDissAceptncPosblInfoInqire` | `STAGE1`, `STAGE2`, 선택 `SM_TYPE` | 원문·메시지 저장 구현, 의미 자동 해석은 보류 |
| 수용 관련 메시지 | `getEmrrmSrsillDissMsgInqire` | `HPID`, `QN`, `Q0`, `Q1` 등 | 원문·표출 상태 해석 후 확장 대상 |

인증키 요청 항목은 공식 Swagger 표의 `ServiceKey` 표기를 사용하며, 평택 목록·가용병상 실호출에서 정상 인증을 확인했다. 공식 명세에서 가용병상·중증질환의 `STAGE1`, `STAGE2`는 모두 필수로 표시된다. 현재 CLI는 `--region`, `--district`를 모두 받는다. **시군구를 생략해 시도 전체를 받을 수 있는지는 실제 호출로 검증해야 하며, 명세만으로 가능하다고 가정하지 않는다.**

목록의 `Q0`, `Q1`은 선택 항목이다. 위치 API의 `distance` 단위·조회 반경은 이번 검토에서 확정하지 않았으며, 이를 도로 이동시간으로 사용하지 않는다.

## 3. 병원 목록·상세 → `hospitals`

목록 API의 아래 필드는 활용가이드 응답표와 XML 예제에서 확인했다. 상세 API에도 동일 기본 필드가 있다.

| 내부 필드 | 원본 필드 | 의미·처리 | 확인 상태 |
|---|---|---|---|
| `source_ids.nmc` | `hpid` | 기관 ID. 없거나 식별자로 부적합하면 적재하지 않음 | 목록·상세 34건, 병상·중증질환 각 28건 매칭 확인 |
| `id` | `hpid`로 생성 | 신규 내부 ID는 `NMC-` + 기관 ID. 기존 매칭이 있으면 기존 내부 ID 유지 | 내부 설계 |
| `name` | `dutyName` | 기관명. 없으면 적재하지 않음 | 명세·예제 확인 |
| `address` | `dutyAddr` | 주소. 신규 결측은 null, 갱신 시 결측으로 기존 값을 지우지 않음 | 명세·예제 확인 |
| `phone` | `dutyTel3`, 없으면 `dutyTel1` | 상세 Swagger는 응급실 전화/대표전화1로 설명. 목록 가이드의 `dutyTel3` 설명은 대표전화2이므로 실제 전화 용도 확인 필요 | 필드 확인, 설명 차이 있음 |
| `lat` | `wgs84Lat` | 병원 위도, 유효한 유한 실수인지 검사 | 명세·예제 확인 |
| `lon` | `wgs84Lon` | 병원 경도, 유효한 유한 실수인지 검사 | 명세·예제 확인 |
| `source` | 내부 생성 | `public`으로 저장해 시연용 `synthetic`과 구분 | 내부 설계 |
| `capabilities` | 이번에는 자동 매핑하지 않음 | 신규 공공 병원은 빈 목록 + `capabilities_status='unknown'`. 진료 역량 미확인과 진료 불가를 구분 | 내부 설계 |

목록 좌표의 실제 존재는 Swagger의 빈 응답 모델이 아니라 **활용가이드 목록 XML 예제의 `wgs84Lat`, `wgs84Lon`**으로 확인했다. 위치 API는 이와 다르게 **`latitude`, `longitude`**를 사용한다. 오퍼레이션을 구분하지 않고 모든 응답에 같은 좌표 필드명을 적용하면 안 된다.

상세의 `dgidIdName`은 쉼표로 구분된 등록 진료과목으로 정규화한다. 이는 **현재 근무 중인 전문의·수술팀의 가용성이나 특정 환자의 수용 확답이 아니다.** 원문과 등록 진료과를 공개 관측에 보존하며 운영용 `capabilities`를 덮어쓰지 않는다. `내과`에서 `심장내과`를 추론하지 않고, 카탈로그에 명시한 정확한 이름과 별칭만 비교한다.

상세 시설 수는 `hperyn`→응급실, `hpicuyn`→일반 중환자실, `hpcuyn`→신경 중환자실, `hpccuyn`→흉부 중환자실, `hpnicuyn`→신생아 중환자실, `hpopyn`→수술실, `hpgryn`→입원병상의 `reported_count`로 저장한다. 등록 시설 수를 현재 가동 병상·가용 수·환자별 수용 확답으로 해석하지 않는다. 상세 응답에 원천 입력시각이 없어 `source_observed_at=null`로 유지한다.

## 4. 실시간 가용병상 → `public_api_snapshots`

**공공 관측과 병원 웹의 운영 자원을 분리한다.** 공공 API 값을 `resources.capacity`, `resources.occupied`에 그대로 넣거나, 총량으로 보고 점유를 다시 차감하지 않는다.

| 저장할 의미 | 원본 필드 | 공식 의미·처리 | 구현 범위 |
|---|---|---|---|
| 연결할 기관 | `hpid` | 병원 목록의 기관 ID와 매칭. 미매칭 자료는 다른 병원에 추정 연결하지 않음 | 구현 |
| `resources[].reported_available` | `hvec` | 일반(응급실 일반병상). 0 이상의 정수만 관측값으로 저장 | 구현 |
| `resources[].reported_available` | `hvoc` | 수술실 공개 가용수, 내부 관측 유형 `operating_room` | 구현 |
| `resources[].reported_available` | `hvicc` | 일반 중환자실 공개 가용수, 내부 관측 유형 `icu_bed` | 구현 |
| `resources[].reported_available` | `hvncc` | 신생아 중환자실 공개 가용수, 내부 관측 유형 `nicu_bed` | 구현 |
| `resources[].reported_available` | `hv42` | 분만실 공개 가용수, 내부 관측 유형 `delivery_room` | 구현 |
| `service_availability[].reported_available` | `hv42` | Y/N이면 분만실의 공개 서비스 표시를 bool로 별도 저장. 수량은 미확인 유지 | 구현, 이번 자료 Y 10곳·N 1곳 |
| `resources[].source_field` | `hvec` | 어떤 원본 필드에서 왔는지 저장 | 구현 |
| `resources[].status` | 내부 생성 | 유효 정수이면 `reported`, 공백·누락·음수·특수값이면 `unknown` | 구현 |
| `source_observed_at` | **`hvidate`** | 입력일시. XML 예제 `20230414092700` 확인. 파싱 실패 시 null | 구현 |
| `fetched_at` | 서버 수집 시각 | 서버가 응답을 받은 시각. 원본 입력시각을 대신하지 않음 | 구현 |
| `raw_data` | 응답 item의 전체 필드 | 해석하지 않은 값과 원래 필드명·값 보존 | 구현 |

공식 명세·XML 예제에서 확인한 날짜 필드는 **`hvidate`**다. `hvdate`는 이번 확인 자료에 없으므로 공식 필드로 기재하지 않는다. 가이드의 입력일시 설명에 과거 서술형 날짜 예시도 남아 있어 모든 응답의 날짜 형식을 보장하지 않는다. 14자리 형식 외 허용 형식과 한국시간 해석은 클라이언트의 처리 정책이며 실제 응답에서 검증한다.

### 운영값과 구분해야 하는 필드

가용 수 5종은 공공 관측으로 정규화하지만 운영 `resources`에는 투영하지 않는다. 장비는 아래 Y/N 필드만 해석하며 그 밖의 값은 원문과 함께 미확인으로 남긴다.

| 원본 필드 | 최신 활용가이드의 의미 | 주의 |
|---|---|---|
| `HVS01` / 예제 `hvs01` | 일반_기준 | 가용 병상 수가 아님. 원문 보존 |
| `hvoc` | [기타] 수술실 | 전문팀·마취과 가용성과 동치가 아님 |
| `hvncc` | [중환자실] 신생아 | 신생아 환자별 수용 확답이 아님 |
| `hv42` | [기타] 분만실 | 고위험 분만 대응 가능 여부와 동치가 아님 |
| `hvctayn`, `hvmriayn` | CT·MRI 가용 | Y/N만 `reported_available` true/false, 나머지는 unknown |
| `hvangioayn`, `hvventiayn` | 혈관촬영기·인공호흡기 가용 | Y/N만 정규화. 검사·시술팀 가용성과 구분 |
| `hvventisoayn`, `hvincuayn` | 조산아 인공호흡기·인큐베이터 가용 | Y/N만 정규화. 실응답의 `N1`은 N이나 1로 바꾸지 않고 unknown |

입력시각을 알 수 없는 경우 신선도를 알 수 없음으로 표시한다. 수집이 성공했다는 이유만으로 병원이 방금 갱신했다고 표시하지 않는다. 오래된 관측이나 특수값은 수용 불가 확정 근거로 사용하지 않는다.

## 5. 중증질환 코드는 API·버전별로 구분

**현재 자동 후보 필터에 투영하지 않는다.** 최신 활용가이드의 `getSrsillDissAceptncPosblInfoInqire`와 Swagger·기본정보의 구 코드표는 같은 숫자에 서로 다른 의미를 부여한다.

| 필드 | 최신 중증질환 조회 가이드 | 기본정보/Swagger에 남아 있는 구 의미 |
|---|---|---|
| `MKioskTy1` / `mkioskty1` | 심근경색 재관류중재술 | 뇌출혈수술 |
| `MKioskTy8` / `mkioskty8` | 담도포함질환 | 조산산모 |
| `MKioskTy10` / `mkioskty10` | 장중첩/폐색(영유아) | 신생아 |
| `MKioskTy15` / `mkioskty15` | 저체중출생아 집중치료 | 구 코드표에 해당 항목 없음 |
| `MKioskTy16` / `mkioskty16` | 산부인과응급 분만 | 구 코드표에 해당 항목 없음 |
| `MKioskTy17` / `mkioskty17` | 산부인과응급 산과수술 | 구 코드표에 해당 항목 없음 |
| `MKioskTy25` / `mkioskty25` | 안과적 응급수술 | 응급실 |
| `MKioskTy28` / `mkioskty28` | 응급실 | 구 코드표에 해당 항목 없음 |

최신 가이드 응답표는 `Y:가능, N:불가`로 설명하지만 XML 예제에는 **`불가능`, `정보미제공`, 공백이 붙은 `Y `**도 나온다. 메시지 필드에는 연령·체중 등 부가조건이 들어갈 수 있다. 단순히 Y/N만 읽거나 숫자 코드를 전 API에 공통 적용하면 잘못된 필터링이 발생할 수 있다.

28곳의 실제 응답을 확보해 필드·메시지를 원문으로 저장했다. 원천 입력시각은 응답에서 확인되지 않아 미상으로 유지한다. 다음을 확정한 뒤에만 의미를 자동 해석한다.

1. 오퍼레이션·가이드 버전별 필드 의미 및 대소문자.
2. 실제 값의 집합과 결측·정보미제공 처리.
3. 연령·체중 등 부가조건 메시지의 보존·표시.
4. 병원 웹에서 확인한 진료 역량 및 환자별 수용 응답과의 구분.

## 6. 적재·갱신 원칙

- 페이지를 끝까지 수집하고 전체 건수·기관 ID 중복을 검사한 뒤 한 트랜잭션에서 적재한다. 통신 오류로 모은 일부 페이지만 정상 전체 자료로 적재하지 않는다.
- 공공 병원은 기관 ID로 갱신한다. 이름만 같다는 이유로 시연 병원이나 수동 등록 병원에 합치지 않는다.
- 병원 웹에서 설정한 운영 자원·수용 제한·진료 역량과 기존 환자·문의·배정은 공공 수집으로 덮어쓰지 않는다.
- 공공 관측 시각과 수집 시각을 구분하고, 기존 관측보다 오래된 관측으로 최신 값을 되돌리지 않는다.
- 시도/시군구·페이지 크기·갱신 간격은 실제 호출 허용량과 필수 파라미터를 확인해 정한다. 프로세스의 주기 실행이 구현돼도 실제 주기 수집이 가동 중이라는 뜻은 아니다.
- API 인증키·인증키가 포함된 URL을 DB, 문서, 로그에 남기지 않는다.

## 7. 실제 호출 결과와 남아 있는 검증

| 확인 대상 | 상태 |
|---|---|
| 발급 인증키로 정상 XML·성공 코드 수신 | 2026-10-11 8개 시 목록·상세·가용병상·중증질환 정상 호출. 목록·상세 34곳, 병상·중증질환 각 28곳 |
| 가용병상 시군구 필수 여부와 시도 전체 조회 허용 여부 | 요청한 8개 시의 STAGE1·STAGE2 조합 정상. 시군구 생략 호출은 미검증 |
| 모든 페이지의 `totalCount`, `pageNo` 일관성 | 수집 지역의 단일 페이지 정상. 다중 페이지는 단위 테스트만 통과, 실제 자료는 미검증 |
| 좌표·주소·연락처 결측률 | 목록 34건 모두 유효 좌표·주소·전화 수신. 전화의 실제 용도는 별도 확인 필요 |
| 입력일시 파싱과 신선도 | 가용병상 hvidate를 원천시각으로 사용. 상세·중증질환 원천시각은 미상. 장기 갱신·지연 검증은 별도 |
| 응급실·수술실·일반 ICU·신생아 ICU·분만실 | 병상 관측 28곳 중 수치 확인 27·28·18·5·0곳. 나머지는 unknown, 수치 0과 구분 |
| 분만실 Y/N | 28곳 중 Y 10곳·N 1곳·누락 17곳. bool로 별도 정규화, 슬롯 수량이나 고위험 분만 수용 확답으로 사용하지 않음 |
| 특수값·음수 병상 수·장비 N1의 의미 | 미확정, unknown으로 보존 |
| 중증질환 최신 코드·값·부가조건 | 원문 28곳 확보. 코드·버전 의미 자동 해석은 보류 |
| 실제 병원 자료를 넣은 DB | data/public.db 병원 34곳. 기관 ID 중복·미매칭·권역 외 주소 0, foreign_key_check 이상 없음, integrity_check=ok |
| 실제 병원 자료의 조회 응답·화면 연결 | 최초 평택 6건 적재에서 조회 API HTTP 200 확인. 현재 34곳 데이터의 실험·기존 화면 전환은 별도 |
| 주기 수집의 장기 실행 | CLI 기능 테스트 완료, 실제 장기 실행은 미검증·미가동 |

필드별 `present`, `empty`, `missing` 집계는 `python -m server.probe_apis`로 실제 응답을 확보한 후 비교한다. 명세를 읽었다는 이유만으로 실호출 검증 완료로 표시하지 않는다.

## 8. 심평원 신고 현황과 기관 매칭

[심평원 병원정보서비스(15001698)](https://www.data.go.kr/data/15001698/openapi.do), [의료기관별상세정보서비스(15001699)](https://www.data.go.kr/data/15001699/openapi.do)의 현재 공식 Swagger에서 다음 경로·필드를 확인했다. 예전 메타데이터에 남은 2.7을 현행 경로로 사용하지 않는다.

목록 기본주소는 `https://apis.data.go.kr/B551182/hospInfoServicev2/`, 상세 기본주소는 `https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/`다. 목록은 `ServiceKey`, 상세는 `serviceKey`를 사용한다. 상세 요청은 기관별 `ykiho`, `pageNo`, `numOfRows`를 보내며 XML 응답을 읽는다.

| 내부 관측 | 오퍼레이션 | 주요 원본 필드 | 의미 |
|---|---|---|---|
| `hira_hospital_basis` | `getHospBasisList` | `ykiho`, `yadmNm`, `addr`, `telno`, `XPos`, `YPos`, `drTotCnt`, `mdeptSdrCnt` | 암호화 기관 ID·기본정보·신고 인력 수. XPos 경도, YPos 위도 |
| `hira_departments` | `getDgsbjtInfo2.8` | `dgsbjtCd`, `dgsbjtCdNm`, `dgsbjtPrSdrCnt` | 등록 진료과·의사수. 현행 설명상 의사수를 전문의수로 바꾸지 않음 |
| `hira_specialists` | `getSpcSbjtSdrInfo2.8` | `dgsbjtCd`, `dgsbjtCdNm`, `dtlSdrCnt` | 전문과목별 신고 전문의 수. 현재 당직·가용 인원 아님 |
| `hira_equipment` | `getMedOftInfo2.8` | `oftCd`, `oftCdNm`, `oftCnt` | 장비 코드·이름·신고 보유대수. 현재 사용 가능 여부 아님 |
| `hira_facilities` | `getEqpInfo2.8` | `emymCnt`, `soprmCnt`, `partumCnt`, `nbySprmCnt`, `aduChldSprmCnt`, `chldSprmCnt`, `permSbdCnt` | 응급실·수술실·분만실·NICU·성인/소아 ICU·허가 병상 수. 현재 가용수 아님 |
| `hira_special_care` | `getSpclDiagInfo2.8` | `srchCd`, `srchCdNm` | 특수진료 구분 원문. 환자별 수용·특정 시술 가능으로 자동 확장하지 않음 |

`server/hira_data.py`, `server/hira_matching.py`, `server/sync_hira.py`에 수집·정규화·저장을 구현했다. **2026-10-11 실제 호출로 34곳 모두를 연결하고 기본정보·상세 11종, 총 408개 관측을 DB에 저장했다.** 일반 자동 연결에서 남은 3곳은 공식 출처를 검토한 기관쌍·식별정보 해시로 제한적으로 연결했다.

자동 연결은 정규화한 병원명 완전 일치, 괄호 부가정보를 제거한 주소 완전 일치, 양쪽 유효 좌표 300m 이내를 모두 요구한다. 전화는 응급실·대표전화가 다를 수 있어 보조 근거로만 기록한다. 중복 기관 ID·복수 후보·기존 연결 충돌은 보류한다. 적재 직전 같은 트랜잭션에서 기관쌍을 다시 검사하고 `source_ids.hira`에 검증된 연결만 저장한다.

상세 11종과 기본정보 1종을 모두 받은 병원만 묶음으로 저장한다. 일부 실패한 병원은 이전 관측을 유지하며 성공한 다른 병원은 적재할 수 있다. 권한 오류는 반복 호출하지 않는다. 빈 결과는 시설·인력이 없다는 뜻으로 사용하지 않으며 원천 입력시각은 미상으로 유지한다. 운영 `resources`·`capabilities`·병원 수동 제한은 덮어쓰지 않는다.

실행·신청·결과 위치는 [분만실 Y/N과 심평원 연계 상태](../research/분만실_YN_심평원_연계_20261011.md)를 참고한다.

## 9. 추가 상세정보와 수용 메시지

| 내부 관측 | 오퍼레이션 | 주요 원본 필드 | 사용 기준 |
|---|---|---|---|
| `hira_operating_details` | `getDtlInfo2.8` | `emyDayYn`, `emyNgtYn`, `emyDayTelNo1`, `trmtMonStart`, `rcvWeek` 등 | 운영 신고값·시간·연락처. 알 수 없는 숫자 코드를 Y/N으로 추정하지 않음 |
| `hira_designated_specialties` | `getSpclHospAsgFldList2.8` | `srchCd`, `srchCdNm` | 지정 분야 원문. 개별 환자 치료 확답 아님 |
| `hira_nursing_grades` | `getNursigGrdInfo2.8` | `tyCd`, `tyCdNm`, `careGrd` | 등급은 현재 간호인력 수가 아님 |
| `hira_other_personnel` | `getEtcHstInfo2.8` | `gnlNopDtlCd`, `dtlGnlNopCdNm`, `gnlNopCnt` | 신고 기타 인력 수. 현재 근무 인원 아님 |
| `hira_transport` | `getTrnsprtInfo2.8` | `trafNm`, `lineNo`, `arivPlc`, `dir`, `dist`, `rmk` | 대중교통 안내. 구급차 이동시간으로 사용하지 않음 |
| `hira_meal_additions` | `getFoepAddcInfo2.8` | `tyCd`, `tyCdNm`, `trmealGrd`, `calcNopCnt`, `gnmAddcYn` | 식대가산 신고값. 환자 수용 판정에서 제외 |
| `nmc_acceptance_messages` | `getEmrrmSrsillDissMsgInqire` | `hpid`, `symBlkMsg`, `symBlkMsgTyp`, `symTypCod`, `symTypCodMag`, `symOutDspYon`, `symOutDspMth`, `symBlkSttDtm`, `symBlkEndDtm` | 메시지·질환·표출 상태·적용 기간 원문. 자유문 자동 제외 금지 |

NMC 메시지의 `symOutDspYon`은 Y/해제, N/차단이라는 **표출 상태**다. 환자 수용 가능 Y/N과 다르다. `symOutDspMth`의 A/자동, B/수동도 표출 방식이다. 시작·종료 시각은 원천 갱신시각이 아니므로 `source_observed_at`은 null로 남긴다. 빈 응답은 수용 가능 확답이 아니다.

2026-10-11 심평원 34기관×12종=408관측, NMC 메시지 34관측·63건을 실제 수집했다. 판정에는 정확한 등록 진료과, 조건별 신고 수·현재 가용 관측을 분리해 연결하고 운영 정보·메시지는 확인 문맥으로 제공한다. 검토된 기관쌍 3개는 공식 출처·원본 식별정보 해시·전화 일치·500m 상한으로 제한하며 일반 자동매칭은 300m 기준을 유지한다. [수집·판정 연결 결과](../research/API추가수집_치료조건판정연결_20261011.md)에 실제 저장과 검증 범위를 정리했다.
