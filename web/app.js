// Bandeja de soporte: a la izquierda el mensaje y cómo decide Kev, a la derecha el tablero.
// El mismo manejador sirve para la sesión en vivo (WebSocket) y para las repeticiones.

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
if (params.get("clean") === "1") document.body.classList.add("clean");

const COLS = [
  { id: "tecnico", name: "Técnico", ico: "🛠️" },
  { id: "facturacion", name: "Facturación", ico: "💳" },
  { id: "reservas", name: "Reservas", ico: "📅" },
  { id: "delivery", name: "Delivery", ico: "🛵" },
  { id: "persona", name: "Persona", ico: "🙋" },
];
const AREA_ES = { tecnico: "Técnico", facturacion: "Facturación", reservas: "Reservas", delivery: "Delivery" };
const AREA_ICO = { tecnico: "🛠️", facturacion: "💳", reservas: "📅", delivery: "🛵" };
const URG = ["baja", "media", "alta"];
const URG_LABEL = ["Baja", "Media", "Urgente"];
const AVATAR_COLORS = ["#e07a5f", "#3d8b7d", "#7b6cd9", "#d4a017", "#4f7cc4", "#c25b8a", "#6b8f3c"];

function initials(de) {
  const who = (de || "").split("·")[0].trim();
  const parts = who.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] || "?") + (parts[1]?.[0] || "")).toUpperCase();
}
function avatarColor(de) {
  let h = 0;
  for (const ch of de || "") h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

let S;
let speed = 1;
let replayTimers = [];
let ws;

function reset() {
  S = { msgs: {}, ms: [], areaOk: 0, urgOk: 0, angryOk: 0, escOk: 0, n: 0, nEval: 0, thinkStart: null };
  $("kanban").innerHTML = COLS.map((c) => `
    <section class="col ${c.id}"><header><span class="ico">${c.ico}</span><span>${c.name}</span><span class="count" id="count-${c.id}">0</span></header>
    <div class="cards" id="col-${c.id}"></div></section>`).join("");
  $("q-area").innerHTML = "";
  $("q-urg").innerHTML = "";
  setMeter("angry", null);
  setMeter("esc", null);
  $("ms").textContent = "–";
  $("v-text").textContent = "esperando…";
  $("history").innerHTML = '<li class="empty">Todavía no entró ningún mensaje.</li>';
  $("in-avatar").textContent = "·";
  $("verdict").classList.remove("persona");
  renderStats();
  document.body.dataset.done = "";
}

function bars(el, entries, best) {
  $(el).innerHTML = entries.map(([label, p, key]) => `
    <div class="bar ${key === best ? "best" : ""} u${key}">
      <span class="name">${label}</span>
      <span class="track"><span class="fill" style="width:${(p * 100).toFixed(1)}%"></span></span>
      <span class="pct">${Math.round(p * 100)}%</span>
    </div>`).join("");
}

function setMeter(which, p, on = false) {
  $(`q-${which}`).style.width = p == null ? "0" : `${(p * 100).toFixed(1)}%`;
  $(`q-${which}`).classList.toggle("on", on);
  $(`q-${which}-p`).textContent = p == null ? "–" : `${Math.round(p * 100)}%`;
}

const fmtMs = (v) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);
function pct(arr, q) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(q * s.length))];
}

function renderStats() {
  $("st-n").textContent = S.n;
  $("st-area").textContent = S.nEval ? `${S.areaOk}/${S.nEval}` : "–";
  $("st-avg").textContent = fmtMs(S.ms.length ? S.ms.reduce((a, b) => a + b, 0) / S.ms.length : null);
  const e = S.nEval;
  $("st-more").textContent = e
    ? `urgencia ${Math.round((S.urgOk / e) * 100)}% · enojo ${Math.round((S.angryOk / e) * 100)}% · escalar ${Math.round((S.escOk / e) * 100)}% · p95 ${fmtMs(pct(S.ms, 0.95))}`
    : S.n ? `sin respuestas correctas para comparar · p95 ${fmtMs(pct(S.ms, 0.95))}` : "";
}

