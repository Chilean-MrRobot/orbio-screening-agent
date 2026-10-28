# Conversaciones de ejemplo

Conversaciones **reales** con el agente: Google ADK + OpenAI (`gpt-4.1-mini` para extraer, `gpt-5-mini` para responder y resumir, `gpt-5-nano` para la emoción). Usan el mismo orquestador, reglas, puntaje, agendamiento y resumen que el demo desplegado, con la base en memoria. Las respuestas de Sofía y las fichas están **sin editar**; los mensajes del candidato son guiones de prueba.

| # | Escenario | Resultado |
|---|---|---|
| 1 | España · calificada, con una pregunta en medio y entrevista agendada | `interview` |
| 2 | México · descartado por no tener licencia, tras confirmarlo | `disqualified` |
| 3 | Cambio a inglés a mitad de la conversación · entrevista agendada | `interview` |
| 4 | España · un dato eliminatorio mal dicho que se corrige al confirmar | `in_screening` |
| 5 | España · descartado por ciudad fuera de zona | `disqualified` |
| 6 | México · opt-out | `opted_out` |
| 7 | España · candidato frustrado | `in_screening` |
| 8 | España · preguntas fuera del flujo y respuestas aproximadas | `in_screening` |

## 1. España · calificada, con una pregunta en medio y entrevista agendada

**Qué muestra:** Varios datos en un mensaje, normalizados ("carnet B desde hace 5 años" → `license_type: B`, `license_years: 5`) · responde el pago con la FAQ y vuelve a la pregunta pendiente · "cualquiera, me da igual" → horario `flexible` · resumen para confirmar · puntaje · 3 horarios de entrevista y reserva del elegido.

🛵 **Sofía**

> ¡Hola Lucía! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> hola! sí, tengo tiempo y acepto el tratamiento de mis datos

🛵 **Sofía**

> Perfecto, gracias Lucía. ¿Tienes carnet de conducir vigente? Por ejemplo: sí / no.

👤 **Candidato**

> tengo el carnet B desde hace 5 años, repartiría en mi coche y vivo en Madrid

🛵 **Sofía**

> Genial, gracias por la info, Lucía. ¿Tienes experiencia entregando pedidos o mensajería? Por ejemplo: "2 años en Glovo" o "no, sin experiencia".

👤 **Candidato**

> una pregunta, ¿cuánto se paga por hora?

🛵 **Sofía**

> En España el rango es de 9 a 12 € brutos por hora según la ciudad y la franja (la noche y los fines de semana pagan más). Ahora dime: ¿tienes experiencia en reparto o mensajería? Por ejemplo: "2 años en Glovo" o "no, sin experiencia".

👤 **Candidato**

> llevo 2 años en Glovo, busco jornada completa, puedo empezar el lunes y tengo permiso de trabajo

🛵 **Sofía**

> Perfecto, gracias Lucía. ¿El coche es tuyo o lo prestas? Por ejemplo: "Sí, es mío" o "No, es prestado".

👤 **Candidato**

> sí, el coche es mío. Y de horario, cualquiera, me da igual

🛵 **Sofía**

> Perfecto, Lucía. Resumo lo que tenemos:
>
> - Nombre: Lucía Pérez
> - Consentimiento para usar tus datos: sí
> - Carnet de conducir vigente: sí, tipo B (5 años)
> - Vehículo para repartir: coche (propio)
> - Ciudad: Madrid
> - Disponibilidad: jornada completa
> - Horario preferido: flexible
> - Experiencia en reparto: 2 años en Glovo
> - Fecha de inicio disponible: 2026-11-02
> - Permiso de trabajo: sí
>
> ¿Todo correcto o quieres corregir o añadir algo?

👤 **Candidato**

> sí, todo correcto

🛵 **Sofía**

> ¡Listo, Lucía! 🎉 Tu postulación quedó completa. El siguiente paso es una entrevista de 30 min por videollamada (te enviaremos el enlace antes de la entrevista). ¿Cuál de estos horarios te acomoda? (hora peninsular)
> 1) viernes 30 de octubre, 10:00
> 2) lunes 2 de noviembre, 10:00
> 3) martes 3 de noviembre, 10:00
> Responde con el número, o dime si ninguno te sirve.

