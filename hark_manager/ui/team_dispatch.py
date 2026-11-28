"""Team Plan & Dispatch (right-panel drawer) and the Shared Repo Team editor.

The plan is pasted by hand from ChatGPT (the Planner) and split into @alias blocks. Every worker's Plan Preview card
shows its Hark account, Cent profile, exact Hark project URL, branch + base commit and git worktree, and the FULL
final prompt (worktree path + branch + base SHA first).

Targets:
  Real Hark   manual only: 'Open Project' shows that profile's exact project tab and 'Copy Task' copies the final
              prompt; the user pastes and sends it himself. The Manager never types or sends anything here.
  Local Mock  automatic: Dispatch -> Sent -> Reply received against the local mock Hark (api_dispatch.py). 'Sent'
              only once the expected account's project shows the message, 'Reply received' only for the reply of
              that account's selected project.
  Live API    BLOCKED until Hark grants approved access: no live call is made."""
import os
import re
import threading

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QFrame,
                               QPushButton, QDialog, QLineEdit, QComboBox, QFileDialog, QGridLayout,
                               QApplication)

from .widgets import label, button
from .. import repo_team
from ..api_dispatch import IN_FLIGHT
from ..config import log

PILL = {   # kind -> (background, text colour)
    "ok": ("#DDF5D0", "#1E7B1E"), "sent": ("#C9F0B0", "#14620F"), "warn": ("#FFF1D6", "#8A5A00"),
    "block": ("#FDE2E0", "#C62828"), "info": ("#EEF0F2", "#4A5058"), "run": ("#E6F0FF", "#1F5FBF"),
}
TARGETS = (("real", "Real Hark \u2014 manual send"), ("mock", "Local Mock \u2014 automatic dispatch"),
           ("live", "Live API \u2014 BLOCKED"))
MOCK_PILL = {"verifying": ("run", "\u23F3 Verifying\u2026"), "sending": ("run", "\u23F3 Sending\u2026"),
             "sent": ("sent", "\u2713 Sent"), "reply": ("ok", "\u2713 Reply received"),
             "blocked": ("block", "\u26A0 Blocked"), "unconfirmed": ("warn", "\u26A0 Unconfirmed"),
             "account_changed": ("block", "\u26A0 Account changed"), "no_reply": ("warn", "\u26A0 No reply")}
FIELDS = ("account", "profile", "project", "branch", "worktree")


def breakable(s):
    """Display copy of a URL / path that may wrap after / \\ - (zero-width spaces). Tooltips keep the exact text."""
    return re.sub(r"([/\\-])", "\\1\u200b", s or "")


class _Relay(QObject):
    done = Signal(object)


