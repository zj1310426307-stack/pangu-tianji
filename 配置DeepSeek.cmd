@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\configure_deepseek_gui.ps1"
exit /b %errorlevel%

