@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"

set "RUNNER=%CD%\运行AI量化研究任务.cmd"
if not exist "%CD%\.venv\Scripts\python.exe" goto missing
if not exist "%CD%\quant_ai.py" goto missing

rem Windows只负责08:00唤醒；证据校验、幂等、失败终态和零交易能力由quant_ai服务负责。
schtasks /Create /F /RL LIMITED /TN "盘古天机-V2-AI量化研究晨报" /SC DAILY /ST 08:00 /TR "\"%RUNNER%\" due"
if errorlevel 1 goto failed

echo AI量化研究晨报任务安装完成。任务只读取封存研究证据，不修改策略、参数、组合、风控或订单。
popd
exit /b 0

:missing
echo 未找到项目Python环境或quant_ai.py，请先启动一次盘古天机完成环境准备。
popd
exit /b 1

:failed
schtasks /Delete /F /TN "盘古天机-V2-AI量化研究晨报" >nul 2>nul
echo AI量化研究任务创建失败；已回滚同名任务。
popd
exit /b 1

