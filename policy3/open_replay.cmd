@echo off
setlocal
set "REPLAY=%~dp0results\acceptance_15_20260913\trajectory_viewer.html"
if not "%~1"=="" set "REPLAY=%~f1"
if not exist "%REPLAY%" goto missing
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
 start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%REPLAY%"
 exit /b 0
)
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
 start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%REPLAY%"
 exit /b 0
)
start "" "%REPLAY%"
exit /b 0
:missing
echo Run from the policy3 directory:
echo python tools/visualize.py --input results/acceptance_15_20260913 --output results/acceptance_15_20260913/trajectory_viewer.html
pause
