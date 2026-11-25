"""Cent Browser profiles managed by this tool: Cent exe, windows (matched by full exe path + user-data-dir),
launch, focus, live previews, docking (SetParent) and taskbar buttons.

Only Cent Browser is supported. Google Chrome, Edge and Brave are never scanned, launched or touched: a window
counts as ours only when its process runs the configured Cent chrome.exe (full path) with one of our profiles'
--user-data-dir folders, so a personal Chrome window (also chrome.exe) is never matched."""
import json
import os
import subprocess
import ctypes
from ctypes import wintypes

from .config import log

user32 = ctypes.windll.user32

CENT_EXE = ""      # the configured Cent chrome.exe (set by the controller); windows of any other exe are ignored


def set_cent_exe(exe):
    global CENT_EXE
    CENT_EXE = norm_path(exe) if exe else ""


def norm_path(p):
    return os.path.normcase(os.path.abspath(p)) if p else ""


class Profile:
    """A Cent Browser profile with its own --user-data-dir folder."""

    def __init__(self, directory, name, email="", gaia="", avatar=""):
        self.directory = directory
        self.chrome_name = name
        self.email = email
        self.gaia = gaia
        self.avatar = avatar
        self.alias = ""
        self.kind = "managed"
        self.data_dir = ""
        self.exe = ""
        self.tag = ""

    @property
    def managed(self):
        return True

    @property
    def browser_name(self):
        return "Cent Browser"

    @property
    def name(self):
        return self.alias or self.chrome_name or self.directory


