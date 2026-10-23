# 006 — Cliente LLM de OpenAI + trazas en LangSmith

**Estado:** implementada
**Ola:** 1 · **Depende de:** 000, 001

## Objetivo
Implementar el puerto `LLMClient` con OpenAI para que el agente (009), el resumen (012), el clasificador (014) y las evaluaciones (007) llamen al LLM de una sola forma: con salidas validadas, reintentos, costo medido y trazas opcionales. Incluye un fake guionado para que las features de la ola 2 testeen sin red.

## Alcance
- Incluye: `OpenAILLMClient`, `FakeLLMClient`, tabla de precios `config/llm_pricing.yaml` con su loader y trazas en LangSmith si están activadas.
- No incluye: embeddings (los usa el RAG de la 010), streaming, herramientas (*function calling*), caché de respuestas ni `reasoning_effort` (siguientes pasos).

## Interfaz

**`screening.adapters.llm_pricing`**
- `ModelPrice`: `input_per_million`, `output_per_million` y `cached_input_per_million` (opcional) en USD.
- `PricingTable`: `models: dict[str, ModelPrice]`; `price_for(model) -> ModelPrice | None` y `cost(model, input_tokens, output_tokens, cached_input_tokens=0) -> float`.
- `load_pricing(path=None) -> PricingTable`: lee `llm_pricing.yaml` desde `SCREENING_CONFIG_DIR` (o `config/`); un archivo inválido lanza `ConfigError`.

**`screening.adapters.openai_llm`**
- `OpenAILLMClient(pricing, *, client=None, max_retries=2, backoff_seconds=0.5, tracing=None, sleep=time.sleep, timer=time.perf_counter)`. Implementa `generate_text` y `generate_structured` de `LLMClient`.
  - `client`: cliente del SDK (`openai.OpenAI`) inyectable; si falta, se crea uno con `max_retries=0` (los reintentos son nuestros).
  - `tracing`: `None` lee `LANGSMITH_TRACING`.
- `OpenAILLMClient.from_env()`: arma el cliente con la tabla de precios por defecto y `OPENAI_API_KEY` del entorno.
- `strict_json_schema(schema: type[BaseModel]) -> dict`: JSON schema estricto para Structured Outputs.

**`screening.adapters.fake_llm`**
- `FakeLLMClient(responses=(), *, responder=None, input_tokens=10, output_tokens=5, latency_ms=1.0, cost_usd=0.0)`.
- Cada respuesta guionada puede ser `str`, `dict`, instancia de `BaseModel`, `Exception` (se lanza) o `ScriptedResponse(value, usage=None)` para fijar el uso.
- `responder(call) -> respuesta`: alternativa dinámica cuando se acaba la cola.
- `calls: list[LLMCall]` registra método, `system`, `messages`, `model`, `temperature` y `schema`.

## Comportamiento
- **Mensajes:** `system` va como primer mensaje con rol `system`, seguido de `messages` en orden.
- **`temperature`:** solo se envía si no es `None` (los modelos de razonamiento la rechazan).
- **Salida estructurada:** `response_format` de tipo `json_schema` con `strict: true`, generado desde el modelo Pydantic: todos los objetos con `additionalProperties: false`, todas las propiedades en `required` y sin `default`. La respuesta se valida con `schema.model_validate_json`.
- **Reintentos por errores transitorios** (conexión, timeout, 408, 409, 429 y 5xx): hasta `max_retries` reintentos con backoff exponencial (`backoff_seconds * 2**intento`). Al agotarlos, `LLMError`.
- **Errores no transitorios** (400, 401, 403, 404, 422…): `LLMError` sin reintentar.
- **Salida inválida** (JSON mal formado, no cumple el esquema, *refusal*, respuesta vacía o cortada por `length`): **un** reintento; si vuelve a fallar, `LLMError`.
- **Uso (`LLMUsage`):** `model` es el modelo pedido; los tokens, la latencia y el costo **suman todos los intentos** que llegaron a responder, porque todos se pagan. Latencia medida con el `timer` inyectado.
- **Costo:** `(input - cached) * input + cached * cached_input + output * output`, por millón de tokens. El precio se busca por nombre exacto y, si no está, por el prefijo más largo (`gpt-5-mini-2025-08-07` → `gpt-5-mini`). Un modelo sin precio cuesta 0 y deja un *warning* en el log.
- **Trazas:** si `LANGSMITH_TRACING=true`, el cliente del SDK se envuelve con `langsmith.wrappers.wrap_openai`. Si no está activado, LangSmith no se importa. Si falla el import, se sigue sin trazas y se registra un *warning*.

## Criterios de aceptación
- **Dado** `temperature=None`, **cuando** se llama al LLM, **entonces** el request no lleva `temperature`; **dado** `0.2`, **entonces** sí la lleva.
- **Dado** un modelo Pydantic, **cuando** se pide salida estructurada, **entonces** el request lleva `response_format` estricto y se devuelve una instancia validada.
- **Dado** un 429 seguido de una respuesta válida, **cuando** se llama, **entonces** se duerme `backoff_seconds` y se devuelve la respuesta.
- **Dado** errores transitorios en todos los intentos, **cuando** se llama, **entonces** hay `max_retries + 1` intentos y se lanza `LLMError`.
- **Dado** un 400, **cuando** se llama, **entonces** se lanza `LLMError` sin reintentar.
- **Dado** un JSON que no valida y luego uno válido, **cuando** se pide salida estructurada, **entonces** se devuelve el válido y el uso suma ambos intentos.
- **Dado** dos salidas inválidas seguidas, **entonces** se lanza `LLMError`.
- **Dado** 1.000.000 de tokens de entrada y 1.000.000 de salida en `gpt-5-mini`, **entonces** el costo es la suma de ambos precios de la tabla.
- **Dado** `LANGSMITH_TRACING=false`, **entonces** el cliente no se envuelve; **dado** `true`, **entonces** se envuelve con `wrap_openai`.
- **Dado** un `FakeLLMClient` con un `dict` guionado, **cuando** se pide salida estructurada, **entonces** devuelve el modelo validado y registra la llamada.

## Tests
- `tests/unit/test_llm_pricing.py`: carga del YAML real, costo, prefijo, modelo desconocido, YAML inválido.
- `tests/unit/test_openai_llm.py`: request (mensajes, `temperature`, `response_format`), esquema estricto, reintentos y backoff, errores no transitorios, salida inválida, refusal, suma de uso, trazas.
- `tests/unit/test_fake_llm.py`: respuestas guionadas, `responder`, excepciones, cola agotada, registro de llamadas.
- `tests/integration/test_openai_llm_integration.py` (`integration`): una llamada de texto y una estructurada reales; se salta sin `OPENAI_API_KEY`.

## Evaluación
El cliente no tiene comportamiento propio que evaluar con el golden dataset: lo ejercita el juez de la 007 (suite `judge_sanity`) en cada corrida.
