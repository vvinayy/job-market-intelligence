@echo off
REM ====================================================================
REM Job Market pipeline - the single entry point.
REM
REM Replaces the old run_daily_scrape.bat + start_demo.bat pair. They
REM were split because one was for Task Scheduler and one was for
REM demoing, but the scheduled scrape was just this script's first step,
REM so keeping two files meant two places to edit a search URL.
REM
REM Order matters: refresh today's data, THEN start the API, THEN start
REM the dashboard - and each step waits for the previous one to actually
REM be ready. Starting the dashboard before the API is listening is
REM exactly how you end up staring at "Can't reach the API" mid-demo.
REM
REM Usage:
REM   jobmarket.bat                 scrape, then start API + dashboard
REM   jobmarket.bat --skip-scrape   data's already fresh, just start the app
REM   jobmarket.bat --scrape-only   scrape and exit - this is the mode
REM                                 Windows Task Scheduler should run
REM   jobmarket.bat --check-only    check every posting URL for expiry and
REM                                 exit. Runs as its own scheduled task
REM                                 (JobMarket Liveness Check, 5pm daily),
REM                                 kept off the morning path so it cannot
REM                                 delay the dashboard launch. ~10 min.
REM ====================================================================

cd /d "C:\Users\Acer\Webscraping_Extraction"
REM Real Python at D:\python, not the Microsoft Store build. The
REM Store build sandboxes file access in a way that crashes Streamlit's
REM file watcher (access violation, 0xc0000005) and can be silently
REM replaced by a background update -- moved off it 2026-09-17 after two
REM crashes in one day. See CLAUDE.md.
set PY=D:\python\python.exe

if /i "%~1"=="--skip-scrape"  goto :start_services
if /i "%~1"=="--scrape-only"  goto :scrape
if /i "%~1"=="--check-only"   goto :check
if /i "%~1"==""               goto :scrape

echo Unknown option "%~1".
echo Usage: jobmarket.bat [--skip-scrape ^| --scrape-only ^| --check-only]
exit /b 1

REM --------------------------------------------------------------------
REM STAGE 1 - scrape. Each naukri_collector.py call cleans and writes
REM straight into cleaned_postings (job_database.py -> cleaning.py) and
REM records the daily snapshot itself, so there is no separate cleaning
REM or snapshot step per search.
REM --------------------------------------------------------------------
:scrape
echo.
echo === Refreshing today's data ===
echo This opens a visible browser window on purpose (Naukri blocks
echo headless scraping) and takes several minutes. If you already
echo scraped today, re-run with --skip-scrape.
echo.

REM Timestamped log file: logs\scrape_2026-08-06.log
REM %date% formatting varies by Windows locale, so slicing it by
REM character position produces different (often broken) results on
REM different machines. PowerShell gives a consistent format instead.
if not exist "logs" mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set LOGDATE=%%i
set LOGFILE=logs\scrape_%LOGDATE%.log

echo. >> "%LOGFILE%"
echo ================================================== >> "%LOGFILE%"
echo Run started: %date% %time% >> "%LOGFILE%"
echo ================================================== >> "%LOGFILE%"

REM One line per search. Add or remove searches here - this is where you
REM control what gets collected each day.
"%PY%" naukri_collector.py "https://www.naukri.com/python-developer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" naukri_collector.py "https://www.naukri.com/data-science-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" naukri_collector.py "https://www.naukri.com/java-full-stack-developer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" naukri_collector.py "https://www.naukri.com/machine-learning-engineer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" naukri_collector.py "https://www.naukri.com/python-full-stack-developer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1

REM hirist, the second board. Until these were added, every hirist row in
REM the database came from a hand-run collector: times_seen sat at 1.0
REM against naukri's 4.8, nothing ever re-surfaced a posting, and the
REM hirist half of the daily snapshot had holes on the days nobody ran it.
REM Discovery needs a visible browser like the naukri calls above; the
REM detail fetches that follow are plain JSON and need none.
REM
REM Seven searches, each verified to return 20 codes before being added.
REM They overlap barely -- 152 distinct postings across 160 codes when
REM measured -- so each one is a genuinely different slice, not the same
REM jobs re-surfaced. Also verified and ready if more depth is wanted:
REM full-stack-developer, python-developer, qa-engineer, java-developer
REM (java overlaps the most, 14 of 20 new).
"%PY%" hirist_collector.py "https://www.hirist.tech/search/software-developer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" hirist_collector.py "https://www.hirist.tech/search/cloud-engineer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" hirist_collector.py "https://www.hirist.tech/search/machine-learning-engineer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" hirist_collector.py "https://www.hirist.tech/search/data-engineer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" hirist_collector.py "https://www.hirist.tech/search/devops-engineer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" hirist_collector.py "https://www.hirist.tech/search/backend-developer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
"%PY%" hirist_collector.py "https://www.hirist.tech/search/frontend-developer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1

