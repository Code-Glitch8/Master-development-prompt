@echo off
rem Starts Project Pilot (or just opens the dashboard if it is already running).
rem Works both from the installed location and from the extracted ZIP.
if "%PROJECT_PILOT_HOME%"=="" set "PROJECT_PILOT_HOME=%LOCALAPPDATA%\ProjectPilot"
if exist "%~dp0ProjectPilot.exe" (set "EXE=%~dp0ProjectPilot.exe") else (set "EXE=%~dp0ProjectPilot\ProjectPilot.exe")
if not exist "%EXE%" ( echo ProjectPilot.exe not found. & pause & exit /b 1 )
"%EXE%" %*
if errorlevel 1 pause
