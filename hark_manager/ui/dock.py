"""Docking of Cent Browser windows inside the Manager.

Every Cent window of a managed profile lives inside the Manager (Win32 SetParent), with no desktop window and no
taskbar button of its own:
  - shown in the single live view (Back to profiles hides the view; the window stays docked and running),
  - shown in a Live Multi-View pane,
  - or parked in a hidden host inside the main window (browser keeps running, chats keep going).

Each window remembers how it was on the desktop the first time it was docked. When it is given back (Multi-View
Close with "Keep Cent windows docked" off, a docking failure, or quitting the Manager) it returns to that
position, size, minimised/maximised state and gets its taskbar button back."""
import time

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication

from .browser_view import Host
from .. import chrome
from ..config import log


class Dock(QObject):
    PARK_W, PARK_H = 1280, 860

    def __init__(self, ctl, owner):
        super().__init__(owner)
        self.ctl = ctl
        # parked windows live in an off-screen, frameless tool window: no taskbar button, not in Alt+Tab, never on
        # the visible desktop, but still "shown", so the browser keeps rendering (live previews, no throttling) and
        # its own bubbles/popups open off-screen with it instead of on the user's desktop
        self.park = Host()
        self.park.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.park.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.park.setWindowTitle("Hark Browser Manager park")
        self.park.setMinimumSize(1, 1)
        vg = QApplication.primaryScreen().virtualGeometry()
        self.park.setGeometry(vg.left() - self.PARK_W - 3000, vg.top() - self.PARK_H - 3000, self.PARK_W, self.PARK_H)
        self.park.show()
        self.states = {}      # hwnd -> desktop state from chrome.embed (restored by undock)
        self.where = {}       # hwnd -> "park" | "view" | "grid"
        self.dirs = {}        # hwnd -> profile dir
        self.origin = {}      # hwnd -> "desktop" (first docked by the Multi-View from the desktop) | "dock"
        self.released = {}    # hwnd -> time given back to the desktop on purpose: auto-docking leaves it there
        self.mv_open = lambda: False
        self.t = QTimer(self, interval=1500, timeout=self.auto)
        self.t.start()

    @property
    def park_id(self):
        return int(self.park.winId())

    # ---------- core moves ----------
    def has(self, hwnd):
        return hwnd in self.states

    def dock(self, d, hwnd, host_id, where, origin="dock"):
        """Dock (or move) hwnd into host_id. False when it is not this profile's Cent window or docking failed;
        in that case the window is left / put back on the desktop as it was."""
        if hwnd in self.states:
            if not chrome.is_window(hwnd):
                self.forget(hwnd)
                return False
            chrome.reparent(hwnd, host_id)
        else:
            p = self.ctl.profile(d)
            if not p or not chrome.window_profile_ok(hwnd, p.data_dir):
                log(f"dock {d}: hwnd={hwnd} is not this profile's Cent window, left alone")
                return False
            try:
                self.states[hwnd] = chrome.embed(hwnd, host_id)
            except Exception as e:
                log(f"dock {d} hwnd={hwnd} failed, window restored to the desktop: {e}")
                self.released[hwnd] = time.time()
                return False
            self.origin[hwnd] = origin
            log(f"docked {d} hwnd={hwnd} ({where}, from desktop, taskbar button removed)")
        self.where[hwnd] = where
        self.dirs[hwnd] = d
        self.released.pop(hwnd, None)
        if self.ctl.embedded.get(d) != hwnd and (d not in self.ctl.embedded or not chrome.is_window(self.ctl.embedded[d])):
            self.ctl.embedded[d] = hwnd
        return True

    def park_window(self, hwnd):
        """Keep it docked and running, out of sight (no desktop window, no taskbar button)."""
        if hwnd not in self.states or not chrome.is_window(hwnd):
            self.forget(hwnd)
            return
        chrome.reparent(hwnd, self.park_id)
        chrome.embed_resize(hwnd, self.PARK_W, self.PARK_H)
        self.where[hwnd] = "park"
        self.origin[hwnd] = "dock"

    def undock(self, hwnd):
        """Back to the desktop exactly as it was (position, size, state, taskbar button)."""
        st = self.states.pop(hwnd, None)
        d = self.dirs.pop(hwnd, None)
        self.where.pop(hwnd, None)
        self.origin.pop(hwnd, None)
        if st is not None and chrome.is_window(hwnd):
            chrome.unembed(hwnd, st)
            self.released[hwnd] = time.time()
            log(f"undocked {d} hwnd={hwnd} -> desktop (position/state/taskbar restored)")
        if d and self.ctl.embedded.get(d) == hwnd:
            self.ctl.embedded.pop(d, None)
            other = self.window_of(d)
            if other:
                self.ctl.embedded[d] = other
        QTimer.singleShot(300, self.ctl.refresh_windows)

    def forget(self, hwnd):
        d = self.dirs.pop(hwnd, None)
        self.states.pop(hwnd, None)
        self.where.pop(hwnd, None)
        self.origin.pop(hwnd, None)
        if d and self.ctl.embedded.get(d) == hwnd:
            self.ctl.embedded.pop(d, None)

    def give_back(self, hwnd):
        """Multi-View Close: a window goes back to where it was before the grid took it."""
        if not self.ctl.settings["keep_docked"]:
            self.undock(hwnd)
        else:
            self.park_window(hwnd)

    def window_of(self, d, where=None):
        for h, dd in self.dirs.items():
            if dd == d and (where is None or self.where.get(h) == where) and chrome.is_window(h):
                return h
        return None

    def release_all(self):
        """Quitting: every docked window back to the desktop (Windows would destroy it with the Manager)."""
        for h in list(self.states):
            self.undock(h)

    # ---------- keep every Cent window docked ----------
    def auto(self):
        for h in [h for h in self.states if not chrome.is_window(h)]:
            self.forget(h)
        if not self.ctl.settings["keep_docked"] or self.mv_open():
            return              # an open Multi-View docks new windows into its grid itself
        now = time.time()
        for p in self.ctl.profiles:
            if p.directory in self.ctl.no_dock:
                continue
            for w in self.ctl.windows.get(p.directory) or []:
                h = w[0]
                if h in self.states or h in self.released or not chrome.is_window(h):
                    continue
                if chrome.user32.GetParent(h):
                    continue
                if self.dock(p.directory, h, self.park_id, "park"):
                    chrome.embed_resize(h, self.PARK_W, self.PARK_H)
                    log(f"auto-docked {p.directory} hwnd={h} (hidden in the Manager)")
        for h, t in list(self.released.items()):
            if not chrome.is_window(h) or now - t > 24 * 3600:
                self.released.pop(h, None)
