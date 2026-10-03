@echo off
setlocal EnableDelayedExpansion
title ITC Shield
cd /d "%~dp0"

echo ==========================================
echo   ITC Shield - local launcher
echo ==========================================
echo.

rem ---- 1. prerequisites -----------------------------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python was not found. Install Python 3.11+ from https://www.python.org/downloads/ ^(tick "Add python to PATH"^) and run this file again.
  pause & exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
  echo [!] Node.js was not found. Install the LTS version from https://nodejs.org/ and run this file again.
  pause & exit /b 1
)

rem ---- 2. backend dependencies (first run only; later runs are quick) -------------------------------
echo [1/4] Installing backend dependencies...
python -m pip install -q -r backend\requirements.txt
if errorlevel 1 (
  echo [!] pip install failed. Check your internet connection and try again.
  pause & exit /b 1
)

rem ---- 3. frontend build (only when missing; delete frontend\dist to force a rebuild) ---------------
if not exist "frontend\dist\index.html" (
  echo [2/4] Installing frontend dependencies ^(first run only^)...
  pushd frontend
  if not exist "node_modules" call npm install --no-audit --no-fund
  echo [3/4] Building the user interface...
  call npm run build
  popd
  if not exist "frontend\dist\index.html" (
    echo [!] The frontend build failed. See the messages above.
    pause & exit /b 1
  )
) else (
  echo [2/4] Frontend already built ^(delete frontend\dist to rebuild^).
  echo [3/4] Skipping build.
)

rem ---- 4. start the server (serves API + UI on one port) and open the browser ----------------------
set PORT=8000
echo [4/4] Starting ITC Shield on http://127.0.0.1:%PORT% ...
start "ITC Shield server" cmd /k "cd /d "%~dp0backend" && python -m uvicorn main:app --host 127.0.0.1 --port %PORT%"
timeout /t 4 /nobreak >nul
start "" "http://127.0.0.1:%PORT%"
echo.
echo ITC Shield is running. Keep the "ITC Shield server" window open; close it to stop.
echo Optional: put your Groq API key in backend\.env ^(see .env.example^) to enable AI-written briefs.
timeout /t 5 >nul
endlocal
