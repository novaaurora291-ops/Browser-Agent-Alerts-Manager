"""Telegram alerts over a small built-in HTTPS client.

* Optional proxy (HTTP/HTTPS CONNECT or SOCKS5, with username/password), applied ONLY to Telegram calls.
* The TLS ClientHello is split into two TLS records, which gets past networks that reset
  connections to api.telegram.org by inspecting the first packet.
* Bot token and proxy credentials never appear in logs or error text.
"""
import base64
import json
import re
import socket
import ssl
import time
from urllib.parse import urlparse, unquote

from .config import log

HOST = "api.telegram.org"
PROXY = None  # dict(type, host, port, user, pw) or None; set by the controller
_SECRETS = set()


# ---------------- masking ----------------
def scrub(text, token=None):
    s = str(text)
    for sec in list(_SECRETS) + ([token] if token else []):
        if sec and len(sec) >= 3:
            s = s.replace(sec, "***")
    s = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot***", s)
    s = re.sub(r"\d{6,}:[A-Za-z0-9_-]{20,}", "***", s)
    s = re.sub(r"(://)[^:/@\s]+:[^@\s]+@", r"\1***:***@", s)
    return s


def mask_proxy(px):
    if not px:
        return "none"
    return f"{px['type']}://{'***:***@' if px.get('user') else ''}{px['host']}:{px['port']}"


# ---------------- proxy parsing ----------------
def parse_proxy(text, ptype="auto"):
    """Accepts IP:PORT, IP:PORT:USER:PASS, USER:PASS@IP:PORT and URLs like socks5://user:pass@host:port."""
    t = (text or "").strip()
    if not t:
        return None
    if "://" in t:
        u = urlparse(t)
        sch = u.scheme.lower()
        kind = "socks5" if sch.startswith("socks") else "http"
        px = dict(type=kind, host=u.hostname, port=u.port, user=unquote(u.username or ""), pw=unquote(u.password or ""))
    elif "@" in t:
        cred, hp = t.rsplit("@", 1)
        user, _, pw = cred.partition(":")
        host, _, port = hp.rpartition(":")
        px = dict(type=None, host=host, port=int(port), user=user, pw=pw)
    else:
        parts = t.split(":")
        if len(parts) == 2:
            px = dict(type=None, host=parts[0], port=int(parts[1]), user="", pw="")
        elif len(parts) >= 4:
            px = dict(type=None, host=parts[0], port=int(parts[1]), user=parts[2], pw=":".join(parts[3:]))
        else:
            raise ValueError("Proxy format samajh nahi aaya. IP:PORT:USERNAME:PASSWORD ya socks5://user:pass@host:port dein")
    if not px["host"] or not px["port"]:
        raise ValueError("Proxy mein host aur port dono chahiye")
    want = (ptype or "auto").lower()
    if want in ("http", "https", "http/https"):
        px["type"] = "http"
    elif want.startswith("socks"):
        px["type"] = "socks5"
    elif not px["type"]:
        px["type"] = "auto"
    for k in ("user", "pw"):
        if px.get(k):
            _SECRETS.add(px[k])
    return px


def set_proxy(px):
    global PROXY
    PROXY = px
    if px:
        for k in ("user", "pw"):
            if px.get(k):
                _SECRETS.add(px[k])


