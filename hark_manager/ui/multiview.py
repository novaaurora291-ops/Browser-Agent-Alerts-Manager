"""Live Multi-View: the open profiles' real browser windows side by side in a separate, maximised window.

Every running Cent window (docked in the Manager or still on the desktop) is docked into a pane with Win32
SetParent: its own process, data folder and signed-in session, so its Hark chat can be read, scrolled and
answered right there. Docked windows have no taskbar button. A Cent profile launched while the grid is open is
added to it automatically. Close (or the window's X): with "Keep Cent windows docked" on (default) every window
stays docked and running inside the Manager; with it off each returns to its desktop position, size,
maximised/minimised state and taskbar button. A window whose docking fails is put back on the desktop as it was."""
import json
import math
import time

from PySide6.QtCore import Qt, QTimer, QSettings, QPoint, QRect
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QComboBox, QSlider, QSplitter,
                               QScrollArea, QScrollBar, QPushButton, QApplication)

from .browser_view import Host, EmbedFocus
from .widgets import label, button, Dot, VpnBadge
from .style import build_qss, GREEN, LIME
from . import icons
from .. import chrome
from ..config import log

LAYOUTS = ["2 Columns (Portrait)", "3 Columns (Portrait)", "2 Rows (Horizontal)", "2x3 Grid (6 Panels)", "Auto Fit"]
DEFAULT_LAYOUT = "2x3 Grid (6 Panels)"


def grid_for(layout, n):
    """(columns, visible columns, visible rows) for n panes."""
    n = max(1, n)
    if layout.startswith("2 Columns"):
        return 2, 2, 1
    if layout.startswith("3 Columns"):
        return 3, 3, 1
    if layout.startswith("2 Rows"):
        return 1, 1, 2
    if layout.startswith("2x3"):
        return 3, 3, 2
    cols = math.ceil(math.sqrt(n))                      # Auto Fit: everything on one screen
    return cols, cols, math.ceil(n / cols)


class PaneHeader(QFrame):
    """Pane title bar; dragging it onto another pane swaps the two."""

    def __init__(self, pane):
        super().__init__()
        self.pane = pane
        self.setObjectName("paneHead")
        self.setCursor(Qt.OpenHandCursor)
        self._press = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._press is not None:
            g = e.globalPosition().toPoint()
            if (g - self._press).manhattanLength() > 8:
                self.setCursor(Qt.ClosedHandCursor)
                self.pane.mv.drag_hover(self.pane, g)

    def mouseReleaseEvent(self, e):
        if self._press is not None and e.button() == Qt.LeftButton:
            g = e.globalPosition().toPoint()
            moved = (g - self._press).manhattanLength() > 8
            self._press = None
            self.setCursor(Qt.OpenHandCursor)
            self.pane.mv.drag_drop(self.pane, g if moved else None)


class GridScroll(QScrollArea):
    """The grid's scroll area. The mouse wheel over a pane header, a splitter gap or a pane message scrolls the
    whole grid (those events bubble up here). The wheel inside a docked browser is never seen here: Windows
    delivers it to the browser window under the cursor (its own process) and the pane's Host swallows anything
    the browser would hand back, so a chat scrolls only itself."""
    WHEEL_PX = 110                    # pixels per wheel notch (120 units)

    def wheelEvent(self, e):
        d = e.angleDelta()
        horiz = bool(e.modifiers() & Qt.ShiftModifier) or (d.x() and not d.y())
        bar = self.horizontalScrollBar() if horiz else self.verticalScrollBar()
        delta = (d.y() or d.x())
        if not delta and not e.pixelDelta().isNull():
            delta = e.pixelDelta().y() or e.pixelDelta().x()
            bar.setValue(bar.value() - delta)
        else:
            bar.setValue(bar.value() - int(delta * self.WHEEL_PX / 120))
        e.accept()


