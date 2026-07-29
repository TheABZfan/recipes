@echo off
setlocal
cd /d "%~dp0"

REM Run this when "Start Movement Reminder.bat" does nothing. It runs the app
REM with a visible console and keeps the window open, so you can read the error.

echo === Which Python is being used ===
where py 2>nul
where python 2>nul
where pythonw 2>nul
echo.

set "PY=python"
where py >nul 2>&1
if not errorlevel 1 set "PY=py"

echo === Version and tkinter check (%PY%) ===
%PY% -c "import sys; print(sys.version); print(sys.executable)"
%PY% -c "import tkinter; print('tkinter OK, Tk', tkinter.TkVersion)"
echo.

echo === Starting the app (close its window, or press Ctrl+C here, to stop) ===
%PY% "movement_reminder.py" %*

echo.
echo === The app exited. Any error is printed above. ===
pause
