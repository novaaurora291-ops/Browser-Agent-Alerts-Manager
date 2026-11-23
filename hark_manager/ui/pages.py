import os
import time

from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton,
                               QScrollArea, QFrame, QMenu, QInputDialog, QButtonGroup, QLineEdit, QMessageBox,
                               QSizePolicy, QApplication, QFileDialog)

from . import icons
from .widgets import card, label, button, StatusLabel, Toggle, SearchBox, Preview, IconLabel, Dot
from .style import ORANGE, GREEN, GRAY, LIME
from .. import config, notify
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
class ProfileCard(QFrame):
    def __init__(self, page, p):
        super().__init__()
        self.page, self.p = page, p
        self.setObjectName("card")
        self.setMinimumSize(260, 345)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.name = label(p.name, size=21, bold=True)
        self.name.setToolTip(f"{p.browser_name} • {p.email or 'not signed in'} • {p.data_dir if p.managed else p.directory}")
        top.addWidget(self.name, 1)
        self.dots = QPushButton("•••")
        self.dots.setObjectName("dots")
        self.dots.setCursor(Qt.PointingHandCursor)
        self.dots.clicked.connect(self.menu)
        top.addWidget(self.dots)
        v.addLayout(top)
        self.status = StatusLabel("Idle")
        v.addWidget(self.status)
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
        self.setObjectName("cardAlert" if s == "Needs reply" else "card")
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
        m.addAction("Focus window", lambda: c.focus_profile(self.p))
        m.addAction("Open Hark", lambda: c.open_in(self.p, "https://hark.com/chat"))
        m.addSeparator()
        m.addAction("Rename…", self.rename)
        if not self.p.managed:
            m.addAction("Install Hark bridge…", lambda: self.page.install_bridge.emit(self.p.directory))
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


class ProfileRow(QFrame):
    def __init__(self, page, p):
        super().__init__()
        self.page, self.p = page, p
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
        col.addWidget(self.name)
        col.addWidget(label(p.email or "Not signed in", obj="muted"))
        h.addLayout(col, 2)
        self.status = StatusLabel("Idle")
        h.addWidget(self.status, 1)
        self.action = button("Focus window", "outline", "window")
        self.action.setMinimumWidth(160)
        self.action.clicked.connect(lambda: ProfileCard.act(self))
        h.addWidget(self.action)

    def refresh(self):
        c = self.page.ctl
        s = c.status(self.p)
        self.name.setText(self.p.name)
        self.status.set_status(s)
        open_ = bool(c.windows.get(self.p.directory))
        self.action.setText("Focus window" if open_ else "Launch")
        self.action.setIcon(icons.icon("window" if open_ else "play", 18))
        self.prev.set_png(c.previews.get(self.p.directory) if open_ and c.settings["show_previews"] else None, "" if open_ else "")


def short_browser(name):
    return "Chrome" if name == "Google Chrome" else name


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

    def launch(self):
        self.action.setEnabled(False)
        self.action.setText("Opening…")
        self.page.ctl.launch(self.p)

    def refresh(self):
        self.name.setText(self.p.name)


