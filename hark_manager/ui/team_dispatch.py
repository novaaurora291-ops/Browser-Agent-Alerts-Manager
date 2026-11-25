"""Team Plan & Safe Dispatch (right-panel drawer) and the Shared Repo Team editor.

The plan is pasted by hand from ChatGPT (the Planner). It is split into @alias blocks; each worker card shows the
exact project URL, branch, worktree and the FULL final prompt (worktree path + branch + base SHA first).
Dispatch sends only to workers whose worktree is clean and on its branch AND whose exact Hark project tab is
freshly verified (authenticated profile, exact active tab ID, exact URL, ready + empty composer).
"Sent" shows only when the page shows the new user message; otherwise "Unconfirmed", never re-sent."""
import os
import threading

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QFrame,
                               QPushButton, QDialog, QLineEdit, QComboBox, QFileDialog, QGridLayout,
                               QApplication)

from .widgets import label, button
from .. import repo_team
from ..config import log

PILL = {   # kind -> (background, text colour)
    "ok": ("#DDF5D0", "#1E7B1E"), "sent": ("#C9F0B0", "#14620F"), "warn": ("#FFF1D6", "#8A5A00"),
    "block": ("#FDE2E0", "#C62828"), "info": ("#EEF0F2", "#4A5058"), "run": ("#E6F0FF", "#1F5FBF"),
}


class _Relay(QObject):
    done = Signal(object)


