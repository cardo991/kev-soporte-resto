"""Bandeja de soporte en vivo: van entrando mensajes inventados y Kev decide cada uno.

    uv run uvicorn soporte.server:app --port 8002
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from soporte.kev import AREAS, Kev

ROOT = Path(__file__).resolve().parent.parent
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


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/status")
async def status():
    try:
        r = await Kev(KEV_URL).client.get(f"{KEV_URL}/v1/models")
        return {"kev": {"ok": r.status_code == 200, "url": KEV_URL, "model": r.json()["models"][0].get("run")}}
    except Exception as e:  # noqa: BLE001 — sólo es un chequeo de salud
        return {"kev": {"ok": False, "url": KEV_URL, "error": str(e)}}


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


def route(v) -> tuple[str, str]:
    """Columna destino y motivo."""
    conf = max(v.area_probs.values())
    if v.escalar >= ESCALATE_AT:
        return "persona", "pide una persona"
    if conf < UNSURE_BELOW:
        return "persona", "Kev no está seguro"
    return v.area, ""


class Session:
    def __init__(self, ws: WebSocket, count: int, delay_ms: int, shuffle: bool, seed: int):
        self.ws = ws
        self.kev = Kev(KEV_URL, lang=LANG)
        msgs = load_messages()
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
        await self.emit({"type": "init", "total": len(self.msgs), "areas": AREAS, "lang": LANG})
        for m in self.msgs:
            await self.emit({"type": "incoming", "msg": {k: m[k] for k in ("id", "de", "texto")}})
            await asyncio.sleep(self.delay * 0.35)  # que se alcance a leer antes de decidir
            await self.emit({"type": "thinking", "id": m["id"]})
            v = await self.kev.classify(m["de"], m["texto"])
            col, why = route(v)
            await self.emit({
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
                "truth": {k: m[k] for k in ("area", "urgencia", "enojado", "escalar")},
            })
            await asyncio.sleep(self.delay)
        await self.emit({"type": "done"})
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        meta = {"created": stamp, "messages": len(self.msgs)}
        (REPLAYS / f"{stamp}.json").write_text(json.dumps({"meta": meta, "events": self.events}))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    task: asyncio.Task | None = None
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("action") == "start":
                if task and not task.done():
                    task.cancel()
                s = Session(ws, count=int(msg.get("count", 24)), delay_ms=int(msg.get("delay_ms", 1600)),
                            shuffle=bool(msg.get("shuffle", False)), seed=int(msg.get("seed") or 1))

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
    except WebSocketDisconnect:
        if task:
            task.cancel()