👤 **Candidato**

> el primero

🛵 **Sofía**

> ¡Agendado, Lucía! ✅ Tu entrevista es el viernes 30 de octubre, 10:00 (hora peninsular), por videollamada (te enviaremos el enlace antes de la entrevista). Si necesitas cambiarla, escríbenos por aquí.

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "interview",
  "idioma": "es",
  "datos": {
    "full_name": "Lucía Pérez",
    "consent": true,
    "has_license": true,
    "license_type": "B",
    "license_years": 5.0,
    "vehicle_type": "car",
    "owns_vehicle": true,
    "city": "Madrid",
    "availability": "full_time",
    "preferred_schedule": "flexible",
    "delivery_experience": {
      "years": 2.0,
      "platforms": [
        "Glovo"
      ]
    },
    "start_date": "2026-11-02",
    "work_permit": true,
    "needs_review": []
  },
  "eliminatorias": {
    "passed": true,
    "failed_rule": null,
    "reason_code": null
  },
  "puntaje": {
    "total": 100.0,
    "desglose": {
      "experience": 35.0,
      "availability": 25.0,
      "start_date": 20.0,
      "owns_vehicle": 10.0,
      "schedule_flexibility": 10.0
    }
  }
}
```

**Resumen para el recruiter:**

```
Lucía Pérez · Madrid (ES) · Calificado · Puntaje 100/100
Licencia: B (5 años) · Vehículo: auto, propia
Disponibilidad: jornada completa · Horario: flexible
Experiencia: 2 años (Glovo) · Inicio: 2026-11-02 · Permiso de trabajo: sí

Observaciones:
- El candidato preguntó explícitamente “¿cuánto se paga por hora?” y la agente respondió con el rango de 9 a 12 € brutos por hora.
- Confirmó que el coche es propio, disponibilidad jornada completa y inicio el 02/11; no solicitó hablar con una persona ni dejó preguntas pendientes.
- Tono neutral y cordial; aceptó el resumen y la propuesta de entrevista sin objeciones.
```

**Costo del LLM:** 20 llamadas · 46726+1441 tokens · $0.0076 · **Latencia por turno:** 8.0, 3.1, 4.1, 2.9, 4.3, 3.9, 1.7 s

</details>

## 2. México · descartado por no tener licencia, tras confirmarlo

**Qué muestra:** Vocabulario de México ("licencia de manejo") · antes de descartar, Sofía confirma el dato con una pregunta neutra (spec 023) · como vuelve a decir que no, cierre amable con el motivo.

🛵 **Sofía**

> ¡Hola Juan! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> qué onda, sí, adelante, acepto

🛵 **Sofía**

> ¡Perfecto, gracias Juan! ¿Tienes licencia de manejo vigente? Por ejemplo: sí / no.

👤 **Candidato**

> no, la verdad todavía no tengo licencia de manejo

🛵 **Sofía**

> Perdona, Juan, para confirmar: ¿tienes licencia de manejo vigente? Responde sí o no.

👤 **Candidato**

> no, no tengo

🛵 **Sofía**

> Gracias por contarme, Juan. Por ahora necesitamos repartidores con licencia de manejo vigente, así que no podemos avanzar. ¡Suerte!

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "disqualified",
  "idioma": "es",
  "datos": {
    "full_name": "Juan López",
    "consent": true,
    "has_license": false,
    "needs_review": []
  },
  "eliminatorias": {
    "passed": false,
    "failed_rule": "has_license",
    "reason_code": "no_license"
  },
  "confirmaciones_antes_de_descartar": [
    {
      "rule": "has_license",
      "reason_code": "no_license",
      "fields": {
        "has_license": false
      }
    }
  ]
}
```

**Resumen para el recruiter:**

