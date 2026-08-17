@echo off
REM ====================================================================
REM Daily job scrape — wrapper for Windows Task Scheduler.
REM
REM Task Scheduler doesn't run from your project folder by default, so
REM this sets the directory explicitly. It also captures all output to a
REM dated log, because a scheduled task runs unattended — without a log
REM you have no way to know whether it worked.
REM ====================================================================

cd /d "C:\Users\Acer\Webscraping_Extraction"

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

REM One line per search. Add or remove searches here — this is where you
REM control what gets collected each day.
python naukri_collector.py "https://www.naukri.com/software-engineer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
python naukri_collector.py "https://www.naukri.com/data-science-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
python naukri_collector.py "https://www.naukri.com/devops-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
python naukri_collector.py "https://www.naukri.com/data-engineer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1
python naukri_collector.py "https://www.naukri.com/full-stack-developer-jobs-in-hyderabad" --limit 20 >> "%LOGFILE%" 2>&1


REM Each naukri_collector.py call above already cleans and writes
REM straight into cleaned_postings (job_database.py -> cleaning.py) —
REM there is no separate batch cleaning step anymore. Only the daily
REM snapshot still needs its own run, after all scraping is done.
"C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d jobmarket -c "SELECT snapshot_daily_skills();" >> "%LOGFILE%" 2>&1

echo Run finished: %date% %time% >> "%LOGFILE%"
