# 005 — Canales (WhatsApp, SMS simulado y consola) y webhook

**Estado:** implementada
**Ola:** 1 · **Depende de:** 000

## Objetivo
Dar al agente una forma de hablar con el candidato: enviar mensajes por WhatsApp Cloud API (número de prueba de Meta), simular el SMS de los recordatorios, chatear por consola en desarrollo y recibir los mensajes entrantes por un webhook confiable (firma validada, persistencia antes de procesar, sin duplicados y agrupando los mensajes seguidos).

## Alcance
- Incluye:
  - `WhatsAppCloudChannel`, `SimulatedSmsChannel` y `ConsoleChannel`, todos implementan `MessageChannel`.
  - App FastAPI (`create_app`) con el webhook de WhatsApp (`GET` de verificación y `POST` de mensajes) y `/healthz`.
  - `InboundMessageStore` (interfaz + implementación en memoria), `InboundHandler` (interfaz + handlers de prueba) y el agrupador de mensajes.
  - Guía para configurar el número de prueba: `docs/whatsapp_numero_prueba.md`.
- No incluye:
  - La conversación (ola 2, spec 009): aquí el handler solo registra o hace eco.
  - El opt-out ("no me escriban más") y el traspaso a humano: los decide la conversación; el canal solo entrega el texto.
  - `InboundMessageStore` en Firestore (ola 2, spec 008).
  - Plantillas reales aprobadas por Meta: el número de prueba solo trae `hello_world`.
  - Respuesta a audios, imágenes o ubicaciones: se persisten y se entregan con `text=None`.

## Interfaz

**`screening.channels`**

```python
class WhatsAppCloudChannel:  # name = ChannelName.WHATSAPP
    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        *,
        client: httpx.Client | None = None,
        api_version: str = "v23.0",
        template_language: str = "es",
    ) -> None: ...
    def send(self, message: OutboundMessage) -> SendResult: ...


class SimulatedSmsChannel:  # name = ChannelName.SMS
    sent: list[SentSms]  # lo "enviado", para el demo y los tests

    def __init__(self, clock: Clock) -> None: ...
    def send(self, message: OutboundMessage) -> SendResult: ...


class ConsoleChannel:  # name = ChannelName.CONSOLE
    def __init__(self, out: TextIO = sys.stdout) -> None: ...
    def send(self, message: OutboundMessage) -> SendResult: ...


class InboundMessage(BaseModel):
    external_id: str  # wamid de Meta
    sender: str  # E.164 con "+"
    text: str | None  # None si no es texto (audio, imagen...)
    type: str  # "text", "button", "interactive", "audio"...
    profile_name: str | None
    sent_at: datetime  # timestamp de Meta
    received_at: datetime  # Clock inyectado
    processed: bool = False

    def to_domain_message(self) -> Message: ...  # role=candidate, channel=whatsapp


class InboundBatch(BaseModel):
    sender: str
    messages: list[InboundMessage]
    text: str  # propiedad: textos unidos con "\n"


class InboundMessageStore(Protocol):
    def add(self, message: InboundMessage) -> bool: ...  # False si el ID ya existía
    def mark_processed(self, external_ids: list[str]) -> None: ...
    def get(self, external_id: str) -> InboundMessage | None: ...
    def list_unprocessed(self) -> list[InboundMessage]: ...


class InboundHandler(Protocol):
    def handle(self, batch: InboundBatch) -> None: ...


class MessageBatcher:  # puro y síncrono, testeable con FixedClock
    def __init__(self, window_seconds: float) -> None: ...
    def add(self, message: InboundMessage, now: datetime) -> None: ...
    def seconds_until_due(self, sender: str, now: datetime) -> float | None: ...
    def pop(self, sender: str) -> InboundBatch | None: ...
    def pop_all(self) -> list[InboundBatch]: ...
```

Implementaciones incluidas: `InMemoryInboundMessageStore`, `LoggingInboundHandler` y `EchoInboundHandler(channel)`.

**`screening.api`**

```python
class WhatsAppSettings(BaseModel):
    verify_token: SecretStr
    app_secret: SecretStr
    access_token: SecretStr | None
    phone_number_id: str | None
    batch_seconds: float = 8.0         # WHATSAPP_BATCH_SECONDS
    api_version: str = "v23.0"         # WHATSAPP_GRAPH_VERSION
    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> WhatsAppSettings: ...

def create_app(settings: WhatsAppSettings, *, store: InboundMessageStore,
               handler: InboundHandler, clock: Clock,
               sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> FastAPI: ...

def create_app_from_env() -> FastAPI:  # uvicorn screening.api:create_app_from_env --factory
```

