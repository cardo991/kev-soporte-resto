"""Simula un mensaje entrante de WhatsApp contra tu webhook local, sin pasar por Meta.

Arma el mismo JSON que manda Meta y lo firma con WHATSAPP_APP_SECRET del .env (si no hay
App Secret, hace falta WHATSAPP_ALLOW_UNSIGNED=1 en el .env del server).

    uv run python scripts/simular_whatsapp.py "se colgó la comandera y tengo el salón lleno"
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from soporte.whatsapp_cloud import sign  # noqa: E402

load_dotenv(ROOT / ".env")


def payload(text: str, name: str, wa_id: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "0",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "15550000000", "phone_number_id": "0"},
                    "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
                    "messages": [{
                        "from": wa_id,
                        "id": f"wamid.SIMULADO{uuid.uuid4().hex}",
                        "timestamp": str(int(time.time())),
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("texto")
    ap.add_argument("--nombre", default="Cliente de prueba")
    ap.add_argument("--wa-id", default="5490000000000", help="número inventado del remitente")
    ap.add_argument("--url", default="http://127.0.0.1:8002/webhook/whatsapp")
    a = ap.parse_args()
    raw = json.dumps(payload(a.texto, a.nombre, a.wa_id)).encode()
    headers = {"content-type": "application/json"}
    secret = os.getenv("WHATSAPP_APP_SECRET", "").strip()
    if secret:
        headers["x-hub-signature-256"] = sign(raw, secret)
    r = httpx.post(a.url, content=raw, headers=headers, timeout=10)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
