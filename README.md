# Hark Browser Manager

Windows desktop app (Python + PySide6) that manages **Cent Browser** profiles (each with its own `--user-data-dir`) and alerts you when a Hark agent is waiting for your reply. Google Chrome, Edge and Brave are never scanned, matched, launched or touched: a window counts only when its process is the configured Cent `chrome.exe` (full path) running one of the managed profiles' data folders.

## Features
- **Profiles**: every managed Cent profile with name, live preview and status (Needs reply / Working / Idle / Closed). Add Profile, Search, Status filter, Grid/List, Launch, Focus Window (brings that profile's existing window forward).
- **Alerts**: a small bridge extension (in `extension/`) watches hark.com tabs, including background tabs. Each new Hark question gives one desktop notification and one Telegram message with the profile name and the exact question. Open Task opens the right Hark conversation in the right profile. An alert turns Resolved once you reply in Hark. Alerts are keyed by the Hark message id, so restarts never repeat them.
- **Settings**: preview refresh, start with Windows, minimize to tray, notifications, Telegram bot token (stored in Windows Credential Manager, never in files or logs) and Chat ID, optional proxy for Telegram only (Proxy type Auto / HTTP/HTTPS / SOCKS5; accepts `IP:PORT:USERNAME:PASSWORD`, `user:pass@host:port` or `socks5://` / `http://` URLs; kept in Credential Manager and masked in logs), Send test alert (runs getMe, then a test message).
- **Shared Repo Team** (Settings > Shared Repo Team, or Team Dispatch > Edit team): one local git repo, one base commit, a Planner (ChatGPT) profile and worker profiles, each with an `@alias`, its exact `https://hark.com/project/<id>` URL (one project per worker) and its Hark account email (empty = the profile's saved Hark email). Saving creates (or reuses) one git worktree + branch per worker (`agent-<alias>`, from the same base SHA; git runs with argument lists, no shell). The Profiles grid links the team's cards with lime lines to a `Repo: <name> (N Linked • Worktrees Ready)` badge. Multi-View never switches the Planner's tab.
- **Team Plan & Safe Dispatch** (right panel drawer): paste the ChatGPT plan; `@alias` blocks are parsed (unknown, duplicate and empty blocks are flagged) and the Plan Preview card of every worker shows its Hark account, Cent profile, exact Hark project, branch + base SHA, git worktree and the full final prompt (worktree path, branch and base SHA come first). The Target picker chooses where a plan goes:
  - **Real Hark — manual send** (default): the Manager never types or sends anything in Hark. *Open Project* opens the worker's exact project in its own Cent profile; *Copy Task* copies the final prompt (only with a clean worktree); you paste it and press Send yourself.
  - **Local Mock — automatic dispatch**: a mock Hark server on `127.0.0.1` (random port, started on demand, in-memory only) with one signed-in mock account per Cent profile. Dispatch re-checks the worktree, then the account signed in to that profile must be the worker's account and the project must belong to it; otherwise the worker is *Blocked* and nothing is sent. Status goes Dispatch → *Sent* only when the user message is visible in that account's project → *Reply received* only for the assistant reply to that message in the same account + project (an account switch in between gives *Account changed*). A lost delivery acknowledgement gives *Unconfirmed*; from then on every Dispatch of that prompt (also after a *Blocked* retry) reuses the same idempotency key, so it is never stored twice. The same prompt, or a second dispatch to a project that is still busy, is refused. *Switch mock account* signs a different mock account into the same Cent profile to try this; changing a worker's account in Edit team moves its mock project and session along (a profile switched by hand stays switched).
  - **Live API — BLOCKED**: disabled until approved Hark API access exists (`core.Controller.LIVE_API_APPROVED`); the client refuses any non-loopback host before a request is made.

Telegram calls use a small built-in HTTPS client that splits the TLS ClientHello, which gets through networks that reset connections to api.telegram.org.

## Run from source
```
pip install -r requirements.txt
python run.py
```
## Tests
```
python -m unittest discover -s tests -v
```
The Team Dispatch tests run the local mock Hark, a temporary git repo with worktrees and an offscreen panel; they use a temporary AppData and never touch real profiles, settings, the bridge port or the Telegram token. Set `HBM_TEST_SHOTS=<folder>` to save panel screenshots.
## Build the EXE
```
pip install pyinstaller
build.bat
```
Output: `dist\HarkBrowserManager.exe`.

## Bridge extension
Profiles page > profile menu > Install bridge (loads the profile's own copy of `extension/` unpacked through chrome://extensions, Developer mode). It only talks to `127.0.0.1:47823`. No cookies are read. Every request carries the profile's managed ID and its own secret token (generated by the Manager, kept in Windows Credential Manager and in that profile's `_hbm_bridge/tag.json`); anything else is refused. Running bridges reload themselves when their files differ from the Manager's (extension version `1.0.18`, equal to `core.EXT_VERSION`).
