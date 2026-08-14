@echo off
REM ====================================================================
REM Demo launcher — one command from a cold machine to a live dashboard.
REM
REM Order matters: refresh today's data, THEN start the API, THEN start
REM the dashboard — and each step waits for the previous one to actually
REM be ready. Starting the dashboard before the API is listening is
REM exactly how you end up staring at "Can't reach the API" mid-demo.
REM
REM Usage:
REM   start_demo.bat                 refresh data, then start the app
REM   start_demo.bat --skip-scrape   data's already fresh, just start the app
REM ====================================================================

cd /d "C:\Users\Acer\Webscraping_Extraction"

if /i "%~1"=="--skip-scrape" goto :start_services

echo.
echo === Refreshing today's data ===
echo This runs the same scrape as the scheduled task — several minutes,
echo and it opens a visible browser window on purpose (Naukri blocks
echo headless scraping). If you already scraped today, run this script
echo with --skip-scrape instead.
echo.
call run_daily_scrape.bat

:start_services
echo.
echo === Starting the API ===
start "Job Market API" cmd /k "uvicorn api.main:app --reload"

echo Waiting for the API to come up...
set ATTEMPTS=0

:wait_api
set /a ATTEMPTS+=1
if %ATTEMPTS% GTR 30 (
    echo.
    echo API didn't respond after 60 seconds — check the "Job Market API" window for errors.
    goto :start_dashboard
)
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri http://localhost:8000/health -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    REM ping as a 2-second pause instead of `timeout` — timeout resolves
    REM to a different tool if a Unix toolchain (e.g. Git for Windows)
    REM sits ahead of System32 on PATH, and silently stops pacing the loop.
    ping -n 3 127.0.0.1 >nul
    goto :wait_api
)
echo API is up.

:start_dashboard
echo.
echo === Starting the dashboard ===
start "Job Market Dashboard" cmd /k "streamlit run Home.py"

echo.
echo Both are launching in their own windows. Streamlit opens your browser
echo automatically once it's ready.
