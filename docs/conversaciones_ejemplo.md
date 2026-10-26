# Conversaciones de ejemplo

Conversaciones **reales** con el agente (Google ADK + OpenAI `gpt-5-mini` / `gpt-5-nano`), generadas con `scripts/chat_console.py`, que usa el mismo orquestador, reglas, puntaje y resumen que el demo desplegado. Las respuestas de Sofía y las fichas están **sin editar**; los mensajes del candidato son guiones de prueba. Los teléfonos son de prueba.

| # | Escenario | Resultado |
|---|---|---|
| 1 | España · calificada, con una pregunta a la FAQ en medio | `qualified` |
| 2 | México · descartado por no tener licencia | `disqualified` |
| 3 | Cambio a inglés · calificado | `qualified` |
| 4 | España · descartada por ciudad fuera de zona | `disqualified` |
| 5 | México · opt-out | `opted_out` |
| 6 | España · candidato frustrado | `in_screening` (se corta a mitad, a propósito) |
| 7 | España · respuestas ambiguas | `in_screening` (se corta a mitad, a propósito) |

## 1. España · calificada, con una pregunta a la FAQ en medio

**Qué muestra:** Extracción de varios datos en un mensaje y normalización ("carnet B desde hace 5 años" → `license_type: B`, `license_years: 5`) · RAG: responde el pago y vuelve a la pregunta pendiente · resumen para confirmar · puntaje 100 y resumen para el recruiter.

🛵 **Sofía**

> ¡Hola Lucía! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> hola! sí, tengo tiempo y acepto el tratamiento de mis datos

🛵 **Sofía**

> ¡Perfecto, gracias Lucía! ¿Tienes carnet de conducir vigente? (Por ejemplo: sí / no)

👤 **Candidato**

> tengo el carnet B desde hace 5 años, repartiría en mi coche y vivo en Madrid

🛵 **Sofía**

> Genial, gracias Lucía. ¿Tienes experiencia en reparto o mensajería? Di cuántos años y en qué plataformas, por ejemplo: "2 años en Glovo".

👤 **Candidato**

> una pregunta, ¿cuánto se paga por hora?

🛵 **Sofía**

> En España el pago es de 9 a 12 € brutos por hora según la ciudad y la franja (noche y fines de semana suelen pagar más). ¿Cuántos años y en qué plataformas tienes de experiencia en reparto? (Por ejemplo: "2 años en Glovo")

👤 **Candidato**

> llevo 2 años en Glovo, busco jornada completa con horario flexible, puedo empezar el lunes y tengo permiso de trabajo

🛵 **Sofía**

> Perfecto, Lucía — resumo lo registrado:
> - Nombre: Lucía Pérez  
> - Consentimiento para usar tus datos: sí  
> - Carnet de conducir vigente: sí, tipo B (5 años)  
> - Medio de reparto: coche propio  
> - Ciudad: Madrid  
> - Disponibilidad: jornada completa, horario flexible  
> - Experiencia en reparto: 2 años en Glovo  
> - Fecha de inicio disponible: lunes 2 de noviembre de 2026  
> - Permiso para trabajar en España: sí
> ¿Todo correcto o quieres corregir algo?

👤 **Candidato**

> sí, todo correcto

🛵 **Sofía**

