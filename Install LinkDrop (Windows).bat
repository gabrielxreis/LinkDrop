@echo off
setlocal
title LinkDrop installer
rem LinkDrop installer for DaVinci Resolve on Windows - by @gabrielxreis_
rem Runs install-windows.ps1 next to this file, or downloads it from GitHub.

set "PS1=%~dp0install-windows.ps1"
if exist "%PS1%" goto run

set "PS1=%TEMP%\linkdrop-install-windows.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/gabrielxreis/LinkDrop/main/install-windows.ps1' -OutFile '%PS1%'"
if not exist "%PS1%" goto nodownload

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
exit /b 0

:nodownload
echo.
echo   Couldn't download the installer. Check your internet connection and try again.
echo.
pause
exit /b 1
