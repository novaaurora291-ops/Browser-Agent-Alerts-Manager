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
                if self.path.startswith("/cmd?") and not self.headers.get("Origin"):
                    from urllib.parse import parse_qs, urlparse
                    q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                    bridge.queue_command(q.pop("email", ""), q)
                    return self._reply({"queued": q})
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
                    return self._reply({"commands": bridge._take(body.get("email", ""))})
                if self.path == "/dump":
                    try:
                        from .config import APPDATA as APP_DIR
                        import os
                        with open(os.path.join(APP_DIR, "dump.jsonl"), "a", encoding="utf-8") as f:
                            f.write(json.dumps(body, ensure_ascii=False)[:400000] + "\n")
                    except Exception as e:
                        log(f"dump write: {e}")
                    return self._reply({})
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
