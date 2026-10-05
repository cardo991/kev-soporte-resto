"""Conexión con WhatsApp vía la API oficial de WhatsApp Business (Cloud API de Meta).

Meta llama a nuestro webhook cada vez que alguien le escribe al número del negocio:
- GET  /webhook/whatsapp → verificación inicial (devolvemos `hub.challenge` si el token coincide).
- POST /webhook/whatsapp → mensajes entrantes, firmados con HMAC-SHA256 del cuerpo usando el
  App Secret (header `X-Hub-Signature-256: sha256=<hex>`).

Opcionalmente respondemos al cliente con un mensaje de texto (dentro de la ventana de 24 h que
abre su mensaje). Toda la configuración sale de variables de entorno (ver `.env.example` y
`docs/WHATSAPP.md`).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

import httpx

from soporte.kev import AREA_NAMES


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "si", "sí")


@dataclass(frozen=True)
class Config:
    verify_token: str
    app_secret: str
    access_token: str
    phone_number_id: str
    graph_version: str
    auto_reply: bool
    show_names: bool
    allow_unsigned: bool

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip(),
            app_secret=os.getenv("WHATSAPP_APP_SECRET", "").strip(),
            access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip(),
            phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
            graph_version=os.getenv("WHATSAPP_GRAPH_VERSION", "v25.0").strip(),
            auto_reply=_flag("WHATSAPP_AUTO_REPLY"),
            show_names=_flag("WHATSAPP_SHOW_NAMES"),
            # Sólo para probar en local sin firma (scripts/simular_whatsapp.py sin App Secret).
            allow_unsigned=_flag("WHATSAPP_ALLOW_UNSIGNED"),
        )

    @property
    def receiving(self) -> bool:
        """Listo para recibir: hay token de verificación y forma de autenticar los POST."""
        return bool(self.verify_token and (self.app_secret or self.allow_unsigned))

    @property
    def can_reply(self) -> bool:
        # Responder exige firma verificada: si no, cualquiera podría hacernos escribirle a un número.
        return bool(self.auto_reply and self.app_secret and self.access_token and self.phone_number_id)


def verify_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256=") or not app_secret:
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def sign(raw_body: bytes, app_secret: str) -> str:
    """El header que mandaría Meta (para el simulador y los tests)."""
    return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def extract_messages(payload: dict) -> list[dict]:
    """Mensajes de texto entrantes del webhook. Ignora estados de entrega y mensajes no-texto."""
    out = []
    if payload.get("object") != "whatsapp_business_account":
        return out
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts", [])}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id", "")
            for m in value.get("messages", []):
                if m.get("type") != "text":
                    continue
                body = ((m.get("text") or {}).get("body") or "").strip()
                if not body:
                    continue
                out.append({
                    "wamid": m.get("id", ""),
                    "wa_id": m.get("from", ""),
                    "name": names.get(m.get("from"), ""),
                    "text": body[:600],
                    "phone_number_id": phone_number_id,
                })
    return out


URG_ES = ["baja", "media", "alta"]


def reply_text(verdict: dict) -> str:
    """Respuesta automática según lo que decidió Kev."""
    if verdict["column"] == "persona":
        return "¡Hola! Recibimos tu mensaje. Una persona del equipo lo va a revisar y te contacta a la brevedad."
    area = AREA_NAMES.get(verdict["area"], verdict["area"])
    if verdict["urgencia"] == 2:
        return f"¡Hola! Recibimos tu mensaje y lo pasamos con prioridad alta al equipo de {area}. Ya lo estamos viendo."
    return f"¡Hola! Recibimos tu mensaje y lo pasamos al equipo de {area} (urgencia {URG_ES[verdict['urgencia']]}). Te respondemos a la brevedad."


async def send_reply(cfg: Config, client: httpx.AsyncClient, to: str, body: str, reply_to: str | None = None) -> None:
    """Manda un texto al cliente, citando su mensaje. Sólo válido dentro de la ventana de 24 h."""
    url = f"https://graph.facebook.com/{cfg.graph_version}/{cfg.phone_number_id}/messages"
    payload: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    if reply_to:
        payload["context"] = {"message_id": reply_to}
    r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {cfg.access_token}"}, timeout=15.0)
    r.raise_for_status()