class WorkerCard(QFrame):
    def __init__(self, panel, alias):
        super().__init__()
        self.panel, self.alias = panel, alias
        self.setObjectName("card")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(5)
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
        self.branch = label("", size=14)
        self.project = label("", size=12, color="#5B616B")
        self.project.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.wt = label("", size=12, color="#5B616B")
        self.wt.setWordWrap(True)
        v.addWidget(self.branch)
        v.addWidget(self.project)
        v.addWidget(self.wt)
        self.task = QLabel("")
        self.task.setWordWrap(True)
        self.task.setStyleSheet("background:#F3F4F5;border-radius:8px;padding:8px 10px;font-family:Consolas;font-size:13px")
        v.addWidget(self.task)
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

    def toggle(self):
        on = not self.full.isVisible()
        self.full.setVisible(on)
        self.more.setText("Full prompt \u25BE" if on else "Full prompt \u25B8")

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
        self.verify = {}       # alias -> last tab check result
        self.vids = {}         # verify id -> (alias, purpose)
        self.disp = {}         # alias -> dispatch record of the current plan
        self.busy = False      # a Dispatch click is in progress (second clicks are ignored)
        self._want, self._fresh = set(), {}
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
        self.b_send = QPushButton("\U0001F680 Dispatch to Verified Agents (0 Ready)")
        self.b_send.setObjectName("primary")
        self.b_send.setMinimumHeight(52)
        self.b_send.setCursor(Qt.PointingHandCursor)
        self.b_send.setStyleSheet("font-size:16px")
        self.b_send.setAccessibleName("Dispatch to Verified Agents")
        self.b_send.clicked.connect(lambda: self.dispatch())
        v.addWidget(self.b_send)
        self.foot = label("", size=12, color="#5B616B")
        self.foot.setWordWrap(True)
        self.foot.setAccessibleName("Dispatch summary")
        v.addWidget(self.foot)
        self.t_parse = QTimer(self, singleShot=True, interval=450, timeout=self.parse)
        ctl.verify_done.connect(self.on_verify)
        ctl.dispatch_update.connect(self.on_dispatch)
        ctl.team_changed.connect(self.on_team)
        QTimer.singleShot(1200, self.on_team)

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
        # a new plan text: earlier dispatch results belong to the old text
        self.disp = {a: r for a, r in self.disp.items() if self._prompt(a) and self.ctl.prompt_key(r["tag"], r["url"], self._prompt(a)) == r.get("key")}
        self.rebuild()
        if self.blocks and not self.busy:
            QTimer.singleShot(100, self.verify_all)

    def _prompt(self, alias):
        w, b = self.worker(alias), self.blocks.get((alias or "").lower())
        if not w or not b or not b["known"] or b["dup"] or b["empty"]:
            return ""
        return repo_team.build_prompt(self.team(), w, b["text"])

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

    def status_of(self, alias):
        """(kind, text, ready) for a worker card."""
        a = alias.lower()
        b, w = self.blocks.get(a), self.worker(alias)
        d = self.disp.get(a)
        if d:
            st, why = d["status"], d.get("reason") or ""
            return {"queued": ("run", "\u23F3 Sending\u2026", False), "sending": ("run", "\u23F3 Sending\u2026", False),
                    "sent": ("sent", "\u2713 Sent", False), "unconfirmed": ("warn", "\u26A0 Unconfirmed", False),
                    "duplicate": ("block", "\u26A0 Duplicate (not sent)", False),
                    "expired": ("block", "\u26A0 Not sent (expired)", False)}.get(st, ("block", f"\u26A0 Blocked ({why})", False))
        if not w:
            return "block", "\u26A0 Unknown alias (not in team)", False
        if not b:
            return "info", "No task in plan", False
        if b["dup"]:
            return "block", "\u26A0 Duplicate blocks", False
        if b["empty"]:
            return "block", "\u26A0 Empty block", False
        wt = self.ctl.worktrees_ok.get(w["alias"])
        if wt and not wt[0]:
            return "block", f"\u26A0 Blocked (Worktree: {wt[1]})", False
        prev = self.ctl.already_sent(w["profile_id"], w["project_url"], self._prompt(alias))
        if prev and prev["status"] not in ("blocked", "expired"):
            return "warn", f"Already dispatched ({prev['status']})", False
        v = self.verify.get(a)
        if not v:
            return "info", "Checking tab\u2026", False
        if not v.get("ok"):
            return "block", f"\u26A0 Blocked ({v.get('reason')})", False
        if not wt:
            return "info", "Checking worktree\u2026", False
        return "ok", "\u2713 Tab Verified", True

    def refresh_cards(self):
        ready = 0
        for a, c in self.cards.items():
            w, b = self.worker(a), self.blocks.get(a)
            if w:
                c.branch.setText(f"branch: {w.get('branch') or repo_team.default_branch(w['alias'])}")
                u = w.get("project_url") or "(project URL not set)"
                c.project.setText(c.project.fontMetrics().elidedText(u, Qt.ElideMiddle, 300))
                c.project.setToolTip(u)
                c.project.setAccessibleName(f"Project {c.alias}: {u}")
                wt = f"worktree: {w.get('worktree') or '-'}"
                c.wt.setText(c.wt.fontMetrics().elidedText(wt, Qt.ElideMiddle, 300))
                c.wt.setToolTip(wt)
            else:
                c.branch.setText("not in team")
                c.project.setText("")
                c.wt.setText("")
            c.task.setText((b or {}).get("text") or "(no task)")
            c.task.setAccessibleName(f"Task {c.alias}")
            full = self._prompt(a)
            c.full.setPlainText(full or "(nothing will be sent)")
            c.more.setVisible(bool(w))
            kind, text, ok = self.status_of(c.alias)
            c.set_pill(kind, text)
            ready += ok
        self.ready = ready
        self.b_send.setText(f"\U0001F680 Dispatch to Verified Agents ({ready} Ready)" if not self.busy else "\u23F3 Dispatching\u2026")
        self.b_send.setEnabled(ready > 0 and not self.busy)
        self.b_verify.setEnabled(not self.busy)
        self.paste.setReadOnly(self.busy)          # the plan is frozen while it is being dispatched
        self.b_send.setAccessibleName(f"Dispatch to Verified Agents ({ready} Ready)" + (" busy" if self.busy else ""))

    # ---------- tab checks ----------
    def verify_all(self, purpose="show"):
        if self.busy:
            return            # a Dispatch is checking its tabs: a second check would replace its fresh one
        if repo_team.is_configured(self.team()):
            threading.Thread(target=self._check_wt, daemon=True).start()     # worktrees too, fresh
        for w in self.team().get("workers") or []:
            a = w["alias"].lower()
            if a not in self.blocks or a in self.disp and self.disp[a]["status"] in ("queued", "sending"):
                continue
            if not repo_team.PROJECT_URL_RE.match(w.get("project_url") or ""):
                self.verify[a] = {"ok": False, "reason": "Project URL not set"}
                continue
            self.verify.pop(a, None)
            vid = self.ctl.verify_tab(w["profile_id"], w["project_url"])
            self.vids[vid] = (a, purpose)
        self.refresh_cards()

    def on_verify(self, vid, res):
        if vid not in self.vids:
            return
        a, purpose = self.vids.pop(vid)
        self.verify[a] = res
        if purpose == "dispatch":
            self._fresh[a] = res
            if all(x in self._fresh for x in self._want):
                self._send_verified()
        self.refresh_cards()

    # ---------- dispatch ----------
    def dispatch(self):
        if self.busy:
            return                                  # double click: the first click is still working
        self.busy = True
        self.b_send.setEnabled(False)
        self.b_send.setText("\u23F3 Dispatching\u2026")
        cand = [a for a, c in self.cards.items() if self.status_of(c.alias)[2]]
        self._snap = {a: self._prompt(a) for a in cand}      # exactly what the preview showed at this click
        self.paste.setReadOnly(True)
        self.b_verify.setEnabled(False)
        log(f"dispatch click: {len(cand)} candidate(s) {cand}")
        self._set_foot("Worktrees aur tabs dobara verify ho rahe hain\u2026")
        threading.Thread(target=self._recheck_wt, args=(cand,), daemon=True).start()

    def _recheck_wt(self, cand):
        res = {}
        try:
            res = repo_team.verify_all(self.team())
        except Exception as e:
            log(f"dispatch: worktree recheck {e}")
        self.relay.done.emit(lambda: self._after_wt(cand, res))

    def _after_wt(self, cand, res):
        self.ctl.worktrees_ok = res
        ok = [a for a in cand if (res.get(self.worker(a)["alias"]) or (False,))[0]]
        self._want, self._fresh = set(ok), {}
        if not ok:
            return self._finish("Koi worker ready nahi (worktree check).")
        for a in ok:
            w = self.worker(a)
            vid = self.ctl.verify_tab(w["profile_id"], w["project_url"])
            self.vids[vid] = (a, "dispatch")
        self.refresh_cards()

    def _send_verified(self):
        sent = 0
        for a in sorted(self._want):
            v, w = self._fresh.get(a) or {}, self.worker(a)
            if not v.get("ok") or not w:
                continue
            text = (getattr(self, "_snap", None) or {}).get(a) or ""     # the prompt previewed at the click
            if not text:
                continue
            rec = self.ctl.send_prompt(w["profile_id"], v["tab_id"], w["project_url"], text, w["alias"])
            if rec:
                self.disp[a] = rec
                sent += 1
        self._want = set()
        if not sent:
            return self._finish("Fresh check mein koi tab verify nahi hua: kuch nahi bheja.")
        self._set_foot(f"{sent} prompt(s) bheje ja rahe hain\u2026 'Sent' sirf page mein naya message dikhne par.")
        self.refresh_cards()

    def on_dispatch(self, did, rec):
        a = (rec.get("alias") or "").lower()
        if a in self.disp and self.disp[a]["id"] == did:
            self.disp[a] = rec
        if self.busy and not self._want and all(r["status"] not in ("queued", "sending") for r in self.disp.values()):
            done = {}
            for r in self.disp.values():
                done[r["status"]] = done.get(r["status"], 0) + 1
            self._finish(", ".join(f"{k}: {n}" for k, n in done.items()))
        self.refresh_cards()

    def _set_foot(self, msg):
        self.foot.setText(msg)
        self.foot.setAccessibleName("Dispatch summary: " + msg)

    def _finish(self, msg):
        self.busy = False
        self._snap = {}
        self._set_foot(msg)
        log(f"dispatch finished: {msg}")
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
        self._complete(url, prof)
        prof.currentIndexChanged.connect(lambda _=0: self._complete(url, prof))
        rm = button("\u2715", "link")
        rm.setAccessibleName(f"Remove worker {n}")
        row = {"prof": prof, "alias": alias, "url": url, "keep": {k: w.get(k) for k in ("branch", "worktree") if w.get(k)}}
        rm.clicked.connect(lambda: self.remove_row(row, h))
        for x, st in ((prof, 2), (alias, 1), (url, 4), (rm, 0)):
            h.addWidget(x, st)
        self.rows_w.addLayout(h)
        self.rows.append(row)

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
