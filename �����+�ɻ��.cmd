@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\pythonw.exe" (
  echo 请先完整解压压缩包，然后再双击启动。
  pause
  exit /b 1
)
start "" "%~dp0runtime\pythonw.exe" -E -s -X utf8 "%~dp0run.py"
