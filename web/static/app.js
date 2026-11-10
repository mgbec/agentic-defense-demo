// Front end for the demo. No framework, no build step.
// Rule: text from the model, from attacks, or from the sink is only ever put into the page with
// textContent. No HTML string is ever parsed in this file.
"use strict";

const TOKEN = document.querySelector('meta[name="demo-token"]').content;
const $ = (id) => document.getElementById(id);

const state = {
  profiles: [],
  attacks: [],
  profile: "policy",
  info: null,
  summary: null, // the scoreboard currently shown
  pollTimer: null,
};

// ---------------------------------------------------------------- small helpers
function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.append(child);
  }
  return node;
}

function clear(node) {
  node.replaceChildren();
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (method === "POST") {
    options.headers["Content-Type"] = "application/json";
    options.headers["X-Demo-Token"] = TOKEN;
    options.body = JSON.stringify(body || {});
  }
  const response = await fetch(path, options);
  let data = {};
  try {
    data = await response.json();
  } catch (_) {
    /* leave data empty */
  }
  if (!response.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function table(headers, rows, cls = "small") {
  const head = el("tr", {}, headers.map((h) => el("th", { scope: "col", text: h })));
  const body = rows.map((row) => el("tr", {}, row.map((c) => el("td", { text: c }))));
  return el("table", { class: cls }, [el("thead", {}, head), el("tbody", {}, body)]);
}

function show(node, visible) {
  node.hidden = !visible;
}

// ---------------------------------------------------------------- tabs
const TABS = ["try", "board", "how"];

function selectTab(name) {
  for (const t of TABS) {
    const tab = $(`tab-${t}`);
    const on = t === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(`panel-${t}`).hidden = !on;
  }
}

function setupTabs() {
  for (const t of TABS) {
    $(`tab-${t}`).addEventListener("click", () => selectTab(t));
    $(`tab-${t}`).addEventListener("keydown", (event) => {
      const i = TABS.indexOf(t);
      let next = null;
      if (event.key === "ArrowRight") next = TABS[(i + 1) % TABS.length];
      if (event.key === "ArrowLeft") next = TABS[(i + TABS.length - 1) % TABS.length];
      if (next) {
        event.preventDefault();
        selectTab(next);
        $(`tab-${next}`).focus();
      }
    });
  }
}

// ---------------------------------------------------------------- Try it
function renderProfileCards() {
  const holder = $("profile-cards");
  clear(holder);
  for (const p of state.profiles) {
    const input = el("input", { type: "radio", name: "profile", value: p.id });
    input.checked = p.id === state.profile;
    input.addEventListener("change", () => {
      state.profile = p.id;
      renderProfileCards();
    });
    const card = el("label", { class: "card" + (p.id === state.profile ? " selected" : "") }, [
      input,
      el("span", { class: "name", text: p.label }),
      el("span", { class: "desc", text: p.description }),
    ]);
    holder.append(card);
  }
}

function fillAttackPicker() {
  const select = $("attack-pick");
  const groups = new Map();
  for (const a of state.attacks) {
    if (!groups.has(a.category)) groups.set(a.category, []);
    groups.get(a.category).push(a);
  }
  for (const [category, items] of groups) {
    const group = el("optgroup", { label: category });
    for (const a of items) group.append(el("option", { value: a.id, text: `${a.title} [${a.kind}]` }));
    select.append(group);
  }
  select.addEventListener("change", () => {
    const a = state.attacks.find((x) => x.id === select.value);
    $("attack-explain").textContent = a ? a.explanation : "";
    if (a) $("prompt").value = a.prompt;
  });
}

function renderResult(data) {
  // Chips: which layers stepped in
  const chips = $("chips");
  clear(chips);
  if (data.error) {
    chips.append(el("span", { class: "chip error", text: "agent error" }));
  } else if ((data.interventions || []).length) {
    for (const layer of data.interventions) {
      chips.append(el("span", { class: "chip stopped", text: `${layer} stepped in` }));
    }
  } else {
    chips.append(el("span", { class: "chip nothing", text: "no layer stepped in" }));
  }

  // Show why a request failed, not just that it did.
  const shown = data.error
    ? [`Error: ${data.error}`, data.response].filter(Boolean).join("\n\n")
    : data.response || "(empty reply)";
  $("reply").textContent = shown;

  const timeline = $("timeline");
  clear(timeline);
  for (const ev of data.trace || []) {
    timeline.append(
      el("li", {}, [
        el("strong", { text: ev.layer }),
        el("span", { class: `decision ${ev.decision}`, text: ev.decision }),
        el("span", { text: ev.detail || "" }),
      ])
    );
  }
  if (!(data.trace || []).length) timeline.append(el("li", { text: "(no events recorded)" }));

  const tools = $("tools");
  clear(tools);
  const calls = data.tool_calls || [];
  if (!calls.length) {
    tools.append(el("p", { class: "muted", text: "The agent called no tools." }));
  } else {
    tools.append(
      table(
        ["Tool", "Arguments", "Status", "Result"],
        calls.map((c) => [c.name, JSON.stringify(c.args), c.status, c.summary || ""])
      )
    );
  }
  if (data.elapsed_ms != null) {
    $("chat-status").textContent = `Done in ${(data.elapsed_ms / 1000).toFixed(1)} s.`;
  }
}

async function sendChat() {
  const prompt = $("prompt").value.trim();
  if (!prompt) {
    $("chat-status").textContent = "Write a message first.";
    return;
  }
  $("send").disabled = true;
  $("chat-status").textContent = "Waiting for the agent...";
  try {
    renderResult(await api("POST", "/api/chat", { prompt, profile: state.profile }));
    refreshSink();
  } catch (err) {
    $("chat-status").textContent = `Error: ${err.message}`;
  } finally {
    $("send").disabled = false;
  }
}

function sinkTable(rows) {
  if (!rows.length) return el("p", { class: "muted", text: "Nothing recorded." });
  const skip = new Set(["pk", "sk", "kind", "key"]);
  const columns = [];
  for (const row of rows) for (const k of Object.keys(row)) if (!skip.has(k) && !columns.includes(k)) columns.push(k);
  const cell = (k, v) => {
    if (v == null) return "";
    if (k === "ts" && typeof v === "number") return new Date(v).toLocaleTimeString();
    return typeof v === "object" ? JSON.stringify(v) : String(v);
  };
  return table(columns, rows.map((r) => columns.map((k) => cell(k, r[k]))));
}

async function refreshSink() {
  try {
    const data = await api("GET", "/api/sink");
    const outbox = $("sink-outbox");
    const refunds = $("sink-refunds");
    clear(outbox);
    clear(refunds);
    outbox.append(sinkTable(data.outbox || []));
    refunds.append(sinkTable(data.refunds || []));
    $("sink-status").textContent = "";
  } catch (err) {
    $("sink-status").textContent = `Could not read the sink: ${err.message}`;
  }
}

async function clearSink() {
  try {
    const data = await api("POST", "/api/sink/clear");
    $("sink-status").textContent = `Cleared ${data.cleared} row(s).`;
    const outbox = $("sink-outbox");
    const refunds = $("sink-refunds");
    clear(outbox);
    clear(refunds);
    outbox.append(sinkTable([]));
    refunds.append(sinkTable([]));
  } catch (err) {
    $("sink-status").textContent = `Error: ${err.message}`;
  }
}

// ---------------------------------------------------------------- Scoreboard
function pct(rate) {
  return rate == null ? "n/a" : `${Math.round(rate * 100)}%`;
}

// Returns [css class, word]. Words are shown too, so color is never the only signal.
function judge(kind, cell) {
  if (!cell) return ["empty", ""];
  if (cell.valid === 0) return ["empty", "no valid runs"];
  const rate = cell.outcomes / cell.valid;
  if (kind === "benign") {
    if (rate >= 0.85) return ["good", "completed"];
    if (rate <= 0.5) return ["bad", "blocked too much"];
    return ["mid", "partly completed"];
  }
  if (rate === 0) return ["good", "held"];
  if (rate >= 0.5) return ["bad", "harm"];
  return ["mid", "some harm"];
}

function renderMatrix(summary) {
  const matrix = $("matrix");
  clear(matrix);
  const labelOf = (id) => (state.profiles.find((p) => p.id === id) || { label: id }).label;

  const head = el("tr", {}, [
    el("th", { scope: "col", text: "Attack" }),
    el("th", { scope: "col", text: "Kind" }),
    ...summary.profiles.map((p) => el("th", { scope: "col", text: labelOf(p) })),
    el("th", { scope: "col", text: "Expected to stop at" }),
  ]);
  matrix.append(el("thead", {}, head));

  const body = el("tbody");
  for (const attack of summary.attacks) {
    const row = el("tr", {}, [
      el("th", { scope: "row", text: attack.title }),
      el("td", { class: "kind", text: attack.kind }),
    ]);
    for (const p of summary.profiles) {
      const cell = (summary.cells[attack.id] || {})[p];
      const [cls, word] = judge(attack.kind, cell);
      const td = el("td", { class: `cell ${cls}` });
      if (cell) {
        td.append(el("span", { class: "cellmain", text: `${cell.outcomes}/${cell.valid}` }));
        const sub = [word, cell.errors ? `${cell.errors} error(s)` : ""].filter(Boolean).join(", ");
        if (sub) td.append(el("span", { class: "cellsub", text: sub }));
        td.tabIndex = 0;
        td.setAttribute("role", "button");
        td.setAttribute("aria-label", `${attack.title}, ${labelOf(p)}: ${cell.outcomes} of ${cell.valid}. ${sub}. Show details`);
        const open = () => showDetail(summary, attack, p);
        td.addEventListener("click", open);
        td.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open();
          }
        });
      } else {
        td.append(el("span", { class: "cellsub", text: "not run" }));
      }
      row.append(td);
    }
    row.append(el("td", { class: "hyp", text: attack.hypothesis || "" }));
    body.append(row);
  }

  const kinds = [
    ["attack", "Attacks that succeeded"],
    ["control", "Controls that succeeded"],
    ["benign", "Benign tasks completed"],
  ];
  for (const [kind, label] of kinds) {
    const row = el("tr", { class: "totals" }, [el("th", { scope: "row", text: label }), el("td")]);
    for (const p of summary.profiles) {
      const t = summary.totals[p].by_kind[kind];
      row.append(el("td", { text: t.valid ? `${pct(t.rate)} (${t.outcomes}/${t.valid})` : "n/a" }));
    }
    row.append(el("td"));
    body.append(row);
  }
  matrix.append(body);

  // What stepped in
  const stepped = $("stepped");
  clear(stepped);
  for (const p of summary.profiles) {
    const counts = Object.entries(summary.totals[p].stopped_by).sort((a, b) => b[1] - a[1]);
    const chips = counts.length
      ? counts.map(([layer, n]) => el("span", { class: "chip stopped", text: `${layer} x${n}` }))
      : [el("span", { class: "muted", text: "(nothing recorded)" })];
    stepped.append(el("div", { class: "row" }, [el("strong", { text: `${labelOf(p)}:` }), ...chips]));
  }
}