```
Juan López · ciudad no informada (MX) · Descartado · Motivo: sin licencia de conducir vigente
Licencia: no tiene · Vehículo: no informado
Disponibilidad: no informado · Horario: no informado
Experiencia: no informado · Inicio: no informado · Permiso de trabajo: no informado

Observaciones:
- Candidato confirmó dos veces que no tiene licencia de manejo vigente, motivo de la descalificación. Conversación breve y directa, tono neutral/cordial sin preguntas abiertas ni dudas pendientes. No pidió hablar con una persona ni hubo cambios de idioma ni temas para revisar.
```

**Costo del LLM:** 8 llamadas · 15348+518 tokens · $0.0024 · **Latencia por turno:** 7.8, 1.5, 3.6 s

</details>

## 3. Cambio a inglés a mitad de la conversación · entrevista agendada

**Qué muestra:** Empieza en español y pasa a inglés: desde ahí todo sale en inglés, incluidos la respuesta de la FAQ ("¿puedo repartir en bici?"), el resumen y las plantillas del agendamiento · carnet B con 4 años habilita moto en España · reserva "the second one".

🛵 **Sofía**

> ¡Hola Mark! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> Hola, sí, acepto

🛵 **Sofía**

> Genial, Mark, gracias 😊 ¿Tienes carnet de conducir vigente? Por ejemplo: sí / no

👤 **Candidato**

> Sorry, can we continue in English? I have a B license since 4 years, I'd deliver with my own motorbike and I live in Barcelona

🛵 **Sofía**

> Perfect, thanks Mark — clear and useful. Do you have prior delivery or courier experience? For example: "2 years with Glovo" or "no experience".

👤 **Candidato**

> Can I deliver by bike instead?

🛵 **Sofía**

> No — for now deliveries are only by motorcycle or car, so a driving license that allows your vehicle is required. Do you have any delivery experience? For example: "2 years with Glovo" or "no experience"?

👤 **Candidato**

> Ok, motorbike then. I worked 1 year with Uber Eats, part time, evenings, I can start in two weeks and I have a work permit

🛵 **Sofía**

> Perfect, thanks Mark. Here’s what I have registered so far:
>
> - Name: Mark Evans
> - Consent to use data for this process: yes
> - Driving license: yes — type B, 4 years
> - Vehicle: motorcycle, own vehicle
> - City: Barcelona
> - Availability: part time
> - Preferred schedule: evening
> - Delivery experience: 1 year (Uber Eats)
> - Possible start date: 2026-11-11
> - Work permit: yes
>
> Is everything correct or do you want to change anything?

👤 **Candidato**

> yes, everything is correct

🛵 **Sofía**

> All set, Mark! 🎉 Your application is complete. The next step is a 30-minute interview by video call (we'll send you the link beforehand). Which of these times works for you? (Spain mainland time)
> 1) Friday, October 30, 10:00
> 2) Monday, November 2, 10:00
> 3) Tuesday, November 3, 10:00
> Reply with the number, or tell me if none of them work.

👤 **Candidato**

> the second one

🛵 **Sofía**

> You're booked, Mark! ✅ Your interview is on Monday, November 2, 10:00 (Spain mainland time), by video call (we'll send you the link beforehand). If you need to change it, just message us here.

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "interview",
  "idioma": "en",
  "datos": {
    "full_name": "Mark Evans",
    "consent": true,
    "has_license": true,
    "license_type": "B",
    "license_years": 4.0,
    "vehicle_type": "motorcycle",
    "owns_vehicle": true,
    "city": "Barcelona",
    "availability": "part_time",
    "preferred_schedule": "evening",
    "delivery_experience": {
      "years": 1.0,
      "platforms": [
        "Uber Eats"
      ]
    },
    "start_date": "2026-11-11",
    "work_permit": true,
    "needs_review": []
  },
  "eliminatorias": {
    "passed": true,
    "failed_rule": null,
    "reason_code": null
  },
  "puntaje": {
    "total": 68.3,
    "desglose": {
      "experience": 26.25,
      "availability": 15.0,
      "start_date": 12.0,
      "owns_vehicle": 10.0,
      "schedule_flexibility": 5.0
    }
  }
}
```

**Resumen para el recruiter:**

```
Mark Evans · Barcelona (ES) · Calificado · Puntaje 68.3/100
Licencia: B (4 años) · Vehículo: moto, propia
Disponibilidad: media jornada · Horario: noche
Experiencia: 1 año (Uber Eats) · Inicio: 2026-11-11 · Permiso de trabajo: sí