class WorkerCard(QFrame):
    def __init__(self, panel, alias):
        super().__init__()
        self.panel, self.alias = panel, alias
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.name = label(alias, size=19, bold=True)
        top.addWidget(self.name, 0, Qt.AlignTop)
        self.pill = QLabel("")
        self.pill.setAlignment(Qt.AlignCenter)
        top.addStretch(1)
        top.addWidget(self.pill, 0, Qt.AlignTop | Qt.AlignRight)
        v.addLayout(top)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.hide()
        v.addWidget(self.detail)
        self.fields = {}
        for k in FIELDS:
            f = label("", size=12, color="#3C4148", wrap=True)
            f.setTextFormat(Qt.PlainText)
            v.addWidget(f)
            self.fields[k] = f
        mh = QHBoxLayout()
        mh.setSpacing(6)
        self.mock = label("", size=12, wrap=True)
        self.mock.setTextFormat(Qt.PlainText)
        self.b_switch = button("Switch mock account", "link")
        self.b_switch.setAccessibleName(f"Switch mock account {alias}")
        self.b_switch.clicked.connect(lambda: panel.switch_mock(alias))
        mh.addWidget(self.mock, 1)
        mh.addWidget(self.b_switch, 0, Qt.AlignTop)
        v.addLayout(mh)
        self.task = QLabel("")
        self.task.setWordWrap(True)
        self.task.setStyleSheet("background:#F3F4F5;border-radius:8px;padding:8px 10px;font-family:Consolas;font-size:13px")
        v.addWidget(self.task)
        self.reply = QLabel("")
        self.reply.setWordWrap(True)
        self.reply.setTextFormat(Qt.PlainText)
        self.reply.setStyleSheet("background:#EAF7E1;color:#14501A;border-radius:8px;padding:8px 10px;font-size:12px")
        self.reply.hide()
        v.addWidget(self.reply)
        self.more = QPushButton("Full prompt \u25B8")
        self.more.setObjectName("link")
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.clicked.connect(self.toggle)
        self.more.setAccessibleName(f"Full prompt {alias}")
        v.addWidget(self.more, 0, Qt.AlignLeft)
        self.full = QPlainTextEdit()
        self.full.setReadOnly(True)
        self.full.setMinimumHeight(150)
        self.full.setStyleSheet("font-family:Consolas;font-size:12px;background:#FBFBFC;border:1px solid #E3E6EA;border-radius:8px")
        self.full.setAccessibleName(f"Final prompt preview {alias}")
        self.full.hide()
        v.addWidget(self.full)
        bh = QHBoxLayout()
        bh.setSpacing(8)
        self.b_open = button("Open Project", "outline")
        self.b_open.setAccessibleName(f"Open Project {alias}")
        self.b_open.clicked.connect(lambda: panel.open_project(alias))
        self.b_copy = button("Copy Task", "outline")
        self.b_copy.setAccessibleName(f"Copy Task {alias}")
        self.b_copy.clicked.connect(lambda: panel.copy_task(alias))
        bh.addWidget(self.b_open)
        bh.addWidget(self.b_copy)
        bh.addStretch(1)
        v.addLayout(bh)

    def toggle(self):
        on = not self.full.isVisible()
        self.full.setVisible(on)
        self.more.setText("Full prompt \u25BE" if on else "Full prompt \u25B8")

    def set_field(self, key, text, bad=False):
        f = self.fields[key]
        f.setText(breakable(text))
        f.setToolTip(text)
        f.setAccessibleName(f"{key} {self.alias}: {text}")
        f.setStyleSheet(f"font-size:12px;color:{'#C62828' if bad else '#3C4148'}")
        f.setVisible(bool(text))

    def set_pill(self, kind, text):
        bg, fg = PILL.get(kind, PILL["info"])
        short = text if len(text) <= 26 else text.split(" (")[0]
        self.pill.setText(short)
        self.detail.setVisible(short != text)
        self.detail.setText(text[len(short):].strip().strip("()") if short != text else "")
        self.detail.setStyleSheet(f"color:{fg};font-size:12px")
        self.pill.setStyleSheet(f"background:{bg};color:{fg};border-radius:8px;padding:4px 10px;font-size:13px;font-weight:600")
        self.pill.setAccessibleName(f"Status {self.alias}: {text}")


