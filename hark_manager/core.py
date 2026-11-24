"""Controller: profiles + live state from the bridge, alert lifecycle, notifications, previews."""
import threading
import os
import time
EXT_VERSION = "1.0.16"   # must equal extension/manifest.json "version"
ALERT_GRACE = 15  # an alert Hark no longer shows as waiting (live DOM) is cleared after this many seconds

from PySide6.QtCore import QObject, QTimer, Signal

from . import chrome, notify, config, repo_team
from .alerts import AlertStore
from .bridge import Bridge
from .config import Settings, get_bot_token, log

STALE = 20  # seconds without a report = bridge not live for that profile


def conv_key(url):
    """Stable id of a Hark conversation: project:<uuid> or chat."""
    import re
    m = re.search(r"hark\.com/project/([0-9a-fA-F-]{8,})", url or "")
    if m:
        return "project:" + m.group(1).lower()
    return "chat"


HOSTED_FILE = os.path.join(config.APPDATA, "hosted.json")


class Hosted(dict):
    """Windows hosted inside the Manager, mirrored to disk: if the Manager dies while hosting them, Windows closes
    those browser windows with it, and the next start reopens them (with their tabs) through each profile's bridge."""
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl

    def _save(self):
        import json
        try:
            os.makedirs(os.path.dirname(HOSTED_FILE), exist_ok=True)
            with open(HOSTED_FILE, "w", encoding="utf-8") as f:
                json.dump({"dirs": list(self.keys()), "tabs": {d: self.ctl.tab_urls(d) for d in self.keys()},
                           "t": time.time()}, f)
        except Exception as e:
            log(f"hosted save: {e}")

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self._save()

    def __delitem__(self, k):
        super().__delitem__(k)
        self._save()

    def pop(self, k, *d):
        r = super().pop(k, *d)
        self._save()
        return r