> ¡Listo, Lucía! 🎉 Tu postulación quedó completa. Un reclutador de Grupo Sazón te contactará en las próximas 48 h.

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "qualified",
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
  "eliminatorias": "{'passed': True, 'failed_rule': None, 'reason_code': None}",
  "puntaje": "100.0 {'experience': 35.0, 'availability': 25.0, 'start_date': 20.0, 'owns_vehicle': 10.0, 'schedule_flexibility': 10.0}"
}
```

**Resumen para el recruiter:**

```
Lucía Pérez · Madrid (ES) · Calificado · Puntaje 100/100
Licencia: B (5 años) · Vehículo: auto, propia
Disponibilidad: jornada completa · Horario: flexible
Experiencia: 2 años (Glovo) · Inicio: 2026-11-02 · Permiso de trabajo: sí
Observaciones:
- El candidato preguntó «¿cuánto se paga por hora?» al inicio; ya recibió la respuesta del asistente (9–12 € brutos/hora) y no insistió.
- Conversación cordial y neutral; confirmó toda la información sin cambios y aceptó el tratamiento de datos.
```

**Costo del LLM:** 16 llamadas · 25123+2451 tokens · $0.0081

</details>

## 2. México · descartado por no tener licencia

**Qué muestra:** Vocabulario de México ("licencia de manejo") · regla eliminatoria evaluada apenas llega el dato · cierre amable con el motivo · resumen con el motivo del descarte.

🛵 **Sofía**

> ¡Hola Juan! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> qué onda, sí, adelante, acepto

🛵 **Sofía**

> Perfecto, gracias Juan. ¿Tienes licencia de manejo vigente? Por ejemplo: sí / no.

👤 **Candidato**

> no, la verdad todavía no tengo licencia de manejo

🛵 **Sofía**

> Gracias por contarme, Juan. Por ahora necesitamos repartidores con licencia de manejo vigente, así que no podemos avanzar. ¡Suerte!

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "disqualified",
  "datos": {
    "full_name": "Juan López",
    "consent": true,
    "has_license": false,
    "needs_review": []
  },
  "eliminatorias": "{'passed': False, 'failed_rule': 'has_license', 'reason_code': 'no_license'}"
}
```

**Resumen para el recruiter:**

```
Juan López · ciudad no informada (MX) · Descartado · Motivo: sin licencia de conducir vigente
Licencia: no tiene · Vehículo: no informado
Disponibilidad: no informado · Horario: no informado
Experiencia: no informado · Inicio: no informado · Permiso de trabajo: no informado
Observaciones:
- El candidato confirmó claramente que no tiene licencia de manejo vigente, motivo de la descalificación.
- Conversación breve y directa, tono neutral/informal; no hubo preguntas adicionales ni temas pendientes.
```

**Costo del LLM:** 6 llamadas · 8068+848 tokens · $0.0026

</details>

## 3. Cambio a inglés · calificado

**Qué muestra:** Cambio de idioma desde el primer mensaje: Sofía sigue en inglés y la plantilla final sale en inglés · carnet B con 4 años habilita moto (regla `min_years` por país) · puntaje con desglose · el resumen para el recruiter queda en español.

🛵 **Sofía**

> ¡Hola Ahmed! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> Hi! Sorry, I don't speak Spanish very well. Yes, I accept

🛵 **Sofía**

> Perfect, thanks Ahmed. Do you have a valid driver's license (for example: yes / no)?

👤 **Candidato**

> I have a B license since 4 years, I would deliver with my own motorbike and I live in Barcelona

🛵 **Sofía**

> Great, thanks Ahmed. Do you have delivery experience — if yes, how many years and on which platforms? (For example: "2 years on Glovo")?

👤 **Candidato**

> I worked 1 year with Uber Eats. I can do part time, evenings, and start in two weeks. I have a work permit

🛵 **Sofía**

> Perfect, thanks Ahmed — here's what I have so far:
> - Full name: Ahmed Benali
> - Consent to use your data for this process: yes
> - Driver's license: yes, B (4 years)
> - Vehicle: motorcycle (own)
> - City: Barcelona
> - Availability: part time
> - Preferred schedule: evening
> - Delivery experience: 1 year on Uber Eats
> - Start date: 2026-11-09
> - Work permit: yes
> Is everything correct or do you want to change anything?

👤 **Candidato**

> yes, everything is correct

🛵 **Sofía**

