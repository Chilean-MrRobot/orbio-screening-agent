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
- `CandidateRepository`: `get`, `get_by_phone`, `save`, `list(status)`.
- `LLMClient`: `generate_text` y `generate_structured(schema=...)`. Ambos devuelven `LLMUsage` (modelo, tokens, latencia, costo) para las métricas. Las fallas se reportan con `LLMError`.
- `MessageChannel`: `send(OutboundMessage) -> SendResult`. Un envío fallido devuelve `ok=False`, no lanza excepción.

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
