# 015 — Lease por candidato en el orquestador

**Estado:** implementada
**Ola:** 4 (escalabilidad) · **Depende de:** 000 (`LeaseStore`), 008

## Objetivo
Que dos instancias de Cloud Run nunca procesen a la vez al mismo candidato. Hoy el
orquestador serializa con un `threading.Lock` en memoria, que solo sirve con una
instancia. Se reemplaza por un `LeaseStore` inyectado (Firestore en producción), así la
exclusión vive en la base y el proceso queda sin estado.

## Alcance
- Incluye: lease por teléfono en `ConversationOrchestrator.handle` y `start_application`;
  la excepción `CandidateBusy` como contrato con quien llama; liberación siempre; armado de
  `FirestoreLeaseStore` / `InMemoryLeaseStore` según `SCREENING_REPOSITORY`; respuesta 503
  de `POST /applications` y reencolado del lote en el despachador en memoria.
- No incluye: renovar el lease durante turnos largos (el TTL cubre un turno completo);
  reintentos con Cloud Tasks (spec 016); proteger el calentamiento (spec 017), que guarda
  fichas sin pasar por el orquestador.

## Interfaz

```python
# screening.channels (contrato acordado con la spec 016)
class CandidateBusyError(Exception):
    """El handler no pudo tomar al candidato; el lote no se procesó y hay que reintentarlo."""

# screening.conversation
DEFAULT_LEASE_TTL = timedelta(seconds=120)

class CandidateBusy(CandidateBusyError):
    """Otro proceso tiene el lease del candidato; el trabajo no se hizo y hay que reintentar."""
    key: str  # teléfono E.164 del candidato

ConversationOrchestrator(
    ...,
    leases: LeaseStore | None = None,       # por defecto, InMemoryLeaseStore()
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
)

ConversationOrchestrator.handle(batch) -> None            # puede lanzar CandidateBusy
ConversationOrchestrator.start_application(raw) -> ApplicationStartResult  # ídem

# screening.api.wiring
build_lease_store(env) -> LeaseStore
```

## Comportamiento

- **Clave:** el teléfono del candidato en E.164. En `handle` es `batch.sender`; en
  `start_application` es el teléfono del JSON normalizado (`normalize_phone`) **antes** del
  intake, para que una postulación y un mensaje entrante del mismo número no creen dos
  fichas. Si el teléfono falta o no se puede normalizar no hay conversación posible, y el
  intake corre sin lease.
- **Dueño:** un ID único por proceso y por llamada (`<uuid del proceso>:<uuid de la
  llamada>`). Dos llamadas del mismo proceso compiten igual que dos instancias.
- **TTL:** `lease_ttl`, 120 s por defecto, cubre un turno con dos llamadas al LLM. Es un
  parámetro técnico, no de negocio. Si un proceso cae, el lease vence solo y otro puede
  tomarlo. El `now` del lease sale del `Clock` inyectado.
- **Ocupado:** si `try_acquire` devuelve `False`, el orquestador lanza `CandidateBusy`
  **sin tocar la ficha ni el canal**. Contrato con quien llama:
  - el lote (o la postulación) **no se procesó**: no se marca como procesado y se reintenta;
  - el webhook de Cloud Tasks (spec 016) responde **503** (Cloud Tasks reintenta con
    backoff); `POST /applications` responde **503** con `Retry-After`;
  - el despachador en memoria (spec 005) devuelve los mensajes al agrupador y vuelve a
    intentar tras la ventana de agrupación.
  Es la única excepción que sale de `handle`: los demás errores se siguen registrando en la
  ficha y se contestan con la disculpa, como en la spec 008.
- **Liberación:** siempre en un `finally`, aunque el agente, el canal o el repositorio
  fallen. Si el `release` mismo falla, se registra en el log y no se propaga: el lease
  vence por TTL.
- **Armado:** `SCREENING_REPOSITORY=firestore` usa `FirestoreLeaseStore` (colección
  `leases`, un documento por teléfono, transacción por intento); en memoria,
  `InMemoryLeaseStore`.

## Criterios de aceptación
- **Dado** un lote de un candidato, **cuando** se procesa sin errores, **entonces** el lease
  se tomó con su teléfono y quedó libre al terminar.
- **Dado** que el agente o el canal lanzan, **cuando** se procesa el lote, **entonces** el
  lease queda libre.
- **Dado** el lease tomado por otro dueño y vigente, **cuando** llega un lote, **entonces**
  se lanza `CandidateBusy`, no se llama al agente ni al canal y la ficha no cambia.
- **Dado** un lease ajeno vencido, **cuando** llega un lote, **entonces** se procesa.
- **Dado** dos orquestadores con el mismo `LeaseStore`, **cuando** el segundo recibe un lote
  del candidato mientras el primero está en su turno, **entonces** el segundo lanza
  `CandidateBusy` y el primero termina normalmente.
- **Dado** el lease del teléfono tomado, **cuando** llega una postulación con ese teléfono,
  **entonces** `start_application` lanza `CandidateBusy`, no crea ficha y
  `POST /applications` responde 503.
- **Dado** `SCREENING_REPOSITORY=memory`, **cuando** se arma el lease store, **entonces** es
  un `InMemoryLeaseStore`.
- **Dado** un lote ocupado en el despachador en memoria, **cuando** se libera el lease,
  **entonces** el lote se entrega en el siguiente intento.

## Tests
`tests/unit/conversation/test_lease.py`, `tests/unit/test_wiring.py` y
`tests/unit/test_whatsapp_webhook.py` (despachador). La concurrencia se simula de forma
determinista: el agente del primer orquestador, en medio de su turno, le entrega un lote al
segundo.

## Evaluación
No usa LLM.
