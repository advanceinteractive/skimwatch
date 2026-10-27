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
rem Deno is the JavaScript runtime the downloader needs for YouTube links.
".venv\Scripts\python.exe" -c "import sys, local_transcribe; sys.exit(0 if local_transcribe.find_js_runtime() else 1)" >nul 2>&1
if not errorlevel 1 goto start
set /p DENO_VERSION=<packaging\deno-version.txt
echo Downloading Deno ^(used for YouTube links^)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$asset='deno-x86_64-pc-windows-msvc.zip';" ^
  "Invoke-WebRequest -Uri ('https://github.com/denoland/deno/releases/download/%DENO_VERSION%/' + $asset) -OutFile '.venv\deno.zip';" ^
  "$want=(Select-String -Path 'packaging\deno-sha256.txt' -Pattern $asset).Line.Split(' ')[0];" ^
  "$got=(Get-FileHash '.venv\deno.zip' -Algorithm SHA256).Hash.ToLower();" ^
  "if ($got -ne $want) { throw 'checksum mismatch' };" ^
  "Expand-Archive -Force '.venv\deno.zip' '.venv\Scripts';" ^
  "Remove-Item '.venv\deno.zip'"
if errorlevel 1 echo WARNING: Could not download Deno. YouTube links may fail until you install it from https://deno.com

:start
".venv\Scripts\python.exe" webapp.py %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
pause
exit /b 1