class GridBar(QFrame):
    """Always visible "Grid" scrollbar on the right edge, outside the panes (▲ / thumb / ▼). It mirrors the grid
    scroll area's own vertical bar (kept hidden): dragging it, ▲ / ▼ (one grid row at a time) and the wheel over
    headers / gaps all move the same value."""

    def __init__(self, mv, inner):
        super().__init__()
        self.mv, self.inner = mv, inner
        self.setObjectName("gridCol")
        self.setFixedWidth(58)
        v = QVBoxLayout(self)
        v.setContentsMargins(7, 8, 7, 8)
        v.setSpacing(8)
        cap = QLabel("Grid")
        cap.setObjectName("gridCap")
        cap.setAlignment(Qt.AlignCenter)
        cap.setFixedHeight(32)
        cap.setAccessibleName("Grid scroll")
        v.addWidget(cap)
        self.up = QPushButton("▲")
        self.up.setObjectName("gridBtn")
        self.up.setFixedSize(44, 40)
        self.up.setAutoRepeat(True)
        self.up.setAutoRepeatDelay(450)
        self.up.setAutoRepeatInterval(220)
        self.up.setToolTip("Grid upar scroll (ek row)")
        self.up.setAccessibleName("Grid scroll up")
        self.up.clicked.connect(lambda: self.step(-1))
        v.addWidget(self.up, 0, Qt.AlignHCenter)
        self.bar = QScrollBar(Qt.Vertical)
        self.bar.setObjectName("gridBar")
        self.bar.setAccessibleName("Grid scrollbar")
        self.bar.setToolTip("Poori grid scroll karein")
        v.addWidget(self.bar, 1, Qt.AlignHCenter)
        self.down = QPushButton("▼")
        self.down.setObjectName("gridBtn")
        self.down.setFixedSize(44, 40)
        self.down.setAutoRepeat(True)
        self.down.setAutoRepeatDelay(450)
        self.down.setAutoRepeatInterval(220)
        self.down.setToolTip("Grid neeche scroll (ek row)")
        self.down.setAccessibleName("Grid scroll down")
        self.down.clicked.connect(lambda: self.step(1))
        v.addWidget(self.down, 0, Qt.AlignHCenter)
        for w in (self.up, self.down, self.bar):
            w.setFocusPolicy(Qt.NoFocus)        # keys typed for a pane never land on the Grid controls
        inner.rangeChanged.connect(self.sync_range)
        inner.valueChanged.connect(self._from_inner)
        self.bar.valueChanged.connect(self._from_bar)
        self.sync_range(inner.minimum(), inner.maximum())

    def sync_range(self, lo, hi):
        self.bar.setRange(lo, hi)
        self.bar.setPageStep(max(1, self.mv.scroll.viewport().height()))
        self.bar.setSingleStep(60)
        self.bar.setValue(self.inner.value())
        self.bar.setEnabled(hi > lo)
        self.up.setEnabled(hi > lo)
        self.down.setEnabled(hi > lo)

    def _from_inner(self, val):
        if self.bar.value() != val:
            self.bar.setValue(val)

    def _from_bar(self, val):
        if self.inner.value() != val:
            self.inner.setValue(val)

    def wheelEvent(self, e):
        self.mv.scroll.wheelEvent(e)

    def step(self, direction):
        """One grid row up / down: the next row starts at the top of the view (the last stop is the bottom)."""
        cur, hi = self.inner.value(), self.inner.maximum()
        tops = sorted({r.mapTo(self.mv.canvas, QPoint(0, 0)).y() for r in self.mv.rows if r.isVisible()})
        if direction > 0:
            nxt = [t for t in tops if t > cur + 2]
            self.inner.setValue(min(hi, nxt[0]) if nxt else hi)
        else:
            prv = [t for t in tops if t < cur - 2]
            self.inner.setValue(prv[-1] if prv else 0)


