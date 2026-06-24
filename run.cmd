@echo off
REM run.cmd — 開發用：不必 build 就開啟 CodexAutoAI 啟動器 GUI。
setlocal
set "DIR=%~dp0"
where pythonw >nul 2>&1 && (
  start "" pythonw "%DIR%desktop\launcher.py"
) || (
  start "" python "%DIR%desktop\launcher.py"
)