class ProfilesPage(QWidget):
    install_bridge = Signal(str)

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.items = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 10)
        v.setSpacing(18)
        v.addWidget(label("Browser profiles", obj="h1"))
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
        self.filter = QComboBox()
        self.filter.addItems(["All statuses", "Working", "Needs reply", "Idle", "Closed"])
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
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)
        v.addWidget(self.scroll, 1)
        self.cols = 3
        ctl.changed.connect(self.refresh)
        ctl.previews_changed.connect(self.refresh)
        self.rebuild()

    def set_tab(self, t):
        self.tab = t
        (self.t_open if t == "open" else self.t_closed).setChecked(True)
        self.filter.setVisible(t == "open")
        self.seg.setVisible(t == "open")
        self.rebuild()

    def is_open(self, p):
        # any window of the profile counts, minimized ones too
        return bool(self.ctl.windows.get(p.directory))

    def set_mode(self, m):
        self.ctl.settings["view_mode"] = m
        self.ctl.settings.save()
        self.cols = self._calc_cols()
        self.rebuild()

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
        rank = {"Needs reply": 0, "Working": 1, "Idle": 2, "Closed": 3}
        return sorted(out, key=lambda p: rank.get(self.ctl.status(p), 9))

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
        ps = self.visible_profiles()
        self._sig = (self.tab, [p.directory for p in ps])
        self.update_counts()
        if self.tab == "closed":
            return self._build_closed(ps)
        for i, p in enumerate(ps):
            if mode == "list":
                w = ProfileRow(self, p)
                self.grid.addWidget(w, i, 0)
            else:
                w = ProfileCard(self, p)
                self.grid.addWidget(w, i // self.cols, i % self.cols)
            self.items[p.directory] = w
            w.refresh()
        if not ps:
            self.grid.addWidget(label("Koi profile khula nahi. Closed tab se Launch karein." if self.tab == "open" and not self.search.text().strip() else "Koi profile nahi mila.", obj="muted"), 0, 0)
        ncols = self.cols if mode == "grid" else 1
        for c in range(8):
            # only real columns stretch (equal widths); empty columns must not eat space
            self.grid.setColumnStretch(c, 1 if c < ncols else 0)
        self.grid.setRowStretch(self.grid.rowCount(), 1)

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
        hh.insertSpacing(0, 0)
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

    CARD_MIN = 260
    GAP = 18

    def _calc_cols(self):
        w = self.scroll.viewport().width() - 6  # grid right margin
        return max(1, min(4, (w + self.GAP) // (self.CARD_MIN + self.GAP)))

    def _layout_bar(self, narrow):
        if narrow == self._bar_narrow:
            return
        self._bar_narrow = narrow
        for w in (self.search, self.filter, self.seg, self.addbtn):
            self.bar.removeWidget(w)
        for c in range(5):
            self.bar.setColumnStretch(c, 0)
        if narrow:  # two rows: search full width, then filter + view + add
            self.bar.addWidget(self.search, 0, 0, 1, 4)
            self.bar.addWidget(self.filter, 1, 0)
            self.bar.addWidget(self.seg, 1, 1)
            self.bar.addWidget(self.addbtn, 1, 3)
            self.bar.setColumnStretch(0, 1)
            self.bar.setColumnStretch(2, 0)
        else:
            self.bar.addWidget(self.search, 0, 0)
            self.bar.addWidget(self.filter, 0, 1)
            self.bar.addWidget(self.seg, 0, 2)
            self.bar.addWidget(self.addbtn, 0, 4)
            self.bar.setColumnStretch(0, 3)
            self.bar.setColumnStretch(1, 2)
            self.bar.setColumnStretch(3, 1)

    def _update_cols(self):
        self._layout_bar(self.width() < 900)
        cols = self._calc_cols()
        if cols != self.cols:
            self.cols = cols
            if self.ctl.settings["view_mode"] == "grid":
                self.rebuild()

    def eventFilter(self, obj, e):
        if obj is self.scroll.viewport() and e.type() == QEvent.Resize:
            self._update_cols()
        return super().eventFilter(obj, e)

    def showEvent(self, e):
        super().showEvent(e)
        self._update_cols()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_cols()

    def add_profile(self):
        from .add_profile import AddProfileDialog
        AddProfileDialog(self).exec()
        self.rebuild()


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
        self.exe.setPlaceholderText("Khali = Google Chrome")
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
        v1.addWidget(row("Browser EXE Path", exe_w))
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
        v3.addSpacing(8)
        v3.addWidget(row("Start with Windows", self.t_start)); v3.addWidget(hline())
        v3.addWidget(row("Minimize to tray", self.t_tray)); v3.addWidget(hline())
        v3.addWidget(row("Restore last view", self.t_restore))
        g.addWidget(c3, 1, 0)

        # Desktop notifications
        c4, v4 = section("Desktop notifications")
        self.t_desk, self.t_click, self.t_sound = Toggle(), Toggle(), Toggle()
        v4.addSpacing(8)
        v4.addWidget(row("Needs reply alerts", self.t_desk)); v4.addWidget(hline())
        v4.addWidget(row("Open task on alert click", self.t_click)); v4.addWidget(hline())
        v4.addWidget(row("Play alert sound", self.t_sound))
        g.addWidget(c4, 1, 1)
        for c in (c1, c2, c3, c4):
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
        self.t_desk.setChecked(bool(s["desktop_alerts"]))
        self.t_click.setChecked(bool(s["open_task_on_click"]))
        self.t_sound.setChecked(bool(s["play_sound"]))
        self.test_res.setText("")
        self.on_tg(self.ctl.tg_ok, self.ctl.tg_msg)

    def pick_exe(self):
        start = os.path.dirname(self.exe.text().strip()) or chrome_mod.detect_cent() or os.environ.get("LOCALAPPDATA", "C:\\")
        f, _ = QFileDialog.getOpenFileName(self, "Browser EXE chunein", start, "Browser (*.exe)")
        if f:
            self.exe.setText(os.path.normpath(f))

    def _exe_hint(self):
        exe = self.exe.text().strip().strip('"')
        if not exe:
            self.exe_hint.setText("Add profile: Google Chrome ke andar naya profile (purana tareeqa).")
        elif os.path.isfile(exe):
            self.exe_hint.setText(f"Add profile: {chrome_mod.browser_name(exe)}, har profile ka alag folder: {config.browser_profiles_dir()}")
        else:
            self.exe_hint.setText("Yeh file nahi mili.")

    def current_token(self):
        return self.token.text().strip() or config.get_bot_token()

    def save(self):
        s = self.ctl.settings
        exe = self.exe.text().strip().strip('"')
        if exe and not (os.path.isfile(exe) and exe.lower().endswith(".exe")):
            QMessageBox.warning(self, "Browser EXE Path", "Yeh .exe file nahi mili. Browse karke browser ki exe chunein.")
            return
        s["browser_exe"] = exe
        s["default_browser"] = chrome_mod.browser_name(exe) if exe else "Google Chrome"
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
        s["desktop_alerts"] = self.t_desk.isChecked()
        s["open_task_on_click"] = self.t_click.isChecked()
        s["play_sound"] = self.t_sound.isChecked()
        s.save()
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
