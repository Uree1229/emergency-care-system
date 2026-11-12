# 테스트 폴더

| 경로 | 대상 |
|---|---|
| `core/` | 공통 서버, 화면과 API 연결, 음성·이송, 팀 배포, 로컬 자료 검증 |
| `browser/` | Node로 실행하는 구급대·환자·병원·지도 화면 테스트 |
| `research/` | 문의 정책과 배정 알고리즘, 재생 및 실험 검증 |
| `research/inquiry_lab/` | 병원 문의 정책의 독립 실험 테스트 |

저장소 루트에서 아래 명령을 실행합니다.

```powershell
python scripts/test_suites.py list
python scripts/test_suites.py core
python scripts/test_suites.py python test_inquiry_lab_batch_policy -v
node --test tests/browser/test_hospital_operations.mjs
```

로컬 자료나 당시 소스가 필요한 연구 검사의 실행 조건은 [테스트 안내](../docs/TESTING.md)에 있습니다.
