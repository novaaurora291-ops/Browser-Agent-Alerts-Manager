from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
import re

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFrame, QWidget,
                               QButtonGroup, QComboBox, QPlainTextEdit, QScrollArea, QSizePolicy)

from . import icons
from .widgets import label, button
from .style import LIME, LIME_SOFT, LIME_BORDER, BORDER, MUTED, GREEN
from .. import chrome as chrome_mod
from ..setup import ProfileSetup, parse_dob

DLG_QSS = f"""
QDialog {{ background: white; }}
QWidget#formw {{ background: white; }}
QLabel#flabel {{ font-size: 14px; font-weight: 700; }}
QLineEdit {{ min-height: 26px; font-size: 14px; }}
QLineEdit:read-only {{ background: #EEF0F2; }}
QPushButton#mode {{ background: white; border: 1.5px solid #D6DADF; border-radius: 9px; padding: 7px 12px; font-size: 14px; text-align: left; }}
QPushButton#mode:checked {{ background: {LIME_SOFT}; border-color: {LIME_BORDER}; font-weight: 600; }}
QFrame#steps {{ background: #FAFBFB; border: 1px solid {BORDER}; border-radius: 12px; }}
QPlainTextEdit {{ background: white; border: 1px solid #CDD1D6; border-radius: 9px; padding: 6px; font-family: Consolas; font-size: 13px; }}
QPushButton#tab {{ background: white; border: 1.5px solid #D6DADF; padding: 7px 18px; font-size: 14px; min-width: 120px; }}
QPushButton#primary:disabled {{ background: #DDE3D3; color: #8A9081; }}
QPushButton#tab:checked {{ background: {LIME}; border-color: {LIME}; font-weight: 700; }}
QPushButton#cnt {{ background: #F3F4F5; border: 1px solid #CDD1D6; min-width: 34px; min-height: 30px; font-size: 16px; font-weight: 700; }}
QFrame#prow {{ background: white; border: 1px solid #E3E6EA; border-radius: 9px; }}
QPushButton#mini {{ background: white; border: 1px solid #CDD1D6; border-radius: 7px; padding: 2px 9px; font-size: 12px; }}
QPushButton#mini:hover {{ border-color: #8A9099; }}
QLabel#num {{ border: 1.5px solid #CDD1D6; border-radius: 13px; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; font-weight: 700; qproperty-alignment: AlignCenter; background: white; }}
"""

COLORS = {"wait": MUTED, "run": "#1f6feb", "ok": GREEN, "user": "#B26A00", "fail": "#C62828", "skip": MUTED}
MARK = {"ok": "✓", "fail": "!", "user": "…", "run": "", "wait": "", "skip": "–"}


