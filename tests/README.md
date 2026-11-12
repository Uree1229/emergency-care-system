# 테스트 폴더

저장소 루트에서 `python scripts/test_suites.py core`를 실행하면 CI와 같은 제품 회귀 묶음을 확인할 수 있습니다. 묶음별 대상과 실행 환경은 [테스트 안내](../docs/TESTING.md)에 있습니다.

`tests/research/inquiry_lab/`에는 병원 문의 정책 실험의 Python 테스트를 모았습니다. 전체 테스트 실행기는 루트와 이 폴더를 모두 탐색합니다. 모듈 이름과 연구 묶음에서의 분류는 위치를 옮기기 전과 같습니다.

문의 정책 모듈 하나만 확인할 때는 저장소 루트에서 실행합니다.

```powershell
python -m unittest discover -s tests/research/inquiry_lab -p test_inquiry_lab_batch_policy.py -v
```

루트의 테스트는 제품·화면 검증, 팀 배포 묶음, 원본 경로가 연구 재현에 필요한 검사에 사용합니다.
