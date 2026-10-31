# 보관 자료

2026-10-31에 저장소 루트에서 옮긴 지난 자료다. 현재 서버·화면·시뮬레이션 실행에는 쓰지 않는다. 삭제하지 않고 이력(`git log --follow`)과 함께 보관한다.

| 폴더 | 내용 | 대체한 것 |
|---|---|---|
| `early_documents/` | 제안서·멘토링·최종보고서 등을 다시 만들던 `build_*.py` 11개와 보조 스크립트 `implementation_sections.py` | 완성된 문서는 `docs/proposals/`, `docs/reports/` |
| `usb_transfer/` | USB 드라이브로 프로젝트를 옮기던 안내·스크립트, `portable-runtime.zip` 기반 `SETUP_WINDOWS.cmd`·`TEST_WINDOWS.cmd`·`RUN_EXPERIMENTS_WINDOWS.cmd`, 당시 `LOCAL_SETUP.md` | GitHub 클론과 루트 `README.md`의 설치 절차 |
| `early_optimization/` | 초기 CP-SAT 최적화 실험 `solver_experiment.py`·`experiment_improved.py`·`solver_sensitivity.py`, 결과 `optimization_results/`·`optimization_results_v2/`, 문제 정의 검증 `problem_definition_example.py` | `simulation/`, `inquiry_lab/`의 실험과 루트의 `solver_improved.py`(현재도 서버가 사용) |

## 다시 실행할 때

보관용이라 경로를 고치지 않았다. 초기 실험은 저장소 루트의 `solver_improved.py`와 OR-Tools가 필요하다. 저장소 루트에서 다음처럼 실행한다.

```powershell
$env:PYTHONPATH = '.'
python archive/early_optimization/experiment_improved.py
```

`solver_experiment.py`는 자기 폴더 아래 `.solver_deps`를 찾으므로, OR-Tools가 설치된 Python 환경에서 실행한다. `solver_experiment.py`는 자기 폴더 옆 `optimization_results/`에, `experiment_improved.py`는 실행한 위치의 `optimization_results_v2/`에 결과를 쓴다. 기존 결과를 덮어쓰지 않으려면 복사본에서 실행한다.
