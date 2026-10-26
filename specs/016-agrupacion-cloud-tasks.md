# 016 — Agrupación de mensajes con Cloud Tasks

**Estado:** implementada
**Ola:** 4 (escalabilidad) · **Depende de:** 005, 008 · **Coordina con:** 015 (lease por candidato)

## Objetivo

Que la agrupación de mensajes de WhatsApp funcione con varias instancias de Cloud Run y con CPU solo durante la request. Hoy el lote vive en la memoria del proceso y se entrega con temporizadores `asyncio` después de responder (opción A de la spec 005); se pasa a la opción B: el webhook persiste y **encola una tarea en Cloud Tasks**, y la tarea arma el lote leyendo la base.

## Alcance

- **Incluye:** la interfaz `TaskQueue` con `CloudTasksQueue` (producción) y `LocalTaskQueue` (desarrollo y tests); el cambio del webhook para encolar en vez de procesar; el endpoint `POST /tasks/process-inbound`; la consulta de no procesados por remitente en los stores; la señal `CandidateBusyError` que espera de la spec 015; la selección de la cola por entorno; el comando para crear la cola.
- **No incluye:** crear la cola ni desplegar (se hace al desplegar, comando en §Despliegue); verificar el token OIDC en la app (ver §Autenticación); el lease en sí (spec 015).
- **Se elimina:** `BatchDispatcher` y `MessageBatcher` (temporizadores y estado en memoria).

## Interfaz

```python
# screening.tasks
class TaskQueue(Protocol):
    def enqueue(
        self, path: str, payload: dict[str, Any], *, delay_seconds: float, task_id: str
    ) -> bool:
        """Programa un POST JSON a `path` dentro de `delay_seconds`.

        Devuelve False si ya existía una tarea con ese `task_id` (no se crea otra).
        Lanza si la cola no está disponible.
        """

class CloudTasksQueue:   # google-cloud-tasks; tarea HTTP a `service_url + path`
class LocalTaskQueue:    # en el proceso: duerme `delay_seconds` y hace el POST a la app ASGI
class TaskQueueSettings(BaseModel):  # leída del entorno (ver §Variables)

# screening.api.inbound_tasks  (antes dispatcher.py)
PROCESS_INBOUND_PATH = "/tasks/process-inbound"
def inbound_task_id(sender: str, now: datetime, window_seconds: float) -> str
class InboundScheduler:          # lo usa el webhook: encola la tarea del remitente
    def schedule(self, sender: str) -> bool
class InboundBatchProcessor:     # lo usa el endpoint
    def process(self, sender: str) -> ProcessOutcome   # processed | empty | busy | failed
def build_inbound_tasks_router(processor, token: SecretStr) -> APIRouter

# screening.channels
class CandidateBusyError(Exception): ...           # la lanza el handler (spec 015)
class InboundMessageStore(Protocol):
    def list_unprocessed_for(self, sender: str) -> list[InboundMessage]: ...   # nuevo
```

Payload de la tarea: `{"sender": "+34600111222"}`. Cabecera: `X-Task-Token: <TASKS_TOKEN>`.

## Comportamiento

**Webhook (`POST /webhooks/whatsapp`)** — igual que en la spec 005 hasta la persistencia; luego:
1. Por cada mensaje, `store.add`. Si es nuevo, su remitente queda pendiente de encolar. Si es un duplicado **todavía sin procesar**, también (cubre el caso en que se persistió pero falló el encolado y Meta reintenta). Un duplicado ya procesado se ignora.
2. Por cada remitente pendiente (uno por remitente aunque el webhook traiga varios mensajes), `InboundScheduler.schedule(sender)`: encola `POST /tasks/process-inbound` con retraso `WHATSAPP_BATCH_SECONDS` y `task_id = inbound_task_id(sender, clock.now(), ventana)`.
3. Responde 200. Si la cola falla, 500 para que Meta reintente (el mensaje ya está persistido y el paso 1 lo vuelve a encolar).

**Nombre de la tarea (deduplicación)**
- `inbound_task_id = f"{sha256(sender)[:16]}-inbound-{bucket}"`, con `bucket = floor(epoch(now) / ventana)`. El hash va primero porque Cloud Tasks recomienda nombres no secuenciales, y evita poner el teléfono (dato personal) en el nombre.
- Todas las llegadas del mismo remitente dentro de un mismo intervalo de la ventana producen **una sola tarea**: Cloud Tasks rechaza el nombre repetido (`AlreadyExists` → `enqueue` devuelve `False`, no es error). Los reintentos de Meta también caen en el mismo nombre.
- `now` se toma **después** de persistir. Así la tarea del intervalo, programada en `t0 + ventana` (t0 = primera llegada del intervalo), corre después de que todo mensaje del intervalo esté persistido: `persistido ≤ now < fin del intervalo ≤ t0 + ventana`.

