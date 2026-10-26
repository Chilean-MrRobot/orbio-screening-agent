# 019 — Chat web público del demo

**Estado:** implementada
**Ola:** 5 · **Depende de:** 008, 009, 013, 015, 018

## Objetivo
Que cualquiera (los evaluadores, el usuario) pueda probar el agente desde el navegador, sin
WhatsApp ni una cuenta de Meta. El canal es intercambiable: el mismo orquestador, agente,
reglas, puntaje, lease y Firestore atienden la conversación; solo cambia la "boca".

## Alcance
- Incluye: canal `web`, endpoints `/chat` en la API, página pública de chat en la app de
  Streamlit (junto al panel), y la API arrancando sin credenciales de WhatsApp.
- No incluye: autenticación del candidato, límite de tasa por IP (ver "Fuera de alcance").
  WhatsApp sigue implementado (specs 005 y 016) y se activa con sus variables.

## Interfaz
**API** (`src/screening/api/web_chat.py`, público):
- `POST /chat/start` `{full_name, country, phone?, city?}` → simula el JSON del portal
  (`source: "web"`, `application_id: web-<uuid>`); sin teléfono se genera un móvil de prueba
  válido del país. Devuelve `ChatView`. Teléfono inválido → 422; candidato ocupado → 409.
- `POST /chat/{candidate_id}/messages` `{text}` (1–1000 caracteres) → arma un
  `InboundMessage` con `channel=web` y llama a `orchestrator.handle` (con su lease, spec 015).
  Devuelve la conversación actualizada. Ocupado → 409. Conversación cerrada → se devuelve sin
  procesar.
- `GET /chat/{candidate_id}` → `ChatView`. ID desconocido → 404.
- `ChatView`: `candidate_id`, `phone`, `status`, `stage`, `done` y `messages` (solo
  candidato y agente).

El `candidate_id` es un UUID: funciona como llave del chat y no da acceso a otras fichas ni
al panel.

**Canal:** `WebChatChannel` (`channels/web.py`) no envía nada afuera; el mensaje del agente
queda en la ficha y la página lo lee por la API.

**Arranque de la API:** sin `WHATSAPP_VERIFY_TOKEN` ni `WHATSAPP_APP_SECRET`, la API no monta
el webhook ni la cola de lotes, y usa `WebChatChannel`. Con solo uno de los dos, no arranca
(configuración inconsistente). `/chat` se monta siempre.

**Streamlit** (`src/screening/crm/app.py`): dos páginas en un link, con `st.navigation`:
- "Postular (candidato)", pública y por defecto (`crm/chat_page.py`, `crm/chat_client.py`);
- "Panel (recruiter)", con `PANEL_PASSWORD` (spec 018).

## Comportamiento
- El primer mensaje de Sofía llega con la respuesta de `/chat/start`.
- Cada mensaje del candidato es un lote de uno: no hace falta agrupar (en web se escribe un
  mensaje a la vez); el lease evita turnos simultáneos si se envía dos veces.
- Al cerrar (calificado, descartado, opt-out, sin respuesta) la página lo indica y ofrece
  empezar una postulación nueva.
- El calentamiento (spec 011/017) sigue funcionando: sus toques quedan en la conversación y se
  ven al volver a la página.

## Criterios de aceptación
- **Dado** que no hay credenciales de WhatsApp, **cuando** arranca la API, **entonces**
  `/chat/start` existe y `/webhooks/whatsapp` no.
- **Dado** un nombre y un país, **cuando** se llama a `/chat/start` sin teléfono, **entonces**
  se crea la ficha con un móvil de prueba válido y la respuesta trae el saludo de Sofía.
- **Dado** un chat iniciado, **cuando** el candidato responde, **entonces** pasa por el
  orquestador (los datos se guardan, las reglas se evalúan) y la respuesta trae el mensaje del
  agente; los mensajes quedan con `channel=web`.
- **Dado** un candidato con el lease tomado, **cuando** envía un mensaje, **entonces** 409.
- **Dado** un chat cerrado, **cuando** llega otro mensaje, **entonces** no se llama al agente.

## Tests
`tests/unit/test_web_chat.py`. Verificado además contra Firestore y OpenAI reales: la
conversación completa termina `qualified`.

## Fuera de alcance (demo)
Endpoint público sin límite de tasa ni captcha: en producción iría detrás de rate limiting
(Cloud Armor o un middleware por IP) y el chat web solo aceptaría postulaciones firmadas por
el portal.
