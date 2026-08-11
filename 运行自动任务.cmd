@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" exit /b 2
".venv\Scripts\python.exe" -c "from pathlib import Path; p=Path('automation.py'); g={'__name__':'__main__','__file__':str(p.resolve())}; exec(compile(p.read_text(encoding='utf-8'),str(p),'exec'),g)" %1
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
