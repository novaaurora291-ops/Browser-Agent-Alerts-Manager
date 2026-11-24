// Hark Browser Manager Bridge - content script on hark.com (runs in background tabs too)
(function () {
  const HBM_VER = chrome.runtime.getManifest().version;
  if (window.__hbmLoaded === HBM_VER) return; window.__hbmLoaded = HBM_VER;
  function compact(el, depth) {
    if (!el || depth > 14) return null;
    const o = { t: el.tagName.toLowerCase() };
    if (el.id) o.id = el.id;
    const cls = (el.getAttribute("class") || "").trim(); if (cls) o.c = cls.slice(0, 120);
    for (const a of el.getAttributeNames()) if (a.startsWith("data-") || a.startsWith("aria-") || a === "role") o[a] = (el.getAttribute(a) || "").slice(0, 80);
    const kids = Array.from(el.children);
    if (!kids.length) { const tx = (el.textContent || "").trim(); if (tx) o.x = tx.slice(0, 160); }
    else o.k = kids.slice(-25).map(k => compact(k, depth + 1)).filter(Boolean);
    return o;
  }
  function txt(el) { return (el ? (el.innerText || el.textContent || "") : "").replace(/\s+\n/g, "\n").trim(); }
  function isUser(e) {
    const kind = (e.getAttribute("data-entry-kind") || "") + " " + (e.getAttribute("data-role") || "");
    if (/user|human|outgoing|self|me\b/i.test(kind)) return true;
    if (/assistant|agent|bot|hark/i.test(kind)) return false;
    if (e.querySelector("[data-testid=bubble-wrap-host]")) return false;
    // an entry with plain text and no assistant bubble is the user's own (also while it is still sending)
    if (!e.querySelector("[data-testid=bubble-wrap-host], button, input, form") && txt(e)) return true;
    return !!e.querySelector("[data-testid=message-text] > span:not([aria-hidden])");
  }
  function isBrief(e) {
    const row = e.closest("[data-row-slot]");
    const div = row && row.querySelector("[data-row-divider]");
    return !!(div && /brief/i.test(div.textContent || ""));
  }
  function convTitle() {
    const p = location.pathname;
    if (!p.startsWith("/project/")) return "Chat";
    const cur = document.querySelector("[data-testid=projects-panel] [aria-current], [data-testid=projects-panel] [aria-selected=true], [data-testid=projects-panel] [data-active=true]");
    if (cur && txt(cur)) return txt(cur).split("\n")[0].slice(0, 80);
    const h = document.querySelector("[data-testid=chat-column] h1, [data-testid=project-title], header h1");
    if (h && txt(h)) return txt(h).slice(0, 80);
    return "Project " + p.split("/")[2].slice(0, 8);
  }
  // ---- live run state (what Hark itself shows) ----
  const IDLE_PHASE = /^(done|idle|complete|completed|finished|stopped|error)$/i;
  const OFFER = /^(want me to|would you like|would a |would an |shall i|should i also|do you want me to|if you('d| would) like|i can also)/i;
  function projectId() { const m = location.pathname.match(/^\/project\/([0-9a-f-]{8,})/i); return m ? m[1] : ""; }
  function convKey() { const id = projectId(); return id ? "project:" + id : (location.pathname.startsWith("/chat") || location.pathname === "/" ? "chat" : location.pathname); }
  function runningProjects() {
    const out = [];
    document.querySelectorAll("[data-project-id]").forEach(el => {
      if (/(^|\n)\s*(Working|Waiting for your task to finish)\s*($|\n)/i.test(txt(el))) out.push(el.getAttribute("data-project-id"));
    });
    return out;
  }
  function shown(el) {
    if (!el || !el.isConnected) return false;
    for (let a = el, i = 0; a && i < 6; a = a.parentElement, i++) {
      const cs = getComputedStyle(a);
      if (cs.display === "none" || cs.visibility === "hidden" || parseFloat(cs.opacity) === 0) return false;
    }
    return true;
  }
  // what Hark itself shows RIGHT NOW for a running task (whole page, not old messages)
  function liveRun() {
    const reasons = [];
    document.querySelectorAll("[data-testid=live-run-pill], [data-testid=run-narration]").forEach(el => {
      const ph = el.getAttribute("data-phase") || (el.querySelector("[data-phase]") || el).getAttribute("data-phase") || "";
      if (IDLE_PHASE.test(ph) || !shown(el)) return;
      reasons.push("pill:" + (ph || "run") + ":" + txt(el).slice(0, 40));
    });
    // "Waiting for your task to finish" chip
    const w = document.evaluate("//*[normalize-space(text())='Waiting for your task to finish']", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    if (w && shown(w)) reasons.push("waiting-task");
    // composer Stop button only exists while a run is going
    const stop = Array.from(document.querySelectorAll("button[aria-label]")).find(b => /^stop( generating| run| task)?$/i.test(b.getAttribute("aria-label")) && shown(b));
    if (stop) reasons.push("stop-button");
    const pid = projectId();
    if (pid && runningProjects().includes(pid)) reasons.push("badge");
    return Array.from(new Set(reasons));
  }
  window.__harkDetect = function () {
    const list = document.querySelector("[data-testid=message-list]");
    const base = { pending: false, working: false, convTitle: convTitle(), convKey: convKey(), hasList: !!list, running: runningProjects() };
    if (!list) return base;
    const entries = Array.from(list.querySelectorAll("[data-entry-kind]")).filter(e => !e.parentElement.closest("[data-entry-kind]"));
    const why = liveRun();
    base.workWhy = why;
    if (!entries.length) return Object.assign(base, { working: why.length > 0 });
    // assistant messages the user has already replied after (used to mark alerts Resolved)
    const answered = [];
    let seenUser = false;
    for (let i = entries.length - 1; i >= 0 && answered.length < 12; i--) {
      if (isUser(entries[i])) { seenUser = true; continue; }
      if (seenUser) { const id = entries[i].getAttribute("data-message-id"); if (id) answered.push(id); }
    }
    base.answered = answered;
    const last = entries[entries.length - 1];
    base.lastId = last.getAttribute("data-message-id") || "";
    if (isUser(last)) return Object.assign(base, { working: true, workWhy: why.concat("user-last") });
    if (why.length) return Object.assign(base, { working: true });
    // Hark is idle: only its LAST real message (after the user's latest message) can be waiting on the user
    let e = null;
    for (let i = entries.length - 1; i >= 0 && !isUser(entries[i]); i--) {
      if (isBrief(entries[i])) continue;
      e = entries[i]; break;
    }
    if (!e) return base;
    const t = txt(e.querySelector("[data-testid=message-text]") || e);
    const hasForm = !!e.querySelector("input:not([type=hidden]), textarea, button[aria-label*=pprove i], form");
    const paras = Array.from(e.querySelectorAll("p, li")).map(txt).filter(Boolean);
    const qs = paras.filter(x => x.includes("?") || x.includes("\u061F"));
    if (!qs.length && !hasForm && !(t.includes("?") || t.includes("\u061F"))) return base;
    let q = qs.pop() || t;
    // the question sentence itself (last sentence ending in ?)
    const sent = q.split(/(?<=[.!\u06D4])\s+/).filter(x => /[?\u061F]/.test(x)).pop() || q;
    const optional = !hasForm && OFFER.test(sent.trim());
    return Object.assign(base, { pending: !optional, optional, qid: e.getAttribute("data-message-id") || e.getAttribute("data-entry-id") || ("h" + t.length + t.slice(0, 40)), question: q.slice(0, 600), full: t.slice(0, 1200) });
  };
  let seenQ = "", seenAt = 0;
  function detect() {
    const st = window.__harkDetect ? window.__harkDetect() : { pending: false };
    if (st.pending) {
      if (st.qid !== seenQ) { seenQ = st.qid; seenAt = Date.now(); }
      if (Date.now() - seenAt < 6000) { st.pending = false; st.settling = true; }
    } else { seenQ = ""; }
    try {
      const l = document.querySelector("[data-testid=message-list]");
      if (l) st.kinds = Array.from(l.querySelectorAll("[data-entry-kind]")).slice(-3).map(e => e.getAttribute("data-entry-kind"));
    } catch (e) {}
    return st;
  }
  let last = "";
  function tick(force) {
    let st;
    try { st = detect(); } catch (e) { st = { pending: false, err: String(e) }; }
    st.url = location.href.split("#")[0];
    st.title = document.title;
    const key = JSON.stringify(st);
    if (!force && key === last && (Date.now() - (window.__hbmSent || 0)) < 4000) return; // changes at once, heartbeat every ~4s
    window.__hbmSent = Date.now();
    last = key;
    try { if (window.__hbmLoaded !== HBM_VER) return; chrome.runtime.sendMessage({ type: "hark_state", state: st }); } catch (e) {}
  }
  chrome.runtime.onMessage.addListener((msg, _s, send) => {
    if (msg && msg.type === "rescan") { last = ""; tick(true); send({ ok: true }); return; }
    if (msg && msg.type === "send_text") {
      const ed = document.querySelector("textarea, [contenteditable=true], [role=textbox]");
      if (!ed) { send({ ok: false, err: "no composer" }); return; }
      ed.focus();
      if (ed.tagName === "TEXTAREA") {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
        setter.call(ed, msg.text); ed.dispatchEvent(new Event("input", { bubbles: true }));
      } else { document.execCommand("insertText", false, msg.text); }
      setTimeout(() => {
        const btn = Array.from(document.querySelectorAll("button")).find(b => /send/i.test(b.getAttribute("aria-label") || ""));
        if (btn) btn.click(); else ed.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
        send({ ok: true, via: btn ? "button" : "enter", tag: ed.tagName });
      }, 400);
      return true;
    }
    if (msg && msg.type === "dump") {
      const ids = {}; document.querySelectorAll("[data-testid]").forEach(e => { const k = e.getAttribute("data-testid"); ids[k] = (ids[k] || 0) + 1; });
      const ml = document.querySelector("[data-testid=message-list]");
      const rows = Array.from(document.querySelectorAll("[data-testid=message-list] [data-row-slot]")).slice(-4).map(r => {
        const b = r.querySelector("[data-testid=message-bubble]"); const rr = b ? b.getBoundingClientRect() : null;
        return { html: r.outerHTML.replace(/<path[^>]*>/g, "").slice(0, 2500), rect: rr ? [rr.left, rr.right, rr.width] : null, listW: document.querySelector("[data-testid=message-list]").getBoundingClientRect().width };
      });
      const tail = document.querySelector("[data-testid=message-list]");
      const marks = [];
      document.querySelectorAll("body *").forEach(e => {
        if (e.children.length) return;
        const t = (e.textContent || "").trim();
        if (t && t.length < 60 && /working|waiting|thinking|paused|needs|reply|running/i.test(t)) {
          let a = e; for (let i = 0; i < 4 && a.parentElement; i++) a = a.parentElement;
          marks.push({ t, html: a.outerHTML.replace(/<path[^>]*>/g, "").replace(/<svg.*?<\/svg>/gs, "").slice(0, 1500) });
        }
      });
      const side = document.querySelector("[data-testid=projects-panel]");
      const col = document.querySelector("[data-testid=chat-column]") || (ml && ml.parentElement && ml.parentElement.parentElement);
      send({ url: location.href, ids, rows, marks: marks.slice(0, 40), side: side ? side.outerHTML.replace(/<path[^>]*>/g, "").replace(/<svg.*?<\/svg>/gs, "").slice(0, 8000) : null,
             colTail: col ? Array.from(col.children).slice(-3).map(c => c.outerHTML.replace(/<path[^>]*>/g, "").replace(/<svg.*?<\/svg>/gs, "").slice(-3000)) : null, tailHtml: tail ? tail.outerHTML.slice(-3000) : "", text: document.body.innerText.slice(-3000), list: ml ? compact(ml, 0) : null,
             composer: Array.from(document.querySelectorAll("textarea, [contenteditable], [role=textbox]")).map(e => compact(e, 12)),
             buttons: Array.from(document.querySelectorAll("button")).map(b => b.getAttribute("aria-label") || b.textContent.trim().slice(0,30)) });
      return;
    }
    if (false) {
      const main = document.querySelector("main") || document.body;
      send({ url: location.href, text: document.body.innerText.slice(-4000), tree: compact(main, 0) });
    }
  });
  setInterval(() => tick(false), 2000);
  let lastMut = 0;
  new MutationObserver(() => {
    const now = Date.now();
    if (now - lastMut > 700) { lastMut = now; tick(false); }  // run inline: timers are throttled in background tabs
    clearTimeout(window.__hbmT); window.__hbmT = setTimeout(() => tick(false), 800);
  })
    .observe(document.documentElement, { subtree: true, childList: true, characterData: true });
  tick(true);
})();
