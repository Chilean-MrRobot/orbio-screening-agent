# 017 — Calentamiento por índice

**Estado:** implementada
**Ola:** 4 (escalabilidad) · **Depende de:** 000, 001, 008, 011

## Objetivo

Que la pasada de calentamiento (spec 011) lea solo las fichas con un toque vencido, por índice y paginando, en vez de cargar todas las fichas en cada llamada de Cloud Scheduler. Meta de referencia: 10K postulaciones por semana sin que el costo de una pasada crezca con el total de fichas.

## Alcance

- **Incluye:** la función pura `schedule_next_touch`, su llamada en cada punto donde cambia el próximo toque (orquestador y pasada), `run_due_touches` con `CandidateRepository.query` paginada y un tope por pasada, y el backfill de `next_touch_at` para las fichas antiguas (`backfill_next_touch` y `scripts/backfill_next_touch.py`).
- **No incluye:** transacciones entre el orquestador y la pasada (la 015 agrega el lease por candidato; ver §Concurrencia), repartir la pasada en Cloud Tasks (ver §Escalar) ni cambios en los contratos de la ola 0: se usan `CandidateQuery(touch_due_before=..., order="next_touch_asc", cursor=...)` y el índice `idx_next_touch_at` que ya existen.

## Interfaz

```python
# screening.reengagement
def schedule_next_touch(candidate: Candidate, config: AppConfig) -> datetime | None:
    """Cuándo vence el próximo toque, o None si no hay cadencia que aplique. Pura."""


def refresh_next_touch(candidate: Candidate, config: AppConfig) -> None:
    """candidate.reengagement.next_touch_at = schedule_next_touch(candidate, config)."""


def run_due_touches(
    *,
    repository: CandidateRepository,
    channels: dict[ChannelName, MessageChannel],
    clock: Clock,
    config: AppConfig,
    page_size: int = 100,  # fichas por consulta (1..500)
    max_per_pass: int = 1000,  # tope de fichas leídas por pasada
) -> ReengagementReport: ...


def backfill_next_touch(
    repository: CandidateRepository, config: AppConfig, *, page_size: int = 200
) -> BackfillReport: ...


class ReengagementReport(BaseModel):  # spec 011 + dos campos
    read: int  # fichas devueltas por la consulta de vencidas
    capped: bool  # la pasada cortó por el tope: quedan vencidas para la próxima
    evaluated: int
    touches: list[TouchRecord]
    closed: list[str]
    errors: list[str]


class BackfillReport(BaseModel):
    read: int  # fichas abiertas revisadas
    updated: int  # fichas cuyo next_touch_at cambió
```

`ReengagementService(..., page_size=..., max_per_pass=...)` pasa los dos parámetros. En la API, `max_per_pass` sale de la variable `REENGAGEMENT_MAX_PER_PASS` (por defecto 1000). Son parámetros operativos, no de negocio: por eso no van en `cadences.yaml`.

## Comportamiento

### El próximo toque

`schedule_next_touch` decide igual que la pasada de la spec 011, sin efectos ni reloj:

1. **Qué cadencia aplica** (tabla de la spec 011): ninguna → `None`. También `None` si el país no tiene `business_hours`.
2. **Ancla y paso:** si la cadencia guardada en `reengagement` es la que aplica y sigue activa (su `reengagement_started` es posterior al último mensaje del candidato), se usan su ancla y `next_step_index`. Si no, el ancla es el inicio del silencio y el paso es el primero cuya plantilla no salió desde el ancla (el `first_contact` que envía el orquestador no se repite).
3. **Sin pasos pendientes** → `None`.
4. **Hora:** ancla + `after_hours` del paso. Con `queue_outside_hours: true`, si cae fuera del horario hábil del país se mueve a la próxima apertura (`next_opening`, hora local, con cambio de horario). Con `false` queda la hora nominal: la pasada lo encuentra y lo salta (`outside_business_hours`), como en la 011.

Como la hora ya viene ajustada, la consulta no devuelve una y otra vez las fichas que esperan el horario hábil. El evento `reengagement_touch_queued` queda solo para cuando la pasada llega tarde (el paso venció en horario, pero la pasada corrió después del cierre).

### Dónde se recalcula

| Momento | Quién | Resultado |
|---|---|---|
| Primer contacto (enviado o fallido) | orquestador, `_send` | `contacted`: +24 h; `new` con el envío fallido: el `first_contact` ya (lo reintenta la pasada) |
| Mensaje del agente | orquestador, `_send` | `in_screening`: `abandoned_mid_screening` desde ese mensaje (+24 h) |
| Mensaje del candidato | orquestador, hook de la 011 | `ReengagementState()` → `None`. El candidato es el último en hablar, así que no aplica ninguna cadencia: se reprograma cuando el agente responde |
| Cierre y estados finales (`qualified`, `disqualified`, `human_requested`…) | orquestador, `_send` del mensaje final | `None` |
| `opted_out` | hook al recibir el mensaje | `None` (no se envía nada después) |
| Toque enviado, saltado o encolado | pasada | el paso siguiente, o la próxima apertura |
| Cierre por la cadencia (`no_response`) | pasada | `None` |
| Ficha vencida sin cadencia que aplique (dato viejo) | pasada | `None` y se guarda, para que no vuelva a salir en la consulta |

El cambio en el orquestador es una línea: `refresh_next_touch(candidate, self._config)` en `_send`, antes de guardar. Todo envío del agente pasa por ahí y es el único momento en que una ficha abierta queda en silencio.

La pasada sigue arrancando la cadencia (`reengagement_started`) cuando vence el primer toque, no el orquestador: si no, cada respuesta del agente dejaría un evento de inicio y otro de cancelación en el `timeline`.

