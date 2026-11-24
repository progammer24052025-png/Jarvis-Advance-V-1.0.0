@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Jarvis - Premium Launcher
cd /d "%~dp0"

color 0E

echo ==========================================================================
echo    ___    _    ____  __   __ ___ ____
echo   ^|_ _^|  / \  ^|  _ \ \ \ / /^|_ _/ ___^|
echo    ^| ^|  / _ \ ^| ^|_) ^| \ V /  ^| ^|\___ \
echo    ^| ^| / ___ \^|  _ ^<   ^| ^|   ^| ^| ___) ^|
echo   ^|___^|/_/   \_\_^| \_\  ^|_^|  ^|___^|____/
echo.
echo                  by Rudraksh Patel
echo ==========================================================================
echo.
echo Launching automated bootstrap sequence...

powershell.exe -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"

if %errorlevel% neq 0 (
  echo ERROR: Bootstrap failed.
  pause
  exit /b %errorlevel%
)
exit /b 0