function addCard(v, msg) {
  const col = $(`col-${v.column}`);
  const card = document.createElement("div");
  card.className = `card ${URG[v.urgencia]}`;
  card.id = `card-${v.id}`;
  const short = msg.texto.length > 110 ? msg.texto.slice(0, 108) + "…" : msg.texto;
  const tags = [`<i class="pill ${URG[v.urgencia]}">${URG_LABEL[v.urgencia]}</i>`];
  if (v.angry) tags.push(`<i class="tag angry">Enojado</i>`);
  if (v.why) tags.push(`<i class="tag persona">${v.why === "pide una persona" ? "Pide una persona" : "Kev dudó"}</i>`);
  if (v.truth && v.truth.area !== v.area) tags.push(`<i class="tag wrong" title="Kev se equivocó de área">Era ${AREA_ES[v.truth.area]}</i>`);
  const where = (msg.de.split("·")[1] || msg.de.split("·")[0] || "").trim();
  card.innerHTML = `<div class="tags">${tags.join("")}</div>${short}
    <div class="meta"><span>${where}</span><span>${fmtMs(v.ms)}</span></div>`;
  col.prepend(card);
  $(`count-${v.column}`).textContent = col.children.length;
}

function addHistory(v, msg) {
  const list = $("history");
  list.querySelector(".empty")?.remove();
  const li = document.createElement("li");
  const ico = v.column === "persona" ? "🙋" : AREA_ICO[v.area];
  li.innerHTML = `<span>${ico}</span><span class="txt">${msg.texto}</span><i class="pill ${URG[v.urgencia]}">${URG_LABEL[v.urgencia]}</i><span class="ms">${fmtMs(v.ms)}</span>`;
  list.prepend(li);
  while (list.children.length > 8) list.lastChild.remove();
}

function animateTimer() {
  if (S && S.thinkStart != null) {
    $("ms").textContent = Math.round((performance.now() - S.thinkStart) * speed);
    $("timer").classList.add("thinking");
  }
  requestAnimationFrame(animateTimer);
}

function handle(ev) {
  switch (ev.type) {
    case "init":
      reset();
      $("source-note").textContent = ev.source === "whatsapp"
        ? "Chat importado de WhatsApp · nombres reemplazados por “Contacto N” · se procesa sólo en esta máquina"
        : "Mensajes inventados para la demo · ningún dato real";
      if (!replayTimers.length) setStatus("atendiendo…");
      break;

    case "incoming": {
      S.msgs[ev.msg.id] = ev.msg;
      if (ev.source === "whatsapp_live") $("source-note").textContent = "Mensajes en vivo de WhatsApp · remitentes anonimizados · se procesan en esta máquina";
      $("in-from").textContent = ev.msg.de;
      $("in-text").textContent = ev.msg.texto;
      $("in-avatar").textContent = initials(ev.msg.de);
      $("in-avatar").style.background = avatarColor(ev.msg.de);
      const box = $("incoming");
      box.classList.remove("flash");
      void box.offsetWidth;
      box.classList.add("flash");
      $("v-text").textContent = "leyendo…";
      $("verdict").classList.remove("persona");
      break;
    }

    case "thinking":
      S.thinkStart = performance.now();
      break;

    case "verdict": {
      S.thinkStart = null;
      $("timer").classList.remove("thinking");
      $("ms").textContent = Math.round(ev.ms);
      bars("q-area", Object.keys(AREA_ES).map((k) => [AREA_ES[k], ev.area_probs[k] ?? 0, k]), ev.area);
      bars("q-urg", URG.map((u, i) => [u[0].toUpperCase() + u.slice(1), ev.urgencia_probs[i], i]), ev.urgencia);
      setMeter("angry", ev.enojado, ev.angry);
      setMeter("esc", ev.escalar, ev.column === "persona" && ev.why === "pide una persona");
      const v = $("verdict");
      if (ev.column === "persona") {
        v.classList.add("persona");
        $("v-text").innerHTML = `🙋 Una persona <span class="why">${ev.why === "pide una persona" ? "lo pide" : "Kev no está seguro"}</span><span class="ms">${fmtMs(ev.ms)}</span>`;
      } else {
        v.classList.remove("persona");
        $("v-text").innerHTML = `${AREA_ICO[ev.area]} ${AREA_ES[ev.area]} <i class="pill ${URG[ev.urgencia]}">${URG_LABEL[ev.urgencia]}</i>${ev.angry ? '<i class="tag angry">Enojado</i>' : ""}<span class="ms">${fmtMs(ev.ms)}</span>`;
      }
      S.n += 1;
      S.ms.push(ev.ms);
      if (ev.truth) {
        // sólo los mensajes de ejemplo traen la respuesta correcta
        S.nEval += 1;
        S.areaOk += ev.area === ev.truth.area;
        S.urgOk += ev.urgencia === ev.truth.urgencia;
        S.angryOk += ev.angry === ev.truth.enojado;
        S.escOk += (ev.escalar >= 0.6) === ev.truth.escalar;
      }
      addCard(ev, S.msgs[ev.id]);
      addHistory(ev, S.msgs[ev.id]);
      renderStats();
      break;
    }

    case "replied": {
      const tags = document.querySelector(`#card-${CSS.escape(ev.id)} .tags`);
      if (tags) tags.insertAdjacentHTML("beforeend", `<i class="tag replied" title="${ev.text.replace(/"/g, "&quot;")}">Respondido</i>`);
      break;
    }

    case "done":
      document.body.dataset.done = "1";
      setStatus("bandeja vacía");
      loadReplays();
      break;

    case "error":
      setStatus(ev.message, true);
      break;
  }
}

