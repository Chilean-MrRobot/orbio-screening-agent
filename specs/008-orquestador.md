# 008 — Orquestador de la conversación y persistencia

**Estado:** implementada
**Ola:** 2 · **Depende de:** 000, 001, 002, 003, 004, 005 · **La usa:** 009 (agente), 011 (calentamiento), 013 (CRM)

## Objetivo
Conducir la conversación de screening de forma determinista: decidir la etapa según la ficha guardada, aplicar lo que el agente extrajo, correr las reglas eliminatorias después de cada respuesta, calcular el puntaje al confirmar y guardar la ficha después de cada paso para que el CRM la vea en vivo. El LLM solo extrae y redacta (detrás de `ScreeningAgent`); todo lo demás es código.

## Alcance
- Incluye:
  - `ConversationOrchestrator` (`screening.conversation`): implementa `InboundHandler` y arranca la conversación de una postulación nueva (primer contacto).
  - Derivación de los campos que faltan por etapa a partir de las reglas, los componentes del puntaje y la lista de campos del diseño.
  - Reintentos por campo, `needs_review`, opt-out, pedido de humano, descarte con plantilla, cierre con confirmación y puntaje.
  - Registro del uso de tokens en el `timeline` y `usage_totals` para las métricas de costo.
  - `FirestoreCandidateRepository` y `FirestoreInboundMessageStore` (`screening.adapters.firestore`), con cliente inyectable.
  - Endpoint `POST /applications` y el cableado de `create_app_from_env` (repositorio Firestore o en memoria según el entorno).
- No incluye:
  - El agente real (spec 009): aquí hay un punto de inyección (`screening.api.wiring.load_agent`) y los tests usan un fake guionado.
  - Cancelar o programar toques de calentamiento (spec 011) ni el resumen post-conversación en `candidate.summary` (spec 012).
  - Responder después del cierre: si un candidato calificado o descartado vuelve a escribir, el mensaje se guarda y se registra, pero no se contesta (lo ve HR en el CRM).
  - Plantillas aprobadas por Meta: el primer contacto y los mensajes fijos se envían como texto libre (el número de prueba solo trae `hello_world`).
  - Autenticación del endpoint `/applications` (en producción el portal firmaría la llamada).
  - Concurrencia entre instancias: hay un candado por teléfono dentro del proceso, no distribuido.

## Interfaz

**`screening.conversation`**

```python
CONFIRMATION_FIELD = "summary_confirmed"  # pseudo-campo del cierre (ver "Cierre")
MAX_INVALID_ATTEMPTS = 2  # diseño §6


class ConversationOrchestrator:  # implementa InboundHandler
    def __init__(
        self,
        *,
        repository: CandidateRepository,
        agent: ScreeningAgent,
        channel: MessageChannel,
        clock: Clock,
        config: AppConfig,
        registry: SpecialConditionRegistry | None = None,
    ) -> None: ...

    def handle(self, batch: InboundBatch) -> None: ...  # un lote de un candidato
    def start(self, candidate: Candidate) -> bool: ...  # primer contacto
    def start_application(self, raw: dict[str, Any]) -> ApplicationStartResult: ...


class ApplicationStartResult(BaseModel):
    outcome: IntakeOutcome
    candidate_id: str | None
    status: CandidateStatus | None
    reason: IntakeReason | None
    errors: list[str]
    contacted: bool


def missing_fields(
    candidate: Candidate, stage: Stage, evaluation: KnockoutEvaluation | None, config: AppConfig
) -> list[str]: ...
def current_stage(
    candidate: Candidate, evaluation: KnockoutEvaluation | None, config: AppConfig
) -> Stage: ...
def usage_totals(candidate: Candidate) -> UsageTotals: ...  # suma de eventos llm_usage


class UsageTotals(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
```

**`screening.adapters.firestore`**

```python
class FirestoreCandidateRepository:  # implementa CandidateRepository
    def __init__(self, client: firestore.Client, collection: str = "candidates") -> None: ...


class FirestoreInboundMessageStore:  # implementa InboundMessageStore
    def __init__(self, client: firestore.Client, collection: str = "inbound_messages") -> None: ...
```

