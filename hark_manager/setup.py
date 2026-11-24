"""New-profile setup for a managed (own user-data-dir) profile:
1. install Browsec VPN from the official Chrome Web Store page (Cent Browser only),
2. connect Browsec to United States and confirm the public IP is in the US,
3. sign in to Hark: Auto fills the official Google sign-in form, Manual opens the form for the user.

The browser is driven over a local DevTools port opened only for this setup launch (127.0.0.1).
The password lives only in this object for the one sign-in attempt and is dropped right after it is typed.
Anything the user must approve (extension install, Google verification, Hark's age check) is left to the user
in the same Cent window; the step status says so and the worker waits for it."""
import glob
import json
import os
import socket
import threading
import time
import urllib.request

from PySide6.QtCore import QObject, Signal

from .config import log

BROWSEC_ID = "omghfjlpggmjjaagoclmmobgdodcjboh"
BROWSEC_URL = "https://chromewebstore.google.com/detail/browsec-vpn-free-vpn-for/omghfjlpggmjjaagoclmmobgdodcjboh"
POPUP = f"chrome-extension://{BROWSEC_ID}/popup/popup.html"
# several public "what is my IP" services; shared VPN exits often hit one service's rate limit
IPCHECKS = [("https://ipinfo.io/json", "country", "ip"), ("https://api.country.is/", "country", "ip"),
            ("https://ipwho.is/", "country_code", "ip"), ("https://ipapi.co/json/", "country_code", "ip")]

