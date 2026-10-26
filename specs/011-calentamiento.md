# 011 — Calentamiento de candidatos

**Estado:** implementada
**Ola:** 3 · **Depende de:** 000, 001, 005, 008

## Objetivo

Recuperar a los candidatos que se quedan en silencio, ya sea porque nunca respondieron al primer contacto o porque abandonaron a mitad del screening. Una tarea periódica envía los toques de `config/cadences.yaml` en su hora, por su canal y con su plantilla, respeta el horario hábil del país y cierra la ficha en *Sin respuesta* al final de la cadencia. Es lógica determinista: no usa LLM.

## Alcance

- **Incluye:** el servicio `run_due_touches` (`src/screening/reengagement/`), el hook en el orquestador que cancela los toques cuando el candidato responde, el endpoint `POST /tasks/reengagement` para Cloud Scheduler y el registro de cada toque en el `timeline`.
- **No incluye:** plantillas aprobadas en Meta (ver §Ventana de 24 h), transacciones entre el orquestador y la tarea (ver §Concurrencia), el índice `next_touch_at` en Firestore (ver §Escalar) ni la creación del job de Cloud Scheduler (es infraestructura; el comando va en §Despliegue).

## Interfaz

```python
# screening.reengagement
def run_due_touches(
    *,
    repository: CandidateRepository,
    channels: dict[ChannelName, MessageChannel],
    clock: Clock,
    config: AppConfig,
) -> ReengagementReport: ...


class TouchOutcome(StrEnum):
    SENT = "sent"  # se envió
    FAILED = "failed"  # el canal falló; se reintenta en la próxima pasada
    QUEUED = "queued"  # fuera de horario: queda para la próxima apertura
    SKIPPED = "skipped"  # fuera de horario sin encolar, o superado por un paso posterior


class TouchRecord(BaseModel):
    candidate_id: str
    cadence: str
    step: str
    channel: ChannelName
    outcome: TouchOutcome
    error: str | None = None


class ReengagementReport(BaseModel):
    evaluated: int  # fichas con una cadencia activa en esta pasada
    touches: list[TouchRecord]
    closed: list[str]  # fichas que pasaron a no_response
    errors: list[str]  # fichas que no se pudieron procesar (excepción inesperada)
```

`ReengagementService(repository=..., channels=..., clock=..., config=...)` envuelve la función con sus dependencias (`.run()`), para inyectarla en la API.

`POST /tasks/reengagement` (router `screening.api.reengagement`): exige el encabezado `X-Task-Token` igual a `REENGAGEMENT_TASK_TOKEN`. Responde `200` con el `ReengagementReport`, `401` si el token falta o no coincide y `503` si la variable no está configurada (la tarea queda deshabilitada en vez de abierta).

## Comportamiento

### Qué cadencia aplica

En cada pasada se leen las fichas con un toque vencido (spec 017) y se decide:

| Ficha | Cadencia | Inicio del silencio |
|---|---|---|
| `contacted` sin mensajes del candidato | `never_responded` | primer mensaje del agente |
| `new` con teléfono y sin mensajes del candidato (el primer contacto falló) | `never_responded` | `created_at` |
| `in_screening` cuyo último mensaje es del agente | `abandoned_mid_screening` | `last_agent_message_at` |
| Estado final (`qualified`, `disqualified`, `opted_out`, `invalid_data`, `no_response`, `interview`, `hired`), `human_requested`, sin teléfono, sin horario hábil para su país, o `in_screening` con el último mensaje del candidato (le toca responder al agente) | ninguna | — |

Los tiempos de cada paso (`after_hours`) cuentan desde el inicio del silencio. Al arrancar una cadencia se registra `reengagement_started` en el `timeline` con `cadence`, `silence_started_at` y `stage` (la etapa en que quedó), y ese evento es el ancla de los pasos siguientes: los toques también son mensajes del agente y moverían `last_agent_message_at`.

Al arrancar `never_responded` se dan por enviados los primeros pasos cuya plantilla ya tiene un `message_sent` desde el inicio del silencio: el `first_contact` (0 h) lo envía el orquestador al postular y no se repite.

`ReengagementState` guarda la cadencia, el próximo paso (`next_step_index`), cuándo toca (`next_touch_at`: la hora nominal del paso, que se mueve a la próxima apertura si al vencer cae fuera de horario) y cuántos toques salieron (`touches_sent`). Si la cadencia guardada ya no es la que corresponde, o el candidato escribió después del ancla, la cadencia se reinicia (defensa por si el hook no corrió).

### Envío de un paso

