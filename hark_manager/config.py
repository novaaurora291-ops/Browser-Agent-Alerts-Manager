"""Paths, persisted settings and secure secret storage (Windows Credential Manager via keyring)."""
import json
import os
import sys
import shutil

APP_NAME = "HarkBrowserManager"
APP_TITLE = "Hark Browser Manager"
BRIDGE_PORT = 47823
DEFAULT_CHAT_ID = "6938600681"
BOT_USERNAME = "@HarkAgentAlertsPKBot"
KEYRING_SERVICE = "HarkBrowserManager"

APPDATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
LOCALDATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
os.makedirs(APPDATA, exist_ok=True)
os.makedirs(LOCALDATA, exist_ok=True)
SETTINGS_PATH = os.path.join(APPDATA, "settings.json")
ALERTS_PATH = os.path.join(APPDATA, "alerts.json")
LOG_PATH = os.path.join(APPDATA, "app.log")
EXTENSION_DIR = os.path.join(LOCALDATA, "bridge_extension")
# the tool's own folder (next to the EXE, or the source folder when run from source)
TOOL_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def browser_profiles_dir():
    """<tool folder>\\BrowserProfiles, or a LocalAppData copy when the tool folder is read-only (e.g. Program Files)."""
    for base in (TOOL_DIR, LOCALDATA):
        d = os.path.join(base, "BrowserProfiles")
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return d
        except OSError:
            continue
    return os.path.join(LOCALDATA, "BrowserProfiles")

DEFAULTS = {
    "default_browser": "Cent Browser",
    "browser_exe": "",            # Cent Browser's chrome.exe (the only supported browser); empty = found automatically
    "keep_docked": True,          # Cent windows stay docked inside the Manager (no desktop window / taskbar icon)
    "managed_profiles": [],       # [{id, name, exe, data_dir, created}] profiles with their own user-data-dir
    "preview_refresh": 5,
    "show_previews": True,
    "start_with_windows": False,
    "minimize_to_tray": True,
    "restore_last_view": True,
    "telegram_enabled": True,
    "telegram_chat_id": "6938600681",
    "telegram_proxy_type": "Auto",
    "desktop_alerts": True,
    "open_task_on_click": True,
    "play_sound": False,
    "last_view": 0,
    "view_mode": "grid",
    "aliases": {},
    "hidden_profiles": [],
    "repo_team": None,            # Shared Repo Team (see repo_team.py): repo, base commit, planner + workers
    "settings_version": 2,
    "theme": "light",             # "light" / "dark": the whole Manager UI (Settings > Theme)
}


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


def log(msg):
    try:
        import time
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


class Settings:
    def __init__(self):
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                self.data.update(json.load(f))
            old = self.data.pop("telegram_proxy", None)
            if old:
                set_proxy_text(old)
                self.save()
            if not str(self.data.get("telegram_chat_id") or "").strip():
                self.data["telegram_chat_id"] = DEFAULTS["telegram_chat_id"]
        except Exception:
            pass
        try:
            if self.migrate():
                self.save()
        except Exception as e:
            log(f"settings migrate: {e}")

    def migrate(self):
        """Cent-only (settings v2). Nothing is deleted: a non-Cent browser path is kept under legacy_browser_exe and
        profiles made with another browser stay in the file (marked legacy) but are never listed or launched."""
        from . import chrome
        changed = False
        if self.data.get("default_browser") != "Cent Browser":
            self.data["legacy_default_browser"] = self.data.get("default_browser")
            self.data["default_browser"] = "Cent Browser"
            changed = True
        exe = str(self.data.get("browser_exe") or "").strip().strip('"')
        if exe and os.path.isfile(exe) and not chrome.is_cent_exe(exe):
            self.data["legacy_browser_exe"] = exe
            self.data["browser_exe"] = ""          # found again automatically (Cent only)
            log(f"settings: non-Cent browser path moved to legacy_browser_exe ({exe})")
            changed = True
        for m in self.data.get("managed_profiles") or []:
            mexe = str(m.get("exe") or "").strip('"')
            legacy = bool(mexe) and os.path.isfile(mexe) and not chrome.is_cent_exe(mexe)
            if legacy and not m.get("legacy"):
                m["legacy"] = True
                log(f"settings: profile {m.get('id')} was made with {mexe}: kept, marked legacy (not Cent)")
                changed = True
        if self.data.get("settings_version") != 2:
            self.data["settings_version"] = 2
            changed = True
        return changed

    def save(self):
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS_PATH)

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    def get(self, k, d=None):
        v = self[k]
        return d if v is None else v

    def __setitem__(self, k, v):
        self.data[k] = v


# ---- secrets (Windows Credential Manager) ----
def _kr():
    import keyring
    return keyring


