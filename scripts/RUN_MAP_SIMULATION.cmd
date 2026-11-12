@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m simulation.viewer_server --reuse-data
) else if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m simulation.viewer_server --reuse-data
) else (
  py -3 -m simulation.viewer_server --reuse-data
)
if errorlevel 1 (
  echo.
  echo 실행에 실패했습니다. Python 설치 또는 위 오류를 확인하세요.
  pause
)
