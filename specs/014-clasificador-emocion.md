# 014 — Clasificador de emoción

**Estado:** implementada
**Ola:** 3 · **Depende de:** 000, 001, 006, 007, 009

## Objetivo
Detectar con un modelo muy pequeño y rápido (`llm.classifier_model`, hoy `gpt-5-nano`) si el candidato está `neutral`, `frustrated`, `confused` o `rushed`, para que Sofía adapte el tono de su respuesta (sección 5 de `docs/fase1_diseno_proceso.md`): frustrado → más corto, reconoce la molestia y ofrece una persona; confundido → más simple y con un ejemplo; apurado → directo al grano.

## Alcance
- Incluye: `SentimentClassifier` sobre `LLMClient.generate_structured`, prompt versionado `prompts/agent/sentiment.md`, inyección opcional en `AdkScreeningAgent` (y conexión en `from_env`), instrucciones de tono por etiqueta en el turno de respuesta y la suite `sentiment` del golden dataset.
- No incluye: guardar historial de emociones ni métricas por etiqueta en el CRM, umbral de confianza en YAML (ver *Pendientes*), detección de otras emociones, voz.

## Interfaz

**`screening.agent.sentiment`**
```python
class SentimentResponse(BaseModel):  # salida estructurada del LLM
    label: Sentiment
    confidence: float  # 0..1


@dataclass(frozen=True)
class SentimentResult:
    label: Sentiment | None  # None: falló o confianza bajo el umbral
    confidence: float
    usage: list[LLMUsage]


class SentimentClassifier:
    def __init__(
        self,
        llm: LLMClient,
        *,
        model: str,
        prompt_path: Path = DEFAULT_PROMPT_PATH,  # prompts/agent/sentiment.md
        min_confidence: float = 0.6,
        message_limit: int = 3,
    ) -> None: ...
    @classmethod
    def from_config(cls, config: AppConfig, llm: LLMClient, **kwargs) -> SentimentClassifier: ...
    @classmethod
    def from_env(cls, config: AppConfig, **kwargs) -> SentimentClassifier: ...  # OpenAILLMClient
    def classify(self, texts: list[str]) -> SentimentResult: ...
```

**`AdkScreeningAgent(..., sentiment_classifier: SentimentClassifier | None = None)`**. `from_env` lo arma con `config.llm.classifier_model` si hay `OPENAI_API_KEY` (se puede pasar uno propio o `None` por `kwargs`).

**Prompt** `prompts/agent/sentiment.md` (front matter `version`, sin placeholders): define las cuatro etiquetas y pide la salida mínima (`label`, `confidence`).

## Comportamiento
- **Entrada barata:** solo los textos de los últimos `message_limit` mensajes del candidato (no del agente, no el historial completo), como una lista en un único mensaje de usuario. El prompt del sistema es corto. La salida son dos campos.
- **Qué mensajes:** en `extract`, los mensajes previos del candidato en la ficha más los nuevos, quedándose con los últimos `message_limit` no vacíos. Sin mensajes nuevos con texto no se clasifica (como hoy, no hay extracción).
- **Concurrencia:** el clasificador corre en un hilo en paralelo con la extracción, así no suma su latencia al turno.
- **Etiqueta final de `ExtractionOutcome.sentiment`:**
  - sin clasificador → la heurística del turno de extracción (comportamiento de la 009);
  - con clasificador y `confidence >= min_confidence` → la del clasificador;
  - confianza baja, `LLMError` o cualquier excepción del clasificador → la heurística de la extracción, con un *warning*. Nunca bloquea el turno.
