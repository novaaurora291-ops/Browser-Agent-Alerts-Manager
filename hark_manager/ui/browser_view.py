"""The profile's own browser window shown inside the Manager (mock-up: Back to profiles / name / status / Expand).

Nothing is copied: the real Chrome / Cent window of that profile (its own process, data folder and signed-in
session) is re-parented into this view, so you read, scroll and reply in the live Hark chat right here.
Back to profiles only shows the dashboard: the window stays docked (hidden) inside the Manager and keeps
running, with no desktop window and no taskbar button."""
import time

from ctypes import wintypes

from PySide6.QtCore import Qt, Signal, QTimer, QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFrame

from .widgets import label, button, StatusLabel
from . import icons
from .. import chrome
from ..config import log


WM_ACTIVATE = 0x0006
WM_PARENTNOTIFY = 0x0210
_BUTTON_DOWN = {0x0201, 0x0204, 0x0207, 0x020B, 0x0246}   # L/R/M/X button down, WM_POINTERDOWN (touch, pen)


class EmbedFocus(QAbstractNativeEventFilter):
    """Keyboard focus for docked browser windows (single live view and Live Multi-View panes).

    A docked Cent window is a child window of another process inside our Qt window. Clicking in it (a Hark chat
    box, the address bar) sets the caret there, but Windows keeps the keyboard focus on the Qt top-level, so
    typing, Ctrl+V, Backspace and Enter went to the Manager instead of that browser. Every button press in a
    child window sends WM_PARENTNOTIFY up to our host widget: the browser window under the press gets the
    focus right after it. Listeners (the Multi-View) are told which window got it, and when one of our
    windows is activated by the keyboard or the taskbar (not by a click), so it can give the keys back."""
    _inst = None

    def __init__(self):
        super().__init__()
        self.listeners = []           # f(hwnd, ok): a docked browser window got (ok) the keyboard focus
        self.activated = {}           # our top-level hwnd -> f(): activated by Alt+Tab / taskbar / code

    @classmethod
    def install(cls):
        if cls._inst is None and QApplication.instance() is not None:
            cls._inst = EmbedFocus()
            QApplication.instance().installNativeEventFilter(cls._inst)
        return cls._inst

    def nativeEventFilter(self, etype, message):
        try:
            if etype == b"windows_generic_MSG" or etype == "windows_generic_MSG":
                m = wintypes.MSG.from_address(int(message))
                if m.message == WM_PARENTNOTIFY and (m.wParam & 0xFFFF) in _BUTTON_DOWN:
                    child = chrome.embedded_child_at(m.hWnd, m.pt.x, m.pt.y)
                    if child:
                        # right now, before the browser handles the press itself: it then puts its own focus on
                        # what was clicked (chat box, address bar) instead of restoring the page on WM_SETFOCUS
                        self.focus(child)
                elif m.message == WM_ACTIVATE and (m.wParam & 0xFFFF) == 1 and int(m.hWnd or 0) in self.activated:
                    # WA_ACTIVE (WA_CLICKACTIVE = 2 is left to the click): after Windows focused the window itself
                    QTimer.singleShot(0, self.activated[int(m.hWnd)])
                elif m.message == WM_ACTIVATE and (m.wParam & 0xFFFF) == 2 and int(m.hWnd or 0) in self.activated:
                    # activated by a click: when that click landed in a docked browser, its WM_PARENTNOTIFY came
                    # while our window was not in the foreground yet, so give that browser the keys now
                    root = int(m.hWnd)
                    QTimer.singleShot(0, lambda: self.focus_under_cursor(root))
        except Exception as e:
            log(f"embed focus filter: {e}")
        return False, 0

    def focus_under_cursor(self, root):
        import win32api
        try:
            x, y = win32api.GetCursorPos()
            child = chrome.embedded_window_from_point(x, y, root)
        except Exception:
            child = None
        if child:
            self.focus(child)

    def focus(self, hwnd):
        ok = chrome.focus_embedded(hwnd)
        for f in list(self.listeners):
            try:
                f(hwnd, ok)
            except Exception:
                pass


class Host(QWidget):
    """A native child window the browser window is parented to."""
    resized = Signal()

    def __init__(self):
        super().__init__()
        EmbedFocus.install()
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self.setMinimumSize(300, 200)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    def wheelEvent(self, e):
        # a wheel over the docked browser belongs to that browser only (its chat scrolls); anything the browser
        # passes back to its parent window must not scroll the Multi-View grid around it
        e.accept()


