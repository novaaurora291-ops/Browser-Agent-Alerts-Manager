"""Installs the bridge extension into a browser profile through its own chrome://extensions page
(Developer mode -> Load unpacked -> folder), driven only by UI Automation patterns and window messages:
no real mouse cursor, no keystrokes to whatever window is in front."""
import os
import time

from . import chrome
from .config import log


def _windows(p):
    return chrome.managed_windows([p.data_dir]).get(chrome.norm_dir(p.data_dir), [])


def _open_extensions_page(p, exe):
    import subprocess
    args = [exe, f"--user-data-dir={p.data_dir}", "--no-first-run", "about:blank"]
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 4          # SW_SHOWNOACTIVATE: do not take the user's foreground window
    subprocess.Popen(args, creationflags=0x00000008 | 0x00000200, close_fds=True, startupinfo=si)


def _find_ext_window(p, timeout=20):
    """Browsers refuse chrome:// URLs from the command line, so the new blank tab is sent there through its
    own address bar (UIA value + Enter posted to that window only)."""
    import win32gui
    import win32con
    from pywinauto import Desktop
    end = time.time() + timeout
    while time.time() < end:
        for h, title, *_ in _windows(p):
            t = title.lower()
            if t.startswith("extensions"):
                return h
            if t.startswith("about:blank") or t.startswith("new tab"):
                try:
                    w = Desktop(backend="uia").window(handle=h)
                    ed = [e for e in w.descendants(control_type="Edit") if "address" in (e.element_info.name or "").lower()]
                    if ed:
                        ed[0].iface_value.SetValue("chrome://extensions/")
                        time.sleep(0.3)
                        win32gui.PostMessage(h, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0x001C0001)
                        win32gui.PostMessage(h, win32con.WM_KEYUP, win32con.VK_RETURN, 0xC01C0001)
                        time.sleep(2)
                        if win32gui.GetWindowText(h).lower().startswith("extensions"):
                            return h
                except Exception as e:
                    log(f"bridge install: address bar {e}")
        time.sleep(0.5)
    return None


def _close_ext_tab(w):
    """Close the Extensions tab we opened (its tab's own close button, via UIA invoke)."""
    try:
        for t in w.descendants(control_type="TabItem"):
            if (t.element_info.name or "").lower().startswith("extensions"):
                for b in t.descendants(control_type="Button"):
                    if "close" in (b.element_info.name or "").lower():
                        b.invoke()
                        return True
    except Exception as e:
        log(f"bridge install: close tab {e}")
    return False


def install_bridge(p, ext_dir, exe=None):
    """Load the bridge folder into profile p. Returns (ok, message). Caller verifies the bridge reports in."""
    import pythoncom
    import win32gui
    pythoncom.CoInitialize()
    if not os.path.isfile(os.path.join(ext_dir, "manifest.json")):
        return False, "Bridge folder nahi mila"
    fg = win32gui.GetForegroundWindow()
    try:
        return _install(p, ext_dir, exe)
    finally:
        try:      # give the user's window back if the browser took the front
            if fg and win32gui.GetForegroundWindow() != fg:
                win32gui.SetForegroundWindow(fg)
        except Exception:
            pass


def _install(p, ext_dir, exe):
    import win32gui
    from pywinauto import Desktop
    _open_extensions_page(p, exe)
    hwnd = _find_ext_window(p)
    if not hwnd:
        return False, "Extensions page nahi khula"
    w = Desktop(backend="uia").window(handle=hwnd)
    lu = []
    for _ in range(20):
        time.sleep(0.6)
        btns = w.descendants(control_type="Button")
        dm = [b for b in btns if b.element_info.name == "Developer mode"]
        if dm:
            try:
                if dm[0].get_toggle_state() == 0:
                    dm[0].toggle()
                    time.sleep(1.2)
            except Exception as e:
                log(f"bridge install: dev mode {e}")
        lu = [b for b in w.descendants(control_type="Button") if b.element_info.name == "Load unpacked"]
        if lu:
            break
    if not lu:
        return False, "Load unpacked button nahi mila"
    lu[0].invoke()
    dlg = None
    for _ in range(24):
        time.sleep(0.5)
        hs = []
        win32gui.EnumWindows(lambda h, _x: hs.append(h) if win32gui.GetClassName(h) == "#32770" and "extension directory" in win32gui.GetWindowText(h).lower() else None, None)
        if hs:
            dlg = Desktop(backend="uia").window(handle=hs[0])
            break
    if not dlg:
        return False, "Folder dialog nahi khula"
    ed = [e for e in dlg.descendants(control_type="Edit") if (e.element_info.name or "").startswith("Folder")]
    if not ed:
        return False, "Folder box nahi mila"
    ed[0].iface_value.SetValue(ext_dir)
    time.sleep(0.4)
    sel = [b for b in dlg.descendants(control_type="Button") if (b.element_info.name or "") == "Select Folder"]
    if not sel:
        return False, "Select Folder button nahi mila"
    sel[0].invoke()
    time.sleep(2.5)
    # an error (bad manifest etc.) shows on the page; otherwise the card appears
    err = [t for t in w.descendants(control_type="Text") if "failed to load extension" in (t.element_info.name or "").lower()]
    if err:
        return False, "Extension load fail hua"
    log(f"bridge loaded unpacked into {p.data_dir} from {ext_dir}")
    _close_ext_tab(w)
    return True, "Load unpacked ho gaya"
