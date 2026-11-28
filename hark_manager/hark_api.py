"""Hark chat routes used by Team Dispatch, from the browser-observed contract in HARK_API_ANALYSIS.txt:

    GET  /api/auth/get-session                -> {"user": {"id", "email"}}
    POST /api/messages/send?cid=<project id>  -> {"messageId", "conversationId", "agentId", "steered", "redirected"}
         payload: timezone, conversationId, message, idempotencyKey, responseMessageId, expectedUserId
    GET  /api/conversations/main?timezone=..  -> snapshot; the reply is the message whose id is responseMessageId

These are private Hark routes, not a documented API. Until Hark grants approved access only a loopback host (the local
mock) may be called: any other host raises LiveAccessBlocked unless live_approved=True. Cookie / token values are never
logged or put into an error message."""
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

LOOPBACK = ("127.0.0.1", "localhost", "::1")
ACTIVE_JOB = ("running", "streaming", "pending", "queued")


class HarkApiError(RuntimeError):
    """status: HTTP status (0 = no HTTP answer). maybe_delivered: False only when Hark certainly did not take the
    request (it refused it with a 4xx, or the connection was refused); anything else may have been delivered."""

    def __init__(self, msg, status=0, code="", maybe_delivered=True):
        super().__init__(msg)
        self.status, self.code, self.maybe_delivered = status, code, maybe_delivered


class LiveAccessBlocked(HarkApiError):
    pass


def is_loopback(url):
    return (urllib.parse.urlsplit(url or "").hostname or "") in LOOPBACK


class HarkApi:
    """One Cent profile's Hark session. `headers` is called for every request (like a browser reading its cookie
    jar), so an account switch inside that profile is seen by the very next request."""

    def __init__(self, base_url, headers, live_approved=False, timeout=15.0, timezone="UTC"):
        if not is_loopback(base_url) and not live_approved:
            raise LiveAccessBlocked("Live Hark API: approved access nahi mili (koi live call nahi)", code="live_blocked",
                                    maybe_delivered=False)
        self.base = base_url.rstrip("/")
        self._headers = headers
        self.timeout = timeout
        self.timezone = timezone
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, method, path, query=None, payload=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        h = {"Accept": "application/json", "X-Hark-Locale": "en"}
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(self._headers() or {})
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                status, body = r.status, r.read()
        except urllib.error.HTTPError as e:
            try:
                j = json.loads(e.read() or b"{}")
            except ValueError:
                j = {}
            code = str((j.get("error") if isinstance(j, dict) else "") or "")
            raise HarkApiError(f"HTTP {e.code}" + (f" {code}" if code else ""), status=e.code, code=code,
                               maybe_delivered=e.code >= 500)
        except urllib.error.URLError as e:
            if isinstance(e.reason, ConnectionRefusedError):
                raise HarkApiError("connection refused", code="refused", maybe_delivered=False)
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise HarkApiError("timeout", code="timeout")
            raise HarkApiError(f"network error: {e.reason}", code="network")
        except (socket.timeout, TimeoutError):
            raise HarkApiError("timeout", code="timeout")
        except (ConnectionError, OSError) as e:
            raise HarkApiError(f"network error: {e.__class__.__name__}", code="network")
        try:
            return json.loads(body or b"null")
        except ValueError:
            raise HarkApiError(f"HTTP {status}: answer is not JSON", status=status, code="bad_json")

    def session(self):
        """{"id", "email"} of the account this profile is signed in to. Raises (status 401) when signed out."""
        j = self._request("GET", "/api/auth/get-session")
        user = j.get("user") if isinstance(j, dict) else None
        if not isinstance(user, dict) or not user.get("id"):
            raise HarkApiError("not signed in", status=401, code="signed_out", maybe_delivered=False)
        return {"id": str(user["id"]), "email": str(user.get("email") or "").strip().lower()}

    def send(self, project_id, text, expected_user_id, idempotency_key, response_message_id):
        payload = {"timezone": self.timezone, "conversationId": project_id, "message": text,
                   "idempotencyKey": idempotency_key, "responseMessageId": response_message_id,
                   "expectedUserId": expected_user_id}
        ack = self._request("POST", "/api/messages/send", {"cid": project_id}, payload)
        if not isinstance(ack, dict):
            raise HarkApiError("acknowledgement is not a JSON object", status=200, code="bad_ack")
        return ack

    def conversations(self):
        return self._request("GET", "/api/conversations/main", {"timezone": self.timezone})


# ---------------------------------------------------------------- snapshot reading (fail closed)
def _objects(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _objects(v)
    elif isinstance(value, list):
        for v in value:
            yield from _objects(v)


def project_scope(snapshot, project_id, user_id):
    """The selected project's conversation, only when it belongs to user_id. A same-id object owned by another
    account, or one without an owner, is never used."""
    if not project_id or not user_id:
        return None
    for v in _objects(snapshot):
        if project_id not in (v.get("conversationId"), v.get("projectId"), v.get("id")):
            continue
        if isinstance(v.get("messages"), list) and str(v.get("userId") or v.get("ownerId") or "") == user_id:
            return v
    return None


def scope_message(scope, message_id, role):
    """A direct message of that conversation (nested objects are not searched) with this id and role."""
    for m in (scope or {}).get("messages") or []:
        if isinstance(m, dict) and message_id and (m.get("id") or m.get("messageId")) == message_id \
                and m.get("role") == role:
            return m
    return None


def message_text(m):
    c = (m or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(x["text"] for x in c if isinstance(x, dict) and isinstance(x.get("text"), str))
    t = (m or {}).get("text")
    return t if isinstance(t, str) else ""


def is_streaming(m):
    return bool(m.get("isStreaming") or str(m.get("jobStatus") or "").lower() in ACTIVE_JOB)


def canon(text):
    return re.sub(r"\s+", " ", text or "").strip()
