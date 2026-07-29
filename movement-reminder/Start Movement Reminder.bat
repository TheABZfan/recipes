@echo off
setlocal
cd /d "%~dp0"

REM Edit this line to change the rhythm, e.g. set FLAGS=--interval 45
set FLAGS=

REM Prefer the py/pyw launcher. python.org installs it even when "Add python.exe
REM to PATH" was left unticked, and unlike "pythonw" it never resolves to the
REM Microsoft Store stub in WindowsApps (which exists on PATH but launches
REM nothing at all - the classic "console flashes, nothing happens").
set "PY="
set "PYW="

where pyw >nul 2>&1
if not errorlevel 1 set "PYW=pyw"
if defined PYW set "PY=py"

if not defined PYW where pythonw >nul 2>&1
if not defined PYW if not errorlevel 1 set "PYW=pythonw"
if not defined PY if defined PYW set "PY=python"

if not defined PYW goto nopython

REM Confirm the interpreter actually runs and can open windows. This is what
REM catches the Store stub and a Python built without tkinter - and because it
REM runs in this console, you get to read the error instead of a blank flash.
%PY% -c "import tkinter"
if errorlevel 1 goto badpython

start "" %PYW% "movement_reminder.py" %FLAGS%
exit /b 0

:nopython
echo.
echo   Could not find Python on this PC.
echo.
echo   Install it from https://www.python.org/downloads/ and tick
echo   "Add python.exe to PATH" during setup, then run this file again.
echo.
pause
exit /b 1

:badpython
echo.
echo   Found Python (%PY%) but it could not start, or it has no tkinter,
echo   so the app cannot open a window. The error above says which.
echo.
echo   Most common causes:
echo     - "%PY%" is the Microsoft Store placeholder rather than real Python.
echo       Install Python from https://www.python.org/downloads/ instead, or
echo       turn off the python.exe/pythonw.exe aliases under
echo       Settings ^> Apps ^> Advanced app settings ^> App execution aliases.
echo     - Python was installed without the "tcl/tk and IDLE" option. Re-run
echo       the python.org installer, choose Modify, and tick it.
echo.
pause
exit /b 1