REM Backstop only. naukri_collector.py already snapshots after every
REM run; this catches the case where every search failed before reaching
REM that code. The SQL function recalculates rather than duplicating on
REM a same-day re-run, so calling it again is free.
"C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d jobmarket -c "SELECT snapshot_daily_skills();" >> "%LOGFILE%" 2>&1

echo Run finished: %date% %time% >> "%LOGFILE%"
echo Scrape complete. Log: %LOGFILE%

if /i "%~1"=="--scrape-only" exit /b 0

REM --------------------------------------------------------------------
REM STAGE 2 - API, and wait until it genuinely answers /health.
REM --------------------------------------------------------------------
:start_services
echo.
echo === Starting the API ===
start "Job Market API" cmd /k "%PY% -m uvicorn api.main:app --reload"

echo Waiting for the API to come up...
set ATTEMPTS=0

:wait_api
set /a ATTEMPTS+=1
if %ATTEMPTS% GTR 30 (
    echo.
    echo API didn't respond after 60 seconds - check the "Job Market API" window for errors.
    goto :start_dashboard
)
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri http://localhost:8000/health -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    REM ping as a 2-second pause instead of `timeout` - timeout resolves
    REM to a different tool if a Unix toolchain (e.g. Git for Windows)
    REM sits ahead of System32 on PATH, and silently stops pacing the loop.
    ping -n 3 127.0.0.1 >nul
    goto :wait_api
)
echo API is up.

REM --------------------------------------------------------------------
REM STAGE 3 - dashboard.
REM --------------------------------------------------------------------
:start_dashboard
echo.
echo === Starting the dashboard ===
start "Job Market Dashboard" cmd /k "%PY% -m streamlit run Home.py"

echo.
echo Both are launching in their own windows. Streamlit opens your browser
echo automatically once it's ready.
exit /b 0


REM --------------------------------------------------------------------
REM STAGE 4 - liveness check. Separate from the scrape on purpose: the
REM scraper only ever sees postings a search surfaces, so it can never
REM revisit an expired one. This walks stored URLs directly.
REM
REM Its own 5pm task rather than bolted onto the 11am run: the
REM no-argument path above starts the API and dashboard, which should not
REM wait ~10 minutes on it. Unlike the scrape this needs no browser, so it
REM does not require an interactive logon.
REM --------------------------------------------------------------------
:check
if not exist "logs" mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set LOGDATE=%%i
set LOGFILE=logs\liveness_%LOGDATE%.log

echo. >> "%LOGFILE%"
echo ================================================== >> "%LOGFILE%"
echo Check started: %date% %time% >> "%LOGFILE%"
echo ================================================== >> "%LOGFILE%"

"%PY%" liveness_checker.py >> "%LOGFILE%" 2>&1

REM hirist publishes no expiry signal at all. hasExpired was measured on
REM 2026-09-08 to be a clock, not an event: it flips at exactly 150 days
REM after createdTime, 148d reading False and 150d reading True with no
REM exception in 41 samples spanning 2019 to 2026. It says nothing about
REM whether a job closed, so the nightly probe is switched off. Five nights
REM of it recorded nothing but "live" and could not have recorded anything
REM else. The 150 days are acted on instead by mark_delisted_hirist() inside
REM liveness_checker.py, which is arithmetic on posted_date and needs no
REM network. Run hirist_liveness_probe.py by hand around 2026-11-07, when
REM the oldest posted_date crosses day 150, to confirm that against their
REM own flag rather than against our subtraction.
REM python hirist_liveness_probe.py >> "%LOGFILE%" 2>&1

echo Check finished: %date% %time% >> "%LOGFILE%"
echo Liveness check complete. Log: %LOGFILE%
exit /b 0