- **Uso:** el `LLMUsage` del clasificador se agrega al final de `ExtractionOutcome.usage` (también cuando la confianza es baja, porque se pagó). Si falla, no hay uso que sumar.
- **Tono de la respuesta:** `build_turn_instructions` agrega, según `candidate.sentiment`, una línea de tono:
  - `frustrated`: mensaje de 1 o 2 líneas, sin emojis, reconoce la molestia en pocas palabras y ofrece hablar con una persona del equipo (si ya la pidió, no se ofrece de nuevo: se le confirma el contacto).
  - `confused`: frases simples, explica qué se necesita y da un ejemplo de respuesta.
  - `rushed`: directo al grano, sin saludos ni agradecimientos, solo la pregunta con sus opciones.
  - `neutral`: sin línea extra.
  `reply.md` (v2) remite a esa línea.

## Criterios de aceptación
- **Dado** un clasificador que responde `frustrated` con confianza 0.9 **cuando** se extrae un turno **entonces** `outcome.sentiment == frustrated` aunque la extracción diga `neutral` (igual para cada etiqueta).
- **Dado** confianza 0.3 **cuando** se extrae **entonces** queda la etiqueta de la extracción y el uso del clasificador igual se suma.
- **Dado** un clasificador que lanza `LLMError` (o devuelve una salida inválida) **cuando** se extrae **entonces** el turno sigue con la etiqueta de la extracción.
- **Dado** que no hay clasificador **cuando** se extrae **entonces** todo es igual que en la 009.
- **Dado** un candidato con 5 mensajes **cuando** se clasifica **entonces** el modelo recibe solo los últimos 3, sin mensajes del agente, con `llm.classifier_model`.
- **Dado** `candidate.sentiment = frustrated` **cuando** se arman las instrucciones del turno **entonces** incluyen la línea de tono que pide ofrecer una persona; con `neutral` no hay línea de tono.
- **Dado** `OPENAI_API_KEY` en el entorno **cuando** se llama `AdkScreeningAgent.from_env` **entonces** el agente trae clasificador con el modelo de `client.yaml`; sin la variable, no.

## Tests
`tests/unit/agent/test_sentiment.py` (con `FakeLLMClient` y `ScriptedLlm`, sin red):
- `classify` devuelve cada etiqueta con confianza alta; confianza bajo el umbral → `label=None`; `LLMError` y salida inválida → `label=None` sin excepción; recorta a los últimos mensajes y usa el modelo y prompt configurados.
- El agente: cada etiqueta reemplaza la heurística; confianza baja y falla del clasificador dejan la heurística; el uso suma extracción + clasificador; sin mensajes con texto no se llama al clasificador; `from_env` con y sin `OPENAI_API_KEY`.
- `build_turn_instructions` agrega la línea de tono correcta para cada etiqueta y ninguna para `neutral`; la instrucción de la respuesta enviada al modelo cambia según la etiqueta.
- La suite `sentiment` está registrada, tiene al menos 20 casos y su objetivo devuelve la etiqueta y el uso.

## Evaluación
Suite `sentiment` (`evals/datasets/sentiment.jsonl`, objetivo `evals/targets/sentiment.py`, umbral 0.8). `input.messages` son los últimos mensajes del candidato tal como llegan por WhatsApp (España, México e inglés) y `expected` la etiqueta esperada. Incluye casos sutiles: ironía ("genial, otra vez lo mismo"), mayúsculas, mensajes de una palabra ("?", "ok"), apuro implícito ("estoy manejando"), confusión con jerga y neutrales con mayúsculas o con quejas sobre terceros. El objetivo llama al clasificador real (con el fallback a `neutral` si la confianza es baja) y responde `Etiqueta: <label> (confianza <c>)`.

```
.venv\Scripts\python -m evals.run --suite sentiment
```

## Pendientes (fuera de alcance del demo)
- `min_confidence` y `message_limit` deberían vivir en `config/client.yaml` (`llm.classifier_min_confidence`); hoy son parámetros del constructor porque el contrato de configuración de la ola 0 no se toca desde una feature.
- `LLMClient.generate_structured` no acepta `reasoning_effort`: con `minimal` el clasificador gastaría muchos menos tokens de razonamiento. Requiere ampliar el puerto (ola 0).
- Comparar con exact match en vez de LLM-as-a-judge para esta suite (la salida es una etiqueta).
