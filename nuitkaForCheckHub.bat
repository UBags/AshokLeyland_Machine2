@echo off
echo ============================================
echo Compiling TestCheckHub.py with Nuitka
echo ============================================
echo Started: %date% %time%

REM Clean previous build
if exist TestCheckHub.dist rmdir /s /q TestCheckHub.dist
if exist TestCheckHub.build rmdir /s /q TestCheckHub.build

REM Compile with Nuitka
nuitka --standalone ^
    --follow-imports ^
    --assume-yes-for-downloads ^
    --remove-output ^
    --force-dll-dependency-cache-update ^
    --enable-plugin=numpy ^
    --enable-plugin=torch ^
    --windows-console-mode=attach ^
    --show-progress ^
    --include-package=cv2 ^
    --include-package=torch ^
    --include-package=torchvision ^
    --include-package=ultralytics ^
    --include-package-data=ultralytics ^
    --include-package-data=mobile_sam ^
    TestCheckHub.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================
    echo SUCCESS! Executable created at:
    echo TestCheckHub.dist\TestCheckHub.exe
    echo ============================================
) else (
    echo.
    echo ============================================
    echo ERROR! Compilation failed.
    echo ============================================
)

echo Ended: %date% %time%
pause