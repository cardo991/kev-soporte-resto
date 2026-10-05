import json

import pytest
from fastapi.testclient import TestClient

import soporte.server as server
from soporte.whatsapp_cloud import Config, extract_messages, reply_text, sign, verify_signature

SECRET = "secreto-de-prueba"


def cfg(**kw):
    base = dict(verify_token="tok", app_secret=SECRET, access_token="", phone_number_id="", graph_version="v25.0",
                auto_reply=False, show_names=False, allow_unsigned=False)
    return Config(**{**base, **kw})


def meta_payload(text="no imprime la comandera", type_="text", wamid="wamid.1"):
    msg = {"from": "5491100000000", "id": wamid, "timestamp": "1", "type": type_}
    if type_ == "text":
        msg["text"] = {"body": text}
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "123"},
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": "5491100000000"}], "messages": [msg]}}]}]}


def test_firma():
    raw = b'{"a":1}'
    assert verify_signature(raw, sign(raw, SECRET), SECRET)
    assert not verify_signature(raw, sign(raw, "otro"), SECRET)
    assert not verify_signature(raw, None, SECRET)


def test_extrae_solo_texto():
    assert [m["text"] for m in extract_messages(meta_payload())] == ["no imprime la comandera"]
    assert extract_messages(meta_payload(type_="image")) == []
    status = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"statuses": [{"status": "read"}]}}]}]}
    assert extract_messages(status) == []


def test_respuesta_automatica():
    assert "Técnico" in reply_text({"column": "tecnico", "area": "tecnico", "urgencia": 1})
    assert "persona" in reply_text({"column": "persona", "area": "tecnico", "urgencia": 0})


@pytest.fixture
def client(monkeypatch):
    calls = []

    async def fake_process(m):
        calls.append(m)

    monkeypatch.setattr(server, "WA", cfg())
    monkeypatch.setattr(server, "process_whatsapp", fake_process)
    server.SEEN_WAMIDS.clear()
    with TestClient(server.app) as c:
        yield c, calls


def test_verificacion_webhook(client):
    c, _ = client
    r = c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "1234"})
    assert r.status_code == 200 and r.text == "1234"
    assert c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "mal", "hub.challenge": "1"}).status_code == 403


def test_post_firmado_y_dedupe(client):
    c, calls = client
    raw = json.dumps(meta_payload()).encode()
    assert c.post("/webhook/whatsapp", content=raw, headers={"x-hub-signature-256": sign(raw, "otro")}).status_code == 401
    for _ in range(2):  # Meta reintenta: el segundo no se procesa
        assert c.post("/webhook/whatsapp", content=raw, headers={"x-hub-signature-256": sign(raw, SECRET)}).status_code == 200
    assert len(calls) == 1 and calls[0]["text"] == "no imprime la comandera"


def test_sin_secret_rechaza(monkeypatch, client):
    c, _ = client
    monkeypatch.setattr(server, "WA", cfg(app_secret=""))
    assert c.post("/webhook/whatsapp", content=b"{}").status_code == 503
