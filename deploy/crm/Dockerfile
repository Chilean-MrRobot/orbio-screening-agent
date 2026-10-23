# Imagen del CRM de Streamlit (spec 013). Se construye desde la raíz del repo:
#   gcloud builds submit --config deploy/crm/cloudbuild.yaml
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Cloud Run inyecta $PORT.
CMD streamlit run src/screening/crm/app.py \
    --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true \
    --browser.gatherUsageStats=false
