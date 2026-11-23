# Hark Browser Manager

Windows desktop app (Python + PySide6) that manages Chrome profiles and alerts you when a Hark agent is waiting for your reply.

## Features
- **Profiles**: every Chrome profile with name, live preview and status (Needs reply / Working / Idle / Closed). Add Profile, Search, Status filter, Grid/List, Launch, Focus Window (brings that profile's existing window forward).
- **Alerts**: a small bridge extension (in `extension/`) watches hark.com tabs, including background tabs. Each new Hark question gives one desktop notification and one Telegram message with the profile name and the exact question. Open Task opens the right Hark conversation in the right profile. An alert turns Resolved once you reply in Hark. Alerts are keyed by the Hark message id, so restarts never repeat them.
- **Settings**: preview refresh, start with Windows, minimize to tray, notifications, Telegram bot token (stored in Windows Credential Manager, never in files or logs) and Chat ID, optional proxy for Telegram only (Proxy type Auto / HTTP/HTTPS / SOCKS5; accepts `IP:PORT:USERNAME:PASSWORD`, `user:pass@host:port` or `socks5://` / `http://` URLs; kept in Credential Manager and masked in logs), Send test alert (runs getMe, then a test message).

Telegram calls use a small built-in HTTPS client that splits the TLS ClientHello, which gets through networks that reset connections to api.telegram.org.

## Run from source
```
pip install -r requirements.txt
python run.py
```
## Build the EXE
```
pip install pyinstaller
build.bat
```
Output: `dist\HarkBrowserManager.exe`.

## Bridge extension
Profiles page > profile menu > Install bridge (loads `extension/` unpacked through chrome://extensions, Developer mode). It only talks to `127.0.0.1:47823`. No cookies are read.
