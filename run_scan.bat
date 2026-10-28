@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set "SCANNER_PYTHON="
if exist ".venv\Scripts\python.exe" set "SCANNER_PYTHON=%CD%\.venv\Scripts\python.exe"
if not defined SCANNER_PYTHON if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "SCANNER_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if defined SCANNER_PYTHON (
  "%SCANNER_PYTHON%" dashboard.py %*
  goto done
)
where py >nul 2>nul
if errorlevel 1 (
  python dashboard.py %*
) else (
  py -3 dashboard.py %*
)
:done
pause
