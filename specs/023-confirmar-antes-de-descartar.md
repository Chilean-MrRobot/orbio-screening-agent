# 023 — Confirmar antes de descartar

**Estado:** implementada
**Ola:** 8 · **Depende de:** 003, 008, 009, 022

## Objetivo
Que una respuesta mal entendida no descarte a un candidato. En una llamada, ElevenLabs
llama a nuestro LLM con la transcripción **provisional** mientras el candidato todavía habla.
En una prueba real, el candidato dijo "Eeeh, sí, sí tengo", pero el orquestador recibió
"Eh." y después "Eeeh, no.". Con eso lo descartó por no tener licencia, y el descarte no se
deshace. Por chat pasa lo mismo con un error de tipeo.

## Alcance
- Incluye:
  - Confirmación de la regla eliminatoria, en todos los canales.
  - Flag `confirm` por regla en el YAML.
  - En la llamada:
    - los turnos que son solo muletillas no se procesan;
    - el cierre no se repite cuando la conversación ya terminó.
- No incluye:
  - Esperar la transcripción final de ElevenLabs: su Custom LLM no avisa cuál es la final.

## Interfaz
- **`KnockoutRule.confirm_template: str | None`** (`config/rules/knockout.yaml`): la plantilla
  que vuelve a preguntar el dato antes de descartar, y que se valida con las demás
  plantillas al arrancar.
  - Plantillas: `confirm_license`, `confirm_license_vehicle` y `confirm_city`.
  - `consent_given` no tiene: un "no" a usar sus datos se respeta a la primera.
  - Es texto fijo y no lo redacta el LLM. En las evals, `gpt-5-mini` a veces saltaba a otra
    pregunta o no preguntaba nada, y la pregunta que decide un descarte es lógica de
    negocio.
- **Evento `knockout_confirmation_requested`** en el `timeline`, con:
  - `rule`;
  - `reason_code`;
  - `fields`: los campos con el valor entendido.
- **Plantilla `voice_listening`:** la respuesta a un turno de solo muletillas en la llamada.

## Comportamiento
1. **Se pide confirmación** si se cumplen todas estas condiciones:
   - una regla con `confirm_template` falla;
   - entre sus `requires` hay campos que cambiaron en este turno;
   - esa regla todavía no pidió confirmación.

   En ese caso:
   - Los campos que cambiaron vuelven a su valor anterior.
   - Se registra `knockout_confirmation_requested`.
   - Se envía la plantilla de la regla, que vuelve a preguntar de forma neutra ("Perdona,
     Lucía, para confirmar: ¿tienes carnet de conducir vigente? Responde sí o no."). No dice
     qué se entendió, que la respuesta descarta ni cuál es la correcta.
2. **Si la respuesta vuelve a fallar la misma regla**, se descarta como antes, con su plantilla.
3. **Si la respuesta pasa**, la conversación sigue normal. El evento queda en el `timeline`
   para el reclutador.
4. **Se descarta directo** si:
   - la regla no tiene `confirm_template`, o
   - los campos de la regla no cambiaron en este turno.
5. **Llamada:**
   - Un turno con solo muletillas ("eh", "mmm", "este…") no pasa por el orquestador; Sofía
     responde `voice_listening`.
   - Si la conversación ya estaba cerrada al llegar el pedido y existe `end_call`, la
     respuesta es solo `end_call`, sin repetir el mensaje de cierre. Antes salía varias veces:
     ElevenLabs volvía a pedir con la transcripción final.

## Criterios de aceptación
- **Dado** "no tengo carnet", **cuando** se extrae `has_license=false`, **entonces** no se
  descarta: `has_license` queda vacío, se registra el evento y se envía `confirm_license`
  (sin llamar al LLM de respuesta).
- **Dado** que ya se pidió confirmar, **cuando** vuelve a decir que no, **entonces** se descarta
  con `no_license`.
- **Dado** que ya se pidió confirmar, **cuando** dice que sí tiene, **entonces** sigue en
  `knockout`.
- **Dado** `consent=false`, **cuando** se extrae, **entonces** se descarta a la primera.
- **Dado** "Eh." en la llamada, **cuando** llega al endpoint, **entonces** no se llama al
  orquestador y la respuesta es `voice_listening`.
- **Dado** una conversación cerrada, **cuando** ElevenLabs vuelve a pedir con `end_call`,
  **entonces** la respuesta es solo `end_call`.

## Tests
- `tests/unit/conversation/test_orchestrator.py`:
  - confirmación y descarte tras confirmar, por cada regla;
  - confirmación que pasa;
  - regla sin `confirm_template`;
  - consentimiento negado.
- `tests/unit/test_voice_call.py`: muletillas y pedido con la conversación ya cerrada.
- `tests/unit/crm/test_demo.py`: los escenarios de descarte de la demo incluyen el turno de
  confirmación.

## Evaluación
- `extraction`: `ex-45-confirma-que-si-tiene`. Después de "Eeeh, no." y la pregunta de
  confirmación, "Eeeh, sí, sí tengo." es `has_license = true`.
- La pregunta de confirmación es una plantilla: no lleva caso de `reply`.
