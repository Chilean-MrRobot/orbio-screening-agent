# 002 — Intake del JSON del portal

**Estado:** implementada
**Ola:** 1 · **Depende de:** 000, 001

## Objetivo
Convertir el JSON que entrega el portal de empleo en una ficha de candidato lista para que el agente la contacte (etapa 0 del diseño, sin IA): validar, normalizar el teléfono, evitar fichas duplicadas y dejar a la vista de HR lo que no se puede contactar.

## Alcance
- Incluye: validación del payload, normalización del teléfono a E.164, inferencia del país, detección de duplicados por teléfono, creación de la ficha y registro en `timeline`.
- No incluye: el endpoint HTTP (se cablea en la ola 2), el envío del primer WhatsApp, la deduplicación de fichas sin teléfono, ni validar el formato del email.

## Interfaz

**`screening.intake`**

```python
def process_application(
    raw: dict[str, Any],
    *,
    repository: CandidateRepository,
    clock: Clock,
    config: AppConfig,
) -> IntakeResult: ...
```

```python
class IntakeOutcome(StrEnum):
    CREATED = "created"  # ficha nueva en `new`
    DUPLICATE = "duplicate"  # ya existía una ficha con ese teléfono
    INVALID_DATA = "invalid_data"  # ficha creada en `invalid_data` para HR
    REJECTED = "rejected"  # payload inutilizable, sin ficha


class IntakeReason(StrEnum):
    INVALID_PAYLOAD = "invalid_payload"
    PHONE_MISSING = "phone_missing"
    PHONE_INVALID = "phone_invalid"
    COUNTRY_UNDETERMINED = "country_undetermined"
    COUNTRY_NOT_SERVED = "country_not_served"


class IntakeResult(BaseModel):
    outcome: IntakeOutcome
    candidate: Candidate | None  # None solo si es REJECTED
    reason: IntakeReason | None  # None si es CREATED o DUPLICATE
    errors: list[str]  # detalle legible (validación, teléfono)
```

`normalize_phone(raw, country, regions) -> PhoneResult` es la función pura de normalización y se expone para reutilizarla (p. ej. el webhook de WhatsApp).

## Comportamiento

**Validación del payload**
- Se valida con `ApplicationPayload` (los campos extra se ignoran).
- Si falta `application_id` o `source`, o vienen vacíos, se rechaza (`REJECTED`, `invalid_payload`) sin crear ficha.
- Si un campo **opcional** viene con un valor inválido (p. ej. `country: "AR"` o una fecha mal formada), se descarta ese campo, se procesa el resto y se registra un evento `fields_ignored` con los nombres. Así un dato secundario malo no deja a un postulante sin ficha.
- Los textos se recortan; un texto vacío cuenta como ausente.

**Teléfono y país**
- Los prefijos válidos son los `phone_prefix` de `config/regions/*.yaml`; no hay países hardcodeados.
- Si el teléfono viene en formato internacional (`+…` o `00…`), el país se deduce del prefijo. Si el prefijo no es de ningún país configurado → `country_not_served`.
- Si viene en formato local:
  - con `country` en el payload, se interpreta en ese país;
  - sin `country`, se prueba con cada región configurada y se acepta si es válido en exactamente una; si es válido en ninguna → `phone_invalid`, y si es válido en varias → `country_undetermined`.
- Se acepta cualquier número que `phonenumbers` considere válido (espacios, guiones y paréntesis se toleran). No se distingue fijo de móvil: en México `phonenumbers` no puede hacerlo.
- `candidate.country` es el del payload; si no viene, el inferido del teléfono. Si ambos existen y no coinciden, se respeta el del payload (es donde postuló) y se registra un evento `country_mismatch`.

**Ficha**
- Ficha válida: estado `new`, etapa `welcome`, `phone` en E.164 y `data.full_name` prellenado con el nombre del portal (el agente lo confirma en la bienvenida). **No** se prellenan campos eliminatorios (p. ej. la ciudad del portal queda solo en `application.city`; la confirma la conversación).
- Teléfono ausente o inválido: se crea la ficha igual, con `phone = None` y estado `invalid_data` (el evento `status_changed` lleva el motivo). El evento `application_received` incluye el email si existe, para que HR pueda contactar por otra vía.
- Duplicado: si `get_by_phone` encuentra una ficha, no se crea otra. Se registra `duplicate_application` en la existente (con `application_id`, `source`, `vacancy_id` y `applied_at` de la nueva postulación) y se devuelve esa ficha sin cambiar su estado.
- Todas las fechas (`created_at`, `updated_at`, eventos) salen del `Clock` inyectado.

**Eventos de `timeline`**

| Evento | Cuándo | Detalle |
|---|---|---|
| `application_received` | Ficha nueva (válida o inválida) | `application_id`, `source`, `email` |
| `fields_ignored` | Se descartaron campos opcionales inválidos | `fields` |
| `country_mismatch` | País del payload ≠ país del teléfono | `payload_country`, `phone_country` |
| `status_changed` | Ficha inválida (`new` → `invalid_data`) | `reason` |
| `duplicate_application` | Postulación repetida | `application_id`, `source`, `vacancy_id`, `applied_at` |

## Criterios de aceptación
- **Dado** un payload de España con teléfono `+34 600 111 222`, **cuando** se procesa, **entonces** se crea una ficha en `new` con teléfono `+34600111222`, país `ES` y `data.full_name` igual al nombre del portal.
- **Dado** un payload de México con teléfono local de 10 dígitos y `country: MX`, **cuando** se procesa, **entonces** el teléfono queda como `+52` seguido de los 10 dígitos.
- **Dado** un payload sin `country` y con teléfono `+52…`, **cuando** se procesa, **entonces** la ficha queda con país `MX`.
- **Dado** un payload sin teléfono y con email, **cuando** se procesa, **entonces** se crea una ficha en `invalid_data` con motivo `phone_missing` y el email en el evento `application_received`.
- **Dado** un teléfono que no es un número válido, **cuando** se procesa, **entonces** la ficha queda en `invalid_data` con motivo `phone_invalid`.
- **Dado** un teléfono con prefijo de un país no configurado (p. ej. `+54`), **cuando** se procesa, **entonces** la ficha queda en `invalid_data` con motivo `country_not_served`.
- **Dado** una ficha existente con `+34600111222`, **cuando** llega otra postulación con `600 111 222` y `country: ES`, **entonces** no se crea otra ficha y la existente tiene un evento `duplicate_application`.
- **Dado** un payload sin `application_id` o sin `source`, **cuando** se procesa, **entonces** se devuelve `rejected` y el repositorio sigue vacío.
- **Dado** un payload con campos desconocidos, **cuando** se procesa, **entonces** se ignoran y la ficha se crea normal.
- **Dado** un payload con `country: "AR"` y teléfono `+34…`, **cuando** se procesa, **entonces** se descarta el país, se infiere `ES` y queda un evento `fields_ignored`.

## Tests
`tests/unit/test_intake.py`: caso feliz ES y MX, teléfono con espacios o formato local, sin teléfono, teléfono inválido, país no atendido, país inferido (internacional y local), conflicto de país, duplicado, payload rechazado (sin `application_id`, sin `source`, vacíos), campos extra ignorados, campo opcional inválido descartado, fechas del `Clock` y que no se prellenan eliminatorios.

## Evaluación
No aplica: no usa LLM.
