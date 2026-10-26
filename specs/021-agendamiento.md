# 021 — Agendamiento de entrevistas

**Estado:** implementada
**Ola:** 5 (bonus del enunciado: *interview scheduling*) · **Depende de:** 000, 001, 008, 009, 011, 013, 015, 017

## Objetivo

Que el candidato calificado salga de la conversación con la entrevista agendada: al confirmar el resumen, Sofía le ofrece 3 horarios libres en su idioma y en su hora local, entiende cuál eligió y el código reserva el horario sin que dos candidatos queden en el mismo. El recruiter ve la entrevista en el panel y puede reagendarla o cancelarla.

## Alcance

- **Incluye:** `config/scheduling.yaml` validado al arrancar; generación determinista de horarios libres; reserva atómica de un horario por documento en Firestore; la etapa `scheduling` en el orquestador (ofrecer, elegir, "ninguno me sirve", horario recién ocupado, preguntas); el calentamiento existente para quien no responde; la entrevista en la tabla y en la ficha del panel, con reagendar y cancelar; casos de selección de horario en la suite `extraction`.
- **No incluye:** calendarios externos (Google Calendar, Calendly), varios entrevistadores o capacidad mayor a 1 por horario, recordatorio el día antes, enlaces de videollamada reales, avisar al candidato cuando el recruiter reagenda o cancela (lo hace el recruiter a mano por ahora), ni varias zonas horarias dentro de un país (México usa la de la región, `America/Mexico_City`). Ver §Escalar.

## Configuración (`config/scheduling.yaml`)

```yaml
scheduling:
  enabled: true                 # false: el flujo de la 008 (calificado → qualified_next_steps)
  slot_minutes: 30
  min_notice_hours: 24          # antelación mínima entre ahora y el horario
  horizon_days: 14              # hasta cuándo se buscan horarios (desde ahora + antelación)
  options_per_offer: 3
  max_rejections: 2             # "ninguno me sirve" por segunda vez → agendar a mano
  countries:
    ES:
      days: [1, 2, 3, 4, 5]     # 1=lunes … 7=domingo, en la zona horaria de la región
      windows:
        - {start: "10:00", end: "13:00"}
        - {start: "16:00", end: "18:00"}
      modality: video           # video | in_person
      timezone_label: {es: hora peninsular, en: Spain mainland time}
    MX:
      days: [1, 2, 3, 4, 5]
      windows: [{start: "10:00", end: "14:00"}]
      modality: in_person
      timezone_label: {es: hora del centro de México, en: Mexico City time}
      locations:                # una dirección por ciudad de servicio de la región
        Ciudad de México: ...
  reengagement_templates:       # plantilla de la cadencia → la que se usa esperando la elección
    reminder_abandoned: interview_reminder
    reminder_abandoned_sms: interview_reminder_sms
    closing_no_response: interview_no_response
```

La zona horaria es la de `regions/<país>.yaml` (`timezone`), no se repite. Validaciones (Pydantic, al arrancar): ventanas `HH:MM` con `start < end` y al menos un horario completo; días 1–7; los países existen en `regions`; `in_person` exige `locations` para **todas** las ciudades de la región; las plantillas de `reengagement_templates` y las del agendamiento existen en `templates.yaml` (en es y en). Si falta el archivo, el agendamiento queda desactivado (retrocompatible).

Plantillas nuevas en `templates.yaml` (variables extra: `{options}`, `{interview_time}`, `{place}`, `{duration}`, `{timezone_label}`, `{city}`, `{address}`): `interview_offer`, `interview_offer_more`, `interview_offer_retry`, `interview_slot_taken`, `interview_confirmed`, `interview_manual`, `interview_place_video`, `interview_place_in_person`, `interview_reminder`, `interview_reminder_sms`, `interview_no_response`.

## Cambios de contrato (mínimos y retrocompatibles)