function setStatus(msg, err = false) {
  const el = $("status");
  el.textContent = msg;
  el.classList.toggle("err", err);
}

function connect() {
  return new Promise((resolve) => {
    if (ws && ws.readyState === WebSocket.OPEN) return resolve(ws);
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (m) => handle(JSON.parse(m.data));
    ws.onclose = () => setStatus("desconectado");
    ws.onopen = () => resolve(ws);
  });
}

async function startLive() {
  stopReplay();
  speed = 1;
  const st = await fetch("/api/status").then((r) => r.json());
  if (!st.kev.ok) return setStatus(`Kev no responde en ${st.kev.url}. ¿Corriste ./scripts/start.sh?`, true);
  setStatus("calentando a Kev…");
  await connect();
  ws.send(JSON.stringify({ action: "start", count: $("in-count").value, delay_ms: $("in-delay").value, shuffle: $("in-shuffle").checked, seed: Date.now() % 10000 }));
}

async function loadReplays() {
  const list = await fetch("/api/replays").then((r) => r.json()).catch(() => []);
  const sel = $("in-replay");
  const cur = sel.value;
  sel.innerHTML = '<option value="">–</option>' + list.map((r) => `<option value="${r.file}">${r.created} · ${r.messages} mensajes${r.source === "whatsapp" ? " · WhatsApp" : ""}</option>`).join("");
  if (cur) sel.value = cur;
}

function stopReplay() {
  replayTimers.forEach(clearTimeout);
  replayTimers = [];
}

async function playReplay(file, spd) {
  stopReplay();
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: "stop" }));
  speed = spd;
  const data = await fetch(`/api/replays/${encodeURIComponent(file)}`).then((r) => r.json());
  setStatus(`repetición ${spd}×`);
  for (const ev of data.events) replayTimers.push(setTimeout(() => handle(ev), ev.t / spd));
}

$("btn-start").onclick = startLive;

// ---------- mensajes propios ------------------------------------------------------------

$("composer").onsubmit = async (e) => {
  e.preventDefault();
  const texto = $("cmp-text").value.trim();
  if (!texto) return;
  stopReplay();
  speed = 1;
  await connect();
  ws.send(JSON.stringify({ action: "classify", de: $("cmp-de").value.trim() || "Vos", texto }));
  $("source-note").textContent = "Incluye mensajes tipeados a mano · se procesan sólo en esta máquina";
  $("cmp-text").value = "";
  $("cmp-text").focus();
};

$("in-wa").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  setStatus(`leyendo ${file.name}…`);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  const res = await fetch("/api/whatsapp", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ filename: file.name, data_b64: btoa(bin), limit: Number($("in-count").value) }),
  });
  const data = await res.json();
  if (!res.ok) return setStatus(data.detail || "no pude leer el chat", true);
  stopReplay();
  speed = 1;
  setStatus(`${data.count} mensajes importados (nombres reemplazados por "Contacto N")`);
  await connect();
  ws.send(JSON.stringify({ action: "start", messages: data.messages, delay_ms: $("in-delay").value }));
};
$("btn-replay").onclick = () => {
  const f = $("in-replay").value;
  if (f) playReplay(f, Number($("in-speed").value));
};

reset();
loadReplays();
requestAnimationFrame(animateTimer);
if (params.get("replay")) {
  playReplay(params.get("replay"), Number(params.get("speed") || 1));
} else {
  // Conectados desde el arranque: así aparecen los mensajes que lleguen por WhatsApp.
  connect();
  fetch("/api/status").then((r) => r.json()).then((st) => {
    if (st.whatsapp && st.whatsapp.receiving) {
      const b = $("wa-badge");
      b.textContent = st.whatsapp.auto_reply ? "WhatsApp conectado · responde solo" : "WhatsApp conectado";
      b.classList.remove("hidden");
    }
  }).catch(() => {});
}
