# 020 — Agente de voz en el navegador (ElevenLabs)

**Estado:** implementada
**Ola:** 6 · **Depende de:** 008, 015, 019

## Objetivo
El bonus "Great" del enunciado: que el candidato haga el mismo screening **hablando** con
Sofía desde el navegador. Igual que el chat web (spec 019), el canal es solo otra "boca": el
mismo orquestador, agente, reglas, puntaje, lease y Firestore atienden la conversación. La voz
se suma por fuera, en dos traducciones: audio → texto antes del turno y texto → audio después.

## Enfoque elegido: por turnos sobre el orquestador
1. El candidato graba su respuesta en el navegador (`st.audio_input`, WAV).
2. La API la transcribe con **ElevenLabs Speech-to-Text** (Scribe; detecta español o inglés).
3. El texto entra por el mismo camino que el chat web (`WebChatService.send`, el de
   `/chat/{id}/messages`): un `InboundMessage` con `channel=web` y el lease de la spec 015.
4. Los mensajes nuevos de Sofía se sintetizan con **ElevenLabs Text-to-Speech** (modelo
   multilingüe de baja latencia y voz femenina, configurables) y la página los reproduce sola.

La key de ElevenLabs vive **solo en la API** (Secret Manager en Cloud Run). Ni el navegador ni
Streamlit la ven: Streamlit manda el audio a la API y recibe audio de vuelta.

### Alternativa: ElevenLabs Conversational AI (tiempo real)
ElevenLabs ofrece agentes de voz en tiempo real (WebRTC/WebSocket, con detección de turnos,
interrupciones y latencia de ~1 s) que pueden usar un **LLM propio** expuesto como endpoint
compatible con OpenAI (`custom_llm`).

| | Por turnos (elegido) | Conversational AI |
|---|---|---|
| Reutiliza el orquestador | Tal cual: el turno es el mismo que en WhatsApp y web | Habría que exponerlo como endpoint `chat/completions` con streaming y mapear la sesión de voz a la ficha |
| Separación extracción / respuesta | Igual que hoy (dos llamadas, spec 009) | El endpoint debe devolver solo el mensaje, con la extracción hecha antes; la latencia de las dos llamadas se nota en tiempo real |
| Experiencia | Pulsar para hablar; 3–8 s por turno | Conversación natural, se puede interrumpir |
| Frontend | `st.audio_input` + `st.audio` en Streamlit | Widget o SDK JS de ElevenLabs (Streamlit no maneja WebRTC sin un componente) |
| Costo aprox. | STT por minuto de audio + TTS por carácter (centavos por candidato) | Por minuto de conversación (≈ US$0,08–0,10/min según plan) más el LLM |
| Riesgo para el demo | Bajo: dos llamadas HTTP | Alto: un servicio de streaming nuevo, otro estado de sesión |

Se eligió el enfoque por turnos porque **reutiliza el orquestador sin cambios** (reglas,
puntaje, lease, resumen y calentamiento funcionan solos), se prueba con tests sin red y se
despliega en el mismo Cloud Run. Conversational AI es el paso natural para producción y para
la llamada telefónica (ver "Siguiente paso").

## Alcance
- Incluye: adaptador de ElevenLabs (STT y TTS), endpoints `/voice` en la API, página pública
  "Hablar con Sofía (voz)" en Streamlit, variables y secreto de despliegue.
- No incluye: tiempo real, interrupciones, llamada telefónica (documentada abajo), límite de
  tasa por IP (igual que la spec 019).

## Interfaz
**Adaptador** (`src/screening/adapters/elevenlabs.py`), `httpx` con cliente inyectable:
- `ElevenLabsVoice.transcribe(audio, *, filename, content_type) -> Transcript(text, language)`:
  `POST /v1/speech-to-text` (multipart, `model_id`); sin `language_code` (Scribe detecta).
- `ElevenLabsVoice.synthesize(text, *, language) -> SpeechAudio(data, mime_type)`:
  `POST /v1/text-to-speech/{voice_id}?output_format=...` con `model_id` y `language_code`.
- `ElevenLabsSettings.from_env()` → `None` sin `ELEVENLABS_API_KEY` (voz deshabilitada).
- Errores: `VoiceError` con un mensaje claro (timeout, HTTP de ElevenLabs, respuesta inválida).
  El cuerpo de error de ElevenLabs se resume; la key nunca se registra.

