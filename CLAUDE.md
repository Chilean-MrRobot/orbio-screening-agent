# Orbio — Agente de screening para Grupo Sazón

Demo para el assignment de FDE de Orbio (`assignment.txt`). El diseño del proceso está en `docs/fase1_diseno_proceso.md`; es la fuente de verdad del comportamiento del agente.

Es un **demo, no un producto**: cuando haya dudas, priorizar lo simple y dejar documentado lo que quedó fuera de alcance.

## Forma de trabajo

- **Spec-driven development.** Cada feature parte con una spec en `specs/NNN-nombre.md` (objetivo, esquemas de entrada y salida, criterios de aceptación Dado/Cuando/Entonces y casos de test y de evaluación). El orden siempre es **spec → tests → código**.
- **Cada feature se entrega con sus tests unitarios y, si usa LLM, con sus casos en el golden dataset.** No se da por terminada sin ambos.
- **Conversar antes de implementar.** Las decisiones de negocio y de diseño se discuten primero; no saltar a lo técnico ni entregar bloques largos de información.
- **Cuando el usuario repite una indicación, se agrega a este archivo** para que quede como regla.

## Buenas prácticas de desarrollo

- Python 3.12, tipado completo, modelos con Pydantic.
- Arquitectura por capas: dominio (reglas, puntaje, estados) sin dependencias de infraestructura; adaptadores (LLM, base de datos, canales) detrás de interfaces e inyectados.
- La lógica de negocio determinista (reglas eliminatorias, puntaje, cadencias) **es código, nunca LLM**. El LLM solo conversa y normaliza respuestas.
- Toda salida del LLM se valida con Pydantic antes de usarse.
- Prompts en archivos versionados, no dentro del código.
- Reglas de negocio, cadencias, pesos y ciudades en YAML; nada de eso va hardcodeado.
- `ruff` para lint y formato, `pytest` para tests. Los tests unitarios no llaman al LLM (usan mocks).
- Código e identificadores en inglés; documentación y conversación en español.

## Stack

- **API y webhook de WhatsApp:** FastAPI, desplegado en Cloud Run.
- **Base de datos:** Firestore, detrás de una interfaz de repositorio (fake en memoria para tests).
- **CRM:** Streamlit, con link público para que los evaluadores lo vean.
- **LLM:** OpenAI. Modelos configurables en YAML.
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

## Costos

Es un demo: minimizar el gasto. Servicios que escalen a cero y capas gratuitas de GCP; modelos pequeños donde alcance; evals con datasets chicos.

## Fuera de alcance

- Zona por barrio, código postal o distancia: se valida solo por ciudad.
- Idiomas distintos de español e inglés.
- Voz, guardrail adicional fuera del prompt y agendamiento de entrevistas: siguientes pasos.
- El clasificador de emoción va al final del orden de features.
