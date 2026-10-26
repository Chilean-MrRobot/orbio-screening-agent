# Fase 1 — Diseño del proceso de screening

**Cliente:** Grupo Sazón · repartidores · 45 locales en España y México · ~1.000 postulaciones/semana

## 1. Problema y enfoque

Hoy, 13 recruiters hacen ~975 llamadas por semana para ~1.000 postulantes. El 60% no contesta y el 80% del tiempo restante se va en candidatos que no califican. En la práctica se habla con menos del 40% de los postulantes, y tarde, mientras Glovo, Uber Eats y Rappi compiten por el mismo talento.

El diagnóstico es de **canal, orden y velocidad**:

- **Canal:** contacto por WhatsApp (asíncrono) a los minutos de postular, en vez de una llamada que no se contesta. El canal es intercambiable: el demo corre por **chat web** y **voz en el navegador** con el mismo agente, y WhatsApp está implementado para producción.
- **Orden:** las preguntas eliminatorias van primero, así que un candidato no apto sale en menos de un minuto.
- **Velocidad:** el recruiter solo recibe leads limpios y ordenados por puntaje. Deja de filtrar y pasa a cerrar.

## 2. Flujo general

```
JSON del portal → Validación de entrada → Agente conversacional por etapas → Reglas (código) → Puntaje (código)
                                              ↑ RAG (FAQ) · clasificador de emoción · memoria en BD
                         └──────────────── cada paso actualiza la ficha en BD → panel en vivo ──────────────┘
```

- **El panel de recruiters se actualiza en tiempo real, no solo al final.** Cada mensaje, cada campo llenado, cada regla evaluada y cada cambio de etapa se escribe en la ficha. HR ve en vivo en qué etapa va cada candidato, qué datos ya entregó y si está esperando respuesta.

- **El LLM solo conversa y normaliza** las respuestas, por ejemplo: "tengo el carnet de coche hace 5 años" → `{license_type: "B", years: 5}`.
- **Las reglas eliminatorias y el puntaje son código determinista:** auditable, explicable y sin costo de tokens.
- **Una máquina de estados** decide la etapa según la ficha guardada. Cada etapa tiene un prompt corto y enfocado, y todas comparten la misma personalidad y el mismo historial.
- **Persistencia:** se guarda después de cada mensaje. Si el candidato vuelve días después, retoma donde quedó.
- **Configuración en YAML** (sin tocar código): reglas, pesos del puntaje, ciudades de servicio, vocabulario por región y **el calentamiento de candidatos**. Para cada situación (nunca respondió, abandonó a mitad del screening, volvió a responder) se definen los tiempos, el canal de cada toque, el mensaje o la plantilla y cuándo cerrar. Si el candidato responde, se cancelan los toques pendientes. Si vuelve a quedarse en silencio, la cadencia se reinicia desde la etapa en que quedó.

## 3. Etapas de la conversación

| # | Etapa | Objetivo | Bifurcación |
|---|---|---|---|
| 0 | **Entrada** (sin IA) | Validar el JSON del portal, crear o unir la ficha y enviar el primer WhatsApp | Teléfono inválido o ausente → *Datos inválidos* (revisión de HR). Mismo teléfono ya registrado → se une a la ficha existente. Falta un campo opcional → se pregunta en el chat |
| 1 | **Bienvenida** | Presentarse, confirmar el nombre y la vacante, pedir consentimiento de datos | Sin consentimiento → cierre amable, *Descartado (sin consentimiento)* |
| 2 | **Eliminatorias** | Licencia, tipo de licencia, vehículo y ciudad. Las reglas se evalúan **después de cada respuesta** | Falla una regla → cierre amable con motivo, *Descartado (motivo)* |
| 3 | **Perfil** | Experiencia, disponibilidad, horario, fecha de inicio y permiso de trabajo | No descarta, solo alimenta el puntaje |
| 4 | **Cierre** | Resumir los datos para que el candidato los confirme y contarle los próximos pasos | Corrige un dato → se actualiza y se vuelven a correr las reglas. Confirma → se calcula el puntaje, *Calificado* |
| 5 | **Agendamiento** (opcional, YAML) | Ofrecer 3 horarios de entrevista en su zona horaria y reservar el elegido | Elige uno → *Entrevista* agendada. Ninguno sirve dos veces → *Calificado* con "agendar manualmente" para el recruiter |

En cualquier etapa, si el candidato hace una pregunta, el agente consulta el **RAG** (FAQ de Grupo Sazón), responde y vuelve a la pregunta pendiente.

## 4. Campos y validación

| Campo | Validación | Tipo |
|---|---|---|
| `full_name` | Obligatorio. Viene del portal y se confirma | Dato |
| `consent` | Debe ser sí | Eliminatorio |
| `has_license` | Sí/No. "No" descarta | Eliminatorio |
| `license_type` | Debe ser compatible con el vehículo según la tabla del país (YAML) | Eliminatorio |
| `vehicle` | Moto o auto. Se registra si es propio | Eliminatorio (tipo) + puntaje (propio) |
| `city` | Debe ser una ciudad con local de Grupo Sazón (lista en YAML). Variantes como "CDMX" o "Ciudad de México" se normalizan a la misma ciudad | Eliminatorio |
| `availability` | `full_time` · `part_time` · `weekends` | Puntaje |
| `preferred_schedule` | `morning` · `afternoon` · `evening` · `flexible` | Puntaje |
| `delivery_experience` | Años (≥0) y plataformas. **Solo cuenta la experiencia relevante** (reparto, mensajería, courier). Sin experiencia es válido | Puntaje |
| `start_date` | Se normaliza a fecha: "ya" = hoy; "pronto" → se repregunta | Puntaje |
| `work_permit` | Sí/No. Se guarda como dato y lo verifica el humano | Dato |
| `language` | Último idioma usado por el candidato (para los recordatorios) | Sistema |

