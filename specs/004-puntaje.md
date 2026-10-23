# 004 — Puntaje

**Estado:** implementada
**Ola:** 1 · **Depende de:** 000, 001

## Objetivo
Ordenar la cola del recruiter con un puntaje 0–100 explicable. El puntaje nunca descarta: solo se calcula cuando todas las eliminatorias pasaron, y cada ficha guarda el desglose para que el CRM muestre por qué alguien tiene 78 y no 90.

## Alcance
- Incluye: el cálculo puro y determinista a partir de `ScreeningData` y `ScoringConfig` (`config/scoring.yaml`).
- No incluye: decidir cuándo se calcula ni guardarlo en la ficha (máquina de estados, 008), mostrarlo (CRM, 013) ni validar el YAML (001). Nunca usa LLM.

## Interfaz
`screening.scoring.compute_score(data: ScreeningData, config: ScoringConfig, today: date) -> ScoreBreakdown`

- `data`: la ficha del candidato (`ScreeningData`).
- `config`: `AppConfig.scoring`.
- `today`: fecha de hoy. La pasa quien llama, a partir del `Clock` inyectado (en la zona horaria del país del candidato); el módulo nunca llama a `date.today()`.
- Salida: `ScoreBreakdown(total, components)`, con `components` = `{nombre del componente: puntos}`.

## Comportamiento
Cada componente aporta `weight × value`, con `value` entre 0 y 1. Como los pesos suman `max_score` (lo valida 001), el máximo posible es `max_score`.

| Tipo | Cómo se obtiene `value` |
|---|---|
| `bands` | Valor numérico del campo. Gana el **primer** tramo con `min ≤ x ≤ max` (límites inclusivos; el que falta queda abierto). |
| `mapping` | Clave = valor del enum (p. ej. `full_time`). |
| `boolean` | `True` vale 1; cualquier otro valor (`False`, `None`) vale 0. |
| `days_until` | Días entre `today` y la fecha del campo, evaluados con los tramos como en `bands`. Una fecha pasada cuenta como 0 días ("empieza ya"). |

- **Rutas anidadas:** el campo admite rutas como `delivery_experience.years`; si un tramo intermedio es `None`, el campo cuenta como vacío.
- **Sin error, aporta 0:** campo vacío, valor que no calza con ningún tramo ni clave (p. ej. una fecha a 45 días con el último tramo en 30), o tipo inesperado (un texto en un componente `bands`, un booleano usado como número).
- **Redondeo:** cada componente se guarda redondeado a 2 decimales (evita ruido de coma flotante como `15.000000000000002`); `total` es la suma de los puntos sin redondear, redondeada a 1 decimal. Se redondea "hacia arriba en el medio" (78,25 → 78,3), que es lo que espera un recruiter, y no con el redondeo bancario de `round()` (78,25 → 78,2).
- **Orden de `components`:** el del YAML, para que el CRM lo muestre igual que se configuró.

## Criterios de aceptación
- **Dado** un candidato con 3 años de experiencia, `full_time`, inicio hoy, vehículo propio y horario `flexible`, **cuando** se calcula con el `scoring.yaml` del repo, **entonces** `total` es 100 y cada componente vale su peso.
- **Dado** el mismo candidato con 0 años de experiencia, **cuando** se calcula, **entonces** `experience` vale 0, no hay error y `total` es 65.
- **Dado** una ficha vacía, **cuando** se calcula, **entonces** `total` es 0 y todos los componentes valen 0.
- **Dado** 1 año exacto de experiencia, **cuando** se calcula, **entonces** cae en el tramo `min: 1` (0,75 × 35 = 26,25).
- **Dado** una `start_date` anterior a hoy, **cuando** se calcula, **entonces** cuenta como 0 días y suma el peso completo.
- **Dado** una `start_date` a 31 días, **cuando** se calcula, **entonces** `start_date` aporta 0.
- **Dado** una configuración armada a mano, **cuando** se calcula, **entonces** cada tipo (`bands`, `mapping`, `boolean`, `days_until`) se comporta como en la tabla.

## Tests
`tests/unit/test_scoring.py`: candidato perfecto, sin experiencia, ficha vacía, límites de cada tramo de experiencia y de fecha de inicio, fecha pasada, fecha más allá del último tramo, un test por tipo de componente con configuración propia (incluidos tramos con ambos límites, abiertos y sin calce), redondeo y orden del desglose.
