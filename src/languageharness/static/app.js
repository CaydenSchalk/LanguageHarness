"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const touch = matchMedia("(pointer: coarse)").matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- toast
let toastTimer;
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}
$("#toast").addEventListener("click", () => $("#toast").classList.remove("show"));

// ---------------------------------------------------------------- network
async function api(url, opts = {}) {
  const r = await fetch(url, { headers: { "content-type": "application/json" }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

class ServerError extends Error {}

/** POST and consume an SSE stream. handlers: {eventName: fn(data)}. Server "error" events throw ServerError;
 *  a dropped connection throws a plain Error (the server keeps working on the turn). */
async function stream(url, body, handlers) {
  const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
  if (!r.ok || !r.body) throw new ServerError((await r.json().catch(() => ({}))).detail || r.statusText);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "", failed = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) !== -1) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      let ev = "message", data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) ev = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      const parsed = data ? JSON.parse(data) : {};
      if (ev === "error") failed = parsed.message;
      handlers[ev]?.(parsed);
    }
  }
  if (failed) throw new ServerError(failed);
}

// ---------------------------------------------------------------- routing
const views = ["setup", "prep", "chat", "review", "history", "settings"];
const tabOf = { setup: "setup", prep: "setup", chat: "history", review: "history", history: "history", settings: "settings" };
function show(name) {
  for (const v of views) $(`#view-${v}`).hidden = v !== name;
  document.body.dataset.view = name;
  $$(".tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tabOf[name]));
  if (name !== "chat") { closeSheet(); stopTimer(); }
  window.scrollTo(0, 0);
}
function go(hash) { if (location.hash !== hash) location.hash = hash; else route(); }
window.addEventListener("hashchange", route);

async function route() {
  const [name, id] = location.hash.slice(1).split("/");
  try {
    if (name === "prep" && id) return await openPrep(id);
    if (name === "chat" && id) return await openChat(id);
    if (name === "review" && id) return await openReview(id);
    if (name === "history") return await openHistory();
    if (name === "settings") return await openSettings();
  } catch (e) { toast(e.message); }
  show("setup");
  openSetup();
}

// ---------------------------------------------------------------- setup
const form = $("#setup-form");
form.addEventListener("change", () => {
  const mode = form.mode.value;
  $$(".mode-opt").forEach((el) => (el.hidden = el.dataset.mode !== mode));
});
$$(".chips").forEach((c) => c.addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  $$("button", c).forEach((x) => x.classList.toggle("on", x === b));
  form[c.dataset.target].value = b.dataset.v;
}));
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(form);
  const body = {
    topic: f.get("topic"), scenario: f.get("scenario"), level: f.get("level"), politeness: f.get("politeness"),
    mode: f.get("mode"), budget_turns: +f.get("budget_turns"), budget_minutes: +f.get("budget_minutes"),
    goal: f.get("goal") || "", goal_max_turns: +f.get("goal_max_turns"),
    use_research: !!f.get("use_research"), use_anki: !!f.get("use_anki"),
  };
  if (body.mode === "goal" && !body.goal.trim()) body.goal = `Talk about "${body.topic}" and accomplish the scenario naturally.`;
  document.activeElement?.blur();
  try {
    const s = await api("/api/sessions", { method: "POST", body });
    form.topic.value = ""; form.scenario.value = "";
    go(`#prep/${s.id}`);
  } catch (err) { toast(err.message); }
});

