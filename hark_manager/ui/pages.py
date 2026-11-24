import os
import time

import threading

from PySide6.QtCore import Qt, Signal, QTimer, QEvent, QRect, QObject, QPointF
from PySide6.QtGui import QPainter, QPainterPath, QPen, QColor, QBrush
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton,
                               QScrollArea, QFrame, QMenu, QInputDialog, QButtonGroup, QLineEdit, QMessageBox,
                               QSizePolicy, QFileDialog, QRubberBand)

from . import icons
from .widgets import card, label, button, StatusLabel, Toggle, SearchBox, Preview, IconLabel, Dot, VpnBadge, AlertPulse
from .style import ORANGE, GREEN, GRAY
from .. import config, notify
from ..core import EXT_VERSION as EXT_VER
from .. import chrome as chrome_mod


def ago(ts):
    d = time.time() - ts
    if d < 60:
        return "Just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        h = int(d // 3600)
        return f"{h} hr ago"
    if d < 172800:
        return "Yesterday"
    return time.strftime("%b %d", time.localtime(ts))


# ======================= Profiles =======================
BADGE = {   # result pill on a card: (background, text colour)
    "limit": ("#FDE2E0", "#C62828"), "ready": ("#DDF5D0", "#1E7B1E"), "ok": ("#EAF8D2", "#2E6B00"),
    "run": ("#E6F0FF", "#1F5FBF"), "fail": ("#FDE2E0", "#C62828"), "info": ("#EEF0F2", "#4A5058"),
}