Rutas: `GET /webhooks/whatsapp`, `POST /webhooks/whatsapp`, `GET /healthz`.

## Comportamiento

**Envío por WhatsApp**
- `POST https://graph.facebook.com/{api_version}/{phone_number_id}/messages` con `Authorization: Bearer`.
- Sin `template`: mensaje `type=text` con `message.text`. Con `template`: `type=template`, idioma `template_language` y, si hay `template_params`, un componente `body` con parámetros con nombre (`parameter_name`).
- El destinatario se envía en dígitos, sin `+`.
- Respuesta 2xx → `SendResult(ok=True, external_id=<wamid>)`.
- Respuesta no 2xx → `ok=False` con `error="HTTP <status>: (#<code>) <mensaje de Meta>"`. Error de red o timeout → `ok=False` con el tipo de error. **Nunca lanza excepción.**
- `OutboundMessage` no trae idioma; por eso el idioma de la plantilla se fija al construir el canal. Se propone en el PR agregar `language` al contrato.

**SMS simulado y consola**
- `SimulatedSmsChannel` no envía nada: registra con `logging` (`INFO`) y agrega un `SentSms(to, text, template, at)` a `sent`, con `at` del `Clock`. Devuelve `ok=True` con `external_id="sms-sim-<n>"`.
- `ConsoleChannel` escribe `[agente → <to>] <texto>` en el stream y devuelve `ok=True`.

**Webhook: verificación (`GET`)**
- Si `hub.mode == "subscribe"` y `hub.verify_token` coincide con `WHATSAPP_VERIFY_TOKEN` → 200 con `hub.challenge` en texto plano. Si no → 403.

**Webhook: mensajes (`POST`)**, en este orden:
1. **Firma:** `X-Hub-Signature-256 = "sha256=" + HMAC-SHA256(app_secret, cuerpo crudo)`, comparada en tiempo constante. Falta o no coincide → 401 y no se procesa nada.
2. Cuerpo que no es JSON → 400. JSON con otra forma → 200 sin hacer nada (no tiene sentido que Meta reintente).
3. Se recorren `entry[].changes[].value`. Se ignoran los cambios de un `phone_number_id` distinto al configurado y **los webhooks de estado** (`value.statuses`: enviado, entregado, leído, fallido).
4. Por cada mensaje: se arma `InboundMessage`. El remitente se normaliza a E.164 (`+` + dígitos). En México, WhatsApp reporta `521` + 10 dígitos; se normaliza a `+52` + 10 dígitos para que coincida con la ficha.
5. **Se persiste antes de procesar** (`store.add`). Si el ID de Meta ya existía, `add` devuelve `False` y el mensaje **se descarta (deduplicación)**. Si el store falla, el webhook responde 500 para que Meta reintente.
6. Se entrega al despachador de lotes y se responde **200 de inmediato**; el procesamiento ocurre fuera de la request.

**Agrupación (`BatchDispatcher` + `MessageBatcher`)** — *reemplazada por la spec 016 (Cloud Tasks); se conserva como historia de la decisión.*
- Cada mensaje nuevo de un remitente reinicia su plazo: `deadline = now + WHATSAPP_BATCH_SECONDS` (por defecto 8 s). Ventana deslizante: si el candidato escribe "hola", "soy Lucía" y "tengo moto" con menos de 8 s entre cada uno, se entregan en **un solo lote** 8 s después del último.
- Cada mensaje programa un temporizador (`asyncio`) que duerme hasta el plazo; al despertar recalcula con el `Clock` y, si el plazo se movió, vuelve a dormir. El primero que encuentra el lote vencido lo saca (`pop`) y los demás ya no encuentran nada.
- El lote se entrega a `InboundHandler.handle` en un hilo (`asyncio.to_thread`) para no bloquear el webhook con el LLM. Si termina bien, los mensajes se marcan `processed`. Si lanza excepción, se registra y los mensajes quedan sin procesar (`list_unprocessed` permite recuperarlos).
- Al apagar la app (lifespan, p. ej. el `SIGTERM` de Cloud Run) se entregan de inmediato los lotes pendientes.
- El tiempo sale del `Clock` inyectado y la espera de un `sleep` inyectable, así que los tests controlan ambos.
- Un webhook que trae varios mensajes del mismo remitente cae en el mismo lote.

