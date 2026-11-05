@echo off
REM RefreshSizer launcher (Windows 10/11). Installs into a per-project venv: no admin rights needed.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where uv >nul 2>nul
  if %ERRORLEVEL%==0 (
    uv venv .venv
    uv pip install -p .venv\Scripts\python.exe -r requirements.lock
    uv pip install -p .venv\Scripts\python.exe --no-deps -e .
  ) else (
    py -3 -m venv .venv 2>nul || python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.lock
    .venv\Scripts\python.exe -m pip install --no-deps -e .
  )
)
.venv\Scripts\python.exe -m refreshsizer %*
endlocal