class StepRow(QWidget):
    def __init__(self, n, title, sub):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 3, 0, 3)
        h.setSpacing(10)
        self.n = n
        self.num = QLabel(str(n))
        self.num.setObjectName("num")
        h.addWidget(self.num, 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(2)
        v.addWidget(label(title, size=14, bold=True))
        self.sub = label(sub, wrap=True, color=MUTED)
        self.sub.setStyleSheet(f"font-size:12px;color:{MUTED}")
        v.addWidget(self.sub)
        h.addLayout(v, 1)

    def set_state(self, state, text):
        c = COLORS.get(state, MUTED)
        self.sub.setText(text)
        self.sub.setStyleSheet(f"font-size:12px;color:{c}")
        mk = MARK.get(state, "")
        self.num.setText(mk or str(self.n))
        bg = {"ok": LIME, "fail": "#FFD9D9", "user": "#FFF1D6", "run": "#E6F0FF"}.get(state, "white")
        self.num.setStyleSheet(f"background:{bg};border-color:{c}")


BULLET = "•"
EMAIL_RE = re.compile(r"^[^@\s|]+@[^@\s|]+\.[^@\s|]+$")


class ProgressRow(QFrame):
    """One setup job in the side progress panel: name, account, current step, its own Stop."""
    STEP_NAMES = {1: "Create profile", 2: "Browsec VPN", 3: "US VPN", 4: "Hark sign-in"}

    def __init__(self, dlg, idx, name, email):
        super().__init__()
        self.dlg, self.idx, self.p, self.job = dlg, idx, None, None
        self.setObjectName("prow")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 7, 10, 8)
        v.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.num = QLabel(str(idx + 1))
        self.num.setObjectName("num")
        top.addWidget(self.num)
        self.name = label(name, size=13, bold=True)
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name.setToolTip(name)
        top.addWidget(self.name, 1)
        self.stopb = QPushButton("Stop")
        self.stopb.setObjectName("mini")
        self.stopb.setCursor(Qt.PointingHandCursor)
        self.stopb.setAccessibleName(f"Stop {name}")
        self.stopb.clicked.connect(lambda: self.dlg.stop_job(self.job))
        top.addWidget(self.stopb)
        v.addLayout(top)
        self.sub = label(email or "Manual sign-in", color=MUTED)
        self.sub.setStyleSheet(f"font-size:11px;color:{MUTED}")
        self.sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(self.sub)
        self.state = label("Queued", wrap=True, color=MUTED)
        self.state.setStyleSheet(f"font-size:12px;color:{MUTED}")
        self.state.setAccessibleName(f"Status {name}")
        v.addWidget(self.state)
        self.focus = QPushButton("Open window")
        self.focus.setObjectName("mini")
        self.focus.setVisible(False)
        self.focus.clicked.connect(self.open_window)
        v.addWidget(self.focus, 0, Qt.AlignLeft)

    def set(self, step, state, text):
        c = COLORS.get(state, MUTED)
        prefix = {"user": "Needs action: ", "fail": "FAIL: "}.get(state, "")
        self.state.setText(f"{step}/4 {self.STEP_NAMES.get(step, '')} · {prefix}{text}")
        self.state.setAccessibleName(self.state.text())
        self.state.setStyleSheet(f"font-size:12px;color:{c};" + ("font-weight:700;" if state in ("user", "fail") else ""))
        bg = {"ok": LIME, "fail": "#FFD9D9", "user": "#FFF1D6", "run": "#E6F0FF"}.get(state, "white")
        self.num.setStyleSheet(f"background:{bg};border-color:{c}")
        self.focus.setVisible(state == "user" and self.p is not None)

    def finish(self, ok, msg, cancelled=False):
        col = MUTED if cancelled else (GREEN if ok else "#C62828")
        self.state.setText(("Stopped · " if cancelled else ("PASS · " if ok else "FAIL · ")) + msg)
        self.state.setAccessibleName(self.state.text())
        self.state.setStyleSheet(f"font-size:12px;font-weight:700;color:{col}")
        self.num.setText("–" if cancelled else ("✓" if ok else "!"))
        self.num.setStyleSheet(f"background:{'white' if cancelled else (LIME if ok else '#FFD9D9')};border-color:{col}")
        self.focus.setVisible(False)
        self.stopb.setVisible(False)

    def open_window(self):
        if self.p:
            chrome_mod.focus_profile(self.p)


