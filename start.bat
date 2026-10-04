@echo off
rem Busscript launcher for Windows.
rem   start.bat         demo bus with synthetic traffic (no hardware needed)
rem   start.bat live    your own channels and adapters
rem Any other arguments are passed to the program, for example: start.bat --demo --port 9000
setlocal
cd /d "%~dp0"

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

".venv\Scripts\python.exe" -c "import can, cantools, fastapi, uvicorn, mcp, asammdf, serial" >nul 2>nul
if errorlevel 1 (
  echo Installing the libraries Busscript needs. This takes a few minutes and needs internet, once.
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail
)

if "%~1"=="" (
  set "ARGS=--demo"
) else if /i "%~1"=="live" (
  set "ARGS="
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
