# Imagen de la API (webhook de WhatsApp, postulaciones, tareas y panel). Desde la raíz:
#   gcloud builds submit --config deploy/api/cloudbuild.yaml
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
# Instalación editable: config/, prompts/ y knowledge/ se resuelven relativos a /app.
RUN pip install -e .

COPY config ./config
COPY prompts ./prompts
COPY knowledge ./knowledge

# Cloud Run inyecta $PORT.
CMD uvicorn screening.api:create_app_from_env --factory --host 0.0.0.0 --port ${PORT:-8080}