**Variables** (`.env.example`):

| Variable | Por defecto | Uso |
|---|---|---|
| `ELEVENLABS_API_KEY` | — | Sin ella, la voz queda deshabilitada |
| `ELEVENLABS_VOICE_ID` | `FGY2WhTYpPnrIDTdsKH5` (Laura, voz femenina prediseñada) | Voz de Sofía; se puede cambiar por una nativa en español de la biblioteca |
| `ELEVENLABS_TTS_MODEL` | `eleven_flash_v2_5` | Multilingüe y de menor latencia (≈ 1 s por mensaje) |
| `ELEVENLABS_STT_MODEL` | `scribe_v1` | Scribe |
| `ELEVENLABS_OUTPUT_FORMAT` | `mp3_44100_64` | MP3 liviano (≈ 8 KB/s) |
| `VOICE_MAX_AUDIO_BYTES` | `5000000` | Tamaño máximo de la grabación |
| `VOICE_MAX_AUDIO_SECONDS` | `60` | Duración máxima (se mide en WAV) |
| `ELEVENLABS_TIMEOUT_SECONDS` | `30` | Timeout de cada llamada |

Son parámetros de infraestructura (como la versión de la Graph API de WhatsApp), no de
negocio; por eso viven en variables y no en `config/`.

**API** (`src/screening/api/voice.py`, pública, se monta siempre):
- `GET /voice/status` → `{enabled}`.
- `POST /voice/start` (mismo cuerpo JSON que `/chat/start`) → crea la postulación y devuelve
  `VoiceTurn` con el saludo hablado.
- `POST /voice/{candidate_id}/turn` (multipart, campo `audio`) → transcribe, procesa el turno
  y devuelve `VoiceTurn`.
- `VoiceTurn`: `transcript` (lo que entendió; `null` en `/start`), `chat` (`ChatView` de la
  spec 019), `audio_base64` y `audio_mime` (la respuesta hablada, o `null`) y `audio_error`.
  El audio viaja en el mismo JSON: un solo viaje por turno y nada que guardar en el servidor.

El texto que se sintetiza son los mensajes de Sofía posteriores al último mensaje del
candidato (o todos, en el saludo), sin emojis ni marcas de Markdown, en el idioma de la ficha.

**Errores:**

| Caso | Respuesta |
|---|---|
| Sin `ELEVENLABS_API_KEY` | 503 "la voz no está habilitada"; `/voice/status` → `enabled=false` y la página lo dice |
| Audio vacío, que no es audio, o WAV ilegible | 422 |
| Audio más grande que `VOICE_MAX_AUDIO_BYTES` | 413 |
| WAV más largo que `VOICE_MAX_AUDIO_SECONDS` | 413 |
| No se entendió nada (transcripción vacía) | 422 "no se escuchó nada" (no se llama al agente) |
| Falla el STT | 502 (el turno no se procesa) |
| Falla el TTS | 200 con `audio_base64=null` y `audio_error`: el turno ya quedó guardado y se lee como texto |
| ID desconocido / ocupado / cerrado | 404 / 409 / se devuelve sin procesar (igual que el chat web) |

**Chat web:** `web_chat.py` expone `WebChatService` (`start`, `send`, `load`), que usan los
dos routers. El comportamiento de `/chat` no cambia.

**Streamlit** (`crm/voice_page.py`, `crm/voice_client.py`): tercera página pública "Hablar con
Sofía (voz)". Formulario igual al del chat; historial como texto; `st.audio_input` para grabar;
la respuesta se reproduce sola (`st.audio(autoplay=True)`, una sola vez) y se muestra lo que la
API entendió. Sin voz habilitada, avisa y apunta a la página de chat.

## Criterios de aceptación
- **Dado** que no hay `ELEVENLABS_API_KEY`, **cuando** se llama a `/voice/status` y a
  `/voice/start`, **entonces** `enabled=false` y 503.
- **Dado** un nombre y un país, **cuando** se llama a `/voice/start`, **entonces** se crea la
  ficha y la respuesta trae el saludo como texto y como audio.
