# Specs

Cada feature parte con una spec aquí, **antes** de escribir tests o código. Orden: **spec → tests → código**.

## Numeración

| Nº | Feature | Ola |
|---|---|---|
| 000 | Contratos base (dominio, puertos, reloj) | 0 |
| 001 | Configuración YAML | 0 |
| 002 | Validación del JSON de entrada (intake) | 1 |
| 003 | Motor de reglas eliminatorias | 1 |
| 004 | Puntaje | 1 |
| 005 | Canal de WhatsApp (webhook, dedup, agrupación) + SMS simulado + consola | 1 |
| 006 | Cliente LLM de OpenAI + trazas en LangSmith | 1 |
| 007 | Pipeline de evaluación (golden dataset + LLM-as-a-judge) | 1 |
| 008 | Máquina de estados y persistencia (Firestore) | 2 |
| 009 | Agente conversacional y extracción | 2 |
| 010 | RAG sobre FAQ | 2 |
| 011 | Calentamiento de candidatos | 3 |
| 012 | Resumen post-conversación | 3 |
| 013 | CRM (Streamlit) | 3 |
| 014 | Clasificador de emoción | 3 |
| 015 | Lease por candidato en el orquestador | 4 |

## Plantilla

```markdown
# NNN — Nombre de la feature

**Estado:** borrador | aprobada | implementada
**Ola:** N · **Depende de:** 000, 001

## Objetivo
Qué problema resuelve y para quién (1–3 líneas).

## Alcance
- Incluye: ...
- No incluye: ...

## Interfaz
Módulos, funciones públicas y esquemas de entrada y salida (Pydantic).

## Comportamiento
Reglas y decisiones, incluidos los casos borde.

## Criterios de aceptación
- **Dado** ... **cuando** ... **entonces** ...

## Tests
Lista de tests unitarios (sin red ni LLM).

## Evaluación (solo si usa LLM)
Casos del golden dataset: pregunta + respuesta esperada, y umbral de accuracy.
```
