# Orbio — Agente de screening para Grupo Sazón

Demo para el assignment de FDE de Orbio (`assignment.txt`). El diseño del proceso está en `docs/fase1_diseno_proceso.md`; es la fuente de verdad del comportamiento del agente.

Es un **demo, no un producto**: cuando haya dudas, priorizar lo simple y dejar documentado lo que quedó fuera de alcance.

## Forma de trabajo

- **Spec-driven development.** Cada feature parte con una spec en `specs/NNN-nombre.md` siguiendo `specs/README.md` (objetivo, esquemas de entrada y salida, criterios de aceptación Dado/Cuando/Entonces y casos de test y de evaluación). El orden siempre es **spec → tests → código**.
- **Cada feature se entrega con sus tests unitarios y, si usa LLM, con sus casos en el golden dataset.** No se da por terminada sin ambos.
- **Conversar antes de implementar.** Las decisiones de negocio y de diseño se discuten primero; no saltar a lo técnico ni entregar bloques largos de información.
- **Cuando el usuario repite una indicación, se agrega a este archivo** para que quede como regla.

## Estructura

```
config/            YAML de negocio (reglas, puntaje, cadencias, regiones, plantillas)
specs/             Una spec por feature (NNN-nombre.md)
src/screening/
  domain/          Modelos de dominio (contrato compartido)
  config/          Esquemas Pydantic del YAML y loader
  ports.py         Interfaces: repositorio, LLM, canales
  clock.py         Reloj inyectable (SystemClock / FixedClock)
  adapters/        Implementaciones concretas (memoria, Firestore, OpenAI, WhatsApp...)
tests/unit/        Tests sin red ni LLM
evals/             Golden datasets y pipeline de evaluación
docs/              Diseño y documentación
```

## Motor de reglas en YAML

Inspirado en el motor declarativo usado en Kelp. Toda la lógica de negocio configurable vive en `config/` y **el código no tiene defaults de negocio hardcodeados**:

- `rules/knockout.yaml`: reglas eliminatorias. Cada regla declara `requires` (campos necesarios para evaluarla), una `condition` y un `reason_code`. Se evalúa apenas están sus campos, no al final.
- Condiciones: `field` + `operator` (`equals`, `not_equals`, `greater_than`, `less_than`, `greater_or_equal`, `less_or_equal`, `in`, `not_in`, `exists`, `not_exists`, `between`), combinables con `all`, `any` y `not`.
- **Condiciones especiales** (`special`): funciones genéricas del motor para lo que no se expresa con un operador (`license_compatible`, `city_in_service_area`). Se registran en código y se referencian por nombre.
- `scoring.yaml`: componentes del puntaje con peso y tipo (`bands`, `mapping`, `boolean`, `days_until`). Los pesos deben sumar `max_score`.
- `cadences.yaml`: cadencias de calentamiento por situación, horario hábil por país y políticas al responder o volver a quedar en silencio.
- `regions/*.yaml`: ciudades de servicio, licencias válidas por vehículo y vocabulario por país.
- `templates.yaml`: mensajes fijos (recordatorios, descartes) por idioma.
- Todo se valida con Pydantic al arrancar, incluidas las referencias cruzadas (plantillas, campos). Un YAML inválido no arranca el sistema.

## Buenas prácticas de desarrollo