**Ventana: fija, no deslizante (decisión).** Con Cloud Tasks no se puede mover la hora de una tarea creada, así que cada intervalo de `ventana` segundos tiene su tarea. Un mensaje espera entre 0 y `ventana` segundos tras el último del intervalo (antes, siempre `ventana` tras el último). Si el candidato escribe durante más de una ventana, puede llegar en dos lotes seguidos; el segundo lo procesa la tarea siguiente y el lease (015) los serializa. Alternativa documentada: al ejecutar, si el último pendiente es más reciente que la ventana, encolar otra tarea y responder 200 (ventana deslizante con tope); no se implementa en el demo.

**Endpoint `POST /tasks/process-inbound`**
1. `X-Task-Token` ausente o distinto → 401 (comparación en tiempo constante). Cuerpo inválido → 400.
2. `store.list_unprocessed_for(sender)`. Vacío → **200** `{"status": "empty"}` (idempotente: una tarea repetida o la tarea siguiente que ya no encuentra nada).
3. Ordena por `sent_at` y `received_at`, arma el `InboundBatch` y llama a `handler.handle` en un hilo.
4. Si termina bien, `mark_processed` de esos IDs → 200 `{"status": "processed", "messages": n}`.
5. `CandidateBusyError` → **503** con `Retry-After`, sin marcar nada (Cloud Tasks reintenta con backoff). Cualquier otra excepción → 503 y se registra con traza (también se reintenta, hasta `max-attempts`; después los mensajes quedan sin procesar y visibles en el log).
6. Mensajes que llegan mientras corre el lote: no están en el lote leído, quedan sin procesar y su llegada creó (o encontró) la tarea de su intervalo, que corre después → entran en el lote siguiente. Si dos tareas del mismo remitente se solapan, el orquestador deduplica por `external_id` y el lease (015) las serializa.

**Señal de candidato ocupado (contrato con la spec 015).** `CandidateBusyError` vive en `screening.channels.inbound`. El handler la **lanza** (no la traga) cuando no obtiene el lease del candidato. Si 015 define su propia excepción, basta con que herede de esta.

**`LocalTaskQueue` (desarrollo y tests).** Mantiene el uso local igual que antes (`uvicorn ... --factory` sin GCP): deduplica por `task_id` en memoria, duerme `delay_seconds` con un `sleep` inyectable y hace el POST a la propia app vía ASGI con el mismo token. Si la respuesta no es 2xx, reintenta con backoff (1 s, 2 s, 4 s…) hasta 5 intentos, como Cloud Tasks. Al apagar la app, ejecuta de inmediato una vez las tareas pendientes. Tiene temporizadores en memoria, por eso **no** se usa en producción.

**Autenticación.** El endpoint siempre exige `X-Task-Token`. En Cloud Tasks la cabecera se agrega a la tarea; con la cola local, si no hay `TASKS_TOKEN` se genera uno aleatorio al arrancar (solo lo conocen la cola y el endpoint). Con `TASKS_BACKEND=cloud`, `TASKS_TOKEN` es obligatorio: sin él la app no arranca. Opcionalmente (`CLOUD_TASKS_SERVICE_ACCOUNT`) la tarea lleva además un token OIDC de esa cuenta de servicio; verificarlo en la app (o dejar el servicio privado con `roles/run.invoker`) es el paso siguiente, no se implementa porque el servicio es público por el webhook de Meta.

**Selección por entorno** (`build_task_queue` en `wiring.py`): `TASKS_BACKEND=local` (por defecto) o `cloud`.

## Variables

| Variable | Uso |
|---|---|
| `TASKS_BACKEND` | `local` (por defecto) o `cloud` |
| `TASKS_TOKEN` | Token compartido en `X-Task-Token`; obligatorio con `cloud` |
| `CLOUD_TASKS_PROJECT` | Proyecto (`gentle-app-313016`) |
| `CLOUD_TASKS_LOCATION` | Región de la cola (`europe-west1` por defecto) |
| `CLOUD_TASKS_QUEUE` | Nombre de la cola (`inbound-batches` por defecto) |
| `TASKS_SERVICE_URL` | URL pública del servicio de Cloud Run, destino de las tareas |
| `CLOUD_TASKS_SERVICE_ACCOUNT` | Opcional: cuenta de servicio para el token OIDC |

## Despliegue

```bash
gcloud tasks queues create inbound-batches \
  --project=gentle-app-313016 --location=europe-west1 \
  --max-dispatches-per-second=20 --max-concurrent-dispatches=50 \
  --max-attempts=10 --min-backoff=2s --max-backoff=60s --max-doublings=4 \
  --max-retry-duration=15m
```