class Pane(QFrame):
    def __init__(self, mv, directory):
        super().__init__()
        self.mv, self.dir = mv, directory
        self.hwnd = None
        self.hark_tries, self.hark_next = 0, 0
        self.deadline = time.time() + 25
        self.setObjectName("pane")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.head = PaneHeader(self)
        h = QHBoxLayout(self.head)
        h.setContentsMargins(16, 6, 10, 6)
        h.setSpacing(10)
        self.name = label("", size=20, bold=True)
        self.name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        h.addWidget(self.name, 1)
        self.vpn = VpnBadge(14)
        h.addWidget(self.vpn)
        self.b_exp = button("", "link", "expand", 20)
        self.b_exp.setFixedSize(38, 34)
        self.b_exp.setToolTip("Expand")
        self.b_exp.clicked.connect(lambda: self.mv.toggle_expand(self))
        h.addWidget(self.b_exp)
        v.addWidget(self.head)
        self.msg = QLabel("")
        self.msg.setAlignment(Qt.AlignCenter)
        self.msg.setStyleSheet("color:#C9CED6;font-size:15px;background:#1E2430;")
        self.host = Host()
        self.host.setMinimumSize(120, 80)
        self.host.resized.connect(self.fit)
        v.addWidget(self.msg, 1)
        v.addWidget(self.host, 1)
        self.host.hide()
        self.refresh()

    def set_expanded(self, on):
        self.b_exp.setIcon(icons.icon("collapse" if on else "expand", 20))
        self.b_exp.setProperty("hbm_icon", f"{'collapse' if on else 'expand'}:20")
        self.b_exp.setToolTip("Collapse" if on else "Expand")
        self.b_exp.setAccessibleName(f"{'Collapse' if on else 'Expand'} {self.name.text()}")

    def set_drop_target(self, on):
        self.setProperty("drop", on)
        self.setStyleSheet(f"QFrame#pane{{border:3px solid {LIME};}}" if on else "")

    def refresh(self):
        ctl = self.mv.ctl
        p = ctl.profile(self.dir)
        if not p:
            return
        self.name.setText(p.name)
        self.vpn.set_state(*ctl.vpn(p), who=p.name)
        self.setAccessibleName(f"Pane {p.name}")
        self.head.setAccessibleName(f"Pane header {p.name}")
        self.set_expanded(self.mv.expanded is self)

    # ---------- embedding ----------
    def host_size(self):
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

    def take(self, hwnd):
        """Dock this Cent window into the pane (from the desktop, or moved over from where it is docked)."""
        self.host.show()
        self.msg.hide()
        hid = int(self.host.winId())
        dock = self.mv.dock
        origin = "dock" if dock.has(hwnd) else "desktop"
        if not dock.dock(self.dir, hwnd, hid, "grid", origin):
            self.host.hide()
            self.msg.setText("Docking fail hui: window apni jagah desktop par wapas hai.")
            self.msg.show()
            self.failed = hwnd
            return
        self.hwnd = hwnd
        self.fit()
        self.hark_tries, self.hark_next = 0, 0
        self.ask_hark()

    def ask_hark(self):
        """Bring this window's Hark chat tab to the front (the bridge opens hark.com/chat only when it has none).
        Retried a few times: a bridge picks commands up on its next report (<= 30 s) or may be reloading."""
        p = self.mv.ctl.profile(self.dir)
        if p and self.mv.ctl.is_planner(p):
            # the Shared Repo Team's Planner works in ChatGPT: never switch its tab to Hark
            if self.hark_tries < 99:
                log(f"multiview {self.dir}: team Planner, its tab is left as it is (no show_hark)")
            self.hark_tries = 99
            return
        if not self.hwnd or "hark" in chrome.window_title(self.hwnd).lower() or self.hark_tries >= 4:
            return
        if p and self.hark_tries >= 2 and not getattr(self, "hark_opened", False):
            # an older bridge copy has no show_hark: open hark.com/chat; the browser puts it in this (hosted) window
            self.hark_opened = True
            self.hark_tries += 1
            self.hark_next = time.time() + 35
            log(f"multiview {self.dir}: bridge did not show a Hark tab, opening hark.com/chat")
            self.mv.ctl.open_in(p, "https://hark.com/chat")
            return
        if p:
            key = self.mv.ctl.bridge_key(p)
            with self.mv.ctl.bridge.lock:
                q = self.mv.ctl.bridge.commands.get(key) or []
                self.mv.ctl.bridge.commands[key] = [c for c in q if c.get("type") != "show_hark"]
            cmd = {"type": "show_hark"}
            wurl = self.mv.ctl.team_worker_url(p)
            if wurl:
                cmd["url"] = wurl         # a team worker: its own project tab, never another Hark tab
            self.mv.ctl.bridge.queue_command(key, cmd)
            self.hark_tries += 1
            self.hark_next = time.time() + 35

    def poll(self):
        if self.hwnd:
            if not chrome.is_window(self.hwnd):
                self.mv.dock.forget(self.hwnd)
                self.hwnd = None
                self.host.hide()
                self.msg.setText("Browser window band ho gayi. Refresh dabayein.")
                self.msg.show()
                return
            self.keep_in_place()
            if time.time() > getattr(self, "hark_next", 0):
                self.ask_hark()
            return
        p = self.mv.ctl.profile(self.dir)
        if not p:
            return
        dock = self.mv.dock
        h = dock.window_of(self.dir, "park")
        if h:
            return self.take(h)
        wins = [w for w in chrome.windows_for(p) if chrome.is_window(w[0]) and not dock.has(w[0])
                and w[0] != getattr(self, "failed", None)]
        if wins:
            hark = [w for w in wins if "hark" in (w[1] or "").lower()]
            return self.take((hark or wins)[0][0])
        if time.time() > self.deadline:
            self.msg.setText("Window nahi mili. Profile launch karke Refresh dabayein.")
        else:
            self.msg.setText(f"{p.name} ki window dhoond raha hoon…")
        self.msg.show()

    def keep_in_place(self):
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

    def visible_rect(self):
        """Screen rect (device pixels) of this pane's browser area that is on screen: host ∩ grid viewport."""
        import win32gui
        try:
            if not self.host.isVisible():
                return None
            l, t, r, b = win32gui.GetWindowRect(int(self.host.winId()))
            vp = int(self.mv.scroll.viewport().winId())
            vl, vt = win32gui.ClientToScreen(vp, (0, 0))
            _, _, vw, vh = win32gui.GetClientRect(vp)
            l, t, r, b = max(l, vl), max(t, vt), min(r, vl + vw), min(b, vt + vh)
            return (l, t, r, b) if r > l and b > t else None
        except Exception:
            return None

    def clip_popups(self, popups):
        chrome.clip_popups(popups, self.visible_rect())


