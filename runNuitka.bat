@echo off
setlocal EnableExtensions
REM ==========================================================================================================
REM  Build MainProgram.exe with Nuitka for Machine 1 (Allen-Bradley) or Machine 2 (Siemens S7-1515).
REM
REM    runNuitka.bat allenbradley   Machine 1: ApplicationConfiguration.Machine1.properties (plc.vendor=allenbradley)
REM    runNuitka.bat siemens        Machine 2: ApplicationConfiguration.Machine2.properties (plc.vendor=siemens)
REM    runNuitka.bat siemens copyonly   no build: only copies the run-time files into the existing MainProgram.dist
REM                                     and checks it (also: allenbradley copyonly)
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
set "MODE=%~2"
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
if /I "%MODE%"=="copyonly" goto :copyfiles
if not "%MODE%"=="" goto :usage

echo ============================================
echo Compiling MainProgram.py with Nuitka for Machine %MACHINE% (%VENDOR%)
echo ============================================
echo Started: %date% %time%

REM ---- checks before a long build
if not exist "%PROPS%" (
    echo ERROR: %PROPS% not found in the current folder
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
REM Exclusions (--nofollow-import-to). Checked against the source of every library in the build at the pinned versions
REM (torch 2.11.0, ultralytics 8.0.200, tqdm 4.67.1, pandas 2.2.3, scikit-learn 1.7.0, networkx 3.4.2, scipy, matplotlib,
REM sympy, timm, huggingface_hub, mobile_sam, ... 80 packages) and against Nuitka 4.3.4: in the exe an excluded module
REM raises ImportError not only on import but also on importlib.util.find_spec() (MetaPathBasedLoader.c), where normal
REM Python returns None - so a library that only LOOKS FOR a package by name breaks too.
REM MUST stay IN (do not exclude):
REM   networkx - torch._functorch...graph_info_provider imports it at module level; reached when MobileSAM loads
REM   pandas   - every YOLO predict (hub / bunk / nut checks): ultralytics AutoBackend.__init__ -> _model_type() ->
REM              exporter.export_formats() -> import pandas
REM   sklearn  - torch._dynamo.trace_rules calls find_spec("sklearn") (and "pandas", "networkx") at module level
REM   scipy (hub / top bearing / nut checks), sympy (torch), matplotlib (ultralytics) stay as well.
REM Left OUT - each is only imported inside a function or try/except ImportError on a path MainProgram never takes, and
REM no library looks it up by name:
REM   seaborn    - ultralytics plot_labels / ConfusionMatrix.plot (training, validation; never called)
REM   torchaudio - torch datapipes audio decoder, inside try/except ImportError
REM   boto3, botocore, s3transfer - PostgresBackupUtility (try/except ImportError, local backups);
REM              pandas reads s3:// URLs only (try/except ImportError)
REM   pymodbus + costhetaio.allenbradley.IOServer_ModbusTCP - an unused Modbus IOServer; nothing imports it
REM   costhetaio.siemens.test - command-line test tools for the Siemens PLC (s7_*); nothing imports them
REM --clang: MSVC's cl ran out of heap space (C1060 / C1002) on sympy\polys\polyquinticconst (a huge generated C file;
REM sympy.polys.polyroots imports it, torch imports sympy) with one job per core and again with 4 jobs. clang-cl from
REM Visual Studio ("C++ Clang Compiler for Windows" component) is used instead. The objects cached by cl are not reused,
REM so the first clang build compiles everything; --jobs=4 keeps memory in check. If clang also runs out of memory,
REM replace --jobs=4 with --low-memory (one file at a time).
REM --report: writes compilation-report.xml (every module, why it is included, DLLs, data files).
python -m nuitka --version
if not exist "%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" (
    echo ERROR: vswhere.exe not found - Visual Studio / Build Tools are needed for clang-cl.
    goto :end
)
set "CLANGCL="
for /f "usebackq delims=" %%P in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -find VC\Tools\Llvm\x64\bin\clang-cl.exe`) do set "CLANGCL=%%P"
if not defined CLANGCL (
    echo ERROR: clang-cl.exe not found. In the Visual Studio Installer, Modify the installation and add the individual
    echo        component "C++ Clang Compiler for Windows" ^(and "MSBuild support for LLVM ^(clang-cl^) toolset"^).
    goto :end
)
echo Using %CLANGCL%
REM "python -m nuitka", not "nuitka": in a venv "nuitka" is nuitka.cmd, and a batch file run from a batch file
REM without CALL never returns - everything below (properties copy, checks) was skipped (CHANGES 113).
python -m nuitka --standalone ^
    --clang ^
    --jobs=4 ^
    --report=compilation-report.xml ^
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
    --nofollow-import-to=seaborn ^
    --nofollow-import-to=torchaudio ^
    --nofollow-import-to=boto3 ^
    --nofollow-import-to=botocore ^
    --nofollow-import-to=s3transfer ^
    --nofollow-import-to=pymodbus ^
    --nofollow-import-to=costhetaio.allenbradley.IOServer_ModbusTCP ^
    --nofollow-import-to=costhetaio.siemens.test ^
    MainProgram.py

if errorlevel 1 (
    echo.
    echo ============================================
    echo ERROR! Compilation failed.
    echo ============================================
    goto :end
)

REM ---- after the build (also "runNuitka.bat <vendor> copyonly"): run-time files into MainProgram.dist, then checks.
REM Every command's own error output is suppressed; a failure is reported in plain words and counted (PROBLEMS).
:copyfiles
echo.
echo ============================================
echo Copying run-time files into MainProgram.dist
echo ============================================
set "PROBLEMS=0"
if not exist "MainProgram.dist\" (
    echo   ERROR: MainProgram.dist not found in the current folder - build first: runNuitka.bat %VENDOR%
    set "PROBLEMS=1"
    goto :summary
)
if not exist "%PROPS%" (
    echo   ERROR: %PROPS% not found in the current folder
    set "PROBLEMS=1"
    goto :checks
)
copy /Y "%PROPS%" "MainProgram.dist\ApplicationConfiguration.properties" >nul 2>&1
if errorlevel 1 (
    echo   ERROR: could not copy %PROPS% to MainProgram.dist\ApplicationConfiguration.properties - is it open, or MainProgram.exe running?
    set "PROBLEMS=1"
) else (
    echo   ApplicationConfiguration.properties  ^<-  %PROPS%
)

:checks
echo.
echo ============================================
echo Checking MainProgram.dist
echo ============================================
if exist "MainProgram.dist\MainProgram.exe" (echo   OK: MainProgram.exe) else (echo   MISSING: MainProgram.exe& set "PROBLEMS=1")
if not exist "MainProgram.dist\ApplicationConfiguration.properties" (
    echo   MISSING: ApplicationConfiguration.properties
    set "PROBLEMS=1"
    goto :checkfolders
)
for %%K in ("plc.vendor=%VENDOR%" "picture.retention.days.trial=" "picture.retention.days.main=") do (
    findstr /B /C:%%K "MainProgram.dist\ApplicationConfiguration.properties" >nul 2>&1
    if errorlevel 1 (
        echo   MISSING in ApplicationConfiguration.properties: %%~K
        set "PROBLEMS=1"
    ) else (
        for /f "delims=" %%L in ('findstr /B /C:%%K "MainProgram.dist\ApplicationConfiguration.properties" 2^>nul') do echo   OK: %%L
    )
)
:checkfolders
if exist "MainProgram.dist\costhetaio\siemens\IOServerProperties.yaml" (echo   OK: costhetaio\siemens\IOServerProperties.yaml) else (echo   MISSING: costhetaio\siemens\IOServerProperties.yaml& set "PROBLEMS=1")
if exist "MainProgram.dist\wavs\" (echo   OK: wavs\) else (echo   MISSING: wavs\& set "PROBLEMS=1")
if exist "MainProgram.dist\internalimages\" (echo   OK: internalimages\) else (echo   MISSING: internalimages\& set "PROBLEMS=1")

:summary
echo.
echo ============================================
if "%PROBLEMS%"=="0" (
    echo SUCCESS! Machine %MACHINE% ^(%VENDOR%^) executable:
) else (
    echo SOMETHING ABOVE FAILED OR IS MISSING - fix it before copying to the plant PC. Executable:
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
echo     runNuitka.bat siemens copyonly   ^(or allenbradley copyonly^)
echo         No build: copies the properties file into the existing MainProgram.dist and checks it.
echo.
echo From PowerShell:  .\runNuitka.bat allenbradley    or    .\runNuitka.bat siemens
if not "%~1"=="" echo.& echo Not a valid option: "%~1 %~2"
endlocal
exit /b 1