class TeamDispatchPanel(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.cards = {}
        self.blocks = {}       # alias -> parsed block
        self.issues = []
        self.verify = {}       # alias -> last read-only tab check (Real Hark)
        self.vids = {}         # verify id -> alias
        self.copied = {}       # alias -> the prompt 'Copy Task' put on the clipboard
        self.click_keys = set()  # mock dispatch keys started by the current Dispatch click
        self._refused = []     # why workers of the current click were not dispatched
        self.busy = False      # a Dispatch click is in progress (second clicks are ignored)
        self.ready = 0
        self.relay = _Relay()
        self.relay.done.connect(lambda f: f())
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 18, 16, 14)
        v.setSpacing(10)
        v.addWidget(label("Team Plan & Safe Dispatch", size=22, bold=True))
        self.repo = label("", size=13, color="#2E6B00")
        self.repo.setWordWrap(True)
        self.repo.setAccessibleName("Repo team status")
        v.addWidget(self.repo)
        hb = QHBoxLayout()
        self.b_edit = button("Edit team…", "link")
        self.b_edit.setAccessibleName("Edit team")
        self.b_edit.clicked.connect(self.edit_team)
        self.b_verify = button("Verify tabs", "outline")
        self.b_verify.setAccessibleName("Verify tabs")
        self.b_verify.clicked.connect(lambda: self.verify_all())
        hb.addWidget(self.b_edit)
        hb.addStretch(1)
        hb.addWidget(self.b_verify)
        v.addLayout(hb)
        th = QHBoxLayout()
        th.addWidget(label("Target", size=14, bold=True))
        self.target = QComboBox()
        self.target.setAccessibleName("Dispatch target")
        for k, t in TARGETS:
            self.target.addItem(t, k)
        self.target.model().item(2).setEnabled(False)       # Live API: no approved access
        self.target.setCurrentIndex(max(0, self.target.findData(ctl.dispatch_target())))
        self.target.currentIndexChanged.connect(self.on_target)
        th.addWidget(self.target, 1)
        v.addLayout(th)
        self.note = label("", size=12, color="#5B616B", wrap=True)
        self.note.setAccessibleName("Target note")
        v.addWidget(self.note)
        self.live = label("", size=12, color="#C62828", wrap=True)
        self.live.setAccessibleName("Live API status")
        v.addWidget(self.live)
        self.paste_lbl = label("Paste ChatGPT Plan", size=14, bold=True)
        v.addWidget(self.paste_lbl)
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText("1. @cent2 \u2013 Add Settings validation.\n2. @cent5 \u2013 Run Smart GPS regression tests.\n3. @cent6 \u2013 Implement GPS fallback and verify.")
        self.paste.setAccessibleName("Paste ChatGPT Plan")
        self.paste.setFixedHeight(120)
        self.paste.setStyleSheet("font-family:Consolas;font-size:13px;background:white;border:1px solid #CDD1D6;border-radius:9px;padding:6px")
        self.paste.textChanged.connect(lambda: self.t_parse.start())
        v.addWidget(self.paste)
        self.issue_lbl = QLabel("")
        self.issue_lbl.setWordWrap(True)
        self.issue_lbl.setStyleSheet("color:#C62828;font-size:13px")
        self.issue_lbl.setAccessibleName("Plan issues")
        v.addWidget(self.issue_lbl)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.list = QVBoxLayout(body)
        self.list.setContentsMargins(0, 0, 4, 0)
        self.list.setSpacing(10)
        sc.setWidget(body)
        v.addWidget(sc, 1)
        self.b_send = QPushButton("\U0001F680 Dispatch to Local Mock (0 Ready)")
        self.b_send.setObjectName("primary")
        self.b_send.setMinimumHeight(52)
        self.b_send.setCursor(Qt.PointingHandCursor)
        self.b_send.setStyleSheet("font-size:16px")
        self.b_send.setAccessibleName("Dispatch to Local Mock")
        self.b_send.clicked.connect(lambda: self.dispatch())
        v.addWidget(self.b_send)
        self.foot = label("", size=12, color="#5B616B")
        self.foot.setWordWrap(True)
        self.foot.setAccessibleName("Dispatch summary")
        v.addWidget(self.foot)
        self.t_parse = QTimer(self, singleShot=True, interval=450, timeout=self.parse)
        ctl.verify_done.connect(self.on_verify)
        ctl.api.update.connect(self.on_api)
        ctl.mock_changed.connect(self.refresh_cards)
        ctl.team_changed.connect(self.on_team)
        self.apply_mode()
        QTimer.singleShot(1200, self.on_team)

    # ---------- target ----------
    def mode(self):
        return self.target.currentData() or "real"

    def on_target(self, _i=0):
        self.ctl.set_dispatch_target(self.mode())
        self.verify, self.copied = {}, {}
        self.apply_mode()
        self.parse()

    def apply_mode(self):
        mock = self.mode() == "mock"
        if mock:
            m = self.ctl.mock_hark()
            self.note.setText(f"Local Mock ({m.base_url}): Dispatch \u2192 Sent (jab mock project mein message dikhe) "
                              "\u2192 Reply received (sirf sahi account + project ka reply).")
        else:
            self.note.setText("Real Hark: Manager kuch type ya send nahi karta. 'Open Project' se worker ka exact "
                              "project kholein, 'Copy Task' se final prompt copy karein, phir Hark mein khud paste "
                              "karke Send karein.")
        self.note.setAccessibleName("Target note: " + self.note.text())
        state, text = self.ctl.live_status()
        self.live.setText(text)
        self.live.setStyleSheet(f"font-size:12px;color:{'#1E7B1E' if state == 'approved' else '#C62828'}")
        self.live.setAccessibleName(text)
        self.b_send.setVisible(mock)
        self.b_verify.setVisible(not mock)
        self._set_foot("" if mock else "Message aap khud send karenge: Manager Real Hark mein kuch nahi bhejta.")

    # ---------- team / plan ----------
    def team(self):
        return self.ctl.team()

    def worker(self, alias):
        for w in self.team().get("workers") or []:
            if (w.get("alias") or "").lower() == (alias or "").lower():
                return w
        return None

    def on_team(self):
        t = self.team()
        if not repo_team.is_configured(t):
            self.repo.setText("Team set nahi. 'Edit team…' se repo, Planner aur workers chunein.")
        else:
            threading.Thread(target=self._check_wt, daemon=True).start()
        if self.mode() == "mock":
            self.ctl.mock_hark()
        self.paste_lbl.setText("Paste ChatGPT Plan (" + ", ".join(w["alias"] for w in t.get("workers") or []) + ")")
        self.parse()

    def _check_wt(self):
        try:
            self.ctl.worktrees_ok = repo_team.verify_all(self.team())
        except Exception as e:
            log(f"team: worktree check {e}")
        self.relay.done.emit(self._wt_done)

    def _wt_done(self):
        t = self.team()
        n = len(t.get("workers") or []) + (1 if (t.get("planner") or {}).get("profile_id") else 0)
        ready = self.ctl.worktrees_ready()
        self.repo.setText(f"\U0001F517 Repo: {repo_team.repo_name(t.get('repo_path'))} ({n} Linked \u2022 "
                          f"{'Worktrees Ready' if ready else 'Worktree check: see cards'})  \u2022  base {(t.get('base_sha') or '')[:10]}")
        self.ctl.changed.emit()       # profile cards: repo badge text
        self.refresh_cards()

    def parse(self):
        if self.busy:
            self.t_parse.start()       # a Dispatch is running: apply the new plan text once it has finished
            return
        t = self.team()
        aliases = [w["alias"] for w in t.get("workers") or []]
        blocks, issues = repo_team.parse_plan(self.paste.toPlainText(), aliases)
        planner_alias = ((t.get("planner") or {}).get("alias") or "").lower()
        issues = [i for i in issues if not (planner_alias and i.lower().startswith(planner_alias + ":"))]
        self.blocks = {}
        for b in blocks:
            self.blocks.setdefault(b["alias"].lower(), b)
            if b["dup"]:
                self.blocks[b["alias"].lower()] = dict(b, dup=True)
        self.issues = issues
        self.issue_lbl.setText("\n".join("\u26A0 " + i for i in issues))
        self.issue_lbl.setAccessibleName("Plan issues: " + ("; ".join(issues) or "none"))
        self.rebuild()
        if self.blocks and self.mode() == "real":
            QTimer.singleShot(100, self.verify_all)

    def _prompt(self, alias):
        w, b = self.worker(alias), self.blocks.get((alias or "").lower())
        if not w or not b or not b["known"] or b["dup"] or b["empty"]:
            return ""
        return repo_team.build_prompt(self.team(), w, b["text"])

    def record(self, alias):
        """The Local Mock dispatch of this worker's CURRENT prompt (None if it was not dispatched)."""
        w, text = self.worker(alias), self._prompt(alias)
        if not w or not text:
            return None
        return self.ctl.api.find("mock", w["profile_id"], w["project_url"], text)

    def rebuild(self):
        while self.list.count():
            it = self.list.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
                it.widget().deleteLater()
        self.cards = {}
        for w in self.team().get("workers") or []:
            c = WorkerCard(self, w["alias"])
            self.cards[w["alias"].lower()] = c
            self.list.addWidget(c)
        planner_alias = ((self.team().get("planner") or {}).get("alias") or "").lower()
        for a, b in self.blocks.items():
            if not b["known"] and a != planner_alias:      # the Planner's own block is never a worker card
                c = WorkerCard(self, b["alias"])
                self.cards[a] = c
                self.list.addWidget(c)
        self.list.addStretch(1)
        self.refresh_cards()

    def _mock_problem(self, w):
        """Why this worker cannot be dispatched to the mock now (kind, text), or None."""
        wt = self.ctl.worktrees_ok.get(w["alias"])
        if not wt:
            return "info", "Checking worktree\u2026"
        if not wt[0]:
            return "block", f"\u26A0 Blocked (Worktree: {wt[1]})"
        if not self.ctl.worker_account(w)[0]:
            return "block", "\u26A0 Blocked (Account set nahi)"
        if not repo_team.project_id(w.get("project_url")):
            return "block", "\u26A0 Blocked (Project URL not set)"
        return None

    @staticmethod
    def _rec_pill(rec):
        kind, text = MOCK_PILL.get(rec["status"], ("block", rec["status"]))
        if rec["status"] == "sent":
            return kind, text + " (reply ka intezar)"
        if rec["reason"] and rec["status"] != "reply":
            return kind, f"{text} ({rec['reason']})"
        return kind, text

    def status_of(self, alias):
        """(kind, text, ready) for a worker card. ready = Local Mock Dispatch may send it now."""
        a = alias.lower()
        b, w = self.blocks.get(a), self.worker(alias)
        if not w:
            return "block", "\u26A0 Unknown alias (not in team)", False
        if not b:
            return "info", "No task in plan", False
        if b["dup"]:
            return "block", "\u26A0 Duplicate blocks", False
        if b["empty"]:
            return "block", "\u26A0 Empty block", False
        if self.mode() == "mock":
            rec, problem = self.record(alias), self._mock_problem(w)
            if rec:
                kind, text = self._rec_pill(rec)
                # blocked / unconfirmed may be tried again; once maybe delivered, always with the same idempotency key
                again = rec["final"] and rec["status"] in ("blocked", "unconfirmed") and not problem
                return kind, text + (" \u2014 Dispatch = retry" if again else ""), again
            if problem:
                return problem[0], problem[1], False
            return "ok", "\u2713 Ready (Local Mock)", True
        wt = self.ctl.worktrees_ok.get(w["alias"])
        if wt and not wt[0]:
            return "block", f"\u26A0 Blocked (Worktree: {wt[1]})", False
        prompt = self._prompt(alias)
        if prompt and self.copied.get(a) == prompt:
            return "sent", "\U0001F4CB Copied (Hark mein paste karke khud Send karein)", False
        v = self.verify.get(a)
        if not v:
            return "info", "Checking tab\u2026", False
        if not v.get("ok"):
            return "warn", f"Tab: {v.get('reason')} (Open Project)", False
        if not wt:
            return "info", "Checking worktree\u2026", False
        return "ok", "\u2713 Tab Verified", False

    def refresh_cards(self):
        mode, ready = self.mode(), 0
        base = (self.team().get("base_sha") or "")[:10] or "-"
        for a, c in self.cards.items():
            w, b = self.worker(a), self.blocks.get(a)
            prompt = self._prompt(a)
            if w:
                acc, src = self.ctl.worker_account(w)
                c.set_field("account", f"Account: {acc} ({'team' if src == 'team' else 'Cent profile ka Hark email'})"
                            if acc else "Account: set nahi (Edit team\u2026 mein likhein)", bad=not acc)
                p = self.ctl.by_tag.get(w.get("profile_id") or "")
                c.set_field("profile", f"Cent profile: {p.name if p else '(missing)'} [{w.get('profile_id') or '-'}]",
                            bad=not p)
                u = w.get("project_url") or ""
                c.set_field("project", "Hark project: " + (u or "(not set)"), bad=not repo_team.project_id(u))
                c.set_field("branch", f"Branch: {w.get('branch') or repo_team.default_branch(w['alias'])}  \u2022  base {base}")
                c.set_field("worktree", f"Git worktree: {w.get('worktree') or '(team save par banega)'}",
                            bad=not w.get("worktree"))
            else:
                c.set_field("account", "not in team", bad=True)
                for k in FIELDS[1:]:
                    c.set_field(k, "")
            c.task.setText((b or {}).get("text") or "(no task)")
            c.task.setAccessibleName(f"Task {c.alias}")
            c.full.setPlainText(prompt or "(nothing will be sent)")
            c.more.setVisible(bool(w))
            real, mock = mode == "real", mode == "mock"
            wt = self.ctl.worktrees_ok.get(w["alias"]) if w else None
            c.b_open.setVisible(real and bool(w))
            c.b_copy.setVisible(real and bool(w))
            c.b_open.setEnabled(bool(w) and bool(repo_team.project_id(w.get("project_url"))) and
                                w.get("profile_id") in self.ctl.by_tag)
            c.b_copy.setEnabled(bool(prompt) and bool(wt and wt[0]))
            c.b_copy.setToolTip("Final prompt clipboard par copy hota hai" if c.b_copy.isEnabled() else
                                "Task ya clean worktree nahi: copy band")
            c.mock.setVisible(mock and bool(w))
            c.b_switch.setVisible(mock and bool(w))
            rec = self.record(a) if mock else None
            if mock and w:
                sess, acc = self.ctl.mock_session(w["profile_id"]), self.ctl.worker_account(w)[0]
                mism = sess != acc
                txt = f"Mock session: {sess or 'signed out'}" + ("  (account mismatch: dispatch blocked hoga)" if mism else "")
                c.mock.setText(breakable(txt))
                c.mock.setAccessibleName(f"mock {c.alias}: {txt}")
                c.mock.setStyleSheet(f"font-size:12px;color:{'#C62828' if mism else '#1F5FBF'}")
            c.reply.setVisible(bool(rec and rec["status"] == "reply"))
            if rec and rec["status"] == "reply":
                c.reply.setText("Reply: " + rec["reply"])
                c.reply.setAccessibleName(f"Reply {c.alias}: {rec['reply']}")
            kind, text, ok = self.status_of(c.alias)
            c.set_pill(kind, text)
            ready += ok
        self.ready = ready
        self.b_send.setText(f"\U0001F680 Dispatch to Local Mock ({ready} Ready)" if not self.busy else "\u23F3 Dispatching\u2026")
        self.b_send.setEnabled(mode == "mock" and ready > 0 and not self.busy)
        self.b_send.setAccessibleName(f"Dispatch to Local Mock ({ready} Ready)" + (" busy" if self.busy else ""))
        self.b_verify.setEnabled(not self.busy)
        self.target.setEnabled(not self.busy)
        self.paste.setReadOnly(self.busy)          # the plan is frozen while it is being dispatched

    # ---------- Real Hark: manual send ----------
    def verify_all(self):
        """Read-only check of each worker's exact project tab (nothing is typed)."""
        if self.mode() != "real":
            return
        if repo_team.is_configured(self.team()):
            threading.Thread(target=self._check_wt, daemon=True).start()     # worktrees too, fresh
        for w in self.team().get("workers") or []:
            a = w["alias"].lower()
            if a not in self.blocks:
                continue
            if not repo_team.PROJECT_URL_RE.match(w.get("project_url") or ""):
                self.verify[a] = {"ok": False, "reason": "Project URL not set"}
                continue
            self.verify.pop(a, None)
            self.vids[self.ctl.verify_tab(w["profile_id"], w["project_url"])] = a
        self.refresh_cards()

    def on_verify(self, vid, res):
        if vid not in self.vids:
            return
        self.verify[self.vids.pop(vid)] = res
        self.refresh_cards()

    def open_project(self, alias):
        w = self.worker(alias)
        if not w:
            return
        ok = self.ctl.open_project(w["profile_id"], w.get("project_url") or "")
        p = self.ctl.by_tag.get(w["profile_id"])
        self._set_foot(f"{w['alias']}: {p.name if p else w['profile_id']} mein project khul raha hai \u2014 "
                       f"{w['project_url']}" if ok else f"{w['alias']}: project URL ya profile ghalat")
        QTimer.singleShot(2500, self.verify_all)

    def copy_task(self, alias):
        text = self._prompt(alias)
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.copied[alias.lower()] = text
        log(f"team: copied final prompt for {alias} ({len(text)} chars)")
        self._set_foot(f"{alias}: final prompt copy ho gaya ({len(text)} chars). Hark mein paste karke khud Send karein.")
        self.refresh_cards()

    # ---------- Local Mock: automatic dispatch ----------
    def switch_mock(self, alias):
        w = self.worker(alias)
        if not w or self.mode() != "mock":
            return
        email = self.ctl.mock_switch_account(w["profile_id"])
        self._set_foot(f"Mock: Cent profile [{w['profile_id']}] ab {email} par signed in hai.")
        self.refresh_cards()

    def dispatch(self):
        if self.busy or self.mode() != "mock":
            return                                  # double click: the first click is still working
        self.busy = True
        cand = [a for a, c in self.cards.items() if self.status_of(c.alias)[2]]
        snap = {a: self._prompt(a) for a in cand}   # exactly what the preview showed at this click
        self.click_keys = set()
        self.ctl.mock_hark()                        # started and synced here, in the UI thread
        log(f"mock dispatch click: {len(cand)} candidate(s) {cand}")
        self._set_foot("Worktrees dobara verify ho rahe hain\u2026")
        self.refresh_cards()
        threading.Thread(target=self._recheck_wt, args=(cand, snap), daemon=True).start()

    def _recheck_wt(self, cand, snap):
        res = {}
        try:
            res = repo_team.verify_all(self.team())
        except Exception as e:
            log(f"dispatch: worktree recheck {e}")
        self.relay.done.emit(lambda: self._after_wt(cand, snap, res))

    def _after_wt(self, cand, snap, res):
        self.ctl.worktrees_ok = res
        refused = []
        for a in cand:
            w = self.worker(a)
            if not w or not (res.get(w["alias"]) or (False, "not checked"))[0]:
                refused.append(f"{a}: worktree")
                continue
            acc = self.ctl.worker_account(w)[0]
            rec, why = self.ctl.api.dispatch("mock", w["profile_id"], acc, w["project_url"], snap[a], w["alias"])
            if rec:
                self.click_keys.add(rec["key"])
            else:
                refused.append(f"{w['alias']}: {why}")
        self._refused = refused
        if not self.click_keys:
            return self._finish("Kuch nahi bheja. " + "; ".join(refused))
        self._set_foot(f"{len(self.click_keys)} prompt(s) Local Mock ko ja rahe hain\u2026 'Sent' sirf mock project "
                       "mein message dikhne par." + (" Refused: " + "; ".join(refused) if refused else ""))
        self.refresh_cards()

    def on_api(self, key, rec):
        if key in self.click_keys:
            recs = [r for r in (self.ctl.api.record(k) for k in self.click_keys) if r]
            done = {}
            for r in recs:
                done[r["status"]] = done.get(r["status"], 0) + 1
            extra = getattr(self, "_refused", [])
            msg = ", ".join(f"{k}: {n}" for k, n in done.items()) + (". Refused: " + "; ".join(extra) if extra else "")
            if self.busy and all(r["status"] not in IN_FLIGHT for r in recs):
                return self._finish(msg)
            if not self.busy:
                self._set_foot(msg)
        self.refresh_cards()

    def _set_foot(self, msg):
        self.foot.setText(msg)
        self.foot.setAccessibleName("Dispatch summary: " + msg)

    def _finish(self, msg):
        self.busy = False
        self._set_foot(msg)
        log(f"mock dispatch click finished: {msg}")
        self.refresh_cards()

    def edit_team(self):
        open_team_dialog(self.ctl, self)