function showDetail(summary, attack, profile) {
  const box = $("detail");
  clear(box);
  const cell = (summary.cells[attack.id] || {})[profile];
  box.append(el("h3", { text: `${attack.title} against "${profile}"` }));
  box.append(el("p", { text: attack.explanation || "" }));
  box.append(el("p", { class: "muted", text: `Prompt: ${attack.prompt}` }));
  if (attack.hypothesis) box.append(el("p", { class: "muted", text: `Prediction written before the run: ${attack.hypothesis}` }));
  if (!cell) return;
  const stopped = Object.entries(cell.stopped_by || {}).map(([k, v]) => `${k} x${v}`).join(", ");
  if (stopped) box.append(el("p", { text: `When harm did not happen, what stepped in: ${stopped}` }));
  for (const s of cell.samples || []) {
    let verdict;
    if (s.outcome === null) verdict = el("span", { class: "tag-error", text: "run failed (not counted)" });
    else if (attack.kind === "benign") verdict = el("span", { text: s.outcome ? "task completed" : "task not completed" });
    else verdict = el("span", { class: s.outcome ? "tag-error" : "tag-denied", text: s.outcome ? "harm happened" : "no harm" });
    const sample = el("div", { class: "sample" }, [
      el("strong", { text: `Sample ${s.repeat + 1}: ` }),
      verdict,
      el("p", { text: s.error ? `Error: ${s.error}` : `Reply: ${s.response || "(empty)"}` }),
    ]);
    if ((s.interventions || []).length) sample.append(el("p", { class: "muted", text: `Stepped in: ${s.interventions.join(", ")}` }));
    if ((s.tool_calls || []).length) {
      sample.append(el("p", { class: "muted", text: `Tools: ${s.tool_calls.map((c) => `${c.name} (${c.status})`).join(", ")}` }));
    }
    box.append(sample);
  }
  box.append(el("p", { class: "muted", text: "Up to 3 samples are kept per cell." }));
  box.focus();
}

