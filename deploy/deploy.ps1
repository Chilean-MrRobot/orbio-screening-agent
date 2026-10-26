# Despliega la API y el panel en Cloud Run, más el job de Cloud Scheduler (ver deploy/README.md).
# Requisitos: gcloud autenticado, imágenes construidas (deploy/*/cloudbuild.yaml) y secretos
# subidos (python deploy/sync_secrets.py). Idempotente: se puede correr de nuevo.
#
#   ./deploy/deploy.ps1                       # todo, con el chat web (sin WhatsApp)
#   ./deploy/deploy.ps1 -Only api             # solo la API
#   ./deploy/deploy.ps1 -WhatsApp -PhoneNumberId <id>   # además, el webhook de WhatsApp
param(
    [string]$Project = "gentle-app-313016",
    [string]$Region = "europe-west1",
    [ValidateSet("all", "api", "panel", "scheduler")][string]$Only = "all",
    [switch]$WhatsApp,            # monta el webhook y la cola de Cloud Tasks (spec 016)
    [string]$PhoneNumberId = ""   # WHATSAPP_PHONE_NUMBER_ID de Meta
)
# "Continue": gcloud escribe avisos por stderr (p. ej. "no existe" al probar un secreto) y con
# "Stop" PowerShell 5.1 los trata como fatales. Cada paso revisa $LASTEXITCODE.
$ErrorActionPreference = "Continue"
$registry = "$Region-docker.pkg.dev/$Project/orbio"
$apiSa = "screening-api@$Project.iam.gserviceaccount.com"
$panelSa = "screening-panel@$Project.iam.gserviceaccount.com"

function Test-Secret([string]$Name) {
    gcloud secrets describe $Name --project=$Project *> $null
    return $LASTEXITCODE -eq 0
}

function Get-ServiceUrl([string]$Service) {
    $url = gcloud run services describe $Service --region=$Region --project=$Project --format="value(status.url)" 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return $url
}

if ($Only -in "all", "api") {
    $secrets = @(
        "OPENAI_API_KEY=openai-api-key:latest",
        "PANEL_PASSWORD=panel-password:latest",
        "REENGAGEMENT_TASK_TOKEN=reengagement-task-token:latest"
    )
    # Trazas en LangSmith solo si existe su key en Secret Manager.
    $tracing = "false"
    if (Test-Secret "langsmith-api-key") {
        $secrets += "LANGSMITH_API_KEY=langsmith-api-key:latest"
        $tracing = "true"
    }
    # Agente de voz (spec 020) solo si existe la key de ElevenLabs; sin ella /voice responde 503.
    if (Test-Secret "elevenlabs-api-key") {
        $secrets += "ELEVENLABS_API_KEY=elevenlabs-api-key:latest"
    }

    $envVars = @(
        "SCREENING_REPOSITORY=firestore",
        "FIRESTORE_PROJECT=$Project",
        "LANGSMITH_TRACING=$tracing",
        "LANGSMITH_PROJECT=orbio-screening-agent",
        "LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com",
        "PANEL_METRICS_WINDOW=1000",
        "REENGAGEMENT_MAX_PER_PASS=1000",
        "ELEVENLABS_VOICE_ID=FGY2WhTYpPnrIDTdsKH5",
        "ELEVENLABS_TTS_MODEL=eleven_flash_v2_5",
        "ELEVENLABS_STT_MODEL=scribe_v1"
    )
    $apiUrl = Get-ServiceUrl "screening-api"
    if ($WhatsApp) {
        $secrets += @(
            "TASKS_TOKEN=tasks-token:latest",
            "WHATSAPP_VERIFY_TOKEN=whatsapp-verify-token:latest",
            "WHATSAPP_APP_SECRET=whatsapp-app-secret:latest"
        )
        if (Test-Secret "whatsapp-access-token") { $secrets += "WHATSAPP_ACCESS_TOKEN=whatsapp-access-token:latest" }
        $envVars += @(
            "TASKS_BACKEND=cloud",
            "CLOUD_TASKS_PROJECT=$Project",
            "CLOUD_TASKS_LOCATION=$Region",
            "CLOUD_TASKS_QUEUE=inbound-batches",
            "WHATSAPP_BATCH_SECONDS=8",
            "WHATSAPP_GRAPH_VERSION=v23.0"
        )
        if ($PhoneNumberId) { $envVars += "WHATSAPP_PHONE_NUMBER_ID=$PhoneNumberId" }
        # Las tareas apuntan al propio servicio: la URL existe desde el primer despliegue.
        if (-not $apiUrl) { throw "con -WhatsApp, primero despliega sin el flag para crear la URL" }
        $envVars += "TASKS_SERVICE_URL=$apiUrl"
    }

    # Armar cada argumento completo: `--x=($a -join ",")` PowerShell lo pasa como DOS argumentos.
    $envArg = "--set-env-vars=" + ($envVars -join ",")
    $secretsArg = "--set-secrets=" + ($secrets -join ",")
    gcloud run deploy screening-api --project=$Project --region=$Region `
        --image="$registry/api:latest" --service-account=$apiSa `
        --allow-unauthenticated `
        --cpu=1 --memory=1Gi --concurrency=20 --timeout=300 `
        --min-instances=0 --max-instances=3 `
        $envArg $secretsArg
    if ($LASTEXITCODE -ne 0) { throw "falló el despliegue de la API" }
    Write-Host "API: $(Get-ServiceUrl 'screening-api')"
}

if ($Only -in "all", "panel") {
    $apiUrl = Get-ServiceUrl "screening-api"
    if (-not $apiUrl) { throw "primero hay que desplegar la API" }
    gcloud run deploy screening-panel --project=$Project --region=$Region `
        --image="$registry/crm:latest" --service-account=$panelSa `
        --allow-unauthenticated --session-affinity `
        --cpu=1 --memory=1Gi --min-instances=0 --max-instances=2 `
        --set-env-vars="PANEL_API_URL=$apiUrl,PANEL_REFRESH_SECONDS=5,PANEL_PAGE_SIZE=25" `
        --set-secrets="PANEL_PASSWORD=panel-password:latest"
    if ($LASTEXITCODE -ne 0) { throw "falló el despliegue del panel" }
    Write-Host "Panel: $(Get-ServiceUrl 'screening-panel')"
}

if ($Only -in "all", "scheduler") {
    $apiUrl = Get-ServiceUrl "screening-api"
    $token = gcloud secrets versions access latest --secret=reengagement-task-token --project=$Project
    $job = "reengagement-pass"
    $jobArgs = @("--location=$Region", "--project=$Project", "--schedule=*/15 * * * *",
              "--time-zone=Europe/Madrid", "--uri=$apiUrl/tasks/reengagement", "--http-method=POST",
              "--attempt-deadline=300s")
    gcloud scheduler jobs describe $job --location=$Region --project=$Project *> $null
    if ($LASTEXITCODE -eq 0) {
        gcloud scheduler jobs update http $job @jobArgs --update-headers="X-Task-Token=$token" | Out-Null
    } else {
        gcloud scheduler jobs create http $job @jobArgs --headers="X-Task-Token=$token" | Out-Null
    }
    if ($LASTEXITCODE -ne 0) { throw "falló el job de Scheduler" }
    Write-Host "Scheduler: $job cada 15 min → $apiUrl/tasks/reengagement"
}
