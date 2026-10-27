# 022 — Llamada en tiempo real en el navegador (ElevenLabs Conversational AI)

**Estado:** implementada
**Ola:** 7 · **Depende de:** 008, 015, 019, 020, 021

## Objetivo
Pasar de los turnos grabados de la spec 020 a una **llamada**: el candidato pulsa "Llamar",
habla con Sofía con detección de turnos e interrupciones, y la ficha se llena igual que por
WhatsApp o por chat. ElevenLabs pone el audio (WebRTC, STT, TTS, turnos) y **nuestro
orquestador sigue siendo el cerebro**: reglas, puntaje, lease, agendamiento (spec 021) y
Firestore no cambian.

## Arquitectura
```
navegador (SDK @elevenlabs/client, WebRTC)
   │  token de conversación + variables dinámicas (candidate_id, first_name)
   ▼
ElevenLabs Conversational AI ── STT, turnos, interrupciones, TTS (voz de Sofía)
   │  POST /voice/llm/v1/chat/completions  (OpenAI, SSE, Authorization: Bearer <secreto>)
   ▼
nuestra API ── último mensaje del usuario → ConversationOrchestrator.handle → mensajes nuevos de Sofía
```
- El agente de ElevenLabs usa **Custom LLM** apuntando a nuestra API. Su prompt de sistema
  lleva `candidate_id: {{candidate_id}}`; ElevenLabs reemplaza la variable dinámica y nuestro
  endpoint la lee del mensaje `system`. El resto del prompt no se usa: lo que dice Sofía lo
  decide el orquestador.
- La key de ElevenLabs nunca llega al navegador: la API pide un **token de conversación**
  (WebRTC) y lo entrega junto con las variables dinámicas.
- El primer mensaje del agente es la plantilla `first_contact` con `{{first_name}}`: es el
  mismo texto que el orquestador guarda en la ficha al crear la postulación.

## Interfaz
**Endpoint compatible con OpenAI** (`src/screening/api/voice_call.py`):
`POST /voice/llm/v1/chat/completions`
- Auth: `Authorization: Bearer <VOICE_LLM_SECRET>` (comparación en tiempo constante). Sin
  secreto configurado → 503; secreto incorrecto → 401.
- Entrada: `messages` (OpenAI), `stream`, `tools` (opcional), resto ignorado.
- `candidate_id` se lee del mensaje `system` (`candidate_id: <id>`); sin él → 400; ficha
  desconocida → 404.
- El texto del candidato son los mensajes `user` posteriores al último `assistant` (si
  interrumpió, pueden ser varios). Pasa por `WebChatService.send` (mismo camino que el chat
  web, `channel=web`).
- Respuesta: los mensajes de Sofía posteriores al último del candidato, sin emojis ni
  Markdown (`speakable`, spec 020). Con `stream: true`, SSE `chat.completion.chunk` terminado
  en `data: [DONE]`; sin streaming, un `chat.completion`.
- Si la conversación quedó cerrada (calificado, descartado, opt-out) y ElevenLabs ofreció la
  herramienta `end_call`, después del texto se emite una llamada a `end_call` para colgar.

**Inicio de llamada** (pública):
- `GET /voice/call/status` → `{enabled}` (hay `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID` y
  `VOICE_LLM_SECRET`).
- `POST /voice/call/start` (mismo cuerpo que `/chat/start`) → crea la postulación y devuelve
  `CallSession`: `chat` (`ChatView`), `conversation_token`, `dynamic_variables`
  (`candidate_id`, `first_name`) y `language`.
- `POST /voice/call/{candidate_id}/token` → un token nuevo para volver a llamar (el token
  vence). Conversación cerrada → 409.
- Sin configuración → 503. Falla de ElevenLabs al pedir el token → 502.

**Adaptador** (`adapters/elevenlabs_agents.py`, `httpx` con transporte inyectable):
`conversation_token(agent_id)`, `signed_url(agent_id)` y, para el script, secretos del
workspace y agentes (crear, buscar por nombre, actualizar).

