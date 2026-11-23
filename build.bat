@echo off
REM Builds dist\HarkBrowserManager.exe (one file, no console)
cd /d %~dp0
python -m PyInstaller --noconfirm --clean --onefile --windowed --name HarkBrowserManager ^
  --add-data "extension;extension" ^
  --hidden-import keyring.backends.Windows --hidden-import win32timezone ^
  run.py
