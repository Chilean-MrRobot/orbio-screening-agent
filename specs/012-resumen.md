# 012 — Resumen post-conversación

**Estado:** implementada
**Ola:** 3 · **Depende de:** 000, 001, 004, 006, 007, 008

## Objetivo

Cuando la conversación termina (`qualified` o `disqualified`), dejar en la ficha un resumen para el recruiter: los datos clave de un vistazo y 2 a 3 observaciones útiles que no se ven en los campos (dudas, tono, qué revisar). Diseño §7: "la ficha pasa a *Calificado* con un resumen generado automáticamente".

## Alcance

- Incluye: servicio `SummaryService` en `src/screening/summary/`, prompt `prompts/summary.md`, hook opcional en el orquestador, conexión en `api/wiring.py` y suite `summary` del golden dataset.
- No incluye: resumen de fichas `no_response`, `opted_out` o `invalid_data` (no hay conversación terminada que resumir); regenerar el resumen si la ficha cambia después; resumen en inglés para el recruiter (el CRM es en español).

## Interfaz

```python
# screening.summary
class KeyFacts(BaseModel):          # armado desde la ficha, nunca por el LLM
    full_name: str | None
    city: str | None
    country: Country | None
    license: str | None             # "B (3 años)" | "no tiene" | None
    vehicle: str | None             # "moto, propia"
    availability: str | None        # "jornada completa"
    preferred_schedule: str | None
    delivery_experience: str | None # "2 años (Glovo)" | "sin experiencia en reparto" | None
    start_date: date | None
    work_permit: bool | None
    status: CandidateStatus
    score: float | None
    max_score: float
    disqualification_reason: str | None   # texto legible del reason_code
    needs_review: list[str]
    human_requested: bool

class SummaryNotes(BaseModel):      # salida estructurada del LLM (validada con Pydantic)
    observations: list[str]         # 1 a 3 frases cortas

class CandidateSummary(BaseModel):
    key_facts: KeyFacts
    observations: list[str]
    source: Literal["llm", "fallback"]
    prompt_version: str
    error: str | None = None
    def render(self) -> str: ...    # texto que va a `candidate.summary`

class SummaryService:
    def __init__(self, llm: LLMClient, config: AppConfig, prompt: PromptTemplate | None = None): ...
    def summarize(self, candidate: Candidate) -> tuple[CandidateSummary, list[LLMUsage]]: ...
    def apply(self, candidate: Candidate, at: datetime) -> CandidateSummary: ...

# screening.conversation.orchestrator: lo único que conoce el orquestador. Vive ahí y no en
# `screening.summary` para evitar un import circular (el servicio usa `record_usage` de 008).
class Summarizer(Protocol):
    def apply(self, candidate: Candidate, at: datetime) -> object: ...

# screening.api.wiring
def load_summarizer(config, env=os.environ) -> SummaryService | None
```

`ConversationOrchestrator(..., summarizer: Summarizer | None = None)`.

## Comportamiento

- **Datos clave deterministas.** `KeyFacts` sale de `candidate.data`, `knockout`, `score` y `status`. Un dato que la ficha no tiene queda `None` y se muestra "no informado": nunca se completa con el LLM. La experiencia `None` es "no informado"; `years == 0` es "sin experiencia en reparto". El nombre usa `data.full_name` y si falta el del portal; la ciudad igual.
- **Motivo de descarte:** el `reason_code` se traduce a texto (`no_license` → "sin licencia de conducir vigente", etc.). Un código desconocido se muestra tal cual.
- **Observaciones del LLM:** `generate_structured` con `SummaryNotes`, el modelo `config.llm.conversation_model` y el prompt `prompts/summary.md` como `system`. El mensaje de usuario lleva los datos clave (JSON), la emoción detectada, los eventos relevantes del `timeline` (`question_received`, `field_invalid`, `field_needs_review`, `human_requested`, `language_changed`, `agent_error`, `message_failed`) y la transcripción (últimos 40 mensajes). El prompt exige 2 a 3 frases, solo con lo que está en la conversación, sin repetir los datos clave y sin inventar (si no habló de experiencia, no se menciona experiencia).
- **Salida:** `candidate.summary = summary.render()`; evento `summary_generated` con `summary.model_dump(mode="json")` para el CRM; el uso de tokens como eventos `llm_usage` con `turn="summary"` (`record_usage` de 008).
- **Respaldo:** si el LLM lanza (incluido `LLMError` por salida inválida), el resumen se arma solo con los datos clave (`source="fallback"`, `observations=[]`, `error=repr(exc)`), se registra el evento `summary_error` y se loguea. `apply` nunca lanza por el LLM.
- **Hook en el orquestador:** en `_qualify` y `_disqualify`, **después** de enviar el mensaje final (el candidato no espera al resumen), se llama `summarizer.apply(candidate, now)` y se guarda la ficha. Sin `summarizer`, no pasa nada. Si `apply` lanzara igual, se registra `summary_error` y el cierre sigue.
- **Wiring:** `load_summarizer` devuelve `SummaryService(OpenAILLMClient.from_env(), config)` si hay `OPENAI_API_KEY`; si no, `None`. `create_app_from_env` lo inyecta.

