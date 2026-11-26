"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(url, opts = {}) {
  const r = await fetch(url, { headers: { "content-type": "application/json" }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

/** POST and consume an SSE stream. handlers: {eventName: fn(data)} */
async function stream(url, body, handlers) {
  const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
  if (!r.ok || !r.body) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
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
  if (failed) throw new Error(failed);
}

// ---------------------------------------------------------------- routing
const views = ["setup", "prep", "chat", "review", "history"];
function show(name) { for (const v of views) $(`#view-${v}`).hidden = v !== name; }
function go(hash) { if (location.hash !== hash) location.hash = hash; else route(); }
window.addEventListener("hashchange", route);
$$("[data-nav]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); go("#" + a.dataset.nav); }));

async function route() {
  const [name, id] = location.hash.slice(1).split("/");
  try {
    if (name === "prep" && id) return await openPrep(id);
    if (name === "chat" && id) return await openChat(id);
    if (name === "review" && id) return await openReview(id);
    if (name === "history") return await openHistory();
  } catch (e) { alert(e.message); }
  show("setup");
  setupHint();
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
  try {
    const s = await api("/api/sessions", { method: "POST", body });
    go(`#prep/${s.id}`);
  } catch (err) { alert(err.message); }
});

async function setupHint() {
  const { settings, secrets } = await api("/api/settings");
  const msgs = [];
  if (!settings.partner.model || !settings.utility.model) msgs.push("⚠ Choose partner and utility models in Settings.");
  if (!secrets.TAVILY_API_KEY) msgs.push("No Tavily key: the brief will use only the model's own knowledge.");
  if (settings.anki.decks.length === 0) msgs.push("No Anki decks chosen: all reviewed cards will be considered.");
  $("#setup-hint").textContent = msgs.join(" ");
}

// ---------------------------------------------------------------- prep
function renderBrief(b) {
  if (!b) return "<p class='hint'>No brief.</p>";
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
  const { session: s } = await api(`/api/sessions/${id}`);
  if (s.status === "active" || s.status === "ended") return go(`#chat/${id}`);
  const log = $("#prep-log");
  const done = (d) => {
    $("#prep-title").innerHTML = `準備完了 <small>Ready: ${esc(s.topic)}</small>`;
    $("#prep-brief").innerHTML = renderBrief(d.brief);
    $("#prep-targets").innerHTML = renderTargets(d.targets) || `<span class="hint">${esc(d.anki_note || "No target words.")}</span>`;
    $("#prep-tcount").textContent = d.targets.length ? `(${d.targets.length}, from ${d.anki_source})` : "";
    $("#prep-ready").hidden = false;
  };
  $("#prep-ready").hidden = true;
  log.innerHTML = "";
  if (s.status === "ready") return done({ brief: s.brief, targets: s.targets, anki_note: s.anki_note, anki_source: s.anki_source });
  $("#prep-title").innerHTML = `準備中… <small>${esc(s.topic)}</small>`;
  try {
    await stream(`/api/sessions/${id}/prepare`, {}, {
      progress: (d) => log.insertAdjacentHTML("beforeend", `<li>${esc(d.message)}</li>`),
      ready: done,
    });
  } catch (e) {
    log.insertAdjacentHTML("beforeend", `<li class="err">${esc(e.message)}</li>`);
    log.insertAdjacentHTML("beforeend", `<li><button onclick="location.reload()">Retry</button></li>`);
  }
  $("#start-btn").onclick = () => go(`#chat/${id}`);
}

// ---------------------------------------------------------------- chat
let cur = null; // {id, session, state, timer}
const chatLog = $("#chat-log");

function corrHtml(c) {
  if (!c) return `<div class="corr"><span class="pending">checking…</span></div>`;
  if (c.error) return `<div class="corr"><span class="hint">correction unavailable</span></div>`;
  if (c.ok && !c.notes?.length) return `<div class="corr ok">✓ 自然です</div>`;
  return `<div class="corr fix"><div class="fixed">${esc(c.corrected)}</div><ul>${(c.notes || []).map((n) => `<li><b>${esc(n.original)}</b> → <b>${esc(n.fix)}</b>: ${esc(n.explanation)}</li>`).join("")}</ul></div>`;
}
function addRow(role, text, extra = {}) {
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;
  row.innerHTML = `<div><div class="bubble ${extra.phase === "closing" ? "closing" : ""}">${esc(text)}</div></div>${role === "user" ? corrHtml(extra.correction) : "<div></div>"}`;
  chatLog.append(row);
  chatLog.scrollTop = chatLog.scrollHeight;
  return row;
}

function renderState(st) {
  cur.state = st;
  const ph = $("#phase-label");
  ph.textContent = st.status === "ended" ? "ended" : st.phase.replace("_", " ");
  ph.className = `phase ${st.phase}`;
  let label = "", pct = st.info?.progress ?? 0;
  if (st.mode === "turns") label = `${st.turn} / ${st.budget_turns} exchanges`;
  else if (st.mode === "goal") label = `${st.checkpoints_met.length} / ${st.checkpoints.length} checkpoints`;
  if (st.status === "ended") pct = 1;
  $("#budget-label").textContent = label;
  $("#bar-fill").style.width = `${Math.round(pct * 100)}%`;
  $("#targets").innerHTML = renderTargets(st.targets) || `<span class="hint">none</span>`;
  $("#tcount").textContent = st.targets.length ? `${st.targets.filter((t) => t.used).length}/${st.targets.length}` : "";
  $("#goal-panel").hidden = st.mode !== "goal";
  $("#checkpoints").innerHTML = st.checkpoints.map((c) => `<li class="${st.checkpoints_met.includes(c) ? "done" : ""}">${esc(c)}</li>`).join("");
  const ended = st.status === "ended";
  $("#chat-form").hidden = ended;
  $("#ended-bar").hidden = !ended;
  if (ended && cur.timer) { clearInterval(cur.timer); cur.timer = null; }
}

function tickTime() {
  const st = cur?.state;
  if (!st || st.mode !== "time" || !st.started_at || st.status === "ended") return;
  const el = (Date.now() / 1000 - st.started_at) / 60;
  const m = st.budget_minutes;
  $("#budget-label").textContent = `${Math.floor(el)}:${String(Math.floor((el % 1) * 60)).padStart(2, "0")} / ${m}:00`;
  $("#bar-fill").style.width = `${Math.min(100, (el / m) * 100)}%`;
}

function setBusy(b) {
  $("#send-btn").disabled = b; $("#end-btn").disabled = b; $("#chat-input").readOnly = b;
}

async function runStream(url, body, userRow) {
  setBusy(true);
  let row = null, bubble = null;
  try {
    await stream(url, body, {
      delta: (d) => {
        if (!row) { row = addRow("assistant", ""); bubble = $(".bubble", row); bubble.classList.add("typing"); }
        bubble.textContent += d.text;
        chatLog.scrollTop = chatLog.scrollHeight;
      },
      assistant_done: (d) => { if (bubble) { bubble.classList.remove("typing"); bubble.textContent = d.text; if (d.phase === "closing") bubble.classList.add("closing"); } },
      correction: (d) => { if (userRow) userRow.lastElementChild.outerHTML = corrHtml(d.correction); },
      state: renderState,
    });
    return true;
  } catch (e) {
    row?.remove();
    if (userRow) userRow.remove();
    alert(e.message);
    return false;
  } finally { setBusy(false); $("#chat-input").focus(); }
}

async function openChat(id) {
  show("chat");
  if (cur?.timer) clearInterval(cur.timer);
  const { session: s, state } = await api(`/api/sessions/${id}`);
  if (s.status === "new") return go(`#prep/${id}`);
  cur = { id, session: s, state, timer: setInterval(tickTime, 1000) };
  chatLog.innerHTML = "";
  for (const m of s.messages) addRow(m.role, m.content, m);
  $("#side-brief").innerHTML = renderBrief(s.brief);
  $("#goal-text").textContent = s.goal;
  $("#anki-note").textContent = s.anki_note || "";
  renderState(state);
  if (s.status === "ready") await runStream(`/api/sessions/${id}/start`);
}

$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || !cur) return;
  input.value = "";
  const row = addRow("user", text);
  const ok = await runStream(`/api/sessions/${cur.id}/message`, { text }, row);
  if (!ok) input.value = text;
});
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});
$("#end-btn").addEventListener("click", async () => {
  if (cur && confirm("会話を終わりにしますか？ The partner will say goodbye.")) await runStream(`/api/sessions/${cur.id}/close`);
});
$("#review-btn").addEventListener("click", () => go(`#review/${cur.id}`));
$("#show-corr").addEventListener("change", (e) => chatLog.classList.toggle("hide-corr", !e.target.checked));

