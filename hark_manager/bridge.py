"""Local HTTP endpoint (127.0.0.1 only) that the Chrome bridge extension reports to."""
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import BRIDGE_PORT, log


class Bridge:
    def __init__(self):
        self.inbox = queue.Queue()          # reports for the UI thread
        self.commands = {}                  # email -> [cmd]
        self.lock = threading.Lock()
        self.server = None
        self.error = None

    def queue_command(self, email, cmd):
        with self.lock:
            self.commands.setdefault(email or "", []).append(cmd)

    def _take(self, email):
        with self.lock:
            return self.commands.pop(email or "", [])

    def start(self):
        bridge = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, obj, code=200):
                d = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(d)))
                self.end_headers()
                self.wfile.write(d)

            def do_GET(self):
                if self.path.startswith("/hbm-tag?"):
                    # first page of a new managed profile (or a pairing tab): the bridge reads the tag from this URL
                    if "close=1" in self.path:   # pairing: the bridge closes this tab itself
                        d = (b"<!doctype html><meta charset=utf-8><title>Hark Browser Manager</title>"
                             b"<body style='font:16px Segoe UI;padding:40px'>Hark Browser Manager: bridge pairing...</body>")
                    else:
                        d = (b"<!doctype html><meta charset=utf-8><meta http-equiv=refresh content='2;url=https://hark.com/'>"
                             b"<title>Hark Browser Manager</title><body style='font:18px Segoe UI;padding:40px'>"
                             b"Hark Browser Manager: profile ready. Opening Hark...</body>")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(d)))
                    self.end_headers()
                    self.wfile.write(d)
                    return
                if self.path.startswith("/ping"):
                    return self._reply({"app": "HarkBrowserManager", "ok": True})
                self._reply({"error": "not found"}, 404)

            def do_POST(self):
                # only accept calls from Chrome extensions (or local tools without an Origin)
                origin = self.headers.get("Origin", "")
                if origin and not origin.startswith("chrome-extension://"):
                    return self._reply({"error": "forbidden"}, 403)
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(n) or b"{}")
                except Exception:
                    return self._reply({"error": "bad json"}, 400)
                if self.path == "/report":
                    body["_t"] = time.time()
                    bridge.inbox.put(body)
                    cmds = bridge._take(body["tag"]) if body.get("tag") else bridge._take((body.get("email") or "").lower()) + bridge._take(body.get("email", ""))
                    return self._reply({"commands": cmds})
                self._reply({"error": "not found"}, 404)

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), H)
        except OSError as e:
            self.error = str(e)
            log(f"bridge bind failed: {e}")
            return False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        log(f"bridge listening on {BRIDGE_PORT}")
        return True

    def stop(self):
        if self.server:
            self.server.shutdown()