**Agrupación en Cloud Run (decisión)**

| Opción | Cómo | Costo para el demo | Contras |
|---|---|---|---|
| **A. CPU siempre asignada** (elegida) | `--no-cpu-throttling --min-instances=0 --max-instances=1`. El temporizador corre en memoria tras responder 200 | Facturación por instancia mientras vive (hasta ~15 min tras la última request). Con tráfico de demo entra en la capa gratuita (180 000 vCPU-s/mes) | El estado del lote vive en una instancia: obliga a `max-instances=1`. Si la instancia muere, los mensajes quedan persistidos sin procesar |
| B. Cloud Tasks | El webhook encola una tarea con `schedule_time = deadline` (nombre `remitente+deadline`) hacia un endpoint interno que lee los no procesados del store y arma el lote | Facturación por request (CPU solo en requests) + 1 M de operaciones gratis | Otra pieza que configurar (cola, cuenta de servicio, autenticación del endpoint) |
| C. Esperar dentro de la request | El webhook duerme 8 s antes de responder | Barato | Meta espera un 200 rápido; reintenta y duplica. Descartada |

Con la CPU estrangulada (por defecto en Cloud Run) un temporizador después de la respuesta no tiene CPU garantizada y el lote podría no entregarse, por eso A exige `--no-cpu-throttling`. Para escalar a varias instancias se pasa a B: el `MessageBatcher` ya separa "cuándo vence" de "qué se entrega" y el store persistido permite rearmar el lote desde cualquier instancia.

## Criterios de aceptación
- **Dado** el verify token correcto, **cuando** Meta llama al `GET` con `hub.mode=subscribe`, **entonces** responde 200 con el `hub.challenge`.
- **Dado** un verify token incorrecto, **cuando** llega el `GET`, **entonces** responde 403.
- **Dado** un `POST` con firma válida y un mensaje de texto, **cuando** llega, **entonces** responde 200 y el mensaje queda persistido con remitente en E.164.
- **Dado** un `POST` sin firma o con firma inválida, **cuando** llega, **entonces** responde 401 y no se persiste nada.
- **Dado** el mismo `wamid` dos veces, **cuando** llegan ambos webhooks, **entonces** se persiste una vez y el handler recibe el mensaje una sola vez.
- **Dado** un webhook de estado (`delivered`, `read`), **cuando** llega, **entonces** responde 200 y no persiste ni entrega nada.
- **Dado** tres mensajes del mismo remitente con 3 s entre cada uno y ventana de 8 s, **cuando** pasan 8 s desde el último, **entonces** el handler recibe un solo lote con los tres en orden; y antes de eso no recibe nada.
- **Dado** mensajes de dos remitentes distintos, **cuando** vence la ventana, **entonces** se entrega un lote por remitente.
- **Dado** que la Graph API responde 400 o hay un error de red, **cuando** se envía, **entonces** `send` devuelve `ok=False` con el error y no lanza excepción.
- **Dado** un SMS simulado, **cuando** se envía, **entonces** queda en `sent` con la hora del `Clock` y se registra en el log.

## Tests
- `tests/unit/test_channels.py`: envío de texto y de plantilla (cuerpo de la request), error HTTP, error de red, normalización del destinatario, SMS simulado (registro y `sent`), consola.
- `tests/unit/test_inbound.py`: store en memoria (dedup, `mark_processed`, `list_unprocessed`), `MessageBatcher` con `FixedClock` (ventana deslizante, remitentes separados), normalización de teléfonos (incluido `521` de México).
- `tests/unit/test_whatsapp_webhook.py`: verificación correcta e incorrecta, firma válida, inválida y ausente, deduplicación, estado ignorado, otro `phone_number_id` ignorado, agrupación de varios `POST` en un lote (con `httpx.AsyncClient` + reloj y `sleep` falsos), handler que falla deja el mensaje sin procesar, lotes pendientes entregados al apagar.
- `tests/integration/test_whatsapp_live.py` (`integration`): envía la plantilla `hello_world` al número en `WHATSAPP_TEST_RECIPIENT`; se salta si faltan las credenciales.

## Evaluación (solo si usa LLM)
No usa LLM.
