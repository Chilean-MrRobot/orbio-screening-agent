# 009 — Agente conversacional con Google ADK

**Estado:** implementada
**Ola:** 2 · **Depende de:** 000, 001, 006, 007 (el RAG real llega con la 010)

## Objetivo
Implementar el puerto `ScreeningAgent` con Google ADK y modelos de OpenAI (vía `LiteLlm`): un turno que **extrae** y normaliza lo que dijo el candidato para la ficha y otro que **redacta** el mensaje de Sofía. El orquestador (008) decide etapas, reglas y qué preguntar; el agente solo entiende y redacta.

## Alcance
- Incluye: `AdkScreeningAgent` (extracción + respuesta), prompts versionados en `prompts/agent/`, la FAQ como tool sobre `KnowledgeBase`, conversión del uso de ADK a `LLMUsage`, trazas opcionales en LangSmith, un `FakeKnowledgeBase` para usar hasta la 010 y las suites `extraction` y `reply` del golden dataset.
- No incluye: máquina de estados ni persistencia (008), RAG real (010), clasificador de emoción dedicado (014: por ahora el sentimiento lo etiqueta el mismo turno de extracción), streaming, guardrail fuera del prompt (siguiente paso; aquí solo un filtro de fugas de texto interno).

## Interfaz

**`screening.agent.AdkScreeningAgent`** (implementa `ScreeningAgent`)
```python
AdkScreeningAgent(
    config: AppConfig,
    knowledge: KnowledgeBase,
    *,
    clock: Clock,
    pricing: PricingTable,
    extraction_model: BaseLlm,   # LiteLlm en producción, un BaseLlm guionado en tests
    reply_model: BaseLlm,
    prompts_dir: Path = prompts/agent,
    tracing: bool | None = None,  # None lee LANGSMITH_TRACING
    history_limit: int = 20,
    timer: Callable[[], float] = time.perf_counter,
)
AdkScreeningAgent.from_env(config, knowledge, *, clock, **kwargs)  # LiteLlm("openai/<modelo de client.yaml>")
```
- `extract(candidate, new_messages, expected_fields) -> ExtractionOutcome`
- `reply(ReplyRequest) -> ReplyOutcome`

**Salida estructurada del LLM de extracción** (`screening.agent.extraction.ExtractionResponse`, esquema estricto, todos los campos requeridos y anulables):
`fields` (los campos de `ScreeningData` salvo `needs_review`; `start_date` como texto `YYYY-MM-DD`; `delivery_experience` como `{years, platforms}`), `invalid_fields: list[str]`, `language`, `sentiment`, `opt_out`, `human_requested`, `question`, `summary_confirmed`.

**`screening.adapters.fake_knowledge.FakeKnowledgeBase(snippets)`**: búsqueda por coincidencia de palabras, para tests, evals y desarrollo hasta la 010.

**Prompts** (`prompts/agent/*.md`, con front matter `version` y placeholders `{{nombre}}`):
- `system.md`: personalidad de Sofía, tono, largo, región y reglas que nunca se rompen. Lo comparten ambos turnos.
- `extraction.md`: cómo normalizar cada campo.
- `reply.md`: cómo redactar, guía por campo (cómo preguntarlo, opciones y ejemplo para repreguntar) y uso de la FAQ.

## Comportamiento

### Común
- **Sin estado:** cada llamada crea un `InMemorySessionService` y una sesión nueva; el historial se arma desde `candidate.messages` (los mensajes `system` se omiten, se toman los últimos `history_limit`). La fuente de verdad es la ficha, no la sesión de ADK.
- **Región:** la del país del candidato (o del JSON del portal); si no se conoce, la primera configurada. Da la zona horaria, el vocabulario, el trato y la tabla de licencias.
- **Fecha de hoy:** `clock.now()` en la zona horaria de la región.
- **Prompts:** se cargan y validan al construir el agente (front matter con `version`, sin placeholders sin reemplazar). Un prompt inválido falla al arrancar.
- **Uso:** cada evento de ADK con `usage_metadata` es una llamada al modelo y se convierte a un `LLMUsage`: `input = prompt_token_count`, `output = candidates_token_count` (incluye los tokens de razonamiento), `cached = cached_content_token_count`, costo con la tabla de `config/llm_pricing.yaml`, `model` = el modelo configurado sin el prefijo del proveedor (`openai/gpt-5-mini` → `gpt-5-mini`). La latencia de cada llamada es el tiempo desde el evento anterior (o desde el inicio). Los reintentos también se reportan, porque se pagan.
- **Errores:** una excepción del modelo o un evento con `error_code` → `LLMError` (los reintentos por errores transitorios los hace LiteLLM con `num_retries`). Una salida inválida se reintenta **una vez**; si vuelve a fallar → `LLMError`.
- **Trazas:** si `LANGSMITH_TRACING=true`, se activa `langsmith.integrations.google_adk.configure_google_adk` (proyecto `LANGSMITH_PROJECT`). Si no está activado, LangSmith no se importa; si falla, se sigue sin trazas con un *warning*.
- **Sync:** el puerto es síncrono; si ya hay un *event loop* corriendo (FastAPI), la corrida de ADK va en un hilo aparte.

