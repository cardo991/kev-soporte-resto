"""Cliente de Kev para clasificar mensajes de soporte de restaurantes.

Una sola llamada a `POST /v1/systemone` con cuatro preguntas (área, urgencia, enojo y si
hay que escalar a una persona). Kev las responde todas en una pasada, sin generar texto.
Los mensajes llegan en español; las preguntas pueden ir en inglés (el idioma en que se
entrenó Kev) o en español: `lang` elige cuál.

Las áreas (las columnas de la bandeja) salen de `config/areas.json`, o del archivo que diga
la variable `AREAS_FILE`: para cambiar las columnas no hace falta tocar código.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # antes de leer AREAS_FILE


def load_config(path: str | Path | None = None) -> dict:
    """Lee y valida el archivo de áreas. Falla con un mensaje claro si algo está mal."""
    path = Path(path or os.getenv("AREAS_FILE") or ROOT / "config" / "areas.json")
    if not path.is_absolute():
        path = ROOT / path
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"No encuentro el archivo de áreas: {path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name} no es un JSON válido (línea {e.lineno}): {e.msg}")
    areas = cfg.get("areas")
    if not cfg.get("pregunta") or not isinstance(areas, list) or len(areas) < 2:
        raise ValueError(f"{path.name}: hacen falta 'pregunta' y al menos 2 'areas'")
    seen = set()
    for a in areas:
        aid = a.get("id", "")
        if not re.fullmatch(r"[a-z0-9_]+", aid):
            raise ValueError(f"{path.name}: id inválido {aid!r} (minúsculas, números y _, sin espacios)")
        if aid == "persona" or aid in seen:
            raise ValueError(f"{path.name}: id repetido o reservado: {aid!r}")
        if not a.get("descripcion"):
            raise ValueError(f"{path.name}: al área {aid!r} le falta la 'descripcion'")
        seen.add(aid)
        a.setdefault("nombre", aid.capitalize())
        a.setdefault("icono", "📌")
    cfg["_archivo"] = path.name
    return cfg


CONFIG = load_config()
AREAS = [a["id"] for a in CONFIG["areas"]]
AREA_NAMES = {a["id"]: a["nombre"] for a in CONFIG["areas"]}
URGENCIA = ["baja", "media", "alta"]

QUESTIONS = {
    "en": {
        "area": {
            "instructions": CONFIG["pregunta"],
            "criteria": {a["id"]: a["descripcion"] for a in CONFIG["areas"]},
        },
        "urgencia": {
            "instructions": "How urgent is it?",
            "criteria": [
                "Low: a question or a change for later, nothing is broken right now",
                "Medium: something is wrong but they can keep working",
                "High: it is stopping service right now (customers waiting, orders or payments not going through)",
            ],
        },
        "enojado": {"instructions": "Is the sender angry or upset?"},
        "escalar": {
            "instructions": "Should a human take over (refund request, threat to cancel or complain, asks for a manager, repeated unsolved problem, or the message is too vague to act on)?"
        },
    },
    "es": {
        "area": {
            "instructions": CONFIG.get("pregunta_es") or CONFIG["pregunta"],
            "criteria": {a["id"]: a.get("descripcion_es") or a["descripcion"] for a in CONFIG["areas"]},
        },
        "urgencia": {
            "instructions": "¿Qué tan urgente es?",
            "criteria": [
                "Baja: una consulta o un cambio para más adelante, no hay nada roto ahora",
                "Media: algo anda mal pero pueden seguir trabajando",
                "Alta: está frenando el servicio ahora (clientes esperando, pedidos o cobros que no salen)",
            ],
        },
        "enojado": {"instructions": "¿El que escribe está enojado o molesto?"},
        "escalar": {
            "instructions": "¿Tiene que tomarlo una persona (pide un reembolso, amenaza con darse de baja o denunciar, pide hablar con un responsable, problema repetido sin resolver, o el mensaje es demasiado vago para actuar)?"
        },
    },
}


@dataclass
class Verdict:
    area: str
    area_probs: dict[str, float]
    urgencia: int  # nivel más probable (0 baja, 1 media, 2 alta)
    urgencia_probs: list[float]
    enojado: float  # probabilidad de "sí"
    escalar: float
    ms: float  # punta a punta, incluye HTTP
    model_ms: float | None
    input_tokens: int | None


class Kev:
    def __init__(self, base_url: str = "http://127.0.0.1:8009", lang: str = "en"):
        self.base_url = base_url.rstrip("/")
        self.lang = lang
        self.client = httpx.AsyncClient(timeout=60.0)

    def body(self, de: str, texto: str) -> dict:
        q = QUESTIONS[self.lang]
        return {
            "state": {"from": de, "message": texto} if self.lang == "en" else {"de": de, "mensaje": texto},
            "model": "kev-latest",
            "questions": {
                "area": {"type": "choice", **q["area"]},
                "urgencia": {"type": "score", **q["urgencia"]},
                "enojado": {"type": "noul", **q["enojado"]},
                "escalar": {"type": "noul", **q["escalar"]},
            },
        }

    async def classify(self, de: str, texto: str) -> Verdict:
        t0 = time.perf_counter()
        r = await self.client.post(f"{self.base_url}/v1/systemone", json=self.body(de, texto))
        ms = (time.perf_counter() - t0) * 1000
        r.raise_for_status()
        data = r.json()
        a = data["answers"]
        u = a["urgencia"]["probabilities"]
        urg_probs = [u[str(i)] for i in range(len(URGENCIA))]
        return Verdict(
            area=a["area"]["choice"],
            area_probs=a["area"]["probabilities"],
            urgencia=max(range(len(urg_probs)), key=lambda i: urg_probs[i]),
            urgencia_probs=urg_probs,
            enojado=a["enojado"]["noul"],
            escalar=a["escalar"]["noul"],
            ms=ms,
            model_ms=data.get("latency_ms"),
            input_tokens=data.get("usage", {}).get("input_tokens"),
        )
