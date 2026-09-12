@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 "run_gui.py"
) else if exist "..\.venv\Scripts\python.exe" (
  "..\.venv\Scripts\python.exe" -X utf8 "run_gui.py"
) else (
  python -X utf8 "run_gui.py"
)
if errorlevel 1 pause
