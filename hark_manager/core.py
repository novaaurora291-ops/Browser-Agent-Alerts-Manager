"""Controller: profiles + live state from the bridge, alert lifecycle, notifications, previews."""
import threading
import os
import time
EXT_VERSION = "1.0.14"
ALERT_GRACE = 15  # an alert Hark no longer shows as waiting (live DOM) is cleared after this many seconds

from PySide6.QtCore import QObject, QTimer, Signal

from . import chrome, notify, config
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

    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.alerts = AlertStore()
        self.bridge = Bridge()
        self.bridge_ok = self.bridge.start()
        self.profiles = []
        self.by_email = {}
        self.by_tag = {}
        self.state = {}       # profile_dir -> {"t":..., "tabs":[...], "hark":{tabId: state}}
        self.windows = {}     # profile_dir -> [(hwnd,title,aumid,minimized)]
        self.embedded = Hosted(self)  # profile_dir -> hwnd of its browser window currently hosted inside the Manager

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

    # ---------- profiles ----------
    def reload_profiles(self):
        ps = chrome.load_profiles()
        aliases = self.settings["aliases"] or {}
        for p in ps:
            p.alias = aliases.get(p.directory, "")
        managed = []
        for m in self.settings["managed_profiles"] or []:
            p = chrome.Profile("hbm:" + m["id"], m.get("name") or m["id"])
            p.kind, p.tag, p.data_dir, p.exe = "managed", m["id"], m["data_dir"], m.get("exe", "")
            p.email = chrome.managed_email(p.data_dir) or m.get("email", "")
            p.alias = aliases.get(p.directory, "")
            managed.append(p)
        self.profiles = ps + managed
        # bridge reports: Chrome profiles are matched by their Google account, managed ones by their own tag
        self.by_email = {p.email.lower(): p for p in ps if p.email}
        self.by_tag = {p.tag: p for p in managed}
        for p in ps:   # a Chrome profile is paired with its bridge by a tag too (needed when it has no Google account)
            self.by_tag[chrome.pair_tag(p)] = p
        self.changed.emit()

    def bridge_key(self, p):
        if p.managed:
            return p.tag
        st = self.state.get(p.directory) or {}
        return st.get("tag") or (p.email or "").lower()

    def browser_exe(self):
        exe = (self.settings["browser_exe"] or "").strip().strip('"')
        return exe if exe and os.path.isfile(exe) else ""

    def exe_for(self, p):
        """A managed profile keeps the browser it was made with; if that exe is gone, the current Settings browser."""
        if p.exe and os.path.isfile(p.exe):
            return p.exe
        return self.browser_exe() or chrome.chrome_exe()

    def open_in(self, p, url=None, new_window=False, extra=None):
        if p.managed:
            m = self._managed_rec(p.tag) or {}
            if not m.get("tagged") and not url:
                url = "https://hark.com/"
            chrome.launch_managed(p, self.exe_for(p), url, new_window, config.ensure_profile_extension(p.data_dir, p.tag), extra)
        else:
            chrome.launch(p.directory, url, new_window)

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
            return "Working"
        if any(a["profile_dir"] == p.directory for a in self.alerts.open_alerts()):
            return "Needs reply"
        if not self.windows.get(p.directory):
            return "Closed"
        return "Idle"

    def pair_bridge(self, p):
        """Tell this profile's bridge which profile it is: open the Manager's local tag page in that profile;
        the bridge stores the tag, reports, and closes the tab itself."""
        url = f"http://127.0.0.1:{config.BRIDGE_PORT}/hbm-tag?p={chrome.pair_tag(p) if not p.managed else p.tag}&close=1"
        log(f"pairing bridge in {p.directory}")
        self.state.setdefault(p.directory, {"t": 0, "tabs": [], "hark": {}, "email": ""})["checking"] = time.time()
        if p.managed:
            self.open_in(p, url)
        else:
            chrome.launch(p.directory, url)

    def reload_bridge(self, p):
        try:
            config.ensure_profile_extension(p.data_dir, p.tag) if p.managed else config.ensure_extension()
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
        log(f"last run ended while hosting {len(dirs)} browser window(s): reopening them with their tabs")
        self.embedded._save()
        self.refresh_windows()
        for d in dirs:
            p = self.profile(d)
            urls = tabs.get(d) or ["https://hark.com/chat"]
            log(f"restore {d}: {len(urls)} tab(s)")
            if p.managed:
                self.open_in(p, urls[0], new_window=bool(self.windows.get(d)), extra=urls[1:])
            else:
                chrome.launch(p.directory, urls[0], new_window=bool(self.windows.get(d)), more_urls=urls[1:])
        QTimer.singleShot(2500, self.refresh_windows)

    def vpn_check(self, p):
        self.bridge.queue_command(self.bridge_key(p), {"type": "vpn_check"})

    def bridge_live(self, p):
        st = self.state.get(p.directory)
        return bool(st and time.time() - st["t"] < STALE)

    def refresh_windows(self):
        try:
            wins = chrome.chrome_windows()
        except Exception as e:
            log(f"windows: {e}")
            return
        try:
            mwins = chrome.managed_windows([p.data_dir for p in self.profiles if p.managed])
        except Exception as e:
            log(f"managed windows: {e}")
            mwins = {}
        new = {p.directory: chrome.windows_for(p, wins, mwins) for p in self.profiles}
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
        if not p:
            if not getattr(self, "_unmapped_logged", None) or time.time() - self._unmapped_logged > 60:
                self._unmapped_logged = time.time()
                log(f"bridge report not mapped to a profile (email={email or '-'} tag={tag or '-'}, ext {rep.get('ext_id')})")
            return False
        if p.managed:
            m = self._managed_rec(p.tag)
            if m and not m.get("tagged"):
                m["tagged"] = True
                self.settings.save()
                log(f"managed profile {p.tag} bridge connected")
        st = self.state.setdefault(p.directory, {"t": 0, "tabs": [], "hark": {}, "email": email})
        st["tag"] = tag if not p.managed else st.get("tag", "")
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
        if rep.get("limit_result") is None and rep.get("ver") != EXT_VERSION and not checking and time.time() - st.get("reload_asked", 0) > 60:
            st["reload_asked"] = time.time()
            try:   # put the current files where this profile loads the bridge from, then let it reload itself
                config.ensure_profile_extension(p.data_dir, p.tag) if p.managed else config.ensure_extension()
            except Exception as e:
                log(f"bridge files update {p.directory}: {e}")
            self.bridge.queue_command(self.bridge_key(p), {"type": "reload_ext"})
            log(f"bridge in {p.directory} is {rep.get('ver')}, asked to reload to {EXT_VERSION}")
        if time.time() - st["t"] >= STALE:
            st["live_since"] = time.time()  # bridge (re)connected: give alerts a fresh grace window
        st["t"] = rep["_t"]
        st["tabs"] = rep.get("tabs", [])
        live_tabs = {t["id"] for t in st["tabs"]}
        st["hark"] = {k: v for k, v in st["hark"].items() if k in live_tabs}
        h = rep.get("hark")
        changed = False
        if h and h.get("tabId") is not None:
            prev = st["hark"].get(h["tabId"])
            st["hark"][h["tabId"]] = h
            changed = prev != h
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
        """With a browser set in Settings: a new profile with its own user-data-dir in <tool>\\BrowserProfiles.
        Without one: the old way, a new profile inside Google Chrome."""
        exe = self.browser_exe()
        if not exe:
            d = chrome.next_profile_dir()
            if name:
                al = dict(self.settings["aliases"] or {})
                al[d] = name
                self.settings["aliases"] = al
                self.settings.save()
            chrome.launch(d, "https://hark.com/")
            for delay in (4000, 9000):
                QTimer.singleShot(delay, self.reload_profiles)
            return {"kind": "chrome", "dir": d, "browser": "Google Chrome"}
        import re
        import uuid
        recs = list(self.settings["managed_profiles"] or [])
        n = len(recs) + 1
        name = name or f"{chrome.browser_name(exe)} {n}"
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
        return {"kind": "managed", "tag": pid, "dir": data_dir, "browser": chrome.browser_name(exe), "name": name}

    def rename(self, p, name):
        al = dict(self.settings["aliases"] or {})
        if name:
            al[p.directory] = name
        else:
            al.pop(p.directory, None)
        self.settings["aliases"] = al
        self.settings.save()
        self.reload_profiles()

    def needs_reply_count(self):
        return len(self.alerts.open_alerts())
