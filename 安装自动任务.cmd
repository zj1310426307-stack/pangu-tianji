@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
set "RUNNER=%CD%\运行自动任务.cmd"
if not exist "%CD%\.venv\Scripts\python.exe" goto missing

schtasks /Create /F /TN "盘古天机-v09-收盘研究" /SC DAILY /ST 15:10 /TR "\"%RUNNER%\" collect"
if errorlevel 1 goto failed
schtasks /Create /F /TN "盘古天机-v09-模拟调仓" /SC DAILY /ST 09:35 /TR "\"%RUNNER%\" execute"
if errorlevel 1 goto failed
schtasks /Create /F /TN "盘古天机-v09-五分钟监控" /SC MINUTE /MO 5 /ST 09:35 /ET 14:55 /TR "\"%RUNNER%\" monitor"
if errorlevel 1 goto failed
echo 自动任务安装完成。非交易日和过期行情会由程序安全拒绝。
popd
exit /b 0

:missing
echo 未找到Python环境，请先运行一次启动脚本完成安装。
popd
exit /b 1

:failed
echo Windows定时任务创建失败，请检查当前用户的任务计划权限。
popd
exit /b 1
