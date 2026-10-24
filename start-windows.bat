@echo off
rem SkimWatch launcher for Windows. Double-click this file.
rem The first run creates a private Python environment in .venv and installs
rem the requirements; later runs start straight away.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto install
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
    echo ERROR: Python 3.10 or newer is needed.
    echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and run this again.
    goto failed
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo ERROR: Your Python is too old. Python 3.10 or newer is needed.
    echo Install it from https://www.python.org/downloads/ and run this again.
    goto failed
)
echo First run: creating a private Python environment ^(.venv^)...
%PY% -m venv .venv
if errorlevel 1 (
    echo ERROR: Could not create the Python environment.
    goto failed
)

:install
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1
if not errorlevel 1 goto run
echo Installing requirements ^(this takes a few minutes the first time^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Could not install the requirements. Check your internet connection and run this again.
    goto failed
)
copy /y requirements.txt ".venv\requirements.installed" >nul

:run
".venv\Scripts\python.exe" webapp.py %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
pause
exit /b 1