> All set, Ahmed! 🎉 Your application is complete. A Grupo Sazón recruiter will contact you within 48 hours.

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "qualified",
  "datos": {
    "full_name": "Ahmed Benali",
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
    "start_date": "2026-11-09",
    "work_permit": true,
    "needs_review": []
  },
  "eliminatorias": "{'passed': True, 'failed_rule': None, 'reason_code': None}",
  "puntaje": "68.3 {'experience': 26.25, 'availability': 15.0, 'start_date': 12.0, 'owns_vehicle': 10.0, 'schedule_flexibility': 5.0}"
}
```

**Resumen para el recruiter:**

```
Ahmed Benali · Barcelona (ES) · Calificado · Puntaje 68.3/100
Licencia: B (4 años) · Vehículo: moto, propia
Disponibilidad: media jornada · Horario: noche
Experiencia: 1 año (Uber Eats) · Inicio: 2026-11-09 · Permiso de trabajo: sí
Observaciones:
- El candidato cambió el idioma a inglés al inicio y respondió en ese idioma.
- Confirmó experiencia de 1 año en Uber Eats, disponibilidad media jornada por las noches y fecha de inicio en dos semanas (coincide con el start_date).
- La conversación fue breve y cordial, sin dudas abiertas ni pedidos de hablar con una persona.
```

**Costo del LLM:** 12 llamadas · 18150+1836 tokens · $0.0061

</details>

## 4. España · descartada por ciudad fuera de zona

**Qué muestra:** Regla `city_in_service_area`: Lugo no tiene local · descarte inmediato, sin seguir preguntando datos que ya no sirven.

🛵 **Sofía**

> ¡Hola Marta! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> sí, claro, acepto

🛵 **Sofía**

> Genial, Marta. ¿Tienes carnet de conducir vigente? Por ejemplo: sí / no.

👤 **Candidato**

> sí tengo carnet B de hace 10 años, iría en coche, vivo en Lugo

🛵 **Sofía**

> Gracias, Marta. Por ahora no tenemos locales en tu ciudad, así que no podemos avanzar. ¡Ojalá pronto!

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "disqualified",
  "datos": {
    "full_name": "Marta Gil",
    "consent": true,
    "has_license": true,
    "license_type": "B",
    "license_years": 10.0,
    "vehicle_type": "car",
    "city": "Lugo",
    "needs_review": []
  },
  "eliminatorias": "{'passed': False, 'failed_rule': 'city_in_service_area', 'reason_code': 'out_of_service_area'}"
}
```

**Resumen para el recruiter:**

```
Marta Gil · Lugo (ES) · Descartado · Motivo: ciudad fuera de la zona de servicio
Licencia: B (10 años) · Vehículo: auto
Disponibilidad: no informado · Horario: no informado
Experiencia: no informado · Inicio: no informado · Permiso de trabajo: no informado
Observaciones:
- Candidato confirmó tener carnet B desde hace 10 años y que usaría coche, información ya en la ficha.
- Mencionó vivir en Lugo; la conversación fue breve y cordial y no surgieron preguntas ni dudas por parte del candidato.
```

**Costo del LLM:** 7 llamadas · 9686+888 tokens · $0.0028

</details>

## 5. México · opt-out

**Qué muestra:** "No me escriban más" se respeta: estado `opted_out` y ningún mensaje más (ni recordatorios).

🛵 **Sofía**

