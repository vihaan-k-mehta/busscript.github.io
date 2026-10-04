@echo off
rem Busscript launcher for Windows.
rem   start.bat         first time: a demo bus with synthetic traffic. Once you have added a channel, your own channels.
rem   start.bat live    never show the demo, only your own channels
rem   start.bat demo    always the demo
rem Any other arguments are passed to the program, for example: start.bat --demo --port 9000
setlocal
cd /d "%~dp0"

echo %~dp0 | find /i "\Temp\" >nul && (
  echo.
  echo This looks like a temporary folder, probably because the zip was opened without extracting it.
  echo Right-click the zip, choose Extract All, move the folder somewhere permanent, and run start.bat from there.
  echo Running it from here would reinstall everything every time.
  echo.
  pause
  exit /b 1
)

set "PY="
for %%C in (python py) do (
  if not defined PY (
    %%C -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul && set "PY=%%C"
  )
)
if not defined PY (
  echo.
  echo Busscript needs Python 3.11 or newer.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo then run start.bat again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating a private Python environment...
  %PY% -m venv .venv || goto :fail
)

".venv\Scripts\python.exe" -c "import can, cantools, fastapi, uvicorn, mcp, asammdf, serial, webview" >nul 2>nul
if errorlevel 1 (
  echo Installing the libraries Busscript needs. This takes a few minutes and needs internet, once.
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail
)

if "%~1"=="" (
  set "ARGS="
) else if /i "%~1"=="live" (
  set "ARGS=--live"
) else if /i "%~1"=="demo" (
  set "ARGS=--demo"
) else (
  set "ARGS=%*"
)

echo Starting Busscript. Your browser will open. Close this window or press Ctrl+C to stop.
".venv\Scripts\python.exe" -m core %ARGS%
goto :eof

:fail
echo.
echo Setup failed. The messages above say why. A common cause is no internet connection.
pause
exit /b 1
