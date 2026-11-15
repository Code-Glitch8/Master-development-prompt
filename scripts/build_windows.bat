@echo off
rem Local Windows build (same steps as .github/workflows/build-windows.yml).
rem Requires Python 3.12+ and Node.js 20+ on PATH. Output: release\ProjectPilot-<version>-windows.zip
setlocal
cd /d "%~dp0.."
call npm --prefix frontend ci || exit /b 1
call npm --prefix frontend run build || exit /b 1
python -m venv build\venv || exit /b 1
call build\venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r backend\requirements.txt -c backend\constraints.txt || exit /b 1
pip install pyinstaller -c backend\constraints.txt || exit /b 1
pyinstaller packaging\project_pilot.spec --noconfirm --distpath build\dist --workpath build\work || exit /b 1
python scripts\smoke_test.py build\dist\ProjectPilot\ProjectPilot.exe || exit /b 1
python scripts\build_dist.py --platform windows || exit /b 1
echo Done. See the release folder.
endlocal
