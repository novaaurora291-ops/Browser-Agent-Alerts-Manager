"""Chrome profiles on this PC: discovery, windows (by AppUserModelID), launch, focus and live previews."""
import json
import os
import subprocess
import ctypes
from ctypes import wintypes

from .config import log

USER_DATA = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
CHROME_CANDIDATES = [
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
]

user32 = ctypes.windll.user32


def chrome_exe():
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    return "chrome.exe"


class Profile:
    def __init__(self, directory, name, email="", gaia="", avatar=""):
        self.directory = directory
        self.chrome_name = name
        self.email = email
        self.gaia = gaia
        self.avatar = avatar
        self.alias = ""
        self.kind = "chrome"      # "chrome" = a profile inside Google Chrome's own User Data; "managed" = own user-data-dir
        self.data_dir = ""
        self.exe = ""
        self.tag = ""

    @property
    def managed(self):
        return self.kind == "managed"

    @property
    def browser_name(self):
        return browser_name(self.exe) if self.managed else "Google Chrome"

    @property
    def name(self):
        return self.alias or self.chrome_name or self.directory

    @property
    def aumids(self):
        if self.managed:
            return set()
        d = self.directory.replace(" ", "")
        ids = {f"Chrome.UserData.{d}"}
        if self.directory == "Default":
            ids |= {"Chrome", "Chrome.UserData"}
        return ids


def load_profiles():
    """Read every profile Chrome knows about from Local State (names, signed-in emails)."""
    out = []
    try:
        with open(os.path.join(USER_DATA, "Local State"), encoding="utf-8") as f:
            ls = json.load(f)
        cache = ls.get("profile", {}).get("info_cache", {})
        order = ls.get("profile", {}).get("profiles_order", []) or list(cache)
        for d in order + [k for k in cache if k not in order]:
            v = cache.get(d)
            if not v or not os.path.isdir(os.path.join(USER_DATA, d)):
                continue
            out.append(Profile(d, v.get("name", d), v.get("user_name", ""), v.get("gaia_name", ""), v.get("avatar_icon", "")))
    except Exception as e:
        log(f"load_profiles: {e}")
    return out


def next_profile_dir():
    n = 1
    while os.path.exists(os.path.join(USER_DATA, f"Profile {n}")):
        n += 1
    return f"Profile {n}"


# ---------------- managed profiles (own --user-data-dir, any Chromium browser such as Cent Browser) ----------------
def browser_name(exe):
    """Product name of a browser exe (e.g. 'Cent Browser'), from its version info; falls back to the file name."""
    if not exe:
        return "Google Chrome"
    try:
        import win32api
        lang, cp = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")[0]
        n = win32api.GetFileVersionInfo(exe, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\ProductName")
        if n:
            return str(n).strip()
    except Exception:
        pass
    return os.path.splitext(os.path.basename(exe))[0]


def detect_cent():
    for c in (os.path.join(os.environ.get("LOCALAPPDATA", ""), r"CentBrowser\Application\chrome.exe"),
              os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"CentBrowser\Application\chrome.exe"),
              os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"CentBrowser\Application\chrome.exe")):
        if os.path.exists(c):
            return c
    return ""


def managed_email(data_dir):
    """Signed-in account of a managed profile (read-only, from its Local State), if the browser shows one."""
    try:
        with open(os.path.join(data_dir, "Local State"), encoding="utf-8") as f:
            cache = json.load(f).get("profile", {}).get("info_cache", {})
        v = cache.get("Default") or next(iter(cache.values()), {})
        return v.get("user_name", "") or ""
    except Exception:
        return ""


def norm_dir(d):
    return os.path.normcase(os.path.abspath(d)).rstrip("\\/") if d else ""


def launch_managed(p, exe, url=None, new_window=False, ext_dir=None):
    os.makedirs(p.data_dir, exist_ok=True)
    args = [exe, f"--user-data-dir={p.data_dir}", "--no-first-run", "--no-default-browser-check"]
    if ext_dir and os.path.isdir(ext_dir):
        args.append(f"--load-extension={ext_dir}")
    if new_window:
        args.append("--new-window")
    if url:
        args.append(url)
    DETACHED = 0x00000008 | 0x00000200
    subprocess.Popen(args, creationflags=DETACHED, close_fds=True)
    log(f"launch managed {p.tag} via {os.path.basename(os.path.dirname(os.path.dirname(exe)))} -> {p.data_dir}")


