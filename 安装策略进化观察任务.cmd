@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"

set "RUNNER=%CD%\运行策略进化观察.cmd"
if not exist "%CD%\.venv\Scripts\python.exe" goto missing
if not exist "%CD%\strategy_evolution_daily.py" goto missing

rem Windows只负责每日唤醒；相同审查证据由Strategy Evolution Engine幂等处理。
rem 任务只生成策略健康和研究问题，不运行实验、不改策略、不创建订单。
schtasks /Create /F /RL LIMITED /TN "盘古天机-V3-策略健康观察" /SC DAILY /ST 16:10 /TR "\"%RUNNER%\""
if errorlevel 1 goto failed

echo 策略健康观察任务安装完成。每日16:10读取最新封存审查证据。
popd
exit /b 0

:missing
echo 未找到项目Python环境或strategy_evolution_daily.py。
popd
exit /b 1

:failed
echo Windows策略健康观察任务创建失败。
popd
exit /b 1
