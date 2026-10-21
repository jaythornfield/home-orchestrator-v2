@echo off
setlocal DisableDelayedExpansion
"%~dp0runtime\python.exe" -I "%~dp0tools\export_review.py"
set "HO_EXPORT_EXIT=%ERRORLEVEL%"
if not "%HO_EXPORT_EXIT%"=="0" (
  echo Review export is not complete. No private data was included.
  pause
)
exit /b %HO_EXPORT_EXIT%