```python
# domain/models.py
class Stage(StrEnum):
    ...
    SCHEDULING = "scheduling"  # entre closing y done


class InterviewModality(StrEnum):
    VIDEO = "video"
    IN_PERSON = "in_person"


class InterviewSlot(BaseModel):
    start: datetime  # UTC
    end: datetime


class Interview(BaseModel):
    slot_id: str  # documento de la reserva
    start: datetime
    end: datetime
    timezone: str  # para mostrar la hora local
    modality: InterviewModality
    location: str | None = None  # dirección si es presencial
    booked_at: datetime
    booked_by: str  # "agent" o el nombre del recruiter


class SchedulingState(BaseModel):
    offered: list[InterviewSlot] = []  # la oferta vigente, en el orden mostrado
    declined: list[InterviewSlot] = []  # ofertas rechazadas: no se vuelven a ofrecer
    rejections: int = 0
    manual_required: bool = False  # el recruiter debe agendar a mano
    # declined_options | no_slots | no_response | human_requested | cancelled
    manual_reason: str | None = None


class Candidate:
    ...
    interview: Interview | None = None
    scheduling: SchedulingState = SchedulingState()


# ports.py
class ExtractionOutcome:
    ...
    slot_choice: int | None = None
    slots_rejected: bool = False


class ReplyRequest:
    ...
    interview_options: list[str] = []


class InterviewSlotStore(Protocol):
    def try_reserve(
        self, slot_id: str, candidate_id: str, *, start: datetime, now: datetime
    ) -> bool: ...
    def release(self, slot_id: str, candidate_id: str) -> None: ...
    def reserved_between(self, scope: str, start: datetime, end: datetime) -> set[str]: ...


# config/models.py
class AppConfig:
    ...
    scheduling: SchedulingConfig | None = None
```

Todos los campos nuevos tienen default: las fichas guardadas antes en Firestore se leen igual.

## Interfaz

```python
# screening.scheduling
def generate_slots(country_config, timezone, *, slot_minutes, start, end) -> list[InterviewSlot]
def pick_options(free: list[InterviewSlot], count: int, timezone: str) -> list[InterviewSlot]
def slot_id(scope: str, start: datetime) -> str            # "ES_20261027T0900Z"
def format_slot(slot, timezone, language) -> str          # "martes 27 de octubre, 10:00"
def awaiting_choice(candidate) -> bool                    # qualified + etapa scheduling
def mark_manual(candidate, reason, now) -> None

class InterviewScheduler:
    def __init__(self, config: AppConfig, store: InterviewSlotStore, clock: Clock): ...
    def enabled_for(self, candidate) -> bool
    def free_slots(self, country, *, exclude=(), limit=None) -> list[InterviewSlot]
    def options(self, candidate) -> list[InterviewSlot]    # 3 repartidas en días distintos
    def book(self, candidate, slot, *, booked_by) -> Interview | None   # None: ocupado o ya no válido
    def cancel(self, candidate) -> Interview | None

# adapters
InMemoryInterviewSlotStore, FirestoreInterviewSlotStore(client, collection="interview_slots")

# API del panel (con PANEL_PASSWORD, solo si hay scheduler)
GET  /crm/candidates/{id}/slots?limit=20 → list[InterviewSlot]
POST /crm/candidates/{id}/interview  {action: "book"|"cancel", start?: datetime, recruiter?: str}
```

## Comportamiento

### Horarios libres

1. Se generan los horarios de cada ventana del país, cada `slot_minutes`, entre `ahora + min_notice_hours` y ese instante + `horizon_days`, en la hora local de la región (con cambio de horario: `zoneinfo`). Un horario cabe si `inicio + slot_minutes <= fin de la ventana`. Solo en los `days` configurados.
2. Se restan los ocupados: `reserved_between(país, desde, hasta)` devuelve los `slot_id` reservados (una consulta por rango sobre el ID, sin recorrer fichas ni índice compuesto).
3. **Las 3 opciones** se reparten: el primer horario libre de cada día distinto, en orden; si hay menos de 3 días, se completa con los siguientes libres. En la segunda oferta se excluyen los horarios ya rechazados (`declined`).

### Reserva atómica

Un documento por horario: `interview_slots/{slot_id}` con `candidate_id`, `slot_id`, `start` y `reserved_at`. `try_reserve` usa `create()` (falla si existe): dos instancias que reservan el mismo horario a la vez → solo una gana. Si el documento ya es del mismo candidato, devuelve `True` (reintento idempotente). `release` borra el documento en una transacción solo si es del candidato. Al reagendar se reserva el nuevo **antes** de liberar el anterior. Si el proceso cae entre reservar y guardar la ficha, queda una reserva huérfana con su `candidate_id` (se ve en Firestore y se libera a mano; ver §Escalar).