Observaciones:
- El candidato cambió el idioma a inglés al inicio de la conversación y continuó en ese idioma. Preguntó si podía entregar en bicicleta; se le indicó que solo moto o coche y aceptó usar moto. Conversación breve y cordial, confirmó toda la información y quedó pendiente que el recruiter le proponga fecha/hora para la entrevista (se ofrecieron tres opciones).
```

**Costo del LLM:** 17 llamadas · 38783+1209 tokens · $0.0062 · **Latencia por turno:** 7.7, 3.2, 4.0, 3.9, 4.0, 1.9 s

</details>

## 4. España · un dato eliminatorio mal dicho que se corrige al confirmar

**Qué muestra:** "No tengo carnet" no descarta: Sofía lo confirma, la candidata corrige ("me equivoqué: sí tengo") y la conversación sigue normal. Es lo que evita que una transcripción de voz mal entendida saque a alguien del proceso (se corta a mitad, a propósito).

🛵 **Sofía**

> ¡Hola Ana! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> sí, acepto

🛵 **Sofía**

> Perfecto, gracias Ana. ¿Tienes carnet de conducir vigente? Por ejemplo: sí / no.

👤 **Candidato**

> no tengo carnet

🛵 **Sofía**

> Perdona, Ana, para confirmar: ¿tienes carnet de conducir vigente? Responde sí o no.

👤 **Candidato**

> perdón, me equivoqué: sí tengo, el B desde hace 3 años

🛵 **Sofía**

> Genial, gracias Ana. ¿Repartirías en moto o en coche? Por ejemplo: moto / coche.

👤 **Candidato**

> en coche, es mío, y vivo en Valencia

🛵 **Sofía**

> Perfecto, gracias Ana. ¿Tienes experiencia repartiendo? Indica cuántos años y en qué plataformas o empresas (por ejemplo: "2 años en Glovo" o "sin experiencia").

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "in_screening",
  "idioma": "es",
  "datos": {
    "full_name": "Ana Ruiz",
    "consent": true,
    "has_license": true,
    "license_type": "B",
    "license_years": 3.0,
    "vehicle_type": "car",
    "owns_vehicle": true,
    "city": "Valencia",
    "needs_review": []
  },
  "eliminatorias": {
    "passed": true,
    "failed_rule": null,
    "reason_code": null
  },
  "confirmaciones_antes_de_descartar": [
    {
      "rule": "has_license",
      "reason_code": "no_license",
      "fields": {
        "has_license": false
      }
    }
  ]
}
```

**Costo del LLM:** 11 llamadas · 24968+666 tokens · $0.0035 · **Latencia por turno:** 7.9, 1.6, 3.6, 3.8 s

</details>

## 5. España · descartado por ciudad fuera de zona

**Qué muestra:** Regla `city_in_service_area`: Lugo no tiene local · se confirma la ciudad antes de descartar · descarte sin seguir pidiendo datos que ya no sirven.

🛵 **Sofía**

> ¡Hola Andrés! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> sí, claro, acepto

🛵 **Sofía**

> Perfecto, gracias Andrés. ¿Tienes carnet de conducir vigente? Por ejemplo: sí / no.

👤 **Candidato**

> sí tengo carnet B de hace 10 años, iría en coche, vivo en Lugo

🛵 **Sofía**

> Perdona, Andrés, para confirmar: ¿en qué ciudad vives o quieres repartir?

👤 **Candidato**

> sí, en Lugo

🛵 **Sofía**

