"""Cliente de Kev para clasificar mensajes de soporte de restaurantes.

Una sola llamada a `POST /v1/systemone` con cuatro preguntas (área, urgencia, enojo y si
hay que escalar a una persona). Kev las responde todas en una pasada, sin generar texto.
Los mensajes llegan en español; las preguntas pueden ir en inglés (el idioma en que se
entrenó Kev) o en español: `lang` elige cuál.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

AREAS = ["tecnico", "facturacion", "reservas", "delivery"]
URGENCIA = ["baja", "media", "alta"]

QUESTIONS = {
    "en": {
        "area": {
            "instructions": "This message was sent by a restaurant to the support team of its restaurant software (point of sale, kitchen printers, online orders, reservations). Which team should handle it?",
            "criteria": {
                "tecnico": "Technical: the in-house system, point of sale, cash register, kitchen printers or screens, tablets, card readers, connection, errors, slowness, how to use a feature (not online orders)",
                "facturacion": "Billing: the restaurant's subscription with us, charges, invoices we send, payments, prices, plans, cancelling the service",
                "reservas": "Reservations: table bookings, the online booking page, booking confirmations and capacity",
                "delivery": "Delivery: anything about online or delivery orders, even if they fail or do not arrive: the delivery app, online menu, couriers, delivery fees, online payments",
            },
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
            "instructions": "Este mensaje lo mandó un restaurante al soporte de su software (punto de venta, comanderas, pedidos online, reservas). ¿Qué equipo lo tiene que atender?",
            "criteria": {
                "tecnico": "Técnico: el sistema, la caja, comanderas o pantallas de cocina, tablets, lectores de tarjeta, conexión, errores, lentitud, cómo usar una función",
                "facturacion": "Facturación: el abono del restaurante con nosotros, cobros, facturas que le mandamos, pagos, precios, planes, dar de baja el servicio",
                "reservas": "Reservas: reservas de mesas, la página de reservas online, confirmaciones y cupos",
                "delivery": "Delivery: pedidos online, la integración con la app de delivery, repartidores, menú, precios o costos de envío",
            },
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
