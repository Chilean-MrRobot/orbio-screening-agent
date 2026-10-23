# 010 — RAG sobre la FAQ

**Estado:** implementada
**Ola:** 2 · **Depende de:** 000, 001, 006, 007

## Objetivo

Que el agente (009) responda las dudas de los candidatos (pago, turnos, requisitos, proceso, privacidad) con la FAQ de Grupo Sazón y, cuando la respuesta **no** está en la FAQ, lo sepa y no invente ("Lo consulto con el equipo").

## Alcance

- Incluye: FAQ de ejemplo en Markdown (`knowledge/faq/`), fragmentación por pregunta, embeddings con OpenAI, índice versionado en un archivo JSON reconstruido solo cuando cambian los documentos, `EmbeddingKnowledgeBase` (implementa `KnowledgeBase` de `ports.py`) con similitud coseno en memoria y umbral mínimo, CLI para reconstruir el índice y la suite `rag` del golden dataset.
- No incluye: base vectorial (pgvector, Vertex AI Vector Search), búsqueda híbrida o por palabras clave, re-ranking, FAQ en inglés (el modelo de embeddings es multilingüe: una pregunta en inglés recupera el fragmento en español y el agente responde en inglés), redacción de la respuesta al candidato (es del agente 009; la suite usa un redactor mínimo solo para evaluar).

## Interfaz

```
knowledge/
  faq/*.md            FAQ (contenido ficticio para el demo)
  index.json          índice versionado (fragmentos + embeddings + hash)
src/screening/knowledge/
  chunking.py         FaqChunk, split_document, load_chunks
  embeddings.py       Embedder (Protocol), EmbeddingResult, OpenAIEmbedder
  index.py            KnowledgeIndex, corpus_hash, build_index, load_index, save_index
  store.py            VectorStore (Protocol), InMemoryVectorStore
  base.py             EmbeddingKnowledgeBase
  build.py            CLI: python -m screening.knowledge.build [--check] [--force]
```

### Formato de la FAQ

Un archivo Markdown por tema. El `# Título` es el tema; cada `## Pregunta` abre un fragmento cuya respuesta es el texto hasta el siguiente `##`. Lo anterior al primer `##` (título y aviso de contenido ficticio) no se indexa.

### Modelos

```python
@dataclass(frozen=True)
class FaqChunk:
    id: str        # "pago#cuanto-se-paga-en-espana" (archivo sin extensión + slug de la pregunta)
    source: str    # "pago.md · ¿Cuánto se paga en España?" (lo que ve el agente para citar)
    text: str      # "¿Cuánto se paga en España?\n<respuesta>" (pregunta + respuesta, se embebe junto)

class EmbeddingResult:            # dataclass
    vectors: list[list[float]]
    usage: LLMUsage               # tokens y costo (tabla de precios de la 006)

class Embedder(Protocol):
    model: str
    def embed(self, texts: list[str]) -> EmbeddingResult: ...

class IndexedChunk(BaseModel):   id, source, text, embedding: list[float]
class KnowledgeIndex(BaseModel): version: int, model: str, corpus_hash: str, chunks: list[IndexedChunk]

class VectorStore(Protocol):
    def query(self, vector: list[float], *, limit: int) -> list[tuple[IndexedChunk, float]]: ...
```

`EmbeddingKnowledgeBase(store, embedder, *, min_score=DEFAULT_MIN_SCORE)` implementa `search(query, *, limit=3) -> list[KnowledgeSnippet]` y expone `last_usage: LLMUsage | None` (el costo del embedding de la última consulta, para las métricas). `EmbeddingKnowledgeBase.from_index(index, embedder, min_score=...)` arma la base sobre un `InMemoryVectorStore`.

## Comportamiento

**Fragmentación.** Un fragmento por pregunta (las respuestas de la FAQ son cortas: un fragmento = una unidad de respuesta, sin cortes a mitad de frase ni solapamiento). Se embebe pregunta + respuesta para que la búsqueda acierte tanto por la forma de preguntar como por el contenido. Preguntas sin respuesta se omiten; dos preguntas con el mismo slug en un archivo son un error.

**Índice y hash.** `corpus_hash` es el SHA-256 del modelo de embeddings y de cada fragmento (`id` + `text`), en orden. `build_index(chunks, embedder, existing)`:
- si `existing` tiene el mismo hash, lo devuelve tal cual y **no llama a la API**;
- si cambió, reconstruye y solo embebe los fragmentos cuyo texto no estaba en `existing` con el mismo modelo (editar una respuesta cuesta un embedding, no toda la FAQ).

El CLI `python -m screening.knowledge.build` lee `knowledge/faq/`, reconstruye `knowledge/index.json` solo si cambió el hash e imprime tokens y costo. `--check` no llama a la API: sale con 1 si el índice no existe o está desactualizado (para CI). `--force` reconstruye todo. Al arrancar, el sistema solo **lee** el índice: nunca paga embeddings de la FAQ en cada arranque.