Antes de reservar, `book` revalida que el horario siga siendo válido (no pasó la antelación mínima y está en las ventanas): si el candidato responde días después, un horario vencido cuenta como ocupado.

### Conversación (orquestador)

- **Al confirmar el resumen:** se calcula el puntaje y la ficha queda `qualified` como en la 008. Si el agendamiento está activo para el país del candidato y hay horarios libres, la etapa pasa a `scheduling`, se guardan las 3 opciones en `scheduling.offered` y se envía `interview_offer` con la lista numerada en su idioma y hora local. Si no hay horarios libres: `qualified_next_steps`, etapa `done` y `manual_required` (`no_slots`). Desactivado o país sin configuración: el flujo de la 008 tal cual.
- **Mensajes en `scheduling`** (`qualified` + etapa `scheduling`): la extracción recibe `expected_fields = ["interview_slot"]` y la lista de opciones ofrecidas; devuelve `slot_choice` (1..3) o `slots_rejected`. Luego, en código:
  - **Eligió** una opción válida → `book`. Si reserva: estado `interview`, etapa `done`, `candidate.interview` lleno y plantilla `interview_confirmed` (fecha y hora local, modalidad, dirección si es presencial). Si estaba ocupado: `interview_slot_taken` con 3 opciones nuevas (no cuenta como rechazo).
  - **"Ninguno me sirve"** → la oferta pasa a `declined`, `rejections + 1`. Si `rejections < max_rejections`, `interview_offer_more` con 3 nuevas. Si no, `interview_manual`, etapa `done`, `manual_required` (`declined_options`); la ficha sigue `qualified` para que el recruiter agende.
  - **Pregunta** sin elegir → Sofía responde con la FAQ (turno de respuesta, etapa `scheduling`) y recuerda las opciones (`interview_options`).
  - **No se entiende** → `interview_offer_retry` con las mismas opciones.
  - **Opt-out** → `opted_out` como siempre. **Pide un humano** → `human_handoff`, etapa `done`, `manual_required` (`human_requested`).
  - Los datos de la ficha no se tocan en esta etapa.
- **Silencio:** aplica la cadencia `abandoned_mid_screening` existente (mismos tiempos y horario hábil), con las plantillas de `reengagement_templates`. El último paso (`closes`) no deja la ficha en `no_response`: la deja `qualified`, etapa `done` y `manual_required` (`no_response`).
- Un mensaje después de agendar (`interview`) queda como `message_after_close`, como hoy.

### Panel

- **Tabla:** columna *Entrevista*: "mar 27/10 10:00 · Videollamada" (hora local del país), "📅 Agendar a mano" si `manual_required`, o vacío.
- **Ficha:** bloque con fecha, hora local y zona, modalidad y dirección; o el aviso de agendar a mano con el motivo. Acciones: **Agendar / Reagendar** (elige entre los próximos horarios libres) y **Cancelar entrevista**.
  - Agendar o reagendar: permitido en `qualified` e `interview`; deja `interview`, etapa `done`, limpia `manual_required` y registra `interview_booked` o `interview_rescheduled` con el recruiter. Horario ocupado → 409.
  - Cancelar: libera el horario, la ficha vuelve a `qualified` con `manual_required` (`cancelled`) y se registra `interview_cancelled`.
- Eventos nuevos del timeline: `interview_offered`, `interview_booked`, `interview_rescheduled`, `interview_slot_taken`, `interview_declined`, `interview_manual_required`, `interview_cancelled`.

### Escalar

- La reserva ya es multi-instancia (un documento por horario, `create` atómico). Para capacidad > 1 por horario (varios entrevistadores) el documento pasaría a ser `slot_id + entrevistador` o un contador en transacción.
- Calendario real: `InterviewSlotStore` es el puerto; un adaptador de Google Calendar leería los ocupados y crearía el evento.
- El panel guarda la ficha sin el lease de la 015, igual que el cambio de estado de la 013: si el recruiter reagenda justo mientras el agente procesa un mensaje del mismo candidato, gana el último en guardar (el horario sí queda bien reservado). Siguiente paso: tomar el lease en las rutas del panel.
- Reservas huérfanas: un job diario que compare `interview_slots` con las fichas (por `candidate_id`) y libere las que no correspondan.
- Recordatorio el día antes y aviso al reagendar: una tarea de Cloud Tasks programada al reservar.

