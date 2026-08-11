@echo off
chcp 65001 >nul
setlocal

schtasks /Delete /F /TN "盘古天机-V2-AI量化研究晨报" >nul 2>nul
echo AI量化研究晨报任务已移除；研究证据、报告、记忆和问题记录均保留。
exit /b 0

