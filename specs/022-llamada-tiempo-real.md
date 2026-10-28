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
   una frase de espera que se alterna por turno (`voice_wait`, `voice_wait_2`, `voice_wait_3`).
   ElevenLabs la dice mientras el orquestador trabaja. No se guarda en la ficha. Con 0 se
   desactiva.
2. **Streaming apenas existe la respuesta:** el texto sale en cuanto el turno termina, entero en
   un solo chunk (ver "Voz cortada").
3. **TTS de ElevenLabs** `eleven_turbo_v2_5` sin optimización de latencia, y WebRTC (ver "Voz
   cortada").
4. **Stream vivo:** si el turno sigue después de `VOICE_WAIT_REPEAT_SECONDS`, se dice una
   segunda frase (`voice_wait_more`). Entre medio, cada `VOICE_HEARTBEAT_SECONDS` sale un chunk
   vacío (`delta.content = ""`), que no se dice. Ver "Corrección tras la prueba en producción".
5. **Arranque en frío:** con `LITELLM_LOCAL_MODEL_COST_MAP=True`, LiteLLM no descarga su mapa de
   precios. Al arrancar, la API hace una llamada mínima a cada modelo configurado, en paralelo
   (`api/warmup.py`, `LLM_WARMUP`). Ese calentamiento retrasa el arranque como máximo
   `LLM_WARMUP_TIMEOUT_SECONDS`; si tarda más, sigue en segundo plano. `deploy.ps1 -MinInstances 1`
   deja una instancia caliente para la presentación, y la API usa `--cpu-boost`.

Se mide y registra (log `voice_llm_turn`): ms hasta el primer chunk, ms del turno, frases de
espera dichas y heartbeats enviados. El calentamiento queda en el log `llm_warmup`.

**Arranque en frío medido** (local; primera llamada después de importar LiteLLM, como en la API):

| | Extracción (gpt-4.1-mini) |
|---|---|
| Sin mapa local ni calentamiento | 8,8 s (igual que en producción) |
| Con `LITELLM_LOCAL_MODEL_COST_MAP=True` | 3,3 s |
| Después del calentamiento | 1,2 s |

El calentamiento de los tres modelos en paralelo tarda 5,6 s.

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

El endpoint **no supone un modelo**: transmite en cuanto el orquestador entrega texto y no
llama al LLM por su cuenta. Si se cambian los modelos de extracción y respuesta en
`config/client.yaml` (por ejemplo, por modelos sin razonamiento), el turno se acorta y la
llamada lo nota sin cambiar nada aquí.

Siguiente paso, fuera de alcance: **streaming por token de la respuesta**. Cambia el contrato
del agente (`ports.py`), así que se coordina aparte:
1. `ScreeningAgent.reply` acepta un callback opcional `on_delta(text)`. El adaptador ADK/LiteLLM
   lo llama con cada fragmento cuando el modelo hace streaming, y si no lo hace, una sola vez
   con el texto final. Así funciona con cualquier modelo.
2. El orquestador pasa ese callback durante el turno y guarda el mensaje completo al final,
   como hoy.
3. Este endpoint pone los fragmentos en una `asyncio.Queue` (desde el hilo del turno, con
   `loop.call_soon_threadsafe`) y los emite como chunks SSE a medida que llegan. La frase de
   espera solo sale si no llegó ningún fragmento antes de `VOICE_WAIT_AFTER_SECONDS`.

La extracción sigue antes de la respuesta, porque decide qué preguntar. Con modelos sin
razonamiento, el primer token llegaría en ~1–2 s.

## Casos borde
- **Interrupción:** ElevenLabs cancela la respuesta en curso y manda otro pedido con lo nuevo.
  El turno anterior sigue en su hilo con el lease tomado. El pedido nuevo espera el lease
  hasta `VOICE_BUSY_WAIT_SECONDS`; si no lo obtiene, responde "un segundo" con la plantilla
  `voice_busy`. La respuesta del turno cancelado queda en la ficha aunque no se haya oído.
- **Conversación cerrada:** se responde el último mensaje de Sofía y se cuelga con `end_call`.
- **Reintento de ElevenLabs con el mismo texto:** no se procesa otra vez. Se considera
  reintento cuando el texto es igual al último mensaje del candidato en la ficha y la
  respuesta de Sofía a ese mensaje todavía no existe o no viene en el historial como su último
  mensaje. Si el candidato repite de verdad ("sí" y otra vez "sí"), sí escuchó la respuesta
  intermedia y se procesa.
- **Pedido sin texto nuevo** (el historial termina en un mensaje de Sofía, p. ej. la frase de
  espera): se espera el turno en curso y se devuelve lo que falta decir. Si no hay nada
  pendiente, se repite la última pregunta. **Nunca se devuelve un stream sin texto.**

## Corrección tras la prueba en producción
Primera llamada real: el turno 1 tardó 11 s. La frase de espera salió a 1,5 s y después hubo
9,5 s sin chunks. ElevenLabs tiene `cascade_timeout_seconds` en **4 s por defecto**: sin
noticias del LLM, "cascadea", reintenta o pasa a un LLM de respaldo. Los dos pedidos nuevos
llegaron con el historial terminado en la frase de espera, sin texto nuevo. La API respondió
vacío en 1–2 ms y ElevenLabs cortó con `custom_llm_error: LLM Cascade Error: TimeoutError`.
Por eso el candidato solo oyó la frase de espera.

Arreglos:
1. **Agente** (`deploy/elevenlabs_agent.py`): `cascade_timeout_seconds: 15` (el máximo) y
   `backup_llm_config: disabled`. Un LLM de respaldo respondería sin el orquestador, sin las
   reglas y sin la ficha. Ya está aplicado al agente.
2. **Endpoint:** los reintentos y los pedidos sin texto esperan el turno en curso, leyendo la
   ficha (Firestore) hasta `VOICE_BUSY_WAIT_SECONDS`, sin estado en memoria. Devuelven solo las
   frases que ElevenLabs no dijo y nunca devuelven vacío: si el turno no termina a tiempo,
   dicen la frase de espera.
3. **Stream vivo:** heartbeats (chunks vacíos) y una segunda frase de espera mientras corre el
   turno.

**Verificado en producción** con el agente real (`scripts/voice_call_probe.py`, 4 turnos
seguidos, instancia caliente, extracción con gpt-4.1-mini):

| Turno | Primer audio (servidor) | Turno en el servidor | Respuesta completa en ElevenLabs |
|---|---|---|---|
| 1 | 1,5 s (frase de espera) | 4,3 s | 5,1 s |
| 2 | 1,5 s | 3,0 s | 3,8 s |
| 3 | 1,5 s | 3,6 s | 5,0 s |
| 4 | 1,5 s | 3,0 s | 4,3 s |

En modo texto, ElevenLabs entrega cada respuesta completa en un solo `agent_response`, que incluye
la frase de espera. En voz, el TTS empieza con el primer chunk, a los ~1,5 s.

El probe tenía un error propio: medía el "silencio" del agente desde el último evento, y los
`ping` de ElevenLabs (cada ~2 s) lo reiniciaban. Por eso nunca enviaba el primer
`user_message`. Ahora el silencio se mide desde la última `agent_response`
(`tests/unit/test_voice_call_probe.py`).

No se sabe todavía si ElevenLabs cuenta los chunks vacíos para su timeout. Si no los cuenta,
el límite real es de 15 s sin texto, y la segunda frase de espera a los 6 s lo cubre en los
turnos de hasta ~20 s. Se confirma con `scripts/voice_call_probe.py` después de desplegar.

## Voz cortada
Al probar desde el teléfono, la voz de Sofía "se escucha súper cortada", poco natural.

**Qué se midió:**
- La llamada real del teléfono (WebRTC/LiveKit), con su transcript, sus métricas y su audio
  descargados de la API de conversaciones de ElevenLabs.
- Llamadas por WebSocket con `scripts/voice_call_probe.py --record <carpeta>`, que guarda un WAV
  por turno. Para cada turno da los cortes que oiría un reproductor en vivo (audio que llega
  cuando el anterior ya sonó) y las pausas dentro del audio.

**Hallazgos en la llamada del teléfono (98 s, 7 turnos):**
- **No hubo interrupciones** (eco que corte a Sofía), salvo la del candidato al final.
- **El audio no viene cortado por dentro.** Las pausas en la voz de Sofía son de 0,2–0,5 s
  entre frases.
- **El ritmo sí está cortado.** Cada turno tiene este patrón:

  | Paso | Duración |
  |---|---|
  | Silencio después de que habla el candidato | ~2,6 s |
  | "Vale, déjame anotarlo." | 1,2 s |
  | Silencio | 0,3–3,4 s |
  | En los turnos largos: "Un momento más, ya casi lo tengo." | — |
  | Otro silencio | — |
  | La respuesta | — |

  ElevenLabs mide la primera frase a los 2,2 s y la última a los 3,6–8,6 s.
- **La frase de espera es idéntica en los 7 turnos.**
- **TTS:** `eleven_flash_v2_5` con `optimize_streaming_latency: 3`, el máximo, que degrada la
  entonación. Salida `pcm_16000`. TTFB del TTS: 0,09–0,11 s.
- **El texto llegaba frase por frase,** cada una en su chunk. ElevenLabs entona mejor un texto
  completo.
- **Los ejemplos del chat** ("Por ejemplo: sí / no.") llegan con la barra al TTS.

**Cambios:**
1. **La respuesta va entera en un solo chunk** en lugar de frase por frase. Los heartbeats
   vacíos se mantienen: no se dicen, en el audio no se ven cortes asociados y siguen haciendo
   falta para el timeout de cascada.
2. **Las frases de espera se alternan por turno.** La frase sale del historial de ElevenLabs, así
   que un reintento del mismo turno elige la misma y no la repite. La segunda frase
   (`voice_wait_more`) queda solo para turnos de más de 6 s.
   - Primera versión: "Vale, déjame anotarlo.", "Ajá, un segundo." y "Muy bien, déjame ver.".
   - En la prueba del usuario sonaban raras: afirmaban algo que no pasaba, como "anotarlo"
     después de una pregunta. Ahora son asentimientos cortos y neutros: "Ajá.", "Mmm, vale."
     y "Okey.". Para el turno largo, "Dame un segundito.".
3. **TTS del agente** (`build_agent_config`, `deploy/elevenlabs_agent.py`):

   | Ajuste | Antes | Ahora |
   |---|---|---|
   | Modelo | `eleven_flash_v2_5` | `eleven_turbo_v2_5` (configurable con `ELEVENLABS_AGENT_TTS_MODEL`; la voz por turnos de la spec 020 sigue con flash) |
   | `optimize_streaming_latency` | 3 | 0 |
   | Salida | `pcm_16000` | `pcm_24000` |

   Ya está aplicado al agente, con un PATCH solo de `tts` que no toca el prompt. Medido con el
   probe: el TTFB del TTS pasó de 0,09–0,11 s a 0,14–0,34 s, poco al lado de los 3–8 s del turno.
4. **`speakable`:**
   - "sí / no" se dice "sí, no";
   - las viñetas de lista ("- ", "• ", "1. ") se quitan;
   - cada línea termina en pausa (el resumen).

   Sigue siendo idempotente, porque la llamada compara textos ya dichos.
5. **Widget:** la comprobación del permiso suelta el micrófono enseguida. Antes, la llamada
   corría con dos capturas abiertas, porque el SDK abre la suya.

**Queda para después:**
- El silencio de ~2,6 s entre el fin del candidato y la frase de espera: ~0,7 s de detección
  de fin de turno y red, más `VOICE_WAIT_AFTER_SECONDS` (1,5 s). Bajarlo a ~1 s acorta la
  espera pero hace que la frase suene casi siempre.
- El streaming por token de la respuesta (ver "Latencia").
- Los ejemplos tipo chat en el prompt de respuesta. Los prompts están en cambio en otra rama.

## Criterios de aceptación
- **Dado** un pedido sin el bearer correcto, **cuando** llega al endpoint LLM, **entonces**
  401 y no se llama al orquestador.
- **Dado** un pedido válido con `candidate_id` en el prompt de sistema, **cuando** el último
  mensaje es del usuario, **entonces** pasa por el orquestador (datos guardados, `channel=web`)
  y la respuesta trae solo los mensajes nuevos de Sofía, en formato OpenAI (SSE o JSON).
- **Dado** un turno lento, **cuando** pasan `VOICE_WAIT_AFTER_SECONDS`, **entonces** el
  primer chunk es la frase de espera y después llega la respuesta, entera en un solo chunk.
- **Dado** el segundo turno del candidato, **cuando** hace falta frase de espera, **entonces**
  es distinta de la del primero (`voice_wait_2`).
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
