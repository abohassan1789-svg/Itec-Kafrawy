@echo off
REM Run A-H CODE (ITEC accounting) in development mode
cd /d "%~dp0"
py -3.11 -m app.main
if errorlevel 1 pause
