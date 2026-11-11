# 전체 시연

2026-11-05 기준. 같은 환자의 ID로 구급대 기록부터 병원 도착과 연동 상태를 이어 확인한다.

## 저장소 기본 시연

[실행 안내](RUNNING.md)에 따라 새 가상 DB로 `server.chat_demo`를 실행한다. 구급대 입력은 `/paramedic/`, 이송 진행은 등록 결과의 `/patient/?pid=...`, 병원은 `/hospital/?hid=H01`이다. 세 화면을 같은 서버에 연결한다.

1. 가상 환자 상태를 입력하고 원문·중요 항목·미확인 값을 검토한다.
2. 위치와 동의를 확인해 환자를 등록한다. 결과에 표시된 같은 환자의 진행 링크로 이동한다.
3. 후보 병원에 문의를 시작한다. 병원 화면에서 해당 문의에 수용 가능으로 응답한다.
4. 이송 화면에서 목적지를 확정하고 출발한다.
5. 병원 화면의 도착 예정 목록에서 같은 환자의 도착을 확인한다.

조건 변경 시연에서는 이송 중 목적지 병원 화면의 **병원 자원 → 시연: 시스템 밖 환자 유입**을 연다. 해당 환자가 필요로 하는 자원을 선택하고 **신규 여유**보다 큰 수량으로 가상 유입을 등록한다. 서버에 실제 점유와 재확인 사유가 반영되면 현재 병원이 유지·조정을 판단한다. 조정이 필요하면 대체 병원의 새 수용 응답을 확인한 뒤 이송자가 목적지를 변경한다. [가상 유입과 퇴실](HOSPITAL_OPERATIONS_20261030.md)

관제 화면을 사용할 때는 [전체 흐름 콘솔](FLOW_CONSOLE.md)의 실행기를 사용한다. 같은 소스의 구급대·이송·병원 화면을 묶고 선택한 환자의 기록을 읽기 전용으로 조회한다.

## 로컬 병원 연동 통합본

로컬 `.codex-build/hospital-full-demo-20261105/`가 있으면 해당 폴더의 `run_demo.py`를 사용한다. 이 폴더는 단계형 구급대 통합 화면과 연동 대조를 포함하는 별도 소스 스냅샷이다. Git 클론의 기본 실행과 구분한다. 기본 포트는 실행 시 새로 선택되며 실행 폴더의 `ready.json`에 세 화면 주소가 기록된다.

```powershell
python .codex-build/hospital-full-demo-20261105/run_demo.py --run .codex-build/hospital-full-demo-20261105/presentation-new
```

구급대 `/paramedic/`에서 환자를 등록하고 정상 시연의 3~4단계를 수행한다. 연동 오류는 출발 후 가상 수신기의 출발 기록만 누락시키는 시험으로 만든다. 명령의 실행 폴더와 환자 ID는 현재 시연 기록을 사용한다.

```powershell
$demoRun = '.codex-build/hospital-full-demo-20261105/presentation-new'
$demoPatient = '현재 이송 화면의 환자 ID'
python .codex-build/hospital-full-demo-20261105/verify_full_demo.py --run $demoRun --mode inject --patient $demoPatient --hospital H01
```

병원에서 도착을 확인하면 도착 이벤트가 순서 오류로 보관된다. **연동 확인 필요 → 오류 원인 대조**에서 수신기 순서 1, 도착 순서 3, 확인할 출발 순서 2를 확인한다. 아래 명령으로 원래 출발 이벤트를 가상 수신기에 복구한다.

```powershell
python .codex-build/hospital-full-demo-20261105/verify_full_demo.py --run $demoRun --mode restore --patient $demoPatient --hospital H01
```

병원 화면에서 다시 대조하고 검토 체크 후 재전송한다. 전송 상태 조회에서 전달 완료를 확인한다. 검증 기록은 로컬 `browser-qa-001/browser-timeline.json`, `browser-verification.json`, 최종 실행기 `http-qa-002/verification.json`이다. 원본 이벤트 해시·담당자·요청 ID와 가상 병상 1개 반영을 확인했다.

## 발표 리허설

실제 발표에 쓸 실행본·기기·브라우저를 고정하고 전체 시간을 잰다. 정상 경로와 오류 복구 경로의 소요시간을 각각 기록하고 예비 영상을 준비한다. 현재 브라우저 검증에는 직접 입력을 사용했다. 마이크·소음·실기기 측정은 [검증 안내](TESTING.md)를 따른다.