class BrowserView(QWidget):
    back = Signal()
    expand_toggled = Signal(bool)

    WAIT = 25  # seconds to wait for a just-launched window

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.dir = None
        self.hwnd = None
        self.dock = None          # set by the main window
        self.prefer = ""
        self.deadline = 0
        self.expanded = False
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(18)
        self.b_back = button("  Back to profiles", "outline", "back", 18)
        self.b_back.setMinimumHeight(42)
        self.b_back.setStyleSheet("font-size:16px; padding: 6px 18px;")
        self.b_back.clicked.connect(self.go_back)
        top.addWidget(self.b_back)
        sep = QFrame()
        sep.setFixedSize(1, 34)
        sep.setStyleSheet("background:#D5D9DE;")
        top.addWidget(sep)
        self.name = label("", size=26, bold=True)
        top.addWidget(self.name)
        self.status = StatusLabel("Idle")
        top.addWidget(self.status)
        top.addStretch(1)
        self.b_exp = button("  Expand", "outline", "expand", 18)
        self.b_exp.setMinimumHeight(42)
        self.b_exp.setStyleSheet("font-size:16px; padding: 6px 18px;")
        self.b_exp.clicked.connect(self.toggle_expand)
        top.addWidget(self.b_exp)
        v.addLayout(top)
        self.frame = QFrame()
        self.frame.setObjectName("bvFrame")
        self.frame.setStyleSheet("QFrame#bvFrame{background:#F3F4F5;border:1px solid #D5D9DE;border-radius:10px;}")
        fv = QVBoxLayout(self.frame)
        fv.setContentsMargins(1, 1, 1, 1)
        self.msg = label("", obj="muted", size=17)
        self.msg.setAlignment(Qt.AlignCenter)
        self.host = Host()
        self.host.resized.connect(self.fit)
        fv.addWidget(self.msg)
        fv.addWidget(self.host, 1)
        v.addWidget(self.frame, 1)
        self.tick = QTimer(self)
        self.tick.setInterval(700)
        self.tick.timeout.connect(self.poll)
        ctl.changed.connect(self.refresh_status)

    # ---------- open / close ----------
    def show_profile(self, directory, prefer=""):
        if self.dir and self.dir != directory:
            self.release()
        p = self.ctl.profile(directory)
        if not p:
            return
        self.dir = directory
        self.prefer = prefer
        self.name.setText(p.name)
        self.refresh_status()
        self.deadline = time.time() + self.WAIT
        if not self.hwnd:
            self.msg.setText(f"{p.name} ki window khul rahi hai…")
            self.msg.show()
        self.tick.start()
        QTimer.singleShot(0, self.poll)

    def find_window(self, p):
        h = self.dock.window_of(p.directory, "park") if self.dock else None
        if h:
            return h            # already docked in the Manager
        wins = [w for w in chrome.windows_for(p) if chrome.is_window(w[0]) and not (self.dock and self.dock.has(w[0]))]
        if not wins:
            return None
        if self.prefer:
            for w in wins:
                if self.prefer.lower() in w[1].lower():
                    return w[0]
        return wins[0][0]

    def attach(self, hwnd):
        if not self.dock.dock(self.dir, hwnd, int(self.host.winId()), "view"):
            self.msg.setText("Window dock nahi hui; wo apni jagah desktop par wapas hai.")
            self.tick.stop()
            return
        self.hwnd = hwnd
        self.msg.hide()
        self.fit()
        log(f"embedded {self.dir} hwnd={hwnd}")

    def release(self):
        """Leave the view: the window stays docked and running, parked out of sight inside the Manager."""
        if self.hwnd:
            self.dock.park_window(self.hwnd)
            log(f"parked {self.dir} hwnd={self.hwnd} (still running, docked)")
        self.hwnd = None
        self.tick.stop()

    def go_back(self):
        if self.expanded:
            self.toggle_expand()
        self.release()
        self.dir = None
        self.back.emit()

    def toggle_expand(self):
        self.expanded = not self.expanded
        self.b_exp.setText("  Collapse" if self.expanded else "  Expand")
        self.b_exp.setIcon(icons.icon("collapse" if self.expanded else "expand", 18))
        self.expand_toggled.emit(self.expanded)
        QTimer.singleShot(50, self.fit)

    # ---------- upkeep ----------
    def host_size(self):
        """The host's real size in device pixels (what the child browser window is measured in)."""
        import win32gui
        try:
            l, t, r, b = win32gui.GetClientRect(int(self.host.winId()))
            return r - l, b - t
        except Exception:
            k = self.host.devicePixelRatioF()
            return int(self.host.width() * k), int(self.host.height() * k)

    def fit(self):
        if self.hwnd:
            w, h = self.host_size()
            chrome.embed_resize(self.hwnd, w, h)

    def poll(self):
        if not self.dir:
            return
        p = self.ctl.profile(self.dir)
        if not p:
            return self.go_back()
        if self.hwnd:
            if not chrome.is_window(self.hwnd):
                # the user closed the browser window from inside the view
                self.dock.forget(self.hwnd)
                self.hwnd = None
                self.msg.setText("Browser window band ho gayi.")
                self.msg.show()
                self.tick.stop()
                QTimer.singleShot(300, self.ctl.refresh_windows)
                return
            self.keep_in_place()
            return
        h = self.find_window(p)
        if h:
            # a just-launched window: give the Hark tab a moment to become the active one
            if self.prefer and self.prefer.lower() not in chrome.window_title(h).lower() and time.time() < self.deadline - self.WAIT + 6:
                return
            return self.attach(h)
        if time.time() > self.deadline:
            self.tick.stop()
            self.msg.setText("Window nahi mili. Profile Launch karke dobara try karein.")

    def keep_in_place(self):
        """The browser's own tab strip can drag, minimise or maximise its window; keep it filling the view."""
        import win32gui
        import win32con
        try:
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            l, t, r, b = win32gui.GetWindowRect(self.hwnd)
            hl, ht = win32gui.ClientToScreen(int(self.host.winId()), (0, 0))
            w, h = self.host_size()
            if (l, t) != (hl, ht) or abs((r - l) - w) > 1 or abs((b - t) - h) > 1:
                self.fit()
        except Exception:
            pass

    def refresh_status(self):
        p = self.ctl.profile(self.dir) if self.dir else None
        if p:
            self.status.set_status(self.ctl.status(p))
            self.name.setText(p.name)
