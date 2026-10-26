# 000 — Contratos base

**Estado:** implementada
**Ola:** 0 · **Depende de:** —

## Objetivo
Definir los modelos y las interfaces que comparten todas las features, para que varios agentes puedan desarrollar en paralelo sin inventar versiones distintas de lo mismo.

## Alcance
- Incluye: modelos de dominio, interfaces de infraestructura, reloj inyectable y un repositorio en memoria.
- No incluye: lógica de negocio (reglas, puntaje, conversación) ni adaptadores reales.

## Interfaz

**`screening.domain`**
- `ApplicationPayload`: JSON del portal. Solo `application_id` y `source` son obligatorios; el resto puede faltar y lo resuelve el intake.
- `ScreeningData`: campos que llena la conversación, todos opcionales hasta recogerse, más `needs_review`.
- `Candidate`: la ficha completa (estado, etapa, datos, mensajes, resultado eliminatorio, puntaje, resumen, calentamiento y `timeline`).
- Enums: `Country`, `Language`, `Stage`, `CandidateStatus`, `VehicleType`, `Availability`, `Schedule`, `Sentiment`, `ChannelName`, `MessageRole`.
- `Candidate.set_status`, `set_stage` y `record_event` registran cada cambio en `timeline` para que el CRM se actualice en vivo.

**`screening.ports`**
- `CandidateRepository`: `get`, `get_by_phone`, `save`, `list(status)` (recorre todo: solo tests, seed y scripts) y, para escalar (bloque de escalabilidad):
  - `query(CandidateQuery) -> CandidatePage`: filtros por `statuses`, `country`, `stage` y `touch_due_before` (`reengagement.next_touch_at` ≤ instante), orden `updated_desc` · `score_desc` · `next_touch_asc` (desempate por `id`), `limit` ≤ 500 y paginación con `cursor` opaco. Las fichas sin puntaje van al final de `score_desc`; las sin próximo toque no entran en `next_touch_asc`. Un cursor de otro orden lanza `InvalidCursorError`.
  - `count_by_status()`: conteo por estado con agregaciones, sin leer las fichas.
  - En Firestore se guardan campos de índice `idx_*` junto a la ficha; los índices compuestos están en `deploy/firestore.indexes.json`.
- `LeaseStore`: `try_acquire(key, owner, now, ttl)` atómico (renueva si es el mismo dueño, toma el lease si venció) y `release(key, owner)` (solo el dueño). Memoria para tests; Firestore con transacción.
- `LLMClient`: `generate_text` y `generate_structured(schema=...)`. Ambos devuelven `LLMUsage` (modelo, tokens, latencia, costo) para las métricas. Las fallas se reportan con `LLMError`.
- `MessageChannel`: `send(OutboundMessage) -> SendResult`. Un envío fallido devuelve `ok=False`, no lanza excepción.

- `KnowledgeBase` (ola 2): `search(query, limit) -> list[KnowledgeSnippet]`.
- `ScreeningAgent` (ola 2), independiente del framework (la implementación usa Google ADK):
  - `extract(candidate, new_messages, expected_fields) -> ExtractionOutcome`: turno de extracción. Devuelve solo los campos entregados o corregidos, los inválidos, el idioma, el sentimiento, si pidió opt-out o un humano, una pregunta para la FAQ y el uso de tokens.
  - `reply(ReplyRequest) -> ReplyOutcome`: turno de respuesta. El orquestador decide qué preguntar (`ask_fields`), qué repreguntar (`retry_fields`), si hay que responder una pregunta y si toca el resumen de cierre; el agente solo redacta.

**`screening.clock`**
- `Clock` (`now()`), `SystemClock` y `FixedClock` (con `advance()`), siempre con zona horaria.

**`screening.adapters.memory`**
- `InMemoryCandidateRepository`: guarda copias profundas, como una base de datos real.

## Comportamiento
- Todas las fechas son con zona horaria (UTC al persistir).
- Cambiar el estado o la etapa al mismo valor no genera evento.
- Los contratos no se modifican desde una feature; un cambio se propone en el PR.

## Criterios de aceptación
- **Dado** un candidato en `new`, **cuando** pasa a `contacted`, **entonces** `timeline` tiene un evento `status_changed` con el estado anterior y el nuevo, y `updated_at` es el instante del cambio.
- **Dado** un candidato guardado en el repositorio en memoria, **cuando** se modifica la instancia original sin guardar, **entonces** el repositorio sigue devolviendo la versión guardada.
- **Dado** un `FixedClock`, **cuando** se avanza 24 horas, **entonces** `now()` devuelve el instante +24 h.

## Tests
`tests/unit/test_domain.py`, `tests/unit/test_memory_repository.py`, `tests/unit/test_clock.py`.
