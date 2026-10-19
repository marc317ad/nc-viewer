@echo off
rem Launch the viewer from this folder: double-click, or run with extra
rem arguments (e.g. run_viewer.bat --root D:\data\products --port 9000).
rem Interpreter: VIEWER_PYTHON when set, else .venv\Scripts\python.exe next
rem to this file when present, else python on the PATH.
setlocal
cd /d "%~dp0"
set "PY=%VIEWER_PYTHON%"
if "%PY%"=="" if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if "%PY%"=="" set "PY=python"
"%PY%" -m viewer %*
if errorlevel 1 pause