> ¡Hola Carla! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> no gracias, ya conseguí trabajo, por favor no me escriban más

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "opted_out",
  "datos": {
    "full_name": "Carla Ruiz",
    "needs_review": []
  }
}
```

**Costo del LLM:** 2 llamadas · 2846+256 tokens · $0.0007

</details>

## 6. España · candidato frustrado

**Qué muestra:** Clasificador de emoción → tono adaptado: reconoce la molestia, pide disculpas, ofrece pasar con una persona y sigue con mensajes cortos · extrae todos los datos de un mensaje en mayúsculas.

🛵 **Sofía**

> ¡Hola Pablo! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> vale acepto

🛵 **Sofía**

> Genial, Pablo. ¿Tienes carnet de conducir vigente? Por ejemplo: sí / no.

👤 **Candidato**

> SÍ TENGO CARNET, YA LO DIJE EN EL FORMULARIO. Es el B, de hace 6 años, iría en mi moto, vivo en Valencia

🛵 **Sofía**

> Perfecto, gracias por decirlo, Pablo. Siento las molestias; si prefieres hablar con una persona del equipo, te pasan en las próximas 48 h.  
> ¿Tienes experiencia en reparto o mensajería? Por ejemplo: "2 años en Deliveroo" o "no, ninguno".

👤 **Candidato**

> otra pregunta más?? cuánto falta, de verdad

🛵 **Sofía**

> Entiendo, lo siento por la espera; si quieres hablar con una persona del equipo te contactarán en las próximas 48 h.  
> ¿Tienes experiencia en reparto o mensajería? Por ejemplo: "2 años en Glovo" o "no, ninguno".

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "in_screening",
  "datos": {
    "full_name": "Pablo Sanz",
    "consent": true,
    "has_license": true,
    "license_type": "B",
    "license_years": 6.0,
    "vehicle_type": "motorcycle",
    "owns_vehicle": true,
    "city": "Valencia",
    "needs_review": []
  },
  "eliminatorias": "{'passed': True, 'failed_rule': None, 'reason_code': None}"
}
```

**Costo del LLM:** 9 llamadas · 13806+1331 tokens · $0.0046

</details>

## 7. España · respuestas ambiguas

**Qué muestra:** "Tengo carnet" → repregunta el tipo con ejemplos · "algo de experiencia" → repregunta años y plataforma · no inventa datos que el candidato no dio.

🛵 **Sofía**

> ¡Hola Laura! Soy Sofía, asistente virtual de Grupo Sazón 👋 Vi que postulaste como repartidor. ¿Tienes 3 minutos para unas preguntas rápidas?

👤 **Candidato**

> sí, acepto

🛵 **Sofía**

> Genial, gracias Laura. ¿Tienes carnet de conducir vigente? (Por ejemplo: sí / no)

👤 **Candidato**

> tengo carnet

🛵 **Sofía**

> Perfecto, ¿qué tipo de carnet tienes? Por ejemplo: B, A2 o AM.

👤 **Candidato**

> el B, de hace 3 años, iría en coche, vivo en Sevilla

🛵 **Sofía**

> Perfecto, ¿tienes experiencia repartiendo? Dinos cuántos años y en qué plataformas, por ejemplo: "2 años en Glovo".

👤 **Candidato**

> algo de experiencia tengo

🛵 **Sofía**

> Perfecto, ¿cuántos años exactamente y en qué plataformas? Por ejemplo: "1 año en Glovo" o "6 meses en reparto propio".

<details><summary>Ficha resultante</summary>

```json
{
  "estado": "in_screening",
  "datos": {
    "full_name": "Laura Méndez",
    "consent": true,
    "has_license": true,
    "license_type": "B",
    "license_years": 3.0,
    "vehicle_type": "car",
    "city": "Sevilla",
    "needs_review": []
  },
  "eliminatorias": "{'passed': True, 'failed_rule': None, 'reason_code': None}"
}
```

**Costo del LLM:** 12 llamadas · 18140+1554 tokens · $0.0060

</details>

## Lo que estas conversaciones dejan ver para mejorar

- **Candidato frustrado (6):** pregunta "¿cuánto falta?" y Sofía se disculpa pero no responde cuántas preguntas quedan. El orquestador sabe exactamente qué falta: se puede pasar al turno de respuesta para que lo diga ("son 3 preguntas más").
- **Latencia:** cada turno tarda 6–12 s (extracción + respuesta con `gpt-5-mini`). Ver "Mejoras" en el README.
