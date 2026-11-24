from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
import re

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFrame, QWidget,
                               QButtonGroup, QMessageBox, QComboBox, QPlainTextEdit, QScrollArea)

from . import icons
from .widgets import label, button
from .style import LIME, LIME_SOFT, LIME_BORDER, BORDER, MUTED, GREEN
from .. import chrome as chrome_mod
from ..setup import ProfileSetup, parse_dob

DLG_QSS = f"""
QDialog {{ background: white; }}
QLabel#flabel {{ font-size: 16px; font-weight: 700; }}
QLineEdit {{ min-height: 30px; font-size: 15px; }}
QLineEdit:read-only {{ background: #EEF0F2; }}
QPushButton#mode {{ background: white; border: 1.5px solid #D6DADF; border-radius: 9px; padding: 10px 14px; font-size: 15px; text-align: left; }}
QPushButton#mode:checked {{ background: {LIME_SOFT}; border-color: {LIME_BORDER}; font-weight: 600; }}
QFrame#steps {{ background: #FAFBFB; border: 1px solid {BORDER}; border-radius: 12px; }}
QPlainTextEdit {{ background: white; border: 1px solid #CDD1D6; border-radius: 9px; padding: 6px; font-family: Consolas; font-size: 15px; }}
QPushButton#tab {{ background: white; border: 1.5px solid #D6DADF; padding: 9px 30px; font-size: 16px; min-width: 150px; }}
QPushButton#primary:disabled {{ background: #DDE3D3; color: #8A9081; }}
QPushButton#tab:checked {{ background: {LIME}; border-color: {LIME}; font-weight: 700; }}
QPushButton#cnt {{ background: #F3F4F5; border: 1px solid #CDD1D6; min-width: 44px; min-height: 36px; font-size: 18px; font-weight: 700; }}
QFrame#prow {{ background: white; border: 1px solid #E3E6EA; border-radius: 10px; }}
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


BULLET = "•"
EMAIL_RE = re.compile(r"^[^@\s|]+@[^@\s|]+\.[^@\s|]+$")


class ProgressRow(QFrame):
    """One profile in a Multiple-profiles run: name, account, current step and its own result."""
    STEP_NAMES = {1: "Create profile", 2: "Browsec VPN", 3: "US VPN", 4: "Hark sign-in"}

    def __init__(self, dlg, idx, name, email):
        super().__init__()
        self.dlg, self.idx, self.p = dlg, idx, None
        self.setObjectName("prow")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(12)
        self.num = QLabel(str(idx + 1))
        self.num.setObjectName("num")
        h.addWidget(self.num)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = label(name, size=16, bold=True)
        col.addWidget(self.name)
        self.sub = label(email or "Manual sign-in", color=MUTED)
        col.addWidget(self.sub)
        h.addLayout(col, 3)
        self.state = label("Queued", wrap=True, color=MUTED)
        self.state.setStyleSheet(f"font-size:14px;color:{MUTED}")
        h.addWidget(self.state, 4)
        self.focus = button("Open window", "outline")
        self.focus.setVisible(False)
        self.focus.clicked.connect(self.open_window)
        h.addWidget(self.focus)

    def set(self, step, state, text):
        c = COLORS.get(state, MUTED)
        prefix = {"user": "Needs action: ", "fail": "FAIL: "}.get(state, "")
        self.state.setText(f"{step}/4 {self.STEP_NAMES.get(step, '')} · {prefix}{text}")
        self.state.setStyleSheet(f"font-size:14px;color:{c};" + ("font-weight:700;" if state in ("user", "fail") else ""))
        bg = {"ok": LIME, "fail": "#FFD9D9", "user": "#FFF1D6", "run": "#E6F0FF"}.get(state, "white")
        self.num.setStyleSheet(f"background:{bg};border-color:{c}")
        self.focus.setVisible(state == "user" and self.p is not None)

    def finish(self, ok, msg):
        self.state.setText(("PASS · " if ok else "FAIL · ") + msg)
        self.state.setStyleSheet(f"font-size:14px;font-weight:700;color:{GREEN if ok else '#C62828'}")
        self.num.setText("✓" if ok else "!")
        self.num.setStyleSheet(f"background:{LIME if ok else '#FFD9D9'};border-color:{GREEN if ok else '#C62828'}")
        self.focus.setVisible(False)

    def open_window(self):
        if self.p:
            chrome_mod.focus_profile(self.p)


class AddProfileDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page, self.ctl = page, page.ctl
        self.setup = None
        self.running = False
        self.finished_all = False
        self._pws = {}          # email -> password, only while the dialog lives; each is dropped once used
        self.queue = []
        self.setWindowTitle("Add profile")
        self.setStyleSheet(DLG_QSS)
        self.setMinimumWidth(980)
        exe = self.ctl.browser_exe()
        self.managed = bool(exe)
        self.bname = chrome_mod.browser_name(exe) if exe else "Google Chrome"
        self.cent = "cent" in self.bname.lower()

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 18, 28, 20)
        root.setSpacing(14)
        root.addWidget(label("Add profile", size=28, bold=True))

        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.t_single = QPushButton("Single profile")
        self.t_multi = QPushButton("Multiple profiles")
        tg = QButtonGroup(self)
        for b in (self.t_single, self.t_multi):
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            tg.addButton(b)
            tabs.addWidget(b)
        self.t_single.setStyleSheet("border-top-left-radius:9px;border-bottom-left-radius:9px;")
        self.t_multi.setStyleSheet("border-top-right-radius:9px;border-bottom-right-radius:9px;")
        self.t_single.setChecked(True)
        self.t_multi.setEnabled(self.managed)
        if not self.managed:
            self.t_multi.setToolTip("Multiple profiles ke liye Settings mein Browser EXE Path set karein")
        self.t_single.toggled.connect(self._mode)
        tabs.addStretch(1)
        root.addLayout(tabs)

        body = QHBoxLayout()
        body.setSpacing(26)
        form = QVBoxLayout()
        form.setSpacing(8)

        def fl(t):
            l = QLabel(t)
            l.setObjectName("flabel")
            return l
        self.l_name = fl("Profile name")
        form.addWidget(self.l_name)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Enter profile name")
        form.addWidget(self.name)

        bc = QHBoxLayout()
        bc.setSpacing(18)
        bcol = QVBoxLayout()
        bcol.addWidget(fl("Browser"))
        self.browser = QComboBox()
        self.browser.addItem(self.bname)     # the browser saved in Settings
        self.browser.setEnabled(False)
        self.browser.setMinimumHeight(40)
        self.browser.setToolTip(exe or "Settings mein Browser EXE Path set nahi; Google Chrome ka apna profile banega")
        bcol.addWidget(self.browser)
        bc.addLayout(bcol, 3)
        self.cnt_box = QWidget()
        ccol = QVBoxLayout(self.cnt_box)
        ccol.setContentsMargins(0, 0, 0, 0)
        ccol.addWidget(fl("Profile count"))
        ch = QHBoxLayout()
        ch.setSpacing(0)
        self.minus = QPushButton("−")
        self.plus = QPushButton("+")
        self.count = QLineEdit("2")
        self.count.setAlignment(Qt.AlignCenter)
        self.count.setFixedWidth(80)
        self.count.setMinimumHeight(38)
        for b in (self.minus, self.plus):
            b.setObjectName("cnt")
            b.setCursor(Qt.PointingHandCursor)
        self.minus.setStyleSheet("border-top-left-radius:9px;border-bottom-left-radius:9px;")
        self.plus.setStyleSheet("border-top-right-radius:9px;border-bottom-right-radius:9px;")
        self.minus.clicked.connect(lambda: self._bump(-1))
        self.plus.clicked.connect(lambda: self._bump(1))
        self.count.textChanged.connect(self._validate)
        ch.addWidget(self.minus)
        ch.addWidget(self.count)
        ch.addWidget(self.plus)
        ch.addStretch(1)
        ccol.addLayout(ch)
        bc.addWidget(self.cnt_box, 2)
        form.addSpacing(4)
        form.addLayout(bc)
        form.addSpacing(4)

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
        form.addSpacing(4)
        self.l_email = fl("Email")
        form.addWidget(self.l_email)
        self.email = QLineEdit()
        self.email.setPlaceholderText("name@example.com")
        form.addWidget(self.email)
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

        self.l_paste = fl("Paste accounts")
        form.addWidget(self.l_paste)
        self.l_paste_hint = label("One account per line · email|password", color=MUTED)
        form.addWidget(self.l_paste_hint)
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText("test1@example.com|password\ntest2@example.com|password")
        self.paste.setMinimumHeight(130)
        self.paste.setAccessibleName("Paste accounts")
        self.paste.textChanged.connect(self._on_paste)
        form.addWidget(self.paste)
        self.ready = label("", bold=True)
        form.addWidget(self.ready)

        self.l_dob = fl("Birthday (agar Hark poochhe)")
        form.addWidget(self.l_dob)
        self.dob = QLineEdit(str(self.ctl.settings.get("signin_dob") or ""))
        self.dob.setPlaceholderText("YYYY-MM-DD · Hark ke age check par khud fill hoga")
        self.dob.setAccessibleName("Birthday")
        self.dob.textChanged.connect(self._validate)
        form.addWidget(self.dob)

        self.l_manual = label("Manual: har profile mein Hark ka sign-in page khul jayega, login aap khud karenge.", wrap=True, color=MUTED)
        form.addWidget(self.l_manual)

        self.plist_box = QScrollArea()
        self.plist_box.setWidgetResizable(True)
        self.plist_box.setMinimumHeight(260)
        self.plist_box.setStyleSheet("QScrollArea{border:none;background:transparent}")
        pw_ = QWidget()
        self.plist = QVBoxLayout(pw_)
        self.plist.setContentsMargins(0, 0, 0, 0)
        self.plist.setSpacing(8)
        self.plist.addStretch(1)
        self.plist_box.setWidget(pw_)
        self.plist_box.setVisible(False)
        form.addWidget(self.plist_box, 1)
        form.addStretch(1)
        body.addLayout(form, 3)

        steps = QFrame()
        steps.setObjectName("steps")
        sv = QVBoxLayout(steps)
        sv.setContentsMargins(20, 14, 20, 16)
        self.steps_title = label("Setup order", size=20, bold=True)
        sv.addWidget(self.steps_title)
        sl = QFrame()
        sl.setFixedHeight(1)
        sl.setStyleSheet(f"background:{BORDER}")
        sv.addWidget(sl)
        na = "Sirf Cent Browser ke liye"
        self.rows = [StepRow(1, "Create profile", "Alag data folder"),
                     StepRow(2, "Install Browsec VPN", f"In {self.bname}" if self.cent else na),
                     StepRow(3, "Connect VPN", "United States" if self.cent else na),
                     StepRow(4, "Sign in to Hark", "Auto / Manual")]
        for r in self.rows:
            sv.addWidget(r)
        sv.addStretch(1)
        steps.setMinimumWidth(300)
        steps.setMaximumWidth(340)
        body.addWidget(steps, 2, Qt.AlignTop)
        root.addLayout(body, 1)

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

    # ---------- form state ----------
    def multi(self):
        return self.t_multi.isChecked()

    def auto(self):
        return self.b_auto.isChecked()

    def _mode(self):
        if self.running or self.finished_all:
            return
        multi, auto = self.multi(), self.auto()
        for w in (self.l_name, self.name):
            w.setVisible(not multi)
        self.cnt_box.setVisible(multi)
        for w in (self.l_email, self.email, self.l_pw, self.pw):
            w.setVisible(auto and not multi)
        for w in (self.l_paste, self.l_paste_hint, self.paste, self.ready):
            w.setVisible(auto and multi)
        self.l_manual.setVisible(not auto)
        for w in (self.l_dob, self.dob):
            w.setVisible(auto)
        self.b_auto.setIcon(icons.icon("radioon" if auto else "radio", 20))
        self.b_manual.setIcon(icons.icon("radio" if auto else "radioon", 20))
        self.steps_title.setText("Setup per profile" if multi else "Setup order")
        self._validate()

    def _n(self):
        try:
            return max(0, int(self.count.text().strip()))
        except ValueError:
            return 0

    def _bump(self, d):
        self.count.setText(str(min(50, max(1, self._n() + d))))

    def _on_paste(self):
        """Keep each password in memory only and show it as dots, whether pasted or typed."""
        text = self.paste.toPlainText()
        out, seen = [], {}
        for line in text.split("\n"):
            if "|" not in line:
                out.append(line)
                continue
            em, pw = line.split("|", 1)
            key = em.strip().lower()
            body = pw.lstrip(" ")
            nb = len(body) - len(body.lstrip(BULLET))
            old = self._pws.get(key, "")
            real = old[:nb] + body[nb:]
            seen[key] = real
            out.append(f"{em.rstrip()} | " + BULLET * len(real) if real else f"{em.rstrip()} | ")
        self._pws = seen
        new = "\n".join(out)
        if new != text:
            cur = self.paste.textCursor()
            blk, col = cur.blockNumber(), cur.positionInBlock()
            self.paste.blockSignals(True)
            self.paste.setPlainText(new)
            self.paste.blockSignals(False)
            c = self.paste.textCursor()
            b = self.paste.document().findBlockByNumber(min(blk, self.paste.document().blockCount() - 1))
            c.setPosition(b.position() + min(len(b.text()), max(col, len(b.text()) if "|" in b.text() else col)))
            self.paste.setTextCursor(c)
        self._validate()

    def accounts(self):
        """Valid email|password rows, in order, de-duplicated. Returns (valid, bad_line_count)."""
        good, bad, emails = [], 0, set()
        for line in self.paste.toPlainText().split("\n"):
            if not line.strip():
                continue
            if "|" not in line:
                bad += 1
                continue
            em = line.split("|", 1)[0].strip()
            pw = self._pws.get(em.lower(), "")
            if not EMAIL_RE.match(em) or not pw or em.lower() in emails:
                bad += 1
                continue
            emails.add(em.lower())
            good.append(em)
        return good, bad

    def _validate(self):
        if self.running or self.finished_all:
            return
        dob_bad = self.auto() and bool(self.dob.text().strip()) and not parse_dob(self.dob.text())
        self.dob.setStyleSheet("border:1px solid #C62828" if dob_bad else "")
        if dob_bad:
            self.create.setEnabled(False)
            return
        if not self.multi():
            self.create.setText("Create profile")
            self.create.setEnabled(True)
            return
        n = self._n()
        if not self.auto():
            self.create.setText(f"Create {n} profiles" if n > 1 else "Create profile")
            self.create.setEnabled(n >= 1)
            return
        good, bad = self.accounts()
        if not self.paste.toPlainText().strip():
            self.ready.setText("")
            self.create.setText(f"Create {n} profiles" if n > 1 else "Create profile")
            self.create.setEnabled(False)
            return
        ok = n >= 1 and len(good) == n and bad == 0
        if ok:
            self.ready.setText(f"✓  {n} accounts ready")
            self.ready.setStyleSheet(f"color:{GREEN};font-size:15px;font-weight:700")
        else:
            why = []
            if bad:
                why.append(f"{bad} line galat (email|password chahiye, email dobara na ho)")
            if len(good) != n:
                why.append(f"{len(good)} valid accounts, count {n}")
            self.ready.setText("  " + " · ".join(why) if why else "")
            self.ready.setStyleSheet("color:#C62828;font-size:14px;font-weight:600")
        self.create.setText(f"Create {n} profiles" if n > 1 else "Create profile")
        self.create.setEnabled(ok)

    # ---------- run ----------
    def unique_name(self, base, taken):
        base = base.strip() or self.bname
        name, i = base, 2
        while name.lower() in taken:
            name = f"{base} {i}"
            i += 1
        taken.add(name.lower())
        return name

    def go(self):
        if self.finished_all:
            return self.accept()
        if self.running:
            return
        self.msg.setStyleSheet("")
        if self.multi():
            return self.go_multi()
        name = self.name.text().strip()
        auto = self.auto()
        if not name:
            self.msg.setText("Profile ka naam likhein.")
            return
        if auto and (not self.email.text().strip() or not self.pw.text()):
            self.msg.setText("Auto sign-in ke liye email aur password dono chahiye.")
            return
        self.rows[0].set_state("run", "Data folder ban raha hai…")
        try:
            r = self.ctl.add_profile(name, launch=not self.managed)
        except Exception as e:
            self.rows[0].set_state("fail", str(e))
            self.msg.setText(f"Profile nahi ban saka: {e}")
            return
        self.page.rebuild()
        if r["kind"] != "managed":
            self.pw.clear()
            QMessageBox.information(self, "Add profile", f"Chrome mein naya profile ({r['dir']}) khul raha hai. Wahan Hark mein sign in kar lein; ye list mein khud aa jayega.")
            return self.accept()
        self.rows[0].set_state("ok", "Profile ban gaya")
        p = self.ctl.profile("hbm:" + r["tag"])
        email, pw = self.email.text(), self.pw.text()
        self.pw.clear()           # the dialog keeps no copy; the worker drops its copy after typing it
        for w in (self.name, self.email, self.pw, self.b_auto, self.b_manual, self.t_single, self.t_multi):
            w.setEnabled(False)
        self.running = True
        self.setup = ProfileSetup(self.ctl, p, "auto" if auto else "manual", email, pw, self._save_dob())
        del pw
        self.setup.step.connect(lambda n, st, t: self.rows[n].set_state(st, t))
        self.setup.field.connect(self.on_field)
        self.setup.finished.connect(self.on_done)
        self.create.setEnabled(False)
        self.create.setText("Setting up…")
        self.cancel.setText("Stop")
        self.msg.setText(f"Data folder: {r['dir']}")
        self.setup.start()

    def go_multi(self):
        auto = self.auto()
        n = self._n()
        if auto:
            emails, bad = self.accounts()
            if bad or len(emails) != n:
                return self._validate()
        else:
            emails = [""] * n
        taken = {p.name.lower() for p in self.ctl.profiles}
        self.queue = []
        for i, em in enumerate(emails):
            base = em.split("@")[0] if em else f"{self.bname.replace(' Browser', '')} {len(self.ctl.settings['managed_profiles'] or []) + 1 + i}"
            nm = self.unique_name(base, taken)
            row = ProgressRow(self, i, nm, em)
            self.plist.insertWidget(self.plist.count() - 1, row)
            self.queue.append({"name": nm, "email": em, "row": row})
        self.paste.blockSignals(True)
        self.paste.clear()        # nothing left on screen; passwords stay only in _pws until each is used
        self.paste.blockSignals(False)
        for w in (self.l_paste, self.l_paste_hint, self.paste, self.ready, self.l_manual, self.cnt_box,
                  self.b_auto, self.b_manual, self.t_single, self.t_multi):
            w.setEnabled(False)
        for w in (self.l_paste, self.l_paste_hint, self.paste, self.ready, self.l_manual):
            w.setVisible(False)
        self.plist_box.setVisible(True)
        self.running = True
        self.results = []
        self.create.setEnabled(False)
        self.create.setText("Setting up…")
        self.cancel.setText("Stop")
        self.mode_str = "auto" if auto else "manual"
        self.dob_val = self._save_dob()
        self.dob.setEnabled(False)
        self.adjustSize()
        QTimer.singleShot(100, self.next_profile)

    def _save_dob(self):
        """Remember the batch birthday for next time (only a date, used for Hark's age check)."""
        v = self.dob.text().strip() if self.auto() else ""
        if v and parse_dob(v) and self.ctl.settings.get("signin_dob") != v:
            try:
                self.ctl.settings["signin_dob"] = v
                self.ctl.settings.save()
            except Exception:
                pass
        return v

    def next_profile(self):
        if not self.running:
            return
        if not self.queue:
            return self.all_done()
        job = self.queue.pop(0)
        self.cur = job
        row = job["row"]
        row.set(1, "run", "Data folder ban raha hai…")
        try:
            r = self.ctl.add_profile(job["name"], launch=False)
        except Exception as e:
            row.finish(False, f"Profile nahi bana: {e}")
            self._pws.pop(job["email"].lower(), None)
            self.results.append(False)
            return QTimer.singleShot(100, self.next_profile)
        self.page.rebuild()
        p = self.ctl.profile("hbm:" + r["tag"])
        row.p = p
        row.set(1, "ok", "Profile ban gaya")
        pw = self._pws.pop(job["email"].lower(), "") if job["email"] else ""
        self.setup = ProfileSetup(self.ctl, p, self.mode_str, job["email"], pw, self.dob_val)
        del pw
        self.setup.step.connect(lambda n, st, t, row=row: row.set(n + 1, st, t))
        self.setup.field.connect(self.on_field)
        self.setup.finished.connect(lambda ok, m, row=row: self.one_done(row, ok, m))
        self.setup.start()

    def one_done(self, row, ok, msg):
        row.finish(ok, msg)
        self.results.append(ok)
        self.setup = None
        self.ctl.refresh_windows()
        QTimer.singleShot(800, self.next_profile)

    def all_done(self):
        self.running = False
        self.finished_all = True
        self._pws = {}
        n_ok = sum(1 for r in self.results if r)
        self.create.setEnabled(True)
        self.create.setText("Done")
        self.cancel.setVisible(False)
        ok = n_ok == len(self.results)
        self.msg.setText(f"{n_ok}/{len(self.results)} profiles tayyar")
        self.msg.setStyleSheet(f"color:{GREEN if ok else '#C62828'};font-weight:700")

    def on_field(self, tag, key, value):
        self.ctl.set_managed_field(tag, key, value)
        self.ctl.reload_profiles()

    def on_done(self, ok, msg):
        self.running = False
        self.finished_all = True
        self.create.setEnabled(True)
        self.create.setText("Done" if ok else "Close")
        self.cancel.setVisible(False)
        self.msg.setText(("✓ " if ok else "") + msg)
        self.msg.setStyleSheet(f"color:{GREEN if ok else '#C62828'};font-weight:600")
        self.ctl.refresh_windows()

    def reject(self):
        self.running = False
        self.queue = []
        self._pws = {}
        if self.setup:
            self.setup.stop()
        super().reject()

    def accept(self):
        self._pws = {}
        super().accept()