- **Dado** un chat de voz iniciado, **cuando** llega una grabación, **entonces** se transcribe,
  el texto pasa por el orquestador (datos guardados, reglas evaluadas, `channel=web`) y la
  respuesta trae la transcripción, la conversación y el audio de los mensajes nuevos de Sofía.
- **Dado** un audio vacío, demasiado grande o demasiado largo, **cuando** se envía,
  **entonces** 422 o 413 y no se llama a ElevenLabs ni al agente.
- **Dado** que ElevenLabs falla al sintetizar, **cuando** se procesa un turno, **entonces** la
  conversación se devuelve igual, sin audio y con `audio_error`.
- **Dado** que ElevenLabs falla al transcribir, **cuando** se envía audio, **entonces** 502 y
  no se llama al agente.

## Tests
- `tests/unit/test_elevenlabs.py`: adaptador con `httpx.MockTransport` (STT, TTS, errores
  HTTP, timeout, respuesta inválida, settings desde el entorno, limpieza del texto hablado).
- `tests/unit/test_voice.py`: endpoints con un adaptador falso y el orquestador real con el
  agente guionado; límites; voz deshabilitada; cliente de Streamlit.
- `tests/integration/test_elevenlabs_integration.py`: ciclo real TTS → STT con un texto corto
  (se salta sin `ELEVENLABS_API_KEY`).

Verificado además contra ElevenLabs real con el agente guionado: saludo hablado en ~0,7 s y un
turno de voz (STT + orquestador + TTS) en ~1,2 s, sin contar el LLM.

Sin golden dataset: la voz no agrega llamadas al LLM (los turnos ya están cubiertos por las
suites de respuesta y extracción).

## Fuera de alcance (demo)
- Tiempo real e interrupciones (Conversational AI, arriba).
- Duración medida solo en WAV (lo que graba `st.audio_input`); otros formatos se limitan por
  tamaño. En producción se mediría con `ffprobe` o con la duración que devuelve Scribe.
- Endpoint público sin límite de tasa: igual que el chat web, iría detrás de rate limiting.
  La voz cuesta por uso, así que en producción además habría un tope de turnos por ficha.

## Siguiente paso: llamada telefónica saliente (no implementada)
Un botón **"Llamar"** en la ficha del panel para que Sofía llame al candidato (por ejemplo
cuando no responde por WhatsApp, como un toque más del calentamiento de la spec 011).

**Diseño**
- ElevenLabs Conversational AI con su integración nativa de Twilio: se importa a ElevenLabs un
  número de Twilio y la llamada sale con `POST /v1/convai/twilio/outbound-call`
  (`agent_id`, `agent_phone_number_id`, `to_number` y variables dinámicas como el nombre).
- El agente de ElevenLabs usa como LLM un endpoint nuevo de la API, `POST /voice/llm/chat/completions`
  (compatible con OpenAI, con streaming), que identifica la ficha por una variable dinámica
  (`candidate_id`) y llama al orquestador: extracción, reglas y respuesta como en cualquier
  turno, con `channel=phone`. Así las reglas y el puntaje siguen siendo código.
- Al cortar, el webhook post-llamada de ElevenLabs (`/webhooks/elevenlabs`, firmado con HMAC)
  guarda la transcripción, la duración y el costo en el timeline de la ficha.
- El panel muestra el botón solo si el candidato dio consentimiento y está en horario hábil de
  su país (las mismas reglas de `cadences.yaml`); el clic llama a `POST /crm/candidates/{id}/call`.

**Costos aproximados** (verificar con las tarifas vigentes)
- ElevenLabs Conversational AI: ≈ US$0,08–0,10 por minuto en los planes Creator/Pro.
- Twilio: número ≈ US$1–15/mes según país; llamada saliente a móvil ≈ US$0,03–0,10/min
  (España y México). Una llamada de screening de 4 minutos ≈ US$0,50–0,80 en total, más el LLM.

**Requisitos**
- Cuenta de Twilio con un número habilitado para llamar a ES y MX (en México y España se exige
  documentación del titular), importado en ElevenLabs.
- Un agente creado en ElevenLabs (voz, primer mensaje, idioma) apuntando al endpoint de LLM.
- Consentimiento explícito para llamadas y aviso de grabación al inicio (GDPR en España).
- Cambios de contrato a coordinar: `ChannelName.PHONE` en `domain/` y un `VoiceCallChannel`
  detrás de `ports.py`.