> Gracias, Andrés. Por ahora no tenemos locales en tu ciudad, así que no podemos avanzar. ¡Ojalá pronto!

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "disqualified",
  "idioma": "es",
  "datos": {
    "full_name": "Andrés Vidal",
    "consent": true,
    "has_license": true,
    "license_type": "B",
    "license_years": 10.0,
    "vehicle_type": "car",
    "city": "Lugo",
    "needs_review": []
  },
  "eliminatorias": {
    "passed": false,
    "failed_rule": "city_in_service_area",
    "reason_code": "out_of_service_area"
  },
  "confirmaciones_antes_de_descartar": [
    {
      "rule": "city_in_service_area",
      "reason_code": "out_of_service_area",
      "fields": {
        "city": "Lugo"
      }
    }
  ]
}
```

**Resumen para el recruiter:**

```
Andrés Vidal · Lugo (ES) · Descartado · Motivo: ciudad fuera de la zona de servicio
Licencia: B (10 años) · Vehículo: auto
Disponibilidad: no informado · Horario: no informado
Experiencia: no informado · Inicio: no informado · Permiso de trabajo: no informado

Observaciones:
- El candidato confirmó tener carnet B y vehículo propio y reiteró que vive en Lugo, información ya registrada. Se le informó que no hay locales en su ciudad y la conversación terminó sin preguntas adicionales por parte del candidato. Tono neutral y cordial; no hubo pedidos de hablar con una persona ni campos pendientes para revisar.
```

**Costo del LLM:** 8 llamadas · 15423+528 tokens · $0.0026 · **Latencia por turno:** 2.6, 1.7, 4.2 s

</details>

## 6. México · opt-out

**Qué muestra:** "No me escriban más" se respeta: estado `opted_out` y ningún mensaje más (ni recordatorios).

🛵 **Sofía**

> ¡Hola Carlos! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> no gracias, ya conseguí trabajo, por favor no me escriban más

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "opted_out",
  "idioma": "es",
  "datos": {
    "full_name": "Carlos Ramírez",
    "needs_review": []
  }
}
```

**Costo del LLM:** 2 llamadas · 3741+140 tokens · $0.0006 · **Latencia por turno:** 1.6 s

</details>

## 7. España · candidato frustrado

**Qué muestra:** Extrae todos los datos de un mensaje en mayúsculas · ante "¿cuánto falta?" responde sin inventar un número y sigue (se corta a mitad, a propósito).

🛵 **Sofía**

> ¡Hola Pablo! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> vale acepto

🛵 **Sofía**

> Perfecto, gracias Pablo. Para seguir: ¿tienes carnet de conducir vigente? (Por ejemplo: sí / no)

👤 **Candidato**

> SÍ TENGO CARNET, YA LO DIJE EN EL FORMULARIO. Es el B, de hace 6 años, iría en mi moto, vivo en Valencia

🛵 **Sofía**

> Genial, Pablo. ¿Tienes experiencia repartiendo o en mensajería? Indica años y plataformas si aplican (por ejemplo: 2 años en Glovo) ¿sí o no y detalles?

👤 **Candidato**

> otra pregunta más?? cuánto falta, de verdad

🛵 **Sofía**

