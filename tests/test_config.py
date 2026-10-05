import json

import pytest

from soporte import kev
from soporte.whatsapp_cloud import reply_text


def write(tmp_path, cfg):
    p = tmp_path / "areas.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_config_por_defecto_son_las_columnas_del_restaurante():
    assert kev.AREAS == ["tecnico", "facturacion", "reservas", "delivery"]
    body = kev.Kev().body("Mozo · bar", "no imprime la comandera")
    assert list(body["questions"]["area"]["criteria"]) == kev.AREAS


def test_otro_archivo_cambia_las_columnas(tmp_path):
    cfg = kev.load_config(kev.ROOT / "config" / "areas.tienda-online.json")
    assert [a["id"] for a in cfg["areas"]] == ["envios", "pagos", "cambios", "productos"]
    mini = kev.load_config(write(tmp_path, {"pregunta": "Which team?", "areas": [
        {"id": "ventas", "descripcion": "Sales"}, {"id": "soporte", "descripcion": "Support"}]}))
    assert mini["areas"][0]["nombre"] == "Ventas" and mini["areas"][0]["icono"]


@pytest.mark.parametrize("areas", [
    [{"id": "solo", "descripcion": "x"}],                                          # menos de 2
    [{"id": "Con Espacio", "descripcion": "x"}, {"id": "b", "descripcion": "y"}],  # id inválido
    [{"id": "persona", "descripcion": "x"}, {"id": "b", "descripcion": "y"}],      # reservado
    [{"id": "a", "descripcion": "x"}, {"id": "a", "descripcion": "y"}],            # repetido
    [{"id": "a"}, {"id": "b", "descripcion": "y"}],                                # sin descripción
])
def test_config_invalida_avisa(tmp_path, areas):
    with pytest.raises(ValueError):
        kev.load_config(write(tmp_path, {"pregunta": "Which team?", "areas": areas}))


def test_respuesta_de_whatsapp_usa_el_nombre_del_area():
    assert "Facturación" in reply_text({"column": "facturacion", "area": "facturacion", "urgencia": 0})