class MultiView(QWidget):
    def __init__(self, ctl, release_main=None, dock=None):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.release_main = release_main or (lambda d: None)
        self.dock = dock
        self.panes = []
        self.expanded = None
        self.rows = []
        self.qs = QSettings("Hark", "HarkBrowserManager")
        self.setWindowTitle("Hark Browser Manager — Live Multi-View")
        self.setWindowIcon(icons.icon("logo", 32))
        self.setStyleSheet(build_qss() + """
            QWidget#mvRoot{background:#F6F7F8;}
            QFrame#pane{background:#1E2430;border:none;}
            QFrame#paneHead{background:#FFFFFF;border-bottom:1px solid #E3E6EA;}
            QSplitter::handle{background:#D5D9DE;}
            QSplitter::handle:hover{background:#9BEA2C;}
            QSlider::groove:horizontal{height:6px;background:#E3E6EA;border-radius:3px;}
            QSlider::sub-page:horizontal{background:#9BEA2C;border-radius:3px;}
            QSlider::handle:horizontal{background:white;border:2px solid #7BD21A;width:16px;margin:-7px 0;border-radius:10px;}
            QFrame#gridCol{background:#FFFFFF;border-left:1px solid #E3E6EA;}
            QLabel#gridCap{background:#2A303B;color:white;font-size:15px;font-weight:700;border-radius:7px;}
            QPushButton#gridBtn{background:#FFFFFF;border:1px solid #CDD1D6;border-radius:9px;font-size:15px;color:#111418;padding:0;}
            QPushButton#gridBtn:hover{background:#EAF8D2;border-color:#7BD21A;}
            QPushButton#gridBtn:disabled{color:#B8BDC4;}
            QScrollBar#gridBar:vertical{width:16px;background:#E9ECEF;border-radius:8px;margin:0;}
            QScrollBar#gridBar::handle:vertical{background:#9BEA2C;border-radius:8px;min-height:48px;}
            QScrollBar#gridBar::handle:vertical:hover{background:#8FE01F;}
            QScrollBar#gridBar::handle:vertical:disabled{background:#D5D9DE;}
            QScrollBar#gridBar::add-line, QScrollBar#gridBar::sub-line{height:0;}
            QScrollBar#gridBar::add-page, QScrollBar#gridBar::sub-page{background:transparent;}
        """)
        self.setObjectName("mvRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("mvBar")
        bar.setStyleSheet("QFrame#mvBar{background:#FFFFFF;border-bottom:1px solid #E3E6EA;}")
        b = QHBoxLayout(bar)
        b.setContentsMargins(18, 8, 16, 8)
        b.setSpacing(16)
        b.addWidget(Dot(GREEN, 14))
        self.title = label("Live Multi-View Grid", size=21, bold=True)
        self.title.setAccessibleName("Multi-View title")
        b.addWidget(self.title)
        b.addStretch(1)
        self.layout_box = QComboBox()
        self.layout_box.addItems(LAYOUTS)
        self.layout_box.setMinimumHeight(40)
        self.layout_box.setMinimumWidth(230)
        self.layout_box.setAccessibleName("Layout")
        b.addWidget(self.layout_box)
        b.addSpacing(8)
        b.addWidget(label("Panel Size", size=16, bold=True))
        sl = QVBoxLayout()
        sl.setSpacing(0)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(50, 150)
        self.zoom.setSingleStep(5)
        self.zoom.setPageStep(10)
        self.zoom.setFixedWidth(170)
        self.zoom.setAccessibleName("Panel Size")
        sl.addWidget(self.zoom)
        ends = QHBoxLayout()
        ends.addWidget(label("50%", size=12))
        ends.addStretch(1)
        ends.addWidget(label("150%", size=12))
        sl.addLayout(ends)
        b.addLayout(sl)
        self.zoom_lbl = label("100%", size=16, bold=True)
        self.zoom_lbl.setMinimumWidth(52)
        self.zoom_lbl.setAccessibleName("Panel Size value")
        b.addWidget(self.zoom_lbl)
        self.b_tile = button("Tile All", "primary")
        self.b_tile.setMinimumSize(110, 40)
        self.b_tile.clicked.connect(self.tile_all)
        b.addWidget(self.b_tile)
        self.b_ref = button("Refresh", "plain")
        self.b_ref.setMinimumSize(110, 40)
        self.b_ref.clicked.connect(self.refresh_all)
        b.addWidget(self.b_ref)
        self.b_close = button("✕  Close", "plain")
        self.b_close.setMinimumSize(110, 40)
        self.b_close.setAccessibleName("Close Multi-View")
        self.b_close.clicked.connect(self.close)
        b.addWidget(self.b_close)
        v.addWidget(bar)

        self.scroll = GridScroll()
        self.scroll.setWidgetResizable(False)
        # the vertical bar is the "Grid" column on the right (GridBar); the area's own one stays hidden
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        # a native viewport clips the hosted browser windows to the visible area while scrolling
        self.scroll.viewport().setAttribute(Qt.WA_NativeWindow, True)
        self.scroll.setStyleSheet("QScrollArea{background:#2A303B;border:none;}")
        self.canvas = QWidget()
        self.canvas.setObjectName("mvCanvas")
        self.cv = QVBoxLayout(self.canvas)
        self.cv.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.canvas)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.scroll, 1)
        self.gridbar = GridBar(self, self.scroll.verticalScrollBar())
        body.addWidget(self.gridbar)
        v.addLayout(body, 1)
        # docked browser windows follow the grid at once when it scrolls (their hosts are clipped by the viewport)
        self.scroll.verticalScrollBar().valueChanged.connect(self.on_scrolled)
        self.scroll.horizontalScrollBar().valueChanged.connect(self.on_scrolled)
        self.outer = None

        lay = self.qs.value("multiview/layout", DEFAULT_LAYOUT)
        self.layout_box.setCurrentText(lay if lay in LAYOUTS else DEFAULT_LAYOUT)
        self.zoom.setValue(int(self.qs.value("multiview/zoom", 100)))
        self.zoom_lbl.setText(f"{self.zoom.value()}%")
        self.layout_box.currentTextChanged.connect(self.change_layout)
        self.zoom.valueChanged.connect(self.change_zoom)

        self.tick = QTimer(self, interval=700, timeout=self.poll)
        self.t_status = QTimer(self, interval=3000, timeout=self.refresh_status)
        ctl.changed.connect(self.refresh_status)
        self._save_t = QTimer(self, singleShot=True, interval=400, timeout=self.save_splits)
        self._clip_t = QTimer(self, singleShot=True, interval=150, timeout=self.clip_popups)
        # keyboard focus: a click in a pane's browser gives that browser the keys (EmbedFocus); the pane that had
        # them last gets them back when this window is activated again (Alt+Tab, taskbar) or after Expand/Collapse
        self.kb_hwnd = None
        EmbedFocus.install().listeners.append(self.on_embed_focus)

    # ---------- open / close ----------
    def open(self):
        # activated again by Alt+Tab / the taskbar: the keys go back to the pane that had them (a click decides itself)
        EmbedFocus.install().activated[int(self.winId())] = self.focus_pane
        if self.isVisible():
            self.showMaximized()
            self.raise_()
            self.activateWindow()
            return
        self.showMaximized()
        # saved layout, zoom, pane order and splitter sizes (Tile All is the one that resets sizes)
        QTimer.singleShot(150, lambda: self.build(self.open_dirs()))
        self.tick.start()
        self.t_status.start()

    def open_dirs(self):
        """Running Cent profiles (a window on the desktop, or one docked in the Manager), in the saved pane order."""
        self.ctl.refresh_windows()
        dirs = [p.directory for p in self.ctl.profiles if p.directory not in self.ctl.no_dock and
                (self.ctl.windows.get(p.directory) or p.directory in self.ctl.embedded)]
        order = self.saved_order()
        return sorted(dirs, key=lambda d: (order.index(d) if d in order else 10 ** 4, dirs.index(d)))

    def saved_order(self):
        try:
            return json.loads(self.qs.value("multiview/order", "[]"))
        except Exception:
            return []

    def save_order(self):
        cur = [p.dir for p in self.panes]
        rest = [d for d in self.saved_order() if d not in cur]
        self.qs.setValue("multiview/order", json.dumps(cur + rest))
        self.qs.sync()

    # ---------- keyboard focus ----------
    def on_embed_focus(self, hwnd, ok):
        if any(p.hwnd == hwnd for p in self.panes):
            self.kb_hwnd = hwnd

    def focus_pane(self, pane=None):
        """Keys to this pane's browser (default: the one that had them last), if it is shown in the grid."""
        h = pane.hwnd if pane else self.kb_hwnd
        p = next((p for p in self.panes if p.hwnd and p.hwnd == h), None)
        if p and p.isVisible() and chrome.focus_embedded(p.hwnd):
            self.kb_hwnd = p.hwnd
            return True
        return False

    def closeEvent(self, e):
        self.release_all()
        self.tick.stop()
        self.t_status.stop()
        super().closeEvent(e)

    def release_all(self):
        self.save_splits()
        chrome.unclip_popups()          # bubbles of windows leaving the grid get their whole area back
        for pane in self.panes:
            if pane.hwnd:
                self.dock.give_back(pane.hwnd)
                log(f"multiview released {pane.dir} hwnd={pane.hwnd} -> {'docked (hidden)' if self.dock.has(pane.hwnd) else 'desktop'}")
                pane.hwnd = None
        self.expanded = None
        self._clear_layout()
        QTimer.singleShot(300, self.ctl.refresh_windows)

    # ---------- building ----------
    def _clear_layout(self):
        for pane in self.panes:
            if pane.hwnd and self.dock.has(pane.hwnd):
                chrome.reparent(pane.hwnd, self.dock.park_id)    # kept alive while the splitters are rebuilt
            pane.setParent(None)
            pane.deleteLater()
        self.panes = []
        if self.outer:
            self.outer.setParent(None)
            self.outer.deleteLater()
            self.outer = None
        self.rows = []

    def build(self, dirs, keep=None):
        """(Re)build panes for dirs. keep: {dir: hwnd} of windows already hosted, moved across via the park."""
        keep = keep or {}
        for p in self.panes:
            if p.hwnd:
                keep[p.dir] = p.hwnd
        # windows that drop out of the grid go back to where they were before it
        for d, h in list(keep.items()):
            if d not in dirs:
                self.dock.give_back(h)
                keep.pop(d)
        self._clear_layout()
        self.expanded = None
        lay = self.layout_box.currentText()
        cols, _, _ = grid_for(lay, len(dirs))
        self.outer = QSplitter(Qt.Vertical)
        self.outer.setHandleWidth(6)
        self.outer.setChildrenCollapsible(False)
        self.outer.splitterMoved.connect(lambda *_: self._save_t.start())
        for i in range(0, max(1, len(dirs)), cols):
            row = QSplitter(Qt.Horizontal)
            row.setHandleWidth(6)
            row.setChildrenCollapsible(False)
            row.splitterMoved.connect(lambda *_: self._save_t.start())
            for d in dirs[i:i + cols]:
                if d in self.ctl.embedded and d not in keep:
                    self.release_main(d)       # the single live view had it: take it over
                pane = Pane(self, d)
                self.panes.append(pane)
                row.addWidget(pane)
            self.rows.append(row)
            self.outer.addWidget(row)
        if not dirs:
            empty = QLabel("Koi open profile nahi. Profile launch karke Refresh dabayein.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#C9CED6;font-size:17px;")
            self.outer.addWidget(empty)
        self.cv.addWidget(self.outer)
        self.size_canvas()
        self.restore_splits()
        QTimer.singleShot(400, lambda: self.expanded is None and self.restore_splits())   # again once the canvas has its real size
        QApplication.processEvents()
        for pane in self.panes:
            if pane.dir in keep:
                pane.take(keep[pane.dir])
        self.update_title()
        self.save_order()
        QTimer.singleShot(0, self.poll)

    def size_canvas(self):
        vw = self.scroll.viewport().width()
        vh = self.scroll.viewport().height()
        z = self.zoom.value() / 100
        if self.expanded:
            w, h = int(vw * z), int(vh * z)
        else:
            n = len(self.panes)
            cols, vcols, vrows = grid_for(self.layout_box.currentText(), n)
            rows = max(1, math.ceil(n / cols)) if n else 1
            w = int(vw * z * min(cols, max(1, n)) / vcols) if n else vw
            h = int(vh * z * rows / vrows)
        # never leave a strip of a pixel or two that only shows a scrollbar
        if abs(w - vw) < 4:
            w = vw
        if abs(h - vh) < 4:
            h = vh
        self.canvas.setFixedSize(max(200, w), max(150, h))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self.size_canvas)

    def on_scrolled(self, *_):
        for p in self.panes:
            if p.hwnd:
                p.keep_in_place()
                chrome.notify_moved(p.hwnd)     # its own bubbles re-anchor to where it is now
        self._clip_t.start()

    def clip_popups(self):
        """Chrome's bubbles of the docked windows are top-level windows: keep each inside its pane's visible area."""
        live = [p for p in self.panes if p.hwnd]
        pops = chrome.owned_popups([p.hwnd for p in live])
        for p in live:
            p.clip_popups(pops.get(p.hwnd, []))

    # ---------- splitter sizes ----------
    def _split_key(self):
        return f"multiview/splits/{self.layout_box.currentText()}/{len(self.panes)}"

    def save_splits(self):
        if not self.outer or self.expanded or not self.panes:
            return
        def frac(s):
            t = sum(s.sizes()) or 1
            return [round(x / t, 4) for x in s.sizes()]
        data = {"outer": frac(self.outer), "rows": [frac(r) for r in self.rows]}
        self.qs.setValue(self._split_key(), json.dumps(data))
        self.qs.sync()

    def restore_splits(self):
        try:
            data = json.loads(self.qs.value(self._split_key(), ""))
        except Exception:
            data = None
        def apply(s, fr):
            n = s.count()
            if not fr or len(fr) != n:
                fr = [1 / n] * n
            tot = (s.height() if s.orientation() == Qt.Vertical else s.width()) or 1000
            s.setSizes([max(40, int(f * tot)) for f in fr])
        self.canvas.layout().activate()
        apply(self.outer, (data or {}).get("outer"))
        for i, r in enumerate(self.rows):
            rr = (data or {}).get("rows") or []
            apply(r, rr[i] if i < len(rr) else None)

    # ---------- top bar ----------
    def change_layout(self, lay):
        self.qs.setValue("multiview/layout", lay)
        self.qs.sync()
        self.build([p.dir for p in self.panes])

    def change_zoom(self, z):
        self.zoom_lbl.setText(f"{z}%")
        self.qs.setValue("multiview/zoom", z)
        self.qs.sync()
        self.size_canvas()

    def tile_all(self):
        """Every open Hark profile in a pane, panes in equal sizes."""
        dirs = self.open_dirs()
        self.qs.remove(f"multiview/splits/{self.layout_box.currentText()}/{len(dirs)}")
        self.build(dirs)
        log(f"multiview tile all: {len(dirs)} panes ({self.layout_box.currentText()}, {self.zoom.value()}%)")

    def refresh_all(self):
        """Profile list + windows + status + VPN, then add newly opened profiles and drop closed ones."""
        self.ctl.reload_profiles()
        dirs = self.open_dirs()
        cur = [p.dir for p in self.panes]
        alive = [d for d in cur if d in dirs]
        new = alive + [d for d in dirs if d not in alive]
        for p in self.ctl.profiles:
            if p.directory in new:
                self.ctl.vpn_check(p)
        for pane in self.panes:
            pane.hark_tries = 0
            pane.ask_hark()
        if new != cur:
            self.build(new)
        self.refresh_status()
        log(f"multiview refresh: {len(new)} panes")

    def update_title(self):
        n = len(self.panes)
        self.title.setText(f"Live Multi-View Grid ({n} Open Profile{'s' if n != 1 else ''})")

    def refresh_status(self):
        for p in self.panes:
            p.refresh()

    # ---------- expand / swap ----------
    def toggle_expand(self, pane):
        self.expanded = None if self.expanded is pane else pane
        for r in self.rows:
            show_row = self.expanded is None or self.expanded.parent() is r
            r.setVisible(show_row)
        for p in self.panes:
            p.setVisible(self.expanded is None or p is self.expanded)
            p.set_expanded(p is self.expanded)
        self.size_canvas()
        if self.expanded is None:
            self.restore_splits()
        QTimer.singleShot(50, lambda: [p.fit() for p in self.panes])
        # the keys go to the pane just expanded / collapsed (its browser was hidden or moved meanwhile)
        QTimer.singleShot(120, lambda: self.focus_pane(pane) if pane in self.panes else None)

    def pane_at(self, g):
        for p in self.panes:
            if p.isVisible() and QRect(p.mapToGlobal(QPoint(0, 0)), p.size()).contains(g):
                return p
        return None

    def drag_hover(self, src, g):
        t = self.pane_at(g)
        for p in self.panes:
            if p is not src:
                p.set_drop_target(p is t)

    def drag_drop(self, src, g):
        for p in self.panes:
            p.set_drop_target(False)
        t = self.pane_at(g) if g is not None else None
        if not t or t is src:
            return
        self.swap(src, t)

    def swap(self, a, b):
        """Swap two panes: their browser windows trade hosts (no window is closed or re-created)."""
        ha, hb = a.hwnd, b.hwnd
        if ha:
            chrome.reparent(ha, self.dock.park_id)
        if hb:
            chrome.reparent(hb, self.dock.park_id)
        a.dir, b.dir = b.dir, a.dir
        a.hwnd = b.hwnd = None
        a.deadline = b.deadline = time.time() + 25
        if hb:
            a.take(hb)
        if ha:
            b.take(ha)
        a.refresh()
        b.refresh()
        self.save_order()
        log(f"multiview swap: {b.dir} <-> {a.dir}")

    # ---------- upkeep ----------
    def poll(self):
        for p in self.panes:
            p.poll()
        self.clip_popups()
        if time.time() - getattr(self, "_auto_t", 0) > 2:
            self._auto_t = time.time()
            self.auto_add()

    def auto_add(self):
        """A Cent profile launched (or a docked one) while the grid is open gets its own pane automatically."""
        cur = [p.dir for p in self.panes]
        new = [p.directory for p in self.ctl.profiles if p.directory not in cur and p.directory not in self.ctl.no_dock
               and (any(not self.dock.has(w[0]) and w[0] not in self.dock.released for w in self.ctl.windows.get(p.directory) or [])
                    or self.dock.window_of(p.directory, "park"))]
        if new:
            log(f"multiview auto-add: {new}")
            self.build(cur + new)
