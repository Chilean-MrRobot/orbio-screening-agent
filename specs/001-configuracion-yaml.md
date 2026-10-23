# 001 — Configuración YAML

**Estado:** implementada
**Ola:** 0 · **Depende de:** 000

## Objetivo
Que Grupo Sazón (u otro cliente de Orbio) ajuste reglas, puntaje, cadencias, ciudades y mensajes sin tocar código. El motor no tiene defaults de negocio hardcodeados.

## Alcance
- Incluye: esquemas Pydantic de cada archivo, validación de referencias cruzadas y el loader.
- No incluye: la evaluación de reglas (003), el cálculo del puntaje (004) ni la ejecución de cadencias (011).

## Interfaz
`screening.config.load_config(config_dir=None) -> AppConfig`. Directorio: argumento, o `SCREENING_CONFIG_DIR`, o `config/` del repo. Cualquier error lanza `ConfigError` con el archivo y el detalle.

| Archivo | Contenido | Esquema |
|---|---|---|
| `client.yaml` | Nombre del cliente y del agente, SLA del recruiter, modelos de OpenAI | `ClientConfig`, `LLMConfig` |
| `regions/*.yaml` | País, zona horaria, prefijo, trato, vocabulario, ciudades con alias, licencias por vehículo | `RegionConfig` |
| `rules/knockout.yaml` | Reglas eliminatorias | `KnockoutRule`, `Condition` |
| `scoring.yaml` | Componentes del puntaje | `ScoringConfig` |
| `cadences.yaml` | Horario hábil, políticas y cadencias de calentamiento | `CadencesConfig` |
| `templates.yaml` | Mensajes fijos por idioma | `dict[str, dict[Language, str]]` |

## Comportamiento
- **Condición:** exactamente uno de `field`+`operator`, `all`, `any`, `not` o `special`. Los campos deben existir en `ScreeningData` (se admiten rutas como `delivery_experience.years`). `in`/`not_in` exigen `values`; `between` exige `min` y `max`; el resto de operadores (salvo `exists`/`not_exists`) exige `value`.
- **Regla eliminatoria:** declara `requires`, la lista de campos que deben estar llenos para evaluarla.
- **Puntaje:** los pesos deben sumar `max_score`. `bands` y `days_until` exigen `bands`; `mapping` exige valores entre 0 y 1.
- **Cadencias:** pasos en orden de `after_hours` y una sola cadencia por trigger.
- **Referencias cruzadas:** toda plantilla usada por reglas o cadencias debe existir y cada plantilla debe tener texto en todos los idiomas; los nombres de reglas son únicos; cada región declara el país de su clave.

## Criterios de aceptación
- **Dado** el `config/` del repo, **cuando** se carga, **entonces** se obtiene un `AppConfig` válido con las regiones ES y MX.
- **Dado** un `scoring.yaml` cuyos pesos suman 90, **cuando** se carga, **entonces** falla con `ConfigError` indicando la suma.
- **Dado** una regla con un campo inexistente, **cuando** se carga, **entonces** falla nombrando el campo.
- **Dado** una cadencia que usa una plantilla inexistente, **cuando** se carga, **entonces** falla nombrando la plantilla.
- **Dado** una condición con `field` y `all` a la vez, **cuando** se valida, **entonces** falla.

## Tests
`tests/unit/test_config.py`.
