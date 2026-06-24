@echo off
REM setup.cmd — 在 Windows 上雙擊即可執行 setup.ps1 的一鍵設定。
REM 會優先用 PowerShell 7（pwsh），找不到再退回內建 Windows PowerShell。
setlocal
where pwsh >nul 2>&1 && (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
) || (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
)
echo.
pause