# ---------------- transport ----------------
def _recv_exact(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("proxy ne connection band kar diya")
        b += c
    return b


def _http_connect(s, px):
    req = f"CONNECT {HOST}:443 HTTP/1.1\r\nHost: {HOST}:443\r\n"
    if px.get("user"):
        req += "Proxy-Authorization: Basic " + base64.b64encode(f"{px['user']}:{px['pw']}".encode()).decode() + "\r\n"
    s.sendall((req + "\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        c = s.recv(4096)
        if not c:
            raise ConnectionError("proxy ne CONNECT ka jawab nahi diya")
        resp += c
        if len(resp) > 16384:
            break
    line = resp.split(b"\r\n")[0].decode("latin-1", "replace")
    code = line.split(" ")[1] if " " in line else "?"
    if code == "407":
        raise PermissionError("proxy ne username/password reject kar diya (407)")
    if code != "200":
        raise ConnectionError(f"proxy CONNECT fail: {line}")


def _socks5_connect(s, px):
    methods = b"\x00\x02" if px.get("user") else b"\x00"
    s.sendall(b"\x05" + bytes([len(methods)]) + methods)
    ver, m = _recv_exact(s, 2)
    if ver != 5:
        raise ConnectionError("ye SOCKS5 proxy nahi lagta (shayad HTTP hai)")
    if m == 2:
        u, p = px["user"].encode(), px["pw"].encode()
        s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        if _recv_exact(s, 2)[1] != 0:
            raise PermissionError("SOCKS5 proxy ne username/password reject kar diya")
    elif m != 0:
        raise PermissionError("SOCKS5 proxy ka auth method support nahi")
    h = HOST.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + (443).to_bytes(2, "big"))
    rep = _recv_exact(s, 4)
    if rep[1] != 0:
        raise ConnectionError(f"SOCKS5 connect fail (code {rep[1]})")
    atyp = rep[3]
    _recv_exact(s, 4 if atyp == 1 else 16 if atyp == 4 else _recv_exact(s, 1)[0])
    _recv_exact(s, 2)


def _open_tcp(px, timeout):
    if not px:
        s = socket.create_connection((HOST, 443), timeout=timeout)
    else:
        s = socket.create_connection((px["host"], int(px["port"])), timeout=timeout)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.settimeout(timeout)
    if px:
        kind = px["type"]
        if kind == "auto":
            kind = detect_type(px, timeout)
        (_socks5_connect if kind == "socks5" else _http_connect)(s, px)
    return s


def detect_type(px, timeout=10):
    """Asks the proxy a SOCKS5 greeting; an HTTP proxy answers with text instead."""
    s = socket.create_connection((px["host"], int(px["port"])), timeout=timeout)
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x02\x00\x02")
        r = s.recv(8)
        return "socks5" if r[:1] == b"\x05" else "http"
    except socket.timeout:
        return "http"
    finally:
        s.close()


def _tls_request(px, raw, timeout=20):
    s = _open_tcp(px, timeout)
    try:
        ctx = ssl.create_default_context()
        inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
        o = ctx.wrap_bio(inc, out, server_hostname=HOST)
        first = True
        deadline = time.time() + timeout
        while True:
            try:
                o.do_handshake()
                break
            except ssl.SSLWantReadError:
                pass
            data = out.read()
            if data:
                if first and len(data) > 30 and data[0] == 0x16:
                    # split the ClientHello record in two records
                    hdr, body, k = data[:3], data[5:], 16
                    s.sendall(hdr + k.to_bytes(2, "big") + body[:k])
                    time.sleep(0.05)
                    s.sendall(hdr + (len(body) - k).to_bytes(2, "big") + body[k:])
                else:
                    s.sendall(data)
                first = False
            if time.time() > deadline:
                raise socket.timeout("TLS handshake timeout")
            c = s.recv(65536)
            if not c:
                raise ConnectionResetError("TLS handshake ke dauran connection band ho gaya")
            inc.write(c)
        o.write(raw)
        s.sendall(out.read())
        buf = b""
        while True:
            try:
                d = o.read(65536)
                if not d:
                    break
                buf += d
                continue
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLZeroReturnError:
                break
            if _complete(buf):
                break
            c = s.recv(65536)
            if not c:
                break
            inc.write(c)
        return buf
    finally:
        s.close()


def _complete(buf):
    if b"\r\n\r\n" not in buf:
        return False
    head, body = buf.split(b"\r\n\r\n", 1)
    m = re.search(rb"content-length:\s*(\d+)", head, re.I)
    if m:
        return len(body) >= int(m.group(1))
    return body.endswith(b"0\r\n\r\n")


def _parse_http(buf):
    head, _, body = buf.partition(b"\r\n\r\n")
    status = head.split(b"\r\n")[0].decode("latin-1", "replace")
    if re.search(rb"transfer-encoding:\s*chunked", head, re.I):
        out, rest = b"", body
        while rest:
            ln, _, rest = rest.partition(b"\r\n")
            n = int(ln.split(b";")[0] or b"0", 16)
            if n == 0:
                break
            out, rest = out + rest[:n], rest[n + 2:]
        body = out
    return status, body


def telegram_call(token, method, payload=None, timeout=20, tries=2):
    body = json.dumps(payload or {}).encode()
    raw = (f"POST /bot{token}/{method} HTTP/1.1\r\nHost: {HOST}\r\nContent-Type: application/json\r\n"
           f"Content-Length: {len(body)}\r\nConnection: close\r\nUser-Agent: HarkBrowserManager\r\n\r\n").encode() + body
    last = None
    for _ in range(tries):
        try:
            status, b = _parse_http(_tls_request(PROXY, raw, timeout))
            try:
                return json.loads(b.decode("utf-8"))
            except Exception:
                return {"ok": False, "description": status or "khali jawab"}
        except Exception as e:
            last = e
    raise last


def _friendly(e, token):
    s = scrub(repr(e), token)
    via = f" (proxy {mask_proxy(PROXY)})" if PROXY else ""
    if isinstance(e, PermissionError):
        return scrub(str(e), token) + via
    if isinstance(e, socket.timeout) or "timed out" in s.lower() or "timeout" in s.lower():
        return ("api.telegram.org tak connection timeout" + via +
                (". Proxy chal rahi hai ya nahi check karein" if PROXY else ". Is network par Telegram blocked hai: Settings mein proxy dein"))
    if isinstance(e, socket.gaierror):
        return "DNS nahi mila" + via
    if isinstance(e, ssl.SSLError):
        return "SSL error" + via + ": " + scrub(str(e), token)[:120]
    if isinstance(e, (ConnectionError, OSError)):
        return "connection error" + via + ": " + scrub(str(e), token)[:160]
    return s[:200]


def check_bot(token):
    if not token:
        return False, "Bot token missing"
    try:
        j = telegram_call(token, "getMe")
        if j.get("ok"):
            return True, "@" + j["result"].get("username", "")
        return False, scrub(j.get("description", "error"), token)
    except Exception as e:
        msg = _friendly(e, token)
        log(f"telegram getMe failed: {msg}")
        return False, msg


def send_alert(token, chat_id, text):
    if not token or not chat_id:
        return False, "Bot token or Chat ID missing"
    try:
        j = telegram_call(token, "sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
        if j.get("ok"):
            return True, str(j["result"]["message_id"])
        d = scrub(j.get("description", "error"), token)
        log(f"telegram error: {d}")
        return False, d
    except Exception as e:
        msg = _friendly(e, token)
        log(f"telegram send failed: {msg}")
        return False, msg


def test(token, chat_id):
    ok, who = check_bot(token)
    if not ok:
        return False, "getMe: " + who
    ok, info = send_alert(token, chat_id, f"🔔 Hark Browser Manager — test alert ({who})\nSawal: Kya ye test alert mil gaya?\n\nJawab Hark mein dein.")
    return ok, (f"getMe OK {who}, test message {info}" if ok else "sendMessage: " + info)


def format_alert(profile_name, question, conv_title, conv_url):
    return (f"🔔 Hark alert — jawab chahiye\n"
            f"Profile: {profile_name}\n"
            f"Project: {conv_title}\n"
            f"Sawal: {question}\n\n"
            f"Jawab Hark mein dein: {conv_url}")
