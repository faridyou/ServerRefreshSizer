@echo off
REM RefreshSizer launcher (Windows 10/11). No admin rights needed: everything lives in .venv.
REM   double-click run.bat      -> start the local web UI (127.0.0.1) and open the browser
REM   run.bat <command> ...     -> CLI, e.g. run.bat run inventory.xlsx --out reports --yes
setlocal
cd /d "%~dp0"
set "STAMP=.venv\requirements.lock.installed"
if not exist ".venv\Scripts\python.exe" goto install
fc /b requirements.lock "%STAMP%" >nul 2>nul
if errorlevel 1 goto install
goto start
:install
where uv >nul 2>nul
if %ERRORLEVEL%==0 (
  if not exist ".venv\Scripts\python.exe" uv venv .venv
  uv pip install -p .venv\Scripts\python.exe -r requirements.lock
  uv pip install -p .venv\Scripts\python.exe --no-deps -e .
) else (
  if not exist ".venv\Scripts\python.exe" (py -3 -m venv .venv 2>nul || python -m venv .venv)
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.lock
  .venv\Scripts\python.exe -m pip install --no-deps -e .
)
copy /y requirements.lock "%STAMP%" >nul
:start
if "%~1"=="" (
  .venv\Scripts\python.exe -m refreshsizer ui
) else (
  .venv\Scripts\python.exe -m refreshsizer %*
)
endlocal
