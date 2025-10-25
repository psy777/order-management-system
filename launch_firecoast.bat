@echo off
setlocal ENABLEDELAYEDEXPANSION

REM Handle elevation relaunch flag
if /I "%~1"=="__elevated__" (
    shift
    set "FIRECOAST_ELEVATED=1"
) else (
    set "FIRECOAST_ELEVATED=0"
)

REM Ensure we are running with Administrator rights so firewall changes succeed
fltmc >nul 2>&1
if errorlevel 1 (
    if "%FIRECOAST_ELEVATED%"=="0" (
        echo [FireCoast] Requesting Administrator privileges to configure the firewall...
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '__elevated__ %*' -Verb RunAs -WorkingDirectory '%CD%'"
        if %ERRORLEVEL% NEQ 0 (
            echo [FireCoast] Unable to request Administrator privileges. Exiting.
            exit /b %ERRORLEVEL%
        )
        exit /b 0
    ) else (
        echo [FireCoast] Administrator privileges are required to manage the firewall automatically.
        echo [FireCoast] Continuing without firewall automation.
        set "FIRECOAST_SKIP_FIREWALL=1"
    )
) else (
    set "FIRECOAST_SKIP_FIREWALL=0"
)

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

if not "%FIRECOAST_SKIP_FIREWALL%"=="1" (
    echo [FireCoast] Ensuring firewall access for new device registration...
    "%PYTHON_EXE%" scripts\ensure_firewall_registration.py
    set "FIRECOAST_FIREWALL_EXIT=%ERRORLEVEL%"
    if "%FIRECOAST_FIREWALL_EXIT%"=="2" (
        echo [FireCoast] Firewall automation requires Administrator privileges. FireCoast will continue to launch, but automatic device approval may be blocked until access is granted.
    ) else if "%FIRECOAST_FIREWALL_EXIT%"=="3" (
        echo [FireCoast] Warning: Automatic firewall configuration failed. Review the message above and adjust the firewall manually if needed.
    )
) else (
    echo [FireCoast] Skipping firewall automation.
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
