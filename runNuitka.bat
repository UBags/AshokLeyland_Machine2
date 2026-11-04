@echo off
setlocal EnableExtensions
REM ==========================================================================================================
REM  Build MainProgram.exe with Nuitka for Machine 1 (Allen-Bradley) or Machine 2 (Siemens S7-1515).
REM
REM    runNuitka.bat allenbradley   Machine 1: ApplicationConfiguration.Machine1.properties (plc.vendor=allenbradley)
REM    runNuitka.bat siemens        Machine 2: ApplicationConfiguration.Machine2.properties (plc.vendor=siemens)
REM  No default: without (or with any other) argument it shows these two commands and exits.
REM
REM  Run from the project root with the project's .venv activated (torch cu128 for the target machine).
REM  The exe reads everything relative to its own folder (MainProgram.dist), so after compiling this copies in:
REM    ApplicationConfiguration.properties   <- the chosen machine's properties file (decides IOServer + vision settings)
REM    costhetaio\siemens\IOServerProperties.yaml  (Siemens tag map; included by Nuitka below)
REM  NOT copied: models\ (MobileSAM / YOLO weights, very large, already on the target machine). The exe looks for them in
REM  <folder of MainProgram.exe>\models\ - on the target, MainProgram.exe must sit next to that models folder.
REM ==========================================================================================================

set "VENDOR=%~1"
if /I "%VENDOR%"=="allenbradley" (
    set "VENDOR=allenbradley"
    set "MACHINE=1"
    set "PROPS=ApplicationConfiguration.Machine1.properties"
) else if /I "%VENDOR%"=="siemens" (
    set "VENDOR=siemens"
    set "MACHINE=2"
    set "PROPS=ApplicationConfiguration.Machine2.properties"
) else (
    goto :usage
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
python -c "import torch; print('torch', torch.__version__, '| CUDA', torch.version.cuda, '| GPU available', torch.cuda.is_available())"

REM ---- clean previous build
if exist MainProgram.dist rmdir /s /q MainProgram.dist
if exist MainProgram.build rmdir /s /q MainProgram.build

REM ---- compile
REM torch / torchvision are NOT given as --include-package: that compiles every torch submodule, including torch._dynamo
REM (torch.compile), which this program never imports and which crashed Nuitka 2.7.10 (torch\_dynamo\pgo.py). The torch
REM modules the program uses are followed from its imports. The numpy / torch plugins are deprecated (built in now);
REM --follow-imports is the default in standalone mode.
REM networkx / pandas / seaborn are left out: no MainProgram code imports them, and importing torch, ultralytics and the
REM vision checks does not load them (only optional paths of torch.compile / ultralytics training plots would; pandas is
REM used by the MI dashboard, which is OtherServices.py, not MainProgram). scipy (hub / top bearing / nut checks), sympy
REM (loaded by torch) and matplotlib (loaded by ultralytics) stay.
REM Also left out (nothing MainProgram runs imports them; from the 4.3.4 build report):
REM   sklearn            - only there because of --include-package=sklearn.externals; no code imports sklearn
REM   torchaudio         - optional import inside torch; never used
REM   boto3 / botocore / s3transfer - S3 backup only (PostgresBackupUtility imports boto3 in try/except; local backups)
REM   pymodbus + costhetaio.allenbradley.IOServer_ModbusTCP - an unused Modbus IOServer variant
REM   costhetaio.siemens.s7_* - command-line test tools for the Siemens PLC
REM --jobs=4: the C compiler ran out of heap space (C1060 / C1002) on the huge sympy modules with one job per core.
python -m nuitka --version
nuitka --standalone ^
    --jobs=4 ^
    --module-parameter=torch-disable-jit=yes ^
    --assume-yes-for-downloads ^
    --remove-output ^
    --force-dll-dependency-cache-update ^
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
    --include-package=ultralytics ^
    --nofollow-import-to=networkx ^
    --nofollow-import-to=pandas ^
    --nofollow-import-to=seaborn ^
    --nofollow-import-to=sklearn ^
    --nofollow-import-to=torchaudio ^
    --nofollow-import-to=boto3 ^
    --nofollow-import-to=botocore ^
    --nofollow-import-to=s3transfer ^
    --nofollow-import-to=pymodbus ^
    --nofollow-import-to=costhetaio.allenbradley.IOServer_ModbusTCP ^
    --nofollow-import-to=costhetaio.siemens.s7_read_all_tags ^
    --nofollow-import-to=costhetaio.siemens.s7_smoke_test ^
    --nofollow-import-to=costhetaio.siemens.s7_tag_check ^
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

echo.
echo ============================================
echo Checking MainProgram.dist
echo ============================================
set "PROBLEMS=0"
findstr /B /C:"plc.vendor=%VENDOR%" "MainProgram.dist\ApplicationConfiguration.properties"
if errorlevel 1 (echo   MISSING: plc.vendor=%VENDOR% in MainProgram.dist\ApplicationConfiguration.properties & set "PROBLEMS=1")
if exist "MainProgram.dist\costhetaio\siemens\IOServerProperties.yaml" (echo   OK: costhetaio\siemens\IOServerProperties.yaml) else (echo   MISSING: costhetaio\siemens\IOServerProperties.yaml & set "PROBLEMS=1")
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
echo models\ is NOT in MainProgram.dist. On the target machine the exe needs, next to MainProgram.exe:
echo     models\MobileSAM\weights\mobile_sam.pt
echo     models\MobileSAM\MobileSAMv2\weight\ObjectAwareModel.pt
echo ============================================

:end
echo Ended: %date% %time%
endlocal
pause
exit /b

:usage
echo.
echo runNuitka.bat - builds MainProgram.exe with Nuitka for one machine. Give the PLC vendor:
echo.
echo     runNuitka.bat allenbradley
echo         Machine 1 ^(Allen-Bradley PLC^): uses ApplicationConfiguration.Machine1.properties
echo.
echo     runNuitka.bat siemens
echo         Machine 2 ^(Siemens S7-1515 PLC^): uses ApplicationConfiguration.Machine2.properties
echo.
echo From PowerShell:  .\runNuitka.bat allenbradley    or    .\runNuitka.bat siemens
if not "%~1"=="" echo.& echo Not a valid option: "%~1"
endlocal
exit /b 1
