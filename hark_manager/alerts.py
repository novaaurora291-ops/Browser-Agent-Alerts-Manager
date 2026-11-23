"""Alert store: one alert per Hark question (keyed by the Hark message id), persisted across restarts."""
import json
import os
import time
import uuid

from .config import ALERTS_PATH, log


class AlertStore:
    def __init__(self, path=ALERTS_PATH):
        self.path = path
        self.alerts = []
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                self.alerts = json.load(f)
        except Exception:
            self.alerts = []

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.alerts[-500:], f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    def find(self, qid):
        for a in self.alerts:
            if a["qid"] == qid:
                return a
        return None

    def open_alerts(self):
        return [a for a in self.alerts if a["status"] == "needs_reply"]

    def add(self, qid, profile_dir, profile_name, email, question, conv_url, conv_title):
        """Returns the new alert, or None when this exact question was already alerted (no duplicates)."""
        if self.find(qid):
            return None
        a = {
            "id": uuid.uuid4().hex[:12], "qid": qid, "profile_dir": profile_dir, "profile_name": profile_name,
            "email": email, "question": question, "conv_url": conv_url, "conv_title": conv_title,
            "received": time.time(), "status": "needs_reply", "resolved_at": None,
            "telegram": None, "desktop": False,
        }
        self.alerts.append(a)
        self.save()
        log(f"alert added {qid} {profile_name}: {question[:80]}")
        return a

    def reopen_if_resolved(self, qid):
        a = self.find(qid)
        if a and a["status"] == "resolved":
            a["status"] = "needs_reply"
            a["resolved_at"] = None
            self.save()
            log(f"alert reopened {qid} (still waiting in Hark)")
            return True
        return False

    def resolve(self, a, why=""):
        if a["status"] != "resolved":
            a["status"] = "resolved"
            a["resolved_at"] = time.time()
            a["resolved_why"] = why
            self.save()
            log(f"alert resolved {a['qid']} ({why})")
            return True
        return False