def get_secret(name):
    try:
        return _kr().get_password(KEYRING_SERVICE, name) or ""
    except Exception as e:
        log(f"keyring get failed: {e}")
        return ""


def set_secret(name, value):
    kr = _kr()
    if value:
        kr.set_password(KEYRING_SERVICE, name, value)
    else:
        try:
            kr.delete_password(KEYRING_SERVICE, name)
        except Exception:
            pass


def get_bot_token():
    return get_secret("telegram_bot_token")


def set_bot_token(v):
    v = (v or "").strip()
    if v.lower().startswith("bot") and ":" in v:
        v = v[3:]
    set_secret("telegram_bot_token", v)
    log(f"bot token saved to Credential Manager ({len(v)} chars, {mask_token(v)})")


def get_proxy_text():
    return get_secret("telegram_proxy")


def set_proxy_text(v):
    set_secret("telegram_proxy", (v or "").strip())


def load_proxy(settings):
    """Proxy for Telegram only, credentials from Credential Manager. Returns parsed dict or None."""
    from . import notify
    txt = get_proxy_text()
    if not txt:
        notify.set_proxy(None)
        return None
    px = notify.parse_proxy(txt, settings["telegram_proxy_type"])
    notify.set_proxy(px)
    return px


def mask_token(t):
    return (t[:4] + "..." + t[-3:]) if t and len(t) > 10 else ("•••" if t else "(empty)")


# ---- Windows startup ----
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def exe_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    return f'"{sys.executable.replace("python.exe", "pythonw.exe")}" "{os.path.abspath(sys.argv[0])}" --minimized'


def set_start_with_windows(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, exe_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def get_start_with_windows():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def bridge_token(tag, create=True):
    """This profile's own bridge secret (Windows Credential Manager). Generated by the Manager on first use."""
    import secrets
    if tag in _TOKENS:
        return _TOKENS[tag]
    name = f"bridge_token_{tag}"
    tok = get_secret(name)
    if tok:
        _TOKENS[tag] = tok
    if not tok and create:
        tok = secrets.token_urlsafe(32)
        try:
            set_secret(name, tok)
        except Exception as e:
            log(f"bridge token save {tag}: {e}")
        _TOKENS[tag] = tok
        log(f"bridge token created for profile {tag} ({len(tok)} chars)")
    return tok


_TOKENS = {}


def ensure_profile_extension(data_dir, tag):
    """A copy of the bridge extension inside a managed profile's own folder, with tag.json naming the profile
    and carrying that profile's own bridge token (checked by the Manager on every request)."""
    src = ensure_extension()
    dst = os.path.join(data_dir, "_hbm_bridge")
    os.makedirs(dst, exist_ok=True)
    for fn in os.listdir(src):
        s = os.path.join(src, fn)
        if os.path.isfile(s):
            d = os.path.join(dst, fn)
            try:
                if not os.path.exists(d) or open(s, "rb").read() != open(d, "rb").read():
                    shutil.copy2(s, d)
            except Exception as e:
                log(f"profile ext copy {fn}: {e}")
    t = os.path.join(dst, "tag.json")
    want = json.dumps({"tag": tag, "token": bridge_token(tag), "build": extension_build()})
    if not os.path.exists(t) or open(t, encoding="utf-8").read() != want:
        with open(t, "w", encoding="utf-8") as f:
            f.write(want)
    return dst


_BUILD = []


def extension_build():
    """Short hash of the bundled bridge files: a running bridge whose files differ is told to reload."""
    if not _BUILD:
        import hashlib
        h = hashlib.sha1()
        src = resource_path("extension")
        try:
            for fn in sorted(os.listdir(src)):
                fp = os.path.join(src, fn)
                if os.path.isfile(fp) and fn != "tag.json":
                    h.update(fn.encode() + b"\0" + open(fp, "rb").read().replace(b"\r\n", b"\n"))
        except OSError:
            pass
        _BUILD.append(h.hexdigest()[:12])
    return _BUILD[0]


def ensure_extension():
    """Copy the bundled bridge extension to a stable folder Chrome can load it from."""
    src = resource_path("extension")
    if not os.path.isdir(src):
        return EXTENSION_DIR
    os.makedirs(EXTENSION_DIR, exist_ok=True)
    for fn in os.listdir(src):
        s = os.path.join(src, fn)
        d = os.path.join(EXTENSION_DIR, fn)
        if os.path.isfile(s):
            try:
                if not os.path.exists(d) or open(s, "rb").read() != open(d, "rb").read():
                    shutil.copy2(s, d)
            except Exception as e:
                log(f"ext copy {fn}: {e}")
    return EXTENSION_DIR
