from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QStackedWidget,
                               QScrollArea, QSystemTrayIcon, QMenu, QApplication, QMessageBox)

from . import icons
from .pages import ProfilesPage, AlertsPage, SettingsPage
from .widgets import card, label, button, IconLabel, Dot
from .style import build_qss, ORANGE, GREEN, GRAY
from .. import config
from ..config import APP_TITLE


class NavButton(QPushButton):
    def __init__(self, text, ic):
        super().__init__("  " + text)
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setIcon(icons.icon(ic, 24))
        self.setProperty("hbm_icon", f"{ic}:24")
        self.setIconSize(QSize(24, 24))
        self.setMinimumHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("font-size:19px")


class NeedsReplyPanel(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.setObjectName("rightPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(340)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 22, 16, 18)
        v.setSpacing(14)
        v.addWidget(label("Needs reply", obj="h1"))
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        self.body = QWidget()
        self.list = QVBoxLayout(self.body)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(14)
        sc.setWidget(self.body)
        v.addWidget(sc, 1)
        foot = QHBoxLayout()
        self.dot = Dot(GRAY, 14)
        self.tg = label("Telegram not set up", size=17)
        foot.addSpacing(8)
        foot.addWidget(self.dot)
        foot.addSpacing(6)
        foot.addWidget(self.tg)
        foot.addStretch(1)
        v.addLayout(foot)
        ctl.changed.connect(self.rebuild)
        ctl.telegram_status.connect(self.on_tg)
        self.rebuild()

    def on_tg(self, ok, msg):
        self.dot.set_color(GREEN if ok else (ORANGE if "error" in msg.lower() else GRAY))
        self.tg.setText(msg if len(msg) < 34 else msg[:32] + "…")
        self.tg.setToolTip(msg)

    def rebuild(self):
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        al = sorted(self.ctl.alerts.open_alerts(), key=lambda a: -a["received"])
        for a in al:
            c = card()
            h = QHBoxLayout(c)
            h.setContentsMargins(16, 16, 16, 16)
            h.setSpacing(12)
            ic = IconLabel("chat", 34, ORANGE)
            ic.setAlignment(Qt.AlignTop)
            col0 = QVBoxLayout()
            col0.addWidget(ic)
            col0.addStretch(1)
            h.addLayout(col0)
            col = QVBoxLayout()
            col.setSpacing(6)
            col.addWidget(label(a["profile_name"], size=21, bold=True))
            q = label(a["question"], size=16, wrap=True)
            q.setToolTip(a["conv_title"])
            col.addWidget(q)
            p = self.ctl.profile(a["profile_dir"])
            if p and self.ctl.status(p) == "Unknown":
                # the last Refresh got no fresh answer from this profile: the question may be answered by now
                u = label("Status: Unknown (last Refresh: no fresh answer)", obj="muted", wrap=True)
                u.setAccessibleName(f"Needs reply status {a['profile_name']}: Unknown")
                col.addWidget(u)
            b = button("Open task", "outline")
            b.setMinimumHeight(42)
            b.setStyleSheet("font-size:17px")
            b.clicked.connect(lambda _=False, a=a: self.ctl.open_task(a))
            col.addSpacing(4)
            col.addWidget(b)
            h.addLayout(col, 1)
            self.list.addWidget(c)
        if not al:
            e = label("Sab clear hai. Jab koi Hark agent jawab maangega, yahan dikhega.", obj="muted", wrap=True)
            e.setContentsMargins(6, 4, 6, 4)
            self.list.addWidget(e)
        self.list.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self, ctl, start_minimized=False):
        super().__init__()
        self.ctl = ctl
        self.quitting = False
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(icons.icon("logo", 32))
        self.resize(1680, 980)
        self.setMinimumSize(1180, 720)
        self.setStyleSheet(build_qss())
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = self.side = QWidget()
        side.setObjectName("sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        side.setFixedWidth(228)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 40, 14, 14)
        sv.setSpacing(8)
        self.navs = [NavButton("Profiles", "grid"), NavButton("Alerts", "bell"), NavButton("Settings", "gear")]
        for i, b in enumerate(self.navs):
            b.clicked.connect(lambda _=False, i=i: self.go(i))
            sv.addWidget(b)
        self.badge = QLabel("0", self.navs[1])
        self.badge.setObjectName("badge")
        self.badge.move(160, 15)
        sv.addStretch(1)
        h.addWidget(side)

        self.stack = QStackedWidget()
        self.stack.setObjectName("content")
        self.profiles = ProfilesPage(ctl)
        self.alerts = AlertsPage(ctl)
        self.settings = SettingsPage(ctl)
        from .browser_view import BrowserView
        self.view = BrowserView(ctl)
        for p in (self.profiles, self.alerts, self.settings, self.view):
            self.stack.addWidget(p)
        h.addWidget(self.stack, 1)
        self.right = self._build_right(ctl)
        h.addWidget(self.right)

        self.mv = None
        from .dock import Dock
        self.dock = Dock(ctl, self)
        self.dock.mv_open = lambda: self.mv is not None and self.mv.isVisible()
        self.view.dock = self.dock
        QApplication.instance().aboutToQuit.connect(self.dock.release_all)
        self.profiles.open_multiview.connect(self.open_multiview)
        self.profiles.install_bridge.connect(self.install_bridge)
        self.profiles.open_view.connect(lambda d: ctl.view_profile(ctl.profile(d)) if ctl.profile(d) else None)
        ctl.view_request.connect(self.show_view)
        self.view.back.connect(lambda: self.go(0))
        self.view.expand_toggled.connect(self.set_expanded)
        ctl.changed.connect(self.update_badge)
        ctl.new_alert.connect(self.on_new_alert)
        ctl.need_telegram_setup.connect(self.open_secure_input)
        self.update_badge()

        # tray
        self.tray = QSystemTrayIcon(icons.icon("logo", 32), self)
        self.tray.setToolTip(APP_TITLE)
        m = QMenu()
        m.addAction("Open Hark Browser Manager", self.show_normal)
        m.addAction("Alerts", lambda: (self.show_normal(), self.go(1)))
        m.addSeparator()
        m.addAction("Quit", self.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_normal() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.messageClicked.connect(self.on_message_clicked)
        self.tray.show()
        self.last_alert = None

        s = ctl.settings
        self.go(int(s["last_view"]) if s["restore_last_view"] else 0)
        if not ctl.bridge_ok:
            QTimer.singleShot(800, lambda: QMessageBox.warning(self, APP_TITLE, f"Bridge port {config.BRIDGE_PORT} busy hai: {ctl.bridge.error}\nKya app pehle se chal rahi hai?"))

    def _build_right(self, ctl):
        """Right panel: Needs reply, and the Team Plan & Safe Dispatch drawer (switch with the two tabs on top)."""
        from .team_dispatch import TeamDispatchPanel
        box = QWidget()
        box.setObjectName("rightPanel")
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setFixedWidth(390)
        v = QVBoxLayout(box)
        v.setContentsMargins(10, 10, 10, 0)
        v.setSpacing(0)
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.rt_needs = QPushButton("Needs reply")
        self.rt_team = QPushButton("Team Dispatch")
        for b, rad in ((self.rt_needs, "border-top-left-radius:9px;border-bottom-left-radius:9px;"),
                       (self.rt_team, "border-top-right-radius:9px;border-bottom-right-radius:9px;")):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(36)
            b.setStyleSheet("font-size:15px;" + rad)
            tabs.addWidget(b, 1)
        self.rt_needs.setAccessibleName("Needs reply tab")
        self.rt_team.setAccessibleName("Team Dispatch tab")
        v.addLayout(tabs)
        self.right_stack = QStackedWidget()
        self.needs = NeedsReplyPanel(ctl)
        self.needs.setMinimumWidth(0)
        self.needs.setMaximumWidth(16777215)
        self.team = TeamDispatchPanel(ctl)
        self.right_stack.addWidget(self.needs)
        self.right_stack.addWidget(self.team)
        v.addWidget(self.right_stack, 1)

        def show(i):
            self.right_stack.setCurrentIndex(i)
            self.rt_needs.setChecked(i == 0)
            self.rt_team.setChecked(i == 1)
            ctl.settings["right_drawer"] = i
            ctl.settings.save()
        self.rt_needs.clicked.connect(lambda: show(0))
        self.rt_team.clicked.connect(lambda: show(1))
        i = int(ctl.settings.get("right_drawer", 0) or 0)
        self.right_stack.setCurrentIndex(i)
        self.rt_needs.setChecked(i == 0)
        self.rt_team.setChecked(i == 1)
        return box

    def go(self, i):
        if self.stack.currentIndex() == 3 and i != 3:
            # leaving the live view (Back or a nav click): the window stays docked and running, out of sight
            if self.view.expanded:
                self.view.toggle_expand()
            self.view.release()
            self.view.dir = None
        for j, b in enumerate(self.navs):
            b.setChecked(i == j or (i == 3 and j == 0))
        self.stack.setCurrentIndex(i)
        if i != 3:
            self.ctl.settings["last_view"] = i
            self.ctl.settings.save()

    def open_multiview(self):
        if self.mv is None:
            from .multiview import MultiView
            self.mv = MultiView(self.ctl, self._release_main, self.dock)
            QApplication.instance().aboutToQuit.connect(self.mv.release_all)
        self.mv.open()

    def _release_main(self, directory):
        """The Multi-View takes a profile the single live view is showing: hand it over."""
        if self.stack.currentIndex() == 3 and self.view.dir == directory:
            self.go(0)

    def show_view(self, directory, prefer=""):
        if self.mv is not None and self.mv.isVisible():
            for pane in self.mv.panes:
                if pane.dir == directory and pane.hwnd:
                    # already live in the Multi-View: bring that pane up instead of moving the window
                    self.mv.open()
                    if self.mv.expanded is not pane:
                        self.mv.toggle_expand(pane)
                    return
        if not self.isVisible() or self.isMinimized():
            self.show_normal()
        self.go(3)
        self.view.show_profile(directory, prefer)

    def set_expanded(self, on):
        self.side.setVisible(not on)
        self.right.setVisible(not on)

    def update_badge(self):
        n = self.ctl.needs_reply_count()
        self.badge.setText(str(n))
        self.badge.setVisible(n > 0)
        self.tray.setToolTip(f"{APP_TITLE} — {n} needs reply") if hasattr(self, "tray") else None

    def on_new_alert(self, a):
        s = self.ctl.settings
        self.last_alert = a
        if s["desktop_alerts"]:
            self.tray.showMessage(f"Hark: {a['profile_name']} jawab maang raha hai", a["question"][:240], icons.icon("chat", 32, ORANGE), 15000)
            a["desktop"] = True
            self.ctl.alerts.save()
        if s["play_sound"]:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                QApplication.beep()

    def on_message_clicked(self):
        a = self.last_alert
        if a and self.ctl.settings["open_task_on_click"]:
            self.ctl.open_task(a)
        else:
            self.show_normal()
            self.go(1)

    def open_secure_input(self, msg):
        self.show_normal()
        self.go(2)
        self.settings.ask_secure(msg)

    def install_bridge(self, directory):
        self.profiles.run_install([directory])

    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, e):
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.WindowStateChange and self.isMinimized() and self.ctl.settings["minimize_to_tray"]:
            if self.stack.currentIndex() == 3:
                self.go(0)
            QTimer.singleShot(0, self.hide)
        super().changeEvent(e)

    def closeEvent(self, e):
        if self.stack.currentIndex() == 3:
            self.go(0)  # never let a hosted browser window close with the Manager
        if not self.quitting and self.ctl.settings["minimize_to_tray"]:
            e.ignore()
            self.hide()
            self.tray.showMessage(APP_TITLE, "Tray mein chal raha hai — alerts aate rahenge.", icons.icon("logo", 32), 3000)
            return
        self.quitting = True
        if self.mv is not None and self.mv.isVisible():
            self.mv.close()
        self.dock.release_all()  # every docked Cent window back to the desktop: Windows would close it with us
        self.tray.hide()
        self.ctl.bridge.stop()
        e.accept()
        QApplication.quit()

    def quit(self):
        self.quitting = True
        self.close()