class Selectable:
    """Drag-select / click-select / right-click for a profile card or row (the page owns the selection)."""

    def _make_result_row(self):
        self.res = QWidget()
        rh = QHBoxLayout(self.res)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(8)
        self.badge = QLabel()
        self.badge_sub = QLabel()
        self.badge_sub.setStyleSheet("font-size:13px;color:#5B616B")
        rh.addWidget(self.badge)
        rh.addWidget(self.badge_sub, 1)
        self.res.setVisible(False)
        return self.res

    def show_result(self):
        r = self.page.results.get(self.p.directory)
        if not r:
            self.res.setVisible(False)
            return
        kind, text, sub = r
        bg, fg = BADGE.get(kind, BADGE["info"])
        self.badge.setText(text)
        self.badge.setAccessibleName(f"Result {self.p.name}: {text}")
        self.badge.setStyleSheet(f"background:{bg};color:{fg};border-radius:7px;padding:3px 9px;font-size:14px;font-weight:600")
        self.badge_sub.setText(sub)
        self.badge_sub.setAccessibleName(f"Result detail {self.p.name}: {sub}")
        self.res.setVisible(True)

    def selected(self):
        return self.p.directory in self.page.selected

    def _a11y(self):
        self.setAccessibleName(f"Profile card {self.p.name}" + (" (selected)" if self.selected() else ""))

    # the card takes the press itself: if it fell through to the page, the page would treat it as a
    # click on empty space (clearing the selection / losing Ctrl+click)
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.page.drag_press(e.globalPosition().toPoint(), self, e.modifiers())
            e.accept()
            return
        if e.button() == Qt.RightButton:
            e.accept()          # right-click never changes a group selection that includes this card
            return
        QFrame.mousePressEvent(self, e)

    def mouseMoveEvent(self, e):
        if self.page._drag:
            self.page.drag_move(e.globalPosition().toPoint())
            e.accept()
            return
        QFrame.mouseMoveEvent(self, e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.page.drag_release(e.globalPosition().toPoint())
            e.accept()
            return
        QFrame.mouseReleaseEvent(self, e)

    def contextMenuEvent(self, e):
        if not self.selected():
            self.page.set_selection({self.p.directory})
        e.accept()
        self.page.selection_menu(e.globalPos())


class ProfileCard(Selectable, AlertPulse, QFrame):
    def __init__(self, page, p):
        super().__init__()
        self.page, self.p = page, p
        self._pulse_init(14)
        self.setObjectName("card")
        self.setMinimumSize(260, 345)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.name = label(p.name, size=21, bold=True)
        self.name.setToolTip(f"{p.browser_name} • {p.email or 'not signed in'} • {p.data_dir if p.managed else p.directory}")
        top.addWidget(self.name, 1)
        self.vpn = VpnBadge(13)
        top.addWidget(self.vpn)
        self.dots = QPushButton("•••")
        self.dots.setObjectName("dots")
        self.dots.setCursor(Qt.PointingHandCursor)
        self.dots.clicked.connect(self.menu)
        top.addWidget(self.dots)
        v.addLayout(top)
        self.status = StatusLabel("Idle")
        v.addWidget(self.status)
        self.role = QLabel("")
        self.role.setVisible(False)
        v.addWidget(self.role, 0, Qt.AlignLeft)
        v.addWidget(self._make_result_row())
        self.preview = Preview()
        v.addWidget(self.preview, 1)
        self.action = button("Focus window", "outline", "window")
        self.action.setMinimumHeight(40)
        self.action.clicked.connect(self.act)
        v.addWidget(self.action)

    def refresh(self):
        c = self.page.ctl
        s = c.status(self.p)
        self.name.setText(self.p.name)
        self.status.set_status(s)
        self.vpn.set_state(*c.vpn(self.p), who=self.p.name)
        role = self.page.team_role(self.p)
        self.setObjectName("cardSel" if self.selected() else ("cardAlert" if s == "Needs reply" else ("cardTeam" if role else "card")))
        self.set_pulse(s == "Needs reply" and not self.selected())
        if role == "planner":
            self.role.setText("\U0001F9E0  Planner (ChatGPT)")
            self.role.setStyleSheet("background:#EAF8D2;border:1px solid #9BEA2C;border-radius:12px;padding:3px 12px;font-size:14px;font-weight:600")
            self.role.setAccessibleName(f"Team role {self.p.name}: Planner (ChatGPT)")
        elif role:
            self.role.setText(role)
            self.role.setStyleSheet("background:#F4FCE8;border:1px solid #C9EE95;border-radius:12px;padding:2px 10px;font-size:13px;font-weight:600;color:#2E6B00")
            self.role.setAccessibleName(f"Team role {self.p.name}: worker {role}")
        self.role.setVisible(bool(role))
        self.show_result()
        self._a11y()
        self.style().unpolish(self)
        self.style().polish(self)
        open_ = bool(c.windows.get(self.p.directory))
        if open_:
            self.action.setText("Focus window")
            self.action.setIcon(icons.icon("window", 18))
        else:
            self.action.setText("Launch")
            self.action.setIcon(icons.icon("play", 18))
        show = c.settings["show_previews"]
        mini = open_ and all(w[3] for w in c.windows.get(self.p.directory, []))
        ph = "Closed" if not open_ else ("Minimized" if mini else ("Previews off" if not show else "Loading preview…"))
        self.preview.set_png(c.previews.get(self.p.directory) if (open_ and show) else None, ph)

    def paintEvent(self, e):
        QFrame.paintEvent(self, e)
        self._paint_pulse()

    def mouseDoubleClickEvent(self, e):
        # double-click: the profile's live Hark chat inside the Manager
        if e.button() == Qt.LeftButton:
            self.page.open_view.emit(self.p.directory)
            return
        QFrame.mouseDoubleClickEvent(self, e)

    def act(self):
        c = self.page.ctl
        if c.windows.get(self.p.directory):
            c.focus_profile(self.p)
        else:
            c.launch(self.p)

    def menu(self):
        c = self.page.ctl
        m = QMenu(self)
        m.addAction("Launch new window", lambda: c.launch(self.p))
        m.addAction("Open in Manager", lambda: self.page.open_view.emit(self.p.directory))
        m.addAction("Focus window", lambda: c.focus_profile(self.p))
        m.addAction("Open Hark", lambda: c.open_in(self.p, "https://hark.com/chat"))
        m.addSeparator()
        m.addAction("Rename…", self.rename)
        m.addAction("Check Limit", lambda: self.page.run_check([self.p.directory]))
        m.addAction("Install Hark Bridge", lambda: self.page.run_install([self.p.directory]))
        m.addAction("Hide from list", self.hide_profile)
        m.exec(self.dots.mapToGlobal(self.dots.rect().bottomLeft()))

    def rename(self):
        name, ok = QInputDialog.getText(self, "Rename profile", "Profile ka naam:", text=self.p.name)
        if ok:
            self.page.ctl.rename(self.p, name.strip())

    def hide_profile(self):
        c = self.page.ctl
        hid = list(c.settings["hidden_profiles"] or [])
        hid.append(self.p.directory)
        c.settings["hidden_profiles"] = hid
        c.settings.save()
        self.page.rebuild()


class ProfileRow(Selectable, AlertPulse, QFrame):
    def __init__(self, page, p):
        super().__init__()
        self.page, self.p = page, p
        self._pulse_init(14)
        self.setObjectName("card")
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(16)
        self.prev = Preview()
        self.prev.setFixedSize(120, 72)
        self.prev.setMinimumHeight(72)
        h.addWidget(self.prev)
        col = QVBoxLayout()
        self.name = label(p.name, size=18, bold=True)
        nh = QHBoxLayout()
        nh.setSpacing(8)
        nh.addWidget(self.name)
        self.vpn = VpnBadge(13)
        nh.addWidget(self.vpn)
        nh.addStretch(1)
        col.addLayout(nh)
        col.addWidget(label(p.email or "Not signed in", obj="muted"))
        col.addWidget(self._make_result_row())
        h.addLayout(col, 2)
        self.status = StatusLabel("Idle")
        h.addWidget(self.status, 1)
        self.action = button("Focus window", "outline", "window")
        self.action.setMinimumWidth(160)
        self.action.clicked.connect(lambda: ProfileCard.act(self))
        h.addWidget(self.action)

    mouseDoubleClickEvent = ProfileCard.mouseDoubleClickEvent
    paintEvent = ProfileCard.paintEvent

    def refresh(self):
        c = self.page.ctl
        s = c.status(self.p)
        self.name.setText(self.p.name)
        self.status.set_status(s)
        self.vpn.set_state(*c.vpn(self.p), who=self.p.name)
        self.setObjectName("cardSel" if self.selected() else ("cardAlert" if s == "Needs reply" else "card"))
        self.set_pulse(s == "Needs reply" and not self.selected())
        self.show_result()
        self._a11y()
        self.style().unpolish(self)
        self.style().polish(self)
        open_ = bool(c.windows.get(self.p.directory))
        self.action.setText("Focus window" if open_ else "Launch")
        self.action.setIcon(icons.icon("window" if open_ else "play", 18))
        self.prev.set_png(c.previews.get(self.p.directory) if open_ and c.settings["show_previews"] else None, "" if open_ else "")


def short_browser(name):
    return name


class ClosedRow(QFrame):
    """One row of the Closed tab: Profile, Browser, Launch (opens the profile with its saved browser + data folder)."""

    def __init__(self, page, p, last=False):
        super().__init__()
        self.page, self.p = page, p
        self.setObjectName("closedRow")
        self.setStyleSheet("QFrame#closedRow{background:white;border:none;" + ("" if last else "border-bottom:1px solid #E3E6EA;") + "}")
        h = QHBoxLayout(self)
        h.setContentsMargins(26, 10, 26, 10)
        h.setSpacing(14)
        av = QLabel()
        av.setPixmap(icons.pixmap("user", 22, "#5B616B"))
        av.setFixedSize(38, 38)
        av.setAlignment(Qt.AlignCenter)
        av.setStyleSheet("background:#EEF0F2;border-radius:19px;")
        h.addWidget(av)
        self.name = label(p.name, size=18, bold=True)
        self.name.setToolTip(p.email or "")
        h.addWidget(self.name, 4)
        h.addWidget(label(short_browser(p.browser_name), size=17), 3)
        self.action = button("Launch", "primary", "play", 16)
        self.action.setMinimumSize(166, 40)
        self.action.setStyleSheet("font-size:17px;")
        self.action.clicked.connect(self.launch)
        h.addWidget(self.action, 2, Qt.AlignLeft)

    mouseDoubleClickEvent = ProfileCard.mouseDoubleClickEvent

    def launch(self):
        self.action.setEnabled(False)
        self.action.setText("Opening…")
        self.page.ctl.launch(self.p)

    def refresh(self):
        self.name.setText(self.p.name)


class TeamOverlay(QWidget):
    """Lime Bezier lines + connection dots between the Shared Repo Team's cards and the repo badge.
    A child of the scrolled cards widget, so it moves with the cards on scroll and is re-laid on every resize."""
    LIME = QColor("#7BD21A")

    def __init__(self, page, holder):
        super().__init__(holder)
        self.page = page
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.badge = QLabel(holder)
        self.badge.setObjectName("repoBadge")
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.badge.setStyleSheet("QLabel#repoBadge{background:white;border:2px solid #9BEA2C;border-radius:19px;"
                                 "padding:6px 18px;font-size:16px;font-weight:600;color:#111418;}")
        self.badge.hide()
        self.hide()
        self.links = None     # (planner card or None, [worker cards])

    def sync(self):
        """Recompute from the page's current cards; hide when no team card is visible."""
        page = self.page
        pid, wids = page.ctl.team_profile_ids()
        cards = {w.p.tag: w for w in page.items.values() if isinstance(w, ProfileCard)}
        planner = cards.get(pid)
        workers = [cards[t] for t in wids if t in cards]
        if page.tab != "open" or page.ctl.settings["view_mode"] != "grid" or (not workers and not planner) or not pid:
            self.links = None
            self.hide()
            self.badge.hide()
            return
        for c in ([planner] if planner else []) + workers:
            c.installEventFilter(self)
        self.links = (planner, workers)
        self.badge.setText(page.repo_badge_text())
        self.badge.setAccessibleName(page.repo_badge_text())
        self.badge.adjustSize()
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.badge.show()
        self.raise_()
        self.badge.raise_()
        self.place_badge()
        self.update()

    def eventFilter(self, obj, e):
        if e.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.Hide) and self.links:
            QTimer.singleShot(0, self.relayout)
        return False

    def relayout(self):
        if not self.links:
            return
        try:
            self.setGeometry(self.parentWidget().rect())
            self.place_badge()
            self.update()
        except RuntimeError:
            self.links = None

    def _anchor(self):
        planner, workers = self.links
        gs = [w.geometry() for w in workers if w.isVisible()]
        bw, bh = self.badge.width(), self.badge.height()
        if planner is not None and planner.isVisible():
            pg = planner.geometry()
            cx = pg.center().x()
            below = [g for g in gs if g.top() >= pg.bottom()]
            cy = (pg.bottom() + min(g.top() for g in below)) / 2 if below else pg.bottom() + bh / 2 + 10
        elif gs:
            cx = sum(g.center().x() for g in gs) / len(gs)
            cy = min(g.top() for g in gs) - bh / 2 - 10
        else:
            return None
        cx = max(bw / 2 + 4, min(self.width() - bw / 2 - 4, cx))
        return QPointF(cx, max(bh / 2 + 2, cy))

    def place_badge(self):
        a = self._anchor()
        if a is None:
            self.badge.hide()
            return
        self.badge.move(int(a.x() - self.badge.width() / 2), int(a.y() - self.badge.height() / 2))

    def paintEvent(self, e):
        if not self.links:
            return
        a = self._anchor()
        if a is None:
            return
        planner, workers = self.links
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self.LIME, 3)
        pen.setCapStyle(Qt.RoundCap)
        qp.setPen(pen)
        qp.setBrush(Qt.NoBrush)
        bh = self.badge.height()
        top, bot = QPointF(a.x(), a.y() - bh / 2), QPointF(a.x(), a.y() + bh / 2)
        dots = []
        if planner is not None and planner.isVisible():
            g = planner.geometry()
            d = QPointF(g.center().x(), g.bottom() + 1)
            path = QPainterPath(d)
            path.cubicTo(QPointF(d.x(), (d.y() + top.y()) / 2), QPointF(top.x(), (d.y() + top.y()) / 2), top)
            qp.drawPath(path)
            dots.append(d)
        for w in workers:
            if not w.isVisible():
                continue
            g = w.geometry()
            if g.top() >= a.y():
                d, s0 = QPointF(g.center().x(), g.top() - 1), bot
            else:
                d, s0 = QPointF(g.center().x(), g.bottom() + 1), top
            h = d.y() - s0.y()
            path = QPainterPath(s0)
            path.cubicTo(QPointF(s0.x(), s0.y() + h * 0.55), QPointF(d.x(), d.y() - h * 0.75), d)
            qp.drawPath(path)
            dots.append(d)
        qp.setPen(QPen(QColor("white"), 2))
        qp.setBrush(QBrush(self.LIME))
        for d in dots:
            qp.drawEllipse(d, 7, 7)
        qp.end()


