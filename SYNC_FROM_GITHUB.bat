@echo off
REM Hark Browser Manager: is folder ko GitHub par ISI folder ki branch ki latest copy tak sync karo, phir yahin se launch karo.
REM Safe sync:
REM   - tracked files mein local edits hon to RUK jata hai (kuch nahi badalta)
REM   - sirf fast-forward; is PC par aise commits hon jo GitHub par nahi, to RUK jata hai
REM   - update se pehle restore point: branch backup/before-sync-YYYYMMDD-HHMMSS
REM   - chalta Manager pehle saaf band hota hai (run.py --quit: docked Cent windows desktop par wapas)
REM   - profiles (BrowserProfiles\, git ignore) aur settings (%APPDATA%\HarkBrowserManager) ko git chhoota bhi nahi
REM   - "SYNC SUCCESSFUL" sirf tab jab local HEAD = GitHub branch ka SHA (git ls-remote) aur working tree clean
REM Self-update: git yeh file sync ke dauran badal sakta hai, is liye %TEMP% copy se chalti hai.
REM HBM_NOPAUSE=1: pause nahi.  HBM_NOLAUNCH=1: launch nahi (tests).

if /i "%~1"=="--run" goto :stage2
set "HBM_SYNC_TMP=%TEMP%\hbm_sync_run.bat"
copy /y "%~f0" "%HBM_SYNC_TMP%" >nul || ( echo SYNC FAILED: %TEMP% mein copy nahi hui. Kuch nahi badla. & pause & exit /b 7 )
"%HBM_SYNC_TMP%" --run "%~dp0."

:stage2
setlocal EnableExtensions
set "PAUSE=pause"
if defined HBM_NOPAUSE set "PAUSE=type nul"
set "WHY="
cd /d "%~2" || ( set "WHY=Folder nahi mila: %~2" & goto :fail )
if not exist "run.py" ( set "WHY=Yeh Hark Browser Manager ka folder nahi - run.py nahi mila: %cd%" & goto :fail )
where git >nul 2>&1 || ( set "WHY=Git install nahi hai: https://git-scm.com/download/win" & goto :fail )
git rev-parse --is-inside-work-tree >nul 2>&1 || ( set "WHY=Yeh folder git clone nahi hai: %cd%" & goto :fail )
set "BRANCH="
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set "BRANCH=%%b"
if not defined BRANCH ( set "WHY=Branch pata nahi chali." & goto :fail )
if "%BRANCH%"=="HEAD" ( set "WHY=Folder kisi branch par nahi - detached HEAD. Pehle: git checkout step1-cent-team" & goto :fail )
call :ver
for /f %%a in ('git rev-parse HEAD') do set "OLDHEAD=%%a"
echo ============================================================
echo  Hark Browser Manager sync
echo  Abhi:   v%VER%  (%OLDHEAD:~0,7%)   branch %BRANCH%
echo  Folder: %cd%
echo ============================================================
echo.
echo GitHub check ho raha hai...
git fetch origin "%BRANCH%"
if errorlevel 1 ( set "WHY=GitHub se fetch fail - internet ya GitHub login ka masla. Kuch nahi badla." & goto :fail )
git rev-parse --verify --quiet "refs/remotes/origin/%BRANCH%" >nul
if errorlevel 1 ( set "WHY=GitHub par branch %BRANCH% nahi mili. Kuch nahi badla." & goto :fail )
for /f %%a in ('git rev-parse "refs/remotes/origin/%BRANCH%"') do set "REMOTE=%%a"

REM 1) local edits in tracked files: stop, touch nothing
git diff --quiet HEAD
if errorlevel 1 goto :localchanges

REM 2) already at GitHub's commit
if "%OLDHEAD%"=="%REMOTE%" ( echo Pehle se latest hai. & goto :verify )

REM 3) commits on this PC that GitHub does not have: stop
git merge-base --is-ancestor HEAD "refs/remotes/origin/%BRANCH%"
if errorlevel 1 goto :diverged

REM 4) close the running Manager, restore point, fast-forward only
call :stopmgr
if defined WHY goto :fail
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%t"
git branch -f "backup/before-sync-%TS%" HEAD >nul
echo Restore point: backup/before-sync-%TS%
echo.
echo Badalne wali files:
git diff --stat HEAD "refs/remotes/origin/%BRANCH%"
git merge --ff-only --quiet "refs/remotes/origin/%BRANCH%"
if errorlevel 1 ( set "WHY=Fast-forward update fail - shayad koi untracked file raaste mein hai. Kuch overwrite nahi hua. Restore point: backup/before-sync-%TS%" & goto :fail )
git diff --quiet "%OLDHEAD%" HEAD -- requirements.txt
if errorlevel 1 call :pipinstall
if defined WHY goto :fail

