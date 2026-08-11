@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
set "PYTHONUTF8=1"

if not exist ".venv\Scripts\python.exe" exit /b 2
if not exist "quant_ai.py" exit /b 3

".venv\Scripts\python.exe" -B "quant_ai.py" %*
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%