- Si `next_touch_at` aún no llega, no se hace nada.
- **Pasos atrasados:** si varios pasos ya vencieron (la tarea estuvo caída, o un paso falló hasta que llegó la hora del siguiente), se envía solo el último vencido y los anteriores se registran como `skipped` con `reason: superseded`. Nunca se manda una ráfaga.
- **Horario hábil** (`business_hours` del país, hora local, días 1 = lunes … 7 = domingo, `[start_hour, end_hour)`): se evalúa en el momento del envío. Fuera de horario, si `queue_outside_hours` es verdadero, `next_touch_at` pasa a la próxima apertura (evento `reengagement_touch_queued`, una sola vez por apertura); si es falso, el paso se salta (`reengagement_touch_skipped`, `reason: outside_business_hours`) y se agenda el siguiente.
- El texto se arma con `render_template` (el mismo del orquestador): idioma del candidato (`language`) y variables de la región (`first_name`, `agent_name`, `client_name`, `license_term`, `sla_hours`).
- Se envía por el canal del paso (`channels[step.channel]`). Si el canal no está en el diccionario, cuenta como falla.
- **Éxito:** se agrega el mensaje del agente a `messages`, se actualiza `last_agent_message_at`, se registra `reengagement_touch_sent` (cadencia, paso, canal, plantilla, `external_id`, `as_template`) y se agenda el siguiente paso. Una ficha `new` pasa a `contacted`.
- **Falla** (resultado `ok=False` o excepción del canal): se registra `reengagement_touch_failed` con el error y el paso queda pendiente: se reintenta en la próxima pasada. Si mientras tanto vence el paso siguiente, se pasa a ese (ver pasos atrasados).
- **Cierre** (`closes: true`): tras el envío la ficha pasa a `no_response` (`reason: reengagement_closed`) y `next_touch_at` queda vacío. Si el envío del cierre falla o el paso se salta por horario, la ficha se cierra igual: el cierre a los 5 días es la decisión de negocio y no depende de que el mensaje llegue. Si el candidato escribe después, el orquestador lo retoma (ya lo hace con `no_response`).
- La ficha se guarda después de cada cambio. Una excepción inesperada con una ficha se registra en `errors` y la pasada sigue con las demás.

### Al responder y al volver a quedar en silencio

- **Hook en el orquestador** (`on_candidate_reply: cancel_pending`): al persistir un mensaje nuevo del candidato se reinicia `ReengagementState` y, si había una cadencia activa, se registra `reengagement_cancelled`.
- **Nuevo silencio** (`on_new_silence: restart_from_current_stage`): cuando el agente responde y el candidato vuelve a callar, la ficha está `in_screening` con el último mensaje del agente y arranca `abandoned_mid_screening` desde cero, anclada en esa última respuesta. La etapa no cambia: cuando el candidato vuelva, el orquestador sigue donde quedó.

### Ventana de 24 horas de WhatsApp

Meta solo permite mensajes libres dentro de las 24 h siguientes al último mensaje del candidato. Fuera de esa ventana solo se aceptan **plantillas aprobadas** por Meta (si no, error `#131047`), y el primer mensaje que inicia la empresa también tiene que ser una plantilla.

- **Cómo lo maneja el servicio:** en los pasos de WhatsApp, si el candidato nunca escribió o su último mensaje tiene 24 h o más, el `OutboundMessage` lleva `template` = clave de la plantilla y `template_params` = las variables que usa (con nombre). El canal de WhatsApp lo manda como mensaje de plantilla. Dentro de la ventana se manda como texto libre. El texto renderizado va siempre en `text` (lo usan el SMS, la consola y la ficha).
- **En producción:** cada plantilla de `templates.yaml` que se use fuera de la ventana se registra en Meta con el mismo nombre, en cada idioma, con parámetros con nombre (`{{first_name}}`…), en la categoría *utility*. El canal hoy fija el idioma de la plantilla por canal (spec 005); habría que pasarlo por mensaje.
- **En el demo, con el número de prueba:** solo está aprobada `hello_world`, así que los toques fuera de la ventana fallan y quedan como `reengagement_touch_failed` en el `timeline` (visible en el CRM). Los recordatorios de la cadencia por defecto (24 h o más) siempre caen fuera de la ventana. Para mostrar el flujo en vivo hay dos opciones: una copia de `config/` con `after_hours` en fracciones de hora (`SCREENING_CONFIG_DIR`), para que los toques caigan dentro de la ventana, o mirar el SMS simulado y la consola, que no tienen esa restricción.

### Concurrencia

El orquestador y la tarea leen, modifican y guardan la ficha completa. Si el candidato escribe justo durante una pasada, una de las dos escrituras puede pisar a la otra. En el demo la ventana es de milisegundos y se acepta; en producción se usaría una transacción de Firestore (o una actualización por campos) para `reengagement` y `messages`. Lo mismo vale para el primer contacto: una pasada entre que el intake guarda la ficha `new` y el orquestador envía el `first_contact` podría duplicarlo.