class _Relay(QObject):
    result = Signal(str, str, str, str)     # profile dir, kind, text, detail (from worker threads)


class ProfilesPage(QWidget):
    install_bridge = Signal(str)
    open_view = Signal(str)   # profile dir -> show its live browser window inside the Manager
    open_multiview = Signal()

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.items = {}
        self.selected = set()      # profile dirs highlighted (drag / click / right-click)
        self.results = {}          # profile dir -> (kind, text, detail) shown on its card
        self._drag = None
        self._checks = {}          # check id -> profile dir (waiting for the bridge)
        self._cid = 0
        self.relay = _Relay()
        self.relay.result.connect(self.set_result)
        ctl.limit_result.connect(self.on_limit)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 10)
        v.setSpacing(18)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(label("Browser profiles", obj="h1"))
        head.addStretch(1)
        self.refresh_btn = button("  Refresh", "outline", "refresh", 18)
        self.refresh_btn.setMinimumHeight(40)
        self.refresh_btn.setStyleSheet("font-size:15px; padding: 6px 16px;")
        self.refresh_btn.setToolTip("Sab Cent profiles ka live Hark status dobara check karo")
        self.refresh_btn.setAccessibleName("Refresh profile statuses")
        self.refresh_btn.clicked.connect(self.recheck_status)
        head.addWidget(self.refresh_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        v.addLayout(head)
        self.cent_banner = QLabel()
        self.cent_banner.setObjectName("banner")
        self.cent_banner.setWordWrap(True)
        self.cent_banner.setAccessibleName("Cent Browser error")
        self.cent_banner.hide()
        v.addWidget(self.cent_banner)
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.t_open = QPushButton("Open")
        self.t_closed = QPushButton("Closed")
        tg = QButtonGroup(self)
        for b in (self.t_open, self.t_closed):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumSize(126, 40)
            tg.addButton(b)
            tabs.addWidget(b)
        self.t_open.setStyleSheet("font-size:17px;border-top-left-radius:9px;border-bottom-left-radius:9px;")
        self.t_closed.setStyleSheet("font-size:17px;border-top-right-radius:9px;border-bottom-right-radius:9px;")
        self.tab = "open"
        self.t_open.setChecked(True)
        self.t_open.clicked.connect(lambda: self.set_tab("open"))
        self.t_closed.clicked.connect(lambda: self.set_tab("closed"))
        tabs.addStretch(1)
        v.addLayout(tabs)
        self.bar = QGridLayout()
        self.bar.setHorizontalSpacing(18)
        self.bar.setVerticalSpacing(12)
        self.search = SearchBox("Search profiles")
        self.search.textChanged.connect(self.rebuild)
        self.search.setMinimumWidth(160)
        self.bfilter = QComboBox()
        self.bfilter.addItem("All browsers")
        self.bfilter.setMinimumHeight(42)
        self.bfilter.setMinimumWidth(170)
        self.bfilter.currentIndexChanged.connect(self.rebuild)
        self.bfilter.hide()        # Cent Browser only: no browser picker
        self.filter = QComboBox()
        self.filter.addItems(["All statuses", "Running", "Needs reply", "Idle", "Closed"])
        self.filter.setMinimumHeight(42)
        self.filter.setMinimumWidth(150)
        self.filter.currentIndexChanged.connect(self.rebuild)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        self.bgrid = QPushButton()
        self.blist = QPushButton()
        for b, ic in ((self.bgrid, "grid"), (self.blist, "list")):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setIcon(icons.icon(ic, 22))
            b.setFixedSize(64, 42)
            b.setCursor(Qt.PointingHandCursor)
            seg.addWidget(b)
        self.bgrid.setStyleSheet("border-top-left-radius:9px;border-bottom-left-radius:9px;")
        self.blist.setStyleSheet("border-top-right-radius:9px;border-bottom-right-radius:9px;")
        grp = QButtonGroup(self)
        grp.addButton(self.bgrid)
        grp.addButton(self.blist)
        (self.blist if ctl.settings["view_mode"] == "list" else self.bgrid).setChecked(True)
        self.bgrid.clicked.connect(lambda: self.set_mode("grid"))
        self.blist.clicked.connect(lambda: self.set_mode("list"))
        self.seg = QWidget()
        self.seg.setLayout(seg)
        seg.setContentsMargins(0, 0, 0, 0)
        add = button("  Add profile", "primary", "plus", 20)
        add.setMinimumHeight(46)
        add.setStyleSheet("font-size:17px; padding: 8px 26px;")
        add.clicked.connect(self.add_profile)
        self.addbtn = add
        mv = button("  Live Multi-View", "outline", "tile", 20)
        mv.setMinimumHeight(46)
        mv.setMinimumWidth(205)
        mv.setStyleSheet("font-size:16px; padding: 8px 18px;")
        mv.setToolTip("Sab open profiles ki live Hark chats ek alag window mein")
        mv.clicked.connect(self.open_multiview.emit)
        self.mvbtn = mv
        self.sel_label = label("", size=16)
        self.sel_label.setAccessibleName("Selection count")
        self._bar_narrow = None
        self._layout_bar(False)
        v.addLayout(self.bar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.grid = QGridLayout(self.holder)
        self.grid.setContentsMargins(0, 0, 6, 10)
        self.grid.setSpacing(18)
        self.scroll.setWidget(self.holder)
        self.holder.installEventFilter(self)       # drag starting on empty space between cards
        self.band = QRubberBand(QRubberBand.Rectangle, self.holder)
        self.band.setStyleSheet("QRubberBand{border:2px solid #7BD21A;background:rgba(155,234,44,45);}")
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)
        v.addWidget(self.scroll, 1)
        self.overlay = TeamOverlay(self, self.holder)
        self.cols = 3
        ctl.changed.connect(self.refresh)
        ctl.team_changed.connect(self.rebuild)
        ctl.previews_changed.connect(self.refresh)
        self.rebuild()

    def team_role(self, p):
        """'planner', the worker's @alias, or '' for a profile outside the Shared Repo Team."""
        from .. import repo_team
        t = self.ctl.team()
        if not repo_team.is_configured(t):
            return ""
        if (t.get("planner") or {}).get("profile_id") == p.tag:
            return "planner"
        for w in t.get("workers") or []:
            if w.get("profile_id") == p.tag:
                return w.get("alias") or "worker"
        return ""

    def repo_badge_text(self):
        from .. import repo_team
        t = self.ctl.team()
        n = len(t.get("workers") or []) + (1 if (t.get("planner") or {}).get("profile_id") else 0)
        name = repo_team.repo_name(t.get("repo_path"))
        res = self.ctl.worktrees_ok
        if self.ctl.worktrees_ready():
            state = "Worktrees Ready"
        elif not res:
            state = "Worktrees not verified"
        else:
            state = f"{sum(1 for r in res.values() if not r[0])} Worktree(s) Blocked"
        return f"\U0001F517 Repo: {name} ({n} Linked \u2022 {state})"

    def recheck_status(self):
        """Refresh button: ask every Cent profile's bridge for its live Hark state now, then sync cards + Needs reply."""
        b = self.refresh_btn
        if not b.isEnabled():
            return
        b.setEnabled(False)
        b.setText("  Checking…")

        def done(summary):
            b.setText("  Refresh")
            b.setEnabled(True)
            b.setToolTip(f"Last check: {summary}")
            self.rebuild()
        self.ctl.recheck_all(done)

    def set_tab(self, t):
        self.tab = t
        (self.t_open if t == "open" else self.t_closed).setChecked(True)
        self.filter.setVisible(t == "open")
        self.seg.setVisible(t == "open")
        self.mvbtn.setVisible(t == "open")
        self.rebuild()

    def is_open(self, p):
        # any window of the profile counts, minimized ones too
        return bool(self.ctl.windows.get(p.directory))

    def set_mode(self, m):
        self.ctl.settings["view_mode"] = m
        self.ctl.settings.save()
        self.cols = self._calc_cols()
        self.rebuild()

    def update_browser_filter(self):
        pass                       # Cent Browser only

    def visible_profiles(self):
        q = self.search.text().strip().lower()
        f = self.filter.currentText()
        hid = set(self.ctl.settings["hidden_profiles"] or [])
        out = []
        for p in self.ctl.profiles:
            if p.directory in hid:
                continue
            if q and q not in (p.name + " " + p.email + " " + p.chrome_name + " " + p.gaia).lower():
                continue
            if (self.tab == "open") != self.is_open(p):
                continue
            if self.tab == "open" and f != "All statuses" and self.ctl.status(p) != f:
                continue
            out.append(p)
        # profiles needing a reply / open first, like the mock-up
        if self.tab == "closed":
            return sorted(out, key=lambda p: (p.managed, p.name.lower()))
        rank = {"Needs reply": 0, "Running": 1, "Idle": 2, "Closed": 3}
        _, wids = self.ctl.team_profile_ids()
        # team workers go last, in team order, so their linked cards sit together under the planner
        return sorted(out, key=lambda p: (1, wids.index(p.tag), 0) if p.tag in wids else (0, 0, rank.get(self.ctl.status(p), 9)))

    def update_counts(self):
        hid = set(self.ctl.settings["hidden_profiles"] or [])
        ps = [p for p in self.ctl.profiles if p.directory not in hid]
        n = sum(1 for p in ps if self.is_open(p))
        self.t_open.setText(f"Open ({n})")
        self.t_closed.setText(f"Closed ({len(ps) - n})")

    def rebuild(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.items = {}
        mode = self.ctl.settings["view_mode"]
        self.update_browser_filter()
        ps = self.visible_profiles()
        self._sig = (self.tab, [p.directory for p in ps])
        self.update_counts()
        self.selected &= {p.directory for p in ps} if self.tab == "open" else set()
        self._sel_text()
        self._cent_banner()
        if self.tab == "closed":
            self.overlay.sync()
            return self._build_closed(ps)
        cells = self._team_cells(ps) if mode == "grid" else None
        for i, p in enumerate(ps):
            if mode == "list":
                w = ProfileRow(self, p)
                self.grid.addWidget(w, i, 0)
            else:
                w = ProfileCard(self, p)
                r, c = cells[p.directory] if cells else (i // self.cols, i % self.cols)
                self.grid.addWidget(w, r, c)
            self.items[p.directory] = w
            w.refresh()
        if not ps:
            self.grid.addWidget(label("Koi profile khula nahi. Closed tab se Launch karein." if self.tab == "open" and not self.search.text().strip() else "Koi profile nahi mila.", obj="muted"), 0, 0)
        ncols = self.cols if mode == "grid" else 1
        for c in range(8):
            # only real columns stretch (equal widths); empty columns must not eat space
            self.grid.setColumnStretch(c, 1 if c < ncols else 0)
        self.grid.setRowStretch(self.grid.rowCount(), 1)
        pid, wids = self.ctl.team_profile_ids()
        team_here = mode == "grid" and any(getattr(w, "p", None) is not None and w.p.tag in wids + [pid] for w in self.items.values())
        self.grid.setVerticalSpacing(74 if team_here else 18)   # room for the repo badge between rows
        QTimer.singleShot(0, self.overlay.sync)

    def _team_cells(self, ps):
        """Grid cells when a Shared Repo Team is visible: the Planner in the middle of the row right above its
        workers (like the mock-up), the workers together on the next row(s), every other profile around them."""
        pid, wids = self.ctl.team_profile_ids()
        workers = [p for p in ps if p.tag in wids]
        planner = next((p for p in ps if p.tag == pid), None)
        if not workers and not planner:
            return None
        cols = max(1, self.cols)
        others = [p for p in ps if p not in workers and p is not planner]
        cells, row = {}, 0
        if planner is not None:
            mid = cols // 2
            free = [c for c in range(cols) if c != mid]
            cells[planner.directory] = (0, mid)
            for c in free:
                if others:
                    cells[others.pop(0).directory] = (0, c)
            row = 1
        for i, p in enumerate(workers):
            cells[p.directory] = (row + i // cols, i % cols)
        row += (len(workers) + cols - 1) // cols
        for i, p in enumerate(others):
            cells[p.directory] = (row + i // cols, i % cols)
        return cells

    def _cent_banner(self):
        err = self.ctl.cent_error
        self.cent_banner.setText("\u26A0 " + err if err else "")
        self.cent_banner.setVisible(bool(err))

    def _build_closed(self, ps):
        for c in range(8):
            self.grid.setColumnStretch(c, 1 if c == 0 else 0)
        tbl = QFrame()
        tbl.setObjectName("closedTbl")
        tbl.setStyleSheet("QFrame#closedTbl{background:white;border:1px solid #E3E6EA;border-radius:14px;}")
        tv = QVBoxLayout(tbl)
        tv.setContentsMargins(1, 1, 1, 1)
        tv.setSpacing(0)
        head = QFrame()
        head.setObjectName("closedHead")
        head.setStyleSheet("QFrame#closedHead{background:#F3F4F5;border:none;border-bottom:1px solid #E3E6EA;border-top-left-radius:13px;border-top-right-radius:13px;}")
        hh = QHBoxLayout(head)
        hh.setContentsMargins(26, 12, 26, 12)
        hh.setSpacing(14)
        sp = QLabel()
        sp.setFixedWidth(38)
        hh.addWidget(label("Profile", size=17, bold=True), 4)
        hh.addWidget(label("Browser", size=17, bold=True), 3)
        hh.addWidget(label("Action", size=17, bold=True), 2)
        hh.insertWidget(0, sp)
        tv.addWidget(head)
        for i, p in enumerate(ps):
            r = ClosedRow(self, p, last=(i == len(ps) - 1))
            tv.addWidget(r)
            self.items[p.directory] = r
        if not ps:
            e = label("Koi band profile nahi mila." if self.search.text().strip() else "Sab profiles khule hain.", obj="muted")
            e.setContentsMargins(26, 16, 26, 16)
            tv.addWidget(e)
        self.grid.addWidget(tbl, 0, 0)
        self.grid.setRowStretch(self.grid.rowCount(), 1)

    def refresh(self):
        if (self.tab, [p.directory for p in self.visible_profiles()]) != getattr(self, "_sig", None):
            return self.rebuild()
        self.update_counts()
        for w in self.items.values():
            w.refresh()
        if self.overlay.links:
            t = self.repo_badge_text()
            if self.overlay.badge.text() != t:
                self.overlay.sync()

    CARD_MIN = 260
    GAP = 18

    def _calc_cols(self):
        w = self.scroll.viewport().width() - 6  # grid right margin
        return max(1, min(4, (w + self.GAP) // (self.CARD_MIN + self.GAP)))

    def _layout_bar(self, narrow):
        if narrow == self._bar_narrow:
            return
        self._bar_narrow = narrow
        for w in (self.search, self.bfilter, self.filter, self.seg, self.addbtn, self.sel_label, self.mvbtn):
            self.bar.removeWidget(w)
        for c in range(7):
            self.bar.setColumnStretch(c, 0)
        if narrow:  # two rows: search, then status + view + add
            self.bar.addWidget(self.search, 0, 0, 1, 5)
            self.bar.addWidget(self.filter, 1, 0)
            self.bar.addWidget(self.seg, 1, 1)
            self.bar.addWidget(self.sel_label, 1, 2)
            self.bar.addWidget(self.mvbtn, 1, 3)
            self.bar.addWidget(self.addbtn, 1, 4)
            self.bar.setColumnStretch(0, 1)
            self.bar.setColumnStretch(2, 1)
        else:
            self.bar.addWidget(self.search, 0, 0, 1, 2)
            self.bar.addWidget(self.filter, 0, 2)
            self.bar.addWidget(self.seg, 0, 3)
            self.bar.addWidget(self.sel_label, 0, 4)
            self.bar.addWidget(self.mvbtn, 0, 5)
            self.bar.addWidget(self.addbtn, 0, 6)
            self.bar.setColumnStretch(0, 3)
            self.bar.setColumnStretch(1, 2)
            self.bar.setColumnStretch(2, 2)
            self.bar.setColumnStretch(4, 1)

    def _update_cols(self):
        self._layout_bar(self.width() < 1060)
        cols = self._calc_cols()
        if cols != self.cols:
            self.cols = cols
            if self.ctl.settings["view_mode"] == "grid":
                self.rebuild()

    def eventFilter(self, obj, e):
        if obj is self.scroll.viewport() and e.type() == QEvent.Resize:
            self._update_cols()
        if obj is self.holder and e.type() in (QEvent.Resize, QEvent.LayoutRequest) and getattr(self, "overlay", None):
            QTimer.singleShot(0, self.overlay.relayout)
        if obj is self.holder and self.tab == "open":
            t = e.type()
            if t == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
                self.drag_press(e.globalPosition().toPoint(), None, e.modifiers())
                return True
            if t == QEvent.MouseMove and self._drag:
                self.drag_move(e.globalPosition().toPoint())
                return True
            if t == QEvent.MouseButtonRelease and e.button() == Qt.LeftButton:
                self.drag_release(e.globalPosition().toPoint())
                return True
        return super().eventFilter(obj, e)

    # a drag may also start in the page margin next to the cards
    def _in_cards_area(self, e):
        return self.tab == "open" and e.position().y() >= self.scroll.geometry().top()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._in_cards_area(e):
            self.drag_press(e.globalPosition().toPoint(), None, e.modifiers())
            return e.accept()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag:
            self.drag_move(e.globalPosition().toPoint())
            return e.accept()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag:
            self.drag_release(e.globalPosition().toPoint())
            return e.accept()
        super().mouseReleaseEvent(e)

    # ---------- selection ----------
    def drag_press(self, gpos, item, mods):
        ctrl = bool(mods & (Qt.ControlModifier | Qt.ShiftModifier))
        self._drag = {"o": self.holder.mapFromGlobal(gpos), "item": item, "ctrl": ctrl, "moved": False,
                      "base": set(self.selected) if ctrl else set()}

    def drag_move(self, gpos):
        d = self._drag
        if not d:
            return
        pt = self.holder.mapFromGlobal(gpos)
        if not d["moved"] and (pt - d["o"]).manhattanLength() < 8:
            return
        d["moved"] = True
        r = QRect(d["o"], pt).normalized()
        self.band.setGeometry(r.intersected(self.holder.rect()))
        self.band.show()
        self.band.raise_()
        hit = {k for k, w in self.items.items() if isinstance(w, (ProfileCard, ProfileRow)) and w.geometry().intersects(r)}
        self.set_selection(d["base"] | hit)

    def drag_release(self, gpos):
        d, self._drag = self._drag, None
        self.band.hide()
        if not d or d["moved"]:
            return
        it = d["item"]
        if it is None:
            if not d["ctrl"]:
                self.set_selection(set())
            return
        k = it.p.directory
        if d["ctrl"]:
            self.set_selection(self.selected ^ {k})
        else:
            self.set_selection({k})

    def set_selection(self, dirs):
        dirs = {k for k in dirs if k in self.items and isinstance(self.items[k], (ProfileCard, ProfileRow))}
        if dirs == self.selected:
            return
        self.selected = dirs
        for k, w in self.items.items():
            if isinstance(w, (ProfileCard, ProfileRow)):
                w.refresh()
        self._sel_text()

    def _sel_text(self):
        n = len(self.selected)
        self.sel_label.setText(f"{n} selected" if n else "")

    def selection_menu(self, gpos):
        n = len(self.selected)
        if not n:
            return
        m = QMenu(self)
        dirs = sorted(self.selected)
        # close the menu first, then run (also when the item is pressed through accessibility)
        m.addAction("Check Limit", lambda: (m.close(), QTimer.singleShot(0, lambda: self.run_check(dirs))))
        m.addAction("Install Hark Bridge", lambda: (m.close(), QTimer.singleShot(0, lambda: self.run_install(dirs))))
        m.exec(gpos)

    def set_result(self, k, kind, text, sub):
        self.results[k] = (kind, text, sub)
        w = self.items.get(k)
        if w is not None and hasattr(w, "show_result"):
            w.show_result()

    # ---------- Check Limit (read only) ----------
    def run_check(self, dirs):
        for k in dirs:
            p = self.ctl.profile(k)
            if not p:
                continue
            if not self.ctl.windows.get(k):
                self.set_result(k, "info", "Closed", "Check Limit: Not checked")
                continue
            self._cid += 1
            cid = self._cid
            self._checks[cid] = k
            if self.ctl.bridge_seen(p, 10) and self.ctl.bridge_ver(p) == EXT_VER:
                self._send_check(p, cid)
                continue
            # bridge silent: is it installed + enabled here? then pair it with this profile and wait for it
            if not chrome_mod.bridge_installed(p, True):
                self._checks.pop(cid, None)
                self.set_result(k, "fail", "Bridge disconnected", "Bridge install nahi · Check Limit: Not checked")
                config.log(f"check limit {k}: bridge not installed/enabled")
                continue
            self.set_result(k, "run", "Connecting…", "Bridge ko is profile se jod raha hoon")
            self.ctl.pair_bridge(p)
            self._wait_live(p, cid, time.time() + 35)

    def _wait_live(self, p, cid, end):
        if cid not in self._checks:
            return
        if self.ctl.bridge_seen(p, 15):
            ver = self.ctl.bridge_ver(p)
            if ver and ver != EXT_VER:
                # an old bridge copy is running: let it reload to the current files first (it reports again on reload)
                if not getattr(self, "_reload_sent", {}).get(cid):
                    self._reload_sent = getattr(self, "_reload_sent", {}) | {cid: True}
                    config.log(f"check limit {p.directory}: bridge {ver} -> reload to {EXT_VER}")
                    self.set_result(p.directory, "run", "Updating bridge…", f"v{ver} → v{EXT_VER}")
                    self.ctl.reload_bridge(p)
                QTimer.singleShot(700, lambda: self._wait_live(p, cid, end))
                return
            config.log(f"check limit {p.directory}: bridge connected (v{ver})")
            return self._send_check(p, cid)
        if time.time() > end:
            self._checks.pop(cid, None)
            config.log(f"check limit {p.directory}: bridge disconnected")
            return self.set_result(p.directory, "fail", "Bridge disconnected", "Check Limit: Not checked")
        QTimer.singleShot(700, lambda: self._wait_live(p, cid, end))

    def _send_check(self, p, cid):
        k = p.directory
        ver = self.ctl.bridge_ver(p)
        if ver and tuple(int(x) for x in ver.split(".")[:3]) < (1, 0, 7):
            # this browser still runs an old bridge copy that has no Check Limit (it updates when the browser restarts)
            self._checks.pop(cid, None)
            config.log(f"check limit {k}: bridge {ver} too old")
            return self.set_result(k, "fail", "Bridge update needed", f"v{ver} · browser restart karein · Not checked")
        self.set_result(k, "run", "Checking…", "Hark tab dekh raha hoon")
        self.ctl.check_limit(p, cid)
        QTimer.singleShot(45000, lambda: self._check_timeout(cid, k))

    def _check_timeout(self, cid, k):
        if self._checks.pop(cid, None):
            p = self.ctl.profile(k)
            if p and self.ctl.bridge_seen(p):
                self.set_result(k, "fail", "No answer", "Check Limit: Not checked")
            else:
                self.set_result(k, "fail", "Bridge disconnected", "Check Limit: Not checked")

    def on_limit(self, k, res):
        cid = res.get("id")
        if cid in self._checks:
            self._checks.pop(cid, None)
        tabs = [t for t in (res.get("tabs") or []) if t and not t.get("err")]
        lim = [t for t in tabs if t.get("reached")]
        if lim:
            reset = next((t["reset"] for t in lim if t.get("reset")), "")
            if reset[:5].lower() in ("today", "tomor"):
                reset = reset[0].lower() + reset[1:]
            out = ("limit", "Limit reached", f"Resets {reset}" if reset else "Reset time nahi dikha")
        elif any(t.get("loaded") and t.get("active") for t in tabs):
            out = ("ready", "Ready to work", "")
        elif any(t.get("signin") for t in tabs):
            out = ("fail", "Not signed in", "Hark sign-in page khula hai")
        elif any(t.get("loaded") for t in tabs):
            out = ("fail", "Not ready", "Message box active nahi")
        elif not tabs:
            out = ("fail", "Not ready", "Hark tab nahi mila")
        else:
            out = ("fail", "Not ready", "Hark chat load nahi hui")
        config.log(f"check limit {k}: {out[1]} {out[2]} tabs={[(t.get('url','')[:40], t.get('reached'), t.get('loaded'), t.get('active')) for t in tabs]}")
        self.set_result(k, *out)

    # ---------- Install Hark Bridge ----------
    def run_install(self, dirs):
        ps = [p for p in (self.ctl.profile(k) for k in dirs) if p]
        if not ps:
            return
        for p in ps:
            self.set_result(p.directory, "info", "Queued", "Bridge install")
        threading.Thread(target=self._install_all, args=(ps,), daemon=True).start()

    def _install_all(self, ps):
        from .. import installer
        emit = self.relay.result.emit
        for p in ps:
            k = p.directory
            self.ctl.no_dock.add(k)      # the Extensions window is driven on the desktop, docked again afterwards
            try:
                running = bool(self.ctl.windows.get(k))
                emit(k, "run", "Checking…", "Bridge dhoond raha hoon")
                if self.ctl.bridge_live(p) or chrome_mod.bridge_installed(p, running):
                    config.log(f"bridge install {k}: already installed, skipped")
                    emit(k, "ok", "Already installed", "Skip kiya")
                    continue
                ext = config.ensure_profile_extension(p.data_dir, p.tag)     # Cent managed profiles only
                t0 = time.time()
                emit(k, "run", "Installing…", "Extensions · Load unpacked")
                ok, msg = installer.install_bridge(p, ext, self.ctl.exe_for(p))
                if not ok:
                    emit(k, "fail", "Install failed", msg)
                    continue
                emit(k, "run", "Verifying…", "Bridge report ka intezar")
                verified = ""
                end = time.time() + 40
                paired = False
                while time.time() < end and not verified:
                    st = self.ctl.state.get(k)
                    if st and st["t"] > t0:
                        verified = "bridge connected"
                    else:
                        if not paired and time.time() - t0 > 6 and chrome_mod.bridge_installed(p, True):
                            paired = True       # installed but not reporting under this profile yet: pair it
                            self.ctl.pair_bridge(p)
                        time.sleep(1)
                if not verified and chrome_mod.bridge_installed(p, True):
                    verified = "installed (connection pending)"
                config.log(f"bridge install {k}: {msg}; verify={verified or 'FAILED'}")
                emit(k, "ok" if verified else "fail", "Bridge installed" if verified else "Not verified",
                     f"Verified: {verified}" if verified else "Bridge ne report nahi kiya")
            except Exception as e:
                config.log(f"bridge install {k}: {e}")
                emit(k, "fail", "Install failed", str(e)[:60])
            finally:
                self.ctl.no_dock.discard(k)

    def showEvent(self, e):
        super().showEvent(e)
        self._update_cols()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_cols()

    def add_profile(self):
        """Non-modal: one Add profile window at a time; if setup is running (even hidden) bring it back."""
        from .add_profile import AddProfileDialog
        d = getattr(self, "_add_dlg", None)
        if d is not None:
            try:
                d.showNormal()
                d.raise_()
                d.activateWindow()
                return
            except RuntimeError:      # already deleted
                self._add_dlg = None
        d = AddProfileDialog(self)
        d.busy.connect(self._add_busy)
        self._add_dlg = d
        d.show()

    def _add_busy(self, done, total):
        if done == -1:                # window closed
            self._add_dlg = None
            self.addbtn.setText("  Add profile")
            self.rebuild()
        elif done == -2:              # all jobs finished, window still open
            self.addbtn.setText("  Add profile · done")
        else:
            self.addbtn.setText(f"  Setup {done}/{total}…")
            self.addbtn.setToolTip("Add profile window: setup background mein chal raha hai")


# ======================= Alerts =======================
class AlertRow(QFrame):
    def __init__(self, page, a):
        super().__init__()
        self.page, self.a = page, a
        h = QHBoxLayout(self)
        h.setContentsMargins(24, 10, 18, 10)
        h.setSpacing(12)
        needs = a["status"] == "needs_reply"
        ic = IconLabel("chat", 30, ORANGE) if needs else IconLabel("checkcircle", 30, GREEN)
        h.addWidget(ic)
        name = label(a["profile_name"], size=18, bold=True)
        name.setFixedWidth(180)
        name.setToolTip(a.get("email", ""))
        h.addWidget(name)
        msg = label(a["question"], size=15, wrap=True)
        msg.setToolTip(f"{a['conv_title']}\n{a['question']}")
        msg.setMinimumWidth(200)
        h.addWidget(msg, 1)
        self.when = label(ago(a["received"]), size=15)
        self.when.setFixedWidth(110)
        h.addWidget(self.when)
        pill = QLabel()
        if needs:
            pill.setObjectName("pillNeeds")
            pill.setText("  Needs reply")
            ic2 = icons.pixmap("alertcircle", 18, "#B87500")
        else:
            pill.setObjectName("pillDone")
            pill.setText("  Resolved")
            ic2 = icons.pixmap("check", 18, GREEN)
        box = QHBoxLayout()
        box.setSpacing(0)
        wrap = QFrame()
        wrap.setObjectName("pillNeeds" if needs else "pillDone")
        wrap.setStyleSheet(f"#{wrap.objectName()}{{border-radius:8px;}}")
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(10, 5, 12, 5)
        icl = QLabel()
        icl.setPixmap(ic2)
        wl.addWidget(icl)
        t = QLabel("Needs reply" if needs else "Resolved")
        t.setStyleSheet("font-size:15px; background:transparent")
        wl.addWidget(t)
        wrap.setFixedWidth(150)
        h.addWidget(wrap)
        act = button("Open task", "outline") if needs else button("View task", "link")
        act.setFixedWidth(130)
        act.clicked.connect(lambda: page.ctl.open_task(a))
        h.addWidget(act)


class AlertsPage(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 10)
        v.setSpacing(18)
        v.addWidget(label("Alerts", obj="h1"))
        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.chips = []
        grp = QButtonGroup(self)
        for i, t in enumerate(["All alerts", "Needs reply", "Resolved"]):
            b = QPushButton(t)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setMinimumHeight(46)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("font-size:17px; padding: 8px 22px")
            grp.addButton(b, i)
            bar.addWidget(b)
            self.chips.append(b)
            b.clicked.connect(self.rebuild)
        self.chips[0].setChecked(True)
        bar.addStretch(1)
        self.search = SearchBox("Search alerts")
        self.search.setMinimumWidth(340)
        self.search.setMinimumHeight(48)
        self.search.textChanged.connect(self.rebuild)
        bar.addWidget(self.search)
        v.addLayout(bar)
        self.table = card()
        tv = QVBoxLayout(self.table)
        tv.setContentsMargins(0, 8, 0, 8)
        tv.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(30, 10, 18, 12)
        for txt, w in (("Profile", 222), ("Message", 0), ("Received", 110), ("Status", 150), ("Action", 130)):
            l = label(txt, size=18, bold=True)
            if w:
                l.setFixedWidth(w)
            head.addWidget(l, 0 if w else 1)
        tv.addLayout(head)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#E3E6EA")
        tv.addWidget(line)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.rows = QVBoxLayout(self.body)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.scroll.setWidget(self.body)
        tv.addWidget(self.scroll, 1)
        v.addWidget(self.table, 1)
        ctl.changed.connect(self.rebuild)
        self.t = QTimer(self, timeout=self.rebuild, interval=30000)
        self.t.start()
        self.rebuild()

    def select(self, idx):
        self.chips[idx].setChecked(True)
        self.rebuild()

    def rebuild(self):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        f = [b.isChecked() for b in self.chips].index(True)
        q = self.search.text().strip().lower()
        al = sorted(self.ctl.alerts.alerts, key=lambda a: (a["status"] != "needs_reply", -a["received"]))
        n = 0
        for a in al:
            if f == 1 and a["status"] != "needs_reply":
                continue
            if f == 2 and a["status"] != "resolved":
                continue
            if q and q not in (a["profile_name"] + " " + a["question"] + " " + a.get("conv_title", "")).lower():
                continue
            if n:
                line = QFrame()
                line.setFixedHeight(1)
                line.setStyleSheet("background:#ECEEF0; margin: 0 22px")
                self.rows.addWidget(line)
            self.rows.addWidget(AlertRow(self, a))
            n += 1
        if not n:
            e = label("Abhi koi alert nahi.", obj="muted")
            e.setContentsMargins(30, 20, 0, 20)
            self.rows.addWidget(e)
        self.rows.addStretch(1)


# ======================= Settings =======================
def row(text, widget, line=True):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 10, 0, 10)
    l = label(text, size=18)
    l.setMinimumWidth(110)
    h.addWidget(l)
    h.addSpacing(12)
    if isinstance(widget, (QLineEdit, QComboBox)) or widget.property("expand"):
        widget.setMinimumWidth(150)
        widget.setMaximumWidth(16777215)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        h.addWidget(widget, 1)
    else:
        h.addStretch(1)
        h.addWidget(widget)
    if line:
        w.setStyleSheet("QWidget#r{border-bottom:1px solid #ECEEF0}")
    return w


def section(title, sub=None):
    c = card()
    v = QVBoxLayout(c)
    v.setContentsMargins(24, 18, 24, 18)
    v.setSpacing(4)
    v.addWidget(label(title, size=25, bold=True))
    if sub:
        v.addWidget(label(sub, size=16, color="#5B616B"))
    return c, v


def hline():
    l = QFrame()
    l.setFixedHeight(1)
    l.setStyleSheet("background:#ECEEF0")
    return l


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 18)
        outer.setSpacing(14)
        outer.addWidget(label("Settings", obj="h1"))
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        outer.addWidget(self.banner)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setMinimumWidth(0)
        g = QGridLayout(body)
        g.setContentsMargins(0, 0, 4, 0)
        g.setSpacing(18)
        sc.setWidget(body)
        outer.addWidget(sc, 1)

        # Browser & previews
        c1, v1 = section("Browser & previews")
        self.exe = QLineEdit()
        self.exe.setPlaceholderText("Cent Browser ki chrome.exe (khali = khud dhoondo)")
        self.exe.setMinimumHeight(38)
        self.exe.textChanged.connect(self._exe_hint)
        self.browse = button("Browse…", "outline")
        self.browse.setMinimumHeight(38)
        self.browse.clicked.connect(self.pick_exe)
        exe_w = QWidget()
        eh = QHBoxLayout(exe_w)
        eh.setContentsMargins(0, 0, 0, 0)
        eh.setSpacing(8)
        eh.addWidget(self.exe, 1)
        eh.addWidget(self.browse)
        exe_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.exe_hint = label("", size=13, color="#5B616B")
        self.exe_hint.setWordWrap(True)
        self.refresh_cb = QComboBox()
        self.refresh_cb.addItems(["2 seconds", "5 seconds", "10 seconds", "30 seconds", "60 seconds"])
        self.refresh_cb.setMinimumWidth(150)
        self.t_prev = Toggle()
        v1.addSpacing(8)
        exe_w.setProperty("expand", True)
        v1.addWidget(row("Cent Browser Path", exe_w))
        v1.addWidget(self.exe_hint); v1.addWidget(hline())
        v1.addWidget(row("Preview refresh", self.refresh_cb)); v1.addWidget(hline())
        v1.addWidget(row("Show browser previews", self.t_prev))
        v1.addStretch(1)
        g.addWidget(c1, 0, 0)

        # Telegram
        c2, v2 = section("Telegram", config.BOT_USERNAME)
        self.t_tg = Toggle()
        st = QWidget()
        sh = QHBoxLayout(st)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.addWidget(self.t_tg)
        sh.addSpacing(14)
        self.tg_dot = Dot(GRAY, 12)
        self.tg_lbl = label("Not set up", size=16)
        sh.addWidget(self.tg_dot)
        sh.addWidget(self.tg_lbl)
        v2.addWidget(row("Telegram alerts", st)); v2.addWidget(hline())
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("Bot token paste karein")
        self.token.setMinimumWidth(150)
        self.token.setMinimumHeight(38)
        self.chat = QLineEdit()
        self.chat.setPlaceholderText("Chat ID")
        self.chat.setMinimumWidth(150)
        self.chat.setMinimumHeight(38)
        v2.addWidget(row("Bot token", self.token)); v2.addWidget(hline())
        v2.addWidget(row("Chat ID", self.chat)); v2.addWidget(hline())
        self.proxy_type = QComboBox()
        self.proxy_type.addItems(["Auto", "HTTP/HTTPS", "SOCKS5"])
        v2.addWidget(row("Proxy type", self.proxy_type)); v2.addWidget(hline())
        self.proxy = QLineEdit()
        self.proxy.setEchoMode(QLineEdit.Password)
        self.proxy.setPlaceholderText("IP:PORT:USERNAME:PASSWORD (sirf Telegram ke liye)")
        self.proxy.setMinimumHeight(38)
        v2.addWidget(row("Proxy", self.proxy))

        tb = QHBoxLayout()
        self.test_btn = button("Send test alert", "outline")
        self.test_btn.setMinimumSize(200, 44)
        self.test_btn.setStyleSheet("font-size:17px")
        self.test_btn.clicked.connect(self.send_test)
        tb.addStretch(1); tb.addWidget(self.test_btn); tb.addStretch(1)
        v2.addLayout(tb)
        self.test_res = label("", size=13, color="#5B616B")
        self.test_res.setAlignment(Qt.AlignCenter)
        self.test_res.setWordWrap(True)
        self.test_res.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v2.addWidget(self.test_res)
        g.addWidget(c2, 0, 1)

        # Startup & taskbar
        c3, v3 = section("Startup & taskbar")
        self.t_start, self.t_tray, self.t_restore = Toggle(), Toggle(), Toggle()
        self.t_dock = Toggle()
        self.t_dock.setAccessibleName("Keep Cent windows docked")
        v3.addSpacing(8)
        v3.addWidget(row("Start with Windows", self.t_start)); v3.addWidget(hline())
        v3.addWidget(row("Minimize to tray", self.t_tray)); v3.addWidget(hline())
        v3.addWidget(row("Restore last view", self.t_restore)); v3.addWidget(hline())
        v3.addWidget(row("Keep Cent windows docked", self.t_dock))
        g.addWidget(c3, 1, 0)

        # Desktop notifications
        c4, v4 = section("Desktop notifications")
        self.t_desk, self.t_click, self.t_sound = Toggle(), Toggle(), Toggle()
        v4.addSpacing(8)
        v4.addWidget(row("Needs reply alerts", self.t_desk)); v4.addWidget(hline())
        v4.addWidget(row("Open task on alert click", self.t_click)); v4.addWidget(hline())
        v4.addWidget(row("Play alert sound", self.t_sound))
        g.addWidget(c4, 1, 1)

        # Shared Repo Team
        c5, v5 = section("Shared Repo Team", "Planner + workers on one local git repo, a worktree per worker")
        self.team_lbl = label("", size=14, color="#5B616B")
        self.team_lbl.setWordWrap(True)
        self.team_lbl.setAccessibleName("Shared Repo Team summary")
        v5.addWidget(self.team_lbl)
        tb5 = QHBoxLayout()
        self.team_btn = button("Edit team…", "outline")
        self.team_btn.setMinimumHeight(40)
        self.team_btn.setAccessibleName("Edit Shared Repo Team")
        self.team_btn.clicked.connect(self.edit_team)
        tb5.addWidget(self.team_btn)
        tb5.addStretch(1)
        v5.addLayout(tb5)
        g.addWidget(c5, 2, 0, 1, 2)
        ctl.team_changed.connect(self._team_summary)
        for c in (c1, c2, c3, c4, c5):
            c.setMinimumWidth(0)
            c.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.cancel = button("Cancel", "plain")
        self.cancel.setMinimumSize(170, 48)
        self.cancel.setStyleSheet("font-size:18px")
        self.save_b = button("Save changes", "primary")
        self.save_b.setMinimumSize(210, 48)
        self.save_b.setStyleSheet("font-size:18px")
        self.cancel.clicked.connect(self.load)
        self.save_b.clicked.connect(self.save)
        btns.addWidget(self.cancel)
        btns.addSpacing(10)
        btns.addWidget(self.save_b)
        outer.addLayout(btns)
        ctl.telegram_status.connect(self.on_tg)
        self.load()

    def load(self):
        s = self.ctl.settings
        self.exe.setText(s["browser_exe"] or "")
        self._exe_hint()
        self.refresh_cb.setCurrentText(f"{s['preview_refresh']} seconds")
        self.t_prev.setChecked(bool(s["show_previews"]))
        self.t_tg.setChecked(bool(s["telegram_enabled"]))
        tok = config.get_bot_token()
        self._token_saved = bool(tok)
        self.token.clear()
        self.token.setPlaceholderText("•••••••• (saved)" if tok else "Bot token paste karein")
        self.chat.setText(str(s["telegram_chat_id"] or config.DEFAULT_CHAT_ID))
        self.proxy_type.setCurrentText(s["telegram_proxy_type"] or "Auto")
        self.proxy.clear()
        try:
            px = config.load_proxy(s)
        except Exception:
            px = None
        self.proxy.setPlaceholderText(("•••• saved: " + notify.mask_proxy(px)) if px else "IP:PORT:USERNAME:PASSWORD (sirf Telegram ke liye)")
        try:
            self.t_start.setChecked(config.get_start_with_windows())
        except Exception:
            self.t_start.setChecked(bool(s["start_with_windows"]))
        self.t_tray.setChecked(bool(s["minimize_to_tray"]))
        self.t_restore.setChecked(bool(s["restore_last_view"]))
        self.t_dock.setChecked(bool(s["keep_docked"]))
        self.t_desk.setChecked(bool(s["desktop_alerts"]))
        self.t_click.setChecked(bool(s["open_task_on_click"]))
        self.t_sound.setChecked(bool(s["play_sound"]))
        self.test_res.setText("")
        self.on_tg(self.ctl.tg_ok, self.ctl.tg_msg)
        ok, err = self.ctl.cent_status()
        if not ok:
            self.banner.setText("\u26A0 Cent Browser: " + err)
            self.banner.setAccessibleName("Cent Browser error")
            self.banner.show()
        self._team_summary()

    def _team_summary(self):
        from .. import repo_team
        t = self.ctl.team()
        if not repo_team.is_configured(t):
            self.team_lbl.setText("Team set nahi. Edit team… se repo, Planner aur workers chunein.")
            return
        names = {p.tag: p.name for p in self.ctl.profiles}
        pl = t.get("planner") or {}
        ws = ", ".join(f"{w['alias']} = {names.get(w.get('profile_id'), w.get('profile_id'))}" for w in t["workers"])
        self.team_lbl.setText(f"Repo: {t['repo_path']}  •  base {t.get('base_ref')} ({(t.get('base_sha') or '')[:10]})\n"
                              f"Planner: {names.get(pl.get('profile_id'), '-')}  •  Workers: {ws}")

    def edit_team(self):
        from .team_dispatch import open_team_dialog
        open_team_dialog(self.ctl, self)

    def pick_exe(self):
        start = os.path.dirname(self.exe.text().strip()) or chrome_mod.detect_cent() or os.environ.get("LOCALAPPDATA", "C:\\")
        f, _ = QFileDialog.getOpenFileName(self, "Cent Browser chrome.exe chunein", start, "Cent Browser (chrome.exe)")
        if f:
            self.exe.setText(os.path.normpath(f))

    def _exe_hint(self):
        exe = self.exe.text().strip().strip('"')
        if not exe:
            found = chrome_mod.detect_cent()
            self.exe_hint.setText(f"Khud mila: {found}" if found else "\u26A0 Cent Browser nahi mila. Browse karke Cent ki chrome.exe chunein.")
        elif not os.path.isfile(exe):
            self.exe_hint.setText("\u26A0 Cent Browser exe nahi mili (file missing). Sahi Cent chrome.exe chunein.")
        elif not chrome_mod.is_cent_exe(exe):
            self.exe_hint.setText("\u26A0 Yeh Cent Browser nahi hai. Sirf Cent Browser supported hai (Chrome, Edge, Brave nahi).")
        else:
            self.exe_hint.setText(f"Cent Browser. Har profile ka alag folder: {config.browser_profiles_dir()}")

    def current_token(self):
        return self.token.text().strip() or config.get_bot_token()

    def save(self):
        s = self.ctl.settings
        exe = self.exe.text().strip().strip('"')
        if exe and not chrome_mod.is_cent_exe(exe):
            QMessageBox.warning(self, "Cent Browser Path", "Yeh Cent Browser ki chrome.exe nahi hai. Sirf Cent Browser supported hai.")
            return
        s["browser_exe"] = exe
        s["default_browser"] = "Cent Browser"
        s["preview_refresh"] = int(self.refresh_cb.currentText().split()[0])
        s["show_previews"] = self.t_prev.isChecked()
        s["telegram_enabled"] = self.t_tg.isChecked()
        s["telegram_chat_id"] = self.chat.text().strip()
        if not self._apply_proxy():
            return
        if self.token.text().strip():
            config.set_bot_token(self.token.text().strip())
        s["start_with_windows"] = self.t_start.isChecked()
        try:
            config.set_start_with_windows(self.t_start.isChecked())
        except Exception as e:
            QMessageBox.warning(self, "Startup", f"Windows startup set nahi ho saka: {e}")
        s["minimize_to_tray"] = self.t_tray.isChecked()
        s["restore_last_view"] = self.t_restore.isChecked()
        s["keep_docked"] = self.t_dock.isChecked()
        s["desktop_alerts"] = self.t_desk.isChecked()
        s["open_task_on_click"] = self.t_click.isChecked()
        s["play_sound"] = self.t_sound.isChecked()
        s.save()
        self.ctl.reload_profiles()        # Cent path may have changed: only profiles of that Cent are listed
        ok, err = self.ctl.cent_status()
        if not ok:
            QMessageBox.warning(self, "Cent Browser Path", err)
        self.ctl.apply_preview_interval()
        self.banner.hide()
        self.load()
        self.ctl.check_telegram()
        self.saved.emit()
        self.test_res.setText("Settings save ho gayin.")

    def send_test(self):
        tok, chat = self.current_token(), self.chat.text().strip()
        if not tok or not chat:
            self.ask_secure("Test alert ke liye Bot Token aur Chat ID dono chahiye.")
            return
        # keep what was typed: token to Credential Manager, chat id to settings
        if self.token.text().strip():
            config.set_bot_token(self.token.text().strip())
            self.token.clear()
            self.token.setPlaceholderText("•••••••• (saved)")
        if not self._apply_proxy():
            self.test_btn.setEnabled(True)
            return
        if True:
            self.ctl.settings["telegram_chat_id"] = chat
            self.ctl.settings.save()
        tok = config.get_bot_token()
        self.banner.hide()
        self.test_btn.setEnabled(False)
        self.test_res.setText("getMe check kar raha hoon, phir test bhej raha hoon…")

        def done(ok, info):
            self.test_btn.setEnabled(True)
            self.test_res.setText(("✓ " + info) if ok else ("✗ Nahi gaya: " + info))
            self.on_tg(ok, "Telegram connected" if ok else "Telegram error: " + info)
        self.ctl.send_test(tok, chat, done)

    def _apply_proxy(self):
        """Validates the proxy field, stores it in Credential Manager and applies it to Telegram calls only."""
        s = self.ctl.settings
        s["telegram_proxy_type"] = self.proxy_type.currentText()
        txt = self.proxy.text().strip()
        try:
            if txt:
                px = notify.parse_proxy(txt, s["telegram_proxy_type"])
                if px["type"] == "auto":
                    px["type"] = notify.detect_type(px)
                    s["telegram_proxy_type"] = "SOCKS5" if px["type"] == "socks5" else "HTTP/HTTPS"
                    self.proxy_type.setCurrentText(s["telegram_proxy_type"])
                config.set_proxy_text(txt)
                self.proxy.clear()
                config.log(f"telegram proxy saved: {notify.mask_proxy(px)}")
            s.save()
            px = config.load_proxy(s)
            self.proxy.setPlaceholderText(("•••• saved: " + notify.mask_proxy(px)) if px else "IP:PORT:USERNAME:PASSWORD (sirf Telegram ke liye)")
            return True
        except Exception as e:
            self.test_res.setText("✗ Proxy: " + notify.scrub(e))
            return False

    def clear_proxy(self):
        config.set_proxy_text("")
        config.load_proxy(self.ctl.settings)
        self.load()

    def ask_secure(self, msg):
        self.banner.setText("🔒 " + msg + " Neeche secure field mein paste karke Save changes dabayein. Token Windows Credential Manager mein save hota hai.")
        self.banner.show()
        if not self.current_token():
            self.token.setFocus()
        elif not self.chat.text().strip():
            self.chat.setFocus()

    def on_tg(self, ok, msg):
        self.tg_dot.set_color(GREEN if ok else (ORANGE if "error" in msg.lower() else GRAY))
        self.tg_lbl.setText("Connected" if ok else ("Error" if "error" in msg.lower() else "Not set up"))
        self.tg_lbl.setToolTip(msg)