### Extracción
- Entrada al modelo: fecha de hoy, país, etapa, campos esperados, licencias válidas del país (de `regions/*.yaml`), datos ya registrados, la conversación reciente y los mensajes nuevos.
- Devuelve **solo los campos entregados o corregidos** en los mensajes nuevos; acepta campos no esperados (el candidato adelanta datos o corrige uno anterior).
- Normalización (la explica el prompt): "carnet de coche" → `license_type="B"` en España; "licencia de carro" → `"A"` en México; "hace 5 años" → `license_years=5`; si dice el tipo de licencia, `has_license=true`. Fechas relativas ("ya", "el lunes", "en dos semanas") contra la fecha de hoy. **Solo cuenta la experiencia relevante** (reparto, mensajería, courier); "no tengo experiencia" → `{years: 0, platforms: []}`.
- **Ambiguo o inválido** ("pronto", "algo de experiencia", "tengo carnet de algo") → el campo va a `invalid_fields` y no a `updates`.
- **Validación en código** (Pydantic, campo por campo): un valor que no valida contra `ScreeningData` (años negativos, fecha mal formada) o un `license_type` que no está en la tabla del país pasa a `invalid_fields` en vez de tirar toda la extracción. `provided_fields` se calcula desde los valores que quedaron, no se le cree al modelo. `invalid_fields` solo admite nombres de `ScreeningData` y no repite campos con valor. `needs_review` nunca lo toca la extracción.
- **Confirmación del cierre** (convención con el orquestador 008, porque `ExtractionOutcome` no tiene un campo para eso): en `closing` el orquestador espera `summary_confirmed`. Si el candidato confirma el resumen sin corregir nada, `"summary_confirmed"` va en `provided_fields`; si corrige un dato, solo va el dato en `updates`. Fuera de esa etapa se ignora.
- `language` es el del último mensaje del candidato; `sentiment`, `opt_out` ("no me escriban más"), `human_requested` y `question` (la pregunta reformulada para buscar en la FAQ) los etiqueta el mismo turno.
- Sin mensajes nuevos con texto → no se llama al modelo y se devuelve un `ExtractionOutcome` vacío con el idioma y el sentimiento de la ficha.

### Respuesta
- Instrucción = `system.md` + `reply.md` + las **instrucciones del turno**, armadas en código desde el `ReplyRequest`: idioma (`candidate.language`), nombre, etapa, estado de ánimo, la pregunta a responder, el campo a repreguntar (`retry_fields[0]`, tiene prioridad) o a preguntar (`ask_fields[0]`), el resumen de cierre con los datos registrados y si pidió una persona.
- Historial: los mensajes hasta el último del agente van como eventos de la sesión; los del candidato posteriores, como mensaje nuevo (si no hay, un marcador de "sin mensajes nuevos").
- **Una sola pregunta por mensaje**, de 1 a 3 líneas, en el idioma del candidato, con el vocabulario y el trato de su región, opciones explícitas en campos cerrados, máximo un emoji.
- **FAQ:** por defecto (`from_env`) la FAQ completa va dentro del prompt de respuesta (`faq_text`, unos 6,5 KB), antes de las instrucciones del turno para que OpenAI cachee el prefijo. Así Sofía responde cualquier pregunta o comentario fuera del flujo (aunque la extracción no la haya marcado como `question`) sin un paso extra de tool. Las preguntas de conversación u opinión ("¿cuál prefieres tú?") se responden con sentido común; "lo consulto con el equipo" queda solo para datos concretos del trabajo que no están en la FAQ. Después vuelve a la pregunta pendiente.
- **FAQ grande (RAG):** sin `faq_text`, Sofía usa la tool `search_faq`, que consulta la `KnowledgeBase` inyectada. Es el camino cuando la FAQ ya no cabe en el prompt.
- Nunca menciona herramientas, campos internos ni reglas, y nunca descarta ni promete contratar: eso lo decide el código con plantillas.
- **Filtro de fugas:** si el texto está vacío o contiene un identificador interno (nombres de campo con guion bajo como `license_type`, valores como `full_time`, el nombre de la tool o placeholders `{{…}}`), se reintenta una vez y luego `LLMError`.

