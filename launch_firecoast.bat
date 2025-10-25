@echo off
setlocal ENABLEDELAYEDEXPANSION

REM Determine repository root relative to this script
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"

set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "ACTIVATE_BAT=%VENV_DIR%\Scripts\activate.bat"

if not exist "%PYTHON_EXE%" (
    echo [FireCoast] Creating Python virtual environment...
    where py >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        py -3 -m venv "%VENV_DIR%"
    ) else (
        python -m venv "%VENV_DIR%"
    )
    if exist "%PYTHON_EXE%" (
        echo [FireCoast] Virtual environment created.
    ) else (
        echo [FireCoast] Failed to create the Python virtual environment.
        echo Ensure that Python 3.9 or newer is installed and available on your PATH.
        pause
        exit /b 1
    )
)

call "%ACTIVATE_BAT%"
if %ERRORLEVEL% NEQ 0 (
    echo [FireCoast] Unable to activate the virtual environment.
    pause
    exit /b 1
)

echo [FireCoast] Installing/updating Python dependencies...
python -m pip install --upgrade pip --disable-pip-version-check
python -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [FireCoast] Failed to install dependencies.
    pause
    exit /b 1
)

echo [FireCoast] Ensuring firewall access for new device registration...
"%PYTHON_EXE%" scripts\ensure_firewall_registration.py
set "FIRECOAST_FIREWALL_EXIT=%ERRORLEVEL%"
if "%FIRECOAST_FIREWALL_EXIT%"=="2" (
    echo [FireCoast] Firewall automation requires Administrator privileges. FireCoast will continue to launch, but automatic device approval may be blocked until access is granted.
) else if "%FIRECOAST_FIREWALL_EXIT%"=="3" (
    echo [FireCoast] Warning: Automatic firewall configuration failed. Review the message above and adjust the firewall manually if needed.
)

echo [FireCoast] Starting the application...
python app.py

REM Keep the window open if the server exits unexpectedly
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [FireCoast] Application exited with an error.
    pause
)

endlocal
