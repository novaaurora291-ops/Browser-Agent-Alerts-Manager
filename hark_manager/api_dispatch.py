"""Team Dispatch over the Hark chat routes (hark_api.py): used with the local mock, and with live Hark only after
Hark grants approved access. One worker's status flow:

    verifying -> sending -> sent -> reply
        |           |         |-> account_changed   (the profile switched account: the reply is not read)
        |           |         '-> no_reply          (no completed reply under our reply id in time)
        |           '-> unconfirmed                  (may have been sent, delivery not seen: never re-sent blindly)
        '-> blocked                                  (certainly NOT sent: wrong account, project missing, refused)

"sent" only when the expected account's conversation of the selected project shows our user message (the
acknowledged messageId with the exact text). "reply" only when that account's selected project shows the completed
assistant message under the responseMessageId this dispatch chose. One (target, profile, project, prompt) is
dispatched once: a "blocked" or "unconfirmed" one may be tried again, everything else is refused. Once any attempt
may have been delivered (maybe_delivered, sticky across attempts), every later attempt resumes with that attempt's
idempotency key and reply id (Hark stores one message per key), so a blocked retry in between never makes a new copy."""
import hashlib
import threading
import time
import uuid

from PySide6.QtCore import QObject, Signal

from . import hark_api, repo_team
from .config import log

RETRYABLE = ("blocked",)            # certainly NOT sent by this attempt: may be tried again
RESUMABLE = ("unconfirmed",)        # maybe sent: may be tried again, always with the same idempotency key
IN_FLIGHT = ("verifying", "sending")
FINAL = ("reply", "blocked", "unconfirmed", "account_changed", "no_reply")


