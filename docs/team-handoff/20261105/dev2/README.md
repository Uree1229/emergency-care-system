# dev2 병원 웹 인계 — 추가 질문 초안 분리

2026-11-05 확인 기준은 main `6fbf6c6`의 병원 문의 A2 화면이다. 작업을 시작할 때 원격 main과 실제 코드를 다시 확인한다. 첫 PR은 **문의별 추가 질문 초안 분리**에 집중한다. [A2 PR #19](https://github.com/Uree1229/emergency-care-system/pull/19)

## 첫 작업 — 병원 문의별 추가 질문 초안 분리

dev2, 기존 채팅 복구(H01)보다 먼저 이 작업을 해줘. 문의 A에 질문을 쓰다가 B를 열면 A의 문장이 B 입력창에 나타날 수 있어. 최신 원격 `main`에서 별도 브랜치를 만들고, 이번 PR은 `hospital-ui/`의 초안 처리와 필요한 병원 테스트로 범위를 잡아줘.

**첫 PR 완료 기준**

- 초안은 서버 주소·병원 ID·문의 ID별로 관리해. A와 B를 오가면 각 문의의 초안만 보여야 해. 같은 문의를 자동 갱신할 때는 입력과 포커스를 유지해. 병원이나 서버를 바꾸거나 작업공간을 초기화하면 이전 초안이 따라오지 않아야 해.
- 보내기를 누른 문의에만 전송해. 성공하면 제출한 초안만 지우고, 실패하면 해당 초안을 남겨줘. 응답이 늦게 와도 다른 문의의 입력·포커스·알림이나 이후 편집분을 바꾸면 안 돼.

합성 문의 A·B로 브라우저에서 확인하고, [반납 형식](#반납-형식)에 맞춰 자동 갱신·전송 실패·응답 지연 테스트 결과와 PR 링크를 남겨줘.

## 재현 근거

- `renderDetail()`은 기존 `questionInput.value`와 포커스를 먼저 읽는다. 문의 ID 비교는 펼친 영역에만 적용하고, 새 문의에 이전 입력과 포커스를 복원한다. [렌더링 코드](https://github.com/Uree1229/emergency-care-system/blob/6fbf6c6/hospital-ui/index.html#L487-L553)
- 문의 카드 클릭은 `selectedId`를 바꾼다. 질문 버튼은 입력창의 문장을 읽고 `respond()`는 현재 문의 ID의 응답 경로로 보낸다. [클릭 처리](https://github.com/Uree1229/emergency-care-system/blob/6fbf6c6/hospital-ui/index.html#L768-L786) · [응답 처리](https://github.com/Uree1229/emergency-care-system/blob/6fbf6c6/hospital-ui/index.html#L413-L441)
- 기존 렌더 함수를 Node VM의 DOM 모형에서 실행했다. A에서 `A-specific draft`를 작성하니 같은 문의의 재렌더에서는 유지됐고, B 선택 후에도 `inquiry=2`, `input=A-specific draft`, `questionOpen=true`였다. 이 재현의 POST 요청 수는 0건이다.

## 완료 기준

1. 한 병원에서 열린 가상 문의 A와 B를 준비한다. A에만 초안을 쓰고 B를 선택하면 빈 질문창으로 시작한다. B에도 다른 초안을 쓰면 A↔B 이동에서 각자 자기 문장만 복원한다.
2. A 작성 중 같은 문의의 실제 자동 조회로 상세가 다시 그려져도 값·펼침·포커스를 유지한다. 문의 전환 시 이전 입력 포커스를 강제로 복원하지 않는다. 목록에서 사라진 문의, 닫힌 문의, 빈 상세 상태도 확인한다.
3. 병원이나 서버를 바꾸거나 `resetWorkspace()`가 실행되면 초안과 관련 표시를 정리한다. 두 서버에서 같은 문의 번호를 반환하는 경우도 서로 섞이지 않는다. [컨텍스트 전환](https://github.com/Uree1229/emergency-care-system/blob/6fbf6c6/hospital-ui/index.html#L788-L809)
4. 가상 A의 보내기 클릭으로만 `POST /api/inquiries/{A의 ID}/respond`가 발생하고 `action: question`과 A 문장이 들어간다. 성공은 제출한 A 초안만 정리한다. 기존에 작성한 B 초안과 이후 편집분을 보존한다. 추가 질문은 기존 요청 계약을 그대로 사용한다.
5. 실패·응답 지연 중 A→B 전환을 검증한다. 실패의 원래 A 초안과 기존 오류 안내를 보존하고, B의 초안·포커스·화면에 A 결과를 덮어쓰지 않는다. 새 요청을 자동으로 보내지 않는다.
6. 브라우저에서 A↔B와 3초 갱신을 확인하고, 범위 분리·전송 성공·실패·지연을 새 `test_hospital*` 회귀 검사에 넣는다. 기존 병원 워크플로·운영 테스트도 실행한다. 시험은 별도 포트와 새 합성 DB에서 진행한다. [공통 실행 안내](../../20261102/COMMON_RUNTIME.md)

```powershell
node --test test_hospital_workflow.mjs test_hospital_operations.mjs
```

수정 대상은 `hospital-ui/`의 문의 입력과 필요한 병원 테스트다. H01 채팅 복구, H02 임상변경 검토 발견, H03 정산·감사는 이 PR을 검증한 뒤 별도 작업으로 이어간다. 구급대 음성·검토는 별도 담당 범위다. 서버 API, DB 스키마, 배정 정책과 공유 시연 서버는 공통 담당자가 관리한다. [이전 H01~H03 인계](../../20261102/dev2/README.md)

## 반납 형식

```text
기준 main / 브랜치 / PR
수정 전 재현과 수정 후 화면 동작
변경 파일과 문의·병원·서버 범위의 상태 관리 방식
실행한 자동 검사·브라우저 확인 결과·가상 데이터 증거 위치
남은 문제 / 확인하지 못한 항목
```
