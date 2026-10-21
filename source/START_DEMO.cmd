@echo off
setlocal DisableDelayedExpansion
"%~dp0runtime\python.exe" -I "%~dp0tools\portable_launcher.py"
set "HO_EXIT=%ERRORLEVEL%"
if "%HO_EXIT%"=="2" (
  echo Safety limit reached. This app instance closed; this was not a STOP test.
  exit /b 2
)
if "%HO_EXIT%"=="3" (
  echo Preparation cancelled. No product worker was created.
  exit /b 3
)
if not "%HO_EXIT%"=="0" (
  echo Home Orchestrator could not confirm complete cleanup. Preserve this extraction.
  pause
)
exit /b %HO_EXIT%
