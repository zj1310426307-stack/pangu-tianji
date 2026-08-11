@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 goto path_error

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

if exist ".venv\Scripts\python.exe" goto ensure_deps
where python >nul 2>nul
if errorlevel 1 goto python_missing

echo Creating the project Python environment...
python -m venv .venv
if errorlevel 1 goto setup_error

:ensure_deps
".venv\Scripts\python.exe" -c "import numpy,pandas,yaml" >nul 2>nul
if not errorlevel 1 goto run_agent
echo Installing the project dependencies. This is needed only once...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto setup_error

:run_agent
echo Running Pangu-Tianji in paper mode...
".venv\Scripts\python.exe" -c "from pathlib import Path; p=Path('run.py'); g={'__name__':'__main__','__file__':str(p.resolve())}; exec(compile(p.read_text(encoding='utf-8'),str(p),'exec'),g)"
if errorlevel 1 goto run_error
echo.
echo Run completed. Report: output\backtest_report.md
popd
pause
exit /b 0

:path_error
echo Unable to open the agent folder.
pause
exit /b 1

:python_missing
echo Python was not found. Install Python 3.11 or newer, then try again.
popd
pause
exit /b 1

:setup_error
echo.
echo The local environment could not be prepared. Review the message above.
popd
pause
exit /b 1

:run_error
echo.
echo The run failed. Another local process may already be using this project.
popd
pause
exit /b 1
