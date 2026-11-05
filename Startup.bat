@echo off
rem Startup.bat - starts the Hub and Disc application by hand (keep it on the Desktop).
rem At logon, the .vbs in the Startup folder starts it automatically.
rem Only one copy may run: a second MainProgram would flush Redis and fight the first over COM3.
setlocal
set "APP_DIR=D:\AshokLeyland"
set "MAIN_EXE=%APP_DIR%\MainProgram.dist\MainProgram.exe"

tasklist /FI "IMAGENAME eq MainProgram.exe" /NH | find /I "MainProgram.exe" >nul
if not errorlevel 1 (
    echo MainProgram.exe is already running - not starting a second copy.
    timeout /t 10
    exit /b 0
)

if not exist "%MAIN_EXE%" (
    echo Not found: %MAIN_EXE%
    pause
    exit /b 1
)

cd /d "%APP_DIR%"
title Hub and Disc application - MainProgram
"%MAIN_EXE%"
echo.
echo MainProgram.exe exited with code %ERRORLEVEL%.
pause
