from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFrame, QWidget,
                               QButtonGroup, QMessageBox)

from . import icons
from .widgets import label, button
from .style import LIME, LIME_SOFT, LIME_BORDER, BORDER, MUTED, ORANGE, GREEN
from .. import chrome as chrome_mod
from ..setup import ProfileSetup

DLG_QSS = f"""
QDialog {{ background: white; }}
QLabel#flabel {{ font-size: 16px; font-weight: 700; }}
QLineEdit {{ min-height: 30px; font-size: 15px; }}
QLineEdit:read-only {{ background: #EEF0F2; }}
QPushButton#mode {{ background: white; border: 1.5px solid #D6DADF; border-radius: 9px; padding: 10px 14px; font-size: 15px; text-align: left; }}
QPushButton#mode:checked {{ background: {LIME_SOFT}; border-color: {LIME_BORDER}; font-weight: 600; }}
QFrame#steps {{ background: #FAFBFB; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#num {{ border: 1.5px solid #CDD1D6; border-radius: 15px; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; font-weight: 700; qproperty-alignment: AlignCenter; background: white; }}
"""

COLORS = {"wait": MUTED, "run": "#1f6feb", "ok": GREEN, "user": "#B26A00", "fail": "#C62828", "skip": MUTED}
MARK = {"ok": "✓", "fail": "!", "user": "…", "run": "", "wait": "", "skip": "–"}


