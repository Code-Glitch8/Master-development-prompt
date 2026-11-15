PROJECT PILOT - Windows package
================================
1. Extract this whole ZIP to a folder (do not run from inside the ZIP).
2. Double-click install.bat  (per-user install, no administrator rights needed).
3. Open "Project Pilot" from the Start Menu or Desktop. The dashboard opens at http://127.0.0.1:8765

Or skip installing: double-click run.bat to run straight from this folder.

Everything stays on your computer:
  Data (database, projects, conversations, checkpoints): %LOCALAPPDATA%\ProjectPilot\data
  API keys: Windows Credential Manager (never in files)
GitHub is only used to download this package; Project Pilot never needs it to run.

stop.bat stops the background service. uninstall.bat removes the app (it asks before deleting your data).
Verify the download: compare the ZIP's SHA-256 with SHA256SUMS.txt from the release page.
Windows SmartScreen may warn about an unsigned app: choose "More info" > "Run anyway" if you trust the source.
Full docs: https://github.com/Code-Glitch8/Master-development-prompt
