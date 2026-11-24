@echo off
REM Builds dist\HarkBrowserManager.exe (one file, no console)
cd /d %~dp0
python -m PyInstaller --noconfirm --clean --onefile --windowed --name HarkBrowserManager ^
  --add-data "extension;extension" ^
  --add-data "hark_manager\ui\assets;hark_manager\ui\assets" ^
  --hidden-import keyring.backends.Windows --hidden-import win32timezone ^
  run.py
