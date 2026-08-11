@echo off
chcp 65001 >nul
setlocal

schtasks /Delete /F /TN "盘古天机-V2-晨报" >nul 2>nul
schtasks /Delete /F /TN "盘古天机-V2-盘中监控-上午" >nul 2>nul
schtasks /Delete /F /TN "盘古天机-V2-盘中监控-下午" >nul 2>nul
schtasks /Delete /F /TN "盘古天机-V2-收盘复盘" >nul 2>nul
schtasks /Delete /F /TN "盘古天机-V2-周报" >nul 2>nul

echo 盘古天机V2投资运营任务已移除；原有研究与模拟交易任务未改动。
exit /b 0