**Script** `deploy/elevenlabs_agent.py --api-url <url pública>` (idempotente):
1. Crea o actualiza el secreto del workspace `orbio-voice-llm-secret` con `VOICE_LLM_SECRET`.
2. Busca el agente por `ELEVENLABS_AGENT_ID` o por nombre; si no existe lo crea, y si existe lo
   actualiza. La configuración incluye la voz de Sofía (`ELEVENLABS_VOICE_ID`), el modelo de TTS,
   el idioma `es` con un preset `en`, el primer mensaje `first_contact`, el custom LLM
   (`{api}/voice/llm/v1`) con el secreto, `end_call`, y overrides de idioma permitidos.
3. Imprime el `agent_id` para guardarlo como `ELEVENLABS_AGENT_ID`.

**Variables** (`.env.example`): `ELEVENLABS_AGENT_ID`, `VOICE_LLM_SECRET`,
`VOICE_WAIT_AFTER_SECONDS` (1,5), `VOICE_BUSY_WAIT_SECONDS` (8). Secretos nuevos en
`sync_secrets.py`: `voice-llm-secret` y `elevenlabs-agent-id` (se montan si existen).

**Streamlit** (`crm/call_page.py`): página pública "Llamar a Sofía (tiempo real)". Tiene el
formulario de postulación y un componente HTML (`st.components.v1.html`) con el SDK
`@elevenlabs/client@1.28.0` (IIFE de unpkg, versión fija). El componente tiene botón
llamar/colgar, el estado (conectando, escuchando, hablando) y la transcripción en vivo, y un
campo para escribir en vez de hablar (`sendUserMessage`). Debajo, el historial guardado (de
`/chat/{id}`) con un botón para actualizarlo. La página por turnos de la spec 020 se mantiene.

## Latencia
Un turno nuestro tarda unos 5–8 s: extracción y respuesta con el LLM (spec 009), y a veces
el resumen al calificar. En una llamada, ese silencio parece un corte. Mitigaciones que no
cambian el diseño:
1. **Frase de espera:** si el turno no terminó en `VOICE_WAIT_AFTER_SECONDS`, se envía antes
   la plantilla `voice_wait` ("Vale, déjame anotarlo."). ElevenLabs la dice mientras el
   orquestador trabaja. No se guarda en la ficha. Con 0 se desactiva.
2. **Streaming apenas existe la respuesta:** el texto sale en cuanto el turno termina, frase
   por frase, y ElevenLabs empieza a sintetizar con la primera.
3. **TTS y STT de ElevenLabs** de baja latencia (`eleven_flash_v2_5`) y WebRTC.

Se mide y registra (log `voice_llm_turn`): ms hasta el primer chunk, ms del turno y si hubo
frase de espera.

**Medido** con la API en local, el agente real (OpenAI) y cinco turnos de screening:

| | Mediana | Rango |
|---|---|---|
| Primer audio (frase de espera) | 1,95 s | 1,91–2,12 s |
| Respuesta completa | 6,2 s | 5,4–6,8 s |

Sin la frase de espera, el candidato escucharía ~6 s de silencio en cada turno. A eso se suman
la red hasta Cloud Run y el TTS de ElevenLabs (~0,3–0,5 s).

Las respuestas del agente traen ejemplos pensados para el chat ("Por ejemplo: sí / no."). En voz
suenan raros. Queda como mejora un prompt de respuesta por canal (spec 009), que se controla con
el golden dataset.

Siguiente paso, fuera de alcance: que la respuesta del agente (spec 009) se genere en
streaming dentro del orquestador y la extracción vaya en paralelo. Así bajaría el primer
token a ~1 s, pero cambia el contrato del agente.

## Casos borde
- **Interrupción:** ElevenLabs cancela la respuesta en curso y manda otro pedido con lo nuevo.
  El turno anterior sigue en su hilo con el lease tomado. El pedido nuevo espera el lease
  hasta `VOICE_BUSY_WAIT_SECONDS`; si no lo obtiene, responde "un segundo" con la plantilla
  `voice_busy`. La respuesta del turno cancelado queda en la ficha aunque no se haya oído.
- **Conversación cerrada:** se responde el último mensaje de Sofía y se cuelga con `end_call`.
- **Reintento de ElevenLabs con el mismo texto:** se procesa de nuevo como un mensaje más. Es
  aceptable para el demo; en producción se deduplicaría por `conversation_id` + índice.

