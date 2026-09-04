@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 Go2 UWB伴随实时监测（模拟数据）...
echo 浏览器将自动打开；演示结束后在此窗口按 Ctrl+C 停止。
echo.
"Go2-UWB-Mock.exe"
if errorlevel 1 (
  echo.
  echo 启动失败，请查看上方提示。
  pause
)
