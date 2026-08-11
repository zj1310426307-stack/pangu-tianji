@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
set "PYTHONUTF8=1"

if not exist ".venv\Scripts\python.exe" exit /b 2
if not exist "strategy_evolution_daily.py" exit /b 3

".venv\Scripts\python.exe" "strategy_evolution_daily.py"
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
