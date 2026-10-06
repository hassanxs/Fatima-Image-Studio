@echo off
rem Starts Fatima Image Studio and opens it in the browser.
cd /d "%~dp0"
python -m studio %*
if errorlevel 1 pause
