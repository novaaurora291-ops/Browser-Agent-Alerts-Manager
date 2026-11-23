"""Controller: profiles + live state from the bridge, alert lifecycle, notifications, previews."""
import threading
import time
EXT_VERSION = "1.0.2"

from PySide6.QtCore import QObject, QTimer, Signal

from . import chrome, notify, config
from .alerts import AlertStore
from .bridge import Bridge
from .config import Settings, get_bot_token, log

STALE = 20  # seconds without a report = bridge not live for that profile


class Controller(QObject):
    changed = Signal()                 # profiles/status/alerts changed
    previews_changed = Signal()
    new_alert = Signal(dict)           # fresh alert -> desktop notification
    telegram_status = Signal(bool, str)
    need_telegram_setup = Signal(str)

    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.alerts = AlertStore()
        self.bridge = Bridge()
        self.bridge_ok = self.bridge.start()
        self.profiles = []
        self.by_email = {}
        self.state = {}       # profile_dir -> {"t":..., "tabs":[...], "hark":{tabId: state}}
        self.windows = {}     # profile_dir -> [(hwnd,title,aumid,minimized)]
        self.previews = {}    # profile_dir -> png bytes
        self.tg_ok = False
        self.tg_msg = "Telegram not set up"
        self._capturing = False
        self.reload_profiles()

        self.t_bridge = QTimer(self, timeout=self.drain_bridge, interval=400)
        self.t_bridge.start()
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
        self.profiles = ps
        self.by_email = {p.email.lower(): p for p in ps if p.email}
        self.changed.emit()

    def profile(self, directory):
        for p in self.profiles:
            if p.directory == directory:
                return p
        return None

    def status(self, p):
        if any(a["profile_dir"] == p.directory for a in self.alerts.open_alerts()):
            return "Needs reply"
        if not self.windows.get(p.directory):
            return "Closed"
        st = self.state.get(p.directory)
        if st and time.time() - st["t"] < STALE and any(h.get("working") for h in st["hark"].values()):
            return "Working"
        return "Idle"

    def bridge_live(self, p):
        st = self.state.get(p.directory)
        return bool(st and time.time() - st["t"] < STALE)

    def refresh_windows(self):
        try:
            wins = chrome.chrome_windows()
        except Exception as e:
            log(f"windows: {e}")
            return
        new = {p.directory: chrome.windows_for(p, wins) for p in self.profiles}
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
        p = self.by_email.get(email)
        if not p:
            self.reload_profiles()
            p = self.by_email.get(email)
        if not p:
            return False
        st = self.state.setdefault(p.directory, {"t": 0, "tabs": [], "hark": {}, "email": email})
        # an older bridge copy is running in this profile: ask it to reload itself (at most once a minute)
        if rep.get("ver") != EXT_VERSION and time.time() - st.get("reload_asked", 0) > 60:
            st["reload_asked"] = time.time()
            self.bridge.queue_command(email, {"type": "reload_ext"})
            log(f"bridge in {p.directory} is {rep.get('ver')}, asked to reload to {EXT_VERSION}")
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
            self.process_hark_state(p, h)
        # resolve alerts whose conversation tab now shows no open question / a different question
        for a in self.alerts.open_alerts():
            if a["profile_dir"] != p.directory:
                continue
            # Resolved only when the page shows a user reply after this exact question
            if any(a["qid"] in (v.get("answered") or []) for v in st["hark"].values()):
                if self.alerts.resolve(a, "answered in Hark"):
                    changed = True
        return changed

    def process_hark_state(self, p, h):
        if not h.get("pending") or not h.get("qid"):
            return
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
        if chrome.focus_profile(p):
            return True
        chrome.launch(p.directory)
        return False

    def launch(self, p):
        chrome.launch(p.directory, new_window=bool(self.windows.get(p.directory)))
        QTimer.singleShot(1500, self.refresh_windows)

    def open_task(self, a):
        p = self.profile(a["profile_dir"])
        if not p:
            return
        url = a["conv_url"] or "https://hark.com/chat"
        if self.bridge_live(p):
            self.bridge.queue_command(p.email.lower(), {"type": "open_task", "url": url})
            # the extension activates the tab on its next report; bring the window up as well
            for delay in (600, 2500, 5000):
                QTimer.singleShot(delay, lambda p=p: chrome.focus_profile(p, prefer_title="Hark"))
        else:
            chrome.launch(p.directory, url)
            QTimer.singleShot(2500, lambda p=p: chrome.focus_profile(p, prefer_title="Hark"))
        log(f"open_task {a['qid']} -> {p.directory} {url}")

    def add_profile(self, name):
        d = chrome.next_profile_dir()
        if name:
            al = dict(self.settings["aliases"] or {})
            al[d] = name
            self.settings["aliases"] = al
            self.settings.save()
        chrome.launch(d, "https://hark.com/")
        for delay in (4000, 9000):
            QTimer.singleShot(delay, self.reload_profiles)
        return d

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
