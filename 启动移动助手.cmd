@echo off
setlocal
pushd "%~dp0" || exit /b 1
chcp 65001 >nul
set "PYTHONUTF8=1"

echo [盘古·天机] 正在启动移动投资助手...
echo [安全提示] 仅在你信任的家庭或办公局域网使用，禁止端口映射或直接暴露公网。
echo [配对入口] 启动后请在本机浏览器打开 http://127.0.0.1:8765/mobile/

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" server.py --mobile --port 8765
) else (
  py -3 server.py --mobile --port 8765
)

if errorlevel 1 (
  echo.
  echo [启动失败] 请先按运行说明安装依赖，并确认8765端口未被占用。
  pause
)

popd
endlocal