_udd_cache = {}


def _proc_udd(pid):
    """--user-data-dir of a browser process (cached per pid)."""
    if pid in _udd_cache:
        return _udd_cache[pid]
    udd = None
    try:
        import psutil
        for a in psutil.Process(pid).cmdline():
            if a.startswith("--user-data-dir="):
                udd = norm_dir(a.split("=", 1)[1].strip('"'))
                break
    except Exception:
        udd = None
    if len(_udd_cache) > 500:
        _udd_cache.clear()
    _udd_cache[pid] = udd
    return udd


def managed_windows(dirs):
    """Visible top-level browser windows whose process runs with one of our --user-data-dir folders.
    Returns {normalized dir: [(hwnd, title, "", minimized)]}."""
    import win32gui
    import win32process
    want = {norm_dir(d) for d in dirs if d}
    out = {}
    if not want:
        return out

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h) or win32gui.GetClassName(h) != "Chrome_WidgetWin_1":
                return True
            if win32gui.GetWindow(h, 4):  # GW_OWNER: popups/menus belong to a browser window
                return True
            title = win32gui.GetWindowText(h)
            if not title:
                return True
            _, pid = win32process.GetWindowThreadProcessId(h)
            udd = _proc_udd(pid)
            if udd in want:
                out.setdefault(udd, []).append((h, title, "", bool(win32gui.IsIconic(h))))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def launch(profile_dir, url=None, new_window=False):
    args = [chrome_exe(), f"--profile-directory={profile_dir}"]
    if new_window:
        args.append("--new-window")
    if url:
        args.append(url)
    DETACHED = 0x00000008 | 0x00000200
    subprocess.Popen(args, creationflags=DETACHED, close_fds=True)


# ---------------- windows ----------------
def _aumid(hwnd):
    try:
        from win32com.propsys import propsys, pscon
        ps = propsys.SHGetPropertyStoreForWindow(hwnd, propsys.IID_IPropertyStore)
        v = ps.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
        return v or ""
    except Exception:
        return ""


def chrome_windows():
    """All visible top-level Chrome browser windows in z-order: [(hwnd, title, aumid, minimized)]."""
    import win32gui
    res = []

    def cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h) or win32gui.GetClassName(h) != "Chrome_WidgetWin_1":
                return True
            title = win32gui.GetWindowText(h)
            if not title or not title.endswith("Google Chrome"):
                return True
            a = _aumid(h)
            if a:
                res.append((h, title, a, bool(win32gui.IsIconic(h))))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return res


def windows_for(profile, wins=None, mwins=None):
    if profile.managed:
        mwins = managed_windows([profile.data_dir]) if mwins is None else mwins
        return mwins.get(norm_dir(profile.data_dir), [])
    wins = chrome_windows() if wins is None else wins
    ids = profile.aumids
    return [w for w in wins if w[2] in ids]


def focus_hwnd(hwnd):
    import win32gui
    import win32con
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        # Alt-key trick lets a background process hand focus over reliably.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
        return True
    except Exception as e:
        log(f"focus_hwnd {hwnd}: {e}")
        try:
            user32.SwitchToThisWindow(hwnd, True)
            return True
        except Exception:
            return False


def focus_profile(profile, prefer_title=None):
    wins = windows_for(profile)
    if not wins:
        return False
    target = wins[0]
    if prefer_title:
        for w in wins:
            if prefer_title.lower() in w[1].lower():
                target = w
                break
    return focus_hwnd(target[0])


def capture(hwnd, max_w=520, max_h=330):
    """Screenshot a window (even when covered by other windows) via PrintWindow. Returns PNG bytes or None."""
    import win32gui
    import win32ui
    from PIL import Image
    import io
    try:
        if win32gui.IsIconic(hwnd):
            return None
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h = r - l, b - t
        if w < 50 or h < 50:
            return None
        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        ok = user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        data = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), data, "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
        if not ok:
            return None
        # trim the 7-8px invisible resize borders Windows 10/11 add around the window
        img = img.crop((8, 0, max(9, w - 8), max(9, h - 8)))
        img.thumbnail((max_w, max_h))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception as e:
        log(f"capture {hwnd}: {e}")
        return None