- Python 3.12, tipado completo, modelos con Pydantic.
- Arquitectura por capas: dominio (reglas, puntaje, estados) sin dependencias de infraestructura; adaptadores (LLM, base de datos, canales) detrás de las interfaces de `ports.py` e inyectados.
- La lógica de negocio determinista (reglas eliminatorias, puntaje, cadencias) **es código, nunca LLM**. El LLM solo conversa y normaliza respuestas.
- Toda salida del LLM se valida con Pydantic antes de usarse.
- Separar el turno que **genera el mensaje** al candidato del turno que **extrae datos** para la ficha; el mensaje nunca debe mencionar herramientas ni campos internos.
- Prompts en archivos versionados (`prompts/`), no dentro del código.
- El tiempo se obtiene siempre del `Clock` inyectado, nunca de `datetime.now()` directo (para poder testear cadencias).
- **WhatsApp:** persistir el mensaje entrante antes de procesarlo, deduplicar por ID de mensaje de Meta, agrupar mensajes seguidos del mismo candidato antes de responder, respetar el opt-out ("no me escriban más") y ofrecer siempre la vía a un humano.
- `ruff` para lint y formato, `pytest` para tests. Los tests unitarios no llaman al LLM ni a la red (usan fakes). Fixtures compartidos en `tests/conftest.py`. Los tests de integración se saltan solos si faltan credenciales.
- Código e identificadores en inglés; documentación y conversación en español.
- Git: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`); ramas `<tipo>/<descripcion-kebab>`.

## Trabajo en paralelo (varios agentes)

- Las features se desarrollan en olas. La ola 0 (contratos: `domain/`, `ports.py`, `clock.py`, `config/`) es la base compartida.
- **No modificar los contratos de la ola 0 desde una feature.** Si hace falta un cambio, se propone en el PR y se coordina.
- Cada agente trabaja en su worktree y su rama, escribe su propia spec con el número asignado, y abre un PR contra `main`.
- Las dependencias van en `pyproject.toml`; si una feature necesita una nueva, solo se agrega la línea.

## Stack

- **API y webhook de WhatsApp:** FastAPI, desplegado en Cloud Run.
- **Base de datos:** Firestore, detrás de `CandidateRepository` (en memoria para tests y desarrollo).
- **Panel de recruiters ("CRM"):** es nuestro propio panel en Streamlit, no un CRM externo. Link público para los evaluadores, protegido con una contraseña simple.
- **LLM:** OpenAI. Modelos configurables en `config/client.yaml`.
- **Capa de agentes:** Google ADK, con los modelos de OpenAI vía `LiteLlm`. La orquestación por etapas es determinista (agente custom o workflow de ADK) y los `LlmAgent` solo conversan o extraen; el RAG se expone como tool.
- **Observabilidad:** LangSmith.
- **Canales:** WhatsApp Cloud API con número de prueba (los evaluadores prueban en vivo desde sus teléfonos registrados), chat de consola para desarrollo y SMS simulado (solo se registra).
- **Recordatorios:** Cloud Scheduler llamando a un endpoint.
- **Repositorio:** GitHub.

## Evaluación

- Pipeline **custom** (no Vertex AI Evaluation).
- Golden dataset: pares de **pregunta + respuesta esperada**. La respuesta del sistema se compara con la esperada usando **LLM-as-a-judge**.
- Métricas iniciales: **accuracy, latencia y costo en tokens**.
- Se corre a pedido (`pytest -m eval`), no en cada test.
- No tiene que escalar, pero sí dejar las bases y una guía para hacerlo. El resto (más métricas, CI de evals) es nice-to-have.

## Escalabilidad

Es un demo, pero cada pieza debe poder escalar sin reescribirse (meta de referencia: 10K postulaciones por semana):

- Procesos sin estado propio: todo el estado vive en Firestore, nunca en la memoria del proceso. Nada de locks, timers ni cachés en memoria que supongan una sola instancia.
- Concurrencia entre instancias resuelta en la base (transacciones o precondiciones), no con locks locales.
- Nada de recorrer todas las fichas: consultas por índice (`status`, `next_touch_at`) y paginación.
- Trabajo diferido con colas (Cloud Tasks / Scheduler), no con tareas en segundo plano dentro de una request.
- Si algo se simplifica para el demo, se documenta como tal junto con el camino para escalarlo.

## Costos

Es un demo: minimizar el gasto. Servicios que escalen a cero y capas gratuitas de GCP; modelos pequeños donde alcance; evals con datasets chicos.

## Fuera de alcance

- Zona por barrio, código postal o distancia: se valida solo por ciudad.
- Idiomas distintos de español e inglés.
- Voz, guardrail adicional fuera del prompt y agendamiento de entrevistas: siguientes pasos.
- El clasificador de emoción va al final del orden de features.

## Comandos

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check . && .venv\Scripts\python -m ruff format --check .
```