**`screening.api`**
- `create_app(..., applications: ApplicationStarter | None = None)`: si se entrega, agrega `POST /applications`.
- `create_app_from_env(agent: ScreeningAgent | None = None)`: arma el orquestador real. Variables nuevas: `SCREENING_REPOSITORY` (`memory` por defecto, o `firestore`), `FIRESTORE_PROJECT` y `FIRESTORE_DATABASE` (opcionales). Sin credenciales de WhatsApp, el canal es la consola.

## Comportamiento

### Campos por etapa
La etapa la decide la ficha guardada, no el historial: es la **primera** de `welcome → knockout → profile` con campos faltantes; si no falta nada, `closing`. Un campo falta si su valor es `None` y no está en `needs_review`.

| Etapa | Campos, en el orden a preguntar | Fuente |
|---|---|---|
| welcome | `full_name`, luego los `requires` de las reglas con `stage: welcome` (`consent`) | diseño §4 + reglas |
| knockout | `requires` de las reglas con `stage: knockout`, y además los campos que el motor reporte como faltantes en reglas pendientes (p. ej. `license_years` con carnet B y moto) | reglas |
| profile | primer tramo del `field` de cada componente del puntaje que no esté en una etapa anterior, en el orden del YAML, y luego `work_permit` | puntaje + diseño §4 |

`full_name` y `work_permit` son los campos "de dato" del diseño que no salen ni de reglas ni del puntaje; viven en una constante del módulo. Como las actualizaciones nunca vacían un campo, la etapa solo retrocede cuando una corrección hace aparecer un campo nuevo (p. ej. cambia el vehículo a moto con carnet B y ahora falta `license_years`).

### Un lote entrante (`handle`)
1. **Ficha:** se busca por teléfono. Si no existe, se crea una mínima (`source="whatsapp"`, `application_id="inbound-<wamid>"`, país deducido del prefijo con `normalize_phone`, `profile_name` como nombre de la postulación, `data.full_name` vacío para que se confirme) y se registra `candidate_created`.
2. **Persistir primero:** los mensajes se agregan a `candidate.messages` (los que ya estaban por `external_id` se ignoran: reprocesar es idempotente; si no queda ninguno nuevo, termina), se actualiza `last_candidate_message_at`, se registra `message_received` y **se guarda**.
3. **Estado:** `opted_out` → no se hace nada más (sin respuesta). `qualified`, `disqualified`, `interview`, `hired` o `invalid_data` → se registra `message_after_close` y no se responde. `new`, `contacted` o `no_response` → pasa a `in_screening`.
4. **Extracción:** `agent.extract(candidate, mensajes_nuevos, expected_fields)`, con los campos faltantes de la etapa actual (en el cierre, `[CONFIRMATION_FIELD]`). Se registra cada `LLMUsage` como `llm_usage` (`turn="extract"`). Se actualizan `language` (evento `language_changed` si cambia) y `sentiment`.
5. **Opt-out:** estado `opted_out` (motivo `opt_out`), se guarda y **no se envía nada**, ni ahora ni después.
6. **Actualizaciones:** cada campo no vacío de `updates` (salvo `needs_review`) sobrescribe el de la ficha; se registra `field_updated` con `field`, `value` y `previous`. Si el campo estaba en `needs_review`, sale de ahí.
7. **Inválidos:** por cada campo de `invalid_fields` que no vino válido en el mismo turno se registra `field_invalid` con `attempt` = intentos inválidos acumulados (se cuentan en el `timeline`, así que sobreviven entre lotes). Si `attempt < 2`, va a `retry_fields`; al segundo, el campo pasa a `needs_review` (evento `field_needs_review`) y la conversación sigue. `CONFIRMATION_FIELD` no se cuenta.
8. **Eliminatorias:** si hubo actualizaciones, `evaluate_knockouts` y evento `knockouts_evaluated` (`passed`, `pending`, `failed_rule`, `reason_code`). Si una regla falla: `candidate.knockout`, envío de su `message_template`, etapa `done`, estado `disqualified` con el `reason_code` como motivo. Si todas pasan, `candidate.knockout = passed`. Sin país configurado, las reglas no se evalúan (evento `knockouts_skipped`).
9. **Humano:** si `human_requested`, se marca la ficha, se registra `human_requested` y se envía el aviso `human_handoff`. En ese turno no se hace otra pregunta; la conversación sigue normal en los siguientes.
10. **Etapa:** se recalcula con `current_stage` (evento `stage_changed` del dominio).
11. **Cierre:** si la ficha ya estaba en `closing` antes del lote, el agente marcó `CONFIRMATION_FIELD` en `provided_fields`, no hubo actualizaciones y sigue sin faltar nada → se calcula el puntaje (`compute_score` con la fecha local de la región, a partir del `Clock`), evento `score_computed`, etapa `done`, estado `qualified` y envío de `qualified_next_steps`.
12. **Respuesta:** en cualquier otro caso, `agent.reply(ReplyRequest(...))` con `ask_fields` (faltantes de la etapa sin los de `retry_fields`), `retry_fields`, `question` (si el candidato preguntó, evento `question_received`) y `closing_summary=True` en el cierre. El texto se envía por el canal.

