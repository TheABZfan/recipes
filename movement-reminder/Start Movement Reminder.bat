@echo off
REM Double-click this to start the reminder with no console window.
REM Edit the flags below if you want a different rhythm, e.g.:
REM   set FLAGS=--interval 45 --break-length 120
set FLAGS=

cd /d "%~dp0"

where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw "movement_reminder.py" %FLAGS%
    exit /b 0
)

where python >nul 2>&1
if %errorlevel%==0 (
    start "" python "movement_reminder.py" %FLAGS%
    exit /b 0
)

echo Python was not found on your PATH.
echo Install it from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" during setup, then run this file again.
pause
exit /b 1
