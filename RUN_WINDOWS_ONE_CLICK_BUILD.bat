@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\build_windows_distribution.ps1"

if errorlevel 1 (
  echo.
  echo INAES Windows build failed.
  pause
  exit /b 1
)

echo.
echo INAES Windows build completed.
echo Outputs are in ..\release_delivery\windows_desktop_installer
pause