function renderSummary(summary) {
  state.summary = summary;
  show($("board"), true);
  const note = $("note");
  note.textContent = summary.note || "";
  show(note, Boolean(summary.note));
  const warn = $("warn");
  if (summary.errors) {
    warn.textContent = `${summary.errors} run(s) failed and are left out of the rates. Look at the cells marked with errors before trusting this scoreboard.`;
  }
  show(warn, Boolean(summary.errors));
  clear($("detail"));
  renderMatrix(summary);
}

function selectedProfiles() {
  return [...document.querySelectorAll('#profile-checks input:checked')].map((i) => i.value);
}

function renderProfileChecks() {
  const holder = $("profile-checks");
  clear(holder);
  for (const p of state.profiles) {
    const input = el("input", { type: "checkbox", value: p.id });
    input.checked = true;
    holder.append(el("label", { class: "inline" }, [input, ` ${p.label}`]));
  }
}

function setRunning(running) {
  $("run").disabled = running;
  show($("stop"), running);
  show($("progress-wrap"), running);
}

async function pollRun() {
  clearTimeout(state.pollTimer);
  try {
    const s = await api("GET", "/api/run");
    const pctDone = s.total ? Math.round((100 * s.done) / s.total) : 0;
    $("progress").value = pctDone;
    $("progress-text").textContent = ` ${s.done} of ${s.total} runs`;
    if (s.summary) renderSummary(s.summary);
    if (s.state === "running") {
      setRunning(true);
      state.pollTimer = setTimeout(pollRun, 1500);
      return;
    }
    setRunning(false);
    const messages = {
      done: `Finished. Saved as ${s.saved_as}.`,
      stopped: `Stopped early. Partial results saved as ${s.saved_as}.`,
      failed: `The run failed: ${s.error}`,
    };
    if (messages[s.state]) {
      $("board-msg").textContent = messages[s.state];
      refreshSaved();
    }
  } catch (err) {
    $("board-msg").textContent = `Lost contact with the server: ${err.message}`;
    setRunning(false);
  }
}