### Escalar

> Los puntos 1 a 3 están implementados en la spec 017 (`specs/017-calentamiento-por-indice.md`): la pasada consulta por `next_touch_at`, paginada y con tope, y el orquestador agenda el próximo toque al enviar.

Antes cada pasada cargaba todas las fichas. Para escalar:

1. Consultar solo las fichas vencidas: `where("reengagement.next_touch_at", "<=", now)` con un índice en Firestore sobre `next_touch_at` (más `status` si se combina). Cuando un toque se encola, `next_touch_at` pasa a la próxima apertura, así que la consulta no devuelve una y otra vez las fichas que esperan el horario hábil.
2. Para eso, quien deja una ficha en silencio debe agendar el primer toque: el orquestador, al enviar, escribiría `next_touch_at` (hoy lo calcula la tarea en la pasada siguiente). Agregar `find_due(now)` a `CandidateRepository` es un cambio de contrato de la ola 0.
3. Procesar por lotes con paginación y, si una pasada no alcanza en 15 minutos, repartir el trabajo con Cloud Tasks (una tarea por ficha) en lugar de un bucle.
4. Idempotencia por toque: una clave `candidate_id + cadence + step + silence_started_at` evita duplicados si dos pasadas se solapan.

### Despliegue

Cloud Scheduler llama al endpoint cada 15 minutos:

```bash
gcloud scheduler jobs create http reengagement --schedule="*/15 * * * *" --uri="https://<servicio>/tasks/reengagement" --http-method=POST --headers="X-Task-Token=<token>"
```

En la API, la tarea usa el mismo canal de WhatsApp (o la consola) que el orquestador y el SMS simulado.

## Criterios de aceptación

- **Dado** un candidato contactado que no responde, **cuando** pasan 24, 72 y 120 h desde el primer contacto (en horario hábil), **entonces** se envían el recordatorio por WhatsApp, el SMS y el cierre, en ese orden, y la ficha queda `no_response`.
- **Dado** que el primer contacto ya salió, **cuando** corre la primera pasada, **entonces** no se reenvía.
- **Dado** un paso ya enviado, **cuando** se corre otra pasada a la misma hora, **entonces** no se repite.
- **Dado** un candidato en screening cuyo último mensaje es del agente, **cuando** pasan 24, 72 y 120 h, **entonces** se envían los pasos de `abandoned_mid_screening`.
- **Dado** un toque que vence fuera del horario del país, **cuando** `queue_outside_hours` es verdadero, **entonces** se envía en la próxima apertura (hora local de Madrid o de Ciudad de México); **cuando** es falso, **entonces** se salta y se registra.
- **Dado** que el envío falla, **cuando** corre la pasada, **entonces** se registra la falla sin cortar la pasada y el paso se reintenta en la siguiente.
- **Dado** un candidato con toques pendientes, **cuando** responde, **entonces** se cancelan; **cuando** el agente le contesta y vuelve a callar, **entonces** arranca `abandoned_mid_screening` desde esa respuesta.
- **Dado** el endpoint, **cuando** se llama sin el token correcto, **entonces** responde 401 y no corre la pasada.

## Tests

`tests/unit/reengagement/` (con `FixedClock`, repositorio en memoria y canales falsos):

- `never_responded`: nada antes de las 24 h, cada paso en su hora, sin duplicados, cierre en `no_response` y luego ignorada; `first_contact` no se reenvía; ficha `new` recibe el `first_contact` y pasa a `contacted`.
- `abandoned_mid_screening`: cada paso en su hora, desde `last_agent_message_at`.
- Horario hábil: MX fuera de horario se encola a las 9:00 de Ciudad de México; ES en domingo se encola al lunes 9:00 de Madrid (con el cambio de horario de octubre); sin encolar se salta; el cierre saltado cierra igual.
- Pasos atrasados: solo se envía el último vencido.
- Fallas: `ok=False` y excepción del canal se registran y se reintentan; canal no configurado; la pasada sigue con las demás fichas.
- Ventana de 24 h: plantilla fuera de la ventana, texto libre dentro.
- Idioma inglés y variables de la región.
- Fichas ignoradas: cada estado final, `human_requested`, sin teléfono, último mensaje del candidato.
- `tests/unit/conversation/test_reengagement_hook.py`: responder cancela los toques; nuevo silencio reinicia con `abandoned_mid_screening`.
- `tests/unit/test_reengagement_endpoint.py`: sin token, token incorrecto, token correcto y variable sin configurar.

## Evaluación

No aplica: no usa LLM.
