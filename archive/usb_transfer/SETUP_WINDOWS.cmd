@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "Expand-Archive -LiteralPath 'portable-runtime.zip' -DestinationPath '.' -Force"
if errorlevel 1 goto fail
"_runtime\python\python.exe" -c "import sys; sys.path.insert(0,'.solver_deps'); from ortools.sat.python import cp_model; import docx; print('Runtime ready')"
if errorlevel 1 goto fail
echo Setup complete. Run TEST_WINDOWS.cmd next.
pause
exit /b 0
:fail
echo Setup failed. Keep this output for troubleshooting.
pause
exit /b 1