**Búsqueda.** Se embebe la consulta (una llamada), se calcula la similitud coseno contra todos los fragmentos en memoria (con ~25 fragmentos, una base vectorial es sobreingeniería), se ordena de mayor a menor y se devuelven los `limit` primeros **con score ≥ `min_score`**. Si ninguno lo supera, la lista vacía es la señal para el agente de que la respuesta no está en la FAQ. Consulta vacía, `limit ≤ 0` o índice vacío → lista vacía sin llamar a la API. Un vector de norma cero tiene score 0.

**Umbral.** `DEFAULT_MIN_SCORE = 0.35`, calibrado para `text-embedding-3-small` (las preguntas relacionadas quedan sobre ~0,45 y las ajenas bajo ~0,30). Es un parámetro técnico del recuperador, no una regla de negocio; si se quiere ajustar sin tocar código, se propone moverlo a `config/client.yaml` (cambio de contrato, fuera de esta feature). Con el umbral filtrando lo ajeno, el prompt del agente sigue siendo la segunda línea de defensa: si los fragmentos no responden la pregunta, dice que lo consulta con el equipo.

**Camino de escalamiento.** La interfaz `VectorStore` es lo único que conoce el almacenamiento: para pasar a **pgvector** (Cloud SQL) o a **Vertex AI Vector Search** se implementa otro `VectorStore.query` (y el indexador hace *upsert* en vez de escribir el JSON); `EmbeddingKnowledgeBase`, el agente y la suite no cambian. Conviene hacerlo cuando la FAQ pase de unos cientos de fragmentos, cuando haya varios clientes con FAQ distintas (filtro por cliente/país como metadato) o cuando el contenido lo edite el equipo de HR desde un CMS. En ese punto también suman: búsqueda híbrida (BM25 + vectores) para términos exactos como "A1" o "IMSS", re-ranking y una métrica de recuperación (recall@k) aparte de la de respuesta.

## Criterios de aceptación

- **Dado** un Markdown con título, aviso y tres `##`, **cuando** se fragmenta, **entonces** salen tres fragmentos con `id`, `source` y `text` (pregunta + respuesta) y el aviso no se indexa.
- **Dado** un índice existente con el mismo hash, **cuando** se llama a `build_index`, **entonces** se devuelve sin llamar al `Embedder`.
- **Dado** que cambió una sola respuesta, **cuando** se reconstruye, **entonces** solo se embebe ese fragmento y el hash cambia.
- **Dado** que cambió el modelo de embeddings, **cuando** se reconstruye, **entonces** se embeben todos los fragmentos.
- **Dado** una consulta, **cuando** se busca, **entonces** los fragmentos vienen ordenados por similitud coseno, como máximo `limit`, y todos con score ≥ `min_score`.
- **Dado** una consulta ajena a la FAQ (score bajo el umbral), **cuando** se busca, **entonces** se devuelve `[]`.
- **Dado** un índice vacío, una consulta vacía o `limit ≤ 0`, **cuando** se busca, **entonces** se devuelve `[]` sin llamar a la API.
- **Dado** el índice versionado, **cuando** se corre `build --check`, **entonces** sale con 0 si está al día con `knowledge/faq/` y con 1 si no.

## Tests

Unitarios (`tests/unit/knowledge/`), con un `Embedder` falso y determinista (bolsa de palabras con hashing a un vector fijo, sin red):

- Fragmentación: título y aviso ignorados, pregunta + respuesta, slug estable y sin tildes, pregunta sin respuesta omitida, slug repetido → error, carga de una carpeta en orden alfabético y la FAQ real tiene entre 15 y 30 fragmentos.
- Hash: determinista, cambia con el texto, con el id y con el modelo.
- Índice: reutiliza el existente sin llamadas, reembebe solo lo cambiado, cambio de modelo reembebe todo, guardar y cargar ida y vuelta, archivo inválido → error.
- Búsqueda: ranking, umbral, `limit`, índice vacío, consulta vacía, `last_usage`, coseno con vector cero.
- CLI: construye, segunda corrida no llama al `Embedder`, `--check` (0 al día, 1 desactualizado o sin índice), `--force`.
- El `knowledge/index.json` versionado está al día con la FAQ (se salta si aún no se construyó).

Integración (`tests/integration/test_knowledge_integration.py`, se salta sin `OPENAI_API_KEY`): construye el índice en memoria con embeddings reales, una pregunta sobre el pago en México recupera ese fragmento primero y una pregunta ajena devuelve `[]`.

## Evaluación

Suite `rag` (`evals/datasets/rag.jsonl`, objetivo en `evals/targets/rag.py`, umbral **0,8**). El objetivo recupera con `EmbeddingKnowledgeBase` sobre el índice versionado y redacta una respuesta corta con `conversation_model` y el prompt `prompts/rag_answer.md`; si no hay fragmentos, responde sin LLM que lo consultará con el equipo. El costo del caso suma el embedding de la consulta y la redacción.

15 casos de pregunta + respuesta esperada:

- 11 respondibles con la FAQ, varios con paráfrasis o jerga ("¿cuánto se saca?", "carnet B de moto"), uno en inglés y uno de México.
- 4 que **no** están en la FAQ (menú vegano, bono por referidos, acompañante en la moto, horario de atención de los restaurantes): lo esperado es reconocer que no tiene esa información y que lo consultará con el equipo, sin inventar.
