"use strict";
// standbild Web-UI: eine Seite, kein Build-Schritt. Inhalte werden nur über textContent gesetzt (kein innerHTML).

const $ = (sel) => document.querySelector(sel);
const h = (tag, props = {}, ...kids) => {
  const el = Object.assign(document.createElement(tag), props);
  el.append(...kids.filter((k) => k != null));
  return el;
};
const fmtDate = (iso) => new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
const fmtSize = (bytes) => (bytes / 1048576).toLocaleString("de-DE", { maximumFractionDigits: 1 }) + " MB";
const fmtTime = (sec) => new Date(Math.max(0, sec) * 1000).toISOString().slice(11, 19);
const STATUS = { queued: "wartet", running: "läuft", done: "fertig", failed: "fehlgeschlagen" };
const STAGE = { download: "Download", transcribe: "Transkription", detect: "Bildwechsel-Erkennung", assemble: "Zusammenführen", store: "Ablegen" };

async function api(path, method, body) {
  const res = await fetch(path, { method, headers: body && { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.detail || `Fehler ${res.status}`), { errors: data.errors });
  return data;
}

// --- Parameter (Tabelle 4.3) -----------------------------------------------------------------
let cfg; // Antwort von /api/defaults
let prefilledFor = null; // Video, für das die Felder zuletzt vorbelegt wurden
const field = (key) => $("#p-" + key);

function buildFields() {
  const box = $("#fields");
  box.replaceChildren();
  for (const [key, f] of Object.entries(cfg.fields)) {
    let input;
    if (key === "profile" || key === "whisper_model") {
      input = h("select", {}, ...(key === "profile" ? f.enum : cfg.models).map((v) => h("option", { value: v, textContent: v })));
    } else if (key === "masks") {
      input = h("textarea", { rows: 2, placeholder: "0.78, 0.72, 0.22, 0.28" });
    } else if (f.type === "boolean") {
      input = h("input", { type: "checkbox" });
    } else if (f.type === "integer" || f.type === "number") {
      input = h("input", { type: "number", min: f.minimum, max: f.maximum, step: f.type === "integer" ? 1 : "any", required: true });
    } else {
      input = h("input", { type: "text", required: true });
      if (f.pattern) input.pattern = f.pattern.replace(/^\^|\$$/g, "");
    }
    input.id = "p-" + key;
    const error = h("p", { className: "error", id: "e-" + key });
    input.setAttribute("aria-describedby", `h-${key} e-${key}`);
    input.addEventListener("invalid", () => (error.textContent = input.validationMessage));
    input.addEventListener("input", () => {
      input.setCustomValidity("");
      error.textContent = "";
    });
    const range = f.minimum !== undefined ? ` (${f.minimum}–${f.maximum})` : "";
    box.append(h("div", {}, h("label", { htmlFor: input.id, textContent: key }), input,
      h("p", { className: "hint", id: "h-" + key, textContent: f.description + range }), error));
  }
  field("profile").addEventListener("change", () => setFields(cfg.profiles[field("profile").value]));
}

function setFields(values) {
  for (const [key, value] of Object.entries(values)) {
    const input = field(key);
    if (!input) continue;
    if (input.type === "checkbox") input.checked = value;
    else if (key === "masks") input.value = value.map((m) => m.join(", ")).join("\n");
    else input.value = value;
    input.setCustomValidity("");
    $("#e-" + key).textContent = "";
  }
}

function readFields() {
  const out = {};
  for (const key of Object.keys(cfg.fields)) {
    const input = field(key);
    if (input.type === "checkbox") out[key] = input.checked;
    else if (input.type === "number") out[key] = input.valueAsNumber;
    else if (key === "masks") {
      out[key] = input.value.split("\n").filter((l) => l.trim()).map((l) => l.split(/[,;\s]+/).filter(Boolean).map(Number));
      const ok = out[key].every((m) => m.length === 4 && m.every((n) => n >= 0 && n <= 1));
      input.setCustomValidity(ok ? "" : "Pro Zeile vier Zahlen zwischen 0 und 1: x, y, w, h");
    } else out[key] = input.value;
  }
  return out;
}

function prefill(v) {
  if (prefilledFor === v.youtube_id) return; // Eingaben des Nutzers beim Aktualisieren nicht überschreiben
  prefilledFor = v.youtube_id;
  setFields(v.last_options || cfg.defaults);
  $("#params-note").textContent = !v.last_options ? ""
    : `Parameter vom ${fmtDate(v.last_options_date)} übernommen.` +
      (v.model_replaced ? " Das damals verwendete Modell ist nicht mehr installiert; das Standardmodell wurde gesetzt." : "");
}

// --- Karte zum eingefügten Link (10.1) ------------------------------------------------------
let pollTimer;
let lastState;

async function start(force) {
  const options = readFields();
  if (!$("#params-form").checkValidity()) {
    $("#params").open = true;
    $("#params-form").querySelector(":invalid").focus();
    return;
  }
  try {
    await api("/api/jobs", "POST", { url: $("#url").value.trim(), force, options });
    lookup();
  } catch (err) {
    if (!err.errors) return showError(err.message);
    $("#params").open = true;
    for (const [key, msg] of Object.entries(err.errors)) {
      if (field(key)) field(key).setCustomValidity(msg);
    }
    $("#params-form").checkValidity();
    $("#params-form").querySelector(":invalid")?.focus();
  }
}

function showError(message) {
  $("#card").append(h("p", { className: "error", role: "alert", textContent: message }));
}

function render(v) {
  const card = $("#card");
  const actions = h("div", { className: "actions" });
  const again = (label, force) => h("button", { type: "button", textContent: label, onclick: () => start(force) });
  const head = [
    v.thumbnail && h("img", { src: v.thumbnail, alt: "", width: 160 }),
    h("h3", { textContent: v.title || (v.youtube_id ? `Video ${v.youtube_id}` : "") }),
  ];
  card.className = "card";
  if (v.state === "invalid") {
    card.replaceChildren(h("p", { className: "error", textContent: v.error }));
  } else if (v.state === "unknown") {
    actions.append(again("Verarbeiten", false));
    card.replaceChildren(...head, h("p", { className: "note", textContent: `Video-ID ${v.youtube_id} · noch nicht verarbeitet` }), actions);
  } else if (v.state === "processing") {
    const j = v.job;
    const stage = j.status === "queued" ? "In der Warteschlange" : STAGE[j.stage] || j.stage || "";
    const runtime = fmtTime((Date.now() - new Date(j.created_at)) / 1000);
    card.replaceChildren(...head, h("p", { textContent: `${stage} · Laufzeit ${runtime}` }),
      h("progress", { max: 1, value: j.progress, ariaLabel: `Fortschritt ${stage}` }));
    pollTimer = setTimeout(lookup, 2000);
  } else if (v.state === "done") {
    const j = v.job;
    const facts = [["Kanal", v.channel], ["Dauer", v.duration && fmtTime(v.duration)], ["Verarbeitet am", fmtDate(j.finished_at)],
      ["Screenshots", j.screenshot_count], ["Paketgröße", fmtSize(j.package_size)]];
    const dl = h("dl", {}, ...facts.filter(([, val]) => val != null).flatMap(([k, val]) => [h("dt", { textContent: k }), h("dd", { textContent: val })]));
    const used = h("details", {}, h("summary", { textContent: "Verwendete Parameter" }),
      h("pre", { textContent: Object.entries(v.options).map(([k, val]) => `${k}: ${JSON.stringify(val)}`).join("\n") }));
    actions.append(h("a", { className: "button", href: `/api/jobs/${j.job_id}/package`, textContent: "Herunterladen" }),
      Object.assign(again("Erneut verarbeiten", true), { className: "secondary" }));
    card.replaceChildren(...head, dl, used, actions);
  } else {
    const message = v.state === "missing" ? "Paket fehlt – die ZIP-Datei liegt nicht mehr im Volume." : v.job.error || "Verarbeitung fehlgeschlagen.";
    actions.append(again("Erneut verarbeiten", true));
    card.replaceChildren(...head, h("p", { className: "error", textContent: message }), actions);
  }
  const startable = ["unknown", "done", "failed", "missing"].includes(v.state);
  $("#params").hidden = !startable;
  if (startable) prefill(v);
  const state = v.state + (v.job ? v.job.status : "");
  if (state !== lastState) loadRecent();
  lastState = state;
}

async function lookup() {
  clearTimeout(pollTimer);
  const url = $("#url").value.trim();
  if (!url) {
    $("#card").replaceChildren();
    $("#card").className = "";
    $("#params").hidden = true;
    return;
  }
  try {
    const v = await api("/api/videos/lookup?url=" + encodeURIComponent(url));
    if (url === $("#url").value.trim()) render(v); // veraltete Antworten verwerfen
  } catch (err) {
    $("#card").replaceChildren();
    showError(err.message);
  }
}

async function loadRecent() {
  const jobs = await api("/api/jobs?limit=20");
  $("#recent tbody").replaceChildren(...jobs.map((j) => h("tr", {},
    h("td", {}, h("button", { type: "button", className: "link", textContent: j.title || j.youtube_id, onclick: () => {
      $("#url").value = "https://www.youtube.com/watch?v=" + j.youtube_id;
      lookup();
      window.scrollTo({ top: 0 });
    } })),
    h("td", { textContent: STATUS[j.status] }),
    h("td", { textContent: fmtDate(j.created_at) }),
    h("td", {}, j.has_package ? h("a", { href: `/api/jobs/${j.job_id}/package`, textContent: "ZIP" }) : null))));
}

// --- Modelle (10.3) ----------------------------------------------------------------------------
let modelTimer;

async function loadModels() {
  clearTimeout(modelTimer);
  const list = await api("/api/models");
  const act = (label, fn) => h("button", { type: "button", className: "secondary", textContent: label, onclick: async () => {
    $("#model-error").textContent = "";
    try { await fn(); } catch (err) { $("#model-error").textContent = err.message; }
    await refreshConfig();
    loadModels();
  } });
  $("#model-list tbody").replaceChildren(...list.map((m) => {
    const status = m.status === "ready" ? "bereit" : m.status === "error" ? m.error : `lädt … ${Math.round(m.progress * 100)} %`;
    const name = encodeURIComponent(m.name);
    return h("tr", {},
      h("td", {}, m.name, m.default ? h("span", { className: "badge", textContent: "Standard" }) : null),
      h("td", { textContent: m.source }),
      h("td", { textContent: m.size ? fmtSize(m.size) : "" }),
      h("td", { textContent: status, className: m.status === "error" ? "error" : "" }),
      h("td", {},
        m.status === "ready" && !m.default ? act("Als Standard setzen", () => api(`/api/models/${name}/default`, "PUT")) : null,
        m.status !== "downloading" && !m.default ? act("Entfernen", () => api(`/api/models/${name}`, "DELETE")) : null));
  }));
  if (list.some((m) => m.status === "downloading")) modelTimer = setTimeout(loadModels, 2000);
  else if (cfg && list.filter((m) => m.status === "ready").length !== cfg.models.length) await refreshConfig();
}

async function refreshConfig() {
  const values = cfg && readFields();
  cfg = await api("/api/defaults");
  buildFields();
  setFields({ ...cfg.defaults, ...(values && cfg.models.includes(values.whisper_model) ? values : {}) });
}

$("#model-url").addEventListener("input", () => {
  const repo = $("#model-url").value.trim().replace(/^https?:\/\/huggingface\.co\//, "").split("/")[1] || "";
  if (!$("#model-name").dataset.edited) $("#model-name").value = repo.replace(/^faster-whisper-/, "");
});
$("#model-name").addEventListener("input", () => ($("#model-name").dataset.edited = $("#model-name").value ? "1" : ""));
$("#model-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#model-error").textContent = "";
  try {
    await api("/api/models", "POST", { url: $("#model-url").value, name: $("#model-name").value.trim() || null });
    event.target.reset();
    delete $("#model-name").dataset.edited;
  } catch (err) {
    $("#model-error").textContent = err.message;
  }
  loadModels();
});

// --- Start ----------------------------------------------------------------------------------
for (const tab of document.querySelectorAll("[role=tab]")) {
  tab.addEventListener("click", () => {
    for (const t of document.querySelectorAll("[role=tab]")) {
      t.setAttribute("aria-selected", String(t === tab));
      $("#" + t.dataset.tab).hidden = t !== tab;
    }
    if (tab.dataset.tab === "models") loadModels();
  });
}
let debounce;
$("#url").addEventListener("input", () => {
  clearTimeout(debounce);
  debounce = setTimeout(lookup, 300);
});
$("#reset").addEventListener("click", () => {
  setFields(cfg.defaults);
  $("#params-note").textContent = "";
});
refreshConfig().then(() => {
  loadRecent();
  const linked = new URLSearchParams(location.search).get("url"); // /?url=… öffnet die Karte direkt
  if (linked) {
    $("#url").value = linked;
    lookup();
  }
});