## Criterios de aceptación
- **Dado** un pedido sin el bearer correcto, **cuando** llega al endpoint LLM, **entonces**
  401 y no se llama al orquestador.
- **Dado** un pedido válido con `candidate_id` en el prompt de sistema, **cuando** el último
  mensaje es del usuario, **entonces** pasa por el orquestador (datos guardados, `channel=web`)
  y la respuesta trae solo los mensajes nuevos de Sofía, en formato OpenAI (SSE o JSON).
- **Dado** un turno lento, **cuando** pasan `VOICE_WAIT_AFTER_SECONDS`, **entonces** el
  primer chunk es la frase de espera y después llega la respuesta.
- **Dado** que la conversación se cierra en el turno y existe la herramienta `end_call`,
  **cuando** termina el texto, **entonces** el stream trae la llamada a `end_call`.
- **Dado** `/voice/call/start`, **cuando** ElevenLabs entrega el token, **entonces** la
  respuesta trae el token, `candidate_id`, `first_name` y la conversación; la key no aparece.
- **Dado** el script dos veces seguidas, **cuando** el agente y el secreto existen,
  **entonces** se actualizan sin duplicarse.

## Tests
- `tests/unit/test_voice_call.py`: endpoint LLM (auth, formato JSON y SSE, mapeo al
  orquestador real con agente guionado, frase de espera, `end_call`, ocupado) e inicio de
  llamada con transporte falso.
- `tests/unit/test_elevenlabs_agents.py`: adaptador y script (crear y actualizar, idempotente).
- `tests/integration/test_elevenlabs_agents_integration.py`: token de conversación real
  (se salta sin `ELEVENLABS_API_KEY` y `ELEVENLABS_AGENT_ID`).

Sin golden dataset: no hay llamadas nuevas al LLM (el turno es el mismo de la spec 009).

**Verificado contra ElevenLabs real:**
- `deploy/elevenlabs_agent.py` creó el agente y, en una segunda corrida, lo actualizó sin
  duplicarlo. La configuración guardada coincide (custom LLM con el secreto, `end_call`, preset
  `en`, voz y modelo).
- Token de conversación real (test de integración).
- Por WebSocket (`scripts/voice_call_probe.py`), el agente dice el saludo con `{{first_name}}`
  reemplazado.
- ElevenLabs llama a `POST {url}/chat/completions` con el cliente `AsyncOpenAI`, así que la
  ruta `/voice/llm/v1/chat/completions` es la correcta. Se comprobó en los logs de Cloud Run.
- La página carga el SDK y el componente desde la CDN. En el navegador de pruebas sin
  micrófono, la llamada no arranca y lo explica.

El turno completo (ElevenLabs → nuestro endpoint → respuesta hablada) se prueba después de
desplegar con `python scripts/voice_call_probe.py --api-url <URL de la API>`.

## Despliegue (orden)
1. Generar `VOICE_LLM_SECRET` en el `.env`:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Correr `python deploy/sync_secrets.py --project <id>` y desplegar la API
   (`deploy/deploy.ps1 -Only api`). En ese momento existe la URL pública.
3. Correr `python deploy/elevenlabs_agent.py --api-url <URL de la API>`. Imprime el `agent_id`.
4. Guardar `ELEVENLABS_AGENT_ID` en el `.env`, volver a correr `sync_secrets.py` y desplegar
   la API otra vez.

La key de ElevenLabs necesita el permiso **ElevenLabs Agents** (lectura y escritura) además de
TTS y STT.

## Fuera de alcance (demo)
- El endpoint de inicio es público y sin límite de tasa, igual que el chat web. Una llamada
  cuesta por minuto (≈ US$0,08–0,10), así que en producción tendría un tope por IP y por ficha
  y una duración máxima en el agente.
- Webhook post-llamada de ElevenLabs (grabación, duración y costo en el timeline): queda
  documentado en la spec 020, en "llamada telefónica".
- Cambio de idioma en mitad de la llamada: el idioma se fija al iniciar según la ficha.
