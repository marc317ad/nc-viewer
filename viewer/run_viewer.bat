@echo off
rem Launch the NetCDF viewer from anywhere: double-click or run with extra args
rem (e.g. run_viewer.bat --root D:\some\dir --port 9000).
setlocal
cd /d "%~dp0.."
set "PY=C:\Python313\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m viewer %*
if errorlevel 1 pause