> Poco, quedan pocas preguntas y va rápido. ¿Tienes experiencia repartiendo o en mensajería? Indica años y plataformas si procede (por ejemplo: 2 años en Glovo).

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "in_screening",
  "idioma": "es",
  "datos": {
    "full_name": "Pablo Sanz",
    "consent": true,
    "has_license": true,
    "license_type": "B",
    "license_years": 6.0,
    "vehicle_type": "motorcycle",
    "city": "Valencia",
    "needs_review": []
  },
  "eliminatorias": {
    "passed": true,
    "failed_rule": null,
    "reason_code": null
  }
}
```

**Costo del LLM:** 9 llamadas · 21511+558 tokens · $0.0028 · **Latencia por turno:** 7.7, 3.0, 3.0 s

</details>

## 8. España · preguntas fuera del flujo y respuestas aproximadas

**Qué muestra:** "Tengo carnet" → pregunta el tipo · "¿cuál de los dos prefieres tú?" → responde con naturalidad, sin "lo consulto" · "¿me pagan la gasolina?" → FAQ (plus por km) · "algo de experiencia" → repregunta años y plataforma · "eeeh, cualquier día" → jornada completa (se corta a mitad, a propósito).

🛵 **Sofía**

> ¡Hola Laura! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> sí, acepto

🛵 **Sofía**

> Genial, gracias Laura. ¿Tienes carnet de conducir vigente? Por ejemplo: sí / no.

👤 **Candidato**

> tengo carnet

🛵 **Sofía**

> Perfecto, Laura. ¿Qué tipo de carnet tienes? Por ejemplo: AM, A1, A2 o B.

👤 **Candidato**

> el B, de hace 3 años

🛵 **Sofía**

> Perfecto, gracias Laura. ¿Repartirías en moto o en coche? Por ejemplo: moto / coche.

👤 **Candidato**

> ¿cuál de los dos prefieres tú?

🛵 **Sofía**

> La que te quede más cómoda; lo importante es que tu carnet la habilite. ¿Repartirías en moto o en coche? Por ejemplo: moto / coche.

👤 **Candidato**

> vale, en coche, es mío. ¿Me pagan la gasolina?

🛵 **Sofía**

> Sí: si usas tu coche propio hay un plus por kilómetro para cubrir combustible y desgaste. ¿En qué ciudad vives o quieres repartir? Por ejemplo: Madrid.

👤 **Candidato**

> vivo en Sevilla

🛵 **Sofía**

> Perfecto, Laura. ¿Tienes experiencia repartiendo o en mensajería? Di cuántos años y en qué plataformas o empresas, por ejemplo: "2 años en Glovo" o "sin experiencia".

👤 **Candidato**

> algo de experiencia tengo

🛵 **Sofía**

> Genial, gracias. ¿Cuántos años de experiencia y en qué plataformas o empresas? Por ejemplo: "1 año en Glovo" o "6 meses en restaurante X".

👤 **Candidato**

> unos 2 años en Glovo

🛵 **Sofía**

> Perfecto, gracias Laura. ¿Cuál es tu disponibilidad: jornada completa, media jornada o solo fines de semana? Por ejemplo: media jornada.

👤 **Candidato**

> eeeh, cualquier día

🛵 **Sofía**

> Perfecto, gracias Laura. ¿Desde qué fecha podrías empezar? Por ejemplo: "el lunes 2" o "en dos semanas".

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "in_screening",
  "idioma": "es",
  "datos": {
    "full_name": "Laura Méndez",
    "consent": true,
    "has_license": true,
    "license_type": "B",
    "license_years": 3.0,
    "vehicle_type": "car",
    "owns_vehicle": true,
    "city": "Sevilla",
    "availability": "full_time",
    "delivery_experience": {
      "years": 2.0,
      "platforms": [
        "Glovo"
      ]
    },
    "needs_review": []
  },
  "eliminatorias": {
    "passed": true,
    "failed_rule": null,
    "reason_code": null
  }
}
```

**Costo del LLM:** 27 llamadas · 65770+1631 tokens · $0.0094 · **Latencia por turno:** 3.7, 3.4, 3.0, 3.2, 2.9, 3.5, 3.8, 3.2, 2.8 s

</details>

## Lo que estas conversaciones dejan ver para mejorar

- **Latencia:** cada turno tarda ~3–4 s, y el primero ~8 s porque el proceso arranca en frío. Ver "Mejoras" en el README.
- **"¿Cuánto falta?" (7):** Sofía ya no inventa un número, pero tampoco dice cuántas preguntas quedan. El orquestador lo sabe: se puede pasar al turno de respuesta.
- **Tono ante la frustración (7):** el mensaje en mayúsculas se procesa bien, pero en esta corrida Sofía no reconoce la molestia ni ofrece pasar con una persona. Hay que revisar qué etiqueta puso el clasificador de emoción y sumar el caso a la suite `sentiment`.
