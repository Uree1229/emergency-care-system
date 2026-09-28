# 로컬 작업환경

원본 `D:\Capstone-20260923`은 이동식 드라이브의 백업입니다. 이 폴더가 작업 복사본입니다.
`portable-runtime.zip`을 `_runtime`과 `.solver_deps`로 풀어 두었습니다.

PowerShell에서 이 폴더로 이동한 뒤 다음 명령으로 검증할 수 있습니다.

```powershell
& .\_runtime\python\python.exe -m unittest test_solver_improved -v
& .\_runtime\node.exe prototype\test_workflow.cjs
& .\_runtime\python\python.exe solver_improved.py
& .\_runtime\python\python.exe experiment_improved.py
```

사용성 화면은 `prototype\index.html`을 브라우저에서 열면 됩니다. 임상 현장에 연결된 시스템은 아닙니다.
