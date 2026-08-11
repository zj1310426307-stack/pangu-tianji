@echo off
chcp 65001 >nul
setlocal
schtasks /Delete /F /TN "盘古天机-v09-收盘研究"
schtasks /Delete /F /TN "盘古天机-v09-模拟调仓"
schtasks /Delete /F /TN "盘古天机-v09-五分钟监控"
echo 盘古天机v0.9自动任务已移除。
exit /b 0
