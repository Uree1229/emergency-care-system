@echo off
cd /d "%~dp0"
"_runtime\python\python.exe" -m unittest test_solver_improved -v
if errorlevel 1 goto fail
"_runtime\node.exe" prototype\test_workflow.cjs
if errorlevel 1 goto fail
echo All tests passed.
pause
exit /b 0
:fail
echo Tests failed or runtime missing. Run SETUP_WINDOWS.cmd first.
pause
exit /b 1