class StepRow(QWidget):
    def __init__(self, n, title, sub):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 6, 0, 6)
        h.setSpacing(14)
        self.n = n
        self.num = QLabel(str(n))
        self.num.setObjectName("num")
        h.addWidget(self.num, 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(2)
        v.addWidget(label(title, size=17, bold=True))
        self.sub = label(sub, wrap=True, color=MUTED)
        self.sub.setStyleSheet(f"font-size:14px;color:{MUTED}")
        v.addWidget(self.sub)
        h.addLayout(v, 1)

    def set_state(self, state, text):
        c = COLORS.get(state, MUTED)
        self.sub.setText(text)
        self.sub.setStyleSheet(f"font-size:14px;color:{c}")
        mk = MARK.get(state, "")
        self.num.setText(mk or str(self.n))
        bg = {"ok": LIME, "fail": "#FFD9D9", "user": "#FFF1D6", "run": "#E6F0FF"}.get(state, "white")
        self.num.setStyleSheet(f"background:{bg};border-color:{c}")


class AddProfileDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page, self.ctl = page, page.ctl
        self.setup = None
        self.setWindowTitle("Add profile")
        self.setStyleSheet(DLG_QSS)
        self.setMinimumWidth(820)
        exe = self.ctl.browser_exe()
        self.managed = bool(exe)
        self.bname = chrome_mod.browser_name(exe) if exe else "Google Chrome"
        self.cent = "cent" in self.bname.lower()

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 18, 26, 20)
        root.setSpacing(14)
        root.addWidget(label("Add profile", size=26, bold=True))
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{BORDER}")
        root.addWidget(line)

        body = QHBoxLayout()
        body.setSpacing(26)
        form = QVBoxLayout()
        form.setSpacing(8)

        def fl(t):
            l = QLabel(t)
            l.setObjectName("flabel")
            return l
        form.addWidget(fl("Profile name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Enter profile name")
        form.addWidget(self.name)
        form.addSpacing(6)
        form.addWidget(fl("Browser"))
        self.browser = QLineEdit(self.bname)
        self.browser.setReadOnly(True)
        self.browser.setToolTip(exe or "Settings mein Browser EXE Path set nahi; Google Chrome ka apna profile banega")
        form.addWidget(self.browser)
        form.addSpacing(6)
        form.addWidget(fl("Sign-in mode"))
        mh = QHBoxLayout()
        self.b_auto = QPushButton("  Auto sign-in")
        self.b_manual = QPushButton("  Manual sign-in")
        grp = QButtonGroup(self)
        for b in (self.b_auto, self.b_manual):
            b.setObjectName("mode")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            grp.addButton(b)
            mh.addWidget(b)
        self.b_auto.setChecked(self.managed)
        self.b_manual.setChecked(not self.managed)
        self.b_auto.setEnabled(self.managed)
        if not self.managed:
            self.b_auto.setToolTip("Auto sign-in sirf Settings ke Browser EXE Path wale profiles ke liye")
        self.b_auto.toggled.connect(self._mode)
        form.addLayout(mh)
        form.addSpacing(6)
        self.l_email = fl("Email")
        form.addWidget(self.l_email)
        self.email = QLineEdit()
        self.email.setPlaceholderText("name@example.com")
        form.addWidget(self.email)
        form.addSpacing(6)
        self.l_pw = fl("Password")
        form.addWidget(self.l_pw)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("Sirf is sign-in ke liye, save nahi hota")
        self.eye = QAction(icons.icon("eye", 18, "#3a3f45"), "Show", self.pw)
        self.eye.triggered.connect(lambda: self.pw.setEchoMode(
            QLineEdit.Normal if self.pw.echoMode() == QLineEdit.Password else QLineEdit.Password))
        self.pw.addAction(self.eye, QLineEdit.TrailingPosition)
        form.addWidget(self.pw)
        self.l_manual = label("Manual: profile ke andar Hark ka sign-in form khul jayega, login aap khud karenge.", wrap=True, color=MUTED)
        form.addWidget(self.l_manual)
        form.addStretch(1)
        body.addLayout(form, 3)

        steps = QFrame()
        steps.setObjectName("steps")
        sv = QVBoxLayout(steps)
        sv.setContentsMargins(20, 14, 20, 16)
        sv.addWidget(label("Setup order", size=20, bold=True))
        sl = QFrame()
        sl.setFixedHeight(1)
        sl.setStyleSheet(f"background:{BORDER}")
        sv.addWidget(sl)
        self.rows = [StepRow(1, "Install Browsec VPN", f"In {self.bname}" if self.cent else "Sirf Cent Browser ke liye"),
                     StepRow(2, "Connect VPN", "United States" if self.cent else "Sirf Cent Browser ke liye"),
                     StepRow(3, "Sign in to Hark", "Auto / Manual")]
        for r in self.rows:
            sv.addWidget(r)
        sv.addStretch(1)
        steps.setMinimumWidth(300)
        body.addWidget(steps, 2)
        root.addLayout(body)

        self.msg = label("", wrap=True)
        root.addWidget(self.msg)
        line2 = QFrame()
        line2.setFixedHeight(1)
        line2.setStyleSheet(f"background:{BORDER}")
        root.addWidget(line2)
        bh = QHBoxLayout()
        bh.addStretch(1)
        self.cancel = button("Cancel", "plain")
        self.cancel.setMinimumHeight(46)
        self.cancel.clicked.connect(self.reject)
        self.create = button("Create profile", "primary")
        self.create.setMinimumHeight(46)
        self.create.setStyleSheet("font-size:17px;padding:8px 26px")
        self.create.clicked.connect(self.go)
        bh.addWidget(self.cancel)
        bh.addWidget(self.create)
        root.addLayout(bh)
        self._mode()

    def _mode(self):
        auto = self.b_auto.isChecked()
        for w in (self.l_email, self.email, self.l_pw, self.pw):
            w.setVisible(auto)
        self.l_manual.setVisible(not auto)
        self.b_auto.setIcon(icons.icon("radioon" if auto else "radio", 20))
        self.b_manual.setIcon(icons.icon("radio" if auto else "radioon", 20))

    def go(self):
        if self.setup:          # finished: the button now closes the dialog
            return self.accept()
        name = self.name.text().strip()
        auto = self.b_auto.isChecked()
        if not name:
            self.msg.setText("Profile ka naam likhein.")
            return
        if auto and (not self.email.text().strip() or not self.pw.text()):
            self.msg.setText("Auto sign-in ke liye email aur password dono chahiye.")
            return
        try:
            r = self.ctl.add_profile(name, launch=not self.managed)
        except Exception as e:
            self.msg.setText(f"Profile nahi ban saka: {e}")
            return
        self.page.rebuild()
        if r["kind"] != "managed":
            self.pw.clear()
            QMessageBox.information(self, "Add profile", f"Chrome mein naya profile ({r['dir']}) khul raha hai. Wahan Hark mein sign in kar lein; ye list mein khud aa jayega.")
            return self.accept()
        p = self.ctl.profile("hbm:" + r["tag"])
        email, pw = self.email.text(), self.pw.text()
        self.pw.clear()           # the dialog keeps no copy; the worker drops its copy after typing it
        for w in (self.name, self.email, self.pw, self.b_auto, self.b_manual):
            w.setEnabled(False)
        self.setup = ProfileSetup(self.ctl, p, "auto" if auto else "manual", email, pw)
        del pw
        self.setup.step.connect(self.on_step)
        self.setup.field.connect(self.on_field)
        self.setup.finished.connect(self.on_done)
        self.create.setEnabled(False)
        self.create.setText("Setting up…")
        self.cancel.setText("Stop")
        self.msg.setText(f"Data folder: {r['dir']}")
        self.setup.start()

    def on_step(self, n, state, text):
        self.rows[n - 1].set_state(state, text)

    def on_field(self, tag, key, value):
        self.ctl.set_managed_field(tag, key, value)
        self.ctl.reload_profiles()

    def on_done(self, ok, msg):
        self.create.setEnabled(True)
        self.create.setText("Done" if ok else "Close")
        self.cancel.setVisible(False)
        self.msg.setText(("✓ " if ok else "") + msg)
        self.msg.setStyleSheet(f"color:{GREEN if ok else '#C62828'};font-weight:600")
        self.ctl.refresh_windows()

    def reject(self):
        if self.setup:
            self.setup.stop()
        super().reject()
