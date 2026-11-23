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
async function post(path, body) {
  const r = await fetch(APP + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}
async function runCommands(cmds) {
  for (const c of cmds || []) {
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
async function injectExisting() {
  const tabs = await chrome.tabs.query({ url: "https://hark.com/*" });
  for (const t of tabs) {
    try { await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["content.js"] }); } catch (e) {}
  }
}
chrome.runtime.onInstalled.addListener(() => { injectExisting(); report().catch(() => {}); });