def dispatch_key(target, tag, url, text):
    return f"{target}|{tag}|{url}|" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class ApiDispatcher(QObject):
    update = Signal(str, dict)      # dispatch key, record copy (emitted from worker threads: queued to the UI)

    def __init__(self, transport, poll=0.4, confirm_timeout=8.0, reply_timeout=120.0):
        super().__init__()
        self.transport = transport  # (target, profile tag) -> hark_api.HarkApi for that profile's session
        self.poll, self.confirm_timeout, self.reply_timeout = poll, confirm_timeout, reply_timeout
        self.lock = threading.RLock()
        self.records = {}           # key -> record
        self.events = []            # (key, status) in order, for the log / tests
        self._stop = threading.Event()

    # ---------- public ----------
    def record(self, key):
        with self.lock:
            r = self.records.get(key)
            return dict(r) if r else None

    def find(self, target, tag, url, text):
        return self.record(dispatch_key(target, tag, url, text))

    def dispatch(self, target, tag, account, url, text, alias=""):
        """Start ONE dispatch. Returns (record, "") or (None, why refused)."""
        account = (account or "").strip().lower()
        pid = repo_team.project_id(url)
        if not pid:
            return None, "Project URL exact https://hark.com/project/<id> nahi"
        if not account:
            return None, "Worker account set nahi"
        if not (text or "").strip():
            return None, "Prompt khali hai"
        key = dispatch_key(target, tag, url, text)
        with self.lock:
            prev = self.records.get(key)
            if prev and not (prev["final"] and prev["status"] in RETRYABLE + RESUMABLE):
                log(f"api dispatch {alias}: refused, same prompt already {prev['status']}")
                return None, f"Duplicate: yeh prompt pehle se {prev['status']}"
            busy = next((r for r in self.records.values() if not r["final"] and r["target"] == target
                         and r["tag"] == tag and r["project_id"] == pid), None)
            if busy:
                return None, f"Is project mein ek dispatch abhi chal raha hai ({busy['status']})"
            resume = bool(prev and prev.get("maybe_delivered"))
            rec = {"key": key, "target": target, "tag": tag, "alias": alias, "account": account, "url": url,
                   "project_id": pid, "text": text, "status": "verifying", "reason": "", "final": False,
                   "attempt": (prev or {}).get("attempt", 0) + 1, "resumed": resume, "maybe_delivered": resume,
                   "idempotency_key": prev["idempotency_key"] if resume else uuid.uuid4().hex,
                   "reply_id": prev["reply_id"] if resume else "rsp_" + uuid.uuid4().hex,
                   "message_id": "", "user_id": "", "reply": "", "t": time.time(),
                   "history": [("verifying", time.time())]}
            self.records[key] = rec
            self.events.append((key, "verifying"))
        log(f"api dispatch {alias} -> {target} {tag} project {pid[:8]} attempt {rec['attempt']}"
            + (" (resume: may have been delivered, same idempotency key)" if resume else ""))
        self.update.emit(key, self.record(key))
        threading.Thread(target=self._run, args=(key,), daemon=True, name="api-dispatch").start()
        return self.record(key), ""

    def shutdown(self):
        self._stop.set()

    # ---------- worker thread ----------
    def _set(self, key, status, reason="", final=False, **fields):
        with self.lock:
            r = self.records[key]
            if r["status"] != status:
                r["history"].append((status, time.time()))
                self.events.append((key, status))
            r.update(fields, status=status, reason=reason, final=final)
            alias = r["alias"]
        log(f"api dispatch {alias}: {status}" + (f" ({reason})" if reason else ""))
        self.update.emit(key, self.record(key))

    def _run(self, key):
        try:
            self._flow(key)
        except Exception as e:      # never leave a record in flight
            log(f"api dispatch error: {e.__class__.__name__}: {e}")
            st = (self.record(key) or {}).get("status")
            self._set(key, {"verifying": "blocked", "sending": "unconfirmed"}.get(st, "no_reply"),
                      f"error: {e.__class__.__name__}", final=True, **({"maybe_delivered": True} if st == "sending" else {}))

    def _flow(self, key):
        r = self.record(key)
        pid, text, want = r["project_id"], r["text"], r["account"]
        # 1. verify: the profile's signed-in account is the worker's account and owns the selected project
        try:
            api = self.transport(r["target"], r["tag"])
            s = api.session()
            if s["email"] != want:
                return self._set(key, "blocked", f"Wrong account: profile mein {s['email'] or '?'} (chahiye {want})",
                                 final=True)
            uid = s["id"]
            if not hark_api.project_scope(api.conversations(), pid, uid):
                return self._set(key, "blocked", f"Project {pid[:8]} is account ({want}) mein nahi", final=True)
        except hark_api.LiveAccessBlocked as e:
            return self._set(key, "blocked", str(e), final=True)
        except hark_api.HarkApiError as e:
            return self._set(key, "blocked", "Verify: " + ("signed out" if e.status == 401 else str(e)), final=True)
        # 2. send once; Hark itself refuses it (409) if the session's account changed since the check
        self._set(key, "sending", user_id=uid)
        ack, ack_err = None, ""
        try:
            ack = api.send(pid, text, uid, r["idempotency_key"], r["reply_id"])
        except hark_api.HarkApiError as e:
            if not e.maybe_delivered:
                why = "account badal gaya (Hark ne send refuse kiya)" if e.status == 409 else f"Hark ne refuse kiya: {e}"
                return self._set(key, "blocked", why, final=True)
            ack_err = str(e)
        with self.lock:
            self.records[key]["maybe_delivered"] = True     # from now on only this idempotency key may be sent
        if ack is not None:
            bad = ("ack: doosri conversation" if ack.get("conversationId") != pid else
                   "ack: messageId nahi" if not ack.get("messageId") else
                   "ack: redirected" if ack.get("redirected") else "")
            if bad:
                return self._set(key, "unconfirmed", bad, final=True)
        # 3. delivery: our user message must show in this account's selected project
        mid = self._confirm(api, pid, uid, text, ack, r["reply_id"])
        if isinstance(mid, tuple):
            return self._set(key, "unconfirmed", mid[0] + (f"; send: {ack_err}" if ack_err else ""), final=True)
        self._set(key, "sent", "reply ka intezar", message_id=mid, sent_at=time.time())
        # 4. reply: the completed assistant message under OUR reply id, same account, same project
        end = time.time() + self.reply_timeout
        while not self._stop.is_set():
            try:
                s = api.session()
                if s["id"] != uid:
                    return self._set(key, "account_changed", f"profile ab {s['email'] or '?'} par hai: reply nahi parha",
                                     final=True)
                m = hark_api.scope_message(hark_api.project_scope(api.conversations(), pid, uid), r["reply_id"],
                                           "assistant")
                if m and not hark_api.is_streaming(m) and hark_api.message_text(m).strip():
                    return self._set(key, "reply", "", final=True, reply=hark_api.message_text(m), reply_at=time.time())
            except hark_api.HarkApiError as e:
                if e.status == 401:
                    return self._set(key, "account_changed", "profile signed out: reply nahi parha", final=True)
            if time.time() > end:
                return self._set(key, "no_reply", f"{self.reply_timeout:.0f}s mein reply nahi aaya", final=True)
            self._stop.wait(self.poll)
        self._set(key, "no_reply", "app band ho raha hai", final=True)

    def _confirm(self, api, pid, uid, text, ack, reply_id):
        """messageId of our delivered user message, or (reason,) when delivery could not be confirmed."""
        end = time.time() + self.confirm_timeout
        why = "delivery confirm nahi hui (message conversation mein nahi dikha)"
        while not self._stop.is_set():
            try:
                s = api.session()
                if s["id"] != uid:
                    return (f"confirm se pehle account badal gaya ({s['email'] or '?'})",)
                scope = hark_api.project_scope(api.conversations(), pid, uid)
                if ack:
                    m = hark_api.scope_message(scope, ack["messageId"], "user")
                else:
                    # no acknowledgement: our reply id is known, the user message right before it must be ours
                    msgs = (scope or {}).get("messages") or []
                    i = next((n for n, x in enumerate(msgs) if isinstance(x, dict) and x.get("id") == reply_id
                              and x.get("role") == "assistant"), -1)
                    m = msgs[i - 1] if i > 0 and isinstance(msgs[i - 1], dict) and msgs[i - 1].get("role") == "user" else None
                if m and hark_api.canon(hark_api.message_text(m)) == hark_api.canon(text):
                    return m.get("id") or m.get("messageId") or ""
                if m:
                    why = "message ka text match nahi"
            except hark_api.HarkApiError as e:
                if e.status == 401:
                    return ("confirm se pehle profile signed out",)
            if time.time() > end:
                return (why,)
            self._stop.wait(self.poll)
        return ("app band ho raha hai",)
