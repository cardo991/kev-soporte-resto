import io
import zipfile

from soporte.whatsapp import parse, read_export

ANDROID = """4/10/26, 21:10 - Los mensajes y las llamadas están cifrados de extremo a extremo.
4/10/26, 21:15 - Juan Pérez: se colgó la comandera
y tengo el salón lleno
4/10/26, 21:16 - María: <Multimedia omitido>
4/10/26, 21:17 - María: me cobraron dos veces el abono
"""
IOS = "‎[04/10/26, 21:15:03] Juan Pérez: no imprime la barra\n[04/10/26, 21:16:10] Ana: ‎imagen omitida\n[4/10/26, 9:01:00 p. m.] Ana: quiero cambiar una reserva\n"


def test_android_multilinea_y_sistema():
    msgs = parse(ANDROID)
    assert [m["texto"] for m in msgs] == ["se colgó la comandera\ny tengo el salón lleno", "me cobraron dos veces el abono"]
    assert msgs[0]["de"].startswith("Contacto 1") and msgs[1]["de"].startswith("Contacto 2")  # nombres anonimizados
    assert "Juan" not in str(msgs) and "María" not in str(msgs)


def test_ios_invisibles_y_omitidos():
    msgs = parse(IOS)
    assert [m["texto"] for m in msgs] == ["no imprime la barra", "quiero cambiar una reserva"]


def test_zip_y_limite():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("_chat.txt", ANDROID)
    text = read_export("chat.zip", buf.getvalue())
    assert len(parse(text, limit=1)) == 1 and parse(text, limit=1)[0]["texto"].startswith("me cobraron")