async function openSetup() {
  try {
    const [{ settings, secrets }, { sessions }] = await Promise.all([api("/api/settings"), api("/api/sessions")]);
    const msgs = [];
    if (!settings.partner.model || !settings.utility.model) msgs.push("⚠ Choose models in 設定 first.");
    if (!secrets.TAVILY_API_KEY) msgs.push("No Tavily key: the brief uses only the model's own knowledge.");
    $("#setup-hint").textContent = msgs.join(" ");
    const open = sessions.find((s) => s.status === "active" || s.status === "ready");
    const card = $("#continue-card");
    card.hidden = !open;
    if (open) {
      card.innerHTML = `<a class="continue" href="#chat/${esc(open.id)}"><div class="t"><small>続ける Continue</small><b class="ellipsis">${esc(open.topic)}</b>
        <small>${open.turns} exchanges so far</small></div><span class="arrow">›</span></a>`;
    }
  } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- prep
function renderBrief(b) {
  if (!b || (!b.summary && !b.facts?.length)) return "<p class='hint'>No brief.</p>";
  return `<div class="brief"><p>${esc(b.summary)}</p>
    ${b.facts?.length ? `<ul>${b.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    ${b.vocab?.length ? `<p><b>語彙:</b> ${b.vocab.map((v) => `${esc(v.word)}<small>（${esc(v.reading)}）${esc(v.meaning)}</small>`).join("、")}</p>` : ""}
    ${b.arc?.length ? `<p><b>Arc:</b> ${b.arc.map(esc).join(" → ")}</p>` : ""}
    ${b.goal_checkpoints?.length ? `<p><b>Goal checkpoints:</b></p><ul>${b.goal_checkpoints.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${b.sources?.length ? `<div class="src">${b.sources.map((s) => `<div><a href="${esc(s.url)}" target="_blank" rel="noreferrer">${esc(s.title || s.url)}</a></div>`).join("")}</div>` : ""}
  </div>`;
}
function renderTargets(ts) {
  return ts.map((t) => `<span class="tchip ${t.used ? "used" : ""}" title="${esc(t.meaning)}">${esc(t.expression)}${t.reading && t.reading !== t.expression ? `<span class="r">${esc(t.reading)}</span>` : ""}</span>`).join("");
}

async function openPrep(id) {
  show("prep");
  const { session: s, busy } = await api(`/api/sessions/${id}`);
  if (s.status === "active" || s.status === "ended") return go(`#chat/${id}`);
  const log = $("#prep-log");
  const done = (d) => {
    $("#prep-title").innerHTML = `準備完了 <small>${esc(s.topic)}</small>`;
    $("#prep-brief").innerHTML = renderBrief(d.brief);
    $("#prep-targets").innerHTML = renderTargets(d.targets) || `<span class="hint">${esc(d.anki_note || "No target words.")}</span>`;
    $("#prep-tcount").textContent = d.targets.length ? `(${d.targets.length})` : "";
    $("#prep-ready").hidden = false;
    log.innerHTML = "";
  };
  $("#start-btn").onclick = () => go(`#chat/${id}`);
  $("#prep-ready").hidden = true;
  log.innerHTML = "";
  if (s.status === "ready") return done({ brief: s.brief, targets: s.targets, anki_note: s.anki_note });
  $("#prep-title").innerHTML = `準備中… <small>${esc(s.topic)}</small>`;
  if (busy) { // prep still running from before the app was backgrounded
    log.innerHTML = `<li><span class="spinner"></span>準備を続けています…</li>`;
    return waitUntilIdle(id, () => openPrep(id));
  }
  try {
    await stream(`/api/sessions/${id}/prepare`, {}, {
      progress: (d) => log.insertAdjacentHTML("beforeend", `<li>${esc(d.message)}</li>`),
      ready: done,
    });
  } catch (e) {
    if (!(e instanceof ServerError)) return waitUntilIdle(id, () => openPrep(id));
    log.insertAdjacentHTML("beforeend", `<li class="err">${esc(e.message)}</li><li><button type="button" onclick="location.reload()">Retry</button></li>`);
  }
}

/** Poll until the server has finished whatever it was doing for this session, then call `then`. */
async function waitUntilIdle(id, then) {
  for (let i = 0; i < 120; i++) {
    await sleep(2000);
    if (!location.hash.includes(id)) return;
    try { if (!(await api(`/api/sessions/${id}`)).busy) return then(); } catch { /* offline: keep waiting */ }
  }
}

// ---------------------------------------------------------------- chat
let cur = null; // {id, state, timer, streaming}
const chatLog = $("#chat-log");

function corrHtml(c) {
  if (!c) return `<div class="corr"><span class="pending">確認中…</span></div>`;
  if (c.error) return `<div class="corr"><span class="pending">訂正なし (unavailable)</span></div>`;
  if (c.ok && !c.notes?.length) return `<div class="corr"><button type="button" class="corr-chip ok" disabled>✓ 自然</button></div>`;
  return `<div class="corr"><button type="button" class="corr-chip fix">訂正 ›</button><div class="corr-body"><div class="fixed">${esc(c.corrected)}</div>
    <ul>${(c.notes || []).map((n) => `<li><b>${esc(n.original)}</b> → <b>${esc(n.fix)}</b>: ${esc(n.explanation)}</li>`).join("")}</ul></div></div>`;
}
chatLog.addEventListener("click", (e) => {
  const chip = e.target.closest(".corr-chip.fix");
  if (!chip) return;
  const box = chip.parentElement;
  box.classList.toggle("open");
  chip.textContent = box.classList.contains("open") ? "訂正 ⌄" : "訂正 ›";
});

function addRow(role, text, extra = {}) {
  const row = document.createElement("div");
  row.className = `msg ${role}`;
  row.innerHTML = `<div class="bubble ${extra.phase === "closing" ? "closing" : ""}">${esc(text)}</div>${role === "user" ? corrHtml(extra.correction) : ""}`;
  chatLog.append(row);
  scrollChat();
  return row;
}
function scrollChat() { chatLog.scrollTop = chatLog.scrollHeight; }

function renderState(st) {
  cur.state = st;
  const ended = st.status === "ended";
  const ph = $("#phase-label");
  ph.textContent = ended ? "ended" : st.phase.replace("_", " ");
  ph.className = `phase ${ended ? "ended" : st.phase}`;
  let label = "", pct = st.info?.progress ?? 0;
  if (st.mode === "turns") label = `${st.turn} / ${st.budget_turns}`;
  else if (st.mode === "goal") label = `${st.checkpoints_met.length} / ${st.checkpoints.length} goals`;
  if (ended) pct = 1;
  $("#budget-label").textContent = label;
  $("#bar-fill").style.width = `${Math.round(pct * 100)}%`;
  $("#targets").innerHTML = renderTargets(st.targets) || `<span class="hint">none</span>`;
  $("#tcount").textContent = st.targets.length ? `${st.targets.filter((t) => t.used).length}/${st.targets.length}` : "";
  $("#goal-panel").hidden = st.mode !== "goal";
  $("#checkpoints").innerHTML = st.checkpoints.map((c) => `<li class="${st.checkpoints_met.includes(c) ? "done" : ""}">${esc(c)}</li>`).join("");
  $("#chat-form").hidden = ended;
  $("#ended-bar").hidden = !ended;
  $("#end-btn").hidden = ended;
  if (ended) stopTimer();
  tickTime();
}

function stopTimer() { if (cur?.timer) { clearInterval(cur.timer); cur.timer = null; } }
function tickTime() {
  const st = cur?.state;
  if (!st || st.mode !== "time" || !st.started_at || st.status === "ended") return;
  const el = Math.max(0, Date.now() / 1000 - st.started_at) / 60;
  const m = st.budget_minutes;
  $("#budget-label").textContent = `${Math.floor(el)}:${String(Math.floor((el % 1) * 60)).padStart(2, "0")} / ${m}:00`;
  $("#bar-fill").style.width = `${Math.min(100, (el / m) * 100)}%`;
}

function setBusy(b) {
  cur && (cur.streaming = b);
  $("#send-btn").disabled = b; $("#end-btn").disabled = b;
}

async function runStream(url, body, userRow) {
  setBusy(true);
  let row = null, bubble = null;
  try {
    await stream(url, body, {
      delta: (d) => {
        if (!row) { row = addRow("assistant", ""); bubble = $(".bubble", row); bubble.classList.add("typing"); }
        bubble.textContent += d.text;
        scrollChat();
      },
      assistant_done: (d) => { if (bubble) { bubble.classList.remove("typing"); bubble.textContent = d.text; if (d.phase === "closing") bubble.classList.add("closing"); } },
      correction: (d) => { if (userRow) userRow.querySelector(".corr").outerHTML = corrHtml(d.correction); },
      state: renderState,
    });
    return true;
  } catch (e) {
    if (e instanceof ServerError) {
      row?.remove();
      userRow?.remove();
      toast(e.message);
      return false;
    }
    // connection dropped (app backgrounded, network blip): the server finishes the turn; resync
    toast("接続が切れました。返信を待っています…");
    setBusy(false);
    resync();
    return true;
  } finally { setBusy(false); }
}

async function openChat(id, { quiet = false } = {}) {
  const { session: s, state, busy } = await api(`/api/sessions/${id}`);
  if (s.status === "new") return go(`#prep/${id}`);
  show("chat");
  stopTimer();
  cur = { id, state, timer: setInterval(tickTime, 1000), streaming: false };
  chatLog.innerHTML = "";
  for (const m of s.messages) addRow(m.role, m.content, m);
  $("#chat-topic").textContent = s.topic;
  $("#side-brief").innerHTML = renderBrief(s.brief);
  $("#goal-text").textContent = s.goal;
  $("#anki-note").textContent = s.anki_note || "";
  renderState(state);
  requestAnimationFrame(scrollChat);
  if (busy) {
    const w = addRow("assistant", "");
    $(".bubble", w).classList.add("typing");
    setBusy(true);
    return waitUntilIdle(id, () => openChat(id, { quiet: true }));
  }
  if (s.status === "ready" && !quiet) await runStream(`/api/sessions/${id}/start`);
}

async function resync() {
  if (!cur || cur.streaming || document.body.dataset.view !== "chat") return;
  try { await openChat(cur.id, { quiet: true }); } catch { /* offline */ }
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") resync(); });
window.addEventListener("pageshow", (e) => { if (e.persisted) resync(); });

const input = $("#chat-input");
function autosize() { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 152)}px`; }
input.addEventListener("input", autosize);
$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || !cur || cur.streaming) return;
  input.value = ""; autosize();
  const row = addRow("user", text);
  const ok = await runStream(`/api/sessions/${cur.id}/message`, { text }, row);
  if (!ok) { input.value = text; autosize(); }
});
input.addEventListener("keydown", (e) => {
  // desktop: Enter sends. Phones: Enter is a newline (IME-friendly); send with the button.
  if (!touch && e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});
$("#end-btn").addEventListener("click", async () => {
  if (!cur || !confirm("会話を終わりにしますか？\nThe partner will say goodbye.")) return;
  closeSheet();
  await runStream(`/api/sessions/${cur.id}/close`);
});
$("#review-btn").addEventListener("click", () => go(`#review/${cur.id}`));
$("#show-corr").addEventListener("change", (e) => chatLog.classList.toggle("hide-corr", !e.target.checked));

// bottom sheet
function openSheet() { $("#chat-side").classList.add("open"); $("#sheet-backdrop").classList.add("open"); }
function closeSheet() { $("#chat-side").classList.remove("open"); $("#sheet-backdrop").classList.remove("open"); }
$("#sheet-btn").addEventListener("click", openSheet);
$("#sheet-backdrop").addEventListener("click", closeSheet);
(() => { // swipe the sheet down to close
  const sheet = $("#chat-side");
  let y0 = null;
  sheet.addEventListener("touchstart", (e) => { y0 = sheet.scrollTop <= 0 ? e.touches[0].clientY : null; }, { passive: true });
  sheet.addEventListener("touchmove", (e) => {
    if (y0 === null) return;
    const dy = e.touches[0].clientY - y0;
    if (dy > 0) sheet.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  sheet.addEventListener("touchend", (e) => {
    if (y0 === null) return;
    const dy = e.changedTouches[0].clientY - y0;
    sheet.style.transform = "";
    if (dy > 80) closeSheet();
    y0 = null;
  });
})();

// keep the composer above the iOS keyboard
if (window.visualViewport) {
  const fit = () => {
    const v = $("#view-chat");
    if (innerWidth >= 900) { v.style.height = ""; v.style.transform = ""; return; }
    v.style.height = `${visualViewport.height}px`;
    v.style.transform = `translateY(${visualViewport.offsetTop}px)`;
    if (document.activeElement === input) scrollChat();
  };
  visualViewport.addEventListener("resize", fit);
  visualViewport.addEventListener("scroll", fit);
}

// ---------------------------------------------------------------- review
async function openReview(id) {
  show("review");
  const body = $("#review-body");
  const { session: s } = await api(`/api/sessions/${id}`);
  let r = s.review;
  if (!r) {
    body.innerHTML = `<h1 class="page-title">振り返り <small>${esc(s.topic)}</small></h1><div class="card"><span class="spinner"></span>振り返りを書いています…</div>`;
    try { r = await api(`/api/sessions/${id}/review`, { method: "POST" }); }
    catch (e) { body.innerHTML += `<p class="hint">${esc(e.message)}</p><button type="button" onclick="location.reload()">Retry</button>`; return; }
    if (!location.hash.startsWith(`#review/${id}`)) return;
  }
  const st = r.stats || {};
  const total = r.targets_used.length + r.targets_missed.length;
  body.innerHTML = `
    <h1 class="page-title">振り返り <small>${esc(s.topic)}</small></h1>
    <div class="stats"><div><b>${st.turns ?? "-"}</b>exchanges</div><div><b>${st.minutes ?? "-"}</b>minutes</div>
      <div><b>${st.messages_ok ?? 0}/${st.turns ?? 0}</b>natural messages</div><div><b>${r.targets_used.length}/${total}</b>target words</div></div>
    <div class="card"><p style="margin:0">${esc(r.summary)}</p>
      ${r.strengths?.length ? `<h3 style="margin-top:1rem">良かった点 Strengths</h3><ul>${r.strengths.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>
    <div class="card"><h3>よくある間違い Recurring mistakes</h3>
      ${(r.recurring_mistakes || []).map((m) => `<div class="mistake"><b>${esc(m.pattern)}</b><ul>${m.examples.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><div class="hint">${esc(m.tip)}</div></div>`).join("") || "<p class='hint'>None 🎉</p>"}</div>
    <div class="card"><h3>ターゲット語彙 Target words</h3>
      <div class="tchips">${r.targets_used.map((t) => `<span class="tchip used">${esc(t)}</span>`).join("")}${r.targets_missed.map((t) => `<span class="tchip">${esc(t)}</span>`).join("")}</div></div>
    <div class="card"><h3>次回 Focus next</h3><ul>${(r.focus_next || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <div class="card"><h3>新しい単語 New words</h3>
      ${(r.new_words || []).map((w, i) => `<label class="word"><input type="checkbox" data-i="${i}" checked><div><span class="w">${esc(w.word)}</span><span class="rd">${esc(w.reading)}</span>
        <div>${esc(w.meaning)}</div><div class="ex">${esc(w.example)}</div></div></label>`).join("") || "<p class='hint'>None.</p>"}
      <div class="row"><label>Deck <input id="add-deck" value="LanguageHarness"></label><label>Note type <input id="add-model" value="Basic"></label></div>
      <button type="button" class="primary wide-btn" id="add-anki">Add selected to Anki</button>
      <a href="/api/sessions/${id}/new-words.tsv" download><button type="button" class="wide-btn">Download TSV</button></a>
    </div>
    <div class="card"><details><summary>Transcript</summary><div class="transcript">${s.messages.map((m) => `<p><b>${m.role === "user" ? "You" : "Partner"}:</b> ${esc(m.content)}${m.correction && !m.correction.ok ? `<br><small>→ ${esc(m.correction.corrected)}</small>` : ""}</p>`).join("")}</div></details></div>
    <button class="primary big" type="button" onclick="location.hash='#setup'">新しい会話 New conversation</button>`;
  $("#add-anki").onclick = async () => {
    const words = $$(".word input:checked").map((c) => r.new_words[+c.dataset.i]);
    try {
      const res = await api("/api/anki/add", { method: "POST", body: { deck: $("#add-deck").value, model: $("#add-model").value, words } });
      toast(`Added ${res.added}.` + (res.errors.length ? ` ${res.errors.length} skipped (${res.errors[0]})` : ""));
    } catch (e) { toast(e.message); }
  };
}

// ---------------------------------------------------------------- history
async function openHistory() {
  show("history");
  const { sessions } = await api("/api/sessions");
  $("#history-list").innerHTML = sessions.map((s) => {
    const dest = s.status === "ended" ? "review" : s.status === "new" ? "prep" : "chat";
    const when = new Date(s.created * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    return `<li><a href="#${dest}/${esc(s.id)}"><div class="t ellipsis">${esc(s.topic)}</div>
      <div class="sub"><span class="badge ${esc(s.status)}">${esc(s.status)}</span><span>${s.turns} turns</span><span>${esc(when)}</span></div></a>
      <button class="more" type="button" data-del="${esc(s.id)}" aria-label="Delete">⋯</button></li>`;
  }).join("") || `<li class="empty">まだ会話がありません<br><small>No conversations yet</small></li>`;
  $$("[data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("この会話を削除しますか？\nDelete this conversation?")) return;
    try { await api(`/api/sessions/${b.dataset.del}`, { method: "DELETE" }); openHistory(); } catch (e) { toast(e.message); }
  }));
}

// ---------------------------------------------------------------- settings
let S = null;
const getK = (o, k) => k.split(".").reduce((a, p) => a?.[p], o);
const setK = (o, k, v) => { const ps = k.split("."); const last = ps.pop(); ps.reduce((a, p) => a[p], o)[last] = v; };
const isLocal = ["localhost", "127.0.0.1", "::1", "[::1]"].includes(location.hostname);

$$(".prov").forEach((sel) => (sel.innerHTML = `<option value="ollama">Ollama</option><option value="openai">OpenAI API key</option><option value="chatgpt">ChatGPT subscription</option>`));

async function loadModels(slot) {
  const prov = $(`[data-k="${slot}.provider"]`).value;
  const sel = $(`[data-k="${slot}.model"]`);
  const saved = getK(S, `${slot}.model`);
  const keep = sel.value || saved;
  sel.innerHTML = `<option value="">loading…</option>`;
  const { models, error } = await api(`/api/models?provider=${prov}`);
  const opts = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`);
  if (!models.length) opts.push(`<option value="">${esc(error || "no models found")}</option>`);
  opts.push(`<option value="__custom">Other (type a name)…</option>`);
  sel.innerHTML = opts.join("");
  if (keep && models.includes(keep)) sel.value = keep;
  else if (keep && keep !== "__custom" && getK(S, `${slot}.provider`) === prov) {
    sel.insertAdjacentHTML("afterbegin", `<option value="${esc(keep)}">${esc(keep)}</option>`);
    sel.value = keep;
  }
}
$$(".model-sel").forEach((sel) => sel.addEventListener("change", () => {
  if (sel.value !== "__custom") return;
  const name = prompt("Model name:");
  if (name) { sel.insertAdjacentHTML("afterbegin", `<option value="${esc(name)}">${esc(name)}</option>`); sel.value = name; }
  else sel.selectedIndex = 0;
}));
$$(".prov").forEach((sel) => sel.addEventListener("change", () => { const slot = sel.dataset.k.split(".")[0]; $(`[data-k="${slot}.model"]`).value = ""; loadModels(slot); }));

async function refreshChatGPT() {
  const st = await api("/api/chatgpt/status");
  $("#chatgpt-status").textContent = st.pending ? "Waiting for browser sign-in…" : st.connected ? `✓ Connected${st.email ? " as " + st.email : ""}${st.planScopes ? "" : " (plan permission missing)"}` : st.error || "Not connected";
  $("#chatgpt-login").hidden = !isLocal;
  $("#chatgpt-remote").hidden = isLocal || st.connected;
  return st;
}
$("#chatgpt-login").onclick = async () => {
  try { await api("/api/chatgpt/login", { method: "POST" }); } catch (e) { toast(e.message); }
  const poll = setInterval(async () => { const st = await refreshChatGPT(); if (!st.pending) { clearInterval(poll); loadModels("partner"); loadModels("utility"); } }, 1500);
};
$("#chatgpt-logout").onclick = async () => {
  if (!confirm("Sign out of ChatGPT?")) return;
  await api("/api/chatgpt/logout", { method: "POST" }); refreshChatGPT();
};

async function openSettings() {
  show("settings");
  const data = await api("/api/settings");
  S = data.settings;
  $$("[data-k]:not(.model-sel)", $("#settings-form")).forEach((el) => (el.value = getK(S, el.dataset.k) ?? ""));
  for (const k of ["OPENAI_API_KEY", "TAVILY_API_KEY"]) { const el = $(`#secret-${k}`); el.value = ""; el.placeholder = data.secrets[k] ? "•••••• saved (type to replace)" : "not set"; }
  $("#anki-decks").innerHTML = S.anki.decks.map((d) => `<label><input type="checkbox" value="${esc(d)}" checked> ${esc(d)}</label>`).join("");
  $("#anki-types").innerHTML = "";
  $("#anki-status").textContent = "";
  refreshChatGPT();
  loadModels("partner"); loadModels("utility");
  api("/api/anki/collections").then(({ paths }) => ($("#collections").innerHTML = paths.map((p) => `<option value="${esc(p)}">`).join("")));
}

function collectSettings() {
  $$("[data-k]", $("#settings-form")).forEach((el) => setK(S, el.dataset.k, el.type === "number" ? +el.value : el.value));
  S.anki.decks = $$("#anki-decks input:checked").map((c) => c.value);
  for (const box of $$(".ntype")) {
    const map = Object.fromEntries($$("select", box).map((s) => [s.dataset.f, s.value]));
    if ($("input[type=checkbox]", box).checked) S.anki.field_maps[box.dataset.name] = map;
    else delete S.anki.field_maps[box.dataset.name];
  }
}

$("#anki-load").onclick = async () => {
  collectSettings();
  try {
    await api("/api/settings", { method: "PUT", body: S }); // so the backend uses the source/path typed here
    $("#anki-status").innerHTML = `<span class="spinner"></span>Loading…`;
    const [d, t] = await Promise.all([api("/api/anki/decks"), api("/api/anki/note-types")]);
    if (d.error) { $("#anki-status").textContent = d.error; return; }
    $("#anki-status").textContent = `Source: ${d.source}. Tick the decks with words you know.`;
    const chosen = new Set(S.anki.decks);
    $("#anki-decks").innerHTML = d.decks.map((x) => `<label><input type="checkbox" value="${esc(x)}" ${chosen.has(x) ? "checked" : ""}> ${esc(x)}</label>`).join("");
    $("#anki-types").innerHTML = `<p class="hint">Field mapping per note type (guessed unless saved). Tick "save" to pin it.</p>` + t.types.map((nt) => {
      const sel = (f) => `<label>${f}<select data-f="${f}"><option value="">—</option>${nt.fields.map((x) => `<option ${nt.map[f] === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select></label>`;
      return `<div class="ntype" data-name="${esc(nt.name)}"><label class="inline"><input type="checkbox" ${nt.saved ? "checked" : ""}> <b>${esc(nt.name)}</b> <small>save</small></label>
        <div class="row">${sel("expression")}${sel("reading")}${sel("meaning")}</div></div>`;
    }).join("");
  } catch (e) { toast(e.message); }
};

$("#settings-save").onclick = async () => {
  collectSettings();
  try {
    await api("/api/settings", { method: "PUT", body: S });
    for (const k of ["OPENAI_API_KEY", "TAVILY_API_KEY"]) {
      const el = $(`#secret-${k}`);
      if (el.value.trim()) { await api("/api/secrets", { method: "PUT", body: { name: k, value: el.value.trim() } }); el.value = ""; el.placeholder = "•••••• saved (type to replace)"; }
    }
    toast("保存しました Saved ✓");
  } catch (e) { toast(e.message); }
};

// ---------------------------------------------------------------- boot
if ("serviceWorker" in navigator && (location.protocol === "https:" || isLocal)) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
route();