# ---------------- managed profiles (own --user-data-dir, Cent Browser only) ----------------
@__import__("functools").lru_cache(maxsize=64)
def browser_name(exe):
    """Product name of a browser exe (e.g. 'Cent Browser'), from its version info; falls back to the file name."""
    if not exe:
        return ""
    try:
        import win32api
        lang, cp = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")[0]
        n = win32api.GetFileVersionInfo(exe, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\ProductName")
        if n:
            return str(n).strip()
    except Exception:
        pass
    return os.path.splitext(os.path.basename(exe))[0]


def is_cent_exe(exe):
    """A Cent Browser chrome.exe (product name or its install folder says Cent)."""
    if not exe or not os.path.isfile(exe) or not exe.lower().endswith(".exe"):
        return False
    return "cent" in browser_name(exe).lower() or "centbrowser" in norm_path(exe).replace(" ", "")


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


def is_cent_process_exe(pexe):
    """A process exe (full path) is the configured Cent chrome.exe. Any other chrome.exe (personal Google Chrome)
    never counts."""
    return bool(CENT_EXE) and norm_path(pexe) == CENT_EXE


def _profile_running(data_dir):
    """True while the configured Cent exe runs with this --user-data-dir (it would overwrite the name on exit)."""
    want = norm_dir(data_dir)
    try:
        import psutil
        for pr in psutil.process_iter(["name", "cmdline", "exe"]):
            try:
                if (pr.info["name"] or "").lower() != "chrome.exe" or not is_cent_process_exe(pr.info.get("exe") or ""):
                    continue
                for a in pr.info["cmdline"] or []:
                    if a.startswith("--user-data-dir=") and norm_dir(a.split("=", 1)[1].strip('"')) == want:
                        return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _rw_json(path, fn):
    """Read a JSON file (missing = {}), let fn change it, write it back atomically when it changed."""
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    before = json.dumps(data, sort_keys=True)
    fn(data)
    if json.dumps(data, sort_keys=True) == before:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".hbm.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)
    return True


def set_profile_name(data_dir, name):
    """Make `name` Cent Browser's own profile name for this user-data-dir (avatar menu, window title, profile
    picker). Written to Local State (info_cache) and Default/Preferences while the browser is closed, so the
    first launch and every reopen show it. Returns True when something was written."""
    name = (name or "").strip()
    if not data_dir or not name:
        return False
    if _profile_running(data_dir):
        log(f"profile name '{name}': Cent is running for {data_dir}, will set it at the next launch")
        return False

    def local_state(d):
        prof = d.setdefault("profile", {})
        ent = prof.setdefault("info_cache", {}).setdefault("Default", {})
        ent["name"] = name
        ent["shortcut_name"] = name
        ent["is_using_default_name"] = False
        prof.setdefault("last_used", "Default")
        if "Default" not in (prof.get("last_active_profiles") or []):
            prof["last_active_profiles"] = ["Default"]

    def prefs(d):
        prof = d.setdefault("profile", {})
        prof["name"] = name
        prof["using_default_name"] = False

    a = _rw_json(os.path.join(data_dir, "Local State"), local_state)
    b = _rw_json(os.path.join(data_dir, "Default", "Preferences"), prefs)
    if a or b:
        log(f"profile name set to '{name}' in {data_dir}")
    return a or b


def norm_dir(d):
    return os.path.normcase(os.path.abspath(d)).rstrip("\\/") if d else ""


def launch_managed(p, exe, url=None, new_window=False, ext_dir=None, extra=None, minimized=False):
    if not exe or not is_cent_exe(exe) or (CENT_EXE and norm_path(exe) != CENT_EXE):
        log(f"launch {p.tag}: refused, not the configured Cent Browser exe ({exe})")
        raise RuntimeError("Cent Browser exe set nahi / ghalat (Settings dekhein)")
    if not p.data_dir:
        raise RuntimeError("profile data folder missing")
    os.makedirs(p.data_dir, exist_ok=True)
    args = [exe, f"--user-data-dir={p.data_dir}", "--no-first-run", "--no-default-browser-check"] + list(extra or [])
    # docked windows sit inside the Manager, often out of sight: keep them rendering and their chats live
    for f in ("--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
              "--disable-features=CalculateNativeWinOcclusion"):
        if f not in args:
            args.insert(1, f)
    if ext_dir and os.path.isdir(ext_dir):
        args.append(f"--load-extension={ext_dir}")
    if new_window:
        args.append("--new-window")
    if url:
        args.append(url)
    DETACHED = 0x00000008 | 0x00000200
    si = None
    if any(a.startswith("--remote-debugging-port=") for a in (extra or [])):
        # background setup launch: open the window without taking focus from the dashboard
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 4            # SW_SHOWNOACTIVATE
    elif minimized:
        # it is docked into the Manager right away: open it minimised and without taking focus
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7            # SW_SHOWMINNOACTIVE
    subprocess.Popen(args, creationflags=DETACHED, close_fds=True, startupinfo=si)
    log(f"launch managed {p.tag} via {os.path.basename(os.path.dirname(os.path.dirname(exe)))} -> {p.data_dir}")


_proc_cache = {}


def _proc_info(pid):
    """(full exe path, --user-data-dir) of a browser process, both normalized (cached per pid)."""
    if pid in _proc_cache:
        return _proc_cache[pid]
    info = ("", None)
    try:
        import psutil
        pr = psutil.Process(pid)
        udd = None
        for a in pr.cmdline():
            if a.startswith("--user-data-dir="):
                udd = norm_dir(a.split("=", 1)[1].strip('"'))
                break
        info = (norm_path(pr.exe()), udd)
    except Exception:
        info = ("", None)
    if len(_proc_cache) > 500:
        _proc_cache.clear()
    _proc_cache[pid] = info
    return info


def _proc_udd(pid):
    return _proc_info(pid)[1]


def managed_windows(dirs, exe=None):
    """Visible top-level Cent windows whose process is the configured Cent exe (full path) running with one of
    our --user-data-dir folders. Anything else (personal Chrome, Edge, Brave, Cent's own default profile) is
    never listed. Returns {normalized dir: [(hwnd, title, "", minimized)]}."""
    import win32gui
    import win32process
    want = {norm_dir(d) for d in dirs if d}
    exe = norm_path(exe) if exe else CENT_EXE
    out = {}
    if not want or not exe:
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
            pexe, udd = _proc_info(pid)
            if udd in want and pexe == exe:
                out.setdefault(udd, []).append((h, title, "", bool(win32gui.IsIconic(h))))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def window_profile_ok(hwnd, data_dir, exe=None):
    """Re-check one window right before docking it: Cent exe path and the profile's user-data-dir."""
    try:
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        pexe, udd = _proc_info(pid)
        return pexe == (norm_path(exe) if exe else CENT_EXE) and udd == norm_dir(data_dir)
    except Exception:
        return False


# ---------------- windows ----------------
def windows_for(profile, wins=None, mwins=None):
    mwins = managed_windows([profile.data_dir]) if mwins is None else mwins
    return mwins.get(norm_dir(profile.data_dir), [])


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
    wins = [w for w in windows_for(profile) if window_profile_ok(w[0], profile.data_dir)]
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
        if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return None           # docked out of sight (parked): it would print what lies on top of it; keep the last preview
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
        bw, bh = info["bmWidth"], info["bmHeight"]
        # a bitmap smaller than expected (window resized / maximised meanwhile) used to be read past its end
        # (frombuffer does not check the size): copy with frombytes only when the size is exactly right
        img = Image.frombytes("RGB", (bw, bh), data, "raw", "BGRX", 0, 1) if bw > 0 and bh > 0 and len(data) == bw * bh * 4 else None
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
        if not ok or img is None:
            return None
        if max(hi for _, hi in img.getextrema()) < 10:
            return None           # a docked window that is not on screen prints black: keep the last preview
        # trim the 7-8px invisible resize borders Windows 10/11 add around the window
        img = img.crop((8, 0, max(9, w - 8), max(9, h - 8)))
        img.thumbnail((max_w, max_h))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception as e:
        log(f"capture {hwnd}: {e}")
        return None


# ---------- show a profile's real browser window inside the Manager ----------
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
WS_EX_APPWINDOW = 0x00040000

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.GetFocus.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetActiveWindow.restype = wintypes.HWND
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO)]


