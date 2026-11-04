@echo off
setlocal EnableExtensions
REM ==========================================================================================================
REM  Build MainProgram.exe with Nuitka for Machine 1 (Allen-Bradley) or Machine 2 (Siemens S7-1515).
REM
REM    runNuitka.bat 1     Machine 1: ApplicationConfiguration.Machine1.properties (plc.vendor=allenbradley)
REM    runNuitka.bat 2     Machine 2: ApplicationConfiguration.Machine2.properties (plc.vendor=siemens)
REM    runNuitka.bat       asks which machine
REM
REM  Run from the project root with the project's .venv activated (torch cu128 for the target machine).
REM  The exe reads everything relative to its own folder (MainProgram.dist), so after compiling this copies in:
REM    ApplicationConfiguration.properties   <- the chosen machine's properties file (decides IOServer + vision settings)
REM    models\                               <- MobileSAM / YOLO weights (not in git)
REM    costhetaio\siemens\IOServerProperties.yaml  (Siemens tag map; included by Nuitka below)
REM    sklearn data files
REM ==========================================================================================================

set "MACHINE=%~1"
if "%MACHINE%"=="" set /p "MACHINE=Build for which machine (1 = Allen-Bradley, 2 = Siemens)? "
if "%MACHINE%"=="1" (
    set "PROPS=ApplicationConfiguration.Machine1.properties"
    set "VENDOR=allenbradley"
) else if "%MACHINE%"=="2" (
    set "PROPS=ApplicationConfiguration.Machine2.properties"
    set "VENDOR=siemens"
) else (
    echo ERROR: machine must be 1 or 2, got "%MACHINE%"
    goto :end
)

echo ============================================
echo Compiling MainProgram.py with Nuitka for Machine %MACHINE% (%VENDOR%)
echo ============================================
echo Started: %date% %time%

REM ---- checks before a long build
if not exist "%PROPS%" (
    echo ERROR: %PROPS% not found in %CD%
    goto :end
)
findstr /B /C:"plc.vendor=%VENDOR%" "%PROPS%" >nul
if errorlevel 1 (
    echo ERROR: %PROPS% does not contain plc.vendor=%VENDOR%
    goto :end
)
if not exist "costhetaio\siemens\IOServerProperties.yaml" (
    echo ERROR: costhetaio\siemens\IOServerProperties.yaml not found
    goto :end
)
if not exist "models" (
    echo WARNING: models\ not found here - the vision checks need it; copy it into MainProgram.dist\models yourself.
)
python -c "import torch; print('torch', torch.__version__, '| CUDA', torch.version.cuda, '| GPU available', torch.cuda.is_available())"

REM ---- clean previous build
if exist MainProgram.dist rmdir /s /q MainProgram.dist
if exist MainProgram.build rmdir /s /q MainProgram.build

REM ---- compile
nuitka --standalone ^
    --follow-imports ^
    --assume-yes-for-downloads ^
    --remove-output ^
    --force-dll-dependency-cache-update ^
    --enable-plugin=numpy ^
    --enable-plugin=torch ^
    --include-module=psycopg2._psycopg ^
    --include-package=psycopg2 ^
    --enable-plugin=tk-inter ^
    --enable-plugin=pyside6 ^
    --include-data-dir=wavs=wavs ^
    --include-data-dir=internalimages=internalimages ^
    --include-data-files=costhetaio/siemens/IOServerProperties.yaml=costhetaio/siemens/IOServerProperties.yaml ^
    --include-package=costhetaio ^
    --include-package=pyS7 ^
    --include-package=pycomm3 ^
    --include-package=yaml ^
    --windows-console-mode=attach ^
    --include-package=cv2 ^
    --include-package=torch ^
    --include-package=torchvision ^
    --include-package=ultralytics ^
    --include-package=sklearn.externals ^
    MainProgram.py

if errorlevel 1 (
    echo.
    echo ============================================
    echo ERROR! Compilation failed.
    echo ============================================
    goto :end
)

echo.
echo ============================================
echo Copying run-time files into MainProgram.dist
echo ============================================
copy /Y "%PROPS%" "MainProgram.dist\ApplicationConfiguration.properties" >nul
echo   ApplicationConfiguration.properties  ^<-  %PROPS%
if exist "models" (
    robocopy "models" "MainProgram.dist\models" /E /NFL /NDL /NJH /NJS /NP >nul
    echo   models\
)
robocopy ".venv\lib\site-packages\sklearn" "MainProgram.dist\sklearn" *.css *.json *.txt *.rst /S /NFL /NDL /NJH /NJS /NP >nul
echo   sklearn data files

echo.
echo ============================================
echo Checking MainProgram.dist
echo ============================================
set "PROBLEMS=0"
findstr /B /C:"plc.vendor=%VENDOR%" "MainProgram.dist\ApplicationConfiguration.properties"
if errorlevel 1 (echo   MISSING: plc.vendor=%VENDOR% in MainProgram.dist\ApplicationConfiguration.properties & set "PROBLEMS=1")
if exist "MainProgram.dist\costhetaio\siemens\IOServerProperties.yaml" (echo   OK: costhetaio\siemens\IOServerProperties.yaml) else (echo   MISSING: costhetaio\siemens\IOServerProperties.yaml & set "PROBLEMS=1")
if exist "MainProgram.dist\models\MobileSAM\weights\mobile_sam.pt" (echo   OK: models\MobileSAM\weights\mobile_sam.pt) else (echo   MISSING: models\MobileSAM\weights\mobile_sam.pt & set "PROBLEMS=1")
if exist "MainProgram.dist\models\MobileSAM\MobileSAMv2\weight\ObjectAwareModel.pt" (echo   OK: models\MobileSAM\MobileSAMv2\weight\ObjectAwareModel.pt) else (echo   MISSING: models\MobileSAM\MobileSAMv2\weight\ObjectAwareModel.pt & set "PROBLEMS=1")
if exist "MainProgram.dist\wavs" (echo   OK: wavs\) else (echo   MISSING: wavs\ & set "PROBLEMS=1")
if exist "MainProgram.dist\internalimages" (echo   OK: internalimages\) else (echo   MISSING: internalimages\ & set "PROBLEMS=1")

echo.
echo ============================================
if "%PROBLEMS%"=="0" (
    echo SUCCESS! Machine %MACHINE% ^(%VENDOR%^) executable:
) else (
    echo BUILT, BUT SOMETHING ABOVE IS MISSING. Executable:
)
echo MainProgram.dist\MainProgram.exe
echo ============================================

:end
echo Ended: %date% %time%
endlocal
pause
