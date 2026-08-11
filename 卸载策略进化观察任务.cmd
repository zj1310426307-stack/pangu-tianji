@echo off
chcp 65001 >nul
setlocal
schtasks /Delete /F /TN "盘古天机-V3-策略健康观察"
exit /b %ERRORLEVEL%