class AddProfileDialog(QDialog):
    """Non-modal, resizable window: the dashboard stays usable while setup jobs run in the background."""
    busy = Signal(int, int)       # (finished jobs, total jobs) while running; (-1, -1) when the window is gone

    def __init__(self, page):
        super().__init__(None)
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.page, self.ctl = page, page.ctl
        self.setup = None
        self.running = False
        self.finished_all = False
        self._pws = {}          # email -> password, only while the dialog lives; each is dropped once used
        self.queue = []
        self.setWindowTitle("Add profile")
        from .style import build_qss
        self.setStyleSheet(build_qss() + DLG_QSS)   # own top-level window: bring the app look along
        self.resize(760, 560)
        self.setMinimumSize(620, 440)
        self.jobs = []
        exe = self.ctl.browser_exe()
        self.managed = bool(exe)
        self.bname = "Cent Browser"
        self.cent = "cent" in self.bname.lower()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(8)
        root.addWidget(label("Add profile", size=20, bold=True))

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
            self.t_multi.setToolTip("Multiple profiles ke liye Settings mein Cent Browser Path set karein")
        self.t_single.toggled.connect(self._mode)
        tabs.addStretch(1)
        root.addLayout(tabs)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.form_w = QWidget()
        self.form_w.setObjectName("formw")
        form = QVBoxLayout(self.form_w)
        form.setContentsMargins(0, 0, 8, 0)
        form.setSpacing(5)

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
        self.browser.setMinimumHeight(32)
        self.browser.setToolTip(exe or "Settings mein Cent Browser Path set nahi")
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
        self.count.setFixedWidth(56)
        self.count.setMinimumHeight(30)
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
            self.b_auto.setToolTip("Auto sign-in sirf Settings ke Cent Browser Path wale profiles ke liye")
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
        self.paste.setMinimumHeight(96)
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

        form.addStretch(1)
        fscroll = QScrollArea()
        fscroll.setWidgetResizable(True)
        fscroll.setFrameShape(QFrame.NoFrame)
        fscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fscroll.setStyleSheet("QScrollArea{background:transparent;border:none}")
        fscroll.setWidget(self.form_w)
        body.addWidget(fscroll, 3)

        steps = QFrame()
        steps.setObjectName("steps")
        sv = QVBoxLayout(steps)
        sv.setContentsMargins(14, 10, 14, 10)
        self.steps_title = label("Setup order", size=15, bold=True)
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
        self.steps = steps
        side = QVBoxLayout()
        side.setSpacing(0)
        side.addWidget(steps, 0, Qt.AlignTop)

        # compact live progress panel, shown in place of "Setup order" once jobs start
        self.prog = QFrame()
        self.prog.setObjectName("steps")
        pv = QVBoxLayout(self.prog)
        pv.setContentsMargins(10, 10, 10, 10)
        pv.setSpacing(6)
        self.prog_title = label("Progress", size=15, bold=True)
        self.prog_title.setAccessibleName("Progress")
        pv.addWidget(self.prog_title)
        self.plist_box = QScrollArea()
        self.plist_box.setWidgetResizable(True)
        self.plist_box.setFrameShape(QFrame.NoFrame)
        self.plist_box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.plist_box.setStyleSheet("QScrollArea{border:none;background:transparent}")
        pw_ = QWidget()
        pw_.setStyleSheet("background:transparent")
        self.plist = QVBoxLayout(pw_)
        self.plist.setContentsMargins(0, 0, 0, 0)
        self.plist.setSpacing(6)
        self.plist.addStretch(1)
        self.plist_box.setWidget(pw_)
        pv.addWidget(self.plist_box, 1)
        self.prog.setVisible(False)
        side.addWidget(self.prog, 1)
        sidew = QWidget()
        sidew.setLayout(side)
        sidew.setMinimumWidth(240)
        sidew.setMaximumWidth(290)
        body.addWidget(sidew, 2)
        root.addLayout(body, 1)

        self.msg = label("", wrap=True)
        root.addWidget(self.msg)
        line2 = QFrame()
        line2.setFixedHeight(1)
        line2.setStyleSheet(f"background:{BORDER}")
        root.addWidget(line2)
        bh = QHBoxLayout()
        bh.addStretch(1)
        self.hideb = button("Hide", "plain")
        self.hideb.setMinimumHeight(38)
        self.hideb.setToolTip("Window chhupa dein; setup background mein chalta rahega. Add profile se wapas khulegi.")
        self.hideb.clicked.connect(self.hide)
        self.hideb.setVisible(False)
        self.cancel = button("Cancel", "plain")
        self.cancel.setMinimumHeight(38)
        self.cancel.clicked.connect(self.reject)
        self.create = button("Create profile", "primary")
        self.create.setMinimumHeight(38)
        self.create.setStyleSheet("font-size:15px;padding:6px 20px")
        self.create.clicked.connect(self.go)
        bh.addWidget(self.hideb)
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
            self.ready.setText(f"✓  {n} account{'s' if n != 1 else ''} ready")
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
        if not self.managed:
            self.msg.setText("Cent Browser nahi mila. Settings mein Cent Browser Path set karein (sirf Cent supported hai).")
            return
        email = self.email.text().strip() if auto else ""
        if auto:
            self._pws = {email.lower(): self.pw.text()}
        self.pw.clear()           # the window keeps no copy on screen; the worker drops its copy after typing it
        taken = {p.name.lower() for p in self.ctl.profiles}
        self._start_jobs([(self.unique_name(name, taken), email)], auto)

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
        start = len(self.ctl.settings["managed_profiles"] or []) + 1
        jobs = []
        for i, em in enumerate(emails):
            base = em.split("@")[0] if em else f"{self.bname.replace(' Browser', '')} {start + i}"
            jobs.append((self.unique_name(base, taken), em))
        self.paste.blockSignals(True)
        self.paste.clear()        # nothing left on screen; passwords stay only in _pws until each is used
        self.paste.blockSignals(False)
        self._start_jobs(jobs, auto)

    def _start_jobs(self, jobs, auto):
        """Queue the setup jobs and switch the side panel to live progress. Work runs off the GUI thread."""
        self.queue, self.jobs, self.results = [], [], []
        for i, (nm, em) in enumerate(jobs):
            row = ProgressRow(self, i, nm, em)
            job = {"name": nm, "email": em, "row": row, "state": "queued", "setup": None}
            row.job = job
            self.plist.insertWidget(self.plist.count() - 1, row)
            self.queue.append(job)
            self.jobs.append(job)
        self.form_w.setEnabled(False)
        for w in (self.l_paste, self.l_paste_hint, self.paste, self.ready, self.pw, self.l_pw):
            w.setVisible(False)      # nothing about passwords stays on screen
        for w in (self.t_single, self.t_multi):
            w.setEnabled(False)
        self.steps.setVisible(False)
        self.prog.setVisible(True)
        self.running = True
        self.mode_str = "auto" if auto else "manual"
        self.dob_val = self._save_dob()
        self.create.setEnabled(False)
        self.create.setText("Setting up…")
        self.cancel.setVisible(False)
        self.hideb.setVisible(True)
        self._progress()
        QTimer.singleShot(100, self.next_profile)

    def _progress(self):
        done = sum(1 for j in self.jobs if j["state"] in ("done", "cancelled"))
        self.prog_title.setText(f"Progress · {done}/{len(self.jobs)}")
        self.msg.setText("Setup background mein chal raha hai. Dashboard use kar sakti hain; Hide se window chhup jayegi."
                         if self.running else self.msg.text())
        self.busy.emit(done, len(self.jobs)) if self.running else None

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

    def stop_job(self, job):
        """Cancel only this job: a queued one is dropped, a running one stops at its next step."""
        if not job or job["state"] in ("done", "cancelled"):
            return
        if job["state"] == "queued":
            if job in self.queue:
                self.queue.remove(job)
            self._pws.pop(job["email"].lower(), None)
            job["state"] = "cancelled"
            job["row"].finish(False, "Shuru hone se pehle rok diya", cancelled=True)
            self.results.append(None)
            self._progress()
            if self.running and not self.queue and not any(j["state"] == "running" for j in self.jobs):
                self.all_done()
            return
        job["stopping"] = True
        job["row"].stopb.setEnabled(False)
        job["row"].state.setText("Rok raha hoon…")
        if job.get("setup"):
            job["setup"].stop()

    def next_profile(self):
        if not self.running:
            return
        if not self.queue:
            return self.all_done()
        job = self.queue.pop(0)
        job["state"] = "running"
        self.cur = job
        row = job["row"]
        row.set(1, "run", "Data folder ban raha hai…")
        try:
            r = self.ctl.add_profile(job["name"], launch=False)
        except Exception as e:
            job["state"] = "done"
            row.finish(False, f"Profile nahi bana: {e}")
            self._pws.pop(job["email"].lower(), None)
            self.results.append(False)
            self._progress()
            return QTimer.singleShot(100, self.next_profile)
        self.page.rebuild()
        p = self.ctl.profile("hbm:" + r["tag"])
        row.p = p
        row.set(1, "ok", "Profile ban gaya")
        pw = self._pws.pop(job["email"].lower(), "") if job["email"] else ""
        st = ProfileSetup(self.ctl, p, self.mode_str, job["email"], pw, self.dob_val)
        del pw
        job["setup"] = st
        st.step.connect(lambda n, stt, t, row=row: row.set(n + 1, stt, t))
        st.field.connect(self.on_field)
        st.finished.connect(lambda ok, m, job=job: self.one_done(job, ok, m))
        self.setup = st
        st.start()

    def one_done(self, job, ok, msg):
        cancelled = bool(job.get("stopping")) and not ok
        job["state"] = "cancelled" if cancelled else "done"
        job["row"].finish(ok, msg, cancelled=cancelled)
        job["setup"] = None
        self.results.append(None if cancelled else ok)
        self.setup = None
        self._progress()
        self.ctl.refresh_windows()
        QTimer.singleShot(800, self.next_profile)

    def all_done(self):
        self.running = False
        self.finished_all = True
        self._pws = {}
        real = [r for r in self.results if r is not None]
        n_ok = sum(1 for r in real if r)
        stopped = len(self.results) - len(real)
        self.create.setEnabled(True)
        self.create.setText("Done")
        self.hideb.setVisible(False)
        ok = bool(real) and n_ok == len(real)
        self.prog_title.setText(f"Progress · {len(self.results)}/{len(self.jobs)}")
        self.msg.setText(f"{n_ok}/{len(self.jobs)} profiles tayyar" + (f" · {stopped} roke gaye" if stopped else ""))
        col = GREEN if ok else (MUTED if not real else "#C62828")
        self.msg.setStyleSheet(f"color:{col};font-weight:700")
        self.busy.emit(-2, len(self.jobs))
        self.page.rebuild()

    def on_field(self, tag, key, value):
        self.ctl.set_managed_field(tag, key, value)
        self.ctl.reload_profiles()

    def _cleanup(self):
        if getattr(self, "_gone", False):
            return
        self._gone = True
        self._pws = {}
        self.busy.emit(-1, -1)

    def reject(self):
        # Esc / Cancel / title-bar X: while jobs run, only hide (they keep going); otherwise close for good
        if self.running:
            self.hide()
            return
        self._cleanup()
        super().reject()          # done(): hides and, with WA_DeleteOnClose, deletes the window

    def accept(self):
        self._cleanup()
        super().accept()

    def closeEvent(self, e):
        if self.running:          # closing the window never kills running setups; it just hides
            e.ignore()
            self.hide()
            return
        self._cleanup()
        super().closeEvent(e)