### Formato del texto

```
Lucía Pérez · Madrid (ES) · Calificado · Puntaje 82/100
Licencia: B (3 años) · Vehículo: moto, propia
Disponibilidad: jornada completa · Horario: tarde
Experiencia: 2 años (Glovo) · Inicio: 2026-10-26 · Permiso de trabajo: sí
Revisar: start_date
Pidió hablar con una persona.

Observaciones:
- Preguntó cómo se paga y si las propinas son para el repartidor.
- Tono cordial y respuestas rápidas.
```

Un descartado muestra `Descartado · Motivo: sin licencia de conducir vigente` en lugar del puntaje. Las líneas "Revisar" y "Pidió hablar con una persona" solo aparecen si aplican. En el respaldo, "Observaciones: no disponibles (no se pudo generar el resumen automático)".

## Criterios de aceptación

- **Dado** un candidato calificado con puntaje **cuando** se resume **entonces** `candidate.summary` tiene sus datos clave, el puntaje sobre `max_score` y las observaciones del LLM, y el `timeline` tiene `summary_generated` y un `llm_usage` con `turn="summary"`.
- **Dado** un candidato descartado por `no_license` **cuando** se resume **entonces** el texto muestra el motivo legible y no muestra puntaje.
- **Dado** un candidato con `needs_review=["start_date"]` **cuando** se resume **entonces** el texto lo lista en "Revisar" y el LLM recibe ese dato.
- **Dado** un candidato que pidió un humano **cuando** se resume **entonces** el texto lo indica.
- **Dado** que el LLM falla **cuando** se resume **entonces** `candidate.summary` tiene los datos clave, `source="fallback"`, hay un evento `summary_error` y no se lanza excepción.
- **Dado** un orquestador con `summarizer` **cuando** el candidato confirma el cierre o queda descartado **entonces** la ficha guardada tiene `summary`; **dado** uno sin `summarizer` **entonces** `summary` sigue en `None`.
- **Dado** un `summarizer` que lanza **cuando** se califica **entonces** la ficha igual queda `qualified` y se envía el mensaje final.

## Tests

`tests/unit/summary/`: datos clave y formato (calificado, descartado, `needs_review`, humano, experiencia ausente vs. cero, código de descarte desconocido); llamada al LLM (modelo, esquema, contexto con eventos y transcripción); respaldo ante `LLMError` y ante una salida que no valida; `apply` deja `summary`, eventos y uso; el prompt carga con versión. `tests/unit/conversation/test_summary_hook.py`: hook al calificar y al descartar, sin servicio, y servicio que lanza. `tests/unit/test_wiring.py`: `load_summarizer` con y sin clave. `tests/unit/eval_pipeline/test_summary_suite.py`: la suite carga y el objetivo arma la ficha.

## Evaluación

Suite `summary` (`evals/datasets/summary.jsonl`, objetivo `evals/targets/summary.py`), umbral **0,8**. Cada caso trae la ficha (`status`, `data`, `knockout`, `score`, `human_requested`, `events`) y la conversación (`history`); `expected` describe qué deben decir las observaciones y qué no deben inventar. Casos: calificado con dudas de pago, descartado sin licencia, descartado fuera de zona, campo en `needs_review`, pidió un humano, candidato frustrado, cambio a inglés y uno donde **no** debe aparecer experiencia porque el candidato nunca la mencionó.
