"""Installs the bridge extension into a Chrome profile by driving chrome://extensions (Developer mode -> Load unpacked)."""
import time

from . import chrome
from .config import log


def install_bridge(profile, ext_dir):
    import win32gui
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys

    chrome.launch(profile.directory, "about:blank", new_window=True)
    hwnd = None
    for _ in range(30):
        time.sleep(0.5)
        for h, title, a, _m in chrome.windows_for(profile):
            if title.startswith("about:blank"):
                hwnd = h
                break
        if hwnd:
            break
    if not hwnd:
        return False, "Chrome window nahi khula"
    chrome.focus_hwnd(hwnd)
    time.sleep(0.8)
    send_keys("^l")
    time.sleep(0.3)
    send_keys("chrome://extensions/{ENTER}")
    time.sleep(3)
    w = Desktop(backend="uia").window(handle=hwnd)
    dm = [b for b in w.descendants(control_type="Button") if b.element_info.name == "Developer mode"]
    if dm:
        try:
            if dm[0].get_toggle_state() == 0:
                dm[0].toggle()
                time.sleep(1.5)
        except Exception:
            dm[0].click_input()
            time.sleep(1.5)
    lu = [b for b in w.descendants(control_type="Button") if b.element_info.name == "Load unpacked"]
    if not lu:
        return False, "Load unpacked button nahi mila"
    lu[0].click_input()
    dlg = None
    for _ in range(20):
        time.sleep(0.5)
        hs = []
        win32gui.EnumWindows(lambda h, _x: hs.append(h) if win32gui.GetClassName(h) == "#32770" and "extension directory" in win32gui.GetWindowText(h) else None, None)
        if hs:
            dlg = Desktop(backend="uia").window(handle=hs[0])
            break
    if not dlg:
        return False, "Folder dialog nahi khula"
    ed = [e for e in dlg.descendants(control_type="Edit") if e.element_info.name == "Folder:"]
    ed[0].set_edit_text(ext_dir)
    time.sleep(0.4)
    [b for b in dlg.descendants(control_type="Button") if b.window_text() == "Select Folder"][0].click_input()
    time.sleep(2.5)
    log(f"bridge installed into {profile.directory} from {ext_dir}")
    try:
        send_keys("^w")  # close the extensions tab
    except Exception:
        pass
    return True, "Bridge install ho gaya"
