@echo off
setlocal
rem Removes the program, shortcuts and start-at-login entry. Your data is KEPT unless you say otherwise.
set "APP=%LOCALAPPDATA%\Programs\ProjectPilot"
set "DATA=%LOCALAPPDATA%\ProjectPilot"
set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
taskkill /im ProjectPilot.exe /f >nul 2>&1
del /q "%SM%\Project Pilot.lnk" "%SM%\Startup\Project Pilot (background).lnk" >nul 2>&1
del /q "%USERPROFILE%\Desktop\Project Pilot.lnk" >nul 2>&1
powershell -NoProfile -Command "Remove-Item ([Environment]::GetFolderPath('Desktop')+'\Project Pilot.lnk') -ErrorAction SilentlyContinue"
echo Your projects, conversations, checkpoints and database are in:
echo   %DATA%\data
set /p DEL="Delete that data too? This cannot be undone. [y/N] "
if /i "%DEL%"=="y" ( rmdir /s /q "%DATA%" && echo Data deleted. ) else ( echo Data kept. )
rem The script may be running from inside the install folder; delete it after exit.
start "" /b cmd /c "ping 127.0.0.1 -n 3 >nul & rmdir /s /q "%APP%""
echo Project Pilot uninstalled. API keys stored in Windows Credential Manager (service "project-pilot") can be removed there.
pause
endlocal
