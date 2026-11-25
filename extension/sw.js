// Hark Browser Manager Bridge - service worker (Cent Browser, managed profiles only)
const APP = "http://127.0.0.1:47823";
let email = null;
async function getEmail() {
  if (email !== null) return email;
  try {
    const info = await chrome.identity.getProfileUserInfo({ accountStatus: "ANY" });
    email = info.email || "";
  } catch (e) { email = ""; }
  return email;
}
// a profile the Manager made with its own user-data-dir is identified by a tag (its managed profile ID) and
// authenticated by its own secret token; both come from tag.json in this profile's copy of the extension
let tag = null, token = null;
// THIS script's own build. The Manager stamps it into the profile's copy of sw.js (config.ensure_profile_extension).
// It is never read from tag.json: Chrome can keep running an older cached service worker under the same version,
// and such a script used to report the build from the current tag.json, so it looked current while its VPN check
// was the old one (VPN off shown although Browsec was on). Now a stale script reports its own old build and is
// told to reload.
const CODE_BUILD = "";
async function readTagFile() {
  try { const r = await fetch(chrome.runtime.getURL("tag.json"), { cache: "no-store" }); if (r.ok) return await r.json(); } catch (e) {}
  return null;
}
async function getTag() {
  if (tag !== null) return tag;
  const j = await readTagFile();
  if (j && j.tag) { tag = j.tag + ""; token = (j.token || "") + ""; return tag; }
  try { const r = await chrome.storage.local.get("hbm_tag"); tag = r.hbm_tag || ""; } catch (e) { tag = ""; }
  token = "";
  return tag;
}
async function getToken() { await getTag(); return token || ""; }
chrome.tabs.onUpdated.addListener((id, info, t) => {
  const u = info.url || (t && t.url) || "";
  const m = u.match(/^http:\/\/127\.0\.0\.1:47823\/hbm-tag\?p=([A-Za-z0-9_-]+)/);
  if (!m) return;
  // a per-profile tag.json (managed profiles) always wins over a pairing tag
  getTag().then(cur => {
    if (m[1] !== cur) {
      fetch(chrome.runtime.getURL("tag.json")).then(r => r.ok ? r.json() : null).catch(() => null).then(j => {
        if (j && j.tag) return;
        tag = m[1]; chrome.storage.local.set({ hbm_tag: tag });
      }).finally(() => report().catch(() => {}));
    } else report().catch(() => {});
    if (/[?&]close=1/.test(u)) setTimeout(() => chrome.tabs.remove(id).catch(() => {}), 300);
  });
});
async function post(path, body) {
  const t = await getTag(), k = await getToken();
  const r = await fetch(APP + path, { method: "POST", headers: { "Content-Type": "application/json", "X-HBM-Tag": t, "X-HBM-Auth": k }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401) {
    // refused: the Manager may have issued a new token (tag.json rewritten). Read it again next time.
    tag = null; token = null;
    return { commands: (j.commands || []).filter(c => c.type === "reload_ext") };
  }
  return j;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
// every report may hand back queued commands for this profile: run them, never drop them
async function postReport(body) {
  const res = await post("/report", body);
  if (res && res.commands && res.commands.length) runCommands(res.commands).catch(() => {});
  return res;
}
// READ-ONLY look at a Hark tab: limit popup, reset time, chat loaded, message box active. Clicks/typing nothing.
function hbmLimitProbe() {
  const shown = el => { for (let a = el, i = 0; a && i < 8; a = a.parentElement, i++) { const cs = getComputedStyle(a); if (cs.display === "none" || cs.visibility === "hidden" || parseFloat(cs.opacity) === 0) return false; } const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const LIM = /reached your (daily |weekly |monthly |usage )*limit/i;
  const hits = Array.from(document.querySelectorAll("body *")).filter(e => { const t = e.textContent || ""; return t.length < 500 && LIM.test(t) && !Array.from(e.children).some(k => LIM.test(k.textContent || "")) && shown(e); });
  let reached = hits.length > 0, reset = "";
  if (reached) {
    let c = hits[0];
    for (let i = 0; i < 10 && c && !/\breset/i.test(c.innerText || ""); i++) c = c.parentElement;
    const t = c ? (c.innerText || "") : "";
    for (const m of t.matchAll(/resets?\s*(?:at|on|in)?[\s:]*([^\n]{2,40})/gi)) { if (/\d/.test(m[1])) { reset = m[1].trim(); break; } }
  }
  const ed = Array.from(document.querySelectorAll("textarea, [contenteditable=true], [role=textbox]")).find(e => shown(e) && e.getBoundingClientRect().width > 80);
  const active = !!ed && !ed.disabled && !ed.readOnly && ed.getAttribute("aria-disabled") !== "true" && !ed.closest("[inert],[aria-disabled=true],fieldset[disabled]");
  const p = location.pathname;
  const signin = /sign-?in|login|verify-age/i.test(p) || !!document.querySelector("iframe[src*=framed-sign-in]");
  const loaded = !signin && document.readyState === "complete" && (p.startsWith("/chat") || p.startsWith("/project")) && !!(document.querySelector("[data-testid=message-list]") || ed);
  return { reached, reset, composer: !!ed, active, loaded, signin, url: location.href.split("#")[0] };
}
async function checkLimit(c) {
  let tabs = (await chrome.tabs.query({ url: "https://hark.com/*" })).filter(t => !/\/(framed-)?sign-in/.test(t.url || ""));
  let opened = null;
  if (!tabs.length) {   // no Hark tab: look in a background tab, then close it again
    opened = await chrome.tabs.create({ url: "https://hark.com/chat", active: false });
    for (let i = 0; i < 40; i++) { await sleep(500); const t = await chrome.tabs.get(opened.id); if (t.status === "complete") break; }
    await sleep(5000);
    tabs = [await chrome.tabs.get(opened.id)];
  }
  const out = [];
  for (const t of tabs) {
    try { const [r] = await chrome.scripting.executeScript({ target: { tabId: t.id }, func: hbmLimitProbe }); out.push(r.result); }
    catch (e) { out.push({ err: String(e), url: t.url }); }
  }
  if (opened) { try { await chrome.tabs.remove(opened.id); } catch (e) {} }
  const body = { email: await getEmail(), tag: await getTag(), ext_id: chrome.runtime.id, ver: chrome.runtime.getManifest().version,
                 tabs: [], windows: [], hark: null, limit_result: { id: c.id, tabs: out, opened: !!opened } };
  await postReport(body);
}
// ---- Safe Dispatch ----
// READ-ONLY look at one Hark tab's composer and run state (in the page). Types/clicks nothing.
function hbmComposerProbe() {
  const shown = el => { for (let a = el, i = 0; a && i < 8; a = a.parentElement, i++) { const cs = getComputedStyle(a); if (cs.display === "none" || cs.visibility === "hidden") return false; } const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const ed = document.querySelector("textarea[data-testid=chat-input]") || Array.from(document.querySelectorAll("textarea, [contenteditable=true], [role=textbox]")).find(e => shown(e) && e.getBoundingClientRect().width > 80);
  const val = ed ? (ed.tagName === "TEXTAREA" ? ed.value : (ed.innerText || "")) : "";
  const disabled = !ed || ed.disabled || ed.readOnly || ed.getAttribute("aria-disabled") === "true" || !!ed.closest("[inert],[aria-disabled=true],fieldset[disabled]");
  const stop = Array.from(document.querySelectorAll("button[aria-label]")).some(b => /^stop( generating| run| task)?$/i.test(b.getAttribute("aria-label")) && shown(b));
  const p = location.pathname;
  const signin = /sign-?in|login|verify-age/i.test(p) || !!document.querySelector("iframe[src*=framed-sign-in]");
  const list = !!document.querySelector("[data-testid=message-list]");
  return { url: location.href.split("#")[0], composer: !!ed && shown(ed), ready: !!ed && !disabled && document.readyState === "complete",
           empty: !val.trim(), running: stop, signin, list, visible: document.visibilityState };
}
async function baseBody() {
  return { email: await getEmail(), tag: await getTag(), ext_id: chrome.runtime.id, ver: chrome.runtime.getManifest().version, tabs: [], windows: [], hark: null };
}
async function verifyTab(c) {
  // every tab of this profile whose URL is EXACTLY the project URL (no prefix match, no fallback tab)
  const all = await chrome.tabs.query({});
  const out = [];
  for (const t of all) {
    const u = (t.url || "").split("#")[0];
    if (u !== c.url) continue;
    let probe = null;
    try { const [r] = await chrome.scripting.executeScript({ target: { tabId: t.id }, func: hbmComposerProbe }); probe = r.result; }
    catch (e) { probe = { err: String(e) }; }
    let wfocused = false;
    try { wfocused = (await chrome.windows.get(t.windowId)).focused; } catch (e) {}
    out.push({ id: t.id, windowId: t.windowId, active: t.active, url: u, status: t.status, discarded: !!t.discarded, wfocused, probe });
  }
  await postReport(Object.assign(await baseBody(), { verify_result: { vid: c.vid, url: c.url, tabs: out, at: Date.now() } }));
}
const doneIds = new Set();
async function seenDispatch(id) {
  if (doneIds.has(id)) return true;
  try { const r = await chrome.storage.session.get("hbm_done"); if ((r.hbm_done || []).includes(id)) return true; } catch (e) {}
  return false;
}
async function markDispatch(id) {
  doneIds.add(id);
  try { const r = await chrome.storage.session.get("hbm_done"); const l = (r.hbm_done || []).slice(-200); l.push(id); await chrome.storage.session.set({ hbm_done: l }); } catch (e) {}
}
async function sendText(c) {
  const res = { dispatch_id: c.dispatch_id || "", tab_id: c.tab_id, url: c.url };
  const finish = async (status, reason, extra) => {
    Object.assign(res, { status, reason: reason || "" }, extra || {});
    await postReport(Object.assign(await baseBody(), { dispatch_result: res }));
  };
  if (!c.dispatch_id) return;                       // never send without a dispatch ID
  if (await seenDispatch(c.dispatch_id)) return finish("duplicate", "dispatch ID already used");
  await markDispatch(c.dispatch_id);                // before anything is typed: the same ID can never send twice
  if (!c.expires_ms || Date.now() > c.expires_ms) return finish("expired", "dispatch ID expired");
  const t = await chrome.tabs.get(c.tab_id).catch(() => null);
  if (!t) return finish("blocked", "Tab Missing");
  if ((t.url || "").split("#")[0] !== c.url) return finish("blocked", "Tab URL changed");
  if (!t.active) return finish("blocked", "Tab not active");
  const msg = { type: "hbm_send", text: c.text, url: c.url, dispatch_id: c.dispatch_id };
  let r = await chrome.tabs.sendMessage(t.id, msg).catch(e => ({ noscript: true, err: String(e) }));
  if (r && r.noscript) {
    // content script missing in that tab (nothing was typed): load it and try once more
    try { await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["content.js"] }); } catch (e) {}
    await sleep(800);
    const t2 = await chrome.tabs.get(c.tab_id).catch(() => null);
    if (!t2 || !t2.active || (t2.url || "").split("#")[0] !== c.url) return finish("blocked", "Tab changed");
    r = await chrome.tabs.sendMessage(t.id, msg).catch(e => ({ err: String(e) }));
  }
  if (!r || r.err) return finish("unconfirmed", "no answer from the tab: " + ((r && r.err) || "none"));
  return finish(r.status || "unconfirmed", r.reason || "", { via: r.via || "", msgId: r.msgId || "" });
}
async function runCommands(cmds) {
  // a reload always runs last so it never cuts another command short
  cmds = (cmds || []).filter(c => c.type !== "reload_ext").concat((cmds || []).filter(c => c.type === "reload_ext"));
  for (const c of cmds) {
    try {
      if (c.type === "open_task") {
        const tabs = await chrome.tabs.query({ url: "https://hark.com/*" });
        let t = tabs.find(x => x.url && c.url && x.url.split("#")[0].split("?")[0] === c.url.split("?")[0]);
        if (t) {
          await chrome.tabs.update(t.id, { active: true });
          await chrome.windows.update(t.windowId, { focused: true, drawAttention: true, state: "normal" });
        } else {
          const w = await chrome.windows.getLastFocused().catch(() => null);
          t = await chrome.tabs.create({ url: c.url, active: true, windowId: w ? w.id : undefined });
          await chrome.windows.update(t.windowId, { focused: true });
        }
      } else if (c.type === "focus") {
        const w = await chrome.windows.getLastFocused();
        await chrome.windows.update(w.id, { focused: true, state: "normal" });
      } else if (c.type === "check_limit") {
        await checkLimit(c);
      } else if (c.type === "show_hark") {
        // the Manager's Multi-View pane hosts this profile's window: put its Hark chat tab in front (no window focus)
        const tabs = await chrome.tabs.query({ url: "https://hark.com/*" });
        // a team worker's own project tab (exact URL) first; otherwise the first signed-in Hark tab
        const t = (c.url && tabs.find(x => (x.url || "").split("#")[0] === c.url)) || tabs.find(x => !/sign-in|login/.test(x.url || "")) || tabs[0];
        if (t) await chrome.tabs.update(t.id, { active: true });
        else {
          const w = await chrome.windows.getLastFocused().catch(() => null);
          await chrome.tabs.create({ url: "https://hark.com/chat", active: true, windowId: w ? w.id : undefined });
        }
      } else if (c.type === "vpn_check") {
        vpnCache.at = 0;
        await postReport({ email: await getEmail(), tag: await getTag(), ext_id: chrome.runtime.id, ver: chrome.runtime.getManifest().version, build: CODE_BUILD,
                           tabs: [], windows: [], hark: null, vpn_only: true, vpn: await vpnState(true) });
      } else if (c.type === "rescan") {
        // Manager Refresh: every Hark tab re-sends its live state now, then a fresh tab/window report
        const tabs = await chrome.tabs.query({ url: "https://hark.com/*" });
        for (const t of tabs) {
          let ok = await chrome.tabs.sendMessage(t.id, { type: "rescan" }).catch(() => null);
          if (!ok) {   // content script missing (tab opened before the bridge loaded): inject it, it reports on load
            try { await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["content.js"] }); } catch (e) {}
          }
        }
        await report();
      } else if (c.type === "reload_ext") {
        chrome.runtime.reload();
      } else if (c.type === "reload_hark") {
        const tabs = await chrome.tabs.query({ url: "https://hark.com/*" });
        for (const t of tabs) await chrome.tabs.reload(t.id);
      } else if (c.type === "verify_tab") {
        await verifyTab(c);
      } else if (c.type === "send_text") {
        await sendText(c);
      }
    } catch (e) { console.warn("cmd failed", c, e); }
  }
}
// VPN status of THIS profile, read-only: who controls the proxy (Browsec sets it through chrome.proxy) and,
// when a VPN extension holds it, the country of the public IP this profile's traffic actually leaves from.
const IPS = [["https://api.country.is/", "country"], ["https://ipinfo.io/json", "country"], ["https://ipwho.is/", "country_code"], ["https://ipapi.co/json/", "country_code"]];
let vpnCache = { key: "", country: "", at: 0 };
async function ipCountry() {
  for (const [u, k] of IPS) {
    try {
      const ac = new AbortController(); const tm = setTimeout(() => ac.abort(), 6000);
      const r = await fetch(u, { cache: "no-store", signal: ac.signal }); clearTimeout(tm);
      if (!r.ok) continue;
      const c = ((await r.json())[k] || "").toUpperCase();
      if (/^[A-Z]{2}$/.test(c)) return c;
    } catch (e) {}
  }
  return "";
}
async function vpnState(force) {
  let ps;
  try { ps = await chrome.proxy.settings.get({ incognito: false }); } catch (e) { return { state: "unknown", why: "proxy api" }; }
  const lvl = ps.levelOfControl || "", mode = (ps.value || {}).mode || "";
  // the bridge never sets a proxy itself, so a pac/fixed proxy that policy does not hold is a VPN extension's
  // (Browsec). Its level reads "controllable_by_this_extension" (not "controlled_by_other_extensions") once the
  // bridge was (re)loaded after the VPN: Chrome ranks the newest-installed extension first.
  const held = lvl === "controlled_by_other_extensions" || lvl === "controllable_by_this_extension" || lvl === "controlled_by_this_extension";
  if (!held || !(mode === "pac_script" || mode === "fixed_servers")) { vpnCache = { key: "", country: "", at: 0 }; return { state: "off", lvl, mode, at: Date.now() }; }
  const key = lvl + "|" + mode;
  if (force || vpnCache.key !== key || Date.now() - vpnCache.at > 180000 || !vpnCache.country) {
    vpnCache = { key, country: await ipCountry(), at: Date.now() };
  }
  return vpnCache.country ? { state: "on", country: vpnCache.country, lvl, mode, at: vpnCache.at }
                          : { state: "unknown", why: "ip check failed", lvl, mode, at: vpnCache.at };
}
try { chrome.proxy.settings.onChange.addListener(() => { vpnCache.at = 0; report().catch(() => {}); }); } catch (e) {}
async function report(extra) {
  const tabs = await chrome.tabs.query({});
  const wins = await chrome.windows.getAll();
  const body = {
    email: await getEmail(),
    tag: await getTag(),
    ext_id: chrome.runtime.id,
    ver: chrome.runtime.getManifest().version,
    build: CODE_BUILD,
    windows: wins.map(w => ({ id: w.id, focused: w.focused, state: w.state })),
    tabs: tabs.map(t => ({ id: t.id, windowId: t.windowId, active: t.active, url: t.url || "", title: t.title || "" })),
    hark: extra || null,
    vpn: await vpnState(false).catch(() => ({ state: "unknown", why: "error" }))
  };
  const res = await post("/report", body);
  await runCommands(res && res.commands);
}
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "hark_state") {
    const st = Object.assign({}, msg.state, { tabId: sender.tab && sender.tab.id, windowId: sender.tab && sender.tab.windowId });
    report(st).then(() => sendResponse({ ok: true })).catch(e => sendResponse({ ok: false, err: String(e) }));
    return true;
  }
});
chrome.tabs.onActivated.addListener(() => report().catch(() => {}));
chrome.tabs.onRemoved.addListener(() => report().catch(() => {}));
chrome.runtime.onStartup.addListener(() => report().catch(() => {}));
// heartbeat: a profile with no Hark tab still checks in (and picks up Manager commands) every 30 s
try { chrome.alarms.create("hbm-beat", { periodInMinutes: 0.5 }); chrome.alarms.onAlarm.addListener(a => { if (a.name === "hbm-beat") report().catch(() => {}); }); } catch (e) {}
async function injectExisting() {
  const tabs = await chrome.tabs.query({ url: "https://hark.com/*" });
  for (const t of tabs) {
    try { await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["content.js"] }); } catch (e) {}
  }
}
chrome.runtime.onInstalled.addListener(() => { injectExisting(); report().catch(() => {}); });
