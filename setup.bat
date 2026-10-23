@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: Could not change to the project directory: "%~dp0"
    exit /b 1
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
set "MARKER=%CD%\.setup-complete"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    echo Install Python 3.11 or newer and ensure it is available as "python".
    exit /b 1
)

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: The Python command was found but could not run.
    exit /b 1
)

if not exist "%VENV_PY%" (
    echo Creating the project virtual environment...
    python -m venv "%CD%\.venv"
    if errorlevel 1 (
        echo ERROR: Could not create .venv.
        exit /b 1
    )
) else (
    echo Reusing existing .venv; it will not be recreated.
)

if not exist "%VENV_PY%" (
    echo ERROR: .venv was created or found, but its Python interpreter is missing.
    exit /b 1
)

echo Upgrading pip in the project environment...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo ERROR: pip upgrade failed. Check network access and the Python installation.
    exit /b 1
)

echo Installing the project and declared dependencies...
"%VENV_PY%" -m pip install -e .
if errorlevel 1 (
    echo ERROR: Project installation failed.
    exit /b 1
)

echo Verifying application imports...
"%VENV_PY%" -c "import scanner_app; import PySide6; import cv2; import numpy; import psutil; print('Import verification passed.')"
if errorlevel 1 (
    echo ERROR: Runtime import verification failed. The environment is not ready.
    if exist "%MARKER%" del /q "%MARKER%"
    exit /b 1
)

set "PYPROJECT_HASH="
for /f "usebackq delims=" %%H in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'pyproject.toml').Hash"`) do set "PYPROJECT_HASH=%%H"
if not defined PYPROJECT_HASH (
    echo ERROR: Could not fingerprint pyproject.toml.
    if exist "%MARKER%" del /q "%MARKER%"
    exit /b 1
)

>"%MARKER%" echo PYPROJECT_SHA256=!PYPROJECT_HASH!
>>"%MARKER%" echo SETUP_COMPLETED_UTC=!DATE! !TIME!
if not exist "%MARKER%" (
    echo ERROR: Could not write the setup-completion marker.
    exit /b 1
)

echo.
echo Setup completed successfully. The environment is ready.
exit /b 0