// ---------------------------------------------------------------- review
async function openReview(id) {
  show("review");
  const body = $("#review-body");
  const { session: s } = await api(`/api/sessions/${id}`);
  let r = s.review;
  if (!r) {
    body.innerHTML = `<h1>振り返り <small>Review</small></h1><p class="hint">Writing your review…</p>`;
    try { r = await api(`/api/sessions/${id}/review`, { method: "POST" }); }
    catch (e) { body.innerHTML += `<p class="hint">${esc(e.message)}</p><button onclick="location.reload()">Retry</button>`; return; }
  }
  const st = r.stats || {};
  body.innerHTML = `
    <h1>振り返り <small>${esc(s.topic)}</small></h1>
    <div class="stats"><div><b>${st.turns ?? "-"}</b>exchanges</div><div><b>${st.minutes ?? "-"}</b>minutes</div>
      <div><b>${st.messages_ok ?? 0}/${st.turns ?? 0}</b>natural messages</div><div><b>${r.targets_used.length}/${r.targets_used.length + r.targets_missed.length}</b>target words used</div></div>
    <p>${esc(r.summary)}</p>
    ${r.strengths?.length ? `<h3>良かった点 Strengths</h3><ul>${r.strengths.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    <h3>よくある間違い Recurring mistakes</h3>
    ${(r.recurring_mistakes || []).map((m) => `<div class="mistake"><b>${esc(m.pattern)}</b><ul>${m.examples.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><div class="hint">${esc(m.tip)}</div></div>`).join("") || "<p class='hint'>None 🎉</p>"}
    <h3>ターゲット語彙 Target words</h3>
    <div class="tchips">${r.targets_used.map((t) => `<span class="tchip used">${esc(t)}</span>`).join("")}${r.targets_missed.map((t) => `<span class="tchip">${esc(t)}</span>`).join("")}</div>
    <h3>次回 Focus next</h3><ul>${(r.focus_next || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    <h3>新しい単語 New words</h3>
    <table class="words"><tr><th></th><th>Word</th><th>Reading</th><th>Meaning</th><th>Example</th></tr>
      ${(r.new_words || []).map((w, i) => `<tr><td><input type="checkbox" data-i="${i}" checked></td><td>${esc(w.word)}</td><td>${esc(w.reading)}</td><td>${esc(w.meaning)}</td><td>${esc(w.example)}</td></tr>`).join("")}
    </table>
    <div class="row" style="margin-top:.8rem">
      <a href="/api/sessions/${id}/new-words.tsv"><button type="button">Download TSV</button></a>
      <label class="inline">Deck <input id="add-deck" value="LanguageHarness" style="width:14rem"></label>
      <label class="inline">Note type <input id="add-model" value="Basic" style="width:8rem"></label>
      <button type="button" class="primary" id="add-anki">Add selected to Anki</button>
      <span id="add-status" class="hint"></span>
    </div>
    <details><summary>Transcript</summary>${s.messages.map((m) => `<p><b>${m.role === "user" ? "You" : "Partner"}:</b> ${esc(m.content)}${m.correction && !m.correction.ok ? `<br><small>→ ${esc(m.correction.corrected)}</small>` : ""}</p>`).join("")}</details>
    <button class="primary big" onclick="location.hash='#setup'">新しい会話 New conversation</button>`;
  $("#add-anki").onclick = async () => {
    const words = $$("table.words input:checked").map((c) => r.new_words[+c.dataset.i]);
    try {
      const res = await api("/api/anki/add", { method: "POST", body: { deck: $("#add-deck").value, model: $("#add-model").value, words } });
      $("#add-status").textContent = `Added ${res.added}.` + (res.errors.length ? ` ${res.errors.length} skipped (${res.errors[0]})` : "");
    } catch (e) { $("#add-status").textContent = e.message; }
  };
}

// ---------------------------------------------------------------- history
async function openHistory() {
  show("history");
  const { sessions } = await api("/api/sessions");
  $("#history-list").innerHTML = sessions.map((s) => {
    const dest = s.status === "ended" ? "review" : s.status === "new" ? "prep" : "chat";
    return `<li><a href="#${dest}/${s.id}">${esc(s.topic)}</a><span class="hint">${new Date(s.created * 1000).toLocaleString()} · ${s.turns} turns · ${s.status}
      <button class="ghost" data-del="${s.id}">✕</button></span></li>`;
  }).join("") || "<li class='hint'>No sessions yet.</li>";
  $$("[data-del]").forEach((b) => (b.onclick = async () => { if (confirm("Delete this session?")) { await api(`/api/sessions/${b.dataset.del}`, { method: "DELETE" }); openHistory(); } }));
}

// ---------------------------------------------------------------- settings
const dlg = $("#settings");
let S = null;
const getK = (o, k) => k.split(".").reduce((a, p) => a?.[p], o);
const setK = (o, k, v) => { const ps = k.split("."); const last = ps.pop(); ps.reduce((a, p) => a[p], o)[last] = v; };

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
  $("#chatgpt-status").textContent = st.pending ? "Waiting for browser sign-in…" : st.connected ? `Connected${st.email ? " as " + st.email : ""}${st.planScopes ? "" : " (plan permission missing)"}` : st.error || "Not connected";
  return st;
}
$("#chatgpt-login").onclick = async () => {
  try { await api("/api/chatgpt/login", { method: "POST" }); } catch (e) { alert(e.message); }
  const poll = setInterval(async () => { const st = await refreshChatGPT(); if (!st.pending) { clearInterval(poll); loadModels("partner"); loadModels("utility"); } }, 1500);
};
$("#chatgpt-logout").onclick = async () => { await api("/api/chatgpt/logout", { method: "POST" }); refreshChatGPT(); };

$("#open-settings").onclick = async () => {
  const data = await api("/api/settings");
  S = data.settings;
  $$("[data-k]:not(.model-sel)", dlg).forEach((el) => (el.value = getK(S, el.dataset.k) ?? ""));
  for (const k of ["OPENAI_API_KEY", "TAVILY_API_KEY"]) { const el = $(`#secret-${k}`); el.value = ""; el.placeholder = data.secrets[k] ? "•••••• saved (type to replace)" : "not set"; }
  $("#anki-decks").innerHTML = S.anki.decks.map((d) => `<label><input type="checkbox" value="${esc(d)}" checked> ${esc(d)}</label>`).join("");
  $("#anki-types").innerHTML = "";
  refreshChatGPT();
  loadModels("partner"); loadModels("utility");
  api("/api/anki/collections").then(({ paths }) => ($("#collections").innerHTML = paths.map((p) => `<option value="${esc(p)}">`).join("")));
  dlg.showModal();
};

function collectSettings() {
  $$("[data-k]", dlg).forEach((el) => setK(S, el.dataset.k, el.type === "number" ? +el.value : el.value));
  S.anki.decks = $$("#anki-decks input:checked").map((c) => c.value);
  for (const box of $$(".ntype")) {
    const map = Object.fromEntries($$("select", box).map((s) => [s.dataset.f, s.value]));
    if ($("input[type=checkbox]", box).checked) S.anki.field_maps[box.dataset.name] = map;
    else delete S.anki.field_maps[box.dataset.name];
  }
}

$("#anki-load").onclick = async () => {
  collectSettings();
  await api("/api/settings", { method: "PUT", body: S }); // so the backend uses the source/path typed here
  $("#anki-status").textContent = "Loading…";
  const [d, t] = await Promise.all([api("/api/anki/decks"), api("/api/anki/note-types")]);
  if (d.error) { $("#anki-status").textContent = d.error; return; }
  $("#anki-status").textContent = `Source: ${d.source}. Tick the decks with words you know.`;
  const chosen = new Set(S.anki.decks);
  $("#anki-decks").innerHTML = d.decks.map((x) => `<label><input type="checkbox" value="${esc(x)}" ${chosen.has(x) ? "checked" : ""}> ${esc(x)}</label>`).join("");
  $("#anki-types").innerHTML = `<p class="hint">Field mapping per note type (guessed unless saved). Tick "save" to pin it.</p>` + t.types.map((nt) => {
    const sel = (f) => `<label>${f}<select data-f="${f}"><option value="">—</option>${nt.fields.map((x) => `<option ${nt.map[f] === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select></label>`;
    return `<div class="ntype" data-name="${esc(nt.name)}"><b>${esc(nt.name)}</b> <label class="inline"><input type="checkbox" ${nt.saved ? "checked" : ""}> save</label>
      <div class="row">${sel("expression")}${sel("reading")}${sel("meaning")}</div></div>`;
  }).join("");
};

$("#settings-save").onclick = async () => {
  collectSettings();
  try {
    await api("/api/settings", { method: "PUT", body: S });
    for (const k of ["OPENAI_API_KEY", "TAVILY_API_KEY"]) {
      const v = $(`#secret-${k}`).value.trim();
      if (v) await api("/api/secrets", { method: "PUT", body: { name: k, value: v } });
    }
    dlg.close();
    setupHint();
  } catch (e) { alert(e.message); }
};

route();