**No se pide documento de identidad** por chat. Es un dato sensible y lo maneja la etapa humana.

**Puntaje (0–100, pesos de ejemplo en YAML):** experiencia relevante 35 · disponibilidad 25 · inicio ≤7 días 20 · vehículo propio 10 · flexibilidad horaria 10. Cada ficha guarda el **desglose**, para que el recruiter vea por qué un candidato tiene 78 y no 90.

## 5. Personalidad, tono y largo

- **Personalidad fija:** *Sofía*, asistente virtual de selección de Grupo Sazón. Cercana, directa y resolutiva. Dice que es un asistente virtual.
- **Largo:** de 1 a 3 líneas por mensaje y **una pregunta por mensaje**. Es mensajería, no correo.
- **Opciones explícitas** para los campos cerrados: "¿Mañana, tarde, noche o flexible?".
- **Emojis:** máximo uno por mensaje y nunca en un descarte.
- **Vocabulario por región (YAML):** España usa "carnet de conducir" y "coche"; México usa "licencia de manejo" y "carro". La lógica es la misma.
- **Tono adaptativo:** un modelo pequeño de OpenAI clasifica cada mensaje como `neutral` · `frustrado` · `confundido` · `apurado`, y la etiqueta ajusta el system prompt:
  - *Frustrado:* mensajes más cortos, reconoce la molestia y ofrece pasar con una persona.
  - *Confundido:* más simple y con ejemplos.
  - *Apurado:* va directo al grano.

## 6. Casos borde

| Caso | Comportamiento |
|---|---|
| **No responde (nunca o a mitad)** | Cadencia configurable en YAML, distinta para "nunca respondió" y "abandonó a mitad". Por defecto: WhatsApp a las 0 h → WhatsApp a las 24 h → SMS a las 72 h → mensaje de cierre por WhatsApp a los 5 días y estado *Sin respuesta*. Si responde, se cancelan los toques pendientes y retoma en la etapa pendiente. Cada toque enviado queda registrado en el panel. Fuera de la ventana de 24 h, WhatsApp exige plantillas aprobadas por Meta |
| **Respuesta inválida** | Se repregunta con un ejemplo. Tras 2 intentos fallidos, el campo queda `needs_review` y la conversación sigue |
| **Respuesta ambigua** | Se pide precisión: "pronto" → fecha; "algo de experiencia" → años y plataforma |
| **Cambio de idioma (ES ↔ EN)** | El agente responde en el idioma del último mensaje del candidato, sin una regla aparte. Se guarda `language` para los recordatorios |
| **Pregunta fuera del flujo** | Responde con el RAG. Si la respuesta no está en la FAQ, no inventa: "Lo consulto con el equipo". Después vuelve a la pregunta pendiente |
| **Contenido inapropiado u off-topic** | Redirige con amabilidad. Si se repite, cierra la conversación y marca la ficha para HR |
| **Pide hablar con una persona** | Se marca `human_requested` en el panel y se le avisa que lo contactarán |
| **Corrige un dato anterior** | Se actualiza el campo y se vuelven a evaluar las reglas |

## 7. Resultados

- **Calificado:** el candidato confirma su resumen, se calcula el puntaje y la ficha pasa a *Calificado* con un resumen generado automáticamente. Sofía le ofrece horarios de entrevista y, si elige uno, la ficha queda en *Entrevista* con la fecha. En el panel, el recruiter ve la cola ordenada por puntaje y las entrevistas agendadas, y mueve la ficha a *Contratado*.
- **Descartado:** recibe un mensaje breve y respetuoso con el motivo (por ejemplo: "por ahora necesitamos repartidores con licencia vigente"). La ficha queda *Descartado* con el motivo para las métricas.
- **Sin respuesta / Datos inválidos:** visibles en el panel para que HR decida.

**Estados del panel:** Nuevo → Contactado → En screening (etapa) → Calificado / Descartado (motivo) / Sin respuesta / Datos inválidos → Entrevista → Contratado.

## 8. Supuestos y preguntas para el cliente

- Grupo Sazón reparte en moto o en auto. La bicicleta queda fuera porque el criterio exige licencia.
- La tabla de compatibilidad entre licencia y vehículo está simplificada para el demo.
- **Fuera del alcance del demo:** la zona se valida solo por ciudad. Validar por barrio o código postal, o por distancia real a un local (por ejemplo, ≤30 min), queda como siguiente paso.
- El permiso de trabajo se registra pero no descarta automáticamente; la validación legal es humana.
- **Siguientes pasos:** llamada telefónica saliente (voz + telefonía), un guardrail adicional fuera del prompt e integración con el ATS del cliente (diseño en `docs/integracion_ats.md`).