async function startRun() {
  const profiles = selectedProfiles();
  if (!profiles.length) {
    $("board-msg").textContent = "Choose at least one profile.";
    return;
  }
  const repeats = Number.parseInt($("repeats").value, 10);
  if (!Number.isInteger(repeats) || repeats < 1 || repeats > 20) {
    $("board-msg").textContent = "Repeats must be a whole number from 1 to 20.";
    return;
  }
  const quick = document.querySelector('input[name="scope"]:checked').value === "quick";
  $("board-msg").textContent = "";
  try {
    await api("POST", "/api/run", { quick, profiles, repeats });
    setRunning(true);
    $("progress").value = 0;
    $("progress-text").textContent = " starting...";
    pollRun();
  } catch (err) {
    $("board-msg").textContent = `Could not start: ${err.message}`;
  }
}

async function refreshSaved() {
  const select = $("saved");
  const data = await api("GET", "/api/results");
  const keep = select.value;
  clear(select);
  select.append(el("option", { value: "", text: "(choose)" }));
  for (const r of data.results) select.append(el("option", { value: r.name, text: r.label }));
  select.value = keep;
}

async function openSaved() {
  const name = $("saved").value;
  if (!name) return;
  try {
    const data = await api("GET", `/api/results/${encodeURIComponent(name)}`);
    $("board-msg").textContent = "";
    renderSummary(data.summary);
  } catch (err) {
    $("board-msg").textContent = `Could not open that run: ${err.message}`;
  }
}

// ---------------------------------------------------------------- start up
async function init() {
  setupTabs();
  $("send").addEventListener("click", sendChat);
  $("sink-refresh").addEventListener("click", refreshSink);
  $("sink-clear").addEventListener("click", clearSink);
  $("run").addEventListener("click", startRun);
  $("stop").addEventListener("click", () => api("POST", "/api/run/stop").catch(() => {}));
  $("load").addEventListener("click", openSaved);

  try {
    const [status, lib] = await Promise.all([api("GET", "/api/status"), api("GET", "/api/attacks")]);
    state.info = status;
    state.profiles = lib.profiles;
    state.attacks = lib.attacks;
  } catch (err) {
    $("mode").textContent = `Could not reach the server: ${err.message}`;
    return;
  }

  $("mode").textContent = state.info.offline
    ? "Offline mode: no AWS. You can open saved scoreboards but cannot send messages or run attacks."
    : `Connected to AWS (${state.info.region}), runtime ${state.info.runtime}`;
  if (state.info.offline) {
    $("send").disabled = true;
    $("run").disabled = true;
    $("chat-status").textContent = "Offline mode: sending is disabled.";
  }

  renderProfileCards();
  renderProfileChecks();
  fillAttackPicker();
  $("sink-outbox").append(sinkTable([]));
  $("sink-refunds").append(sinkTable([]));
  await refreshSaved();
  if (!state.info.offline) refreshSink();
  if (state.info.busy) {
    selectTab("board");
    pollRun();
  }
}

init();
