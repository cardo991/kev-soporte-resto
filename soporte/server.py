"""Bandeja de soporte en vivo: van entrando mensajes y Kev decide cada uno.

Los mensajes pueden ser los de ejemplo, tipeados en la pantalla, importados de un chat
exportado de WhatsApp o, si está configurado, llegar en vivo desde WhatsApp Business
(webhook en /webhook/whatsapp, ver docs/WHATSAPP.md).

    uv run uvicorn soporte.server:app --port 8002
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from collections import deque
import random
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from soporte import whatsapp, whatsapp_cloud
from soporte.kev import AREAS, CONFIG, Kev

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("soporte")
WEB = ROOT / "web"
REPLAYS = ROOT / "replays"
REPLAYS.mkdir(exist_ok=True)
KEV_URL = os.getenv("KEV_URL", "http://127.0.0.1:8009")
LANG = os.getenv("KEV_LANG", "en")

# Umbrales elegidos con los 60 mensajes de data/mensajes.jsonl (scripts/evaluar.py):
# enojado ≥ 0.6 → 92% de aciertos; escalar ≥ 0.6 → 83%. Si la confianza del área
# es menor a 0.45, Kev "duda" y el mensaje va a una persona.
ANGRY_AT = 0.6
ESCALATE_AT = 0.6
UNSURE_BELOW = 0.45


def load_messages() -> list[dict]:
    return [json.loads(l) for l in (ROOT / "data" / "mensajes.jsonl").read_text().splitlines() if l.strip()]


app = FastAPI()
app.mount("/web", StaticFiles(directory=WEB), name="web")

WA = whatsapp_cloud.Config.from_env()
SCREENS: set[WebSocket] = set()  # pantallas abiertas: ahí se muestran los mensajes que llegan por WhatsApp
SEEN_WAMIDS: deque[str] = deque(maxlen=500)  # Meta reintenta si tardamos: no procesar dos veces
WA_ALIASES: dict[str, str] = {}  # wa_id → "Contacto N" (el teléfono no se muestra ni se guarda)
_kev_shared: Kev | None = None


def shared_kev() -> Kev:
    global _kev_shared
    if _kev_shared is None:
        _kev_shared = Kev(KEV_URL, lang=LANG)
    return _kev_shared


async def broadcast(ev: dict) -> None:
    for ws in list(SCREENS):
        try:
            await ws.send_json(ev)
        except Exception:  # noqa: BLE001 — una pantalla cerrada no debe frenar a las demás
            SCREENS.discard(ws)


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/config")
async def config():
    """Las columnas de la bandeja (config/areas.json o AREAS_FILE)."""
    return {"archivo": CONFIG["_archivo"], "titulo": CONFIG.get("titulo", "Soporte"), "areas": [{k: a[k] for k in ("id", "nombre", "icono")} for a in CONFIG["areas"]]}


@app.get("/api/status")
async def status():
    try:
        r = await Kev(KEV_URL).client.get(f"{KEV_URL}/v1/models")
        kev = {"ok": r.status_code == 200, "url": KEV_URL, "model": r.json()["models"][0].get("run")}
    except Exception as e:  # noqa: BLE001 — sólo es un chequeo de salud
        kev = {"ok": False, "url": KEV_URL, "error": str(e)}
    return {"kev": kev, "whatsapp": {"receiving": WA.receiving, "auto_reply": WA.can_reply, "signed": bool(WA.app_secret)}}


# ---------- WhatsApp Business (Cloud API) ----------------------------------------------

@app.get("/webhook/whatsapp")
async def whatsapp_verify(request: Request):
    """Meta verifica el webhook una vez: hay que devolver hub.challenge tal cual (texto, no JSON)."""
    q = request.query_params
    if WA.verify_token and q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == WA.verify_token:
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "verify token inválido o WHATSAPP_VERIFY_TOKEN sin configurar")


@app.post("/webhook/whatsapp")
async def whatsapp_incoming(request: Request):
    raw = await request.body()  # la firma se calcula sobre el cuerpo crudo, antes de parsear
    if WA.app_secret:
        if not whatsapp_cloud.verify_signature(raw, request.headers.get("x-hub-signature-256"), WA.app_secret):
            raise HTTPException(401, "firma inválida")
    elif not WA.allow_unsigned:
        raise HTTPException(503, "Falta WHATSAPP_APP_SECRET en .env (ver docs/WHATSAPP.md)")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON inválido")
    for m in whatsapp_cloud.extract_messages(payload):
        if m["wamid"] in SEEN_WAMIDS:
            continue
        SEEN_WAMIDS.append(m["wamid"])
        # Respondemos 200 enseguida (Meta espera pocos segundos) y procesamos aparte.
        asyncio.create_task(process_whatsapp(m))
    return {"ok": True}


async def process_whatsapp(m: dict) -> None:
    alias = WA_ALIASES.setdefault(m["wa_id"], f"Contacto {len(WA_ALIASES) + 1}")
    who = m["name"] if WA.show_names and m["name"] else alias
    msg = {"id": f"wa-{m['wamid'][-12:]}", "de": f"{who} · WhatsApp en vivo", "texto": m["text"]}
    await broadcast({"type": "incoming", "msg": msg, "t": 0, "source": "whatsapp_live"})
    await broadcast({"type": "thinking", "id": msg["id"], "t": 0})
    try:
        v = await shared_kev().classify(msg["de"], msg["texto"])
    except Exception as e:  # noqa: BLE001
        log.warning("Kev no respondió a un mensaje de WhatsApp: %s", e)
        await broadcast({"type": "error", "message": f"Kev no respondió: {e}"})
        return
    ev = verdict_event(msg, v)
    await broadcast({**ev, "t": 0})
    if WA.can_reply:
        try:
            async with httpx.AsyncClient() as client:
                await whatsapp_cloud.send_reply(WA, client, m["wa_id"], whatsapp_cloud.reply_text(ev), reply_to=m["wamid"])
            await broadcast({"type": "replied", "id": msg["id"], "text": whatsapp_cloud.reply_text(ev), "t": 0})
        except httpx.HTTPError as e:
            log.warning("No se pudo responder por WhatsApp: %s", e)
            await broadcast({"type": "error", "message": f"No se pudo responder por WhatsApp: {e}"})


@app.get("/api/replays")
async def list_replays():
    out = []
    for f in sorted(REPLAYS.glob("*.json"), reverse=True)[:50]:
        try:
            out.append({"file": f.name, **json.loads(f.read_text())["meta"]})
        except (json.JSONDecodeError, KeyError):
            continue
    return out


@app.get("/api/replays/{name}")
async def get_replay(name: str):
    path = (REPLAYS / name).resolve()
    if path.parent != REPLAYS.resolve() or not path.exists():
        raise HTTPException(404)
    return JSONResponse(json.loads(path.read_text()))


@app.post("/api/whatsapp")
async def import_whatsapp(payload: dict = Body(...)):
    """Recibe el chat exportado de WhatsApp (.txt o .zip, en base64) y devuelve los mensajes leídos."""
    try:
        data = base64.b64decode(payload["data_b64"])
        msgs = whatsapp.parse(whatsapp.read_export(payload.get("filename", "chat.txt"), data), limit=int(payload.get("limit", 60)))
    except (KeyError, ValueError) as e:
        raise HTTPException(422, f"No pude leer el chat: {e}")
    if not msgs:
        raise HTTPException(422, "No encontré mensajes con texto. ¿Es un chat exportado de WhatsApp (sin archivos)?")
    return {"messages": msgs, "count": len(msgs)}


def route(v) -> tuple[str, str]:
    """Columna destino y motivo."""
    conf = max(v.area_probs.values())
    if v.escalar >= ESCALATE_AT:
        return "persona", "pide una persona"
    if conf < UNSURE_BELOW:
        return "persona", "Kev no está seguro"
    return v.area, ""


def verdict_event(m: dict, v) -> dict:
    col, why = route(v)
    return {
        "type": "verdict",
        "id": m["id"],
        "ms": round(v.ms, 1),
        "model_ms": v.model_ms,
        "area": v.area,
        "area_probs": v.area_probs,
        "urgencia": v.urgencia,
        "urgencia_probs": v.urgencia_probs,
        "enojado": v.enojado,
        "angry": v.enojado >= ANGRY_AT,
        "escalar": v.escalar,
        "column": col,
        "why": why,
        # Sólo los mensajes de ejemplo traen la respuesta correcta; los tipeados o importados no.
        # (y sólo si son de estas mismas columnas: con otro config/areas.json no se comparan).
        "truth": {k: m[k] for k in ("area", "urgencia", "enojado", "escalar")} if m.get("area") in AREAS else None,
    }


class Session:
    def __init__(self, ws: WebSocket, kev: Kev, count: int, delay_ms: int, shuffle: bool, seed: int, messages: list[dict] | None = None):
        self.ws = ws
        self.kev = kev
        self.source = "whatsapp" if messages else "ejemplos"
        msgs = list(messages) if messages else load_messages()
        if shuffle:
            random.Random(seed).shuffle(msgs)
        self.msgs = msgs[:count]
        self.delay = delay_ms / 1000
        self.events: list[dict] = []
        self.t0 = time.perf_counter()

    async def emit(self, ev: dict) -> None:
        ev["t"] = round((time.perf_counter() - self.t0) * 1000)
        self.events.append(ev)
        await self.ws.send_json(ev)

    async def run(self) -> None:
        await self.kev.classify("warm up", "hola")  # la primera llamada carga el modelo en la GPU
        self.t0 = time.perf_counter()
        await self.emit({"type": "init", "total": len(self.msgs), "areas": AREAS, "lang": LANG, "source": self.source})
        for m in self.msgs:
            await self.emit({"type": "incoming", "msg": {k: m[k] for k in ("id", "de", "texto")}})
            await asyncio.sleep(self.delay * 0.35)  # que se alcance a leer antes de decidir
            await self.emit({"type": "thinking", "id": m["id"]})
            v = await self.kev.classify(m["de"], m["texto"])
            await self.emit(verdict_event(m, v))
            await asyncio.sleep(self.delay)
        await self.emit({"type": "done"})
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        meta = {"created": stamp, "messages": len(self.msgs), "source": self.source}
        (REPLAYS / f"{stamp}.json").write_text(json.dumps({"meta": meta, "events": self.events}))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    SCREENS.add(ws)
    task: asyncio.Task | None = None
    kev = Kev(KEV_URL, lang=LANG)
    typed = 0
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("action") == "start":
                if task and not task.done():
                    task.cancel()
                custom = msg.get("messages")
                s = Session(ws, kev, count=len(custom) if custom else int(msg.get("count", 24)), delay_ms=int(msg.get("delay_ms", 1600)),
                            shuffle=bool(msg.get("shuffle", False)), seed=int(msg.get("seed") or 1), messages=custom)

                async def guarded(sess=s):
                    try:
                        await sess.run()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 — se informa a la UI en vez de cortar el socket
                        await ws.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})

                task = asyncio.create_task(guarded())
            elif msg.get("action") == "stop" and task:
                task.cancel()
            elif msg.get("action") == "classify":
                # Un mensaje tipeado a mano: entra a la bandeja como cualquier otro.
                texto = str(msg.get("texto", "")).strip()[:600]
                if not texto:
                    continue
                typed += 1
                m = {"id": f"yo{typed}", "de": str(msg.get("de") or "Vos").strip()[:60] + " · tipeado", "texto": texto}
                await ws.send_json({"type": "incoming", "msg": m, "t": 0})
                await ws.send_json({"type": "thinking", "id": m["id"], "t": 0})
                try:
                    v = await kev.classify(m["de"], m["texto"])
                    await ws.send_json({**verdict_event(m, v), "t": 0})
                except Exception as e:  # noqa: BLE001
                    await ws.send_json({"type": "error", "message": f"Kev no respondió: {e}"})
    except WebSocketDisconnect:
        if task:
            task.cancel()
    finally:
        SCREENS.discard(ws)