def open_team_dialog(ctl, parent):
    """Window-modal, non-blocking (open, not exec): one editor at a time."""
    d = getattr(ctl, "_team_dlg", None)
    try:
        if d is not None and d.isVisible():
            d.raise_()
            d.activateWindow()
            return d
    except RuntimeError:
        pass
    d = TeamDialog(ctl, parent.window())
    d.setAttribute(Qt.WA_DeleteOnClose, True)
    ctl._team_dlg = d
    d.open()
    return d


class TeamDialog(QDialog):
    """Edit the Shared Repo Team. Save validates it, then creates/reuses one worktree per worker (git)."""

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Shared Repo Team")
        from .style import build_qss
        self.setStyleSheet(build_qss())
        self.resize(900, 560)
        t = ctl.team()
        v = QVBoxLayout(self)
        v.setSpacing(10)
        v.addWidget(label("Shared Repo Team", size=20, bold=True))
        v.addWidget(label("Ek local git repo, ek base commit, har worker ka apna worktree + branch (agent-<alias>).", obj="muted", wrap=True))
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        self.repo = QLineEdit(t.get("repo_path") or "")
        self.repo.setAccessibleName("Repo path")
        self.repo.setPlaceholderText(r"D:\Code\GeoFrost-App")
        br = button("Browse…", "outline")
        br.clicked.connect(self.pick_repo)
        self.base = QLineEdit(t.get("base_ref") or "HEAD")
        self.base.setAccessibleName("Base ref")
        g.addWidget(label("Repo folder", bold=True), 0, 0)
        g.addWidget(self.repo, 0, 1)
        g.addWidget(br, 0, 2)
        g.addWidget(label("Base ref", bold=True), 1, 0)
        g.addWidget(self.base, 1, 1)
        v.addLayout(g)
        self.profiles = [(p.name, p.tag) for p in ctl.profiles]
        v.addWidget(label("Planner (ChatGPT)", size=15, bold=True))
        pl = t.get("planner") or {}
        ph = QHBoxLayout()
        self.p_prof = self._combo(pl.get("profile_id"), "Planner profile")
        self.p_alias = QLineEdit(pl.get("alias") or "@cent1")
        self.p_alias.setAccessibleName("Planner alias")
        self.p_alias.setMaximumWidth(120)
        self.p_url = QLineEdit(pl.get("project_url") or "")
        self.p_url.setAccessibleName("Planner project URL")
        self.p_url.setPlaceholderText("optional: https://hark.com/project/<id>")
        ph.addWidget(self.p_prof, 2)
        ph.addWidget(self.p_alias, 1)
        ph.addWidget(self.p_url, 4)
        v.addLayout(ph)
        v.addWidget(label("Workers", size=15, bold=True))
        self.rows_w = QVBoxLayout()
        self.rows = []
        v.addLayout(self.rows_w)
        for w in t.get("workers") or []:
            self.add_row(w)
        if not self.rows:
            for a in ("@cent2", "@cent5", "@cent6"):
                self.add_row({"alias": a})
        add = button("+ Add worker", "link")
        add.clicked.connect(lambda: self.add_row({"alias": f"@cent{len(self.rows) + 2}"}))
        v.addWidget(add, 0, Qt.AlignLeft)
        v.addStretch(1)
        self.msg = label("", size=13, color="#C62828", wrap=True)
        self.msg.setAccessibleName("Team save result")
        v.addWidget(self.msg)
        bh = QHBoxLayout()
        bh.addStretch(1)
        c = button("Cancel", "plain")
        c.clicked.connect(self.reject)
        s = button("Save team", "primary")
        s.setAccessibleName("Save team")
        s.clicked.connect(self.save)
        bh.addWidget(c)
        bh.addWidget(s)
        v.addLayout(bh)

    def _combo(self, cur, name):
        cb = QComboBox()
        cb.setAccessibleName(name)
        cb.addItem("(none)", "")
        for n, tag in self.profiles:
            cb.addItem(f"{n}  [{tag}]", tag)
        i = cb.findData(cur or "")
        cb.setCurrentIndex(max(0, i))
        return cb

    def add_row(self, w):
        n = len(self.rows) + 1
        h = QHBoxLayout()
        prof = self._combo(w.get("profile_id"), f"Worker {n} profile")
        alias = QLineEdit(w.get("alias") or "")
        alias.setAccessibleName(f"Worker {n} alias")
        alias.setMaximumWidth(120)
        url = QLineEdit(w.get("project_url") or "")
        url.setAccessibleName(f"Worker {n} project URL")
        url.setPlaceholderText("https://hark.com/project/<id>")
        acct = QLineEdit(w.get("account") or "")
        acct.setAccessibleName(f"Worker {n} Hark account")
        self._complete(url, prof)
        self._account_hint(acct, prof)
        prof.currentIndexChanged.connect(lambda _=0: (self._complete(url, prof), self._account_hint(acct, prof)))
        rm = button("\u2715", "link")
        rm.setAccessibleName(f"Remove worker {n}")
        row = {"prof": prof, "alias": alias, "url": url, "acct": acct,
               "keep": {k: w.get(k) for k in ("branch", "worktree") if w.get(k)}}
        rm.clicked.connect(lambda: self.remove_row(row, h))
        for x, st in ((prof, 2), (alias, 1), (url, 4), (acct, 3), (rm, 0)):
            h.addWidget(x, st)
        self.rows_w.addLayout(h)
        self.rows.append(row)

    def _account_hint(self, edit, combo):
        """Empty = the Hark email this profile saved at setup (shown as the placeholder)."""
        p = self.ctl.by_tag.get(combo.currentData() or "")
        email = (p.email if p else "") or ""
        edit.setPlaceholderText(email or "Hark account email")
        edit.setToolTip("Is worker ka Hark account. Khali = profile ka saved Hark email" + (f" ({email})" if email else ""))

    def _complete(self, edit, combo):
        """Suggest the exact project URLs that profile's Hark sidebar shows."""
        from PySide6.QtWidgets import QCompleter
        urls = self.ctl.project_urls(combo.currentData() or "")
        c = QCompleter([u for u, _ in urls], edit)
        c.setCaseSensitivity(Qt.CaseInsensitive)
        c.setFilterMode(Qt.MatchContains)
        edit.setCompleter(c)
        edit.setToolTip("\n".join(f"{t}: {u}" for u, t in urls) or "Is profile ke projects abhi nahi mile (Hark tab kholein)")

    def remove_row(self, row, h):
        self.rows.remove(row)
        while h.count():
            it = h.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def pick_repo(self):
        d = QFileDialog.getExistingDirectory(self, "Git repo folder", self.repo.text() or "C:\\")
        if d:
            self.repo.setText(os.path.normpath(d))

    def collect(self):
        old = self.ctl.team()
        t = repo_team.empty_team()
        t["repo_path"] = self.repo.text().strip().strip('"')
        t["base_ref"] = self.base.text().strip() or "HEAD"
        if os.path.normcase(t["repo_path"]) == os.path.normcase(old.get("repo_path") or ""):
            t["worktree_root"] = old.get("worktree_root") or ""
        t["planner"] = {"profile_id": self.p_prof.currentData() or "", "alias": self.p_alias.text().strip(),
                        "project_url": self.p_url.text().strip()}
        for r in self.rows:
            alias = r["alias"].text().strip()
            if alias and not alias.startswith("@"):
                alias = "@" + alias
            w = {"profile_id": r["prof"].currentData() or "", "alias": alias, "project_url": r["url"].text().strip()}
            if r["acct"].text().strip():
                w["account"] = r["acct"].text().strip().lower()
            if os.path.normcase(t["repo_path"]) == os.path.normcase(old.get("repo_path") or ""):
                w.update(r["keep"])
            t["workers"].append(w)
        return t

    def save(self):
        t = self.collect()
        errs = repo_team.validate(t, {p.tag for p in self.ctl.profiles})
        if errs:
            self.msg.setText("\n".join(errs))
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            t, res = self.ctl.save_team(t)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self.msg.setText(f"Save nahi hua: {e}")
            return
        QApplication.restoreOverrideCursor()
        lines = [f"{a}: {'OK - ' if r[0] else 'BLOCKED - '}{r[1]}" for a, r in res.items()]
        bad = [a for a, r in res.items() if not r[0]]
        txt = f"Base {t['base_sha'][:10]} ({t['base_ref']})\n" + "\n".join(lines)
        self.msg.setStyleSheet(f"color:{'#C62828' if bad else '#1E7B1E'};font-size:13px")
        self.msg.setText(txt)
        if not bad:
            QTimer.singleShot(1200, self.accept)
