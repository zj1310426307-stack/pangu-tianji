@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"

set "RUNNER=%CD%\运行投资运营任务.cmd"
if not exist "%CD%\.venv\Scripts\python.exe" goto missing
if not exist "%CD%\daily_os.py" goto missing

rem Windows只负责唤醒；交易日、证据时效、幂等和失败关闭由Daily Investment OS校验。
rem 每30分钟唤醒两段交易时段，应用配置可选择30或60分钟实际执行频率。
schtasks /Create /F /RL LIMITED /TN "盘古天机-V2-晨报" /SC DAILY /ST 08:45 /TR "\"%RUNNER%\" run morning_report"
if errorlevel 1 goto failed
schtasks /Create /F /RL LIMITED /TN "盘古天机-V2-盘中监控-上午" /SC MINUTE /MO 30 /ST 09:45 /ET 11:15 /TR "\"%RUNNER%\" run intraday_monitor"
if errorlevel 1 goto failed
schtasks /Create /F /RL LIMITED /TN "盘古天机-V2-盘中监控-下午" /SC MINUTE /MO 30 /ST 13:15 /ET 14:45 /TR "\"%RUNNER%\" run intraday_monitor"
if errorlevel 1 goto failed
schtasks /Create /F /RL LIMITED /TN "盘古天机-V2-收盘复盘" /SC DAILY /ST 15:30 /TR "\"%RUNNER%\" run closing_review"
if errorlevel 1 goto failed
schtasks /Create /F /RL LIMITED /TN "盘古天机-V2-周报" /SC WEEKLY /D FRI /ST 16:00 /TR "\"%RUNNER%\" run weekly_report"
if errorlevel 1 goto failed

echo 投资运营任务安装完成。所有任务只生成报告和通知，不修改策略、仓位、风险或订单。
popd
exit /b 0

:missing
echo 未找到项目Python环境或daily_os.py，请先启动一次盘古天机完成环境准备。
popd
exit /b 1

:failed
call :cleanup
echo Windows投资运营任务创建失败；本次已回滚已创建的同组任务。
popd
exit /b 1

:cleanup
schtasks /Delete /F /TN "盘古天机-V2-晨报" >nul 2>nul
schtasks /Delete /F /TN "盘古天机-V2-盘中监控-上午" >nul 2>nul
schtasks /Delete /F /TN "盘古天机-V2-盘中监控-下午" >nul 2>nul
schtasks /Delete /F /TN "盘古天机-V2-收盘复盘" >nul 2>nul
schtasks /Delete /F /TN "盘古天机-V2-周报" >nul 2>nul
exit /b 0