### La pasada

1. `repository.query(CandidateQuery(touch_due_before=now, order="next_touch_asc", limit=min(page_size, restante), cursor=...))`, con `now` del `Clock` fijado al inicio de la pasada.
2. Cada ficha se procesa como en la 011. Al terminar, su `next_touch_at` queda en `None`, después de `now`, o igual si el envío falló (se reintenta en la próxima pasada). Como el cursor avanza por `(next_touch_at, id)`, una ficha no se procesa dos veces en la misma pasada.
3. Se sigue con `next_cursor` hasta que no haya más páginas o se llegue a `max_per_pass` fichas leídas. Si corta por el tope, `capped: true`: quedan vencidas y las toma la próxima pasada, empezando por las más atrasadas.

**Pasada interrumpida.** Si la pasada se cae (timeout de Cloud Run, error de la base), la siguiente vuelve a consultar desde el principio. Las fichas ya procesadas tienen su `next_touch_at` en el futuro y no se repiten: el estado guardado tras cada ficha es la idempotencia. La única ventana de duplicado es enviar y caerse antes de guardar esa ficha (milisegundos; ver §Escalar).

### Backfill

Las fichas creadas antes de esta feature pueden estar abiertas con `next_touch_at` vacío (la 011 lo calculaba recién en la primera pasada) y la consulta no las vería nunca. `backfill_next_touch` recorre por páginas las fichas en estados con conversación pendiente (`new`, `contacted`, `in_screening`), recalcula `next_touch_at` con `schedule_next_touch` y guarda solo las que cambian. Es idempotente: correrlo dos veces no cambia nada la segunda. Las fichas finales con un `next_touch_at` viejo las limpia la pasada (ver tabla).

Se corre una vez, después de desplegar y antes de que Cloud Scheduler empiece a usar la consulta:

```bash
python scripts/backfill_next_touch.py
```

Usa el repositorio de `SCREENING_REPOSITORY` (Firestore en Cloud Run) y la configuración de `SCREENING_CONFIG_DIR`, igual que la API.

### Concurrencia

Igual que en la 011: el orquestador y la pasada guardan la ficha completa. El lease por candidato de la spec 015 cubre que no procesen la misma ficha a la vez; esta feature no lo usa todavía.

### Escalar

- Con 10K postulaciones por semana, cada pasada de 15 minutos lee del orden de decenas de fichas vencidas, no todas. Si el volumen crece, el tope (`max_per_pass`) evita que una pasada supere el timeout del request y el resto queda para la siguiente.
- El siguiente paso es repartir: la pasada solo consulta y encola una tarea de Cloud Tasks por ficha vencida (`name` = `candidate_id + cadence + step + silence_started_at` como clave de idempotencia), y cada tarea procesa una ficha con el lease de la 015. Eso cierra también la ventana de duplicado entre enviar y guardar.

## Criterios de aceptación

- **Dado** un candidato contactado, **cuando** el orquestador envía el `first_contact`, **entonces** `next_touch_at` queda a las 24 h (o la próxima apertura si cae fuera de horario).
- **Dado** un candidato en screening, **cuando** el agente responde, **entonces** `next_touch_at` queda 24 h después de esa respuesta; **cuando** el candidato escribe, **entonces** queda en `None`.
- **Dado** un candidato calificado, descalificado o que pidió un humano, **cuando** se envía el mensaje final, **entonces** `next_touch_at` queda en `None`.
- **Dado** un paso que cae fuera del horario de su país (ES o MX), **cuando** se calcula el próximo toque, **entonces** es la próxima apertura en hora local; **cuando** `queue_outside_hours` es falso, **entonces** es la hora nominal.
- **Dado** fichas vencidas y no vencidas, **cuando** corre la pasada, **entonces** solo lee las vencidas.
- **Dado** más fichas vencidas que `page_size`, **cuando** corre la pasada, **entonces** las procesa todas por páginas; **cuando** superan `max_per_pass`, **entonces** procesa ese tope, informa `capped` y la siguiente pasada sigue con el resto sin repetir toques.
- **Dado** una pasada que se cayó a mitad, **cuando** corre la siguiente, **entonces** termina el trabajo sin enviar ningún toque dos veces.
- **Dado** una ficha antigua abierta sin `next_touch_at`, **cuando** corre el backfill, **entonces** queda con su próximo toque y la pasada la encuentra; **cuando** corre otra vez, **entonces** no cambia nada.

## Tests

`tests/unit/reengagement/` (con `FixedClock` y repositorio en memoria):

- `test_schedule.py`: `schedule_next_touch` para `contacted` (no repite el `first_contact`), `new` con el primer contacto fallido, `in_screening` con el último mensaje del agente o del candidato, estados finales, `human_requested`, sin teléfono, país sin horario, cadencia activa a mitad de camino, cadencia terminada; horario hábil en ES (domingo y cambio de horario) y MX (madrugada), y sin encolar.
- `test_index_pass.py`: la pasada solo lee las vencidas (repositorio espía), varias páginas, el tope y `capped`, la pasada siguiente sigue con el resto, reintento tras una pasada interrumpida sin duplicados, ficha vencida sin cadencia queda en `None`, y el backfill (fichas antiguas, idempotente, ignora las finales).
- `tests/unit/conversation/test_reengagement_hook.py`: `next_touch_at` tras el primer contacto, el mensaje del candidato, la respuesta del agente y el cierre.
- Los tests de la 011 siguen pasando; el arnés agenda cada ficha con `refresh_next_touch` al agregarla (lo que antes hacía el orquestador sin saberlo la pasada) y los casos de horario hábil reflejan que el ajuste ya viene en la hora agendada.

## Evaluación

No aplica: no usa LLM.