# ---------- taskbar buttons (ITaskbarList, plain COM through ctypes) ----------
class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    def __init__(self, s):
        import uuid
        u = uuid.UUID(s)
        super().__init__(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


_tbl = None


def _taskbar():
    global _tbl
    if _tbl is None:
        ole = ctypes.windll.ole32
        ole.CoInitialize(None)
        ptr = ctypes.c_void_p()
        hr = ole.CoCreateInstance(ctypes.byref(_GUID("56FDF344-FD6D-11d0-958A-006097C9A090")), None, 1,
                                  ctypes.byref(_GUID("56FDF342-FD6D-11d0-958A-006097C9A090")), ctypes.byref(ptr))
        if hr != 0 or not ptr.value:
            raise OSError(f"ITaskbarList: {hr:#x}")
        vt = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND)
        init = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vt[3])
        init(ptr)
        _tbl = (ptr, proto(vt[4]), proto(vt[5]))      # (this, AddTab, DeleteTab)
    return _tbl


def taskbar_button(hwnd, show):
    """Add or remove a window's own button on the Windows taskbar."""
    try:
        this, add, delete = _taskbar()
        (add if show else delete)(this, hwnd)
        return True
    except Exception as e:
        log(f"taskbar {'add' if show else 'delete'} {hwnd}: {e}")
        return False


def is_window(hwnd):
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def embed(hwnd, parent):
    """Dock a Cent window into one of our widgets (Win32 SetParent). The browser keeps running as itself (same
    process, same profile, same signed-in session); we only host its window. While docked it has no taskbar
    button of its own. Returns the state unembed() needs; on any failure the window is put back as it was."""
    import win32gui
    import win32con
    place = win32gui.GetWindowPlacement(hwnd)     # (flags, showCmd, minpos, maxpos, normal rect): restored on unembed
    l, t, r, b = place[4]
    st = {"style": user32.GetWindowLongPtrW(hwnd, GWL_STYLE), "ex": user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE),
          "rect": win32gui.GetWindowRect(hwnd) if place[1] == win32con.SW_SHOWNORMAL else (l, t, r, b), "place": place}
    try:
        # the browser must not think it is minimised/maximised while docked (it would paint white / ignore size)
        if place[1] == win32con.SW_SHOWMINIMIZED or win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        elif place[1] == win32con.SW_SHOWMAXIMIZED:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # hidden while it changes owner: the shell drops its taskbar button
        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        style = (st["style"] & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
                                 | 0x20000000 | 0x01000000)) | WS_CHILD       # no WS_MINIMIZE / WS_MAXIMIZE either
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, st["ex"] & ~WS_EX_APPWINDOW)
        if not user32.SetParent(hwnd, parent) or user32.GetParent(hwnd) != parent:
            raise OSError(f"SetParent failed ({ctypes.GetLastError()})")
        taskbar_button(hwnd, False)
        win32gui.SetWindowPos(hwnd, 0, 0, 0, max(200, r - l), max(150, b - t),
                              win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW)
    except Exception:
        unembed(hwnd, st)           # docking failed: the window goes back exactly as it was, taskbar button too
        raise
    return st


