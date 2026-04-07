#!/usr/bin/env bash

echo "============================================================"
echo "  ProxiPT - Free LLM Chat to OpenAI API Server"
echo "============================================================"
echo ""

# Check Python installation
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed. Please install Python 3.10+ and try again."
    exit 1
fi

# Check if .venv exists
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating Python virtual environment..."
    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment."
        exit 1
    fi
fi

# Activate venv
source .venv/bin/activate

# Check if required packages are installed
python -c "import proxipt" &> /dev/null
if [ $? -ne 0 ]; then
    echo "[INFO] Installing required packages..."
    pip install -e "."
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to install dependencies."
        exit 1
    fi
    echo "[INFO] Installing Playwright browsers..."
    playwright install chromium
fi

echo ""
echo "[INFO] Starting ProxiPT server on port 8787..."
echo "[INFO] A browser window will automatically open with the dashboard in 3 seconds."
echo ""

# Wait and open browser based on OS
(
    sleep 3
    if command -v xdg-open &> /dev/null; then
        xdg-open "http://localhost:8787/dashboard"
    elif command -v open &> /dev/null; then
        open "http://localhost:8787/dashboard"
    fi
) &

# Run the application
python -m proxipt.main
