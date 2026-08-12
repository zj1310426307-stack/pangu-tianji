@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python virtual environment not found.
  exit /b 1
)
".venv\Scripts\python.exe" engineering_ops.py health
endlocal
