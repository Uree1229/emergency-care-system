@echo off
cd /d "%~dp0"
"_runtime\python\python.exe" solver_improved.py
if errorlevel 1 goto fail
"_runtime\python\python.exe" experiment_improved.py
if errorlevel 1 goto fail
echo Results saved in optimization_results_v2.
pause
exit /b 0
:fail
echo Run failed. Run SETUP_WINDOWS.cmd first.
pause
exit /b 1
