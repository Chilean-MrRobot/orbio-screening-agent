# Despliegue en GCP

Proyecto `gentle-app-313016`, región `europe-west1` (datos en la UE por GDPR). Todo escala a
cero: sin uso, el costo es prácticamente nulo.

## Qué queda desplegado

| Recurso | Nombre | Para qué |
|---|---|---|
| Cloud Run | `screening-api` | API: chat web (`/chat`), postulaciones, tareas y API del panel |
| Cloud Run | `screening-panel` | Streamlit: chat público + panel de recruiters (con contraseña) |
| Firestore | `(default)` | Fichas, leases y mensajes entrantes |
| Secret Manager | `openai-api-key`, `panel-password`, `reengagement-task-token`, … | Secretos, nunca en el código ni en la imagen |
| Cloud Scheduler | `reengagement-pass` | Calentamiento cada 15 min → `POST /tasks/reengagement` |
| Cloud Tasks | `inbound-batches` | Agrupación de mensajes de WhatsApp (solo con `-WhatsApp`) |
| Artifact Registry | `orbio` | Imágenes `api` y `crm` |

Cuentas de servicio con permisos mínimos: `screening-api` (Firestore, Cloud Tasks, secretos,
logs) y `screening-panel` (solo secretos: lee los datos por la API).

## Pasos (desde la raíz del repo, con `gcloud` autenticado)

```bash
# 1. Secretos desde .env (idempotente, no imprime valores)
python deploy/sync_secrets.py --project gentle-app-313016

# 2. Índices compuestos de Firestore (idempotente)
python deploy/create_firestore_indexes.py --project gentle-app-313016 --wait

# 3. Imágenes
gcloud builds submit --config deploy/api/cloudbuild.yaml --region=europe-west1 .
gcloud builds submit --config deploy/crm/cloudbuild.yaml --region=europe-west1 .

# 4. Servicios y Scheduler
./deploy/deploy.ps1
```

Con WhatsApp (cuando haya app de Meta): poner sus valores en `.env`, repetir el paso 1 y
correr `./deploy/deploy.ps1 -Only api -WhatsApp -PhoneNumberId <id>`. El webhook queda en
`<URL de la API>/webhooks/whatsapp` con el `WHATSAPP_VERIFY_TOKEN` del `.env`.

## Notas

- Cloud Run reserva las rutas que terminan en `z`: `/healthz` responde 404 desde Google. Para
  monitorear se usa `/health`.
- El primer request tras un rato sin uso tarda unos segundos más (arranque en frío). Para la
  presentación en vivo conviene abrir el link unos minutos antes, o desplegar con
  `--min-instances=1` durante esa hora.
- `.gcloudignore` evita subir `.env` y entornos locales a Cloud Build.