:verify
set "LOCAL="
set "GH="
for /f %%a in ('git rev-parse HEAD') do set "LOCAL=%%a"
for /f %%a in ('git ls-remote origin "refs/heads/%BRANCH%"') do set "GH=%%a"
if not defined GH ( set "WHY=GitHub se %BRANCH% ka SHA nahi mila - ls-remote fail. Sync confirm nahi hua." & goto :fail )
if /i not "%LOCAL%"=="%GH%" ( set "WHY=Local %LOCAL:~0,7% GitHub %GH:~0,7% se match nahi karta - shayad abhi GitHub par naya commit aaya. BAT dobara chalayen." & goto :fail )
git diff --quiet HEAD
if errorlevel 1 ( set "WHY=Working tree mein local edits hain - code exactly %LOCAL:~0,7% nahi." & goto :fail )
call :ver
echo.
echo ============================================================
echo  SYNC SUCCESSFUL:  v%VER%  (%LOCAL:~0,7%)  =  GitHub %BRANCH%
echo ============================================================
if defined HBM_NOLAUNCH goto :ok
call :stopmgr_if_other
echo Manager launch ho raha hai: %cd%
call "%cd%\RUN_HARK_MANAGER.bat"
call :waitmgr
if defined WHY goto :fail
echo Manager chal raha hai - title/sidebar mein v%VER% (%LOCAL:~0,7%) dikhna chahiye.
goto :ok

:localchanges
echo.
echo SYNC FAILED: is PC par tracked files mein local edits hain jo GitHub par nahi. Kuch nahi badla.
git status --short --untracked-files=no
echo Inhein commit + push karein, ya agent ko is window ka screenshot bhejein.
%PAUSE%
endlocal & exit /b 2

:diverged
echo.
echo SYNC FAILED: is PC par aise commits hain jo GitHub %BRANCH% par nahi. Kuch nahi badla.
git log --oneline "refs/remotes/origin/%BRANCH%..HEAD"
echo Pehle git push karein, ya agent ko screenshot bhejein.
%PAUSE%
endlocal & exit /b 3

:fail
echo.
echo SYNC FAILED: %WHY%
%PAUSE%
endlocal & exit /b 1

:ok
%PAUSE%
endlocal & exit /b 0

REM ---------- helpers ----------
:ver
set "VER=unknown"
for /f "tokens=2 delims==" %%v in ('findstr /b /c:"APP_VERSION" hark_manager\version.py 2^>nul') do set "VER=%%v"
set "VER=%VER: =%"
set VER=%VER:"=%
exit /b 0

:running
set "RUNNING="
for /f %%r in ('powershell -NoProfile -Command "if([IO.Directory]::GetFiles('\\.\pipe\') -contains '\\.\pipe\HarkBrowserManager'){'yes'}"') do set "RUNNING=%%r"
exit /b 0

:stopmgr
call :running
if not defined RUNNING exit /b 0
echo Chalta Manager band ho raha hai (docked Cent windows desktop par wapas)...
call :pyexe
if not defined PY ( set "WHY=Python nahi mila, chalta Manager band nahi kar saka. Tray se Quit karke dobara chalayen." & exit /b 1 )
"%PY%" "%cd%\run.py" --quit >nul 2>&1
for /l %%i in (1,1,25) do (
  timeout /t 1 /nobreak >nul
  call :running
  if not defined RUNNING exit /b 0
)
set "WHY=Chalta Manager 25 s mein band nahi hua. Tray icon se Quit karein, phir BAT dobara chalayen. Kuch nahi badla."
exit /b 1

:stopmgr_if_other
REM up-to-date path: a Manager running old code (another folder or before this sync) is restarted from this folder
call :running
if not defined RUNNING exit /b 0
call :stopmgr
exit /b 0

:waitmgr
for /l %%i in (1,1,30) do (
  timeout /t 1 /nobreak >nul
  call :running
  if defined RUNNING exit /b 0
)
set "WHY=Sync theek hua (v%VER% %LOCAL:~0,7%), lekin Manager 30 s mein start nahi hua. %APPDATA%\HarkBrowserManager\app.log dekhein."
exit /b 1

:pyexe
set "PY="
for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
exit /b 0

:pipinstall
call :pyexe
if not defined PY ( set "WHY=requirements.txt badli, lekin Python nahi mila." & exit /b 1 )
echo requirements.txt badli: packages install ho rahe hain...
"%PY%" -m pip install -q -r requirements.txt
if errorlevel 1 ( set "WHY=Code sync ho gaya, lekin pip install fail - internet check karke BAT dobara chalayen." & exit /b 1 )
exit /b 0