Cada envío agrega un `Message` con rol `agent` (si salió bien), actualiza `last_agent_message_at` y registra `message_sent` (`template`, `external_id`) o `message_failed` (`error`). **La ficha se guarda después de cada paso** (mensajes entrantes, extracción, eliminatorias, cada envío).

### Primer contacto (`start` y `start_application`)
- `start_application(raw)` llama a `process_application` (spec 002). Solo si el resultado es `created`, envía `first_contact`; si sale bien, el estado pasa a `contacted`. Duplicados, datos inválidos y rechazos no se contactan.
- `POST /applications` recibe el JSON del portal y responde `ApplicationStartResult`: 201 si se creó, 200 si es duplicado o con datos inválidos, 422 si se rechazó o el cuerpo no es un objeto JSON.

### Plantillas y textos fijos
- Se renderizan con `{first_name}` (primera palabra de `data.full_name` o del nombre de la postulación), `{agent_name}`, `{client_name}`, `{sla_hours}` y `{license_term}` (vocabulario `license` de la región), en el idioma del candidato. Una variable desconocida queda tal cual.
- `apology` y `human_handoff` no están en `templates.yaml` (los YAML existentes no se tocan desde esta feature): tienen un texto por defecto en `screening.conversation.messages`, y si algún día se agregan al YAML, el YAML gana. Se propone moverlos en el PR.
- El envío va como texto (`template=None`); la clave de la plantilla queda en el evento `message_sent`.

### Errores
Ningún error del agente, del canal o del motor tumba el proceso ni se propaga al despachador:
- Si `extract` o `reply` lanza, se registra `agent_error` (`turn`, `error`), se envía `apology` y se guarda.
- Si el canal devuelve `ok=False` o lanza, se registra `message_failed` y se intenta una sola vez enviar `apology` (salvo que lo que falló sea la propia disculpa).
- Cualquier otra excepción se registra como `orchestrator_error`, se intenta la disculpa y se guarda lo que se alcanzó.

### Costo en tokens
Cada `LLMUsage` de `extract` y `reply` queda en el `timeline` como `llm_usage` (`turn`, `model`, `input_tokens`, `output_tokens`, `latency_ms`, `cost_usd`). `usage_totals(candidate)` los suma para el CRM y las métricas. No hace falta otra colección.

