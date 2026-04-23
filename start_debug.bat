@echo off
setlocal

echo ============================================================
echo   ProxiPT - DEBUG MODE (Visible GUI / No Headless)
echo ============================================================
echo.

:: Check if .venv exists
if not exist ".venv" (
    echo [ERROR] Virtual environment not found. Please run normal start.bat first.
    pause
    exit /b
)

:: Activate venv
call .venv\Scripts\activate

:: Use environment variable to override headless config safely
set PROXIPT_HEADLESS=false

echo [INFO] Starting ProxiPT server on port 8787...
echo [INFO] Watch the automation happen in real Chrome windows!
echo.

:: Start the server in background, wait a bit, then open browser
start "" cmd /c "timeout /t 3 >nul && start http://localhost:8787/dashboard"

:: Run the application
python -m proxipt.main

pause