def embed_resize(hwnd, w, h):
    import win32gui
    try:
        win32gui.MoveWindow(hwnd, 0, 0, max(1, w), max(1, h), True)
    except Exception:
        pass


def unembed(hwnd, st):
    """Give the window back to the desktop exactly as it was."""
    import win32gui
    import win32con
    if not is_window(hwnd):
        return
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        user32.SetParent(hwnd, None)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, st["style"])
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, st["ex"])
        l, t, r, b = st["rect"]
        win32gui.SetWindowPos(hwnd, 0, l, t, r - l, b - t, win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW)
        pl = st.get("place")
        if pl:
            # back to the size it had and to maximised / minimised when it was
            cmd = pl[1] if pl[1] in (win32con.SW_SHOWMAXIMIZED, win32con.SW_SHOWMINIMIZED) else win32con.SW_SHOWNORMAL
            if cmd == win32con.SW_SHOWMINIMIZED:
                cmd = win32con.SW_SHOWMINNOACTIVE
            win32gui.SetWindowPlacement(hwnd, (pl[0], cmd, pl[2], pl[3], pl[4]))
        taskbar_button(hwnd, True)      # its own taskbar button back
    except Exception as e:
        log(f"unembed {hwnd}: {e}")


def window_title(hwnd):
    import win32gui
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def _is_bridge_dir(path):
    """Any unpacked folder whose manifest is the Hark bridge (repo's extension\\, bridge_extension\\, _hbm_bridge\\)."""
    import json
    try:
        with open(os.path.join(path, "manifest.json"), encoding="utf-8") as f:
            return json.load(f).get("name") == "Hark Browser Manager Bridge"
    except Exception:
        return False


def _ext_records(prefs_dir):
    """Bridge extension entries in a profile's Preferences (read only)."""
    import json
    out = []
    for fn in ("Preferences", "Secure Preferences"):
        fp = os.path.join(prefs_dir, fn)
        try:
            with open(fp, encoding="utf-8") as f:
                st = ((json.load(f).get("extensions") or {}).get("settings") or {})
        except Exception:
            continue
        for k, v in st.items():
            raw = (v.get("path") or "").replace("/", "\\").rstrip("\\")
            path = raw.lower()
            if path.endswith("\\_hbm_bridge") or path.endswith("\\bridge_extension") or (v.get("location") == 4 and _is_bridge_dir(raw)):
                # newer Chrome drops "state"; disabled shows as disable_reasons
                dr = v.get("disable_reasons")
                enabled = v.get("state", 1) == 1 and not (dr if isinstance(dr, int) else (dr or []))
                out.append({"id": k, "location": v.get("location"), "state": 1 if enabled else 0, "path": path})
    return out


def _proc_has_bridge_flag(p):
    """True if the running browser for this profile was started with --load-extension=<bridge folder>."""
    try:
        import psutil
        want = norm_dir(p.data_dir)
        for pr in psutil.process_iter(["name", "cmdline", "exe"]):
            cl = pr.info.get("cmdline") or []
            if not cl or any(a.startswith("--type=") for a in cl):
                continue
            if not is_cent_process_exe(pr.info.get("exe") or ""):
                continue                 # personal Chrome / any other browser is never looked at
            if want and not any(a.startswith("--user-data-dir=") and norm_dir(a.split("=", 1)[1].strip('"')) == want for a in cl):
                continue
            if not want:
                continue
            if any(a.startswith("--load-extension=") and ("_hbm_bridge" in a or "bridge_extension" in a) for a in cl):
                return True
    except Exception:
        pass
    return False


def bridge_installed(p, running):
    """Is the Hark bridge in this profile? Unpacked (persistent) record always counts; a command-line copy
    counts only if the running browser actually carries the flag (or, closed + managed, the Manager adds it at launch)."""
    prefs = os.path.join(p.data_dir, "Default")
    recs = [r for r in _ext_records(prefs) if r["state"] == 1]
    if any(r["location"] == 4 for r in recs):
        return True
    if running:
        return _proc_has_bridge_flag(p)
    return os.path.isfile(os.path.join(p.data_dir, "_hbm_bridge", "manifest.json"))