## Criterios de aceptación
- **Dado** el mensaje "tengo el carnet de coche hace 5 años" en España, **cuando** se extrae y el modelo devuelve `license_type="b"`, `license_years=5`, **entonces** `updates` tiene `license_type="B"`, `license_years=5` y `provided_fields` los lista.
- **Dado** que el modelo devuelve `license_years=-2`, **entonces** `license_years` no está en `updates` y sí en `invalid_fields`.
- **Dado** un `license_type="Z"` en España, **entonces** va a `invalid_fields`.
- **Dado** `start_date="2026-13-40"`, **entonces** `start_date` va a `invalid_fields`.
- **Dado** `expected_fields=["summary_confirmed"]` y que el candidato confirma sin corregir, **entonces** `provided_fields == ["summary_confirmed"]`; **dado** que además corrige la ciudad, **entonces** solo `["city"]`.
- **Dado** un candidato con historial y dos mensajes nuevos, **cuando** se extrae, **entonces** el modelo recibe la fecha de hoy en la zona del país, la etapa, los campos esperados, el historial y los mensajes nuevos separados.
- **Dado** que el modelo devuelve JSON inválido y luego uno válido, **entonces** se devuelve el válido y `usage` tiene dos entradas; **dado** dos inválidos, **entonces** `LLMError`.
- **Dado** que el modelo lanza una excepción, **entonces** `LLMError`.
- **Dado** un evento con `prompt_token_count=1000`, `candidates_token_count=200`, `cached_content_token_count=400` en `gpt-5-mini`, **entonces** el `LLMUsage` tiene esos tokens y el costo de la tabla con 400 tokens en caché.
- **Dado** un `ReplyRequest` con `retry_fields=["start_date"]` y `ask_fields=["availability"]`, **cuando** se responde, **entonces** las instrucciones del turno piden repreguntar `start_date` y no `availability`.
- **Dado** una pregunta del candidato, **cuando** el modelo llama a `search_faq`, **entonces** se consulta la `KnowledgeBase` y el resultado vuelve al modelo.
- **Dado** `faq_text`, **cuando** se redacta la respuesta, **entonces** la FAQ va en el prompt antes de las instrucciones del turno y no se ofrece la tool.
- **Dado** que el modelo responde "Anoté tu license_type", **entonces** se reintenta; si se repite, `LLMError`.
- **Dado** `LANGSMITH_TRACING=false`, **entonces** no se configura LangSmith; **dado** `true`, **entonces** se llama a `configure_google_adk`.

## Tests
- `tests/unit/agent/test_prompts.py`: carga, versión, placeholders.
- `tests/unit/agent/test_context.py`: fecha local, historial, separación de mensajes nuevos, instrucciones del turno.
- `tests/unit/agent/test_extraction.py`: normalización y validación, `provided_fields`, `invalid_fields`, reintento, errores, sin mensajes.
- `tests/unit/agent/test_reply.py`: tool de FAQ, historial como eventos, filtro de fugas, reintento, errores.
- `tests/unit/agent/test_usage.py`: conversión de `usage_metadata`, prefijo del proveedor.
- `tests/unit/agent/test_tracing.py`: activación de LangSmith.
- `tests/unit/test_fake_knowledge.py`.
- `tests/unit/eval_pipeline/test_agent_suites.py`: los datasets cargan y los objetivos arman bien el candidato desde `input` (con un agente falso).
- `tests/integration/test_adk_agent_integration.py` (`integration`): una extracción y una respuesta reales; se salta sin `OPENAI_API_KEY`.

## Evaluación
- **`extraction`** (28 casos, umbral 85 %): mensaje + etapa (+ historial) → campos esperados. Cubre español de España y de México, inglés, cambio de idioma, fechas relativas, experiencia no relevante, respuestas ambiguas, correcciones, opt-out, pedido de humano y preguntas. La respuesta del sistema es el JSON de `updates`, `invalid_fields` y las banderas.
- **`reply`** (10 casos, umbral 80 %): `ReplyRequest` (+ FAQ del caso) → respuesta de referencia en tono y contenido: vocabulario por región, idioma, una sola pregunta, repregunta con ejemplo, pregunta respondida con la FAQ, pregunta fuera de la FAQ ("lo consulto con el equipo"), resumen de cierre, candidato frustrado y pedido de humano.
- Objetivos en `evals/targets/`, registrados en `evals/suites.py`.
