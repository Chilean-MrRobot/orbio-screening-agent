# CRM de candidatos

App de Streamlit para que los recruiters sigan a los candidatos en vivo: tabla que se
refresca sola, ficha con la conversación, los datos extraídos (y el JSON crudo), las reglas
eliminatorias, el desglose del puntaje, el resumen y el `timeline`, botones para mover el
estado y un panel de métricas. Diseño en [`specs/013-crm.md`](../specs/013-crm.md).

```
Navegador ──contraseña──▶ Streamlit (CRM) ──HTTP + Bearer token──▶ API FastAPI ──▶ repositorio
                                                                    /crm/...       (memoria o Firestore)
```

El CRM nunca lee la base directo: con el repositorio en memoria, los datos viven dentro del
proceso de la API.

## Variables de entorno

| Variable | Dónde | Para qué |
|---|---|---|
| `CRM_API_TOKEN` | API y CRM | Token compartido. Sin él, la API no monta las rutas `/crm`. |
| `CRM_API_URL` | CRM | URL base de la API (por defecto `http://localhost:8000`). |
| `CRM_PASSWORD` | CRM | Contraseña simple para entrar al CRM. Sin ella, el CRM no arranca. |
| `CRM_REFRESH_SECONDS` | CRM | Cada cuántos segundos se refresca (por defecto 5). |

## En local, con datos de demo (sin LLM ni WhatsApp)

La API de demo usa el flujo real (intake, orquestador, reglas, puntaje y `timeline`) con un
agente guionado y el canal de consola. Trae valores por defecto para el demo local:
`CRM_API_TOKEN=demo-token`, `WHATSAPP_APP_SECRET=demo-secret` y
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

3. CRM (terminal 2), con `CRM_API_TOKEN=demo-token` y `CRM_PASSWORD` definidas:

```bash
.venv\Scripts\python -m streamlit run src/screening/crm/app.py
```

Abrir `http://localhost:8501`. Los escenarios cubren calificados (con puntajes distintos,
uno en entrevista y uno contratado), un descarte por cada regla eliminatoria, uno en curso,
uno contactado sin respuesta, uno que pidió hablar con una persona y uno con datos
inválidos, en España y México.

Al reiniciar la API en memoria se pierden los datos: hay que volver a sembrar. Con
`SCREENING_REPOSITORY=firestore` persisten, y el seed reconoce los duplicados.

## En local, con la API real

Igual que arriba, pero la API se levanta con
`uvicorn screening.api:create_app_from_env --factory` y `CRM_API_TOKEN` definido. Los
candidatos llegan por `POST /applications` y por el webhook de WhatsApp.

## Despliegue en Cloud Run (link para los evaluadores)

Se despliegan dos servicios que escalan a cero: la API (que ya expone el webhook) y el CRM.

1. **API:** agregar `CRM_API_TOKEN` a sus variables (mejor como secreto de Secret
   Manager). Con el repositorio en memoria, la API debe correr con
   `--max-instances=1`: cada instancia tendría su propia memoria. Con Firestore no hay
   esa restricción.

```bash
gcloud run services update orbio-api --region us-central1 --update-secrets CRM_API_TOKEN=crm-api-token:latest
```

2. **Imagen del CRM** (una vez creado el repositorio `orbio` de Artifact Registry):

```bash
gcloud builds submit --config deploy/crm/cloudbuild.yaml
```

3. **Servicio del CRM**, público (la protección es la contraseña) y con afinidad de sesión,
   porque Streamlit mantiene la sesión por websocket:

```bash
gcloud run deploy orbio-crm --image us-central1-docker.pkg.dev/PROJECT_ID/orbio/crm:latest --region us-central1 --allow-unauthenticated --session-affinity --min-instances 0 --max-instances 2 --memory 512Mi --set-env-vars CRM_API_URL=https://orbio-api-XXXX.a.run.app --update-secrets CRM_API_TOKEN=crm-api-token:latest,CRM_PASSWORD=crm-password:latest
```

4. Compartir con los evaluadores la URL que imprime el comando (`https://orbio-crm-…run.app`)
   y la contraseña por un canal aparte.

Para tener datos sin gastar en LLM en el entorno desplegado, se puede correr
`scripts/seed_demo.py --api-url https://…` contra una API de demo
(`screening.crm.demo:create_demo_app`) desplegada con Firestore.

## Costos

Ambos servicios escalan a cero. El CRM consulta la API cada `CRM_REFRESH_SECONDS` solo
mientras alguien lo tiene abierto; el demo y el seed no llaman al LLM.