def embedded_child_at(parent, x, y):
    """The docked Cent window (a direct child of one of our host widgets) that contains screen point x, y."""
    import win32gui
    GW_CHILD, GW_HWNDNEXT = 5, 2
    c = user32.GetWindow(parent, GW_CHILD)
    while c:
        try:
            if user32.IsWindowVisible(c) and win32gui.GetClassName(c) == "Chrome_WidgetWin_1":
                l, t, r, b = win32gui.GetWindowRect(c)
                if l <= x < r and t <= y < b:
                    return int(c)
        except Exception:
            pass
        c = user32.GetWindow(c, GW_HWNDNEXT)
    return None


def embedded_window_from_point(x, y, root):
    """The docked Cent window (child of one of our hosts, inside top-level root) under screen point x, y."""
    import win32gui
    w = user32.WindowFromPoint(wintypes.POINT(x, y))
    while w:
        par = user32.GetParent(w)
        try:
            if par and win32gui.GetClassName(w) == "Chrome_WidgetWin_1" and win32gui.GetClassName(par) != "Chrome_WidgetWin_1" \
                    and user32.GetAncestor(w, 2) == root:
                return int(w)
        except Exception:
            return None
        w = par
    return None


def _thread_focus(tid):
    gi = _GUITHREADINFO()
    gi.cbSize = ctypes.sizeof(_GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gi)):
        return None
    return int(gi.hwndFocus or 0)


def is_activatable(hwnd):
    """A docked window that can be the active window (hosted without WS_CHILD, see set_activatable)."""
    st = user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    return bool(st) and not (st & WS_CHILD)


def set_activatable(hwnd, on):
    """Address / search bar in a Live Multi-View pane. A WS_CHILD window can never be the active window, and
    Chrome's own UI (omnibox focus on a click, Ctrl+L / Alt+D / F6, typing and Ctrl+V into the omnibox) only works
    while GetActiveWindow() is the browser window itself: clicking the bar or pressing Ctrl+L did nothing (only
    typing into the page worked). on=True keeps the window parented to its pane (it moves, scrolls and is clipped
    with the pane, no taskbar button) but drops WS_CHILD, so a click in it activates THAT browser window like a
    normal window and every other pane, and the Manager, become inactive. on=False: a plain child window again."""
    import win32gui
    import win32con
    if not is_window(hwnd):
        return False
    st = user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    new = ((st & ~WS_CHILD) | WS_POPUP) if on else ((st & ~WS_POPUP) | WS_CHILD)
    if new != st:
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, new)
        win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                              | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED)
    return True


def foreground():
    return int(user32.GetForegroundWindow() or 0)


def press_ctrl(key):
    """Ctrl+<key> to whatever window has the keyboard now (SendInput through keybd_event)."""
    VK_CONTROL, KEYUP = 0x11, 0x0002
    vk = ord(key.upper())
    ke = ctypes.windll.user32.keybd_event
    ke(VK_CONTROL, 0, 0, 0)
    ke(vk, 0, 0, 0)
    ke(vk, 0, KEYUP, 0)
    ke(VK_CONTROL, 0, KEYUP, 0)