class Controller(QObject):
    changed = Signal()                 # profiles/status/alerts changed
    previews_changed = Signal()
    new_alert = Signal(dict)           # fresh alert -> desktop notification
    telegram_status = Signal(bool, str)
    need_telegram_setup = Signal(str)
    limit_result = Signal(str, dict)   # profile_dir, read-only Hark limit probe from the bridge
    view_request = Signal(str, str)    # profile_dir, preferred window title -> show it inside the Manager
    team_changed = Signal()            # Shared Repo Team saved / worktrees verified
    verify_done = Signal(str, dict)    # verify id, result
    dispatch_update = Signal(str, dict)  # dispatch id, record

    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.alerts = AlertStore()
        self.bridge = Bridge()
        self.bridge.ext_version = EXT_VERSION
        self.bridge_ok = self.bridge.start()
        self.verifies = {}    # verify id -> {tag, url, t}
        self.dispatches = {}  # dispatch id -> record (Safe Dispatch)
        self.sent_keys = {}   # (tag, url, sha256(prompt)) -> dispatch id: the same prompt is never sent twice
        self.worktrees_ok = {}  # alias -> (ok, reason) from the last git check
        self.cent_error = ""
        self.profiles = []
        self.by_email = {}
        self.by_tag = {}
        self.state = {}       # profile_dir -> {"t":..., "tabs":[...], "hark":{tabId: state}}
        self.windows = {}     # profile_dir -> [(hwnd,title,aumid,minimized)]
        self.embedded = Hosted(self)  # profile_dir -> hwnd of its browser window currently hosted inside the Manager
        self.no_dock = set()  # profile dirs whose windows must stay on the desktop for now (setup, bridge install)

        self.previews = {}    # profile_dir -> png bytes
        self.tg_ok = False
        self.tg_msg = "Telegram not set up"
        self._capturing = False
        self.seen = {}        # alert qid -> last time a live Hark tab showed it still waiting
        # alerts made by the older detector (scanned old messages for "?") are not trustworthy: clear them once
        for a in self.alerts.open_alerts():
            if a.get("v") != 2:
                self.alerts.resolve(a, "made by older detector, re-checked live")
        self.reload_profiles()

        self.t_bridge = QTimer(self, timeout=self.drain_bridge, interval=400)
        self.t_bridge.start()
        QTimer.singleShot(1500, self.recover_hosted)
        self.t_hosted = QTimer(self, interval=10000, timeout=lambda: self.embedded and self.embedded._save())
        self.t_hosted.start()
        self.t_windows = QTimer(self, timeout=self.refresh_windows, interval=1500)
        self.t_windows.start()
        self.t_prev = QTimer(self, timeout=self.refresh_previews)
        self.apply_preview_interval()
        QTimer.singleShot(300, self.refresh_windows)
        QTimer.singleShot(800, self.refresh_previews)
        QTimer.singleShot(500, self.check_telegram)
        self.t_dispatch = QTimer(self, timeout=self._dispatch_tick, interval=500)
        self.t_dispatch.start()
        self.t_snap = QTimer(self, timeout=self._write_snapshot, interval=4000)
        self.t_snap.start()

    # ---------- profiles ----------
    def reload_profiles(self):
        """Only Cent Browser profiles made by this tool, linked to the configured Cent install. Google Chrome's
        own profiles (and Edge / Brave) are not scanned at all."""
        aliases = self.settings["aliases"] or {}
        cent = self.cent_exe()
        chrome.set_cent_exe(cent)
        self.cent_error = self.cent_status()[1]
        cdir = os.path.dirname(chrome.norm_path(cent)) if cent else ""
        managed = []
        for m in self.settings["managed_profiles"] or []:
            rexe = chrome.norm_path((m.get("exe") or "").strip('"'))
            if m.get("legacy") or not cent or (rexe and os.path.dirname(rexe) != cdir):
                continue      # made with another browser / another Cent install: kept in settings, not linked
            p = chrome.Profile("hbm:" + m["id"], m.get("name") or m["id"])
            p.kind, p.tag, p.data_dir, p.exe = "managed", m["id"], m["data_dir"], m.get("exe", "")
            p.email = chrome.managed_email(p.data_dir) or m.get("email", "")
            p.alias = aliases.get(p.directory, "")
            managed.append(p)
        self.profiles = managed
        self.alerts.only = {p.directory for p in managed}
        # bridge reports are matched by each profile's own tag only (a bridge in personal Chrome maps to nothing)
        self.by_email = {}
        self.by_tag = {p.tag: p for p in managed}
        # each managed profile's own bridge token: only requests carrying it are accepted
        toks = {}
        for p in managed:
            try:
                toks[p.tag] = config.bridge_token(p.tag)
                # current bridge files + this profile's token in its own copy: a running older bridge is told to
                # reload (bridge.py) and comes back as the new version, authenticated
                if os.path.isdir(p.data_dir):
                    config.ensure_profile_extension(p.data_dir, p.tag)
            except Exception as e:
                log(f"bridge token {p.tag}: {e}")
        self.bridge.set_tokens(toks)
        self.changed.emit()

    def bridge_key(self, p):
        if p.managed:
            return p.tag
        st = self.state.get(p.directory) or {}
        return st.get("tag") or (p.email or "").lower()

    def cent_exe(self):
        """The configured Cent Browser chrome.exe (Settings), else Cent found in its usual install folders.
        Any other browser is refused: this tool works with Cent Browser only."""
        exe = (self.settings["browser_exe"] or "").strip().strip('"')
        if exe and chrome.is_cent_exe(exe):
            return exe
        found = chrome.detect_cent()
        if found and not exe:
            self.settings["browser_exe"] = found
            self.settings.save()
        return found if found and not exe else ""

    browser_exe = cent_exe

    def cent_status(self):
        """(ok, message) about the Cent Browser exe, shown in Settings when something is wrong."""
        exe = (self.settings["browser_exe"] or "").strip().strip('"')
        if exe:
            if not os.path.isfile(exe):
                return False, f"Cent Browser exe nahi mili: {exe}. Settings mein sahi Cent chrome.exe chunein."
            if not chrome.is_cent_exe(exe):
                return False, f"Yeh Cent Browser nahi hai: {exe}. Sirf Cent Browser supported hai."
            return True, ""
        found = chrome.detect_cent()
        if not found:
            return False, "Cent Browser install nahi mila. Settings mein Cent Browser ki chrome.exe chunein."
        return True, ""

    def exe_for(self, p):
        """Every profile runs on the configured Cent exe (the one its windows are matched against)."""
        return self.cent_exe()

    def open_in(self, p, url=None, new_window=False, extra=None):
        exe = self.cent_exe()
        if not exe:
            log(f"open {p.directory}: Cent Browser path not set, nothing launched")
            return False
        # the name given in Add profile / Rename is Cent's own profile name (avatar menu), set while Cent is closed
        if not self.windows.get(p.directory) and p.directory not in self.embedded:
            try:
                chrome.set_profile_name(p.data_dir, p.name)
            except Exception as e:
                log(f"profile name {p.directory}: {e}")
        m = self._managed_rec(p.tag) or {}
        if not m.get("tagged") and not url:
            url = "https://hark.com/"
        dock = bool(self.settings["keep_docked"]) and p.directory not in self.no_dock
        try:
            chrome.launch_managed(p, exe, url, new_window, config.ensure_profile_extension(p.data_dir, p.tag), extra,
                                  minimized=dock)
        except Exception as e:
            log(f"open {p.directory}: {e}")
            return False
        return True

    def _managed_rec(self, tag):
        for m in self.settings["managed_profiles"] or []:
            if m["id"] == tag:
                return m
        return None

    def profile(self, directory):
        for p in self.profiles:
            if p.directory == directory:
                return p
        return None

    def live_tabs(self, p):
        st = self.state.get(p.directory)
        if not st or time.time() - st["t"] >= STALE:
            return []
        return list(st["hark"].values())

    def is_working(self, p):
        tabs = self.live_tabs(p)
        return any(h.get("working") or h.get("running") for h in tabs)

    def status(self, p):
        # RUNNING wins: if Hark shows a running task in this profile (live pill, Stop button, Working badge,
        # "Waiting for your task to finish", or the user's message is last) the profile is Working.
        if self.is_working(p):
            return "Running"
        if any(a["profile_dir"] == p.directory for a in self.alerts.open_alerts()):
            return "Needs reply"
        if not self.windows.get(p.directory):
            return "Closed"
        return "Idle"

    def pair_bridge(self, p):
        """Tell this profile's bridge which profile it is: open the Manager's local tag page in that profile;
        the bridge stores the tag, reports, and closes the tab itself."""
        url = f"http://127.0.0.1:{config.BRIDGE_PORT}/hbm-tag?p={p.tag}&close=1"
        log(f"pairing bridge in {p.directory}")
        self.state.setdefault(p.directory, {"t": 0, "tabs": [], "hark": {}, "email": ""})["checking"] = time.time()
        self.open_in(p, url)

    def reload_bridge(self, p):
        try:
            config.ensure_profile_extension(p.data_dir, p.tag)
        except Exception as e:
            log(f"bridge files update {p.directory}: {e}")
        st = self.state.setdefault(p.directory, {"t": 0, "tabs": [], "hark": {}, "email": ""})
        st["reload_asked"] = time.time()
        self.bridge.queue_command(self.bridge_key(p), {"type": "reload_ext"})
        self.pair_bridge(p)      # the pairing tab makes the old bridge report now and take the reload

    def check_limit(self, p, cid):
        """Ask this profile's bridge for a read-only look at its Hark tab (limit popup / message box)."""
        st = self.state.setdefault(p.directory, {"t": 0, "tabs": [], "hark": {}, "email": ""})
        st["checking"] = time.time()
        key = self.bridge_key(p)
        with self.bridge.lock:   # a reload still waiting to be picked up would cut the check short
            q = self.bridge.commands.get(key) or []
            self.bridge.commands[key] = [c for c in q if c.get("type") != "reload_ext"]
        self.bridge.queue_command(key, {"type": "check_limit", "id": cid})

    def bridge_seen(self, p, within=75):
        """The bridge checked in recently (Hark tab reports or the 30 s heartbeat)."""
        st = self.state.get(p.directory)
        return bool(st and time.time() - st["t"] < within)

    def bridge_ver(self, p):
        return (self.state.get(p.directory) or {}).get("ver", "")

    def vpn(self, p):
        """('on', 'US') / ('off', '') / ('unknown', why) - read by this profile's own bridge, never guessed."""
        st = self.state.get(p.directory) or {}
        if not self.windows.get(p.directory) and p.directory not in self.embedded:
            return "unknown", "browser band hai"
        if not st or time.time() - st.get("vpn_t", 0) > 90:
            return "unknown", "bridge se VPN status nahi mila"
        v = st.get("vpn") or {}
        if v.get("state") == "on" and v.get("country"):
            return "on", v["country"]
        if v.get("state") == "off":
            return "off", ""
        return "unknown", v.get("why") or "verify nahi hua"

    def tab_urls(self, d):
        """URLs of the open tabs in this profile (from its bridge), Hark tabs first; the Manager's own pages left out."""
        tabs = (self.state.get(d) or {}).get("tabs") or []
        urls = [t.get("url", "") for t in tabs if (t.get("url") or "").startswith("http") and "127.0.0.1" not in t.get("url", "")]
        urls = sorted(dict.fromkeys(urls), key=lambda u: 0 if "hark.com" in u else 1)
        return urls[:20]

    def recover_hosted(self):
        import json
        try:
            with open(HOSTED_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        dirs = [d for d in data.get("dirs", []) if self.profile(d)]
        if not dirs:
            return
        # Windows destroys a browser window together with the app hosting it, so the browser's own
        # "recently closed" list does not have it: open it again with the tabs it had (saved while hosted).
        tabs = data.get("tabs") or {}
        self.refresh_windows()
        log(f"last run ended while hosting {len(dirs)} browser window(s): reopening the ones that were closed with it")
        self.embedded._save()
        for d in dirs:
            p = self.profile(d)
            if self.windows.get(d) or d in self.embedded:
                log(f"restore {d}: its window is still running, nothing reopened")
                continue        # the window survived (the Manager was not hosting it any more): no duplicate
            urls = tabs.get(d) or ["https://hark.com/chat"]
            log(f"restore {d}: {len(urls)} tab(s)")
            self.open_in(p, urls[0], new_window=bool(self.windows.get(d)), extra=urls[1:])
        QTimer.singleShot(2500, self.refresh_windows)

    def vpn_check(self, p):
        self.bridge.queue_command(self.bridge_key(p), {"type": "vpn_check"})

    def bridge_live(self, p):
        st = self.state.get(p.directory)
        return bool(st and time.time() - st["t"] < STALE)

    def refresh_windows(self):
        try:
            mwins = chrome.managed_windows([p.data_dir for p in self.profiles])
        except Exception as e:
            log(f"cent windows: {e}")
            mwins = {}
        new = {p.directory: chrome.windows_for(p, None, mwins) for p in self.profiles}
        # a window hosted inside the Manager is a child window now, so EnumWindows no longer lists it
        for d, h in list(self.embedded.items()):
            if chrome.is_window(h):
                if d in new and not any(w[0] == h for w in new[d]):
                    new[d] = [(h, chrome.window_title(h), "", False)] + new[d]
            else:
                self.embedded.pop(d, None)
        sig = lambda d: {k: [w[0] for w in v] for k, v in d.items()}
        if sig(new) != sig(self.windows):
            self.windows = new
            self.changed.emit()
        else:
            self.windows = new

    def apply_preview_interval(self):
        self.t_prev.setInterval(int(self.settings["preview_refresh"]) * 1000)
        if self.settings["show_previews"]:
            self.t_prev.start()
        else:
            self.t_prev.stop()

    def refresh_previews(self):
        if self._capturing or not self.settings["show_previews"]:
            return
        self._capturing = True
        jobs = [(d, w[0][0]) for d, w in self.windows.items() if w]

        def run():
            out = {}
            for d, h in jobs:
                img = chrome.capture(h)
                if img:
                    out[d] = img
            self._prev_result = out
            self._capturing = False

        def done():
            if self._capturing:
                QTimer.singleShot(200, done)
                return
            self.previews.update(getattr(self, "_prev_result", {}))
            self.previews_changed.emit()

        threading.Thread(target=run, daemon=True).start()
        QTimer.singleShot(300, done)

    # ---------- bridge reports ----------
    def drain_bridge(self):
        dirty = False
        while not self.bridge.inbox.empty():
            rep = self.bridge.inbox.get()
            dirty |= self.handle_report(rep)
        # profiles whose bridge went silent keep their open alerts (we can't know it was answered)
        if dirty:
            self.changed.emit()

    def handle_report(self, rep):
        email = (rep.get("email") or "").lower()
        tag = rep.get("tag") or ""
        p = self.by_tag.get(tag) or (self.by_email.get(email) if email else None)
        if not p and time.time() - getattr(self, "_reloaded", 0) > 10:
            self._reloaded = time.time()
            self.reload_profiles()
            p = self.by_tag.get(tag) or (self.by_email.get(email) if email else None)
        if not p or not rep.get("_auth"):
            if not getattr(self, "_unmapped_logged", None) or time.time() - self._unmapped_logged > 60:
                self._unmapped_logged = time.time()
                log(f"bridge report not mapped to a profile (email={email or '-'} tag={tag or '-'}, ext {rep.get('ext_id')})")
            return False
        if rep.get("verify_result") is not None:
            self._on_verify(p, rep["verify_result"])
            return False
        if rep.get("dispatch_result") is not None:
            self._on_dispatch_result(p, rep["dispatch_result"])
            return False
        if True:
            m = self._managed_rec(p.tag)
            if m and not m.get("tagged"):
                m["tagged"] = True
                self.settings.save()
                log(f"managed profile {p.tag} bridge connected")
        st = self.state.setdefault(p.directory, {"t": 0, "tabs": [], "hark": {}, "email": email})
        st["tag"] = st.get("tag", "")
        st["ver"] = rep.get("ver") or ""
        if "vpn" in rep:
            old = (st.get("vpn") or {}).get("state"), (st.get("vpn") or {}).get("country")
            st["vpn"] = rep["vpn"] or {}
            st["vpn_t"] = rep["_t"]
            if old != (st["vpn"].get("state"), st["vpn"].get("country")):
                log(f"vpn {p.directory}: {st['vpn'].get('state')} {st['vpn'].get('country', '')} ({st['vpn'].get('lvl', '')}/{st['vpn'].get('mode', '')})")
                QTimer.singleShot(0, self.changed.emit)
        if rep.get("vpn_only"):
            st["t"] = rep["_t"]
            return False
        if rep.get("limit_result") is not None:      # answer to Check Limit: not a tab/state report
            st["t"] = rep["_t"]
            st["checking"] = 0
            self.limit_result.emit(p.directory, rep["limit_result"])
            return False
        # an older bridge copy is running in this profile: ask it to reload itself (at most once a minute)
        checking = time.time() - st.get("checking", 0) < 60     # never reload the bridge in the middle of a Check Limit
        stale = rep.get("ver") != EXT_VERSION or (rep.get("build") or "") != config.extension_build()
        if any(d["tag"] == p.tag and d["status"] in ("queued", "sending") for d in self.dispatches.values()):
            stale = False         # never reload a bridge in the middle of a dispatch
        if rep.get("limit_result") is None and stale and not checking and time.time() - st.get("reload_asked", 0) > 60:
            st["reload_asked"] = time.time()
            try:   # put the current files where this profile loads the bridge from, then let it reload itself
                config.ensure_profile_extension(p.data_dir, p.tag)
            except Exception as e:
                log(f"bridge files update {p.directory}: {e}")
            self.bridge.queue_command(self.bridge_key(p), {"type": "reload_ext"})
            log(f"bridge in {p.directory} is {rep.get('ver')}/{rep.get('build') or '-'}, asked to reload to {EXT_VERSION}/{config.extension_build()}")
        if time.time() - st["t"] >= STALE:
            st["live_since"] = time.time()  # bridge (re)connected: give alerts a fresh grace window
        st["t"] = rep["_t"]
        st["tabs"] = rep.get("tabs", [])
        live_tabs = {t["id"] for t in st["tabs"]}
        st["hark"] = {k: v for k, v in st["hark"].items() if k in live_tabs}
        h = rep.get("hark")
        changed = False
        if h and h.get("projects"):
            st["projects"] = h["projects"][:40]
        if h and h.get("tabId") is not None:
            prev = st["hark"].get(h["tabId"])
            h["_t"] = rep["_t"]
            st["hark"][h["tabId"]] = h
            changed = {k: v for k, v in (prev or {}).items() if k != "_t"} != {k: v for k, v in h.items() if k != "_t"}
            kinds = lambda x: tuple(sorted({str(r).split(":")[0] for r in (x.get("workWhy") or [])}))
            sig = (h.get("working"), h.get("pending"), h.get("optional"), h.get("qid"), kinds(h))
            if not prev or sig != (prev.get("working"), prev.get("pending"), prev.get("optional"), prev.get("qid"), kinds(prev)):
                log(f"state {p.name} {conv_key(h.get('url',''))} working={h.get('working')} why={h.get('workWhy')} "
                    f"pending={h.get('pending')} optional={h.get('optional')} q={(h.get('question') or '')[:60]!r}")
            self.process_hark_state(p, h)
        # alerts follow the state of THEIR OWN conversation (matched by profile + conversation)
        tabs = list(st["hark"].values())
        running = set()
        for v in tabs:
            running.update("project:" + r for r in (v.get("running") or []))
        for a in self.alerts.open_alerts():
            if a["profile_dir"] != p.directory:
                continue
            ck = conv_key(a.get("conv_url", ""))
            why = None
            if any(a["qid"] in (v.get("answered") or []) for v in tabs):
                why = "answered in Hark"
            elif ck in running:
                why = "Hark resumed work (Working badge)"
            else:
                for v in tabs:
                    if conv_key(v.get("url", "")) != ck or not v.get("hasList"):
                        continue
                    if v.get("working"):
                        why = "Hark resumed work in this chat"
                    elif v.get("optional") and v.get("qid") == a["qid"]:
                        why = "optional suggestion, not a question"
                    elif v.get("lastId") and v.get("lastId") != a["qid"] and not (v.get("pending") and v.get("qid") == a["qid"]):
                        why = "conversation moved on"
                    if why:
                        break
            if why and self.alerts.resolve(a, why):
                changed = True
        # live check: an alert stays only while a Hark tab of this profile still shows THAT question waiting
        now = time.time()
        for v in tabs:
            if v.get("qid") and (v.get("pending") or v.get("settling")) and not v.get("working"):
                self.seen[v["qid"]] = now
        for a in self.alerts.open_alerts():
            if a["profile_dir"] != p.directory:
                continue
            last = self.seen.setdefault(a["qid"], a.get("received", now))
            if now - max(last, st.get("live_since", now)) > ALERT_GRACE:
                if self.alerts.resolve(a, "no longer waiting in Hark (live check)"):
                    changed = True
        return changed

    def process_hark_state(self, p, h):
        if h.get("working") or not h.get("pending") or not h.get("qid"):
            return
        self.seen[h["qid"]] = time.time()
        if self.alerts.reopen_if_resolved(h["qid"]):
            self.changed.emit()  # a question still waiting was marked resolved too early: reopen it, no repeat alert
            return
        a = self.alerts.add(h["qid"], p.directory, p.name, p.email, h.get("question", ""), h.get("url", ""), h.get("convTitle", "Chat"))
        if not a:
            return
        self.changed.emit()
        self.new_alert.emit(a)
        if self.settings["telegram_enabled"]:
            self.send_telegram_for(a)

    # ---------- telegram ----------
    def send_telegram_for(self, a):
        token, chat = get_bot_token(), str(self.settings["telegram_chat_id"] or "").strip()
        if not token or not chat:
            a["telegram"] = "missing token/chat id"
            self.alerts.save()
            self.need_telegram_setup.emit("Telegram alert bhejne ke liye Bot Token aur Chat ID chahiye.")
            return
        text = notify.format_alert(a["profile_name"], a["question"], a["conv_title"], a["conv_url"])

        def run():
            ok, info = notify.send_alert(token, chat, text)
            a["telegram"] = ("sent " + info) if ok else ("failed: " + info)
            self.alerts.save()
            if not ok:
                QTimer.singleShot(0, lambda: self.telegram_status.emit(False, info))
        threading.Thread(target=run, daemon=True).start()

    def check_telegram(self):
        try:
            config.load_proxy(self.settings)
        except Exception as e:
            log(f"proxy parse failed: {notify.scrub(e)}")
        token, chat = get_bot_token(), str(self.settings["telegram_chat_id"] or "").strip()
        if self.settings["telegram_enabled"] and (not token or not chat):
            self.tg_ok, self.tg_msg = False, "Telegram not set up"
            self.telegram_status.emit(False, self.tg_msg)
            self.need_telegram_setup.emit("Telegram alerts on hain, lekin Bot Token ya Chat ID save nahi hai.")
            return
        if not token:
            self.tg_ok, self.tg_msg = False, "Telegram not set up"
            self.telegram_status.emit(False, self.tg_msg)
            return

        def run():
            ok, msg = notify.check_bot(token)
            self.tg_ok, self.tg_msg = ok, ("Telegram connected" if ok else "Telegram error: " + msg)
            self._emit_tg = True
        self._emit_tg = False
        threading.Thread(target=run, daemon=True).start()

        def poll():
            if self._emit_tg:
                self.telegram_status.emit(self.tg_ok, self.tg_msg)
                if self.tg_ok and self.settings["telegram_enabled"]:
                    # questions still waiting that never reached Telegram (token was missing): send each once
                    for a in self.alerts.open_alerts():
                        if not a.get("telegram") or str(a["telegram"]).startswith("missing"):
                            a["telegram"] = "sending"
                            self.send_telegram_for(a)
            else:
                QTimer.singleShot(300, poll)
        QTimer.singleShot(300, poll)

    def send_test(self, token, chat, callback):
        def run():
            ok, info = notify.test(token, chat)
            self._test_res = (ok, info)

        self._test_res = None
        threading.Thread(target=run, daemon=True).start()

        def poll():
            if self._test_res is None:
                QTimer.singleShot(250, poll)
            else:
                callback(*self._test_res)
        QTimer.singleShot(250, poll)

    # ---------- actions ----------
    def focus_profile(self, p):
        if p.directory in self.embedded:
            self.view_request.emit(p.directory, "")
            return True
        if chrome.focus_profile(p):
            return True
        self.open_in(p)
        return False

    def launch(self, p):
        self.open_in(p, new_window=bool(self.windows.get(p.directory)))
        QTimer.singleShot(1500, self.refresh_windows)

    def view_profile(self, p, url=None):
        """Show the profile's own browser window inside the Manager (launching it first when it is closed)."""
        if not self.windows.get(p.directory) and p.directory not in self.embedded:
            self.open_in(p, url or "https://hark.com/chat")
            QTimer.singleShot(1500, self.refresh_windows)
        self.view_request.emit(p.directory, "Hark")

    def open_task(self, a):
        p = self.profile(a["profile_dir"])
        if not p:
            return
        url = a["conv_url"] or "https://hark.com/chat"
        open_ = bool(self.windows.get(p.directory)) or p.directory in self.embedded
        if open_ and self.bridge_live(p):
            # the extension finds the tab of that exact chat (or opens it) and activates it
            self.bridge.queue_command(self.bridge_key(p), {"type": "open_task", "url": url})
        else:
            self.open_in(p, url)
            QTimer.singleShot(1500, self.refresh_windows)
        self.view_request.emit(p.directory, "Hark")
        log(f"open_task {a['qid']} -> {p.directory} {url}")

    def set_managed_field(self, tag, key, value):
        recs = list(self.settings["managed_profiles"] or [])
        for m in recs:
            if m["id"] == tag:
                m[key] = value
        self.settings["managed_profiles"] = recs
        self.settings.save()

    def add_profile(self, name, launch=True):
        """A new Cent Browser profile with its own user-data-dir in <tool>\\BrowserProfiles."""
        exe = self.cent_exe()
        if not exe:
            raise RuntimeError("Settings mein Cent Browser ki chrome.exe set karein (sirf Cent Browser supported hai)")
        import re
        import uuid
        recs = list(self.settings["managed_profiles"] or [])
        n = len(recs) + 1
        name = name or f"Cent {n}"
        pid = uuid.uuid4().hex[:6]
        slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:32] or "Profile"
        root = config.browser_profiles_dir()
        data_dir = os.path.join(root, f"{slug}_{pid}")
        os.makedirs(data_dir, exist_ok=False)
        rec = {"id": pid, "name": name, "exe": exe, "data_dir": data_dir, "created": time.time()}
        recs.append(rec)
        self.settings["managed_profiles"] = recs
        self.settings.save()
        log(f"managed profile created {pid} '{name}' -> {data_dir}")
        self.reload_profiles()
        if launch:
            self.open_in(self.profile("hbm:" + pid))
            QTimer.singleShot(2500, self.refresh_windows)
        return {"kind": "managed", "tag": pid, "dir": data_dir, "browser": "Cent Browser", "name": name}

    def rename(self, p, name):
        al = dict(self.settings["aliases"] or {})
        if name:
            al[p.directory] = name
        else:
            al.pop(p.directory, None)
        self.settings["aliases"] = al
        self.settings.save()
        self.reload_profiles()
        q = self.profile(p.directory)
        if q and not self.windows.get(q.directory) and q.directory not in self.embedded:
            try:      # Cent closed: write it now; otherwise it is written at the next launch
                chrome.set_profile_name(q.data_dir, q.name)
            except Exception as e:
                log(f"profile name {q.directory}: {e}")

    def recheck_all(self, done=None, wait_ms=6000):
        """Refresh button: re-read the live Hark state of every Cent profile and sync cards + Needs reply.
        Each open profile's bridge re-sends every Hark tab's state now; after the wait, tabs that did not answer
        are dropped and open alerts whose question no tab still shows waiting are resolved."""
        t0 = time.time()
        self.refresh_windows()
        asked = []
        for p in self.profiles:
            if self.windows.get(p.directory) or p.directory in self.embedded or self.bridge_live(p):
                self.bridge.queue_command(self.bridge_key(p), {"type": "rescan"})
                asked.append(p)
        log(f"refresh: rescan asked in {len(asked)} profile(s)")

        def finish():
            self.refresh_windows()
            now = time.time()
            for p in self.profiles:
                st = self.state.get(p.directory)
                open_ = bool(self.windows.get(p.directory)) or p.directory in self.embedded
                if not st:
                    continue
                if not open_:
                    st["hark"] = {}           # browser closed: nothing can be running there
                elif st.get("t", 0) >= t0:
                    # the bridge answered: keep only the tabs that re-reported their state just now
                    st["hark"] = {k: v for k, v in st["hark"].items() if v.get("_t", 0) >= t0}
                else:
                    continue                  # no answer: keep what we know, the normal live check handles it
                waiting = {v.get("qid") for v in st["hark"].values()
                           if v.get("qid") and (v.get("pending") or v.get("settling")) and not v.get("working")}
                for a in self.alerts.open_alerts():
                    if a["profile_dir"] == p.directory and a["qid"] not in waiting:
                        self.alerts.resolve(a, "refresh: no longer waiting in Hark")
                for q in waiting:
                    self.seen[q] = now
            counts = {}
            for p in self.profiles:
                s = self.status(p)
                counts[s] = counts.get(s, 0) + 1
            summary = ", ".join(f"{k} {counts[k]}" for k in ("Running", "Needs reply", "Idle", "Closed") if counts.get(k)) or "no profiles"
            log(f"refresh done: {summary}")
            self.changed.emit()
            if done:
                done(summary)
        QTimer.singleShot(wait_ms, finish)

    def needs_reply_count(self):
        return len(self.alerts.open_alerts())

    # ---------- Shared Repo Team ----------
    def team(self):
        return repo_team.load(self.settings)

    def team_profile_ids(self):
        t = self.team()
        if not repo_team.is_configured(t):
            return "", []
        return (t.get("planner") or {}).get("profile_id", ""), [w.get("profile_id") for w in t.get("workers") or []]

    def project_urls(self, tag):
        """[(url, title)] of the Hark projects this profile's sidebar shows (from its bridge)."""
        p = self.by_tag.get(tag)
        st = self.state.get(p.directory) if p else None
        return [(f"https://hark.com/project/{x['id']}", x.get("title", "")) for x in (st or {}).get("projects") or []
                if repo_team.PROJECT_URL_RE.match(f"https://hark.com/project/{x.get('id', '')}")]

    def team_worker_url(self, p):
        t = self.team()
        if not p or not repo_team.is_configured(t):
            return ""
        return next((w.get("project_url") or "" for w in t.get("workers") or [] if w.get("profile_id") == p.tag), "")

    def is_planner(self, p):
        return bool(p) and self.team_profile_ids()[0] == p.tag

    def save_team(self, team):
        """Save the team and create/reuse one worktree per worker from ONE base commit (git, no shell)."""
        team, res = repo_team.setup_worktrees(team)
        repo_team.save(self.settings, team)
        self.worktrees_ok = res
        self.team_changed.emit()
        return team, res

    def check_worktrees(self):
        t = self.team()
        self.worktrees_ok = repo_team.verify_all(t) if repo_team.is_configured(t) else {}
        self.team_changed.emit()
        return self.worktrees_ok

    def worktrees_ready(self):
        t = self.team()
        ws = t.get("workers") or []
        return bool(ws) and all((self.worktrees_ok.get(w.get("alias")) or (False,))[0] for w in ws)

    # ---------- Safe Dispatch (bridge side) ----------
    VERIFY_TIMEOUT = 35     # s for a profile's bridge to answer a fresh tab check (it reports every <= 30 s)
    PICKUP_TTL = 45         # s a send command may wait for its bridge; after that its dispatch ID is dead
    ACK_TIMEOUT = 60        # s after pickup without a result -> Unconfirmed (never re-sent)

    def verify_tab(self, tag, url):
        """Fresh check of this profile's exact project tab. Answer comes as verify_done(vid, result)."""
        import uuid
        vid = uuid.uuid4().hex
        p = self.by_tag.get(tag)
        self.verifies[vid] = {"tag": tag, "url": url, "t": time.time()}
        if not p:
            QTimer.singleShot(0, lambda: self._verify_finish(vid, {"ok": False, "code": "no_profile", "reason": "Profile not found"}))
            return vid
        if not (self.windows.get(p.directory) or p.directory in self.embedded):
            QTimer.singleShot(0, lambda: self._verify_finish(vid, {"ok": False, "code": "closed", "reason": "Browser closed"}))
            return vid
        self.bridge.drop_command(tag, lambda c: c.get("type") == "verify_tab" and c.get("url") == url)
        self.bridge.queue_command(tag, {"type": "verify_tab", "vid": vid, "url": url})
        return vid

    def _verify_finish(self, vid, res):
        v = self.verifies.pop(vid, None)
        if v is None:
            return
        res = dict(res, tag=v["tag"], url=v["url"], vid=vid, at=time.time())
        self.verify_done.emit(vid, res)

    def _on_verify(self, p, vr):
        vid = vr.get("vid") or ""
        v = self.verifies.get(vid)
        if not v or v["tag"] != p.tag or vr.get("url") != v["url"]:
            log(f"verify result ignored (unknown id or wrong profile) from {p.tag}")
            return
        url = v["url"]
        tabs = [t for t in (vr.get("tabs") or []) if (t.get("url") or "") == url]
        if not tabs:
            return self._verify_finish(vid, {"ok": False, "code": "tab_missing", "reason": "Tab Missing"})
        act = [t for t in tabs if t.get("active")]
        if not act:
            return self._verify_finish(vid, {"ok": False, "code": "not_active", "reason": "Tab not active", "tabs": len(tabs)})
        if len(act) > 1:
            return self._verify_finish(vid, {"ok": False, "code": "ambiguous", "reason": f"{len(act)} active tabs with this URL"})
        t = act[0]
        pr = t.get("probe") or {}
        if pr.get("err"):
            why = "Tab not readable"
        elif pr.get("url") != url:
            why = "Tab URL changed"
        elif pr.get("signin"):
            why = "Not signed in"
        elif not pr.get("composer"):
            why = "Composer not found"
        elif not pr.get("ready"):
            why = "Composer not ready"
        elif not pr.get("empty"):
            why = "Composer not empty"
        elif pr.get("running"):
            why = "Agent running"
        else:
            why = ""
        if why:
            return self._verify_finish(vid, {"ok": False, "code": "not_ready", "reason": why, "tab_id": t.get("id")})
        self._verify_finish(vid, {"ok": True, "code": "verified", "reason": "Tab Verified", "tab_id": t.get("id"),
                                  "window_id": t.get("windowId")})

    @staticmethod
    def prompt_key(tag, url, text):
        import hashlib
        return f"{tag}|{url}|" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    def already_sent(self, tag, url, text):
        did = self.sent_keys.get(self.prompt_key(tag, url, text))
        return self.dispatches.get(did) if did else None

    def send_prompt(self, tag, tab_id, url, text, alias=""):
        """Queue ONE send of `text` to the verified tab. Returns the dispatch record (or None if refused).
        The same (profile, project, prompt) is never queued twice in a session; a dispatch ID expires."""
        import uuid
        key = self.prompt_key(tag, url, text)
        prev = self.sent_keys.get(key)
        if prev and self.dispatches.get(prev, {}).get("status") not in ("blocked", "expired"):
            log(f"dispatch {alias}: refused, same prompt already dispatched ({prev[:8]})")
            return None
        if not repo_team.PROJECT_URL_RE.match(url or "") or tab_id is None:
            return None
        did = uuid.uuid4().hex
        now = time.time()
        rec = {"id": did, "tag": tag, "alias": alias, "url": url, "tab_id": tab_id, "status": "queued", "reason": "",
               "t": now, "expires": now + self.PICKUP_TTL, "picked": 0, "key": key, "chars": len(text)}
        self.dispatches[did] = rec
        self.sent_keys[key] = did
        self.bridge.queue_command(tag, {"type": "send_text", "dispatch_id": did, "tab_id": tab_id, "url": url,
                                        "text": text, "expires_ms": int(rec["expires"] * 1000)})
        log(f"dispatch {did[:8]} {alias} -> {tag} tab {tab_id} {url} ({len(text)} chars) queued")
        self._dispatch_log(rec)
        self.dispatch_update.emit(did, dict(rec))
        return rec

    def _set_dispatch(self, rec, status, reason):
        rec["status"], rec["reason"], rec["done"] = status, reason, time.time()
        log(f"dispatch {rec['id'][:8]} {rec.get('alias')}: {status} {reason}")
        self._dispatch_log(rec)
        self.dispatch_update.emit(rec["id"], dict(rec))

    def _on_dispatch_result(self, p, dr):
        did = dr.get("dispatch_id") or ""
        rec = self.dispatches.get(did)
        if not rec or rec["tag"] != p.tag:
            log(f"dispatch result ignored (unknown id or wrong profile {p.tag})")
            return
        st = dr.get("status") or "unconfirmed"
        if rec["status"] in ("sent", "blocked", "expired", "duplicate"):
            return                        # final already: a late or repeated answer changes nothing
        if rec["status"] == "unconfirmed" and st != "sent":
            return                        # only a late confirmation may still turn Unconfirmed into Sent
        if st not in ("sent", "unconfirmed", "blocked", "expired", "duplicate"):
            st = "unconfirmed"
        self._set_dispatch(rec, st, dr.get("reason") or "")

    def _dispatch_tick(self):
        now = time.time()
        for vid, v in list(self.verifies.items()):
            if now - v["t"] > self.VERIFY_TIMEOUT:
                self.bridge.drop_command(v["tag"], lambda c, vid=vid: c.get("vid") == vid)
                self._verify_finish(vid, {"ok": False, "code": "no_answer", "reason": "Bridge did not answer"})
        for did, rec in list(self.dispatches.items()):
            if rec["status"] != "queued" and rec["status"] != "sending":
                continue
            picked = self.bridge.picked.get(did)
            if picked and rec["status"] == "queued":
                rec["status"], rec["picked"] = "sending", picked
                self.dispatch_update.emit(did, dict(rec))
            if not picked and now > rec["expires"]:
                n = self.bridge.drop_command(rec["tag"], lambda c, did=did: c.get("dispatch_id") == did)
                self._set_dispatch(rec, "expired", "bridge did not pick it up in time; not sent" + ("" if n else " (gone)"))
            elif picked and now - picked > self.ACK_TIMEOUT:
                self._set_dispatch(rec, "unconfirmed", "no confirmation from the page (not re-sent)")

    def _dispatch_log(self, rec):
        import json
        try:
            with open(os.path.join(config.APPDATA, "dispatch_log.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({k: v for k, v in rec.items() if k != "key"}) + "\n")
        except Exception:
            pass

    def _write_snapshot(self):
        """Live tabs per managed profile (no secrets, no page text) for troubleshooting: bridge_state.json."""
        import json
        try:
            out = {}
            for p in self.profiles:
                st = self.state.get(p.directory) or {}
                out[p.tag] = {"name": p.name, "ver": st.get("ver", ""), "age": round(time.time() - st.get("t", 0), 1),
                              "open": bool(self.windows.get(p.directory)) or p.directory in self.embedded,
                              "tabs": [{"id": t.get("id"), "active": t.get("active"), "url": t.get("url", "")[:200]}
                                       for t in st.get("tabs") or []],
                              "projects": st.get("projects") or []}
            tmp = os.path.join(config.APPDATA, "bridge_state.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"t": time.time(), "ext": EXT_VERSION, "profiles": out}, f, indent=1)
            os.replace(tmp, os.path.join(config.APPDATA, "bridge_state.json"))
        except Exception:
            pass
