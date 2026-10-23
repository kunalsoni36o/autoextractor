@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: Could not change to the project directory: "%~dp0"
    pause
    exit /b 1
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
set "MARKER=%CD%\.setup-complete"
set "MARKER_HASH="
set "CURRENT_HASH="

if exist "%MARKER%" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%MARKER%") do if /I "%%A"=="PYPROJECT_SHA256" set "MARKER_HASH=%%B"
)

if exist "pyproject.toml" (
    for /f "usebackq delims=" %%H in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'pyproject.toml').Hash"`) do set "CURRENT_HASH=%%H"
)

if not exist "%VENV_PY%" (
    echo The project environment is missing. Running setup...
    call "%CD%\setup.bat"
    if errorlevel 1 goto :setup_failed
)

if not exist "%MARKER%" goto :run_setup
if not defined CURRENT_HASH goto :run_setup
if /I not "%MARKER_HASH%"=="%CURRENT_HASH%" goto :run_setup

"%VENV_PY%" -c "import scanner_app; import PySide6; import cv2; import numpy; import psutil" >nul 2>&1
if errorlevel 1 goto :run_setup
goto :launch

:run_setup
echo Setup is missing or outdated. Running setup...
call "%CD%\setup.bat"
if errorlevel 1 goto :setup_failed

:launch
echo Launching Scanner Workstation...
"%VENV_PY%" -m scanner_app %*
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
    echo.
    echo ERROR: Scanner Workstation exited with code %APP_EXIT%.
    pause
)
exit /b %APP_EXIT%

:setup_failed
echo.
echo ERROR: Setup failed. Scanner Workstation was not launched.
pause
exit /b 1