def focus_embedded(hwnd):
    """Give the keyboard focus to a docked Cent window. A docked window is a WS_CHILD of our (Qt) window in
    another process: a click in it never moves the Win32 focus there (Chrome does not SetFocus itself, it waits
    for WM_ACTIVATE, which child windows never get), so the keys kept going to the Qt top-level. Only done while
    our window is the foreground window. True when the Cent window (or one of its children) has the focus."""
    if not is_window(hwnd):
        return False
    root = user32.GetAncestor(hwnd, 2)            # GA_ROOT: our window that hosts it
    fg = user32.GetForegroundWindow()
    # ours in the foreground: the hosting window itself, or one of its activatable docked browser windows
    if not root or not fg or (fg != root and user32.GetAncestor(fg, 2) != root):
        return False
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    if is_activatable(hwnd):
        # a Multi-View pane: make THIS browser window the active one (its omnibox, Ctrl+L and typing then work)
        if fg == hwnd:
            return True
        user32.SetActiveWindow(hwnd)
        if user32.GetForegroundWindow() != hwnd:
            me = ctypes.windll.kernel32.GetCurrentThreadId()
            try:
                if user32.AttachThreadInput(me, tid, True):
                    user32.SetActiveWindow(hwnd)
                    user32.AttachThreadInput(me, tid, False)
            except Exception as e:
                log(f"focus_embedded {hwnd}: {e}")
        ok = user32.GetForegroundWindow() == hwnd
        if not ok:
            log(f"focus_embedded {hwnd}: the pane's browser window did not become active")
        return ok

    def has_focus():
        f = _thread_focus(tid)
        return bool(f) and (f == hwnd or bool(user32.IsChild(hwnd, f)))

    if has_focus():
        return True
    user32.SetFocus(hwnd)          # works directly: SetParent attached our input queue to the browser's thread
    if has_focus():
        return True
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    try:
        if user32.AttachThreadInput(me, tid, True):
            user32.SetFocus(hwnd)
            user32.AttachThreadInput(me, tid, False)
    except Exception as e:
        log(f"focus_embedded {hwnd}: {e}")
    ok = has_focus()
    if not ok:
        log(f"focus_embedded {hwnd}: the browser window did not take the keyboard focus")
    return ok


_clipped = {}     # popup hwnd -> rect (window coords) it is clipped to, or () for fully hidden


def owned_popups(owners):
    """{owner hwnd: [visible top-level popup hwnds it owns]} for docked Cent windows: Chrome's own bubbles
    ('Restore pages?', permission prompts, menus) are separate top-level windows owned by the docked window."""
    import win32gui
    owners = set(int(h) for h in owners if h)
    out = {h: [] for h in owners}
    if not owners:
        return out

    def cb(w, _):
        try:
            o = int(user32.GetWindow(w, 4) or 0)          # GW_OWNER
            if o in owners and user32.IsWindowVisible(w):
                out[o].append(int(w))
        except Exception:
            pass
        return True
    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return out


def notify_moved(hwnd):
    """A docked window moved on screen together with its host (e.g. the grid scrolled): its position relative to
    the host did not change, so the browser was not told. WM_MOVE makes it re-anchor its bubbles to where it is."""
    try:
        user32.PostMessageW(hwnd, 0x0003, 0, 0)
    except Exception:
        pass


def clip_popups(popups, vis):
    """Clip the docked window's popups to vis (screen rect l, t, r, b of the pane area that is on screen; None when
    none is), so no bubble floats over the Manager's header bar, the Grid scrollbar or another pane."""
    import win32gui
    for w in popups:
        try:
            l, t, r, b = win32gui.GetWindowRect(w)
        except Exception:
            continue
        if vis and l >= vis[0] and t >= vis[1] and r <= vis[2] and b <= vis[3]:
            if w in _clipped:
                user32.SetWindowRgn(w, None, True)
                _clipped.pop(w, None)
            continue
        if vis:
            cl, ct, cr, cb_ = max(l, vis[0]) - l, max(t, vis[1]) - t, min(r, vis[2]) - l, min(b, vis[3]) - t
            rect = (cl, ct, cr, cb_) if cr > cl and cb_ > ct else ()
        else:
            rect = ()
        if _clipped.get(w) == rect:
            continue
        gdi = ctypes.windll.gdi32
        gdi.CreateRectRgn.restype = ctypes.c_void_p
        rgn = gdi.CreateRectRgn(*(rect or (0, 0, 0, 0)))
        if rgn and user32.SetWindowRgn(w, ctypes.c_void_p(rgn), True):
            _clipped[w] = rect          # the system owns the region now
        elif rgn:
            gdi.DeleteObject(ctypes.c_void_p(rgn))


def unclip_popups(popups=None):
    """Give popups their whole area back (the window left the grid). None: every popup clipped so far."""
    for w in list(_clipped) if popups is None else popups:
        if w in _clipped:
            try:
                if is_window(w):
                    user32.SetWindowRgn(w, None, True)
            except Exception:
                pass
            _clipped.pop(w, None)


def reparent(hwnd, parent):
    """Move an already hosted browser window from one of our host widgets to another. It always arrives as a plain
    child window (WS_CHILD); a Live Multi-View pane makes its own window activatable again (set_activatable)."""
    import win32gui
    import win32con
    set_activatable(hwnd, False)
    user32.SetParent(hwnd, parent)
    win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)
