// Hark Browser Manager Bridge - service worker
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
// a profile the Manager made with its own user-data-dir is identified by a tag (its first page carries it)
let tag = null;
async function getTag() {
  if (tag !== null) return tag;
  // per-profile copy of this extension carries tag.json (written by the Manager); fall back to the first-page tag
  try { const r = await fetch(chrome.runtime.getURL("tag.json")); if (r.ok) { tag = ((await r.json()).tag || "") + ""; if (tag) return tag; } } catch (e) {}
  try { const r = await chrome.storage.local.get("hbm_tag"); tag = r.hbm_tag || ""; } catch (e) { tag = ""; }
  return tag;
}
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
  const r = await fetch(APP + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
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
  await post("/report", body);
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
      } else if (c.type === "reload_ext") {
        chrome.runtime.reload();
      } else if (c.type === "reload_hark") {
        const tabs = await chrome.tabs.query({ url: "https://hark.com/*" });
        for (const t of tabs) await chrome.tabs.reload(t.id);
      } else if (c.type === "send_text") {
        const tabs = await chrome.tabs.query({ url: "https://hark.com/*" });
        const t = tabs.find(x => x.url.startsWith(c.url)) || tabs[0];
        const r = await chrome.tabs.sendMessage(t.id, { type: "send_text", text: c.text }).catch(e => ({ err: String(e) }));
        await post("/dump", { email: await getEmail(), url: t.url, data: { send_result: r } });
      } else if (c.type === "dump") {
        const tabs = await chrome.tabs.query({ url: "https://hark.com/*" });
        for (const t of tabs) {
          const [res] = await chrome.tabs.sendMessage(t.id, { type: "dump" }).then(r => [r]).catch(e => [{ err: String(e) }]);
          await post("/dump", { email: await getEmail(), url: t.url, data: res });
        }
      }
    } catch (e) { console.warn("cmd failed", c, e); }
  }
}
async function report(extra) {
  const tabs = await chrome.tabs.query({});
  const wins = await chrome.windows.getAll();
  const body = {
    email: await getEmail(),
    tag: await getTag(),
    ext_id: chrome.runtime.id,
    ver: chrome.runtime.getManifest().version,
    windows: wins.map(w => ({ id: w.id, focused: w.focused, state: w.state })),
    tabs: tabs.map(t => ({ id: t.id, windowId: t.windowId, active: t.active, url: t.url || "", title: t.title || "" })),
    hark: extra || null
  };
  const res = await post("/report", body);
  await runCommands(res.commands);
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
