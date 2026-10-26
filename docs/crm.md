# Panel de candidatos

Nuestro propio panel para recruiters (en el código, "CRM"), hecho en Streamlit, para que
sigan a los candidatos en vivo: tabla paginada que se refresca sola, ficha con la
conversación, los datos extraídos (y el JSON crudo), las reglas eliminatorias, el desglose
del puntaje, el resumen y el `timeline`, botones para mover el estado y un panel de
métricas. Diseño en [`specs/013-crm.md`](../specs/013-crm.md) y
[`specs/018-panel-escalable.md`](../specs/018-panel-escalable.md) (paginación y contraseña
única).

```
Navegador ──PANEL_PASSWORD──▶ Streamlit (panel) ──HTTP + Bearer PANEL_PASSWORD──▶ API FastAPI ──▶ repositorio
                                                                                  /crm/...       (memoria o Firestore)
```

El panel nunca lee la base directo: con el repositorio en memoria, los datos viven dentro
del proceso de la API.

## Variables de entorno

| Variable | Dónde | Para qué |
|---|---|---|
| `PANEL_PASSWORD` | API y panel | La única contraseña: con ella se entra al panel y el panel la usa como bearer hacia la API. Sin ella, la API no monta `/crm` y el panel no arranca. |
| `PANEL_API_URL` | panel | URL base de la API (por defecto `http://localhost:8000`). |
| `PANEL_REFRESH_SECONDS` | panel | Cada cuántos segundos se refresca (por defecto 5). |
| `PANEL_PAGE_SIZE` | panel | Filas por página de la tabla (por defecto 25). |
| `PANEL_METRICS_WINDOW` | API | Sobre cuántas fichas recientes se calcula la analítica (por defecto 1000). |

## Cómo escala

- **Tabla:** una página por petición (`GET /crm/candidates?limit=…&cursor=…`), con filtros y
  orden resueltos por índices de Firestore (`deploy/firestore.indexes.json`). Botones
  "Anterior" y "Siguiente"; al cambiar un filtro se vuelve a la página 1.
- **KPI de la tabla y totales por estado:** `count_by_status()`, agregaciones que no leen
  las fichas.
- **Resto de la analítica** (embudo, abandono, motivos, tiempos, toques, costo): sobre las
  `PANEL_METRICS_WINDOW` fichas actualizadas más recientemente. El panel dice sobre
  cuántas se calculó. El camino a métricas exactas (contadores agregados al guardar o
  export a BigQuery) está en la spec 018.

## En local, con datos de demo (sin LLM ni WhatsApp)

La API de demo usa el flujo real (intake, orquestador, reglas, puntaje y `timeline`) con un
agente guionado y el canal de consola. Trae valores por defecto para el demo local:
`PANEL_PASSWORD=demo`, `WHATSAPP_APP_SECRET=demo-secret` y
`WHATSAPP_VERIFY_TOKEN=demo-verify`.

1. API de demo (terminal 1):

```bash
.venv\Scripts\python -m uvicorn screening.crm.demo:create_demo_app --factory --port 8000
```

2. Sembrar los 12 candidatos (terminal 2). Tarda unos segundos: espera la respuesta del
   agente a cada mensaje.

```bash
.venv\Scripts\python scripts\seed_demo.py
```

3. Panel (terminal 2), con `PANEL_PASSWORD=demo` definida:

```bash
.venv\Scripts\python -m streamlit run src/screening/crm/app.py
```

Abrir `http://localhost:8501` y entrar con la contraseña. Los escenarios cubren calificados
(con puntajes distintos, uno en entrevista y uno contratado), un descarte por cada regla
eliminatoria, uno en curso, uno contactado sin respuesta, uno que pidió hablar con una
persona y uno con datos inválidos, en España y México.

Al reiniciar la API en memoria se pierden los datos: hay que volver a sembrar. Con
`SCREENING_REPOSITORY=firestore` persisten, y el seed reconoce los duplicados.

## En local, con la API real

Igual que arriba, pero la API se levanta con
`uvicorn screening.api:create_app_from_env --factory` y `PANEL_PASSWORD` definida. Los
candidatos llegan por `POST /applications` y por el webhook de WhatsApp.

## Despliegue en Cloud Run (link para los evaluadores)

Se despliegan dos servicios en `europe-west1` que escalan a cero: la API (que ya expone el
webhook) y el panel. Los dos leen la misma contraseña de un único secreto de Secret
Manager.

1. **Secreto** (una vez):

```bash
gcloud secrets create panel-password --replication-policy automatic
```

```bash
gcloud secrets versions add panel-password --data-file=-
```

2. **API:** agregarle el secreto. Con el repositorio en memoria, la API debe correr con
   `--max-instances=1`: cada instancia tendría su propia memoria. Con Firestore no hay
   esa restricción. Antes, crear los índices con `python deploy/create_firestore_indexes.py`.

```bash
gcloud run services update orbio-api --region europe-west1 --update-secrets PANEL_PASSWORD=panel-password:latest
```

3. **Imagen del panel** (una vez creado el repositorio `orbio` de Artifact Registry en
   `europe-west1`):

```bash
gcloud builds submit --config deploy/crm/cloudbuild.yaml
```

4. **Servicio del panel**, público (la protección es la contraseña) y con afinidad de
   sesión, porque Streamlit mantiene la sesión por websocket:

```bash
gcloud run deploy orbio-panel --image europe-west1-docker.pkg.dev/gentle-app-313016/orbio/crm:latest --region europe-west1 --allow-unauthenticated --session-affinity --min-instances 0 --max-instances 2 --memory 512Mi --set-env-vars PANEL_API_URL=https://orbio-api-XXXX.a.run.app --update-secrets PANEL_PASSWORD=panel-password:latest
```

5. Compartir con los evaluadores la URL que imprime el comando
   (`https://orbio-panel-…run.app`) y la contraseña por un canal aparte.

Para tener datos sin gastar en LLM en el entorno desplegado, se puede correr
`scripts/seed_demo.py --api-url https://…` (con `PANEL_PASSWORD` definida) contra una API
de demo (`screening.crm.demo:create_demo_app`) desplegada con Firestore.

## Costos

Ambos servicios escalan a cero. El panel consulta la API cada `PANEL_REFRESH_SECONDS` solo
mientras alguien lo tiene abierto: una página de la tabla, los conteos y, en la vista de
métricas, hasta `PANEL_METRICS_WINDOW` lecturas por refresco. El demo y el seed no llaman
al LLM.
