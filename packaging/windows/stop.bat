@echo off
rem Stops the Project Pilot background service. Tasks are saved and resume on next start.
taskkill /im ProjectPilot.exe /f >nul 2>&1 && (echo Project Pilot stopped.) || (echo Project Pilot is not running.)
pause
