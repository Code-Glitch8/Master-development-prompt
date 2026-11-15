@echo off
setlocal
rem Project Pilot installer. Copies the app for the current user (no admin rights needed).
rem Your data (database, projects, conversations, checkpoints) lives in %LOCALAPPDATA%\ProjectPilot\data
set "SRC=%~dp0ProjectPilot"
set "APP=%LOCALAPPDATA%\Programs\ProjectPilot"
set "DATA=%LOCALAPPDATA%\ProjectPilot"

if not exist "%SRC%\ProjectPilot.exe" (
  echo Could not find ProjectPilot\ProjectPilot.exe next to this script.
  echo Extract the whole ZIP first, then run install.bat again.
  pause & exit /b 1
)
echo Installing Project Pilot to "%APP%" ...
if exist "%APP%" (
  rem Upgrade: remove old program files only; data folder is never touched.
  rmdir /s /q "%APP%" || ( echo Close Project Pilot first, then retry. & pause & exit /b 1 )
)
mkdir "%APP%" || ( pause & exit /b 1 )
xcopy "%SRC%\*" "%APP%\" /e /i /q /y >nul || ( echo Copy failed. & pause & exit /b 1 )
copy /y "%~dp0run.bat" "%APP%\run.bat" >nul
copy /y "%~dp0stop.bat" "%APP%\stop.bat" >nul
copy /y "%~dp0uninstall.bat" "%APP%\uninstall.bat" >nul
if not exist "%DATA%" mkdir "%DATA%"

set "PS=powershell -NoProfile -ExecutionPolicy Bypass -Command"
set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
%PS% "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%SM%\Project Pilot.lnk'); $s.TargetPath='%APP%\run.bat'; $s.WorkingDirectory='%APP%'; $s.IconLocation='%APP%\ProjectPilot.exe'; $s.WindowStyle=7; $s.Save()"
%PS% "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Project Pilot.lnk'); $s.TargetPath='%APP%\run.bat'; $s.WorkingDirectory='%APP%'; $s.IconLocation='%APP%\ProjectPilot.exe'; $s.WindowStyle=7; $s.Save()"

set /p AUTO="Start Project Pilot's background service when you sign in to Windows? [y/N] "
if /i "%AUTO%"=="y" (
  %PS% "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%SM%\Startup\Project Pilot (background).lnk'); $s.TargetPath='%APP%\ProjectPilot.exe'; $s.Arguments='--no-browser'; $s.WorkingDirectory='%APP%'; $s.WindowStyle=7; $s.Save()"
  echo Background start at sign-in enabled.
)
echo.
echo Installed. Open "Project Pilot" from the Start Menu or Desktop.
echo Dashboard: http://127.0.0.1:8765   (local only; nothing leaves your computer except calls you configure to AI providers)
echo Data folder: %DATA%\data
pause
endlocal
