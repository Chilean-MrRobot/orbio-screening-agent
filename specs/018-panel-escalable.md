# 018 — Panel paginado y una sola contraseña

**Estado:** implementada
**Ola:** 4 (escalabilidad) · **Depende de:** 000, 013

## Objetivo
Que el panel de recruiters (spec 013, el código lo llama "CRM") funcione igual con 10K
postulaciones por semana que con las 12 del demo: ninguna ruta recorre todas las fichas
(ver `CLAUDE.md`, Escalabilidad). Y simplificar el acceso a un solo secreto.

## Alcance
- Incluye:
  - `GET /crm/candidates` paginado con `repository.query` (filtros, orden, `limit`, `cursor`).
  - `GET /crm/counts`: totales por estado con `count_by_status()` (agregación, sin leer fichas).
  - `GET /crm/metrics`: totales por estado con `count_by_status()`; el resto de la analítica
    sobre una ventana acotada de las fichas actualizadas más recientemente, diciendo sobre
    cuántas se calculó.
  - Paginación de la tabla en Streamlit (anterior / siguiente) que mantiene filtros,
    refresco automático y la ficha abierta.
  - Una sola variable, `PANEL_PASSWORD`, para entrar al panel y como bearer entre el panel
    y la API. Las demás variables del panel pasan a `PANEL_API_URL` y
    `PANEL_REFRESH_SECONDS`.
  - Índices compuestos de Firestore que faltaban para las combinaciones de filtros del panel.
- No incluye:
  - Métricas exactas sobre todo el histórico (ver "Camino para escalar las métricas").
  - Conteos filtrados por país o etapa para los KPI de la tabla: los KPI son globales.
  - Búsqueda por nombre o teléfono.
  - Renombrar rutas (`/crm/...`), módulos (`screening.crm`) ni clases: solo cambian los
    textos visibles ("CRM" → "panel").

## Interfaz

### API (`screening.api.crm`)
`build_crm_router(repository, clock, password, *, metrics_window=DEFAULT_METRICS_WINDOW)`.
`build_crm_router_from_env(repository, clock, env)` lee `PANEL_PASSWORD` y
`PANEL_METRICS_WINDOW` (opcional, por defecto 1000). Sin `PANEL_PASSWORD` devuelve `None`
y la API no monta `/crm` (los datos son personales). `app.py` no cambia.

| Método y ruta | Entrada | Salida |
|---|---|---|
| `GET /crm/candidates` | `status` (repetible), `country`, `stage`, `order` (`score_desc` por defecto · `updated_desc`), `limit` (1–200, por defecto 50), `cursor` | `CandidateRowPage` · `400` si el cursor es inválido · `422` si un parámetro no valida |
| `GET /crm/counts` | — | `StatusCounts` |
| `GET /crm/metrics` | `stale_hours` | `CrmMetrics` |
| `GET /crm/candidates/{id}` y `POST .../status` | sin cambios (spec 013) | |

```python
class CandidateRowPage(BaseModel):
    items: list[CandidateRow]
    next_cursor: str | None = None  # None: no hay más páginas


class StatusCounts(BaseModel):
    total: int
    by_status: dict[CandidateStatus, int]


class CrmMetrics(BaseModel):  # campos nuevos sobre la spec 013
    sample_size: int  # fichas sobre las que se calculó la analítica
    sample_limit: int  # tamaño máximo de la ventana
    ...  # total y by_status salen de count_by_status()
```

### Analítica (`screening.crm.metrics`)
`compute_metrics(candidates, now, stale_after, *, status_counts=None, sample_limit=None)`.
Con `status_counts`, `total` y `by_status` salen de ahí y no de la muestra; sin él (tests,
uso directo) se calculan sobre `candidates` como antes.

### Cliente (`screening.crm.client`)
- `CrmClient.list_candidates(..., order, limit, cursor) -> CandidateRowPage`.
- `CrmClient.counts() -> StatusCounts`.
- `CrmClient.from_env()` lee `PANEL_API_URL` y `PANEL_PASSWORD`.

### Paginación del panel (`screening.crm.pagination`)
Lógica pura, sin Streamlit, que se guarda en `st.session_state`:

```python
@dataclass
class Pager:
    filters: tuple | None = None        # filtros con los que se armó la pila
    cursors: list[str | None]           # cursor de cada página visitada; [None] = la primera

    def sync(self, filters) -> None     # si cambian los filtros, vuelve a la página 1
    cursor -> str | None                # cursor de la página actual
    page_number -> int                  # 1, 2, ...
    has_previous -> bool
    def next(self, next_cursor) -> None # avanza solo si hay next_cursor
    def previous(self) -> None
    def reset(self) -> None             # a la página 1 (p. ej. tras un cursor inválido)
```

## Comportamiento
- **Listado.** Una página por petición, con `repository.query`. El orden por defecto es
  `score_desc` (las fichas sin puntaje al final), desempatado por `id`, como define la
  spec 000; ya no se desempata por actividad reciente (sí se puede ordenar por
  `updated_desc`). `next_touch_asc` no se expone: es del calentamiento.
- **Cursor inválido** (no es base64/JSON, o es de otro orden) → `400` con el detalle. El
  panel, si recibe `400`, vuelve a la primera página.
- **Conteos.** `GET /crm/counts` y los totales de `GET /crm/metrics` usan solo
  `count_by_status()`: en Firestore son agregaciones `count()` que no leen documentos.
