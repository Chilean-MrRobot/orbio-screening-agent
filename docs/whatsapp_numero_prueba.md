# Configurar el número de prueba de WhatsApp Cloud API

Guía para dejar funcionando el webhook (spec 005) con el número de prueba de Meta, de modo que los evaluadores escriban desde sus teléfonos. El número de prueba es gratis, pero solo envía a **hasta 5 destinatarios registrados** y solo trae la plantilla `hello_world`.

## 1. Crear la app en Meta

1. Entrar a [developers.facebook.com](https://developers.facebook.com/) → **My Apps** → **Create app**.
2. Caso de uso: **Connect with customers through WhatsApp** (tipo *Business*). Asociarla a un portafolio comercial (se puede crear uno en el mismo paso).
3. En el panel de la app: **WhatsApp → API Setup**. Meta crea automáticamente un **número de prueba**.
4. Copiar a `.env`:
   - **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID` (no es el número, es el ID).
   - **Temporary access token** → `WHATSAPP_ACCESS_TOKEN`. Dura 24 h; para el demo conviene un token permanente (paso 6).
5. **App settings → Basic → App secret** → `WHATSAPP_APP_SECRET` (se usa para validar la firma `X-Hub-Signature-256`).
6. Inventar un valor para `WHATSAPP_VERIFY_TOKEN` (cualquier texto largo y aleatorio; solo se usa al registrar el webhook).

## 2. Registrar los teléfonos de los evaluadores

1. En **API Setup**, campo **To** → **Manage phone number list** → **Add phone number**.
2. Ingresar el número con código de país. Al dueño le llega un código por WhatsApp que debe dictar o ingresar.
3. Máximo 5 números. Si se envía a uno no registrado, Meta responde `(#131030) Recipient phone number not in allowed list` y el canal devuelve `SendResult(ok=False)`.

**México:** WhatsApp identifica los móviles mexicanos como `521` + 10 dígitos. El webhook los normaliza a `+52` + 10 dígitos para que coincidan con la ficha. Si un envío a `+52…` falla con el error 131030, registrar el número también con el `1` (`+521…`).

## 3. Levantar el webhook en local con un túnel

```bash
.venv\Scripts\python -m uvicorn screening.api:create_app_from_env --factory --port 8080
```

En otra terminal (con [ngrok](https://ngrok.com/) instalado y autenticado):

```bash
ngrok http 8080
```

Ngrok muestra una URL pública `https://<algo>.ngrok-free.app`. Las variables de entorno se leen del entorno del proceso (no hay carga automática de `.env`), así que hay que exportarlas antes de lanzar `uvicorn`.

## 4. Apuntar el webhook

1. En la app de Meta: **WhatsApp → Configuration → Webhook → Edit**.
2. **Callback URL:** `https://<algo>.ngrok-free.app/webhooks/whatsapp`.
3. **Verify token:** el mismo valor de `WHATSAPP_VERIFY_TOKEN`. Al guardar, Meta llama al `GET` y la app responde el `hub.challenge`.
4. En **Webhook fields**, suscribir **`messages`** (incluye los mensajes entrantes y los estados de entrega).

## 5. Probar el circuito

1. Desde un teléfono registrado, escribir al número de prueba (esto abre la ventana de 24 h en la que se pueden enviar mensajes libres).
2. Escribir dos o tres mensajes seguidos. Tras `WHATSAPP_BATCH_SECONDS` (8 s por defecto) sin mensajes nuevos, el handler de prueba responde **un solo** eco con todo el lote ("Recibí: …"). Si no hay `WHATSAPP_ACCESS_TOKEN`, solo se registra en el log.
3. Test de integración (envía `hello_world`, que funciona fuera de la ventana de 24 h):

```bash
.venv\Scripts\python -m pytest -m integration tests/integration
```

Requiere `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` y `WHATSAPP_TEST_RECIPIENT` (un número registrado, en E.164).

## 6. Token permanente para el demo

El token temporal vence a las 24 h. Para que el demo no se corte:

1. **Business Settings → Users → System users → Add** (rol *Admin*).
2. **Assign assets**: la app, con control total.
3. **Generate new token** → elegir la app y los permisos `whatsapp_business_messaging` y `whatsapp_business_management`, sin vencimiento.
4. Reemplazar `WHATSAPP_ACCESS_TOKEN`.

## 7. Cloud Run

La agrupación de mensajes corre en memoria después de responder 200 a Meta (ver la decisión en `specs/005-canal-whatsapp.md`), así que el servicio necesita **CPU siempre asignada** y **una sola instancia**:

```bash
gcloud run deploy orbio-screening --source . --region europe-southwest1 --no-cpu-throttling --min-instances 0 --max-instances 1 --set-secrets WHATSAPP_ACCESS_TOKEN=whatsapp-access-token:latest,WHATSAPP_APP_SECRET=whatsapp-app-secret:latest,WHATSAPP_VERIFY_TOKEN=whatsapp-verify-token:latest --set-env-vars WHATSAPP_PHONE_NUMBER_ID=<id>
```

Luego cambiar la Callback URL del paso 4 por la URL del servicio. Con `min-instances 0` el servicio escala a cero cuando no hay tráfico; con el tráfico del demo el costo cabe en la capa gratuita.

## Problemas comunes

| Síntoma | Causa |
|---|---|
| Meta no logra verificar el webhook | `WHATSAPP_VERIFY_TOKEN` distinto, o el túnel apunta a otro puerto |
| Todos los `POST` responden 401 | `WHATSAPP_APP_SECRET` equivocado (es el *App secret*, no el token) |
| Llegan los mensajes pero no hay eco | Falta `WHATSAPP_ACCESS_TOKEN`, o venció (error `#190`) |
| `#131030` al enviar | El destinatario no está en la lista del número de prueba |
| `#131047` al enviar | Pasaron más de 24 h desde el último mensaje del candidato: solo se pueden enviar plantillas |
