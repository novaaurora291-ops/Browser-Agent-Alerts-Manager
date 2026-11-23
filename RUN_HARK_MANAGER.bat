@echo off
rem Hark Browser Manager - double-click to run from source (no EXE needed)
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python nahi mila. python.org se Python 3.12 install karein ^(Add to PATH tick karein^).
  pause
  exit /b 1
)

rem first run: install missing packages once
python -c "import PySide6, win32gui, psutil, keyring, pywinauto, PIL, requests, pyperclip, websocket" >nul 2>nul
if errorlevel 1 (
  echo Pehli dafa zaroori packages install ho rahe hain...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Packages install nahi hue. Internet check karke dobara chalayen.
    pause
    exit /b 1
  )
)

rem start without a console window
for /f "delims=" %%i in (where pythonw 2^>nul) do (
  start "" "%%i" "%~dp0run.py"
  exit /b 0
)
start "" python "%~dp0run.py"