- Reintentos: backoff 2 s → 60 s, hasta 10 intentos o 15 min. Cubre un lease ocupado (una respuesta del LLM tarda segundos) y caídas breves de OpenAI o Firestore, sin reintentar para siempre un lote roto.
- Límites: 20 despachos/s y 50 concurrentes protegen a OpenAI y a Cloud Run de picos; 10K postulaciones/semana con ~10 lotes por candidato son ~0,2 tareas/s de media.
- La cuenta que corre Cloud Run necesita `roles/cloudtasks.enqueuer` (y `roles/iam.serviceAccountUser` sobre la cuenta OIDC si se usa).
- Costo: el primer millón de operaciones al mes es gratis; Cloud Run puede volver a CPU estrangulada y `max-instances > 1`.

## Criterios de aceptación

- **Dado** un mensaje nuevo con firma válida, **cuando** llega al webhook, **entonces** se persiste, se encola una tarea a `/tasks/process-inbound` con `{"sender": ...}` y retraso = ventana, y el handler **no** se llama en la request.
- **Dado** tres mensajes del mismo remitente en el mismo intervalo, **cuando** llegan, **entonces** se crea una sola tarea (mismo `task_id`) y al ejecutarse el handler recibe un solo lote con los tres en orden.
- **Dado** dos instantes en intervalos distintos, **cuando** se calcula `inbound_task_id`, **entonces** los nombres difieren; en el mismo intervalo y remitente son iguales; con remitentes distintos difieren; y no contienen el teléfono.
- **Dado** un duplicado de Meta de un mensaje aún sin procesar, **cuando** llega, **entonces** se vuelve a intentar encolar; si ya está procesado, no.
- **Dado** que la cola falla, **cuando** llega el webhook, **entonces** responde 500 y el mensaje queda persistido.
- **Dado** el endpoint sin token o con token incorrecto, **cuando** se llama, **entonces** 401 y no se procesa nada.
- **Dado** pendientes del remitente, **cuando** se llama al endpoint con token, **entonces** el handler recibe el lote, los mensajes quedan procesados y responde 200.
- **Dado** que no hay pendientes, **cuando** se llama al endpoint, **entonces** 200 y el handler no se llama.
- **Dado** que el handler lanza `CandidateBusyError` u otra excepción, **cuando** se procesa, **entonces** 503 y los mensajes siguen sin procesar.
- **Dado** un mensaje que llega después de leído el lote, **cuando** corre la tarea siguiente, **entonces** se entrega en ese lote siguiente; nunca se pierde.
- **Dado** `CloudTasksQueue`, **cuando** encola, **entonces** crea en `projects/{p}/locations/{l}/queues/{q}` una tarea HTTP POST a `service_url + path` con cuerpo JSON, `X-Task-Token`, `schedule_time = now + retraso` y nombre `.../tasks/{task_id}`; con `AlreadyExists` devuelve `False`.
- **Dado** `TASKS_BACKEND=cloud` sin `TASKS_TOKEN` o sin `TASKS_SERVICE_URL`, **cuando** arranca, **entonces** falla con un error claro.

## Tests

- `tests/unit/test_task_queue.py`: `CloudTasksQueue` con un cliente fake (forma de la tarea, OIDC opcional, `AlreadyExists` → `False`); `LocalTaskQueue` (dedup por `task_id`, ejecuta tras el retraso, reintenta ante 503, ejecuta lo pendiente al cerrar); `TaskQueueSettings.from_env` y validación de `cloud`.
- `tests/unit/test_inbound_tasks.py`: `inbound_task_id` (mismo intervalo, intervalo distinto, remitentes distintos, sin teléfono); endpoint con y sin token; lote de varios mensajes; sin pendientes → 200; ocupado → 503; error → 503; mensajes tardíos → lote siguiente.
- `tests/unit/test_whatsapp_webhook.py`: el webhook encola en vez de procesar (cola fake), una tarea por remitente, duplicado sin procesar se reencola, fallo de la cola → 500; flujo completo con `LocalTaskQueue` y `sleep` falso.
- `tests/unit/test_inbound.py` y `test_firestore.py`: `list_unprocessed_for` en memoria y en Firestore (fake).

## Escalar

- Estado solo en Firestore y en la cola; ninguna instancia guarda lotes ni temporizadores.
- `list_unprocessed_for` es una consulta por igualdad (`sender`, `processed`) que Firestore resuelve con índices simples, sin recorrer la colección.
- El siguiente paso es la ventana deslizante con tope (ver arriba) y verificar OIDC en la app.