- **Ventana de métricas.** Respuesta, completitud, calificación, abandono por etapa,
  motivos de descarte, tiempo hasta calificar, toques, piden humano, puntaje medio, países
  y costo se calculan sobre las `metrics_window` fichas actualizadas más recientemente
  (`order="updated_desc"`, en páginas de 500, el máximo de `CandidateQuery`).
  `sample_size` dice cuántas fichas entraron y el panel lo muestra ("calculadas sobre las
  últimas N fichas"). Si `sample_size < total`, la muestra está sesgada hacia las
  conversaciones recientes; para el demo es lo que interesa ver.
- **Ninguna ruta del panel usa `repository.list()`.** Un test lo verifica con un
  repositorio cuyo `list()` falla.
- **Panel.** La tabla muestra una página (`PANEL_PAGE_SIZE`, por defecto 25) con botones
  "← Anterior" y "Siguiente →" e indica la página. Al cambiar un filtro se vuelve a la
  página 1. El refresco automático recarga la página actual con el mismo cursor (si
  entran fichas nuevas, la página 1 las muestra; las demás pueden correrse, como en
  cualquier paginación por cursor). La ficha abierta se mantiene aunque cambie de página:
  si el seleccionado no está en la página actual, se agrega al selector. Los KPI de
  arriba vienen de `GET /crm/counts` y son globales (no dependen de los filtros).
- **Contraseña única.** `PANEL_PASSWORD` se define en la API y en el panel. La API exige
  `Authorization: Bearer <PANEL_PASSWORD>` (comparación con `hmac.compare_digest`); el
  panel pide la misma contraseña para entrar y la usa como bearer. Sin la variable, la
  API no monta `/crm` y el panel muestra un error y no arranca.
- **Demo local.** `screening.crm.demo` trae `PANEL_PASSWORD=demo` por defecto, como antes
  traía `CRM_API_TOKEN`.

## Camino para escalar las métricas
La ventana es una simplificación del demo: mil fichas se leen en ~2 lecturas de 500, una
vez por refresco y por recruiter con el panel abierto. Para métricas exactas sobre todo
el histórico sin leer las fichas:
1. **Contadores agregados al guardar.** Al cambiar de estado o de etapa, el repositorio
   incrementa en la misma transacción documentos `metrics/{día}/{país}` con contadores
   (contactados, respondieron, calificados, descartes por motivo, abandono por etapa,
   tokens y costo). Para evitar contención en Firestore (~1 escritura/s por documento) se
   reparten en N shards que se suman al leer. El panel lee unos pocos documentos.
2. **Export a BigQuery.** La extensión "Stream Firestore to BigQuery" (o un export
   programado) deja las fichas en una tabla; las métricas pasan a ser consultas SQL
   (o una vista en Looker Studio) sin tocar la base operativa. Es lo recomendable cuando
   se quieran cortes ad hoc (por semana, ciudad, fuente).
Mientras tanto, cachear la respuesta de `/crm/metrics` unos segundos (p. ej. en
Memorystore o como documento en Firestore) reduce las lecturas cuando hay varios recruiters.

## Criterios de aceptación
- **Dado** 7 fichas y `limit=3`, **cuando** se recorren las páginas siguiendo
  `next_cursor`, **entonces** se obtienen las 7 sin repetir, en orden de puntaje, y la
  última página trae `next_cursor = null`.
- **Dado** filtros por estado y país, **cuando** se pagina, **entonces** todas las páginas
  respetan los filtros.
- **Dado** un `cursor` que no salió de la API, o uno de `updated_desc` usado con
  `score_desc`, **cuando** se pide el listado, **entonces** responde `400`.
- **Dado** un repositorio con más fichas que `metrics_window`, **cuando** se piden las
  métricas, **entonces** `total` y `by_status` cuentan todas, `sample_size ==
  metrics_window` y la analítica solo usa las más recientes.
- **Dado** un repositorio cuyo `list()` lanza error, **cuando** se llama a cualquier ruta
  del panel, **entonces** ninguna falla.
- **Dado** `PANEL_PASSWORD`, **cuando** se llama a una ruta del panel sin bearer, con uno
  incorrecto o sin el prefijo `Bearer`, **entonces** `401`; con la contraseña, `200`.
- **Dado** que no hay `PANEL_PASSWORD`, **cuando** arranca la API, **entonces** no monta
  `/crm`.
- **Dado** el panel en la página 3, **cuando** cambia un filtro, **entonces** vuelve a la
  página 1; **cuando** se pulsa "Anterior", vuelve a la página 2 con su cursor.

## Tests
- `tests/unit/crm/test_api.py`: paginación con varias páginas, paginación con filtros,
  orden `updated_desc`, `limit` fuera de rango → `422`, cursor inválido y de otro orden →
  `400`, `counts`, métricas con `count_by_status` y ventana acotada (`sample_size`),
  ninguna ruta usa `list()`, contraseña única (con, sin, incorrecta), router desde el
  entorno solo con `PANEL_PASSWORD` y `PANEL_METRICS_WINDOW`.
- `tests/unit/crm/test_metrics.py`: `status_counts` reemplaza `total` y `by_status`, y
  `sample_size`.
- `tests/unit/crm/test_client.py`: `list_candidates` envía `limit`, `cursor` y `order` y
  devuelve la página; `counts`; `from_env` con `PANEL_PASSWORD`.
- `tests/unit/crm/test_pagination.py`: `Pager` (avanzar, retroceder, no avanzar sin
  cursor, volver a la página 1 al cambiar filtros, reinicio).

## Evaluación
No usa LLM.
