# Conectar la bandeja a WhatsApp

Con esto, los mensajes que le escriban al número de WhatsApp de tu negocio aparecen en vivo en la bandeja y Kev los clasifica. Opcionalmente, le responde al cliente ("lo pasamos al equipo de Técnico…").

Usa la **API oficial de WhatsApp Business (Cloud API de Meta)**. No uses librerías no oficiales que se conectan como si fueran WhatsApp Web: violan los términos de WhatsApp y te pueden bloquear el número.

## Cómo funciona

```
Cliente ──WhatsApp──▶ Meta ──webhook (HTTPS, firmado)──▶ /webhook/whatsapp ──▶ Kev ──▶ bandeja en vivo
                                                                        └──(opcional)──▶ respuesta al cliente
```

- Meta le avisa a tu app cada mensaje con un POST a `/webhook/whatsapp`, firmado con tu **App Secret**. Si la firma no coincide, el mensaje se rechaza.
- Por ahora la app procesa los mensajes de **texto**; audios, fotos, etc. se ignoran.
- Los remitentes aparecen como "Contacto 1", "Contacto 2"…; el teléfono no se muestra ni se guarda. Con `WHATSAPP_SHOW_NAMES=1` se muestra el nombre de perfil.
- Kev corre en tu máquina, pero **los mensajes pasan por los servidores de Meta**, como cualquier mensaje de WhatsApp.

## 1. Crear la app en Meta (una sola vez)

1. Entrá a [developers.facebook.com](https://developers.facebook.com/) con tu cuenta de Facebook y creá una app de tipo **Business** (Negocios).
2. En la app, agregá el producto **WhatsApp**. Meta te da un **número de prueba** gratis y te deja agregar hasta 5 números destinatarios para probar (el tuyo, por ejemplo).
3. En **WhatsApp → Configuración de la API** (*API Setup*) vas a ver:
   - el **Phone number ID** (identificador del número),
   - un **token de acceso temporal** (dura unas horas; para algo permanente hay que crear un *System User* en el Business Manager).
4. En **Configuración de la app → Básica** copiá la **Clave secreta de la app** (*App Secret*).

## 2. Completar el `.env`

```bash
cp .env.example .env
```

| Variable | Qué poner |
|---|---|
| `WHATSAPP_VERIFY_TOKEN` | Un texto cualquiera que inventás vos (por ejemplo, `kev-resto-2026`). Lo vas a repetir en Meta. |
| `WHATSAPP_APP_SECRET` | La clave secreta de la app (obligatoria). |
| `WHATSAPP_ACCESS_TOKEN` | El token de acceso. Sólo hace falta si querés respuestas automáticas. |
| `WHATSAPP_PHONE_NUMBER_ID` | El Phone number ID. Sólo para respuestas automáticas. |
| `WHATSAPP_AUTO_REPLY` | `1` para que Kev le responda al cliente; `0` (por defecto) para sólo clasificar. |

El `.env` está en `.gitignore`: no lo subas nunca, tiene tus credenciales.

## 3. Darle a tu compu una URL pública

Meta necesita una URL **HTTPS pública** para llamar a tu webhook. Para probar desde tu máquina, la forma más simple es un túnel:

```bash
./scripts/start.sh                                  # en una terminal: Kev + la bandeja en :8002
brew install cloudflared                            # una sola vez
cloudflared tunnel --url http://localhost:8002      # en otra terminal
```

`cloudflared` te muestra una URL del estilo `https://algo-al-azar.trycloudflare.com`. Esa URL cambia cada vez que lo corrés: si la reiniciás, hay que actualizarla en Meta. Para algo estable, usá un túnel con nombre (cuenta gratis de Cloudflare), ngrok con dominio fijo, o subí la app a un servidor.

## 4. Configurar el webhook en Meta

1. En **WhatsApp → Configuración** (*Configuration*) → **Webhook** → **Editar**:
   - **URL de devolución de llamada:** `https://<tu-url>/webhook/whatsapp`
   - **Token de verificación:** el mismo `WHATSAPP_VERIFY_TOKEN` del `.env`
2. Tocá **Verificar y guardar**. Meta hace un GET y la app le devuelve el `hub.challenge`; si sale bien, queda verificado.
3. En **Campos del webhook**, suscribite a **`messages`**.

## 5. Probar

Abrí la bandeja (`http://127.0.0.1:8002`). Arriba tiene que decir **"WhatsApp conectado"**. Desde tu celular, mandale un mensaje al número de prueba: aparece en el tablero con la etiqueta *WhatsApp en vivo*. Si activaste las respuestas automáticas, te llega la respuesta y la tarjeta muestra "↩ respondido".

### Probar sin Meta

Para ver que todo anda en tu máquina antes de configurar Meta:

```bash
# con WHATSAPP_VERIFY_TOKEN y WHATSAPP_APP_SECRET en el .env (pueden ser valores inventados)
uv run python scripts/simular_whatsapp.py "se colgó la comandera y tengo el salón lleno"
```

El simulador arma el mismo JSON que manda Meta y lo firma con tu App Secret.

## Antes de usarlo con clientes reales

- **Token permanente:** creá un *System User* en el Business Manager y generá un token sin vencimiento.
- **Número propio:** registrá el número de tu negocio en lugar del de prueba (Meta pide verificar el negocio).
- **Ventana de 24 h:** sólo podés responder con texto libre dentro de las 24 h desde el último mensaje del cliente. Para iniciar conversaciones hacen falta plantillas aprobadas por Meta (esta app no las usa).
- **Precios y políticas:** revisá los precios vigentes de la API de WhatsApp Business y sus políticas de uso.
- **Privacidad:** avisales a tus clientes que sus mensajes los clasifica un sistema automático.
- **Servidor:** para algo que funcione 24/7, corré Kev y la app en un servidor (Kev-0.8B entra en una GPU de 4 GB o en un Mac) con una URL fija.

## Problemas comunes

| Síntoma | Causa probable |
|---|---|
| Meta dice que no pudo verificar la URL | La URL o el `WHATSAPP_VERIFY_TOKEN` no coinciden, el túnel no está corriendo o falta `/webhook/whatsapp` al final. |
| Los mensajes no llegan a la bandeja | No te suscribiste al campo `messages`, o la URL del túnel cambió. |
| `401 firma inválida` en los logs | El `WHATSAPP_APP_SECRET` no es el de esta app. |
| `503` en el webhook | Falta `WHATSAPP_APP_SECRET` en el `.env`. |
| No responde al cliente | Falta `WHATSAPP_AUTO_REPLY=1`, el token o el Phone number ID; o el token temporal venció. |
