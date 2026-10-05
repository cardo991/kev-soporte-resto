"""Lee un chat exportado de WhatsApp ("Exportar chat", sin archivos) y lo convierte en mensajes.

Soporta el formato de Android ("4/10/26, 21:15 - Nombre: texto") y el de iPhone
("[4/10/26, 21:15:03] Nombre: texto"), mensajes de varias líneas y el .zip que arma
WhatsApp. Se saltean los mensajes del sistema (cifrado, "se unió", etc.) y los multimedia.

Los nombres se reemplazan por "Contacto 1", "Contacto 2"... para no mostrar quién escribió.
Todo se procesa en la máquina; nada sale a internet.
"""

from __future__ import annotations

import io
import re
import zipfile

LINE = re.compile(
    r"^\[?(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?\s?m\.?)?)\]?\s*(?:-\s*)?"
    r"(?P<who>[^:]{1,60}?):\s(?P<text>.*)$",
    re.IGNORECASE,
)
# Invisibles que mete WhatsApp (marcas de dirección, espacios finos).
INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‬ ﻿"), None)
SKIP = re.compile(
    r"(multimedia omitido|media omitted|imagen omitida|image omitted|video omitido|video omitted|audio omitido|audio omitted|"
    r"sticker omitido|sticker omitted|documento omitido|document omitted|gif omitido|gif omitted|"
    r"se eliminó este mensaje|this message was deleted|eliminaste este mensaje|you deleted this message|<se editó este mensaje>|"
    r"null$)",
    re.IGNORECASE,
)


def read_export(filename: str, data: bytes) -> str:
    """Texto del chat, venga como .txt o como el .zip que exporta WhatsApp."""
    if filename.lower().endswith(".zip") or data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            txts = [n for n in z.namelist() if n.lower().endswith(".txt")]
            if not txts:
                raise ValueError("el .zip no tiene ningún .txt de chat")
            name = next((n for n in txts if "chat" in n.lower()), txts[0])
            data = z.read(name)
    return data.decode("utf-8-sig", errors="replace")


def parse(text: str, limit: int = 60) -> list[dict]:
    """Los últimos `limit` mensajes con texto, como {id, de, texto}."""
    msgs: list[dict] = []
    for raw in text.splitlines():
        line = raw.translate(INVISIBLE).rstrip()
        m = LINE.match(line)
        if m:
            msgs.append({"who": m["who"].strip(), "text": m["text"].strip()})
        elif msgs and line.strip():
            msgs[-1]["text"] += "\n" + line.strip()  # continuación de un mensaje de varias líneas
    contacts: dict[str, str] = {}
    out = []
    for m in msgs:
        t = m["text"].strip()
        if not t or SKIP.search(t):
            continue
        alias = contacts.setdefault(m["who"], f"Contacto {len(contacts) + 1}")
        out.append({"de": f"{alias} · WhatsApp", "texto": t[:600]})
    out = out[-limit:]
    for i, m in enumerate(out, 1):
        m["id"] = f"wa{i}"
    return out
