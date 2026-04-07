@echo off
setlocal

echo ============================================================
echo   ProxiPT - Free LLM Chat to OpenAI API Server
echo ============================================================
echo.

:: Check python installation
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed. Please install Python 3.10+ and try again.
    pause
    exit /b
)

:: Check if .venv exists
if not exist ".venv" (
    echo [INFO] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b
    )
)

:: Activate venv
call .venv\Scripts\activate

:: Check if requirements are installed
python -c "import proxipt" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Installing required packages...
    pip install -e "."
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b
    )
    echo [INFO] Installing Playwright browsers...
    playwright install chromium
)

echo.
echo [INFO] Starting ProxiPT server on port 8787...
echo [INFO] A browser window will automatically open with the dashboard.
echo.

:: Start the server in background, wait a bit, then open browser
start "" cmd /c "timeout /t 3 >nul && start http://localhost:8787/dashboard"

:: Run the application
python -m proxipt.main

pause