## Criterios de aceptación

- **Dado** un horario hábil ES de 10–13 y 16–18 h, **cuando** se generan los horarios, **entonces** son cada 30 min en hora de Madrid, ninguno antes de ahora + 24 h ni fuera de lunes a viernes, y el último de cada ventana termina a la hora de cierre.
- **Dado** MX y un cambio de horario en ES, **cuando** se generan, **entonces** los instantes UTC respetan la hora local de cada país.
- **Dado** horarios reservados, **cuando** se calculan los libres, **entonces** no aparecen; **cuando** se eligen las 3 opciones, **entonces** están en días distintos.
- **Dado** dos reservas del mismo horario por candidatos distintos, **cuando** compiten, **entonces** solo una devuelve `True`; la del mismo candidato es idempotente; liberar solo libera la propia.
- **Dado** un candidato que confirma el resumen con el agendamiento activo, **cuando** termina el turno, **entonces** queda `qualified`, etapa `scheduling`, con 3 opciones guardadas y el mensaje `interview_offer` las lista en su idioma y hora local.
- **Dado** el candidato en `scheduling`, **cuando** elige "el segundo", **entonces** queda `interview`, etapa `done`, con la entrevista en la ficha, el horario reservado y `interview_confirmed` enviado.
- **Dado** que el horario elegido lo tomó otro, **cuando** elige, **entonces** recibe 3 opciones nuevas y sigue en `scheduling`.
- **Dado** el candidato en `scheduling`, **cuando** dice "ninguno me sirve" dos veces, **entonces** la primera recibe 3 opciones distintas y la segunda queda `qualified`, etapa `done`, `manual_required` y `interview_manual` enviado.
- **Dado** `enabled: false`, **cuando** confirma el resumen, **entonces** el flujo es el de la 008 (`qualified_next_steps`, etapa `done`).
- **Dado** el candidato en `scheduling` en silencio, **cuando** pasa la cadencia, **entonces** recibe los recordatorios del agendamiento y al cerrar queda `qualified` con `manual_required`, no `no_response`.
- **Dado** una ficha con entrevista, **cuando** el recruiter la reagenda o cancela desde el panel, **entonces** el horario anterior queda libre, la ficha cambia de estado como se describe y el timeline lo registra.
- **Dado** un `scheduling.yaml` con `in_person` sin dirección para una ciudad, o una plantilla que no existe, **cuando** arranca, **entonces** la configuración no valida.

## Tests

- `tests/unit/scheduling/test_slots.py`: generación (ventanas, días, antelación, horizonte, ES y MX, cambio de horario), libres sin los ocupados, reparto en días distintos, formato es/en.
- `tests/unit/scheduling/test_store.py`: contrato de `InterviewSlotStore` sobre memoria y el fake de Firestore (reserva con conflicto, idempotencia, liberar solo la propia, rango por país).
- `tests/unit/scheduling/test_scheduler.py`: `options`, `book` (conflicto, horario vencido, reagendar libera el anterior), `cancel`.
- `tests/unit/test_config.py`: validaciones del YAML nuevo.
- `tests/unit/conversation/test_scheduling.py`: calificado → oferta → elige → `interview`; horario ocupado; "ninguno me sirve" dos veces; pregunta; no se entiende; humano; sin horarios; desactivado.
- `tests/unit/reengagement/`: la cadencia en `scheduling` usa las plantillas del agendamiento y cierra en `manual_required`.
- `tests/unit/agent/`: `slot_choice` y `slots_rejected` en `build_outcome` y las opciones en el contexto de la extracción y de la respuesta.
- `tests/unit/crm/`: columna y bloque de entrevista en las vistas, API de horarios, agendar, reagendar, cancelar y conflicto.

## Evaluación

Suite `extraction` (`evals/datasets/extraction.jsonl`), casos `ex-sch-*` con `offered_slots` en la entrada: "el segundo", "el martes a las 10", "the third one works", "ninguno me sirve", "el de las 4 de la tarde", una pregunta sin elegir y una respuesta ambigua (dos opciones de mañana). La respuesta esperada fija `slot_choice` y `slots_rejected`. Umbral de la suite: 0,85 (el de la 009).