### Firestore
- Fichas en `candidates/{candidate.id}` con `candidate.model_dump(mode="json")` (fechas en ISO 8601) y `model_validate` al leer. `get_by_phone` y `list(status)` usan consultas por igualdad sobre `phone` y `status`.
- Mensajes entrantes en `inbound_messages/{external_id}`: `add` usa `create`, que falla con `AlreadyExists` si el wamid ya existía → `False` (deduplicación atómica). `mark_processed` actualiza en un batch y `list_unprocessed` consulta `processed == False`.
- El cliente se inyecta; `create_app_from_env` crea `firestore.Client(project, database)` solo si `SCREENING_REPOSITORY=firestore`.

## Criterios de aceptación
- **Dado** un candidato contactado que responde a todo con datos válidos, **cuando** confirma el resumen, **entonces** queda `qualified`, etapa `done`, con `score` calculado con la fecha del `Clock` y recibe `qualified_next_steps`.
- **Dado** un candidato en la etapa eliminatoria, **cuando** responde algo que hace fallar cualquiera de las cuatro reglas, **entonces** recibe la plantilla de descarte de esa regla con su nombre (y el término de licencia de su país) y queda `disqualified` con el `reason_code` como motivo.
- **Dado** un candidato que pide no ser contactado, **cuando** llega el lote, **entonces** queda `opted_out`, no recibe mensaje y los lotes siguientes tampoco reciben respuesta.
- **Dado** un candidato que pide hablar con una persona, **cuando** llega el lote, **entonces** `human_requested=True`, hay evento `human_requested` y recibe el aviso.
- **Dado** un campo respondido de forma inválida, **cuando** es el primer intento, **entonces** el agente lo repregunta (`retry_fields`); **cuando** es el segundo, **entonces** queda en `needs_review` y se pregunta el siguiente campo.
- **Dado** un candidato en el cierre, **cuando** corrige un dato, **entonces** el campo se sobrescribe, se vuelven a correr las reglas y se pide de nuevo el resumen (sin calificar todavía).
- **Dado** un candidato que hace una pregunta, **cuando** llega el lote, **entonces** `reply` recibe la pregunta junto con los campos pendientes.
- **Dado** que el agente o el canal fallan, **cuando** llega el lote, **entonces** `handle` no lanza, los mensajes entrantes quedan guardados, hay evento de error y se intenta la disculpa.
- **Dado** cualquier lote, **cuando** se procesa, **entonces** la ficha guardada tiene en el `timeline` los mensajes, campos, reglas, envíos y cambios de estado o etapa, y el uso de tokens.
- **Dado** un JSON válido del portal, **cuando** llega a `POST /applications`, **entonces** responde 201, la ficha queda `contacted` y el candidato recibe `first_contact`.

## Tests
- `tests/unit/conversation/test_stages.py`: campos por etapa (welcome, knockout, profile), `license_years` dinámico, `needs_review` excluido y `current_stage`.
- `tests/unit/conversation/test_orchestrator.py`: conversación completa calificada, descarte por cada regla (parametrizado), opt-out, humano, inválido dos veces, corrección en el cierre, pregunta, error del agente (extract y reply), error del canal (`ok=False` y excepción), candidato nuevo por WhatsApp, lote repetido, mensaje después del cierre, `timeline` guardado en cada paso y uso de tokens.
- `tests/unit/conversation/test_start.py`: primer contacto, `start_application` por resultado de intake y el endpoint `POST /applications`.
- `tests/unit/test_firestore.py`: ambos adaptadores contra un cliente Firestore falso en memoria.
- `tests/unit/test_wiring.py`: `build_storage` por entorno y `create_app_from_env` con un agente inyectado.
- `tests/integration/test_firestore_emulator.py` (`integration`): ida y vuelta real; se salta sin `FIRESTORE_EMULATOR_HOST`.

## Evaluación
No llama al LLM directamente: los casos del golden dataset de extracción y respuesta son de la spec 009.

## Propuestas para el PR (contratos que no se tocan aquí)
- Agregar `apology` y `human_handoff` a `config/templates.yaml`.
- Mover `MAX_INVALID_ATTEMPTS` a la configuración.
- Agregar a `ExtractionOutcome` un campo explícito `confirmed: bool` para el cierre en vez del pseudo-campo `summary_confirmed`.