# deep query through open shadow roots (Browsec's popup is built from web components)
DEEP = """window.__deep=(sel)=>{const res=[];const walk=r=>{r.querySelectorAll('*').forEach(e=>{if(e.matches(sel))res.push(e);if(e.shadowRoot)walk(e.shadowRoot)})};walk(document);return res};true"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Stop(Exception):
    pass


class CDP:
    def __init__(self, port):
        self.port = port

    def _get(self, path, method="GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        return json.loads(urllib.request.urlopen(req, timeout=5).read() or b"null")

    def targets(self):
        return self._get("/json")

    def pages(self):
        return [t for t in self.targets() if t.get("type") == "page"]

    def new(self, url):
        return self._get("/json/new?" + url, "PUT")

    def close(self, tid):
        try:
            self._get(f"/json/close/{tid}")
        except Exception:
            pass

    def activate(self, tid):
        try:
            self._get(f"/json/activate/{tid}")
        except Exception:
            pass

    def session(self, target):
        return Session(target["webSocketDebuggerUrl"])


class Session:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=20)
        self.n = 0

    def call(self, method, params=None):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.n:
                return r.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("value")

    def click_at(self, x, y):
        for t in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})

    def type_text(self, text):
        self.call("Input.insertText", {"text": text})

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def parse_dob(text):
    """'1999-05-12' -> (1999, 5, 12); anything else -> None."""
    import datetime
    try:
        d = datetime.date.fromisoformat((text or "").strip())
        return (d.year, d.month, d.day) if 1900 <= d.year <= datetime.date.today().year - 13 else None
    except Exception:
        return None


BIRTHDAY_JS = """(()=>{const v=%s;const ss=[...document.querySelectorAll('select')];if(ss.length<3)return 'wait';
const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;
for(const s of ss){const k=(s.getAttribute('aria-label')||s.name||s.id||'');
 const key=/month/i.test(k)?'m':/day/i.test(k)?'d':/year/i.test(k)?'y':null;if(!key)return 'nolabel';
 if(![...s.options].some(o=>o.value===v[key]))return 'noopt';
 set.call(s,v[key]);s.dispatchEvent(new Event('input',{bubbles:true}));s.dispatchEvent(new Event('change',{bubbles:true}));}
const b=[...document.querySelectorAll('button')].find(x=>/continue/i.test(x.innerText)&&!x.disabled);
if(!b)return 'nobtn';b.click();return 'ok'})()"""


class ProfileSetup(QObject):
    step = Signal(int, str, str)      # step number (1..3), state (wait|run|ok|user|fail|skip), text
    finished = Signal(bool, str)
    field = Signal(str, str, object)  # (tag, key, value) saved on the GUI thread

    def __init__(self, ctl, profile, mode, email="", password="", dob=""):
        super().__init__()
        self.ctl, self.p, self.mode = ctl, profile, mode
        self.email = email.strip()
        self.dob = parse_dob(dob)    # (year, month, day) or None
        self._pw = password
        self.stopped = False
        self.cent = "cent" in (profile.browser_name or "").lower()

    # ---------- plumbing ----------
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stopped = True
        self._pw = None

    def _say(self, n, state, text):
        log(f"setup {self.p.tag} step{n} {state}: {text}")
        self.step.emit(n, state, text)

    def _sleep(self, s):
        end = time.time() + s
        while time.time() < end:
            if self.stopped:
                raise Stop()
            time.sleep(0.25)

    def _focus(self):
        try:
            from . import chrome
            wins = chrome.managed_windows([self.p.data_dir]).get(chrome.norm_dir(self.p.data_dir), [])
            if wins:
                chrome.focus_hwnd(wins[-1][0])
        except Exception as e:
            log(f"setup focus: {e}")

    def _page(self, url_part, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            for t in self.cdp.pages():
                if url_part in t.get("url", ""):
                    return t
            self._sleep(0.5)
        return None

    def _run(self):
        ok, msg = False, ""
        try:
            self.port = free_port()
            self.cdp = CDP(self.port)
            first = BROWSEC_URL if self.cent else "https://hark.com/"
            self.ctl.open_in(self.p, first, extra=[f"--remote-debugging-port={self.port}", "--remote-allow-origins=*"])
            end = time.time() + 40
            while True:
                try:
                    self.cdp.pages()
                    break
                except Exception:
                    if time.time() > end:
                        raise RuntimeError("Browser DevTools port nahi khula")
                    self._sleep(0.7)
            if self.cent:
                self.install_browsec()
                self.connect_vpn()
            else:
                self._say(1, "skip", "Sirf Cent Browser ke liye")
                self._say(2, "skip", "Sirf Cent Browser ke liye")
            self.sign_in()
            ok, msg = True, "Setup mukammal"
        except Stop:
            msg = "Setup rok diya gaya"
        except Exception as e:
            msg = f"Setup ruk gaya: {e}"
            log(f"setup {self.p.tag} error: {e}")
        finally:
            self._pw = None
        self.finished.emit(ok, msg)

    # ---------- step 1: Browsec ----------
    def _browsec_installed(self):
        if glob.glob(os.path.join(self.p.data_dir, "Default", "Extensions", BROWSEC_ID, "*", "manifest.json")):
            return True
        try:
            return any(BROWSEC_ID in t.get("url", "") and t.get("type") in ("service_worker", "background_page")
                       for t in self.cdp.targets())
        except Exception:
            return False

    def install_browsec(self):
        self._say(1, "run", "Chrome Web Store ka official page khul raha hai…")
        if self._browsec_installed():
            self._say(1, "ok", "Browsec pehle se installed hai")
            return
        t = self._page("chromewebstore.google.com", 40)
        if not t:
            t = self.cdp.new(BROWSEC_URL)
        s = self.cdp.session(t)
        try:
            clicked = False
            for _ in range(40):
                r = s.js("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>/Add to (Chrome|browser)/i.test(x.innerText));if(!b)return 'wait';if(b.disabled)return 'disabled';b.click();return 'clicked'})()""")
                if r == "clicked":
                    clicked = True
                    break
                if self._browsec_installed():
                    break
                self._sleep(1)
        finally:
            s.close()
        if clicked:
            self._say(1, "run", "\"Add to Chrome\" dabaya, extension approval…")
        approved, asked, t0 = False, False, time.time()
        end = t0 + 300
        self._sleep(2)
        while time.time() < end:
            if self._browsec_installed():
                break
            if clicked and not approved:
                approved = self._approve_dialog()
            if not asked and time.time() - t0 > 15:
                asked = True
                self._say(1, "user", "Cent window mein \"Add extension\" approve karein")
                self._focus()
            self._sleep(1.5)
        if not self._browsec_installed():
            self._say(1, "fail", "Browsec install nahi hua (5 min)")
            raise RuntimeError("Browsec install nahi hua")
        self._sleep(3)
        self._say(1, "ok", "Browsec VPN installed (Chrome Web Store)")

    def _approve_dialog(self):
        """Press 'Add extension' on the browser's own confirm dialog, in this profile's window only."""
        try:
            import pythoncom
            pythoncom.CoInitialize()
            from pywinauto import Desktop
            from . import chrome
            hw = {h for h, *_ in chrome.managed_windows([self.p.data_dir]).get(chrome.norm_dir(self.p.data_dir), [])}
            for h in hw:
                w = Desktop(backend="uia").window(handle=h)
                for e in w.descendants(control_type="Button"):
                    if (e.element_info.name or "").strip().lower() == "add extension":
                        e.invoke()
                        log(f"setup {self.p.tag}: extension approval pressed")
                        return True
        except Exception as e:
            log(f"setup approve: {e}")
        return False

    # ---------- step 2: VPN ----------
    def _ip(self, s):
        for url, ck, ik in IPCHECKS:
            try:
                s.call("Page.navigate", {"url": url})
                self._sleep(2.5)
                r = s.js("document.body ? document.body.innerText : ''") or ""
                j = json.loads(r)
                c = (j.get(ck) or "").upper()
                if len(c) == 2:
                    return c, j.get(ik, "")
                log(f"setup {self.p.tag}: ip check {url} gave no country")
            except Stop:
                raise
            except Exception:
                continue
        return "", ""

    def connect_vpn(self):
        self._say(2, "run", "Browsec mein United States chun raha hoon…")
        t = self.cdp.new(POPUP)
        s = self.cdp.session(t)
        try:
            self._sleep(2.5)
            s.js(DEEP)
            self._accept_terms(s)
            for attempt in range(3):
                s.js(DEEP)
                st = s.js("""(()=>{const a=__deep('.Active_Country_Name')[0];const on=__deep('.Title').some(e=>/protected/i.test(e.textContent));return (on?'on':'off')+'|'+(a?a.textContent.trim():'')})()""") or "off|"
                if st == "on|United States":
                    break
                s.js("""(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));const c=__deep('.ChangeButton')[0];if(c){c.click();await w(1800)}
                        const us=__deep('.Country').find(e=>e.textContent.trim()==='United States');if(us){us.click();await w(2200)}
                        const b=__deep('button').find(x=>/Start VPN/i.test(x.textContent));if(b){b.click();await w(3000)}return 1})()""")
                self._sleep(2)
        finally:
            s.close()
            self.cdp.close(t["id"])
        # confirm from a normal page: the public IP must be in the US
        self._say(2, "run", "VPN connection confirm kar raha hoon…")
        t = self.cdp.new("about:blank")
        s = self.cdp.session(t)
        try:
            end, asked = time.time() + 240, False
            while time.time() < end:
                c, ip = self._ip(s)
                if c == "US":
                    masked = ip.rsplit(".", 1)[0] + ".x" if "." in ip else ip
                    self._say(2, "ok", f"Connected: United States (IP {masked})")
                    self.field.emit(self.p.tag, "vpn", "US")
                    return
                if time.time() > end - 225 and not asked:
                    asked = True
                    self._say(2, "user", f"VPN US nahi dikha ({c or 'no IP'}). Browsec icon se United States connect karein")
                    self._focus()
                self._sleep(4)
        finally:
            s.close()
            self.cdp.close(t["id"])
        self._say(2, "fail", "United States connection confirm nahi hua")
        raise RuntimeError("VPN US confirm nahi hua; sign-in shuru nahi kiya")

    def _accept_terms(self, s):
        """Browsec's first-run terms screen: press its Accept button (found by text across shadow DOM)."""
        try:
            s.call("DOM.getDocument", {"depth": -1, "pierce": True})
            r = s.call("DOM.performSearch", {"query": "Accept", "includeUserAgentShadowDOM": True})
            n = r.get("resultCount", 0)
            if not n:
                return
            ids = s.call("DOM.getSearchResults", {"searchId": r["searchId"], "fromIndex": 0, "toIndex": n}).get("nodeIds", [])
            for nid in ids:
                try:
                    q = s.call("DOM.getBoxModel", {"nodeId": nid}).get("model", {}).get("content")
                    if not q:
                        d = s.call("DOM.describeNode", {"nodeId": nid}).get("node", {})
                        pid = d.get("parentId")
                        if not pid:
                            continue
                        q = s.call("DOM.getBoxModel", {"nodeId": pid}).get("model", {}).get("content")
                    if q:
                        x, y = (q[0] + q[4]) / 2, (q[1] + q[5]) / 2
                        s.click_at(x, y)
                        log(f"setup {self.p.tag}: browsec terms accepted")
                        self._sleep(2)
                        return
                except Exception:
                    continue
        except Exception as e:
            log(f"setup terms: {e}")

    # ---------- step 3: Hark sign-in ----------
    @staticmethod
    def _signed_in(url):
        return url.startswith("https://hark.com/") and any(k in url for k in ("/chat", "/projects", "/home", "/c/"))

    def sign_in(self):
        self._say(3, "run", "Hark sign-in page khul raha hai…")
        for x in self.cdp.pages():
            u = x.get("url", "")
            if "staticstream.org" in u or "installed_compare" in u or "browsec.com" in u:
                self.cdp.close(x["id"])
        t = self._page("hark.com", 3) or self.cdp.new("https://hark.com/")
        self.cdp.activate(t["id"])
        s = self.cdp.session(t)
        try:
            if "hark.com" not in t.get("url", "") or "robots" in t.get("url", ""):
                s.call("Page.navigate", {"url": "https://hark.com/"})
            self._sleep(5)
            url = s.js("location.href") or ""
            if self._signed_in(url):
                self._done(s)
                return
            if self.mode == "manual":
                self._say(3, "user", "Sign-in form khul gaya. Cent window mein khud login karein")
                self._focus()
            else:
                self._auto_google(s)
        finally:
            s.close()
        self._wait_signed_in()

    def _auto_google(self, s):
        if not self.email or not self._pw:
            raise RuntimeError("Auto sign-in ke liye email/password nahi")
        js = """(()=>{try{const f=[...document.querySelectorAll('iframe')].find(f=>/framed-sign-in/.test(f.src)&&f.getBoundingClientRect().top<1500);
                 const d=f?f.contentDocument:document;if(!d||!d.body)return 'wait';
                 const b=[...d.querySelectorAll('button,a,[role=button]')].find(x=>/Continue with Google/i.test(x.innerText||''));
                 if(!b)return 'wait';b.scrollIntoView({block:'center'});const o=f?f.getBoundingClientRect():{left:0,top:0};
                 const q=b.getBoundingClientRect();return JSON.stringify([o.left+q.left+q.width/2,o.top+q.top+q.height/2])}catch(e){return 'wait'}})()"""
        clicked = False
        end = time.time() + 60
        while time.time() < end and not clicked:
            r = s.js(js)
            if not r or r == "wait":
                self._sleep(1)
                continue
            self._sleep(0.8)
            r = s.js(js)
            if not r or r == "wait":
                continue
            x, y = json.loads(r)
            s.call("Page.bringToFront")
            s.click_at(x, y)        # in-browser trusted click (no real cursor)
            for _ in range(10):
                self._sleep(1)
                if self._page("accounts.google.com", 0.1) or "accounts.google.com" in (s.js("location.href") or ""):
                    clicked = True
                    break
        if not clicked:
            raise RuntimeError("Hark par 'Continue with Google' nahi mila")
        self._say(3, "run", "Google ka official sign-in form, email…")
        g = self._page("accounts.google.com", 25)
        if not g:
            u = s.js("location.href") or ""
            if "accounts.google.com" in u:
                g = self._page("accounts.google.com", 5)
        if not g:
            raise RuntimeError("Google sign-in form nahi khula")
        gs = self.cdp.session(g)
        try:
            if not self._fill(gs, "input#identifierId, input[type=email]", self.email):
                raise RuntimeError("Google email field nahi mila")
            gs.js("""(()=>{const b=document.querySelector('#identifierNext button, #identifierNext');if(b)b.click()})()""")
            self._say(3, "run", "Google form, password…")
            ok = False
            for _ in range(40):
                if gs.js("""!!document.querySelector('input[name=Passwd]') && document.querySelector('input[name=Passwd]').getBoundingClientRect().width>0"""):
                    self._sleep(1)
                    ok = self._fill(gs, "input[name=Passwd]", self._pw)
                    break
                u = gs.js("location.href") or ""
                if "/challenge/" in u and "/pwd" not in u:
                    break
                self._sleep(0.7)
            self._pw = None          # used once; drop it
            if ok:
                gs.js("""(()=>{const b=document.querySelector('#passwordNext button, #passwordNext');if(b)b.click()})()""")
                self._say(3, "run", "Google ne password le liya, Hark par wapas…")
        finally:
            self._pw = None
            gs.close()

    def _fill(self, s, sel, text):
        for _ in range(30):
            if s.js(f"""(()=>{{const e=document.querySelector({json.dumps(sel)});if(!e||e.getBoundingClientRect().width==0)return false;e.focus();e.value='';return true}})()"""):
                s.type_text(text)
                return True
            self._sleep(0.7)
        return False

    def _wait_signed_in(self):
        end = time.time() + 1800
        last = ""
        while time.time() < end:
            pages = self.cdp.pages()
            urls = [t.get("url", "") for t in pages]
            done = [t for t in pages if self._signed_in(t.get("url", ""))]
            if done:
                s = self.cdp.session(done[0])
                try:
                    self._done(s)
                finally:
                    s.close()
                return
            note = ""
            for t in pages:
                u = t.get("url", "")
                if "accounts.google.com" in u:
                    if self.mode == "auto" and ("consent" in u or "oauth" in u or "signin/oauth" in u):
                        s = self.cdp.session(t)
                        try:
                            r = s.js("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^(Continue|Allow)$/i.test(x.innerText.trim()));if(b){b.click();return 1}return 0})()""")
                        finally:
                            s.close()
                        if r:
                            note = "Google consent: Continue"
                    if "/challenge/" in u or "/signin/rejected" in u or "speedbump" in u:
                        seen = self.__dict__.setdefault("_chal", {})
                        seen[u] = seen.get(u, 0) + 1
                        if seen[u] >= 3:   # only if it stays (~6s), not a passing redirect
                            note = "user:Google verification: Cent window mein complete karein"
                    elif self.mode == "manual" and not note:
                        note = "user:Cent window mein Google sign-in complete karein"
                elif "hark.com/verify-age" in u:
                    note = self._birthday(t)
            if last.startswith("user:") and not note.startswith("user:") and not any(
                    "/challenge/" in u for u in urls):
                note = note or "Verification ho gayi, setup resume…"
            if note and note != last:
                last = note
                if note.startswith("user:"):
                    self._say(3, "user", note[5:])
                    self._focus()
                else:
                    self._say(3, "run", note)
            self._sleep(2)
        self._say(3, "fail", "30 min mein sign-in mukammal nahi hua")
        raise RuntimeError("sign-in timeout")

    def _birthday(self, t):
        """Hark's age check: fill the batch birthday and press Continue (auto mode only)."""
        if self.mode != "auto" or not self.dob:
            return "user:Hark birthday maang raha hai: Cent window mein bhar dein"
        tries = self.__dict__.setdefault("_bd_tries", [0])
        if tries[0] >= 3:
            return "user:Birthday auto-fill nahi chala: Cent window mein bhar dein"
        y, m, d = self.dob
        s = self.cdp.session(t)
        try:
            r = s.js(BIRTHDAY_JS % json.dumps({"y": str(y), "m": str(m), "d": str(d)}))
        finally:
            s.close()
        if r == "wait":
            return ""
        tries[0] += 1
        log(f"setup {self.p.tag}: birthday fill -> {r}")
        return "Hark birthday (age check) auto-fill, Continue…" if r == "ok" else ""

    def _done(self, s):
        em = ""
        try:
            em = s.js("""(async()=>{for(const u of ['/api/auth/get-session','/api/auth/session','/api/me']){try{const r=await fetch(u,{credentials:'include'});if(r.ok){const t=await r.text();const m=t.match(/[\\w.+-]+@[\\w-]+\\.[\\w.]+/);if(m)return m[0]}}catch(e){}}return ''})()""") or ""
        except Exception:
            pass
        em = em or self.email
        if em:
            self.field.emit(self.p.tag, "email", em)
        self._say(3, "ok", f"Hark signed in{(': ' + em) if em else ''}")
